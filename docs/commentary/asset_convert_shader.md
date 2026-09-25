# asset_convert/nif/nif_converter.py - shader values

**Code:** `asset_convert/nif/shaders.py`, `asset_convert/nif/geometry_shader.py`, `asset_convert/nif/nif_converter.py`, `asset_convert/texture/spec_mask.py`, `asset_convert/texture/luminance_textures.py`

## Sky geometry takes the sky shader, not a world one
<a id="sky-object-types"></a>

**Code:** `sky_object_type_for`, `_build_sky_shader` in `asset_convert/nif/geometry_shader.py`

Skyrim draws the sky in a dedicated pass BEFORE the world: unlit, unfogged, with
the horizon blend the weather record drives. `BSSkyShaderProperty.sky_object_type`
tells that pass which layer a shape is. Routing these through
`BSLightingShaderProperty` made the stars ordinary world geometry, which drew
over the terrain.

The pass does its own blending and vanilla sky meshes carry **no**
`NiAlphaProperty`, so Oblivion's is dropped rather than carried across.

**The mapping cannot be derived from the NIF.** Oblivion has no equivalent enum
— it identifies sky geometry by WHICH SLOT of the climate/weather record
references the mesh — so the table keyed by lowercase basename is the mapping
between the two models, and is authored data.

Eligibility is by DIRECTORY, not basename: only meshes under a `sky/` folder
qualify, because names like `clouds.nif` and `sky.nif` collide with ordinary
clutter elsewhere in the tree.

| Oblivion mesh | sky object type |
|---|---|
| `stars.nif`, `stars_oblivion.nif`, `sestars.nif` | `SKY_STARS` (5) |
| `clouds.nif`, `clouds_oblivion.nif` | `SKY_CLOUDS` (3) |
| `atmosphere.nif`, `sky.nif` | `SKY_BASE` (2) |
| `sunbeam01-03.nif` | `SKY_SUNGLARE` (1) |

`SKY_TEXTURE` (0) and `SKY_MOON_STARS_MASK` (7) complete the engine's enum; no
Oblivion mesh maps onto either.

## Lit or unlit: choosing the Effect shader
<a id="fx-shader-discriminator"></a>

**Code:** `_is_fx_surface` in `asset_convert/nif/geometry_shader.py`

`BSLightingShaderProperty` is a LIT material: it shades every pixel against the
normal map in texture slot 1. Oblivion's FX textures ship no `_n` companion at
all (SEFXWHITE, SEFXLightRippleINVERT, SEForceRipple), so routing that geometry
through the lighting shader shaded it against a texture that does not exist —
the "major texturing problem" on `se11sheopooffx` and `se01waitingroomwalls`.
`BSEffectShaderProperty` is the vanilla home for glow/FX geometry and has no
normal-map slot at all.

**Indicator 1 — Oblivion's own unlit declaration.**
`NiVertexColorProperty.lighting_mode == LIGHTING_E` (0) means "emissive only,
ignore scene lighting"; lit geometry uses `LIGHTING_E_A_D` (1). In
`se01waitingroomwalls` the three roomRoomFX light-ripple shapes are the only
mode-0 surfaces in the mesh, while all 40-odd wall and trim shapes are mode 1 —
exactly the lit/unlit split the two Skyrim shaders encode.

**Indicator 2 — additive blending.** A surface whose `NiAlphaProperty` sets
`dst=ONE` ADDS its color to the framebuffer, so it can never be ordinary lit
geometry: lighting it would double-count the light it already contributes.
Vanilla agrees without exception — of **64** additively-blended shapes sampled
across `meshes/effects` and `meshes/dungeons`, **64** use
`BSEffectShaderProperty` and **0** use the lighting shader.

This second indicator exists because mode 0 is not always present: many FX
meshes ship no `NiVertexColorProperty` at all, so the mode defaults to "lit".
`dungeons/misc/fx/fxmistgroundeffect01` — the Ayleid-ruin ground mist — is five
additively-blended AtmosphereCloud01 planes with no vertex-color property, and
every one became a LIT, normal-mapped surface with no soft fade: the visible
rectangle that was reported. Across Oblivion's own FX directories **76 of 179**
blended shapes declare no `lighting_mode`, so the gap is the common case.

**Plain alpha blending is deliberately excluded.** That same census shows **3**
legitimate `BSLightingShaderProperty` cases (glass, ice), so widening the rule
to all blending would misroute real lit geometry.

**Do NOT infer any of this from the texture path or a missing `_n`.** A
700-mesh census found **101** shapes whose diffuse has no `_n` companion, and
they are overwhelmingly ordinary LIT geometry — troll skin, clothing, painted
signs, plaster walls, grass — that must keep its lighting. The material fields
are equally useless: these FX shapes disagree on every one of them (roomRoomFX
emissive-white and blended, LightBeam emissive-black and blended, Cone01 no
alpha property, GlowPlane material-alpha 0).

### <a id="flipbook-to-atlas"></a>NiFlipController becomes a frame-strip atlas

Fire and effect quads animate through multiple texture frames using
`NiFlipController` on the `NiTexturingProperty`. It is DEAD in Skyrim — **0 of
17,216** vanilla meshes — and the equivalent is a frame-strip atlas driven by a
`BSEffectShaderPropertyFloatController` stepping "U Offset" (var 6) with CONST
keys. The frames are composed into a horizontal-strip DDS (the job runs in
`convert_nif`, which knows the output tree), which restores the animation in
game AND in NifSkope, whose EffectFloatController is supported where NiPSys
chains are not. When the frames cannot be resolved the shape falls back to a
static first-frame texture.

pyffi defaults UV Scale to (0,0), which collapses every UV to the texture's
top-left texel — usually transparent on a flame texture — and renders the
geometry invisible. Vanilla is offset (0,0), scale (1,1).

## The soft-particle depth fade
<a id="soft-particle-fade"></a>

**Code:** `apply_fx_soft_effect` in `asset_convert/nif/shaders.py`

A blended FX quad that intersects solid geometry is normally cut off along the
intersection line, so a smoke or mist billboard standing in a floor shows the
QUAD'S OWN RECTANGULAR EDGE — the "distracting bounding box around transparent
effects". `slsf_1_soft_effect` makes the engine fade the quad out over Soft
Falloff Depth units of depth difference instead, which removes the hard edge.

Oblivion has no equivalent flag (its FX quads are hand-placed to avoid
intersections), so there is no source field to carry across — the value comes
from what vanilla does with the same kind of surface. Census of **1,198**
`BSEffectShaderProperty` shapes across `meshes/effects` + `meshes/dungeons`:

| source alpha | n | soft_effect=1 |
|---|---|---|
| 0x100d (additive) | 470 | 417 (89%) |
| 0x10ed (blend) | 362 | 224 (62%) |
| no `NiAlphaProperty` | 332 | 10 (soft_effect=0 in 322, 97%) |

