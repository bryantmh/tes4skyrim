"""The dark top menu bar: Settings, Converted, Mods, Tools, About, Updates.

Windows renders a NATIVE (white) bar for `root.configure(menu=...)` and ignores
tk colors on it, so the bar is built from dark Menubuttons whose dropdown
popups -- which DO honour color options -- are tk.Menu instances.

`build_menubar` must be wired AFTER the features its entries drive: they reach
`app.info`, `app.commit_plugin`, `app.run_global_action` and friends through
the carrier, so those have to be bound first.

Two entries are deliberately not automatic. Converted rebuilds on `<Map>`,
the menu's own "about to be shown" signal, because output/ gains entries as
conversions finish and a menu built once at startup goes stale within the
session. Check for Updates is a network call, so a GUI that phoned home on
launch would stall startup and do it unasked.

See: docs/reference/pipeline.md#configuration
"""

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

import version as version_info
from core.collision_options import WINDING_FIX_DEFAULT_PLUGINS
from core.gui import journal as gui_journal
from core.gui import mods as gui_mods
from core.gui.config import (
    CLR,
    EXPORT_DIR,
    GLOBAL_ACTIONS,
    PACK_DEFAULT_CONFIG_KEY,
    PACKING_STEPS,
    REPO_ROOT,
    LOD_DETAIL_CONFIG_KEY,
    WINDING_AUTO,
    WINDING_CONFIG_KEY,
    WINDING_OFF,
    WINDING_ON,
    load_config,
    lod_detail_labels,
    save_config,
    save_setting,
    scan_converted,
)
from core.gui.menubar_behavior import (add_tipped_command, enable_hover_switch,
                                       enable_tips)
from core.gui.morrowind import add_source_menu
from core.gui.selection import runnable
from core.gui.widgets import open_url
from core.subprocess_flags import POPEN_FLAGS
from core.worker_budget import worker_count

DISCORD_URL = "https://discord.gg/NTkCDfYUru"
YOUTUBE_URL = "https://www.youtube.com/@bryanthinton"


# ---------------------------------------------------------------------------
#  Settings
# ---------------------------------------------------------------------------

def _add_workers_menu(app, settings_menu, menu_opts) -> None:
    """Settings > Workers: a radio group bound to `app.workers_var`."""
    workers_menu = tk.Menu(settings_menu, **menu_opts)
    for n in range(1, app.cpu_max + 1):
        label = f"{n}  (default)" if n == worker_count() else str(n)
        workers_menu.add_radiobutton(
            label=label, value=n, variable=app.workers_var,
            command=app.save_dirs)
    settings_menu.add_cascade(label=f"Workers  (max {app.cpu_max})",
                              menu=workers_menu)


def _add_cache_download(app, settings_menu) -> None:
    """Settings > Download navmesh cache, saved immediately.

    ON by default because the prebuilt cache turns the slowest import stage
    from minutes into seconds; it exists for metered or offline connections.
    Turning it off does NOT disable the cache itself: a zip dropped in
    navmesh_cache/ is still installed, and an existing cache still used.
    """
    def _changed():
        """Persist the new state."""
        save_setting("navmeshCacheDownload", bool(app.cache_dl_var.get()))

    settings_menu.add_checkbutton(
        label="Download navmesh cache", variable=app.cache_dl_var,
        onvalue=True, offvalue=False, command=_changed)


def _add_pack_default(app, settings_menu) -> None:
    """Settings > Pack BSAs / Mod Zip by default.

    Applied to the live checkboxes as soon as it is toggled, so the effect is
    visible immediately rather than only after the next launch. It moves only
    the two packing boxes; every other step keeps whatever the user set.
    """
    def _changed():
        """Persist the setting and retick the two packing steps."""
        on = bool(app.pack_default_var.get())
        save_setting(PACK_DEFAULT_CONFIG_KEY, on)
        for key in PACKING_STEPS:
            app.step_vars[key].set(on and runnable(app, key))
        app.update_run_btn()

    settings_menu.add_checkbutton(
        label="Pack BSAs / Mod Zip by default", variable=app.pack_default_var,
        onvalue=True, offvalue=False, command=_changed)


