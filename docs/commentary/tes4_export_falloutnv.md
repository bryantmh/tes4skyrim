# FO3/FNV export deltas

**Code:** `tes4_export/record_types/falloutnv.py`, `tes4_export/tes4_reader.py`

Why a Fallout plugin needs its own delta module, and why the field list is
shorter than a signature census predicts.

## The header size is per-file, not a constant

FO3/FNV record headers are 24 bytes against Oblivion's 20, and **the GRUP header
scales with them**. An early note claimed `GROUP_HEADER_SIZE` was fixed at 20 in
both games; that was wrong, and came from a test that set the group size to 24
for *both*, which corrupted the Oblivion parse (63 types collapsed to 31). The
measurement that settles it: an FO3/FNV GRUP's first record signature sits at
+24, Oblivion's at +20.

`detect_header_size` probes both offsets for the `HEDR` literal rather than
trusting a version float, then one detected size drives both headers. Across 60
real plugins the split is unambiguous — 5 TES4 files report HEDR 1.00 at offset
20, and 55 FNV/FO3 files report 1.32-1.34 at offset 24.

`_format_chunk_worker` re-detects from its own mmap instead of receiving the size
in its args tuple. `ProcessPoolExecutor` runs with no initializer, so on Windows
spawn a module global set in the parent never reaches the worker; detecting
locally sidesteps the problem without widening the args tuple.

## A shared signature is not a shared field

The subrecord census that motivated this work compared signature presence and
frequency. That is necessary but not sufficient, and three fields prove it.

**`XCLC` is 12 bytes in FO3/FNV, 8 in TES4** — X, Y, then a land-flags byte and
3 unused. `export_CELL` reads X and Y at offsets 0 and 4 behind a `>= 8` guard,
so it was already correct for both; only the flags byte is new.

**`XCLL` is 40 bytes against TES4's 36.** The extra trailing float is Fog Power.
Everything before it is positionally identical, so the existing reader is safe
and only the tail needed adding.

**`LNAM` looks like a passthrough and is not.** In FO3/FNV it is a `U32` of
lighting-inherit flags; in Skyrim it is `wbByteArray(LNAM, 'Unknown', 0,
cpIgnore)` — leftover, ignored, with the real flags moved into `XCLC`. It is
dropped rather than passed through.

## Format-identical fields whose referents do not exist

The 14.4% "Skyrim-native passthrough" bucket is right about layout and wrong
about five CELL fields. `LTMP`, `XCAS`, `XCIM`, `XCMO` and `XEZN` are byte-for
byte the same in FNV and Skyrim per xEdit, but every one is a FormID pointing at
a record type this converter does not emit:

| Field | Points at | Resolved in FalloutNV.esm |
|---|---|---|
| `LTMP` | `LGTM` | 273 real + 30,224 null |
| `XCAS` | `ASPC` | 17,485 |
| `XCIM` | `IMGS` | 321 |
| `XCMO` | `MUSC` | 297 |
| `XEZN` | `ECZN` | 84 |

`ASPC` reduces to a `STAT` through `FALLOUT_BASE_TYPES`, so `XCAS` still drops.
`LGTM`, `IMGS`, `MUSC` and `ECZN` now convert, and their CELL pointers carry.

`XNAM` survives the same test: FNV and Skyrim both define it as
`wbString(XNAM, 'Water Noise Texture')`, and it carries no FormID. 30,458 of
30,495 are a 1-byte empty string; 36 carry real 35-47 byte paths.

## <a id="reference-only-types"></a>The types CELL points at

**Code:** `tes4_export/record_types/falloutnv.py`,
`tes5_import/record_types/reference_falloutnv.py`

Five record types are format-identical enough between FNV and TES5 to convert
directly. Four exist only to be referenced — by `CELL.LTMP`, `XCIM`, `XCMO`,
`XEZN` — so converting them is what makes those pointers legal.

### <a id="formlist-members"></a>FormList members are filtered, not passed through

