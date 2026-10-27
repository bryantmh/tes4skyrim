// TESSandbox -- folder redirection for a headless test instance.
//
// Injected by tools/live/test_instance.py BEFORE SKSE, into a game created
// suspended. It points Documents (My Games: INIs, saves, every SKSE and plugin
// log) and LocalAppData (Plugins.txt) at the folders named by
// TES_SANDBOX_DOCUMENTS / TES_SANDBOX_LOCALAPPDATA, so a test run never reads
// or writes the player's own profile. With TES_SANDBOX_MUTE=1 it also keeps the
// game's audio session muted.
//
// No code bytes are patched. shell32's EXPORT table is repointed, which every
// module bound after this one (SKSE, every plugin) resolves through, and the
// IMPORT tables of modules already bound (the exe) are repointed in place.
// See: docs/commentary/headless_testing.md#sandbox

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <audiopolicy.h>
#include <mmdeviceapi.h>
#include <shlobj.h>
#include <tlhelp32.h>

#include <share.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <initializer_list>

namespace {

using FolderPathA = HRESULT(WINAPI*)(HWND, int, HANDLE, DWORD, LPSTR);
using FolderPathW = HRESULT(WINAPI*)(HWND, int, HANDLE, DWORD, LPWSTR);
using KnownFolderPath = HRESULT(WINAPI*)(REFKNOWNFOLDERID, DWORD, HANDLE, PWSTR*);

FolderPathA g_realA = nullptr;
FolderPathW g_realW = nullptr;
KnownFolderPath g_realKnown = nullptr;

wchar_t g_documents[MAX_PATH] = {};
wchar_t g_localAppData[MAX_PATH] = {};
FILE* g_log = nullptr;

void Log(const char* fmt, ...) {
    if (!g_log) return;
    va_list args;
    va_start(args, fmt);
    std::vfprintf(g_log, fmt, args);
    va_end(args);
    std::fputc('\n', g_log);
    std::fflush(g_log);
}

// The sandbox folder standing in for `csidl`, or null to pass the call through.
const wchar_t* RedirectCsidl(int csidl) {
    switch (csidl & ~CSIDL_FLAG_MASK) {
        case CSIDL_PERSONAL: return g_documents[0] ? g_documents : nullptr;
        case CSIDL_LOCAL_APPDATA: return g_localAppData[0] ? g_localAppData : nullptr;
        default: return nullptr;
    }
}

const wchar_t* RedirectKnown(REFKNOWNFOLDERID id) {
    if (IsEqualGUID(id, FOLDERID_Documents)) return RedirectCsidl(CSIDL_PERSONAL);
    if (IsEqualGUID(id, FOLDERID_LocalAppData)) return RedirectCsidl(CSIDL_LOCAL_APPDATA);
    return nullptr;
}

HRESULT WINAPI HookFolderPathA(HWND hwnd, int csidl, HANDLE token, DWORD flags, LPSTR out) {
    const wchar_t* path = RedirectCsidl(csidl);
    if (!path) return g_realA(hwnd, csidl, token, flags, out);
    if (!WideCharToMultiByte(CP_ACP, 0, path, -1, out, MAX_PATH, nullptr, nullptr)) return E_FAIL;
    Log("SHGetFolderPathA(0x%x) -> %s", csidl, out);
    return S_OK;
}

HRESULT WINAPI HookFolderPathW(HWND hwnd, int csidl, HANDLE token, DWORD flags, LPWSTR out) {
    const wchar_t* path = RedirectCsidl(csidl);
    if (!path) return g_realW(hwnd, csidl, token, flags, out);
    wcscpy_s(out, MAX_PATH, path);
    Log("SHGetFolderPathW(0x%x) -> redirected", csidl);
    return S_OK;
}

HRESULT WINAPI HookKnownFolderPath(REFKNOWNFOLDERID id, DWORD flags, HANDLE token, PWSTR* out) {
    const wchar_t* path = RedirectKnown(id);
    if (!path) return g_realKnown(id, flags, token, out);
    const size_t bytes = (std::wcslen(path) + 1) * sizeof(wchar_t);
    *out = static_cast<PWSTR>(CoTaskMemAlloc(bytes));
    if (!*out) return E_OUTOFMEMORY;
    std::memcpy(*out, path, bytes);
    Log("SHGetKnownFolderPath -> redirected");
    return S_OK;
}

struct Redirect {
    const char* name;
    void* hook;
    void* real;
};

Redirect g_redirects[3];

// A 14-byte `jmp [rip+0]` to `target`, placed ABOVE `base` and within 4 GB of
// it, because an export-table entry is a 32-bit RVA from the module base.
void* StubAbove(std::uintptr_t base, std::uintptr_t size, void* target) {
    static unsigned char* page = nullptr;
    static size_t used = 0;
    if (!page) {
        for (std::uintptr_t at = (base + size + 0xFFFF) & ~std::uintptr_t(0xFFFF);
             at < base + 0xF0000000 && !page; at += 0x10000) {
            page = static_cast<unsigned char*>(
                VirtualAlloc(reinterpret_cast<void*>(at), 0x1000, MEM_RESERVE | MEM_COMMIT,
                             PAGE_EXECUTE_READWRITE));
        }
        if (!page) return nullptr;
    }
    unsigned char* stub = page + used;
    used += 16;
    const unsigned char jmp[6] = {0xFF, 0x25, 0, 0, 0, 0};
    std::memcpy(stub, jmp, sizeof(jmp));
    std::memcpy(stub + 6, &target, sizeof(target));
    return stub;
}

bool PatchExports(HMODULE shell32) {
    auto base = reinterpret_cast<std::uintptr_t>(shell32);
    auto* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    auto* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    const auto& dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT];
    auto* exports = reinterpret_cast<IMAGE_EXPORT_DIRECTORY*>(base + dir.VirtualAddress);
    auto* names = reinterpret_cast<DWORD*>(base + exports->AddressOfNames);
    auto* ordinals = reinterpret_cast<WORD*>(base + exports->AddressOfNameOrdinals);
    auto* functions = reinterpret_cast<DWORD*>(base + exports->AddressOfFunctions);
    int patched = 0;
    for (DWORD i = 0; i < exports->NumberOfNames; ++i) {
        const char* name = reinterpret_cast<const char*>(base + names[i]);
        for (Redirect& r : g_redirects) {
            if (std::strcmp(name, r.name) != 0) continue;
            void* stub = StubAbove(base, nt->OptionalHeader.SizeOfImage, r.hook);
            DWORD* slot = &functions[ordinals[i]];
            DWORD old = 0;
            if (!stub || !VirtualProtect(slot, sizeof(DWORD), PAGE_READWRITE, &old)) continue;
            *slot = static_cast<DWORD>(reinterpret_cast<std::uintptr_t>(stub) - base);
            VirtualProtect(slot, sizeof(DWORD), old, &old);
            ++patched;
        }
    }
    Log("export table: %d of 3 entries repointed", patched);
    return patched == 3;
}

