"""
Settings ▸ Morrowind source: the GUI half of the Morrowind master switch.

The export stage reads the chosen set from `conversion_config.json`, so the
radio group saves on every change and nothing else has to be plumbed. Choosing
Morroblivion also builds the compatibility patch when it is missing, because
that mode refuses every conversion without it; choosing it again while it is
already selected rebuilds the patch.

See: docs/commentary/tes4_export_morrowind.md#masters
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from asset_convert.sources import source_registry
from core.gui.menubar_behavior import enable_tips
from tes4_export.export_morrowind import MORROWIND_SOURCE_KEY, SOURCE_MORROBLIVION, SOURCE_VANILLA, morroblivion_exports
from tes4_export.morrowind_patch import PATCH_NAME, PATCH_SOURCES, build_patch, patch_exists, source_paths

#: Source set -> its menu label, in menu order.
_LABELS = (
    (SOURCE_VANILLA, "Vanilla  (Morrowind + Tribunal + Bloodmoon)"),
    (SOURCE_MORROBLIVION, "Morroblivion + patch"),
)

#: Title of every dialog the patch build raises.
_TITLE = "Morroblivion compatibility patch"

#: Menu tip on the Morroblivion entry.
MORROBLIVION_TIP = (
    f"Convert Morrowind plugins against Morroblivion and {PATCH_NAME}. "
    "Builds the patch if it is missing (convert Morrowind_ob.esm first); "
    "choose it again while selected to rebuild the patch")


def source_default(cfg: dict) -> str:
    """The configured source set; anything unrecognised reads as vanilla."""
    value = str(cfg.get(MORROWIND_SOURCE_KEY, "")).strip().lower()
    return value if value in dict(_LABELS) else SOURCE_VANILLA


def add_source_menu(settings_menu, menu_opts: dict, cfg: dict,
                    load_config, save_config, export_dir,
                    out_root) -> tk.StringVar:
    """Add Settings ▸ Morrowind source as a radio cascade saved on change.

    `out_root` is called at click time, not read now: the user can retarget the
    output directory after the menu is built.
    """
    var = tk.StringVar(value=source_default(cfg))
    chosen = {"mode": var.get()}

    def _save():
        """Persist the chosen set and remember it as the current one."""
        chosen["mode"] = var.get()
        updated = load_config()
        updated[MORROWIND_SOURCE_KEY] = var.get()
        save_config(updated)

    def _pick_morroblivion():
        """Switch, building the patch first when it is missing or asked for."""
        was = chosen["mode"]
        if patch_exists(str(export_dir)):
            if was != SOURCE_MORROBLIVION:
                _save()
                return
            if not messagebox.askyesno(_TITLE, f"Rebuild {PATCH_NAME}?"):
                return
        if build_patch_dialog(settings_menu, str(export_dir), out_root()):
            _save()
        else:
            var.set(was)

    menu = tk.Menu(settings_menu, **menu_opts)
    enable_tips(menu)
    menu.add_radiobutton(label=dict(_LABELS)[SOURCE_VANILLA],
                         value=SOURCE_VANILLA, variable=var, command=_save)
    menu.add_radiobutton(label=dict(_LABELS)[SOURCE_MORROBLIVION],
                         value=SOURCE_MORROBLIVION, variable=var,
                         command=_pick_morroblivion)
    menu.entry_tips[menu.index("end")] = MORROBLIVION_TIP
    settings_menu.add_cascade(label="Morrowind source", menu=menu)
    return var


def _morrowind_data_dir(export_dir: str) -> str:
    """The Morrowind Data Files folder: the registered install, else asked for.

    Returns "" when the user cancels or picks a folder without the masters.
    """
    known = source_registry.directory_for(export_dir, PATCH_SOURCES[0])
    if known and not source_paths(known, PATCH_SOURCES)[1]:
        return known
    data_dir = filedialog.askdirectory(
        title="Select your Morrowind 'Data Files' folder")
    if not data_dir:
        return ""
    _, missing = source_paths(data_dir, PATCH_SOURCES)
    if missing:
        messagebox.showerror(
            _TITLE,
            "That folder is not a Morrowind Data Files directory.\n\n"
            f"Looked in:\n{data_dir}\n\nMissing:\n  " + "\n  ".join(missing))
        return ""
    return data_dir


def build_patch_dialog(parent, export_dir: str, out_root) -> bool:
    """Start building the patch in a window; False when it could not start.

    Morroblivion has to be converted first -- the patch holds what it does NOT
    supply, so without it there is no gap to measure.
    """
    exports = morroblivion_exports(export_dir)
    if not exports:
        messagebox.showerror(
            _TITLE,
            "No converted Morroblivion plugin was found.\n\n"
            f"{PATCH_NAME} holds the objects Morroblivion does NOT convert, so "
            "convert Morrowind_ob.esm first, then choose Morroblivion again.")
        return False
    data_dir = _morrowind_data_dir(export_dir)
    if not data_dir:
        return False
    _run_build_window(parent, data_dir, export_dir, exports, out_root)
    return True


def _run_build_window(parent, data_dir: str, export_dir: str,
                      exports: list, out_root) -> None:
    """Run the build on a worker thread, streaming progress into a window."""
    win = tk.Toplevel(parent)
    win.title("Building compatibility patch")
    win.geometry("620x300")
    text = tk.Text(win, wrap="word", state="disabled")
    text.pack(fill="both", expand=True, padx=8, pady=8)
    log = _line_writer(win, text)

    def _work():
        """Build, then report the outcome in the same window."""
        try:
            result = build_patch(data_dir, export_dir, exports, progress=log,
                                 out_root=out_root)
        except Exception as exc:
            log(f"FAILED: {exc}")
            return
        if not result["ok"]:
            log("")
            for line in result["error"].splitlines():
                log(line)
            return
        log("")
        log(f"Done in {result['seconds']:.1f}s -- {result['records']} records, "
            f"{result['assets']} assets.")
        log(f"Plugin: {result['plugin']}")
        log(f"{PATCH_NAME} is now a master of every Morroblivion-mode "
            f"conversion.")

    log(f"Source: {data_dir}")
    log(f"Against: {', '.join(exports)}")
    threading.Thread(target=_work, daemon=True).start()


def _line_writer(win, text):
    """A callable appending one line to `text`, safe to call off the UI thread."""
    def _log(line=""):
        """Queue one progress line onto the UI thread."""
        win.after(0, _append, str(line))

    def _append(line: str):
        """Append one line and scroll to it; runs on the UI thread."""
        text.configure(state="normal")
        text.insert("end", line + os.linesep)
        text.see("end")
        text.configure(state="disabled")

    return _log
