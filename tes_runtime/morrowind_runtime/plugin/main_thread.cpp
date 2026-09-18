#include "main_thread.h"

#include <utility>

namespace mwruntime {

namespace {

SKSETaskInterface* g_task = nullptr;

// Owns the callable until the game has run it. The game calls Dispose() on the
// main thread once Run() returns, which is the only place this is freed.
class Task : public TaskDelegate {
public:
    explicit Task(std::function<void()> fn) : fn_(std::move(fn)) {}

    void Run() override {
        // An exception escaping into the game's task pump would take the
        // process down; a dropped call is recoverable, a crash is not.
        try {
            if (fn_) fn_();
        } catch (...) {
        }
    }

    void Dispose() override { delete this; }

private:
    std::function<void()> fn_;
};

}  // namespace

void SetTaskInterface(SKSETaskInterface* task) { g_task = task; }

bool CanPostToMainThread() { return g_task != nullptr; }

bool PostToMainThread(std::function<void()> fn) {
    if (!g_task || !fn) return false;
    g_task->AddTask(new Task(std::move(fn)));
    return true;
}

}  // namespace mwruntime
