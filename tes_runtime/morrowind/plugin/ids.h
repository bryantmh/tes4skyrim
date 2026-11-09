// Address Library stable IDs for everything MorrowindRuntime touches.
//
// Every ID was derived from the RUNNING Steam build (1.6.1170), whose on-disk
// .text is DRM-encrypted -- `skyrim_disasm.py --live --save-image` gives a
// decrypted image with RVAs matching the running game. Each was then inverted
// through versionlib-1-6-1170-0.bin and checked to exist in all 12 shipped
// databases. Nothing here is a raw RVA.
//
// How each was located, with the disassembly:
// docs/commentary/morrowind_runtime.md#menu-registration

#pragma once

#include <cstddef>
#include <cstdint>

namespace tesruntime::mw::ids {

// The MenuManager singleton POINTER (0x20f6a00 on 1.6.1170), read, never
// called.
//
// 🛑 0xfa32f0 next door is the CONSTRUCTOR, not a getter: it takes placement
// memory in rcx. Calling it with no argument corrupts the manager and the
// game dies reading [r9] at 0xfa40cc. A registration site tests the pointer
// first and only constructs when it is null, which by our DataLoaded it never
// is.
// See: docs/commentary/morrowind_runtime.md#menu-registration
constexpr std::uint64_t kMenuManagerSingleton = 400327;

// MenuManager::numPauseGame: how many OPEN menus carry IMenu flag 0x1, so
// nonzero is the engine's own "the game is paused". 75 sites read it as
// `cmp dword ptr [rax+0x160], 0` and branch past their work.
// See: docs/commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused
constexpr std::size_t kOffMenuNumPauseGame = 0x160;

// MenuManager::IsMenuOpen(this, BSFixedString* name) (0xfa37b0 on 1.6.1170,
// SKSE's own address for it): looks the interned name up in the menu table at
// +0x128 and tests flag 0x40, which the engine sets while a menu is on the stack.
// SE 1.5.97 (0xebe150) and VR (0xf1a3b0) inline the lookup but test the same.
// See: docs/reference/address_library_formats.md#hand-proven
constexpr std::uint64_t kMenuManagerIsMenuOpen = 82074;

// That menu table, walked to reach a menu's own object: capacity at +0x134,
// entries at +0x150, each 0x20 bytes holding the interned name at +0, the
// IMenu at +8 and the chain link at +0x18, null in an empty slot. The same on
// all three builds' IsMenuOpen.
constexpr std::size_t kOffMenuTableCapacity = 0x134;
constexpr std::size_t kOffMenuTableEntries = 0x150;
constexpr std::size_t kMenuTableEntrySize = 0x20;
constexpr std::size_t kOffMenuEntryMenu = 0x8;
constexpr std::size_t kOffMenuEntryNext = 0x18;

// The perks menu's skills in label order: StatsMenu's array of actor values
// at +0x50, its count at +0x60. Its fill (0x962450 on 1.6.1170, 0x8c20c0 on
// 1.5.97) hands entry n's level, name and color to the movie's SkillText<n>.
// VR's perks menu has neither the fill nor those labels.
// See: docs/commentary/morrowind_runtime.md#capped-skills
constexpr std::size_t kOffPerksSkills = 0x50;
constexpr std::size_t kOffPerksSkillCount = 0x60;

// GFxValue's ObjectInterface::ReleaseManaged(iface, value, data) (0xfac750,
// skse64's ReleaseManaged_Internal), what the engine calls on every value the
// movie owns once it is done with it -- a string read back from a movie is one.
constexpr std::uint64_t kGfxReleaseManaged = 82270;

// Skyrim's perks menu as the manager names it. Its constructor (0x95ed58 on
// 1.6.1170) leaves IMenu's default depth, 3, and sets flags 0x8189.
// See: docs/commentary/morrowind_runtime.md#perks-button
constexpr const char* kPerksMenu = "StatsMenu";

// StatsMenu's MenuEventHandler vtable (0x1901438 on 1.6.1170) and the
// CanProcess its slot 1 holds (0x961030). MenuControls (0x947db0) offers every
// input event to EVERY registered handler whose CanProcess agrees -- none can
// consume one for the rest -- so a click on our button reaches the perks menu
// unless its own gate refuses it. An InputEvent carries its device at +0x8
// (1 mouse; the loop compares 2, the gamepad) and its type at +0xC (0 a
// button, dispatched to ProcessButton).
// See: docs/commentary/morrowind_runtime.md#perks-button
constexpr std::uint64_t kPerksHandlerVtable = 215975;
constexpr std::uint64_t kPerksCanProcess = 52518;
constexpr std::size_t kCanProcessSlot = 0x8;
constexpr std::size_t kOffInputDevice = 0x8;
constexpr std::size_t kOffInputType = 0xC;
constexpr std::uint32_t kDeviceMouse = 1;
constexpr std::uint32_t kInputButton = 0;

// MenuManager::Register(this, const char* name, IMenu* (*creator)())
// (0xfa5480). Found as the jmp target shared by 10 distinct menu-name call
// sites; identical to the RVA SKSE hardcodes.
constexpr std::uint64_t kMenuManagerRegister = 82086;

// GFxLoader::LoadMovie(this, IMenu* menu, GFxMovieView** viewOut,
//                      const char* name, int scaleMode, float bgAlpha)
// (0xfb0110). `name` carries NO extension: the callee formats it through
// "Interface/%s.swf". 61 call sites, one per vanilla menu.
constexpr std::uint64_t kGFxLoaderLoadMovie = 82325;

// The GFxLoader singleton POINTER, not a getter (0x35f11c8): menu ctors load
// it with a plain mov from .data.
constexpr std::uint64_t kGFxLoaderSingleton = 402775;

// bool MovieFileExists(const char* path) (0xfb49a0): LoadMovie's own check,
// through the engine's resource locator, before it tries "Interface/%s.swf"
// and then "Interface/Exported/%s.gfx".
constexpr std::uint64_t kMovieFileExists = 82411;

// Scaleform's state bag (the GFx loader proper), at +8 in that singleton.
// Vtable slot 2 is SetState(type, state), slot 3 GetStateAddRef(type); type 4
// is the log. Layout from skse64 ScaleformState.h, whose bEnableGFXLog logger
// is installed the same way.
constexpr std::size_t kOffLoaderStateBag = 0x8;
constexpr std::size_t kStateBagSetStateSlot = 2;
constexpr std::size_t kStateBagGetStateSlot = 3;
constexpr std::uint32_t kGfxStateLog = 4;

// The Scaleform allocator singleton POINTER (0x3292490). Menus are allocated
// through it, at vtable slot 0x50: Alloc(this, size, 0).
//
// 🛑 The engine FREES a menu through this same allocator, so a menu from
// HeapAlloc is a crash at close, not at open.
constexpr std::uint64_t kScaleformAllocator = 412058;

// The Alloc slot in that allocator's vtable.
constexpr std::size_t kScaleformAllocSlot = 0x50;

// GFxMovieView::ScaleModeType for LoadMovie. Vanilla menus pass kNoBorder
// (3), which fills the viewport and CROPS whatever the aspect ratio does not
// fit: on a 3440x1440 display the 1280x720 stage scaled by 2.69 to the width
// and lost 247 px top and bottom -- measured as a 1580 px wide window running
// off the bottom of the screen. kShowAll fits the whole stage, aspect kept.
constexpr int kScaleModeShowAll = 1;

// TESFullName inside TESNPC, read off TESNPC's destructor (0x385037 on
// 1.6.659), which restores one base vtable per offset: 0x00, 0x30, 0x88,
// 0xa0, 0xb0, 0xd8, 0xe8, 0xf0, 0x100, 0x110, 0x128, 0x138, 0x150, 0x160,
// 0x188 -- the exact sequence TESActorBase declares, with TESFullName sixth
// at 0xd8. Its BSFixedString (a `const char*`) follows the vtable at 0xe0.
// The player's own TESNPC is form 0x7 of Skyrim.esm, and the name entered
// at character creation is written to it.
constexpr std::size_t kOffNpcFullName = 0xe0;
constexpr std::uint32_t kPlayerBaseFormId = 0x7;
constexpr const char* kSkyrimMaster = "Skyrim.esm";

// UIMessage::type values, read off the base IMenu::ProcessMessage (0xf21bd0
// on 1.6.659): it forwards ONLY type 6, whose data is a BSUIScaleformData
// holding the GFxEvent* at +0x10, to the movie's HandleEvent. Type 7 is a
// user event whose BSUIMessageData carries the event name ("Cancel") at
// +0x18.
constexpr std::uint32_t kMessageScaleformEvent = 6;
constexpr std::uint32_t kMessageUserEvent = 7;
constexpr std::size_t kMessageTypeOffset = 0x8;
constexpr std::size_t kMessageDataOffset = 0x10;
constexpr std::size_t kScaleformEventOffset = 0x10;
constexpr std::size_t kUserEventNameOffset = 0x18;

// What ProcessMessage returns: 0 consumed the message, 2 passes it on. The
// base returns 0 after HandleEvent and 2 for everything else.
constexpr std::uint32_t kResultHandled = 0;
constexpr std::uint32_t kResultPassOn = 2;

// GFxEvent::type, at +0. A GFxMouseEvent continues with x and y as floats at
// +4 and +8, then the button at +0x10. Types 1, 2, 3, 5 and 6 (move, down,
// up, key down, key up) were seen in game.
constexpr std::uint32_t kEventMouseMove = 1;
constexpr std::uint32_t kEventMouseDown = 2;
constexpr std::uint32_t kEventMouseUp = 3;
constexpr std::size_t kMouseEventXOffset = 0x4;
constexpr std::size_t kMouseEventButtonOffset = 0x10;
// A GFxKeyEvent (type 5 down) holds the key code (Windows' virtual key) at
// +4, the ASCII character at +8 and the modifier bits at +0x10 (CommonLibSSE
// GFxKeyEvent, sizeof 0x14).
constexpr std::uint32_t kEventKeyDown = 5;
constexpr std::size_t kKeyEventCodeOffset = 0x4;
constexpr std::size_t kKeyEventAsciiOffset = 0x8;
constexpr std::size_t kKeyEventModsOffset = 0x10;
// A GFxCharEvent (type 13, Scaleform's numbering, which types 1-6 above
// follow) holds the typed character, case and all, as a UTF-32 code at +4.
// Skyrim sends it only while text input is allowed.
constexpr std::uint32_t kEventChar = 13;
constexpr std::size_t kCharEventCodeOffset = 0x4;

// The ControlMap (SKSE's InputManager) singleton POINTER (0x30fda10 on
// 1.6.1170), and ControlMap::AllowTextInput(this, bool) (0xcd5910): a counter
// byte at +0x128 that it raises or lowers; while it is above 0 the game sends
// menus character events. Read off the function on 1.6.1170, where a menu
// closing calls it with false.
constexpr std::uint64_t kControlMapSingleton = 400863;
constexpr std::uint64_t kControlMapAllowTextInput = 68552;
// The ControlMap's gameplay context (SKSE GameInput.h, whose +0x128 text-input
// byte matches the one above): an InputContext* at +0x60, holding the keyboard
// then the mouse mappings as BSTArrays of 0x18 bytes (data at +0, count at
// +0x10). A mapping is the user event's name (const char*) and its key, a
// DirectInput scan code or mouse button, at +8; 0xFF is unbound.
constexpr std::size_t kOffControlMapGameplay = 0x60;
constexpr std::size_t kControlArraySize = 0x18;
constexpr std::size_t kOffControlArrayCount = 0x10;
constexpr std::size_t kControlMappingSize = 0x18;
constexpr std::size_t kOffControlMappingKey = 0x8;
constexpr std::uint32_t kControlUnbound = 0xFF;
// TESRace's TESFullName (at +0x20) holds its name's BSFixedString at +8.
constexpr std::size_t kOffRaceFullName = 0x28;
// TESQuest's current stage, a u16: Quest.GetCurrentStageID tail-calls a
// getter that is only `movzx eax, word [rcx+0x228]` on 1.6.1170, 1.5.97 and
// VR alike.
constexpr std::size_t kOffQuestCurrentStage = 0x228;

// TESNPC's primary vtable (0x17e4d50 on 1.6.1170), whose slot 0x1b8 is
// Activate(this, ref, activator, ...). `TESObjectREFR::ActivateRef` dispatches
// through it after every guard, so this fires for EVERY NPC activation --
// crucially, whether or not the actor has any Skyrim dialogue.
//
// 🛑 Hooking the DIALOGUE MENU does not work: a converted Morrowind NPC has no
// Skyrim dialogue, so that menu never opens and a sink on it never fires.
//
// 🛑 Nor can `ActivateRef` itself be detoured: its prologue opens with
// `mov rax, rsp` (48 8B C4), which AnalyzePrologue refuses because a relocated
// copy captures the TRAMPOLINE's stack pointer. That exact instruction crashed
// the game on 2026-08-14. A vtable swap steals no bytes, so the hazard does
// not arise.
// See: docs/commentary/morrowind_runtime.md#activation
constexpr std::uint64_t kNpcVtable = 195816;

// The hello evaluator (0x6f16d0 on 1.6.1170, run every AI update): when an
// actor whose package allows "Hellos to player" notices the player within
// 150, it calls the hello approach (0x66b410; (Actor*, int)) at +0x2bd, which
// puts a created Follow package aimed at the player on it. That package has
// no end of its own: only the hello line being spoken ends it.
constexpr std::uint64_t kHelloEvaluator = 39632;
constexpr std::uint64_t kHelloApproach = 37367;
constexpr std::size_t kHelloEvaluatorScan = 0x400;

// The Activate slot's byte offset in that vtable, read off the dispatch
// `mov rcx,[ref+0x40] / mov rax,[rcx] / call [rax+0x1b8]`.
constexpr std::size_t kActivateSlot = 0x1b8;

// TESNPC::Activate (0x3b9500 on 1.6.1170), the value that slot holds. Checked
// against the running build's image so a wrong swap is caught before install.
constexpr std::uint64_t kNpcActivate = 24715;

// 🛑 Activate is OVERRIDDEN per form type, so the NPC swap above reaches only
// actors. Read at vtable byte 0x1b8 out of the 1.6.659 image: nine DISTINCT
// functions, of which 0x233b60 is the inherited TESBoundObject::Activate that
// the plain item types share. An object script sits on a BOOK, a WEAP or an
// ACTI as often as on an actor, so each is swapped separately -- and so is each
// plain item type, since each has its OWN vtable (a ring is an ARMO here).
// See: docs/plans/morrowind_object_scripts.md#activate-is-per-type
struct ActivateTarget {
    const char* name;
    std::uint64_t vtable;
    std::uint64_t activate;
};

//: Every form type that can carry a TES3 script, with the Activate it holds.
constexpr ActivateTarget kActivateTargets[] = {
    {"TESNPC", 195816, 24715},          {"TESObjectBOOK", 189577, 17840},
    {"TESObjectWEAP", 189786, 18101},   {"TESObjectACTI", 189485, 17702},
    {"TESObjectCONT", 189633, 17887},   {"TESObjectDOOR", 189666, 17922},
    {"TESObjectLIGH", 189414, 17615},   {"TESFlora", 189287, 17369},
    {"TESObjectMISC", 189689, 17670},   {"TESObjectARMO", 189542, 17670},
    {"AlchemyItem", 186204, 17670},     {"TESAmmo", 189195, 17670},
    {"IngredientItem", 186407, 17670},  {"TESKey", 189342, 17670},
    {"TESSoulGem", 189850, 17670},      {"ScrollItem", 186591, 17670},
};

// ContainerMenu's open(ref, mode) (0x8fbff0 on 1.6.1170), which TESNPC::Activate
// itself calls from inside Activate: mode 0 on a corpse, 2 to pickpocket, and
// Actor.OpenInventory passes 3 for a teammate. Mode 0 is TES3's ActionOpen --
// OpenMW opens a knocked-down actor with the very action it loots a corpse with.
// See: docs/commentary/morrowind_runtime.md#a-corpse-is-looted-not-talked-to
constexpr std::uint64_t kOpenContainerMenu = 51140;
constexpr std::int32_t kContainerLoot = 0;

// Actor::actorState1, the flag word at +0xC8 (IActorState at +0xC0, flags at
// +8). Actor.IsSneaking tests bit 9 of it and Actor.IsBleedingOut masks
// 0x1E00000 for life states 7 and 8, which fixes the layout: the life state is
// bits 21-24 and the knock state the three bits above it, 0 while standing.
// Life state 3 is UNCONSCIOUS, which TESNPC::Activate refuses an NPC activator.
constexpr std::size_t kOffActorState1 = 0xC8;
constexpr std::uint32_t kLifeStateMask = 0x01E00000;
constexpr std::uint32_t kLifeUnconscious = 0x00600000;
constexpr std::uint32_t kKnockStateMask = 0x0E000000;

// The Game.GetFormFromFile Papyrus native (0x9adb30):
//   TESForm* (VM*, uint32 stack, void* tag, int32 formID, const BSFixedString&)
// resolves a plugin-LOCAL id through the RUNNING load order.
//
// 🛑 This is the only correct way to learn a plugin's index. The FormIDs the
// sidecar carries hold the index the plugin had AT CONVERSION TIME; in the
// player's game it is whatever their load order says. Measured: TR_Mainland
// converts at 0x03 and loaded at 0x22, so a baked index matched nothing.
// See: docs/commentary/morrowind_runtime.md#load-order
constexpr std::uint64_t kGetFormFromFile = 55465;

// Papyrus natives, each the callback its registration loads beside the name
// string (`lea rdx,["AddItem"]` ... `lea rax,[callback]`), on 1.6.659:
// 0x9cd4b0, 0x9d0b30, 0x9ce530, 0x9adf00. All are called as the VM calls
// them: (VM*, stack id, self, arguments...).
//   void  ObjectReference.AddItem(Form, int count, bool silent)
//   void  ObjectReference.RemoveItem(Form, int count, bool silent, ObjectReference moveTo)
//   int   ObjectReference.GetItemCount(Form)
//   Actor Game.GetPlayer()

// 🛑 The stage path the ENGINE'S OWN CONSOLE COMMAND takes, NOT the Papyrus
// native. `setstage` (0x30df30) calls these three in order: EnsureQuestStarted
// with startNow = TRUE runs TESQuest::Start on the spot, then the stage is
// set directly. Quest.SetCurrentStageID (id 56684) passes startNow = FALSE,
// which only QUEUES the quest on BGSStoryTeller for promotion on a later
// frame -- and the dialogue menu pauses the game, so the quest is still
// stopped when its objective is displayed and the display is a no-op.
//   bool   TESQuest::EnsureQuestStarted(quest, bool* justStarted, bool startNow)  0x38a020
//   Stage* TESQuest::GetStage(quest, u16 index)                                  0x38ae70
//   bool   TESQuest::SetStage(quest, u16 index)                                  0x38a130
// See: docs/commentary/morrowind_runtime.md#objectives-must-be-displayed
constexpr std::uint64_t kQuestEnsureStarted = 25003;
constexpr std::uint64_t kQuestGetStage = 25028;
constexpr std::uint64_t kQuestSetStage = 25004;

// 🛑 The objective pair the ENGINE'S OWN CONSOLE COMMAND uses, NOT the Papyrus
// natives. `setobjectivedisplayed` is handled at 0x31b5b0, which calls these
// two directly; a hook on Quest.SetObjectiveDisplayed (id 56682) recorded ZERO
// hits while that command changed the state, and the native does nothing when
// called from this plugin.
//   BGSQuestObjective* TESQuest::GetObjective(quest, u16 index)   0x389300
//   void BGSQuestObjective::SetState(objective, u32 state)        0x354870
// State: 0 dormant, 1 displayed, 2/3 completed.
// See: docs/commentary/morrowind_runtime.md#objectives-must-be-displayed
constexpr std::uint64_t kQuestGetObjective = 24981;
constexpr std::uint64_t kQuestObjectiveSetState = 23933;

// Quest.IsRunning (0x9ea9c0 on 1.6.659): bit 0 of TESQuest+0xdc set, bit 7
// clear, and NO pending start at +0x248. A quest started this frame still
// carries that pending start (`sqv` says "Waiting For Promotion") until the
// StoryTeller finishes it on a later unpaused frame, and only then does its
// instance id become final. An objective displayed before that is filed under
// the OLD instance and the journal lists the quest as finished.
// See: docs/commentary/morrowind_runtime.md#objectives-must-be-displayed
constexpr std::uint64_t kQuestIsRunning = 56727;
// Actor.StartCombat(Actor target) (0x98c1b0) and Actor.StopCombat()
// (0x98c5a0), each found at its registration: `lea r9,[callback]` sits three
// instructions above the `lea rdx,["StartCombat"]` that names it.
//
// 🛑 Both take the ACTOR in r8 and the target in r9, so a bare `StartCombat`
// is the speaker attacking, not the player. Measured over Tamriel Rebuilt's
// result scripts: 720 of the 1,150 call sites name `player` as the argument.
constexpr std::uint64_t kActorStartCombat = 54768;
constexpr std::uint64_t kActorStopCombat = 54770;
// Actor.GetRelationshipRank(Actor other) (0x9e8f60 on 1.6.1170) and
// .SetRelationshipRank(Actor other, int rank) (0x9ea730), found at their
// registrations like StartCombat. Both take the actor in r8, the other in r9.
constexpr std::uint64_t kActorGetRelationshipRank = 54710;
constexpr std::uint64_t kActorSetRelationshipRank = 54759;

// ObjectReference.Enable(bool fadeIn) (0x9cdfa0), .Disable(bool fadeOut)
// (0x9cddc0) and .IsDisabled() (0x9e5b90). Enable/Disable register with the
// callback stored AFTER the registration call (`lea rax,[cb]` into
// `[rbx+0x50]`), not in r9 the way the argument-less natives do.
constexpr std::uint64_t kRefEnable = 56158;
constexpr std::uint64_t kRefDisable = 56155;
constexpr std::uint64_t kRefIsDisabled = 56639;

// The position, angle and TranslateTo natives are shared: common/engine_ids.h,
// used through common/glide.h.

// The world natives the result-script commands reach through, each the r9
// argument of its registration helper beside the name string on 1.6.659:
//   void  ObjectReference.Activate(ObjectReference actionRef, bool defaultOnly) 0x9ccfe0
//   void  ObjectReference.Lock(bool lock, bool asOffLimits)                  0x9cebd0
//   bool  ObjectReference.IsLocked()                                          0x9ceb30
//   void  ObjectReference.SetLockLevel(int level)                             0x9d1740
//   void  ObjectReference.Delete()                                            0x9cdc10
//   float ObjectReference.GetDistance(ObjectReference other)                  0x9ce3d0
//   Cell  ObjectReference.GetParentCell()                                     0x9e5b20
//   bool  Cell.IsInterior()                                                   0x9c8830
//   float Actor.GetActorValue(BSFixedString* name)                            0x989600
//   void  Actor.SetActorValue(BSFixedString* name, float value)               0x98b010
//   void  Actor.RestoreActorValue(BSFixedString* name, float amount)          0x98ae80
//   void  Actor.DamageActorValue(BSFixedString* name, float amount)           0x989050
//   void  Actor.EquipItem(Form item, bool preventRemoval, bool silent)        0x989160
// The Sound script's four natives, all registered against the class string
// 'Sound' in one function at 0x9eaeb0 on 1.6.659. Each id was inverted from
// the r9 pointer beside its name string and exists in all 12 shipped
// versionlibs:
//   int  Sound.Play(ObjectReference source)              0x9eaad0  member
//   bool Sound.PlayAndWait(ObjectReference source)       0x9eabf0  member
//   void Sound.StopInstance(int instance)                0x9ead70  global
//   void Sound.SetInstanceVolume(int instance, float v)  0x9eadc0  global
//
// Play RETURNS the playback instance id, which is the only handle StopSound
// and GetSoundPlaying have; without keeping it a sound can be started and
// never stopped.
// See: docs/commentary/morrowind_runtime.md#sound-opcodes
constexpr std::uint64_t kSoundPlay = 56740;
constexpr std::uint64_t kSoundPlayAndWait = 56741;
constexpr std::uint64_t kSoundStopInstance = 56742;
constexpr std::uint64_t kSoundSetInstanceVolume = 56743;

// ObjectReference.Say(Topic topic, Actor speakAs, bool inPlayersHead), member.
// Found at its registration the same way as the Sound natives: the callback is
// the `lea rax,[rip-N]` stored into [r12+0x50] beside the name string
// `Say` (rva 0x167fe08 on 1.6.659) -> 0x9d0de0 -> id 56220, present in every
// shipped versionlib. Calibrated against GetItemCount, whose same-shaped site
// gives 0x9ce530 -> 56173, matching kRefGetItemCount.
//
// A TES3 `Say` names a FILE, so the topic comes from `say_formid.txt`.
// See: docs/commentary/morrowind_runtime.md#scripted-say
constexpr std::uint64_t kRefSay = 56220;

constexpr std::uint64_t kRefActivate = 56139;

// What activating a Skyrim bed runs (1.6.1170 id 17420 +0x16a): the player's
// can-sleep-here check (0x731350; with a bed it also refuses one the player
// does not own, with the engine's own message), then the Sleep/Wait menu
// toggle (0x95e0d0) with `sleeping` set; the wait key passes false.
// See: docs/commentary/morrowind_runtime.md#show-rest-menu
constexpr std::uint64_t kPlayerCanSleepHere = 40443;
constexpr std::uint64_t kToggleSleepWaitMenu = 52490;
constexpr std::uint64_t kRefLock = 56198;
constexpr std::uint64_t kRefIsLocked = 56196;
constexpr std::uint64_t kRefSetLockLevel = 56229;
constexpr std::uint64_t kRefDelete = 56154;
constexpr std::uint64_t kRefGetDistance = 56170;
constexpr std::uint64_t kRefGetParentCell = 56632;
constexpr std::uint64_t kCellIsInterior = 56056;
// bool Cell.IsAttached() (0xa274e0 on 1.6.1170, 0x9c8820 on 1.6.659): the `lea`
// beside the name string `IsAttached`, one registration above IsInterior's.
// Its whole body is `cmp byte ptr [cell+0x44], 7`.
constexpr std::uint64_t kCellIsAttached = 56055;

// Debug.SendAnimationEvent(ObjectReference, string) (0xa073f0 on 1.6.1170),
// the `lea` stored beside the name string `SendAnimationEvent`. Its whole
// body builds a BSFixedString and calls slot 1 of the
// IAnimationGraphManagerHolder at ref+0x38 -- NotifyAnimationGraph -- and
// throws the bool away. The NPC animation driver needs that bool, so it makes
// the same call itself; the id only proves the build has the native.
// See: docs/commentary/morrowind_runtime.md#npc-animation-files
constexpr std::uint64_t kDebugSendAnimationEvent = 55380;
constexpr std::size_t kGraphHolderOffset = 0x38;
constexpr std::size_t kNotifyGraphSlot = 1;
// Actor.DrawWeapon (0x9eb940 on 1.6.1170), the `lea r9` beside "DrawWeapon":
// it calls the actor's vtable slot +0x530 with true.
constexpr std::uint64_t kActorDrawWeapon = 54779;
// Actor.GetEquippedWeapon(bool abLeftHand) (0x9f52d0 on 1.6.1170), stored at
// +0x50 of the function object beside "GetEquippedWeapon"; and
// Actor.UnequipItem(Form, bool abPreventEquip, bool abSilent) (0x9eb330), the
// `lea r9` beside "UnequipItem".
constexpr std::uint64_t kActorGetEquippedWeapon = 54924;
constexpr std::uint64_t kActorUnequipItem = 54774;
// Actor.IsInCombat (0x9f54a0), found at its registration beside "IsInCombat".
constexpr std::uint64_t kActorIsInCombat = 54945;
constexpr std::uint64_t kActorGetValue = 54675;
constexpr std::uint64_t kActorSetValue = 54743;
// Actor.ModActorValue (0x9e90f0 on 1.6.1170), found at its registration beside
// "ModActorValue". It moves the permanent modifier, which takes where a base
// write does not: `setav damageresist 0` left 65, `modav damageresist 10` gave 75.
constexpr std::uint64_t kActorModValue = 54721;
constexpr std::uint64_t kActorRestoreValue = 54737;
constexpr std::uint64_t kActorDamageValue = 54660;
constexpr std::uint64_t kActorEquipItem = 54661;

// Actor::DrinkPotion(AlchemyItem*, ExtraDataList*) -> bool (0x6c2dd0 on
// 1.6.1170): casts the potion on the actor through its instant caster, then
// removes one. Actor.EquipItem leaves a potion in the player's pack, and TES3's
// `Equip` of a potion drinks it (OpenMW ActionEquip applies it).
constexpr std::uint64_t kActorDrinkPotion = 38746;
constexpr std::uint8_t kFormTypePotion = 0x2E;

// ActorEquipManager::EquipObject (0x97a5e0) and UnequipObject (0x97a9e0) on
// 1.6.1170: the chokepoint EVERY equip passes, menu and script alike, which
// `Actor.EquipItem` itself reaches.
//
// 🛑 Hooked rather than POLLED because `PCSkipEquip` lets a script REFUSE an
// equip, and 143 scripts in the corpus set it. A poll sees an equip that has
// already happened and cannot veto one.
//
// 🛑 Both prologues were checked RELOCATABLE before choosing a detour: pushes
// then an rsp-relative `sub`/`lea`, no `mov rax,rsp`, no rip-relative operand
// and no relative jump in the stolen range. 52934 is NOT UnequipObject -- it
// opens `cmp [rcx+0x30],1 / jnz rel8`, which a detour would have cut.
// See: docs/commentary/morrowind_runtime.md#engine-written-locals
constexpr std::uint64_t kEquipObject = 52933;
constexpr std::uint64_t kUnequipObject = 52937;

// The message box Debug.MessageBox is a one-button WRAPPER over
// (0xa072f0 tail-calls it): it builds the MessageBoxData, so it takes the
// BUTTON NAMES and reports the click. Identical prologue on 1.6.1170
// (0x94b280) and 1.6.659 (0x8ec2f0).
//
// ShowMessageBox(const char* text, Callback* cb, bool, u32 kind,
//                ... , const char** buttons): the wrapper passes a
// one-entry array holding "OK" (0x1ad18f0), r9d=4 and 0xa at [rsp+0x20],
// which is what a buttoned call varies.
// See: docs/commentary/morrowind_runtime.md#messagebox-buttons
constexpr std::uint64_t kShowMessageBox = 52269;

// BookMenu::OpenBookMenu(const BSString* text, ExtraDataList*, TESObjectREFR*,
// TESObjectBOOK*, const NiPoint3*, const NiMatrix3*, float, bool) (0x8f1400 on
// 1.6.1170, 0x896fd0 on 1.6.659; CommonLibSSE's AE id): it reads only the
// text's data pointer and copies it into the menu's static string (0x31ac978).
// See: docs/commentary/morrowind_runtime.md#book-text-defines
constexpr std::uint64_t kOpenBookMenu = 51053;
// TES3's `Equip` of a book READS it (OpenMW's useItem). Skyrim's EquipItem
// does not, so the runtime opens it as the inventory does (0x92f740 on
// 1.6.1170): TESDescription::Get(out, parent, 'DESC') (14552, 0x1dbcc0) on
// the book's TESDescription at +0xa8, then OpenBookMenu with no ref.
constexpr std::uint64_t kDescriptionGet = 14552;
constexpr std::size_t kOffBookDescription = 0xa8;
constexpr std::uint32_t kDescField = 0x43534544;
constexpr std::uint8_t kFormTypeBook = 0x1B;

// ObjectReference.PlaceAtMe(Form base, int count, bool persist, bool disabled)
// (0x9cf630), found at its registration like the rest: `lea r8,
// ['ObjectReference'] / lea rdx,['PlaceAtMe']`, callback into [r12+0x50].
//
// 🛑 It RETURNS the created reference, which is the only way a script instance
// can attach to something the world never placed -- `PlaceAtPC` is how the
// TR_m3 vermai enters the game at all.
// See: docs/plans/morrowind_object_scripts.md#placeatpc
constexpr std::uint64_t kRefPlaceAtMe = 56203;

// What the movement and AI commands reach through. Each was found at its
// registration by `tools/script/papyrus_native_locate.py` against the GOG
// 1.6.659 exe and inverted through the Address Library; all seven exist in
// every one of the 12 shipped versionlibs.
//   void  ObjectReference.MoveTo(ref target, float x, y, z, bool match) 0x9cec80
//   void  ObjectReference.SetScale(float)                               0x9d23a0
//
// 🛑 `MoveTo` aims at a REFERENCE, and a non-persistent one does not exist
// while its cell is unloaded -- 4,502 of Morrowind.esm's 5,635 cells hold no
// persistent reference at all. A move into a named cell goes through
// `kRefMoveToCell` instead; `MoveTo` is kept for travel MARKERS only.
// 🛑 `Actor.PathToReference` is the walking form of AiTravel and is LATENT --
// it suspends its caller until the path ends. A hook cannot wait, so travel
// places the actor instead.
// See: docs/commentary/morrowind_runtime.md#ai-packages
constexpr std::uint64_t kRefMoveTo = 56199;

// TESObjectREFR::MoveTo_Impl(this, const ObjectRefHandle& target,
//     TESObjectCELL* cell, TESWorldSpace* world, const NiPoint3& position,
//     const NiPoint3& rotation) (0xa447f0), rotation in RADIANS. It takes the
// destination CELL itself, or a worldspace and lets the position pick the
// cell, so nothing inside the destination has to exist yet. Read from its
// own body: `[r8 + 0x40] & 1` is the cell's interior flag, and the two stack
// arguments are dereferenced as vectors.
constexpr std::uint64_t kRefMoveToCell = 56626;

// TESForm::formType, and the two types a move's destination can be.
constexpr std::size_t kOffFormType = 0x1A;
constexpr std::uint8_t kFormTypeCell = 0x3C;
constexpr std::uint8_t kFormTypeWorld = 0x47;
constexpr std::uint64_t kRefSetScale = 56240;

// WorldSpace ObjectReference.GetWorldSpace() (0xa44ba0 on 1.6.1170): the code
// `lea` beside the "GetWorldSpace" registration under ObjectReference, found
// by papyrus_native_locate.py. Where Mark stands when it is outdoors.
constexpr std::uint64_t kRefGetWorldSpace = 56636;

// SkyrimVM's BSTEventSink<TESMagicEffectApplyEvent> vtable: 0x19127c8 on
// 1.6.1170, 0x17bc0d0 on 1.6.659, the subobject at +0xA8 by its RTTI complete
// object locator. Slot 1 (byte 0x8) is its ProcessEvent (0x9c5910), which
// loads the 'OnMagicEffectApply' string: every effect applied to anything
// passes through it, script-less Script effects included.
// See: docs/commentary/morrowind_runtime.md#teleport-effects
constexpr std::uint64_t kVmMagicEffectApplySink = 217097;
constexpr std::uint64_t kVmMagicEffectApplyProcess = 53978;
constexpr std::size_t kProcessEventSlot = 0x8;

// SkyrimVM's BSTEventSink<TESSleepStartEvent> and <TESSleepStopEvent>
// vtables (0x1912948 and 0x1912960 on 1.6.1170), whose slot-1 ProcessEvents
// (0x9c7910, 0x9c7b20) load 'OnSleepStart' and 'OnSleepStop'. The Sleep/Wait
// menu's start (0x95e000) calls the player's sleep start (0x72e7f0), which
// sends the start event, only when the menu was opened for SLEEPING; a wait
// goes to 0x72e940 and its own event instead.
// See: docs/commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused
constexpr std::uint64_t kVmSleepStartSink = 217129;
constexpr std::uint64_t kVmSleepStartProcess = 53994;
constexpr std::uint64_t kVmSleepStopSink = 217131;
constexpr std::uint64_t kVmSleepStopProcess = 53995;

// SkyrimVM's BSTEventSink<TESHitEvent> vtable (0x1912768 on 1.6.1170, 0x17bc070
// on 1.6.659), whose slot-1 ProcessEvent (0x9c54b0) loads 'OnHit': every hit
// on anything passes through it. TESHitEvent (skse64 GameEvents.h) holds the
// target and attacker as raw pointers and the weapon or spell's runtime FormID.
// See: docs/commentary/morrowind_runtime.md#hits
constexpr std::uint64_t kVmHitSink = 217089;
constexpr std::uint64_t kVmHitProcess = 53974;
constexpr std::size_t kOffHitTarget = 0x0;
constexpr std::size_t kOffHitAttacker = 0x8;
constexpr std::size_t kOffHitSource = 0x10;

// SkyrimVM's BSTEventSink<TESContainerChangedEvent> vtable (0x19126a8 on
// 1.6.1170, 0x17bbfb0 on 1.6.659), whose slot-1 ProcessEvent (0x9c4170) loads
// 'OnItemAdded' and 'OnItemRemoved': every inventory change passes through it.
// TESContainerChangedEvent (skse64 GameEvents.h) holds the old and the new
// container's FormIDs at +0x0 and +0x4, and the item's at +0x8.
constexpr std::uint64_t kVmContainerSink = 217073;
constexpr std::uint64_t kVmContainerProcess = 53966;
constexpr std::size_t kOffContainerFrom = 0x0;
constexpr std::size_t kOffContainerTo = 0x4;
constexpr std::size_t kOffContainerItem = 0x8;
// The player's reference, Skyrim.esm 0x14, the same in every load order.
constexpr std::uint32_t kPlayerRefFormId = 0x14;

// SkyrimVM's BSTEventSink<TESDeathEvent> vtable (0x19126c0 on 1.6.1170), whose
// slot-1 ProcessEvent (0x9c45f0) loads 'OnDying' when the state byte at +0x10
// is 0 and 'OnDeath' otherwise: every actor's death passes through it. The
// dying actor is the raw pointer at +0x0 (skse64 GameEvents.h TESDeathEvent).
constexpr std::uint64_t kVmDeathSink = 217075;
constexpr std::uint64_t kVmDeathProcess = 53967;
constexpr std::size_t kOffDeathActor = 0x0;
// The state byte: 0 as the actor BEGINS dying, the kill itself. A corpse that
// loads dead raises the event too, which a fresh death must not be mistaken for.
constexpr std::size_t kOffDeathState = 0x10;
constexpr std::uint8_t kDeathStateDying = 0;

// TESMagicEffectApplyEvent: the target and caster (NiPointers' raw pointers)
// and the MGEF's runtime FormID. The VM's argument functor (vtable 0x1912ba8,
// slot 1) reads the caster at +0x8 and looks the +0x10 id up expecting form
// type 0x12.
constexpr std::size_t kOffApplyTarget = 0x0;
constexpr std::size_t kOffApplyCaster = 0x8;
constexpr std::size_t kOffApplyEffect = 0x10;

// The alias plumbing the AI packages run on, each found at its registration
// and present in all 12 shipped versionlibs:
//   Alias  Quest.GetAlias(int aliasId)              0x9ea980
//   void   ReferenceAlias.ForceRefTo(ObjectReference) 0x9a47c0
//   void   ReferenceAlias.Clear()                    0x9a46f0
//   ref    ReferenceAlias.GetReference()             0x9a4740
//   Package Actor.GetCurrentPackage()                0x9898b0
//
// 🛑 A quest that is not RUNNING owns no alias instances, so `ForceRefTo` on
// one silently does nothing -- `StartQuest` is what makes the AI quest's
// aliases fillable at all.
// 🛑 `ForceRefTo` re-evaluates the actor's packages by itself, so no
// `EvaluatePackage` is needed after it.
// See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
constexpr std::uint64_t kQuestGetAlias = 56723;
constexpr std::uint64_t kAliasForceRefTo = 55288;
constexpr std::uint64_t kAliasClear = 55286;
constexpr std::uint64_t kAliasGetReference = 55287;
constexpr std::uint64_t kActorCurrentPackage = 54681;
// Actor.EvaluatePackage() (0x9e81b0 on 1.6.1170, 0x989500 on 1.6.659), the
// function its 'EvaluatePackage' registration stores.
constexpr std::uint64_t kActorEvaluatePackage = 54672;

// The one-call world queries, each found at its registration and verified
// present in all 12 shipped versionlibs INCLUDING 1.6.1170:
//   bool    Actor.HasLOS(Actor other)                    0x989df0
//   bool    Actor.IsDetectedBy(Actor other)              0x98a010
//   Actor   Actor.GetCombatTarget()                      0x989840
//   bool    Actor.IsWeaponDrawn()                        0x996920
//   bool    Actor.IsSneaking()                           0x996870
//   bool    Actor.IsRunning()                            0x996860
//   void    Actor.Resurrect()                            0x98ad40
//   ref     ObjectReference.DropObject(Form, int)        0x9cde20
//   Weather Weather.GetCurrentWeather()                  0x9ed570  global
//   int     Weather.GetClassification()                  0x9ec210
//
// 🛑 `IsSneaking` (54953) and `IsRunning` (54952) are ADJACENT ids on
// near-identical leaf functions -- the name locator matched one thunk for
// both, so each was taken from its own registration site.
// See: docs/commentary/morrowind_runtime.md#the-query-commands
constexpr std::uint64_t kActorHasLos = 54697;
constexpr std::uint64_t kActorIsDetectedBy = 54706;
constexpr std::uint64_t kActorCombatTarget = 54680;
// TEMP (follower-freeze diag): int Actor.GetCombatState() 0x9e84e0 on 1.6.1170.
constexpr std::uint64_t kActorCombatState = 54679;
constexpr std::uint64_t kActorWeaponDrawn = 54957;
constexpr std::uint64_t kActorIsSneaking = 54953;
constexpr std::uint64_t kActorIsRunning = 54952;
constexpr std::uint64_t kActorResurrect = 54736;
constexpr std::uint64_t kRefDropObject = 56157;
constexpr std::uint64_t kWeatherCurrent = 56803;
constexpr std::uint64_t kWeatherClassification = 56775;

// The weather setters, found at their Weather registrations the same way:
//   void Weather.ForceActive(bool override)                 0xa4b1f0  [0x9ec1b0]
//   void Weather.SetActive(bool override, bool accelerate)  0xa4b210  [0x9ec1d0]
//   void Weather.ReleaseOverride()                          0xa4c630  [0x9ed5f0]  global
// Live-image RVAs (1.6.1170), GOG 1.6.659 in brackets.
// See: docs/commentary/morrowind_runtime.md#region-weather
constexpr std::uint64_t kWeatherForceActive = 56773;
constexpr std::uint64_t kWeatherSetActive = 56774;
constexpr std::uint64_t kWeatherReleaseOverride = 56807;

// int ActorBase.GetDeadCount() (0x9c5370): the engine's own per-base kill
// count, the only native of that name.
constexpr std::uint64_t kActorBaseDeadCount = 55987;

// The equip queries, located the same way and present in all 12
// shipped versionlibs. Live-image RVAs (1.6.1170), GOG 1.6.659 in brackets:
//   bool  Actor.IsEquipped(Form)           0x9e8d20  [0x98a070]
//   int   Actor.GetEquippedItemType(int)   0x9e8710  [0x989a60]
//   Spell Actor.GetEquippedSpell(int)      0x9e8630  [0x989980]
//
// 🛑 Two registration SHAPES sit side by side. `IsEquipped` passes the
// function in `lea r9` beside the name, but the other three `xor r9d, r9d`
// and store it into `[rbx+0x50]` AFTER the registering call -- reading only
// the `r9` form finds nothing and reads as "no such native".
// See: docs/commentary/morrowind_runtime.md#the-query-commands
// `SetForceSneak` is a FLAG on the actor, not a call. Its console handler
// (0x3552b0, from the command table row at 0x1fdfc00) reads `[actor+0xCC]`,
// ORs 4 to set and ANDs ~4 to clear, then echoes `SetForceSneak >> %0.2f`.
//
// 🛑 `Actor.StartSneaking` is NOT this: it compares its target against the
// player singleton and only acts for the player, so it silently did nothing
// for every NPC. Verified in game -- the console command sneaks an NPC, the
// native does not.
// See: docs/commentary/morrowind_runtime.md#forced-movement-is-a-latch
constexpr std::size_t kOffActorMoveFlags = 0xCC;
constexpr std::uint32_t kActorFlagForceSneak = 4;
constexpr std::uint64_t kActorIsEquipped = 54707;
constexpr std::uint64_t kActorEquippedItemType = 54685;
constexpr std::uint64_t kActorEquippedSpell = 54683;

// The spell natives, each read off its registration site's `lea` beside the
// name string, the owning script confirmed from the `lea r8` class string
// (`Actor` for five, `Spell` for Cast), and inverted through the Address
// Library. All six exist in ALL 12 shipped versionlibs -- the check the angle
// getters failed.
//   bool Actor.AddSpell(Spell, bool verbose)     0x988ad0
//   bool Actor.RemoveSpell(Spell)                0x988940
//   bool Actor.HasSpell(Form)                    0x988bc0
//   bool Actor.HasMagicEffect(MagicEffect)       0x988a00
//   bool Actor.DispelSpell(Spell)                0x9889a0
//   void Spell.Cast(ObjectReference source, ObjectReference target)  0x9bb750
//
// 🛑 `Spell.Cast` is a MEMBER function on the SPEL, so the spell form is
// `self` and BOTH actors ride as arguments. `ExplodeSpell` passes the same
// reference for both, which is what "explode on the calling object" means.
// See: docs/commentary/morrowind_runtime.md#spell-commands
constexpr std::uint64_t kActorAddSpell = 54652;
constexpr std::uint64_t kActorRemoveSpell = 54647;
constexpr std::uint64_t kActorHasSpell = 54653;
constexpr std::uint64_t kActorHasMagicEffect = 54649;
constexpr std::uint64_t kActorDispelSpell = 54648;
constexpr std::uint64_t kSpellCast = 55747;

// TESGlobal's value, the float `GlobalVariable.GetValue` (0x9c2b30) returns:
// `movss xmm0,[r8+0x34]`, a two-instruction leaf, so the field is read.
constexpr std::size_t kOffGlobalValue = 0x34;

// The player's faction standing, pushed onto the converted FACT, and a TES4
// actor's stat faction ranks. Live-image RVAs (1.6.1170), GOG 1.6.659 in
// brackets:
//   int  Actor.GetFactionRank(Faction)        0x9e87a0  [0x989af0]
//        tail-calls the rank lookup with (actor, faction, actor == player).
//   void Actor.SetFactionRank(Faction, int)   0x9ea340  [0x98b690]
//        `lea r9` form; the rank rides at [rsp+0x28] and is read as a byte.
//        Adds the actor to the faction when it is not already in it.
//   void Faction.SetPlayerExpelled(bool)      0xa1dc80  [0x9befc0]
//        stored-after form; sets or clears bit 4 of the faction's +0x58 flags.
//   void Actor.RemoveFromFaction(Faction)     0x9f55f0  [0x996940]
//        `lea r9` form; tail-calls the engine's remove with (actor, faction).
// See: docs/commentary/morrowind_runtime.md#player-factions
constexpr std::uint64_t kActorGetFactionRank = 54686;
constexpr std::uint64_t kActorSetFactionRank = 54750;
constexpr std::uint64_t kFactionSetPlayerExpelled = 55843;
constexpr std::uint64_t kActorRemoveFromFaction = 54958;

// Actor.IsDead() (0x989ff0), the r9 of its registration beside 'IsDead'.
//
// 🛑 `OnDeath` is POLLED from the tick rather than hooked. TES3 raises it for
// one tick on the actor's own script, which a poll reproduces exactly, and the
// tick already visits every bound instance -- a death hook would be a second
// mechanism for no gain.
constexpr std::uint64_t kActorIsDead = 54705;

// The engine's frame timer (0x31cc270 on 1.6.1170): +0x18 the frame's game
// delta (id 410199), +0x1C its real delta (410200), +0x20 a millisecond clock
// that advances by each frame's CLAMPED delta, so under iFPSClamp it runs with
// the game: measured 8,280 ms over ~2.07 s real at iFPSClamp=15 (4.2x).
// See: docs/commentary/headless_testing.md#speed
constexpr std::uint64_t kFrameTimer = 410196;
constexpr std::size_t kOffFrameClockMs = 0x20;
// +0x10 the fixed game step per frame in MILLISECONDS, 1000/iFPSClamp, 0
// unclamped: set once from the INI (1.6.1170 0x642ff6) and read by the timer
// update (0xcc9538) every frame, which adds it to the ms clock and sets the
// frame's delta to step * 0.001.
constexpr std::size_t kOffFrameStep = 0x10;

// The game-settings collection (a pointer; 0x30fd6f0 on 1.6.1170, as SKSE's
// g_gameSettingCollection): its name -> Setting map sits at +0x118 (buckets
// +0x10, bucket count +0x08; entry {next, name, Setting*}); a Setting holds its
// value at +0x08 and its name at +0x10.
constexpr std::uint64_t kGameSettings = 400782;
constexpr std::size_t kOffSettingsMap = 0x118;
constexpr std::size_t kOffMapBucketCount = 0x08;
constexpr std::size_t kOffMapBuckets = 0x10;
constexpr std::size_t kOffSettingValue = 0x08;
constexpr std::size_t kOffSettingName = 0x10;

// Actor.Kill(Actor akKiller) (0x9e90c0 on 1.6.1170), the r9 of its one
// registration beside 'Kill', between 54719 and ModActorValue 54721.
constexpr std::uint64_t kActorKill = 54720;

// Game.GetForm(int formId) (0x9b3990), global: a form by its RUNTIME FormID,
// which is the only id a spawned reference has -- `PlaceAtPC` creates one with
// no authored placement, so Game.GetFormFromFile can never name it.
constexpr std::uint64_t kGameGetForm = 55566;

// ObjectReference.Is3DLoaded() (0x9ce880), the r9 of its registration beside
// 'Is3DLoaded'. TES3 runs a local script only while its object is LOADED, and
// this is that test -- without it an instance ticks forever once bound, for a
// thing no longer in the world.
// See: docs/plans/morrowind_object_scripts.md#unload-with-the-cell
constexpr std::uint64_t kRefIs3DLoaded = 56188;

// What barter and persuasion reach through, each the r9 of its registration
// beside the name string (`lea r9,[callback]; lea r8,"Actor"; lea rdx,"<name>"`)
// on 1.6.659:
//   void  Actor.ShowBarterMenu()                                  0x98bef0
//   int   Actor.GetLevel()                                        0x996650
//   float Actor.GetActorValuePercentage(BSFixedString* name)      0x989740
//   void  Game.AdvanceSkill(BSFixedString* skill, float amount)   0x9ace40
// AdvanceSkill is a GLOBAL native: its self slot is a tag, and the float
// rides fifth, on the stack.
// See: docs/commentary/morrowind_runtime.md#barter
constexpr std::uint64_t kActorShowBarterMenu = 54765;
constexpr std::uint64_t kActorGetLevel = 54927;
constexpr std::uint64_t kActorGetValuePercent = 54677;
constexpr std::uint64_t kGameAdvanceSkill = 55449;

// What the character sheet reads to count skill increases, on 1.6.1170:
//   float Actor.GetBaseActorValue(BSFixedString* name)  0x9e8460, the r9 of
//         its registration, as GetActorValue (54675) and the percentage
//         (54677) beside it are
//   Race  Actor.GetRace()  0x9f5340, `mov rax,[r8+0x1f8]; ret`, stored at
//         +0x50 of the native object its registration builds inline
// See: docs/commentary/morrowind_runtime.md#leveling
constexpr std::uint64_t kActorGetBaseValue = 54678;
constexpr std::uint64_t kActorGetRace = 54930;
// Actor.SetRace(Race) (0x9ea6b0 on 1.6.1170, 0x98ba00 on 1.6.659), the
// callback its registration beside 'SetRace' under 'Actor' stores; it opens
// with `cmp r9,[r8+0x1f8]`, the race GetRace reads. None puts back the race
// the actor was made with.
constexpr std::uint64_t kActorSetRace = 54758;

// Game.ShowTrainingMenu(Actor) (0xa127a0 on 1.6.1170, 0x9b3af0 on 1.6.659),
// the r9 of its registration beside 'ShowTrainingMenu' under 'Game'. GLOBAL:
// the self slot is a tag, and the callback hands its fourth argument, the
// trainer, on as rcx.
// See: docs/commentary/morrowind_runtime.md#barter
constexpr std::uint64_t kGameShowTrainingMenu = 55582;

// The character sheet's skill cap, read on 1.6.1170 and checked on 1.7.104.
//   PlayerCharacter::AdvanceSkill(av, points, form, unk)   0x736e20, id 40488:
//        PlayerCharacter vtable slot 247 (byte 0x7b8) on both builds; the hook
//        finds the slot holding it rather than assuming one, since VR's Actor
//        carries more virtuals. Every skill-USE experience arrives here
//        virtually -- no direct call to it exists -- and so does
//        Game.AdvanceSkill (0xa0baf0, `call [rax+0x7b8]`). It forwards to
//        PlayerSkills::AdvanceSkill (0x77ae60, id 41561).
//   TrainingMenu's train step(menu)                        0x96e710, id 52667:
//        session limit, the trainer's maximum, the gold check, then the gold
//        is taken and the skill at menu+0x40 incremented. Its one caller is
//        id 52662 (+0xd8). Books (id 17842) and Game.IncrementSkill (id 55616)
//        reach the increment by other callers, so the cap leaves them alone.
// See: docs/commentary/morrowind_runtime.md#skill-cap
constexpr std::uint64_t kPlayerAdvanceSkill = 40488;
constexpr std::size_t kMaxPlayerVirtuals = 400;
constexpr std::uint64_t kTrainingMenuTrain = 52667;
constexpr std::uint64_t kTrainingMenuTrainCaller = 52662;
constexpr std::size_t kTrainingCallerScan = 0x200;
constexpr std::size_t kOffTrainingMenuSkill = 0x40;
// VR's IMenu is 0x10 longer, so its train step (0x8fb9c0 on 1.4.15) reads the
// skill at +0x50; SE 1.5.97's (0x8ce8e0) reads +0x40 as AE does.
constexpr std::size_t kVrOffTrainingMenuSkill = 0x50;

// The player's sex and skill progress, read on 1.6.1170 and 1.7.104 alike.
//   TESNPC::GetSex (0x3a8df0; 0x3afeb0 on 1.7.104), what the ActorBase.GetSex
//        native jumps to: `cmp byte [rcx+0x1a],0x2b; movzx eax,byte
//        [rcx+0x38]; and eax,1` -- an NPC_ is female when bit 0 of +0x38 is set.
//   PlayerCharacter::AdvanceSkill (id 40488) loads the PlayerSkills pointer by
//        `mov rcx,[rcx+disp32]` 0x10 in: 0x9b8 on 1.6.1170, 0x9c0 on 1.7.104,
//        so the offset is read from that instruction. Its data block holds a
//        {level, points, pointsMax} per skill from +0x08, 12 bytes each, actor
//        value 6 first: PlayerSkills::AdvanceSkill (id 41561) adds to
//        [data + 12*(av-6) + 0xc] and compares it with +0x10. The player
//        level's {points, pointsMax} open the block, at +0x00 and +0x04
//        (skse64's PlayerSkills::Data levelPoints, levelPointsMax).
// See: docs/commentary/morrowind_runtime.md#skill-tooltips
constexpr std::uint8_t kFormTypeNpc = 0x2B;
// TESObjectDOOR's form type (skse64 GameForms.h kFormType_Door).
constexpr std::uint8_t kFormTypeDoor = 0x1D;
constexpr std::size_t kOffNpcSexFlags = 0x38;
constexpr std::size_t kAdvanceSkillLoadAt = 0x10;
constexpr std::size_t kLevelPoints = 0x00;
constexpr std::size_t kSkillDataFirst = 0x08;
constexpr std::size_t kSkillDataStride = 0x0C;
constexpr int kFirstSkillValue = 6;

// The character-creation and control natives, each the registration callback
// beside its name string under script `Game` on 1.6.1170:
//   void Game.ShowRaceMenu()                                      0xa12780
//   void Game.DisablePlayerControls(8 bools, int povType)         0xa0bcb0
//   void Game.EnablePlayerControls(8 bools, int povType)          0xa0bd90
// All three are GLOBAL natives: the self slot is a tag. The eight bools ride
// abMovement in r9b and the rest on the stack, in the order abFighting,
// abCamSwitch, abLooking, abSneaking, abMenu, abActivate, abJournalTabs --
// read off the callback, which tests them at [rsp+0x50] through [rsp+0x80].
// See: docs/commentary/morrowind_runtime.md#the-control-switches
constexpr std::uint64_t kGameShowRaceMenu = 55580;
// void Game.SetPlayerAIDriven(bool) (0xa12750 on 1.6.1170): the function the
// `Game` registration passes beside its name string, as ShowRaceMenu's
// 0xa12780 is 55580's. Lets packages drive the player (the test runner's walk).
constexpr std::uint64_t kGameSetPlayerAIDriven = 55577;
// void Game.ForceThirdPerson() (0xa12630 on 1.6.1170), found the same way: an
// AI-driven player in first person never walks.
constexpr std::uint64_t kGameForceThirdPerson = 55565;
// void Game.ForceFirstPerson() (0xa12620 on 1.6.1170), registered beside its
// name the same way: a steered player moves along its own facing only in first
// person, as third person turns it to the camera.
constexpr std::uint64_t kGameForceFirstPerson = 55564;
constexpr std::uint64_t kGameDisableControls = 55454;
constexpr std::uint64_t kGameEnableControls = 55455;

// TESObjectCELL's TESFullName: the BSFixedString at +0x28, read as the
// `const char*` it wraps.
constexpr std::size_t kOffCellFullName = 0x28;

constexpr std::uint64_t kRefAddItem = 56145;
// The base-form workers the AddItem and RemoveItem natives queue as a VM task
// (0xa3d6a0 and 0xa44fe0 on 1.6.1170): called directly, the change is immediate.
constexpr std::uint64_t kRefAddForm = 56489;
constexpr std::uint64_t kRefRemoveForm = 56647;
constexpr std::uint64_t kRefGetItemCount = 56173;
constexpr std::uint64_t kGameGetPlayer = 55469;

// ~BSFixedString (0xc60c30), to release an interned name.
constexpr std::uint64_t kBSFixedStringDtor = 69164;

// UIMessage ids: kMessage_Open, and kMessage_Close (2 is a legacy alias).
constexpr std::uint32_t kMessageOpen = 1;
constexpr std::uint32_t kMessageClose = 3;

// BSFixedString::BSFixedString(this, const char*) (0xc60ac0), the interning
// ctor every menu name goes through. A menu name must be an INTERNED string:
// AddMessage compares by pointer, so a plain char* never matches.
constexpr std::uint64_t kBSFixedStringCtor = 69161;

// Skyrim's own dialogue menu, the one a Morrowind speaker diverts away from.
constexpr const char* kVanillaDialogueMenu = "Dialogue Menu";
constexpr const char* kMessageBoxMenu = "MessageBoxMenu";
// The Sleep/Wait menu, and the paths in sleepwaitmenu.swf its Sleep button
// works through: the hours slider, and the handler the button calls.
constexpr const char* kSleepWaitMenu = "Sleep/Wait Menu";
constexpr const char* kSleepHoursPath = "_root.SleepWaitMenu_mc.HoursSlider.value";
constexpr const char* kSleepOkPath = "_root.SleepWaitMenu_mc.onOKPress";

// MenuTopicManager::GetSpeaker(this, TESObjectREFR** out) (0x5945b0). It
// reads the ObjectRefHandle at this+0x68, resolves it, and keeps the result
// only when its form type is 0x3e (Character).
constexpr std::uint64_t kGetSpeaker = 35293;

// The MenuTopicManager singleton POINTER (0x3137778 on 1.6.1170), read, never
// called. Found as the static the constructor stores the instance into.
//
// 🛑 NOT the address `tools/live/dialog_live.py` documents (0x3191880): that
// is the OBJECT, which that tool finds by scanning for the vtable pair. This
// is the pointer TO it, which is what a plugin can read directly.
constexpr std::uint64_t kMenuTopicManagerSingleton = 401099;

// GFxValue::SetString(this, const char*) (0x8c8830), which types a GFxValue as
// a string before SetVariable copies it into the movie. A GFxValue is 0x18
// bytes with its type at +8 and its data at +0x10.
constexpr std::uint64_t kGfxSetString = 51740;

// GFxMovieView vtable INDICES (skse64 ScaleformMovie.h), each confirmed
// against a byte offset the engine's own menus use:
//   SetVariable  0x10 -> [vt+0x80],  IMenu::NextFrame writes "CurrentTime"
//   GetVariable  0x11 -> [vt+0x88]
//   Invoke       0x16 -> [vt+0xb0]   (this, name, result, args, count)
//   Advance      0x25 -> [vt+0x128], IMenu::NextFrame(this, dt, 2)
//   Render       0x26 -> [vt+0x130], MessageBoxMenu::Render's tail-jump
//   HandleEvent  0x2d -> [vt+0x168], IMenu::ProcessMessage's forward
//
// 🛑 These are INDICES. The first build added 0x10 to the vtable as a BYTE
// offset, which is slot 2 -- an unrelated getter -- so SetVariable never ran
// and every field stayed empty with nothing in the log to say so.
constexpr std::size_t kMovieViewSetVariableSlot = 0x10;
constexpr std::size_t kMovieViewGetVariableSlot = 0x11;
constexpr std::size_t kMovieViewInvokeSlot = 0x16;
constexpr std::size_t kMovieViewAdvanceSlot = 0x25;
// GetVisibleFrameRect 0x1F (skse64 ScaleformMovie.h, after Get/SetViewScaleMode
// and Get/SetViewAlignment at 0x1B-0x1E): the part of the stage on screen as
// left, top, right, bottom floats, returned through a hidden pointer.
constexpr std::size_t kMovieViewVisibleRectSlot = 0x1F;
constexpr std::size_t kMovieViewRenderSlot = 0x26;
constexpr std::size_t kMovieViewHandleEventSlot = 0x2d;

// GFxValue: 0x18 bytes, type at +8 (3 number, 4 string; bit 6 marks a value
// the movie owns), data at +0x10.
constexpr std::size_t kGfxValueSize = 0x18;
constexpr std::size_t kGfxValueTypeOffset = 0x8;
constexpr std::size_t kGfxValueDataOffset = 0x10;
constexpr std::uint32_t kGfxValueTypeMask = 0x8f;
constexpr std::uint32_t kGfxValueNumber = 3;
// A string's data is its `const char*`, or a pointer to one when managed.
constexpr std::uint32_t kGfxValueString = 4;
constexpr std::uint32_t kGfxValueManaged = 0x40;

// Debug.Notification(string) (0xa07340), global, the callback stored beside
// "Notification": the same shape as Debug.MessageBox one id before it. What
// OpenMW's buttonless messageBox is.
constexpr std::uint64_t kDebugNotification = 55377;

// Game.PlayBink(string, bool abortOnFocusLost, bool abortOnEscape, bool loop,
// bool pauseVoice) (0xa0d0b0), global, the callback beside "PlayBink" at its
// registration: it reads the file through [r9] and the four flags from
// [rsp+0x28..0x40] at entry, and plays Bink 1 ("BIK") and Bink 2 ("KB2").
constexpr std::uint64_t kGamePlayBink = 55488;

// Crime natives, each found at its registration in 1.6.1170 (the callback's
// lea beside the name's): Actor.GetCrimeFaction, Faction.GetCrimeGold /
// SetCrimeGold / SetCrimeGoldViolent / PlayerPayCrimeGold / SendPlayerToJail.
// See: docs/commentary/morrowind_runtime.md#crime-is-the-engines
constexpr std::uint64_t kActorGetCrimeFaction = 54921;
constexpr std::uint64_t kFactionGetCrimeGold = 55794;
constexpr std::uint64_t kFactionSetCrimeGold = 55809;
constexpr std::uint64_t kFactionSetCrimeGoldViolent = 55810;
constexpr std::uint64_t kFactionPlayerPayCrimeGold = 55805;
constexpr std::uint64_t kFactionSendPlayerToJail = 55807;
// Game.ServeTime (1.6.1170 0xa126b0): PlayerCharacter's ServeTime, which
// passes the sentence's days and releases the player. kOffPlayerJailFaction is
// the faction whose jail holds the player; SendPlayerToJail sets it only once
// no menu is open. A 1.6.x offset: read it through PlayerField.
constexpr std::uint64_t kGameServeTime = 55573;
constexpr std::size_t kOffPlayerJailFaction = 0x720;

// TESObjectREFR's base form, which TESObjectREFR::ActivateRef dispatches on.
constexpr std::size_t kOffRefBase = 0x40;

// A soul the player traps rides on the gem's inventory stack as ExtraSoul,
// so the soul gem commands walk the inventory. Read off 1.6.1170:
//   TESObjectREFR::GetInventoryChanges (id 16040) passes `ref+0x70`, the
//        ref's ExtraDataList, to ExtraDataList::GetInventoryChanges (11717:
//        the type-0x15 extra's +0x10), whose list of entries sits at +0
//   InventoryEntryData::GetSoulLevel (0x223250) reads the entry's base form
//        at +0 and its list of ExtraDataLists at +8, then asks one list
//        ExtraDataList::GetSoulLevel (11707: ExtraSoul, type 0x9C, byte +0x10)
//   ExtraDataList::GetCount (11704: ExtraCount, type 0x24, word +0x10, else 1)
//   TESObjectREFR::RemoveItem is vtable slot 86 (0x2e1750): (ret handle,
//        item, count, reason, ExtraDataList*, moveTo, dropLoc, rotate), the
//        list passed on to InventoryChanges::RemoveItem
// None is in the pre-AE tables, so pre-AE builds count only filled records.
// See: docs/commentary/morrowind_runtime.md#soul-gems
constexpr std::size_t kOffRefExtraList = 0x70;
constexpr std::uint64_t kExtraListInventoryChanges = 11717;
constexpr std::uint64_t kExtraListSoulLevel = 11707;
constexpr std::uint64_t kExtraListCount = 11704;
// The Follow procedure's spacing (0x4161f0 on 1.6.1170): float (follower,
// leader), the room the leader's earlier followers take up -- the sum of
// neighbouring bound radii plus 100 per place in the leader's ExtraFollower
// list -- added to the package's radii. Called by the procedure's update
// (0x468f10, call at +0x6f8) and an actor update (0x6f50a0, call at +0x580).
constexpr std::uint64_t kFollowSpacing = 26378;
constexpr std::uint64_t kFollowProcedureUpdate = 28644;
constexpr std::size_t kFollowProcedureScan = 0x800;
constexpr std::uint64_t kActorFollowUpdate = 39639;
constexpr std::size_t kActorFollowScan = 0x700;
// TEMP (follower-freeze diag): ExtraDataList's ExtraFollower (type 0x1D;
// 0x15d770 on 1.6.1170): entries {handle, float} at +0x10, count at +0x20.
constexpr std::uint64_t kExtraListFollower = 11651;
// A door's lock. TESObjectREFR::GetLock (20223, 0x2fad60 on 1.6.1170) returns the ref's own ExtraLock
// (type 0x2A, REFR_LOCK* at +0x10) and, when it has none, its LINKED door's (via ExtraTeleport 0x2B);
// TESObjectDOOR::Activate (0x27da50) refuses the player while that lock's flags (+0x10) carry bit 0.
// ExtraDataList::GetByType(list, type) (12329, 0x179250) finds the ref's own.
constexpr std::uint64_t kExtraListGetByType = 12329;
constexpr std::uint8_t kExtraLock = 0x2A;
constexpr std::size_t kOffExtraLockData = 0x10;
constexpr std::uint64_t kRefGetLock = 20223;
constexpr std::size_t kOffLockFlags = 0x10;
constexpr std::uint8_t kLockLocked = 0x01;
constexpr std::size_t kOffEntryObject = 0x0;
constexpr std::size_t kOffEntryExtraLists = 0x8;
constexpr std::size_t kRefRemoveItemSlot = 86;

// Soul Trap sizes a soul by the victim's LEVEL, not the plugin's soul value
// (Sittali, level 20, a grand soul in TES3, trapped as soul 3). Read off
// 1.6.1170: Actor::GetSoulSize (38817, 0x6c6a80; actor in rcx, size 0..5 in
// eax) passes level and black-soul flag to 0x41ae60 (5 when black, else the
// level against four thresholds). Its four direct callers, all repointed:
//   0x6c6bdf / 0x6c6ca1  Actor::TrapSoul (0x6c6ae0) matching the gem, then
//                        writing the size into its ExtraSoul (0x224720)
//   0x2319ba             the best-soul-gem search
//   0x5d3e3a             SoulTrapEffect
// See: docs/commentary/morrowind_runtime.md#soul-gems
constexpr std::uint64_t kActorGetSoulSize = 38817;

// TESObjectREFR's own scale in percent (uint16, 100 by default): what TES3's
// GetScale reads. Papyrus GetScale multiplies in the race height and lags a
// SetScale made in the same frame (measured: 1.03 after `SetScale 1`). 0x98
// on 1.6.1170, found by `player.setscale` 1.5 then 0.75 reading 150 then 75;
// SKSE's pre-AE layout has it at 0x90, so it is read through ActorField.
constexpr std::size_t kOffRefScale = 0x98;

// SwiftSwim, Levitate and SlowFall steer the character controller. Each id
// was read off 1.6.1170 and checked to do the same on 1.7.104 (1.6.659 in
// brackets):
//   bhkCharacterStateInAir vtable     0x19f70b8  [0x189cfa8]  RTTI
//   bhkCharacterStateInAir slot 8     0xf01f40   [0xe74db0]   simulate(state,
//        controller): adds gravity to the velocity, tracks the fall start
//   bhkCharacterStateOnGround vtable  0x19f72f8  [0x189d1e8]  RTTI
//   bhkCharacterStateOnGround slot 8  0xf02550   [0xe753c0]
//   bhkCharacterStateSwimming vtable  0x19f7378  [0x189d268]  RTTI
//   bhkCharacterStateSwimming slot 8  0xf02b30   [0xe759a0]   rotates the
//        stroke at controller+0x70 into the velocity, then adds buoyancy
//   Actor::GetCharController          0x6628f0   [0x608600]   what the
//        `GetVelocity` console handler (0x329ac0) calls on its actor:
//        [actor+0xF8] -> [+0x8] -> [+0x250]
//   PlayerControls singleton pointer  0x30fda18  [0x2f59828]  stored by its
//        constructor (0x79a4c0) beside its four sink vtables
// See: docs/commentary/morrowind_runtime.md#levitate-and-slowfall
constexpr std::uint64_t kInAirStateVtable = 240826;
constexpr std::uint64_t kInAirSimulate = 80098;
constexpr std::uint64_t kOnGroundStateVtable = 240833;
constexpr std::uint64_t kOnGroundSimulate = 80106;
constexpr std::uint64_t kSwimmingStateVtable = 240836;
constexpr std::uint64_t kSwimmingSimulate = 80110;
constexpr std::uint64_t kActorGetCharController = 37258;
constexpr std::uint64_t kPlayerControlsSingleton = 400864;
constexpr std::size_t kStateSimulateSlot = 0x40;

// bhkCharacterController fields the state simulates read and write, identical
// on 1.6.1170 and 1.7.104: the swim stroke (an hkVector4), the step's
// seconds, the velocity it integrates (an hkVector4 in havok units), and the
// highest z of the fall, which landing charges damage from. Slot 2 is
// GetPositionImpl(out, applyCenterOffset).
constexpr std::size_t kOffControllerStroke = 0x70;
constexpr std::size_t kOffControllerStepSeconds = 0x88;
constexpr std::size_t kOffControllerVelocity = 0x90;
constexpr std::size_t kOffControllerFallStart = 0x240;
constexpr std::size_t kControllerGetPositionSlot = 2;

// A still actor is never pushed out of what it stands in (game_calls_still.cpp).
//   bhkCharProxyController vtable  0x19edff0  [1.7.104 0x1a89578]  the one
//        GetCharController's pointer carries; its GetPositionImpl (0xed1dc0,
//        [0x1098920]) reads the bhkCharacterProxy at +0x330, whose
//        hkpCharacterProxy is at +0x10
//   hkpCharacterProxy+0xdc  penetrationRecoverySpeed: copied to and from the
//        Cinfo's +0x7c (0xb80497, 0xb8058c), the offset Havok's own class
//        table gives it; at 0xb83c44 a penetrating contact adds
//        -distance * this along its normal, then is clamped to distance 0
//   ProcessLists singleton pointer  0x20f69b0: the nearby actors' handles at
//        +0x30, their count at +0x40 (the two fields its 443 readers use most)
constexpr std::uint64_t kProxyControllerVtable = 240560;
constexpr std::size_t kOffProxyControllerProxy = 0x340;
constexpr std::size_t kOffProxyRecoverySpeed = 0xdc;
constexpr std::uint64_t kProcessListsSingleton = 400315;
constexpr std::size_t kOffHighActorHandles = 0x30;
constexpr std::size_t kOffHighActorCount = 0x40;

// An NPC npc_anim holds on its spot is taken out of the physics simulation
// the way the engine takes a paired-animation actor out of it: bit 17 of the
// bhkCharacterController flags at +0x218 (CommonLib's kNoSim).
//   0x54a070 (ID 32847) sets it on an actor's controller; its only caller,
//        0x547e10 (ID 32803), looks up two ACHR handles and freezes both
//   0xed505b, in the proxy controller's step (ID 79206): the bit jumps past
//        the whole step, so no gravity, push-out or support moves the actor
//   0x6ab9c8: the move onto the navmesh skips an actor carrying the bit
//   0x688156..0x6881f6: SetPosition sets the bit around its move and puts
//        the old value back, so a TranslateTo glide keeps the hold
constexpr std::size_t kOffControllerFlags = 0x218;
constexpr std::uint32_t kControllerNoSim = 1u << 17;

// PlayerControlsData.moveInputVec: strafe then forward, -1..1. The input sink
// (0x79a970) zeroes it, then hands `this+0x24` to each handler, and
// MovementHandler writes x at +0 and y at +4 -- on 1.7.104 too.
constexpr std::size_t kOffPlayerControlsMoveX = 0x24;
constexpr std::size_t kOffPlayerControlsMoveY = 0x28;
// PlayerControlsData.autoMove, the Auto-Move key's toggle: AutoMoveHandler's
// ProcessButton (0x79f360 on 1.6.1170) flips the byte at data+0x24, the data
// being this+0x24 as above.
constexpr std::size_t kOffPlayerControlsAutoMove = 0x48;
// PlayerControlsData.running, the Always Run toggle: ToggleRunHandler's
// ProcessButton (0x79f980 on 1.6.1170) flips the byte at data+0x25.
constexpr std::size_t kOffPlayerControlsRunning = 0x49;

// An actor's active effects, as Actor.HasMagicEffect (0x9e76b0) walks them on
// 1.6.1170 and 1.7.104 alike: the MagicTarget base at actor+0xA0, its vtable
// slot 7 (+0x38) returning the list head, nodes {ActiveEffect*, next}, and
// the MGEF at [[effect+0x48]+0x10] (0x5ac560). Magnitude and flags, with
// 0x8000 Inactive, are SKSE64's ActiveEffect layout (GameObjects.h), whose
// GetMagnitude native reads +0x78.
constexpr std::size_t kOffActorMagicTarget = 0xA0;
constexpr std::size_t kActiveEffectListSlot = 7;
constexpr std::size_t kOffActiveEffectItem = 0x48;
constexpr std::size_t kOffEffectItemBase = 0x10;
constexpr std::size_t kOffActiveEffectMagnitude = 0x78;
constexpr std::size_t kOffActiveEffectFlags = 0x7C;
constexpr std::uint32_t kActiveEffectInactive = 0x8000;

// The spell an active effect came from, at effect+0x40 (SKSE64's `item`). The
// engine's own GetDisease reads it: the condition handler (0x32a830) calls
// 0x5c29a0, which walks this list with a visitor (0x5c59d0) that loads
// [effect+0x40], asks its vtable slot 0x298 for the spell type, compares it to
// Disease (1), and skips an effect whose flags (+0x7C) hold bit 0x40000.
// See: docs/commentary/morrowind_runtime.md#disease-queries
constexpr std::size_t kOffActiveEffectSource = 0x40;
constexpr std::uint32_t kActiveEffectDispelled = 0x40000;

// Game units per havok unit: what InAir's simulate scales the controller's
// height by before comparing it with the fall start.
constexpr float kHavokToGame = 69.99125f;

}  // namespace tesruntime::mw::ids
