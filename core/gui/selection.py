"""The step checkboxes and the Upgrade shortcut that drives them.

The Upgrade button ticks exactly the steps whose code changed between the
version that last converted this plugin and the one in this folder. Without it
every upgrade costs a full multi-hour reconversion for what is usually a
three-step change. It is disabled and relabelled when nothing is owed, so the
button's own state IS the status readout and no separate banner is needed.

Three plan states leave it inert, and the labels matter. "Never converted" is
NOT "up to date": it owes every step, and labelling it so told users with no
output at all that they had nothing to run -- the exact inversion of the truth.
It still has nothing to NARROW, so it stays disabled, but it must not claim the
work is done. "Offline" is the same trap: ticking all twelve boxes would read
as a considered recommendation for a multi-hour reconversion when it really
means "we could not ask".

The plan is a NETWORK call -- the version-to-steps table is published with the
release assets rather than shipped in the tree -- so it runs on a worker and
returns through `root.after`. This is called from combobox and tab handlers,
where a blocking socket would freeze the window for the whole timeout;
version.py caches the result, failures included, so only the first call per
process goes out.

SOURCES. A source is a registered game folder or an imported mod archive.
Switching one moves `tes4_var` too: every downstream consumer -- the run
command, the config, version stamping -- still reads it. Capabilities are a
property of the MOD, not the plugin: every plugin from one archive draws on the
same assets, so an un-catalogued import is measured against the SHARED tree.
Measuring `export/<plugin>/` looked at a folder that no longer exists and
reported a resource pack as having no meshes at all. A plugin in a game folder
is assumed fully capable, because its BSAs are not catalogued until Extract runs.

THE DROP ZONE. The WHOLE sidebar accepts a dropped archive, so there is no small
target. Three widgets are registered, not one: the scroll viewport covers the
frame almost entirely, so a drop lands on whichever is under the pointer. All
three are painted for the same reason. tkdnd delivers <<DragLeave>> unreliably
as the pointer crosses children, so the highlight is guarded by a depth counter
and force-reset on drop and focus loss.

See: docs/plans/in_app_update.md
"""

import os
import threading
import tkinter as tk
from tkinter import ttk

import version as version_info
from core.gui import mods as gui_mods
from core.gui.config import CLR, EXPORT_DIR, STEPS, default_on_steps, parse_dropped_paths, scan_plugins

TIP_IDLE = ("Everything in this folder's version has already been run for this "
            "plugin, so there is nothing to re-convert.")
TIP_NEVER_RUN = ("This plugin has never been converted, so there is no previous "
                 "version to compare against and nothing to narrow down.\n\n"
                 "Leave the default steps ticked and press Run.")
TIP_OFFLINE = ("The list of steps each release changed is published on GitHub "
               "and could not be fetched.\n\nConnect to the internet and "
               "reselect this plugin to enable the shortcut, or tick the steps "
               "by hand.")

#: Plan state -> (button label, tooltip); each leaves the button inert.
_INERT_STATES = (
    (lambda p: p["never_run"], "Not converted", TIP_NEVER_RUN),
    (lambda p: not p["steps"], "Up to date", TIP_IDLE),
    (lambda p: p.get("offline"), "Offline", TIP_OFFLINE),
)


# ---------------------------------------------------------------------------
#  The step list and its Upgrade shortcut
# ---------------------------------------------------------------------------

def build_step_rows(app, parent) -> None:
    """One checkbox row per pipeline step; binds `app.step_widgets`.

    The widgets are kept so a step the selected source cannot run -- an
    asset-only mod has no plugin to export -- can be disabled rather than
    silently running on nothing.
    """
    for step in STEPS:
        key, label, tip = step[0], step[2], step[3]
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill=tk.X, padx=14, pady=1)
        cb = ttk.Checkbutton(row, text=label, variable=app.step_vars[key],
                             command=app.update_run_btn)
        cb.pack(side=tk.LEFT)
        tip_lbl = ttk.Label(row, text=tip, style="PanelSub.TLabel")
        tip_lbl.pack(side=tk.LEFT, padx=(6, 0))
        app.step_widgets[key] = (cb, tip_lbl, tip)
        if key == "meshes":
            app.mesh_step_row = row


