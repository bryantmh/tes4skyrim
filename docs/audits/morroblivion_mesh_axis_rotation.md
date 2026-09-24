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

## <a id="pairing-the-bases"></a>Pairing the bases

Morroblivion mangles the Morrowind EditorID: it prefixes `0` and writes `_` as
`U`, so `misc_com_bucket_metal` becomes `0MiscUComUBucketUMetal`. Reducing both
sides by dropping `_`, `u` and any leading `0` joins them.

The reduction also folds the `u` INSIDE a word (`bucket` → `bcket`), which is
harmless while it runs identically on both sides but does collide distinct
EditorIDs. A collision must be **counted and reported, never resolved by
overwriting**: a base that loses the race is absent from the census entirely,
which reads as "needs no correction" rather than "never measured". The first
census overwrote silently and had no way to show it.

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

## <a id="regenerating"></a>Regenerating the tables

**Tool:** `tools/audit/morroblivion_placement_audit.py`.
**Results:** [morroblivion_placement_results.txt](morroblivion_placement_results.txt),
rewritten in place by every run. The first census was a
pile of scratchpad scripts that were thrown away, so the tables could not be
re-measured and a newly-reported bad object had to be chased by hand. The tool
replaces them and fixes what they got wrong:

- All **three** rotation axes. The old pass read RotX only and therefore saw a
  third of the corrections — `inulavaurocku14/16/17/18` and `inumoldurocku17`
  each carry a RotY and RotZ delta as well.
- The spike search runs over the **real histogram**, not a fixed 90/180/270
  candidate list.
- Candidates that miss the threshold print under **HELD with their
  histograms**; the old pass dropped them, so "no row" was indistinguishable
  from "no correction needed".
- EditorID pairing **collisions are counted**. The old `norm_edid` overwrote on
  collision, silently removing a base from the census entirely.
- REFR, ACHR and ACRE, not REFR alone.

Re-measured 2026-09-20: 5,976 bases and 138,520 references pair, giving 48
rotation corrections (was 33), 126 Z re-seats (was 15) and 232 held candidates.

### <a id="a-swap-is-a-missing-mesh"></a>A swap is found by the mesh tree, not the path prefix

