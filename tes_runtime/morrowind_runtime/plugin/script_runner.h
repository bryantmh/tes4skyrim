// Compiles and runs an INFO's result script on the vendored OpenMW compiler
// and interpreter -- the path DialogueManager::executeScript takes.
//
// Every command OpenMW registers PARSES, because the compiler's own extension
// table is used whole. Which ones DO something is the port's progress: the
// dialogue set is real, and every other command is a stub that pops exactly
// the arguments its signature says it was given, returns zero, and logs its
// name once -- so an unported command can never corrupt the stack of the ones
// that follow it, and the log is the list of what is left.
// See: docs/commentary/morrowind_runtime.md#result-scripts

#pragma once

#include <string>
#include <vector>

#include "script_tables.h"

namespace Interpreter {
class Context;
}

namespace mwruntime {

// Runs `source` under `context`. False when it did not compile or threw;
// both are logged with the script's own line.
bool RunResultScript(const std::string& source, Interpreter::Context& context);

// Runs a whole SCPT body -- an object script -- under `context`, compiling it
// once and keeping the program for every later tick.
//
// 🛑 NOT the same compile as a result script's. A body OPENS with its own
// `begin`/`end` and DECLARES its locals inline, both of which a dialogue
// compile rejects outright: `canDeclareLocals()` is false there, so every
// `short` line would be an error.
// See: docs/plans/morrowind_object_scripts.md#instances
bool RunObjectScript(const std::string& script, const std::string& source,
                     Interpreter::Context& context);

// Drops every compiled object-script program, so a restaged sidecar is read
// again rather than served from the cache.
void ClearObjectPrograms();

// Compiles `script` if it has not been, so its locals and program are ready.
// Returns false when the body does not compile.
bool EnsureObjectScript(const std::string& script, const std::string& source);

// The locals a compiled object script DECLARED, in the per-type order its
// indices address, or null when it has not compiled.
//
// 🛑 This, not SCPT_locals.txt, is the layout an object script's locals read
// through. A body declares them inline and the compiler numbers them as it
// parses, so the staged table is a copy that can disagree -- and a script
// absent from it would silently drop every write.
// See: docs/plans/morrowind_object_scripts.md#instances
const ScriptLocals* ObjectScriptLocals(const std::string& script);

// Every unported command a script has reached so far, which is the port's
// worklist as the content itself orders it.
std::vector<std::string> UnportedCommandsSeen();

}  // namespace mwruntime