def runnable(app, key: str) -> bool:
    """False for a step the selected source has no content for."""
    cb = app.step_widgets.get(key, (None,))[0]
    return cb is None or str(cb.cget("state")) != "disabled"


def bind_selectors(app) -> None:
    """Bind `app.set_all`, `set_default`, `set_none` and `update_run_btn`.

    All and Default must not re-tick a greyed step: the run would collect it
    and the phase would work on nothing.
    """
    def _set_all():
        """Tick every step the current source can actually run."""
        for key, v in app.step_vars.items():
            v.set(runnable(app, key))
        app.update_run_btn()

    def _set_default():
        """Tick the default set, minus anything this source cannot run."""
        on = default_on_steps(app.pack_default_var.get())
        for key, v in app.step_vars.items():
            v.set(key in on and runnable(app, key))
        app.update_run_btn()

    def _set_none():
        """Clear every step."""
        for v in app.step_vars.values():
            v.set(False)
        app.update_run_btn()

    def _update_run_btn(*_):
        """Enable Run only when something is ticked and no run is in flight.

        A no-op until the button exists: the step panel is built before it, and
        binding availability can fire during construction.
        """
        if app.run_btn is None:
            return
        has = any(v.get() for v in app.step_vars.values())
        state = "normal" if has and not app.running.is_set() else "disabled"
        app.run_btn.configure(state=state)

    app.set_all = _set_all
    app.set_default = _set_default
    app.set_none = _set_none
    app.update_run_btn = _update_run_btn


def apply_upgrade_plan(app) -> None:
    """Tick exactly the steps the plan says this plugin still owes.

    Filtered through the Pack-by-default setting: the plan answers "what code
    changed since this plugin was last converted", which for a packaging change
    legitimately includes the packing steps -- but the setting is the user
    saying "never tick those for me automatically". Without the filter,
    selecting a plugin silently re-ticked the boxes the setting had cleared.
    """
    plan = app.upgrade_plan[0]
    if not plan or not plan.get("steps"):
        return
    wanted = set(plan["steps"]) & default_on_steps(app.pack_default_var.get())
    for key, v in app.step_vars.items():
        v.set(key in wanted)
    app.update_run_btn()


def _plan_tip(plan) -> str:
    """The Upgrade tooltip for a plan that has steps to offer.

    `unknown` does not mean ALL of them: each step resolves against its own
    recorded version, so a step already run at this version stays unticked even
    when another step's range cannot be read.
    """
    label_of = dict(version_info.STEP_KEYS)
    names = ", ".join(label_of.get(k, k) for k in plan["steps"])
    if plan["unknown"]:
        return (f"Updated to {plan['current']} from {plan['installed']}.\n\n"
                f"Which steps changed could not be determined for some of "
                f"these, so they are selected to be safe:\n{names}")
    if plan["upgraded"]:
        return (f"Updated {plan['installed']} → {plan['current']}.\n\n"
                f"Selects only the steps still owed at this version:\n{names}")
    return (f"These steps have not been run at {plan['current']} for this "
            f"plugin:\n{names}")


def _apply_plan_state(app, plan, fname: str, auto_apply: bool) -> None:
    """Set the Upgrade button's label, tooltip and enabled state for `plan`."""
    if not plan:
        app.upgrade_btn.configure(text="Upgrade", state="disabled")
        app.set_upgrade_tip(TIP_IDLE)
        return
    for matches, label, tip in _INERT_STATES:
        if matches(plan):
            app.upgrade_btn.configure(text=label, state="disabled")
            app.set_upgrade_tip(tip)
            return
    app.upgrade_btn.configure(text="Upgrade", state="normal")
    app.set_upgrade_tip(_plan_tip(plan))
    if auto_apply and fname and fname not in app.plan_applied:
        app.plan_applied.add(fname)
        apply_upgrade_plan(app)


