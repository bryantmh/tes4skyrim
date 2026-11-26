# Character sheet: attributes, legacy skills and Nehrim leveling

Status: PLAN

A Morrowind-style stats window, built into `TESRuntime.dll`, that shows Skyrim's
skills plus the attributes, extra skills and statistics of every converted game
installed, and replaces Nehrim's journal-item leveling. It is a mod on top of
the converter, shipped in TESGameSelect and requiring TESRuntime.dll.

**Mockup (clickable):** <https://claude.ai/artifact/PUhYcFLPuXwLSzWviaJBzE>.
It uses real data (skill descriptions, governing attributes, hunting counts,
crafting thresholds, level-up multipliers) and example character values.

Work order: [bugs first](#bugs), then the [MVP](#mvp).

**Built first, Morrowind only (unconfirmed in game):** the stats window (C, from the perks menu), the
level-up dialog after Skyrim's own level-up, and skill increases credited to
the governing attribute, all inside `MorrowindRuntime.dll` over the stat store
it already keeps. It uses Morrowind's own governing attributes (each Skyrim skill
through its namesake Morrowind skill), not the [one table](#governing) below,
which is for the multi-game sheet. See
[morrowind_runtime.md](../commentary/morrowind_runtime.md#character-sheet).
On top of it: the [attribute buffs](../commentary/morrowind_runtime.md#attribute-buffs),
Morrowind's [attribute effects](../commentary/morrowind_runtime.md#attribute-effects),
the [skill cap](../commentary/morrowind_runtime.md#skill-cap), and one ini switch,
on by default, whose off reads every attribute as 100
([sheet off](../commentary/morrowind_runtime.md#sheet-off)).

**Built since (2026-10-02, unconfirmed in game):**
- every converted game with attributes uses it ([TES4 tables](../commentary/morrowind_runtime.md#tes4-tables));
- [class and birthsign menus](../commentary/morrowind_runtime.md#chargen-menus), without major and minor skills;
- NPC attributes and kept skills as [stat faction ranks](../commentary/morrowind_runtime.md#npc-attributes), read by scripts and conditions (M3, M4, M5);
- the [Statistics tab](../commentary/morrowind_runtime.md#statistics-tab) (M9).

The player's kept skills still read their Skyrim stand-ins: nothing grows
them yet.

## <a id="decisions"></a>Settled decisions

| Question | Decision |
|---|---|
| Where it lives | `TESRuntime.dll` (required, no fallback) plus records and scripts in TESGameSelect |
| Game builds | 1.6.1170 first, then 1.7.x |
| Art | Morrowind's own UI art, composed from the player's Morrowind install at package time, as the dialogue menu already is. No Morrowind install means no sheet in v1 |
| Layout | "Skyrim + extras": Skyrim's 18 skills, then the few legacy skills Skyrim lacks |
| Who has stats | The player **and** NPCs. The Morrowind runtime's NPC stats move under the same store |
| Generic or not | Not generic. This is a mod, so Nehrim tables are fixed data. Converter bugs found on the way stay generic fixes |
| Leveling | Skyrim's own level-up in the skills menu, then the sheet opens to choose attributes |
| Health, Magicka, Stamina | Skyrim's +10 choice stays, scaled by the governing attribute: normal × (0.5 + attribute / 100). The full chart is in [attribute buffs](../commentary/morrowind_runtime.md#attribute-buffs) |
| On or off | One ini switch, on by default. Off: attributes read 100 (Personality reads Speech), no buffs, no cap. The skill cap has its own key, on by default |
| Kept skills when off | Read 100, like the attributes (not yet built) |
| New-game options | Attribute rules (Standard or Strict), plus a Nehrim-only "slowed skill growth" option |
| Nehrim's journal settings page | Dropped |
| Enderal's system | Not ported. Kept as a reference (`ScriptsEnderal.zip` in the Enderal SE install) |

---

## <a id="bugs"></a>Part 1: Bugs to fix first

Found while researching the sheet. None is fixed yet. Each is a converter or
runtime bug on its own, independent of the sheet, and each fix is generic.

### <a id="bug-skil-shift"></a>B1. Oblivion skill records are exported one field off

**Where:** `tes4_export/record_types/actors.py:511-517`.

The exporter reads `DATA` as Attribute, Specialization, UseValue1, UseValue2.
The real layout (xEdit `wbDefinitionsTES4.pas:3545`) is **Action, Attribute,
Specialization, Use Value ×2**, 20 bytes. So in every `SKIL.txt`:

- `DATA.Attribute` holds the skill's own index (Alchemy shows 19).
- `DATA.Specialization` holds the real governing attribute (Alchemy shows 1 = Intelligence).
- `DATA.UseValue1` holds the specialization as a float bit pattern (`1.4e-45` = int 1).
- `DATA.UseValue2` holds the real first use value; the second use value is lost.

**Fixed:** the exporter reads all five fields (`DATA.Action`, `Attribute`,
`Specialization`, `UseValue1`, `UseValue2`) with a `>= 20` length guard; tested
on SkillAlchemy's verbatim DATA. Nothing in `tes5_import` reads these fields
(SKIL is skipped, `registry.py:183`), so the fix changes no converted output. The sheet
needs them: governing attributes for the tooltips and the Strict cap, and use
values for Nehrim's slowed growth.

**Test:** an export test on a real Oblivion SKIL record (Alchemy: Attribute
Intelligence, Specialization Magic). **Build:** `--export-only` for Oblivion.esm
and Nehrim.esm.

### <a id="bug-nehrim-misc-stats"></a>B2. Nehrim's repurposed statistics land in Skyrim's vampire stats

**Where:** `script_convert/commands.py:240` (`pc_misc_stat`),
`script_convert/constants.py:25` (`TES4_MISC_STAT_NAMES`).

Nehrim reuses four of Oblivion's general statistics and renames them through
its own `sMisc*` game settings (`export/Translation.esp/GMST.txt`):

| Index | Oblivion label | Nehrim label | Converted today to Skyrim stat |
|---:|---|---|---|
| 22 | Days as a Vampire | Overall amount of experience points | "Days as a Vampire" |
| 24 | People Fed On | Current amount of learning points | "Necks Bitten" |
| 13 | Oblivion Gates Shut | Magical symbols found | dropped |
| 27 | Nirnroots Found | Rare plants found | "Nirnroots Found" |

`GlobalplayerScript` runs `ModPCMiscStat 22 EPdiff` and `ModPCMiscStat 24
LPdiff` every time EP or learning points change. So converted Nehrim adds every
experience point to Skyrim's "Days as a Vampire".

**Fixed (engine-kept rule, not the label):** Oblivion's own content writes
only stats 14, 15, 16, 19 and 27 (`TES4_SCRIPT_OWNED_MISC_STATS`); its engine
keeps the rest, so a plugin writing one has repurposed it and the write becomes
a note. The label comparison would be more precise but cannot cross languages:
German Nehrim.esm relabels all 52 stat labels, so every one differs from
Oblivion's English defaults. Cost: Nehrim's trainer (`ModPCMiscStat 3`) and
skill-book (`ModPCMiscStat 18`) writes no longer bump Skyrim's Training
Sessions and Skill Books Read. Once the sheet exists these go to its
statistics store (see [M9](#m9-statistics)). See
[the commentary](../commentary/script_convert.md#pc-misc-stat-names).

### <a id="bug-mysticism"></a>B3. Mysticism goes to two different schools

Spells and effects fold Mysticism into **Alteration**:
`tes5_import/record_types/magic.py:235` (school) and `:269` (effect skill).

Everything else folds it into **Illusion**:

| Site | Reads Mysticism as |
|---|---|
| `script_convert/constants.py` `ACTOR_VALUE_MAP['mysticism']` | Illusion |
| `script_convert/static_scripts/TES4Polyfill.psc` `MapActorValue` | Illusion |
| `tes5_import/record_types/npc.py:79` (NPC skills) | Illusion |
| `tes5_import/base/conditions.py:269` (dialogue conditions) | Illusion |
| `tes5_import/base/constants.py:192` | Illusion |
| `tes5_import/base/equivalents.py:676` (and the potion at `:634`) | Illusion |

A converted Mysticism spell trains Alteration, but every script and dialogue
check of Mysticism reads Illusion. **Fixed:** all six read Alteration, where
the spells are, and so does the Morrowind runtime's stat table. The same sweep
found **Mercantile** going to Pickpocket in the NPC-skill, trainer, class and
book tables while scripts, conditions and effects read Speech; all read Speech
now.

**Fixed, found on the way:** the Morrowind exporter folded Enchant into
Oblivion's Mysticism (`MW_SKILL_TO_TES4`). Enchant now exports as its own skill
index `MW_ENCHANT_SKILL` (100, actor value 112, past every TES4 value), and
`DATA.Enchant` on NPCs. The book, effect, condition and NPC tables map it to
Enchanting. While checking the book path, the BOOK table
(`TES4_SKILL_TO_TES5_INDEX`) turned out to be keyed by actor value (12-32)
while `DATA.Teaches` is a 0-20 skill index. All 110 Oblivion skill books
taught nothing or the wrong skill; the table is now keyed by skill index.
**Build:** `--import-only` and `--scripts-only`. Because
`TES4Polyfill.psc` changes, the
[static-scripts rule](../../CLAUDE.md#static-scripts-rebuild-all) applies:
rebuild scripts for every masterless plugin.

### <a id="bug-blade-blunt"></a>B4. Blade and Blunt read only One-Handed

Oblivion's Blade covers claymores and its Blunt covers warhammers
(Oblivion.esm SKIL descriptions). Morrowind's Long Blade, Axe and Blunt Weapon
also have two-handed kinds. Morrowind spears export as two-handed blades
(`tes4_export/record_types/morrowind.py:27-28`). Today:

| Site | Blade | Blunt |
|---|---|---|
| `script_convert/constants.py` `ACTOR_VALUE_MAP` | One-Handed | One-Handed |
| `tes5_import/base/conditions.py:259-261` | One-Handed | One-Handed |
| `tes5_import/record_types/npc.py:74-75` (NPC skills) | One-Handed | One-Handed |
| `tes5_import/record_types/magic.py:259-261` (Fortify effects) | One-Handed | **Two-Handed** |
| Morrowind runtime `kSkills` | One-Handed (all blades, axes, blunt, spear) | |

So an NPC with Blade 60 gets Two-Handed 15, and a script gate on Blade ignores
a two-handed character entirely.

**Fixed:**
- NPC skills: a split skill feeds **both** One-Handed and Two-Handed (`npc.py` `_TES4_SKILL_TO_TES5`).
- Script reads: `GetAV` takes the higher of the two (`TES4Polyfill.HigherActorValue`); writes and `GetBaseAV` stay One-Handed, because a base read feeds a write (Nehrim's trainers read the base, add 1 and write it back).
- Conditions (`tes5_import/base/split_skill_conditions.py`): a `GetActorValue` test with `>=`/`>` becomes One-Handed OR Two-Handed, and `<`/`<=` outside an OR group becomes One-Handed AND Two-Handed. `GetBaseActorValue` and any other shape keep One-Handed alone. Oblivion.esm's 6 are `GetActorValue >= 70` and split; Nehrim's 8 are `GetBaseActorValue < 90/85` trainer caps and stay One-Handed. FO3/FNV Melee Weapons (38) splits the same way; FNV's 14 is Critical Chance and does not.
- Morrowind runtime: Long Blade, Axe and Blunt Weapon read the higher of One-/Two-Handed, Medium Armor the higher of Heavy/Light, Spear Two-Handed.
- Fortify effects: one actor value per effect, since a converted effect is one MGEF variant. Fortify Blade is One-Handed and Fortify Blunt is Two-Handed (`magic.SKILL_TO_AV`); the vanilla-potion fallback in `base/equivalents.py` now agrees. Emitting both halves would change every affected item's effect count and cost.
- Spear reads Two-Handed.

**Build:** `--import-only`, `--scripts-only` for every masterless plugin, and
the Morrowind runtime DLL.

### <a id="bug-fame"></a>B5. Fame and Infamy are written to one place and read from another

Converted scripts keep Fame and Infamy in the `TES4Fame`/`TES4Infamy` globals
(`script_convert/command_rows.py:271-283`, records from
`tes5_import/base/owned_records.py:41`). Dialogue conditions map them to Skyrim
actor values 60 and 61 (`tes5_import/base/conditions.py:283-284`), which
nothing sets.

**Fixed:** 60 and 61 are Skyrim's Fame and Infamy (xEdit TES5 enum), and none
of Skyrim.esm's conditions reads them. A **run-on-target** (player) test, 10 of
Oblivion.esm's 14, becomes `GetGlobalValue` on the global; a **subject** test (4
guard greetings testing the guard's own Infamy, 0 in Oblivion too) stays on the
actor value. See
[the commentary](../commentary/tes5_import_conditions.md#fame-infamy-global).

**Fixed, found on the way:** FO3/FNV condition actor values were translated
with Oblivion's table, so FNV's 540 Speech checks dropped and Melee Weapons and
Repair landed on Fame and Infamy. They now use FNV's own table; see
[the commentary](../commentary/tes5_import_conditions.md#fallout-actor-values).

### <a id="bug-persuasion"></a>B6. Morrowind persuasion ignores the player's live Personality and Luck

**Where:** `tes_runtime/morrowind/plugin/persuasion.cpp:104-116`.

`PlayerSide()` takes Personality and Luck from Morrowind's `player` NPC record
(through `NpcSide`) and never from the runtime's stat store, which scripts can
change (`ModPersonality`). **Fixed:** both sides read them through
`ActorAttribute`, the same single read the dialogue filter uses, falling back
to the actor line's own column when the store reads 0 (a sidecar older than
its attribute column). Tested in `script_test.exe` (`PersuasionCases`). After
[M3](#m3-store) this becomes the sheet's value.

### <a id="bug-level-gmst"></a>B7. Nehrim's "skills don't level you" setting is never converted

Nehrim sets `iLevelUpSkillCount = 9999999` (`export/Nehrim.esm/GMST.txt`), so in
Nehrim skill increases never cause a level-up. Levels come only from EP. In
converted Nehrim, Skyrim's skill-based leveling still runs underneath Nehrim's
scripted leveling.

This cannot be fixed with Skyrim game settings in the Nehrim plugin
(`fXPPerSkillRank` and the rest, as Enderal does). Game settings are global,
so that would switch off leveling in every installed game. **Fixed by the MVP**
([M6](#m6-levelup), [M8](#m8-nehrim)), per current game.

### <a id="bug-advancepclevel"></a>B8. Unverified: whether converted `AdvancePCLevel` levels the player

`script_convert/command_rows.py:295` converts it to
`Game.GetPlayer().ModActorValue("Level", 1)`. Enderal levels with SKSE's
`Game.SetPlayerLevel`. It needs confirming that "Level" is a writable actor
value. The MVP replaces Nehrim's only call site (the journal), so this matters
only for other plugins. **Check:** grep converted `.psc` for other callers.

---

## <a id="mvp"></a>Part 2: MVP

### <a id="systems"></a>What the source games do

| | Skyrim | Oblivion | Morrowind | Nehrim |
|---|---|---|---|---|
| Skills | 18 | 21 | 27 | Oblivion's 21, Armorer renamed "Crafting" |
| Attributes | none | 8 | 8 (same names) | 8 |
| Skills grow by | use | use | use | use, slowed (per-skill SKIL use values), plus trainers |
| Level comes from | skill XP | 10 major-skill increases | 10 major/minor increases | EP only (`iLevelUpSkillCount` 9,999,999) |
| On level-up | perk, +10 H/M/S | 3 attributes × multiplier | same | 7 learning points (10 the first time), then Oblivion's attribute screen (unverified, [L7](#learn)) |

Nehrim details, all read from `export/Nehrim.esm`:

- **EP awards:** kills (`GlobalScriptExpGained`, value per creature script), picked locks 30, discoveries 20 (`AAGeneralUpdateQuest`).
- **EP per level:** level × `EPMultiplikator` (600; 740 from level 20, 840 from 25, 1,080 from 30).
- **Trainers:** 1 learning point plus level × 6 gold per +1, or 5 points and five times the gold per +5. 98 INFO lines. They set the base skill directly (`SetActorValue`), so trained points never count toward attribute bonuses.
- **Skill growth:** Nehrim lowered most use values (Blade 0.5 → 0.08, Heavy Armor 1.25 → 0.3, Block 1.25 → 0.5) and raised a few (Mercantile 0.4 → 1.0). Read through the B1 shift until it is fixed.
- **Governing attributes differ from Oblivion's:** Illusion and Destruction are Intelligence, Athletics is Agility, Acrobatics is Endurance.

### <a id="design"></a>The design in brief

- **One character.** One level, one set of attributes, one set of legacy skills, whichever game is running.
- **Skills.** Skyrim's 18 skills are the engine's own. Nine legacy skills are folded into them (below). Four are kept by the sheet: **Athletics, Acrobatics, Hand-to-Hand, Unarmored**.
- **Attributes.** Eight, kept by the sheet, raised at level-up.
- **Current game.** TESGameSelect's `TESGS_CurrentGame` decides whose rules apply (EP bridge, Nehrim growth option). It is set by the new-game menu and the travel scroll, and it handles Morroblivion, where Morrowind's cells sit in an Oblivion-format plugin.

#### <a id="folding"></a>Folded skills

| Legacy skill | Game(s) | Now read as | Why |
|---|---|---|---|
| Blade, Blunt | OB, NE | higher of One-Handed / Two-Handed | both covered one- and two-handed weapons |
| Long Blade, Axe, Blunt Weapon | MW | higher of One-Handed / Two-Handed | same |
| Short Blade | MW | One-Handed | daggers and short swords |
| Spear | MW | Two-Handed | spears export as two-handed blades |
| Medium Armor | MW | higher of Light / Heavy Armor | each piece converts to one or the other |
| Mysticism | OB, MW, NE | Alteration | its spells already convert there ([B3](#bug-mysticism)) |
| Mercantile | OB, MW, NE | Speech | Speech trains from trading and sets prices |

A trainer for a split skill (Nehrim's "Blade +1") asks which of the two to raise.

#### <a id="governing"></a>One governing table

A skill can have only one cap, so the sheet uses one table: Oblivion's, through
the folded skills, with Enchanting from Morrowind's Enchant and Pickpocket
from Oblivion's Sneak. The per-game differences stay in the tooltips as information.

| Attribute | Governs |
|---|---|
| Strength | One-Handed, Two-Handed, Hand-to-Hand |
| Intelligence | Conjuration, Enchanting, Alchemy |
| Willpower | Alteration, Destruction, Restoration |
| Agility | Archery, Lockpicking, Pickpocket, Sneak |
| Speed | Light Armor, Athletics, Acrobatics, Unarmored |
| Endurance | Block, Smithing, Heavy Armor |
| Personality | Illusion, Speech |
| Luck | nothing |

#### <a id="rules"></a>Attribute rules (chosen at new game)

| | Standard | Strict |
|---|---|---|
| Attributes rise at level-up | yes | yes |
| Content checks read them (guilds, faction ranks, persuasion, dialogue) | yes | yes |
| Gameplay bonuses | none; Skyrim's balance is untouched | yes, centered so an attribute of 40 plays like vanilla (**not in the MVP**; magnitudes undecided) |
| Skill cap | none | a skill cannot rise above its governing attribute |
| Legacy skill effects (speed, jump, unarmed damage, unarmored armor rating) | none | yes (**not in the MVP**) |

Strict's cap extends Morrowind's own trainer rule, `sNotifyMessage17` "You
cannot train a skill above its governing attribute." (Morrowind.esm; enforced in
OpenMW `apps/openmw/mwgui/trainingwindow.cpp:186`). Following Morrowind:

- **Blocked above the cap:** skill use and trainers.
- **Still allowed:** skill books and quest rewards.
- **The cap is the fortified attribute:** OpenMW checks `getModified()`.
- **Nothing is lowered:** a skill above its cap simply stops.

The rule can later be changed from Strict to Standard only.

### <a id="components"></a>Components

#### <a id="m1-menu"></a>M1. Shared menu plumbing

Move the working custom `IMenu` (`tes_runtime/morrowind/plugin/menu.cpp`:
register, load movie, render itself through vtable slot 6, text and number
setters) into `tes_runtime/common/`, so TESRuntime and MorrowindRuntime share
one copy. See [the menu must render itself](../commentary/morrowind_runtime.md#the-menu-must-render-itself).

#### <a id="m2-swf"></a>M2. The sheet movie

- Extend `asset_convert/ui/morrowind_menu_art.py` and `tools/generators/gen_morrowind_menu_swf.py` with the stats-window layout from OpenMW's `openmw_stats_window.layout`.
- Frame and boxes use `menu_thick_border_*` and `menu_thin_border_*`; colors come from `Morrowind.ini` `[FontColor]`; text uses Morrowind's own font (as the dialogue menu already does).
- Composed by `tools/release/package_runtime_dll.py` into the `TESRuntime` mod folder, never committed.
- **Content:** Health/Magicka/Stamina/Level bars; Level, Race, Perk points, Attribute rules; Nehrim's Experience, Learning points and Spell grade; the 8 attributes; skills as Skyrim + extras; Nehrim abilities and crafts; Character and Statistics tabs; tooltips; the attribute level-up step.

#### <a id="m3-store"></a>M3. Stat store (player and NPCs)

- **Where:** TESRuntime owns attributes and the four kept skills for every actor.
- **Starting values:**
  - NPCs start from their authored record: Oblivion/Nehrim `NPC_` `DATA` attributes (`tes4_export/record_types/actors.py:142`) and `CREA` `DATA` attributes (`:187`); Morrowind from the runtime's existing `ActorDef`.
  - The player starts from the chosen game's race attributes (`RACE` `ATTR`, male/female, `actors.py:370`).
  - **A chargen choice is retroactive** (race now; class and birthsign when their menus exist): when one moves a base attribute, every pick buff already earned is re-scored as if the attribute had always had its new value. Level-up gains on top are kept. This is unlike a level-up or a script's change, which is never retroactive.
- **Morrowind:** `script_ops_stats.cpp`'s own `stat|` store moves under this one. The Morrowind runtime reads and writes through TESRuntime, so there is one store and one read.
- **Player values** are mirrored into TESGameSelect globals (a new hand-assigned block; see [TESGameSelect records](../commentary/tesgameselect.md#records)) so dialogue conditions can read them with `GetGlobalValue` and saves carry them.
- **NPC values** live in the TESRuntime co-save. The co-save already exists for the journal: `tes_runtime/tes/journal_log.*`, `plugin.cpp`.
- **A mid-game install** reconstructs the player's attributes: each skill's gain over its race start counts as increases for its governing attribute, then past level-ups are replayed taking the best three multipliers each time.

#### <a id="m4-scripts"></a>M4. Converted scripts call the store

- Replace `TES4Polyfill`'s attribute stub (`IsTES4Attribute`, `TES4AttributeStub`) and the kept-skill aliases in `MapActorValue` with Papyrus natives registered by TESRuntime (get, set, mod for any actor).
- Folded skills read as in [Folded skills](#folding).
- Static-scripts rule: rebuild scripts for every masterless plugin.

#### <a id="m5-conditions"></a>M5. Dialogue conditions on attributes and kept skills

Today these conditions are dropped at import
(`tes5_import/base/conditions.py:238`, `:540`).

- **Player-subject** conditions become `GetGlobalValue` on the mirrored globals.
- **NPC-subject** conditions need the engine to ask TESRuntime; see [L4](#learn).

#### <a id="m6-levelup"></a>M6. Level-up

- **Flow:** Skyrim's own level-up (skills menu, Health/Magicka/Stamina +10, perk) → when it completes, the sheet opens on the attribute step.
- **The step:** pick three; each rises by Morrowind.esm's `iLevelUp01Mult..10Mult` (none +1, 1–4 +2, 5–7 +3, 8–9 +4, 10+ +5); Luck always +1.
- **Must finish:** the sheet cannot be closed until the step is done, with Oblivion's `sLevelDoneWarning` ("You need to finish distributing attribute points before leaving.").
- **Counting:** skill increases are credited to their governing attribute as they happen.
- **Only with converted games:** with no converted game installed, the step never appears.

#### <a id="m7-options"></a>M7. New-game options (TESGameSelect)

- **Attribute rules:** Standard / Strict. Asked once after the game is chosen.
- **Nehrim: slowed skill growth** (only when Nehrim is the chosen game; default on): while `TESGS_CurrentGame` is Nehrim, skill experience for each Skyrim skill is scaled by Nehrim's use value ÷ Oblivion's, taken through the folded skills.
- **Storage:** new globals in TESGameSelect's hand-assigned range. `test_no_two_records_share_a_formid` guards the growing blocks.

#### <a id="m8-nehrim"></a>M8. Nehrim takeover

- **The journal item** (`1Tagebuch` / `1TagebuchLevelup`) opens the sheet.
- **Nehrim's own logic is switched off:** `GlobaltagebuchScript` and the leveling part of `GlobalplayerScript` (EP threshold check, `VarLevelUp`, the nag messages, the `LevelUp1` spell, the book swap, the `Manazuwachs` magicka growth) are replaced. Other scripts keep reading the `EP` and `Lernpunkte` globals as before.
- **EP bridge:** while the current game is Nehrim, EP earned fills Skyrim's level bar proportionally: 10% of Nehrim's current requirement fills 10% of Skyrim's (level + 3) × 25. Skill increases in Nehrim add no level XP there, matching [B7](#bug-level-gmst).
- **Learning points** at each level-up: +7, +10 on the first.
- **Abilities:** 6 hunting flags (`JagdFellVar`, `JagdHerzVar`, `JagdHornVar`, `JagdKlauenVar`, `JagdKrallenVar`, `JagdZahnVar`; checked by 27, 6, 5, 11, 32 and 25 creature scripts) and 2 special skills (`VarTrapMine`, `VarTrapFrostSphere`). Shown as learned or not.
- **Crafts:** thresholds on Armorer (now Smithing) from the station scripts:
  - Anvil: 50 / 75, plus Place Mine to forge mines.
  - Smelting: 25 / 50 / 75 / 100.
  - Grindstone: 25.
  - Cooking fire: 20.
  - Brew kettle: 35 / 45.
  - Wine press: 45 / 55.
  - Prospecting and treasure digging check only for the tool (pickaxe, shovel), although the journal lists 20 and 15.
- **Spell grade:** from level (1, 7, 14, 21, 28, 35, 42).
- **Trainers** for Blade and Blunt ask One-Handed or Two-Handed ([B4](#bug-blade-blunt)).
- **Dropped:** the settings page (depth blur, underwater effects, night lights, autosave, memory cleanup).

#### <a id="m9-statistics"></a>M9. Statistics tab

| Group | Rows | Source |
|---|---|---|
| Standing | Fame, Infamy (Oblivion) | `TES4Fame` / `TES4Infamy` globals ([B5](#bug-fame)) |
| | Reputation (Morrowind) | Morrowind runtime `State().reputation` |
| | Bounty per game | the game's crime factions (`TES4CrimeFactions`) and the Morrowind runtime's |
| Oblivion | Oblivion Gates Shut, Artifacts Found | globals of ours: the Daedric quests' `ModPCMiscStat 19`, and `CloseCurrentOblivionGate`, whose engine code counted 13. Lockpicks Broken and Jokes Told only Oblivion's minigames counted, so they are dropped ([statistics tab](../commentary/morrowind_runtime.md#statistics-tab)) |
| Nehrim | the four relabeled stats ([B2](#bug-nehrim-misc-stats)), bank balance, bank interest | labels from `sMisc*` game settings; bank from `ErothinBankQuest.PlayerKontostand`; interest by the journal's rule: 2% before MQ14 stage 20, 1% before MQ19 stage 70, then 3% |

#### <a id="m10-input"></a>M10. Opening the sheet

- **Hotkey:** C, only in Skyrim's perks menu, where it is free; the sheet opens
  from there and nowhere else, through the key or a **[C] CHARACTER** button
  (built 2026-10-03, [perks button](../commentary/morrowind_runtime.md#perks-button)).
- **Other ways in:** the Nehrim journal item, and a Tween-menu entry if the engine allows it ([L9](#learn)).
- **Gamepad:** the engine's stick cursor (right stick, A clicks, B closes) and Y
  in the perks menu (built 2026-10-03, [controllers](../commentary/morrowind_runtime.md#controller)).

### <a id="oblivion-plan"></a>Oblivion and Nehrim: plan of attack (proposed 2026-10-02)

Morrowind already has the whole chain: a stat store, buffs, the skill cap,
attribute magic, the level-up step, race starts, tooltips and the menus. The
Oblivion work reuses it rather than building a second one.

**Where they stand today (read in the code, counted in `export/`):**

| Area | Today | Count |
|---|---|---|
| Script reads and writes (`GetAV Strength`, `ModAV`, …) | reads stubbed to 100, writes dropped (`script_convert/constants.py` `TES4_ATTRIBUTES`, `ATTRIBUTE_STUB_VALUE`) | Oblivion 51 calls, Nehrim 55 |
| Dialogue/package conditions on attributes | dropped at import (`dialog_conditions._TES4_AV_ATTRIBUTES`) | not yet counted (the census query found none, so it was wrong) |
| Attribute magic (FOAT, DRAT, DGAT, ABAT, REAT) | mapped onto a stand-in Skyrim value (`magic.ATTRIBUTE_TO_AV`: Strength → CarryWeight, Endurance → Health, …) | Oblivion ≈1,270 uses on SPEL/ENCH/ALCH/INGR/SGST, Nehrim ≈1,000 |
| Level-up | Skyrim's only | — |
| Race starts | none | — |

**Order of attack.** Each step is one build and one in-game check, and each
leaves the game working.

1. **One sheet, every game.** The store, buffs, cap, leveling, tooltips and
   menus stay in MorrowindRuntime (it ships in the same `TESRuntime` mod) and
   switch on when any converted game with attributes is installed, not only
   Morrowind. Which game's rules apply comes from `TESGS_CurrentGame` ([M7](#m7-options)).
   No code is copied into TESRuntime: parts are ported from OpenMW (GPL), so
   moving them under MIT would need a rewrite for nothing.
2. **The tables, per game, in the sidecar the TES4 import writes:** SKIL
   (governing attribute and specialization from `DATA`), RACE (the `ATTR`
   male/female starts, keyed by Skyrim race exactly as `RACE.txt` is), and
   the NPC_/CREA authored attributes. These are the same file formats the
   Morrowind sidecar uses, so the runtime reads both.
3. **Scripts.** The 106 calls become Papyrus natives the runtime registers
   (`TES4Polyfill.GetAttribute/SetAttribute/ModAttribute` for any actor),
   replacing the stub. `--scripts-only` for every masterless plugin (static
   scripts rule).
4. **Magic.** FOAT/DRAT/DGAT/ABAT/REAT move off the stand-in values onto the
   per-attribute runtime variants Morrowind already uses
   (`magic_variants.build_av_variants` and the effect table), so one tick
   handles all three games. Changed MGEFs keep their FormIDs; new variants are
   hashed by EditorID. Measure drift before shipping.
5. **Conditions.** Count them correctly first. Player-subject ones become
   `GetGlobalValue` on TESGameSelect globals the runtime mirrors ([M5](#m5-conditions)).
   NPC-subject ones are decided after the count shows how many there are.
6. **Leveling per game.** Oblivion's step is Morrowind's (three attributes ×
   `iLevelUp##Mult`, Luck +1), so it reuses the code with Oblivion's own SKIL
   and GMST rows. The sheet's skill list comes from the current game's SKIL
   table (21 skills, not 27). Nehrim keeps its own rules ([M8](#m8-nehrim)).
7. **Menus.** Oblivion and Nehrim get the Skyrim style with their own icons
   (built 2026-10-02, `menu_art.OblivionIcons`).
8. **Chargen.** Native class and birthsign menus, for Morrowind mode and for
   the Papyrus calls that open them today. Each feeds the starting attributes
   through the same retroactive rule as race ([M3](#m3-store)).

**Decisions needed before step 1:**
- Should an Oblivion-only install (no Morrowind) still use the sheet's
  hotkey, or open it from the journal or Tween menu only?
- Standard or Strict ([rules](#rules)): do Oblivion attributes also give the
  buffs and the cap, or only gate content?

### <a id="not-mvp"></a>Not in the MVP

- Strict's gameplay bonuses and the kept skills' effects (magnitudes to decide).
- Major and minor skills (classes and birthsigns are built).
- Morrowind's own class-based leveling (replaced by Skyrim's).
- Nehrim's Endurance-based health and Intelligence-based magicka (Skyrim's pools stay).
- A sheet for players without a Morrowind install.
- SE 1.5.97 and VR.

---

## <a id="learn"></a>What we need to learn

Each item names where to look first, following the
[verification order](../../CLAUDE.md#verifying-your-work).

| # | Question | Where to look |
|---|---|---|
| L1 | Which engine function finishes Skyrim's level-up (the Health/Magicka/Stamina choice), for the hook that opens the attribute step | StatsMenu in 1.6.1170, then 1.7.x; SKSE `PlayerSkills`; the CK exe's strings |
| L2 | The skill-experience path, for the Strict cap and Nehrim's slowed growth: where use XP is added, and whether trainers, skill books and quest rewards take separate paths | SKSE `PlayerSkills::AdvanceSkill`; `TrainingMenu`; `Game.IncrementSkill` |
| L3 | How to add level progress from EP: the player's level XP and threshold fields | SKSE `PlayerSkills::Data` layout, confirmed against 1.6.1170 |
| L4 | How a dialogue condition on an **NPC's** attribute can read a value only the DLL keeps: a hook on the `GetActorValue` condition function for chosen indices, or another route | condition evaluation in 1.6.1170; CK asserts on condition functions |
| L5 | Whether actor values 60 and 61 are Skyrim's Fame and Infamy, and whether the engine touches them ([B5](#bug-fame)) | xEdit TES5 actor value enum; a vanilla census of `references/Skyrim.esm` conditions |
| L6 | Whether `ModActorValue("Level", 1)` levels the player ([B8](#bug-advancepclevel)) | the engine's actor value table; a vanilla census |
| L7 | Whether Nehrim shows Oblivion's attribute screen after `AdvancePCLevel`; decides whether Nehrim had attribute growth at all | `Oblivion.exe` in the Nehrim install: the command's handler |
| L8 | Whether TESGameSelect's `TESGS_CurrentGame` is right at every moment the sheet needs it (new game, travel, loading an old save) | `TESGameSelect/scripts/source`; [tesgameselect.md](../commentary/tesgameselect.md) |
| L9 | Adding an entry to the Tween menu, and the default hotkeys | 1.6.1170 TweenMenu; Skyrim's `controlmap.txt` |
| L10 | The stats window's exact layout and textures | `references/openmw/files/data/mygui/openmw_stats_window.layout`; `Morrowind.bsa` |
| L11 | How the Morrowind runtime's player and NPC stat reads route through TESRuntime without a load-order dependency between the two DLLs | `tes_runtime/common`; how the DLLs already share code |
| L12 | Uninstall behavior: TESGameSelect globals vanish, TESRuntime co-save data is orphaned; what a reinstall sees | SKSE serialization |
| L13 | Other leveling mods (uncappers, Experience) hooking the same functions: our hooks must call through | the hook helpers in `tes_runtime/common/hook.cpp` |

## <a id="testing"></a>Testing

- **Converter bugs:** targeted pytest per bug, then the build stages listed with each.
- **Runtime:** C++ test executables in the Morrowind runtime's style (`filter_test`, `store_test`) for the store, multipliers, the EP bridge and the cap rule. Each needs its own working directory ([memory](../commentary/morrowind_runtime.md)).
- **In game:**
  - A Nehrim start that earns EP to level 2 and gets 10 learning points.
  - An Oblivion guild promotion gated on Strength.
  - A Morrowind faction rank gated on attributes.
  - A Strict character whose skill stops at its attribute.
  - A trainer asking One-Handed or Two-Handed.

## <a id="sources"></a>Sources

- Nehrim scripts: `export/Nehrim.esm/SCPT.txt` (`GlobalplayerScript`, `GlobaltagebuchScript`, `GlobalScriptExpGained`, `AAGeneralUpdateQuest`, `EPJagd*`, `JagdBuch*`, `Werkzeug*`, `Zauberhaendler*`).
- Nehrim and Oblivion game settings and skill records: `export/*/GMST.txt`, `export/*/SKIL.txt`.
- Morrowind skills and settings: `Morrowind.esm` read directly (`SKIL`, `GMST`).
- OpenMW: `trainingwindow.cpp`, `npcstats.cpp`, `files/openmw.cfg` (UI colors), `openmw_stats_window.layout`.
- Enderal SE (reference only): `_00E_EPUpdateFunctions`, `_00E_QuestFunctions`, `_00E_Game_SkillmenuSC` in `ScriptsEnderal.zip`; its `Skyrim.esm` switches off use-based leveling with `fSkillUseCurve` 1e9 and `fXPPerSkillRank`, `fXPLevelUpBase`, `fXPLevelUpMult` = 999.
