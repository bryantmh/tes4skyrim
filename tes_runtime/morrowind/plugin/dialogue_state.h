// The Morrowind state Skyrim has no place for: journal indices, disposition,
// script variables, the player's factions, reputation, crime -- and what the
// conversation in progress has been told to do next.
//
// Result scripts WRITE it and the dialogue filter READS it, which is what
// makes picking the right topic change what the next one says. It is the
// co-save's whole payload: Serialize() is what gets written into the save.
// See: docs/commentary/morrowind_runtime.md#dialogue-state

#pragma once

#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace tesruntime::mw {

struct TravelDest;

// A running global script as the tick runs it: its state key, its bare name,
// the target its bare commands act on, and the layer it runs as (scope.h).
struct RunningGlobal {
    std::string key;
    std::string script;
    std::string target;
    int layer = -1;
};

// One line a `Choice` command offers, and the index it answers with.
struct ChoiceLine {
    std::string text;
    int index = 0;
};

// The player's standing in one faction.
struct Membership {
    int  rank = -1;          // -1: not a member
    bool expelled = false;
    int  reputation = 0;
};

// The forced movement latches TES3 keeps on an actor. Only sneak is here:
// Skyrim can force no gait, so Run/Jump/MoveJump are `kDeliberateNoOps`.
// See: docs/commentary/morrowind_runtime.md#forced-movement-is-a-latch
enum MovementFlag { kForceSneak };

// The player-control switches Skyrim has a matching flag for, numbered as the
// compiler numbers them: `opcodeEnable + i` walks its `controls[]` table, so
// the value IS the opcode offset. Jumping (2), magic (4) and vanity mode (6)
// have no Skyrim flag and are not ported.
// See: docs/commentary/morrowind_runtime.md#the-control-switches
enum ControlSwitch {
    kPlayerControls = 0,
    kPlayerFighting = 1,
    kPlayerLooking = 3,
    kPlayerViewSwitch = 5,
    kControlSwitchCount = 7
};

// Why a reference's 3D did not report itself loaded. Only `kUnloaded` says
// the object left the world; the other two say the question could not be
// answered, which is NOT grounds to unbind a running script.
// See: docs/commentary/morrowind_runtime.md#instances-bind-from-the-world
enum class LoadState {
    kLoaded,
    kUnloaded,
    // `Game.GetForm` did not answer for the FormID this tick.
    kUnresolved,
    // The native was never resolved, so nothing was asked at all.
    kUnknown,
};

