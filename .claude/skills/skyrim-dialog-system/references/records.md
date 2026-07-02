# Skyrim Dialogue/Voice/Quest — Record Definitions

Authoritative binary layout of every dialogue-related record. Field names,
types, sizes, and enums are taken verbatim from
`references/xEdit/Core/wbDefinitionsTES5.pas`; example values are from
`references/Skyrim.esm` (dumped with `tools/tes5_esm_reader.py`).

**Type legend:** `U8/U16/U32` = unsigned int of that bit width (little-endian);
`S8/S16/S32` = signed; `float` = 32-bit IEEE; `FormID` = U32 reference;
`LString` = localized string (U32 string-table index when the file's header has
the Localized flag, else inline null-terminated). All multi-byte values are
little-endian.

---

## VTYP — Voice Type

The routing key for all voiced dialogue. Skyrim.esm has ~143.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID, e.g. `MaleNord`, `FemaleNord`, `MaleGuard`. |
| `DNAM` | U8 flags | `0x01` Allow Default Dialog, `0x02` Female. **Required.** |

Real record:
```
Signature=VTYP
FormID=000AA8D3
EditorID=MaleGuard
DNAM=1            # Allow Default Dialog
```

### Voice types present in Skyrim.esm

**Humanoid (used for GetIsVoiceType routing):**
```
FemaleArgonian FemaleChild FemaleCommander FemaleCommoner FemaleCondescending
FemaleCoward FemaleDarkElf FemaleElfHaughty FemaleEvenToned FemaleKhajiit
FemaleNord FemaleOldGrumpy FemaleOldKindly FemaleOrc FemaleShrill FemaleSoldier
FemaleSultry FemaleYoungEager
MaleArgonian MaleBandit MaleBrute MaleChild MaleCommander MaleCommoner
MaleCommonerAccented MaleCondescending MaleCoward MaleDarkElf MaleDrunk
MaleElfHaughty MaleEvenToned MaleEvenTonedAccented MaleForsworn MaleGuard
MaleKhajiit MaleNord MaleNordCommander MaleOldGrumpy MaleOldKindly MaleOrc
MaleSlyCynical MaleSoldier MaleWarlock MaleYoungEager
```
**Creatures (`Cr*Voice`):** AtronachFlame/Frost/Storm, Bear, Chaurus, Chicken,
Cow, Deer, Dog, DragonPriest, Dragon, Draugr, Dremora, DwarvenCenturion/Sphere/
Spider, Falmer, Fox, FrostbiteSpider(+Giant), Giant, Goat, Hagraven, Hare,
Horker, Horse, IceWraith, Mammoth, Mudcrab, SabreCat, Skeever, Skeleton,
Slaughterfish, Spriggan, Troll, Werewolf, Wisp, Witchlight, Wolf; uniques
`CrUniqueAlduin/Odahviing/Paarthurnax`.
**Unique characters:** e.g. `MaleUniqueUlfric`, `MaleUniqueTullius`,
`MaleUniqueCicero`, `MaleUniqueSheogorath`, `FemaleUniqueAstrid`,
`FemaleUniqueNightMother`, `FemaleUniqueDelphine`, plus the `SPECIAL*`
Greybeard/Sovngarde voices.

---

## DIAL — Dialog Topic

