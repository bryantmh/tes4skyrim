# Morrowind (TES3) export

**Code:** `tes4_export/tes3_reader.py`, `tes4_export/export_morrowind.py`,
`tes4_export/morrowind_ids.py`, `tes4_export/morrowind_world.py`,
`tes4_export/morrowind_cell.py`, `tes4_export/morrowind_land.py`,
`tes4_export/record_types/morrowind.py`

Morrowind shares no container structure with TES4, so it gets a parallel reader
rather than the header-size probe FO3/FNV needed. The exporter's contract is
unchanged: emit the same KEY=VALUE vocabulary `tes5_import` already consumes, so
no new record converters are required.

Format authority is the OpenMW source at `references/openmw/components/esm3/`.

## <a id="the-tes3-container"></a>The TES3 container

| | TES4 | TES3 |
|---|---|---|
| Record header | 20 bytes (24 for FO3/FNV) | **16** — type, dataSize, unused, flags |
| Subrecord header | 6 bytes, u16 size | **8 bytes, u32 size** |
| Grouping | nested GRUP tree | **none** — a flat record stream |
| Identity | 32-bit FormID | **case-insensitive string** in NAME |
| Deletion | header flag | **a DELE subrecord** |

`esmreader.cpp:332-393` is the spec. Walking `Morrowind.esm` with a 16-byte
stride reaches exact EOF at 48,295 records: INFO 23693, STAT 2788, NPC_ 2675,
CELL 2538, DIAL 2358, GMST 1449, LAND 1390, PGRD 1194.

Strings are cp1252 and NUL-*padded*, not NUL-terminated — decoding as UTF-8
destroys every accented character in the German and French releases.

## <a id="coordinates-and-cell-splitting"></a>Coordinates and cell splitting

**World positions are carried across unchanged.** Morroblivion did not scale
them, which is measurable: the `hlaalu_loaddoor` references span
X -25737..57000 / Y -65313..-9723 in *both* Morrowind.esm and Morroblivion, and
Tel Mora's references sit at X~107k / Y~118k in both.

Only the grid changes, and only because the cell size halved — 8192 units in
Morrowind against 4096 in Oblivion (`openmw/components/misc/constants.hpp:24`).
So one Morrowind cell covers a 2x2 block of Oblivion cells:

    grid = floor(pos / 4096)

Verified against Morroblivion: **156,181 of 156,316** placed references satisfy
it. The 135 exceptions are all cell (0,0), the persistent-reference holding
cell, which is expected rather than a violation.

The quadrant suffix Morroblivion appends is `(x mod 2) + 2*(y mod 2)`, giving
`00`/`01`/`02`/`03` south-west first — measured with exactly 100 cells in each
bucket and no exceptions. Tel Mora at Morrowind grid (13,14) becomes Oblivion
(26,28) `TelSMora00`, matching the shipped plugin.


## <a id="the-synthetic-worldspace"></a>The synthetic worldspace

Morrowind has **no WRLD record at all** -- there is one implicit exterior and a
cell is located purely by its grid X/Y. TES5 requires every exterior cell to
name a parent worldspace, so one has to be minted.

Both conversion paths land in `WrldMorrowind`, and which of them owns the
record is the only difference between them:

| Morroblivion converted | worldspace FormID | WRLD written |
|---|---|---|
| yes | `01380000`, borrowed from its export | none |
| no | derived from the EditorID | one |

Defining a second WRLD under the same EditorID when Morroblivion already ships
one would split the map in two rather than join it, which is the entire point
of the Morroblivion path -- so `worldspace_record` returns nothing there.

The borrow needs `IdIndex.lookup_editor_id`, not `lookup`: Morroblivion
authored `WrldMorrowind` itself, so it is a plain name and must NOT be run
through the Morrowind EditorID escape (which would ask for `0WrldMorrowind`).
`WRLD` is in `BASE_TYPES` purely so the index carries it.

### The WRLD must name a climate that exists

`convert_WRLD` falls back to Oblivion's `DefaultClimate` (`0x0000015F`) for a
worldspace with no authored CNAM, because 57 of 84 TES4 worldspaces author
none. A standalone Morrowind conversion contains no Oblivion records at all,
so that fallback dangles -- caught by `tools/validate/dangling_ref_check.py`
as the only dangling reference in the whole 45 MB output.

The synthetic WRLD therefore authors `CNAM.Climate=00000812`, SkyrimClimate,
which is the climate vanilla Tamriel itself uses and is always present.

### NAM0/NAM9 must span the grid — an empty rectangle crashes before the menu

The synthetic WRLD shipped with `NAM0`/`NAM9` all zeros, because the exporter
never wrote the four keys `convert_WRLD` reads (`NAM0.MinX`, `NAM0.MinY`,
`NAM9.MaxX`, `NAM9.MaxY`) and `get_float` defaults them to 0.0. The result is
a worldspace whose declared object bounds are a zero-area rectangle at the
origin, while its cells span x -36..47, y 10..35.

**The game crashed before reaching the main menu.** Nothing structural was
wrong: `plugin_load_audit`, `dangling_ref_check`, `float_sanity_check` (1.84M
floats) and `refr_rotation_check` (305,858 refs) were all CLEAN, and no crash
log was written — the failure is inside plugin parsing, before CrashLogger can
catch it. This is the "a CLEAN audit is not an alibi" case: a VALUE the engine
chokes on, not a STRUCTURE it rejects.

The engine builds the worldspace extents and the object-LOD quadtree from that
rectangle at load time, so a degenerate rectangle containing 305,858
references subdivides against bounds nothing falls inside.

The fix computes the bounds from the exterior cells actually claimed —
`MorrowindContext.world_bounds()` — which is why `worldspace_record` is emitted
AFTER the cell loop rather than before it. Measured for Morrowind.esm:
`NAM0 = (-147456, -155648)`, `NAM9 = (196608, 229376)`, against Morroblivion's
own `(-290816, -180224)`..`(229376, 249856)` for the same worldspace.

A side effect confirms the field is load-bearing: with real bounds the
world-map cloud bank generator runs and the WRLD gains a `MODL`, which it
skips entirely when `NAM0.MinX` is absent.

### The parent keys are FormIDs, and the importer reads only these

The import stage binds children to parents through `get_formid`, so the
export has to write these exact keys:

| Record | Keys |
|---|---|
| exterior CELL | `ParentWRLD` |
| LAND | `ParentWRLD` **and** `ParentCELL` |
| exterior REFR | `ParentCELL` |

Anything else is silently ignored -- there is no error and no warning. An
early version emitted `ParentWorldspace=WrldMorrowind` (a name, not an id) on
cells and `CellX=`/`CellY=` on LAND; `import_main.py` reads neither, so every
exterior cell would have imported as an INTERIOR and every LAND would have
been orphaned.

### All four quadrants are always emitted

A Morrowind cell splits into four Oblivion-sized cells, and the terrain splits
with it whether or not anything is placed in a given quarter. Emitting only
the quadrants that held a reference stranded **1,060 of Morrowind.esm's 5,560
LAND quadrants** (19%) -- terrain naming a cell that was never written.

Emitting all four raises the exterior cell count from 4,500 to 6,750 and
leaves 0 orphaned LAND records. A reference sitting outside its own cell's
four quadrants -- which Morrowind tolerates -- still adds the grid square it
actually falls in, so no reference is lost either.

## <a id="morroblivion-editorid-escape"></a>The Morroblivion EditorID escape

ESPConverter allocated FormIDs sequentially as it walked its input, so **they
are not reproducible** and must never be chased. Its EditorIDs, however, are a
deterministic escape: `0` + the Morrowind ID with each non-alphanumeric
character replaced by a letter.

| Char | `_` | space | `,` | `'` | `:` | `-` | `.` |
|---|---|---|---|---|---|---|---|
| Code | `U` | `S` | `V` | `A` | `X` | `D` | `P` |

`ex_redoran_hut_01` becomes `0exUredoranUhutU01`;
`Sadrith Mora, Volmyni Dral's House` becomes
`0SadrithSMoraVSVolmyniSDralAsSHouse`.

Measured **96.2% exact agreement** (8,662/9,002) with the shipped plugin across
13 base types and all three masters. The 340 differences are objects
Morroblivion authored itself (`NOSIT`, `Static`, `PNIF` variants), not encoding
failures.

The escape is used in ONE direction only. Decoding back is ambiguous, because
`A`/`S`/`U`/`V`/`X` are themselves legal ID characters — build the index by
encoding Morrowind IDs forward, never by parsing Morroblivion's.

Indexing Morroblivion's 421 MB dump costs 0.1s because only `EditorID=` and
`FormID=` lines are read. It resolves **89.3%** of vanilla Morrowind base
records (9,366 of 10,486); most of the remainder are engine markers, which are
named rather than converted and handed to the existing
`skyrim_overrides.TES4_MARKER_FORMID_TO_SKYRIM` table.

## <a id="per-type-id-namespaces"></a>Morrowind namespaces IDs by type; FormIDs do not

A TES3 string ID is unique only *within* a record type, so one name may denote
two unrelated records. Vanilla has exactly one such name — `Sound_Boat_Creak`
is both a `SOUN` and a `SCPT` — but the flat 32-bit FormID space has no
namespaces at all, and keying derivation on the bare name gave both records
`00712579`. Two records sharing a FormID is a malformed plugin: the later one
silently replaces the earlier, so the sound went missing and the script was
loaded as a sound.

Derivation is therefore keyed on `<TES4 signature>:<id>`, matching the
`wrld:` / `cell:` / `land:` / `refr:` prefixes every other derived id already
carries. `register_own` keeps a *list* of signatures per name, and `resolve`
takes the signature its call site already knows — `SCRI` names a script,
`SNAM` a sound. An untyped cell reference passes none and gets the first
registered type, which is correct because a reference can only place a
placeable object; `SCPT` and `SOUN` are never placed.

## <a id="cell-references"></a>CELL references

Morrowind has no REFR record: a cell's references follow its header fields as
repeating runs that each begin with `FRMR`. `NAME` and `DATA` appear in **both**
halves, so the split must be positional — scanning for subrecord names reads a
reference's ID as the cell's own. `cellref.cpp:70-160` is the vocabulary.

`NAM0` inside the reference stream is a "temp refs" section marker, not a field.

## <a id="land-terrain"></a>LAND terrain

A Morrowind LAND is 65x65 vertices over 8192 units; an Oblivion LAND is 33x33
over 4096 — **identical 128-unit vertex spacing**. One Morrowind cell therefore
splits into four Oblivion cells by taking 33x33 sub-grids sharing an edge row
and column, with no resampling and no interpolation.

Both games delta-encode heights identically (a running offset per row, then per
column, times eight), so a quadrant re-encodes straight back into TES4 form.
Morrowind's deltas are already whole bytes, so the round trip is **exact —
measured at zero height error** over 22 LAND records times 4 quadrants.

Two decoding steps are easy to miss:

- `VTEX` ships swizzled as a 4x4 grid of 4x4 blocks and must be de-transposed
  (`transposeTextureData`, `loadland.cpp:31-39`).
- Every `VTEX` entry is an **LTEX index plus one**; zero means "no texture, use
  the default" (`Storage::getTextureName`: *"NB: All vtex ids are +1 compared to
  the ltex ids"*).

Of 1,390 LAND records, 1,292 carry the full VNML/VHGT/VTEX payload.

### Terrain textures: the crash on entering a cell

The first build reached the main menu and then crashed loading into
`WrldMorrowind`. Every structural validator was CLEAN — `plugin_load_audit`,
`dangling_ref_check`, `float_sanity_check` (1.84M floats),
`refr_rotation_check` (305,858 refs), `land_record_check` (LAND first in group,
0 violations) — and the cell's 37 meshes were all present and readable. Two
VALUE defects, both in terrain:

**1. No texture layers at all.** `morrowind_land.py` had `decode_textures`,
`ltex_index` and `quadrant_textures` fully implemented, but
`export_morrowind.py` never called them: all 5,168 LAND records shipped with
VHGT and VNML and nothing else. Morroblivion averages ~838 layer entries per
LAND. Terrain with no base layer gives the landscape shader nothing to draw.

Each TES4 quadrant gets one BASE layer from the dominant index over its 4x4
sub-patch. Measured: 5,006 of 5,168 LANDs carry layers (4,965 with all four
quadrants); the other 162 name only the default texture in the source and
correctly carry none.

### <a id="terrain-texture-blending"></a>One base layer per quadrant is not enough

The first fix stopped at that BASE layer, on the reasoning that Morrowind has
no blend weights and so "there is no alpha layer to recover". **That reasoning
was wrong, and it threw away most of the terrain.** Morrowind has no *weight*
per patch, but it fully authors *which patch uses which texture* — and the
spatial signal is exactly what ATXT/VTXT encodes. Keeping only the dominant
index per quadrant discards the rest.

Measured over `Morrowind.esm`'s 1,292 LANDs with VTEX (20,672 layer quadrants):

| distinct textures in a 4x4 layer quadrant | quadrants |
|---|---|
| 1 | 3,456 (16.7%) |
| 2 | 8,341 (40.3%) |
| 3 | 5,935 (28.7%) |
| 4 | 2,424 (11.7%) |
| 5+ | 516 (2.5%) |

**83.3% of layer quadrants lost texture data**, and the dominant texture covered
only **67.1%** of a quadrant's area on average. Of TES4 cells, 88.1% use two or
more textures. The visible symptom is a cell painted in one flat texture.

Every non-dominant texture in a quadrant now becomes an ALPHA layer. The
quadrant's VTXT opacity grid is 17x17 vertices over the same 4x4 patches
(`wbVTXTPosition`: `pos = row*17 + col`, range 0..288), so one patch spans four
vertex cells and each vertex is shared by the one to four patches meeting at it.
A vertex's opacity is the share of those patches using the texture — 1.0 inside
a patch, a partial value on a boundary. That ramp is what vanilla writes:
sampling 300 Skyrim.esm LANDs, **62.6% of opacity values are strictly between 0
and 1**, only 30.5% are fully opaque, and quadrants carry 1–6 alpha layers
(mode 5). Morrowind's worst quadrant holds 8 distinct textures, so the
importer's six-alpha cap (`build_land_layers`, which keeps the highest-coverage
layers) binds on 4 quadrants of 20,672.

**The opacity must be computed over the NEIGHBOURING patches too, or every
quadrant and cell boundary is a hard seam.** The first version scored a
vertex only against the patches inside its own 4x4 quadrant, so an edge
vertex saw one or two patches instead of four: where quadrant A ends in
texture T1 and quadrant B begins in T2, A's edge vertex was 1.0 T1 and B's
was 1.0 T2, a step, while the same two textures meeting INSIDE a quadrant
blended over the 0.5/0.5 vertex between them. That is the "blends here,
seams there" symptom, and the seam lines are exactly the TES4 quadrant and
cell edges the Morrowind cell was split along. `pad_grid` now rings the
16x16 grid with one patch from each of the eight neighbouring cells (each
shifted the same way, see below; a missing neighbour repeats the edge), a
quadrant's patch is 6x6, and `_touching_patches` no longer clamps at the
quadrant edge. A texture seen only in the ring is emitted as a trailing
layer so the blend reaches the edge from both sides; the importer's
coverage cap drops it first when the quadrant is full. NOT yet in-game
verified.

