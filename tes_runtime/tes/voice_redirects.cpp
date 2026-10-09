#include "voice_redirects.h"

#include <algorithm>

namespace tesruntime {
namespace {

// BSA names preserve UTF-8 bytes of the master's VTYP. Fold ASCII only,
// just like the converter's voice filenames; never reinterpret as CP1252.
std::string Normal(std::string s) {
    for (char& c : s) {
        if (c == '/') c = '\\';
        else if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
    }
    if (s.compare(0, 5, "data\\") == 0) s.erase(0, 5);
    return s;
}

bool Folder(const std::string& s) {
    return !s.empty() && s != "." && s != ".." &&
        s.find_first_of("\\/:") == std::string::npos;
}

bool VoicePath(const std::string& s, std::string& plugin,
               std::string& voice, std::string& leaf) {
    if (s.compare(0, 12, "sound\\voice\\") != 0) return false;
    const auto p = s.find('\\', 12);
    const auto v = p == std::string::npos ? p : s.find('\\', p + 1);
    if (v == std::string::npos) return false;
    plugin = s.substr(12, p - 12);
    voice = s.substr(p + 1, v - p - 1);
    leaf = s.substr(v + 1);
    if (!Folder(plugin) || !Folder(voice) || !Folder(leaf)) return false;
    const auto dot = leaf.rfind('.');
    if (dot == std::string::npos) return false;
    const auto ext = leaf.substr(dot);
    return ext == ".fuz" || ext == ".xwm" || ext == ".wav" || ext == ".lip";
}

}  // namespace

void VoiceRedirects::Add(const Json& sidecar) {
    const auto plugin = Normal(sidecar["plugin"].asString());
    if (!Folder(plugin) || !sidecar["voices"].isObject()) return;
    std::map<std::string, Rule> rules;
    for (const auto& entry : sidecar["voices"].fields()) {
        const auto voice = Normal(entry.first);
        if (!Folder(voice)) continue;
        Rule rule;
        for (const auto& item : entry.second["alternates"].items()) {
            const auto alternate = Normal(item.asString());
            if (Folder(alternate) && alternate != voice)
                rule.alternates.push_back(alternate);
        }
        rule.greeting = Normal(entry.second["greeting"].asString());
        std::string owner, type, leaf;
        if (!VoicePath(rule.greeting, owner, type, leaf)) rule.greeting.clear();
        if (!rule.alternates.empty() || !rule.greeting.empty())
            rules.emplace(voice, std::move(rule));
    }
    if (!rules.empty()) plugins_.emplace(plugin, std::move(rules));
}

int VoiceRedirects::Open(const char* path, void** stream, std::uint8_t flag,
                        void* context, VoiceOpenFn original) const {
    const int rc = original(path, stream, flag, context);
    // Never mask file errors, writes or a stream the engine already returned.
    // ResourceOpen's flag is writable (BSResourceNiBinaryStream ctor passes
    // a_writeable unchanged); normal voice reads pass zero.
    if (rc != 1 || !path || !stream || *stream || flag) return rc;
    const auto name = Normal(path);
    std::string plugin, voice, leaf;
    if (!VoicePath(name, plugin, voice, leaf)) return rc;
    const auto p = plugins_.find(plugin);
    if (p == plugins_.end()) return rc;
    const auto v = p->second.find(voice);
    if (v == p->second.end()) return rc;
    const auto& rule = v->second;
    for (const auto& alternate : rule.alternates) {
        const auto candidate = "sound\\voice\\" + plugin + "\\" + alternate + "\\" + leaf;
        const int result = original(candidate.c_str(), stream, flag, context);
        if (result == 0 || *stream) return result;
        if (result != 1) return result;
    }
    const auto dot = leaf.rfind('.');
    const bool firstGreeting = leaf.find("_greeting_") != std::string::npos &&
        dot >= 2 && leaf.compare(dot - 2, 2, "_1") == 0;
    if (firstGreeting && !rule.greeting.empty()) {
        auto candidate = rule.greeting;
        candidate.replace(candidate.rfind('.'), std::string::npos, leaf.substr(dot));
        return original(candidate.c_str(), stream, flag, context);
    }
    return rc;
}

}  // namespace tesruntime
