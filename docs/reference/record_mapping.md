# TES4 → TES5 Record Mapping Reference

Linked from [CLAUDE.md](../../CLAUDE.md). Reference tables for record type mapping,
structural requirements, and known problem records. For narrative conversion
notes (NIF/mesh/collision/particle/creature), see
[nif_conversion_notes.md](../commentary/asset_convert_nif.md). For dialogue/quest specifics,
see the `oblivion-to-skyrim-dialog` skill.

## Record Format Differences: TES4 vs TES5

### Critical Structural Requirements for TES5

1. **OBND (Object Bounds)** — 12-byte struct required on nearly all item/object records (ACTI, ALCH, AMMO, ARMO, BOOK, CONT, DOOR, ENCH, FLOR, FURN, INGR, KEYM, LIGH, MISC, NPC_, SLGM, SPEL, SCRL, STAT, TREE, WEAP, and more). TES4 has no OBND. **Missing OBND will cause the engine to reject records.**

2. **File Header (TES4 record)** — HEDR version must be 1.7 (Skyrim LE) or 1.71 (SSE), not 1.0 (Oblivion). Form version = 44 for SSE.

3. **Form Version** — Each TES5 record header has a form version field. SSE = 44, LE = 43. Some record structures differ by form version.

4. **No SCRI** — TES4 uses SCRI (FormID → SCPT record). TES5 uses VMAD (Virtual Machine Adapter) for Papyrus. VMAD cannot be auto-generated from TES4 scripts.

5. **Keyword System (KSIZ/KWDA)** — TES5 records extensively use keywords. ARMO, WEAP, NPC_, SPEL, ALCH, INGR, RACE, and many others have keyword arrays. Many game systems depend on specific keywords.

6. **Localized Strings** — When the TES4 header has the Localized flag (0x80), FULL/DESC/etc. become LString indices. Non-localized plugins use inline strings.

### <a id="record-type-mapping"></a>Record Type Mapping

