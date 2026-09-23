// spt_engine_dump - drive Oblivion.exe's statically-linked SpeedTreeRT 4.x
// directly and dump the geometry it generates.
//
// Why: the converter (asset_convert/spt_generator.py) must reproduce the
// engine's tree EXACTLY.  Comparing against billboard renders is a 2-D proxy;
// the only ground truth is the engine's own vertex buffers.  Oblivion.exe has
// SpeedTreeRT linked in with symbols intact, so we map the image at its fixed
// base and call CSpeedTreeRT::LoadTree / Compute / GetGeometry ourselves.
// The game never runs: we never touch its entry point, only the SpeedTree
// functions, which are self-contained (they use only the CRT heap and rand()).
//
// The image has RELOCS_STRIPPED and a fixed ImageBase of 0x400000, so it must
// be mapped there; the process is 32-bit for the same reason.
//
// Read-only interoperability analysis. Nothing is patched or redistributed.
//
// Build: see build.bat (needs the x86 MSVC toolchain).
#include <windows.h>
#include <cstdio>
#include <cstdarg>
#include <float.h>
#include <io.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <vector>
#include <string>

// ---- addresses recovered in docs/speedtree_engine_decomp.md ---------------
static const uintptr_t VA_CTOR        = 0x78d6a0;  // CSpeedTreeRT::CSpeedTreeRT()
static const uintptr_t VA_LOADTREE    = 0x78df90;  // __thiscall (void* buf, int len)
static const uintptr_t VA_COMPUTE     = 0x78cca0;  // CSpeedTreeRT::Compute
static const uintptr_t VA_GETGEOMETRY = 0x78c6f0;  // CSpeedTreeRT::GetGeometry
static const uintptr_t VA_SETTREESIZE = 0x78b0e0;  // CSpeedTreeRT::SetTreeSize
static const uintptr_t IMAGE_BASE     = 0x400000;

static void* g_image = nullptr;
static void logf(const char* fmt, ...);
static HANDLE g_crtheap = nullptr;

// The mapped image must land at 0x400000 (Oblivion.exe has its relocations
// stripped), but by the time ANY of our code runs -- even a custom /ENTRY
// ahead of the CRT -- WoW64 and the loader have already mapped NLS tables,
// heaps and stacks across that range, so VirtualAlloc there fails with
// ERROR_INVALID_ADDRESS (487).
//
// The one thing laid out before all of that is the exe image itself.  So the
// host reserves the range as part of its OWN image: an uninitialised section
// (.oblimg, no file bytes) sized so that, with the host linked at /BASE:
// 0x200000 (build.bat), it spans 0x400000..0x400000+ORIGINAL_IMAGE_MAX
// whatever the size of the sections the linker puts ahead of it.
// map_image checks that and copies Oblivion.exe into it.
//
// This replaced relaunching ourselves suspended and reserving the range with
// VirtualAllocEx: that is the process-hollowing pattern, and Defender
// quarantined the exe as Trojan:Win32/Wacatac.B!ml for it.
static const size_t ORIGINAL_IMAGE_MAX = 0x900000;  // > any Oblivion.exe SizeOfImage
static const size_t IMAGE_SPACE        = 0xB00000;  // 0x200000 slack for the host
#pragma bss_seg(".oblimg")
static uint8_t g_image_space[IMAGE_SPACE];
#pragma bss_seg()

// Give each mapped section the protection its own header asks for, as the
// Windows loader would.  Later code patches (write_jmp etc.) VirtualProtect
// around themselves, so nothing needs the image left writable+executable.
static void protect_sections(uint8_t* mem, IMAGE_NT_HEADERS32* nt)
{
    IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; ++i) {
        DWORD size = sec[i].Misc.VirtualSize ? sec[i].Misc.VirtualSize
                                             : sec[i].SizeOfRawData;
        if (!size) continue;
        DWORD c = sec[i].Characteristics;
        bool x = (c & IMAGE_SCN_MEM_EXECUTE) != 0;
        bool w = (c & IMAGE_SCN_MEM_WRITE) != 0;
        DWORD prot = x ? (w ? PAGE_EXECUTE_READWRITE : PAGE_EXECUTE_READ)
                       : (w ? PAGE_READWRITE : PAGE_READONLY);
        DWORD old = 0;
        if (!VirtualProtect(mem + sec[i].VirtualAddress, size, prot, &old))
            logf("[spt_engine]   protect sec %d failed (err %lu)\n", i,
                 GetLastError());
    }
}

static bool map_image(const char* path)
{
    FILE* f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return false; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> raw(sz);
    if (fread(raw.data(), 1, sz, f) != (size_t)sz) { fclose(f); return false; }
    fclose(f);

    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)raw.data();
    IMAGE_NT_HEADERS32* nt = (IMAGE_NT_HEADERS32*)(raw.data() + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) { fprintf(stderr, "not a PE\n"); return false; }
    if (nt->OptionalHeader.ImageBase != IMAGE_BASE) {
        fprintf(stderr, "unexpected image base 0x%x\n", nt->OptionalHeader.ImageBase);
        return false;
    }
    DWORD image_size = nt->OptionalHeader.SizeOfImage;

    // Must land exactly at 0x400000: relocations are stripped.  The range is
    // our own zero-filled, read-write .oblimg section; protect_sections()
    // applies each section's own protection once the imports are bound.
    uintptr_t space = (uintptr_t)g_image_space;
    if (image_size > ORIGINAL_IMAGE_MAX || space > IMAGE_BASE ||
        space + IMAGE_SPACE < IMAGE_BASE + image_size) {
        fprintf(stderr, ".oblimg 0x%x..0x%x does not cover 0x%x..0x%x "
                "(check /BASE in build.bat)\n", (unsigned)space,
                (unsigned)(space + IMAGE_SPACE), (unsigned)IMAGE_BASE,
                (unsigned)(IMAGE_BASE + image_size));
        return false;
    }
    void* mem = (void*)IMAGE_BASE;
    logf("[spt_engine] mapping 0x%x bytes at 0x%p\n", (unsigned)image_size, mem);
    memcpy(mem, raw.data(), nt->OptionalHeader.SizeOfHeaders);
    IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; ++i) {
        if (!sec[i].SizeOfRawData) continue;
        logf("[spt_engine]   sec %d va=0x%x raw=0x%x size=0x%x\n", i,
             (unsigned)sec[i].VirtualAddress, (unsigned)sec[i].PointerToRawData,
             (unsigned)sec[i].SizeOfRawData);
        memcpy((uint8_t*)mem + sec[i].VirtualAddress,
               raw.data() + sec[i].PointerToRawData,
               sec[i].SizeOfRawData);
    }

    // Resolve imports: the SpeedTree code itself only needs the CRT, but the
    // import table must be valid for any call that reaches an imported thunk.
    logf("[spt_engine] sections copied\n");
    DWORD impRva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    if (impRva) {
        IMAGE_IMPORT_DESCRIPTOR* imp = (IMAGE_IMPORT_DESCRIPTOR*)((uint8_t*)mem + impRva);
        for (; imp->Name; ++imp) {
            const char* dll = (const char*)((uint8_t*)mem + imp->Name);
            logf("[spt_engine]   import %s\n", dll);
            HMODULE h = LoadLibraryA(dll);
            if (!h) { fprintf(stderr, "warn: LoadLibrary %s failed\n", dll); continue; }
            uint32_t* oft = (uint32_t*)((uint8_t*)mem +
                (imp->OriginalFirstThunk ? imp->OriginalFirstThunk : imp->FirstThunk));
            uint32_t* ft  = (uint32_t*)((uint8_t*)mem + imp->FirstThunk);
            for (; *oft; ++oft, ++ft) {
                FARPROC p = nullptr;
                if (*oft & 0x80000000u) {
                    p = GetProcAddress(h, (LPCSTR)(uintptr_t)(*oft & 0xffff));
                } else {
                    IMAGE_IMPORT_BY_NAME* ibn = (IMAGE_IMPORT_BY_NAME*)((uint8_t*)mem + *oft);
                    p = GetProcAddress(h, ibn->Name);
                }
                if (p) *ft = (uint32_t)(uintptr_t)p;
            }
        }
    }
    logf("[spt_engine] imports resolved\n");
    protect_sections((uint8_t*)mem, nt);
    g_image = mem;
    return true;
}

// ---- allocator redirection ------------------------------------------------
// We never run the image's entry point, so neither its CRT nor its custom heap
// manager (0xb02020) is initialised.  Both crash:
//   * CRT malloc  (0x9816f9) reads uninitialised heap globals 0xbaa2ac/0xbaabc0
//   * the heap's free path (0x401d57) calls through a vtable at [heap+0] which
//     is NULL, because .data maps zero-filled
//
// The engine only ever allocates through two public wrappers:
//     operator new    = 0x401f00
//     operator delete = 0x401f20
// Redirecting just those two to the HOST CRT is the smallest possible cut: the
// SpeedTree code itself is untouched, and allocation identity cannot affect the
// geometry it generates (no address is ever hashed or ordered on).
//
// __cdecl and the wrappers' own convention agree (both take args on the stack
// and leave cleanup to the caller: 0x401f00 ends in a plain `ret`).
// Allocate from the PROCESS heap, not the host CRT's private heap.  The image's
// own CRT is pointed at the process heap too (`_crtheap` = 0xbaa2ac), and it
// calls HeapSize/HeapFree on blocks that reach it, so both sides must use the
// SAME heap or those calls corrupt it (observed: STATUS_HEAP_CORRUPTION
// 0xC0000374 once _crtheap was set correctly).
// Instrumented operator new: logs every allocation the engine makes, so an
// absurd size (computed from an uninitialised field) is visible immediately
// instead of surfacing later as heap corruption.
static unsigned g_alloc_n = 0;
static bool g_alloc_check = true;
static bool g_alloclog = false;
static void* __cdecl host_operator_new(size_t n)
{
    // Use the HOST CRT's malloc, not raw HeapAlloc.  The image's CRT calls
    // _msize / _recalloc on these blocks (seen in the failure stack:
    // 0x981f95 _recalloc -> RtlSizeHeap), and those need a block the CRT
    // recognises.  A raw HeapAlloc pointer handed to _msize is exactly the
    // STATUS_HEAP_CORRUPTION we were chasing -- the pointer reaching
    // RtlSizeHeap was float data read out of an unrecognised header.
    if (g_alloclog && g_alloc_check && !HeapValidate(GetProcessHeap(), 0, nullptr)) {
        logf("[alloc] heap INVALID before allocation #%u (%u bytes)\n",
             g_alloc_n + 1, (unsigned)n);
        g_alloc_check = false;          // report once
    }
    void* p = malloc(n ? n : 1);
    ++g_alloc_n;
    if (g_alloclog && (g_alloc_n <= 100000 || n > (1u << 20)))
        logf("[alloc %4u] %zu bytes -> 0x%p%s\n", g_alloc_n, n, p,
             n > (1u << 20) ? "   <-- LARGE" : "");
    return p;
}
// Instrumented delete: every freed pointer is checked against the heap it was
// allocated from.  A free of a pointer this heap does not own is the classic
// cause of STATUS_HEAP_CORRUPTION appearing "randomly" later.
static unsigned g_free_n = 0;
static void  __cdecl host_operator_del(void* p)
{
    if (!p) return;
    ++g_free_n;
    // Must pair with host_operator_new's malloc: the host CRT owns the header.
    free(p);
}

