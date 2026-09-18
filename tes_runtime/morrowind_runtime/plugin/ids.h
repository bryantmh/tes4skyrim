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

namespace mwruntime::ids {

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
constexpr std::size_t kMouseEventXOffset = 0x4;
constexpr std::size_t kMouseEventButtonOffset = 0x10;

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

// The Activate slot's byte offset in that vtable, read off the dispatch
// `mov rcx,[ref+0x40] / mov rax,[rcx] / call [rax+0x1b8]`.
constexpr std::size_t kActivateSlot = 0x1b8;

// TESNPC::Activate (0x3b9500 on 1.6.1170), the value that slot holds. Checked
// against the running build's image so a wrong swap is caught before install.
constexpr std::uint64_t kNpcActivate = 24715;

// UIManager::AddMessage(this, BSFixedString* menu, u32 msgId, void* data)
// (0x170730). Identified by its pool arithmetic: [rcx+0x378] is poolUsed,
// compared against 0x40 = kPoolSize, and (poolUsed + 0x1c) << 5 lands on
// messagePool at 0x380 with stride 32. Inverts to the RVA SKSE hardcodes.
constexpr std::uint64_t kUIAddMessage = 13631;

// The UIManager singleton POINTER (0x20f8950 on 1.6.1170), read, never called.
constexpr std::uint64_t kUIManagerSingleton = 400445;

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

// ObjectReference.Enable(bool fadeIn) (0x9cdfa0), .Disable(bool fadeOut)
// (0x9cddc0) and .IsDisabled() (0x9e5b90). Enable/Disable register with the
// callback stored AFTER the registration call (`lea rax,[cb]` into
// `[rbx+0x50]`), not in r9 the way the argument-less natives do.
constexpr std::uint64_t kRefEnable = 56158;
constexpr std::uint64_t kRefDisable = 56155;
constexpr std::uint64_t kRefIsDisabled = 56639;

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
constexpr std::uint64_t kRefActivate = 56139;
constexpr std::uint64_t kRefLock = 56198;
constexpr std::uint64_t kRefIsLocked = 56196;
constexpr std::uint64_t kRefSetLockLevel = 56229;
constexpr std::uint64_t kRefDelete = 56154;
constexpr std::uint64_t kRefGetDistance = 56170;
constexpr std::uint64_t kRefGetParentCell = 56632;
constexpr std::uint64_t kCellIsInterior = 56056;
constexpr std::uint64_t kActorGetValue = 54675;
constexpr std::uint64_t kActorSetValue = 54743;
constexpr std::uint64_t kActorRestoreValue = 54737;
constexpr std::uint64_t kActorDamageValue = 54660;
constexpr std::uint64_t kActorEquipItem = 54661;

// TESObjectCELL's TESFullName: the BSFixedString at +0x28, read as the
// `const char*` it wraps.
constexpr std::size_t kOffCellFullName = 0x28;

constexpr std::uint64_t kRefAddItem = 56145;
constexpr std::uint64_t kRefRemoveItem = 56218;
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
constexpr std::size_t kMovieViewRenderSlot = 0x26;
constexpr std::size_t kMovieViewHandleEventSlot = 0x2d;

// GFxValue: 0x18 bytes, type at +8 (3 number, 4 string; bit 6 marks a value
// the movie owns), data at +0x10.
constexpr std::size_t kGfxValueSize = 0x18;
constexpr std::size_t kGfxValueTypeOffset = 0x8;
constexpr std::size_t kGfxValueDataOffset = 0x10;
constexpr std::uint32_t kGfxValueTypeMask = 0x8f;
constexpr std::uint32_t kGfxValueNumber = 3;

}  // namespace mwruntime::ids