| TES4 Type | TES5 Type | Notes |
|-----------|-----------|-------|
| ACTI | ACTI | Add OBND. Needs VMAD instead of SCRI. |
| ALCH | ALCH | Add OBND. ENIT restructured. Effects need MGEF FormID resolution. |
| AMMO | AMMO | Add OBND. SSE DATA (20B): Projectile FID + Flags(U32) + Damage(f) + Value + Weight(f). **Flags bit 0x04 = Non-Bolt — must be set or the game classifies the ammo as a crossbow bolt.** A companion PROJ is synthesised per arrow (TES4 has none): PROJ DATA Type is a **bit value** (Arrow=0x40, not enum 7 — wrong type = no working projectile); offsets per wbDefinitionsTES5 {72}=CollisionRadius 0.5, {76}=Lifetime 0, {80}=RelaunchInterval 0.25; Sound=WPNBowProjectileSD (0x0003F2B4). Values matched to vanilla ArrowIronProjectile (0x0003BE11). |
| ANIO | ANIO | Minor changes. |
| APPA | MISC | No apparatus in TES5. Convert to MISC. |
| ARMO | ARMO | **Major changes**: BMDT(4B)→BOD2(8B), 16→32 biped slots. Armor models move to ARMA records. ARMO references ARMA via MODL array. No direct mesh on ARMO. Add OBND, RNAM (race), keywords. |
| BOOK | BOOK **or SCRL** | A book carrying an **ENAM is a SCROLL**, and Skyrim's BOOK has no field for an object effect — so those emit a `SCRL` with the ENCH's effect list copied onto it (503 across Oblivion/Nehrim/Morrowind_ob were converting to blank, uncastable paper). `import_main` files each record by the signature its own bytes carry, so one converter can retarget per record. The rest: add OBND, DATA restructured, skill teaching uses the TES5 skill enum. `DATA.Type` is always 0 — the CK lists 255 = Note/Scroll but all 821 vanilla BOOKs write 0, so 255 is engine-untested. **INAM (Inventory Art STAT) is mandatory — BookMenu null-derefs (in-game crash) when a book without INAM is read.** But pointing INAM at a vanilla stand-in shows the default Skyrim cover, so we synthesise a per-book `InvArt_<edid>` STAT wrapping the book's own converted mesh. CNAM (Description string) present-but-empty like vanilla. Book text (DESC) HTML: Skyrim Scaleform only knows **named fonts** (`<font face='$SkyrimBooks'>`, `$HandwrittenFont`, `$DaedricFont`) — Oblivion's numeric `<font face=N>` resolves to no font and renders NO text (map 1/2/3→$SkyrimBooks, 4→$DaedricFont, 5→$HandwrittenFont); IMG src needs `img://textures/tes4/menus/<path>` (Oblivion srcs are relative to Textures\Menus\). |
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
| FACT | FACT | DATA is U8 in TES4 / U32 in TES5 and the bits are **renumbered**. Crime data: CNAM→CRVA (`<BBHHHHHfHH`). See FACT Conversion below. |
| FLOR | FLOR | Add OBND. Minor changes. |
| FURN | FURN | Add OBND. Furniture markers restructured (FNMK was a U32 bitmask; TES5 uses entry-based system with FNPR). |
| GLOB | GLOB | Identical. |
| GMST | GMST | Many settings differ but format is same. Some TES4 GMSTs don't exist in TES5. |
| GRAS | GRAS | Add OBND. Minor changes. |
| HAIR | HDPT | Hair→Head Part (Type=3). **Converted, not substituted** — `convert_HAIR`. One HAIR becomes SEVERAL HDPTs: Oblivion's per-NPC hair length (`NPC_.LNAM`) blends the mesh toward its `.tri`'s `HairMorph`, and Skyrim has no such field, so each length in use is baked into its own mesh. See [Hair conversion](#hair-conversion). |
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
| MGEF | MGEF | **Converted since 2026-07-31** (`record_types/magic.py`) — was in SKIP_TYPES, which cost 796 dropped effects and 382 filler records. TES4's 4-char code picks a TES5 **Archtype** (152-byte DATA, FormVersion 44); the code table covers all 161 codes any export defines. Extra MGEFs are emitted per **actor value** (one TES4 `DGAT` is Damage Strength on one spell and Damage Endurance on the next — Skyrim moved the AV onto the MGEF) and per **script** (a TES4 `SEFF` names its script per-effect; archetype 1 + VMAD). `Assoc. Item` is written only for the 10 archetypes that read it, and its target is re-type-checked. See [magic_conversion_plan.md](../commentary/tes5_import_magic.md). |
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
| SGST | SCRL | Sigil Stone → Scroll (closest equivalent). Shares `_build_scrl` with the enchanted-book path (see BOOK). |
| SKIL | *(skip)* | Skills hardcoded in TES5. Exported for reference. |
| SLGM | SLGM | Add OBND. Minor changes. |
| SOUN | SOUN + SNDR | TES5 splits sound into SOUN (marker) + SNDR (Sound Descriptor with actual data). |
| SPEL | SPEL | **SPIT restructured**: 16B→36B. New fields: Cast Type, Target Type, Cast Duration, Range, Half-cost Perk. Add OBND, keywords. |
| STAT | STAT **or MSTT** | Add OBND. A STAT whose CONVERTED mesh is a constrained dynamic havok island (mass>0 body + any bhk constraint; flag bit 0 in the mesh-bounds cache's optional 7th element, computed by `collision_extract.physics_flags_from_data`) is written as **MSTT** (`EDID OBND MODL DATA:u8=0`) — Skyrim never simulates constrained bodies on a STAT reference (PrisonCellChains01 hung rigid); vanilla routes all such content through MSTT (every swinging inn sign) or ACTI (bone alarm). 18 records retype on Oblivion.esm (chains, chain dolls, punching bags, hanging lamps, root havok pieces). FormID unchanged; `import_main` files records by their own signature. |
| TREE | TREE | CNAM restructured. |
| WATR | WATR | DATA→DNAM. Completely different water properties structure. |
| WEAP | WEAP | **DATA restructured**: 32B→10B. Type moves to DNAM. Massive DNAM struct (~100B). CRDT (critical data) new. Add OBND, keywords. |
| WRLD | WRLD | New fields: XLCN, fixed dimensions, various flags. |
| WTHR | WTHR | Cloud system redesigned (layer-based). HDR/lighting data restructured. |

#### <a id="proj-data-layout"></a>PROJ DATA (92 bytes) offset map

Per `wbDefinitionsTES5.pas`; built by `equipment._build_arrow_proj`, values
matched to vanilla `ArrowIronProjectile` (0x0003BE11). Subrecord order is
`EDID OBND FULL MODL DATA NAM1 VNAM`.

```
{00} Flags(U16)  {02} Type(U16)  {04} Gravity(f)  {08} Speed(f)  {12} Range(f)
{16} Light  {20} MuzzleFlashLight  {24} TracerChance(f)
{28} ExplAltTrigProximity(f)  {32} ExplAltTrigTimer(f)  {36} Explosion
{40} Sound  {44} MuzzleFlashDuration(f)  {48} FadeDuration(f)
{52} ImpactForce(f)  {56} SoundCountdown  {60} SoundDisable
{64} DefaultWeaponSource  {68} ConeSpread(f)  {72} CollisionRadius(f)
{76} Lifetime(f)  {80} RelaunchInterval(f)  {84} DecalData  {88} CollisionLayer
```

`Type` is a BIT value: Arrow = `0x40`, **not** the ordinal 7 (which reads as
Missile|Lobber|Beam and spawns no usable projectile). `Flags 0x00C0` = Can Be
Picked Up + Supersonic, as `ArrowIronProjectile`. Speed scales the TES4
normalized 0-1 value proportionally into TES5 units/sec (1.0 -> 3600, the iron
arrow's), floored at 500.

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
| MUSC | Music Type | Built from the TES4 `Music\<Category>\` FOLDERS (no TES4 record exists). CELL `XCMT`/WRLD `SNAM` enum -> `XCMO`/`ZNAM` FormID. See [music_conversion.md](../commentary/asset_convert_audio.md). |
| MUST | Music Track | One per converted music file; `ANAM` names the xWMA, `FLTV` the measured duration. |
| SNDR | Sound Descriptor | Sound data (SOUN is just a marker in TES5). |
| MATT | Material Type | Landscape material (was HNAM enum). |

## Known Problems and Skipped Records

### Why the dispatch table converts what it converts
<a id="dispatch-table-membership"></a>

**Code:** `tes5_import/registry.py` — `IMPORT_DISPATCH`, `TYPE_MAP`, `SKIP_TYPES`.

Several signatures look like they should be skipped, or look like they should be
in the generic dispatch, and are not. Each exception is load-bearing:

- **MGEF is CONVERTED** (`record_types/magic.py`). It used to be skipped, with
  every effect re-pointed at a vanilla Skyrim MGEF through a flat code table —
  which cannot express an effect parameterised by a FormID the source carries,
  so all 33 summons and every bound weapon/armor were dropped and **382 records
  became inert filler**.
- **GLOB is NOT skipped.** Converted scripts bind GlobalVariable properties to
  TES4 globals (`TES4Fame`, quest counters), which read `None` if the records do
  not exist. `convert_GLOB` drops the engine-time globals (`GameHour` etc.);
  properties naming those bind unshifted to Skyrim's own forms via
  `ENGINE_GLOBAL_FORMIDS`.
- **CLMT is CONVERTED** — it is the ONLY path to the converted WTHR records
  (weather is reached via `WRLD → CNAM → CLMT → WLST`, never referenced
  directly). Skipping it orphans every converted weather.
- **REGN is CONVERTED for its WEATHER entries only** (`convert_REGN`).
  TamrielClimate carries a single Clear weather at 100%, so ALL of Cyrodiil's
  weather variety lives in region RDWT lists. The other region data types
  (objects/grass/sound/map) belong to TES4 systems with no direct equivalent and
  are dropped.
- **HAIR is CONVERTED to HDPT** (`convert_HAIR`). Oblivion hair is a real mesh
  with a real per-NPC length (`NPC_.LNAM` blending the `.tri`'s HairMorph), none
  of which survives substituting a vanilla Skyrim hairstyle. The length is baked
  per variant by `asset_convert.character.hair_pipeline`, which is why one HAIR
  record can emit several HDPTs.
- **GMST is skipped WHOLESALE**, but the ambient-dialogue pacing settings are an
  exception — `AMBIENT_GMST_OVERRIDES` in `constants.py`, emitted by the
  orchestrator regardless of the skip.

Two signatures are converted but deliberately **outside** the generic dispatch,
because it runs too early or in the wrong order:

- **PACK** (`pack_converter.py`) — quest packages need the QUST aliases to exist
  first, so PACK is written in its own phase after QUST (Phase 3b2).
- **WTHR** — it mints four IMGS companions for its HDR tone mapping, so it runs
  in its own serial phase (Phase 2b) where record order is deterministic.

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
  bug — the vanilla-asset A/B dog moved even with dangling packages. `tes5_import/packages/actor_wiring.py`
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
- **Outfit split (`tes5_import/actors/outfits.py`)** — TES4's single CNTO inventory (engine picks what to wear at
  spawn) → TES5 DOFT/OTFT (worn) + CNTO (carried), disjoint. Per-biped-slot conflict resolution keeps
  one winner per slot (armor > clothing > value). **ChanceNone contract:** only a *guaranteed* winner
  (plain ARMO/CLOT, or an LVLI with `LVLD.ChanceNone==0` down to slot-filling leaves) may EVICT a
  lower-priority item from its slot. A probabilistic list (e.g. `LL0NPCArmorLightGreaves25`, ChanceNone
  75) must NOT evict a guaranteed clothing base under it (`LL0NPCClothingPantsLower`, ChanceNone 0) —
  Skyrim resolves the outfit once and has no equivalent of Oblivion's per-spawn re-scoring, so evicting
  the guaranteed pants left ~75% of bandits bare-legged. Keep both; engine wears greaves when rolled,
  pants otherwise. The `NN` in Bethesda's list names (`...Greaves25`, `...Cuirass100`) is the equip
  probability. Trace any actor with `python -m tools.audit.trace_outfit export/Oblivion.esm <EditorID>`.
  **A plugin WITH MASTERS must index its masters' items too** (`load_item_index(by_type,
  ctx.master_export)`): a dependent plugin dresses its actors out of its MASTER's wardrobe, so most
  inventory entries name a record the plugin does not contain. DLCBattlehornCastle draws 155 of its 165
  NPC inventory entries from Oblivion.esm; indexing only its own 45 records left every one of them
  unclassifiable, so they fell to the non-wearable default, stayed in CNTO, and all 22 NPCs — knights,
  captain, maid, cook — spawned with NO outfit at all (index 45 items/9 wearable → 8,501/1,609; 20/22
  now dressed, the 2 without being a dog and a lich). Only the item signatures are indexed, never the
  master's whole export: at ~1.17M records that is the difference between ~30k dicts and all of them
  (and the navmesh phase then forks workers off this process — see the memory note in `load_item_index`).
  The index keys on the low 24 bits, so the plugin's own records are loaded LAST and win. An override
  plugin that only retitles NPCs is unaffected and must stay so: Translation.esp's 1,284 NPC overrides
  keep the master's DOFT byte-identically, 0 lost.
- **RACE** — Almost entirely restructured. Only basic data (height/weight/skill boosts/spells) can transfer.
- **ENCH/SPEL** — ENIT/SPIT completely restructured. Effects need MGEF FormID resolution.
- **ARMO/CLOT** — Missing ARMA records means armor won't render in-game.
- **🔴 A wearable authored for ONE gender must still get an armature (2026-08-08).** `convert_ARMO` gated the ARMA build on `male_model`, and `_build_arma`/the ground-model fallback read only the male fields. Oblivion lets a record ship a female biped model with the male field empty — Nehrim has 5 (`IrlandaRobe`, the four `SFLight*` Silverlight pieces) — so those ARMO came out with **no armature at all**. An ARMO with no ARMA still equips and occupies its slot but renders nothing, i.e. the actor looks naked while apparently dressed: Goddess Irlanda and both MQ34 Eliath embodiments. Fix: build the ARMA when EITHER gender has a mesh, fall back across genders for the ground model and the boot test. **Do not invent a MOD2** — vanilla census (Skyrim.esm, 766 ARMA): 590 MOD2+MOD3, 172 MOD2 only, **4 MOD3 only, 0 with neither**, so female-only is legal and empty never is. `wearable_plan`'s ground-model fallback must track `convert_ARMO.ground_model` exactly or the dropped item's mesh gets pruned. Diagnose with `tools/naked_npc_trace.py`.

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

### LVLN shell NPC_ DNAM must not be zero (fixed 2026-07-30)

**Symptom:** most animals in converted Nehrim (river crabs, boar, deer,
chickens, pigs) keeled over dead the instant the cell loaded. Spawning the
*same base record* from the console produced a healthy animal, and the actor's
health pool audited clean (`tools/audit/actor_health_audit.py`: 0 SPAWN-DEAD, 99.9%
exact) — so health, ACBS flags, the generated RACE and the behaviour project
were all exonerated.

**Discriminator:** every dying animal was placed through a generated
`<list>_Lvl` template shell (TES4 `REFR → LVLC` becomes
`ACHR → NPC_ shell → TPLT → LVLN`, see `tes5_import/actors/leveled_actors.py`), while
every confirmed survivor — sheep `1A1963`, pack mule `206C73` — was a
hand-placed `ACHR` pointing **straight at its base NPC_**. `placeatme` on the
base likewise never goes through the shell, which is exactly why the console
test looked fine. Note that sheep and pig are indistinguishable on every other
axis (identical 45-bone project, 14 clips, same speeds, same `ACBS.Flags=840`),
so only the placement path explains the split.

**Cause:** `_build_shell` wrote `pack_subrecord('DNAM', bytes(52))`. DNAM
offsets 36/38/40 are the cached derived Health/Magicka/Stamina; a zero Health
cache spawns the actor at 0 HP and it dies on load. `Use Stats` in
`TemplateFlags` does not save it — the placed reference still comes up seeded
from the shell's own cached pool.

**Vanilla census (the decisive evidence):** of Skyrim.esm's 508 NPC_ shells
whose `TPLT` is an LVLN, **zero** write Health=0. Minimum is 47; the dominant
triple is 55/37/49 (Health 379/508, Magicka and Stamina 370/508). The shell now
writes exactly that. The template supplies the real pools at spawn, so this only
needs to be a sane non-zero seed, not any particular creature's stats.

**Rule:** a template shell's Required subrecords may be neutral, but never
*zero* for a field the engine caches and reads before template resolution.

### LVLN shell NPC_ must carry the LVLC's scripts (fixed 2026-08-27)

TES4 attaches scripts to a leveled creature in **two independent places**, and
Skyrim's LVLN has a field for neither:

| TES4 field | Runs on | Count in Oblivion.esm |
|---|---|---|
| `SCRI` on the LVLC record itself | the placed leveled marker | 27 lists |
| `SCRI` on the CREA named by the LVLC's `TNAM` (template) | the creature the list SPAWNS | 176 lists |

Both died in the `ACHR → shell NPC_ → TPLT → LVLN` chain. The shell's TPLT
points at the LVLN, whose entries are ordinary unscripted creatures, so ACBS
Use Script (bit 9, `wbTemplateFlags` in `wbDefinitionsCommon.pas:7715`)
inherited an **empty** script list and silently discarded both.

Measured: **1063 placed refs across 96 leveled lists** lost their script —
MS48's Kvatch southern plaza, MS49's castle courtyard, MQ12/15/16, MG14/16,
SE03/04/10/12 and Dark07 among them.

The observed symptom was "Clear the southern plaza of Kvatch" never completing.
`MS48PlazaCreatureFinalScript` increments `MS48.monsterskilled` on each death
and sets stage 80 at six; live `sqv MS48` showed **stage 70 with
`monsterskilled = 0` while all six plaza creatures returned `GetDead >> 1.00`**.

`leveled_actors._attach_lvlc_script` now attaches BOTH scripts to the shell via
`object_scripts.attach_scripts_to_record`, and `_shell_acbs(own_script=True)`
clears **only** bit 9 (`0x1FFF` → `0x1DFF`) so the shell's own list survives
while every other category still inherits.

**They are not alternatives.** Keying on `SCRI` alone attaches the list-marker
housekeeping script (`MS48PlazaCreatureFinalLLScript`, a `setdestroyed` guard)
and still loses the counter. Skyrim's VMAD is a script LIST — both attach side
by side, each keeping its own handlers, exactly as Oblivion ran them.

Audit a built ESM with `temp/shell_vmad_audit.py`: 442 shells, 96 with a VMAD,
103 attached scripts, and Use Script cleared on exactly the scripted ones.

**Rule:** when a TES4 record's data reaches the engine through a field Skyrim's
target record lacks, check whether the *template* can carry it — and if the
shell must own that data, clear the matching template bit or the empty
inherited value wins.

## Hair conversion

Oblivion hair is CONVERTED, not substituted with a vanilla Skyrim hairstyle.
`convert_HAIR` -> HDPT (Type 3), `asset_convert/character/hair_pipeline.py` for the
meshes, `asset_convert/character/facegen_tri.py` for the `.tri` codec,
`tes5_import/actors/hair_variants.py` for the plan both sides share.

### Why one HAIR becomes several HDPTs

`NPC_.LNAM` ("Hair length", `wbFloat` in xEdit's TES4 definitions) is an
authored per-NPC float. Oblivion blends the hair mesh toward the `HairMorph`
target stored in the mesh's sibling `.tri` by exactly that weight. Skyrim has
no per-NPC hair-length field, so the blend is resolved at conversion time:
each length actually worn is baked into its own mesh with its own HDPT.

Distribution in Oblivion.esm (654 NPCs at 0.0, 371 at 1.0, long tail at 0.58 /
0.55 / 0.47 / 0.37 ...) is strongly bimodal, so the endpoint buckets are free —
bucket 0 IS the base mesh and bucket `LENGTH_BUCKETS` IS the fully applied
morph. Measured cost for Oblivion.esm (2026-08-25): **721 meshes + 721 `.tri`
for 57 hair records** — each gender fitted to its own Skyrim head, and each
GENERIC style additionally baked per race group (bare stem = human, `__ev` =
elves, `__or` = orc). A race-NAMED style bakes ONE mesh, fitted to its own
group's head but keeping the bare stem, because its RNAM already restricts
who wears it. Driven by real NPC usage rather than exhaustive expansion.

### That the morph is length, not a face slider

Verified in both engines, not inferred:

* **Oblivion.exe** holds the literal `HairMorph` once (file `0x6640e8`) with a
  single xref — a `strcmp` at `0x55c48b` inside a loader copying 12-byte x/y/z
  triples, adjacent to the RTTI names `BSFaceGenMorphDataHair` /
  `BSFaceGenMorphDataHead`. Neighbouring diagnostics bucket it as a **"Custom
  Morph"** (phoneme/modifier/expression have their own messages). There is no
  second path applying a scale on top, so baking cannot double-apply.
* **SkyrimSE.exe** holds a morph-name pointer table in `.data` (file
  `0x1e756c0`) whose categories are Phoneme / Expression / Modifier / **Custom**.
  `SkinnyMorph` sits alone at index 7, fenced by NULLs from the expression
  block (8-24), modifier block (26-42) and phoneme block (44-59) — the sole
  member of `Custom`, structurally the same slot `HairMorph` occupies.
* **Geometry**, across all 57 vanilla Oblivion hair `.tri` files: 50 extend
  DOWNWARD under the morph, **0 extend upward**, and the 7 that do not move are
  exactly the short/spiky styles with no length to add (`argonianspikes`,
  `highelfmalepeak`, `orcmalestubs`, `woodelfmalepony` ...). Median z-min drop
  −2.7, extreme −15.9 (`style01`).

`SkinnyMorph` is NOT a length morph — it is a mild build/weight tweak, moving
9-36% of vertices with mean |delta| 0.017-0.33, versus `HairMorph` at 82-100%
of vertices and mean 0.38-2.06. The emitted Skyrim `.tri` therefore carries the
slot with **zero deltas**: the length is already in the geometry.

### The .tri container

Both games use the identical `FRTRI003` FaceGen format (layout documented in
`facegen_tri.py`, round-tripped against all 57 Oblivion + 113 vanilla Skyrim
hair files with zero trailing bytes on every one). 4 Oblivion hairs ship no
`.tri` at all (`blindfold`, `emperor`, `khajiitearrings`, `style07`); those
convert fine and their HDPT simply omits NAM0/NAM1 rather than naming a file
that was never written.

**`.egm` does NOT convert.** It is a different system — 50 symmetric + 30
asymmetric FaceGen PCA morphs, the same basis behind FGGS, and Skyrim ships no
`.egm` at all with direct sliders instead of a PCA basis (the wall
`npc_face_mapper` already documents for FGGS→NAM9). `LNAM` does not drive the
`.egm`, so hair length is unaffected by that gap. What IS lost: converted hair
will not follow a chargen-morphed player skull. NPC faces are fixed, so NPC
hair is unaffected.

### Meshes

`meshes\characters\` is in `nif_converter.SKIP_PATHS` and hair could not
simply be un-skipped, because one record produces several meshes. The stage
bakes each length, then routes the result through `convert_nif(..., hair=True)`
— which marks it worn with biped bit 1, so the existing helmet path gives it a
`BSDismemberSkinInstance` in **slot 131** bound to `NPC Head [Head]`, exactly
as vanilla Skyrim hair does.

**Head gear is fitted by `asset_convert/character/head_fit.py` (v3) — a smooth
scalp-to-scalp displacement field, sampled per vertex.** Built once per
gender/race: for every OB head vertex, where the matching SK skin point is
(nearest-point init over the identity carrier — no scale, ever — relaxed by
smoothing + reprojection along the OB normals; every target ends exactly ON
the SK skin). Fitting is then a pure per-vertex function: a vertex ON the
skin lands ON the new skin (hairline edges exactly at the skin line), a
vertex N units off stays exactly N off (helmets keep authored standoff),
and everything over one scalp region moves identically (headbands never
stretch; hanging tails take their region's delta by diffusion). The real
skull differences it transports: SK jaw/cheek 1-1.6 wider per side, nose and
crown 2.1 lower, occiput/nape 2-3.5 further back. Ears are flattened out of
the correspondence (gear ignores them, like vanilla hair); orc hair uses the
human pair (Skyrim orcs share the human head); khajiit/argonian keep their
own head pairs. Skinned helmets/hoods blend the SAME field by head weight in
the wrap deform. **Every in-game head additionally wears its RACE's
races-tri morph (elf scalps 2.6 off base; the base mesh is worn only by the
five human races + Dremora, whose scalp morphs are <=0.15). A races.tri on
the hair HDPT was tried and the engine does NOT apply it to type-3 parts —
generic hair is instead BAKED per race group (base / `__ev` elves / `__or`
orc) with four HDPTs per variant gated by the vanilla RNAM race lists, and
the NPC face mapper routes each NPC to its race group's variant.** Full
details and the measured gates in
[nif_conversion_notes.md](../commentary/asset_convert_nif.md).

**Hair is emitted PER GENDER** (`stem` / `stem__f`, gendered HDPTs): the
Skyrim male and female scalps differ by up to **1.23 units** (mean 0.46),
so one unisex mesh cannot be flush on both — vanilla genders every
hairstyle. TES4's `NotMale`/`NotFemale` DATA flags decide which genders
exist; the base gender's bucket-0 HDPT keeps the source FormID, male
variants key `('HDPT_HAIR', (fid, bucket))`, female ones `('HDPT_HAIR',
(fid, bucket, 'F'))`, and `npc_face_mapper._resolve_hair_part` routes each
NPC by its own gender + LNAM bucket (master-owned hair resolves to the base
FormID — a dependent plugin cannot mint the master's derived ids).

One fix was needed to read the target: `sse_nif._geometry_arrays` returned
early on inline vertices, but vanilla `malehead.nif` is a `BSDynamicTriShape`
holding verts inline with UVs/normals/indices in the skin partition — it now
falls back per attribute.

Two traps found while building this, both worth keeping in mind elsewhere:

* **Hair matched the body-skin stripper.** `is_body_skin_geometry` keys off the
  `textures\characters\` prefix, which hair shares — so once the mesh became
  skinned it was deleted wholesale, and the converted NIF shipped with a head
  bone node and no geometry. Hair is excluded by its `\hair\` subfolder
  (`_HAIR_TEX_MARKER`). Censused: 0 armor meshes are affected by the exclusion.
* **The load-order index byte is not identity.** The export dumps HAIR
  `000C4821` but the record reaching `convert_HAIR` carries `010C4821`. Keying
  the bucket index on the raw value made every lookup miss **silently** — no
  error, every NPC just fell back to bucket 0 and the whole feature evaporated.
  Masked with `& 0x00FFFFFF` at every keying site, including the
  `derive_formid` key. Guarded by
  `tests/test_hair_conversion.py::test_lookup_ignores_the_load_order_index_byte`.

### Hair color

Two separate things have to be right, and BOTH were wrong at first — the mesh
rendered as its raw grey source texture regardless of the NPC's hair color.

**1. The mesh needs the Hair Tint shader (type 6).** `nif.xml` gates the Hair
Tint Color field on `Shader Type == 6` ("Enables Hair Tint Color"), and the
engine carries a matching `BSLightingShaderMaterialHairTint` RTTI class, a
`HairTint` shader technique, and a `HairTint` console command ("3 ints, RGB").
Converted hair shipped as type 0 (Default), so the tint was never sampled —
with the wrong material class the color has nowhere to land. Vanilla census
(214 hair shaders): **type 6 on 196**, type 5 on the 18 beast-race horn meshes,
which are genuinely untinted. `hair_pipeline.apply_hair_shader` sets type 6
plus `SLSF1_Hair_Soft_Lighting`, `SLSF1_Own_Emit`, `SLSF2_Assume_Shadowmask`
**and `SLSF2_Anisotropic_Lighting` with lighting effects 0.3/2.0** — the
anisotropic flag is what makes the specular read as hair strands; without it
(and with lighting effects left 0/0) converted hair looked "really shiny and
not hair-like" in game (2026-08-24; vanilla hairshorthumanm/malehumanoldhair01
carry it). Vanilla glossiness/specular (10 / 0.9 / white) — a zero glossiness
reads as flat matte under the tint.

The tint color written into the mesh is a **placeholder** — `nif.xml` says it
is "Overridden by game settings", and the engine substitutes the wearer's
HCLF→CLFM color at runtime. We write vanilla's most common value
(`0.5176, 0.4706, 0.3922` = RGB 132/120/100, 92 of 214 shaders) so a converted
mesh looks right in NifSkope and the CK preview.

**2. The color itself needs a CLFM that actually holds it.** Skyrim's
`NPC_.HCLF` is a FormID into CLFM, and vanilla ships only **15** hair-color
swatches, all dark and desaturated. Oblivion authors a free RGB per NPC:
**2,482 haired actors using 571 distinct colors** spanning the whole cube.
Snapping each to the nearest vanilla swatch measured a mean RGB error of
**26.9**, median 22.1, p90 49.9, **max 274.5** — visibly wrong hair.

So `actors.hair_color_formid` generates one CLFM per DISTINCT authored color
(EDID + CNAM byte-RGBA + FNAM playable — a tiny record) and HCLF points at it,
carrying the authored RGB across **exactly**. One record per color rather than
per actor keeps the count at the 571 distinct values, not 2,482.

Keyed `derive_formid('CLFM_HAIR', (r, g, b))` — authored data, so the same
color lands on the same id everywhere. **No drift**: the old HCLF targets were
Skyrim.esm ids (`0x000A04xx`), so nothing of ours moved; only the HCLF *value*
changed. `map_hair_color` survives as the fallback for the writer-less path,
now with all 15 swatches (`HairColor12BlackTrue` was missing).

### FormIDs

Variant HDPTs use `derive_formid('HDPT_HAIR', (hair_fid & 0xFFFFFF, bucket))`.
Both halves are authored TES4 data, satisfying the hashing contract. Bucket 0
keeps the source FormID, so an NPC with `LNAM` 0 resolves straight through the
id its `HNAM` already names.

🛑 **Changing `LENGTH_BUCKETS` renumbers every non-zero variant** — that is
FormID drift and breaks saves.

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
`tes5_import/base/constants.py`), which suppresses the 141 partitions → all hair
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
- **Level**: fixed levels carry across unchanged (TES4 and TES5 both store a plain
  level). PC-Level-Offset actors (TES4 flag 0x80) become PC Level Mult 1000
  (=1.0x): an additive offset has no multiplier equivalent. **CalcMin/CalcMax are
  NOT doubled** — they are a plain level band in both games. (The old `* 2` was
  real code in `_npc_acbs`; it doubled every NPC's level range and disagreed with
  the creature path, which never doubled.)
- **Health / Magicka / Stamina — the authored control is `ACBS.*Offset`, not DNAM.**
  The engine computes an actor's pool as
  `RACE.StartingHealth + ACBS.HealthOffset + (Level-1) * fNPCHealthLevelBonus`,
  with `fNPCHealthLevelBonus = 5.0` (Skyrim.esm GMST) and all 11 mapped playable
  /Dremora races at `StartingHealth = 50.0`.
  `DNAM.Health/Magicka/Stamina` (offsets 36/38/40) are only the engine's
  *calculated cache* — vanilla proves they are not a function of the record at
  all: 52 groups of NPCs with identical race/class/level/offset carry different
  DNAM.Health (55 / 51 / 0 / 20971), matching UESP's "otherwise seems to be
  random". TES4 `DATA.Health` is by contrast a FINAL, fully-calculated pool.
  So conversion **solves for the offset** that reproduces the TES4 total exactly
  (`_health_and_level` in `record_types/actors.py`) rather than copying the pool
  into the cache field. Verified against the BUILT esm with
  `tools/audit/actor_health_audit.py`: **Oblivion 100.0% exact (1,413/1,413), Nehrim
  99.9% (2,224/2,227)**, zero spawn-dead actors.
  - **Creatures use the same flat 50 base.** A generated creature RACE is
    **shared** by every CREA with the same mesh folder + body set (`made[key]` in
    `build_creature_races`), so it must NOT carry any one creature's health —
    doing that gave every other creature on that race the first one's HP
    (measured: 345 Nehrim actors, e.g. all three StartCelleTroll variants at 15 HP
    instead of 450). Per-creature health lives entirely in the per-record
    `ACBS.HealthOffset` (`creature_health_offset`). Creatures that get no
    generated race fall back to a vanilla Skyrim race, which has the same 50 base,
    so one formula covers both.
  - Zero-health TES4 creatures are **intentional corpse props** (52 Nehrim, 45
    Oblivion) and are pinned dead with offset `-32768`, including the
    PC-Level-Mult ones whose runtime level term would otherwise revive them.
  - The int16 offset cannot express the handful of TES4 actors above ~32k HP (dev
    test dummies, story-boss invulnerability sentinels, the Player record). The
    surplus is spent through the Level term instead of clamping, so they stay
    effectively unkillable; only 3 actors (1,000,000 and 9,999,999 HP) exceed even
    the combined ~360k ceiling, which is still 59x the toughest real enemy.
  - Magicka/Stamina offsets come from TES4 `ACBS.SpellPoints` and `ACBS.Fatigue`
    (the actual pools). They previously received raw **Intelligence** and
    **Strength** attributes, which are not pools at all.
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

**TES4 `DATA` is one U8, not a U32.** xEdit `wbDefinitionsTES4`: bit 0 Hidden
from Player, bit 1 Evil, bit 2 Special Combat. Measured at exactly 1 byte in all
204 Nehrim.esm factions. The exporter used to guard on `len >= 4` and unpack a
U32, so `DATA.Flags` was silently absent from **all 476** Oblivion.esm factions
(fixed 2026-07-31).

- **Flag bits are renumbered between the games** — do NOT pass them through.
  TES4 bit 1 is *Evil* but TES5 bit 1 is *Special Combat*.
  Mapping: TES4 bit 0 → TES5 bit 0 (Hidden From NPC); TES4 bit 2 → TES5 bit 1
  (Special Combat).
- **Can Be Owner** ($8000) set on all factions.
- **Track Crime** ($40, xEdit bit 6) — Skyrim requires it before the engine
  accumulates any crime gold. Oblivion has no equivalent flag, so the set is
  derived in Phase 0 (`_load_crime_factions`) from the factions the plugin's own
  scripts pass to `Get`/`SetPCFaction{Murder,Attack,Steal}`. Oblivion.esm → 6.
  The old "Evil → all crime flags" line set the ***Ignore* Crimes** bits
  (7-11, 13, 16), the exact opposite of the intent, and never set Track Crime.
**XNAM Relations** — `Faction(FormID) Modifier(S32) GroupCombatReaction(U32)`, 12 bytes.

The enum is **`0 Neutral, 1 Enemy, 2 Ally, 3 Friend`** (xEdit `wbFactionRelations`
in `wbDefinitionsCommon`). **Ally is 2 and Friend is 3** — these were swapped in
the converter until 2026-07-31. Confirmed twice: the xEdit definition, and a
census of Skyrim.esm where **160 of 200 faction SELF-relations use 2** (a faction
is Ally to itself, never merely Friend).

- **Modifier is always 0.** 1,035 of Skyrim.esm's 1,036 XNAM relations write 0
  there. TES4's -100..+100 disposition scalar has no meaning in TES5; the
  reaction enum carries the entire signal. Writing the disposition into that
  field was noise the engine ignores.
- **Disposition → Reaction**: ≤-50 → Enemy(1); ≥50 → **Ally(2) only for a
  faction's relation to ITSELF, otherwise Friend(3)**; else Neutral(0).

**Why Ally is reserved for the self-relation.** A TES4 disposition is a 0-100
scalar meaning "likes them more" — it never obliges anyone to fight. TES5's Ally
is a hard contract: reaction combines with aggression and **assistance** to
decide who joins a fight (UESP `Skyrim:Factions`). Oblivion's CG data has
`BladesCG → MythicDawnCG` at **+100**, purely a disposition bonus — TES4 starts
the intro ambush with `StartCombat`, not through the faction graph. Converted as
Ally, that edge made the Emperor's guards **assist the Mythic Dawn and turn on
the player** (a Neutral) instead of on the assassins. Oblivion.esm has 171
cross-faction positive relations that would each wire a similar assist edge.
Self-relations (147) are the legitimate case and match vanilla's idiom.

`script_convert`'s `setfactionreaction`/`modfactionreaction` path
(`_faction_reaction_call`) follows the same rule: positive amounts stop at
Friend (`SetAlly(f2, true, true)`) and never reach Ally, since that function
always names two *different* factions.

**CRVA layout** (xEdit; verified byte-for-byte against Skyrim.esm's
`WERoad12HorsemanFaction` = `0101 E803 2800 0500 1900 0000 0000003F 6400 E803`):

```
Arrest U8, Attack On Sight U8, Murder U16, Assault U16, Trespass U16,
Pickpocket U16, Unknown U16, Steal Multiplier Float, Escape U16, Werewolf U16
```

`'<BBHHHHHfHH'`, 20 bytes. The old `'<HHHHIfI'` packing was the same *size* so it
never errored, but misaligned every field — the leading U16 swallowed both U8
booleans (no converted crime faction ever arrested) and left every crime amount
at 0, so `GetCrimeGoldViolent()`/`GetCrimeGoldNonViolent()` returned 0 forever.

Amounts follow the vanilla census — **all 14** real Skyrim crime factions use
exactly murder=1000, assault=40, trespass=5, pickpocket=25, escape=100. The 25x
murder/assault gap is what lets converted scripts tell TES4's `GetPCFactionMurder`
and `GetPCFactionAttack` apart (see
[quest_script_conversion.md](../audits/quest_script_conversion.md) R4-1);
`script_convert/constants.py` holds the matching thresholds, so the two sides
must stay in step. CNAM.CrimeGold carries across as the Steal Multiplier.

### ALCH Conversion
- **Food Detection**: Flag $02 (food) → set food flag + ITMPotionUse sound ($000CAF94) + VendorItemFood keyword
- **Poison Detection**: Name contains 'poison' → set poison flag ($20000) + ITMPoisonUse sound ($00106614) + VendorItemPoison keyword
- Needs OBND, standard potion sound otherwise

### Magic Effects (EFID/EFIT) — null EFID = inventory CTD (fixed 2026-07-10)
- **NEVER write EFID=00000000**: the game dereferences each effect's base MGEF
  when a menu builds the item card → instant CTD on opening inventory with the
  item (crash log: `(AlchemyItem*)` in RCX + `InventoryMenu`). Applies to ALCH,
  INGR, ENCH, SPEL, SCRL alike.
- `_resolve_mgef()` (tes5_import/record_types/equipment.py) tries three lookups,
  most specific first — **since MGEF became a converted record type, the normal
  answer is an effect of OUR OWN**, not a vanilla lookalike:
  1. **script-effect variant** — `SEFF` names its script per effect, and Skyrim
     keeps the script on the MGEF, so each distinct script has its own record;
  2. **per-actor-value variant** — DGAT/DRAT/FOAT/REAT/ABAT/ABSK/DRSK/FOSK are
     parameterised by the effect's own ActorValue, which Skyrim moved into the
     MGEF (so "Damage Attribute" becomes a real "Damage Strength" record);
  3. the plugin's plain MGEF for the code.
  `MGEF_AV_CODE_TO_SKYRIM` / `MGEF_CODE_TO_SKYRIM` (skyrim_overrides.py) survive
  only as the fallback for a plugin whose MGEFs were never exported. Its 17
  phantom keys — codes no export uses — are deleted.
- `_pack_effects()` still guarantees ≥1 real effect (INGR: exactly 4) by padding
  with zero-magnitude AlchRestore* fillers. As of Phase 1 **no record actually
  reaches that path** (audit: 0 filler records for both plugins); it stays as the
  guard against a future plugin using an unseen code.
- **TES5 EFIT is Magnitude, Area, Duration** — offset 4 is Area, offset 8 is
  Duration (xEdit `wbEFIT`; all 427 vanilla ALCH effects put the potion duration
  at offset 8 and 0 at offset 4). `tools/esm/tes5_esm_reader.py` had the two labels
  swapped until 2026-07-31, which made every dump of a converted spell look wrong.
- **TES5 INGR ENIT is 8 bytes** (s32 value + u32 flags), NOT the 20-byte ALCH
  layout (xEdit wbDefinitionsTES5 INGR).

### CK-warning sweep learnings (fixed 2026-07-16)
- **AIMED magic needs a projectile effect**: an aimed ENCH/SPEL/SCRL whose
  effects all resolve to projectile-less Alch* MGEFs casts NOTHING in game
  (CK: "is AIMED but has no Magic Effects with Projectiles assigned", 369x).
  Every effect slot is now cloned onto its owner's casting type and delivery
  (`magic_variants.delivery_variant`), and `magic.fit_delivery` gives an Aimed
  clone a projectile; a vanilla effect clones from its 152-byte DATA baked in
  `vanilla_mgef_data.py` (regen with `tools/generators/gen_vanilla_mgef_table.py`).
  See [tes5_import_magic.md](../commentary/tes5_import_magic.md#owner-casting-type).
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
- Verify all of the above against a build with `tools/validate/verify_ck_fixes.py`.

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
- **XESP must be MIRRORED into XLKR for `GetParentRef` scripts (2026-08-05, in-game confirmed).**
  TES4 `GetParentRef` returns the reference's **Enable Parent** — the `XESP`
  field (xEdit literally names it `'Enable Parent'`; the UESP modding guide
  states the idiom outright: *"make the container its Parent Ref"*, then
  `set rCont to GetParentRef`). Skyrim exposes **no getter for the enable
  parent**, so `script_convert` maps `GetParentRef` → `GetLinkedRef()`, which
  reads **`XLKR`**. Nothing wrote XLKR, so **every converted `GetParentRef`
  returned None** — this affected all **345** scripts that call it, not just
  traps. Symptom: the Vilverin pressure plate's body ran, but
  `target = GetLinkedRef()` was None, so `target.Activate()` never reached the
  swinging mace and the trap hung frozen in mid-air.
  - Layout (xEdit **and** a real Skyrim.esm dump — 11,287 vanilla uses): 8
    bytes, `{Keyword/Ref, Ref}`, keyword slot **NULL** (`00000000`) for a plain
    link. Written after XESP (vanilla orders it both ways; 548 refs carry both).
  - **Scoped to bases whose script actually calls `GetParentRef`** (tracked in
    `object_scripts._GETPARENTREF_BASES`). XESP is ordinary enable-parenting on
    **9,157** Oblivion refs and only **2,660** belong to such a base — mirroring
    all of them would invent links the game never had. Output: 2,813 XLKR.
  - TES4 `XTRG` ("Target") is a *different*, rarer field (90 refs) and still has
    no TES5 equivalent; it is dropped as before.

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

### Sound descriptor slots — a SOUN id here CRASHES the audio thread

Every TES5 slot xEdit types `[SNDR]` must hold the sound DESCRIPTOR, never the
SOUN a TES4 record names there:

| Record | Slots |
|---|---|
| ACTI | SNAM (looping), VNAM (activation) |
| CONT | SNAM (open), QNAM (close) |
| DOOR | SNAM (open), ANAM (close), BNAM (loop) |
| LIGH | SNAM |
| MSTT / TACT | SNAM (looping) |

This is not a cosmetic wrong-sound bug. Our SOUNs are not self-describing the
way a legacy vanilla SOUN is — `convert_SOUN` emits `EDID + OBND + SDSC` only,
with every byte of audio data on the companion SNDR — so the engine takes the
id at face value, registers it in the audio manager's emitter table, and
`BSAudioManagerThread` faults dereferencing it as a `BSGameSound`:

```
EXCEPTION_ACCESS_VIOLATION  SkyrimSE.exe+0CB3CEA   mov ecx, [r8+0x48]
  r8 = 0x0000000100000001        <- a FormID, not a pointer
  RBX (BSXAudio2GameSound*)  RSP+110 (BSFadeNode*) "CathedralCryptLight01"
  RSP+230 (BSAudioManagerThread*)
```

(1.6.1170 RVA `0xCB3CEA` = Address Library id 67726+0x1DA = GOG 1.6.659
`0xC28CCA`, inside the audio event handler's case for event type `0x19`; the
faulting instruction walks the emitter array at `[mgr+0x50]`, count
`[mgr+0x34]`, 0x18-byte entries of `{?, BSGameSound* at +8, guard at +0x10}`.)
A single lit torch or opened container arms it.

These records are all written in Phase 1/2, before Phase 3 mints the
descriptors, so the converters store the TES4 SOUN id as a placeholder and
`items.patch_sound_descriptor_slots` resolves it afterwards. Allocating the
descriptor id at conversion time instead would shift every later generated
FormID.

**The master resolver is required, not optional.** `own_soun_ids` covers only
the SOUNs THIS plugin converts; a slot pointing at a master-owned SOUN is
resolved by reading the SDSC out of the master's *converted* SOUN
(`ctx.master_index.record(fid)`), not re-derived — deriving would mint an id in
this plugin's index space. The master manifest does NOT answer for these: it
holds only companion-bearing records and carries no SOUN entries. Measured on
Morrowind_ob before the fix: 2,377 CONT slots, 97 LIGH, 31 ACTI and 40 DOOR
slots all pointed at a SOUN, the majority of them Oblivion.esm's
(`AMBTorchMountedLP` 0x01085837 → master SNDR 0x0158689D). Standalone plugins
(Oblivion.esm, Nehrim.esm) have zero dangling slots, so this path only matters
for plugins with masters — the [master-blindness](../../CLAUDE.md) failure mode.

WEAP's NAM8/NAM9 are also `[SNDR]` slots but are already correct: they are
written from `WEAPON_ANIM_NAM8/9`, which name vanilla Skyrim.esm descriptors.

### CLAS Conversion
- **Trainer classes**: Skyrim's training menu reads skill/cap from CLAS DATA (Teaches S8 + MaxTrainingLevel U8 at offset 4), but Oblivion trainers store them per-NPC in AIDT (92/114 vanilla trainers disagree with their class). Phase 0c `create_trainer_records` clones each trainer NPC's class with the AIDT values and repoints CNAM; the NPC also joins `TES4JobTrainerFaction`, which gates the generated Training dialogue topic. Vendor barter gold becomes carried Gold001 (no TES5 field). See [dialogue_conversion_notes.md](../commentary/tes5_import_dialogue.md) (Barter/Training services).
- **VendorItem keywords (2026-07-10)**: Skyrim vendors only buy/sell items whose keywords appear in their faction's VEND formlist — converted items with NO keywords are invisible in the barter menu ("vendor missing nearly their entire inventory"). Every sellable converter now emits KSIZ/KWDA from `VENDOR_KYWD` (record_types/common.py): WEAP→Weapon (type 4→Staff), AMMO→Arrow, ARMO/CLOT→Armor/Clothing (TES4 biped bits 6/7/8 ring/amulet→Jewelry), BOOK→Book (flag 0x01→Scroll), ALCH→Potion/Poison/Food, INGR→Ingredient, SLGM→SoulGem, SGST→Scroll, APPA/MISC→Clutter, KEYM→Key. The service-bit→FLST table in actors.py must stay in sync (Weapons list includes Arrow; Books includes Scroll; Ingredients includes Food; Misc includes Clutter).
- **Skill Weight Algorithm** (from Skyblivion):
  1. Start with all TES5 skills at weight 0
  2. Specialization (Combat/Magic/Stealth) adds +2 to corresponding TES5 skills
  3. Two primary attributes: each attribute's associated skills get +1
  4. Seven major skills: mapped to TES5 equivalents, each gets +3
  - Attribute→Skill mapping: Str→OneHanded/TwoHanded/Smithing, Int→Conjuration/Alchemy, Wil→Restoration/Alteration, Agi→Sneak/LightArmor/Lockpicking, Spd→Pickpocket/Speechcraft, End→Block/HeavyArmor, Per→Destruction/Illusion/Marksman/Enchanting, Luck→all skills +1
