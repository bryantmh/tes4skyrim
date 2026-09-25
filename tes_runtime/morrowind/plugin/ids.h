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

// 🛑 Activate is OVERRIDDEN per form type, so the NPC swap above reaches only
// actors. Read at vtable byte 0x1b8 out of the 1.6.659 image: nine DISTINCT
// functions, of which 0x233b60 is the inherited TESBoundObject::Activate that
// the plain item types share. An object script sits on a BOOK, a WEAP or an
// ACTI as often as on an actor, so each is swapped separately.
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
    {"TESObjectMISC", 189689, 17670},
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

// ObjectReference.Enable(bool fadeIn) (0x9cdfa0), .Disable(bool fadeOut)
// (0x9cddc0) and .IsDisabled() (0x9e5b90). Enable/Disable register with the
// callback stored AFTER the registration call (`lea rax,[cb]` into
// `[rbx+0x50]`), not in r9 the way the argument-less natives do.
constexpr std::uint64_t kRefEnable = 56158;
constexpr std::uint64_t kRefDisable = 56155;
constexpr std::uint64_t kRefIsDisabled = 56639;

// ObjectReference.GetPositionX/Y/Z (0x9ce600/610/620) and GetAngleX/Y/Z
// (0x9ce0c0/0e0/100), one instruction each: `movss xmm0,[r8+off]`, the angle
// getters then multiplying by 180/pi. They never touch rcx, so the VM pointer
// is irrelevant to them.
//
// 🛑 The ANGLE getters return DEGREES while the field holds radians, and
// SetAngle takes degrees back -- so a get/set round trip needs no conversion,
// but reading the field directly does.
constexpr std::uint64_t kRefGetPositionX = 56178;
constexpr std::uint64_t kRefGetPositionY = 56179;
constexpr std::uint64_t kRefGetPositionZ = 56180;

// 🛑 THE ANGLE GETTERS HAVE NO STABLE ID ON A CURRENT BUILD. Ids 56162-56164
// exist on 1.6.659 and are GONE on 1.6.1170 -- measured against both
// versionlibs, and the live log showed all three UNRESOLVED, which silently
// broke every rotation (Rotate, RotateWorld, PositionCell's zRot, Face).
// Each is a 3-instruction leaf the Address Library stopped covering, so the
// field is read directly instead: rotation x/y/z are floats at these offsets
// on TESObjectREFR, immediately before the position triple at +0x54.
// See: docs/commentary/morrowind_runtime.md#the-angle-getters-have-no-id
constexpr std::size_t kOffRefRotX = 0x48;
constexpr std::size_t kOffRefRotY = 0x4c;
constexpr std::size_t kOffRefRotZ = 0x50;

// ObjectReference.SetPosition(float x, y, z) (0x9d1c60) and SetAngle (0x9d12d0)
// take ALL THREE axes, so a one-axis MWScript `SetPos` reads the other two
// back first. Unlike the getters these DO use rcx, to report "Cannot move the
// player because they are dead", so they need the real VM.
constexpr std::uint64_t kRefSetPosition = 56234;
constexpr std::uint64_t kRefSetAngle = 56224;

// ObjectReference.TranslateTo(x, y, z, ax, ay, az, speed, maxRotSpeed)
// (0x9d1f70, the latent native registered beside the "TranslateTo" string at
// 0x9d760d). Angles in degrees; it glides the loaded 3D without reloading it.
constexpr std::uint64_t kRefTranslateTo = 56237;

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
constexpr std::uint64_t kActorGetValue = 54675;
constexpr std::uint64_t kActorSetValue = 54743;
constexpr std::uint64_t kActorRestoreValue = 54737;
constexpr std::uint64_t kActorDamageValue = 54660;
constexpr std::uint64_t kActorEquipItem = 54661;

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

