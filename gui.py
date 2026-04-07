"""
TES4-to-TES5 Conversion Tool — GUI

Usage:
  python gui.py          # open GUI
  python gui.py --cli    # headless CLI wrapper (see --help)
"""

import argparse
import json
import subprocess
import sys
import threading
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "conversion_config.json"

# ── Pipeline steps ordered for display ────────────────────────────────────────
STEPS = [
    ("export",             "Export",             "Parse TES4 binary → key/value text cache"),
    ("import",             "Import",             "Build TES5 binary ESM/ESP from text cache"),
    ("assets",             "Assets",             "Extract BSAs, convert NIFs/SPTs, copy textures"),
    ("lod",                "LOD",                "Generate object & terrain LOD meshes"),
    ("modify_body_meshes", "Body Meshes",        "Add greaves partition to character body NIFs"),
    ("verify_plugin",      "Verify",             "Run integrity checks on output plugin(s)"),
]

# CLI flag name for each step key
_STEP_FLAGS = {
    "export":             "--export-only",
    "import":             "--import-only",
    "assets":             "--assets-only",
    "lod":                "--lod-only",
    "modify_body_meshes": "--modify-body-meshes",
    "verify_plugin":      "--verify-plugin",
}

# Steps enabled by default
_DEFAULT_ON = {"export", "import", "assets"}

# Steps that take a -f FILE argument
_STEPS_NEED_FILE = {"export", "import", "assets", "lod", "verify_plugin"}

# ── Colours ───────────────────────────────────────────────────────────────────
CLR = {
    "bg":          "#1e1e2e",   # main background
    "panel":       "#2a2a3d",   # card / panel background
    "border":      "#44475a",   # border / separator
    "accent":      "#7c6af7",   # purple accent
    "accent_hover":"#9a8cf8",
    "btn":         "#313244",   # button bg
    "btn_hover":   "#45475a",
    "green":       "#a6e3a1",
    "red":         "#f38ba8",
    "yellow":      "#f9e2af",
    "blue":        "#89dceb",
    "text":        "#cdd6f4",   # primary text
    "subtext":     "#6c7086",   # secondary / label text
    "log_bg":      "#141420",   # log pane background
    "log_fg":      "#cdd6f4",
    "log_info":    "#89b4fa",
    "log_ok":      "#a6e3a1",
    "log_err":     "#f38ba8",
    "log_warn":    "#f9e2af",
    "check_on":    "#7c6af7",
    "check_off":   "#44475a",
}


def load_config():
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    files = cfg.get("files", [])
    cfg["_files"] = [f if isinstance(f, str) else f["name"] for f in files]
    return cfg


