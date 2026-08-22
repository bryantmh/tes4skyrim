# Objects present in the Creation Kit, missing in game

Investigated 2026-08-22/23 against `output/Oblivion.esm`, with both CK load
gates green throughout. The reported symptom: the district loads correctly in
the editor, but in game whole buildings are absent while doors and `ICSign*`
activators stand there normally.

## Why the two disagree at all

The CK loads a whole worldspace at once and draws every reference straight from
the record, at any distance, ignoring enable state. The game draws one only if
it *also* survives enable state, its enable parent, a non-zero scale, and the
cell-streaming window — and beyond that window it draws **object LOD** instead
of the object.

So "the CK is fine" rules out nothing on the game side. Every record-level gate
can be green while the world is visibly missing objects.

## The cause: our own forced Persistent flag

**Confirmed in game 2026-08-23** — reverting this brought the buildings back.

`_build_world_groups` force-persisted any exterior REFR whose base OBND radius
reached past a cell edge (74,467 refs). Those STAT/TREE objects then failed to
render while the CK kept showing them.

The diagnosis came from a controlled pair the user supplied in
`ICMarketDistrict` cell (7,17): `ICMarketBlock03House02` rendered,
`ICMarketBlock03House01` did not. Adjacent positions, same mesh family,
identical base records apart from OBND and MODL. One difference:

| | House02 (renders) | House01 (was missing) |
|---|---|---|
| RecordFlags | `0x0` | `0x400` Persistent |
| children group | 9 temporary | 8 persistent |
| XLCN | absent | present |

House01's OBND is 1251x1734 against House02's 836x796, so only House01 crossed
the threshold and got the flag.

### The premise did not survive a vanilla census

The code read the CK warning "Ref ... should be persistent but is not" as a
rule. Applying the same straddle test to `Skyrim.esm`:

| exterior STAT/TREE refs | Skyrim.esm | our build (before) |
|---|---|---|
| straddling a cell edge | 67,751 | 137,745 |
| ...of those persistent | **842 (1.2%)** | **74,467 (54.1%)** |
| not straddling | 173,864 | 486,335 |
| ...of those persistent | 4,020 (2.3%) | 113 |

Bethesda ships 66,909 cell-edge-straddling statics that are **not** persistent,
and persistence is *rarer* among straddlers (1.2%) than among refs that
straddle nothing (2.3%). There is no such rule.

Removed. Persistent exterior STATs fell 58,911 → 134, TREE 15,669 → 3, and the
pair now matches in flags, group and XLCN.

The *mechanism* — why a persistent STAT fails to render while a persistent DOOR
does not — was never established, only the correlation plus the refuted
premise. No decryptable `SkyrimSE.exe` was available to disassemble (the GOG/AE
copy is the one that works; a Steam copy is encrypted).

## Still open

Three real divergences found on the way, none of them the cause, none fixed.

**No LOD ships at all.** `tools/lod_coverage_check.py` reports 0 `.bto` and 0
`.btr` in the loose tree and in all three BSAs, for every worldspace including
`TES4Tamriel` (14,686 cells). `lod_gen.py` writes
`meshes/terrain/<EDID>/Objects/*.bto` (`asset_convert/lod_gen.py:1402,1699`),
so the stage simply never ran for this build. Everything past the uGridsToLoad
window is undrawn until the player closes to ~2 cells. Fix is
`convert.py -f <plugin> --lod-only`.

**XLCN on object references.** Vanilla `Skyrim.esm` carries XLCN on **1**
exterior REFR out of 292,884; we wrote it on 76,720 (`world.py`, on every
persistent REFR). Falsified as a render blocker by direct counter-example —
every DOOR in `ICMarketDistrict` is persistent, carries XLCN, and renders — but
wrong at that scale and worth its own pass. On ACHR it is legitimate: vanilla
sets it on 1,795 of 6,052 exterior actors.

**`0x8000` on TREE.** Vanilla sets "Has Distant LOD" on 0 of 154 TREE records;
we set it on 119 of 142. On STAT it is legal (vanilla 826 of 9,720) and was
left alone. Untouched pending a look at what it does to tree LOD.

## Fixed on the way, but not the cause

`convert_STAT`/`convert_TREE` set `RecordFlags |= 0x10000000` on any object
over 1024 units, commented "Show in World Map". There is no such STAT flag —
vanilla sets that bit on 143 FURN, 16 REFR and exactly **1** STAT of 9,720,
never on a TREE, against our 1,372 STATs and 70 TREEs. The single vanilla STAT
carrying it is `WHdockdoortrim`, a dock trim piece rather than a landmark,
which alone says the "world map" reading was invented. Removed;
`WORLD_MAP_SIZE_THRESHOLD` deleted. Removing it changed nothing in game.