// What the game itself has to do when the state changes. Null in the
// headless tests, where only the state is checked.
struct GameHooks {
    void (*setQuestStage)(const std::string& quest, int stage) = nullptr;
    void (*addItem)(const std::string& owner, const std::string& item,
                    int count) = nullptr;
    void (*removeItem)(const std::string& owner, const std::string& item,
                       int count) = nullptr;
    int  (*itemCount)(const std::string& owner,
                      const std::string& item) = nullptr;
    // `attacker` attacks `target`; an empty `target` stops the fight instead.
    void (*setCombat)(const std::string& attacker,
                      const std::string& target) = nullptr;
    void (*setEnabled)(const std::string& ref, bool enabled) = nullptr;
    bool (*isDisabled)(const std::string& ref) = nullptr;
    // The player activates `ref`: as a script's `Activate`, past an OnActivate
    // claim, or as a click, which a claim holds (a headless run's `talk`).
    void (*activate)(const std::string& ref) = nullptr;
    // False, doing nothing, while `ref` has no 3D or its script has not run
    // since it loaded: nobody clicks what is not there yet.
    bool (*playerActivate)(const std::string& ref) = nullptr;
    // The player picks `ref` up into the inventory (a headless run's `take`).
    void (*playerTake)(const std::string& ref) = nullptr;
    // `ShowRestMenu`: sleep in `bed` as in a Skyrim bed; empty rests in place.
    void (*showRestMenu)(const std::string& bed) = nullptr;
    // Locks at `level`; a negative level unlocks.
    void (*setLocked)(const std::string& ref, int level) = nullptr;
    bool (*isLocked)(const std::string& ref) = nullptr;
    void (*deleteRef)(const std::string& ref) = nullptr;
    float (*distance)(const std::string& from, const std::string& to) = nullptr;
    // `which`: 0 health, 1 magicka, 2 fatigue -- OpenMW's dynamic order.
    float (*dynamicStat)(const std::string& actor, int which) = nullptr;
    void (*setDynamicStat)(const std::string& actor, int which,
                           float value) = nullptr;
    // With `base` the maximum moves too (`ModHealth`); without, only the
    // current value (`ModCurrentHealth`).
    void (*modDynamicStat)(const std::string& actor, int which, float delta,
                           bool base) = nullptr;
    void (*equipItem)(const std::string& actor, const std::string& item) = nullptr;
    // The player's current cell as the game NAMES it, or "". An unnamed
    // exterior gives "" while a game is perfectly well loaded, so this answers
    // CellChanged and never "is a game running" -- that is `playerInWorld`.
    std::string (*playerCell)() = nullptr;
    // Whether a game is loaded at all: the player is in some cell, named or
    // not. What the object tick gates on.
    bool (*playerInWorld)() = nullptr;
    bool (*playerInInterior)() = nullptr;
    // Whether the ENGINE has the game paused by a menu OTHER than our own
    // dialogue window, which pauses too but must not stop the tick.
    // See: docs/commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused
    bool (*gamePaused)() = nullptr;
    bool (*menuMode)() = nullptr;
    void (*forceGreeting)(const std::string& actor) = nullptr;
    // A `MessageBox` raised OUTSIDE a conversation, which has no dialogue
    // menu to render it: Skyrim's own message box. With buttons it is the
    // modal the player clicks, and the click lands in `buttonPressed`.
    // See: docs/commentary/morrowind_runtime.md#messagebox-buttons
    void (*showMessage)(const std::string& text,
                        const std::vector<std::string>& buttons) = nullptr;
    // `PlaceAtPC id count` and `id->PlaceAtMe ...`: creates `count` of a base
    // beside `near` -- the player for the PC form, the named reference for the
    // other. The new reference runs the base's script, so this also binds its
    // instance.
    void (*placeNear)(const std::string& near, const std::string& base,
                      int count) = nullptr;
    // Whether the reference with this RUNTIME FormID is a DEAD actor, which
    // the tick polls to raise `OnDeath`. False for anything that is not one.
    //
    // 🛑 The RUNTIME id, not a staged one: a reference created by `PlaceAtPC`
    // has no staged placement at all, and it is exactly the spawned creature
    // whose death a quest turns on.
    bool (*isDead)(std::uint32_t runtimeFormId) = nullptr;
    // Whether that reference's 3D is LOADED, which is the whole of TES3's rule
    // for when a local script runs.
    bool (*is3DLoaded)(std::uint32_t runtimeFormId) = nullptr;
    // The same question, keeping the reason a `false` came back: only
    // `kUnloaded` is an unload, and the tick must not drop an instance over
    // `kUnresolved` or `kUnknown`.
    LoadState (*load3DState)(std::uint32_t runtimeFormId) = nullptr;
    // The RUNTIME FormID of the staged placement `(plugin, local)` once its 3D
    // is loaded, or 0. This is how an instance the player never activates ever
    // starts ticking -- the Activate hook used to be the only binding path.
    // See: docs/commentary/morrowind_runtime.md#instances-bind-from-the-world
    std::uint32_t (*loadedRef)(const std::string& plugin,
                               std::uint32_t localFormId) = nullptr;
    // `axis`: 0 x, 1 y, 2 z. Position is world units, angle is DEGREES --
    // the engine stores radians and its getters convert, so these do not.
    float (*position)(const std::string& ref, int axis) = nullptr;
    void (*setPosition)(const std::string& ref, int axis, float value) = nullptr;
    float (*angle)(const std::string& ref, int axis) = nullptr;
    void (*setAngle)(const std::string& ref, int axis, float value) = nullptr;
    // What persuasion and barter read and do in the game: the player's
    // level, a Skyrim actor value by name, that value as a fraction of its
    // maximum, a skill-use credit, and Skyrim's own barter and training menus
    // on `actor`.
    int   (*playerLevel)() = nullptr;
    float (*actorValue)(const std::string& actor, const char* name) = nullptr;
    float (*statPercent)(const std::string& actor, const char* name) = nullptr;
    // The BASE of that value, which a skill increase, a trainer or a book
    // moves and a fortify never does.
    float (*baseActorValue)(const std::string& actor, const char* name) = nullptr;
    // The actor's race by runtime FormID (0 unknown): a change of race moves
    // the skills for a reason that is not an increase, and sets the player's
    // starting attributes. `female` is the actor's base record's sex.
    // See: docs/commentary/morrowind_runtime.md#race-attributes
    std::uint32_t (*race)(const std::string& actor) = nullptr;
    bool (*female)(const std::string& actor) = nullptr;
    // That race's name, "" when unknown.
    std::string (*raceName)(const std::string& actor) = nullptr;
    // The stage of the quest at `formId` in `plugin`, -1 when it does not
    // resolve; the crime gold on the faction there, false when it does not.
    // See: docs/commentary/morrowind_runtime.md#statistics-tab
    int (*questStageOf)(const std::string& plugin, std::uint32_t formId) = nullptr;
    bool (*factionCrimeGold)(const std::string& plugin, std::uint32_t formId,
                             float* out) = nullptr;
    // How far the player's Skyrim skill (by actor value name) is toward its
    // next point, 0..1; negative when it cannot be read.
    // See: docs/commentary/morrowind_runtime.md#skill-tooltips
    float (*skillProgress)(const char* skill) = nullptr;
    // The player's experience toward Skyrim's next level and what it takes;
    // false when it cannot be read.
    bool (*levelProgress)(float* points, float* most) = nullptr;
    // `EnableLevelUpMenu`: fills that experience to what the next level takes.
    void (*fillLevelProgress)() = nullptr;
    // Settles every TES4 plugin's player attribute globals with the sheet's
    // store: what a script wrote moves the base, then the attribute is
    // written back. Game thread.
    // See: docs/commentary/morrowind_runtime.md#tes4-tables
    void (*syncAttributeGlobals)() = nullptr;
    // The value of the GLOB at `formId` in `plugin`, read and written in
    // place; null when it does not resolve. Game thread.
    float* (*globalSlot)(const std::string& plugin, std::uint32_t formId) = nullptr;
    // `EnableClassMenu` (1) / `EnableBirthMenu` (2): the runtime's own menu.
    // See: docs/commentary/morrowind_runtime.md#chargen-menus
    void (*showChargenMenu)(int kind) = nullptr;
    void  (*advanceSkill)(const char* skill, float amount) = nullptr;
    void  (*showBarterMenu)(const std::string& actor) = nullptr;
    void  (*showTrainingMenu)(const std::string& actor) = nullptr;
    // Skyrim's gold (form 0xF), never Morrowind's: what `actor` carries, and
    // a bribe moving `count` of it from one actor to another.
    int   (*goldCount)(const std::string& actor) = nullptr;
    void  (*moveGold)(const std::string& from, const std::string& to,
                      int count) = nullptr;
    // `sound` is a TES3 SOUN id, resolved through SOUN.txt to the SNDR the
    // import minted. `ref` is what it plays from, "" for the player -- TES3's
    // non-3D PlaySound is the same call on the listener.
    //
    // Playing RETURNS Skyrim's playback instance id, which is the only handle
    // StopSound and GetSoundPlaying have; 0 means it did not start.
    int   (*playSound)(const std::string& ref, const std::string& sound,
                       bool loop, float volume) = nullptr;
    void  (*stopSound)(int instance) = nullptr;
    // `Say ref file text`: a VOICE line, named by its path under Sound\ rather
    // than by a SOUN id, so it carries a lip track and moves the actor's mouth.
    // True when it started; `sayDone` reports whether it has finished.
    bool  (*say)(const std::string& ref, const std::string& file,
                 const std::string& text) = nullptr;
    bool  (*sayDone)(const std::string& ref) = nullptr;
    // `PlayBink file allowSkipping`: the engine's own full-screen Bink player
    // on a file under Data\Video, skippable with Escape when `allowSkipping`.
    void  (*playBink)(const std::string& file, bool allowSkipping) = nullptr;
    // A headless run's click on button `button` of the open MessageBox.
    void  (*pressButton)(int button) = nullptr;
    // A headless run's view of the engine's menus: whether the menu registered
    // as `name` is open, and the close a player's Done or Escape sends it.
    bool  (*menuOpen)(const std::string& name) = nullptr;
    void  (*closeMenu)(const std::string& name) = nullptr;
    // A headless run's pick in the runtime's open menu: a class or birthsign
    // row named `name`, then OK; or a level-up attribute, or its OK. False
    // when none is open or nothing there has the name.
    bool  (*menuPick)(const std::string& name) = nullptr;
    // A headless run's sleep in the open Sleep/Wait menu: the hours slider
    // set to `hours`, then its Sleep button.
    void  (*sleepFor)(int hours) = nullptr;
    // The key the player bound to Skyrim's user event `userEvent`, by name
    // (menu.h BoundKeyName), for `^ActionForward` and the rest in text.
    std::string (*boundKeyName)(const char* userEvent) = nullptr;
    // `PositionCell`: moves `ref` into the cell of that name and puts it at
    // (x, y, z) with a Z rotation of `zRot` DEGREES.
    void (*moveToCell)(const std::string& ref, const std::string& cell,
                       float x, float y, float z, float zRot) = nullptr;
    // `Position`: the same, staying in the cell the reference is already in.
    void (*moveInCell)(const std::string& ref, float x, float y, float z,
                       float zRot) = nullptr;
    // `Move`/`MoveWorld`/`Rotate`/`RotateWorld`: adds a DELTA to one axis.
    // `local` rotates the offset into the object's own frame, which is what
    // separates `Move` from `MoveWorld`.
    void (*moveBy)(const std::string& ref, int axis, float delta,
                   bool local) = nullptr;
    void (*rotateBy)(const std::string& ref, int axis, float degrees) = nullptr;
    // `SetScale` / `GetScale`: Skyrim's own scale, which is TES3's too.
    float (*scale)(const std::string& ref) = nullptr;
    void (*setScale)(const std::string& ref, float value) = nullptr;
    // `PlaceItem`/`PlaceItemCell`: a base record placed at an absolute spot,
    // and `PlaceAtMe`'s count form used for `PlaceAtPC`'s sibling.
    void (*placeAtCell)(const std::string& base, const std::string& cell,
                        float x, float y, float z, float zRot) = nullptr;
    // The AI commands. Each fills the QUEST ALIAS that the import hung a real
    // PACK record off, so the engine runs an actual package.
    // See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
    void (*aiTravel)(const std::string& actor, float x, float y,
                     float z) = nullptr;
    void (*aiWander)(const std::string& actor, float range,
                     float duration) = nullptr;
    // `target` is who to follow; an empty one CLEARS the alias, which ends it.
    void (*aiFollow)(const std::string& actor, const std::string& target,
                     float duration, float x, float y, float z) = nullptr;
    void (*aiEscort)(const std::string& actor, const std::string& target,
                     float duration, float x, float y, float z) = nullptr;
    void (*aiActivate)(const std::string& actor,
                       const std::string& object) = nullptr;
    // `Face x y`: turns the actor toward a world point.
    void (*aiFace)(const std::string& actor, float x, float y) = nullptr;
    // `GetCurrentAiPackage`: OpenMW's AiPackageTypeId of the package the actor
    // is running, or -1 -- read off the engine, not from our own bookkeeping.
    int  (*currentPackage)(const std::string& actor) = nullptr;
    // `GetAiPackageDone`: whether the actor has finished the package a script
    // gave it, i.e. it no longer runs one of ours.
    bool (*packageDone)(const std::string& actor) = nullptr;
    // The one-call world QUERIES: each is a single Skyrim native on a
    // reference, or on the player when the command names nobody.
    // See: docs/commentary/morrowind_runtime.md#the-query-commands
    //
    // `GetLOS`, `GetDetected` and `GetTarget` all take a SECOND actor: does
    // `actor` see / notice / fight `other`.
    bool (*hasLos)(const std::string& actor, const std::string& other) = nullptr;
    bool (*detects)(const std::string& actor, const std::string& other) = nullptr;
    bool (*fighting)(const std::string& actor,
                     const std::string& other) = nullptr;
    // Draw state and movement, all on one actor.
    bool (*weaponDrawn)(const std::string& actor) = nullptr;
    bool (*sneaking)(const std::string& actor) = nullptr;
    bool (*running)(const std::string& actor) = nullptr;
    // `GetSpellReadied`: TES3's third draw state, magic, which Skyrim reports
    // as the equipped right-hand item being a spell rather than a weapon.
    bool (*spellReadied)(const std::string& actor) = nullptr;
    // `HasItemEquipped`: is that item in any of the actor's equip slots.
    bool (*itemEquipped)(const std::string& actor,
                         const std::string& item) = nullptr;
    // `HitOnMe "id"`: whether the actor's last hit came from that weapon or
    // spell, consuming it when it did.
    bool (*hitOnMe)(const std::string& actor, const std::string& item) = nullptr;
    // `ForceSneak`/`ClearForceSneak`: puts the actor into the movement state
    // the latch now holds. `which` is a `MovementFlag`.
    void (*applyMovementFlag)(const std::string& actor, int which,
                              bool on) = nullptr;
    // Pushes the whole control-switch set to the engine, which takes them as
    // one call rather than one flag at a time. `on[i]` is a `ControlSwitch`.
    void (*applyControlSwitches)(const bool* on, int count) = nullptr;
    // `EnableRaceMenu`: Skyrim's own race/sex menu, the one TES3 command with
    // a real equivalent. The other chargen menus have none.
    void (*showRaceMenu)() = nullptr;
    void (*resurrect)(const std::string& actor) = nullptr;
    // `Drop item count`: puts them on the ground at the actor's feet.
    void (*dropItem)(const std::string& actor, const std::string& item,
                     int count) = nullptr;
    // `GetCurrentWeather`: Skyrim's weather CLASSIFICATION, 0..4.
    int  (*weather)() = nullptr;
    // Writes one Skyrim actor value by name, and any actor's level.
    void (*setActorValue)(const std::string& actor, const char* name,
                          float value) = nullptr;
    // Moves one actor value's permanent modifier: a magic-effect stat's write.
    void (*modActorValue)(const std::string& actor, const char* name,
                          float delta) = nullptr;
    int  (*level)(const std::string& actor) = nullptr;
    // How far active magic moves one of an actor's attributes, by TES3 id
    // and index: every Fortify and Absorb it cast, less every Drain, Absorb
    // and accrued Damage on it.
    // See: docs/commentary/morrowind_runtime.md#attribute-effects
    float (*attributeEffect)(const std::string& actor, int tes3Index) = nullptr;
    // The travel service. `followerCount` is how many actors travel WITH
    // the player, which multiplies the fare; `advanceHours` moves the game
    // clock on; `travelTo` puts the player and those followers at the
    // destination.
    // See: docs/commentary/morrowind_runtime.md#travel
    int  (*followerCount)() = nullptr;
    void (*advanceHours)(int hours) = nullptr;
    void (*travelTo)(const TravelDest& dest) = nullptr;
    // `GetDeadCount id`: how many of that base actor have died, which the
    // ENGINE counts and saves for every actor, scripted or not.
    int  (*deadCount)(const std::string& actor) = nullptr;
    // Copies the game's clock into GameHour, Day, Month, Year, DaysPassed and
    // TimeScale, which TES3 scripts and dialogue read as plain globals.
    void (*syncClock)() = nullptr;
    // A script's write to one of those clock globals, passed on to the game's
    // own clock; otherwise the next syncClock copies the old time back over it.
    void (*setClockGlobal)(const std::string& name, float value) = nullptr;
    // The four AI settings, pushed onto the actor: `which` is kAiFight..kAiFlee
    // and `value` is TES3's 0..100.
    void (*applyAiSetting)(const std::string& actor, int which,
                           int value) = nullptr;
    // The player's rank (-1: not a member) and expulsion in one TES3 faction,
    // pushed onto the converted FACT so conditions can read them.
    // See: docs/commentary/morrowind_runtime.md#player-factions
    void (*applyPlayerFaction)(const std::string& faction, int rank,
                               bool expelled) = nullptr;
    // The player's bounty: the crime gold the engine keeps on the speaker's
    // crime faction, else the realm's. `crimeGold` answers false when no
    // faction resolves, and the DialogueState copy stands in.
    // See: docs/commentary/morrowind_runtime.md#crime-is-the-engines
    bool (*crimeGold)(float* out) = nullptr;
    void (*setCrimeGold)(float gold) = nullptr;
    // PayFine (confiscates stolen goods) and PayFineThief (does not); both
    // clear the bounty. GoToJail is the engine's own SendPlayerToJail.
    void (*payFine)(bool confiscate) = nullptr;
    void (*goToJail)() = nullptr;
    // Polled by the tick, on the main thread: when Skyrim's own dialogue menu
    // has opened on a Morrowind speaker -- a guard force-greeting to arrest --
    // closes it and opens the Morrowind conversation; once the engine has
    // jailed the player for GoToJail, serves the sentence.
    void (*crimeTick)() = nullptr;
    // Polled by the tick, on the main thread: ends the feuds StartCombat set
    // up once either side is dead.
    void (*combatTick)() = nullptr;
    // The engine frame timer's millisecond clock, which advances with GAME
    // time (each frame's clamped delta); 0 when unresolved.
    std::uint32_t (*frameClockMs)() = nullptr;
    // Once a tick of unpaused play: re-reads the runtime-carried duration
    // effects -- Levitate, SlowFall for the physics hook, Sanctuary's ranks.
    void (*effectTick)() = nullptr;
    // The spell commands. `spell` is a TES3 SPEL id, resolved through SPEL.txt
    // to the SPEL the import minted. AddSpell/RemoveSpell put it on the actor's
    // spell list; HasSpell is what `GetSpell` answers.
    // See: docs/commentary/morrowind_runtime.md#spell-commands
    void (*addSpell)(const std::string& actor, const std::string& spell) = nullptr;
    void (*removeSpell)(const std::string& actor,
                        const std::string& spell) = nullptr;
    bool (*hasSpell)(const std::string& actor,
                     const std::string& spell) = nullptr;
    // `Cast`/`ExplodeSpell`: `caster` casts at `target`; ExplodeSpell casts a
    // spell on the target from itself, so both go through one call.
    void (*castSpell)(const std::string& caster, const std::string& target,
                      const std::string& spell) = nullptr;
    // `RemoveSpellEffects`: dispels just that spell's effects.
    void (*dispelSpell)(const std::string& actor,
                        const std::string& spell) = nullptr;
    // `RemoveEffects`: removes every spell affecting the actor that CONTAINS
    // that TES3 effect index, which the staged effect lists identify.
    // See: docs/commentary/morrowind_runtime.md#removeeffects-is-per-spell
    void (*dispelByEffect)(const std::string& actor,
                           int effectIndex) = nullptr;
    // `GetEffect`: is that one magic effect active on the actor now. `effect`
    // is the name MGEF.txt keys, already stripped of its `sEffect` prefix.
    bool (*hasEffect)(const std::string& actor,
                      const std::string& effect) = nullptr;
    // `GetSpellEffects`: is the actor under the effect of that SPELL, answered
    // through the effects the spell carries.
    bool (*spellActive)(const std::string& actor,
                        const std::string& spell) = nullptr;
    // The soul gem commands. Each names the CREATURE whose soul is trapped,
    // never the gem tier; `gem` is the container `AddSoulGem` fills.
    // `soulGemCount` answers `HasSoulGem`, and `takeSoulGem` removes one --
    // dropping it on the ground instead when `drop`.
    // See: docs/commentary/morrowind_runtime.md#soul-gems
    void (*addSoulGem)(const std::string& actor, const std::string& creature,
                       const std::string& gem) = nullptr;
    int  (*soulGemCount)(const std::string& actor,
                         const std::string& creature) = nullptr;
    void (*takeSoulGem)(const std::string& actor, const std::string& creature,
                        bool drop) = nullptr;
    // `PlayGroup` / `LoopGroup`: queues `group` on the object's own animation,
    // OpenMW's playGroup. `mode` is TES3's 0..2; `loops` is how many times a
    // looping group repeats, kLoopForever for PlayGroup.
    void (*playGroup)(const std::string& ref, const std::string& group, int mode,
                      std::uint32_t loops) = nullptr;
    // `SkipAnim`: holds the object's ambient animation still this tick.
    void (*skipAnim)(const std::string& ref) = nullptr;
    // Once a tick of unpaused play: advances every queue `playGroup` filled.
    void (*animTick)() = nullptr;
    // What the NPC animation driver (npc_anim.cpp) needs of ONE reference, by
    // RUNTIME FormID: a base NPC is placed many times and each copy plays its
    // own idle, so the TES3 id alone cannot name which.
    // See: docs/commentary/morrowind_runtime.md#npc-animation-files
    //
    // The runtime FormID of the reference a TES3 id names, or 0.
    std::uint32_t (*refRuntimeId)(const std::string& ref) = nullptr;
    // Sends a behaviour-graph event; false when no graph took it, which is
    // what an actor whose 3D is still loading answers.
    bool (*sendAnimEvent)(std::uint32_t runtimeFormId,
                          const std::string& event) = nullptr;
    // The reference's x, y, z and its three angles in degrees; false when it
    // could not be read.
    bool (*refPose)(std::uint32_t runtimeFormId, float out[6]) = nullptr;
    // Starts a TranslateTo glide to that pose, arriving `seconds` from now.
    // While it runs the reference is moved by nothing else.
    void (*glideRef)(std::uint32_t runtimeFormId, const float pose[6],
                     float seconds) = nullptr;
    bool (*inCombat)(std::uint32_t runtimeFormId) = nullptr;
};

