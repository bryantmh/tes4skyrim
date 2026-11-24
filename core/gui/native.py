"""What the Slint window needs from Windows itself: file and folder pickers, dropped files,
opening folders and links, and whether the user runs Windows in dark mode.

Slint draws the window but offers no native dialogs and no file-drop event, so these call the
Win32 API through ctypes. Every function degrades to a harmless default off Windows.
See: docs/commentary/core_gui.md#native-pieces
"""

import ctypes
import os
import queue
import sys
import webbrowser
from ctypes import wintypes

#: Windows message a window receives when files are dropped on it (DragAcceptFiles).
WM_DROPFILES = 0x0233
#: Our subclass id; any constant unique to this process works.
SUBCLASS_ID = 0x7E5A
#: SHBrowseForFolder flags: file-system folders only, new dialog style, edit box.
BIF_FLAGS = 0x0001 | 0x0040 | 0x0010
#: GetOpenFileName flags: path and file must exist, no network button, explorer style.
OFN_FLAGS = 0x00001000 | 0x00000800 | 0x00020000 | 0x00080000
#: Longest path a picker returns.
MAX_PATH_CHARS = 4096

# ---------------------------------------------------------------------------
#  Pickers, folders and links
# ---------------------------------------------------------------------------

class OPENFILENAMEW(ctypes.Structure):
    """The GetOpenFileNameW argument block."""

    _fields_ = (("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
                ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
                ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
                ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
                ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
                ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
                ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
                ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
                ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
                ("FlagsEx", wintypes.DWORD))


class BROWSEINFOW(ctypes.Structure):
    """The SHBrowseForFolderW argument block."""

    _fields_ = (("hwndOwner", wintypes.HWND), ("pidlRoot", ctypes.c_void_p),
                ("pszDisplayName", wintypes.LPWSTR), ("lpszTitle", wintypes.LPCWSTR),
                ("ulFlags", wintypes.UINT), ("lpfn", ctypes.c_void_p),
                ("lParam", wintypes.LPARAM), ("iImage", ctypes.c_int))


def os_dark() -> bool:
    """True when Windows apps are set to dark mode."""
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def pick_file(title: str, filters: list, owner=None) -> str:
    """The file the user picked, or "". `filters` is [(label, "*.7z;*.zip"), ...]."""
    if sys.platform != "win32":
        return ""
    spec = "".join(f"{label}\0{pattern}\0" for label, pattern in filters) + "\0"
    buf = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
    ofn = OPENFILENAMEW(lStructSize=ctypes.sizeof(OPENFILENAMEW), hwndOwner=owner,
                        lpstrFilter=spec, lpstrFile=ctypes.cast(buf, wintypes.LPWSTR),
                        nMaxFile=MAX_PATH_CHARS, lpstrTitle=title, Flags=OFN_FLAGS)
    if not ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return ""
    return buf.value


def pick_folder(title: str, owner=None) -> str:
    """The folder the user picked, or ""."""
    if sys.platform != "win32":
        return ""
    shell = ctypes.windll.shell32
    shell.SHBrowseForFolderW.restype = ctypes.c_void_p
    name = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
    info = BROWSEINFOW(hwndOwner=owner, pszDisplayName=ctypes.cast(name, wintypes.LPWSTR),
                       lpszTitle=title, ulFlags=BIF_FLAGS)
    pidl = shell.SHBrowseForFolderW(ctypes.byref(info))
    if not pidl:
        return ""
    path = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
    ok = shell.SHGetPathFromIDListW(ctypes.c_void_p(pidl), path)
    ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
    return path.value if ok else ""


def open_path(path) -> bool:
    """Open a folder or file in Explorer; False when it does not exist."""
    if not path or not os.path.exists(path):
        return False
    if sys.platform == "win32":
        os.startfile(str(path))
    return True


def open_url(url: str) -> None:
    """Open a web page in the default browser."""
    webbrowser.open(url)


# ---------------------------------------------------------------------------
#  Dropped files
# ---------------------------------------------------------------------------

#: The subclass procedure's C signature (comctl32 SUBCLASSPROC).
SUBCLASSPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM, ctypes.c_size_t,
                                  ctypes.c_size_t)


