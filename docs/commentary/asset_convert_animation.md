# asset_convert/havok/hkx_anim.py — animation and behaviour graphs

**Code:** `asset_convert/havok/hkx_anim.py`, `asset_convert/havok/hkx_animobject.py`, `asset_convert/havok/hkx_behavior.py`, `asset_convert/havok/kf_decode.py`, `asset_convert/havok/kf_writer.py`

## Contents

- [NIF animated mesh conversion](#nif-animated-mesh-conversion)
- [Animated-object behaviour graphs (asset_convert/havok/hkx_animobject.py)](#animated-object-behaviour-graphs)

## NIF animated mesh conversion
<a id="nif-animated-mesh-conversion"></a>
- Oblivion animated doors/activators use keyframed collision (motion_system=6 in Oblivion format)
- Key differences from static collision in Skyrim:
  - bhkCollisionObject.flags = 137 (0x89 = ACTIVE | D_ANIMATED | bit 7)
  - bhkRigidBody.motion_system = 4 (MO_SYS_KEYFRAMED)
  - **bhkRigidBody.mass = 0 AND filter layer = 2 SKYL_ANIMSTATIC** (see below)
  - bhkRigidBody.quality_type = 1 (MO_QUAL_FIXED)
  - bhkRigidBody.unknown_byte = 10 (broadphase type for animated)
  - NiNode flags |= 0x80 (selective update sync for physics)

### Keyframed bodies: layer 2 ANIMSTATIC + mass 0 (2026-08-02, PENDING in-game confirmation)

**History, kept honest.** The earlier note here ("mass 0 is MANDATORY,
implemented") was wrong twice over: (a) the mass write was never in the shipped
code — the attempted `rb.mass = 0.0` inside the keyframed branch changed which
downstream shape path ran (hull decomposition keys on `mass > 0`), collapsed
the collision compound, and was reverted, so **the in-game test that "mass
changed nothing" tested a broken build, not the theory**; (b) the two
"real causes" it then blamed (adjacent `PlayGroup`s cancelling; one-sequence
hold-state dead end) were both fixed and the planks still failed in-game
(2026-08-02: "begins to animate, stops suddenly, doesn't finish" — so the
event DOES reach the graph and the sequence starts; something reclaims the
nodes mid-clip). Do not cite either as the mechanism again.

**The discriminator the earlier session missed is the collision filter
LAYER.** Census of every vanilla motion_system=4 body found
(`farmhouseanimdoor01`, `farmbtrapdoor01`, `rtirongate01`, `orcdoor01`,
`riftenkeepdoor01` ×2, `mrkmarketstalldoor01`, `rifrmsmbasewallgrate01`,
`rifrmsmsecretcabinetdoor01` ×2, `sldjailwallcollapse01`): **layer 2
SKYL_ANIMSTATIC and mass exactly 0.0, no exceptions.** Our one in-game-working
animated object (`prisonSecretWall01`, source-authored OL_ANIM_STATIC + mass 0)
also ships layer 2 / mass 0. The broken ones shipped **layer 10 PROPS**
(Oblivion authored the bricks/planks on OL_PROPS, which the 0-18 identity
remap passes through) with mass 40/100:
- `mwallplankbreakaway01` (Oblivion + Nehrim) — 8 planks × mass 40, layer 10
- `IDCrumbleWall01` (ImperialDungeon01) — 13 bricks × mass 100, layer 10

Fix in `_convert_collision`: the keyframed branch forces layer 2 on both
filters; mass is zeroed at the very END of the function (after the mass-keyed
decompose gate), so the only bytes that change are the two fields themselves —
verified by structural diff (block graph identical; prisonSecretWall01
unchanged in every field). Multiple keyframed bodies per NIF is vanilla-legal
(`riftenkeepdoor01` ships two).

Note `sldjailwallcollapse01`'s own pattern for multi-piece collapses: ONE
keyframed mass-0 helper body (`ColHelper01`) and NO collision on the 22
animated pieces — vanilla never gives each piece its own body. We keep
per-piece keyframed bodies (faithful to the source collision), normalized to
the vanilla per-body contract.

### Constrained trap islands are HELD, not dynamic (2026-08-05, in-game confirmed)

A swinging trap (`ctrapswingmacelong01`'s chain links + mace head,
`ctraplogs01`, `cprollingrock01`) is authored exactly like a breakaway piece:
`ms=6` KEYFRAMED bodies with **real mass** and `Unyielding = 1`, wired together
by constraints. Oblivion's own script states the contract in its header:

> `; On activation havok will turn on and logs will roll` — `CTrapLogs01SCRIPT`

The old rule sent any `ms=6` body with *mass + a constraint* to **DYNAMIC**
(case 2), which is why **every swinging trap swung freely the instant the cell
loaded**, before anything tripped it. The opposite error (mass-0 keyframed)
welds the trap solid forever. Both were wrong for the same reason: the island is
**held rigid until the trap script fires**.

Fix (`_node_is_held_trap`): a constrained island member ships **KEYFRAMED but
keeps its authored mass**, and the converted script releases it with
`SetMotionType(Motion_Dynamic)`. Membership is checked **island-wide, not
per-body** — a chain link routinely carries mass with `num_constraints == 0` and
hangs off a neighbour's constraint (same reason `collision_extract` checks
constraints file-wide).

Vanilla `trapmace01` ships its links dynamic because a *Skyrim* trap has no
script-held phase; ours must reproduce Oblivion's held phase instead. Do not
"correct" ours to match vanilla here.

**But the MOTION TYPE is the only thing the held phase changes — quality_type
and solver_deactivation must be the POST-RELEASE values (2026-08-10, in-game
confirmed hang).** `SetMotionType(Motion_Dynamic)` swaps the motion type and
nothing else, so whatever the NIF ships for collision quality is what the body
simulates with *after* it is let go. The keyframed branch used to give every
animated body `quality_type=1` (MO_QUAL_FIXED, "static body") with
`solver_deactivation=1` (OFF) — right for a door, whose position really is
deterministic and which is never released, but wrong for a held trap. On
release that handed Havok a ring of mass-bearing bodies inside a live
constraint island all still claiming to be static with deactivation disabled;
the solver has no consistent state to converge on and the simulation step stops
completing. The game keeps running and never renders another frame — it reads
as a **freeze on a black loading screen**, not a crash, and nothing appears in
the Papyrus log.

Symptom that isolated it: walking onto the tripwire in Natural Caverns
(`ImperialDungeon05`, `CGTrigTripwire01` → three chained `CTrapSwingMaceLong01`)
hung the game, while the Vilverin tripwire was fine. Vilverin's trap is
`CTrapSwingMaceShort01`, and the two differ in exactly one way that matters:
the long mace hangs on **7 `chainLink` bodies with `bhkRagdollConstraint`**,
the short one had no chain at the point it was compared. Same mesh family,
same script, same `playgroup` — the constraint island was the whole difference.

Vanilla is the reference for the released state: `trapmace01.nif` ships every
`Link01..11` **and** `Mace01` at `quality_type=4` (MO_QUAL_MOVING) with
`solver_deactivation=2` (LOW). So `_convert_collision` now branches on
`breakaway_body`: held/breakaway pieces get 4/2, plain animated bodies keep
1/1. Plain animated doors (`cdoor03`, `ricketyfencegate01`) stay
**byte-identical**, and creature skeletons are untouched because they route
through `_convert_blend_collision` before this branch. Regression test:
`test_held_trap_ships_post_release_quality`.

**The release is keyed on the MESH, never the animation-group name.**
`physics_flags_from_data` bit 1 = "ships a keyframed body that kept a non-zero
mass", which `_convert_collision` writes for held pieces only (36 meshes).
Keying off the group name cannot work: `forward` is **491 of Oblivion's 850**
`playgroup` calls and is overwhelmingly gates, doors and portcullises that must
keep following their clip exactly — yet it is *also* the tripwire's break group.
The mesh knows which is which; the name does not.

**The bounds cache is SCHEMA-VERSIONED — bump the version when you add a field**
(2026-08-14). `mesh_bounds_cache.json` carries a `"__schema__": [N]` entry, and
`collision_extract.bounds_cache_is_current()` treats any cache written at a
lower version — or with no stamp at all — as missing, so it regenerates instead
of being trusted. Bump `BOUNDS_SCHEMA_VERSION` in the same commit as any change
to an entry's fields.

This is not hypothetical tidiness; skipping it shipped a bug. Entries are plain
lists, so a cache written before a field existed parses cleanly and reads as
**zero** for that field, which is indistinguishable from a computed zero. The
scan used to run only when the file was **absent**, so when bit 1 (HELD) shipped
on 2026-08-05 Nehrim simply kept its 2026-08-02 cache: 0 of its 11,946 meshes
carried the bit, `needs_havok_release` answered False for every one, and **no**
converted `playgroup` emitted `TES4Polyfill.ReleaseBreakaway`.
`mwallplankbreakaway01`'s planks stopped falling. Oblivion's cache happened to
be rebuilt an hour after that commit, so the *same mesh* still worked there —
which made it look like a Nehrim mesh bug rather than a stale cache. Diagnostic
that settles it in one step: compare the entry length for the same path key
across two plugins' caches (`[…, 2]` vs a bare 6-element list).

`--scripts-only` cannot rebuild the cache (it runs with no mesh scan), so
`script_convert/pipeline.py` prints a loud warning when the cache is stale
rather than silently emitting scripts with no release. The script stage also
has to load that cache in **both** the parent and the spawned workers (Windows
spawn does not inherit module state), or every lookup silently answers 0.

**Layer 14 (`OL_TRAP`) on the striking body is LOAD-BEARING — never remap it.**
`_remap_world_filter` passes 14 through unchanged and must keep doing so: it is
the layer whose contact raises Skyrim's `OnTrapHitStart`, which is the ONLY
thing that makes a converted trap deal damage (see the trap-damage section of
[papyrus_conversion_notes.md](script_convert.md) — the damage lives
in the script's `fTrapDamage` variables, not in the mesh). Oblivion and Skyrim
agree on the idiom: `ctrapswingmacelong01`'s mace-end link is layer 14 with its
chain on 10, and vanilla `trapmace01` is identical (Mace01 = 14, Link01-11 =
10). Flattening 14 → 10 "for consistency" would silently disarm every trap.

### The Rest state is CORRECT — the animobject crash is elsewhere (2026-08-10)

Recorded so the next session does not re-tread this. `pSequence=''` on
`GamebryoSequenceGeneratorRest` is **right** and must not be changed: nothing
should play on cell load, for doors, rubble AND plants alike (the Spiddal
plant animates when the player approaches, driven by its script, not by the
graph starting in Forward).

Two "fixes" were tried and both were wrong:

| change | crash | door |
|---|---|---|
| `pSequence=sequences[0]`, `fPercent=0` | stopped | **opened on cell load** |
| non-empty sentinel naming no sequence | **still crashed** (in Generator00) | ok |

The decisive evidence: the FIRST crash of this family
(crash-2026-08-10-00-42-35) was already on **`GamebryoSequenceGenerator00`** —
the generator that plays `Forward` — not on the Rest generator. So the empty
Rest name was never the cause, and every Rest-state change merely moved the
symptom.

Also ruled out by census (700 vanilla meshes, 149 sequences): a **dataless
interpolator is legal**. Vanilla ships 72 dataless `NiFloatInterpolator`, 248
`NiBoolInterpolator`, 259 `NiBoolTimelineInterpolator` and even 4 dataless
`NiTransformInterpolator` (fxbatgroup, fxpoisongaswithonoff,
sprigganfxtestunified). Both crashing meshes carry dataless blocks, but so does
working vanilla content — it is not the discriminator.

RESOLVED (same day): the crash was **empty text key values** in the activated
sequence — see "🔴 A graph-bound mesh must ship NO empty text keys" below.
The secret door's `Forward` plays fine because its keys are only
`start`/`end`, both non-empty; the plants shipped Oblivion-authored empty
keys.

### Every state needs a real transitions array — including at ONE sequence (2026-08-01)

`_transitions(exclude_state=i)` gives each motion state "every OTHER sequence",
so a repeated event cannot restart a sequence mid-play. For a **one-sequence**
object that set is EMPTY and the emitter writes `transitions=null` — the exact
dead end the Rest-state comment warns about. `IDCrumbleWall01`'s only sequence
is `Unequip`, so once it played it could never be re-entered and `OnReset` was
inert. Fix: when the exclusion would empty the array, keep the self-transition.
2- and 3-sequence graphs are byte-identical to before, so the working
`prisonSecretWall01` is unaffected.

The regression test now runs at 1, 2 and 3 sequences — it only covered the
2-sequence case, which is why this shipped.

### Sequence controlled-block ID strings are the ENGINE'S LOOKUP KEY (2026-08-02)

The engine resolves each controlled block at sequence activation BY STRING: on
node `<node_name>` find the property whose class is `<property_type>`, then its
controller of class `<controller_type>`, disambiguated by `<variable_1>`.  Our
shader-controller rewrites swapped the controller block + `controller_type` but
left Oblivion's strings — `property_type='NiTexturingProperty'` (a class that
no longer exists in the file) and `variable_1='0-0-TT_TRANSLATE_V'` — so the
lookup failed silently and the interpolator never drove the shader:
palacefont01's fountain shipped a correct V-offset curve that never played.
Vanilla convention (beehive01, blackpool, dweastrolabehub01, every entry
sampled): `property_type` = shader class name, `variable_1` =
`str(type_of_controlled_variable/color)` (`'8'`, `'11'`, …), `variable_2` = `''`.
Fixed generically in `_normalize_shader_cb_strings` (runs at the end of
`_match_seq_shader_types`).  PSys controlled-block strings were already
vanilla-identical (`var1='NiPSysBoxEmitter:0'`, `var2='BirthRate'`) — leave.

**Shared Oblivion properties → one entry per shape (2026-08-18, the Font of
Madness's upper tier).** Oblivion shares one `NiTexturingProperty` /
`NiMaterialProperty` block between several shapes and a sequence entry names
only ONE of them: palacefont01's `Water` entry drives texturing property #71,
which `Water03`, `PalaceWaterL2` and `PalaceWaterR02` also wear, so in TES4 one
entry scrolls all four. Skyrim gives every converted shape its own
`BS*ShaderProperty`, so only the named shape animated (lower tier moving, upper
frozen). `_process_controller_manager` now indexes property controllers →
wearing shapes once per manager (`_property_ctrl_index`) and, for each
retargeted texture-transform / alpha / material-color entry, appends one entry
per sibling with a cloned controller + interpolator (`_fan_out_shared_entries`,
key data shared; `_attach_seq_shader_controllers` then hangs each off its own
shader). 33 Oblivion.esm meshes / 703 entries (oblivionwargateani02 168,
citadeldeadralordscenterring 106, obeliskenergybox01 102, se01waitingroomwalls
36).

<a id="morph-emulation"></a>
### NiGeomMorpherController does not exist in Skyrim — emulate as a visibility swap on wrapper nodes (in-game confirmed 2026-09-24)

**Code:** `emulate_morphs`, `_emit_flipbook`, `_flipbook_states`, `_wrap_shape`, `_own_properties` in `asset_convert/nif/morphs.py`

The SSE exe has NO `NiGeomMorpherController` RTTI class (only the orphaned
`NiMorphData` remains) and vanilla ships 0 uses, so morph entries HAD to be
dropped — but the morph IS the visible effect for 18 Oblivion meshes
(ctrigtripwire01's wire snap, se01waitingroomwalls, obliviongate_forming,
gnarlspawner…).  `emulate_morphs` (fed by a harvest at the drop site in
`_process_controller_manager`) replays the morph as a **30 fps flipbook** (in-game
confirmed 2026-09-24): for each sequence and shape it samples every target's
weight curve at `FLIPBOOK_FPS`, blends base + Σ weight × delta
(relative_targets; absolute targets become target − base), bakes each distinct
pose into a sibling shape, and shows exactly one shape per frame.  The first
version baked only the full target and cut at weight 0.5 — Oblivion blends
smoothly (the tripwire morphs taut → snapped over its whole 0.67 s `Forward`),
so the wire visibly jumped.

**Frame reuse keeps the size sane.**  A frame within `FLIPBOOK_TOLERANCE`
(1.0 game unit, ~1.4 cm, max over vertices) of the shape on screen keeps it,
else reuses the closest kept shape, else becomes a new shape.  Fast motion keeps
every frame (the tripwire moves ~7 units a frame: 20 shapes, 91 KB → 838 KB);
slow motion shares shapes.  At 0.25 units `sigillighttowerbase` (two targets on
independent 56.7 s curves) baked 1,152 shapes; at 1.0 it bakes 375 (11 KB →
716 KB).  The heaviest are genuine motion: `obgatemini01` 129 shapes
(194 KB → 5.2 MB), `rootgatedementia01` 306 (205 KB → 3.4 MB).  Each baked
shape gets its normals bent by the change in smooth normals between base and
pose (keeps authored hard edges) and its tangents rebuilt, so a bent shape is
lit as bent.

**The shipped design, and why each part is there:**

1. **Every swapped shape — the base and each baked target — sits under its own
   identity `NiNode` named `"<shape> Swap"`**, and the sequence's
   `NiVisController` entry targets that wrapper, never the geometry.  Census of
   vanilla sequence-driven `NiVisController` entries: the target is a NiNode /
   NiBillboardNode / particle system in **1852/1852** cases (1221 plain
   NiNodes), a NiTriShape in **zero**.  Aiming the controller at the NiTriShape
   (the version shipped until 2026-09-23) produced no visible swap in-game.
   The controller itself is the vanilla pattern: flags 108 on the target
   node's own controller chain, a `NiBlendBoolInterpolator`, and step-keyed
   `NiBoolInterpolator` data in the sequence.
2. **The clone gets its OWN shader property and texture set.**
   `_copy_block_fields` copies reference fields as pointers, so the first cut
   had both ropes wearing the same `BSLightingShaderProperty` (block 29).  The
   engine keeps per-shape render state inside the shader property, so a shape
   made visible mid-view drew through its hidden twin's state: it showed
   **semi-transparent for as long as the player watched it and snapped solid
   the moment it left the screen**.  Settled live on the Vilverin tripwire by
   snapshotting the swap nodes, both geometries and their shader property while
   semi-transparent and again after looking away: the only non-counter
   differences were inside the ONE shader property both ropes pointed at
   (`+0x98`/`+0xa8` pointer fields).  Every other converted shape already had
   its own property.
3. **The clone's wrapper rests hidden** — `apply_rest_visibility` sets the
   hidden bit from the entry's t=0 key — while the clone geometry itself ships
   visible.

**Ruled out while finding the above (1.6.1170, do not re-derive):**

* A hidden node still runs its own controllers, so a vis controller CAN unhide
  its own node: `NiNode::UpdateDownwardPass` (0xd1dad0) and
  `UpdateSelectedDownwardPass` (0xd1dc80) run the controller chain before they
  read the hidden bit, and the geometry update (0xd1c170) always recomputes
  world transform and bound.
* `NiVisController::Update` (0xdc5230) writes bit 0 of the target's flags
  (`+0xF4`) and, only when it UNHIDES a node, ORs 0x1000 into the update data
  passed down.  Setting NiAVObject `kIgnoreFade` (0x8000) on the wrappers and
  geometry live changed nothing.
* The live readback showed the switch itself was always right: the flags
  flipped the frame the keys said, and a freshly loaded tripped wire drew
  correctly.  Only the in-view draw was wrong.

Live tooling that settled it: `tools/live/nif_live.py tree` prints each node's
flags and hidden bit; a sequence clamped on its last frame caches its value,
so forcing a re-evaluation means writing the `NiBoolInterpolator`'s cached
value (`+0x18`) and last time (`+0x10`), with the keys at the data pointer
(`+0x20`) as `{float time, byte value}` at an 8-byte stride.

**The scale swap (commit 90d04a3) froze the game — do not bring scale back.**
It animated the wrapper nodes' scale 1 ↔ 0 through the manager's
`NiMultiTargetTransformController`.  Walking onto the Natural Caverns
(`ImperialDungeon05`) tripwire hard-froze Skyrim — no crash, no log — while
the same mesh worked in Vilverin; in-game bisection pinned it to morph
emulation, and moving the wrappers off the MTC, full t/r/s channels, a
constant rotation channel and scale 1e-4 all still froze.  The cause was never
found.  The visibility swap on the same wrappers has run in Vilverin without a
freeze; the Natural Caverns placement has not been retested.

Bisection detail: long mace `ctrapswingmacelong01.nif` removed → still froze;
tripwire NIF removed → no freeze; `ctrigtripwire01_behavior/` removed → froze;
`_emulate_morphs` disabled → no freeze; the `OnTrapHitStart` scripts stripped →
still froze.  A shader-ALPHA cross-fade (`BSLightingShaderPropertyFloatController`
variable 12) did not freeze but showed no break.  The one unchased lead: the two
placements differ only in `XSCL` (0.75 vs 0.71) and the persistent flag
(Vilverin `0x400`, ImperialDungeon05 not).

Ruled out for the freeze by measurement: MTC target count (vanilla `alduin.nif`
ships 246), targets with no driving block (`fxnocturnalbirdl.nif`: 10 targets,
1 block), missing `NiBlendTransformInterpolator` (vanilla ships 0; the engine
allocates them), MTC identity/chain shape, scale 0.0 vs 1e-4, the orphaned
manager-chain `NiTransformController` (identical in the working build),
`-FLT_MAX` statics, palette registration, scene-graph reachability, clone
flags, block priorities, and both maces' NIFs and graphs.  A 250-mesh vanilla
census found 36 `NiTransformData`, 8 with scale keys, all on `skeleton.nif` at
a constant 1.0 — vanilla never animates a node's scale; it hides geometry
mid-sequence with `BSEffectShaderPropertyFloatController` (25),
`BSLightingShaderPropertyFloatController` (17) and
`BSNiAlphaPropertyTestRefController` (4).

Verified exe layouts from that hunt (GOG/AE exe):
`NiMultiTargetTransformController` interpolator slots `+0x48` (`count * 0x48`,
allocated at 0xd0d857), target pointers `+0x50` (`count * 8`, zero-filled at
0xd0d91f), `num_extra_targets` a ushort at `+0x58`, both walked strictly by
index (0xd0ca20); blend bookkeeping (0xd0b640) walks 0x20-byte records with the
priority byte at `+0x10`; `NiTransformInterpolator` `+0x18` translation, `+0x24`
rotation, `+0x34` scale, `+0x38` data; `NiTransformData` `+0x10/+0x18`
translation count/keys, `+0x20/+0x28` rotation, `+0x30` scale keys,
`+0x14/+0x24` key types; `NiControllerSequence` controlled blocks at a 32-byte
stride from `+0x20`, count `+0x18`, priority insertion at 0xd08890, and its
constructor seeds `-FLT_MAX` (0xd04549–0xd04589), so that sentinel is
engine-native.

**Vanilla's own tripwire is not a model for this.** `traptripwire01.nif` has
no sequence and no swap: it is two `bhkBallSocketConstraintChain` ropes of
dynamic bodies hung from fixed pegs, never joined to each other, which its
`Tripwire` script (extends `TrapTriggerBase`) holds with `SetMotionType` and
releases on trigger.  Oblivion's wire snap is a shape blend with no physics,
so a swap is the faithful conversion.

**The two CTDs the first vis-swap path caused** — both apply to the current
swap and to blocks COPIED from Oblivion, which `normalize_blend_interpolators`
repairs:

1. **NiBoolData keys must be `CONST_KEY` (5), never `LINEAR` (1).**  Writing 1
   CTD'd on entering Vilverin — an access violation at `0x0` inside
   `NiBoolData::Load`, `RSI/R14 = NiBoolData*`,
   `inputFilePath: ctrigtripwire01.nif`.  Census: **3449/3449 vanilla Skyrim
   and 1296/1296 Oblivion source NiBoolData store 5**; `nif [version].xml`
   documents type 5 as "Step function.  Used for visibility keys in
   NiBoolData".  The two types are byte-identical on disk
   (`{float time, byte value}`), so the file round-trips through PyFFI and
   NifSkope cleanly and nothing but the engine notices — hence check 3 in
   `tools/validate/nif_block_type_audit.py`.
2. The Manager-Controlled flag defect below, which the same crash hunt found.

Both apply to every NiBoolData / blend interpolator the converter synthesizes
or copies through, morph emulation included.

### NiBlendInterpolator must be Manager Controlled (2026-08-02)

Fixing the key type above moved the Vilverin CTD one block later, to
`lock inc [rax+0x08]` — an AddRef — with `RDI = NiVisController*` and
`rax = 0xBF800000421BED50`.  That high half is `-1.0f`, i.e. **float data being
dereferenced as a pointer**, which is the signature of a block read at the wrong
length.

`NiBlendInterpolator.Flags` bit 0 is **Manager Controlled**.  nif.xml makes the
next SEVEN fields (Interp Count, Single Index, High Priority, Next High
Priority, Single Time, High Weights Sum, Next High Weights Sum) conditional on
that bit being **clear** — so a manager-driven block is 7 bytes and a
free-standing one is 15.  We were writing `Flags=0` into a 7-byte block, so the
engine read 15 bytes, ran into the following block, and AddRef'd whatever it
found.  `Single Time` defaults to `-1.0f`, which is precisely the `0xBF800000`
in the faulting address.

Vanilla is unanimous: **8779/8779 `NiBlend*Interpolator` blocks store Flags=1,
Array Size=2** (2688 bool, 5520 float, 571 point3).

The underlying cause is a **PyFFI 2.2.3 broken layout** (cf. NiPSysData): it
models this block as `unknown_short` + `unknown_int` + `bool_value` instead of
`byte Flags, byte Array Size, float Weight Threshold, byte Value`.  So
`unknown_short = 0x0201` IS `Flags=1, ArraySize=2`.  These are **not padding** —
the usual "never touch unknown_*" rule does not apply, because they are real
named fields PyFFI failed to describe.  Critically this hits blocks **copied**
from Oblivion as well as synthesized ones: PyFFI reads them under the old
version's layout and rewrites them under Skyrim's, and the flags do not survive.
`normalize_blend_interpolators` therefore stamps the header onto every blend
interpolator in the tree after all controller passes, and
`tools/validate/nif_block_type_audit.py` checks it (check 4).  261 blocks across 26
Oblivion meshes were affected — gates, magic effects, creatures and the enemy
health bar, not just the morph-swap meshes.

### Oblivion `sound:` text keys are NATIVE in Skyrim — never rewrite them (2026-08-05)
<a id="sound-text-keys-are-native"></a>

**This section previously said the opposite.  The rewrite it described silenced
244 Oblivion meshes** — every animated gate, portcullis and prison door — and
was reverted after the user reported StoneWallGateDoor01 losing its iron creak.
Confirmed fixed in-game 2026-08-05.

SkyrimSE keeps Gamebryo's own text-key sound handler.  At `0x1401db723` (GOG
build) it compares the key against the literal **`"Sound: "`** (`r8d = 7`) with
**`_strnicmp`, which is CASE-INSENSITIVE**, so Oblivion's lowercase `sound: X`
matches, and it plays whatever follows those 7 characters (`lea rcx, [rbx + 7]`
at `0x1401db890`).  The same handler also accepts `"Enum: StopSounds "`.  Both
literals sit at file offsets `0x1635f50` / `0x168d0ec`.

**The trap:** the earlier pass searched the exe for lowercase `sound:`, found
nothing, and concluded the keyword did not exist.  The string is capitalised.
Case-fold before concluding a string is absent from the exe.

`SoundPlay.<SNDR EDID>` is a DIFFERENT, non-interchangeable channel: it is
matched against a behaviour graph's declared event-name table, so it only works
on meshes that have one (38 of the 39 vanilla meshes using it carry
`BSBehaviorGraphExtraData`).  Converted doors deliberately have **no** graph —
attaching one to an Open/Close door CTDs it on cell load — so the rewritten key
matched nothing and was dropped.  Creature/actor sounds still correctly use
`SoundPlay.` because they DO go through a graph (`hkx_behavior.py`).

`_convert_sound_text_keys` is therefore a documented no-op returning 0.

### DOOR sound records: SNAM/ANAM must name an SNDR (2026-08-05)

Separate defect found in the same investigation.  TES5 `DOOR` SNAM (open) /
ANAM (close) / BNAM (loop) reference a sound **descriptor**, not a SOUN — xEdit
declares `wbFormIDCk(SNAM, 'Sound - Open', [SNDR])`, and all 90 sounded vanilla
Skyrim DOORs agree (WRDragonSideDoor01's SNAM `0005AFC9` is the SNDR
`DRSWoodImperialDouble01OpenSD`).  The converter was writing the TES4 SOUN id,
so all 417 sounded Oblivion doors held a wrong-typed reference.

DOORs are written in import Phase 1, before Phase 3 mints the descriptors, so
`convert_DOOR` stores the SOUN id as a placeholder and
`items.patch_door_sounds` resolves it afterwards — the same approach
`actors.patch_actor_sounds` uses for CSDI.  Allocating descriptor ids earlier
would shift every other generated FormID.

Oblivion also lets a door's sound live ONLY in the mesh (the record has no
SNAM/ANAM at all — StoneWallGateDoor01 and 57 other doors).  Skyrim's record
channel is what vanilla relies on, so `asset_convert/audio/door_sounds.py` reads the
model's `Open`/`Close` sequence text keys and `items.load_door_model_sounds`
lifts those names onto SNAM/ANAM.  The sequence NAME decides the slot, so the
NIF is parsed rather than byte-scanned.

### PlayGroup chains: NEVER convert to PlayAnimationAndWait (2026-08-02)

`PlayAnimationAndWait("<seq>", "<event>")` waits on a BEHAVIOR-GRAPH event.  A
BGSGamebryoSequenceGenerator state has no completion event and NIF text keys
are not delivered as anim events — vanilla proof: every gamebryo-sequence
object script (norsarcophagustopanim01script, dunsolitudejailopencelldoor, the
Solitude jail-wall scene) uses plain `PlayAnimation` with a state debounce and
never waits; the scripts that DO wait (`sarcophagusskulllock01script`
"alldone", `dunlabyanimateontrig` "done") drive native-hkx objects whose
events are havok annotations.  The wait blocks its thread forever.  Consecutive
same-frame PlayGroups therefore stay plain `PlayAnimation` calls (last event
wins — which also matches Oblivion's own queue-depth-1 PlayGroup semantics).

- BSXFlags must have bit 0 set (ANIMATED) → value 139 (0x8B) for animated meshes. Detect via NiControllerManager on root.
- Animation data: NiControllerSequence StringPalette offsets MUST be resolved BEFORE version upgrade (UV2=11→83). After upgrade, PyFFI switches to direct-string mode and offsets are ignored → empty node_name → crash.
- **EVERY `NiTimeController` needs "Compute Scaled Time" (flags bit 6, 0x40) or a `PlayAnimation()`d sequence NEVER MOVES — the CharacterGen secret-wall fix (2026-07-26, `_fix_controller_flags`)**: `nif.xml` `TimeControllerFlags` declares bit 6 `default="true"`, and Oblivion's engine computed scaled time unconditionally without ever writing the bit — every controller in the Chargen secret-wall/switch NIFs stores 12 / 40 / 44, always 0x40 **clear**. Skyrim reads the flag: the sequence binds its targets, `ObjectReference.PlayAnimation("Forward")` returns success and logs **no Papyrus error**, but scaled time never advances so the object sits on frame 0 forever. Symptom was maximally misleading — the quest stage said the wall had opened (the switch fired, `secretDoor` flipped 0→1, the timer ran; all visible in the `TES4CharGen` user log) while the wall physically stayed shut. Census: across 62 vanilla animated door/activator meshes (Windhelm animated secret doors, Nordic animated doors, Dwemer doors, Labyrinthian panel, Winterhold anim door) **157/157 `NiMultiTargetTransformController` have flags=108 (0x6C)** and every other controller — `NiTransformController`, `NiControllerManager`, `NiVisController`, `NiFloatExtraDataController` — has **76 (0x4C)**; both set 0x40, and 108 vs our 44 differs *only* in this bit. Fix ORs 0x40 into every `NiTimeController` in the tree before the version upgrade, so activators/doors/traps/levers are all covered. This generalizes the emitter-controller rule below (which had it only for `NiPSysEmitterCtlr`/`NiPSysUpdateCtlr`) and the `0x48` already hardcoded on the flip-book `BSEffectShaderPropertyFloatController`.
  - **Things that were NOT the cause** (all verified fine, don't re-investigate): the Papyrus conversion (`PlayGroup` correctly routed to `PlayAnimation` via base-signature lookup); the dropped `prisonSecretWall01`/`... NonAccum` controlled blocks (genuinely empty — `data=None`, zero translation — the real motion is on the `bed`/`wall` transform tracks, which survive with all 111/21 keys); the missing `NiStringPalette` (correct — Skyrim uses direct strings); sequence names `Forward`/`Backward` (vanilla `VolunruudLeftDoor`/`RightDoor` use exactly these); and the absent ACTI `PNAM`/`FNAM` (marker color + flags, cosmetic — 1739/1753 vanilla write FNAM=0).
  - **CORRECTION (2026-07-26): the "needs no BGED" claim previously recorded here was WRONG.** The earlier note reasoned that because 227 ACTI + 196 DOOR vanilla records ship `NiControllerManager` meshes, the in-NIF sequence was sufficient. That census is real but does not support the conclusion: `ObjectReference` exposes **two different animation paths** — `PlayGamebryoAnimation` drives an in-NIF `NiControllerSequence`, while **`PlayAnimation`/`PlayAnimationAndWait` drive the BEHAVIOUR GRAPH and require an animation graph manager**, which exists only when the root carries a `BSBehaviorGraphExtraData` naming an hkx project. `PlayGroup` converts to `PlayAnimation`, so without a BGED the call is accepted, returns immediately, logs no Papyrus error, and nothing moves. Fixed by generating the graph (below).

## Animated-object behaviour graphs (`asset_convert/havok/hkx_animobject.py`)
<a id="animated-object-behaviour-graphs"></a>

**ONLY meshes whose sequences carry SCRIPT-DRIVEN group names get a GENERATED graph** — `Forward`, `Backward`, `FastForward`, `FastBackward`, `Left`, `Right`, `Equip`, `Unequip`, `SpecialIdle`, `Stagger` (`_SCRIPT_DRIVEN_SEQUENCES` in `nif_converter.py`; 161 trees on Oblivion.esm). Ambient `AutoPlay`/`AutoLoop` meshes point at vanilla's shared `GenericBehaviors\Autoplay.hkx` instead (next section). Generated by `collect_sequence_names` + `_add_animobject_bged`. Layout, sibling to the mesh so two animated NIFs in one folder never collide:

    <model>_behavior/<model>.hkx            project    (this is what BGED names)
    <model>_behavior/Characters/Character01.hkx
    <model>_behavior/CharacterAssets/Skeleton.hkx      1-bone; transforms live in the NIF
    <model>_behavior/Behaviors/Behavior00.hkx          state machine + Gamebryo generators

The bridge is **`BGSGamebryoSequenceGenerator`**, whose `pSequence` names a NIF `NiControllerSequence`. Each sequence becomes one state AND one same-named event, so `PlayAnimation("Forward")` sends `Forward` and lands on the generator bound to the `Forward` sequence, plus a synthetic **`Rest`** start state. BSX bit 0 (Animated) must also be set or the graph loads and never ticks.

### 🛑 A NIF CANNOT BE HOT-RELOADED — the engine caches it for the whole process

Skyrim parses each NIF once and keeps it in its model cache for the lifetime of
the process. `coc` out and back unloads the CELL, not the model: the reload
hands back the cached copy, so **overwriting the mesh on disk while the game is
running changes nothing.** There is no console command that drops it (`pcb`
purges the cell buffer, not the model cache).

Consequence for debugging: **every in-game observation describes the build that
was on disk when the game LAUNCHED.** Rebuilding a mesh mid-session and
re-entering the cell tests the OLD file and silently produces a "the fix did
nothing" result. On 2026-08-18 this invalidated several rounds of ambient-mesh
testing before it was noticed.

So: deploy the mesh, THEN relaunch, then test. One build per launch. Verify the
DEPLOYED file (`Data\meshes\...`, not just `output/`) before asking for a
relaunch — a wasted launch costs a full play cycle.

Note the deploy is hardlinked here (Vortex): writing `output/...` updates
`Data/...` in place, same inode. That makes deployment instant and is easy to
mistake for a working hot reload. It is not one.

### Ambient (self-playing) meshes: the vanilla AutoPlay/AutoLoop pair (2026-08-18, mechanism read out of the live engine)

Oblivion authors ambient scenery (arena spectator crowds, `palacefont01`'s
fountain, `watersurf01`, candle flames) as a STAT whose NIF holds a self-playing
`Idle` sequence. TES4 starts `Idle` on load; Skyrim starts nothing. What vanilla
does instead, and what `_autoplay_ambient_sequences` now emits:

- BGED → **`GenericBehaviors\Autoplay.hkx`**, the shared graph every one of the
  63 vanilla self-playing meshes points at (`hkx_animobject` returns it for
  meshes whose sequences are only ambient; anything a script drives by name
  keeps its generated project). Its state machine STARTS on `AutoplayState`
  (sequence **`AutoPlay`**), and on that sequence's `End` event hands off to
  `AutoLoopState` (sequence **`AutoLoop`**). Its events are `End`, `StopEffect`,
  `AutoOneOff`, `Reset`, `AutoReset` — `AutoPlay`/`AutoLoop` are sequence names,
  never events.
- The authored `Idle` becomes **`AutoLoop` and keeps its authored cycle type**;
  a full-length **`AutoPlay` clone with cycle type CLAMP** is added for the
  start state (shares the interpolators — the engine binds the same pointers
  from both and plays).
- **CycleType is 0 = LOOP, 1 = REVERSE, 2 = CLAMP.** All 116 Oblivion `Idle`
  sequences are 0. Vanilla: `AutoPlay` CLAMP 53/54, `AutoLoop` LOOP 39/53.
  Looping is the SEQUENCE's — `BGSGamebryoSequenceGenerator` has no looping
  field and `AutoLoopState` has no self-transition.

**Why it took ten builds** (all read back out of the running game with
`tools/live/nif_live.py sequences|nodes` on a loaded arena spectator, then patched
in memory with `set-cycle` / `set-pose` + `sae AutoReset` to prove each fix
before rebuilding):

1. *"Plays one cycle then freezes"* — the converter had `_CYCLE_LOOP = 2`, i.e.
   it wrote CLAMP into `AutoLoop`. Live: `Autoplay` state INACTIVE (finished,
   `End` fired), `AutoLoop` state ANIMATING with lastTime far past its end —
   frozen on the last frame. Flipping the loaded sequence's `cycleType` to 0 and
   `sae AutoReset` made it loop indefinitely.
2. *"Rotated ~90°"* — Bethesda's exporter writes the sequence's **accum root**
   (`Bip01`, `DoorLowerINT01`, `MetalGate`…) as an IDENTITY pose and moves the
   node's real transform onto the **`<accum> NonAccum`** child (crowd:
   NonAccum key 0 = Bip01's authored (−0.34,−1.64,64.07) / 82.5° Z; census of
   464 Oblivion NIFs: 853 accum-root entries, 815 identity poses, 0 that
   move). Both engines apply the identity and NonAccum restores the world
   pose. Our data-less sentinel rule (rotation/scale → −FLT_MAX) left Bip01's
   authored 82.5° in place while NonAccum re-applied its own 82.5° → the crowd
   faced 165° off whenever the sequence actually played, and was correct only
   in builds where nothing played. `_accum_root_mode` now leaves a
   'transferred' accum-root entry exactly as authored (identity applied) and
   sentinels every channel only for an 'orphan' (nothing carries the
   transform). Live: Bip01 → identity, NonAccum → (−0.34,−1.64,~64) at 82.5°,
   crowd at the authored pose, looping.

Ruled out along the way, do not retry: STAT→MSTT promotion (crashed on save
load — 94 vanilla STATs carry a BGED, the record type is not the gate);
`selfTransitionMode` FORCE_TRANSITION_TO_START_STATE (looping is the
sequence's); a generated per-mesh graph instead of the shared one (works, but
the shared one is what vanilla ships and what is verified live).

**`sae <event>` is the fastest first diagnostic** for any "animated object does
nothing": empty reply = the graph is bound and knows the event; *"not processed
by the graph"* = no such event or no graph. Then `python tools/live/nif_live.py
sequences <ref>` (state, cycleType, lastTime per sequence) and `nodes <ref>
--names ... --samples N` (is the bone moving, and where is it) instead of
theorising.

### The four defects that had to be fixed before it worked in-game (2026-07-26, all CONFIRMED)

Every one was invisible to structural inspection **and to NifSkope, which renders and animates the NIF perfectly while never loading the hkx at all** — so "it's fine in NifSkope" tells you nothing about any of these. In symptom order:

1. **BGED must NOT carry a `meshes\` prefix — the object is otherwise NEVER RENDERED.** The engine prepends `Meshes\%s` itself, so `meshes\tes4\…` resolves to `Meshes\meshes\tes4\…`, the project is never found, and the object silently gets no graph and never draws. Vanilla stores `Clutter\BlackPool\BlackPoolSecretDoor\NocturnalsSecretDoor01.hkx`; our own working bow rig stores `Weapons\Bow\BowProject.hkx`. **The path is relative to `meshes\`, not to `data\`.**
2. **The skeleton's bone must be the fixed dummy name `x_SingleBone`, never the model stem.** The rig is a placeholder (the real motion is in the NIF's sequences), and vanilla's `SingleBoneSkeleton.hkx` uses that reserved name precisely so it can never collide with a NIF node. Naming it after the model made the engine bind the graph's identity bind pose onto the object and place it **far from its authored worldspace position**.
3. **`startStateId` must point at a state that plays NOTHING.** Vanilla starts on an idle (`BlackPoolSecretDoor` `startStateId=3` = `AnimIdle01`) and reaches the motion only by event. Oblivion sources have no idle sequence — a converted wall has only `Forward`/`Backward` — so starting on state 0 made the wall **swing open by itself the instant the cell loaded**. Fix: synthesise a `Rest` state whose `pSequence` is empty (it holds the NIF's authored rest pose = closed) and start there. It is the LAST state, so the event→stateId mapping of the real sequences is untouched.
4. **Transitions must live ON EACH STATE, not only in the machine's `wildcardTransitions`.** Vanilla's Gamebryo state machine sets `wildcardTransitions=null` and gives every state its own `hkbStateMachineTransitionInfoArray` (`State00` carries event 0 → state 4). Leaving `Rest.transitions = null` made the start state a **DEAD END**: nothing could open the wall again, from the quest *or* from console `activate`. Each state now reaches every *other* sequence (self-transitions excluded, or a repeated event restarts the sequence mid-play); the global wildcard array is kept as a harmless second route.

**`Open`/`Close` MUST NOT get a graph — attaching one is a CTD (2026-07-26).** They are the engine's own DOOR group names, driven natively through the NIF's `NiControllerManager`; no converted script ever names them (census of 18,566 output scripts: Forward 418, Backward 192, Unequip 45, Equip 27, SpecialIdle 10, FastForward 8, Left 6, FastBackward 6, Right 5, Stagger 1 — **zero Open/Close**). `prisonCellGate01` animated perfectly before the graph existed; giving it one made the engine bind the sequence through the graph instead of natively and crash on cell load (`EXCEPTION_ACCESS_VIOLATION`, `movdqu xmm2,[rax]` with `rax=0`, relevant objects `BGSGamebryoSequenceGenerator "GamebryoSequenceGenerator00"` + `hkbBehaviorGraph "prisoncellgate01"`). Vanilla agrees: the graph-driven `NocturnalsSecretDoor01` uses `AnimIdle01`/`AnimPlay01`, never Open/Close. **A mesh that already animates is not a mesh that needs a graph — check whether a script actually drives it first.**

**Template: vanilla `NocturnalsSecretDoor01`** — `Behaviors/Behavior00.hkx` ships loose at `references/Skyrim Animations/meshes/clutter/blackpool/blackpoolsecretdoor/`, the NIF at `references/Skyrim Meshes/meshes/clutter/blackpool/blackpoolsecretdoor/nocturnalssecretdoor01.nif` (BGED + BSX 0x0B + a 12-object `NiDefaultAVObjectPalette`). Decompile with `hkx_xml.decompile_hkx` and match field-for-field. Traps found the hard way:

- **hkxcmd fails SILENTLY on a malformed packfile**: it prints `Converting '...'`, exits non-zero, and writes **no file and no error text**. A missing or extra param is indistinguishable from any other failure, so bisect against the decompiled vanilla file rather than guessing.
- **`BGSGamebryoSequenceGenerator` takes exactly `pSequence`, `eBlendModeFunction`, `fPercent`.** The class also declares `bLooping`/`bDelayedActivate`/`fTime`/`events`, and they appear in hkxcmd's own field-name table in the exe — but vanilla marks them **`SERIALIZE_IGNORED`** and emitting them breaks the compile. *The exe's class definition lists fields that must not be written; only the decompiled vanilla file distinguishes them.*
- **`hkbBehaviorGraphData` needs `wordMinVariableValues` + `wordMaxVariableValues`** between `eventInfos` and `variableInitialValues`.
- **`eventToSendWhenStateOrTransitionChanges` is a nested `hkobject` (`{id:-1, payload:null}`), not `null`**; likewise `triggerInterval`/`initiateInterval` are nested `hkbStateMachineTimeInterval` structs (all `-1`/`0.0`), not tuple literals — `param_structs` renders values inline and cannot express either, so they are built with `param_raw`.
- **`hkbBlendingTransitionEffect.flags` is the integer `0`**, and `selfTransitionMode` is the full `SELF_TRANSITION_MODE_CONTINUE_IF_CYCLIC_BLEND_IF_ACYCLIC`.
- Class signatures for all of these are registered in `hkx_xml.SIGNATURES`, read off the vanilla file.
- Sequences that `_process_controller_manager` stripped to zero controlled blocks are **excluded** — a state for a dead sequence makes `PlayAnimation()` succeed while animating nothing, reintroducing the original silent failure.
- **The skeleton needs exactly ONE `referencePose` entry per bone, emitted ONCE.** `HkxPackfile` happily writes a duplicate `hkparam` and hkxcmd keeps the **FIRST**, so an empty `referencePose` emitted before the real one yields a skeleton with 1 bone and 0 poses; binding a sequence then indexes past the end and null-derefs (this was the second half of the prisonCellGate01 CTD).
- **hkxcmd compiles the identity pose into a ZERO QUATERNION — patch the bytes (`fix_identity_quat`).** The XML text `(0 0 0)(0 0 0 1)(1 1 1)` is exactly what every shipped creature skeleton uses, but for this file hkxcmd writes the rotation slot as all zeros. A zero quaternion is not a rotation, so the single bone the graph drives has no valid bind pose and **the entire object renders nothing** — while the graph loads without error and no Papyrus message appears. Havok's **binary** quaternion is **w-first** `(1,0,0,0)`, unlike the XML's xyzw, so the fix rewrites the 48-byte pose block (trans/quat/scale hkVector4 slots) in the compiled WIN32 file, before the AMD64 step. Verified **byte-identical to vanilla `clutter\beehive\characterassets\SingleBoneSkeleton.hkx`** (1104 bytes, 0 diffs) — that file is the reference for any single-bone animated object.
- **`hkbCharacterData`'s field list is not what the name suggests** — copy `clutter\beehive\characters\Character00.hkx`: `characterControllerInfo, modelUpMS, modelForwardMS, modelRightMS, characterPropertyInfos, numBonesPerLod, characterPropertyValues (this is where the hkbVariableValueSet hangs), footIkDriverInfo (null POINTER, not an array), handIkDriverInfo (null), stringData, mirroredSkeletonInfo, scale`. There is **no `variableInitialValues` and no `aiControlDriverInfo`**. Getting it wrong made hkxcmd silently drop the `hkbVariableValueSet` — detectable by diffing the packfile's `__classnames__` string table against vanilla's, which is a fast sanity check for any generated hkx.
- Vanilla lays these files out in the mesh's OWN folder (`clutter\beehive\{behaviors,characters,characterassets}\`), not a `<stem>_behavior\` subfolder; ours nests them so two animated NIFs in one directory cannot collide on `Character01.hkx`. Both work — the paths inside the character file resolve relative to the project file's folder. Our project hkx is byte-identical to vanilla's (880 bytes).
- Final step is `convert_hkx_to_amd64` on every file: SSE loads only 64-bit packfiles (verified pointer-size byte 8 on all 161×4 outputs).

## Accum-bone bind pose leaks into every clip (user patches, 2026-08-30)
<a id="accum-bind-pose-leak"></a>

**Code:** `asset_convert/havok/kf_decode.py` `split_root_motion`,
`asset_convert/havok/hkx_behavior.py` `generate_creature_project`.

Four user patches (`AshVampireFixedAnimations`, `AshCreaturesFixedAnims`,
`WingedTwilightFixedAnims`, `FixedAshCreatureOrientation`) covering
ascendedsleeper, ashghoul, ashslave, ashvampire, ashzombie and wingedtwilight.
102 hkx files; 89 of them differ from our output by 2 or 5 bytes only — a single
40-bit compressed quaternion (`_read_40bit_quat`, 5 bytes) on **track 1
(`Bip01 NonAccum`)**, replaced with the identity `01 18 80 01 38`.

### Bug 1 — the accum ROOT was played twice (SUPERSEDED diagnosis)

The first reading of these patches — "NonAccum's frame-0 rotation is a
leak, flatten it to identity" — was wrong, and shipping it turned every
super mutant 90° and put the centaur on its back once track 0 stopped
carrying the rest pose. The real contract is
[the accum root plays as identity](asset_convert_falloutnv.md#accum-root-identity):
the patches only *looked* like a NonAccum fix because our track 0 still held
the skeleton rest pose, and rest pose + NonAccum composed the same heading
twice. The user zeroed the copy on track 1; the converter now zeroes the
copy on track 0 and keeps NonAccum's authored frame 0 (54.18° / 67.11° /
89.99° / 32.67° above are the correct static headings, not leaks).

Measured frame-0 accum yaw, ashvampire source KFs:

| clip | NonAccum frame-0 yaw (kept) | track 0 |
|---|---|---|
| idle / turnleft / attack / equip / awarevocal / ragdollpose | 54.18° | identity |
| stagger | 67.11° | identity |
| wingedtwilight cast | 89.99° | identity |
| ashslave / ashzombie | 32.67° | identity |

Ashvampire `idle.kf` authors a STATIC `Bip01` track (54.2°, Z 85.17), a copy
of the bind pose: `ACCUM_ROOT_BONES` tracks are always written as identity,
so it no longer double-counts against NonAccum's 85.155.

### Bug 2 — the walk speed-bake cap truncates the clip

`generate_creature_project` bakes commanded speed into the walk/run clip with
`min(max(formula / natural, 1.0), cap)`, cap 1.4 walk / 2.0 run. Ash vampire
CREA `Speed=14` → formula 46.3 u/s; the clip's natural root-motion speed is
15.64 u/s, so the required factor is 2.96 and the cap clamps it to 1.4. Our
`walkforward.hkx` is 47 frames / 1.533 s; the patch is 24 frames / 0.767 s
(factor 2.71). The cap is the whole defect — the creature walks in place.

### Bug 3 — model axis vectors: NOT A BUG, patch rejected

`FixedAshCreatureOrientation`'s character.hkx swaps `modelForwardMS`
(offset 576) to (1,0,0,0) and `modelRightMS` (592) to (0,-1,0,0).

**Vanilla uses BOTH conventions**, so neither is universally right — census of
9 vanilla creature `character.hkx` pulled from `Skyrim - *.bsa`:

| forward / right | count | creatures |
|---|---|---|
| (1,0,0,0) / (0,-1,0,0) | 6 | frostbitespider, giant, hagraven, icewraith, mudcrab, wisp |
| (0,1,0,0) / (1,0,0,0) | 3 | bear, mammoth, skeever |

The value describes THAT skeleton's authored facing, not a constant. Our
hardcoded (0,1,0,0)/(1,0,0,0) matches vanilla bear/mammoth/skeever exactly.
Deriving it per-skeleton is the real fix and is NOT built — the vanilla clips
are in-place (root delta 0,0,0 on all 5 checked), so root motion cannot
supply the facing and no derivation was established. Left unchanged rather
than swapping one hardcoded guess for another.