// The image's CRT also calls _msize and _recalloc on blocks it believes it
// allocated (failure stack: 0x981f95 _recalloc -> _msize -> RtlSizeHeap).
// Point those at the HOST CRT too, so every block is created, measured,
// resized and released by one allocator.
// Entries resolved from the failure stack's return addresses (nearest called
// function below each): 0x981ed7 -> 0x981e9c, 0x981f95 -> 0x981f78.
static const uintptr_t VA_CRT_MSIZE    = 0x981e9c;
static const uintptr_t VA_CRT_RECALLOC = 0x981f78;

static size_t __cdecl host_msize(void* p) { return p ? _msize(p) : 0; }
static void*  __cdecl host_recalloc(void* p, size_t count, size_t size)
{
    return _recalloc(p, count, size);
}

static bool write_jmp(uintptr_t at, void* target)
{
    DWORD old = 0;
    if (!VirtualProtect((LPVOID)at, 5, PAGE_EXECUTE_READWRITE, &old)) return false;
    uint8_t* p = (uint8_t*)at;
    p[0] = 0xE9;                                        // jmp rel32
    *(int32_t*)(p + 1) = (int32_t)((uintptr_t)target - (at + 5));
    VirtualProtect((LPVOID)at, 5, old, &old);
    return true;
}

// The engine also calls the CRT's malloc/free DIRECTLY (not only through the
// game's operator new/delete), so those must be redirected too or blocks
// allocated by one allocator get freed by the other.
static const uintptr_t VA_CRT_MALLOC = 0x9816f9;
static const uintptr_t VA_CRT_FREE   = 0x9817bc;

static const uintptr_t VA_OPERATOR_NEW = 0x401f00;
static const uintptr_t VA_OPERATOR_DEL = 0x401f20;

// ---- minimal CRT bring-up -------------------------------------------------
// Some engine paths reach the image's own CRT, which faults because we never
// ran its startup: _getptd (0x98c072) calls through a NULL TLS slot, giving
// eip=0 with the return address 0x0098c095 on the stack (observed).
//
// __tmainCRTStartup (0x98769c) cannot be run wholesale -- it ends by calling
// the game's main().  But its prologue is the stock MSVC sequence, so we call
// only the three initialisers it invokes before any game code:
//     0x98d55e  _heap_init(1)
//     0x98c22e  _mtinit()      -- allocates the per-thread data _getptd wants
//     0x98d7bd  _RTC_Initialize()
// (call sites 0x98776d, 0x98777f, 0x987790).
typedef int  (__cdecl *fnHeapInit)(int);
typedef int  (__cdecl *fnMtInit)(void);
typedef void (__cdecl *fnRtcInit)(void);

static const uintptr_t VA_HEAP_INIT = 0x98d55e;
static const uintptr_t VA_MT_INIT   = 0x98c22e;
static const uintptr_t VA_RTC_INIT  = 0x98d7bd;

// _getptd (0x98c072) fetches the CRT's per-thread data block:
//     0x98c081  push [0xb310ac]        ; cookie
//     0x98c087  push [0xb310b0]        ; TLS index
//     0x98c08d  call TlsGetValue
//     0x98c093  call eax               ; <- decoded pointer, NULL here
// Every CRT path that touches errno lands here.  Rather than reproduce the
// whole CRT bootstrap (tried: _mtinit faults inside ntdll), give it a real TLS
// slot holding a zeroed 0x214-byte block -- the size _getptd itself allocates
// at 0x98c09b when the slot is empty.
static const uintptr_t VA_TLS_INDEX  = 0xb310b0;
static const uintptr_t VA_TLS_COOKIE = 0xb310ac;
static const size_t    PTD_SIZE      = 0x214;

// Bypass _getptd entirely: hand back one preallocated, zeroed block.  We are
// single-threaded, so a per-thread block and a global one are equivalent.
// _getptd is `push esi / push edi` then a plain `ret` at the end -- it takes NO
// arguments (the two pushes at 0x98c081/0x98c087 are arguments to the call at
// 0x98c08d, not to _getptd itself) and preserves esi/edi.  __stdcall with zero
// args gives the right `ret` form; the register preservation is handled by
// declaring it naked-free and touching nothing.
// The real _getptd pushes esi/edi and pops them before `ret`, so callers may
// rely on those being preserved.  A plain C function is free to clobber them,
// which smashed the caller's frame (detected by the HOST CRT's /GS check as a
// gsfailure -> ExitProcess(0xC000000D), which looked like an engine crash).
// __declspec(naked) gives exact control: save/restore and return in eax.
static void* g_ptd = nullptr;
static __declspec(naked) void host_getptd(void)
{
    __asm {
        push esi
        push edi
        mov  eax, g_ptd
        pop  edi
        pop  esi
        ret
    }
}

static const uintptr_t VA_GETPTD = 0x98c072;

// _invalid_parameter (0x984d3a -> 0x984c3e) is the CRT's "report and die" path.
// With a synthetic per-thread block the CRT trips its own validation, and the
// reporter then dispatches through an uninitialised handler pointer -- the
// observed jump to a random address (eax=ecx=edx all equal, esp unchanged).
// The real function ends `pop ebp / ret` (0x984d58) -- caller-cleanup, and the
// callers do `add esp,0x14` themselves (0x981c18).  So the replacement must NOT
// pop the arguments: __cdecl with ZERO declared parameters gives a bare `ret`.
// Declaring five __cdecl params also emits a bare `ret`, but stating it this way
// makes the contract explicit.
static const uintptr_t VA_INVALID_PARAM = 0x984d3a;
static __declspec(naked) void host_invalid_parameter(void)
{
    __asm { ret }
}

// Every engine function is compiled with /GS and /EHa:
//     push -1 / push <scope table> / mov eax, fs:[0] / push eax
//     mov eax, [0xb30aac]   <- __security_cookie
//     xor eax, ebp ; push eax
// __security_cookie is normally set by __security_init_cookie() at CRT startup.
// Left at 0, the frame guard and the SEH unwind data disagree and the process
// is terminated with STATUS_INVALID_PARAMETER (0xC000000D) -- which no SEH
// handler can intercept, because it is raised BY the unwinder.
static const uintptr_t VA_SECURITY_COOKIE     = 0xb30aac;
static const uint32_t  DEFAULT_SECURITY_COOKIE = 0xBB40E64E;  // MSVC's default

// __security_check_cookie (0x9811e2) does NOT raise a catchable exception on a
// mismatch -- it calls __report_gsfailure, which terminates the process with
// STATUS_INVALID_PARAMETER (0xC000000D).  That is exactly the failure observed:
// no SEH handler fires, and the exit code is 0xC000000D.
//
// The frame guard compares a value XOR'd with the frame pointer against the
// global cookie.  Reproducing consistent values across every engine frame is
// not feasible from outside, so the check itself is turned into a no-op: it is
// a debugging aid for a running game, not part of tree generation.
static const uintptr_t VA_SECURITY_CHECK  = 0x9811e2;
static const uintptr_t VA_CRT_HEAP_HANDLE = 0xba9d94;  // set at 0x987744

