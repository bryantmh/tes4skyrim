"""The window itself: its palette, its OS chrome, and the carrier.

`GuiApp` is the seam that let gui.py be split at all. It grew out of `ModsUI`
(see `mods.py`), which proved the shape: a plain carrier the builder fills once
and passes in, so a dialog never closes over the builder's local scope.

Every extracted module takes the carrier as a parameter and binds its own
callbacks onto it as it is wired, so the wiring order is the only place that
knows how the pieces fit together.

tkinter is imported inside functions, never at module scope: `gui.py`
re-exports from here and must stay importable headless (test_gui_startup
asserts `not hasattr(gui, "tk")`).

See: docs/reference/pipeline.md#configuration
"""

import sys
import threading
import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import ttk

import version as version_info
from core.gui import mods as gui_mods
from core.gui import panels, runner, selection, widgets
from core.gui.config import (
    CLR,
    EXPORT_DIR,
    GLOBAL_ACTIONS,
    ICON_HANDLES,
    PACK_DEFAULT_CONFIG_KEY,
    REPO_ROOT,
    STEPS,
    WINDING_AUTO,
    LOD_DETAIL_CONFIG_KEY,
    WINDING_CONFIG_KEY,
    WINDING_MODES,
    default_on_steps,
    find_game_path,
    load_config,
    save_config,
    scan_plugins,
    winding_enabled_for,
)
from core.gui.menus import build_menubar
from core.worker_budget import cpu_total, worker_count
from preflight import RC_MISSING_DEP

# ---------------------------------------------------------------------------
#  Palette and OS chrome
# ---------------------------------------------------------------------------


def set_app_user_model_id() -> None:
    """Give the process its own taskbar identity, so it shows OUR icon.

    Windows groups taskbar buttons by AppUserModelID; without an explicit
    one a script inherits the interpreter's and shows its icon.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "TESConversion.AutoConvert.GUI")
    except Exception:
        pass


def set_window_icon(root, icon_path) -> None:
    """Load the .ico at both icon sizes Windows asks for.

    Tk's `iconbitmap` only ever supplies the SMALL icon (title bar / Alt-Tab).
    The taskbar button and the large Alt-Tab overlay ask for ICON_BIG, which Tk
    never sets, so the shell substitutes a generic one. LoadImage picks the
    best-matching frame out of our multi-resolution .ico for each request.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()

        IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        metrics = {
            ICON_BIG: (ctypes.windll.user32.GetSystemMetrics(11),
                       ctypes.windll.user32.GetSystemMetrics(12)),
            ICON_SMALL: (ctypes.windll.user32.GetSystemMetrics(49),
                         ctypes.windll.user32.GetSystemMetrics(50)),
        }

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]

        for which, (cx, cy) in metrics.items():
            flags = LR_LOADFROMFILE | (LR_DEFAULTSIZE if not cx else 0)
            handle = user32.LoadImageW(None, str(icon_path), IMAGE_ICON,
                                       cx, cy, flags)
            if handle:
                ICON_HANDLES.append(handle)
                user32.SendMessageW(hwnd, WM_SETICON, which, handle)
    except Exception:
        pass


