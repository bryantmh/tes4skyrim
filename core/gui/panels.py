"""The modal panels: mesh subfolders, Skyrim patch plugins, Convert Oblivion UI.

Every panel is a card placed directly over `app.outer` -- no Toplevel, so it
stays inside the one window. A full-size backdrop is deliberately avoided: it
would cover the log and sidebar and read as the app disappearing.

What a panel decides is written to `app.selection` (see `PanelSelection` in
app.py), never to a closure the command builder happens to share. The command
builder and the startup stamp both read that state before any panel has been
opened, so the channel has to outlive the dialog.

The Create-LOD list draws its tick INTO the row text and toggles it by
click, so one Listbox carries both the order and the on/off state without the
two fighting over the mouse.

Three of these panels gate a global action rather than merely editing a
setting: Create LOD, Convert to Master and Convert Oblivion UI each rewrite something
shared -- tiles on a fixed grid, plugins in place, or the installed UI -- so
the selection is a decision to confirm before anything runs, not a default to
fire off.

See: docs/reference/pipeline.md#configuration
"""

import tkinter as tk
from tkinter import ttk

from core.gui.config import CLR, EXPORT_DIR, REPO_ROOT, scan_mesh_subdirs, scan_skyrim_load_order

# ---------------------------------------------------------------------------
#  The shared card
# ---------------------------------------------------------------------------

def _card(app):
    """An empty modal card centered over the window; returns (card, close)."""
    card = tk.Frame(app.outer, bg=CLR["panel"],
                    highlightbackground=CLR["border"], highlightthickness=1)
    bound = []

    def _close():
        """Release the wheel grab, if any, and tear the card down."""
        if bound:
            card.unbind_all("<MouseWheel>")
        card.destroy()

    return card, _close, bound


def _title_row(card, title: str, pairs) -> None:
    """The card's heading plus its All / None buttons."""
    row = tk.Frame(card, bg=CLR["panel"])
    row.pack(fill=tk.X, padx=16, pady=(14, 0))
    tk.Label(row, text=title, bg=CLR["panel"], fg=CLR["text"],
             font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
    ttk.Button(row, text="All", width=4,
               command=lambda: [v.set(True) for _, v in pairs]).pack(
                   side=tk.RIGHT, padx=(4, 0))
    ttk.Button(row, text="None", width=5,
               command=lambda: [v.set(False) for _, v in pairs]).pack(
                   side=tk.RIGHT)


def _checkbox_list(card, pairs, bound, padx: int) -> None:
    """A scrolling column of checkbuttons, one per (name, var) pair."""
    list_frame = tk.Frame(card, bg=CLR["panel"])
    list_frame.pack(fill=tk.BOTH, expand=True, padx=8)

    canvas = tk.Canvas(list_frame, bg=CLR["panel"], highlightthickness=0,
                       width=320, height=min(360, 22 * len(pairs)))
    vsb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=CLR["panel"])
    inner.bind("<Configure>",
               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _wheel(e):
        """Scroll the list under the pointer."""
        canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    card.bind_all("<MouseWheel>", _wheel)
    bound.append(True)

    for name, var in pairs:
        ttk.Checkbutton(inner, text=name, variable=var,
                        style="TCheckbutton").pack(anchor="w", padx=padx,
                                                   pady=1)


def _show(app, card) -> None:
    """Center the finished card over the window and raise it."""
    card.update_idletasks()
    card.place(in_=app.outer, anchor="center", relx=0.5, rely=0.5)
    card.lift()


def checkbox_card(app, title: str, pairs, empty_text: str,
                  padx: int = 12) -> None:
    """A modal card offering one checkbox per (name, var) pair.

    The mesh-subfolder and Skyrim-patch panels are the same dialog twice: a
    heading with All/None, a scrolling list, and an OK button. `empty_text`
    replaces the list when there is nothing to choose from.
    """
    card, close, bound = _card(app)
    _title_row(card, title, pairs)
    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

    if not pairs:
        tk.Label(card, text=empty_text, bg=CLR["panel"], fg=CLR["subtext"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 8))
    else:
        _checkbox_list(card, pairs, bound, padx)

    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)
    ttk.Button(card, text="OK", style="Accent.TButton",
               command=close).pack(pady=(0, 14))
    _show(app, card)


# ---------------------------------------------------------------------------
#  The checkbox panels
# ---------------------------------------------------------------------------

def open_mesh_subdir_panel(app) -> None:
    """Choose which mesh subfolders the Meshes step converts.

    The variables are rebuilt from disk each time so a fresh Extract shows up,
    but any box the user already set keeps its state.
    """
    subdirs = scan_mesh_subdirs(app.file_var.get())
    old = {name: v.get() for name, v in app.mesh_subdir_vars}
    app.mesh_subdir_vars.clear()
    for name in subdirs:
        app.mesh_subdir_vars.append(
            (name, tk.BooleanVar(value=old.get(name, True))))

    checkbox_card(app, "Mesh subfolders to convert", app.mesh_subdir_vars,
                  "Run the Extract step first to populate this list.")


