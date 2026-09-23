# TESRuntime: the gun shot

**Code:** `tes_runtime/plugin/fire.cpp`, `tes_runtime/plugin/guns.cpp`,
`asset_convert/havok/gun_graph_falloutnv.py` (`fire_triggers`,
`fire_machine`), `asset_convert/havok/gun_patch_falloutnv.py` (`patch_1hm`),
`tes5_import/record_types/equipment_falloutnv.py` (`gun_profile`).

## <a id="why-not-the-crossbow"></a>Why the crossbow path cannot fire a gun

Every ranged shot in Skyrim goes through the engine's attack state machine.
A trigger pull is an attack request; the IDLE tree turns it into
`crossbowAttackStart` only while the attack state is idle; the graph then
raises `arrowAttach`, `bowDrawn`, `BowRelease`, `arrowRelease` and
`attackStop`, each handled by its own class (`ArrowAttachHandler`,
`BowDrawnHandler`, `BowReleaseHandler`, `ArrowReleaseHandler`,
`AttackStopHandler`: 18 such handlers in the RTTI), and each advancing the
state 9 → 10 → 11 → 12 → 0. `ArrowReleaseHandler::ProcessAction`
(0x75ee20, id 42859) refuses unless the equipped weapon's animation type is
7 or 9 and the state is 9 or above; only then does it launch, then sets the
state to 12. So a shot costs one whole engine attack cycle, a held trigger
is one request because bows do not repeat, and events raised faster than
the cycle are dropped: the converted guns fired like crossbows no matter
what the graph did (measured in game across three builds: a fast loop clip
played while the SMG fired a fraction of its rate).

## <a id="the-shot"></a>The shot is the DLL's

The release handler's launch is one call:
`TESObjectWEAP::Fire(weapon, shooter, ammo, poison, nockedArrow)`
(0x2470b0, id 18102), the routine SKSE exposes to Papyrus as
`Weapon.Fire`. With a null ammo it reads the shooter's current ammo itself
(virtual slot 0x4f0/8 on the actor), so it consumes the round, applies the
weapon and ammo damage and launches the ammo's projectile from the weapon.
Nothing in it checks the attack state; the gate lives in the handler.