def style_titlebar(root) -> None:
    """Recolor the native Windows title bar to match the app's dark/purple theme."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())

        def _bgr(hexcolor: str) -> int:
            """`#rrggbb` as the BGR integer the DWM API wants."""
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
        pass


DND_AVAILABLE = False


def make_root():
    """The Tk root, drag-and-drop capable when tkinterdnd2 is installed.

    tkinterdnd2 works by subclassing Tk and loading the tkdnd Tcl package,
    so it must be chosen at root creation rather than bolted on later. The
    fallback imports tkinter locally, so a missing package can never stop
    the window from opening.
    """
    global DND_AVAILABLE
    set_app_user_model_id()
    import tkinter
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        DND_AVAILABLE = True
        return root
    except Exception:
        return tkinter.Tk()


# ---------------------------------------------------------------------------
#  The carrier
# ---------------------------------------------------------------------------

class PanelSelection:
    """What the modal panels decided, and what the command builder reads.

    The panels and the command builder used to communicate only through
    `gui_main`'s closure variables; this is that channel made explicit, so both
    sides name one object instead of sharing a scope. `__slots__` is
    load-bearing: it turns a misspelled field into an AttributeError rather
    than the silently-ignored write the closure allowed.

    An empty list means "never confirmed", which is what lets the command
    builder fall back to its computed defaults -- derived fresh each time a
    dialog opens, so converting another plugin shows up without the user
    having to reset anything.

    `lod_plugins` order is conflict resolution, not presentation: LOD tiles
    are files on a fixed grid, so the LAST plugin applied wins every tile two
    of them both change.
    """

    __slots__ = ("lod_plugins", "lod_worldspaces", "master_plugins",
                 "convert_ui", "patch_plugins")

    def __init__(self):
        """Start with nothing chosen, so every default still applies."""
        self.lod_plugins = []
        self.lod_worldspaces = []
        self.master_plugins = []
        self.convert_ui = {"messagebox": True, "cursor": True}
        self.patch_plugins = []


class GuiApp:
    """What every extracted GUI module needs from the main window.

    A carrier, not a base class. Widgets and tk vars are filled in build order;
    the callback attributes are bound by each module as it is wired, which is
    why they start as None rather than being declared abstract.
    """

    def __init__(self, root, cfg):
        """Bind the root window and saved config; the rest fills in later."""
        self.root = root
        self.cfg = cfg
        self.CLR = CLR
        self.REPO_ROOT = REPO_ROOT
        self.EXPORT_DIR = EXPORT_DIR
        self.cpu_max = cpu_total()
        self.running = threading.Event()
        self.cancel_evt = threading.Event()
        self.selection = PanelSelection()
        self._init_widgets()
        self._init_vars()
        self._init_callbacks()

    def _init_widgets(self):
        """Declare every widget slot; `build_layout` fills them in order."""
        self.outer = self.sidebar = self.sb_body = self.sb_canvas = None
        self.log_pane = self.log_text = None
        self.file_combo = self.scope_combo = None
        self.run_btn = self.cancel_btn = self.upgrade_btn = None
        self.prog_bar = self.status_row = None
        self.menubar = self.converted_menu = self.update_mb = None
        self.style = None
        self.global_btns = {}
        self.step_widgets = {}

    def _init_vars(self):
        """Declare the tk variables and the plugin-list state they drive.

        `last_valid` holds the last name the user actually committed to: typing
        a search fragment writes over the entry text, so focus leaving
        mid-search falls back to it rather than stranding the fragment.
        `searching` is True only while the text IS a fragment, so a committed
        name never filters the list down to itself.
        """
        self.tes4_var = self.tes5_var = self.output_var = None
        self.file_var = self.scope_var = self.workers_var = None
        self.status_var = self.timer_var = None
        self.cache_dl_var = self.pack_default_var = None
        self.winding_mode_var = self.parallax_var = self.tex_only_var = None
        self.lod_detail_var = None
        self.step_vars = {}
        self.mesh_subdir_vars = []
        self.all_plugins = []
        self.scope_ids = []
        self.scope_rows = {}
        self.last_valid = [""]
        self.searching = [False]
        self.last_global_stamp = {}
        self.upgrade_plan = [None]
        self.plan_applied = set()
        self.mesh_step_row = None

    def _init_callbacks(self):
        """Declare the callback slots each module binds as it is wired.

        They start None rather than abstract because the binding order IS the
        wiring order: a module can only bind what its dependencies already set.
        """
        self.info = self.confirm = self.log = self.clear_log = None
        self.dialog = self.open_url = self.attach_tooltip = None
        self.refresh_scopes = self.apply_scope = None
        self.select_source_for_plugin = None
        self.apply_step_availability = self.refresh_upgrade_notice = None
        self.set_running = self.start_timer = self.stop_timer = None
        self.update_run_btn = self.set_default = None
        self.refresh_global_btns = self.run_global_action = None
        self.start_global_action = self.commit_plugin = None
        self.sb_tag_wheel = self.save_dirs = None
        self.run_log = None

    def out_root(self) -> Path:
        """The configured output directory, or the default beside the repo."""
        chosen = self.output_var.get().strip() if self.output_var else ""
        return Path(chosen or str(REPO_ROOT / "output"))

    def winding_on(self) -> bool:
        """Whether the INFERRED winding steps run for the current plugin."""
        return winding_enabled_for(self.winding_mode_var.get(),
                                   self.file_var.get())

    def get_workers(self) -> int:
        """The worker-count value, clamped to [1, cpu_max]."""
        import tkinter as tk

        try:
            n = int(self.workers_var.get())
        except (tk.TclError, ValueError):
            n = worker_count()
        n = max(1, min(n, self.cpu_max))
        if n != self.workers_var.get():
            self.workers_var.set(n)
        return n


# ---------------------------------------------------------------------------
#  Building the window
# ---------------------------------------------------------------------------

def apply_styles(root):
    """Configure every ttk style the window uses; returns the ttk.Style.

    Drop.TFrame shifts only its background: a ttk.Frame cannot draw a
    border without a relief that reflows every child, and a visible reflow
    on hover reads as a glitch.
    """
    from tkinter import ttk

    style = ttk.Style(root)
    style.theme_use("clam")

    def S(*a, **kw):
        """Shorthand for `style.configure`."""
        style.configure(*a, **kw)


    S(".",             background=CLR["bg"], foreground=CLR["text"],
                       troughcolor=CLR["panel"], borderwidth=0, relief="flat")
    S("TFrame",        background=CLR["bg"])
    S("Panel.TFrame",  background=CLR["panel"])
    S("Drop.TFrame",   background=CLR["global_btn"])

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

    _apply_button_styles(S, style)
    return style


def _apply_button_styles(S, style):
    """Configure the pushbutton styles: Run, Cancel and the global row.

    Global actions are teal and outlined rather than gold-and-solid so
    the sidebar reads as two kinds of thing: a global action takes no
    plugin, runs over the whole load order, and its result is shared by
    every conversion. The done state stays clickable -- re-running is
    always allowed, it just stops advertising itself as outstanding work.
    """
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
                        borderwidth=0, relief="flat", padding=(8, 6),
                        font="Segoe\\ UI 10")
    style.map("Danger.TButton", background=[("active", "#5a3030")])

    S("Global.TButton", background=CLR["global_btn"], foreground=CLR["blue"],
                        borderwidth=0, relief="flat", padding=(8, 6),
                        font="Segoe\\ UI 9")
    style.map("Global.TButton",
              background=[("active", CLR["global_hover"]),
                          ("disabled", CLR["panel"])],
              foreground=[("disabled", CLR["subtext"])])

    S("GlobalDone.TButton", background=CLR["btn"], foreground=CLR["subtext"],
                        borderwidth=0, relief="flat", padding=(8, 6),
                        font="Segoe\\ UI 9")
    style.map("GlobalDone.TButton",
              background=[("active", CLR["btn_hover"])],
              foreground=[("active", CLR["text"])])

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


def _initial_paths(cfg: dict) -> tuple:
    """(tes4, tes5, output) from the config, auto-detecting what is missing."""
    return (cfg.get("tes4DataPath", "") or find_game_path("oblivion"),
            cfg.get("tes5DataPath", "") or find_game_path("skyrimse"),
            cfg.get("outputDir", "") or str(REPO_ROOT / "output"))


def _initial_workers(cfg: dict, cpu_max: int) -> int:
    """The saved worker count if usable, else the pipeline's own default."""
    try:
        chosen = int(cfg.get("workers"))
    except (TypeError, ValueError):
        chosen = worker_count()
    return max(1, min(chosen, cpu_max))


