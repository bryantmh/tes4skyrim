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
void Log(const char* fmt, ...);

}  // namespace mwruntime