def refresh_patch_plugin_vars(app) -> None:
    """(Re)populate `app.selection.patch_plugins` from the Skyrim load order.

    Existing checkbox state is preserved. A new entry defaults to checked only
    if it is official content or listed in plugins.txt; a plugin found by the
    raw directory scan alone starts unchecked.
    """
    names, default_checked = scan_skyrim_load_order(app.tes5_var.get())
    old = {name: v.get() for name, v in app.selection.patch_plugins}
    app.selection.patch_plugins = [
        (name, tk.BooleanVar(value=old.get(name, name in default_checked)))
        for name in names]


def open_patch_plugin_panel(app) -> None:
    """Choose which Skyrim plugins the slot-44 body patch covers."""
    refresh_patch_plugin_vars(app)
    checkbox_card(app, "Skyrim plugins to patch", app.selection.patch_plugins,
                  "No plugins found. Set the Skyrim SE Data Directory above.",
                  padx=8)


# ---------------------------------------------------------------------------
#  What each dialog offers when nothing has been confirmed
# ---------------------------------------------------------------------------

def plugin_masters(app, name: str) -> list:
    """The MAST list of a converted plugin, or [] if it cannot be read."""
    import sys

    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tools.esm.make_master import read_header, resolve
        _flags, masters = read_header(resolve(name, str(app.out_root())))
        return masters
    except Exception:
        return []


def default_master_plugins(app) -> list:
    """Converted plugins worth flagging ESM, masters first.

    Ordered so a plugin always follows the masters it depends on: the tool
    rejects a batch that would leave an ESM mastering a plain ESP, and this is
    also the order the user must install them in.
    """
    try:
        from asset_convert.lod.sibling_lod import converted_plugins
        names = sorted(converted_plugins(app.out_root()))
    except Exception:
        return []
    deps = {n: [m for m in plugin_masters(app, n) if m in set(names)]
            for n in names}
    ordered, seen = [], set()

    def _visit(n, stack=()):
        """Depth-first, leaving a cycle wherever it lands."""
        if n in seen or n in stack:
            return
        for m in deps.get(n, ()):
            _visit(m, stack + (n,))
        seen.add(n)
        ordered.append(n)

    for n in names:
        _visit(n)
    return ordered


def default_lod_plugins(app) -> list:
    """Converted plugins in plugins.txt order, the rest appended.

    Must mirror `sibling_lod.create_lod_order` exactly: this list is what the
    user sees and drags, so deriving it differently here would show an order
    the run does not apply and misreport which plugin wins a tile.
    """
    try:
        from asset_convert.lod.sibling_lod import converted_plugins, create_lod_order
    except Exception:
        return []
    return create_lod_order(converted_plugins(app.out_root()), EXPORT_DIR)


def default_lod_worldspaces(app, names: list) -> list:
    """Every worldspace the selected plugins would generate LOD for."""
    try:
        from asset_convert.lod.sibling_lod import lod_worldspaces
    except Exception:
        return []
    return lod_worldspaces(names, EXPORT_DIR, app.out_root())


# ---------------------------------------------------------------------------
#  Convert to Master
# ---------------------------------------------------------------------------

MASTER_BLURB = (
    "A plugin that is not a master has EVERY reference it contains treated as\n"
    "always-active. The engine caps those at 1,048,576 — past that the game "
    "hangs\non the main menu with no crash and no log. Flagging a large "
    "worldspace\nplugin ESM lets its references load per cell instead.\n\n"
    "Ticking a plugin also ticks the masters it depends on: a master must "
    "load\nfirst, so an ESM may not master a plain ESP.")


def _empty_card(app, title: str, text: str) -> None:
    """A card that only explains why there is nothing to choose from."""
    card, close, _bound = _card(app)
    tk.Label(card, text=title, bg=CLR["panel"], fg=CLR["text"],
             font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16,
                                                 pady=(14, 0))
    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)
    tk.Label(card, text=text, bg=CLR["panel"], fg=CLR["subtext"],
             font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 8))
    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)
    ttk.Button(card, text="Close", command=close).pack(pady=(0, 14))
    _show(app, card)


def _read_esm_flags(app, names: list) -> dict:
    """name -> whether it is ALREADY flagged ESM; False if unreadable."""
    import sys

    try:
        sys.path.insert(0, str(REPO_ROOT))
        from tools.esm.make_master import FLAG_ESM, read_header, resolve
    except Exception:
        return {n: False for n in names}
    flags = {}
    for n in names:
        try:
            got, _m = read_header(resolve(n, app.out_root()))
            flags[n] = bool(got & FLAG_ESM)
        except Exception:
            flags[n] = False
    return flags


