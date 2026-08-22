# Changes since 0.606 (English)

Baseline: `old_project/tes4skyrim-0.606`. Р СѓСЃСЃРєР°СЏ РІРµСЂСЃРёСЏ:
[CHANGES_since_0.606.ru.md](CHANGES_since_0.606.ru.md).

**Headline:** the Creation Kit now loads the full converted `Oblivion.esm`.
It used to freeze forever on "Initializing References". Two independent
defects were behind that screen вЂ” one froze the editor, the other silently
deleted 1,852 placed objects from every build.

Scope: **16 files changed, 6 added, 0 removed.** Only 6 of the changed files
are pipeline code; the rest are tools and documentation.

---

## 1. The CK "Initializing References" hang вЂ” a crash, not a deadlock

### What was actually happening

The editor sat at 0% CPU forever, which for a year of investigation looked
like a deadlock. It is not. A real stack walk of the live process showed:

* The **main thread** was inside `BSSpinLock::Lock` вЂ” specifically its
  unbounded slow path, which after 10,000 spins degrades to `Sleep(1)` in a
  loop. That is why CPU read zero and why it never resembled a busy hang.
* A **second thread** was spinning on the same lock at the same call site.
* A **third thread had crashed.** Its stack was
  `KiUserExceptionDispatcher в†’ UnhandledExceptionFilter в†’ CKPE в†’ SleepEx`:
  CKPE's crash handler caught the fault and *parked the faulting thread*,
  which never released the spin lock it held.

`CreationKitPlatformExtendedCrashReport.log`, written 46 seconds before the
capture, named it outright:

```
EXCEPTION_ACCESS_VIOLATION at CreationKit.exe+134BEDA   mov eax, [rax+0x10]
Tried to read memory at 0x0000000000000010
R9 = "..\Shared\TESForms\World\TESObjectCELL_Reference.cpp"
```

### Root cause

