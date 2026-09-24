#include "main_thread.h"

#include <windows.h>

#include <atomic>
#include <utility>

#include "scope.h"

namespace mwruntime {

namespace {

SKSETaskInterface* g_task = nullptr;

// The main thread's id, learned the first time the game runs one of our
// tasks. 0 until then, which makes RunOnGameThread post.
std::atomic<DWORD> g_mainThread{0};

// Owns the callable until the game has run it. The game calls Dispose() on the
// main thread once Run() returns, which is the only place this is freed.
//
// 🛑 It runs as the layer that POSTED it (scope.h): a call a script defers --
// a spawn, a force-greet -- still resolves ids through that script's plugin.
class Task : public TaskDelegate {
public:
    explicit Task(std::function<void()> fn)
        : fn_(std::move(fn)), layer_(CurrentLayer()) {}

    void Run() override {
        // An exception escaping into the game's task pump would take the
        // process down; a dropped call is recoverable, a crash is not.
        g_mainThread = GetCurrentThreadId();
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
    g_task->AddTask(new Task(std::move(fn)));
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

}  // namespace mwruntime
