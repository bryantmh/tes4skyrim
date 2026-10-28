// The game side of the headless test runner for converted TES4 plugins
// (Oblivion, Nehrim): Skyrim's own dialogue menu, MessageBox and activation,
// driven the way a player drives them. Conditions and console lines go to the
// harness (common/test_runner.h).
// See: docs/commentary/headless_testing.md#tes4-quest-cases

#pragma once

namespace tesruntime {

// Hands the runner its game side and starts its tick, when TES_TEST_CASES is
// set; nothing otherwise. Call once the game's data has loaded.
void InstallTestRunner();

}  // namespace tesruntime
