// Missing converted voice resources: redirect the request, never copy audio.
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "json.h"

namespace tesruntime {

using VoiceOpenFn = int (*)(const char*, void**, std::uint8_t, void*);

class VoiceRedirects {
public:
    void Add(const Json& sidecar);
    bool Empty() const { return plugins_.empty(); }
    int Open(const char* path, void** stream, std::uint8_t flag, void* context,
             VoiceOpenFn original) const;
private:
    struct Rule { std::vector<std::string> alternates; std::string greeting; };
    std::map<std::string, std::map<std::string, Rule>> plugins_;
};

bool InstallVoiceRedirects();

}  // namespace tesruntime