def _initial_winding(cfg: dict) -> str:
    """The saved winding mode; anything unrecognised reads as auto."""
    mode = str(cfg.get(WINDING_CONFIG_KEY, "")).strip().lower()
    return mode if mode in WINDING_MODES else WINDING_AUTO


def _initial_lod_detail(cfg: dict) -> int:
    """The saved LOD detail preset index, clamped to a real preset."""
    from asset_convert.lod.mesh_decimate import (LOD_DETAIL_DEFAULT,
                                                 LOD_DETAIL_PRESETS)
    try:
        idx = int(cfg.get(LOD_DETAIL_CONFIG_KEY, LOD_DETAIL_DEFAULT))
    except (TypeError, ValueError):
        return LOD_DETAIL_DEFAULT
    return max(0, min(idx, len(LOD_DETAIL_PRESETS) - 1))


def build_state(root, cfg: dict) -> GuiApp:
    """The carrier with every tk variable created and seeded from `cfg`.

    The two boolean settings default ON and are turned off only by an explicit
    saved `false`, so a config written before either option existed keeps the
    old behaviour instead of silently losing it.
    """
    import tkinter as tk

    app = GuiApp(root, cfg)
    tes4_path, tes5_path, output_path = _initial_paths(cfg)
    app.tes4_var = tk.StringVar(value=tes4_path)
    app.tes5_var = tk.StringVar(value=tes5_path)
    app.output_var = tk.StringVar(value=output_path)
    app.file_var = tk.StringVar()
    app.workers_var = tk.IntVar(value=_initial_workers(cfg, app.cpu_max))
    app.cache_dl_var = tk.BooleanVar(
        value=cfg.get("navmeshCacheDownload") is not False)
    app.pack_default_var = tk.BooleanVar(
        value=cfg.get(PACK_DEFAULT_CONFIG_KEY) is not False)
    app.winding_mode_var = tk.StringVar(value=_initial_winding(cfg))
    app.lod_detail_var = tk.IntVar(value=_initial_lod_detail(cfg))
    app.parallax_var = tk.BooleanVar(value=False)
    app.tex_only_var = tk.BooleanVar(value=False)

    initial_on = default_on_steps(app.pack_default_var.get())
    app.step_vars = {key: tk.BooleanVar(value=(key in initial_on))
                     for key, *_ in STEPS}
    return app