So blended FX gets the fade and unblended FX does not. **100.0** is the
commonest falloff depth in the same census (250/521 on mist/smoke/fog geometry)
and is what vanilla uses for ambient room fog, which is exactly this case.

### <a id="self-lit-flames-are-not-faded"></a>A self-lit flame must not be faded

The depth fade attenuates a quad against whatever it intersects. On ambient fog
that is the point. On a FLAME it is destructive: a candle flame sits directly on
its own wax and a sconce flame against its own bracket, so the fade dims the
flame into the very object it is mounted on. Vanilla authors exactly this split
inside ONE mesh — `mps\mpscandleflame01.nif`, both particle systems, both
additive 0x100d, both `emissive_multiple` 1.0:

| shape | soft_effect | falloff |
|---|---|---|
| CandleFlame01 (the flame) | 0 | 2.0 |
| CandleGlow01 (the halo) | 1 | 6.0 |

The same holds for every mounted fire core in the vanilla corpus —
`slighthousefire` "Fireball", `torchsconce01` "pFireballCore04",
`giantcampfire01burning` "PFireball" — all `soft_effect=0`, **49** such particle
systems across **281** vanilla fire meshes.

Skyrim's value is NOT reconstructible from structure. Measured over those **511**
vanilla FX shaders, none of block type (particle 119/168 soft=1 vs geometry
159/343), alpha flags (0x100d splits 163/74) or `double_sided` (78% vs 44%)
predicts it; it is authored per effect. So key it on the one authored quantity
that DOES separate the populations — `NiMaterialProperty.emissive_color`:

| | example | emissive |
|---|---|---|
| flames | `fire\firetorchlarge`, `firecandleflame`, `fireopen*` | (1.0, 1.0, 1.0) |
| fog/dust | `fxcloudthick01` 0.078, `fxcloudthin01` 0.047, `fxdustcloud01` 0.337, `sefxmistdemen` 0.310 | all ≤ 0.34 |

A surface authored at FULL WHITE declares "I am the light source" and is left
hard; anything dimmer is ambient haze and takes the fade. Erring here is
asymmetric — a missing fade leaves a nicety off a flame, a wrongly-applied one
erases the flame outright.

### <a id="particle-soft-effect"></a>Particles are the case the fade matters most for

A smoke plume drifting into a wall otherwise cuts off along a hard line, and
every billboard shows its own quad edge. `alpha_prop` is always set by the time
the particle path calls this (defaulted to additive), so blended systems all
qualify.

**The test uses the AUTHORED emissive, never the shader's final value.** The
shader ends up white in three different situations and only one of them is a
flame: authored full white (**109** systems), a fallback because the source
authored BLACK (**159**), and a fallback because a chromatic curve supplies the
color instead (**320**). Keying the flame test on the final value would skip the
depth fade on all **479** fallback cases — including the smoke plume in
`fire\fireopensmallsmoke.nif`, which authors (0,0,0) and is exactly the kind of
surface the fade exists for.

### <a id="effect-shader-vertex-colors"></a>The vertex-color flag must match the data

SSE renders geometry black when `slsf_2_vertex_colors` disagrees with what the
mesh data actually carries, so the flag is set from `has_vertex_colors` rather
than assumed. Vertex alpha rides along with it, which is what dims layered flame
quads correctly.

### <a id="effect-shader-emissive"></a>The effect emissive is carried, not forced white

**Code:** `_effect_emissive` in `asset_convert/nif/geometry_shader.py`

Oblivion dims an FX surface through `NiMaterialProperty.emissive_color` —
`fxmist01` ships **(0.47, 0.47, 0.47)**. Forcing white DOUBLED every such
effect, and on an additive quad that accumulates once per layer.

White is used only as the fallback when the authored color is (0,0,0), which
would otherwise render the surface black.

The material alpha rides in the emissive ALPHA channel, because that is what the
engine multiplies the sampled texel by. `emissive_multiple` is held at 1.0:
vanilla's value on **852 of 1164** blended FX shapes.

### <a id="flame-brightness-is-authored"></a>Flame brightness: the authored emissive, never the filename

An earlier revision matched `fire`/`flame`/`torch` in the diffuse path (minus a
smoke/mist/fog/dust/steam/cloud veto) and boosted anything that hit to
`emissive_multiple` 1.5. That is classification by filename and it is wrong in
both directions: in Oblivion's own tree it caught `textures\lights\torch02.dds`
— the WOODEN HANDLE, whose host `lights\torch02noflame.nif` has no flame in it
at all — and it can only ever work for meshes following Bethesda's naming, never
for Nehrim, Morroblivion or any third-party plugin.

Oblivion states the brightness itself, per SHAPE, in
`NiMaterialProperty.emissive_color`. Measured across every particle system in
`meshes/` (all **778**) the two populations do not overlap:

| | mesh / shape | emissive |
|---|---|---|
| flames | `fire\firetorchlarge` "Fire" | (1.000, 1.000, 1.000) |
| | `crtfirelogs` "PCloud08BigFlame" | (1.000, 1.000, 1.000) |
| fog | `fx\fxcloudthick01` "Cloud" | (0.078, 0.078, 0.078) |
| | `fx\fxcloudthin01` "Cloud" | (0.047, 0.047, 0.047) |
| | `fx\fxdustcloud01` "PCloud02v" | (0.337, 0.337, 0.294) |

Distribution: **227** author full 1.0 white, **190** a dim <0.5, **202** in
between, **159** author black (which already falls back to white). The authored
value IS the discriminator, at better than 12× separation, and it is per-shape —
which matters because `firetorchlargesmoke.nif` holds a flame AND a smoke plume
in one file, and any per-file test must give them the same answer.

**The boost is 1.5, not 1.0.** 1.0 is the mode across all vanilla FX, but that
population is mostly smoke, mist and glow planes. Restricting the census to the
shapes that match this branch — vanilla FX that are full-white AND
`soft_effect=0`, i.e. self-lit surfaces mounted against geometry — makes 1.0 the
minority:

| mult | 1.0 | 1.1 | 1.25 | 1.5 | 1.6 | 2.0 | 3.0 |
|---|---|---|---|---|---|---|---|
| n | 16 | 9 | 6 | 5 | 43 | 6 | 5 |

**74 of 90** are above 1.0, median 1.6, with the burning cores clustered at the
top: `torchsconce01` pFireballCore04 1.50 (Torch:0 1.25), `giantcampfire01burning`
PCloudForgeSparks 1.25, `fxsmokelargeclose01` Flames 1.60. The 1.0 entries are
`*off*` variants and non-flame parts (GlowMesh, lamp bodies).

The flames commit's 1.5 was therefore the RIGHT VALUE on the wrong test. Holding
every flame at a neutral 1.0 made `fire\fireopensmall.nif` and its siblings
visibly dimmer, which the project owner spotted in game. 1.5 sits inside the
vanilla cluster and is what the previous build shipped, so it is also the
no-regression choice.

## Rewriting a texture path into the `tes4\` tree
<a id="rewrite-tex-path"></a>

