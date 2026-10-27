// Opening and closing a registered menu by name, the way the engine's own
// code does: UIManager::AddMessage with the menu's interned name.
// See: docs/commentary/morrowind_runtime.md#opening-a-menu

#pragma once

#include <cstdint>

namespace tesruntime {

// UIMessage ids: kMessage_Open, kMessage_Close (2 is a legacy alias), and
// kMessage_ForceHide, which closes a MessageBoxMenu that Close leaves open
// (measured on 1.6.1170, docs/commentary/headless_testing.md#startup-popups).
constexpr std::uint32_t kMessageOpen = 1;
constexpr std::uint32_t kMessageClose = 3;
constexpr std::uint32_t kMessageForceHide = 4;

// Posts `message` to the menu registered as `name`. Resolves its entry points
// on first use; logs and does nothing when any is missing.
void PostMenuMessage(const char* name, std::uint32_t message);

void OpenMenuNamed(const char* name);
void CloseMenuNamed(const char* name);

}  // namespace tesruntime
