// Where a plugin's files live: its name, its data folder under
// Data\SKSE\Plugins, and the SKSE log folder.

#pragma once

#include <string>

namespace tesruntime {

// Names the plugin ("TESRuntime", "MorrowindRuntime", ...). The log file and
// the sidecar folder both take it, so it is set first thing in
// SKSEPlugin_Load, before OpenLog.
void SetPluginName(const char* name);
const std::string& PluginName();

// Data\SKSE\Plugins\<plugin name>\, with the trailing backslash: where the
// converter puts this plugin's sidecars. "" when the game's path is unknown.
std::string SidecarDir();

// The game's Data\SKSE\Plugins\, with the trailing backslash: where SKSE loads
// plugins from, and the folder holding every runtime's sidecar folder. "" when
// unknown.
//
// Derived from the GAME, not from this DLL's own path: under MO2 the DLL's
// path is its mod folder, which holds none of the other mods' sidecars.
// See: docs/commentary/morrowind_runtime.md#sidecar-root
std::string PluginsDir();

// The SKSE log folder with a trailing backslash, or "" when Documents is
// unknown; every file a plugin writes for a human goes here.
std::wstring LogDir();

}  // namespace tesruntime
