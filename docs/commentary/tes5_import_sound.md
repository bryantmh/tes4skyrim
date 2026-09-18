# tes5_import/record_types/sound.py — SOUN, SNDR and SOPM

**Code:** `tes5_import/record_types/sound.py`, `tes5_import/record_types/actors.py`

Oblivion stores a sound's falloff distances on the SOUN itself (SNDX). Skyrim
splits the same data across two records: a **SNDR** (the sound descriptor) whose
`ONAM` points at a **SOPM** (the sound output model) carrying the attenuation
curve. Vanilla ships one SOPM per distance — `SOMMono00400`, `SOMMono03000`,
`SOMMono10000`, … — so `_build_sopm` mints one per distinct TES4 distance pair
and caches it rather than pinning every converted sound to a single model.

## Contents

- [SOPM NAM1 is little-endian — do NOT reverse it](#sopm-nam1-little-endian)
- [SOPM defaults, and why never `_SOPM_2D` for a 3D sound](#sopm-defaults)
- [The SNDR map is filled during Phase 3, not reserved up front](#sndr-map-phase-3)
- [GNAM audio category: the 2D test is bit 6 ALONE](#gnam-2d-test-bit-6)
- [ONAM: a 3D sound with max=0 must not take the 2D model](#onam-3d-max-zero)
- [SNDX/SNDD field scaling](#sndx-field-scaling)
- [ANAM paths: relative form, and directory-valued FNAM](#anam-paths)
- [set_sound_source_dir takes the ASSET root](#asset-root-not-record-dir)

## SOPM NAM1 is little-endian — do NOT reverse it
<a id="sopm-nam1-little-endian"></a>

`NAM1` is `Flags(u8) unknown[2] ReverbSend%(u8)`. Flag `0x01` is **Attenuates
With Distance** and is required — without it the sound plays at full volume
everywhere.

🛑 **Do not "fix" the pack order to `(30, 0, 0x01)`.** The Skyrim.esm dump prints
NAM1 as a little-endian u32, so a dumped `NAM1=1E000001` is the bytes
`01 00 00 1E` on disk — exactly what `struct.pack('<BHB', 0x01, 0, 30)` writes.
Reversing it on 2026-08-05 made **every sound in the game play far too loud**
(confirmed in-game), because it moved the reverb percentage into the flags byte.

## SOPM defaults, and why never `_SOPM_2D` for a 3D sound
<a id="sopm-defaults"></a>

| Constant | Value | Source |
|---|---|---|
| `_SOPM_2D` | `0x000B5183` (`SOMDialogue2D`) | non-attenuating; menu/2D sounds only |
| `_DEFAULT_3D_MAX_DIST` | 1800.0 | vanilla's most common mono model (`SOMMono01800`, 285 SNDRs) |
| `_SOPM_ANAM_LEAD` | `809dfa00` | most common in vanilla (24/69) |
| `_SOPM_ANAM_TAIL` | `000000` | most common in vanilla (56/69) |
| `_SOPM_CURVE` | 100/50/20/5/0 | the standard falloff shared by every `SOMMono*`/`SOMStereoRad*` |

A 3D sound that authored no max attenuation distance takes
`_DEFAULT_3D_MAX_DIST`, **never** `_SOPM_2D`: pointing a positioned sound at the
non-attenuating model is what produced the worldspace-wide siege-engine thump.

`MNAM` is 0 for Uses HRTF (mono) and 1 for Defined Speaker Output (stereo).

## The SNDR map is filled during Phase 3, not reserved up front
<a id="sndr-map-phase-3"></a>

`_SNDR_FOR_SOUN` maps a TES4 SOUN FormID (low 24 bits) to the SNDR its
conversion produced. It is filled **during** Phase 3 and read afterwards to
patch the actor records that reference it.

An earlier version reserved those ids in a Phase 0 pre-pass so actors could
embed them directly. That allocated ~1,100 FormIDs before anything else and so
**shifted every other generated id** (OTFT, ARMA, TXST, …), which silently
invalidated the separately-built `Slot44 Patch.esp` — its 818 ARMO / 233 ARMA
overrides are matched to the master **by FormID**, so NPCs lost their armor.
Allocation order is a compatibility contract with anything built against a
previous output.

## GNAM audio category: the 2D test is bit 6 ALONE
<a id="gnam-2d-test-bit-6"></a>

A 2D **looping** TES4 sound is an ambience bed (weather winds, interior drones)
and must land in `AudioCategoryAMB` (`0x0007F80B`) like every vanilla
`AMBWeather*` descriptor. Filed under `AudioCategorySFX` (`0x000172A1`) it sits
in the wrong mix bus, ignores the ambience slider and ducking, and Oblivion's
weather winds played LOUD over everything. One-shots and 3D sounds stay SFX.

🛑 **The 2D test is bit 6 alone.** It once accepted Menu Sound (bit 5) as well,
but xEdit (`wbDefinitionsTES4` 'Flags') lists them as SEPARATE flags: bit 5
'Menu Sound' is a UI routing hint, bit 6 '2D' is what actually means
non-positional.

Three Nehrim sounds are LOOP|MENU **without** 2D — `AMBFireSmallLP`,
`AMBFireMediumLP` and a forest-birds loop — all genuinely POSITIONAL world
sounds attached to fire pits. Promoting them to the global ambience bus detached
them from their emitter, so a fire pit crackled (and birds chirped) across the
entire worldspace at constant volume, in clear weather, nowhere near any fire.
Confirmed by attaching to the live game: the loaded descriptor
`TES4_AMBFireSmallLP_SNDR` carried GNAM `0x0007F80B` (AudioCategoryAMB) with
LNAM loop `0x08`.

## ONAM: a 3D sound with max=0 must not take the 2D model
<a id="onam-3d-max-zero"></a>

`ONAM` is required — the CK reports "Sound Output Model missing" without it.
Only a genuinely 2D sound takes the vanilla non-attenuating model; everything
else gets a SOPM built from its own TES4 falloff distances.

🛑 In Oblivion an unset max distance means "use the engine's default falloff",
**not** "audible everywhere". `_SOPM_2D` is `SOMDialogue2D`, which does not
attenuate at all, so such a sound played at full volume across the whole
worldspace. Nehrim's siege-engine set-piece is authored exactly this way
(`ambsiegeenginestep` / `_idle_lp` / `_foward_lp`: 3D, max=0), which put a
repeating mechanical THUMP over open countryside far from any siege engine.

Vanilla Skyrim never ships a positional loop on a non-attenuating model: its
SNDRs overwhelmingly use finite mono falloffs (SOMMono01400 x428 including
Player1st, 01800 x285, 02000 x225), and the non-attenuating models are reserved
for UI and 2D dialogue. An absent distance therefore takes
`_DEFAULT_3D_MAX_DIST`.

## SNDX/SNDD field scaling
<a id="sndx-field-scaling"></a>

SNDX and SNDD hold the same struct; whichever is present wins.

| Field | Conversion |
|---|---|
| Min attenuation distance | TES4 stores it scaled down x5 (xEdit `wbMul`) |
| Max attenuation distance | TES4 stores it scaled down x100 |
| Static attenuation | u16 of hundredths of a dB in **both** games — raw transfer, no rescaling |

Volume in Skyrim comes from two places and both must be carried, or every sound
plays far louder than vanilla: `SNDR BNAM` static attenuation (Oblivion's SNDX
bytes 8-9; 95% of Oblivion.esm SOUNs set it, median 6.6 dB) and the SOPM's
min/max distance (SNDX bytes 0-1).

`CNAM` is the Descriptor Type constant `0x1EEF540A`, matching all vanilla SNDR
records. `LNAM` is a 4-byte Loop Data struct — byte[1] is the looping enum
(0x00 None, 0x08 Loop), byte[3] Rumble. `BNAM` is
`FreqShift(S8) FreqVariance(S8) Priority(U8) dbVariance(U8) StaticAttenuation(U16)`.

## ANAM paths: relative form, and directory-valued FNAM
<a id="anam-paths"></a>

**Relative is correct.** Both forms are legal and both work: 3,512 vanilla ANAM
values are rooted at the data folder (`Data\Sound\FX\...`) and 707 are
relative like ours (`fx\npc\dragon\npc_dragon_breathe_lp.wav`). Prefixing
`Data\Sound\` was tried on 2026-08-05 and changed nothing in-game, so the
relative form stands — it is what this pipeline has always written.

**Extensions must match what the sound stage produces.** Non-voice audio keeps
its extension; only `.mp3` is transcoded (to PCM `.wav`), because the SSE exe
has no mp3 support. Keep `_shipped_name` in lockstep with
`asset_convert.audio.audio_converter.convert_sounds` — an ANAM naming an extension
the sound stage does not produce is a reference to a file that isn't there, and
the sound is silently dropped.

**A directory-valued FNAM expands to one ANAM per file.** Oblivion lets a SOUN
name a DIRECTORY instead of a file and picks one of its files at random per
play — 6 of the goblin's 7 sound slots are authored this way, and it is how
every creature gets varied vocal lines. Skyrim has the same feature expressed
differently: the variants are listed explicitly, one ANAM per file, and the
engine randomises across them (vanilla `NPCWolfHowl` lists 5 wav ANAMs).

A single ANAM naming a bare directory is not a sound Skyrim can open, so every
such SOUN was silent — the CREA sound channel converted to records that could
never play. Enumerating the extracted folder reproduces Oblivion's
random-variant behaviour with Skyrim's own mechanism. The list is **sorted** so
the ANAM order, and therefore the output ESM, stays byte-reproducible across
runs (the determinism contract).

## set_sound_source_dir takes the ASSET root
<a id="asset-root-not-record-dir"></a>

🛑 The **asset** root (the folder holding `sound/`), not the record dir. For an
imported mod those are two different folders, and the record dir has no
`sound/` — every directory-valued FNAM then fell back to its bare literal.
Callers hold a record dir, so wrap it: `assets_for(export_dir)`.

Only needed to expand directory-valued FNAMs; a missing or None dir simply means
such sounds fall back to the single literal path.

## The runtime sound table
<a id="the-runtime-sound-table"></a>

TES3 script opcodes name a **SOUN id** -- `PlaySound3D "Door Stone Open"`,
`GetSoundPlaying "Cave Drip"` (45 call sites in Morrowind.esm) -- but the record
the engine plays is the **SNDR** this import mints, not the SOUN. So the sidecar
table maps `TES3 sound id=Plugin.esm|SNDR FormID`, and the runtime resolves that
pair through the running load order exactly as it does for items and refs.

🛑 **It cannot be built from the export text.** The SNDR FormID is *derived*
during Phase 3 (`record_sndr_for_soun`), whereas `write_script_tables` runs from
the export alone, in the simple-records phase that precedes it. The table is
therefore written from the WRITER's state in `_patch_sounds`, the same finalize
pass that binds every other sound slot -- which is also why a restage with no
import leaves an existing table alone, as the journal QUSTs do.

Master-owned sounds resolve through the same `_master_sound_descriptor` the slot
patcher uses (read the master's converted SOUN and take its `SDSC`), never by
re-deriving, which would mint an id in this plugin's index space. A sound with
no descriptor is omitted rather than written as 0: the runtime then falls
through to silence instead of naming a record nothing wrote.
