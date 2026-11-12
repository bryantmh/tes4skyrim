"""Start a process on a hidden desktop, suspended, with DLLs injected first.

The same shape as skse64_loader (CreateProcess suspended, LoadLibrary on a
remote thread, resume), with two additions: the process runs on its own
desktop, so it can never take focus, input or the screen, and any number of
DLLs load in order before its first instruction.

See: docs/commentary/headless_testing.md#hidden-launch
"""
import ctypes
import os
from ctypes import wintypes

from PIL import Image

_K32 = ctypes.WinDLL('kernel32', use_last_error=True)
_U32 = ctypes.WinDLL('user32', use_last_error=True)

CREATE_SUSPENDED = 0x4
CREATE_UNICODE_ENVIRONMENT = 0x400
MEM_COMMIT_RESERVE = 0x3000
PAGE_READWRITE = 0x4
GENERIC_ALL = 0x10000000
WAIT_OBJECT_0 = 0
DUPLICATE_SAME_ACCESS = 0x2
PAGE_EXECUTE_READWRITE = 0x40
#: skse64_loader's remote stub, byte for byte: LoadLibraryA(path), GetProcAddress(dll, 1), call it.
SKSE_STUB = bytes((0x48, 0x83, 0xEC, 0x28, 0x48, 0x8B, 0xD9, 0x48, 0x8D, 0x4B, 0x10, 0xFF, 0x13,
                   0x48, 0x8B, 0xC8, 0xBA, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x53, 0x08, 0xFF, 0xD0,
                   0x48, 0x83, 0xC4, 0x28, 0xC3))
#: A remote LoadLibrary that has not returned in this long has hung the loader.
INJECT_TIMEOUT_MS = 60000


class StartupInfo(ctypes.Structure):
    """STARTUPINFOW."""
    _fields_ = (('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR),
                ('lpDesktop', wintypes.LPWSTR), ('lpTitle', wintypes.LPWSTR),
                ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
                ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD),
                ('dwXCountChars', wintypes.DWORD), ('dwYCountChars', wintypes.DWORD),
                ('dwFillAttribute', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                ('lpReserved2', ctypes.c_void_p), ('hStdInput', wintypes.HANDLE),
                ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE))


class ProcessInformation(ctypes.Structure):
    """PROCESS_INFORMATION."""
    _fields_ = (('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE),
                ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD))


_U32.CreateDesktopW.restype = wintypes.HANDLE
_U32.CreateDesktopW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
_K32.VirtualAllocEx.restype = ctypes.c_void_p
_K32.VirtualAllocEx.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                wintypes.DWORD, wintypes.DWORD)
_K32.WriteProcessMemory.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_size_t, ctypes.c_void_p)
_K32.GetProcAddress.restype = ctypes.c_void_p
_K32.GetProcAddress.argtypes = (wintypes.HMODULE, ctypes.c_char_p)
_K32.GetModuleHandleW.restype = wintypes.HMODULE
_K32.CreateRemoteThread.restype = wintypes.HANDLE
_K32.CreateRemoteThread.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.c_void_p)
_K32.GetCurrentProcess.restype = wintypes.HANDLE
_K32.DuplicateHandle.argtypes = (wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                 ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                 wintypes.BOOL, wintypes.DWORD)
_K32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_K32.GetExitCodeThread.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
_K32.CloseHandle.argtypes = (wintypes.HANDLE,)
_K32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_K32.ResumeThread.argtypes = (wintypes.HANDLE,)
_U32.CloseDesktop.argtypes = (wintypes.HANDLE,)


_G32 = ctypes.WinDLL('gdi32')
DESKTOP_READOBJECTS_ENUMERATE = 0x0041
PW_RENDERFULLCONTENT = 2
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_U32.OpenDesktopW.restype = wintypes.HANDLE
_U32.OpenDesktopW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_U32.EnumDesktopWindows.argtypes = (wintypes.HANDLE, _ENUM_PROC, wintypes.LPARAM)
_U32.GetDC.restype = wintypes.HANDLE
_U32.GetDC.argtypes = (wintypes.HWND,)
_U32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HANDLE)
_U32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HANDLE, wintypes.UINT)
_G32.CreateCompatibleDC.restype = wintypes.HANDLE
_G32.CreateCompatibleDC.argtypes = (wintypes.HANDLE,)
_G32.CreateCompatibleBitmap.restype = wintypes.HANDLE
_G32.CreateCompatibleBitmap.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_int)
_G32.SelectObject.restype = wintypes.HANDLE
_G32.SelectObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_G32.GetBitmapBits.argtypes = (wintypes.HANDLE, ctypes.c_long, ctypes.c_void_p)
_G32.DeleteObject.argtypes = (wintypes.HANDLE,)
_G32.DeleteDC.argtypes = (wintypes.HANDLE,)


