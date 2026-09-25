// Address Library stable IDs for everything CreatureRuntime touches: the two
// animation cache parsers and the resource-open helper they share.
//
// Every ID was derived by locating the target in the GOG/AE 1.6.659 build
// (the only non-DRM-packed copy, so the only one that disassembles
// statically) and inverting its RVA through versionlib-1-6-659-0.bin; each
// was then checked to exist in versionlib-1-6-1170-0.bin, the Steam build
// the user plays. Nothing here is a raw RVA.
//
// The facts each ID rests on are recorded in
// docs/commentary/asset_convert_creature.md#runtime-animation-cache-composition.

#pragma once

#include <cstddef>
#include <cstdint>

namespace tesruntime::ids {

// The AnimationDataSingleFile.txt parser (0x4f7280 on 1.6.659, 0x536ec0 on
// 1.6.1170). Its ONE call to kResourceOpen is the hook site.
constexpr std::uint64_t kAnimDataParser = 32571;

// The AnimationSetDataSingleFile.txt parser (0x4fb3c0 on 1.6.659). Same
// shape: one call to kResourceOpen, one hook site.
constexpr std::uint64_t kAnimSetDataParser = 32624;

// The shared resource-open helper (0xc7e2d0 on 1.6.659):
//   int Open(const char* path, BSResource::Stream** out, uint8 flag, void* r9)
// returns 0 on success with *out holding a refcounted stream.
constexpr std::uint64_t kResourceOpen = 69839;

// Signature fallbacks, '|'-separated alternates tried in order. Each occurs
// EXACTLY ONCE in .text of the build it was taken from and never in the
// other: first the GOG 1.6.659 prologue, then Skyrim VR 1.4.15's
// (0x4ec580 / 0x4f0860 / 0xc89d60 there). Used when no versionlib applies,
// which is every pre-AE runtime.
constexpr const char* kSigAnimDataParser =
    "48 89 4C 24 08 55 53 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 68 F8 FF "
    "FF 48 81 EC 98 08 00 00 48 C7 45 58 FE FF FF FF|"
    "48 89 4C 24 08 55 53 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 58 F8 FF "
    "FF 48 81 EC A8 08 00 00 48 C7 45 68 FE FF FF FF";
constexpr const char* kSigAnimSetDataParser =
    "48 89 4C 24 08 55 53 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 88 F3 FF "
    "FF 48 81 EC 78 0D 00 00 48 C7 85 F8 00 00 00 FE|"
    "48 89 4C 24 08 55 53 56 57 41 54 41 55 41 56 41 57 48 8D AC 24 58 F3 FF "
    "FF 48 81 EC A8 0D 00 00 48 C7 85 30 01 00 00 FE";
constexpr const char* kSigResourceOpen =
    "48 8B C4 57 41 56 41 57 48 81 EC 40 01 00 00 48 C7 44 24 60 FE FF FF FF "
    "48 89 58 08 48 89 68 10 48 89 70 20 4D 8B F9 45 0F B6 F0|"
    "48 8B C4 57 48 83 EC 70 48 C7 40 C8 FE FF FF FF 48 89 58 08 48 89 68 10 "
    "48 89 70 18 49 8B E9 41 0F B6 F8 48 8B F2 48 8B";

// How far into a parser the helper call may sit. Both calls are within the
// first 0x100 bytes on 1.6.659 (+0xce and +0x84); the bound only limits the
// scan, the match itself is by resolved target.
constexpr std::size_t kParserScanBytes = 0x400;

// How far into the AnimData parser its stream release may sit (+0x498 on
// 1.6.659 and 1.6.1170, +0x4e8 on VR); the body is under 0xe00 bytes on all.
constexpr std::size_t kParserBodyBytes = 0x1000;

}  // namespace tesruntime::ids