// The loop count `PlayGroup` passes: a looping group repeats until replaced.
constexpr std::uint32_t kLoopForever = 0xffffffffu;

GameHooks& Hooks();

class DialogueState {
public:
    // --- journal ---------------------------------------------------------
    int  JournalIndex(const std::string& quest) const;
    void SetJournalIndex(const std::string& quest, int index);
    // `Journal quest index`: records the entry once, raises the quest's
    // index only when `index` is higher. True when the entry is new.
    bool AddJournalEntry(const std::string& quest, int index);

    // --- disposition, 0..100: the NPC's authored value until moved ---------
    int  Disposition(const std::string& actor) const;
    // Also re-applies Fight: whether an actor attacks on sight is a function
    // of BOTH, so either one moving has to re-run it.
    void SetDisposition(const std::string& actor, int value);
    void ModDisposition(const std::string& actor, int delta);

    // --- an NPC's OWN rank: its authored rank until RaiseRank moves it -----
    // Keyed by actor, not by faction: TES3 promotes within the NPC's PRIMARY
    // faction, which is the only one an NPC record carries.
    // See: docs/commentary/morrowind_runtime.md#npc-rank
    int  ActorRank(const std::string& actor) const;
    void SetActorRank(const std::string& actor, int rank);

    // --- script globals: a GLOB record's value until a script sets it ------
    bool  HasGlobal(const std::string& name) const;
    float Global(const std::string& name) const;
    void  SetGlobal(const std::string& name, float value);
    // The same write, unlogged: for a value the GAME owns and refreshes every
    // tick, such as the clock.
    void  SyncGlobal(const std::string& name, float value);
    std::vector<std::string> Globals() const;

