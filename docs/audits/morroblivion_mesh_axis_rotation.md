# Morroblivion's hand-authored placement fixes (2026-09-17)

The original Morroblivion converter shipped meshes on the wrong axis, re-seated
others, and in places repointed a record at a different object outright. It then
corrected every reference IT placed, by hand. Any plugin that places those bases
without the same corrections renders the objects on their side, or floating.

Reported symptom: TR ref `0383CB89` (cell `0382E6C5`) on Morroblivion base
`012C0134` (`0lightUcomUcandleU10U128`, Silver Candlestick,
`Morroblivion\Lights\Common\candle_10.nif`) lies flat.

## <a id="measure-the-delta"></a>Measure the DELTA, never the absolute value

The only correct measurement is **Morroblivion's value minus Morrowind's, per
reference**. An absolute reading cannot tell a Morroblivion repair from
rotation Morrowind itself authored, so it needs a veto — and the veto is both
redundant and actively harmful:

- Redundant, because an object tilted the same in both **cancels to 0** and
  never becomes a candidate. `doorudwrvuloadup00` reads delta 0 on 11 of 11
  references; `dungeons/tikitorch.nif` on 1,310 of 1,311.
- Harmful, because it **hid real fixes**. `inulavaurocku17/18` are rotated a
  further 180° by Morroblivion; an earlier veto discarded them as "authored
  TES3 data" on a `mwTilt` statistic that counted RotY as well as RotX. The
  lava rock is in fact **0° in Morrowind on 69 of 69 references and 180° in
  Morroblivion on 67 of 69** — purely Morroblivion-introduced. That earlier
  claim, that they were 91–100% tilted in Morrowind, was wrong.

References are paired by cell, then by nearest neighbour **horizontally**.
Pairing on full 3D distance is what an earlier pass did, and it silently
discarded 94% of the references for exactly the meshes Morroblivion re-seated
vertically: the object had moved further in Z than the cutoff allowed, so it
never matched itself, and the surviving sample was biased toward things that
had NOT been fixed.

Cells pair on **grid coordinates for exteriors and `FULL` for interiors**.
`ParentWRLD` cannot join the two exports — the same worldspace carries a
different FormID in each — and Morroblivion's `EditorID` is mangled
(`TelSMora`) where its `FULL` is not (`Tel Mora`). Getting this wrong is not a
small error: an earlier key matched **zero** cells and the census reported no
position fixes at all, which read as a clean negative result rather than the
broken query it was.

With the pairing fixed, **247,156 references** pair across the two exports.

## The mechanism

Geometry of the affected meshes (vertex bounds via `sse_nif.read_nif`):

| Mesh | sizeX | sizeY | sizeZ | Longest |
|---|---|---|---|---|
| `common/candle_10.nif` | 18.5 | 37.3 | 18.6 | **Y** |
| `common/candle_01.nif` | 16.7 | 27.7 | 17.1 | **Y** |
| `common/lantern_02.nif` | 14.0 | 38.7 | 14.0 | **Y** (negative) |
| `lights/torchnohavok.nif` | 8.2 | 47.8 | 8.5 | **Y** |

X and Z are symmetric about zero; the object's height lies on Y, and a 270°
pitch stands it up. But geometry is only the mechanism, never the test —
`candle_13`, `candle_blue_01/02`, `candle_green_01`, `candle_ivory_01`,
`candle_red_01` and `candle_02` all measure longest in **Z** and are still
corrected, because their references carry a unanimous 270° delta. Conversely
`candle_06` (1,148 refs) and `daedric/brazier00` (363) are Y-longest and
correctly placed: they are braziers and squat clusters, wider than tall.
Classifying on the axis alone would mis-flag roughly 2,500 references.

The base case, `012C0134` ↔ Morrowind `00E94F2F`:

| Plugin | Refs | RotX |
|---|---|---|
| Morroblivion | 8 | 270° (8/8) |
| Tamriel Rebuilt | 36 | 0° (36/36) |

## <a id="the-correction"></a>The correction, as shipped

**Code:** `tes4_export/morroblivion_axis.py`, applied by
`remap_vanilla_models` / `_fix_placements` in `tes4_export/morroblivion.py`.

