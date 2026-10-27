// Named-pipe server.
//
// Local-only by construction: a named pipe has no listening TCP port, so this
// cannot be reached off-machine even by accident.
//
// One client at a time. A second connection is refused rather than queued, so
// two clients can never interleave state mutations against the same game.

#pragma once

#include <atomic>
#include <functional>
#include <string>
#include <thread>

namespace bridge {

// `\\.\pipe\tes_game_bridge`, or the TES_BRIDGE_PIPE environment variable when
// set, so a headless test instance never answers on the player's own pipe.
const char* PipeName();

// Handles one request line, returns one response line (without the newline).
using RequestHandler = std::function<std::string(const std::string&)>;

class PipeServer {
public:
    bool Start(RequestHandler handler);
    void Stop();
    bool clientConnected() const { return connected_.load(); }

    // Invoked when a client disconnects, on the pipe thread. Used to release
    // per-session state (tracked spawns) so a crashed client does not leave the
    // game littered with test actors.
    std::function<void()> onDisconnect;

private:
    void ThreadMain();

    RequestHandler     handler_;
    std::thread        thread_;
    std::atomic<bool>  running_{false};
    std::atomic<bool>  connected_{false};
    void*              pipe_ = nullptr;   // HANDLE
};

extern PipeServer g_pipe;

}  // namespace bridge
