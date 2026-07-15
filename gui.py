"""
TES4-to-TES5 Conversion Tool — GUI

Usage:
  python gui.py          # open GUI
  python gui.py --cli    # headless CLI wrapper (see --help)
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import queue
import time
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "conversion_config.json"

# ── Pipeline steps ─────────────────────────────────────────────────────────
# (key, cli_flag, label, description, default_on, needs_file)
STEPS = [
    ("export",             "--export-only",        "1. Export",
     "Parse TES4 binary into a text cache",          True,  True),
    ("extract",            "--extract-only",       "2. Extract",
     "Pull assets from TES4 BSA archives",            True,  True),
    ("meshes",             "--meshes-only",        "3. Meshes",
     "Convert standard NIFs and copy textures",                 True,  True),
    ("speedtrees",         "--speedtrees-only",    "4. SpeedTrees",
     "Convert SPT files",           True,  True),
    ("creatures",          "--creatures-only",     "5. Creatures",
     "Convert creature models and animations",       True,  True),
    ("import_",            "--import-only",        "6. Import",
     "Build TES5 ESM/ESP from text cache",       True,  True),
    ("sounds",             "--sounds-only",        "7. Sounds",
     "Convert voice files to XWM and copy sounds",               True,  True),
    ("scripts",            "--scripts-only",       "8. Scripts",
     "Convert Oblivion scripts to Papyrus",      True,  True),
    ("lod",                "--lod-only",           "9. LOD",
     "Generate distant LOD",               False, True),
    ("modify_body_meshes", "--modify-body-meshes", "10. Patch Skyrim",
     "Build ARMA slot-44 patch for your load order",       True,  False),
    ("pack",               "--pack-only",          "11. Pack BSAs",
     "Pack assets into BSA archives",             False, True),
    ("pack_zip",           "--pack-zip-only",      "12. Pack Mod Zip",
     "Zip mod files for installation",   True,  True),
]

_DEFAULT_ON = {k for k, *_ in STEPS}

# ── Colours ───────────────────────────────────────────────────────────────────
CLR = {
    "bg":           "#1e1e2e",
    "panel":        "#2a2a3d",
    "border":       "#44475a",
    "accent":       "#7c6af7",
    "accent_hover": "#9a8cf8",
    "btn":          "#313244",
    "btn_hover":    "#45475a",
    "green":        "#a6e3a1",
    "red":          "#f38ba8",
    "yellow":       "#f9e2af",
    "blue":         "#89dceb",
    "text":         "#cdd6f4",
    "subtext":      "#6c7086",
    "log_bg":       "#141420",
    "log_fg":       "#cdd6f4",
    "log_info":     "#89b4fa",
    "log_ok":       "#a6e3a1",
    "log_err":      "#f38ba8",
    "log_warn":     "#f9e2af",
    "check_on":     "#7c6af7",
    "check_off":    "#44475a",
    "gold":         "#c9a35c",
    "gold_hover":   "#ddb96f",
}


def _style_titlebar(root) -> None:
    """Recolor the native Windows title bar to match the app's dark/purple theme."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())

        def _bgr(hexcolor: str) -> int:
            hexcolor = hexcolor.lstrip("#")
            r, g, b = (int(hexcolor[i:i + 2], 16) for i in (0, 2, 4))
            return r | (g << 8) | (b << 16)

        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36
        dwmapi = ctypes.windll.dwmapi
        caption = ctypes.c_int(_bgr(CLR["bg"]))
        text = ctypes.c_int(_bgr(CLR["text"]))
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(caption), ctypes.sizeof(caption))
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_TEXT_COLOR, ctypes.byref(text), ctypes.sizeof(text))
    except Exception:
        pass  # older Windows builds (<22H2) don't support these attributes


# ── Config helpers ────────────────────────────────────────────────────────────

def _find_game_path(game: str) -> str:
    """Auto-detect game data path from the Windows registry."""
    try:
        import winreg
        keys = {
            "oblivion": [
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\WOW6432Node\Bethesda Softworks\Oblivion"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Bethesda Softworks\Oblivion"),
            ],
            "skyrimse": [
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition"),
            ],
        }
        for hkey, subkey in keys.get(game, []):
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    path, _ = winreg.QueryValueEx(key, "Installed Path")
                    data = os.path.join(path, "Data")
                    if os.path.isdir(data):
                        return data
            except (FileNotFoundError, OSError):
                continue
    except ImportError:
        pass
    return ""


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def scan_plugins(data_path: str) -> list:
    """Return sorted list of .esm/.esp files in data_path."""
    if not data_path or not os.path.isdir(data_path):
        return []
    plugins = []
    for name in sorted(os.listdir(data_path)):
        if name.lower().endswith(('.esm', '.esp')):
            plugins.append(name)
    return plugins