def _untick_dependents(row_vars, deps) -> None:
    """Untick anything whose masters are no longer all ticked, to a fixpoint."""
    changed = True
    while changed:
        changed = False
        for n, v in row_vars.items():
            if v.get() and any(not row_vars[m].get() for m in deps.get(n, ())):
                v.set(False)
                changed = True


def _tick_masters(row_vars, deps, name) -> None:
    """Tick `name`'s masters, and theirs, so no dependent is left dangling."""
    stack = [name]
    while stack:
        cur = stack.pop()
        for m in deps.get(cur, ()):
            if not row_vars[m].get():
                row_vars[m].set(True)
                stack.append(m)


def _master_rows(inner, names, wanted, deps, is_esm, on_toggle) -> dict:
    """One checkbox row per plugin; returns {name: var}."""
    row_vars = {}
    for name in names:
        row = tk.Frame(inner, bg=CLR["panel"])
        row.pack(fill=tk.X, anchor="w", pady=1)
        var = tk.BooleanVar(value=name in wanted)
        row_vars[name] = var
        ttk.Checkbutton(row, text=name, variable=var, style="TCheckbutton",
                        command=lambda n=name: on_toggle(n)).pack(
                            side=tk.LEFT, padx=12)
        if is_esm.get(name):
            tk.Label(row, text="already ESM", bg=CLR["panel"],
                     fg=CLR["green"], font=("Segoe UI", 8)).pack(
                         side=tk.LEFT, padx=(4, 0))
        if deps.get(name):
            tk.Label(row, text="masters: " + ", ".join(deps[name]),
                     bg=CLR["panel"], fg=CLR["subtext"],
                     font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(6, 0))
    return row_vars


def _scrolling_pane(card, bound, width: int, height: int):
    """A scrolling canvas inside `card`; returns the inner frame."""
    list_frame = tk.Frame(card, bg=CLR["panel"])
    list_frame.pack(fill=tk.BOTH, expand=True, padx=16)
    canvas = tk.Canvas(list_frame, bg=CLR["panel"], highlightthickness=0,
                       width=width, height=height)
    vsb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=CLR["panel"])
    inner.bind("<Configure>",
               lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    card.bind_all("<MouseWheel>",
                  lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1,
                                                "units"))
    bound.append(True)
    return inner


def _validate_masters(row_vars, deps, is_esm, warn) -> bool:
    """Flag any dangling master in `warn`; True when the set is valid."""
    sel = {n for n, v in row_vars.items() if v.get()}
    bad = [f"{n} needs {m}" for n in sel for m in deps.get(n, ())
           if m not in sel and not is_esm.get(m)]
    more = "" if len(bad) <= 3 else f" (+{len(bad) - 3} more)"
    warn.configure(text=("⚠ " + "; ".join(bad[:3]) + more) if bad else "")
    return not bad


def _all_none_buttons(title_row, row_vars, validate) -> None:
    """All ticks everything (so nothing dangles); None ticks nothing."""
    def _set_all(on: bool):
        """Set every row and re-validate."""
        for v in row_vars.values():
            v.set(on)
        validate()

    ttk.Button(title_row, text="All", width=4,
               command=lambda: _set_all(True)).pack(side=tk.RIGHT,
                                                    padx=(4, 0))
    ttk.Button(title_row, text="None", width=5,
               command=lambda: _set_all(False)).pack(side=tk.RIGHT)


def _warn_label(card):
    """The amber line under the list that reports a dangling master."""
    warn = tk.Label(card, text="", bg=CLR["panel"], fg=CLR["yellow"],
                    justify=tk.LEFT, font=("Segoe UI", 9))
    warn.pack(anchor="w", padx=16, pady=(6, 0))
    return warn


def _master_inputs(app, all_names) -> tuple:
    """(deps, already-ESM flags, initially-ticked set) for the panel.

    Anything not already a master starts ticked, and a plugin that IS
    flagged stays ticked, so the list reads as the FINAL state rather than
    "what will change".
    """
    names = set(all_names)
    deps = {n: [m for m in plugin_masters(app, n) if m in names]
            for n in all_names}
    chosen = app.selection.master_plugins
    wanted = set(chosen) & names if chosen else names
    return deps, _read_esm_flags(app, all_names), wanted


def _master_header(card):
    """The Convert-to-Master title row and explanatory blurb."""
    title_row = tk.Frame(card, bg=CLR["panel"])
    title_row.pack(fill=tk.X, padx=16, pady=(14, 0))
    tk.Label(title_row, text="Convert to Master", bg=CLR["panel"],
             fg=CLR["text"], font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
    tk.Label(card, text=MASTER_BLURB, bg=CLR["panel"], fg=CLR["subtext"],
             justify=tk.LEFT, font=("Segoe UI", 9)).pack(anchor="w", padx=16,
                                                         pady=(4, 0))
    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)
    return title_row