    // --- script locals, by owner: an actor's id or a global script's -------
    bool  HasVar(const std::string& owner, const std::string& name) const;
    float Var(const std::string& owner, const std::string& name) const;
    void  SetVar(const std::string& owner, const std::string& name,
                 float value);
    // Every variable name `owner` holds, lowercase, in order.
    std::vector<std::string> VarNames(const std::string& owner) const;

    // --- the topics the player has heard of -----------------------------------
    // DialogueManager::mKnownTopics: a topic is LISTED only when the speaker
    // can answer it AND it is here. It starts empty and grows from keywords
    // in replies and from AddTopic.
    bool KnowsTopic(const std::string& topic) const;
    void LearnTopic(const std::string& topic);
    std::size_t KnownTopicCount() const { return mKnownTopics.size(); }

    // --- the player's factions ---------------------------------------------
    const Membership& Faction(const std::string& faction) const;
    // Every faction the player has a row for, member or not, by its id.
    const std::map<std::string, Membership>& Factions() const {
        return mFactions;
    }
    // PCJoinFaction: rank 0 unless already a member.
    void JoinFaction(const std::string& faction);
    // PCRaiseRank joins at rank 0 first; PCLowerRank leaves below rank 0.
    void ChangeRank(const std::string& faction, int delta);
    void SetExpelled(const std::string& faction, bool expelled);
    void SetFactionReputation(const std::string& faction, int value);