## What was ruled out, with counts

All measured, not reasoned. Do not re-investigate these on this build.

| Class | Result |
|---|---|
| Missing XCLC / teleport grid | `cell_grid_check --teleport-cells` — 33,570 cells, 0 missing; 4,397 XTEL doors, 0 unresolved |
| Base-object GRUP after CELL | `esm_group_anchors --order-only` — clean |
| Poisoned floats | `float_sanity_check` — 6,207,596 floats, 0 bad |
| Refs in the wrong grid cell | `verify_ck_fixes` — 0 misplaced |
| Cell block/sub-block filing | `ICMarketDistrict` — 0 wrong of 58 cells |
| Missing mesh files | `missing_mesh_refs` — 88 of 985,311, all editor markers (`Marker_LinkMarker.nif` on our synthetic `TES4Voice_*` TACTs) and helper STATs |
| Initially Disabled, no parent | 1,007 — **exactly the authored count** |
| Enable parents | 9,661 — **exactly the authored count** |
| Scale < 0.01 | 12 — **exactly the authored count** |
| Persistent flag vs children group | 0 mismatches |
| Dangling enable parent / base | 0 |

And on the named meshes specifically (`ICMarketBlock02House03`,
`ICMarketBlock03House01` vs `House02`):

* mesh present loose **and** in the BSA; NIF version / user version / BS version
  identical to the signs that render (20.2.0.7, uver 12, bsver 83);
* parsed structure identical in kind — `BSFadeNode` root, `BSXFlags 130`,
  `bhkCollisionObject`, `NiTriShape` + `BSLightingShaderProperty`;
* `mopp_validator` and `collision_sanity` — 0 issues on each;
* every texture ships, loose and packed (22 and 28 references, 0 missing).
  The counter-example is decisive: `ICSignCopious01` is **missing** its normal
  map and renders fine.

## The rules this cost us

**An absolute count of render-blocking references means nothing.** 1,007
disabled-with-no-parent placements looks like a smoking gun and is entirely
Bethesda's authoring; so are all 12 sub-0.01 scales and all 9,661 enable
parents. Only a **delta against the authored export** is a defect.
`tools/refr_render_audit.py --export-dir <export/Plugin>` does that diff and
refuses to judge those classes without one.

**Census vanilla before writing a flag, not after.** Both defects here —
`0x10000000` and the forced Persistent — were unsourced assertions in a code
comment that a five-minute census of `Skyrim.esm` refutes outright. Both
comments read as confident explanations.

**A pair beats a theory.** Four hypotheses died on structural audits that all
came back clean. What solved it was one user-supplied pair of adjacent objects,
one visible and one not, holding mesh, cell and base record constant. Ask for
that pair early.

## A leading space in a MODL path is not a bug

`tes4\Dungeons\Caves\Exterior\ CEntranceRockMossSm01.NIF` — the space before
the filename is authored, and the BSA entry carries it too
(`meshes\tes4\...\ centrancerockmosssm01.nif`), so the hashed lookup matches.
Only the loose copy is written without it. Checking the filesystem alone
reports ~54 phantom missing meshes; consult the BSAs as well.

## Tool bugs found while investigating

`tools/missing_mesh_refs.py` was unusable and then misleading:

1. imported `output_layout` without putting the repo root on `sys.path` — died
   instantly with `ModuleNotFoundError`;
2. crashed with `UnicodeEncodeError` on a cp1251 console mid-report, throwing
   away findings already computed;
3. read `MODL` for every record type. In TES5 `ARMO`'s `MODL` is a 4-byte ARMA
   FormID, not a path — decoding it as text produced **1,798 phantom missing
   meshes** out of a true 88;
4. checked only the loose tree, never the BSAs.

All four fixed. New tools from this investigation: `refr_render_audit.py`
(differential, per-cell `--inventory`) and `lod_coverage_check.py`. Both are
documented in [python_tools_reference.md](python_tools_reference.md).

## Building on a 16 GB machine

The import stage's pool takes `cpu_total() - 3` workers, each holding ~945 MB of
its own copy of the export index. On a 32-core / 15.7 GB box that is 29 workers
and ~27 GB against 15.7 GB of RAM: the run thrashes the page file at 2,825 page
faults/s and ~2.5% CPU, looking exactly like a hang. Set `TESCONV_WORKERS=3`;
the same build then completes in 237 s.
