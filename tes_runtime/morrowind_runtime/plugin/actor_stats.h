// One actor's skill or attribute by its TES3 index, for callers that have no
// business including the interpreter.
//
// Separate from script_ops.h because that header pulls Interpreter::Runtime
// and Context, which the dialogue filter's actor does not have and does not
// need. Both are implemented in script_ops_stats.cpp beside the Get commands,
// so there is ONE stat read and one table.
// See: docs/commentary/morrowind_runtime.md#the-player-stats-are-real

#pragma once

#include <string>

namespace mwruntime {

// `tes3Index` is 0..26 for a skill and 0..7 for an attribute -- the order
// NPC_.txt stores them in. Skyrim's actor value where the stat has one, else
// the DLL's own number over what the NPC_ record authored. An index outside
// its family answers 0.
float ActorSkill(const std::string& actor, int tes3Index);
float ActorAttribute(const std::string& actor, int tes3Index);

}  // namespace mwruntime
