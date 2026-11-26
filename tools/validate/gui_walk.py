"""Drive the TESRACT window like a user: click every screen, dialog and control by its label, and record each step.

usage: python -m tools.validate.gui_walk [--exe PATH] [--out DIR] [--list] [--only NAME ...] [--runs]

Runs on a hidden desktop of its own (the user never sees a window), where it starts `python gui.py` (or the
built TESRACT.exe), finds labels through UI Automation and clicks them with window messages, captures the
window after every step to `<out>/<nn>_<step>.png`, and fails a step whose expected labels do not appear,
that shows an error, or after which the app has exited. The report is printed here when the walk ends, and
the app is closed. `--list` prints every label of every screen instead. `--runs` also starts real
conversions from the window (they write output). The settings file is restored afterwards.
See: docs/commentary/core_gui.md#ui-walk
"""

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from _ctypes import COMError
from ctypes import wintypes
from pathlib import Path

from PIL import Image
from pywinauto import Desktop

from core.gui import install_mods as mods
from output_layout import configured_output
from tools.live import hidden_launch

REPO = Path(__file__).resolve().parents[2]
TITLE = "T.E.SR.A.C.T"
#: A label only the finished main window has (the splash has none).
MAIN_LABEL = "Install & play"
#: The hidden desktop the walk and the app run on.
DESKTOP = "tesract_gui_walk"
#: Held by the walk running now; another waits for it (pid inside, so a dead holder is noticed).
WALK_LOCK = REPO / "temp" / "gui_walk.lock"
#: GetExitCodeProcess for a process still running.
STILL_ACTIVE = 259
#: Seconds the whole walk may take before it is stopped.
WALK_LIMIT = 900
#: Labels that mean something went wrong, wherever they appear.
ERROR_LABELS = ("Something went wrong", "Traceback", "WinError", "invalid handle")
#: Seconds a step waits for its expected labels.
STEP_WAIT = 8.0
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEWHEEL = 0x200, 0x201, 0x202, 0x20A
USER, GDI, K32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32


# ---------------------------------------------------------------------------
#  The window
# ---------------------------------------------------------------------------

