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
    HMODULE self = nullptr;
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCSTR>(&PluginsDir), &self)) {
        return "";
    }
    char dll[MAX_PATH] = {0};
    if (!GetModuleFileNameA(self, dll, MAX_PATH)) return "";
    std::string path(dll);
    const std::size_t slash = path.find_last_of("\\/");
    if (slash == std::string::npos) return "";
    return path.substr(0, slash + 1);
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
