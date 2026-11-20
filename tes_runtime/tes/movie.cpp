#include "movie.h"

#include <cstdint>

#include "addresses.h"
#include "engine.h"
#include "ids.h"
#include "log.h"

namespace tesruntime {

namespace {

using SetMemberFn = bool (*)(void* objectInterface, void* object, const char* name, GFxValue* value,
                             bool isDisplayObject);
using ReleaseManagedFn = void (*)(void* objectInterface, GFxValue* value, void* data);
using CreateFunctionFn = void (*)(void* view, GFxValue* value, GFxFunctionHandler* handler, void* refCon);
using GetVariableFn = bool (*)(void* view, GFxValue* value, const char* path);
using SetVariableFn = void (*)(void* view, const char* path, GFxValue* value, UInt32 flags);
using IsMenuOpenFn = bool (*)(void* manager, void** name);
using InvokeFn = bool (*)(void* view, const char* path, GFxValue* result, GFxValue* args, UInt32 count);

SetMemberFn g_setMember = nullptr;
ReleaseManagedFn g_releaseManaged = nullptr;
std::uintptr_t g_menuManagerSlot = 0;
IsMenuOpenFn g_isMenuOpen = nullptr;

}  // namespace

bool ResolveMovieCalls() {
    g_setMember = reinterpret_cast<SetMemberFn>(Resolve("GFxValue SetMember", ids::kGfxSetMember, nullptr));
    g_releaseManaged =
        reinterpret_cast<ReleaseManagedFn>(Resolve("GFxValue ReleaseManaged", ids::kGfxReleaseManaged, nullptr));
    g_menuManagerSlot = Resolve("MenuManager singleton", ids::kMenuManagerSingleton, nullptr);
    g_isMenuOpen =
        reinterpret_cast<IsMenuOpenFn>(Resolve("MenuManager::IsMenuOpen", ids::kMenuManagerIsMenuOpen, nullptr));
    return g_setMember != nullptr;
}

void ReleaseValue(GFxValue* value) {
    if ((value->type & ids::kGfxValueManaged) && g_releaseManaged) {
        g_releaseManaged(value->objectInterface, value, value->data.object);
    }
    *value = GFxValue();
}

bool GetMovieVariable(void* view, const char* path, GFxValue* out) {
    return VCall<GetVariableFn>(view, ids::kMovieViewGetVariableSlot)(view, out, path);
}

void SetMovieVariable(void* view, const char* path, GFxValue* value) {
    VCall<SetVariableFn>(view, ids::kMovieViewSetVariableSlot)(view, path, value, 0);
}

bool MovieDefined(void* view, const char* path) {
    GFxValue value;
    const bool defined = GetMovieVariable(view, path, &value) && (value.type & ids::kGfxValueTypeMask) != 0;
    ReleaseValue(&value);
    return defined;
}

bool SetMovieMember(GFxValue* object, const char* name, GFxValue* value) {
    const bool displayObject = (object->type & ids::kGfxValueTypeMask) == ids::kGfxValueDisplayObject;
    return g_setMember && g_setMember(object->objectInterface, object->data.object, name, value, displayObject);
}

bool AddMovieFunction(void* view, GFxValue* object, const char* name, GFxFunctionHandler* handler) {
    GFxValue function;
    VCall<CreateFunctionFn>(view, ids::kMovieViewCreateFunctionSlot)(view, &function, handler, nullptr);
    const bool set = SetMovieMember(object, name, &function);
    ReleaseValue(&function);
    return set;
}

bool InvokeMovie(void* view, const char* path) {
    GFxValue result;
    const bool called = VCall<InvokeFn>(view, ids::kMovieViewInvokeSlot)(view, path, &result, nullptr, 0);
    ReleaseValue(&result);
    return called;
}

bool MenuIsOpen(const char* name) {
    void* manager = g_menuManagerSlot ? *reinterpret_cast<void**>(g_menuManagerSlot) : nullptr;
    if (!manager || !g_isMenuOpen) return false;
    FixedString fixed(name);
    return fixed.ptr && g_isMenuOpen(manager, &fixed.ptr);
}

void ForEachOpenMovie(void (*visit)(void* view)) {
    void* manager = g_menuManagerSlot ? *reinterpret_cast<void**>(g_menuManagerSlot) : nullptr;
    if (!manager || !g_setMember) return;
    void** menus = At<void**>(manager, ids::kOffMenuStack);
    const std::uint32_t count = At<std::uint32_t>(manager, ids::kOffMenuStackCount);
    for (std::uint32_t i = 0; menus && i < count; ++i) {
        void* view = menus[i] ? At<void*>(menus[i], ids::kOffMenuView) : nullptr;
        if (view) visit(view);
    }
}

}  // namespace tesruntime