def _add_winding_menu(app, settings_menu, menu_opts) -> None:
    """Settings > Infer collision winding: Automatic / Always on / Always off.

    Controls ONLY the inferred steps; the authored-normal repair always runs
    and has no switch. A radio group rather than a checkbox because "follow the
    per-plugin default" is a third answer, not the absence of one.

    See: docs/commentary/asset_convert_collision.md#inverted-collision-winding-i-fall
    """
    def _changed():
        """Persist the chosen mode."""
        save_setting(WINDING_CONFIG_KEY, app.winding_mode_var.get())

    winding_menu = tk.Menu(settings_menu, **menu_opts)
    auto_plugins = ", ".join(sorted(WINDING_FIX_DEFAULT_PLUGINS))
    for mode, label in ((WINDING_AUTO, f"Automatic  (on for {auto_plugins})"),
                        (WINDING_ON, "Always on"),
                        (WINDING_OFF, "Always off")):
        winding_menu.add_radiobutton(
            label=label, value=mode, variable=app.winding_mode_var,
            command=_changed)
    settings_menu.add_cascade(label="Infer collision winding",
                              menu=winding_menu)


def _add_lod_detail_menu(app, settings_menu, menu_opts) -> None:
    """Settings > Distant LOD detail: a radio group over the detail presets.

    Each label carries its measured triangle multiplier and the LOD size it
    lands on, so the choice is a number rather than an adjective. Applies on the
    NEXT LOD bake -- the meshes are decimated at generation time.

    See: docs/commentary/asset_convert_terrain.md#object-lod-detail-presets
    """
    def _changed():
        """Persist the chosen preset index."""
        save_setting(LOD_DETAIL_CONFIG_KEY, app.lod_detail_var.get())

    lod_menu = tk.Menu(settings_menu, **menu_opts)
    for idx, label in enumerate(lod_detail_labels()):
        lod_menu.add_radiobutton(label=label, value=idx,
                                 variable=app.lod_detail_var, command=_changed)
    settings_menu.add_cascade(label="Distant LOD detail", menu=lod_menu)


def _build_settings_menu(app, menubutton, menu_opts) -> None:
    """Settings: workers, cache download, packing, winding, LOD and Morrowind."""
    settings_menu = menubutton("Settings")
    _add_workers_menu(app, settings_menu, menu_opts)
    _add_cache_download(app, settings_menu)
    _add_pack_default(app, settings_menu)
    _add_winding_menu(app, settings_menu, menu_opts)
    _add_lod_detail_menu(app, settings_menu, menu_opts)
    add_source_menu(settings_menu, menu_opts, app.cfg, load_config,
                    save_config, EXPORT_DIR, app.out_root)


# ---------------------------------------------------------------------------
#  Converted
# ---------------------------------------------------------------------------

def _select_converted(app, name: str) -> None:
    """Point the GUI at an already-converted plugin and plan its re-run.

    The source lookup comes FIRST: a plugin imported from a mod archive has no
    data directory at all, and plugins do not share one. Picking from Converted
    is an explicit re-plan request, so the auto-apply guard is cleared first.
    """
    if not app.select_source_for_plugin(name):
        app.info("Source Not Found",
                 f"{name!r} is in the output folder, but no source has it.\n\n"
                 "Add the folder it came from with the + button beside "
                 "Source, then pick it again.")
        return
    app.plan_applied.discard(name)
    app.set_default()
    app.commit_plugin(name)
    app.file_combo.selection_clear()


def _build_converted_menu(app, menubutton, _menu_opts) -> None:
    """Converted: re-select a plugin already present in output/."""
    app.converted_menu = menubutton("Converted")

    def _rebuild():
        """Re-read output/ so the list is current every time it opens."""
        app.converted_menu.delete(0, tk.END)
        names = scan_converted(app.output_var.get().strip())
        if not names:
            app.converted_menu.add_command(label="(nothing converted yet)",
                                           state="disabled")
            return
        for name in names:
            app.converted_menu.add_command(
                label=name, command=lambda n=name: _select_converted(app, n))

    app.converted_menu.bind("<Map>", lambda _e: _rebuild())
    _rebuild()


# ---------------------------------------------------------------------------
#  Tools
# ---------------------------------------------------------------------------

def _probe_phases(preflight) -> tuple:
    """(ok labels, [(label, [missing])]) across every pipeline phase."""
    ok, bad = [], []
    for phase, label in preflight.PHASE_LABELS.items():
        try:
            missing = preflight.check_phase(phase)
        except Exception as exc:
            bad.append((label, [f"check failed: {exc}"]))
            continue
        if missing:
            bad.append((label, [f"{m.name} — {m.purpose}" for m in missing]))
        else:
            ok.append(label)
    return ok, bad