    // --- the four AI settings, 0..100, per actor ---------------------------
    // Fight, Hello, Alarm and Flee. Morrowind stores them on the actor and
    // dialogue both sets and tests them; Skyrim has no equivalent field, so
    // the DLL owns them the way it owns disposition.
    int  AiSetting(const std::string& actor, int which) const;
    void SetAiSetting(const std::string& actor, int which, int value);

    // --- the forced sneak latch, per actor ---------------------------------
    // TES3 stores it ON the actor: ForceSneak sets, ClearForceSneak clears,
    // GetForceSneak reads it back, and the stance is that flag OR the AI's
    // own. The DLL owns it the way it owns the AI settings. `which` is a
    // `MovementFlag`, declared beside GameHooks so the OP that writes it and
    // the CALL that applies it cannot disagree about the number.
    bool MovementFlag(const std::string& actor, int which) const;
    void SetMovementFlag(const std::string& actor, int which, bool on);

    // --- the player-control switches ---------------------------------------
    // Seven booleans the whole game shares, all true until a script says
    // otherwise. `which` is a `ControlSwitch`. Writing one pushes the whole
    // set to the engine, because Skyrim takes them in one call.
    bool ControlEnabled(int which) const;
    void SetControlEnabled(int which, bool on);

    // --- Mark, and whether teleport magic works -----------------------------
    // Where Mark last stood: the CELL of an interior or the WORLDSPACE of an
    // exterior as a runtime FormID (0 when never marked), and the spot in it
    // with its Z rotation in degrees. The co-save re-resolves the FormID
    // after a load-order change.
    struct MarkedPlace {
        std::uint32_t place = 0;
        float x = 0.0f, y = 0.0f, z = 0.0f, zRot = 0.0f;
    };
    MarkedPlace mark;
    // `DisableTeleporting` / `EnableTeleporting`: gates Mark, Recall and both
    // Interventions, as OpenMW's isTeleportingEnabled does.
    bool teleporting = true;
    // `DisableLevitation` / `EnableLevitation`: gates Levitate, as OpenMW's
    // isLevitationEnabled does.
    bool levitation = true;
    // Runtime FormIDs of the actors a Sanctuary has landed on whose faction
    // rank the tick still keeps; each leaves once its Sanctuary is gone.
    std::set<std::uint32_t> sanctuaryHolders;
    // Runtime FormIDs of the placed NPCs a script gave AI or moved, or that
    // fought: npc_anim no longer holds them on their authored spot.
    std::set<std::uint32_t> holdReleased;
    // The rank last set per actor since this state was loaded; not saved, so
    // a load always sets every rank once.
    std::map<std::uint32_t, int> sanctuaryRanks;