def refresh_upgrade_notice(app, auto_apply: bool = True) -> None:
    """Recompute the upgrade shortcut for the selected plugin.

    `auto_apply` ticks the implied steps the first time a plugin's plan is
    seen: the point of the feature is that someone who pastes a new build over
    the old one and hits Run gets the right subset without reading anything.
    A finished run passes False: the outstanding set changed, but the user is
    looking at the selection they just ran.
    """
    fname = app.file_var.get().strip()

    def _apply(plan):
        """Land the answer, unless the selection moved while it was in flight."""
        if app.file_var.get().strip() != fname:
            return
        app.upgrade_plan[0] = plan
        _apply_plan_state(app, plan, fname, auto_apply)

    def _worker():
        """Fetch the plan off the UI thread, then hop back."""
        try:
            plan = version_info.upgrade_plan(fname or None)
        except Exception:
            plan = None
        app.root.after(0, lambda: _apply(plan))

    threading.Thread(target=_worker, daemon=True).start()


def build_upgrade_button(app, parent) -> None:
    """Add the Upgrade button beside All/Default/None; binds its tooltip.

    The tooltip is attached ONCE and its text swapped afterwards: the bindings
    are additive, so re-attaching would stack handlers and leak a popup a call.
    """
    from core.gui.widgets import attach_tooltip

    app.upgrade_plan = [None]
    app.plan_applied = set()
    app.upgrade_btn = ttk.Button(parent, text="Upgrade", width=9,
                                 command=lambda: apply_upgrade_plan(app))
    app.upgrade_btn.pack(side=tk.RIGHT, padx=(2, 0))
    app.set_upgrade_tip = attach_tooltip(app.upgrade_btn, TIP_IDLE)
    app.refresh_upgrade_notice = lambda auto_apply=True: refresh_upgrade_notice(
        app, auto_apply)


def build_step_panel(app, parent) -> None:
    """The Pipeline Steps heading, its four selector buttons, and the rows."""
    bind_selectors(app)
    header = ttk.Frame(parent, style="Panel.TFrame")
    header.pack(fill=tk.X, padx=14, pady=(0, 4))
    ttk.Label(header, text="Pipeline Steps",
              style="PanelSub.TLabel").pack(side=tk.LEFT)
    ttk.Button(header, text="None", command=app.set_none, width=5).pack(
        side=tk.RIGHT, padx=(2, 0))
    ttk.Button(header, text="Default", command=app.set_default, width=7).pack(
        side=tk.RIGHT, padx=(2, 0))
    ttk.Button(header, text="All", command=app.set_all, width=4).pack(
        side=tk.RIGHT, padx=(2, 0))
    build_upgrade_button(app, header)
    build_step_rows(app, parent)


# ---------------------------------------------------------------------------
#  The Meshes step's sub-options
# ---------------------------------------------------------------------------

PARALLAX_TIP = (
    "Carries Oblivion's own parallax (depth on dungeon walls, rock and "
    "architecture) across to Skyrim.\n\n"
    "ONLY turn this on if you play with Community Shaders or an ENB.\n\n"
    "Without one, the affected surfaces do not just look flat -- the texture "
    "visibly swims across them as you move. Tested: the SSE Parallax Shader "
    "Fix does not repair it.")

TEXONLY_TIP = (
    "Ships the textures and their height maps, but NO meshes.\n\n"
    "For PGPatcher (ParallaxGen), which patches meshes across your whole load "
    "order and can also upgrade them to complex material -- neither of which "
    "a single-plugin conversion can see.\n\n"
    "The meshes are still read: Oblivion's parallax flag lives in the mesh, "
    "and it is the only evidence that a texture carries a height map at all.")


def _sub_row(parent, after):
    """An indented row packed directly under `after`."""
    row = ttk.Frame(parent, style="Panel.TFrame")
    row.pack(fill=tk.X, padx=14, pady=(0, 1), after=after)
    return row


def _subdir_link(app, parent, after, on_click):
    """The "filter subfolders..." link under the Meshes checkbox."""
    row = _sub_row(parent, after)
    label = tk.Label(row, text="  filter subfolders...", bg=CLR["panel"],
                     fg=CLR["subtext"], font=("Segoe UI", 9, "underline"),
                     cursor="hand2")
    label.pack(side=tk.LEFT, padx=(20, 0))
    label.bind("<Button-1>", lambda _e: on_click())
    return row


