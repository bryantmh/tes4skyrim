# Havok contact callback: the "fixed until touched" hook site

Papyrus cannot see a non-actor being bumped -- `OnHit` fires only for
weapon/spell/explosion hits -- and SKSE adds no physics event. Its whole
registerable set is `OnActorAction`, `OnControlDown/Up`, `OnCrosshairRefChange`,
`OnKeyDown/Up`, `OnMenuOpen/Close`, `OnNiNodeUpdate`, `OnPlayerCameraState`
(`skse64/PapyrusEvents.cpp`), and its `ObjectReference.psc` adds 25
inventory/enchantment functions and no motion type. The engine's own contact
callback is the only real detector.

## The one that actually runs

`skyrim_disasm.py --find ContactListener` returns six RTTI names. Counting how
many times each vtable is installed (`lea reg,[rip+disp]` -> `mov [rcx],reg`;
a vtable pointer is never a file literal, so search .text for the lea, not the
image for the VA) settles which are live:

| Class | vtable (1.6.659) | vtable installs |
|---|---|---|
| `hkpContactListener` | `0x1699310` | 14 (Havok base, many users) |
| `bhkContactListener` | `0x1699350` | **0 -- never instantiated** |
| `bhkBackfaceContactListener` | `0x1699390` | **1 (ctor at `0x03b4e9b`)** |
| `BSRagdollContactListenerModifier` | `0x1829548`, `0x1829618` | 8 |
| `BSIRagdollContactListenerModifierSingleton` | `0x1829530` | 2 |
| `BSRagdollContactListenerModifierInterface` | `0x1767c08` | 3 |

**`bhkContactListener` is an abstract base and is never constructed.** Hooking
its vtable would do nothing. `bhkBackfaceContactListener` is the concrete
listener the game installs.

## Slot 8 is `contactPointCallback`

Slots 0-7 are shared stubs. Slot 8 is where the classes diverge:

| Class | slot[8] | size |
|---|---|---|
| `hkpContactListener` | `0x03b5e40` | stub |
| `bhkContactListener` | `0x0e413f0` | 137 bytes |
| `bhkBackfaceContactListener` | `0x03b4fc0` | **893 bytes** |

The 893-byte one is the live handler, and its first act is
`call 0x140e413f0` -- the 137-byte body that `bhkContactListener` would have
used. So the abstract class's function still runs, as a helper.

`(this = rcx, hkpContactPointEvent* = rdx)`. Off the event:

| Offset | Meaning |
|---|---|
| `+0x08`, `+0x10` | the two colliding bodies |
| `+0x28` | contact point |
| `+0x30` | contact properties; `[+0x13] bit 3` = disable-contact |
| `+0x40` | contact distance (read as float) |
| `+0x50` | per-body collision-filter data |

The 137-byte helper ends `or byte ptr [rax+0x13], 8`, i.e. it SETS that
disable bit, and the 893-byte caller immediately tests it
(`test byte ptr [rax+0x13], 8; jne <skip>`) and bails. That is the backface
rejection: the helper decides a contact faces the wrong way, and the outer
function then skips it.

## Stable IDs -- never hardcode the RVA

| Target | Stable ID | 1.6.659 | 1.6.1170 |
|---|---|---|---|
| `bhkBackfaceContactListener::contactPointCallback` (**hook this**) | **25816** | `0x03b4fc0` | `0x3f4b90` |
| `bhkBackfaceContactListener` vtable | **196246** | `0x1699390` | `0x17ec5f0` |
| the 137-byte backface helper | 79086 | `0x0e413f0` | `0xece580` |

All at offset 0, so each ID names a function start exactly. Feed them to
`tesruntime::Resolve()`, which returns 0 -> "do not hook" on an unknown build.

## Hooking notes

A **vtable swap** on slot 8 of ID 196246 beats patching the function: no
prologue is stolen and nothing is relocated, so none of the hazards in
`project_detour_relocatability` apply -- the same reasoning
`tes_runtime/common/hook.h` records for its call-site patches. Only one object
of this class is ever constructed, so one swap covers the whole world.

**The callback is hot.** It fires per contact pair per step for every pair in
the world, and it already saves five xmm registers on entry. A handler must
early-out on the collision layer (`byte ptr [body+0x139]`, the byte the engine
itself tests) before touching anything else.

**Scoping to converted content only:** the layer byte does not distinguish our
meshes from vanilla ones. The reliable discriminator is the `TESObjectREFR`
behind the body -- its FormID's mod index names the owning plugin -- so a
handler resolves the body to its reference and ignores anything whose file is
not one of ours. `ids.h` already resolves `LookupReferenceByHandle` for exactly
this kind of body -> reference step.

## <a id="no-keyframed-reporting"></a>There is no "keyframed reporting" motion type

**`MO_SYS_KEYFRAMED_REPORTING` does not exist.** The exe's own enum-name table
(`0x1817538`) lists Havok's complete set, and it has seven entries:

    MOTION_DYNAMIC  MOTION_SPHERE_INERTIA  MOTION_BOX_INERTIA  MOTION_KEYFRAMED
    MOTION_FIXED    MOTION_THIN_BOX_INERTIA  MOTION_CHARACTER   (MOTION_MAX_ID)

NIF value 6 is `MO_SYS_KEYFRAMED` (nif.xml `hkMotionType`), not a reporting
variant: *"Simulation is not performed as a normal rigid body. The keyframed
rigid body has an infinite mass when viewed by the rest of the system."*

Two independent confirmations that such a body cannot be pushed:

