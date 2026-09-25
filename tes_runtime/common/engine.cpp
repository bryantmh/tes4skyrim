#include "engine.h"

#include <windows.h>

#include <atomic>
#include <chrono>
#include <fstream>
#include <sstream>
#include <thread>

#include "engine_ids.h"
#include "json.h"
#include "log.h"
#include "paths.h"

namespace tesruntime {

EngineApi g_api;

FixedString::FixedString(const char* text) { g_api.fixedStringCtor(&ptr, text); }
FixedString::~FixedString() { g_api.fixedStringDtor(&ptr); }

namespace {

template <typename T>
bool Bind(T& slot, const char* name, std::uint64_t id) {
    const std::uintptr_t a = Resolve(name, id, nullptr);
    slot = reinterpret_cast<T>(a);
    return a != 0;
}

struct Ticker {
    int ms = 0;
    void (*fn)() = nullptr;
    std::atomic<bool> queued{false};
};

class TickTask : public TaskDelegate {
public:
    explicit TickTask(Ticker* ticker) : ticker_(ticker) {}
    void Run() override {
        ticker_->queued = false;
        ticker_->fn();
    }
    void Dispose() override { delete this; }

private:
    Ticker* ticker_;
};

}  // namespace

bool ResolveEngine() {
    bool core = true;
    core &= Bind(g_api.fixedStringCtor, "BSFixedString::ctor", ids::kFixedStringCtor);
    core &= Bind(g_api.fixedStringDtor, "BSFixedString::dtor", ids::kFixedStringDtor);
    core &= Bind(g_api.getFormFromFile, "Game.GetFormFromFile", ids::kGetFormFromFile);
    Bind(g_api.lookupForm, "LookupFormByID", ids::kLookupFormByID);
    if (!core) Log("engine: fixed strings or form lookup unresolved; forms unavailable");
    return core;
}

void* FormFromFile(std::uint32_t local, const std::string& file) {
    if (!g_api.getFormFromFile || !g_api.vm) return nullptr;
    FixedString name(file.c_str());
    return g_api.getFormFromFile(g_api.vm, 0, nullptr, static_cast<std::int32_t>(local), &name.ptr);
}

bool RefPosition(void* ref, float* out) {
    void* root = VCall<void* (*)(void*)>(ref, kVtGet3D)(ref);
    if (!root) return false;
    const float* t = &At<float>(root, kWorldTranslate);
    out[0] = t[0]; out[1] = t[1]; out[2] = t[2];
    return true;
}

void ForEachSidecar(const char* suffix,
                    void (*visit)(const std::string& name, const Json& doc)) {
    const std::string dir = SidecarDir();
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA((dir + "*." + suffix).c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        std::ifstream f(dir + fd.cFileName, std::ios::binary);
        std::stringstream ss;
        ss << f.rdbuf();
        std::string err;
        Json j = Json::Parse(ss.str(), &err);
        if (!j.isObject()) {
            Log("sidecar: %s unreadable (%s)", fd.cFileName, err.c_str());
            continue;
        }
        visit(fd.cFileName, j);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}

void ReleaseRef(void* ref) {
    // BSHandleRefObject: refcount in the low 10 bits of +0x28; zero deletes
    // through virtual slot 1 (measured at 0x62c3e2..0x62c3f8).
    auto* count = &At<volatile long>(ref, kRefCount);
    const long before = InterlockedExchangeAdd(count, -1);
    if (((before - 1) & 0x3ff) == 0) {
        VCall<void (*)(void*, int)>(ref, 1)(ref, 1);
    }
}

void RunOnMainThread(TaskDelegate* task) {
    if (g_api.task) {
        g_api.task->AddTask(task);
    } else {
        task->Run();
        task->Dispose();
    }
}

bool StartMainThreadTick(int ms, void (*fn)()) {
    if (!g_api.task || !fn) return false;
    auto* ticker = new Ticker();
    ticker->ms = ms;
    ticker->fn = fn;
    std::thread([ticker]() {
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(ticker->ms));
            if (!ticker->queued.exchange(true)) g_api.task->AddTask(new TickTask(ticker));
        }
    }).detach();
    return true;
}

}  // namespace tesruntime