def _find_window(desk, window_class: str) -> int:
    """The first visible window of `window_class` on the open desktop `desk`, or 0."""
    found = []

    def visit(hwnd, _):
        """Records a visible `window_class` window; False stops the walk."""
        name = ctypes.create_unicode_buffer(256)
        _U32.GetClassNameW(hwnd, name, 256)
        if name.value == window_class and _U32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return not found

    _U32.EnumDesktopWindows(desk, _ENUM_PROC(visit), 0)
    return found[0] if found else 0


def capture(desktop: str, window_class: str):
    """An image of the `window_class` window on `desktop`, or None when there is none.

    The desktop stays open throughout: once this process closes its handle,
    the window's handle stops being valid here.
    """
    desk = _U32.OpenDesktopW(desktop, 0, False, DESKTOP_READOBJECTS_ENUMERATE)
    if not desk:
        return None
    try:
        hwnd = _find_window(desk, window_class)
        return _capture_window(hwnd) if hwnd else None
    finally:
        _U32.CloseDesktop(desk)


def _capture_window(hwnd: int) -> Image.Image:
    """An image of `hwnd` drawn by PrintWindow, which works on a hidden desktop."""
    rect = wintypes.RECT()
    _U32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    screen = _U32.GetDC(None)
    mem = _G32.CreateCompatibleDC(screen)
    bitmap = _G32.CreateCompatibleBitmap(screen, width, height)
    try:
        _G32.SelectObject(mem, bitmap)
        _check(_U32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT), 'PrintWindow')
        pixels = ctypes.create_string_buffer(width * height * 4)
        _G32.GetBitmapBits(bitmap, len(pixels), pixels)
        return Image.frombuffer('RGBA', (width, height), pixels, 'raw', 'BGRA', 0, 1).convert('RGB')
    finally:
        _G32.DeleteObject(bitmap)
        _G32.DeleteDC(mem)
        _U32.ReleaseDC(None, screen)


PROCESS_TERMINATE_QUERY = 0x1001
SYNCHRONIZE = 0x100000
_K32.OpenProcess.restype = wintypes.HANDLE
_K32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_K32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD))


def process_image(pid: int) -> str:
    """The full exe path of running process `pid`, or "" when it is gone."""
    handle = _K32.OpenProcess(PROCESS_TERMINATE_QUERY, False, pid)
    if not handle:
        return ''
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _K32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ''
        return buf.value
    finally:
        _K32.CloseHandle(handle)


def terminate(pid: int, required_dir: str) -> bool:
    """Kills `pid` only if its exe lives under `required_dir`, and waits up to 30 s
    for it to exit; whether it was killed."""
    image = process_image(pid)
    root = os.path.normcase(os.path.abspath(required_dir)) + os.sep
    if not image or not os.path.normcase(image).startswith(root):
        return False
    handle = _K32.OpenProcess(PROCESS_TERMINATE_QUERY | SYNCHRONIZE, False, pid)
    try:
        killed = bool(handle and _K32.TerminateProcess(handle, 0))
        if killed:
            _K32.WaitForSingleObject(handle, 30000)
        return killed
    finally:
        if handle:
            _K32.CloseHandle(handle)


def _check(ok, what: str):
    """Raises OSError naming `what` when a Win32 call failed."""
    if not ok:
        raise OSError(ctypes.get_last_error(), '%s failed' % what)
    return ok


def _environment_block(extra: dict) -> ctypes.Array:
    """A CREATE_UNICODE_ENVIRONMENT block: this process's variables plus `extra`."""
    env = dict(os.environ)
    env.update(extra)
    text = ''.join('%s=%s\0' % kv for kv in sorted(env.items())) + '\0'
    return ctypes.create_unicode_buffer(text, len(text))


def _kernel32(name: bytes) -> int:
    """`name`'s address in kernel32, which every process maps at the same base."""
    return _K32.GetProcAddress(_K32.GetModuleHandleW('kernel32.dll'), name)