def _master_buttons(card, on_apply_click, close) -> None:
    """The Apply / Cancel row."""
    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)
    btns = tk.Frame(card, bg=CLR["panel"])
    btns.pack(pady=(0, 14))
    ttk.Button(btns, text="Apply", style="Accent.TButton",
               command=on_apply_click).pack(side=tk.LEFT, padx=4)
    ttk.Button(btns, text="Cancel", command=close).pack(side=tk.LEFT, padx=4)


def open_make_master_panel(app, on_apply=None) -> None:
    """Pick which converted plugins to flag as masters (ESM).

    Ticking a plugin auto-ticks the masters it depends on, because the tool
    refuses a batch that would leave an ESM mastering a plain ESP -- an invalid
    load order, since a master must load first. Doing it here means the user
    sees the whole chain before anything is written rather than getting a
    refusal after pressing Apply.

    `on_apply` is called when Apply is pressed; None makes this a pure editor
    of the saved selection.
    """
    all_names = default_master_plugins(app)
    if not all_names:
        _empty_card(app, "Convert to Master",
                    "Nothing converted yet — convert a plugin first.")
        return

    deps, is_esm, wanted = _master_inputs(app, all_names)

    card, close, bound = _card(app)
    title_row = _master_header(card)
    inner = _scrolling_pane(card, bound, 460, min(300, 26 * len(all_names)))
    warn = _warn_label(card)
    row_vars = {}

    def _validate():
        """Warn if the ticked set would leave an ESM mastering an ESP."""
        return _validate_masters(row_vars, deps, is_esm, warn)

    def _on_toggle(name):
        """Tick a plugin -> tick its masters; untick -> untick dependents."""
        if row_vars[name].get():
            _tick_masters(row_vars, deps, name)
        else:
            _untick_dependents(row_vars, deps)
        _validate()

    _all_none_buttons(title_row, row_vars, _validate)

    row_vars.update(_master_rows(inner, all_names, wanted, deps, is_esm,
                                 _on_toggle))
    _validate()

    def _apply():
        """Store the ticked set and hand off, unless it is invalid."""
        if not _validate():
            return
        app.selection.master_plugins[:] = [n for n in all_names
                                           if row_vars[n].get()]
        close()
        if on_apply is not None:
            on_apply()

    _master_buttons(card, _apply, close)
    _show(app, card)


# ---------------------------------------------------------------------------
#  Convert Oblivion UI
# ---------------------------------------------------------------------------

UI_BLURB = ("Build a standalone mod that reskins Skyrim's UI with Oblivion's "
            "own art, read from your local game install. Choose what to "
            "convert:")

UI_NOTE = ("Nothing Bethesda ships is redistributed. The result lands in "
           "output/Finished Mods as \"Oblivion UI.zip\"; it conflicts only "
           "with mods that replace those same movies.")

UI_MOVIES = (("messagebox", "Message boxes  (Interface\\messagebox.swf)"),
             ("cursor",
              "Menu cursor  (Interface\\cursormenu.swf) (EXPERIMENTAL)"))


def _ui_body(card) -> None:
    """The Convert-UI title, rule and explanatory paragraph."""
    tk.Label(card, text="Slopping of Screens", bg=CLR["panel"],
             fg=CLR["text"], font=("Segoe UI", 10, "bold")).pack(
                 anchor="w", padx=16, pady=(14, 0))
    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)
    tk.Label(card, text=UI_BLURB, bg=CLR["panel"], fg=CLR["subtext"],
             font=("Segoe UI", 9), justify=tk.LEFT, anchor="w",
             wraplength=380).pack(anchor="w", padx=16, pady=(0, 8))


