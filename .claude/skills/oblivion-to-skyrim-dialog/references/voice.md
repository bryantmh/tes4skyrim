# Voice Conversion (TES4 → TES5)

Oblivion and Skyrim resolve voice completely differently, and there is **no 1:1
mapping in the data** — this is the largest pure-judgment area of the conversion.
This file explains the mismatch, gives a defensible race+gender → VTYP mapping,
and specifies the INFO/NPC changes and audio re-pathing required for behavioral
fidelity.

---

## The mismatch

- **Oblivion:** no voice-type record. The voice file for a line is resolved from
  the **speaking NPC's race + gender** (the NPC_ `VNAM` struct can override which
  *race* is used for voice, per gender). An INFO carries no voice information.
  Path: `Sound\Voice\<plugin>\<Race>\<Gender>\<dialogFID>_<infoFID>.mp3`.
- **Skyrim:** every speaking NPC has `VTCK` → a **VTYP** record. Nearly every
  INFO has a `GetIsVoiceType(VTYP)` condition. The engine plays the recording for
  that voice type and only exports audio for voice types an INFO is gated to.
  Path: `Sound\Voice\<plugin>\<VoiceType>\<infoFID>_<respNum>.fuz`.

So conversion must invent a layer Oblivion never had: a discrete voice type per
NPC, and a voice-type condition per INFO.

---

## Step 1 — assign every speaking NPC a VTYP (`VTCK`)

For each Oblivion NPC_/CREA that can speak, derive a Skyrim VTYP from its
**race + gender** (use the NPC_ `VNAM` voice-race override if present, else the
NPC's own race). Skyrim's humanoid voice types are role/personality flavored
(e.g. `MaleNord`, `FemaleEvenToned`, `MaleSlyCynical`), so the mapping is
inherently approximate.

### Defensible race+gender → VTYP mapping (judgment)

This maps the ten Oblivion playable races to the **closest racial Skyrim voice
type**. It is a starting point chosen for recognizability, not a derivation from
data — **document that it's a judgment and allow per-NPC overrides** (a unique
Oblivion NPC may deserve a personality voice like `MaleSlyCynical` rather than
the generic racial one).

| Oblivion race | Male VTYP | Female VTYP | Note |
|---------------|-----------|-------------|------|
| Nord | MaleNord | FemaleNord | Direct racial match. |
| Imperial | MaleEvenToned | FemaleEvenToned | Imperials have no SK racial voice; even-toned is the common Imperial-flavored generic. |
| Breton | MaleEvenToned | FemaleEvenToned | No Breton racial voice in SK; even-toned/condescending are the usual stand-ins. |
| Redguard | MaleEvenToned | FemaleEvenToned | No Redguard racial voice; even-toned is the safe generic. |
| High Elf (Altmer) | MaleElfHaughty | FemaleElfHaughty | Skyrim's "elf haughty" matches Altmer tone. |
| Wood Elf (Bosmer) | MaleEvenToned | FemaleEvenToned | No Bosmer racial voice; even-toned generic (elf-haughty is too aloof). **Judgment.** |
| Dark Elf (Dunmer) | MaleDarkElf | FemaleDarkElf | Direct racial match. |
| Orc (Orsimer) | MaleOrc | FemaleOrc | Direct racial match. |
| Khajiit | MaleKhajiit | FemaleKhajiit | Direct racial match. |
| Argonian | MaleArgonian | FemaleArgonian | Direct racial match. |

Shivering Isles races (Golden Saint, Dark Seducer) and any custom races have no
Skyrim equivalent — pick the nearest humanoid voice (e.g. elf-haughty / sultry)
and flag it. **Judgment.**

### Creatures

Oblivion creature dialogue (growls/barks) maps to Skyrim creature voice types
(`Cr*Voice`: `CrWolfVoice`, `CrBearVoice`, `CrDraugrVoice`, …) by creature kind.
Match by creature model/family. Many Oblivion creatures have no Skyrim equivalent
voice — flag and pick the nearest, or leave the creature mute if it had no
meaningful dialogue.

> All Skyrim humanoid + creature VTYP EditorIDs are listed in the
> `skyrim-dialog-system` skill (`references/records.md`). Use that as the target
> vocabulary.

---

## Step 2 — add `GetIsVoiceType` conditions to INFOs

Skyrim needs each INFO gated to the voice type(s) that should say it. Derive the
voice type(s) from the NPC(s) the Oblivion line belonged to:

1. If the INFO had a `GetIsID(npc)` condition → that NPC's VTYP (from step 1).
2. If the topic was AddTopic'd / restricted to specific NPCs → those NPCs' VTYPs.
3. For a generic greeting/bark with no identity → the voice type(s) of the NPCs
   that actually use that topic (collect from sibling INFOs), or leave ungated
   only if it is genuinely meant for everyone (rare; ungated INFOs export audio
   for every voice type).

Multiple voice types → an **OR-chain** of `GetIsVoiceType` CTDAs (OR on all but
the last; see `conditions.md`). Inject these *before* the translated Oblivion
conditions so a trailing OR flag can't contaminate the chain.

**Why this matters for fidelity:** in Oblivion the correct voice played because
the speaking NPC's race+gender picked the file. In Skyrim, if the INFO isn't
gated to that NPC's VTYP, either the wrong voice plays or the line is offered to
every NPC of any voice type. The VTYP gate is what reproduces "the right
character says it in the right voice."

---

## Step 3 — re-path the audio

Oblivion and Skyrim store voice files under different directory schemes and
formats:

| | Oblivion | Skyrim |
|--|----------|--------|
| Path | `Sound\Voice\<plugin>\<Race>\<Gender>\<dialogFID>_<infoFID>.mp3` | `Sound\Voice\<plugin>\<VoiceType>\<infoFID>_<respNum>.fuz` |
| Format | MP3 (+ `.lip`) | FUZ (XWM + LIP), or XWM + separate LIP |
| Key | race + gender + dialog + info | voice type + info + response number |

Re-pathing requires: (a) the race+gender→VTYP mapping (step 1) to choose the
target voice-type folder, (b) renaming by the new FormIDs and per-response index,
and (c) **format conversion MP3→XWM/FUZ** (an audio transcode, not a data edit).
A `.lip` file is needed for lip-sync; if absent, set the INFO response `No LIP
File (0x0800)` flag or provide a placeholder, or the line may not play.

**Fidelity:** without re-pathed, transcoded audio, converted lines are silent /
subtitle-only even when the records are perfect.

---

## Fidelity summary for voice

| Aspect | Faithful from data alone? | Notes |
|--------|---------------------------|-------|
| Which NPC is bound to a voice | Partially | race+gender is known; the *choice* of Skyrim VTYP is judgment. |
| Right voice plays for right line | Yes, once VTYP+GetIsVoiceType set | The injected condition reproduces Oblivion's implicit routing. |
| Personality nuance of voice | No | Skyrim voice types are role-flavored; racial mapping loses per-NPC nuance unless overridden. |
| Audio actually audible | No (asset step) | Requires re-path + MP3→FUZ transcode + LIP. |