`FLST` itself is byte-identical between the games, but its members are not
guaranteed to survive. FalloutNV.esm's 464 lists hold **4,436 members**, and
189 of them name a type this converter does not write: **139 ARMA**
(TES5 ARMA is generated from ARMO, never read from source) and **50 IMOD**.
Every other member resolves — 1,343 WEAP, 802 ARMO, 748 ALCH, 550 MISC, the
14 EXPL ([explosions](#explosions)) and the rest. The index covers the
masters' records too, so a DLC list naming a FalloutNV.esm record keeps it.

Writing those 203 verbatim produces a dangling FormID, which the engine treats
as a broken reference rather than a bad value. `index_convertible_records`
indexes every source FormID whose own type is in `IMPORT_DISPATCH` and not
skipped, and `convert_FLST` drops any member missing from it.

Dropping rather than NULL-ing is safe here because nothing reads these lists
positionally: TES5 indexes a FLST only where a script does, and every list a
converted script touches is homogeneous.

### <a id="texture-sets"></a>Texture sets

FNV `TXST` declares `TX00`-`TX05`; TES5 adds `TX06` (multilayer) and `TX07`
(backlight mask), which no FO3/FNV record authors. `DNAM` is a `u16` flag field
in both, and bit 0 (No Specular Map) is the only value FNV sets. So the record
passes through slot-for-slot. `LTEX` already indexed these for its diffuse
path; this emits them as records in their own right.

### <a id="imagespaces"></a>Imagespaces

`IMGS.DNAM` is a 152-byte block in FNV and the same 152 bytes in TES5 — the HDR
tone-mapping parameters the weather conversion already mints companions for.
It is exported verbatim as hex rather than field-by-field: nothing reorders.

### <a id="lighting-templates"></a>Lighting templates

`LGTM.DATA` is **40 bytes in FNV and 92 in TES5**, so this one is not a
passthrough. The shared 40-byte prefix (ambient / directional / fog colors,
fog near/far, directional rotation and fade, fog clip, fog power) is identical;
TES5 appends a 24-byte unused ambient block, a far fog color, fog max, and a
light-fade start/end pair. The converter keeps the prefix and zero-fills the
tail, then writes the `DALC` ambient-colors subrecord TES5 requires.

### <a id="encounter-zones"></a>Encounter zones

`ECZN.DATA` is 8 bytes in FNV — `Owner`(4) `Rank`(1) `MinLevel`(1) `Flags`(1)
pad(1) — and 12 in TES5 at form version 34+, which **inserts a `Location`
FormID at offset 4** and appends a `MaxLevel` byte. The fields are therefore
reordered, not extended. TES4 has no LCTN source, so Location writes NULL.

### <a id="music-types"></a>Music types

FO3/FNV `MUSC` is a single **track** (`FNAM` filename, `ANAM` dB gain); TES5
`MUSC` is a music **type** holding `MUST` tracks. So each FNV record becomes a
pair: one `MUST` naming the file, wrapped in one `MUSC` that `XCMO` points at.
Both are built by the existing `build_MUST` / `build_MUSC`, so FNV music joins
the folder-derived types rather than forking the music system.

`ANAM` is a gain, not a duration, and the TES5 single-track shape carries
neither — it is dropped rather than written into `FLTV`, which on a non-silent
track produces a combination no vanilla record uses.

## Measured field coverage

From `FalloutNV.esm` (Tale of Two Wastelands merge), 465,054 records parsed
through the shipped reader:

| Type | Records | Notes |
|---|---|---|
| `REFR` | 307,710 | `NAME`+`DATA`+`XSCL` = 89% of 816k subrecords |
| `CELL` | 30,497 | `DATA`/`LNAM`/`LTMP`/`XCLW` fire on every record |
| `LAND` | 29,363 | 7 subrecord types, all TES4-known |
| `STAT` | 6,795 | `OBND` on all; `BRUS` (3,414) drops |
| `ACHR` | 3,386 | |
| `ACRE` | 2,999 | |
| `DOOR` | 320 | `OBND` on all |
| `WRLD` | 14 | |

`LAND` needs no delta handling at all — every one of its 7 subrecord signatures
is one Oblivion also emits, at the same size.

## Fallout-only base objects

FO3/FNV place references against base-object types Oblivion does not have. The
records are skipped, but the REFRs that point at them are not — so the base
resolves to null and the engine faults dereferencing it. The measured crash:
`EXCEPTION_ACCESS_VIOLATION` reading `[rcx+0x1A]` with `rcx = 0` inside
`QueuedPromoteLocationReferencesTask`, while promoting a persistent REFR into
`BGSLocation \"Mojave Wasteland\"`.

Measured in the first FalloutNV output: **721 base objects, 11,722 references**
with no record behind them. Oblivion's output has 6 such targets, all of them
deliberate Skyrim.esm marker references (`XMarker`, `XMarkerHeading`,
`NorthMarker`, `MapMarker`, `Gold001`), which resolve through the master.

| Type | Bases | Refs | Converted as |
|---|---|---|---|
| `MSTT` movable static | 224 | 7,823 | STAT |
| `TERM` terminal | 171 | 219 | ACTI |
| `IDLM` idle marker | 98 | 1,424 | STAT |
| `SCOL` static collection | 83 | 1,084 | STAT |
| `NOTE` note | 51 | 54 | ACTI |
| `ASPC` acoustic space | 32 | 49 | STAT |
| `TACT` talking activator | 31 | 43 | ACTI |
| `PWAT` placeable water | 28 | 102 | STAT |

Every one carries `MODL` and `OBND`, which is all a Skyrim STAT needs; the
named, scriptable ones (`TERM`, `NOTE`, `TACT`) additionally carry `FULL`
and a script, making them ACTI. `SCOL` loses its `ONAM` part list and renders
as the collection's own model rather than its instanced pieces.


## LTEX moved its texture to a TXST

Oblivion names a landscape texture directly on `LTEX.ICON`. FO3/FNV keep
`ICON` in the record definition but do not use it: the texture is a `TNAM`
FormID pointing at a `TXST` record, whose `TX00` holds the diffuse path.
xEdit's FNV definition declares both, and the shipped data settles which is live.

All 89 of FalloutNV.esm's LTEX records carry a `TNAM` and **zero** carry an
`ICON`, so an ICON-only exporter emits no texture at all and the terrain
renders black or falls back to the Skyrim default.

`index_texture_sets` indexes the 495 TXST records that carry a `TX00`, and
`ltex_icon_path` resolves `TNAM` through it. The TX00 paths are spelled
`Landscape\Asphalt02.dds`, while `convert_LTEX` prepends
`landscape\` itself (Oblivion's ICON convention is relative to that
folder), so the prefix is stripped on the way out rather than teaching the
importer a second form.

## Weapons: guns become crossbows

**Code:** `tes4_export/record_types/falloutnv.py` (`_emit_weap_deltas`),
`tes5_import/record_types/equipment.py` (`convert_WEAP`)

### The defect

The FO3/FNV WEAP layout differs from TES4's, so the shared exporter dumped
none of it. Measured over `export/FalloutNV.esm/WEAP.txt`: **265 weapons, 0
carrying `DATA.Type`** — only EDID, FULL, MODL, ICON and OBND survived.

With no type, `WEAPON_TYPE_MAP.get(tes4_type, 1)` fell to its default of **1 =
Sword**, so every gun in the game arrived as a one-handed blade with no damage,
value, weight or reach.

### Where the fields live

TES4 packs everything into one 30-byte `DATA`. FO3/FNV split it:

| Field | FO3/FNV | TES4 |
|---|---|---|
| Animation Type | `DNAM` +0 (u32) | `DATA` +0 |
| Animation Multiplier (speed) | `DNAM` +4 (f32) | `DATA` +4 |
| Reach | `DNAM` +8 (f32) | `DATA` +8 |
| Value | `DATA` +0 (s32) | `DATA` +16 |
| Health | `DATA` +4 (s32) | `DATA` +20 |
| Weight | `DATA` +8 (f32) | `DATA` +24 |
| Base Damage | `DATA` +12 (s16) | `DATA` +28 (u16) |
| Clip Size | `DATA` +14 (u8) | — |

The exporter emits the **TES4 key names**, so the importer needs no new
vocabulary for any of the shared fields.

### The animation mapping

FO3/FNV has 14 weapon animation types (`wbWeaponAnimTypeEnum`,
`Core/wbDefinitionsFNV.pas:2901`); Skyrim has 10
(`Core/wbDefinitionsTES5.pas:2718`). Skyrim's only ranged animations are
**Bow (7)** and **Crossbow (9)**, so every firearm maps to Crossbow — it aims
and fires a projectile flat, where a bow is drawn and arced.

| FO3/FNV | | → Skyrim |
|---|---|---|
| 0 | Hand to Hand | HandToHandMelee (0) |
| 1 | Melee 1 Hand | OneHandSword (1) |
| 2 | Melee 2 Hand | TwoHandSword (5) |
| 3, 4 | Pistol — Ballistic / Energy | Crossbow (9) |
| 5, 6, 7 | Rifle — Ballistic / Automatic / Energy | Crossbow (9) |
| 8 | Handle (2 Hand) | TwoHandSword (5) |
| 9 | Launcher (2 Hand) | Crossbow (9) |
| 10–13 | Grenade / mine / thrown | Crossbow (9) |

Types 10–13 have no Skyrim equivalent at all — a thrown grenade is not an
animation the engine has. They take Crossbow so they remain usable ranged
weapons rather than becoming swords.

`DNAM.FalloutAnimType` carries the raw value through so the importer can tell a
pistol from a rifle; the exporter also writes a TES4-equivalent `DATA.Type` so
every existing code path keeps working unchanged.

### Reload animation

A converted gun reloads with the crossbow's crank animation. Skyrim has no
other ranged reload, and this is cosmetic — the weapon fires correctly.

### <a id="ammo-is-a-bolt"></a>Ammo: rounds are bolts

**Code:** `tes4_export/record_types/falloutnv.py` (`_emit_ammo_deltas`),
`tes5_import/record_types/equipment_falloutnv.py` (`ammo_flags`).

The FO3/FNV `AMMO.DATA` is 13 bytes (speed f32, flags u8 + 3 unused, value
s32, clip rounds u8) and the weight moved to `DAT2` (+8, after projectiles
per shot and the projectile form), so the shared exporter's `>= 18` byte
guard dumped nothing: all 92 FalloutNV AMMO records exported with no DATA at
all. The FNV flags share only bit 0 (Ignores Normal Weapon Resistance) with
TES4; bit 1 is Non-Playable.

The importer sets Non-Bolt (0x04) on every TES4 arrow, and a crossbow-type
WEAP can only load bolts, so a converted gun could never equip its rounds
and `arrowRelease` had nothing to spend. A Fallout-sourced AMMO leaves that
bit clear.

### <a id="projectiles"></a>Projectiles and the gun's fire fields

**Code:** `tes4_export/record_types/falloutnv.py` (`_emit_weap_fire`,
`_emit_proj_deltas`), `tes5_import/record_types/projectile_falloutnv.py`.

FO3/FNV keep the shot on three records TES4 never had. `PROJ` (95 in
FalloutNV.esm) is the bullet itself: an 84-byte `DATA` (flags u16, type u16,
gravity, speed, range, light, muzzle-flash light, tracer chance, two
alt-trigger floats, explosion, sound, muzzle-flash duration, fade, impact
force, countdown/disable sounds, default weapon, rotation vec3, bouncy
mult), the muzzle-flash model `NAM1` and the sound level `VNAM`. The gun's
`DNAM` names its projectile at +36 (`DNAM.Projectile`), the rounds a shot
spends at +14, the projectile count at +42, and its rate fields at +60
(`AnimAttackMult`), +64 (`FireRate`) and +88 (`ShotsPerSec`); its sounds
are `SNAM` (shoot 3D, the first of two), `XNAM` (2D), `NAM7` (loop),
`TNAM` (dry fire), `UNAM` (idle), `NAM9`/`NAM8` (equip/unequip); `NAM0` is
the ammo and `WNAM` the first-person model STAT. The AMMO's own
`DAT2.Projectile` is usually null (Ammo9mm: 0), the gun's is not.

<a id="formlists"></a>**`NAM0` is a FormList, not an AMMO.** The 9mm's
`NAM0` is `AmmoList9mm` (FLST 001537E7: the round plus its two hand-load
variants), and FLST was an unexported type, so the first version of the
ammo index keyed 48 list ids that no AMMO ever matched and every round fell
back to the arrow: the casing model, the 3,600 units/s flight and the
arrow bounce were all the fallback. `export_FORMLIST` now dumps every FLST
as `LNAM[i]` FormIDs and the index expands a `NAM0` that names a list to
its members.

Before this the importer synthesized one arrow PROJ per AMMO from the
ammo's inventory model, so a 9mm shot was a flying brass casing that
whooshed like an arrow at 3,600 units/s. Now each FNV PROJ converts to a
TES5 PROJ with its FNV type and flags kept verbatim (the low bits agree in
both games: 1 Missile, 2 Lobber, 4 Beam, 8 Flame; Hitscan 0x01, Muzzle
Flash 0x08, Supersonic 0x80; FNV's Continuous Beam 0x10 becomes Beam),
so a bullet is a Missile, which the CK documents as consumed on contact
where an Arrow sticks or bounces, and carries the FNV model, gravity,
speed, range, impact force, light, sound and the alt-trigger proximity and
timer (28 grenade timers, 8 proximity mines). The AMMO's
TES5 projectile is its own `DAT2` projectile when set, else the projectile
the guns firing that ammo name most often (both the plugin's and its
masters' WEAPs), else the arrow fallback. The gun's shoot/dry-fire/idle
sounds go to the TES5 `SNAM`, `XNAM`, `NAM7`, `TNAM`, `UNAM` through the
SOUN's companion SNDR id.

<a id="no-hitscan"></a>**No Hitscan on a converted PROJ.** Bullets that
kept FNV's Hitscan flag only hit at point-blank range in game. No vanilla
Skyrim PROJ sets it (0 of 134 in Skyrim.esm), and in the engine
(SkyrimSE 1.6.1170) it is a side path: the Missile setup (0x7e0480) turns
it into runtime bits on the projectile (+0x1d4: bits 0, 3 = no art, 13 on,
15 = remove-on-impact off), and the hit is one raycast of the PROJ's range
(`+0x198`, set from the record by the projectile constructor) from the
projectile's position, run by vtable slot 0xb7 (0x7e83e0) only once the
projectile's model has loaded (the 3D-load step 0x7ee3e0, called from
`Projectile::Update` 0x7e5990). Only `Projectile::Launch` (0x7e46c0) calls
that slot at once, and only for the Beam type. A Missile's default
collision layer is L_SPELL (0x260830: Arrow 6, Cone 38, everything else 7),
the layer every vanilla spell missile flies on. The converter now drops
Hitscan and keeps the FNV speed with no gravity, so a bullet is an ordinary
straight-flying Missile (23,680 units/s for the 9mm), which hits at range
in game.

<a id="no-muzzle-flash-light"></a>**No muzzle-flash light on a converted
PROJ.** Keeping FNV's Muzzle Flash flag with its `MuzzleFlashLight`
(LIGH `MuzzleFlashOrange352`) crashed on the first shot: crash log
2026-09-09 00:35, `SkyrimSE.exe+026F089` = id 17610+0x439 (the point-light
creation, `mov ecx,[rsi+0x10]` with rsi = the reference argument = 0).
The caller (id 44056+0x25B, the projectile's muzzle flash) passes a null
reference by construction (`xor edx,edx` before the call), and the light
routine's null-reference path dereferences it anyway. The six vanilla
PROJs with a muzzle-flash light are all Cone/Flame spell projectiles, cast
with a caster reference; no weapon-launched Missile or Arrow has one. The
converter keeps the flag and the `NAM1` flash model and writes light 0.

<a id="nocked-model"></a>**The AMMO's model is its box; nothing is
attached to the actor.** Skyrim attaches the AMMO's `MODL` to the hand at
`arrowAttach` and to the actor's `QUIVER` node while equipped, and shows
it in the inventory viewer and on the ground. A gun clip never raises
`arrowAttach`, so the earlier bullet-model substitution (a carton at the
hand for 30 ms) is moot and the FNV box model (`ammo\9mmammo.nif`) is
kept for the inventory and the world; the flying round is the PROJ's own
model. The quiver attachment (which sat at a gun holder's feet) is
culled by TESRuntime on every gun draw and ammo equip, and restored on a
non-gun draw (docs/commentary/tes_runtime_guns.md#quiver).

### <a id="impacts"></a>Impacts: the gun's own IPDS, not the arrow's

**Code:** `tes4_export/record_types/falloutnv.py` (`export_IMPACT`,
`export_IMPACTSET`), `tes5_import/record_types/impact_falloutnv.py`.

A FNV gun names its impact data set on `INAM` (the 9mm: 00019083
`BallisticImpactDataSet`) exactly as a Skyrim WEAP does, but IPDS and
IPCT were unexported, so the converted WEAP kept the crossbow template's
`WPNzArrowImpactSet`: every bullet hit thudded and sparked like a bolt.
FNV's IPDS `DATA` is twelve IPCT ids in a fixed material order (stone,
dirt, grass, glass, metal, wood, organic, cloth, water, hollow metal,
organic bug, organic glow); Skyrim's is a list of `PNAM` (MATT, IPCT)
pairs, so each slot maps to the vanilla material it names (`MaterialStone`,
`MaterialDirt`, `MaterialGrass`, `MaterialGlass`, `MaterialSolidMetal`,
`MaterialWoodHeavy`, `MaterialSkin`, `MaterialCloth`, `MaterialWater`,
`MaterialHeavyMetal`, `MaterialInsect`, `MaterialOrganicLarge`) plus the
near relatives vanilla's arrow set also lists (gravel, heavy and broken
stone, light wood, light and chain metal, light armor) pointed at the same
slots. FNV's IPCT `DATA` (24 bytes: duration, orientation, angle
threshold, placement radius, sound level, flags u32) is byte-compatible
with TES5's (flags u8, impact result u8, 2 pad), so it copies through with
result Default; the model and both SOUN links (as SNDRs) carry over. The
decal also copies: `DODT` is the same 36 bytes in both games and `DNAM`
names a TXST, which converts ([texture sets](#texture-sets)). 119 of the
125 IPCTs author a `DODT` and 91 a TXST; an impact missing either keeps No
Decal Data.

### <a id="explosions"></a>Explosions

**Code:** `tes4_export/record_types/falloutnv.py` (`export_EXPLOSION`),
`tes5_import/record_types/impact_falloutnv.py` (`convert_EXPL`).

EXPL was unexported, so the 51 FNV projectiles naming an explosion (the
grenades, missiles, mines) pointed at a record that did not exist. Both
games hold the same fields in a different order: FNV's 52-byte `DATA` is
force, damage, radius, light, sound 1, flags, IS radius, impact set,
sound 2, a radiation block and sound level; TES5's is light, both sounds
(SNDRs), impact set, placed object, spawn projectile, force, damage,
radius, IS radius, vertical offset, flags and sound level (52 bytes in 136
of Skyrim.esm's 143). The flags agree bit for bit: FNV's bit 0 is Radius in
BS Units, set on all 154 FNV and on the vanilla records xEdit calls
Unknown 0. Radiation has no TES5 field. Vertical offset is 0, as in 116
vanilla records. A link to a type this run does not write is nulled: the
97 image-space modifiers (`MNAM`, IMAD is not converted) go, the 98 lights,
81 impact sets and 22 enchantments stay.

### <a id="addon-nodes"></a>Addon nodes: the muzzle flash's sparks and smoke

**Code:** `tes4_export/record_types/falloutnv.py` (`export_ADDON`),
`tes5_import/record_types/impact_falloutnv.py` (`convert_ADDN`),
`asset_convert/nif/addon_nodes_falloutnv.py` (`remap_addon_nodes`).

A FNV effect mesh spawns its particles through `BSValueNode`s named
`AddOnNode<N>` whose value N is an ADDN's `DATA` index: the SMG's
`MinigunMuzzleFlash01.nif` carries 22 (`MPSGunSparks01`) and 5
(`MPSSmokeMetalBurn01`). ADDN was unexported (37 records in FalloutNV.esm,
indexes 0-36), and Skyrim.esm owns 0-88, so every converted mesh spawned
the vanilla addon at its index instead: the SMG's flash played
`MPSPushGrass01` and `MPSFireBallSparkTrail`. SkyrimSE 1.6.1170 has no
"AddOnNode" string at all; `BGSAddonNode`'s loader (vtable slot 6, 0x255fa0)
reads `DATA` into +0x68 as a u32, so the value, not the name, selects the
record. Both sides now move by `ADDON_INDEX_BASE` (20000): the converted
ADDN's `DATA`, and every FO3/FNV mesh's addon node value and name. The
record is otherwise the same in both games (model, sound as SNDR, `DNAM`
particle cap u16 + flags u16); FNV's one flags-0 record (`FireSmall01`)
becomes 1, since all 89 vanilla ADDNs carry 1 or 3.

Where the flash sits: the projectile's muzzle-flash setup (id 44056,
0x7e0b40) takes the flash 3D the projectile loaded from `NAM1` and attaches
it to the firing weapon's `ProjectileNode` (the static-init string at
0x185d4 for the global the lookup 0x286b00 uses), which the converted FNV
gun meshes keep.

## Navmesh: authored, not generated

**Code:** `tes4_export/record_types/falloutnv.py` (`_emit_navm_deltas`,
`_emit_navi_deltas`), `tes5_import/record_types/navm_falloutnv.py`

### Why FO3/FNV needs a different path

Oblivion has no navmesh. It ships **pathgrids** (PGRD), and the importer
*generates* navmesh geometry from them — the slowest stage in the pipeline, and
the reason the shared navmesh cache exists.

FO3/FNV ship real navmeshes. Measured:

| | PGRD | NAVM | NAVI |
|---|---:|---:|---:|
| Oblivion.esm | 8,228 | 0 | 0 |
| FalloutNV.esm | **0** | **4,771** | **1** |

So FalloutNV got no navmesh at all: nothing to generate from, and its authored
navmeshes were never read. Symptom: NPCs cannot path anywhere.

This is a **read-and-repack**, not a generation problem, so it needs none of
the pathgrid machinery and none of the cache.

### The format gap

FO3/FNV spread the navmesh across separate subrecords; TES5 packs the whole
thing into one `NVNM` blob.

| | FO3/FNV | TES5 |
|---|---|---|
| Cell + counts | `DATA` (24B) | inside `NVNM` |
| Vertices | `NVVX`, 12B each | inside `NVNM` |
| Triangles | `NVTR`, 16B each | inside `NVNM` |
| Door links | `NVDP`, 8B each | inside `NVNM` |
| Grid | `NVGD` | rebuilt |
| Cover triangles | `NVCA` | dropped — Skyrim has no cover system |

The triangle record is the same shape in both: three u16 vertex indices, three
s16 edge-adjacency indices, then flags. **FO3/FNV already store the edge
adjacency**, so the importer skips `_compute_adjacency` entirely — the authored
answer is better than a recomputed one, and cheaper.

`NVGD` is not exported: it is a spatial lookup grid derived from the geometry,
and `_pack_nvnm` builds its own via `_build_navmesh_grid`.

### What the exporter emits

`NVVX`, `NVTR` and `NVDP` are dumped verbatim as hex; `DATA` is unpacked into
named fields. NAVI's repeating `NVMI` entries are emitted one per line with an
ordinal plus a count, since a single record carries thousands.

### Reusing the TES5 serialiser

`tes5_import/navmesh/from_pgrd.py::_pack_nvnm` already writes the TES5 blob and is
validated byte-exact against real Skyrim navmeshes. It takes
`(verts, tris, adj, tri_flags, ...)` — exactly what NVVX/NVTR carry — so the
FO3/FNV path feeds it authored data where the Oblivion path feeds it generated
data. One serialiser, two sources.

### Authored edges that name no triangle

Vanilla FalloutNV.esm has **43 edge-adjacency entries across 38 navmeshes**
that index past the last triangle in their own mesh -- 43 of 674,700, a 0.006%
defect rate in Bethesda's own data. The first is navmesh 00124991, whose edge
names triangle 6 in a 6-triangle mesh.

FO3/FNV otherwise use -1 for no" "neighbour exactly as TES5 does (251,745
occurrences). parse_triangles normalizes an out-of-range edge to -1, because
navm_split._components indexes comp[e] directly and only guards against -1;
an unchecked value raises IndexError and kills the whole import.

## Fallout-only base objects
<a id="fallout-only-base-objects"></a>

**Code:** `tes4_export/record_types/falloutnv.py`

FO3/FNV carry base-object signatures Oblivion never defines, so the generic
exporter falls through to its unknown-type path — which dumps each subrecord's
NAME and SIZE but no VALUE. Downstream that is indistinguishable from the record
not existing.

Two reductions cover the model-shaped ones. `export_STATIC_BASE` (MSTT, SCOL,
PWAT, IDLM, ASPC) keeps model plus bounds; without it the 10,000+ REFRs those
base is null and the engine faults promoting them into their location.
`export_ACTIVATOR_BASE` (TERM, NOTE, TACT) adds a display name and the script.

### MESG — the record a converted `ShowMessage` binds to
<a id="mesg-export"></a>

`ShowMessage <msg>` is FNV's ordinary way to put text on screen (562 authored
MESG records, named by scripts at 562 sites). Skyrim has the SAME record type,
so this is a straight carry-over, not an approximation — the layout was checked
against `wbRecord(MESG, ...)` in `references/xEdit/Core/wbDefinitionsTES5.pas`
and against `references/Skyrim.esm/MESG.txt`:

| Subrecord | Skyrim | Notes |
|---|---|---|
| `EDID` | required | |
| `DESC` | **required** | the body text |
| `FULL` | optional | title; absent on a plain notification |
| `INAM` | **required** | vestigial icon, always `00000000` in vanilla |
| `DNAM` | **required** | flags: bit0 Message Box, bit1 Auto Display |
| `ITXT` | array | one per button (612 across FNV) |

`TNAM` (display time) is FNV-only in practice and Skyrim defaults it, so it is
not written. Vanilla writes `DNAM=1` on every help message.

Before this, the property a converted `ShowMessage` declared had nothing to
bind to, and the record-type table typed the EditorID through its
`ObjectReference` default — so the call emitted
`SomeMessage.Show(0.0, 0.0)` against an `ObjectReference Property`, which the
compiler rejects with "undefined function `Show`" (6 FNV scripts).

## Marker base objects
<a id="marker-base-objects"></a>

**Code:** `tes5_import/record_types/world_falloutnv.py`

FO3/FNV ship their editor markers as ordinary `STAT` records with real meshes
(`MarkerX.nif`, `Marker_Audio.NIF`, `Markers\FurnitureMarker01.NIF`). The engine
hides them by convention; Skyrim has no such rule, so they convert into fully
visible statics scattered through every interior.

`TES4_MARKER_FORMID_TO_SKYRIM` already substitutes Skyrim.esm's invisible
markers for the ones FO3/FNV share with Oblivion -- XMarker `0x3B`,
XMarkerHeading `0x34`, MapMarker `0x10`, NorthMarker `0x03` all keep their
Oblivion FormIDs. Six do not, counted from `export/FalloutNV.esm/REFR.txt`:

| EditorID | FormID | REFRs |
|---|---|---:|
| CollisionMarker | `0x21` | 2,761 |
| RoomMarker | `0x1F` | 679 |
| AudioMarker | `0x23` | 577 |
| COCMarkerHeading | `0x32` | 135 |
| RadiationMarker | `0x33` | 119 |
| MultiBoundMarker | `0x15` | 2 |

4,273 references in all, `CollisionMarker` alone accounting for two thirds and
concentrated in interiors.

🛑 **These must never be merged into the shared TES4 table.** FO3/FNV reuse the
low FormID space Oblivion fills with real content -- measured in both
Oblivion.esm and Nehrim.esm:

| FormID | FO3/FNV | Oblivion / Nehrim |
|---|---|---|
| `0x15` | MultiBoundMarker | `CLOT` JailPants |
| `0x1F` | RoomMarker | `STAT` FlameNode1 |
| `0x21` | CollisionMarker | `STAT` FlameNode3 |
| `0x23` | AudioMarker | `STAT` FlameNode5 |

Merging the two tables would turn Oblivion's flame nodes and a pair of trousers
invisible. The FO3/FNV table is selected per source instead.

## <a id="fnv-biped-slots"></a>FNV biped slots

FO3/FNV `BMDT.BipedFlags` is a **20-bit** field (`itU32`); Oblivion's is
**16-bit** (`itU16`), and the two share only bits 0-2. From bit 3 they mean
entirely different things, so reading FNV flags through `BIPED_SLOT_MAP`
silently puts armor in the wrong slot rather than failing.

| Bit | FNV | Oblivion | Skyrim slot chosen |
|---:|---|---|---|
| 3 | Left Hand | Lower Body | 33-Hands |
| 4 | Right Hand | Hand | 59-Right hand ([body slots](asset_convert_armor.md#body-slot-layout)) |
| 5 | Weapon | Foot | *dropped — not a wearable slot* |
| 6 | PipBoy | Right Ring | 34-Forearms |
| 7 | Backpack | Left Ring | 46-Unnamed |
| 8 | Necklace | Amulet | 35-Amulet |
| 9 | Headband | Weapon | 42-Circlet |
| 10 | Hat | Back Weapon | 31-Hair |
| 11 | Eye Glasses | Side Weapon | 42-Circlet |
| 12 | Nose Ring | Quiver | 43-Ears |
| 13 | Earrings | Shield | 43-Ears |
| 14 | Mask | Torch | 30-Head |
| 15 | Choker | Tail | 35-Amulet |
| 16-19 | Mouth Object, Body AddOn 1-3 | — | 43-Ears, 47, 48, 60 (49 is the lower body) |

Censused over all 393 FNV ARMO records, the bits actually authored and what
the Oblivion table made of them:

| Bit | Records | FNV meaning | Was read as |
|---:|---:|---|---|
| 9 | 131 | Headband | Weapon |
| 10 | 104 | Hat | Back Weapon |
| 14 | 57 | Mask | Torch |
| 11 | 33 | Eye Glasses | Side Weapon |
| 2 | 231 | Upper Body | Upper Body (correct) |
| 1 | 87 | Hair | Hair (correct) |
| 0 | 18 | Head | Head (correct) |

So 325 of 393 head-and-face items were landing in weapon slots. Only bits
0-2 (336 records) were ever right.

Several FNV slots collapse onto one Skyrim slot because Skyrim has no
equivalent: both hands share 33-Hands, and the four head-accessory bits
distribute across Circlet/Ears/Head by where the item actually sits. Weapon
(bit 5) is dropped outright — an ARMO occupying it would block the weapon.

### <a id="upper-body-covers-feet"></a>Upper Body covers the feet

FNV's naked body is one `characters\_male\upperbody.nif` (torso, arms, legs
and feet as a single skin) plus `lefthand.nif` / `righthand.nif`; there is no
foot mesh and no foot slot. So an Upper Body item is a whole-body outfit with
its footwear built in. Mapping bit 2 to 32-Body alone left the built ARMA at
Body+Forearms (BOD2 `0x14`), and Skyrim's own feet addon kept rendering over
the outfit's boots (the Caravaneer Outfit, flags 4, as do 222 of the 380 FNV
ARMO records). `biped_slot_tables` therefore adds 37-Feet to Upper Body for a
Fallout source, and `ARMA_BODY_COVERAGE_EXTRA` then brings in Calves.

The mesh side keeps a single 32 partition for the whole outfit
(`wearable_plan_falloutnv.FNV_BIPED_BIT_BODY_PART`): a partition renders
wherever the ARMA claims its slot, and letting the bone-mass resolver split
the pants shape between 32 and 37 gains nothing.

## <a id="child-worldspaces"></a>Child worldspaces: the engine defaults PNAM to *everything*

`TESWorldSpace::Load` (SkyrimSE.exe GOG/AE, RVA `0x2c5620`) handles WNAM at
`+0x2c5c43`: it stores the parent FormID and then writes **0xFFFF** into the
parent-use flags (`mov word ptr [r15+0xa2], 0xffff` at `+0x2c5c79`). A PNAM
chunk, which follows WNAM in every authored file, overwrites that field at
`+0x2c5c86`. `InitializeData` (`+0x2c5120`) zeroes the same field, so 0xFFFF is
specifically the "has a parent but said nothing" default. Skyrim.esm authors
PNAM on 34/34 child worldspaces and xEdit marks it `SetRequired`, so nothing
vanilla ever exercises that default.

`convert_WRLD` wrote WNAM without PNAM, so every converted child worldspace
borrowed its parent's **land, LOD, map, water, climate and sky cell**. On FNV
that is the "entering Freeside puts me somewhere else in the Mojave" report:
FreesideNorthWorld's 108 LAND records were ignored for WastelandNV's terrain
at Freeside's own coordinates (the gate ref sits at (9450, -7035), wasteland
grid (2, -2)). Every record involved was correct: the door REFR lands in the
child's persistent cell with a matching XTEL, and the 172 cells and 2,102
REFRs all sit under the child's group, which is why record audits found
nothing.

| Field | FNV authored | TES5 written |
|---|---|---|
| PNAM | `0x0004` Use Map Data, on all 10 children | authored `& 0x5F` (FO3 bit 5 *Use Image Space* has no TES5 bit) |
| PNAM | absent (TES4 has no PNAM) | absent, so the engine's `0xFFFF` applies: Oblivion draws the PARENT's land under a child, and the child's own LAND is unfinished (ICImperialPalace: 47 LAND over 58 cells, 35 with flags `0x401` and no texture layers, heights differing from Tamriel's at the same grid). Writing `0x0004` here rendered that junk land and open water in the IC palace and arena worlds; writing `0x005F` lost the parent's distant LOD in AnvilWorld and the other city worlds |
| ONAM | scale 0.7, offset (-16000, 103000) for the Freeside worlds | authored (scale, x, y, 0); was a constant (1, 0, 0, 0) |
| NAM4 | -2300 on WastelandNV | authored; TES4 sea level stays 0 |
| DNAM | authored land/water defaults | authored; TES4 keeps (-2048, 0) |
| DATA | bit 5 No LOD Noise, bit 6 no NPC fall damage, bit 7 needs water adjustment | bits 0-1 shared, bit 4 to bit 3 (No LOD Water), the rest dropped; bit 7 would have read as TES5 *No Grass* on WastelandNV (`DATA=0x80`) |

ONAM is how FNV places a child on the parent's map: Freeside's gate at
0.7 x (9450, -7035) + (-16000, 103000) = (-9385, 98075) against the wasteland
gate's (-9815, 102729). The identity ONAM put the marker at raw child
coordinates instead.

Oblivion's own children get no PNAM at all, never a Fallout-derived value.
`0x005F` is NOT the engine default: it drops bit 5, which vanilla sets on the
cities that show Tamriel's LOD (WhiterunWorld and WindhelmWorld author `0x7F`,
RiftenWorld `0x77`). Only the authored FO3/FNV PNAM is written. With no PNAM,
terrain LOD still treats a TES4 child as borrowing the parent's grid; see
[asset_convert_terrain.md](asset_convert_terrain.md#child-worldspaces-with-their-own-lod).


## <a id="humanoid-races"></a>Humanoid races map onto Skyrim playable races

**Code:** `tes5_import/record_types/race_falloutnv.py`

FNV ships 22 RACE records and shares no FormID with Oblivion, so
`TES4_RACE_FID_TO_EDID` missed on every one and all 3,816 FalloutNV.esm NPCs
resolved to a single race. Two paths produced that: FNV `Caucasian` is
`0x00000019`, which Oblivion's table already spends on `VampireRace -> Imperial`,
so 1,815 actors hit it by collision; the other 2,001 fell to `DEFAULT_RACE`
(Nord). The collision is why FNV needs its own dict rather than extra entries
in the Oblivion one.

The miss also silently emptied every downstream race-keyed table: `FTST` was
written 0 times, and `_resolve_eyes_hdpt` returned 2 head parts for all 3,816
actors. Hair was unaffected -- `convert_HAIR` keys on the source FormID, so
2,767 converted FNV hairstyles resolved correctly throughout.

Measured population (`export/FalloutNV.esm/NPC_.txt`, 3,816 NPCs):

| Race | NPCs | Race | NPCs |
|---|---:|---|---:|
| Caucasian | 1,815 | Ghoul | 55 |
| AfricanAmerican | 509 | Old (4 races) | 145 |
| Hispanic | 412 | OldAged (4 races) | 31 |
| Asian | 274 | Child (4 races) | 37 |
| Raider (4 races) | 538 | | |

The four ethnicities plus their Raider variants are 3,548 of 3,816 (93%).

Ethnicity choice is by skin tone, the only axis Skyrim races vary on that FNV
also authors (`_SKIN_FALLBACK_RGB` in `npc_face_mapper.py`): Nord (234,162,145)
is the lightest human race, Redguard (118,60,35) by far the darkest, Imperial
(186,120,80) the mid-brown between. Skyrim has no Asian race; Breton
(224,164,120) is chosen to keep four ethnicities on four distinct tints rather
than collapsing Asian and Hispanic onto Imperial together (686 actors).

Skyrim's human races differ in tint and head texture, not bone geometry, so this
restores variety and tone, not facial structure. Only converted FNV head meshes
would do that, and FNV `HDPT` does not decode yet (exported as
`# Unknown record type: HDPT`, 61 records, sizes only).

Raider/Old/OldAged resolve to their base ethnicity: they differ from it by
texture paths and FaceGen coefficients, not skeleton or head structure, and the
per-actor face already rides on `NPC_.FGGS` -> `NAM9`, which needs no race table
(3,027 FNV actors already carried non-neutral morphs before this change).

Ghoul is deliberately left unmapped -- it falls through to the Oblivion default.
Aliasing it to a human race makes ghouls look human, which is worse than a wrong
ethnicity; it needs `HeadGhoul.NIF` registered as a `head_fit.py` race pack.
Child likewise: FNV children are a 0.8-scale variant with `DATA.Flags` bit 2 and
child head/body meshes, and Skyrim's child races carry their own skeleton and
armor-race handling.

## <a id="voice-files"></a>Voice files: no gender level, Ogg Vorbis

**Code:** `asset_convert/audio/audio_falloutnv.py`

Oblivion and FO3/FNV disagree on both the voice tree shape and the codec, and
either difference alone zeroes the voice stage:

| | Oblivion | FO3/FNV |
|---|---|---|
| Path | `voice/<plugin>/<race FULL>/<m\|f>/` | `voice/<plugin>/<voice type>/` |
| Codec | `.mp3` | `.ogg` (52,896 of 52,936 files in FalloutNV.esm) |
| Folder identity | race display name | VTYP EditorID, gender in the name |

`organize_voice_files` walked a fixed plugin -> race -> gender -> files tree, so
on FNV the gender level bound to `.ogg` FILES, `is_dir()` skipped every one, and
the file loop was never reached -- 0 organised, reported as "all already present
or no files found". Even past the walk, `VOICE_FILENAME_RE` accepted only
`mp3|wav|xwm|fuz`, so all 52,896 would have counted as `no_match`.

FNV needs no race->voice mapping: the folder name IS the VTYP EditorID
(`femaleadult01default` -> `FemaleAdult01Default`), and the importer stamps the
same id from `NPC_.VTCK`, so the two sides agree by construction. That is why
the exporter now emits `VTCK.Voice` -- Oblivion resolves voice through the RACE
record's VNAM chain and never needed it, but in FNV it is the only authored
link between an actor and its recordings.

Gender is read off the folder prefix only for callers that still want it.
Robot and creature voices (`robotvictor`, `creatureferalghoul`) match neither
prefix and stay male, which is what they are.

## <a id="acbs-lost-its-spellpoints"></a>ACBS lost its SpellPoints

**Code:** `tes4_export/record_types/falloutnv.py` `_emit_actor_acbs`

TES4's ACBS is 16 bytes and spends bytes 4-5 on `SpellPoints`. FO3/FNV's is 24
and has no such field: `Fatigue` moves up into 4, and everything after it sits
two bytes earlier than the shared exporter in `record_types/actors.py` expects.
The tail — `SpeedMultiplier` (14), `Karma` (16), `Disposition` (20) and
`TemplateFlags` (22) — has no TES4 counterpart at all.

Nothing about the mis-read is obvious downstream, because every field lands on
a real neighbour rather than on garbage. `FalloutNV.esm` CREA `00156782`
(`VSpawnTier3GiantRadscorpionMed`, raw ACBS
`40020000 3200 0000 0100 0000 0000 6400 00000000 2300 df01`) genuinely holds
Fatigue=50, BarterGold=0, Level=1, CalcMin=0, CalcMax=100, TemplateFlags=0x01DF.
Read as TES4 it exported SpellPoints=50, Fatigue=0, BarterGold=1, Level=0,
CalcMin=100, CalcMax=0 — a plausible record made entirely of shifted fields.

The size is the discriminator: 24 bytes means FO3/FNV, 16 means TES4. The
delta emitter re-emits the six shared keys with the right offsets and
`format_record` drops the TES4-layout lines (`SUPERSEDED_ACTOR_KEYS`) so the
text parser never sees a duplicate key, which it would turn into a list.

CREA `DATA` is mis-sized the same way: 17 bytes in FO3/FNV against TES4's 20,
with no `Soul` and seven attributes instead of eight. The shared exporter
guards on `len >= 20`, so it silently emitted no creature stats whatsoever.

## <a id="lvln-is-a-native-type"></a>LVLN is a native FO3/FNV type

TES4 has one leveled-actor list, `LVLC`. FO3/FNV adds `LVLN` "Leveled NPC"
(`wbDefinitionsFNV.pas:6686`), structurally identical to LVLI — EDID, OBND,
LVLD chance-none, LVLF flags and an LVLO entry array — whose entries point at
`[LVLN, NPC_]`.

It exported as an unparsed stub, so its entries could not be walked. That
matters because an FNV spawn stub's TPLT commonly points at an LVLN rather than
an LVLC: `LvlWastelander` → `0002E2A4 VarWastelander`, `LvlBrotherhoodOfSteelGun`
→ `00000A87`. With the entries missing, the template chain dead-ended and
**258 spawn stubs kept inheriting nothing**, so they shipped nameless.

Skyrim has LVLN natively and the importer already converts TES4's LVLC into
one, so the entries only have to be read.

## <a id="aidt-gained-a-mood-byte"></a>AIDT gained a Mood byte

**Code:** `tes4_export/record_types/falloutnv.py` `_emit_actor_aidt`

TES4's AIDT is 12 bytes and puts the service bitmask at offset 4. FO3/FNV's is
20: `Mood` takes byte 4 followed by 3 unused (xEdit notes it is stored as a
DWord but truncated to a byte on load), pushing services to 8 and the trainer
pair to 12-13. Bytes 14-19 — `Assistance`, `Aggro Radius Behavior` and a s32
`Aggro Radius` — have no TES4 counterpart.

Offsets 0-3 (Aggression, Confidence, Energy Level, Responsibility) are shared,
so the delta emitter re-emits only the tail and leaves those to the shared
exporter in `record_types/actors.py`.

Unlike the ACBS shift, this one produces visible garbage rather than a
plausible neighbour, because the mis-read straddles the aggro radius. Across
`FalloutNV.esm`'s 5,394 actors the TES4 layout exported `AIDT.Services` values
like 1885586944 and 757935360, and `Teaches` of 127/126/124 against a
`wbSkillEnum` that stops well short of those.

The values at 0-3 are *not* interchangeable even though the offsets are:
FO3/FNV Aggression is `wbAggressionEnum` (0-3) and Confidence is
`wbConfidenceEnum` (0-4), where TES4 uses 0-100 scalars for both. That is an
import-side concern — see
[tes5_import_falloutnv_actors.md](tes5_import_falloutnv_actors.md#aggression-is-already-a-tier).

## <a id="tplt-carries-the-whole-actor"></a>TPLT carries the whole actor

**Code:** `tes4_export/record_types/falloutnv.py` `_emit_actor_template`

FO3/FNV actors inherit by category. A `TPLT` names another actor (CREA/LVLC for
CREA, NPC_/LVLN for NPC_) and ACBS `TemplateFlags` says which categories come
from it — bit 6 is Model/Animation. A spawn stub therefore carries no MODL, no
race, and no stats: `00156782` is EDID/OBND/EAMT/NIFT/ACBS/TPLT/AIDT/PKID/DATA
plus a few tail subrecords, and its mesh lives two links away
(LVLC `001567A0` → CREA `0014F401` → `0001CF9E`, `Creatures\Radscorpion\Skeleton.nif`).

TES4 has no equivalent, so the exporter never emitted TPLT and every such stub
arrived modelless. `convert_CREA` then failed `get_creature_race` (no model, no
generated race) and fell through to `resolve_creature_race(edid, full)`, whose
keyword list matches the substring `giant` in `VSpawnTier3GiantRadscorpionMed`
and returns Skyrim's `GiantRace` (0x000131F9). The radscorpion's own converted
mesh and animations were built and sitting unused; only the pointer was missing.

Skyrim expresses the same idea with the same subrecord, and the importer
already builds that shape for Oblivion's placed-LVLC case in
`tes5_import/actors/leveled_actors.py` — an actor whose TPLT and Template Flags make
it a pure indirection. An FNV stub is that record already, so carrying TPLT and
`TemplateFlags` through is the whole conversion.

## <a id="effects-are-a-different-shape"></a>Effects are a different shape, and carry conditions

**Code:** `tes4_export/record_types/falloutnv.py` `_emit_effect_deltas`

Both games write EFID/EFIT pairs, so `common.emit_effects` looked shared. It is
not, and the mismatch was total: **all 1,312 FO3/FNV EFIT blocks were dropped**
(ENCH 251, SPEL 446, ALCH 614, INGR 1), leaving every spell, enchantment and
consumable with an effect count and no effects.

Two independent differences, both fatal to the TES4 reader:

| | EFID | EFIT |
|---|---|---|
| TES4 (measured, Nehrim) | 4-char code | **24 B**, repeating the code first |
| FO3/FNV (measured) | **FormID** | **20 B**, no code prefix |

`emit_effects` decodes EFID as ASCII and gates EFIT behind `len >= 24`, so on
FO3/FNV the guard never passes and the EFID prints as mojibake. The FO3/FNV
reader takes the 20-byte layout and resolves the FormID to its MGEF EditorID —
which is what `tes5_import` keys `_code_to_fid` on, so the existing import path
then works unchanged.

### A CTDA belongs to the effect it follows

FO3/FNV conditions an INDIVIDUAL effect; TES4 has no such field. **508 effect
conditions** exist (ALCH 341, SPEL 85, ENCH 82), and the most common function by
far is **586 `IsHardcore`, at 191 occurrences** — the switch a consumable uses to
do nothing outside hardcore mode.

Attribution is positional: the CTDAs after an EFIT belong to that effect, so the
subrecords are walked in order. Gathering them per signature, as the other
emitters do, cannot say which effect a condition guards.

`IsHardcore` gates the dehydration effect only. It does NOT gate the
`PreordVault13CanteenQuest` sip message, which repeats because the quest's
authored delay was dropped — see [Quest delay and objectives](#quest-delay-and-objectives).

🛑 Skyrim has **no function 586** (580, 584, 589 are assigned; 586 is not).
Fallout indices are reconciled at import by
[the generated remap](tes5_import_conditions.md#fallout-ctda), never emitted raw.

## <a id="quest-delay-and-objectives"></a>Quest delay and objectives

**Code:** `tes4_export/record_types/quest_falloutnv.py`

FNV's QUST differs from TES4's in two places the shared `export_QUST` cannot
see (xEdit `wbDefinitionsFNV.pas` vs `wbDefinitionsTES4.pas`):

| | TES4 | FO3/FNV |
|---|---|---|
| `DATA` | 2 B: Flags, Priority | **8 B**: Flags, Priority, 2 unused, **Quest Delay float** |
| Targets | bare `QSTA` list | nested in **Objectives**: `QOBJ` index, `NNAM` text, then `QSTA` + conditions |

### <a id="quest-delay"></a>The delay is the poll cadence

The float is the seconds between quest-script passes. Measured over the 441
FNV quests: 359 write 0.0 (engine default), 82 write an authored value from
0.001 to 300.0. `PreordVault13CanteenQuest` writes **300.0** — the sip message
is scripted to fire every 5 real minutes, and with the field dropped the
converted poll ran at 0.5 s, 600x too often.

xEdit labels flag `0x10` "Default Script Processing Delay", but the data says
the engine does not consult it: the canteen quest sets `0x10` AND writes 300.0,
`VERGoodsprings01` sets it with 17.0, `VMS01Nightkin` with 60.0, while the
`*Timer`/`*FX` quests with 0.1 leave it clear. Every non-zero value reads as
authored, so the export emits `DATA.Delay` unconditionally and the script
converter honors any non-zero value
([poll interval](script_convert.md#poll-interval)).

### <a id="objectives"></a>Objectives are authored, not derived

125 of the 441 quests carry `QOBJ` objectives (1,045 in all), keyed by their
own index, not the stage index: `VMQ01`'s stages are 10/15/40/50/60/100 and its
objectives 10/20/25/30/34/36/38/40/45/46/50/60. Their `NNAM` is the journal
line; most FNV stages carry **no** `CNAM` at all (VMQ01 has one, "Travel to
Novac."). The shared walker treated the first `QSTA` as the start of a flat
target list, so every `QOBJ`/`NNAM` fell through and the flat `Target[]` lost
the objective grouping.

The FNV emitter writes `Objective[i].Index/.Text` and, under each,
`Target[j].FormID/.Flags/.Condition[k].Raw`, and `export_falloutnv.superseded_keys`
drops the shared `Target[]` lines for QUST. FNV `QSTA` is FormID + a u8 flag +
3 unused bytes; the TES4 reader unpacked those four bytes as one u32 flag word.
Target conditions: 1,229, Run On 2 (Reference) on 844 of them.

## <a id="info-end-script"></a>INFO carries two result scripts; the exporter dumped one

**Code:** `tes4_export/record_types/falloutnv.py:info_result_scripts`,
`tes5_import/base/text_reader.py:info_result_script`.

An FO3/FNV INFO embeds a *Begin* script and an *End* script, back to back and
split by the `NEXT` marker (xEdit `wbDefinitionsFNV`: `Script (Begin)`, then
`NEXT` + `Script (End)`). TES4 embeds one. The shared INFO exporter took the
FIRST `SCTX` in the stream, so an INFO whose Begin was empty exported its End
text as `ResultScript`, and an INFO with both lost its End text entirely.

Measured on FalloutNV.esm: 23,247 INFO records, 4,499 with a non-empty End
script. `VCG01` (Doc Mitchell's opening) parks at stage 80 on this: the psych
test's closing lines (`VCG01DocMitchellTopic072`..`075`, "Well, that's all she
wrote.") bump `VCG01Test` counters in Begin and carry `SetStage VCG01 85` in
End, and nothing else in the plugin sets 85.

The exporter now walks the subrecords positionally and emits `ResultScript`
(Begin) and `ResultScriptEnd` (End). Every consumer reads the pair through
`info_result_script`, which joins them Begin-then-End: the converter already
runs a TES4 result script in the INFO's End fragment, so both halves land
there in authored order.

## <a id="trigger-primitives"></a>Trigger volumes are XPRM primitives, and the importer dropped them

**Code:** `tes4_export/record_types/falloutnv.py:_emit_refr_deltas`,
`tes5_import/record_types/world.py:_refr_head`.

FO3/FNV place a trigger as a REFR of a model-less ACTI plus an `XPRM`
primitive (bounds, RGBA, type) — 5,677 REFRs in FalloutNV.esm carry one. TES5's
`XPRM` has the identical 32-byte layout (xEdit: bounds, `wbFloatRGBA`, type
enum with Box = 1), and vanilla Skyrim triggers are the same shape
(`DA09Trigger`'s REFR is NAME + XPRM alone). The exporter only dumped the
bounds and the importer wrote no XPRM at all, so every converted trigger
volume was a bare REFR with nothing to enter: `OnTriggerEnter` never fired.
VCG01 hung on this — `VCG01VigorTesterTriggerSCRIPT` sets stage 60 from
`onTriggerEnter`, and the tester's `OnActivate` only advances at stage 60.
The raw subrecord now round-trips as `XPRM.Raw`.