def open_convert_ui_panel(app, on_continue=None) -> None:
    """The Convert Oblivion UI options card: pick which movies to reskin.

    Both boxes are on by default, and Continue greys out when NEITHER is
    checked -- there would be nothing to build. Reached from a MENU, where
    there is no tooltip or button state to explain the action, so the card
    doubles as the explanation.
    """
    card = tk.Frame(app.outer, bg=CLR["panel"],
                    highlightbackground=CLR["border"], highlightthickness=1)
    _ui_body(card)

    chosen = app.selection.convert_ui
    vars_by_key = {key: tk.BooleanVar(value=chosen[key])
                   for key, _label in UI_MOVIES}
    for key, label in UI_MOVIES:
        ttk.Checkbutton(card, text=label, variable=vars_by_key[key],
                        style="TCheckbutton").pack(anchor="w", padx=20, pady=1)

    tk.Label(card, text=UI_NOTE, bg=CLR["panel"], fg=CLR["subtext"],
             font=("Segoe UI", 8), justify=tk.LEFT, anchor="w",
             wraplength=380).pack(anchor="w", padx=16, pady=(8, 12))

    btn_row = tk.Frame(card, bg=CLR["panel"])
    btn_row.pack(fill=tk.X, padx=16, pady=(0, 14))

    def _close():
        """Release the grab and tear the card down."""
        card.grab_release()
        card.destroy()

    def _continue():
        """Store the choice and hand off."""
        for key, _label in UI_MOVIES:
            chosen[key] = vars_by_key[key].get()
        _close()
        if on_continue:
            on_continue()

    ttk.Button(btn_row, text="Cancel", width=10,
               command=_close).pack(side=tk.RIGHT, padx=(6, 0))
    continue_btn = ttk.Button(btn_row, text="Continue", width=10,
                              style="Accent.TButton", command=_continue)
    continue_btn.pack(side=tk.RIGHT, padx=(6, 0))

    def _sync(*_a):
        """Nothing selected means nothing to build."""
        any_on = any(v.get() for v in vars_by_key.values())
        continue_btn.configure(state="normal" if any_on else "disabled")

    for v in vars_by_key.values():
        v.trace_add("write", _sync)
    _sync()

    _show(app, card)
    card.bind("<Escape>", lambda _e: _close())
    card.focus_set()
    card.grab_set()


# ---------------------------------------------------------------------------
#  Create LOD
# ---------------------------------------------------------------------------

LOD_BLURB = ("Distant LOD is generated once for the whole load order.\n"
             "Drag to reorder — the plugin at the BOTTOM wins any tile two of "
             "them both change.")

#: Row prefixes for the ordered plugin list.
TICK, UNTICK = "\u2611 ", "\u2610 "


def _lod_initial_order(app, all_names) -> tuple:
    """(ordered names, ticked set) from the saved selection.

    Never hides a plugin converted since the selection was made: unknown names
    are appended in default order, so a new conversion appears ticked rather
    than silently dropping out of every future run.
    """
    chosen = app.selection.lod_plugins
    if not chosen:
        return list(all_names), set(all_names)
    ordered = [n for n in chosen if n in all_names]
    ordered += [n for n in all_names if n not in ordered]
    wanted = {n for n in chosen if n in all_names}
    wanted |= {n for n in all_names if n not in chosen}
    return ordered, wanted


def _scan_lod_inputs(app, all_names) -> tuple:
    """(dependents, worldspaces-by-plugin, why-empty, merge fn, all-by-plugin).

    Scanned ONCE because both reads walk every export dir, so each subsequent
    tick is a dict lookup rather than a rescan. The last item is every
    worldspace each converted ESM defines, offered only under Force.
    """
    try:
        from asset_convert.lod.sibling_lod import defined_worldspaces_by_plugin, dependents_of, merge_worldspaces, worldspaces_by_plugin_diagnosed
        deps = dependents_of(all_names, EXPORT_DIR)
        ws_by, ws_why = worldspaces_by_plugin_diagnosed(
            all_names, EXPORT_DIR, app.out_root())
        all_by = defined_worldspaces_by_plugin(all_names, EXPORT_DIR,
                                               app.out_root())
        return deps, ws_by, ws_why, merge_worldspaces, all_by
    except Exception as exc:
        return ({n: set() for n in all_names}, {n: [] for n in all_names},
                {"": f"Worldspace scan failed: {exc}"},
                lambda names, by_plugin: [], {})


def _why_no_worldspaces(names, ws_why) -> str:
    """Explain, per plugin, why nothing is on offer.

    "No worldspace ships distant LOD" is a symptom shared by every failure
    mode, and it is outright wrong for the commonest one -- assets never
    extracted -- which sent users looking for a plugin problem that did not
    exist.
    """
    why = [ws_why[n] for n in names if n in ws_why]
    why += [v for k, v in ws_why.items() if k == ""]
    if not why:
        return ("No selected plugin has worldspace terrain\n"
                "to generate LOD from.")
    text = "\n\n".join(why[:6])
    return text + (f"\n\n(+{len(why) - 6} more)" if len(why) > 6 else "")


class _LodState:
    """The mutable state the two LOD columns share."""

    __slots__ = ("ordered", "wanted", "checked", "disabled", "deps", "ws_by",
                 "ws_why", "merge", "all_by", "force", "ws_state", "ws_vars",
                 "listbox", "inner")

    def __init__(self, ordered, wanted, deps, ws_by, ws_why, merge, all_by):
        """Seed from the saved selection and the one-time scan."""
        self.all_by = all_by
        self.force = None
        self.ordered = ordered
        self.wanted = wanted
        self.checked = set(wanted)
        self.disabled = set()
        self.deps = deps
        self.ws_by = ws_by
        self.ws_why = ws_why
        self.merge = merge
        self.ws_state = {}
        self.ws_vars = []
        self.listbox = None
        self.inner = None


