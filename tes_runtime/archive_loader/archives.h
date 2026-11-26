// Extra archives a converted plugin ships beyond the two Skyrim mounts itself.
//
// Skyrim mounts "<stem>.bsa" and "<stem> - Textures.bsa" for each active plugin. A converted plugin whose assets
// overflow those two ships "<stem> - Part 2.bsa", "<stem> - Part 3.bsa", ... (contiguous from 2); this mounts them
// in the same engine call, right after the plugin's own two, so they take that plugin's place in load order.
// See: docs/commentary/tes_runtime_archives.md#part-archives

#pragma once

namespace tesruntime {

// Hooks the engine's plugin-archive mount; false (logged) when an address or the call site is missing. Must run
// in SKSEPlugin_Load: the engine mounts every plugin's archives at startup, before kMessage_PostLoad.
bool InstallPartArchives();

}  // namespace tesruntime
