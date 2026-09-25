// Address Library stable IDs and engine layouts for everything TESRuntime
// touches: jails, journal stage text, the crafting bench and the alchemy
// apparatus. The shared engine services' ids are in
// common/engine_ids.h.
//
// Each id was found in 1.6.1170, the Steam build the user plays, and checked
// against every shipped versionlib (tools/validate/stable_id_check.py). Nothing
// here is a raw RVA.

#pragma once

#include <cstddef>
#include <cstdint>

namespace tesruntime::ids {

// ---------------------------------------------------------------------------
// Jails (docs/commentary/tes_runtime_crime.md#nearest-jail)
// ---------------------------------------------------------------------------

// Papyrus natives, each found at its registration in 1.6.1170: the lea of the
// callback beside the lea of its name (ObjectReference ones store it at
// [rsi+0x50] after the name). Game.GetPlayer is shared with the Morrowind
// runtime's table.
constexpr std::uint64_t kGetPlayer = 55469;
constexpr std::uint64_t kRefGetParentCell = 56632;
constexpr std::uint64_t kRefGetWorldSpace = 56636;
constexpr std::uint64_t kRefGetPositionX = 56178;
constexpr std::uint64_t kRefGetPositionY = 56179;
constexpr std::uint64_t kRefIsDisabled = 56639;
constexpr std::uint64_t kCellIsInterior = 56056;

// TESFaction crime data: TESObjectREFR* jail marker, follower wait marker,
// stolen-goods container and player-inventory container. TESFaction::Load
// (0x3ac9c0) stores the JAIL/WAIT/STOL/PLCN FormIDs there and InitItem
// (0x3ad2e0, vtable 0x17e1fc0 slot 19) resolves each to a reference.
constexpr std::size_t kFactionJail = 0x60;
constexpr std::size_t kFactionWait = 0x68;
constexpr std::size_t kFactionStolen = 0x70;
constexpr std::size_t kFactionInventory = 0x78;

// PlayerCharacter's vtable (1.6.1170 0x18ab9c0). Slot 186 is ServeTime
// (0x747740, id 40657; Game.ServeTime tail-calls it and nothing calls it
// directly): its first call fades out and sets kServeFadePending, its second
// passes the days and hands back the player-inventory chest. Slot 187 is
// PayCrimeGold(faction, goToJail, removeStolen) (0x747a10). kPlayerJailFaction
// is the faction whose jail holds the player.
constexpr std::uint64_t kPlayerVtable = 208040;
constexpr std::size_t kVtServeTime = 186;
constexpr std::size_t kVtPayCrimeGold = 187;
constexpr std::size_t kPlayerJailFaction = 0x720;
constexpr std::size_t kPlayerServeFlags = 0xbe5;
constexpr std::uint8_t kServeFadePending = 0x10;

// ---------------------------------------------------------------------------
// Journal stage text. Everything below was read off the journal's objective
// builder (0x98a6a0 on 1.6.1170, 0x92b5f0 on 1.6.659 -- same offsets in both).
// See: docs/commentary/tes_runtime_journal.md#journal-stage-text
// ---------------------------------------------------------------------------

// The PlayerCharacter singleton POINTER (0x31874f8), read, never called.
constexpr std::uint64_t kPlayerSingleton = 403521;

// The player's objective array: every objective ever shown, oldest first, 16
// bytes each -- BGSQuestObjective* at +0, instance id at +8, state at +0xc.
// The journal walks it newest first.
constexpr std::size_t kOffPlayerObjectives = 0x588;
constexpr std::size_t kOffPlayerObjectiveCount = 0x598;
constexpr std::size_t kShownObjectiveSize = 0x10;
constexpr std::size_t kOffShownInstance = 0x8;
constexpr std::size_t kOffShownState = 0xc;

// BGSQuestObjective: owning TESQuest* at +8, its authored index at +0x1c, and
// flags at +0x20, whose bit 0 is ORed With Previous.
constexpr std::size_t kOffObjectiveQuest = 0x8;
constexpr std::size_t kOffObjectiveIndex = 0x1c;
constexpr std::size_t kOffObjectiveFlags = 0x20;
constexpr std::uint8_t kObjectiveFlagOr = 0x1;

// TESForm's FormID at +0x14 and type at +0x1a; type 0x4d is QUST.
constexpr std::size_t kOffFormId = 0x14;
constexpr std::size_t kOffFormType = 0x1A;
constexpr std::uint8_t kFormTypeQuest = 0x4d;

// TESQuest: DNAM quest type at +0xdf (6 is Miscellaneous), and an array of
// per-instance records at +0x38 with its count at +0x48.
constexpr std::size_t kOffQuestType = 0xdf;
constexpr std::uint8_t kQuestTypeMisc = 6;
constexpr std::size_t kOffQuestInstances = 0x38;
constexpr std::size_t kOffQuestInstanceCount = 0x48;

// A per-instance record: its instance id at +0, and the CURRENT journal text
// as the stage index at +0x38 and which of that stage's log entries at +0x3a.
// The engine overwrites the pair at every stage that has a log entry.
constexpr std::size_t kOffInstanceStage = 0x38;
constexpr std::size_t kOffInstanceLogEntry = 0x3a;
constexpr std::size_t kInstanceRecordSize = 0x40;

// LookupFormByID(formId) -> TESForm* (0x1e01a0).
constexpr std::uint64_t kLookupFormById = 14617;

// The journal description builder (0x392ab0): (record, quest, BSString* out).
// Reads ONLY the record's instance id, stage and log entry, fetches that
// stage's text (0x3d1c70) and fills in alias names (0x392c80).
constexpr std::uint64_t kQuestInstanceText = 23896;

// The MenuManager singleton POINTER (0x20f6a00 on 1.6.1170), read, never
// called.
//
// 🛑 0xfa32f0 next door is the CONSTRUCTOR, not a getter: it takes placement
// memory in rcx. Calling it with no argument corrupts the manager.
// See: docs/commentary/morrowind_runtime.md#menu-registration
constexpr std::uint64_t kMenuManagerSingleton = 400327;

// MenuManager's open menus: IMenu* array at +0x110, count at +0x120 (read in
// 0xfa3e95 on 1.6.1170 and 0xf16d05 on 1.6.659). A menu's movie view is at
// IMenu+0x10.
constexpr std::size_t kOffMenuStack = 0x110;
constexpr std::size_t kOffMenuStackCount = 0x120;
constexpr std::size_t kOffMenuView = 0x10;

// GFxValue::ObjectInterface::SetMember(this, obj, name, value, isDisplayObj)
// (0xfae210) and ReleaseManaged(this, value, data) (0xfac750).
constexpr std::uint64_t kGfxSetMember = 82292;
constexpr std::uint64_t kGfxReleaseManaged = 82270;

// GFxMovieView slots, as INDICES (not byte offsets): CreateString(value,
// text), CreateObject(value, className, args, count), CreateFunction(value,
// handler, refcon), SetVariable (0x10 -> [vt+0x80]) and GetVariable (0x11 ->
// [vt+0x88]).
constexpr std::size_t kMovieViewCreateStringSlot = 0x0b;
constexpr std::size_t kMovieViewCreateObjectSlot = 0x0d;
constexpr std::size_t kMovieViewCreateFunctionSlot = 0x0f;
constexpr std::size_t kMovieViewSetVariableSlot = 0x10;
constexpr std::size_t kMovieViewGetVariableSlot = 0x11;

// GFxValue type at +8: masked, 3 is a number; bit 6 marks a value the movie
// owns and must be released.
constexpr std::uint32_t kGfxValueTypeMask = 0x8f;
constexpr std::uint32_t kGfxValueNumber = 3;
constexpr std::uint32_t kGfxValueManaged = 0x40;

// What asset_convert/ui/journal_patch.py sets on a patched movie's root, and
// where the runtime puts the object carrying its functions.
constexpr const char* kJournalPatchMarker = "_root.TESRT_Patched";
constexpr const char* kJournalFunctionsPath = "_root.TESRT_Runtime";

// DEPRECATED, to be removed: the same pair in a movie patched before the
// runtime split, when MorrowindRuntime served it.
constexpr const char* kLegacyJournalPatchMarker = "_root.MWRT_Patched";
constexpr const char* kLegacyJournalFunctionsPath = "_root.MWRT_Runtime";


// ---------------------------------------------------------------------------
// Alchemy apparatus and the crafting bench
// ---------------------------------------------------------------------------
// Every id below was read off 1.6.1170 and translated to 1.6.659, where the
// same code and offsets were confirmed.
// See: docs/commentary/tes_runtime_alchemy.md#alchemy-apparatus

// InventoryMenu's vtable (0x18f9998) and its slot 1, Accept (0x92d2a0): the
// call that registers the movie's callbacks, each through the processor's
// slot 1 as (processor, name, callback).
constexpr std::uint64_t kInventoryMenuVtable = 215494;
constexpr std::uint64_t kInventoryMenuAccept = 51850;
constexpr std::size_t kAcceptSlot = 0x8;

// The `ItemSelect` callback Accept registers (0x92d970). Its FxDelegate args
// hold the menu at +0x18; the menu holds its ItemList at +0x48.
constexpr std::uint64_t kInventoryItemSelect = 51856;
constexpr std::size_t kOffDelegateArgsHandler = 0x18;
constexpr std::size_t kOffInventoryItemList = 0x48;

// The `CloseTweenMenu` callback Accept registers (0x92d920), which the
// inventory's own full exit calls: TweenMenu stays OPEN, hidden, under an
// inventory it opened, and this posts it kMessage_Close and marks the
// inventory at +0x81. Takes the same FxDelegate args as ItemSelect.
constexpr std::uint64_t kInventoryCloseTween = 51855;

// ItemList::GetSelectedItem (0x8ef5d0): the highlighted entry, whose first
// field is the item's form.
constexpr std::uint64_t kItemListSelected = 51018;

// Actor.IsInCombat (0x9f54a0), found at its registration beside "IsInCombat".
constexpr std::uint64_t kActorIsInCombat = 54945;

// CraftingMenu's vtable (0x18f50c8) and slot 4, ProcessMessage (0x900200).
// On kMessage_Open it reads the player's furniture, switches on the bench
// type at base+0xe0, and for 5 (Alchemy) allocates 0x1a0 bytes and calls the
// AlchemyMenu constructor (0x903120) with (this+0x10 movie, furniture base).
// It stores the sub-menu at +0x30, calls 0x96fe40(0xf6), registers the
// sub-menu with the FxDelegate at +0x28 (0xfbe870) and calls its slot 2.
// Bench types 3 and 4 (Enchanting, Enchant Experiment) share one case: 0x220
// bytes, the EnchantConstructMenu constructor (0x904f90; 0x8a6ee0 on 1.6.659)
// with the same arguments, help id 0xf3.
constexpr std::uint64_t kCraftingMenuVtable = 215111;
constexpr std::uint64_t kCraftingMenuProcessMessage = 51200;
constexpr std::size_t kProcessMessageSlot = 0x20;
constexpr std::uint64_t kAlchemyMenuCtor = 51239;
constexpr std::uint64_t kEnchantMenuCtor = 51242;
constexpr std::uint64_t kCraftingHelpId = 52692;
constexpr std::uint32_t kAlchemyHelp = 0xf6;
constexpr std::uint32_t kEnchantHelp = 0xf3;
constexpr std::size_t kEnchantMenuSize = 0x220;
constexpr std::uint64_t kDelegateAddHandler = 82638;
constexpr std::size_t kAlchemyMenuSize = 0x1a0;
constexpr std::size_t kOffCraftingMovie = 0x10;
constexpr std::size_t kOffCraftingDelegate = 0x28;
constexpr std::size_t kOffCraftingSubMenu = 0x30;
constexpr std::size_t kSubMenuShowSlot = 2;

// AlchemyMenu's vtable (0x18f5858) and slot 5 (0x90d010), its user-event
// handler, handed a BSFixedString*. The name at UserEvents+0x300 -- what
// CraftButtonPress sends, and the craft key -- starts a brew (0x909a30).
// The UserEvents singleton POINTER is 0x315cc30.
constexpr std::uint64_t kAlchemyMenuVtable = 215210;
constexpr std::uint64_t kAlchemyMenuUserEvent = 51377;
constexpr std::size_t kUserEventSlot = 0x28;
constexpr std::uint64_t kUserEventsSingleton = 402638;
constexpr std::size_t kOffUserEventCraft = 0x300;

// Vanilla CraftingAlchemyWorkbench (Skyrim.esm 000BAD0C), WBDT 05 10: bench
// Alchemy, skill Alchemy. The furniture base the alchemy sub-menu is built on.
constexpr std::uint32_t kAlchemyWorkbench = 0x000BAD0C;

// Vanilla CraftingEnchantingWorkbench (Skyrim.esm 000BAD0D), WBDT 03 17: bench
// Enchanting, skill Enchanting.
constexpr std::uint32_t kEnchantingWorkbench = 0x000BAD0D;

// AlchemyMenu::ModEffectivenessFunctor's vtable (0x18f5840) and its slot 1
// (0x906e90), which sets each effect's final magnitude and duration: entry
// point 66 and the skill factor, then Effect::SetMagnitude / SetDuration.
constexpr std::uint64_t kEffectivenessVtable = 215208;
constexpr std::uint64_t kEffectivenessApply = 51289;
constexpr std::size_t kEffectivenessSlot = 0x8;

// Effect accessors (0x143310, 0x143330, 0x1433d0, 0x1433f0). The getters read
// 0 and the setters refuse when the effect has No Magnitude / No Duration;
// the setters then recompute the effect's cost.
constexpr std::uint64_t kEffectGetMagnitude = 11007;
constexpr std::uint64_t kEffectSetMagnitude = 11008;
constexpr std::uint64_t kEffectGetDuration = 11011;
constexpr std::uint64_t kEffectSetDuration = 11012;

// Effect+0x10 is its EffectSetting; that holds DATA.Flags at +0x68 and the
// float base cost at +0x6c, which the cost recompute (0x143660) reads.
constexpr std::size_t kOffEffectSetting = 0x10;
constexpr std::size_t kOffEffectFlags = 0x68;
constexpr std::size_t kOffEffectBaseCost = 0x6c;

// The registered menu names the apparatus closes and opens.
constexpr const char* kInventoryMenuName = "InventoryMenu";
constexpr const char* kCraftingMenuName = "Crafting Menu";

// Debug.Notification(string) (0xa07340), global, the callback stored beside
// "Notification": the same shape as Debug.MessageBox one id before it. What
// OpenMW's buttonless messageBox is.
constexpr std::uint64_t kDebugNotification = 55377;

// The Scaleform allocator singleton POINTER (0x3292490) and its Alloc slot,
// Alloc(this, size, 0), through which the engine allocates and FREES menus.
constexpr std::uint64_t kScaleformAllocator = 412058;
constexpr std::size_t kScaleformAllocSlot = 0x50;

constexpr const char* kSkyrimMaster = "Skyrim.esm";

// UIMessage::type sits at +8. ProcessMessage returns 0 when it consumed the
// message.
constexpr std::size_t kMessageTypeOffset = 0x8;
constexpr std::uint32_t kResultHandled = 0;

// ObjectReference.GetItemCount, found at its registration in 1.6.1170.
constexpr std::uint64_t kRefGetItemCount = 56173;

// ActorValue 16 is Alchemy (Health is 24, engine.h kActorValueHealth).
constexpr int kActorValueAlchemy = 16;

}  // namespace tesruntime::ids
