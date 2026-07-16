# TES4 → TES5 Record Mapping Reference

Linked from [CLAUDE.md](../CLAUDE.md). Reference tables for record type mapping,
structural requirements, and known problem records. For narrative conversion
notes (NIF/mesh/collision/particle/creature), see
[nif_conversion_notes.md](nif_conversion_notes.md). For dialogue/quest specifics,
see the `oblivion-to-skyrim-dialog` skill.

## Record Format Differences: TES4 vs TES5

### Critical Structural Requirements for TES5

1. **OBND (Object Bounds)** — 12-byte struct required on nearly all item/object records (ACTI, ALCH, AMMO, ARMO, BOOK, CONT, DOOR, ENCH, FLOR, FURN, INGR, KEYM, LIGH, MISC, NPC_, SLGM, SPEL, SCRL, STAT, TREE, WEAP, and more). TES4 has no OBND. **Missing OBND will cause the engine to reject records.**

2. **File Header (TES4 record)** — HEDR version must be 1.7 (Skyrim LE) or 1.71 (SSE), not 1.0 (Oblivion). Form version = 44 for SSE.

3. **Form Version** — Each TES5 record header has a form version field. SSE = 44, LE = 43. Some record structures differ by form version.

4. **No SCRI** — TES4 uses SCRI (FormID → SCPT record). TES5 uses VMAD (Virtual Machine Adapter) for Papyrus. VMAD cannot be auto-generated from TES4 scripts.

5. **Keyword System (KSIZ/KWDA)** — TES5 records extensively use keywords. ARMO, WEAP, NPC_, SPEL, ALCH, INGR, RACE, and many others have keyword arrays. Many game systems depend on specific keywords.

6. **Localized Strings** — When the TES4 header has the Localized flag (0x80), FULL/DESC/etc. become LString indices. Non-localized plugins use inline strings.

### Record Type Mapping

