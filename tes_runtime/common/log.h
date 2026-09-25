// Plugin log: <plugin name>.log in the SKSE folder (paths.h LogDir), beside
// the Papyrus logs the project already reads.

#pragma once

namespace tesruntime {

// Opens <PluginName()>.log; SetPluginName comes first.
void OpenLog();
void Log(const char* fmt, ...);

// For the headless tests: everything logged goes to stdout, or nowhere.
void LogToStdout(bool on);

// A line worth writing only while chasing a bug: script variables and globals
// that a script rewrites every tick. A timer or a random roll CHANGES every
// tick by definition, so "log on change" does not thin them -- measured over
// one chargen run, 19,000 of 21,022 lines. Off unless `TESRUNTIME_VERBOSE` is
// set in the environment.
void LogVerbose(const char* fmt, ...);

}  // namespace tesruntime
