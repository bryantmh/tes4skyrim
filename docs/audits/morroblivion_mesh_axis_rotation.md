# Morroblivion mis-authored mesh axes — ref-rotation census (2026-09-17)

The original Morroblivion converter exported a set of meshes built along **+Y
instead of +Z**, then compensated by baking a pitch into every placed ref. Any
plugin that places those bases *without* the compensation — Tamriel Rebuilt does
— renders them on their side.

Reported symptom: TR ref `0383CB89` (cell `0382E6C5`) on Morroblivion base
`012C0134` (`0lightUcomUcandleU10U128`, Silver Candlestick,
`Morroblivion\Lights\Common\candle_10.nif`) lies flat.

## The mechanism, measured

Geometry extents of the affected meshes (vertex bounds, `sse_nif.read_nif`):

| Mesh | sizeX | sizeY | sizeZ | Y range | Longest |
|---|---|---|---|---|---|
| `common/candle_10.nif` | 18.5 | 37.3 | 18.6 | -9.6 → +27.7 | **Y** |
| `common/candle_01.nif` | 16.7 | 27.7 | 17.1 | -7.6 → +20.0 | **Y** |
| `common/lantern_02.nif` | 14.0 | 38.7 | 14.0 | -35.2 → +3.5 | **Y** (negative) |
| `lights/torchnohavok.nif` | 8.2 | 47.8 | 8.5 | -14.4 → +33.5 | **Y** |

X and Z are symmetric about zero; the object's height lies on Y. A ref pitch of
RotX=270° is what stands them upright. `lantern_02` runs along **-Y** (hangs
downward), which is why its refs add roll 180°.

## Ref census

Morroblivion vs the Morrowind originals, joined on normalized EditorID
(Morroblivion mangles `_`→`U` and prefixes `0`), comparing pitch/roll
distributions per mesh.

Base case, `012C0134` ↔ Morrowind `00E94F2F` (`light_com_candle_10_128`,
`l\LIght_Com_Candle_10.NIF`):

| Plugin | Refs | RotX | RotY | RotZ |
|---|---|---|---|---|
| Morroblivion | 8 | **270° (8/8)** | varies (yaw) | 0 |
| Tamriel Rebuilt | 36 | **0° (36/36)** | 0 | varies (yaw) |

Both plugins place the same base; only Morroblivion applies the correction. TR
uses RotZ as yaw, the normal upright convention.

Corpus-wide, restricted to meshes where ≥90% of Morroblivion refs are tilted to
a right angle:

| Class | Meshes | Refs | Fixable by one table entry? |
|---|---|---|---|
| UNIFORM | 27 | 1,259 | yes |
| BIMODAL | 18 | 1,319 | no — author picked ±90° per ref |

`mwTilt` (share of the Morrowind originals that were tilted) separates the two
causes cleanly and is the load-bearing column:

- **0–2%** — Morroblivion-introduced. All are lights: `candle_01/02/03/05/08/09/`
  `10/13/14/16`, `candle_blue_01/02`, `candle_red_01`, `candle_green_01`,
  `candle_ivory_01`, `lantern_01/02`, `buglamp_01`. Pitch 270°, 91–100%
  agreement.
- **91–100%** — Morrowind-authored tilt, **not our bug**: `inulavaurocku17/18`,
  `inumudcaveustal20`, `doorudwrvuloadup00`. Morrowind itself places these
  rotated; leave them alone.

Vanilla *Oblivion* fire meshes (`Fire\FireOpenMedium*.nif`) are correctly
authored and genuinely mixed (172 upright vs 99 at 270°) — per-ref artistic
placement, correctly excluded.

### BIMODAL detail

`torchnohavok.nif`: 602 refs — 463 at 270°, 101 at +90°, only 7 upright, against
456 Morrowind originals that are 94% untilted. The mesh axis is wrong *and* the
author chose a direction per ref (torches mount pointing up or down), so baking
one rotation cannot express it. `dunmer/lantern_06s.nif` has 136 refs and **zero**
upright, but its geometry is diagonal (sizeY 34.9, sizeZ 36.2 — lantern plus
chain), so it has no clean right-angle correction either.

## <a id="the-correction"></a>The correction, as shipped

The mesh is left alone; only a non-owning plugin's references are pitched.

**Code:** `tes4_export/morroblivion_axis.py`, applied by
`remap_vanilla_models` / `_pitch_placements` in `tes4_export/morroblivion.py`.

It lives in the **Morrowind export**, not the import. `remap_vanilla_models`
already walks every `MODL` line to swap a vanilla mesh for Morroblivion's, and
the ownership answer is free there: a pitch is registered only for a model that
came back from `replacement()`, meaning a vanilla path this plugin does **not**
own. Exporting Morroblivion itself substitutes nothing — its records already
name `Morroblivion\...` directly — so its own references can never be pitched,
and the importer stays generic, needing no Morroblivion knowledge on the
~1.2M-ref hot path every plugin and game shares.

`_pitch_placements` then rewrites `RotX` in place for every REFR/ACHR/ACRE
naming a registered base.

Two tables, kept disjoint by `test_morroblivion_axis_pitch.py`:

- `AXIS_PITCH_DEG` — the 16 meshes one right-angle pitch demonstrably fixes.
- `AXIS_PITCH_UNSURE` — the 25 that are mis-authored but which one pitch cannot
  fix, each with the measurement that disqualified it. Recorded so a later pass
  re-measures rather than rediscovering them; never applied.

Verified against the exports: Morroblivion's own 686 references to these bases
are 99% already pitched, Tamriel Rebuilt's 2,997 are 96% upright. That 99%-vs-4%
split is what makes ref ownership the right key.

Still open: the BIMODAL meshes (`torchnohavok.nif` foremost, 602 refs) and the
diagonal ones (`candle_ivory_01.nif`), where per-ref intent cannot be recovered
from a table.

Scripts: `rot_census.py`, `rot_by_mesh.py`, `rot_table.py`, `rot_bimodal.py`,
`mesh_axis.py` (one-offs, scratchpad — a re-run needs only the two exports).

**Status: diagnosis only. No code written, nothing fixed, not verified in-game.**
