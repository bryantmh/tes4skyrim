# tes5_import/record_types/magic.py - magic conversion

**Code:** `tes5_import/record_types/equipment.py`, `tes5_import/record_types/magic.py`, `tes5_import/record_types/magic_variants.py`, `tes5_import/record_types/magic_art.py`, `tes5_import/generated/vanilla_mgef_data.py`, `asset_convert/character/skyrim_overrides.py`

## Contents

- [The problem Phase 1 solved](#problem-phase-1)
- [The four defects, in priority order](#four-defects-priority-order)
- [Path to complete conversion](#path-complete-conversion)
- [Casting type and delivery follow the owner](#owner-casting-type)
- [Effect families: HasMagicEffect tests a keyword](#effect-families)
- [Magic art](#magic-art)
- [Effect shader particle counts](#effect-shader-particles)
- [Impact data](#impact-data)
- [Menu display object](#menu-display-object)
- [Rules for working in this area](#rules-working-this-area)
- [Enchantment charge (ANAM to EAMT)](#enchantment-charge-eamt)

Status as of 2026-07-31. Measured with `python tools/audit/magic_audit.py export/<Plugin>`
(written alongside this doc; re-run it after every change in this area).

**Phase 1 is DONE (2026-07-31).** `MGEF` is a converted record type
(`tes5_import/record_types/magic.py`); the numbers below are the *before*
picture, kept because they are what the remaining phases are measured against.
Current state:

| | Oblivion.esm | Nehrim.esm |
|---|---|---|
| source MGEF records | 145 | 149 |
| converted as our own MGEF | **145** | **149** |
| unmapped → effect dropped | **0** | **0** |
| records losing ALL effects → filler | **0** | **0** |
| phantom table keys (no export uses them) | **0** | **0** |

## The problem Phase 1 solved
<a id="problem-phase-1"></a>

`MGEF` was in `SKIP_TYPES`. Because no magic effect was ever converted, every
effect on every SPEL/ENCH/ALCH/INGR/SGST was re-pointed at a **vanilla Skyrim
MGEF** through a flat 4-char code table (`MGEF_CODE_TO_SKYRIM` /
`MGEF_AV_CODE_TO_SKYRIM` in `skyrim_overrides.py`), and anything the table
could not name was **silently dropped** (`_pack_effects`,
`record_types/equipment.py`).

Measured fallout, before the fix:

| | Oblivion.esm | Nehrim.esm |
|---|---|---|
| source MGEF records | 145 | 149 |
| mapped to a vanilla effect | 74 | 75 |
| **unmapped → effect dropped** | **71** | **74** |
| distinct Skyrim targets used | 51 | 51 |
| records losing **ALL** effects → filler | **382** | **356** |
| SPEL effects dropped | 375 / 1856 (20.2%) | 274 / 1138 (24.1%) |
| ENCH effects dropped | 298 / 2411 (12.4%) | 246 / 2745 (9.0%) |

382 Oblivion records (201 SPEL, 154 ENCH, 22 ALCH, 5 INGR) converted to a
zero-magnitude `AlchRestoreHealth` filler — they existed, they were castable,
and they did nothing. **330 NPC spell-list entries pointed at one of the 201
gutted spells.** Every summon spell in the game was in that set.

The one thing that is *not* a gap: the summon/bound targets are all real
records this pipeline already converts — of 118 MGEFs carrying an `AssocItem`,
33 resolve to a CREA, 13 to a WEAP, 8 to an ARMO, 4 to an NPC\_ (the remaining
60 are actor-value indices, not FormIDs). And all 22 `meshes/magiceffects/*.nif`
are already converted and sitting unused in `output/`.

## The four defects, in priority order
<a id="four-defects-priority-order"></a>

### 1. Whole effect families are dropped because MGEF is never converted

The flat table can only express "Oblivion effect X behaves like Skyrim effect
Y". That works for value modifiers (Restore Health → `AlchRestoreHealth`) and
fails completely for effects whose behaviour is *parameterised by a FormID the
source record carries*:

- **All 33 summons** (`Z001`–`Z019`, `ZCLA`, `ZDAE`, `ZDRE`, `ZFIA`, `ZFRA`,
  `ZGHO`, `ZLIC`, `ZSCA`, `ZSKE`, `ZSPD`, `ZSTA`, `ZWRA`, `ZXIV`, `ZZOM`, …).
  Each is `AssocItem` = the creature to summon. There is no "summon *anything*"
  vanilla effect to point at, so all 33 are unmapped and dropped.
- **Bound weapons/armor** (`BWSW`, `BACU`, `BW01`–`BW08`, `MYHL`): `AssocItem`
  = the WEAP/ARMO to conjure.
- Effects with a plain Skyrim archetype that simply has no vanilla
  value-modifier stand-in: `OPEN` (16 uses), `DSPL` Dispel (55), `TURN` Turn
  Undead (32), `NEYE` Night-Eye (41), `CHRM` Charm (23), `WABR` Water Breathing
  (48), `WAWA` Water Walking (37), `DIAR`/`DIWE` Disintegrate (57).

Skyrim's MGEF `Archtype` enum covers essentially all of these — `18 Summon
Creature`, `17 Bound Weapon`, `16 Open`, `2 Dispel`, `24 Turn Undead`,
`12 Light`, `35 Cloak`, `21 Paralysis`, `1 Script`
(`wbDefinitionsTES5.pas:8145`). The conversion is *possible*; it just requires
emitting MGEF records instead of aliasing to vanilla ones.

### 2. 17 of the 100 table entries are codes that do not exist

The map was written against effect names rather than against the export.
These keys match **no MGEF in either Oblivion or Nehrim** and can never fire:

```
BACT RFDG SMAC SMBO SMCL SMDM SMFL SMFR SMGH SMLI SMSK SMSP SMZB
TNUN WBUA WKFW WKSK
```

The real Oblivion codes for those concepts are `ZFIA`/`ZFRA`/`ZSTA`/`ZDRE`
(summons), `TURN` (turn undead), `WABR` (water breathing), `WKFI`/`WKSH`
(weaknesses) — all of which are in the *unmapped* list. So the summon entries
in the table look like coverage but contribute nothing, while the codes the
game actually uses fall through to 0. `BACU` (Bound Cuirass, 8 uses) is
unmapped while the phantom `BACT` is mapped.

**Every new entry must be validated against `export/*/MGEF.txt`**, not against
a name. `tools/audit/magic_audit.py` reports phantom keys.

### 3. ~~The vanilla DATA blobs are truncated — 96 bytes where 152 are required~~ — FIXED 2026-07-25

`tes5_import/generated/vanilla_mgef_data.py` claimed "152-byte DATA hex" in its
docstring. Every one of its 80 blobs was **96 bytes**.

Cause: `tools/generators/gen_vanilla_mgef_table.py:read_dump` did
`line[9:].split('...')[0]`, and the generic hex fallback in the dump writer
truncates at 192 chars (`tools/esm/tes5_esm_reader.py`) — so the `...` split
silently discarded the tail instead of failing.

Consequence: every MGEF synthesized by `magic_effects.aimed_variant()` was
written with a 96-byte DATA, missing the 14 fields from offset 96 to 151:
`Hit Effect Art`, `Impact Data`, `Skill Usage Multiplier`, `Dual Casting
Art/Scale`, `Enchant Art`, `Hit Visuals`, `Enchant Visuals`, `Equip Ability`,
`Image Space Modifier`, `Perk to Apply`, `Casting Sound Level`, and `Script
Effect AI Score/Delay`.

Resolved by Phase 0 below — all 80 blobs are now full 152-byte structs,
byte-verified against the dump they are generated from.

### 4. All per-effect art, sound and counter-effect data is discarded

Per MGEF, the source carries and the conversion drops:

| field | Oblivion MGEFs carrying it |
|---|---|
| `Model.MODL` (cast art) | 141 |
| casting/bolt/hit/area sounds | 144 |
| `EffectShader` / `EnchantEffect` | 83 |
| `AssocItem` | 118 |
| `Light` | 87 |
| counter effects (ESCE) | 41 |

`EFSH` *is* converted (`record_types/world.py:856`) but nothing references the
result for magic, because no MGEF is written. The 22 magic-effect meshes are
converted and unused. Since the mesh pipeline is now solid, this data is
recoverable — it just has nowhere to land today.

Note also that `EFSH` conversion itself is partial: it writes a 128-byte DATA
populated only through offset 44 (flags, fill color, 6 fill-alpha floats) and
zeroes the rest.

Supporting record types with **no writer at all**: `ARTO`, `RFCT`, `IPDS`,
`EXPL`, `PROJ`, `HAZD`. `ARTO` (art object) is what Skyrim uses for
casting/hit art — it is the destination for those 141 `Model.MODL` paths.
(`SCRL` gained one in Phase 1: sigil stones and enchanted books both write it.)

## Path to complete conversion
<a id="path-complete-conversion"></a>

Ordered so each phase is independently shippable and testable. Phase 0 is a
prerequisite for everything after it.

### Phase 0 — Fix the truncated DATA table (prerequisite) — DONE 2026-07-25

1. **Typed MGEF DATA decoder** (`_dec_mgef_data` in `tools/esm/tes5_esm_reader.py`,
   registered as `('MGEF', 'DATA')`). It emits the hex **untruncated** plus all
   38 named fields with the archetype enum resolved, so the dump is both
   lossless for the generator and readable for analysis. Adding a typed decoder
   rather than raising the global 96-byte hex cap keeps every other record
   type's dump size unchanged.
2. **Regenerated** `references/Skyrim.esm/MGEF.txt` from the real
   `Skyrim.esm` — 950 records, every `DATA.hex` now 152 bytes.
3. **Generator now fails loudly** — `read_dump` no longer does
   `split('...')`; a blob that is not `MGEF_DATA_SIZE` raises `SystemExit`
   naming the offending effects and the exact regeneration command. The
   "wanted FormID not in Skyrim.esm" case is called out as a mapping table
   pointing at an effect that does not exist.
4. **Consumer asserts too** — `magic_effects.aimed_variant()` raises rather
   than writing a short DATA, so a bad table can never reach the output ESM.
5. The generated module now exports `MGEF_DATA_SIZE` alongside the table.

Verified: all 80 blobs are 152 bytes and match the dump **byte-for-byte**
(80/80); a synthesized clone writes a 152-byte DATA with Casting Type=1,
Delivery=2 (Aimed), a real projectile, zeroed counter count, and an intact
tail (`DualCastScale == 1.0` at offset 112 — past the old 96-byte cut).
Re-truncating the dump reproduces the original bug and the generator now exits
1. Regression tests: `TestVanillaMgefDataSize` in `tests/test_import.py`.

Immediate benefit: the aimed-variant clones stop shipping a malformed DATA.

### Phase 1 — Convert MGEF as a real record type — DONE 2026-07-31

`'MGEF'` is out of `SKIP_TYPES`; `convert_MGEF` lives in
`tes5_import/record_types/magic.py` and emits `EDID`, `VMAD`, `FULL`,
`DATA` (152 bytes, FormVersion 44), the `ESCE` array and `DNAM`.

What actually shipped, beyond the sketch below:

1. **Archetype table, validated against the export.** `EFFECT_ARCHETYPES` maps
   all **161 codes** any export defines (Oblivion 145, Nehrim 149,
   Morrowind_ob's masters, the DLCs) to a `(archetype, actor value)` pair. Zero
   missing, zero phantom — enforced by
   `tests/test_import.py::TestMgefConversion::test_every_source_effect_code_has_an_archetype`,
   which reads `export/*/MGEF.txt` rather than trusting a name. The 17 phantom
   keys are deleted from `MGEF_CODE_TO_SKYRIM`, which now survives only as the
   fallback for a plugin whose MGEFs were never exported.

2. **Per-actor-value variants.** Oblivion parameterises one MGEF by the
   attribute or skill each *effect* names — a single `DGAT` is Damage Strength
   on one spell and Damage Endurance on the next, because the AV lives in the
   item's EFIT. Skyrim moved the AV **onto the MGEF**, so a single converted
   `DGAT` could only ever damage one stat. `build_av_variants()` emits one MGEF
   per `(code, actor value)` pair the plugin actually uses (**100 for Oblivion,
   100 for Nehrim**) covering 1,897 effect uses across 8 codes, and names them
   what the item card said: "Damage Strength", not "Damage Attribute".

3. **Script-effect (`SEFF`) variants — Phase 4, brought forward.** `SEFF` was
   the single most-dropped code (143 uses in Oblivion, 176 in Nehrim) and it
   had to land here, because a TES4 script effect names its script **per
   effect** (`ScriptEffect[i].FormID` on the owning record), not on the MGEF.
   `build_seff_variants()` emits one archetype-1 MGEF per distinct
   `(script, delivery)` pair with the converted `ActiveMagicEffect` attached as
   a `VMAD` — **78 for Oblivion, 100 for Nehrim, 34 for Morrowind_ob**. The
   VMADs come from `object_scripts.build_magic_effect_script_plan()`, which is
   where the property-resolution machinery already lives.

4. **AssocItem is type-checked, not copied.** `wbMGEFAssocItemDecider` reads
   Assoc. Item for **10 archetypes only**, and each expects a specific record
   type — Summon Creature an `NPC_`, Bound Weapon a `WEAP`/`ARMO` (what the
   field *accepts*; the engine only implements weapons — see
   [Bound items](#bound-items)). The
   converter resolves the TES4 FormID through a plugin-wide index
   (`_build_assoc_item_index`, masters included) and drops it under any other
   archetype rather than writing a meaningless FormID. Two Oblivion summons
   point at an **LVLC**, which converts to an `LVLN` the archetype rejects, so
   the list's lowest-level entry stands in (chased transitively through nested
   lists).

5. **Dependent plugins read the master's effects.** A plugin like
   Morrowind_ob.esm defines **no MGEF at all** yet has 109 items carrying
   script effects. The effect index, the AssocItem index and the ENCH index all
   merge `ctx.master_export` the same way the outfit index does — without it
   every effect in such a plugin resolved to nothing.

Two defects surfaced while verifying and were fixed in the same pass:

- **Enchanted books were unusable paper.** A TES4 BOOK carrying an `ENAM` is a
  scroll, and Skyrim's BOOK record has **no field for an object effect** — so
  **503 scrolls** across the three plugins (307 Oblivion, 62 Nehrim, 134
  Morrowind_ob) converted to blank books that could never be cast, the Scroll
  of Icarian Flight among them. `convert_BOOK` now emits a **`SCRL`** for those,
  copying the ENCH's effect list onto it (SCRL carries its effects directly),
  and `import_main` files each record by the signature its own bytes carry
  rather than by a per-signature `TYPE_MAP` entry.
- **`tools/esm/tes5_esm_reader.py` had EFIT Area/Duration swapped**, which made
  every dump of a converted spell look wrong. TES5 EFIT is Magnitude, **Area**,
  **Duration** (xEdit `wbEFIT`); settled by census — all **427 vanilla ALCH
  effects** write 0 at offset 4 and 30/60/300/720 at offset 8, which are potion
  durations, and potions have no area. The writer was always correct.

**Archetype legality.** Vanilla Skyrim.esm never uses archetypes 2 (Dispel),
15 (Lock), 16 (Open) or 24 (Turn Undead), so a census cannot license them.
They are legal anyway: `DispelEffect`, `LockEffect`, `OpenEffect` and
`TurnUndeadEffect` are all present in SkyrimSE.exe as RTTI classes with real
vtables and constructors (read via `tools/disasm/skyrim_disasm.py --find Effect`
against the GOG build). Don't "fix" them back to a value modifier.

Original field-derivation sketch, still accurate:

Field derivation (TES4 offsets from `tes4_export/record_types/equipment.py:219`,
TES5 from `wbDefinitionsTES5.pas:8195`):

| TES5 field | off | source |
|---|---|---|
| Flags | 0 | remap TES4 flag bits (they differ — TES4 0x8 = MagnitudePercent, TES5 0x8 = Snap to Navmesh) |
| Base Cost | 4 | `DATA.BaseCost` |
| Assoc. Item | 8 | `DATA.AssocItem`, load-order remapped, **only when the archetype uses it** |
| Magic Skill | 12 | `DATA.School` → Skyrim AV (Alteration/Conjuration/Destruction/Illusion/Restoration; Mysticism has no equivalent — fold to Alteration) |
| Resist Value | 16 | `DATA.ResistValue` → Skyrim AV |
| Counter Effect Count | 20 | `len(ESCE)` — must match the array exactly |
| Casting Light | 24 | `DATA.Light` |
| Hit/Enchant Shader | 32/36 | converted `EFSH` from `DATA.EffectShader` / `DATA.EnchantEffect` |
| **Archtype** | 64 | from the effect code (table below) |
| Actor Value | 68 | from the effect's TES4 attribute/skill AV |
| Projectile | 72 | needed for aimed delivery (see Phase 3) |
| Casting Type / Delivery | 80/84 | from the TES4 flag bits Self/Touch/Target |

The archetype table replaces the current FormID table as the primary mapping.
Sketch, by family:

- summons (`Z0xx`, `Zxxx`) → `18 Summon Creature`, AssocItem = converted CREA/NPC\_
- bound **weapon** (`BW*`) on a castable spell → `17 Bound Weapon`, AssocItem = converted WEAP
- bound **armor** (`BA*`, `MYHL`, `MYTH`), and any bound item on an Ability/Lesser
  Power → `1 Script` + `TES4_BoundItemEffect` ([Bound items](#bound-items))
- `OPEN` → `16 Open`; `LOCK` → `15 Lock`
- `DSPL` → `2 Dispel`; `TURN` → `24 Turn Undead`
- `CHRM`/`CALM` → `6 Calm`; `DEMO` → `7 Demoralize`; `FRNZ` → `8 Frenzy`; `RALY` → `38 Rally`
- `PARA` → `21 Paralysis`; `REAN` → `22 Reanimate`; `STRP` → `23 Soul Trap`
- `INVI`/`CHML` → `11 Invisibility`; `LGHT` → `12 Light`; `NEYE` → `0` + Night-Eye AV
- `TELE` → `20 Telekinesis`; `DTCT` → `19 Detect Life`
- `FISH`/`FRSH`/`LISH` → `35 Cloak`
- damage/restore/fortify/drain/absorb → `0 Value Modifier` / `4 Absorb` / `5 Dual Value Modifier`, with the AV carrying the meaning
- `SEFF` → `1 Script` (see Phase 4)

Keep the vanilla-alias table **only** as a fallback for codes with no sensible
archetype, and delete the 17 phantom keys.

Verification: `magic_audit.py` unmapped count → 0; no record falls back to
filler; dump a converted summon MGEF and diff its DATA field-by-field against
`SummonFlameAtronach` from the Skyrim.esm dump.

### Phase 2 — Wire the art back in — DONE (user-confirmed in-game)

Covered in the sections below:

- [Magic art](#magic-art): the ARTO, PROJ, EXPL and STAT companions, and the
  SNDD sounds.
- [Casting type and delivery follow the owner](#owner-casting-type).
- [Effect shader particle counts](#effect-shader-particles).
- [Impact data](#impact-data).
- [Menu display object](#menu-display-object).

ESCE counter effects landed with Phase 1.

### Phase 3 — Projectiles and delivery — DONE 2026-08-01 (projectile half)

<a id="aimed-ench-null-projectile"></a>
**An Aimed magic item with no projectile is a HARD CRASH, not a dud cast.**
This was written up as a cosmetic problem ("the item casts NOTHING in game",
`magic_effects.py`'s module docstring, and the CK's "is AIMED but has no Magic
Effects with Projectiles assigned" warning). It is not. Traced through the GOG
1.6.659 exe from a real crash log (Nehrim, `EnStaffFrostDamage` + `FRDG`):

```
MagicItem::GetCostliestEffectItem            0x10c9f0
    GetDelivery()  = EnchantmentItem vtable +0x2b8 -> mov eax,[rcx+0xa0]
    when delivery == 2 (Aimed), SKIPS every effect whose
    EffectSetting+0xC8 (the MGEF Projectile) is null          0x10ca7c
    -> with every effect skipped it returns NULL
combat-AI item rating fn                     0x7fb6c0
    calls the above for any Aimed ENCH/SPEL, then
    mov rdi, [rax+0xC8]     <-- no null check                 0x7fb83e
    -> EXCEPTION_ACCESS_VIOLATION reading 0x00000000000000C8
```

So the game dies the moment an actor's combat AI rates such an item — nothing
to do with the player ever casting it.

**Vanilla census (`references/Skyrim.esm`), zero exceptions:** 43/43 Aimed
`ENCH` and 264/264 Aimed `SPEL` reach a projectile through at least one effect.
Individual *effects* may lack one (23 MGEFs do) — those are always secondary
effects riding alongside a primary that supplies one — so **the invariant is
per item, not per effect.**

Two defects, both fixed:

1. `_build_data` never wrote `_O_PROJECTILE` (offset 72) at all. It now resolves
   one for Aimed/Target-Location deliveries the way vanilla picks them: **resist
   type (element) first, then magic school, with cast type selecting the
   Fire-and-Forget vs Concentration variant** (`_resolve_projectile`). Self,
   Contact and Target Actor still get 0, as vanilla mostly does.
   `HealFakeProjectile` (`0x00012FDC`) is the fallback — vanilla's own
   "needs a projectile but has no visual" stand-in, used by 31 of its MGEFs.
2. `magic_effects.has_projectile()` could not see the converter's own MGEFs.
   Its comment claimed "the only other fids flowing through effect lists are
   variants this module generated" — false since `convert_MGEF` landed, when our
   own records became the *normal* answer from `_resolve_mgef`. It returned
   False for them, then `aimed_variant()` returned 0 (our FormIDs aren't in
   `VANILLA_MGEF_DATA` either), so the guard silently did nothing. It now
   consults `magic.emitted_projectile()`.

Ordering trap: Phase 1 converts record types **alphabetically**, so `ENCH` runs
before `MGEF`. The registry is therefore populated in `register_mgef_formids()`
(Phase 0), not as a side effect of `convert_MGEF`.

Measured after the fix, in the built ESMs: Oblivion 332 Aimed ENCH + 330 Aimed
SPEL, Nehrim 366 + 263 — **1,291 records that all previously carried a null
projectile, now 0 exceptions.** Projectiles are assigned selectively (159/349
Nehrim MGEFs, 161/323 Oblivion), not blanket-applied.

The `PROJ` and `EXPL` writers have since landed ([Magic art](#magic-art)).
`aimed_variant` is retired: every effect slot, filler included, is now cloned
onto its owner's delivery, and `fit_delivery` gives any Aimed clone a
projectile ([Cleanup](#phase-5-cleanup)).

### Phase 4 — Script effects (`SEFF`) — DONE 2026-07-31 (with Phase 1)

The single most-used dropped code — **143 uses in Oblivion, 176 in Nehrim** —
used to map to 0, so a script-effect spell converted to an inert filler that
merely held the original duration so `HasMagicEffectByID` polling still saw it.

Done as part of Phase 1 because it could not be separated from it: a TES4
script effect names its script **per effect**, so archetype 1 forces one MGEF
per distinct script (see Phase 1 item 3). `build_seff_variants()` emits them
with the converted `ActiveMagicEffect` attached as a VMAD.

Two script-side gaps had to be closed for these to be more than inert records
(both found by tracing the Scroll of Icarian Flight end to end):

- **`SetNumericGameSetting` was unconverted** — it fell through as a bare call
  that did not compile. SKSE's `Game.SetGameSettingFloat` is the literal
  counterpart but **does not compile against the vanilla headers this pipeline
  builds with** (verified directly against `papyrus.exe`: "undefined function
  `SetGameSettingFloat`", while the *getter* resolves fine). So the settings
  with a per-actor equivalent go through `Actor.ForceActorValue` instead
  (`_GMST_TO_ACTOR_VALUE` in `script_convert/converter.py`), and the *getter*
  is routed to the same channel — otherwise the save/restore idiom these
  scripts use reads back a number the write never changed. Anything with no
  actor-value equivalent keeps a `;TODO` marker rather than a call that
  silently does nothing.
  - **`fJumpHeightMax` does not exist in Skyrim** (only `fJumpHeightMin`) —
    confirmed against both Skyrim.esm's GMST records and the SkyrimSE.exe
    settings strings. Scripts that set both are writing one real setting and
    one Oblivion dropped.
- **`ResetFallDamageTimer` was a no-op** on one half of a paired on/off
  command — the latent soft-lock in
  [papyrus_conversion_notes.md](script_convert.md). Skyrim keeps the
  console command (opcode 4404) but binds no Papyrus equivalent, and
  `fJumpFallHeightMin` has readers but no vanilla writer. It now calls
  `TES4Polyfill.SuppressFallDamage()`, and the converter **injects the paired
  `RestoreFallDamage()` into the teardown event** (synthesizing an
  `OnEffectFinish` when the script has none), so the resistance cannot outlive
  the effect. `SetGhost`/`SetInvulnerable` were rejected: they suppress ALL
  damage, so a levitation scroll would grant temporary immortality — a worse
  defect than the one being fixed.

<a id="bound-items"></a>
### Bound items — DONE 2026-08-07 (user-confirmed in-game)

**Skyrim has no bound armor.** Archetype `17 Bound Weapon` is a bound *weapon*
implementation. xEdit types the Assoc. Item field as `[WEAP, ARMO, NULL]`, but
that is only what the field **accepts** — it is not evidence the engine equips
armor, and reading it that way cost three round trips. The census settles it:

| | |
|---|---|
| Vanilla archetype-17 effects | 7 |
| …whose AssocItem is a `WEAP` | **7** |
| …whose AssocItem is an `ARMO` | **0** |

Oblivion, by contrast, has a full bound-armor family — `BACU` cuirass, `BAGR`
greaves, `BAGA` gauntlets, `BAHE` helmet, `BABO` boots, `BASH` shield, plus the
Mythic Dawn set (`MYTH`/`MYHL`) — none of which the native archetype can serve.

**Second, independent gap: archetype 17 only fires on a CAST.** Oblivion also
delivers bound gear through Abilities (`SPIT.Type` 4) and Lesser Powers (3),
which Skyrim applies passively and never casts, so even a bound *weapon* dies
when delivered that way. Vanilla census: archetype 17 appears under `Type 0`
only — 8 uses, zero under Type 3/4. Oblivion has 10 Ability + 3 Lesser Power
spells carrying bound effects; the Mythic Dawn assassins in the Imperial Dungeon
wear `AbBoundArmorMaceNoHelmetMD`, an Ability.

So the routing rule keys on **both** the item type and the delivery:

| | Castable spell (Type 0) | Ability / Lesser Power |
|---|---|---|
| **Bound armor** | scripted | scripted |
| **Bound weapon** | native archetype 17 | scripted |

`magic.bound_script_variant()` clones the bound MGEF into an archetype-`1`
(Script) stand-in whose VMAD carries `TES4_BoundItemEffect`, with the item as a
`Form` property (`Assoc. Item` is "Unused" under archetype 1, so it is zeroed).
Clones are cached per source MGEF and allocated inside the serial, sorted record
pass, so the output stays byte-reproducible. The source DATA is registered in
Phase 0 by `register_mgef_formids()`: Phase 1 converts types **alphabetically**,
so SPEL runs *before* MGEF and would otherwise find nothing to clone.

**Teardown is the hard half.** Bound gear is not real equipment: it must vanish
when the effect drops, including on death, so a corpse is never lootable for it.
`OnEffectFinish` alone does **not** achieve that — an Ability is permanent and
never finishes, so on death the engine simply stops processing the actor. The
script therefore reclaims from `OnDying` (which fires while the actor is still
alive, before the body can be searched), `OnDeath`, and `OnEffectFinish`, all
routed through one idempotent function that clears its holder reference first.

Two traps worth keeping written down:

- **`UnequipItem`'s second argument is `abPreventEquip`.** Passing `true` tells
  the engine *not* to re-equip the freed slot, which left the assassins stripped
  after the armor vanished. Every `UnequipItem` call in vanilla Skyrim's own
  scripts uses the defaults.
- **A dying or dead actor never re-evaluates its equipment**, so the engine's
  own re-equip cannot be relied on. The script samples every biped slot (30–61)
  with `GetEquippedArmorInSlot`, plus both hands with `GetEquippedWeapon`,
  *before* equipping, and restores those by name. A bound cuirass covers the
  same slots as whatever was underneath — the Mythic Dawn robe occupies exactly
  the 32/33/37/44 the bound armor takes — so nothing comes back on its own.
  Sweeping the full range matters: the Mythic Dawn *helmet* claims 30/31/41/42/43,
  which a hand-picked head/body/hands/feet set would have missed.

### SPEL needs `ETYP` — DONE 2026-08-07 (user-confirmed in-game)

`ETYP` (Equip Type) names the slot the magic menu files a spell under. **A spell
without one never appears in the menu at all** — it can be added by console but
is invisible and uncastable. All 1137 converted spells were missing it; the
symptom surfaced on the Bound Dagger/Mace spells.

Census of `references/Skyrim.esm`: **827/827 spells carry `ETYP`**, with no
exceptions in any spell type — the strongest possible evidence it is mandatory.
Oblivion has no equivalent field, so it is derived from the spell type using
vanilla's own majority choice per type:

| TES5 type | Vanilla majority | Emitted |
|---|---|---|
| 0 Spell | EitherHand (292/407) | `EitherHand` `0x00013F44` |
| 1 Disease | EitherHand (13/13) | `EitherHand` |
| 2 Power | Voice (24/28) | `Voice` `0x00025BEE` |
| 3 Lesser Power | Voice (2/3) | `Voice` |
| 4 Ability | EitherHand (242/250) | `EitherHand` |

**Diseases and abilities still do not show in the menu, and `ETYP` is not what
keeps them out** — the *spell type* is, which the engine handles. All 13 vanilla
diseases carry `ETYP=EitherHand`, so omitting it for them would be the deviation.
The field that does track "not menu-facing" is `MDOB` (the menu icon): diseases
are the only type with zero (0/13). Every other spell type now gets one
([Menu display object](#menu-display-object)).

The same trap was already known for `SCRL`, where the converter has always
written `ETYP` — `convert_SPEL` was simply never given it.

<a id="phase-5-cleanup"></a>
### Phase 5 — Cleanup — DONE

- **`tes5_import/actors/magic_effects.py` is deleted.**
  - `aimed_variant`, `has_projectile` and `set_tes4_effect_names` are gone,
    and so is the projectile registry in `magic.py` that fed them.
  - Built Oblivion.esm had **0** aimed clones left before the removal. Every
    aimed slot already reached a projectile through `delivery_variant`.
- **Vanilla effects clone like ours.** `magic_variants._parts_of` falls back
  to `vanilla_mgef_data.py` for a vanilla FormID. This covers the filler
  effects and the `MGEF_CODE_TO_SKYRIM` fallback, so the same
  `delivery_variant` fits them to their owner.
- **Fillers take the owner's pair.** The filler for a dropped effect is now
  cloned onto the owner's casting type and delivery. Before, an Aimed spell
  whose only effect dropped carried a Self filler, the null-projectile crash
  above. The fillers are still needed: Oblivion.esm has 6 spells whose only
  codes (`RSWD`, `DUMY`) no Oblivion MGEF defines, and 36 ingredients padded
  to 4 effects.
- **`filler_dur` is deleted.** It kept a dropped effect's duration on the
  filler only so a dropped `SEFF` still answered `HasMagicEffectByID`, and
  SEFF is no longer dropped.
- **`bound_item_assoc` checks the archetype.** It returned the Assoc. Item of
  any effect, so a summon on an ability would have been scripted as a bound
  item. It now answers only for archetype 17.

## <a id="owner-casting-type"></a>Casting type and delivery follow the owner

**Code:** `tes5_import/record_types/magic_variants.py` (`delivery_variant`), `record_types/equipment.py` (`_slot_mgef`)

Skyrim keeps the casting type (Constant, Fire and Forget, Concentration) and
the delivery (Self, Contact, Aimed, ...) on the MGEF. The engine applies an
effect the way *the effect* says, whatever the spell holding it says.
Oblivion has neither field: the item decides.

**The rule, from a census of every vanilla effect slot:** casting type and
delivery match the owner on **2,138 of 2,146** slots.

| Owner | Effect casting type / delivery |
|---|---|
| Ability | Constant / Self |
| Disease | Constant / Contact |
| Apparel enchantment | Constant / Self |
| Potion, poison, ingredient | Fire and Forget / Self (803 of 803) |
| Scroll (owner cast type 3) | Fire and Forget, the owner's delivery |
| Weapon enchantment | Fire and Forget / Contact |
| Spell, power, staff | the owner's casting type and delivery |

**This was the "effects die after a few milliseconds" bug.** On the build
before the fix, 3,097 of about 4,300 effect slots did not match their owner.
Every ability carried Fire-and-Forget effects, so the engine applied them
once and they expired at once. Script-called spells died the same way.

Each slot is re-pointed at a clone of its MGEF carrying the owner's pair.
`delivery_variant` returns the effect itself when it already matches.
Otherwise it writes one clone per (source, casting type, delivery, burst),
hashed at site `MGEF_DELIVERY`, so existing FormIDs are untouched. A spell
whose effects have different ranges gives each effect its own TES4 range; the
spell takes Aimed if any effect is, else Target Actor, else Self
(`owner_delivery`).

**Touch is Target Actor, not Contact.** The CK wiki's Magic Effect page:
Contact "only works for Weapons". None of the 70 vanilla Contact spells is
hand-cast by the player, so a converted Touch spell appeared in the menu but
never cast. Skyrim's hand-cast touch spells (Heal Other, Soul Trap, Fade
Other, the Daedra commands: 19 spells) are Target Actor with a 0 range. A
weapon enchantment is fixed to Contact, the one owner that fires it. Changing
this renumbered every Touch copy (the delivery is in the `MGEF_DELIVERY` key).
The base MGEF follows (`_delivery_and_cast`).

### <a id="creature-attack-spells"></a>A creature's touch spell rides its melee attacks, as Contact

**Code:** `tes5_import/actors/creature_races.py` (`creature_touch_attack_spell`), `record_types/equipment.py` (`attack_spell`)

Vanilla's melee casters name a spell as each attack's ATKD Attack Spell: the
flame atronach's four ordinary `attackStart_*` entries name
`crAtronachFlameMeleeAttack` (109 attack entries in Skyrim.esm carry one), and
that spell is Contact (`SPIT` delivery 1). The TES4 analogue is a creature's
castable Touch spell with no Target effect. The same spell can also be a
player's (`StandardParalyze3Journeyman`, Morrowind's `frostbite`), which must
be Target Actor. So the attack names a Contact copy, `<EditorID>Attack`,
hashed at site `SPEL_ATTACK` on the spell's EditorID. Its effect copies carry
the Contact key every Touch copy had before, so they keep their FormIDs.
Measured on the built plugins: 9 such spells in Morrowind_ob, 24 in
Tamriel_Data, 17 in TR_Mainland, 6 in Oblivion.esm.

Measured on the rebuilt Oblivion.esm:

- 600 of 601 ability slots are Constant/Self.
- All 1,782 worn-enchantment slots match.
- Every spell slot matches.
- What still differs is intended: the scroll ENCH records, and the Self half
  of mixed-range spells.

**Dual casting needs nothing extra.** 944 of 950 vanilla effects carry no Dual
Cast Art. Dual Cast Scale 1.0 and the either-hand `ETYP` were already written.

## <a id="effect-families"></a>Effect families: HasMagicEffect tests a keyword

**Code:** `tes5_import/record_types/magic.py` (`settle_family_keywords`), `record_types/magic_variants.py` (`_emit`), `tes5_import/base/conditions.py` (`_effect_family`), `script_convert/commands.py` (`has_magic_effect`, `is_spell_target`)

One source effect becomes several MGEFs: the base, a copy per owner casting
type and delivery, per actor value, per script, and scripted bound items.
A spell carries a copy, but `HasMagicEffect` compares one exact MGEF. So a
script asking for the base effect never saw the copy on the actor.

The failure that exposed it: New Vegas's `GenericScript` ends with
`If Player.HasMagicEffect Concussion == 0` → `CastImmediateOnSelf
PlayerConcussed`. The spell is Fire and Forget / Self, so its effect is the
copy `TES4ConcussionFFSelf`, not `Concussion`. The test never passed, and the
converted poll re-cast the spell every 0.5 s. The player piled up dozens of
"Concussion" and "Reduced Perception" entries, each 315,360,000 s (87,600 h).

**The fix:** every MGEF a plugin can reference gets one keyword,
`TES4FX_<effect EditorID, lowercase>`. The base and every copy carry it in
`KWDA`.
- The keyword is written in the magic pre-scan, before any record converts. It
  is hashed at site `KYWD_MGEF_FAMILY` on the effect's EditorID, so no existing
  FormID moves.
- A master's effect adopts the master's keyword by EditorID. A dependent
  plugin writes keywords only for its own effects that the masters lack.
- Scripts: `HasMagicEffect X`, where X is an MGEF, becomes
  `HasMagicEffectWithKeyword(TES4FX_x)`. The property binds through
  `WELL_KNOWN_PROPERTIES`. Any other argument keeps the plain call.
- `IsSpellTarget S` tests the family of S's first effect that has an MGEF
  record. It used to test the vanilla Skyrim alias of that effect, which no
  converted spell carries since MGEF became a converted record, so it always
  read false.
- Conditions: `HasMagicEffect` (214) on an effect with a family becomes
  `HasMagicEffectKeyword` (699) with the keyword.

Not yet confirmed in-game.

## <a id="magic-art"></a>Magic art

**Code:** `tes5_import/record_types/magic_art.py`; meshes in [asset_convert_magic_art.md](asset_convert_magic_art.md#phase-meshes)

Each TES4 MGEF's `Model.MODL` is split per phase on the asset side. The import
writes one companion record per phase the **source** NIF authors, keyed on the
authored model path, so effects sharing a mesh share the record:

| MGEF DATA field | Companion | Mesh | Values with no Oblivion source |
|---|---|---|---|
| Casting Art (92) | ARTO, DNAM 0 | `_cast` | - |
| Hit Effect Art (96) | ARTO, DNAM 1 | `_hit`, else `_summon` | - |
| Projectile (72) | PROJ, Missile | `_projectile` | FireboltProjectile01's fade 0.5, impact force 1.5, collision radius 10, relaunch 0.25, VNAM 1 |
| Explosion (76) | EXPL | `_area` | FireBallExp01's radius 320, image-space radius 1024, flags 0x41, sound level 1; force 0 |

A few PROJ and EXPL fields do have Oblivion sources:

- **Projectile speed** is 1000 × `DATA.ProjectileSpeed`. 1000 is Oblivion.exe's
  `fMagicProjectileBaseSpeed` default, and no plugin overrides it.
- **Projectile range** is 10000, Oblivion.exe's `fMagicProjectileMaxDistance`.
- **Projectile light** is `DATA.Light`, and its sound is the `BoltSound` SNDR.
- **Explosion sound** is the `AreaSound` SNDR.

An explosion is written only for an effect slot with a TES4 area on an aimed
delivery. That area burst belongs to that use of the effect alone, so it is
part of the `delivery_variant` key.

**Sounds** go in `SNDD`: the casting sound as Release (3) and the hit sound as
On Hit (5). An SNDR exists only for a SOUN carrying `FNAM.Filename`; its FormID
is `derive_formid('SNDR', soun)`.

Measured on Oblivion.esm, out of 703 emitted effects:

| Field | Effects |
|---|---|
| casting art | 581 |
| hit art | 564 |
| an explosion | 44 |
| their own projectile | 182 |
| an SNDD | 702 |

## <a id="master-effects"></a>A master's effect keeps its master's art

**Code:** `tes5_import/record_types/magic.py` (`_fill_art`), `magic_art.py` (`master_effect_data`, `_master_sound_set`)

A dependent plugin rebuilds its copies of a master's effects (per delivery,
per actor value) from the master's EXPORT record. Two parts of that record
cannot be resolved from the child:

- **Art.** `magic_art` finds an effect's phases by reading its source NIF
  from the CURRENT plugin's mesh tree; the master's effect meshes are in the
  master's. Measured on Morrowind_ob.esm: **all 535** of its copies of
  Oblivion.esm's effects had no casting art, no hit art and no sound set, so
  a Morroblivion spell that needed a copy showed nothing in the hand. One that
  could use Oblivion's effect directly (Frost Bolt → `FRDG`) showed Oblivion's
  art.
- **FormIDs.** Its SOUNs are numbered in the master's own master list.

The same goes for the effect's own FormID, so `register_mgef_formids` finds a
master's effect by EditorID in the master's converted plugin
(`find_by_edid`). Taking the export's FormID was correct only where the
master's list happens to line up with the child's (Morrowind_ob over
Oblivion.esm). Elsewhere it named a record that does not exist. Tamriel_Data's
patch effects became Tamriel_Data's own index: 295 of 1,000 effect slots,
238 spells. TR_Mainland's became Tamriel_Data's: 180 spells. Skyrim drops such
a spell from the magic menu. Every slot whose delivery matched the master's
effect pointed at the bad id, and the copies were keyed on it. The fix
renumbered those copies.

So a copy of a master's effect takes Assoc. Item, Casting Art, Hit Effect Art
and Projectile from the master's converted MGEF (`find_by_edid`, then
`record`, which restates `DATA` 8/72/92/96 and `SNDD` in the child's ids), and
its SNDD likewise. These REPLACE the rebuilt values rather than filling gaps: a
rebuilt Assoc. Item is numbered in the master's own list, so in TR_Mainland it
can name the wrong file's record. Routing a bound item to the script stand-in
types the item the same way (`master_signature`), since `known_sigs` keys a
master's records by the master's export ids. None of those fields is part of a
variant key, but routing is: bound armor, and any bound effect on a never-cast
spell, now becomes a `...Scripted` copy. Measured: 2 Tamriel_Data and 5
TR_Mainland `...BoundXConstantSelf` copies, all with no item before, were
replaced by scripted ones.
The effect's area burst (Explosion) still comes only from the plugin's own
meshes, because adding it would change the `MGEF_DELIVERY` key.

## <a id="effect-shader-particles"></a>Effect shader particle counts

**Code:** `tes5_import/record_types/world.py` (`convert_EFSH`)

EFSH DATA offsets 124 and 128 are ratios (0 to 1) in TES4. TES5 reads them as
**counts**: Full Particle Birth Ratio and Persistent Particle Count. Vanilla
writes 40 to 150 for these.

Copied as-is, a ratio of 1.0 spawned about one particle, so hit shaders barely
showed. Bethesda's own port of `LifeDetected` maps 1.0 to 300, and that factor
is `EFSH_PARTICLES_PER_RATIO`. After it, all 102 Oblivion EFSH records carry
counts of 100 or more.

## <a id="impact-data"></a>Impact data

**Code:** `tes5_import/record_types/magic.py` (`fit_delivery`, `IMPACT_BY_PROJECTILE`)

Oblivion has no impact data sets, so the decals and impact sounds a bolt
leaves on the world are borrowed from vanilla. Vanilla puts an impact set on
140 of 199 Aimed and 34 of 36 Target Location effects. It follows the
projectile, and each vanilla bolt's MGEFs almost always share one set:

| Vanilla projectile | Impact set | MGEFs flying it that carry the set |
|---|---|---|
| FireboltProjectile01 | MAGFirebolt01ImpactSet | 12 of 20 |
| FlamesProjectile | MAGFlames01ImpactSet | 6 of 7 |
| FrostIcicleProjectile01 | MAGFrostBolt01ImpactSet | 5 of 6 |
| FrostSprayProjectile01 | MAGFrost01ImpactSet | 4 of 4 |
| ShockBoltAim | MAGShock02ImpactSet | 5 of 5 |
| ShockBoltConAim | MAGShock01ImpactSet | 11 of 12 |
| AbsorbBeam01 | MAGAbsorbRedImpactSet | 8 of 8 |
| SpiderSpitProjectile | MAGSpiderSpitImpactSet | 6 of 6 |
| Illusion01Projectile | MAGIllusionImpactSet | 13 of 14 |
| IllusionNeg01Projectile | MAGIllusionNegImpactSet | 10 of 14 |
| ReanimateProjectile | MAGReanimatelImpactSet | 10 of 11 |
| TurnUndeadProjectile | MAGTurnUnlImpactSet | 10 of 10 |
| HealFakeProjectile, ParalyzeProjectile | none (the majority) | - |

`fit_delivery` gives every aimed MGEF the impact set of the vanilla bolt its
element and school call for. It does this even when the effect flies its own
Oblivion projectile, so a converted fireball leaves the Firebolt scorch.
Non-aimed deliveries get none, matching the projectile rule.

## <a id="menu-display-object"></a>Menu display object

**Code:** `tes5_import/record_types/magic.py` (`menu_display_object`), `magic_variants.menu_object`, `equipment.convert_SPEL`

`MDOB` names the STAT the magic menu shows for a spell or an effect.
Census of Skyrim.esm:

- **SPEL:** every type carries MDOB except Disease (0 of 13). That is 342 of
  407 spells, 28 of 28 powers, 3 of 3 lesser powers, 122 of 250 abilities and
  112 of 115 shouts. All 618 targets are STATs.
- **MGEF:** 835 of 950 carry one. `MagicHatMarker` (0x000435A5) is the most
  common: 646 MGEFs and 275 spells.
- **The specific art lives on the spell.** 292 of 618 spells show different
  art from their first effect.

**Menu art is vanilla Skyrim's, picked the way vanilla picks it.** Our own
Cast-phase meshes were invisible and then flickered in the menu
([asset_convert_magic_art.md](asset_convert_magic_art.md#menu-art)).

The rule comes from vanilla spells of types 0, 2 and 3, grouped by their first
effect. The first rule that matches wins:

1. **By archetype.** The vanilla majority for that archetype:

   | Archetype | Menu STAT |
   |---|---|
   | Summon | MAGINVSummon (16 of 17) |
   | Reanimate | MAGINVReanimate (18 of 19) |
   | Turn Undead | MAGINVTurnUndead (10 of 11) |
   | Invisibility | MAGINVInvisibility (6 of 6) |
   | Paralysis | MAGInvParalyze (6 of 7) |
   | Bound Weapon | MAGINVBoundWeapon (5 of 5) |
   | Detect Life | MAGInvDetectLife |
   | Light | MAGINVLightSpellArt |
   | Telekinesis | MAGINVTelekinesis |
   | Calm, Rally | MAGINVIllusionLight01 |
   | Demoralize, Frenzy | MAGINVIllusionDarkt01 |
   | Command Summoned, Soul Trap | MAGINVBanish |

2. **By element (resist actor value).**
   - Fire: FireballInvArt (52 of 59 Destruction fire spells).
   - Frost: IceSpellArt.
   - Shock: ShockSpellArt.
   - Magic: MAGINVAbsorb.
3. **By school.** Alteration (and Mysticism, which folds into it): MAGINVAlteration
   (10 of 11). Restoration: HealSpellArt (15 of 19).
4. **Otherwise** `MagicHatMarker`.

A spell takes the art its first **written** effect calls for: the clone the
slot resolved to, filler included. Every type gets one but Disease. Each
converted MGEF (base, per-AV and script variants) carries the same rule's
answer.

No STAT is written, so no FormID is derived for menu art.

## Rules for working in this area
<a id="rules-working-this-area"></a>

- **Validate every effect code against `export/*/MGEF.txt`.** 17 of the old
  alias table's 100 entries were codes no Oblivion or Nehrim record uses. A
  plausible-looking 4-char code is not evidence. A regression test now enforces
  this in both directions (nothing missing, nothing phantom).
- **A vanilla census cannot license an archetype.** Skyrim.esm uses only 39 of
  the 47 archetypes; the ones it skips (Dispel, Lock, Open, Turn Undead) are
  still fully implemented engine classes. Check the exe's RTTI before assuming
  an unused enum value is dead.
- **…but an xEdit type signature cannot license one either — it says what a
  field ACCEPTS, not what the engine IMPLEMENTS.** `Assoc. Item` is typed
  `[WEAP, ARMO, NULL]` under archetype 17, and bound *armor* still does nothing
  in game: all 7 vanilla uses name a WEAP, none an ARMO. When the two disagree,
  a unanimous vanilla census is the stronger evidence — 0/7 is a finding, not a
  coincidence. (Cost three round trips; see [Bound items](#bound-items).)
- **A field every vanilla record carries is mandatory until proven otherwise.**
  827/827 spells have `ETYP`, and the spells we shipped without it were
  invisible in the magic menu. "n/n with no exceptions" is the strongest signal
  this codebase gets — treat a 100% census as a requirement, not a convention.
- **`Assoc. Item` is typed by archetype and the target must be re-checked, not
  copied.** A CREA converts to an NPC_ (fine for Summon Creature) but an LVLC
  converts to an LVLN, which that archetype rejects.
- **A dependent plugin usually defines NO magic effects.** Every index in this
  area (effects, AssocItem targets, enchantments) must merge
  `ctx.master_export`, exactly like the outfit wardrobe index.
- **Skyrim has vanilla Papyrus GMST readers but NO writer.** `Game.GetGameSetting*`
  compiles; `Game.SetGameSetting*` is SKSE-only and this pipeline builds against
  vanilla headers. Route a runtime setting write through the actor value it
  changes, and route the matching READ through the same channel.
- **TES5 EFIT is Magnitude, Area, Duration** — in that order. All 427 vanilla
  ALCH effects put the potion duration at offset 8.
- **A hand-built TES5 MGEF DATA is 152 bytes, FormVersion 44.** Verified
  against both `wbDefinitionsTES5.pas:8195` and the Skyrim.esm dump.
- **`Counter Effect Count` (offset 20) must equal the number of `ESCE`
  subrecords** or the CK reads garbage counter slots.
- **`AssocItem` is archetype-dependent** (`wbMGEFAssocItemDecider`): it is a
  LIGH/WEAP+ARMO/NPC\_/HAZD/SPEL/RACE/ENCH/KYWD depending on the archetype.
  Writing a creature FormID under a value-modifier archetype is meaningless.
- Never introduce a null `EFID` — it crashes the inventory menu as soon as the
  item card is shown. That constraint is why filler effects exist and it
  survives this plan.
- Re-run `python tools/audit/magic_audit.py export/Oblivion.esm` **and**
  `export/Nehrim.esm` after each change; Nehrim exercises 16 mod-authored
  effect codes (`BA01`–`BA10`, `BW09`, `BW10`, `DISE`, `DUMY`, `RSWD`, `Z020`)
  that Oblivion does not, and its strings are German (the tool forces UTF-8
  output for this reason).

## <a id="enchantment-charge-eamt"></a>Enchantment charge (ANAM to EAMT)

An enchanted weapon carries two things: the ENCH it invokes, and the size of
the charge pool it spends per cast. Both games store the pair as one struct —
xEdit builds it from a single shared `wbEnchantment(aCapacity)` in
`wbDefinitionsCommon.pas:8046`, whose members are
`IsTES4(ENAM, EITM)` for the effect and `IsTES4(ANAM, EAMT)` for the u16
capacity. TES4's `ANAM` and TES5's `EAMT` are therefore the same field under
two names, and the value converts with no transformation.

`convert_WEAP` wrote `EITM` and dropped `ANAM`, so every converted enchanted
weapon reached Skyrim with a zero charge pool. Staves were the visible casualty
because a staff's only function is to cast: with no charge it reads as fully
depleted and does nothing when used. Enchanted swords degraded more quietly —
they still swing, they just never fire their effect.

Measured evidence:

- Real `Skyrim.esm` (binary, not a dump): 2484 WEAP records, **2273 `EAMT`
  subrecords, every one exactly 2 bytes**. Sample values 1500/2000/3000.
- Vanilla pairing: 2293 WEAP carry `EITM`, **2272 of those carry `EAMT`**
  (99.1%). An enchanted weapon without one is the rare exception.
- TES4 source authors it universally: **221/221 Oblivion staves and 242/242
  Nehrim staves** with an `ENAM` also carry `ANAM`, as do 720/721 and
  559/560 enchanted weapons of all types respectively.

ARMO deliberately does **not** get this field: 0 of 2167 enchanted vanilla
`ARMO` records carry `EAMT`, because apparel enchantments in Skyrim are
permanent while worn rather than charge-consuming. The TES4 `ANAM` on an
armor record has no TES5 counterpart and is correctly discarded.

The u16 clamp is real, not defensive: the field is 2 bytes on disk, so an
authored `ANAM` above 65535 must saturate rather than wrap to a near-zero
pool.

## <a id="power-affects"></a>Power Affects Magnitude / Duration

**Code:** `tes5_import/record_types/magic.py` (`_power_affects`)

TES5 MGEF flags `0x200000` and `0x400000` decide what skill, perks, alchemy
effectiveness and dual-casting scale. Neither TES3 nor TES4 has them, and the
import set neither, so a potion brewed from converted ingredients ignored the
player's Alchemy and every perk: `AlchemyMenu::ModEffectivenessFunctor`
(`0x906e90` on 1.6.1170) applies entry point 66 and the skill factor only
behind those two bits.

Census over the 950 MGEFs of Skyrim.esm: an effect WITH magnitude carries
Power Affects Magnitude (438 + 151) far more than Duration (100 + 14, mostly
summon, bound, invisibility and paralysis archetypes, which the import already
marks No Magnitude); an effect with NO magnitude carries Power Affects
Duration (87 of 94). Both at once is 4 records. So the rule is magnitude when
the effect has one, else duration when it has one. The rest are authored
one-offs -- perk abilities, diseases, fixed-strength armor spells -- that a
converted effect has no field to express.

## <a id="morrowind-effects"></a>Morrowind effects key on an index, not a code

**Code:** `tes5_import/record_types/magic_morrowind.py`

Morrowind has no four-character effect codes: its 143 effects are an
engine-fixed table addressed by index, so `EFFECT_ARCHETYPES` cannot describe
them and must not be extended to try. `MW_EFFECT_ARCHETYPES` is the parallel
table, keyed by index, and nothing in it is reachable from the Oblivion path --
`convert_MGEF` consults it only for a record carrying `MorrowindEffectIndex`.

The export writes each effect's EditorID (`MW014FireDamage`) as the `EFID`,
and `register_mgef_formids` already indexes `_code_to_fid` by EditorID, so
`_resolve_mgef` resolves Morrowind effects with **no change at all**.

### 114 of 143 are native; 19 await the runtime

Most Morrowind effects are ordinary Skyrim ones -- fire damage is fire damage.
The mapping was checked against `wbActorValueEnum` and against real vanilla
records, which turned up actor values the Oblivion tables never needed and so
never defined:

| Effect | Actor value | Vanilla record proving it |
|---|---|---|
| Blind | 84 Blindness | `AbBlind` |
| Sound | 92 Movement Noise Mult | `AbVampireMuffle` |
| SpellAbsorption | 83 Absorb Chance | `RaceBretonAbsorbSpellChance` |
| Reflect | 163 Reflect Damage | `PerkReflectBlows` |
| Jump | 62 Jumping Bonus | `wbActorValueEnum` |

Vampirism (index 133) is the instructive case, and it cuts both ways. Skyrim
ships an entire vampire system, so reimplementing one would be waste -- but
`VampireChangeEffect` is **archetype 1 (Script)** with a null Assoc. Item: the
transformation lives in attached Papyrus, not in the archetype, so copying the
archetype alone would convert to a no-op. What Skyrim *does* model natively is
the drain, as `DisDamageHealthVampire` (archetype 34 on Health), and that is
what Vampirism and Corprus map to. Attaching the full vampire quest chain is
runtime work, not record work.

The 19 with no Skyrim mechanism carry `NATIVE_NONE`. They convert today as an
inert Value Modifier -- present, addressable by a script, doing nothing -- which
is where the MorrowindRuntime effect table attaches. Only Levitate and SlowFall
genuinely need new engine addresses; the rest are state the DLL can hold.

<a id="morrowind-borrowed-art"></a>
### Art is borrowed from vanilla Skyrim

**Code:** `tes5_import/record_types/magic_art_morrowind.py`, `magic.py` (`_borrow_art`), `tes4_export/record_types/morrowind_magic.py` (`_emit_sounds`)

A TES3 effect names its visuals by the ID of a shared VFX record (`CVFX`
cast, `BVFX` bolt, `HVFX` hit, `AVFX` area) and tints them per effect with a
particle texture (`PTEX`). OpenMW swaps that texture in wherever the mesh
uses its first root `NiTexturingProperty` (`nifloader.cpp`, `mwrender/util.cpp`).
In Morrowind.esm's 137 effects:

| Job | Distinct VFX records | Effects using one |
|---|---|---|
| Cast | 15 | 134 |
| Bolt | 9 | 113 |
| Hit | 17 | 105 |
| Area | 11 | 116 |
| Particle texture | 35 | 137 |

Morrowind splits its art by school the way Skyrim does, so each cast and hit
VFX ID maps to the vanilla effect that does the same job. The converted
effect copies that effect's Casting Art and Casting Light (cast) or Hit
Effect Art and Hit Shader (hit); vanilla fire, frost and shock burn their
targets through the hit shader, not hit art. Fire, Shock and Frost Damage
(engine indices 14 to 16) take their element's own vanilla effect instead:
TES3 draws them through the generic destruction meshes, told apart only by
the texture. The donors' DATA is baked into `vanilla_mgef_data.py`.

- **Bolts** already come from the element/school projectile rule, which
  splits the same way Morrowind's 9 bolt records do.
- **Area explosions are not written yet.** An explosion is part of the
  `MGEF_DELIVERY` variant key, so adding one renumbers existing variant
  FormIDs ([FormID drift](../../CLAUDE.md#formid-drift)).
- **What does not map:** Mysticism has no Skyrim school (Soul Trap's art
  stands in); Levitate borrows Waterbreathing, Poison the Absorb Stamina hand
  and the generic damage hit; the 35 per-effect tints are lost.

**Sounds.** Only 14 of the 137 effects author `CSND`/`BSND`/`HSND`/`ASND`.
An unset one plays the school's default, the SOUN `"<school> cast"` (and
`bolt`, `hit`, `area`) (OpenMW `mwworld/store.cpp`, `spellcasting.cpp`), so
the export writes that. In Morroblivion mode the effects live in the
compatibility patch and their SOUNs in Morrowind_ob.esm, so `magic_art._sndr`
resolves a master's SOUN through `master_sound_descriptor`, the same way the
ACTI/CONT/DOOR/LIGH slots do.

**A dependent plugin inherits a master effect's sounds from the master's
conversion.** TR_Mainland and Tamriel_Data define no MGEF; their per-delivery
copies are rebuilt from the patch's EXPORT record, whose SOUN FormIDs are
numbered in the patch's own master list (`Oblivion, Morrowind_ob`) and mean
nothing in the child's. `load_master_export` re-keys only each record's own id,
not the ids inside it. So `sound_set` takes a master effect's SNDD from the
master's converted MGEF (`find_by_edid`, then `record`, which restates it in
the child's ids; `SNDD` is in `_FORMID_FIELDS_BY_SIG`). Before this, every copy
shipped with no sound set at all: 0 of TR_Mainland's 404 MGEFs carried an SNDD.
