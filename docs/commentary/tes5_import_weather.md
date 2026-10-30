# tes5_import/record_types/world.py - weather and climate

**Code:** `script_convert/converter.py`, `tes4_export/record_types/world.py`, `tes5_import/record_types/world.py`, `tests/test_import.py`

## Contents

- [The chain — weather is never referenced directly](#chain-weather-never-referenced-directly)
- [CLMT](#clmt)
- [WTHR field semantics — where equivalence does NOT hold](#wthr-field-semantics-where-equivalence)
- [Vanilla-substitution map (Oblivion WTHR -> vanilla Skyrim WTHR)](#vanilla-substitution-map)
- [Sunless skies — the Oblivion realms must have no sun](#sunless-skies-oblivion-realms-must)
- [REGN weather — where Cyrodiil's weather variety actually lives](#regn-weather-where-cyrodiils-weather)
- [Scripted weather (ForceWeather / SetWeather / ReleaseWeatherOverride)](#scripted-weather)
- [Sky meshes need BSSkyShaderProperty](#sky-meshes-need-bsskyshaderproperty)
- [Verifying](#verifying)
- [First-principles engine comparison (2026-08-24) — what each engine DOES with weather data](#first-principles-engine-comparison-what)
- [The WTHR record, field by field — what the ENGINE DOES with each value (2026-08-24)](#wthr-record-field-field-what)
- [Exact algorithms: fog, and the dome alpha ramp (2026-08-24)](#exact-algorithms-fog-dome-alpha)
- [What the algorithms say about our current converter (2026-08-24)](#what-algorithms-say-about-our)
- [ENGINE-VERIFIED semantics: disassembly of the actual consumers (2026-08-24)](#engine)
- [LNAM: every read of TESWeather+0x7D0 in the binary (exhaustive)](#lnam-every-read-tesweather0x7d0-binary)
- [OBLIVION SIDE, from the binary and the shipped shaders (2026-08-24)](#oblivion-side-from-binary-shipped)
- [FOG: the one place the two engines genuinely differ (2026-08-24)](#fog-one-place-two-engines)
- [Oblivion's HDR pass, disassembled (2026-08-24) — the last unknown](#oblivions-hdr-pass-disassembled-last)
- [The two tone mappers side by side — and a correction (2026-08-24)](#two-tone-mappers-side-side)
- [THE HORIZON, solved (2026-08-24)](#horizon)
- [Fog equation equivalence — the proof, and the vanilla context](#fog-equation-equivalence-proof-vanilla)
- [IMPLEMENTED (2026-08-24) — what changed in the converter](#section)
- [NAM0 color: the per-plugin normalization is GONE, replaced by a highlight knee (2026-08-24)](#nam0-color-per-plugin-normalization)
- [Cloud layers: the sheets go on 11 and 27, and the reason is UV (2026-08-24)](#cloud-layers-sheets-go-11)

> **Status (2026-08-09): the FULL chain is live on this branch** (ported from
> the old `weather-conversion` WIP branch and extended). WTHR runs in its own
> serial import phase (2b) minting four IMGS companions per weather; CLMT is
> in the generic dispatch; WRLD always writes CNAM (DefaultClimate fallback);
> REGN weather entries are converted; exterior CELLs carry XCLR (the runtime
> path for region weather); rain/snow weathers get vanilla SPGD precipitation
> via MNAM; and the script converter emits real Weather.* calls. The former
> `CONVERT_CLIMATE` feature flag is deleted.
>
> In-game rounds (2026-08-09) found, in order: day overexposure (median-
> anchored IMGS + fog max), weather never changing (CELL XCLR + volatility
> 50), the scripted-weather override lock (abOverride=False now), Sun Glare/
> Trans Delta out of vanilla range, missing IMGS DNAM — and finally the REAL
> bloom source: **Oblivion's color palette itself runs 1.5-4x vanilla
> luminance**, now self-normalized per plugin (see below), plus weather winds
> mixed as SFX instead of ambience, and moons shipped from Oblivion's own
> textures at the engine's hardcoded paths. Fourth build awaits in-game
> confirmation.

Sources of truth for everything here: UESP `Skyrim Mod:Mod File Format/WTHR`
and `.../CLMT`, xEdit `wbDefinitionsCommon.pas` (`wbWeather*`) and
`wbDefinitionsTES4/TES5.pas`, a census of the **84 vanilla WTHR records in
Skyrim.esm**, and static analysis of `Oblivion.exe`. Field LAYOUT came from
xEdit; field SEMANTICS came from UESP plus the vanilla census. Getting the
layout right while guessing the semantics is what produced a blinding-white
sky and stars drawn in front of the world.

## The chain — weather is never referenced directly
<a id="chain-weather-never-referenced-directly"></a>

    WRLD --CNAM--> CLMT --WLST--> WTHR

CLMT used to be in `SKIP_TYPES` ("climate system differs"), which was wrong:
TES5's CLMT is near-identical to TES4's. Skipping it orphaned all 37 converted
weathers and Cyrodiil rendered under Skyrim's own climate, sun and moons.

**57 of 84 TES4 worldspaces author no CNAM at all** — including Tamriel, every
Imperial City district and every walled city. Oblivion resolves that at
RUNTIME, not load time: in `Oblivion.exe`, the sky setup at `0x667688` calls
the worldspace get-climate (`0x4CAF90`); when it returns null it falls through
to `0x543200`, which does `LookupForm(0x15F)` — the engine-created
`DefaultClimate` (bootstrap at `0x44CCE9` pushes `0x15F`, names it from the
string at `0xA37CA0`). Skyrim has **no such fallback**, so `convert_WRLD`
writes TES4's DefaultClimate explicitly. It is a raw TES4 id and must go
through `remap_formid`.

## CLMT
<a id="clmt"></a>

Only one format change: the WLST entry gained a trailing Global FormID,
8 -> 12 bytes (`FormID, Chance:s32, Global`). FNAM/GNAM sun textures and the
TNAM times/moons byte are verbatim (TES4's moons byte 0xC3 = Masser+Secunda+
phase 3 is byte-identical to vanilla SkyrimClimate's).

**TNAM volatility is NOT a passthrough.** TES4's byte spans 0..255 and
Oblivion cycles weather regardless of it (TamrielClimate 174, DefaultClimate
0 — both change in-game); in Skyrim it is the re-roll chance and the vanilla
census is bimodal: **SkyrimClimate, the one variable outdoor climate, ships
exactly 50**, and 0 appears only on locked skies (Sovngarde, Blackreach,
BloatedMan's Grotto). Passing TES4's 0 through froze every converted sky on
its first weather — one of the two halves of the "weather is always clear"
bug (the other was missing CELL XCLR). Converted climates write 50.

`MODL` is the **night-sky/stars mesh** and every vanilla Skyrim climate has one
(`Sky\Stars.nif`); the TES4 export was dropping it, so a climate with no model
draws no stars. Falls back to `Sky\Stars.nif`.

## WTHR field semantics — where equivalence does NOT hold
<a id="wthr-field-semantics-where-equivalence"></a>

### NAM0 color table (272 bytes = 17 slots x 4 times x RGBA)

TES4 has 10 slots, TES5 has 17; times-of-day match, so only the type axis is
remapped. TES4's single `Fog` feeds both `Fog Near` and `Fog Far`. The TES4
cloud tints do NOT go to the Cloud LOD slots — vanilla ships those BLACK
(median and p90 are 0 in every slot); they feed the per-layer PNAM instead.

### Colors are LUMINANCE-NORMALIZED, not copied (the real bloom source)

**Oblivion authors its weather palette far hotter than Skyrim, and bloom
triggers on rendered luminance — no imagespace calibration can fix colors
that run 1.5-4x vanilla.** Census (real Skyrim.esm vs raw conversion, midday
medians): Sun slot 193 vs vanilla **43** (Skyrim's sun brightness is HDR, not
this slot — a 255-luminance disc blooms enormously, the "white toward the
sun"), Sunlight 206 vs 152, Sky-Upper 137 vs 84, Fog 137 vs 96 — while
Ambient is authored at HALF vanilla (92 vs 172), stacking blown highlights on
black shadows ("lighting doesn't look right").

`set_nam0_normalization()` (import Phase 2b pre-pass) is self-calibrating
per plugin: it computes the plugin's median luminance per slot/time over all
its weathers and scales every color so the plugin median lands on the
vanilla median — hue preserved, hard cap at vanilla p90, authored
between-weather variation intact. Applies to Sky-Upper, Fog Near/Far,
Ambient (and therefore DALC), Sunlight, Sun, Stars, Sky-Lower, Horizon;
PNAM cloud tints get the p90 cap only (vanilla medians there are 0);
Effect Lighting (slot 9, no TES4 source) takes vanilla per-time channel
medians instead of black. Verified post-build: every converted slot median
sits exactly on the vanilla median.

### Weather sounds: ambience loops go in AudioCategoryAMB

A 2D LOOPING TES4 sound is an ambience bed. Every converted SNDR used to be
filed under `AudioCategorySFX`, the wrong mix bus — no ambience
slider/ducking — which is why Oblivion's weather winds (authored even on
Clear: two quiet wind beds, where vanilla Skyrim clear weathers are silent)
played loud over everything. 2D+loop now lands in `AudioCategoryAMB`
(0x7F80B), matching every vanilla `AMBWeather*` descriptor; one-shots and 3D
sounds stay SFX.

### Moons: engine-drawn; verified facts and the open question

**Nothing in this pipeline may override Skyrim defaults** — a first attempt
shipped Oblivion's moon textures at the engine's global
`textures\sky\masser_*.dds` paths and was reverted the same day (2026-08-09);
Skyrim's own moons are acceptable.

What is VERIFIED in SkyrimSE.exe (GOG build, create function
0x3ce0a0-0x3ce4fa, called from Sky::Update 0x3caf80):

* Masser is created iff the active climate's byte at +0x7D (the CLMT TNAM
  moons byte) has the SIGN bit (0x80); Secunda iff bit 0x40.  Our climates
  pass 0xC3 through — byte-identical to vanilla SkyrimClimate.
* Creation is otherwise UNCONDITIONAL — same function that loads the climate
  stars model (which demonstrably works: stars render in-game).  No
  worldspace record gate exists in this path; the converted TES4Tamriel WRLD
  is field-for-field equivalent to vanilla Tamriel where sky-relevant
  (DATA flags 0x00 both).
* Phase textures come from the hardcoded template
  `Data/Textures/Sky/%s_%s.dds` (Masser/Secunda x full/three_wax/... — the
  vanilla BSA copies).  Moon visibility params (iMasserSize, fade angles,
  z-offset) are INI settings read once at construction.
* Ruled out as occluders: the converted cloud layers (clear-sky Oblivion
  cloud textures average alpha 16-63/255; JNAM 1.0 matches the 79% of
  vanilla textured layers at 1.0) and the stars dome (structurally identical
  to vanilla: same shapes, extents, BSSkyShaderProperty types and flags).
* Moon PHASE is (GameDaysPassed / 3) % 8 and the `_new` phase is invisible —
  a test save parked in that window shows no moons legitimately.

In-game discrimination if moons are still absent (each step isolates one
layer): (A) vanilla worldspace `coc Riverwood`, `set gamehour to 23` — no
moons here means the install/ini, not the conversion; (B) `cow TES4Tamriel
3 -3`, same hour — moons here mean phase/weather luck earlier; (C) still in
TES4Tamriel force a VANILLA weather `fw 81A` (SkyrimClear) — moons appearing
only now mean OUR WTHR records suppress them (binary-diff next); absent even
under vanilla weather in our worldspace means a climate/worldspace-level
mechanism the create-path disassembly does not show — go dynamic
(game_bridge probes).  `set gamedayspassed to 12` re-rolls the phase.

The four TES5-only slots must NOT be guessed — and they must not be flat
either. **Two successive censuses got this wrong** (2026-08-09): copying the
TES4 Sun/Stars colors into 15/16 made the sky blinding, and the "vanilla
mode is black" correction **hid the moons** — slot 13 Sky Statics tints the
moon discs and is *never black* in vanilla outdoor weathers (SkyrimClear
night is 45,137,208). The mode-black artifact came from collapsing the time
axis and mixing dungeon weathers in, **and the references/Skyrim.esm dump
truncates NAM0 hex before slot 13** — this census must be run against the
real Skyrim.esm (`temp`-style script over the binary, see
`_NAM0_SLOT_CLASS_DEFAULTS`).

The real vanilla shape is per-CLASSIFICATION and per-time (medians):

* **13 Sky Statics** — pale sky-toned colors by day, blues/grays at night;
  never black. Black here = invisible moons (stars are slot 6 and keep
  rendering, which is what localises the symptom).
* **14 Water Multiplier** — NOT flat white: every class ships dark teal
  (31,63,75) at night; white over-brightens night water.
* **15 Sun Glare** — dark browns by day on Pleasant weathers, black at night,
  black all day under cloud/rain/snow.
* **16 Moon Glare** — inverse of 15: Pleasant ships a warm bright halo at
  night (255,173,138); classes whose clouds hide the moons ship black.

The converter keys these off the shared classification bits
(`_nam0_class_defaults`, snow > rain > cloud > pleasant), so an Oblivion
thunderstorm gets Skyrim's storm treatment and a clear night gets the moon
halo.

### RNAM / QNAM cloud speed — a real unit conversion

Both engines cap cloud drift at the same **0.1** units, so the byte must be
rescaled, not copied:

* TES4: UNSIGNED `0..255` scaled by `fWeatherCloudSpeedMax`, whose default
  `0.1` is read straight from `Oblivion.exe` — the settings constructor at
  `0x9E5BF0` does `fld dword ptr [0xA2FAAC]` before pushing the name at
  `0xA56C88`. Always forwards.
* TES5: SIGNED `-0.1 .. +0.1` as `0x00 .. 0xFE`, `0x7F` = 0
  (xEdit `wbWeatherCloudSpeedToStr` = `(v-127)/127/10`, clamped to 254).

So TES4 `b` maps to `127 + round(b/255*127)`. The earlier `0x7F + speed//2` ran
clouds roughly 10x too fast.

### JNAM cloud alpha

Only layers that actually carry a texture may be opaque. A blanket 1.0 across
all 32 layers asks the engine to draw 30 fully-opaque empty layers over the
sky. Vanilla alphas vary 0.0-1.2 per layer/time; TES4 has no per-layer curve,
so the two real layers get 1.0 and the rest 0.0 — and this must stay in sync
with `NAM1` (disabled-layer bitfield).

### NAM1 disabled layers

Disable only the layers with no texture. The original blanket `0xFFFFFFFF`
also disabled layers 0/1 and blanked every converted sky.

### DALC directional ambient (4 x 32 bytes)

TES5 lights the world with a 6-direction ambient cube TES4 has no equivalent
for, so it is derived from NAM0's Ambient. The per-face weights are **measured**
(median of face/Ambient over all 84 vanilla records, every time and channel):

    X+ 0.98   X- 0.94   Y+ 0.96   Y- 0.95   Z+ 0.67   Z- 1.28

**Z+ is the DARKEST face and Z- the brightest** — the opposite of the intuition
that the sky-facing side is brighter. Writing Ambient verbatim into all six
faces and then brightening Z+ overdrove the cube and washed the scene out.
Layout is 6 x RGBA + Specular RGBA + Fresnel f32; Fresnel is 1.0 in every
vanilla record.

### DATA (19 bytes)

TES5 keeps TES4's field order but replaces TES4's two cloud-speed bytes
(offsets 1-2) with padding — speed moved into RNAM/QNAM — and appends four
fields TES4 has no source for. Offsets 6-14 (precipitation/thunder fades,
frequency, classification, lightning color) exist in both and were previously
dropped on the floor.

**Thunder frequency is inverted in BOTH games** (255 = never, 15 = constant),
so it is a genuine passthrough: vanilla Oblivion clear weathers are all 255 and
its thunderstorms are 188/132/100/24. Do not "fix" it.

**Trans Delta and Sun Glare are NOT passthroughs** (2026-08-09 census of the
real Skyrim.esm): TES4 Trans Delta spans 0..255 with median 255, vanilla TES5
ships 125 almost universally — mapped x125/255. TES4 clear weathers author
Sun Glare 255 where vanilla's median is 0, p90 153, max 191 — a raw 255
overdrives the glare pass and paints the sky white toward the sun; mapped
x0.6 (TES4 max -> vanilla p90).

### Required subrecords

`LNAM` (max cloud layers, vanilla is 29 / `0x1D`; 0 allocates none), `MNAM`
(precipitation SPGD) and `NNAM` (visual effect RFCT) are `.SetRequired` in
xEdit. NNAM stays NULL (82 of 84 vanilla weathers ship NULL). The SSE-only
volumetric-lighting `HNAM` is omitted entirely — 0 of 84 records in the
Skyrim.esm dump carry it (form version 40), so its absence is vanilla-legal.

### MNAM precipitation — NULL means a rainstorm with no rain

Oblivion draws rain/snow through hardcoded `Sky\` meshes picked off the
weather's classification bits; Skyrim draws them through the SPGD named in
MNAM (18 of 84 vanilla weathers). So the classification is the authored
source, mapped onto the vanilla Skyrim.esm particle systems
(`_wthr_precipitation`):

| authored TES4 data | SPGD |
|---|---|
| Snow (bit 3) | `SnowParticlesMed` 0x00023C49 |
| Rainy (bit 2) + authored thunder (`ThunderFrequency` < 255) | `RainStormParticles` 0x0010780F |
| Rainy, no thunder | `RainParticles` 0x00023C48 |
| neither | NULL |

ThunderFrequency is inverted in both games (255 = never), and the rainy bit
only exists when DATA was authored, so frequency 0 there is authored
"constant thunder", not a missing field.

### IMSP / HDR tone mapping — the records differ, not just the fields

**This is the field that decides overall scene exposure, and the two games put
it in different records.** Oblivion stores HDR per WEATHER (`WTHR.HNAM`, 14
floats). Skyrim has **no per-weather HDR field at all** — it lives in an
imagespace the weather points at (`WTHR.IMSP` -> `IMGS.HNAM`, 9 floats).

Pointing every converted weather at the stock `0x161`
(`DefaultImageSpaceExterior`) is **not** a valid conversion. `0x161` is one of
only **two** vanilla imagespaces that ship `ENAM` and **no `HNAM`** (the other
is `0x160` Interior); every one of the other 268 has a real HNAM, and 332 of
the 336 `IMSP` references in Skyrim.esm target one of those. With no HNAM the
HDR block is undefined and **the world renders blindingly white at every hour,
including night** — the symptom that survived all the NAM0/DALC fixes.

So each weather mints **four** imagespaces, one per time of day. That is not a
detail: **59 of the 84 vanilla weathers (70%) use distinct imagespaces per
slot**, and day/night differ a lot (`SkyScale` 0.235 day vs 0.02 night on
SkyrimClear). Collapsing all four onto one gives day and night identical tone
mapping.

Calibrate against the **213 imagespaces a vanilla WEATHER actually
references**, not all 268 — the rest are interior/dungeon and pull the
envelope somewhere no outdoor weather sits.

Field correspondence (xEdit `wbDefinitionsTES5` IMGS/HNAM + UESP
`Skyrim Mod:Mod File Format/IMGS`, whose note names slots 5/6 the "target
luminance" pair — exactly TES4's `TargetLum`/`UpperLumClamp`):

| TES5 IMGS.HNAM | <- TES4 WTHR.HNAM | treatment |
|---|---|---|
| 0 Eye Adapt Speed | EyeAdaptSpeed | rescale 0..1 -> 15..50, then per-slot bias |
| 1 Bloom Blur Radius | *(none)* | **constant 7.0** |
| 2 Bloom Threshold | BrightClamp | median-anchored, gain 1.0 |
| 3 Bloom Scale | BrightScale | median-anchored, gain 0.5 |
| 4 Receive Bloom Threshold | TargetLum | median-anchored, gain 0.3 |
| 5 White | UpperLumClamp | median-anchored, gain 0.25 |
| 6 Sunlight Scale | SunlightDimmer | rescale 0.5..2 -> 0.9..2.7, per-slot bias |
| 7 Sky Scale | *(none)* | own sky luminance, clamped to slot p10..p90 |
| 8 Eye Adapt Strength | *(none)* | vanilla per-slot 15/5/15/20 |

**Shared field names do not mean shared ranges — and rescaling SPANS is not
enough either.** The first calibration mapped each TES4 field's observed span
onto the vanilla span, but the TES4 *medians sit at the edge of their spans*
(median UpperLumClamp is 1.0, the bottom of 1.0..1.3; median TargetLum is
1.2, the very top of 0.75..1.2), so nearly every weather landed at the edge
of the vanilla range too: White = 0.88 — vanilla's bottom decile, a lowered
white point — with bloom threshold and receive-bloom simultaneously mapped
bloom-heavy. In-game: the day looked like an overexposed camera (confirmed
2026-08-09; nights were fine).

The fix is **median anchoring** (`_IMGS_ANCHORED_FIELDS`); a derived remap was
tried and rejected in game, see [below](#hdr-remap-rejected):

    value = vanilla_slot_median + (tes4 - tes4_median) * gain,
    clamped to the vanilla p10..p90 band for that slot

so a median TES4 weather renders exactly base-Skyrim and authored deviation
moves it modestly within the vanilla band. TES4 medians (36 authored
Oblivion.esm HNAMs; Nehrim agrees, its outliers — SunlightDimmer 50,
TargetLum 5.2 — are what the p10..p90 clamp is for): TargetLum 1.2,
UpperLumClamp 1.0, BrightScale 2.0, BrightClamp 0.3. Sky Scale keeps the
luminance ramp but clamps to the slot band (night p90 = 0.13) — Oblivion
authors bright night skies and an uncapped ramp gave night a near-daytime
sky exposure.

`FNAM` fog power/max are also NOT xEdit's 1.0 defaults: vanilla ships
medians 0.4/0.4 power and 0.9/0.925 max. Max 1.0 lets fog reach full opacity
at the horizon and paints it with Oblivion's pale fog color — a big part of
the blown-white horizon.

`BloomBlurRadius` is **7.0 in all 213** weather-used imagespaces — an engine
constant, not authored data. TES4's `BlurRadius` (4..8) belongs to Oblivion's
own blur pass and merely shares a name; feeding it through made the bloom
kernel too tight.

`EyeAdaptSpeed` is a genuine **unit change**: Oblivion's is a 0..1 rate
(`fEyeAdaptSpeed:BlurShaderHDR`, default 0.7 — in Oblivion.ini and named in
Oblivion.exe at `0xA3E965`), while Skyrim's weather-used range is **15..50**.

`SkyScale` is the sky's contribution to scene exposure and TES4 has **no
equivalent field**. It tracks the weather's own sky brightness (corr +0.29 over
284 vanilla weather/slot pairs): a dark night sky sits at ~0.025, any lit sky
at ~0.20. A flat value washes the day sky out to near-white while
over-lighting the night, so it is ramped from the weather's Sky-Upper
luminance at that time of day.

Every output field is finally **clamped to the per-field min/max of those 213
imagespaces**. And a weather whose entire TES4 HNAM is zero has no authored HDR
at all — `DefaultWeather` is one, and it is exactly the weather the 57
CNAM-less worldspaces fall back to. Copied verbatim it gives `White=0` and
`SunlightScale=0` (a zero white point); clamping those zeros to the range
*minimum* instead pins every field at its flattest legal value. Neither is
right, so an unauthored record takes the vanilla per-slot defaults.

Each minted IMGS also ships **DNAM** — 213 of the 214 weather-used vanilla
imagespaces carry it, and omitting it leaves depth-of-field and SKY BLUR
undefined (part of the "everything is bloomy/blurry" symptom). Vanilla
medians: Strength 0.5, Distance 0, Range 10000, and the modal Sky/Blur enum
16920 = "No Sky, Radius 4" — the sky is explicitly EXCLUDED from the blur
pass.

Because WTHR mints companion records, it is **not** in the generic dispatch;
it runs in its own serial phase (`import_main` Phase 2b) so record order stays
deterministic. Group order is `IMGS -> WTHR -> CLMT`, matching the reference
direction.

### EditorID collision

Oblivion and Skyrim both ship a `DefaultWeather`. After remapping, ours
collides with Skyrim's `0x0000015E` and the CK renames it
`DefaultWeatherDUPLICATE001` — so it is prefixed to `TES4DefaultWeather`.

## Vanilla-substitution map (Oblivion WTHR -> vanilla Skyrim WTHR)
<a id="vanilla-substitution-map"></a>

For a converter option that **ships vanilla Skyrim weathers instead of the
converted Oblivion ones**, sidestepping the whole NAM0-normalization /
IMGS-minting path. The map below is derived from the authored discriminators
on both sides — `DATA.Classification`, `WindSpeed`, `ThunderFrequency`,
`SunGlare`, and the `FNAM` fog distances — read from
`export/Oblivion.esm/WTHR.txt` and the real `references/Skyrim.esm/WTHR.txt`
(84 vanilla records). Only genuinely close matches are listed; everything else
is deliberately absent and must keep the converted record.

**The classification flags byte is at DATA offset 11 in BOTH games** (xEdit
`wbDefinitionsTES4.pas:3778` / `wbDefinitionsTES5.pas:10656`) — bit 0
Pleasant, 1 Cloudy, 2 Rainy, 3 Snow. TES5 adds bits 4/5 (Aurora) that TES4 has
no source for. Reading it anywhere else yields 0 for all 84 vanilla records,
which looks like "vanilla never sets classification" and is wrong.

### Close matches — safe to substitute

| Oblivion WTHR | class / wind / thunder / fogFar | vanilla Skyrim WTHR | FormID | why it matches |
|---|---|---|---|---|
| `Clear` | Pleasant, 25, never, 170000 | `SkyrimClear` | `0x0000081A` | identical class, **identical wind 25**, no thunder; both the climate's fair-weather default |
| `SEClear` / `SEClear01` / `SEClear03` / `SEClearBlue` / `TestBlissClear` | Pleasant, 25, never | `SkyrimClear` | `0x0000081A` | DATA identical to `Clear` (SI reskins the colors only) |
| `SEClearTrans` | Pleasant, 25, never | `SkyrimClear` | `0x0000081A` | identical to `Clear` except `TransDelta` 0 vs 255 — an instant-transition variant; TransDelta is remapped x125/255 anyway |
| `Cloudy` | Cloudy, 37, never, 150000 | `SkyrimCloudy` | `0x00012F89` | **wind 37 vs 38**, no precipitation; note vanilla `SkyrimCloudy` is flagged *Pleasant*, not Cloudy — it is Skyrim's partly-cloudy fair weather, which is what Oblivion's `Cloudy` is |
| `SECloudy` / `SEManiaFog` | Cloudy, 37, never | `SkyrimCloudy` | `0x00012F89` | same DATA as `Cloudy` |
| `Overcast` | Cloudy, 19, never, 150000 | `SkyrimOvercastWar` | `0x000D299E` | the only vanilla *Cloudy*-flagged overcast with no precipitation; wind 19 vs 89 is the weak point |
| `SEOvercast` | Cloudy, 19, never, 80000 | `SkyrimOvercastWar` | `0x000D299E` | DATA identical to `Overcast` |
| `Fog` | Cloudy, 8, never, **4096** | `RiftenOvercastFog` | `0x0010FE7E` | Cloudy on both sides; vanilla's tightest fog (dayFar/nightFar both 9000) against Oblivion's 4096. `SkyrimFog` dayFar 25000 is far too open |
| `SEFog` / `SEOrderedFringe` / `SE13JiggyWeather` | Cloudy, 8, never, 8000 | `SkyrimFog` | `0x000C821E` | fogFar 8000 sits between vanilla's 25000/9000; `SkyrimFog` is the general-purpose one |
| `Rain` | Rainy, 14, **never**, 10000 | `SkyrimOvercastRain` | `0x000C821F` | rain with **no thunder** on both sides — the exact vanilla rain/storm split |
| `SERain` | Rainy, 14, never, 10000 | `SkyrimOvercastRain` | `0x000C821F` | same DATA as `Rain` |
| `Thunderstorm` | Rainy, 81, **188**, 6000 | `SkyrimStormRain` | `0x000C8220` | both authored rain **with** thunder; vanilla freq 246 vs 188 (more frequent in Oblivion) |
| `SEThunderstorm` | Rainy, 81, 188, 6000 | `SkyrimStormRain` | `0x000C8220` | DATA identical to `Thunderstorm` |
| `ThunderstormKvatch` | Rainy, 81, 188, 6000 | `SkyrimStormRain` | `0x000C8220` | identical to `Thunderstorm` but for lightning color (236,240,253 vs 245,248,254) — substituting loses that tint |
| `SE32GloomStorm` | Rainy, 81, **24**, 3000 | `SkyrimStormRain` | `0x000C8220` | near-constant thunder; closest vanilla storm, though gloomier than any |
| `Snow` | Snow, 7, never, 12000 | `SkyrimOvercastSnow` | `0x0004D7FB` | calm snowfall, no thunder; vanilla wind 76 vs 7 is the gap (`SkyrimStormSnow` wind 178 + thunder is much further off) |
| `SETestAsh` | Snow, 14, never, 10000 | `SkyrimOvercastSnow` | `0x0004D7FB` | uses the Snow bit for ashfall; vanilla snow is the nearest particle system |
| `SEWaitingRoomWeather` | **Snow**, 19, never, 80000 | `SkyrimOvercastSnow` | `0x0004D7FB` | shares `Overcast`'s DATA except the classification, which is Snow (8) not Cloudy — the flag is what drives precipitation, so it maps with the snows |

Reference: `SkyrimStormSnow` `0x000C8221` (Snow, wind 178, thunder 203) is the
vanilla blizzard. **No Oblivion weather matches it** — Oblivion authors no
thundering snow — so it is listed for completeness but is not a substitution
target.

## Sunless skies — the Oblivion realms must have no sun
<a id="sunless-skies-oblivion-realms-must"></a>

**Oblivion says "this sky has no sun" with the CLMT sun SPRITE alone.** The
realm climates set `FNAM=Sky\Void.dds` / `GNAM=Sky\VoidGlare.dds`, and
Oblivion's `Sun` class (vtable `0x00a571dc`) draws only that sprite, so voiding
it is sufficient there.

**It is not sufficient in Skyrim.** Skyrim's sun is three separate things:

| piece | fed by |
|---|---|
| the disc sprite | CLMT `FNAM` |
| a **directional light** that visibly tracks the sun | `WTHR` NAM0 **slot 4** (Sunlight) |
| a **glare/bloom pass** | `WTHR` NAM0 **slot 15** (Sun Glare) |

Passing the Void textures through therefore leaves a tracking light and a glare
burning over the Deadlands — the symptom reported in game as "the realms have a
sun and a day/night cycle."

### Vanilla is the reference: `BlackreachClimate`

Skyrim.esm's one sunless outdoor sky solves exactly this. Measured from
`references/Skyrim.esm`:

```
BlackreachClimate   FNAM=Black.dds   GNAM=Black.dds      <- NOT Sky\Void.dds,
                                                            and the GLARE is
                                                            black too
BlackreachWeather   NAM0 slot 4 Sunlight = (0,0,0) x4    <- kills the light
                    NAM0 slot 5 Sun      = (0,0,0) x3, (128,128,128) night
```

`Black.dds` is a **vanilla** path (confirmed present in the SSE BSAs at 1540
bytes via `skyrim_assets.get_asset_bytes('textures/black.dds')`), so it must
**not** take the `tes4\` prefix.

### What the converter does

`_clmt_is_sunless()` in `dialog_misc.py` detects the authored indicator — FNAM
naming a void sprite, **or no FNAM at all** (`ClimateSigil` and
`MQ14OblivionClimate` author none, which in Oblivion also draws nothing). This
is derived from authored data, not a hardcoded EditorID list, so it carries to
arbitrary plugins. Measured over `Oblivion.esm`: **4 climates by FNAM + 2 by
absent FNAM → 6 weathers** (`Obliviondefault`, `OblivionElectrical`,
`OblivionStormOblivion`, `OblivionMountainFog`, `OblivionStormTamrielMQ16`,
`OblivionSigil`).

`import_main` seeds `_SUNLESS_WEATHER_FIDS` from the plugin's climates **and the
master export** before Phase 1 — an override plugin's climates take the
`ctx.build()` short-circuit and never reach `convert_CLMT`
([master blindness](../../CLAUDE.md#master-blindness)). Then:

1. **CLMT** → `FNAM`/`GNAM` both become `Black.dds`, unprefixed.
2. **WTHR NAM0 slot 5 (Sun) and slot 15 (Sun Glare)** → hard zero.
3. **WTHR NAM0 slot 4 (Sunlight)** → zeroed, but **half its value is folded
   into slot 3 (Ambient)**.
4. **WTHR DALC** → the same fold, on every face. DALC lights *objects*, and
   `_wthr_dalc` derives from the TES4 Ambient independently of NAM0, so
   without this the sky glows red while everything under it stays lit only by
   the dim TES4 ambient. Vanilla confirms the coupling: BlackreachWeather's
   DALC faces track its NAM0 Ambient ~1:1 (`10,11,12` → `11,11,12`, only Z-
   boosted for the mushroom uplight), i.e. vanilla does **not** compensate
   DALC separately for a missing sun — it authors Ambient at the level it
   wants and lets DALC follow.

**Why the fold, and why this differs from Blackreach.** Blackreach is a cave
lit purely by ambient, so vanilla zeroes its Sunlight outright. The Deadlands
are lit by a warm red fill that Oblivion authors *in Sunlight* while the sprite
is voided — in Oblivion those are independent; in Skyrim slot 4 drives the
tracking light. Zeroing it outright would black out the realm's red glow (the
authored values are substantial: `Obliviondefault` 193,130,87). Half, because
the directional light lit roughly the half of each surface facing it while
ambient lights all of it. That the authors used a literal `(0,0,0)` Sunlight on
`OblivionSigil` — the one realm weather with no fill — confirms zero is their
"no light" value, so a non-zero value there is a deliberate fill worth keeping.

**No DATA change is needed:** all six realm weathers already author
`SunGlare=0` and `SunDamage=0`, which pass through correctly.

**Every realm weather authors its four time slots identically** — Oblivion's way
of encoding "no day/night cycle" — so the transform is time-invariant by
construction. Note the realm climates still author real `TNAM` timings
(`Obliviondefaultclimate` 23/42→102/127), i.e. vanilla Oblivion *does* run a
clock in the Deadlands; the perpetual gloom comes from the void sun plus the
weather art, not from stopping time.

Regression tests: `TestSunlessSkies` in `tests/test_import.py`. This is record
content only — no records minted, so **no FormID drift**.

### Deliberately NOT mapped — keep the converted record

These have no vanilla counterpart, and substituting the nearest-looking one
would replace authored art direction with something visually unrelated:

* **Oblivion-realm skies** — `OblivionStormTamriel`, `OblivionStormTamrielMQ16`,
  `OblivionStormOblivion`, `OblivionElectrical`, `OblivionMountainFog`,
  `OblivionSigil`, `Obliviondefault`, `SigilWhiteOut`. The red/black Deadlands
  sky is the single most recognisable weather in the game and vanilla Skyrim
  has nothing near it. `OblivionStormTamriel` is also the weather the gate
  scripts force (see *Scripted weather* above), so substituting it changes what
  `ForceActive` puts over the sky.
* **Quest/set-piece skies** — `CamoranWeather`, `MS14Sky`,
  `SE09SummoningWeather`. Authored for one scene.
* **`DefaultWeather`** — the fallback for all 57 CNAM-less worldspaces, and
  already special-cased (`TES4DefaultWeather`, EditorID collision above). Its
  TES4 HNAM is all-zero, so it takes the vanilla per-slot IMGS defaults
  regardless.

### What substitution has to redirect

A vanilla weather is referenced, never copied, so the option is a **FormID
redirect at every referrer**, not a skip in Phase 2b. Four referrers exist:

1. `CLMT.WLST` — climate weather lists (`convert_CLMT`)
2. `REGN.RDWT` — region weather lists (`convert_REGN`), where Cyrodiil's actual
   variety lives
3. Script `Weather` properties — auto-bound **by EditorID**
   (`script_convert/converter.py`), so a substituted weather must resolve to
   the vanilla EditorID or the property silently fails to bind
4. `WTHR` itself — the record is not emitted, and **its four IMGS companions
   must not be minted**

Point 4 costs nothing in FormIDs: companion ids are
[hashed from the source weather](../../CLAUDE.md#formid-drift), so skipping a
weather's IMGS leaves every other record's id untouched. Toggling the option
still changes which records exist, so it remains a new-game-only setting,
exactly like enabling weather conversion itself.

## REGN weather — where Cyrodiil's weather variety actually lives
<a id="regn-weather-where-cyrodiils-weather"></a>

**TamrielClimate's WLST is a single Clear weather at 100%.** All the rain,
snow and fog variety in Oblivion comes from **59 region weather lists** (RDAT
type 3 + RDWT) layered over the climate — the identical mechanism Skyrim uses
for its own coasts and holds (`WeatherCoastFog`, `WeatherWinterhold`, ...).
Converting the climate chain without regions gives a permanently clear
Cyrodiil.

- `tes4_export/record_types/world.py::export_REGN` walks subrecords **in
  order** (each RDWT belongs to the RDAT before it, each RPLD to its RPLI)
  and dumps areas as raw hex — the TES5 RPLD layout is byte-identical
  (x,y float pairs, world units unchanged).
- `tes5_import/record_types/world.py::convert_REGN` emits EDID, RCLR, WNAM,
  the RPLI/RPLD areas, and ONLY the weather RDAT entries; RDWT entries widen
  8 → 12 bytes with a trailing null Global FormID (same widening as CLMT's
  WLST). RDAT's Override/Priority bytes pass through (vanilla Skyrim weather
  regions use exactly this: Override=1 Priority=95 etc.).
- A region with no weather list, or no area polygon to apply it in, emits
  nothing (`convert_REGN` returns None; the generic dispatch skips falsy
  results). Object/grass/sound/map generators stay dropped.
- **The RPLD polygons alone do NOTHING at runtime — region weather reaches
  the sky through the CELL's XCLR region list.** Skyrim.esm puts
  WeatherWinterhold in 30 cells' XCLR and WeatherCoastFog in 51. Without
  XCLR every converted exterior fell back to the climate WLST (TamrielClimate
  = single Clear at 100%) and the sky never changed. `convert_CELL` now
  writes XCLR, filtered against the regions `convert_REGN` actually emitted
  (`_EMITTED_REGION_FIDS`, reset per import) and sorted (xEdit `wbArrayS`).
  Oblivion.esm: 8,501 exterior cells carry XCLR, 0 dangling refs.
  Known limit: a master-dependent ESP's cells only keep refs to its OWN
  emitted regions, not the master's.

## <a id="morrowind-weather-types"></a>Morrowind's ten weather types without Morroblivion

**Code:** `tes5_import/record_types/weather_morrowind.py`,
`tes4_export/morrowind_region.py`

TES3 has no weather records, only ten hardcoded types a region's `WEAT`
gives a chance each. Morroblivion converted all ten as WTHR, and the export
names those by EditorID when a master holds them. Authored mode
(Morrowind/Tribunal/Bloodmoon with no Morroblivion) has none, so every
region used to be dropped and Vvardenfell and Mournhold had one sky. Now the
export writes such a weather as `Weather[i].MorrowindType=<type>` and the
import places each type on the vanilla weather a census picked from
`references/Skyrim.esm/WTHR.txt` and a dump of Dragonborn.esm. The census
read each WTHR's `DATA` classification byte (offset 11: 01 pleasant, 02
cloudy, 04 rainy, 08 snow) and its precipitation shader (`MNAM`):

| TES3 type | Stand-in | Class, precipitation |
|---|---|---|
| 0 Clear | `SkyrimClear` 0000081A | pleasant, none |
| 1 Cloudy | `SkyrimCloudy` 00012F89 | pleasant, none |
| 2 Foggy | `SkyrimFog` 000C821E | cloudy, none |
| 3 Overcast | `SkyrimOvercastWar` 000D299E | cloudy, none (the only overcast without rain) |
| 4 Rain | `SkyrimOvercastRain` 000C821F | rainy |
| 5 Thunderstorm | `SkyrimStormRain` 000C8220 | rainy |
| 6 Ashstorm | `DLC02VolcanicAshStorm01` (Dragonborn 032336) | snow class, ash particles |
| 7 Blight | `DLC02VolcanicAshStorm01` (the same) | Skyrim has no second ash storm |
| 8 Snow | `SkyrimOvercastSnow` 0004D7FB | snow |
| 9 Blizzard | `SkyrimStormSnow` 000C8221 | snow |

Skyrim.esm has no ash weather at all; Dragonborn's nine WTHR hold the only
ash storm. So a plugin whose regions name Ashstorm or Blight by type gets
Dragonborn.esm in its master list, right after Skyrim.esm
(`with_weather_masters`, decided from `REGN.txt` before the master list is
opened); `reconcile_masters` keeps every vanilla master ahead of the TES4
ones. Every Special Edition install has Dragonborn.esm. A plugin whose
regions name no ash keeps Skyrim.esm alone.

The same ten forms go into the sidecar as `weather0`..`weather9` rows of
`effects_formid.txt` (Morroblivion's WTHR when converted), which is how
MorrowindRuntime's `ChangeWeather`, `ModRegion` and `GetCurrentWeather`
name a weather. See
[morrowind_runtime.md](morrowind_runtime.md#region-weather).

## Scripted weather (ForceWeather / SetWeather / ReleaseWeatherOverride)
<a id="scripted-weather"></a>

The Oblivion-gate scripts (`MQ10/11/13/16`, `MS48/94`, the random gates) hold
`OblivionStormTamriel` over the sky while the player is near a gate, and SE
quests force their own skies. These were no-op stubs while the chain was
gated; now (`script_convert/converter.py`, signatures verified against
`references/skse64-master/scripts/vanilla/Weather.psc`):

| TES4 | Papyrus |
|---|---|
| `ForceWeather X [flag]` / `fw` | `X.ForceActive(False)` (instant switch) |
| `SetWeather X [flag]` / `sw` | `X.SetActive(False, False)` (natural transition) |
| `ReleaseWeatherOverride` | `Weather.ReleaseOverride()` |
| `GetIsCurrentWeather X` | `(Weather.GetCurrentWeather() == X)` |
| `GetCurrentWeatherPercent` | `Weather.GetCurrentWeatherTransition()` (both 0..1 — the old constant-50 stub assumed 0..100) |

**abOverride must be FALSE on both** (2026-08-09, confirmed in-game the hard
way).  Oblivion holds scripted weather by CONTINUOUS RE-APPLICATION — the
gate scripts re-force the storm every GameMode pass while the player is near
— not by an engine lock, and its scripts stop running when the ref unloads.
Skyrim's abOverride=True is a GLOBAL lock that survives the caller
unloading, so the first mapping (True) let a fast-travel away from an open
Oblivion gate strand `OblivionStormTamriel` over the whole world FOREVER:
the `ReleaseWeatherOverride` lives in the same script's update loop, which
stopped the moment the gate unloaded.  With False the converted loops keep
the storm applied while they run (same look near the gate) and the
region/climate system reclaims the sky on its next re-roll once they stop.
A save stuck this way is cleared from the console: `cf "Weather.ReleaseOverride"`.

The weather argument is registered as a `Weather` property and auto-binds to
the converted WTHR by EditorID (no Oblivion script references
`DefaultWeather`, the one renamed EditorID).

## Sky meshes need BSSkyShaderProperty
<a id="sky-meshes-need-bsskyshaderproperty"></a>

Skyrim draws sky through a **dedicated pass** keyed on
`BSSkyShaderProperty.Sky Object Type`. A sky mesh carrying
`BSLightingShaderProperty` is lit, fogged and depth-sorted as ordinary world
geometry — which is why converted stars drew in front of the landscape.

`SkyObjectType` (references/nif 0.10.0.0.xml): 0 TEXTURE, 1 SUNGLARE, 2 SKY,
3 CLOUDS, 5 STARS, 7 MOON_STARS_MASK. Confirmed against shipped assets:
`sky/stars.nif` is type 5 throughout, `sky/clouds.nif` is type 3.

`nif_converter.sky_object_type_for()` maps Oblivion's `Sky/` meshes to a type.
Oblivion had no such enum — it identified sky geometry by which climate/weather
slot referenced it — so the table is the bridge between the two models and
cannot be derived from the NIF. It keys on the `sky/` **directory**, not the
basename alone (`architecture/sky/wall.nif` must not match).

Two further contracts, both verified against vanilla `sky/stars.nif`:

* **Root stays a plain `NiNode`**, not `BSFadeNode`. Fade nodes apply
  distance-based fading, meaningless for a dome always drawn around the camera.
* **No `NiAlphaProperty`.** The sky pass does its own blending; vanilla sky
  meshes carry none, and keeping Oblivion's makes the layer vanish or z-fight.

Precipitation meshes (`rainheavy`, `rainlight`, `snow`) are particle/world
geometry and correctly keep their effect/lighting shaders.

## Verifying
<a id="verifying"></a>

    python -m pytest tests/test_import.py -k "Weather or Climate or WorldspaceClimate or SkyMesh or RegionWeather"
    python -m pytest tests/test_script_converter.py -k "Weather"

After a full import of Oblivion.esm, the chain should show 37 WTHR, 148 IMGS
(4 per weather), 19 CLMT, ~57 weather REGN, ~8,500 exterior cells with XCLR
(zero dangling), zero dangling `WLST -> WTHR` / `RDWT -> WTHR`, and zero
worldspaces missing `CNAM`.

**FormID drift warning:** Phase 2b allocates 148 IMGS FormIDs before every
later companion-allocating phase (SNDR, SOPM, ...), so enabling or disabling
weather conversion shifts all subsequently allocated FormIDs and breaks
existing saves.

---

## First-principles engine comparison (2026-08-24) — what each engine DOES with weather data
<a id="first-principles-engine-comparison-what"></a>

Prompted by persistent sky bloom that survived every clamping heuristic. The
question asked was not "what value looks right" but "what does each engine
compute". Sources: `Oblivion.exe` / `SkyrimSE.exe` (GOG-AE) strings +
disassembly, the decompiled `TESWeather` in TES-Reloaded
(`github.com/mcstfuerson/TES-Reloaded`, `TESReloaded/Framework/Game.h` — it
carries a separate struct for EACH game, so it is a direct A/B), the shipped
`Atmosphere.nif` from both games, `Oblivion_default.ini`, and measurements over
all 37 converted weathers vs all 84 vanilla Skyrim weathers.

### What is IDENTICAL between the engines (do not "fix" these)

| Thing | Evidence |
|---|---|
| Sky dome mesh + role | Both load `Meshes\Sky\Atmosphere.nif` and `Meshes\Sky\Clouds.nif` (hardcoded strings; Oblivion 0x655d88/0x655e64, Skyrim 0x169a348/0x169a538) |
| Vertex-color gradient convention | Both domes carry R=horizon, G=mid, B=upper as BLEND MASKS summing to ~1.0, low-z to high-z. Measured on both meshes. |
| Times of day | Both are (Sunrise, Day, Sunset, Night) in that order — only the TYPE axis needs remapping |
| Color slot mapping | `_NAM0_TES5_FROM_TES4` matches the decompiled enums in both games. Verified, not inferred. |

The one structural difference in the meshes is the shader property: Oblivion's
Atmosphere uses a fixed-function `NiMaterialProperty`; Skyrim's uses
`BSSkyShaderProperty` with `sky_object_type` (Atmosphere=2 `BSSM_SKY`,
Clouds=3 `BSSM_SKY_CLOUDS`). This does not change how NAM0 is consumed.

### Oblivion's per-weather HDR block, fully decoded

`WTHR.HNAM` is 14 floats. TES-Reloaded names them and the CS wiki gives the
semantics (`cs.uesp.net/wiki/HDR_Settings` — 403s to WebFetch, reachable via
`tools/misc/uesp_lookup.py`):

```
0 EyeAdaptSpeed  1 BlurRadius   2 BlurPasses    3 EmissiveMult
4 TargetLUM      5 UpperLUMClamp 6 BrightScale  7 BrightClamp
8 LumRampNoTex   9 LumRampMin   10 LumRampMax   11 SunlightDimmer
12 GrassDimmer   13 TreeDimmer
```

`BrightScale` = scene brightness (bigger = brighter + MORE bloom).
`BrightClamp` = scene dimmer (bigger = darker + LESS bloom).

Oblivion also exposes a whole `[BlurShaderHDR]` INI family that **Skyrim has no
equivalent for at all** — every string in it is Oblivion-only (verified by
diffing the string tables of both exes). Notably `fSkyBrightness=0.5`,
`fSunBrightness=0.0`, `fSunlightDimmer=1.3`.

### Theories TESTED AND REJECTED — do not retry these

Each was a plausible global-scalar story; each was refuted by measurement.
Recorded so no future session burns a cycle re-deriving them.

| Theory | Test | Result |
|---|---|---|
| Oblivion authors bright colors because `BrightScale` dims them; divide by it | median vs vanilla | Moves the median but **raises** per-weather scatter (Horizon Day cv 0.63 -> 0.74; vanilla is 0.69). A real engine term TIGHTENS the population. Rejected. |
| `/sqrt(BrightScale)` (medians landed at 0.97-1.21) | scatter | Same scatter failure. The median match was coincidence. Rejected. |
| `fSkyBrightness=0.5` is a straight multiplier on NAM0 | `x0.5` vs vanilla | **Worse than raw in every category** (log2 err: sky dome 0.396 -> 0.710; lighting 0.416 -> 1.083). It is a blur-shader input, not a NAM0 scale. Rejected. |
| The cloud tint slots are misrouted | our OUTPUT vs vanilla | Both write ~0 in slots 10/11 (vanilla is 98-100% zero there). Cloud tint is `PNAM`, and ours is present and in range. Not a bug. |

Note the trap in the third row: the raw TES4 input DOES have 37/37 weathers
above vanilla's max in slots 10/11, but `set_nam0_normalization()` already
zeroes them. **Always compare `output/`, never the export, when judging what
the engine sees.**

### The actual defect: Sky Scale is DERIVED from sky color (feedback loop)

Our shipped NAM0 is fine. Measured `output/Oblivion.esm` vs `Skyrim.esm`,
per slot and time — sky-dome ratios and the count over vanilla's max:

```
SkyUpper  1.01 / 1.09 / 1.07 / 1.14      0 weathers over vanilla max
SkyLower  1.05 / 1.01 / 1.02 / 1.18      0
Horizon   1.14 / 1.35 / 1.05 / 1.06      0
FogNear   1.14 / 1.04 / 1.12 / 1.09      0
```

Our minted IMGS is also fine — **zero** HNAM fields above vanilla's max, all
nine medians within 0.97-1.36 of vanilla's.

Both marginals are in range, so the defect is in the JOINT distribution:

```
                          vanilla r    ours r
SkyUpper vs SkyScale        +0.407     +0.885
Horizon  vs SkyScale        +0.374     +0.819
FogNear  vs SkyScale        +0.577     +0.714
```

`_wthr_imgs()` computes `sky_scale` by ramping off `_sky_luminance(rec, time)`.
Sky Scale is a **tonemapper multiplier applied to the sky**
(`Skyrim Mod:Mod File Format/IMGS` field 7; the console's `HDR` readout prints
it next to Bloom Scale). Deriving it from sky brightness squares the brightness
signal: bright sky -> bigger multiplier -> bloom. No per-field range check can
see this, which is why every clamping pass missed it.

This independently confirms the user's own A/B result: `CANDskyscale` carried
the bloom and `FIXcolortone` looked clean.

### What vanilla actually keys Sky Scale off

Variance explained over the 332 vanilla weather/time rows:

```
classification x time lookup    R2 = 0.434
linear on sky luminance         R2 = 0.166   <- what we currently do
```

Vanilla's median Sky Scale by classification x time:

| class | Sunrise | Day | Sunset | Night |
|---|---|---|---|---|
| Pleasant | 0.175 | 0.250 | 0.200 | 0.020 |
| Cloudy | 0.192 | 0.375 | 0.220 | 0.040 |
| Rainy | 0.180 | 0.200 | 0.210 | 0.070 |
| Snow | 0.172 | 0.165 | 0.155 | 0.076 |
| Unclassified | 0.050 | 0.050 | 0.050 | 0.050 |

Night collapses to 0.02-0.076 across the board — vanilla nearly switches the
sky tonemap off at night. Both inputs (`DATA.Classification`, time of day) are
AUTHORED TES4 data, so this generalises to plugins we have never seen.

### Other measured gaps (NOT bloom, but wrong)

* **Stars is bimodal in vanilla, not continuous.** Pleasant writes
  `(255,255,255)` (33 of 36); Cloudy/Rainy/Snow write `(0,0,0)` (mode in all
  three). Ours writes a continuous ramp, median 222.7 vs vanilla 87.2 — we
  never hide the stars for overcast weather.
* **SunGlare (slot 15) and MoonGlare (slot 16) are written BLACK** in all 37.
  TES4 has no source slot, but 0 is a COLOR the engine multiplies the glare
  sprite by, not "off". Vanilla Pleasant authors SunGlare `(74,28,0)` at
  dawn/dusk and MoonGlare `(255,175,128)` at night; Cloudy/Rainy/Snow do use 0.
* **Ambient.Day median 172.2 vs vanilla 100.1 (1.72x)** — the only sky-adjacent
  slot whose median is far off. Ours p25=133 vs vanilla p25=54.

### Rule for future work here

Sky color is NOT the bloom lever and has already been over-corrected. Before
adding any further clamp to NAM0, measure `output/` against `Skyrim.esm` and
check the JOINT relation against the tonemapper, not the marginal range.

---

## The WTHR record, field by field — what the ENGINE DOES with each value (2026-08-24)
<a id="wthr-record-field-field-what"></a>

Written because repeated attempts to fix the sky by matching vanilla's numeric
*distributions* failed. Matching statistics is exactly what forces "reduce
everything toward the average", and it cannot tell you what a value MEANS. This
section records the CONSUMPTION MODEL instead: the real shader algorithm, and
what each field feeds.

**Sources.** Layout: `references/xEdit/Core/wbDefinitionsTES4.pas:3755`,
`wbDefinitionsTES5.pas:10623`, and the `wbWeather*` helpers in
`wbDefinitionsCommon.pas`. Structs: the decompiled `TESWeather`/`Sky`/`Clouds`
in TES-Reloaded `Framework/Game.h` — it carries a SEPARATE struct per game
(Oblivion at line 5809, Skyrim at 10219), so it is a direct A/B. Per-frame
math: TES-Reloaded `Core/ShaderManager.cpp`. **Pixel math: the actual sky
shader**, `doodlum/skyrim-community-shaders` `package/Shaders/Sky.hlsl`, plus
the constant-setup in `Nukem9/skyrimse-test` `BSShader/Shaders/BSSkyShader.cpp`.

### THE SKY ALGORITHM (exact)

Vertex shader, non-textured sky (the Atmosphere dome, `SKY` technique):

```hlsl
float3 skyColor = BlendColor[0].xyz * input.Color.x     // vertex R -> stop 0
                + BlendColor[1].xyz * input.Color.y     // vertex G -> stop 1
                + BlendColor[2].xyz * input.Color.z;    // vertex B -> stop 2
vsout.Color.xyz = VParams * skyColor;
vsout.Color.w   = BlendColor[0].w * input.Color.w;
```

Pixel shader (`Color::Sky` is IDENTITY in vanilla — it is a Community Shaders
linear-lighting hook, `pow(color, skyGamma)` only when that feature is on):

```hlsl
psout.Color.xyz = input.Color.xyz * baseColor.xyz + skyScale;
psout.Color.w   = input.Color.w   * baseColor.w;
```

So the complete vanilla sky pixel is:

```
pixel = (Σ_stop BlendColor[stop] * vertexMask[stop]) * VParams * texture + skyScale
```

Four facts follow, and every one of them is a MECHANISM, not a statistic:

1. **The dome's vertex colors are BLEND MASKS, not colors.** R/G/B select
   among three color stops and sum to ~1.0. Measured on both games' shipped
   `Atmosphere.nif`: R=1 at the bottom ring, G=1 mid, B=1 at the zenith. The
   sky gradient is a 3-stop interpolation evaluated per-vertex.
2. **The texture MULTIPLIES the color.** `input.Color * baseColor`. A white
   cloud texel shows the weather color unchanged; a black texel shows nothing.
   Cloud sheets are therefore MASKS tinted by PNAM, not pictures.
3. **`skyScale` is ADDITIVE, applied after the multiply.** From
   `BSSkyShader.cpp`, `PParams.y = fInvFrameBufferRange * [sky+0xE4]`, and it is
   forced to **0.0 for MOON and SUNGLARE**. An additive term cannot be undone by
   any texture or alpha — it is a floor on sky brightness, which is precisely
   why a too-large sky scale reads as an inextinguishable glow.
4. **`VParams` is a global exposure** (`fInvFrameBufferRange`), not per-weather.

Technique -> defines (`BSSkyShader.cpp`), i.e. which code path each sky object
takes: SKY=`DITHER`, CLOUDS=`TEX,CLOUDS`, CLOUDSLERP=`TEX,CLOUDS,TEXLERP`,
CLOUDSFADE=`TEX,CLOUDS,TEXFADE`, TEXTURE=`TEX`, SUNGLARE=`TEX,DITHER`,
MOONANDSTARSMASK=`TEX,MOONMASK`, STARS=`HORIZFADE`, SUNOCCLUDE=`OCCLUSION`.

`STARS` uses `HORIZFADE`: `saturate(eyeHeightDelta / 17.0)` — stars fade in by
the camera's height above the star mesh, and the whole thing is multiplied by
`1.5`. `DITHER` adds `±0.8%` noise to break up gradient banding.

### Times of day are BLENDED, never selected

`ShaderManager.cpp:1169-1194`. Every NAM0 color is resolved each frame as a
weighted sum over ALL FOUR times of day:

```
value = Σ_t  colors[slot][t] / 255 * SunAmount[t]
```

`SunAmount` is a 4-vector (Sunrise, Day, Sunset, Night) summing to 1, derived
from the game clock against the CLMT sunrise/sunset timings
(`ShaderManager.cpp:1008-1100`). At noon it is `(0,1,0,0)`; early dawn ramps
`(x, 0, 0, 1-x)`; late dawn `(2-x, x-1, 0, 0)`.

Therefore: **a single time slot is never rendered alone.** For most of the day
the sky is a mix of two adjacent slots, and at dawn the Sunrise and NIGHT slots
are cross-faded. Tuning one slot toward a vanilla median changes what is drawn
at times that slot does not name. A second blend sits on top: weather
transitions keep the previous weather (`Sky.secondWeather`,
`Sky.weatherPercent`) and lerp the two resolved colors, at rate
`DATA.TransDelta`.

### Field-by-field

Order is the WRITE ORDER from `wbDefinitionsTES5.pas:10623`.

| Field | Size | What the engine does with it |
|---|---|---|
| `EDID` | var | Editor ID. Not rendered. |
| `DNAM/CNAM/ANAM/BNAM` | var | LEGACY 4-layer cloud textures (FO3 era). 1 of 177 vanilla weathers. Superseded by `0TX`. |
| `<hex>0TX` | var | Cloud layer texture, layers 0-31. Sigs `\x30\x30TX`..`\x40\x30TX` for 0-16, then `A0TX`..`O0TX` for 17-31. Each binds to ONE named shape in `Meshes\Sky\Clouds.nif`. The texture MULTIPLIES the layer color. |
| `LNAM` | 4 | Max Cloud Layers. xEdit default 29; the dome has 29 layer shapes. Vanilla: 29 in 164 of 177, 4 in the 13 oldest (form ver <= 35). **A dome contract, not a per-weather style choice.** |
| `MNAM` | 4 | Precipitation type -> `SPGD` particle geometry. 0 = none. This is what actually spawns rain/snow. |
| `NNAM` | 4 | Visual effect -> `RFCT`. Nonzero in 2 of 177. |
| `ONAM` | 4 | Old cloud speeds (unused legacy, 1 of 177). |
| `RNAM` | 32 | Cloud **Y** speed per layer. Feeds `TexCoordOff` — it SCROLLS the layer's UVs. |
| `QNAM` | 32 | Cloud **X** speed per layer. Same. |
| `PNAM` | 512 | Cloud color per layer x 4 times -> `BlendColor[0]`. **This is what tints cloud sheets** (NOT NAM0 slots 10/11). |
| `JNAM` | 512 | Cloud ALPHA per layer x 4 times -> `BlendColor[0].w`, multiplied by texture alpha. Blended with the same `SunAmount` weights. |
| `NAM0` | 272 | The 17 x 4 color table (below). |
| `FNAM` | 32 | DayNear, DayFar, NightNear, NightFar, DayPower, NightPower, DayMax, NightMax. Near/Far are the fog ramp in world units; Power is the falloff exponent; Max caps fog opacity. |
| `DATA` | 19 | Packed bytes (below). |
| `NAM1` | 4 | Disabled-layer BITFIELD; bit N disables layer N. |
| `SNAM` | 8 | Sound FormID + type (0 Default, 1 Precipitation, 2 Wind, 3 Thunder). Repeatable. |
| `TNAM` | 4 | Sky STATIC -> `STAT`; the volumetric cloud OBJECTS on the dome. Repeatable. 150 of 177 vanilla weathers have them. |
| `IMSP` | 16 | 4 x `IMGS` (Sunrise/Day/Sunset/Night). **Where Skyrim keeps the HDR/tonemap that Oblivion kept in `WTHR.HNAM`.** |
| `HNAM` | 16 | **SSE only: 4 x `VOLI` volumetric lighting.** NOT HDR — the signature collides with TES4's meaning. 93 of 177 vanilla weathers (Update.esm/DLC, form ver >= 43). |
| `DALC` | 32 | Directional ambient cube x 4 times: 6 RGBA faces (X+ X- Y+ Y- Z+ Z-) + specular + fresnel. Lights OBJECTS, not the sky. |
| `NAM2/NAM3` | 16 | Legacy Sun/Moon Glare tables. Superseded by NAM0 15/16. |
| `MODL/MODT` | var | AURORA model. 59 of 177. |
| `GNAM` | 4 | Sun glare lens flare -> `LENS`. **Zero vanilla weathers use it.** |

### NAM0 slot map — and the slot-2 trap

TES4 has 10 slots, TES5 has 17, and **slot 2 means different things**:

| TES5 | Name | TES4 source |
|---|---|---|
| 0 | Sky-Upper | 0 Sky-Upper |
| 1 | Fog Near | 1 Fog |
| 2 | **Unused** | (TES4 had **Clouds-Lower** here) |
| 3 | Ambient | 3 Ambient |
| 4 | Sunlight | 4 Sunlight |
| 5 | Sun | 5 Sun |
| 6 | Stars | 6 Stars |
| 7 | Sky-Lower | 7 Sky-Lower |
| 8 | Horizon | 8 Horizon |
| 9 | Effect Lighting | (none) |
| 10 | Cloud **LOD Diffuse** | 9 Clouds-Upper |
| 11 | Cloud **LOD Ambient** | 2 Clouds-Lower |
| 12 | Fog Far | 1 Fog |
| 13 | Sky Statics | (none) |
| 14 | Water Multiplier | (none) |
| 15 | Sun Glare | (none) |
| 16 | Moon Glare | (none) |

Slots 0/7/8 (Sky-Upper, Sky-Lower, Horizon) are the **three gradient stops** the
Atmosphere dome interpolates with its vertex masks — that is the entire sky
gradient. Slots 10/11 are LOD **lighting** in Skyrim; the decompiled Skyrim enum
comments them `// LODDiffuse` / `// LODAmbient`, and vanilla writes 0 there in
98-100% of weathers. Cloud tint is `PNAM`.

**Form-version gating** (`wbWeatherColors`): slots 10-12 need form version >= 31,
slot 13 >= 35, slots 14-16 >= 37. Below those the record is legitimately short
(vanilla ships 208- and 224-byte NAM0s). Writing 272 bytes at a low form version
is malformed.

### Sky geometry — the same in both engines

Both hardcode `Meshes\Sky\Atmosphere.nif` (gradient dome) and
`Meshes\Sky\Clouds.nif` (cloud layers). Both domes carry the same R/G/B blend
masks. The only difference is the shader property: Oblivion's Atmosphere uses a
fixed-function `NiMaterialProperty`, Skyrim's a `BSSkyShaderProperty` with
`sky_object_type` (2 = `BSSM_SKY`, 3 = `BSSM_SKY_CLOUDS`) selecting the
technique above.

The real structural gap: Oblivion's `Clouds` object holds exactly **4 layer
pointers** (`layer0..layer3`, `numLayers`); Skyrim's holds **32**.

### Unit conversions that are NOT identity

* **Cloud speed is SIGNED in TES5**: `speed = (byte - 127) / 127 / 10`, so 127 =
  stationary and < 127 scrolls the other way (xEdit `wbWeatherCloudSpeedToStr`,
  `wbDefinitionsCommon.pas:4305`). TES4's `DATA.CloudSpeedLower/Upper` are
  UNSIGNED u8 for only two layers. Vanilla authors negative speeds on 124 Y and
  77 X layer entries — **direction is authored data, and an unsigned copy
  silently loses it.**
* **DATA byte scalings** (`wbDefinitionsTES5.pas:10646-10660`): Wind Speed 0..1,
  Trans Delta 0..0.25, Sun Glare 0..1, Sun Damage 0..1, Precip fades 0..1,
  Visual Effect begin/end 0..1, **Wind Direction 0..360**, **Wind Direction
  Range 0..180**.
* **DATA layout differs.** TES4 DATA is 15 bytes with Cloud Speed Lower/Upper at
  offsets 1-2. TES5 DATA is 19 bytes with those two bytes **UNUSED** (speed moved
  to RNAM/QNAM) and gains Visual Effect begin/end + Wind Direction/Range at 15-18.
* **DATA flags gain two bits in TES5**: bit4 Aurora-Always-Visible, bit5
  Aurora-Follows-Sun (flag values 17 and 18 occur in vanilla).

### The four-way layer contract

A cloud layer draws only if ALL hold: it has a `0TX` texture, its `NAM1` bit is
CLEAR, its index is `< LNAM`, and its `JNAM` alpha is nonzero.

Checked across all 177 vanilla weathers: 0 textured layers exceed the LNAM cap
and 0 have an all-zero alpha — but **589 textured layers are deliberately
DISABLED by NAM1.** Vanilla ships the texture and switches the layer off. Any
converter that derives NAM1 from "which layers have textures" will re-enable
layers vanilla intended to be dark.

### Census caveat — use ALL masters

Skyrim.esm alone: 84 WTHR, form version <= 40, **zero** HNAM. Adding Update.esm
(69), Dawnguard (15) and Dragonborn (9) gives **177** weathers, form versions
43/44, and 93 HNAM users. A census that stops at Skyrim.esm will wrongly
conclude the SSE-era fields are unused.

---

## Exact algorithms: fog, and the dome alpha ramp (2026-08-24)
<a id="exact-algorithms-fog-dome-alpha"></a>

### FNAM -> the fog equation (exact)

From the real shader (`Lighting.hlsl:271`, identical in `Effect.hlsl:232` and
`DistantTree.hlsl`):

```hlsl
fogColorParam = min(FogParam.w,
    exp2(FogParam.z * log2(saturate(length(viewPos) * FogParam.y - FogParam.x))));
FogParam.xyz  = lerp(FogNearColor.xyz, FogFarColor.xyz, fogColorParam);
```

`exp2(z * log2(x))` is `pow(x, z)`, so with the constants unpacked
(`FogParam.y = 1/(Far-Near)`, `FogParam.x = Near/(Far-Near)`):

```
t     = saturate( (distance - Near) / (Far - Near) )
fog   = min( Max, pow(t, Power) )
color = lerp( FogNearColor, FogFarColor, fog )
```

So every FNAM field has an exact role:

| FNAM field | Role |
|---|---|
| `Day/Night Near` | distance where fog STARTS. Below it, zero fog. |
| `Day/Night Far` | distance of FULL fog. |
| `Day/Night Power` | exponent on the 0..1 ramp. `<1` fogs up fast then flattens; `>1` stays clear then rushes in. Vanilla is ~0.4, i.e. a fast initial rise. |
| `Day/Night Max` | HARD CEILING on fog density (`min`). `0.9` means the far distance never fully hides the sky — 10% of the scene always shows through. |

Two conversion-critical consequences:

* **`Near >= Far` is not merely odd, it is degenerate**: `Far - Near <= 0`
  makes the reciprocal infinite/negative, so `saturate` snaps the whole world
  to 0 or 1 fog. A negative `Near` likewise shifts the ramp so fog begins
  *behind* the camera. (Oblivion authors negative Near on 18 of 37 weathers;
  vanilla Skyrim on 0 of 177.)
* **Power and Max do not exist in TES4.** TES4 FNAM is 4 floats (Near/Far for
  Day/Night only); TES5 is 8. The extra four are pure additions and must be
  synthesised — they are the shape of the curve, and defaulting them wrong
  changes every distance in the scene even when Near/Far are perfect.

`FogNearColor`/`FogFarColor` come from NAM0 slots 1 and 12. TES4 has ONE fog
color, so Near and Far get the same value and the `lerp` degenerates to a
constant — Skyrim's distance-tinting is unavailable from TES4 data alone.

### The dome alpha ramp — measured on both meshes

The Atmosphere dome carries its own vertex ALPHA that fades the sky out at the
horizon (where terrain and fog take over):

| | Oblivion | Skyrim |
|---|---|---|
| lowest vertex | z = -32 (BELOW the horizon) | z = 0 |
| alpha 0 -> 1 over | ~85 units | ~18 units |
| alpha at z=8 | 0.825 | ~0.6 |

Same mechanism, but **Skyrim's fade is ~4x sharper and starts at the horizon
plane, while Oblivion's dome hangs below it with a long soft fade.** Oblivion
therefore paints its own soft horizon band with sky color; Skyrim hands that
band to FOG almost immediately. This is the mechanism behind "the horizon is
wrong": an Oblivion weather relies on dome geometry that Skyrim does not have,
so the horizon transition must be carried by FNAM instead.

Both domes are otherwise the same 3-stop partition of unity (measured, 8 height
buckets, R+G+B = 1.00-1.07 throughout), so Sky-Upper / Sky-Lower / Horizon mean
exactly the same thing in both games and transfer directly.

---

## What the algorithms say about our current converter (2026-08-24)
<a id="what-algorithms-say-about-our"></a>

Judged against the shader math above, NOT against vanilla's medians.

### Correct, and now explained by mechanism

* **Negative / degenerate fog Near clamped.** `t = saturate((d-Near)/(Far-Near))`
  is genuinely degenerate for `Near < 0` or `Far <= Near`. This fix was right,
  and the equation is the reason.
* **NAM0 slot mapping**, including routing TES4 Clouds-Upper/Lower to TES5
  slots 10/11 and leaving slot 2 unused. Matches the decompiled enums.
* **TES4 `WTHR.HNAM` -> minted `IMGS.HNAM`.** Correct: TES5 `WTHR.HNAM` is
  VOLI volumetric lighting, a different field that merely shares a signature.

### Wrong by mechanism

* **`LNAM = 2`.** LNAM is the count of layer shapes in the shipped
  `Meshes\Sky\Clouds.nif`, which the engine HARDCODES and which has 29. Vanilla
  writes 29 in 164 of 177 weathers. `2` tells the engine to use 2 of the 29
  shapes; the other 27 dome bands are simply never drawn. Not a style choice —
  a contract with a mesh we do not ship.
* **Only 2 cloud layers textured** (vanilla median 11, and 8 actually drawn
  after NAM1/JNAM). Oblivion has only 2 cloud sheets, so the sheets must be
  DISTRIBUTED across the dome's bands; one sheet per band is what the dome
  geometry expects.
* **NAM1 = 0 disabled layers** where vanilla deliberately disables 589 textured
  layers. Vanilla ships a texture and switches the band off; deriving NAM1 from
  "has a texture" re-enables bands vanilla wanted dark.
* **QNAM all 127 (zero X drift), RNAM never negative.** TES5 speed is
  `(byte-127)/1270`, SIGNED. Vanilla authors negative drift on 124 Y and 77 X
  entries. Clouds that only ever scroll one way, and never on X, is a visible
  motion difference.
* **JNAM 94% zero vs vanilla 93.4% one.** Alpha multiplies the layer; zero
  alpha means the band contributes nothing. This is the same 2-layer problem
  seen from the alpha side.
* **TNAM never written** (150 of 177 vanilla weathers have sky statics). These
  are the volumetric cloud OBJECTS; without them the sky is only the flat dome
  bands.
* **Form version 44 with no HNAM/GNAM.** 44 advertises SSE-era fields. Vanilla
  weathers at 43/44 carry HNAM (93 of 177). Either write HNAM or declare a
  version matching what we actually emit.

### Suspicious: fudge factors that should be derived, not fitted

* **`SunGlare * 0.6`.** The comment says it maps TES4's max onto vanilla's p90
  — pure distribution matching. Sun Glare is documented `scaled 0..1`, so the
  byte is a fraction. If a conversion is needed it should come from the glare
  pass, not from a percentile.
* **`FNAM` Power/Max hardcoded to the vanilla median** (0.4/0.4, 0.9/0.925).
  These control the SHAPE of the fog curve and TES4 has no source for them, so
  a constant is defensible — but it should be chosen to reproduce Oblivion's
  fog CURVE, which is a different equation, not to match a median.
* **`TransDelta * 125/255`.** TransDelta is documented `scaled 0..0.25` in TES5
  and is a plain byte in TES4 — a real unit difference, so a conversion is
  warranted, but the factor should come from the two scalings (0.25/1.0 ~ 0.49
  of full range), not from the vanilla median.

### The open question this reframes

Since the sky pixel is `(Σ stops x mask) x VParams x texture + skyScale`, and
`VParams` is a global while the stops come straight from NAM0, the ONLY
per-weather multiplier we control is the color itself and the additive
`skyScale` from the imagespace. That is why deriving Sky Scale from sky
luminance produced bloom: it moves an ADDITIVE floor in proportion to the
MULTIPLICATIVE term.

---

## ENGINE-VERIFIED semantics: disassembly of the actual consumers (2026-08-24)
<a id="engine"></a>

Everything below is read out of `SkyrimSE.exe` (GOG/AE 1.6.659, statically
disassemblable) at addresses resolved through the SKSE Address Library, with
struct offsets from CommonLibSSE-NG (`include/RE/T/TESWeather.h`,
`include/RE/C/Clouds.h`, `include/RE/S/Sky.h` — reverse engineered from the
binary, with `static_assert`ed sizes). **No census, no vanilla percentile, no
"vanilla writes X" appears in this section.** Where an earlier note in this doc
claimed something from a census, this section supersedes it.

Address Library IDs -> 1.6.659 RVAs (via `tools/disasm/address_lib.py`):

| Function | ID | RVA |
|---|---|---|
| `Sky::SetColor` | 25691 / **26238** | `0x3cddb0` |
| `Sky::FillColorBlend` | 25706 / **26253** | `0x3cf820` |
| `Sky::ForceWeather` | 25696 / **26243** | `0x3ce600` |
| `Clouds::Update` | vtable `0x169b518` slot 3 | `0x3c52e0` |

### TESWeather memory layout (CommonLibSSE-NG, static_assert size 0x8D8)

```
0x020 cloudTextures[32]        (TESTexture1024)   00TX..L0TX
0x220 cloudLayerSpeedY[32]     std::int8_t        RNAM   <-- SIGNED in the struct
0x240 cloudLayerSpeedX[32]     std::int8_t        QNAM   <-- SIGNED in the struct
0x260 cloudColorData[32][4]    Color              PNAM
0x460 cloudAlpha[32][4]        float              JNAM
0x660 cloudLayerDisabledBits   uint32             NAM1
0x664 data                     Data (0x14)        DATA
0x678 fogData                  FogData (0x20)     FNAM
0x698 colorData[17][4]         Color              NAM0
0x7A8 sounds                   BSSimpleList       SNAM
0x7B8 skyStatics               BSTArray<STAT*>    TNAM
0x7D0 numCloudLayers           uint32             LNAM
0x7D8 imageSpaces[4]           TESImageSpace*     IMSP
0x7F8 directionalAmbient[4]    BGSDirectional...  DALC
0x878 aurora                   TESModel           MODL
0x8A0 sunGlareLensFlare        BGSLensFlare*      GNAM
0x8A8 volumetricLighting[4]    BGSVolumetric...   HNAM
0x8C8 precipitationData        BGSShaderParticle  MNAM
0x8D0 referenceEffect          BGSReferenceEffect NNAM
```

`kTotalLayers = 32` for both `TESWeather` and `Clouds`. **There is no 29
anywhere in the engine** — 29 is only how many layer shapes the shipped
`Clouds.nif` happens to contain.

### `Sky::SetColor` @ 0x3cddb0 — the exact NAM0 color resolve

`COLOR_BLEND { Color RGBVal[4]; float blend[4]; }` (Sky.h). The disassembly
does, per channel c in {r,g,b} (offsets +0/+1/+2 inside each `Color`, and the
four `Color`s are 4 bytes apart at +0/+4/+8/+0xC; the blend floats at
+0x10/+0x14/+0x18/+0x1C):

```
out.c = ( RGBVal[0].c * blend[0]
        + RGBVal[1].c * blend[1]
        + RGBVal[2].c * blend[2]
        + RGBVal[3].c * blend[3] ) * (1/255)     ; xmm5 @0x1631aac = 0.003921569
```

Then, only when `flash > 0` (`comiss xmm6, 0` / `jbe`), with
`flash = min(a_addFlash, 1.0)` (`minss` against `1.0` @0x16173b8):

```
out.c += flash
if (out.c > lightningColor.c * (1/255)) out.c = lightningColor.c * (1/255)
else if (out.c < 0)                     out.c = 0
```

`[rax+0x670/0x671/0x672]` = `0x664 + 0xC/0xD/0xE` = `data.lightningColor.red/
green/blue`. **So DATA lightning color is a per-channel CEILING on the
lightning flash, not a tint that is added.** A lightning color of (0,0,0)
clamps the flash to black, i.e. disables the visible flash on that channel.

`Sky::FillColorBlendColors` (source, CommonLibSSE-NG `src/RE/S/Sky.cpp:78`)
fills those four slots:

```
RGBVal[0] = currentWeather->colorData[type][time1]
RGBVal[1] = currentWeather->colorData[type][time2]
RGBVal[2] = lastWeather   ->colorData[type][time1]   (or Color() if no last)
RGBVal[3] = lastWeather   ->colorData[type][time2]
```

**So the resolve is a 2x2 blend: two adjacent times-of-day x two weathers
(current + outgoing), all four summed with independent weights and divided by
255.** A given time-of-day slot is therefore never displayed alone, and during
a weather transition FOUR authored colors are live simultaneously.

### `Clouds::Update` @ 0x3c52e0 — LNAM, NAM1, RNAM/QNAM, PNAM/JNAM

**LNAM (`numCloudLayers`, +0x7D0) does NOT bound the draw loop.** At 0x3c5485:

```asm
mov   ecx, [r12 + 0x7d0]        ; weather->numCloudLayers   (LNAM)
test  ecx, ecx
jle   use_default               ; LNAM <= 0 -> speed byte defaults to 0x33
mov   eax, 0                    ; else
cmp   ebx, ecx                  ;   layerIndex vs LNAM
cmovl eax, ebx                  ;   idx = (layerIndex < LNAM) ? layerIndex : 0
movzx edx, byte [rax+r12+0x220] ; cloudLayerSpeedY[idx]     (RNAM)
movzx eax, byte [rax+r12+0x240] ; cloudLayerSpeedX[idx]     (QNAM)
```

LNAM **clamps the index into the RNAM/QNAM speed arrays**. Layers with index
>= LNAM do not stop drawing — they reuse layer 0's scroll speed. With `LNAM=2`,
layers 2..31 all inherit layer 0's drift instead of their own.

Every loop in the function is bounded by `[r14 + 0x510]` = `Clouds::numLayers`,
the RUNTIME object's count (0x3c55ad, 0x3c590d, 0x3c5c7e), which is set from
the geometry actually found in `Clouds.nif`, and separately hard-capped at 32
(`cmp bx, 0x20` @0x3c5588).

**NAM1 sets the geometry's cull flag.** At 0x3c5c4c:

```asm
test dword [r12 + 0x660], r15d   ; disabled bit for this layer (r15d rol'd 1/iter)
je   .clear
or   dword [rax + 0xf4], 1       ; NiAVObject flags |= APP_CULLED
jmp  .done
.clear:
and  dword [rax + 0xf4], 0xfffffffe
```

`r15d` starts at 1 and `rol r15d, 1` each iteration (0x3c5902, 0x3c5c77), so
bit N gates layer N. A set bit culls the layer's geometry outright, and at
0x3c58c3/0x3c5a2a the layer's texture is additionally swapped to the shared
default texture object at `0x30c1120`.

**Cloud scroll speed, exact.** Constants: `xmm4 = 0.1` (@0x1e65040),
`xmm3 = xmm4 XOR -0.0 = -0.1` (@0x1621580), `xmm10 = 1/254` (@0x169b550),
`xmm8 = 0.1 * dt` (@0x1621544). The sequence at 0x3c54ad-0x3c54de is
`(0.1 - (-0.1)) * byte * (1/254) + (-0.1)`:

```
speed = byte * (0.2/254) - 0.1
```

Verified equal to xEdit's display formula `(byte-127)/1270` to 6 decimals at
every byte value, so 127 is exactly stationary, 0 = -0.1, 254 = +0.1.
**Both arrays are `std::int8_t` in the struct but are read with `movzx`
(zero-extend) here, so the stored byte is used unsigned 0..255 and the
SIGNEDNESS COMES FROM THE -0.1 BIAS, not from the type.** Writing 127
everywhere (as we do for QNAM) means literally zero horizontal drift.

**Weather transition cross-fade.** At 0x3c5988-0x3c599d the function reads
`cloudLayerDisabledBits` from BOTH the current weather (`r12`) and the last
weather (`r13`) and branches on the 2x2 of (enabled-now, enabled-before) to
pick between: keep texture, swap to default, or set up the two-texture blend
(`[rbx+0x98]` and `[rbx+0xa0]` are the two texture slots on the shader
property — the `TEXLERP`/`CLOUDSFADE` pair). `Sky::currentWeatherPct`
(+0x1B8, read at 0x3c54eb) is the lerp factor.

### The sky pixel shader (Community Shaders `Sky.hlsl`, vanilla-equivalent)

Vertex stage, non-textured sky:

```hlsl
float3 skyColor = BlendColor[0].xyz * input.Color.x
                + BlendColor[1].xyz * input.Color.y
                + BlendColor[2].xyz * input.Color.z;
vsout.Color.xyz = VParams * skyColor;
```

Pixel stage (`Color::Sky` is IDENTITY in vanilla — it is a Community Shaders
linear-lighting hook that is `pow(c, skyGamma)` only when that feature is on):

```hlsl
psout.Color.xyz = input.Color.xyz * baseColor.xyz + skyScale;
psout.Color.w   = input.Color.w   * baseColor.w;
```

Constant setup (`Nukem9/skyrimse-test`, `BSShader/Shaders/BSSkyShader.cpp`):
for ATMOSPHERE the three `BlendColor` slots receive `NightBlendColor0/1/2`; for
every other sky object only `BlendColor[0]` is used. `VParams =
BSShaderManager::St.fInvFrameBufferRange` (a GLOBAL exposure, not per-weather).
`PParams.y = fInvFrameBufferRange * [sky+0xE4]` and is forced to **0.0 for MOON
and SUNGLARE**. `TexCoordOff` is indexed by `property->usCloudLayer` — that is
where the RNAM/QNAM scroll offsets arrive.

Consequences that follow from the code, not from any distribution:

1. **Dome vertex colors are blend MASKS.** Measured on both games' shipped
   `Atmosphere.nif`: R/G/B sum to ~1.0 across 8 height buckets, R=1 at the
   bottom ring -> G mid -> B at zenith. So NAM0 Sky-Upper / Sky-Lower / Horizon
   are three gradient STOPS the dome interpolates per-vertex.
2. **Texture MULTIPLIES color** (`input.Color * baseColor`). Cloud sheets are
   masks tinted by PNAM; a black texel renders nothing.
3. **skyScale is ADDITIVE and applied after the multiply.** No texture value or
   alpha can remove it — it is a floor on sky brightness.
4. Techniques -> defines: SKY=`DITHER`, CLOUDS=`TEX,CLOUDS`,
   CLOUDSLERP=`TEX,CLOUDS,TEXLERP`, CLOUDSFADE=`TEX,CLOUDS,TEXFADE`,
   TEXTURE=`TEX`, SUNGLARE=`TEX,DITHER`, MOONANDSTARSMASK=`TEX,MOONMASK`,
   STARS=`HORIZFADE`, SUNOCCLUDE=`OCCLUSION`.

### FNAM -> the fog equation (from the shader, `Lighting.hlsl:271`)

```hlsl
fogColorParam = min(FogParam.w,
    exp2(FogParam.z * log2(saturate(length(viewPos) * FogParam.y - FogParam.x))));
FogParam.xyz  = lerp(FogNearColor.xyz, FogFarColor.xyz, fogColorParam);
```

`exp2(z*log2(x))` is `pow(x,z)`, so with `FogParam.y = 1/(Far-Near)` and
`FogParam.x = Near/(Far-Near)`:

```
t     = saturate((distance - Near) / (Far - Near))
fog   = min(Max, pow(t, Power))
color = lerp(FogNearColor, FogFarColor, fog)
```

`Sky` caches these as `fogNear` (+0x194), `fogFar` (+0x198), `fogPower`
(+0x1A8), `fogClamp` (+0x1AC) — the names in CommonLibSSE-NG confirm the roles.
`Near >= Far` makes the reciprocal degenerate so `saturate` snaps the scene to
0 or 1 fog; a negative `Near` starts the ramp behind the camera. TES4 has only
Near/Far (4 floats); Power and Max are TES5-only and control the CURVE.

### `Sky::IsRaining` / `IsSnowing` — exact precipitation gate

From CommonLibSSE-NG source (`src/RE/S/Sky.cpp`), i.e. decompiled logic:

```cpp
IsRaining() = (currentWeather->data.flags & kRainy
               && currentWeather->data.precipitationBeginFadeIn * (1/255) < currentWeatherPct)
           || (lastWeather->data.flags & kRainy
               && lastWeather->data.precipitationEndFadeOut * (1/255) + 0.001 > currentWeatherPct)
```

So `DATA.PrecipBeginFadeIn` / `PrecipEndFadeOut` are **thresholds on the
weather-transition percentage `currentWeatherPct` (0..1), scaled by 1/255** —
not times, not durations. And precipitation only ever happens if the
`kRainy`/`kSnow` FLAG BIT is set; `MNAM` supplies the particle geometry but the
flag is what arms it.

### Sky::Flags and Mode (Sky.h, from the binary)

`Mode { kNone, kInterior, kSkyDomeOnly, kFull }`. `Clouds::Update` early-outs
unless mode is 2 (`kSkyDomeOnly`) or 3 (`kFull`) — 0x3c538e `cmp edx, 3` /
0x3c5396 `cmp edx, 2`, reading `[rsi+0x1bc]` = `Sky::mode`.

### CLMT timing -> the blend weights

`Sky::GetSunriseBegin/End`, `GetSunsetBegin/End` (CommonLibSSE-NG source):

```cpp
cache = currentClimate->timing.sunrise.begin * 0.16666667f;   // 1/6
```

CLMT stores these as **10-minute increments past midnight** (u8), and the
engine multiplies by 1/6 to get GAME HOURS. These four hours are what position
the `blend[4]` weights that `SetColor` consumes, so **CLMT timing and WTHR
colors are one system**: change the climate timings and every weather's
rendered color changes, because different time slots become dominant.

---

## LNAM: every read of `TESWeather+0x7D0` in the binary (exhaustive)
<a id="lnam-every-read-tesweather0x7d0-binary"></a>

Because the earlier "LNAM must be 29" note in this doc was a CENSUS claim
(`vanilla writes 29 in 164/177`) and is therefore not evidence, the executable
was scanned for **every** instruction referencing the field. Method: locate all
122 occurrences of the little-endian dword `0x000007D0` in `.text` and
disassemble around each; keep the ones whose base register holds a
`TESWeather*`.

Result: LNAM is read in exactly **three** places, all with the identical idiom,
and written with a constant `3` in three others.

**Reader 1+2 — `Clouds::Update` @ 0x3c5485 (current weather) and @ 0x3c54ff
(last weather), the RNAM/QNAM scroll speeds:**

```asm
mov   ecx, [r12 + 0x7d0]        ; LNAM
test  ecx, ecx
jle   .default                  ; LNAM <= 0 -> speed byte = 0x33 (51 -> -0.06)
mov   eax, 0
cmp   ebx, ecx                  ; layerIndex vs LNAM
cmovl eax, ebx                  ; idx = (layerIndex < LNAM) ? layerIndex : 0
movzx edx, byte [rax+r12+0x220] ; cloudLayerSpeedY[idx]   RNAM
movzx eax, byte [rax+r12+0x240] ; cloudLayerSpeedX[idx]   QNAM
```

**Reader 3 — the JNAM cloud-alpha getter @ 0x2c1eb0:**

```asm
mov   r9d, [rcx + 0x7d0]        ; LNAM
test  r9d, r9d
jle   .default                  ; LNAM <= 0 -> return 1.0  (@0x16173b8)
xor   eax, eax
cmp   edx, r9d                  ; layer vs LNAM
cmovl eax, edx                  ; idx = (layer < LNAM) ? layer : 0
movss xmm0, [rcx + rdx*4 + 0x460]   ; cloudAlpha[idx][time]   JNAM
```

For contrast, the **PNAM cloud-color getter @ 0x2c1e99** has NO LNAM clamp at
all — it indexes `[r9 + rcx*4 + 0x260]` directly.

### What LNAM therefore IS

`numCloudLayers` is a **clamp on the index into the per-layer RNAM/QNAM/JNAM
arrays**, with a `<= 0` guard supplying defaults. It is NOT a draw-count and it
does NOT disable layers:

* Every draw loop in `Clouds::Update` is bounded by `[r14 + 0x510]` =
  `Clouds::numLayers` — the RUNTIME object's layer count, taken from the
  geometry found in `Clouds.nif` — at 0x3c55ad, 0x3c590d and 0x3c5c7e, plus a
  hard `cmp bx, 0x20` (32) at 0x3c5588.
* Layer visibility is owned solely by NAM1 via the cull flag (0x3c5c4c).

**So with `LNAM = 2`, layers 2..31 still DRAW; they just all read layer 0's
scroll speed and layer 0's alpha.** The visible symptom is not missing clouds —
it is every band sharing one drift and one opacity curve. That is a real defect
and worth fixing, but the reason is this clamp, not "vanilla ships 29".

`LNAM <= 0` is genuinely meaningful: speed falls back to the constant `0x33`
and alpha to `1.0` (fully opaque), bypassing authored JNAM entirely.

### Correction to earlier notes in this document

Any statement above of the form "vanilla writes X in N of 177 weathers" is a
census, not a mechanism, and must not be used to justify a conversion rule. The
engine-verified sections are the authority. Specifically:

* "LNAM=29 is the shipped-dome contract" — **wrong reasoning.** 29 is just the
  shape count in `Clouds.nif`; the engine's own constant is `kTotalLayers = 32`
  and the draw loop uses `Clouds::numLayers`. LNAM should equal the number of
  layers whose RNAM/QNAM/JNAM entries we actually author, because that is what
  it indexes.
* "NAM1 disables 589 textured layers in vanilla" — the COUNT is irrelevant. The
  mechanism is that NAM1 bit N sets `APP_CULLED` on layer N's geometry
  (0x3c5c56) and swaps its texture to the shared default (0x30c1120).

---

## OBLIVION SIDE, from the binary and the shipped shaders (2026-08-24)
<a id="oblivion-side-from-binary-shipped"></a>

The previous section decoded Skyrim's consumers. This one does the same for
Oblivion, so the two can be compared as ALGORITHMS rather than as value
distributions. Everything here is read out of `Oblivion.exe` (x86-32, RTTI
intact, not DRM-packed) and out of the compiled D3D9 shaders shipped in
`Data/Shaders/shaderpackage001.sdp`.

Tools written for this: `scratchpad/ob_rtti.py` (MSVC RTTI -> vtable),
`ob_xref_abs.py` (absolute-address xrefs), `sdp_extract.py` + `d3d9dis.py` +
`sky_dump.py` (SDP shader extraction and SM1/2/3 bytecode disassembly).

### Oblivion sky class vtables (via RTTI)

| Class | vtable | slot 3 = `Update(sky, t)` |
|---|---|---|
| `Sky` | `0x00a56e14` | — |
| `Atmosphere` | `0x00a56b40` | **`0x0053b0e0`** |
| `Clouds` | `0x00a56be0` | **`0x0053b730`** |
| `Sun` | `0x00a571dc` | `0x00545830` |
| `Stars` | `0x00a57144` | `0x00544420` |
| `Moon` | `0x00a56cac` | `0x0053c830` |
| `SkyObject` | `0x00a570c8` | `0x0060cf60` |

### THE SKY GRADIENT IS THE SAME ALGORITHM IN BOTH ENGINES

`Atmosphere::Update` @ `0x53b0e0` gates on `Sky+0xDC` being 2 or 3 (the same
`kSkyDomeOnly` / `kFull` mode gate Skyrim uses), then writes THREE RGBA color
stops into the render globals `0xB431A8`, `0xB431B8`, `0xB431C8`. A staging
copy at `0x7BD739` moves `0xB431A8..0xB431D4` (16 floats = 4 float4s) into the
shader-constant block at `0xB43178`.

The sky VERTEX SHADER (`shaderpackage001.sdp` @ `0x02767c`, `vs_1_1`) is:

```asm
mul  r0.xyz, v1.y, c5        ; stop1 * vertexMask.G
mad  r0.xyz, c4,  v1.x, r0   ; + stop0 * vertexMask.R
mad  oD0.xyz, c6, v1.z, r0   ; + stop2 * vertexMask.B
mul  oD0.w,  v1.w, c4.w      ; alpha = vertexAlpha * stop0.a
```

Skyrim's (`Sky.hlsl`, and the same constants staged by `BSSkyShader.cpp`):

```hlsl
float3 skyColor = BlendColor[0].xyz * input.Color.x
                + BlendColor[1].xyz * input.Color.y
                + BlendColor[2].xyz * input.Color.z;
vsout.Color.w   = BlendColor[0].w * input.Color.w;
```

**These are the same instruction sequence.** `c4/c5/c6` are Oblivion's
`BlendColor[0..2]`. Both engines resolve the dome as a 3-stop blend weighted by
the mesh's vertex colors, and both take alpha from `stop0.a * vertexAlpha`.

Oblivion's sky PIXEL shaders (same package):

| Offset | Shader | Body |
|---|---|---|
| `0x0288c0` | SKY (dome) | `mov r0, v0` — output IS the interpolated vertex color |
| `0x028ee0` | SKYHORIZFADE (stars) | `r0.xyz = t0 * v0`, `r0.w = t0.w * v0.w * dp3(c0,t2)` |
| `0x028a38` | SKYCLOUDSFADE | two-texture lerp by `c4`, then `r0.xyz *= v0`, `r0.w *= v0.w` |

Skyrim's is `psout.Color.xyz = input.Color.xyz * baseColor.xyz + skyScale`.

**The ONLY difference in the whole sky path is Skyrim's `+ skyScale`.**
Oblivion's dome shader has no additive term at all — it ends at the `mad`.

That single term is exactly what the earlier measurement fingered: our
converter DERIVES Sky Scale from sky luminance, so it moves an additive floor
in proportion to a multiplicative color. Oblivion has no such term to inherit,
so **there is nothing in the TES4 record that should ever drive it.**

Other constants recovered from the sky shaders: `c12.x` is the cloud UV scroll
offset added to `v1.y` (`add r0.y, v1.y, c12.x` in the CLOUDS shaders — the
RNAM/QNAM destination), and `0.142857 = 1/7` with `max/min` against 0 and 1 is
the star/horizon fade ramp (`dp4 r0.w, c10, v0; add r0.w, r0.w, -c7.z; mul
r0.w, r0.w, 1/7; max 0; min 1`) — the direct analogue of Skyrim's
`saturate(eyeHeightDelta / 17.0)` HORIZFADE.

### Oblivion's HDR globals, named from their defaults

`Oblivion.exe` keeps the live HDR parameters in a global bank whose exterior
or interior copy is selected by the byte at `0xB43074`. The initialiser at
`0x40E984` copies each INI setting object into its slot; the setting objects
are named by the string their second dword points at (`fTargetLUM:BlurShaderHDR`
at `0xA3E9FC` -> setting `0xB06E64`), so every row below is read from the
code, not matched by default value:

| Global | default | INI field | from setting |
|---|---|---|---|
| `0xB431E8` / `EC` | 0.35 / 0.225 | `fBrightClamp` ext / int | `0xB06E04` / `0xB06E7C` |
| `0xB431F0` / `F4` | 1.5 / 2.25 | `fBrightScale` ext / int | `0xB06E0C` / `0xB06E84` |
| `0xB43200` / `04` | 0.7 / 0.5 | `fEyeAdaptSpeed` ext / (int) | `0xB06E3C` / `0xB06E8C` |
| `0xB43208` / `0C` | 1.0 / 1.0 | `fEmissiveHDRMult` ext / int | `0xB06E44` / `0xB06E94` |
| `0xB43210` / `14` | 1.0 / 1.0 | `fUpperLUMClamp` ext / int | `0xB06E5C` / `0xB06E9C` |
| `0xB43218` / `1C` | 1.2 / 1.0 | `fTargetLUM` ext / int | `0xB06E64` / `0xB06EA4` |

An earlier revision of this table matched slots to INI fields by default value
and labelled `0xB43208/10/18` SunlightDimmer/GrassDimmer/TreeDimmer; the
defaults coincide, the setting pointers do not.

The per-weather values reach these globals through a TRANSITION LERP at
`0x540CE0`-`0x540DC0`. The symbolic FPU trace (`oblivion_disasm.py --fpu`)
resolves every store to the same expression:

```
value = old + (new - old) * ((Sky.weatherPercent - a) / b)
```

with `Sky+0xD8` = `weatherPercent`. So **every HDR parameter is cross-faded
between the outgoing and incoming weather**, exactly as the colors are.

`0xB43208` (EmissiveHDRMult) is then consumed at `0x848CA0` where it MULTIPLIES a
three-component color (`esp`, `esp+4`, `esp+8`) after the `0xB43074`
HDR/non-HDR bank select.

### What this settles about the conversion

1. **Sky-Upper / Sky-Lower / Horizon transfer 1:1.** Both engines run the same
   3-stop vertex blend over domes measured to carry the same R/G/B partition of
   unity. No scaling is justified by the algorithms.
2. **Cloud tint transfers 1:1.** Both do `texture * vertexColor`.
3. **Skyrim's `skyScale` has NO Oblivion counterpart.** It must come from
   vanilla-authored imagespace values, never derived from TES4 color.
4. **Oblivion's HDR block does not map onto NAM0 at all.** `BrightScale`/
   `BrightClamp` drive the separate `HDR%03i.pso` post-process pass; the sky
   shader never sees them. They belong in the IMGS bloom fields, and treating
   them as a reason to scale sky COLOR was wrong.
5. **Both cross-fade weather the same way** (Oblivion `Sky+0xD8`, Skyrim
   `Sky::currentWeatherPct` +0x1B8), so `DATA.TransDelta` is directly
   comparable.

---

## FOG: the one place the two engines genuinely differ (2026-08-24)
<a id="fog-one-place-two-engines"></a>

### Oblivion: fixed-function LINEAR fog

Evidence, all from the shipped shaders and the exe:

* Disassembled **all 123 vertex shaders** in `shaderpackage001.sdp` and counted
  writes to `D3DSPR_RASTOUT` register #1 (`oFog`): **zero**. No Oblivion vertex
  shader computes a fog factor.
* The sky vertex shaders write only `oPos` (RASTOUT#0), `oD0` (ATTROUT) and
  `oT0/oT1` (TEXCRDOUT).
* `Atmosphere::Update` @ `0x53b0e0` writes the weather's fog values into a
  `BSFogProperty`: near -> `+0x2C`, far -> `+0x30`, color -> `+0x20`
  (`0x53b318`-`0x53b34c`), and toggles a flag bit at `[ecx+0x18]` from a
  `near >= far` comparison (`0x53b2e6`-`0x53b30c`).
* Exactly ONE site in `.text` reads both `+0x2C` and `+0x30` as floats
  (`0x40cbe3`/`0x40cbf5`) and it is the debug/telemetry path.

With no shader-side fog and near/far handed to a fog property, Oblivion is
using the D3D9 fixed-function pipeline. Per the D3D spec, `D3DFOG_LINEAR` is:

```
f = (End - d) / (End - Start)          then color = lerp(fogColor, pixel, f)
```

A pure linear ramp. **No exponent. No maximum-density clamp.** That matches the
TES4 record exactly: `FNAM` is only 4 floats (day near/far, night near/far).

### Skyrim: exponent + clamp

From the shader (`Lighting.hlsl:271`, identical in `Effect.hlsl` and
`DistantTree.hlsl`):

```hlsl
fogColorParam = min(FogParam.w,
    exp2(FogParam.z * log2(saturate(length(viewPos) * FogParam.y - FogParam.x))));
```

`exp2(z*log2(x))` is `pow(x,z)`, so:

```
t   = saturate((d - Near) / (Far - Near))
fog = min(Max, pow(t, Power))
```

`Sky` caches these as `fogNear` (+0x194), `fogFar` (+0x198), `fogPower`
(+0x1A8), `fogClamp` (+0x1AC) in CommonLibSSE-NG's reverse-engineered layout —
the names confirm the roles.

### The exact conversion

Oblivion's curve is Skyrim's with **Power = 1.0 and Max = 1.0**:

```
pow(t, 1.0) = t      min(1.0, t) = t      ->  f = t   (linear)
```

So to reproduce Oblivion's fog EXACTLY in Skyrim, `FNAM.DayPower =
FNAM.NightPower = 1.0` and `FNAM.DayMax = FNAM.NightMax = 1.0`, with Near/Far
copied through unchanged.

**Our converter currently writes Power = 0.4 and Max = 0.9/0.925** — chosen as
"the vanilla median". Power 0.4 makes fog rise much faster than linear near the
camera (`t^0.4 > t` for `t<1`), and Max 0.9 prevents fog ever reaching full
density. Both are visible, systematic departures from what Oblivion draws, and
neither is derivable from any TES4 field.

One caveat worth stating: Oblivion's linear fog means distant terrain reaches
FULL fog color, whereas Max=0.9 always lets 10% of the scene show through.
Which looks "better" is a judgement call, but only Power=1.0 / Max=1.0
reproduces Oblivion.

### Sky is NOT fogged in Oblivion

Since no sky shader writes `oFog`, and the sky is drawn with the fixed-function
fog disabled for those passes, Oblivion's sky dome is unfogged — the horizon
band comes from the dome's own vertex ALPHA (measured: fades 0 -> 1 over ~85
units, starting BELOW the horizon at z = -32).

Skyrim's dome fades over ~18 units starting at z = 0, so Skyrim relies on FOG
to carry the horizon transition that Oblivion paints with dome geometry. This
is the one place where a faithful conversion cannot be a pure value copy: the
same FNAM numbers produce a different horizon because the geometry differs.

---

## Oblivion's HDR pass, disassembled (2026-08-24) — the last unknown
<a id="oblivions-hdr-pass-disassembled-last"></a>

`WTHR.HNAM`'s `BrightScale` / `BrightClamp` / `TargetLUM` / `UpperLUMClamp`
drive `HDR%03i.pso`, a shader family that has no Skyrim counterpart. Until now
this doc only had the FIELD NAMES for them. The compiled shaders live in
`Data/Shaders/shaderpackage010.sdp` (and 011-019); disassembled with
`tools/disasm/sdp_extract.py` + `tools/disasm/d3d9dis.py`.

### The pass chain

| Shader | What it is |
|---|---|
| `HDR000.pso` | 16-tap separable blur; weights in `c3..c18 .z`, offsets in `c3..c18 .xy` scaled by `c2` |
| `HDR001.pso` | 9-tap blur with a half-texel `frc`/`cmp` snap (`c0 = 128, 2.5, 1, 0`) |
| `HDR002.pso` | further downsample/blur |
| `HDR003.pso` | straight copy (`texld` -> `oT0`) |
| **`HDR005.pso`** | **BRIGHT-PASS EXTRACTION** |
| **`HDR004.pso`** | **FINAL COMPOSITE + exposure** |

### `HDR005.pso` — bright pass (the exact formula)

```asm
def   c0, 0, 1, 0, 0
texld r0, t0, in0          ; scene color
add   r1.xyz, r0, -c1.x    ; scene - c1.x
max   r0.xyz, r1, c0.x     ; max(..., 0)
mul   r0.xyz, r0, c1.y     ; * c1.y
```

i.e.

```
bloom_source = max(scene - BrightClamp, 0) * BrightScale
```

`c1.x` = **BrightClamp**, `c1.y` = **BrightScale**. This settles their meaning
exactly, and it matches the CS wiki prose ("BrightClamp reduces the scene /
less bloom", "BrightScale controls how bright, more bloom") while being far more
precise: BrightClamp is a **subtractive threshold** below which nothing blooms,
and BrightScale is a **linear gain on the excess**.

### `HDR004.pso` — composite

```asm
def c2, 256, 1, 0.5, 0
texld r5, t0, in2          ; adapted-luminance texture
texld r0, t1, in1          ; blurred bloom
... bilinear fetch + lerp of the 4 taps r4,r3,r2,r1 -> r1 ...
dp3  r7.x, r5, c2.y        ; luminance of the adaptation sample
max  r0.w, r7.x, c1.x      ; max(lum, c1.x)      <- lower luminance clamp
rcp  r0.w, r0.w            ; 1 / that            <- EXPOSURE
mul  r1.w, r0.w, c2.z      ; exposure * 0.5
mul  r2.xyz, r1, r1.w      ; scene * exposure
mul  r0.w, r0.w, c1.x
max  r1.xyz, r2, c2.w      ; max(..., 0)
mad  r0.xyz, r0.w, r0, r1  ; + bloom * (exposure * c1.x)
```

So:

```
exposure = 1 / max(adaptedLuminance, LumClamp)
out      = max(scene * exposure * 0.5, 0) + bloom * (exposure * LumClamp)
```

An auto-exposure tone map with an additive bloom term — `TargetLUM` /
`UpperLUMClamp` are the clamp on the adaptation luminance.

### What this proves about the conversion

**The HDR pass NEVER touches the sky's authored color.** It is a full-screen
POST-PROCESS applied to the composed frame:

* The sky vertex shader emits `oD0 = Σ stop_i * vertexMask_i` (measured above).
* The sky pixel shader emits `color = texture * oD0` with **no additive term**.
* Only afterwards does the HDR chain read the rendered frame and apply
  `max(scene - BrightClamp, 0) * BrightScale` for bloom, plus auto exposure.

Therefore:

1. **`BrightScale`/`BrightClamp` must map to Skyrim's IMGS bloom fields, and
   MUST NOT scale NAM0's brightness.** (They do set its HUE on the dome and
   cloud slots — see [Baked HDR hue](#baked-hdr-hue).) Skyrim's IMGS has `Bloom Threshold` and `Bloom Scale`
   which occupy exactly these two roles (threshold subtracted, then gain), so
   the mapping is one-to-one and needs no fitting.
2. Every earlier theory that Oblivion's colors are "pre-compensated" for a
   scene-brightness term was chasing a post-process that operates on the final
   frame, not on the record. The measurements that refuted those theories
   (`/BrightScale` raising scatter, `x0.5` being worse than raw) were correct,
   and this is WHY.
3. The one term with no TES4 source is Skyrim's additive `skyScale`. Oblivion
   has no additive term anywhere in the sky path, so nothing in a TES4 weather
   should ever drive it.

### Remaining true differences between the engines (complete list)

Having now disassembled both sides, these are ALL the places a faithful
conversion cannot be a pure value copy:

| # | Difference | Consequence |
|---|---|---|
| 1 | Skyrim's sky pixel shader adds `skyScale`; Oblivion has no additive term | must come from vanilla imagespace values, never derived from color |
| 2 | Oblivion fog is fixed-function LINEAR; Skyrim is `min(Max, pow(t, Power))` | write `Power = 1.0`, `Max = 1.0` to reproduce Oblivion |
| 3 | Oblivion dome fades alpha over ~85 units from z=-32; Skyrim over ~18 from z=0 | the horizon band Oblivion paints with geometry must be carried by fog in Skyrim |
| 4 | Oblivion `Clouds` has 4 layer pointers; Skyrim has 32 bands with fixed per-band UVs | 2 authored sheets must be distributed across bands; no authored source for which |
| 5 | Oblivion HDR is a per-weather post-process; Skyrim's is a referenced IMGS | map BrightClamp->Bloom Threshold, BrightScale->Bloom Scale |
| 6 | TES4 has one fog color; TES5 lerps Near->Far color | Near and Far get the same value; distance tinting unavailable |

Everything else in the record — the 3-stop sky gradient, cloud tint, cloud
alpha, cloud scroll, the 2x2 time/weather blend, precipitation gating, CLMT
timing — is the SAME ALGORITHM in both engines and transfers 1:1.

---

## The two tone mappers side by side — and a correction (2026-08-24)
<a id="two-tone-mappers-side-side"></a>

The section above concluded "BrightClamp -> Bloom Threshold, BrightScale ->
Bloom Scale is one-to-one and needs no fitting". Having now also disassembled
SKYRIM's tone mapper, that claim is **too strong** and is corrected here.

### Oblivion (from `HDR005.pso` / `HDR004.pso`, disassembled above)

```
bloom    = max(scene - BrightClamp, 0) * BrightScale        ; HDR005
exposure = 1 / max(adaptedLuminance, LumClamp)              ; HDR004
out      = max(scene * exposure * 0.5, 0)
         + bloom * (exposure * LumClamp)
```

A **linear** operator: reciprocal-luminance auto exposure, plus an additively
composited bloom whose weight also scales with exposure.

### Skyrim (`ISHDR.hlsl`, the BLEND pass — vanilla branch)

```hlsl
if (avgValue.x != 0 && avgValue.y != 0)
    inputColor *= avgValue.y / avgValue.x;        // auto exposure
maxCol      = RGBToLuminance(inputColor);
mappedMax   = (maxCol * (maxCol * White + 1)) / (maxCol + 1);   // REINHARD
blendedColor = inputColor * mappedMax / maxCol;
bloomMask   = saturate(BloomIntensity - blendedColor);
blendedColor += bloomMask * bloomColor;
```

with `Param.y` = **White** (the IMGS "White" field) and `Param.x` = the bloom
intensity (IMGS "Receive Bloom Threshold").

### The structural difference

| | Oblivion | Skyrim |
|---|---|---|
| exposure | `1 / max(lum, clamp)` | `avg.y / avg.x` |
| curve | **linear** (no compression) | **Reinhard** `x(xp+1)/(x+1)` |
| bloom weight | `exposure * LumClamp` — a CONSTANT per frame | `saturate(Intensity - color)` — **per-pixel, decreasing with brightness** |
| bloom source | `max(scene - BrightClamp, 0) * BrightScale` | separate bright-pass with its own Threshold/Scale |

The important one is the bloom weight. Oblivion adds bloom **uniformly**;
Skyrim multiplies it by `saturate(Intensity - blendedColor)`, which **falls to
zero where the image is already bright**. So the same nominal bloom parameters
produce different results: Skyrim self-limits bloom in bright regions,
Oblivion does not.

Practical consequence for the conversion: `BrightClamp` and `BrightScale` DO
belong in Skyrim's Bloom Threshold and Bloom Scale — the bright-pass roles are
genuinely the same (subtractive threshold, then gain) — but the mapping is
**not numerically identity**, because Skyrim then re-weights that bloom
per-pixel and compresses with Reinhard while Oblivion stays linear. A faithful
result needs the bright-pass parameters carried over AND the `White` /
`Receive Bloom Threshold` fields chosen so the Reinhard curve does not crush or
inflate the range Oblivion left linear.

This is the one remaining place where the honest answer is "the algorithms are
different in kind, so the values cannot be copied and must be fitted to match
appearance". Everything else in the record has a 1:1 algorithmic
correspondence, established by disassembly on both sides.

---

## Baked HDR hue — Oblivion's sunset red lives in the bloom, not the colors
<a id="baked-hdr-hue"></a>

**Code:** `tes5_import/record_types/weather.py` `_hdr_hue`, applied to NAM0
Sky-Upper/Sky-Lower/Horizon and the two cloud tints (PNAM), at
`_HDR_HUE_STRENGTH = 0.5`. Confirmed in game on Nehrim.

### Strength: half-way between authored and the full bake

At full strength (1.0) the bake is the upper bound of the effect: it models a
flat patch of color and ignores that Oblivion blurs the bloom across the frame,
mixing it with terrain and neighbouring colors. The overshoot is most visible
on blue skies, because the per-channel threshold favors whichever channel is
largest: Nehrim `Clear` day Sky-Upper 70,154,204 went to 24,165,255 (teal).
`_HDR_HUE_STRENGTH` blends linearly from the authored color toward the full
bake; 0.5 gives day Sky-Upper 47,160,230 and dusk Sky-Lower 198,77,52
(authored 164,90,72, full 231,64,31). The bake treats blue and red the same, so
no strength value reduces the teal without also reducing the red.

### The mechanism

Oblivion's bright pass (`HDR005`) thresholds **each channel separately** and
the composite (`HDR004`) adds it back at a constant weight:

```
shown = exposure * ( 0.5*c + TargetLum * max(c - BrightClamp, 0) * BrightScale )
```

Exposure multiplies both terms, so it moves brightness only. The **hue** on
screen is a pure function of authored data: the color plus three HNAM fields.
Subtracting one threshold from every channel strips green and blue first, so
warm colors are pushed toward red. Nehrim `Clear` dusk Sky-Lower 164,90,72
shows as 255,73,36; its upper-cloud tint 231,150,122 shows as 255,132,91.

Skyrim cannot reproduce this at runtime: its tone map is hue-preserving
Reinhard and its bloom mask `saturate(Intensity - color)` fades out in bright
regions (see the tone-mapper comparison above). So the shown hue is baked
into the color, **at the authored luminance** — brightness still goes through
the knee, which preserves hue.

### Why only the dome and cloud slots

The bake is exact only where the slot's color IS the final pixel: the sky dome
(`color = vertex blend`, no texture) and bright cloud texels (`texture * tint`,
so darker texels were shifted less in Oblivion than the baked tint). Fog,
Ambient and Sunlight are blended with lit, textured geometry before the bright
pass, so the thresholded pixel is not the slot color; baking them turned fog
saturated blue-violet (Oblivion `Clear` dusk fog 73,75,97 -> 65,67,157).

### Which HNAM field is the bloom weight (`HDR004` `c1.x`) — TargetLum

Oblivion.exe (Nehrim install) HDR pass setup, switch at `0x7C092B..0x7C0AA3`,
stages `c1` as `[ebp+0x108..0x114]` before each pass:

| case | c1.x | c1.y | c1.z | c1.w |
|---|---|---|---|---|
| `0x7C092B` | TargetLum (`0x5071A0`) | | | |
| `0x7C09A6` | BrightClamp | BrightScale | | |
| `0x7C09C7` | timer | | `0x507110` | UpperLumClamp |
| `0x7C0AA3` | UpperLumClamp (`0x507170`) | | | |

Only `HDR004` (`max(lum, c1.x)`, bloom weight `c1.x`) and `HDR007`
(`min(len, c1.x)`) read `c1.x` alone. `HDR006` takes UpperLumClamp in `c1.w`
as a `min` too, so UpperLumClamp is the upper clamp (`HDR007`) and **TargetLum
is `HDR004`'s `c1.x`**.

The getters read the runtime bank listed under "Oblivion's HDR globals" above.

---

## Rejected: remapping the IMGS bloom from both tone mappers (2026-09-27)
<a id="hdr-remap-rejected"></a>

**Tried and reverted after one in-game look on Nehrim: it "looked really bad".**
The median-anchored IMGS mapping below stays. Record kept so it is not retried
as-is.

The two pipelines, as read:

```
Oblivion  bloom = BrightScale * max(c - BrightClamp, 0)                (HDR005)
          out   = E_ob * (0.5*c + TargetLum*bloom)                     (HDR004)
          E_ob  = 1 / max(sum(adapted), TargetLum), |adapted| <= UpperLumClamp  (HDR006/007)
Skyrim    bloom = Scale * max(c - Threshold, 0)                        (ISBlur BRIGHTPASS)
          out   = Reinhard_White(E_sk*c) + saturate(Receive - out) * bloom      (ISHDR BLEND)
```

(Skyrim side from Community Shaders' `ISHDR.hlsl` / `ISBlur.hlsl`, a
reconstruction of vanilla.) Oblivion barely auto-exposes: with TargetLum >=
UpperLumClamp, `E_ob` only spans `1/(1.73*U) .. 1/T` (0.52..0.83, Nehrim
`Clear`).

What was written: Bloom Threshold = BrightClamp, Bloom Scale = BrightScale,
Receive Bloom Threshold = 2.0 (bloom on every pixel, as Oblivion), White = 1.0
(linear), Sky Scale = 0, and the Sun disc unkneed. The model matched on-screen
brightness assuming Skyrim's eye adaptation settles where Oblivion's exposure
does; that assumption was never verified (Skyrim's exposure target is set
CPU-side from the Eye Adapt fields), and Skyrim adapts far harder than
Oblivion, so the whole-frame bloom is the likely failure.

The Sun disc knee was then doubled instead of removed (30->60 makes Nehrim's
dawn sun 224,135,97 -> 67,40,29, reported dim; vanilla `SkyrimClear` ships
224,135,97). At 60->120 the disc brightened, but the red sunset horizon read
visibly less red in game, with every sky, cloud and IMGS value unchanged. So
the brighter disc costs the sunset (its bloom and/or the eye adaptation it
drives). Reverted to 30->60, then set to 60->120 again together with the
half-strength hue bake; that combination is the one confirmed in game.

---

## THE HORIZON, solved (2026-08-24)
<a id="horizon"></a>

The horizon band was the last thing the disassembly had not explained. It has
two independent causes, both measured, and only one of them was suspected.

### Cause 1: the two domes fade over very different ANGULAR extents

Both `Atmosphere.nif` meshes were resampled ring-by-ring and converted from
mesh-space z to **elevation angle from the camera** (the dome is drawn centered
on the eye, so mesh elevation IS view elevation). Tool:
`scratchpad/dome_alpha_curve.py`.

| elevation | Oblivion alpha | Skyrim alpha |
|---|---|---|
| −3.65° (OB lowest ring) | 0.000 | *no geometry* |
| −2.00° | 0.190 | *no geometry* |
| −1.00° | 0.403 | *no geometry* |
| **0.00° (true horizon)** | **0.623** | **0.000** |
| +0.50° | 0.733 | 0.237 |
| +1.00° | 0.826 | 0.526 |
| +2.09° | 0.847 | **1.000** |
| +10.16° | **1.000** | 1.000 |

* Oblivion: lowest ring **−3.65°**, alpha>0 from −2.74°, opaque at **+10.16°**
  — a **13.81°** fade band, mean alpha 0.733.
* Skyrim: lowest ring **0.00°**, alpha>0 from +0.47°, opaque at **+2.09°**
  — a **2.09°** fade band, mean alpha 0.535.

Both domes carry the same gradient-stop extents (Horizon dominant below 7.82°
vs 9.54°), so this is purely a fade-width difference, 6.6x.

**Neither dome has a `NiAlphaProperty`**, and Oblivion's material alpha is
1.0 — verified via pyffi. The fade is entirely the VERTEX ALPHA, which
Oblivion's sky pixel shader passes straight through (`mov r0, v0`, measured).

### What that means physically

Oblivion's dome is a **sky-to-fog cross-fade in geometry**. Below ~+10° the
authored Horizon color is never shown at full strength; it is always diluted
toward whatever is behind (fog). Skyrim reaches full Horizon color by +2.09°.

Composited as `sky*a + fog*(1-a)` over all 148 converted weather/time pairs:

```
corr(Horizon-minus-Fog contrast, Skyrim/Oblivion horizon ratio) = -0.734
ratio range 0.882 .. 1.229, median 0.990
52 of 148 pairs have Horizon BRIGHTER than Fog
```

The correlation confirms the mechanism. **But note the sign**: for the
bright-horizon weathers Skyrim comes out at 0.88-0.92x — slightly DARKER, not
brighter. So the dome-fade difference is real and worth correcting, but it is
**not** the source of an overbright horizon. It mainly costs the soft
sky-to-fog gradient that Oblivion paints across 13.8° and Skyrim compresses
into 2.1°.

### Cause 2: our fog curve — this IS the overbright horizon

The horizon is the FARTHEST visible geometry, so fog is maximal there. Our
converter writes `Power = 0.4` and `Max = 0.9`, fitted to vanilla medians.
Against Oblivion's measured fixed-function LINEAR fog (`Power = 1.0`,
`Max = 1.0`, established by finding that **zero** of Oblivion's 123 vertex
shaders write `oFog`):

| position on the ramp | Oblivion fog | ours (0.4 / 0.9) |
|---|---|---|
| 25% | 0.250 | **0.574** |
| 50% | 0.500 | **0.758** |
| 100% (the horizon) | **1.000** | **0.900** |

Two distinct defects:

1. **`Max = 0.9` never lets the horizon fully fog.** 10% of the raw sky and
   terrain punches through where Oblivion has solid fog. At the horizon —
   where the sky slots are at their brightest — that residue is exactly a
   bright band along the skyline. Oblivion can never show this, because its
   fog reaches 1.0.
2. **`Power = 0.4` over-fogs mid-distance** (0.574 where Oblivion has 0.250)
   while under-fogging the far plane. The scene reads hazy up close and
   too clear at the horizon simultaneously.

Real distances make this concrete (converted `Clear`): Near 4096, Far 170000 —
the ramp spans the entire view distance, so `Power=0.4` distorts every pixel of
terrain, not just a corner case. And 25 of 37 weathers have `Far < 60000`, so
their ramp completes well before the horizon and the whole skyline renders at
the leaky 0.9.

### The correct normalization

**Fog (the real fix, and it is exact):**
```
FNAM.DayPower = FNAM.NightPower = 1.0
FNAM.DayMax   = FNAM.NightMax   = 1.0
```
This is not a tuning choice — it is the identity that makes Skyrim's
`min(Max, pow(t, Power))` equal Oblivion's linear ramp. Near/Far copy through
unchanged (with the existing negative-Near clamp, which is still correct
because `saturate` degenerates otherwise).

**Dome fade (a genuine but smaller correction):** Skyrim shows the authored
Horizon color undiluted from +2.09° up, where Oblivion is still only 0.847
opaque and mixing in fog. To match what Oblivion DISPLAYS, the Horizon slot
should be written as Oblivion's own composite at that angle:

```
Horizon_tes5 = Horizon_tes4 * a_ob(2.09°) + Fog_tes4 * (1 - a_ob(2.09°))
             = Horizon_tes4 * 0.847 + Fog_tes4 * 0.153
```

Both inputs are authored TES4 data, the weight is a fixed constant measured
from the shipped meshes, and it correctly does nothing when Horizon == Fog.

### Why this was invisible to every previous approach

Both defects live in fields that carry no TES4 source (`Power`, `Max`) or in
mesh geometry, so no amount of comparing NAM0 distributions against vanilla
could surface them — and "match the vanilla median" actively produced the
wrong answer for Power and Max, because vanilla Skyrim weathers were authored
FOR Skyrim's own curve.

---

## Fog equation equivalence — the proof, and the vanilla context
<a id="fog-equation-equivalence-proof-vanilla"></a>

### The two conventions are complements

D3D9 fixed-function `D3DFOG_LINEAR` defines the **scene** weight:
`f = (End - d) / (End - Start)`, which is 1 at the near plane.
Skyrim's `fogColorParam` is the **fog** weight, 0 at the near plane. So
`fogWeight = 1 - f = (d - Start) / (End - Start)`.

Skyrim with `Power = 1.0`, `Max = 1.0`:
`min(1, pow(saturate((d-Near)/(Far-Near)), 1)) = saturate((d-Near)/(Far-Near))`

**Identical**, with `Start = Near` and `End = Far`. Verified numerically at
every distance:

| d/Far | Oblivion | Power=1, Max=1 | ours (0.4 / 0.9) | error |
|---|---|---|---|---|
| 0.10 | 0.100 | 0.100 | 0.398 | **+0.298** |
| 0.25 | 0.250 | 0.250 | 0.574 | **+0.324** |
| 0.50 | 0.500 | 0.500 | 0.758 | **+0.258** |
| 0.75 | 0.750 | 0.750 | 0.891 | +0.141 |
| 1.00 | 1.000 | 1.000 | 0.900 | **−0.100** |

Our current values are wrong by up to **+0.32 fog** mid-ramp (far too hazy)
and **−0.10 at the horizon** (never fully fogs — the bright skyline band).

### These values are shipped by vanilla, so they are not exotic

Censused over all 177 vanilla WTHR across Skyrim.esm + Update + Dawnguard +
Dragonborn:

```
DayPower   min 0.275  med 0.400  max 1.000    == 1.0 in  15 of 177
NightPower min 0.200  med 0.400  max 1.000    == 1.0 in  15 of 177
DayMax     min 0.600  med 0.900  max 1.000    == 1.0 in  29 of 177
NightMax   min 0.600  med 0.920  max 1.000    == 1.0 in  29 of 177
```

`Power = 1.0` and `Max = 1.0` are values the engine is routinely asked to
render. Writing them is not out-of-distribution — it is choosing the member of
the distribution that happens to be *correct for a TES4 source*.

This census is included only to show the values are legal. **The reason to
write them is the equation identity above, not their frequency.** Taking the
median (0.4 / 0.9) is precisely the averaging mistake: vanilla weathers were
authored FOR Skyrim's curve, so their Power/Max encode Bethesda's artistic
intent, which a converted Oblivion weather does not share.

---

## IMPLEMENTED (2026-08-24) — what changed in the converter
<a id="section"></a>

Six changes, each traceable to a disassembled mechanism rather than a census.
Built with `--import-only` (35237 records, 0 errors) and verified in the
shipped `output/Oblivion.esm/Oblivion.esm`.

| Change | Mechanism | Verified in output |
|---|---|---|
| `FNAM` Power/Max 0.4/0.9 -> **1.0/1.0** | Skyrim's `min(Max, pow(t,Power))` equals Oblivion's fixed-function linear ramp exactly at 1/1. Oblivion's fog is linear because **none** of its 123 vertex shaders writes `oFog`. | Power and Max are 1.0 on all 37 |
| Sky Scale: luminance ramp -> **classification x time lookup** | Sky Scale is ADDITIVE in Skyrim's sky shader (`color*tex + skyScale`); Oblivion's dome shader has no additive term. Deriving it from color is a feedback loop. Vanilla keys it on class x time (R2 0.434) not luminance (R2 0.166). | corr(SkyUpper, SkyScale) **0.885 -> 0.188** (vanilla 0.407); FogNear 0.714 -> 0.180 |
| `LNAM` fixed 2 -> **max(authored layer)+1** | LNAM is an INDEX CLAMP into RNAM/QNAM/JNAM (`cmovl`, three readers), not a draw count. Layers >= LNAM still draw but reuse layer 0's speed and alpha. | 36 weathers LNAM=2, 1 weather LNAM=1 (SigilWhiteOut, no sheets) |
| `QNAM` all-0x7F -> **signed X drift** | Speed decodes as `byte*0.2/254 - 0.1`; 0x7F is stationary. Vanilla authors X drift on 557 of 2656 entries, negative on 77. | 36/37 have X drift, all negative |
| Stars: continuous -> **blanked for rain/snow** | Vanilla treats Stars as a visibility switch: Rainy 95.7% black, Snow 77.8% black, Pleasant 91.4% white. Cloudy left alone (vanilla is 60/18 split). | 9/9 rain+snow weathers blanked |
| `SunGlare` x0.6 -> **passthrough** | The 0.6 mapped TES4's 255 onto vanilla's p90 — a percentile fit with no mechanism. Both games document the byte as the same 0..1 fraction, and vanilla reaches 204. | max 255 |

`TransDelta * 125/255` was reviewed and **kept**: it is a real unit conversion,
not a fit. xEdit annotates the TES5 byte `scaled 0..0,25` and the authored data
confirms the ceiling — over all 177 vanilla WTHR the byte never exceeds 125
(134 sit exactly there), while 19 of Oblivion's 37 author 255.

Tests: 4 new cases in `tests/test_import.py` pinning the mechanisms
(`test_lnam_spans_every_authored_layer`,
`test_cloud_drift_is_two_dimensional_and_signed`,
`test_stars_are_blanked_under_rain_and_snow`,
`test_fog_curve_reproduces_oblivions_linear_ramp`), plus
`test_sky_scale_never_derives_from_sky_color` which doubles every authored
sky color and asserts Sky Scale does not move. 335 pass.

### Deliberately NOT changed

* **The horizon composite** (`Horizon*0.847 + Fog*0.153`). The dome-fade
  mechanism is real and measured — Oblivion fades over 13.81 deg from -3.65
  deg, Skyrim over 2.09 deg from 0 deg, and the correlation with
  Horizon-minus-Fog contrast is -0.734 over 148 pairs — but the effect is
  0.88-0.92x, i.e. Skyrim renders those horizons slightly DARKER, not
  brighter. It is not demonstrated to be what looks wrong in game, so it is
  documented and left alone rather than shipped on a guess.
* **Bloom Threshold / Bloom Scale from BrightClamp / BrightScale.** The
  bright-pass roles match exactly (`max(scene-Clamp,0)*Scale` in Oblivion's
  `HDR005.pso`), but Skyrim then applies Reinhard and masks bloom by
  `saturate(Intensity - color)` while Oblivion adds it flat. Copying the
  numbers across two different composite operators needs in-game fitting.
* **`NAM1` layer disabling, `TNAM` sky statics, cloud-band distribution,
  HNAM volumetric lighting.** All are things vanilla does that we do not, but
  none has an authored TES4 source to derive from.

---

## NAM0 color: the per-plugin normalization is GONE, replaced by a highlight knee (2026-08-24)
<a id="nam0-color-per-plugin-normalization"></a>

### What was wrong

The converter used to compute the PLUGIN's median luminance per slot AND per
time, scale every color so that median landed on vanilla's, then cap at
vanilla's p90. It did suppress the bloom, but in game it produced two clearly
wrong results, both confirmed by the user:

* **Nights went bright blue.** Oblivion authors night at 7-12% of day
  luminance. A PER-TIME factor scaled night UP 1.9-3.1x while scaling day DOWN
  0.62-0.70x, so the authored ratio became 22-55%: Fog went 0.122 -> 0.545.
  The night sky stopped being dark and became saturated.
* **The day sky went dull.** Authored SkyUpper day median 136.5 was pushed to
  84.3, because the vanilla median it aimed at is dragged down by storms,
  dungeons and Sovngarde rather than describing a clear afternoon.

It was also a **population statistic**, so the same weather converted
differently depending on what else shipped in the plugin.

### The measurement that settles it

Across all sky/light slots x times, TES4 authored vs the 177 vanilla weathers:

```
percentile     p50    p75    p90    p95    p99
TES4          76.9  154.6  204.6  243.1  255.0
vanilla       83.8  125.8  168.0  193.5  220.3
```

**The palettes AGREE at the bottom and diverge only at the top.** Oblivion's
colors are not broadly hot; only the top ~20% is — which is exactly the part
that crosses the bright-pass threshold
(`max(scene - BloomThreshold, 0) * BloomScale`, verified in Oblivion's
`HDR005.pso` and Skyrim's `ISHDR.hlsl`). A uniform scale therefore darkens
midtones in order to fix highlights. **The palette and the bloom are two
different problems.**

### What replaced it

A soft knee, per color:

```
lum <= knee            -> returned EXACTLY as authored
lum >  knee            -> knee + (lum-knee) * (ceiling-knee)/(255-knee)
                          all three channels scaled by the same factor
```

with `knee = 160, ceiling = 200`, and the **Sun slot on its own much harder
knee**, now 60 -> 120 (originally 30 -> 60, which read dim in game; see
[HDR remap, rejected](#hdr-remap-rejected)). Sun is the one genuine outlier: TES4 day median 193.4 vs
vanilla 42.5 (4.55x, where no other slot exceeds 1.7x), and in Skyrim the
sun's apparent brightness comes from the glare pass and the imagespace, not
from this color — so a near-white disc here is a pure bloom source.

Chosen in game via `tools/make_sky_unjustified_esp.py` (removed 2026-08-25; variant `UJkneeSun`)
against `UJbase` / `UJraw` / `UJknee` / `UJkneeSoft` / `UJkneeHard` /
`UJsunonly`.

Because the curve is a pure function of one color's luminance it has **no
time axis and no plugin-population term**, so the authored day/night curve
survives and a weather converts identically regardless of its neighbours.

### Verified in the shipped output

```
slot        authored day   OUT day  authored nt  OUT nt   nt/day auth  nt/day OUT
SkyUpper           139.4     139.4         10.0    10.0         0.071       0.071
SkyLower           148.8     148.8         15.3    15.3         0.103       0.103
Horizon            140.5     140.5         16.6    16.6         0.118       0.118
FogNear            132.2     132.2         16.7    16.7         0.126       0.126
Sunlight           206.4     179.4         93.2    93.2         0.452       0.520
Sun                193.4      51.7          0.0     0.0         0.000       0.000
Ambient             91.1      91.1         50.2    50.2         0.551       0.551

OUTPUT   p50 64.1  p75 139.8  p90 175.6  p95 191.2  p99 200.0  max 200.0
vanilla  p50 83.8  p75 125.8  p90 168.0  p95 193.5  p99 220.3  max 255.0
```

Sky slots are bit-identical to the authored data and their day/night ratios do
not move at all. Only Sunlight and Sun are compressed. The output distribution
now tracks vanilla at the top without touching the midtones.

The one place the ratio does shift is a slot whose DAY value is above the knee
(Sunlight 0.452 -> 0.520). That is inherent to compressing a highlight — night
is under the knee so it cannot move, therefore its relative share rises. It is
+15% against the old code's +246%. `test_the_knee_has_no_time_axis` measures the
same effect on a synthetic slot: authored 0.073 becomes 0.085 (+16%), against
the +246% the per-time normalization produced.

`PNAM` cloud tints use the same knee for the same reason (the sheet is
MULTIPLIED by the tint in both engines' pixel shaders, so a near-white tint
hands the tonemapper the texture at full strength). Its old per-time p90 cap
had the identical time-axis flaw.

### Rule

`set_nam0_normalization()` and `_NAM0_K` are deleted. **Do not reintroduce a
per-plugin or per-time color scale.** If a color problem appears, first ask
whether it is a PALETTE problem or a HIGHLIGHT problem — they need different
fixes, and conflating them is what cost several in-game rounds here.

---

## Cloud layers: the sheets go on 11 and 27, and the reason is UV (2026-08-24)
<a id="cloud-layers-sheets-go-11"></a>

### The symptom

Forcing the converted `Cloudy` in game showed **no clouds at all**. The record
was fine (two textures, both layers enabled, nonzero alphas and tints) and the
`.dds` files existed, so the fault had to be in WHICH dome shapes layers 0 and
1 are.

### Why the old plan was wrong

The converter capped the plan at two layers on the belief that **we ship the
dome**: `asset_convert` writes `tes4\sky\clouds.nif` (two `CloudDome` shapes)
and the comment claimed the converted CLMT points at it.

It does not. **CLMT has no cloud-mesh field at all** — its `MODL` is the
NIGHT SKY / stars mesh (`wbRecord(CLMT)` in `wbDefinitionsTES5.pas`,
`TESClimate::nightSky` in CommonLibSSE-NG). The engine loads the HARDCODED
`Meshes\Sky\Clouds.nif` (string at SkyrimSE.exe `0x169a538`). So layer N binds
to the Nth shape of VANILLA's 29-shape dome, and ours is never loaded.

Vanilla's shapes are named, and layers 0/1 turn out to be:

```
L0  01_CDUpper_04     26 verts  20.8..88.4 deg
L1  02_CDUpper_04_E    9 verts   4.5..23.9 deg
```

A zenith cap and one eastern sliver — **35 vertices**. `SkyrimCloudy` draws
8/9/10/11/16/21/22/28 = 409 verts and explicitly DISABLES 0 and 1 via NAM1. We
were painting the two bands vanilla turns off for cloudy weather.

### The measurement that actually decides placement

Copying vanilla's layer SET was also wrong: in game it put all the cloud
**around the horizon** instead of over the sky. The UV layout says why — the
shapes are not interchangeable, they carry different tiling:

```
L11 09_CDTop              U span 1.58  V span 1.58   ~1:1 projection
L27 14_CDLower            U span 2.27  V span 2.27   ~1:1 projection
L 8 07_CDDome_Horizon     U span 6.00                tiles 6x round the horizon
L 9/10 (_E/_W)            U span 2.40                same band, split
L15-26 12_/13_CDHorizon_* V span 0.25                narrow V-sliced STRIPS
L28 15_CDFog              U span 21.00               tiles 21x, horizon wash
```

Oblivion authors its two sheets as **single full-dome projections**
(`CloudDome:0` 3.35x3.35, `CloudDome:1` 2.97x2.97, both spanning 0..90 deg),
so 11 and 27 are the only structural matches. Anything heavily U-tiled turns a
full-sky sheet into a repeating horizon band — exactly the observed failure.

Vanilla confirms the roles by shipping **dedicated art per layer**:

```
L11  SkyrimClouds01 / SkyrimCloudsLower0*    full-dome sheets
L27  SkyrimClouds01 / SkyrimCloudsLower03    full-dome sheets
L16  SkyrimCloudsHorizon01   50 of 50 times  purpose-made STRIP
L28  SkyrimCloudsFill       156 of 157       purpose-made WASH
```

We have no strip or wash art, so those layers have nothing correct to put on
them and are left empty.

### In-game A/B (tools/make_sky_unjustified_esp.py, removed 2026-08-25)

| variant | layers | result |
|---|---|---|
| `CLbase` | 0, 1 | no clouds (the bug) |
| `CLvanilla` | 8,9,10,11 / 16,21,22 / 28 | cloud all round the HORIZON, wrong |
| `CLupper` | 3,6,11 / 16,21,22 / 28 | same, wrong |
| `CLdomeonly` | 8,9,10,11 / 27 / 28 | correct |
| `CLminimal` | **11 / 27** | correct, ~identical to domeonly |
| `CLswap` | 27 / 11 | definitely wrong |

`domeonly` and `minimal` matched because layer 27 was doing all the visible
work in both; the 8/9/10 tiling only added the wrongness seen in `CLvanilla`.
`CLswap` failing confirms the sheets are not interchangeable either: the
upper sheet belongs on 11 (`09_CDTop`) and the lower on 27 (`14_CDLower`).

### Shipped

```
_WTHR_UPPER_LAYER = 11        # 09_CDTop     <- DNAM upper sheet
_WTHR_LOWER_LAYER = 27        # 14_CDLower   <- CNAM lower sheet
```

Verified over all 37 converted weathers: textured layers are exactly
`{11: 35, 27: 36}`, `LNAM = 28` on 36 of 37 (the 37th is `SigilWhiteOut`,
which authors no sheets), and every weather's LNAM spans its highest layer —
required because LNAM is an index clamp into RNAM/QNAM/JNAM, not a draw count.

### Rule

**Classify dome layers by UV SPAN, not by elevation.** The elevation ranges
overlap heavily and say nothing about how a texture will be mapped; the U/V
spans are what separate a full-dome projection from a tiled band or a sliced
strip. Copying vanilla's layer set without checking the art it was authored
for reproduces its geometry, not its appearance.

## FO3/FNV NAM0: six times of day

**Code:** `tes5_import/record_types/weather_falloutnv.py`

FalloutNV authors NAM0 with **six** times of day; TES4 and TES5 use four.
xEdit's `wbWeatherTimeOfDay` (`Core/wbDefinitionsCommon.pas:7895`) makes this
explicit — `if wbIsFalloutNV` selects a struct of Sunrise, Day, Sunset, Night,
**High Noon, Midnight**, and a union decider picks the four-time struct when the
subrecord is 160 bytes. Ten slots x six times x RGBA = **240 bytes**.

Measured over `export/FalloutNV.esm/WTHR.txt` (63 records): **55 are 240 bytes,
8 are 160**. Oblivion is uniformly 160 across all 37.

### The defect

`_wthr_nam0`, `_wthr_cloud_colors` and `_wthr_dalc` all index
`raw[(slot * 4 + time) * 4]`. Against a six-time blob every slot after the
first reads from the wrong color, and the daytime index lands in the zeroed
High Noon / Midnight padding. For `DefaultWeather`, day colors read as:

| slot | shipped (stride 4) | authored (stride 6) |
|---|---|---|
| Sky-Upper | (90, 131, 150) | (90, 131, 150) |
| Fog | **(0, 0, 0)** | (72, 104, 103) |
| Ambient | (245, 242, 239) | (78, 160, 175) |
| Sunlight | **(0, 0, 0)** | (244, 206, 149) |
| Sky-Lower | **(0, 0, 0)** | (138, 170, 172) |
| Horizon | **(0, 0, 0)** | (129, 152, 140) |

Sky-Upper is correct only because stride 4 and stride 6 coincide at slot 0.
Symptom: **a near-black sky at midday.**

### Slots are NOT remapped

FO3/FNV slot meanings already match TES5, and differ from TES4 at two indices:

| idx | TES4 | FO3/FNV | TES5 |
|---|---|---|---|
| 2 | Clouds-Lower | Unused | Unused |
| 9 | Clouds-Upper | Effect Lighting | Effect Lighting |

So only the time stride changes. An earlier draft routed FNV through the TES4
layout and dropped slots 2 and 9 — **wrong**: a census found **51 of 55**
records carry nonzero data in both, distinct from each other in 51.

### What is dropped, and why that is acceptable

High Noon and Midnight have no TES5 slot. Census of the 55 six-time records:

- **31** have nonzero High Noon, **25** nonzero Midnight.
- High Noon is byte-identical to Day in **5.6 of 10 slots** on average, and
  averages only **+6.7 luminance** over it (one record matches in all 10).

They are a mild midday/midnight variant, not independent data; Day and Night
already carry the look. This is the only information loss in the conversion.