**Code:** `rewrite_tex_path` in `asset_convert/nif/tex_paths.py`

**Normalize the separator FIRST.** Oblivion NIFs use both, sometimes in the same
file, so testing only for `textures\` let a forward-slash
`textures/lowres/foo.dds` fall through to the else branch and come out as
`Textures\tes4\textures/lowres/foo.dds` — a path that resolves to nothing, and
the LOD tiles then reference 100 textures that do not exist.

<a id="lowres-textures"></a>
**`textures\lowres\` is kept, falling back to its full-res twin.** It is an
Oblivion `_far.nif` authoring convention for low-resolution LOD copies; pyffi
ships a spell that writes exactly this prefix, documented "used mainly for making
_far.nifs". The texture copy ships the `lowres` tree like any other, and the BSA
prune no longer cuts it.

Neither "always drop" nor "always keep" is correct: both happen in real data.
A census of every `lowres` reference in the source meshes, checked against the
plugin's own texture tree plus Oblivion.esm's:

| plugin | lowres refs | lowres file exists | only the full-res twin exists | neither |
|---|---|---|---|---|
| Unique Landscapes v2.2.0 | 21 | 16 | 5 | 0 |
| Nehrim | 115 | 105 | 8 | 2 |
| Oblivion, Knights, Elsweyr Anequina | 0 | — | — | — |

A user's build of Unique Landscapes 0.663 hit the reverse case: `_far` meshes
naming `LowRes\xullc\Rockbeach05.dds` and `LowRes\xulJerallGlacier\…`, which the
mod ships ONLY under `lowres`. Dropping the segment pointed them at nothing.

So the shape's diffuse and authored normal keep the authored `lowres` path when
`resolve_source_texture` finds its source, and otherwise take the full-res twin
(`resolve_lowres` in `shaders.py`); when neither exists the twin is kept. A
derived `_n`/`_g` map for a `lowres` diffuse also tries the twin's.

**A leading `data\` is stripped.** It is an authoring slip Oblivion tolerates (it
resolves paths from the Data folder either way) and Skyrim does not. Measured
across Nehrim's **12,437** source meshes: **4** distinct textures in **10** meshes,
among them `dwarven\rock02.dds` in **7**. Left in, the reference came out as
`Textures\tes4\data\textures\…` — nothing there, AND the prune then deleted the
real texture, because the manifest key never matched the shipped path.

## Resolving a source texture through the master's tree
<a id="texture-fallback-roots"></a>

**Code:** `resolve_source_texture`, `master_texture_roots` in
`asset_convert/nif/shaders.py`

[Master-export blindness](../../CLAUDE.md#master-blindness), the asset half. An
imported mod ships only the files it changes; everything else lives in its
MASTER's export tree, which deriving the texture root from the mesh path can
never reach. Measured on the author's parallax mod: of the **3357** distinct
texture paths its **8665** meshes reference, **1464** were in the mod and **1602**
ONLY in `Nehrim.esm`. Unreachable means no height map and no specular verdict, so
the resolver falls back through the master roots in order.

## Material defaults come from vanilla, not from Oblivion
<a id="shader-material-defaults"></a>

**Code:** `_set_material_defaults` in `asset_convert/nif/geometry_shader.py`

These were once never assigned at all, so every shape shipped at pyffi's
defaults — glossiness 0.0 with a BLACK specular color and the specular flag
on, measured at **100% of 3931** shaders in our own output.

The replacements are vanilla's modes, not Oblivion's values:

| Field | Value | Evidence |
|---|---|---|
| glossiness | 80 | Vanilla shader type 0 has 80 as both median AND mode — **1333 of 2961** sampled shaders, and the modal value in **12 of 15** top folders. |
| specular color | white | White in **56%** of vanilla, black in **3%**. |
| specular strength | 1.0 | The mode. Arcane University puts the typical band at 0.25–1.0, which vanilla's own 2.2 and 3.0 outliers ignore, so the mode is taken and the tail is not. |

**Oblivion's glossiness is deliberately NOT carried over.** Its median is 10
with **59.4%** of shapes sitting on exactly 10 — an authoring default rather
than a chosen value — and 10 in Skyrim is what HAIR uses: a very wide highlight.

Specular strength is uniform on purpose: the modulation belongs in the normal
map's alpha, not here. The spec-mask check still runs, because its per-category
counters are what tell the texture stage how much it had to synthesise.

## Refraction surfaces
<a id="refraction-surfaces"></a>

**Code:** `_make_refractive` in `asset_convert/nif/geometry_shader.py`

TES4 marks a refraction surface with the material NAME `refractF` — Bethesda's
own convention, authored, not a filename guess. Census of `export/Oblivion.esm`
`meshes/oblivion`: **17** shapes carry a refraction diffuse and **all 17** are
named `refractF`/`RefractF` (Oblivion gates, sigil stones, the siege crawler);
Morroblivion's ancestor ghost and dwarven spectre use the same name.

Without the refraction bits these ship as ordinary lit geometry, so a
refraction texture — a dark distortion map — draws as a solid dark surface. The
ancestor ghost's `RefractiveSphere` is a **90-unit radius** sphere, reported in
game as a large black ball covering the actor.

Vanilla census of `meshes/magic`: **93** shapes set `slsf_1_refraction`.

**Split them by vertex alpha — it decides every other flag.** A refraction
surface fades at the silhouette either by per-vertex alpha or by the shader.
The 80 shapes WITH vertex colors are split on every flag (fire 37/43, cast
42/38, zwrite 72/8, median strength **0.0**) because the vertex ramp does the
work — `boundswordencheffects`'s `RefrectHit01` ramps alpha 0.0→1.0. The **13
WITHOUT** vertex colors are unanimous: fire refraction **13/13**, cast shadows
**13/13**, receive shadows **13/13**, z-write 10/13, median strength **0.25**.
`slowtimehiteffect`'s `GeoSphere01` is that case exactly — a refraction sphere
with no vertex data.

**TES4 belongs in the unfaded group.** Of 20 `refractF` shapes across
`Oblivion.esm` and Morroblivion's creatures, 13 carry no vertex colors and the
other 7 carry alpha that is flat **1.0** (distinct=1) — no gradient anywhere,
so there is no rim fade to port and setting `slsf_1_vertex_alpha` would only
risk erasing the shape. Hence the single unfaded path.

Copying `RefrectHit01` instead — shadows off, z-write off, strength 0.6, no
fire bit — was the FIRST attempt and was reported in game as the sphere
refracting but showing a hard visible outline. That shape has a vertex ramp;
ours does not, so it was the wrong comparison group.

**The distortion lives in the NORMAL map, not the diffuse.** nif.xml calls bit
15 "Use normal map for refraction effect", and all **93** vanilla refraction
shapes bind a real normal map — none uses a flat default. Oblivion's
`magic/refract.dds` is a **1x1** 132-byte placeholder while `refract_n.dds` is
a real **128x128 DXT1**, so binding the flat default threw the effect away
entirely. The creature pipeline now passes `master_texture_roots` as
`tex_fallback`, without which a Morroblivion creature cannot resolve a texture
that lives in Oblivion.esm's tree (Morroblivion ships no `textures/magic/`).

**Strength is not authored in TES4.** `Refraction Strength` is `#BSVER# #GT# 14`
(FO3 and later), so Oblivion stores none and the value comes from vanilla's
unfaded group: median **0.25** (range 0.15–0.6 over 13). The whole-corpus
median of 0.6 is the wrong number — it is dominated by vertex-faded shapes.

