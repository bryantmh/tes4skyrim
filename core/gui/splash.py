"""The splash shown from the first moment TESRACT starts until its window is ready.

Started from the release folder, the native launcher (`native/src/launcher`) already shows the splash
and names its window in SPLASH_ENV; `Splash` then only closes that window when ours is ready. Started
any other way, `Splash` shows its own: a layered Win32 popup on its own thread and message loop, so the
main thread can import, build the window and read every screen's data without the splash freezing.
Both draw the same pixels: the still part comes from `picture`, rendered at the display's scale (by
the build into SPLASH_FILE for the launcher), and the load bar is a rectangle sliding along its track.
The process is made per-monitor DPI aware first, so Windows never rescales or moves the splash.
Does nothing off Windows.
See: docs/commentary/core_gui.md#splash
"""

import ctypes
import os
import struct
import sys
import threading
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw

#: The banner the splash shows.
BANNER = Path(__file__).resolve().parents[2] / "docs" / "assets" / "banner.png"
#: Panel size in pixels at 100% display scale.
SIZE = (560, 250)
#: Panel, border and load-bar colors (the dark theme's bg, line and accent).
PANEL, BORDER, ACCENT = (16, 18, 22, 255), (60, 66, 76, 255), (212, 163, 82, 255)
#: The load bar's track (left, top, right, bottom) and its moving segment's width, at 100%.
TRACK, SEGMENT = (90, 206, SIZE[0] - 90, 210), 110
#: Pixels the segment moves per frame at 100%.
STEP = 9
#: Time between frames, in ms.
FRAME_MS = 30
#: The launcher's splash window handle, in decimal, when the launcher started this process.
SPLASH_ENV = "TESRACT_SPLASH"
#: The splash file the launcher draws from, written by the build beside the app's TESRACT.exe.
SPLASH_FILE = "splash.bin"
#: Display scales, in percent, the splash file carries; the launcher takes the largest not above its own.
FILE_SCALES = (100, 125, 150, 175, 200, 250, 300)
#: Win32 window styles: layered (per-pixel alpha) and popup (no frame).
WS_EX_LAYERED, WS_POPUP = 0x80000, 0x80000000
#: Win32 message constants: UpdateLayeredWindow alpha mode, PeekMessage removal, all input, WM_CLOSE.
ULW_ALPHA, PM_REMOVE, QS_ALLINPUT, WM_CLOSE = 2, 1, 0x04FF, 0x0010
#: The per-monitor-v2 DPI awareness context.
DPI_PER_MONITOR_V2 = -4


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


# ---------------------------------------------------------------------------
#  The picture
# ---------------------------------------------------------------------------

def geometry(scale: float) -> tuple:
    """(size, track, segment, step) at display `scale` (1.0 is 100%)."""
    return (tuple(round(v * scale) for v in SIZE), tuple(round(v * scale) for v in TRACK),
            round(SEGMENT * scale), max(1, round(STEP * scale)))


def picture(scale: float = 1.0) -> Image.Image:
    """The splash's still part at `scale`: the banner centered on a bordered dark panel, the bar's track."""
    size, track, _segment, _step = geometry(scale)
    img = Image.new("RGBA", size, PANEL)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=BORDER, width=max(1, round(scale)))
    try:
        with Image.open(BANNER) as banner:
            banner = banner.convert("RGBA")
            banner = banner.crop(banner.getchannel("A").getbbox() or (0, 0, *banner.size))
            banner.thumbnail((size[0] - round(80 * scale), size[1] - round(110 * scale)), Image.LANCZOS)
            img.alpha_composite(banner, ((size[0] - banner.width) // 2, round(36 * scale)))
    except OSError:
        pass
    draw.rectangle((track[0], track[1], track[2] - 1, track[3] - 1), fill=BORDER)
    return img


def segment_span(track: tuple, segment: int, step: int, n: int) -> tuple:
    """(x0, x1) of the load bar's moving segment in frame `n`, clipped to the track (x1 > x0 when visible)."""
    left, right = track[0], track[2]
    start = left - segment + (n * step) % (right - left + segment)
    return max(start, left), min(start + segment, right)


def frame(base: Image.Image, n: int, scale: float = 1.0) -> bytes:
    """Frame `n`: `base` with the load bar's segment drawn, as premultiplied BGRA bytes."""
    _size, track, segment, step = geometry(scale)
    img = base.copy()
    x0, x1 = segment_span(track, segment, step, n)
    if x1 > x0:
        ImageDraw.Draw(img).rectangle((x0, track[1], x1 - 1, track[3] - 1), fill=ACCENT)
    return img.convert("RGBa").tobytes("raw", "BGRa")


def write_splash_file(path: Path) -> None:
    """Write the launcher's splash file: per scale in FILE_SCALES, its geometry then its still picture.

    Layout (little-endian u32s): b"TSPL", count; per scale: percent, width, height, track left, top,
    right, bottom, segment, step, frame ms, accent as 0xAARRGGBB; then width*height premultiplied BGRA.
    """
    out = [b"TSPL", struct.pack("<I", len(FILE_SCALES))]
    for percent in FILE_SCALES:
        size, track, segment, step = geometry(percent / 100)
        r, g, b, a = ACCENT
        out.append(struct.pack("<11I", percent, *size, *track, segment, step, FRAME_MS,
                               (a << 24) | (r << 16) | (g << 8) | b))
        out.append(picture(percent / 100).convert("RGBa").tobytes("raw", "BGRa"))
    path.write_bytes(b"".join(out))


# ---------------------------------------------------------------------------
#  The window
# ---------------------------------------------------------------------------

def make_dpi_aware() -> float:
    """Make the process per-monitor DPI aware (before any window exists); returns the display scale."""
    user = ctypes.windll.user32
    user.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
    if not user.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_PER_MONITOR_V2)):
        user.SetProcessDPIAware()
    return user.GetDpiForSystem() / 96


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
    user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
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
        """Nothing shown yet; a splash the launcher shows is adopted from SPLASH_ENV."""
        self._stop = threading.Event()
        self._thread = None
        self._launcher = int(os.environ.pop(SPLASH_ENV, "0") or 0)

    def show(self) -> None:
        """Put the splash on screen unless the launcher already did (returns at once)."""
        if sys.platform != "win32" or self._thread is not None:
            return
        scale = make_dpi_aware()
        if self._launcher:
            return
        self._thread = threading.Thread(target=self._run, args=(picture(scale), scale), daemon=True,
                                        name="splash")
        self._thread.start()

    def close(self) -> None:
        """Take the splash down (ours, or the launcher's, which then exits)."""
        if self._launcher:
            _prototypes(ctypes.windll.user32, ctypes.windll.gdi32)
            ctypes.windll.user32.PostMessageW(self._launcher, WM_CLOSE, 0, 0)
            self._launcher = 0
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self, img: Image.Image, scale: float) -> None:
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
            data = frame(img, n, scale)
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