def _parallax_row(app, parent, after, attach_tooltip):
    """The Convert-parallax checkbox and its hint."""
    row = _sub_row(parent, after)
    chk = ttk.Checkbutton(row, text="Convert parallax",
                          variable=app.parallax_var, style="TCheckbutton")
    chk.pack(side=tk.LEFT, padx=(20, 0))
    hint = ttk.Label(row, text="needs Community Shaders",
                     style="PanelSub.TLabel")
    hint.pack(side=tk.LEFT, padx=(6, 0))
    attach_tooltip(chk, PARALLAX_TIP)
    attach_tooltip(hint, PARALLAX_TIP)
    return row


def _texonly_row(app, parent, after, attach_tooltip):
    """The Textures-only checkbox; returns (row, checkbox)."""
    row = _sub_row(parent, after)
    chk = ttk.Checkbutton(row, text="Textures only",
                          variable=app.tex_only_var, style="TCheckbutton")
    chk.pack(side=tk.LEFT, padx=(40, 0))
    hint = ttk.Label(row, text="for PGPatcher", style="PanelSub.TLabel")
    hint.pack(side=tk.LEFT, padx=(6, 0))
    attach_tooltip(chk, TEXONLY_TIP)
    attach_tooltip(hint, TEXONLY_TIP)
    return row, chk


def build_mesh_options(app, parent, attach_tooltip, on_subdirs) -> None:
    """The three sub-options under the Meshes step.

    Parallax has no per-plugin default and never turns itself on: whether the
    output renders correctly depends on the PLAYER's setup, not on the plugin,
    so only they can answer it. Textures-only is a sub-option OF parallax --
    it exists so PGPatcher can do the mesh side across a whole load order, and
    without parallax it would just be a texture copy with the meshes missing
    -- so it follows the parallax box's state.
    """
    row = _subdir_link(app, parent, app.mesh_step_row, on_subdirs)
    row = _parallax_row(app, parent, row, attach_tooltip)
    _row, texonly_chk = _texonly_row(app, parent, row, attach_tooltip)

    def _sync(*_a):
        """Textures-only is meaningless, and cleared, without parallax."""
        on = app.parallax_var.get()
        texonly_chk.state(["!disabled"] if on else ["disabled"])
        if not on:
            app.tex_only_var.set(False)

    app.parallax_var.trace_add("write", _sync)
    _sync()

# ---------------------------------------------------------------------------
#  Where plugins come from
# ---------------------------------------------------------------------------

def all_sources(app) -> list:
    """Every source: registered folders, the configured one, and mods."""
    try:
        from asset_convert.sources import source_registry
        return source_registry.all_sources(EXPORT_DIR,
                                           extra_dirs=[app.tes4_var.get()])
    except Exception:
        path = app.tes4_var.get()
        if not path:
            return []
        return [{"id": "dir:" + os.path.normcase(os.path.normpath(path)),
                 "kind": "directory", "label": os.path.basename(path),
                 "path": path}]


def dir_sources(app) -> list:
    """Only the directory sources, in registration order."""
    return [r for r in all_sources(app) if r["kind"] == "directory"]


def shorten_path(path, limit: int = 34) -> str:
    """`path` trimmed from the left to fit `limit` characters."""
    p = str(path)
    return p if len(p) <= limit else "..." + p[-(limit - 3):]


def _source_detail(row) -> str:
    """The parenthesised summary beside a source's name.

    An asset-only mod says so: counting "1 plugin" for a texture pack that has
    none is a lie the user would act on.
    """
    if row["kind"] == "directory":
        count = len(scan_plugins(row["path"]))
    elif row.get("asset_only"):
        return "assets only"
    else:
        count = len(row.get("plugins") or [])
    return f"{count} plugin{'s' if count != 1 else ''}"


def refresh_scopes(app, src_hint, select=None) -> None:
    """Rebuild the Source dropdown; optionally switch to `select`."""
    app.scope_rows.clear()
    labels, ids = [], []
    for row in all_sources(app):
        app.scope_rows[row["id"]] = row
        labels.append(f"{row['label']}  ({_source_detail(row)})")
        ids.append(row["id"])
    app.scope_ids[:] = ids
    app.scope_combo["values"] = labels

    want = select if select in app.scope_rows else app.scope_var.get()
    if want not in app.scope_rows:
        want = ids[0] if ids else ""
    app.scope_var.set(want)
    if want not in ids:
        return
    app.scope_combo.current(ids.index(want))
    row = app.scope_rows[want]
    src_hint.configure(text=("mod archive" if row["kind"] == "mod"
                             else shorten_path(row["path"])))