| TES4 Type | TES5 Type | Notes |
|-----------|-----------|-------|
| ACTI | ACTI | Add OBND. Needs VMAD instead of SCRI. |
| ALCH | ALCH | Add OBND. ENIT restructured. Effects need MGEF FormID resolution. |
| AMMO | AMMO | Add OBND. SSE DATA (20B): Projectile FID + Flags(U32) + Damage(f) + Value + Weight(f). **Flags bit 0x04 = Non-Bolt — must be set or the game classifies the ammo as a crossbow bolt.** A companion PROJ is synthesised per arrow (TES4 has none): PROJ DATA Type is a **bit value** (Arrow=0x40, not enum 7 — wrong type = no working projectile); offsets per wbDefinitionsTES5 {72}=CollisionRadius 0.5, {76}=Lifetime 0, {80}=RelaunchInterval 0.25; Sound=WPNBowProjectileSD (0x0003F2B4). Values matched to vanilla ArrowIronProjectile (0x0003BE11). |
| ANIO | ANIO | Minor changes. |
| APPA | MISC | No apparatus in TES5. Convert to MISC. |
| ARMO | ARMO | **Major changes**: BMDT(4B)→BOD2(8B), 16→32 biped slots. Armor models move to ARMA records. ARMO references ARMA via MODL array. No direct mesh on ARMO. Add OBND, RNAM (race), keywords. |
| BOOK | BOOK | Add OBND. DATA restructured. Skill teaching uses TES5 skill enum. TES4 Scroll flag (0x01) → DATA.Type 255 (Note/Scroll). **INAM (Inventory Art STAT) is mandatory — BookMenu null-derefs (in-game crash) when a book without INAM is read.** But pointing INAM at a vanilla stand-in shows the default Skyrim cover, so we synthesise a per-book `InvArt_<edid>` STAT wrapping the book's own converted mesh. CNAM (Description string) present-but-empty like vanilla. Book text (DESC) HTML: Skyrim Scaleform only knows **named fonts** (`<font face='$SkyrimBooks'>`, `$HandwrittenFont`, `$DaedricFont`) — Oblivion's numeric `<font face=N>` resolves to no font and renders NO text (map 1/2/3→$SkyrimBooks, 4→$DaedricFont, 5→$HandwrittenFont); IMG src needs `img://textures/tes4/menus/<path>` (Oblivion srcs are relative to Textures\Menus\). |
| BSGN | *(none)* | Birthsigns don't exist. Spells should go to Race records or Standing Stones. Exported as BSGN_SPELLS for reference. |
| CELL | CELL | DATA: U8→U16 flags. Lighting (XCLL) expanded. New: LTMP (lighting template), XLCN (location), XCAS (acoustic space), XCMO (music type). |
| CLAS | CLAS | Simplified in TES5. No attributes/skills. Only Flags, Teaches, MaxTraining. |
| CLMT | CLMT | Minor changes. |
| CLOT | ARMO | Clothing → ARMO with ArmorType=Clothing in BOD2. Same ARMA requirement. |
| CONT | CONT | Add OBND. Minor changes. |
| CREA | NPC_ | **No CREA in TES5**. Must convert to NPC_. TES4 creature stats (attributes, skills) must map to TES5 DNAM. Needs race assignment. |
| CSTY | CSTY | **Completely restructured**: TES4 CSTD/CSAD → TES5 CSGD/CSMD/CSME. |
| DIAL | DIAL | Categories restructured. TES5 adds DLBR (Dialog Branch) and DLVW (Dialog View). |
| DOOR | DOOR | Add OBND. Minor changes. |
| EFSH | EFSH | DATA structure differs. |
| ENCH | ENCH | **ENIT completely restructured**: 16B→36B. Type enum changes (0-3 → 6/0xC). Add OBND. New fields: Cast Type, Target Type, Charge Time, Base Enchantment. |
| EYES | EYES | Minor changes. |
| FACT | FACT | DATA flags differ slightly. Crime data: CNAM→CRVA. |
| FLOR | FLOR | Add OBND. Minor changes. |
| FURN | FURN | Add OBND. Furniture markers restructured (FNMK was a U32 bitmask; TES5 uses entry-based system with FNPR). |
| GLOB | GLOB | Identical. |
| GMST | GMST | Many settings differ but format is same. Some TES4 GMSTs don't exist in TES5. |
| GRAS | GRAS | Add OBND. Minor changes. |
| HAIR | HDPT | Hair→Head Part. TES5 HDPT has Type=3 (Hair), flags, extra parts list, TNAM (texture set). |
| IDLE | IDLE | Add conditions changes. |
| INFO | INFO | **Major restructuring**: TES5 INFO uses VMAD fragments, ENAM, different response structure (TRDA vs TRDT), conditions restructured. |
| INGR | INGR | Add OBND. ENIT restructured. Effects need MGEF resolution. TES5 ingredients have exactly 4 effects. |
| KEYM | KEYM | Add OBND. Minor changes. |
| LAND | LAND | Compatible heightmap structure. Texture layers may need LTEX FormID remapping. |
| LIGH | LIGH | Add OBND. Minor changes. |
| LSCR | LSCR | Add OBND. TES5 uses NNAM (loading screen text) instead of ICON+DESC. |
| LTEX | LTEX | **Restructured**: TES4 uses ICON (texture path) + HNAM + SNAM + Grasses. TES5 uses TNAM (→TXST record) + HNAM(→MATT) + SNAM + Grasses. Needs TXST creation. |
| LVLC | LVLN | Leveled Creature → Leveled NPC. Same entry format (LVLO). |
| LVLI | LVLI | Same entry format. Minor flag differences. |
| LVSP | LVSP | Same entry format. |
| MGEF | MGEF | **Major restructuring**: TES4 uses 4-char codes (OBME), FormID-based effects (EFID/EFIT). TES5 MGEF has completely different DATA struct. Magic school → skill enum. |
| MISC | MISC | Add OBND. Minor changes. |
| NPC_ | NPC_ | **Massive restructuring**: ACBS different fields. DATA(33B)→empty marker. Skills/stats→DNAM(52+B). Hair→PNAM(HDPT array). Voice→VTCK(VTYP). Outfits→DOFT/SOFT(OTFT). Perks new. Template system new. Add OBND, keywords. |
| PACK | PACK | **Completely incompatible**: TES4 type-based (Find/Follow/Escort/Eat/Sleep). TES5 procedure-tree based. Must create skeleton records. |
| PGRD | *(skip)* | Path grids replaced by NavMesh (NAVM). Cannot auto-convert. |
| QUST | QUST | **Major restructuring**: DATA(2B)→DNAM(12B). Stages similar but restructured. Objectives are new. Alias system entirely new. VMAD fragments replace SCRI. |
| RACE | RACE | **Massive restructuring**: DATA completely different (30B→128+B). Many new subsystems: behavior graphs, movement types, tints, morph data. HAIR→HDPT, hair color→CLFM. Voice→VTYP records. |
| REFR | REFR | More subrecords in TES5: XLKR (linked refs), activate parents, locations, emittance. |
| ACHR | ACHR | Similar expansion. TES4 ACRE (placed creature) → ACHR. |
| ACRE | ACHR | Placed creature → Placed NPC (ACHR). |
| REGN | REGN | Minor changes. |
| ROAD | *(skip)* | Roads replaced by NavMesh. |
| SBSP | STAT | Subspace has no equivalent. Export as STAT. |
| SCPT | *(skip)* | Scripts must be rewritten in Papyrus. Source exported for reference. |
| SGST | SCRL | Sigil Stone → Scroll (closest equivalent). |
| SKIL | *(skip)* | Skills hardcoded in TES5. Exported for reference. |
| SLGM | SLGM | Add OBND. Minor changes. |
| SOUN | SOUN + SNDR | TES5 splits sound into SOUN (marker) + SNDR (Sound Descriptor with actual data). |
| SPEL | SPEL | **SPIT restructured**: 16B→36B. New fields: Cast Type, Target Type, Cast Duration, Range, Half-cost Perk. Add OBND, keywords. |
| STAT | STAT | Add OBND. Minor changes. |
| TREE | TREE | CNAM restructured. |
| WATR | WATR | DATA→DNAM. Completely different water properties structure. |
| WEAP | WEAP | **DATA restructured**: 32B→10B. Type moves to DNAM. Massive DNAM struct (~100B). CRDT (critical data) new. Add OBND, keywords. |
| WRLD | WRLD | New fields: XLCN, fixed dimensions, various flags. |
| WTHR | WTHR | Cloud system redesigned (layer-based). HDR/lighting data restructured. |

