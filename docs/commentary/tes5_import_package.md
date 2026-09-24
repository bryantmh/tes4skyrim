# tes5_import/packages/converter.py - AI packages

**Code:** `tes5_import/packages/converter.py`, `tes5_import/packages/actor_wiring.py`, `tes5_import/dialogue/converter.py`, `tes5_import/base/conditions.py`

## Contents

- [PACK Conversion Plan (TES4 → TES5)](#pack-conversion-plan)
- [1. Ground truth (verified, not assumed)](#1-ground-truth)
- [2. What we have on the TES4 side](#2-what-we-have-tes4)
- [3. Design](#3-design)
- [4. Implementation order](#4-implementation-order)
- [5. Verification](#5-verification)
- [6. Risks](#6-risks)
- [7. GetVMScriptVariable package gates need the script on the PLACED ref (2026-07-20)](#7-getvmscriptvariable-package-gates-need)
- [8. PLDT alias locations must be type 8, not type 9 (2026-07-20)](#8-pldt-alias-locations-must)
- [9. QUST.DNAM.Priority must stay in the engine's 0-100 band (2026-07-20)](#9-qustdnampriority-must-stay-engines)
- [10. What the engine actually does with a PACK (disassembly, 2026-07-20)](#10-what-engine-actually-does)
- [PACK conversion: the 2026-08-17 fix pass](#pack-conversion)
- [Status after the 2026-08-17 fix pass](#status-after)
- [PACK conversion: verified-correct behaviour](#pack-conversion-2)
- [Verified correct — do NOT "fix" these](#section)
- [Shop doors: Unlock Doors At Location becomes Unlock At Start](#shop-doors-unlock-at-location)

## PACK Conversion Plan (TES4 → TES5)
<a id="pack-conversion-plan"></a>

Goal: **Oblivion-equivalent AI behavior expressed in Skyrim syntax.** Not
"records that load" — actors must keep their schedules, walk their routes,
follow, escort, flee, and ambush the way they did in Oblivion.

> **Status: IMPLEMENTED (updated 2026-07-26).** This document is the design plan
> that the implementation followed; the ground-truth sections below are still the
> reference for how Skyrim's package model works. The status text that used to
> head this file described the pre-implementation state and is preserved at the
> bottom under "Historical: pre-implementation status" so its dated claims are not
> mistaken for current behavior.
>
> Current reality:
> - `PACK` is **NOT** in `SKIP_TYPES` — it is converted.
> - `convert_PACK` lives in [tes5_import/packages/converter.py](../../tes5_import/packages/converter.py)
>   (with templates in [pack_templates.py](../../tes5_import/packages/templates.py)), not
>   in `record_types/dialog_misc.py`, and is live code.
> - PACK is written in its **own phase (import_main Phase 3b2), after QUST**,
>   because quest packages need the aliases to exist first — it is deliberately
>   not in the generic dispatch table.
> - For the verified engine contracts that came out of this work (PTDA distance,
>   Ambush→approach, force-greet topic binding, template data inputs), see
>   [package_ai_contracts.md](../reference/package_ai_contracts.md).

---

## 1. Ground truth (verified, not assumed)
<a id="1-ground-truth"></a>

Sources: `references/xEdit/Core/wbDefinitionsTES5.pas` (PACK at line 11182) and a
census of all 5,961 `PACK` records in `references/Skyrim.esm/PACK.txt`.

### 1.1 The template model — this is the whole architecture

Skyrim packages come in two kinds, and the census settles which one we build:

| Kind | `PKDT.Type` | Has Procedure Tree (`PRCB`) | Count in Skyrim.esm |
|---|---|---|---|
| Template **root** | **19** (`0x13`) | yes | 104 |
| Package **instance** | **18** (`0x12`) | no | 5,758 |

**96.6% of vanilla packages are instances that carry no procedure tree at all.**
They point `PKCU.PackageTemplate` at a root and supply *data inputs*. The root
owns the behavior; the instance owns the customization (destination, target,
radius, schedule, conditions, owner quest).

> `PKDT.Type` is **not** the behavior selector. It only says "I am a template" (19)
> or "I am a package" (18). Behavior lives in the template you point at. Any plan
> that tries to map TES4 `PKDT.Type` onto `PKDT.Type` is building the wrong thing.

**Therefore: we never author procedure trees.** We emit Type-18 instances that
point at stock Skyrim.esm template roots (master index 0, no remapping needed) and
fill in their data inputs. This is exactly what the CK does.

### 1.2 The data-input contract

A template root declares its public inputs as an ordered `UNAM`/`BNAM`/`PNAM`
list. An instance must supply values in a parallel `ANAM`(+`CNAM`/`PLDT`/`PTDA`)
list, then repeat the same `UNAM` index list, then `XNAM`.

Verified instance — `WERoad02Follow` (`0010F589`), a Follow instance.
**⚠ WERoad02 is a HORSEBACK world encounter — its `Ride Horse?=1` /
`Prefer Preferred Path?=1` values are the exception, not the norm (root and
41/44 vanilla Escort instances, 121/124 Follow instances use 0). Freezing
those values as converter defaults made every converted escort/follow NPC
stand still (a horseless actor with Ride Horse?=1 never moves — Pinarus
Inventius in FGC01Rats). Defaults now mirror the template ROOT; the TES4
Use-Horse flag (PKDT 0x00800000, 65 packages) sets `ride_horse` explicitly
in `pack_converter._choose()`. Fixed 2026-07-19.**

```
PKDT.hex = 00000000 12 00 02 82 FFFF 0000   ; Type=18 (instance)
CTDA     = <condition>
QNAM     = 001027A5                          ; owner quest
PKCU.hex = 06000000 2C9B0100 04000000        ; 6 inputs, template=00019B2C (Follow), version=4
ANAM=SingleRef  PTDA = Type 4 (RefAlias), alias 0x29, count 0
ANAM=Float      CNAM = 256.0     ; Min Radius
ANAM=Float      CNAM = 512.0     ; Max Radius
ANAM=Bool       CNAM = 1         ; Accompany?
ANAM=Bool       CNAM = 1         ; Ride Horse?
ANAM=Bool       CNAM = 0         ; Need LOS?
UNAM=0 UNAM=1 UNAM=2 UNAM=4 UNAM=6 UNAM=8
XNAM=9
POBA/INAM/PDTO  POEA/INAM/PDTO  POCA/INAM/PDTO
```

The `UNAM` index list and `XNAM` value are **copied verbatim from the template
root** — they are the root's public-input signature, not something we compute.
`XNAM` is the root's marker byte-count value (`9` for Follow, `5` for Travel,
`32` for Sandbox, `20` for EscortPlayerWhenNear).

`PKCU` = `DataInputCount:u32`, `PackageTemplate:formid`, `VersionCounter:u32`.
`DataInputCount` is the number of `ANAM` value entries (6 above), which equals the
number of `UNAM` entries.

### 1.3 Template roots we will target (all vanilla Skyrim.esm, master index 0)

Skyrim ships a **dedicated template for nearly every Oblivion package type**. This
is the crux of the whole plan: we are not approximating Oblivion behavior with
generic Skyrim sandboxing — we are mapping each Oblivion type onto the Skyrim
template that implements *the same procedure*.

| FormID | EditorID | Procedures in its tree | Key inputs |
|---|---|---|---|
| `00016FAA` | `Travel` | Travel | Place to Travel *(Location)*, Ride Horse, Prefer Preferred Path |
| `0001C254` | `Sandbox` | Travel → UnlockDoors → Sandbox | Location, + 10 booleans (Eating/Sleeping/Conversation/IdleMarkers/Sitting/Wandering/SpecialFurniture…), Energy |
| `00019714` | **`Eat`** | Travel → UnlockDoors → Find → Sandbox → **Acquire** → Find | **Eat Location**, **Food Criteria**, NumFoodItems, Chair Target, Wait Time |
| `00019717` | **`Sleep`** | Travel → **LockDoors** → Find → Sandbox → **Sleep** | **Sleep Location**, **Search Criteria** (bed), Warn Before Locking, Lock Doors |
| `00017723` | **`Patrol`** | Patrol | Patrol Start, Patrol Radius, Repeatable?, Start At Nearest?, Static Pathing? |
| `000503D0` | **`HoldPosition`** | HoldPosition | Hold Position Location, Radius, Center |
| `000A9277` | **`SitTarget`** | Sit → Wait | Sit Location, Search Criteria, **Chairs** *(ObjectList)*, Wait Time |
| `00019B2C` | `Follow` | Follow | Target to Follow *(SingleRef)*, Min/Max Radius, **Accompany?**, Ride Horse?, Need LOS? |
| `00069665` | `EscortPlayerWhenNear` | Escort → Travel | Target to Escort *(SingleRef)*, Destination *(Location)*, Distance to Wait for Player, Follower Min/Max Distance, Run if Behind |
| `000C7039` | **`FleeTo`** | Flee | Distance to Flee, **Flee To Location**, **Flee From Target**, Goal Radius, Quiet? |
| `0003C1C4` | `ForceGreet` | Travel → ForceGreet → Sandbox | Target, Location, **Topic**, Forcegreet Distance, Trigger Radius |
| `000F5842` | `UseMagicRepeat` | (dump before use) | — |

Note `Eat` and `Sleep` are *not* Sandbox-with-a-boolean — they are their own trees
with an `Acquire` procedure (go get food) and a `Sleep`+`LockDoors` procedure
respectively. Using them instead of Sandbox is what makes an Oblivion innkeeper's
"eat at 8pm in the tavern" actually read as eating rather than milling around.

**Step 0 of implementation is still to dump every root we intend to use and freeze
its exact `UNAM`/`BNAM` signature** (`tools/esm/pack_template_dump.py`). The tables in
this document are a design aid, not the source of truth — inputs are positional.

### 1.4 Substructures

`PKDT` (12 bytes): `GeneralFlags:u32`, `Type:u8`, `InterruptOverride:u8`,
`PreferredSpeed:u8` (0 Walk/1 Jog/2 Run/3 FastWalk), `pad:u8`,
`InterruptFlags:u16`, `pad:u16`.

`PSDT` (12 bytes): `Month:s8`, `DayOfWeek:s8`, `Date:u8`, `Hour:s8`, `Minute:s8`,
`unused[3]`, `Duration:s32` **in minutes**. TES4 `PSDT.Duration` is in *hours* —
multiply by 60. TES4 `PSDT.Time` is an hour → `Hour`, `Minute=0`. `-1` = Any.

`PLDT` (12 bytes): `Type:s32`, `Value:4`, `Radius:s32`. Types: 0 Reference,
1 Cell, 2 Near Package Start Loc, 3 Near Editor Loc, 4 Object ID, 5 Object Type,
6 Keyword, **8 Alias**, **9 Reference (alias)**.

`PTDA` (12 bytes): `Type:s32`, `Target:4`, `Count/Distance:s32`. Types:
0 Specific Reference, 1 Object ID, 2 Object Type, 3 Linked Reference,
**4 Ref Alias**, 6 Self.

### 1.5 PKDT General Flags (TES5)

`0x1` Offers Services · `0x4` Must complete · `0x8` Maintain Speed at Goal ·
`0x40` Unlock doors at start · `0x80` Unlock doors at end · `0x200` Continue if PC
Near · `0x400` Once per day · `0x2000` Preferred Speed · `0x20000` Always Sneak ·
`0x40000` Allow Swimming · `0x100000` Ignore Combat · `0x200000` Weapons
Unequipped · `0x800000` Weapon Drawn · `0x8000000` No Combat Alert.

### 1.6 How a package reaches an actor

Two routes, and vanilla uses both:

- **`PKID` on the actor** — the actor's own standing package list (schedules).
- **`ALPC` on a quest reference alias** — quest packages. Vanilla Skyrim.esm has
  **4,125 `ALPC` entries**. Alias packages outrank the actor's base list while the
  quest is running, which is precisely how Oblivion's "quest package with a
  `GetStage` condition sitting at the top of the NPC's list" behaves.

Our `convert_QUST` ([tes5_import/dialogue/converter.py:379](../../tes5_import/dialogue/converter.py#L379))
currently emits **no aliases at all**. That is a hard prerequisite for quest
packages.

---

## 2. What we have on the TES4 side
<a id="2-what-we-have-tes4"></a>

`export/Oblivion.esm/PACK.txt` — 7,209 records, and the exporter already emits
every field we need (`PKDT.Flags/Type/Format`, `PSDT.*`, `PLDT.Type/Location/Radius`,
`PTDT.Type/Target/Count`, `Condition[N].Raw`).

| TES4 Type | Count | Target template | Fidelity |
|---|---:|---|---|
| 6 Travel | 1,924 | `Travel` | **exact** — same procedure |
| 5 Wander | 1,820 | `Sandbox` | **exact** — TES4 Wander = wander/sit/idle in a radius, which is what Sandbox does |
| 3 Eat | 829 | `Eat` | **exact** — dedicated tree w/ Acquire+Find-chair |
| 8 UseItemAt | 751 | `SitTarget` / `Activate` / `Travel` | **partial** — see §2.1, §3.2 |
| 0 Find | 741 | `Activate` / `ForceGreet` / `Sandbox` | **partial** — see §2.1, §3.2 |
| 4 Sleep | 725 | `Sleep` | **exact** — dedicated tree w/ bed-find + LockDoors |
| 1 Follow | 208 | `Follow` | **exact** |
| 9 Ambush | 80 | `HoldPosition` + Weapon Drawn / No Combat Alert | **close** |
| **2 Escort** | **75** | `EscortPlayerWhenNear` | **exact** ← *fgc01rats* |
| 7 Accompany | 40 | `Follow` w/ `Accompany?=1` | **exact** — Skyrim models Accompany as a Follow input |
| 10 FleeNotCombat | 11 | `FleeTo` | **exact** |
| 11 CastMagic | 5 | `UseMagicRepeat` | **close** |

3,874 have conditions, 6,576 have `PLDT`, 1,776 have `PTDT`.

### 2.1 Fidelity analysis — where Oblivion behavior does and doesn't survive

**Locations carry over almost exactly.** TES4 `PLDT` types and TES5 `PLDT` types
are the same enum for 0–5, and vanilla Skyrim *uses* the ones we need:

| TES4 PLDT type | Uses | TES5 support |
|---|---:|---|
| 0 Near Reference | 4,647 | type 0 — used 4,048× in vanilla |
| 3 Near Editor Location | 856 | type 3 — used 605× |
| 1 In Cell | 746 | type 1 — **used 448× in vanilla**, so cell-scoped Eat/Sleep survive |
| 2 Near Current Location | 237 | type 2 — used 341× |
| 4/5 Object ID / Type | 14 | types 4/5 exist |

So "sleep in *this* bed", "eat in *this* cell", "wander within radius R of *this*
marker" all translate 1:1. This is the single biggest reason the schedules survive:
**the spatial data is not being approximated, it is being copied.**

**Schedules carry over exactly.** `PSDT` is month/day-of-week/date/hour/duration in
both games. The only conversion is hours → minutes on Duration. An NPC who ate at
20:00 for 2 hours still does.

**Conditions carry over** via the existing CTDA translator, which is what preserves
the *activation logic* (`GetStage`, `GetDayOfWeek`, disposition checks).

**Where it degrades — be honest about these:**

1. **UseItemAt (751)** — TES4's target is an *object type* (336) or *object ID*
   (318) more often than a specific ref (97): "use any chair", "use any bed".
   Skyrim's `SitTarget` takes a `Chairs` ObjectList input, which covers the common
   furniture cases, but TES4 UseItemAt could point at arbitrary activators. Plan:
   route furniture-ish targets to `SitTarget`, everything else to `Travel` +
   Sandbox-with-special-furniture at the location. Some "use this specific device"
   packages will read as "go there and idle." Accept, document, revisit.

2. **Find (741)** — TES4 Find = travel to a location *and locate an object/actor
   there*, with the object in `PTDT` (464 specific refs, 84 object types). Skyrim's
   `Eat` template has a `Find` procedure but there is no standalone generic Find
   root. Plan: `Travel` to the `PLDT` + Sandbox at the destination. The travel and
   the destination — the parts a player observes — are exact; the "locate this
   object" tail is dropped.

3. **Ambush (80)** — Skyrim has no Ambush procedure. `HoldPosition` + `Weapon
   Drawn` + `No Combat Alert` reproduces "wait hidden, weapon out, don't call for
   help," which is behaviorally most of it, but the trigger-on-detection nuance is
   the combat AI's, not the package's.

4. **PKDT flag bits without TES5 equivalents.** TES4 "Once per day" → TES5 `0x400`
   (same concept, different bit). TES4 "Always run" → `PreferredSpeed=Run` +
   `0x2000`, not a flag. Bits with no counterpart are dropped, not guessed. **Do
   not map a TES4 flag onto a TES5 "Unknown NN" bit** — the old `convert_PACK`
   docstring proposed exactly that ("0x2000 Always run → 0x02000000 Unknown 26"),
   which would set random engine behavior.

**Net:** ~6,300 of 7,209 packages (87%) map onto a Skyrim template that runs the
same procedure with the same location, the same schedule, and the same conditions.
The rest degrade to travel-and-sandbox, which is strictly better than today's
"everything is one sandbox and nobody moves."

---

## 3. Design
<a id="3-design"></a>

### 3.1 New module: `tes5_import/packages/converter.py`

Own file (CLAUDE.md: keep files < ~1000 lines; `dialog_misc.py` is already large).
Delete `convert_PACK` from `dialog_misc.py`.

```
TEMPLATES = {...}                  # dumped from Skyrim.esm, §1.3 — the input signatures
build_package(rec, ctx) -> bytes   # one TES4 PACK → one TES5 PACK instance
```

The core is a **template-instance emitter**:

```
emit_instance(template, inputs, flags, speed, schedule, conditions, owner_quest)
  PKDT  <- flags | Type=18 | preferred speed | interrupt flags
  PSDT  <- schedule (hours→minutes)
  CTDA* <- translated conditions
  QNAM  <- owner quest (quest packages only)
  PKCU  <- (len(inputs), template.formid, version)
  ANAM/CNAM/PLDT/PTDA per input, in the template's declared order
  UNAM* <- template.unam_indices  (verbatim)
  XNAM  <- template.xnam          (verbatim)
  POBA/INAM/PDTO  POEA/...  POCA/...   (all three required)
```

Everything else is a per-TES4-type function deciding *which template* and *what
inputs*. That keeps the type-specific logic small and declarative.

### 3.2 Type mapping (behavior-first)

Each rule below preserves the TES4 `PLDT` (location, **including its type and
radius**), the `PSDT` schedule, and the conditions. Only the *procedure* is
re-expressed in Skyrim's vocabulary.

- **Travel (6)** → `Travel`. `PLDT` → *Place to Travel*. TES4 "always run" →
  `PreferredSpeed=Run` + `0x2000`.
- **Wander (5)** → `Sandbox` at `PLDT`, radius preserved. Booleans:
  Wandering/Sitting/IdleMarkers/Conversation on.
- **Eat (3)** → `Eat` template. `PLDT` → *Eat Location*. The template's own
  Find→Acquire→Sandbox chain does the food-seeking; `PSDT` carries the mealtime.
- **Sleep (4)** → `Sleep` template. `PLDT` → *Sleep Location*. Template finds the
  bed and locks doors; `PSDT` carries bedtime and duration.
- **Find (0)** → `Activate` when `PTDT` names a specific **ACTI/DOOR/CONT** ref
  (see "operate the thing" below); `ForceGreet` when it names the player;
  otherwise `Travel` to `PLDT`, then Sandbox at destination. (Object-location
  tail is dropped — see §2.1.)
- **UseItemAt (8)** → `SitTarget` when the `PTDT` target is furniture (chair/bed/
  bench, incl. object-type targets → *Chairs* ObjectList); `Activate` for any
  other specific ref; otherwise `Travel` + Sandbox with Special Furniture
  allowed.

#### "Operate the thing": Find/UseItemAt at an activator

Both TES4 types are *seek-then-use* procedures, and Skyrim has no standalone
equivalent — the seek half looks like Travel, but the **use** half is the whole
point, because the target object's `OnActivate` script is what advances the
quest. Routing these to Sandbox leaves the actor standing inert beside the
object forever; worse, this idiom usually carries **no `PLDT` at all**, so the
Sandbox gets an empty location and the actor does not even walk over.

Skyrim expresses exactly this with the **`Activate` template (`00019B2D`)** —
24 vanilla instances, e.g. `MQ101HadvarOpenGate2`, `MS02BorkulOpenDoorPackage`,
`TG08AKarliahOpenGatePackage`, `MQ203DelphineLightRightSconce`.

`pack_converter._operate_target()` is the single decision point for both types:
target must be `PTDT.Type == 0` (specific ref), not the player, and the base
signature decides — UseItemAt takes anything **non-furniture**, Find takes
`ACTI`/`DOOR`/`CONT` only. It also drives the PKDT choice (vanilla speed +
`0xFFFF` interrupts), so the actor can break off its schedule to do the job.

Census of Oblivion's 741 Find packages by target: 230 `NPC_` (a greet — must
stay Sandbox), 123 no `PTDT`, 101 `STAT` (markers — Sandbox), 84+70 object-type
targets, 73 player (ForceGreet), and **24 `ACTI`/`DOOR`/`CONT`** — the operate
set. Getting this wrong stalls scripted sequences with no error and no log
line: `CGRatAmbushAPushBricks` (rat → `CGCrumbleWall01REF`) meant the
CharacterGen wall never crumbled, `setstage MQ01 24` never ran, and the
tutorial rats never turned hostile no matter how long the player waited.
- **Follow (1)** → `Follow`, `PTDA` = target, `Accompany?=0`.
- **Accompany (7)** → `Follow`, `Accompany?=1` — Skyrim models Accompany as a
  Follow input, so this is exact, not an approximation.
- **Escort (2)** → `EscortPlayerWhenNear`. `PTDT` → *Target to Escort*,
  `PLDT` → *Destination*.
- **FleeNotCombat (10)** → `FleeTo`. `PLDT` → *Flee To Location*, `PTDT` →
  *Flee From Target*.
- **Ambush (9)** → `HoldPosition` at `PLDT` + `Weapon Drawn` + `No Combat Alert`.
- **CastMagic (11)** → `UseMagicRepeat`.

### 3.3 Reference targets → aliases

`PTDT.Target = 0x00000014` is the **player**. In Skyrim a package can't name the
player as a raw FormID target in a base-actor package; targets resolve through
`PTDA` Type 4 (Ref Alias) on a quest, or Type 0 against a persistent ref.

Rule:
- Package is quest-owned (has a `GetStage`/`GetQuestVariable` condition, or is
  referenced only from a quest context) → emit as **alias package**: create the
  reference alias on the owning QUST, set `QNAM`, use `PTDA` Type 4 / `PLDT`
  Type 8-9 pointing at alias indices, and attach via `ALPC`.
- Otherwise → base actor `PKID`, `PTDA` Type 0 against the persistent ref.

### 3.4 QUST aliases (prerequisite, `dialog_converter.py`)

Extend `convert_QUST` to emit reference aliases:
`ANAM` (next alias id) then, per alias: `ALST`, `ALID`, `FNAM`, `ALFR` (forced ref)
or `ALUA` (unique actor), `ALPC`* , `ALED`.

Sources for aliases:
1. Every actor named by a quest package's `PTDT`/`PLDT` (e.g. `PinarusInventiusREF`).
2. The player — a `Player` alias (`ALFR = 0x00000014`) for escort/follow targets.
3. TES4 `QSTA` quest targets we already parse.

Aliases must be **stable and idempotent** (index by EditorID) because the Papyrus
`Package Property` bindings and existing quest fragments reference them by name.

### 3.5 Conditions

TES4 `Condition[N].Raw` → TES5 `CTDA` via the **existing** translator in
[tes5_import/base/conditions.py](../../tes5_import/base/conditions.py) — do not
write a second one. Quest packages' `GetStage FGC01Rats == 50` conditions are the
entire activation mechanism, so this must be reused, not approximated.

### 3.6 Actor wiring — retire the substitution shim

[tes5_import/packages/actor_wiring.py](../../tes5_import/packages/actor_wiring.py) currently *drops* types
{1,2,7,8,9,10} and collapses everything else to one sandbox. Once real packages
exist:

- `PKID` = the actor's converted packages, **in TES4 order** (Skyrim, like
  Oblivion, takes the first package whose conditions pass — order is behavior).
- Keep `DPLT` (`DefaultMasterPackageList`) as the fallback beneath them.
- Keep the creature path (`DefaultMasterPackageCreature`) — creature AI is driven
  by the behavior-graph work, not by TES4 packages.
- `packages.py` shrinks to the creature default + a fallback for actors whose
  packages all failed to convert.

---

## 4. Implementation order
<a id="4-implementation-order"></a>

Each step is independently testable; **do not batch them**.

0. **Dump the template roots.** Write `tools/esm/pack_template_dump.py` — given a
   template EditorID/FormID, print its `UNAM`/`BNAM`/`PNAM` signature, `XNAM`, and
   procedure tree from `references/Skyrim.esm/PACK.txt`. Freeze the results into
   `TEMPLATES` in `pack_converter.py`. **No table in this plan is a substitute for
   this step.**

1. **Emitter + Travel.** `pack_converter.py`, `emit_instance`, and TES4 Travel →
   `Travel`. Remove `PACK` from `SKIP_TYPES`. Byte-compare one emitted record
   against a vanilla Travel instance (`MQ303OdahviingWaitToFlyAlias` structure).
   Ship it and check NPCs actually walk their routes.

2. **Routine family** — Wander → `Sandbox`, Eat → `Eat`, Sleep → `Sleep`
   (3,374 records, 47% of the corpus). This is what restores daily routines across
   the whole game, and it is the step where "is this really Oblivion behavior?"
   gets answered empirically: pick an NPC with a known Oblivion schedule (e.g. an
   Anvil innkeeper), and watch a full 24h day at high timescale. They should eat,
   sleep, and open shop at the same hours, in the same rooms, as in Oblivion.

3. **QUST reference aliases** in `convert_QUST` (player alias + actor aliases +
   `ALPC`). No behavior change yet; verify in SSEEdit that aliases resolve.

4. **Escort + Follow + Accompany**, routed through aliases. **This is the
   fgc01rats fix.** The script side already works: `TES4_QF_FGC01Rats.psc` already
   emits `PinarusInventiusRef.EvaluatePackage()` at stage 50, and
   `TES4_FGC01PiranusScript.psc` already emits
   `Event OnPackageEnd(...) If akOldPackage == FGC01PinarusEscort`. Both are
   currently calling into a package record that does not exist. Creating
   `FGC01PinarusEscort` as a real PACK is the *only* missing piece — the
   `Package Property` VMAD binding will then resolve instead of reading `None`.

5. **Find / UseItemAt** (1,492 records — sitting, eating at inns, using furniture).

6. **Ambush / Flee / CastMagic** (96 records — long tail).

7. **Delete the substitution shim** from `packages.py`; wire real `PKID` lists in
   TES4 order.

## 5. Verification
<a id="5-verification"></a>

- **Structural:** SSEEdit loads output with no PACK errors; `tools/` script
  byte-compares a converted instance against its vanilla analogue (per CLAUDE.md:
  verify against **both** the xEdit def and a real Skyrim.esm dump).
- **Behavioral, per stage:** load a save, `tc` off, watch an NPC. Step 1 = NPCs
  travel. Step 2 = NPCs eat/sleep/wander on schedule. Step 4 = **Pinarus follows
  the player after stage 50, and starts the wrap-up conversation when the escort
  ends** (proves `PKID` order, alias resolution, `CTDA` translation, `ALPC`
  priority, `EvaluatePackage()`, and `OnPackageEnd` all line up).

## 6. Risks
<a id="6-risks"></a>

- **`PKDT.Type=19` roots must never be emitted.** Writing a template root as an
  actor's package gives an actor a package with no instance data. Always 18.
- **Input order is positional.** The `ANAM` value list must match the template's
  `UNAM` order exactly; a swapped Float feeds "max radius" into "min radius".
  Drive both lists from one frozen `TEMPLATES` entry so they cannot drift.
- **`PSDT` duration unit.** Hours (TES4) vs minutes (TES5). A 6-hour package
  becomes 6 minutes if missed.
- **Alias index churn.** If alias IDs shift between runs, Papyrus property
  bindings and `ALPC` links break. Assign deterministically.
- **Package order is behavior.** Preserve TES4 `AIPackage[N]` order in `PKID`.
- Old `convert_PACK` docstring is actively misleading (wrong `PKDT.Type` semantics,
  invented template FormIDs like "DefaultTravelToRef 0x000D6B8C"). **Delete it
  with the function**; do not mine it for tables.

## 7. `GetVMScriptVariable` package gates need the script on the PLACED ref (2026-07-20)
<a id="7-getvmscriptvariable-package-gates-need"></a>

Symptom: quest NPCs don't move when they should — Arielle (MG04Restore) never
walks to her rented room, Pinarus (FGC01Rats) never hunts the mountain lions.
Their quest packages (travel / escort / find) are gated by a translated
`GetScriptVariable(ActorRef, packageVAR)==N` → `GetVMScriptVariable(630)` with the
variable name in a `CIS2 ::packageVAR_var` (see the legacy-var-condition note in
docs/commentary/tes5_import_dialogue.md). Everything downstream was correct — the
package was detected as quest-owned, an `ALPC` hung it off the actor's QUST
reference alias, `packageVAR` was declared `Auto Conditional`, and the reveal
INFO's TIF fragment set `ActorRef.packageVAR = N; ActorRef.EvaluatePackage()`.

**The break:** `GetVMScriptVariable(ref, "::var_var")` reads the property off a
script attached to the **reference named in param1 (the ACHR)** — NOT the base
actor. The converter attached the actor script to the base `NPC_`/`CREA`
(object_scripts.SCRIPTABLE_TYPES), and the placed `ACHR` had no VMAD of its own.
A base-attached script propagates to instances for property *access* (the
fragment write works), but the condition *read* fails, so the gate never passes,
the package never wins arbitration, and the actor stays put. Verified against
Skyrim.esm: **100% of vanilla func-630 (`GetVMScriptVariable`) package conditions
name a REFR that carries its own VMAD** holding the variable (RatwayDrawbridgeRef
`::isOpen_var`, ResourceObject `::ResourceState_var`, MG02DraugrAmbushTrigger
`::DoOnce_var`). Every vanilla p1 is a REFR (object) — Bethesda never stores this
on an actor, so the actor case is novel, but the VM contract is identical.

**Fix (`object_scripts._relocate_actor_scripts_to_refs`):** for each `ACHR`/`ACRE`
read by a `GetVMScriptVariable` package condition, relocate the actor's script
VMAD from the base record onto the placed ref (`convert_ACHR` now splices it in as
`EDID VMAD NAME …`). The script is *moved* (base entry removed) so there is one
instance both the write and the read resolve to — unless the base has >1
placement (SI victims, Sheogorath's sheep: 3 bases), where the base keeps its
script and the read ref gains its own copy. Scope: 94 actors relocated across
~142 gated refs. Scripts `extends Actor`, which attaches fine to a placed actor
reference. Regression: `test_actor_script_relocated_to_placed_ref`,
`test_shared_base_keeps_script_and_adds_ref`.

## 8. `PLDT` alias locations must be type 8, not type 9 (2026-07-20)
<a id="player-target-is-the-reference"></a>
### A player package target is the REFERENCE, never the base NPC_

"The player", however TES4 spelled it, is the specific reference `PlayerRef`.
Oblivion routinely writes it as Object-ID plus the player's base `NPC_`
(0x07), but Skyrim's escort/follow procedures need a *reference* to act on,
and vanilla is emphatic about which one: **Skyrim.esm names the player as a
package target 543x as (type 0, 0x14) against just 6x as (type 1, 0x07)**.
Left as an Object-ID the engine holds a base form rather than an actor to
follow, so the package is SELECTED but its procedure never engages —
**Morroblivion's chargen guard said "follow me" and stood still**.

`resolve_target` normalizes to the reference FIRST, before the alias lookup,
so the lookup sees 0x14 and a quest package still routes the player through
its quest reference alias (`PTDA` type 4). That aliasing is what lets the
package outrank the actor's standing schedule, so it must not be
short-circuited.

<a id="8-pldt-alias-locations-must"></a>

The fix in §7 was necessary but not sufficient — after it, `sv` on Pinarus showed
`TES4_FGC01PiranusScript` attached to the ACHR, `packageVAR` = 1, the package
property bound, and the quest at stage 50 with every alias filled. He **stood up
out of his chair and then went nowhere**. That symptom is diagnostic: the package
won arbitration and its procedure started, so the fault is in the package's own
data inputs, not the gate.

`build_alias_location` emitted **`PLDT` type 9**. Per xEdit `wbLocationEnum`
(`wbDefinitionsTES5.pas:2620`):

| Type | Meaning |
|---|---|
| 8 | **Alias (reference)** — a REFERENCE alias ✅ what a quest package needs |
| 9 | Alias (location) — an LCTN-type **location** alias |

We were handing a *reference*-alias index to the *location*-alias slot, so the
destination resolved to nothing. Census of Skyrim.esm confirms 9 is a dead end:

```
vanilla PLDT types: {0: 4048, 1: 448, 2: 341, 3: 605, 6: 416, 8: 585, 9: 1, 12: 394}
```

**Type 9 appears once in 6,838 packages; type 8 appears 585 times.** Vanilla
attestation: `WERoad11EscortNoHorse` = `PLDT` type 8 alias 0x22 + `PTDA` type 4.
`PTDA` type 4 ('Ref Alias', 236 vanilla uses) was already correct — only the
LOCATION side was wrong, which is why the escort *target* (the player) was fine
and only the destination was dead.

Fixed to type 8; **85 quest packages** corrected, zero type-9 PLDTs remain.
Regressions: `test_alias_location_uses_reference_alias_type_8`,
`test_quest_escort_location_routes_through_alias_as_type_8`.

Independently confirmed against the engine's own reverse-engineered layout —
CommonLibSSE-NG `RE/P/PackageLocation.h` `PackageLocation::Type`:

```
kNone(-1) kNearReference(0) kInCell(1) kNearPackageStartLocation(2)
kNearEditorLocation(3) kObjectID(4) kObjectType(5) kNearLinkedReference(6)
kAtPackagelocation(7) kAlias_Reference(8) kAlias_Location(9) kNearSelf(12)
```

`SkyrimSE.exe` RTTI also carries `BGSLocAlias` as a class distinct from
`BGSBaseAlias`, corroborating that 8 and 9 resolve through different alias kinds.

**Debug lesson:** `sv` on a selected actor is the fastest way to split this class
of bug — it shows attached scripts, their variable values, and bound properties
in one shot. It cleared the entire condition/alias/script layer and localized the
fault to the package inputs.

## 9. QUST.DNAM.Priority must stay in the engine's 0-100 band (2026-07-20)
<a id="9-qustdnampriority-must-stay-engines"></a>

Third bug on the same symptom, and the systemic one. `FGC01Rats` was written with
**DNAM.Priority = 161**. Census of Skyrim.esm: **391 quests, max priority exactly
100, ZERO above it** — the CK field is 0-100. Our output had **265 of 391 (68%)
over 100**, up to 191.

Cause: `compute_quest_priorities` boosted every staged quest by a raw additive
offset so staged quests would outrank stage-less "conversation container" quests
in dialogue arbitration (§ dialogue notes). TES4 priority 60 + offset 101 = 161.
The boost solved the dialogue-ordering problem and silently created an AI one:
**DNAM.Priority is not only a dialogue tiebreak — it arbitrates a quest ALIAS
PACKAGE against the actor's standing schedule.** Out-of-band priority is why a
converted escort could pass its condition and start (the actor visibly stands up)
and still never travel.

Fix: keep the two-band design but **rescale** each band into the valid range
instead of adding an offset — zero-stage quests → `0..ZERO_STAGE_TOP` (49),
staged quests → `50..QUEST_PRIORITY_MAX` (100). Relative order within each group
is preserved (a linear map, not a rank remap), and the staged band still
universally outranks the container band.

Result: priorities now span 0-100 with **zero** out-of-band; staged 50-100 (n=265),
zero-stage 0-49 (n=125). `FGC01Rats` and `MG04Restore` both land at **83 — the
same priority vanilla gives MQ203**, its own escort-package quest.

Regression: `test_quest_priority_never_exceeds_engine_max` (asserts the range,
the written byte, and that the two-band ordering survives the clamp).

**Lesson:** when a derived value is written into a field the engine reads for
MORE than one subsystem, bound it to the range the engine documents for that
field, not to the storage type's range (U8 0-255). The old code clamped to 255.

## 10. What the engine actually does with a PACK (disassembly, 2026-07-20)
<a id="10-what-engine-actually-does"></a>

Settled by disassembling the **GOG** (unencrypted) `SkyrimSE.exe` 1.6.659 — the
Steam copy is Steam-DRM packed (`.text` entropy 8.00) and cannot be read
statically. Use `tools/disasm/skyrim_disasm.py --exe "D:/Other Games/Skyrim Anniversary
Edition/SkyrimSE.exe"`.

Key RVAs:

| RVA | What |
|---|---|
| `0x451990` | `TESPackage::LoadBuffer` (TESForm vtable slot 6) |
| `0x451a00` | its main subrecord dispatch loop |
| `0x4507d0` | package-type setter; types 18/19 both dispatch via `0x450c68` |
| `0x457cd0` | PKCU handler → builds the package-data object |
| `0x404710` | **the data-input reader** — driven by `PKCU.DataInputCount` |
| `0x4432e0` | single-ANAM fallback reader (only when `Template == 0`) |
| `0x4154d0` | `GetPackageData(slot)` |
| `0x404e10` | slot resolver: searches the **UNAM index-byte array** |
| `0x359c9f` | alias `ALPC` handler |

Subrecords `LoadBuffer` reads: `PKDT PSDT PLDT PTDT PTDA PKCU PKPT PLD2 PTD2
PKE2 PKW3 PKDD PKFD PT2A CTDA IDLA-F POBA POEA POCA EDID OBND VMAD`. `CNAM`/`QNAM`
are package-level u32s (`0x45217d`); `ANAM`/`UNAM`/`XNAM` are consumed by the
data-input reader, not this switch.

**Two contracts that matter for conversion:**

1. **`PKCU.DataInputCount` drives the read.** `0x404710` loops exactly that many
   times consuming `ANAM` entries — regardless of `PKCU.Template`. If the count
   disagrees with the number of `ANAM`s emitted, inputs are lost or the reader
   over-runs into following subrecords.
2. **Procedures address inputs by UNAM byte, not by position.** `0x404e10` walks
   a parallel index-byte array (the `UNAM` list) and returns the entry whose byte
   equals the requested slot. So the `UNAM` list must match the template root's
   exactly; a positional-but-wrong UNAM silently feeds a procedure the wrong
   value.

**A misread worth recording:** `0x457d59`'s `test eax,eax / jne` on
`PKCU.Template` looks like "skip the data inputs when a template is set". It is
not — the real reader (`0x404710`) already ran at `0x457d54`; the branch only
skips building the no-template fallback. Data confirms it: vanilla instances
carry `PLDT` values that differ from their root (Esbern type 0 ref `0x0010ff08`;
root type 3 value 0). **Never zero `PKCU.Template`.**

**Validator:** `tools/esm/pack_validate.py` encodes all of the above.

```bash
python tools/esm/pack_validate.py output/Oblivion.esm/Oblivion.esm \
       --ref "<SSE>/Data/Skyrim.esm" --summary
```

It checks PKDT type/size, PKCU size, count-vs-ANAM agreement, UNAM presence and
length, PLDT/PTDA type legality (rejecting the type-9 bug from §8), and — with
`--ref` — full agreement with the template root on input count, PKCU version,
UNAM order and ANAM type names. It flags the §8 bug on a synthetic pre-fix
record, so a clean run is meaningful. **Current state: all 7,209 converted PACKs
clean.**


## PACK conversion: the 2026-08-17 fix pass
<a id="pack-conversion"></a>

What changed after the audit, including two findings the audit got wrong.

## Status after the 2026-08-17 fix pass
<a id="status-after"></a>

Everything below was measured with `python tools/esm/pack_audit.py --detail`
(which now builds the import's own context via `tes5_import.pack_indexes`).
Oblivion routing before → after, Find/UseItemAt only:

```
Find      Sandbox 513 → 194   Travel 236 → 337   Acquire 0 → 72   Sit 0 → 16
          SitTarget 0 → 17    ForceGreet 79      Activate 26
UseItemAt Sandbox 662 → 656   Activate 87        Sit 0 → 6        SitTarget 2
```

### Fixed

* **Gap 1 (Object-ID / Object-Type targets).**
  `pack_converter._find_object_criteria` + `object_criteria_kind`:
  * actor base with **one** placement → the Object ID *is* that ref → Travel
    near it (alias-routed like any specific ref);
  * actor base with several placements (FGC06Goblin ×9, FGD08Goblin ×11,
    FGC01 lions/rats) → a **chain of Follow packages, one per placed target,
    nearest the hunter first**, each gated on the source's conditions +
    `GetInSameCell(target)`, `GetDisabled==0` and `GetDead==0` on the target,
    ahead of the source in the alias ALPC / PKID list; the source itself
    (a wander-only in-cell Sandbox) is the tail. FormIDs are derived from
    (source PACK, target ref). **Measured live 2026-08-18** (game bridge, FGC06
    at stage 30): with the Sandbox alone all three fighters were RUNNING their
    hunt package (`getiscurrentpackage`) yet stayed within ~300 units of spawn
    — the Sandbox wander is local; a PLDT type-4 "Object ID" location patched
    into the live package left the fighter standing (type 4/5 are dead in the
    engine); the mine's navmesh is one component (`tools/navmesh/reach.py`).
    Oblivion: 6 hunts → 46 seek links;
  * item base/type (WEAP/INGR/MISC/ALCH/BOOK/… incl. `SEBruscusDannusFind*`,
    `HeroLoot`, the goblins' totem staffs) → **Acquire** with the same
    criteria and `PTDT.Count` as num-to-acquire (vanilla
    `MQ101RalofGetDoorKey` type 1, `MS09Stage25JonAcquireNote` type 0);
  * furniture base/type → **Sit** with the criteria (vanilla
    `MG06Stage99MirabelleGetIntoFurniture` type 1, `DA14StartSamSit` type 2),
    also for UseItemAt "any furniture" (6);
  * a specific STAT/other ref (`SEBlackrootFindPrisonerNNTarget`, 101) →
    Travel near it; a specific FURN ref → SitTarget; a specific item ref →
    Acquire.
  * **The TES4 and TES5 object-TYPE enums differ** (Apparatus/Clothing/
    NPCs/Creatures/Soul Gems exist only in TES4, everything after Activators is
    shifted). `TES4_TO_TES5_OBJECT_TYPE` translates every PTDT type-2 target
    and PLDT type-5 location; before, the value was parsed as hex and
    load-order-shifted (TES4 "Furniture" 12 → written as 0x0100000C).
  * A PLDT type-4 (Object ID) location whose base has one placement is
    written as the type-0 reference (4,048 vanilla uses vs 0 for type 4).
* **Gap 2.** `_base_sig` now covers every placeable signature
  (`pack_indexes.PLACEABLE_BASE_SIGS`), so MS39's APPA mortar resolves;
  UseItemAt at a carriable item ref falls to the sandbox (Activate would pick
  it up), STAT stays Activate. `FURNITURE_SIGS = {FURN}`.
* **Gap 4, func 53.** `build_script_var_map` was blind to scripted
  WEAP/MISC/… bases (21 PACK + 13 INFO conditions, e.g. the goblin
  `CreatureGoblinLeaderFindHead*` totem gates). Beyond that, an unresolvable
  script variable is now emitted against `::TES4NoSuchVariable_var` — reads
  0, exactly TES4's value for a missing variable — instead of being dropped
  (fail-open). SE08's five Xedilian victims no longer force-greet/flee
  unconditionally. Func 171 `IsPlayerInJail` stays dropped: Skyrim has no
  equivalent function (`GetArrestedState` is the arrest, not the sentence).
* **AddScriptPackage** (not in the original audit): packages forced on by
  script and gated on a quest are attached to the actor's alias as ALPCs
  (`pack_aliases.build_script_assigned_packages`; 15 Oblivion / 4 Nehrim,
  incl. `MQ12MartinPlaceWelkyndStone`, `MQ14MartinPlaceSigilStone`,
  `MQ15MartinOpenPortal`, `MQ00CalebroPackage04`). Unconditioned forced
  packages (52 / 20) are NOT attached — with no gate they would run whenever
  the quest runs — and remain the known `AddScriptPackage → EvaluatePackage`
  gap.

### The audit was wrong about

* **Gap 3** — `PackagePlan.build` and the base-signature indexes already take
  `master_export` (checked in HEAD before this pass).
* **Gap 5** — Escort's arrival radius IS the destination location's radius,
  which `build_location` preserves (CGEmperorToMarkerB writes PLDT type 8
  radius 70 in the built ESM). Slots 3/4/5 are follower spacing, which TES4
  does not author.

### Still open (measured)

* UseItemAt with an object-type/token criteria — 232 `aaaObeisanceToken` /
  `aaaPreachToken` / Hoe / Rake / PaintBrush MISC tokens, 79 "read any book",
  55 "any melee weapon" drills, 142 "None" — keep the sandbox at the location.
  Oblivion drives these through IDLE records conditioned on the used item;
  Skyrim's equivalent is a placed IdleMarker/furniture + UseIdleMarker/
  UseWeapon-with-a-dummy, i.e. synthesised references, not a template choice.
* Find at a container/door/activator *type* (SE12 gnarl chests ×6, obelisk,
  cathedral doors) and at "any NPC" (ImpEx couriers ×35): sandbox in the
  authored cell. `ActivateAfterFinding` exists as a root but has 0 instances.
* Unconditioned script-forced packages (above).


## PACK conversion: verified-correct behaviour
<a id="pack-conversion-2"></a>

Binary layouts and behaviours confirmed against vanilla. Do not re-litigate.

## Verified correct — do NOT "fix" these
<a id="section"></a>

Recorded so a later session does not re-litigate them.

| Area | Verification |
|---|---|
| PKDT flag re-derivation | Matches xEdit `wbPackageFlags` (`wbDefinitionsCommon.pas:7635`) bit for bit. Both collisions handled: TES4 bit 3 `Lock Doors At Package Start` vs TES5 `Maintain Speed At Goal`; TES4 bit 20 `Armor Unequipped` vs TES5 `Ignore Combat`. |
| PKDT dual format | `export_PACK` emits `PKDT.Format`, matching `wbPACKPKDTDecider` (4-byte subrecord = U16 flags + U8 type; 8-byte = U32 + U8). Measured: **561** old-format Oblivion packages, **zero** with any flag bit above 16 — so no flag is misread. Nehrim is 100% new-format. |
| PSDT layout | 12 bytes `<bbBbb3xi>`, Duration hours→minutes, `minute=-1`. Confirmed against all **5,961** vanilla PSDTs. The nonzero bytes at `[5:8]` in some vanilla records are uninitialised garbage (`ababab`, ASCII fragments), not a field. |
| PSDT DayOfWeek | `wbPackageScheduleDayOfWeekEnum` is a **shared** enum — identical 0..10 values incl. `Weekdays (MTWTF)`, `Weekends (SS)`, `Monday, Wednesday, Friday`. Oblivion's 306 day-scheduled packages copy through correctly. |
| PSDT Date | Non-issue: 7,134/7,209 Oblivion and 1,900/1,900 Nehrim packages write 0, and all 5,961 vanilla records write 0. |
| PTDA slot 3 = 0 | Re-confirmed: all **3,740** vanilla PTDA records write 0 across every target type. |
| Speed byte | Vanilla honours `PKDT` speed only when flag `0x2000` (Preferred Speed) is set — **4,386** vanilla records carry an inert `speed=2` with the flag clear. Writing walk-unflagged is inert, not a defect. |
| PKDT byte layout | `<IBBBBHH>` confirmed: `[4]`=Type (18 ×5,857 / 19 ×104), `[5]`=interrupt override, `[6]`=speed, `[10:12]`=interrupt flags. |
| Reused CTDA indices | `_FUNC_DROP` correctly catches the index collisions, incl. **365 = `GetPlayerInSEWorld` (TES4) → `IsChild` (TES5)**, 249 `GetPCFame` → `IsInDialogueWithPlayer`, 224, 227, 258, 259, 264. |
| Structural contract | `tools/esm/pack_validate.py output/Oblivion.esm/Oblivion.esm` → **clean, 7,209 records**. Every defect below is *semantic*, which is exactly why the structural validator passes. |

---

## Shop doors: Unlock Doors At Location becomes Unlock At Start
<a id="shop-doors-unlock-at-location"></a>

Oblivion opens a shop through the owner's daytime package flag `0x100` Unlock
Doors At Location; the door itself is authored locked (Edgar's Discount Spells:
inside door `0002C243`, lock 50, owned by `EdgarVautrine`). His
`aaaServicesEditorLoc8x12LockAtEnd` is a Travel to his editor location with flags
`0x311`. TES5 bit 8 is Request Block Idles, so the bit was dropped, and the Travel
template has no UnlockDoors procedure: the door stayed locked all day, and a
player who got in anyway was a trespasser, so the owner asked them to leave
instead of trading. Confirmed fixed in-game.

The TES4 bit now maps to TES5 `0x40` Unlock Doors At Package Start, which is how
vanilla writes shop hours (`MarkarthGeneralStoreVendor8x16xPackage` `0xC1`,
`LucanValeriusTraderServices7x13` `0x251`; 35 of 5,961 vanilla packages set
`0x40`). Oblivion.esm has 165 packages with `0x100`.

Still dropped: TES4 Lock Doors At Package End (`0x10`, 92 packages) and At
Location (`0x20`, 141). TES5 has no named flag for either (the vanilla bits
`0x10`/`0x20` appear on 33/2 packages but are unnamed in xEdit and the CK).
Relocking comes from the Sleep template's LockDoors procedure, so a shop whose
owner sleeps elsewhere stays unlocked overnight.