# ---------------------------------------------------------------------------
#  The menu bar
# ---------------------------------------------------------------------------


def create_window():
    """The root window, sized, themed and icon'd; returns (root, config).

    1060x1030 rather than the old 900: the sidebar's content measures 1003px
    including window chrome, so a 1000px default clipped its own bottom block
    by 3px on every machine before DPI or a taskbar was involved. The sidebar
    scrolls now, but the DEFAULT window should still clear it outright. The
    minimum only has to keep the log pane usable, for the same reason.
    """
    from core.gui.config import load_config, write_default_config

    write_default_config()
    cfg = load_config()
    root = make_root()
    root.title(f"T.E.SR.A.C.T  {version_info.current_version()}")
    root.geometry("1060x1030")
    root.minsize(860, 520)
    root.configure(bg=CLR["bg"])
    root.option_add("*Background", CLR["bg"])
    root.option_add("*Foreground", CLR["text"])
    icon_path = REPO_ROOT / "docs" / "assets" / "favicon.ico"
    root.iconbitmap(default=str(icon_path))
    set_window_icon(root, icon_path)
    style_titlebar(root)
    apply_styles(root)
    return root, cfg


def build_layout(app):
    """Split the window into sidebar and log pane; returns (sb_body, log_pane).

    The sidebar column has a fixed minimum so the two-column Global rows keep
    their width; the log pane takes all the slack.
    """
    import tkinter as tk
    from tkinter import ttk

    from core.gui.widgets import build_scroller

    outer = ttk.Frame(app.root)
    outer.pack(fill=tk.BOTH, expand=True)
    app.outer = outer
    outer.columnconfigure(0, weight=0, minsize=330)
    outer.columnconfigure(1, weight=1)
    outer.rowconfigure(0, weight=1)

    app.sidebar = ttk.Frame(outer, style="Panel.TFrame")
    app.sidebar.grid(row=0, column=0, sticky="nsew")
    sb_body = build_scroller(app)

    log_pane = tk.Frame(outer, bg=CLR["log_bg"])
    log_pane.grid(row=0, column=1, sticky="nsew")
    app.log_pane = log_pane
    return sb_body, log_pane

# ---------------------------------------------------------------------------
#  Run, Global and status widgets
# ---------------------------------------------------------------------------