### New TES5 Record Types (May Need Creation)

| Type | Purpose | When Needed |
|------|---------|-------------|
| ARMA | Armor Addon | **Every ARMO record** needs at least one ARMA. Holds the actual mesh models. |
| KYWD | Keyword | Many records need keywords for game systems to work. |
| TXST | Texture Set | LTEX records need TXST instead of ICON paths. NPC_ head textures. |
| FLST | FormID List | Package override lists on NPC_. Quest objective lists. |
| OTFT | Outfit | NPC_ default outfits. |
| VTYP | Voice Type | NPC_ voice assignment. |
| CLFM | Color | Hair color (replaces inline HCLR). |
| LGTM | Lighting Template | Interior CELL lighting. |
| MUSC | Music Type | CELL music (was U8 enum). |
| SNDR | Sound Descriptor | Sound data (SOUN is just a marker in TES5). |
| MATT | Material Type | Landscape material (was HNAM enum). |

## Known Problems and Skipped Records

### Records That Cannot Be Auto-Converted
- **PGRD** (Path Grid) → Must be rebuilt as NAVM (NavMesh) in Creation Kit
- **ROAD** → Replaced by NavMesh system
- **SCPT** (Script) → Papyrus rewrite required (source exported for reference)
- **SKIL** (Skill) → Hardcoded in TES5, no record equivalent
- **BSGN** (Birthsign) → No record type. Spells should go to Race or Standing Stone

### Records With Major Conversion Issues
- **PACK** — TES5 package system is completely different (procedural tree). Only skeleton records can be created.
  **Substitution (2026-07-09)**: PKID refs to skipped PACKs must NOT be passed through (they dangle → the
  actor has no working AI packages). NOTE: this was necessary but did NOT resolve the creature stuck-in-idle
  bug — the vanilla-asset A/B dog moved even with dangling packages. `tes5_import/packages.py`
  substitutes vanilla generics instead: creatures always get PKID `DefaultMasterPackageCreature` (0010F2A5) +
  DPLT `DefaultMasterPackageListCreature` (0010F2A6) — exactly what every vanilla wolf/dog/skeever carries;
  humanoids get one `DefaultSandboxCurrentLocation1024` (000BFB6B) standing in for wander/eat/sleep-type TES4
  packages (ref-targeted types — follow/escort/ambush — are dropped) + DPLT `DefaultMasterPackageList`
  (00021E81). Skipped CSTY refs are likewise replaced: ZNAM = `csWolf` (00057BE8) for animal/horse CREA,
  `DefaultCombatstyle` (0000003D) otherwise. TES4 aggression >5 now maps to TES5 tier 1 (the old >=40
  threshold left e.g. dogs at Unaggressive, which never initiates combat).