Note the shape keeps `BSLightingShaderProperty` and needs no `NiAlphaProperty`:
vanilla's refraction shapes carry neither an effect shader nor alpha blending.

## The emissive color, and when `own_emit` is cleared
<a id="emissive-own-emit"></a>

**Code:** `_set_emissive` in `asset_convert/nif/geometry_shader.py`

Skyrim **multiplies** the emissive color by `emissive_multiple`, so a zero
there leaves the surface black no matter what an animation does to the color.
Vanilla shapes carrying an emissive color controller set `own_emit` in
**133 of 133** cases and never pair it with a 0 multiple, so the multiple is
stamped to 1.0 whenever the flag goes on.

The flag is CLEARED on a shape with no emissive color and no emissive
animation. The default preset turns `slsf_1_own_emit` on for every shape, and
leaving it on for ordinary geometry costs overdraw for a contribution that is
always (0,0,0).

The animation test matters independently of the color: a shape whose emissive
is driven by a controller can author (0,0,0) as its FIRST key and still light up
later, so the flag must survive an all-zero starting color.

## Texture slots are never left empty
<a id="texture-slots-never-empty"></a>

**Code:** `_fill_texture_slots` in `asset_convert/nif/geometry_shader.py`

**Slot 0, the diffuse.** A shape with no `NiTexturingProperty` at all is legal
in Oblivion, which renders it with the flat `NiMaterialProperty` color. Skyrim
has no such mode: `BSLightingShader::SetupMaterial` binds the diffuse
UNCONDITIONALLY (SkyrimSE.exe 1.6.659 `+0x1412138` → `+0x1415790`,
`mov rax,[rdx+0x48]` with `rdx = material->diffuse`), so a null diffuse is an
access violation the moment the shape is drawn. Vanilla never exercises that
path: **0 of 772** `BSLightingShaderProperty` shapes sampled across Skyrim's own
meshes ship an empty slot 0.

`white.dds` is Skyrim's own neutral texture, so multiplying it by the material
color already carried across reproduces Oblivion's flat shading exactly.

**Slot 1, the normal.** The normal path is DERIVED from the diffuse, so it is a
guess rather than authored data, and Oblivion content frequently has no `_n`
beside the diffuse at all. Measured on the shipped tree before this check
existed: **1904 of 20696** lighting shaders (**9.2%**) named a normal map with
no file behind it, all fabricated at this one site.

Skyrim null-checks slot 1 (`+0x1412144`, `test rax,rax / je`), so a dangling
path does not crash — it renders with NO normal, which vanilla never does (**0
of 8740** shapes sampled across architecture, dungeons, clutter and weapons ship
an empty slot 1). Those are pointed at the shared flat normal instead, which
carries the same constant specular mask the texture stage bakes into maskless
maps.

The stand-in is the LAST resort: `resolve_normal_for` first tries the variant's
own `_n`, then the one its base name shares across color variants. When the
shape genuinely has no texturing property, slot 1 stays empty on purpose —
vanilla ships normal-less shapes, so a fabricated `_n` would only dangle.

## Contents