def find_window(title_prefix: str):
    """This process's visible top-level window whose title starts with `title_prefix`."""
    if sys.platform != "win32":
        return None
    user = ctypes.windll.user32
    found = []
    pid = os.getpid()
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _visit(hwnd, _lparam):
        """Keep the first matching window."""
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        text = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(hwnd, text, 512)
        if owner.value == pid and text.value.startswith(title_prefix):
            found.append(hwnd)
            return False
        return True

    user.EnumWindows(enum_proc(_visit), 0)
    return found[0] if found else None


def _dropped_paths(hdrop) -> list:
    """Every path in an HDROP, then release it."""
    shell = ctypes.windll.shell32
    shell.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    count = shell.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    out = []
    for i in range(count):
        buf = ctypes.create_unicode_buffer(MAX_PATH_CHARS)
        shell.DragQueryFileW(hdrop, i, buf, MAX_PATH_CHARS)
        out.append(buf.value)
    shell.DragFinish.argtypes = [ctypes.c_void_p]
    shell.DragFinish(hdrop)
    return out


class DropTarget:
    """Collects files dropped on a window; `take()` returns them on the UI thread.

    The window toolkit registers its own OLE drop target, which never reports the drop to us,
    so it is revoked and the classic WM_DROPFILES route is used instead.
    """

    def __init__(self):
        """Nothing attached yet."""
        self.paths = queue.Queue()
        self.hwnd = None
        self._proc = None

    def attach(self, hwnd) -> bool:
        """Start receiving drops on `hwnd`; False when it could not be attached."""
        if sys.platform != "win32" or not hwnd:
            return False
        comctl = ctypes.windll.comctl32
        comctl.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        comctl.DefSubclassProc.restype = ctypes.c_ssize_t
        self._proc = SUBCLASSPROC(self._on_message)
        ctypes.windll.ole32.RevokeDragDrop(hwnd)
        ctypes.windll.shell32.DragAcceptFiles(hwnd, True)
        self.hwnd = hwnd
        return bool(comctl.SetWindowSubclass(hwnd, self._proc, SUBCLASS_ID, 0))

    def _on_message(self, hwnd, msg, wparam, lparam, _id, _data):
        """Queue dropped paths; pass every other message on."""
        if msg == WM_DROPFILES:
            for path in _dropped_paths(wparam):
                self.paths.put(path)
            return 0
        return ctypes.windll.comctl32.DefSubclassProc(hwnd, msg, wparam, lparam)

    def take(self) -> list:
        """Every path dropped since the last call."""
        out = []
        while not self.paths.empty():
            out.append(self.paths.get_nowait())
        return out


# ---------------------------------------------------------------------------
#  Window icon
# ---------------------------------------------------------------------------

#: The process's own taskbar identity, so Windows groups it and shows our icon, not Python's.
APP_USER_MODEL_ID = "TESConversion.TESRACT.GUI"
#: Loaded icon handles; freeing one blanks the taskbar button, so they live as long as the process.
ICON_HANDLES = []


def set_app_id() -> None:
    """Give the process its own taskbar identity (call before the window opens)."""
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def set_window_icon(hwnd, icon_path) -> None:
    """Load the .ico at the small (title bar) and big (taskbar, Alt-Tab) sizes Windows asks for."""
    if sys.platform != "win32" or not hwnd:
        return
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    image_icon, load_from_file, wm_seticon = 1, 0x0010, 0x0080
    for which, (cx, cy) in ((1, (user32.GetSystemMetrics(11), user32.GetSystemMetrics(12))),
                            (0, (user32.GetSystemMetrics(49), user32.GetSystemMetrics(50)))):
        handle = user32.LoadImageW(None, str(icon_path), image_icon, cx, cy, load_from_file)
        if handle:
            ICON_HANDLES.append(handle)
            user32.SendMessageW(hwnd, wm_seticon, which, handle)
