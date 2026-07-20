# PACK Conversion Plan (TES4 → TES5)

Goal: **Oblivion-equivalent AI behavior expressed in Skyrim syntax.** Not
"records that load" — actors must keep their schedules, walk their routes,
follow, escort, flee, and ambush the way they did in Oblivion.

Status: `PACK` is in `SKIP_TYPES` ([tes5_import/constants.py:367](../tes5_import/constants.py#L367)).
Zero package records are written. Every converted actor instead receives a single
vanilla `DefaultSandboxCurrentLocation1024` substitution from
[tes5_import/packages.py](../tes5_import/packages.py), so **no NPC in the game
keeps its Oblivion schedule** — they all sandbox in place forever.

`convert_PACK` exists in [tes5_import/record_types/dialog_misc.py:155](../tes5_import/record_types/dialog_misc.py#L155)
but is dead code, and its brainstorm docstring is **wrong on the central point**
(it claims `PKDT.Type` carries the TES4 behavior enum). Delete the docstring's
claims; the facts below replace them.

---

## 1. Ground truth (verified, not assumed)

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
its exact `UNAM`/`BNAM` signature** (`tools/pack_template_dump.py`). The tables in
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

Our `convert_QUST` ([tes5_import/dialog_converter.py:379](../tes5_import/dialog_converter.py#L379))
currently emits **no aliases at all**. That is a hard prerequisite for quest
packages.

---

## 2. What we have on the TES4 side

`export/Oblivion.esm/PACK.txt` — 7,209 records, and the exporter already emits
every field we need (`PKDT.Flags/Type/Format`, `PSDT.*`, `PLDT.Type/Location/Radius`,
`PTDT.Type/Target/Count`, `Condition[N].Raw`).

| TES4 Type | Count | Target template | Fidelity |
|---|---:|---|---|
| 6 Travel | 1,924 | `Travel` | **exact** — same procedure |
| 5 Wander | 1,820 | `Sandbox` | **exact** — TES4 Wander = wander/sit/idle in a radius, which is what Sandbox does |
| 3 Eat | 829 | `Eat` | **exact** — dedicated tree w/ Acquire+Find-chair |
| 8 UseItemAt | 751 | `SitTarget` / `Travel` | **partial** — see §2.1 |
| 0 Find | 741 | `Travel` + `SitTarget`/`Sandbox` | **partial** — see §2.1 |
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

### 3.1 New module: `tes5_import/pack_converter.py`

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
- **Find (0)** → `Travel` to `PLDT`, then Sandbox at destination. (Object-location
  tail is dropped — see §2.1.)
- **UseItemAt (8)** → `SitTarget` when the `PTDT` target is furniture (chair/bed/
  bench, incl. object-type targets → *Chairs* ObjectList); otherwise `Travel` +
  Sandbox with Special Furniture allowed.
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
[tes5_import/dialog_conditions.py](../tes5_import/dialog_conditions.py) — do not
write a second one. Quest packages' `GetStage FGC01Rats == 50` conditions are the
entire activation mechanism, so this must be reused, not approximated.

### 3.6 Actor wiring — retire the substitution shim

[tes5_import/packages.py](../tes5_import/packages.py) currently *drops* types
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

Each step is independently testable; **do not batch them**.

0. **Dump the template roots.** Write `tools/pack_template_dump.py` — given a
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

- **Structural:** SSEEdit loads output with no PACK errors; `tools/` script
  byte-compares a converted instance against its vanilla analogue (per CLAUDE.md:
  verify against **both** the xEdit def and a real Skyrim.esm dump).
- **Behavioral, per stage:** load a save, `tc` off, watch an NPC. Step 1 = NPCs
  travel. Step 2 = NPCs eat/sleep/wander on schedule. Step 4 = **Pinarus follows
  the player after stage 50, and starts the wrap-up conversation when the escort
  ends** (proves `PKID` order, alias resolution, `CTDA` translation, `ALPC`
  priority, `EvaluatePackage()`, and `OnPackageEnd` all line up).

## 6. Risks

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
