// Opening and closing a registered menu by name, the way the engine's own
// code does: UIManager::AddMessage with the menu's interned name.
// See: docs/commentary/morrowind_runtime.md#opening-a-menu

#pragma once

#include <cstdint>

namespace tesruntime {

// UIMessage ids: kMessage_Open, and kMessage_Close (2 is a legacy alias).
constexpr std::uint32_t kMessageOpen = 1;
constexpr std::uint32_t kMessageClose = 3;

// Posts `message` to the menu registered as `name`. Resolves its entry points
// on first use; logs and does nothing when any is missing.
void PostMenuMessage(const char* name, std::uint32_t message);

void OpenMenuNamed(const char* name);
void CloseMenuNamed(const char* name);

}  // namespace tesruntime
