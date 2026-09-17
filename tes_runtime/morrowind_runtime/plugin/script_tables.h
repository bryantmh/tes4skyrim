// What the result-script COMPILER has to be told about a name: whether it is
// a global, which locals a script declares, and which script an actor runs.
//
// All three are authored data, staged into the sidecar beside the dialogue
// (MWGL.txt, MWSV.txt, MWOS.txt) from the plugin's GLOB and SCPT records and
// its TES3 masters'. The parser asks "is this a global?" BEFORE "is this an
// id?", so guessing here breaks `player->...` and `Script.member` outright.
// See: docs/commentary/morrowind_runtime.md#script-tables

#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace mwruntime {

// A GLOB record: 's', 'l' or 'f', and the value it starts at.
struct GlobalDef {
    char  type = 'f';
    float value = 0.0f;
};

// A script's declared locals, lowercased, in declaration order per type --
// which is the index the compiled code addresses them by.
struct ScriptLocals {
    std::vector<std::string> shorts;
    std::vector<std::string> longs;
    std::vector<std::string> floats;

    // 's', 'l', 'f', or ' ' when `name` is not declared.
    char TypeOf(const std::string& name) const;
};

// What an NPC_ record authors about an actor, which the filter compares by
// NAME: MWNP.txt, merged over the plugin's TES3 masters.
struct ActorDef {
    std::string race;
    std::string clazz;
    std::string faction;
    int  rank = 0;
    int  disposition = 50;
    bool female = false;
    std::string name;
};

// A Skyrim form behind a TES3 id: the plugin that owns it and its FormID
// there, resolved through the RUNNING load order. MWID.txt (items) and
// MWQS.txt (journal quests).
struct FormRef {
    std::string plugin;
    std::uint32_t formId = 0;
};

void ClearScriptTables();

// Reads the three tables from one plugin's sidecar folder (trailing slash).
void LoadScriptTables(const std::string& pluginDir);

const std::unordered_map<std::string, GlobalDef>& GlobalDefs();
const GlobalDef* FindGlobal(const std::string& name);
const ScriptLocals* FindScriptLocals(const std::string& script);

// The script an actor runs, or "" -- by the actor's TES3 id.
const std::string& ScriptOf(const std::string& actor);

const ActorDef* FindActor(const std::string& actor);
const FormRef* FindItem(const std::string& item);
const FormRef* FindQuest(const std::string& quest);
std::size_t ActorCount();
std::size_t QuestCount();

// How many scripts and actor bindings are loaded, for the log.
std::size_t ScriptCount();
std::size_t ActorScriptCount();

}  // namespace mwruntime