def build_run_buttons(app, parent, sep_gap: int) -> None:
    """Run, Clear Log and Cancel; binds `run_btn` and `cancel_btn`.

    The top pad matches a separator's gap: the step rows are packed tight, so
    without it Run crowds the last checkbox and reads as another entry in the
    list rather than the thing that runs them. Bottom pad is 0 because the rule
    below owns that gap.
    """
    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.pack(fill=tk.X, padx=14, pady=(sep_gap, 0))

    app.run_btn = ttk.Button(frame, text="  Run Selected Steps",
                             style="Run.TButton",
                             command=lambda: app.run_clicked())
    app.run_btn.pack(fill=tk.X, pady=(0, 6))

    row = ttk.Frame(frame, style="Panel.TFrame")
    row.pack(fill=tk.X)
    row.columnconfigure(0, weight=1)
    row.columnconfigure(1, weight=1)
    ttk.Button(row, text="Clear Log", style="Danger.TButton",
               command=lambda: app.clear_log()).grid(
                   row=0, column=0, sticky="ew", padx=(0, 3))
    app.cancel_btn = ttk.Button(row, text="Cancel", style="Cancel.TButton",
                                state="disabled",
                                command=lambda: app.cancel_clicked())
    app.cancel_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))


def _global_rows() -> dict:
    """Row index -> the actions on it, skipping the menu-only ones."""
    rows = {}
    for act in GLOBAL_ACTIONS:
        if act[4] is not None:
            rows.setdefault(act[4], []).append(act)
    return rows


def _global_header(app, parent) -> None:
    """The "Global" caption, padded to match the Pipeline Steps header."""
    header = ttk.Frame(parent, style="Panel.TFrame")
    header.pack(fill=tk.X, padx=14, pady=(0, 4))
    ttk.Label(header, text="Global", style="PanelSub.TLabel").pack(side=tk.LEFT)


def _global_button_grid(app, parent, attach_tooltip) -> None:
    """One button per global action, two uniform columns per row."""
    rows = _global_rows()
    for index in sorted(rows):
        acts = rows[index]
        row_frame = ttk.Frame(parent, style="Panel.TFrame")
        row_frame.pack(fill=tk.X, pady=(0 if index == 0 else 4, 0))
        row_frame.columnconfigure(0, weight=1, uniform="global")
        row_frame.columnconfigure(1, weight=1, uniform="global")
        for i, (gkey, _label, gtip, gshort, _r) in enumerate(acts):
            btn = ttk.Button(row_frame, text=gshort, style="Global.TButton",
                             command=(lambda k=gkey: app.run_global_action(k)))
            btn.grid(row=0, column=i, sticky="ew",
                     padx=((0, 3) if i == 0 else (3, 0)))
            attach_tooltip(btn, gtip)
            app.global_btns[gkey] = btn


def _global_links(app, parent, on_patch_plugins) -> None:
    """The sub-links under the Global buttons, sharing their two columns.

    Column 0 sits under "Patch Skyrim", the only action with a selection to
    make from here. Create LOD needs no sub-link -- its button opens the
    selection dialog itself -- and neither packaging action has a choice.
    Stacked on separate rows they would read as two options of the left-hand
    button rather than one option of each.
    """
    links = ttk.Frame(parent, style="Panel.TFrame")
    links.pack(fill=tk.X, pady=(5, 0))
    links.columnconfigure(0, weight=1, uniform="global")
    links.columnconfigure(1, weight=1, uniform="global")
    label = tk.Label(links, text="select plugins...", bg=CLR["panel"],
                     fg=CLR["subtext"], font=("Segoe UI", 9, "underline"),
                     cursor="hand2")
    label.grid(row=0, column=0, sticky="w", padx=(0, 3))
    label.bind("<Button-1>", lambda _e: on_patch_plugins())


def build_global_block(app, parent, attach_tooltip, sep, on_patch_plugins):
    """The Global caption, its button grid and its sub-links.

    Uses the shared `sep` rather than a hand-rolled Separator so its spacing
    matches the only two other rules in the sidebar.
    """
    sep()
    _global_header(app, parent)
    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.pack(fill=tk.X, padx=14, pady=(0, 6))
    _global_button_grid(app, frame, attach_tooltip)
    _global_links(app, frame, on_patch_plugins)