// Debug.MessageBox(string) (0x9a8640), global: found at its registration,
// `lea r8,['Debug'] / lea rdx,['MessageBox']`, the callback stored into
// [rdi+0x50] AFTER the call. Takes the text in r9 as a BSFixedString.
//
// 🛑 The MESSAGE-BOX native, not Notification: TES3's `MessageBox` is modal and
// may carry buttons. `Message.Show` cannot serve it -- that needs an authored
// MESG record per string, and the corpus passes arbitrary runtime text.
// See: docs/plans/morrowind_object_scripts.md#messagebox
constexpr std::uint64_t kDebugMessageBox = 55376;

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
//   float ObjectReference.GetScale()                                    0x9e5b30
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
constexpr std::uint64_t kRefGetScale = 56633;
constexpr std::uint64_t kRefSetScale = 56240;

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
constexpr std::uint64_t kActorWeaponDrawn = 54957;
constexpr std::uint64_t kActorIsSneaking = 54953;
constexpr std::uint64_t kActorIsRunning = 54952;
constexpr std::uint64_t kActorResurrect = 54736;
constexpr std::uint64_t kRefDropObject = 56157;
constexpr std::uint64_t kWeatherCurrent = 56803;
constexpr std::uint64_t kWeatherClassification = 56775;

// int ActorBase.GetDeadCount() (0x9c5370): the engine's own per-base kill
// count, the only native of that name.
constexpr std::uint64_t kActorBaseDeadCount = 55987;

// The equip and sleep queries, located the same way and present in all 12
// shipped versionlibs. Live-image RVAs (1.6.1170), GOG 1.6.659 in brackets:
//   bool  Actor.IsEquipped(Form)           0x9e8d20  [0x98a070]
//   int   Actor.GetSleepState()            0x9e9030  [0x98a380]
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
constexpr std::uint64_t kActorGetSleepState = 54715;
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

// The player's faction standing, pushed onto the converted FACT. Live-image
// RVAs (1.6.1170), GOG 1.6.659 in brackets:
//   void Actor.SetFactionRank(Faction, int)   0x9ea340  [0x98b690]
//        `lea r9` form; the rank rides at [rsp+0x28] and is read as a byte.
//        Adds the actor to the faction when it is not already in it.
//   void Faction.SetPlayerExpelled(bool)      0xa1dc80  [0x9befc0]
//        stored-after form; sets or clears bit 4 of the faction's +0x58 flags.
// See: docs/commentary/morrowind_runtime.md#player-factions
constexpr std::uint64_t kActorSetFactionRank = 54750;
constexpr std::uint64_t kFactionSetPlayerExpelled = 55843;

// Actor.IsDead() (0x989ff0), the r9 of its registration beside 'IsDead'.
//
// 🛑 `OnDeath` is POLLED from the tick rather than hooked. TES3 raises it for
// one tick on the actor's own script, which a poll reproduces exactly, and the
// tick already visits every bound instance -- a death hook would be a second
// mechanism for no gain.
constexpr std::uint64_t kActorIsDead = 54705;

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

// Game.ShowTrainingMenu(Actor) (0xa127a0 on 1.6.1170, 0x9b3af0 on 1.6.659),
// the r9 of its registration beside 'ShowTrainingMenu' under 'Game'. GLOBAL:
// the self slot is a tag, and the callback hands its fourth argument, the
// trainer, on as rcx.
// See: docs/commentary/morrowind_runtime.md#barter
constexpr std::uint64_t kGameShowTrainingMenu = 55582;

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
constexpr std::uint64_t kGameDisableControls = 55454;
constexpr std::uint64_t kGameEnableControls = 55455;

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

// Debug.Notification(string) (0xa07340), global, the callback stored beside
// "Notification": the same shape as Debug.MessageBox one id before it. What
// OpenMW's buttonless messageBox is.
constexpr std::uint64_t kDebugNotification = 55377;

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
// no menu is open.
constexpr std::uint64_t kGameServeTime = 55573;
constexpr std::size_t kOffPlayerJailFaction = 0x720;

// TESObjectREFR's base form, which TESObjectREFR::ActivateRef dispatches on.
constexpr std::size_t kOffRefBase = 0x40;

}  // namespace tesruntime::mw::ids