# Base game + official Creation Club content, in Bethesda's own load-order
# priority (the order the game/CC installer expects them in, independent of
# whatever plugins.txt says) — these are always listed first, and default to
# checked whenever they're actually present in the Data folder.
_OFFICIAL_PLUGINS = [
    "Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm",
    "ccasvsse001-almsivi.esm", "ccbgssse001-fish.esm", "ccbgssse002-exoticarrows.esl",
    "ccbgssse003-zombies.esl", "ccbgssse004-ruinsedge.esl", "ccbgssse005-goldbrand.esl",
    "ccbgssse006-stendarshammer.esl", "ccbgssse007-chrysamere.esl",
    "ccbgssse010-petdwarvenarmoredmudcrab.esl", "ccbgssse011-hrsarmrelvn.esl",
    "ccbgssse012-hrsarmrstl.esl", "ccbgssse014-spellpack01.esl",
    "ccbgssse019-staffofsheogorath.esl", "ccbgssse020-graycowl.esl",
    "ccbgssse021-lordsmail.esl", "ccmtysse001-knightsofthenine.esl",
    "ccqdrsse001-survivalmode.esl", "cctwbsse001-puzzledungeon.esm",
    "cceejsse001-hstead.esm", "ccqdrsse002-firewood.esl", "ccbgssse018-shadowrend.esl",
    "ccbgssse035-petnhound.esl", "ccfsvsse001-backpacks.esl", "cceejsse002-tower.esl",
    "ccedhsse001-norjewel.esl", "ccvsvsse002-pets.esl", "ccbgssse037-curios.esl",
    "ccbgssse034-mntuni.esl", "ccbgssse045-hasedoki.esl", "ccbgssse008-wraithguard.esl",
    "ccbgssse036-petbwolf.esl", "ccffbsse001-imperialdragon.esl", "ccmtysse002-ve.esl",
    "ccbgssse043-crosselv.esl", "ccvsvsse001-winter.esl", "cceejsse003-hollow.esl",
    "ccbgssse016-umbra.esm", "ccbgssse031-advcyrus.esm", "ccbgssse038-bowofshadows.esl",
    "ccbgssse040-advobgobs.esl", "ccbgssse050-ba_daedric.esl", "ccbgssse052-ba_iron.esl",
    "ccbgssse054-ba_orcish.esl", "ccbgssse058-ba_steel.esl",
    "ccbgssse059-ba_dragonplate.esl", "ccbgssse061-ba_dwarven.esl",
    "ccpewsse002-armsofchaos.esl", "ccbgssse041-netchleather.esl",
    "ccedhsse002-splkntset.esl", "ccbgssse064-ba_elven.esl", "ccbgssse063-ba_ebony.esl",
    "ccbgssse062-ba_dwarvenmail.esl", "ccbgssse060-ba_dragonscale.esl",
    "ccbgssse056-ba_silver.esl", "ccbgssse055-ba_orcishscaled.esl",
    "ccbgssse053-ba_leather.esl", "ccbgssse051-ba_daedricmail.esl",
    "ccbgssse057-ba_stalhrim.esl", "ccbgssse066-staves.esl", "ccbgssse067-daedinv.esm",
    "ccbgssse068-bloodfall.esl", "ccbgssse069-contest.esl", "ccvsvsse003-necroarts.esl",
    "ccvsvsse004-beafarmer.esl", "ccbgssse025-advdsgs.esm", "ccffbsse002-crossbowpack.esl",
    "ccbgssse013-dawnfang.esl", "ccrmssse001-necrohouse.esl", "ccedhsse003-redguard.esl",
    "cceejsse004-hall.esl", "cceejsse005-cave.esm", "cckrtsse001_altar.esl",
    "cccbhsse001-gaunt.esl", "ccafdsse001-dwesanctuary.esm", "_ResourcePack.esl",
]


def scan_skyrim_plugins(data_path: str) -> list:
    """Return sorted list of .esm/.esp/.esl files in data_path."""
    if not data_path or not os.path.isdir(data_path):
        return []
    return sorted(name for name in os.listdir(data_path)
                 if name.lower().endswith(('.esm', '.esp', '.esl')))


def scan_skyrim_load_order(data_path: str) -> tuple:
    """Return (ordered_names, default_checked_set) for the Skyrim plugin picker.

    Order: base game + official Creation Club content first (in Bethesda's
    own priority order), then any other plugins.txt entries in load order,
    then any remaining installed-but-unlisted plugins last. Only the first
    two groups (official content and anything plugins.txt actually lists)
    default to checked — a plugin sitting in Data/ that neither list
    mentions is surfaced but starts unchecked.
    """
    installed = {name.lower(): name for name in scan_skyrim_plugins(data_path)}
    if not installed:
        return [], set()

    ordered = []
    seen = set()
    for name in _OFFICIAL_PLUGINS:
        found = installed.get(name.lower())
        if found and found not in seen:
            ordered.append(found)
            seen.add(found)
    default_checked = set(ordered)

    plugins_txt = (Path(os.environ.get("LOCALAPPDATA", ""))
                   / "Skyrim Special Edition" / "plugins.txt")
    if plugins_txt.exists():
        try:
            with open(plugins_txt, "r", encoding="utf-8-sig", errors="replace") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # A leading "*" means the plugin is active; without it,
                    # plugins.txt is just listing a disabled/known plugin.
                    active = line.startswith("*")
                    line = line.lstrip("*")
                    name = installed.get(line.lower())
                    if name and name not in seen:
                        ordered.append(name)
                        seen.add(name)
                        if active:
                            default_checked.add(name)
        except OSError:
            pass

    for name in installed.values():
        if name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered, default_checked


def scan_mesh_subdirs(file_name: str) -> list:
    """Return sorted list of root mesh subdirectories in export/<file_name>/meshes/."""
    if not file_name:
        return []
    mesh_dir = SCRIPT_DIR / "export" / file_name / "meshes"
    if not mesh_dir.is_dir():
        return []
    subdirs = sorted(
        d.name for d in mesh_dir.iterdir()
        if d.is_dir()
    )
    return subdirs


# ── Subprocess helper ─────────────────────────────────────────────────────────

# On Windows, hide the console window that subprocess.Popen would otherwise
# create when launched from a console-less process (pythonw / .pyw).
from subprocess_flags import POPEN_FLAGS as _POPEN_FLAGS, configure_multiprocessing

configure_multiprocessing()


