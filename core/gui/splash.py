"""The splash shown from the first moment TESRACT starts until its window is ready.

A layered Win32 popup on its own thread with its own message loop, so the main thread can import,
build the window and read every screen's data without the splash ever freezing. The banner sits on
the theme's dark panel above a sliding load bar; each frame is drawn with PIL and handed to Windows
with UpdateLayeredWindow. The window procedure is DefWindowProcW itself, so Windows never calls
back into Python. Does nothing off Windows.
See: docs/commentary/core_gui.md#splash
"""

import ctypes
import sys
import threading
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw

#: The banner the splash shows.
BANNER = Path(__file__).resolve().parents[2] / "docs" / "assets" / "banner.png"
#: Panel size in pixels.
SIZE = (560, 250)
#: Panel, border and load-bar colors (the dark theme's bg, line and accent).
PANEL, BORDER, ACCENT = (16, 18, 22, 255), (60, 66, 76, 255), (212, 163, 82, 255)
#: The load bar's track (left, top, right, bottom) and its moving segment's width, in pixels.
TRACK, SEGMENT = (90, 206, SIZE[0] - 90, 210), 110
#: Pixels the segment moves per frame.
STEP = 9
#: Time between frames, in ms.
FRAME_MS = 30
#: Win32 constants: layered and popup styles, UpdateLayeredWindow's alpha mode, PeekMessage removal.
WS_EX_LAYERED, WS_POPUP, ULW_ALPHA, PM_REMOVE, QS_ALLINPUT = 0x80000, 0x80000000, 2, 1, 0x04FF


class WNDCLASSEXW(ctypes.Structure):
    """The RegisterClassExW argument block."""

    _fields_ = (("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON))


class BLENDFUNCTION(ctypes.Structure):
    """How UpdateLayeredWindow blends: per-pixel alpha, fully opaque overall."""

    _fields_ = (("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte))


def picture() -> Image.Image:
    """The splash's still part: the banner's opaque part centered on a bordered dark panel, the bar's track."""
    img = Image.new("RGBA", SIZE, PANEL)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, SIZE[0] - 1, SIZE[1] - 1), outline=BORDER)
    try:
        with Image.open(BANNER) as banner:
            banner = banner.convert("RGBA")
            banner = banner.crop(banner.getchannel("A").getbbox() or (0, 0, *banner.size))
            banner.thumbnail((SIZE[0] - 80, SIZE[1] - 110))
            img.alpha_composite(banner, ((SIZE[0] - banner.width) // 2, 36))
    except OSError:
        pass
    draw.rounded_rectangle(TRACK, radius=2, fill=BORDER)
    return img


def frame(base: Image.Image, n: int) -> bytes:
    """Frame `n`: `base` with the load bar's segment slid in from the left, as premultiplied BGRA bytes."""
    left, top, right, bottom = TRACK
    start = left - SEGMENT + (n * STEP) % (right - left + SEGMENT)
    img = base.copy()
    x0, x1 = max(start, left), min(start + SEGMENT, right)
    if x1 > x0:
        ImageDraw.Draw(img).rounded_rectangle((x0, top, x1, bottom), radius=2, fill=ACCENT)
    return img.convert("RGBa").tobytes("raw", "BGRa")


def _prototypes(user, gdi) -> None:
    """Declare the handle-carrying calls: ctypes' default int return would cut 64-bit handles in half."""
    user.GetDC.restype = wintypes.HDC
    user.GetDC.argtypes = (wintypes.HWND,)
    user.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user.UpdateLayeredWindow.argtypes = (wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.HDC, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.DWORD)
    user.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user.DestroyWindow.argtypes = (wintypes.HWND,)
    gdi.CreateDIBSection.restype = wintypes.HBITMAP
    gdi.CreateDIBSection.argtypes = (wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
                                     wintypes.HANDLE, wintypes.DWORD)
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi.DeleteDC.argtypes = (wintypes.HDC,)
    gdi.DeleteObject.argtypes = (wintypes.HGDIOBJ,)


def _bitmap(size, screen_dc):
    """(memory DC, bitmap, pixel pointer) of a top-down 32-bit DIB of `size` for the frames."""
    gdi = ctypes.windll.gdi32
    header = (ctypes.c_uint32 * 10)(40, size[0], (-size[1]) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = gdi.CreateDIBSection(screen_dc, header, 0, ctypes.byref(bits), None, 0)
    mem = gdi.CreateCompatibleDC(screen_dc)
    gdi.SelectObject(mem, bmp)
    return mem, bmp, bits


class Splash:
    """Show with `show()`, take down with `close()`; both are safe to call more than once."""

    def __init__(self):
        """Nothing shown yet."""
        self._stop = threading.Event()
        self._thread = None

    def show(self) -> None:
        """Put the splash on screen (returns at once; the splash runs on its own thread)."""
        if sys.platform != "win32" or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, args=(picture(),), daemon=True, name="splash")
        self._thread.start()

    def close(self) -> None:
        """Take the splash down and wait for its thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self, img: Image.Image) -> None:
        """Create the window, show the frames, and pump messages until closed."""
        user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
        _prototypes(user, gdi)
        hwnd = self._create(user, img.size)
        if not hwnd:
            return
        screen = user.GetDC(None)
        mem, bmp, bits = _bitmap(img.size, screen)
        work = wintypes.RECT()
        user.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)
        point = wintypes.POINT((work.left + work.right - img.width) // 2, (work.top + work.bottom - img.height) // 2)
        size, origin = wintypes.SIZE(*img.size), wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)

        def draw(n):
            """Put frame `n` on screen."""
            data = frame(img, n)
            ctypes.memmove(bits, data, len(data))
            user.UpdateLayeredWindow(hwnd, screen, ctypes.byref(point), ctypes.byref(size), mem,
                                     ctypes.byref(origin), 0, ctypes.byref(blend), ULW_ALPHA)

        draw(0)
        user.ShowWindow(hwnd, 4)
        self._pump(user, draw)
        user.DestroyWindow(hwnd)
        gdi.DeleteDC(mem)
        gdi.DeleteObject(bmp)
        user.ReleaseDC(None, screen)

    def _create(self, user, size):
        """The splash's popup window, its class registered with DefWindowProcW; None on failure."""
        kernel = ctypes.windll.kernel32
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        instance = kernel.GetModuleHandleW(None)
        wc = WNDCLASSEXW(ctypes.sizeof(WNDCLASSEXW), 0, ctypes.cast(user.DefWindowProcW, ctypes.c_void_p).value,
                         0, 0, instance, None, None, None, None, "TESRACTSplash", None)
        user.RegisterClassExW(ctypes.byref(wc))
        user.CreateWindowExW.restype = wintypes.HWND
        user.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                         wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p)
        return user.CreateWindowExW(WS_EX_LAYERED, "TESRACTSplash", "TESRACT", WS_POPUP, 0, 0, size[0], size[1],
                                    None, None, instance, None)

    def _pump(self, user, draw) -> None:
        """Answer the splash's messages and draw the next frame every FRAME_MS until `close()` is called."""
        msg = wintypes.MSG()
        n = 0
        while not self._stop.is_set():
            user.MsgWaitForMultipleObjects(0, None, False, FRAME_MS, QS_ALLINPUT)
            while user.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user.TranslateMessage(ctypes.byref(msg))
                user.DispatchMessageW(ctypes.byref(msg))
            n += 1
            draw(n)