**2. `DATA.Flags` was 3, which is not a value vanilla ever writes.**
Per xEdit (`wbDefinitionsTES5.pas`): `0x001` normals/height map, `0x002`
vertex colors, `0x004` layers, `0x008` unknown4, `0x010` auto-calc normals.
The old value claimed vertex colors that were never written and omitted
layers. Census of vanilla Skyrim's 15,564 LAND records: 31 (5,887), 25
(5,094), 29 (4,207), 28 (149) — **`3` appears zero times, and every value has
`0x008` set**. We now write **29** (`0x1D`), the vanilla value that carries
everything we emit and omits only the colors bit.

### <a id="vtex-is-offset-one-column"></a>VTEX is applied one column east of where it is stored

**Code:** `morrowind_land.shift_textures`, `export_morrowind.land_records`.

Morrowind does not draw VTEX entry `[y][x]` on the x-th column of ground. The
engine applies it one column to the east: ground column x shows entry `x-1`,
and column 0 shows the WEST neighbour's entry 15. OpenMW reproduces this in
`components/esmterrain/gridsampling.hpp` (`sampleBlendmaps`: `minRow = ... - 1`,
then `--minCellX; minRow += textureSize` when it goes negative) and, in its
older `Storage::getVtexIndexAt`, as a bare `--x` before the neighbour-cell
wrap. Only the X axis shifts; Y is stored where it is drawn.

Exporting the grid unshifted moved every texture 256 units east of where
Morrowind draws it, and the west 1/16 of every cell showed the texture the
neighbour owns — the reported "bad blending between cells". The exporter now
walks every LAND once to index the grids by cell, then shifts each grid with
its west neighbour's column 15. When the west LAND is not in the plugin (the
border with a master's terrain) the cell's own column 0 stands in, which is
the one strip that stays approximate.

### LTEX ICON is relative to Textures\\, not Textures\\Landscape\\

