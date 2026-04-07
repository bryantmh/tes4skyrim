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
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "conversion_config.json"

# ── Pipeline steps ─────────────────────────────────────────────────────────
# (key, cli_flag, label, description, default_on, needs_file)
STEPS = [
    ("export",             "--export-only",        "1. Export",
     "Parse TES4 binary -> text cache",          True,  True),
    ("import_",            "--import-only",        "2. Import",
     "Build TES5 ESM/ESP from text cache",       True,  True),
    ("extract",            "--extract-only",       "3. Extract",
     "Pull assets from BSA archives",            True,  True),
    ("assets",             "--assets-only",        "4. Assets",
     "Convert NIFs/SPTs, copy textures",         True,  True),
    ("lod",                "--lod-only",           "5. LOD",
     "Generate LOD meshes (slow)",               False, True),
    ("modify_body_meshes", "--modify-body-meshes", "6. Body Meshes",
     "Add greaves partition to body NIFs",       False, False),
]

_DEFAULT_ON = {k for k, *_ in STEPS if _[3]}  # keys where default_on=True

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
}


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


def _run_process(cmd, log_cb, env=None):
    """Run cmd as subprocess, streaming each line to log_cb immediately."""
    try:
        full_env = os.environ.copy()
        full_env["PYTHONUNBUFFERED"] = "1"
        if env:
            full_env.update(env)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(SCRIPT_DIR),
            env=full_env,
            bufsize=1,          # line-buffered
        )
        while True:
            line = proc.stdout.readline()
            if not line:
                break
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
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        print("ERROR: tkinter not available")
        return 1

    # ── Load / init config ────────────────────────────────────────────────────
    cfg = load_config()
    tes4_path    = cfg.get("tes4DataPath", "") or _find_game_path("oblivion")
    output_path  = cfg.get("outputDir", "")  or str(SCRIPT_DIR / "output")

    # ── Root window ───────────────────────────────────────────────────────────
    root = tk.Tk()
    root.title("TES4 -> TES5 Converter")
    root.geometry("1060x800")
    root.minsize(860, 620)
    root.configure(bg=CLR["bg"])
    root.option_add("*Background", CLR["bg"])
    root.option_add("*Foreground", CLR["text"])

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
    output_var  = tk.StringVar(value=output_path)
    file_var    = tk.StringVar()
    step_vars   = {key: tk.BooleanVar(value=(key in _DEFAULT_ON))
                   for key, *_ in STEPS}
    running     = threading.Event()

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
        updated["outputDir"]    = output_var.get()
        save_config(updated)

    # Also save on focusout of entries
    tes4_var.trace_add("write", lambda *_: None)  # live binding via on_change

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

    ttk.Button(sh, text="Default", command=_set_default, width=7).pack(
        side=tk.RIGHT, padx=(2, 0))
    ttk.Button(sh, text="All", command=_set_all, width=4).pack(
        side=tk.RIGHT, padx=(2, 0))

    def _update_run_btn(*_):
        has = any(v.get() for v in step_vars.values())
        st  = "normal" if has and not running.is_set() else "disabled"
        run_btn.configure(state=st)

    for key, flag, label, tip, default_on, needs_file in STEPS:
        row = ttk.Frame(sidebar, style="Panel.TFrame")
        row.pack(fill=tk.X, padx=14, pady=1)
        ttk.Checkbutton(row, text=label, variable=step_vars[key],
                         command=_update_run_btn).pack(side=tk.LEFT)
        ttk.Label(row, text=tip, style="PanelSub.TLabel").pack(
            side=tk.LEFT, padx=(6, 0))

    _sep()

    # ── Action buttons ────────────────────────────────────────────────────────
    bf = ttk.Frame(sidebar, style="Panel.TFrame")
    bf.pack(fill=tk.X, padx=14, pady=(0, 6))

    run_btn = ttk.Button(bf, text="  Run Selected Steps",
                         style="Accent.TButton", command=lambda: _run_clicked())
    run_btn.pack(fill=tk.X, pady=(0, 6))

    clear_btn = ttk.Button(bf, text="Clear Log", command=lambda: _clear_log(),
                           style="Danger.TButton")
    clear_btn.pack(fill=tk.X)

    # Progress bar + status
    prog_bar = ttk.Progressbar(sidebar, mode="indeterminate", length=200)
    prog_bar.pack(fill=tk.X, padx=14, pady=(4, 0))
    prog_bar.pack_forget()

    status_var = tk.StringVar(value="Ready")
    ttk.Label(sidebar, textvariable=status_var, style="PanelSub.TLabel").pack(
        side=tk.BOTTOM, fill=tk.X, padx=14, pady=(0, 10))

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
        if "error" in l:
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

    def _set_running(state: bool):
        running.set() if state else running.clear()
        run_btn.configure(state="disabled" if state else "normal")
        file_combo.configure(state="disabled" if state else "readonly")
        if state:
            prog_bar.pack(fill=tk.X, padx=14, pady=(4, 0))
            prog_bar.start(12)
            status_var.set("Running...")
        else:
            prog_bar.stop()
            prog_bar.pack_forget()
            status_var.set("Ready")
        _update_run_btn()

    # ── Run logic ─────────────────────────────────────────────────────────────
    def _build_cmd(step_key: str, fname: str, out_dir: str) -> list:
        """Build the convert.py command for a single step."""
        _, flag, _, _, _, needs_file = next(
            s for s in STEPS if s[0] == step_key)
        cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py"), flag]
        if needs_file and fname:
            cmd += ["-f", fname]
        if out_dir:
            cmd += ["--output-dir", out_dir]
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
        _clear_log()
        _log(f"File: {fname or '(none)'}")
        _log(f"Steps: {', '.join(steps)}")
        _log(f"Output: {out_dir}")
        _log("")

        def _worker():
            _set_running(True)
            try:
                default_set = {k for k, *rest in STEPS if rest[3]}
                active_set  = set(steps)
                # If selection == default set and a file is specified,
                # run the pipeline without --*-only flags (uses convert.py defaults)
                if active_set == default_set and fname:
                    cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py"),
                           "-f", fname]
                    if out_dir:
                        cmd += ["--output-dir", out_dir]
                    root.after(0, _log, f"Running: {' '.join(cmd)}")
                    ret = _run_process(cmd, lambda m: root.after(0, _log, m))
                else:
                    ret = 0
                    for step in steps:
                        cmd = _build_cmd(step, fname, out_dir)
                        root.after(0, _log, f"Running: {' '.join(cmd)}")
                        r = _run_process(cmd, lambda m: root.after(0, _log, m))
                        if r != 0:
                            ret = r
                root.after(0, _log, "")
                root.after(0, _log,
                           "  DONE" if ret == 0 else "  FAILED")
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
    parser = argparse.ArgumentParser(description="TES4->TES5 Converter GUI")
    parser.add_argument("--cli", action="store_true",
                        help="Headless: forward remaining args to convert.py")
    args, extra = parser.parse_known_args()

    if args.cli:
        cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py")] + extra
        ret = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
        return ret.returncode

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
