// TESRACT.exe, the launcher at the top of the release folder.
//
// The compiled app lives in app\ (Python's DLL must sit beside the exe that loads it, so it cannot
// be moved out of the program's own folder); this launcher keeps the top folder to itself, app\ and
// the user's data. It shows the splash at once -- before Windows has read a byte of the app -- from
// app\splash.bin, which the build renders with core/gui/splash.py so both splashes draw the same
// pixels, then starts app\TESRACT.exe with the splash window's handle in TESRACT_SPLASH. The app
// closes that window when its own window is ready; the launcher then exits. It also exits if the app
// does. With arguments (a command-line run) it shows no splash and only forwards them.
// See: docs/commentary/core_gui.md#folder-build

#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#include <windows.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace {

struct Splash {
    uint32_t percent = 0, width = 0, height = 0;
    uint32_t left = 0, top = 0, right = 0, bottom = 0;
    uint32_t segment = 0, step = 1, frame_ms = 30, accent = 0;
    std::vector<uint32_t> base;
};

Splash g_splash;
HBITMAP g_bitmap = nullptr;
HDC g_mem = nullptr;
uint32_t* g_bits = nullptr;
POINT g_at = {0, 0};
unsigned g_frame = 0;

std::wstring ExeDir() {
    std::vector<wchar_t> buf(32768);
    DWORD n = GetModuleFileNameW(nullptr, buf.data(), static_cast<DWORD>(buf.size()));
    std::wstring path(buf.data(), n);
    return path.substr(0, path.find_last_of(L'\\'));
}

// The arguments this launcher was given, after its own path (quoted or not).
std::wstring ForwardedArgs() {
    const wchar_t* p = GetCommandLineW();
    if (*p == L'"') {
        for (++p; *p && *p != L'"'; ++p) {}
        if (*p) ++p;
    } else {
        while (*p && *p != L' ' && *p != L'\t') ++p;
    }
    while (*p == L' ' || *p == L'\t') ++p;
    return p;
}

// Read the entry of the splash file for display scale `percent`: the largest not above it, else the smallest.
bool LoadSplash(const std::wstring& path, uint32_t percent, Splash& out) {
    FILE* f = nullptr;
    if (_wfopen_s(&f, path.c_str(), L"rb") != 0 || !f) return false;
    char magic[4];
    uint32_t count = 0;
    bool ok = fread(magic, 1, 4, f) == 4 && memcmp(magic, "TSPL", 4) == 0 && fread(&count, 4, 1, f) == 1;
    long chosen = -1;
    for (uint32_t i = 0; ok && i < count; ++i) {
        uint32_t h[11];
        if (fread(h, 4, 11, f) != 11) { ok = false; break; }
        Splash cur;
        cur.percent = h[0]; cur.width = h[1]; cur.height = h[2];
        cur.left = h[3]; cur.top = h[4]; cur.right = h[5]; cur.bottom = h[6];
        cur.segment = h[7]; cur.step = h[8]; cur.frame_ms = h[9]; cur.accent = h[10];
        long pixels = ftell(f);
        bool better = chosen < 0 || (cur.percent <= percent && (out.percent > percent || cur.percent > out.percent));
        if (better) { out = cur; chosen = pixels; }
        fseek(f, static_cast<long>(cur.width) * cur.height * 4, SEEK_CUR);
    }
    if (ok && chosen >= 0) {
        out.base.resize(static_cast<size_t>(out.width) * out.height);
        fseek(f, chosen, SEEK_SET);
        ok = fread(out.base.data(), 4, out.base.size(), f) == out.base.size();
    }
    fclose(f);
    return ok && chosen >= 0 && out.width && out.height;
}

// Frame `n`: the still picture plus the load bar's segment (the same arithmetic as splash.segment_span).
void Draw(HWND hwnd) {
    const Splash& s = g_splash;
    memcpy(g_bits, s.base.data(), s.base.size() * 4);
    long left = s.left, right = s.right, seg = s.segment;
    long start = left - seg + static_cast<long>((static_cast<unsigned long long>(g_frame) * s.step)
                                                % static_cast<unsigned long long>(right - left + seg));
    long x0 = start > left ? start : left, x1 = start + seg < right ? start + seg : right;
    for (uint32_t y = s.top; x1 > x0 && y < s.bottom; ++y)
        for (long x = x0; x < x1; ++x) g_bits[y * s.width + x] = s.accent;
    HDC screen = GetDC(nullptr);
    SIZE size = {static_cast<LONG>(s.width), static_cast<LONG>(s.height)};
    POINT origin = {0, 0};
    BLENDFUNCTION blend = {AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
    UpdateLayeredWindow(hwnd, screen, &g_at, &size, g_mem, &origin, 0, &blend, ULW_ALPHA);
    ReleaseDC(nullptr, screen);
}

LRESULT CALLBACK SplashProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_TIMER: ++g_frame; Draw(hwnd); return 0;
    case WM_CLOSE: DestroyWindow(hwnd); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    default: return DefWindowProcW(hwnd, msg, wp, lp);
    }
}

