# asset_convert/audio/audio_converter.py - sound and music

**Code:** `asset_convert/audio/audio_converter.py`, `asset_convert/audio/music_convert.py`, `asset_convert/audio/door_sounds.py`

## Contents

- [The core fact: no record names a music file](#core-fact-no-record-names)
- [Scoping: per plugin, not per worldspace](#scoping-per-plugin-not-per)
- [Format: xWMA, not PCM](#format-xwma-not-pcm)
- [Bitrate: native rates only, scaled to the source](#bitrate-native-rates-only-scaled)
- [Record structure](#record-structure)
- [The manifest is built by whichever stage needs it first](#manifest-built-whichever-stage-needs)
- [🛑 Exterior music comes from REGN.RDMO, not WRLD.ZNAM](#exterior-music-comes-from-regnrdmo)
- [Group order](#group-order)
- [Script revival](#script-revival)
- [The one authored value](#one-authored-value)
- [Verified output](#section)
- [Running the extract stage for a standalone conversion](#running-extract-stage-standalone-conversion)

Implemented 2026-08-25. Built and verified on Oblivion.esm and Nehrim.esm.

## The core fact: no record names a music file
<a id="core-fact-no-record-names"></a>

TES4 has **no music records at all**. Oblivion's engine scans
`Data\Music\<Category>\` and shuffles whatever it finds; the plugin carries only
a 3-value enum (xEdit `wbMusicEnum`):

| value | meaning |
|---|---|
| 0 | Default |
| 1 | Public |
| 2 | Dungeon |

stored in `CELL.XCMT` (U8) and `WRLD.SNAM` (U32).

**So the FOLDER is the authored unit of meaning**, and a
"only extract files the plugin references" filter is wrong here. Measured:

| | referenced music paths | files on disk |
|---|---|---|
| Oblivion.esm | **0** | 32 |
| Nehrim.esm | 35 (all from SCPT/SOUN) | 76 |

For Nehrim, **49 of 76 files (64%) are referenced by no record** — all of
`Battle/` and `Public/`, most of `Explore/` and `Dungeon/`. Those are exactly
the files the engine's folder-scan uses. A reference filter would ship 0 files
for Oblivion and silence every town and fight in Nehrim.

Conversely **8 of Nehrim's 35 references do not exist on disk**
(`bardemusik01.wav`, `music\nehrim\theme_03.mp3`, `theme_06_part01/02.mp3`,
`schandmaulbacktrack.mp3`, `event_chase01.mp3`, and the `specialevent_05.mp3`
typo — a missing path separator). The reference list is not even a reliable
subset; those call sites keep an inert marker.

## Scoping: per plugin, not per worldspace
<a id="scoping-per-plugin-not-per"></a>

Output goes to `Music\tes4\<plugin>\<Category>\`. This is load-bearing:
Oblivion and Nehrim **both** ship `Explore/`, `Dungeon/`, `Public/`, `Battle/`
and `Special/`, so a shared `Music\Explore\` would have one overwrite the other.

Per-*worldspace* scoping was considered and rejected — there is no authored
source for it. Nehrim's 23 worldspaces with `SNAM` carry only the same 3-value
enum (18 Dungeon + 5 Public); `MQ08SanktumWorld` and `CubeWorldspace` both just
say `music=2`. Vanilla Skyrim agrees: **9 distinct MUSC across 27 worldspaces**,
with `MUSDungeonCave` reused 8× and `MUSTownTest` 6×.

Loose music is ingested **only for masterless plugins**
(`bsa_extract._is_masterless`, which delegates to `terrain_lod._master_names`).
Nehrim's `Data\` holds five plugins (Nehrim.esm, ORN.esp, Translation.esp, …)
beside one `Music\` tree; without the gate every dependent ESP would re-ship
329 MB.

## Format: xWMA, not PCM
<a id="format-xwma-not-pcm"></a>

Vanilla SSE ships loose music as `RIFF/XWMA, wFormatTag=0x161, 44.1 kHz stereo`.
All 240 vanilla `MUST.ANAM` strings say `.wav`, but `.xwm` loads too and we
write `.xwm` in the ANAM so no extension substitution is assumed.

Encoding is **stereo** — `audio_converter.convert_file_to_xwm` downmixes to mono,
which is right for dialogue and wrong for a soundtrack, hence the separate
`music_convert.convert_music_file`.

## Bitrate: native rates only, scaled to the source
<a id="bitrate-native-rates-only-scaled"></a>

🛑 **xWMAEncode accepts only 20000, 32000, 48000, 64000, 96000, 160000, 192000.**
Anything else fails outright with `XWMA_E_UNSUPPORTED_BITRATE` — 128000 looks
natural and is not selectable.

🛑 **Of those, only a subset is NATIVE per (sample rate, channels).** From
xWMAEncode's own usage text:

```
44100Hz mono:   32000, 48000
44100Hz stereo: 32000, 48000, 96000, 192000
48000Hz stereo: 48000, 64000, 96000, 160000, 192000

"Other combinations are supported by resampling the source data
 and/or using a bitrate of 48kbps as a fallback"
```

**64000 and 160000 are NOT native at 44.1 kHz.** Asking for either silently
resamples the output to 48 kHz. Verified by reading the `fmt` chunk of the
result rather than trusting the request:

| asked | actual | sample rate | size | |
|---|---|---|---|---|
| 48000 | 48 | 44100 | 0.68 MB | native |
| 96000 | 96 | 44100 | 1.35 MB | native |
| **160000** | 160 | **48000** | 2.25 MB | **RESAMPLED** |
| 192000 | 192 | 44100 | 2.69 MB | native |

We normalize every source to 44.1 kHz stereo before encoding, so only the
`44100Hz stereo` row applies. `pick_bitrate()` never returns anything outside
`NATIVE_44K_STEREO` / `NATIVE_44K_MONO`.

### Why the target tracks the source

Re-encoding lossy→lossy compounds artifacts: spending 192k on a 128k mp3
preserves that mp3's existing damage more faithfully without recovering
anything. Measured SNR of the decoded xWMA against the source PCM:

| source | @32k | @48k | @96k | @192k |
|---|---|---|---|---|
| 128 kb/s | 12.96 | 14.37 | **20.92** | 29.50 |
| 192 kb/s | 12.75 | 14.51 | **26.77** | 33.88 |
| 320 kb/s | 15.26 | 19.25 | **25.62** | 31.50 |

The 128k source at 96k (20.92 dB) is worse than the 320k source at the same
96k (25.62 dB) — the ceiling is the SOURCE, so the ladder scales with it:

| source kb/s | target |
|---|---|
| ≤ 64 | 32000 |
| ≤ 128 | 48000 |
| ≤ 224 | 96000 |
| > 224 | 192000 |

Mono clamps to `NATIVE_44K_MONO` (tops out at 48000).

For calibration, **vanilla Skyrim ships ALL its music at 48 kb/s** 44.1 kHz
stereo — measured on `mus_combat_01.xwm` / `mus_dungeon_01.xwm` in
`Skyrim - Sounds.bsa` and all 49 loose AE soundtrack files. Even the bottom
rung here is vanilla parity; the top is 4×.

### Measured source spread and result

| source kb/s | Nehrim | Oblivion | → target |
|---|---|---|---|
| 64 (mono) | 1 | — | 32k |
| 112–128 | 16 | 1 | 48k |
| 160–192 | 2 | 29 | 96k |
| 320 | 57 | 2 | 192k |

| plugin | source mp3 | output xwm |
|---|---|---|
| Nehrim.esm | 328.6 MB | **194 MB** |
| Oblivion.esm | 103.6 MB | **53 MB** |

All 108 output files verified at 44.1 kHz — none resampled.

## Record structure
<a id="record-structure"></a>

Per `wbDefinitionsTES5.pas` and a real Skyrim.esm dump (258 MUST / 50 MUSC):

```
MUSC: EDID, FNAM flags(u32), PNAM {Priority u16, Ducking u16}, WNAM fade(f32),
      TNAM array of MUST FormIDs
MUST: EDID, CNAM track type(u32), FLTV duration(f32), DNAM fade-out(f32),
      ANAM track filename
```

`CNAM` values are **hashes, not an ordinal enum** — writing 0/1/2 gives a track
the engine ignores:

| constant | value | vanilla count |
|---|---|---|
| Single Track | `0x6ED7E048` | 240 |
| Palette | `0x23F678C3` | 13 |
| Silent Track | `0xA1A9C4D5` | 5 |

Vanilla census drove what we omit: only **1** record carries `LNAM` loop data,
so that is left out rather than invented. 210 of 240 `ANAM`s start with a
leading backslash; we follow the majority.

🛑 **`FNAM` cue points are NOT optional for combat tracks.** They were
originally omitted with `LNAM`/`BNAM`, and the result was that combat music
stopped playing entirely — *including Skyrim's own*, because our Battle MUSC
and vanilla `MUSCombat` are both found by the engine scanning for the combat
flag signature (neither is referenced by any record: our Battle FormID appears
exactly **once** in the ESM, its own header). Ours was selected, could not
play, and displaced theirs.

All **10** vanilla MUST carrying `FNAM` are combat tracks; no ambient track has
any. Per the CK wiki (*Music Track*, "Choose Finale") the engine crossfades
"from one of the Cue Points directly into the Finale track when combat ends
before the completion of a Combat track" — the cues are how a combat track both
enters and leaves.

Measured vanilla shape, over all 10:

| | range |
|---|---|
| cue count | 12–20 |
| gap | 3.74–8.00 s |
| layout | evenly spaced, first cue ≈ one gap in, last ≈ one gap before the end |

The gap is that track's musical **bar length**, which TES4 gives no way to read,
so `combat_cue_points()` tiles the measured duration at ≈6 s (vanilla's mean),
clamped to vanilla's 12–20 count. Every Oblivion battle track lands inside the
measured ranges (12–20 cues, gaps 4.75–6.10 s).

`BNAM` is still omitted — Oblivion's `Battle\` folder ships 8 tracks and **no
finale file**, so there is nothing authored to point at. With no `BNAM` the
engine exits on a cue point instead of crossfading into a finale. Neither
subrecord is `SetRequired` in xEdit.

Per-category MUST shape, matching vanilla:

| category | shape |
|---|---|
| Battle | `EDID CNAM ANAM FNAM` |
| Explore / Public / Dungeon | `EDID CNAM ANAM` |
| silence | `EDID CNAM FLTV` |

🛑 **A MUST carries exactly the subrecords its track TYPE takes — no more.**
Censused over all 258 vanilla MUST in `references/Skyrim.esm/MUST.txt`:

| CNAM type | count | shape | FLTV | DNAM |
|---|---|---|---|---|
| Single Track | 240 | `EDID CNAM ANAM` (203 exactly this) | **0/240** | **0/240** |
| Silent | 5 | `EDID CNAM FLTV` | 5/5 | 0/5 |
| Palette | 13 | `EDID CNAM FLTV DNAM` | 13/13 | 13/13 |

`FLTV` is the duration of a track with **no file to measure**; a Single Track
names a file in `ANAM` and the engine reads the length from it. `DNAM` is the
**Palette** fade-out — CK wiki, *Music Track*: "when the duration of the Palette
has been reached, the Palette will be faded out over this value in seconds."

**Neither belongs on a single track.** Emitting `DNAM` without `FLTV` makes a
combination that occurs in **no** vanilla record, and the track goes silent —
which in practice means every exterior worldspace loses its music, because the
45 unauthored worlds all fall through to `Explore`. Confirmed in game.

Only the one silent track per plugin needs an ffmpeg probe (Oblivion 300 s,
Nehrim 230 s).

### Silence tracks

A stem containing `silence` becomes a Silent Track (no `ANAM`). Both games
author one: Nehrim's `Special\Silence.mp3`, and — at the music **root** with no
category folder — Oblivion's `5min-silence.mp3` (300 s). A root-level file is
given one-shot `special` treatment rather than joining `Explore`'s rotation,
where it would play as five minutes of dead air.

## The manifest is built by whichever stage needs it first
<a id="manifest-built-whichever-stage-needs"></a>

Music rides the **sound** stage (phase 7) but the records are built by the
**import** stage (phase 6) — so on a plugin's first conversion the manifest did
not exist yet, `load_music_manifest` returned `{}`, and the ESM shipped with
**zero MUST/MUSC and no `XCMO`/`ZNAM`** while the `.xwm` files converted
normally. Music was simply absent in game until the plugin was converted a
second time, which is why it reproduced for some users and not others.
(Shipped in 0.618, the first release with music at all.)

Everything the importer reads — `source_rel`, `category`, `stem`, `game_path` —
comes from the **directory walk alone**, so `music_convert.scan_music` writes a
complete manifest with no ffmpeg, no xWMAEncode and no subprocess.
`load_music_manifest` calls it when the file is absent, and the records no
longer depend on stage order at all. `duration`, `bitrate` and `source_kbps`
are encode-side only; the sole exception is the silent track's duration, which
is the one value `scan_music` probes (1 file per plugin, not 76).

Verified: a first-ever import now builds Oblivion 32 MUST / 9 MUSC and Nehrim
76 / 34 — matching the table below, where it previously built none. FormIDs are
unchanged (`derive_formid` keys on `source_rel` / category, neither of which
moved). Regression: `tests/test_music_first_run.py`.

## 🛑 Exterior music comes from REGN.RDMO, not WRLD.ZNAM
<a id="exterior-music-comes-from-regnrdmo"></a>

**This is the mechanism, and getting it wrong is why "cities have music but the
countryside is silent" survived three separate fixes.**

Vanilla Skyrim's `Tamriel` DOES carry a `ZNAM` — pointing at a MUSC named
`_NONE` (priority 5) whose single track is `_MUSExploreSILENT30`: a **Silent
Track of 30 seconds**. The overworld's ZNAM is deliberately near-silence. Every
real explore type reaches the player through a **region**:

| | count |
|---|---|
| vanilla REGN with `RDMO` | 28 |
| distinct MUSC they reference | 5 (`MUSExploreMountain` ×12, `MUSExploreTundra` ×7, `MUSExploreForestPine` ×7, …) |
| vanilla exterior CELLs with `XCMO` | 150 of 16,978 |

So neither ZNAM nor per-cell XCMO carries the open world — `RDMO` does.

TES4 authors exactly the same thing: **`REGN` → `RDAT` type 7 (Sound) →
`RDMD`**, the identical 3-value `wbMusicEnum`. Measured on the raw plugins:

| | REGN | with RDMD | values |
|---|---|---|---|
| Oblivion.esm | 211 | **127** | 126× Default(0), 1× Dungeon(2) |
| Nehrim.esm | 78 | **60** | 39× Default, 9× Public, 12× Dungeon |

`RDMD` was never exported and `RDMO` never written, so the countryside had
nothing to play. The export now emits `RegionData[i].MusicType` and
`convert_REGN` writes `RDAT`(type 7, override 0, priority 50) + `RDMO` —
byte-identical to vanilla's header. A region with music but **no** weather list
is no longer dropped.

Verified in the built ESM: **127 of 133 REGN carry RDMO**, 126 → `MUSOblivionesmExplore`.

### Cities: a default RDMD must yield to the worldspace

**Oblivion's cities are their own worldspaces** (`BrumaWorld`, `ChorrolWorld`,
the 8 IC districts…), each with `SNAM=1` (Public). But each also has a weather
region whose `RDMD` is **0**, and a region overrides the worldspace — so
honouring it pinned every city to Explore and the track never changed on
entering one.

`RDMD=0` is the CS's **unset default, not a choice**:

| | count |
|---|---|
| Oblivion RDMD = 0 (Default) | **126** |
| Oblivion RDMD ≠ 0 | **1** (`WaitingRoomRegion`, Dungeon) |
| regions whose RDMD contradicts their worldspace SNAM | **21 — every one a city** |

So `convert_REGN` drops a default-valued `RDMD` when its worldspace authors a
non-default `SNAM`; a region naming a real type still wins. Because REGN
converts in Phase 1 — before WRLD — the worldspace enums are indexed in
Phase 0c via `register_world_music()`.

Result: RDMO 127 → **106**, the 21 city regions yielding, while
`BrumaWorld`/`ChorrolWorld`/`SkingradWorld`/`ICMarketDistrict` keep
`ZNAM=MUSOblivionesmPublic` and `TES4Tamriel` keeps Explore.

## Group order
<a id="group-order"></a>

MUSC/MUST go **after** CELL, matching vanilla. Measured on the real Skyrim.esm
top-level order: CELL 57, WRLD 58, LCTN 86, MUSC 91, DLBR 97, MUST 98.

Unlike a REFR base object, a cell's `XCMO` is **not** resolved when the CELL
group parses — vanilla's own 701 `XCMO` cells load with the music groups 34
slots later — so the base-object rule in `writer._group_order` does not apply.

## Script revival
<a id="script-revival"></a>

`StreamMusic` was previously inert. It now resolves to `MusicType.Add()` on the
per-cue MUSC built from that exact path.

- **38 `StreamMusic` calls in Nehrim.esm** (35 by path, 3 by bare category);
  Oblivion.esm has **none**.
- **26 revived**; the remaining 12 call sites are the 5 distinct dead
  references above and `StreamMusic Random`.
- All **13** declared `MusicType` properties bind to a real MUSC record.

The EditorID is the contract between the two sides:
`script_convert.constants.music_cue_editor_id` / `music_type_editor_id` **must**
match `tes5_import.record_types.music.musc_cue_editor_id` / `musc_editor_id`.
If they drift, the property binds to nothing and the cue is silent.

Music cues ride `initargs` into the script workers — Windows **spawns** workers,
so a dict built only in the parent leaves every worker with an empty map.

`emc*` (Elys Music Control: `emcMusicStop`, `emcSetMusicHold`,
`emcIsBattleOverridden`, `emcGetPlaylist`) stays inert — those control the
playlist rather than naming a track, and Papyrus has no equivalent even with
MUSC authored.

## The one authored value
<a id="one-authored-value"></a>

`Battle\` has **no record-level source** — Oblivion picks combat music by folder
name alone, and `XCMT` has no combat value. Its MUSC priority/flags are authored
in `CATEGORY_SPECS`. Every other category is a direct folder mapping.

## Verified output
<a id="section"></a>

| | Oblivion.esm | Nehrim.esm |
|---|---|---|
| MUST | 32 (31 single + 1 silent) | 76 (75 single + 1 silent) |
| MUSC | 9 (4 category + 5 cue) | 34 (4 category + 30 cue) |
| CELL `XCMO` | 1,871 | 613 |
| WRLD `ZNAM` | — | 23 of 34 |

Counts match the source census exactly (Oblivion 1104 Dungeon + 767 Public =
1871; Nehrim 386 + 227 = 613, and 18 + 5 = 23 worldspaces).

## Running the extract stage for a standalone conversion
<a id="running-extract-stage-standalone-conversion"></a>

`phase_extract` reads the single global `tes4DataPath`, and `find_game_path`
checks that config value BEFORE falling back to registry detection. So a
standalone total conversion (Nehrim ships its own game folder on another drive)
works by pointing `tes4DataPath` at that install — which is what the GUI's data
path selector writes (`gui.py`, `updated["tes4DataPath"]`).

With `tes4DataPath` empty, a bare CLI run registry-detects Oblivion and finds
none of Nehrim's BSAs or its loose `Music\`. That is a configuration state, not
a bug: set the path (or use the GUI) and both the BSA pass and the loose-music
ingest resolve correctly.

## Voice conversion: the LipGenerator Fonix mutex
<a id="lipgenerator-fonix-mutex"></a>

`LipGenerator.exe` is single-threaded *for the whole machine*, not per process.
The Fonix engine inside it serializes every instance through a named mutex
(`FonixMemoryMutex`), so aggregate throughput caps at **~8 lips/s** no matter
how many processes run. The visible symptom is dozens of LipGenerator processes
each sitting at ~0.1% CPU, ~97% idle waiting their turn.

The mutex guards nothing shared: the exe creates no file mapping, and each
process's Fonix state is private. Renaming the mutex in per-worker copies of the
exe lets them run genuinely in parallel.

**Measured: ~8.5 → ~105 lips/s with 32 workers.** Per-call latency also drops
from 6–9 s to ~0.3 s, because the tool's own 1 s poll loop was itself waiting on
the contended mutex.

`build_lipgen_pool()` makes the per-worker copies; each lands in its own
subdirectory with `FonixData.cdf` hard-linked beside it, since the exe loads the
`.cdf` relative to its own location.

## Voice file naming: the Oblivion prefix cannot be trusted
<a id="voice-file-naming-prefix"></a>

The two engines name voice files differently, and the difference is not
recoverable from the filename alone:

| | Pattern |
|---|---|
| Oblivion | `<quest>_<topic>_<infoFID8hex>_<idx>.<ext>` |
| Skyrim | `<prefix>_<InfoFormID>_<RespNum>.<ext>` |

Skyrim builds `<prefix>` **at runtime** from the converted plugin's own
owning-quest and topic EditorIDs, applying its own truncation rules (see
`dialog_converter.voice_file_prefix`). The Oblivion filename prefix encodes
*Oblivion's* truncation of the *original* quest name, so it will not match.

Files are therefore renamed through the voicemap the importer emits, keyed on
the InfoFormID with the load-order byte stripped (a 24-bit value). The voicemap
line may also carry a tab-separated VTYP list naming the folder(s) the speaker
actually resolves to when that differs from the Oblivion source race folder —
Arvena Thelas is a Dark Elf whose recordings sit under `high elf/f/`. An empty
list means keep the source race folder, which is correct for generic lines
recorded once per race.

## Race identity spans the masters
<a id="race-identity-spans-the-masters"></a>

A source voice folder is named after a race's **display name** (`FULL`), so
resolving `high elf/` to `TES4MaleHighElf` means finding the RACE record whose
FULL is "High Elf". `load_race_voices` reads masters first, then the plugin's
own RACE.txt, so a later record overrides an inherited one of the same EditorID.

This replaced a hardcoded `TES4_VOICE_TYPE_MAP` of 39 (folder, gender) pairs.
Measured against it before deletion:

| Export | Table entries reached | Verdict |
|---|---|---|
| Oblivion.esm | 0 of 39 | every folder already resolved from its own races |
| Nehrim.esm | 0 | standalone; German FULLs (`Hochelf`, `Eraterna`) — the table *disagreed* on 8 |
| ElsweyrAnequina.esp, Unique Landscapes, Morrowind_ob | all folders | declare `Master[0]=Oblivion.esm`; the table was standing in for a master lookup the audio stage never did |

The table was a transcript of Oblivion.esm's RACE records, so a plugin mastered
on Oblivion.esm now reads the real thing. Its alternate spellings (`high elf`
alongside `HighElf`) are likewise authored: `by_folder` registers both the FULL
and the EditorID of every race.

A race with **no FULL** is skipped — the folder on disk is the display name, so
a race without one has no folder to route to. Oblivion's `VampireRace`
(`0x00000019`) is the only such record, and no `vampire/` source folder exists.

## VNAM: which race's actors voice a race
<a id="vnam-voice-routing"></a>

Oblivion does not record one take per RACE. It records one take per **voice**,
and the TES4 RACE record says which voice a race uses: `VNAM` is a per-gender
pair of RACE FormIDs (xEdit `wbDefinitionsTES4.pas`, `wbStruct(VNAM, 'Voice')`).
A null/absent VNAM means the race is voiced by its own actors.

Measured on Oblivion.esm — 15 RACE records, 10 carrying VNAM:

| Race | Male voice | Female voice |
|---|---|---|
| Orc | Nord | Nord |
| Khajiit | Argonian | Argonian |
| DarkElf | HighElf | HighElf |
| WoodElf | HighElf | HighElf |
| Breton | Breton (own) | Imperial |

That is exactly why only **17 voice folders** exist on disk for 15 races, why
there is no `breton/f`, and why no folder exists for Orc, Khajiit, DarkElf or
WoodElf. It is also why an Orc legitimately speaks with a Nord-family voice —
authored, not a conversion artifact.

`build_npc_to_vtyp_map` (`tes5_import/dialogue/converter.py`) resolves NPC voice
types through VNAM, so the voicemap's per-INFO VTYP list already reflects it.

The audio stage therefore needs no VNAM lookup of its own: a source folder maps
to exactly one VTYP (`_resolve_voice_type`), and `_voice_destination` emits a
take only into the targets equal to that VTYP — all of them when none matches,
which preserves the relocation case where a line is recorded under a different
race than the speaker's.

**The defect this fixes:** the destination name is `(prefix, InfoFormID, resp)`,
with no voice component, and it was fanned into *every* target VTYP. Whichever
race `os.walk` reached first won, and `dst_path.exists()` discarded the rest.
Measured on Oblivion.esm: 24,017 source keys, 5,793 present in more than one
voice folder, of which **5,782 hold genuinely different takes** (11 byte-
identical). `000363B1`/`000363B2` — eligible to Ruslan (Redguard) and Luronk
(Orc→Nord) — emitted one 27,831-byte file into both folders from sources of
29,885 and 24,660 bytes.

**FormID constraint:** `RaceVoices.keys` and `by_race_edid` drive VTYP FormID
allocation in `owned_records._emit_race_vtyps`, so neither may change — moving a
VTYP FormID breaks saves.

## Pruning stale voice output
<a id="pruning-stale-voice-output"></a>

`prune_stale_voice_files()` deletes output files this run did not want, and the
ordering around it is load-bearing in two places:

- **Prune even when every file already exists.** "Nothing to convert" is exactly
  the state a re-run lands in after a rename, with the OLD names still sitting
  alongside the new ones.
- **Prune AFTER conversion, never before.** A job that fails never writes its
  output; pruning first would delete the previous run's still-usable file and
  leave nothing in its place.

## <a id="which-ffmpeg-a-run-uses"></a>Which ffmpeg a run uses

**Code:** `asset_convert/audio/audio_converter.py` (`find_ffmpeg`)

An explicit `ffmpeg_path` (anything but the bare default `ffmpeg`) is used
ALONE: a caller naming a specific binary -- from config, or a test -- gets that
binary or None. Silently falling back to the bundled copy would turn a typo'd
config path into a run that looks fine while ignoring what the user asked for.

Otherwise the search order is the bundled `external/ffmpeg/` build first (see
`external/ffmpeg/BUILD.md`), then PATH. Bundled wins so a run is reproducible:
it is a known build with a known codec set, whereas whatever ffmpeg a user
already has could be any version with any codecs compiled out.

`need_decoder` skips a candidate that cannot decode that codec. The bundled
build is deliberately minimal (`--disable-everything`), so one predating a
source format decodes nothing rather than falling back -- FO3/FNV voice is Ogg
Vorbis where Oblivion's is MP3, and without this every one of FalloutNV.esm's
52,896 `.ogg` lines failed with "Invalid data found when processing input".
The codec check lets such a run fall through to a PATH ffmpeg that can read it.