def _dependency_report(preflight, ok, bad) -> str:
    """The Check Dependencies message body.

    A Python version mismatch leads but does not turn the report into a
    failure. Missing tools are deduplicated: one absent tool usually blocks
    several phases, and listing it per phase buries how few are really absent.
    """
    lines = []
    warn = preflight.python_version_warning()
    if warn:
        lines.extend([warn.strip(), ""])
    if not bad:
        lines.extend(["All phases have what they need.", "",
                      f"Checked: {', '.join(ok)}"])
        return "\n".join(lines)

    seen, items = set(), []
    for _label, missing in bad:
        for m in missing:
            if m not in seen:
                seen.add(m)
                items.append(m)

    lines.append(f"{len(bad)} phase(s) are missing dependencies:")
    lines.append("")
    for label, missing in bad:
        named = ', '.join(m.split(' — ')[0] for m in missing)
        lines.append(f"  {label}: {named}")
    lines.extend(["", "Missing:"])
    lines.extend(f"  • {m}" for m in items)
    lines.extend(["", "See the Requirements section of README.md to install "
                  "these."])
    if ok:
        lines.extend(["", f"Ready: {', '.join(ok)}"])
    return "\n".join(lines)


def _check_dependencies(app) -> None:
    """Report every phase's unmet dependencies without running anything.

    Called IN-PROCESS rather than shelling out to `python preflight.py`: under
    gui.pyw a spawn can allocate a console window, and the GUI is one window.
    preflight's probes are plain imports and file checks, so this is fast
    enough to run inline.
    """
    try:
        import preflight
    except Exception as exc:
        app.info("Check Dependencies",
                 f"Could not load the dependency checker:\n\n{exc}")
        return
    ok, bad = _probe_phases(preflight)
    app.info("Check Dependencies", _dependency_report(preflight, ok, bad))


def _open_folder(app, path: str, what: str) -> None:
    """Reveal `path` in the system file manager."""
    if not path or not os.path.isdir(path):
        app.info(f"Open {what}",
                 f"{what} does not exist yet:\n\n{path or '(not set)'}\n\n"
                 "Run a conversion first.")
        return
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path], **POPEN_FLAGS)
        else:
            subprocess.Popen(["xdg-open", path], **POPEN_FLAGS)
    except OSError as exc:
        app.info(f"Open {what}", f"Could not open:\n\n{path}\n\n{exc}")


def _build_tools_menu(app, menubutton, _menu_opts) -> None:
    """Tools: dependency check, global actions, journal patch, folder shortcuts.

    The global entries are late-bound: `app.run_global_action` is wired with
    the rest of the run logic, so the lambda resolves it at click time.
    Rotation is worthless if nobody knows the log files are there, hence the
    Logs shortcut.
    """
    tools_menu = menubutton("Tools")
    enable_tips(tools_menu)
    add_tipped_command(
        tools_menu, "Check Dependencies", lambda: _check_dependencies(app),
        "List what each pipeline phase needs that is missing -- Python "
        "packages, bundled tools, game installs -- without running anything")
    tools_menu.add_separator()
    for gkey, glabel, gtip, _gshort, _grow in GLOBAL_ACTIONS:
        add_tipped_command(tools_menu, glabel,
                           lambda k=gkey: app.run_global_action(k), gtip)
    tools_menu.add_separator()
    add_tipped_command(tools_menu, gui_journal.TITLE,
                       lambda: gui_journal.run_patch(app), gui_journal.TIP)
    tools_menu.add_separator()
    add_tipped_command(
        tools_menu, "Open Output Folder",
        lambda: _open_folder(app, app.output_var.get().strip(),
                             "Output folder"),
        "Open the folder converted plugins and finished mods are written to")
    add_tipped_command(
        tools_menu, "Open Logs Folder",
        lambda: _open_folder(app, str(REPO_ROOT / "logs"), "Logs folder"),
        "Open the folder holding each run's log files")


# ---------------------------------------------------------------------------
#  About and Check for Updates
# ---------------------------------------------------------------------------

def _flat_menubutton(parent, text: str):
    """A bar entry that is a plain click target, not a dropdown."""
    mb = tk.Menubutton(parent, text=text,
                       bg=CLR["panel"], fg=CLR["text"],
                       activebackground=CLR["btn_hover"],
                       activeforeground=CLR["text"],
                       disabledforeground=CLR["subtext"],
                       relief="flat", borderwidth=0, padx=10, pady=4,
                       font=("Segoe UI", 9))
    mb.pack(side=tk.LEFT)
    return mb


