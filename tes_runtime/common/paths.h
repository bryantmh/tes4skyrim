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
// converter puts this plugin's sidecars. "" when the module path is unknown.
//
// Derived from THIS MODULE, not from GetModuleFileName(nullptr): a plugin is
// always loaded from the folder it needs to read, whatever launched the game.
// See: docs/commentary/morrowind_runtime.md#sidecar
std::string SidecarDir();

// Data\SKSE\Plugins\, with the trailing backslash: the folder this DLL is
// loaded from, which holds every runtime's sidecar folder. "" when unknown.
std::string PluginsDir();

// The SKSE log folder with a trailing backslash, or "" when Documents is
// unknown; every file a plugin writes for a human goes here.
std::wstring LogDir();

}  // namespace tesruntime
