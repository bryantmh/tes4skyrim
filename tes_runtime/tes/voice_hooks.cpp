#include "voice_redirects.h"

#include "addresses.h"
#include "engine.h"
#include "hook.h"
#include "log.h"
#include "../creature/ids.h"

namespace tesruntime {
namespace {
VoiceRedirects g_redirects;
VoiceOpenFn g_open = nullptr;

int OpenVoice(const char* path, void** stream, std::uint8_t flag, void* context) {
    // The rules are immutable once hooks are installed. Opening a candidate
    // goes straight to the engine entry point, not back through our hook.
    return g_redirects.Open(path, stream, flag, context, g_open);
}
void LoadRules(const std::string&, const Json& doc) { g_redirects.Add(doc); }
}  // namespace

bool InstallVoiceRedirects() {
    static bool attempted = false;
    if (attempted) return g_open != nullptr;
    attempted = true;
    ForEachSidecar("voice_redirects.json", LoadRules);
    if (g_redirects.Empty()) return false;
    // Same resource entry point and ABI used by CreatureRuntime. Both BSA
    // and loose resources use it; existing call-site hooks are left in place.
    const auto address = Resolve("VoiceResourceOpen", ids::kResourceOpen,
                                 ids::kSigResourceOpen);
    if (!address) return false;
    g_open = reinterpret_cast<VoiceOpenFn>(address);
    const int hooks = PatchAllCalls(address, reinterpret_cast<void*>(&OpenVoice),
                                    "missing converted voices");
    Log("voice redirects: %d resource call site(s)", hooks);
    if (!hooks) g_open = nullptr;
    return hooks > 0;
}
}  // namespace tesruntime