def _kill_process_tree(proc):
    """Forcibly kill `proc` and every descendant it spawned.

    ``proc.terminate()`` only signals the direct child (convert.py); the
    conversion spawns grandchildren — multiprocessing Pool workers plus helper
    .exes (ffmpeg, hkxcmd, BSArch, LODGen). Those must be killed too or they
    keep running and hold the stdout pipe open, so cancellation appears to hang.

    On Windows, ``taskkill /T`` walks the whole tree by PID. Fall back to
    ``proc.kill()`` if taskkill is unavailable or errors.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                **_POPEN_FLAGS,
            )
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_process(cmd, log_cb, env=None, cancel_event=None):
    """Run cmd as subprocess, streaming output to `log_cb` as bytes arrive.

    A dedicated reader thread pulls bytes off the pipe so the control loop can
    poll `cancel_event` on a short interval — a blocking pipe read must never be
    what stands between the user clicking Cancel and the process dying.

    On cancellation the entire process tree is killed (see
    `_kill_process_tree`) and -2 is returned.
    """
    try:
        full_env = os.environ.copy()
        full_env["PYTHONUNBUFFERED"] = "1"
        if env:
            full_env.update(env)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,  # unbuffered binary mode
            cwd=str(SCRIPT_DIR),
            env=full_env,
            **_POPEN_FLAGS,
        )

        out = proc.stdout
        line_q: "queue.Queue" = queue.Queue()

        def _reader():
            """Read bytes off the pipe, split into lines, push onto line_q."""
            buf = bytearray()
            try:
                while True:
                    chunk = out.read(1024)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    while True:
                        nl = buf.find(b"\n")
                        if nl == -1:
                            break
                        line = bytes(buf[:nl + 1])
                        del buf[:nl + 1]
                        line_q.put(line.decode("utf-8", errors="replace")
                                   .rstrip("\r\n"))
            except (OSError, ValueError):
                pass
            finally:
                if buf:
                    text = bytes(buf).decode("utf-8", errors="replace").rstrip("\r\n")
                    if text:
                        line_q.put(text)
                line_q.put(None)  # sentinel: pipe closed

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        cancelled = False
        while True:
            # Cancel takes effect within one poll interval, even if the child
            # is silent or blocked deep inside a long-running step.
            if cancel_event is not None and cancel_event.is_set():
                _kill_process_tree(proc)
                cancelled = True
                break

            try:
                item = line_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:  # pipe closed — process finished on its own
                break
            log_cb(item)

        if cancelled:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return -2  # sentinel for cancelled

        proc.wait()
        return proc.returncode
    except Exception as exc:
        try:
            log_cb(f"ERROR: {exc}")
        except Exception:
            pass
        return -1


# ══════════════════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════════════════

def gui_main():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        print("ERROR: tkinter not available")
        return 1

    # ── Load / init config ────────────────────────────────────────────────────
    cfg = load_config()
    tes4_path    = cfg.get("tes4DataPath", "") or _find_game_path("oblivion")
    tes5_path    = cfg.get("tes5DataPath", "") or _find_game_path("skyrimse")
    output_path  = cfg.get("outputDir", "")  or str(SCRIPT_DIR / "output")

    # ── Root window ───────────────────────────────────────────────────────────
    root = tk.Tk()
    root.title("TES4Skyrim")
    root.geometry("1060x877")
    root.minsize(860, 680)
    root.configure(bg=CLR["bg"])
    root.option_add("*Background", CLR["bg"])
    root.option_add("*Foreground", CLR["text"])
    icon_path = SCRIPT_DIR / "docs" / "favicon.ico"
    root.iconbitmap(default=str(icon_path))
    _style_titlebar(root)

    style = ttk.Style(root)
    style.theme_use("clam")

    def S(*a, **kw):
        style.configure(*a, **kw)

    S(".",             background=CLR["bg"], foreground=CLR["text"],
                       troughcolor=CLR["panel"], borderwidth=0, relief="flat")
    S("TFrame",        background=CLR["bg"])
    S("Panel.TFrame",  background=CLR["panel"])

    S("TLabel",        background=CLR["bg"],    foreground=CLR["text"])
    S("Sub.TLabel",    background=CLR["bg"],    foreground=CLR["subtext"],
                       font="Segoe\\ UI 9")
    S("Panel.TLabel",  background=CLR["panel"], foreground=CLR["text"])
    S("PanelSub.TLabel", background=CLR["panel"], foreground=CLR["subtext"],
                       font="Segoe\\ UI 9")
    S("Head.TLabel",   background=CLR["panel"], foreground=CLR["accent"],
                       font=("Segoe UI", 15, "bold"))
    S("Entry.TLabel",  background=CLR["panel"], foreground=CLR["subtext"],
                       font="Segoe\\ UI 9")

    S("TEntry",        fieldbackground=CLR["btn"], foreground=CLR["text"],
                       insertcolor=CLR["text"], borderwidth=1, relief="flat")

    S("TCombobox",     fieldbackground=CLR["btn"], background=CLR["btn"],
                       foreground=CLR["text"], arrowcolor=CLR["text"],
                       selectbackground=CLR["accent"],
                       selectforeground=CLR["text"], borderwidth=1, relief="flat")
    style.map("TCombobox",
              fieldbackground=[("readonly", CLR["btn"])],
              foreground=[("readonly", CLR["text"])])

    S("TButton",       background=CLR["btn"], foreground=CLR["text"],
                       borderwidth=1, relief="flat", padding=(8, 4),
                       font="Segoe\\ UI 10")
    style.map("TButton",
              background=[("active", CLR["btn_hover"]), ("disabled", CLR["border"])],
              foreground=[("disabled", CLR["subtext"])])

    S("Accent.TButton", background=CLR["accent"], foreground="#ffffff",
                        borderwidth=0, relief="flat", padding=(14, 6),
                        font="Segoe\\ UI 10 bold")
    style.map("Accent.TButton",
              background=[("active", CLR["accent_hover"]),
                          ("disabled", CLR["btn"])],
              foreground=[("disabled", CLR["subtext"])])

    S("Run.TButton",   background=CLR["gold"], foreground="#1e1e2e",
                        borderwidth=0, relief="flat", padding=(14, 6),
                        font="Segoe\\ UI 10 bold")
    style.map("Run.TButton",
              background=[("active", CLR["gold_hover"]),
                          ("disabled", CLR["btn"])],
              foreground=[("disabled", CLR["subtext"])])

    S("Cancel.TButton", background="#453030", foreground=CLR["red"],
                        borderwidth=0, relief="flat", padding=(8, 6),
                        font="Segoe\\ UI 10")
    style.map("Cancel.TButton",
              background=[("active", "#5a3030"), ("disabled", CLR["border"])],
              foreground=[("disabled", CLR["subtext"])])

    S("Danger.TButton", background="#453030", foreground=CLR["red"],
                        borderwidth=0, relief="flat", padding=(8, 4))
    style.map("Danger.TButton", background=[("active", "#5a3030")])

    S("TSeparator",    background=CLR["border"])
    S("TScrollbar",    background=CLR["btn"], troughcolor=CLR["bg"],
                       borderwidth=0, arrowcolor=CLR["subtext"], relief="flat")
    style.map("TScrollbar", background=[("active", CLR["btn_hover"])])

    S("TCheckbutton",  background=CLR["panel"], foreground=CLR["text"],
                       indicatorcolor=CLR["check_off"],
                       indicatorrelief="flat", focuscolor="")
    style.map("TCheckbutton",
              indicatorcolor=[("selected", CLR["check_on"])],
              background=[("active", CLR["panel"])])

    S("TProgressbar",  troughcolor=CLR["panel"], background=CLR["accent"],
                       borderwidth=0, thickness=4)

    # ── State vars ────────────────────────────────────────────────────────────
    tes4_var    = tk.StringVar(value=tes4_path)
    tes5_var    = tk.StringVar(value=tes5_path)
    output_var  = tk.StringVar(value=output_path)
    file_var    = tk.StringVar()
    step_vars   = {key: tk.BooleanVar(value=(key in _DEFAULT_ON))
                   for key, *_ in STEPS}
    running     = threading.Event()
    cancel_evt  = threading.Event()  # set to request cancellation

    # mesh subfolder state: list of (name, BooleanVar)
    mesh_subdir_vars = []  # populated when "Meshes" step panel expands
    # Skyrim patch-plugin state: list of (name, BooleanVar), all-on by default
    patch_plugin_vars = []

    # ── Layout: sidebar + log pane ────────────────────────────────────────────
    outer = ttk.Frame(root)
    outer.pack(fill=tk.BOTH, expand=True)
    outer.columnconfigure(0, weight=0, minsize=330)
    outer.columnconfigure(1, weight=1)
    outer.rowconfigure(0, weight=1)

    sidebar  = ttk.Frame(outer, style="Panel.TFrame")
    sidebar.grid(row=0, column=0, sticky="nsew")

    log_pane = tk.Frame(outer, bg=CLR["log_bg"])
    log_pane.grid(row=0, column=1, sticky="nsew")

    # ── Sidebar helpers ───────────────────────────────────────────────────────
    def _sep():
        ttk.Separator(sidebar, orient=tk.HORIZONTAL).pack(
            fill=tk.X, padx=14, pady=6)

    def _section(text: str):
        f = ttk.Frame(sidebar, style="Panel.TFrame")
        f.pack(fill=tk.X, padx=14, pady=(6, 2))
        ttk.Label(f, text=text, style="PanelSub.TLabel").pack(anchor="w")
        return f

    def _path_row(parent, label_text: str, var: tk.StringVar,
                  browse_dir=True, on_change=None):
        """A labelled Entry + Browse button row."""
        ttk.Label(parent, text=label_text, style="PanelSub.TLabel").pack(
            anchor="w", pady=(4, 0))
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill=tk.X)
        row.columnconfigure(0, weight=1)
        entry = ttk.Entry(row, textvariable=var, width=26)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        def _browse():
            if browse_dir:
                path = filedialog.askdirectory(
                    initialdir=var.get() or str(SCRIPT_DIR),
                    title=f"Select {label_text}")
            else:
                path = filedialog.askopenfilename(
                    initialdir=var.get() or str(SCRIPT_DIR),
                    title=f"Select {label_text}")
            if path:
                var.set(path)
                if on_change:
                    on_change(path)

        ttk.Button(row, text="...", command=_browse, width=3).grid(
            row=0, column=1)
        return entry

    # ── Title ─────────────────────────────────────────────────────────────────
    tf = ttk.Frame(sidebar, style="Panel.TFrame")
    tf.pack(fill=tk.X, padx=14, pady=(16, 4))

    banner_img = None
    banner_path = SCRIPT_DIR / "docs" / "banner.png"
    if banner_path.exists():
        try:
            from PIL import Image, ImageTk
            src = Image.open(banner_path)
            target_w = 350
            # Scale to 1.5x the width-fit height, then crop left/right back
            # down to target_w so the logo reads larger within the same column.
            base_h = target_w * src.height / src.width
            scale_h = round(base_h * 1.5)
            scale_w = round(target_w * (scale_h / base_h))
            src = src.resize((scale_w, scale_h), Image.LANCZOS)
            left = (scale_w - target_w) // 2
            src = src.crop((left, 0, left + target_w, scale_h))
            banner_img = ImageTk.PhotoImage(src)
        except Exception:
            banner_img = None

    if banner_img is not None:
        banner_label = ttk.Label(tf, image=banner_img, style="Panel.TLabel")
        banner_label.image = banner_img  # keep a reference alive
        banner_label.pack(fill=tk.X)
    else:
        ttk.Label(tf, text="TES4  ->  TES5", style="Head.TLabel").pack(anchor="w")
        ttk.Label(tf, text="Oblivion to Skyrim SE converter",
                  style="PanelSub.TLabel").pack(anchor="w")

    _sep()

    # ── Oblivion data directory ───────────────────────────────────────────────
    dir_frame = ttk.Frame(sidebar, style="Panel.TFrame")
    dir_frame.pack(fill=tk.X, padx=14, pady=(0, 4))

    def _on_tes4_change(path):
        """Refresh plugin list when Oblivion data dir changes."""
        plugins = scan_plugins(path)
        file_combo["values"] = plugins
        if plugins:
            # Prefer Oblivion.esm if present
            preferred = None
            for p in plugins:
                if p.lower() == 'oblivion.esm':
                    preferred = p
                    break
            file_var.set(preferred if preferred else plugins[0])
        else:
            file_var.set("")
        _save_dir_to_config()

    _path_row(dir_frame, "Oblivion Data Directory", tes4_var,
              browse_dir=True, on_change=_on_tes4_change)

    # ── Plugin selector ───────────────────────────────────────────────────────
    pf = ttk.Frame(sidebar, style="Panel.TFrame")
    pf.pack(fill=tk.X, padx=14, pady=(6, 0))
    ttk.Label(pf, text="Plugin File", style="PanelSub.TLabel").pack(anchor="w")
    initial_plugins = scan_plugins(tes4_path)
    file_combo = ttk.Combobox(pf, textvariable=file_var,
                               values=initial_plugins, state="readonly", width=30)
    file_combo.pack(fill=tk.X, pady=(2, 0))
    if initial_plugins and not file_var.get():
        # Prefer Oblivion.esm if present, otherwise pick the first plugin
        preferred = None
        for p in initial_plugins:
            if p.lower() == 'oblivion.esm':
                preferred = p
                break
        file_var.set(preferred if preferred else initial_plugins[0])

    _sep()

    # ── Output directory ──────────────────────────────────────────────────────
    out_frame = ttk.Frame(sidebar, style="Panel.TFrame")
    out_frame.pack(fill=tk.X, padx=14, pady=(0, 4))

    def _on_output_change(path):
        _save_dir_to_config()

    _path_row(out_frame, "Output Directory", output_var,
              browse_dir=True, on_change=_on_output_change)

    def _save_dir_to_config(*_):
        updated = load_config()
        updated["tes4DataPath"] = tes4_var.get()
        updated["tes5DataPath"] = tes5_var.get()
        updated["outputDir"]    = output_var.get()
        save_config(updated)

    tes4_var.trace_add("write", lambda *_: None)  # live binding via on_change

    _sep()

    # ── Skyrim SE data directory (for the "Patch Skyrim" step) ───────────────
    tes5_frame = ttk.Frame(sidebar, style="Panel.TFrame")
    tes5_frame.pack(fill=tk.X, padx=14, pady=(0, 4))

    def _on_tes5_change(path):
        _refresh_patch_plugin_vars()
        _save_dir_to_config()

    _path_row(tes5_frame, "Skyrim SE Data Directory", tes5_var,
              browse_dir=True, on_change=_on_tes5_change)

    _sep()

    # ── Pipeline steps ────────────────────────────────────────────────────────
    sh = ttk.Frame(sidebar, style="Panel.TFrame")
    sh.pack(fill=tk.X, padx=14, pady=(0, 4))
    ttk.Label(sh, text="Pipeline Steps", style="PanelSub.TLabel").pack(side=tk.LEFT)

    def _set_all():
        for v in step_vars.values():
            v.set(True)
        _update_run_btn()

    def _set_default():
        for key, v in step_vars.items():
            v.set(key in _DEFAULT_ON)
        _update_run_btn()

    def _set_none():
        for v in step_vars.values():
            v.set(False)
        _update_run_btn()

    ttk.Button(sh, text="None", command=_set_none, width=5).pack(
        side=tk.RIGHT, padx=(2, 0))
    ttk.Button(sh, text="Default", command=_set_default, width=7).pack(
        side=tk.RIGHT, padx=(2, 0))
    ttk.Button(sh, text="All", command=_set_all, width=4).pack(
        side=tk.RIGHT, padx=(2, 0))

    def _update_run_btn(*_):
        has = any(v.get() for v in step_vars.values())
        st  = "normal" if has and not running.is_set() else "disabled"
        run_btn.configure(state=st)

    # ── Mesh subfolder modal overlay ──────────────────────────────────────────
    # A Frame placed over `outer` (fills the whole window) with a card centred
    # inside it.  No Toplevel — entirely within the existing window.

    def _open_mesh_subdir_panel():
        nonlocal mesh_subdir_vars

        fname   = file_var.get()
        subdirs = scan_mesh_subdirs(fname)

        old_vals = {name: v.get() for name, v in mesh_subdir_vars}
        mesh_subdir_vars.clear()
        for name in subdirs:
            mesh_subdir_vars.append((name, tk.BooleanVar(value=old_vals.get(name, True))))

        # Card placed directly over the window, no overlay behind it
        card = tk.Frame(outer, bg=CLR["panel"],
                        highlightbackground=CLR["border"], highlightthickness=1)

        def _close():
            card.destroy()

        # Title row
        title_row = tk.Frame(card, bg=CLR["panel"])
        title_row.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(title_row, text="Mesh subfolders to convert",
                 bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(title_row, text="All",
                   command=lambda: [v.set(True) for _, v in mesh_subdir_vars],
                   width=4).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(title_row, text="None",
                   command=lambda: [v.set(False) for _, v in mesh_subdir_vars],
                   width=5).pack(side=tk.RIGHT)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        if not subdirs:
            tk.Label(card, text="Run the Extract step first to populate this list.",
                     bg=CLR["panel"], fg=CLR["subtext"],
                     font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 8))
        else:
            for name, var in mesh_subdir_vars:
                ttk.Checkbutton(card, text=name, variable=var,
                                style="TCheckbutton").pack(anchor="w", padx=20, pady=1)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        ttk.Button(card, text="OK", style="Accent.TButton",
                   command=_close).pack(pady=(0, 14))

        # Centre the card over the window
        card.update_idletasks()
        card.place(in_=outer, anchor="center", relx=0.5, rely=0.5)
        card.lift()

    def _refresh_patch_plugin_vars():
        """(Re)populate patch_plugin_vars from the Skyrim load order,
        preserving any existing checkbox state. New entries default to
        checked only if they're official content or listed in plugins.txt;
        plugins found only by a raw directory scan default to unchecked."""
        nonlocal patch_plugin_vars
        names, default_checked = scan_skyrim_load_order(tes5_var.get())
        old_vals = {name: v.get() for name, v in patch_plugin_vars}
        patch_plugin_vars = [
            (name, tk.BooleanVar(value=old_vals.get(name, name in default_checked)))
            for name in names]

    _refresh_patch_plugin_vars()

    def _open_patch_plugin_panel():
        _refresh_patch_plugin_vars()

        card = tk.Frame(outer, bg=CLR["panel"],
                        highlightbackground=CLR["border"], highlightthickness=1)
        _wheel_bound = []  # [bind_id] once a canvas is created below

        def _close():
            if _wheel_bound:
                card.unbind_all("<MouseWheel>")
            card.destroy()

        title_row = tk.Frame(card, bg=CLR["panel"])
        title_row.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(title_row, text="Plugins to patch (slot 44 / body)",
                 bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(title_row, text="All",
                   command=lambda: [v.set(True) for _, v in patch_plugin_vars],
                   width=4).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(title_row, text="None",
                   command=lambda: [v.set(False) for _, v in patch_plugin_vars],
                   width=5).pack(side=tk.RIGHT)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        if not patch_plugin_vars:
            tk.Label(card,
                     text="No plugins found. Set the Skyrim SE Data Directory above.",
                     bg=CLR["panel"], fg=CLR["subtext"],
                     font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 8))
        else:
            list_frame = tk.Frame(card, bg=CLR["panel"])
            list_frame.pack(fill=tk.BOTH, expand=True, padx=8)

            canvas = tk.Canvas(list_frame, bg=CLR["panel"], highlightthickness=0,
                               width=320, height=min(360, 22 * len(patch_plugin_vars)))
            vsb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
            inner = tk.Frame(canvas, bg=CLR["panel"])
            inner.bind("<Configure>",
                      lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.configure(yscrollcommand=vsb.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)

            def _wheel(e):
                canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
            card.bind_all("<MouseWheel>", _wheel)
            _wheel_bound.append(True)

            for name, var in patch_plugin_vars:
                ttk.Checkbutton(inner, text=name, variable=var,
                                style="TCheckbutton").pack(anchor="w", padx=8, pady=1)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        ttk.Button(card, text="OK", style="Accent.TButton",
                   command=_close).pack(pady=(0, 14))

        card.update_idletasks()
        card.place(in_=outer, anchor="center", relx=0.5, rely=0.5)
        card.lift()

    # Build step checkboxes
    _mesh_step_row = None
    _body_step_row = None
    for step in STEPS:
        key, label, tip = step[0], step[2], step[3]
        row = ttk.Frame(sidebar, style="Panel.TFrame")
        row.pack(fill=tk.X, padx=14, pady=1)
        ttk.Checkbutton(row, text=label, variable=step_vars[key],
                        command=_update_run_btn).pack(side=tk.LEFT)
        ttk.Label(row, text=tip, style="PanelSub.TLabel").pack(
            side=tk.LEFT, padx=(6, 0))
        if key == "meshes":
            _mesh_step_row = row
        if key == "modify_body_meshes":
            _body_step_row = row

    # Small link sitting just below the Meshes checkbox row
    _mesh_toggle_row = ttk.Frame(sidebar, style="Panel.TFrame")
    _mesh_toggle_row.pack(fill=tk.X, padx=14, pady=(0, 1), after=_mesh_step_row)
    mesh_toggle_lbl = tk.Label(
        _mesh_toggle_row, text="  filter subfolders...",
        bg=CLR["panel"], fg=CLR["subtext"],
        font=("Segoe UI", 9, "underline"), cursor="hand2",
    )
    mesh_toggle_lbl.pack(side=tk.LEFT, padx=(20, 0))
    mesh_toggle_lbl.bind("<Button-1>", lambda _: _open_mesh_subdir_panel())

    # Small link sitting just below the Patch Skyrim checkbox row
    _body_toggle_row = ttk.Frame(sidebar, style="Panel.TFrame")
    _body_toggle_row.pack(fill=tk.X, padx=14, pady=(0, 1), after=_body_step_row)
    body_toggle_lbl = tk.Label(
        _body_toggle_row, text="  select plugins...",
        bg=CLR["panel"], fg=CLR["subtext"],
        font=("Segoe UI", 9, "underline"), cursor="hand2",
    )
    body_toggle_lbl.pack(side=tk.LEFT, padx=(20, 0))
    body_toggle_lbl.bind("<Button-1>", lambda _: _open_patch_plugin_panel())

    _sep()

    # ── Action buttons ────────────────────────────────────────────────────────
    bf = ttk.Frame(sidebar, style="Panel.TFrame")
    bf.pack(fill=tk.X, padx=14, pady=(0, 6))

    run_btn = ttk.Button(bf, text="  Run Selected Steps",
                         style="Run.TButton", command=lambda: _run_clicked())
    run_btn.pack(fill=tk.X, pady=(0, 6))

    # Clear Log + Cancel on the same row
    btn_row = ttk.Frame(bf, style="Panel.TFrame")
    btn_row.pack(fill=tk.X)
    btn_row.columnconfigure(0, weight=1)
    btn_row.columnconfigure(1, weight=1)

    clear_btn = ttk.Button(btn_row, text="Clear Log", command=lambda: _clear_log(),
                           style="Danger.TButton")
    clear_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

    cancel_btn = ttk.Button(btn_row, text="Cancel", command=lambda: _cancel_clicked(),
                            style="Cancel.TButton", state="disabled")
    cancel_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))

    # Progress bar + status
    prog_bar = ttk.Progressbar(sidebar, mode="indeterminate", length=200)
    prog_bar.pack(fill=tk.X, padx=14, pady=(4, 0))
    prog_bar.pack_forget()

    status_row = ttk.Frame(sidebar, style="Panel.TFrame")
    status_row.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(0, 10))

    status_var = tk.StringVar(value="Ready")
    ttk.Label(status_row, textvariable=status_var, style="PanelSub.TLabel").pack(
        side=tk.LEFT)

    timer_var = tk.StringVar(value="")
    ttk.Label(status_row, textvariable=timer_var, style="PanelSub.TLabel").pack(
        side=tk.LEFT, padx=(8, 0))

    # ── Log pane ──────────────────────────────────────────────────────────────
    log_hdr = tk.Frame(log_pane, bg=CLR["panel"], height=34)
    log_hdr.pack(fill=tk.X)
    log_hdr.pack_propagate(False)
    tk.Label(log_hdr, text="Output Log", bg=CLR["panel"],
             fg=CLR["subtext"], font=("Segoe UI", 9)).pack(
        side=tk.LEFT, padx=12, pady=8)

    log_text = tk.Text(
        log_pane, wrap=tk.WORD,
        font=("Consolas", 9),
        bg=CLR["log_bg"], fg=CLR["log_fg"],
        insertbackground=CLR["text"],
        selectbackground=CLR["accent"],
        relief="flat", borderwidth=0,
        state=tk.DISABLED, padx=10, pady=8,
    )
    log_sb = ttk.Scrollbar(log_pane, command=log_text.yview)
    log_text.configure(yscrollcommand=log_sb.set)
    log_sb.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.pack(fill=tk.BOTH, expand=True)

    log_text.tag_configure("head", foreground=CLR["accent"],
                                   font=("Consolas", 9, "bold"))
    log_text.tag_configure("ok",   foreground=CLR["log_ok"])
    log_text.tag_configure("err",  foreground=CLR["log_err"])
    log_text.tag_configure("warn", foreground=CLR["log_warn"])
    log_text.tag_configure("cmd",  foreground=CLR["blue"],
                                   font=("Consolas", 9, "bold"))
    log_text.tag_configure("dim",  foreground=CLR["subtext"])

    def _classify(line: str) -> str:
        l = line.lower()
        if line.startswith("===") or "phase" in l[:20]:
            return "head"
        if "error" in l and "errors" not in l:
            return "err"
        if "warning" in l or "warn" in l:
            return "warn"
        if line.strip() in ("done", "ok") or "complete" in l or "success" in l:
            return "ok"
        if line.startswith("Running:") or line.startswith("["):
            return "cmd"
        return None

    def _log(line: str):
        log_text.configure(state=tk.NORMAL)
        tag = _classify(line)
        if tag:
            log_text.insert(tk.END, line + "\n", tag)
        else:
            log_text.insert(tk.END, line + "\n")
        log_text.see(tk.END)
        log_text.configure(state=tk.DISABLED)

    def _clear_log():
        log_text.configure(state=tk.NORMAL)
        log_text.delete("1.0", tk.END)
        log_text.configure(state=tk.DISABLED)

    _timer_job = [None]
    _timer_start = [0.0]

    def _tick_timer():
        elapsed = time.monotonic() - _timer_start[0]
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        timer_var.set(f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}")
        _timer_job[0] = root.after(1000, _tick_timer)

    def _start_timer():
        _timer_start[0] = time.monotonic()
        _tick_timer()

    def _stop_timer():
        if _timer_job[0] is not None:
            root.after_cancel(_timer_job[0])
            _timer_job[0] = None

    def _set_running(state: bool):
        running.set() if state else running.clear()
        if not state:
            cancel_evt.clear()
        run_btn.configure(state="disabled" if state else "normal")
        cancel_btn.configure(state="normal" if state else "disabled",
                             text="Cancel")
        file_combo.configure(state="disabled" if state else "readonly")
        if state:
            prog_bar.pack(fill=tk.X, padx=14, pady=(4, 0))
            prog_bar.start(12)
            status_var.set("Running...")
            _start_timer()
        else:
            prog_bar.stop()
            prog_bar.pack_forget()
            status_var.set("Ready")
            _stop_timer()
        _update_run_btn()

    def _cancel_clicked():
        if running.is_set():
            cancel_evt.set()
            status_var.set("Cancelling...")
            cancel_btn.configure(state="disabled", text="Cancelling...")
            _log("")
            _log("  Cancelling — killing running processes...")

    # ── Run logic ─────────────────────────────────────────────────────────────
    def _build_cmd(step_key: str, fname: str, out_dir: str,
                   selected_subdirs=None, selected_patch_plugins=None) -> list:
        """Build the convert.py command for a single step."""
        _, flag, _, _, _, needs_file = next(
            s for s in STEPS if s[0] == step_key)
        cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py"), flag]
        if needs_file and fname:
            cmd += ["-f", fname]
        if out_dir:
            cmd += ["--output-dir", out_dir]
        if step_key == "meshes" and selected_subdirs:
            cmd += ["--mesh-subdirs"] + selected_subdirs
        if step_key == "modify_body_meshes" and selected_patch_plugins:
            cmd += ["--patch-plugins"] + selected_patch_plugins
        return cmd

    def _run_clicked():
        if running.is_set():
            return
        fname   = file_var.get()
        out_dir = output_var.get().strip()
        steps   = [key for key, *_ in STEPS if step_vars[key].get()]
        if not steps:
            messagebox.showwarning("No Steps",
                                   "Select at least one pipeline step.", parent=root)
            return

        # Collect selected mesh subdirs (None = all)
        selected_subdirs = None
        if "meshes" in steps and mesh_subdir_vars:
            chosen = [name for name, v in mesh_subdir_vars if v.get()]
            all_names = [name for name, _ in mesh_subdir_vars]
            if chosen and chosen != all_names:
                selected_subdirs = chosen

        # Collect selected Skyrim plugins to patch (None = all/default)
        selected_patch_plugins = None
        if "modify_body_meshes" in steps and patch_plugin_vars:
            chosen = [name for name, v in patch_plugin_vars if v.get()]
            all_names = [name for name, _ in patch_plugin_vars]
            if chosen != all_names:
                selected_patch_plugins = chosen

        _clear_log()
        _log(f"File: {fname or '(none)'}")
        _log(f"Steps: {', '.join(steps)}")
        _log(f"Output: {out_dir}")
        if selected_subdirs:
            _log(f"Mesh subdirs: {', '.join(selected_subdirs)}")
        if selected_patch_plugins is not None:
            _log(f"Patch plugins: {', '.join(selected_patch_plugins) or '(none)'}")
        _log("")

        q = queue.Queue()

        def _drain_queue():
            try:
                while True:
                    line = q.get_nowait()
                    _log(line)
            except queue.Empty:
                pass
            # Continue draining while running
            if running.is_set():
                root.after(50, _drain_queue)

        def _worker():
            _set_running(True)
            try:
                default_set = {k for k, *rest in STEPS if rest[3]}
                active_set  = set(steps)
                ret = 0
                # If selection == default set and a file is specified and no
                # mesh subfolder / patch-plugin filter, run the pipeline once
                if (active_set == default_set and fname
                        and not selected_subdirs and selected_patch_plugins is None):
                    cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py"),
                           "-f", fname]
                    if out_dir:
                        cmd += ["--output-dir", out_dir]
                    q.put(f"Running: {' '.join(cmd)}")
                    ret = _run_process(cmd, q.put, cancel_event=cancel_evt)
                else:
                    for step in steps:
                        if cancel_evt.is_set():
                            break
                        cmd = _build_cmd(step, fname, out_dir, selected_subdirs,
                                         selected_patch_plugins)
                        q.put(f"Running: {' '.join(cmd)}")
                        r = _run_process(cmd, q.put, cancel_event=cancel_evt)
                        if r == -2:
                            ret = -2
                            break
                        if r != 0:
                            ret = r

                q.put("")
                if ret == -2:
                    q.put("  CANCELLED")
                elif ret == 0:
                    q.put("  DONE")
                else:
                    q.put("  FAILED")
            finally:
                root.after(0, lambda: _set_running(False))

        threading.Thread(target=_worker, daemon=True).start()
        # Start draining the queue in the UI thread
        root.after(50, _drain_queue)

    _update_run_btn()
    root.mainloop()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def _relaunch_windowless() -> bool:
    """On Windows, re-exec the GUI under pythonw.exe so no console window lingers.

    When launched as ``python gui.py`` / ``./gui.py`` from a terminal (or via the
    ``py`` launcher, which allocates its own console), the GUI ends up with a
    console window that just sits behind it. Detaching under the console-less
    interpreter removes that stray window; the GUI's own log pane is the only
    place subprocess output should appear.

    Returns True if a relaunch was started (caller should exit), False otherwise.
    """
    if sys.platform != "win32":
        return False
    # Already console-less (launched via .pyw / pythonw) — nothing to do.
    exe = sys.executable
    if not exe.lower().endswith("python.exe"):
        return False
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if not os.path.isfile(pythonw):
        return False
    # Guard against an infinite relaunch loop.
    if os.environ.get("_TES_GUI_RELAUNCHED") == "1":
        return False

    env = os.environ.copy()
    env["_TES_GUI_RELAUNCHED"] = "1"
    # DETACHED_PROCESS so the new process has no console at all and is not tied
    # to the (soon-to-close) parent terminal.
    flags = getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(
            [pythonw, str(Path(__file__).resolve())] + sys.argv[1:],
            cwd=str(SCRIPT_DIR),
            env=env,
            creationflags=flags,
            close_fds=True,
        )
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="TES4->TES5 Converter GUI")
    parser.add_argument("--cli", action="store_true",
                        help="Headless: forward remaining args to convert.py")
    args, extra = parser.parse_known_args()

    if args.cli:
        cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py")] + extra
        ret = subprocess.run(cmd, cwd=str(SCRIPT_DIR), **_POPEN_FLAGS)
        return ret.returncode

    # Detach from any inherited console so the GUI stands alone (Windows only).
    if _relaunch_windowless():
        return 0

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
