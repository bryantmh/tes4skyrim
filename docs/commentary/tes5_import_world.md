# tes5_import/record_types/world.py — CELL, WRLD and placed references

**Code:** `tes5_import/record_types/world.py`

CELL, WRLD, REFR and ACHR are the placement records: where a thing stands, what
owns it, and what it is linked to. Region decoration (REGN, LSCR, WATR) lives in
[landscape](tes5_import_landscape.md).

## Contents

- [XLKR — the enable parent becomes the linked ref](#xlkr-enable-parent-becomes-linked-ref)
- [ACHR VMAD — scripts relocated onto the placed reference](#achr-vmad-relocated-actor-scripts)
- [Exclusive LCEC cell ownership](#exclusive-lcec-cell-ownership)
- [Teleport doors bucketed by worldspace](#teleport-doors-by-worldspace)
- [Nested interiors inherit a location](#nested-interiors-inherit-location)
- [LAND DATA flags pass through VERBATIM](#land-data-flags-verbatim)
- [WRLD land and water defaults](#wrld-land-and-water-defaults)

## <a id="xlkr-enable-parent-becomes-linked-ref"></a>XLKR — the enable parent becomes the linked ref

TES4 has no Linked Reference field. Its `GetParentRef` returns the **enable
parent** instead — xEdit names XESP "Enable Parent", and the UESP modding guide
states the idiom directly: "make the container its Parent Ref", then
`set rCont to GetParentRef`. Skyrim exposes no getter for the enable parent, so
`script_convert` maps `GetParentRef` → `GetLinkedRef()`, which reads XLKR.

Nothing wrote XLKR, so every converted `GetParentRef` resolved to None. That is
why the Vilverin pressure plate did nothing when stepped on: its body ran, but
`target = GetLinkedRef()` was None, so `target.Activate()` never reached the mace
and the trap hung in the air. Mirroring the enable parent into XLKR restores the
link the script expects.

**Layout** (xEdit plus a real Skyrim.esm dump, 11287 vanilla uses): 8 bytes,
`{Keyword/Ref, Ref}` with the keyword slot NULL for a plain link — vanilla writes
`00000000` there in the general case.

It is emitted **only** when the base record's script actually calls
`GetParentRef`. XESP is ordinary enable-parenting on 9157 Oblivion refs, and
turning all of those into linked refs would invent links the game never had.

## <a id="achr-vmad-relocated-actor-scripts"></a>ACHR VMAD — scripts relocated onto the placed reference

A converted actor script is relocated onto the placed reference so a
`GetVMScriptVariable` package condition can pass and the quest package can win.
That condition reads the property off the **ref named in its param1**, not off
the base actor, so a script left on the base record is invisible to it. The
relocation itself is `object_scripts.relocate_actor_scripts_to_refs`.

Skyrim's ACHR subrecord order is `EDID VMAD NAME ...`, so the VMAD is emitted
immediately after EDID and before NAME.

## <a id="exclusive-lcec-cell-ownership"></a>Exclusive LCEC cell ownership

**A location's LCEC cell list must be EXCLUSIVE.** A cell carries a single
XLCN, so when two locations both list it the CK reports the losers with
"Exterior cell (x, y) in world 'W' is no longer tagged to this location".
Vanilla never overlaps: all **948** LCEC cells in `Skyrim.esm` are claimed by
exactly one location. Ours overlapped on **873** squares (**762** claimed
twice, **106** three times, **5** four times) because every marker takes a 3x3
block and neighbouring markers collide — the count matched the warning exactly.

`_resolve_cell_ownership` resolves this before any LCTN is written, in two
passes over markers sorted by FormID:

1. **Own square first.** The marker standing IN a square always beats one that
   merely spills into it.
2. **Then the ring.** The surrounding 3x3 fills only squares still unowned.

Ties break by marker FormID so the output stays byte-reproducible; the marker's
LCEC and the cell's XLCN are written from the same resolved set, which is what
keeps them pointing at each other.

## <a id="teleport-doors-by-worldspace"></a>Teleport doors bucketed by worldspace

`_bucket_teleport_doors` returns `(doors_by_world, cell_of_door)`. XTEL names
the *destination door*, and that door's parent cell is the interior being
entered, so `cell_of_door` is built over every REFR, not only the teleporting
ones.

**Only INTERIOR cells may be claimed by a location through a door.** A teleport
door can just as well lead OUT to an exterior (city gate → Tamriel, Oblivion
gate exit → the wilds), and exterior destinations are poison: XTEL destination
doors are persistent, and a worldspace stores every persistent ref in one dummy
cell (Tamriel's `00023777`), so a single exterior entry hands its location to
EVERY persistent ref in that worldspace. That was the CK's "Ref is not in its
persistence location 'TES4SkingradWestGateLocation'" spam across the whole map.

## <a id="nested-interiors-inherit-location"></a>Nested interiors inherit a location

A basement, upper floor, or back room reached ONLY from another interior
inherits the location of the interior it opens off. A quest target in Arvena's
basement needs a marker just as much as one in her front room, and vanilla
gives the whole building one location.

`_propagate_nested_interiors` builds the interior→interior door graph (only
doors that themselves live inside an interior — a door with a ParentWRLD is an
exterior entrance and is handled by the door fallback) and propagates to a
fixed point. Iteration is over `sorted(interior_links)` and each
`sorted(interior_links[src])`, and the first writer wins, so the marker links
established earlier are never overwritten and the output stays
byte-reproducible.

## <a id="land-data-flags-verbatim"></a>LAND DATA flags pass through VERBATIM

🛑 **Verified vanilla-legal — do NOT "normalize" these.** Bit 0 (`0x01`) is
"Has Vertex Normals/Height Map" and bit 4 (`0x10`) is "Auto-Calc Normals".

A LAND with no VNML/VHGT is the author DELETING that cell's terrain — the
"water only, no landscape" case — and it is legal with or without the
auto-calc bit. Skyrim.esm's records that CLEAR bit 0: **149 at flags 28, 3 at
flags 30** (these carry VCLR), **and 2 at flags 12** — which is exactly the
value `TWMP_ValenwoodImproved` uses.

Rewriting the flags-12 pair to 28 looked like a fix only because a PARTIAL
census missed it. `convert_LAND` writes `DATA.Flags` through unchanged.

## <a id="wrld-land-and-water-defaults"></a>WRLD land and water defaults

`DNAM` (Default Land Height, Default Water Height) and `NAM4` (LOD Water
Height) are the planes the engine draws for a cell that has **no LAND record
of its own**. Only FO3/FNV author them; TES4 WRLD has neither field —
measured over `export/Oblivion.esm/WRLD.txt`: **0 `DNAM` lines, 0 `NAM4`
lines** in 84 worldspaces.

Reading them unconditionally therefore gave every TES4 worldspace
`land=-2048, water=0`, because only the land read carried an explicit
fallback. That inverts the two planes: water ended up **2048 units above**
the fallback land, so every LAND-less cell rendered as open sea. It showed up
first in the small Imperial City worldspaces, where most of the grid has no
LAND — `ICImperialPalace` ships 47 LAND records over a footprint spanning
(-2,-2)..(8,17) — while Tamriel hid it by having real LAND nearly everywhere.

Vanilla always puts water at or below land: Skyrim's worldspaces run
`DefaultLandHeight` -27000..9399 against `DefaultWaterHeight`
-500000..9999999, and **no vanilla worldspace floats water above its land
plane** the way the converted output did.

`_world_water_and_planes()` writes both planes with
`_TES4_DEFAULT_PLANE_HEIGHT` (-2048) as the fallback for EACH, so an absent
field can never leave water above land, and writes this run together with the
water types.
**NAM2** honours the authored TES4 pointer — 14 of Oblivion.esm's 84
worldspaces point it at lava, and hardcoding `0x18` made every Oblivion realm
render as ordinary blue water regardless of its WATR. Worldspaces with no
authored water fall back to Skyrim.esm's DefaultWater (`0x18`, master index
0), as vanilla Tamriel does. **NAM3** stays on DefaultWater in all cases: it
is the water drawn on distant terrain LOD, which vanilla always renders as
ordinary water, and a null NAM3 makes the terrain-LOD water codepath deref a
null WATR pointer and CTD as soon as a `.btr` contains a WATER
`BSMultiBoundNode`.


## <a id="every-cell-gets-an-xcll"></a>Every CELL gets an XCLL

**Code:** `build_cell_xcll` in `tes5_import/record_types/world.py`

`build_cell_xcll` used to return `None` when the source authored no ambient
color, so the CELL shipped with no lighting block at all. **All 590 of
Skyrim.esm's interior cells carry an XCLL** — there is no vanilla precedent
for omitting it, and a converted cell without one does not render like a
vanilla cell.

That bites hardest on a Morrowind quasi exterior
([Show Sky](tes4_export_morrowind.md#quasi-exterior-interiors)): those cells
carry no `AMBI`, so they reached the engine with no XCLL, and their water did
not appear even with Has Water and a valid height set.

The fallback is a real vanilla block rather than zeros — the most common XCLL
among Skyrim's own Show Sky interiors, shared verbatim by 61 of them: ambient
`1E1E28`, black directional and fog colors, fog near/far 0, fog power, scale
and fog max 1.0, the six directional-ambient colors it ships with, and
inherit flags `0x9F`. An authored source still overrides every field it
states; the block only fills in what TES3/TES4 never authored.
