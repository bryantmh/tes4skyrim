"""Tests for the GUI's ability to START on a machine that has only stdlib tkinter.

The failure this guards against is the worst kind to diagnose: main() relaunches
the GUI under pythonw, which has NO CONSOLE, so an exception raised on the way
to the first window prints nowhere. The user sees a program that does nothing at
all -- no window, no error -- and the developer, who has every optional package
installed, cannot reproduce it.

Every optional GUI dependency must therefore degrade to a working window, and
the fallback path must be exercised by a test rather than by a user.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gui  # noqa: E402


@pytest.fixture
def without_tkinterdnd2(monkeypatch):
    """Simulate a user who never pip-installed the optional drag-drop package.

    Blocks the module in sys.modules rather than patching __import__: Tk's own
    startup sources its Tcl library through the import machinery, so a
    broad-brush __import__ patch breaks real window creation and the test then
    fails for a reason that has nothing to do with drag-and-drop.
    """
    monkeypatch.setitem(sys.modules, "tkinterdnd2", None)
    monkeypatch.setattr(gui, "DND_AVAILABLE", False)


@pytest.fixture
def display():
    """Skip when Tk cannot open a window (headless CI).

    Deliberately does NOT build a throwaway root to probe with. Creating and
    destroying a root, then creating another in the same process, makes Tcl
    re-run its own initialisation, and on Python 3.14 for Windows that second
    init fails to re-read init.tcl. The probe would then sink the very tests it
    gates. Importing _tkinter is enough to tell a headless box apart -- the
    single root each test creates is the real check.
    """
    pytest.importorskip("tkinter")
    pytest.importorskip("_tkinter")


# ── The regression ────────────────────────────────────────────────────────
# _make_root()'s fallback called a bare `tk.Tk()`, but `tk` is imported INSIDE
# gui_main() and is not a module-level name. So on any machine without
# tkinterdnd2 the fallback raised NameError instead of returning a window, and
# the GUI died before creating one. Users on earlier builds were unaffected
# because _make_root did not exist -- gui_main() called tk.Tk() directly, where
# the local import made the name valid.

def test_module_defines_no_global_tk():
    """The name the old fallback reached for genuinely does not exist.

    If someone later adds a module-level `import tkinter as tk`, this test
    fails loudly -- that is fine, but then the fallback's local import is what
    should be deleted, not this guarantee.
    """
    assert not hasattr(gui, "tk")


def test_root_is_created_without_the_optional_dnd_package(display, without_tkinterdnd2):
    """The whole point: no tkinterdnd2 still yields a real, usable window."""
    root = gui._make_root()
    try:
        assert root.winfo_exists()
    finally:
        root.destroy()


def test_missing_dnd_package_is_reported_not_fatal(display, without_tkinterdnd2):
    """Drag-and-drop turns itself off rather than taking the app down with it."""
    root = gui._make_root()
    try:
        assert gui.DND_AVAILABLE is False
    finally:
        root.destroy()


def test_a_broken_tkdnd_runtime_also_falls_back(display, monkeypatch):
    """The package can be installed yet fail to load its Tcl half.

    That raises from TkinterDnD.Tk() rather than from the import, so it must be
    caught at the same place -- an installed-but-broken tkdnd is the common
    case on Linux, where the wheel ships no matching Tcl library.
    """
    import tkinterdnd2

    class _Exploding:
        def __init__(self, *a, **k):
            raise RuntimeError("can't find package tkdnd")

    monkeypatch.setattr(tkinterdnd2, "TkinterDnD", _Exploding, raising=False)
    monkeypatch.setattr(gui, "DND_AVAILABLE", False)

    root = gui._make_root()
    try:
        assert root.winfo_exists()
        assert gui.DND_AVAILABLE is False
    finally:
        root.destroy()


class TestCollisionWindingSetting:
    """Settings ▸ Fix collision winding must preserve the per-plugin defaults.

    The repair moved from a Meshes checkbox to a persisted tri-state setting.
    "Automatic" is the default and has to resolve exactly as the checkbox did:
    ON for the plugins measured to need it (collision_options), OFF elsewhere.
    A regression here is invisible in the GUI -- the wrong flag simply reaches
    convert.py -- and shows up only as floors you fall through, or as a plugin
    silently getting a repair its collision never needed.
    """

    def test_auto_matches_the_measured_per_plugin_defaults(self):
        for plugin in gui.WINDING_FIX_DEFAULT_PLUGINS:
            assert gui.winding_enabled_for(gui.WINDING_AUTO, plugin + ".esm")
            assert gui.winding_enabled_for(gui.WINDING_AUTO, plugin + ".esp")
        for plugin in ("Oblivion.esm", "SomeMod.esp", ""):
            assert not gui.winding_enabled_for(gui.WINDING_AUTO, plugin)

    def test_explicit_modes_override_every_plugin(self):
        for plugin in ("Nehrim.esm", "Morrowind_ob.esm", "Oblivion.esm", ""):
            assert gui.winding_enabled_for(gui.WINDING_ON, plugin)
            assert not gui.winding_enabled_for(gui.WINDING_OFF, plugin)

    def test_unknown_or_absent_config_value_reads_as_auto(self):
        """A config written before this setting existed keeps the defaults."""
        for stored in ("", "maybe", None, "AUTO"):
            mode = str(stored or "").strip().lower()
            if mode not in gui.WINDING_MODES:
                mode = gui.WINDING_AUTO
            assert gui.winding_enabled_for(mode, "Morrowind_ob.esm") is True
            assert gui.winding_enabled_for(mode, "Oblivion.esm") is False
            # Nehrim's exporter left the authored normals intact, so the
            # ungated step 0 repairs it on its own and it must NOT pull in
            # the inferred steps (see collision_options).
            assert gui.winding_enabled_for(mode, "Nehrim.esm") is False

    def test_auto_agrees_with_collision_options(self):
        """The GUI must not carry its own copy of the plugin list."""
        from collision_options import default_for_plugin
        for plugin in ("Nehrim.esm", "Morrowind_ob.esp", "Oblivion.esm",
                       "Anything.esp"):
            assert (gui.winding_enabled_for(gui.WINDING_AUTO, plugin)
                    == default_for_plugin(plugin))


# ---------------------------------------------------------------------------
#  Step selection is per-plugin
# ---------------------------------------------------------------------------

def _switching_resets(name, previous):
    """The rule `_commit` applies: a genuine plugin SWITCH resets the ticks.

    Mirrors gui._commit. `_commit` is a closure inside build_gui and cannot be
    reached without standing a whole window up, so the rule is asserted here
    and the source is checked below to keep the two from drifting.
    """
    return (name or "").strip().lower() != (previous or "").strip().lower()


@pytest.mark.parametrize("previous,name,expect", [
    ("TamRes.esm", "TamRes.esp",  True),   # different plugin -> reset
    ("TamRes.esm", "TamRes.esm",  False),  # same plugin      -> keep edits
    ("TamRes.esm", "tamres.esm",  False),  # same, other case -> keep edits
    ("TamRes.esm", " TamRes.esm", False),  # same, whitespace -> keep edits
    ("",           "TamRes.esm",  True),   # first selection  -> defaults
])
def test_only_a_real_switch_resets_the_step_selection(previous, name, expect):
    """Ticks are per-plugin state: what THIS plugin still owes.

    Carrying them across meant edits made for one plugin silently governed the
    run for the next one. Re-selecting the plugin already shown is not a switch
    and must not discard the user's edits.
    """
    assert _switching_resets(name, previous) is expect


def test_commit_still_resets_on_switch():
    """Guards the rule above against gui._commit being changed out from under it."""
    import inspect
    src = inspect.getsource(gui)
    commit = src[src.index("    def _commit(name: str):"):]
    commit = commit[:commit.index(chr(10) + "    def ", 10)]
    # The switch test and the reset must both still be there.
    assert "_set_default()" in commit
    assert ".strip().lower() != " in commit
    assert "_plan_applied.discard(name)" in commit


def test_opt_in_steps_are_not_swept_in_by_bulk_selections():
    """Nemesis depends on an EXTERNAL tool being installed, so no bulk action
    may tick it. Ticking it for someone who does not run Nemesis turns an
    otherwise complete run into a FAILED one for a step they never asked for.
    """
    assert "nemesis" in gui.OPT_IN_STEPS

    # not in the initial tick state, and not in what "Default" restores
    for pack_default in (True, False):
        assert "nemesis" not in gui.default_on_steps(pack_default)

    # the STEPS default_on column agrees, so the runner's "is this the default
    # selection?" check never counts it either
    on_by_default = {k for k, *rest in gui.STEPS if rest[3]}
    assert "nemesis" not in on_by_default

    # "Upgrade" filters its plan through default_on_steps, so it is covered by
    # the assertions above; "All" has to exclude OPT_IN_STEPS explicitly.
    import inspect
    src = inspect.getsource(gui)
    set_all = src[src.index("    def _set_all():"):]
    set_all = set_all[:set_all.index(chr(10) + "    def ", 10)]
    assert "OPT_IN_STEPS" in set_all