- [The defect this replaced](#defect-this-replaced)
- [Vanilla census: 80 is real, the tail is not](#vanilla-census-80-real-tail)
- [Oblivion's glossiness does not transfer](#oblivions-glossiness-does-not-transfer)
- [The rule: slot 1's alpha decides](#rule-slot-1s-alpha-decides)
- [Interaction with landscape_normals](#interaction-with-landscapenormals)
- [What the source does NOT carry](#what-source-does-not-carry)
- [Known gaps](#known-gaps)
- [Prior art: the guards PGPatcher applies (read 2026-08-20, not adopted)](#prior-art-guards-pgpatcher-applies)
- [The default specular mask value (64/255)](#default-mask-alpha)
- [The Oblivion property enums the converter keys off](#ob-enums)
- [Animated texture transforms: the NiTextureTransformController map](#texture-transform-controller-map)

Measured 2026-08-19/20 with `tools/shader_value_census.py`,
`tools/mesh_identity_census.py` and `tools/bc4_preview.py`. Every number here
was computed in that session; nothing is quoted from a wiki without a
measurement beside it.

## The defect this replaced
<a id="defect-this-replaced"></a>

Our output assigned **no** material values at all, so every shape shipped at
pyffi's defaults. Measured over 400 output meshes / 3931 lighting shaders:

| | our output | vanilla Skyrim (type 0) |
|---|---|---|
| glossiness | **0.0 — 100%** | 80.0 median **and** mode |
| specular color | **black — 100%** | white 56.1%, black 3.0% |
| specular strength | 1.0 | 1.0 mode (44.7%) |
| `SLSF1_Specular` | **on — 100%** | — |

Glossiness 0 with a black specular color and the flag on is the combination
that reads as a flat blown-out sheen.

## Vanilla census: 80 is real, the tail is not
<a id="vanilla-census-80-real-tail"></a>

Random 1500-mesh sample of `references/Skyrim Meshes`, 4693 lighting shaders.
**Glossiness is a property of the shader TYPE, not of the asset category:**

| type | shaders | median | modal |
|---|---|---|---|
| 0 Default | 2961 | **80.0** | 80.0 (1333) |
| 6 HairTint | 761 | 10.0 | 10.0 |
| 16 EyeEnvmap | 233 | 479.0 | 479.0 |
| 4 FaceTint | 223 | 33.0 | 33.0 |
| 5 SkinTint | 143 | 64.0 | 64.0 |
| 2 GlowMap | 39 | 80.0 | 80.0 |

Within type 0, 80 is the modal value in **12 of 15** top folders — architecture
613/1092, dungeons, clutter, armor, clothes, landscape, weapons, traps, and
100% of plants, furniture and animobjects. So **no per-category table is
needed**; what actors need is the correct shader TYPE, which we do not yet
write (see gaps).

🔴 **Do not copy the vanilla distribution, only its mode.** Arcane University
puts typical specular strength at 0.25–1.0; Bethesda's own meshes ship 2.2
(263×) and 3.0 (152×). The spread is per-artist noise, not a system.

## Oblivion's glossiness does not transfer
<a id="oblivions-glossiness-does-not-transfer"></a>

Nehrim source, 4031 `NiMaterialProperty` from 1200 meshes:

| | |
|---|---|
| glossiness median | **10.0**, with 59.4% sitting on exactly 10 |
| specular color | (0.9,0.9,0.9) 39.9%, black 32.1%, white 13.1% |

10 is an authoring default, not a chosen value — and 10 in Skyrim is what HAIR
uses, a very wide highlight. Carrying it across would give every Nehrim surface
a hair-like sheen. **Glossiness is written as 80, never copied.**

The specular colors are equally uninformative: only 170 of 4038 shapes carry
`NiSpecularProperty`, and without it Gamebryo renders no specular at all, so
the color on the other 95.8% was never used.

## The rule: slot 1's alpha decides
<a id="rule-slot-1s-alpha-decides"></a>

`asset_convert/texture/spec_mask.py`. Both engines read the normal map's alpha as the
specular mask — Arcane University's slot table says so for Skyrim ("Black is
zero reflection, white full") and `landscape_normals.py` already relies on it
for terrain. It is the one piece of Oblivion's material authoring that
transfers intact and changes how a surface looks.

**Why it needs no height-style classifier.** A diffuse's alpha is ambiguous —
transparency OR height — which is why `parallax.classify_alpha` weighs mid-tone
ratios and level counts. Slot 1's alpha has no competing meaning. So:

| verdict | condition | `specular_strength` |
|---|---|---|
| `mask` | alpha present, >2 levels, amplitude ≥ 8 | **1.0** |
| `no_alpha` | DXT1 / uncompressed | 0.25 |
| `flat` | amplitude < 8 (a tool that saved DXT5 it never needed) | 0.25 |
| `binary` | two values — on/off, more likely a stray mask | 0.25 |

`SLSF1_Specular` stays **on in every case**. Switching it off would split the
world into shiny and dead surfaces along a line the player cannot read, and a
missing alpha reads as 1.0 in Skyrim, so the strength is what holds it back.

🔴 **Glossiness cannot do this job.** AU defines it as the INVERSE WIDTH of the
highlight: a low value gives a broad sheen over the whole surface, a high one a
small bright hotspot. Neither is "barely shines". Intensity is
`specular_strength`.

`NiSpecularProperty` is deliberately ignored: too rare (4.2%) to carry the
decision, and a shape with the property but no mask would render a uniform
sheen over its whole surface — worse than none.

### The yield tracks material reality

Share of `_n` maps carrying an alpha channel, per area:

```
armor 94.5%  weapons 89.9%  rocks 75.0%  dungeons 67.9%  nehrim 63.6%
clutter 45.9%  landscape 45.9%  architecture 38.1%  plants 15.3%
```

That ordering is not noise — metal and leather reflect, plaster and leaves do
not. The artists drew masks where the material warrants one, which is why the
uneven distribution is authored intent rather than a defect to smooth over.

## Interaction with `landscape_normals`
<a id="interaction-with-landscapenormals"></a>

`textures/…/landscape` serves BOTH terrain (from `LAND` records, which has no
mesh and therefore no shader property we could set) and object meshes such as
rocks. Classified with our own rule, Nehrim's 135 landscape normals are
**58 `mask`, 73 `no_alpha`, 4 `binary`**.

| case | texture stage | mesh stage | result |
|---|---|---|---|
| mask (58) | untouched — the fix only rewrites DXT1 | strength 1.0 | authored mask, applied once ✅ |
| DXT1 (73) | → DXT5 with alpha 32/255 = 0.125 | strength 0.25 | 0.03 — **doubly damped** |
| binary (4) | untouched | strength 0.25 | 0.25 |

The masked case resolves itself correctly with no extra work, and the fix is
mod-safe by construction: it bails on anything that is not DXT1, so a mod's
real specular map can never be overwritten.

The DXT1 double-damping is a known, deliberately unfixed overlap — both stages
independently solve the same problem. Left in place pending an in-game look,
because a too-matte rock is far less noticeable than shiny ground. If it does
show, the precise fix is to narrow `landscape_normals` to the textures `LTEX`
records actually name, so each stage only touches what it owns.

## What the source does NOT carry
<a id="what-source-does-not-carry"></a>

Measured on 1200 Nehrim meshes / 4038 shapes:

| signal | present | status |
|---|---|---|
| `NiStencilProperty` → `SLSF2_Double_Sided` | 3.5% | mapped |
| emissive color | 7.6% | mapped |
| vertex color `lighting_mode=0` → effect shader | 1.3% | mapped |
| `apply_mode=4` → parallax | 6.7% | mapped (opt-in) |
| **glow texture slot** | **0.7%**, 588 `_g` files exist | **dropped** |
| `apply_mode=3` (HILIGHT) | 3.1% | unexplained |

No dark, detail, gloss or decal slots. No `BSShader*Property`. **No `_e`
cubemaps at all**, so environment mapping cannot be reconstructed — the 12 `_m`
masks have nothing to mask.

Root node type is not a discriminator either: 89% of Oblivion roots are plain
`NiNode`. `BSFadeNode` / `BSLeafAnimNode` / `BSTreeNode` are Skyrim-side
distinctions the converter must CHOOSE, not read. BSXFlags describe physics and
animation, not surface.

The Havok material on the collision body does describe what a surface is made
of, and `collision.py` already translates it — but only for physics. Note it is
per rigid BODY, not per shape: a first census read 30% "Skin" until it turned
out all of it came from one goblin ragdoll skeleton with 18 bodies.

## Dropping the alpha property on a parallax shape
<a id="hilight2-alpha-dropped"></a>

**Code:** `process_geometry` in `asset_convert/nif/geometry_shader.py`

Oblivion's `APPLY_HILIGHT2` (4) is its PARALLAX switch: the diffuse's alpha
channel is a HEIGHT FIELD, not a transparency mask. Skyrim reads that same
channel as plain transparency, so the SI mania/dementia rocks render see-through,
and where the surface is low they disappear completely — `seisland`'s body
texture `mrock01.dds` averages alpha 133, i.e. the whole island ~50% transparent.

**These are provably not cutout masks.** 97–99% of texels are PARTIALLY opaque
with almost no fully-transparent region (`mrock01` **97.9%** ≥ 1 but only **22%**
≥ 254; `DMRockSideRoot01` **99.0%** ≥ 1 and **0%** ≥ 254) — mid-tone-dominant,
which is exactly a height map's profile and not a cutout's.

Vanilla agrees on the remedy: across **600** landscape/clutter meshes,
**1088/1313** shapes ship NO `NiAlphaProperty` at all, and the commonest value on
the rest is `0x12EC` (test, blend OFF). Vanilla rock simply does not alpha-blend.
So the property is dropped and the rock renders solid. This is right whether or
not `--parallax` is on; with it, the height also survives as a real slot-3 map.

**Only the parallax case is touched.** Genuine transparency (gems, bottles,
curtains, potion liquids) ships MODULATE/HILIGHT and keeps its alpha exactly as
authored.

**A shape that KEEPS its alpha property is evidence about the texture.** It reads
the diffuse's alpha as blend weight or test threshold — either way as opacity —
so the channel is not a height field here, whatever the texture-level classifier
decided. That diffuse must keep its alpha and may not be stripped to BC1 later.
Measured on the author's Nehrim parallax mod: **1** shape of **39,201**, but the
converter runs on plugins nobody has measured.

### <a id="detail-overlay-diffuses"></a>Detail-overlay diffuses get a LOD-only copy

**Code:** `redirect_overlay_diffuses` in `asset_convert/lod/lod_far_gen.py`

`APPLY_HILIGHT2` marks a diffuse whose ALPHA the source reads as a per-texel
DETAIL BLEND WEIGHT, not as transparency. Two readers want incompatible things
from that one channel:

- the **full-size mesh still needs that channel as authored** — confirmed in
  game, where removing the LOD-side fix outright brought the bug straight back;
- **object LOD must not have it.** LODGen stamps `slsf_2_lod_objects` on every
  baked shape and the LOD object shader samples diffuse alpha as OPACITY, so
  `RockGreatForest645` renders solid up close and see-through at distance.

So one file cannot serve both, and two tempting fixes are both wrong:

| attempt | why it fails |
|---|---|
| flatten the plugin's texture in place | destroys the blend weight the full mesh reads. **Shipped, and reverted before it built** |
| remove the fix entirely | the transparency bug came straight back. **Confirmed in game** |
| shadow a copy at the SAME path from the LOD mod | two mods hold one path, so install order decides. PIL wrote it **uncompressed and mipless**: 200 files, **401.6 MB** (~100 MB as DXT5), and 192 plugin-owned paths inside `AutoConvertLOD`, which `drop_staged_meshes` (meshes only) never reclaimed |

The fix is a **distinct path**, applied while the `_far` mesh is generated:
`redirect_overlay_diffuses` writes `<name>_lod.dds` beside the original and
repoints the LOD mesh's slot 0 at it. Every file then has exactly one reader --
the plugin's texture keeps its alpha for the full mesh, the `_lod.dds` copy is
opaque for the tiles, and nothing is shadowed or swept. No `.bto` needs
patching: LODGen bakes whatever path the mesh names.

The manifest reaches it unchanged from the mesh stage: `_record_overlay`
(`geometry_shader.py`) collects every `APPLY_HILIGHT2` diffuse into
`stats['overlay_diffuses']`, `convert_meshes` writes it as
`texture_prune.OVERLAY_MANIFEST_NAME` in the plugin's EXPORT asset dir, and
`create_lod._supplier_overlay_dirs` hands those dirs to `generate_lod`
index-aligned with `far_nif_dirs`, so `_overlays_by_asset_dir` can pair each
plugin's output tree with its own set. Keys are the CONVERTED path
(post-`tes4\` rewrite, forward slashes, lowercased), which is what the shipped
mesh references.

It runs from `_write_decimated`, beside `strip_parallax`, so the coarser
`_far8`/`_far16` tiers are covered by the same call. `strip_alpha_to_bc1` does
the conversion losslessly -- a DXT3/DXT5 block is 8 bytes of alpha followed by 8
bytes of color in exactly BC1's layout, so the color half is copied verbatim and
**every mip survives**; an existing `_lod.dds` is reused, so a re-run and a
parallel worker are both safe.

Verified on `anvilaltar01.nif` (10 slot-0 diffuses, Oblivion.esm): the **2**
manifest-listed overlays were redirected and copied (DXT5 → DXT1, 9 and 10 mips
preserved, each exactly half the source bytes), every original byte-identical
afterwards, and `texture_prune.refs_from_assets` harvested both `_lod.dds`
references back out of the generated `_far.nif`, so a packed build keeps them.

The discriminator is the AUTHORED apply mode, never measured alpha. Two of the
diffuses left alone on that same mesh — `rfdunxcolmlitebase01`,
`rfdunxcolmdark003` — are **also DXT5**: alpha alone cannot tell an overlay from
a cutout mask. Gating on "has an alpha channel" instead of the manifest was
measured across 4,000 generated `_far` meshes and would have copied **2,044 of
3,557** diffuses (~678 MB of sources) rather than the authored **64**
(Oblivion.esm) and **128** (Nehrim.esm).

The key is the CONVERTED path (post-`tes4\` rewrite), because that is what the
shipped mesh — and therefore the baked `.bto` tile — actually references.

## Deriving a normal map: the base-name fallback
<a id="normal-base-name-fallback"></a>

**Code:** `resolve_normal_for`, `_resolve_map_for` in `asset_convert/nif/shaders.py`

Oblivion does not store the normal's path — it appends `_n` to the diffuse — and
when the variant's own `_n` is absent it falls back to the BASE name, the part
before the last `_`. That is intended engine behaviour, confirmed by the project
owner from their own research (2026-08-26); it is why `BrumaWoodPost_Dark.dds`
and `BrumaWoodPost_Grey.dds` both render with `BrumaWoodPost_n.dds` and ship no
normal of their own.

Deriving from the full name alone invents `BrumaWoodPost_Dark_n.dds`, which
exists nowhere; dropping straight to a flat stand-in would discard a real normal
sitting right beside it.

Measured over the merged Nehrim texture tree: of the variants whose own `_n` is
missing, **201** have one under the base name, against **48** that ship their own
alongside the base's — and those 48 are unaffected, because the variant's own is
tried FIRST. The suffixes involved are color and state words throughout
(`_dark`, `_black`, `_red`, `_harvested`, `_haunted`, `_01`), i.e. variants of one
surface rather than different materials.

Only ONE separator is stripped, and only when the result actually exists on disk
— this never guesses a path into being.

**The rule is not specific to normal maps.** It applies to every derived map,
glow (`_g`) included, which is why `_resolve_map_for` takes the suffix as a
parameter. Keeping it generic means the next slot inherits it instead of
reinventing it — the failure mode this replaced, where `_n` had the rule and
nothing else would have.

`_normal_exists` resolves through the master fallback for the same reason
[`resolve_source_texture`](#texture-fallback-roots) does: a mod's mesh usually
names a normal that lives in its BASE's tree, and without the fallback every one
of those would look absent and get needlessly replaced by the stand-in.

## Parallax is never set on a distant-LOD tier mesh
<a id="parallax-not-on-lod-tiers"></a>

**Code:** `_is_lod_tier_mesh`, `apply_parallax` in `asset_convert/nif/shaders.py`

Three independent reasons, any one of which is sufficient.

**It is invisible.** A `_far.nif` is only ever drawn at LOD distance, where a
per-pixel height offset resolves to nothing.

**It does not survive.** The LOD stage regenerates these from the full model
with `force_regen_generated=True`, and that path knows nothing about parallax —
it drops the vertex colors the heightmap shader needs while leaving shader type
3 in place. `parallax_check.py verify` found exactly that: **60** malformed
shapes, every one in a `_far`/`_far8`/`_far16` mesh, all reported as "no vertex
colors (renders unlit-black)".

**It made the output ORDER-DEPENDENT**, which is the real defect: run meshes
then LOD and the tier meshes come out clean; run LOD then meshes and they keep a
half-built parallax shape. The shape count moved **1495 → 1555** purely on that
ordering.

Skyrim's heightmap shader also needs vertex colors present or the shape renders
unlit-black, so `apply_parallax` synthesises all-white ones where the source has
none — measured on Nehrim, **848 of 1551** converted shapes have none of their
own. All-white is neutral and is what the in-game test shipped.

## Specular strength is uniform, and the modulation lives in the texture
<a id="spec-strength-uniform"></a>

**Code:** `SPEC_STRENGTH` in `asset_convert/nif/shaders.py`

EVERY shape gets the same specular strength. Where a source has no usable mask,
`landscape_normals.normalize_specular_alpha` bakes a constant 64/255 into the
texture instead — 64/255 = 0.251, i.e. EXACTLY the per-mesh 0.25 this used to
write, so the two encodings render identically.

**The point is not the pixels, it is who can change them afterwards.** A
strength baked into 20,000 NIFs is a TRAP for anyone who later ships real
specular maps: their good mask would be multiplied by 0.25, and fixing it means
editing every mesh rather than dropping in a texture. The alpha is overridable
by definition. Uniform 1.0 is also vanilla's mode (**44.7%**).

It also retires the double damping recorded in `shader_value_mapping.md`:
landscape was 0.125 (alpha) × 0.25 (strength) = 0.03, and is now 0.125.

## Texture classification is cached per worker
<a id="texture-classification-caches"></a>

**Code:** `_PARALLAX_ALPHA_CACHE`, `_SPEC_MASK_CACHE` in
`asset_convert/nif/shaders.py`

Classifying either a diffuse alpha or a normal map's mask means a full scan of
the texture's top mip, and one texture is shared by many shapes: **2359** flagged
shapes share only **130** diffuse textures, so without the cache the same DDS
would be decoded eighteen times over.

Both are keyed on the RESOLVED ABSOLUTE PATH, so they hold per worker process
and stay deterministic — a key derived from the mesh-relative path would collide
across plugins whose trees resolve differently.

`_plan_parallax` counts its skips per CATEGORY rather than into one "skipped"
counter: two thirds of the flagged textures have nothing to carry, and the build
log has to say WHY or the next person re-measures all 130 of them. `has_spec_mask`
does the same, because "no specular" has three quite different causes.

## Known gaps
<a id="known-gaps"></a>

- **Actor shader types.** We write type 0 for skin, hair and faces; vanilla
  uses 4/5/6/16 with quite different glossiness. This is the real actor defect,
  not the glossiness value.
- **Glow.** 588 `_g` textures exist and slot 2 has a direct equivalent
  (type 2 + `SLSF2_Glow_Map`, with the env-map flag off — AU says the two are
  mutually exclusive). Currently dropped entirely.
- **Trees.** Leaf flutter needs a `TREE`/`FLOR` record *and* a `BSLeafAnimNode`
  root *and* three flags; skinned branches crash on load if the bone arrays are
  empty. Vanilla puts soft lighting on 102 of 364 tree shaders with
  `Lighting Effect 1` around 7.5. Deferred.

## Prior art: the guards PGPatcher applies (read 2026-08-20, not adopted)
<a id="prior-art-guards-pgpatcher-applies"></a>

[PGPatcher](https://github.com/hakasapl/PGPatcher) has been patching parallax
across Skyrim load orders for years, and its wiki lists exactly when it
**refuses** to. We set parallax on `APPLY_HILIGHT2` + usable height data with a
single guard (`_is_lod_tier_mesh`), so this list is worth measuring against:

* mesh has attached havok — `BSBehaviorGraphExtraData` present
* shape is skinned
* shape has an `NiAlphaProperty`
* shader flags `decal` or `dynamic_decal`
* shader flags `soft lighting`, `rim lighting`, `back lighting`, or
  `anisotropic lighting`
* the mesh is used as a `GRAS` record, or has single-pass `MATO`
* shader type before patching is not `default`, `parallax`, or `environment`

Two of those we satisfy incidentally: the alpha property is dropped for
HILIGHT2 shapes anyway, and we never write the lighting-effect flags. **Skinned
shapes and havok-graph meshes are unchecked** — whether we currently set
parallax on any is unmeasured, and that is the measurement to run before
copying the rule.

Its Complex Material patcher also sets `specular strength` to 1.0, but only
where a CM texture exists — a richer texture type Oblivion content does not
have, which is why the normal-alpha rule here has no overlap with it.

## The default specular mask value (64/255)
<a id="default-mask-alpha"></a>

Constant mask written where a normal map carries none.  64/255 = 0.251 is
EXACTLY the `specular_strength` the mesh stage used to write for a maskless
shape, so moving the value out of the mesh and into the texture is visually
neutral -- and from then on a modder who ships a real mask simply overrides
it, instead of having to discover and undo a shader parameter baked into
thousands of NIFs.

## The glow shader: derived, not read
<a id="glow-shader"></a>

**Code:** `apply_glow` in `asset_convert/nif/shaders.py`

Oblivion does not require the NIF to name its glow texture. It derives
`<diffuse base>_g.dds` exactly as it derives `_n`, so most glowing shapes name
nothing at all: measured over a random 1200-mesh sample of Nehrim, **227** shapes
have a `_g` on disk for their diffuse and only **31** name it in
`NiTexturingProperty`'s glow slot. Reading the slot alone therefore missed **86%**
of the glow content. The named path still wins when present — it is authored —
and derivation is the fallback, base-name aware via `_resolve_map_for`.

**Without this the conversion is not merely incomplete, it is WRONG.** Arcane
University on Emissive Color: "if the shader type is not 'Glow Shader', it will
make the WHOLE MESH glow", while the glow shader "allows per-texel glow …
applied additively using the color map in texture slot 2". So a rune stone whose
glyph should glow was flooding its entire surface with the emissive color.

Slot 2 and shader type 2 come from AU's texture-slot table: "2 | Glow | Glow map
/ Skin Tint | none | `_g` / `_sk.dds` | BC1". The environment-map flag is cleared
alongside — AU: "The environment map shader is incompatible with glow mapping."

**Emissive is defaulted to white when the source left it black.** Of **60** type-2
shapes sampled across Skyrim's own meshes, ALL set `own_emit` and carry the glow
flag, **55 of 60** carry a slot-2 texture, the modal emissive color is white (21)
and the modal multiple is 1.0. Skyrim MULTIPLIES the glow map by the emissive, so
leaving it black would keep the map and show nothing.

**Glow BLOCKS parallax.** `skyrim_shader_type` holds ONE value, so type 2 (glow)
and type 3 (height) cannot coexist. Glow wins: the glow map is authored content
while our height map is derived from the diffuse, and a surface that was meant to
glow and does not is far more noticeable than one that is merely flat.

A named glow path that resolves nowhere is counted and dropped, never invented —
absence of glow is the neutral state.

## Shader float controllers: the flags Oblivion does not set
<a id="shader-float-controller-flags"></a>

**Code:** `attach_tex_transform_ctrls` in `asset_convert/nif/shaders.py`

Vanilla chains one `BS*ShaderPropertyFloatController` per animated UV channel
through `next_controller` — `fxwaterfallthin512x128` does U Scale → V Offset →
U Offset — so the re-emit mirrors that, and anything already on the shader (the
flip-book U-Offset controller, say) is kept at the END of the chain.

`flags = 0x48` is `Active | Compute Scaled Time`, the value on every vanilla
shader float controller. **Oblivion ships 0x08 (Active only), and without the
scaled-time bit the curve does not advance.** The source's cycle bits (0x06) are
preserved so CLAMP/REVERSE loops survive.

The `NiFloatData` is reused as-is: both engines interpret the curve as a UV-space
offset/scale over time, so the Oblivion keys (waterfall V 0.0 → −2.0 over 3.3s)
are already correct. `NiFloatInterpolator.float_value` takes the vanilla
"use data" sentinel (−FLT_MAX).

### <a id="niuvcontroller-has-no-rtti"></a>NiUVController has no RTTI in Skyrim

`NiUVController` is Oblivion's UV-scroll animation (Morrowind's Ghostfence
shimmer, `ex_gg_fence*`). **SkyrimSE.exe has no `NiUVController` RTTI at all** —
searching its RTTI for "NiUV" returns only `NiUVData` — so `NiStream` cannot
construct the block, and a link to that slot hands `NiPointer` a non-NiObject
pointer: the engine then does `lock cmpxchg` on a "refcount" inside read-only
`.rdata` and takes an access violation while loading the mesh.

The curve itself survives. `NiUVData.uv_groups` holds the same U/V offset and
scale key groups a `NiTextureTransformController` would, so each populated group
becomes one `BS*ShaderPropertyFloatController` by the same path. The harvest must
therefore run BEFORE the strip that removes the dead controller.

Harvesting skips `TT_ROTATE` (no Skyrim equivalent), non-base texture slots
(Skyrim shaders expose one UV transform, applied to all maps), and curves that
cannot be translated — a `NiBlendFloatInterpolator` is driven by a
`NiControllerManager` sequence rather than inline keys (**46/127** in Nehrim, all
on skull/fireball meshes), and a single key is a constant, not an animation.

## The Oblivion property enums the converter keys off
<a id="ob-enums"></a>

Seven `NiTexturingProperty` / `NiVertexColorProperty` / `NiAlphaProperty`
values decide which Skyrim shader a shape gets and which channels survive.
They are declared as constants in `nif_converter.py`; the semantics are here.

### `_APPLY_HILIGHT2 = 4` — Oblivion's parallax switch

`NiTexturingProperty.apply_mode = APPLY_HILIGHT2` means the diffuse's alpha
channel holds a **height field**, not transparency and not a blend weight.
Both engines' mechanism is written up in
[asset_convert_texture.md](asset_convert_texture.md). Skyrim reads that same
channel as plain opacity, so the alpha property must be dropped either way
(the alpha handling in `process_geometry`); with `--parallax` the height is
additionally carried across into a slot-3 map.

### `_LIGHTING_EMISSIVE_ONLY = 0` — the unlit-FX declaration

`NiVertexColorProperty.lighting_mode`: `LIGHTING_E` (0) = "emissive only", the
surface ignores scene lighting entirely. That is Oblivion's declaration of an
UNLIT FX surface and maps onto Skyrim's `BSEffectShaderProperty`.
`LIGHTING_E_A_D` (1) — ordinary lit geometry — maps onto
`BSLightingShaderProperty`. This is the shader choice in `process_geometry`.

### `_MATERIAL_COLOR_EMISSIVE = 3`

`NiMaterialColorController.target_color`: which material channel the curve
drives. 3 = `TC_SELF_ILLUM` (emissive) — the only one with a Skyrim analogue.

### `_SHADER_COLOR_EMISSIVE = (1, 0)` — (Lighting, Effect)

`BS*ShaderPropertyColorController.type_of_controlled_color`; the two shaders
number this differently. Vanilla census of 361 meshes carrying a shader
ColorController: Lighting uses 1 for emissive (124 blocks, vs 5 at 0 which is
Specular); Effect uses 0 (46 blocks).

### `_SHADER_ALPHA_VAR = (12, 5)` — (Lighting, Effect)

`BS*ShaderPropertyFloatController` variable for opacity. Per
`references/nif 0.10.0.0.xml`: Lighting 12 = "Alpha", Effect 5 = "Alpha
Transparency"; both appear in the vanilla float-controller census.

### `_ALPHA_BLEND_ENABLED = 0x0001`

`NiAlphaProperty.flags` bit 0 = alpha blending enabled. The FX path keys off
"does this surface alpha-blend", which is the discriminator vanilla itself
uses for the soft-particle depth fade (`_apply_fx_soft_effect`).

### `_DEFAULT_DIFFUSE_TEXTURE = Textures\white.dds`

Diffuse for a shape whose Oblivion source carries no `NiTexturingProperty`.
Skyrim's lighting shader dereferences the diffuse **without a null check**, so
"no texture" is not representable. `white.dds` is vanilla Skyrim's own neutral
texture (shipped in the SSE BSAs), so the material color we carry across shows
through unmodified.

## Animated texture transforms: the NiTextureTransformController map
<a id="texture-transform-controller-map"></a>

Oblivion animates UVs with `NiTextureTransformController`, one controller per
operation, hung on the `NiTexturingProperty`. Skyrim has no such block: the
equivalent is a `BSLightingShaderPropertyFloatController` /
`BSEffectShaderPropertyFloatController` driving a named shader variable. The
constant table maps each Oblivion transform operation onto the
`(Lighting, Effect)` variable pair that reproduces it:

| Operation | Lighting var | Effect var |
|---|---:|---:|
| `TRANSLATE_U` | 20 (U Offset) | 6 |
| `TRANSLATE_V` | 22 (V Offset) | 8 |
| `SCALE_U` | 21 (U Scale) | 7 |
| `SCALE_V` | 23 (V Scale) | 9 |

Vanilla `FXWaterfallThin512x128` chains U Scale + V Offset + U Offset exactly
this way. **`TT_ROTATE` has no Skyrim equivalent** — neither shader exposes a
UV rotation float — so it is dropped, not faked.

### `NiFlipController` — dead in Skyrim, rebuilt as an atlas

Fire and effect quads in Oblivion animate through multiple discrete textures
using `NiFlipController` on the `NiTexturingProperty`. That block is **dead in
Skyrim: 0 of 17,216 vanilla meshes use it.** The Skyrim equivalent is a
frame-strip atlas texture plus a `BSEffectShaderPropertyFloatController` on
"U Offset" (var 6) with stepped (CONST) keys. The converter composes the
source frames into a horizontal-strip DDS and emits that controller; see
`asset_convert/nif/flipbook.py`.