So the gun clips no longer raise the bow trio. `fire_triggers` puts one
`TES4GunShot` trigger at the clip's fire key (0.02 s for a loop clip with
no key), plus the `AttackWinStart`/`attackStop` pair at the `a:` key and
`AttackWinEnd` at the end as before, so the engine's own press gate
(`attackStop` resets the state to 0) reopens for the next pull. Every
trigger an actor's graph raises reaches `Actor::ProcessEvent` (0x645160,
id 37997), slot 1 of the `BSTEventSink<BSAnimationGraphEvent>` vtables of
`Character` (0x1750180, id 207890) and `PlayerCharacter` (0x17565e8, id
208044), the subobject at +0x30 of the actor. `fire.cpp` replaces that
slot in both vtables; on a `TES4GunShot` tag (pointer-compared against the
interned string, as the engine compares its own) it takes the actor's
equipped weapon through `AIProcess::GetEquippedObject` (0x6b37f0, id
39806) and, if the weapon is a sidecar gun, calls `Fire`. Semi-automatics
fire once per clip at FNV's attack multiplier and accept the next pull at
the `a:` key window; automatics fire once per loop pass at
[rate × loop duration](asset_convert_falloutnv.md#automatic-fire-rate).
NPCs go through the same path: their combat AI sends the attack, the
graph plays the class clip, the trigger fires the round.

## <a id="ammo-restriction"></a>Ammo restriction and reload key

The gun sidecar carries each gun's ammo list (`NAM0` resolved through its
FormList, [formlists](tes4_export_falloutnv.md#formlists)); the DLL
resolves them at DataLoaded. A shot whose current ammo is not in the list
(or with no ammo) is refused and logged, once per two seconds. Equipping
the right round automatically is not done yet: it needs the
`Actor.EquipItem` native, whose id has not been measured.

The reload key is read from `SKSE\Plugins\TESRuntime\TESRuntime.ini`
(`[Guns] ReloadKey=<virtual-key code>`, default 0x52, R) and polled on a
thread; a press posts a main-thread task that sends `TES4GunReloadRequest`
to the player's graph through `IAnimationGraphManagerHolder::
NotifyAnimationGraph` (slot 1 of the holder at +0x38), then posts itself
once more for the next frame. The root `1HM_Behavior` enters the gun
attack state on that event as on `crossbowAttackStart` (hand type 13),
and the fire machine's FireA/FireB take it to Reload at priority 5; the
second send is what the freshly entered machine sees. The magazine count
is the DLL's, per actor (`g_shots`): written into `iGunShots` on every
graph of the actor (first and third person) on each shot, on the draw, and
reset on `TES4GunReloadEnd`
(docs/commentary/asset_convert_falloutnv.md#fire-transitions-are-instant).

## <a id="shot-event-name"></a>The shot trigger must be an engine event name

The first builds compared the wrong field: `BSAnimationGraphEvent` is
`{tag +0, holder +8, payload +0x10}` (Actor::ProcessEvent, GOG 0x645160,
reads `[rdx+8]` as the holder and falls back to the sink's actor when it
is null), and the hook read +8 as the tag, so no event ever matched and
the "event seen" log printed the actor's vtable bytes. The trigger stays
`arrowRelease`: the hook consumes it for a gun holder (fires, returns
without calling the engine's handler), which also suppresses the
`reloadStart` the crossbow release handler raises after every shot, the
cause of a reload animation after each click. Bows and crossbows pass
through. The synthetic zoom event the DLL hands the engine uses the same
layout.

## <a id="bash-and-reload-events"></a>Bash and reload events the root already routes

Measured on the patched `1HM_Behavior` with `behavior_dump.py`: the
vanilla root has `bashStart -> BashState if iRightHandType == 7 || 12`,
and, for the player, `bashStart -> BashState if IsNPC == 0 &&
iLeftHandType != 7 && != 12`. `GetHandAnimType` answers 13 for either
hand of a gun, so on `bashStart`/`bashPowerStart` transitions the
widening rewrites only the `!=` tests (the left-hand exclusion gains
`!= 13`) and leaves the `== 12` tests alone: right-click (block, which is
bash for a ranged weapon) neither bashes nor matches the crossbow. The
same dump showed `reloadStart -> CrossBow_Reload` with
`FLAG_DISABLE_CONDITION`: the engine raises `reloadStart` when ammo is
equipped on a drawn crossbow, so equipping rounds (the DLL's own swap
included) played the crossbow reload. The root now enters a second gun
state, the attack machine started at Reload, on `reloadStart` and on
`TES4GunReloadRequest` (hand type 13, magazine not full), and
`gate_transitions` gives the vanilla `reloadStart` transitions
`iRightHandType != 13`, since with a full magazine the gun transition
declines and the unconditioned vanilla one would still fire.

The widening also wrote `\&\&` into every `!= 12` condition it touched
(`re.sub` keeps an unknown `\&` escape literally), leaving the player's
`blockStart`/`bashStart` conditions unparsable since the first gun build;
the replacement is plain `&&` now.

## <a id="own-the-click"></a>The click is the DLL's: no engine attack action reaches a gun

The crossbow path kept failing one piece at a time (the interrupt below,
then an attack state of 8 through 123 of the user's clicks, because the
state that clears it belongs to the bow draw sequence a gun never plays,
and it blocks sheathing until the engine times it out). So the gun no
longer takes part in it. The action dispatcher hook swallows, for a gun
holder, the attack press (`crossbowAttackStart`), the release
(`attackRelease`), the bash (`bashStart`/`bashPowerStart`) and the
interrupt (`bowEnd`), and turns the press and release into the graph's
own `TES4GunFire` / `TES4GunFireRelease`, sent with `NotifyAnimationGraph`
on the main thread inside the same call. The engine's attack state is
never written for a gun; the shot stays a clip trigger the sink hook
consumes; `bBowDrawn`, the attack-window initiate flags and the crossbow
attack-data entry are gone. Zoom is a variable, not an event: the DLL
writes `iGunZoom` on the actor's `BShkbAnimationGraph` (found as
`graph_vars.py` finds it: process +8 -> middle-high +0x1a8 -> manager
+0x40 small array, graph +0x208 unused here) with the same
`SetVariableInt` the hand-type routing already calls, and every aim pose
is an `hkbManualSelectorGenerator` bound to it, which a bound selector
switches live where an event into an inactive nested machine is refused
(the log showed every zoom event refused). The camera does what the
console `fov` command does (GOG 0x32ed80): `PlayerCamera` (id 400802)
+0x13c / +0x140, the render-state setter (id 106461) and its recompute
(id 105655), plus the two cached defaults (ids 388785 / 388788), with the
gun's FOV multiplier on the values saved at zoom start and restored at
stop.

## <a id="bow-drawn"></a>The button release: bBowDrawn (superseded)

Traced live (`trace:` lines in TESRuntime.log): every engine-dispatched
`crossbowAttackStart` was followed within the same click by an action
`bowEnd`, and no fire-clip trigger (`arrowRelease`, `AttackWinStart`,
`TES4GunFireEnd`) ever reached the sink, while the same event sent by
`player.sae` ran the whole clip and fired. `bowEnd` is in no engine string
and in no Skyrim.esm IDLE: Dawnguard.esm's crossbow `RightInterrupt` idle
raises it under the condition `bBowDrawn == 0`, and the vanilla crossbow
sets `bBowDrawn` once loaded. A gun never set it, so every button release
became the interrupt that cuts the attack. With `player.sgv bBowDrawn 1`
every click fired and the release resolved to `attackRelease`. The gun
ready machine now asserts `bBowDrawn = 1` every frame
(`hkbEvaluateExpressionModifier`), the same contract the loaded crossbow
keeps.

## <a id="hud"></a>The HUD magazine

Skyrim's HUD shows only the spare count: `HUDMenu::ProcessMessage`
(id 51612) answers its HUD-data message type 8 by invoking the Scaleform
`ShowArrowCount(count, show, text)` with a number from the message's
+0x44, a bool from +0x40 and a string from +0x18 (empty by default). The
DLL patches that one Invoke call (found by the `lea r9, ShowArrowCount`
preceding it). A first build filled the text argument with "rounds/size +
spare": the movie then showed nothing at all, not even the vanilla count
(what the movie does with a non-empty text is unknown; the Flash source is
not shipped and the player's HUD movie may be a mod's). The arguments are
now left as the engine made them and, after the call, while the player
holds a gun, the counter's text field
`_root.HUDMovieBaseInstance.ArrowInfoInstance.ArrowCountTextInstance.text`
is overwritten through `GFxMovieView::SetVariable` (vtable 0x10, GetVariable 0x11, Invoke 0x17, checked against real callers); the view is the first field of the GFxValue ObjectInterface the engine's Invoke wrapper (id 82256) takes as its first argument, not the argument itself (a build that called the wrapper's argument as the view crashed on the first counter update); the
first call probes that path and its parents with `GetVariable` (0x11) and
logs `hud: <path> -> found/missing (type N)`, so a HUD movie with another
layout shows itself in the log. Rounds are the shots since the last reload
as the DLL counts them (the shot hook increments, `TES4GunReloadEnd` resets),
the size the gun's DATA Clip Size. A reload changes no ammo count, so the
fresh magazine shows at the next shot or equip: a re-send of the message
from a main-thread task with hand-built GFxValues crashed the game on the
first draw (crash-2026-09-09-21-37-11: Invoke id 82256 +0xe7 jumped to
null from the DLL's task), so only the engine's own messages are touched.
Whether the vanilla HUD renders that text argument is not known from any
sanctioned source; the number stays the spare count so nothing regresses
if it does not.

## <a id="quiver"></a>No quiver

Skyrim attaches the equipped AMMO's model to the actor's `QUIVER` node;
with FNV's box model (or the earlier bullet model) that attachment sat at
a gun holder's feet. On every `weaponDraw` and every ammo equip
(`reloadStart`, a frame later) the DLL looks the node up on the actor's
3D with the engine's `GetObjectByName` (id 76207, the lookup
`Actor::ProcessEvent` uses for its own node events) and sets the
`NiAVObject` hidden flag (bit 0 of +0xf4) when a gun is in hand, clearing
it on a non-gun draw so bows keep their quivers. Both skeletons are
walked, `Get3D(false)` and `Get3D(true)`: `Get3D()` answers for the
current camera, so a first build hid the quiver on whichever skeleton the
player was not looking at.

## <a id="dry-fire"></a>Every click answers: the dry fire

Measured live (bridge, `player.getitemcount`, `graph_vars.py`): with a
9mm pistol, 49 rounds of 10mm and none of the three 9mm rounds, the first
click played the fire clip, the DLL found no usable round, unequipped the
10mm, and every later click was refused by the engine's own no-ammo check
before any graph event was raised (the recording hook on
`Actor::ProcessEvent` saw zero player events per click; `IsAttackReady`
stayed 1 and the attack state 0). That silent refusal is the "sometimes
one shot, then nothing" report. With 40 rounds added the whole cycle was
clean at one click per 1.2 s: 13 shots, the automatic reload, four more
shots, 17 rounds consumed. Clicks faster than the clip's `a:` key are the
fire rate and are dropped, as in FNV.

The click is now decided by the DLL, in the engine's own action path:
every call to the actor action dispatcher (id 39004; GOG 0x675460, the
routine that sets attack state 8 on `crossbowAttackStart` and 12 on
`attackRelease`) is patched. For `crossbowAttackStart` on a gun holder it
first looks for a round: the current ammo if the gun loads it, else the
first listed round the actor carries (equipped silently). A found round
is fired explicitly (`Fire(weapon, actor, ammo)`), so the shot never
depends on the swap having landed. With no round the action is dropped
and the gun's own TNAM (`Sound - Gun - No Ammo`, e.g.
`WPNPistol10mmFireDry`) plays through the engine's `SoundPlay` animation
event with the converted descriptor's EditorID (`TES4_<edid>_SNDR`) as
payload. A wrong round is no longer unequipped: leaving it in hand keeps
the engine's attack button alive, so the dry fire, not silence, answers.
The reload key answers the same way when no round can be loaded.

## <a id="ammo-natives"></a>Ammo enforcement through the natives

A shot or a reload first runs `EnsureAmmo`: if the current ammo is in the
gun's list it proceeds; else the first listed round the actor carries
(`ObjectReference.GetItemCount`) is equipped (`Actor.EquipItem`, silent);
else the wrong round is unequipped (`Actor.UnequipItem`) so the engine's
own no-ammo check refuses the attack, and the shot or reload is refused.
The same check runs on `weaponDraw` and, a frame later, on `reloadStart`
(the engine's ammo-equipped event), so a wrong round never stays in hand
long enough for the attack button to start the fire clip.
The same runs on the `weaponDraw` trigger, so a gun drawn over the wrong
round swaps it before the first pull. The natives' addresses come from
their registration sites: each is the `lea r9, fn` (or `lea rax, fn;
mov [rbx+0x50], rax`) beside the `RegisterFunction("<name>", "Actor", ...)`
call the name literal cross-references to (`EquipItem` 0x989160,
`UnequipItem` 0x98c680, `GetItemCount` 0x9ce530, `AddPerk` 0x988870 on
1.6.659). The reload transitions in the graph carry `iGunShots > 0`, so a
full magazine ignores the key.

## <a id="zoom"></a>Iron sights on the zoom key

The zoom key (`[Guns] ZoomKey`, default right mouse) is polled by the key
thread; its edge posts a task (`zoom.cpp`) that ramps a blend 0..1 over
0.2 s, re-posting itself every frame. Each frame writes the REAL graph
variable `fGunZoom`, on which every aim pose is a parametric
hkbBlenderGenerator of the hip and iron stances (`pose_gen`; while
moving, the ready machine's `moving_gen`: the locomotion clip alone at
hip, the class' `aimis` pose over the locomotion's lower body at iron,
the overlay FNV plays), and lerps the camera FOV with it (SetFOV does no easing of its own; the ramp is the ease). A first ramp stepped `blend += goal > blend ? step : -step` and so, at the goal, alternated between 1.0 and 1.0 - step every frame: pose and FOV jittered between the last frames of the zoom while the once-a-second log only ever caught the 1.0 side. The step now moves towards the goal and stops on it. The blenders carry flags 17 (sync + parametric, what every vanilla parametric blend carries) and the DLL keeps the written value 0.001 inside [0, 1]: a first build used flag 16 alone and parked the parameter exactly on the iron child's anchor, and the held pose flickered between frames while the DLL log showed the blend steady at 1.00. Each attack is an
hkbManualSelectorGenerator on the INT `iGunZoom` (`_attack_gen`, FNV's
own `attack..is` clip); Skyrim's Havok has no latch on that selector, so a
change mid-clip restarts the clip and its shot trigger (a double
projectile when the key flipped during a shot): the DLL writes `iGunZoom`
only while no fire clip runs (`ZoomNoteFiring`, from the fire press to
`TES4GunFireEnd`/`TES4GunAttackEnd`/`TES4GunReloadEnd`). A first build
sent zoom events instead; the nested machines refused them mid-clip, the
variable is read every frame.

<a id="sight-alignment"></a>**Sight alignment is not built.** FNV aligns
the iron sights at runtime by moving the first-person model so the
weapon's `##SightingNode` sits on the camera axis. A build shifted the
first-person 3D root's local translation from the zoom task (SKSE task
time, before the engine's own update): the log showed the root
(`skeleton.nif`) has no parent, its local translation is the actor's
world position, rewritten by the engine every frame, and the sighting node
sits ~115 units above it (the root is at the feet, not at the camera). The
task's write and the engine's placement alternated frames: a visible
flicker between the two poses while the key was held. The task now only
logs the measurement once a second (`zoom: blend .. sight (x y z)`, the
node's offset in the root's frame). The alignment needs a hook after the
engine places the first-person model and a node under the camera, still
to be found. Most of the visible offset is not this step: FNV's iron-sight
clips already center the sight side to side, and the retarget moves it
([hands spread](asset_convert_falloutnv.md#first-person-hands-spread)).

`SetActorGraphInt` writes the variable to every graph of the actor's
BSAnimationGraphManager. The player has two, third and first person; a
build that wrote only the first (`iGunZoom written: 1` in the log) zoomed
the camera and the third-person body while the first-person arms stayed
at the hip.

The camera does what the console's `fov` command does: `PlayerCamera`
+0x13c/+0x140 (world, first person), the render camera state's SetFOV,
the recompute call and the two cached defaults, scaled by the WEAP's
Sight FOV over FNV's default 75 (`DNAM.SightFOV`, 65 when unset); the
saved values return on release. The bow-zoom perk entry route
(`BowZoomStartHandler`, perk entry point 0x14) was tried and dropped: it
zooms only through the engine's bow state, which a gun never enters.

Not done: spread and VATS.
