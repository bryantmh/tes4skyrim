# script_convert/ - TES4 script to Papyrus

**Code:** `script_convert/converter.py`, `tes5_import/constants.py`, `script_convert/tes5/blocks.py`, `tes5_import/record_types/actors.py`

## Contents

- [Papyrus / Script Conversion Notes](#papyrus-script-conversion-notes)
- [Language mapping basics](#language-mapping-basics)
- [Paired on/off commands — the asymmetric-map trap](#paired-onoff-commands-asymmetric-map)
- [Skyrim has GMST readers but no GMST writer (2026-07-31)](#skyrim-has-gmst-readers-but)
- [Silent mis-conversion — the unmarked loss](#silent-mis-conversion-unmarked-loss)
- [Event / timer conversion](#event-timer-conversion)
- [Magic / condition helpers](#magic-condition-helpers)
- [Reaching 100% compile (2026-07-28, 42 → 0 failures)](#reaching-100-compile)
- [Syntax traps found via Nehrim (2026-07-20, 50.5% → 98.4% compile rate)](#syntax-traps)
- [OBSE constructs (Nehrim depends on these heavily)](#obse-constructs)
- [Scripts on placed references](#scripts-placed-references)
- [Actor promotion must follow the DECLARING type, not the "feels like an actor" test (2026-08-18)](#actor-promotion-must-follow-declaring)
- [StopQuest converts to Stop() — a "run bit" global does NOT work (2026-08-19)](#stopquest-converts-stop-run-bit)
- [The SCRO table outranks the script TEXT (2026-08-22)](#scro-table-outranks-script-text)
- [Zero-argument commands must be ROUTED or they survive undefined](#zero-argument-commands-must-be)
- [A raw FormID in a FORM-ARGUMENT slot is never a numeric literal](#raw-formid-form-argument-slot)
- [TES4's destroyed flag has no Papyrus READER — mirror it in a FormList (2026-08-27)](#tes4s-destroyed-flag-has-no)
- [Closing an Oblivion gate is the destroyed FLAG and nothing else (2026-08-27)](#closing-oblivion-gate-destroyed-flag)
- [Script conversion: known defects found during the parse-tree rewrite](#script-conversion-known-defects)
- [1. Cross-plugin script types are a BUILD-ORDER dependency (measured 2026-08-28)](#1-cross-plugin-script-types)
- [2. Shadowed command handlers in _emit_function (measured, pre-existing)](#2-shadowed-command-handlers-emitfunction)
- [3. Two latent scanner bugs — both FIXED in stage 3 (measured 2026-08-28)](#3-two-latent-scanner-bugs)
- [4. Authored typos in source scripts (measured, not our bug)](#4-authored-typos-source-scripts)
- [5. Divergent block scanners in the repair passes — FIXED in stage 4 (measured 2026-08-28)](#5-divergent-block-scanners-repair)
- [6. Two divergent boolean-function lists (measured 2026-08-28)](#6-two-divergent-boolean-function)
- [7. this → Self substitution leaked INTO string literals — FIXED by the tree emitter (2026-08-28)](#7-this-self-substitution-leaked)
- [8. A local variable named like a built-in was shadowed by the FUNCTION — FIXED by the tree emitter (2026-08-28)](#8-local-variable-named-like)
- [9. SetPos <axis>, <value> wrote the WRONG AXIS — FIXED (2026-08-28)](#9-setpos-axis-value-wrote)
- [10. GetLOS was listed as taking no arguments — FIXED (2026-08-28)](#10-getlos-was-listed-as)
- [11. Multi-button MessageBox degraded to a plain text box — FIXED (2026-08-28)](#11-multi-button-messagebox-degraded)
- [12. pms <shader>, <n> created a second, unbound property — FIXED (2026-08-28)](#12-pms-shader-n-created)
- [13. Twelve commands were treated as unknown by the node path — FIXED (2026-08-28)](#13-twelve-commands-were-treated)
- [14. GetDayOfWeek had two conversions and the worse one won — FIXED (2026-08-28)](#14-getdayofweek-had-two-conversions)
- [15. FUNCTION_MAP silently drops 20 entries — LATENT (2026-08-28)](#15-functionmap-silently-drops-20)
- [16. Two disagreeing lists of Bool-returning Papyrus names — FIXED (2026-08-28)](#16-two-disagreeing-lists-bool)
- [17. Type coercion guessed from emitted text — REPLACED (2026-08-28)](#17-type-coercion-guessed-from)
- [18. Operator precedence encoded twice, and the copies disagreed — LATENT (2026-08-29)](#18-operator-precedence-encoded-twice)
- [19. Activate drops its arguments when the caller passes nodes — LATENT (2026-08-29)](#19-activate-drops-its-arguments)
- [20. Feature flags scanned from raw source matched COMMENTS — FIXED (2026-08-29)](#20-feature-flags-scanned-from)
- [21. Set X to <literal> dropped when the block filter was unconvertible — FIXED](#21-set-x-literal-dropped)
- [22. If True where one && term had no equivalent — FIXED](#22-if-true-where-one)
- [23. Sentence spacing stripped from message text — FIXED](#23-sentence-spacing-stripped-from)
- [24. setDestroyed 0 and setDestroyed 1 both destroyed — FIXED](#24-setdestroyed-0-setdestroyed-1)
- [25. Three converter regressions in the parse-tree rewrite — FIXED](#25-three-converter-regressions-parse)
- [script_convert: measurements and failure modes](#scriptconvert-measurements-failure-modes)
- [7. Why it is this way — the failure modes to not repeat](#7-why-this-way-failure)
- [TES4 Script → Papyrus Conversion Plan](#tes4-script-papyrus-conversion-plan)
- [Scope](#scope)
- [Architecture](#architecture)
- [Step-by-Step Implementation Plan](#step-step-implementation-plan)
- [Conversion Quality Tiers](#conversion-quality-tiers)
- [Testing Strategy](#testing-strategy)
- [Known Limitations](#known-limitations)
- [Creation Kit Papyrus Compiler Contracts (2026-07-12)](#creation-kit-papyrus-compiler-contracts)

## Papyrus / Script Conversion Notes
<a id="papyrus-script-conversion-notes"></a>

Linked from [CLAUDE.md](../../CLAUDE.md). TES4 script → Papyrus conversion
learnings. Implemented in `script_convert/`. For the original scope analysis and
record counts see [Script_Conversion_Plan.md](script_convert.md).

## Language mapping basics
<a id="language-mapping-basics"></a>

TES4 uses an imperative scripting language with event blocks (GameMode,
OnActivate, …). TES5 uses Papyrus, an object-oriented language.

- Variables become Properties: `short myVar` → `Int Property myVar Auto`
- Event blocks change: `begin OnActivate` → `Event OnActivate(ObjectReference akActionRef)`
- Functions change: `Message "text"` → `Debug.Notification("text")`
- TES4 `set x to y` → `x = y`
- Player reference: `player.` → `Game.GetPlayer().`
- No direct equivalent for: GetInCell (→IsInLocation), ShowMap, CloseOblivionGate, SetQuestObject
- TES4 attributes (Strength, etc.) have no Papyrus equivalent — reads are
  stubbed open and writes dropped, never aliased onto a look-alike actor value
  (see "Skyrim has NO attributes" below)
- Vanilla forms with no TES4 counterpart are reached via
  `Game.GetFormFromFile(0x..., "Skyrim.esm")` in TES4Polyfill (ActorTypeNPC
  keyword for GetIsCreature, GuardDialogueFaction for IsGuard,
  PlayerVampireQuestScript.VampireStatus for HasVampireFed) — no property
  binding needed.

**Vanilla Papyrus has more than the wikis suggest** — check the game's
`Data/Source/Scripts/*.psc` headers before declaring something unconvertible.
`Faction.SetReaction/ModReaction`, `Actor.GetCurrentPackage()` (→
GetIsCurrentPackage/GetCurrentAIPackage-vs-form),
`ObjectReference.PushActorAway` and
`ObjectReference.GetAnimationVariableBool("bAnimPlaying")` (→ IsAnimPlaying) all
exist and are used by the converter.

## Paired on/off commands — the asymmetric-map trap
<a id="paired-onoff-commands-asymmetric-map"></a>

**A `;NE:` (no-equivalent) comment on ONE HALF of a paired on/off command is a
latent soft-lock, not a cosmetic gap.** When the "on" half converts to a
state-changing call and the "off" half is a no-op, the actor can never return to
the original state. Audit the partner call before accepting either.

- **`SetAlert` is NATIVE in both games (`Actor.SetAlert(bool)`) — never
  approximate it with `DrawWeapon()`.** Oblivion's SetAlert sets the AI
  combat-READINESS flag; the engine clears it and it does NOT suppress dialogue.
  The old mapping sent `SetAlert 1`→`DrawWeapon()` while `SetAlert 0` was a
  silent no-op, so any actor alerted for a scripted ambush drew a weapon and
  NEVER stood down. CharacterGen alerts Uriel for the prison-cell ambush (stage
  15) and clears it at stages 17/24 to run the conversation — converted Uriel
  stood weapon-drawn, could not force-greet, and the intro SOFT-LOCKED with
  player controls disabled. 97 scripts across the game use SetAlert, most in
  talking scenes (MQ13/MQ14 Bruma, SE06 battle, MS13), not fights.

- **`ResetFallDamageTimer` (2026-07-31)** was a `;NE:` no-op with no "on" half
  at all, so a levitation/flight effect converted to a spell that dropped the
  player to their death. Skyrim keeps the console command (opcode 4404) but
  binds no Papyrus equivalent, and `fJumpFallHeightMin` has readers but no
  vanilla writer. It now calls `TES4Polyfill.SuppressFallDamage()`, and the
  converter **injects the paired `RestoreFallDamage()` into the teardown
  event** — synthesizing an `OnEffectFinish` when the script has none, so the
  suppression can never outlive the effect. The injection is a post-pass run
  after the synthesized `OnInit`/`OnUpdate` are appended, because TES4 does not
  order its blocks and the teardown event must already be in the output for the
  restore to land inside it. `SetGhost`/`SetInvulnerable` were rejected as the
  mechanism: both suppress ALL damage, so the scroll would grant temporary
  immortality — a worse defect than the one being fixed.

## Skyrim has GMST readers but no GMST writer (2026-07-31)
<a id="skyrim-has-gmst-readers-but"></a>

`Game.GetGameSettingFloat/Int/String` are vanilla natives. **`Game.SetGameSetting*`
is SKSE-only and does NOT compile against the vanilla headers this pipeline
builds with** — verified directly against `papyrus.exe`: a script calling the
setter fails with "undefined function `SetGameSettingFloat`" on the same line
the getter resolves fine.

So OBSE's `SetNumericGameSetting` cannot convert literally. The settings that
have a per-actor equivalent go through `Actor.ForceActorValue` instead
(`_GMST_TO_ACTOR_VALUE` in `script_convert/converter.py`) — same observable
change, scoped to the actor rather than the world, which is what these scripts
actually want. Two rules fall out:

- **The READ must use the same channel as the WRITE.** These scripts all use the
  save/restore idiom ("remember the old value, set a new one, put it back"); if
  the getter still goes to the global GMST it reads back a number the write
  never changed, and the restore writes garbage. `GetGameSetting` is redirected
  to `GetActorValue` for exactly the settings in the table.
- **`fJumpHeightMax` does not exist in Skyrim** — only `fJumpHeightMin`.
  Confirmed against both Skyrim.esm's GMST records and the SkyrimSE.exe settings
  strings. A TES4 script that sets both is writing one real setting and one
  Oblivion had that Skyrim dropped; the second write is a harmless no-op.

Settings with no actor-value equivalent keep a `;TODO` marker — a call that
compiles and silently does nothing is the dangerous outcome, not the honest one.

## Silent mis-conversion — the unmarked loss
<a id="silent-mis-conversion-unmarked-loss"></a>

**A `;NE:`/`;TODO:` marker is the HEALTHY failure. The dangerous conversions are
the ones that emit a plausible call which compiles, runs, and does nothing.**
Audited output carries only 2 `;TODO:` markers across 18,566 scripts, so marker
counts measure honesty, not correctness — never treat a clean output scan as
evidence the conversion is complete.

### 🔴 An `as Actor` cast on a non-actor INVERTS every guard around it (2026-08-09)

**`(Self as Actor)` on an ObjectReference is `None` at runtime — and Papyrus
does not stop there. It aborts the call and substitutes `0` for the result**,
so a distance test built on it silently flips to always-true.

`MS48OblivionGateScript` rides on an ACTI (the Oblivion gate). TES4:

```
if getdistance player < 8000
    if MQ00.nearOblivionGate == 0 && getdistance player < 1000
        forceweather OblivionStormTamriel 1
```

emitted as `If (Self as Actor).GetDistance(Player) < 1000`. The gate is not an
actor, so the cast is None, the call aborts, the comparison reads `0 < 1000`,
and the gate hammered `OblivionStormTamriel.ForceActive()` **every 0.1s** while
the player crossed into the Plane of Oblivion. Papyrus.0.log shows the
signature 34x in the two seconds before the CTD:

```
error: Cannot call getDistance() on a None object, aborting function call
warning: Assigning None to a non-object variable named "::temp5"
```

Cause: `_ACTOR_ONLY_FUNCTIONS` and `_OBJREF_SHARED_FUNCTIONS` deliberately
overlap — 14 entries are declared on BOTH Actor and ObjectReference. The
ref'd-receiver site in `converter.py` subtracts the shared set; the
**implicit-Self site did not**, so any bare call to an overlapping function on
a non-actor script got the cast. Blast radius before the fix: **383 calls in
101 scripts** (137 GetDistance, 115 GetItemCount, 115 AddItem, 11 RemoveItem,
4 RemoveAllItems, 1 SetAlpha).

Rule: **every site that consults `_ACTOR_ONLY_FUNCTIONS` must subtract
`_OBJREF_SHARED_FUNCTIONS`.** Note `saa`/`setactoralpha` are deliberately NOT
in the shared set — their documented `(Self as Actor).SetAlpha()` degradation
is intentional and must survive. Guarded by
`tests/test_script_converter.py::TestObjRefSharedFunctionsNeverCastToActor`,
which asserts the invariant across the whole overlap, not just GetDistance.

This is the archetypal silent mis-conversion: it compiles, it is plausible, and
it does the opposite of the original.

### The two stages build DIFFERENT CrossRefGraphs (2026-07-31)

A conversion decision that depends on the graph can come out **differently in
the script stage than in the import stage**, because they do not share a graph:

| Stage | Writes | Builds its graph via |
|---|---|---|
| `--scripts-only` | the `.psc` / `.pex` | `CrossRefGraph.load_from_export()` (the parallel scan) |
| `--import-only` | the **VMAD property bindings** | hand-rolled loop over `all_records` in `import_main` |

`_resolve_props` re-runs the *whole converter* over the source to learn which
properties to bind. If the import's hand-built graph is missing a field the
scan collects, the converter takes a different branch there — and you get a
`.psc` that reads properties the VMAD never declares. They are `None` at
runtime: **as dead as whatever they replaced, while looking fixed in the
source.**

Found via R9-1 (`GetCurrentAIPackage`): the new `pack_type`/`actor_packages`
indexes existed only in the scan, so `TES4_MG17Script.psc` referenced six
`Package` properties and its VMAD declared 25 properties, none of them packages.

**Rule: anything added to `_scan_record_lines` must be mirrored into
`import_main`'s hand-built graph.** Verify with `tools/script/vmad_probe.py <esm>
<script> --props` — compare the bound set against the `.psc` declarations, and
never assume a correct-looking `.psc` means the binding happened.

### `extends` must be a base type EVERY attaching record can bind (2026-07-31)

The quietest failure in the pipeline so far. Papyrus binds a script to a form
only when the declared base type matches; a mismatch is rejected outright and
**nothing in the script runs** — no events, no poll, no properties:

```
error: Unable to bind script TES4_GoblinHeadScript to (1A08564B)
       because their base types do not match
```

It is invisible to every static check. `Actor extends ObjectReference`, so an
`extends Actor` script on a WEAP/ACTI/CONT/DOOR still **compiles cleanly** —
all 15,959 compiles passed while 67 scripts were dead in-game. The only place
it shows up is the Papyrus log.

Two independent sources of a wrong base type, both fixed in round 7 of the
quest-script audit ([quest_script_conversion.md](../audits/quest_script_conversion.md), R7-1):

* **`_infer_extends` overriding a correct answer.** `get_extends_class` derives
  the type from the attaching record's signature and is right; the bare-call
  pre-scan then upgraded 88 non-actor scripts to `Actor`. Its function set
  shared 14 entries with `_OBJREF_SHARED_FUNCTIONS` (`GetDistance` alone hit
  101 scripts), it matched inside comments and string literals, it matched
  locals *named* like functions, and it matched actor-only calls inside
  `OnEquipped`-style events whose subject is the passed-in actor, not the item.
* **A script shared between an actor and a non-actor record.**
  `NoActivationScript` sits on both a DOOR and an NPC_. The scan returned on
  the first actor attachment, so every DOOR copy was unbound — and its empty
  `OnActivate`, which exists purely to *consume* the activation, never ran.
  The base type must now be one all attachments can bind.

Rules that follow:

* **Never widen `extends` for convenience.** It is not a cosmetic type change;
  it decides whether the script exists at runtime.
* **`_ACTOR_ONLY_FUNCTIONS` is not a sound test for "is this an actor".** It
  lists methods `ObjectReference` also declares — that is why
  `_OBJREF_SHARED_FUNCTIONS` exists (see also R3-5's `PlaceAtMe` trap). Any
  new consumer of it must subtract that set.
* **Read the Papyrus log for bind errors after a script-side change.**
  `grep -c "Unable to bind script"` is a one-line health check that no amount
  of compiling or record inspection replaces.

### GameHour is FLOAT — never truncate a global read (2026-07-28)

`GameHour` is FormID `0x00000038` in **both** games, and Skyrim declares it
**float** (`GLOB.FNAM=102`), so `GetValue()` returns fractional hours — 23.9847,
not 23. Oblivion mislabels it `short` in its own GLOB record but the engine still
reports the fraction, which is why the bell/chime idiom works there:

```
if ( GameHour >= 23.98 ) || ( GameHour <= 0.02 )   ; the top of the hour
```

Emitting `GameHour.GetValue() as Int` truncated that, and every such window
collapsed into an **always-true whole-hour test** (`23 >= 23.98` is false, but
`0 <= 0.02` is true for all of hour 0). The guarded body then ran every frame:
the Erodans-Kapelle chapel bell and Oblivion's `BellTowerScript` rang
continuously instead of once on the hour. 157 comparisons across 7 scripts in
both plugins.

- `_global_read()` decides the cast from the GLOB's real `FNAM.Type` (now carried
  by `CrossRefGraph.global_types`), plus an explicit `_FRACTIONAL_ENGINE_GLOBALS`
  set for engine globals Oblivion mislabels. `TimeScale` really is short
  (Skyrim `FNAM=115`) — do NOT add it.
- Assignments into Int variables still get their cast from
  `_coerce_float_to_int` (`GetValue` is in `_FLOAT_RETURNING_FUNCS`), so
  `currenthour = GameHour` remains correct.
- **General rule: a blanket cast on a global read is a silent behaviour change.**
  Type the cast from the record, not from the call site.

### The chime latch is REAL seconds vs a GAME-hour window (2026-07-30)

The `as Int` fix above was necessary but **not sufficient** — the bell still rang
on repeat in Nehrim. The guard was fixed; the *latch* was not.

The idiom is a one-shot latch. The GameHour window sets `soundplaying = 1`, and a
countdown holds the latch until it passes a negative sentinel:

```
if ( soundplaying == 1 )
    set timer to ( timer - GetSecondsPassed )
    if ( timer <= -5 )              ; REAL seconds
        set soundplaying to 0
```

The window is measured in **game hours**; the sentinel in **real seconds**. The
two only stay in step at the TimeScale the author used:

| TimeScale | 1 game hour | 0.04gh window | 5s latch | rings/hour |
|---|---|---|---|---|
| 30 (Oblivion) | 120s | **4.8s** | outlasts it | 1 ✓ |
| 10 (**Nehrim**) | 360s | **14.4s** | expires 2× *inside* the window | 3 ✗ |
| 5 | 720s | 28.8s | expires 5× inside | 6 ✗ |

Nehrim ships `TimeScale = 10` (GLOB `0x3A`; Oblivion ships 30), so the latch
clears while `GameHour` is *still* inside the window and immediately re-fires.
**This is not a Papyrus artifact** — Oblivion's own interpreter rings 3× per hour
at TimeScale 10. The scripts were only ever correct at the author's TimeScale.

- `_scaled_debounce_seconds()` widens the sentinel to `window * 1.25` whenever
  the authored value no longer outlasts the window, and **returns it untouched
  when it already does** — so TimeScale-30 output is byte-identical (verified:
  `TES4_BellTowerScript.psc` diffs clean, 0 Oblivion scripts widened).
- Gated on `_uses_hour_window` (the `GameHour >= X.98` idiom) so ordinary timers
  keep their authored durations; `_LATCH_EXPIRY_RE` only matches a `<= -N`
  sentinel, never `<= 0` or a `>=` test.
- `CrossRefGraph.global_values` now carries GLOB `FLTV` values, so the converter
  can read the plugin's *own* TimeScale rather than assuming 30.
- Measured with `temp/bell_sim.py`: Nehrim 3.0 -> 1.0 rings/game-hour, Oblivion
  unchanged at 1.0.
- **General rule: a TES4 constant in real seconds that gates on game time is
  only valid at the author's TimeScale.** Scale it, don't copy it.
- **This was NOT the cause of the reported "bells on infinite repeat."** It is a
  real defect and the fix stands, but the chapel bell had a separate cause — see
  the next section. Do not re-litigate the latch when a bell repeats.

### `Begin OnTrigger` is PER-FRAME, not on-entry (2026-07-30)

The chapel bell that "tolls ~12 times, breaks briefly, then tolls again forever"
is **not** `SoundZoneKapelleGlockenScript` at all, and not a sound-record loop
(`SNDX.Flags=0`, emitted `LNAM=00000000` — verified in the built ESM).

Two facts have to land together:

1. **`fx\nehrim\kapelleglocke.wav` is 15.46 s long and contains a full peal of
   ~12 tolls.** "12 tolls" is ONE `Play()` call, not twelve. Measure the asset
   before treating a count of anything as a loop count.
2. The bell is rung by nine `Magieverbot*` (magic-ban) scripts, not the chapel
   script — `AAKapelleGlocken` is referenced by **10** Nehrim scripts. It is an
   *alarm* bell for casting inside Erothin's no-magic zone.

Those scripts are `Begin OnTrigger Player` blocks, and **TES4 runs an
`OnTrigger` block every frame the object is inside the volume.** The block's own
code proves it: it counts `frame >= 25` and `frame >= 100` *executions* as a
cooldown. Converting it to Papyrus `OnTriggerEnter` — which fires once, on entry
— froze the state machine on `counter == 1`, so the 100-frame cooldown never
ran and the alarm re-fired on every re-evaluation.

- Skyrim keeps the same three-way split, and all three are distinct engine
  events (`OnTrigger`, `OnTriggerEnter`, `OnTriggerLeave` each appear once,
  NUL-terminated, in `SkyrimSE.exe`). `ObjectReference.psc` documents
  `OnTrigger` as "a trigger is tripped" versus "volume is entered/left".
- `TES4_BLOCK_MAP` now sends `ontrigger`, `ontriggeractor` and `ontriggermob`
  to `Event OnTrigger`. The latter two differ only in *what* trips them, not in
  edge-vs-repeat; Skyrim has no actor/creature split, so that filter stays in
  the body.
- Scope: **504 blocks** (Nehrim 317, Oblivion 187); 79 of them keep a
  per-execution counter and were therefore hard-frozen. Verified no script
  declares two blocks that would now collide into a duplicate `Event OnTrigger`
  (0 in both plugins), and both plugins compile clean.
- **General rule: check whether a TES4 block is edge-triggered or per-frame
  before picking the Papyrus event.** A body that counts its own executions is
  proof of per-frame.
- This is a real defect and the fix stands, but it was **not** the cause of the
  looping chapel bell either. See below.

#### …but the ENTRY frame still has to fire — emit BOTH (2026-08-05, in-game confirmed)

Keeping the body on `OnTrigger` is right, but it is not sufficient: **Skyrim does
not deliver `OnTrigger` for a fast crossing**, which is exactly what walking over
a tripwire or a pressure plate is. Stepping on the Vilverin plate did nothing at
all — the body never ran once.

The vanilla census is unanimous and settles it: `Tripwire.pex`,
`PressurePlate.pex`, `TrapTriggerBase.pex` and `TrapTriggerHinge.pex` **all**
define `OnTriggerEnter`, and vanilla's own `Tripwire` does **not** define
`OnTrigger` at all. (Read them with `skyrim_assets.get_asset_bytes('scripts/
<Name>.pex')` and grep the string table for `On*` — the .psc sources are not
shipped.)

So a converted `begin OnTrigger` block now emits **both** events: the body stays
in `Event OnTrigger` (repeat semantics preserved, Magieverbot counters still
work) and a generated `Event OnTriggerEnter` delegates to it for the crossing
frame:

```papyrus
Event OnTriggerEnter(ObjectReference akActionRef)
  OnTrigger(akActionRef)
EndEvent
```

- Scope: **187 Oblivion scripts**. Skipped when the script authors its own
  `OnTriggerEnter` block (Papyrus allows one definition per event; 0 Oblivion
  scripts do, but a third-party plugin may).
- **Do not "simplify" this back to a single event.** Remapping to
  `OnTriggerEnter` alone re-freezes the per-frame counters above; leaving it on
  `OnTrigger` alone means trap triggers never fire. Both are required.

### Physical-trap damage: TES4's ENGINE read the script's variables (2026-08-09, in-game confirmed)

A converted swinging mace, swinging log, falling log or cave-in fired, swung
and connected — and dealt **zero damage**. Nothing in the TES4 script explains
it, because **the damage is not in the script**: Oblivion's engine dealt it.

When a Havok body on layer 14 (`OL_TRAP`) struck an actor, TES4 read three
magic variables off the striking object's script and applied
`fTrapDamage + fLevelledDamage × victimLevel` damage plus `fTrapPushBack`:

| TES4 script variable | Meaning |
|---|---|
| `fTrapDamage` | flat damage |
| `fLevelledDamage` | per-victim-level damage coefficient |
| `fTrapPushBack` | knockback impulse |
| `fTrapMinVelocity` | contact speed floor (NOT converted — see below) |
| `bTrapContinuous` | re-hit while in contact (NOT converted) |

The names are a **convention the engine keys on**, not ordinary locals — the
script body never assigns damage anywhere. `CTrapSwingMace01SCRIPT` sets
`fTrapDamage 20 / fLevelledDamage 1.5` on activation, which is exactly UESP's
documented "20 + 1.5 × level" for the swinging mace; the swinging log's 15 and
the falling logs' 30 match their scripts the same way. Census:
`fTrapDamage` appears in **226 Oblivion and 127 Nehrim** scripts.

**Skyrim keeps the detection but moves the damage into the script.** The
layer-14 contact still fires — it arrives as the `OnTrapHitStart` script event
— and vanilla answers it in `TrapHitBase.psc` with the native
`ObjectReference.ProcessTrapHit`. So the conversion mirrors vanilla's contract:
every converted `ObjectReference`/`Actor` script that DECLARES `fTrapDamage`
gets a synthesized handler.

```papyrus
Event OnTrapHitStart(ObjectReference akTarget, float afXVel, float afYVel, \
    float afZVel, float afXPos, float afYPos, float afZPos, int aeMaterial, \
    bool abInitialHit, int aeMotionType)
  Actor victim = akTarget as Actor
  If victim == None
    Return
  EndIf
  Float totalDamage = fTrapDamage + fLevelledDamage * victim.GetLevel()
  If totalDamage <= 0.0
    Return   ; not armed yet - TES4 variables start at 0
  EndIf
  akTarget.ProcessTrapHit(Self, totalDamage, fTrapPushBack, afXVel, afYVel, \
      afZVel, afXPos, afYPos, afZPos, aeMaterial, 0.0)
EndEvent
```

- **Read the variables LIVE, never bake the numbers in.** Doing so reproduces
  the whole authored lifecycle for free: the mace script leaves `fTrapDamage`
  at 0 while the trap is armed and held (so brushing it is harmless), sets 20
  on release, and drops it to 5 six seconds later. The `<= 0.0` guard is what
  makes the held phase safe, and it is why an un-triggered trap does nothing.
- Only `fLevelledDamage`/`fTrapPushBack` that the script actually declares are
  referenced; a script with `fTrapDamage` alone emits the flat term only.
- Scope: **64 Oblivion + 33 Nehrim** scripts (maces, swinging/falling logs,
  cave-ins, spike pits, blades, gas emitters).
- `fTrapMinVelocity` and `bTrapContinuous` are deliberately **not** converted.
  The event's velocity units are unverified, and gating on a wrong threshold
  silences all damage — the exact failure being fixed. `OnTrapHitStart` fires
  per contact-start rather than per frame, which already approximates the
  non-continuous case.
- The collision side needed no change: `_remap_world_filter` passes authored
  layer 14 straight through, and vanilla agrees — `trapmace01`'s striking mace
  head is layer 14 while its chain links are layer 10.
- **General rule: when a TES4 feature has no code behind it, suspect an engine
  convention keyed on variable names.** Grepping the script for "damage" finds
  nothing; the census of variable NAMES across all scripts is what exposes it.
- Pinned by `tests/test_script_converter.py::TestPhysicalTrapDamage` — including
  the two cases that would not compile if the emission were naive: a script
  declaring `fTrapDamage` alone must not reference the variables it lacks, and a
  Quest script must get no handler at all (`OnTrapHitStart` is an
  `ObjectReference` event).

### Engine globals must bind UNSHIFTED (2026-07-30) — the actual bell bug

**Root cause of the endlessly-looping chapel bell**, found in `Papyrus.0.log`
after two wrong theories (the TimeScale latch and `OnTrigger`, both above):

```
error: Property Gamehour on script TES4_SoundZoneKapelleGlockenScript
attached to (1A20DD0F) cannot be bound because <nullptr form> (1A000038)
is not the right type
error: Cannot call GetValue() on a None object, aborting function call
	[ (1A20DD0F)].TES4_SoundZoneKapelleGlockenScript.OnUpdate()
```

`convert_GLOB` deliberately drops the engine-owned globals (`GameHour`,
`TimeScale`, …) because Skyrim already ships them — and at the **same FormIDs
Oblivion uses** (`GameYear 0x35` … `TimeScale 0x3A`, verified in both GLOB
dumps). But the VMAD property binders still ran those FormIDs through the
load-order remap, producing `1A000038` — a form that does not exist. The
property bound to **None**, so `GameHour.GetValue()` returned **0.0 forever**,
which is permanently inside every `GameHour <= 0.02` hour-boundary window. The
bell re-fired on a continuous loop.

The `constants.py` comment claimed these references "are canonicalized to the
vanilla forms by script_convert (`_GLOBAL_CANONICAL`)". That was **false** —
`_GLOBAL_CANONICAL` only canonicalizes the *name*; nothing ever fixed the
FormID, and `_ENGINE_GLOBALS` had exactly one use (dropping the record). A
documented mechanism that does not exist in the source is worse than none.

- `constants.ENGINE_GLOBAL_FORMIDS` maps the six engine globals to their vanilla
  FormIDs. All three VMAD binders now bind them unshifted, exactly like Player
  (`0x14`): `object_scripts._resolve_props`,
  `dialog_converter._build_info_script_properties`, and
  `dialog_converter._collect_scro_properties` (the SCRO path shifted them too).
- Scope: **338 bindings** repaired (Nehrim 113, Oblivion 225); 0 remain shifted
  in either plugin.
- **Why "~12 chimes" is not a bug**: `fx\nehrim\kapelleglocke.wav` is a 15.46 s
  recording of exactly 12 evenly-spaced strikes (1.24 s apart, measured). Nehrim
  has only one bell asset and the script never counts hours — it plays the same
  12-strike file at every hour. That is vanilla Nehrim behaviour, not a
  conversion artifact. Making it strike the hour would be a redesign.
- **General rule: any FormID shared with the engine must skip the load-order
  remap.** Player was special-cased; the globals were not. When a property reads
  None in-game, check the Papyrus log for the binding error *first* — it names
  the exact FormID and costs one grep, versus days of modelling script logic.

### Synthesized records were unbound on object scripts (2026-07-31)

Same failure mode as the engine-globals bug above — a property binding to
**None** — from the opposite cause, and it survived that fix because it lives on
a different code path.

The converter mints properties for records that exist only in the OUTPUT:
`TES4Fame`, `TES4Infamy`, `TES4GoldFenced`, `TES4CyrodiilCrimeFaction`, and the
`TES4Unlock_*` topic gates. `object_scripts._resolve_props` binds properties
through `resolve_property_formid()` → `xref.edid_to_formid`, which is built
**from the TES4 export** and therefore can never contain a synthesized record.
Every one silently resolved to nothing.

Only the **object-script** binder was affected: `dialog_converter` already
injects the same registry as `well_known_props`, so `QF_*`/`TIF_*` fragments
bound correctly — which is why a verification counting the 4,762 *dialogue*
bindings reported all-clear while every object script was broken.

- `import_main.get_well_known_properties()` exposes the registry (an accessor,
  not a direct import, because `import_main` imports `object_scripts`).
  `_resolve_props` consults it before falling through to the EditorID lookup.
- Worst case found: `TGStolenGoodsScript`, the **Thieves Guild rank driver** —
  all ten of its gates read `TES4GoldFenced.GetValue()`, so a None property
  threw on the first tick and no TG rank ever advanced.
- **General rule: a record the importer synthesizes needs an explicit binding
  route in EVERY VMAD binder.** The export-derived EditorID map cannot see it.
  Check with `python tools/script/vmad_probe.py <esm> <script> --props` — a property the
  `.psc` declares but the probe does not list is unbound.

### An early `return` killed the OnUpdate poll (2026-07-31)

TES4 `return` ends only **this frame's** `GameMode` pass; the script runs again
next frame. The converted `OnUpdate` is one-shot and self-rescheduling, so a
`Return` that falls past the trailing `RegisterForSingleUpdate` stops the script
**for the rest of the game**.

`if GetStage X < N / return` is a standard Oblivion early-out, so this was
widespread: **115 Returns across 96 scripts**, including quest drivers
(`MG01`/`MG02`/`MG05`/`MG06`/`MG08`/`MG12`/`MG17`/`MG18`, `MQ16Script`,
`MS04`/`MS09`/`MS14`). `MG05RockScript` fires one shock bolt per tick and uses
`return` to serialize six — it fired exactly one bolt, ever.

The poll is armed at three places, each for a different reason (2026-08-16):

* **top of `OnUpdate`: `RegisterForSingleUpdate(5.0)` — abort insurance
  ONLY.** A runtime error mid-body aborts the event; without this the poll
  died for the rest of the game. 🛑 It must NOT be the real interval:
  `RegisterForSingleUpdate` counts from *now*, so a top arm at `interval`
  starts the next pass `interval` after this one **started**, and a pass whose
  body takes longer than that (MQ01Script's tutorial poll does ~15 latent
  natives per 0.1s tick) overlaps itself; each overlap slows the VM, and the
  pile grows without bound. Measured in game (Papyrus stack dump at the start
  of CharacterGen): **251 concurrent `TES4_MQ01Script.OnUpdate` stacks**, End
  fragments of 1–2s lines running 19–24s late, conversations with 10s+ gaps
  and repeated lines. That was the "excruciating" prison scene.
* **every TES4 `return` in the body**: `RegisterForSingleUpdate(<interval>)`
  spliced before `Return` (`_poll_return_prefix`), `Is3DLoaded()`-gated for
  object/actor scripts. A value-returning `Return <x>` (OBSE user function) is
  not touched; the `!IsRunning()` guard keeps the 5s insurance (a quest that
  is not running need not poll faster); the dialogue gate re-arms at 0.5s.
* **bottom of the body: `RegisterForSingleUpdate(<interval>)`** — the cadence,
  measured from the END of the pass, so passes never overlap.

### `begin MenuMode` — the BARE form is not the menu-ID form (2026-07-31)

The two spellings look alike and behave completely differently, and conflating
them costs real quest logic in one direction or a stage blowout in the other.

* **`begin MenuMode <id>`** fires only while that one menu is open (1014 =
  lockpicking, 1030 = class menu, 1002 = inventory). Skyrim has no per-menu
  hook, so these **must not run**: MQ01's id'd blocks `setstage MQ01 70`/`84`
  unconditionally, and merging them into the poll blew the tutorial's whole
  stage machine on the first tick, then hit stage 100's `stopquest MQ01`.
* **`begin MenuMode`** (bare) fires on *every* menu frame. Censused over
  Oblivion.esm, **not one of the 20 bare blocks is a menu-specific trigger** —
  they are all time-and-inventory bookkeeping that runs on the frames where
  GameMode does *not*, i.e. wait/sleep and the inventory screen. Several say so
  in their own comments (`ErthorScript`: *"contingency if player is
  waiting/resting"*; `SE02OrcCaptainScript` guards on `isTimePassing`).

Dropping the bare bodies deleted the **only** writer of two quest flags:
`MelisandeScript`'s `set MS40.cureready to 1` (so MS40's vampirism cure could
never be handed over) and `Dark09RetirementScript`'s `set GotFinger to 1`. Also
lost: the 7 innkeeper rent timers, 195 lines of SE37 item checks, and
`GandredhelScript`'s topic reveal.

The faithful conversion is to **merge a bare, non-sleep MenuMode body into the
GameMode poll** at its source position — in Oblivion the pair together covered
every frame, so one always-running pass reproduces the union rather than half of
it. `_has_gamemode` must account for it too, or a script whose only block is a
bare MenuMode (`SE42Script`, `DAOghmaInfiniumScript`) gets no loop at all.

Two exceptions keep their own routes: the `isPCSleeping` idiom becomes
`OnSleepStart`/`OnSleepStop`, and menu-ID blocks stay commented.

Before merging, check the bodies are safe on an ordinary frame. All 20 are
idempotent state machines gated by their own doonce/stage variables; a merged
body that reads a menu (`DAOghmaInfiniumScript`'s `getbuttonpressed`) is safe
because the read is consume-once (see the button-MessageBox section below):
until its own box has been shown and clicked it reads -1, so no branch
matches. Where a body is duplicated in both blocks (the `Publican*` rent
counter), running both in one pass still advances the hour once — the first
copy rewrites `renthour` to `GameHour`, so the second's
`(renthour + 1) < GameHour` is false.

### Button MessageBoxes become authored MESG records (2026-08-03)

TES4 builds every in-world choice menu as `MessageBox "text" "Btn1" "Btn2"`
plus a `GetButtonPressed` poll in GameMode. Skyrim has no dynamic boxes, so
for years both halves were stubbed — the box lost its buttons
(`Debug.MessageBox`) and the poll read a constant `-1` — which left ~289
menus dead across the plugins, including two that **soft-locked chargen**:
Oblivion's `CGSewerExitScript` ("Finished - Exit Sewers" is the dead
`button == 3` branch that sets MQ01 stage 88) and Morroblivion's
`mwCGCensusExitDootScript` (same shape, `fbmwChargen` stage 100).

The real conversion (`script_convert/message_menus.py` is the shared plan
both sides run):

* the **importer** writes one MESG per call site — EDID
  `TES4Msg_<Script>_<NN>`, DESC = text, one ITXT per trailing quoted string,
  DNAM bit 0 — and registers the EDIDs in `_WELL_KNOWN_PROPERTIES` so VMAD
  property binding resolves them;
* the **converter** emits `TES4_MsgButton = TES4_ShowMsg(TES4Msg_X_NN)` at
  the call site and `TES4_TakeMsgButton()` for `GetButtonPressed`.
  `TES4_ShowMsg` clears the state before `Show()` (TES4: displaying a box
  resets GetButtonPressed to -1), `Show()` parks its thread on the box, and
  the take helper returns the clicked index once, then -1 — TES4's own
  contract, which is what keeps `if button == N` polls from re-firing on a
  stale index.

Sites are matched by (text, buttons) content, not position — MenuMode merges
can reorder blocks. A `GetButtonPressed` in a script that never shows a
button box of its own (cross-script polling of TES4's global button state —
a handful of sites) still reads `-1`, explicitly dead rather than miswired.
Format specifiers inside a button-box's text (`"...%.0f Drakes?" cost "Yes"
"No"`) survive literally: MESG DESC is static text.

### A modal menu in a POLLED body must block the whole pass (2026-08-15)

`ShowBirthsignMenu` / `ShowClassMenu` convert to a `Message.Show()` chain
(`message_menus.build_chargen_menus`). TES4's chargen menus were modal to the
**entire GameMode pass**: the statement written after `ShowBirthsignMenu` did
not run until the player had chosen. Papyrus only parks *the thread that
called* `Show()`, so the poll's next tick — 0.1s later, on another thread —
re-enters the same body **while the menu is still open**.

A re-entrancy latch alone is not enough. The first form latched the menu but
let the latched-out pass **fall through to the authored tail**, which for
CharacterGen ran `setstage 44` mid-menu. Stage 44's fragment force-greets the
Emperor (`UrielSeptimRef.evp`) at a player still locked in the menu, so the
greet is evaluated and consumed with nobody able to receive it: the menu
closes onto an Emperor with nothing pending, and chargen soft-locks with the
player free-roaming mid-scene.

Verified live through the game bridge rather than by reading: driving
`setstage charactergen 43` advanced the stage to 44 **instantly** while
`TES4ChargenBirthsignChoice` was still 0 — the menu had not yet returned a
choice. That single readback is what separated this from the (superficially
identical) "menu shows twice" symptom the latch was originally added for.

So the emission is **context-dependent**:

* **Polled body** (`_current_event == 'Event OnUpdate()'`) — a latched-out
  pass `Return`s. The tick defers entirely; the pass that owns the menu runs
  the authored tail itself once `Show()` returns. Safe because the poll
  re-arms at the TOP of `OnUpdate`, so returning cannot kill the loop.
* **One-shot site** (quest-stage fragment, `OnActivate`) — keeps the
  fall-through `If !busy` form. Nothing repeating re-enters it, so the latch
  can only trip on a genuine race, and there a `Return` would **drop** the
  authored tail rather than defer it. CharacterGen stage 87 is exactly that
  shape: `MQ02.SetStage(20)`, the end-of-chargen topic unlocks and the
  autosave all follow its class menu.

The general rule: when a converted call blocks, ask whether its caller
repeats. A `Return` is only correct where something will call again.

### A "no equivalent → 0" fallback can shadow a working handler (2026-07-31)

`_convert_expression` keeps a list of argument-less commands that have no Skyrim
equivalent and returns the literal `'0'` for them. Two entries on that list
**also had real handlers** in `_emit_function` — and because those commands take
no arguments they are *always* read bare, so the fallback always won and the
handler was unreachable dead code.

* **`IsPCAMurderer`** → `If 0 == 1`. `DarkBrotherhoodScript`'s site is the sole
  trigger for the entire Dark Brotherhood questline, so Lucien Lachance never
  appeared and `Dark01Knife` never started.
* **`GetDetectionLevel`** → 56 dead threshold tests, including all 7 of
  `Dark04ExecutionScript`'s guard-aggro triggers and the Dark Sanctuary
  assassins' reaction to the player.

The lesson generalises: **before adding a command to a "no equivalent" list,
grep for an existing handler**, and before trusting one that is already there,
check whether the command's arity lets the handler be reached at all. A flat `0`
is invisible — it compiles, it never warns, and the call site quietly becomes a
constant.

When flattening *is* right, it must still be justified by how call sites read
the value. `GetDetectionLevel` was defensible only if scripts read the level
numerically; censused over the plugin, **not one of the 56 sites does** — every
one is `>= 2`, `>= 3` or `== 3`, i.e. "is the target detected", which
`IsDetectedBy` answers exactly.

### A compound `player.X` entry can shadow a handler too (2026-08-02)

Same family as above, different mechanism. `_emit_function` short-cuts any
`ref.func` whose **compound** key (`player.moveto`) exists in `FUNCTION_MAP`,
returning before the dedicated handler further down. `_COMPOUND_HAS_OWN_HANDLER`
exempts commands that need the handler; only `placeatme` was listed.

`moveto` needed the exemption for three reasons:

1. The compound path routes args through `_convert_args`, which **splits on
   commas only** — Oblivion writes the offsets space-separated
   (`MoveTo marker 0 100 0`), so they glued onto the target name.
2. It never registers the destination as a property. The call then emitted a
   bare identifier that nothing declared, and the compiler rejected the **whole
   script**.
3. Only the `Player.`-prefixed form took that path, so a plain `ref.MoveTo`
   looked correct — which is exactly what hid the bug.

MoveTo's destination is a placed reference, so the handler now types it
`ObjectReference` (and skips `player`, a converter keyword that is never a
property, and any already-converted expression).

**Morroblivion symptom:** `CATChargenAndTransport` failed on
`Player.MoveTo CGPlayerStartMarker1`. Note the mod's own typo — no such marker
exists; the SCRO table binds only `CGPlayerStartMarker`, because Oblivion's
compiler treated the trailing `1` as MoveTo's optional offset argument. Oblivion
silently no-ops an unresolved target; Papyrus will not compile an undefined
name, so **one dead line in the mod took down the whole start-menu script**, and
with it the Imperial City transport.

### A script that fails to COMPILE takes its dependents down with it (2026-08-07)

**Symptom:** Morroblivion's Fighters Guild handed out no quests after joining.
The Papyrus log named a *linking* failure, not a compile one:

```
Error: Unable to link type of variable "::fbmwFGAdvancement_var" on object
  "TES4_QF_fbmwFGKillBosses"
error: Unable to bind script TES4_QF_fbmwFGKillBosses to fbmwFGKillBosses (...)
  because their base types do not match
```

**The chain, and why the log points at the wrong file.** `TES4_QF_...KillBosses`
declares a property typed `TES4_fbmwFGAdvancementQuestScript`. That script
declares one typed `TES4_mwGetFactionWitnessesFunc` — which **failed to compile,
so no `.pex` was ever written**. A missing type cannot be linked, so the quest
script fails to load, so the QF fragment cannot bind, so **no stage fragment ever
runs**. Three files away from the error message.

The lesson generalises: **check `output/<plugin>/scripts/compile_errors.log`
before theorising about a dead quest.** One uncompilable script silently
disables every script that names its type, and the runtime error surfaces on the
dependent, never on the culprit.

`mwGetFactionWitnessesFunc` is called by **all nine** Morroblivion guild
advancement scripts, so a single unconvertible OBSE loop disabled every guild.

**What was actually wrong with it**, each fixed generically:

| Defect | Fix |
|---|---|
| OBSE `Label`/`Goto` ref-walk — not Papyrus keywords at all | `GetFirstRef 69`/`GetNextRef` → `Game.FindRandomActorFromRef` sampling; `Label` opens a `While`, `Goto` is a no-op (the header re-tests) |
| `GetIsGhost` / `GetUnconscious` unmapped (only the SETTERS were) | → `IsGhost()` / `IsUnconscious()` |
| `NextActor.IsCreature` — the dotted path resolves a name only if it is a `FUNCTION_MAP` key | added the `iscreature` alias beside `getiscreature` |
| `SetFunctionValue` with **no following `return`** | staged value inside a branch now returns where it stands — it was being dropped, making the function a constant `false` |
| `IsInFaction(Form)` — Papyrus wants a `Faction` | table-driven downcast at UDF call sites (`_UDF_ARG_DOWNCASTS`) |
| `_balance_if_endif` matched only a bare `function `, never `Int Function ...` | typed UDF bodies are now balanced too |

**Two parser bugs found alongside, both silent corruption rather than errors:**

- The arithmetic split ignored string literals, so `FileExists "Data\Morrowind_ob
  - Meshes.bsa"` split on the hyphen *inside the path* and leaked fragments out
  as code (`If 0 - Meshes.bsa(") == 0`).
- `_convert_args` split on whitespace regardless of quotes, so
  `IsModLoaded "Voice Overs V002.esp"` became three arguments and collapsed to a
  bare `If True` — firing every "deprecated plugin detected" warning
  unconditionally.

**Polarity matters when neutralising an install probe.** `FileExists` and
`GetModIndex` have no Papyrus answer, but every TES4 caller uses them to detect a
BROKEN install (`if FileExists ... == 0 → "ERROR: ... is missing"`). The paths
named are Oblivion-side BSAs and inis that do not exist after conversion *by
design*. Answering `0` fired every missing-file branch at once and greeted the
player with a bogus error box, so both answer the not-an-error side.

### `GetIsClass` / `GetPCIsClass` read the ActorBase (2026-08-02)

Both were **absent from `FUNCTION_MAP` entirely**, so the call survived
untranslated and Papyrus parsed `GetPCIsClass CharactergenClass` as a bare name
after a name — a syntax error that failed the whole script. Skyrim reads the
class off the ActorBase (`ActorBase.GetClass()`); `Actor` has no `GetClass()`.
The CLAS argument types as `Class`.

Site: Morroblivion's `fbmwChargenQuestScript` (the class quiz), which the
Chargen-and-Transport start menu imports — so the failure propagated to the
transport NPCs.

### A Bool cannot carry a multi-valued TES4 threshold (2026-07-31)

Mapping `GetDetectionLevel` onto `IsDetectedBy` is only half the fix, and the
missing half fails *silently*. Papyrus rejects a bare `Bool >= 2` outright
(*"cannot relatively compare variables of type bool"*), so the generic
`_BOOL_CMP_RE` pass wraps it as `(... as Int) >= 2` — and **`true as Int` is 1**.
That compiles cleanly and is permanently false, so a naive mapping trades one
dead form for another while looking like a fix.

TES4 detection levels run 0 (unnoticed) to 3 (fully detected), so the emission
scales the Bool to the source's own top value:

```papyrus
((target.IsDetectedBy(observer) as Int) * 3)
```

0 or 3 satisfies every threshold the plugin uses (`== 3`, `>= 2`, `>= 3`)
exactly when detected and never otherwise. **Whenever a TES4 function with a
range wider than 0/1 is mapped onto a Papyrus Bool, rescale to the source's
range** — do not let the generic `as Int` cast decide, because it collapses the
range to 0/1 and quietly kills every threshold above 1.

### Skyrim has NO attributes — the AV tables share nothing (2026-08-06)

**The two games' actor-value tables do not overlap at a single index.** TES4 0 is
Strength, TES5 0 is Aggression; TES4 5 is Endurance, TES5 5 is Assistance
(xEdit `wbActorValueEnum` in `wbDefinitionsTES4.pas` vs `wbDefinitionsTES5.pas`).
A CTDA `ptActorValue` param is a **raw index** into that table, so passing it
through unchanged reads a completely unrelated value.

Worse, Skyrim has no attributes at all. Strength, Intelligence, Willpower,
Agility, Speed, Endurance, Personality and Luck simply do not exist as actor
values, and no TES5 value is a faithful stand-in — every candidate (`SpeedMult`,
`HealRate`, `UnarmedDamage`, …) sits on a different scale, so a 0-100 attribute
threshold compared against one is arbitrary.

**This made every Morroblivion guild unjoinable.** Joining the Fighters Guild is
gated on `GetActorValue Strength >= 30 AND GetActorValue Endurance >= 30`
(INFO `013204F7`); converted verbatim that became `Aggression >= 30 AND
Assistance >= 30` — 0-3 enums that can never reach 30 at any level — so the
recruiter always fell through to *"The Fighters Guild can't just sign up anyone.
You don't meet our requirements."* The Thieves Guild (Agility/Personality →
Morality/One-Handed) failed identically, and the same defect hit ~600 conditions
across the exports. The script side had its own version: the polyfill aliased
`Strength → UnarmedDamage` (≈0, never passes) and `Agility → SpeedMult` (≈100,
always passes), so `fbmwFGAdvancementQuestScript`'s per-rank promotion gates were
equally dead.

The rule now, on **both** sides:

| TES4 AV | Conversion |
|---|---|
| The 8 attributes | **DROPPED** (CTDA) / stubbed to `100.0` (script read), writes discarded |
| Skills | Translated to the TES5 skill index / name |
| Shared derived + AI + magic values | Translated to the matching TES5 index |
| Everything else (Magicka Multiplier, Attack Bonus, Silence, Telekinesis, …) | Dropped — no TES5 equivalent |

Dropping an attribute gate **fails OPEN**, which is the faithful outcome: the
gate exists to keep an under-developed character out, and a Skyrim character has
no way to raise an attribute, so enforcing it would lock the content away
*permanently* rather than merely early.

Three places must agree, and a change to one needs the same change in the others:
`tes5_import/dialog_conditions.py` (`_TES4_AV_ATTRIBUTES` / `_TES4_AV_TO_TES5`,
applied in `convert_ctda` for functions 14 `GetActorValue` and 277
`GetBaseActorValue`), `script_convert/constants.py` (`TES4_ATTRIBUTES`,
`ATTRIBUTE_STUB_VALUE`, `ACTOR_VALUE_MAP`), and
`script_convert/static_scripts/TES4Polyfill.psc` (`IsTES4Attribute`).

Two AV names the map used to emit — `LuckModifier` and `MuteModifier` — are **not
names the engine knows** (verified against `SkyrimSE.exe`'s AV name table, which
runs `…Blindness, WeaponSpeedMult…` with no silence entry), so every read
returned 0 and every write was rejected. Skyrim's internal names for two skills
are also *not* the UI names: use `Speechcraft` (not Speech) and `Marksman` (not
Archery) in Papyrus strings; the CTDA side uses the numeric indices 17 and 8.

### Aggression/Confidence are ENUMS in TES5, not 0-100 (2026-07-28)

TES4 stores the AI traits on a 0-100 scale; TES5 defines them as small enums
(xEdit `wbDefinitionsCommon.pas`: `wbAggressionEnum` 0-3, `wbConfidenceEnum` 0-4,
`wbAssistanceEnum` 0-2, Morality 0-3). `SetActorValue("Aggression", 100)` is
**rejected outright** — the engine logs *"attempt made to set illegal value"* and
leaves the trait **unchanged**, so every scripted "now turn hostile" beat
silently did nothing. 160 such writes in Nehrim alone (94 of them `Aggression
100`), 509 enum-AV writes across both plugins.

`_scale_enum_av()` buckets literals onto the same thresholds the record-side
converter uses (`tes5_import/record_types/actors.py`), so a scripted change lands
on the tier the NPC's AIDT was converted to. Values already inside the enum range
pass through untouched; non-literals are left alone. `ModActorValue` is
deliberately NOT scaled — a delta on a 0-100 scale has no enum equivalent, and no
such call exists in the source. Verify with
grep the generated sources for `Set/ForceActorValue` on the enum AVs
(`check_enum_actor_values.py` did this; removed 2026-08-25).

#### Aggression must not collapse 6..105 onto tier 2 (fixed 2026-07-31)

**TES4 aggression is only half of a PER-TARGET rule**; TES5's is a GLOBAL tier.
UESP `Oblivion:Aggression`: an actor attacks a target when
`disposition(actor→target) < aggression - 5`, so ≤5 never attacks and ≥106
attacks anyone regardless of disposition. TES5 instead names *which reaction
class* the actor attacks (UESP `Skyrim:NPCs#Aggression`): 0 nobody, 1 Enemies,
**2 Enemies AND NEUTRALS**, 3 everyone.

The old rule was `0 if raw <= 5 else (3 if raw >= 106 else 2)` — everything from
6 to 105 became tier 2. **The player is a Neutral to most factions**, so any
scripted "wake up and join this fight" turned the actor hostile to the player.

CharacterGen stage 22 is the case that exposed it: `GlenroyRef.setav aggression
10` exists so the Emperor's guards respond to the Mythic Dawn ambush. In Oblivion
10 only beats a disposition below 5, and the guards' disposition toward the
player is ≈47, so they never turn on you. Converted to tier 2 they attacked the
player from stage 22 onward — the exact failure UESP describes: *"a guard would
attack the whole town if their aggression were sufficiently raised."*

The threshold is `_ONSIGHT_AGGRESSION = 65`, matching the record path's margin
test (a default actor with disposition ≈ Personality 50 needs `(aggr-5) - 50 >=
10`, i.e. aggression ≥ 65 before it earns tier 2):

| TES4 `setav aggression` | tier | meaning |
|---|---|---|
| ≤ 5 | 0 | never initiates |
| 6 – 64 | **1** | attacks declared Enemies only — the faction graph picks the opponent |
| 65 – 105 | 2 | attacks Neutrals on sight too |
| ≥ 106 | 3 | Frenzied |

Census of the 227 scripted calls in Oblivion.esm: 38 → tier 0, **78 → tier 1**
(values 10/20/25/30/40/50, previously all tier 2), 111 → tier 2 (70/80/90/100,
the genuine "now attack anyone" beats). Keep this table in step with
`_npc_aidt` in `tes5_import/record_types/actors.py`, which applies the same rule
to base records but subtracts disposition explicitly.

Two recurring shapes, both found in the animation handlers:

- **Wrong target vocabulary.** The emitted call is valid Papyrus but the string
  argument comes from TES4's namespace, which the engine silently drops.
  `PlayIdle`/`PickIdle` passes the raw TES4 IDLE EditorID straight into
  `Debug.SendAnimationEvent(ref, "<edid>")`; Skyrim defines no such event, so the
  idle never plays and nothing is logged (`"fastforward"` survives into output
  this way, next to correctly-mapped events like `moveStart`).
- **Unconditional target-type assumption.** *The correct API depends on WHAT THE
  TARGET IS, not on whether the call names a reference.* `PlayGroup` routed every
  explicit-ref call to `Debug.SendAnimationEvent` (behavior-graph actors only), so
  `CGPrisonSecretWallRef.playgroup forward 1` — an ACTI whose NIF carries a
  `Forward` NiControllerSequence — did nothing and Renault's switch never moved
  the wall, while the SELF-call on the very next line converted correctly. Fix:
  resolve the base record via `CrossRefGraph.get_base_signature()` and treat only
  `NPC_`/`CREA`/`ACHR`/`ACRE` as actors; unknown targets keep the behavior event,
  which is inert on an object but never corrupts an actor's graph. `PlayIdle`
  still uses the old `actor_func=True` assumption and needs the same treatment.
**Census the no-op lists against the real API, not against intuition.** Six
entries in `_NO_OP_FUNCS`/`_BARE_NO_EQUIV_COMMANDS` exist natively in Skyrim:
`AddAchievement` (59 call sites), `PlayBink` (5), `SendTrespassAlarm` (2),
`SetPublic` (1), `AttachAshPile`, and `GetCurrentPackage` (already special-cased
for the PACK-comparison form; the residual sites compare TES4 package-TYPE codes,
which genuinely have no equivalent). `SetCellPublicFlag` (100 sites) sets the same
Cell flag as `SetPublic` and should route there rather than no-op. The
authoritative list is the vanilla Papyrus sources at
`references/skse64-master/scripts/vanilla` — extract every `Function` declaration
and diff it against the no-op sets before assuming a command was dropped for a
good reason.

Losses that ARE correct and should not be re-litigated: `AddTopic` (223 `;NE:`)
is deliberate — `tes5_import/dialog_unlocks.py` re-expresses topic visibility as
`TES4Unlock_*` GLOB gates and scans SCPT sources *because* script_convert leaves
an inert comment. `ModDisposition` (414) is a genuine engine removal, with the
`<= -100` hostility case already converting to `StartCombat`.

## Event / timer conversion
<a id="event-timer-conversion"></a>

- `begin OnAlarm` → `OnCombatStateChanged` guarded `aeCombatState != 0`;
  `OnStartCombat` bodies are guarded `== 1` (the event also fires on combat END).
- Bare `begin MenuMode` + `isPCSleeping` (Oblivion's sleep-detection idiom) →
  `RegisterForSleep()` + OnSleepStart/OnSleepStop running the body twice with a
  `TES4_PCSleeping` flag (11 quests incl. MG04 inn ambush, Rufio murder,
  vampirism relied on it). Menu-ID MenuMode blocks stay commented out.
- `GetSecondsPassed` substitutes `_get_update_interval()` (must equal the
  RegisterForSingleUpdate arg or timers run off-rate).
- Converted GameMode loops must not only start on cell attach — an
  already-loaded actor never ticks. They start from an `OnInit` gated on
  `TES4Polyfill.ShouldRunGameMode(Self)`.
- **That gate is cell attachment, NOT `Is3DLoaded()`** (2026-08-01). A disabled
  reference has no 3D, so a 3D-gated poll can never start on one — and the poll
  body is routinely the only thing that ever calls `Enable()` on that same
  reference. See "The self-enable deadlock" below.

### Say() timers — `TES4Polyfill.SayLine` (2026-08-16)

**The single design fact.** TES4's `Say`/`SayTo` were **synchronous**: the
engine picked the INFO, started the audio and **returned its length before the
next script line ran**, so every scripted conversation is written as

```
if CharacterGen.speaker == 4 && CharacterGen.convTimer <= 0
    set CharacterGen.convTimer to SayTo player, CharGenMain 1   ; := line length
endif
```

and every other participant waits on that one countdown. Papyrus `Say()` is
fire-and-forget and returns nothing. Every previous conversion tried to
*estimate* the missing number (a topic-max charge at the call site, a "park"
sentinel released by the End fragment, an OnBegin re-charge, per-owner
property bindings, a decay-proof beat companion, a race-safe decrement, an
`If T <= 0` override guard, three End-fragment ordering constraints, quest-
scoped release …) and each estimate had an edge where a line was cut, repeated,
dropped or held. **The rewrite stops estimating: the length comes from the
engine.**

#### The mechanism

* **Every INFO carries a Begin+End fragment pair** (`TES4_TIF__<fid>`, VMAD
  flags 0x03; `build_vmad_info_fragment` and `_info_batch` are both
  unconditional so the two sides can never disagree). Their fixed job:
  `Fragment_1` (OnBegin) → `TES4Polyfill.LineBegan(akSpeakerRef, <measured
  length of THIS line>)`; `Fragment_0` (OnEnd) → the TES4 result script, then
  `TES4Polyfill.LineEnded(akSpeakerRef)` **last**. The hooks carry only the
  speaker — no owner analysis, no property binding, nothing to miss.
* State lives in four script Actor Values **on the speaker** (`Variable07`
  claim, `Variable08` claim deadline, `Variable09` playing line's length,
  `Variable10` speaking deadline; deadlines in game time, see the polyfill).
* A converted `set T to [ref.]Say[To] … topic [+ n]` becomes
  ```
  T = <topic max + 1>                    ; closes this poll's own guard for the ~2s a SayLine can take
  T = TES4Polyfill.SayLine(<speaker>, <topic>, <topic max>) [+ n]
  ```
  `SayLine` **blocks until the engine has begun the line** and returns that
  line's real length **+ an adaptive tail**. The tail must cover the engine's
  own overhead between the measured audio length and the End fragment running
  (dispatch + trailing hold + inter-response gaps); it is stable per machine
  but unknowable in advance (measured here: median 0.35s, p90 0.54s; 11–24s
  under a starved VM), so `LineEnded` records each played-through line's
  overhead into a per-speaker running average (`Variable04`, plus a
  game-wide one on the player) and the tail is that estimate + 0.2s, clamped
  to [0.35, 2.5] (0.8 until anything is measured). A fixed 1.0s cost 0.6s of
  silence on every line (~1.5s audible gaps); a fixed 0.4 repeated lines when
  the VM was slow. Say-driving scripts poll at 0.25s and the pre-charge is
  capped at 2.0s (it is shared with the other speakers' guards, so a stray
  Say costs them at most that). Then the script continues at once, exactly
  as after TES4's `set T to Say`. A Say nothing qualifies for returns **0**
  after a 2s start timeout and the caller's own poll retries — Oblivion's
  behaviour too. Before Saying it waits while the speaker is in the player's
  dialogue menu (Oblivion froze GameMode in menus) or still speaking a tracked
  line (Skyrim silently drops a Say on a talking actor; Oblivion cut the line),
  and it keeps **one waiter per speaker** (a second SayLine returns 0.5 and the
  poll comes back). A `short` timer rounds UP (`Math.Ceiling`).
* **Fragments never write timers.** The owning script's countdown is a plain
  `T = T - dt` again; a fixed override right after the Say (`set convTimer to
  12`) replaces the length before any countdown, exactly as in Oblivion; a
  `set Q.convTimer to Q.convTimer + 2` in an End result lands on the live
  countdown's tail as an after-line pause; `convTimer - .4` "cut him off"
  trims it. None of that needs machinery any more.
* **An ACTOR script's poll skips the pass while the player is in a dialogue
  menu with anyone** — `Self.IsInDialogueWithPlayer() ||
  TES4Polyfill.PlayerIsInDialogue()` (Oblivion's GameMode never ran while a
  menu was open). Skyrim has no "is the player in dialogue" query, so
  `LineBegan` stamps the speaker of any line spoken inside the player's menu
  on the player (`Variable05/06` = FormID hi/lo) and `PlayerIsInDialogue`
  asks that actor. Two in-game failures drove it: the Emperor's
  `speaker == 4 && convTimer <= 0` poll fired during his stage-42 greeting
  (the greeting's End result is what sets `speaker = 0`), its SayLine waited
  for the menu and then spoke a stale "come closer" line exactly as stage
  44's force-greet arrived, which was consumed by a talking actor; and
  Baurus's stage-19 torch line fired INTO the player's conversation with the
  Emperor because a reply's result set stage 19 while the menu was open.
  Quest polls are NOT gated (the conversation countdown lives there; freezing
  it in 2026-08-14 shifted every beat).
* **Diagnostics are built in.** Every SayLine / LineBegan / LineEnded writes
  a `TES4Say …` `Debug.Trace` with real-time stamps; `python
  tools/dialog/say_trace_stats.py` turns the Papyrus log into Say→Begin latency,
  End overhead vs measured length (what SAY_TAIL must cover), pre-waits,
  drops and the dead-air gap between lines. Read those before touching the
  tail.
* Bare `Say`/`SayTo` (no assignment) stay plain fire-and-forget `Say()`
  (Nehrim's 727 hand-timed speech state machines). The measure-then-deliver
  pair (`set L to ref.Say T` / `ref.Say T`) collapses to the SayLine alone.
* The NPC-to-NPC driver (`tes5_import/npc_conversations.py`) uses the same
  primitive: `Utility.Wait(TES4Polyfill.SayLine(A, T, fallback) + 0.6)`.

#### Why results stay in the END fragment

Oblivion ran an INFO's result script when the line **finished**. The evidence
is the CS wiki's own scripted-conversation recipe (`How do you set up a
scripted conversation between two or more NPCs?`): it has each result write
`set <quest>.convTimer to <duration of sound file +/- a few seconds>` "to
further refine the timing between this dialogue and the next, and allowing for
momentary pauses" — an after-line pause, which is only meaningful if the
result runs at the end (at line start `set T to Say` would overwrite it). MQ04's
`set MQ04.convTimer to MQ04.convTimer + 2 / + 3 / + 10` beats and CharGen's
`convTimer - .4  ; cut him off` are the same idiom. So OnBegin only reports;
the sequence gate (`_sequence_gate`, applied only when the body itself steps
the counter it is conditioned on) still protects against a mid-line re-seed.

#### Measured in game (2026-08-16, CharacterGen 30-50, `TES4Say` traces)

* Say→Begin latency **0.14–0.26s** when the engine takes the line.
* End overhead (End fragment vs measured audio) **0.4–0.72s** for single
  lines — so a 0.4 tail was genuinely too small and 1.0 leaves ~0.3s.
* **The player can skip a menu line** (click through) and **exit the menu**
  mid-line; Skyrim then runs the skipped line's End and the next line's
  Begin in the SAME frame, End sometimes second. An unconditional clear in
  `LineEnded` wiped the flag of the line that had just started; the speaker's
  own poll saw him idle, and its `Say()` **INTERRUPTED the live line — a Say
  on a talking actor is not always dropped, it can cut the line, and the cut
  line's End result is lost** (`CGEmperor09`'s `setstage 43` → birthsign
  menu never opened). Hence the length-matched clear.
* A Goodbye reply keeps playing after the menu closes; `IsInDialogueWithPlayer`
  goes false at once. Hence `PlayerIsInDialogue()` also holds while the last
  dialogue speaker is still speaking, and QUEST polls are gated too (stage
  `45 → 50` fired from the quest poll mid-dialogue and sent Baurus in).
* **The Papyrus VM starves easily** (`Update budget: 1.2ms` per frame in the
  log). With the dialogue gate on every poll (~210 quest polls at 0.1s +
  every actor poll), from the START of CharacterGen the End fragments of 1–2s
  lines ran **11–17s late**, the VM dumped stacks, SayLine's 10s busy margin
  expired first and "Yessir" played twice. Only scripts that speak carry the
  gate now (153 in Oblivion), the busy deadline is length+30s (it only bounds
  a lost End), the pre-charge is capped at 3.5s (it is shared with the other
  participants' guards), and the start timeout is 1.5s nominal (each
  iteration is a VM turn, so it stretches with load). Same code from a stage-30
  save had 0.4–0.7s End overhead — load, not logic, was the difference.
* Right after an ambush, `Say(CharGenMain)` on Glenroy was refused for ~20s
  while his HELLO greeting bark did play (combat/search state — traces now log
  `inCombat`/`weaponDrawn`); the first Say after a busy wait was dropped
  because the End fragment was still returning — hence the 0.25s wait.

#### What was measured (Oblivion.esm)

397 timer-assigned Say sites in 207 scripts over 275 topics (+28 in QUST stage
results, 1 in an INFO result); 409 bare Say sites; Nehrim: 0 timer-assigned,
727 bare. Only 6 INFO results write a Say timer (MQ04's three beats, the
CharGen `- .4`, one `= 1`, one unrelated `timer = 0`).

#### Traps

* `Utility.GetCurrentRealTime()` restarts with the process, so a deadline
  stamped in one session is garbage in the next — deadlines are game-time
  days at the current TimeScale (`_GameDays`).
* A dead SayLine thread (mod update, script removed) would hold the per-speaker
  claim; the claim is renewed every 0.1s and expires 5s after the last
  renewal, so it can never strand a speaker.
* A lost End (actor killed or unloaded mid-line) expires the speaking state
  `length + tail + 2s` after Begin — a stale busy flag costs one line's length,
  never a stall.
* If OnBegin ever fired *before* the audio started by more than SAY_TAIL, the
  guard would reopen while the audio still played and the same speaker's next
  SayLine would wait on the busy flag (bounded) before Saying — no repeat is
  possible because the state has advanced by then, but the pause would show.
  Raise `SAY_TAIL` in `TES4Polyfill.psc` if that is ever observed.

## Magic / condition helpers
<a id="magic-condition-helpers"></a>

- `pme`/`sme` (PlayMagicEffectVisuals) take a MGEF code, not a shader: resolve
  code → TES4 MGEF → its `DATA.EffectShader` (else EnchantEffect, else school
  enchant glow) → converted EFSH, and emit `<shader>.Play(ref, dur)`. EFSH
  records are converted, so the property binds.
- `IsSpellTarget X` → `TES4Polyfill.HasMagicEffectByID(ref, <Skyrim MGEF fid>)`
  where the MGEF is the spell's first effect surviving import (same mapping as
  `_pack_effects`); pure script-effect spells are detected via the importer's
  first filler effect, which keeps the dropped effect's duration for exactly
  this reason.

## Reaching 100% compile (2026-07-28, 42 → 0 failures)
<a id="reaching-100-compile"></a>

Nehrim 2620/2620 and Oblivion 15959/15959 now compile. The failures clustered
into a few generic causes, all fixed in the converter rather than per-script:

- **Comma-form RECEIVER on a zero-arg command.** `StopCombat, Player` /
  `IsInCombat, Player == 1` name the *receiver*, not an argument — the comma
  spelling of `Player.StopCombat`. Treating it as an argument gave `IsInCombat(Player)`
  ("function takes 0 parameters not 1") or dropped the token and acted on the
  WRONG ACTOR. `_ZERO_ARG_REF_FUNCTIONS` (derived from the empty-argument `ref.`
  rows of `docs/reference/skyrim_commands.md`) drives the promotion. **When widening the
  bool-comparison regex, keep the `\b` and the mandatory separator** — without
  them `GetDead` matched the prefix of `GetDeadCount` and split off `Count` as an
  argument across 28 scripts.
- **A local variable may shadow `player`.** `StartCelleAufzugTriggerZone01Script`
  declares `Short Player` as its own trigger flag; substituting the keyword gave
  the un-assignable `Game.GetPlayer() = 1`. Locals win in a VALUE position but
  never as a **receiver** (a Short has no methods) and never inside
  `IsActionRef`, whose operand is always a reference — hence
  `_convert_ref(..., as_receiver=True)`.
- **Property names must key on the CANONICAL EditorID.** TES4 lookup is
  case-insensitive, so `SetEssential Kornderbraumeister` refers to
  `KornderBraumeister`; keying on the local spelling created a second
  `_property_refs` entry differing only in case, and since Papyrus is also
  case-insensitive the two declarations collided and the typed one lost.
  Where the EditorID collides with one of the script's own variables (MQ19Script
  has an `Int narel` beside the NPC_ `Narel`), `_actor_base_property()` mints a
  `<Name>Base` property and `resolve_property_formid` strips the suffix to bind it.
- **A mapped GLOBAL call takes no receiver.** `Player.DisablePlayerControls`
  emitted `Game.GetPlayer().Game.DisablePlayerControls()`. Any `FUNCTION_MAP`
  target starting `Game.`/`Utility.`/`Debug.`/`Math.` drops the TES4 receiver.
- **`as` binds tighter than arithmetic.** A trailing `as Int` only types the whole
  expression when no bare operator precedes it; `A - B.GetValue() as Int` is
  `Float - Int`. Also: a plain Float→Int copy (`ihour = vtime`) needs the cast
  just as much as an expression does, and OBSE `let` needs the same coercion
  `set` already had.
- **No-equivalent handlers must return a BARE literal.** These sit inside larger
  conditions, where a trailing `;` comment swallows the rest of the line
  (`If True  ;(False ;NE: ...)`). Push the note to `_line_comments` instead.
- **Match no-equivalent FAMILIES by pattern, not by name.** Enumerating OBSE
  commands one per build is how `disableKey` and `setMenuFloatValue` each survived
  to fail alone. `con_*`, `get/setMenu*`, and the input family are prefix-matched,
  and the bare-read router honours those prefixes without a `FUNCTION_MAP` entry.

New native equivalents found (always check before declaring one absent):

| TES4 / OBSE | Papyrus | Note |
|---|---|---|
| `GetDeadCount <base>` | `ActorBase.GetDeadCount()` | Exact match. Previously emitted a literal `0`, silently disabling **152 quest gates** (126 of them `== 1` checks that became `0 == 1`). |
| `SetEssential` | `ActorBase.SetEssential(bool)` | On ActorBase, not Actor. |
| `PositionWorld x y z ang ws` | `SetPosition` + `SetAngle` | No worldspace param; dropped. |
| `ForceFlee` / `Flee` | `SetActorValue("Confidence", 0)` + `EvaluatePackage()` | Skyrim drives fleeing off Confidence — the engine's own mechanism. |
| `GetAttacked` | `Actor.IsAlarmed() as Int` | |
| `IsInAir` | `Actor.IsFlying() as Int` | |
| `con_Save` | `Game.RequestSave()` | |
| `DispelSpell` | `Actor.DispelSpell(Spell)` | Actor-only — must NOT sit in `_OBJREF_SHARED_FUNCTIONS`. |
| `$var` (OBSE) | `(var as String)` | `$` is not even a legal Papyrus character. |
| `string_var` / `array_var` | `String` | Missing from `TYPE_MAP`, so the variable got **no declaration at all**. |

Genuinely absent (inert `;NE:`): OBSE UI/menu (`get/setMenu*`), raw input
(`isKeyPressed*`, `disableKey`, `getControl`), console commands (`con_*`),
INI access (`Set/GetNumericINISetting`), `getCrosshairRef`, `getObjectType`
(Skyrim's form-type numbering differs entirely), `GetStringGameSetting` (Papyrus
has only the numeric getters), `SkipAnim`, `getPackageTarget`, and
`UnlockAchievement`. An OBSE `forEach … loop` suppresses its **whole body** — the
body reads an iterator that cannot exist.

A cross-script write to a variable the owner never declares
(`AutoSaveQuest.ReadyForAutosave`, 3 scripts) is **dangling in the original mod**.
Oblivion ignored it; Papyrus fails the whole file, so it is commented out.

## Syntax traps found via Nehrim (2026-07-20, 50.5% → 98.4% compile rate)
<a id="syntax-traps"></a>

- **`;/` opens a Papyrus BLOCK comment** (closed by `/;`). Oblivion scripts use
  `;//////...` banner rules constantly and TES4 had no block-comment syntax, so
  every banner swallowed the rest of the file. The compiler only reports this as
  `unexpected end of file` at the LAST line, and one unterminated banner in a
  widely-extended base script cascaded into ~300 downstream failures.
  `_postprocess_lines` pads a space after the `;`.
- **Oblivion accepted a comma between a command and its first argument**
  (`IsActionRef, Player`, `MessageBox, "text"`, `SetPCExpelled Fac, 1`).
  `_emit_function` strips a leading comma once for all handlers; the expression
  router also matches `^(\w+)(?:\s*,\s*|\s+)(.+)$`. Handlers that
  `split(None, 1)` must still `rstrip(',')` the token.
- **TES4 EditorIDs may start with a digit** (`1Feuerball`, `01SetBonus...`);
  Papyrus identifiers may not. Regexes anchored on `^[a-zA-Z_]` silently skipped
  these, leaving the raw name in the output. Use `^\w+` and exclude pure digits /
  `(?!\d+\.)` so float literals still parse. `_safe_property_name` strips the
  leading digit for the declaration, so call sites must go through the same
  lookup or the two disagree.
- **`"EditorID".Function` (quoted ref)** is valid TES4 and appears in 143 Nehrim
  scripts. Unquote before the ref patterns run, or the call is emitted as a
  property access on a string.
- **Anything unparseable must be emitted COMMENTED**, never as bare code — TES4
  uses `-----` separator rules, which parse as a prefix expression.
- A `FUNCTION_MAP` entry with a `None` Papyrus name normally falls through to the
  EditorID lookup on purpose (bare `getSecondsPassed` etc. are rewritten by later
  passes; routing them early TODO's them mid-expression and leaves
  `timer = timer - `). Bare-read commands that have no such pass belong in
  `_BARE_NO_EQUIV_COMMANDS`.
- `Activate` conversions: bare `Activate` → `(akActionRef/self, true)`. Passing
  `Game.GetPlayer()` produced door/lockpick/teleport storms.

## OBSE constructs (Nehrim depends on these heavily)
<a id="obse-constructs"></a>

- **User-defined functions**: `begin Function{ a, b }` + `Call <ScriptName> arg1,
  arg2` (first arg space-separated, rest comma-separated; param list may use
  EITHER separator). Converted to a Papyrus method named `TES4Call` on the callee
  script, reached through a property typed as that script. NOT `Global` — the
  bodies read the script's own object properties.
  - Params must NOT also be emitted as auto-properties; the parameter would
    shadow the property while callers write neither, so the body reads a
    permanent 0.
  - A TES4 `ref` param is an untyped handle: type it from USAGE (convert the body
    first, then read `_property_refs`), else `Form`. Typing it
    `ObjectReference` — the literal translation — rejected all 170 call sites
    that pass a Spell.
  - `SetFunctionValue X` + `return` → `Return X`, and the function needs a return
    type plus a trailing `Return 0` for fall-through paths.
- `eval <expr>` is a pure pass-through wrapper (Nehrim uses it only around
  `Call`) — drop it. Beware over-broad stripping: an earlier pass ate a variable
  named `Eval`.
- `Let X := Y` and the compound forms `+= -= *= /=` → `X = X op Y` (Papyrus has
  no compound assignment).
- **OBSE `IsCasting` maps NATIVELY** — `GetAnimationVariableBool("bIsCastingRight"
  /"bIsCastingLeft")`, no SKSE needed. Check for a native equivalent before
  declaring a function unconvertible.
- **`sv_Construct` is the ONE OBSE string command with an exact equivalent**: it
  builds a `string_var` from a literal, and a Papyrus `String` *is* that literal,
  so `set q to sv_Construct "text"` → `q = "text"`. It used to fall through to
  the inert `ar_`/`sv_` catch-all below, which left an undefined identifier and
  failed the whole script (2026-08-02). `sv_Destruct` stays a no-op — Papyrus
  strings are garbage-collected, so there is nothing to free.
- No Papyrus equivalent, emitted inert with `;NE:` — OBSE arrays/strings (`ar_*`,
  the rest of `sv_*`, `forEach`), path-based music (`StreamMusic` and Nehrim's bundled `emc*`
  plugin; Skyrim music is MusicType-based), `GetPlayerHasLastRiddenHorse`,
  `HasFlames`/`AddFlames`/`RemoveFlames`, `PositionCell` (Papyrus `MoveTo` takes
  a reference, not cell coordinates), `GetIgnoreFriendlyHits` (Skyrim exposes
  only the setter).

## Scripts on placed references
<a id="scripts-placed-references"></a>

Reference events (`OnPackageEnd`, `OnActivate`) never fire on a base NPC_ VMAD —
they must be relocated to the placed ACHR. This was the CharacterGen stage-10
stall.

### Bare self-reference calls also force relocation (2026-08-01)

`_relocate_actor_scripts_to_refs` originally moved a script for two reasons: a
`GetVMScriptVariable` package gate, or a `begin <reference-event>` declaration.
There is a **third**: a script that calls a reference function on *itself* with
no `ref.` prefix — `enable`, `disable`, `moveto`, `startcombat`, `playgroup`,
`evp`, … An ActorBase is not a reference, so on the base record these calls have
nothing to act on and do nothing at all, whatever event drives them.

This matters because Oblivion's standard scripted-entrance idiom is an
**initially-disabled placement (record flag 0x800) whose OWN GameMode block
enables it on a cue**. `_script_uses_self_reference_call` now detects the bare
call (skipping comment lines, so a commented-out `;evp` does not trigger a move,
and requiring no `.` prefix so `CelebroRef.Disable` — someone *else's* method,
which works fine from the base — does not either).

### The self-enable deadlock (2026-08-01)

The same idiom hit a second, independent bug in the poll gate. The chain:

1. The ref is initially disabled → **no 3D**.
2. `OnLoad` / `OnCellAttach` need 3D or a cell *transition*; a ref already
   sitting in the player's starting cell gets neither.
3. The `OnInit` fallback was gated on `Is3DLoaded()` → **false while disabled**.
4. So the poll never starts, `Enable()` never runs, the ref never gets 3D.

The script that enables the reference only runs once the reference is enabled —
unbreakable. **200 placed refs in Nehrim** were stranded this way (Kim/MQ04,
Erik/NQ01, the MQ20 paladins, MQ31 batteries, MQ33 mirages, sound zones).

The fix is `TES4Polyfill.ShouldRunGameMode(akRef)`: 3D-loaded **or** parent cell
attached. Oblivion's own rule was cell-scoped, not 3D-scoped — GameMode ran for
every ref in an active cell, disabled ones included, which is precisely what
makes the self-enable idiom work. Cell attachment preserves the anti-storm
property the 3D gate was introduced for (refs in detached cells still never
tick); it only stops treating "invisible" as "not there".

**Nehrim intro symptom:** Celebro, the companion who is supposed to attack a
troll and then talk to the player, never appeared in the start cell
(`StartCelle`, 0x00000B9B). `MQ00CelebroScript` is nothing but
`begin GameMode / if ( GetStage MQ00 == 5 ) / enable / endif` — it declared no
reference event, so it stayed on the base NPC_ (bug 1), and its poll was
3D-gated, so it could not have run anyway (bug 2). Both had to be fixed for him
to spawn.

### A bare GameMode block also forces relocation (2026-08-02)

The two triggers above still missed a whole class: an actor script that is
**nothing but a `GameMode` block making explicit `Other.Method()` calls**. It
declares no reference event and makes no bare self-call, so neither reason
fired and it rode the base NPC_ — where it is dead code, because the converter
compiles `GameMode` into an OnUpdate poll whose only starters are
`OnCellAttach` / `OnCellDetach` / `OnLoad` / `OnInit`, all gated on
`TES4Polyfill.ShouldRunGameMode(Self)`. Every one of those is an
`ObjectReference` member; on a base VMAD `Self` is an `ActorBase`, so the events
never fire, the gate has no reference to answer for, and the poll never starts.

`gamemode` is therefore in `_TES4_REFERENCE_EVENTS` now. It is not an engine
reference event — it is there because *our own* lowering of it is
reference-only.

**Morroblivion symptom:** `CATDestinationSorter`, the script driving the
Cyrodiil↔Vvardenfell world transport, is pure GameMode polling a global
(`CATDestinationCode`) and calling `Player.MoveTo(marker)`. Attached to the
base NPC_ of both ferrymen (Kisimba in the Imperial City, Jo'Tesh in Seyda
Neen), it never ran: the player paid 1000 gold, the dialogue fragment set the
destination code, and nothing ever moved them.

### A script on the PLAYER base needs a quest alias (2026-08-01)

Oblivion let a plugin script the player by attaching a SCPT to the player's base
`NPC_ 0x00000007`. **Skyrim has no equivalent binding**, and the relocation above
cannot help: it walks ACHR/ACRE, and the player has no ACHR — `PlayerRef 0x14` is
engine-created and its record signature is **PLYR**, not ACHR, so a plugin cannot
author an override of it. PlayerRef's base is *Skyrim's own* `0x07`; our shifted
copy (`0x01000007`) is a record nothing ever instantiates, so a VMAD there is
inert.

Vanilla's mechanism for "code that runs on the player forever" is a
start-game-enabled quest holding a reference alias forced to `0x14`, with the
script on that **alias** — `JailQuestPlayerScript`, `TutorialPlayerScript`; 71
Skyrim.esm quests force an alias to `0x14`, and the vanilla `Player` NPC_ carries
no VMAD at all. The converter now mints `TES4PlayerScripts` for this
(`object_scripts.build_player_alias_plan` →
`dialog_converter._make_player_script_quest`), lists it in the `.seq`, and emits
the script as `extends ReferenceAlias`, routing every implicit-self call through
`GetReference()` / `GetActorReference()` (`Self` there is the alias, so
`Self as Actor` is a cast the compiler rejects).

Vanilla Oblivion attaches nothing to the player base, so this only ever surfaced
on Nehrim — where `GlobalplayerScript` holds the **entire** XP / level /
learning-point / gold economy *and* the only `SetStage MQ00 1`, which is what
starts the main quest. Without it the intro never began and no character levelled.

Two consequences worth remembering:

- **The player is never a script-typed property.** `player`/`playerref` is a
  converter keyword emitted as `Game.GetPlayer()`. Because the player base has
  EditorID `Player` *and* can carry a SCRI, both `_add_scro_ref` (which skipped
  only `0x14`, not `0x07`) and `get_record_script_type` typed it as the attached
  script — so 242 Nehrim scripts declared
  `TES4_GlobalplayerScript Property Player` and then failed to convert it to
  `ObjectReference` at every `X.GetDistance(Player)` / `MoveTo(Player)`.
- **A property typed as the attached script is not an Actor.** `_add_scro_ref`
  deliberately prefers the script type so cross-script variable reads work, so
  actor-only calls on such a property must be **cast at the call site**
  (`(KreoRef as Actor).EvaluatePackage()`) rather than retyped — and likewise for
  arguments of the four functions whose Papyrus signature declares an `Actor`
  (`_ACTOR_ARG_FUNCTIONS`: `StartCombat`, `IsHostileToActor`,
  `GetRelationshipRank`, `SetRelationshipRank`).

### Quoted EditorIDs — the `_MQ01Tate_` property (2026-08-01)

Oblivion's parser accepts quotes around any EditorID and Nehrim's authors use
them constantly (**173 sites**: `SetStage "MQ01Tate" 20`, `GetStage "NQ00Karick"`,
`StartQuest "NQ05"`, `AddScriptPackage "..."`). `_safe_property_name` maps
`[^\w]` to `_`, so `"MQ01Tate"` became the property `_MQ01Tate_` while the *same
script's* unquoted `GetStage MQ01Tate` became `MQ01Tate`. Only the unquoted
spelling matches an EditorID, so only it was bound in the VMAD — `_MQ01Tate_`
stayed **None** and every `_MQ01Tate_.SetStage(...)` threw at runtime.

The damage was structural, not cosmetic: MQ01Tate could never advance past stage
15, so it never reached stage 40 — the only thing that runs `SetStage MQ01 1` —
and MQ00's completion stage 65 (behind an INFO owned by MQ01) was unreachable
too. `_safe_property_name` now strips a wrapping quote pair, and
`_convert_line` unquotes the dotted member form (`"NQ16"."NQ16CountBooksVar"`,
which previously emitted un-parseable Papyrus because the assignment target and
its value took different code paths). Genuine string literals are untouched.

### `AdvancePCLevel` → the Level actor value (2026-08-01)

Vanilla `Game.psc` (from `Data/Scripts.zip`) has **no level setter** —
`Game.SetPlayerLevel` exists only in mod-supplied headers, so it will not
compile against the shipped set. `Game.GetPlayer().ModActorValue("Level", 1)` is
the equivalent the base game does offer. Nehrim drives its whole custom level-up
through `AdvancePCLevel` (`GlobaltagebuchScript`'s journal menu), so leaving it
unmapped pinned the player at level 1 forever.

> Check `Data/Scripts.zip`, not `Data/Scripts/Source/`, when asking whether a
> Papyrus native exists: the latter is where mods install their own headers, and
> in this install its `Game.psc` is 454 lines against vanilla's 266.

## Actor promotion must follow the DECLARING type, not the "feels like an actor" test (2026-08-18)
<a id="actor-promotion-must-follow-declaring"></a>

**Symptom:** the Imperial City Arena softlocked. The player arranged a match with
Owyn, walked up the ramp, and the announcer never spoke — so `Arena.GateDownFight`
was never set, the gates never opened, and nothing could advance.

**Cause:** the dedicated `Say` handler resolved its receiver with
`_resolve_self_ref(..., actor_func=True)`, which PROMOTES the receiver's property
to `Actor`. But `Say` is declared on **ObjectReference**:

```
ObjectReference.Say(Topic akTopicToSay, Actor akActorToSpeakAs = None,
                    bool abSpeakInPlayersHead = false)
```

Oblivion exercises that breadth. A census of Oblivion.esm's `Say`/`SayTo`
receivers found **144 calls on 21 non-actor references**: every Daedric shrine
(ACTI), Clavicus' dog statue (MISC), and — the arena case — four **XMarker
(STAT)** refs the announcer talks through: `ArenaMatchPlayerRef`,
`ArenaGalleryMarkerRef`, `ICArenaPlayerMarkerRef`, `ICMonsterFightPlayerRef`.
The announcer is not an NPC standing somewhere; it is an invisible marker
positioned in the arena that the topic is played from.

Declared `Actor Property`, those refuse to bind ("cannot be bound because
`<fid>` is not the right type"), the property comes back **None**, and the first
call on it aborts the whole function. In `ArenaAnnouncerScript` that first call
is `ArenaMatchPlayerRef.GetDistance(Player)` on the very first gate, so the
entire `OnUpdate` body was dead every tick.

This is the same failure the `Unlock` handler already documents. Four handlers
had it:

| Handler | Real declaration | Non-actor subjects in Oblivion.esm |
|---|---|---|
| `say` / `sayto` / `saycustom` | `ObjectReference.Say` | arena XMarkers, Daedric shrines, dog statue |
| `cast` | `Spell.Cast(ObjectReference akSource, …)` | `SEHaskillSummonMarker`, `MG05ShockMark1`, `SE05SpellMarker1-3` |
| `pms` / `sms` | `EffectShader.Play/Stop(ObjectReference)` | `SEXedPuzStatue1-5` |
| `getpos`/`getangle`/`setpos`/`setangle`, `moveto` | all on `ObjectReference` | the Xeddefen puzzle statues, summon markers |

All now use `_resolve_objref_ref`. Whole-plugin `Actor Property`-bound-to-a-
non-actor count went **54 → 3**, and the 3 survivors are `LVLC` refs called with
`evp`, which spawn actors — correct as-is.

**The rule:** promote to `Actor` only when the Papyrus method is declared on
`Actor` and nowhere up the chain. `ObjectReference` is the base type, so anything
declared there must resolve with `_resolve_objref_ref` — no matter how strongly
the TES4 call reads like something only an actor would do. `Say` reads exactly
like an actor-only call and is not one.

**Audit for the whole class** (map REFR/ACHR/ACRE EditorID → base record type,
then flag every `Actor Property <name>` whose named ref has a non-NPC_/CREA
base) — worth re-running after touching any handler that passes
`actor_func=True`.

## `StopQuest` converts to `Stop()` — a "run bit" global does NOT work (2026-08-19)
<a id="stopquest-converts-stop-run-bit"></a>

**The real difference** is that Skyrim's `Quest.Start()` on a stopped quest
RESETS it: every `Auto` property back to its default, stage back to 0.
Oblivion's `StopQuest` clears a run bit and touches nothing, so the authored
idiom "seed the variables, then StartQuest" is safe there and destructive here.

**That is handled by `_hoist_quest_start_above_writes`** — move `Start()` ABOVE
the writes it would clobber. Nothing else is needed, and nothing else works.

🛑 **A converter-owned run bit was built and REVERTED (2026-08-19).** The idea:
keep the TES4 run bit in a GLOB `TES4Stopped_<Quest>`, never engine-stop the
quest, and gate dialogue/`GetQuestRunning` on the global — so variables and
stages survive a stop/start cycle exactly as in Oblivion. It preserved the
variables correctly (`CombatantsKilled`/`FirstWin` measured surviving), and it
still broke the Arena, because **a quest that never stops keeps its CURRENT
STAGE**:

* `Arena` has exactly ONE stage, 10 (`AllowRepeatedStages`), whose script
  zeroes the whole match state and then stops the quest.
* Oblivion restages it every match: stopped quest -> `SetStage 10` runs the
  reset again.
* Left engine-running, `Arena` sat at stage 10 permanently. `SetStage(10)` on
  the stage it is already at does nothing, so the reset never re-ran,
  `ReadyMatch` stayed 1, and Owyn's next-match line (gated `ReadyMatch == 0`)
  could never fire — he behaved as though the match had not happened, and the
  gate stayed down. Measured live: `GetStage Arena >> 10`, `ReadyMatch = 1`.

Reverted in full: no `TES4Stopped_*` globals, no INFO stop-gates, no
`GetQuestRunning` expansion, no `IsQuestStopped` poll gate, no
`TES4PersistentActors` FLST / `ResetInterior` polyfill (that rode on the same
design). `StopQuest`/`StartQuest`/`GetQuestRunning` are plain
`Stop()`/`Start()`/`IsRunning()`.

**Kept from that work** (both independently verified): the `Start()` hoist now
also matches a renamed call shape, and `set X.fQuestDelayTime to N` emits
`RegisterForSingleUpdate`, never `RegisterForUpdate` — the latter is a
REPEATING registration and `RegisterForUpdate(0)` shipped in 45 scripts as an
every-frame storm, ended only by the engine stop that the reverted design
removed. Measured on the shipped build: 0 repeating registrations.

### Renaming a converted call can silently disable a post-pass (2026-08-19)

**Symptom:** after the StopQuest run-bit change, ALL arena dialogue stopped —
no announcer line, no subtitle, and the gate never opened. Regression, not a
new bug.

**Cause:** `_hoist_quest_start_above_writes` exists because Skyrim's
`Quest.Start()` resets every `Auto` property, so the authored TES4 idiom
"seed the variables, then StartQuest" must become "Start, then seed". Its
`_QUEST_START_RE` matched only the literal `Q.Start()` shape. Converting
`StartQuest` to `TES4Polyfill.StartQuest(Q, TES4Stopped_Q)` renamed the call
out from under that regex, the hoist stopped firing, and every seeded write
was clobbered again — **91 writes across 43 scripts** (every arena match INFO,
the arena betting, FGC01), reproducing the exact softlock the hoist was
written to fix. The polyfill still calls `Q.Start()` internally, so the
hazard was unchanged; only the pattern that detected it moved.

**Rule:** when a converted call site changes SHAPE, grep for every post-pass
regex that matches the old shape. A post-pass that silently stops matching
fails open — no error, no warning, just the original bug back.

**Second, independent clobber (same symptom class):** `pipeline.py`'s
`_state_writes_before_setstage` hoists literal state writes ABOVE the first
`SetStage` (so an inline stage fragment's `EvaluatePackage` sees committed
state). That runs AFTER the converter's post-processing, and it can lift a
write above `SetStage` while leaving the `Start()` below it — stranding the
seed again (`ArenaICGrandChampion.CrazyIdea`, 2 sites). Fixed by
re-establishing the invariant on that pass's own output.

**This was a fixpoint re-run until 2026-08-28**, and read like one: the hoist
was called a second time, from a fabricated `ScriptConverter.__new__(...)`
instance (it never touched `self` — only three class-level regexes). Nothing
proved the two reordering passes could not ping-pong. It is now
`tes5.blocks.hoist_quest_start_above_writes`, a module function
`_state_writes_before_setstage` calls on its own result as an ordinary fixup
— the same emitted lines, but a pass repairing what it just did rather than
two passes iterating to agreement.

**Guard:** the invariant is "no write to `Q.<prop>` may precede a `Start()` /
`TES4Polyfill.StartQuest` on the same `Q` within one straight-line run".
Measured on the shipped build: 91 → 0.

## The SCRO table outranks the script TEXT (2026-08-22)
<a id="scro-table-outranks-script-text"></a>

**Oblivion runs the COMPILED script, not the source the CK shows you, and the
two can disagree.** When a record is renamed after its scripts were last
compiled, the source keeps the old name while the compiled form-reference table
(the `SCRO` array) keeps the FormID. The engine reads the FormID, so the stale
spelling is completely invisible in-game.

Converting the TEXT, such a name resolves to nothing. Two failure modes, both
measured:

| Symptom | Where | Cost |
|---|---|---|
| Undefined identifier | Knights.esp `TES4_QF_ND00/03/07/09` | CHECKER error → **no `.pex` for the whole script**, so every OTHER stage of the quest dies too |
| Property declared but bound to nothing | Oblivion.esm `TES4_QF_SE02` | compiles fine; the first use ABORTS the fragment (see `project_unbound_vmad_property_aborts`) |

Knights.esp's stage result scripts still read `player.additem NDArmorCuirass 1`
and `player.additem NDLL0WeaponSword 1` while their SCROs bind
`NDArmorHeavyCuirass1` ("Cuirass of the Crusader") and
`NDLL0WeaponSwordLvl100` — these fragments are what hand out the Crusader
relics. Oblivion's SE02 stage 15 reads `startQuest SE02FIN`, a name no record
carries, while the SCRO binds the real quest `SE02Conv`; the Shivering Isles
post-quest dialogue quest was silently never started.

### Recovery is a SET DIFFERENCE, not a positional walk

`pipeline.resolve_scro_aliases()`. The compiler's emission order does **not**
follow source order — `player.additem X` emits the receiver first, so the ND03
stage reads `ND02 / ND02 / ND02 / player / NDArmorCuirass / ND03` against SCROs
`[Player, ND02, NDArmorHeavyCuirass1, ND03]`. A positional walk mis-binds. The
CONTENTS of the table, however, are exact:

- a SCRO whose EditorID the body never spells = a form referenced under some
  other name;
- a body name that resolves to no record = a reference with no form.

**Exactly one of each ⇒ they are the same reference.** Anything less certain
binds nothing.

A prefix rule does not work either: two of the five Knights renames only append
(`NDLL0WeaponSword` → `…Lvl100`) but `NDArmorCuirass` → `NDArmorHeavyCuirass1`
inserts a word in the middle.

### Tokenising the body — the two traps

- **Command names must be dropped** (`low in FUNCTION_MAP`): a command is not a
  form. Otherwise `setstage` looks like an unresolvable name.
- **Quoted EditorIDs must be KEPT, quotes stripped.** Oblivion's parser accepts
  quotes around any EditorID and vanilla uses them. Stripping the whole literal
  made TG03Elven's `PlaceAtMe "TG03LlathasasBust"` look unspelled, and that
  stage's `IsXBox` — an OBSE command with no `FUNCTION_MAP` entry — looked like
  the rename it paired with. It would have bound a variable to a statue.

### Measured selectivity

| Plugin | fragments scanned | aliases fired |
|---|---|---|
| Oblivion.esm | 9,892 (2,393 SCPT + 1,870 QUST stages + 5,629 INFO) | **1** (SE02FIN → SE02Conv, a true rename) |
| Knights.esp | 495 (194 SCPT + 146 QUST stages + 155 INFO) | **5**, across the four failing quests (ND00 twice, ND03, ND07, ND09) |

Oblivion's other 22 stage scripts with unresolvable names have **no** unspelled
SCRO — master-owned records and local variables, correctly left alone. Full
Oblivion `--scripts-only` rebuild after the change: 16,518/16,518 compile, and
exactly ONE of the 16,518 `.psc` files differs from the pre-change baseline.

### Handlers that name a property directly need their own hook

`_convert_ref` and the bare-identifier path in `_convert_expression` both
consult the alias map, but `startquest`/`stopquest`/`getquestrunning` build the
property name themselves via `_safe_property_name` and bypass both — that is
why SE02 still emitted `SE02FIN.Start()` after the first fix. Any new handler
that skips `_convert_ref` must call `_scro_alias_for()` itself.

## Zero-argument commands must be ROUTED or they survive undefined
<a id="zero-argument-commands-must-be"></a>

A command taking no arguments is ALWAYS read bare, so it never reaches
`_emit_function` through the call path — it must be in `_BARE_NO_EQUIV_COMMANDS`
(with a `FUNCTION_MAP` entry) or the name reaches the compiler as an undefined
identifier and fails the CHECKER. Added 2026-08-22:

- `getcurrentweatherpercent` — the spelled-out form of `getweatherpercent`, used
  by Knights' `ND08WraithSCRIPT`. Both now reach the real handler and return
  `Weather.GetCurrentWeatherTransition()` (0..1). `getweatherpercent` was
  previously caught by a stub list that returned a constant `0`, which made
  every `< 0.1` "still transitioning" test permanently true.
- `isplayerslastriddenhorse` — the other authored spelling of
  `GetPlayerHasLastRiddenHorse` (both are engine function `0x1153`, confirmed via
  `tools/misc/uesp_lookup.py`). No Skyrim equivalent; neutralised to `0` with an
  `;NE:` marker.

## A raw FormID in a FORM-ARGUMENT slot is never a numeric literal
<a id="raw-formid-form-argument-slot"></a>

`_convert_expression`'s bare-identifier path only reinterprets a **6-8 digit**
token as a FormID, because anywhere else a short run of digits is an ordinary
number. In an argument slot the engine reads as a FORM there is no such
ambiguity, and the LOW ids are the ones scripts write by hand: Knights'
`ND10TimeStopSpellScript` tests `GetIsID 7`, i.e. the Player NPC_ at
`0x00000007`.

Left a literal it produced `Form == Int` (checker error, no `.pex`) plus a
phantom `Form Property d7` — `_safe_property_name` prefixing the digit.
`_form_operand_edid()` resolves any 1-8 digit token in such a slot.

**An `ActorBase`-typed `Player` property must bind to `0x7`, not `0x14`.** Both
binders (`dialog_converter`, `object_scripts`) hardcoded the reference id; the VM
refuses a reference into an `ActorBase` property and the script's whole init
aborts. Skyrim's Player ActorBase is `0x00000007`, the same id as TES4's.

## TES4's destroyed flag has no Papyrus READER — mirror it in a FormList (2026-08-27)
<a id="tes4s-destroyed-flag-has-no"></a>

**Symptom.** Closing the Kvatch Oblivion gate teleported the player out
correctly, but the gate stayed standing and MS48 never advanced past stage 10.

**Cause.** `MS48OblivionGateScript`'s only `setstage ms48 50` is gated on
`getdestroyed == 1`:

```
begin gamemode
  if getdisabled == 1
    return
  endif
  if getdestroyed == 1 && getstage ms48 < 50
    setstage ms48 50
  endif
```

**What `SetDestroyed` actually does**, per the CK wiki's own page:

> "Objects that have been Destroyed no longer present mouseover text and
> cannot be activated. Note that they still exist, and continue to render and
> process events normally — they are **not Disabled or Deleted**, and their
> visual **Destruction State, if any, is unaffected**."

So it is *only* non-interactability. Three states share the word "destroyed"
and are all distinct — in Oblivion.exe they are literally different bits of
`[ref+8]`:

| State | Oblivion bit | Read in Papyrus by |
|---|---|---|
| destroyed **flag** | `0x2000` | *(no member — see below)* |
| enable state | `0x800` | `IsDisabled()` |
| DEST destruction **stage** | *(not a flag)* | `GetCurrentDestructionStage()` |

**Availability of the reader.** Skyrim exposes the setter to Papyrus
(`ObjectReference.psc:553`) but **not** the getter. `GetDestroyed` is real —
it is a console command (`0x10CB` / 4299) and a condition function (CTDA
index 203, no params, per xEdit `wbDefinitionsTES5.pas:336`) — but it has no
`ObjectReference` member: **0 hits across every vanilla `.psc`**, and it is
absent from the CK wiki's ObjectReference member list.

The CTDA route is not usable either, and not worth building: **0 of 134,748
conditions** across all 16 exported plugins use function 203.

**This conversion never writes a DEST subrecord** (grep `tes5_import/` — the
signature appears only in comments), so `GetCurrentDestructionStage()` returns
0 for *every* converted record. Both spellings of `getdestroyed` were
therefore dead reads that could never become true, and every quest advancing
off its own destruction was stuck.

**Do not shadow it in a script Actor Value.** `SetActorValue`/`GetActorValue`
are declared on `Actor.psc` (lines 521/143), **not** on `ObjectReference`, so
they do not compile against the things TES4 destroys — the Kvatch gate is a
`DOOR`, and the rest are activators, statics and trap triggers.

**The fix.** A conversion-owned FormList, `TES4DestroyedRefs`:

* `tes5_import._create_destroyed_formlist` mints it at
  `writer.chargen_fid_base + 0x44`, in the already-reserved 0x800 gap beside
  the ForceCombat FACT pair — a fixed slot, so **no FormID drift**.
* `TES4Polyfill.SetDestroyed(ref, list, bool)` calls the real native *and*
  mirrors the write into the list; `GetDestroyed(ref, list)` reads it back.
  `AddForm` / `HasForm` / `RemoveAddedForm` are native and work on any
  reference type, and script-added entries persist in the save.
* Every writer routes through the polyfill — `setdestroyed` is a special
  handler in `converter.py`, **not** a `constants.py` direct-native mapping.
  A direct mapping there silently bypasses the mirror and reproduces the bug.
* `CloseCurrentOblivionGate` / `CloseOblivionGate` / `DestroyAfterAnimation`
  all take the list and go through the same setter.

**Measured in the built artifacts (Oblivion.esm):** FLST `0118E17B`
`TES4DestroyedRefs`; 138 `SetDestroyed` writers, 26 `GetDestroyed` readers and
19 `DestroyAfterAnimation` calls across 69 scripts; 89 VMAD properties, all
bound to `0118E17B`, none misbound; zero remaining bare-native writes and zero
`GetCurrentDestructionStage` reads. `DOOR 011778C8` (MS48OblivionGate) and the
sigil-stone scripts are both bound, so the read and the write meet.

It is general, not gate-specific: tripwires, breakaway planks, cave-ins,
pressure plates, crumbling walls, Elven statues, the MQ06 Paradise portal and
all 20 gate-closing scripts use the same mechanism, including the
`setDestroyed 0` re-arm path (`TES4Polyfill.SetDestroyed(Self, list, false)`).

## Closing an Oblivion gate is the destroyed FLAG and nothing else (2026-08-27)
<a id="closing-oblivion-gate-destroyed-flag"></a>

Decompiled from `Oblivion.exe` (the Nehrim install), so this is the engine's
own answer rather than an inference:

| | Address | What it does |
|---|---|---|
| `CloseOblivionGate` handler | `0x515ef0` | opcode `0x10DE` |
| `CloseCurrentOblivionGate` handler | `0x515d20` | opcode `0x10C0` |
| destroyed-flag setter | `0x46aa50` | `or [ref+8], 0x2000` / `and ...,~0x2000` |
| `GetDestroyed` handler | `0x4f82c0` | `[ref+8] >> 0xD & 1` — same bit |
| `Disable` handler | `0x50a240` | tests/sets bit `0x800` — a DIFFERENT flag |

Walking every call target transitively from `0x515ef0`: the flag setter
`0x46aa50` **is** reachable; `Disable` `0x50a240` is **not**, at any depth.
So closing a gate sets one bit and does nothing else.

**Then why does the gate visibly disappear?** Because the visible portal is an
*animation*, not the reference's presence. The gate NIF
(`Oblivion\Gate\OblivionArchGate01.NIF`) carries exactly three sequences —
`Forward`, `Backward`, `SpecialIdle` — and the gate's own `GameMode` re-issues
the looping one only while it is not destroyed:

```
if GetDisabled == 0 && GetDestroyed == 0
    if IsAnimPlaying == 0
        playgroup specialidle 1
```

Once the flag is set that branch stops running, the loop is no longer
re-issued, and the portal closes itself. UESP's "the gate ... disappearing" is
describing this, not a `Disable`.

**Do NOT `Disable()` the gate.** Two independent reasons:

1. Oblivion never does (measured above), and Bethesda's own MQ14 stage script
   closes three gates with bare `CloseOblivionGate` while disabling only a
   *sound* marker (`MQ13Gate2Sound.disable`) — proof they reached for
   `.disable` when they wanted it, and did not here.
2. It would break the quest. The gate's poll opens with
   `if getdisabled == 1 / return`, *above* the `getdestroyed` stage check, so
   a disabled gate can never run its own `setstage`.

It is also not available: Skyrim refuses `Disable()` on a reference with an
enable-state parent (`SkyrimSE.exe`: "cannot disable an object with an enable
state parent" — and symmetrically "cannot enable ..."), and `MS48KvatchGate`
has `XESP.Reference=00091229`. Disabling that parent instead is wrong too —
`MQ13CountessBattleMarker` parents all four MQ13 gates, so it would close
gates the script left open.



## Script conversion: known defects found during the parse-tree rewrite
<a id="script-conversion-known-defects"></a>

The `script_convert/` rewrite (see the parse-tree plan) reproduces **current
behaviour, bugs included** — the tree path emits the same wrong thing the regex
path emits, and defects are recorded here instead of being fixed inline. Fixing
is separate work, done on a foundation where the fix is one transform rather
than one more repair pass.

Each entry says how it was measured and what is *not* yet known, so nothing here
gets treated as more certain than it is.

---

## 1. Cross-plugin script types are a BUILD-ORDER dependency (measured 2026-08-28)
<a id="1-cross-plugin-script-types"></a>

**Status:** not a runtime defect. Recorded because it is invisible to both
whole-tree repair passes and will matter to stage 5.

A converted script can declare a property typed as a script owned by another
plugin, which its own output directory does not contain:

| Plugin | Distinct missing script types | Property declarations | Files |
|---|---|---|---|
| Translation.esp | 165 | 830 | 179 |
| Knights.esp | 14 | 42 | 25 |
| Morrowind_ob.esm | 5 | 10 | 4 |
| **Nehrim.esm** | **0** | 0 | 0 |
| **Oblivion.esm** | **0** | 0 | 0 |

The split is exact: **every plugin with masters has them; both standalone
plugins have none.** Examples — `TES4_AltaroftheNine`, `TES4_FXDustFall01SCRIPT`
and `TES4_TG03Main` are declared by `Knights.esp` scripts and defined in
`Oblivion.esm`; `TES4_HMSfromFloat24h` is declared by
`Translation.esp/TES4_AAWaitMenuActorScript.psc` (which calls `.TES4Call()` on
it three times, from OBSE `Call HMSfromFloat24h GameHour`) and defined in
`Nehrim.esm`.

**Verified**: every one of the sampled missing types IS generated into its
owning plugin's output (`TES4_AltaroftheNine.psc` etc. are present under
`output/Oblivion.esm/`), and all plugins deploy to the same `Data/Scripts/`
folder, so the `.pex` resolves at runtime. `tools/script/compile_papyrus.py`
already carries `--extra-headers` for exactly this case.

**Not yet known**: whether every one of the 165 Translation.esp types resolves
(only a sample was checked), and whether a *compile* of one plugin in isolation
fails without `--extra-headers`. Neither affects a full-pipeline build.

**Why it matters to the rewrite**: `_comment_undeclared_identifiers` cannot see
this class at all — the property *is* declared, it is just typed as a script
absent from this output tree. A symbol table spanning plugins can check it; the
grep passes cannot.

---

## 2. Shadowed command handlers in `_emit_function` (measured, pre-existing)
<a id="2-shadowed-command-handlers-emitfunction"></a>

Six TES4 command names have two competing branches in the 201-branch chain, one
of them unreachable. Dated with `git log -L`, they split into two opposite
kinds:

**(a) Superseded corpses — safe to delete, zero output change.** The
earlier-in-file branch is the *newer* commit; a better handler was added above
the old one and the corpse left below: `getpcisrace` (L7338 shadows L8166),
`ispcexpelled`/`getpcexpelled` (L6391 shadows L8016), `isexpelled`, `expel`
(L7380 shadows L8008).

**(b) Unreachable NEW code of UNKNOWN correctness.** Commit `fd04769`
(2026-07-28, "handle OBSE extensions") added implementations that an older stub
~1,100 lines above silently defeats:

- **`forceflee`** — the new branch emits `SetActorValue("Confidence", 0)` +
  `EvaluatePackage()`; the April stub at L7299 returns `;NE: ForceFlee` + `0`
  and wins. Its sibling name `flee`, added in the same commit, **does** reach
  the new code — so one commit's two names behave differently today.
- **`positioncell`** — the new branch emits `SetPosition(x,y,z)` + `SetAngle`;
  the 2026-07-20 stub at L6604 wins. `positionworld` works, `positioncell`
  returns `0`.

**This code has never executed.** It was shadowed at birth, so it has never
produced a line of output or been seen in game, and its rationale comment is an
argument rather than evidence — it is *not* known to be better than the stub it
lost to. Enabling it changes output and needs an in-game test; it is out of
scope for the rewrite.

**A precedence inversion, not a duplicate**: `getbookread` is in `_NO_OP_FUNCS`
(L7477) but `bookread` is not, so the membership test at L7500 claims
`getbookread` and the later L8503 branch is reachable only for `bookread`.
Deleting that branch wholesale would change `bookread`'s emitted comment text;
only the `'getbookread'` tuple entry may be removed.

---

## 3. Two latent scanner bugs — both FIXED in stage 3 (measured 2026-08-28)
<a id="3-two-latent-scanner-bugs"></a>

Found while replacing the hand-rolled scanners with the lexer. Both were cases
where the old character-level code did the wrong thing on input the corpus
happens not to contain, so neither changed emitted output — verified by a
zero-diff rebuild of all 38,612 scripts across four plugins after each fix.

**`_split_logical` was not quote-aware.** It tracked parenthesis depth but not
string state, so `MessageBox "a || b"` split into two parts. Verified over
413,210 comparisons: no corpus script has `||` or `&&` inside a string literal.
**Fixed** — the parser-based replacement is quote-aware by construction.

**Two regexes missed digit-leading EditorIDs.** An Oblivion EditorID may start
with a digit (`"1TrapFireMineWorldRef"`, `"2akulaSdoorSa"` — 118 lines, 16
distinct ids, across Nehrim, Morrowind_ob and Translation), but both
`_QUOTED_MEMBER_RE` and `_QUOTED_NAME_RE` required a letter or underscore
first, so those kept their quotes. Only `_safe_property_name` saved them: it
strips the quotes *and* prefixes the `d` that makes the name legal Papyrus, so
quoted and unquoted spellings happened to normalise to the same property
(verified for all 12 sampled ids). Anything reading the name without going
through it would have hit the `_MQ01Tate_` failure that
`_QUOTED_NAME_RE`'s own comment describes — a property bound to nothing,
throwing at every use.

**Fixed** — both name classes widened to `\w+`, and `_unquote_identifiers`
now delegates to `parser.unquote_member_names`, where "a quoted name touching
a `.`" is a structural test rather than a lookahead/lookbehind pair with a
second character class to keep in sync. Verified identical on all 206,612
source lines before the substitution.

**A non-finding, recorded so it is not re-investigated:**
`d1TrapFireMineWorldRef.MoveTo(d1TrapFireMineWorldRef)` looks like an object
being moved to itself, but it is the deliberate conversion of TES4
`Reset3DState` (`converter.py`, `fname_low == 'reset3dstate'`) — `MoveTo(self)`
is the Skyrim idiom for forcing a 3D reset.

---

## 4. Authored typos in source scripts (measured, not our bug)
<a id="4-authored-typos-source-scripts"></a>

The parser degrades an unparseable line to a `Raw` node rather than failing the
script — Oblivion's own compiler was permissive, and a script that fails to
convert takes down every other script declaring a property of its type. Across
all 19,013 script bodies in 10 plugins there are **17** such lines, every one an
authored typo:

- `MG09Script` line 132 (Oblivion.esm): a stray `` ` `` after `endif`.
- `SE09BodyPartActivatorScript` (Oblivion.esm): a bare `:` where the author
  meant `;`, so a comment line lexes as code.
- `AkarusScript`, `MelvinScript`, `AchievementsQuestScript` (Nehrim.esm): bare
  `-----` / `:= == ==` separator lines with no leading `;`.

No action needed; recorded so a future session does not re-investigate them.

---

## 5. Divergent block scanners in the repair passes — FIXED in stage 4 (measured 2026-08-28)
<a id="5-divergent-block-scanners-repair"></a>

Four post-emit passes each re-derived Papyrus block structure from text, with
their own keyword spellings, and disagreed. The disagreements were invisible
because they only bite on shapes the emitter does not currently produce —
exactly the class of latent defect §3 records.

**`_remove_dead_code_after_return` did not know `If(`.** `_balance_if_endif`
matched `if ` *and* `if(`; the dead-code pass matched only `if `. So a `Return`
inside an `If(x)` block counted as top-level, and **every statement after that
block was rewritten to `;  <line>  ;dead code after Return`** — live code
silently commented out, including the `EndIf` itself:

    Event A()          old ->  Event A()
    If(x)                      If(x)
    Return                     Return
    EndIf                      ;  EndIf  ;dead code after Return
    foo                        ;  foo  ;dead code after Return
    EndEvent                   EndEvent

**A third divergence, same shape**: `_hoist_quest_start_above_writes` carried
its own barrier list (`_HOIST_STOP_RE`) which matched a bare `Function` but
not a typed `Int Function` header — so a `Quest.Start()` could in principle
hoist ACROSS a function boundary into an unrelated body. Also unreachable:
walking back from all 40,586 files' `Start()` sites crosses a typed header
**0 times**. A 200,000-case randomised differential found the loop rewrite
byte-equivalent to the old cursor loop once the barrier was held constant,
and every divergence with it unpinned was this hardening.

**Not currently reachable**: censused all 40,586 generated `.psc` — **0 lines**
begin `If(`, `While(` or `ElseIf(`; the emitter always writes a space. The
pass also missed `While(` openers and typed `Int Function` headers (which
`_balance_if_endif` did handle), both harmless for the same reason.

**Fixed** — `script_convert/tes5/blocks.py` classifies an emitted line once
(`classify`) and resolves depth/stack once (`scan`); the passes consume `Line`
records and no longer mention a keyword. A future emitter change to `If(` now
lands on every pass at once instead of on one of them. Verified: the
scan-based rewrite is byte-identical to the old logic on all 40,586 files, and
a 24-case adversarial suite of unbalanced input agrees everywhere except this
bug.

---

## 6. Two divergent boolean-function lists (measured 2026-08-28)
<a id="6-two-divergent-boolean-function"></a>

`_BARE_BOOL_FUNCTIONS` (constants.py, 21 names) and the `_BOOL_FUNC_NAMES`
regex inside `_convert_expression` (34 names) both answer *"does this TES4
function return a boolean?"* — and agree on only **10**. Which collapse a call
receives depends on which list happens to name it: `ref.IsDisabled == 1`
collapses to `ref.IsDisabled()`, `ref.GetDetected == 1` does not.

**Reachable, and wide**: 24 names are in the regex only, used **3,577 times
across 1,944 scripts** (`isactionref` 1,597, `getincell` 688, `getstagedone`
447). 11 names are in `_BARE_BOOL_FUNCTIONS` only.

**Deliberately NOT fixed during the parse-tree rewrite.** Unifying the lists
changes ~1,944 scripts, which would swamp the rewrite's semantic-diff gate and
make an emitter bug indistinguishable from this fix. The rewrite's expression
emitter reads ONE table (`_BARE_BOOL_FUNCTIONS`), so the union lands as a
one-line table edit once the rewrite is verified — at which point the diff is
attributable and reviewable on its own.

---

## 7. `this` → `Self` substitution leaked INTO string literals — FIXED by the tree emitter (2026-08-28)
<a id="7-this-self-substitution-leaked"></a>

`_convert_expression`'s terminal substitution pass rewrites the TES4 keyword
`this` to Papyrus `Self` with a regex over the whole expression **text**, so it
also fires inside a quoted string:

```
authored:  "... Almalexia.esp detected. This file is deprecated ..."
shipped:   "... Almalexia.esp detected.Self file is deprecated ..."
```

Both the space and the word are destroyed, in **player-facing** message text.

**Measured**: 9 lines, all in `TES4_mwFnCheckInstallation.psc` (Morroblivion's
installation-warning banner). Narrow only because few converted scripts build
long English sentences.

**Fixed** by the parse-tree emitter, structurally rather than by a better
regex: a `Literal` node with `is_string` set is emitted verbatim and no
substitution pass can reach inside it. This is the class of defect the rewrite
exists to make unrepresentable — the same shape as the `;NE:`-inside-an-
expression family.

---

## 8. A local variable named like a built-in was shadowed by the FUNCTION — FIXED by the tree emitter (2026-08-28)
<a id="8-local-variable-named-like"></a>

`fbmwMercCalvusScript` declares `short isdead` and later tests `if isdead == 1`.
Oblivion resolves that to the VARIABLE — a local always wins over a command
name. The string path checked its boolean-function tables before its local
table, so it emitted the call instead:

```
authored:  short isdead   ...   if isdead == 1
shipped:   If (Self as Actor).IsDead()      ← reads the ACTOR, not the variable
correct:   If isdead                        ← reads the declared property
```

The script also declares `Int Property isdead Auto Conditional`, so the
emitted call ignored a property the quest actually writes.

**Fixed** by the parse-tree emitter, which resolves a bare `Ident` against the
script's own declarations before consulting any command table. Found by the
semantic diff (`calls[isdead/0]: 1 -> None`), not by a compile failure — the
old output compiled perfectly and simply did the wrong thing.

**Scope**: 1 script measured across the four verified plugins. Narrow because
few TES4 authors name a variable after a command.

---

## 9. `SetPos <axis>, <value>` wrote the WRONG AXIS — FIXED (2026-08-28)
<a id="9-setpos-axis-value-wrote"></a>

TES4 separates arguments with whitespace, a comma, or both, so
`Ref.SetPos Z, PlacePosZ` is as legal as `Ref.SetPos Z PlacePosZ`. The
handler split on whitespace only:

```python
parts = args_str.split(None, 1)      # 'Z, PlacePosZ' -> ['Z,', 'PlacePosZ']
axis  = parts[0].upper()             # 'Z,'  -- not in {X, Y, Z}
```

`'Z,'` fails the axis test and the lookup falls back to its X default, so the
value is written to the **X** coordinate:

```
authored:  Ref.SetPos Z, PlacePosZ
shipped:   Ref.SetPosition(PlacePosZ, Ref.GetPositionY(), Ref.GetPositionZ())
correct:   Ref.SetPosition(Ref.GetPositionX(), Ref.GetPositionY(), PlacePosZ)
```

**Measured**: 27 sites in 10 scripts across the four verified plugins,
including Morroblivion's `JDLevitate` and `mwRotationFix` and Nehrim's
`1MarkFxEffectScript`. `SetAngle` shares the handler and the defect.

**Fixed** by splitting on `,`-or-whitespace. Found by the statement
differential — the tree path joins arguments with `", "` and produced the
correct axis, which made the old path's output the outlier.

---

## 10. `GetLOS` was listed as taking no arguments — FIXED (2026-08-28)
<a id="10-getlos-was-listed-as"></a>

`_ZERO_ARG_REF_FUNCTIONS` exists so that `StopCombat, Player` resolves to
`Player.StopCombat()` — for a command that takes nothing, the token after a
leading comma is the RECEIVER, not an argument.

`getlos` was in that set, and it takes a TARGET: `GetLOS, Player` asks
whether **Self** can see the player. Promoting the argument inverted the
question and dropped it:

```
authored:  if ( GetLOS, Player == 1 )
wrong:     Game.GetPlayer().HasLOS()      ← the PLAYER's line of sight, to nothing
correct:   (Self as Actor).HasLOS(Game.GetPlayer())
```

It does not even compile ("function takes 1 parameters not 0"), which is how
it surfaced: **9 Nehrim scripts** failed once the parse tree started
preserving the leading comma. Before that the comma was discarded upstream,
so the promotion never fired and the bad table entry was inert.

**Fixed** by removing `getlos` from the set. Audited the other 61 entries
against their argument counts; it was the only one wrong.

---

## 11. Multi-button `MessageBox` degraded to a plain text box — FIXED (2026-08-28)
<a id="11-multi-button-messagebox-degraded"></a>

`_convert_function_call` split a command line with two regexes:

```python
ref_m = re.match(r'^(\w+)\.(\w+)\s*(.*)', stripped, re.IGNORECASE)
func_m = re.match(r'^(\w+)\s*(.*)', stripped, re.IGNORECASE)
```

The argument tail then reached `_emit_function` as raw TEXT, and every handler
re-split it — on whitespace, on commas, or on both. That tears a quoted
argument apart at the first separator inside it, and a `MessageBox` is
mostly quoted arguments:

```
authored:  messagebox "Do you steer by the stars of the Lover?", "No", "Yes"
shipped:   Debug.MessageBox("Do you steer by the stars of the Lover?")
correct:   TES4_MsgButton = TES4_ShowMsg(TES4Msg_DoomstoneLoverScriptNEW_01)
```

The buttons were dropped, so the box became a notification the player could
only dismiss — the Doomstone asks a question that could never be answered.

**Measured**: 39 button menus restored and 81 `Message` properties added
across the four verified plugins. The same split also ate the space after a
sentence-ending period (`"...the crowd.He screams for help."`) in 232 strings,
because the tail was re-joined with single spaces after being split.

**Fixed** by PARSING the line instead: `_convert_function_call` now builds a
`Call` node and hands `_emit_function` the parsed `args`, so arguments are
separated once, by the parser, and a quoted literal is one token.

---

## 12. `pms <shader>, <n>` created a second, unbound property — FIXED (2026-08-28)
<a id="12-pms-shader-n-created"></a>

Branches read their first argument as `args_str.strip().split()[0]`, which
keeps the SEPARATOR on the token when the source uses the comma form. The
name then went through `_safe_property_name`, which sanitises the comma to an
underscore:

```
pms effectDrain 5   ->  property `effectDrain`
pms effectDrain, 5  ->  property `effectDrain_`     ← a different property
```

Both spellings mean the same shader, so a script using both declared two
properties for one record and only one of them was ever bound.

**Fixed** by the `arg_src()` / `arg_srcs()` accessors, which read the parsed
argument nodes; the separator is gone before the name is seen. Affects
`pms`, `sms`, `pme`, `sme` and `showmap`.

---

## 13. Twelve commands were treated as unknown by the node path — FIXED (2026-08-28)
<a id="13-twelve-commands-were-treated"></a>

`_is_known_command` gates whether `name <args>` is a call at all; an unknown
name becomes `;TODO:` over the whole line. It tested a hand-kept list of
tables, and the branch chain in `_emit_function` had grown twelve names that
appeared in none of them — `setforcerun`, `resethealth`, `setgamesetting`,
`getcrosshairreference` and nine others.

While only the string path reached the command layer this was invisible: that
path never asked the question. Routing statements through the node path made
it live, and `setforcerun 1` — the SpeedMult write — became `;TODO:` in 62
statements.

**Fixed** by deriving `_BRANCH_ONLY_COMMANDS` from the chain itself rather
than maintaining a parallel list. `foreach` is deliberately excluded: it is a
statement keyword intercepted before the command layer.

---

## 14. `GetDayOfWeek` had two conversions and the worse one won — FIXED (2026-08-28)
<a id="14-getdayofweek-had-two-conversions"></a>

The command was converted in two places that did not agree:

| Path | Emitted |
|---|---|
| `FIXED_PROPERTY_CALLS` (a call) | `(GameDaysPassed.GetValueInt() % 7)` |
| a branch in the bare-identifier path | `(GameDaysPassed.GetValue() as Int) % 7` |

`GetValue()` returns Float, so the second form typed the whole expression
Float. Assigning it to a TES4 `short` then attracted a SECOND cast:

```
DayofLastUse = (GameDaysPassed.GetValue() as Int) % 7 as Int
```

Which spelling a script got depended only on whether the author wrote the
command bare or as a call — the same command, two answers.

**Measured**: 42 call sites across Knights.esp and Morrowind_ob.esm.

**Fixed** by deleting the duplicate branch and routing both spellings
(`getdayofweek`, `getdayoftheweek`) to the table. Found by the S1 typing
harness: `symbols.type_of_expr` typed the expression Int from the tree while
the old text scan typed it Float, and the disagreement was the bug.

---

## 15. `FUNCTION_MAP` silently drops 20 entries — LATENT (2026-08-28)
<a id="15-functionmap-silently-drops-20"></a>

The literal has **537 keys but evaluates to 517**: 17 keys are written more
than once and Python keeps only the last. Four of them carry *different*
values, so a working mapping is overwritten by `(None, ...)`:

| Key | Earlier | Later (wins) |
|---|---|---|
| `getcontainer` | `('GetContainer', True, None)` | `(None, True, None)` |
| `setdoordefaultopen` | `('SetOpen', True, None)` | `(None, True, None)` |
| `setdisplayname` | `('SetDisplayName', True, None)` | `(None, True, None)` |
| `getinfame` | `(None, False, None)` | `(None, True, None)` |

**Not currently a live defect**: three of the four are rescued by an explicit
branch in `_emit_function` (which is *why* those branches exist), and
`setdisplayname` correctly degrades because Skyrim needs SKSE for it. But the
duplicates are invisible, and a future edit to the earlier entry would have no
effect. To be resolved when the command tables merge into one row per command,
where a duplicate key is detectable.

---

## 16. Two disagreeing lists of Bool-returning Papyrus names — FIXED (2026-08-28)
<a id="16-two-disagreeing-lists-bool"></a>

The same fact — "does this Papyrus function return Bool" — was recorded twice:

| Where | Form |
|---|---|
| `constants.PAPYRUS_BOOL_FUNCTIONS` | a `set` of 53 names |
| `converter._BOOL_FUNC_NAMES` | a regex alternation of 33 names |

They disagreed by **twelve names** — `IsDetectedBy`, `HasLOS`, `CanSee`,
`GetDetected`, `IsAnimPlaying`, `IsRidingMount`, `IsHostileToActor`,
`IsWeaponDrawn`, `IsChild`, `IsAlarmed`, `IsCompleted`, `IsObjectiveCompleted`
— so whether a Bool got its `as Int` depended on which list the code path
happened to consult. `Temp = Player.IsDetectedBy(x)` reached the set (which
lacked it), got no cast, and failed to compile:

```
Checker error: value with type `Bool` cannot be assigned to a variable with type `Int`
```

**Fixed** by merging the twelve into `PAPYRUS_BOOL_FUNCTIONS` and DERIVING the
regex from it, so there is one list. A side effect, and the intended one: a
`GetLOS Player == 0` now knows its left side is Bool and collapses to
`!(...HasLOS(Player))` instead of comparing a Bool to `0` — 21 scripts.

**Also fixed in the same pass**: `RETURN_TYPES` is keyed by the bare Papyrus
method, but `FUNCTION_MAP` maps some commands to a QUALIFIED name
(`rand` -> `Utility.RandomFloat`). Without stripping the class prefix,
`set randint to Rand 1 5` looked untyped and lost its cast in 4 Morroblivion
scripts.

---

## 17. Type coercion guessed from emitted text — REPLACED (2026-08-28)
<a id="17-type-coercion-guessed-from"></a>

`_coerce_float_to_int` decided whether an assignment needed `as Int` by
running four scans over the ALREADY-EMITTED Papyrus: a Float-function regex,
a `\d+\.\d+` literal probe, an identifier sweep looking up each name, and a
Bool-function regex. All four re-derive the value's type from its rendering,
where a command name inside a string literal counts as a call and the shape of
the arithmetic is invisible.

Replaced by `symbols.type_of_expr`, which types the value from its PARSE TREE
before any text exists. Verified by differential harness over the corpus:

| Plugin | Assignments to Int targets | Disagreements |
|---|---|---|
| Oblivion.esm | 1,076 | 0 |
| Nehrim.esm | 1,133 | 0 |
| Morrowind_ob.esm | 1,414 | 0 |
| Knights.esp | 570 | 0 |

Getting to zero is what surfaced §14 and §16 — both were cases where the tree
and the text scan disagreed, and the tree was right.

---

## 18. Operator precedence encoded twice, and the copies disagreed — LATENT (2026-08-29)
<a id="18-operator-precedence-encoded-twice"></a>

`tes4/parser._PRECEDENCE` (six tiers, which the parser BINDS by) and
`emit/expr._PRECEDENCE_RANK` (five ranks, which the emitter PARENTHESISES by)
were written out separately and did not match: the parser gives `==` and `<`
their own tiers, the emitter collapsed both to rank 2.

The emitter parenthesises a child only when it binds LOOSER than its parent, so
the disagreement drops the parens on an equality nested under a relational
operator. `(a == b) < c` emitted as `a == b < c`, which Papyrus re-reads as
`a == (b < c)` — a different expression. Twelve operator pairs changed
parenthesisation once the tables were unified.

**Not observed in any script.** Censused all 6,364 exported TES4 scripts, 4,010
of which use a relational operator: **zero** occurrences of the shape. It cannot
change current output, which is why the semantic diff is unmoved.

Fixed by deriving both from `tes4/lexer.PRECEDENCE` — the lowest layer the
parser and the emitter both reach (`tes4/*` is stdlib-only, so `constants.py`
was not available).

---

## 19. `Activate` drops its arguments when the caller passes nodes — LATENT (2026-08-29)
<a id="19-activate-drops-its-arguments"></a>

The `activate` branch read its arguments as

```python
parts = self.arg_srcs(args_str) if args_str else []
```

`arg_srcs` reads the parsed argument NODES, but the guard tests the parallel
SOURCE-TEXT channel. A caller supplying nodes with an empty `args_str` would
have had every argument silently discarded — the activator and the run-flag
both lost, emitting a no-argument `Activate()`.

**Not reachable today**: measured 0 occurrences over 6,082 scripts, because the
only caller that supplies nodes also built the text. It was one caller away, and
removing the second channel (both are now derived from the nodes) makes it
unreachable by construction rather than by luck.

## 20. Feature flags scanned from raw source matched COMMENTS — FIXED (2026-08-29)
<a id="20-feature-flags-scanned-from"></a>

Six per-script flags were set by scanning the lowercased source as text:

```python
self._uses_timer = bool(re.search(r'\btimer\b', source_low))
self._uses_say   = bool(re.search(r'\bsay(?:to)?\b', source_low))
```

A text scan cannot tell a call from the same letters inside a comment or a
string literal. Measured over 6,082 exported scripts, tree-derived facts against
the text scans:

| Flag | Scripts the text scan got wrong |
|---|---|
| `uses_timer` | **122** |
| `uses_say` | 8 |
| `uses_getsecondspassed` | 7 |
| `elapsed_is_realtime` | 6 |
| `uses_say_timer` | 1 |

Every difference is the same direction — the scan says true, the tree says
false — and every sample is a COMMENT: `;Timer for pirate placement`,
`;Float Timer`, whole commented-out `;Begin GameMode` blocks.

`_uses_timer` picks the poll interval in `_get_update_interval`, so **122
scripts polled every 0.25s when they should poll every 0.5s** — twice the VM
load, forever, because of a word in a comment. `DLCOrreryConsoleScript`,
`DLC06FletcherScript` and `ND02BattleControlSCRIPT` are among them.

Replaced by `script_convert/facts.py`, which derives all six from the parse
tree. Semantic diff 475 -> 515: 40 scripts whose interval is now correct.

---

## 21. `Set X to <literal>` dropped when the block filter was unconvertible — FIXED
<a id="21-set-x-literal-dropped"></a>

168 Nehrim scripts (`EP0001Kuecken` et al.) lost `Set EPWert to 15`. `EPWert`
is the argument to the OBSE XP-award call in `OnDeath`, so every affected
creature awarded **0 XP**.

Cause: a `begin OnHitWith <weapon>` filter was judged unconvertible whenever
the body had already bound the weapon as a property under its own narrow type
(`Weapon`), and the whole body was then emitted commented out. A base record
compares to a `Form` event parameter perfectly well. `_block_filter_guard` now
accepts `existing in _BASE_OBJECT_PAPYRUS` against a `Form` parameter.

Same cause, same fix: `TES4_CGRopeBucketScript` — shooting the CharacterGen
rope bucket with an iron arrow advances MQ01 to stage 58, and the body was
disabled — plus 8 more Nehrim scripts (`MQ06Golem01Script`: `RemoveSpell`,
two `EffectShader.Stop`, a `Say`). Nehrim went from 8 unconvertible filters
to 0; Oblivion's remaining 8 are genuine (no parameter carries the filtered
object).

---

## 22. `If True` where one `&&` term had no equivalent — FIXED
<a id="22-if-true-where-one"></a>

4 Nehrim spell scripts (`SpellEinfrieren10Prozent`) collapsed
`Target.isActor == 0 || Target.IsDead() || Target.IsOnMount()` to `If True`,
so the freeze fired on **every** target. `_logical` now keeps the convertible
terms and comments only the dead one.

---

## 23. Sentence spacing stripped from message text — FIXED
<a id="23-sentence-spacing-stripped-from"></a>

76 Oblivion scripts ran two sentences together in a `MessageBox`: the Arena
poster read `...valor and skill.Anyone can gamble...`. The authored SCTX has
the space; it was lost on the way out.

---

## 24. `setDestroyed 0` and `setDestroyed 1` both destroyed — FIXED
<a id="24-setdestroyed-0-setdestroyed-1"></a>

`SEObeliskNewSCRIPT`'s deferred-destroy rewrite dropped the boolean, so
`_deferred_destroy` turned BOTH directions into
`DestroyAfterAnimation(...)` — the "safety net for destroyed status" flipped
nothing. Only `... , true` defers now.

---

## 25. Three converter regressions in the parse-tree rewrite — FIXED
<a id="25-three-converter-regressions-parse"></a>

Found by diffing generated output against master head, not by compiling.

**An empty `OnActivate` was dropped.** In TES4 the PRESENCE of the block
consumes the activation, so an empty body is meaningful. Dropping it lost both
the consume and the door-relock preamble (`ND04TitanSCRIPT`,
`ARLesserWelkyndStoneStaticScript`). `events()` now keeps a block that
`_consumes_activation` reports, body or not.

**`TES4Polyfill.EnterOblivionGate` was never emitted.** The gate's identity is
known only on the authored line that discards it (`set MQ00.nearOblivionGate
to 0`), so the capture must precede the body. Re-added as
`assemble.gate_capture`; without it `CloseCurrentOblivionGate` had nowhere to
send the player back to.

**`ModPCSkill`/`AdvancePCSkill` emitted `(Self as Actor).ModActorValue(...)`**
— `None` on the Quest scripts that call it, so 12 scripts' skill gains did
nothing (`DAOghmaInfiniumScript` has 9). Both are player commands by
definition and now route to `Game.GetPlayer()`. `Game.AdvanceSkill` is NOT the
right target: per the CK wiki it adds skill-USAGE progress and "won't
necessarily change the Skill itself", where `ModPCSkill Blade 10` raises the
skill by 10.

**`TES4Polyfill.RestoreFallDamage` was never emitted** — `SuppressFallDamage`
writes a GLOBAL GMST, so leaving it set disables fall damage permanently. The
flag had no setter after the rewrite; now derived in `_load_facts` from the
tree, and the restore MERGES into the script's existing `OnEffectFinish`
rather than declaring a second one.


## script_convert: measurements and failure modes
<a id="scriptconvert-measurements-failure-modes"></a>

The working log behind the architecture contract.

### What the baseline IS

`temp/psc_semantic/` matches **HEAD**. The working tree is not expected to
match it: the current output is *HEAD plus the intentional bug fixes* recorded
in [script_conversion_bugs.md](../commentary/script_convert.md), which is the ledger
for this rewrite — 17 numbered, dated entries marked FIXED / LATENT / REPLACED,
each naming what was measured and in which scripts.

So the changed-count is **not noise and not debt**. It is the footprint of
those deliberate fixes, and every script in it should be traceable to a
numbered entry.

🛑 **If the count GROWS, you had better have a very good reason.** It is a
justification threshold, not a hard cap — a refactor legitimately uncovers real
defects, and §14, §16 and §17 of the ledger were all found exactly that way,
by the tree and the text scan disagreeing. What is forbidden is growth that
nobody looked at. When the number moves up:

1. Name every newly-changed script (`--show`).
2. Decide, per script, whether it is a fix or a regression.
3. A fix gets **a new numbered entry in `script_conversion_bugs.md`** in the
   existing format: what was measured, how many sites in which plugins, the
   authored-vs-shipped-vs-correct comparison, and how it was found.
4. A regression gets reverted.

Growth without steps 1-3 is how a rewrite quietly ships twelve behaviour
changes because "the number only moved a little".

A change is acceptable when **all four** hold:

1. **No CharacterGen script changed.** The tutorial dungeon is the most
   play-tested content in the project and every new game passes through it.
   `psc_semantic_diff` exits 1 and names the file. **Any gated diff is a
   regression until proven otherwise: revert first, diagnose second** — never
   explain it away in the pass that introduced it.
2. **Every semantic change is accounted for** in
   `script_conversion_bugs.md` — see the threshold rule above.
3. **All scripts compile, 0 failures.**
4. **No fitness metric moved away from its target.**

`Morrowind_ob.esm` is non-negotiable: the only plugin exercising
`ctx.master_export`, and at ~18,000 scripts the largest corpus.

🛑 **A stale baseline makes the whole guarantee meaningless.** Re-snapshot
(`psc_semantic_diff.py snapshot --all`) from a clean build before starting, and
confirm `compare` reports **0 changed** — that zero is what proves the net is
measuring the tree you are actually working from.

---

### S3 closed the round trip (2026-08-29)

`emit_call` flattened its parsed argument nodes back into a TES4 source string
and handed it to `_emit_function`, which re-split and re-parsed it. Measured
before cutting, since the cut depends on the two channels agreeing:

| Check | Result |
|---|---|
| Calls reaching `_emit_function` with nodes | 44,322 |
| Where `args_str` differed from the rebuilt node text | **0** |
| Expression conversions through the tree | 30,315 |
| Falling back to the string scanner | **0** |
| `parser.parse` calls per script, after | **1.000** |

`args_str` is gone from `_emit_function`, from all four argument accessors and
from the row engine's `_Args`. `_convert_expression` and `_tree_expression` are
deleted -- 38 call sites became `arg_expr(n)`, which emits from the node.

**reparse-round-trip measures the ROUND TRIP, not `emit_source`.** The old pattern counted every
`emit_source` mention, which was right while it fed the re-parse. It is now a
node->text formatter for `;NE:`/`;TODO:` markers and for keying a lookup on the
authored spelling -- neither re-enters the parser. The pattern was narrowed to
`_convert_expression(` / `_tree_expression` / `parse(emit_source` /
`tokenize(emit_source`, and reparse-round-trip is 0.

**One real regression, caught by the semantic diff and fixed.** Routing the
printf helpers at `_format_string_call` onto the node list made them ignore the
trimmed argument string their callers had built: `message "Rank %.0f Fireball",
SpellRank, 10` emitted the trailing display-duration as text
(`+ (10 as String)`), 73 scripts. Fixed by passing argument INDEXES down instead
of a rebuilt string, so the trim happens on nodes and both helpers stop
reconstructing text at all.

### ms-per-script is noisy; sample it before believing it

ms-per-script is a median of 5 runs x 40 iterations, which is not enough to reject
background load. It read 0.75-0.82 ms three times during S2 -- an apparent
1.8x regression -- while six clean samples on the same code gave 0.452-0.495
(median 0.465 against a 0.443 baseline, i.e. 1.05x). Every high reading
coincided with another job on the machine.

So a single ms-per-script flag is not evidence. Re-sample it 5-6 times with nothing else
running before acting; a real regression holds its value across samples.

### The baseline is not a scratchpad

`--update-baseline` used to write whatever it measured. That makes the whole
fitness suite advisory: add a violation, refresh the baseline, and
`--fail-on-regression` compares the file against itself and reports
`no regressions`. It happened -- 5 plain-`#` comments were added to
`psc_semantic_diff.py` and enshrined in the same session.

`--update-baseline` now REFUSES when any metric moved away from its target and
prints which. `--accept-regression` overrides it, and the override is the point:
accepting one becomes a deliberate, visible act rather than a side effect of
running the tool.

🛑 **Refresh the baseline only at a stage EXIT, never mid-edit.** The metrics are
package-wide totals, so a new violation in one file nets out against unrelated
improvements elsewhere in the same run and the guard never sees it. Order:
fix, verify with `--fail-on-regression`, THEN refresh.

## 7. Why it is this way — the failure modes to not repeat
<a id="7-why-this-way-failure"></a>

- **A stage's exit criterion must be a STRUCTURAL FACT, never a deletion list.**
  "Delete these helpers" was satisfied by *moving* them: every named function
  went away, the corpus stayed byte-identical, and the package shrank 54 lines
  while `parse()` still had zero callers. Write it as a property that cannot be
  faked — which is what the fitness metrics are.
- **Build the foundation and USE it in the same change.** An unwired foundation
  is indistinguishable from dead code, and the next change routes around it.
- **Count lines, not branches.** "98 of 197 branches are identical" sounds like
  thousands; it was 356.
- **A metric that cries wolf gets deleted.** The naive form of invariant 2 fires
  32 times and flags `tes4/parser.py` (which legitimately takes TES4 source) and
  `tes5/blocks.py` (which legitimately is the Papyrus classifier). Scope it, and
  give every exemption a comment saying why.
- **Comment volume is not comment value.** The comment-to-code ratio correlates
  *inversely* with code quality here: `converter.py` sits at 0.59 and the clean
  AST layer at 0.13, because prose was compensating for code that could not
  express its own intent. Compression is the comment counts down with every anchor kept — the same
  knowledge in fewer characters, never fewer facts.
- 🛑 **Inline comments track COMPLEXITY, but the comment rules stand.**
  Measured across the package: inline comments per 10 code lines run **1.16**
  at complexity 1-5, 2.18 at 6-10, 2.80 at 11-25 and **7.42 at 26+** — a 6.4x
  spread. `_emit_function` (complexity 295) carried 869 inline comments against
  882 code lines. The correlation shows prose compensating for code that cannot
  state its own intent — `converter.py` sits at a 0.59 comment-to-code ratio
  against 0.13 for the clean AST layer — so it argues for KEEPING the pressure
  on, not for exempting the comments.

  An earlier revision of this section concluded the opposite and told future
  agents not to add a docstring-only rule. That is superseded: `inline-comments`
  and `stray-comments` are gated at 0 by `tools/validate/code_rules.py`, and
  evidence that must survive (a measurement, a census count, a reverted attempt)
  goes to a `docs/` file cited by a `See:` line, which the gate verifies
  resolves. Compression keeps every fact and drops the narration.

  What is forbidden at any complexity: a comment that narrates the next line.
  If a function of complexity <10 needs inline signposts, the docstring is
  missing, not the comments.


## TES4 Script → Papyrus Conversion Plan
<a id="tes4-script-papyrus-conversion-plan"></a>

## Scope
<a id="scope"></a>

| Source | Count | Description |
|--------|-------|-------------|
| SCPT records | 2,393 | Standalone scripts (object, quest, magic effect) |
| INFO ResultScript | 5,694 | Dialogue result scripts (run when INFO is selected) |
| QUST stage SCTX | 1,881 | Quest stage result scripts (NOT yet exported) |
| **Total** | **9,968** | All scripts requiring conversion |

### SCPT Type Distribution
| SCHR.Type | Meaning | Count | Papyrus `extends` |
|-----------|---------|-------|--------------------|
| 0 | Object script | 2,031 | `ObjectReference` (or `Actor` if attached to NPC_/CREA) |
| 1 | Quest script | 265 | `Quest` |
| 256 | Magic effect script | 97 | `ActiveMagicEffect` |

### Variable Type Distribution
| Type | Count | Papyrus |
|------|-------|---------|
| `short` | 4,994 | `Int Property ... Auto` |
| `float` | 1,147 | `Float Property ... Auto` |
| `ref` | 984 | `ObjectReference Property ... Auto` |
| `long` | 1 | `Int Property ... Auto` |

### Top 10 Block Types (from 2,393 scripts)
| Block | Count | Papyrus Event |
|-------|-------|---------------|
| `GameMode` | 1,335 | `OnUpdate()` + `RegisterForSingleUpdate()` |
| `OnActivate` | 899 | `OnActivate(ObjectReference akActionRef)` |
| `OnDeath` | 452 | `OnDeath(Actor akKiller)` |
| `OnReset` | 224 | `OnReset()` |
| `OnLoad` | 208 | `OnLoad()` |
| `OnPackageDone` | 174 | `OnPackageEnd(Package akOldPackage)` |
| `OnTrigger` | 151 | `OnTriggerEnter(ObjectReference akActionRef)` |
| `OnPackageEnd` | 135 | `OnPackageEnd(Package akOldPackage)` |
| `OnAdd` | 102 | `OnContainerChanged(ObjectReference akNew, ObjectReference akOld)` |
| `OnPackageChange` | 90 | `OnPackageChange(Package akOldPackage)` |

---

## Architecture
<a id="architecture"></a>

### Pipeline Overview

```
1. Export phase (tes4_export)
   └── SCPT.txt         (SCTX source + SCHR.Type + SCRO refs)
   └── INFO.txt          (ResultScript field)
   └── QUST.txt          (Stage[i].Log[j].ResultScript — NEEDS ADDING)
   └── NPC_.txt / CREA.txt / ACTI.txt / etc. (SCRI → script attachment)

2. Script conversion (`script_convert/`)
   ├── Parse all script sources
   ├── Build cross-reference graph (FormID→EditorID→ScriptName)
   ├── Classify each script (extends type)
   ├── Convert line-by-line with function mapping
   ├── Inject RegisterForSingleUpdate for GameMode blocks
   ├── Generate property declarations for all external refs
   ├── Generate polyfill calls for unmapped functions
   └── Write .psc files

3. Quest fragment generation (tes5_import side)
   └── For QUST with stage scripts → populate VMAD script fragments

4. Compilation (optional)
   └── PapyrusCompiler.exe validates output
```

### Output Structure
```
output/oblivion.esm/
  scripts/source/
    TES4_<EditorID>.psc              # Standalone scripts
    TES4_QF_<QuestEDID>.psc          # Quest fragment scripts
    TES4_TIF_<FormID>.psc            # Topic info fragment scripts
    TES4Polyfill.psc                 # Polyfill library
    TES4Compat.psc                   # Compatibility utilities
  scripts/compiled/                   # .pex (if compiler available)
```

---

## Step-by-Step Implementation Plan
<a id="step-step-implementation-plan"></a>

### Step 1: Export Quest Stage Scripts

The TES4 QUST export currently only captures stage index, flags, and log text. It misses 1,881 per-stage SCTX (result scripts) and per-stage CTDA (conditions). These are INDX → QSDT → CTDA → SCHR → SCDA → SCTX ordered subrecords.

**Changes to `tes4_export/record_types/dialog_misc.py::export_QUST()`:**
- After QSDT, check for SCHR/SCTX subrecords following the stage entry
- Export as `Stage[i].Log[j].ResultScript=<escaped source>`
- Export `Stage[i].Log[j].SCHR.Type=<int>` for script type context

### Step 2: Build Cross-Reference Graph

Before converting any script, build a lookup table from the export data:

1. **FormID → EditorID map**: From ALL record types (ACTI, NPC_, CREA, QUST, WEAP, etc.)
2. **EditorID → Script name map**: From SCRI fields on all records → SCPT EditorID
3. **SCPT FormID → SCHR.Type**: Script type classification
4. **QUST EditorID → variable list**: Quest scripts' variables are globally accessible

This graph enables:
- Resolving `set SomeRef.VarName to value` → `(SomeRef as ScriptType).VarName = value`
- Determining correct `extends` class when SCHR.Type=0 (check if attached to NPC_→Actor)
- Property declaration for all referenced FormIDs

### Step 3: Script Type Classification

| Signal | Extends | Priority |
|--------|---------|----------|
| SCHR.Type = 1 | `Quest` | Highest |
| SCHR.Type = 256 | `ActiveMagicEffect` | Highest |
| Attached to NPC_/CREA via SCRI | `Actor` | High |
| Contains `ScriptEffectStart` block | `ActiveMagicEffect` | Medium |
| Contains `SetStage`/`GetStage` as self | `Quest` | Medium |
| Calls `Kill`, `GetAV`, `StartCombat` on self | `Actor` | Low |
| Default (SCHR.Type = 0) | `ObjectReference` | Lowest |

### Step 4: Function Mapping (Complete)

The existing FUNCTION_MAP has ~90 entries. Full Oblivion has ~200+ vanilla functions. We need three tiers:

**Tier 1: Direct equivalents (~100 functions)**
Same or very similar Papyrus function exists. Mechanical substitution.

**Tier 2: Polyfill required (~50 functions)**
Function exists in Oblivion but not Papyrus. A polyfill script provides the equivalent:
- `GetRandomPercent` → `Utility.RandomInt(0, 99)`
- `GetButtonPressed` → Queue-based message system via polyfill
- `PlayGroup` → **routes on WHAT THE TARGET IS, never on call syntax**:
  animated OBJECTS (ACTI/DOOR/STAT/MSTT — a NiControllerManager NIF that keeps
  its TES4 sequence names) get `PlayAnimation("Forward")`; ACTORS
  (NPC_/CREA/ACHR/ACRE) get `Debug.SendAnimationEvent()` (animation group name
  mapping), because `PlayAnimation()` on an actor corrupts its behavior graph.
  Resolve the base record via `CrossRefGraph.get_base_signature()`; an unknown
  target keeps the event (inert on an object, never harmful to an actor).
  `PlayAnimation` is an ObjectReference method, so an explicit ref must play on
  THAT ref, not on `Self`.
  Sending every explicit-ref call to `SendAnimationEvent` broke **every
  lever-operated secret door in the game** (196 calls / 86 scripts: Anvil
  Castle ×4, Bravil Castle, Anga, mine traps). CharacterGen's
  `CGPrisonSecretWallRef.playgroup forward 1` went inert, so Renault threw the
  switch, the quest advanced, and the wall never moved — while the SELF-call on
  the next TES4 line converted correctly. **When one of a pair of identical TES4
  statements converts and the other doesn't, suspect the branch that
  distinguishes them.** Guarded by `TestPlayGroupTargetRouting`.
- `GetPos X/Y/Z` → `GetPositionX()` / `GetPositionY()` / `GetPositionZ()`
- `SetPos X/Y/Z` → `SetPosition(x, y, z)` (needs axis decomposition)
- `GetAngle X/Y/Z` → `GetAngleX()` / `GetAngleY()` / `GetAngleZ()`
- `ShowMap` → `Game.ShowFirstPersonGeometry(true)` (approximate)
- `SetCrimeGold` → `Faction.SetCrimeGold(amount, false)`
- `GetInCell` → Polyfill: compare `GetParentCell() == targetCell`
- `GetSelf` → `Self` (keyword, not function call)
- `IsActionRef player` → `akActionRef == Game.GetPlayer()` (event parameter)
- `PMS`/`SMS` (play/stop magic shader) → `Game.ShakeCamera()` (approximate)
- `PlaceAtMe` with persistent flag → `Game.CreateReferenceAtLocation()`

**Tier 3: No equivalent (~50 functions)**
Functions with no Papyrus equivalent. Emit `;TODO:` comments:
- `CloseOblivionGate` — Oblivion-specific
- `SetQuestObject` — Engine-level, no Papyrus API
- `PurgeCellBuffers` — Engine memory management
- `SetCellOwnership` — No direct Papyrus equivalent
- `Reset3DState` — Rendering internals
- `ShowMap` (discovery) — Partial via `WorldSpace.SetMapMarkerVisible()`

### Step 5: GameMode → OnUpdate Conversion

Every `begin GameMode` block becomes:

```papyrus
Event OnInit()
  RegisterForSingleUpdate(0.5)  ; Default interval
EndEvent

Event OnUpdate()
  ; ... converted GameMode body ...
  RegisterForSingleUpdate(0.5)  ; Re-register at end
EndEvent
```

**Interval heuristic:**
- Script uses `GetSecondsPassed` → 0.1s (fast poller)
- Script checks distance/position → 0.5s (spatial check)
- Script only checks flags/stages → 1.0s (slow check)
- Default → 0.5s

**`begin MenuMode <id>` blocks: comment out, do NOT merge into OnUpdate**

A `begin MenuMode <id>` block runs *only while that specific menu is open* —
1014 = lockpicking, 1030 = class menu, 1002 = inventory, 1023 = quest/map,
1022 = magic. Skyrim has no per-menu event, and `Utility.IsInMenuMode()` only
answers "is *some* menu open", so **there is nothing to convert the trigger to.**

Merging these bodies into the GameMode `OnUpdate` loop (which is what the
converter used to do, with no guard at all) makes them run on the first tick as
if every menu were open at once. `MQ01Script` is the worst case: its
`MenuMode 1014` and `MenuMode 1030` blocks call `setstage MQ01 70` / `84`
unconditionally, so on a new game the tutorial quest blew straight through its
stage machine and hit stage 100's `stopquest MQ01` — this was the
"MQ01 starts then immediately fails / jumps to the last stage" bug.

The converter now emits MenuMode bodies as a converted-but-commented block after
`OnUpdate`, so the trigger can't fire and the translation stays available for
anyone hand-porting it to a Papyrus menu hook. Only ~11 MenuMode blocks exist in
all of Oblivion.esm, 5 of them in MQ01Script.

**Locals whose name collides with a TES4 command**

`DiveRockScript` declares `short message`. A local is registered under BOTH its
original TES4 spelling and its Papyrus-safe rename (`message` → `myMessage`,
since `Message` is a Papyrus type): the body still spells it the TES4 way, so if
only the safe name is registered, `if message == 0` is compiled as the TES4
`Message` *command* and comes out as `If Debug.Notification("") == 0`.

### Step 6: Variable → Property Conversion

```
TES4: short doOnce            → Int Property doOnce = 0 Auto
TES4: float timer             → Float Property timer = 0.0 Auto
TES4: ref mySelf              → ObjectReference Property mySelf Auto
```

**Special cases:**
- Variables used as boolean flags (short with only 0/1 values) → `Bool Property ... Auto`
- `ref` variables that always hold actors → `Actor Property ... Auto`
- Quest variables accessed cross-script → public properties on Quest script

**`_property_refs` MUST be keyed on the Papyrus-safe name**

Everything that writes a property ref — `_add_scro_ref` (SCRO preload) and
`_convert_ref` (body conversion) — has to key `_property_refs` on
`_safe_property_name(edid)`, which is also what `_collect_scro_properties` writes
into the VMAD. Keying on the raw EditorID anywhere creates a *second* entry for
any EditorID that gets renamed, and many Oblivion EditorIDs collide with vanilla
Skyrim script names (`MS14` → `myMS14`).

When that happened, the generic `Quest` type seeded from the SCRO and the
specific `TES4_MS14Script` promoted by `_convert_ref` lived under different keys.
The "don't downgrade a promoted type" guard compared the wrong key and never
fired, so the *generic* type won the declaration and the script compiled to
`Quest Property myMS14` with a body calling `myMS14.QuestDone` →
`field or property QuestDone not found`. Same root cause for `GoHomeRythe`.

### Step 7: Expression Conversion

TES4 expressions have function calls inline:
```
if GetActorValue Health > 50
set myVar to GetDistance player
```

Papyrus requires:
```papyrus
If GetActorValue("Health") > 50
myVar = GetDistance(Game.GetPlayer())
```

Key transformations:
1. Actor value names become string parameters: `Health` → `"Health"`
2. Function calls get parenthesized arguments
3. `player` → `Game.GetPlayer()`
4. `set X to Y` → `X = Y`
5. `let X := Y` (OBSE) → `X = Y`
6. `X <> Y` → `X != Y`
7. `&&` / `||` already valid in Papyrus

### Step 8: INFO Result Script → VMAD Fragments

Each INFO ResultScript becomes a Papyrus fragment:

```papyrus
; TES4_TIF__<InfoFormID>.psc
ScriptName TES4_TIF__<InfoFormID> extends TopicInfo Hidden

Function Fragment_0()
  ; converted result script body
EndFunction
```

The import script must populate INFO VMAD with the fragment reference. VMAD structure:
```
VMAD {
  Version: 5
  ObjectFormat: 2
  Scripts: []  (empty — no persistent scripts)
  ScriptFragments: {
    UnknownByte: 0
    FileName: "TES4_TIF__<InfoFormID>"
    Fragments: [
      { Unknown: 0, ScriptName: "TES4_TIF__<InfoFormID>", FragmentName: "Fragment_0" }
    ]
  }
}
```

### Step 9: Quest Stage Script → VMAD Fragments

Each QUST stage script becomes a function in a quest fragment script:

```papyrus
; TES4_QF_<QuestEditorID>.psc
ScriptName TES4_QF_<QuestEditorID> extends Quest Hidden

Function Fragment_Stage_0010_Item_0()
  ; converted stage 10 script body
EndFunction

Function Fragment_Stage_0020_Item_0()
  ; converted stage 20 script body
EndFunction
```

QUST VMAD gets populated with:
```
VMAD {
  Scripts: [{ name: "TES4_QF_<QuestEditorID>", properties: [...] }]
  ScriptFragments: {
    FileName: "TES4_QF_<QuestEditorID>"
    Fragments: [
      { StageIndex: 10, Unknown: 0, StageIndex2: 10,
        ScriptName: "TES4_QF_<QuestEditorID>",
        FragmentName: "Fragment_Stage_0010_Item_0" },
      ...
    ]
  }
}
```

### Step 10: Polyfill Library

Create `TES4Polyfill.psc` — a utility script providing functions that don't exist in vanilla Papyrus:

```papyrus
ScriptName TES4Polyfill extends Quest
{Utility functions for converted TES4 scripts. Attach to a quest and access via property.}

; --- Random ---
Int Function GetRandomPercent() Global
  Return Utility.RandomInt(0, 99)
EndFunction

; --- Cell comparison ---
Bool Function IsInCell(ObjectReference akRef, Cell akCell) Global
  Return akRef.GetParentCell() == akCell
EndFunction

; --- Timer utility ---
Float Function GetSecondsPassed() Global
  ; Papyrus has no frame delta. Return update interval estimate.
  Return 0.5
EndFunction

; --- Actor value wrappers with TES4 AV name resolution ---
Float Function GetTES4ActorValue(Actor akActor, String avName) Global
  ; Maps TES4 attribute/skill names to TES5 equivalents
  If avName == "Strength"
    Return akActor.GetActorValue("UnarmedDamage")
  ElseIf avName == "Intelligence"
    Return akActor.GetActorValue("Magicka")
  ElseIf avName == "Willpower"
    Return akActor.GetActorValue("MagickaRate")
  ElseIf avName == "Agility"
    Return akActor.GetActorValue("SpeedMult")
  ElseIf avName == "Speed"
    Return akActor.GetActorValue("SpeedMult")
  ElseIf avName == "Endurance"
    Return akActor.GetActorValue("HealRate")
  ElseIf avName == "Personality"
    Return akActor.GetActorValue("Speechcraft")
  ElseIf avName == "Luck"
    Return 50.0  ; No equivalent
  ElseIf avName == "Fatigue"
    Return akActor.GetActorValue("Stamina")
  ElseIf avName == "Armorer"
    Return akActor.GetActorValue("Smithing")
  ElseIf avName == "Athletics"
    Return akActor.GetActorValue("Stamina")
  ElseIf avName == "Blade"
    Return akActor.GetActorValue("OneHanded")
  ElseIf avName == "Blunt"
    Return akActor.GetActorValue("TwoHanded")
  ElseIf avName == "HandToHand"
    Return akActor.GetActorValue("UnarmedDamage")
  ElseIf avName == "Mysticism"
    Return akActor.GetActorValue("Alteration")
  ElseIf avName == "Mercantile"
    Return akActor.GetActorValue("Speechcraft")
  ElseIf avName == "Security"
    Return akActor.GetActorValue("Lockpicking")
  ElseIf avName == "Acrobatics"
    Return akActor.GetActorValue("SpeedMult")
  Else
    Return akActor.GetActorValue(avName)
  EndIf
EndFunction

; --- PlayGroup approximation ---
Function PlayAnimationGroup(ObjectReference akRef, String groupName, Bool abForward) Global
  ; TES4 PlayGroup → TES5 animation event
  If groupName == "Forward"
    Debug.SendAnimationEvent(akRef, "IdleForceDefaultState")
  ElseIf groupName == "Backward"
    Debug.SendAnimationEvent(akRef, "IdleForceDefaultState")
  ElseIf groupName == "SpecialIdle"
    Debug.SendAnimationEvent(akRef, "IdleForceDefaultState")
  Else
    Debug.SendAnimationEvent(akRef, "IdleForceDefaultState")
  EndIf
EndFunction

; --- MessageBox with button tracking ---
; Note: Full MessageBox conversion requires creating Message form records.
; This provides a basic notification fallback.
Function ShowMessage(String text) Global
  Debug.Notification(text)
EndFunction
```

### Step 11: VMAD Binary Generation

The import script (`tes5_import`) needs a VMAD writer to attach scripts to records.

**VMAD binary format (version 5, object format 2):**
```
I16  version (5)
I16  objectFormat (2)
U16  scriptCount
  For each script:
    WSTRING  scriptName
    U8       flags (0=local, 1=inherited)
    U16      propertyCount
    For each property:
      WSTRING  propertyName
      U8       propertyType (1=Object, 2=String, 3=Int, 4=Float, 5=Bool)
      U8       propertyFlags (0x01=readonly)
      <value depending on type>
        Object:  U16(1) + U16(aliasId) + U32(formID)
        String:  WSTRING
        Int:     I32
        Float:   F32
        Bool:    U8
```

**For QUST ScriptFragments:**
```
After scripts array:
  U8   unknownByte (0)
  WSTRING fileName
  U16  fragmentCount
  For each fragment:
    U16  stageIndex
    U16  unknown (0)
    I32  stageIndex2 (same as above, signed)
    U8   unknown2 (1)
    WSTRING scriptName
    WSTRING fragmentName ("Fragment_Stage_NNNN_Item_0")
```

**For INFO ScriptFragments:**
```
After scripts array:
  U8   unknownByte (0)
  WSTRING fileName
  U8   fragmentCount (usually 1)
  For each fragment:
    U8   unknown (0)
    WSTRING scriptName
    WSTRING fragmentName ("Fragment_0")
  U8   unknown (1 if has condition scripts, 0 if not)
```

### Step 12: Pipeline Integration

Add to `run/convert.py` as Phase 4 (after Phase 3: Assets):

```
Phase 4: Script Conversion
  1. Load cross-reference graph from export data
  2. Convert SCPT → .psc files
  3. Convert INFO ResultScript → fragment .psc files
  4. Convert QUST stage scripts → fragment .psc files
  5. Generate polyfill library
  6. (Optional) Compile via PapyrusCompiler.exe
  7. Copy .psc to output/scripts/source/
  8. Copy .pex to output/scripts/compiled/ (if compiled)
```

---

## Conversion Quality Tiers
<a id="conversion-quality-tiers"></a>

### Tier 1: Mechanically Correct (~60% of scripts)
Simple scripts with direct function mappings. No cross-script references. No complex expressions.

**Example:**
```oblivion
scriptname SE09RootGateScript
short open
begin onActivate
  if isActionRef player == 1
    message "The roots will not budge."
  endif
end
```
→
```papyrus
ScriptName TES4_SE09RootGateScript extends ObjectReference
Int Property open = 0 Auto
Event OnActivate(ObjectReference akActionRef)
  If akActionRef == Game.GetPlayer()
    Debug.Notification("The roots will not budge.")
  EndIf
EndEvent
```

### Tier 2: Needs Polyfill (~25% of scripts)
Uses functions without direct equivalents. Polyfill library provides replacements.

### Tier 3: Manual Review Required (~15% of scripts)
Complex patterns: state machines, multi-frame sequences, cross-script communication, MessageBox with choices, animation sequencing. Emit `;TODO:` markers.

---

## Testing Strategy
<a id="testing-strategy"></a>

1. **Syntax validation**: Every .psc must parse without errors (basic Papyrus grammar check)
2. **Property completeness**: Every referenced FormID/EditorID has a property declaration
3. **Event coverage**: Every TES4 block maps to a Papyrus event
4. **Function coverage**: No unmapped functions appear without `;TODO:` markers
5. **Compilation test**: If PapyrusCompiler.exe is available, compile all .psc and report errors
6. **Round-trip test**: Convert sample scripts, verify output matches expected Papyrus

---

## Known Limitations
<a id="known-limitations"></a>

1. **No VMAD generation yet**: The import script doesn't write VMAD subrecords. Scripts will compile but not attach to records until VMAD writer is implemented.
2. **Cross-script variable access**: `set QuestRef.VarName to value` requires knowing which script type is on QuestRef. Partial solution via cross-reference graph.
3. **MessageBox choices**: Oblivion MessageBox with buttons + GetButtonPressed needs synthetic Message form records. Initially emit `;TODO:`.
4. **Frame-rate dependent timing**: `GetSecondsPassed` has no Papyrus equivalent. OnUpdate interval is an approximation.
5. **Cell/location mismatch**: TES4 `GetInCell` uses Cell records; TES5 `IsInLocation` uses Location records (not created by converter).
6. **Animation events**: TES4 PlayGroup animation names don't map 1:1 to Skyrim animation events.
7. **OBSE extensions**: Scripts using OBSE functions (ar_*, sv_*, etc.) cannot be mechanically converted.

---

## Creation Kit Papyrus Compiler Contracts (2026-07-12)
<a id="creation-kit-papyrus-compiler-contracts"></a>

The bundled MIT compiler (`external/papyrus-compiler/papyrus.exe`) accepts code the
**real** compiler rejects, so a clean run there means nothing. Always validate with
`python tools/script/ck_compile_check.py` — it drives Skyrim's own
`Papyrus Compiler/PapyrusCompiler.exe`, the one the CK uses. A script that fails to
compile produces no `.pex`, so **the object it is attached to silently does nothing
in-game** — and it takes every script that references it down too (all member
accesses on it then fail), so one bad script can mask hundreds.

These contracts were each verified against `PapyrusCompiler.exe`:

| Contract | Symptom if violated |
|---|---|
| **ScriptName ≤ 38 chars.** Enforce via `constants.papyrus_script_name()` — the single source of truth for the `.psc` ScriptName, the `.psc` filename, AND the VMAD ScriptName (they must agree or binding breaks). Long names are truncated + given an MD5 tag, since many Oblivion EditorIDs differ only past the cut (`…RdCitadel0{1..5}SCRIPT`). | `"…" is too long, please shorten it to 38 characters or less`. 81 Oblivion scripts overflowed. |
| **No identifier may start with a lowercase `temp`.** The compiler mangles a variable `x` to the register `::x_var` and reserves the `::temp*` namespace for its own scratch registers. Case-sensitive, prefix-anchored: `temp`, `tempstage`, `template`, `temperature` all fail; `Temp`, `tmp`, `atemp` are fine. `_safe_property_name` capitalises the leading `t`. | `Attempting to add temporary variable named ::temp_var to free list multiple times` (558 errors from 15 scripts). |
| **No identifier may reuse ANY Skyrim script name** — not just native types. `Door`, `DarkBrotherhood`, `MS14` are all real Skyrim `.psc` files. The reserved list lives in `script_convert/papyrus_reserved.txt`, generated by `tools/generators/gen_papyrus_reserved.py` from `Data/Scripts.zip` (Bethesda's pristine archive — do NOT read `Data/Source/Scripts`, which on a modded install also contains the user's mods and would make conversion non-reproducible). | `cannot name a variable or property the same as a known type or script`, then `Door is not a variable` / `cannot call the member function SetStage … on a type` at every use. |
| **A rename must reach EVERY emission path.** Renames are only recorded when `safe != vname` (**case-sensitive** — `temp`→`Temp` differs only in case, and a case-insensitive test skipped it). Handlers that emit an operand *raw* bypass renaming entirely: `setstage`'s stage arg, `startquest`/`stopquest`'s quest arg, and `_convert_ref`'s quest path all had to be routed through `_convert_expression` / `_safe_property_name`. | Declaration renamed but body still references the old name. |
| **No doubled cast.** `X as Int as Int` is a parse error. Emit casts via `ScriptConverter._cast()`, which is a no-op if the expression already ends in that cast. | `no viable alternative at input 'Int'` — 1965 errors, the single biggest class, from just 116 sites (the CK reports each one many times). |
| **Bool-returning functions can't meet a number.** TES4's `GetDetected`/`GetDead`/`GetDeadCount` return Int 0/1, so scripts write `getdetected X > 0` and `set n to getdeadcount X + 3`. Papyrus refuses to order or add a Bool. `_BOOL_CMP_RE` casts the call; `GetDeadCount` (which has no Papyrus equivalent at all, and whose operand is a *base* form, not a reference) now emits a typed `0`. | `cannot relatively compare variables of type bool`, `cannot add a bool to a int`. |
| **`OnEffectStart`/`OnEffectFinish` take `(Actor akTarget, Actor akCaster)`.** The signature is fixed by `ActiveMagicEffect.psc`; an invented one is rejected. | `the parameter types of function oneffectstart … do not match the parent script activemagiceffect`. |
| **A `Global` function may not touch a script property** (there is no instance). `TES4Polyfill` is all-Global, so `GetDayOfWeek` fetches GameDaysPassed via `Game.GetFormFromFile(0x39, "Skyrim.esm")` instead of holding a property. | `variable GameDaysPassed is undefined`. |
| **`GetIsID` → `GetBaseObject()`, never `(x as Actor).GetActorBase()`.** TES4's `GetIsID` compares against *any* base form (the SE38 oddities are MISC/INGR/WEAP/KEY, not actors). `GetBaseObject()` is declared on ObjectReference (no cast needed, works for actors too) and returns a Form, which compares against every base type. Operands are typed via `_record_type_to_base_papyrus` (NPC_/CREA → **ActorBase**, not Actor). | `cannot cast a tes4_se38oddityscript to a actor, types are incompatible`. |
| **`_property_refs` must be keyed on `_safe_property_name(edid)` on EVERY write path** (`_add_scro_ref` *and* `_convert_ref`) — that is also the name `_collect_scro_properties` puts in the VMAD. Keying on the raw EditorID makes a *second* entry for any renamed EditorID (`MS14` → `myMS14`), so the "don't downgrade a promoted type" guard compares the wrong key, never fires, and the generic `Quest` from the SCRO beats the specific `TES4_MS14Script` promoted from the body. | `field or property QuestDone not found` / `field or property GoHomeRythe not found` — a `Quest`-typed property with a body calling quest-script members on it. |
| **Always pass `-nocache` to `papyrus.exe compile`.** Its cache keys on the *source* only, not the output path: an unchanged `.psc` is treated as already compiled, so it **exits 0 and writes no `.pex` at all**. Static scripts whose text never varies between runs (`TES4_ShowBarterMenu`, `TES4_ShowTrainingMenu`, `TES4Polyfill`) hit this every time. | Reported as a bare `exit code 0` "failure" with no error text, and the object the script is attached to silently does nothing in-game. |

### Quest scripts: gate the GameMode body on `IsRunning()`

In TES4 a **quest script**'s `begin gamemode` block only executes *while the quest is
running*, so its body routinely assumes that. Skyrim raises `OnInit` on the quest
object whether or not the quest ever started, and **`SetStage` on a stopped quest
STARTS it** — so an ungated body silently auto-starts the quest at load. This is why
"Imperial Dragon Armor" appeared in the journal on a new game: `MQDragonArmorQuestSCRIPT`
runs `if gamedayspassed >= armorFinishDay: setstage MQDragonArmor 20`, and at day 1
vs. an unset `armorFinishDay` of 0 that is immediately true.

The converter now wraps the OnUpdate body of any `extends Quest` script in
`If (!IsRunning()) … Return`, re-arming the poll while stopped so it resumes on its own
once the quest legitimately starts (211 quest scripts affected).

## The Say fallback line length
<a id="say-line-fallback-duration"></a>

Fallback line length (seconds) a converted `set T to Say topic` assumes when
the topic has no measured audio.  The real value comes from the engine at run
time: TES4Polyfill.SayLine blocks until the INFO's OnBegin fragment reports
the selected line's own length (say_durations, `info:<FID>`), and only falls
back to this when the line has no voice file at all.  See the "Say() timers"
section of docs/commentary/script_convert.md.

## The StartQuest post-pass fails open
<a id="startquest-postpass-fails-open"></a>

`<Quest>.Start()` and a write to a property of that same quest's script.
🛑 If the emitted shape of a `StartQuest` conversion ever changes, this
regex must change with it: a post-pass that silently stops matching
FAILS OPEN -- no error, just the original bug back (measured: renaming
this call once re-clobbered 91 seed writes across 43 scripts).  See
docs/commentary/script_convert.md.

## Journal objective completion

**Code:** `script_convert/objective_completion.py`;
`script_convert/data/parallel_objectives.json`;
`tes4_export/record_types/dialog_misc.py` (`export_QUST`).
**Audit:** `tools/validate/objective_completion_audit.py`.

Oblivion's journal is an append-only log; Skyrim's is a set of objectives each
independently Displayed / Completed. A Displayed-but-not-Completed objective
renders as an open bullet with a live compass marker, so every converted quest
must say which step each stage FINISHES.

That is authored data, not something to infer. Oblivion's journal filter
(`Oblivion.exe` 0x52af40, reached from 0x52adf0 -> the condition evaluator at
0x56a950) walks the quest's subrecords matching `QSDT`/`CNAM` and evaluates
each log entry's OWN CTDA set, displaying the entry only while it passes. The
`ShowFullQuestLog` console command ("Show all log entries for a single quest")
exists precisely because the normal journal shows a subset.

Two idioms express supersession. Censused over the 950 log-entry CTDAs in
Oblivion.esm:

| Condition | Count | Meaning |
|---|---|---|
| `GetStage <quest> < N` | 102 | supersedes at N |
| `GetStageDone <quest> N == 0` | 167 | supersedes at N |

`GetStageDone` names its stage in **param 2**, not in the comparison value.
Only a LATER stage is a supersede: the same function against an EARLIER stage
(112 uses) selects WHICH WORDING to show. MS48 stage 20 is the worked example
— two entries on `GetStageDone MS48 10`, one `== 0` and one `== 1`, choosing
between two phrasings of the same step.

Other functions appearing on log entries (79 GetQuestVariable 249x, 84, 309
IsXBox) are not display-supersedes and are ignored.

**The export dropped all of this.** `export_QUST` parsed log-entry CTDAs into
`entry['ctdas']` and then emitted only Flags/Text/ResultScript/SCRO — targets
got their conditions written, log entries did not. 950 CTDAs across 71 quests
were silently lost, which is why the data looked absent and the completion
points had to be guessed from quest-target marker gates.

Where no authored gate exists the marker-gate inference remains as fallback:
an objective's step runs while its QSTA markers are live and ends at the first
stage they go dark. That is NOT "complete every lower-numbered objective" —
quests legitimately hold several objectives open at once, guarded by
`tests/test_script_converter.py::TestQuestObjectiveCompletion`.

### Only a CLOSING gate is evidence

A marker gate that never stops being satisfied says nothing about whether a
step finished. Reading it as "still in progress" stranded **617 of 6,312**
objectives (9.8%) across the big 3 — 607 of them because the marker rule
returned nothing and, being an `elif`, never reached the sequential default.
Quests WITH targets had 617 stuck of 4,819; quests WITHOUT targets had **0 of
1,493**, which located the defect.

MS48 is the worked example. Targets 0-3 are bounded (`GetStage >= 10, < 30`)
and complete correctly; Target 4 (SavlianMatiusRef) is `GetStage >= 50` with no
upper bound, so it is live at 50/60/70/80/90/200 and objectives 50-90 hung
forever — the reported bug.

`_target_closes` therefore counts a gate as evidence only when it can stop
being satisfied: a `GetStage` comparison with a closing edge (`==`, `<`, `<=`),
or ANY `GetStageDone` test. **GetStageDone must count.** 316 of 2,724 targets
are not purely GetStage-gated (47 GetStageDone only, 169 both, 100 neither),
and `fbmwBMStones`' six Standing Stone rituals are gated purely on
`GetStageDone` with no stage window — order-independent by construction.
Measured over every objective in every export:

| predicate | unchanged | stuck->closes | closes EARLIER | residue |
|---|---:|---:|---:|---:|
| `GetStage` closing op only | 5181 | 498 | **51** | 65 |
| exclude any `GetStageDone` target | 5120 | 501 | **109** | 62 |
| **`GetStageDone` counts as closing** | 5240 | 487 | **3** | 76 |

The 3 remaining early closures were each read against their quest text and are
improvements, not regressions:

| objective | was | now | why |
|---|---:|---:|---|
| `TrainingHeavyArmor` 10 | 30 | 20 | 10 is "go see Pranal"; 20 is Pranal's own request, so 10 is done |
| `fbmwTR09` 60 | 90 | 70 | 60/70 are two ways to extract the same promise; 90 is the alternate "I killed him" ending |
| `fbmwTR09` 70 | 90 | 80 | as 60 — answered by the next step, not by the later duplicate ending |

### Terminal stages are mutually exclusive

TES4 QSDT is `wbBoolEnum` "Complete Quest" — one boolean
(`wbDefinitionsTES4.pas:3212`). TES5's is a flags byte with bit 0 Complete and
bit 1 **Fail** (`wbDefinitionsTES5.pas:8811`): Bethesda added failure in
Skyrim, and Oblivion.esm contains **zero** QSDT values of 2 or 3. Success and
failure endings are the same bit, and **374 of 1,130 quests (33%) have 2+
ending stages** — SE44 stage 200 "Ahjazda rewarded me" beside 201 "Ahjazda is
dead". A sequential rule would close 200 with 201 in **572** cases, so
`_terminal_stages` excludes any flagged stage from ever being superseded.

Failure is therefore NOT inferable. The only signals are prose (39 of 2,338
Oblivion log entries match a failure vocabulary; just 17 of those also carry
the flag) and the stage-band convention, which **SE38 inverts** (190 failure,
200 success). Both are heuristics and are not used. `CompleteAllObjectives()`
on the flag settles endings dynamically instead.

### The runtime sweep

76 objectives across 19 quests remain unresolvable statically. Static analysis
cannot know which branch a player walked, but the running game can, so the
generated fragment asks it: an objective still Displayed and not Completed is
one the player saw and has moved past. A branch never taken was never
Displayed, so it is left alone — which is exactly "mark it only if it is
already in the player's journal".

Scored against the 76, hand-classified by reading all 19 quests:

| approximation | correct | wrong |
|---|---:|---:|
| blind sequential fallback | 33 | 43 |
| "run of >=3 unclosed => parallel" | ~48 | ~28 |
| **runtime displayed-and-incomplete sweep** | **61** | **15** |

The 15 are genuinely concurrent tasks — MQ11's six city gates, TGDirections'
four fences, ND02's four relics, SE13's obelisks — listed in
`parallel_objectives.json` and exempted from the sweep. A quest absent from
that table is swept normally; a miss leaves an objective open, which is the
pre-existing behaviour rather than a wrongly-ticked one.

**An order-independence probe over the 10,353 `setstage` callers does NOT
separate parallel from linear**: MS48 is linear yet has 14 ungated callers,
because dialogue-driven stages are ungated in linear quests too. The parallel
table is hand-read, not derived.

**The residue is a biased sample** — it holds only what the rules give up on,
so it can never reveal an objective the rules close WRONGLY. That is why
`objective_completion_audit.py --against` sweeps all 6,338 objectives; it is
what caught the 51 and the 109 above, neither of which appears in the residue.

## Generic OBSE/SKSE64 syntax recovery

The parser recognizes released-script damage seen across independent mods,
not plugin-specific EditorIDs: punctuation-only banner lines become source
comments, a physical line beginning with `&&`/`||` continues the preceding
condition, and a quoted known zero-argument command follows its normal mapping.
Authored `TODO` comments are relabeled `Source note:` so the converter's own
`;TODO:` remains a reliable unsupported-behavior audit.

TES4 `OnMurder` maps to Skyrim's distinct `OnMurder(Actor akKiller)` event and
retains its block filter through `akKiller`; `OnKnockout` maps to
`OnEnterBleedout()`. A standalone `GetSecondsPassed` resets the real-time poll
baseline instead of emitting a bare Float expression.

OBSE `IsPlayable`/`IsPlayable2` targets SKSE64 `Form.IsPlayable()`. The
`TES4SKSE.GetBaseForm` polyfill normalizes placed references and base forms,
and compilation augments the CK's vanilla `Form.psc` in a temporary header-only
overlay. The overlay is deleted and never shipped. Bool-valued commands used
in TES4 arithmetic are cast at the operand, preserving expression precedence.
