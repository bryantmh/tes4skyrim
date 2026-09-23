"""The generic widget kit: dialogs, the sidebar scroller, tooltips, path rows.

Nothing here knows what the converter does. Each function takes the `GuiApp`
carrier, reads the widgets it needs off it, and binds back what it creates --
so the kit can be wired before any feature that uses it exists.

Dialogs are drawn as cards rather than via `tkinter.messagebox`, whose native
OS dialogs ignore this palette and flash white in a dark UI. SEP_GAP is owned
by the separator so the gap above a rule always equals the gap below it;
blocks either side pack flush against it.

THE PLUGIN COMBOBOX. ttk offers no editable combobox with a filtered dropdown,
so `build` drives the Tcl popdown directly. Two stock behaviours have to be
undone for "popup open" and "entry typable" to stop being mutually exclusive:
the listbox bindtag grabs focus on <Map> with `focus -force`, and on win32 it
cancels the popup on <FocusOut>. `_BINDTAG_PATCH` swaps in a private bindtag
keeping only the selection bindings, which drops both while leaving the
popdown's global grab untouched so clicking the list still works.
`_sync_values` is wired to -postcommand as well as called directly, because
ttk::combobox::Post re-reads -values via ConfigureListbox AFTER the postcommand
runs. Only text the user actually TYPED counts as a search term, so a committed
name never filters the list down to itself. Switching plugin starts that
plugin's step selection over -- the ticks are per-plugin state -- and the
comparison is case-insensitive, because the combo and a typed name differ in
case often enough that a raw compare would read as a switch and wipe it.

See: docs/reference/pipeline.md#configuration
"""

import threading
import tkinter as tk
from tkinter import filedialog, ttk

from core.gui.config import CLR, REPO_ROOT

#: Vertical padding a sidebar separator owns on both sides.
SEP_GAP = 12

#: Bindtag carrying the sidebar wheel handler across the whole subtree.
_SB_WHEEL_TAG = "SidebarWheel"


# ---------------------------------------------------------------------------
#  Dialogs
# ---------------------------------------------------------------------------

def open_url(url: str) -> None:
    """Open `url` in the default browser, off the UI thread.

    webbrowser can shell out on some platforms, and nothing on the UI thread
    may block: a slow browser launch must not freeze the window.
    """
    def _go():
        """Launch the browser, ignoring any platform failure."""
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def _dialog_body(card, title: str, message: str, links, status=None) -> None:
    """Fill `card` with the title, rule, any status line, message and links.

    `status` is (text, color): an outcome in large bold type under the rule,
    so the result reads at a glance before the details.
    """
    tk.Label(card, text=title, bg=CLR["panel"], fg=CLR["text"],
             font=("Segoe UI", 10, "bold")).pack(anchor="w",
                                                 padx=16, pady=(14, 0))
    ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)
    if status:
        tk.Label(card, text=status[0], bg=CLR["panel"], fg=status[1],
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=16,
                                                     pady=(0, 8))
    tk.Label(card, text=message, bg=CLR["panel"], fg=CLR["subtext"],
             font=("Segoe UI", 9), justify=tk.LEFT, anchor="w",
             wraplength=380).pack(anchor="w", padx=16,
                                  pady=(0, 6 if links else 12))
    for text, url in links:
        lnk = tk.Label(card, text=text, bg=CLR["panel"], fg=CLR["accent_hover"],
                       font=("Segoe UI", 9, "underline"), cursor="hand2",
                       anchor="w")
        lnk.pack(anchor="w", padx=16, pady=1)
        lnk.bind("<Button-1>", lambda _e, u=url: open_url(u))
    if links:
        tk.Frame(card, bg=CLR["panel"], height=6).pack(fill=tk.X)