Creation Kit's teleport-data init (`CreationKit.exe` 1.5.73 `+0x15f0580`)
resolves a door's XTEL link. It null-checks generously вЂ” it logs
`Could not find linked door`, `points to invalid object`, `has no parent
cell` вЂ” and then, for a door standing in an **exterior** cell, re-derives the
destination cell from the worldspace grid using **the XTEL's own
coordinates** (not the door's position). That lookup's result is passed
straight into the next call at `+0x15f0893` **with no null check**. An empty
grid square yields null, the callee reads `[this+0x10]`, and the editor dies.

Exactly one reference in Oblivion.esm triggers it. `DAPeryiteDoorTEMPREF`
(`0001ECC6`) stores an XTEL position of (2036.7, 1785.9) в†’ grid **(0,0)**,
while its partner `DAPeryiteDoorREFX` (`000E7A92`) stands at
(67326.7, 67976.7) в†’ grid **(16,16)** in `DAPeryiteRealm`, whose cells span
x[4,36] y[5,26]. Nothing occupies (0,0). Those coordinates are **authored
Bethesda data**, carried through faithfully вЂ” Oblivion never noticed, because
it teleports to the target door's parent cell and never uses the coordinates
to find a cell. The name `TEMPREF` says what the record is.

### Fix

`tes5_import/record_types/world.py`, `convert_REFR`: when an XTEL names a door
in an exterior cell and the XTEL's own coordinates fall on a grid square that
holds no cell, write the **target door's own position** instead. That is the
authored indicator, and where Oblivion would have placed the player anyway.

Supporting changes:

| File | Change |
|---|---|
| `tes5_import/record_types/world.py` | new `_WORLD_GRID_CELLS` / `_DOOR_PLACEMENT` module state + `set_teleport_grid()`; the fallback inside `convert_REFR`'s XTEL block |
| `tes5_import/import_main.py` | new `_build_teleport_grid()` (master-aware: also indexes the masters' cells and doors) and its call before the CELL/WRLD phase |
| `tes5_import/convert_worker.py` | two new `init_worker` parameters so pool workers receive the same module state вЂ” without this the fix silently does nothing in worker processes |

**Guard:** `python tools/cell_grid_check.py <esm> --teleport-cells` вЂ”
1 offender before, 0 after; exits non-zero so it can gate a build.

---

## 2. Top-level GRUP order вЂ” 1,852 references silently deleted

Found by reading the CK warning log (`ckpe.log`) of a failing run:

```
[MASTERFILE] Missing/Invalid base object (0106035D) for reference (вЂ¦)   Г—1852
[MASTERFILE] Missing base object for ref '' (вЂ¦). Ref will be deleted.   Г—1850
```

All 1,852 trace to just **18 base objects** вЂ” every one of them present in the
output, as MSTT: Oblivion's swinging/havok statics (`RootHavok01`вЂ“`07`,
`ChainDoll01`/`02`, `ChainDollArena01`, `ArenaDummy1`, `ArenaHeavyBag01`,
`TargetHeavy01`, `ArenaColumn01`, `PrisonCellChains01`,
`NecroTapestrySkinned01`, `MDTapestrySkinned01`, hanging lamps), i.e. exactly
the set `items.convert_STAT` promotes from STAT to MSTT.

The records were fine; their **group was in the wrong place**. A REFR resolves
its `NAME` *while the CELL group is parsed*, so a base-object type written
after CELL is not in the form map yet and the reference is dropped.
`Writer._group_order()` ends with "append any groups not in the canonical
order", and `MSTT` had no entry вЂ” so it landed at index 65 with CELL at 54.
`PROJ` (#64) and `LTEX` (#67) were in the same tail; `LTEX` matters because
LAND names it in `ATXT`/`BTXT`.

Ground truth was measured from the real `Skyrim.esm` and `Dawnguard.esm`,
which agree on every entry: `вЂ¦STAT SCOL MSTT PWAT GRAS TREEвЂ¦`, `LTEX` right
after `MGEF`/`SCPT`, `PROJ` just before `HAZD`/`SLGM` вЂ” all before `CELL` вЂ”
while `WATR`, `FLST` and `MOVT` genuinely do come after it.

**Fix** (`tes5_import/writer.py`): `MSTT`, `LTEX`, `PROJ` pinned at their
vanilla slots; `WATR`, `FLST`, `MOVT` pinned too so the layout no longer
depends on the order groups happened to be added in.

**Guard:** `python tools/esm_group_anchors.py <esm> --order-only`.

---

## 3. Other pipeline fixes (all confirmed correct independently)

### 3.1 Cross-cell-boundary statics not persistent
`tes5_import/import_main.py`, `_build_world_groups`. Oblivion never needs a
placed reference to be Persistent just because its bounds cross into a
neighbouring cell; Skyrim's cross-cell rendering does, and CK warns
`Ref (...) should be persistent but is not`. For every exterior REFR a
worst-case rotation-invariant radius is derived from its base STAT/TREE's
OBND (via `_resolve_obnd`, the same source `convert_STAT`/`convert_TREE`
use), and the Persistent flag is forced when the ref sits within that radius
of a cell edge. **74,444 refs forced persistent.**

### 3.2 Re-homing missed persistent refs and left `ParentCELL` stale
Same function, the "Re-home misplaced exterior refs" block. Two bugs, both
exposed by 3.1:

* It skipped any ref with the Persistent flag, on the wrong assumption that
  persistent refs load by worldspace rather than by grid. That is true only
  of a worldspace's own dummy cell (already excluded structurally), not of a
  ref merely *flagged* persistent inside an ordinary grid-tiled cell вЂ”
  exactly what 3.1 creates in bulk.
* Moving a ref between `by_cell` buckets never updated its `ParentCELL`.
  Persistent refs get an XLCN via `_reference_location()`, which reads
  `ParentCELL` directly, so a stale value resolved the wrong cell's Location
  в†’ `Ref is not in its persistence location`. The write-back stores the **raw
  TES4-space** FormID string, because `get_formid()` remaps on every read and
  storing the already-remapped value would double-remap it.

**38 refs re-homed** (both fixes are needed together).

### 3.3 One-sided ledge portal link
`tes5_import/pgrd_to_navm.py`, `_pack_nvnm`'s ledge-linking loop.
`_open_edge_towards` is evaluated independently per side of a hi/lo drop
pair, and asymmetric geometry can make one side resolve an open facing edge
while the other does not вЂ” a one-sided link CK's validator rejects by name
("Bad portal navmesh ID/triangle index вЂ¦ the cell needs to be refinalized").
Measured: **1 of 234,612** portal links (`FortRayles`/`0x1015e31`). Now both
directions must resolve before either is committed; a half-resolved pair is
dropped entirely.

### 3.4 `navm_split.py` split every disconnected interior
`split_disconnected_interiors` exists for a real bug вЂ” a same-cell teleport
door pair with both ends in one NAVM, which the engine cannot portal. It was
splitting on `ncomp > 1` alone. Measured: of **352** interior cells with more
than one connected component, only **18** have a same-cell door pair whose
ends land in different components. It now takes `door_xtel_target` and skips
the split unless a door's XTEL target really is on a different component.
**352 в†’ 18 splits.**

### 3.5 WATR `DATA`/`DNAM` were swapped and mis-sized
`tes5_import/record_types/world.py`, `convert_WATR`. The 228-byte
water-visuals struct was written under the `DATA` tag, plus a bogus 196-byte
block under `DNAM`. Vanilla layout is `DATA` = a 2-byte Damage value and
`DNAM` = the 228-byte struct. SSEEdit's background loader flagged every
single WATR record ("unexpected (or out of order) subrecord"). Now `DATA` is
`<H` 0 (TES4 exposes no damage value; matches the vast majority of vanilla
WATR) and the 228-byte struct goes in `DNAM`.

---

## 4. New and improved tools

### New: `tools/win_stackwalk.py` вЂ” real stacks of a hung process
**The tool to reach for on any editor or game hang.** Walks every thread
through DbgHelp's `StackWalk64`, i.e. each module's own `.pdata` unwind
tables вЂ” the same mechanism Windows SEH uses вЂ” so the frames are *real*.
The previous approach *scanned* a 6 GB minidump's stack bytes for values that
happened to land inside a module, which cannot tell a live return address
from stale leftovers; it produced four rounds of confident wrong conclusions.

No PDBs needed: exported names resolve from the export tables, which is
normally enough to name the blocking primitive. Every frame prints as
`module+0xRVA`, ready for `skyrim_disasm.py --exe <module> --func 0x<rva>`.

```bash
python tools/win_stackwalk.py --watch            # unattended; then launch CK
python tools/win_stackwalk.py --name CreationKit --all-threads --windows
```

`--watch` waits for the process, prints the loading-phase text as it changes,
and dumps every stack plus the full window tree to `temp/hang_stacks.txt` the
moment CPU stays flat for `--idle-seconds`. `--windows` is not decoration: a
"hang" is often an invisible modal dialog, and nothing states that as plainly
as seeing the dialog in the tree.

### `tools/skyrim_disasm.py`
* `--func 0x<rva>` вЂ” disassembles the **whole enclosing function**, with
  bounds read from the `.pdata` unwind table. On x64 those bounds are a
  recorded fact, so this is exact with no PDBs and is almost always what you
  want over `--disasm` plus a guessed `--count`.
* `--strings 0x<rva>` вЂ” dumps consecutive NUL-terminated strings.
* Every `lea reg,[rip+X]` operand is now **annotated inline with the string it
  points at**, which turns the engine's own error text into a map of what each
  branch tests. This is what decoded the whole reference-init phase.

### `tools/cell_grid_check.py`
* `--teleport-cells` вЂ” the guard for defect 1 (see above).
* `--extents` вЂ” per-worldspace cell counts and grid extents, flagging any cell
  outside **В±100**, the range CK enforces (`cmp eax,0x64` inside
  `TESObjectCELL`'s init-references virtual). Measured on Oblivion.esm: 3 such
  cells, all at grid (-345,-3), in `E3Kvatch`/`KvatchPlaza`/`KvatchEntrance` вЂ”
  present in the TES4 export too, so authored leftover data, not a conversion
  bug. CK logs and moves on.

### `tools/esm_group_anchors.py`
* `--order-only` вЂ” the guard for defect 2. Canonical order is measured from
  the real Skyrim.esm/Dawnguard.esm; `--reference <plugin>` re-derives it
  instead of using the built-in copy. Fast enough to gate a build.

### `tools/ck_strref.py`
Required a `.text` section larger than 16 MB (tuned for `CreationKit.exe`) and
crashed on every DLL. Now picks the largest `.text` whatever its size, so it
works on the small `CKPE.*.dll` too.

---

## 5. Documentation

| File | Change |
|---|---|
| `docs/ck_reference_init_hang.md` | **new** вЂ” the full investigation: the diagnosis, the decompiled map of the "Initializing References" phase, the group-order defect, what was ruled out, and the methodology |
| `docs/override_conversion.md` | new section: **a base-object type's top-level GRUP must precede `CELL`** |
| `docs/python_tools_reference.md` | entries for `win_stackwalk.py` and the new flags |
| `docs/world_land_navmesh_notes.md` | the one-sided ledge portal link and the over-broad navmesh split |
| `docs/record_mapping_reference.md`, `CLAUDE.md` | hang marked solved, with the "stack-walk first, read the crash log" rule |

---

## 6. Not part of the change set

`conversion_config.json`, `.conversion_state.json`, `myeasylog.log` and
`.claude/settings.local.json` differ only because they hold **this machine's**
paths and state. Do not copy them to another checkout.
`external/xwmaencode/xWMAEncode.exe` is a third-party binary that the old
snapshot simply did not include.