    // --- reputation, crime, faction reactions, running scripts -------------
    int   reputation = 0;
    float crimeLevel = 0.0f;
    // The player's class name as chosen, capitals kept (the chargen vars
    // hold it lowercased). Never a tab or newline.
    std::string className;
    int  FactionReaction(const std::string& a, const std::string& b) const;
    void SetFactionReaction(const std::string& a, const std::string& b,
                            int value);
    bool ScriptRunning(const std::string& script) const;
    // `StartScript`: the global script ticks from now on. `target` is the id
    // an explicit `ref->StartScript` named, which its bare commands act on.
    void StartScript(const std::string& script, const std::string& target);
    void StopScript(const std::string& script);
    // Starts every SSCR script that is not running. TES3 does this at new
    // game AND on every load, so a start script that stopped itself runs
    // again after a reload.
    void StartStartupScripts();
    // Every running global script.
    std::vector<RunningGlobal> RunningScripts() const;

    // One running global script: the explicit target its bare commands act
    // on, and the plugin it runs as (scope.h), by NAME so a save survives a
    // load order that moved it.
    struct Running {
        std::string target;
        std::string runAs;
    };

    // --- the conversation in progress --------------------------------------
    // Reset by BeginConversation; filled by result scripts as they run.
    std::vector<ChoiceLine>  choices;
    std::vector<std::string> addedTopics;
    std::vector<std::string> messages;
    bool goodbye = false;
    int  choice = -1;