It lives in the **Morrowind export**, not the import, so the importer needs no
Morroblivion knowledge on the ~1.2M-ref hot path every plugin and game shares.
Morroblivion's own export is unaffected by construction rather than by a name
check: its records already name `Morroblivion\...` paths, so nothing
substitutes and no reference is corrected.

`_fix_placements` resolves each REFR/ACHR/ACRE's `NAME` through the MASTERS'
indexes on `MorroblivionModels` — `models` (FormID low 24 bits → model path)
for the pitch, `editor_ids` (→ EditorID) for the Z re-seat. Resolving through
the masters is load-bearing: the corrected bases are Morroblivion's own
records, so their `MODL` lines never appear in a dependent plugin's output. An
earlier version registered from the `remap_vanilla_models` loop over `out` and
caught **15 of 2,997** references for that reason — the master-export blindness
CLAUDE.md warns about.

Entries are accepted on a **spike**: one value carrying ≥80% of a base's
references. A median is not enough — `clay_urn05` showed a median dZ of −9.48
that proved to be two populations (11 refs at exactly 0.0, ~15 near −12.3), so
no single offset is right and it is excluded.

### The tables

- `AXIS_PITCH_DEG` — 33 meshes. Pitch delta, keyed on the mesh.
- `Z_RESEAT` — 15 bases. Keyed on the base EditorID, not the mesh, because the
  fixes were hand-made: Morroblivion moved `0torchU256` by −5.0 (129 of 131
  refs) while leaving `0lightUcomUtorchU01` at 0.0, though both use
  `torchnohavok.nif`.
- `MESH_SWAPS` — 21 bases, 678 refs. **Measured but deliberately not applied.**
- `AXIS_PITCH_UNSURE` — 9 meshes whose delta splits, each with its measurement.

### <a id="mesh-swaps"></a>MESH_SWAPS: the wrong object, not the wrong place

Some records were repointed at a genuinely different object and their
references dragged to suit. `light_de_lamp_01` — a **hanging** Dunmer lamp —
became `Lights\MiddleCandlestickFloor02.NIF`, a vanilla Oblivion **floor**
candlestick, with every reference pulled down ~127 units.

Applying that offset would only relocate the error: it puts a floor candlestick
where the author placed a ceiling lamp. The object is wrong, not its height,
and the fix is to restore the Morrowind mesh. These are recorded for a separate
pass and never applied as positions.

A swap is identified by the replacement being absent from Morroblivion's own
mesh tree (so it is stock Oblivion), or by the offset exceeding the replacement
mesh's entire height. Affected: `light_de_lamp_01/03/05`,
`light_de_candle_01/07/09`, and every `Light_Fire*` → vanilla `Fire\`.

### Held

`torchnohavok.nif` — 123 paired refs, delta splits 270:82 / 90:36. The axis is
wrong *and* the author picked a direction per reference (torches mount up or
down), so no single value expresses it. `dunmer/lantern_06s.nif` is diagonal
(lantern plus chain) and splits the same way.

Not every light is affected: of 114 meshes under `Morroblivion\Lights\`, 68
are built correctly on Z — `tikitorch` (1,602 refs), `sconce10` (913),
`sconce00a` (677), the whole `6thhouse/` and `ashlander/` families. The defect
is a subset, never the folder, and `common/candle_10.nif` is broken while
`dunmer/candle_10.nif` is not, so the table keys the full mesh path and nothing
is classified by filename.

## Verified against the rebuilt export

| | corrected meshes | control (`tikitorch`) | UNSURE (held) |
|---|---|---|---|
| Morroblivion | 2,012 refs | 1,602 refs, 99.6% upright | 921 refs |
| Tamriel Rebuilt | 7,169 refs, **1.8%** upright | 3,139 refs, 94.8% upright | 2,096 refs |

TR was 96% upright on those meshes beforehand. The control and the held meshes
are untouched, so the correction is scoped. In the built `TR_Mainland.esm`, all
36 references to base `012C0134` read RotX=270°.

**Not yet verified in-game.**