def _scope_plugins(app, row, save_dirs) -> list:
    """The plugins the active source offers, moving `tes4_var` if needed."""
    if not row:
        return []
    if row["kind"] != "directory":
        return list(row.get("plugins") or [])
    if app.tes4_var.get() != row["path"]:
        app.tes4_var.set(row["path"])
        save_dirs()
    return scan_plugins(row["path"])


def apply_scope(app, save_dirs, last_valid, searching,
                select_plugin=None) -> None:
    """Repopulate the plugin list from the active source."""
    row = app.scope_rows.get(app.scope_var.get())
    plugins = _scope_plugins(app, row, save_dirs)
    app.all_plugins[:] = plugins
    app.file_combo["values"] = plugins

    if select_plugin and select_plugin in plugins:
        app.file_var.set(select_plugin)
    elif app.file_var.get() not in plugins:
        preferred = next((p for p in plugins if p.lower() == "oblivion.esm"),
                         None)
        app.file_var.set(preferred or (plugins[0] if plugins else ""))
    last_valid[0] = app.file_var.get()
    searching[0] = False


def capabilities_for_selection(app):
    """What the selected plugin can do, or None meaning "everything"."""
    try:
        from asset_convert.sources import mod_ingest, source_registry
        from output_layout import asset_root
        name = app.file_var.get()
        entry = source_registry.get(EXPORT_DIR, name)
    except Exception:
        return None
    if not entry:
        return None
    caps = entry.get("capabilities")
    if isinstance(caps, dict):
        return caps
    try:
        return mod_ingest.capabilities_for(
            asset_root(EXPORT_DIR, entry.get("plugin") or name),
            has_plugin=bool(entry.get("plugin")))
    except Exception:
        return None


#: Steps a Morrowind source cannot run, and the label that replaces the tip.
_TES3_UNAVAILABLE = {"scripts": "Morrowind scripts run on their own interpreter"}


def selection_is_tes3(app) -> bool:
    """True when the selected plugin is a Morrowind binary."""
    from source_paths import resolve_plugin_path
    from tes4_export.tes3_reader import is_tes3
    name = app.file_var.get()
    if not name:
        return False
    try:
        return is_tes3(resolve_plugin_path(name, app.tes4_var.get(),
                                           str(EXPORT_DIR)))
    except Exception:
        return False


def why_unavailable(key: str, caps) -> str:
    """Why a step is greyed, in the user's terms rather than "disabled"."""
    if key == "extract":
        return "already extracted on import"
    from asset_convert.sources import mod_ingest

    needs = mod_ingest.STEP_REQUIREMENTS.get(key, ())
    if needs == ("plugin",):
        return "needs a plugin"
    missing = ", ".join(n for n in needs if not caps.get(n))
    return f"no {missing}" if missing else "nothing to convert"


def _grey_step(app, key: str, reason: str) -> None:
    """Untick and disable one step, replacing its tip with `reason`.

    Untick as well as disable: a step left ticked is still collected when the
    run gathers its selection.
    """
    cb, lbl, _tip = app.step_widgets[key]
    app.step_vars[key].set(False)
    cb.configure(state="disabled")
    lbl.configure(text=reason)


def _enable_all_steps(app) -> None:
    """Restore every step to its normal label and enabled state."""
    for _key, (cb, lbl, tip) in app.step_widgets.items():
        cb.configure(state="normal")
        lbl.configure(text=tip)