A record Morroblivion repointed at a stock Oblivion mesh is identified by the
replacement being **absent from Morroblivion's own extracted `meshes/` tree**.
Testing the `morroblivion\` prefix alone is wrong: `morro/`, `mip/` and
`clutter/` are Morroblivion's own prefixes too, and treating them as foreign
reports 3,188 swaps instead of the real **22 meshes over 62 bases**.

The fix is to **skip the substitution**, not to apply the offset: leave the
Morrowind mesh in place and let the compatibility patch convert it. Applying
the measured dZ would only move the wrong object.

### <a id="base-objects-not-meshes"></a>The substitution is a BASE OBJECT, not a mesh

**Code:** `blacklisted_bases` / `collect_gap_records` in
`tes4_export/morrowind_patch.py`.

A blacklist in `remap_vanilla_models` only reaches a converted mod's OWN
records — the ones naming a vanilla mesh path. It cannot reach a placement of
a Morroblivion base: `0lightUdeUlampU03U64` is a record in `Morrowind_ob.esm`
already carrying `Lights\MiddleCandlestickFloor02.NIF`, and a converted mod
merely references it. There is no model line of ours to rewrite, and
Morroblivion itself is fixed input that is never re-exported.

So the fix is not to replace the MESH but to stop borrowing the BASE OBJECT.
`collect_gap_records` asks whether Morroblivion supplies an object; a base
wearing a blacklisted mesh now answers NO, so the compatibility patch builds
the real Morrowind record and every converted Morrowind mod references that
instead. This is the same exclusion `_paired_creature` already applies.

Measured: **328** Morroblivion bases wear a blacklisted mesh (158 LIGH,
145 CONT, 7 FURN, 7 STAT, 6 MISC, 3 ACTI, 2 INGR); **324** map back to a
vanilla record the patch can build. The remaining 4 have no Morrowind
counterpart and keep Morroblivion's object.

### <a id="geometry-beats-the-threshold"></a>Geometry is evidence; the spike is a proxy

`candle_15` shipped upright-less because its reference agreement measured
**35/44 = 0.7955**, just under `MIN_SHARE = 0.80`, while `candle_17` at
**32/40 = 0.8000** passed. The report prints both as "80%", so the two look
identical and the half-percent that separated them is invisible.

The geometry is not ambiguous at all: Morrowind `(6.4, 6.4, 29.9)` against
Morroblivion `(6.4, 25.8, 6.4)` — sorted ratio 1.00/1.00/0.86, the long axis
moved from Z to Y. The mesh is plainly mis-built.

So a threshold on how many references agree must never overrule a direct
measurement of the mesh. `candle_15` and `lantern_06s` (ratio 1.76 on one
axis) are blacklisted on their geometry; `furnucolonyuhook01` stays held
because it has no Morrowind counterpart to measure against.

### <a id="why-blacklist-wins"></a>Why a swap beats a correction

**The blacklisted mesh is the only option we can be certain about.** The
Morrowind mesh at its authored placement is the original object at the original
coordinates — both known-good. A pitch or a dZ is a value fitted to a
histogram, and none of them is in-game verified. So where a mesh is both
"rebuilt" and "carries a measured rotation", the mesh wins: shipping the real
object beats rotating a rebuilt one.

That rule removed 20 entries from `AXIS_PITCH_DEG` and 6 from `Z_RESEAT`, which
now describe only meshes that still reach the output.
`test_no_correction_targets_a_blacklisted_mesh` keeps them disjoint.

### <a id="permutation-test"></a>Compare SORTED extents, never raw Z

A mesh authored on the wrong axis has the SAME extents, permuted: a 55-unit
lantern lying down is 55 units on Y instead of Z. Comparing raw Z calls that a
size change and blacklists a mesh a rotation would have fixed — it reported
`candle_17` as x0.21 when the sorted ratio is 1.00/1.00/0.86.

Sorting both meshes' extents before comparing separates the two cases:

| Mesh | Sorted ratio | Reading |
|---|---|---|
| `morro/i/inulavaurocku14` | 1.00/1.00/1.00 | identical — the 180° pitch is real |
| `dunmer/candle_16`, `candle_17` | 1.00/1.00/0.86 | same object, wrong axis — pitch fits |
| `common/candle_10` | 1.00/1.00/**0.81** | rebuilt shorter — blacklist |
| `common/lantern_01/02` | 1.00/0.86/**0.70** | rebuilt shorter — blacklist |
| `dunmer/lamp_05` | 0.95/0.95/**0.63** | chain gone — blacklist |
| `dunmer/candle_blue_01` | 1.03/**4.29**/1.09 | different object — blacklist |

Of the 33 meshes the first census pitched, **20 are rebuilt objects** rather
than mis-authored axes, including `common/candle_10` — the Silver Candlestick
this audit was originally written about.

### <a id="substitution-blacklist"></a>Which swaps are blacklisted

`SUBSTITUTION_BLACKLIST` in `tes4_export/morroblivion_axis.py`. Judged on
measured vertex bounds of both meshes, never on the filename: a replacement
stays only when it is the same kind of object at roughly the same size.

Blacklisted — 15 meshes, 54 references:

| Oblivion mesh | Morrowind original | OB size | MW size | Why |
|---|---|---|---|---|
| `lights/middlecandlestickfloor02.nif` | `light_de_lamp_03` | 25×21×109 | 55×55×188 | floor stand for a HANGING lamp |
| `lights/candlefat02.nif` | `light_de_candle_11` | 8×8×10 | 15×11×21 | half size |
| `lights/candleskinny01.nif` | `light_de_candle_09` | 2×2×15 | 18×18×15 | taper for a wide candelabra |
| `lights/torch01fake.nif` | `light_com_torch_01` | 5×5×28 | 10×9×49 | unlit prop, half height |
| `dungeons/misc/fx/fxmist01.nif` | `ex_waterfall_mist_s_01` | 41×192×**0** | 350×275×164 | flat plane for a volume |
| `dungeons/misc/fx/fxcloudthick01.nif` | `furn_mist256` | 415×1055×159 | 242×224×67 | 4x too big |
| `dungeons/misc/cobweb04.nif` | `furn_web10` | 192×**0**×192 | 128×128×256 | flat plane |
| `dungeons/misc/root03.nif` | `in_cavern_roots00` | 105×89×224 | 218×261×438 | half size every axis |
| `clutter/sack01.nif` | `contain_com_sack_02` | 24×24×31 | 42×46×76 | under half |
| `clutter/ingredskooma.nif` | `potion_skooma_01` | 3×3×5 | 7×7×20 | ~4x too short |
| `clutter/morro/n/nbbread01.nif` | `ingred_bread_01` | — | 26×10×7 | **mesh ships in NO game** |
| `clutter/morro/m/misculwucup.nif` | `misc_lw_cup` | — | 12×12×17 | **mesh ships in NO game** |
| `mip/container/crates/common_chest01.nif` | `contain_com_chest_01` | — | 81×39×49 | **mesh ships in NO game** |

Kept — the replacement is the same object at a comparable size:
`clutter/goldcoin01.nif`, `clutter/middleclass/middlecrate04.nif` (75³ vs 64³),
`lights/torch02.nif` (48 vs 49 tall, axes permuted),
`clutter/soulgemlesser01.nif`, `clutter/lowerclass/broomlower01.nif`,
`clutter/bread01.nif`, `dungeons/misc/root08.nif`.

### <a id="hovering-reseats"></a>Re-seated meshes are blacklisted, not offset

An in-game report of Tamriel Rebuilt's `0lightUdeUcandleU01U64` hovering showed
that the 2026-09-20 re-measure had never been pasted into `Z_RESEAT`: the file
still held the first census, and its `0lightudecandleu24u64` key was misspelled
(no `u` after `de`), so it never matched. The report's "applied" heading means
"passes the threshold", not "shipped".

Where Morroblivion lowered every base on a mesh by a large amount, the mesh is
blacklisted rather than offset. A hand-placed re-seat scatters (±1–2 units,
often under the 80% spike), and the Morrowind mesh at the Morrowind placement is
exact:

| Mesh | dZ | Refs |
|---|---|---|
| `dunmer/candle_01`, `_07`, `_09` | −16.8 … −19.2 | 136, 227, 131 (applied rows) |
| `dunmer/candle_02`, `_04`, `_08` | −18.5 … −20.0 | 73, 51, 75 (held, one population) |
| `common/candle_07` | −18.7 … −20.3 | 256 (held, one population) |
| `dunmer/lamp_06` | −127 / −136 | 116 (held, two populations) |
| `urns/clay_urn02`, `_05` | −8.5, −12.3 | 129, 230 |
| `urns/clay_urn03` | +16.8 (sunk) | 307 |
| `barrels/mwbarrel10` | −5 / −25 | ~200; replaces `0barrelu02umarshmerrow: -25` |

Sub-unit rows (±0.5–0.6 on clutter) are left out: invisible, and within Havok
settling.

The three meshes present in neither Morroblivion, Oblivion nor Skyrim are the
strongest entries: those bases currently render **nothing at all**.

**The `fire/` meshes are NOT blacklisted**, although they measure as swaps
(150×216×69 for a 18×17×114 standing fire). Confirmed in game as looking
correct, so the geometric test is overruled by the observation. They keep their
measured corrections instead: 270° pitch (92% / 87%) and +13.30 on the six
`0lightUFire*` bases (100%). A fire is a particle effect whose vertex bounds
describe its emitter volume, not an object's silhouette, which is why the
size comparison misreads it.

### <a id="needs-verification"></a>🛑 NEEDS IN-GAME VERIFICATION

**49 blacklist entries, none verified in game.** The blacklist is the safer
default ([why](#why-blacklist-wins)), not a confirmed result, and it now
governs far more objects than the 22 stock-mesh swaps it started as.

Watch for these when testing:

- **Every Dunmer lantern and most candles** now render the Morrowind mesh
  instead of Morroblivion's. If they look worse, the rebuilt mesh was the
  better object and the entry should come out.
- **`common/candle_10`** — the Silver Candlestick this audit began with. It is
  blacklisted rather than pitched, which is the opposite of what the first pass
  shipped.
- **`torchnohavok`** (331 refs) and **`tikilamp`** (265 refs) are the widest
  changes by reference count.
- **`morro/f/furnudeutableu01`** (145 refs) and **`furnudeubookshelfu02`**
  (178 refs) — furniture, where a wrong pivot is obvious.

The 13 surviving pitches are the 6 lava rocks (ratio 1.00/1.00/1.00, the
strongest evidence in the table) and 7 candles that measure as genuine axis
permutations.

### <a id="zero-delta-objects"></a>A floating object is not always a missing correction

Three objects reported floating or inverted in-game (2026-09-20) measure a
delta of **zero** on every axis, so Morroblivion never corrected them and no
table row is missing:

| Base | Refs | dZ | Rotation delta |
|---|---|---|---|
| `0barrelU02Upants` (`mwbarrel10.nif`) | 4 | −5.0 on 3 of 4 | 0° on all 230 refs of the mesh |
| `0MiscUComUBucketUMetal` | 15 | **0.0 on 15 of 15** | **0° on all three axes** |
| `0lightUdeUlampU03U64` | — | — | 0° on all 1,351 refs of the mesh |

Morroblivion placed these exactly where Morrowind did. The defect is therefore
either present in Morroblivion itself or introduced downstream in our own
conversion — it is **not** an uncopied hand-fix, and adding a table row would
be inventing a correction no authored data supports. `0lightUdeUlampU03U64` is
a mesh swap (above), which is a separate matter from its height.
