// FO3/FNV limb severing.
//
// 🛑 DORMANT: it has never worked in game, so FalloutRuntime compiles it and
// installs none of it (fallout/plugin.cpp).
//
// Skyrim's own gore path is keyed on its six body part types, so an FNV
// limb (13 types, partitions numbered 0-14 with 100+type / 200+type caps)
// cannot be dismembered by the engine. This module does it on the engine's
// own contract, every fact measured in SkyrimSE.exe (see
// docs/commentary/asset_convert_falloutnv.md#dismemberment):
//
//   * every melee and projectile hit is applied by one routine (id 38586,
//     Actor* + HitData*); a hit that takes health from above zero to zero
//     or below is fatal. The HitData's impact point sits at +0x00 (game
//     units, zero for melee), the hit direction at +0x0c, the aggressor
//     handle at +0x18
//   * the target's body part data comes from id 37181, its FormID at +0x14
//   * an actor's 3D root is virtual slot 0x70; NiAVObject::GetObjectByName
//     is slot 0x2a; a node's world translation is at +0xa0
//   * the engine's partition-visibility routine (id 37641) walks NiNode
//     children (+0x118, count +0x122), casts each geometry's skin instance
//     (+0x130) to BSDismemberSkinInstance through the RTTI cast (id 15619,
//     RTTI id 410521) and writes the visibility BYTE of each 4-byte
//     partition entry {u8 visible, u8, u16 bodyPart} at skin+0x90
//     (count +0x88, summary byte +0x98)
//   * the engine re-applies its own dismemberment when an actor's 3D loads
//     (id 37644, two callers); ours rides the same call
//   * the severed limb is a movable static the import minted from the part's
//     LimbReplacementModel, dropped with the PlaceAtMe / SetPosition /
//     ApplyHavokImpulse natives (ids 56203, 56234, 56147)
//
// The authored limb data (node names, severable flags, limb statics) is
// read from the sidecars the converter writes to Data\SKSE\Plugins\FalloutRuntime\;
// severed parts persist in the SKSE co-save.

#pragma once

#include "skse_abi.h"

namespace tesruntime {

// Resolves every address, loads the sidecars and patches the hit and 3D-load
// call sites. False (with the reason logged) leaves the game untouched.
bool InstallSevering();

// After DataLoaded: resolves every sidecar record and limb to its form.
void ResolveSeverForms();

// SKSE serialization callbacks for the severed-part table.
void SeverSave(SKSESerializationInterface* intfc);
void SeverLoad(SKSESerializationInterface* intfc);
void SeverRevert(SKSESerializationInterface* intfc);

// After a game load: re-applies severed parts to actors whose 3D is loaded.
void SeverReapplyAll();

}  // namespace tesruntime