def _recompute_disabled(st, all_names) -> None:
    """Grey out everything resting on a master the user turned off.

    `deps[m]` is already transitive, so one pass over the unticked plugins
    covers indirect dependents too. Driven by `wanted` -- what the user
    actually clicked -- rather than by `checked`, which this narrows: reading
    `checked` would make a greyed row look like a user choice next pass and
    grey ITS dependents, collapsing the whole chain.
    """
    st.disabled.clear()
    for name in all_names:
        if name not in st.wanted:
            st.disabled.update(st.deps.get(name, ()))
    st.disabled.difference_update(n for n in all_names if n not in st.wanted)
    st.checked.clear()
    st.checked.update(n for n in st.wanted if n not in st.disabled)


def _name_at(listbox, i: int) -> str:
    """The plugin in row `i`, with any tick prefix stripped."""
    row = listbox.get(i)
    return row[2:] if row[:2] in (TICK, UNTICK) else row


def _redraw(st, all_names, refresh_worldspaces) -> None:
    """Repaint every row's tick and grey state, preserving order.

    Rewrites in place rather than clearing and re-inserting, so the scroll
    position survives a toggle. A disabled row shows an EMPTY box like an
    unticked one -- it genuinely will not run -- and the grey foreground is
    what separates "unavailable" from "you turned it off".
    """
    _recompute_disabled(st, all_names)
    plb = st.listbox
    for i in range(plb.size()):
        n = _name_at(plb, i)
        plb.delete(i)
        plb.insert(i, (TICK if n in st.checked else UNTICK) + n)
        plb.itemconfigure(i, foreground=(CLR["subtext"] if n in st.disabled
                                         else CLR["text"]))
    refresh_worldspaces()


def _drag_press(plb, drag, e) -> None:
    """Start a potential drag on the row under the cursor."""
    drag["from"] = plb.nearest(e.y)
    drag["moved"] = False
    plb.selection_clear(0, tk.END)
    plb.selection_set(drag["from"])


def _drag_motion(plb, drag, e) -> None:
    """Move one row at a time so the list follows the cursor."""
    src = drag["from"]
    if src is None:
        return
    dst = plb.nearest(e.y)
    if dst < 0 or dst == src:
        return
    item = plb.get(src)
    plb.delete(src)
    plb.insert(dst, item)
    plb.selection_clear(0, tk.END)
    plb.selection_set(dst)
    drag["from"] = dst
    drag["moved"] = True


def _bind_drag(st, all_names, redraw) -> None:
    """Wire click-to-toggle and drag-to-reorder onto the plugin list.

    `moved` separates a click from a drag: without it every reorder would also
    flip the tick of the row it started on.
    """
    plb = st.listbox
    drag = {"from": None, "moved": False}

    def _press(e):
        """Begin a drag or a click."""
        _drag_press(plb, drag, e)

    def _motion(e):
        """Reorder as the cursor moves."""
        _drag_motion(plb, drag, e)

    def _release(e):
        """A click (not a drag) toggles the row, unless it is greyed."""
        i = drag["from"]
        drag["from"] = None
        if i is None or drag["moved"] or i < 0 or i >= plb.size():
            return
        n = _name_at(plb, i)
        if n in st.disabled:
            return
        st.wanted.discard(n) if n in st.wanted else st.wanted.add(n)
        redraw()
        plb.selection_clear(0, tk.END)
        plb.selection_set(i)

    plb.bind("<Button-1>", _press)
    plb.bind("<B1-Motion>", _motion)
    plb.bind("<ButtonRelease-1>", _release)


def _build_plugin_column(parent, st, all_names, redraw):
    """The left column: an ordered, tickable plugin list.

    A Listbox rather than a stack of Checkbuttons: it already gives index
    hit-testing (`nearest`), selection and scrolling, which is exactly what a
    drag-reorder needs.
    """
    left = tk.Frame(parent, bg=CLR["panel"])
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    lhead = tk.Frame(left, bg=CLR["panel"])
    lhead.pack(fill=tk.X)
    tk.Label(lhead, text="Plugins", bg=CLR["panel"], fg=CLR["text"],
             font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

    st.listbox = tk.Listbox(
        left, bg=CLR["log_bg"], fg=CLR["text"], selectbackground=CLR["accent"],
        selectforeground="#ffffff", highlightthickness=0, borderwidth=0,
        activestyle="none", font=("Segoe UI", 9), width=34,
        height=min(14, max(5, len(st.ordered))), exportselection=False)
    lsb = ttk.Scrollbar(left, orient="vertical", command=st.listbox.yview)
    st.listbox.configure(yscrollcommand=lsb.set)
    st.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(4, 0))
    lsb.pack(side=tk.RIGHT, fill=tk.Y, pady=(4, 0))

    def _set_all(on: bool):
        """Tick or clear every plugin."""
        st.wanted.update(all_names) if on else st.wanted.clear()
        redraw()

    ttk.Button(lhead, text="All", width=4,
               command=lambda: _set_all(True)).pack(side=tk.RIGHT, padx=(4, 0))
    ttk.Button(lhead, text="None", width=5,
               command=lambda: _set_all(False)).pack(side=tk.RIGHT)
    _bind_drag(st, all_names, redraw)