def dialog(app, title: str, message: str, buttons=("OK",),
           default: int = 0, links=(), status=None) -> str:
    """Modal message card in the app's palette; returns the button clicked.

    Placed OVER the window rather than behind a full-size backdrop, which would
    cover the log and sidebar and read as the app disappearing. `links` is
    [(label, url)] rendered as clickable rows under the message; `status` is
    an optional (text, color) outcome line.
    """
    result = [buttons[-1] if len(buttons) > 1 else buttons[0]]
    card = tk.Frame(app.outer, bg=CLR["panel"],
                    highlightbackground=CLR["border"], highlightthickness=1)

    def _close(choice: str):
        """Record the answer and tear the card down."""
        result[0] = choice
        card.grab_release()
        card.destroy()

    _dialog_body(card, title, message, links, status)

    btn_row = tk.Frame(card, bg=CLR["panel"])
    btn_row.pack(fill=tk.X, padx=16, pady=(0, 14))
    for i, label in enumerate(reversed(buttons)):
        is_default = (len(buttons) - 1 - i) == default
        ttk.Button(btn_row, text=label, width=10,
                   style="Accent.TButton" if is_default else "TButton",
                   command=lambda l=label: _close(l)).pack(
                       side=tk.RIGHT, padx=(6, 0))

    card.update_idletasks()
    card.place(in_=app.outer, anchor="center", relx=0.5, rely=0.5)
    card.lift()
    card.bind("<Escape>", lambda _e: _close(buttons[-1]))
    card.focus_set()
    card.grab_set()
    app.root.wait_window(card)
    return result[0]


def info(app, title: str, message: str, links=(), status=None) -> None:
    """Show a one-button message card, with an optional (text, color) status."""
    dialog(app, title, message, links=links, status=status)


def confirm(app, title: str, message: str,
            yes: str = "Yes", no: str = "No") -> bool:
    """True when the user picks `yes`."""
    return dialog(app, title, message, buttons=(yes, no)) == yes


# ---------------------------------------------------------------------------
#  Sidebar scroller
# ---------------------------------------------------------------------------

def _sync_scrollbar(app, vsb, shown, pending) -> None:
    """Match the scrollregion to the content and show/hide the scrollbar.

    The content column keeps a constant width in both states: the scrollbar
    would otherwise steal ~17px from it and reflow the two-column Global rows
    the moment it appeared.
    """
    pending[0] = False
    try:
        need = app.sb_body.winfo_reqheight()
        have = app.sb_canvas.winfo_height()
        app.sb_canvas.configure(
            scrollregion=(0, 0, app.sb_canvas.winfo_width(), need))
        overflow = need > have
        if overflow != shown[0]:
            shown[0] = overflow
            if overflow:
                vsb.grid(row=0, column=1, sticky="ns")
            else:
                vsb.grid_remove()
            app.outer.columnconfigure(
                0, minsize=330 + (vsb.winfo_reqwidth() if overflow else 0))
        if not overflow:
            app.sb_canvas.yview_moveto(0)
    except tk.TclError:
        pass


def build_scroller(app):
    """Make the sidebar body scroll; binds `sb_canvas`, `sb_body`, the wheel.

    Every sidebar block is a fixed height, so a window shorter than their sum
    used to clip the MIDDLE ones: pack runs off the end of the frame, and the
    status row is packed side=BOTTOM so it wins that contest. The progress bar
    and status row stay OUT of the viewport deliberately -- they report what
    the app is doing right now.
    """
    app.sidebar.rowconfigure(0, weight=1)
    app.sidebar.columnconfigure(0, weight=1)

    app.sb_canvas = tk.Canvas(app.sidebar, bg=CLR["panel"],
                              highlightthickness=0, bd=0, takefocus=0)
    app.sb_canvas.grid(row=0, column=0, sticky="nsew")
    vsb = ttk.Scrollbar(app.sidebar, orient="vertical",
                        command=app.sb_canvas.yview)
    app.sb_canvas.configure(yscrollcommand=vsb.set)

    app.sb_body = ttk.Frame(app.sb_canvas, style="Panel.TFrame")
    win = app.sb_canvas.create_window((0, 0), window=app.sb_body, anchor="nw")

    shown = [False]
    pending = [False]

    def _schedule(_e=None):
        """Collapse a burst of <Configure> events into one idle sync."""
        if not pending[0]:
            pending[0] = True
            app.sb_canvas.after_idle(
                lambda: _sync_scrollbar(app, vsb, shown, pending))

    def _canvas_configure(event):
        """Give the canvas window the canvas's own width."""
        app.sb_canvas.itemconfigure(win, width=event.width)
        _schedule()

    app.sb_canvas.bind("<Configure>", _canvas_configure)
    app.sb_body.bind("<Configure>", _schedule)

    def _wheel(event):
        """Scroll the sidebar, or pass the wheel on when it does not overflow."""
        if not shown[0]:
            return None
        app.sb_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    app.root.bind_class(_SB_WHEEL_TAG, "<MouseWheel>", _wheel)
    app.sb_tag_wheel = tag_wheel
    return app.sb_body