static void init_security_cookie()
{
    // The image's own __security_init_cookie normally derives this.  Any
    // consistent value works, because every frame both stores and checks with
    // the SAME global -- what matters is that it does not change mid-run.
    *(uint32_t*)VA_SECURITY_COOKIE = DEFAULT_SECURITY_COOKIE;
    // Leave __security_check_cookie ALONE: patching it to `ret` was tried and
    // does not help, and it is __fastcall (value in ecx) so a bare ret is
    // correct only if nothing downstream depends on the check running.

    // 🛑 THE ACTUAL BLOCKER, found by single-step tracing rather than guessing.
    // 0x981bf8 is _get_heap_handle: it returns the global CRT heap handle at
    // 0xba9d94, and calls _invalid_parameter when that global is zero
    // (0x981c20 loads it, 0x981c27 branches to the error path on 0).  The
    // handle is normally stored by __tmainCRTStartup at 0x987744.
    //
    // The measured trace ran:
    //   0x99cbcf -> 0x981bf8 -> 0x981c20 -> 0x981c27 (zero) -> 0x981c03
    //   -> _errno (0x98540b) -> _getptd -> _invalid_parameter (0x984d3a)
    //   -> __report_gsfailure -> ExitProcess(0xC000000D)
    //
    // Both 0xba9d94 (_get_heap_handle) and 0xbaa2ac (_crtheap) must name the
    // SAME heap -- blocks allocated through one are freed through the other.
    // They are set together below, once the private heap exists.

    // The CRT stores several function pointers ENCODED and decodes them before
    // use (__crt_fast_encode/decode_pointer at 0x98be7f / 0x98beeb).  Both read
    // the cookie at 0xb310ac and, when it is exactly -1, return the pointer
    // UNCHANGED (0x98be97 / 0x98bf03 branch straight to the pass-through exit).
    // Left at 0, decoding scrambles a valid pointer and the CRT calls into
    // garbage -- the second failure observed, a call to 0xde971e68 dispatched
    // from 0x99cc34.
    // Both routines start `TlsGetValue([0xb310b0])` and take the pass-through
    // exit when it returns 0.  Point the index at a fresh TLS slot we never
    // populate, so the slot reads 0 and encode/decode become identity.
    *(uint32_t*)VA_TLS_COOKIE = 0xFFFFFFFFu;
    *(uint32_t*)VA_TLS_INDEX  = TlsAlloc();
    logf("[spt_engine] pointer encoding: cookie=0x%08x tls_index=%u"
         " (identity)\n",
         *(uint32_t*)VA_TLS_COOKIE, *(uint32_t*)VA_TLS_INDEX);

    // 0x99cbe7-0x99cc53 is the CRT's "is this a windowed process?" probe: it
    // GetProcAddress()es GetProcessWindowStation / GetUserObjectInformationA,
    // stores them ENCODED in 0xbaa76c / 0xbaa770, then decodes and calls them.
    // Those slots hold values encoded by a startup we never ran, so decoding
    // yields a different garbage address every run (observed: 0xde971e68,
    // 0x70c49643, 0xfc580b9e -- all dispatched from 0x99cc34).
    //
    // Zeroing both slots makes the guard at 0x99cc21/0x99cc25 take the skip
    // branch to 0x99cc92, bypassing the probe entirely.  It only decides
    // whether the CRT may pop a message box on error, which is irrelevant here.
    // Zeroing the slots is not enough: 0x99cbed-0x99cc14 REPOPULATES them from
    // GetProcAddress just before the check.  Patch the decision instead --
    // 0x99cc19 loads 0xbaa76c and 0x99cc23 jumps to the skip target 0x99cc92
    // when it matches; replace the whole block with an unconditional jump.
    {
        DWORD o2 = 0;
        if (VirtualProtect((LPVOID)0x99cc19, 5, PAGE_EXECUTE_READWRITE, &o2)) {
            uint8_t* p = (uint8_t*)0x99cc19;
            p[0] = 0xE9;                                   // jmp rel32
            *(int32_t*)(p + 1) = (int32_t)(0x99cc92 - (0x99cc19 + 5));
            VirtualProtect((LPVOID)0x99cc19, 5, o2, &o2);
            logf("[spt_engine] window-station probe skipped"
                 " (0x99cc19 -> 0x99cc92)\n");
        }
    }

    // 0x99ccb0-0x99cce1 is the CRT's error REPORTER: it decodes a handler from
    // 0xbaa760 and calls it (`0x99ccd5 call _decode_pointer` / `0x99ccdb call
    // eax`).  That slot was encoded by a startup we never ran, so the call
    // lands on garbage -- the last address in the trace.  Make the reporter a
    // no-op: it only formats a diagnostic, and we already log the real state.
    // 🛑 R6030 "CRT not initialized".  0xbaa2ac is the CRT's "heap/CRT is
    // initialised" flag: 0x98c924 tests it and, when zero, calls
    // _amsg_exit(0x1e) -> abort (0x98c938 / 0x98c93d in the trace).  That is
    // the message box the user saw on screen.
    //
    // Every other piece the CRT needs is already supplied above (heap handle,
    // per-thread block, pointer-encoding identity), so set the flag to say so.
    // 0xbaa2ac is the CRT's HEAP HANDLE (`_crtheap`), not a boolean:
    //   0x98c924  cmp [0xbaa2ac], ebx      ; zero  => "CRT not initialised"
    //                                      ;           -> _amsg_exit(0x1e), the
    //                                      ;           R6030 message box
    //   0x981b28  push [0xbaa2ac]          ; ...and it is passed to HeapSize
    //             call [0xa281a0]
    // Setting it to 1 silenced R6030 but then faulted inside ntdll reading
    // address 0x9 -- a bogus heap handle.  The process heap satisfies both
    // uses.
    // Give it a PRIVATE heap, not the process heap: the host CRT also uses the
    // process heap, and the image's CRT calls HeapSize/HeapValidate on blocks
    // it believes it owns.  A dedicated heap guarantees the two allocators can
    // never see each other's blocks.
    // HEAP_GENERATE_EXCEPTIONS makes a bad block raise at the failing call
    // rather than silently corrupting and blowing up later somewhere else.
    // (gflags page-heap would be stronger but needs a global registry change on
    // the user's machine, so it is deliberately not used.)
    // Turn OFF heap termination-on-corruption for this process.  By default
    // Windows raises STATUS_HEAP_CORRUPTION as a FAIL-FAST, which no SEH
    // handler can intercept -- so our __except never ran and every diagnostic
    // was lost.  With it disabled the same condition arrives as a normal,
    // catchable exception and the post-mortem below can run.
    {
        ULONG mode = 0;   // 0 = do NOT terminate on corruption
        HeapSetInformation(nullptr, HeapEnableTerminationOnCorruption,
                           &mode, sizeof(mode));
    }
    // HEAP_GENERATE_EXCEPTIONS turns allocation FAILURE into an exception, but
    // it also puts the heap into the checked path whose corruption detection is
    // a fail-fast.  Plain flags keep the heap quiet so we reach the real error.
    HANDLE crtheap = HeapCreate(0, 1u << 20, 0);
    g_crtheap = crtheap;
    *(uint32_t*)VA_CRT_HEAP_HANDLE = (uint32_t)(uintptr_t)crtheap;
    *(uint32_t*)0xbaa2ac           = (uint32_t)(uintptr_t)crtheap;
    logf("[spt_engine] _crtheap = 0x%08x (private)\n", *(uint32_t*)0xbaa2ac);

    // 0xbaabc0 selects the allocator strategy: free() checks `cmp [0xbaabc0],3`
    // at 0x9817cf and, when it matches, walks CRT-private bookkeeping
    // (0x98ca4c / 0x98ca77) that a hand-initialised CRT does not have.
    // Anything other than 3 routes straight to HeapFree(_crtheap, ...), which
    // is what we want.  malloc reads the same global at 0x981734.
    *(uint32_t*)0xbaabc0 = 1;
    logf("[spt_engine] CRT allocator strategy = 1 (plain heap)\n");

    // 🛑 ALLOC/FREE MISMATCH -- measured, not guessed.  In the trace the game's
    // allocator wrapper (0x401aa0) ran 32 times and took the CRT-malloc
    // fallthrough (0x401aa9) EVERY time, because the pool object at 0xb02020
    // is zero-filled and `cmp [esi+0xc], 0` at 0x401aa3 selects that path.
    // But its free (0x401d40) took the POOL path all 13 times (0x401d57),
    // dispatching through `[[esi]+0x14]` -- a NULL vtable in zeroed .data.
    // Freeing CRT-malloc blocks through that path is what corrupts the heap
    // (STATUS_HEAP_CORRUPTION 0xC0000374 inside Compute).
    //
    // The layout is:
    //   0x401d50  mov ecx,[esi+0xc]      ; pool size, 0 here
    //   0x401d53  test ecx,ecx
    //   0x401d55  jne 0x401d66           ; pool live -> range check
    //   0x401d57  mov eax,[esi]          ; <- taken: NULL vtable dispatch
    //   0x401d59  mov edx,[eax+0x14]
    //   0x401d5f  call edx
    //   0x401d78  xor bl,bl              ; "not ours" -> plain CRT free
    // Replace the conditional jump with an unconditional `jmp 0x401d78`
    // (2 bytes, eb <rel8>) so free() always reaches the CRT path that matches
    // how these blocks were allocated.
    // REVERTED: jumping 0x401d55 -> 0x401d78 skips the pool bookkeeping the
    // rest of the function still expects and produced a NEW fault inside the
    // allocator itself (esi = 0x401f33).  The mismatch is real, but it must be
    // fixed by giving the pool object at 0xb02020 valid state, not by
    // rerouting free()'s control flow.

    // The game's allocator takes a CRITICAL_SECTION at 0xb32b80 (entered via
    // 0x401020 from 0x401af1).  It lives in zero-filled .data and is normally
    // initialised during startup, so EnterCriticalSection faults writing +0x14.
    InitializeCriticalSection((LPCRITICAL_SECTION)0xb32b80);
    InitializeCriticalSection((LPCRITICAL_SECTION)0xb32c00);
    logf("[spt_engine] allocator critical sections initialised\n");

    // 🛑 Construct the game's heap object properly instead of rerouting free().
    // 0xa16400 is its static initialiser: it installs the vtable
    //     mov [0xb02020], 0xa2f810
    // and tail-calls the constructor at 0x401750.  With the vtable in place the
    // pool's own free path (`[[esi]+0x14]` at 0x401d59) dispatches to a REAL
    // function (0xa2f810+0x14 = 0x401490) instead of a NULL pointer, so alloc
    // and free finally agree.
    ((void(__cdecl*)(void))0xa16400)();
    logf("[spt_engine] game heap constructed, vtable=0x%08x\n",
         *(uint32_t*)0xb02020);

    // The CRT lazily builds a table of locks (0x98c910: allocate 0x18 bytes,
    // memset, register; 0x98c8fb: enter one).  Its backing store is never
    // initialised here, so RtlAllocateHeap faults reading 0x9.  We are strictly
    // single-threaded, so make lock/unlock no-ops rather than reproducing the
    // table.  Both are `__cdecl(int)` with caller cleanup, so a bare `ret` is
    // the correct replacement.
    {
        static const uintptr_t locks[] = { 0x98c8fb, 0x98c910, 0x98c9d3 };
        for (uintptr_t va : locks) {
            DWORD o2 = 0;
            if (VirtualProtect((LPVOID)va, 1, PAGE_EXECUTE_READWRITE, &o2)) {
                *(uint8_t*)va = 0xC3;
                VirtualProtect((LPVOID)va, 1, o2, &o2);
            }
        }
        logf("[spt_engine] CRT lock table bypassed (single-threaded)\n");
    }

    // `0x99ccdb: call eax` is the dispatch itself (2 bytes, ff d0).  Replacing
    // it with `xor eax,eax` (31 c0) makes the reporter return "no handler"
    // instead of jumping to a garbage address, without disturbing the stack.
    {
        DWORD o2 = 0;
        if (VirtualProtect((LPVOID)0x99ccdb, 2, PAGE_EXECUTE_READWRITE, &o2)) {
            ((uint8_t*)0x99ccdb)[0] = 0x31;   // xor eax, eax
            ((uint8_t*)0x99ccdb)[1] = 0xC0;
            VirtualProtect((LPVOID)0x99ccdb, 2, o2, &o2);
            logf("[spt_engine] CRT error-reporter dispatch neutralised\n");
        }
    }
    logf("[spt_engine] __security_cookie = 0x%08x, check byte now 0x%02x\n",
         *(uint32_t*)VA_SECURITY_COOKIE, *(uint8_t*)VA_SECURITY_CHECK);

    // Also neutralise the process-terminating exits the image can reach, so a
    // failure surfaces as a log line instead of a silent ExitProcess:
    //   0x98c49c __report_gsfailure   (from __security_check_cookie)
    //   0x9933a9 exit / _amsg_exit    (from _invalid_parameter's fallback)
    static const uintptr_t kills[] = { 0x98c49c, 0x9933a9 };
    for (uintptr_t va : kills) {
        DWORD o2 = 0;
        if (VirtualProtect((LPVOID)va, 1, PAGE_EXECUTE_READWRITE, &o2)) {
            *(uint8_t*)va = 0xC3;
            VirtualProtect((LPVOID)va, 1, o2, &o2);
            logf("[spt_engine]   neutralised terminator 0x%x\n", (unsigned)va);
        }
    }
}

static void init_image_tls()
{
    write_jmp(VA_INVALID_PARAM, (void*)host_invalid_parameter);
    logf("[spt_engine] _invalid_parameter neutralised\n");
    g_ptd = calloc(1, PTD_SIZE);
    // Initialise the block the way _getptd's own allocation path does
    // (0x98c0c2-0x98c0dc): [ptd+4] = -1, [ptd+0] = thread handle.
    // A wholly zeroed block leaves the SEH machinery reading a NULL
    // exception-registration chain.
    ((uint32_t*)g_ptd)[1] = 0xFFFFFFFFu;
    ((uint32_t*)g_ptd)[0] = (uint32_t)(uintptr_t)GetCurrentThread();
    bool ok = write_jmp(VA_GETPTD, (void*)host_getptd);
    uint8_t* p = (uint8_t*)VA_GETPTD;
    logf("[spt_engine] _getptd stub %s -> 0x%p; bytes now %02x %02x %02x %02x %02x\n",
         ok ? "ok" : "FAILED", g_ptd, p[0], p[1], p[2], p[3], p[4]);
    // Sanity-check the stub through the same path the engine will use.
    typedef void* (*fnG)(void);
    void* got = ((fnG)VA_GETPTD)();
    logf("[spt_engine] _getptd() returns 0x%p (expect 0x%p)\n", got, g_ptd);
}