- **QUST** — Alias system, objectives, and VMAD fragments are all new. Only basic stage data can be transferred.
- **INFO** — Dialog response structure changed significantly. VMAD fragments replace result scripts.
- **NPC_/CREA** — Attribute system removed, skill system changed, many new subsystems (templates, outfits, perks, keywords).
- **Outfit split (`tes5_import/outfits.py`)** — TES4's single CNTO inventory (engine picks what to wear at
  spawn) → TES5 DOFT/OTFT (worn) + CNTO (carried), disjoint. Per-biped-slot conflict resolution keeps
  one winner per slot (armor > clothing > value). **ChanceNone contract:** only a *guaranteed* winner
  (plain ARMO/CLOT, or an LVLI with `LVLD.ChanceNone==0` down to slot-filling leaves) may EVICT a
  lower-priority item from its slot. A probabilistic list (e.g. `LL0NPCArmorLightGreaves25`, ChanceNone
  75) must NOT evict a guaranteed clothing base under it (`LL0NPCClothingPantsLower`, ChanceNone 0) —
  Skyrim resolves the outfit once and has no equivalent of Oblivion's per-spawn re-scoring, so evicting
  the guaranteed pants left ~75% of bandits bare-legged. Keep both; engine wears greaves when rolled,
  pants otherwise. The `NN` in Bethesda's list names (`...Greaves25`, `...Cuirass100`) is the equip
  probability. Trace any actor with `python -m tools.trace_outfit export/Oblivion.esm <EditorID>`.
- **RACE** — Almost entirely restructured. Only basic data (height/weight/skill boosts/spells) can transfer.
- **MGEF** — 4-char code system vs FormID system. Flag mapping is complex.
- **ENCH/SPEL** — ENIT/SPIT completely restructured. Effects need MGEF FormID resolution.
- **ARMO/CLOT** — Missing ARMA records means armor won't render in-game.

### Common Causes of ESP Failing to Load in Skyrim Engine
1. **Missing OBND** on records that require it (most item/object types)
2. **Wrong HEDR version** (must be 1.7/1.71, not 1.0)
3. **Wrong form version** (must be 43/44, not 0)
4. **Malformed record structures** — Wrong subrecord sizes (e.g., ENIT 16B instead of 36B)
5. **Invalid FormID references** — Pointing to non-existent records
6. **Missing required subrecords** — Some records crash without certain subrecords
7. **Wrong DATA sizes** — NPC_ DATA must be empty (0B) in TES5, not 33B
8. **CELL DATA flag size** — Must be U16, not U8
9. **Biped slots** — BOD2 (8B) required instead of BMDT (4B)
10. **ARMO without ARMA references** — Engine expects armor models via ARMA indirection

### Cross-File Reference System (Dependent Plugins)
When importing a dependent plugin (e.g., Knights.esp that depends on Oblivion.esm), the pipeline must:
1. **Load converted masters** — convert.ps1 adds converted Oblivion.esm to activePlugins during Knights.esp import
2. **Search all loaded files** — `FindMappedRecord` iterates `FileByIndex(0..FileCount-1)` to find parent records (CELLs, WRLDs, DIALs) in master files
3. **Create overrides for cross-file parents** — `CreateChildRecord` calls `wbCopyElementToFile(parentRec, TargetPlugin, False, False)` when parent is in a master file, which creates an override in the target plugin and automatically adds the master dependency
4. **Register masters in relink phase** — All loaded files are registered via `AddMasterIfMissing` so cross-file FormID references are valid when saved

**Key point**: `RecordByFormID(singleFile, formID, True)` only searches one file. Must loop `FileByIndex` to find records in any loaded file.

### NPC_ DNAM Skill Path Names
TES5 NPC_ DNAM stores skills as arrays. The correct xEdit paths are:
- `DNAM\Skill Values\OneHanded` (not `DNAM\One-Handed`)
- `DNAM\Skill Values\TwoHanded`, `Marksman`, `Block`, `Smithing`, `HeavyArmor`, `LightArmor`, `Pickpocket`, `Lockpicking`, `Sneak`, `Alchemy`, `Speechcraft`, `Alteration`, `Conjuration`, `Destruction`, `Illusion`, `Restoration`, `Enchanting`
- Plus `DNAM\Health`, `DNAM\Magicka`, `DNAM\Stamina` (U16 each)