class Window:
    """The running app: its process, its window and what UI Automation sees in it."""

    def __init__(self, cmd: list, errors: Path):
        """Start `cmd` (as itself: no windowless relaunch, its error output to `errors`) and wait for its window."""
        self.errors, self.errors_read = errors, 0
        with open(errors, "wb") as sink:
            self.proc = subprocess.Popen(cmd, cwd=str(REPO), env={**os.environ, "_TES_GUI_RELAUNCHED": "1"},
                                         stdout=sink, stderr=subprocess.STDOUT)
        self.started = time.perf_counter()
        self.win = None
        for _ in range(300):
            wins = [w for w in Desktop(backend="uia").windows() if w.window_text().startswith(TITLE)]
            if wins and any(el.window_text() == MAIN_LABEL for el in wins[0].descendants()):
                self.win = wins[0]
                break
            time.sleep(0.1)
        if self.win is None:
            self.proc.kill()
            raise RuntimeError("no TESRACT window within 30 s")
        self.shown_after = time.perf_counter() - self.started
        self.hwnd = self.win.handle

    def labels(self) -> list:
        """[(text, (x, y) client center)] for every visible label (one that vanishes while being read is skipped;
        the tree is read again when one vanishes while it is listed, as a running log's rows do)."""
        for _try in range(5):
            try:
                found = self.win.descendants()
                break
            except (COMError, KeyError):
                time.sleep(0.1)
        else:
            return []
        out = []
        for el in found:
            try:
                text, r = el.window_text(), el.rectangle()
            except COMError:
                continue
            if not text or r.width() <= 0 or r.height() <= 0:
                continue
            pt = wintypes.POINT((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            USER.ScreenToClient(self.hwnd, ctypes.byref(pt))
            if pt.y >= 0:
                out.append((text, (pt.x, pt.y)))
        return out

    def box(self, name: str, nth: int = 0):
        """(ticked, (x, y) client center) of the `nth` tick box named `name`, or None."""
        found = []
        for el in self.win.descendants(control_type="CheckBox"):
            try:
                if el.window_text() != name:
                    continue
                ticked = any(c.window_text() == "✓" for c in el.descendants())
                r = el.rectangle()
            except COMError:
                continue
            pt = wintypes.POINT((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            USER.ScreenToClient(self.hwnd, ctypes.byref(pt))
            found.append((ticked, (pt.x, pt.y)))
        return found[nth] if len(found) > nth else None

    def find(self, label: str, nth: int = 0, exact: bool = True):
        """The client center of the `nth` label equal to (or, not `exact`, containing) `label`, or None."""
        hits = [pos for text, pos in self.labels() if (text == label if exact else label in text)]
        return hits[nth] if len(hits) > nth else None

    def post(self, msg: int, wparam: int, x: int, y: int) -> None:
        """Post one mouse message at client (x, y)."""
        USER.PostMessageW(self.hwnd, msg, wparam, (y << 16) | (x & 0xFFFF))

    def click_at(self, x: int, y: int) -> None:
        """A left click at client (x, y)."""
        self.post(WM_MOUSEMOVE, 0, x, y)
        time.sleep(0.03)
        self.post(WM_LBUTTONDOWN, 1, x, y)
        time.sleep(0.03)
        self.post(WM_LBUTTONUP, 0, x, y)

    def click(self, label: str, nth: int = 0, exact: bool = True) -> bool:
        """Click a label; False when it is not on screen."""
        pos = self.find(label, nth, exact)
        if pos is None:
            return False
        self.click_at(*pos)
        return True

    def wheel(self, x: int, y: int, notches: int) -> None:
        """Turn the wheel at client (x, y): positive scrolls up."""
        pt = wintypes.POINT(x, y)
        USER.ClientToScreen(self.hwnd, ctypes.byref(pt))
        for _ in range(abs(notches)):
            delta = (120 if notches > 0 else -120) & 0xFFFF
            USER.PostMessageW(self.hwnd, WM_MOUSEWHEEL, delta << 16, (pt.y << 16) | (pt.x & 0xFFFF))
            time.sleep(0.03)

    def capture(self, path: Path) -> None:
        """Save the window's client area (PrintWindow: nothing needs to be in front)."""
        rect = wintypes.RECT()
        USER.GetClientRect(self.hwnd, ctypes.byref(rect))
        w, h = rect.right, rect.bottom
        hdc = USER.GetDC(self.hwnd)
        mem = GDI.CreateCompatibleDC(hdc)
        bmp = GDI.CreateCompatibleBitmap(hdc, w, h)
        GDI.SelectObject(mem, bmp)
        USER.PrintWindow(self.hwnd, mem, 3)
        buf = ctypes.create_string_buffer(w * h * 4)
        hdr = (ctypes.c_uint32 * 10)(40, w, -h, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
        GDI.GetDIBits(mem, bmp, 0, h, buf, hdr, 0)
        Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(path)
        GDI.DeleteObject(bmp)
        GDI.DeleteDC(mem)
        USER.ReleaseDC(self.hwnd, hdc)

    def new_errors(self) -> list:
        """The last line of each traceback the app has printed since the last call."""
        text = self.errors.read_bytes()[self.errors_read:].decode("utf-8", "replace")
        self.errors_read += len(text.encode("utf-8"))
        lines = text.splitlines()
        return [lines[i + 1 + next((k for k, line in enumerate(lines[i + 1:]) if not line.startswith(" ")), 0)]
                for i, line in enumerate(lines) if line.startswith("Traceback")]

    def alive(self) -> bool:
        """Whether the window still exists."""
        return bool(USER.IsWindow(self.hwnd))

    def close(self) -> None:
        """Close the window the way its close button does, then make sure the app's process is gone."""
        pid = wintypes.DWORD()
        USER.GetWindowThreadProcessId(self.hwnd, ctypes.byref(pid))
        if self.alive():
            USER.PostMessageW(self.hwnd, 0x0010, 0, 0)
        for _ in range(100):
            if not self.alive():
                break
            time.sleep(0.1)
        for left in {pid.value, self.proc.pid} - {0}:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(left)], capture_output=True)


# ---------------------------------------------------------------------------
#  Listing
# ---------------------------------------------------------------------------

#: The rail's five steps, by their labels.
SCREENS = ("Setup", "Choose", "Convert", "Shared mods", "Install & play")


def print_labels(win: Window, heading: str, before=frozenset()) -> None:
    """Print every label on screen now that was not in `before`."""
    print(f"== {heading}")
    for text, (x, y) in win.labels():
        if (text, (x, y)) not in before:
            print(f"   {x:5} {y:5}  {text}")


def list_labels(win: Window, clicks: list) -> None:
    """Print every label of every screen, or of the screen reached by clicking `clicks` in order."""
    if clicks:
        before = frozenset()
        for label in clicks:
            before = frozenset(win.labels())
            name, _, nth = label.partition("#")
            print(f"click {label!r}: {'ok' if win.click(name, int(nth or 0)) else 'NOT FOUND'}")
            time.sleep(1.0)
        print_labels(win, " > ".join(clicks) + " (new labels)", before)
        return
    for screen in SCREENS:
        win.click(screen)
        time.sleep(1.0)
        print_labels(win, screen)


# ---------------------------------------------------------------------------
#  Walking
# ---------------------------------------------------------------------------

def wait_for(win: Window, expect: tuple) -> list:
    """Wait until every expected label shows and every `!label` is gone (or STEP_WAIT passes); returns the misses."""
    deadline = time.monotonic() + STEP_WAIT
    while True:
        texts = [text for text, _pos in win.labels()]
        missing = [e for e in expect if (any(e[1:] in t for t in texts) if e.startswith("!")
                                         else not any(e in t for t in texts))]
        if not missing or time.monotonic() > deadline:
            return missing
        time.sleep(0.2)


def errors_shown(win: Window) -> list:
    """Error labels on screen."""
    return [text for text, _pos in win.labels() if any(e.lower() in text.lower() for e in ERROR_LABELS)]


def run_step(win: Window, out: Path, index: int, step: tuple) -> bool:
    """Do one (name, action, expected labels) step, capture the window, and print its verdict."""
    name, action, expect = step
    problems = []
    if action(win) is False:
        problems.append("its action failed (a control missing, or a check false)")
    missing = wait_for(win, expect) if win.alive() else list(expect)
    if missing:
        problems.append(f"missing {missing}")
    if not win.alive():
        problems.append("the app exited")
    else:
        problems += [f"error shown: {e!r}" for e in errors_shown(win)]
        problems += [f"app error: {e}" for e in win.new_errors()]
        win.capture(out / f"{index:02}_{name.replace(' ', '_')}.png")
    print(f"{'FAIL' if problems else 'ok  '} {index:02} {name}" + (f"  -- {'; '.join(problems)}" if problems else ""),
          flush=True)
    return not problems


def walk(win: Window, out: Path, only: list, runs: bool) -> int:
    """Every step in order (or those matching `only`); 0 when all passed."""
    for old in out.glob("*.png"):
        old.unlink()
    steps = STEPS + (RUN_STEPS if runs else [])
    chosen = [s for s in steps if not only or any(o.lower() in s[0].lower() for o in only)]
    failed = [s[0] for i, s in enumerate(chosen, 1) if win.alive() and not run_step(win, out, i, s)]
    print(f"{len(chosen) - len(failed)}/{len(chosen)} steps passed; pictures in {out}")
    return 1 if failed or not win.alive() else 0


# ---------------------------------------------------------------------------
#  Actions
# ---------------------------------------------------------------------------

def tap(label: str, nth: int = 0, exact: bool = True):
    """An action clicking one label."""
    return lambda win: win.click(label, nth, exact)


def taps(*labels):
    """An action clicking labels in turn (`Label#n` picks the nth match)."""
    def _do(win):
        """Click each, pausing between; False if one is missing."""
        for label in labels:
            name, _, nth = label.partition("#")
            if not win.click(name, int(nth or 0)):
                return False
            time.sleep(0.6)
        return True
    return _do


def owned_window(win: Window, title: str):
    """A top-level window titled `title` that belongs to the app's process, or 0."""
    pid = wintypes.DWORD()
    USER.GetWindowThreadProcessId(win.hwnd, ctypes.byref(pid))
    for w in Desktop(backend="win32").windows(process=pid.value):
        if w.window_text() == title:
            return w.handle
    return 0


def picker(label: str, title: str):
    """An action clicking `label`, waiting for the file or folder picker `title`, then cancelling it."""
    def _do(win):
        """True when the picker opened and closed."""
        if not win.click(label):
            return False
        for _ in range(50):
            hwnd = owned_window(win, title)
            if hwnd:
                USER.PostMessageW(hwnd, 0x0010, 0, 0)
                time.sleep(0.8)
                return True
            time.sleep(0.1)
        return False
    return _do


def search(text: str):
    """An action setting the plugin search to `text` (through UI Automation, as typing would)."""
    def _do(win):
        """Set the search field's text."""
        edits = win.win.descendants(control_type="Edit")
        if not edits:
            return False
        edits[0].iface_value.SetValue(text)
        return True
    return _do


def toggle(name: str, nth: int = 0, by_text: bool = False):
    """An action clicking the tick box `name` (or, `by_text`, the label on its row); True once it flipped."""
    def _do(win):
        """Click, then wait for the box to show the other state."""
        before = win.box(name, nth)
        if before is None:
            return False
        ticked, (x, y) = before
        rows = [pos for text, pos in win.labels() if text == name and abs(pos[1] - y) < 12]
        win.click_at(*(rows[0] if by_text and rows else (x, y)))
        deadline = time.monotonic() + STEP_WAIT
        while time.monotonic() < deadline:
            now = win.box(name, nth)
            if now is not None and now[0] != ticked:
                return True
            time.sleep(0.2)
        return False
    return _do


def ticked_count(win: Window):
    """The number on the "Ticked" filter chip, or None."""
    chip = next((text for text, _pos in win.labels() if text.startswith("Ticked ·")), None)
    return int(chip.rsplit(" ", 1)[1]) if chip else None


def toggle_counted(plugin: str):
    """An action ticking or unticking `plugin` by its name; True once the Ticked count moved by one with it."""
    def _do(win):
        """Toggle, then wait for the chip to follow."""
        before, box = ticked_count(win), win.box(plugin)
        if before is None or box is None or not toggle(plugin, by_text=True)(win):
            return False
        want = before - 1 if box[0] else before + 1
        deadline = time.monotonic() + STEP_WAIT
        while ticked_count(win) != want and time.monotonic() < deadline:
            time.sleep(0.2)
        return ticked_count(win) == want
    return _do


#: Stand-ins for the mod managers, beside the output so hardlinks work: never a real manager or its settings.
FAKES = REPO / "temp" / "gui_walk_fakes"
#: The folder standing in for Vortex's staging.
INSTALL_TARGET = FAKES / "vortex_staging"
#: A portable MO2 instance of our own (its ModOrganizer.exe is an empty file that is never started).
FAKE_MO2 = FAKES / "mo2"
#: Install rows on screen before the list scrolls (its box is 380 px of 54 px rows).
VISIBLE_ROWS = 6
#: The app's %APPDATA% during the walk, so Vortex's real state is never read and never claims our staging.
FAKE_APPDATA = FAKES / "appdata"


def make_fakes() -> dict:
    """Create the stand-in folders; returns the settings pointing the app at them."""
    shutil.rmtree(FAKES, ignore_errors=True)
    for folder in (INSTALL_TARGET, FAKE_MO2 / "profiles" / "Default", FAKE_APPDATA):
        folder.mkdir(parents=True, exist_ok=True)
    (FAKE_MO2 / "ModOrganizer.exe").write_bytes(b"")
    (FAKE_MO2 / "ModOrganizer.ini").write_text(
        "[General]\ngameName=Skyrim Special Edition\nselected_profile=@ByteArray(Default)\n", encoding="utf-8")
    (FAKE_MO2 / "profiles" / "Default" / "modlist.txt").write_text("+SkyUI\n", encoding="utf-8")
    return {"vortex.staging": str(INSTALL_TARGET), "mo2Path": str(FAKE_MO2)}


def walk_output() -> Path:
    """The output folder the app under walk uses."""
    saved = json.loads((REPO / "conversion_config.json").read_text(encoding="utf-8")).get("outputDir", "")
    return configured_output(saved)


def walk_mods() -> list:
    """Two mods of the rows shown unscrolled to install: a load-order one if shown, then the fewest files."""
    shown = mods.list_mods(walk_output())[:VISIBLE_ROWS]
    return sorted(shown, key=lambda m: (m.name not in mods.ROLES, len(mods.files(m))))[:2]


def beside(win, label: str, anchor: str):
    """The client center of `label` on the same line as `anchor`, or None."""
    at = win.find(anchor)
    hits = [pos for text, pos in win.labels() if text == label and at and abs(pos[1] - at[1]) < 12]
    return hits[0] if hits else None


def switch_stays_put(win) -> bool:
    """Pick each way of installing in turn: the switch's segments and the buttons under it never move."""
    ways = ("Mod Organizer 2", "Vortex", "Manual install")
    seen = []
    for way in ways + ways[:1]:
        pos = beside(win, way, "Install with")
        if pos is None:
            return False
        win.click_at(*pos)
        time.sleep(1.0)
        places = {w: beside(win, w, "Install with") for w in ("Install with", *ways)}
        places.update({b: win.find(b) for b in ("Select all", "Select none")})
        print(f"     {way}: {places}")
        seen.append(places)
    return all(s == seen[0] and None not in s.values() for s in seen)


def installed_here(folder: Path) -> list:
    """The walk's mods installed in `folder` (a tesract.json in their folder)."""
    return [m.name for m in walk_mods() if (folder / m.name / "tesract.json").is_file()]


def one_uninstalled(folder: Path):
    """An action checking one walk mod was uninstalled from `folder` and the other is still there."""
    def _do(win):
        """Exactly one walk mod left (none when only one mod was walked), and every output file still there."""
        left = installed_here(folder)
        print(f"     still installed in {folder.name}: {left}")
        return len(left) == len(walk_mods()) - 1 and all(e[1].is_file() for m in walk_mods() for e in mods.files(m))
    return _do


def mo2_lists_only_the_rest(win) -> bool:
    """The fake MO2 profile lists the walk mods still installed, not the uninstalled one, and keeps the user's."""
    lines = (FAKE_MO2 / "profiles" / "Default" / "modlist.txt").read_text(encoding="utf-8").splitlines()
    left = installed_here(FAKE_MO2 / "mods")
    gone = [m.name for m in walk_mods() if m.name not in left]
    print(f"     modlist.txt: {lines}")
    return "+SkyUI" in lines and all(f"+{n}" in lines for n in left) and not any(f"+{n}" in lines for n in gone)


def wait_finished(win) -> bool:
    """Wait (RUN_LIMIT) for the Convert screen to say a run finished."""
    deadline = time.monotonic() + RUN_LIMIT
    while time.monotonic() < deadline and win.alive():
        if win.find("Finished in", exact=False):
            return True
        time.sleep(2)
    return False


def wait_task(win) -> bool:
    """Wait (RUN_LIMIT) for a job that runs beside the pages to say done, still on Install & play."""
    deadline = time.monotonic() + RUN_LIMIT
    while time.monotonic() < deadline and win.alive():
        if win.find(": done", exact=False):
            return bool(win.find("Install with")) and not win.find("Next: shared mods")
        time.sleep(0.2)
    return False


def confirm_then_finish(yes: str, beside: str):
    """An action pressing the dialog button `yes` (the one level with `beside`), then waiting for the job to finish."""
    def _do(win):
        """True once the job is done and the window is still on Install & play."""
        anchor = win.find(beside)
        hits = [pos for text, pos in win.labels() if text == yes and anchor and abs(pos[1] - anchor[1]) < 12]
        if not hits:
            return False
        win.click_at(*hits[0])
        return wait_task(win)
    return _do


def run_to_the_end(go_label: str = "", nth: int = 0):
    """An action pressing the `nth` go button (none when `go_label` is empty: the run already started), then
    waiting for the run to finish (RUN_LIMIT)."""
    def _do(win):
        """True once the Convert screen says the run finished."""
        if go_label and not win.click(go_label, nth):
            return False
        return wait_finished(win)
    return _do


def run_started(go_label: str):
    """An action pressing the go button and waiting until a lane runs a sub-step, then a moment more, so the
    picture shows the running bar, its sub-step and the queued lanes."""
    def _do(win):
        """True once a lane says 'Running · <sub-step>'."""
        if not win.click(go_label):
            return False
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and win.alive():
            if win.find("Running · ", exact=False) and win.find("Queued", exact=False):
                time.sleep(1.5)
                return True
            time.sleep(0.25)
        return False
    return _do


def install_ticked_mods(win) -> bool:
    """Tick only the walk's mods (after Select none), install them and wait for the job, which stays on this page."""
    chosen = walk_mods()
    for mod in chosen:
        box = win.box("Include " + mod.name)
        if box is None or (not box[0] and not toggle("Include " + mod.name)(win)):
            return False
    return win.click(f"Install {len(chosen)}") and wait_task(win)


def installed_files_are_hardlinks(win) -> bool:
    """Each installed mod lists its files; a folder mod's are the same file (same index) as the output's."""
    good = True
    if installed_here(INSTALL_TARGET) != [m.name for m in walk_mods()]:
        return False
    for mod in walk_mods():
        dest = INSTALL_TARGET / mod.name
        marker = json.loads((dest / "tesract.json").read_text(encoding="utf-8"))
        wanted = {e[0]: e[1] for e in mods.files(mod)}
        listed = sorted(wanted) == sorted(marker["files"]) and all((dest / r).is_file() for r in wanted)
        linked = all(os.stat(src).st_ino == os.stat(dest / rel).st_ino and
                     os.path.samefile(src, dest / rel) for rel, src in wanted.items())
        print(f"     {mod.name}: {len(wanted)} files, mode {marker['mode']}, listed {listed}, hardlinked {linked}")
        good = good and listed and linked and marker["mode"] == "link"
    return good


#: (name, action, labels expected afterwards; `!label` must be gone) for every screen, control and dialog.
STEPS = [
    ("start", lambda win: None, ("Setup", "Choose", "Convert", "Shared mods", "Install & play")),
    ("setup open", tap("Setup"), ("Checked at every start.", "SKYRIM", "CONVERTING", "MENUS IN GAME")),
    ("setup check again", tap("Check again"), ("Skyrim Special Edition", "Bundled tools")),
    ("setup versions", tap("Supported versions"), ("Supported Skyrim versions",)),
    ("setup versions close", tap("Done"), ("!Supported Skyrim versions",)),
    ("setup skyrim folder picker", picker("Change", "Browse For Folder"), ("SKYRIM",)),
    ("setup look skyrim", tap("Skyrim", 0), ()),
    ("setup look morrowind", tap("Morrowind", 0), ()),
    ("setup icons oblivion", tap("Oblivion", 0), ()),
    ("setup icons morrowind", tap("Morrowind", 1), ()),
    ("setup continue", tap("Continue"), ("PLUGIN", "STATUS")),
    ("choose open", tap("Choose"), ("PLUGIN", "FROM", "STATUS")),
    ("choose card oblivion", tap("Oblivion"), ("Steps and options",)),
    ("choose card new vegas", tap("Fallout New Vegas"), ("Steps and options",)),
    ("choose card nehrim", tap("Nehrim At Fate's Edge"), ("Steps and options",)),
    ("choose arrow right", tap("›"), ("Arktwend",)),
    ("choose card fallout 3", tap("Fallout 3 goty"), ("Fallout3.esm",)),
    ("choose card arktwend", tap("Arktwend"), ("Arktwend_English.esm",)),
    ("choose arrow right to the end", taps("›", "›"), ("Add a game",)),
    ("choose add game picker", picker("Add a game", "Browse For Folder"), ("PLUGIN",)),
    ("choose arrow left", taps("‹", "‹", "‹", "‹"), ("Oblivion",)),
    ("choose card morrowind", tap("Morrowind"), ("Steps and options",)),
    ("choose filter converted", tap("Converted ·", exact=False), ("Converted",)),
    ("choose filter out of date", tap("Out of date ·", exact=False), ()),
    ("choose filter not converted", tap("Not converted ·", exact=False), ("Not converted",)),
    ("choose filter archives", tap("From archives ·", exact=False), ()),
    ("choose filter ticked", tap("Ticked ·", exact=False), ()),
    ("choose filter all", tap("All ·", exact=False), ("AreaEffectArrows.esp",)),
    ("choose search", search("bloodmoon"), ("Bloodmoon.esm", "!AreaEffectArrows.esp")),
    ("choose search cleared", search(""), ("AreaEffectArrows.esp",)),
    ("choose row name unticks", toggle_counted("Bloodmoon.esm"), ()),
    ("choose row name ticks", toggle_counted("Bloodmoon.esm"), ()),
    ("choose mode dialog", tap("Change"), ("Morrowind mode", "Switch mode")),
    ("choose mode cancel", tap("Cancel"), ("!Morrowind mode",)),
    ("choose remove game asks", tap("Remove from list"), ("Remove Morrowind", "Keep")),
    ("choose remove game kept", tap("Keep"), ("!Remove Morrowind", "Data Files")),
    ("choose clear asks", tap("Clear"), ("Keep",)),
    ("choose clear kept", tap("Keep"), ("!Keep",)),
    ("choose steps dialog", tap("Steps and options"), ("Automatic unless changed here.", "1. Export", ". Pack BSAs", "!Pack Mod Zip")),
    ("choose steps tab meshes", tap("Meshes"), ("Parallax", "Collision fix")),
    ("choose steps tab navmesh", tap("Navmesh"), ("Generator", "Shared cache")),
    ("choose steps tab performance", tap("Performance"), ("Workers", "Run logs kept")),
    ("choose steps tab steps", tap("Steps"), ("1. Export",)),
    ("choose steps done", tap("Done"), ("!Automatic unless changed here.",)),
    ("choose manage mods", tap("Manage added mods"), ("Added mods", "Tamriel Data (HD)")),
    ("choose manage mods cancel", tap("Cancel"), ("!Added mods",)),
    ("choose add archive picker", picker("Add mod archive", "Add a mod archive"), ("PLUGIN",)),
    ("choose add folder picker", picker("Add mod folder", "Browse For Folder"), ("PLUGIN",)),
    ("convert open", tap("Convert"), ("Log", "Full log")),
    ("convert clear log", tap("Clear"), ()),
    ("convert back to choose", taps("Convert", "Choose#1"), ("PLUGIN",)),
    ("load order open", tap("Shared mods"), ("Built once and used by every converted game.", "Distant LOD")),
    ("load order body plugins", tap("Plugins"), ("Body slot patch", "Skyrim.esm")),
    ("load order body cancel", tap("Cancel"), ("!Your active Skyrim plugins are ticked.",)),
    ("load order masters", tap("Choose", 1), ("Masters", "Apply")),
    ("load order masters cancel", tap("Cancel"), ("!Ticking a plugin ticks the masters",)),
    ("load order lod worlds", lambda win: None, ("Worldspaces to build", "WrldMorrowind")),
    ("load order lod world off", toggle("WrldMorrowind", by_text=True), ()),
    ("load order lod world on", toggle("WrldMorrowind"), ()),
    ("load order lod worlds none", tap("None"), ()),
    ("load order lod worlds all", tap("All"), ()),
    ("load order lod dialog", tap("Order & detail"), ("Lower in the list wins", "Worldspaces without distant land")),
    ("load order lod move down", tap("↓"), ()),
    ("load order lod move up", tap("↑", 1), ()),
    ("load order lod plugin off", toggle("Morrowind.esm", by_text=True), ()),
    ("load order lod plugin on", toggle("Morrowind.esm", by_text=True), ()),
    ("load order lod force on", toggle("Worldspaces without distant land"), ()),
    ("load order lod force off", toggle("Worldspaces without distant land"), ()),
    ("load order lod plugins none", tap("None", 1), ()),
    ("load order lod plugins all", tap("All", 1), ()),
    ("load order lod reset", tap("Reset"), ("Lower in the list wins",)),
    ("load order lod done", tap("Done"), ("!Lower in the list wins",)),
    ("load order include off", toggle("Include Body patch"), ()),
    ("load order include on", toggle("Include Body patch"), ()),
    ("install open", tap("Install & play"), ("Install with", "Select all")),
    ("install manual", tap("Manual install"), ("Zips of the ticked mods", "Open Finished Mods", "!Uninstall")),
    ("install vortex", tap("Vortex"), ("hardlinked, same drive", "Open staging folder")),
    ("install mo2", tap("Mod Organizer 2"), ('profile "Default"', "Open mods folder")),
    ("install switch stays put", switch_stays_put, ()),
    ("install vortex again", tap("Vortex"), ("Open staging folder",)),
    ("install select none", tap("Select none"), ("Nothing ticked",)),
    ("install select all", tap("Select all"), ()),
    ("install select none again", tap("Select none"), ("Nothing ticked",)),
    ("install tick the walk mods", install_ticked_mods, ("Install with", "!Next: shared mods", "!Finished in")),
    ("install done dialog", tap("OK"), ("!Start Vortex",)),
    ("install back", tap("Install & play"), ("Installed",)),
    ("install files are hardlinks", installed_files_are_hardlinks, ()),
    ("install row uninstall asks", tap("Uninstall"), ("from Vortex? Your output folder keeps", "Keep")),
    ("install row uninstall keep", tap("Keep"), ("!Your output folder keeps",)),
    ("install row uninstall", tap("Uninstall"), ("Your output folder keeps",)),
    ("install row uninstall confirm", confirm_then_finish("Uninstall", "Keep"), ("Install with", "!Next: shared mods", "!Finished in")),
    ("install back after row uninstall", tap("Install & play"), ("Install with",)),
    ("install row uninstall removed one", one_uninstalled(INSTALL_TARGET), ()),
    ("install mo2 again", tap("Mod Organizer 2"), ("Open mods folder",)),
    ("install mo2 select none", tap("Select none"), ("Nothing ticked",)),
    ("install mo2 tick the walk mods", install_ticked_mods, ("Install with", "!Next: shared mods", "!Finished in")),
    ("install mo2 back", tap("Install & play"), ("Installed",)),
    ("install mo2 row uninstall", tap("Uninstall"), ("from Mod Organizer 2? Your output folder keeps",)),
    ("install mo2 row uninstall confirm", confirm_then_finish("Uninstall", "Keep"), ("Install with", "!Next: shared mods", "!Finished in")),
    ("install mo2 back after row uninstall", tap("Install & play"), ("Install with",)),
    ("install mo2 row uninstall removed one", one_uninstalled(FAKE_MO2 / "mods"), ()),
    ("install mo2 profile lists the rest", mo2_lists_only_the_rest, ()),
    ("install navmesh editor", tap("Open"), ("Navmesh editor", "Stops when TESRACT closes.")),
    ("install navmesh server stop", tap("Stop"), ()),
    ("install navmesh server start", tap("Start"), ()),
    ("install navmesh close", tap("Close"), ("!Stops when TESRACT closes.",)),
    ("footer advanced", tap("Advanced"), ("Automatic unless changed here.",)),
    ("footer advanced step off", toggle("1. Export", by_text=True), ()),
    ("footer advanced steps all", tap("All"), ()),
    ("footer advanced steps automatic", tap("Back to automatic"), ("Automatic: only what",)),
    ("footer advanced meshes", tap("Meshes"), ("Parallax",)),
    ("footer advanced parallax on", tap("On"), ()),
    ("footer advanced textures only on", toggle("Textures only"), ()),
    ("footer advanced textures only off", toggle("Textures only"), ()),
    ("footer advanced parallax off", tap("Off"), ()),
    ("footer advanced winding on", tap("Always on"), ()),
    ("footer advanced winding automatic", tap("Automatic"), ()),
    ("footer advanced navmesh", tap("Navmesh"), ("Generator",)),
    ("footer advanced lattice", tap("Lattice"), ()),
    ("footer advanced corridor", tap("Corridor"), ()),
    ("footer advanced shared cache on", toggle("Shared cache"), ()),
    ("footer advanced shared cache off", toggle("Shared cache"), ()),
    ("footer advanced performance", tap("Performance"), ("Workers",)),
    ("footer advanced pack on", toggle("Pack by default"), ()),
    ("footer advanced pack off", toggle("Pack by default"), ()),
    ("footer advanced done", tap("Done"), ("!Automatic unless changed here.",)),
    ("footer about", tap("About"), ("Released under the MIT License",)),
    ("footer about check updates", tap("Check for updates"), ()),
    ("footer about close", tap("Close"), ("!Released under the MIT License",)),
    ("footer theme dark", tap("Theme"), ("dark",)),
    ("footer theme light", tap("Theme"), ("light",)),
    ("footer theme system", tap("Theme"), ("system",)),
    ("footer update available", tap("Update available"), ("is available",)),
]
def tick_only(plugin: str):
    """An action leaving `plugin` the only ticked plugin of the shown game."""
    def _do(win):
        """Untick every ticked plugin with the header box, then tick `plugin`."""
        search("")(win)
        if not win.click("Ticked ·", exact=False):
            return False
        time.sleep(0.8)
        header = win.box("All shown")
        if header is not None and header[0] and not toggle("All shown")(win):
            return False
        win.click("All ·", exact=False)
        search(plugin)(win)
        time.sleep(0.8)
        mine = win.box(plugin)
        return mine is not None and (mine[0] or toggle(plugin)(win))
    return _do


def clipboard_text() -> str:
    """The clipboard's text (the walk's desktop shares the app's clipboard), or ''."""
    USER.GetClipboardData.restype = ctypes.c_void_p
    K32.GlobalLock.restype, K32.GlobalLock.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    K32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not USER.OpenClipboard(None):
        return ""
    try:
        handle = USER.GetClipboardData(13)
        text = ctypes.wstring_at(K32.GlobalLock(handle)) if handle else ""
        if handle:
            K32.GlobalUnlock(handle)
        return text
    finally:
        USER.CloseClipboard()


def log_copies(needle: str):
    """An action pressing the log's Copy button and checking the clipboard then holds `needle`."""
    def _do(win):
        """True when the copied log holds `needle`."""
        if not win.click("Copy"):
            return False
        time.sleep(0.5)
        return needle in clipboard_text()
    return _do


def newest_run_log_clean(win) -> bool:
    """The newest run log ended OK and holds no failure, printing any it does."""
    log = max((REPO / "logs").glob("run-*.log"), key=lambda p: p.stat().st_mtime)
    text = log.read_text(encoding="utf-8", errors="replace")
    bad = [line for line in text.splitlines() if any(m in line for m in RUN_FAILURES)]
    for line in bad[:10]:
        print(f"     {log.name}: {line.strip()}")
    return not bad and "EXIT: OK" in text


#: Seconds a real run may take.
RUN_LIMIT = 1200
#: Run log text that means part of a run failed.
RUN_FAILURES = ("[FAIL]", "WinError", "Traceback", "EXIT: ERRORS", "FAILED for")
#: Steps that start real conversions (`--runs`): one plugin's Creatures and Sounds, as a user runs them.
RUN_STEPS = [
    ("run choose morrowind", taps("Choose", "Morrowind"), ("Steps and options",)),
    ("run tick only tribunal", tick_only("Tribunal.esm"), ()),
    ("run steps creatures and sounds", taps("Steps and options", "Steps", "None"), ("Automatic unless changed here.",)),
    ("run step creatures", toggle("5. Creatures"), ()),
    ("run step sounds", toggle("7. Sounds"), ()),
    ("run steps chosen", tap("Done"), ("Update 1",)),
    ("run convert running", run_started("Update 1"), ("Running · ", "Queued", "Copy")),
    ("run convert to the end", run_to_the_end(), ("Finished in",)),
    ("run log copies", log_copies("workers"), ()),
    ("run log has no failures", lambda win: newest_run_log_clean(win) or False, ()),
    ("run steps back to automatic", taps("Choose", "Steps and options", "Back to automatic", "Done"), ()),
]


# ---------------------------------------------------------------------------
#  Running
# ---------------------------------------------------------------------------

def walk_here(args) -> int:
    """On this desktop: start the app, walk it (or list its labels), close it, put the settings back."""
    config = REPO / "conversion_config.json"
    saved = config.read_bytes()
    settings = {**json.loads(saved), **make_fakes()}
    config.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.environ["APPDATA"] = str(FAKE_APPDATA)
    win = Window([args.exe] if args.exe else [sys.executable, "gui.py"], Path(args.out) / "app_output.txt")
    print(f"window after {win.shown_after:.2f}s", flush=True)
    try:
        if args.list:
            list_labels(win, args.click)
            return 0
        return walk(win, Path(args.out), args.only, args.runs)
    finally:
        win.close()
        config.write_bytes(saved)
        shutil.rmtree(FAKES, ignore_errors=True)


def _alive(pid: int) -> bool:
    """Whether process `pid` still runs."""
    handle = K32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = wintypes.DWORD(0)
    K32.GetExitCodeProcess(handle, ctypes.byref(code))
    K32.CloseHandle(handle)
    return code.value == STILL_ACTIVE


def _take_turn() -> None:
    """Wait until no other walk runs (they share the hidden desktop and conversion_config.json), then hold WALK_LOCK."""
    told = False
    while True:
        try:
            fd = os.open(WALK_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            try:
                holder = int(WALK_LOCK.read_text() or 0)
            except (OSError, ValueError):
                holder = 0
            if holder and not _alive(holder):
                WALK_LOCK.unlink(missing_ok=True)
                continue
            if not told:
                print(f"waiting for another walk (pid {holder}) to finish", flush=True)
                told = True
            time.sleep(2)


def walk_hidden(out: Path) -> int:
    """Run this tool again with --here on the hidden desktop, wait for it, and print its report."""
    _take_turn()
    try:
        return _walk_hidden(out)
    finally:
        WALK_LOCK.unlink(missing_ok=True)


def _walk_hidden(out: Path) -> int:
    """The hidden walk itself, once this process holds the walk lock."""
    report = out / "report.txt"
    report.unlink(missing_ok=True)
    argv = " ".join(f'"{a}"' for a in ["-m", "tools.validate.gui_walk", "--here"] + sys.argv[1:])
    pid = hidden_launch.launch(sys.executable, str(REPO), DESKTOP, [], {"PYTHONIOENCODING": "utf-8"}, argv)
    handle = K32.OpenProcess(0x00100000 | 0x1000, False, pid)
    finished = K32.WaitForSingleObject(handle, WALK_LIMIT * 1000) == 0
    code = wintypes.DWORD(1)
    K32.GetExitCodeProcess(handle, ctypes.byref(code))
    K32.CloseHandle(handle)
    if not finished:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    print(report.read_text(encoding="utf-8") if report.is_file() else "no report written")
    if not finished:
        print(f"stopped after {WALK_LIMIT} s")
    return code.value if finished else 1


def main() -> int:
    """Walk the window (or list its labels) on the hidden desktop; exit 1 when any step failed."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exe", help="the built TESRACT.exe (default: python gui.py)")
    ap.add_argument("--out", default=str(REPO / "temp" / "gui_walk"), help="where the report and pictures go")
    ap.add_argument("--list", action="store_true", help="print every label of every screen")
    ap.add_argument("--click", nargs="+", default=[], help="with --list: click these labels first, then list")
    ap.add_argument("--only", nargs="+", default=[], help="run only the steps whose names contain these")
    ap.add_argument("--runs", action="store_true", help="also start real conversions from the window")
    ap.add_argument("--here", action="store_true", help="walk on this desktop (what the hidden run does)")
    args = ap.parse_args()
    args.exe = str(Path(args.exe).resolve()) if args.exe else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not args.here:
        return walk_hidden(out)
    with open(out / "report.txt", "w", encoding="utf-8") as report:
        sys.stdout = report
        return walk_here(args)


if __name__ == "__main__":
    sys.exit(main())
