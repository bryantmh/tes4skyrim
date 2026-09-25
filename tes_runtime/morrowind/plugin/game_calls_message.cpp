// `MessageBox` WITH buttons: the engine's own modal, one layer below
// Debug.MessageBox, which hard-codes a lone "OK" and so cannot carry a
// Morrowind script's buttons.
// See: docs/commentary/morrowind_runtime.md#messagebox-buttons

#include "game_calls_internal.h"

#include <string>
#include <vector>

#include "ids.h"
#include "log.h"
#include "main_thread.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// What the engine calls with the clicked button's index. The engine wraps it
// in an IMessageBoxCallback of its own, whose one handler is
// `mov rax,[rcx+0x10] / movzx ecx,dl / jmp rax` -- so a plain function
// pointer is the whole contract, with no object to build.
using ResultFn = void (*)(unsigned int button);

// ShowMessageBox(text, callback, unknown, kind, arg5, button...), the function
// Debug.MessageBox tail-calls.
//
// 🛑 The buttons are VARIADIC `const char*` arguments ended by a null, NOT an
// array: the callee walks the stack upward from argument 7 ([rbp+0x7f], which
// is rsp+0x30 at entry), and the wrapper's argument 6 is the literal string
// "OK" rather than a pointer to it. Passing an array instead puts pointer
// VALUES where the engine reads text, which draws one garbled button.
// See: docs/commentary/morrowind_runtime.md#messagebox-buttons
using ShowMessageBoxFn = void (*)(const char* text, ResultFn callback,
                                  bool unknown, std::uint32_t kind,
                                  std::uint32_t arg5, const char* b0,
                                  const char* b1, const char* b2,
                                  const char* b3, const char* b4,
                                  const char* b5, const char* b6,
                                  const char* b7, const char* b8,
                                  const char* b9, const char* end);

ShowMessageBoxFn g_showMessageBox = nullptr;

// Debug.Notification(string), global, so its self slot is a tag.
using NotificationFn = void (*)(void* vm, std::uint32_t stack, void* tag,
                                void* text);

NotificationFn g_notification = nullptr;

// The engine reports the click on the game thread, and a script only ever
// reads it from its own tick, so the plain store is enough.
void OnButton(unsigned int button) {
    State().buttonPressed = static_cast<int>(button);
}

//: The constants the wrapper passes beside the text and the buttons.
constexpr std::uint32_t kKind = 4;
constexpr std::uint32_t kArg5 = 0xa;
//: Both engines cap a message box at 10 buttons.
constexpr std::size_t kMaxButtons = 10;

}  // namespace

// The click lands in `buttonPressed`, which `GetButtonPressed` hands out once.
// TES3 scripts POLL for it rather than waiting, so the box does not block: the
// script returns, and a later tick reads the answer.
// See: docs/commentary/morrowind_runtime.md#messagebox-buttons
void ShowButtonMessage(const std::string& text,
                       const std::vector<std::string>& buttons) {
    if (!g_showMessageBox) {
        Log("game: MessageBox \"%s\" -- ShowMessageBox unresolved",
            text.c_str());
        return;
    }
    PostToMainThread([text, buttons]() {
        const char* name[kMaxButtons] = {nullptr};
        for (std::size_t i = 0; i < buttons.size() && i < kMaxButtons; ++i) {
            name[i] = buttons[i].c_str();
        }
        g_showMessageBox(text.c_str(), OnButton, false, kKind, kArg5, name[0],
                         name[1], name[2], name[3], name[4], name[5], name[6],
                         name[7], name[8], name[9], nullptr);
    });
}

void Notify(const std::string& text) {
    void* message = nullptr;
    if (text.empty() || !g_notification ||
        !FixedString(&message, text.c_str())) {
        return;
    }
    g_notification(PapyrusVm(), 0, nullptr, &message);
}

void InstallMessageCalls() {
    g_showMessageBox = Native<ShowMessageBoxFn>("ShowMessageBox",
                                                ids::kShowMessageBox);
    g_notification = Native<NotificationFn>("Debug.Notification",
                                            ids::kDebugNotification);
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