def tag_wheel(widget) -> None:
    """Give `widget` and everything under it the sidebar wheel handler.

    Tk delivers <MouseWheel> to the widget under the pointer and then walks its
    BINDTAGS -- it does not bubble to ancestors -- so binding the canvas alone
    leaves the wheel dead over every checkbox in the sidebar.
    """
    tags = widget.bindtags()
    if _SB_WHEEL_TAG not in tags:
        widget.bindtags(tags + (_SB_WHEEL_TAG,))
    for child in widget.winfo_children():
        tag_wheel(child)


# ---------------------------------------------------------------------------
#  Small parts
# ---------------------------------------------------------------------------

def sep(parent) -> None:
    """A horizontal rule owning the whole gap around it."""
    ttk.Separator(parent, orient=tk.HORIZONTAL).pack(
        fill=tk.X, padx=14, pady=SEP_GAP)


def section(parent, text: str) -> None:
    """A small heading above a sidebar block."""
    f = ttk.Frame(parent, style="Panel.TFrame")
    f.pack(fill=tk.X, padx=14, pady=(6, 2))
    ttk.Label(f, text=text, style="PanelSub.TLabel").pack(anchor="w")


def tooltip_window(widget, text: str, width: int, at=None):
    """Create a tip popup and return it, clamped to the screen.

    `at` is the screen (x, y) of its top-left corner; by default it sits
    below-right of the cursor. Topmost, so it shows over a native popup menu.
    """
    win = tk.Toplevel(widget)
    win.wm_overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=CLR["border"])
    tk.Label(
        win, text=text, justify="left", wraplength=width,
        bg=CLR["log_bg"], fg=CLR["text"],
        font=("Segoe UI", 9), padx=8, pady=6,
    ).pack(padx=1, pady=1)
    x, y = at or (widget.winfo_pointerx() + 14, widget.winfo_pointery() + 18)
    win.update_idletasks()
    sw = win.winfo_screenwidth()
    if x + win.winfo_width() > sw:
        x = max(0, sw - win.winfo_width() - 4)
    win.wm_geometry(f"+{x}+{y}")
    return win


def attach_tooltip(widget, text: str, width: int = 340):
    """Show `text` while the cursor rests on `widget`; returns a text setter.

    Widgets whose tip changes (the Upgrade button) MUST use the returned setter
    rather than calling this again: the bindings are `add="+"`, so re-attaching
    stacks another set of handlers and leaks a popup per call.
    """
    state = {"win": None, "after": None, "text": text}

    def _hide(*_):
        """Cancel any pending show and destroy a visible popup."""
        if state["after"] is not None:
            widget.after_cancel(state["after"])
            state["after"] = None
        if state["win"] is not None:
            state["win"].destroy()
            state["win"] = None

    def _show():
        """Put the popup up, unless one already is."""
        state["after"] = None
        if state["win"] is None:
            state["win"] = tooltip_window(widget, state["text"], width)

    def _enter(_=None):
        """Arm the brief hover delay before showing."""
        _hide()
        state["after"] = widget.after(450, _show)

    widget.bind("<Enter>", _enter, add="+")
    widget.bind("<Leave>", _hide, add="+")
    widget.bind("<Button-1>", _hide, add="+")
    widget.bind("<Destroy>", _hide, add="+")

    def _set_text(new_text: str):
        """Retarget the tip, closing it if it is already up."""
        state["text"] = new_text
        if state["win"] is not None:
            _hide()

    return _set_text


def path_row(parent, label_text: str, var: tk.StringVar,
             browse_dir=True, on_change=None):
    """A labelled Entry + Browse button row; returns the Entry.

    No pady on the label: every field block owns its spacing via its frame's
    pady, and a second source here made the four blocks sit at four different
    intervals.
    """
    ttk.Label(parent, text=label_text, style="PanelSub.TLabel").pack(anchor="w")
    row = ttk.Frame(parent, style="Panel.TFrame")
    row.pack(fill=tk.X)
    row.columnconfigure(0, weight=1)
    entry = ttk.Entry(row, textvariable=var, width=26)
    entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

    def _browse():
        """Ask for a folder or a file and store the pick."""
        ask = filedialog.askdirectory if browse_dir else filedialog.askopenfilename
        path = ask(initialdir=var.get() or str(REPO_ROOT),
                   title=f"Select {label_text}")
        if path:
            var.set(path)
            if on_change:
                on_change(path)

    ttk.Button(row, text="...", command=_browse, width=3).grid(row=0, column=1)
    return entry