    // The button a `MessageBox` with buttons is waiting on, read back once by
    // `GetButtonPressed` and -1 from then on. Not part of the conversation:
    // an object script raises one with no menu open.
    // See: docs/commentary/morrowind_runtime.md#messagebox-buttons
    int  buttonPressed = -1;

    // --- the conversation's disposition, DialogueManager's three numbers ---
    // The base the conversation opened on, the base as persuasion has moved
    // it (what the filter and the bar read), and the part that outlives it.
    // See: docs/commentary/morrowind_runtime.md#persuasion
    std::string speaker;
    int originalDisposition = 0;
    int currentDisposition = 0;
    int permanentDispositionChange = 0;

    // Opens a conversation with `speaker`; a persuasion's bookkeeping is
    // kept until EndConversation folds it into the base.
    void BeginConversation(const std::string& speaker = std::string());
    // DialogueManager::persuade's disposition lines: `temp` moves the base
    // the filter reads now, `perm` accumulates for EndConversation.
    void ApplyPersuasion(int temp, int perm);
    // DialogueManager::goodbyeSelected: the base becomes the original plus
    // the permanent change, clamped to 0..100, and the three reset.
    void EndConversation();

    // --- the co-save ---------------------------------------------------------
    // One tab-separated record per line under a version header. A line this
    // build does not know is skipped; a version 1 save is read and re-keyed.
    std::string Serialize() const;
    // Replaces everything persistent. Returns how many records were taken.
    std::size_t Deserialize(const std::string& text);
    void Reset();

private:
    std::map<std::string, int> mJournal;
    std::vector<std::pair<std::string, int>> mEntries;
    std::map<std::string, int> mDisposition;
    std::map<std::string, int> mActorRank;
    std::map<std::string, float> mGlobals;
    std::map<std::pair<std::string, std::string>, float> mVars;
    std::map<std::string, Membership> mFactions;
    std::map<std::pair<std::string, std::string>, int> mReactions;
    std::map<std::string, Running> mRunning;
    std::set<std::string> mKnownTopics;
    std::map<std::pair<std::string, int>, int> mAiSettings;
    std::map<std::pair<std::string, int>, bool> mMovementFlags;
    // Indexed by the opcode offset, so the unported switches keep their slots.
    // All start ENABLED, which is TES3's own default, so a plugin that never
    // touches them behaves as if the switches did not exist.
    bool mControls[kControlSwitchCount] = {true, true, true, true,
                                           true, true, true};
};

// The one state of this game session.
DialogueState& State();

// PC Crime Level: the bounty the engine keeps, else the state's copy.
// See: docs/commentary/morrowind_runtime.md#crime-is-the-engines
float PlayerCrimeLevelNow();

}  // namespace tesruntime::mw
