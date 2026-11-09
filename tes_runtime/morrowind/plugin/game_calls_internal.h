// What the game_calls_*.cpp files share: the resolvers every engine call goes
// through, and the few natives more than one of them needs.
// See: docs/commentary/morrowind_runtime.md#game-calls

#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "activation.h"
#include "addresses.h"
#include "dialogue_state.h"
#include "scope.h"
#include "script_tables.h"

namespace tesruntime::mw {
namespace gamecalls {

using SetValueFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                            void* name, float value);
// ObjectReference.PlaceAtMe(base, count, forcePersist, initiallyDisabled).
using PlaceAtMeFn = void* (*)(void* vm, std::uint32_t stack, void* self,
                              void* base, std::int32_t count,
                              bool forcePersist, bool initiallyDisabled);
// BSTEventSink<T>::ProcessEvent, the slot a VM sink hook replaces.
using ProcessEventFn = int (*)(void* sink, const void* event, void* source);

extern SetValueFn  g_setValue;
extern PlaceAtMeFn g_placeAtMe;
// The reference the player last talked to (SetSpeakerRef), or null once it
// is gone. 🛑 Kept by FormID, never by pointer: the speaker's cell unloads
// after the talk, and a soldier reused the freed Judas, so `talk` reached him.
void* SpeakerRef();

std::string Lower(std::string text);
// Logs an id that does not resolve, once per id.
void ReportOnce(const char* what, const std::string& id);
void* Form(const FormRef* ref);
void* ItemForm(const std::string& item);
// The reference a TES3 id names: the player, the speaker, the instance
// running the current script, or a staged placement.
void* OwnerRef(const std::string& owner);
void* PlayerRef();
void* RefByRuntimeId(std::uint32_t runtimeFormId);
// The reference a player action names: its id, or its live FormID in hex.
void* PlayerTarget(const std::string& id);
// `Actor.IsDead`; false for a null or non-actor reference.
bool IsDead(void* ref);
// Whether `ref` has its 3D now: false for a disabled reference in a loaded cell.
bool Has3D(void* ref);
// Whether `ref` is a placed door; false for null.
bool IsDoor(void* ref);
// World units between two references, or -1 when either cannot be measured.
// The string form `Distance` resolves ids; this one takes what the caller
// already holds.
float DistanceBetween(void* from, void* to);
bool StartQuest(void* form, bool* justStarted);
// Sets the stage, unless the start already ran it as a start-up stage.
bool SetStage(void* form, int stage, bool justStarted);
// `spell` cast from `source` at `target`, posted. game_calls_spell.cpp.
void CastForm(void* spell, void* source, void* target);
// The player reads `book` from their pack, on the game thread. game_calls_message.cpp.
void ReadBookFromInventory(void* book);
// Puts `ref` at a position with a Z rotation in degrees. game_calls_move.cpp.
void PlaceAt(void* ref, float x, float y, float z, float zRot);
// Puts `ref` at a spot inside `place`, a CELL or a WORLDSPACE form, in ONE
// engine move; false when `place` is neither. Game thread only.
bool MoveInto(void* ref, void* place, float x, float y, float z, float zRot);
// While the player's last MoveInto has not landed: where it lands. game_calls_move.cpp.
bool PlayerLandingSpot(void** place, float* at);
// A TES3 exterior cell's width, and the grid index a coordinate falls in.
constexpr float kTes3CellSize = 8192.0f;
int Tes3GridIndex(float v);
// The cell the player stands in, or null before a game is loaded.
void* PlayerCell();
bool PlayerInInterior();
// The name of the interior `ref` stands in; empty outdoors.
std::string InteriorName(void* ref);
// The lowercased TES3 region the player is in: its exterior grid's, or its
// quasi exterior's. Empty in an ordinary interior or outside every region.
std::string PlayerRegion();
// `ref`'s worldspace, null in an interior. game_calls_teleport.cpp.
void* WorldSpaceOf(void* ref);
// Where `ref` is as MoveInto takes it: its cell indoors, its worldspace outdoors.
void* PlaceOf(void* ref);

template <typename Fn>
Fn Native(const char* name, std::uint64_t id) {
    return reinterpret_cast<Fn>(Resolve(name, id, nullptr));
}

// Sends every reference to a spot in the named cell, `zRot` in degrees, in
// ONE engine move each, so the player's cell load cannot land between a move
// and a reposition. game_calls_move.cpp.
void SendToCell(const std::vector<void*>& refs, const std::string& cell,
                float x, float y, float z, float zRot);
// Sends every reference onto a persistent marker, taking its rotation.
void SendToMarker(const std::vector<void*>& refs, const FormRef& marker);
// The actors in a follow slot aimed at the player. game_calls_ai.cpp.
std::vector<void*> PlayerFollowers();
// The followers in the player's place within `radius`. Along: OpenMW's teleport/travel range (800).
// Party: within one exterior cell (4096), so a current quest's lagging follower still counts and a
// follower left by an old quest elsewhere does not. game_calls_move.cpp.
std::vector<void*> PlayerFollowersWithin(float radius);
std::vector<void*> PlayerFollowersAlong();
std::vector<void*> PlayerParty();
// The TES3 id the stat store keeps an actor under: "player", or its base
// NPC_'s id; "" for an actor no Morrowind record made. game_calls_attributes.cpp.
std::string ActorKey(void* ref);
// After a script moved `actor` there: an `AiWander 0` package's marker follows
// it, and a Travel or Escort goal left in another cell or out of OpenMW's range
// stops walking it, as TES3 AI never leaves the actor's cell. An authored
// package left behind in another TES3 cell (`leftCell`) idles at the new spot
// too. Game thread only.
void AiAfterScriptMove(void* actor, void* place, float x, float y, float z, bool leftCell);
// Each file resolves its own natives and supplies its own hooks.
void InstallMoveCalls(GameHooks& hooks);
void InstallAiCalls(GameHooks& hooks);
void InstallQueryCalls(GameHooks& hooks);
// The spell natives: the actor's spell list, casting, and active effects.
// See: docs/commentary/morrowind_runtime.md#spell-commands
void InstallSpellCalls(GameHooks& hooks);
// BecomeWerewolf and its kin on Skyrim's beast form. game_calls_werewolf.cpp.
void InstallWerewolfCalls(GameHooks& hooks);
// The player's factions onto the converted FACTs, and the tick's copy of the
// state barks test into their GLOBs. game_calls_state.cpp.
// See: docs/commentary/morrowind_runtime.md#published-state
void InstallStateCalls(GameHooks& hooks);
// The value of the GLOB at `formId` in `plugin`, or null. Game thread.
float* GlobalSlot(const std::string& plugin, std::uint32_t formId);
// An actor's rank in a faction, -2 when it is no member or the native is
// missing; and setting one, which joins the faction. False when it could not.
int FactionRank(void* actor, void* faction);
bool SetFactionRank(void* actor, void* faction, int rank);
void RemoveFromFaction(void* actor, void* faction);
// The import's disposition factions by effects_formid.txt row, or null:
// `disposition` and `aggression` hold ranks; `hostile` and `hostile_creature`
// are the player's enemies; `guard` marks TES3 guards. game_calls_ai.cpp.
void* DispositionForm(const char* row);
// Whether an actor is in combat. game_calls_anim.cpp.
bool ActorInCombat(void* actor);
// `fn` on each actor in the engine's high process list. Game thread.
// game_calls_still.cpp.
void ForEachHighActor(const std::function<void(void*)>& fn);
// `Actor.GetCombatTarget`, or null. game_calls_query.cpp.
void* CombatTargetOf(void* actor);
// TEMP (Vaerin hostility diag): the actor's crime faction and the player's crime gold there.
std::string TempCrimeNote(void* actor);
int TempRelationRank(void* actor, void* other);
// PC Crime Level on the engine's crime gold, and the fine and jail opcodes.
// See: docs/commentary/morrowind_runtime.md#crime-is-the-engines
void InstallCrimeCalls(GameHooks& hooks);
// StartCombat and StopCombat, with the feud that keeps allies fighting.
// See: docs/commentary/morrowind_runtime.md#startcombat-between-allies
void InstallCombatCalls(GameHooks& hooks);
// ChangeWeather and ModRegion, and the TES3 type of the sky's weather.
// See: docs/commentary/morrowind_runtime.md#region-weather
void InstallWeatherCalls(GameHooks& hooks);
void PublishState();
// Mark, Recall and the two Interventions, on the VM's OnMagicEffectApply sink.
// See: docs/commentary/morrowind_runtime.md#teleport-effects
void InstallTeleportCalls();
// The TES3 effect index and attribute of a runtime-carried MGEF (or delivery
// copy); index -1 for any other.
RuntimeEffect RuntimeEffectOf(std::uint32_t effectId);
// The TES3 indices of the duration effects the runtime carries.
constexpr int kSwiftSwimEffect = 1;
constexpr int kLevitateEffect = 10;
constexpr int kSlowFallEffect = 11;
constexpr int kSanctuaryEffect = 42;
// The cures, which Skyrim's own CureEffect cannot sort by class.
constexpr int kCureCommon = 69;
constexpr int kCureBlight = 70;
constexpr int kCureCorprus = 71;
constexpr int kRemoveCurse = 100;
constexpr int kCorprusEffect = 132;
bool IsCure(int index);
// Purges what the cure `index` takes off `ref`. game_calls_spell.cpp.
// See: docs/commentary/morrowind_runtime.md#cures-purge-by-class
void ApplyCure(void* ref, int index);
// Levitate and SlowFall on the InAir and OnGround states' simulate, SwiftSwim
// on the Swimming state's, and Sanctuary's faction rank, all read from the
// active-effect list.
// See: docs/commentary/morrowind_runtime.md#levitate-and-slowfall
void InstallFlightCalls(GameHooks& hooks);
// An actor's character controller, or null. game_calls_flight.cpp.
void* CharController(void* actor);
// The PlayerControls singleton, or null. game_calls_flight.cpp.
void* PlayerControls();
// OpenMW keeps an actor no AI is moving where it stands: the InAir and
// OnGround steps hold each still non-player controller, and the tick names
// the nearby actors that are not Morrowind's. game_calls_still.cpp.
void InstallStillCalls();
void HoldStill(void* controller, bool isPlayer);
void TickStillActors(void* player);
// The summed magnitude of every active instance of a runtime-carried effect
// on `actor`, read off its active-effect list; with `attribute` set, only the
// instances of that attribute's variant. Game thread.
float ActiveMagnitude(void* actor, int tes3Index, int attribute = -1);
// Each active, runtime-carried effect instance on `actor` with its magnitude:
// one walk of the list for a caller that needs many sums. Game thread.
void ForEachActiveEffect(void* actor,
                         const std::function<void(const RuntimeEffect&, float)>& fn);
// Whether a live active effect on `actor` came from a spell whose FormID
// `match` accepts. Game thread. game_calls_flight.cpp.
bool AnyEffectSource(void* actor,
                     const std::function<bool(std::uint32_t)>& match);
// Morrowind's attribute effects on the player, and the skill cap on its
// skill use and trainers. game_calls_attributes.cpp.
// See: docs/commentary/morrowind_runtime.md#attribute-effects
void TickAttributeEffects(void* player);
void InstallAttributeCalls(GameHooks& hooks);
// Game thread: an attribute effect just landed on `actorId`, cast by
// `casterId` (0 for none), so the tick sums it -- and pays an Absorb's
// magnitude to its caster for as long as the target carries it.
void WatchAttributes(std::uint32_t actorId, std::uint32_t casterId, const RuntimeEffect& row);
// Game thread: `actorId` just had a Sanctuary applied, so the tick ranks it.
void WatchSanctuary(std::uint32_t actorId);
// Whether `ref` is a placed actor: an id saved in one session may name
// something else in the next.
bool IsActorRef(void* ref);
// The weapon `actor` holds in that hand, or null. game_calls_query.cpp.
void* EquippedWeapon(void* actor, bool left);
// Brawls: the ability a brawler gets when it loads, and the Stamina a player's
// punch takes from it. Game thread. game_calls_brawl.cpp.
// See: docs/commentary/morrowind_runtime.md#brawls
void InstallBrawlCalls(GameHooks& hooks);
void TireBrawler(std::uint32_t target);
// A player's hit with anything but fists: the brawl becomes a real fight.
void BreakBrawl(std::uint32_t target);
// Skyrim.esm's Unarmed WEAP, the source a bare-fisted hit reports.
constexpr std::uint32_t kUnarmedWeapon = 0x000001F4;
// Keeps the player's and each watched actor's Sanctuary faction rank at its
// summed Sanctuary. game_calls_sanctuary.cpp.
void TickSanctuary(void* player);
// Game thread: notes which actors our follow slots have following the player,
// for the Follow procedure's spacing hook, and resumes a paused follow once the
// player is back near that follower. game_calls_ai.cpp.
void TickPlayerFollowers();
// Game thread, BEFORE a script move puts the player at (x, y, z) in `place`:
// every follower not already near there pauses its follow and stays put.
void LeaveFollowersBehind(void* place, float x, float y, float z);
// `PlayGroup` / `LoopGroup`: the object animation queue on the mesh's own
// sequences, and the tick that advances it. game_calls_anim.cpp.
void InstallAnimCalls(GameHooks& hooks);
// The seven player-control switches and Game.ShowRaceMenu.
// See: docs/commentary/morrowind_runtime.md#the-control-switches
void InstallControlCalls(GameHooks& hooks);
// The engine's buttoned message box, which Debug.MessageBox cannot raise.
// Hooked through ShowMessage rather than a hook of its own. Also the engine's
// Bink player, which `PlayBink` reaches through its hook.
// See: docs/commentary/morrowind_runtime.md#messagebox-buttons
void InstallMessageCalls(GameHooks& hooks);
void ShowButtonMessage(const std::string& text,
                       const std::vector<std::string>& buttons);
// Skyrim's corner notification, which is what OpenMW's buttonless message box
// is: the line a refused action shows. Nothing for empty text.
void Notify(const std::string& text);

}  // namespace gamecalls
}  // namespace tesruntime::mw