def _run_process(cmd, log_cb):
    """Run a subprocess, streaming output line-by-line to log_cb."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=str(SCRIPT_DIR), encoding="utf-8", errors="replace",
        )
        for line in proc.stdout:
            log_cb(line.rstrip())
        proc.wait()
        return proc.returncode
    except Exception as exc:
        log_cb(f"ERROR: {exc}")
        return -1


# ══════════════════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════════════════

def gui_main():
    try:
        import tkinter as tk
        from tkinter import ttk, scrolledtext, messagebox, font as tkfont
    except ImportError:
        print("ERROR: tkinter not available")
        return 1

    cfg = load_config()
    if not cfg:
        tk.Tk().withdraw()
        messagebox.showerror("Error", f"Not found: {CONFIG_FILE}")
        return 1

    available = cfg.get("_files", [])

    # ── Root window ───────────────────────────────────────────────────────────
    root = tk.Tk()
    root.title("TES4 → TES5 Converter")
    root.geometry("1000x760")
    root.minsize(820, 600)
    root.configure(bg=CLR["bg"])

    # Prevent tk default grey leaking through
    root.option_add("*Background",       CLR["bg"])
    root.option_add("*Foreground",       CLR["text"])
    root.option_add("*Font",             "Segoe\\ UI 10")

    style = ttk.Style(root)
    style.theme_use("clam")

    # ── ttk style overrides ───────────────────────────────────────────────────
    def S(*args, **kw):
        style.configure(*args, **kw)

    S(".",                    background=CLR["bg"], foreground=CLR["text"],
                              troughcolor=CLR["panel"], borderwidth=0, relief="flat")
    S("TFrame",               background=CLR["bg"])
    S("Panel.TFrame",         background=CLR["panel"])
    S("TLabel",               background=CLR["bg"], foreground=CLR["text"])
    S("Sub.TLabel",           background=CLR["bg"], foreground=CLR["subtext"],
                              font="Segoe\\ UI 9")
    S("Head.TLabel",          background=CLR["bg"], foreground=CLR["text"],
                              font="Segoe\\ UI 11 bold")
    S("Panel.TLabel",         background=CLR["panel"], foreground=CLR["text"])
    S("PanelSub.TLabel",      background=CLR["panel"], foreground=CLR["subtext"],
                              font="Segoe\\ UI 9")

    S("TCombobox",            fieldbackground=CLR["btn"], background=CLR["btn"],
                              foreground=CLR["text"], arrowcolor=CLR["text"],
                              selectbackground=CLR["accent"],
                              selectforeground=CLR["text"], borderwidth=1,
                              relief="flat")
    style.map("TCombobox",    fieldbackground=[("readonly", CLR["btn"])],
                              foreground=[("readonly", CLR["text"])])

    S("TButton",              background=CLR["btn"], foreground=CLR["text"],
                              borderwidth=1, relief="flat", padding=(10, 5),
                              font="Segoe\\ UI 10")
    style.map("TButton",      background=[("active", CLR["btn_hover"])],
                              foreground=[("active", CLR["text"])])

    S("Accent.TButton",       background=CLR["accent"], foreground="#ffffff",
                              borderwidth=0, relief="flat", padding=(14, 6),
                              font="Segoe\\ UI 10 bold")
    style.map("Accent.TButton", background=[("active", CLR["accent_hover"]),
                                             ("disabled", CLR["btn"])],
                              foreground=[("disabled", CLR["subtext"])])

    S("Danger.TButton",       background="#453030", foreground=CLR["red"],
                              borderwidth=0, relief="flat", padding=(10, 5))
    style.map("Danger.TButton", background=[("active", "#5a3030")])

    S("TSeparator",           background=CLR["border"])

    S("TScrollbar",           background=CLR["btn"], troughcolor=CLR["bg"],
                              borderwidth=0, arrowcolor=CLR["subtext"],
                              relief="flat")
    style.map("TScrollbar",   background=[("active", CLR["btn_hover"])])

    S("TCheckbutton",         background=CLR["panel"], foreground=CLR["text"],
                              indicatorcolor=CLR["check_off"],
                              indicatorrelief="flat", focuscolor="")
    style.map("TCheckbutton", indicatorcolor=[("selected", CLR["check_on"])],
                              background=[("active", CLR["panel"])])

    S("TProgressbar",         troughcolor=CLR["panel"], background=CLR["accent"],
                              borderwidth=0, thickness=4)

    # ── State vars ────────────────────────────────────────────────────────────
    file_var    = tk.StringVar(value=available[0] if available else "")
    no_cache_var = tk.BooleanVar(value=False)
    step_vars   = {key: tk.BooleanVar(value=(key in _DEFAULT_ON))
                   for key, *_ in STEPS}
    running     = threading.Event()

    # ── Layout ────────────────────────────────────────────────────────────────
    # Left sidebar (controls) + right log pane
    outer = ttk.Frame(root)
    outer.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
    outer.columnconfigure(0, weight=0, minsize=310)
    outer.columnconfigure(1, weight=1)
    outer.rowconfigure(0, weight=1)

    sidebar = ttk.Frame(outer, style="Panel.TFrame")
    sidebar.grid(row=0, column=0, sticky="nsew")

    log_pane = tk.Frame(outer, bg=CLR["log_bg"])
    log_pane.grid(row=0, column=1, sticky="nsew")

    # ── Sidebar: title ────────────────────────────────────────────────────────
    title_frame = ttk.Frame(sidebar, style="Panel.TFrame")
    title_frame.pack(fill=tk.X, padx=16, pady=(18, 6))
    ttk.Label(title_frame, text="TES4  →  TES5", style="Head.TLabel",
              background=CLR["panel"],
              font=("Segoe UI", 15, "bold"), foreground=CLR["accent"]).pack(anchor="w")
    ttk.Label(title_frame, text="Oblivion to Skyrim SE converter",
              style="PanelSub.TLabel").pack(anchor="w")

    ttk.Separator(sidebar, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=6)

    # ── Sidebar: file selector ────────────────────────────────────────────────
    f_frame = ttk.Frame(sidebar, style="Panel.TFrame")
    f_frame.pack(fill=tk.X, padx=16, pady=(6, 2))
    ttk.Label(f_frame, text="Plugin File", style="PanelSub.TLabel").pack(anchor="w")
    file_combo = ttk.Combobox(f_frame, textvariable=file_var, values=available,
                               state="readonly", width=30)
    file_combo.pack(fill=tk.X, pady=(3, 0))

    # No-cache checkbox
    nc_frame = ttk.Frame(sidebar, style="Panel.TFrame")
    nc_frame.pack(fill=tk.X, padx=16, pady=(6, 2))
    ttk.Checkbutton(nc_frame, text="Force re-export (no cache)",
                    variable=no_cache_var).pack(anchor="w")

    ttk.Separator(sidebar, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=10)

    # ── Sidebar: pipeline steps ───────────────────────────────────────────────
    steps_header = ttk.Frame(sidebar, style="Panel.TFrame")
    steps_header.pack(fill=tk.X, padx=16, pady=(0, 4))
    ttk.Label(steps_header, text="Pipeline Steps", style="PanelSub.TLabel").pack(side=tk.LEFT)

    def _select_all_steps():
        for v in step_vars.values():
            v.set(True)
        _update_run_btn()

    def _select_default_steps():
        for key, v in step_vars.items():
            v.set(key in _DEFAULT_ON)
        _update_run_btn()

    sa_btn = ttk.Button(steps_header, text="All",     command=_select_all_steps,    width=4)
    sd_btn = ttk.Button(steps_header, text="Default", command=_select_default_steps, width=7)
    sd_btn.pack(side=tk.RIGHT, padx=(2, 0))
    sa_btn.pack(side=tk.RIGHT, padx=(2, 0))

    def _update_run_btn(*_):
        has = any(v.get() for v in step_vars.values())
        run_btn.configure(state="normal" if has and not running.is_set() else "disabled")

    for key, label, tip in STEPS:
        row = ttk.Frame(sidebar, style="Panel.TFrame")
        row.pack(fill=tk.X, padx=16, pady=1)
        cb = ttk.Checkbutton(row, text=label, variable=step_vars[key],
                              command=_update_run_btn)
        cb.pack(side=tk.LEFT)
        ttk.Label(row, text=tip, style="PanelSub.TLabel").pack(side=tk.LEFT, padx=(8, 0))

    ttk.Separator(sidebar, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=10)

    # ── Sidebar: action buttons ───────────────────────────────────────────────
    btn_frame = ttk.Frame(sidebar, style="Panel.TFrame")
    btn_frame.pack(fill=tk.X, padx=16, pady=(0, 8))

    run_btn = ttk.Button(btn_frame, text="▶   Run Selected Steps",
                         style="Accent.TButton", command=lambda: _run_clicked())
    run_btn.pack(fill=tk.X, pady=(0, 6))

    row2 = ttk.Frame(btn_frame, style="Panel.TFrame")
    row2.pack(fill=tk.X)
    row2.columnconfigure(0, weight=1)
    row2.columnconfigure(1, weight=1)

    test_btn = ttk.Button(row2, text="Run Tests",
                          command=lambda: _run_single("test"))
    test_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

    clear_btn = ttk.Button(row2, text="Clear Log", command=lambda: _clear_log(),
                           style="Danger.TButton")
    clear_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))

    # Progress bar (hidden until running)
    prog_var = tk.DoubleVar(value=0)
    prog_bar = ttk.Progressbar(sidebar, variable=prog_var, mode="indeterminate",
                                length=200)
    prog_bar.pack(fill=tk.X, padx=16, pady=(4, 0))
    prog_bar.pack_forget()  # hidden initially

    # status label at bottom of sidebar
    status_var = tk.StringVar(value="Ready")
    status_lbl = ttk.Label(sidebar, textvariable=status_var,
                           style="PanelSub.TLabel")
    status_lbl.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(0, 12))

    # ── Log pane ──────────────────────────────────────────────────────────────
    log_header = tk.Frame(log_pane, bg=CLR["panel"], height=36)
    log_header.pack(fill=tk.X)
    log_header.pack_propagate(False)

    tk.Label(log_header, text="Output Log", bg=CLR["panel"],
             fg=CLR["subtext"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=14,
                                                            pady=9)

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

    # Coloured text tags
    log_text.tag_configure("info",  foreground=CLR["log_info"])
    log_text.tag_configure("ok",    foreground=CLR["log_ok"])
    log_text.tag_configure("err",   foreground=CLR["log_err"])
    log_text.tag_configure("warn",  foreground=CLR["log_warn"])
    log_text.tag_configure("head",  foreground=CLR["accent"],
                                    font=("Consolas", 9, "bold"))
    log_text.tag_configure("dim",   foreground=CLR["subtext"])
    log_text.tag_configure("cmd",   foreground=CLR["blue"],
                                    font=("Consolas", 9, "bold"))

    def _classify(line: str) -> str:
        l = line.lower()
        if line.startswith("===") or line.startswith("  Phase"):
            return "head"
        if "error" in l:
            return "err"
        if "warning" in l or "warn" in l:
            return "warn"
        if "complete" in l or "done" in l or "ok" in l or "success" in l:
            return "ok"
        if line.startswith("Running:"):
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

    def _set_running(state: bool):
        running.set() if state else running.clear()
        run_btn.configure(state="disabled" if state else "normal")
        test_btn.configure(state="disabled" if state else "normal")
        file_combo.configure(state="disabled" if state else "readonly")
        if state:
            prog_bar.pack(fill=tk.X, padx=16, pady=(4, 0))
            prog_bar.start(12)
            status_var.set("Running…")
        else:
            prog_bar.stop()
            prog_bar.pack_forget()
            status_var.set("Ready")
        _update_run_btn()

    # ── Actions ───────────────────────────────────────────────────────────────
    def _run_clicked():
        if running.is_set():
            return
        fname  = file_var.get()
        if not fname:
            from tkinter import messagebox
            messagebox.showwarning("No File", "Select a plugin file first.", parent=root)
            return
        steps = [key for key, *_ in STEPS if step_vars[key].get()]
        if not steps:
            from tkinter import messagebox
            messagebox.showwarning("No Steps", "Select at least one pipeline step.", parent=root)
            return
        _clear_log()
        _log(f"File: {fname}")
        _log(f"Steps: {', '.join(steps)}")
        _log("")

        def _worker():
            _set_running(True)
            try:
                if len(steps) == 1:
                    flag = _STEP_FLAGS[steps[0]]
                    needs_file = steps[0] in _STEPS_NEED_FILE
                    cmd = [sys.executable, str(SCRIPT_DIR / "convert.py"), flag]
                    if needs_file:
                        cmd += ["-f", fname]
                    if no_cache_var.get() and steps[0] == "export":
                        cmd.append("--no-cache")
                    root.after(0, _log, f"Running: {' '.join(cmd)}")
                    ret = _run_process(cmd, lambda m: root.after(0, _log, m))
                else:
                    # Multiple steps: run one convert.py call for standard steps,
                    # then separately for special ones.
                    std    = [s for s in steps if s in ("export","import","assets","lod")]
                    extras = [s for s in steps if s not in ("export","import","assets","lod")]
                    ret = 0
                    if std:
                        cmd = [sys.executable, str(SCRIPT_DIR / "convert.py"),
                               "-f", fname]
                        if no_cache_var.get():
                            cmd.append("--no-cache")
                        # Only add --*-only flags for a known subset — omitting all
                        # flags means the default pipeline, but we may have a custom
                        # subset. Build explicit args:
                        # We map subset to explicit flags to avoid triggering unselected
                        # default steps (assets is default; if only export+import wanted,
                        # we must pass those two --only flags... which would only work one
                        # at a time).  Solution: if subset == full default, omit flags;
                        # otherwise run each individually in sequence.
                        default_set = {"export", "import", "assets"}
                        if set(std) == default_set:
                            # full default, no flags needed
                            root.after(0, _log, f"Running: {' '.join(cmd)}")
                            ret = _run_process(cmd, lambda m: root.after(0, _log, m))
                        else:
                            for step in std:
                                c = cmd + [_STEP_FLAGS[step]]
                                root.after(0, _log, f"Running: {' '.join(c)}")
                                r = _run_process(c, lambda m: root.after(0, _log, m))
                                if r != 0:
                                    ret = r
                    for step in extras:
                        flag = _STEP_FLAGS[step]
                        needs_file = step in _STEPS_NEED_FILE
                        cmd = [sys.executable, str(SCRIPT_DIR / "convert.py"), flag]
                        if needs_file:
                            cmd += ["-f", fname]
                        root.after(0, _log, f"Running: {' '.join(cmd)}")
                        r = _run_process(cmd, lambda m: root.after(0, _log, m))
                        if r != 0:
                            ret = r
                root.after(0, _log, "")
                root.after(0, _log, "✓ DONE" if ret == 0 else "✗ FAILED")
            finally:
                root.after(0, _set_running, False)

        threading.Thread(target=_worker, daemon=True).start()

    def _run_single(mode: str):
        """Run a non-file task (tests)."""
        if running.is_set():
            return
        _clear_log()
        if mode == "test":
            cmd = [sys.executable, str(SCRIPT_DIR / "convert.py"), "--test"]
            root.after(0, _log, f"Running: {' '.join(cmd)}")
        else:
            return

        def _worker():
            _set_running(True)
            try:
                ret = _run_process(cmd, lambda m: root.after(0, _log, m))
                root.after(0, _log, "")
                root.after(0, _log, "✓ DONE" if ret == 0 else "✗ FAILED")
            finally:
                root.after(0, _set_running, False)

        threading.Thread(target=_worker, daemon=True).start()

    _update_run_btn()
    root.mainloop()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="TES4→TES5 Converter GUI")
    parser.add_argument("--cli", action="store_true",
                        help="Headless mode: forward args to convert.py")
    parser.add_argument("-f", "--file", help="Plugin file to process")
    parser.add_argument("--no-cache", action="store_true")
    args, extra = parser.parse_known_args()

    if args.cli:
        cmd = [sys.executable, str(SCRIPT_DIR / "convert.py")]
        if args.file:
            cmd += ["-f", args.file]
        if args.no_cache:
            cmd.append("--no-cache")
        cmd.extend(extra)
        import subprocess
        ret = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
        return ret.returncode

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())