## Actor Value / Skill Mapping

| TES4 Skill (Index) | TES5 Skill (Index) | Notes |
|---------------------|---------------------|-------|
| Armorer (12) | Smithing (10) | |
| Athletics (13) | *(none)* | Removed in TES5 |
| Blade (14) | One-Handed (6) | |
| Block (15) | Block (9) | |
| Blunt (16) | One-Handed (6) | Merged with Blade |
| Hand to Hand (17) | One-Handed (6) | Merged with Blade |
| Heavy Armor (18) | Heavy Armor (11) | |
| Alchemy (19) | Alchemy (16) | |
| Alteration (20) | Alteration (18) | |
| Conjuration (21) | Conjuration (19) | |
| Destruction (22) | Destruction (20) | |
| Illusion (23) | Illusion (21) | |
| Mysticism (24) | Illusion (21) | Merged with Illusion |
| Restoration (25) | Restoration (22) | |
| Acrobatics (26) | *(none)* | Removed in TES5 |
| Light Armor (27) | Light Armor (12) | |
| Marksman (28) | Archery (8) | |
| Mercantile (29) | Pickpocket (13) | Approximate |
| Security (30) | Lockpicking (14) | |
| Sneak (31) | Sneak (15) | |
| Speechcraft (32) | Speech (17) | |

TES4 Attributes (Strength, Intelligence, etc.) have no TES5 equivalent. Health/Magicka/Stamina are derived from TES4 attributes for NPC conversion.

## Weapon Type Mapping

| TES4 Type | TES5 Animation Type | Notes |
|-----------|---------------------|-------|
| 0 (Blade 1H) | 1 (Sword) | |
| 1 (Blade 2H) | 5 (Greatsword) | |
| 2 (Blunt 1H) | 4 (Mace) | |
| 3 (Blunt 2H) | 6 (Battleaxe) | |
| 4 (Staff) | 8 (Staff) | |
| 5 (Bow) | 7 (Bow) | |

## Biped Slot Mapping

| TES4 Slot (Bit) | TES5 Slot (Bit) | Name |
|------------------|------------------|------|
| 0 (Head) | 0 (30-Head) | Full-face helm: also gets 31+41+42+43 extras |
| 1 (Hair) | 1 (31-Hair) | Helm: also gets 41-LongHair + 42-Circlet extras |
| 2 (Upper Body) | 2 (32-Body) | |
| 3 (Lower Body) | 2 (32-Body) | Merged with upper |
| 4 (Hand) | 3 (33-Hands) | |
| 5 (Foot) | 7 (37-Feet) | |
| 6 (Right Ring) | 6 (36-Ring) | |
| 7 (Left Ring) | 6 (36-Ring) | Merged |
| 8 (Amulet) | 5 (35-Amulet) | |
| 13 (Shield) | 9 (39-Shield) | |
| 15 (Tail) | 13 (43-Tail) | |

**Helmet hair hiding (slot 41):** Skyrim's slot 31 alone does NOT fully hide
hair — the engine swaps the hair headpart for its "hairline" extra part, whose
meshes carry dismember partitions [141, 131] (verified: `hairline01.nif` etc.).
Vanilla helmets are modelled big enough to enclose the hairline; tighter
Oblivion helms are not, so the hairline pokes through the shell (top hidden,
sides visible). Converted headgear therefore also covers slot 41 (LongHair) on
both ARMO and ARMA (`BIPED_SLOT_EXTRA` / `ARMA_BODY_COVERAGE_EXTRA` in
`tes5_import/constants.py`), which suppresses the 141 partitions → all hair
fully hidden.

## Enchantment Type Mapping

| TES4 Type | TES5 Type | Notes |
|-----------|-----------|-------|
| 0 (Scroll) | 6 (Enchantment) | |
| 1 (Staff) | 12 (Staff Enchantment) | |
| 2 (Weapon) | 6 (Enchantment) | |
| 3 (Apparel) | 6 (Enchantment) | |

## Skyblivion Analysis — Conversion Best Practices

Analysis of ~140 Skyblivion/Skywind conversion scripts in `external/Skyblivion Conversion Edit Scripts/`. These findings are incorporated into our import script.

