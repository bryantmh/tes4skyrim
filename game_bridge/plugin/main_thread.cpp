#include "main_thread.h"

#include <windows.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <memory>

#include "log.h"

namespace bridge {

SKSETaskInterface* g_task = nullptr;
bool g_taskPumpLive = false;

namespace {

// Shared state between the waiting pipe thread and the task running on the
// main thread. Refcounted because the task may still be alive (about to be
// Disposed) after the waiter has given up on a timeout -- freeing this from the
// waiter would then be a use-after-free on the game thread.
struct Shared {
    std::mutex              m;
    std::condition_variable cv;
    bool                    done = false;
    std::function<void()>   fn;
};

// Logs a fault's code and module offset, so a handler that dies inside the
// engine says where instead of surfacing as a bare "no result".
int LogFault(EXCEPTION_POINTERS* info) {
    const auto* rec = info->ExceptionRecord;
    HMODULE module = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       static_cast<LPCSTR>(rec->ExceptionAddress), &module);
    char name[MAX_PATH] = "?";
    if (module) GetModuleFileNameA(module, name, MAX_PATH);
    Log("main thread: task faulted, code %08lX at %s+0x%llx", rec->ExceptionCode,
        std::strrchr(name, '\\') ? std::strrchr(name, '\\') + 1 : name,
        static_cast<unsigned long long>(reinterpret_cast<std::uintptr_t>(rec->ExceptionAddress) -
                                        reinterpret_cast<std::uintptr_t>(module)));
    return EXCEPTION_EXECUTE_HANDLER;
}

void RunGuarded(const std::function<void()>& fn) {
    __try {
        fn();
    } __except (LogFault(GetExceptionInformation())) {
    }
}

class Task : public TaskDelegate {
public:
    explicit Task(std::shared_ptr<Shared> s) : s_(std::move(s)) {}

    void Run() override {
        // Never let an exception escape into the game's task pump.
        if (s_->fn) RunGuarded(s_->fn);
        {
            std::lock_guard<std::mutex> lk(s_->m);
            s_->done = true;
        }
        s_->cv.notify_all();
    }

    void Dispose() override { delete this; }

private:
    std::shared_ptr<Shared> s_;
};

}  // namespace

MainThreadStatus RunOnMainThread(std::function<void()> fn, unsigned timeoutMs) {
    if (!g_task || !g_taskPumpLive) return MainThreadStatus::NoTaskInterface;

    auto shared = std::make_shared<Shared>();
    shared->fn = std::move(fn);

    g_task->AddTask(new Task(shared));

    std::unique_lock<std::mutex> lk(shared->m);
    const bool ran = shared->cv.wait_for(
        lk, std::chrono::milliseconds(timeoutMs), [&] { return shared->done; });

    return ran ? MainThreadStatus::Ok : MainThreadStatus::Timeout;
}

}  // namespace bridge
