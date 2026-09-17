#include "log.h"

#include <windows.h>
#include <shlobj.h>

#include <share.h>

#include <cstdarg>
#include <cstdio>
#include <mutex>
#include <string>

namespace mwruntime {

namespace {
FILE*      g_file = nullptr;
std::mutex g_mutex;

constexpr const wchar_t* kLogName = L"MorrowindRuntime.log";
}  // namespace

std::wstring LogDir() {
    PWSTR docs = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_Documents, 0, nullptr, &docs))) return L"";
    std::wstring path = docs;
    CoTaskMemFree(docs);
    path += L"\\My Games\\Skyrim Special Edition\\SKSE\\";
    CreateDirectoryW(path.c_str(), nullptr);
    return path;
}

void OpenLog() {
    std::lock_guard<std::mutex> lk(g_mutex);
    if (g_file) return;
    const std::wstring path = LogDir() + kLogName;
    // _SH_DENYNO so the log can be read while the game is running.
    g_file = _wfsopen(path.c_str(), L"w", _SH_DENYNO);
}

void LogToStdout(bool on) {
    std::lock_guard<std::mutex> lk(g_mutex);
    g_file = on ? stdout : nullptr;
}

void Log(const char* fmt, ...) {
    std::lock_guard<std::mutex> lk(g_mutex);
    if (!g_file) return;
    SYSTEMTIME st;
    GetLocalTime(&st);
    std::fprintf(g_file, "[%02d:%02d:%02d] ", st.wHour, st.wMinute, st.wSecond);
    va_list args;
    va_start(args, fmt);
    std::vfprintf(g_file, fmt, args);
    va_end(args);
    std::fputc('\n', g_file);
    std::fflush(g_file);
}

}  // namespace mwruntime