static void init_image_crt()
{
    // _mtinit FIRST: it allocates the TLS index and the per-thread data block.
    // _heap_init already reaches _getptd (0x98c072 -> TlsGetValue -> call eax),
    // so running it before _mtinit faults with eip=0.  The stock CRT ordering
    // works because its _heap_init predates the TLS-backed errno path; ours
    // must be explicit.
    int m = ((fnMtInit)VA_MT_INIT)();
    logf("[spt_engine] _mtinit    -> %d\n", m);
    int h = ((fnHeapInit)VA_HEAP_INIT)(1);
    logf("[spt_engine] _heap_init -> %d\n", h);
    ((fnRtcInit)VA_RTC_INIT)();
    logf("[spt_engine] _RTC_Initialize done\n");
}

// ---- global construction --------------------------------------------------
// The engine's allocator keeps a global intrusive list head at 0xb4296c whose
// node pointer lives at 0xb42970.  0x784930 dereferences [0xb4296c + 4] and
// then [eax+4]; with .data mapped zero-filled that is a read from 0x00000004
// (the observed fault at 0x784935).
//
// 0xa10c00 is the C++ static initialiser that builds it -- it allocates a node
// via 0x784840, stores it in 0xb42970, and self-links next/prev/head:
//     [node+4] = node;  [node] = node;  [node+8] = node;  [node+0x2d] = 1
// It also registers an atexit destructor, which we skip.
//
// We run ONLY this initialiser rather than the whole __xc_a..__xc_z table:
// most of the other 7,000+ entries construct D3D, file-system and UI globals
// that need a real game boot, and none of them are on the SpeedTree path.
static const uintptr_t VA_ALLOC_LIST_INIT = 0xa10c00;
static const uintptr_t VA_ALLOC_LIST_HEAD = 0xb4296c;
static const uintptr_t VA_ALLOC_LIST_NODE = 0xb42970;
static const uintptr_t VA_ALLOC_NEW_NODE  = 0x784840;

typedef void* (__thiscall *fnNewNode)(void* self);

// Build the allocator list head without running the initialiser's atexit
// registration (0x981fb4 is CRT _onexit, which needs an initialised CRT).
static void init_allocator_globals()
{
    void* node = ((fnNewNode)VA_ALLOC_NEW_NODE)((void*)VA_ALLOC_LIST_HEAD);
    *(uint32_t*)VA_ALLOC_LIST_NODE = (uint32_t)(uintptr_t)node;
    uint8_t* n = (uint8_t*)node;
    *(uint32_t*)(n + 0x00) = (uint32_t)(uintptr_t)node;   // 0xa10c20
    *(uint32_t*)(n + 0x04) = (uint32_t)(uintptr_t)node;   // 0xa10c18
    *(uint32_t*)(n + 0x08) = (uint32_t)(uintptr_t)node;   // 0xa10c27
    *(uint8_t*) (n + 0x2d) = 1;                           // 0xa10c0f
    *(uint32_t*)(VA_ALLOC_LIST_NODE + 4) = 0;             // 0xa10c2f
    logf("[spt_engine] allocator list node = 0x%p\n", node);
}

// Report the faulting address instead of dying silently: when driving code
// this way the exact VA is the whole diagnosis.
// Diagnostics go to a log file as well as stderr: the child process's stderr
// can be swallowed by the shell, and losing the faulting VA loses the whole
// diagnosis.
static FILE* g_log = nullptr;
static void logf(const char* fmt, ...)
{
    va_list ap;
    va_start(ap, fmt); vfprintf(stderr, fmt, ap); va_end(ap);
    if (g_log) { va_start(ap, fmt); vfprintf(g_log, fmt, ap); va_end(ap); fflush(g_log); }
}

static LONG WINAPI fault_reporter(EXCEPTION_POINTERS* ep)
{
    // Breakpoints and single-steps belong to the stage probes / tracer, which
    // are registered later and would otherwise never see them.
    DWORD code = ep->ExceptionRecord->ExceptionCode;
    if (code == EXCEPTION_BREAKPOINT || code == EXCEPTION_SINGLE_STEP)
        return EXCEPTION_CONTINUE_SEARCH;
    logf("\n[spt_engine] EXCEPTION 0x%08lx at 0x%08x\n",
            ep->ExceptionRecord->ExceptionCode,
            (unsigned)(uintptr_t)ep->ExceptionRecord->ExceptionAddress);
    if (ep->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION &&
        ep->ExceptionRecord->NumberParameters >= 2) {
        logf("           %s address 0x%08x\n",
                ep->ExceptionRecord->ExceptionInformation[0] ? "write to" : "read from",
                (unsigned)ep->ExceptionRecord->ExceptionInformation[1]);
    }
    // A call through a NULL pointer lands at eip=0 with the RETURN address on
    // top of the stack -- print it, that is the real site.
    // Faulting outside the image means we were dispatched through a bad
    // pointer; the return address on top of the stack names the call site.
    // Probe the stack before reading it -- it may itself be the bad pointer.
    uintptr_t eip = ep->ContextRecord->Eip;
    if (eip < IMAGE_BASE || eip >= IMAGE_BASE + ORIGINAL_IMAGE_MAX) {
        uint32_t* sp = (uint32_t*)(uintptr_t)ep->ContextRecord->Esp;
        if (!IsBadReadPtr(sp, 6 * sizeof(uint32_t))) {
            logf("           dispatched from:\n");
            for (int i = 0; i < 6; ++i) {
                uint32_t v = sp[i];
                if (v >= IMAGE_BASE && v < IMAGE_BASE + ORIGINAL_IMAGE_MAX)
                    logf("             esp+%02x = 0x%08x  <- in image\n", i * 4, v);
            }
        }
    }
    logf("           eip=0x%08x esp=0x%08x ebp=0x%08x\n"
                    "           eax=0x%08x ecx=0x%08x edx=0x%08x esi=0x%08x edi=0x%08x\n",
            (unsigned)ep->ContextRecord->Eip, (unsigned)ep->ContextRecord->Esp,
            (unsigned)ep->ContextRecord->Ebp, (unsigned)ep->ContextRecord->Eax,
            (unsigned)ep->ContextRecord->Ecx, (unsigned)ep->ContextRecord->Edx,
            (unsigned)ep->ContextRecord->Esi, (unsigned)ep->ContextRecord->Edi);
    // EXCEPTION_CONTINUE_SEARCH: report only, let normal handling proceed.
    return EXCEPTION_CONTINUE_SEARCH;
}

static bool redirect_allocator()
{
    // Redirect CRT malloc/free as well: the engine reaches them directly, and
    // a block allocated by our operator new but freed by the image's CRT free
    // (or vice versa) is exactly the STATUS_HEAP_CORRUPTION we were chasing.
    write_jmp(VA_CRT_MALLOC,   (void*)host_operator_new);
    write_jmp(VA_CRT_FREE,     (void*)host_operator_del);
    write_jmp(VA_CRT_MSIZE,    (void*)host_msize);
    write_jmp(VA_CRT_RECALLOC, (void*)host_recalloc);
    bool a = write_jmp(VA_OPERATOR_NEW, (void*)host_operator_new);
    logf("[spt_engine] patch operator new  -> %s\n", a ? "ok" : "FAILED");
    bool b = write_jmp(VA_OPERATOR_DEL, (void*)host_operator_del);
    logf("[spt_engine] patch operator del  -> %s\n", b ? "ok" : "FAILED");
    return a && b;
}

// ---- SGeometry::SBranchGeometry ------------------------------------------
// Recovered from the branch getter 0x789fe0: every store the getter makes into
// the caller's out-struct (edi).  Table in docs/speedtree_engine_decomp.md
// section 8.
#pragma pack(push, 1)
// Field roles CORRECTED by probing the live buffers (the earlier table came
// from reading the getter's stores and had coords/texcoords transposed).
// Measured on treeenglishoakforest01su, 10,019 verts:
//     +0x18/+0x1c/+0x20 -> all values in [-1,1]  = normal / binormal / tangent
//     +0x24             -> range -24.4 .. 135.4  = POSITIONS (world units)
//     +0x14             -> all NaN               = allocated but unused
struct SBranchGeometry {
    int32_t  nLodLevel;       // +0x00  observed 0
    uint16_t usNumLodLevels;  // +0x04  observed 33
    uint16_t _pad04;
    // MEASURED: +0x08 holds the LENGTHS (small counts) and +0x0c the array
    // of index-array POINTERS -- the reverse of the getter-derived guess.
    // At runtime [+0x0c][i] are heap addresses (54486240, 17319200, ...)
    // while [+0x08][i] are plausible strip lengths.
    uint32_t pStripLengths;   // +0x08
    uint32_t pStrips;         // +0x0c
    uint16_t usVertexCount;   // +0x10  observed 0x2723 = 10019
    uint16_t _pad10;
    uint32_t pUnused14;       // +0x14  NaN-filled
    uint32_t pNormals;        // +0x18
    uint32_t pBinormals;      // +0x1c
    uint32_t pTangents;       // +0x20
    uint32_t pCoords;         // +0x24  <- POSITIONS
    uint32_t pTexCoords0;     // +0x28
    uint32_t pTexCoords1;     // +0x2c
    uint32_t pWindWeights;    // +0x30
    uint32_t pWindMatrices;   // +0x34
    float    fTreeHeight;     // +0x38  observed 84.0
    // The LEAF sub-struct begins at +0x78 of this same out-struct: the leaf
    // getter (0x788120, reached from GetGeometry's `test bl,4` dispatch) does
    // `lea ebx,[edi+0x78]` and hands that to the per-leaf-map filler 0x7989b0,
    // which is called once per billboard-leaf group ([tree+0xc0] groups).
    // 0x788225 stores a byte at +0xb4 and 0x788230 a float at +0x7c, so the
    // leaf region extends well past +0xb4 -- reserve generously and probe.
    uint8_t  reserved[0x400];
};
#pragma pack(pop)

// Number of leaf-map groups the engine will fill, read from the tree object.
// 0x788133: `mov edi,[eax+0xc0]` where eax = *(void**)self.
static inline unsigned leaf_group_count(void* self)
{
    uint8_t* tree = *(uint8_t**)self;
    if (!tree || IsBadReadPtr(tree, 0xc4)) return 0;
    return *(uint16_t*)(tree + 0xc0);
}

typedef void* (__thiscall *fnCtor)(void* self);
// Returns bool in al: 0x78e26d `mov al,[ebp-0x11]` then `ret 8`.  The parse
// failure path (0x78dfe9 -> 0x78e25d) returns false; ignoring it means
// running Compute on a half-built tree, whose spline pointers are garbage.
typedef bool (__thiscall *fnLoadTree)(void* self, const void* buf, unsigned len);
// CSpeedTreeRT::Compute(const float* transform, unsigned seed, bool bInstance)
//
// Signature recovered from the prologue, which is NOT the standard form:
//     0x78cca0  push ebp
//     0x78cca1  lea  ebp, [esp - 0x3c]      <- ebp is 0x40 BELOW entry esp
// so [ebp+0x44] = arg0, [ebp+0x48] = arg1, [ebp+0x4c] = arg2.  The seed is read
// from [ebp+0x48] at 0x78cce9 and handed to the seeding routine 0x7a24f0 --
// i.e. it is the SECOND parameter.  `ret 0xc` (0x78d136) fixes the count at 3.
//
// The earlier 6-argument guess passed nullptr where the seed belongs, so the
// engine seeded from a bogus value and generated garbage sizes.
typedef void (__thiscall *fnCompute)(void* self, const float* transform,
                                     unsigned seed, bool bInstance);