def build_status_row(app) -> None:
    """The progress bar and status/timer row, pinned below the scroller.

    The timer is packed FIRST so it claims its space at the right and the
    status label fills what is left. The row's width is the sidebar's, never
    the text's: a long message (an archive basename runs to 45+ chars) used to
    stretch the whole left pane until it cleared.
    """
    app.prog_bar = ttk.Progressbar(app.sidebar, mode="indeterminate",
                                   length=200)
    app.prog_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14,
                      pady=(0, 6))
    app.prog_bar.grid_remove()

    row = ttk.Frame(app.sidebar, style="Panel.TFrame")
    row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 10))
    app.status_var = tk.StringVar(value="Ready")
    app.timer_var = tk.StringVar(value="")

    timer = ttk.Label(row, textvariable=app.timer_var,
                      style="PanelSub.TLabel")
    timer.pack(side=tk.RIGHT, padx=(8, 0))
    status = ttk.Label(row, textvariable=app.status_var,
                       style="PanelSub.TLabel", anchor="w")
    status.pack(side=tk.LEFT, fill=tk.X, expand=True)

    _freeze_height(row, status, timer)
    app.status_row = row


def _freeze_height(row, status, timer) -> None:
    """Fix the row at one line of its own font, then stop propagation.

    Order matters: `pack_propagate(False)` on a frame with no height set
    collapses it to 1px, so the height has to be taken from the laid-out label
    first.
    """
    row.update_idletasks()
    row.configure(height=max(status.winfo_reqheight(),
                             timer.winfo_reqheight()))
    row.grid_propagate(False)
    row.pack_propagate(False)


def _banner_image(banner_path):
    """The scaled banner, or None when PIL or the file is unavailable.

    Scaled to 1.5x the width-fit height, then cropped left and right back to
    the target width, so the logo reads larger within the same column.
    """
    try:
        from PIL import Image, ImageTk
    except Exception:
        return None
    try:
        src = Image.open(banner_path)
        target_w = 350
        base_h = target_w * src.height / src.width
        scale_h = round(base_h * 1.5)
        scale_w = round(target_w * (scale_h / base_h))
        src = src.resize((scale_w, scale_h), Image.LANCZOS)
        left = (scale_w - target_w) // 2
        return ImageTk.PhotoImage(src.crop((left, 0, left + target_w,
                                            scale_h)))
    except Exception:
        return None


def build_title(parent, banner_path) -> None:
    """The banner, or a text title when it cannot be loaded.

    The label keeps its own reference to the PhotoImage: Tk holds only a weak
    one, so without it the banner is garbage-collected and shows blank.
    """
    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.pack(fill=tk.X, padx=14, pady=(16, 0))
    image = _banner_image(banner_path) if banner_path.exists() else None
    if image is not None:
        label = ttk.Label(frame, image=image, style="Panel.TLabel")
        label.image = image
        label.pack(fill=tk.X)
        return
    ttk.Label(frame, text="TESRACT",
              style="Head.TLabel").pack(anchor="w")
    ttk.Label(frame, text="Gamebryo to Skyrim conversion",
              style="PanelSub.TLabel").pack(anchor="w")

# ---------------------------------------------------------------------------
#  Assembling the window
# ---------------------------------------------------------------------------



class _Fields:
    """The sidebar frames whose display order is decided after the fact."""

    __slots__ = ("source", "plugin", "output", "skyrim")

    def __init__(self):
        """Start empty; each builder fills its own slot."""
        self.source = self.plugin = self.output = self.skyrim = None


def _build_plugin_field(app, parent, on_scope_change):
    """The Plugin File combobox, with type-ahead wired onto it."""
    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.pack(fill=tk.X, padx=14, pady=(0, 8))
    ttk.Label(frame, text="Plugin File", style="PanelSub.TLabel").pack(
        anchor="w")
    initial = scan_plugins(app.tes4_var.get())
    app.all_plugins[:] = initial
    app.file_combo = ttk.Combobox(frame, textvariable=app.file_var,
                                  values=initial, width=30)
    app.file_combo.pack(fill=tk.X, pady=(2, 0))
    commit = widgets.build_plugin_combo(app, initial, on_scope_change)
    return frame, commit


def _save_dirs(app):
    """Write the three directory fields and the worker count."""
    updated = load_config()
    updated["tes4DataPath"] = app.tes4_var.get()
    updated["tes5DataPath"] = app.tes5_var.get()
    updated["outputDir"] = app.output_var.get()
    updated["workers"] = app.get_workers()
    save_config(updated)


