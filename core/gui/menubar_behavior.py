"""What Tk does not give the top menu bar on its own: entry tooltips, and moving
between dropdowns by hover once one is open.

On Windows a `tk.Menu` is a native popup run by `TrackPopupMenu`. Its entries
get no `<Enter>`/`<Leave>`; Tk reports the highlighted entry through
`<<MenuSelect>>`, and keeps servicing `after` timers while the popup is up --
both features ride on those two. The open popup is the Win32 window of class
`#32768`, which gives a tip its anchor, and `EndMenu` is the only way to close
it from outside Tk.
"""

import ctypes
import ctypes.wintypes
import sys

from core.gui.widgets import tooltip_window

#: Hover time before an entry's tip appears, and the tip's wrap width.
TIP_DELAY_MS = 350
TIP_WIDTH = 340

#: How often an open dropdown checks where the pointer is.
WATCH_MS = 40

#: Ticks a just-posted dropdown may take to appear before the watch gives up.
APPEAR_TICKS = 25

#: Win32 window class of an open popup menu.
_MENU_CLASS = "#32768"


# ---------------------------------------------------------------------------
#  The open popup
# ---------------------------------------------------------------------------

def native_menu_rect():
    """Screen (left, top, right, bottom) of the open popup menu, or None."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(_MENU_CLASS, None)
    if not hwnd or not user32.IsWindowVisible(hwnd):
        return None
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _menu_open(menu) -> bool:
    """Whether `menu` (or, on Windows, any popup menu) is showing."""
    if sys.platform == "win32":
        return native_menu_rect() is not None
    return bool(menu.winfo_ismapped())


# ---------------------------------------------------------------------------
#  Entry tooltips
# ---------------------------------------------------------------------------

def add_tipped_command(menu, label: str, command, tip: str) -> None:
    """`add_command` plus a tip; needs `enable_tips(menu)` first."""
    menu.add_command(label=label, command=command)
    menu.entry_tips[menu.index("end")] = tip


def enable_tips(menu) -> None:
    """Show each tipped entry's tip beside the menu while it is highlighted."""
    menu.entry_tips = {}
    state = {"win": None, "after": None}

    def _hide():
        """Cancel a pending tip and close a showing one."""
        if state["after"] is not None:
            menu.after_cancel(state["after"])
            state["after"] = None
        if state["win"] is not None:
            state["win"].destroy()
            state["win"] = None

    def _watch(index):
        """Close the tip once its entry is no longer highlighted or open."""
        if state["win"] is None:
            return
        if not _menu_open(menu) or menu.index("active") != index:
            _hide()
            return
        menu.after(WATCH_MS, lambda: _watch(index))

    def _show(index):
        """Put the tip up to the right of the menu, level with the pointer."""
        state["after"] = None
        if menu.index("active") != index or not _menu_open(menu):
            return
        rect = native_menu_rect()
        at = (rect[2] + 6, menu.winfo_pointery() - 10) if rect else None
        state["win"] = tooltip_window(menu, menu.entry_tips[index], TIP_WIDTH,
                                      at)
        _watch(index)

    def _select(_event):
        """A new entry is highlighted: restart the tip for it."""
        _hide()
        index = menu.index("active")
        if index is not None and index in menu.entry_tips:
            state["after"] = menu.after(TIP_DELAY_MS, lambda: _show(index))

    menu.bind("<<MenuSelect>>", _select, add="+")


# ---------------------------------------------------------------------------
#  Hover between dropdowns
# ---------------------------------------------------------------------------

def button_at(bar, x: int, y: int):
    """The menubutton of `bar` ([(menubutton, menu)]) under screen (x, y)."""
    for button, _menu in bar:
        left, top = button.winfo_rootx(), button.winfo_rooty()
        if (left <= x < left + button.winfo_width()
                and top <= y < top + button.winfo_height()):
            return button
    return None


def _watch_open_dropdown(root, bar, posted) -> None:
    """While `posted`'s dropdown is open, move to whichever one is hovered."""
    state = {"waited": 0, "seen": False}

    def _tick():
        """Follow the pointer; stop once the dropdown has closed."""
        if native_menu_rect() is None:
            state["waited"] += 1
            if state["seen"] or state["waited"] > APPEAR_TICKS:
                return
            root.after(WATCH_MS, _tick)
            return
        state["seen"] = True
        x, y = root.winfo_pointerxy()
        over = button_at(bar, x, y)
        if over is not None and over is not posted:
            ctypes.windll.user32.EndMenu()
            root.after(1, lambda: root.tk.call("tk::MbPost", str(over), x, y))
            return
        root.after(WATCH_MS, _tick)

    root.after(WATCH_MS, _tick)


def enable_hover_switch(root, bar) -> None:
    """Once any dropdown of `bar` is open, hovering another bar entry opens it.

    Windows only: elsewhere Tk menus are Tk windows and its own menubutton
    bindings already move between them.
    """
    if sys.platform != "win32":
        return
    for button, menu in bar:
        menu.configure(
            postcommand=lambda b=button: _watch_open_dropdown(root, bar, b))
