# asset_convert/character/facegen_tri.py - faces, skin tone and tints

**Code:** `asset_convert/character/facegen_egt.py`, `tes5_import/actors/npc_face_mapper.py`, `tests/test_import.py`

## Contents

- [The symptom](#symptom)
- [The old mechanism, and why it failed](#old-mechanism-why-failed)
- [Where the color actually lives](#where-color-actually-lives)
- [The .egt FaceGen texture basis](#egt-facegen-texture-basis)
- [Per-NPC FGTS is negligible — do not use it](#per-npc-fgts-negligible-do)
- [Ruled out by measurement](#ruled-out-measurement)
- [The implemented conversion](#section)
- [Verified result (output ESM, all 15 races authored)](#section-2)
- [Guarded by](#guarded)
- [Rebuilding](#rebuilding)

How a converted NPC gets its skin color, why the old approach was wrong, and
the measurements that settle it. Implemented in
`tes5_import/actors/npc_face_mapper.py` (`load_race_skin_tones`, `_pick_skin_tone`)
and `asset_convert/character/facegen_egt.py`.

## The symptom
<a id="symptom"></a>

White NPCs — Imperials especially — converted to very dark skin.

## The old mechanism, and why it failed
<a id="old-mechanism-why-failed"></a>

`_RACE_SKIN_TONES` was a census of Skyrim.esm: per race+gender, the top three
`TINC` colors vanilla NPCs use on their skin-tone layer, weighted by count and
picked per NPC by hashing its FormID.

The census itself was accurate. `python -m tools.generators.gen_npc_skin_table --race
ImperialRace` genuinely reports 42 of 185 vanilla Imperial males at
(87, 61, 51). The error was treating that distribution as a target.

Simulated over 2,000 FormIDs, the picker produced:

| race/gender | outcome |
|---|---|
| Imperial Male | **72.3%** at luma 66–71 (very dark), 27.6% at luma 180 |
| Imperial Female | 38.5% at (221,221,221), near-white |
| Nord Male | 95.7% at luma 161–180 |

Vanilla Skyrim's Imperial males are a *bimodal* population (pale Imperials plus
a large body of deliberately dark-skinned Imperial-race NPCs); their mean QNAM
is (0.515, 0.42, 0.374) → luma **112**, between the two modes. Sampling the top
three modes proportionally reproduces the tail, not the center.

Two compounding defects:

- **`TINV` was pinned to 100.** QNAM is `lerp(white, tint, TINV/100)`, so at
  full strength it degenerates to the raw TINC — the color is painted flat with
  no blend toward the base texture. Vanilla's own RACE record uses fractional
  interpolations (0.35, 0.38, 0.42, 0.5).
- **Only one tint layer was written.** Of 2,892 tinted vanilla NPCs, only
  **3.6%** have exactly one layer; the mode is 4–6, and 12 is common. (Not
  fixed — one skin-tone layer is sufficient for body color; noted for context.)

## Where the color actually lives
<a id="where-color-actually-lives"></a>

Oblivion authors skin color in the **RACE record**, in two parts:

1. **The body/face part `ICON` texture paths.** Emitted per gender under
   `NAM1`/`MNAM`/`FNAM`, positionally (`INDX` then `MODL`/`ICON`).
2. **The RACE record's own `FGTS` vector** — 50 float32 FaceGen
   Texture-Symmetric coefficients.

The split is exact and is the whole design:

| races | own texture? | race FGTS |
|---|---|---|
| GoldenSaint, Argonian, Khajiit, DarkElf, Orc, Dremora, DarkSeducer | yes | **all zeros** |
| HighElf, Redguard, Nord, Breton, Imperial, WoodElf | no — all share `Characters\Imperial\HeadHuman.dds` | **non-zero** |

A race either ships its own skin textures, or shares another race's and
**recolors them with FGTS**. That is why High Elves are gold and Redguards
brown despite pointing at the same tan human texture. There is no race without
authored color; earlier analysis that found "missing" colors was reading only
the textures.

## The `.egt` FaceGen texture basis
<a id="egt-facegen-texture-basis"></a>

`FGTS` coefficients index the `.egt` file beside the head mesh (e.g.
`meshes/characters/imperial/headhuman.egt`). Layout confirmed byte-exact and
matching `references/pyffi_src/pyffi/formats/egt/egt.xml`:

```
char[8]   "FREGT003"
int32     width         256 for faces, 32 for bodies/ears
int32     height
int64     num symmetric  = 50, matching FGTS's 50 floats
int64     num asymmetric = 81
byte[32]  reserved
then num_symmetric records of:
    byte[3]   unknown
    byte      flags      (bits: intensity 0-1, enable 2, slot 3-5, maxed 6, invert 7)
    int8[w*h] R plane    signed per-texel delta
    int8[w*h] G plane
    int8[w*h] B plane
```

Verification: `(9,830,664 − 64) / (4 + 3×65536) = 50.0` exactly, zero
remainder, on `headhuman.egt`; the 32×32 ears file fits the same way.

**Every one of the 50 modes is chromatic; none is achromatic.** Measured mean
per-channel offsets:

| mode | mean ΔRGB | character |
|---|---|---|
| 1 | (−52.1, −4.8, +35.2) | red↓ / blue↑ (warm↔cool) |
| 0 | (+63.1, +80.7, +101.4) | cool cast |
| 11 | (−0.0, +19.6, −10.0) | green↑ / blue↓ |
| 14 | (−1.7, −26.1, −6.5) | green↓ |

This is why searching FGTS for a *brightness* axis fails: the basis is
mean-centered and whitened, per-component variance is flat across all 50
(sd 1.06–2.02), and no single component correlates with luminance. Brightness
is an emergent sum, not a basis vector.

## Per-NPC FGTS is negligible — do not use it
<a id="per-npc-fgts-negligible-do"></a>

`NPC_` records carry their own FGTS (2,176 distinct vectors across 2,482
Oblivion NPCs, so it is genuinely per-NPC). Reconstructing every NPC's color
from it gives a **within-race standard deviation of ~1 unit in 255**:

| race | n | mean RGB | sd RGB | luma sd |
|---|---|---|---|---|
| Imperial | 812 | (198.1, 138.8, 107.3) | (1.0, 1.0, 1.3) | 1.0 |
| Breton | 266 | (197.9, 138.6, 107.1) | (0.8, 1.0, 1.1) | 0.9 |
| Redguard | 133 | (198.5, 139.3, 107.8) | (0.6, 0.7, 0.9) | 0.6 |
| Nord | 175 | (197.6, 138.2, 106.3) | (0.8, 0.9, 0.9) | 0.8 |

Oblivion *can* express a continuous 50-D chromatic gamut, but in practice
authors barely used it: within a race, NPCs are effectively one skin tone.
`_pick_skin_tone` therefore ignores per-NPC FGTS and keeps its `fid` parameter
only so callers need not care.

For scale, Skyrim has **206 distinct TINC values** across every tint layer of
all 5,118 vanilla NPCs — the target palette is small regardless.

## Ruled out by measurement
<a id="ruled-out-measurement"></a>

- **`textures/faces/<plugin>/<formid>_N.dds`** (2,473 files, one per NPC) are
  **normal maps**, not baked diffuse skin: they sample to ~(128,130,132) at
  saturation 0.01–0.12 across every race. Not a color source.
- **`head<race>f10..m60.dds`** age files are detail/normal maps — flat grey
  (66,65,66). `sample_texture_rgb()` rejects any near-greyscale sample
  (max−min < 4) specifically so these can never be mistaken for skin.

## The implemented conversion
<a id="section"></a>

```
skin_rgb = sample(race body/face ICON texture)
         + FGTS_SCALE * Σ race_FGTS[i] * egt_mode_i
```

`FGTS_SCALE = 0.25`, calibrated against a built-in control: Redguard shares
`HeadHuman.dds` and recolors it via race FGTS, but Oblivion *also* ships a
standalone `Characters\Redguard` body texture at (89,54,35). The scale that
makes the reconstruction agree with that independent texture is ≈0.25, which
also lands High Elf at gold hue 45–47 and Nord palest.

`TINV` is now **80**, not 100, so the tint blends toward the base texture; QNAM
is derived from the same value and stays consistent with the layer.

Fallback colors (`_SKIN_FALLBACK_RGB`) cover an older export lacking the RACE
part/FGTS fields, or a custom race whose assets cannot be resolved.

## Verified result (output ESM, all 15 races authored)
<a id="section-2"></a>

`python -m tools.generators.gen_npc_skin_table --esm output/Oblivion.esm/Oblivion.esm`:

| race | gender | TINC | luma | hue | sat |
|---|---|---|---|---|---|
| Nord | Male | (235, 159, 140) | 174 | 12 | 0.40 |
| Breton | Male | (225, 161, 115) | 171 | 25 | 0.49 |
| HighElf | Male | (213, 180, 78) | **180** | **45** | 0.63 |
| WoodElf | Male | (199, 135, 102) | 146 | 20 | 0.49 |
| Imperial | Male | (187, 117, 75) | **129** | 22 | 0.60 |
| Khajiit | Male | (189, 124, 58) | 133 | 30 | 0.69 |
| Orc | Male | (103, 111, 45) | 105 | 67 | 0.59 |
| DarkElf | Male | (89, 88, 83) | 88 | 50 | **0.07** |
| Redguard | Male | (119, 56, 30) | **68** | 18 | 0.75 |
| Argonian | Male | (101, 56, 33) | 64 | 20 | 0.67 |
| Dremora | Male | (59, 58, 55) | 58 | 45 | 0.07 |
| DarkSeducer | Male | (89, 88, 83)→(81,77,86) F | 79–88 | 267 | 0.10 |

Imperial went from luma **66** (old, 72% of the time) to **129**. Redguard is
the darkest human race, Nord the palest, High Elf reads gold, Dark Elf grey.

<a id="finding-race-textures"></a>**Finding the textures.** A race's body
texture and head `.egt` are record paths in the author's case. They resolve
through `case_paths.resolve` under each export's ASSET tree (`assets_for`, so a
nested mod's `<Mod>/textures/` is searched, not its record folder). Before
that, a case-sensitive filesystem resolved 0 of Oblivion.esm's 15 races and
every NPC took the `_SKIN_FALLBACK_RGB` color; now 15 of 15 (38 of 38 lookups),
and Frostcrag Reborn with its masters 17 of 17.

## Guarded by
<a id="guarded"></a>

- `tests/test_import.py::test_npc_skin_tone_tint_layer` — layer present, QNAM
  agrees with TINV, Nord clearly paler than Redguard.
- `tests/test_import.py::test_race_skin_tones_are_authored_colors` — High Elf
  gold hue, Dark Elf grey saturation, Orc green channel dominant, the
  Redguard < Imperial < Nord luma ordering, and Imperial not dark.

## Rebuilding
<a id="rebuilding"></a>

Changing any of this requires **both** stages — the RACE part/FaceGen fields
are new export output:

```bash
python convert.py -f Oblivion.esm --export-only
python convert.py -f Oblivion.esm --import-only
```

## Skin tone tint layers
<a id="skin-tone-tint-layers"></a>

Skin tone tint layers
Skyrim colors an NPC's body skin from the tint layer whose race mask type
is "Skin Tone" (RACE TINP=6).  An NPC without one renders pale white no
matter its race, so every converted NPC gets one.

The COLOR comes from Oblivion's own authored data, not from a census of
Skyrim: a TES4 RACE record names its skin textures (body/face part ICON
paths) and carries its own FGTS vector.  A race either ships its own
textures with FGTS all-zero, or shares another race's textures and recolors
them with a non-zero FGTS -- which is exactly how High Elf reads gold,
Redguard brown and Nord pale while all three point at
Characters\Imperial\HeadHuman.dds.  See asset_convert/character/facegen_egt.py for the
reconstruction and docs/commentary/asset_convert_facegen.md for the measurements.

Per-NPC FGTS is deliberately NOT used: measured across all 2482 Oblivion
NPCs it shifts the color by a standard deviation of ~1 unit in 255, so
within a race Oblivion NPCs are effectively a single skin tone.

## FGTS coefficient scale (0.25)
<a id="fgts-coefficient-scale"></a>

Coefficient scale.  The EGT planes are quantized signed bytes; FGTS
coefficients are in FaceGen's normalized units.  Calibrated against the
built-in control: Redguard shares HeadHuman.dds and recolors it via race
FGTS, and Oblivion ALSO ships a standalone Characters\Redguard body
texture at (89,54,35).  The scale that makes the reconstruction agree with
that independent texture is ~0.25; see docs/commentary/asset_convert_facegen.md.