def apply_step_availability(app) -> None:
    """Grey out steps the selected source cannot run.

    See: docs/commentary/morrowind_runtime.md#why-not-record-conversion
    """
    caps = capabilities_for_selection(app)
    if caps is None:
        _enable_all_steps(app)
    else:
        try:
            from asset_convert.sources import mod_ingest
            usable = mod_ingest.available_steps(caps)
        except Exception:
            return
        for key, (cb, lbl, tip) in app.step_widgets.items():
            if key in usable:
                cb.configure(state="normal")
                lbl.configure(text=tip)
            else:
                _grey_step(app, key, why_unavailable(key, caps))

    if selection_is_tes3(app):
        for key, reason in _TES3_UNAVAILABLE.items():
            _grey_step(app, key, reason)
    app.update_run_btn()


def select_source_for_plugin(app, name, refresh, apply_) -> bool:
    """Switch to whichever source actually holds `name`; True if found.

    Used by Converted, so re-running a plugin restores its real origin instead
    of whatever folder happens to be selected. A directory source prefers the
    path recorded for this plugin, then the first registered folder holding it.
    """
    import version as version_info

    try:
        from asset_convert.sources import source_registry
        entry = source_registry.get(EXPORT_DIR, name)
    except Exception:
        entry = None
    if entry:
        refresh(select=f"mod:{entry.get('group_id')}")
        apply_(select_plugin=entry.get("plugin") or name)
        return True

    recorded = version_info.source_path_for(name)
    candidates = ([recorded] if recorded else []) + [r["path"] for r
                                                     in dir_sources(app)]
    for path in candidates:
        if path and os.path.isfile(os.path.join(path, name)):
            refresh(select="dir:" + os.path.normcase(os.path.normpath(path)))
            apply_(select_plugin=name)
            return True
    return False


def build_panel(app, parent, on_add, on_remove):
    """The Source dropdown with its +/- buttons; returns (frame, hint)."""
    import tkinter as tk
    from tkinter import ttk

    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.pack(fill=tk.X, padx=14, pady=(0, 8))
    head = ttk.Frame(frame, style="Panel.TFrame")
    head.pack(fill=tk.X)
    ttk.Label(head, text="Source", style="PanelSub.TLabel").pack(
        side=tk.LEFT, anchor="w")
    hint = ttk.Label(head, text="", style="PanelSub.TLabel")
    hint.pack(side=tk.RIGHT, anchor="e")

    row = ttk.Frame(frame, style="Panel.TFrame")
    row.pack(fill=tk.X, pady=(2, 0))
    row.columnconfigure(0, weight=1)
    app.scope_var = tk.StringVar(value="")
    app.scope_combo = ttk.Combobox(row, state="readonly", width=26)
    app.scope_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ttk.Button(row, text="+", width=2, command=on_add).grid(row=0, column=1)
    ttk.Button(row, text="−", width=2, command=on_remove).grid(
        row=0, column=2, padx=(3, 0))
    return frame, hint


def add_source_dir(app, refresh, apply_) -> None:
    """Register another game Data folder as a source."""
    from tkinter import filedialog

    from asset_convert.sources import source_registry
    from core.gui.config import REPO_ROOT

    path = filedialog.askdirectory(
        initialdir=app.tes4_var.get() or str(REPO_ROOT),
        title="Select a game Data folder")
    if not path:
        return
    if not scan_plugins(path) and not app.confirm(
            "Add Source",
            f"No .esm/.esp plugins found in:\n\n{path}\n\nAdd it anyway?",
            yes="Add", no="Cancel"):
        return
    source_registry.add_directory(EXPORT_DIR, path)
    refresh(select=f"dir:{os.path.normcase(os.path.normpath(path))}")
    apply_()


def remove_source(app, refresh, apply_, manage_mods) -> None:
    """Unregister the selected source. Folders on disk are never deleted."""
    from asset_convert.sources import source_registry

    row = app.scope_rows.get(app.scope_var.get())
    if not row:
        return
    if row["kind"] == "mod":
        manage_mods()
        return
    if len(dir_sources(app)) <= 1:
        app.info("Remove Source",
                 "This is the only game folder left. Add another before "
                 "removing it.")
        return
    if not app.confirm("Remove Source",
                       f"Stop listing this folder?\n\n{row['path']}\n\n"
                       "Nothing on disk is deleted.",
                       yes="Remove", no="Cancel"):
        return
    source_registry.remove_directory(EXPORT_DIR, row["path"])
    refresh()
    apply_()