### Race Override System
Playable Oblivion races map directly to Skyrim equivalents by EditorID:
- Argonian→$00013740, Breton→$00013741, DarkElf→$00013742, HighElf→$00013743
- Imperial→$00013744, Khajiit→$00013745, Nord→$00013746, Orc→$00013747
- Redguard→$00013748, WoodElf→$00013749
- DarkSeducer, GoldenSaint, Sheogorath have no Skyrim equivalent (create new)
- ImportRACE() checks EditorID and stores a mapping to Skyrim's FormID instead of creating a new record

### NPC_ Conversion (from Skyblivion)
- **ACBS Flags**: Only keep compatible bits ($01+$02+$08+$10+$80+$4000). Force autocalc stats for creatures.
- **Level**: PC Level Mult formula = `1 + obLevel / 20`. CalcMin doubled, CalcMax doubled (default max=100).
- **ACBS Offsets**: Health=Endurance, Magicka=Intelligence, Stamina=Strength (from TES4 Attributes).
- **AI Thresholds**:
  - Aggression: 0-39→Unaggressive(0), 40-69→Aggressive(1), 70+→Very Aggressive(2)
  - Confidence: 0-29→Cowardly(0), 30-69→Average(2), 70+→Brave(3)
  - Responsibility: <30→No Crime(0) + Helps Allies, 30+→Any Crime(3) + Helps Nobody
  - Mood: always Neutral(4)
- **NPC_ Skills from Creatures**: TES4 CREA has aggregate skills (Combat/Magic/Stealth). Mapping:
  - OneHanded/Block/Smithing = Combat, TwoHanded = max(blunt,blade,h2h)
  - Destruction/Conjuration/Alteration/Illusion/Restoration = Magic
  - Marksman/Sneak/Lockpicking/Pickpocket = Stealth
  - HeavyArmor = max(HeavyArmor, Athletics), LightArmor = max(LightArmor, Acrobatics)
  - Alchemy = Alchemy, Speechcraft = Speechcraft, Enchanting = Intelligence/3

### ENCH/SPEL Conversion
- **ENCH Cast Type** varies by enchantment type: Scroll→4(Scroll), Staff/Weapon→2(Fire and Forget), Apparel→0(Constant Effect)
- **SPEL Cast Type**: Always Fire and Forget(2)
- **SPEL Flag Remapping**: $10→$80000 (No Absorb/Reflect), $20→$100000 (No Dual Cast Modifications), $40→$200000
- **Target Type**: Derived from first magic effect's EFIT\Type (exported as FirstEffect.Type)
- **ENCH Flags**: Only keep No Auto Calc ($08→$01)

### FACT Conversion
- **Evil flag** ($02) → all crime flags ($0080+$0100+$0200+$0400+$0800+$2000+$10000 = Assault/Murder/Trespass/Pickpocket/Steal/Werewolf/Attack on Sight)
- **Can Be Owner** ($8000) set on all factions
- **Relation Disposition → Combat Reaction**: ≤-50→Enemy(1), =100→Ally(3), ≥50→Friend(2), else→Neutral(0)

### ALCH Conversion
- **Food Detection**: Flag $02 (food) → set food flag + ITMPotionUse sound ($000CAF94) + VendorItemFood keyword
- **Poison Detection**: Name contains 'poison' → set poison flag ($20000) + ITMPoisonUse sound ($00106614) + VendorItemPoison keyword
- Needs OBND, standard potion sound otherwise

### Magic Effects (EFID/EFIT) — null EFID = inventory CTD (fixed 2026-07-10)
- **NEVER write EFID=00000000**: the game dereferences each effect's base MGEF
  when a menu builds the item card → instant CTD on opening inventory with the
  item (crash log: `(AlchemyItem*)` in RCX + `InventoryMenu`). Applies to ALCH,
  INGR, ENCH, SPEL, SCRL alike.
- `_pack_effects()` (tes5_import/record_types/equipment.py) drops effects whose
  TES4 code has no Skyrim mapping and guarantees ≥1 real effect (INGR: exactly 4)
  by padding with zero-magnitude AlchRestore* fillers.
