// Plugin log: MorrowindRuntime.log in the SKSE folder, beside the Papyrus
// logs the project already reads.
//
// Copied from tes_runtime/plugin rather than shared. A library linked into
// both DLLs would put this MIT code in the GPL-3.0 binary's dependency graph,
// which is exactly what the separate-DLL split exists to avoid.
// See: docs/commentary/morrowind_runtime.md#licensing

#pragma once

#include <string>

namespace mwruntime {

// The SKSE log folder with a trailing backslash, or "" when Documents is
// unknown; every file the plugin writes for a human goes here.
std::wstring LogDir();
void OpenLog();
// For the headless tests: everything logged goes to stdout, or nowhere.
void LogToStdout(bool on);
void Log(const char* fmt, ...);

// A line worth writing only while chasing a bug: script variables and globals
// that a script rewrites every tick. A timer or a random roll CHANGES every
// tick by definition, so "log on change" does not thin them -- measured over
// one chargen run, 19,000 of 21,022 lines. Off unless `MWRUNTIME_VERBOSE` is
// set in the environment.
void LogVerbose(const char* fmt, ...);

}  // namespace mwruntime