// CSpeedTreeRT::GetGeometry(SGeometry* out, unsigned flags, short, short, short)
// `ret 0x14` (0x78c7d1 and friends) fixes the count at 5 stack arguments.
// The flag bits are read at 0x78c720-0x78c76e: 1=branches, 2=fronds, 4=leaves,
// 8=billboards.
// The three LOD arguments are FLOAT FRACTIONS, not short indices.  0x78c72f
// loads [ebp+0x10] and hands it to 0x787c10, which does
//     idx = (int)((1.0 - fraction) * [tree+0x70])
// so 1.0 selects index 0 (finest) and 0.0 selects the coarsest.  Declaring
// them `short` pushed 2-byte integers where the callee reads 4-byte floats:
// the bit pattern for 0 is 0.0f, which asked for the COARSE level -- that is
// why shrubmugopine returned 6 strips covering 1.7% of its vertices.
typedef void (__thiscall *fnGetGeometry)(void* self, void* out, unsigned flags,
                                         float l0, float l1, float l2);

// SEH wrappers.  These must live in their own functions with no C++ objects:
// MSVC rejects __try in a function that requires object unwinding (C2712).
// The engine installs its own frame-based handlers, so SEH at the call site is
// the only reliable way to attribute a fault to a specific engine call.
// Run an engine call on a DEDICATED thread with a generous stack.
// The default 1 MB main-thread stack is shared with the host CRT; the engine's
// constructor builds several large stack frames (0x328+ bytes each, deeply
// nested) and blows it, which Windows reports as a silent process termination
// rather than a catchable exception.
struct CallCtx { void* self; const void* buf; unsigned len; unsigned seed;
                 void* out; int which; bool ok; bool result; };

// Single-step tracer: records the last N instruction addresses executed inside
// the mapped image.  When the process dies with no catchable exception, the
// trailing addresses are the only way to see WHERE, and guessing has already
// cost several rounds.
static bool     g_trace = false;
static uint32_t g_ring[64];
static unsigned g_ring_n = 0;
static uint32_t g_steps = 0;
static int      g_trace_fd = -1;
static bool     g_bisect = false;
static void report_stage(unsigned idx);
static bool stage_at(uint32_t eip);

struct StageHook {
    uintptr_t   va;
    const char* name;
};

static StageHook g_stages[] = {
    { 0x7a24f0, "seed setup" }, { 0x7a45f0, "tree size" },
    { 0x7a1cd0, "CFrondEngine::Compute" }, { 0x7a5740, "LOD setup" },
    { 0x793c00, "scale setup" }, { 0x799320, "stage7" },
    { 0x798550, "leaf quad setup" }, { 0x79a810, "billboard quads" },
    { 0x7977d0, "stage10" }, { 0x7a66b0, "stage11" },
    { 0x7997f0, "bbox accum" }, { 0x787480, "finalise" },
};
static const unsigned N_STAGES = sizeof(g_stages) / sizeof(g_stages[0]);

// Called from the tracer for every instruction; cheap comparison loop.
static bool stage_at(uint32_t eip)
{
    for (unsigned i = 0; i < N_STAGES; ++i)
        if (eip == (uint32_t)g_stages[i].va) { report_stage(i); return true; }
    return false;
}
static unsigned g_stage_hit = 0;

static void report_stage(unsigned idx)
{
    bool ok = g_crtheap ? (HeapValidate(g_crtheap, 0, nullptr) != FALSE) : true;
    bool okp = HeapValidate(GetProcessHeap(), 0, nullptr) != FALSE;
    logf("[stage %2u] %-24s crtheap=%s processheap=%s\n",
         idx, g_stages[idx].name, ok ? "ok" : "INVALID", okp ? "ok" : "INVALID");
    ++g_stage_hit;
}


// ---- INT3 stage probes ----------------------------------------------------
// The single-step tracer loses the trap flag inside the engine's SEH frames, so
// it never showed Compute's internals.  Instead put an `int3` (0xCC) at each
// stage entry and handle the breakpoint: log, restore the byte, rewind eip and
// continue.  This is exact and costs nothing between stages.
static uint8_t g_stage_orig[16];
static bool    g_stage_armed = false;
static volatile long g_int3_seen = 0;
static uint8_t g_stage_seen[64];
static bool    g_stage_valid[16];

static void arm_stage_probes(void);

