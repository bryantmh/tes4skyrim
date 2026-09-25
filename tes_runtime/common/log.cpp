#define _CRT_SECURE_NO_WARNINGS

#include "log.h"

#include <windows.h>

#include <share.h>

#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>

#include "paths.h"

namespace tesruntime {

namespace {
FILE*      g_file = nullptr;
std::mutex g_mutex;

void Write(const char* fmt, va_list args) {
    if (!g_file) return;
    SYSTEMTIME st;
    GetLocalTime(&st);
    std::fprintf(g_file, "[%02d:%02d:%02d] ", st.wHour, st.wMinute, st.wSecond);
    std::vfprintf(g_file, fmt, args);
    std::fputc('\n', g_file);
    std::fflush(g_file);
}
}  // namespace

void OpenLog() {
    std::lock_guard<std::mutex> lk(g_mutex);
    if (g_file) return;
    const std::string& name = PluginName();
    const std::wstring path = LogDir() + std::wstring(name.begin(), name.end()) + L".log";
    // _SH_DENYNO so the log can be read while the game is running.
    g_file = _wfsopen(path.c_str(), L"w", _SH_DENYNO);
}

void LogToStdout(bool on) {
    std::lock_guard<std::mutex> lk(g_mutex);
    g_file = on ? stdout : nullptr;
}

void Log(const char* fmt, ...) {
    std::lock_guard<std::mutex> lk(g_mutex);
    va_list args;
    va_start(args, fmt);
    Write(fmt, args);
    va_end(args);
}

void LogVerbose(const char* fmt, ...) {
    static const bool on = std::getenv("TESRUNTIME_VERBOSE") != nullptr;
    if (!on) return;
    std::lock_guard<std::mutex> lk(g_mutex);
    va_list args;
    va_start(args, fmt);
    Write(fmt, args);
    va_end(args);
}

}  // namespace tesruntime