- Attribute/skill-targeted codes (DRAT/DGAT/FOAT/REAT/ABAT/FOSK) resolve through
  the effect's ActorValue via `MGEF_AV_CODE_TO_SKYRIM` (skyrim_overrides.py):
  e.g. Drain Endurance → AlchDamageHealth, Fortify Personality → AlchFortifyBarter,
  Fortify Blade → AlchFortifyOneHanded. Flat `MGEF_CODE_TO_SKYRIM` is the fallback.
- **TES5 INGR ENIT is 8 bytes** (s32 value + u32 flags), NOT the 20-byte ALCH
  layout (xEdit wbDefinitionsTES5 INGR).

### CK-warning sweep learnings (fixed 2026-07-16)
- **AIMED magic needs a projectile effect**: an aimed ENCH/SPEL/SCRL whose
  effects all resolve to projectile-less Alch* MGEFs casts NOTHING in game
  (CK: "is AIMED but has no Magic Effects with Projectiles assigned", 369x).
  Skyrim ships no aimed variants of plain value modifiers, so
  `tes5_import/magic_effects.py` synthesizes a companion MGEF per (vanilla
  effect, TES4 code): clone of the vanilla 152-byte DATA (baked in
  `vanilla_mgef_data.py`, regen with `tools/gen_vanilla_mgef_table.py`),
  patched to CastType=FF(1)/Delivery=Aimed(2) + a projectile (spectral arrow
  for hostile, sunfire for beneficial), swapped in for the first effect.
  MGEF DATA offsets: archetype 0x40, AV 0x44, projectile 0x48, cast 0x50,
  delivery 0x54, counter-count 0x14 (zero it — clones carry no ESCE).
- **SPEL/SCRL SPIT CastType**: wbCastEnum 0=Constant, **1=Fire and Forget**,
  2=Concentration, 3=Scroll. `convert_SPEL` used to write 2 → every spell was
  a Concentration cast. Scrolls (SGST→SCRL) use 3 and need ETYP EitherHand
  (0x13F44) + effects (they had none).
- **TES4 negative inventory/leveled counts** mean merchant restock stock;
  Skyrim treats count<1 as "adds nothing" → normalize with abs() (CONT/NPC_
  CNTO and LVLO alike).
- **8-byte LVLO**: the Count+pad tail of TES4 LVLO is optional (xEdit
  wbStructExSK optional-from-element-3). 8 bytes = Level(2)+pad(2)+FormID(4),
  Count defaults 1. The exporter must emit these or leveled entries import
  as null FormIDs.
- **Footstep sets**: FSTArmorLightFootstepSet=0x21486,
  FSTBarefootFootstepSet=0x21468 (the old 0x24238/0x24237 don't exist in
  Skyrim.esm).
- **Generated RACE VTCK must fill both gender slots** (vanilla DogRace:
  CrDogVoice x2) or the CK logs missing-voice-type per race.
- **Top-level group order**: LCTN must come AFTER CELL/WRLD/QUST (vanilla:
  `... NAVI CELL WRLD DIAL QUST ... LCTN ... DLBR DLVW`) — the CK resolves
  LCEC worldspaces + MNAM markers when the LCTN group loads.
- **Door-linked locations may only claim INTERIOR cells** — a gate door leads
  OUT to an exterior, and a worldspace keeps every persistent ref in one
  dummy cell, so one exterior entry hands its location to every persistent
  ref in the worldspace (the "Ref is not in its persistence location" spam /
  CK hang).
- **PlayerRef 0x14 never remaps** (`_ENGINE_FIXED_FORMIDS` in text_reader) —
  but it is the ONLY such id: ~195 real Oblivion.esm records live below
  0x800 (Tamriel 0x3C, gold 0xF, Player NPC_ 0x7, marker STATs, DIALs) and
  must keep remapping.
- **Zero-INFO topics are never emitted** (~856 placeholder DIALs in
  Oblivion.esm → CK "Orphaned topic" each); TCLT choices into them are
  dropped too.
- Verify all of the above against a build with `tools/verify_ck_fixes.py`.

### CELL Conversion
- **Remove Oblivion Interior Flag**: Clear bit $08 from DATA flags on interior cells
- **Clear Hand Changed Flag**: Clear bit $40 from DATA flags
- **Fog Duplication**: TES4 has one fog color, TES5 has near+far → copy to both
- **Lighting Template**: Skyblivion assigns templates by music type (dungeon/public/default) — we don't do this yet

