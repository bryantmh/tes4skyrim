# tes5_import/dialogue/converter.py - dialogue and voice

**Code:** `tes5_import/dialogue/converter.py`, `script_convert/converter.py`, `tes5_import/dialogue/conversations.py`, `tes5_import/base/object_scripts.py`

## Contents

- [Dialogue / Quest Conversion Notes (DIAL / INFO / QUST / DLBR / DLVW)](#dialogue-quest-conversion-notes)
- [DIAL / INFO record structure](#dial-info-record-structure)
- [Branches, views and topic ownership](#branches-views-topic-ownership)
- [Voice types and conditions](#voice-types-conditions)
- [QUST records and journal](#qust-records-journal)
- [Voice files, lip sync and audio](#voice-files-lip-sync-audio)
- [Known gaps and defects](#known-gaps-defects)
- [Ambient dialogue channels: diagnosis and plan of attack](#ambient-dialogue-channels-diagnosis-plan)
- [The three channels](#three-channels)
- [Problem 1 — GREETING lines are on the ambient channel](#problem-1-greeting-lines-are)
- [Problem 2 — NPC-to-NPC conversation is in the player's menu](#problem-2-npc-npc-conversation)
- [Plan of attack — ordered by ease of fixing](#plan-attack-ordered-ease-fixing)
- [The NPC-to-NPC conversation scheduler](#npc-npc-conversation-scheduler)
- [psc-declared Conv* properties must equal the VMAD-bound set, exactly](#psc-declared-conv-properties-must)
- [Verification](#verification)
- [What Oblivion dialogue does not transfer to Skyrim, and what to do about it](#what-oblivion-dialogue-does-not)
- [1. Disposition — 1,451 INFOs — *translate, do not drop*](#1-disposition-1451-infos-translate)
- [2. AddTopic — 586 gated topics](#2-addtopic-586-gated-topics)
- [3. Persuasion — 39 DIALs, 130 INFOs](#3-persuasion-39-dials-130)
- [4. Reply-only topics — 1,955 topics](#4-reply-only-topics-1955)
- [5. Smaller gaps, with verdicts](#5-smaller-gaps-with-verdicts)
- [How to check any of this yourself](#how-check-any-this-yourself)
- [Speak-as lines: what works and what was reverted](#speak-as-lines-what-works)
- [Speak-as lines: Say() on a voiced stand-in, with the in-head flag (2026-08-19)](#speak-as-lines-say-voiced)

## Dialogue / Quest Conversion Notes (DIAL / INFO / QUST / DLBR / DLVW)
<a id="dialogue-quest-conversion-notes"></a>

Linked from [CLAUDE.md](../../CLAUDE.md). Narrative implementation notes from the
DIAL/INFO/QUST/voice conversion work. For the underlying record systems in each
game, see the `oblivion-dialog-system`, `skyrim-dialog-system`, and
`oblivion-to-skyrim-dialog` skills.


## DIAL / INFO record structure
<a id="dial-info-record-structure"></a>

- **Skyrim dialogue architecture**: QUST owns DIAL topics → DLBR branches link DIAL to QUST → INFO records have GetIsVoiceType conditions → engine routes dialogue by voice type
- **Per-quest topic ownership (2026-07 design)**: Single-quest topics → owned by their original quest (remapped QNAM); Skyrim then only evaluates their INFOs while that quest runs = Oblivion's QSTI gating, natively. Shared (multi-QSTI) or quest-less topics → always-running `TES4DialogueGeneric` quest, and each INFO whose own QSTI.Quest is non-SGE gets an injected GetQuestRunning(own quest) gate (Oblivion evaluates INFOs per-INFO-quest; vanilla Skyrim models shared subjects as one DIAL per quest — 288 separate HELO topics).
- **TES5 DIAL subrecord order**: EDID FULL PNAM(float priority=50.0) [BNAM(branch FID)] QNAM(quest) DATA(4B) SNAM(4-char code U32) TIFC(info count U32)
- **DIAL DATA** = TopicFlags(U8) + Category(U8) + Subtype(U16 LE) = 4 bytes total. **CRASH WARNING (verified 2026-07)**: writing subtype in byte1/category in the U16 puts an out-of-range value where the engine reads category → it indexes per-category topic tables out of bounds → EXCEPTION_ACCESS_VIOLATION at startup while initializing topics (crash log shows the TESTopic* + owning TESQuest*). Verified vs vanilla: Hello = `00 07 49 00` (cat 7, subtype 73).
- **DIAL subtype NUMBERS**: take from real Skyrim.esm data, NOT xEdit's display enum (which is shifted ~+6; the field is cpIgnore in xEdit and synced from SNAM). Real: Hello=73 GBYE=72 IDLE=88 ATCK=20 HIT_=23 FLEE=24 BLED=25 BLOC=29 TAUT=30 STEA=32 ASSA=36 MURD=37 TRES=43 NOTA=51 LOTN=57 OBCO=69 NOTI=70 TITG=71 IDAT=84. SNAM is what the engine keys subtype behavior on.
- **DIAL SNAM** = 4-char subtype code stored as raw ASCII bytes (e.g. b'HELO', b'CUST')
- **TES4→TES5 CTDA function reconciliation (data-verified 2026-07)**: joined both xEdit function tables — same-index-same-name functions pass through (incl. 50 GetTalkedToPC, which IS in TES5; an older drop of it was wrong). Renames at same index (128 GetFatigue→GetStaminaPercentage, 215, 327, 339 Horse→Mount) pass through. Remap: 101→263 IsWeaponOut, 116→459 GetCrimeGold, 127→497 CanPayCrimeGold. Vanilla CTDA tail = runOn 0, reference 0, param3 -1. TES4 type-bit 0x02 (Run on Target) → TES5 RunOn field =1 + CLEAR the bit (in TES5 that bit means "use aliases").
- **DIAL Category**: Bark topics → 7(Misc) or 3(Combat) or 5(Detection); all conversation topics → 0(Topic). Old TES4 type-based mapping removed.
- **DIAL BNAM**: Present on ALL non-bark conversation topics (links to DLBR). Bark topics must NOT have BNAM. TCLT target topics ALSO get BNAM — vanilla Skyrim requires BNAM on ALL CUST topics for the engine to route dialog to them.
- **Branch level (DNAM) rule (2026-07)**: a TCLT-target topic that is NEVER explicitly AddTopic'd is choice-only in Oblivion (nothing ever adds it to the menu) → Normal branch (DNAM=0), e.g. Azzan's "Yes. Sign me up."/"I'm not interested." and FGC01Choice1 "It was a mountain lion." — top-level branches on these leak player choice lines into the topic menu. TCLT targets that ARE explicitly added stay top-level + unlock-gated, with their TCLT-parent INFOs as revealers.
- **INFO order within a topic = quest priority (2026-07)**: Oblivion picks the first passing INFO in QUEST PRIORITY order (desc), not file order; Skyrim walks the topic's physical INFO list. Converted topics must sort children by their own QSTI quest's DATA.Priority (stable). Without this, Azzan's priority-11 first-meeting intro greeting outranks the priority-60 FG-ad greeting that reveals jointheFightersGuild/FightersGuildTopic → the join topics never unlock.
- **Quest journal visibility / markers (2026-07)**: QUST DNAM Type=0 (None) is Skyrim's journal-INVISIBLE control-quest type — the quest never lists in the journal, can't be tracked, and objective targets never produce compass/map markers even when objectives+QSTA+aliases are perfect (vanilla: only 16/~396 objective-bearing quests are Type 0). Converted quests with journal stage text get Type=8 (Side Quest).
- **TES4 DIAL DATA.Type classification**: Type 0=Topic (top-level, gets DLBR DNAM=1), Type 1=Conversation (chain topic, reachable via TCLT links, gets DLBR DNAM=0/Normal), Type 2=Combat (bark), Type 3=Persuasion (skipped), Type 4=Detection (bark), Type 5=Service (skipped), Type 6=Misc (bark). Type 1 is NOT excluded from DLBR — they need Normal branches for TCLT routing.

## Branches, views and topic ownership
<a id="branches-views-topic-ownership"></a>

- **DLBR (Dialog Branch)**: EDID + QNAM(quest FID) + TNAM(0=Player) + DNAM(0=Normal or 1=TopLevel) + SNAM(starting DIAL FID). Created for ALL non-bark DIAL topics. Top-level topics get DNAM=1 (appear in dialog menu). TCLT chain topics get DNAM=0 (only reachable via TCLT choice links, not shown in menu).
- **DLVW (Dialog View)**: EDID + QNAM(quest) + BNAM[](branch FIDs) + TNAM[](topic FIDs) + ENAM(view type) + DNAM(show all text). CK UI metadata, one per quest.
- **Service-menu gate (`_service_gate`)**: Barter/Training topics carry two ANDed CTDAs — who offers the service (merchant marker / trainer faction, `GetInFaction`) AND `GetOffersServicesNow` (func 255). Faction membership is permanent, so the faction condition alone left shopkeepers offering "What have you got for sale?" in the street at any hour; TES4 gated this implicitly through its service menu, which has no converted equivalent. Only ONE faction condition either way: an OR-chain over every vendor faction put 25-30 CTDAs on each INFO (vanilla max 22, max OR-run 20) and the engine silently dropped every gated line. Func 255 is vanilla-legal and measured — 165 vanilla Skyrim INFO CTDAs use it, all with run-on 0, operator `==`, params 0. The gate is prepended per-INFO, so it covers all 57 Barter and 10 Training INFOs from one site. Repair/Recharge/Travel remain dropped upstream in `SERVICE_MENU_TOPICS` (26 more INFOs); each needs its own Papyrus menu fragment and Skyrim has no direct Travel equivalent.

## Voice types and conditions
<a id="voice-types-conditions"></a>

- **GetIsVoiceType (func 426)**: Every Skyrim INFO must have this condition. Routes dialogue to the correct voice type. OR'd for multiple voice types. NPC-specific INFOs use GetIsID(npc_fid) from TES4 + GetIsVoiceType for the NPC's voice type. Generic fallback INFOs (no GetIsID) inherit voice types from NPC-specific siblings in the same topic — this prevents conditionless INFOs from making topics appear for ALL NPCs.
- **CTDA OR flag**: bit 0 of type byte. Voice type chain: VT1(OR)|VT2(OR)|...|VTn(AND) → evaluates as (any voice type matches) AND (remaining TES4 conditions). LAST voice type CTDA must NOT have OR flag.
- **Voice type injection order**: Voice type CTDAs are injected BEFORE TES4-converted conditions in INFO. This isolates the OR chain from any trailing OR flags in TES4 data.

## QUST records and journal
<a id="qust-records-journal"></a>

- **QUST dialogue flags**: QUSTs that own DIAL topics get: 0x0001(StartGameEnabled) + 0x0010(StartsEnabled) = 0x0011. **NEVER set HasDialogueData (0x8000)** — Skyrim does not use this flag and it blocks dialogue processing.
- **QUST DNAM format**: 12 bytes: Flags(U16) + Priority(U8) + FormVer(U8=**0 always**) + Unknown(4B) + Type(U32). FormVer must be 0 (not 44). Priority carries the authored TES4 value clamped to the engine's 0-100 band — see the priority entry below.
- **Orphan DIALs**: DIALs without quest association get assigned to a catch-all quest `TES4DialogueGeneric` (Flags=0x0011, Priority=0, FormVer=0). ALL DIALs MUST have QNAM or the engine ignores them.
- **ONE bark/greeting topic per quest per subtype (2026-07-11, engine rule)**: Skyrim honors only ONE topic of a given bark subtype (Hello/GBYE/IDLE/combat/detection) per owning quest — verified in Skyrim.esm: all 297 vanilla HELO topics have distinct owners, zero quests own two. The old converter dumped ALL greetings into one giant `GREETING` topic + one `HELLO` topic + a `TES4FallbackHello` topic, ALL owned by `TES4DialogueGeneric` → three HELO topics under one quest → engine honored only one, so real greetings never fired and only the fallback "Hello." showed (removing the fallback left NPCs uninteractable). **Fix (`dialog_converter._build_bark_pass`)**: a global pass regroups every bark INFO by (remapped owning quest, subtype) across ALL bark DIALs and emits exactly ONE topic per group (GREETING+HELLO INFOs owned by the same quest merge into that quest's single HELO topic). Result: 5636 greeting INFOs → 293 HELO topics (vanilla-scale), zero same-subtype collisions. Quest ownership provides the "only while my quest runs" gate natively (no injected GetQuestRunning). Quest-less bark INFOs → a synthetic per-subtype SGE `TES4Generic<SNAM>` quest (none needed for Oblivion — every greeting has a real QSTI; the universal "Yes?/Good day." lines belong to the SGE `Generic` quest). The `TES4FallbackHello` topic and `_build_fallback_greetings` are REMOVED. Bark INFOs drop only TCLT choices that point at ANOTHER bark (those dangle after the split); choices that point at a conversation topic are KEPT — see the greeting→response note below. The **dialog_emulator does NOT model this rule**, so it shows real greetings as PASS when in-game they never fire.
- **Greeting→response routing: bark INFOs must KEEP TCLT to conversation topics (2026-07-12)**: symptom — an NPC delivers a report-back greeting ("Well, what have you found?…") but the player CANNOT activate to pick a response (FGC01Rats: Arvena asks what happened in the basement, no answer option appears). Cause: in Oblivion a GREETING INFO carries `Choice` (TCLT) links that present the player's response topics (FGC01Choice1 "It was a mountain lion."). The bark pass had `convert_INFO(drop_tclt=True)`, discarding EVERY such link — measured scope: **983 greeting/bark INFOs carry choices, 1701 of those choices point at conversation topics** (Sheogorath, Haskill, arrest "Resist Arrest/Serve Sentence", hundreds of quest greetings — all broken game-wide). This IS a supported vanilla pattern: **6 vanilla HELO greetings TCLT→CUST response topics** (C03SkorQuestStartBranchTopic, MS05ViarmoApplicationTopic, DA03StartLodBranchTopic), and every one of those target topics uses a **top-level branch (DNAM=1)** — the greeting bark can't hold a menu, so the engine surfaces the response as a top-level topic once the bark's TCLT points at it. **Fix (`dialog_converter`)**: (1) `convert_INFO` takes `bark_dial_fids` (set) instead of a blanket `drop_tclt` — a bark INFO keeps choices whose target is NOT a bark, drops choices targeting another bark (those get split/merged and would dangle). (2) `bark_choice_targets` = conversation topics reached from a bark choice → those get a **top-level** DLBR branch (`is_linked=False`), NOT the Normal branch that ordinary mid-chain TCLT targets get. The response topics keep their own GetStage conditions so they surface only at the right quest stage. Verified in the built ESM: greeting INFO 01036622 TCLT→01036613 (FGC01Choice1), branch DNAM=1; `dialog_walkthrough --check-chains` = 0 broken chains (bark→bark drop still clean); 1665 HELO→conversation links preserved. Regression: `test_greeting_choice_reaches_response_topic`.
- **Choice-reached top-level topics must INHERIT the greeting's timing gate (2026-07-12)**: after the fix above, a promoted top-level response topic appears in the menu whenever ITS OWN INFO conditions pass. But in Oblivion those response INFOs carry NO stage condition — the response was only reachable because the revealing greeting was stage-gated (Arvena's "what did you find?" = `GetStage(FGC01Rats)==30`; the response INFO has only `GetIsID(Arvena)`). So the promoted topic leaked in from the FIRST conversation (FGC01Choice1 "It was a mountain lion." selectable before killing the lion). **Fix (`dialog_converter`)**: `bark_choice_gate` maps each bark-choice target → the QUEST-STATE conditions (`_QUEST_STATE_FUNCS` = 56 GetQuestRunning / 58 GetStage / 59 GetStageDone / 99 GetQuestCompleted) of every greeting that reveals it; `_quest_state_ctdas` extracts+converts just those (identity/faction excluded — the response keeps its own GetIsID); `_bark_choice_gate_bytes` OR-combines revealers (any live greeting → available; an always-available revealer → NO gate); injected as an AND gate ahead of the OR-chains. Skipped when the response INFO already has its OWN quest-state condition (`_has_quest_state_condition`) — it knows its timing and ANDing a different stage would suppress it. Corpus: 658 bark-choice targets; 184 always-available (no gate); revealer quest-state-cond histogram {0:1030, 1:481, 2:180, 3:10}; the rare (~31) multi-revealer-multi-AND case that a flat CTDA list can't express as OR-of-ANDs uses the first revealer's group (losing the gate = the leak; slightly-off timing = lesser evil). Regression asserts the response INFO carries GetStage(58).
- **Mention-in-BARK must not un-gate an AddTopic topic (2026-07-12)**: separate manifestation of the same "lost Oblivion gating" class — Azzan offered "Advancement" before the Fighters Guild was joined. `advancementFG` IS AddTopic-gated (revealed by the FGJoin1 join line), but `build_unlock_plan`'s bark-reveal stripping (bark-revealed topics aren't gated, since a greeting fires on first contact) was triggered by the **auto-add-on-mention** heuristic: Oblivion auto-adds a topic when a spoken line's text contains the topic's FULL name, and **6 late-game GREETING lines contain the word "advancement"** ("you are ready for advancement"). Treating a prose mention in a bark as a first-contact reveal stripped the gate from **162 topics**. **Fix (`dialog_unlocks.build_unlock_plan`)**: split each revealer's globals into `explicit_set` (AddTopic data list / AddTopic script cmd / Choice / TCLT — an unconditional-on-play reveal) vs `mention_set` (FULL-name prose match — rides the line's OWN conditions). Only `explicit_set` from a bark feeds `bark_revealed`; a mention keeps the gate (the mentioning greeting is itself stage-gated, so the topic still unlocks when that line actually plays). Gated topics 206→397. Regression: `test_bark_prose_mention_does_not_ungate_topic`.
- **Quest-level dialogue conditions (2026-07-11)**: In Oblivion a QUST's OWN CTDAs gate ALL of that quest's dialogue — 367 of 390 quests use them. `NQDBeggars` is `GetInFaction(Beggars)`, which is the ONLY thing keeping its **conditionless** beggar HELLO lines ("A coin for an old beggar?") on beggars. Skyrim has no quest-level dialogue gate, so the owning quest's converted CTDAs must be injected into every INFO it owns (`quest_dialog_ctdas` in `build_dialog_groups`, applied in `_convert_topic_infos`). Without this, every conditionless bark line greets the whole world. Note func **71 = GetInFaction** in TES4 (67 = GetInCell); TES4 365 `GetPlayerInSEWorld` is `IsChild` at the same index in TES5 and is correctly dropped, while 254 `GetIsPlayableRace` passes through.
- **Quest arbitration priority lives on QUST.DNAM.Priority, NEVER on DIAL PNAM (corrected 2026-07-21)**: Oblivion arbitrates barks by QUEST PRIORITY (`NQDBeggars`=12 beats `Generic`=5, so a beggar begs instead of saying "Good day."), and the converter reproduces that two ways — the **INFO sort order** inside a topic, and the owning **quest's DNAM.Priority**. It must NOT be written into the bark topic's PNAM. Doing so put `FGC01Rats`' GREETING at PNAM 161 while its own player topics kept the 50.0 default, and Pinarus Inventius lost *every* topic he owned (the FGC01Rats mountain-lion topic AND his unrelated Acrobatics training topic) — two unrelated topics on one NPC, which no per-topic condition bug can explain. Vanilla census (Skyrim.esm, 15,037 DIALs): PNAM is the 50.0 default on **5375/6535** player topics and **659/664** Misc/greeting topics, min 10.0, and greetings are never ranked above the topic list.
- **QUST.DNAM.Priority band is 0-100, and staged quests keep their AUTHORED value (2026-07-21)**: vanilla's 1811 quests span 0-100 with nothing above it, in sparse meaningful clusters (822 at 30, 280 at 0). Two corrections failed before the current one and must not be retried: a uniform **upward shift** on staged quests overflowed the band (TES4 60 + 101 = 161; 265/391 quests over 100 — and since the byte also arbitrates a quest ALIAS PACKAGE against an actor's standing schedule, that broke AI too), and a **two-band rescale** stayed in range but destroyed the authored values, collapsing 390 quests onto 35 priorities with 125 tied at 83. The working model is a **downward clamp on zero-stage container quests only** (`ZERO_STAGE_TOP`=49): staged quests are never touched, containers already under the ceiling keep their authored value, and only the over-ceiling ones are order-preservingly compressed into the headroom below it (so `MQConversations`=85 still outranks `Dark00General`=50). Ceiling is FIXED, not `min(staged)` — three staged quests (MQDragonArmor, SE06Battle, E3) are authored at 0. Net effect on Oblivion.esm: 52 of 390 quests change, `MG00General` 61→41 so it stops outranking `MG04Restore`=60.
- **Sibling audience gate for conditionless barks (2026-07-11)**: a few Oblivion bark INFOs carry NO conditions at all AND their quest supplies no audience scope (MS45's "I think we should get out of here, quick!"). Left alone they greet every NPC. Their siblings under the same quest+topic DO name the intended audience (`GetInFaction(HackdirtBrethren)`), so a conditionless line inherits the siblings' faction/GetIsID OR-chain — but ONLY when the quest's own CTDAs don't already scope the audience (`_ctdas_scope_audience`), so the beggar lines stay quest-scoped rather than being narrowed to whichever NPC a sibling happens to name.
- **Identity (GetIsID) injection must not narrow an already-scoped INFO (2026-07-11)**: the GetIsID OR-chain injected into conditionless conversation INFOs must be skipped when the INFO already states its own audience — `has_audience_condition()` = any of GetInCell(67)/GetIsClass(68)/GetIsRace(69)/GetInFaction(71)/GetIsID(72)/GetFactionRank(73). (GetIsSex(70) does NOT count — male/female is not an audience.) Bolting a sibling-derived GetIsID chain onto a `GetInCell(Anvil)`-gated city topic stripped it from most Anvil NPCs.
- **Voice prefix must use the EDID actually written to the record (2026-07-11)**: the engine builds the voice path from the DIAL record's OWN EditorID, so the voicemap must be keyed on the post-split EditorID (`GREETING_<quest>`), not the pre-split `GREETING`. Passing the original EDID made every greeting silent (files named `ms47_greeting_…` while the engine looked for `ms47_greeting_0101f426_…`). Verify with: recompute `voice_file_prefix(questEDID, dialEDID)` from the built ESM for every INFO and diff against `<esm>.voicemap.txt` — must be 0 mismatches.
- **NPC→VTYP mapping**: Built from NPC_+CREA records using TES4_RACE_FID_TO_EDID → VOICE_TYPE_MAP[(race_edid, gender)]. 3396 NPCs mapped to 27 voice types. ALL speakable NPCs (including CREA→NPC_) MUST have VTCK.
- **TES4-only condition functions dropped**: GetDisposition(76), GetVampire(40), IsYielding(104), IsPlayerInJail(171), GetPCInfamy(251) — these would always fail in TES5 and block valid dialogue.
- **Legacy variable reads TRANSLATED, not preserved (2026-07-15)**: TES4 `GetScriptVariable(53)` / `GetQuestVariable(79)` still exist at those indices in TES5 but read the DEAD legacy VM — vanilla Skyrim uses them ZERO times; a condition emitted against them can never pass (this is what broke Owyn's Battle-Raiment check: refusal line `GetScriptVariable(ICArenaMatchGateRef, WearingArmor)==0` "passed" while every `==1` accept line never did, and Kud-Ei's recommendation greetings `GetQuestVariable(MG03Illusion, 1)`). The live equivalents are `GetVMScriptVariable(630)` / `GetVMQuestVariable(629)` whose variable NAME travels in a **CIS2 subrecord right after the CTDA** as `::<name>_var` (the auto-property backing variable). `convert_ctda_list_with_strings` translates BOTH (var-index→name via `build_script_var_map`, which indexes refs AND QUSTs); `convert_INFO` and `quest_dialog_ctdas` now use it (packages already did). 53/79 are in `_FUNC_DROP` so any path that cannot emit CIS2 drops rather than emits a never-true condition. The CIS2 name must be built with `_safe_property_name` so converter renames (reserved words, `temp`→`Temp`) still match. Function 79 is GetQuestVariable (NOT GetIsPlayerBirthsign — that's function 224).
- **Papyrus `Conditional` flags are REQUIRED for the whole ::var_var system (2026-07-15)**: GetVMScriptVariable/GetVMQuestVariable (and CK cond-var lookups) only see variables compiled with the CONDITIONAL user flag, which needs `Conditional` on BOTH the ScriptName line and the `Auto` property (value types only). `script_convert/converter.py` now emits both on every converted script with Int/Float/Bool variables (incl. the ObjectReference→Int usage-based retype path). Without this the CK warns "Unable to find variable ::X_var on any VM scripts for form ..." (the packageVAR class of CK_WARNINGS) and every translated var condition is dead. Verified: external/papyrus-compiler supports the keyword; pex dump shows the `conditional` user flag on object + `::X_var`.
- **RACE condition params must be translated to Skyrim races (2026-07-15)**: RACE is never imported, so the generic remap left `GetIsRace(69)`/`GetPCIsRace(130)` (ptRace at the SAME index in both games) pointing at nonexistent forms — 2,650 INFO conditions dangling = the CK's "Unable to find Function Info TESForm (010224FD)..." warning class (0x224FD = Nord RACE) and race-flavored lines that never fire. `convert_ctda` now maps the param via TES4_RACE_FID_TO_EDID + RACE_MAP (the same map convert_NPC_ uses for RNAM, so GetIsRace(speaker) keeps matching converted actors); unmappable race → condition dropped.
- **Bark-choice gate inherits PLAYER-progress conditions too (2026-07-15)**: `_quest_state_ctdas` originally only harvested {56,58,59,99}; Agronak's challenge greetings gate on `GetFactionRank(ArenaCombatants)==7` RUN-ON-TARGET (the player) — Oblivion's faction-rank-as-questline-progress idiom — so the promoted "Yes, I wish to challenge you." topic had an EMPTY inherited gate and sat in his menu from the first conversation. The harvest now also takes run-on-target GetInFaction(71)/GetFactionRank(73) and the translated 53/79 var reads (gates are now [(ctda, cis2)] pairs). The `_has_quest_state_condition` suppression check deliberately still counts only {56,58,59,99} — a target INFO's own `GetQuestVariable==0` passes at game start and must not suppress the inherited gate.
- **Location/AI conditions on barks**: GetInCell(71) and GetCurrentAIProcedure(67) are PRESERVED on bark INFOs — they provide critical location and AI-state filtering. FormIDs are properly remapped, so these conditions work in TES5. Stripping them causes city-specific greetings to fire everywhere.
- **CTDA Use Global flag**: bit 2 (0x04), NOT bit 5 (0x20). Wrong bit causes all global-based conditions to fail.
- **TES5 INFO subrecord order**: EDID ENAM CNAM [TCLT[]] [TRDT NAM1 NAM2 NAM3]* CTDAs
- **INFO ENAM** = Flags(U16) + ResetHours(U16) = 4 bytes. Flags map from TES4 DATA.Flags with compatible mask 0x37 (bits 0=Goodbye, 1=Random, 2=SayOnce, 4=InfoRefusal, 5=RandomEnd — same bit positions)
- **INFO TRDT** = 24 bytes: EmotionType(U32) + EmotionValue(U32) + Unused(4) + ResponseNumber(U8) + Unused(3) + Sound(FormID=0 U32) + Flags(U8) + Unused(3)
- **INFO CNAM** = Favor Level U8 (0=None, required)
- **INFO TCLT** = repeated FormID subrecords for each choice/next topic (xEdit: "Link To" array)
- **QUST INDX** = StageIndex(U16) + StageFlags(U8) + Unknown(U8) = 4 bytes (NOT 2 bytes like TES4)
- **🔴 QUST VMAD MUST end with the alias-script array count — THE cause of "no quest markers" (2026-07-11)**. Per xEdit `wbVMADFragmentedQUST`, a QUST's VMAD is `Version, ObjectFormat, Scripts, ScriptFragmentsQuest, **Aliases**`, where Aliases is an array with an **S16 count prefix**. Our builder stopped after the fragments and never wrote that count. **The engine parses VMAD strictly: running off the end of the buffer where it expects the count makes it abandon the record's entire script/alias binding.** Result: EVERY quest alias fills as `NONE` *and* every QF script property comes back `None` — so an objective's QSTA points at an empty alias and no marker can ever be drawn, while the journal objective (which needs no alias) displays perfectly.
  - **Diagnosis that finally cracked it**: in-game console `sqv <questID>` showed **all four aliases NONE** with the quest running at the right stage and the target ref alive and selectable — and the QF script's object properties were `None` too. Two independent systems failing identically ⇒ one shared cause upstream of both ⇒ VMAD. Verified against Skyrim.esm: vanilla `DBSideContract03`'s 643-byte QUST VMAD only parses to 643/643 once the trailing S16 is read. `tests/test_script_converter.py::test_vmad_quest_parses_to_exactly_its_length` now round-trips the VMAD and requires every byte be consumed — a truncated tail is invisible to every other check.
  - **Lesson**: when a record's scripts AND its aliases are both empty at runtime but the record looks perfect field-by-field, suspect a **truncated/misparsed binary tail**, not the individual fields. And `sqv` is the fastest way to find it — it prints alias fill state directly.
- **Quest markers (targets)**: TES4 QSTA is QUEST-level (REFR + flags + GetStage conditions saying *when* that target's marker is live). TES5 QSTA is per-OBJECTIVE — and **vanilla leaves it UNCONDITIONAL**: across Skyrim.esm, objectives read `QOBJ FNAM NNAM QSTA [QSTA…]` with CTDAs a rare exception; the right target simply sits on the right objective, and the objective being *Displayed* is what selects the marker.
  - So resolve Oblivion's gates at BUILD time rather than replaying them: `_target_live_at_stage()` (tes5_import/dialogue/converter.py) evaluates each target's TES4 condition chain (AND of OR-groups; GetStage/GetStageDone understood, any other function treated as passing so a maybe never loses a marker) with GetStage == the objective's stage, and each objective emits only its live targets, with no CTDAs. FGC01Rats then walks Arvena→basement door→Arvena→Pinarus→…→Quill-Weave exactly as Oblivion did. (Carrying every target on every objective was a genuine defect, but it was NOT what suppressed the markers — the VMAD truncation above was.)
  - **Aliases** (one forced-ref per unique target): `ALST, ALID, FNAM, ALFR, VTCK, ALED` — **VTCK is present on 2687/2687 vanilla forced-ref aliases and on all 255 vanilla objective+forced-ref quests; a 100% invariant we were omitting.** FNAM=0x0292 (Optional 0x0002 — a fill failure must not block quest start — + AllowDead + AllowDisabled + AllowReserved), an attested vanilla combination; the old 0x109A appears nowhere in vanilla. ANAM = alias count. Objectives: ONE per stage index (engine keys by index; index = stage so the generated `SetObjectiveDisplayed(stage)` matches). Layout: stages, objectives, ANAM, aliases.
- **Known remaining gap — city map markers live in CHILD worldspaces**: Oblivion puts each city's map markers *inside* its city worldspace (AnvilWorld, ChorrolWorld, the IC districts — 37 markers total), and its map drew them. Skyrim's world map only renders markers in the root map worldspace (vanilla: 296/~300 marker-Locations anchor a marker in Tamriel 0x3C). So a converted Location whose MNAM marker sits in a child worldspace has nothing the *map* can draw (the compass, which works off the target ref's world position, is unaffected). Child worldspaces share Tamriel's coordinate space (AnvilWorld NAM0/NAM9 lie inside Tamriel's, grids match), so the fix is to anchor those Locations to a root-worldspace marker. Not yet implemented.
  - **Per-target QSTA conditions — export bug fixed 2026-07-11**: the QSTA→marker gating depends on the CTDAs that FOLLOW each QSTA in the TES4 stream (xEdit `wbDefinitionsTES4` QUST: `wbRArray('Targets', QSTA + wbCTDAs)`). The exporter previously used a flat `get_all_subrecords(rec,'CTDA')`, which (a) lost every per-target condition — so imported objectives carried all target aliases with NO gate and markers never advanced with the objective — and (b) mislabeled per-log-entry result-script CTDAs as quest-level `Condition[]`. `export_QUST` now walks the subrecord stream positionally, bucketing CTDAs into quest-level / per-log-entry / per-target and emitting `Target[i].Condition[k].Raw`. The importer's `convert_ctda_list(rec, prefix='Target[t].')` was already wired for this; it just had no data. Verified end-to-end on SE46 (funcs 58 GetStage / 79 GetQuestVariable / 84 GetDeadCount, quest FormID param remapped to output load order).
  - **Interior-target markers need the cell's XLCN — fixed 2026-07-11**: with correct objectives+aliases+conditions, a target inside an interior STILL produced no compass/map marker (journal entry showed, no arrow). Skyrim resolves an interior ref's map position through its CELL's Location (`ref → CELL.XLCN → LCTN.MNAM → map-marker REFR`); with no XLCN there is nowhere to draw the marker, but the journal text (needing no position) still appears. The converter only XLCN'd interiors whose entrance door sat on a map marker (~12% of interiors), so almost every city-house/shop/back-room quest target had no marker. `tes5_import/base/locations.py build_marker_locations` now, after the marker pass: (1) links every exterior-teleport-reachable interior to the Location of the grid cell (or worldspace) its entrance door stands in, then (2) transitively propagates that Location through interior→interior doors to a fixed point so basements/upper floors inherit their building's Location. Interior XLCN coverage 12%→82% (1855 cells; remainder are doorless/test cells, as in vanilla). Chain verified for FGC01Rats stage-10 target Arvena (house + basement → TES4AnvilCastleGateLocation → "Anvil Castle Gate" marker).
- **QUST stage log entries**: exported as `Stage[i].LogCount + Stage[i].Log[j].{Flags,Text}`; imported with one QSDT (U8) + optional CNAM (string) per log entry

## Voice files, lip sync and audio
<a id="voice-files-lip-sync-audio"></a>

- **Voice files (naming TRANSCRIBED FROM THE ENGINE, 2026-07-22)**: Skyrim resolves `Sound\Voice\<plugin>\<VoiceTypeEDID>\<prefix>_<fid8>_<n>.fuz|.xwm` at runtime from the CONVERTED records. `prefix` = owning-quest EDID + `_` + topic EDID, truncated per the engine algorithm below; topic EDID empty → quest + `_` (double underscore before FormID). `fid8` = 8 lowercase hex with load-order byte ZEROED (never shifted); `n` = TRDT response number. Implemented as `dialog_converter.voice_file_prefix()`. The importer writes `<esm>.voicemap.txt` ({info fid24 → prefix}) and `organize_voice_files(voice_map=...)` renames by FormID — the Oblivion filename prefix CANNOT be trusted (different quest ownership + records renamed after their audio was cut). Format conversion: ffmpeg→WAV→xWMAEncode→XWM (no ASF fallback).
- **Voice prefix truncation — read from the ENGINE, never fitted to filenames (2026-07-22)** — cause of "certain voice lines never play" (Azzan/Carahil/Christophe follow-up responses). `voice_file_prefix` cut the quest to `quest_edid[:10]` UNCONDITIONALLY, so any quest EditorID over 10 chars produced a name the engine never asks for: `MG04Restore`->`mg04restor_`, `FGD00JoinFG`->`fgd00joinf_`. **4,429 INFOs corrected.** The real algorithm is transcribed from the GOG/AE `SkyrimSE.exe` (file 0x3a5460 / va `0x1403a6060`; sibling builder at `0x1403a62b9`), which is NOT DRM-packed and so is statically readable — find it by locating the `"%s_%08X_%u"` literal (file 0x1694210) and xref'ing RIP-relative LEAs into it:
  ```
  A = owning-quest EDID ([rcx+0x40] from the TOPIC)   B = topic EDID
  if len(A) + len(B) > 25:        # cmp rax,0x19 / jbe -> verbatim
      if len(A) > 10:             # cmp rcx,0xa  / jbe
          A, B = A[:10], B[:15]   # mov byte[rsp+0x14a],0 ; lea rax,[rsp+0x3f]
      else:
          B = B[:25 - len(A)]     # lea rax,[rsp+0x49] ; sub rax,rcx
  sprintf(out, "%s_%s", A, B)
  ```
  A pair totalling <=25 is used VERBATIM — which is why the failing lines (23 and 18 chars) needed no cut at all. This matches Skyblivion's `Skyblivion - Copy voice files.pas` (`InfoFileName`), which was correct all along. **Do NOT re-derive this by fitting Oblivion's own filenames**: those follow Oblivion's rule, not Skyrim's — "no truncation" scores 99.8% against them and is WRONG for the engine (the engine rule scores 77.6%, the old bug 52.4%). Agreement with source names is irrelevant because `organize_voice_files` renames by INFO FormID. **Never mirror the rule** — `tools/dialog/voice_audit.py` kept a private copy that drifted and made the audit agree with the bug it existed to catch; it now imports `voice_file_prefix` directly.
- **Lip sync (2026-07-16)**: generated per voice line with the SSE Creation Kit's own `LipGenerator.exe` (`<SSE install>/Tools/LipGen/LipGenerator/`, auto-detected via registry; needs its sibling `FonixData.cdf`). Inputs are the intermediate 44.1 kHz WAV (it resamples internally — writes a `tmp16khz.wav` into its CWD, so every parallel job runs in a private temp dir) and the line's transcript. Transcripts come from the importer: `build_dialog_groups` collects `{(info_fid24, response_number): NAM1 text}` at the same site that fills the voicemap, and `_write_lip_text` emits `<esm>.liptext.txt` next to the ESM. **SSE only reads lip data from `.fuz` containers** (loose `.lip` files are LE-only), so `convert_file_to_xwm` packs lip+xwm as `.fuz` (`FUZE` magic, u32 version=1, u32 lip size, lip, xwm); lines with no transcript stay bare `.xwm` (audio plays, mouth doesn't move). Oblivion's own `.lip` files are an older FaceFX format and stay skipped at BSA extraction. The INFO `ENAM 0x0800 "No LIP File"` bit is intentionally never set. CLI: `LipGenerator <wav> "<text>"` → `<wav basename>.lip`.
- **Lip generation parallelism (2026-07-19)**: the Fonix engine inside LipGenerator.exe serializes ALL running instances machine-wide through a named mutex (`FonixMemoryMutex`, cleartext in the exe), capping aggregate throughput at **~8 lips/s no matter how many processes run** — the symptom is dozens of LipGenerator processes each at ~0.1% CPU (0.2s CPU per 6–9s wall; the tool's 1s sleep-poll loop waits on the contended mutex, so latency balloons too). The exe creates **no file mapping** (imports checked), so the mutex guards nothing shared between processes and renaming it is safe. `build_lipgen_pool()` (asset_convert/audio/audio_converter.py) writes per-worker copies of the exe with unique same-length mutex names (`FonixMemMtx_0000`…) + hard-linked `FonixData.cdf` into a temp pool dir; workers check exes out of a `queue.Queue`. Measured: ~8.5 → ~105 lips/s at 32 workers (128/s at 64), per-call latency 6–9s → ~0.3s; full-chain smoke (mp3→wav→lip→xwm→fuz) ~75 lines/s. Voice batches with transcripts use `_LIP_WORKER_COUNT` (2×CPU, ≤64) threads since jobs are mostly subprocess-wait. Fallback: if the mutex string isn't found exactly once (unknown exe version), the stock exe is used unpatched. Lip output is nondeterministic run-to-run even stock (gesture track seeding) — don't byte-compare .lip files. Nukem9's FaceFXWrapper was evaluated and rejected: same Fonix core, same machine-wide cap, and its copy of the mutex name is compressed (unpatchable).
- **Race→VoiceType mapping** is in `TES4_VOICE_TYPE_MAP` in bsa_extract.py; includes all Oblivion playable races + Shivering Isles races
- **Voice audit + the three silent-line causes (2026-07-16)**: `tools/dialog/voice_audit.py` recomputes every INFO's expected voice file (GetIsVoiceType folders × prefix × TRDT response numbers) from the BUILT ESM and diffs against the files on disk, classifying every miss (also finds orphan files and voicemap drift). It found and drove these fixes:
  1. **Shivering Isles voices were never extracted**: Steam GOTY merges SI INTO Oblivion.esm (the SE quests/dialogue/voice-types are in the ESM) but the assets stay in `DLCShiveringIsles - *.bsa`, which `get_bsa_files` keyed to DLCShiveringIsles.esp. All SI lines were silent and the golden saint/dark seducer/sheogorath voice folders didn't exist (~4,300 INFOs, 10.8K missing lookups). Fix: bsa_extract adds the `DLCShiveringIsles` BSAs for Oblivion.esm.
  2. **Generic voice gates routed voice types to lines they have no recording of**: Oblivion recorded MANY generic lines (rumors/INFOGENERAL, greetings, Question/Attack barks, service lines) for only a subset of race folders (histogram over 19.3K voiced lines: ~10K exist in ONE folder, only ~1.2K in all 11) — Oblivion played silence for the rest; in Skyrim a selected line with no voice file flashes past unreadably, which players see as "NPC has no voice". The injected topic-inherited GetIsVoiceType chains spanned every sibling voice type (19.5K missing lookups over 3,088 INFOs), and 2,073 generic INFOs had NO gate at all. Fix: **NOT IMPLEMENTED — `build_voice_inventory` does not exist anywhere in the source** (grepped 2026-08-08; this paragraph described a design that was never built). The intent was: scan the extracted `sound/Voice` tree into {info_fid24 → set(VTYP fid with a recording)} and have `_build_injected_ctdas` set each GENERIC INFO's gate to `(inherited ∩ recorded) or recorded` — **the recorded folders are ground truth of who speaks a line** (INFOGENERAL race/sex-conditioned lines are recorded for exactly that audience; an inherited gate that excludes the recorded voice type contradicts the line's own GetIsRace/GetIsSex conditions and the line can NEVER play). Unrecorded voice types fall through to a voiced sibling/catch-all. NPC-specific (GetIsID) lines are never touched (the NPC must keep their line; relocation already routes their audio), and lines with no recordings anywhere stay ungated (unvoiced content kept, as in Oblivion). 3,691 INFOs re-gated; recorded sets are ≤14 entries so OR-chains stay under the engine's ~20/22-condition drop threshold (see Barter lesson).
  3. **Stale output files accumulate across builds**: the runtime prefix embeds quest/topic EditorIDs (which embed allocated FormIDs), so import re-runs rename expected files; organize skipped existing files and never cleaned old names. **This was documented as fixed but the prune was never actually implemented** — verified 2026-07-22, when the voice-prefix correction produced 58,931 files on disk against 37,552 intended (21,387 stale). Real fix now in `organize_voice_files`: it records every path the run INTENDS to produce (before the already-exists skip, so a wanted file that needs no rewrite is never deleted) plus the VTYP folders it wrote into, then `prune_stale_voice_files()` removes any file in exactly those folders, with a voice extension, that is not in the intended set. Runs after conversion (a failed job must not delete the previous usable copy) and also on the early "everything already present" return — which is precisely the state a re-run lands in after a rename. `prune=False` opts out; the result dict gains `pruned`. **A stale file is worse than a missing one: it makes a still-broken run look fixed**, because the audio is sitting there under the old name.
  - Bethesda also shipped a few broken source recordings (0-byte or 209-byte stub MP3s, e.g. `generic_goodbye_0002b7ae` argonian/m, `mq05_seen_00095892` redguard/m) — ffmpeg fails on them; unfixable, they were silent in Oblivion too. ~34 orphan recordings reference response numbers deleted from the records during Oblivion's own development.
  - **Final audit state (2026-07-16)**: 39,868 lookups resolve (was 30,961); MISSING_IN_VTYP 19,466→5 (all broken stubs); NO_SOURCE_AUDIO 10,778→347 (genuinely unvoiced); no-voice-gate INFOs 2,073→10; 0 voicemap mismatches. Re-run `python tools/dialog/voice_audit.py` after any dialogue/audio pipeline change.
- **🔴 A LOCALISED plugin's voice folders are named after the race's FULL, not its EditorID (2026-08-08)** — cause of 651 unreachable Nehrim voice files. Oblivion's layout is `sound\voice\<plugin>\<RACE FULL>\<gender>\`, and in English FULL and EditorID are identical, so a table keyed on EditorIDs works by accident. Nehrim's diverge: its seven `Alemanne*` races all read `FULL=Alemanne`, `HighElf` reads `Hochelf`, `DarkElf` reads `Eraterna`, `Argonian` reads `Argonier`. Two independent failures followed. (1) `build_npc_to_vtyp_map` resolved the race name through the hardcoded `TES4_RACE_FID_TO_EDID`, which **cannot name a race the plugin invented** (`Alemanne1` at `0x18A893` is absent, so every NPC fell through to the `Imperial` default) and **silently misnames one that reuses an Oblivion FormID** (`0x19204` is HighElf in both games but Nehrim's reads Hochelf). (2) `organize_voice_files` had no VTYP for the folder name so it invented `TES4Male<folder>` — a directory no VTYP record declared and no speaker ever reads. Fix: `asset_convert/audio/voice_races.py` derives the identity from the plugin's own `RACE.txt`, keyed on FULL, and **both halves resolve through it** so they cannot disagree; `_create_vtyp_records` emits one VTYP per (voice key, gender) after the fixed Oblivion set (whose FormIDs must not move) and points every race EditorID sharing a FULL at it. `VTYP_EDID_BY_FID` records what was actually written — never reconstruct the reverse map from `CUSTOM_VTYP_EDIDS`, which relabelled the Hochelf voice type `TES4MaleHighElf` and sent 42 files to a dead folder. After: **0 unreachable voice files** (was 651), 116 VTYP records, 1252 NPCs on `TES4MaleAlemanne`, 334 on `TES4FemaleAlemanne`, 131 on `TES4MaleHochelf`. For Oblivion the derivation reproduces the old names exactly ("High Elf" → HighElf), so every race keeps the voice type it already had.
  - **A race with NO `FULL` must be SKIPPED, not fall back to its EditorID.** The folder on disk *is* the display name, so a race with no display name has no folder, and minting it an identity points it at a directory that cannot exist. Oblivion's `VampireRace` (`0x00000019`, no FULL) is the case that bites: `TES4_RACE_FID_TO_EDID` deliberately routes it to **Imperial**, which has recordings, and because plugin races bind *after* the fixed set, claiming it here silently overrode that and gave those actors an empty folder. Same for Nehrim's `VampireRace`/`VampireRaceX`/`UndeadRace` — none has a voice folder. Verified: with the skip, Oblivion derives exactly the 14 fixed-table races and every one of its 10 voice folders resolves unchanged.
  - **A dependent plugin must adopt the master's OWN-race VTYPs too, not just `CUSTOM_VTYP_EDIDS`.** `_create_vtyp_records` is gated on `if not ctx`, so for a plugin WITH masters adoption is the *only* path to a voice type — and the fixed table names only the VTYPs derived from Oblivion's race list. The master also wrote one per race of its own (Morroblivion's `mwBMDraugrRace`/`mwVivecRace`/…, Nehrim's `Alemanne`/`Hochelf`/…), and those were unreachable: **29 of Morroblivion's 36 resolvable actors** fell through to the `Imperial` default — the same silently-wrong routing this whole entry is about, just quieter, because Imperial *does* have recordings. `_adopt_master_special_records` now re-derives each master's races from its own `RACE.txt` (via `_master_export_dirs`, which resolves masters the way `load_master_export` does) and adopts those VTYPs as well: 27 → 79 adopted for the Morroblivion chargen ESP.
  - **Do not trust `MISSING_IN_VTYP` alone on a plugin with many ungated lines.** It read **2042** on the fixed Nehrim build and **45** on the same files once the metric was corrected: an INFO with no `GetIsVoiceType` gate was being demanded in *every* folder, so 405 ungated lines became ~5 misses each. The engine resolves an ungated line against the SPEAKER's own VTYP folder at runtime, which a folder-level audit cannot know — present in any folder is reachable. `voice_audit.py` now counts those once. The unambiguous metric is `temp/voice_reachable.py`-style: files sitting in a folder no VTYP record names.
- **Per-line speaker table — `tools/dialog/voice_line_table.py` (2026-07-17)**: the authoritative check the folder-level audit can't do — one row per (INFO, response, resolved speaker), resolving speakers the way the ENGINE does: positive subject GetIsID → that NPC's WRITTEN VTCK folder (`convert_NPC_` VTCK, injected GetIsVoiceType, and audio relocation all derive from the same `_npc_voice_map`, so drift between them = silent NPC; the table cross-checks them against the actual file), else the GetIsVoiceType chain at voice-type level, else every folder. Also validates file STRUCTURE (FUZE magic + lip size + RIFF xwm; bare-xwm and short-payload flags) — existence alone is not playability. Statuses: OK / OK_NOLIP / SUSPECT_SHORT / INVALID / MISSING(with reason: no_source_recording, source_is_broken_stub) / GATE_BLOCKED / NO_VTCK. State on 2026-07-17: 44,856 rows, 95.4% OK, 0 unexplained MISSING; GATE_BLOCKED rows are all intentional (SI NPCs blocked from unrecorded base rumors + cross-quest sibling-injection blocks, e.g. Azzan correctly blocked from Ocheeva's Dark08 briefing that shares the TES4 "contract" DIAL); NO_VTCK ≈ the Player base record. `--npc <edid>` prints one NPC's complete line set; `--csv`/`--status` for reports. Known residual quirk: conditionless lines in shared multi-quest topics still carry sibling GetIsID chains spanning OTHER quests' NPCs — masked by the GetQuestRunning + recorded-voice gates, but a per-QSTI-scoped donor set would be cleaner.
- **🔴 Voice prefix is EMPTY for master-owned topics in a dependent plugin (2026-08-05)** — cause of Morroblivion's Jiub intro being silent, and of 907 topics' worth of dead audio. `quest_edid_by_fid` was built from `by_type['QUST']` only — **this plugin's** quests. A dependent plugin routinely files topics under a quest that lives in a MASTER (907 of Morroblivion's 4,400 DIALs name an Oblivion.esm quest: `Generic 0x010602`, `bed 0x19360a`, …), and the engine builds the voice filename from that quest's EditorID exactly the same way. With no entry the prefix came out **empty**, so every one of those lines was written as `_<topic>_<fid8>_<n>.fuz` — a leading-underscore name the engine never asks for. 234 of the 389 converted voice files (60%) had it. Fix: fall back to `fid_to_edid` for any `Quest[0]` this plugin doesn't own. **`fid_to_edid` is keyed by the RAW source FormID** while `get_formid` has already added the load-order `offset` to the high byte, so the fallback must un-shift (`((high - offset) & 0xFF) << 24 | low24`) before looking up — a master quest is `0x00010602` in Oblivion's own export but `0x01010602` after remap. Diagnosis method that found it: `tools/dialog/say_topic_ab.py --explain` (added in the same pass) cleared every CTDA as passing, which ruled out conditions and left the filename; the voicemap then proved the importer was right and the file on disk was wrong.
  - **Corollary — importer changes invalidate the voice OUTPUT, not just the ESM.** The importer writes `<esm>.voicemap.txt`, but the files are renamed by the SOUND stage. Any fix that changes quest ownership, topic EDIDs, or VTYP assignment silently leaves the previous run's filenames in place until `--sounds-only` re-runs; `prune_stale_voice_files` only cleans folders that run actually writes into. Jiub's `.fuz` files were dated 2026-07-27 against a voicemap regenerated the same day as the fix — correct map, stale audio. **After any dialogue/voice change, run `--import-only` AND `--sounds-only`, then `tools/dialog/voice_audit.py`.**
- **Conversion stats**: 3817 DIAL topics (851 barks, 2966 conversation), 19278 INFOs, 954 DLBR branches, 1 DLVW view, 2908 quest-owned conversation topics, 27 fallback greetings
- **Dialog filtering stats**: 18,761 INFOs with conditions, 20 conditionless (down from 958 before voice type fallback fix). 17,784 INFOs with GetIsVoiceType. 3,704 GetInCell CTDAs (preserved for location gating). 3,169 DLBR branches (555 Type 1 chain topics excluded). 9,365 INFOs quest-gated with GetQuestRunning (non-SGE QSTI quests).
- **Quest running gating (QSTI restoration, 2026-07 design)**: In Oblivion, each INFO only shows while its OWN `QSTI.Quest` is running. Single-quest topics get this natively via quest ownership. For shared topics (owned by TES4DialogueGeneric), `_build_one_topic()` injects `GetQuestRunning(info's own QSTI.Quest)==1.0` as the FIRST CTDA on each INFO whose quest is non-SGE and ≠ the topic owner. **Gate by the INFO's OWN quest, never the DIAL's Quest[0]** — gating all of GREETING's children by one arbitrary Quest[0] blocks ALL greetings (a hard-won earlier lesson). SGE quests are exempt (running from new game via the .seq file).
- **AddTopic unlock system (2026-07)**: Oblivion's CENTRAL visibility mechanic — a topic only appears once ADDED via an INFO's Add-Topics data list (export: `AddTopic[i]=` FormIDs, 1044 INFOs), an `AddTopic X` result-script command, a quest-stage script, or automatically when a spoken line's text mentions the topic's FULL name (Oblivion highlights + auto-adds mentioned names). Skyrim has no AddTopic → re-expressed via `tes5_import/dialogue/unlocks.py`: one GLOB `TES4Unlock_<topic>` per gated topic (206); every INFO of a gated topic gets `GetGlobalValue(GLOB)==1` (func 74, same both games); every reveal event sets the global from a Papyrus fragment (INFO fragments fire OnEnd; reveal-only INFOs get a generated TIF fragment with just the SetValue call). The plan is built identically by the importer (GLOBs, conditions, VMAD property bindings) and script_convert/pipeline (fragment .psc bodies) — keys are low-24 FormIDs so it's load-order-offset independent. Gating rules (each violation caused a real in-game bug):
  - Gate ONLY topics explicitly added somewhere; mention-only topics stay ungated (name-match miss = dead content).
  - **Topics revealed by BARK lines (GREETING/HELLO) are NOT gated** — the bark fires on first contact, so in Oblivion they're effectively visible on first talk (Azzan's "Join the Fighters Guild" via his FG-ad greeting). Gating them makes topics go missing (fragment races the menu / a different greeting plays). 409 of 615 explicit targets are bark-revealed → 206 gated.
  - Gated TCLT targets keep the gate; their TCLT-parent INFOs are added as revealers.
  - Example that must stay gated: contract INFO (0003571C) lists AddTopic[0]=ratsTOPIC → TES4_TIF__0003571C sets TES4Unlock_ratsTOPIC OnEnd → "Rats" appears only after the contract line. Quest-running does NOT hide it — FGC01Rats starts at guild join (FGD00JoinFG stage 100 `StartQuest` → `.Start()` fragment).
- **'AnswerStatus' and 'TRANSITION'** are Oblivion NPC-to-NPC conversation system topics — classify as barks (IDLE/88/cat 7) or they leak into player topic menus.
- **Barter/Training services (2026-07)**: Skyrim opens the barter menu via a Papyrus fragment (`akSpeaker.ShowBarterMenu()` — there is NO Barter DIAL subtype, only BarterExit) and the training menu via `Game.ShowTrainingMenu(trainer)` (the Training subtype exists in the enum but even vanilla never uses it — zero `TRAI` SNAMs in Skyrim.esm). Vanilla contracts (decoded from Skyrim.esm): vendors = `OfferServicesTopic` (Custom, quest DialogueGeneric) with INFOs gated `GetInFaction(JobMerchantFaction)==1 + GetOffersServicesNow(func 255)==1` + TIF fragment calling ShowBarterMenu; trainers = `OffersTrainingTopic` (Custom, quest DialogueTrainers) with per-trainer INFOs gated `GetIsID + GetBaseActorValue(skill)<cap RunOn=Target` + TIF fragment, NPC in JobTrainerFaction + JobTrainer<Skill>Faction, and the menu's skill/cap read from the trainer's **CLAS** (DATA Teaches/MaxTrainingLevel). Our conversion (`dialog_converter.SERVICE_MENU_TOPICS`): the Oblivion Service-type topics `Barter` (57 voiced per-merchant lines) and `Training` (10 generic voiced lines) — previously dropped with all type-5 topics — convert to Custom player topics with synthesized prompts ("What have you got for sale?" / "I would like some training."); every INFO gets an injected service gate (barter: `GetInFaction` OR-chain over the synthesized vendor factions; training: `GetInFaction(TES4JobTrainerFaction)`) and a menu-opening fragment: script-less INFOs share the static scripts `TES4_ShowBarterMenu`/`TES4_ShowTrainingMenu` (in script_convert/static_scripts, auto-deployed + compiled with the generated scripts), while INFOs WITH result scripts (10 TG fence lines) get the menu call appended to their per-INFO TES4_TIF__ fragment by script_convert (same ParentDIAL classification on both sides). A synthetic text-only catch-all INFO ("Take a look." / "Let's begin.") is appended last so every vendor/trainer offers the topic even when no original line's conditions match. Service topics are EXCLUDED from identity/voice-gate inheritance and from the quest-NPC prescan (their 57 merchant GetIsIDs would pollute sibling-topic identity gating). Other Service topics (ServiceRefusal, BarterExit, Repair, Recharge, Travel, ...) stay skipped — no Skyrim mechanic fires them.
- **Trainer data source (2026-07)**: Oblivion stores trainer skill/cap per-NPC in **AIDT** (Teaches S8 @8, MaxTraining U8 @9), NOT the class — 92 of 114 vanilla trainers disagree with their CLAS values (classes are mostly 0/0; even the dedicated Trainer* classes have max=0). Skyrim reads them from the NPC's CLAS, so Phase 0c (`actors.create_trainer_records`) clones each trainer's class with Teaches/MaxTraining replaced from AIDT (deduped per class+skill+cap), points the trainer's CNAM at the clone, and adds the NPC to `TES4JobTrainerFaction`. Trainers of dead skills (Athletics/Acrobatics) or cap 0 are not converted. Vendor buying power: TES5 has no ACBS.BarterGold — a chest-less vendor trades from its own inventory, so barter gold becomes carried Gold001 in CNTO (kept OUT of the DOFT outfit item list).
- **Run-on-Target conditions are DEAD in Say()-driven topics (2026-07-19)**: Skyrim's `Actor.Say(Topic)` has no dialogue target, so a converted `RunOn=Target` CTDA evaluates against nothing and can never pass. Oblivion drives NPC-NPC/NPC-player scripted dialogue with `Say`/`SayTo`/`StartConversation`, and its INFOs routinely pick lines by the TARGET's race/sex/identity — CharacterGen's Valen Dreth taunts are all race-of-target gated, so the intro froze at stage 6 (no taunt → tauntCount never increments → stage 9/10 never set → the Emperor's escort never descends). Scope: 319 Say-driven topics; 1,924 of their 8,083 INFOs carry ≥1 run-on-target condition. Fix (`dialog_converter.build_say_topic_dispositions` + `convert_ctda(run_on_target_ref/drop_run_on_target)`): scan SCPT bodies + INFO/QUST result scripts for Say/SayTo/StartConversation call sites; a topic whose script target is UNIQUE gets its target-conditions retargeted to `RunOn=Reference` on that ref (player → 0x14 PlayerRef — also correct for menu dialogue, where the target IS the player); topics with mixed/unknown targets DROP target-conditions (call sites already select speaker+topic; auto-pass ≈ intent, never-pass = frozen quest). 156 topics retargeted, 163 drop. Regression: `TestSayTopicRetarget`.
  **Engine-fixed CTDA params must NEVER be load-order remapped (2026-07-22, second cause of the same symptom)**: `GetIsID(Player 0x00000007) [Target]` ("am I addressing the player?") appears on 3,761 INFOs — every stage-gated reveal greeting uses it. A condition evaluates against the RUNTIME actor, and the in-game player's base form is vanilla Skyrim's `0x00000007`, never our converted copy of the TES4 Player record — so remapping the param to `0x01000007` makes the condition unpassable, the reveal greeting never fires, its TIF fragment never sets the `TES4Unlock_*` global, and every unlock-gated topic vanishes (FGC01Rats: Arvena's stage-40 "Please, go find Pinarus, and those mountain lions!" is a revealer for `MountainLionsTOPIC`, which is gated `GetGlobalValue(TES4Unlock_MountainLionsTOPIC)==1`). `dialog_conditions._remap_formid` originally passed engine-fixed ids (index 0, object id < 0x100 — Bethesda hardcodes the same ids in every game) through unchanged; the override-conversion work unified it with `text_reader.remap_formid` (passthrough set = only PlayerRef 0x14) and silently regressed it. The two contracts are genuinely DIFFERENT: record FIELDS referencing the player NPC_ must keep shifting to the converted copy, but CONDITION params must stay engine-fixed. The passthrough now lives in `_remap_formid` itself with a regression test (`test_engine_fixed_param_never_remapped`). Diagnosis method: build the last-known-good commit in a worktree against the same export (skip navmesh via a hardlinked export dir without PGRD.txt), dump both ESMs with `tools/esm/tes5_esm_reader.py`, normalize the synthesized-FormID shift, and diff per-record — the regression was invisible on the affected topics' own records (byte-identical) and only showed on the greeting INFOs.
  **IDENTITY functions are EXEMPT from both (corrected 2026-07-21)** — GetIsID(72), GetIsRace(69), GetIsSex(70), GetIsClass(68), GetInFaction(71), GetFactionRank(73) ask WHO is being addressed, a question only the dialogue target can answer. Retargeting them onto a reference changes their meaning, and when that reference is the player it makes them UNPASSABLE: `GetIsID` compares the runtime actor's BASE form, and PlayerRef(0x14)'s base is vanilla Skyrim's 0x00000007 — never the converted TES4 player NPC_ 0x01000007. The blanket version rewrote 667 INFOs across 101 topics (almost all GREETINGs) into conditions that can never pass, so every affected NPC lost their entire topic list: the greeting that opens the list failed, leaving only quest-less topics (Pinarus Inventius kept 'rumors' but lost both his FGC01Rats mountain-lion topic and his unrelated Acrobatics training topic — two unrelated topics on one NPC is the signature of greeting-level damage, not a per-topic condition bug). Identity conditions fall back to RunOn=Target: faithful for menu dialogue, and for a Say()-driven line merely inert rather than inverted. `_NO_TARGET_RETARGET_FUNCS` in dialog_conditions.py.

  **...but "inert" still means SILENT, so inside a Say topic they are DROPPED (2026-08-07)**. The exemption above is about what an identity condition must never BECOME (RunOn=Reference — that is the 667-INFO regression, and it stays vetoed). It is NOT a reason to emit it as RunOn=Target in a Say-driven topic, where there is no dialogue target at all: such a condition can never pass, so the line never plays. Both Say dispositions now drop it — the `drop` case (mixed/unknown target) and the `ref` case (unique target), the latter because the resolved listener IS the NPC the call site addresses, so the authored identity check is statically satisfied. Found via the restored NPC-conversation head topics: their `GetIsID(<listener>)[Target]` survived as a dead RunOn=Target and the chain stayed silent even with the driver quest calling `Say()` correctly. Verify: no CTDA on a Say-topic INFO should carry `runon=1` with an identity function.
- **Say/SayTo returned the line duration in TES4 (2026-07-19)**: every polling conversation does `set timer to ref.Say topic` then counts down before the next line. Papyrus `Say()` returns nothing; substituting 0 machine-gunned lines each tick. `SAY_LINE_SECONDS = 3.0` (script_convert/converter.py) now stands in (+ any parsed `+delay`). Related: `GetSecondsPassed` now substitutes `_get_update_interval()` (was a hard 0.5 while sleep/timer scripts tick at 0.1 — every converted timer ran 5× fast).
- **MenuMode sleep idiom → RegisterForSleep (2026-07-19)**: Oblivion detects "player is sleeping" with a bare `begin MenuMode` + `isPCSleeping` (the only menu frames where it's 1 are sleep frames). Those blocks were preserved-as-comments (menu-id blocks like MQ01's lockpicking-menu stage-skips must stay dead), which silently killed 11 quests' sleep triggers: MG04 (stage 35→40 inn ambush — 'Arielle has no topics' report), Rufio's murder (Dark Brotherhood entry), vampirism onset, MS05 dreamworld, bed disease…. Now: bare-MenuMode-with-isPCSleeping bodies compile into `TES4_MenuModeSleepBody()` called from `OnSleepStart` AND `OnSleepStop` (two passes — MG04 needs one to record GameHour, one to arm its trigger) with a script-managed `TES4_PCSleeping` flag replacing isPCSleeping; `RegisterForSleep()` rides the OnInit/OnCellAttach lifecycle. Menu-id and non-sleep bare blocks stay commented. Regression: `TestMenuModeSleepConversion`.
- **Dialog emulator caveats**: tools/dialog/dialog_emulator.py does not model DLBR branch levels (Normal-branch choice-only topics still print) or GetGlobalValue (its func-74 mapping is wrong: 74=GetGlobalValue, GetFactionRank is 493 in TES5). It also only evaluates SGE quests at stage 0 — non-SGE quest dialogue (e.g. MG04Restore) never appears in its output at all. quest_walkthrough.py does not model Say-time condition evaluation (it passed CharacterGen while every taunt was dead) — a worthwhile future check: every Say-invoked topic needs ≥1 INFO passing with no dialogue target.
- **GetIsID injection (conversation topic NPC restriction)**: Oblivion uses `AddTopic` script command to control which NPCs show conversation topics. Skyrim has NO AddTopic — topics appear if quest is running and conditions match. Since all dialogue quests are StartGameEnabled, conversation topics without NPC-specific conditions appear on ALL NPCs. Fix: inject `GetIsID(npc_fid)==1.0` OR chains into INFOs lacking positive GetIsID, using sibling INFOs in the same topic as donor. Two-tier approach:
  1. **Topic-level**: `collect_topic_npc_fids()` gathers NPC FormIDs from positive `GetIsID(X)==1.0` conditions in sibling INFOs within the same DIAL topic. Injected into INFOs that lack positive GetIsID via `build_topic_npc_ctdas()`.
  2. **Quest-level fallback**: For ALL_CONDITIONLESS topics (every INFO in the topic lacks GetIsID), collect NPCs from ALL topics in the same quest. Handles cases like "Mother" topic in MS45 where the topic itself has no GetIsID but other MS45 topics identify the relevant NPC (Seed-Neeus).
  - OR chain pattern: `GetIsID(A) OR | GetIsID(B) OR | ... | GetIsID(N) AND` — injected BEFORE voice type and TES4-converted conditions.
  - Only conversation topics (non-bark) get injection. Bark topics use voice type conditions only.
  - 1,415 INFOs injected. Reduced wrong-NPC dialog from 3,775 to 208 across 100 NPCs (94.5%). Remaining 208 are 2 city-gossip topics using GetInCell (correct at runtime).
  - Key functions: `build_getisid_ctda()` (func 72), `build_topic_npc_ctdas()`, `info_has_positive_getisid()`, `collect_topic_npc_fids()` in `dialog_misc.py`
- **Dialog emulator**: `tools/dialog/dialog_emulator.py` — Simulates Skyrim dialog engine for validation. Modes: `--npc <edid>` (single NPC), `--batch --max-npcs N` (batch test), `--quest <edid>`, `--collisions`. Parses converted ESM, evaluates conditions, reports wrong-NPC matches.
- **VMAD object-property binding is TYPE-CHECKED by the VM (2026-07-11)**: a property binds only if the target form's class matches the declared Papyrus type — `Actor Property` bound to an NPC_ **base** record silently reads None in-game (sqv shows `<name>_var = None`), while Quest/Cell/MiscObject properties bound to QUST/CELL/MISC records bind fine. The sqv filled/None pattern is the diagnostic: it tells you exactly which property TYPES are wrong. Fixes: `_RECORD_TYPE_PAPYRUS` types NPC_/CREA as **ActorBase** (TES4 scripts only ever pass base EditorIDs as arguments — SetEssential/AddItem/PlaceAtMe); the SetEssential handler emits `base.SetEssential(v)` directly (ref-style `(x as Actor).GetActorBase()` only when the arg resolves to ACHR/ACRE/REFR). Same latent bug class: DOOR/CONT/STAT/FURN/FLOR base records typed `ObjectReference` will also bind None — not yet fixed. `GetDeadCount` still forces Actor on possibly-base args.
- **Script-typed properties require the script to actually be ATTACHED (2026-07-11)**: `TES4_FGQuestTrack Property FGInterimConversation` can only cast if quest 010474EC carries that script in its own VMAD. The conversion generated the .psc/.pex but never attached quest scripts (SCRI) to QUST records, and excluded NPC_/CREA from object-script attachment — so ALL such properties read None and TES4 quest GameMode logic never ran. Fixes: `build_quest_script_plan()` (tes5_import/base/object_scripts.py) resolves each QUST's SCRI to (script name, bound props) and `convert_QUST` splices it into the VMAD alongside the QF fragment script; NPC_/CREA are now in SCRIPTABLE_TYPES with the VMAD spliced after EDID in convert_NPC_/convert_CREA (scripts on a base instantiate per-reference, mirroring TES4 SCRI semantics).
- **QUST VMAD with scripts but no fragments (vanilla-verified 2026-07-11)**: the fragments section is ALWAYS present — version=2, count=0, and an **empty** file name (MS12PostQuest, WIThief01, BardSongs). 856/974 vanilla QUST VMADs strict-parse with the wbVMADFragmentedQUST layout used by build_vmad_quest_fragments.
- **🔴 A dependent plugin's BARK lines were nested under the MASTER's shared topic, bypassing the whole pipeline (2026-08-05)**: GREETING/HELLO are engine-named topics every plugin fills, so every one of Morroblivion's own greeting INFOs named the MASTER's `GREETING` DIAL as its parent. `overrides._attach_new_records` routes any new INFO under its `ParentDIAL` via `_NEW_NESTED_PARENT`, so all **2,727** of them were nested under Oblivion's single `GREETING_0102466E` and converted with a bare `convert_INFO(rec)` — **no voice gate, no quest gate, no unlock gate, no injected CTDAs at all** (0 of 2,727 had `GetIsVoiceType`, against 15,574 everywhere else). Worse, that master topic is owned by **Oblivion's Charactergen quest**, and DIAL.QNAM is a hard runtime gate, so every greeting in the game was gated on a quest that is not running — `tools/dialog/dialog_emulator.py` reported *"No greetings found! NPC will show fallback 'Hello.'"* for every one of the 3,607 actors. Vanilla splits GREETING into **271 per-quest topics** for exactly this reason (Skyrim honours ONE HELO topic per owning quest), and Morroblivion's 2,727 greetings span **384** distinct quests. Fix: `_is_bark_parent()` sends a new INFO whose master parent is a BARK topic back as `unattached`, so the normal builder emits this plugin's OWN per-quest bark topics fully gated; the parent DIAL is re-added to that batch (it was being dropped as an unchanged override, leaving the builder no topic to group under). After: `bark-topics=384` (was 0), `infos=19293` (was 16560), `voice-gated=18280`, ForceGreet bound 1, and Jiub reports **7 greetings** instead of 0. **Generalisable**: `_NEW_NESTED_PARENT` nesting is only correct when the master parent is a genuine container (a CELL). For a SHARED, engine-named parent it silently routes the plugin's content into the master's quest scope AND skips every gate the pipeline would have injected.
- **Morroblivion `Say()` plays no line — VERIFIED-NOT-THE-CAUSE list (2026-08-05, OPEN)**: `tools/script/script_debug.py` proved the call side is healthy — `STAGE 1` fires, quest `run=TRUE`, `JiubSpeak` steps 1→2→3, and each tick logs `SAY ... spk=mwJiubREF 3d=TRUE` — but **zero FRAG lines**, i.e. the engine selects no INFO. Everything below was checked against xEdit AND a real Skyrim.esm dump and is CONFORMANT; do not re-investigate without new evidence:
  - DIAL: `CUST`, category 0 / subtype 0, PNAM 50, TIFC 3 — byte-identical in shape to Oblivion's working `CharGenRenote`, and to all 6,503 vanilla CUST topics.
  - **DLBR `DNAM=0` (Normal, not Top-Level) is CORRECT for a script-driven topic** — vanilla's own `Say()`-driven `DialogueCarriageChatterTopic` branch is `DNAM=0`. The converter forces this deliberately (`is_linked=True`) so scripted topics stay out of the player menu. Not a bug.
  - CIS2 placement: must follow ITS OWN CTDA, not the whole chain (xEdit: CIS1/CIS2 are members of the condition struct). Ours follows the 629 it belongs to; Oblivion's follows its 630. Both correct — the differing *positions* are a red herring.
  - INFO subrecord set (VMAD/ENAM/CNAM/TRDT/NAM1/NAM2/NAM3/CTDA…), TRDT layout (24B, respNum 1, sound NULL), ENAM flags 0 (no say-once lockout), type-7 GRUP nesting under the DIAL, DLVW coverage, quest DNAM flags `0x0000` + type 8 (both used by vanilla), `.fuz` validity (FUZE + lip + XWMA), Jiub's ACHR enabled/persistent in the player's own start cell, all VMAD properties bound, and `GetVMQuestVariable`(629) — used 3,437 times in vanilla, so the function itself is sound.
  - Quest startability: `fbmwChargen` IS in `_startable_quests` (its own script runs `SetStage fbmwChargen 10/20`), so QNAM ownership is kept, not rerouted to the generic quest. SEQ is irrelevant — the quest is not start-game-enabled and TESGameSelect starts it explicitly.
  **The discriminator to chase**: Oblivion.esm and Nehrim.esm are ROOT masters (`Skyrim.esm` only) and their dialogue works; Morrowind_ob.esm is the ONLY plugin with a TES4 master (`Skyrim.esm`, `Oblivion.esm`) and the only one failing. Two dependent-plugin defects were already found and fixed this way (the empty VTYP map, the DIAL/INFO-only `own` dict) — enumerate the remaining `if not ctx:` blocks and the override paths for a third.
- **🔴 The override dialogue path passed a DIAL/INFO-ONLY dict, silently dropping quest-level conditions (2026-08-05)**: for a plugin with TES4 masters, `import_main` builds its own-dialogue call from `unattached_dial`, which contains **only DIAL and INFO**. But `build_dialog_groups` reads far more out of that dict, and every one of those reads no-ops on an empty list instead of failing:
  - **QUST — quest-level CTDAs.** Skyrim has no quest-level dialogue gate, so `quest_dialog_ctdas` must copy each quest's own conditions onto EVERY INFO it owns. `fbmwChargen` carries `GetIsPlayableRace` (CTDA index 254, no param) and Morroblivion converted with it **missing from all 16,560 INFOs** — 1 such condition in the output vs **12,858** in Oblivion.esm, whose CharacterGen lines all carry it. Census confirms it is injected, not authored: the TES4 export has only 14.
  - **SCPT/QUST/INFO — the Say/SayTo/StartConversation call sites** `build_say_topic_dispositions` needs (build log said `say-driven topics: 0`; it is 25 once the scripts are visible). That map also decides whether a Type-1 topic is dropped as NPC-to-NPC, so an empty map silently changes topic survival.
  - ACHR/ACRE/REFR — the refs those call sites name.
  Fix: fill `own[sig]` from `by_type` for QUST/SCPT/ACHR/ACRE/REFR/NPC_/CREA/RACE/FACT. Only DIAL/INFO drive record EMISSION (`dials = by_type.get('DIAL')`, `infos = ...`); every other signature is a read-only lookup, so this cannot duplicate records. After: `quest-cond-gated=15887`, `say-driven topics: 25`, fn=254 conditions 1 → 15,375. **Generalisable**: any `own`-dict built from an override diff is a partial view — check what the callee actually reads before passing it, because a missing signature degrades silently.
- **🔴 A dependent plugin must ADOPT the master's VTYPs or EVERY actor is mute (2026-08-05)**: `_create_vtyp_records` is inside the `if not ctx:` master-only block (correct — a dependent plugin must not duplicate its master's VTYP records), but it is also the ONLY caller of `set_voice_type`, which populates `skyrim_overrides.VOICE_TYPE_MAP`. So for any plugin WITH TES4 masters the map stayed empty, `build_npc_to_vtyp_map` returned `{}` — the build log says **`0 NPC->VTYP`** — and convert_NPC_/convert_CREA wrote **no VTCK on any of Morroblivion's 3,607 actors** (Oblivion.esm, a root master, has VTCK on 3,396/3,838). An actor with no voice type matches no dialogue line, so **every line in the plugin was silent**, which is what actually kept Jiub mute through the prison-ship intro. Fixed in `_adopt_master_special_records`: look each `CUSTOM_VTYP_EDIDS` entry up with `master_index.find_by_edid(b'VTYP', ...)` and re-register it via `set_voice_type` (all 27 resolve). After: `3453 NPC->VTYP`, `voice-gated=15574` INFOs, creature voices bound 80→387.
  - **The RACE records must come from the MASTER too.** VNAM voice-race routing lives on RACE, and all 3,607 Morroblivion actors use their master's races (its own RACE.txt has 12 custom ones). Without them the mapper falls back to the literal race and gives Jiub `TES4MaleDarkElf` — **a folder that does not exist**, because Oblivion's DarkElf VNAM routes BOTH genders to HighElf (`000191C1` → `00019204`) and the recordings live in `TES4MaleHighElf`. `build_npc_to_vtyp_map` is now fed the master's RACEs (this plugin's own applied second so they still win). Verified end-to-end: Jiub `VTCK=0118E93D` (TES4MaleHighElf), his three `.fuz` files are in that exact folder, and all three INFOs carry a matching `GetIsVoiceType(0118E93D)` gate.
  - `VOICE_TYPE_MAP` is module-global and convert.py imports several plugins in ONE process, so it is cleared per run alongside `_WELL_KNOWN_PROPERTIES`.
  - **Diagnostic**: `0 NPC->VTYP` in the build log means every actor in that plugin is mute. It is a one-line canary for total dialogue failure — check it before chasing individual silent lines.
- **🔴 A DECLARED-but-UNBOUND VMAD property aborts the WHOLE Papyrus function — the Morroblivion intro freeze (2026-08-05)**: a property the .psc declares but the VMAD binds nothing for is `None` at runtime, and the FIRST call on it aborts the entire function; everything after that line never runs. Morroblivion's chargen stage 1 opens with `tutorials.Stop()`, so `Cannot call Stop() on a None object` killed the fragment before `fbmwChargen.JiubSpeak = 1` five lines later — Jiub never spoke, and because stage 1 had already called `Game.DisablePlayerControls()`, the player sat frozen forever. The state machine had the SAME abort independently: `OnUpdate`'s `JiubSpeak == 1` branch calls `TES4ControlsDisabled.SetValue(1)` AFTER `DisablePlayerControls()` but BEFORE `Say()` and `RegisterForSingleUpdate`, so even a correct JiubSpeak would have disabled controls, said nothing, and never re-armed the poll. Two root causes, both in `import_main`:
  1. **`fid_to_edid` was built from `all_records` — this plugin's OWN export only.** An override plugin's SCROs point into its MASTERS as freely as its own (`Tutorials` and `Charactergen` exist only in Oblivion.esm), and a master-owned FormID with no EditorID is silently dropped by `_collect_scro_properties`. Fixed by walking `ctx.master_export` FIRST then `all_records` — the exact precedence (and comment) `CrossRefGraph` already used ten lines below; the two MUST agree or the importer resolves a different property set than the .psc was generated against. `remap_formid` already shifts master indices correctly, so `000C47C0` → `010C47C0` = the converted master's record.
  2. **A plugin WITH TES4 masters skips `_create_tes4_special_records`,** so its well-known registry was EMPTY while its scripts still declared `TES4ControlsDisabled`. The master's conversion already emitted those records, so `_adopt_master_special_records` now looks each up via `master_index.find_by_edid` (they have no TES4 source FormID, so the companion manifest cannot name them — find_by_edid exists for exactly this) instead of duplicating them.
  Also: `_WELL_KNOWN_PROPERTIES` is module-global and `convert.py` imports several plugins in ONE process, so it is now cleared at the top of `import_plugin` — otherwise a previous plugin's FormIDs get adopted by the next, pointing properties at records in the wrong file. **Diagnostic**: `Cannot call X() on a None object` naming a converted script is ALWAYS this class; diff the .psc's `Property` declarations against the record's VMAD property names. Still open from this sweep: `GREETING` (a Topic property the converter synthesizes for `startconversation`, with no SCRO to resolve) is unbound on stage 100.
- **The well-known-property registry is a LOOKUP TABLE, never a payload (2026-08-05)**: `_WELL_KNOWN_PROPERTIES` (import_main) maps name→FormID for records that exist only in the OUTPUT — `TES4Unlock_*` (1,770 on Morroblivion), `TES4Msg_*`, TES4Fame/Infamy/GoldFenced/ControlsDisabled/CyrodiilCrimeFaction — because `resolve_property_formid` reads the TES4 export and cannot see them. `convert_INFO` and `convert_QUST` used to `prop_vals.update(well_known_props)`, splicing all ~1,880 entries into EVERY scripted INFO/QUST VMAD. Measured on Morroblivion: 4,985 of 19,393 INFOs carried a ~70 KB VMAD, the chargen QUST 72,989 bytes, and the ESM was **633 MB → 203 MB after the fix** (a third of a gigabyte of properties no script declares). The engine logs each one as `Property <X> on script <Y> ... cannot be initialized because the script no longer contains that property`. Correct pattern (already used by `object_scripts.py`): iterate the properties the generated .psc actually DECLARES and look each up in the registry. INFO does this inside `_build_info_script_properties`; QUST via `_quest_well_known_refs`, which re-runs `ScriptConverter.convert_fragment` over the stage result scripts (it deliberately preserves `_property_refs` across calls, exactly as the QF_ generator accumulates them) and needs the real `xref` — with `xref=None` conversion aborts mid-line and the ref set is silently incomplete. The dedicated `reveal_props`/`timer_props`/`stage_reveals` paths still bind the specific unlock globals a fragment genuinely writes, so nothing is lost. Note a plugin WITH TES4 masters skips `_create_tes4_special_records` entirely, so its TES4ControlsDisabled etc. legitimately come from the master's conversion, not its own registry.
- **GLOB records are converted again (2026-07-11)**: scripts bind GlobalVariable properties to TES4 globals (TES4Fame, quest counters), which read None if the records don't exist. Only the engine-time globals (GameHour/GameDay/GameDaysPassed/GameMonth/GameYear/TimeScale) are dropped by convert_GLOB — script references to those are canonicalized to the vanilla forms.
- **Alias fill still unexplained (2026-07-11, OPEN)**: new-game test with byte-level vanilla-conformant QUST (verified against live Skyrim.esm records, not just xEdit defs): quest runs at stage 10, QF script instantiates (its vars appear in sqv), persistent refs resolve via prid from unloaded cells, vanilla DBSideContract03 fills on the same save — yet all four forced-ref aliases stay NONE, and stopquest/startquest does NOT fill them. A wrong-sounding (negative) sting plays at quest start instead of the quest-started sound. `tools/make_alias_test_esp.py` (removed 2026-08-25) built TestAlias.esp (4 SGE quests: minimal+vanilla target / minimal+Oblivion.esm target / FGC01Rats clone without VMAD / clone with VMAD) + seq file to factorize writer vs target vs structure vs VMAD in one in-game sqv sweep.
- **Alias-fill ROOT CAUSE (2026-07-11): QF quest-script property typed as an Actor-derived TES4_* script but VMAD-bound to an NPC_ BASE record is UNBINDABLE** → Papyrus aborts the quest script's whole init → the quest never finishes initialising → aliases never fill AND the QF stage fragments (SetObjectiveDisplayed) never run → objective has no live target → no compass/map marker. Confirmed by: a byte-identical clone of the QUST record (verified VMAD equal, 1333/1333 bytes) FILLS its aliases while the real quest doesn't; the clone's scripts don't attach (no sqv vars) but the real quest's do — script-attach correlates with fill-failure. `setstage <quest> 10` from a bare console (no connected quests) still fails, proving it's intrinsic to what's attached, not the start path. Vanilla rule: a ref-script-typed quest property is always bound to a REFERENCE whose base has the script (RikkeRef/GalmarRef/MercerFreyRef — 93 vanilla cases), NEVER to a base. Source of the bad typing: `SetEssential QuillWeave 0` (base semantics) — the converter's SetEssential handler, when the base NPC had an attached script, kept the property as the Actor-script type instead of ActorBase. FIX: a SetEssential arg that is a base (NPC_/CREA/unresolved) is ALWAYS typed **ActorBase** with a direct `target.SetEssential(v)` call; ActorBase wins over any reference/script type in the pipeline merge and can't be clobbered by a later SCRO preload (`_add_scro_ref` guard). Only genuine ACHR/ACRE/REFR args go through the `(x as Actor).GetActorBase()` cast. GENERALISABLE: any converted script property BOUND to a base record must be typed to a base Papyrus class; a reference type on a base = unbindable = silent whole-script-init abort. A blanket coercion in `get_property_refs` was tried and REVERTED — it broke 20 scripts whose bodies genuinely use the prop as Actor/ObjectReference (StartCombat, Enable/MoveTo, ==Actor); the correct fix for THOSE is to bind to a placed reference, not downgrade the type. Item bases legitimately carry their own object script (59 vanilla cases) — never coerce those.
- **Strict CK-compiler audit (2026-07-11)**: `tools/script/ck_compile_check.py` runs Skyrim's bundled `Papyrus Compiler/PapyrusCompiler.exe` (needs `-import` of `output/.../scripts/source` + `script_convert/static_scripts` + vanilla `Data/Source/Scripts` (has MiscObject/GlobalVariable/Package/Topic headers, NOT `Data/Scripts/Source`) + `-f=TESV_Papyrus_Flags.flg` by basename). It is MUCH stricter than the app's MIT compiler: of 138 scan-flagged suspects, the app compiled all clean but the CK rejected 87. Dominant CK error classes (separate workstream from alias fill): `no viable alternative at input 'Int'` (1965 — malformed local var decls), `::temp_var added to free list multiple times` (558), `GetStage/SetStage/EvaluatePackage is not a function` (146 — method called on wrongly-typed prop), `cannot cast tes4_Xscript to actor` (81 — ref-script vs Actor), script name >38 chars (72 — Papyrus name limit), `Disable/Enable is not a function` (31 — Actor-typed prop needs ObjectReference). FGC01Rats QF compiles clean under CK (validated both compilers). These CK-only failures = scripts that silently fail to load in-game; worth a dedicated pass.

## Known gaps and defects
<a id="known-gaps-defects"></a>

- **ACTUAL alias-fill / marker ROOT CAUSE (SOLVED 2026-07-11): top-level GROUP ORDER.** Our writer emitted the QUST top-group BEFORE CELL/WRLD/DIAL. The engine/CK loads top-level groups in file order and resolves a quest's forced-reference aliases (ALFR → ACHR/REFR) at the moment it loads the QUST group. With QUST first, the reference targets (which live in the later CELL/WRLD groups) are not in the form map yet, so EVERY forced ref fails: CK log `[QUESTS] Could not find forced ref (0103572C) for Ref Alias 'TES4Target03'.` (970 total, all our aliases). The alias then fills NONE and no marker draws. This is why the SAME alias resolved in a test ESP (target in Oblivion.esm = a MASTER, loaded first) but not in-file (QUST loaded before its own cells). Vanilla Skyrim.esm order is …CELL, WRLD, DIAL, **QUST** (QUST LAST). FIX: `_group_order()` in tes5_import/base/writer.py now places QUST after CELL/WRLD/DIAL. Diagnostic: dump top-level group order of the output vs Skyrim.esm and compare QUST's index to CELL/WRLD. General rule: mirror vanilla group order; any group resolved by a later system must precede its consumers. (Prior theories — VMAD trailing count, ActorBase property binding on QuillWeave, HEDR.numRecords undercount — were each real defects and fixed, but NONE was the marker cause; the user's tests falsified each.)
- **Secondary CK_WARNINGS worth a follow-up (NOT the marker cause)**: `[EDITOR] Editor ID 'X' is not unique … will be renamed` (129 LCTN, 68 BOOK, 42/29 SOUN, dup QUST/NPC_/FACT…) — duplicate EditorIDs across converted records; the CK auto-renames, which can break EDID→FormID lookups. `[CELLS] Ref is not in its persistence location 'TES4SkingradWestGateLocation'` (13) — a persistent ref whose cell XLCN location doesn't match its own persistence location. `[MASTERFILE] Missing base object for ref … Ref will be deleted` (~150 exterior refs) and `Could not find worldspace (FID) in load for Location` (512). These are independent quality issues surfaced by the CK's stricter load validation.
- **Quest objectives never completed (SOLVED 2026-07-12)**: symptom — walk a quest (e.g. FGC01Rats) and every objective you pass stays un-ticked in the journal; the log just accumulates open bullets. **Cause is a semantic mismatch, not a record bug.** Oblivion's journal is an append-only **log**: `SetStage 20` appends entry 20 under entry 10 and 10 remains as history — it was never a checkbox, so Oblivion has *no* "objective completed" concept and nothing in the TES4 data ever says "step N is done". Skyrim's journal is a **set of objectives**, each independently Displayed/Completed/Failed, where a Displayed-but-not-Completed objective renders as an open bullet with a live compass marker. The converter emits one QOBJ per stage-with-log-text and the stage fragment only ever called `SetObjectiveDisplayed(stage)` — `SetObjectiveCompleted` was emitted *only* when the TES4 log entry had the quest-complete flag — so every objective the player walked through stayed open forever. **The completion points must be recovered from the quest TARGETS**: every TES4 QSTA carries `GetStage` conditions saying exactly which stages that target's compass marker is live at, which is Oblivion's own encoding of "the player still has this errand to run" (FGC01Rats: Arvena live at 10/30/55/65/90 = the report-back steps; Pinarus at 40–50 = the hunt; Quill-Weave at 70–80/105 = the stakeout). An objective is in progress while its markers are live and is finished at the first later stage where they go dark → `_superseded_stages()` in script_convert/pipeline.py reuses `dialog_converter._target_live_at_stage` (same gate evaluator the importer uses to place QSTA) and returns, per fragment, the objectives that stage closes; each objective is closed exactly ONCE, by the first stage that ends it.
  - **Do NOT "complete every lower-numbered objective"** (the obvious-looking fix). Quests legitimately hold several objectives open at once and have side branches that a higher-numbered stage does not supersede — a blanket sweep force-ticks them. Corpus check over the 262 quests with fragments: **1030 fragments display an objective while closing nothing** (it stays open in parallel) and **149 fragments close >1 objective at once** (parallel threads converging), across **98 quests**. FGC01Rats stages 40 and 50 are both open together (Pinarus's marker spans 40–50) and close together at 55.
  - **TES4 has no fail bit.** QSDT flag values across all of Oblivion are only 0 and 1 (histogram: 2605×0, 378×1), and 0x01 means "complete the **QUEST**", not "complete this objective" — 89 of 390 quests have several such stages (FGC01Rats: 100 success, 110 success-variant, 200 failure, all flag 1). So a complete-flagged stage emits `CompleteAllObjectives()` + `CompleteQuest()`: the engine closes whatever is still displayed on whichever branch the player actually took, and leaves the never-shown entries of the skipped branch alone. A static per-index sweep can't know the branch; `CompleteAllObjectives`/`IsObjectiveDisplayed` are both real natives (verified in vanilla `Quest.psc`, and the generated calls compile clean against the Skyrim SE headers).
- **Say-driven scenes: the timer estimate is GONE (2026-08-16)**: every previous design (2026-07-25 park/release, per-owner thresholds, race-safe decrement, quest-scoped release, OnBegin re-charge) tried to reconstruct the length TES4's synchronous `Say` returned, and each had an edge where a line was cut, repeated, dropped or held. The rewrite gets the number from the engine: `set T to Say topic` → `T = TES4Polyfill.SayLine(speaker, topic, fallback)`, which blocks until the INFO's OnBegin fragment reports the selected line's measured length and returns it (+1s tail); every INFO carries a Begin/End fragment pair whose only fixed job is `LineBegan`/`LineEnded` on the speaker (state in script Actor Values, no owner analysis, no property binding); fragments never write a timer; the countdown is a plain `T = T - dt` again. Full contract, evidence and traps: [papyrus_conversion_notes.md — Say() timers](script_convert.md#polled-conversations).
- **Oblivion has an NPC-to-NPC conversation SCHEDULER; Skyrim has none (SOLVED 2026-08-07)**: symptom — CharacterGen reaches stage 26 with the assassins dead and everyone in position, then stalls forever; the guards never start talking, and `setstage charactergen 27` from the console resumes it normally. The stage-26 result script only calls `evp` — **nothing in the plugin starts the conversation**. Oblivion's engine picks two nearby idle NPCs, has the initiator speak a `HELLO` line whose conditions name BOTH actors (`GetIsID(speaker)` subject-side + `GetIsID(listener)` **Run-on-Target**), then walks the `TCLT` chain alternating speakers per `DATA.NextSpeaker` until a `GOODBYE`, running each INFO's result script — and the quest payload rides that last result (`setstage charactergen 27`). No record conversion substitutes for the scheduler: on `HELO` Skyrim only ever evaluates against the PLAYER, so the target-side identity can never pass. **`ACAC` (subtype 92) is NOT the destination — it is "ActorCollidewithActor", the bump bark** (xEdit `f4se_plugin_xEdit` subtype table); routing heads there was tried 2026-08-07 and is still silent in-game, do not retry. Fix: `tes5_import/dialogue/conversations.py` replays the **quest-advancing** chains through the proven `Actor.Say()` path — head INFOs reparented onto synthesized hidden `TES4NPCConv<plugin>Topic<N>` topics, driven by a generated start-game-enabled `TES4NPCConv<plugin>` quest that polls each chain's compiled gate plus the scheduler's own preconditions (both loaded/alive/not-fighting/within 500u). Line selection stays with the engine (per-INFO CTDAs), so End fragments fire normally. 15 chains restored in Oblivion.esm (CharacterGen, MQ04, MQ15, **MQ16 endgame ×3**, MQConversations/MQ13, MS91 ×3, TG01, TG03, SEConversations ×3), 2 skipped, 99 VMAD properties. Flavor chatter stays dropped (TODO.txt #16). Full design, selection rules and traps: [ambient_dialogue_channel_plan.md](tes5_import_dialogue.md#the-npc-to-npc-conversation-scheduler). Regression: `tests/test_dialog.py::TestNpcConversationChains`.


## Ambient dialogue channels: diagnosis and plan of attack
<a id="ambient-dialogue-channels-diagnosis-plan"></a>

**Symptom (reported 2026-07-25):** converted NPCs speak a random quip every few
seconds, unprompted. Vanilla Oblivion NPCs do not talk to themselves — they
greet the player on approach, and they occasionally hold conversations *with
each other*.

**Root cause:** Oblivion routes dialogue through three distinct delivery
channels that the converter collapses into two Skyrim ones. Two thirds of what
now plays as ambient chatter was never ambient in Oblivion, and 39% of the
player's dialogue menu is NPC-to-NPC chatter that was never player-selectable.

All numbers below are measured against the current build
(`output/Oblivion.esm/Oblivion.esm`, 2026-07-25) with
`tools/audit/ambient_bark_audit.py`.

---

## The three channels
<a id="three-channels"></a>

Oblivion's engine dialogue-type table (recovered by
`tools/disasm/oblivion_engine_extract.py` into `tes4_export/oblivion_engine_tables.json`)
lists GREETING and HELLO as **separate channels**, indices 0 and 6. NPC-to-NPC
conversation is a third mechanism — not an engine channel but the DIAL
**category** `Type=1` (Conversation), driven by Oblivion's actor-pairing AI.

| # | Oblivion source | Fires when | Currently converts to | Skyrim then fires it |
|---|---|---|---|---|
| 1 | `GREETING` — DIAL type 0, engine ch. 0 | Player **activates** the NPC; the dialogue menu opens | `HELO` | **Ambient, every 5 s** ❌ |
| 2 | `HELLO` — DIAL type 1, engine ch. 6 | Ambient, on player approach | `HELO` | Ambient, every 5 s ✅ |
| 3 | **Conversation** — DIAL type 1 (`*NQDResponses`, `*RumorResponses`, …) | **NPC-to-NPC only**; never reachable by the player | `CUST` | **Player menu topic** ❌ |

Skyrim by contrast has only two unprompted channels — `HELO` (ambient greeting,
re-armed by `fAIGreetingTimer`) and `IDLE` (idle chatter,
`fIdleChatterCommentTimer`) — plus `SCEN` for multi-actor scenes. There is **no
on-activate greeting channel**: in Skyrim the first thing said on activation is
a topic, not a bark.

### Why the cadence is 5 seconds

`fAIGreetingTimer = 5.0` and `fAIMinGreetingDistance` come from Skyrim.esm.
The converter correctly puts `GMST` in `SKIP_TYPES`
(`tes5_import/base/constants.py`), and the output contains **0 GMST records**, so
Skyrim's own timers govern. The cadence is therefore *not* a settings bug —
it is correct Skyrim behaviour applied to a pool of lines that should never
have been on that channel. Do not attempt to fix this by writing GMSTs.

---

## Problem 1 — GREETING lines are on the ambient channel
<a id="problem-1-greeting-lines-are"></a>

`tes5_import/dialogue/converter.py:859-860` maps both channels to one subtype:

```python
'GREETING':       (73, b'HELO', 7),
'HELLO':          (73, b'HELO', 7),
```

Measured result:

```
HELO: topics=293  INFOs=5636
  GREETING   3743   66.4%     <- should not be ambient
  HELLO      1893   33.6%     <- correct
```

**3,743 INFOs** that in Oblivion played only after the player clicked an NPC now
fire on a 5-second ambient timer.

The conditions do not save it. The gates are identity/quest-state predicates
(`GetIsID` 6143 uses, `GetStage` 3217, `GetIsVoiceType` 7291), not
"should I speak unprompted" predicates — so once an NPC matches at the right
stage the line stays permanently eligible and re-fires every tick.

Density is inflated too. Vanilla's Hello pool is deliberately shallow and
broadly shared (one `DialogueRiftenHellos` topic of 315 short lines covers a
whole city); ours is 293 quest-scoped topics of narrative-length lines:

| | Converted | Vanilla |
|---|---|---|
| HELO INFOs | 5,636 | 5,287 |
| Median CTDAs per INFO | 4 | 2 |
| INFOs with ≤2 CTDAs | 165 | 3,570 (68%) |
| Response length (median / max) | 62 / 149 chars | short generic |

Sample of what currently plays as a walk-past bark:

> *"So, you've actually challenged the Gray Prince? Do you really know what
> you've gotten yourself into?"*

That is a conversation opener, not a bark.

### ⚠ A previous fix moved the wrong channel

Memory (`project_hello_channel_split`, 2026-07-19) records a fix setting
`_EDID_SUBTYPE['HELLO'] = (88, b'IDLE', 7)`. **That mapping is not in the
current code** — `git log -S` finds no trace of it, and both entries read
`HELO` today. It was reverted or never landed.

It also moved the wrong channel: HELLO *is* genuinely Oblivion's ambient
approach line and belongs on an ambient Skyrim channel. GREETING is the one
that does not belong there at all. Do not re-apply that mapping.

**Load-hang corollary (still binding):** that attempt hung the game before the
main menu because it carried **TCLT links onto IDLE-subtype INFOs**. Vanilla
Skyrim has TCLT on HELO barks but on **zero** of its 576 IDLE INFOs; the
engine's topic-link init cannot take links from IDLE. Any reroute must census
whether vanilla ever pairs a subrecord with the destination subtype before
emitting it.

---

## Problem 2 — NPC-to-NPC conversation is in the player's menu
<a id="problem-2-npc-npc-conversation"></a>

Oblivion `Type=1` Conversation topics are the lines NPCs trade with *each
other*. 534 such topics (4,260 INFOs, after skips) convert to `CUST` —
player-selectable menu topics — and **all 423 purely-conversation topics carry a
`BNAM` branch**, so they genuinely render in the menu.

Because these topics never had a player-facing prompt, `FULL` fell back to the
EditorID. The player literally sees menu entries reading:

```
SEMiscQuestResponses
SkingradNQDResponses
ICAllNQDQuestionResponses
FGD02Insults
DASheogorathSpeech
SE
```

Vanilla's structural contrast is unambiguous:

```
VANILLA:    SCEN topics = 7426, with BNAM = 0      <- never selectable
            CUST topics = 6503, with BNAM = 6503   <- always selectable
CONVERTED:  SCEN topics = 0
```

Skyrim puts all NPC-to-NPC dialogue on the `SCEN` subtype with **zero**
branches; that absence of a branch is exactly what makes it non-selectable.
We emit **no SCEN records at all**, so these lines had nowhere to go but the
menu.

`INFOGENERAL` is the one correct case: it maps to `Rumors` (subtype `RUMO`),
which Skyrim does have as a real player topic, and is deliberately special-cased.
It must stay as-is.

### How much source structure survives

Oblivion gives us real pairing signal to work with:

```
Conversation-type INFOs: 7772
  NextSpeaker: Target 7590 / Either 131 / Self 51
  with Choice(TCLT) chaining: 4007

core NPC-to-NPC (excl. HELLO/GOODBYE/INFOGENERAL): 2881
  with Choice(TCLT) chaining: 1753 (61%)
```

So 61% of the core response lines already carry the thread structure a scene
needs. What Oblivion does **not** provide is *which two actors* hold the
conversation — Oblivion picks the pair at runtime from proximity + AI packages.
Vanilla Skyrim scenes are authored against **quest alias actors** (2–7 `ALID`
per scene across 1,706 SCEN records). That gap is the reason a faithful port is
expensive, and it is a genuine design decision, not a mechanical translation.

**Resolution: drop these lines rather than convert them wrong** (Step 1). There
is no cheap correct destination — a topic in the player menu is wrong, and a
solo ambient bark is the very "NPCs talking to themselves" defect being fixed.
Absence is the only honest intermediate state. The content is recorded in
TODO.txt #16.

**Update 2026-08-07:** the *quest-advancing* subset of these conversations is
no longer absent — 15 chains are replayed by a generated driver quest. See
[The NPC-to-NPC conversation scheduler](#the-npc-to-npc-conversation-scheduler).
The flavor families below remain dropped.

---

## Plan of attack — ordered by ease of fixing
<a id="plan-attack-ordered-ease-fixing"></a>

### Step 1 — Drop the NPC-to-NPC topics entirely ✅ IMPLEMENTED 2026-07-25

**Decision (2026-07-25): better absent than wrong.** These lines have no correct
destination in Skyrim short of full SCEN synthesis (Step 4), and every partial
destination is worse than silence. So they are **skipped at conversion**, not
emitted-but-hidden.

**Change:** add Oblivion `Type=1` Conversation topics to the skip path in
`should_skip_dial` (`tes5_import/dialogue/converter.py`), excluding the four that
are genuinely something else:

- `INFOGENERAL` → `Rumors`/`RUMO` — a real Skyrim player topic; already correct.
- `HELLO` → `HELO` — the ambient channel; correct (see Problem 1).
- `GOODBYE` → `GBYE` — a real Skyrim subtype.
- The already-skipped emotion-response families (`Question`, `AnswerNegative`, …)
  stay skipped by the existing `_SKIP_EDIDS` list.

That leaves the ~423 pure NPC-to-NPC response families to drop.

**Effect:** removes 423 garbage entries (`SEMiscQuestResponses`, `FGD02Insults`,
`SE`, …) from every NPC's dialogue menu. Most player-visible defect, cheapest fix.

**Cost:** small and localized — one predicate in the skip path.

**Risk:** low, with one thing to check: dropping a DIAL must not leave dangling
`TCLT` choices pointing at it. The converter already handles exactly this via
`_strip_dead_tclt(infos, skipped_fids)`, which runs over `should_skip_dial`
output — so routing the drop through that same path gets the cleanup for free.
This is the reason to skip properly rather than to suppress `BNAM`.

**Do not lose the content.** Recorded as TODO.txt "Later Issues" #16 so the
dropped families can be restored via Step 4.

#### ⚠ The trap that nearly broke CharacterGen
<a id="script-driven-type-1-topics"></a>

A drop keyed on `DATA.Type == 1` alone is **wrong**. Measured against the real
export, **293 of the 535 Type-1 topics are script-driven** — spoken by an
explicit `Say`/`SayTo`/`StartConversation` in a quest script, which converts to
a real Skyrim `Actor.Say()` and works correctly. That set includes:

- every CharGen topic (`CharGenMain`, `CharGenVoice`, `CharGenBaurus`,
  `CharGenEmperor`, `CharGenGlenroy`, `CharGenBlades`, `CharGenRenote`,
  `CharGenTaunt2`) — the Emperor/Blades tutorial intro;
- the Daedric-prince speeches (`DASheogorathSpeech`, `DANamiraSpeech`,
  `DAClavicusSpeech`), the arena/announcer topics (`ICAnnouncer`, `Announcer`),
  `FGD02Insults`, `OblivionGateConv`, the SE and Thieves-Guild scripted scenes.

Dropping by type alone would have deleted all of them and broken the tutorial
outright. The predicate therefore consults `_SAY_TOPIC_DISPOSITIONS` (the
converter's existing Say/SayTo/StartConversation scanner) and keeps anything a
script speaks. Note this detector finds **295** Type-1 topics where a naive
`\bsay\b` regex finds only 12 — it also handles `StartConversation` and
`ref.Say Topic` forms.

**Ordering requirement:** `build_say_topic_dispositions` must run *before* the
first `should_skip_dial` call. It is now built in the `build_dialog_groups`
pre-scan. `dialog_unlocks.build_unlock_plan` also calls `should_skip_dial`, and
runs much earlier (`import_main.py:332` vs `:801`), so the predicate **fails
safe**: an empty map keeps every topic rather than dropping all 293.

#### The script-driven topics still needed unlisting

Keeping them is necessary but not sufficient. They were still emitted with a
**top-level `BNAM` branch** and an EditorID `FULL`, so `CharGenVoice`,
`Dark18TraitorTalk`, `SE11SheogorathFarewell2` … sat in the player's menu —
the same defect, on a different set of topics. Fix: force a **Normal
(non-top-level) branch** for script-driven Type-1 topics. `Actor.Say()` reaches
an INFO through its topic regardless of branch visibility and TCLT links still
resolve, so the scripted lines play exactly as before while the topic leaves the
menu. `INFOGENERAL` is exempt — it is genuinely player-selectable.

#### Measured result

```
Type-1 named-keep    :   3 topics  4891 INFOs   (INFOGENERAL/HELLO/GOODBYE)
Type-1 script-driven : 293 topics  1266 INFOs   <- KEPT, unlisted
Type-1 droppable     : 242 topics  1157 INFOs   <- dropped

converted DIAL 3461 -> 3330
NPC-to-NPC conversation topics dropped: 258
Type-1-sourced CUST topics: TOP-LEVEL(menu)=1 (INFOGENERAL), NORMAL(hidden)=292
dangling TCLT in output: 0
```

(258 > 242 because the count includes topics whose INFOs were already skipped
for other reasons.)

**Verify:** `tools/audit/ambient_bark_audit.py --by-source`; regression tests in
`tests/test_import.py::TestNpcToNpcConversationDrop` (5 tests, incl. the
fail-safe and the CharGen keep).

---

### Step 2 — Take GREETING off the ambient channel

**Change:** route `GREETING`-sourced INFOs to a player-activated topic instead
of `HELO`. `HELLO` stays on `HELO` (it is correct).

**Effect:** removes ~3,743 INFOs (66%) from the 5-second ambient timer,
leaving the 1,893 genuinely-ambient HELLO lines. This is the direct fix for
the reported symptom.

**Cost:** moderate. The destination needs deciding (see below) and ~3,743
records re-parent, which moves group structure.

**Risk:** moderate, with a known trap — re-read the load-hang corollary above
before choosing a destination subtype, and census vanilla for every
subrecord/subtype pairing emitted.

**Open decision — where GREETING lands.** Two candidates:

- **(a) A top-level `CUST` topic entered on activation.** Faithful to
  Oblivion's "opening line when you click them", and Skyrim's natural shape.
  Costs a branch + prompt per quest, and risks cluttering the menu if the
  prompt is wrong — Problem 2 is precisely what that failure looks like, so
  prompts must come from real `FULL` text, never an EditorID fallback.
- **(b) Drop the ones that duplicate a HELLO line, keep the rest as HELO.**
  Much cheaper, no structural change, but discards Oblivion's on-activate
  greeting entirely and leaves NPCs opening dialogue silently.

Recommendation: **(a)**, because it preserves content, and (b)'s silence was
already an accepted-but-disliked trade-off in the reverted 2026-07-19 attempt.

---

### Step 3 — Thin the ambient pool to vanilla density (optional, do after 2)

**Change:** after Step 2, re-measure. If 1,893 HELLO lines across quest-scoped
topics still feel too frequent, consolidate toward vanilla's shape — fewer,
broader topics of short lines, rather than many narrow quest-scoped ones.

**Effect:** brings perceived cadence in line with vanilla even where the channel
routing is now correct.

**Cost:** low-to-moderate, and purely additive tuning.

**Risk:** low. Defer until Steps 1–2 are in-game — the cadence may already be
acceptable once two thirds of the pool is gone, which would make this
unnecessary.

---

### Step 4 — Restore NPC-to-NPC conversations (partially done 2026-08-07)

Step 1 drops these lines; this step is how they come back. Still tracked as
**TODO.txt "Later Issues" #16** for the *flavor* families. The
**quest-advancing** subset is now restored — not via SCEN, but by a generated
driver quest. See
[**The NPC-to-NPC conversation scheduler**](#the-npc-to-npc-conversation-scheduler)
below, which is the implemented design and supersedes the SCEN sketch that
used to sit here.

**Still deferred (the flavor families):** synthesize `SCEN` records for the
~240 dropped `*NQDResponses` / `*RumorResponses` chatter families, using the
surviving `TCLT` chains (1,753 of 2,881 core INFOs) as phase structure. Cost is
high — vanilla scenes bind 2–7 quest **alias** actors each (1,706 SCEN records)
whereas Oblivion picks the pair at runtime from proximity + AI packages, so
this needs invented actor pairing plus aliases, phases/actions and per-scene
conditions. Every partial version is worse than absence — which is exactly how
these lines came to be labelled `SE` and `FGD02Insults` in the player's menu.
Do not ship a half-version of it.

The driver below does **not** generalize to these: it works precisely because
quest-advancing chains name both actors with `GetIsID`, which the flavor
families do not.

---

## Script-started conversation chains
<a id="script-started-conversation-chains"></a>

**Code:** `tes5_import/dialogue/conversations.py:build_script_chain_map`,
`script_convert/commands.py:start_conversation`

The scheduler section below covers chains the ENGINE starts. A second family is
started explicitly by a script: `StartConversation <target> <topic>`. Oblivion
treats the two identically once begun — both hand the whole TCLT chain to the
scheduler, which alternates speakers per `DATA.NextSpeaker` and runs each
INFO's result.

`Actor.Say(topic)` plays exactly ONE line, so converting the call to a bare Say
restores the first line and drops the rest. A chain whose lines gate on a
counter the results advance then stalls on line 0 forever, and any caller
polling that counter re-speaks it every tick.

Measured in Oblivion.esm: Savlian Matius' Kvatch chapel scene (`MS48Convo`,
quest MS48 stage 90) is 10 lines gated `MS48.conv == 0..8`, each result setting
the next value. `SavlianMatiusScript` polls `if MS48.conv == 0` at 0.25s and
called `StartConversation TierraRef MS48Convo`; Tierra's own script speaks only
at `conv == 8`. Converted as a single Say, nothing ever spoke lines 1-7, `conv`
stayed 0, and Savlian repeated "Report, soldier." indefinitely (live readback:
`TES4_MS48Script::Conv_var = 0` at stage 90).

Fix: `build_script_chain_map` linearizes the chain behind every multi-line
topic, and `start_conversation` emits the same replay the driver quest uses —
`Utility.Wait(TES4Polyfill.SayLine(actor, topic, len) + beat)` per hop,
alternating self/target. The call site names BOTH actors, so unlike the
scheduler's chains no identity CTDA is needed to resolve speakers. Line
selection stays with the engine (per-INFO CTDAs), so End fragments — the
`set MS48.conv to N` payloads — fire exactly as before.

Single-line topics (the Daedric-prince speeches, announcer barks) produce no
chain entry and keep the plain `Say`.

**Detection is by CTDA shape, never by name.** A chain is a topic whose INFOs
gate on a consecutive run of ONE quest variable AND whose every counted line
names a non-player listener through a run-on-target `GetIsID` — the scheduler's
own signature. The identity test is what excludes `GREETING`: it also carries a
counter run in places, and without the test 87 of Oblivion's 162
`StartConversation` sites would have looped the player's greeting.

Measured on Oblivion.esm: **18 topics** qualify, changing **12 of 16,519**
generated scripts — MS48 (Kvatch), MQ12 Jauffre/Martin, DANocturnal
(WeebamNa), four Dark Brotherhood scenes, SE06, MS40, Erthor, RallusOdiil and
SulinusVassinus. No CharacterGen script changes and `TES4NPCConv<plugin>.psc`
is byte-identical, because those chains are HELLO-headed and belong to the
scheduler path below, not this one.

---

## The NPC-to-NPC conversation scheduler
<a id="npc-npc-conversation-scheduler"></a>

**Oblivion has an ambient conversation SCHEDULER that Skyrim does not have at
all, and no record conversion can substitute for it.** This is the mechanism
behind the dropped families, and it is also why several main-quest chains
stalled.

### How Oblivion runs one

When two NPCs idle near each other, the engine may start a conversation
between them:

1. It picks the pair from proximity + AI packages.
2. The initiator speaks a **`HELLO`** line whose conditions name BOTH actors —
   `GetIsID(<speaker>)` on the subject side and `GetIsID(<listener>)` with the
   **Run-on-Target** bit on the target side. The target-side identity is what
   makes the line mean "say this *to that specific NPC*".
3. The engine then walks the line's **`Choice`/`TCLT`** links, alternating
   speakers per each INFO's `DATA.NextSpeaker`, until a `GOODBYE` ends it.
4. Each INFO's result script runs as it plays — which is where the quest
   payload lives.

Quest authors lean on this. **CharacterGen stage 26→27 IS such a chain:**

```
Baurus  HELLO   0004EA44  "Are you all right, sire? We're clear, for now."
        conditions: GetIsID(Baurus) + GetIsID(UrielSeptim)[Target]
                    + GetStage(CharacterGen)==26
   -> TCLT
Emperor CharGenVoice 0004EA45  "Captain Renault?"
   -> TCLT
Baurus  GOODBYE 0005144A  "She's dead. I'm sorry, sire, but we have to keep
                           moving."          result: setstage charactergen 27
```

The stage-26 result script itself only calls `evp` — **nothing in the plugin
starts this conversation.** The scheduler does. So on Skyrim the three lines
were never spoken, stage 27 never fired, and the intro stopped dead with
everyone standing in position. (`setstage 27` from the console resumed it
normally, which is what isolated the chain as the only broken link.)

### Why the obvious destinations don't work

| Tried | Why it fails |
|---|---|
| Leave the head on `HELO` | Skyrim evaluates HELO **only when greeting the PLAYER**, so `GetIsID(<other NPC>)[Target]` can never pass. Silent. |
| Route the head to `ACAC` (subtype 92) | ❌ **Wrong — do not retry.** `ACAC` is **"ActorCollidewithActor"**, the bump-into-someone bark. It is not a conversation channel. Tried 2026-08-07; still silent in-game. Source: the engine subtype table in `references/xEdit/Tools/xSE/f4se_plugin_xEdit/f4se_plugin_xEdit-20180628.txt` — `108;"ActorCollidewithActor";108;"ACAC";7;0;0`. **Its presence in Skyrim.esm (3 topics) was mistaken for evidence it was the ambient-conversation channel; that inference was wrong.** |
| Full `SCEN` synthesis | Correct in principle but needs actor pairing Oblivion never records — see the deferred Step 4 above. |

### What is implemented: `tes5_import/dialogue/conversations.py`

These chains are **identity-pinned** — the head names both actors — so the
proven `Actor.Say()` machinery (which already drives every scripted CharGen
conversation) can replay them. Two pieces, built from ONE shared plan:

* **The head INFO is reparented** onto a synthesized hidden topic
  `TES4NPCConv<plugin>Topic<N>` (a Type-1 CUST topic registered say-driven, so
  `Say()` reaches it and the player menu never shows it — the same shape
  `CharGenVoice` already has). Source-space FormIDs come from
  `_CONV_FAKE_FID_BASE = 0x00F40000`, asserted collision-free against the real
  DIALs.
* **A generated start-game-enabled quest** `TES4NPCConv<plugin>` (in the `.seq`)
  polls on a 4 s `RegisterForSingleUpdate` loop and, when a chain's gate
  passes, `Say()`s the sequence with measured waits.

The poll reproduces the scheduler's own preconditions:

```papyrus
Bool Function CanConverse(Actor akA, Actor akB)
    ; both loaded, both alive, neither fighting, within 500 units
```

**Line selection stays with the engine.** The plan fixes only the TOPIC
sequence, each hop's speaker, and the expected line length; which INFO plays
within a topic is decided by that INFO's own converted conditions — exactly
Oblivion's rule. So per-line CTDA fidelity is preserved and the INFO End
fragments (the `setstage` payloads) fire as they do for any other `Say()`.

### The selection rules (and why each exists)

Restored only when ALL hold:

| Rule | Why |
|---|---|
| Head is a `HELLO` INFO whose **every** target-side `GetIsID` names a non-player actor | That is the scheduler's signature. |
| The identity is **positive** (`== 1`, operator `==`) | `GetIsID(x)[Target] == 0` is an *exclusion* on a player greeting, not an address. |
| The chain's TCLT closure carries a quest-advancing result (`setstage`/`startquest`/`stopquest`/`set X.y to`) | Pure flavor stays dropped per "better absent than wrong". |
| Exactly one subject identity, and both actors have a **unique** placed ref | The driver needs concrete `Actor` properties. |
| Every non-identity head condition compiles to a poll gate | See below. |

Supported gates: `GetStage` (58), `GetQuestVariable` (79 → the converted quest
script's property), `GetItemCount` (47 → `Conv<i>A.GetItemCount`). **Anything
else SKIPS the chain** rather than firing it on a looser trigger — a
conversation firing EARLY is worse than one that stays absent. Skips are
logged, not silent.

### Traps this hit, all still live

* **`GOODBYE` hops are bark-grouped.** The bark pass emits one topic per
  (owning quest, subtype), so a chain's GOODBYE line does NOT live at the
  source `GOODBYE` DIAL's FormID. `_build_bark_pass` publishes
  `ctx['bark_topic_fids'][(quest, subtype)]` and the driver binds through that.
* **Hops that ride `HELLO` itself** (MS91 Weebam-Na → Mazoga) must point at the
  *other chain's* synthesized head topic, since the raw HELLO DIAL doesn't
  survive as one record. Chains whose HELLO hop has no restored head are
  dropped.
* **Chain indices name Papyrus properties**, so after any filtering pass the
  chains are **renumbered gapless** — both pipelines must agree on the numbering
  or every property binds to the wrong thing.
* **Overlapping chains are mutually exclusive.** Two heads can open the same
  authored talk (MS91: Weebam-Na's "You want to speak to me?" and Mazoga's
  "You are Weebam-Na?" both walk the MazogaTalk lines). Whichever fires sets
  the other's `_done` flag, or the whole conversation replays.
* **Member topics must be un-dropped.** A chain routing through a Type-1 topic
  the NPC-to-NPC drop would remove registers it say-driven so it survives.

### The mirroring contract

`build_conversation_plan()` is **shared analysis**, in the same family as
`message_menus` and `dialog_unlocks`: `tes5_import` builds the head topics and
the quest VMAD from it, `script_convert.pipeline` generates the matching
`.psc` from it. **Any divergence leaves VMAD properties unbound** — the
generated script guards every property against `None` and disables that chain
rather than aborting the whole poll function (see
`project_unbound_vmad_property_aborts`), but it cannot repair the binding.
Both sides gate on masterless plugins only.

Verify the contract holds after any change:

```bash
## psc-declared Conv* properties must equal the VMAD-bound set, exactly
<a id="psc-declared-conv-properties-must"></a>
python temp/verify_conv_vmad.py     # 99/99, dangling: 0
```

### Measured result (Oblivion.esm, 2026-08-07)

```
NPC-to-NPC conversation topics dropped: 243 (TODO.txt #16)
quest-advancing chains restored: 15
NPC conversations: 15 chains on TES4NPCConvOblivion (2 skipped), 99 bound properties
```

By OWNING quest (note this is the QSTI owner, not the quest a chain's gate
tests — MQConversations holds the MQ13 Narina/Martin exchange, whose gate is
`GetStage(MQ13)==20`):

```
Charactergen 1   MQ04 1   MQ15 1   MQ16 3   MQConversations 1
MS91 3           SEConversations 3         TG01BestThief 1   TG03Elven 1
```

All were stalled the same way, not just the reported one — **MQ16's three are
the endgame Ocato/messenger conversations**. The 2 skips are heads whose
speaker cannot be statically resolved (0 or 2 subject identities).

### Known compromise

`_done` latches are script state, so an overlapping *flavor* pair (the SE
spouse chats) plays once per save rather than recurring. The quest-critical
chains all self-terminate via their own `setstage`, so this costs nothing
there.

---

## Verification
<a id="verification"></a>

- `python tools/audit/ambient_bark_audit.py output/Oblivion.esm/Oblivion.esm --by-source export/Oblivion.esm`
  — per-subtype ambient INFO counts attributed to the originating Oblivion channel.
- `--compare "<SSE>/Data/Skyrim.esm"` — side-by-side against vanilla density.
- `--topics HELO` — largest ambient topics, with conditionless counts.

Target end state after Steps 1–2:

| Metric | Now | Target |
|---|---|---|
| HELO INFOs from GREETING | 3,743 (66%) | 0 |
| HELO INFOs total | 5,636 | ~1,893 |
| Conversation-sourced topics in the player MENU | 423 | 1 (`INFOGENERAL`/Rumors) ✅ |
| Pure NPC-to-NPC topics emitted | 242 | 0 (dropped) ✅ |
| Script-driven Type-1 topics kept | 293 | 293, unlisted ✅ |
| Dangling TCLT after the drop | — | 0 ✅ |
| SCEN records | 0 | 0 (flavor families still deferred) |
| Quest-advancing chains restored | 0 | 15, via the driver quest ✅ |

Ground truth is in-game: the fix is confirmed when NPCs stop quipping
unprompted and their menus no longer list EditorIDs.

For the conversation driver specifically, the in-game check is that
CharacterGen advances 26→27 on its own (Baurus and the Emperor speak the
three-line exchange while the player stands in the ambush room) — and the
equivalent beats in MQ16, MS91 and TG03.


## What Oblivion dialogue does not transfer to Skyrim, and what to do about it
<a id="what-oblivion-dialogue-does-not"></a>

Every number below comes from running the two emulators over real data —
`tools/dialog/oblivion_dialog_emulator.py` against `export/Oblivion.esm` and
`tools/dialog/dialog_emulator.py` against the converted ESM — with the condition tables
read out of both game executables (see
[dialogue_engine_contracts.md](../reference/dialogue_engine_contracts.md)). Counts are for
vanilla Oblivion.esm: 3,817 DIALs, 19,278 INFOs, 2,482 NPCs.

The two engines agree on more than expected. Condition functions use the same
opcode numbering in both games (`GetIsID` is 0x1048 either side), and of the
370 condition functions Oblivion defines, only **78 share an index with a
different Skyrim function** — of which only **2 actually occur in vanilla
Oblivion data**, and both are harmless renames. The real problems are not the
conditions. They are the four mechanics Skyrim simply does not have.

## 1. Disposition — 1,451 INFOs — *translate, do not drop*
<a id="1-disposition-1451-infos-translate"></a>

**Skyrim has a disposition system.** It is not called disposition and it is not
a 0–100 scale, which is why searching the engine for the word finds nothing.
An NPC's friendliness is a **Relationship Rank from −4 to +4** — Archnemesis,
Enemy, Foe, Rival, Acquaintance, Friend, Confidant, Ally, Lover — default 0,
read by condition function **419 `GetRelationshipRank`** and settable from
Papyrus through the native `Actor.SetRelationshipRank`
([UESP](https://en.uesp.net/wiki/Skyrim:Disposition)).

Oblivion uses `GetDisposition` on 1,751 conditions across 1,451 INFOs, tiering
dialogue overwhelmingly at 30 and 70:

| Threshold | Conditions |
|---|---|
| 30 | 646 |
| 70 | 564 |
| 40 | 146 |
| 50 | 128 |
| 20 | 107 |
| 60 | 80 |

**Implemented: the 0–100 disposition maps onto the −4..+4 rank**, in
`dialog_conditions.disposition_to_rank`. A full Oblivion.esm conversion emits
1,724 `GetRelationshipRank` conditions and leaks zero `FastTravel`:

| Oblivion disposition | Skyrim rank |
|---|---|
| 0–19 | −2 Foe |
| 20–39 | −1 Rival |
| 40–60 | 0 Acquaintance *(both games' default)* |
| 61–79 | 1 Friend |
| 80–100 | 2 Confidant |

Ally (3) and Lover (4) are left unused: Oblivion has no equivalent relationship,
and reserving them keeps quest-granted ranks meaningful.

This preserves the **ordering** of the tiers, which is the part that matters. A
line Oblivion gated behind high disposition stays gated more tightly than a
neutral one, so a stranger does not get intimate-friend greetings. The previous
behaviour — dropping the condition — made all three tiers unconditional and
fired them at the same NPC at once.

Two details the translation must get right, both covered by
`test_disposition_becomes_relationship_rank`:

* The condition must be **rewritten, not passed through**. CTDA index 76 is
  `FastTravel` in Skyrim, so an untouched condition invokes an unrelated
  function.
* `GetRelationshipRank` compares against an actor, which in dialogue is always
  the player — base form `0x00000007`, the engine-fixed id, never our converted
  copy of the TES4 Player record.

A `Use Global` disposition comparison is still dropped: it names a GLOB holding
a 0–100 value that cannot be rescaled at conversion time, and comparing a rank
against it would be meaningless.

Note that vanilla Skyrim itself never uses function 419 in a dialogue condition
— it drives relationship rank from Papyrus and gates dialogue on
`IsInFriendStatewithPlayer` / `HasParentRelationship` instead. Using 419
directly is therefore unusual but well-formed; the function, its parameter type
(Actor) and its Papyrus counterpart are all present in the engine.

The tiers now behave, where before conversion they collapsed. Pinarus
Inventius's greeting pool by relationship rank, from
`tools/dialog/dialog_emulator.py --relationship-rank`:

| Rank | Greetings |
|---|---|
| −2 Foe | 23 |
| 0 Acquaintance | 49 |
| 2 Confidant | 50 |

Previously all 98 fired at every rank. Oblivion's own numbers for the same NPC
(`--disposition 10 / 50 / 90`) show the same shape — a markedly smaller hostile
pool widening as regard improves.

## 2. AddTopic — 586 gated topics
<a id="2-addtopic-586-gated-topics"></a>
<a id="addtopic-unlock-globals"></a>

**A script `AddTopic` is the THIRD reveal route**, after an INFO fragment and a
quest-stage fragment, and it is load-bearing rather than cosmetic:
`TGReadWantedPoster` and `TG00MysteriousNoteScript` are how the player first
learns of the Gray Fox. `script_convert.commands.add_topic` emits the same
`SetValue(1)` on the topic's `TES4Unlock_<topic>` global; an UNGATED topic is
already visible, so it compiles to a no-op note.

**The gap.** In Oblivion a conversation topic is invisible until something adds
it: an INFO's `AddTopic` list, an `AddTopic X` result script, or a quest stage.
Skyrim has no equivalent — a topic shows whenever its conditions pass. Of 3,183
conversation topics, **586 are AddTopic-gated** and 2,597 are available from the
start.

**Recommendation: keep the existing global-per-topic translation. It works.**
The converter creates one `TES4Unlock_<topic>` global per gated topic and adds
`GetGlobalValue(...) == 1` to every INFO of that topic, set from a Papyrus
fragment when a revealing line plays. The two emulators independently confirm
this is faithful: Oblivion reports "Mountain Lions" as *hidden, awaiting
AddTopic* for Pinarus at FGC01Rats stage 40, and Skyrim reports the same topic
blocked solely by `GetGlobalValue(TES4Unlock_MountainLionsTOPIC)`. Same gate,
same state, expressed two ways.

The one thing to watch is over-gating. A topic mentioned by name in a bark is
auto-added by Oblivion, and treating that as a reveal previously stripped gates
from 162 topics — see the note in
[dialogue_conversion_notes.md](tes5_import_dialogue.md).

## 3. Persuasion — 39 DIALs, 130 INFOs
<a id="3-persuasion-39-dials-130"></a>

**The gap.** The whole persuasion minigame is gone. Oblivion's 39 persuasion
topics are the wheel's outcomes — `ADMIRE_HATE`, `ADMIRE_LOVE`, `COERCE_*`,
`BOAST_*`, `JOKE_*`, `BRIBE`, `DEMAND*` — each a response to a wheel wedge
played at a disposition tier. Skyrim replaced all of it with Speech-checked
individual lines and persuade/bribe/intimidate *branches* on specific quests.

**Recommendation: skip entirely.** These 130 INFOs have no target to convert
into: there is no wheel to trigger them, no disposition to tier them, and the
Skyrim subtypes named `Intimidate`/`Bribe`/`Flatter` are per-quest favour
dialogue, not a general minigame. Converting them produces topics that can never
fire. This is the one family where dropping is clearly right — and it is cheap,
being 0.7% of INFOs.

## 4. Reply-only topics — 1,955 topics
<a id="4-reply-only-topics-1955"></a>

**The gap.** Oblivion reaches follow-up lines through an INFO's `Choice` list;
the target is a topic that must never sit in the menu on its own. Skyrim
expresses the same idea as a DLBR branch flag: Top-Level (DNAM bit 0) means "in
the menu", clear means "reachable only by following a link".

**Recommendation: keep mapping Choice targets to Normal branches, and only
promote when the revealer is itself gated.** 1,955 topics are Choice targets.
This was a real bug: the Skyrim emulator originally listed every branch as a
menu topic and showed Pinarus offering 12 permanent topics — `SadGeneral`,
`AngerReceive`, `AnswerPositive` and the rest of the emotional-response family —
when he offers exactly one, "I would like some training." The emulator now
checks the Top-Level flag and nests Choice targets under the line that offers
them.

## 5. Smaller gaps, with verdicts
<a id="5-smaller-gaps-with-verdicts"></a>

| Oblivion mechanic | Size | Recommendation |
|---|---|---|
| `GetQuestVariable` / `GetScriptVariable` | 4,429 conditions | **Translate, do not drop.** The legacy VM is gone but the live equivalents `GetVMQuestVariable` (629) / `GetVMScriptVariable` (630) exist; the property name travels in a CIS2 subrecord. Dropping these kills most quest package gating. |
| `GetCrimeGold` | 48 conditions | **Already remapped to 459 — keep it.** Index 116 is `IsIntimidatedbyPlayer` in Skyrim, so leaving it alone would call the wrong function. `_FUNC_REMAP` handles this correctly. |
| `IsRidingHorse` (327), `IsPlayersLastRiddenHorse` (339) | 25 conditions | **Pass through.** Renamed to `IsRidingMount` / `IsPlayersLastRiddenMount` at the same index; semantics unchanged. |
| Combat / Detection / Service barks | 945 INFOs | **Convert; the subtypes exist.** Both engines carry the same families (Attack, Hit, Flee, Yield, Noticed/Seen/Unseen/Lost, Barter/Repair/Training). |
| `Say Once` (1,162), `Goodbye` (1,908) | flags | **Convert.** Both flags exist in Skyrim's INFO response flags at the same bit positions (0 Goodbye, 2 Say Once). |
| Fixed-FormID channels | 4 | **Handle by FormID, not type.** `GREETING` (0x0000C8) is `DATA.Type=0`, the same value ordinary topics use, so it can only be told apart by its hardcoded id. Classifying by type alone turns the greeting channel into a menu topic. |
| Result scripts | 5,694 INFOs | **Convert to Papyrus fragments.** Already done; this is where AddTopic reveals and stage advances live, so failures here silently break gating. |

## How to check any of this yourself
<a id="how-check-any-this-yourself"></a>

    # what Oblivion gives an NPC
    python tools/dialog/oblivion_dialog_emulator.py export/Oblivion.esm \
        --npc PinarusInventius --stage FGC01Rats:40

    # what the conversion gives the same NPC
    python tools/dialog/dialog_emulator.py output/Oblivion.esm/Oblivion.esm \
        --npc PinarusInventius --stage FGC01Rats:40

    # the engines' own condition tables, side by side
    python tools/disasm/oblivion_engine_extract.py --functions GetDisposition
    python tools/disasm/dialog_engine_extract.py --functions FastTravel

A topic present on one side and absent on the other is a conversion bug unless
it falls into one of the five categories above.


## Speak-as lines: what works and what was reverted
<a id="speak-as-lines-what-works"></a>

Measured in-game. Two alternatives were built and both killed the audio.

## Speak-as lines: `Say()` on a voiced stand-in, with the in-head flag (2026-08-19)
<a id="speak-as-lines-say-voiced"></a>

TES4's `Say <topic> <force-subtitles> <speak-as NPC> <in-head>` speaks a line
THROUGH a marker/shrine/door AS some NPC. Skyrim's `Say` has no speak-as
argument and keys voice lookup on the speaker, and a bare XMarker STAT has no
voice type -- so the engine finds no voice folder and plays nothing.

**What works, measured in-game:** mint a TACT carrying that NPC's converted
VTYP, place it at the emitter's authored position, and call a plain
`Say(topic, None, abInHead)` on it. `abSpeakInPlayersHead` is Skyrim's own
third parameter and is the faithful conversion of TES4's fourth argument --
the voice comes from inside the player's head at full volume, which is how
Oblivion delivered the Arena announcer, the Daedric princes and Mankar
Camoran. See `tes5_import/dialogue/speak_as.py`, `TES4Polyfill.SayScene`.

🛑 **Do not get clever with the delivery.** Two alternatives were built and
both KILLED THE AUDIO outright, which is strictly worse than any subtitle
defect:

* **A one-action SCEN per call site.** Scenes do tick a non-actor's line
  (`BGSSceneActionDialogue`), so the reasoning looked sound -- delivered
  through a scene, the lines produced no audio at all.
* **`Activate()` on the talking activator.** This *is* vanilla's idiom
  (`DA08WhisperingDoorScript`, `DA05QuestingBeastGhostScript`, DA10's
  `TalkingMace.GetRef().Activate(...)` -- and no vanilla script calls `Say()`
  on a TACT). But vanilla activates a TACT the PLAYER has walked up to, which
  is not what a polled announcer line is; converted call sites got no audio.

🛑 **Never emulate the in-head flag with `MoveTo(player)`.** That was an
invention: it teleports the marker out of its authored position permanently
(nothing moves it back) and costs the line its audio. Vanilla's one
repositioning case (DA05, following a ghost's head) uses `SetPosition`.

**Known open defect:** a scripted `Say()` on a NON-ACTOR does not retire its
subtitle -- measured live, a direct `Say` on the announcer's TACT played its
audio and left the subtitle onscreen indefinitely. The engine's countdown /
`SubtitleManager::KillSubtitles` path is `TESObjectREFR` vtable slot 0x40
(rva 0x2d9d80, SkyrimSE 1.6.1170), which nothing drives for a plain
reference. **No verified fix exists**; the two attempts above cost the audio
and were reverted. Audio is the higher-value behaviour, so the stuck subtitle
stands until a fix is demonstrated in-game rather than reasoned about.

### Speak-as INFOs silently lost their quest-inherited conditions (fixed 2026-08-25)

A speak-as INFO whose owning QUST carries conditions runs its inherited
condition block through `_drop_non_actor_speaker_ctdas` (dialog_converter),
which strips subject-run actor-identity CTDAs -- the speaker is a TACT, not an
actor, so `GetIsID`-family conditions can never pass.

That function walks the PACKED buffer, so its `sig` is a 4-byte `bytes` slice,
but `pack_subrecord` takes a `str` and calls `sig.encode('ascii')`. Every call
that kept at least one condition therefore raised
`AttributeError: 'bytes' object has no attribute 'encode'`.

The failure was invisible because `_convert_topic_infos`' per-INFO handler is a
blanket `except Exception` that prints `ERROR info under <topic>` and moves on
-- so the INFO was **dropped from the output entirely**.
Fix: `pack_subrecord(sig.decode('ascii'), data)`.

**Blast radius, measured 2026-08-25** by instrumenting the call site and running
both plugins through `--import-only`. An INFO is lost only when the drop leaves
at least one condition standing; when it strips them ALL, the function returns
`b''` at its `if not kept` early exit and never reaches the broken line.

* **Morrowind_ob.esm: 35 INFOs across 7 Daedric-prince topics** --
  mwDAMolagBalSpeech 8, mwDAMalacathSpeech 6, mwDAAzuraSpeech 6,
  mwDAMehrunesDagonSpeech 5, mwDASheogorathSpeech 4, mwDABoethiahSpeech 4,
  mwDAMephalaSpeech 2. Every one had `KEPT38` (one surviving condition).
* **Oblivion.esm: ZERO.** 390 speak-as INFOs reach the call site and 320 carry
  quest conditions, but all 320 measured `KEPT0` -- Oblivion's speak-as quests
  condition exclusively on funcs in `_NON_ACTOR_SPEAKER_DROP` (`{72, 254}`), so
  the block always empties and the bug is a near-miss there. This includes
  ArenaAnnouncer/Announcer (30), ICAnnouncer (34), DASheogorathSpeech (38) and
  the 13 other Daedric shrines.

🛑 **Do not estimate this statically from the export.** A first pass replaying
the drop against the QUST's RAW TES4 conditions predicted 353 lost INFOs in
Oblivion.esm; the real input is the CONVERTED block, whose TES4->TES5 function
remap changes which funcs fall in the drop set. Instrument the call site.

**Lesson:** that handler converts any bug in the INFO path into silent data
loss. When a topic reports `ERROR info under ...`, the INFOs under it are gone,
not degraded -- get the traceback before theorising.

### How a speaker activator is built
<a id="speaker-activator-construction"></a>

**Code:** `tes5_import/dialogue/speak_as.py` — one module, scan through build.

- **Scan then build.** `scan_speak_as_calls` finds the `Say` call sites,
  `build_speaker_activators` mints a TACT + placed REFR for each. These were two
  modules (`talking_activators` / `speaker_activators`); they are one feature and
  the scan half had exactly one consumer.
- **The in-head flag collapses.** A site appearing both with and without the
  flag is recorded as in-head: the flag is per CALL and the converter passes it
  through, so the record set only needs the `(emitter, voice, topic)` triple.
- **The REFR is a CLONE of the emitter's own**, with `NAME` repointed at the new
  TACT. That carries cell, position and rotation across without re-deriving any
  of it; `_CLONE_DROP_PREFIXES` strips what must not be inherited (teleports,
  ownership, locks, enable-parents, scale, counts). Flag 1024 is Persistent.
- **An actor with no voice type is skipped.** There is no folder to read voice
  files from, so the speaker would be as silent as the marker it replaces.
- **Order matters:** it appends REFRs to `by_type`, so it must run BEFORE the
  CELL/WRLD builders place them — like the leveled-actor shells.

## <a id="adopting-a-masters-synthesized-records"></a>Adopting a master's synthesized records

**Code:** `tes5_import/import_main.py` (`_adopt_master_special_records`),
`tes5_import/base/owned_records.py`

The conversion synthesizes records TES4 has no source for — globals, factions,
menus, formlists, and one VTYP per voiced race. A **root master** creates them;
a **dependent plugin** must adopt the master's FormIDs rather than create its
own, or its VMAD properties point at records in the wrong file.

### The voice-type case, which is the loud one

`create_vtyp_records` is master-only, and it is what calls `set_voice_type`. A
dependent plugin therefore left `VOICE_TYPE_MAP` EMPTY,
`build_npc_to_vtyp_map` returned `{}` — "0 NPC->VTYP" in the build log — and
`convert_NPC_`/`convert_CREA` wrote **no VTCK on any of Morroblivion's 3,607
actors**.

An actor with no voice type matches no dialogue, so EVERY line in the plugin
was silent, including Jiub's: the prison-ship intro played out mutely. For
contrast, Oblivion.esm as a root master has VTCK on 3,396 of 3,838 actors.

The master already wrote these VTYPs, so the fix is to adopt its FormIDs.

### Why the registry is cleared per plugin

`WELL_KNOWN_PROPERTIES` is a module global, and `convert.py` imports several
plugins in ONE process. Without the reset, a previous plugin's synthesized
FormIDs would be adopted by the next one. Each run repopulates it — created for
a root master, adopted from the master's output for a dependent plugin.

## <a id="script-addtopic-and-the-unlock-globals"></a>Script AddTopic and the unlock globals

**Code:** `tes5_import/import_main.py` (the `ScriptConverter.topic_unlock_globals`
handoff)

A script `AddTopic X` is the third reveal route for a topic, alongside INFO
fragments and quest stages, and `script_convert` emits the same
`TES4Unlock_<topic>.SetValue(1)` for it.

Two things must hold or that property binds to nothing:

- **The map must be shared.** The converter pass import_main runs — object and
  quest VMAD property resolution — must see the SAME topic→global map the
  script pipeline used. Otherwise it resolves a different property set than the
  generated `.psc` declares.
- **The global must be resolvable by name.** `WELL_KNOWN_PROPERTIES` does that
  for synthesized records with no TES4 counterpart, following the
  `TES4Fame`/`TES4GoldFenced` pattern.

## <a id="the-synthesized-record-id-window"></a>The synthesized-record id window

Writer ids start well above the highest source FormID so synthesized companion
records cannot collide with converted ones: real records stop at or below
`max_formid`, companions start at `+0x1000`, and the chargen menu MESGs take a
fixed window in the middle of the reserved gap, ahead of the null-LAND repair.

## <a id="the-conversion-owned-globals"></a>The conversion-owned globals

**Code:** `tes5_import/base/owned_records.py`

Oblivion state that Skyrim exposes no way to read. Each is a GlobalVariable the
converted scripts read and write, registered in `WELL_KNOWN_PROPERTIES` so VMAD
builders can bind it by name.

| Global | Type | Replaces |
|---|---|---|
| `TES4Fame` | float | `GetPCFame` |
| `TES4Infamy` | float | `GetPCInfamy` |
| `TES4GoldFenced` | float | `GetAmountSoldStolen` / `ModAmountSoldStolen` |
| `TES4ControlsDisabled` | short | `GetPlayerControlsDisabled` |

**`TES4GoldFenced`** tracks the GOLD VALUE of stolen goods fenced — the Thieves
Guild INFOs literally print "Amount fenced: %.0f gold". Skyrim keeps the
counter only as a condition function: there is no Papyrus native and no
matching entry in the stat table, so converted scripts back it with this
global. Reusing the vanilla "Items Stolen" stat would be wrong twice over — it
counts items rather than gold, and the engine bumps it on every theft, so the
TG rank gates would trip without ever visiting a fence.

**`TES4ControlsDisabled`** has no Papyrus native either (checked vanilla
`Game.psc` and SKSE — only the Disable/Enable writers exist). Flattening the
read to 0 is not neutral: it makes `== 1` permanently false AND `== 0`
permanently true, so a script polling the state gets both halves of its own
sequencing wrong. The converter owns both writers, so the state is shadowed:
`Game.DisablePlayerControls()` and `Game.EnablePlayerControls()` each also
write this global, and the read returns it.

### TES4CyrodiilCrimeFaction

Stands in for Oblivion's single global crime faction, and receives most
converted `GetPCExpelled` / `GotoJail` / crime calls, so it must be a REAL
crime faction: DATA sets **Can Be Owner (bit 15) + Track Crime (bit 6)**, and
CRVA carries the vanilla crime values (murder 1000, assault 40, trespass 5,
pickpocket 25, steal x1.0, escape 100) that all 14 real Skyrim crime factions
share.

**REVERTED:** it previously set CanBeOwner plus bits 7-11/13/16, which are
Skyrim's *Ignore* Crimes flags — the opposite of the intent; xEdit decodes the
old `0x0001AF80` as "IgnoreKills". It also never set Track Crime, without which
the engine accumulates no crime gold at all, and its CRVA used the wrong struct
layout, leaving every crime worth 0. See `convert_FACT` in
`record_types/actors.py` for the layout and the vanilla-census amounts.

## <a id="synthesized-menus-factions-and-formlists"></a>Synthesized menus, factions and formlists

**Code:** `tes5_import/base/owned_records.py`

### Chargen menus need FIXED ids

`create_chargen_menu_records` allocates from a reserved window rather than
`derive_formid()`: these ids are a contiguous, order-significant page/button
block, which a hash cannot give. `writer.chargen_fid_base` sits mid-way into
the gap `import_plugin` opens above the plugin's highest real FormID — clear of
real records below, and of the null-LAND repair working down from the gap's
top. The window is reserved with `reserve_source_ids()` so no derived id can
hash onto it.

The plan comes from `message_menus.build_chargen_menus`, the SAME function the
script pipeline runs, so the Message properties in the emitted `.psc` bind to
exactly these records and the page/button order matches the converter's
`Show()` chain arithmetic.

**Authored FO3/FNV menus** (`ShowMessage <MESG>`) enter the same plan under
the MESG's own EDID with text None; `create_message_menu_records` writes
nothing for them, since `convert_MESG` converts the record itself
([script_convert.md](script_convert.md#fnv-showmessage-menus)).

**Choice-persistence globals** sit at fixed slots ABOVE the page window
(`base+0x40`, `+0x41`) so a changed page count can never move them. Menu
emission writes (picked index + 1) there; the dialogue-condition conversion
reads it back for `GetIsPlayerBirthsign` / `GetPCIsClass` (see
`dialog_conditions.set_chargen_choice`). This is what makes the Emperor's
post-birthsign line match the sign the player actually picked.

### The force-combat faction pair

`TES4 StartCombat` forces a fight regardless of aggression, disposition or
faction state; Skyrim's combat AI drops a target it has no hostile reaction to.
So the conversion owns a faction pair with a mutual Enemy reaction, and
`TES4Polyfill.ForceCombat` puts the two actors on opposite sides.

XNAM is (Faction, Modifier 0, Group Combat Reaction 1 = Enemy); DATA bit 0 is
Hidden From PC. **REVERTED:** relationship rank -4 was tried as the hostile
reaction and did not work.

### The GetDestroyed formlist

TES4 keeps a per-reference "destroyed" flag: `SetDestroyed` writes it,
`getdestroyed` reads it, and the engine's `CloseCurrentOblivionGate` sets it on
the gate it closes. Skyrim kept only the SETTER — `ObjectReference.psc` has no
matching getter — so the conversion backs the read with a FormList that
converted scripts add to and test membership in.

### Message menus

One MESG per button-`MessageBox` call site. The layout matches vanilla
script-shown boxes (`dunMiddenNamesMenuMSG`): DESC is the message text, INAM is
a null required leftover, DNAM bit 0 marks Message Box (a modal with buttons,
not a corner notification), one ITXT per button, and no conditions. `Show()`
returns the clicked ITXT's index — the same number the TES4 `GetButtonPressed`
poll compared against.

### <a id="ambient-dialogue-pacing"></a>Ambient-dialogue pacing

Oblivion has no per-package chatter control: its package flags concern doors,
speed, sneak, equipment and combat, nothing about dialogue (xEdit
wbPackageFlags; UESP "Oblivion Mod:Mod File Format/PACK"). Pacing lives
entirely in game settings, and Skyrim runs far faster -- greeting retry 5s vs
Oblivion's 20s, idle chatter 10s vs Oblivion's authored 100s.

Because GMST is in SKIP_TYPES none of that carried over, so converted NPCs ran
Skyrim's clock over Oblivion's much larger line pool: the constant-quipping
defect. The TES4 export's own value wins where the record exists, since that is
what Oblivion.esm shipped; otherwise the Oblivion.exe engine default recorded in
AMBIENT_GMST_OVERRIDES is used.

### Force-combat faction ids are fixed

base+0x42 and +0x43 in the reserved gap. Allocating them would shift every
later id and corrupt saves. Relationship rank -4 was tried as the hostile
reaction first and silently no-ops between non-unique actors (the CharacterGen
final assassin); runtime AddToFaction into a record-side Enemy pair is the
vanilla hostility idiom and works for ANY actors.

### <a id="voice-types-are-created-from-scratch"></a>Voice types are created from scratch

`create_vtyp_records` never references Skyrim.esm VTYPs. Voice files live in
`Sound/Voice/<plugin>/<EditorID>/` and must match the EditorIDs created here
(`TES4Male*`, `TES4Female*`). DNAM bit 0 is AllowDefaultDialogue and bit 1 is
Female, so male voices write DNAM=1 and female DNAM=3.

Two sources, in order:

1. **The fixed Oblivion set** (`CUSTOM_VTYP_EDIDS`), emitted first so its
   FormIDs never move, and so the `('Imperial', gender)` fallback in
   `build_npc_to_vtyp_map` always resolves whatever races the plugin ships.
2. **The plugin's OWN races**, identified by the display name that also names
   the voice folder on disk.

Oblivion's voice layout is `sound\voice\<plugin>\<RACE FULL>\<gender>\`, and
in a localised plugin FULL and EditorID diverge: Nehrim's seven `Alemanne*`
races all read `FULL=Alemanne`, and its `HighElf` race reads `FULL=Hochelf`.
Keying on the EditorID alone left those folders pointing at voice types no
record declared, so the lines were unreachable. See `asset_convert.audio.voice_races`,
which the sound stage resolves through too.

Iteration is sorted because the output ESM must stay byte-reproducible.

## <a id="adopting-a-masters-synthesized-records"></a>Adopting a master's synthesized records

**Code:** `tes5_import/import_main.py` (`_adopt_master_special_records`),
`tes5_import/base/owned_records.py`

The conversion synthesizes records TES4 has no source for — globals, factions,
menus, formlists, and one VTYP per voiced race. A **root master** creates them;
a **dependent plugin** must adopt the master's FormIDs rather than create its
own, or its VMAD properties point at records in the wrong file.

### The voice-type case, which is the loud one

`create_vtyp_records` is master-only, and it is what calls `set_voice_type`. A
dependent plugin therefore left `VOICE_TYPE_MAP` EMPTY,
`build_npc_to_vtyp_map` returned `{}` — "0 NPC->VTYP" in the build log — and
`convert_NPC_`/`convert_CREA` wrote **no VTCK on any of Morroblivion's 3,607
actors**.

An actor with no voice type matches no dialogue, so EVERY line in the plugin
was silent, including Jiub's: the prison-ship intro played out mutely. For
contrast, Oblivion.esm as a root master has VTCK on 3,396 of 3,838 actors.

The master already wrote these VTYPs, so the fix is to adopt its FormIDs.

### Why the registry is cleared per plugin

`WELL_KNOWN_PROPERTIES` is a module global, and `convert.py` imports several
plugins in ONE process. Without the reset, a previous plugin's synthesized
FormIDs would be adopted by the next one. Each run repopulates it — created for
a root master, adopted from the master's output for a dependent plugin.

## <a id="script-addtopic-and-the-unlock-globals"></a>Script AddTopic and the unlock globals

**Code:** `tes5_import/import_main.py` (the `ScriptConverter.topic_unlock_globals`
handoff)

A script `AddTopic X` is the third reveal route for a topic, alongside INFO
fragments and quest stages, and `script_convert` emits the same
`TES4Unlock_<topic>.SetValue(1)` for it.

Two things must hold or that property binds to nothing:

- **The map must be shared.** The converter pass import_main runs — object and
  quest VMAD property resolution — must see the SAME topic→global map the
  script pipeline used. Otherwise it resolves a different property set than the
  generated `.psc` declares.
- **The global must be resolvable by name.** `WELL_KNOWN_PROPERTIES` does that
  for synthesized records with no TES4 counterpart, following the
  `TES4Fame`/`TES4GoldFenced` pattern.

## <a id="the-synthesized-record-id-window"></a>The synthesized-record id window

Writer ids start well above the highest source FormID so synthesized companion
records cannot collide with converted ones: real records stop at or below
`max_formid`, companions start at `+0x1000`, and the chargen menu MESGs take a
fixed window in the middle of the reserved gap, ahead of the null-LAND repair.

### Which synthesized records every plugin needs

The ForceCombat enemy-faction pair and the destroyed-reference FormList are
emitted unconditionally, chargen menus or not: ANY plugin that scripts a
StartCombat needs the first, and every converted getdestroyed reads the second.

The MESG menus are conditional on a plan, but their registration in
WELL_KNOWN_PROPERTIES is what resolves the TES4Msg_* Message properties the
converted .psc files declare -- the same TES4Fame/TES4Unlock binding pattern.

## <a id="addtopic-unlock-gates"></a>AddTopic unlock gates: what reveals a topic

**Code:** `tes5_import/dialogue/unlocks.py`

### Explicit reveals versus mentions

EXPLICIT reveals (an `AddTopic` data list, an `AddTopic` script command, a Choice
link) make the target reachable the instant the revealing line plays, independent
of the target's own conditions.

MENTION reveals -- the target's FULL name appearing in prose -- are tracked
separately. An Oblivion greeting saying "you're ready for advancement" only
auto-adds the topic WHEN THAT LINE FIRES, which is itself stage-gated, so a
mention in a bark line must NOT count as "revealed on first contact". Treating
it as one wrongly ungated 162 topics, e.g. Azzan's "Advancement" showing before
the guild is joined.

A choice link to a gated topic also reveals it: in Oblivion, offering a choice
makes the target reachable regardless of its added state, and once taken it stays
known. Speaking a line of topic T already requires T unlocked, so self-reveals
are meaningless and would only bloat the fragment count.

### Bark-revealed topics are not gated

A GREETING/HELLO revealer fires the moment the player contacts the NPC, so in
Oblivion the topic is effectively visible on first talk. A gate there only adds
the risk of the reveal fragment racing the menu -- or a different greeting
playing -- and locking the topic. Their own GetIsID/faction/stage conditions do
the real filtering. Gates stay only on topics revealed exclusively by
conversation lines or quest stages (e.g. "Rats" after Azzan's contract line).

Only an EXPLICIT bark reveal (AddTopic/Choice) counts; a prose mention rides the
bark line's own conditions and keeps the gate.

### Why quest stages are the robust anchor

A dialogue reveal is only as reliable as the line firing again, and an INFO's
OnEnd fragment fires ONCE, while you are still in the menu. If a topic's only
revealer for a given NPC is that one line and no post-reveal greeting re-fires
it, a single missed or raced `SetValue` leaves the gate shut forever -- globals
persist, so a reload does not help.

This is the Azzan vs Burz split. Burz has a member greeting ("Maybe you want a
contract?") that re-reveals `contract` on every talk, so his gate is continually
re-armed. Azzan's post-join greetings are all gated to LATER stages, so once you
join him nothing re-reveals it and the fragile one-shot is the whole story.

The robust anchor is the QUEST STAGE the same result script sets: a stage
fragment is guaranteed to run when the stage is reached, independent of dialogue
timing, and it too persists. So any reveal whose result script also does
`SetStage QUEST N` is additionally emitted as a stage reveal for (QUEST, N) --
the Fighters Guild join line's `SetStage FGD00JoinFG 100` makes the JoinFG
stage-100 fragment set `TES4Unlock_contract`, giving Azzan the same always-armed
guarantee Burz gets from his greeting. This covers 806 reveals game-wide, not a
special case.

A reveal can only be anchored to a stage that ACTUALLY emits a fragment: the QUST
VMAD fragment list (`tes5_import`) and the generated `.psc` functions
(`script_convert`) must match exactly, so binding a `SetValue` to a stage with no
fragment would either be dropped or create a dangling VMAD entry.

### The invariant: a gate with no revealer is an unopenable door

The gate (`GetGlobalValue` in the ESM) and the thing that opens it (a `SetValue`
in a generated Papyrus fragment) are built from THIS plan by two different
pipelines -- `tes5_import` writes the condition, `script_convert` writes the
fragment body. If a topic is gated but nothing anywhere sets its global, the
topic can NEVER appear: quest-blocking, and invisible to every record-level check
because the ESM looks perfect.

An ungated topic that shows a little early is a cosmetic bug; a gated topic with
no revealer is a dead quest. So when the two disagree, drop the gate.

### The bark-ungating exception

A bark reveal ungates a topic EXCEPT when the topic is ALSO revealed by a
conversation line. A greeting revealer belongs to whichever NPC that greeting is
gated to, and says nothing about a DIFFERENT NPC whose reveal comes from a topic
line.

The `contract` topic proves it: three greetings AddTopic it (Burz's "Maybe you
want a contract?" among them) AND the Fighters Guild join line does. Ungating it
on the greetings' account detached it from the join entirely -- after joining
Azzan, `contract` stood or fell purely on its own INFO conditions while
`advancementFG`/`ratsTOPIC` stayed gated, so the menu desynchronised: a player
who did not click Contract lost every topic and was left with the generic
INFOGENERAL pool ("Rumors"), which is exactly the reported symptom. Keeping the
gate makes the reveal explicit and idempotent from BOTH revealer kinds.

## <a id="info-fragment-emission"></a>INFO fragment emission: one decision function

**Code:** `tes5_import/dialogue/converter.py:_info_vmad`

Whether an INFO gets a `TES4_TIF__<fid>` fragment is decided by
`info_needs_fragment` -- the SAME function `script_convert`'s emitter calls. The
two used to each decide it independently and disagreed more than once: a flag bit
with no `.pex` function behind it, or a function nothing attaches. It must never
be re-derived locally.

Emission is not unconditional. The engine BINDS an INFO's fragment when it
SELECTS that line -- load and link the `.pex`, resolve properties -- before
anything is spoken, so a fragment with no behaviour is a cost paid on the
dialogue path itself.

`reveal_props` and `service_menu` are the caller's already-resolved answers to
the "reveals unlock globals?" and "opens a service menu?" questions that
`info_needs_fragment` otherwise looks up in its maps, so they are handed over as
single-entry maps rather than recomputed.

## <a id="info-enam-reset-timer"></a>INFO ENAM: the re-play lockout timer

**Code:** `tes5_import/dialogue/converter.py:_info_enam`

ENAM is Flags(U16) + Reset(U16). The reset field is what stops an NPC repeating a
line: once spoken, that INFO is ineligible until the timer expires. Beyond
Skyrim's Arcane University documents the flip side -- when every Greeting is on a
timer, the NPC falls back to generic dialogue.

TES4 has NO equivalent field (Oblivion INFO DATA is only
DialogType/NextSpeaker/Flags), so leaving it 0 looked like a faithful translation
of something Oblivion does not record. But 0 means no lockout at all: every
eligible line stays permanently re-playable, the engine re-picks from the whole
pool on each greeting attempt, and NPCs quip on repeat. All 5,636 converted HELO
lines had reset=0, where vanilla sets a real value on 65% of its greetings.

Units: the engine reads the second field as trunc(days * 65535)
(`docs/reference/dialogue_engine_contracts.md` -- `TESTopicInfo::LoadForm` mulss
by 65535.0), so the CK's "hours until reset" H is stored as H/24 * 65535.
Vanilla's values decode to clean hours: 1365=0.5h (its most common, 2809 of 5287
HELO lines = 53%), 2730=1h, 10922=4h, 32767=12h, 65535=24h.

SAY-ONCE lines (flag 0x04) are already permanently locked after one play, so a
reset would only weaken them; they keep 0.

## <a id="info-tclt-choice-filter"></a>INFO TCLT: which choice links survive

**Code:** `tes5_import/dialogue/converter.py:_info_tclt`

A bark INFO keeps only choices that point at a CONVERSATION topic -- the vanilla
greeting-to-CUST-response pattern (Skyrim HELO->CUST TCLT, e.g.
`C03SkorQuestStartBranchTopic`). A choice that points at another bark is dropped,
because barks are split/merged by (quest, subtype) in the bark pass and the link
would dangle at a sub-topic that no longer exists under that FormID.

Dropping the conversation ones too was a real regression: it left greetings with
a line but no selectable response (FGC01Rats -- Arvena asks what happened but the
player cannot answer).

Choices into a zero-INFO topic are dropped as well. Those topics are never
emitted (`EMPTY_DIAL_FIDS`) and Oblivion never showed them either.