// The splash window, shown and animating; nullptr when the splash file is missing or unreadable.
HWND ShowSplash(HINSTANCE instance, const std::wstring& file) {
    uint32_t percent = GetDpiForSystem() * 100 / 96;
    if (!LoadSplash(file, percent, g_splash)) return nullptr;
    WNDCLASSEXW wc = {sizeof(wc)};
    wc.lpfnWndProc = SplashProc;
    wc.hInstance = instance;
    wc.hIcon = LoadIconW(instance, MAKEINTRESOURCEW(1));
    wc.lpszClassName = L"TESRACTSplash";
    RegisterClassExW(&wc);
    HWND hwnd = CreateWindowExW(WS_EX_LAYERED, wc.lpszClassName, L"TESRACT", WS_POPUP, 0, 0,
                                g_splash.width, g_splash.height, nullptr, nullptr, instance, nullptr);
    if (!hwnd) return nullptr;
    RECT work;
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &work, 0);
    g_at = {(work.left + work.right - static_cast<LONG>(g_splash.width)) / 2,
            (work.top + work.bottom - static_cast<LONG>(g_splash.height)) / 2};
    BITMAPINFO bi = {};
    bi.bmiHeader = {sizeof(BITMAPINFOHEADER), static_cast<LONG>(g_splash.width), -static_cast<LONG>(g_splash.height),
                    1, 32, BI_RGB};
    HDC screen = GetDC(nullptr);
    g_bitmap = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, reinterpret_cast<void**>(&g_bits), nullptr, 0);
    g_mem = CreateCompatibleDC(screen);
    ReleaseDC(nullptr, screen);
    if (!g_bitmap || !g_mem) { DestroyWindow(hwnd); return nullptr; }
    SelectObject(g_mem, g_bitmap);
    Draw(hwnd);
    ShowWindow(hwnd, SW_SHOWNORMAL);
    SetTimer(hwnd, 1, g_splash.frame_ms, nullptr);
    return hwnd;
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int) {
    std::wstring dir = ExeDir();
    std::wstring app = dir + L"\\app\\TESRACT.exe";
    std::wstring args = ForwardedArgs();
    HWND splash = args.empty() ? ShowSplash(instance, dir + L"\\app\\splash.bin") : nullptr;
    if (splash) SetEnvironmentVariableW(L"TESRACT_SPLASH", std::to_wstring(reinterpret_cast<uintptr_t>(splash)).c_str());
    std::wstring cmd = L"\"" + app + L"\"" + (args.empty() ? L"" : L" " + args);
    STARTUPINFOW si = {sizeof(si)};
    PROCESS_INFORMATION pi = {};
    if (!CreateProcessW(app.c_str(), cmd.data(), nullptr, nullptr, FALSE, 0, nullptr, dir.c_str(), &si, &pi)) {
        if (splash) DestroyWindow(splash);
        std::wstring text = L"TESRACT could not start " + app + L".\n\nUnzip the whole TESRACT folder again.";
        MessageBoxW(nullptr, text.c_str(), L"TESRACT", MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread);
    if (!splash) {
        DWORD code = 1;
        WaitForSingleObject(pi.hProcess, INFINITE);
        GetExitCodeProcess(pi.hProcess, &code);
        CloseHandle(pi.hProcess);
        return static_cast<int>(code);
    }
    MSG msg;
    for (bool running = splash != nullptr; running;) {
        DWORD woke = MsgWaitForMultipleObjects(1, &pi.hProcess, FALSE, INFINITE, QS_ALLINPUT);
        if (woke == WAIT_OBJECT_0) break;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            if (msg.message == WM_QUIT) { running = false; break; }
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    CloseHandle(pi.hProcess);
    return 0;
}
