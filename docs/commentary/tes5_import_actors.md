# TES4 → TES5 actor conversion

**Code:** `tes5_import/record_types/actor_common.py`, `npc.py`, `creature.py`

Why the actor converters are shaped the way they are. `actor_common.py` holds
what NPC_ and CREA both need; `npc.py` and `creature.py` hold what only one of
them does.

## Contents

- [ACBS flags: the same bit means three different things](#acbs-flag-collision)
- [NAM5 and NAM8: widths taken from the binary](#nam5-nam8-widths)
- [Crime factions are derived from the scripts](#crime-factions-derived)
- [Faction reactions and player disposition](#faction-player-disposition)
- [Aggression and confidence are TIERS, not scalars](#aggression-tiers)
- [Confidence: why converted actors used to flee](#confidence-tiers)
- [Vendor factions](#vendor-factions)
- [The merchant marker faction: a CTDA ceiling](#barter-gate-ctda-limit)
- [The plugin-origin marker faction](#origin-faction)
- [It also unlocks AI barrier doors](#barrier-door-ownership)
- [FACT relations: Ally and Friend are not interchangeable](#faction-relations)
- [Trainers](#trainers)
- [Health is written as an OFFSET, not a pool](#health-offset)
- [Morrowind health is absolute, so its level term is dropped](#morrowind-health-is-absolute)
- [Hair color: a generated CLFM per authored RGB](#hair-color)
- [NAM5/NAM6/NAM7/NAM8 are all required](#required-nam-subrecords)
- [Head parts: RNAM decides who can see the hair](#hdpt-valid-races)
- [Voice type resolution](#voice-resolution)

## <a id="acbs-flag-collision"></a>ACBS flags: the same bit means three different things

A TES4 CREA ACBS bit, a TES4 NPC_ ACBS bit and a TES5 NPC_ ACBS bit with the
same number mean unrelated things (xEdit `wbDefinitionsTES4` CREA ACBS vs
`wbDefinitionsTES5` NPC_ ACBS). A raw `flags & mask` therefore reinterprets
creature data as unrelated NPC behaviour. The old `& 0x4C9B | 0x10` did exactly
that:

| TES4 CREA bit | became TES5 | effect |
|---|---|---|
| 0 Biped | 0x01 Female | every biped went female |
| 4 Swims | 0x10 Auto-calc stats | swimmers got autocalc |
| 11 No Blood Spray | 0x800 Protected | unkillable by NPCs |

It also dropped bit 9 "No Low Level Processing" entirely. The conversion is
therefore written as an explicit bit-by-bit mapping between named constants,
never a mask.

## <a id="nam5-nam8-widths"></a>NAM5 and NAM8: widths taken from the binary

`NAM5` is `wbUnknown` in xEdit, so its width comes from the binary: every NPC_
in the real Skyrim.esm writes exactly 2 bytes, `FF 00` (verified over 60
decompressed records). Writing it as a u32 would desync the subrecord stream.

`NAM8` "Sound Level" is `wbSoundLevelEnum` (0 Loud, 1 Normal, 2 Silent).
Vanilla writes Normal on 5,116 of 5,118 NPC_ records.

## <a id="crime-factions-derived"></a>Crime factions are derived from the scripts

Oblivion's crime functions read per-faction flags the engine maintains for any
faction the player can offend; there is no TES4 "this is a crime faction" flag
to carry across. Skyrim instead requires Track Crime (DATA bit 6) plus nonzero
CRVA amounts before it accumulates crime gold at all, so the set has to be
derived.

Scanning the scripts is the generic way: whatever faction a plugin actually
tests with `Get/SetPCFaction{Murder,Attack,Steal}` is by definition one whose
crimes it tracks. Matching is on EditorID rather than FormID, so a plugin
defining its own crime faction gets the same treatment as vanilla's `0005D556`.

SCTX arrives with `\r\n` still escaped; the regex only needs the function name
and its first argument, so no unescaping is needed.

## <a id="faction-player-disposition"></a>Faction reactions and player disposition

`_FACTION_PLAYER_DISP` maps a faction's low 24 bits to its Relation disposition
toward PlayerFaction, populated in Phase 0 before any actor converter runs. An
empty map is a safe default: `_player_disposition` then falls back to
Personality alone, which is the TES4 base disposition.

TES4 PlayerFaction is `0x0001DBCD` — the same FormID in Oblivion.esm and
Nehrim.esm, since a Nehrim record over the same master layout keeps the id.

Disposition adds a player-Personality term: +1 per 4 points the player's
Personality exceeds the actor's (UESP Oblivion:Disposition).

## <a id="aggression-tiers"></a>Aggression and confidence are TIERS, not scalars

Both games gate combat on the SAME two axes, only renamed, so the mapping
models the TES4 rule directly rather than bucketing aggression alone.

**TES4** (UESP Oblivion:Aggression / Oblivion:Disposition): an actor attacks a
target when `disposition(actor→target) < aggression - 5`. Starting disposition
toward the player ≈ the actor's Personality, shifted by race/faction reactions;
"enemies are programmed to have NEGATIVE dispositions towards you." Aggression
≤ 5 never attacks; ≥ 106 attacks anyone regardless of disposition.

**TES5** replaces the 0-100 disposition scalar with a discrete combat reaction
(Enemy/Neutral/Friend/Ally) and makes aggression a TIER saying which reactions
it will attack:

| Tier | Name | Attacks |
|---|---|---|
| 0 | Unaggressive | nobody unless provoked |
| 1 | Aggressive | Enemies on sight |
| 2 | Very Aggressive | Enemies AND Neutrals on sight |
| 3 | Frenzied | anybody on sight |

**The key point:** aggression is not "how hostile is this actor", it is "WHICH
REACTION TIER does it attack". Who it is hostile TO lives in the faction graph,
in BOTH games. UESP Skyrim:NPCs#Aggression states it directly: "Together with
the FACTION RELATIONSHIP COMBAT MODIFIER this governs whether the NPC initiates
combat". The player is a Neutral to anyone with no relation to PlayerFaction,
so tier 2 is the line between "hunts its faction enemies" and "hunts you".

Worked through for Nehrim's Benno, a dog in MarauderFaction + BanditFaction
with aggr=30, Personality=10:

    Benno → a bandit : 10 + (-100) = -90 < 25   → attacks   (Enemy)
    Benno → player   : 10 +     0  =  10        → no faction relation at all

MarauderFaction and BanditFaction relate ONLY to each other (-100) and to
CreatureFaction (+20); NEITHER has any relation to PlayerFaction. Benno is
aggressive toward MARAUDERS, not toward you — and Oblivion's own CreatureDog
carries byte-identical data, which is why UESP lists Dog under "Aggressive
Animals" while the dog still never mauls the player on sight.

That is the whole bug the current rule avoids: collapsing a per-target rule
onto one global tier and then resolving it against the player. Any actor whose
hostility is expressed purely as faction relations must land on tier 1 — the
faction graph (converted faithfully into XNAM Group Combat Reaction) then does
the targeting exactly as it does in vanilla Skyrim, whose own horses, deer,
elk, cows, goats, foxes and sabre cats are ALL Aggression 0 for the same reason.

### Rejected: gating tier 2 on player-enemy factions

Gating tier 2 on "does a faction make the player an enemy" was tried and is
WRONG. Oblivion's wolves/bears/trolls/mountain lions sit in CreatureFaction,
which has no PlayerFaction relation either, so that rule dropped every predator
to tier 1 and made the wilderness passive.

Skyrim separates these two cases with AIDT's Aggro Radius fields (EncWolf is
Aggression 0 but carries `aggroRadiusBehavior=1`, attack radius 1500) — fields
TES4's AIDT does not have at all (xEdit wbDefinitionsTES4: AIDT is
Aggression/Confidence/Energy/Responsibility/Services/Teaches/MaxTraining only).
The discriminator therefore has to be reconstructed; see `_predator_attack_radius`.

### The calibrated rule

UESP Oblivion:Animals draws the exact distinction needed for the dogs: randomly
generated dogs "are Bandit or marauder dogs ... that are hostile towards you,
ALTHOUGH THEY WILL NOT NECESSARILY ATTACK ON SIGHT", while "the other dogs in
the game are all pets of townspeople and are friendly". "Hostile but not on
sight" is precisely tier 1; "on sight" is tier 2.

Two terms decide it:

* **Prey membership** — vanilla's marker for harmless. Its 43 members are
  horses, deer and sheep, several at aggression 100, so no aggression threshold
  can exclude them (this is what broke earlier attempts).
* **The attack MARGIN**, `(aggr-5) - disposition`: how decisively the TES4 rule
  fires. Measured across Oblivion's creatures, known predators have a median
  margin of 48 and tame animals -47, while Benno scores just 8 — hostile in
  principle, not a threat on sight.

FO3/FNV does not go through any of this: it already stores both axes in the
TES5 enums. See
[tes5_import_falloutnv_actors.md](tes5_import_falloutnv_actors.md#aggression-is-already-a-tier).

## <a id="confidence-tiers"></a>Confidence: why converted actors used to flee

Both engines feed confidence into `fAIFleeConfBase`/`fAIFleeConfMult` to score
"should I run away", but TES4 supplies a 0-100 number while TES5 supplies one
of five tiers (xEdit `wbConfidenceEnum`: 0 Cowardly, 1 Cautious, 2 Average,
3 Brave, 4 Foolhardy). Only tier 4 never flees.

The old mapping was `<30 → 0, >=70 → 3, else 2`, which never emitted tier 1 or
tier 4 at all and capped the whole top of the range at Brave. That is why
converted actors fled constantly: Oblivion's "fearless" value 100 is by far the
most common setting (1,567 of 3,396 exported actors) and it landed on Brave,
which still has a nonzero flee score, instead of Foolhardy.

Vanilla Skyrim leans the opposite way — of 5,118 NPC_ records the distribution
is 292/90/1730/393/2613, i.e. Foolhardy is the single most common tier and more
than half of all NPCs sit at 3 or 4.

Anchoring on the values Oblivion actually uses (100 = fearless, 75-95 = brave,
50 = the engine default "average", 5-25 = timid, 0 = flees on sight) reproduces
a vanilla-shaped spread. Must stay in sync with `_scale_enum_av` in
`script_convert/converter.py`, which buckets scripted `setav confidence N` onto
the same tiers.

## <a id="vendor-factions"></a>Vendor factions

TES4 `AIDT.Services` is a bitmask on the actor; Skyrim expresses the same idea
as membership in a vendor FACT carrying a VEND formlist of item keywords. A
vendor only trades items whose keywords appear in its faction's VEND formlist,
so `_TES4_SERVICE_BIT_TO_SKYRIM_KEYWORDS` MUST stay in sync with the keywords
the item converters emit (`VENDOR_KYWD` in `record_types/common.py`).

Training (bit 14), Recharge (bit 16) and Repair (bit 17) have no vendor keyword
equivalent — Training is handled by CLAS, the others are TES4-only.

`_vendor_faction_cache` maps a service bitmask to a shared vendor FACT, used
for merchants with no dedicated merchant chest — they trade from their carried
inventory only. `_merchant_faction_by_npc` holds the per-merchant factions,
which carry a VENC (Merchant Container) pointing at the actor's own converted
Oblivion merchant chest so the barter menu stocks its full merchandise.

### <a id="barter-gate-ctda-limit"></a>The merchant marker faction: a CTDA ceiling

Every merchant joins one marker faction purely so the Barter topic can be gated
with a SINGLE `GetInFaction` condition.

The barter gate used to OR over every per-service vendor faction (25 of them),
which pushed each Barter INFO to 25-30 CTDAs. Vanilla Skyrim never exceeds 22
conditions on an INFO (max OR-run 20), and past that the engine silently drops
the line — so every Barter INFO failed and merchants lost the topic entirely,
while Training (a 1-condition gate) kept working.

Membership in the marker is what the dialogue asks about; the per-service
factions still do the actual vending via the VEND keyword filter and VENC chest.

## <a id="origin-faction"></a>The plugin-origin marker faction

Every actor a file defines joins it, and dialogue that states no plugin-scoped
audience of its own is gated on it (see `dialog_conditions.needs_origin_gate`).

Without it, two converted plugins loaded together cross-talk: Oblivion's
guard/crime/directions/rumour lines are scoped only by `GetIsRace` (or by a
NEGATIVE `GetIsID`), and conversion rewrites `GetIsRace` to a VANILLA Skyrim
race every plugin shares, so Nehrim NPCs passed them. Race was Oblivion's
plugin boundary only because Oblivion was the only file loaded.

ONLY a root master (no TES4 masters of its own) creates one and gates its
dialogue on it. A DLC/plugin's own dialogue stays ungated so it can extend and
override its master's exactly as it does in Oblivion.

A dependent's actors JOIN the origin faction of every converted master that
has one (`origin_memberships`, found by EditorID through
`ChainedMasterIndex.find_all_by_edid`). They used to join none, so a master's
generic lines could never play on an actor a dependent ADDS: measured on
TR_Mainland, 0 of 9,264 NPCs carried the compatibility patch's origin faction
while 4,322 of the patch's 4,647 voiced barks were gated on it — nearly every
vanilla Morrowind bark was silent on every Tamriel Rebuilt actor.

It is a plain membership marker: no flags, no relations, no vendor data, so it
can never affect crime, combat reaction, or the barter menu.

### <a id="barrier-door-ownership"></a>It also unlocks AI barrier doors

**Code:** `tes5_import/record_types/world.py` `convert_REFR`

Skyrim's AI only passes a locked door it OWNS or holds the key for — the 255
vanilla doors NPCs path through are exactly those (guards with gate keys,
homeowners). Oblivion's AI ignored locks entirely, so a Requires-Key keyless
barrier door with a consume script stranded actors: the CharacterGen back gate
stranded Glenroy.

Owning those doors to the plugin-origin faction grants every converted actor of
a root master the engine's own owner exemption, and costs nothing elsewhere
because the faction carries no crime data. The player is not a member, so the
lock still reads Requires Key and activation stays blocked; the OnActivate
preamble restores the lock after each AI passage.

TES4 `XACT`/`ONAM` ("Open by Default") is deliberately NOT transferred by
`convert_REFR`. In Skyrim those make the door SPAWN open, but Oblivion doors
carrying them still spawn closed — verified in-game, where every CharacterGen
portcullis stood open at load once they were passed through. Oblivion opens
such doors through the AI bypass instead, which is what the consume-door
handling at `XLOC` reproduces.

## <a id="faction-relations"></a>FACT relations: Ally and Friend are not interchangeable

TES4 Relations become TES5 XNAM — `Faction(FormID) Modifier(S32)
GroupCombatReaction(U32)`. The enum is xEdit `wbFactionRelations`
(wbDefinitionsCommon): **0 Neutral, 1 Enemy, 2 ALLY, 3 FRIEND**.

Ally and Friend were previously swapped here (3 written for Ally, 2 for
Friend). Confirmed two ways: the xEdit definition, and a census of Skyrim.esm
where 160 of 200 faction SELF-relations use 2 — a faction is Ally to itself,
never merely Friend.

**Why the swap was a live bug, not cosmetic.** Ally is the tier that makes
members ASSIST each other into combat (UESP Skyrim:Factions — reaction combines
with aggression and Assistance to decide who joins a fight). Oblivion's CG data
has `BladesCG → MythicDawnCG` at +100, which in TES4 is only a disposition
bonus ("these people like each other"); TES4 starts the intro fight with
`StartCombat`, not with the faction graph. Converted with the swap, that +100
became Ally, so the Emperor's guards assisted the Mythic Dawn and turned on the
player — who is a Neutral — instead of on the assassins.

**Reaction thresholds also tightened.** TES4 dispositions are a 0-100 SCALAR
shifting how much an actor likes a target, whereas TES5's Ally is a hard "fight
alongside them" contract. Reserving Ally for a faction's relation to ITSELF
(Oblivion's universal idiom for "we are one group", 147 of 660 relations) keeps
the assist graph vanilla-shaped; a positive relation to a DIFFERENT faction
means "friendly", which is Friend. This is what stops a converted plugin from
silently wiring bystanders into someone else's fight.

### FACT DATA flags are numbered differently in the two games

The old straight passthrough mis-landed every bit: TES4 bit 1 is "Evil" but
TES5 bit 1 is "Special Combat", and TES4 bit 2 (Special Combat) became TES5
bit 2 (unused). Bit meanings per xEdit `wbDefinitionsTES4`/`TES5`:

| Game | Bits |
|---|---|
| TES4 (U8) | 0 Hidden from Player, 1 Evil, 2 Special Combat |
| TES5 (U32) | 0 Hidden From NPC, 1 Special Combat, 6 Track Crime, 7-11/13/16 Ignore Crimes, 12 Crime Gold Use Defaults, 14 Vendor, 15 Can Be Owner |

Crime tracking was inverted too. The old code set the *Ignore* Crimes bits
(7-11, 13, 16) off the Evil flag — the exact opposite of the intent, telling
the engine to ignore murder, assault, stealing, trespass and pickpocket — and
never set Track Crime (bit 6) on anything. Oblivion has no per-faction
crime-tracking flag; see [crime factions](#crime-factions-derived).

XNAM Modifier is written as 0: it is 0 in 1,035 of 1,036 vanilla Skyrim
relations, because the TES4 disposition scalar has no meaning in TES5 and the
reaction enum carries the whole signal.

### <a id="crva-layout"></a>CRVA: the layout that made every crime test false

Layout per xEdit `wbDefinitionsTES5`, verified byte-for-byte against
Skyrim.esm's `WERoad12HorsemanFaction`
(`0101 E803 2800 0500 1900 0000 0000003F 6400 E803`):

    Arrest U8, Attack On Sight U8, Murder U16, Assault U16, Trespass U16,
    Pickpocket U16, Unknown U16, Steal Multiplier Float, Escape U16, Werewolf U16

The old `'<HHHHIfI'` packing was the same 20 bytes but misaligned every field:
the leading U16 swallowed both U8 booleans, so no converted crime faction ever
arrested, and murder/assault/trespass/pickpocket were all left at 0 — meaning
`GetCrimeGoldViolent()` and `GetCrimeGoldNonViolent()` returned 0 forever and
every converted crime-flag test was permanently false.

Amounts follow the vanilla census: all 14 real Skyrim crime factions use
exactly murder=1000, assault=40, trespass=5, pickpocket=25, escape=100. The
25x murder/assault gap is what lets converted scripts tell the two apart (see
`TES4_HasFactionMurder` in the script converter). Werewolf is left 0 — a
converted Oblivion plugin has no werewolf crime.

## <a id="trainers"></a>Trainers

A TES4 trainer advertises a skill and a maximum level in AIDT. Skyrim reads
both off the actor's CLAS, so a trainer needs a CLAS clone carrying its taught
skill — which is why `create_trainer_records` mints one per trainer and
`get_trainer_class_fid` maps the remapped NPC FormID to it.

## <a id="health-offset"></a>Health is written as an OFFSET, not a pool

Every playable/Dremora RACE that `RACE_MAP` targets ships Starting Health 50.0
(verified: all 11 target races in Skyrim.esm decode to 50.0/50.0/50.0), and the
engine derives an actor's max health as

    StartingHealth + HealthOffset + (Level - 1) * fNPCHealthLevelBonus

with `fNPCHealthLevelBonus` = 5.0 (Skyrim.esm GMST). `ACBS.HealthOffset` (int16
at byte 20) is the AUTHORED control; `DNAM.Health` is only a cache the engine
recomputes — vanilla proves it is not a function of the record at all (52 groups
of NPCs with identical race/class/level/offset carry different DNAM.Health, e.g.
55 / 51 / 0 / 20971), matching UESP's "otherwise seems to be random".

TES4 `DATA.Health` is the actor's FINAL hit-point pool, already fully
calculated. So a faithful conversion pins the engine's result to that exact
number by solving for the offset rather than copying the pool into the cache.

When the required offset overflows int16, Level is raised so its bonus absorbs
the surplus and the offset is re-solved for the remainder, capped at the U16
field limit. That division rounds UP: too few levels leaves the remainder above
the int16 cap and the final clamp would silently lose it.

DNAM offsets 36/38/40 are the engine's calculated Health/Magicka/Stamina cache,
not authored stats. The TES4 totals are written there so the cache agrees with
what the engine computes from the offsets (it recomputes on load regardless).
Magicka is Oblivion's SpellPoints; stamina is Fatigue.

## <a id="morrowind-health-is-absolute"></a>Morrowind health is absolute, so its level term is dropped

**Code:** `tes5_import/record_types/npc_morrowind.py`

The offset formula above solves `pool - base - (Level-1)*5` because TES4 derives
an actor's pool from its level. TES3 does not. OpenMW's `MWClass::Npc::ensureCustomData`
(`apps/openmw/mwclass/npc.cpp`) takes the two NPDT layouts apart:

| NPDT | branch | health |
|---|---|---|
| 52-byte (authored) | `setHealth(mNpdt.mHealth)` | used verbatim |
| 12-byte (autocalc) | `autoCalculateAttributes` | `floor(0.5*(Str+End)) + multiplier*(Level-1)` |

An authored TES3 NPC's health is the whole final pool and carries no level term
at all — the level term exists only in the autocalc branch, which computes the
pool the exporter then writes as `DATA.Health`. Either way the exported number
is already final, so subtracting a second, Skyrim-shaped level bonus from it
double-counts.

The damage is proportional to level. High Bishop Derminus (Arktwend intro,
health 397, level 100) solved to `397 - 50 - 99*5 = -148`, i.e. −98 effective
health: dead on load, before the intro's force-greet. Across Arktwend,
652 of 2,053 NPCs converted to ≤ 0 health, city guards and quest actors
included (a further 50 are authored corpses, which stay dead).

Vanilla Skyrim corroborates that a large negative offset is not how the format
is used: of 5,118 NPC_ records, 395 carry a negative offset, all small and all
on actors meant to be weak or already dead (`CurweDead`, `VeezaraDead`,
`MS06Victim`, the summons). `MS13FrostbiteSpider` sits at level 1200 with offset
−90, which only makes sense if the level term is not being relied on to add
6,000 health back.

So a Morrowind-sourced NPC_ keeps the race-base subtraction and passes its level
through untouched. `is_morrowind_npc` gates this on the exporter's authored
`MorrowindRace` key, which no TES4 export emits, leaving Oblivion conversion
byte-identical.

## <a id="hair-color"></a>Hair color: a generated CLFM per authored RGB

Oblivion authors a FREE RGB per NPC (2,482 actors, 571 distinct colors spanning
the whole cube), while Skyrim's HCLF is a FormID into CLFM and vanilla ships
only 15 swatches, all dark and desaturated. Snapping to the nearest vanilla
swatch loses the authored color badly — measured over Oblivion.esm, mean RGB
error 26.9, max 274.5.

So the authored color gets its own generated CLFM, and the vanilla table is
only the fallback for when no writer is available.

## <a id="required-nam-subrecords"></a>NAM5/NAM6/NAM7/NAM8 are all required

All four are `SetRequired` in the TES5 NPC_ definition and present on every one
of the 5,118 vanilla NPC_ records. NAM5 and NAM8 were missing entirely, and
NAM8 "Sound Level" is what lets the engine voice an actor at all — without it
an NPC is silent.

NAM6/NAM7 are Height/Weight, which TES4 keeps on RACE rather than the NPC, so
neutral 1.0 defaults are written and the race's own scale applies.

## <a id="hdpt-valid-races"></a>Head parts: RNAM decides who can see the hair

Skyrim gates which races may wear a head part on the RNAM Valid Races FLST, and
getting it wrong makes the hair INVISIBLE in the race menu for every race not
on the list.

`000A8023` is `HeadPartsHumansandVampires` — **humans only**, despite the name
this constant used to carry. Pointing every converted hair at it is why only
Nords and the other human races saw the new hairstyles while
Argonian/Khajiit/Orc/Elf saw none of theirs. The FLST names are read out of
Skyrim.esm:

| FLST | Meaning |
|---|---|
| 000A8023 | HeadPartsHumansandVampires |
| 000A8024 | HeadPartsElvesandVampires |
| 000A8032 | HeadPartsOrcandVampire |
| 000A8039 | HeadPartsArgonianandVampire |
| 000A8036 | HeadPartsKhajiitandVampire |
| 000A803B | HeadPartsRedguardandVampire |
| 000A8027 | HeadPartsDremora |
| 000A803F | HeadPartsAllRacesMinusBeast (19 races) |

Which list an Oblivion hair belongs on is matched on its EditorID. Oblivion
names every hair for the race it was authored for, and the mesh filename agrees
with the EditorID on all 57 records (checked), so this is the plugin's own
statement rather than a guess. Order matters: `DarkElf` and `HighElf`/`WoodElf`
must be tested before the bare `Elf` substring.

Vanilla routes its own hair exactly this way — censused over Skyrim.esm's hair
HDPTs: every Khajiit hair uses 000A8036 (x21), every Orc 000A8032 (x43), every
Elf 000A8024 (x36), plus Argonian 000A8039 and Dremora 000A8027.

### NAM0 races-tri does not apply to hair

`HDPT.NAM0` Part Type is 0 Race Morph, 1 Tri, 2 Chargen Morph. All 123 vanilla
hair HDPTs that carry a part use NAM0=1. A NAM0=0 races tri was tried on
converted hair and **the engine does not apply it to type-3 (Hair) parts** —
vanilla only ships one on heads (type 1) and beards (type 4), and in game the
hair rendered unmorphed.

Per-race conformance is instead BAKED: one mesh per race GROUP, gated by the
RNAM race lists, which is vanilla hair's own architecture. Generic hair (no
race in its EditorID) is emitted once per group, because the in-game head is
base mesh + the wearer race's races-tri morph and the scalp measurements split
cleanly (`head_fit.GROUP_MORPHS`): all five human races plus Dremora wear the
BASE scalp (morphs <= 0.15 there), the three elf races share one shape (2.6 off
base), Orc its own (1.5). The human mesh serves two HDPTs — the humans+vampires
list and the one-race Dremora list, which share that base scalp.

## <a id="package-order"></a>AI packages keep TES4 order

Both engines run the first package whose conditions pass, so the ORDER is the
behaviour. Quest packages are excluded from the PKID list: they reach the actor
through a QUST reference alias (ALPC), which is what lets them outrank this
standing schedule.

## <a id="creature-sound-channels"></a>Creature sound: only Hit rides the record

Both games use the SAME enum for CREA sound types 0-9 (xEdit
`wbSoundTypeSounds` is one shared struct), but the census of all 5,118 vanilla
Skyrim NPC_ records is the contract for which slots the TES5 ENGINE actually
reads from the record: **31 Hit, 4 Attack, 1 Left Foot, and ZERO of everything
else**. So only Hit rides the record; the rest travel the channels vanilla uses:

| TES4 slot | Channel |
|---|---|
| 0-3 feet | ARMA.SNDD footstep chain (`creature_footsteps`) + FootFront/FootBack animation events |
| 4 Idle / 5 Aware | single-play vocal states entered via ActionIdle/ActionIdleWarn IDLE records (`hkx_behavior` vocal states + `creature_idles`) |
| 6 Attack | SoundPlay annotation at the swing frame of each attack clip |
| 8 Death | annotation on the death clip when one exists |

Idle and Aware must NEVER be per-loop clip annotations or chance-100 record
slots: both made the creature vocalize non-stop, continuing after death.
Writing the Death slot vanilla never writes risks untested engine paths in the
kill flow. Attack is annotation-driven even though vanilla has 4 record entries
— writing both channels would double the bark.

CSDC is the AUTHORED TES4 play-chance, not a hardcoded 100. CSDI is written as
the TES4 SOUN FormID and patched to the real SNDR after Phase 3
(`patch_actor_sounds`): the descriptor does not exist yet, and pre-allocating
its id would shift every other generated FormID — which is what broke the
Slot44 patch and left NPCs unarmoured.

A creature with no own sounds falls back to CSCR inheritance, exactly as both
games do (817 of 909 Oblivion CREA and 725 of Skyrim's NPC_ records inherit
rather than define).

### An OVERRIDE's CSDI is already resolved

An override of a master's actor takes its bytes from the master's
already-converted record, so the CSDI already holds a real SNDR **in the
master's id space**. `sndr_map` is keyed on the low 24 bits only, so masking
such an id looks it up in the wrong space, finds nothing, and the whole
CSDT/CSDI/CSDC group is dropped — which silently stripped the sound block from
all six creature-derived NPC_ overrides in Knights.esp (CreatureWolf's
`CSDI 016D9202` is `TES4_NPCWolfInjured_SNDR`).

A CSDI whose index byte names a MASTER is therefore left alone.

### CSCR chains are flattened

TES4 lets inheritance CHAIN (rat variant → base rat → …); vanilla Skyrim CSCR
always points ONE hop to an actor with a direct array, and the sound-commit
regression window is exactly when non-vanilla record shapes entered the kill
path. So the chain is resolved at convert time and the sounds written directly,
after the CSDI→SNDR patch so the inlined bytes carry real descriptor ids.

## <a id="creature-scale"></a>Creature scale rides NAM6, not the RACE

TES4 sizes a creature with a PER-RECORD BNAM base scale; only humanoids take
their height from the RACE (Male/Female Height in RACE DATA). The creature
converter used the NPC_ default of 1.0, so every creature shipped at 1.0x no
matter what it was authored at: Anequina's bull elephant (BNAM 3.5) rendered a
third of its size and Nehrim's chickens (0.4) two and a half times theirs.
**754 of the 1,903 CREA records** across Oblivion/Nehrim/Anequina carry a
non-1.0 BNAM.

NAM6 is the direct equivalent — both engines multiply the base height by the
placed ref's XSCL, so the per-ref scale (already preserved by `convert_ACHR`)
keeps layering exactly as in TES4. Vanilla uses the field the same way, up to
3.3 on a giant and down to 0.6.

The scale must live on the RECORD, not the generated creature RACE: one race is
shared by every CREA with the same mesh folder, so a race-level height would
collapse the elephant bull/cow/calf to a single size.

## <a id="creature-stat-offsets"></a>Creature stats ride per-record offsets

A creature's generated RACE is SHARED across every CREA with the same mesh
folder, so it carries only a flat base and the per-record ACBS offsets carry
the creature's whole TES4 pool (`creature_health_offset`).

Magicka is the same split: the shared race's starting magicka is 0, so the
actor's whole TES4 SpellPoints pool rides in `ACBS.MagickaOffset`. Without it a
spell-knowing creature has 0 magicka, cannot pay any cast cost, and never casts
— vanilla's atronach carries the same split (race base + MagickaOffset 50).

TES4 flag 0x80 "PC Level Offset" makes Level an additive offset from the
player's; TES5 reuses the bit as "PC Level Mult", where Level is a fixed-point
multiplier (1000 = 1.0x). A raw TES4 offset (0..5) read as a multiplier is
0.000x..0.005x, which the CK clamps to the 0.10 minimum, so a PC-levelled actor
defaults to 1.0x instead.

## <a id="crea-vtck-always"></a>A creature's VTCK is ALWAYS emitted

Even when the humanoid chain yields nothing — the normal case for a creature,
since no TES4 creature race is in `VOICE_TYPE_MAP`. Skipping it broke two
things:

* Vanilla never ships an actor without one: all 3,887 Skyrim.esm NPC_ records
  carry a non-zero VTCK.
* `creature_races.patch_creature_voices` rewrites this slot in the packed bytes
  once the creature VTYPs exist, and it can only patch a subrecord that is
  already THERE — it finds VTCK or skips the record.

Omitting it left all 442 converted creatures with no voice type at all, so
every creature sound channel was dead no matter how correct the descriptors,
triggers and audio files were.

## <a id="crea-spells"></a>Creatures carry spells through SPLO

Exactly as NPCs do — the stunted scamp's fireball, a summoner's summon, an
atronach's touch attack. `convert_CREA` simply never emitted them, so all 600
of the 914 Oblivion CREA that know a spell shipped with none and could not cast
whatever the behavior graph offered them.

Order is RNAM → SPCT → SPLO[] → COCT → CNTO, verified against both the xEdit
TES5 definition (`wbDefinitionsTES5.pas`) and a real Skyrim.esm dump.

## <a id="creature-class-and-package"></a>A creature needs a CLASS and a PACKAGE

**CNAM.** 5,118 of 5,118 vanilla NPC_ carry a class; a converted creature never
did. Under ACBS AutoCalc the engine derives skills and attribute growth from
the CLASS weights, so a classless actor is a skill-less one — and the class is
where a vanilla caster's magic profile lives: `EncAtronachFlame` uses
`EncClassBanditWizard` (Magicka weight 3, Destruction 3), `EncHagraven` its own
mage class, while wolf/sabrecat/skeever/spriggan/wisp share
`EncClassAnimalPredator` (Magicka weight 0). A creature that knows an offensive
spell gets the atronach's class; everything else the predators'.

**PKID.** TES4 PACK records are skipped (`SKIP_TYPES`), so a raw pass-through
gave creatures NO working packages — the AI layer made no decisions and the
engine never sent the graph movement/attack events. That was the stuck-in-idle
root cause. Every vanilla creature carries exactly ONE package,
`DefaultMasterPackageCreature`, so converted creatures get the same hookup.

**ZNAM.** CSTY is skipped, so the vanilla styles stand in, chosen off TES4
`DATA.Type` (0 Creature, 1 Daedra, 2 Undead, 3 Humanoid, 4 Horse).

**NAM8** matters here specifically: every vanilla actor carrying a CSDT sound
array (31 of 31) also carries NAM8, so a creature without it has sound
descriptors the engine never voices.

## <a id="ghost-dissolve"></a>TES4_GhostDissolve: death animations that hide the body

A creature whose AUTHORED death animation dissolves it — ghosts, wraiths, whose
`death.kf` hides `SkinAttachment` via NiVisController rather than dropping the
body — carries `TES4_GhostDissolve` alongside any converted TES4 script.

The script reproduces the effect with Skyrim's native ash pile. Without it the
corpse stands upright in mid-air forever, because those visibility channels
cannot survive into a Havok clip.

`_crea_vmad` takes an already-packed VMAD, so it unwraps, extends and repacks:
`build_vmad_object_script` writes a fixed "1 attached script" count, which
`append_vmad_object_script` bumps.

## <a id="voice-resolution"></a>Voice type resolution

`_npc_voice_map` maps a (remapped) NPC/CREA FormID to a VTYP FormID, built by
`build_npc_to_vtyp_map` from the VNAM-resolved voice the actor actually used in
Oblivion. `import_main` sets it in Phase 0 so VTCK matches the
`GetIsVoiceType` gates and audio folders the dialogue pass emits.

The (race, gender) computation is only the fallback for actors the map has no
entry for.