def _lost_export_warning(st, names):
    """The amber notice for a plugin whose export has gone missing.

    Must be visible even when other plugins filled the list: shipped LOD is
    the authority, so "offers nothing" is ordinary for most of the load order
    and cannot itself be the trigger -- but a deleted export looks the same,
    and warning only when NOTHING was offered let one healthy plugin hide it.
    """
    lost = [st.ws_why[n] for n in names
            if n in st.ws_why and "run the Export stage" in st.ws_why[n]]
    if not lost:
        return
    more = f"\n\n(+{len(lost) - 4} more)" if len(lost) > 4 else ""
    tk.Label(st.inner, text="\n\n".join(lost[:4]) + more, bg=CLR["panel"],
             fg=CLR["yellow"], justify=tk.LEFT, wraplength=210,
             font=("Segoe UI", 9)).pack(anchor="w", padx=4, pady=(4, 6))


def _refresh_worldspaces(st) -> None:
    """Rebuild the worldspace list from the currently ticked plugins.

    A worldspace exists in this run only because some selected plugin ships
    LOD for it, so unticking that plugin must remove it. Ticks are saved to
    `ws_state` before the teardown, so a worldspace that disappears and
    returns comes back exactly as the user left it.
    """
    for wname, wvar in st.ws_vars:
        st.ws_state[wname] = bool(wvar.get())

    names = [n for n in (_name_at(st.listbox, i)
                         for i in range(st.listbox.size()))
             if n in st.checked]
    live = st.merge(names, st.ws_by)
    authored = set(live)
    if st.force.get():
        live += [w for w in st.merge(names, st.all_by) if w not in authored]

    for child in st.inner.winfo_children():
        child.destroy()
    st.ws_vars.clear()

    if not live:
        tk.Label(st.inner, text=_why_no_worldspaces(names, st.ws_why),
                 bg=CLR["panel"], fg=CLR["subtext"], justify=tk.LEFT,
                 wraplength=210, font=("Segoe UI", 9)).pack(anchor="w", padx=4,
                                                            pady=4)
        return

    _lost_export_warning(st, names)
    for wname in live:
        var = tk.BooleanVar(value=st.ws_state.get(wname, wname in authored))
        st.ws_vars.append((wname, var))
        ttk.Checkbutton(st.inner, text=wname, variable=var,
                        style="TCheckbutton").pack(anchor="w", padx=4, pady=1)


def _build_worldspace_column(parent, card, st, bound) -> None:
    """The right column: an unordered worldspace filter."""
    right = tk.Frame(parent, bg=CLR["panel"])
    right.grid(row=0, column=1, sticky="nsew")
    rhead = tk.Frame(right, bg=CLR["panel"])
    rhead.pack(fill=tk.X)
    tk.Label(rhead, text="Worldspaces", bg=CLR["panel"], fg=CLR["text"],
             font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

    ws_frame = tk.Frame(right, bg=CLR["panel"])
    ws_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
    wcanvas = tk.Canvas(ws_frame, bg=CLR["panel"], highlightthickness=0,
                        width=220, height=300)
    wsb = ttk.Scrollbar(ws_frame, orient="vertical", command=wcanvas.yview)
    st.inner = tk.Frame(wcanvas, bg=CLR["panel"])
    st.inner.bind("<Configure>",
                  lambda e: wcanvas.configure(scrollregion=wcanvas.bbox("all")))
    wcanvas.create_window((0, 0), window=st.inner, anchor="nw")
    wcanvas.configure(yscrollcommand=wsb.set)
    wcanvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    wsb.pack(side=tk.RIGHT, fill=tk.Y)
    ttk.Checkbutton(right, text="Force: offer every worldspace,\neven ones "
                                "with no original LOD", variable=st.force,
                    command=lambda: _refresh_worldspaces(st),
                    style="TCheckbutton").pack(anchor="w", pady=(4, 0))
    card.bind_all("<MouseWheel>",
                  lambda e: wcanvas.yview_scroll(-1 if e.delta > 0 else 1,
                                                 "units"))
    bound.append(True)

    ttk.Button(rhead, text="All", width=4,
               command=lambda: [v.set(True) for _, v in st.ws_vars]).pack(
                   side=tk.RIGHT, padx=(4, 0))
    ttk.Button(rhead, text="None", width=5,
               command=lambda: [v.set(False) for _, v in st.ws_vars]).pack(
                   side=tk.RIGHT)


def _lod_header(card) -> None:
    """The Create-LOD title, blurb and rule."""
    tk.Label(card, text="Create LOD", bg=CLR["panel"], fg=CLR["text"],
             font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16,
                                                 pady=(14, 0))
    tk.Label(card, text=LOD_BLURB, bg=CLR["panel"], fg=CLR["subtext"],
             justify=tk.LEFT, font=("Segoe UI", 9)).pack(anchor="w", padx=16,
                                                         pady=(4, 0))
    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)