A prompt plus a bucket of INFOs. The INFOs are stored as children inside the
DIAL's GRUP (group), not inline in the DIAL record. Record flag `0x4000` =
"Partial Form".

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID (optional; many vanilla topics omit it). |
| `FULL` | LString | Topic text — the menu prompt the player sees. |
| `PNAM` | float | Priority. Default **50.0**. Higher = considered first. |
| `BNAM` | FormID→DLBR | Owning branch. Present on conversation topics (those that belong to a branch); absent on bark topics (combat/detection/misc subtypes, which aren't part of a branch). |
| `QNAM` | FormID→QUST | Owning quest. **Required for the topic to function.** |
| `DATA` | struct (4B) | **TopicFlags(U8) + Category(U8) + Subtype(U16)** — the xEdit order; see the warning below about the subtype NUMBERS. |
| `SNAM` | U32 | 4-char Subtype code as raw little-endian ASCII (e.g. `CUST`, `HELO`, `GBYE`). Default `CUST`. Required. Mirrors the Subtype; this is what the engine keys subtype behavior on. |
| `TIFC` | U32 | INFO count (number of child INFOs). |

### DATA breakdown — ⚠ subtype numbers vs. xEdit's display enum

> **CRITICAL (verified against real Skyrim.esm bytes).** The on-disk order IS
> xEdit's (`wbDefinitionsTES5.pas`) order:
>
> ```
> byte0 = TopicFlags (U8)
> byte1 = Category   (U8)   ← 0 Topic,1 Favor,2 Scene,3 Combat,4 Favors,
>                             5 Detection,6 Service,7 Miscellaneous
> byte2,3 = Subtype  (U16)  ← the fine code; mirrors SNAM
> ```
> Writing the struct swapped (subtype in byte1, category in the U16) produces
> an out-of-range category value the engine indexes into its per-category
> topic tables → **access violation / crash on load** while topics initialize.
>
> However, the **subtype enum NUMBERS in the data differ from xEdit's display
> enum** (e.g. real Hello = 73, not the 79 xEdit shows — xEdit marks the field
> `cpIgnore` and syncs it from SNAM, so its enum has drifted). Take the numbers
> from real data (table below). SNAM is authoritative for the engine.

- **Topic Flags (U8):** `0x01` Do All Before Repeating.
- **Category (U8):** 0 Topic, 1 Favor, 2 Scene, 3 Combat, 4 Favors,
  5 Detection, 6 Service, 7 Miscellaneous.

**Real Subtype → SNAM → Category** (the values actually present in
Skyrim.esm; dominant SNAM/category per subtype). Use these, not xEdit's
display enum:

| Sub | SNAM | Cat | Meaning | | Sub | SNAM | Cat | Meaning |
|----:|------|----:|---------|-|----:|------|----:|---------|
| 0 | CUST | 0 | Custom topic | | 43 | TRES | 3 | Trespass |
| 1 | PFGT | 0 | ForceGreet | | 49 | ALIL | 5 | AlertIdle |
| 2 | RUMO | 0 | Rumors | | 50 | LOIL | 5 | LostIdle |
| 14 | SCEN | 2 | Scene | | 51 | NOTA | 5 | Notice/Alert |
| 16 | AGRE | 4 | Agree | | 52 | ALTC | 5 | AlertToCombat |
| 17 | REFU | 4 | Refuse | | 53 | NOTC | 5 | NormalToCombat |
| 20 | ATCK | 3 | Attack | | 54 | ALTN | 5 | AlertToNormal |
| 21 | POAT | 3 | PowerAttack | | 55 | COTN | 5 | CombatToNormal |
| 22 | BASH | 3 | Bash | | 56 | COLO | 5 | CombatToLost |
| 23 | HIT_ | 3 | Hit | | 57 | LOTN | 5 | LostToNormal |
| 24 | FLEE | 3 | Flee | | 58 | LOTC | 5 | LostToCombat |
| 25 | BLED | 3 | Bleedout | | 69 | OBCO | 7 | ObserveCombat |
| 27 | DETH | 3 | Death | | 70 | NOTI | 7 | NoticeCorpse |
| 29 | BLOC | 3 | Block | | 71 | TITG | 7 | TimeToGo |
| 30 | TAUT | 3 | Taunt | | 72 | GBYE | 7 | GoodBye |
| 32 | STEA | 3 | Steal | | 73 | HELO | 7 | Hello |
| 36 | ASSA | 3 | Assault | | 84 | IDAT | 7 | Idle Topic |
| 37 | MURD | 3 | Murder | | 88 | IDLE | 7 | Idle |
| 39 | MUNC | 3 | MurderNC | | 94 | OUTB | 7 | OutOfBreath |

Real record (a Hit combat bark):
```
Signature=DIAL
FormID=00000E3C
PNAM=50.0
QNAM=0003372B          # owning QUST
DATA bytes = 00 03 17 00  → TopicFlags=0x00, Category=0x03(Combat), Subtype=0x0017(23 Hit)
SNAM="HIT_"            # mirrors the subtype
TIFC=1
```
A Hello greeting: `DATA = 00 07 49 00` (Category 7 Misc, Subtype 0x49=73 Hello),
`SNAM="HELO"`. A plain conversation topic: `DATA = 00 00 00 00`, `SNAM="CUST"`.

---

## INFO — Dialog Response

ONE spoken exchange. Lives in the GRUP of its parent DIAL. Record flag `0x2000`
= "Actor Changed".

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID (usually absent). |
| `VMAD` | struct | Papyrus **script fragments** (Begin/End scripts that run when the line starts/finishes). |
| `DATA` | (unknown) | Legacy/unknown; vanilla writes a small block. |
| `ENAM` | struct (4B) | Response Flags (U16) + Reset Hours (U16, stored ×2730/hr). |
| `TPIC` | FormID→DIAL | The owning topic. |
| `PNAM` | FormID→INFO | Previous INFO (chaining within a topic). |
| `CNAM` | U8 | Favor Level: 0 None, 1 Small, 2 Medium, 3 Large. |
| `TCLT` | FormID[]→DIAL/INFO | **"Link To"** — follow-up topics offered after this line. |
| `DNAM` | FormID | Response Data. |
| Responses[] | array | One or more `Response` structs (see below). |
| `CTDA` | array | The conditions gating this INFO (see `conditions.md`). |
| `RNAM` | LString | Prompt override. |
| `ANAM` | FormID→NPC_ | Speaker override. |
| `TWAT` | FormID→DIAL | Walk Away Topic. |
| `ONAM` | FormID→SOPM | Audio Output Override. |

### ENAM Response Flags (U16)
`0x0001` Goodbye, `0x0002` Random, `0x0004` Say once,
`0x0008` Requires Player Activation, `0x0010` Info Refusal, `0x0020` Random end,
`0x0040` Invisible continue, `0x0080` Walk Away,
`0x0100` Walk Away Invisible in Menu, `0x0200` Force subtitle,
`0x0400` Can move while greeting, `0x0800` No LIP File,
`0x1000` Requires post-processing, `0x2000` Audio Output Override,
`0x4000` Spends favor points.

### Response struct (one per spoken line; an INFO may have several)
| Field | Type | Meaning |
|-------|------|---------|
| `TRDT` | struct (24B) | Response Data — see below. |
| `NAM1` | LString | **Response Text** — the actual spoken/subtitled line. |
| `NAM2` | string | Script notes (designer-only; e.g. `excitedly`). |
| `NAM3` | string | Edits (designer-only). |
| `SNAM` | FormID→IDLE | Idle animation, **Speaker**. |
| `LNAM` | FormID→IDLE | Idle animation, **Listener**. |

**TRDT (24 bytes):** EmotionType(U32) + EmotionValue(U32) + Unused(4) +
ResponseNumber(U8) + Unused(3) + Sound FormID(U32, →SNDR, 0=use voice file) +
Flags(U8: `0x01` Use Emotion Animation) + Unused(3).

Real records:
```
# INFO with identity + location gating
FormID=00000E3D  ParentDIAL=00000E3C
ENAM=0x0000  CNAM=0(None)
TRDT: EmotionType=0 EmotionValue=50 ResponseNumber=1
NAM1=0x000126BF
CTDA: GetStage(0x0003372B) >= 70.0
CTDA: GetIsID(0x00000007) == 1.0
CTDA: GetInCell(0x0006491B) == 1.0
CTDA: GetIsID(0x0001414D) == 1.0

# INFO routed by voice type
FormID=00000E45  ParentDIAL=00000E42
ENAM=0x0800
TRDT: EmotionType=0 EmotionValue=50 ResponseNumber=1
NAM1=0x000126BC
CTDA: GetIsVoiceType(0x0001F2E6) == 1.0     # func 426
```

---

## QUST — Quest

Owns dialogue, stages, objectives, aliases. For dialogue to work the quest must
be running.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID. |
| `VMAD` | struct | Papyrus quest script + script fragments (`QF_<edid>_<fid>`). |
| `FULL` | LString | Quest journal name. |
| `DNAM` | struct (12B) | General data — see below. **Required.** |
| `ENAM` | string(4) | Event type (e.g. `ADIA`). |
| `QTGL` | FormID[]→GLOB | Text-display globals. |
| `FLTR` | string | Object-window filter path (e.g. `Main Quest\`). |
| (quest CTDAs) | array | "Quest Dialogue Conditions" — gate ALL of the quest's dialogue. |
| `NEXT` | marker | Separator. |
| Stages[] | array | `INDX` + flags, then per-log-entry `QSDT`/CTDAs/`CNAM`/`NAM0`. |
| Objectives[] | array | `QOBJ` + `FNAM` + `NNAM` + targets (`QSTA` + CTDAs). |
| `ANAM` | U32 | Next Alias ID. **Required.** |
| Aliases[] | array | Reference (`ALST`) or Location (`ALLS`) aliases — see below. |
| `NNAM` | string | Description. |
| Targets[] | array | `QSTA` (→ACHR/REFR/…) + CTDAs. |

### DNAM (12 bytes) — General
| Field | Type | Meaning |
|-------|------|---------|
| Flags | U16 | See flag table below. |
| Priority | U8 | Higher-priority dialogue is considered first. |
| Form Version | U8 | A per-struct version byte; marked `cpIgnore` in xEdit. Observed as 0 in Skyrim.esm dialogue quests. Distinct from the record's form version. |
| Unknown | 4B | Zero in observed data. |
| Type | U32 | Quest type enum (0 = None/generic dialogue). |

**DNAM Flags (U16):** `0x0001` Start Game Enabled, `0x0002` Completed,
`0x0004` Add Idle topic to Hello, `0x0008` Allow repeated stages,
`0x0010` Starts Enabled, `0x0020` Displayed In HUD, `0x0040` Failed,
`0x0080` Stage Wait, `0x0100` Run Once, `0x0200` Exclude from dialogue export,
`0x0400` Warn on alias fill failure, `0x0800` Active, `0x1000` Repeats
Conditions, `0x2000` Keep Instance, `0x4000` Want Dormant,
`0x8000` Has Dialogue Data.

> A common dialogue-quest flag value in Skyrim.esm is `0x0011` (Start Game
> Enabled + Starts Enabled) — e.g. `CreatureDialogueWerewolf`.

### Stages
- `INDX` (4 bytes): Stage Index(U16) + Flags(U8: `0x02` Start Up Stage,
  `0x04` Shut Down Stage, `0x08` Keep Instance Data) + Unknown(U8).
- Per log entry: `QSDT` Stage Flags(U8: `0x01` Complete Quest, `0x02` Fail
  Quest), optional CTDAs, `CNAM` Log Entry (LString), `NAM0` Next Quest.

### Objectives
- `QOBJ` Objective Index(U16), `FNAM` Flags(U32: `0x01` ORed With Previous),
  `NNAM` Display Text (LString, required), then Targets: `QSTA` (Alias S32 +
  Flags U8) + CTDAs.

### Aliases (two variants, discriminated by first subrecord)
- **Reference Alias:** starts `ALST` (Reference Alias ID U32).
- **Location Alias:** starts `ALLS` (Location Alias ID U32).
- Shared fields: `ALID` (Alias Name), alias flags, fill methods
  (`ALFI`/`ALFL`/`ALFR`/`ALUA`/`ALCO`/`ALNA`/`ALFE`+`ALFD`/external `ALEQ`+`ALEA`),
  CTDAs, keywords (`KSIZ`/`KWDA`), package-override FLSTs
  (`SPOR`/`OCOR`/`GWOR`/`ECOR`), `ALSP` spells, `ALFC` factions, `ALPC`
  packages, `VTCK` voice types, `ALED` (Alias End marker, required).

Real records:
```
# Pure dialogue-owner quest
FormID=00000E46  EditorID=CreatureDialogueWerewolf
DNAM: Flags=0x0011 Priority=0 FormVer=0 Type=0
CTDA: GetIsVoiceType(...) == 1.0      # quest-level voice gate

# Quest with stages, VMAD fragments, aliases
FormID=00017042  EditorID=MQSovngardeConv2ActorDialogue
VMAD: fragment script QF_MQSovngardeConv2ActorDial_00017042 (alias Hero2)
DNAM: Flags=0x0000 Priority=80 FormVer=0 Type=0
ENAM="ADIA"  FLTR="Main Quest\"
INDX=stage2, INDX=stage4
ANAM=10                                 # next alias id
ALST=5 ALID=Hero1 FNAM=0x40 ALFE="ADIA" VTCK=0 ALED
ALST=6 ALID=Hero2 FNAM=0x40 ALFE="ADIA" VTCK=0 ALED
```

---

## DLBR — Dialog Branch

Groups topics into a conversation flow.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID. |
| `QNAM` | FormID→QUST | Owning quest. **Required.** |
| `TNAM` | U32 | Category: 0 Player, 1 Command. |
| `DNAM` | U32 flags | `0x01` Top-Level, `0x02` Blocking, `0x04` Exclusive. |
| `SNAM` | FormID→DIAL | Starting topic. **Required.** |

- **Top-Level (`DNAM 0x01`)** branches appear directly in the player's dialogue
  menu when their starting topic's conditions pass.
- **Normal (`DNAM 0x00`)** branches are only reached by being linked to (via a
  TCLT on a preceding INFO); they do not appear directly in the menu.

Real records:
```
FormID=0010EC96 EditorID=MS11RobesQuestionBranch
QNAM=0001F7A3  TNAM=0(Player)  DNAM=0x01(Top-Level)  SNAM=0010EC97
```

---

## DLVW — Dialog View

Creation-Kit UI metadata. Lists a quest's branches/topics for the visual editor.
Not required for runtime dialogue, but vanilla quests with dialogue typically
have one.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID. |
| `QNAM` | FormID→QUST | Owning quest. **Required.** |
| `BNAM` | FormID[]→DLBR | Branches in the view (repeating). |
| `TNAM` | FormID[]→DIAL | Topics in the view (repeating). |
| `ENAM` | U32 | View Category: `0x00` Dialogue Branches, `0x07` Dialogue Topics. |
| `DNAM` | U8 | Show All Text: 0 False, 1 True. |

```
FormID=0010C065 EditorID=BardsCollegeDrumView
QNAM=000D944F  BNAM=000D9458 BNAM=000D9459 BNAM=000D946A  ENAM=0  DNAM=1
```

---

## SCEN — Scene

Scripted multi-actor sequences (the cinematic / staged conversations). Drives
DIAL topics through ordered phases. Not needed for ordinary chatter; included for
completeness.

| Subrecord | Type | Meaning |
|-----------|------|---------|
| `EDID` | string | Editor ID. |
| `VMAD` | struct | Fragmented scene script. |
| `FNAM` | U32 flags | `0x01` Begin on Quest Start, `0x02` Stop Quest on End, `0x04` Show All Text, `0x08` Repeat Conditions While True, `0x10` Interruptible. |
| Phases[] | array | `HNAM` (Marker Phase Start), `NAM0` (Name), Start Conditions (CTDAs), `NEXT`, Completion Conditions (CTDAs), `WNAM` (Editor Width), `HNAM` (Marker Phase End). |
| Actors[] | array | `ALID` (Actor/alias ID), `LNAM` (Flags: No Player Activation/Optional), `DNAM` (Behaviour Flags: Death/Combat/Dialogue/OBS_COM Pause+End). |
| Actions[] | array | `ANAM` Type (0 Dialogue, 1 Package, 2 Timer), `NAM0` Name, `ALID` Actor ID, `INAM` Index, `FNAM` Flags (incl. Face Target/Looping/Headtrack Player), `SNAM`/`ENAM` Start/End Phase, then a type-specific block. |

Action type-specific blocks:
- **Dialogue (0):** `DATA` Topic(→DIAL), `HTID` Headtrack Actor, `DMAX`/`DMIN`
  looping max/min, `DEMO`/`DEVA` emotion type/value.
- **Package (1):** `PNAM[]` packages.
- **Timer (2):** timer fields.

---

## IDLE — Idle Animation

Referenced by INFO responses (`SNAM` speaker / `LNAM` listener) for gestures and
by AI packages. Contains EDID, conditions, `DATA`, and an animation event/path.
Only relevant to dialogue insofar as INFO responses point at it; a missing IDLE
reference simply means no gesture, not a failure to speak.

---

## Cross-reference summary (who points at whom)

```
DLVW.QNAM ─► QUST            DLBR.QNAM ─► QUST
DLVW.BNAM ─► DLBR            DLBR.SNAM ─► DIAL (starting topic)
DLVW.TNAM ─► DIAL
                             DIAL.QNAM ─► QUST   DIAL.BNAM ─► DLBR
                             INFO.TPIC ─► DIAL   INFO.TCLT ─► DIAL/INFO
                             INFO.ANAM ─► NPC_   INFO response SNAM/LNAM ─► IDLE
                             INFO.CTDA GetIsVoiceType ─► VTYP
NPC_.VTCK ─► VTYP            QUST alias VTCK ─► VTYP/FLST
```
