# CK Warnings Audit — Oblivion.esm

Bucketed inventory of every warning the Creation Kit emits when loading our
converted `Oblivion.esm`, ordered **easiest to hardest to fix**.

Prior sweep (2026-07-16) is recorded at the bottom under
[Fixed in the 2026-07 sweep](#fixed-in-the-2026-07-sweep) — do not re-diagnose those.

---

## Snapshot: 2026-08-22 16:46 (CK 1.6.1378.1, CKPE 0.6.267)

**45,437 warning lines.** 46,050 FormID mentions are `01` (ours), 1,320 are `00`.

| # | Bucket | Count | Tag | Effort | Status |
|---|---|---|---|---|---|
| 1 | Book has invalid character | 543 | FORMS | trivial | **WONTFIX** — font, not data |
| 2 | MGEF invalid primary Actor Value → HEALTH | 14 | MAGIC | trivial | **WONTFIX** — vanilla-legal |
| 3 | MGEF counter-effect invalid FormID | 25 | FORMS | trivial | **FIXED** 2026-08-22 |
| 4 | Shader effect sound `01800000` not found | 102 | MASTERFILE | easy | **FIXED** 2026-08-22 |
| 5 | Biped Object slot 44 invalid for DefaultRace | 596 | MASTERFILE | — | **NOT A BUG** — the body-slot patch (now slots 49/59) |
| 6 | One-way faction Friend/Ally relations | 105 | DEFAULT | easy | open |
| 7 | Duplicate EditorIDs → `…DUPLICATE001` | 270 | EDITOR | — | **DEFERRED** — cosmetic; QUST/DIAL feed voice paths |
| 8 | "cannot be scripted, but has scripts attached" | 136 | SCRIPTS | moderate | open |
| 9 | Script property points at invalid object | 40 | SCRIPTS | moderate | **FIXED** 2026-08-23 |
| 10 | Potentially invalid X/Y on reference | 62 | MASTERFILE | — | **WONTFIX** — authored data |
| 11 | Ref should be persistent but is not | 18 | MASTERFILE | moderate | open |
| 12 | CTDA param init failures | 44 | MASTERFILE | moderate | **35 FIXED** 2026-08-23; 11 by design |
| 13 | Navmesh should be refinalized (bounds missing) | 19 | PATHFINDING | moderate–hard | open |
| 14 | Special Ref not in Special Ref data (map markers) | 513 uniq | MASTERFILE | hard | LCSR done; LCEC fix pending verify |
| 15 | Exterior cell no longer tagged to this location | 989 | MASTERFILE | hard | LCEC fix pending verify |
| 16 | Ref uses location but not in unloaded-ref data | 15,333 | MASTERFILE | hard | **FIXED** 2026-08-22 (CK-confirmed 0) |
| 17 | Cell not in worldspace exterior cell data | 26,124 | MASTERFILE | hard | LCEC fix pending verify |

Buckets **14–17** account for 43,448 lines (**96%**). Bucket 16 is CK-confirmed fixed;
14/15/17 share one cause (the XLCN/LCEC contract) whose fix is built but not yet
CK-verified.

---

## 3. MGEF counter-effect invalid FormID (25) — trivial

`[FORMS] Invalid form ID for effect setting found while loading counter effects for XXXX.`

**FIXED 2026-08-22** — `_sort_mgef_by_counter_effects` in
[writer.py](../../tes5_import/base/writer.py).

The original diagnosis ("targets no longer exist") was **wrong**. Every ESCE
target resolves to an MGEF that is present in the output, no ESCE dangles, and
the DATA counter-effect count matches the ESCE subrecord count on every record.

The real cause is **group ORDER**. The reported ids decode as real records —
`16783472` = `0x01001870` = DSPL, `16783458` = `0x01001862` = CUPO — and the CK
resolves ESCE *while reading the MGEF group*, looking the target up in what it
has loaded so far. A record naming a counter effect that appears later in the
group fails.

Measured on the pre-fix ESM: 41 ESCE references, **25 pointing forward and all
25 warned; 16 pointing backward and all 16 silent** — the forward set equals the
warning set exactly.

Vanilla Skyrim.esm ships **zero** ESCE subrecords across its 950 MGEFs, so there
is no vanilla ordering to copy; the engine's counter-effect load path is simply
order-dependent and untested by Bethesda's own data.

Fix: a stable topological sort of the MGEF group at serialization time, so every
target precedes its referrer. Records keep source order except where a dependency
forces one earlier, which preserves byte-reproducibility; a cycle (Oblivion has
none) degrades to source order rather than raising. Verified after rebuild: 340
records, 41 ESCE refs, **0 forward, 0 dangling**.

## 4. Shader effect sound `01800000` not found (102) — easy

`[MASTERFILE] Could not find shader effect sound (01800000) on object 'X'.`

**FIXED 2026-08-22** — `convert_EFSH` in
[world.py](../../tes5_import/record_types/world.py).

Not a stray FormID at all: **the EFSH DATA tail was written 4 bytes off**.

Every field from offset 260 up sat one slot too high against the xEdit TES5
EFSH struct. That put the float `1.0` written for "Addon Models - Scale Out
Time" into **`Ambient Sound` at offset 308**. Float 1.0 is `0x3F800000`; the CK
reads it as an object id, takes the low three bytes `0x800000`, applies our
plugin index `01`, and reports `01800000` — the exact id in all 102 warnings.

Offsets verified field-by-field against the xEdit definition **and** 152 real
Skyrim.esm EFSH records:

| Offset | Field | Was written at |
|---|---|---|
| 260 | Holes - End Val | 264 |
| 264 | Edge Width (alpha units) | 268 |
| 268 | Edge Color | 272 |
| 272 | Explosion Wind Speed | *(skipped)* |
| 276/280 | Texture Count U/V | 280/284 |
| 284-304 | Addon Models Fade/Scale block | 288-308 |
| **308** | **Ambient Sound (SNDR FormID)** | *(never written — got Scale Out Time)* |
| 312/316 | Fill Color Key 2/3 | 316/320 |
| 320-340 | Color key scales and times | 324-344 |
| 344 | Color Scale | 348 |
| 376 | Frame Count | 372 |

Two values were also wrong on the merits and corrected against the vanilla
census: `Holes - End Val` is 0.0 on 143 of 152 vanilla records (we wrote 1.0),
and `Frame Count` is 0 on 108 of 152 (we wrote 1 — a TES4 shader has no frame
data to convert). `Ambient Sound` stays 0: TES4 EFSH has no sound field and 95
of 152 vanilla records leave it null.

Verified after rebuild: 102 EFSH records, **all 11 checked fields correct on
102/102**, Ambient Sound zero on every one.

## 6. One-way faction Friend/Ally relations (105) — easy

`[DEFAULT] Faction 'A' is a Friend or Ally of Faction 'B', but 'B' is not a Friend or Ally of 'A'.`

Skyrim treats XNAM Friend/Ally as reciprocal by convention; Oblivion does not.
Fix: after building all FACT records, symmetrize Friend/Ally XNAM entries.

Entries naming `PlayerFactionDUPLICATE001` are bucket-7 damage, not real
asymmetry — they will disappear when the EditorID collision is fixed.

## 8. "cannot be scripted, but has scripts attached" (136) — moderate

`[SCRIPTS] X (01......) cannot be scripted, but has scripts attached to it.`

These are the **LVLN shell NPCs** from
[leveled_actors.py](../../tes5_import/actors/leveled_actors.py). An NPC_ whose `TPLT`
points at an LVLN is not scriptable in Skyrim, so **every VMAD we attach to a
shell is silently discarded.**

Names confirm the population: `SEZealot*`, `MG16Necromancer*`,
`SECreatureAtronachFlesh*`, `SE04FelldewElytra`, `SECreatureGnarl*`,
`MQ06MythicDawnAnteGuard*`, `SE32genericDefender*`. 40 of the 136 lines are
`Property` entries on those same shells.

This is a real behavioural loss, not cosmetic — the Oblivion scripts on those
actors do not run. Fix requires moving the script off the shell (onto the
placed ACHR, or onto a quest alias that fills the spawned actor).

## 9. Script property points at invalid object (40) — FIXED 2026-08-23

`[SCRIPTS] <name> on script <script> on <form> is pointing at an invalid object.`

Dominated by two scripts x 10 races each (`TES4_DAHermaeusStaff` on
`DAHermaeusThing`, `TES4_DABoethiaPortal` on `DABoethiaPortalFinal`), plus 16 on
`TES4_SE08AllyMainScript`, one on `TES4_DAMalacathStatueScript`, one on
`TES4_MS40DaggerSpellEffect`, one `BurdTopic` on `TES4_QF_MQ07`.

An earlier draft of this audit guessed "a property typed as ObjectReference but
filled with a RACE form". **That was wrong** — the generated `.psc` declares
`Race Property Argonian Auto`, correctly typed. The VMAD binding was the
problem.

**PLAYABLE RACES ARE NOT CONVERTED.** Every actor is retargeted onto Skyrim's
own RACE record (`_resolve_npc_race` -> `RACE_MAP`), so no `TES4Argonian` RACE
is ever written. But the script-property binder remapped race references by load
order like any other form, producing `0x01023FE9` and friends — ids that exist
in no file. The CK reports one warning per bound property, and the scripts, which
branch on the player's race, silently do nothing.

[object_scripts.py](../../tes5_import/base/object_scripts.py) already had exactly this
pattern for engine-hardcoded bases (`TES4_ITEM_FORMID_TO_SKYRIM`, so a scripted
`AddItem Gold001` hands out real Skyrim gold). Races are the same case and were
simply missing; `_skyrim_race_formid` now routes them through
`TES4_RACE_FID_TO_EDID` -> `RACE_MAP`, mirroring `_resolve_npc_race`. There is
deliberately NO `DEFAULT_RACE` fallback — an unrecognised id falls through to
the normal remap, so only ids positively known to be races are redirected.

Verified in the built ESM: Argonian -> `0x00013740`, Nord -> `0x00013746`,
Orc -> `0x00013747`, while the `DAHermaeusMora` QUST property still resolves to
our own `0x010146AF`.

**Note on reading VMAD by hand:** xEdit's `wbScriptObjFormatDecider` selects
"Object v1" `(FormID, Alias, unused)` when `objFormat == 1` and "Object v2"
`(unused, Alias, FormID)` otherwise. Getting that backwards makes every property
look like a null FormID with the real id sitting in the alias field.

Remaining, not fixed: one
`SE32ArrowSteelRef ... is pointing at an object that can be picked up and has an
enable state parent` — separate and minor.

## 11. Ref should be persistent but is not (18) — moderate

`[MASTERFILE] Ref to base object X in cell Y should be persistent but is not.`

18 refs, incl. `MQMythicDawnShrineGuard` (LakeArriusShrineDagon, ×2),
`SEGoldenSaintGeneric` / `SEDarkSeducerGeneric` (SENSPalace, ×2 each),
`EvangelineBeanique` (ICPalaceOcatoChambers), `MQMythicDawnXivCatcher` and
`MQMythicDawnLowM` (TestParadiseCave), `CGMythicDawnAssassinAmbushC`
(ImperialDungeon), plus clutter in QASmoke/ChorrolHouseForSale.

⚠️ **Read [ck_vs_game_missing_objects.md](../commentary/ck_vs_game_missing_objects.md) before
acting.** Force-persisting on the strength of this warning is exactly the fix
that was tried and refuted there — it made objects vanish in game.

## 12. CTDA param init failures (44) — 35 FIXED 2026-08-23, 11 by design

Four shapes, three different causes.

### 33 `Unable to find Function Info TESForm` — FIXED

The named FormIDs (`0x00011F7C`, `0x0005CF61`, `0x0004FE12`, `0x0004FE13`,
`0x00032AFE`) are all **Oblivion WRLD records that kept the master index**:
`0x00011F7C` is `SETheFringeOrdered`, written by us as `0x01011F7C`. With index
0 they either name nothing or collide with an unrelated vanilla REFR — which is
also what produced the 9 "Non-Persistent Function Info Reference" lines, since
`0x00032AFE` happens to be a non-persistent vanilla ref.

**40 of the 47 complained-about parameters belong to condition function 310,
`GetInWorldspace(ptWorldSpace)`.** Its parameter was never remapped because
`ptWorldSpace` is **used by xEdit's function table but never declared in its
`TConditionParameterType` enum** — the only such type. Our generator classifies
by position in that enum, so an undeclared type fell through to "plain value"
and the id was left alone.

Fixed in [gen_ctda_param_types.py](../../tools/generators/gen_ctda_param_types.py):
`_FORMID_TYPES_NOT_IN_ENUM` names `ptWorldSpace` explicitly, and the generator
now **aborts** when any function uses a type the enum does not declare, rather
than silently guessing. Regenerating changed exactly one line of
`ctda_param_types.py` (`310: frozenset({1})`).

Verified in the built ESM: 43 func-310 conditions, **43 remapped, 0 left at
index 0**.

### 2 `GetItemCount(0x0000000B)` — FIXED

`_remap_formid` in [dialog_conditions.py](../../tes5_import/base/conditions.py)
passed through **everything** with index 0 and object id `< 0x100`. That is far
too wide: Oblivion.esm defines **127 records** below 0x100 — Tamriel WRLD
`0x3C`, gold `0xF`, `DASkeletonKey` `0xB`, 57 DIALs from `0xAA`, 21 SKILs, 27
marker STATs. Two MQ08 Skeleton Key INFOs therefore asked for `0x0000000B`,
which is not a Skyrim form.

The pass-through set is now **enumerated**: the player forms PLUS the six
engine globals.

⚠️ **Narrowing it to the player forms alone is WRONG and was tried** — it cost
119 new warnings against the 2 it fixed. `GameYear 0x35` … `TimeScale 0x3A`
exist at **identical ids in both games** (Skyrim.esm carries `GameHour` at
`0x38` exactly as Oblivion.esm does), the engine owns the live copy, and
`convert_GLOB` deliberately writes NO record for them — so a remapped
`0x01000038` points at nothing. Two existing tables already documented this:
`ENGINE_GLOBAL_FORMIDS` in `constants.py` and `_ENGINE_GLOBALS` in
`record_types/actors.py`. Check what actually lives below `0x100` before
touching this predicate.

Note `text_reader._ENGINE_FIXED_FORMIDS` is `{0x14, 0x7}` and that is correct
*there* — a record FIELD naming a global resolves by name through
`ENGINE_GLOBAL_FORMIDS`, so only the CTDA numeric path needs the wider set.

Verified in the built ESM: both Skeleton Key conditions read `0x0100000B`, 255
conditions reference Skyrim's `0x00000036/37/38`, none reference ours, and no
GLOB record is written at an engine-global id.

### 11 `Unable to find variable ::TES4NoSuchVariable_var` — BY DESIGN, do not "fix"

A deliberate sentinel. When a TES4 `GetScriptVariable` names a variable that
does not exist (no script on the base, no variable at that index, deleted ref),
the converter emits a CIS2 name **no script declares**, so the read yields 0 —
exactly what Oblivion returns in those cases, preserving the authored comparison
and Or-flag.

The alternative was tried and **failed open**: dropping the condition made SE08's
five Xedilian victims force-greet and flee unconditionally, and 14 jailor
packages run with the player free. See `_convert_script_var_ctda`.

## 13. Navmesh should be refinalized (19) — moderate to hard

`[PATHFINDING] NavMesh in cell X should be refinalized, there are navmesh bounds missing.`

All 19 are interiors: 11 Chorrol castle/wall towers
(`ChorrolCastleTowerBL/BL2/BR/FL/FR/R2`, `ChorrolCastleWallTowerNE/NW/SE/SW`,
`ChorrolFightersGuildTower`), `ChorrolGateBRandBL`, `ImperialDungeon01`,
`ImperialDungeon03`, `ICPalaceLibrary`, `ICWaterfrontLighthouse`,
`SkingradCastleSouthHall`, `OblivionRDCitadel04`, `OblivionMqKvatchCitadel`.

Missing NVNM bounds data on generated navmeshes. See
[world_land_navmesh_notes.md](../commentary/tes5_import_navmesh.md) and
[ck_navmesh_generation.md](../commentary/ck_navmesh_generation.md).

---

## 14–17. The location cluster (43,448 lines, 96%)

**One root cause: XLCN and LCEC are a two-way contract, and we only wrote one
side.** Bucket 16 needed the LCTN reference arrays as well.

| # | Warning | Count | Cause |
|---|---|---|---|
| 17 | Cell not in exterior cell data | 26,124 | cell XLCN not matched by the location's LCEC |
| 16 | Ref uses location, not in unloaded ref data | 15,333 | LCTN had no `LCPR` |
| 14 | Special Ref not in Special Ref data | 1,002 (513 unique) | LCTN had no `LCSR`, **and** the LCEC contract |
| 15 | Exterior cell no longer tagged to this location | 989 | two locations claimed the same LCEC cell |

### The contract

The CK runs one validator over every LCTN — its own summary line is
*"Warnings were encountered validating unloaded ref data for Location '%s'"* —
and all four warnings come out of it. The rule it enforces:

> Anything that claims a location (a cell's `XLCN`, or a placed reference's
> `XLCN`/`XLRT`) must be claimed back: that cell's grid square has to appear in
> the location's `LCEC` cell list.

`LCEC` is `World(4) + [GridY(i16), GridX(i16)]...`.

**Vanilla is the opposite of blanket coverage.** Of Skyrim.esm's 16,978
exterior cells only **982 carry XLCN at all** (948 of them LCEC-listed); the
other 15,996 are deliberately nameless and show "Wilderness" on a load door.
Its LCECs are small and hand-picked — 344 locations, 948 coords total, median
**2** cells per location, max 22.

### What we were doing

`convert_CELL` gave EVERY exterior cell an XLCN, falling back to a
per-worldspace location when no marker covered the grid square, so that a cell
would not read as "Wilderness". The naming goal is right and deliberate; the
placement was not. Those 60 worldspace locations
(`TES4CyrodiilLocation`, `TES4RealmofSheogorathLocation`, …) have an **empty
LCEC**, so every cell pointing at one failed the check.

Measured on the pre-fix build: 29,743 exterior cells carried XLCN — **26,124
aimed at an empty-LCEC worldspace location (every one of them warning)** and
3,619 at a real marker location whose LCEC did list them (silent). The split is
exactly the warning count.

`_reference_location` had the same fallback, which is what left 489 map markers
still warning after LCPR/LCSR were added: each sat in a cell outside its
location's LCEC.

### The fix: move the worldspace location onto the WRLD record

**`XLCN` belongs on the WRLD, not on every cell.** The engine walks up to the
worldspace when an exterior cell has no Location of its own, so ONE subrecord
names every cell in the world — and a WRLD-level XLCN is **exempt** from the
per-cell LCEC validation.

Vanilla settles both halves of this:

- **32 of Skyrim.esm's 37 worldspaces** carry XLCN on the WRLD record, while
  only **982 of its 16,978 exterior cells** carry one individually (948 of
  those LCEC-listed). 94% of vanilla exteriors have no cell-level XLCN at all.
- `Blackreach`'s WRLD XLCN points at `BlackreachLocation`, which carries **no
  LCEC whatsoever**, and it does not warn. That is the direct proof the
  WRLD-level path skips the check.

So `convert_WRLD` now emits `XLCN` (field order: EDID … FULL, **XLCN**, WNAM)
from the same `_WORLD_LOCATION` map the cell fallback used, and `convert_CELL`
/ `_reference_location` drop the worldspace-wide fallback: an exterior only
claims a location whose LCEC actually lists its square.

Names are preserved — verified in the built ESM: 59 worldspaces carry XLCN
(TES4Tamriel → "Cyrodiil", SEWorld → "Realm of Sheogorath", SENSBliss →
"Bliss", …) and 0 exterior cells are left with an uncovered cell-level XLCN.

**A "fix" that deletes the fallback and stops there is wrong** — it silences the
warnings by making every exterior read "Wilderness". Both halves are required.

### Bucket 15 — LCEC ownership must be EXCLUSIVE

A separate defect from the XLCN contract above, and the one thing the WRLD-XLCN
fix did NOT close (989 lines, unchanged).

A cell carries a single `XLCN`, so when two locations both list that square in
their `LCEC` only one can win and the CK reports the losers with
"Exterior cell (x, y) in world 'W' is no longer tagged to this location".

Measured on the build: **every warned cell was claimed by 2+ locations** (762
claimed twice, 106 three times, 5 four times = 873 squares), and every cell
claimed exactly once was silent (2,746). **Vanilla never overlaps** — all 948
LCEC cells in Skyrim.esm belong to exactly one location.

The cause is `_marker_cells` giving each marker a 3x3 block so neighbouring
markers collide (`TES4LeafrotCaveLocation` and `TES4GarnetCampLocation` both
claiming 44,-13, and so on).

Fix in [locations.py](../../tes5_import/base/locations.py): resolve cell ownership in a
pre-pass before any LCTN is written. A marker's OWN square is claimed first, then
the surrounding ring fills only squares still unowned; ties break on marker
FormID so the output stays byte-reproducible. `LCEC` and `grid_to_location` are
then both emitted from that one resolved map, which is what keeps a cell's XLCN
and its location's LCEC pointing at each other.

### The LCTN reference arrays (bucket 16, and half of 14)

Separately, LCTN needs the arrays listing what belongs to it:

    LCPR  12B/entry   Ref, World/Cell, Grid Y, Grid X
                      every PERSISTENT ref whose XLCN names this location
    LCSR  16B/entry   Loc Ref Type, Ref, World/Cell, Grid Y, Grid X
                      every ref carrying XLRT (map markers), keyed by type

Vanilla writes the `LC*` "master" arrays; `AC*`/`RC*` are save-game deltas and a
plugin has no business emitting them (Skyrim.esm: `LCSR` on 572 of 638 records,
`LCPR` on 289, `LCUN` on 121, and **zero** `AC*`/`RC*` anywhere). An earlier
draft of this audit named the wrong family.

Strides confirmed by GCD over every vanilla payload — **LCSR 16, LCPR 12,
LCUN 12**. A 12-byte read of LCSR looks plausible on the first entry and
desyncs after it.

World/Cell + grid follow one rule, with zero exceptions across 16,093 vanilla
entries:

- **interior cell** → World/Cell = the CELL FormID, grid = `0x7FFF, 0x7FFF`
- **exterior** → World/Cell = the WRLD FormID, grid = the cell's real (X, Y)

`PluginWriter._fill_location_refs` derives both from the already serialized
CELL/WRLD bytes rather than from `locations.py`, which runs before any
reference exists and in the parent process only (cell conversion is
multiprocess). Entries are sorted for byte-reproducibility and the pass is
idempotent.

**Result: 15,333 → 0.** Confirmed in the CK.

#### The persistent dummy cell trap (4,252 lines, self-inflicted)

The first version of these arrays filed refs under whatever cell record
contained them. That surfaced two NEW warnings the log had never shown —
3,447 "Parent space 'W' for ref 'X' currently has the ref under a different
cell key" and 805 "... is a not a valid cell" — because every worldspace keeps
its persistent refs in one **persistent dummy cell** (record flag 0x400, DATA
not-interior, `XCLC` absent or a meaningless 0,0). Naming that cell is wrong
twice over: the grid disagrees with the ref's real position, and on the dummies
with no XCLC at all there is no valid grid to name.

**Vanilla names a persistent dummy cell in ZERO of its 5,596 LCPR entries.**
The World/Cell and grid columns move together, with no third form:

| Cell kind | World/Cell | Grid |
|---|---|---|
| interior | the CELL | `0x7FFF, 0x7FFF` sentinel |
| exterior | the WRLD | real coordinates |

So an exterior ref is always addressed by worldspace + its OWN position, taken
from the containing cell when that cell has real coordinates and from the
reference's own DATA position when it does not (`_ref_position`).