def _about(app) -> None:
    """Show the About card with the project's links."""
    version = version_info.current_version()
    note = ("  (development build)"
            if version_info.is_dev_version(version) else "")
    app.info("About TES Auto-Convert",
             f"T.E.SR.A.C.T {version}{note}\n\n"
             "Comprehensively converts Morrowind, Oblivion, Fallout 3, and "
             "Fallout NV data into TES5 (Skyrim SE) format — including "
             "plugins, assets, scripts, etc.\n\n"
             "Released under the MIT License. Bethesda game assets are not "
             "redistributed; this tool converts the copies you already own.",
             links=(("GitHub — source, releases and issues",
                     version_info.REPO_URL),
                    ("YouTube — @bryanthinton", YOUTUBE_URL),
                    ("Discord — community and support", DISCORD_URL)))


def _show_update_result(app, update_mb, result: dict) -> None:
    """Report a finished check. UI thread only, via `root.after`."""
    update_mb.configure(text="Check for Updates", state="normal")
    if not result["reachable"]:
        app.info("Update Check Failed",
                 "Could not reach GitHub to check for updates.\n\n"
                 "Check your connection, or see:\n"
                 f"{version_info.RELEASES_URL}")
        return
    if not result["available"]:
        app.info("Up to Date",
                 f"You are running {result['current']}, which is the newest "
                 f"release.")
        return
    if app.confirm("Update Available",
                   f"{result['latest']} is available (you have "
                   f"{result['current']}).\n\n"
                   "Download it and paste it over this folder; the Upgrade "
                   "button will then select only the steps that changed.\n\n"
                   "Open the downloads page now?",
                   yes="Open Page", no="Not Now"):
        open_url(version_info.RELEASES_URL)


def _build_about_menu(app, _menubutton, _menu_opts) -> None:
    """About and Check for Updates, the two right-hand bar entries.

    About is top-level rather than under Help: it is currently the only such
    item, and a one-entry menu is a worse click than a direct button.
    """
    about_mb = _flat_menubutton(app.menubar, "About")
    about_mb.bind("<Button-1>", lambda _e: _about(app))

    app.update_mb = _flat_menubutton(app.menubar, "Check for Updates")

    def _check():
        """Run the version check on a worker, unless one is in flight."""
        if str(app.update_mb.cget("state")) == "disabled":
            return
        app.update_mb.configure(text="Checking...", state="disabled")

        def _worker():
            """Ask GitHub, then hop back: tkinter is not thread-safe."""
            try:
                result = version_info.check_for_update()
            except Exception:
                result = {"current": version_info.current_version(),
                          "latest": None, "available": False,
                          "reachable": False}
            app.root.after(
                0, lambda: _show_update_result(app, app.update_mb, result))

        threading.Thread(target=_worker, daemon=True).start()

    app.update_mb.bind("<Button-1>", lambda _e: _check())


# ---------------------------------------------------------------------------
#  The bar itself
# ---------------------------------------------------------------------------

def build_menubar(app):
    """Build the whole bar; binds `app.menubar`, `converted_menu`, `update_mb`.

    Returns the `ModsUI` carrier the Mods dialogs need, which `gui_main` fills
    in once the rest of the window exists.
    """
    menu_opts = dict(
        tearoff=0,
        bg=CLR["panel"], fg=CLR["text"],
        activebackground=CLR["accent"], activeforeground="#ffffff",
        selectcolor=CLR["accent"], relief="flat",
        borderwidth=0, activeborderwidth=0,
        font=("Segoe UI", 9),
    )
    app.menubar = tk.Frame(app.root, bg=CLR["panel"])
    app.menubar.pack(side=tk.TOP, fill=tk.X)
    ttk.Separator(app.root, orient=tk.HORIZONTAL).pack(side=tk.TOP, fill=tk.X)

    bar = []

    def _menubutton(text: str) -> tk.Menu:
        """Add a dark top-level menu button; return its dropdown Menu."""
        mb = _flat_menubutton(app.menubar, text)
        menu = tk.Menu(mb, **menu_opts)
        mb.configure(menu=menu)
        bar.append((mb, menu))
        return menu

    _build_settings_menu(app, _menubutton, menu_opts)
    _build_converted_menu(app, _menubutton, menu_opts)
    mods_ui = gui_mods.ModsUI(app.root, None, CLR, EXPORT_DIR, None, None)
    gui_mods.add_mods_menu(_menubutton("Mods"), mods_ui)
    _build_tools_menu(app, _menubutton, menu_opts)
    _build_about_menu(app, _menubutton, menu_opts)
    enable_hover_switch(app.root, bar)
    return mods_ui
