# tes5_import/record_types/magic.py - magic conversion

**Code:** `tes5_import/record_types/equipment.py`, `tes5_import/record_types/magic.py`, `tes5_import/generated/vanilla_mgef_data.py`, `asset_convert/character/skyrim_overrides.py`

## Contents

- [The problem Phase 1 solved](#problem-phase-1)
- [The four defects, in priority order](#four-defects-priority-order)
- [Path to complete conversion](#path-complete-conversion)
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

### Phase 2 — Wire the art back in

1. **`ARTO` writer** — one art object per distinct MGEF `Model.MODL`; point
   `Casting Art` (92) / `Hit Effect Art` (96) at it. The meshes are already
   converted in `output/.../meshes/tes4/magiceffects/`.
2. **Sounds** — emit `SNDD` from the four TES4 sound FormIDs (casting/bolt/hit/
   area) via the existing SOUN→SNDR conversion.
3. **`ESCE`** — counter effects are MGEF→MGEF references; trivially convertible
   once MGEF records exist (41 effects carry them). The count field at offset 20
   must match.
4. Complete the `EFSH` DATA beyond offset 44.

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

Still open in this phase: a **`PROJ` writer** so effects fly their own TES4
`DATA.ProjectileSpeed` art rather than borrowing a vanilla bolt, and `EXPL` for
area effects (TES4 `Area` is packed into EFIT but no explosion is ever created).
`aimed_variant` still covers effects that resolve to a *vanilla* projectile-less
MGEF, so it cannot be retired yet.

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
are the only type with zero (0/13). We emit no `MDOB` anywhere, which is
consistent with diseases and a cosmetic gap elsewhere, not a functional one.

The same trap was already known for `SCRL`, where the converter has always
written `ETYP` — `convert_SPEL` was simply never given it.

### Phase 5 — Cleanup

No record loses all its effects any more (audit: 0 for both plugins), so the
`_FILLER_EFFECTS` machinery in `record_types/equipment.py` is now dead weight
for its original purpose. It is **deliberately left in place**: it is still the
only thing standing between a null `EFID` and an inventory-menu crash if a
future plugin uses an effect code no export has seen, and INGR still needs
`pad_to=4` regardless. Delete the `filler_dur` duration-faking path — that one
existed only so a dropped `SEFF` still answered `HasMagicEffectByID`, and SEFF
is no longer dropped.

Also still open: `magic_effects.aimed_variant()` should retire once Phase 3
gives every aimed item's own effects a projectile (its clones are now the only
consumer of `vanilla_mgef_data.py`).

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