# ---------------------------------------------------------------------------
#  The plugin combobox
# ---------------------------------------------------------------------------

#: Keys the dropdown handles itself, never a search fragment.
_NAV_KEYS = frozenset((
    "Up", "Down", "Return", "Escape", "Tab", "Left", "Right", "Home", "End",
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"))

#: Replaces ttk's listbox bindtag, minus the focus grab and win32 cancel.
_BINDTAG_PATCH = """
    bind FilterComboListbox <ButtonRelease-1> {ttk::combobox::LBSelected %W}
    bind FilterComboListbox <Return>          {ttk::combobox::LBSelected %W}
    bind FilterComboListbox <Escape>          {ttk::combobox::LBCancel %W}
    bind FilterComboListbox <Motion>          {ttk::combobox::LBHover %W %x %y}
    bind FilterComboListbox <Destroy>         {ttk::combobox::LBCleanup %W}
"""


class _Combo:
    """One combobox's Tcl handles and the state its handlers share."""

    __slots__ = ("app", "widget", "path", "popdown", "on_scope_change")

    def __init__(self, app, on_scope_change):
        """Cache the Tcl paths; the popdown does not exist until first post."""
        self.app = app
        self.widget = app.file_combo
        self.path = str(app.file_combo)
        self.popdown = f"ttk::combobox::PopdownWindow {self.path}"
        self.on_scope_change = on_scope_change

    def tcl(self, script: str) -> str:
        """Evaluate `script` in the widget's interpreter."""
        return self.widget.tk.eval(script)

    def list_is_open(self) -> bool:
        """True while the dropdown is posted."""
        try:
            return bool(int(self.tcl(f"winfo ismapped [{self.popdown}]")))
        except tk.TclError:
            return False

    def is_disabled(self) -> bool:
        """True while a run has disabled the field."""
        return "disabled" in self.widget.state()

    def search_text(self) -> str:
        """The text acting as the current search term, or ""."""
        if not self.app.searching[0]:
            return ""
        try:
            return self.widget.get()
        except tk.TclError:
            return ""

    def matches(self, typed: str) -> list:
        """Every plugin containing `typed`, or all of them when it is empty."""
        typed = typed.strip().lower()
        plugins = self.app.all_plugins
        return [p for p in plugins if typed in p.lower()] if typed \
            else list(plugins)

    def unpost(self) -> None:
        """Close the dropdown if it is open."""
        if self.list_is_open():
            self.tcl(f"ttk::combobox::Unpost {self.path}")


def _unhijack_listbox(combo) -> None:
    """Swap in the private bindtag so the entry keeps the keyboard.

    Idempotent, and the popdown must already exist.
    """
    listbox = f"{combo.tcl(combo.popdown)}.f.l"
    tags = combo.tcl(f"bindtags {listbox}").split()
    if "ComboboxListbox" not in tags:
        return
    combo.tcl(_BINDTAG_PATCH)
    patched = ["FilterComboListbox" if t == "ComboboxListbox" else t
               for t in tags]
    combo.tcl(f"bindtags {listbox} {{{' '.join(patched)}}}")


def _sync_values(combo) -> None:
    """Point -values at the current search text's matches."""
    combo.widget["values"] = combo.matches(combo.search_text())


def _open_list(combo) -> None:
    """Post the dropdown, keeping the caret in the entry so typing filters."""
    if combo.is_disabled():
        return
    if not combo.list_is_open():
        combo.tcl(f"ttk::combobox::Post {combo.path}")
    _unhijack_listbox(combo)
    combo.widget.focus_force()


def _refresh_list(combo) -> None:
    """Re-filter, and push the new values into an already-posted listbox.

    ConfigureListbox is what -postcommand's values normally flow through;
    calling it directly repopulates the open popup without re-posting, which
    would bounce focus back to the listbox mid-word.
    """
    _sync_values(combo)
    if combo.list_is_open():
        combo.tcl(f"ttk::combobox::ConfigureListbox {combo.path}")
        combo.tcl(f"ttk::combobox::PlacePopdown {combo.path} [{combo.popdown}]")


def _commit(combo, name: str) -> None:
    """Adopt `name` as the selection, resetting the steps if it changed.

    The defaults go back first so the window never shows another plugin's
    selection; the upgrade plan then narrows them to what is outstanding when
    its threaded lookup returns.
    """
    app = combo.app
    app.searching[0] = False
    previous = app.last_valid[0]
    app.last_valid[0] = name
    app.file_var.set(name)
    combo.widget["values"] = list(app.all_plugins)
    if (name or "").strip().lower() != (previous or "").strip().lower():
        app.plan_applied.discard(name)
        app.set_default()
    app.refresh_upgrade_notice()
    combo.on_scope_change()


def _bind_typing(combo) -> None:
    """Bind the keystroke and click handlers that open and filter the list."""
    widget, app = combo.widget, combo.app

    def _on_key(evt):
        """Any non-navigation key means the field holds a search fragment."""
        if evt.keysym in _NAV_KEYS:
            return
        app.searching[0] = True
        _open_list(combo)
        _refresh_list(combo)

    def _on_click(_evt=None):
        """Open the full dropdown, with the name selected for replacement."""
        if combo.is_disabled():
            return "break"
        app.searching[0] = False
        widget.selection_range(0, tk.END)
        widget.icursor(tk.END)
        _open_list(combo)
        _refresh_list(combo)
        return "break"

    def _on_down(_evt=None):
        """Down behaves as a click on the field."""
        _on_click()
        return "break"

    widget.bind("<KeyRelease>", _on_key)
    widget.bind("<Button-1>", _on_click)
    widget.bind("<Down>", _on_down)


def _bind_commit(combo) -> None:
    """Bind the handlers that end a search and settle on a plugin."""
    widget, app = combo.widget, combo.app

    def _on_selected(_evt=None):
        """The listbox picked something."""
        _commit(combo, app.file_var.get())
        widget.selection_clear()

    def _on_return(_evt=None):
        """Commit the exact match, else the first, so search is keyboard-only."""
        found = combo.matches(combo.search_text())
        typed = widget.get().strip().lower()
        exact = next((p for p in app.all_plugins if p.lower() == typed), None)
        if exact:
            _commit(combo, exact)
        elif found:
            _commit(combo, found[0])
        combo.unpost()
        return "break"

    def _on_escape(_evt=None):
        """Abandon the search and restore the last committed name."""
        combo.unpost()
        if app.last_valid[0]:
            _commit(combo, app.last_valid[0])
        return "break"

    def _on_focus_out(_evt=None):
        """Snap back to a real plugin, never leaving a fragment in the field.

        Focus bouncing to our own dropdown is not the user leaving.
        """
        if combo.list_is_open():
            return
        typed = widget.get().strip()
        exact = next((p for p in app.all_plugins
                      if p.lower() == typed.lower()), None)
        _commit(combo, exact if exact else (app.last_valid[0] or ""))

    widget.bind("<Return>", _on_return)
    widget.bind("<Escape>", _on_escape)
    widget.bind("<<ComboboxSelected>>", _on_selected)
    widget.bind("<FocusOut>", _on_focus_out)


def _seed_selection(app, initial_plugins) -> None:
    """Pick a starting plugin, preferring Oblivion.esm when it is present.

    The fallback is seeded with whatever ends up selected, so a focus-out
    before the first pick still has a real name to snap back to.
    """
    if initial_plugins and not app.file_var.get():
        preferred = next((p for p in initial_plugins
                          if p.lower() == "oblivion.esm"), None)
        app.file_var.set(preferred or initial_plugins[0])
    app.last_valid[0] = app.file_var.get()


def build_plugin_combo(app, initial_plugins, on_scope_change):
    """Wire type-ahead onto `app.file_combo`; binds `app.commit_plugin`.

    `on_scope_change` runs after a commit, to re-read what the newly selected
    plugin can actually run.
    """
    combo = _Combo(app, on_scope_change)
    combo.widget.configure(postcommand=lambda: _sync_values(combo))
    _bind_typing(combo)
    _bind_commit(combo)
    _seed_selection(app, initial_plugins)
    app.commit_plugin = lambda name: _commit(combo, name)
    return app.commit_plugin
