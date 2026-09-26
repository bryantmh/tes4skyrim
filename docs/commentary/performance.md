# the whole pipeline - performance and parallelism

**Code:** `asset_convert/nif/pyffi_monkey_patch.py`, `tes5_import/base/writer.py`, `asset_convert/ui/book_inam.py`, `asset_convert/nif/nif_geom_array.py`

## Contents

- [Low-core machines: where the time actually goes (measured 2026-08-09)](#low-core-machines-where-time)
- [Parallelism rules (learned 2026-07-16)](#parallelism-rules)
- [FormID determinism — the save-game contract (rewritten 2026-08-17)](#formid-determinism-save-game-contract)
- [One heavy job at a time](#one-heavy-job-at-a-time)
- [Process containment — orphaned workers (learned 2026-07-29)](#process-containment-orphaned-workers)
- [Navmesh generation (learned 2026-07-25)](#navmesh-generation)
- [Measured throughput](#measured-throughput)

Linked from [CLAUDE.md](../../CLAUDE.md). Measured optimisation results and the
rules that keep the output byte-reproducible.

## Low-core machines: where the time actually goes (measured 2026-08-09)
<a id="low-core-machines-where-time"></a>

The pipeline takes ~30 min on a 32-core box. Profiling for 4-8 core machines
found that **the parallel stages are already mature** — the wins were all in
*serial* work and in *per-process-spawn* overhead, both of which a low-core
machine pays in full.

The default worker count is `cpu_total() - 3` (`worker_budget.py`), so a 4-core
machine runs **1 worker**. Anything not parallel therefore dominates there.

### 1. Papyrus compile — one process per script (the biggest low-core win)

`phase_compile` spawned **one `papyrus.exe` per .psc file**: 15,961 processes
for Oblivion.esm, each re-parsing the ~3,000 Skyrim `Data\Source\Scripts`
headers from scratch.

| | measured |
|---|---|
| per-file, serial | 82 ms/script -> **~1,316 s of CPU** |
| per-file @ 4 cores (1 worker) | **~22 minutes** |
| per-file @ 32 cores (29 workers) | ~45 s (why it was invisible here) |
| **batch `-i <dir>`, one process** | 200 scripts in 1.08 s; **2.37 ms/script marginal**, ~40 s for the whole plugin |

`papyrus.exe compile -i` accepts a **directory**. The catch — and the reason
the original code deliberately used per-file — is that **the compiler aborts
the whole batch on the first bad file and writes ZERO .pex** (measured: 1
broken script of 201 -> 0 .pex).

So `phase_compile` is now **batch-first with quarantine-and-retry**: compile the
directory; parse the failing filenames out of the error lines; copy everything
except those into a staging dir; retry. Error-reporting shape (measured):

- **Scanner/parser** errors surface **one file at a time**, each aborting.
- **Checker** (semantic) errors surface for **every** bad file in one pass
  (`failed to compile files, N errors`).
- Every error line is `<path>\Foo.psc:LINE:COL: <Kind> error: <msg>`, so the
  failing file is always recoverable from stdout.

Quarantined scripts are then re-tried individually (a file can be dragged into
a batch failure by a dependency), and if the batch cannot make progress at all
the original per-file path still runs as a fallback. A healthy build spawns
**one** compiler process instead of 15,961.

Measured end-to-end on Oblivion.esm with every `.pex` deleted first:

- **Clean build: 15,961/15,961 succeeded in 10.0 s** (one compiler process).
- **With 2 deliberately-broken scripts injected**: quarantined both over two
  retries, still compiled **15,961 of 15,963**, and reported each failure with
  its exact compiler error. Plain batch mode would have emitted **zero** .pex.

The staging directory is built **once and maintained incrementally** (each
retry only deletes the newly-quarantined file). Re-copying all ~16k scripts per
retry cost 129 s for the two-failure case — far more than the compiles.

### 2. `build_edge_links` — 60-93 s single-threaded (4.25x, byte-identical)

The largest serial block in `--import-only`: 93.2 s cold / 60.3 s warm, versus
**21.7 s** for generating all 8,228 navmeshes across 29 workers.

The cause was **not** algorithmic. Real seams are tiny — mean |A| = 5.7,
median 5, max 33; 262k total inner-loop iterations across every seam — so
`_match_seam`'s O(A x B) greedy pairing is only **1%** of the pass. The cost was
per-element `struct` work:

| | share |
|---|---|
| `NavMeshView.__init__` (per-vertex/per-tri `unpack_from`) | 39% |
| `_border_edges` (Python triangle scan, 24k calls) | 28% |
| `zlib.compress` | 11% |
| `pack()` (per-element `struct.pack`) | 6% |

numpy vectorisation of the decode, the pack, and the border-edge scan gives
**4.25x (35.9 s -> 8.4 s), byte-identical output and an identical link count**
(258,872). Two traps, both of which silently changed the link count:

- **Vertices must stay float64.** The original computed seam midpoints from
  Python floats (doubles). Doing it in float32 shifts midpoints by ~1e-2 units,
  which reorders near-ties in the greedy pairing — **4 extra links**.
- **Keep `verts`/`tris` as numpy arrays; do NOT `.tolist()` them.** `add_link`
  mutates a triangle in place, so a list form has to be re-converted with
  `np.asarray` on every seam scan: 103k calls costing **11.9 s**, more than the
  decode it was meant to save. That version measured only 1.15x. Arrays are
  mutated directly (`self.tris[i]` is a row view) and need no cache at all.
- `np.frombuffer` returns a **read-only** array; the `.astype()` that widens
  i2 -> i4 is what makes it writable (and restores the two unsigned columns
  with `&= 0xFFFF`).

Profile it in isolation without paying for a full import: set
`TESCONV_DUMP_NAVM_CACHE=<path>` on an import run to pickle the precomputed
navmesh cache, then profile `build_edge_links` against it.

### 0b. The ESM was NON-DETERMINISTIC too (fixed 2026-08-21)

Same root cause as the NIF case below, in a different file: an override built
twice from unchanged inputs emitted its subrecords in a different ORDER.
Measured on `Morrowind_ob - Chargen and Transport Mod.esp`, REFR
`CATShipCabinDoorExteriorREF` (70 bytes differing, record size identical):

```
run A:  NAME XLOC XSCL XNDP DATA EDID XTEL XLCN
run B:  NAME XLOC XSCL XNDP DATA XTEL EDID XLCN
```

Cause, in `export_diff.diff_records`:

```python
for key in set(m_scalars) | set(p_scalars):   # <- per-process order
```

`set` over **str**, randomised per process (PEP 456). That dict becomes
`pending` in `override_builder.build_override_record`, whose insert loop puts
every newly-inserted subrecord at the SAME index — so dict order decided which
of EDID/XTEL landed first. Verified directly: under `PYTHONHASHSEED` 1 / 999 /
12345 / 777 the pre-fix `diff_records` returned four different key orders; the
fixed version returns one.

Two changes, both needed:
* `diff_records` iterates `sorted(...)` — the actual cause.
* The insert loop splices the pending subrecords in as a BLOCK
  (`out[pos:pos] = insertable`) instead of one at a time, which had silently
  REVERSED them relative to `pending`.

Guarded by `tests/test_export_diff_determinism.py` (both tests fail against
the pre-fix code). The end-to-end check is two `--import-only` runs under
opposed `PYTHONHASHSEED` values: they must be byte-identical.

🛑 **Any `set` of strings/bytes whose ITERATION ORDER reaches the output is
this bug.** Membership tests are fine; iteration is not.

### 0. NIF output was NON-DETERMINISTIC (fixed — read this first)

Converting the same source NIF twice produced **different bytes**, with no code
change between runs. It is stable *within* one process and varies *between*
processes — the signature of `PYTHONHASHSEED`. Pinning the seed makes three
separate runs agree exactly.

Cause, in PyFFI's `NifFormat.Data.write` (line ~1462):

```python
self._string_list = list(set(self._string_list))   # ensure unique elements
```

`set` over **bytes**, whose hash is randomised per process (PEP 456). Every
`NiStringRef` stores an *index* into that list, so the whole header string table
and its references shuffle. Measured on `dungeons/chargen/dobrick01.nif`: same
size, same 10 blocks, same geometry — **26 of 7,433 bytes differ**, all in the
string table and the indices into it.

**Patch 10** in `pyffi_monkey_patch.py` makes the dedupe insertion-ordered
(`dict.fromkeys`). It recompiles that one method from PyFFI's own source with
the single line rewritten, and declines to install (loudly) if the source does
not match, so it cannot silently drift from the installed PyFFI.

Why it matters beyond tidiness: **no mesh build was reproducible**, a BSA
differed from the previous one for no reason, and — the reason this surfaced —
every before/after byte-comparison of a mesh optimisation reported a spurious
mismatch, so it could not tell a real regression from seed noise. Any mesh
perf work done before this fix was measured with a broken instrument.

Check it with `python tools/nif/nif_determinism.py` (converts a sample under
several `PYTHONHASHSEED` values and diffs the hashes; non-zero exit on failure).
Verified: 65 meshes across 5 seed pairs, 0 differences.

### 3. Mesh conversion is ~100% PyFFI object-model overhead

Per-mesh cost scales with file size (~95 ms per 100 KB; median 304 ms, worst
~9 s for a 2 MB architecture NIF). The profile is almost entirely PyFFI's
generic XML-driven object model — `struct_.__init__`, `get_basic_attribute`,
`getattr`, `_get_filtered_attribute_list` — **not** our conversion code. The
stage already runs one process per core, so the only lever is per-mesh CPU.

**Patch 9** (`asset_convert/nif/pyffi_monkey_patch.py`): `StructBase._log_struct`
-> no-op. PyFFI calls it for every attribute of every struct on **both** read
and write, doing a `getattr`, an `isinstance`, a `get_value()` and a six-operand
`str.format()` before the logger discards the record. Nothing consumes it — the
pipeline's `_PyFFICapture` handler attaches at WARNING+. Measured 1,517,371
calls / 4.44 s cumulative on two heavy NIFs. **1.09x, byte-identical**; the
patch is skipped if DEBUG really is enabled for `pyffi.nif.data.struct`.

**Patch 11**: `NiTriBasedGeom.update_tangent_space` reimplemented in numpy. It
became the hottest single function once the logging patch was in (4.34 s
cumulative of ~11 s across 12 meshes, in only **57 calls**) because it runs a
per-**triangle** Python loop allocating several `Vector3` objects each, then a
per-**vertex** Gram-Schmidt loop. The numpy version reproduces the algorithm
exactly — the same quantised `(vertex, normal)` merge hash (so uv seams still
share a frame), degenerate-triangle skipping, per-triangle normalization
*before* accumulation, the `r_sign` factor, the Gram-Schmidt order, and the
`x cross n` / `y cross n` fallback basis. Anything it cannot handle (no uvs, no
normals, no triangles, length mismatch) falls through to the original.

**1.39x on its own; 1.85x for the whole mesh stage with Patches 9+11**
(15.27 s -> 8.25 s on a 24-mesh sample). Used by the main mesh path (via
`SpellAddTangentSpace`), `lod_far_gen` and `spt_converter`, so all three gain.

Output is *not* byte-identical here, and that is expected: the original
accumulated in float32 `Vector3`s, this accumulates in float64. Measured
divergence across sample meshes is **1e-16 to 1e-5 per component** — rounding,
not a different answer. Toggle with `TESCONV_PYFFI_NO_FAST_TANGENTS=1` to A/B.

**Patch 12**: single-hop `get_interchangeable_tri_shape` / `_tri_strips`.
PyFFI changes a geometry block's container type with **four** deepcopies routed
through the common base class:

```python
shape     = NiTriShape().deepcopy(NiTriBasedGeom().deepcopy(self))
shapedata = NiTriShapeData().deepcopy(NiTriBasedGeomData().deepcopy(self.data))
```

The intermediate exists only because `NiTriShapeData` and `NiTriStripsData` are
**siblings** — `deepcopy` refuses unrelated classes, so a strips→shape copy has
no legal direct form. But it copies every vertex, normal, uv and color TWICE,
and both hops select the *same* attribute list (measured: 19 names for the data
blocks, 29 for the shape blocks — the base-class attributes). The triangle /
strip fields are never among them; `set_triangles`/`set_strips` supplies those
immediately after. So the second copy transfers nothing the first did not.

Two changes, both in `pyffi_monkey_patch.py`:

1. **Copy the base attributes once**, straight from source to target. The
   attribute list is taken from the **SOURCE**, never the freshly-constructed
   target — `_get_filtered_attribute_list` is condition-dependent, and
   `has_normals` / `has_vertex_colors` / the uv flags are all False on a new
   object, so filtering on the target silently drops every normal and color.
2. **Bulk element copy** for flat scalar element types (`Vector3`, `Color4`,
   `TexCoord`). `update_size()` builds each element through
   `StructBase.__init__` — a `set()`, an `_items` list and one holder per
   component — purely so `deepcopy` can overwrite every component one
   `getattr`/`get_value`/`set_value` at a time. A 10-mesh sample copies 22,494
   `Vector3` and 11,237 `Color4` elements this way. The fast path builds each
   element once and assigns the holders' `_value` fields across. Handles the
   2-D `uv_sets` array; anything else falls back.

Elements are always NEW objects, never shared with the source — `process_geometry`
mutates the copy in place (`_set_tangents`, `_clamp_uv_sets`,
`fix_missing_triangles`) while still reading the original's `extra_data_list`
and `data.num_vertices`.

**1.21x–1.31x on mesh conversion, byte-identical.** Measured as CPU time
(`time.process_time`), which is the only stable instrument here — wall-clock on
this box swings ±20% between identical runs and produced a spurious "0.95x
regression" on one sample. Verified byte-identical on 60 Oblivion NIFs, 40
Nehrim NIFs and 30 LOD-inclusive NIFs, plus `tools/nif/nif_determinism.py`.
`get_interchangeable_tri_shape` went from 2.01 s of 6.47 s (31.1%, the largest
single item) to below the top-14 cumulative entries; total profiled calls for a
30-mesh sample dropped 122.4M → 90.6M and `struct_.__init__` 1,176,509 →
936,657. Toggle with `TESCONV_PYFFI_NO_SINGLE_HOP_COPY=1` to A/B; guarded by
`tests/test_pyffi_interchangeable_copy.py`, which asserts the result equals
PyFFI's own two-hop path attribute-for-attribute across five flag combinations.

### 3a. The winding oracle's vertex transform (vectorised 2026-08-24)

`collision._visual_tri_soup` transforms every render vertex under a node into
Havok units, to serve as the orientation oracle for `_repair_inverted_floors`.
It did that one vertex at a time:

```python
for v in data.vertices:
    w = v * m                      # Vector3 * Matrix44
```

`Vector3.__mul__` against a `Matrix44` recurses into
`self * x.get_matrix_33() + x.get_translation()` — three Python calls and
several `Vector3` allocations **per vertex**. Measured on a 20-mesh sample it
was **3.42 s of 18.27 s (18.7%) in 24,887 calls**, and `print_callers` showed
**all** of them came from this one function: the largest single item left after
Patch 12.

Replaced by `collision._transform_verts`, a bulk numpy affine transform.
`_visual_tri_soup` went **3.76 s → 0.208 s (18x)** and `Vector3.__mul__`
disappeared from the profile.

🛑 **It must be BIT-EXACT, and float64 is what makes it so.** The soup feeds a
nearest-face search with a trust radius, so a 1e-7 drift can flip a DIFFERENT
triangle and change the collision we ship. Two traps, both hit and both fixed:

- **float32 is wrong.** PyFFI's `Float` holds a plain **Python float**, so
  `v * m` is evaluated in *double* precision — only the on-disk representation
  is 32-bit. Computing in float32 left just **0.47% of 52,159 sample vertices**
  bit-exact (worst 9.5e-07).
- **`v @ rot` is wrong.** matmul reorders and fuses the accumulation. The
  transform is written as explicit per-component mul/add in PyFFI's own
  evaluation order so every intermediate rounds where the scalar loop rounds.

With both: **52,159 of 52,159 vertices bit-exact across 168 shapes, worst diff
0.** Verified byte-identical on 60 Oblivion + 50 Nehrim + 40 Morrowind_ob NIFs,
and — the invariant that actually matters — `_INVERTED_FLOOR_FLIPS` is
unchanged on all three (53 / **693** / 93), including Nehrim where the repair
does the most work. Toggle with `TESCONV_NO_FAST_VERT_XFORM=1`; guarded by
`tests/test_collision_vert_transform.py`.

There is **no size crossover**: numpy wins at every vertex count measured,
including n=4 (16 us vs 135 us, 8x), because the scalar loop pays three Python
calls and several `Vector3` allocations *per vertex* while the array round trip
is paid once. `_VECTOR_XFORM_MIN` is therefore 2, not the 24 first guessed.

**1.12x–1.18x on its own; 1.41x for mesh conversion combined with Patch 12**
(60 NIFs, CPU 21.11 s → 14.95 s).

### 3b. Patch 13 -- native Array.read/write (and why it was NOT 3x)

`Array.read` is **95% of all NIF read time** (2.99 s of 3.17 s over 60 meshes),
concentrated in three element types: `Vector3` (225,591 elements, 1.259 s),
`Color4` (81,843, 0.536 s) and `TexCoord` (0.521 s). PyFFI builds one element
object per item and calls `elem.read()`, which runs a `struct.unpack` per
**component**.

`native/src/nifgeom/geom.cpp` (`_nifgeom_native`) adds `fill_floats` /
`pack_floats`: the Python side constructs the elements and hands the extension
the flat list of value holders, which fills or drains them in one call.
`asset_convert/nif/nif_geom_native.py` patches `Array.read`/`Array.write` for
element types that are exactly N unconditional float components; everything
else falls through to PyFFI untouched.

**Measured: 1.48x on read+write in isolation, 1.20x on full mesh conversion.**

🛑 **The estimate for this work was 2.5-3x. It delivered 1.20x, and the reason
is worth recording so the next attempt does not repeat it.**

The pitch said "read+write are 46% of per-mesh time and this captures ~90% of
that." That was wrong about WHAT the cost is. Profiling after the patch:

```
struct_.__init__     514,239 calls   2.32 s   <- UNCHANGED by Patch 13
Float.__init__     1,337,405 calls   0.78 s   <- UNCHANGED by Patch 13
getattr            7,617,961 calls   1.24 s
get_basic_attribute 2,148,802 calls  0.65 s
```

The dominant cost was never the `struct.unpack` -- it is **constructing the
element objects**, and Patch 13 does not remove a single one of them. Measured
directly: building 225,591 `Vector3` objects costs **0.70 s** all by itself,
and that is a hard floor for any design in which the converter keeps receiving
real `Vector3` objects.

Object-model overhead is still **~44% of mesh conversion (6.33 s of 14.3 s)**
after all three optimisations. Removing it means arrays *replacing* the element
objects, not feeding them -- which changes the type every consumer sees and
means rewriting the ~31 modules that touch `NifFormat`. That is the 3x, and it
is a different, much larger project than a native serialiser. **Do not estimate
that work again from the read/write share of the profile.**

### 3c. Patch 14 -- numpy-backed geometry arrays (the 2.4x)

`asset_convert/nif/nif_geom_array.py`.  Patch 13 sped up array I/O and left the
real cost untouched: PyFFI materialises one element OBJECT per item, and
constructing 225,591 `Vector3` objects alone costs **0.80 s** per 60 meshes.
This backs `Vector3`/`Color4`/`TexCoord`/`Vector4` arrays with ONE numpy array
and returns lightweight `__slots__` views, so that construction disappears.

Consumers are unchanged: a census of all **346 geometry-array references across
37 files** found **309 (91%) work as-is**, and the remaining ones bind an
element for later use -- which a view supports, because it holds the array and
a row index rather than a copy.

**2.40x cumulative** for mesh conversion (60 NIFs, CPU 22.16 s -> 9.22 s),
byte-identical on 60 Oblivion + 50 Nehrim + 40 Morrowind_ob NIFs, and
`nif_determinism.py` clean.

🛑 **Three bugs it took a byte-diff to find. Do not re-introduce them.**

1. **float64, never float32.**  PyFFI's `Float` holds a Python float (a
   double); only the on-disk format is 32-bit.  A float32 backing store
   truncates every intermediate the converter writes back (skin retargeting,
   tangent generation), and **broke 5 of 60 meshes**.  Same trap as 3a.
2. **The view MUST subclass the real element class.**  PyFFI's own
   `update_tangent_space` does `v_2 - v_1`.  A view without `__sub__` raised
   TypeError inside `SpellAddTangentSpace`, **whose caller swallows exceptions**
   (`except Exception: pass`) -- so tangent generation silently stopped after 9
   of 51 shapes in `explodingrootpod.nif` and shipped zeroed tangents with no
   error printed anywhere.  Subclassing makes the view behaviourally complete by
   construction instead of by enumerating operators.
3. **Install the component properties AFTER `type()` returns.**  `StructBase`
   re-creates a property for every declared attribute when a subclass is built,
   overwriting a property passed in the namespace dict with PyFFI's own
   `partial(set_basic_attribute, name='x')` -- which then reaches for the
   `_x_value_` holder we deliberately never create.

Also: views carry the element class's `_attrs`, because generic copiers detect
a compound with `hasattr(x, '_attrs')`.  Without it
`nif_converter._copy_block_fields` assigned the VIEW OBJECT into a clone,
aliasing it to the source -- and `_emulate_morphs`' `v.x += d.x` then
accumulated onto the original vertices across morph targets.

Toggle with `TESCONV_NO_GEOM_ARRAY=1`; guarded by
`tests/test_nif_geom_array.py` (one test per bug above).

🛑 **A measurement warning that cost more time than any of the bugs.** A
baseline recorded with a patch ACTIVE is worthless: doing that invented a
phantom "4 of 60 regression" and sent a whole debugging cycle down a wrong
path.  Record every `--save-baseline` with **every** optimisation toggled OFF,
and re-record it whenever the default set changes.

**NOT DONE, and do not retry blindly:** caching `_get_filtered_attribute_list`
(1.98M calls, the obvious next target). Memoising it per
`(class, version, user_version)` — even restricted to classes where no attribute
carries a `cond` — **changed the output of 7 of 30 sample meshes**. The
filtering is not instance-independent: `arg`/`vercond` can reference instance
fields, and PyFFI mutates instance state *while* walking the list during read,
so a later attribute's inclusion can depend on an earlier one's just-read value.

### 4. Terrain LOD: the Python side is not the bottleneck

Object LOD (786 `_far.nif` across 29 workers) and the terrain tile pool are both
properly parallel. Two findings:

**Stage result: 18m37s -> 10m58s (1.70x) for all 18 worldspaces**, with
**every one of the 18 LODGen reference counts identical** (Tamriel 180,575) and
**2,507 terrain tiles in both runs**. Measured end-to-end, not extrapolated.

- **`write_lodgen_input` was the real bottleneck — 249.9 s for Tamriel, and
  99.6% of it was one function.** Instrumented: `_lod_mesh_is_safe` accounted
  for **248.8 s in 2,968 calls (84 ms each)**; `_parse_esm` was 5.0 s and the
  `_mesh_exists`/`_import_master_mesh` filesystem checks 0.1 s each.

  That screen exists for a real reason — LODGenx64 casts every LOD mesh's root
  to `NiNode` without checking, and one bad root kills the whole worldspace's
  object LOD — but it was doing a **full `NifFormat.Data.read`** to learn one
  block's type name. Reading just the header (version string, `user_version_2`,
  the 3 export-info strings, the block-type table, `block_type_index[0]`) gives
  the same answer **267x faster** (25.12 s -> 0.09 s over 600 meshes).

  **Verify any change to that parser with `temp/root_check.py`**, which diffs
  the fast path against the full parse. The first attempt omitted
  `user_version_2` and the export-info strings and returned False for **600 of
  600** meshes — i.e. it would have silently dropped every object from LOD.
  Header shapes older than 10.0.1.0 fall back to the full parse.

- **`LODGenx64.exe` is NOT the dominant cost** (an earlier note here said it
  was — wrong). Its own log brackets each run: Tamriel is **3m27s**, and all
  seven worldspaces in one measured run totalled **3.6 min**. It is a
  third-party binary already using 6.4 cores across 122 threads, so it is not
  our lever either way.

- **`_parse_esm` ran once per worldspace**, re-deriving identical data from the
  same 613 MB file 18 times (5.7 s, 1,017,612 refs) — ~103 s. Now memoised on
  `(path, mtime, size)`. The overlay merge mutates all four returned
  structures, so `generate_lod` shallow-copies them when overlays exist;
  without that the second worldspace inherits the first one's merged state.

- <a id="lod-overlay-scoping"></a>**Overlays were attached by master chain, not
  by what they actually edit.** `create_lod` gave a worldspace every selected
  plugin that depends on its owner — which is what a plugin is ALLOWED to touch,
  not what it DOES. On the 12-plugin selection that stacked all 9 of
  Oblivion.esm's dependents onto all 18 of its worldspaces: **162 overlay
  attachments, 114 s of parsing, 4 s of it useful**. Most dependents edit exactly
  one worldspace and three edit none of Oblivion's at all. Scoping on
  `sibling_lod.touched_worldspace_fids` (one linear GRUP walk; bodies never
  parsed) cuts it to **7 attachments**, and the whole planning phase to ~10 s.

  Two caches make the rest cheap: the parsed-ESM memo now holds **the base AND
  its overlays** (it kept one entry, so the two evicted each other and the
  overlay stack was re-parsed every worldspace), and `create_lod` resolves every
  worldspace FormID an owner is responsible for from **one** read of its bytes.

  🛑 **Scope must be judged per file, from its own records only.** A raw FormID
  is meaningless across plugins — the index byte offsets into each file's OWN
  master list, so Morrowind_ob.esm's `02xxxxxx` and Tamriel.esp's `02xxxxxx` are
  both *self* and name unrelated records. The two collide on **4 CELL FormIDs**;
  resolving one plugin's cells against another's table reads that coincidence as
  183 overrides and drags Morrowind **interior** clutter into Cyrodiil's distant
  terrain. Guarded by `tests/test_lod_overlay_scope.py`. See
  [[project_master_index_routing]].

  Scoping removes the 183 phantom refs as a side effect, but the merge itself
  needed the real fix — see below.
- <a id="lands-shared-memory"></a>🛑 **`lands` IS SHARED WITH THE TILE WORKERS,
  NEVER COPIED PER WORKER — AND LOW CPU WITH HIGH RAM IS THE TELL.**
  (found 2026-08-12, reported by the user as "taking forever, cpu usage low,
  ram near 100%")

  `generate_terrain_lod` passed `lands` to the pool via `initializer=`, which
  **pickles it once per worker**. On Windows (spawn) that is a private copy in
  every process. The data is far bigger than it looks: **~24 KB per cell**,
  dominated by the 17x17 float32 opacity grids, NOT the heights.

  | worldspace | LAND records | packed | x29 workers |
  |---|---|---|---|
  | Tamriel (Oblivion.esm alone) | 14,686 | 329 MB | ~9.5 GB |
  | Tamriel + 6 overlays | 110,260 | **1,205 MB** | **~35 GB** |

  Measured mid-run on a 31 GB machine: 25 python processes, **22.9 GB resident,
  1.2 GB free, 13.4 GB of pagefile**. The box was swapping, which is why the
  symptom was **low CPU** while the stage crawled — the instinct to blame
  compute is wrong here.

  Fixed by publishing ONE copy through `multiprocessing.shared_memory`; each
  worker maps it and rebuilds numpy **views** (`SharedLands`). After: **4.8-6.7
  GB peak with all 29 workers and 15 GB free.** Do NOT "fix" this by cutting
  `worker_count()` — the workers were never the problem, the per-worker copy was.

  Three traps, all found by testing:
  - **`decode_land_layers` returns `(ltex_fid, grid)` 2-tuples**, not
    `(layer, fid, grid)`. It sorts by layer and DROPS the index, so **list order
    is the only thing carrying blend order** — a packer that rebuilt from a dict
    would silently reorder terrain texture blending.
  - **Size the block, then fill it in place** (`lands_layout` + `write_lands`).
    Packing into a `bytearray` first holds a second full copy in the parent —
    1.2 GB — exactly while 29 workers spawn.
  - **Drop the parent's `lands` after packing** and keep the `SharedMemory`
    handle in a module global in the worker: if it is garbage collected the
    mapping goes with it and every view becomes invalid memory. `close()` AND
    `unlink()` in a `finally`, or the segment leaks once per worldspace.

  Sharing is only worth anything if it is lossless: verified by generating one
  worldspace single-process and again through the pool — **498 files, 0
  byte-different**. Guarded by `tests/test_terrain_lod_shared_lands.py`.

  Note `worker_count()` is CPU-derived with **no memory awareness**, so any
  future stage that hands workers a large structure hits this same wall.
- **The serial LAND parse runs once PER WORLDSPACE**, and Oblivion.esm ships
  **18** of them, so the same ESM is re-scanned ~18 times before each
  worldspace's tile pool starts. Tamriel alone has **14,686 LAND records**.
  Two vectorisations roughly **halve** that parse — measured per worldspace:

  | worldspace | LANDs | before | after | saved |
  |---|---|---|---|---|
  | TES4Tamriel | 14,686 | 11.09 s | 5.57 s | 5.52 s |
  | MS13CheydinhalOblivionWorld | 1,762 | 3.25 s | 2.39 s | 0.86 s |
  | OblivionRD004 | 441 | 1.81 s | 1.67 s | 0.14 s |

  **Do not multiply the Tamriel figure by 18.** The saving scales with LAND
  *count* (~0.37 s per 1,000 records) on top of a ~1.6 s fixed cost — scanning
  the whole 613 MB file — that the vectorisation does not touch. Only Tamriel is
  large; the other 17 worldspaces have 45-1,762 records. Realistic total across
  the stage: **~9 s, of which Tamriel is ~5.5 s (62%)**. An earlier note here
  claimed ~54 s by assuming every worldspace cost the same; that was wrong.
  The two changes:
  - `_decode_land` (VHGT) ran a **33x33 nested Python loop per LAND record**.
    Replaced with integer prefix sums. The deltas are int8, so an integer
    `cumsum` is **exact** — there is no accumulation-order rounding to
    reproduce, unlike a float cumsum, which could not have matched the scalar
    loop's mixed float32/Python-float accumulator anyway. Worst height
    difference vs the old code: **0.002 game units (0.0014 cm)**.
  - `decode_land_layers` (VTXT) did one `struct.unpack_from` **per opacity
    entry** — 14.7M calls across Tamriel, since one layer carries up to
    17x17 = 289 entries per quadrant. A structured-dtype `np.frombuffer` reads
    each array in one go: **1.4x**, and verified **identical on 4,000 real LAND
    records** (`temp/vtxt_verify.py` pattern).
- Do NOT bother caching the plugin bytes across worldspaces: measured, the
  613 MB read is **0.10 s** (OS page cache) against a ~2.9 s scan. Tried and
  reverted.
- **The remaining per-worldspace fixed cost is the record walk itself**, not the
  read — `scan_land_file` walks every record in the file to find one
  worldspace's LANDs, 18 times over. Caching the *parsed* result per worldspace
  (or bucketing all worldspaces in a single pass) is the next real LOD win and
  is NOT done.
- Stage scale, for anyone timing this: a `--lod-only` run on Oblivion.esm did
  **1,584 tiles across 6 worldspaces in ~50 minutes** and was still going when a
  3,000 s harness timeout killed it at worldspace 7 of 18. **Tamriel alone is
  1,301 of those tiles (82%).** Budget accordingly — and do not put a timeout on
  a real LOD build.

## Parallelism rules (learned 2026-07-16)
<a id="parallelism-rules"></a>

- **ThreadPoolExecutor is ONLY for I/O or subprocess work** (file reads,
  papyrus.exe, xWMAEncode). Pure-Python record conversion/parsing/formatting
  holds the GIL — threads pin one core AND (when converters allocate companion
  FormIDs) make output nondeterministic. Use ProcessPoolExecutor.
- **Worker state replay pattern**: converter functions depend on module globals
  set in Phase 0 (formid offset, cell locations, WORLD_NAMES, furniture origin
  shifts, mesh bounds). Process pools must replay them via an initializer — see
  `tes5_import/navmesh/worker.py` and `tes5_import/base/convert_worker.py`.
- **Determinism contract**: the output ESM must be byte-reproducible. Process
  results in submission order (`ex.map`, not `as_completed`) and keep record
  EMISSION serial — derived ids no longer depend on call order, but group order
  still does. Verify with `tools/esm/esm_diff.py A.esm B.esm` (distinguishes real
  diffs from reorders).
- **Export format workers re-read from mmap**: `tes4_export` scans record
  offsets only (`read_file(..., parse_subs=False)`) and workers re-read/format
  from their own mmap — never pickle `Record` objects across process boundaries.
- **`unescape_value` fast path matters**: a `'\\' not in value` check made text
  parsing ~7x faster; keep C-speed scans in per-line hot paths.
- **Don't parallelize µs-level converters** (REFR/ACHR/CELL): the pickle
  round-trip costs more than the conversion. Only LAND (~0.9 ms/record) is worth
  a pool.
- **`bytes += big` is quadratic** — accumulate group contents in lists and
  `b''.join` at wrap points (CELL/WRLD builders).
- **Pool tools can exhaust memory**: some load the ~2.1 GB export index PER
  WORKER. Cap `--workers` or run single-process for those.

## FormID determinism — the save-game contract (rewritten 2026-08-17)
<a id="formid-determinism-save-game-contract"></a>

A save game stores **FormIDs**. If a rebuild gives a generated record a
different id, every save silently rebinds that object to whatever now holds the
id. And because every user converts the mod themselves, two people's builds
must agree or they cannot share a save or a patch.

So the requirement is stronger than "deterministic on one machine":

> **Same source plugin + same converter version → the same FormIDs, on any
> machine, with no shared state.** Ids must also survive a converter update.

### Two id classes

**Source records keep their TES4 id**, index-shifted into the output's load
order by `text_reader.remap_formid`. Nothing hashes or reallocates them. This
is what lets converted mods interoperate: a patch written against Oblivion's
`0001A2B3` still resolves.

**Generated records derive their id from their source**, via
`PluginWriter.derive_formid(site, key)` (`tes5_import/base/writer.py`). `site` names
the kind of record ('OTFT', 'ARMA', 'NAVM'), `key` identifies what it was
generated FROM — normally the source TES4 FormID. The id is
`md5(site, key)` mapped into `DERIVED_ID_BASE`.. and is therefore a pure
function of authored data: independent of call order, record volume, machine,
and of every unrelated change in the converter.

This replaced a bare `+1` counter whose ids were decided purely by **call
position** — so adding, removing or reordering any allocation, or a site
allocating one more id than before, renumbered everything after it.

### The one rule for new generated records

**The key must be AUTHORED data** — a source FormID, an EditorID from the
export, a TES4 model path. **Never a value our own conversion computes**: a
merged mesh name, a rounded output distance, a resolved path, a component
index. Those change when our logic changes, and the id moves with them.

Shared companions (one record serving many sources) key off the shared thing's
authored identity, not the first source that reached it — e.g. the book
inventory STAT keys on the source model path, `SOPM` on the TES4 min/max
distances, `IPCT`/`IPDS` on the source SOUN.

`navm_split.py` is the instructive case: component *order* falls out of
triangle connectivity (derived), so each split sibling keys on the sorted
**door REFRs its triangles touch** — authored ids that survive
re-triangulation.

### Collisions

Hashing into a finite space collides. Resolution **rehashes with a salt**
rather than probing to the next free slot: a probe would make an id depend on
which other keys exist, reintroducing the coupling this design removes.
`reserve_source_ids()` blocks every authored id first, so a companion can never
land on a real record.

Measured on Oblivion.esm's real source ids, 26,800 derived records:
**28 collisions (0.10%), max 1 rehash.**

Residual risk: if a future converter version adds a record that happens to
collide with an existing one, one of the two moves. Per-record and rare, versus
the old scheme where *any* change moved *everything*.

### Which record types must be stable — measured, not assumed

Ground truth is a real save's FormID array. `tools/esm/save_formid_scan.py`
decompresses an SSE `.ess` (LZ4) and reports it; cross-referenced against the
built ESM (0 unmatched of 14,368):

| Type | ids in save | Type | ids in save |
|---|---|---|---|
| REFR | 5,794 | GLOB | 567 |
| ACHR | 2,349 | **NAVM** | **564** |
| PACK | 1,179 | DIAL | 450 |
| CELL | 1,115 | QUST | 384 |

Plus MESG 41, OTFT 11, DLVW 4, VTYP 1, IPDS 1.

**NAVM is save-persisted** — the engine records navmesh obstacle/door pathing
state against it. So there is **no "build-internal" class** that may be
allocated sequentially: an earlier plan to exempt NAVM/DLVW/IPDS from hashing
would have silently broken 564 navmesh state entries per save. Everything
derived is hashed.

### `FORMID_SCHEME_VERSION`

Bumping it (in `writer.py`) deliberately renumbers every derived record. It
exists so an id-layout change is an explicit decision — changing the hash
input, the site names, or the region silently invalidates every existing save,
and the version is what records that it was intended.

### If a change WILL shift ids: tell the user, up front

Drift is the user's call, not a detail to absorb — the cost lands on players,
not on the build. Prefer the non-drifting route (key off authored data, reuse
an existing record). If drift is genuinely unavoidable, finish the work, then
**lead the final report with it**: say it explicitly, state which record types
renumber and roughly how many, say why it was unavoidable, and which
alternative you rejected. Never accept drift for a refactor or tidy-up.

This is a reporting duty, not a licence to stop mid-task.

Guarded by `tests/test_formid_determinism.py`, which checks stability across
writers, independence from allocation order, non-collision with authored ids,
and — via a subprocess at three `PYTHONHASHSEED` values — that Python's
randomised `hash()` never reaches an id.


## One heavy job at a time
<a id="one-heavy-job-at-a-time"></a>

**Code:** `core/heavy_lock.py`, called from `convert.py:_run_steps` (and the patch
build) and `tools/release/create_lod.py:main`.

Two pooled stages at once exhaust RAM on a 32 GB box. On 2026-09-26 a
Morrowind_ob navmesh pool (29 workers) died as `BrokenProcessPool` beside
another session's Nehrim import. A TR_Mainland import then lost 33 ENCH
records to bare `MemoryError`s (an empty message in the log) and its pool died
the same way. The written rule ("check load first") did not stop agents from
launching blind, so the check is now mechanical.

`hold_heavy_lock` takes the named mutex `Local\TESConversionHeavyJob` for the
rest of the process. A second `convert.py` or LOD bake WAITS instead of
failing. It prints the holder, recorded in `logs/heavy_job.txt`, at once and
again every 5 minutes.

- **No work queued twice.** A waiting job leaves a ticket in
  `logs/heavy_queue/<pid>.json` naming its work: plugins, steps, `--only` and
  `--mesh-subdirs` scopes, and the settings that change output. A newer job whose
  work covers a queued ticket's (`covers`) marks it replaced. The replaced job
  stops waiting, follows its replacement, and exits with that job's exit code,
  so its launcher still learns the result. The newer job runs the newer code.
  A job replaced in the instant it gets the lock hands the lock on. The running
  job is never replaced. Dead waiters' tickets are swept by the next arrival.
- **No stale locks.** The kernel releases a mutex whose owner dies, however it
  dies; the next waiter gets `WAIT_ABANDONED`, which counts as acquired.
- **No self-deadlock.** The holder sets `TESCONV_HEAVY_LOCK_HELD`, so a child
  it spawns, such as a nested `convert.py`, skips the wait. Pool workers never
  run `main`.
- **Not in `create_pool_job`.** The GUI calls that at import, and would hold
  the lock for its whole life. The GUI's per-step `convert.py` processes each
  take it instead.
- Only runs that convert take it. `--help`, `--list-mods`, the mod commands and
  `--dry-run` never wait.
- It is a courtesy, never a failure: off Windows, or if the mutex cannot be
  made, the run proceeds unlocked.

## Process containment — orphaned workers (learned 2026-07-29)
<a id="process-containment-orphaned-workers"></a>

`process_job.py` puts the pipeline in a **Windows Job Object**. Two properties,
one mechanism:

- `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` — when the parent dies the kernel kills
  every process in the job. This covers the cases no Python cleanup can: a
  crash, Task Manager "End task", the console window closed. The GUI's
  `_kill_process_tree` only ever covered the deliberate Cancel button.
- `JobMemoryLimit` — a committed-memory ceiling across the whole job.
  **OFF by default**; opt in with `TESCONV_JOB_MEM_GB=<gb>`. See the measured
  trap below before enabling it.

**A job-wide memory cap divides by the worker count — do not "just set one".**
Measured 2026-07-29: a ceiling of available-RAM-minus-4GB (13.1 GB) sounded
generous but was shared across the import stage's **29 workers**, leaving
~0.45 GB each. A previously-healthy `--import-only` then failed 48 furniture
marker NIF reads. Two things made this nasty to diagnose:

- The failures surfaced as a mix of `MemoryError` **and a misleading numpy
  `ImportError`** ("you should not try to import numpy from its source
  directory"). numpy misreports an allocation failure during import as a
  source-tree problem — it is not a `sys.path`/CWD bug, it is OOM.
- `furniture_model_info_job` catches broad `Exception`, so each failure became a
  per-file "failed to read" line and the stage **silently degraded** to 37/85
  markers while still exiting 0.

A cap safe for 29 workers must sit near total RAM, where it protects nothing the
OS would not. Hence: off by default, and orphan containment does not depend on
it. Verified with the cap off: 0 MemoryErrors, all 85 markers resolved.

**Why orphans were expensive:** `subprocess_flags.configure_multiprocessing()`
points `multiprocessing` at `pythonw.exe`, so leaked workers are console-less
and near-invisible in Task Manager. They kept the multi-GB export index resident
and held handles on `output/`, so the *next* run had less RAM and hit file
permission errors — which is what made a reboot look necessary. It never was:
`taskkill /F /IM pythonw.exe` reclaimed it.

### Measured contracts (verified 2026-07-29, Win11 / Python 3.14)

- **A worker MUST close its own handle to the job after assigning itself.**
  `KILL_ON_JOB_CLOSE` fires only when the *last* handle closes. A worker holding
  one keeps the job — and every process in it — alive after the parent dies.
  Measured both ways: handle leaked → orphans survive a parent kill; handle
  closed → terminated immediately. This is the whole mechanism.
- **Job membership is inherited automatically.** A process spawned by a job
  member joins the same job, so `create_pool_job()` in `convert.py` covers all
  ~25 pool sites *and* the helper .exes (ffmpeg, hkxcmd, BSArch, LODGen) with no
  per-site changes. Verified with a pool that has no initializer at all. The
  explicit `join_pool_job()` calls in the initializers cover only the case of a
  worker whose parent chain was re-parented.
- **Nested jobs work.** GUI job → `convert.py` job → workers: killing the GUI
  still takes the entire tree down (Win8+ allows nesting).
- **`AssignProcessToJobObject` rejects the `GetCurrentProcess()` pseudo-handle**
  with ERROR_INVALID_HANDLE; a real handle from `OpenProcess` is required.
- **Handle argtypes must be pointer-sized.** ctypes defaults to `c_int`, which
  truncates handles on x64 — assignment then *reports success* against a garbage
  handle and silently contains nothing.
- The memory cap surfaces in a worker as a normal catchable `MemoryError`
  (measured: 960 MB allocated against a 1 GB cap), and the parent survives with
  the pool intact — not a `BrokenProcessPool`.

### BrokenProcessPool causes

It is always a symptom: a worker died without returning a result. Known causes
here, in the order worth checking:

1. **Orphan/kill timing** — fixed by the job object above.
2. **An initializer that raises.** An exception inside `initializer=` cannot be
   returned to the parent, so it surfaces as an opaque `BrokenProcessPool` with
   no traceback — and the worker's stderr is invisible when multiprocessing runs
   `pythonw.exe`. `asset_convert/ui/book_inam.py` and
   `import_main._precompute_navmeshes` both guard this by running the
   initializer once in the parent first.
3. **A C++ exception escaping a native extension** (verified 2026-07-29, this is
   what broke the Nehrim import). See the section below.
4. **RAM exhaustion.** Historically real (see the `navm_worker` docstring: a
   heavy worker module cost several GB of RSS per child), which is why that
   module is deliberately light.

### A native extension that aborts (Nehrim import, fixed 2026-07-29)

**Symptom.** `--import-only` on Nehrim died at "Generating 2929 navmeshes" with a
bare `BrokenProcessPool` and no traceback anywhere. It reproduced at **4**
workers as readily as 29 and failed after **0** results, which rules out memory
pressure and worker count — the giveaway that one specific job kills its worker.

**How to find the culprit fast.** Do not bisect the pool. Run the jobs
*in-process* in dispatch order, flushing each job's identity BEFORE the call
(`temp/navm_find_killer.py`); a hard crash then leaves the culprit as the last
line printed. That named `job[27]`, cell `011E4FEC`, and — because the abort
happened in-process — Python printed the C-level frame:
`corridor_grow.py:93 in grow_batch` → `_navgrow_native`.

**Root cause, in two layers.**

- *The data.* Nehrim ships REFRs with uninitialised placement floats: 17 records
  across 10 base objects carry `PosY = 8.936455989415117e+17` (with
  `PosX = 1.68e-36`), e.g. REFR `001E57C4` in cell `001E4FEC`. Oblivion.esm has
  **zero** such records, which is why only Nehrim hit this. These are real
  exported values, not an export bug.
- *The code.* `TriGrid::build` in `native/src/grow.cpp` buckets triangles into a
  **dense** `nx*ny` array sized from the soup's XY *extent*, with no upper bound.
  One outlier vertex put the extent at 8.9e17 units → 5.4e14 buckets → a
  **4-billion-GB** allocation. The `std::bad_alloc` was thrown inside
  `Py_BEGIN_ALLOW_THREADS` with **no `try`/`catch` anywhere in the extension**, so
  it reached `std::terminate()` and the worker died by `abort()` (exit code 3).

**Why it was invisible.** Three failures compounded:
- A C++ throw with the GIL released cannot become a Python error, so there was no
  traceback to return.
- `navm_worker.run_job` caught per-cell errors and `print`ed them — but workers
  run under `pythonw.exe`, where stdout goes nowhere.
- The parent only counted results, so it never reported which cells produced no
  navmesh.

**The fixes (all four are load-bearing).**
- `grow.cpp`: reject non-finite coordinates, bound the grid at `kMaxBuckets`
  (16M; a 4096-unit exterior cell is 33x33 = 1,089, the largest real interior
  measured 77x83), widen the CSR accumulator to 64-bit (`int acc` could overflow
  negative and then `(size_t)acc` allocated huge and wrote out of bounds), and
  wrap **both** entry points' GIL-released blocks in `try`/`catch` that stashes
  the message and raises it as a Python `ValueError` after reacquiring the GIL.
  The same unbounded-bucket pattern existed in `levels_at`'s strip grid and
  `NeighbourField::build` (sparse maps, so no dense allocation trips first — they
  just grow until memory runs out); both are now bounded too.
- `navmesh/world.py`: drop refs whose placement is not finite or exceeds
  `_MAX_PLACEMENT` (1e7, ~50x the whole map) before any geometry is placed. Also
  guarded in `pgrd_to_navm._collect_doors` and `navmesh/build.py`
  (`float()` happily parses `nan` and `8.9e17`). The ref itself still converts
  normally — only its navmesh collision is skipped, and it is nowhere near the
  pathgrid anyway.
- `navm_worker.run_job`: **return** the error instead of printing it, and
  `_precompute_navmeshes`: print the returned failures in the parent.

**That reporting change immediately paid for itself.** With failures finally
visible, the next run surfaced two cells that had been silently shipping with no
navmesh at all: `012217C1` and `01193F44`, both `IndexError` in
`corridor_union._storey_groups`. `group_polys` is built once from `out`, but an
unmatched door strip does `out.append([s])` — so the next door's
`group_polys[gi]` ran off the end. `group_polys` now grows in step (and is
refreshed when a group gains a strip, since its cached footprint goes stale).
Both cells now build (8 KB and 80 KB of navmesh).

**Result:** 2,929/2,929 navmeshes, 0 failures, and the full Nehrim import
completes — 26,865 records, 197 MB.

## Navmesh generation (learned 2026-07-25)
<a id="navmesh-generation"></a>

Corridor navmesh generation was **9.8x** faster after four fixes, all
**byte-identical output** (13 cells A/B'd on (verts, tris) hashes; large
interiors 11-14x, small cells 1.3-2.5x). Profile with
`python tools/navmesh/perf.py --cells <A,B> --stages` (its stage timers now
wrap the CORRIDOR path; they used to wrap the deleted voxel/region/spanmesh and
so reported everything as "(other)"). Sub-stage rows are INDENTED because they
nest inside `build_union_mesh` — only top-level rows may be subtracted, or
"(other)" goes negative.

- **Never recompute a whole-mesh property inside a per-node loop.**
  `_merge_at_pathgrid_nodes` called `_tri_components` (full-mesh union-find) once
  per pathgrid node — 831 x ~4000 tris, ~60% of a large cell. It is only stale
  after an ACTUAL weld, and most nodes weld nothing, so memoise it and clear the
  memo only when a weld happens (61% -> 0.5% of build time).
- **All-pairs shapely predicates are the default hotspot.** `pa.intersects(pb)`
  over N ribbons was 7.4M scalar calls (~33%). `shapely.STRtree(polys)` +
  `tree.query(polys, predicate='intersects')` does the same box filter in bulk C.
  Same in `_same_surface_region`. **Sort the candidate pairs** — the union-find
  that consumes them must see the old nested-loop order or output shifts.
- **Memoise `_ribbon_polygon`** (pure function of the strip, called 379,250x for
  a few thousand strips; the invalid-outline buffer/union repair re-ran every
  time). Keyed on `id(strip)` while holding a reference to the strip, cleared per
  `build_union_mesh` so a worker converting thousands of cells cannot leak.
- **Set-membership beats rebuilding tuples in a loop:**
  `any((min(i,j),max(i,j)) in cset for j in sub)` over sub-sheet members was
  17.8M min/max calls; per-ribbon adjacency sets + `set.isdisjoint` replace it.
- **Batch scalar shapely into vectorised calls only where the work is Python
  overhead, not GEOS.** 9 grid samples per `_overlap_height_gap` built 274k
  `Point` objects; `shapely.points(list)` + `shapely.intersects` in one call won.
  But batching the pairwise `intersection`/`area` was **SLOWER** (17.0 -> 17.9s):
  GEOS clipping dominates and the bulk form materialises an intersection for
  every candidate instead of discarding most on a cheap area test. Measure.
- **C++ is NOT warranted here (measured).** After the above, 46% of remaining
  tottime is inside shapely/GEOS (robust boolean ops + triangulation) and only
  30% is our Python; a perfect union rewrite is Amdahl-capped at 5.3x and would
  mean reimplementing GEOS against a byte-exact output contract. `grow.cpp`
  (`_navgrow_native`) stays. `decimate.cpp`/`_navmesh_native` was DELETED — it
  served only spanmesh.
- **DELETED with the old generator:** `navmesh/voxel.py`, `region.py`,
  `spanmesh.py`, `native/src/decimate.cpp`, and their tests/fixtures in
  `tests/test_pgrd_navm.py` (which asserted collision-discovery rules the
  corridor model does not have — it derives the mesh from the pathgrid).
  Corridor geometry is verified against real cells by `tools/navmesh/check.py`,
  `navmesh_reach.py`, `navmesh_slope_check.py`.

### <a id="cache-keys-must-be-machine-independent"></a>Cache keys must be machine-independent (2026-08-09)

The navmesh geometry cache is now **published as a release asset**
([python_tools.md](../reference/python_tools.md#release--repo)), which
turned two long-standing properties of its key into bugs:

- **`os.stat().st_mtime` was in the tag.** `_navmesh_geom_cache` hashed
  `(size, mtime)` of `collision_cache.bin`. mtime is machine-local and is
  preserved by neither git nor a zip round-trip, so *every* downloader computed
  a different tag and the shared cache would have missed 100% of the time.
  Measured: shifting the mtime by **one second** changed the tag and orphaned
  all 8,217 Oblivion entries. **Never put mtime, an absolute path, a worker
  count, or anything else machine-local into a cache key** — hash the CONTENT.
  Content-hashing a 19 MB collision cache costs 10 ms, once.
- **Whole-file collision hashing made every entry share one fate.** One
  replaced mesh invalidated all ~8,200 entries. Collision now enters each
  cell's hash *per mesh* (`collision_extract.collision_digest`, memoised on
  load), so a user who swaps a few meshes only loses the cells that place them.
  Cost of the finer key, measured on a 400-REFR / 120-model cell:
  **0.083 ms → 0.122 ms per cell (+0.32 s across a full 8,228-cell import)** —
  against navmesh generation measured in minutes. Digesting all 6,080 meshes
  (74 MB of float arrays) is 39 ms once per process.

Measured on Nehrim.esm (2,929 cells, 29 workers), real `--import-only` runs:

| run | navmesh stage | cache hits | whole import |
|---|---|---|---|
| cold (tag changed, full regen) | **192.08 s** | 0 | 205.13 s |
| warm | **3.81 s** | 2,918 / 2,929 (99.6%) | 15.80 s |

**50x on the stage, 13x on the whole import**, and the ESM is identical
(180,578,463 bytes both ways, 27,014 records / 0 errors). The 11 non-hits are
cells whose geometry legitimately depends on something the entry does not
cache. Expect **one** full regeneration after any navmesh source change — that
is the tag doing its job, not a cache bug.

The collision cache is **byte-reproducible** across worker counts (verified:
identical bytes and identical content hash at `--workers 2` vs `6`), which is
what makes a shared cache viable at all — `ex.map` preserves input order and
`os.walk` is deterministic. If that ever changes, every downloader's hash
diverges and the published cache silently stops hitting.

Freshness is certified by `navmesh_geom_cache/CACHE_TAG`, written **only after
a failure-free generation pass** — never by `_navmesh_geom_cache` itself, or
merely *reading* the tag would stamp a stale cache as fresh. Comparison is
stamp-vs-tag, deliberately not mtime: a checkout or unzip rewrites mtimes and
would reject perfectly valid caches.

## Measured throughput
<a id="measured-throughput"></a>

- Export: ~8s to parse 1.17M records from Oblivion.esm, ~36s total with write.
- Import: ~28K records from Oblivion.esm, 413 MB output.
- NIF conversion: 8032 source NIFs; 7380 v20 converted (91.9%), 650 v10/v4
  copied as-is.