// Repoints every import slot in `module` that holds one of the real functions.
int PatchImports(HMODULE module) {
    auto base = reinterpret_cast<std::uintptr_t>(module);
    auto* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    auto* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    const auto& dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress) return 0;
    int patched = 0;
    for (auto* desc = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base + dir.VirtualAddress);
         desc->Name; ++desc) {
        for (auto* thunk = reinterpret_cast<IMAGE_THUNK_DATA*>(base + desc->FirstThunk);
             thunk->u1.Function; ++thunk) {
            for (Redirect& r : g_redirects) {
                if (reinterpret_cast<void*>(thunk->u1.Function) != r.real) continue;
                DWORD old = 0;
                if (!VirtualProtect(&thunk->u1.Function, sizeof(void*), PAGE_READWRITE, &old)) continue;
                thunk->u1.Function = reinterpret_cast<ULONGLONG>(r.hook);
                VirtualProtect(&thunk->u1.Function, sizeof(void*), old, &old);
                ++patched;
            }
        }
    }
    return patched;
}

void PatchLoadedModules() {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, GetCurrentProcessId());
    if (snap == INVALID_HANDLE_VALUE) return;
    MODULEENTRY32W entry = {sizeof(entry)};
    for (BOOL ok = Module32FirstW(snap, &entry); ok; ok = Module32NextW(snap, &entry)) {
        const int n = PatchImports(entry.hModule);
        if (n) Log("import table: %d slot(s) in %ls", n, entry.szModule);
    }
    CloseHandle(snap);
}