static LONG WINAPI int3_stage_handler(EXCEPTION_POINTERS* ep)
{
    if (ep->ExceptionRecord->ExceptionCode != EXCEPTION_BREAKPOINT || !g_stage_armed)
        return EXCEPTION_CONTINUE_SEARCH;
    g_int3_seen++;
    uint32_t eip = (uint32_t)ep->ContextRecord->Eip - 1;   // int3 already retired
    for (unsigned i = 0; i < N_STAGES; ++i) {
        if (eip != (uint32_t)g_stages[i].va) continue;
        // Restore the original byte and rewind FIRST, so execution is already
        // consistent even if anything below misbehaves.
        DWORD o = 0;
        VirtualProtect((LPVOID)(uintptr_t)eip, 1, PAGE_EXECUTE_READWRITE, &o);
        *(uint8_t*)(uintptr_t)eip = g_stage_orig[i];
        VirtualProtect((LPVOID)(uintptr_t)eip, 1, o, &o);
        ep->ContextRecord->Eip = eip;
        // Record only; no formatting or file I/O from inside the handler.
        g_stage_seen[g_stage_hit++ & 63] = (uint8_t)i;
        g_stage_valid[i] = g_crtheap
            ? (HeapValidate(g_crtheap, 0, nullptr) != FALSE) : true;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static void arm_stage_probes(void)
{
    for (unsigned i = 0; i < N_STAGES; ++i) {
        uint8_t* p = (uint8_t*)g_stages[i].va;
        DWORD o = 0;
        if (!VirtualProtect(p, 1, PAGE_EXECUTE_READWRITE, &o)) continue;
        g_stage_orig[i] = p[0];
        p[0] = 0xCC;
        VirtualProtect(p, 1, o, &o);
    }
    g_stage_armed = true;
    AddVectoredExceptionHandler(1, int3_stage_handler);
    logf("[spt_engine] %u stage probes armed\n", N_STAGES);
}

static LONG WINAPI step_tracer(EXCEPTION_POINTERS* ep)
{
    if (ep->ExceptionRecord->ExceptionCode != EXCEPTION_SINGLE_STEP || !g_trace)
        return EXCEPTION_CONTINUE_SEARCH;
    uint32_t eip = (uint32_t)ep->ContextRecord->Eip;
    ++g_steps;

    // Bisect: when execution enters a known Compute() stage, validate the heap.
    // The first stage that reports INVALID on entry is the one AFTER the
    // corrupting stage.
    if (g_bisect) stage_at(eip);
    // Record ONLY addresses inside the mapped image.  Logging every step would
    // also trace our own logging code (and the ntdll it calls), which both
    // swamps the output and recurses.
    if (eip >= IMAGE_BASE && eip < IMAGE_BASE + ORIGINAL_IMAGE_MAX) {
        g_ring[g_ring_n++ & 63] = eip;
        // Write through: the process dies without unwinding, so anything left
        // buffered in memory is lost.
        if (g_trace_fd >= 0) {
            char line[16];
            int n = sprintf(line, "%08x\n", eip);
            _write(g_trace_fd, line, n);
        }
    }
    ep->ContextRecord->EFlags |= 0x100;      // keep TF set
    return EXCEPTION_CONTINUE_EXECUTION;
}

// Set EFLAGS.TF so the next instruction raises a single-step exception.
static void set_trap_flag(void)
{
    __asm {
        pushfd
        or   dword ptr [esp], 0x100
        popfd
    }
}

static void dump_trace(void)
{
    logf("[spt_engine] trace: %u steps, last addresses:\n", g_steps);
    unsigned start = g_ring_n > 64 ? g_ring_n - 64 : 0;
    for (unsigned i = start; i < g_ring_n; ++i)
        logf("    0x%08x\n", g_ring[i & 63]);
}

static DWORD WINAPI engine_thread(LPVOID p)
{
    CallCtx* c = (CallCtx*)p;
    __try {
        switch (c->which) {
        case 0: ((fnCtor)VA_CTOR)(c->self); break;
        case 1: c->result = ((fnLoadTree)VA_LOADTREE)(c->self, c->buf, c->len); break;
        case 2: {
            // The engine is x87 float code compiled for the game's FPU control
            // word.  MSVC's CRT starts in 53-bit precision with masked
            // exceptions; SpeedTree's spline/trig math produces NaNs under the
            // wrong rounding/precision state.  Set the classic Direct3D-era
            // word (24-bit precision, round-to-nearest, all exceptions masked)
            // that the game itself runs with, then restore.
            unsigned saved = _controlfp(0, 0);
            _controlfp(_PC_24 | _RC_NEAR, _MCW_PC | _MCW_RC);
            ((fnCompute)VA_COMPUTE)(c->self, nullptr, c->seed, false);
            _controlfp(saved, _MCW_PC | _MCW_RC);
            break;
        }
        // Branches: flag bit 1.  The third stack arg is a LOD FRACTION, not
        // an index -- 0x787c10 converts it (0x78a235):
        //     idx = (int)((1.0 - fraction) * [tree+0x70])   ; +0x70 = LOD count
        //     if (idx == count) --idx
        // Every Oblivion tree authors count = 2, so fraction 0.0 -> idx 2 ->
        // clamped to 1, and fraction 1.0 -> idx 0.  We were passing 0, i.e.
        // asking for the COARSE level: shrubmugopine returned 6 strips
        // covering 1.7% of its 7,016 vertices.  Fraction 1.0 selects index 0.
        case 3: ((fnGetGeometry)VA_GETGEOMETRY)(c->self, c->out, 1u,
                                                1.0f, 1.0f, 1.0f); break;
        // Leaves: flag bit 4 (0x78c74a `test bl,4` -> 0x788120).  The leaf LOD
        // is the FOURTH stack arg ([ebp+0x18], read at 0x78c74f); 0 selects
        // the highest-detail leaf level.
        // Leaf LOD is the arg at [esp+0x40] inside 0x788120, compared against
        // -1 at 0x7881ee (`or ebp,-1` / `cmp ax,bp` / `jle 0x78823d`).
        // Passing 0 takes the LOD-REDUCED branch: oak authors freq=400 on its
        // leaf level but returned only 572 leaves, and dbush03 freq=200
        // returned 100.  -1 selects full detail.
        case 4: ((fnGetGeometry)VA_GETGEOMETRY)(c->self, c->out, 4u,
                                                1.0f, 1.0f, 1.0f); break;
        }
        c->ok = true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        logf("[spt_engine] call %d raised 0x%08lx\n", c->which, GetExceptionCode());
        // Post-mortem: the global level vector is filled DURING Compute
        // (0x7a4742), so its state at failure time says how far it got.
        logf("[levels] at failure: begin=0x%08x end=0x%08x cap=0x%08x (%d entries)\n",
             *(uint32_t*)0xb429e0, *(uint32_t*)0xb429e4, *(uint32_t*)0xb429e8,
             (int)((*(uint32_t*)0xb429e4 - *(uint32_t*)0xb429e0) / 4));
        c->ok = false;
    }
    return 0;
}

// Run on the CURRENT thread.  A dedicated thread was tried and made things
// worse: several DLLs are injected into this process by the system and by
// third-party tooling (CoreMessaging, inputhost, and Windhawk's engine +
// mods were all observed loaded), and creating a thread runs their
// DLL_THREAD_ATTACH handlers, one of which kills the process before the
// engine call is even reached (the log stops exactly at the dispatch).
static bool run_on_engine_thread(CallCtx* c)
{
    engine_thread(c);
    return c->ok;
}

static bool guarded_ctor(void* self)
{
    logf("[spt_engine] >> ctor\n");
    // SPT_ENGINE_WAIT=<seconds>: hold here after the image is mapped so a
    // debugger can attach to THIS process and set breakpoints on engine
    // addresses (which do not exist until the image is mapped).
    char wait[16] = {0};
    if (GetEnvironmentVariableA("SPT_ENGINE_WAIT", wait, sizeof(wait))) {
        logf("[spt_engine] waiting %s s for a debugger (pid %lu)\n",
             wait, GetCurrentProcessId());
        Sleep((DWORD)(atoi(wait) * 1000));
    }
    // Probe: is the constructor's first instruction even reachable/executable?
    logf("[spt_engine] ctor first bytes: %02x %02x %02x %02x %02x\n",
         ((uint8_t*)VA_CTOR)[0], ((uint8_t*)VA_CTOR)[1], ((uint8_t*)VA_CTOR)[2],
         ((uint8_t*)VA_CTOR)[3], ((uint8_t*)VA_CTOR)[4]);

    // Smoke test: call a LEAF engine function with no SEH frame first
    // (0x7a66f0 is `mov eax,ecx / mov byte [eax+0x30],0 / ret`).  If this
    // works, plain engine code executes fine and the problem is specific to
    // SEH-framed functions rather than to the mapping.
    {
        uint8_t probe[0x40] = {0};
        typedef void* (__thiscall *fnLeaf)(void*);
        void* r = ((fnLeaf)0x7a66f0)(probe);
        logf("[spt_engine] leaf-call probe returned 0x%p (self=0x%p) flag=%u\n",
             r, probe, probe[0x30]);
    }
    CallCtx c{}; c.self = self; c.which = 0;
    logf("[spt_engine] dispatching to engine thread\n");
    if (GetEnvironmentVariableA("SPT_ENGINE_TRACE", nullptr, 0)) {
        g_trace_fd = _open("spt_engine_trace.txt",
                           _O_WRONLY | _O_CREAT | _O_TRUNC | _O_BINARY, _S_IWRITE);
        AddVectoredExceptionHandler(1, step_tracer);
        g_trace = true;
        g_bisect = GetEnvironmentVariableA("SPT_ENGINE_BISECT", nullptr, 0) != 0;
        set_trap_flag();
    }
    bool r = run_on_engine_thread(&c);
    g_trace = false;
    if (g_steps) dump_trace();
    if (!r) return false;
    logf("[spt_engine] ctor ok, inner tree=0x%08x\n", *(uint32_t*)self);
    return true;
}

static bool guarded_loadtree(void* self, const void* buf, unsigned len)
{
    // The ctor builds default splines via 0x78d894 -> 0x7a13b0 -> 0x786d60.
    // If that never completed, level spline pointers stay garbage and the
    // branch builder's spline eval (0x7926a3) reallocs a bogus pointer.
    // The branch builder indexes a GLOBAL level-pointer vector at
    // 0xb429e0 (begin) / 0xb429e4 (end) / 0xb429e8 (capacity), filled during
    // Compute by the tree-size stage (0x7a4742-0x7a4747).  If begin/capacity
    // are null the fill writes through a null pointer and the "corruption" is
    // really an uninitialised std::vector, not a bad free.
    logf("[levels] global vector: begin=0x%08x end=0x%08x cap=0x%08x\n",
         *(uint32_t*)0xb429e0, *(uint32_t*)0xb429e4, *(uint32_t*)0xb429e8);
    {
        uint8_t* tree = *(uint8_t**)self;
        logf("[spt_engine] inner tree=0x%p  first 16 dwords:\n", tree);
        for (int i = 0; i < 16; i += 4)
            logf("    +0x%02x: %08x %08x %08x %08x\n", i * 4,
                 ((uint32_t*)tree)[i], ((uint32_t*)tree)[i+1],
                 ((uint32_t*)tree)[i+2], ((uint32_t*)tree)[i+3]);
    }
    logf("[spt_engine] >> LoadTree(%u bytes)\n", len);
    CallCtx c{}; c.self = self; c.buf = buf; c.len = len; c.which = 1;
    if (!run_on_engine_thread(&c)) return false;
    logf("[spt_engine] LoadTree returned %s\n", c.result ? "TRUE" : "FALSE");
    if (!c.result) {
        // The parse failed; the tree is half-built and its spline pointers are
        // garbage.  Running Compute on it corrupts the heap.
        const char* err = *(const char**)0xb2b614;
        logf("[spt_engine] LoadTree error text: %s\n", err ? err : "(none)");
        return false;
    }
    return true;
}

// ---- heap-corruption bisector ---------------------------------------------
// Compute() runs a fixed pipeline (decomp doc 6j).  To find WHICH stage
// corrupts the heap, hook each stage entry with a 5-byte jmp to a thunk that
// validates the heap, logs, then jumps back through a copy of the displaced
// bytes.  A stage that reports "heap INVALID" on entry means the PREVIOUS
// stage did the damage.

// Implemented inside the single-step tracer rather than with trampolines:
// patching 12 call sites risks introducing new bugs of its own, while the
// tracer already sees every instruction and can validate on the ones we care
// about.  Enable with SPT_ENGINE_BISECT=1.
static bool guarded_compute(void* self, unsigned seed)
{
    logf("[spt_engine] >> Compute(seed=%u)\n", seed);
    CallCtx c{}; c.self = self; c.seed = seed; c.which = 2;
    if (!run_on_engine_thread(&c)) return false;
    logf("[spt_engine] Compute ok\n");
    return true;
}

static bool guarded_getgeometry(void* self, void* out)
{
    logf("[spt_engine] >> GetGeometry\n");
    CallCtx c{}; c.self = self; c.out = out; c.which = 3;
    if (!run_on_engine_thread(&c)) return false;
    logf("[spt_engine] GetGeometry ok\n");
    return true;
}

static bool guarded_getleaves(void* self, void* out)
{
    logf("[spt_engine] >> GetGeometry(leaves)\n");
    CallCtx c{}; c.self = self; c.out = out; c.which = 4;
    if (!run_on_engine_thread(&c)) return false;
    logf("[spt_engine] GetGeometry(leaves) ok\n");
    return true;
}

// Identify the leaf sub-struct's pointer slots the same way the BRANCH layout
// was corrected: read every dword in the region as a candidate pointer and
// report how much of what it points at is finite, plus its value range.
// Positions span the tree's bounding box; normals/orientation vectors sit in
// [-1,1]; texcoords in [0,1].  Guessing from the getter's stores transposed
// coords and texcoords last time -- measure instead.
static void probe_leaf_slots(const void* out, unsigned base, unsigned end,
                             unsigned count)
{
    logf("    leaf slot probe (base +0x%02x .. +0x%02x, assuming %u leaves):\n",
         base, end, count);
    for (unsigned off = base; off <= end; off += 4) {
        uint32_t ptr = *(const uint32_t*)((const uint8_t*)out + off);
        // Skip values that cannot be heap pointers.  A float like 84.0f
        // (0x42a80000) passes a naive `> 0x10000` test and then faults
        // IsBadReadPtr's probe -- exclude the float exponent range and
        // anything not 4-byte aligned.
        if (!ptr || ptr < 0x10000 || (ptr & 3) != 0) continue;
        if (ptr >= 0x40000000u && ptr < 0x50000000u) continue;
        const float* f = (const float*)(uintptr_t)ptr;
        unsigned probe = count ? count * 4 : 256;
        if (probe > 4096) probe = 4096;
        if (IsBadReadPtr(f, probe * sizeof(float))) {
            logf("      +0x%02x = 0x%08x  UNREADABLE\n", off, ptr);
            continue;
        }
        unsigned finite = 0;
        float lo = 1e30f, hi = -1e30f;
        for (unsigned k = 0; k < probe; ++k) {
            float v = f[k];
            if (v == v && v > -1e30f && v < 1e30f) {
                ++finite;
                if (v < lo) lo = v;
                if (v > hi) hi = v;
            }
        }
        logf("      +0x%02x = 0x%08x  finite %u/%u  range %g..%g\n",
             off, ptr, finite, probe, lo, hi);
    }
}

static std::vector<uint8_t> read_file(const char* p, bool* ok)
{
    std::vector<uint8_t> v;
    *ok = false;
    FILE* f = fopen(p, "rb");
    if (!f) return v;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    v.resize(n);
    *ok = (n == 0) || (fread(v.data(), 1, n, f) == (size_t)n);
    fclose(f);
    return v;
}

int main(int argc, char** argv)
{
    if (argc < 4) {
        logf("usage: spt_engine_dump <Oblivion.exe> <tree.spt> <out.bin> [seed]\n"
          "\n"
          "Drives the SpeedTreeRT 4.x code statically linked into Oblivion.exe\n"
          "to produce ground-truth tree geometry.  The game is NEVER launched:\n"
          "the image is mapped as data and only LoadTree/Compute/GetGeometry\n"
          "are called.  Either retail build works - the .text sections of the\n"
          "GOG/Nehrim and Steam executables are byte-identical.\n");
        return 2;
    }
    const char* exe  = argv[1];
    const char* spt  = argv[2];
    const char* outp = argv[3];
    unsigned seed = (argc > 4) ? (unsigned)strtoul(argv[4], nullptr, 0) : 1u;

    // Never put a dialog on the user's desktop.  Patching the CRT's
    // window-station probe (see init_security_cookie) lets it believe it may
    // show a message box, and an "R6030 - CRT not initialized" popup appeared.
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX |
                 SEM_NOOPENFILEERRORBOX);
    _set_error_mode(_OUT_TO_STDERR);
    _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);

    g_log = fopen("spt_engine_dump.log", "w");
    // VECTORED handler, not SetUnhandledExceptionFilter: the engine's functions
    // install their own SEH frames (every one starts `push -1 / push <handler>`),
    // so an unhandled-filter never sees the fault.  A vectored handler runs
    // first, before any frame-based handler.
    AddVectoredExceptionHandler(1, fault_reporter);
    SetUnhandledExceptionFilter(fault_reporter);
    logf("[spt_engine] start\n");

    if (!map_image(exe)) return 1;
    // Redirect the game's two allocator wrappers to the HOST CRT.
    //
    // Booting the image's own CRT was tried and abandoned: _mtinit (0x98c22e)
    // faults inside ntdll, and _heap_init (0x98d55e) already needs the TLS slot
    // _mtinit creates.  Reproducing the full CRT bootstrap is a much larger and
    // more fragile surface than replacing two well-defined functions, and the
    // SpeedTree code never observes allocation identity.
    // NOTE: operator new/delete are deliberately NOT redirected any more.
    // With _crtheap (0xbaa2ac) pointed at the process heap, the image's own CRT
    // allocator works -- and it must own every block, because it stores a
    // header before the payload (0x981b07: `mov esi,[ebx-4]` / `sub esi,9`) and
    // calls HeapSize/HeapFree on blocks it did not allocate otherwise.  Mixing
    // the two allocators produced STATUS_HEAP_CORRUPTION (0xC0000374) inside
    // Compute even when both used GetProcessHeap().
    logf("[spt_engine] using the image's own allocator\n");
    init_security_cookie();
    // Redirect AFTER init_security_cookie, which creates g_crtheap.  This is a
    // CORRECTNESS fix, not instrumentation: the engine reaches BOTH the game's
    // operator new/delete (0x401f00/0x401f20) and the CRT's malloc/free
    // (0x9816f9/0x9817bc).  Routing all four through one heap is what removed
    // the STATUS_HEAP_CORRUPTION.  (SPT_ENGINE_ALLOCLOG only controls logging.)
    g_alloclog = GetEnvironmentVariableA("SPT_ENGINE_ALLOCLOG", nullptr, 0) != 0;
    redirect_allocator();
    init_image_tls();

    bool ok = false;
    std::vector<uint8_t> sptbuf = read_file(spt, &ok);
    if (!ok || sptbuf.empty()) { logf("cannot read %s\n", spt); return 1; }

    // Construct a real CSpeedTreeRT.  LoadTree dereferences [this+0] at
    // 0x78dfd8, so the object MUST be built by the engine's own constructor
    // (0x78d6a0) -- a zeroed buffer crashes there.
    std::vector<uint8_t> obj(0x800, 0);
    void* self = obj.data();
    init_allocator_globals();
    logf("[spt_engine] calling ctor 0x%x, self=0x%p\n",
         (unsigned)VA_CTOR, self);
    logf("[spt_engine] image at 0x%p, spt %u bytes\n",
         g_image, (unsigned)sptbuf.size());
    // Heap checkpoints between calls: STATUS_HEAP_CORRUPTION surfaces at the
    // next heap operation, not where the bad write happened, so validate at
    // each boundary to attribute it to a specific call.
    auto checkpoint = [](const char* where) {
        bool a = g_crtheap ? (HeapValidate(g_crtheap, 0, nullptr) != FALSE) : true;
        bool b = HeapValidate(GetProcessHeap(), 0, nullptr) != FALSE;
        logf("[heap] %-14s crtheap=%s processheap=%s\n",
             where, a ? "ok" : "INVALID", b ? "ok" : "INVALID");
    };

    checkpoint("before ctor");
    if (!guarded_ctor(self)) return 1;
    checkpoint("after ctor");
    if (!guarded_loadtree(self, sptbuf.data(), (unsigned)sptbuf.size())) return 1;
    checkpoint("after LoadTree");

    // The game calls SetTreeSize(size, variance) before Compute (0x56084d ->
    // 0x7871d0 -> 0x7a24d0).  Without it the size/variance the generator uses
    // are whatever LoadTree left behind, which can size geometry buffers wrong.
    typedef void (__thiscall *fnSetSize)(void* self, float size, float variance);
    float authored = *(float*)((uint8_t*)self + 0x24);
    ((fnSetSize)0x7871d0)(self, authored, 0.0f);
    logf("[spt_engine] SetTreeSize(%f, 0) -> +0x24 now %f\n",
         authored, *(float*)((uint8_t*)self + 0x24));
    // Compute reads its tree size from [this+0x24] (0x78ccf9) and its LOD count
    // from [this+0x0c]/[this+0x60].  Garbage here would size the geometry
    // buffers absurdly, which looks exactly like heap corruption.
    logf("[spt_engine] this+0x24 size=%f  +0x0c=0x%08x  +0x5c=0x%08x"
         "  +0x60=0x%08x  +0x45=%u  +0x4c=0x%08x\n",
         *(float*)((uint8_t*)self + 0x24),
         *(uint32_t*)((uint8_t*)self + 0x0c),
         *(uint32_t*)((uint8_t*)self + 0x5c),
         *(uint32_t*)((uint8_t*)self + 0x60),
         *((uint8_t*)self + 0x45),
         *(uint32_t*)((uint8_t*)self + 0x4c));
    if (!guarded_compute(self, seed)) return 1;
    if (g_stage_armed) {
        logf("[spt_engine] stages entered (%u):\n", g_stage_hit);
        unsigned n = g_stage_hit < 64 ? g_stage_hit : 64;
        for (unsigned i = 0; i < n; ++i) {
            unsigned k = g_stage_seen[i];
            logf("    %2u %-24s heap=%s\n", k, g_stages[k].name,
                 g_stage_valid[k] ? "ok" : "INVALID");
        }
    }
    checkpoint("after Compute");

    // AUTHORED LOD selection (0x787c10, the routine 0x78a235 calls to turn
    // the getter's float LOD argument into a strip-table index):
    //     if (arg == -1.0) arg = [tree+0x34] ? [[tree+0x34]+0x10] : [tree+0x14]
    //     idx = (int)((1.0 - arg) * [tree+0x70])      ; +0x70 = LOD COUNT
    //     if (idx == count) --idx                     ; clamp
    // So index 0 is the HIGHEST detail and the count is authored per tree.
    {
        uint8_t* tree = *(uint8_t**)self;
        if (tree && !IsBadReadPtr(tree, 0x74)) {
            unsigned nlod = *(uint16_t*)(tree + 0x70);
            float dflt = *(float*)(tree + 0x14);
            uint32_t alt = *(uint32_t*)(tree + 0x34);
            float dflt2 = (alt && !IsBadReadPtr((void*)(uintptr_t)alt, 0x14))
                          ? *(float*)(uintptr_t)(alt + 0x10) : dflt;
            logf("[spt_engine] LOD count (tree+0x70) = %u  default lod = %g "
                 "(alt %g)\n", nlod, dflt, dflt2);
        }
    }

    SBranchGeometry g;
    memset(&g, 0, sizeof(g));
    if (!guarded_getgeometry(self, &g)) return 1;
    unsigned n = g.usVertexCount;
    logf("[spt_engine] branch verts=%u lods=%u\n"
         "    coords=0x%08x normals=0x%08x tex0=0x%08x strips=0x%08x lens=0x%08x\n",
         n, (unsigned)g.usNumLodLevels, g.pCoords, g.pNormals,
         g.pTexCoords0, g.pStrips, g.pStripLengths);
    // Dump the raw out-struct: if our field offsets are wrong, "coords" may be
    // pointing at an unrelated (never filled) array.
    logf("    raw SGeometry dwords:\n");
    for (int i = 0; i < 16; i += 4)
        logf("      +0x%02x: %08x %08x %08x %08x\n", i * 4,
             ((uint32_t*)&g)[i], ((uint32_t*)&g)[i+1],
             ((uint32_t*)&g)[i+2], ((uint32_t*)&g)[i+3]);
    // Identify which slot actually holds positions by INSPECTING each pointer:
    // real coordinates are finite and span the tree's bounding box.
    for (int i = 2; i <= 11; ++i) {
        uint32_t ptr = ((uint32_t*)&g)[i];
        if (!ptr) continue;
        const float* f = (const float*)(uintptr_t)ptr;
        unsigned finite = 0;
        float lo = 1e30f, hi = -1e30f;
        if (IsBadReadPtr(f, 3000 * sizeof(float))) {
            logf("      slot +0x%02x = 0x%08x  UNREADABLE\n", i * 4, ptr);
            continue;
        }
        for (unsigned k = 0; k < n * 3 && k < 3000; ++k) {
            float v = f[k];
            if (v == v && v > -1e30f && v < 1e30f) {
                ++finite;
                if (v < lo) lo = v;
                if (v > hi) hi = v;
            }
        }
        logf("      slot +0x%02x = 0x%08x  finite %u/3000  range %g..%g\n",
             i * 4, ptr, finite, lo, hi);
    }

    FILE* o = fopen(outp, "wb");
    if (!o) { logf("cannot write %s\n", outp); return 1; }
    uint32_t n32 = n;
    fwrite("SPTG", 1, 4, o);
    fwrite(&n32, 4, 1, o);
    if (n && g.pCoords)     fwrite((void*)(uintptr_t)g.pCoords,     4, n * 3, o);
    if (n && g.pNormals)    fwrite((void*)(uintptr_t)g.pNormals,    4, n * 3, o);
    if (n && g.pTexCoords0) fwrite((void*)(uintptr_t)g.pTexCoords0, 4, n * 2, o);

    // Triangle strips: pStrips is an array of index-array pointers, one per
    // strip, and pStripLengths the matching counts (both indexed by LOD in
    // 0x78a258 / 0x78a264).  Emit them so faces can be rebuilt.
    // +0x04 is the STRIP COUNT, not a LOD count: it is filled from 0x7886c0
    // (0x78a24b), whose body indexes the per-LOD strip table at [this+0x4c]
    // and returns how many strips that LOD has.
    uint32_t nstrip = g.usNumLodLevels;
    // Strip lengths are recovered by WALKING each index array, because the
    // engine's own per-strip container could not be located: `+0x04` of the
    // out-struct is not a strip count (0x7886c0 returns one strip's index
    // COUNT), and the vector at [self+4]+0x4c holds only 2 entries of
    // unrelated pointers.  Both were tried and measured wrong.
    //
    // The walk stops when an index leaves the vertex range, then rounds DOWN
    // to a whole number of RINGS.  Each ring ends in a doubled index (its
    // degenerate stitch) and those repeats are evenly spaced -- cottonwood
    // strip 0 repeats at 18,38,...,258, a 20-index ring.  Without the ring
    // rounding the walk absorbs the next tube's words (272 read where 260 is
    // real) and produces triangles spanning the whole tree.
    // ---- STRIPS -----------------------------------------------------------
    // `+0x04` IS the strip count (32 for treecottonwoodsu) and the vector
    // reached through [self+4]+0x38 holds that many UINT16 LENGTHS, all 260
    // for cottonwood -- exactly the per-strip index counts.  So the lengths
    // were available all along; the old code walked the index arrays instead
    // and could not see where a strip ended, absorbing the next tube's words
    // (272 read where 260 is real) and drawing triangles across the tree.
    //
    // Use the authored lengths.  This also fixes the missing FLARED TRUNK
    // BASE: with correct lengths every strip lands on its own vertices, so
    // the low block (z 0..7.3, radius to 8.5) is referenced again instead of
    // being crowded out by mis-sized strips.
    {
        const uint16_t* lens16 = nullptr;
        uint32_t nlen = 0;
        // pStripLengths (+0x08) IS the length array, stored as UINT16.
        // Read as DWORDS it looked like garbage pointers (0x01040104) -- that
        // is simply two packed lengths, 0x0104 = 260.  Entry 3 reads 0x003c
        // = 60, matching the short branch strips.  So the authored lengths
        // were in plain sight; the old code walked the index arrays instead,
        // and a walk cannot see where a strip ends: it absorbed the next
        // tube's words (272 read where 260 is real), which both produced
        // triangles spanning the whole tree and mis-anchored the strips so
        // vertices [0..167] -- the FLARED TRUNK BASE -- were referenced by
        // nothing at all.
        lens16 = (const uint16_t*)(uintptr_t)g.pStripLengths;
        if (lens16) {
            nlen = nstrip;
            if (IsBadReadPtr(lens16, nlen * sizeof(uint16_t))) {
                lens16 = nullptr; nlen = 0;
            }
        }
        logf("[spt_engine] strip lengths available: %u (nstrip=%u)\n",
             nlen, nstrip);

        const uint32_t* strips = (const uint32_t*)(uintptr_t)g.pStrips;
        uint32_t emitted = 0;
        long strip_pos = ftell(o);
        uint32_t placeholder = 0;
        fwrite(&placeholder, 4, 1, o);

        if (strips && nstrip && nstrip < 4096) {
            for (uint32_t i = 0; i < nstrip; ++i) {
                if (IsBadReadPtr(&strips[i], 4)) break;
                const uint16_t* idx = (const uint16_t*)(uintptr_t)strips[i];
                if (!idx) { uint32_t z = 0; fwrite(&z, 4, 1, o); ++emitted; continue; }
                uint32_t cnt = 0;
                if (lens16 && i < nlen && lens16[i]) {
                    cnt = lens16[i];                 // AUTHORED length
                } else {
                    // No length for this strip: fall back to the in-range walk
                    // rounded down to whole rings (each ring ends in a doubled
                    // index; cottonwood repeats every 20).
                    while (cnt < 65536 && !IsBadReadPtr(&idx[cnt], 2) &&
                           idx[cnt] < n)
                        ++cnt;
                    uint32_t first = 0, second = 0, nrep = 0;
                    for (uint32_t k = 1; k < cnt; ++k)
                        if (idx[k] == idx[k - 1]) {
                            if (!nrep) first = k;
                            else if (nrep == 1) second = k;
                            ++nrep;
                        }
                    if (nrep >= 2 && second > first) {
                        uint32_t ring = second - first;
                        if (ring >= 4 && ring <= 4096) {
                            uint32_t keep = first + 1 +
                                            ((cnt - first - 1) / ring) * ring;
                            if (keep >= 3 && keep <= cnt) cnt = keep;
                        }
                    }
                }
                if (cnt > 65536 || IsBadReadPtr(idx, cnt * sizeof(uint16_t)))
                    cnt = 0;
                fwrite(&cnt, 4, 1, o);
                for (uint32_t k = 0; k < cnt; ++k) {
                    uint32_t v = idx[k] < n ? idx[k] : 0;
                    fwrite(&v, 4, 1, o);
                }
                if (emitted < 4)
                    logf("[verify] strip %u: %u indices (first %u %u %u)\n",
                         emitted, cnt, cnt ? idx[0] : 0,
                         cnt > 1 ? idx[1] : 0, cnt > 2 ? idx[2] : 0);
                ++emitted;
            }
        }
        long endpos = ftell(o);
        fseek(o, strip_pos, SEEK_SET);
        fwrite(&emitted, 4, 1, o);
        fseek(o, endpos, SEEK_SET);
        nstrip = emitted;
    }
    // Report what was actually written, from the same pointers used to write.
    {
        const float* cf = (const float*)(uintptr_t)g.pCoords;
        const float* nf = (const float*)(uintptr_t)g.pNormals;
        const uint32_t* lens = (const uint32_t*)(uintptr_t)g.pStripLengths;
        logf("[verify] coords[0..2]=%g %g %g   normals[0..2]=%g %g %g\n",
             cf[0], cf[1], cf[2], nf[0], nf[1], nf[2]);
        logf("[verify] stripLengths[0..3]=%u %u %u %u\n",
             lens[0], lens[1], lens[2], lens[3]);
    }
    logf("[spt_engine] wrote %u verts, %u strips\n", n, nstrip);

    // ---- LEAVES ----------------------------------------------------------
    // Leaf geometry via GetGeometry flag bit 4 (0x78c74a `test bl,4` ->
    // 0x788120).  The leaf sub-struct lives at +0x78 of the SAME out-struct:
    // the getter does `lea ebx,[edi+0x78]` and passes that to the per-group
    // filler 0x7989b0, called once per billboard-leaf group ([tree+0xc0]).
    //
    // Layout MEASURED by probing the live buffers (the same technique that
    // corrected the branch table, whose getter-derived guess had coords and
    // texcoords transposed).  On treeginkgo, 2 groups:
    //     +0x78  byte flag = 1        (stored 0x788219 / 0x7882e5)
    //     +0x7c  float     = 84.0     tree height (stored 0x788310)
    //     +0x84  uint32    = 470      LEAF COUNT
    //     +0x90  float*    -> count*3 ALL finite, bbox z 70.3..224.4, i.e.
    //                                 one XYZ CENTER per leaf, in the same
    //                                 world space as the branch coords
    //     +0x8c  float*    -> count*N in [0, 0.863] (normalized; size/wind)
    //
    // Written as a second chunk after the branch data so existing readers
    // that stop at the strips keep working:
    //     'SPTL'  uint32 leafCount  float32[count*3] centers
    {
        unsigned groups = leaf_group_count(self);
        logf("[spt_engine] leaf groups (tree+0xc0) = %u\n", groups);
        SBranchGeometry lg;
        memset(&lg, 0, sizeof(lg));
        uint32_t written = 0;
        bool wrote_leaf_chunk = false;
        if (guarded_getleaves(self, &lg)) {
            logf("    raw leaf-region dwords (+0x78..+0xb8):\n");
            for (unsigned i = 0x78; i < 0xbc; i += 16)
                logf("      +0x%02x: %08x %08x %08x %08x\n", i,
                     *(uint32_t*)((uint8_t*)&lg + i),
                     *(uint32_t*)((uint8_t*)&lg + i + 4),
                     *(uint32_t*)((uint8_t*)&lg + i + 8),
                     *(uint32_t*)((uint8_t*)&lg + i + 12));
            // The count is a UINT16, not a dword: ginkgo's +0x84 reads
            // 0x000001d6 (470) but oak's reads 0x15ec023c, whose low word
            // 0x023c = 572 is the real count -- the high word belongs to the
            // next member.  Reading the full dword yielded 367,788,604.
            unsigned lcount = *(const uint16_t*)((const uint8_t*)&lg + 0x84);
            const float* lp = (const float*)(uintptr_t)
                *(const uint32_t*)((const uint8_t*)&lg + 0x90);
            logf("[spt_engine] leaf count=%u centers=0x%08x\n",
                 lcount, (unsigned)(uintptr_t)lp);
            if (lcount && lcount < 500000 && lp &&
                !IsBadReadPtr(lp, lcount * 3 * sizeof(float))) {
                float mn[3] = {1e30f, 1e30f, 1e30f};
                float mx[3] = {-1e30f, -1e30f, -1e30f};
                unsigned finite = 0;
                for (unsigned k = 0; k < lcount; ++k) {
                    const float* c = lp + k * 3;
                    if (c[0] != c[0] || c[1] != c[1] || c[2] != c[2]) continue;
                    ++finite;
                    for (int a = 0; a < 3; ++a) {
                        if (c[a] < mn[a]) mn[a] = c[a];
                        if (c[a] > mx[a]) mx[a] = c[a];
                    }
                }
                logf("    centers finite %u/%u  bbox min %g %g %g  max %g %g %g\n",
                     finite, lcount, mn[0], mn[1], mn[2], mx[0], mx[1], mx[2]);
                fwrite("SPTL", 1, 4, o);
                fwrite(&lcount, 4, 1, o);
                fwrite(lp, 4, lcount * 3, o);
                written = lcount;
                wrote_leaf_chunk = true;
                // +0x8c: the per-leaf SIZE scalar the engine computed at
                // 0x79a25b-0x79a2a6 (size.x * branch_arc_len * 0.5 * size.y).
                // One float per leaf; dumped so the card size can use the
                // engine's own value instead of a whole-tree approximation.
                // ---- per-leaf SIZE PAIR --------------------------------
                // ---- per-leaf SIZE: searched for, NOT found -----------
                // The corner math in 0x7989b0 resolves (FPU simulation) to
                //     corner = +/- quad_uv * lod_scale * M[eax] / M[eax+4]
                // so M[eax], M[eax+4] are the card's X and Y half-extents --
                // the base dimension the 0x79a25b scalar multiplies.  eax
                // comes from the leaf-system's [edi+0x20] indexed per leaf
                // (0x798aed); [edi+0x24] holds the 3-float centers.
                //
                // The leaf system is [self+8] (0x788126 sets esi = ecx, then
                // 0x7881b5 loads ecx = [esi+8]); reading [tree+8] instead is
                // one dereference too many and yields null.  Even with the
                // right object, [lsys+0x20] read as 2 floats/leaf gives
                // 1e31..1e35, and a stride survey (1/2/3/4 over +0x00..+0x3c)
                // found nothing sane at the leaf count.  So the live array is
                // laid out differently than the corner math suggests, and the
                // card size is taken from AUTHORED section 4006 instead
                // (asset_convert/spt_engine_geom.py::_card_extents), which is
                // the engine's own cached card size and measures exactly 2x
                // the previous `size * K * 0.5`.

                // NOTE on the per-leaf SIZE array: not yet identified.
                // `+0x8c` read as one float per leaf yields 1e29 values and
                // NaNs on ginkgo and pure garbage on dbush03, so that is the
                // wrong slot or the wrong stride.  A full stride survey
                // (1/2/3/4/6/8/12/16 across +0x88..+0xa4) found only `+0x88`
                // fully finite, and its values are NORMALIZED (dbush03
                // 0..0.022) rather than world-unit sizes.  Card size
                // therefore still uses the section 6t formula; see
                // docs/speedtree_engine_decomp.md section 6y.
            }
        }
        // Always record the leaf chunk, even when the count is ZERO.  A
        // MISSING chunk is ambiguous -- it could mean a dump written before
        // leaf support, or a tree the engine correctly gave no leaves -- and
        // the reader resolved that ambiguity by falling back to the Python
        // foliage.  On dtree01 (a bare dead tree: its leaf level stores
        // child_freq = 0, the section 6t gate) that pasted 264 Python leaf
        // cards onto engine bark they were never placed against, floating up
        // to 36% of the tree diagonal away and using a mania leaf atlas on a
        // dementia tree.  An explicit zero makes "no leaves" a FACT.
        if (!wrote_leaf_chunk) {
            uint32_t zero = 0;
            fwrite("SPTL", 1, 4, o);
            fwrite(&zero, 4, 1, o);
        }
        logf("[spt_engine] wrote %u leaf centers\n", written);
    }

    fclose(o);
    logf("[spt_engine] wrote %s\n", outp);
    return 0;
}
