#include "paths.h"

#include <windows.h>
#include <shlobj.h>

namespace tesruntime {

namespace {
std::string g_pluginName = "TESRuntime";
}  // namespace

void SetPluginName(const char* name) { g_pluginName = name; }

const std::string& PluginName() { return g_pluginName; }

std::string PluginsDir() {
    char exe[MAX_PATH] = {0};
    if (!GetModuleFileNameA(nullptr, exe, MAX_PATH)) return "";
    std::string path(exe);
    const std::size_t slash = path.find_last_of("\\/");
    if (slash == std::string::npos) return "";
    return path.substr(0, slash + 1) + "Data\\SKSE\\Plugins\\";
}

std::string SidecarDir() {
    const std::string plugins = PluginsDir();
    return plugins.empty() ? "" : plugins + g_pluginName + "\\";
}

std::wstring LogDir() {
    PWSTR docs = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_Documents, 0, nullptr, &docs))) return L"";
    std::wstring path = docs;
    CoTaskMemFree(docs);
    path += L"\\My Games\\Skyrim Special Edition\\SKSE\\";
    CreateDirectoryW(path.c_str(), nullptr);
    return path;
}

}  // namespace tesruntime
