#include "pipe_server.h"

#include <windows.h>

#include <vector>

#include "log.h"

namespace bridge {

PipeServer g_pipe;

const char* PipeName() {
    static const std::string name = [] {
        char buf[256] = {};
        const DWORD n = GetEnvironmentVariableA("TES_BRIDGE_PIPE", buf, sizeof(buf));
        return (n > 0 && n < sizeof(buf)) ? std::string(buf) : std::string(R"(\\.\pipe\tes_game_bridge)");
    }();
    return name.c_str();
}

bool PipeServer::Start(RequestHandler handler) {
    if (running_.load()) return true;
    handler_ = std::move(handler);
    running_.store(true);
    thread_ = std::thread(&PipeServer::ThreadMain, this);
    return true;
}

void PipeServer::Stop() {
    running_.store(false);
    // Unblock a ConnectNamedPipe that is waiting for a client.
    if (pipe_ && pipe_ != INVALID_HANDLE_VALUE) {
        CancelIoEx(static_cast<HANDLE>(pipe_), nullptr);
        DisconnectNamedPipe(static_cast<HANDLE>(pipe_));
        CloseHandle(static_cast<HANDLE>(pipe_));
        pipe_ = nullptr;
    }
    if (thread_.joinable()) thread_.join();
}

void PipeServer::ThreadMain() {
    Log("pipe: listening on %s", PipeName());

    while (running_.load()) {
        HANDLE h = CreateNamedPipeA(
            PipeName(),
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,                    // one instance: one client at a time
            1 << 20,              // out buffer
            1 << 20,              // in buffer
            0,
            nullptr);

        if (h == INVALID_HANDLE_VALUE) {
            Log("pipe: CreateNamedPipe failed (%lu)", GetLastError());
            Sleep(1000);
            continue;
        }

        pipe_ = h;

        const BOOL ok = ConnectNamedPipe(h, nullptr)
                            ? TRUE
                            : (GetLastError() == ERROR_PIPE_CONNECTED);
        if (!ok || !running_.load()) {
            CloseHandle(h);
            pipe_ = nullptr;
            continue;
        }

        connected_.store(true);
        Log("pipe: client connected");

        // Read newline-delimited requests until the client goes away.
        std::string inbuf;
        char chunk[8192];

        while (running_.load()) {
            DWORD read = 0;
            if (!ReadFile(h, chunk, sizeof(chunk), &read, nullptr) || read == 0) break;
            inbuf.append(chunk, read);

            size_t nl;
            while ((nl = inbuf.find('\n')) != std::string::npos) {
                std::string line = inbuf.substr(0, nl);
                inbuf.erase(0, nl + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                if (line.empty()) continue;

                std::string reply;
                try {
                    reply = handler_ ? handler_(line) : std::string("{\"ok\":false}");
                } catch (const std::exception& e) {
                    reply = std::string("{\"ok\":false,\"code\":\"E_INTERNAL\",\"error\":\"") +
                            e.what() + "\"}";
                } catch (...) {
                    reply = "{\"ok\":false,\"code\":\"E_INTERNAL\",\"error\":\"unknown\"}";
                }
                reply.push_back('\n');

                DWORD wrote = 0;
                size_t sent = 0;
                bool writeOk = true;
                while (sent < reply.size()) {
                    if (!WriteFile(h, reply.data() + sent,
                                   static_cast<DWORD>(reply.size() - sent), &wrote, nullptr)) {
                        writeOk = false;
                        break;
                    }
                    sent += wrote;
                }
                if (!writeOk) break;
            }
        }

        connected_.store(false);
        Log("pipe: client disconnected");
        if (onDisconnect) {
            try { onDisconnect(); } catch (...) {}
        }

        DisconnectNamedPipe(h);
        CloseHandle(h);
        pipe_ = nullptr;
    }

    Log("pipe: server stopped");
}

}  // namespace bridge