def _remote_block(process, dll_path: str, entry_ordinal: int):
    """Writes the injection payload; (thread start, argument) to run it.

    With an `entry_ordinal`, the payload is skse64_loader's own stub
    (`InjectDLLThreadData`, Inject.cpp): LoadLibraryA, then GetProcAddress by
    ordinal, then a call to that export. Without one, plain LoadLibraryW.
    """
    if entry_ordinal is None:
        data = (dll_path + '\0').encode('utf-16-le')
        protect, start = PAGE_READWRITE, _kernel32(b'LoadLibraryW')
    else:
        path = dll_path.encode('mbcs').ljust(2048, b'\0')
        data = (_kernel32(b'LoadLibraryA').to_bytes(8, 'little') +
                _kernel32(b'GetProcAddress').to_bytes(8, 'little') + path +
                SKSE_STUB.replace(b'\x01\x00\x00\x00', entry_ordinal.to_bytes(4, 'little'), 1))
        protect, start = PAGE_EXECUTE_READWRITE, None
    remote = _check(_K32.VirtualAllocEx(process, None, len(data), MEM_COMMIT_RESERVE, protect),
                    'VirtualAllocEx')
    _check(_K32.WriteProcessMemory(process, remote, data, len(data), None), 'WriteProcessMemory')
    return (start, remote) if start else (remote + 16 + 2048, remote)


def inject(process, dll_path: str, entry_ordinal: int = None) -> None:
    """Loads `dll_path` into the suspended `process`, calls its `entry_ordinal` export if given."""
    start, argument = _remote_block(process, dll_path, entry_ordinal)
    thread = _check(_K32.CreateRemoteThread(process, None, 0, start, argument, 0, None),
                    'CreateRemoteThread')
    try:
        if _K32.WaitForSingleObject(thread, INJECT_TIMEOUT_MS) != WAIT_OBJECT_0:
            raise OSError('LoadLibrary of %s did not return' % dll_path)
        code = wintypes.DWORD()
        _check(_K32.GetExitCodeThread(thread, ctypes.byref(code)), 'GetExitCodeThread')
        if entry_ordinal is None and not code.value:
            raise OSError('LoadLibrary of %s returned null' % dll_path)
    finally:
        _K32.CloseHandle(thread)


def launch(exe: str, cwd: str, desktop: str, dlls: list, env: dict, args: str = '') -> int:
    """Starts `exe` (with command-line `args`) on desktop `desktop`, injects `dlls` in order, resumes it; its pid.

    Each of `dlls` is a path, or (path, entry ordinal) for a DLL that must be
    started by an export, as SKSE is by ordinal 1 (StartSKSE).

    The process is terminated if any injection fails, so a half-loaded game
    never runs. It holds its own handle to the desktop, which keeps the
    desktop alive after this process exits.
    """
    desk = _check(_U32.CreateDesktopW(desktop, None, None, 0, GENERIC_ALL, None),
                  'CreateDesktopW')
    info = StartupInfo(cb=ctypes.sizeof(StartupInfo), lpDesktop='WinSta0\\' + desktop)
    proc = ProcessInformation()
    block = _environment_block(env)
    command = ctypes.create_unicode_buffer('"%s" %s' % (exe, args)) if args else None
    try:
        _check(_K32.CreateProcessW(exe, command, None, None, False,
                                   CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT, block, cwd,
                                   ctypes.byref(info), ctypes.byref(proc)), 'CreateProcessW')
        kept = wintypes.HANDLE()
        _check(_K32.DuplicateHandle(_K32.GetCurrentProcess(), wintypes.HANDLE(desk), proc.hProcess,
                                    ctypes.byref(kept), 0, False, DUPLICATE_SAME_ACCESS),
               'DuplicateHandle')
        try:
            for dll in dlls:
                path, ordinal = dll if isinstance(dll, tuple) else (dll, None)
                inject(proc.hProcess, path, ordinal)
        except OSError:
            _K32.TerminateProcess(proc.hProcess, 1)
            raise
        _K32.ResumeThread(proc.hThread)
        return proc.dwProcessId
    finally:
        for handle in (proc.hThread, proc.hProcess):
            if handle:
                _K32.CloseHandle(handle)
        _U32.CloseDesktop(desk)
