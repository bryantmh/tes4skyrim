# tes5_import/base/conditions.py — CTDA translation

**Code:** `tes5_import/base/conditions.py`

CTDA is Bethesda's engine-wide condition record, not a dialogue one: it is read
by `creature_idles`, `packages/converter`, `record_types/music`, magic, region
and world. The translation service lives in `base/` for that reason.

Parameter remapping and the crash rule are in
[package_ai_contracts.md](../reference/package_ai_contracts.md#ctda-parameter-remapping--the-crash-rule).

## Contents

- [Actor-value indices diverge from the BOOK skill table](#actor-value-vs-book-skill-table)
- [Chargen-identity conditions become menu-choice globals](#chargen-identity-to-menu-globals)
- [Speak-as topics drop the actor-interrogating conditions](#non-actor-speaker-drop)
- [GetInCell names a prefix family, not a cell](#getincell-prefix-family)
- [Condition order: cheapest OR group first](#condition-order)
- [How the engine evaluates conditions and builds the topic list](#engine-evaluation)
- [Engine-fixed FormID parameters](#engine-fixed-params)

## <a id="engine-fixed-params"></a>Engine-fixed FormID parameters

**Code:** `_remap_formid` in `tes5_import/base/conditions.py`

A condition parameter is remapped like a record field (`text_reader.remap_formid`),
with two exceptions for forms the engine owns.

**Player forms and engine globals pass through unchanged.** Both games define
the player base 0x07, PlayerRef 0x14 and the six clock globals (GameYear 0x35 ..
TimeScale 0x3A) at the same ids, and a condition reads the runtime copy.
Shifting the player rewrote `GetIsID(Player) [Target]` on 3,761 INFOs to
0x01000007, which never passes: stage-gated reveal greetings died and NPCs kept
only "Rumors" (Pinarus Inventius). Shifting the globals cost 119 CK warnings on
`GetGlobalValue` against 0x01000038/0x01000037. The set is enumerated, not
"everything below 0x100": Oblivion.esm defines 127 records of its own there
(Tamriel WRLD 0x3C, 57 DIALs from 0xAA, 21 SKILs, 27 marker STATs), and passing
those through would name an unrelated Skyrim form.

**Engine-fixed items become the Skyrim record** (`TES4_ITEM_FORMID_TO_SKYRIM`:
Gold001 0xF -> 0xF, lockpick 0xA -> 0xA, DASkeletonKey 0xB -> TG08SkeletonKey
0x3A070). `get_formid` substitutes them for every item reference, so the gold
and keys the player carries are Skyrim's. A condition that remapped them to our
own copies asked `GetItemCount(0x0100000F)`, which is always 0: Penniless
Olvus's "Have a coin, beggar." topic never showed, and the two MQ08 skeleton-key
INFOs could never pass either.

## <a id="engine-evaluation"></a>How the engine evaluates conditions and builds the topic list

Read from SkyrimSE 1.6.1170 (unpacked). Stable ids are Address Library ids.

| Function | id | rva 1.6.1170 |
|---|---|---|
| `TESConditionItem::IsTrue` (one CTDA; dispatches the handler at table row +0x40) | 29924 | 0x4a05e0 |
| `TESCondition::IsTrue` (walks the list) | 29895 | 0x49fa70 |
| Pick a topic's line (quest gate, then each INFO in order) | 25544 | 0x3e82f0 |
| One INFO's check (deleted, said-once, then its conditions) | — | 0x3eba10 |
| Add one topic to the dialogue menu | — | 0x5e28e0 |
| Build the top-level topic list (two branch lists on MenuTopicManager) | 35276 | 0x5e0190 |

The script-function table is at rva 0x1fdba10, stride 0x50; condition id N is
row N. Id 21971 also indexes it but is the script compiler, not the evaluator.

**The list walk is AND over OR groups, evaluated in order.** A failing AND stops
the walk, and an OR group stops at its first passing member. There is no time
budget and no retry. Beside the result it returns a "not settled" flag, but only
nested walks set it. So condition order changes cost, never the result.

**The line picker drops a quest's whole topic while `Quest.IsStarting` is
true.** That is the running flag plus either flag 0x80 ("stage wait") or a
pending start job (+0x248). The same test backs the Papyrus native
(rva 0xa49a30). It skips every topic in that quest, so it cannot explain one
topic vanishing while another from the same quest stays.

**Four handlers keep an unlocked single-entry "last answer" cache in globals.**
They can hold wrong answers in play (measured below). This is vanilla engine
behavior and we leave it alone: bypassing all four in TESRuntime changed nothing
visible in game.

| Handler | rva | Cache (key → value) |
|---|---|---|
| `GetIsID` | 0x32d770 | 0x3137ec0 ref → 0x3137eb8 base |
| `GetIsVoiceType` | 0x32d850 | 0x3137ed0 ref → 0x3137ec8 voice type |
| `GetInFaction` | 0x32d390 | 0x3137ea8 ref, 0x3137ea0 faction → 0x3137eb0 rank |
| `GetIsPlayableRace` | 0x32d2c0 | 0x3137ed8 ref → 0x3137eb4 result |

On a miss each handler stores the key and the value as separate writes, and on
a hit it returns the stored value without recomputing it. `GetIsRace` and
`GetIsSex` keep no cache. Conditions run on more than one thread: the walker
keeps its context in thread-local slots, and a crash during a dialogue open had
the topic-list build (id 35276) on a `BSJobs::JobThread`.

Measured live on 1.6.1170 by reading the caches from outside the process and
recomputing each cached actor's answer the handler's way (`ExtraLeveledCreature`
original base, else the reference's base): cached pairs that were wrong and
still held on the next read, e.g. actor 130653B0 held with base 13C5457C
(another actor's) and, in another window, 00000007 (the player's), where its
own is 13464733. Any `GetIsID` on that actor answers wrongly until another
actor overwrites the entry. This was NOT why NPCs showed only "Rumors"; that was
a greeting choice link replacing the topic list
([tes5_import_dialogue.md](tes5_import_dialogue.md#info-tclt-choice-filter)).

A dialogue open cost 1.1 and 2.0 million CTDA evaluations (two opens, counted on
id 29924), against about 40,000 for one pass over every converted INFO with her
as the subject. The bridge's generic hook on id 35304 (the branch loop) crashed
the game at +0x44 on the next dialogue open.

## <a id="condition-order"></a>Condition order: cheapest OR group first

**Code:** `order_condition_groups` in `tes5_import/base/conditions.py`, applied
to every INFO's full list in `dialogue/converter.py:_info_conditions`.

Skyrim evaluates a record's conditions in order and stops at the first
failure. It also walks a topic's INFOs this way every time it builds the topic
list or picks a `Say` line.

The unit that can move is the **OR group**: a run of conditions chained by the
Or flag (bit 0). Groups are AND-joined, so their order never changes which lines
pass. Within a group nothing moves. The ordering is stable and sorts groups by:

1. **Groups that read a Papyrus variable go last.** Every
   `GetVMQuestVariable`(629) or `GetVMScriptVariable`(630) evaluation crosses
   from the main thread into the Papyrus VM (lock plus a lookup by mangled
   name); every other condition is a plain field read. CharGenMain had 59 INFOs
   whose `convCount` VM read came before their `GetIsID`, and its four escort
   speakers share one voice type, so every `Say` paid about 59 VM round-trips.
2. **Then smallest group first.** The prefix-family rewrite
   ([above](#getincell-prefix-family)) produces OR groups of up to about 360
   `GetInCell` checks (IC). The gates injected by the dialogue builder (the
   speaker's `GetIsID`, `GetIsVoiceType`, the AddTopic unlock global) are small
   and reject most speakers. Placed first, they reject a wrong speaker in a
   couple of checks instead of after the whole chain.

On the build before this ordering, Oblivion.esm's INFOs carried 311,739
conditions (mean 18, max 753), 204,470 of them in family chains. Vanilla
Skyrim.esm carries 55,641 over 31,465 INFOs (mean 1.8, max 22).

The injected gates arrive as packed bytes, so `_info_conditions` unpacks them
into (CTDA, CIS2) pairs and orders the combined list once. The list must not
end on an Or flag; both builders clear a trailing one.

## <a id="getincell-prefix-family"></a>GetInCell names a prefix family, not a cell

**Code:** `tes5_import/base/cell_family.py`, `tes5_import/base/locations.py`
(`_build_family_locations`).

TES4 matches the `GetInCell` argument as an EditorID **prefix**:
`GetInCell Anvil` is true in `AnvilTheCountsArms` and in the exterior
`AnvilBayWest01`. The named cell is often an empty dummy that exists only to
anchor the family. The script-side fix for the same semantics is
[tes5_import_quest.md](tes5_import_quest.md#1-getincell-matched-one-cell-instead-of-the-whole-prefix-family).

TES5 tests one exact cell. The condition handler (1.6.659 RVA `0x2ED0E0`)
requires the param to be a CELL (form type `0x3C`) and compares it with the
subject's parent cell (`+0x60`). There is no name comparison. Converted
verbatim, every family condition passed only in the empty dummy cell, so
"Anvil" and the other city topics vanished. On Oblivion.esm that affected
2,879 dialogue conditions: 692 on the INFOs themselves, and 2,187 copied from
10 `NQD<City>` quests. 95 families were affected; the largest are IC (431
cells), Anvil (91) and Chorrol (86).

**An exterior cell cannot be a condition parameter.** It is not a form until
its grid loads. Measured live with `TESForm::LookupByID`: the interior
`AnvilTheCountsArms` resolved to a CELL, but the exteriors `AnvilBayWest01` and
`AnvilExteriorLighthouse02` returned null. Vanilla agrees: all 919 of
Skyrim.esm's `GetInCell` params are interiors. The family's exterior cells are
never whole worldspaces either (0 of 61 family/worldspace pairs), so
`GetInWorldspace` cannot stand in for them.

**Re-expression.**

- Each interior member gets an exact `GetInCell(member)`.
- The exterior members are tested with `LocationHasKeyword(TES4InCell_<anchor>)`.
  That handler (`0x2F3EC0`) checks only the subject's *current* location, with
  no parent walk. For an actor, the current location comes from its parent
  cell's own XLCN before any worldspace fallback (`0x275840`).
- So every exterior member gets its own Location
  (`TES4<Cell>CellLocation`). It is a child of the square's former location,
  with that location's name, marker and radius, so the load-door name and map
  discovery are unchanged. Vanilla shares MNAM between a place and its child 24
  times. The child owns the square's LCEC exclusively, and its KWDA lists every
  family keyword the cell belongs to.
- Interiors keep their ordinary Location, because the child locations are
  built after the door pass.

"In the family" becomes an OR over those tests, and "not in the family" an AND.
The rewrite runs per OR group in conjunctive form: `X OR not-in-F` becomes the
AND over members m of `X OR not-in-m`, so an existing OR group keeps its
meaning.

A keyword is created only for an anchor that some condition in the plugin
names and whose family holds one of this plugin's own exterior cells. A
dependent plugin adopts its master's keyword by EditorID. That plugin's own
exterior family cells are tagged only when it builds Locations (i.e. when it
owns a new worldspace).

## <a id="chargen-identity-to-menu-globals"></a>Chargen-identity conditions become menu-choice globals

`CHARGEN_CHOICE` maps a TES4 function index to
`(choice GLOB output FormID, {param fid24 -> menu index})`.

Two TES4 chargen conditions cannot survive as themselves:
`GetIsPlayerBirthsign` (224) is **dead in Skyrim** — that index is reused for
`GetVATSMode` — and `GetPCIsClass` (129) **can never be true**, because the
player never has a TES4 CLAS.

The converted `ShowBirthsignMenu` / `ShowClassMenu` write the picked menu index
+ 1 into a GLOB (0 = not chosen), so these conditions become
`GetGlobalValue(<choice>) ==/!= index+1`.

**Why it matters:** the Emperor's "Your stars are not mine. Today the
&lt;sign&gt;…" lines are **13 INFOs each gated on one sign**. Without this
translation the first INFO always won regardless of what the player picked.

The table is populated per plugin by the import pipeline and is empty when the
plugin ships no BSGN/CLAS records — 224 then falls through to `_FUNC_DROP` as
before.

## <a id="non-actor-speaker-drop"></a>Speak-as topics drop the actor-interrogating conditions

`NON_ACTOR_SPEAKER_DROP` lists conditions that interrogate the SPEAKER as an
actor. A speak-as topic is spoken by a **non-actor reference**, which has no
base NPC, no voice type and no race, so each of these is unsatisfiable there —
and only there.

| Index | Function | Why it cannot pass |
|---|---|---|
| 72 | `GetIsID` | compares the speaker's base form |
| 254 | `GetIsPlayableRace` | rides in from the QUST-level condition copy |

## <a id="actor-value-vs-book-skill-table"></a>Actor-value indices diverge from the BOOK skill table

`_TES4_AV_TO_TES5` maps a TES4 actor-value index to its TES5 index. Skills
mostly follow `base.equivalents.TES4_SKILL_TO_TES5_INDEX`, but **two entries
deliberately differ**, because the two tables answer different questions: the
BOOK table must name a real *trainable* skill, while a CONDITION only has to
read a comparable number.

| TES4 value | BOOK table | Condition table | Why |
|---|---|---|---|
| Mercantile | Pickpocket | **Speech (17)** | Mercantile is Oblivion's haggling skill and Speech is Skyrim's. Pickpocket appears in the BOOK table only because Skyrim already ships a Speech skill book for that slot. |
| Athletics, Acrobatics | — | **Stamina (26)** | Neither has a Skyrim skill at all; Stamina is the athletic-capacity value the engine actually tracks. |

Changing either to match the BOOK table would make the condition read a value
the actor never trains, so it would compare against a constant.

## <a id="fallout-ctda"></a>A Fallout CTDA is 28 bytes and numbers its functions differently

**Code:** `tes5_import/base/conditions_falloutnv.py`, applied by `convert_ctda`;
table: `tes5_import/generated/ctda_fnv_remap.py` from
`tools/generators/gen_ctda_fnv_remap.py`.

FO3/FNV write TES4's 20 shared bytes, then an explicit **Run On** u32 and a
**Reference** u32 where TES4 has 4 unused bytes (xEdit `wbDefinitionsFNV.pas`
`wbConditions`). The length is the format signal: a 28-byte raw is Fallout, a
24-byte raw is TES4, so no per-game switch is needed.

Census over FalloutNV.esm's 59,664 INFO conditions: Run On 0 (Subject) 55,872,
1 (Target) 2,905, 2 (Reference) 821, 3 (Combat Target) 66. TES4's type-byte
bit `0x02` (run on target) is set on **none** of them, so Run On = Target is
translated onto that bit and takes the same Say-topic retargeting/drop path
TES4 conditions get; the other values pass through with the reference
load-order remapped.

### Function indices

Skyrim's table (402 functions) and Fallout's (452) agree only up to the point
where the games diverged. Joined by name: **58 functions moved slot, 237 have
no Skyrim function** (a slot Skyrim reuses for another name counts as absent).
Before the remap every Fallout index passed through unchanged, so
`GetIsVoiceType` (427) invoked Skyrim's 427 `GetPlantedExplosive`.

Measured over the export (function, count):

| Record | Conditions | Same slot | Moved | Absent |
|---|---|---|---|---|
| INFO | 59,664 | 38,665 | 17,573 (GetIsVoiceType 16,833; GetQuestCompleted 324; HasPerk 123) | 3,426 (GetReputationThreshold 1,565; GetObjectiveCompleted 966; GetObjectiveDisplayed 745) |
| PACK | 2,992 | 2,861 | 37 | 94 |
| QUST | 1,501 | 1,254 | 169 | 78 |
| ALCH | 341 | 11 | 151 (HasPerk 147) | 179 (IsHardcore) |

Absent functions are dropped, failing open, the way TES4's `_FUNC_DROP` does.
`GetDisposition` (76) is absent in Skyrim but is kept on the Fallout path too so
the disposition-tier evaluation applies. `GetScriptVariable` (53) and
`GetQuestVariable` (79) keep TES4's treatment: the strings path rewrites them
to the VM reads, every other path drops them.

## <a id="convert-ctda-phases"></a>`convert_ctda`: the three phases and why each is shaped as it is

**Code:** `tes5_import/base/conditions.py` `convert_ctda`, `_disposition_fields`,
`_convert_params`, `_target_run_on`.

### <a id="say-driven-topics"></a>Why a condition needs `run_on_target_ref` and `in_speak_as_topic`

Skyrim's `Actor.Say()` has no dialogue target, so in a SCRIPT-DRIVEN (Say/SayTo)
topic a Run On = Target condition evaluates against nothing and can never pass
(CharacterGen: every Valen Dreth taunt is race-of-target gated, and the whole
intro froze). When the script's SayTo target is known and unique the condition
is retargeted to Run On = Reference on that ref, which is equivalent, for menu
dialogue too, where the target IS the player. When the topic's targets are
mixed or unknown, `drop_run_on_target` discards the condition: Oblivion's call
sites already pick speaker and topic, so auto-pass is closer to intent than
never-pass.

`in_speak_as_topic` marks an INFO whose topic is reached ONLY through a TES4
`Say <topic> <flag> <speak-as NPC> <flag>` (`ArenaMatchPlayerRef.Say Announcer
1 ArenaMouth 1`). Oblivion resolves the speaker's identity to that NPC even
though an XMarker emits the sound, so the INFO's subject-run `GetIsID
<thatNPC>` passes. Skyrim's Say has no such argument, the subject is the
emitting marker, and the condition can never pass: no INFO is selected and the
line is silent while the caller's timers run on. That is the same never-pass
shape `drop_run_on_target` handles on the target side, so it gets the same
treatment. It is keyed on the TOPIC, never on the NPC: an NPC is not "a voice".
SEThadon is a real placed actor who speaks his own dialogue AND lends his
identity to a marker-spoken shout, and keying on him stripped the authored
`GetIsID(SEThadon)` from lines he delivers himself (53 INFOs name both a voice
identity and a real speaker). A dedicated topic is the authored fact; see
`talking_activators.py`.

### <a id="disposition-param"></a>GetDisposition becomes GetRelationshipRank on PlayerRef

The 0-100 disposition is translated onto Skyrim's relationship rank
(`disposition_to_rank`). A Use Global comparison names a GLOB holding a
disposition that cannot be rescaled, so that gate is dropped rather than
compared against a rank on the wrong scale. The parameter is an ACTOR (engine
param type 0x06), a placed REFERENCE, so the player is PlayerRef `0x14`, NOT
the player base NPC `0x07`. `0x07` is what GetIsID takes (param type 0x15,
ObjectID); reusing it handed the engine a TESNPC where it dereferenced an
Actor: EXCEPTION_ACCESS_VIOLATION on the first GREETING with the player TESNPC
in RSI. All 234 vanilla Skyrim.esm uses of the function pass `0x14` or 0.

### <a id="formid-params"></a>Only FormID parameters are load-order remapped

Most functions take a plain integer, enum or float, and the engine uses
several as a RAW ARRAY INDEX: `GetBaseActorValue(Speechcraft=32)` remapped to
`0x01000020` indexed 16.7M entries off the actor-value table and crashed the
dialogue menu on every NPC. `CTDA_FORMID_PARAMS` is keyed by the POST-remap
(TES5) index because that is the function the output invokes. Actor-value
parameters are a raw index into each game's own table, which do not align:
attributes have no Skyrim equivalent and drop (fail open); skills and shared
derived values translate through `_TES4_AV_TO_TES5`. Race parameters translate
to the Skyrim race the converted NPCs actually use (`_map_race_param`), or the
condition drops.

### <a id="run-on-target"></a>Run On = Target under a script-driven topic

TES4's run-on-target flag bit becomes TES5 Run On = 1 (Target) and the bit is
cleared so it is not double-counted.

The bounty tests are the exception (`_PLAYER_BOUNTY_FUNCS`). Oblivion's arrest
greetings ask `GetCrimeGold` / `CanPayCrimeGold` of the TARGET, the player,
whose bounty is global; Skyrim's versions answer "the player's bounty in the
SUBJECT's crime faction", so run on the player -- who has none -- they are
always 0 and no arrest line could pass. They run on the subject, as vanilla's
`DialogueCrimeGuards` lines do. In a Say-driven topic there is no
dialogue target, so a target-run condition can never pass. With a resolved,
unique listener the condition is retargeted to Run On = Reference on that ref,
EXCEPT for identity functions (`_NO_TARGET_RETARGET_FUNCS`): a GetIsID pinned
to a reference compares the wrong base form and can never pass, the
667-GREETING regression. Dropping is different from retargeting and the
identity veto must NOT block it: this is what stalled CharacterGen at stage 26.
The 26 to 27 bridge is a single GOODBYE INFO (`0005144A`, "She's dead. I'm
sorry, sire, but we have to keep moving.") conditioned on GetIsID(Baurus) AND
GetIsID(UrielSeptim)[RunOnTarget] AND GetStage(CharacterGen)==26. Baurus
delivers it from his poll via Actor.Say(), so the target-run GetIsID never
passed, `setstage charactergen 27` never ran, and the intro stopped in
silence; `setstage 27` from the console resumed it. A resolved listener with
an identity function is likewise dropped: the listener IS the NPC the call
site addresses, so the authored check is statically satisfied (first hit: the
restored NPC-conversation head topics, whose GetIsID(listener)[Target]
otherwise survived as a dead Run On = Target).