// Mutes this process's audio session on the default output device; the HRESULT.
HRESULT MuteSession() {
    IMMDeviceEnumerator* devices = nullptr;
    IMMDevice* device = nullptr;
    IAudioSessionManager* sessions = nullptr;
    ISimpleAudioVolume* volume = nullptr;
    HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                  IID_PPV_ARGS(&devices));
    if (SUCCEEDED(hr)) hr = devices->GetDefaultAudioEndpoint(eRender, eConsole, &device);
    if (SUCCEEDED(hr)) hr = device->Activate(__uuidof(IAudioSessionManager), CLSCTX_ALL, nullptr,
                                             reinterpret_cast<void**>(&sessions));
    if (SUCCEEDED(hr)) hr = sessions->GetSimpleAudioVolume(nullptr, FALSE, &volume);
    if (SUCCEEDED(hr)) hr = volume->SetMute(TRUE, nullptr);
    for (IUnknown* p : {static_cast<IUnknown*>(volume), static_cast<IUnknown*>(sessions),
                        static_cast<IUnknown*>(device), static_cast<IUnknown*>(devices)}) {
        if (p) p->Release();
    }
    return hr;
}

// Keeps the game silent at the OS level whatever its own settings say: the
// session is re-muted every 2 s, which also covers a late-opened stream or a
// change of default output device. COM is not allowed in DllMain, hence the thread.
DWORD WINAPI MuteThread(LPVOID) {
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    HRESULT last = S_FALSE;
    for (;;) {
        const HRESULT hr = MuteSession();
        if (hr != last) Log("audio session mute: hr=0x%08lX", static_cast<unsigned long>(hr));
        last = hr;
        Sleep(2000);
    }
}

bool ReadFolder(const wchar_t* variable, wchar_t* out) {
    const DWORD n = GetEnvironmentVariableW(variable, out, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) out[0] = 0;
    return out[0] != 0;
}

void Install() {
    const bool docs = ReadFolder(L"TES_SANDBOX_DOCUMENTS", g_documents);
    ReadFolder(L"TES_SANDBOX_LOCALAPPDATA", g_localAppData);
    if (docs) {
        wchar_t logPath[MAX_PATH];
        swprintf_s(logPath, L"%ls\\TESSandbox.log", g_documents);
        g_log = _wfsopen(logPath, L"w", _SH_DENYWR);
    }
    HMODULE shell32 = GetModuleHandleW(L"shell32.dll");
    if (!shell32) shell32 = LoadLibraryW(L"shell32.dll");
    g_realA = reinterpret_cast<FolderPathA>(GetProcAddress(shell32, "SHGetFolderPathA"));
    g_realW = reinterpret_cast<FolderPathW>(GetProcAddress(shell32, "SHGetFolderPathW"));
    g_realKnown = reinterpret_cast<KnownFolderPath>(GetProcAddress(shell32, "SHGetKnownFolderPath"));
    if (!g_realA || !g_realW || !g_realKnown) {
        Log("shell32 exports missing; nothing redirected");
        return;
    }
    g_redirects[0] = {"SHGetFolderPathA", reinterpret_cast<void*>(&HookFolderPathA), reinterpret_cast<void*>(g_realA)};
    g_redirects[1] = {"SHGetFolderPathW", reinterpret_cast<void*>(&HookFolderPathW), reinterpret_cast<void*>(g_realW)};
    g_redirects[2] = {"SHGetKnownFolderPath", reinterpret_cast<void*>(&HookKnownFolderPath),
                      reinterpret_cast<void*>(g_realKnown)};
    wchar_t mute[8] = {};
    if (GetEnvironmentVariableW(L"TES_SANDBOX_MUTE", mute, 8) && mute[0] == L'1') {
        CloseHandle(CreateThread(nullptr, 0, MuteThread, nullptr, 0, nullptr));
    }
    Log("Documents -> %ls", g_documents);
    Log("LocalAppData -> %ls", g_localAppData);
    PatchExports(shell32);
    PatchLoadedModules();
}

}  // namespace

BOOL WINAPI DllMain(HINSTANCE, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) Install();
    return TRUE;
}
