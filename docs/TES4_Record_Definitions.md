# TES4 (Oblivion) Complete Binary Record Definitions

Extracted from xEdit source: `wbDefinitionsTES4.pas` and `wbDefinitionsCommon.pas`.

**Type abbreviations used below:**
- `string` = null-terminated string (variable length)
- `zstring` = null-terminated string
- `u8/u16/u32` = unsigned integer (1/2/4 bytes)
- `s8/s16/s32` = signed integer (1/2/4 bytes)
- `f32` = IEEE 754 float (4 bytes)
- `formid` = u32 FormID reference
- `rgba` = struct { u8 red, u8 green, u8 blue, u8 unused } (4 bytes)
- `bytes[N]` = raw byte array of N bytes

---

## Record Header (Common to ALL records)

Every record begins with a 20-byte header:

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | char[4] | Signature (e.g. 'STAT', 'NPC_') |
| 4 | 4 | u32 | Data Size (size of subrecords, NOT including this header) |
| 8 | 4 | u32 | Record Flags |
| 12 | 4 | u32 | FormID |
| 16 | 4 | bytes[4] | Version Control Info |

**Record Flags:**
- 0x00000001 = ESM
- 0x00000020 = Deleted
- 0x00000040 = Border Region / Actor Value
- 0x00000080 = Turn Off Fire / Actor Value
- 0x00000200 = Casts Shadows
- 0x00000400 = Quest Item / Persistent Reference / Show in Menu
- 0x00000800 = Initially Disabled
- 0x00001000 = Ignored
- 0x00008000 = Visible When Distant
- 0x00020000 = Dangerous / Off Limits (Interior)
- 0x00040000 = Compressed
- 0x00080000 = Can't Wait

Each subrecord has a 6-byte header: `char[4] signature`, `u16 dataSize`.

---

## TES4 — Main File Header

| Subrecord | Type | Description |
|-----------|------|-------------|
| HEDR | struct(12) | Header |
| OFST | bytes[] | Offset Data (optional) |
| DELE | bytes[] | Unknown (optional) |
| CNAM | string | Author |
| SNAM | string | Description |
| MAST | string | Master filename (repeating pair with DATA) |
| DATA | bytes[8] | Unused (paired with MAST) |

