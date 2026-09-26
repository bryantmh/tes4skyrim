#include "main_tick.h"

#include <atomic>
#include <chrono>
#include <thread>

namespace tesruntime {

bool StartTick(PostToMainFn post, int ms, void (*fn)()) {
    if (!post || !fn || ms <= 0) return false;
    auto* queued = new std::atomic<bool>(false);
    std::thread([post, ms, fn, queued]() {
        for (;;) {
            std::this_thread::sleep_for(std::chrono::milliseconds(ms));
            if (queued->exchange(true)) continue;
            if (!post([fn, queued]() {
                    *queued = false;
                    fn();
                })) {
                *queued = false;
            }
        }
    }).detach();
    return true;
}

}  // namespace tesruntime