def _order_fields(fields, sep) -> None:
    """Re-pack the sidebar fields into their display order.

    Source, Skyrim dir, Output dir, then Plugin File: the first two are set
    once, while Source and Plugin change run to run, so Plugin reads last,
    nearest the steps it drives. Re-packed rather than moved because the
    selector's combobox handling is closed over by the source callbacks. The
    rule goes ABOVE the plugin box, closing off the set-once directories.
    """
    for block in (fields.source, fields.skyrim, fields.output):
        block.pack_forget()
        block.pack(fill=tk.X, padx=14, pady=(0, 8))
    fields.output.pack_configure(pady=(0, 0))
    sep()
    fields.plugin.pack_forget()
    fields.plugin.pack(fill=tk.X, padx=14, pady=(0, 12))


def _build_fields(app, sb_body, mods_ui, refresh, apply_scope, sep, path_row):
    """The four sidebar path/selection blocks; returns (fields, commit)."""
    fields = _Fields()
    save_dirs = partial(_save_dirs, app)
    on_availability = partial(selection.apply_step_availability, app)

    fields.source, hint = selection.build_panel(
        app, sb_body,
        lambda: selection.add_source_dir(app, refresh, apply_scope),
        lambda: selection.remove_source(app, refresh, apply_scope,
                                      lambda: gui_mods.manage_mods(mods_ui)))
    app.src_hint = hint
    app.scope_combo.bind(
        "<<ComboboxSelected>>",
        lambda _e: selection.on_scope_selected(app, refresh, apply_scope))

    fields.plugin, commit = _build_plugin_field(app, sb_body, on_availability)

    fields.output = ttk.Frame(sb_body, style="Panel.TFrame")
    fields.output.pack(fill=tk.X, padx=14, pady=(0, 8))
    path_row(fields.output, "Output Directory", app.output_var,
             browse_dir=True, on_change=lambda _p: save_dirs())

    fields.skyrim = ttk.Frame(sb_body, style="Panel.TFrame")
    fields.skyrim.pack(fill=tk.X, padx=14, pady=(0, 0))
    path_row(fields.skyrim, "Skyrim SE Data Directory", app.tes5_var,
             browse_dir=True,
             on_change=lambda _p: (panels.refresh_patch_plugin_vars(app),
                                   save_dirs()))
    _order_fields(fields, sep)
    return fields, commit


def _bind_global_actions(app) -> None:
    """Bind the global-action callbacks, and the confirm panels they use.

    Create LOD, Convert to Master and Convert UI each rewrite something shared,
    so their selection is confirmed in a panel before anything runs.
    """
    def _stamp_of(key):
        """This action's input fingerprint."""
        return runner.global_stamp(app, key,
                                   partial(runner.plugin_esm, app))

    def _is_current(key):
        """Whether the action's result is still up to date."""
        return runner.global_is_current(app, key, app.last_global_stamp,
                                          _stamp_of)

    def _record_done(key):
        """Remember that `key` completed, so its button greys out."""
        try:
            app.last_global_stamp[key] = _stamp_of(key)
            version_info.record_step_run(key, None)
        except Exception:
            pass

    def _start(key):
        """Launch the action with its selection already settled."""
        runner.start_global_action(app, key, _record_done)

    confirm_panels = {
        "create_lod": lambda go: panels.open_create_lod_panel(
            app, on_generate=lambda _p, _w: go()),
        "make_master": lambda go: panels.open_make_master_panel(app,
                                                                on_apply=go),
        "convert_ui": lambda go: panels.open_convert_ui_panel(app,
                                                              on_continue=go),
    }

    def _run(key):
        """Run one global action, after its confirming panel if it has one."""
        if app.running.is_set():
            return
        panel = confirm_panels.get(key)
        if panel is None:
            _start(key)
            return
        panel(lambda: _start(key))

    app.global_stamp = _stamp_of
    app.global_is_current = _is_current
    app.start_global_action = _start
    app.run_global_action = _run
    app.refresh_global_btns = partial(runner.refresh_global_btns, app,
                                      _is_current)


