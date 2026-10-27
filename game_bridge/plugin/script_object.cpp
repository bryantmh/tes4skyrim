// Script object construction for console execution.
//
// The console builds a Script the same way every time:
//   1. allocate 0x80 bytes from the game heap
//   2. run Script::ctor
//   3. call Script::SetText(cmd), which allocates and stores the text at +0x38
//   4. hand it to the console executor
//
// We now do exactly that, calling the engine's own constructor.
//
// AN EARLIER VERSION SUBSTITUTED STEP 2 with "memset to zero, then store the
// vtable", because Script::ctor's stable ID (21874) is absent from the 1.6.1170
// Address Library. That substitution is WRONG and the console executor faulted
// on the object it produced. Disassembling Script::ctor (0x2fc5d0 on 1.6.659)
// shows why:
//
//   40 53 48 83 EC 20     push rbx / sub rsp,20
//   48 8B D9 E8 ........  mov rbx,rcx / call 0x19e330   <- BASE-CLASS ctor:
//                                                          touches TLS, stores
//                                                          its own vtable
//   48 8D 05 ........     lea rax,[Script vtable]
//   48 89 03              mov [rbx],rax
//   ... field stores ...
//   C6 43 1A 13           mov byte [rbx+0x1A], 0x13     <- NON-ZERO type byte
//
// A zeroed block has neither the base-class state nor that 0x13, so the
// executor read a half-built object. The ID being missing is not a reason to
// hand-roll the constructor -- it is a reason to resolve it by signature, which
// is what ids::kSigScriptCtor does (verified unique in .text).
//
// Everything here now goes through engine functions, so no field layout is
// assumed beyond the text pointer at +0x38 (verified by disassembling
// Script::SetText, which writes exactly there).

#include <windows.h>

#include <cstring>
#include <string>

#include "addresses.h"
#include "ids.h"
#include "log.h"

namespace bridge {

namespace {

using MemAlloc_t      = void* (*)(void* heap, std::size_t size, std::size_t align, bool aligned);
using ScriptCtor_t    = void* (*)(void* script);
using ScriptSetText_t = void  (*)(void* script, const char* text);

}  // namespace

void FreeScriptObject(void* script);

void* AllocScriptObject(const std::string& text) {
    if (!g_addr.memAlloc || !g_addr.memoryManager || !g_addr.scriptCtor || !g_addr.scriptSetText)
        return nullptr;

    auto alloc = reinterpret_cast<MemAlloc_t>(g_addr.memAlloc);

    // Matches the console's own allocation: 0x80 bytes, unaligned, from the
    // engine's MemoryManager (a null heap faults at [this+0x28]).
    void* mem = alloc(reinterpret_cast<void*>(g_addr.memoryManager), ids::kScriptSize, 0, false);
    if (!mem) return nullptr;

    // DO NOT memset the block before the constructor.
    //
    // MemAlloc hands back memory that is NOT zeroed -- it still carries the
    // allocator's own bookkeeping (observed live: the first qwords are heap
    // pointers). Blanket-zeroing 0x80 bytes destroys that, and the engine's
    // console executor then compiles the script but the command has no effect:
    // it returns success, prints nothing, and does nothing. That combination
    // is exactly what was seen in-game on 2026-08-14, and it survived every
    // other fix because each individual piece (ctor, SetText, compiler index,
    // argument order) verified correct in isolation.
    //
    // Proven live via the raw probes: the identical sequence WITHOUT the
    // memset compiles and runs the command (the Script's +0x10 size, +0x28,
    // +0x32 and the +0x40 bytecode pointer all change, and ToggleGodMode
    // actually toggles). The constructor initialises every field it owns, so
    // pre-zeroing was never buying anything.
    auto ctor = reinterpret_cast<ScriptCtor_t>(g_addr.scriptCtor);
    __try {
        ctor(mem);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        Log("script: constructor raised an exception");
        return nullptr;
    }

    // Corroborates that the constructor ran and that kScriptVtable names the
    // same object the engine builds. A mismatch means one of the two addresses
    // is wrong, and calling the executor would fault -- so refuse instead.
    //
    // The object IS fully constructed here, so releasing it through the normal
    // destructor path is correct (unlike the exception paths, where the object
    // is half-built and freeing it could hand the engine a torn pointer).
    if (g_addr.scriptVtable &&
        *reinterpret_cast<std::uintptr_t*>(mem) != g_addr.scriptVtable) {
        Log("script: vtable mismatch after ctor (got %p, expected %p) -- "
            "refusing to execute",
            *reinterpret_cast<void**>(mem),
            reinterpret_cast<void*>(g_addr.scriptVtable));
        FreeScriptObject(mem);
        return nullptr;
    }

    auto setText = reinterpret_cast<ScriptSetText_t>(g_addr.scriptSetText);
    __try {
        setText(mem, text.c_str());
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        // Leak the 0x80 block rather than risk freeing a half-built object.
        Log("script: SetText raised an exception");
        return nullptr;
    }

    return mem;
}

void FreeScriptObject(void* script) {
    if (!script) return;
    // Release through the engine's own destructor path so the text buffer and
    // any compiled data are freed the way the engine expects.
    if (g_addr.scriptDtor) {
        using Dtor_t = void (*)(void*);
        __try {
            reinterpret_cast<Dtor_t>(g_addr.scriptDtor)(script);
            return;
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            Log("script: destructor raised an exception; leaking object");
            return;
        }
    }
    // No destructor resolved: leaking one 0x80 block per command is strictly
    // better than freeing an object the engine still holds pointers into.
}

}  // namespace bridge
