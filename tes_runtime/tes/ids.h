// Address Library stable IDs and engine layouts for everything TESRuntime
// touches: jails and journal stage text. The shared engine services' ids are in
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

}  // namespace tesruntime::ids
