#include "main_thread.h"

#include <windows.h>

#include <atomic>
#include <type_traits>
#include <utility>

#include "scope.h"

namespace tesruntime::mw {

namespace {

SKSETaskInterface* g_task = nullptr;

// The main thread's id, learned the first time the game runs one of our
// tasks. 0 until then, which makes RunOnGameThread post.
std::atomic<DWORD> g_mainThread{0};

// Owns the callable until the game has run it. The game calls Dispose() once
// Run() returns, which is the only place this is freed. `Base` is the SKSE
// delegate of the queue it goes on: the task queue's notes the main thread.
//
// 🛑 It runs as the layer that POSTED it (scope.h): a call a script defers --
// a spawn, a force-greet -- still resolves ids through that script's plugin.
template <class Base>
class Task : public Base {
public:
    explicit Task(std::function<void()> fn)
        : fn_(std::move(fn)), layer_(CurrentLayer()) {}

    void Run() override {
        // An exception escaping into the game's task pump would take the
        // process down; a dropped call is recoverable, a crash is not.
        if constexpr (std::is_same_v<Base, TaskDelegate>) g_mainThread = GetCurrentThreadId();
        const LayerScope scope(layer_);
        try {
            if (fn_) fn_();
        } catch (...) {
        }
    }

    void Dispose() override { delete this; }

private:
    std::function<void()> fn_;
    int layer_;
};

}  // namespace

void SetTaskInterface(SKSETaskInterface* task) { g_task = task; }

bool CanPostToMainThread() { return g_task != nullptr; }

bool PostToMainThread(std::function<void()> fn) {
    if (!g_task || !fn) return false;
    g_task->AddTask(new Task<TaskDelegate>(std::move(fn)));
    return true;
}

bool PostToUiThread(std::function<void()> fn) {
    if (!g_task || !g_task->AddUITask || !fn) return false;
    g_task->AddUITask(new Task<UIDelegate_v1>(std::move(fn)));
    return true;
}

bool RunOnGameThread(std::function<void()> fn) {
    if (!fn) return false;
    if (g_mainThread != GetCurrentThreadId()) {
        return PostToMainThread(std::move(fn));
    }
    fn();
    return true;
}

}  // namespace tesruntime::mw