`convert_LTEX` unconditionally prepended `landscape\` because an Oblivion LTEX
ICON is a bare filename relative to `Textures\Landscape\`. Morrowind stores a
bare filename too, but ships the file **flat** at `textures\tx_sand_01.dds`,
so the prefix invented a folder that does not exist: all 107 terrain TXST
records pointed at a missing file.

The export now emits the full `textures\` path and `_landscape_icon` only
prepends the folder for a path that is not already rooted. Oblivion's output
is byte-identical. Measured: TXST diffuse files missing on disk went from
**107 of 107 to 2 of 107**, and those two (`Tx_MA_sandstone02`,
`tx_lavacrust00`) are in no Morrowind BSA at all and are referenced by zero
terrain layers — dead LTEX records Bethesda shipped without assets.

## <a id="pathgrids"></a>Pathgrids become PGRD, then navmesh

**Code:** `tes4_export/morrowind_pathgrid.py`

TES3 and TES4 pathgrids hold the same graph in different encodings. Confirmed
against both `wbDefinitionsTES3.pas:1989` and OpenMW `esm3/loadpgrd.cpp`:

| | TES3 | TES4 |
|---|---|---|
| Point position | `PGRP` S32 x3 | `PGRP` float x3 |
| Edge target | `PGRC` U32 | `PGRR` S16 |
| Cell link | `NAME` (interior) / `DATA` grid | `ParentCELL` FormID |
| Inter-cell | *none* | `PGRI` |

`PGRC` is a flat run grouped by each point's connection count, exactly as
`PGRR` is. Edges are stored directed and often asymmetrically -- Morrowind
walks an edge from either end regardless -- so each pair is normalized to
`(lo, hi)` and re-expanded symmetrically.

### Which cell a pathgrid belongs to

**A PGRD carries no interior flag, and its NAME does not classify it.** An
exterior's NAME is the cell or REGION name -- 311 of TR_Mainland's name a
region that is no cell at all, so no name lookup can place them. Routing on
"has a NAME" sent all 3,436 down the interior path and produced 0 exteriors.

The **DATA grid** is the indicator, and is what xEdit keys its own
`GetGridCellCallback` on. Measured over TR_Mainland.esm's 3,436 pathgrids:

| Cell kind | grid (0,0) | non-zero grid |
|---|---|---|
| interior | 2,930 | 0 |
| exterior | 0 | 195 |
| NAME is not a cell | 0 | 311 |

The split is total, so only the origin square is ambiguous -- an interior has
no grid to record and leaves DATA at (0,0) -- and there the cell name settles
it.

### The two coordinate frames

**An exterior pathgrid's points are CELL-LOCAL, an interior's are already in
its own frame.** Measured on TR_Mainland: every exterior point falls in
0..8192 whatever its cell's grid, so the cell origin has to be added before
the points mean anything in world space. Bucketing raw values instead
collapsed every exterior into quadrant (0,0), which showed up as 4 FormIDs
repeated ~280 times each and 1,188 of 4,125 records lost to collision.

Once shifted, positions carry across unscaled like every other Morrowind world
coordinate, so only the owning cell changes: an exterior pathgrid is bucketed
into the four quadrants of [cell splitting](#coordinates-and-cell-splitting)
by the same `cell_grid()` the references use.

**An edge crossing a quadrant boundary cannot stay an edge.** TES4 expresses a
crossing as a `PGRI` entry naming the local point and the FOREIGN node's world
position, which is what the navmesh corridor builder stitches cells together
with. Splitting a cell four ways turns interior edges into crossings, so the
converted graph carries `PGRI` entries Morrowind never had.

### One quadrant, one pathgrid

**A quadrant holds ONE PGRD however many Morrowind cells reach into it.** A
point may lie outside its own cell -- Morrowind tolerates that for references
too -- so two cells can land points in one Oblivion cell. Emitting a record
each gave them the same quadrant-keyed FormID: 63 collisions over TR_Mainland,
and a cell can carry only one navmesh anyway. `pathgrid_records()` therefore
takes EVERY pathgrid at once and merges per quadrant, rebasing point indices
onto each quadrant's own running list.

### Leading with an edge-bearing point

`from_pgrd.py` probes **`Point[0].Edge[0]` alone** and falls back to
nearest-neighbour topology for the WHOLE record when it is missing -- which
splitting a cell causes whenever point 0's every edge became a crossing.
Each quadrant is rotated so a point keeping an in-cell edge leads, which cut
the fallback from 173 records to 71 over TR_Mainland; the remainder are
single-point cells where no in-cell edge can exist and the PGRI exits carry
the connectivity instead.

### Measured result

Nothing is needed on the import side: `tes5_import/navmesh/from_pgrd.py`
already builds NAVM from PGRD and has no source-game branch, so these records
flow through the standard navmesh stage unchanged.

TR_Mainland.esm (Morroblivion mode), from 3,436 source pathgrids:

| | |
|---|---|
| PGRD records exported | 4,059 (2,929 interior, 1,130 exterior) |
| Points / edges | 110,405 / 133,102 undirected pairs |
| PGRI inter-cell entries | 3,730 |
| Duplicate FormIDs | 0 |
| NAVM generated | 4,059 cells, 0 errors |
| Edge links | 5,294 portals over 955 cells (85% of exteriors) |
| Door links (XNDP) | 9,421 |
| NAVI registered | 4,246 navmeshes |

## <a id="nif-4002"></a>Morrowind NIFs are version 4.0.0.2

pyffi does **not** read them out of the box — measured **0 of 60** vanilla
meshes on a first attempt. The version is `0x04000002` and block types are
inline length-prefixed strings per block rather than a header type table.

The first confirmed defect is that `NiGeometryData`'s `has_*` booleans are one
byte at 4.0.0.2 where pyffi reads four, which desynchronises the stream. Fixes
belong in `_install_morrowind_layouts`, beside the existing
`_install_early_oblivion_layouts` that solves the same class of problem for the
10.x meshes in Oblivion's BSAs.

## <a id="what-converts"></a>What the first pass converts

Measured on `Morrowind.esm` (48,295 source records, ~7s):

| | with Morroblivion | standalone |
|---|---|---|
| Records written | 317,656 | 324,628 |
| Cells | 5,634 | 5,634 |
| References | 305,858 | 305,858 |
| Base records | 1,296 | 7,968 |

The base-record difference is the whole point: with Morroblivion present, 6,672
objects are referenced rather than duplicated.

That first pass dropped 10,258 references (3.2%) whose base object was an NPC,
creature or levelled list. With actors and leveled lists exported the
standalone conversion of `Morrowind.esm` now writes (measured, 8.4s):

| | count |
|---|---|
| Records | 343,016 |
| Base records | 11,848 (NPC_ 2,675, CREA 260, LVLI 227, LVLC 116, KEYM 285, AMMO 68, SOUN 430, GLOB 73, FACT 22, CLAS 77 among them) |
| Placements | 319,249 = REFR 315,343 + ACHR 3,043 + ACRE 863 |
| ...of which synthesised door markers | 3,133, one per load door |
| References dropped | **0** |
| Dangling FormIDs across NAME, ParentCELL, XTEL.Door, XOWN.Owner, XLOC.Key, Item[], Entry[], Faction[], Relation[], CNAM.Class, SNAM/ANAM sounds | **0** |

A reference whose base record is missing crashes the engine, so `resolve`
still returns `''` for an unconverted base and such a reference is skipped and
counted rather than written; nothing in Morrowind.esm reaches that path any
more. `write_export` also deletes a record file left by an earlier run for a
type the current run no longer emits (REPA/PROB/LOCK became MISC), which
otherwise imported beside their replacements under the same FormIDs.

Every ICON is rewritten `.tga` to `.dds`: Morrowind records name icons `.tga`
but its archives ship `.dds` and the engine substitutes at load. Without the
rename all 2,908 icon references point at files that do not exist. Mesh paths
need no such fix — 7,693 of 7,699 resolve, the six that do not being dangling
in vanilla Morrowind itself.

## <a id="masters"></a>Masters

**Code:** `export_morrowind.converted_master_dirs`, `load_context`,
`morrowind_ids.load_index`.

A Morrowind plugin names its masters' objects by the same plain string it uses
for its own, so a dependent plugin can only reference what its masters
supply. The rule is: **a master's object resolves only through that master's
converted export**; an object whose master is not converted is dropped and
counted, never minted. The first version registered every master record as
the plugin's own and minted a derived FormID for each, which wrote 11,854
`NAME=` lines on TR_Mainland pointing at records nothing defines -- a REFR
whose base is missing crashes the engine.

**An unconverted master REFUSES the export**, the same contract the import
stage enforces with `MissingMasterOutputError`. The master list fixes the
plugin's own load-order byte, so exporting without one does not merely drop
references -- it renumbers every record into a master's id space. Measured
before the gate existed: TR_Mainland, whose chain is four long, took byte
`0x02` because only two of its masters were converted, putting its own records
where Bloodmoon's belong. The refusal names each missing master and the command
that converts it.

The master list is the plugin's own `MAST` chain, in its order (`record_dir`,
so an imported mod's folder resolves). The own load-order byte is the length of
that list, the TES4 convention the importer's `load_master_export` re-keys
against; a masterless plugin such as Morrowind.esm writes `0x00` exactly as
Oblivion.esm does. Each
master's index is re-keyed into the borrower's list the same way: the master's
own byte (the length of ITS header's `Master[]` list) becomes its slot, and
each of its masters is translated by name; a byte naming a file the borrower
does not load is unreachable and skipped.

The index answers under the raw Morrowind ID as well as the Morroblivion
escape, because our own exports write the raw ID as `EditorID`; before that the
converted-master tier resolved 0 of Morrowind.esm's 11,742 exported records.
Cells are indexed too -- interiors under `cell:<name>`, exteriors under
`cell:<x>:<y>` from `XCLC`, terrain under `land:<parent cell>` -- so a door or
placement into a master's cell names the master's record, and a worldspace the
master already defines is not emitted twice. When the borrower's cells reach
past the master's `NAM0`/`NAM9`, the WRLD is emitted again under the master's
FormID with the union bounds, which the importer applies as an override.

**Source set** (`morrowindSource` in `conversion_config.json`, Settings ▸
Morrowind source in the GUI): `vanilla` borrows from the declared masters;
`morroblivion` puts every converted `Morrowind_ob*` export first and drops the
three vanilla ESMs from the list, so a mod shares Morroblivion's objects
instead of shipping a second copy of every static. Morroblivion's cells and
worldspace use its own EditorIDs, so in that mode doors into vanilla interiors
link only where the escaped name matches, and exteriors do not link at all.

`TR_Mainland.esm` (four masters, 108,448 records) converts in 26s with 98.2%
of references kept when all four masters are converted first.

### <a id="vanilla-assets"></a>Vanilla meshes when the vanilla ESMs are not exported

**Code:** `asset_convert/sources/morrowind_assets.py`.

In `morroblivion` mode nothing extracts `Morrowind.bsa`, yet a dependent
plugin's wearables still list vanilla BODY parts and every worn model is
assembled on vanilla `base_anim.nif`. `resolve_mesh` looks through the
plugin's own mesh tree and its exported masters' first, then the registered
Morrowind install (`source_registry.directory_for('Morrowind.esm')`): a loose
file under `Data Files\Meshes`, else the archive entry, read through
`bsa_extract_morrowind.read_index` (header and name tables only, never the
whole archive) and extracted once into `export/morrowind_assets/meshes/`. The
BODY records themselves are read from the master ESMs resolved the same way,
by `resolve_plugin_path`.

### <a id="who-owns-a-mesh"></a>Ownership is asked of the SOURCE, not the extracted tree

**Code:** `source_meshes` in `asset_convert/sources/morrowind_assets.py`,
`MorroblivionModels.owns`.

Every routing decision below starts with "does this plugin ship this mesh
itself?". The first implementation answered it by testing the plugin's
extracted tree, `export/<plugin>/meshes`, which is wrong on a FIRST run: the
GUI's step order is Export (1), Extract (2), Creatures (5), so at export time
that tree does not exist yet. Measured on a fresh `Morrowind.esm`: every one
of its creatures took the "a master converted this" branch, `CREA.txt`
carried no `MorrowindModel` line, the creature stage split nothing, and the
importer fell through to `resolve_creature_race` -- the reported symptom is
BASE SKYRIM creatures. It never showed in development because those trees
were already extracted.

So ownership is read from the plugin's own SOURCE: its extracted tree when
that exists (an imported mod's ingest writes it before any export), else the
name tables of the archives beside the source ESM, which `read_index` reads
without touching file data. `Morrowind.esm` sits beside `Morrowind.bsa`, so
its creatures resolve identically on run 1 and run 2. The export prints the
count it owns, because a silent zero is what hid this.

### <a id="morroblivion-meshes"></a>Vanilla meshes map through their records

**Code:** `remap_vanilla_models` in `tes4_export/morroblivion.py`;
`orphan_meshes` in `morrowind_patch.py`.

Records name vanilla meshes directly too: 174 Tamriel Data and 165 Tamriel
Rebuilt WEAP records point at `w\` weapon meshes, and 1,043 distinct vanilla
non-creature meshes are named across both plugins. Morroblivion does not
rename meshes by path (4 of the 1,043 exist under `morro\`); it reworks them
per RECORD: vanilla `iron shortsword` is `0ironSshortsword` wearing
`Morroblivion\Weapons\Iron\shortsword.nif`. So a vanilla mesh maps the way
records do: the vanilla record owning that `MODL` (12,631 records over the
three ESMs), the record index, then the Morroblivion record's own model. That
resolves 857 of the 1,043; 111 belong to records Morroblivion lacks
(pauldrons, bracers, some clothes), which are gap records, and 69 are meshes
no vanilla record names at all (`f\ex_boulder00`, furniture). Both remaining
kinds are the compatibility patch's: it extracts and converts every gap
record's mesh and every ownerless vanilla mesh (animation pairs and
`base_anim*` excepted), under the same `tes4` namespace, so a plugin's record
that keeps the vanilla path finds the patch's conversion. The plugin's own
tree always wins, and CREA models take the creature table above instead.

### <a id="morroblivion-origin-shift"></a>A partly re-seated replacement hovers

**Code:** `tes4_export/morroblivion_origin.py`; applied by `remap_vanilla_models`.

Morrowind rests an object on its `RootCollisionNode` when it has one, else on
its render geometry — the two live in different frames, and vanilla
`o\contain_barrel10.nif` is an extreme case: render at +20.38, collision at
−64.01. The converted Skyrim mesh has only render geometry to sit on. So when
the remap above substitutes a Morroblivion mesh, the authored Z stays right
only if the replacement's render bottom lands on the vanilla RESTING plane.

Morroblivion re-seated the barrel from +20.38 toward −64.01 but stopped at
−36.15 — **27.86 short**, which is exactly how far its references hover (a
visible quarter of the barrel's 85-unit height, confirmed in-game). The shift
is `replacement render bottom − vanilla collision bottom`.

**The gate is the fix.** Emit nothing unless the replacement actually moved the
render frame. Measured over the 91 meshes Tamriel Data records name:

| Class | Count | Shifted |
|---|---|---|
| Render frame preserved | 68 | no |
| Re-seated cleanly onto the collision plane | 2 | no |
| Partial re-seat — hovers | 5 | **yes** |
| Geometry replaced outright, no vanilla collision | 16 | no |

Without that gate the 68 preserved meshes would all move, because vanilla
collision often sits far from render: `x_ex_t_tower_seedling` would shift
−1048.80, `d_ex_colony_door06` +508.45. The five that hover are the barrel
(+27.86) and `contain_com_sack_01/02/03` and `contain_com_chest_02`, all under
2.2 units.

Morroblivion's own 313 barrel placements were authored in the CK against its
own mesh — their Z fractions are scattered, not snapped — so they are already
correct, and the shift rides only REMAPPED records, never Morroblivion's.

The value travels as `Model.OriginShift` on the base record; the importer
subtracts it from every placed reference through the same `_BASE_ORIGIN_SHIFT`
path the furniture re-origin uses, which already handles scale and rotation
(see [asset_convert_nif.md](asset_convert_nif.md#master-owned-furniture)).

## <a id="morroblivion-gap-patch"></a>The Morroblivion gap patch

**Code:** `tes4_export/morrowind_patch.py`.

Morroblivion resolves **89.3%** of vanilla Morrowind's base records, so in
Morroblivion mode a dependent plugin routinely places objects its master cannot
supply. Measured against an index covering 89% of Morrowind.esm, Bloodmoon
alone places **50 distinct such objects across 342 references**. Dropping them
loses authored content; minting them in the plugin's own space makes every
plugin needing the same object ship a rival copy, so two mods placing
`ex_scrapwood01` would put two of it in the world.

So each plugin exports one PATCH beside itself, `<plugin> - Morroblivion
Patch.esp`, holding exactly the objects it references, a declared master
defines, and the index cannot supply. A gap is judged on all four conditions:
referenced by this plugin, not defined by it, absent from the index, present in
a master's binary. An id absent from the master binaries too is genuinely
missing and stays dropped and counted.

**The fill id is derived from the authored Morrowind string** in the shared
`mwpatch` site, never from the plugin's own id space, so every plugin that
needs `ex_scrapwood01` names the SAME record. Two patches defining it are
override-compatible rather than duplicates, which is what makes several
Morroblivion mods loadable together. The key is the authored string, so the id
is stable across machines and builds -- the same contract `derive_formid`
states for every other generated record.

The patch is generated only in Morroblivion mode. With all masters converted
the index is complete, no gap exists, and no patch is written.

### <a id="globals-are-always-filled"></a>GLOB is filled even where Morroblivion supplies it

The "absent from the index" test is right for a base object: a STAT the index
resolves is a STAT the game can place, whoever owns it. **It is wrong for a
GLOB**, because a global is not placed -- it is READ, by TES3 dialogue
conditions and MWScript, through the runtime's sidecar.

Morroblivion's global is an OBLIVION record. Morroblivion carries no TES3 data
and gets no Morrowind sidecar, so a global only it defines reaches the runtime
nowhere. Only the patch stages a sidecar the runtime reads, so the patch must
carry every vanilla GLOB whether or not Morroblivion also declares one.

Measured: `WearingOrdinatorUni` is declared by Morrowind.esm AND by
Morrowind_ob.esm (`01F2A3C9`). The index found Morroblivion's copy, the gap
scan skipped it, and the patch shipped 139 GLOBs instead of 140 -- while its
siblings `WearingLegionUni` and `wearingHelmHHDA`, which Morroblivion does not
define, were filled.

The consequence is not a missing value but an INVERTED condition. A global the
sidecar lacks makes the dialogue filter IGNORE the condition testing it
(matching OpenMW's `filter.cpp`, which returns true when
`getGlobalVariableType == ' '`), so `Greeting 0` ordinal 25 --
"The armor you wear is sacred to our Order" -- passed its only condition for
every player, and Ordinators set fight 100 and attacked on sight. A `set`
cannot repair it either: the compiler's `getGlobalType` returns `' '` for an
unknown global, so the patch's own `OrdinatorUniform` script fails to compile.

### Textures are extracted WHOLESALE, meshes are not

Meshes are pulled per gap record; textures cannot be, and the difference is not
symmetry that was overlooked.

Morroblivion reorganizes its texture tree wholesale: of the **4,385** textures
in `Morrowind.bsa` + `Bloodmoon.bsa`, **zero** share an archive path with one
Morroblivion ships (17,173). So "only what Morroblivion lacks" excludes
nothing, and the cheap filter is pure cost.

The filter was also wrong. Textures used to be derived from the meshes the
patch had just extracted, which silently assumed every mesh referencing a
vanilla texture is itself a gap record. Third-party Morrowind content breaks
that: Tamriel Data and Tamriel Rebuilt ship their OWN meshes and reference
vanilla texture names from them. Those meshes are not gap records, so their
textures were never extracted and rendered **purple** in game -- measured at
**2,438** references from converted meshes resolving to a texture present only
in `export/Morrowind.esm`, plus 308 only in Bloodmoon.

`_extract_assets` therefore writes every `textures\` entry of the vanilla
BSAs, with no reference to what any record or mesh names.

### <a id="the-patch-builds-its-own-plugin"></a>The build produces the PLUGIN, not just its export

**Code:** `_import_records` in `tes4_export/morrowind_patch.py`.

Building the patch is one user action, so it runs the whole chain: export
records, extract assets, convert assets, and **import the records into the
plugin itself**. The last step was missing, and its absence was invisible from
every angle the user could check.

`build_patch` wrote `export/<patch>/` and an asset tree under
`output/<patch>/`, then reported `records` and `assets` and declared the patch
"a master of every Morroblivion-mode conversion". No plugin file was ever
written, because nothing called the import stage for it -- `PATCH_NAME` reached
`gui_morrowind`, `export_morrowind` and this module, and never `tes5_import`.

Three things hid it:

- **The success message was true of the EXPORT.** `converted_master_dirs` gates
  on `_HEADER.txt` in the export dir, which `_write_records` does create, so
  Morroblivion-mode exports stopped reporting the patch missing and the build
  looked finished.
- **`output/<patch>/` existed and was full.** `_convert_assets` populated it
  with meshes and textures, so the folder the user would check was there --
  just with no plugin in it.
- **The record count was real.** It counts what was written to text.

So `ok` now means the plugin FILE exists: a build that wrote records and assets
but no plugin reports failure and says the records survived in `export/`. The
patch declares the converted Morroblivion plugins as its masters
([why](#the-patch-masters-morroblivion)), so `_reconcile_masters` builds its
list from the export header like any other dependent's.

The build has two doors, because the refusal that sends a user to it fires on
the command line too: the GUI menu, and `convert.py --build-morrowind-patch
"<Morrowind>/Data Files"`. Both run `build_patch`, so neither can drift.

It is written **ESM-flagged**, keeping its `.esp` extension -- the one converted
plugin that does not wait for `tools/esm/make_master.py`. Every Morroblivion-mode
plugin declares it as a master, and the engine honors a master only if the file
carries the ESM bit; the extension is not what decides. It is also what
`make_master.py` demands: that tool hard-errors on an ESM that masters a plain
ESP, so flagging any Morroblivion-mode plugin was impossible while the patch
underneath it stayed unflagged. Load order is unaffected -- the sorters that read
the extension (`morroblivion_exports`, `sibling_lod`) still see `.esp`, and
`converted_master_dirs` places the patch positionally. Because the build now lands
`<folder>/<folder>` plus a manifest, `converted_plugins` finds the patch, so it
appears in the converted list, the LOD selection and the make-master panel
where it previously could not.

## <a id="tes4-vocabulary"></a>Every exporter speaks the TES4 KEY vocabulary

The importers read TES4's key names and nothing else: `convert_WEAP` reads
`DATA.Type`/`DATA.Weight`/`DATA.Damage`, `convert_CONT` reads `DATA.Flags` and
`Item[i].FormID`, `convert_LIGH` reads `DATA.Color.R` and so on. The first
pass invented its own names (`Weight=`, `WeaponType=`, `ContainerFlags=`,
`LightColor=`, `Item[i].Object=<string>`), so every stat on every Morrowind
item was silently discarded on import -- weapons had type 0, lights had no
color and radius 128, containers were empty. `tools/validate/import_sweep.py`
and a grep of `get_int(rec, '...')` in `tes5_import/record_types/` are the
contract; each Morrowind exporter now emits exactly those keys, with the
Morrowind enum translated to the TES4 one where they differ:

| Morrowind | TES4 key | Translation |
|---|---|---|
| WPDT type 0-13 | `DATA.Type` | short/long blade 1H -> 0, long blade 2H and spear -> 1, blunt 1H and axe 1H -> 2, blunt 2H and axe 2H -> 3, bow/crossbow -> 5, thrown -> 0; arrow/bolt become **AMMO** |
| WPDT chop/slash/thrust max | `DATA.Damage` | the largest of the three |
| WPDT enchant | `ANAM` | Morrowind stores points x10 |
| LHDT color u32 | `DATA.Color.R/G/B` | little-endian RGBA bytes |
| LHDT flags | `DATA.Flags` | identical bits; 0x10 (Fire) is masked by the importer |
| CNDT weight, FLAG | `DATA.Weight`, `DATA.Flags` | Respawn 0x02 -> TES4 0x01 |
| MCDT flags & 1 (key) | record becomes **KEYM** | 285 of 536 MISC records |
| BKDT skill | `DATA.Teaches` | the 27-skill enum mapped onto TES4's 21 (medium armor -> heavy, axe/spear -> blunt, short blade -> blade, unarmored -> light armor, enchant -> mysticism) |
| BKDT isScroll | `DATA.Flags` 0x01 | |
| ALDT autocalc | `ENIT.Flags` | TES4's bit is "NO auto-calc", so it is inverted |
| REPA / PROB / LOCK | **MISC** | Skyrim has no repair or probe items; the importer had no dispatch for these signatures, so their references dangled |

Records the importer could not dispatch (REPA, PROB, LOCK) were being written
by the exporter, registered as convertible, and REFERENCED -- every placed
lockpick named a base record the import never wrote.

## <a id="equipment-slots"></a>Equipment slots and weight class

Morrowind has no biped mask; an armor's slot is its `AODT` type and a
clothing's its `CTDT` type. Each maps onto TES4's `BMDT.BipedFlags`:

| Type | TES4 bits |
|---|---|
| Helmet | Head + Hair (0x3) |
| Cuirass, Shirt | Upper Body (0x4) |
| Greaves, Pants, Skirt | Lower Body (0x8) |
| Robe | Upper + Lower Body (0xC) |
| Boots, Shoes | Foot (0x20) |
| Gauntlet, Bracer, Glove | Hand (0x10) |
| Shield | Shield (0x2000) |
| Ring | Right Ring (0x40) |
| Amulet | Amulet (0x100) |
| Pauldron, Belt | none -- no slot exists in either later game |

The raw type is exported too (`MorrowindWearableType`), because TES4's one
Hand bit cannot say which hand: the importer gives one-sided pieces their
own Skyrim slot from it (left gauntlet/bracer/glove 33, right 59, pauldrons
57/58; [body slots](asset_convert_armor.md#body-slot-layout)).

A helmet keeps the Head bit only when its part list fills the Head part
(`_covered_biped` in `tes4_export/morrowind_armor.py`); an open helm fills only
Hair and must not hide the face. OpenMW draws the head model unless an item
fills `PRT_Head`, and removes the hair for any helmet
(`npcanimation.cpp` `updateParts`). Vanilla: 40 helmets fill Head, 18 only
Hair.

Weight class is not stored either; Morrowind derives it from weight against a
per-type GMST (`iHelmWeight` 5, `iPauldronWeight` 10, `iCuirassWeight` 30,
`iGauntletWeight` 5, `iGreavesWeight` 15, `iBootsWeight` 20, `iShieldWeight`
15): light up to `fLightMaxMod` 0.6 of it, medium up to `fMedMaxMod` 0.9, else
heavy. TES4 has only the heavy bit (`BMDT.GeneralFlags` 0x80), so medium and
heavy both set it -- bonemold and orcish land in Heavy Armor, as Skyrim's own
orcish does.

### <a id="worn-models"></a>Worn models are assembled from body parts

**Code:** `tes4_export/morrowind_armor.py`,
`asset_convert/character/morrowind_armor.py`.

Morrowind dresses an actor from per-body-part `BODY` meshes: each `ARMO`/`CLOT`
carries `INDX` (part slot) + `BNAM`/`CNAM` (male/female BODY id) pairs, and the
BODY's `MODL` is the mesh. Measured on Morrowind.esm's 280 ARMO: helmets,
cuirasses, shields, gauntlets and bracers are one part each; greaves are 3-5
(groin, upper legs, knees), boots 2-4 (feet, ankles), pauldrons 1-3. Every
cuirass part is already skinned to `Bip01` bones; every other part is a RIGID
mesh authored in the frame of the attach node the engine hangs it on
(`Left Knee` under `Bip01 L Calf`, `Groin` under `Bip01 Pelvis`, ...).

The exporter resolves the parts through the BODY records of the plugin and its
masters, names a synthetic worn model per record
(`armor\morrowind\m\<escaped id>.nif`, `f\` when any female part exists) as
`Male/Female.BipedModel.MODL`, and lists the parts as
`MorrowindPart[i].Slot/.Male/.Female`. Because the biped key is present the
importer builds an ARMA exactly as for Oblivion armor. Every record with parts
lists them and names a worn model, pauldrons included, although their
`BMDT.BipedFlags` stay 0.

The mesh stage assembles the model before the ordinary conversion runs
(`assemble_armor`, called from `asset_pipeline.convert_meshes`), reproducing
OpenMW's attach rules (mirrored `Left` parts, `BoneOffset`, the shape-name
filter on skinned parts) and writing a Morrowind-version NIF shaped like the
Oblivion armor the worn path already converts: rigid parts as Prn pieces,
skinned parts re-posed into Oblivion's rest, shields in the forearm frame.
The BODY records come from the masters resolved through
`resolve_plugin_path`, so a plugin ingested from an archive still finds
`Morrowind.esm`. The mechanism and its measurements:
[asset_convert_armor.md#morrowind-armor-assembly](asset_convert_armor.md#morrowind-armor-assembly).

Armor rating is `AODT.armor x 100`: TES4 stores hundredths and the importer
passes the value straight to Skyrim's `DNAM`, which also stores hundredths.

## <a id="actors-and-placements"></a>Actors and their placements

`NPC_` and `CREA` export into the TES4 actor vocabulary (`ACBS.*`, `AIDT.*`,
`DATA.<skill>`, `Faction[i]`, `Item[i]`), so `convert_NPC_`/`convert_CREA` and
everything downstream of them -- outfits, vendor factions, Skyrim race
override, the leveled-actor shells -- run unchanged. The race is named by the
OBLIVION race FormID (`RNAM.Race=000191C1` for Dark Elf), because
`_resolve_npc_race` maps that id through `TES4_RACE_FID_TO_EDID` and
`RACE_MAP` to the Skyrim race; no RACE record is needed or written.

| Morrowind | TES4 |
|---|---|
| FLAG Female 0x01 / Essential 0x02 / Respawn 0x04 / Autocalc 0x10 | `ACBS.Flags` 0x01 / 0x02 / 0x08 / 0x10 |
| CREA FLAG Biped 0x01 / Respawn 0x02 / Weapon 0x04 / Essential 0x80 | `ACBS.Flags` 0x01 / 0x08 / 0x04 / 0x02 (swims, flies, walks keep their bits) |
| NPDT 52-byte: level, 8 attributes, 27 skills, health, mana, fatigue, gold | `ACBS.Level`, `DATA.<attribute>`, `DATA.<skill>` (the 27 folded onto 21, taking the larger where two collide), `DATA.Health`, `ACBS.SpellPoints`, `ACBS.Fatigue`, `ACBS.BarterGold` |
| NPDT 12-byte (autocalc): level, disposition, reputation, rank, gold | `ACBS.Level`, `ACBS.BarterGold`; stats come from the Autocalc bit |
| AIDT hello, fight, flee, alarm, services | `AIDT.EnergyLevel` 50, `AIDT.Aggression`=fight, `AIDT.Confidence`=100-flee, `AIDT.Responsibility`=alarm, `AIDT.Services` with Morrowind-only bits (picks 0x20, probes 0x40, repair items 0x200, spellmaking 0x8000) cleared |
| ANAM faction + NPDT rank | `Faction[0].FormID` / `.Rank` |
| CREA NPDT type 0-3 | `DATA.Type` (the enums agree) |
| CREA soul value | `DATA.Soul` by Morrowind's own gem capacities: 30 petty, 60 lesser, 100 common, 200 greater, else grand |
| CREA attack min/max x3 | `DATA.AttackDamage` = the largest max |

AI packages export as PACK records the actor lists in order (see
[AI packages](#ai-packages)); the importer's default package list still gives
every actor the vanilla sandbox fallback underneath them.

A placed NPC is an **ACHR** and a placed creature an **ACRE**, decided by the
base record's signature, which the ID index now records alongside the FormID.
A REFR whose base is an actor is not a valid Skyrim record, and a placed
leveled creature stays a REFR because the importer's Phase 0h turns those into
shell ACHRs itself.

## <a id="ai-packages"></a>AI packages

**Code:** `tes4_export/record_types/morrowind_packages.py`.

Morrowind stores an actor's packages inline on the actor (`AI_W` wander,
`AI_T` travel, `AI_F` follow, `AI_E` escort, `AI_A` activate, each optionally
followed by `CNDT`), where TES4 stores PACK records the actor lists by FormID.
Measured on Morrowind.esm: 2,817 `AI_W`, 170 `AI_T`, 70 `AI_F`, 3 `CNDT`.
Each `AI_*` becomes one PACK with a FormID derived from `pack:<actor>:<index>`
and is listed in file order, because both later engines run the first package
whose conditions pass.

| Morrowind | TES4 PACK | Location / target |
|---|---|---|
| `AI_W` distance, duration | Wander (5) | near editor location, radius = distance |
| `AI_T` x, y, z | Travel (6) | near an XMarker placed at the position |
| `AI_F` id, duration [, x, y, z] | Follow (1) | the target's placed reference [+ marker] |
| `AI_E` id, duration [, x, y, z] | Escort (2) | the same |
| `AI_A` id | Find (0) | object id, which the importer turns into Activate |

Coordinates are cell-local, and the package sits on the BASE actor, so a
destination marker goes into the exterior grid the position falls in, else into
the cell of the actor's first placement. A package whose marker cell or target
placement cannot be found is dropped and counted (`package:<kind>` in the
unresolved tally). The schedule is "any time" with Morrowind's duration in
hours, which the importer converts to minutes; the per-package idle chances have
no TES4 field and are left to Skyrim's own sandbox idles.

### A dropped package must also drop the actor's reference

The actor's `AIPackage[i]` lines are written while the actor is converted, but
a package can only be resolved after the cells and placements exist, so
`package_records` runs at the end of the export. A package that fails there was
already named by its actor, leaving a `PKID` pointing at a PACK no record
defines.

Skyrim does not reject that at load. `TESNPC::InitItem` resolves each form
pointer and, on failure, formats a warning -- `<SIG> Form '<EditorID>'
(<FormID>)` against `"UNKNOWN form"` -- for every actor, every load. Measured
from a live hang: one such NPC (`Navil Ienith`, a travel package with no
resolvable destination cell) left the game spinning in
`__stdio_common_vsprintf_s` under the data handler's per-form-type init loop,
and it never reached the main menu.

So `package_records` records each failure in `ctx.dropped_packages` and
`prune_dropped_packages` rewrites the affected actors, renumbering the
survivors so the indices stay contiguous. The invariant to hold: **every
`AIPackage[i]` names a PACK the export actually writes** -- checked as
references == records over `NPC_.txt` + `CREA.txt` against `PACK.txt`.

## <a id="creatures"></a>Creatures: one NIF split into a creature folder

**Code:** `tes4_export/record_types/morrowind_actors.py` (`export_CREA`),
`asset_convert/havok/creature_split_morrowind.py`.

The creature pipeline converts "any folder holding a skeleton.nif plus .kf
animations" (`creature_pipeline.convert_creatures`), which is Oblivion's
layout. A Morrowind creature is ONE self-contained NIF: `r\Guar.NIF` holds the
`Bip01` skeleton, 17 skinned shapes, 50 `NiKeyframeController`s spanning a
single 27 s timeline and one `NiTextKeyExtraData` whose 58 keys cut that
timeline into animation groups (`Idle: Start` / `Loop Start` / `Loop Stop` /
`Stop`, `Attack1: Hit`, `SoundGen: Left`). 52 of the 105 creature meshes
instead pair an `x<name>.nif` model with an `x<name>.kf` (a
`NiSequenceStreamHelper` whose extra-data chain names each controller's node),
which the engine prefers when present.

The exporter points each CREA at the folder layout the pipeline scans --
`Model.MODL=r\<stem>\skeleton.nif`, `NIFZ[0]=<stem>.nif` -- and records the
source as `MorrowindModel`. The split runs at the head of `--creatures-only`:
it writes `skeleton.nif` (the tree without geometry, controllers or text
keys), `<stem>.nif` (the same tree with the skinned shapes and no controllers)
and one `.kf` per animation group, sampled at 30 fps over the group's range
and written through `kf_writer.write_skyrim_kf`, so `decode_kf`,
`classify_clips` and `read_animgroup` consume them exactly as Oblivion clips.
A group with a loop segment ships only that segment (Skyrim gaits loop whole
clips), everything else ships `Start`..`Stop`. Group names map onto the
Oblivion stems the clip tables claim (`WalkForward` -> `forward`, `RunForward`
-> `runforward`, `Hit1` -> `recoil`, `Knockdown` -> `stagger`, `Death1` ->
`death`, `SpellCast` -> `casttarget`); `SoundGen: Left/Right` become
`Enum: Left/Right` footfalls, `Attack: Hit` becomes `hit`, `Sound: X` is kept,
and the other `SoundGen` cues are covered by the CREA sound slots below.

Sound slots come from `SNDG` (per-creature sound generators), exported in the
TES4 `SoundType[i].Type/.Sound` vocabulary: LeftFoot/RightFoot keep their
slots, Moan -> Idle (4), Roar -> Attack (6), Scream -> Hit (7); the swim and
Land cues have no TES4 slot.

The split runs from `creature_pipeline._creature_folders`, before the folder
walk, and resolves the source model through the plugin's tree, its masters'
and the Morrowind install ([vanilla assets](#vanilla-assets)); a folder whose
`skeleton.nif` postdates the model, its `x*.kf` and the splitter is kept. The
first in-game build shipped every creature INVISIBLE: the stage found no
folders (`creature_projects.json` held an empty map), so the importer wrote
ARMA paths no mesh backed. Split and converted, `pc_RedServKeeper_01` yields
a 9-bone body with `BSLightingShaderProperty` and its texture, a skeleton and
14 clips.

A Morrowind creature also parents RIGID shapes under bones, which the engine
draws in the bone's frame: `tr_troll_frost01` carries its head, bone helm,
three billboard eyes, hair, the log club (under `Bip01 R Hand`) and eight toe
claws that way. The pipeline knows only skinned and Prn-attached geometry and
shipped them as static shapes at the origin, so the body animated while those
parts floated. `_skin_rigid_parts` bakes each such shape's chain from its
nearest NAMED ancestor node into the vertices and gives it a one-bone identity
skin on that node, under the root, exactly the shape `rigid_skin_creature_parts`
gives an Oblivion Prn part; every skeleton NiNode is a bone
(`hkx_skeleton.collect_bones`), so the node need not be a `Bip01` bone.

Morrowind authors a creature's heading and height on `Bip01` itself: the
frost troll's root is yawed 90° at z 52.65 and its walk animates that node.
The pipeline plays the accum root as identity and keeps heading and height on
`Bip01 NonAccum` ([accum root identity](asset_convert_falloutnv.md#accum-root-identity)),
so the first build faced every creature 90° right and sank it into the
ground. `_insert_nonaccum` gives the split skeleton and body Oblivion's
layout: a `<root> NonAccum` child takes the root's transform, subtree and
skin bindings, the root becomes identity, and the root's animation track is
written under the NonAccum name, where `split_root_motion` extracts the
locomotion and keeps frame 0 (measured: NonAccum first sample (4.3, 28.1,
50.1) at 90°, forward motion 107.8 units).

### <a id="rig-root-is-the-file-root"></a>A rig authored with no scene node

**Code:** `find_skeleton_root` (`hkx_skeleton.py`), `prepare_creature_rig`
(`equipment_rig.py`).

Most rigs wrap the bones in a scene node, so the file root is a filename
(`udyrfrykte.NIF`) and the first NiNode child is `Bip01`. 9 of 151 Tamriel Data
creature skeletons instead make `Bip01` itself the file root: `tr_vermai`,
`tr_vermai_helmet`, `tr_velk`, `tr_nixmount`, `tr_nixrouge`, `tr_skylamp_01`
through `_03`, `tr_skeleton_arise01`. No Oblivion creature does (0 of 43).

Two passes assumed the scene node.

`find_skeleton_root` skipped the file root and returned `Bip01 Pelvis`, one bone
too deep, so `_insert_nonaccum` built the accum child under the pelvis and
`skeleton.hkx` had no `NPC Root [Root]` bone at all. It now returns the file root
when the root's own name is one `BONE_RENAMES` knows.

The second is the one that made the actor INVISIBLE, and it is an ALIASING bug,
not a naming rule. `_to_fade_node` wraps the NiNode root in a BSFadeNode, and
`_copy_root_frame` did `fade.name = root.name` -- a pyffi string object copied by
REFERENCE. Normally the old root is discarded and nothing notices. When the file
root IS a bone it survives as the fade node's child, so the two share one name
object and `prepare_creature_rig`'s `Bip01` -> `NPC Root [Root]` rename wrote
through to BOTH: the file ended up with two `NPC Root [Root]` nodes (measured: 9
of 278 converted creatures). The engine binds the actor through that name,
reaches the scene node instead of a bone in the animated skeleton, and nothing
renders -- collision, AI and sound all work.

The fix is `fade.name = bytes(root.name)`. Renaming the root by hand instead
leaves BOTH nodes named `Bip01` and no root bone at all -- the same invisible
actor by the opposite route.

### <a id="morroblivion-creatures"></a>Who converts a creature mesh

**Code:** `_emit_creature_model` (`morrowind_actors.py`),
`MORROBLIVION_CREATURES` in `tes4_export/morroblivion.py`, `morrowind_patch.py`.

A plugin splits and converts only the creature meshes its OWN asset tree
ships (`MorrowindModel` is emitted only then). Every other CREA keeps the
folder path derived from its mesh, so the importer binds it to the project of
whichever master converted that folder. Measured before this rule: Tamriel
Data placed 96 and Tamriel Rebuilt 473 CREA records on 58 vanilla
`Morrowind.bsa` meshes, and Tamriel Rebuilt's 94 non-vanilla models are all
referenced by a Tamriel Data CREA, so the owner always has a project.

In Morroblivion mode the vanilla meshes are Morroblivion's creatures, already
converted under `Morrowind_ob.esm`. Morroblivion renames its records freely,
so the record index pairs only 20 of the 58 (and pairs `clannfear_daddy` with
the Ogrim); `MORROBLIVION_CREATURES` is the authored pairing, vanilla mesh to
Morroblivion CREA EditorID (79 entries, each checked against the archive and
Morroblivion's export), and the export copies that record's `Model.MODL` and
`NIFZ` so the leaf-name lookup lands on the inherited project. The same
naming gap made the patch treat 352 vanilla CREA records as gaps, so it
converted 82 creatures Morroblivion already ships; `collect_gap_records` now
skips any creature the table pairs, leaving 19 records on 9 meshes
(Almalexia, Vivec, Dagoth Ur, Hircine, the Heart, dremora, `skinnpc`) that
Morroblivion has no creature for. Those are real gaps: the patch extracts
each one's `x<name>.nif`/`.kf` pair beside the model and runs the creature
stage, so it owns those projects once and every dependent plugin inherits
them.

### <a id="creature-particle-emitters"></a>The split must upgrade particle emitters before it strips controllers

`NiAutoNormalParticles` has no Skyrim RTTI, and an actor skeleton carrying one
CTDs the game during actor creation -- reported on the converted Ascended
Sleeper under both `PlaceAtMe` and a player race change, and confirmed by a
diagnostic patch that neutered only those blocks. Header version does not
save it: the shipped `character assets/skeleton.nif` read 20.2.0.7 / uv2=83
and still held 4 `NiAutoNormalParticles` + 4 `NiAutoNormalParticlesData`.

`particles_morrowind.upgrade_legacy_particles` already converts these, and
`convert_nif` runs it on the skeleton like any other creature mesh -- but it
was reaching a tree the upgrade could no longer read. Two properties of the
split combined:

- `_strip` clears `controller` on every `NiObjectNET`, which is right for the
  50 `NiKeyframeController`s the timeline holds but also removed each
  emitter's `NiParticleSystemController` / `NiBSPArrayController`. The upgrade
  reads the authored rate, speed, cone and lifetime off that controller, so
  with it gone `_as_particle_system` returns None and the legacy block is left
  exactly as it was. Measured on `r\ascendedsleeper.nif`: 4 emitters with
  controllers in the source, 4 with `ctrl=NONE` in both split outputs.
- `_strip(keep_geometry=False)` drops `NiTriBasedGeom` children, and
  `NiAutoNormalParticles` derives from `NiParticles` -> `NiGeometry`, NOT
  `NiTriBasedGeom` -- so the geometry cull never reached them either. The BODY
  came out clean only because `convert_nif`'s geometry walk discards what it
  cannot classify; the skeleton keeps its whole node tree by design.

The fix is to keep the input the upgrade needs, not to evict the blocks:
`_strip` spares a legacy emitter's controller, and `convert_nif` then upgrades
it where it already did. One line, and the effect survives the conversion, so
nothing has to be recreated later with a Skyrim-native system.

Running the upgrade inside the split instead does NOT work, and was measured:
`_write_nif` writes the intermediate at the SOURCE header (0x4000002), a
version with no modifier array and no `NiPSysEmitterCtlr` interpolator, so a
system built there round-trips back as `num_modifiers=0` with a null
interpolator -- an emitter that cannot emit. The upgrade has to run downstream
of the version bump, which is exactly where `run_morrowind_fixups` already
sits.

Measured on the Ascended Sleeper after the fix: 0 legacy blocks in the
converted `skeleton.nif`, 4 `NiParticleSystem` with the full modifier chain,
0 `NiBSParticleNode`, every `NiPSysEmitterCtlr.target` non-null (a null there
is an access violation the moment the system updates), and the authored rates
intact -- `PArray` 75.0/67.5, `SuperSpray` 112.5/112.5.

Morroblivion mode was never affected: its creature meshes are Oblivion-era and
already ship `NiParticleSystem`. Sweeps after the fix: 78 of 78 built creature
meshes clean under `Morrowind.esm`, 155 of 155 under `Morrowind_ob.esm`.

Tamriel Data owns 431 `MorrowindModel` creatures and takes the same path: 17
of its 318 distinct models carry legacy emitters (42 in total, every one with
a controller), backing 27 CREA records -- the ghosts, wraiths, liches, dremora
and Dwemer spectres. Tamriel Rebuilt itself owns none and inherits those
projects. The 9 that build a creature project all convert clean.

### <a id="orphaned-split-folders"></a>An orphaned split folder is converted forever

`split_creatures` writes only what `_sources` lists -- the `MorrowindModel`
line, emitted only for a mesh the plugin OWNS -- while the conversion stage
claims ANY folder holding a `skeleton.nif`. A folder left over from an older
export is therefore re-converted from its frozen intermediate on every run and
never re-splits, so an `[ok] <name>` line in the build log does NOT mean that
creature was split this run.

`Morrowind.esm` had 10 such folders (`byagram`, `dagothr`, `dremora`,
`g_centurionspider`, `guar`, `guar_white`, `guar_withpack`, `heart_akulakhan`,
`hunger`, `lordvivec`) against 41 live sources; none appeared in `CREA.txt` or
`creature_projects.json`, so nothing could load them. Deleting them under both
`export/` and `output/` dropped the creature stage from 4 errors to 2 -- the
`dremora` and `lordvivec` "no idle clip" failures were purely the orphans. The
2 that remain (`skeleton`, `r`) are live records and a separate problem.

Check with `_sources` rather than by eye: a folder whose name is a substring of
other records (`guar`) still greps as referenced when it is not.

## <a id="scripts"></a>Scripts

**Code:** `tes4_export/record_types/morrowind_scripts.py`, `tes3_reader.script_name`.

A Morrowind SCPT carries its source text in `SCTX`, the field the script
converter already consumes, so the record exports as a TES4 script: `SCTX`,
`SCHR.Type=0` (every Morrowind script is attached like an object script or
started by name), and `Variable[i].Index/.Name` from `SCVR`, whose order is
the index. The record has no `NAME`: its id is the 32-byte name at the head of
`SCHD`, which the reader lifts into `record_id` so scripts resolve like every
other object. A record's `SCRI` becomes the TES4 `SCRI=` FormID line.

The source grammar (`Begin name` blocks, `ref->command`) is Morrowind's, not
Oblivion's. Measured on the first build: all 632 scripts "convert" and compile,
but a `Begin <name>` block is not a TES4 block type, so its body is dropped
and every script ships as declarations only (median 159 bytes of Papyrus). The
records are attached (1,474 VMAD attachments) and inert. The converter needs a
Morrowind block mode and the `->` member-call syntax before the bodies survive;
Morrowind.esm carries 632 scripts, and 1,231 across the three ESMs.

## <a id="leveled-lists"></a>Leveled lists

`LEVI` -> `LVLI` and `LEVC` -> `LVLC` (which the importer writes as LVLN). The
flag bits are NOT the same: Morrowind's item list has Each=0x01 and
AllLevels=0x02 where TES4's `LVLF` has "calculate from all levels" 0x01 and
"calculate for each item" 0x02, so the two swap; the creature list has only
AllLevels=0x01, which is TES4's 0x01. `NNAM` is `LVLD.ChanceNone` unchanged.
An entry whose object this pass does not convert is dropped rather than
written as a null FormID.

## <a id="teleport-doors"></a>Teleport doors

A Morrowind door reference stores its destination as a position and rotation
(`DODT`) plus, for an interior, the destination cell's name (`DNAM`). There is
no destination door. Skyrim's `XTEL` REQUIRES one, and the importer emits no
XTEL at all without `XTEL.Door` -- so none of Morrowind.esm's 3,133 load doors
(2,022 into named cells, 1,111 into the exterior) led anywhere.

The missing half is **authored, not synthetic**. Morrowind ships load doors in
PAIRS: the `DODT` position is where the destination cell's own door back again
stands. Measured over Morrowind.esm, all 3,127 teleport doors have another door
reference within 1,024 units of their destination, 2,946 of them within 256. So
`_partner_door` resolves the pair by position and `XTEL.Door` names it, with the
door flagged persistent (`RecordFlags=1024`) as every vanilla load door is.

An earlier pass minted an XMarker per door instead. That is wrong, and the
vanilla census is one-sided: of Skyrim.esm's 1,722 XTEL references, **1,703
point at another DOOR reference and not one points at an XMarker** (the other
19 name 5 bases outside the dump). A door whose XTEL names a marker renders and
activates, but only ever as a plain door -- the generic "open door" prompt with
no load. 27 of 3,133 doors have no convertible destination or no partner there;
they stay plain doors and the count is reported.

The destination cell has to exist in the output: an interior by name (the
plugin's own and its masters'), an exterior by the grid square under the
destination position. A door whose destination is in neither stays a plain
door, and the count is reported.

### A persistent reference may not live in an exterior grid cell

Making the doors persistent was correct -- Skyrim.esm's XTEL doors are
1,722/1,722 persistent -- but persistence also decides WHICH cell a reference
is filed under, and that is where the first attempt went wrong. Every exterior
door and its minted marker stayed parented to the grid cell it stood in, and
all 1,113 of Balmora's and everywhere else's exterior doors vanished from the
game with no red triangle: the model loads, the reference is simply never
attached.

> "For an exterior cell, the persistent references are defined in a dummy cell
> in the worldspace group." -- UESP, Mod File Format/CELL

The census agrees and is one-sided. Skyrim.esm's XTEL doors by parent cell:

| parent cell kind | vanilla | ours (before) | ours (after) |
|---|---|---|---|
| interior | 976 | 2,025 | 2,025 |
| worldspace dummy persistent cell | 746 | 0 | 1,108 |
| **exterior grid cell** | **0** | **1,108** | **0** |

The engine loads a worldspace's persistent references by worldspace out of that
one dummy cell, never by grid, so a persistent ref filed under a grid cell is
unreachable from either path. This is Morrowind-specific only because TES4
already ships such a cell and the converted plugin inherits it; the synthetic
Morrowind worldspace had none, so `persistent_cell_record` mints one
(`cell:persistent:<worldspace>`, no XCLC, `RecordFlags=1024`) and
`_rehome_persistent` moves every persistent exterior reference into it. The
importer already files a cell flagged persistent directly under the worldspace
group rather than in the block tree.

Note the flag is Persistent ONLY. Vanilla's dummy cells read `263168`, which is
`0x400` Persistent plus `0x40000` Compressed -- compression is the importer's
concern, not a property the export asserts.

This is the same class of defect as the forced Persistent flag on exterior
statics, which made buildings invisible while every record-level audit stayed
clean. See [ck_vs_game_missing_objects.md](ck_vs_game_missing_objects.md).

### A worldspace has exactly ONE persistent cell

The rehoming above is right for a MASTERLESS plugin, which owns its worldspace
and therefore mints the dummy cell itself. A dependent plugin must not: a
worldspace holds exactly one persistent cell, the engine resolves it as a
single slot, and a second one is never attached.

Tamriel Rebuilt hit this. Morrowind_ob already supplies the Morrowind
worldspace `380000` and its persistent cell `380001` (`wrld + 1`, the vanilla
convention -- 6 of 6 of Morroblivion's worldspaces with a persistent cell hold
it). TR minted `WrldMorrowindPersistent` of its own and rehomed **3,465**
exterior teleport doors into it; every one of them was gone in-game while
`cell_grid_check --teleport-cells` reported all 9,832 XTEL destinations
resolving cleanly. The symptom is exactly the grid-cell case above -- a clean
record-level audit and no red triangle -- one level up.

Two things kept the master's cell invisible to the export:

* It is not indexed. `_add_record` keyed exterior cells on `XCLC` alone, and a
  persistent cell carries `XCLC` too (Morrowind_ob's reads `(0, 0)`), so it was
  filed as the real cell at grid (0, 0). `_add_cell` now checks the Persistent
  bit first and keys it under `persistent_key(<worldspace>)`.
* `persistent_cell_id` derived unconditionally. It now prefers
  `index.lookup_persistent`, so a dependent plugin names the MASTER's cell.

**The cell is written either way.** A plugin that rehomes a persistent
reference defines the cell holding it, re-emitting a master's as an OVERRIDE at
the master's FormID -- what `worldspace_record` already does for the WRLD, and
what the TES4 exporter does (ElsweyrAnequina.esp and Knights.esp both re-emit
Tamriel's `00023777`). Skipping it because the master owned one parented
TR_Mainland's 3,465 rehomed doors to a cell no record defined: the importer
could not bucket them back to their grid squares, so every exterior door lost
its navmesh door triangle while the interior side kept its own -- load doors
that pathfind one way only. Measured exterior XNDP links: 56 of 9,421.

### A door's partner may belong to a master

`_partner_door` scanned `doors_by_cell`, which `export_placements` fills from
THIS plugin's placements only. A dependent plugin's load door usually arrives
in a cell the master owns, where the return door is the master's -- so the
partner resolved to nothing and the door stayed plain. This is the
master-export blindness described in CLAUDE.md. `load_master_doors` scans each
converted master's `REFR.txt` for teleport doors and seeds `doors_by_cell` with
them, re-keyed through the same remap as the id index.

### DOOR also needs the FNAM flags byte

TES5 DOOR has a required `FNAM` flags byte (xEdit marks it `True`); all 235
vanilla doors carry one and 185 of them are `0`. We wrote it on 0 of 139,
because TES3 has no door-flags field to carry over at all: Morrowind's own
`FNAM` is the **display name**, a zstring, which `emit_common` already writes
as FULL. `convert_DOOR` only writes the subrecord when the export supplies
`FNAM.Flags`, so the field simply never appeared.

This is Morrowind-specific for the same reason the persistent cell was: a TES4
DOOR carries FNAM natively, so Oblivion's doors always had one.
`export_DOOR` now writes the vanilla default `FNAM.Flags=0`.

It is a real gap and worth closing, but it was **not** what restored the load
prompt -- that was the XTEL target above. Adding FNAM alone changed nothing
in game.

`MODT` was ruled out on the way: it is absent from **every** record type we
emit (0 of 3,238 STAT, 0 of 139 DOOR), including the statics that render
correctly, so it cannot explain a door-only symptom.

## <a id="map-markers"></a>Map markers

**Code:** `tes4_export/morrowind_markers.py`

Morrowind has no map-marker object. The world map draws exterior cell *names*
directly from the CELL record, so nothing in the file says "put a marker here",
and `ENGINE_MARKERS` in `morrowind_ids.py` has no 0x10 entry to find.

Naming exterior cells is also not a usable source. Morrowind names every cell
of a town the same, and we split each TES3 cell into four TES4 quadrants, so
the naive reading duplicates hard: measured on Tamriel Rebuilt, 1,143 named
exterior cells cover only 225 distinct places -- mean 5.1 cells per place,
worst case 28 ("Port Telvannis"). Vanilla Skyrim.esm carries 398 markers with
**zero** duplicated names, one per place.

### The authored signal is the teleport door

A door that exits an interior to the world is a builder's statement that a
place is entered here; the interior cell name behind it is what the place is
called. So: one marker per interior-name group, positioned at the mean of that
group's exits.

Morroblivion is the ground truth -- 486 hand-authored markers over the same
world. Matching its marker names against Morrowind's cells shows where they
came from: **300 exact matches to an INTERIOR cell name, 57 to an exterior
name, 37 to a "Place," prefix, 92 invented.** Markers are named after
interiors, not exteriors.

Morrowind's `Place, Sub` convention collapses the group: the ~40 interiors of
`Balmora, ...` become one Balmora marker. On Morrowind.esm this yields 402
markers against Morroblivion's 483, **351 of them name-matching (73%)**, and
95% of generated places correspond to a real Morroblivion marker.

### Classification

Two independent signals, and they must be applied in that order:

1. **Size** -- how many world exits the place has. Structural, plugin-agnostic.
2. **Kind** -- vocabulary in the interior cell names.

Size runs **first**, or a city holding an egg mine classifies as a mine:
Ebonheart (45 doors) became Cave and Gnisis (19) became Mine when kind ran
first, and Balmora -- the largest city in the game -- fell through to Landmark.
`camp` outranks size, because a 10-door Ashlander camp is not a town.

Only vocabulary shared between *independent* author teams is used. Comparing
token rates across Morrowind.esm and TR_Mainland, these hold within a factor of
3: `tomb`/`ancestral` (21.6% / 12.7%), `mine` (9.7% / 8.6%), `shrine`,
`shack`, `cabin`, `house`, `tower`, `manor`, `grotto`, `shipwreck`, `camp`,
`trader`, `propylon`. Rejected as one file's idiom: `yurt` (4.5% / 1.0%),
`keep`, `storage`, `office`, `plantation`, `dome`.

### Dwemer ruins are phonotactic, not vocabulary

Dwemer names carry consonant clusters no other Tamrielic naming language
forms. A cluster test generalizes where a prefix list cannot: scored against
Morroblivion's 24 labelled Elven Ruin markers it gets **13/24 recall at
1/442 false positives**, and on TR -- never used for tuning -- it finds 20
genuine Dwemer sites including `Bthalag-Zturamz`, `Mvelthngth-Schel`,
`Nchazdrumn` and `Ngelfltingth`.

Bare `nch` is **excluded**: it is ordinary word-internal spelling ("Ranch",
"Entrenched", "Uncharted") and earned nothing the other clusters missed.
Dropping it cut false positives from 3 to 1. Only syllable-initial `nch` +
vowel qualifies. Padding the list with `ldr`/`fth`/`ghn`/`dwe`/`ftm` was tried
and reverted: it tripled false positives, flagging Ald-Ruhn, every "Dwelling"
and every "-moth Fort".

The ~46% it misses are real Dwemer ruins with pronounceable names -- `Odrosal`,
`Vemynal`, `Endusal`, `Druscashti`, `Dagoth Ur`. No cluster rule reaches them,
and hardcoding them is Morrowind-specific memorization. They fall through to
the generic icon by design.

### Measured output

| Icon | Morrowind.esm (402) | TR_Mainland (1,067) |
|---|---|---|
| Landmark | 218 | 543 |
| Mine | 39 | 88 |
| Tavern | 29 | 162 |
| Daedric Shrine | 30 | 40 |
| Cave | 17 | 85 |
| Camp | 17 | 20 |
| Settlement | 15 | 53 |
| Elven Ruin | 13 | 19 |
| Oblivion Gate | 10 | 13 |
| City | 8 | 24 |
| Fort Ruin | 6 | 20 |

Morrowind's 8 cities are Vivec, Balmora, Ebonheart, Sadrith Mora, Caldera,
Ald-ruhn, Pelagiad and Suran -- correct. TR's 24 lead with Narsis (344 doors,
209 interiors), Old Ebonheart, Firewatch and Necrom, found with no TR-specific
tuning.

Known weakness: Landmark absorbs 51% of TR's markers as the fallback. Most are
genuine tombs and shipwrecks -- Morroblivion's largest category was also
Landmark, 130 of 486 -- but it has not been checked on a map in game.

## <a id="sounds"></a>Sounds

`SOUN` carries a filename relative to `Sound\` and three bytes: volume, minimum
range, maximum range. Morrowind's ranges are in units of `fAudioMinDistanceMult`
(20) and `fAudioMaxDistanceMult` (50), with 0 meaning the defaults
`fAudioDefaultMinDistance` 5 and `fAudioDefaultMaxDistance` 40 -- so a typical
sound reaches from 100 to 2,000 units. TES4's `SNDX` stores minimum in units of
5 and maximum in units of 100, which the importer scales back, so the export
writes `min x 4` and `max / 2`. Volume 0-255 becomes `SNDX.StaticAttenuation`
in hundredths of a dB (`-2000 x log10(volume / 255)`), 0 at full volume.

Morrowind's sounds are LOOSE files under `Data Files\Sound`, not in the BSA,
so the extract stage copies that tree into `export/<plugin>/sound` where the
audio stage expects to find every plugin's sounds.

### <a id="voiced-barks"></a>Voiced barks leave the sidecar

Morrowind is barely voiced, and everything it DOES voice is a bark. Measured
over Morrowind.esm + Tamriel Rebuilt + Tamriel Data: **8,549 voiced INFOs, every
one of topic type `Voice`** -- Hello 6,338, Attack 724, Hit 699, Idle 440, Flee
157, Thief 151, Intruder 39, Alarm 1. There is no voiced conversation at all.
Each names its audio outright in `SNAM` (6,152 distinct files, ~118 MB), and all
8,549 carry `Response` text, so every line can have a lip track generated.

These alone are exported as TES4 `DIAL`/`INFO` instead of `MWDI`/`MWIN`, because
**the engine moves an actor's mouth only for a voice line it plays itself**, from
a `.fuz` carrying lip data. A SNDR played through `Sound.Play` is audio with a
closed mouth. Taking the ordinary TES4 road gets the rest for free: the eight
channels already have entries in `_EDID_SUBTYPE` (Hello->`HELO`, Attack->`ATCK`,
Hit->`HIT_`, Idle->`IDLE`, Flee->`FLEE`, Thief->`STEA`, Intruder->`TRES`,
Alarm->`ASSA`), and `build_info_gates` already adds the voice-type gate.

#### The audience is authored; the folder means nothing

**Code:** `tes4_export/record_types/morrowind_audience.py`

A TES3 INFO states its speaker outright -- `ONAM` one actor, `RNAM`/`CNAM`/
`FNAM` a race, class and faction, `DATA` the rank and gender -- and
`MWDialogue::Filter` tests those fields and nothing else. So the audience is
read off the record by porting the identity half of that filter
(`filter.cpp TestInfo`, mirrored in `Audience.accepts`) over the actors the
export can see: this plugin's own, plus every converted master's, loaded from
their `NPC_.txt`/`CREA.txt` dumps.

🛑 **The folder a recording sits in is NOT its audience.** Measured across the
chain: `Vo\ord\` and `Vo\at_ord\` hold Ordinator lines whose records say
`class=guard, faction=temple` -- Ashlanders are Dark Elves with
`faction=ashlanders`, not a race; `Vo\v\` holds vampire grunts whose records
NAME each vampire (`actor=aundae vampire 1`); `Vo\ay\` is `race=t_cyr_ayleid`
and `Vo\ogr\` is `race=t_hr_ogre`; and 393 of Tamriel Rebuilt's `Vo\k\`
lines state a gender and no race at all. A hand-written folder table got
`Vo\v\` wrong in the worst direction -- mapping it to Dark Elf would have put
vampire grunts on every Dark Elf in the game.

Signatures memoise the work: 6,264 vanilla barks share **333 distinct filter
signatures**, so every audience in Morrowind.esm resolves in 0.01s.

#### Which gate each audience gets

| The record states | Gate emitted | Why |
|---|---|---|
| a vanilla race | that race's VTYP (`BarkRace`) | plugin-owned, so it cannot bleed |
| a class / a faction | `GetIsClass` / `GetInFaction` | each has its own TES4 function |
| a faction rank | `GetFactionRank >= n` | alongside the faction |
| a custom race, or one named actor | `GetIsID` over the real speakers | every custom race exports as Imperial |
| nothing | nothing | the record leaves it open on purpose |

🛑 **Race is NOT exported as a condition**, though 7,743 barks carry one.
`convert_ctda` rewrites `GetIsRace`'s parameter to a VANILLA Skyrim race, so
the condition stops scoping to the converting plugin and every bark bleeds
across plugins. The VOICE TYPE carries race and gender instead, is plugin-owned,
and is gated by `GetIsVoiceType` (426).

A CUSTOM race cannot ride a VTYP: `RNAM.Race` collapses every unknown race onto
Imperial, so an Ayleid gate would catch every Imperial. Those name their
speakers instead, which always fits -- measured largest custom-race audience is
**9 actors**, largest of any id-gated audience **14**.

#### Script locals are CONDITIONS, not identity

🛑 **A script local is runtime state and never an audience.** `T_Local_NPC` is
declared by **1,553 Tamriel Rebuilt scripts across 21 races**, and the companion
script SETS `T_Local_Khajiit` to 1 or -1 as it runs -- so the actors declaring a
variable are not the ones speaking the line. Treating declarers as the audience
was wrong in both directions.

Each local travels as its own condition: the export emits TES4
`GetScriptVariable` (53) with the authored name beside it
(`Condition[i].Variable`), and `convert_script_var_ctda` turns that into
`GetVMScriptVariable` (630) plus a `CIS2` naming the mangled Papyrus property.
The name is stated because a bark names no reference, so the usual
REFR->base->SCRI chain has nothing to walk. Built output carries
`::TR_abomination_var` on 20 grunts, `::TR_Map_var` on 116 regional lines and
`::TR_NecromOrd_var` on 49.

A `NotLocal` rule (kind `C`) is the local rule NEGATED -- its comparison is
inverted on export.

#### <a id="bark-conditions"></a>🛑 Every other rule is a condition too, or the line goes

**Code:** `tes4_export/record_types/morrowind_bark_conditions.py`

The identity gate alone let every Hello play to everyone: "Diseased! Go away.
You make us sick!" (`PcCommonDisease == 1`), "Hiss!" (`PcVampire == 1`) and
the guild-rank greetings (`DNAM` + PC rank) all reached healthy, unaffiliated
players. Measured over Morrowind + Tribunal + Bloodmoon + Tamriel_Data +
TR_Mainland (10,300 barks), the rules that were being dropped:

| Rule | Barks | Becomes |
|---|---|---|
| `Random100` | 6,458 | `GetRandomPercent` |
| disposition (DATA) | 6,028 | `GetDisposition` -> relationship rank |
| `PCRace` | 1,437 | `GetPCIsRace`, the set `RaceCheck` numbers 1..10 |
| cell (`ANAM`) | 1,334 | runtime GLOB `cell:<prefix>` |
| PC faction (`DNAM`) / PC rank | 725 / 693 | `GetFactionRank` on the player |
| Health % / PcHealth % | 419 / 416 | `GetHealthPercentage` (TES5 430) |
| PcExpelled | 415 | `GetPCExpelled` on the stated faction |
| PcClothingModifier | 257 | `GetClothingValue`, naked-or-not only |
| journal | 252 | runtime GLOB `journal:<id>` |
| PcCrimeLevel | 190 | `GetCrimeGold` |
| SameFaction / SameSex / SameRace | 139 / 88 / 51 | `Same*AsPC` |
| Weather | 139 | `GetIsCurrentWeather` over `mw*` WTHRs where loaded, else runtime GLOB `weather`; outdoors only |
| FactionRankDifference | 122 | `GetFactionRankDifference(F, PlayerRef)`, sides swapped |
| FriendHit | 113 | `GetFriendHit(PlayerRef)` |
| PcCommonDisease | 88 | `GetDisease` on the player |
| PcVampire / PcCorprus | 39 / 2 | `HasMagicEffect` of `MW133Vampirism` / `MW132Corpus` |

A player-side rule sets the run-on-target bit and `Condition[i].RunOn=Player`;
the importer points it at PlayerRef. `GetFactionRankDifference` was read off
the 1.6.1170 condition callback (0x32bd80): SUBJECT rank minus the parameter's,
so TES3's player-minus-speaker swaps sides.

`GetClothingValue` scores worn clothing 0..100 by coverage while TES3 sums its
gold value, so only a rule that splits naked from dressed (`<= 0`, 95 barks)
carries across; a gold threshold drops the line.

**Dropped, because Skyrim cannot ask:** CreatureTarget (134; `GetIsCreature`
is always 0 in Skyrim), Reputation (43), Werewolf (7), Alarm (7),
RankRequirement (7), FacReaction (6), the eight attributes, and a
speaker-faction rule (PcExpelled, FactionRankDifference, PC rank) on a bark
that states no faction.

Built: Morrowind.esm keeps 4,306 of 4,513 barks, the patch 3,812 (207 drop
on a rule in each); the most conditions on one line is 21, under the 22
ceiling. Every GetDisease, HasMagicEffect and player GetFactionRank lands as
Run On Reference = PlayerRef.

#### Measured result

Built ESMs, both conversion modes:

| Plugin | Barks | With no gate | Worst CTDA count |
|---|---|---|---|
| Morrowind.esm | 4,608 | 0 | 17 |
| Tamriel_Data | 3,792 | 0 | 5 |
| TR_Mainland | 391 | 0 | 8 |

The engine drops a line past ~22 conditions (vanilla max 22, max OR-run 20), so
every one fits. Checked separately, **no bark admits an actor TES3 would not
have given it** in either mode, with one stated exception: a `factionless`
filter has no single TES4 equivalent and becomes one `GetInFaction == 0` per
faction the speakers could hold. That fits in vanilla (15 exclusions) but needs
25 under Tamriel Rebuilt, past the ceiling, so 41 generic Imperial greetings
fall back to the race VTYP alone and may also reach faction Imperials -- who
already share 104 unrestricted greetings from the same pool.

#### <a id="the-patch-owns-vanilla-barks"></a>The patch owns vanilla's barks

**Code:** `tes4_export/morrowind_patch.py`

In Morroblivion mode vanilla's 4,608 voiced barks have no owner. Morrowind.esm
is not a conversion target -- Morrowind_ob.esm replaces it -- and Morroblivion
converted **19,610 INFOs, not one of them voiced**: it reuses Morrowind's audio
files but never carried the bark records across. A dependent plugin only ever
exports its OWN dialogue, so nothing supplies them.

The compatibility patch does, for the same reason it owns the gap sounds: one
shared master, converted once, declared by every Morroblivion-mode conversion.
`GAP_TYPES` cannot express this -- it filters base objects by record id, while
DIAL/INFO are a sequential stream whose INFOs are identified by `INAM` and
belong to the last DIAL seen -- so `collect_bark_records` walks the vanilla
ESMs separately, keeping the topic order the exporter depends on and carrying
the NPC_/CREA/SCPT records the audience resolver needs.

Nothing is duplicated: a voiced line exists in exactly one place, and
Morroblivion has none of them.

#### <a id="the-patch-masters-morroblivion"></a>The patch masters Morroblivion

**Code:** `tes4_export/morrowind_patch.py` `_write_records`,
`tes5_import/pipeline.py` `_prescan_special_records`

The patch is useless without Morroblivion, so it declares every converted
`Morrowind_ob*` export as a master and resolves names through `load_context`
exactly as Tamriel Rebuilt does. Its own records take the load-order byte that
follows them; only the byte moved, the hashed low 24 bits did not, and the
engine identifies a form by plugin plus those 24 bits, so no save is affected.

It was masterless at first, which left it unable to name any faction, class or
actor Morroblivion holds. Measured on the vanilla ESMs: 192 voiced barks state
a faction and the masterless export kept **0** positive `GetInFaction` tests,
and 494 of 4,572 barks named something it could not reach.

It still CREATES its own voice types, origin faction and `TES4*` support
records. Those live in Oblivion.esm, which is not in its master list, so
`_adopt_master_special_records` finds none to adopt and the import falls back
to creating them -- the rule is "adopt what a master supplies, create what none
does", not "a plugin with masters never creates". Tamriel Data and Tamriel
Rebuilt keep adopting the patch's.

#### <a id="an-audience-that-cannot-be-named"></a>An audience that cannot be named drops the LINE, never the filter

A bark filtered on a class, a faction or an actor that NO converted plugin in
the chain defines has nothing to point a condition at. Skipping the test and
keeping the line would widen it to its race and sex alone, and TES3 topics put
their specific lines FIRST -- as does Skyrim, which takes the first INFO that
passes -- so an Ordinator's `faction=temple` greeting would beat the generic
Dark Elf pool on every Dark Elf male.

`_audience_lines` returns None for such a bark and `dialogue_records` counts it
under `unresolved['bark audience']`.

#### <a id="when-a-bark-fires"></a>When a bark fires: the package has to allow it

**Code:** `tes4_export/record_types/morrowind_packages.py`,
`tes5_import/packages/interrupt_morrowind.py`

A correct bark record is not enough. Skyrim asks the actor's RUNNING PACKAGE
whether it may speak unprompted (PKDT interrupt flags; CK wiki `Package_Flags`:
"Hellos to player: Allow the actor to say hello to the player", "Allow Idle
Chatter"). Every converted package carried `DEFAULT_INTERRUPT` (0x0044, the
combat bits only), which is why Attack/Hit/Flee were audible and Hello/Idle
never were.

OpenMW decides the same thing from authored data (`actors.cpp`
`updateGreetingState` / `playIdleDialogue`), so the flags are derived, not
guessed:

| OpenMW rule | Skyrim interrupt flag |
|---|---|
| greets only with AIDT `Hello` > 0, and only under Wander, Travel or no package | 0x01 Hellos to player on wander/travel packages |
| idle voice needs `Hello` != 0 and no Follow/Escort package | 0x80 Allow Idle Chatter on everything but follow/escort |
| thief / intruder are not package-gated | 0x10 Reaction to player actions on every package |

The export writes the actor's `Hello` onto each of its PACKs as
`MorrowindHello`; a PACK without that field is not Morrowind's and keeps the
default.

### <a id="which-sounds-a-plugin-ships"></a>Which sounds a plugin ships

`Data Files\Sound` is SHARED: Morrowind, its expansions and every installed mod
write into one tree, so presence on disk says nothing about ownership. Measured
on the dev install, 1,810 non-voice files sit there but Morrowind.esm's records
name only 378 of them -- the remaining 1,433 include whole folders (`tr` 456,
`sky` 261, `va` 212, `pc` 76, `ac` 37) belonging to Tamriel Rebuilt and other
mods. Copying the tree wholesale would make every plugin re-ship its neighbours'
audio.

A plugin therefore ships a file when **it names the file and no master of it
does**. `SOUN.FNAM` is the only record-side path -- DOOR, LIGH and ACTI name a
SOUN by ID, which resolves to that SOUN's own file -- so the reference set is
one scan of `SOUN.txt` per plugin, minus the same scan over each converted
master. An unconverted master contributes nothing, which keeps a standalone
plugin self-sufficient.

The `vo` tree is excluded and NOT yet converted: Morrowind has no voice-type
record, and resolves NPC voice purely by path convention
(`Vo\<race>\<sex>\<code><linekind><NN>.mp3`, 7,024 files on the dev install),
so nothing on a record references it and this ownership rule cannot see it.
The `Say` opcode names those paths literally (42 call sites in Morrowind.esm).
Converting it needs its own pass.

### <a id="creature-sound-generators"></a>SNDG: Moan is Aware, not Idle

`SNDG` binds a creature to a sound by type (UESP `Mod File Format/SNDG`):
0 Left Foot, 1 Right Foot, 2 Swim Left, 3 Swim Right, 4 Moan, 5 Roar, 6 Scream,
7 Land. Morrowind.esm holds 168 of them over 43 creatures.

Morrowind gives a creature ONE vocal triad, and the files name it: every
generator resolves to `Cr\<creature>\moan.wav`, `roar.wav`, `scrm.wav`.
Oblivion instead has TWO idle-ish slots, CSDT 4 Idle and 5 Aware, with distinct
folders (`fx\npc\bear\idle\` beside `fx\npc\bear\aware\`). Moan therefore maps
to **Aware (5)**, not Idle (4): slot 4 is deliberately never annotated onto a
clip, because the idle clip LOOPS and an embedded `SoundPlay` fires every cycle
-- the confirmed squeak-spam bug (`hkx_behavior.generate_creature_project`).
Measured before the fix: Oblivion populated slot 5 on 40 of 42 creature folders
while Morrowind populated it on 0 of 43, so all 47 Morrowind creature projects
came out with empty `vocal_events` and zero `SoundPlay` annotations -- every
creature silent.

Swim Left/Right and Land reuse the back-foot and Death slots (2, 3, 8), which
`CSDT_TO_CLIP` already annotates; before this they were dropped outright, which
cost 5 of Morrowind.esm's 168 generators.

### <a id="sound-gen-creature"></a>The sound-gen creature is TES4's inherit-sound source

**Code:** `tes4_export/record_types/morrowind_actors.py` `_emit_sound_slots`,
`asset_convert/havok/creature_sounds.py`

OpenMW (`mwclass/creature.cpp` `getSoundIdFromSndGen`) looks a generator up
under the creature's `CNAM` sound-gen creature BEFORE its own id, and failing
that under any creature sharing its model. The exporter looked only under the
creature's own id, in its own plugin. Measured with OpenMW's rule against the
old export: Morrowind.esm voices 236 of 260 creatures (43 by own id, 176 through
`CNAM`, 17 through a shared model); TR_Mainland voices 714 of 721, **679 of them
through `CNAM`** -- and its export gave 0 of 721 a sound slot.

TES4 has the same indirection natively: `CSCR` names a creature to inherit
sounds from, and 817 of Oblivion's 909 CREA records use it. So `CNAM` is
exported as `CSCR.InheritSound` whenever the generators are not this plugin's
own, and the shared-model rule is already what "the richest slot set in a mesh
folder wins" does.

The creature pipeline's lookup was master-blind: it read only the plugin's own
`CREA.txt` and `SOUN.txt`, so an inherit source or a SOUN owned by a master
resolved to nothing. `creature_sounds` now folds each master's resolved view in
first, re-keyed into the borrower's id space. Old against new lookup on the
real exports: Oblivion.esm 42 folders and Nehrim.esm 59, every entry identical;
Morrowind_ob.esm 60 -> 94 folders (its sounds live in Oblivion.esm), Tamriel
Data 76 -> 173, and the compatibility patch and TR_Mainland 0 -> 94 and 0 -> 173.

Two Morroblivion-mode gaps remained after that, both measured on TR_Mainland's
721 creatures:

* **The patch never read vanilla's SNDG records.** Its 19 gap creatures -- the
  `dremora` many others inherit from among them -- exported with no slot at
  all. `collect_bark_records` now carries `SNDG` and `_patch_context` registers
  them: 14 of the 19 sit in a voiced folder, and TR went 616 -> 653.
* **Morroblivion renames its creatures**, so a sound-gen creature such as
  `skeleton` (88 TR creatures name it) or `ancestor_ghost` (66) resolves to
  nothing by id. `MorroblivionModels.paired_creature` finds the stand-in
  through the MESH the two share, the pairing `MORROBLIVION_CREATURES` already
  records; all 76 EditorIDs in that table resolve in the Morrowind_ob index.
  TR went 653 -> 721 of 721, Tamriel Data 372 -> 469 of 527.

## <a id="tes3-bsa"></a>The TES3 BSA

A different format from every later BSA: magic `0x00000100` (not `BSA\0`), a
flat path list with no folder records, and no compression at all. Parsed from
`Morrowind.bsa`: 11,090 files — 5,798 `.nif`, 5,187 `.dds`, 97 `.kf`.

Vanilla textures are **already DDS**, so TGA/BMP decoding is a loose-mod concern
and not on the critical path.


## <a id="groundcover-as-grass"></a>Groundcover plugins become real grass

**Code:** `tes4_export/morrowind_grass.py`

Morrowind has no grass record. Groundcover mods place one static per clump
because the engine offers nothing else, so the Aesthesia set for Tamriel
Rebuilt ships 2,730,470 placed references across 2,125 cells (median 1,030 per
cell, max 4,097) driving just 65 distinct `Grass\*.nif` statics. Converting
those literally gives Skyrim millions of individually rendered STATs with no
instancing, no grass LOD and no wind.

Skyrim scatters grass procedurally per LANDSCAPE TEXTURE instead: a GRAS record
names the model, and each LTEX names the grasses growing on it. The conversion
inverts the placements into that model:

1. A static whose model path starts `Grass\` is groundcover. That prefix is
   authored by the mod, not inferred.
2. Every placement samples the land texture beneath its X/Y.
3. Counts accumulate per (texture, model), with the authored `XSCL` spread.
4. One GRAS per model, bound through `GNAM` on each texture it grew on.
5. The 2.73M references are dropped.

The result is statistical, not clump-for-clump: the same grasses appear on the
same land textures at the same coarseness, which is the most a procedural
scatter can preserve. Deliberately mowed patterns are the part that is lost,
because Skyrim has no way to express them.

### The terrain belongs to the master

A groundcover plugin ships CELL and STAT records only — the TR grass ESP has
**no LAND and no LTEX at all** — so the texture grid comes from the master's
export dump, not the plugin. Reading only the plugin leaves every sample empty
and silently emits no grass, the master-blindness failure this project keeps
hitting.

The master's LAND is already split into Oblivion quadrants, so sampling reads
`BTXT` (the quadrant's base texture) and `ATXT`/`VTXTCount` (an alpha layer,
which wins only where it covers most of the quadrant's 17x17 vertices). The
bulk `VHGT`/`VNML`/`VCLR` arrays are skipped by line prefix: they are ~97% of
a 352 MB LAND dump and name no texture.

### Rare pairings are sampling noise

A clump sitting just over a texture boundary samples ground it never grew on.
Left alone that binding carpets an unrelated texture across the worldspace, so
a pairing is kept only above both a floor of 8 placements and 2% of that
texture's clumps.

### <a id="groundcover-density"></a>Density is solved per (texture, model) pairing, under the engine's grass cap

**The cap is 3 at the default ini, not 2.** The planter (SkyrimSE.exe
`0x26a2c0`, the only reader of `iMaxGrassTypesPerTexure`) walks the LTEX's
GNAM list with `cmp eax, edi; jg exit` before each entry, so it takes entries
while `taken <= cap`: the shipped default of 2 admits three, which is exactly
why vanilla's four 3-GNAM textures (LReachGrass01, LPineForest02,
LCoastOceanFloor01, LRiverBottom01) work on a stock install. Entries past
that are skipped outright; nothing renormalises the kept densities.
`MAX_GRASSES_PER_TEXTURE` is therefore 3, which keeps 70% of TR's clumps on
their own model before reattribution (2 kept 55%, 7 would keep 89%; the
author places a median 16 distinct models per texture).

**The planter multiplies density by the layer's blend weight, so the area a
texture is solved over is its weight summed across quads, not a quad count.**
Binding every clump to the quad's dominant texture and dividing by the number
of quads it dominates assumed weight 1.0; on TR's 41,164 quads the dominant
texture's mean weight is 0.685 (median 0.665, p10 0.43), because the export
paints alpha ramps at every patch boundary and a quad averages 2.2 alpha
layers, so roughly a third of the intended clumps never planted.
`_quadrant_weights` gives each ALPHA layer its mean opacity and the BASE what
they leave uncovered, `GrassTally.area` sums those per texture, and the grid
is `floor(2048 / PositionRange)` per side as the planter lays it (14 at the
140 clamp, not 14.6).

**A pairing whose density rounds to 0 is dropped, not planted at 1.** The
Density byte cannot express less than 1% of 196 candidates per quad, so the
old `max(1, ...)` floor planted 36 sparse pairings at 2-28x their authored
count (one texture at 28.8x). Those 36 carry 0.49% of all clumps; dropping
them costs that and nothing else. Re-measured with the planter's own formula
(`area x floor(n)^2 x Density/100` summed over a texture's grasses, against
its authored clumps): median 1.00 across 172 textures, range 0.58-1.60 from
integer rounding on the sparsest pairings. 494 GRAS records. NOT yet
in-game verified.

The planter is the one measured in
[grass placement parity](asset_convert_terrain.md#grass-placement-parity): per
LAND quad it lays `n = min(2048/PositionRange, 2048/iMinGrassSize)` candidates
per side and keeps each with probability `Density%`. Skyrim's iMinGrassSize is
20.

Vanilla fixes PositionRange at 29-55 and varies Density over 3-23, because each
of its 27 GRAS records carpets a texture on its own. A groundcover set does not
work that way: 65 models share one cell's ~1,030 clumps, so the median model
places only 85 per cell. Held at a vanilla PositionRange those all round to
Density 0-1 -- the first build emitted Density 1 for all 64 records, which
plants almost nothing.

The second build (one GRAS per model, density from clumps per CELL the model
appears in) was still "extremely sparse" in game, for two measured reasons:

- **The engine plants only the first `iMaxGrassTypesPerTexure` grasses a
  texture names (default 2; vanilla LTEX carry at most 3).** The bindings
  named up to 20 models per texture (median 6), so on the heaviest textures
  the engine planted 25-55 clumps per quad of an authored 190-210.
- **A cell is not the texture's area.** Dividing a model's clumps by the
  cells it appears in undercounts where the texture covers a fraction of
  each cell; the LAND quads whose dominant texture it is are the planter's
  own unit. Across all 180 textures the records implied a median 79 per quad
  (26 after the cap) against an authored 50.

So `bindings()` keeps the two commonest models per texture and hands them the
texture's whole clump count in proportion, and every kept pairing gets ITS
OWN GRAS (356 records for 180 textures; FormID keyed on the static id plus
the LTEX FormID, both authored), so each texture carries the density its
author gave it rather than a mean over the model's textures. PositionRange is
solved per pairing for a mid-vanilla density and clamped to 80-140; Density
then follows the authored count. The floor is `convert_GRAS`'s Oblivion-parity
rule (`PositionRange = max(80)`, Density x 80/PositionRange), which is not
count-preserving: a record solved at 65 came out 20% under authored on the
densest textures, so the exporter never goes below 80 and raises Density
instead (up to 31 at 200 clumps per quad). Re-measured with the harness in
`temp/`-style form (per texture: authored clumps per quad vs
`(2048/PositionRange)^2 * Density/100` summed over its grasses): identical on
all 180 textures, median 50 per quad. NOT yet in-game verified.

The rest of DATA comes from the vanilla census (27 records in
`references/Skyrim.esm/GRAS.txt`, DATA stored as hex): MaxSlope 45 (vanilla
34-50; 90 grows grass up cliff faces), ColorRange 0.2, WavePeriod 120, and
**Flags 6** -- Uniform Scaling + Fit to Slope, which all 27 set (bit meanings
from `wbDefinitionsTES5.pas:7763`). HeightRange is the one field the source
supplies: the authored `XSCL` spread, 0.49-0.99 here.

### The binding is an override, and must look like one

The first build emitted each LTEX with only `GrassCount`/`Grass[n]`. LTEX does
not go through the override-merge path, so the importer wrote those stubs
whole, REPLACING the master's texture with a record carrying no EDID and no
TNAM. The grass never appeared and the terrain lost its texture set. A working
LTEX -- compare a converted Oblivion one, which does render grass -- is
`EDID TNAM MNAM HNAM SNAM GNAM`.

The override therefore carries the master's **EditorID**, and deliberately not
its **ICON**. Repeating the ICON makes `convert_LTEX` mint a second TXST under
THIS plugin's texture namespace (`morrowindob\landscape\morro\...`) pointing
at a path only the master ships as `tes4\...` -- 180 TXST records, all
dangling. The master's own TXST already covers the terrain, so the override
changes the grass list and nothing else, landing as
`EDID MNAM HNAM GNAM`.

Verifying GNAM links resolve is not enough to know grass will render: those
were correct in the broken build too. Check the subrecord ORDER and set
against a working record, and check every MODL and TX00 against the
filesystem.

### The binding must route through the master remap

A converted master's records are keyed by ITS master list, not the borrowing
plugin's. TR_Mainland in Morroblivion mode declares Morrowind_ob, the
Morroblivion patch and Tamriel_Data -- so TR's own records are byte `03` in its
own export, while TR sits at slot `02` in a groundcover plugin's list. Reading
`LTEX.txt` and `LAND.txt` raw keeps the `03` and the importer's +1 shift lands
it on `04`, THIS plugin's own space: the bindings become new records that
override nothing, and no grass appears anywhere.

`load_context` already computes the right translation per master
(`remap = {len(own): slot}` plus each of its masters by name). The grass reads
go through `remap_form_id` with it, exactly like `load_index` and
`load_master_doors`. Correct output has the LTEX at the MASTER's index and only
the new GRAS at the plugin's own.

### Override JUST the grass

The binding adds GNAM to a texture the MASTER defines, so the override must be
the master's record plus that run and nothing else. Two earlier shapes both
failed in-game:

- Emitting only `GrassCount`/`Grass[n]` REPLACED the master's record with a
  stub: no EDID, no TNAM. The landscape shader then read a null texture set and
  crashed in `BSLightingShaderMaterialLandscape` (`mov rcx, [rax+0x48]`,
  rax = 0) on a terrain block.
- Repeating the master's ICON made the splice re-mint a TXST in THIS plugin's
  namespace, pointing at a path only the master ships.

So `master_ltex_fields` repeats the master's record MINUS `_LTEX_OWN_KEYS`
(the header, plus ICON/TextureIndex, whose splice re-mints the texture set) and
minus the master's own `Grass*` keys -- the export reader folds a repeated key
into a LIST, and `_list_as_multiset` then compares str against list and raises.
`('LTEX', 'Grass[]')` is a `_RUN_REBUILDERS` entry, so only the GNAM run is
rewritten. Verified: all 170 TR overrides are byte-identical to the master
except GNAM.

### A lone master still needs its ids restated

`load_master_index` returned a bare `MasterIndex` whenever exactly one master
resolved, skipping `ChainedMasterIndex`'s rewrite. That is only safe when the
master already numbers itself at the slot the child gives it. TR_Mainland's own
records are byte `04` (it has four masters) but it sits at slot `03` here, so
every id inside its bytes named the wrong file. The shortcut now also requires
`own_index == base_slot`.

`_FORMID_FIELDS` was also missing LTEX's own FormID subrecords -- it had
`BTXT`/`ATXT` (a LAND's texture refs) but not `TNAM` -> TXST or `GNAM` -> GRAS
(`wbDefinitionsTES5.pas:8069`). Unshifted, TNAM pointed at index `04`, this
plugin's own empty space: the same null texture set, from the other direction.

### An unmappable override key is dropped, not emitted

`apply_changes` has no mapping for `Grass[]`, so a spliced LTEX came back
byte-identical to the master and hit the "unchanged -- pure bloat" drop: all
180 bindings silently lost, with the log reporting them under `unchanged
(dropped)`. `('LTEX', 'Grass[]')` is therefore in `RECONVERT_KEYS`, which
reconverts the record from the plugin's export while keeping the master's
FormID.

That alone was not enough: `_convert_ltex` is its own phase and, unlike the
generic loop in `pipeline_records`, did not test `ov.status != 'reconvert'` --
a reconvert returns empty bytes, so every record hit `continue` and vanished.
Both call sites now make the same check.

### Cost

Measured on the Tamriel Rebuilt set: 3.3s to parse the 212 MB ESP (838k
refs/sec), 3.4s to build the texture lookup from the master's 352 MB
`LAND.txt`, 1.0s to sample and tally all 2.73M placements. Single-threaded and
well under any stage that would justify a pool.


## <a id="interior-lighting"></a>Interior lighting

Morrowind hand-lights each interior with an `AMBI` subrecord: ambient color,
sunlight color, fog color and a fog-density scalar, all three colors packed
`0x00BBGGRR`. `morrowind_cell.py` parsed `AMBI` into `cell.ambient` from the
start, but nothing read the field and no `XCLL` key was ever emitted, so every
converted interior reached the import with no lighting record at all.

`build_cell_xcll` returns `None` when `XCLL.AmbientR` is absent, and `LTMP` is
written NULL, so those cells inherited nothing either -- they ran entirely on
engine defaults plus whatever placed lights they held. The symptom is flat,
characterless interior lighting across the whole plugin.

`_interior_cell` now emits the ambient, directional and fog colors whenever
the source cell carries `AMBI`. The import side needed no change: it already
packs all 92 XCLL bytes from those keys, and `get_int`/`get_float` default the
components Morrowind does not author (fog near/far, directional rotation) to
0.

### The colors are authored, and varied

Measured on TR_Mainland: **3,390 of 5,871 interior cells (58%) carry `AMBI`**.
Their ambient mean-luminance spreads 0-143 with a peak in the 48-63 bucket
(987 cells), and the colors are plainly deliberate -- `3E495A` blue-grey for a
Dwemer ruin, `1B1111` with `413529` fog for a warm cave, `1B1921`/`2C1F2E` for
a purple-tinted interior. It is per-cell authored color variation, which is
exactly what the flat look was missing.

The remaining 2,481 interiors author no `AMBI` and still fall through to
engine defaults; nothing in the source can fill them.

### Ambient alone is the mechanism

Oblivion's interiors look correct in-game and set almost nothing else: of
1,770 vanilla cells with `XCLL`, **1,643 (93%) have a pure-black directional
color**, and the median fog near and fog far are both 0.0. Ambient carries the
result on its own.

That retires the obvious follow-up. Morrowind's fog density is a scalar rather
than the near/far pair TES4 uses, so mapping it would mean fitting against
Morroblivion's converted cells -- but it is a component vanilla Oblivion
mostly leaves at zero, so it buys nothing until an interior is shown to need
it. The six-way directional-ambient block stays a replicated flat ambient for
the same reason: Oblivion proves that reads fine.

### <a id="quasi-exterior-interiors"></a>A quasi-exterior interior keeps its sky

`_interior_cell` hardcoded `DATA.Flags=1`, discarding every other authored bit.
Two of them matter, and all three games agree on their meaning -- TES3
`QuasiEx = 0x80` (`components/esm3/loadcell.hpp`, "Behave like exterior
(Tribunal+), with skybox and weather") is bit 7 in TES4's "Behave Like
Exterior" and TES5's "Show Sky"; bit 1 is "Has Water" in all three.

Arktwend's opening cell `Melee Monastery` authors `DATA 830000...` -- interior
+ has water + quasi-exterior -- and carries neither `AMBI` nor `WHGT`, because
a quasi exterior takes its light, sky and water from the world rather than
from per-cell fields. Dropping bit 7 left Skyrim rendering the default black
interior void through the cell's windows, and dropping bit 1 left it dry.

Vanilla Skyrim does exactly this at scale: **229 of 590 interior cells set
Show Sky**, 73 of them with Has Water, normally alongside an `XCCM` and an
ordinary `XCLL`. "Use Sky Lighting" (bit 8) is set on **1** cell in the whole
master, so it is deliberately not synthesized. The flags pass through the
import unchanged -- `convert_CELL` only strips TES4 bits 3 and 6 -- so
carrying them in the export is the whole fix.

`XCCM` names the region a show-sky interior draws its sky and weather from,
which is the same `RGNN` the cell already authored, and it is emitted only for
quasi exteriors.

A Has Water cell with no `WHGT` gets `XCLW.WaterHeight=0.0`. That is what
Morrowind itself reads — `mWater` is initialized to 0 and only overwritten by
an authored `WHGT`/`INTV` (`components/esm3/loadcell.cpp`) — and sea level is
0 in both games, since the conversion applies no Z offset.

Of Arktwend's 8 quasi exteriors, **7 author no height at all**. Level 0 is
still right for them: Melee Monastery places 46 of its 718 references below
Z=0 with the bulk just above, which is a shoreline sitting at sea level.

The height alone is not sufficient — the cell also needs an `XCLL`, which
these cells have no `AMBI` to supply
([why](tes5_import_world.md#every-interior-gets-an-xcll)).

## <a id="region-weather"></a>Region weather

**Code:** `tes4_export/morrowind_region.py`

Morrowind authors no weather records. The engine hardcodes ten weather types
and a region's `WEAT` subrecord is one chance byte per type, in the order
OpenMW's `WeatherManager` registers them
(`references/openmw/apps/openmw/mwworld/weather.cpp`): Clear, Cloudy, Foggy,
Overcast, Rain, Thunderstorm, Ashstorm, Blight, Snow, Blizzard. The colors
behind each live in `Morrowind.ini`, not the plugin.

So in Morroblivion mode there is nothing to mint: Morroblivion already
converted all ten as real WTHR (`mwClear` ... `mwBlizzard`, plus
`mwWeatherAshstorm*`), and a region converts by naming them through the
EditorID index. `WEATHER_EDITOR_IDS` is that whole mapping. This is why the
region's NAME never has to match a Morroblivion region: the region is
self-describing, and only the ten weather slots are shared vocabulary.

Measured sources: `Morrowind.esm` 9 regions (8-byte `WEAT`, Bloodmoon widened
it to 10), `TR_Mainland.esm` 1, `Tamriel_Data.esm` 80.

**Slots 6 and 7 are NOT named after the weather.** Morroblivion calls them
`mwAsh` and `mwWeatherAshstormBlight`, not `mwAshstorm`/`mwAshstormBlight`,
and guessing the names silently drops ash and blight. The identity is pinned
by real data, not the naming: Morrowind's `RedMountainRegion` authors
`[0,0,0,0,0,0,0,100]` -- 100% at slot 7 -- and Morroblivion's own
`RedMountainRegion` gives that entry `01F8B26B`
(`mwWeatherAshstormBlight`).

**A weather region needs a polygon.** All 53 of Skyrim.esm's weather-bearing
REGN records carry `RPLD`, so `convert_REGN` rightly returns None without one
-- and Morrowind, which assigns a region per CELL, authors none. The polygon
is therefore synthesized as the bounding rectangle of the grid squares the
region's own cells occupy. It is approximate on purpose: region weather
reaches the sky through the CELL's `XCLR` list, exactly as it does in Skyrim,
so the rectangle only has to make the record legal.

`Override=1 Priority=95` matches vanilla Skyrim's own weather regions.

## <a id="magic"></a>Magic: an effect is an index, not a code

TES4 gives every magic effect a four-character code and its own record, so the
import side keys its archetype table on `FIDG`, `SHLD` and the rest. TES3 has
no such vocabulary: magic effects are an engine-fixed table of **143**, and a
spell names one by its integer index. A plugin may override an effect's cost,
flags or art, but it can never invent one.

So the index is the authored datum. Every exported effect carries it as
`Effect[i].MorrowindIndex`, beside a synthesized EditorID (`MW014FireDamage`)
naming the MGEF record it resolves to. Keying on the index rather than minting
four-character codes leaves the Oblivion tables untouched and keeps FormIDs
derived from authored data.

Measured across Morrowind, Tribunal and Bloodmoon: 990 SPEL, 708 ENCH, 137
authored MGEF records, and **3,479 effect uses over 139 distinct effects**.

### The name table is generated, and only LENGTH is enforced

`tools/generators/gen_morrowind_mgef_table.py` reads the two index-ordered
arrays OpenMW carries in `components/esm3/loadmgef.cpp` and writes
`tes4_export/morrowind_mgef_names.py`.

It requires both arrays to span `MagicEffect::Length`, and deliberately does
**not** require them to agree on spelling: 13 entries differ by upstream style
alone -- index 19 `DrainSpellpoints` vs `DrainMagicka`, 132 `Corpus` vs
`Corprus`, 138-140 the Bloodmoon summons under generic vs real names. A length
change would renumber every converted spell and must fail loudly; a spelling
difference is cosmetic and must not.

### Magnitude is the mean of the authored range

A TES3 effect authors `magnMin` and `magnMax`; a TES5 EFIT holds one number.
The export writes the mean, because taking the maximum would overstate every
spell in the game.

### An enchantment is a separate subrecord

The int inside `AODT`/`CTDT`/`BKDT` that reads like an enchantment is the
enchant-point **capacity**. The enchantment itself is a separate `ENAM` string
subrecord naming an ENCH record, and until it was followed, **836 enchanted
items** (83 ARMO, 309 CLOT, 315 WEAP, 129 BOOK) lost their magic on conversion.

### Soul gems are identified by ID prefix

TES3 has no soul-gem flag and no SLGM record type: a soul gem is a MISC whose
ID starts `misc_soulgem`, which is the same test OpenMW makes
(`mwclass/misc.cpp`, `isSoulGem`). `tes4_signature` routes those to SLGM and
derives the TES5 capacity from the tier suffix -- petty 1 through grand 5, with
Azura's Star, whose suffix names no tier, holding a grand soul.

Vanilla Morrowind has six: petty, lesser, common, greater, grand and Azura.
They ship empty (`SOUL=0`), because TES3 stores a captured soul on the
inventory stack rather than on the base record.

### <a id="filled-soul-gems"></a>The filled variants are SYNTHESIZED

The two games disagree about where a trapped soul lives. TES3 keeps it on the
inventory stack, so one `misc_soulgem_grand` record serves empty and full
alike. **Skyrim makes a filled gem a separate base record** whose `SOUL` is
the trapped size — `SoulGemGrand` has `SOUL=0` and `SoulGemGrandFilled` has
`SOUL=5`, same `SLCP=5`, differing only in `SOUL` and value.

So a filled variant is synthesized per gem per soul size it can hold:
`<gem id>_Filled<n>`, capacity 1..N. Six vanilla gems yield 20 records
(1+2+3+4+5+5). Without them `AddSoulGem` has nothing to add — the command
names a creature, and the gem it must produce is the one holding **that
creature's** soul.

The soul size comes from the creature's own `DATA.Soul`, which the CREA
exporter already emits in Skyrim's 1..5 enum, so no new mapping is needed.

🛑 The key is the gem's **authored id**, never its capacity or an ordinal, so
a derived FormID never moves ([the save-game contract](performance.md#formid-determinism--the-save-game-contract-rewritten-2026-08-17)).

## <a id="surface-materials"></a>Surface materials

**Code:** `tes4_export/record_types/morrowind_materials.py`

Morrowind records no material anywhere, so footsteps and impacts had nothing
to read and every Morrowind surface converted as stone.

This was confirmed against OpenMW rather than assumed:

- TES3 LTEX accepts only `NAME`, `INTV`, `DATA` and its loader's `default:`
  case is `esm.fail("Unknown subrecord")`
  (`references/openmw/components/esm3/loadltex.cpp:36`) — a material field
  could not exist without breaking vanilla loading.
- LAND carries normals, heights, world-map colours, vertex colours and texture
  indices; the "material palette" comment at `loadland.hpp:48` means the LTEX
  index table.
- `bulletnifloader.cpp` assigns no material to any shape, and `grep -i
  material` over `apps/openmw/mwphysics/` returns nothing.
- Both NIF candidates are version-gated above Morrowind: `mMaterialHash` needs
  >= 10.0.1.0 **and** Bethesda-version > FO3 (`nif/data.cpp:116`), and
  `NiGeometry::MaterialData` early-returns below 10.0.1.0 (`nif/node.cpp:236`).
  `NiMaterialProperty` holds only ambient/diffuse/specular/emissive/gloss/alpha
  (`nif/property.cpp:505`).
- Morrowind's own footsteps come from swim state plus the armor SKILL of the
  equipped boots — 12 sound ids, ground never consulted
  (`apps/openmw/mwclass/npc.cpp:71,1161`).

So there is **no faithful port available**; a material assignment here is new
authored intent, not a reproduction of engine behaviour.

### The signal is the texture name

The one authored, semantic, per-surface string is the texture name, in
Bethesda's `tx_<material>_<detail>` scheme. `NiMaterialProperty.name` was
measured and rejected: over a 60-file sample its values are 3ds-Max defaults
and typos (`Material #1` x22, `core` x8, `bttom`, `k./kl'kjl'`).

Measured coverage of `material_of`:

| Corpus | LTEX | resolved |
|---|---|---|
| Morrowind.esm | 107 | 107 = 100% |
| Tamriel Rebuilt | 325 | 300 = 92.3% |
| Tamriel Data (HD) | 358 | 321 = 89.7% |

Vanilla's 107 land textures resolve to 33 Stone / 29 Grass / 18 Dirt /
13 BrokenStone / 7 Sand / 6 Mud / 1 Gravel — close to vanilla Skyrim's own
terrain distribution. The residual misses are dungeon FLOOR textures
(`T_Imp_DngRuinCyr_TxFloor_01`), for which the Stone fallback is right anyway.

**Match anywhere in the name, never by position.** Vanilla puts the material
second (`tx_wood_siding`), but Tamriel Rebuilt prepends a province
(`tx_skyrim_wood_brown_03`, the single most common prefix at 361/1871 refs);
a positional read scores those as material "skyrim" and returns nothing.

LTEX classifies from its EditorID first — the authored `NAME` field, e.g.
`Sand`, `Road Dirt`, `AI_Grass_Cobbles` — with the texture file name as
tiebreak for the records whose EditorID is itself a filename.
