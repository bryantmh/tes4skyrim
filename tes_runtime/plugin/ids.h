// Address Library stable IDs for everything TESRuntime touches.
//
// Every ID was derived by locating the target in the GOG/AE 1.6.659 build
// (the only non-DRM-packed copy, so the only one that disassembles
// statically) and inverting its RVA through versionlib-1-6-659-0.bin; each
// was then checked to exist in versionlib-1-6-1170-0.bin, the Steam build
// the user plays. Nothing here is a raw RVA.
//
// The facts each ID rests on are recorded in
// docs/commentary/asset_convert_creature.md#runtime-animation-cache-composition
// and docs/commentary/asset_convert_falloutnv.md (#gun-graph, #dismemberment).

#pragma once

#include <cstdint>

namespace tesruntime::ids {

// The AnimationDataSingleFile.txt parser (0x4f7280 on 1.6.659, 0x536ec0 on
// 1.6.1170). Its ONE call to kResourceOpen is the hook site.
constexpr std::uint64_t kAnimDataParser = 32571;

// The AnimationSetDataSingleFile.txt parser (0x4fb3c0 on 1.6.659). Same
// shape: one call to kResourceOpen, one hook site.
constexpr std::uint64_t kAnimSetDataParser = 32624;

// The shared resource-open helper (0xc7e2d0 on 1.6.659):
//   int Open(const char* path, BSResource::Stream** out, uint8 flag, void* r9)
// returns 0 on success with *out holding a refcounted stream.
constexpr std::uint64_t kResourceOpen = 69839;

// Signature fallbacks, '|'-separated alternates tried in order. Each occurs
// EXACTLY ONCE in .text of the build it was taken from and never in the
// other: first the GOG 1.6.659 prologue, then Skyrim VR 1.4.15's
// (0x4ec580 / 0x4f0860 / 0xc89d60 there). Used when no versionlib applies,
// which is every pre-AE runtime.
constexpr const char* kSigAnimDataParser =
    "48 89 4C 24 08 55 53 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 68 F8 FF "
    "FF 48 81 EC 98 08 00 00 48 C7 45 58 FE FF FF FF|"
    "48 89 4C 24 08 55 53 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 58 F8 FF "
    "FF 48 81 EC A8 08 00 00 48 C7 45 68 FE FF FF FF";
constexpr const char* kSigAnimSetDataParser =
    "48 89 4C 24 08 55 53 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 88 F3 FF "
    "FF 48 81 EC 78 0D 00 00 48 C7 85 F8 00 00 00 FE|"
    "48 89 4C 24 08 55 53 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 58 F3 FF "
    "FF 48 81 EC A8 0D 00 00 48 C7 85 30 01 00 00 FE";
constexpr const char* kSigResourceOpen =
    "48 8B C4 57 41 56 41 57 48 81 EC 40 01 00 00 48 C7 44 24 60 FE FF FF FF "
    "48 89 58 08 48 89 68 10 48 89 70 20 4D 8B F9 45 0F B6 F0|"
    "48 8B C4 57 48 83 EC 70 48 C7 40 C8 FE FF FF FF 48 89 58 08 48 89 68 10 "
    "48 89 70 18 49 8B E9 41 0F B6 F8 48 8B F2 48 8B";

// How far into a parser the helper call may sit. Both calls are within the
// first 0x100 bytes on 1.6.659 (+0xce and +0x84); the bound only limits the
// scan, the match itself is by resolved target.
constexpr std::size_t kParserScanBytes = 0x400;

// How far into the AnimData parser its stream release may sit (+0x498 on
// 1.6.659 and 1.6.1170, +0x4e8 on VR); the body is under 0xe00 bytes on all.
constexpr std::size_t kParserBodyBytes = 0x1000;

// ---------------------------------------------------------------------------
// Shared engine services (engine.cpp)
// ---------------------------------------------------------------------------

// BSFixedString::BSFixedString(const char*) (0xc60ac0) / ~BSFixedString (0xc60c30).
constexpr std::uint64_t kFixedStringCtor = 69161;
constexpr std::uint64_t kFixedStringDtor = 69164;

// The Game.GetFormFromFile Papyrus native (0x9adb30):
//   TESForm* (VM*, uint32 stack, void* tag, int32 formID, const BSFixedString& file)
// resolves a plugin-local id through the running load order.
constexpr std::uint64_t kGetFormFromFile = 55465;

// TESForm* LookupFormByID(uint32) (0x1a0b70), the body of Game.GetForm.
constexpr std::uint64_t kLookupFormByID = 14617;

// TESObjectREFR* LookupReferenceByHandle(std::uint32_t* handle, NiPointer<TESObjectREFR>* out)
constexpr std::uint64_t kLookupByHandle = 17201;

// The ObjectReference Papyrus natives the limb drop uses (native callback
// pointers read off their registrations at 0x9d600c / 0x9d75bb / 0x9d33ef):
//   PlaceAtMe(VM*, stack, TESObjectREFR* self, TESForm*, int count, bool persist, bool disabled)
//   SetPosition(VM*, stack, TESObjectREFR* self, float x, float y, float z)
//   ApplyHavokImpulse(VM*, stack, TESObjectREFR* self, float x, y, z, float magnitude)
constexpr std::uint64_t kPlaceAtMe = 56203;
constexpr std::uint64_t kSetPosition = 56234;
constexpr std::uint64_t kApplyHavokImpulse = 56147;

// ---------------------------------------------------------------------------
// Gun routing (docs/commentary/asset_convert_falloutnv.md#gun-graph)
// ---------------------------------------------------------------------------

// int GetHandAnimType(TESForm*) (0x1927d0): the weapon anim type, 9 for a
// spell, 10 shield, 11 torch, anim type + 3 (12) for a crossbow. Every
// caller is patched so a gun WEAP answers 13, except the two below.
constexpr std::uint64_t kHandAnimType = 14220;

// The two callers that must keep seeing the vanilla type (12): the
// GetEquippedItemType script/condition function (0x2f5220, call at +0x5b)
// that the attack IDLE tree conditions on, and the Papyrus
// Actor.GetEquippedItemType native (0x989a60, call at +0x35).
// (docs/commentary/asset_convert_falloutnv.md#attack-event-idle-tree)
constexpr std::uint64_t kEquippedItemTypeCondition = 21677;
constexpr std::uint64_t kEquippedItemTypeNative = 54685;
constexpr std::size_t kEquippedItemTypeSpan = 0x100;

// bool BShkbAnimationGraph::SetVariableInt(graph, const BSFixedString&, int)
// (0xb2b770): the ONE routine that writes iRightHandType into a graph.
constexpr std::uint64_t kGraphSetVariableInt = 63609;
constexpr std::uint64_t kGraphSetVariableFloat = 63608;  // (graph, name, float) beside it

// The interned animation string table getter (0x110480): iLeftHandType at
// +0x388, iRightHandType at +0x390 (constructor 0x1dfc20, id 15908).
constexpr std::uint64_t kAnimStringTable = 11437;

// ---------------------------------------------------------------------------
// Gun fire (docs/commentary/tes_runtime_guns.md#the-shot)
// ---------------------------------------------------------------------------

// void TESObjectWEAP::Fire(weapon, TESObjectREFR* shooter, TESAmmo* ammo,
// AlchemyItem* poison, NiAVObject* nockedArrow) (0x2470b0): the launch the
// engine's ArrowReleaseHandler (0x75ee20, id 42859) makes after its
// weapon-type and attack-state checks. A null ammo reads the shooter's
// current ammo.
constexpr std::uint64_t kWeaponFire = 18102;

// InventoryEntryData* AIProcess::GetEquippedObject(process, bool left)
// (0x6b37f0): the entry whose first qword is the equipped form.
constexpr std::uint64_t kProcessEquipped = 39806;

// The BSTEventSink<BSAnimationGraphEvent> vtables of Character (0x1750180)
// and PlayerCharacter (0x17565e8), the subobject at +0x30 of the actor;
// slot 1 is Actor::ProcessEvent (0x645160, id 37997), shared by both. Every
// clip trigger and annotation the actor's graph raises passes through it.
constexpr std::uint64_t kCharacterAnimSinkVtable = 207890;
constexpr std::uint64_t kPlayerAnimSinkVtable = 208044;

// PlayerCharacter* singleton (0x2fbbb58).
constexpr std::uint64_t kPlayerSingleton = 403521;

// Papyrus natives, called the way the VM calls them (vm, stack, self, ...):
// Actor.EquipItem(form, preventRemoval, silent) (0x989160),
// ObjectReference.GetItemCount(form) -> int (0x9ce530),
// each read off its registration site's function pointer
// (docs/commentary/tes_runtime_guns.md#ammo-restriction).
constexpr std::uint64_t kEquipItemNative = 54661;
constexpr std::uint64_t kGetItemCountNative = 56173;

// The camera FOV path the console `fov` command takes (0x32ed80):
// PlayerCamera singleton (0x2f59608), the render camera state pointer
// (0x2fbbb20) handed to SetFOV(state, fov, force, camera, bool)
// (0x13e0110), the projection recompute (0x13b6080) and the two cached
// defaults it writes (0x1e94260 world, 0x1e94278 first person).
// (docs/commentary/tes_runtime_guns.md#own-the-click)
constexpr std::uint64_t kPlayerCamera = 400802;
constexpr std::uint64_t kRenderCameraState = 403513;
constexpr std::uint64_t kRenderSetFov = 106461;
constexpr std::uint64_t kFovRecompute = 105655;
constexpr std::uint64_t kCachedWorldFov = 388785;
constexpr std::uint64_t kCachedFirstFov = 388788;

// Actor::PerformAction (0x674eb0): resolves a BGSAction (ActorActionData:
// actor +8, BGSAction* +0x18 whose formEditorID sits at +0x20) through the
// idle tree, sends the event to the graph and applies the attack state
// (its callee 0x675460, id 39004). All five call sites are patched
// (docs/commentary/tes_runtime_guns.md#own-the-click).
constexpr std::uint64_t kPerformAction = 39002;

// NiAVObject* GetObjectByName(NiAVObject* root, const BSFixedString&, bool)
// (0xd8bc20), the node lookup Actor::ProcessEvent itself uses
// (docs/commentary/tes_runtime_guns.md#quiver).
constexpr std::uint64_t kObjectByName = 76207;

// NiAVObject* CrossbowFiringNode(AIProcess*, holder** 3D) (0x70eb00): the
// "NPC R MagicNode [RMag]" a crossbow-type weapon fires from; its 11
// callers (Fire, aim, combat) are patched to a gun's ProjectileNode
// (docs/commentary/tes_runtime_guns.md#firing-node).
constexpr std::uint64_t kCrossbowFiringNode = 39834;

// Gun parts, the way ObjectReference.PlayGamebryoAnimation (0x9cfdd0) plays
// an in-mesh sequence: the NiControllerManager vtable (0x185c968) that the
// weapon root's first controller must carry, the BSFixedString hash
// (0xc3d430) keying the manager's name map (+0x98 buckets, +0x7c capacity,
// +0x88 sentinel) and NiControllerSequence::Activate (0xd07020)
// (docs/commentary/asset_convert_falloutnv.md#gun-parts).
constexpr std::uint64_t kControllerManagerVtable = 237541;
constexpr std::uint64_t kFixedStringHash = 68221;
constexpr std::uint64_t kSequenceActivate = 72463;

// The HUD ammo counter (docs/commentary/tes_runtime_guns.md#hud):
// HUDMenu::ProcessMessage (0x8bed60) invokes "ShowArrowCount" (the string
// at 0x17a1c70) through GFxMovieView::Invoke (0xf1ee90) with three
// GFxValues, the text one set with GFxValue::SetString (0x8c8830).
constexpr std::uint64_t kHudProcessMessage = 51612;
constexpr std::uint64_t kShowArrowCountName = 215397;
constexpr std::uint64_t kGfxInvoke = 82256;
constexpr std::uint64_t kGfxSetString = 51740;

// ---------------------------------------------------------------------------
// Limb severing (docs/commentary/asset_convert_falloutnv.md#dismemberment)
// ---------------------------------------------------------------------------

// void Actor::ApplyHit(Actor*, HitData*) (0x65d8c0): every melee and
// projectile hit lands here (8 callers, all patched).
constexpr std::uint64_t kApplyHit = 38586;

// void ReapplyDismemberment(Actor*) (0x62be90): the engine re-applies its
// ExtraDismemberedLimbs when an actor's 3D loads; both callers patched.
constexpr std::uint64_t kReapplyDismember = 37644;

// BGSBodyPartData* GetBodyPartData(Actor*) (0x604e00): the race's GNAM, or
// the default, exactly as the hit code resolves it.
constexpr std::uint64_t kActorBodyPartData = 37181;

// NiObject* NiRTTI cast(const NiRTTI*, NiObject*) (0x1d1440) and the
// BSDismemberSkinInstance NiRTTI object (0x30a84e0), the pair the engine's
// partition-visibility routine (0x62b680, id 37641) uses.
constexpr std::uint64_t kRttiCast = 15619;
constexpr std::uint64_t kDismemberSkinRtti = 410521;

}  // namespace tesruntime::ids