**HEDR struct (12 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | f32 | Version (1.0 for Oblivion) |
| 4 | 4 | u32 | Number of Records |
| 8 | 4 | u32 | Next Object ID |

---

## ACHR — Placed NPC

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| NAME | formid | Yes | Base NPC_ record |
| XPCI | formid | | Unused (CELL ref) |
| FULL | string | | Unused |
| XLOD | f32[3] | | Distant LOD Data (3 floats) |
| XESP | struct(8) | | Enable Parent |
| XMRC | formid | | Merchant Container (→REFR) |
| XHRS | formid | | Horse (→ACRE) |
| XRGD | bytes[] | | Ragdoll Data |
| XSCL | f32 | | Scale |
| DATA | struct(24) | Yes | Position/Rotation |

**XESP struct (8 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Reference (PLYR/REFR/ACRE/ACHR) |
| 4 | 1 | u8 | Flags: 0x01=Set Enable State to Opposite of Parent |
| 5 | 3 | bytes[3] | Unused |

**DATA Position/Rotation (24 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | f32 | Position X |
| 4 | 4 | f32 | Position Y |
| 8 | 4 | f32 | Position Z |
| 12 | 4 | f32 | Rotation X (radians) |
| 16 | 4 | f32 | Rotation Y (radians) |
| 20 | 4 | f32 | Rotation Z (radians) |

---

## ACRE — Placed Creature

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| NAME | formid | Yes | Base CREA record |
| XOWN | formid | | Owner (FACT/NPC_) |
| XRNK | s32 | | Faction rank |
| XGLB | formid | | Global variable (GLOB) |
| XRGD | bytes[] | | Ragdoll Data |
| XLOD | f32[3] | | Distant LOD Data |
| XESP | struct(8) | | Enable Parent |
| XSCL | f32 | | Scale |
| DATA | struct(24) | Yes | Position/Rotation (same as ACHR) |

---

## ACTI — Activator

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL | string | | Model filename |
| MODB | f32 | | Bound radius |
| MODT | bytes[] | | Model texture info |
| SCRI | formid | | Script (→SCPT) |
| SNAM | formid | | Sound (→SOUN) |

---

## ALCH — Potion

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| OBME | struct(32) | | OBME data (optional) |
| FULL | string | | Name |
| MODL | string | | Model filename |
| MODB | f32 | | Bound radius |
| MODT | bytes[] | | Model texture info |
| ICON | string | | Icon filename |
| SCRI | formid | | Script (→SCPT) |
| DATA | f32 | Yes | Weight |
| ENIT | struct(8) | Yes | Enchantment Info |
| *Effects* | | | See Effects structure below |

**ENIT struct (8 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | s32 | Value |
| 4 | 1 | u8 | Flags: 0x01=No auto-calc, 0x02=Food item |
| 5 | 3 | bytes[3] | Unused |

---

## AMMO — Ammunition

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| ENAM | formid | | Enchantment (→ENCH) |
| ANAM | u16 | | Enchantment Points |
| DATA | struct(20) | Yes | Data |

**DATA struct (20 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | f32 | Speed |
| 4 | 1 | u8 | Flags: 0x01=Ignores Normal Weapon Resistance |
| 5 | 3 | bytes[3] | Unused |
| 8 | 4 | u32 | Value |
| 12 | 4 | f32 | Weight |
| 16 | 2 | u16 | Damage |

---

## ANIO — Animated Object

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| MODL/MODB/MODT | | | Model |
| DATA | formid | Yes | IDLE animation |

---

## APPA — Alchemical Apparatus

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| SCRI | formid | | Script (→SCPT) |
| DATA | struct(13) | Yes | Data |

**DATA struct (13 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Type: 0=Mortar and Pestle, 1=Alembic, 2=Calcinator, 3=Retort |
| 1 | 4 | u32 | Value |
| 5 | 4 | f32 | Weight |
| 9 | 4 | f32 | Quality |

---

## ARMO — Armor

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| SCRI | formid | | Script (→SCPT) |
| ENAM | formid | | Enchantment (→ENCH) |
| ANAM | u16 | | Enchantment Points |
| BMDT | struct(4) | Yes | Biped Model Data |
| MODL/MODB/MODT | | | Male Biped Model |
| MOD2/MO2B/MO2T | | | Male World Model |
| ICON | string | | Male Icon filename |
| MOD3/MO3B/MO3T | | | Female Biped Model |
| MOD4/MO4B/MO4T | | | Female World Model |
| ICO2 | string | | Female Icon filename |
| DATA | struct(14) | Yes | Data |

**BMDT struct (4 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 2 | u16 | Biped Flags (see below) |
| 2 | 1 | u8 | General Flags: 0x01=Hide Rings, 0x02=Hide Amulets, 0x40=Non-Playable, 0x80=Heavy Armor |
| 3 | 1 | bytes[1] | Unused |

**Biped Flags (u16):**
- 0x0001 = Head
- 0x0002 = Hair
- 0x0004 = Upper Body
- 0x0008 = Lower Body
- 0x0010 = Hand
- 0x0020 = Foot
- 0x0040 = Right Ring
- 0x0080 = Left Ring
- 0x0100 = Amulet
- 0x0200 = Weapon
- 0x0400 = Back Weapon
- 0x0800 = Side Weapon
- 0x1000 = Quiver
- 0x2000 = Shield
- 0x4000 = Torch
- 0x8000 = Tail

**DATA struct (14 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 2 | u16 | Armor Rating (divided by 100 for display) |
| 2 | 4 | u32 | Value |
| 6 | 4 | u32 | Health |
| 10 | 4 | f32 | Weight |

---

## BOOK — Book

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| SCRI | formid | | Script (→SCPT) |
| ENAM | formid | | Enchantment (→ENCH) |
| ANAM | u16 | | Enchantment Points |
| DESC | string | | Book text |
| DATA | struct(10) | Yes | Data |

**DATA struct (10 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Flags: 0x01=Scroll, 0x02=Can't be taken |
| 1 | 1 | s8 | Teaches (skill enum, -1=None, 0=Armorer..20=Speechcraft) |
| 2 | 4 | u32 | Value |
| 6 | 4 | f32 | Weight |

---

## BSGN — Birthsign

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| ICON | string | | Icon filename |
| DESC | string | | Description |
| SPLO | formid | | Spell (→SPEL/LVSP) — repeating |

---

## CELL — Cell

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| DATA | u8 | Yes | Flags |
| XCLC | struct(8) | | Grid (exterior only) |
| XCLL | struct(36) | | Lighting (interior only) |
| XCLR | formid[] | | Regions (array of →REGN) |
| XCMT | u8 | | Music: 0=Default, 1=Public, 2=Dungeon |
| XCLW | f32 | | Water Height |
| XCCM | formid | | Climate (→CLMT) |
| XCWT | formid | | Water (→WATR) |
| XOWN | formid | | Owner (→FACT/NPC_) |
| XRNK | s32 | | Faction rank |
| XGLB | formid | | Global variable |

**DATA flags (u8):**
- 0x01 = Is Interior Cell
- 0x02 = Has Water
- 0x04 = Invert Fast Travel Behavior
- 0x08 = Force Hide Land (ext) / Oblivion Interior (int)
- 0x20 = Public Place
- 0x40 = Hand Changed
- 0x80 = Behave Like Exterior

**XCLC Grid struct (8 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | s32 | X |
| 4 | 4 | s32 | Y |

**XCLL Lighting struct (36 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | rgba | Ambient Color |
| 4 | 4 | rgba | Directional Color |
| 8 | 4 | rgba | Fog Color |
| 12 | 4 | f32 | Fog Near |
| 16 | 4 | f32 | Fog Far |
| 20 | 4 | s32 | Directional Rotation XY |
| 24 | 4 | s32 | Directional Rotation Z |
| 28 | 4 | f32 | Directional Fade (default 1.0) |
| 32 | 4 | f32 | Fog Clip Distance |

---

## CLAS — Class

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| DESC | string | | Description |
| ICON | string | | Icon filename |
| DATA | struct(52+) | Yes | Class Data |

**DATA struct (variable, up to 52 bytes; minimum fields required up to index 5):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 8 | s32[2] | Primary Attributes (2x ActorValueEnum) |
| 8 | 4 | u32 | Specialization: 0=Combat, 1=Magic, 2=Stealth |
| 12 | 28 | s32[7] | Major Skills (7x ActorValueEnum) |
| 40 | 4 | u32 | Flags: 0x01=Playable, 0x02=Guard |
| 44 | 4 | u32 | Buys/Sells and Services (ServiceFlags) |
| 48 | 1 | s8 | Teaches (skill enum) |
| 49 | 1 | u8 | Maximum Training Level |
| 50 | 2 | u16 | Unused |

---

## CLMT — Climate

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| WLST | struct[] | | Weather Types array |
| FNAM | string | | Sun Texture |
| GNAM | string | | Sun Glare Texture |
| MODL/MODB/MODT | | | Model |
| TNAM | struct(6) | Yes | Timing |

**WLST entry (8 bytes each):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Weather (→WTHR) |
| 4 | 4 | s32 | Chance |

**TNAM Timing struct (6 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Sunrise Begin (time = value/6 hours) |
| 1 | 1 | u8 | Sunrise End |
| 2 | 1 | u8 | Sunset Begin |
| 3 | 1 | u8 | Sunset End |
| 4 | 1 | u8 | Volatility |
| 5 | 1 | u8 | Moons/Phase Length (bit6=Masser, bit7=Secunda, bits0-5=phase length) |

---

## CLOT — Clothing

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| SCRI | formid | | Script (→SCPT) |
| ENAM | formid | | Enchantment (→ENCH) |
| ANAM | u16 | | Enchantment Points |
| BMDT | struct(4) | Yes | Biped Model Data (same as ARMO) |
| MODL/MODB/MODT | | | Male Biped Model |
| MOD2/MO2B/MO2T | | | Male World Model |
| ICON | string | | Male Icon filename |
| MOD3/MO3B/MO3T | | | Female Biped Model |
| MOD4/MO4B/MO4T | | | Female World Model |
| ICO2 | string | | Female Icon filename |
| DATA | struct(8) | Yes | Data |

**DATA struct (8 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Value |
| 4 | 4 | f32 | Weight |

---

## CONT — Container

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| SCRI | formid | | Script (→SCPT) |
| CNTO | struct(8) | | Items — repeating |
| DATA | struct(5) | Yes | Data |
| SNAM | formid | | Open Sound (→SOUN) |
| QNAM | formid | | Close Sound (→SOUN) |

**CNTO Item struct (8 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Item |
| 4 | 4 | s32 | Count |

**DATA struct (5 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Flags: 0x02=Respawns |
| 1 | 4 | f32 | Weight |

---

## CREA — Creature

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| CNTO | struct(8) | | Items — repeating |
| SPLO | formid | | Spells — repeating |
| NIFZ | string[] | | Model List (null-separated strings) |
| NIFT | bytes[] | | Model List Textures |
| ACBS | struct(16) | Yes | Configuration |
| SNAM | struct(8) | | Factions — repeating |
| INAM | formid | | Death Item (→LVLI) |
| SCRI | formid | | Script (→SCPT) |
| AIDT | struct(12) | Yes | AI Data |
| PKID | formid | | AI Package (→PACK) — repeating |
| KFFZ | string[] | | Animations (null-separated strings) |
| DATA | struct(18) | Yes | Creature Data |
| RNAM | u8 | Yes | Attack Reach |
| ZNAM | formid | | Combat Style (→CSTY) |
| TNAM | f32 | Yes | Turning Speed |
| BNAM | f32 | Yes | Base Scale |
| WNAM | f32 | Yes | Foot Weight |
| NAM0 | string | | Blood Spray |
| NAM1 | string | | Blood Decal |
| CSCR | formid | | Inherits Sounds From (→CREA) |
| CSDT/CSDI/CSDC | | | Sound Types (see below) |

**ACBS struct (16 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Flags (see below) |
| 4 | 2 | u16 | Base Spell Points |
| 6 | 2 | u16 | Fatigue |
| 8 | 2 | u16 | Barter Gold |
| 10 | 2 | s16 | Level (offset) |
| 12 | 2 | u16 | Calc Min |
| 14 | 2 | u16 | Calc Max |

**CREA ACBS Flags:**
- 0x000001 = Biped
- 0x000002 = Essential
- 0x000004 = Weapon & Shield
- 0x000008 = Respawn
- 0x000010 = Swims
- 0x000020 = Flies
- 0x000040 = Walks
- 0x000080 = PC Level Offset
- 0x000200 = No Low Level Processing
- 0x000800 = No Blood Spray
- 0x001000 = No Blood Decal
- 0x008000 = No Head
- 0x010000 = No Right Arm
- 0x020000 = No Left Arm
- 0x040000 = No Combat in Water
- 0x080000 = No Shadow
- 0x100000 = No Corpse Check

**AIDT AI Data struct (12 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Aggression |
| 1 | 1 | u8 | Confidence |
| 2 | 1 | u8 | Energy Level |
| 3 | 1 | u8 | Responsibility |
| 4 | 4 | u32 | Buys/Sells and Services (ServiceFlags) |
| 8 | 1 | s8 | Teaches (skill enum) |
| 9 | 1 | u8 | Max Training Level |
| 10 | 2 | bytes[2] | Unused |

**DATA Creature Data struct (18 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Type: 0=Creature, 1=Daedra, 2=Undead, 3=Humanoid, 4=Horse, 5=Giant |
| 1 | 1 | u8 | Combat Skill |
| 2 | 1 | u8 | Magic Skill |
| 3 | 1 | u8 | Stealth Skill |
| 4 | 1 | u8 | Soul (SoulGemEnum: 0=None..5=Grand) |
| 5 | 1 | bytes[1] | Unused |
| 6 | 2 | u16 | Health |
| 8 | 2 | bytes[2] | Unused |
| 10 | 2 | u16 | Attack Damage |
| 12 | 1 | u8 | Strength |
| 13 | 1 | u8 | Intelligence |
| 14 | 1 | u8 | Willpower |
| 15 | 1 | u8 | Agility |
| 16 | 1 | u8 | Speed |
| 17 | 1 | u8 | Endurance |
| 18 | 1 | u8 | Personality |
| 19 | 1 | u8 | Luck |

*(Note: 20 bytes total including Personality and Luck)*

**Sound Types (repeating CSDT group):**
- CSDT (u32): Type enum: 0=Left Foot, 1=Right Foot, 2=Left Back Foot, 3=Right Back Foot, 4=Idle, 5=Aware, 6=Attack, 7=Hit, 8=Death, 9=Weapon
- CSDI (formid): Sound (→SOUN)
- CSDC (u8): Sound Chance

---

## CSTY — Combat Style

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| CSTD | struct(124) | Yes | Standard (min fields up to offset 31) |
| CSAD | struct(84) | | Advanced |

**CSTD Standard struct (124 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Dodge % Chance |
| 1 | 1 | u8 | Left/Right % Chance |
| 2 | 2 | bytes[2] | Unused |
| 4 | 4 | f32 | Dodge L/R Timer Min |
| 8 | 4 | f32 | Dodge L/R Timer Max |
| 12 | 4 | f32 | Dodge Forward Timer Min |
| 16 | 4 | f32 | Dodge Forward Timer Max |
| 20 | 4 | f32 | Dodge Back Timer Min |
| 24 | 4 | f32 | Dodge Back Timer Max |
| 28 | 4 | f32 | Idle Timer Min |
| 32 | 4 | f32 | Idle Timer Max |
| 36 | 1 | u8 | Block % Chance |
| 37 | 1 | u8 | Attack % Chance |
| 38 | 2 | bytes[2] | Unused |
| 40 | 4 | f32 | Recoil/Stagger Bonus to Attack |
| 44 | 4 | f32 | Unconscious Bonus to Attack |
| 48 | 4 | f32 | Hand-to-Hand Bonus to Attack |
| 52 | 1 | u8 | Power Attack % Chance |
| 53 | 3 | bytes[3] | Unused |
| 56 | 4 | f32 | Recoil/Stagger Bonus to Power |
| 60 | 4 | f32 | Unconscious Bonus to Power Attack |
| 64 | 1 | u8 | Power Attack - Normal |
| 65 | 1 | u8 | Power Attack - Forward |
| 66 | 1 | u8 | Power Attack - Back |
| 67 | 1 | u8 | Power Attack - Left |
| 68 | 1 | u8 | Power Attack - Right |
| 69 | 3 | bytes[3] | Unused |
| 72 | 4 | f32 | Hold Timer Min |
| 76 | 4 | f32 | Hold Timer Max |
| 80 | 1 | u8 | Flags 1 (see below) |
| 81 | 1 | u8 | Acrobatic Dodge % Chance |
| 82 | 2 | bytes[2] | Unused |
| 84 | 4 | f32 | Range Mult (Optimal) |
| 88 | 4 | f32 | Range Mult (Max) |
| 92 | 4 | f32 | Switch Distance (Melee) |
| 96 | 4 | f32 | Switch Distance (Ranged) |
| 100 | 4 | f32 | Buff Standoff Distance |
| 104 | 4 | f32 | Ranged Standoff Distance |
| 108 | 4 | f32 | Group Standoff Distance |
| 112 | 1 | u8 | Rushing Attack % Chance |
| 113 | 3 | bytes[3] | Unused |
| 116 | 4 | f32 | Rushing Attack Distance Mult |
| 120 | 4 | u32 | Flags 2: 0x01=Do Not Acquire |

**Flags 1:**
- 0x01 = Advanced
- 0x02 = Choose Attack using % Chance
- 0x04 = Ignore Allies in Area
- 0x08 = Will Yield
- 0x10 = Rejects Yields
- 0x20 = Fleeing Disabled
- 0x40 = Prefers Ranged
- 0x80 = Melee Alert OK

**CSAD Advanced struct (84 bytes):** 21 consecutive f32 fields:
Dodge Fatigue Mod Mult, Dodge Fatigue Mod Base, Encumb Speed Mod Base, Encumb Speed Mod Mult, Dodge While Under Attack Mult, Dodge Not Under Attack Mult, Dodge Back While Under Attack Mult, Dodge Back Not Under Attack Mult, Dodge Forward While Attacking Mult, Dodge Forward Not Attacking Mult, Block Skill Modifier Mult, Block Skill Modifier Base, Block While Under Attack Mult, Block Not Under Attack Mult, Attack Skill Modifier Mult, Attack Skill Modifier Base, Attack While Under Attack Mult, Attack Not Under Attack Mult, Attack During Block Mult, Power Att Fatigue Mod Base, Power Att Fatigue Mod Mult

---

## DIAL — Dialog Topic

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| QSTI | formid | | Quest (→QUST) — repeating |
| QSTR | formid | | Quest? (→QUST) — repeating |
| FULL | string | | Name |
| DATA | u8 | Yes | Type: 0=Topic, 1=Conversation, 2=Combat, 3=Persuasion, 4=Detection, 5=Service, 6=Miscellaneous |

---

## DOOR — Door

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| SCRI | formid | | Script (→SCPT) |
| SNAM | formid | | Open Sound (→SOUN) |
| ANAM | formid | | Close Sound (→SOUN) |
| BNAM | formid | | Loop Sound (→SOUN) |
| FNAM | u8 | Yes | Flags: 0x01=Oblivion Gate, 0x02=Automatic, 0x04=Hidden, 0x08=Minimal Use |
| TNAM | formid | | Random Teleport Destinations (→CELL/WRLD) — repeating |

---

## EFSH — Effect Shader

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| ICON | string | | Fill Texture |
| ICO2 | string | | Particle Shader Texture |
| DATA | struct(224) | Yes | Shader Data (min fields up to 25) |

**DATA struct (224 bytes):** Very large structure with shader parameters.

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Flags: 0x01=No Membrane Shader, 0x08=No Particle Shader, 0x10=Edge Effect Inverse, 0x20=Membrane Affect Skin Only |
| 1 | 3 | bytes[3] | Unused |
| 4 | 4 | u32 | Membrane Source Blend Mode |
| 8 | 4 | u32 | Membrane Blend Operation |
| 12 | 4 | u32 | Membrane Z Test Function |
| 16 | 4 | rgba | Fill/Texture Effect Color |
| 20 | 4 | f32 | Fill Alpha Fade In Time |
| 24 | 4 | f32 | Fill Full Alpha Time |
| 28 | 4 | f32 | Fill Alpha Fade Out Time |
| 32 | 4 | f32 | Fill Persistent Alpha Ratio |
| 36 | 4 | f32 | Fill Alpha Pulse Amplitude |
| 40 | 4 | f32 | Fill Alpha Pulse Frequency |
| 44 | 4 | f32 | Fill Texture Anim Speed U |
| 48 | 4 | f32 | Fill Texture Anim Speed V |
| 52 | 4 | f32 | Edge Effect Fall Off |
| 56 | 4 | rgba | Edge Effect Color |
| 60 | 4 | f32 | Edge Alpha Fade In Time |
| 64 | 4 | f32 | Edge Full Alpha Time |
| 68 | 4 | f32 | Edge Alpha Fade Out Time |
| 72 | 4 | f32 | Edge Persistent Alpha Ratio |
| 76 | 4 | f32 | Edge Alpha Pulse Amplitude |
| 80 | 4 | f32 | Edge Alpha Pulse Frequency |
| 84 | 4 | f32 | Fill Full Alpha Ratio |
| 88 | 4 | f32 | Edge Full Alpha Ratio |
| 92 | 4 | u32 | Membrane Dest Blend Mode |
| 96 | 4 | u32 | Particle Source Blend Mode |
| 100 | 4 | u32 | Particle Blend Operation |
| 104 | 4 | u32 | Particle Z Test Function |
| 108 | 4 | u32 | Particle Dest Blend Mode |
| 112 | 4 | f32 | Particle Birth Ramp Up Time |
| 116 | 4 | f32 | Particle Full Birth Time |
| 120 | 4 | f32 | Particle Birth Ramp Down Time |
| 124 | 4 | f32 | Particle Full Birth Ratio |
| 128 | 4 | f32 | Particle Persistent Birth Ratio |
| 132 | 4 | f32 | Particle Lifetime |
| 136 | 4 | f32 | Particle Lifetime +/- |
| 140 | 4 | f32 | Particle Initial Speed Along Normal |
| 144 | 4 | f32 | Particle Acceleration Along Normal |
| 148 | 4 | f32 | Particle Initial Velocity 1 |
| 152 | 4 | f32 | Particle Initial Velocity 2 |
| 156 | 4 | f32 | Particle Initial Velocity 3 |
| 160 | 4 | f32 | Particle Acceleration 1 |
| 164 | 4 | f32 | Particle Acceleration 2 |
| 168 | 4 | f32 | Particle Acceleration 3 |
| 172 | 4 | f32 | Particle Scale Key 1 |
| 176 | 4 | f32 | Particle Scale Key 2 |
| 180 | 4 | f32 | Particle Scale Key 1 Time |
| 184 | 4 | f32 | Particle Scale Key 2 Time |
| 188 | 4 | rgba | Color Key 1 |
| 192 | 4 | rgba | Color Key 2 |
| 196 | 4 | rgba | Color Key 3 |
| 200 | 4 | f32 | Color Key 1 Alpha |
| 204 | 4 | f32 | Color Key 2 Alpha |
| 208 | 4 | f32 | Color Key 3 Alpha |
| 212 | 4 | f32 | Color Key 1 Time |
| 216 | 4 | f32 | Color Key 2 Time |
| 220 | 4 | f32 | Color Key 3 Time |

---

## ENCH — Enchantment

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| OBME | struct(32) | | OBME data (optional) |
| FULL | string | | Name |
| ENIT | struct(16) | Yes | Enchantment Info |
| *Effects* | | | See Effects structure |

**ENIT struct (16 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Type: 0=Scroll, 1=Staff, 2=Weapon, 3=Apparel |
| 4 | 4 | u32 | Charge Amount |
| 8 | 4 | u32 | Enchant Cost |
| 12 | 1 | u8 | Flags: 0x01=Manual Enchant Cost (Autocalc Off) |
| 13 | 3 | bytes[3] | Unused |

---

## EYES — Eyes

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| ICON | string | Yes | Texture |
| DATA | u8 | Yes | Flags: 0x01=Playable |

---

## FACT — Faction

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| XNAM | struct(8) | | Relations — repeating (Faction formid + s32 Modifier) |
| DATA | u8 | Yes | Flags: 0x01=Hidden from Player, 0x02=Evil, 0x04=Special Combat |
| CNAM | f32 | Yes | Crime Gold Multiplier (default 1.0) |
| RNAM | s32 | | Rank # — repeating rank group |
| MNAM | string | | Male Rank Name |
| FNAM | string | | Female Rank Name |
| INAM | string | | Insignia |

**XNAM Relation struct (8 bytes in TES4, no combat reaction):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Faction (→FACT/RACE) |
| 4 | 4 | s32 | Disposition Modifier |

---

## FLOR — Flora

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| SCRI | formid | | Script (→SCPT) |
| PFIG | formid | | Ingredient (→INGR) |
| PFPC | struct(4) | Yes | Seasonal Production |

**PFPC struct (4 bytes):** u8 Spring, u8 Summer, u8 Fall, u8 Winter

---

## FURN — Furniture

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| SCRI | formid | | Script (→SCPT) |
| MNAM | bytes[] | Yes | Marker Flags (variable length bitmask) |

---

## GLOB — Global

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FNAM | u8 | Yes | Type: 's'=Short, 'l'=Long, 'f'=Float |
| FLTV | f32 | Yes | Value |

---

## GMST — Game Setting

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| DATA | union | Yes | Value (type determined by first char of EDID: s=string, i/l=s32, f=f32) |

---

## GRAS — Grass

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| MODL/MODB/MODT | | | Model |
| DATA | struct(32) | Yes | Data |

**DATA struct (32 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Density |
| 1 | 1 | u8 | Min Slope |
| 2 | 1 | u8 | Max Slope |
| 3 | 1 | bytes[1] | Unused |
| 4 | 2 | u16 | Unit From Water Amount |
| 6 | 2 | bytes[2] | Unused |
| 8 | 4 | u32 | Unit From Water Type (0-7 enum) |
| 12 | 4 | f32 | Position Range |
| 16 | 4 | f32 | Height Range |
| 20 | 4 | f32 | Color Range |
| 24 | 4 | f32 | Wave Period |
| 28 | 1 | u8 | Flags: 0x01=Vertex Lighting, 0x02=Uniform Scaling, 0x04=Fit to Slope |
| 29 | 3 | bytes[3] | Unused |

---

## HAIR — Hair

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | Yes | Texture |
| DATA | u8 | Yes | Flags: 0x01=Playable, 0x02=Not Male, 0x04=Not Female, 0x08=Fixed |

---

## IDLE — Idle Animation

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| MODL/MODB/MODT | | | Model |
| CTDA/CTDT | | | Conditions — repeating |
| ANAM | u8 | Yes | Animation Group Section (bit7=must return file; 0-6=body section enum) |
| DATA | formid[2] | Yes | Related Idle Animations: [Parent, Previous Sibling] |

---

## INFO — Dialog Response

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| DATA | struct(3) | Yes | Response data |
| QSTI | formid | Yes | Quest (→QUST) |
| TPIC | formid | | Topic (→DIAL) |
| PNAM | formid | | Previous INFO (→INFO/NULL) |
| NAME | formid | | Add Topics (→DIAL) — repeating |
| TRDT | struct(16) | | Response Data — repeating (paired with NAM1/NAM2) |
| NAM1 | string | | Response Text |
| NAM2 | string | | Actor Notes |
| CTDA/CTDT | | | Conditions — repeating |
| TCLT | formid | | Choices (→DIAL) — repeating |
| TCLF | formid | | Link From (→DIAL) — repeating |
| *Result Script* | | | SCHR/SCDA/SCTX/SCRO/SCRV |

**DATA struct (3 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Type (0-6, same as DIAL) |
| 1 | 1 | u8 | Next Speaker: 0=Target, 1=Self, 2=Either |
| 2 | 1 | u8 | Flags: 0x01=Goodbye, 0x02=Random, 0x04=Say Once, 0x08=Run Immediately, 0x10=Info Refusal, 0x20=Random End, 0x40=Run for Rumors |

**TRDT Response struct (16 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Emotion Type: 0=Neutral, 1=Anger, 2=Disgust, 3=Fear, 4=Sad, 5=Happy, 6=Surprise |
| 4 | 4 | s32 | Emotion Value |
| 8 | 4 | bytes[4] | Unused |
| 12 | 1 | u8 | Response Number |
| 13 | 3 | bytes[3] | Unused |

---

## INGR — Ingredient

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| OBME | struct(32) | | OBME data (optional) |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| SCRI | formid | | Script (→SCPT) |
| DATA | f32 | Yes | Weight |
| ENIT | struct(8) | Yes | Same as ALCH ENIT |
| *Effects* | | | See Effects structure |

---

## KEYM — Key

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| SCRI | formid | | Script (→SCPT) |
| DATA | struct(8) | Yes | Data: s32 Value + f32 Weight |

---

## LAND — Landscape

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| DATA | u32 | | Flags: 0x01=Has Normals/Heights, 0x02=Has Vertex Colors, 0x04=Has Layers |
| VNML | bytes[3267] | | Vertex Normals: 33 rows × 33 cols × 3 bytes (X,Y,Z each u8) |
| VHGT | struct(1093) | | Vertex Height Map |
| VCLR | bytes[3267] | | Vertex Colors: 33 rows × 33 cols × 3 bytes (R,G,B each u8) |
| *Layers* | | | See Landscape Layers below |
| VTEX | formid[] | | Textures (array of →LTEX) |

**VHGT struct (1093 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | f32 | Offset |
| 4 | 1089 | s8[33][33] | Gradient data: 33 rows × 33 columns of signed bytes |
| 1093 | 3 | bytes[3] | Unused |

**Landscape Layers (repeating BTXT/ATXT+VTXT groups):**

Base Layer (BTXT, 8 bytes):

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Texture (→LTEX) |
| 4 | 1 | u8 | Quadrant: 0=BottomLeft, 1=BottomRight, 2=TopLeft, 3=TopRight |
| 5 | 1 | bytes[1] | Unused |
| 6 | 2 | s16 | Layer |

Alpha Layer (ATXT, 8 bytes — same layout as BTXT):
Followed by VTXT array of alpha data entries:

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 2 | u16 | Position |
| 2 | 2 | bytes[2] | Unused |
| 4 | 4 | f32 | Opacity |

---

## LIGH — Light

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| MODL/MODB/MODT | | | Model |
| SCRI | formid | | Script (→SCPT) |
| FULL | string | | Name |
| ICON | string | | Icon filename |
| DATA | struct(32) | Yes | Data (min fields up to 6) |
| FNAM | f32 | Yes | Fade Value (default 1.0) |
| SNAM | formid | | Sound (→SOUN) |

**DATA struct (32 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | s32 | Time |
| 4 | 4 | u32 | Radius |
| 8 | 4 | rgba | Color |
| 12 | 4 | u32 | Flags (see below) |
| 16 | 4 | f32 | Falloff Exponent |
| 20 | 4 | f32 | FOV |
| 24 | 4 | u32 | Value |
| 28 | 4 | f32 | Weight |

**Light Flags:**
- 0x001 = Dynamic
- 0x002 = Can be Carried
- 0x004 = Negative
- 0x008 = Flicker
- 0x020 = Off By Default
- 0x040 = Flicker Slow
- 0x080 = Pulse
- 0x100 = Pulse Slow
- 0x200 = Spot Light
- 0x400 = Spot Shadow

---

## LSCR — Load Screen

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| ICON | string | | Icon filename |
| DESC | string | | Description text |
| LNAM | struct(12) | | Locations — repeating |

**LNAM struct (12 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Direct (→CELL/WRLD/NULL) |
| 4 | 4 | formid | World (→WRLD/NULL) |
| 8 | 2 | s16 | Grid Y |
| 10 | 2 | s16 | Grid X |

---

## LTEX — Landscape Texture

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| ICON | string | | Texture filename |
| HNAM | struct(3) | Yes | Havok Data |
| SNAM | u8 | Yes | Texture Specular Exponent (default 30) |
| GNAM | formid | | Grasses (→GRAS) — repeating |

**HNAM struct (3 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Material Type: 0=Stone, 1=Cloth, 2=Dirt, 3=Glass, 4=Grass, 5=Metal, 6=Organic, 7=Skin, 8=Water, 9=Wood, 10=Heavy Stone, 11=Heavy Metal, 12=Heavy Wood, 13=Chain, 14=Snow |
| 1 | 1 | u8 | Friction (default 30) |
| 2 | 1 | u8 | Restitution (default 30) |

---

## LVLC — Leveled Creature

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| LVLD | u8 | Yes | Chance None |
| LVLF | u8 | Yes | Flags: 0x01=Calc from all levels ≤ player, 0x02=Calc for each item in count |
| LVLO | struct(12) | Yes | Entries — repeating |
| SCRI | formid | | Script (→SCPT) |
| TNAM | formid | | Template (→NPC_/CREA) |

**LVLO entry struct (12 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 2 | s16 | Level |
| 2 | 2 | bytes[2] | Unused |
| 4 | 4 | formid | Reference (→NPC_/CREA/LVLC) |
| 8 | 2 | s16 | Count |
| 10 | 2 | bytes[2] | Unused |

---

## LVLI — Leveled Item

Same structure as LVLC but LVLO references are (→ARMO/AMMO/MISC/WEAP/INGR/SLGM/SGST/BOOK/LVLI/KEYM/CLOT/ALCH/APPA/LIGH). Has DATA (1 byte, unused) instead of SCRI/TNAM.

---

## LVSP — Leveled Spell

Same structure as LVLC but LVLO references are (→SPEL/LVSP). LVLF adds flag 0x04=Use All Spells. No SCRI/TNAM.

---

## MGEF — Magic Effect

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | char[4] | | Magic Effect Code (4-char string, NOT normal EditorID) |
| OBME | struct | | OBME data (optional) |
| EDDX | string | | EditorID (OBME only) |
| FULL | string | | Name |
| DESC | string | | Description |
| ICON | string | | Icon filename |
| MODL/MODB/MODT | | | Model |
| DATA | struct(64+) | Yes | Data (variable, min fields 10) |
| ESCE | char[4][] | | Counter Effects (array of 4-char effect codes) |

**DATA struct (64 bytes minimum, up to ~68 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Flags (see below) |
| 4 | 4 | f32 | Base Cost |
| 8 | 4 | formid/s32 | Assoc. Item (type depends on flags: Weapon/Armor/Creature/ActorValue) |
| 12 | 4 | s32 | Magic School: 0=Alteration, 1=Conjuration, 2=Destruction, 3=Illusion, 4=Mysticism, 5=Restoration |
| 16 | 4 | s32 | Resist Value (ActorValueEnum) |
| 20 | 2 | u16 | Counter Effect Count |
| 22 | 2 | bytes[2] | Unused |
| 24 | 4 | formid | Light (→LIGH) |
| 28 | 4 | f32 | Projectile Speed |
| 32 | 4 | formid | Effect Shader (→EFSH) |
| 36 | 4 | formid | Enchant Effect (→EFSH) |
| 40 | 4 | formid | Casting Sound (→SOUN) |
| 44 | 4 | formid | Bolt Sound (→SOUN) |
| 48 | 4 | formid | Hit Sound (→SOUN) |
| 52 | 4 | formid | Area Sound (→SOUN) |
| 56 | 4 | f32 | Constant Effect Enchantment Factor |
| 60 | 4 | f32 | Constant Effect Barter Factor |

**MGEF Flags:**
- 0x00000001 = Hostile
- 0x00000002 = Recover
- 0x00000004 = Detrimental
- 0x00000008 = Magnitude %
- 0x00000010 = Self
- 0x00000020 = Touch
- 0x00000040 = Target
- 0x00000080 = No Duration
- 0x00000100 = No Magnitude
- 0x00000200 = No Area
- 0x00000400 = FX Persist
- 0x00000800 = Spellmaking
- 0x00001000 = Enchanting
- 0x00002000 = No Ingredient
- 0x00010000 = Use Weapon
- 0x00020000 = Use Armor
- 0x00040000 = Use Creature
- 0x00080000 = Use Skill
- 0x00100000 = Use Attribute
- 0x01000000 = Use Actor Value
- 0x02000000 = Spray Projectile (or Fog if Bolt also set)
- 0x04000000 = Bolt Projectile
- 0x08000000 = No Hit Effect

---

## MISC — Miscellaneous Item

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| SCRI | formid | | Script (→SCPT) |
| DATA | struct(8) | Yes | Data: s32 Value + f32 Weight |

---

## NPC_ — Non-Player Character

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ACBS | struct(16) | Yes | Configuration |
| SNAM | struct(8) | | Factions — repeating |
| INAM | formid | | Death Item (→LVLI) |
| RNAM | formid | Yes | Race (→RACE) |
| SPLO | formid | | Spells — repeating |
| SCRI | formid | | Script (→SCPT) |
| CNTO | struct(8) | | Items — repeating |
| AIDT | struct(12) | Yes | AI Data (same as CREA) |
| PKID | formid | | AI Packages — repeating |
| KFFZ | string[] | | Animations (null-separated) |
| CNAM | formid | Yes | Class (→CLAS) |
| DATA | struct(33) | Yes | Stats |
| HNAM | formid | | Hair (→HAIR) |
| LNAM | f32 | | Hair Length |
| ENAM | formid[] | | Eyes (array of →EYES) |
| HCLR | rgba | Yes | Hair Color |
| ZNAM | formid | | Combat Style (→CSTY) |
| FGGS | bytes[] | Yes | FaceGen Geometry-Symmetric |
| FGGA | bytes[] | Yes | FaceGen Geometry-Asymmetric |
| FGTS | bytes[] | Yes | FaceGen Texture-Symmetric |
| FNAM | bytes[] | | Unknown |

**NPC_ ACBS struct (16 bytes):** Same layout as CREA ACBS.

**NPC_ ACBS Flags:**
- 0x000001 = Female
- 0x000002 = Essential
- 0x000008 = Respawn
- 0x000010 = Auto-calc Stats
- 0x000080 = PC Level Offset
- 0x000200 = No Low Level Processing
- 0x002000 = No Rumors
- 0x004000 = Summonable
- 0x008000 = No Persuasion
- 0x100000 = Can Corpse Check

**SNAM Faction struct (8 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Faction (→FACT) |
| 4 | 1 | s8 | Rank |
| 5 | 3 | bytes[3] | Unused |

**DATA Stats struct (33 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Armorer |
| 1 | 1 | u8 | Athletics |
| 2 | 1 | u8 | Blade |
| 3 | 1 | u8 | Block |
| 4 | 1 | u8 | Blunt |
| 5 | 1 | u8 | Hand to Hand |
| 6 | 1 | u8 | Heavy Armor |
| 7 | 1 | u8 | Alchemy |
| 8 | 1 | u8 | Alteration |
| 9 | 1 | u8 | Conjuration |
| 10 | 1 | u8 | Destruction |
| 11 | 1 | u8 | Illusion |
| 12 | 1 | u8 | Mysticism |
| 13 | 1 | u8 | Restoration |
| 14 | 1 | u8 | Acrobatics |
| 15 | 1 | u8 | Light Armor |
| 16 | 1 | u8 | Marksman |
| 17 | 1 | u8 | Mercantile |
| 18 | 1 | u8 | Security |
| 19 | 1 | u8 | Sneak |
| 20 | 1 | u8 | Speechcraft |
| 21 | 2 | u16 | Health |
| 23 | 2 | bytes[2] | Unused |
| 25 | 1 | u8 | Strength |
| 26 | 1 | u8 | Intelligence |
| 27 | 1 | u8 | Willpower |
| 28 | 1 | u8 | Agility |
| 29 | 1 | u8 | Speed |
| 30 | 1 | u8 | Endurance |
| 31 | 1 | u8 | Personality |
| 32 | 1 | u8 | Luck |

---

## PACK — AI Package

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| PKDT | struct(4 or 8) | | General |
| PLDT | struct(12) | | Location |
| PSDT | struct(8) | | Schedule |
| PTDT | struct(12) | | Target |
| CTDA/CTDT | | | Conditions — repeating |

**PKDT struct (4-byte old format / 8-byte new format):**

Old (4 bytes): u16 Flags, u8 Type, u8 Unused
New (8 bytes): u32 Flags, u8 Type, bytes[3] Unused

**Package Type enum:** 0=Find, 1=Follow, 2=Escort, 3=Eat, 4=Sleep, 5=Wander, 6=Travel, 7=Accompany, 8=Use Item At, 9=Ambush, 10=Flee Not Combat, 11=Cast Magic

**PLDT Location struct (12 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | s32 | Type: 0=Near Ref, 1=In Cell, 2=Near Current, 3=Near Editor, 4=Object ID, 5=Object Type |
| 4 | 4 | formid/u32 | Location (type depends on Type field) |
| 8 | 4 | s32 | Radius |

**PSDT Schedule struct (8 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | s8 | Month |
| 1 | 1 | s8 | Day of Week (-1=Any, 0=Sundas..6=Loredas, 7-10=combined) |
| 2 | 1 | u8 | Date |
| 3 | 1 | s8 | Time |
| 4 | 4 | s32 | Duration |

**PTDT Target struct (12 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | s32 | Type: 0=Specific Reference, 1=Object ID, 2=Object Type |
| 4 | 4 | formid/u32 | Target |
| 8 | 4 | s32 | Count |

---

## PGRD — Path Grid

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| DATA | u16 | Yes | Point Count |
| PGRP | struct[] | Yes | Points (array of 16-byte structs) |
| PGAG | bytes[] | | Unknown |
| PGRR | s16[][] | | Point-to-Point Connections |
| PGRI | struct[] | | Inter-Cell Connections |
| PGRL | struct[] | | Point-to-Reference Mappings |

**PGRP Point (16 bytes):** f32 X, f32 Y, f32 Z, u8 ConnectionCount, bytes[3] Unused

**PGRI Inter-Cell Connection (14 bytes):** u16 Point, bytes[2] Unused, f32 X, f32 Y, f32 Z

---

## QUST — Quest

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| SCRI | formid | | Script (→SCPT) |
| FULL | string | | Name |
| ICON | string | | Icon filename |
| DATA | struct(2) | Yes | General |
| CTDA/CTDT | | | Conditions — repeating |
| *Stages* | | | Repeating stage groups |
| *Targets* | | | Repeating target groups |

**DATA struct (2 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Flags: 0x01=Start Game Enabled, 0x04=Allow Repeated Conversation Topics, 0x08=Allow Repeated Stages |
| 1 | 1 | u8 | Priority |

**Stage structure (repeating groups):**
- INDX (s16): Stage Index
- QSDT (u8): Stage Flags (0x01=Complete Quest)
- CTDA/CTDT: Conditions
- CNAM (string): Log Entry text
- Result Script: SCHR/SCDA/SCTX/SCRO/SCRV

**Target structure (repeating groups):**
- QSTA: struct { formid Target, u8 Flags (0x01=Compass marker ignores locks), bytes[3] Unused }

---

## RACE — Race

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| DESC | string | | Description |
| SPLO | formid | | Spells — repeating |
| XNAM | struct(8) | | Relations — repeating |
| DATA | struct(36) | Yes | Race Data |
| VNAM | struct(8) | | Voice: formid Male, formid Female |
| DNAM | struct(8) | | Default Hair: formid Male, formid Female |
| CNAM | u8 | Yes | Default Hair Color |
| PNAM | f32 | Yes | FaceGen Main Clamp |
| UNAM | f32 | Yes | FaceGen Face Clamp |
| ATTR | struct(16) | | Base Attributes |
| NAM0 | empty | | Face Data Marker |
| *Face Parts* | | | Head parts with INDX/MODL/ICON |
| NAM1 | empty | Yes | Body Data Marker |
| MNAM | empty | | Male Body Data Marker + Model + Body Parts |
| FNAM | empty | | Female Body Data Marker + Model + Body Parts |
| HNAM | formid[] | Yes | Hairs (array of →HAIR) |
| ENAM | formid[] | Yes | Eyes (array of →EYES) |
| FGGS | bytes[] | | FaceGen Geometry-Symmetric |
| FGGA | bytes[] | | FaceGen Geometry-Asymmetric |
| FGTS | bytes[] | | FaceGen Texture-Symmetric |
| SNAM | bytes[2] | Yes | Unknown |

**DATA struct (36 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 14 | struct[7] | Skill Boosts: 7× { s8 Skill (ActorValueEnum), s8 Boost } |
| 14 | 2 | bytes[2] | Unused |
| 16 | 4 | f32 | Male Height |
| 20 | 4 | f32 | Female Height |
| 24 | 4 | f32 | Male Weight |
| 28 | 4 | f32 | Female Weight |
| 32 | 4 | u32 | Flags: 0x01=Playable |

**ATTR struct (16 bytes):**

| Offset | Type | Field |
|--------|------|-------|
| 0-7 | u8[8] | Male: Str, Int, Wil, Agi, Spd, End, Per, Lck |
| 8-15 | u8[8] | Female: Str, Int, Wil, Agi, Spd, End, Per, Lck |

**Face Parts:** Repeating INDX(u32)/MODL/ICON groups. INDX enum: 0=Head, 1=Ear(Male), 2=Ear(Female), 3=Mouth, 4=Teeth(Lower), 5=Teeth(Upper), 6=Tongue, 7=Eye(Left), 8=Eye(Right)

**Body Parts:** Repeating INDX(u32)/ICON groups. INDX enum: 0=Upper Body, 1=Lower Body, 2=Hand, 3=Foot, 4=Tail

---

## REFR — Placed Object

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| NAME | formid | Yes | Base (any placeable record type) |
| XTEL | struct(28) | | Teleport Destination |
| XLOC | struct(16+) | | Lock Information |
| XOWN | formid | | Owner |
| XRNK | s32 | | Faction Rank |
| XGLB | formid | | Global Variable |
| XESP | struct(8) | | Enable Parent |
| XTRG | formid | | Target (→REFR/ACHR/ACRE) |
| XSED | struct(1+) | | SpeedTree Seed |
| XLOD | f32[3] | | Distant LOD Data |
| XCHG | f32 | | Charge |
| XHLT | s32 | | Health |
| XPCI | formid | | Unused (CELL ref) |
| FULL | string | | Unused |
| XLCM | s32 | | Level Modifier |
| XRTM | formid | | Teleport Marker (→REFR) |
| XACT | u32 | | Action Flag |
| XCNT | s32 | | Count |
| XMRK | empty | | Map Marker Data (starts marker group) |
| FNAM | u8 | | Map Flags: 0x01=Visible, 0x02=Can Travel To |
| FULL | string | | Map Marker Name |
| TNAM | struct(2) | | Map Marker Type |
| ONAM | empty | | Open by Default |
| XRGD | bytes[] | | Ragdoll Data |
| XSCL | f32 | | Scale |
| XSOL | u8 | | Contained Soul (SoulGemEnum) |
| DATA | struct(24) | Yes | Position/Rotation |

**XTEL Teleport Destination struct (28 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Door (→REFR) |
| 4 | 24 | struct | Position/Rotation (6× f32) |

**XLOC Lock struct (16+ bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Lock Level |
| 1 | 3 | bytes[3] | Unused |
| 4 | 4 | formid | Key (→KEYM) |
| 8 | 0 or 4 | bytes | Filler (depends on subrecord size) |
| 8/12 | 1 | u8 | Flags: 0x04=Leveled Lock |
| 9/13 | 3 | bytes[3] | Unused |

**TNAM Map Marker struct (2 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Type: 0=None, 1=Camp, 2=Cave, 3=City, 4=Elven Ruin, 5=Fort Ruin, 6=Mine, 7=Landmark, 8=Tavern, 9=Settlement, 10=Daedric Shrine, 11=Oblivion Gate, 12=Unknown(door) |
| 1 | 1 | bytes[1] | Unused |

---

## REGN — Region

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| ICON | string | | Icon filename |
| RCLR | rgba | Yes | Map Color |
| WNAM | formid | | Worldspace (→WRLD) |
| RPLI | u32 | | Edge Fall-off (region area start) |
| RPLD | struct[] | | Points: array of { f32 X, f32 Y } |
| RDAT | struct(8) | | Region Data Entry Header |
| RDOT | struct[] | | Objects |
| RDMP | string | | Map Name |
| RDGS | struct[] | | Grasses |
| RDMD | u32 | | Music Type |
| RDSD | struct[] | | Sounds |
| RDWT | struct[] | | Weather Types |

**RDAT Header struct (8 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Type: 2=Objects, 3=Weather, 4=Map, 6=Grass, 7=Sound |
| 4 | 1 | u8 | Flags: 0x01=Override |
| 5 | 1 | u8 | Priority |
| 6 | 2 | bytes[2] | Unused |

**RDOT Object (52 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Object |
| 4 | 2 | u16 | Parent Index (0xFFFF=none) |
| 6 | 2 | bytes[2] | Unused |
| 8 | 4 | f32 | Density |
| 12 | 1 | u8 | Clustering |
| 13 | 1 | u8 | Min Slope |
| 14 | 1 | u8 | Max Slope |
| 15 | 1 | u8 | Flags |
| 16 | 2 | u16 | Radius wrt Parent |
| 18 | 2 | u16 | Radius |
| 20 | 4 | f32 | Min Height |
| 24 | 4 | f32 | Max Height |
| 28 | 4 | f32 | Sink |
| 32 | 4 | f32 | Sink Variance |
| 36 | 4 | f32 | Size Variance |
| 40 | 6 | u16[3] | Angle Variance (X, Y, Z) |
| 46 | 2 | bytes[2] | Unused |
| 48 | 4 | bytes[4] | Unknown |

**RDSD Sound (12 bytes):** formid Sound, u32 SoundFlags, u32 Chance(×100)

**RDWT Weather (8 bytes):** formid Weather, u32 Chance

---

## ROAD — Road

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| PGRP | struct[] | | Points (same as PGRD PGRP) |
| PGRR | struct[] | | Point-to-Point Connections: array of { f32 X, f32 Y, f32 Z } |

---

## SBSP — Subspace

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| DNAM | struct(12) | Yes | Dimensions: f32 X, f32 Y, f32 Z |

---

## SCPT — Script

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| SCHD | bytes[] | | Unknown (old script header) |
| SCHR | struct(20) | | Basic Script Data |
| SCDA | bytes[] | | Compiled Script |
| SCTX | string | Yes | Script Source |
| SLSD | struct | | Local Variable Data — repeating |
| SCVR | string | | Variable Name — paired with SLSD |
| SCRO | formid | | Global References — repeating |
| SCRV | u32 | | Local Variable References — repeating |

**SCHR struct (20 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | bytes[4] | Unused |
| 4 | 4 | u32 | RefCount |
| 8 | 4 | u32 | CompiledSize |
| 12 | 4 | u32 | VariableCount |
| 16 | 4 | u32 | Type: 0=Object, 1=Quest, 0x100=Magic Effect |

**SLSD Local Variable struct (18+ bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Index |
| 4 | 12 | bytes[12] | Unused |
| 16 | 1 | u8 | Flags: 0x01=IsLongOrShort |
| 17+ | bytes[] | | Unused remainder |

---

## SGST — Sigil Stone

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| OBME | struct(32) | | OBME (optional) |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| SCRI | formid | | Script (→SCPT) |
| *Effects* | | | See Effects structure |
| DATA | struct(9) | Yes | Data |

**DATA struct (9 bytes):** u8 Uses, u32 Value, f32 Weight

---

## SKIL — Skill

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| INDX | s32 | Yes | Skill (ActorValueEnum) |
| DESC | string | | Description |
| ICON | string | | Icon filename |
| DATA | struct(24) | Yes | Skill Data |
| ANAM | string | Yes | Apprentice Text |
| JNAM | string | Yes | Journeyman Text |
| ENAM | string | Yes | Expert Text |
| MNAM | string | Yes | Master Text |

**DATA struct (24 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | s32 | Action (ActorValueEnum) |
| 4 | 4 | s32 | Attribute (ActorValueEnum) |
| 8 | 4 | u32 | Specialization: 0=Combat, 1=Magic, 2=Stealth |
| 12 | 8 | f32[2] | Use Values |

---

## SLGM — Soul Gem

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| SCRI | formid | | Script (→SCPT) |
| DATA | struct(8) | Yes | Data: u32 Value + f32 Weight |
| SOUL | u8 | Yes | Contained Soul (0=None..5=Grand) |
| SLCP | u8 | Yes | Maximum Capacity (0=None..5=Grand) |

---

## SOUN — Sound

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FNAM | string | | Sound Filename |
| SNDX or SNDD | struct(12) | Yes | Sound Data |

**SNDX struct (12 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Min Attenuation Distance (×5) |
| 1 | 1 | u8 | Max Attenuation Distance (×100) |
| 2 | 1 | s8 | Frequency Adjustment % |
| 3 | 1 | bytes[1] | Unused |
| 4 | 2 | u16 | Flags: 0x01=Random Freq Shift, 0x02=Play At Random, 0x04=Environment Ignored, 0x08=Random Location, 0x10=Loop, 0x20=Menu Sound, 0x40=2D, 0x80=360 LFE |
| 6 | 2 | bytes[2] | Unused |
| 8 | 2 | u16 | Static Attenuation (÷100 for dB) |
| 10 | 1 | u8 | Stop Time |
| 11 | 1 | u8 | Start Time |

**SNDD struct (12 bytes):** Same first 8 bytes as SNDX, then 3× unused u32

---

## SPEL — Spell

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| OBME | struct(32) | | OBME (optional) |
| FULL | string | | Name |
| SPIT | struct(16) | Yes | Spell Data |
| *Effects* | | | See Effects structure |

**SPIT struct (16 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Type: 0=Spell, 1=Disease, 2=Power, 3=Lesser Power, 4=Ability, 5=Poison |
| 4 | 4 | u32 | Cost |
| 8 | 4 | u32 | Level: 0=Novice, 1=Apprentice, 2=Journeyman, 3=Expert, 4=Master |
| 12 | 1 | u8 | Flags: 0x01=Manual Cost, 0x02=Immune Silence 1, 0x04=Player Start, 0x08=Immune Silence 2, 0x10=Area Ignores LOS, 0x20=Script Always Applies, 0x40=No Absorb/Reflect, 0x80=Touch Explodes |
| 13 | 3 | bytes[3] | Unused |

---

## STAT — Static

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| MODL | string | | Model filename |
| MODB | f32 | | Bound Radius |
| MODT | bytes[] | | Model Texture Info |

---

## TREE — Tree

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| SNAM | u32[] | | SpeedTree Seeds (array) |
| CNAM | struct(32) | Yes | Tree Data |
| BNAM | struct(8) | Yes | Billboard Dimensions |

**CNAM struct (32 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | f32 | Leaf Curvature |
| 4 | 4 | f32 | Min Leaf Angle |
| 8 | 4 | f32 | Max Leaf Angle |
| 12 | 4 | f32 | Branch Dimming Value |
| 16 | 4 | f32 | Leaf Dimming Value |
| 20 | 4 | s32 | Shadow Radius |
| 24 | 4 | f32 | Rock Speed |
| 28 | 4 | f32 | Rustle Speed |

**BNAM struct (8 bytes):** f32 Width, f32 Height

---

## WATR — Water

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| TNAM | string | Yes | Texture |
| ANAM | u8 | Yes | Opacity |
| FNAM | u8 | Yes | Flags: 0x01=Causes Damage, 0x02=Reflective |
| MNAM | string | Yes | Material ID |
| SNAM | formid | | Sound (→SOUN) |
| DATA | struct(~86) | Yes | Water Properties (variable, min 0 fields) |
| GNAM | struct(12) | | Related Waters |

**DATA struct (approx. 86 bytes, counting all fields):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | f32 | Wind Velocity |
| 4 | 4 | f32 | Wind Direction |
| 8 | 4 | f32 | Wave Amplitude |
| 12 | 4 | f32 | Wave Frequency |
| 16 | 4 | f32 | Sun Power |
| 20 | 4 | f32 | Reflectivity Amount |
| 24 | 4 | f32 | Fresnel Amount |
| 28 | 4 | f32 | Scroll X Speed |
| 32 | 4 | f32 | Scroll Y Speed |
| 36 | 4 | f32 | Fog Distance Near |
| 40 | 4 | f32 | Fog Distance Far |
| 44 | 4 | rgba | Shallow Color |
| 48 | 4 | rgba | Deep Color |
| 52 | 4 | rgba | Reflection Color |
| 56 | 1 | u8 | Texture Blend |
| 57 | 3 | bytes[3] | Unused |
| 60 | 4 | f32 | Rain Simulator Force |
| 64 | 4 | f32 | Rain Simulator Velocity |
| 68 | 4 | f32 | Rain Simulator Falloff |
| 72 | 4 | f32 | Rain Simulator Dampner |
| 76 | 4 | f32 | Rain Simulator Starting Size |
| 80 | 4 | f32 | Displacement Simulator Force |
| 84 | 4 | f32 | Displacement Simulator Velocity |
| 88 | 4 | f32 | Displacement Simulator Falloff |
| 92 | 4 | f32 | Displacement Simulator Dampner |
| 96 | 4 | f32 | Displacement Simulator Starting Size |
| 100 | 2 | u16 | Damage |

**GNAM struct (12 bytes):** formid Daytime, formid Nighttime, formid Underwater (all →WATR)

---

## WEAP — Weapon

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| MODL/MODB/MODT | | | Model |
| ICON | string | | Icon filename |
| SCRI | formid | | Script (→SCPT) |
| ENAM | formid | | Enchantment (→ENCH) |
| ANAM | u16 | | Enchantment Points |
| DATA | struct(32) | Yes | Weapon Data |

**DATA struct (32 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Type: 0=Blade 1H, 1=Blade 2H, 2=Blunt 1H, 3=Blunt 2H, 4=Staff, 5=Bow |
| 4 | 4 | f32 | Speed |
| 8 | 4 | f32 | Reach |
| 12 | 4 | u32 | Flags: 0x01=Ignores Normal Weapon Resistance |
| 16 | 4 | u32 | Value |
| 20 | 4 | u32 | Health |
| 24 | 4 | f32 | Weight |
| 28 | 2 | u16 | Damage |

---

## WRLD — Worldspace

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| FULL | string | | Name |
| WNAM | formid | | Parent Worldspace (→WRLD) |
| CNAM | formid | | Climate (→CLMT) |
| NAM2 | formid | | Water (→WATR) |
| ICON | string | | Map Image |
| MNAM | struct(16) | | Map Data |
| DATA | u8 | Yes | Flags |
| NAM0 | struct(8) | | Min Object Bounds: f32 X, f32 Y |
| NAM9 | struct(8) | | Max Object Bounds: f32 X, f32 Y |
| SNAM | u32 | | Music: 0=Default, 1=Public, 2=Dungeon |
| OFST | bytes[] | | Offset Data |

**MNAM Map Data struct (16 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | s32 | Usable Dimensions X |
| 4 | 4 | s32 | Usable Dimensions Y |
| 8 | 2 | s16 | NW Cell X |
| 10 | 2 | s16 | NW Cell Y |
| 12 | 2 | s16 | SE Cell X |
| 14 | 2 | s16 | SE Cell Y |

**DATA flags (u8):**
- 0x01 = Small World
- 0x02 = Can't Fast Travel
- 0x04 = Oblivion Worldspace
- 0x10 = No LOD Water

---

## WTHR — Weather

| Subrecord | Type | Required | Description |
|-----------|------|----------|-------------|
| EDID | string | | Editor ID |
| CNAM | string | | Texture Lower Layer |
| DNAM | string | | Texture Upper Layer |
| MODL/MODB/MODT | | | Model |
| NAM0 | struct(160) | Yes | Colors by Types/Times |
| FNAM | struct(16) | Yes | Fog Distance |
| HNAM | struct(56) | Yes | HDR Data |
| DATA | struct(15) | Yes | Weather Data |
| SNAM | struct(8) | | Sounds — repeating |

**NAM0 Colors (160 bytes):** 10 types × 4 times × 4 bytes(rgba). Types: Sky-Upper, Fog, Clouds-Lower, Ambient, Sunlight, Sun, Stars, Sky-Lower, Horizon, Clouds-Upper. Times: Sunrise, Day, Sunset, Night.

**FNAM Fog Distance struct (16 bytes):** f32 Day Near, f32 Day Far, f32 Night Near, f32 Night Far

**HNAM HDR Data struct (56 bytes):** 14 consecutive f32 values: Eye Adapt Speed, Blur Radius, Blur Passes, Emissive Mult, Target LUM, Upper LUM Clamp, Bright Scale, Bright Clamp, LUM Ramp No Tex, LUM Ramp Min, LUM Ramp Max, Sunlight Dimmer, Grass Dimmer, Tree Dimmer

**DATA struct (15 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Wind Speed |
| 1 | 1 | u8 | Cloud Speed (Lower) |
| 2 | 1 | u8 | Cloud Speed (Upper) |
| 3 | 1 | u8 | Trans Delta |
| 4 | 1 | u8 | Sun Glare |
| 5 | 1 | u8 | Sun Damage |
| 6 | 1 | u8 | Precipitation Begin Fade In |
| 7 | 1 | u8 | Precipitation End Fade Out |
| 8 | 1 | u8 | Thunder/Lightning Begin Fade In |
| 9 | 1 | u8 | Thunder/Lightning End Fade Out |
| 10 | 1 | u8 | Thunder/Lightning Frequency |
| 11 | 1 | u8 | Weather Classification (enum/flags) |
| 12 | 3 | rgb | Lightning Color (R, G, B) |

**SNAM Sound struct (8 bytes):** formid Sound(→SOUN), u32 Type (0=Default, 1=Precipitation, 2=Wind, 3=Thunder)

---

## Shared Structures

### Effects (EFID/EFIT/SCIT) — Used by ALCH, ENCH, INGR, SGST, SPEL

Each effect is a group of subrecords:

**EFID (4 bytes):** u32 Magic effect name (4-char code interpreted as u32)

**EFIT struct (24 bytes):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | u32 | Magic Effect Name (4-char code) |
| 4 | 4 | u32 | Magnitude |
| 8 | 4 | u32 | Area |
| 12 | 4 | u32 | Duration |
| 16 | 4 | u32 | Type: 0=Self, 1=Touch, 2=Target |
| 20 | 4 | s32 | Actor Value (ActorValueEnum) |

**SCIT Script Effect struct (16 bytes, always paired with FULL):**

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 4 | formid | Script Effect (→SCPT) |
| 4 | 4 | u32 | Magic School |
| 8 | 4 | u32 | Visual Effect Name (4-char code) |
| 12 | 1 | u8 | Flags: 0x01=Hostile |
| 13 | 3 | bytes[3] | Unused |

### CTDA — Condition (28 bytes)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| 0 | 1 | u8 | Type (comparison operator + flags) |
| 1 | 3 | bytes[3] | Unused |
| 4 | 4 | f32/formid | Comparison Value (float or →GLOB if Use Global flag) |
| 8 | 2 | u16 | Function index |
| 10 | 2 | bytes[2] | Unused |
| 12 | 4 | varies | Parameter #1 (type depends on function) |
| 16 | 4 | varies | Parameter #2 (type depends on function) |
| 20 | 4 | u32 | Unused |

**Type byte:** High nibble = comparison (0x00=EQ, 0x20=NE, 0x40=GT, 0x60=GE, 0x80=LT, 0xA0=LE). Low nibble: 0x01=Or, 0x02=Run on Target, 0x04=Use Global.

### Model (MODL/MODB/MODT)

- MODL (string): Model filename
- MODB (f32): Bound Radius
- MODT (bytes[]): Texture file hashes/info

### Textured Model Variants

| Primary | Sigs | Usage |
|---------|------|-------|
| Model | MODL/MODB/MODT | Standard model |
| Model 2 | MOD2/MO2B/MO2T | Male world model / secondary |
| Model 3 | MOD3/MO3B/MO3T | Female biped model |
| Model 4 | MOD4/MO4B/MO4T | Female world model |

### ServiceFlags (u32) — Used in AIDT and CLAS

- 0x00000001 = Weapons
- 0x00000002 = Armor
- 0x00000004 = Clothing
- 0x00000008 = Books
- 0x00000010 = Ingredients
- 0x00000080 = Lights
- 0x00000100 = Apparatus
- 0x00000400 = Miscellaneous
- 0x00000800 = Spells
- 0x00001000 = Magic Items
- 0x00002000 = Potions
- 0x00004000 = Training
- 0x00010000 = Recharge
- 0x00020000 = Repair

### ActorValueEnum (s32)

0=Strength, 1=Intelligence, 2=Willpower, 3=Agility, 4=Speed, 5=Endurance, 6=Personality, 7=Luck, 8=Health, 9=Magicka, 10=Fatigue, 11=Encumbrance, 12=Armorer, 13=Athletics, 14=Blade, 15=Block, 16=Blunt, 17=Hand to Hand, 18=Heavy Armor, 19=Alchemy, 20=Alteration, 21=Conjuration, 22=Destruction, 23=Illusion, 24=Mysticism, 25=Restoration, 26=Acrobatics, 27=Light Armor, 28=Marksman, 29=Mercantile, 30=Security, 31=Sneak, 32=Speechcraft. -1=None.

### SkillEnum (s8/s32)

0=Armorer, 1=Athletics, 2=Blade, 3=Block, 4=Blunt, 5=Hand to Hand, 6=Heavy Armor, 7=Alchemy, 8=Alteration, 9=Conjuration, 10=Destruction, 11=Illusion, 12=Mysticism, 13=Restoration, 14=Acrobatics, 15=Light Armor, 16=Marksman, 17=Mercantile, 18=Security, 19=Sneak, 20=Speechcraft. -1=None.

### SoulGemEnum (u8)

0=None, 1=Petty, 2=Lesser, 3=Common, 4=Greater, 5=Grand

### MagicSchoolEnum (s32)

0=Alteration, 1=Conjuration, 2=Destruction, 3=Illusion, 4=Mysticism, 5=Restoration

### SpecializationEnum (u32)

0=Combat, 1=Magic, 2=Stealth