- `hkpKeyframedRigidMotion` and `hkpFixedRigidMotion` share vtable slots 3/4/5
  with `hkpBoxMotion` replaced by **no-ops**: `0x0abf600` and `0x0abf610` are
  bare `ret 0` (apply-impulse does nothing) and `0x0abf620` zeroes the
  destination (`xorps xmm0,xmm0` into three quadwords) -- an all-zero inverse
  inertia tensor. Infinite mass, exactly as the enum doc says.
- Vanilla census, 1,500 files under `meshes/clutter`: **zero** `motion_system=6`
  bodies. Real clutter is `5 BOX_STABILIZED` on layer 1 (698) and
  `3 SPHERE_STABILIZED` on layer 4 (603); the keyframed bodies present are
  `4` on layer 2 (55), i.e. animated fixtures.

**Consequence: "keyframed until touched" cannot work.** A `MO_SYS_KEYFRAMED`
body has infinite mass and null inverse inertia, so a contact imparts nothing
to it; and `contactPointCallbackDelay` (a real `hkpEntity` field, adjacent in
the same string table) means contact callbacks are not even guaranteed per
step. Waking it would require the hook to call `SetMotionType` itself, which is
a state change the plugin authors -- not the engine reacting to a touch.

The honest options remain: ship clutter dynamic (what is built today), or ship
it `MO_SYS_FIXED`/keyframed and accept that it never moves.

## <a id="deactivation-is-the-lever"></a>Deactivation, not motion type

The mesh stays ordinary dynamic clutter (`motion_system 3`, layer 4, real mass
and inertia -- exactly what `clutter_plan` already ships). What changes is
whether the body is being SIMULATED, which is a runtime property, not a NIF
field.

Havok's own vocabulary for this, all present in the exe's field tables:

| Symbol | Where | Meaning |
|---|---|---|
| `activeSimulationIslands` / `inactiveSimulationIslands` | `hkpWorld` | the two island lists; an inactive island is not integrated |
| `wantDeactivation` | `hkpWorldCinfo` | whether the world deactivates at all |
| `deactivationIntegrateCounter`, `deactivationNumInactiveFrames` | `hkpMotion` | per-body countdown into sleep |
| `deactivationRefPosition`, `deactivationRefOrientation` | `hkpMotion` | the pose sleep is measured against |
| `savedMotion`, `savedQualityTypeIndex` | `hkpMotion` | what a body restores on wake |
| `HK_ENTITY_ACTIVATION_DO_NOT_ACTIVATE` / `_DO_ACTIVATE` | `hkpEntityActivation` | the enum the add/update APIs take |
| `shouldActivateOnRigidBodyTransformChange` | `hkpWorldCinfo` | whether moving a body wakes it |
| `RESPONSE_REPORTING` | `hkpMaterial::ResponseType` | contacts reported, no impulse applied |

**A deactivated body is still in the broadphase.** It is skipped by
integration, not removed from collision -- which is precisely "bypass havok
until collided with", and it is the engine's own steady state for settled
clutter rather than anything we invent. That is why the vanilla census finds no
special motion type for resting objects: they are ordinary dynamic bodies that
have gone to sleep.

So the shape is: ship clutter normally, force it asleep on cell load
(`deactivationNumInactiveFrames` past threshold, or add with
`HK_ENTITY_ACTIVATION_DO_NOT_ACTIVATE`), and let a genuine contact wake it
through the path the engine already uses. `savedMotion`/`savedQualityTypeIndex`
mean the body restores its own simulation state on wake -- nothing to author.

`shouldActivateOnRigidBodyTransformChange` is the field to check first: if the
engine wakes a body merely because its transform was set, forcing sleep at cell
load may not hold.

## <a id="validated-offsets"></a>Validated offsets and the activation gate

Havok ships its reflection metadata in the exe, so the field offsets are read
rather than guessed. An `hkClassMember` is
`{const char* name; ...; u8 type @+24; u16 offset @+30}`:

| Field | Class | Type | Offset |
|---|---|---|---|
| `wantDeactivation` | `hkpWorldCinfo` | bool | **0x155** |
| `shouldActivateOnRigidBodyTransformChange` | `hkpWorldCinfo` | bool | **0x156** |
| `deactivationIntegrateCounter` | `hkpMotion` | int | **0x11** |
| `deactivationNumInactiveFrames` | `hkpMotion` | short | **0x12** |
| `savedMotion` | `hkpMotion` | struct | **0x128** |
| `savedQualityTypeIndex` | `hkpMotion` | short | **0x130** |

Cross-checked against code, not just the table: the shared motion destructor at
`0x0abf5c0` reads `mov rcx,[rcx+0x128]` -- `savedMotion` at the offset the
table gives.

### The gate, at `0x0aa608f`

    cmp  byte ptr [rbx + 0x156], 0    ; shouldActivateOnRigidBodyTransformChange
    je   skip
    cmp  byte ptr [rdi + 0x160], 5    ; body's deactivation/quality class
    je   skip
    mov  rcx, rdi
    call 0x140aa3310                  ; activate the body

`0x0aa3310` is the activation routine: it early-outs when
`byte[body+0x160] == 5`, zeroes `dword[body+0x162]` (the inactive-frame
counters), then re-adds the body through `0x140aba490`.

**This is the answer to the open question.** Setting a body's transform wakes
it ONLY when `shouldActivateOnRigidBodyTransformChange` is set AND the body's
class byte is not 5. Both are readable and both are per-world/per-body, so
forcing sleep at cell load is not defeated by placement: the same `0x160 == 5`
test the engine itself uses is the exemption.

Note `0x0aa6098`/`0x0aa3334` test `[body+0x160]`, adjacent to the
`0x162` counters -- the deactivation state block is `0x160..0x165`.

## Unverified

- Whether TR's placements survive one settle step, or shift before sleeping.
- `contactPointCallbackDelay` defaults: a woken body's first contact callback
  may be delayed by design, which affects how responsive a bump feels.
