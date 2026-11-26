// Address Library ids ArchiveLoader resolves; SE 1.5.97 and VR: ids_pre_ae.h.

#pragma once

#include <cstdint>

namespace tesruntime::ids {

// MountPluginArchives(fileName) (1.6.1170 0x1bf710) returns at once unless the extension is .esp/.esm/.esl
// (VR: .esp/.esm), then, under an archive-registry sink that records each archive in the plugin-archive map,
// calls LoadArchive(name, 0, 0) (0xd0a920) for "<stem>.bsa" and then "<stem> - Textures.bsa" (the suffix table
// at 0x1fd4c88), each skipped when the map holds it. The startup archive init (36639, 0x64b450) calls it for
// each active plugin in load order.
// See: docs/commentary/tes_runtime_archives.md#the-engine-mount
constexpr std::uint64_t kMountPluginArchives = 13786;
constexpr std::uint64_t kLoadArchive = 69852;

}  // namespace tesruntime::ids
