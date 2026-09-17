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

namespace Interpreter {
class Context;
}

namespace mwruntime {

// Runs `source` under `context`. False when it did not compile or threw;
// both are logged with the script's own line.
bool RunResultScript(const std::string& source, Interpreter::Context& context);

// Every unported command a script has reached so far, which is the port's
// worklist as the content itself orders it.
std::vector<std::string> UnportedCommandsSeen();

}  // namespace mwruntime
