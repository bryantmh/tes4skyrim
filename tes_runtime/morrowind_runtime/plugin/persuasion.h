// Persuasion: Admire, Intimidate, Taunt and the three bribes, as OpenMW
// resolves them -- MechanicsManager::getPersuasionDispositionChange and
// DialogueManager::persuade, ported line for line onto DialogueState, the
// actor table and the game hooks. The die roll is injected so every branch
// is checkable headless.
// See: docs/commentary/morrowind_runtime.md#persuasion

#pragma once

#include <string>

namespace mwruntime {

// MWBase::MechanicsManager::PersuasionType, in its order.
enum class Persuasion { Admire, Intimidate, Taunt, Bribe10, Bribe100, Bribe1000 };

constexpr int kPersuasionCount = 6;

struct PersuasionOutcome {
    // False when the tables lack what the formula needs; `missing` names it.
    bool ok = false;
    std::string missing;
    bool success = false;
    // The topic that answers: "Admire Success", "Bribe Fail", ...
    std::string topic;
    // The GMST holding that topic's title: "sAdmireSuccess", ...
    std::string titleGmst;
};

// A roll of 0..99, Misc::Rng::roll0to99. Null means uniform at random.
using RollFn = int (*)();

// Persuades the conversation's speaker (DialogueState::speaker): moves the
// disposition, the AI settings, the gold and the skill credit, and names the
// response topic. Nothing is touched when `ok` comes back false.
PersuasionOutcome Persuade(Persuasion type, RollFn roll = nullptr);

// MechanicsManager::getBarterOffer: what `npc` asks (`buying`) or pays for
// something worth `basePrice`, from both sides' Mercantile, Luck, Personality
// and fatigue and the speaker's disposition. Never below 1; a base of 0 is 0.
int BarterOffer(const std::string& npc, int basePrice, bool buying);

// The gold a bribe of this kind costs; 0 for the other three.
int BribeCost(Persuasion type);

// The Skyrim gold the player carries, through the gold hook; 0 without one.
int PlayerGold();

}  // namespace mwruntime