def on_scope_selected(app, refresh, apply_) -> None:
    """The user picked a different source: repopulate everything from it."""
    idx = app.scope_combo.current()
    if 0 <= idx < len(app.scope_ids):
        app.scope_var.set(app.scope_ids[idx])
    apply_()
    refresh(select=app.scope_var.get())
    apply_step_availability(app)
    app.refresh_upgrade_notice()

# ---------------------------------------------------------------------------
#  The sidebar drop zone
# ---------------------------------------------------------------------------

def _targets(app) -> tuple:
    """The three widgets a drop may land on."""
    return (app.sidebar, app.sb_canvas, app.sb_body)


def _paint_on(app, saved) -> None:
    """Tint the sidebar and show the drop cursor."""
    app.sidebar.configure(style="Drop.TFrame")
    app.sb_body.configure(style="Drop.TFrame")
    drop_bg = ttk.Style().lookup("Drop.TFrame", "background")
    app.sb_canvas.configure(bg=drop_bg or CLR["panel"])
    if saved[0] is None:
        saved[0] = app.sidebar.cget("cursor")
    app.sidebar.configure(cursor="hand2")
    app.sb_canvas.configure(cursor="hand2")


def _paint_off(app, saved) -> None:
    """Restore the normal sidebar colors and cursor."""
    app.sidebar.configure(style="Panel.TFrame")
    app.sb_body.configure(style="Panel.TFrame")
    app.sb_canvas.configure(bg=CLR["panel"])
    app.sidebar.configure(cursor=saved[0] or "")
    app.sb_canvas.configure(cursor="")
    saved[0] = None


def _paint(app, saved, on: bool) -> None:
    """Switch the highlight on or off, ignoring a torn-down widget."""
    try:
        (_paint_on if on else _paint_off)(app, saved)
    except tk.TclError:
        pass


def dropped_mod_path(app, data: str):
    """The single droppable mod path in `data`, or None having explained why.

    A folder is a valid mod source; any other non-archive is not.
    """
    paths = parse_dropped_paths(data)
    if not paths:
        return None
    if len(paths) > 1:
        app.info("Import Mod", "Drop one mod archive at a time.")
        return None
    path = paths[0]
    if not os.path.exists(path):
        app.info("Import Mod", f"Not found:\n\n{path}")
        return None
    if not os.path.isdir(path):
        from asset_convert.sources import archive

        if not archive.is_archive(path):
            app.info("Import Mod",
                     f"{os.path.basename(path)} is not a mod archive.\n\n"
                     "Drop a .zip, .7z or .rar, or an extracted mod folder.")
            return None
    return path


def _register(app, dnd_files, on_enter, on_leave, on_drop) -> int:
    """Bind the drop handlers to every target; returns how many took them."""
    registered = 0
    for target in _targets(app):
        try:
            target.drop_target_register(dnd_files)
            target.dnd_bind("<<DropEnter>>", on_enter)
            target.dnd_bind("<<DropLeave>>", on_leave)
            target.dnd_bind("<<Drop>>", on_drop)
            registered += 1
        except Exception:
            continue
    return registered


def install_dropzone(app, mods_ui) -> None:
    """Register the sidebar as a drop target for mod archives."""
    try:
        from tkinterdnd2 import DND_FILES
    except Exception:
        return

    depth = [0]
    saved = [None]

    def _enter(_e=None):
        """Highlight, counting nested enters."""
        depth[0] += 1
        _paint(app, saved, True)
        return "copy"

    def _leave(_e=None):
        """Un-highlight once every nested enter has been matched."""
        depth[0] = max(0, depth[0] - 1)
        if depth[0] == 0:
            _paint(app, saved, False)

    def _reset(_e=None):
        """Force the highlight off, whatever the counter says."""
        depth[0] = 0
        _paint(app, saved, False)

    def _drop(event):
        """Import the dropped archive, if it is one."""
        _reset()
        path = dropped_mod_path(app, getattr(event, "data", "") or "")
        if path is not None:
            gui_mods.begin_import(path, mods_ui)

    if _register(app, DND_FILES, _enter, _leave, _drop):
        app.root.bind("<FocusOut>", _reset, add="+")