### WRLD Conversion
- **Clear Oblivion Flag**: Clear bit 2 from DATA
- **Move No LOD Water**: Bit $10 → bit $08
- **Add DNAM**: Default land height = -2048.0, water height = 0.0
- **Add NAMA**: Distant LOD multiplier = 1.0

### REFR Conversion
- **Lock Level Tiers**: 0-20→1(Novice), 21-40→25(Apprentice), 41-60→50(Adept), 61-80→75(Expert), 81+→100(Master)
- **Map Marker Types**: Camp→5, Cave→4, City→1, AyleidRuin→7, Fort→6, Landmark→11, Tavern→14, Settlement→3, DaedricShrine→34, OblivionGate→34

### LTEX Conversion
- **Create TXST**: Each LTEX needs a companion TXST record with diffuse texture path + derived normal map path (_n.dds suffix)
- **MATT Mapping** (Material Type → Skyrim MATT FormID):
  - Stone→$00012F34, Cloth→$00012F37, Dirt→$00012F38, Glass→$00012F39
  - Grass→$00012F3A, Metal→$00012F3B, Organic→$00012F3C, Skin→$00012F3D
  - Water→$00012F3E, Wood→$00012F3F, HeavyStone→$00012F40, HeavyMetal→$00012F41
  - HeavyWood→$00012F42, Chain→$00012F43, Snow→$00012F44

### SOUN Conversion
- **Create SNDR**: Each SOUN needs a companion SNDR (Sound Descriptor) with the actual sound file path linked via SDSC
- **Loop flag**: TES4 `SNDD.Flags` bit 4 (`0x10`) = "Is Looping". When set, write `LNAM = 0x00000800` (loop) in the SNDR record. `LNAM` is a 4-byte struct: byte[0]=Unknown, byte[1]=Looping enum (0x00=None, 0x08=Loop, 0x10=Envelope Fast, 0x20=Envelope Slow), byte[2]=Unknown, byte[3]=Rumble. `0x00000800` in little-endian = bytes [0x00, 0x08, 0x00, 0x00] = Loop. Default (`LNAM = 0`) = no loop / plays once. `0xFFFFFFFF` is INVALID and causes no sound to play.

### CLAS Conversion
- **Trainer classes**: Skyrim's training menu reads skill/cap from CLAS DATA (Teaches S8 + MaxTrainingLevel U8 at offset 4), but Oblivion trainers store them per-NPC in AIDT (92/114 vanilla trainers disagree with their class). Phase 0c `create_trainer_records` clones each trainer NPC's class with the AIDT values and repoints CNAM; the NPC also joins `TES4JobTrainerFaction`, which gates the generated Training dialogue topic. Vendor barter gold becomes carried Gold001 (no TES5 field). See [dialogue_conversion_notes.md](dialogue_conversion_notes.md) (Barter/Training services).
- **VendorItem keywords (2026-07-10)**: Skyrim vendors only buy/sell items whose keywords appear in their faction's VEND formlist — converted items with NO keywords are invisible in the barter menu ("vendor missing nearly their entire inventory"). Every sellable converter now emits KSIZ/KWDA from `VENDOR_KYWD` (record_types/common.py): WEAP→Weapon (type 4→Staff), AMMO→Arrow, ARMO/CLOT→Armor/Clothing (TES4 biped bits 6/7/8 ring/amulet→Jewelry), BOOK→Book (flag 0x01→Scroll), ALCH→Potion/Poison/Food, INGR→Ingredient, SLGM→SoulGem, SGST→Scroll, APPA/MISC→Clutter, KEYM→Key. The service-bit→FLST table in actors.py must stay in sync (Weapons list includes Arrow; Books includes Scroll; Ingredients includes Food; Misc includes Clutter).
- **Skill Weight Algorithm** (from Skyblivion):
  1. Start with all TES5 skills at weight 0
  2. Specialization (Combat/Magic/Stealth) adds +2 to corresponding TES5 skills
  3. Two primary attributes: each attribute's associated skills get +1
  4. Seven major skills: mapped to TES5 equivalents, each gets +3
  - Attribute→Skill mapping: Str→OneHanded/TwoHanded/Smithing, Int→Conjuration/Alchemy, Wil→Restoration/Alteration, Agi→Sneak/LightArmor/Lockpicking, Spd→Pickpocket/Speechcraft, End→Block/HeavyArmor, Per→Destruction/Illusion/Marksman/Enchanting, Luck→all skills +1