def _bind_mods_ui(app, mods_ui, outer) -> None:
    """Give gui_mods the callbacks its dialogs need, now they all exist."""
    mods_ui.outer = outer
    mods_ui.running = app.running
    mods_ui.status_var = app.status_var
    mods_ui.info = app.info
    mods_ui.confirm = app.confirm
    mods_ui.log = app.log
    mods_ui.clear_log = app.clear_log
    mods_ui.refresh_scopes = app.refresh_scopes
    mods_ui.apply_scope = app.apply_scope
    mods_ui.apply_step_availability = app.apply_step_availability
    mods_ui.refresh_upgrade_notice = app.refresh_upgrade_notice
    mods_ui.set_running = app.set_running
    mods_ui.start_timer = app.start_timer
    mods_ui.stop_timer = app.stop_timer


def _seed_global_stamps(app) -> None:
    """Grey the global buttons whose work a past session already did.

    Only the actions with NO testable artefact need seeding: for the rest
    `global_is_current` resolves it from the file itself, and a recorded run
    is the sole evidence the others ever completed.
    """
    for gkey, *_ in GLOBAL_ACTIONS:
        try:
            if (runner.global_artifact(app, gkey) is None
                    and version_info.steps_run_at(None).get(gkey)):
                app.last_global_stamp[gkey] = app.global_stamp(gkey)
        except Exception:
            pass


def _seed_startup(app) -> None:
    """Populate the window from what this install already knows.

    The wheel handler is handed out here, not at definition time: the sidebar
    body was still empty then. The source list is seeded from the folders past
    conversions ran from, so someone with two installs sees both rather than
    re-adding the second by hand. The plugin combo is populated first, so an
    upgrade is visible and pre-selected before the user touches anything.
    """
    app.sb_tag_wheel(app.sb_canvas)
    try:
        from asset_convert.sources import source_registry
        source_registry.migrate_known_directories(
            EXPORT_DIR, extra_dirs=[app.tes4_var.get()])
    except Exception:
        pass
    app.refresh_scopes()
    app.apply_scope(select_plugin=app.file_var.get())
    app.apply_step_availability()
    app.refresh_upgrade_notice()
    app.update_run_btn()
    _seed_global_stamps(app)
    app.refresh_global_btns()


def build_window():
    """Build the whole converter window; returns its Tk root.

    The caller runs the mainloop, so this stays testable: the window can be
    assembled, inspected and torn down without ever entering it.
    """
    root, cfg = create_window()
    app = build_state(root, cfg)
    mods_ui = build_menubar(app)
    sb_body, log_pane = build_layout(app)

    app.info = partial(widgets.info, app)
    app.confirm = partial(widgets.confirm, app)
    app.dialog = partial(widgets.dialog, app)
    app.save_dirs = partial(_save_dirs, app)
    app.apply_step_availability = partial(selection.apply_step_availability, app)
    sep = partial(widgets.sep, sb_body)

    app.refresh_scopes = lambda select=None: selection.refresh_scopes(
        app, app.src_hint, select)
    app.apply_scope = lambda select_plugin=None: selection.apply_scope(
        app, app.save_dirs, app.last_valid, app.searching, select_plugin)
    app.select_source_for_plugin = lambda name: (
        selection.select_source_for_plugin(app, name, app.refresh_scopes,
                                         app.apply_scope))

    build_title(sb_body, REPO_ROOT / "docs" / "assets" / "banner.png")
    sep()
    _fields, commit = _build_fields(app, sb_body, mods_ui, app.refresh_scopes,
                                    app.apply_scope, sep, widgets.path_row)
    app.commit_plugin = commit

    selection.build_step_panel(app, sb_body)
    panels.refresh_patch_plugin_vars(app)
    selection.build_mesh_options(app, sb_body, widgets.attach_tooltip,
                                 partial(panels.open_mesh_subdir_panel, app))

    app.run_clicked = partial(runner.run_clicked, app, RC_MISSING_DEP)
    app.cancel_clicked = partial(runner.cancel_run, app)
    build_run_buttons(app, sb_body, widgets.SEP_GAP)
    _bind_global_actions(app)
    build_global_block(app, sb_body, widgets.attach_tooltip, sep,
                               partial(panels.open_patch_plugin_panel, app))
    build_status_row(app)

    runner.bind_log(app, log_pane)
    runner.bind_timer(app)
    runner.bind_set_running(app)
    _bind_mods_ui(app, mods_ui, app.outer)
    selection.install_dropzone(app, mods_ui)
    _seed_startup(app)
    return root
