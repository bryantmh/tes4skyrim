# asset_convert/texture/parallax.py — textures, shaders and parallax

**Code:** `asset_convert/texture/parallax.py`, `asset_convert/texture/luminance_textures.py`, `asset_convert/texture/spec_mask.py`, `asset_convert/texture/texture_prune.py`, `asset_convert/texture/landscape_normals.py`

## Contents

- [Oblivion parallax → Skyrim height maps (asset_convert/texture/parallax.py, opt-in, 2026-08-15)](#oblivion-parallax-skyrim-height-maps)
- [Landscape normal maps: DXT1 = shiny ground (2026-07-09)](#landscape-normal-maps-dxt1-shiny)
- [Dependents borrow a master's textures](#dependents-borrow-a-masters-textures)
  - [A generated companion joins its family's namespace](#borrowing-across-a-namespace-boundary)
- [Per-game asset namespace](#per-game-asset-namespace)
  - [A master is resolved by `record_dir`, never by joining its name](#master-resolved-by-record-dir)
    - [...and the ROOT it resolves against is found by marker, not by `.parent`](#export-root-by-marker)
- [Loose .tga/.bmp textures are transcoded, not just copied](#loose-tgabmp-textures)
- [The blacklist prune](#the-blacklist-prune)

## Oblivion parallax → Skyrim height maps (`asset_convert/texture/parallax.py`, opt-in, 2026-08-15)
<a id="oblivion-parallax-skyrim-height-maps"></a>

`NiTexturingProperty.apply_mode == APPLY_HILIGHT2 (4)` is **Oblivion's parallax
switch**, and the height field lives in the **diffuse's ALPHA channel**. It was
long read here as a "detail-overlay blend weight"; the *action* that reading
produced (drop the NiAlphaProperty) is right either way, but the reason was
wrong and the height was being thrown away. Parallax and glow map are mutually
exclusive in Oblivion.

Skyrim's side: shader type **3** (Heightmap), `SLSF1_Parallax` in Shader Flags
1, the height map in **texture slot 3** as `<name>_p.dds`, height read from the
RED channel, **vertex colors required** (all-white is fine), incompatible with
glow map and env map, compatible with specular and shadow. Type 3 adds **no
conditional fields** to the record (`references/nif 0.10.0.0.xml`: only types
1/5/6/7/11/14/16 do), so setting it changes the enum and nothing else.

### 🔴 This must never be the default
Measured in game on a hand-built parallax shape (`temp/parallax_testbuild.py`,
one shape of `skingradbridgemain01.nif`, the other six as an in-frame control):

| Environment | Result |
|---|---|
| Vanilla SSE | the shape **swims** — visibly broken, not merely flat |
| + SSE Parallax Shader Fix | **identical**, no improvement |
| + Community Shaders | works, effect is good |

So the switch is `--parallax` (CLI), a GUI checkbox, and off everywhere else.
The converter cannot detect the player's shader setup, which is the only thing
that decides whether the output is correct. ENB also handles it (user's
report, not measured here). Do **not** rely on the SSE Parallax Shader Fix: it
did not carry the one test we ran.

### Two conditions, both required
1. **The mesh flag** answers "did the author want parallax here" — authored
   intent, never guessed at.
2. **The texture** answers "is there any height data to carry" — measured over
   Nehrim's full 12,437-mesh set with `tools/audit/parallax_check.py census`:
   **2359 flagged shapes** in 1267 meshes, on **130 distinct diffuse
   textures**, of which only **44** actually hold a height field.

| verdict | textures | shapes |
|---|---|---|
| **height** (converted) | **38** | **1495** |
| no alpha at all (DXT1) | 67 | 508 |
| flat/empty alpha | 14 | 279 |
| **too coarsely quantised** | **6** | **56** |
| soft-edged mask (bimodal) | 1 | 11 |
| transparency cutout (binary) | 1 | 1 |
| names a file that does not exist | 3 | 9 |

### 🔴 Count the LEVELS, not just the distribution (found in game, 2026-08-15)
The first version of the classifier tested range, mid-tone share and edge
share — and accepted six **DXT3** textures that pass all three. DXT3 stores
4-bit explicit alpha: **at most 16 distinct values, whatever the artist
painted.** Measured over the 44 it first accepted, the two clusters do not
overlap at all:

```
DXT3   7-16 levels     (RockBeach04: 7 levels over a range of 102)
DXT5 147-256 levels
```

A parallax shader OFFSETS by the height, so seven levels across a range of 102
is a ~15-unit step per level — the surface renders as **visible spikes and
terracing**, which is exactly how Oblivion's beach rocks looked in game.
`_MIN_LEVELS = 64` rejects them as `quantised`. The threshold sits in the empty
gap between the clusters, so it is not fitted to the data, and the check counts
LEVELS rather than testing the FourCC — a DXT5 alpha that happens to be an
eight-step staircase is just as unusable.

The flag on those meshes is genuine (`rockbeachshell045.nif` carries HILIGHT2
in Nehrim's own BSA and has no loose override at all). What was wrong was
assuming a flag plus a plausible-looking histogram implies usable data.

**Per shape the yield is 63%, not 29%** — the textures that carry height are
the ones used everywhere (cave and fort-ruin walls, the Lazeon interior set).
Flag alone would write an empty height map and switch the shader for the other
864 — producing exactly the swimming surface above. Skipping those is the
**faithful** conversion: with no alpha channel to read, Oblivion renders no
parallax there either. Nothing is invented; the categories are counted and
printed per build. (Whoever wants more should use the TES4N2HGenerator, which
reconstructs height from the normal map properly.)

🔴 **Count textures by LOWERCASED path, not by spelling.** Oblivion meshes
spell the same file several ways — Nehrim's Lazeon walls appear as both
`Lazeon\` and `lazeon\`. Keying on the verbatim string turns these 130
textures into 163 and the 44 height maps into 74, and an earlier measurement
(`temp/parallax_yield.py`, `sorted(set(diffuses))`) reported exactly those
inflated figures. `census` prints both counts side by side so the two can be
reconciled instead of re-investigated. The remaining 74-vs-75 difference is
the one soft-edged mask: the author's own `dds_is_parallax` lacks the
`edge_ratio >= 0.70` guard that `_is_clear_parallax` beside it applies, and we
apply it.

All three unresolvable textures are defects in the source, not path bugs here:
`lazeon\static\wanda2.dds` exists nowhere in the BSAs;
`lowres\architecture\leyawiin\SkingradTrim05.dds` names the Leyawiin folder for
a texture that lives in `architecture\skingrad\`; and one mesh
(`leyawiinhouselower02_far.nif`) drops the separator entirely and writes
`textureslowres\…`. That last one is **one reference in one mesh out of
12,437** — a single-record typo, deliberately not special-cased.

Distribution is worth knowing: **63% of flagged shapes are vanilla Oblivion**
(dungeons 808, rocks 340, architecture 338), not mod content — so the gain
starts at the base conversion. Architecture is where the DXT1 no-data cases
cluster; a 6,000-mesh sample that happened to exclude it put DXT1 at 12%
instead of 37%.

### 🔴 Amplitude: the cap, and NEVER touch a map that is already good
<a id="amplitude-cap"></a>
The raw Oblivion/Nehrim alpha renders far too deep in Skyrim. Both engines
read the channel identically (white out, black in, mid-grey neutral) — what
differs is the depth the shader gives it: Community Shaders computes
`maxHeight = 0.1 * scale` from an engine parameter no texture can influence,
and Oblivion's figure is unmeasurable from here (compiled shader packages).

The rule must not damage a mod that ships GOOD maps. A hand-made height map
works in both engines — the user's own set is authored for Oblivion, rebuilt
from the normal maps and hand-tuned, and renders correctly in Skyrim too.
Calibrated on **56 pairs of the same texture**, hand-tuned vs Nehrim's
original (`temp/parallax_pairs.csv`):

| | min | p25 | median | p75 | max |
|---|---|---|---|---|---|
| hand-tuned (good) | 130 | 144 | **146** | 147 | **148** |
| Nehrim (raw) | 95 | 159 | **203** | 254 | **255** |

```
cap 148 -> 100% of the hand-tuned set untouched, 83% of Nehrim's corrected
```

Then TWO whole reference folders were measured, not just the paired subset,
and they disagree: folder A (1738 fields) median 145 max 148, folder B (1893)
median **153** max **156**. The same author normalizes to ~145 in one set and
~153 in the other, so "amplitude below X" is a CONVENTION test, not a law. A
cap of 150 — which folder A alone appeared to justify — would have compressed
all 1893 maps in folder B. That is exactly the damage this constant exists to
prevent, and it was caught only because a second folder got checked.

Nehrim's over-deep textures start at 159 and the ones that drew the complaint
are 169 and up (`wandb` 171). No texture on either side falls between 156 and
169, so the threshold goes in the middle of that empty gap:
**`DEFAULT_MAX_RANGE = 163`** clears folder B's ceiling by seven and still
catches everything from 169 up. The exact value inside that window changes no
behaviour — it only buys margin.

🔴 Treat this as FRAGILE: it separates two populations ~15 steps apart, and a
mod that normalizes to 170 WOULD be compressed. It is still the BEST
AVAILABLE, and that was measured. As a DETECTOR the tone-curve figure is
worse — over 3631 good fields vs Nehrim's 56:

```
amplitude   > 163      keeps 100% of good, catches 73% of bad
deep share  >  20%     keeps  88%,          catches 80%
deep share  >  40%     keeps  98%,          catches 48%
amp>163 OR deep> 25%   keeps  93%,          catches 92%
amp>163 OR deep> 50%   keeps  98%,          catches 80%
```

Amplitude is the only rule that keeps ALL of them: the hand-tuned deep share
ran 0..94% (median 6.3) so some good maps look legitimately almost black, while
their amplitude stops dead at 156. Do not swap the detector for the curve's
figure; it has been tried twice.

A field already inside the cap is returned **bit for bit unchanged**; only the
over-deep ones are compressed, and around their own MEDIAN so the body of the
distribution stays where the author put it. On Nehrim's 38 shipped maps: 33
compressed, 5 untouched.

### 🔴 The thing that actually mattered: how FLAT the face is (rebuilt 2026-08-16)
<a id="how-flat-the-face-is"></a>
A hand-made height map is a flat face with narrow grooves; Nehrim's raw alpha
undulates everywhere. Stated so no outlier can distort it — the share of the
surface within ±20 levels of its own median:

| | hand-tuned | Nehrim |
|---|---|---|
| **share within ±20 of the median** | **63.2** | **36.6** |

That is a SHAPE difference. **Shifting cannot touch it** (linear, moves the
median with the body), which is why matching amplitude (150 vs 143) changed
nothing about the impression. Only a tone curve does.

#### The measure this replaced was outlier-confounded — do not go back to it
The first version used the share of texels in the bottom third of `min..max`.
That threshold comes from the EXTREMES, so a couple of bright texels stretch
the range and drag the whole surface into the "deep" band.
`leyawiinmetalstrip03.dds` — a flat plate with **two rivets** — scored
**94.2% deep** while 93.7% of it lies within ±20 of the median (p95 83, p99
jumps to 132). Recomputed on a robust p05..p95 range the separation collapses:

| | hand-tuned | Nehrim |
|---|---|---|
| deep third, full range | 12.1 | 39.1 |
| deep third, robust range | 25.8 | 37.9 |
| **share within ±20 of the median** | **63.2** | **36.6** |

The middle row is the finding: once the range is made robust, the deep-third
figure stops separating the two populations at all. `temp/dark_reference_maps.csv`
was generated with the broken figure and lists flat textures with bright specks.

#### The curve: `x**g` cannot do this job, and the reason is worth keeping
`x**g` compresses one END of the range, so the share inside a band around the
median **is not even monotone in g** — measured over all 38 maps, 21 DIP before
they rise as g falls, so a bisection has nothing to bisect on. Inside the range
its own posterisation floor allowed (g ≥ 0.63 at amplitude 255) the median
share only moved 53% → 56%. The 63% target is out of reach for that family.

The replacement works on the DISTANCE from the median:

```
y = med + sign(d) * D * (|d| / D) ** p          d = v - med
```

`D` per side, so `lo`→`lo` and `hi`→`hi` exactly and the amplitude is
untouched. `p > 1` presses the body together and steepens the tails. Two
properties `x**g` lacked:

- **Monotone in p by construction** — raising `p` moves every texel weakly
  closer to the median, so the share inside any band can only grow. That is
  what makes the bisection valid.
- **It cannot punch holes.** Steepest slope is `p`, at the ENDS, where `x**g`
  had *unbounded* slope at 0 — the thing that turned `cave04` into grey
  plateaus with black holes. The linear step then scales even that down by `f`.

The fit is O(1) per bisection step: the curve is monotone and fixes the median,
so the texels landing inside the band are those between the band edges'
pre-images, read straight out of a cumulative histogram (`_flat_share_at`).

**Ceiling on `p` is the authored floor, not a round number.** Counting the
distinct levels actually occupied inside each reference map's own ±20 band,
over all 3631: `min 21, p05 25, median 41, max 41`. A ±20 band spans 41 levels,
so the median hand-tuned map uses every one and none drops below 21 — hence
`_MIN_BODY_LEVELS = 21`. A texture that simply IS restless goes as far as that
allows and no further; a partial correction beats a destroyed map.

#### Calibration — check BOTH reference folders, again
The target is the median of the whole corpus, not of the 56 pairs:

| | p05 | median | p95 |
|---|---|---|---|
| both folders (3631) | 34.7 | **63.3** | 84.8 |
| folder A (1738) | 49.9 | 69.6 | 85.7 |
| folder B (1893) | 29.3 | 51.3 | 83.1 |

🔴 The same author normalizes to 69.6% in one set and 51.3% in the other — the
**same split that made the amplitude cap dangerous** (medians 145 and 153).
The pooled median 63.3 is the natural target and sits between them;
calibrating on either folder alone lands 6–12 points off. The spread is wide on
purpose: half the corpus sits below 63%, so this says "as flat as a typical
hand-made map", not "flatter than every one". It is a CURVE TARGET only — as a
detector the same figure fails badly (see `DEFAULT_MAX_RANGE`).

#### The target saturates, which is what makes it safe to turn
`TARGET_FLAT_SHARE = 0.68`, not the pooled 63.3, on the author's in-game
verdict: *"the maps are OK, they could go a touch flatter"*. 0.68 stays inside
the hand-tuned population, in the direction of folder A's own 69.6.

Swept through the shipped module over the 38 maps, the median flat share is:

```
target   63%    66%    68%    70%    75%    80%
median  63.4   65.4   65.4   65.4   65.4   65.4
```

It stops at 65.4 and does not move again. **`_MIN_BODY_LEVELS`, not the target,
is what ends the curve** — beyond that point the fit would have to press the
face flat, and the guard refuses. So this dial cannot be over-turned, which is
the property that makes tuning it by eye safe.

Three safety properties hold by construction, not by observation:

- **bit-identical maps are decided by the amplitude detector alone** — 6 of 38
  stay bit-identical across the entire target sweep; the curve never runs on a
  map the detector let through (`f < 1.0` gates it);
- **posterisation is bounded** — steepest slope is `p·f` with `p ≤ 4` and
  `f ≤ 1`, so at most ~4 levels. Measured largest output gap is 10 and it sits
  in a map that was only *shifted*, i.e. it is authored, not introduced;
- **the body keeps ≥ 21 levels**, the reference population's own floor.

At 0.68: flat share median **40.4% → 65.4%**, poorest corrected body 34 levels.

#### ✅ Validated against a third-party pack nobody calibrated on
The detector was tuned on two folders by ONE author, which is a real weakness —
so it was then run against QTP3 (Qarl's Texture Pack 3), a well-known Oblivion
replacer with no connection to this project. `dungeons\caves`, via
`tools/audit/parallax_check.py pack`:

| | |
|---|---|
| 38 DDS | 19 diffuse, 19 `_n` normal maps (never read) |
| of the 19 diffuse | **9** hold a usable height field, 8 have no alpha channel at all, 2 are cutout masks |
| of the 9 | **5 bit-identical**, 4 corrected |

The split is clean and lands where it should:

```
untouched   amplitude  82 .. 137      cave08, cave03, cave06, cave04, cave01
corrected   amplitude 207 .. 255      cave07, cave11, cave10, cave12
```

**Nothing sits between 137 and 207.** All four corrections fired on amplitude;
the median floor did not fire once (no QTP3 median is below 45). Largest output
gap 2 — no posterisation. On two of the four the tone curve contributed exactly
+0.0, i.e. the amplitude cap alone had already brought them inside the target.

The author's own verdict on that split: the five left alone are the ones that
"would already look good", the four changed are the ones that are "extreme to
very extreme". That is an independent confirmation of the threshold, on content
it was not fitted to.

Worth stating plainly: those four ARE authored work being changed — `cave07`
loses a third of its depth. The justification is the same premise the whole
correction rests on (Community Shaders renders the same field deeper than
Oblivion does), and `--max-range 0` turns it off for anyone who disagrees.

### 🔴 `durchgangD`: a SECOND defect, and the band measure does not cover it
<a id="durchgangd-second-defect"></a>
`lazeon\static\durchgangD` — a practically black wall, median 17, amplitude 158
— reads as **89.2% flat** on the band measure, and correctly so: it *is* flat,
just parked entirely at the bottom of the channel. Restlessness and
off-centeredness are two different defects and the curve only fixes the first.

The mechanism is why it matters: a parallax shader offsets along the view
vector by `(height − neutral)`, so a surface sitting near 0 renders not as depth
but as a **constant view-dependent UV shift** — the texture slides across the
wall as the camera moves. Same swimming artefact an empty height map produces.

**Centering was rejected once and came back only on new evidence.** The old
refutation stands for what it tested: a *tolerance around mid-grey*, measured on
56 pairs, where the medians overlap so badly that sparing 96% of the good maps
also spares 53% of Nehrim's. `MIN_MEDIAN` is a different rule — a **one-sided
floor** — and the evidence is the full 3631-map corpus:

| median level | min | p05 | median | p95 |
|---|---|---|---|---|
| hand-tuned | **52** | 94 | 125 | 175 |
| Nehrim | 17 | 31 | 105 | 169 |

```
floor < 45   touches 0 of 3631 hand-tuned (0.00%), catches 4 Nehrim maps
floor < 60   touches 3 of 3631 (0.08%)
```

Nothing the author shipped is darker than 52. The four it catches are
`durchgangD` (17), `durchgangA` (31), `decked` (32) and `bodend` (36) — exactly
the set sitting just under the amplitude threshold at 153–159 that used to ship
untouched. 45 sits in the middle of the empty gap between 36 and 52, the same
way `DEFAULT_MAX_RANGE` sits in the gap between 156 and 169.

A map caught by this rule ALONE gets the re-centering shift and nothing else —
no compression, no tone curve. A pure translation cannot damage relief: every
level, gradient and gap survives. Verified on `durchgangD`: median 17 → 113,
amplitude 158 → 158, levels 149 → 149, gap 5 → 5, and the render goes from a
black slab to a legible stone wall with the mortar joints as the deep parts.
The amplitude detector remains the only thing that may compress a field.

Consequence worth knowing: the amplitude target had been dialled down to 80 by
eye, but that was compensating for the wrong SHAPE. With the curve in place it
went back up to 140 — next to the hand-tuned set's own 143.

**Detection and correction depth are two separate numbers** — `max_range`
decides WHICH maps are touched and is pinned at 150 by the hand-tuned set
(130..148); `target_range` decides HOW FAR a condemned map is taken and is
free to go lower. That split exists because the eye and the measurements
disagree: corrected to amplitude 150, Nehrim's `wandb` still read as too
strong in game while the hand-tuned version at **143** read as right. Three
explanations were measured and all three failed —

* amplitude: 150 vs 143, five percent apart;
* steepness: ours is **0.70x** theirs in UV space (mean |dh| per texel x width
  — resolution cancels, so 1024² and 2048² are comparable), i.e. ours is the
  FLATTER one;
* a material depth parameter: shader type 3 has none (only type 7,
  ParallaxOcc, carries `Scale`), Community Shaders' Extended Materials exposes
  on/off switches and nothing numeric, and `HeightScale *= PBRParams1.y`
  belongs to the TruePBR path, not to vanilla parallax shading.

So what drives the perceived strength is still unexplained, and the depth of a
corrected map is a dial set by eye. Keeping it separate from the detector
means turning that dial can never cost a mod anything it authored well —
pinned by `test_lowering_the_target_never_reaches_a_good_map`.

**The refuted theory, so it is not retried:** re-centering on mid-grey. It is
the obvious idea — Nehrim's `wandb` sits at median 63 with 92% below mid-grey
while the hand-tuned version sits at 126 — but the same 56 pairs kill it. The
hand-tuned medians scatter 86..132 and overlap Nehrim's, so any tolerance that
spares the good maps also spares half the bad ones (tolerance 40: 96% of the
good set kept, only 53% of Nehrim's corrected). Amplitude separates cleanly;
centering does not.

Tuning does NOT need a mesh rebuild — the meshes never change, only the
`_p.dds`. Use `python tools/audit/parallax_check.py regen [--max-range N]
[--strength F] [--only SUBSTRING]`, which rewrites the maps in seconds and
reports per texture whether the cap bit or the map was left alone.

#### Rejected by measurement: clamping the tails
The theory was that Nehrim's amplitude comes from a few extreme texels a
p1..p99 clamp could shave. It does not — `core(p5..p95)/full` is **0.68 for
Nehrim and 0.54 for the hand-tuned set**, i.e. Nehrim's depth sits in the BODY
of the surface and the hand-tuned maps have relatively MORE tail. A p1..p99
clamp alone leaves Nehrim's amplitude at 83% and brings only 39% under the cap.

### Output conditioning: halve → blur → curve → BC4 (added 2026-08-19)
<a id="output-conditioning"></a>

**Skyrim's parallax sampling is coarser than Oblivion's.** Verified in game by
the author: an unsmoothed Oblivion height field reads as "comic" under Skyrim's
stepping. So *every* map is smoothed, not just the ones a detector flags.

The chain in `build_height_map` is, in order:

1. **`mitchell_halve`** — half linear size, Mitchell-Netravali (B = C = 1/3),
   resampled for an exact 2× reduction so the tap offsets are constant and the
   seven weights are a literal (`-5/288, 1/36, 77/288, 4/9, …`, summing to 1).
   **Lanczos was ruled out deliberately** — too sharp for a field that is
   already slightly soft, which is the whole point of the blur that follows.
2. **`gaussian_blur`** — radius `BLUR_RADIUS_PER_1000 = 5.0` texels per 1000
   texels of *output* width, i.e. resolution relative; σ = radius/3. A fixed
   pixel radius would hit a 512 map about eight times harder than a 4096 one
   and this content ships both. Below ~100 px output width the radius falls
   under 0.5 and the blur is skipped — small maps are left alone by design.
3. **`normalize_height`** — the tone curve, **last**.
4. **`encode_bc4_dds`**.

#### 🔴 The order is why nothing needed recalibrating

`normalize_height` is not a fixed curve, it is a **fit onto a measured property
of its input** (share of area within ±`FLAT_BAND` of the median, target
`TARGET_FLAT_SHARE`). Run it LAST, on the texels that actually ship, and it
still lands on the calibrated target whatever the halving and the blur did to
the field. Putting the blur *after* the curve would silently give back part of
the in-game-approved depth.

Halving is also a straight **speed win** on the slowest step: `encode_bc4_dds`
is pure Python, one 4×4 block at a time, inside the mesh workers — a quarter of
the pixels is a quarter of the blocks. Both new passes are numpy and accumulate
tap by tap rather than gathering, because a 4096-square map would otherwise
materialise a 234 MB intermediate in each of nine workers.

Measured on `anvilcastledoor01.dds` (4096×8192, 42 MB), `temp/bench_chain.py`:

| step | s |
|---|---|
| `decode_alpha_plane` | 8.45 |
| `mitchell_halve` | 1.78 |
| `gaussian_blur` (r = 10.2) | 0.47 |
| `normalize_height` | 0.38 |
| `encode_bc4_dds` at half | 4.60 |
| **new chain** | **15.68** |
| `encode_bc4_dds` at full — what the old chain paid | 18.00 |
| **old chain** | **26.45** |

So the conditioning is **41% cheaper per texture**, not more expensive: the
encoder saves 13.4 s and the two new passes cost 2.25 s. `decode_alpha_plane`
is now the single biggest cost and is still pure Python — the next place to
look if this ever needs to be faster.

### Diffuse → BC1: a block strip, not a recompression (added 2026-08-19)

Once the height is out in a `_p` map the diffuse has no use for its alpha, and
DXT1 is half the size. **This is not a re-encode.** Every height-carrying
diffuse is DXT5 — `classify_alpha` rejects DXT1 and uncompressed outright, and
`_MIN_LEVELS` rejects every DXT3 source — and a DXT5 block is 8 bytes of alpha
followed by 8 bytes of color **in exactly BC1's color-block layout**. So the
color half is copied verbatim, keeping the endpoints the original encoder
chose.

**Dithering and perceptual error metrics therefore have nothing to act on**:
nothing is being quantised. Decoding to RGB to re-compress with dithering would
*lose* quality, not gain it.

The one real difference is DXT1's 3-color mode. Two exact repairs, neither
changing a texel's color (`_bc1_repair_modes`):

| source block | repair |
|---|---|
| `c0 > c1` | copy verbatim — already a legal 4-color DXT1 block |
| `c0 < c1` | swap the endpoints, XOR the index word with `0x55555555` (0↔1, 2↔3) — the swapped palette names the same four colors |
| `c0 == c1` | zero the indices. Every palette entry already equals `c0`, and DXT1 index 3 would be **transparent black** |

Verified on real Nehrim textures: first 64 blocks decode identically, file
exactly halved (170 KB → 85 KB).

#### The gate: a shape that BLENDS with the alpha vetoes the strip

`strip_diffuse_alpha` runs after the texture copy (same reason as the
landscape-normal fix) and keys on the presence of `<name>_p.dds` beside
`<name>.dds` — the mesh stage already decided that texture carried height, so
no plumbing is needed and a non-parallax build is a no-op by construction.

But a texture-level classification is not the whole answer. If some *other*
shape reads that diffuse's alpha as opacity, that is evidence the channel is
not a height field there, whatever the classifier said. `process_geometry`
records those diffuses in `alpha_opacity_diffuse` (a set, carried separately
from the `parallax` Counter) and the strip skips them.

Measured on the author's NTATU/Qarl parallax mod: 39,201 shapes, 134 textures
classified `height`, of which **1** — `architecture\chorrol\interior\
forgeembers01.dds` — is read as opacity by a non-parallax shape. One in forty
thousand, but the converter runs on plugins nobody has measured, so the gate is
generic rather than a bet on that number.

### The global depth scale (added 2026-08-19)

Oblivion's authored depth reads far too bumpy under Skyrim whatever the map was
calibrated for, so **every** map is compressed toward the neutral plane:

    v' = 128 + (v - 128) * DEPTH_SCALE          # 0.6, confirmed in game

**128 is not a guess.** Community Shaders pivots the height on 0.5 twice over —
`AdjustDisplacementNormalized` returns `(displacement - 0.5) * scale + 0.5 +
offset`, and the POM ray starts at `minHeight = maxHeight * 0.5`. Above 128 a
surface pushes OUT, below it pushes IN, so compressing toward 128 reduces
displacement in both directions and a groove never flips into a bump.

#### 🔴 GLOBAL, not per-map — the trap that was nearly built

The first cut normalized every map to a fixed target amplitude. That is wrong:
it makes a plaster wall exactly as deep as a cave wall and throws away the
relief the author actually authored — the same trap `normalize_height` already
warns about under `strength`. One factor for every texture keeps every
relationship between two surfaces intact and only bounds the excursion.

Prior art confirms the shape of the fix. The author's own `TES4N2HGenerator`
ends its pipeline with Output Levels (Output Black 26 / Output White 165, clamp
26..179) — the same global band operation — and the shipped NTATU/Qarl pack
measures 30..179, so `clamp_max` is visible in the data. Those values are
calibrated for Oblivion's much gentler offset mapping, which is why Skyrim needs
a further factor on top rather than a different band.

Not taken from that tool: its Contrast (150) and Balance. Shape correction is
already done by `TARGET_FLAT_SHARE`, calibrated in game; two S-curves stacked
would fight each other.

#### Why this one has no detector

Everything above the halve/blur/depth block is a CORRECTION — it decides a map
is defective and leaves everything else bit-identical, which is what protects a
mod author's own calibration. These three are a SYSTEM ADAPTATION and run
unconditionally, from any source, because the target engine samples differently:

| step | when |
|---|---|
| amplitude cap (163) | outliers only |
| median floor (45) | sunk maps only |
| tone curve | only if the cap fired |
| **halve, blur, `scale_depth`** | **always, every map, every source** |

`build_height_map` is the single funnel — the mesh converter and
`parallax_check.py regen` are its only two callers, and it is the only thing
that calls `encode_bc4_dds`. There is no second route by which a `_p.dds` can
come into being.

### `--textures-only`: hand the mesh side to PGPatcher (added 2026-08-19)

`convert.py -f <plugin> --meshes-only --parallax --textures-only` reads and
analyses every NIF and writes **none** of them; only the textures ship, height
maps included.

The reason is that there is a better mesh patcher than us for this job.
**PGPatcher** (ParallaxGen) runs over the player's finished load order, so it
sees every plugin at once, and it can also upgrade a shape to ENB's
complex-material system — which Community Shaders reads too. Neither is
knowable from inside a single-plugin conversion. What PGPatcher cannot do is
recover a height field out of Oblivion's diffuse alpha, and that is exactly
what we keep.

The meshes still have to be READ: whether a diffuse carries height is only
knowable from the shape's own `APPLY_HILIGHT2` flag, the authored intent. So
the analysis is unchanged and only the emit is dropped — `convert_nif` returns
right after `_harvest_textures`, through the same `_finish_result` the normal
path uses, so `batch_convert`'s accounting does not silently read zero.
Animation-object projects, grass models and book inventory art are skipped too,
being mesh products.

### Implementation notes
- `classify_alpha` returns a **category** (`height` / `binary` / `bimodal` /
  `empty` / `no_alpha` / `unreadable`), not a bool, so the build log can say
  WHY a shape was skipped. Thresholds come from the user's own
  TES4AutoParallaxer, tuned on this content.
- DXT5 alpha must be decoded through the **interpolated palette**. Sampling
  only the two endpoints misreads every smooth height field as binary — a
  gentle block's endpoints sit far apart with all six mid-tones between them.
- **Empty alpha reads WHITE (mean 255), not black.** Every flat channel
  measured on Nehrim was 255. Code that assumes an unused channel is 0 gets
  these exactly backwards.
- Output format is **BC4**: one channel, BC1's file size, no banding on grey
  gradients. Written without texconv — a BC4 block is byte-for-byte a DXT5
  ALPHA block, so `encode_bc4_dds` reuses the decoder's own understanding of
  the format. The palette index is **computed, not searched** (quantise
  `hi - v` onto sevenths); the 8-way search cost 4x as much and this runs
  inside the mesh workers. Cost per 512x512 texture: 0.23 s end to end.
  Beyond-Skyrim's BC1 recommendation is for the vanilla path we do not serve.
- Normal maps in both games are **DirectX convention** (green/Y inverted).
  Anything reconstructing height FROM a normal map must flip before taking
  gradients — omitting it produced garbage on the first test build. (Not used
  by the converter, which reads the authored alpha; relevant to the tooling.)
- The prune keeps the maps by itself: the shape names `_p.dds` in slot 3, so
  `_harvest_textures` puts it in the mesh manifest. `_p` is also in
  `texture_prune._MAP_SUFFIXES` and `_companions` derives it from any kept
  diffuse — two independent reasons, no extra handling.
- Alpha-blended flagged shapes need no separate exclusion: the HILIGHT2 branch
  already drops a blend-enabled NiAlphaProperty (below), so by the time the
  shape ships it is not blended. A surviving alpha there is test-only
  (`0x12EC`), and alpha-tested cutout + parallax is legal in Skyrim.
- Audit either side with `python tools/audit/parallax_check.py census|verify`.

## Landscape normal maps: DXT1 = shiny ground (2026-07-09)
<a id="landscape-normal-maps-dxt1-shiny"></a>
- Skyrim's landscape shader reads the normal map ALPHA channel as the specular mask. Oblivion's terrain shader never used it, so most Oblivion landscape `*_n.dds` are DXT1 (no alpha) → sampled alpha = 1.0 → full-strength specular over the whole terrain (user-visible "very shiny ground"). Oblivion normals that are already DXT5 carry a real mask (avg ~77/255) and are correct as-is.
- Fix: `asset_convert/texture/landscape_normals.py` (pipeline step after the texture copy, so re-copies can't resurrect DXT1) re-containers DXT1 → DXT5 with constant dark alpha 32/255. DXT1 and DXT5 share the 8-byte color block format, so RGB is preserved losslessly; DXT1 3-color blocks (c0<=c1, ~0.05%) get endpoints swapped + indices 0↔1 remapped since DXT5 color blocks are always 4-color mode.
- **A DXT5 landscape normal with a "real-looking" mask is not evidence of intent either.** Morroblivion's 127 terrain normals (`tes4\landscape\morro\`) are all DXT5; 97 classify as a per-texel mask with median alpha 102 and p90 119, against a median of 16 / p90 72 for Oblivion's own DXT5 landscape normals. In TES4 the normal alpha is the PARALLAX height field (what `parallax.py` turns into `_p` maps), never specular, so `run()` now gives every landscape normal the 32/255 constant whatever its format, skipping files already carrying it.
- **A land texture with NO normal map at all renders shiny too.** `convert_LTEX` names `<diffuse>_n.dds` in every TXST, and Morrowind ships no normal maps: Tamriel Rebuilt's 294 LTEX all pointed at files that exist nowhere in `output/`. `landscape_normals.ensure_ltex_normals` (run for every plugin at the end of `convert_meshes`, even one with no texture tree of its own) looks for each LTEX's normal under every plugin's output tree and writes a flat DXT5 normal with the 32/255 mask under this plugin's tree when none ships it. Path spelling comes from `tes5_import.record_types.common.landscape_texture_path`, the same function the TXST writer uses. NOT yet in-game verified.
- Related: LTEX SNAM is a Phong exponent (never write 0 — see convert_LTEX comment); the alpha mask is what actually controls specular *amount*.

### <a id="default-normal-is-dxt5"></a>The shared `default_n.dds` is DXT5, not the uncompressed form

`write_default_normal` emits the stand-in normal a mesh names when its own
normal map does not exist: flat (128,128,255) with a constant specular mask.

It is written DXT5, NOT the uncompressed form `lod_gen` uses for atlases,
because the alpha has to be a REAL specular mask -- `spec_mask` classifies an
uncompressed DDS as `no_alpha` whatever its content, so an uncompressed
stand-in reads as "no mask" and lands back at full-strength specular.

Each 16-byte block is hand-packed: an 8-byte constant-alpha block followed by a
4-byte color block whose c0 == c1 (RGB565 for (128,128,255) is R=16, G=32,
B=31) and whose 32-bit index word is 0, so every texel resolves to c0.

It must run AFTER `normalize_specular_alpha`, which would otherwise count this
constant-alpha stand-in as one more file it fixed.

## Dependents borrow a master's textures
<a id="dependents-borrow-a-masters-textures"></a>

**Code:** `_refs_from_dependents`, `_dependent_export_dirs` in `asset_convert/texture/texture_prune.py`

Every converted plugin ships its own BSA pair, and all of them merge into ONE
flat Data namespace holding exactly one file per path. So a child plugin's mesh
that names `<ns>\architecture\cathedral\tracery01.dds` resolves against the
MASTER's copy and correctly ships none of its own -- `_fill_missing_lod_textures`
relies on the same rule, and duplicating instead would cost 828 files / 165 MB
for ElsweyrAnequina alone.

But `build_refs` assembles each plugin's keep-set from that plugin's OWN
producers (its mesh manifest, its records, its generated meshes), and
`bsa_pack` drops anything absent from it. A texture only a DEPENDENT uses is
invisible to the master that ships it.

Measured with each supplier's keep-set reconstructed the way `nif_batch` builds
`textures_used` (the union of every converted mesh's own texture paths):

| plugin | borrowed refs the SUPPLIER would prune |
|---|---|
| Knights.esp | **58** (cathedral floor/pillar/step/wall, from Oblivion.esm) |
| TWMP_Valenwood_Elsweyr.esp | **2** (`anvilstonetrimuc02`, from Oblivion.esm) |
| Oblivion.esm, Tamriel.esp, Translation.esp | 0 |

No Oblivion mesh or record names `tracery01.dds`; Knights' meshes do. Oblivion's
BSA dropped it and Knights rendered untextured. Loose `output/` copies survive,
so this only ever appeared in a PACKED build -- the same blind spot that hid the
tree-billboard prune.

The fix unions in each dependent's mesh manifest. Membership is AUTHORED, read
from `_HEADER.txt` via `master_names`: a sibling export dir counts only when it
declares this plugin as a master. Never a filesystem sweep of `output/*`, which
would keep unrelated plugins' art and defeat the prune.

### A generated companion joins its family's namespace
<a id="borrowing-across-a-namespace-boundary"></a>

**Code:** `COMPANION_ROOTS` in `asset_convert/game_paths.py`

Sharing above works because supplier and borrower write the SAME namespace, so
the master's shipped path is the one the dependent's records and meshes name.
A master in a DIFFERENT namespace silently breaks that:
`resolve_source_texture` still finds the file in the master's EXPORT tree, so
conversion succeeds and nothing warns, but the borrower emits its OWN namespace
while the only shipped copy sits under the master's. Purple at runtime.

`Morrowind-Morroblivion-Compatibility.esp` was the case that exists. It is
built standalone from the vanilla Morrowind ESMs and so declares NO master —
nothing to declare — which made `namespace_for` read it as a game root and give
it a namespace of its own. Everything that borrows from it (`Tamriel_Data.esm`,
Tamriel Rebuilt) roots at Oblivion through `Morrowind_ob.esm` and writes
`tes4`. Measured on the built output: 4,783 patch textures and 552 meshes, and
of 2,043 distinct refs in Tamriel_Data's first 3,000 meshes, **440 resolved to
nothing; 437 of those the patch ships**. Tamriel Rebuilt, which ships no art at
all, lost **92 of its 297 LTEX land textures** the same way.

The patch is not a game — it is a generated COMPANION to the Morroblivion load
order, where **Oblivion is the root master**, so the whole family shares one
namespace. Listing it in `COMPANION_ROOTS` makes it resolve to
`DEFAULT_NAMESPACE`, and every borrower's existing `tes4\` path then names
exactly where the patch already ships.

🛑 **Nothing is copied.** Textures are REFERENCED from the master that ships
them — see [dependents borrow a master's textures](#dependents-borrow-a-masters-textures).
Copying a borrowed tree into each dependent duplicates art the masters already
provide and throws away the 828-file/165 MB saving that rule exists to protect.

## Per-game asset namespace
<a id="per-game-asset-namespace"></a>

**Code:** `namespace_for`, `set_namespace`, `current_namespace` in `asset_convert/game_paths.py`

Every converted asset used to ship under one hardcoded `tes4\` folder,
whatever game it came from. All converted plugins merge into ONE flat Data
namespace holding exactly one file per path, so two unrelated games writing the
same relative path silently overwrite each other -- last BSA in load order wins.

Measured on the built output, FalloutNV.esm against Oblivion.esm:

| kind | colliding paths | identical | **DIFFERENT** |
|---|---|---|---|
| meshes | 14 | 0 | **14** |
| textures | 25 | 1 | **24** |

`tes4\marker_error.nif` is 7,178 bytes in FNV and 3,420 in Oblivion;
`tes4\creatures\dog\dog.dds` is 1,398,256 against 699,192. Nehrim is the same
hazard with far more overlap -- it is a total conversion built on Oblivion's
asset NAMES with modified content -- and shares no worldspace or master with
Oblivion, so neither should ever see the other's meshes.

The namespace is the NAME of the masterless plugin at the root of the master
chain, read from `_HEADER.txt` via `master_names`. Resolved over the 11 plugins
with headers in `export/`:

```
Oblivion.esm   (masterless) -> tes4        <- kept, so existing output stays valid
  Knights.esp, Tamriel.esp, Morrowind_ob.esm, ElsweyrAnequina/Pelletine,
  TWMP_Valenwood_Elsweyr, Unique Landscapes  -> tes4
FalloutNV.esm  (masterless) -> falloutnv
  Fallout3.esm (master=FalloutNV.esm)        -> falloutnv
Nehrim.esm     (masterless) -> nehrim
```

Plugins that share a master keep sharing art, which is required -- Knights
borrows Oblivion's cathedral textures rather than shipping its own (see
[dependents borrow a master's textures](#dependents-borrow-a-masters-textures)).
Only unrelated FAMILIES are separated.

Oblivion keeps `tes4` deliberately: it is the largest existing output tree, and
renaming it would invalidate every shipped BSA for no collision benefit.

`Fallout3.esm` declares FalloutNV.esm as its master in this workspace (a
TTW-style setup), so it correctly lands in the FNV namespace rather than
claiming its own.

🛑 **The record side must move in lockstep.** `tes5_import` writes the
namespace into MODL paths; if the asset copy and the record writer disagree,
every converted record points at a path no BSA provides. That is total purple,
far worse than the handful of missing textures this fixes.

### A master is resolved by `record_dir`, never by joining its name
<a id="master-resolved-by-record-dir"></a>

**Code:** `_chain_root` in `asset_convert/game_paths.py`, `export_dirs` in
`asset_convert/sources/base_plugins.py`

An imported mod's plugins share ONE folder named for the MOD, so `export_root /
<master name>` does not exist for them and the master is silently dropped — no
warning, the walk simply stops at the wrong plugin. `Tamriel_Data.esm` lives in
`export/Tamriel Data (HD)/`, and the plain join found nothing:

| plugin | masters declared | resolved before | resolved now |
|---|---|---|---|
| Tamriel Rebuilt | Morrowind_ob, compat patch, **Tamriel_Data** | 2 of 3 | 3 of 3 |

Tamriel Rebuilt therefore lost the master holding the art it places, from every
texture and asset fallback. `tests/test_plugin_path_resolution.py` fails the
build on a bare join for exactly this reason; it caught all three sites
(`_chain_root` twice, `overrides/manifest.py` once). `record_dir` already falls
back to `<root>/<name>` when no registry claims the plugin, so it is always the
correct call — an `os.path.join` fallback behind it only reintroduces the bug.

### ...and the ROOT it resolves against is found by marker, not by `.parent`
<a id="export-root-by-marker"></a>

**Code:** `_export_root` in `asset_convert/game_paths.py`

Calling `record_dir` is necessary but not sufficient: it also needs the export
ROOT. `_chain_root` took `export_dir.parent`, which is the root only for a
plain `export/<plugin>/`. For a plugin nested in a mod folder
(`export/<mod>/<plugin>/`) the parent is the MOD folder, so every master
resolved to a path under it that does not exist, the walk fell through to
`return dirs[0]`, and the namespace was named after that bogus dir.

`Grass_Aes_TRv25_05_mowed.esp` (masters `Morrowind_ob.esm`, the compat patch,
`TR_Mainland.esm`) resolved all three against `export/Aesthesia groundcover/`:

| plugin | chain root before | ns before | chain root now | ns now |
|---|---|---|---|---|
| Grass_Aes_TRv25_05_mowed | `Aesthesia groundcover/Morrowind_ob.esm` (absent) | `morrowindob` | `Oblivion.esm` | `tes4` |

It therefore wrote WRLD MODL `morrowindob\worldmapclouds\wrldmorrowind.nif`
while Morrowind_ob, Tamriel_Data and TR_Mainland all wrote `tes4\...`. No mesh
is ever generated under `morrowindob\`, and the grass ESP loads last and wins
the override, so WrldMorrowind got a dangling cloud model and the world map
drew NO clouds. `_export_root` walks up to `output_layout.REGISTRY_FILENAME`
(`sources.json`, which marks the export root and exists for exactly this
distinction) and falls back to `.parent` only when no marker is found.

## The namespace crosses process boundaries through the environment
<a id="namespace-crosses-process-boundaries"></a>

**Code:** `NAMESPACE_ENV`, `set_namespace` in `asset_convert/game_paths.py`

The active namespace is module state, and **module state does not survive a
process boundary.** Windows SPAWNS pool workers -- a fresh interpreter that
re-imports `game_paths` and starts at `DEFAULT_NAMESPACE`. Measured, parent set
to `falloutnv`, in an unseeded spawned child:

```
namespace     = 'tes4'
anim_prefix   = 'Animations\TES4Guns\'
inv_tex_dir   = 'textures\tes4\clutter\books\inv'
script_name   = 'TES4_AbcScript'
project_hkx   = 'Actors\tes4\creatures\scamp\tes4creatures_scampproject.hkx'
```

Every value wrong. This is why guns, books, creatures and scripts kept writing
`tes4` folders under FalloutNV long after the helpers themselves were correct
-- an audit of the helpers, or a grep for a stray `tes4` literal, can never
find it, because there is no offending string and no wrong helper.

An AST sweep of all 55 pool constructions found **10** whose worker reached a
namespace-derived helper without seeding it. Fixing them one pool at a time
requires every future call site to remember, which is precisely the failure
mode -- three separate rounds each fixed the pools then in view and missed the
rest.

So the namespace rides the ENVIRONMENT, which spawn and `subprocess` both
inherit by construction: `set_namespace` exports `TESCONV_ASSET_NAMESPACE`, and
module init reads it back. A child is correct with no per-pool code, and a pool
added later inherits it without knowing this contract exists.

Per-pool `initializer`/`initargs` seeding is kept where it already exists
(`nif_batch`, `spt_converter`, `lod_far_gen`, `script_convert/pipeline`): it is
harmless, explicit, and independent of environment inheritance.

🛑 The environment is per-process GLOBAL state, so a parent that converts two
plugins must call `set_namespace` for each -- it is the phase's entry point
that owns this, not the pool.

## The blacklist prune
<a id="the-blacklist-prune"></a>

**Code:** `is_excluded` in `asset_convert/texture/texture_prune.py`

The prune is a BLACKLIST of categories Skyrim cannot load, not a keep-set
reconstructed from references. A blacklist can only ship a file nothing needs;
a keep-set can WITHHOLD one something needs, and that failure is invisible in
testing.

### Why the keep-set was removed

It rebuilt each texture's name from the records, the mesh manifest and a scan
of late assets, then packed only what it had predicted. Any name it failed to
predict was dropped from the archive while surviving in `output/` -- so the
mesh rendered untextured for anyone installing the BSA and looked perfect in
loose-file testing. Three independent instances, all found in one session:

- **Tree billboards.** The generator names a card after the OUTPUT record's
  model; the keep-set guessed from the EXPORT's `.spt` MODL. Import fans 14
  source SPTs out into 30 per-record NIFs, so the two disagree by
  construction: 58 of 63 billboards dropped from Unique Landscapes, 136 from
  Nehrim, 33 from Oblivion. An earlier fix had reduced Oblivion's from 43 by
  adding a second producer, which is the shape of the problem -- each new
  producer covers one more way to spell a name it still has to guess.
- **Creature textures.** `creatures/rat`, `bear`, `minotaur`, `chicken` and
  others dropped across UL and Oblivion, cause never diagnosed.
- **`obliviongate` and `fire`**, ~50% and ~20% dropped, never diagnosed.

Those last two were never explained because the design makes a drop
unremarkable: every run dropped thousands of files legitimately, so a
wrongly-dropped one had nowhere to stand out.

### The rules

Each excludes a whole second-level subtree (below the game namespace), plus a
non-texture extension rule. Verified against every path named by 36,128
converted meshes across 8 plugins -- **zero** of the files these rules drop is
named by any mesh:

| rule | files | MB | false positives |
|---|---|---|---|
| `faces` | 11,068 | 496.6 | 0 |
| `menus` | 6,701 | 400.0 | 0 |
| `menus80` | 3,669 | 177.8 | 0 |
| `menus50` | 3,787 | 52.9 | 0 |
| `landscapelod` | 264 | 213.1 | 0 |
| `distantlod` | 0 | 0.0 | 0 |
| `lowres` | 758 | 1.5 | 0 |
| non-`.dds`/`.tga` | 150 | 1,120.3 | 0 |

`faces/` is FaceGen output keyed `<formid>_0.dds`, which Skyrim regenerates
from NPC records; `landscapelod/` is superseded by our own bake into
`AutoConvertLOD`; the `menus*` trees are Oblivion UI atlases.

The extension rule exists because mods ship build junk under `textures/`: one
nests an entire Oblivion `Data` folder (`.bsa`, `.esp`, `dlclist.txt` --
1.1 GB) under an architecture path. The only extensions on disk across the
corpus are `.dds` (62,170) and `.tga` (1); everything else is junk.

### What is deliberately NOT excluded

`characters/` holds 254 MB of Oblivion NPC art and we ship no NPC meshes, so
nearly all of it is dead -- but not all. Measured, 52 files under it ARE named
by shipped meshes: all of `characters/imperial/**` (44 files -- vanilla body
maps: `footfemale`, `handmale`, `upperbodymale`, `underwear`), plus
`characters/hair/**`, `characters/*/hair/**` (`ren/hair/rengrey.dds`, nested
one level deeper than a top-level `hair` exemption would catch),
`characters/frost zombie/**`, and `arenaspectator.dds` at the root. Excluding
the subtree needs five exemptions carved back out of it, and `imperial` is
only special because it happens to be the vanilla body race -- the next plugin
could use `nord/`. A blacklist that needs a whitelist inside it is not a
category rule, so `characters/` ships whole.

Likewise `nehrim/nehrim/` (600 files, 191 MB, 0 named -- a mod author's
dumping ground of `bell.dds`, `cube.dds`, `elevator01.dds`) is genuinely dead,
but the rule to catch it would name one plugin's folder. Both are accepted
dead weight: shipping a few hundred MB nothing reads is strictly cheaper than
one texture that fails to ship.

<a id="loose-tgabmp-textures"></a>
## Loose .tga/.bmp textures are transcoded, not just copied

**Code:** `asset_convert/texture/image_transcode.py`

`tex_paths.as_dds` rewrites every `.tga`/`.bmp` reference in a converted NIF to
`.dds`, because Morrowind and Oblivion both name the artist's source file while
their archives ship the DDS and substitute the extension at load. Skyrim does
not substitute, and it cannot read TGA or BMP at all.

The rewrite is therefore only half a fix. A plugin that ships its art LOOSE
rather than in a BSA has no DDS for the mesh's rewritten path to find, so the
mesh renders untextured. Measured on Arktwend: 5,589 DDS ship, but 179 `.tga`
and 78 `.bmp` do too, and **251 of those have no DDS twin** — every mesh naming
one of them lost its diffuse. `arktwendenglish\a pmelise.tga` was the reported
case; `pmelisenstrauch.NIF` asks for `a pmelise.dds`, which was never written.

So the copy is followed by a transcode: any `.tga`/`.bmp` with no `.dds`
sibling gets one written beside it, mip chain included. Skipping when the twin
exists keeps the pass idempotent and, more importantly, stops it clobbering
the DDS the plugin shipped itself or one the L8/specular/parallax repairs
rewrote — which is also why it runs FIRST, before those passes, so they see
the new files.

`.jpg` is deliberately left alone (1 file on Arktwend). `as_dds` does not
rewrite `.jpg`, so transcoding it would write a DDS nothing names while the
mesh still points at a file Skyrim cannot read; the fix there is a path rewrite,
not an encode.

### DXT5 only when the alpha is real

Format is picked from the pixels, not the source extension: DXT5 when any
alpha sample is under 250, DXT1 otherwise. TGA authors habitually save 32-bit
with a fully opaque alpha channel — 94 of the 172 orphaned TGAs read as RGBA —
and taking those as DXT5 would double their size for nothing.

Both block encoders already existed for the terrain LOD bake. A DXT5 block is
a BC4 alpha block followed by an unmodified DXT1 color block, so
`encode_bc4_channel` and `encode_dxt1_quality` compose into one without a new
codec. The only gap was the header: `dds_header` writes one side into both
dimensions, and 44 of these textures are non-square, so the width word is
patched after the fact rather than duplicating the 128-byte layout.
