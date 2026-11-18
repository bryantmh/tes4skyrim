# TESRuntime: TES4 IsAnimPlaying

**Code:** `tes_runtime/tes/anim_playing.cpp`, `tes_runtime/common/gamebryo_sequence.cpp` (`AnyPlaying`),
`script_convert/command_rows.py` (`isanimplaying`).

## Is anim playing
<a id="is-anim-playing"></a>

TES4 scripts on gates, doors, switches and wells use `IsAnimPlaying == 0` (215 calls in Oblivion.esm, every one
an `== 0` test) to wait for their own NIF animation: RFSwitch03SCRIPT sets `busy` on activation and clears it
once the handle has turned. Papyrus has no call for it. The converter used to write
`GetAnimationVariableBool("bAnimPlaying")`, but converted animated objects play their mesh's own
NiControllerSequences and have no behavior graph, so the call always failed: it returned false and logged
"cannot fetch variable named bAnimPlaying" with a stack dump on every poll (90,453 errors in run 57c's Papyrus
log, from TES4_MQ05SecretDoor01SCRIPT, TES4_RFSwitch03SCRIPT, TES4_CGate01SCRIPT, TES4_SewerSloughGate01SCRIPT
and others).

Skyrim still has the TES4-era script function `IsAnimPlaying` (opcode 0x1128, console and script only, no
condition handler; 1.6.1170 0x357b30). It reads the reference's 3D root, that node's controller as a
NiControllerManager, and returns 1 when any sequence in the manager's list (+0x50, count +0x5a) has a non-zero
state (+0x68). No behavior graph is involved.

The converter now asks for the graph variable `TES4IsAnimPlaying`, and TESRuntime patches slot 18
(GetGraphVariableBool, 0x54c620) of TESObjectREFR's IAnimationGraphManagerHolder vtable (Address Library 190265;
the holder sits at reference + 0x38, where GetAnimationVariableBool 0xa2cdf0 calls it). For that one name the
hook answers from the manager as the engine function does, and returns success, so nothing is logged; every
other name goes to the engine. One difference from the engine walk: a CLAMP sequence whose weighted time
(+0x54) has reached its end key (+0x4c) counts as finished, so a switch's `busy` clears after its turn. Actors
are not patched: their holder slot 18 (0x6a3610) reads the graph through the AI process instead, and no
Oblivion.esm script was found asking an actor (all calls are on the script's own object or a named door or
secret wall). Without TESRuntime the variable reads false, the old behavior.

Measured on the headless instance (1.6.1170): the hook installs ("IsAnimPlaying installed"); mq05_baurus_meeting
and mq05_sewer_plan (sewer gates and wheel) pass with 0 "cannot fetch variable" lines; an Ayleid well's looping
glow reads 1; RFSwitch01 (REFR 000A51B1, WarehouseFortRuinsHALLrooms) activated by the harness ends with
`OPEN` 1, `busy` back to 0 and its timer run down.