def _lod_buttons(app, card, st, all_names, redraw, close, on_generate) -> None:
    """The Reset / Cancel / Generate row."""
    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)
    btns = tk.Frame(card, bg=CLR["panel"])
    btns.pack(fill=tk.X, padx=16, pady=(0, 14))

    def _reset():
        """Back to the derived default: everything on, default order."""
        st.listbox.delete(0, tk.END)
        for n in default_lod_plugins(app):
            st.listbox.insert(tk.END, n)
        st.wanted.clear()
        st.wanted.update(all_names)
        authored = set(st.merge(all_names, st.ws_by))
        st.ws_state = {w: True for w in authored}
        st.ws_vars.clear()
        st.force.set(False)
        redraw()

    def _generate():
        """Store the confirmed selection and hand off.

        Stored even when it equals the default: the user having LOOKED and
        approved is itself information, and it keeps the next run stable if
        plugins.txt changes in between.
        """
        plugins = [n for n in (_name_at(st.listbox, i)
                               for i in range(st.listbox.size()))
                   if n in st.checked]
        worlds = [w for w, v in st.ws_vars if v.get()]
        if not plugins:
            app.info("No Plugins",
                     "Tick at least one plugin to generate LOD for.")
            return
        if st.ws_vars and not worlds:
            app.info("No Worldspaces",
                     "Tick at least one worldspace to generate LOD for.")
            return
        app.selection.lod_plugins[:] = plugins
        app.selection.lod_worldspaces[:] = worlds
        close()
        if on_generate is not None:
            on_generate(plugins, worlds)

    ttk.Button(btns, text="Reset", command=_reset).pack(side=tk.LEFT)
    ttk.Button(btns, text="Generate", style="Accent.TButton",
               command=_generate).pack(side=tk.RIGHT)
    ttk.Button(btns, text="Cancel", command=close).pack(side=tk.RIGHT,
                                                        padx=(0, 6))


def open_create_lod_panel(app, on_generate=None) -> None:
    """Pick the plugins (left, ordered) and worldspaces (right) to build.

    Two lists rather than one because they answer different questions. The
    plugin list is ORDERED -- its order decides who wins a contested tile -- so
    it is a drag-reorder list with a tick per row. The worldspace list is an
    unordered filter, so it is plain checkboxes.

    `on_generate` is called with (plugins, worldspaces) when Generate is
    pressed; None makes this a pure editor of the saved selection.
    """
    all_names = default_lod_plugins(app)
    if not all_names:
        _empty_card(app, "Create LOD",
                    "Nothing converted yet — convert a plugin first.")
        return

    ordered, wanted = _lod_initial_order(app, all_names)
    st = _LodState(ordered, wanted, *_scan_lod_inputs(app, all_names))
    st.ws_state = {w: True for w in default_lod_worldspaces(app, all_names)}
    saved = app.selection.lod_worldspaces
    st.force = tk.BooleanVar(value=any(w not in st.ws_state for w in saved))
    if saved:
        for w in set(st.ws_state) | set(saved):
            st.ws_state[w] = w in saved

    card, close, bound = _card(app)
    _lod_header(card)
    cols = tk.Frame(card, bg=CLR["panel"])
    cols.pack(fill=tk.BOTH, expand=True, padx=16)
    cols.columnconfigure(0, weight=1, uniform="lodcol")
    cols.columnconfigure(1, weight=1, uniform="lodcol")

    def _paint():
        """Repaint both columns."""
        _redraw(st, all_names, lambda: _refresh_worldspaces(st))

    _build_plugin_column(cols, st, all_names, _paint)
    _build_worldspace_column(cols, card, st, bound)
    for n in ordered:
        st.listbox.insert(tk.END, n)
    _paint()
    _lod_buttons(app, card, st, all_names, _paint, close, on_generate)
    _show(app, card)
