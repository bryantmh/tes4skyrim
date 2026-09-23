"""Tests for version.py -- the installed-version stamp and the upgrade-aware
step selection the GUI uses after a user pastes a new build over the old one.

The failure this guards against is silent and expensive in both directions:
selecting too few steps ships stale output the user believes is current, and
selecting too many costs a multi-hour full reconversion for a 3-step change.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import version as v


# ── Tag numbering: two schemes on one scale ───────────────────────────────
# Tags through 0.58 are MAJOR.MM (hundredths); 0.581 onward are MAJOR.MMM.
# Ranking the minor field as a bare int sorts 0.59 below 0.581 and an upgrade
# would silently select nothing.

def test_two_digit_and_three_digit_minors_share_a_scale():
    assert v.version_key("0.58") == v.version_key("0.580")


def test_ordering_across_the_scheme_boundary():
    assert v.version_key("0.581") > v.version_key("0.58")
    assert v.version_key("0.59") > v.version_key("0.581")
    assert v.version_key("1.00") > v.version_key("0.999")


def test_dev_version_string_ranks_as_its_base_tag():
    """A dev checkout reports `<tag>+g<sha>`, e.g. 0.58+ge49ab44.

    It must rank as 0.58, or a developer's upgrade plan resolves against a
    version that does not exist and reports 'unknown' -> re-run everything.
    """
    for described in ("0.58+ge49ab44", "0.58+", "0.58-3-g6c7a351-dirty"):
        assert v.version_key(described) == v.version_key("0.58"), described


def test_exact_tag_is_not_a_dev_version():
    assert v.is_dev_version("0.58") is False
    assert v.is_dev_version("0.581") is False


@pytest.mark.parametrize("described", [
    "0.58+ge49ab44", "0.58+", "0.0-dev",
])
def test_moved_past_a_tag_is_a_dev_version(described):
    assert v.is_dev_version(described) is True


def test_resolving_the_version_never_spawns_a_subprocess():
    """The GUI resolves the version while building its window, and under
    gui.pyw the parent is console-less pythonw.exe -- every spawn there is a
    potential console window.  The GUI is ONE window."""
    import subprocess
    calls = []
    originals = {n: getattr(subprocess, n)
                 for n in ("run", "Popen", "call", "check_output")}
    for name in originals:
        setattr(subprocess, name,
                lambda *a, _n=name, **k: calls.append(_n))
    try:
        v._CURRENT[0] = None
        v.current_version()
    finally:
        for name, fn in originals.items():
            setattr(subprocess, name, fn)
        v._CURRENT[0] = None
    assert calls == [], f"version resolution spawned: {calls}"


# ── Annotated tags must be peeled to their commit ─────────────────────────
# Every release tag in this repo is ANNOTATED, so refs/tags/<v> names a tag
# OBJECT, not the commit.  Comparing that ref to HEAD can never match, so a
# checkout sitting exactly on a release reported `<tag>+g<sha>` and read as a
# dev build ahead of it.  record_step_run stamps that string into the state
# file, so a step run at release 0.586 was recorded as `0.585+g<sha>` -- the
# newest tag known LOCALLY at the time -- which ranks as 0.585 and leaves the
# step looking permanently stale, re-ticking it on every future check.

def _fake_repo(tmp_path, tag, tag_obj_sha, commit_sha, head_sha):
    """A .git with one annotated tag, laid out the way git stores it."""
    import zlib
    git = tmp_path / ".git"
    (git / "refs" / "tags").mkdir(parents=True)
    (git / "refs" / "tags" / tag).write_text(tag_obj_sha, encoding="utf-8")
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "master").write_text(head_sha, encoding="utf-8")
    (git / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")

    body = (f"object {commit_sha}\ntype commit\ntag {tag}\n\nRelease {tag}\n"
            ).encode()
    raw = b"tag " + str(len(body)).encode() + b"\x00" + body
    obj = git / "objects" / tag_obj_sha[:2]
    obj.mkdir(parents=True)
    (obj / tag_obj_sha[2:]).write_bytes(zlib.compress(raw))
    return git


_TAG_OBJ = "cdd431bbef540fc48611612f09f9306c45a14fb5"
_COMMIT  = "0909928f05e5bbb66db7d0d92d0d65bf0d14d4ae"


def test_on_an_annotated_tag_reports_the_bare_release(tmp_path, monkeypatch):
    _fake_repo(tmp_path, "0.586", _TAG_OBJ, _COMMIT, _COMMIT)
    monkeypatch.setattr(v, "SCRIPT_DIR", tmp_path)
    assert v._git_version() == "0.586"
    assert v.is_dev_version("0.586") is False


def test_past_an_annotated_tag_still_reports_a_dev_build(tmp_path, monkeypatch):
    head = "eefacb32f4c3c400a789f6668df3790a275451df"
    _fake_repo(tmp_path, "0.586", _TAG_OBJ, _COMMIT, head)
    monkeypatch.setattr(v, "SCRIPT_DIR", tmp_path)
    got = v._git_version()
    assert got == f"0.586+g{head[:7]}"
    assert v.is_dev_version(got) is True


def test_peeling_an_unreadable_tag_object_falls_back(tmp_path, monkeypatch):
    """A tag packed away by `git gc` cannot be peeled from loose objects.

    That must degrade to the old `+g<sha>` form, never raise -- the GUI resolves
    the version while building its window.
    """
    git = tmp_path / ".git"
    (git / "objects").mkdir(parents=True)
    assert v._peel_tag(git, _TAG_OBJ) == _TAG_OBJ
    assert v._peel_tag(git, "") == ""


def test_a_lightweight_tag_points_straight_at_its_commit(tmp_path, monkeypatch):
    """Peeling must return a commit sha unchanged, not treat it as a tag."""
    import zlib
    git = tmp_path / ".git"
    obj = git / "objects" / _COMMIT[:2]
    obj.mkdir(parents=True)
    raw = b"commit 5\x00hello"
    (obj / _COMMIT[2:]).write_bytes(zlib.compress(raw))
    assert v._peel_tag(git, _COMMIT) == _COMMIT


def test_peeling_never_spawns_a_subprocess(tmp_path, monkeypatch):
    """Same constraint as version resolution: zero spawns under pythonw.exe."""
    import subprocess
    _fake_repo(tmp_path, "0.586", _TAG_OBJ, _COMMIT, _COMMIT)
    monkeypatch.setattr(v, "SCRIPT_DIR", tmp_path)
    calls = []
    for name in ("run", "Popen", "call", "check_output"):
        monkeypatch.setattr(subprocess, name,
                            lambda *a, _n=name, **k: calls.append(_n))
    v._git_version()
    assert calls == []


# ── Update check ──────────────────────────────────────────────────────────

def test_update_check_ignores_non_release_tags(monkeypatch):
    """The repo also carries navmesh-cache-* tags; offering one as an update
    would send the user to a release that is not the converter."""
    monkeypatch.setattr(v, "latest_release", lambda timeout=8: None)
    monkeypatch.setattr(v, "current_version", lambda: "0.58")
    got = v.check_for_update()
    assert got["reachable"] is False and got["available"] is False


def test_update_available_only_when_strictly_newer(monkeypatch):
    monkeypatch.setattr(v, "current_version", lambda: "0.58")
    for latest, expect in (("0.59", True), ("0.58", False), ("0.57", False)):
        monkeypatch.setattr(v, "latest_release", lambda timeout=8, l=latest: l)
        assert v.check_for_update()["available"] is expect, latest


def test_dev_tree_on_the_newest_tag_is_not_behind(monkeypatch):
    """`0.58+ge49ab44` is ahead of 0.58, never behind it."""
    monkeypatch.setattr(v, "current_version", lambda: "0.58+ge49ab44")
    monkeypatch.setattr(v, "latest_release", lambda timeout=8: "0.58")
    assert v.check_for_update()["available"] is False


@pytest.mark.parametrize("bad", ["", "dev", "x.yy", "0", "0.", ".58"])
def test_unparseable_tags_are_rejected(bad):
    assert v.version_key(bad) is None


# ── steps_between: union, and the honest "unknown" ────────────────────────

def _table(monkeypatch, versions, reachable=True):
    """Stand in for the fetched table, bypassing the network entirely."""
    monkeypatch.setattr(v, "steps_table",
                        lambda timeout=8, refresh=False: (versions, reachable))


def test_skipping_releases_unions_every_entry_in_range(monkeypatch):
    """Upgrading 0.50 -> 0.53 owes all three releases' steps, not just 0.53's."""
    _table(monkeypatch, {
        "0.51": ["3. Meshes"],
        "0.52": ["6. Import"],
        "0.53": ["8. Scripts"],
    })
    got = v.steps_between("0.50", "0.53")
    assert got == ["3. Meshes", "6. Import", "8. Scripts"]


def test_result_is_in_run_order_not_table_order(monkeypatch):
    _table(monkeypatch, {"0.51": ["10. Pack Mod Zip", "1. Export", "6. Import"]})
    assert v.steps_between("0.50", "0.51") == [
        "1. Export", "6. Import", "10. Pack Mod Zip"]


def test_releases_outside_the_range_are_excluded(monkeypatch):
    _table(monkeypatch, {
        "0.50": ["1. Export"],      # at/below `from` -- already run
        "0.51": ["3. Meshes"],
        "0.52": ["6. Import"],      # above `to` -- not installed yet
    })
    assert v.steps_between("0.50", "0.51") == ["3. Meshes"]


def test_a_hole_in_the_range_reports_unknown_not_empty(monkeypatch):
    """A missing entry must NOT read as 'nothing changed'.

    None is the caller's signal to select everything.  Returning [] here would
    tell a user with stale output that they are up to date.
    """
    _table(monkeypatch, {"0.51": ["3. Meshes"]})   # 0.52 absent
    assert v.steps_between("0.50", "0.52") is None


def test_missing_table_reports_unknown(monkeypatch):
    _table(monkeypatch, {})
    assert v.steps_between("0.50", "0.52") is None


def test_no_upgrade_owes_nothing(monkeypatch):
    _table(monkeypatch, {"0.51": ["3. Meshes"]})
    assert v.steps_between("0.51", "0.51") == []
    # A downgrade owes nothing either -- there is no forward delta to apply.
    assert v.steps_between("0.52", "0.51") == []


# ── Step keys line up with the GUI and with release_notes ─────────────────

def test_step_keys_match_the_gui_step_table():
    """version.STEP_KEYS must mirror the GUI, or auto-selection ticks nothing.

    The GUI splits its work in two: STEPS are the numbered per-plugin
    checkboxes, GLOBAL_ACTIONS are the buttons that run once for the whole load
    order.  version.STEP_KEYS carries both, steps first and globals last, so
    the concatenation is what has to line up.

    gui.py is imported for those two constants only; it must not need tkinter
    at import time for this to work.
    """
    import gui
    gui_keys   = [k for k, *_ in gui.STEPS] + [k for k, *_ in gui.GLOBAL_ACTIONS]
    gui_labels = [s[2] for s in gui.STEPS] + [g[1] for g in gui.GLOBAL_ACTIONS]
    assert gui_keys   == [k for k, _ in v.STEP_KEYS]
    assert gui_labels == [lbl for _k, lbl in v.STEP_KEYS]


def test_every_global_action_is_a_global_step():
    """A GUI global action is recorded once, not per plugin."""
    import gui
    for key, *_ in gui.GLOBAL_ACTIONS:
        assert key in v.GLOBAL_STEPS


def test_pack_default_setting_gates_the_packing_steps():
    """Settings > Pack by default decides whether the packing pair starts ticked.

    Only that pair moves: turning the setting off must not disturb any other
    step's default.
    """
    import gui
    on  = gui.default_on_steps(True)
    off = gui.default_on_steps(False)
    assert set(gui.PACKING_STEPS) <= on
    assert not (set(gui.PACKING_STEPS) & off)
    assert on - off == set(gui.PACKING_STEPS)


def test_upgrade_plan_cannot_re_tick_packing_when_the_setting_is_off():
    """The upgrade plan must be filtered through the Pack-by-default setting.

    Selecting a plugin auto-applies its upgrade plan, and a plan legitimately
    includes the packing steps whenever packaging code changed.  Applying it
    unfiltered re-ticked the two boxes the setting had just cleared -- the
    setting appeared to do nothing, because merely picking a plugin undid it.
    """
    import gui
    plan_steps = ["export", "meshes", "pack", "pack_zip"]

    ticked = set(plan_steps) & gui.default_on_steps(False)
    assert ticked == {"export", "meshes"}

    # With the setting ON the plan is applied verbatim, so a genuine packaging
    # upgrade still selects the work it owes.
    assert set(plan_steps) & gui.default_on_steps(True) == set(plan_steps)


def test_labels_match_release_notes_step_order():
    """The labels version.py maps are exactly the ones release_notes emits."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import tools.release.release_notes as rn
    assert [lbl for _k, lbl in v.STEP_KEYS] == rn.STEP_ORDER


# ── Conversion state ──────────────────────────────────────────────────────

@pytest.fixture
def state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr(v, "STATE_FILE", path)
    return path


def test_records_and_reads_back_per_plugin(state):
    v.record_step_run("meshes", "Oblivion.esm", "0.55")
    v.record_step_run("import_", "Nehrim.esm", "0.57")
    assert v.steps_run_at("Oblivion.esm") == {"meshes": "0.55"}
    assert v.steps_run_at("Nehrim.esm") == {"import_": "0.57"}


def test_plugin_key_is_case_insensitive(state):
    """The GUI combo and the CLI's -f disagree on case for the same file; two
    spellings must not split one plugin's history in two."""
    v.record_step_run("meshes", "Oblivion.esm", "0.55")
    assert v.steps_run_at("OBLIVION.ESM") == {"meshes": "0.55"}


def test_installed_version_is_the_oldest_step_not_the_newest(state):
    """The upgrade owed is whatever the most out-of-date step owes."""
    v.record_step_run("meshes", "Oblivion.esm", "0.52")
    v.record_step_run("import_", "Oblivion.esm", "0.58")
    assert v.installed_version_for("Oblivion.esm") == "0.52"


def test_unknown_plugin_has_no_installed_version(state):
    assert v.installed_version_for("Never.esm") is None


# ---------------------------------------------------------------------------
#  Plugin-independent steps
# ---------------------------------------------------------------------------

def test_a_global_step_is_recorded_once_not_per_plugin(state):
    v.record_step_run("modify_body_meshes", "Oblivion.esm", "0.586")
    raw = json.loads(state.read_text(encoding="utf-8"))["steps"]
    assert raw[v.GLOBAL_PLUGIN_KEY] == {"modify_body_meshes": "0.586"}
    assert "oblivion.esm" not in raw


def test_a_global_step_counts_for_a_plugin_that_never_ran_it(state, monkeypatch):
    """THE bug: Body Slot Patch re-selected for every plugin but the one it ran
    alongside, even though the single shared patch already existed."""
    monkeypatch.setattr(v, "current_version", lambda: "0.586")
    _table(monkeypatch, {"0.586": []})
    v.record_step_run("modify_body_meshes", "Oblivion.esm", "0.586")
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Nehrim.esm", "0.586")

    assert v.steps_run_at("Nehrim.esm")["modify_body_meshes"] == "0.586"
    assert "modify_body_meshes" not in v.upgrade_plan("Nehrim.esm")["steps"]


def test_a_global_step_is_still_owed_when_its_own_code_changed(state, monkeypatch):
    """Sharing the record must not make the step un-re-runnable."""
    monkeypatch.setattr(v, "current_version", lambda: "0.586")
    _table(monkeypatch, {"0.586": ["Body Slot Patch"]})
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Nehrim.esm", "0.585")
    v.record_step_run("modify_body_meshes", "Oblivion.esm", "0.585")

    assert "modify_body_meshes" in v.upgrade_plan("Nehrim.esm")["steps"]


def test_a_global_step_never_run_at_all_is_still_owed(state, monkeypatch):
    monkeypatch.setattr(v, "current_version", lambda: "0.586")
    _table(monkeypatch, {"0.586": []})
    for key, _ in v.STEP_KEYS:
        if key != "modify_body_meshes":
            v.record_step_run(key, "Nehrim.esm", "0.586")
    assert "modify_body_meshes" in v.upgrade_plan("Nehrim.esm")["steps"]


def test_a_legacy_per_plugin_global_record_is_lifted_to_the_shared_key(state):
    """State written by an older build must not keep re-ticking the step.

    The old scheme stamped it onto whichever plugins were in the run, so it
    survives under one plugin and nowhere else.  One shared artifact means the
    newest such record is the truth for every plugin.
    """
    state.write_text(json.dumps({"steps": {
        "oblivion.esm": {"modify_body_meshes": "0.584"},
        "nehrim.esm":   {"modify_body_meshes": "0.585"},
    }}), encoding="utf-8")
    # The NEWEST legacy record wins, and reaches a plugin that never had one.
    assert v.steps_run_at("Tamriel.esp")["modify_body_meshes"] == "0.585"


def test_lifting_a_legacy_record_never_rewrites_the_file(state):
    """A status query must not mutate the user's state."""
    original = json.dumps({"steps": {
        "oblivion.esm": {"modify_body_meshes": "0.584"}}})
    state.write_text(original, encoding="utf-8")
    v.steps_run_at("Nehrim.esm")
    v.upgrade_plan("Nehrim.esm")
    assert state.read_text(encoding="utf-8") == original


def test_a_newer_per_plugin_record_beats_a_stale_shared_one(state):
    """Neither key is blindly authoritative -- the newer version wins."""
    state.write_text(json.dumps({"steps": {
        v.GLOBAL_PLUGIN_KEY: {"modify_body_meshes": "0.583"},
        "nehrim.esm":        {"modify_body_meshes": "0.586"},
    }}), encoding="utf-8")
    assert v.steps_run_at("Nehrim.esm")["modify_body_meshes"] == "0.586"


def test_a_global_step_records_no_source_directory(state):
    """The shared key belongs to no plugin, so it must not own an install path,
    or `source_path_for` would hand one plugin's directory to another."""
    v.record_step_run("modify_body_meshes", "Oblivion.esm", "0.586",
                      data_path=r"D:\Obliv\Data")
    assert v.source_path_for("Oblivion.esm") is None
    assert v.source_path_for(v.GLOBAL_PLUGIN_KEY) is None


# ── Source directory: plugins do not share one ────────────────────────────

def test_each_plugin_remembers_its_own_data_directory(state):
    """Nehrim and Morrowind_ob live in their own installs, so re-running a
    converted plugin must restore ITS directory, not whichever is configured."""
    v.record_step_run("import_", "Nehrim.esm", "0.55", data_path=r"D:\Nehrim\Data")
    v.record_step_run("import_", "Oblivion.esm", "0.55", data_path=r"D:\Obliv\Data")
    assert v.source_path_for("Nehrim.esm") == r"D:\Nehrim\Data"
    assert v.source_path_for("Oblivion.esm") == r"D:\Obliv\Data"


def test_source_path_is_case_insensitive(state):
    v.record_step_run("import_", "Nehrim.esm", "0.55", data_path=r"D:\Nehrim\Data")
    assert v.source_path_for("NEHRIM.ESM") == r"D:\Nehrim\Data"


def test_source_path_absent_when_never_recorded(state):
    v.record_step_run("import_", "Nehrim.esm", "0.55")   # no data_path
    assert v.source_path_for("Nehrim.esm") is None
    assert v.source_path_for("Unknown.esm") is None


def test_recording_a_source_does_not_disturb_step_versions(state):
    v.record_step_run("meshes", "Nehrim.esm", "0.55", data_path=r"D:\Nehrim\Data")
    assert v.steps_run_at("Nehrim.esm") == {"meshes": "0.55"}


def test_corrupt_state_file_is_survivable(state):
    state.write_text("{not json", encoding="utf-8")
    assert v.steps_run_at("Oblivion.esm") == {}
    assert v.installed_version_for("Oblivion.esm") is None


# ── upgrade_plan ──────────────────────────────────────────────────────────

def test_fresh_install_is_never_run_not_an_upgrade(state, monkeypatch):
    monkeypatch.setattr(v, "current_version", lambda: "0.58")
    plan = v.upgrade_plan("Oblivion.esm")
    assert plan["never_run"] is True
    assert plan["upgraded"] is False
    # Nothing pre-selected: the GUI's normal defaults are right for a first run.
    assert plan["steps"] == []


def test_upgrade_selects_only_the_changed_steps(state, monkeypatch):
    monkeypatch.setattr(v, "current_version", lambda: "0.58")
    _table(monkeypatch, {"0.58": ["6. Import", "9. Pack BSAs"]})
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.57")

    plan = v.upgrade_plan("Oblivion.esm")
    assert plan["upgraded"] is True
    assert plan["unknown"] is False
    assert plan["steps"] == ["import_", "pack"]


def test_unresolvable_range_selects_everything(state, monkeypatch):
    """Unknown must fail toward re-running too much, never too little."""
    monkeypatch.setattr(v, "current_version", lambda: "0.58")
    _table(monkeypatch, {})
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.55")

    plan = v.upgrade_plan("Oblivion.esm")
    assert plan["unknown"] is True
    assert plan["steps"] == [k for k, _ in v.STEP_KEYS]


def test_a_step_that_never_ran_is_owed_even_without_an_upgrade(state, monkeypatch):
    """Same version, but Pack BSAs was never run -- it is still outstanding."""
    monkeypatch.setattr(v, "current_version", lambda: "0.58")
    _table(monkeypatch, {})
    for key, _ in v.STEP_KEYS:
        if key != "pack":
            v.record_step_run(key, "Oblivion.esm", "0.58")

    plan = v.upgrade_plan("Oblivion.esm")
    assert plan["upgraded"] is False
    assert plan["steps"] == ["pack"]


def test_steps_are_returned_in_run_order(state, monkeypatch):
    monkeypatch.setattr(v, "current_version", lambda: "0.58")
    _table(monkeypatch, {"0.58": ["10. Pack Mod Zip", "1. Export", "6. Import"]})
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.57")
    assert v.upgrade_plan("Oblivion.esm")["steps"] == [
        "export", "import_", "pack_zip"]


def test_a_step_already_run_at_the_current_version_is_not_reselected(
        state, monkeypatch):
    """THE auto-select bug: steps are judged individually, not as one group.

    A user who upgrades to 0.586, runs Import and Scripts, then reopens the GUI
    must not see Import and Scripts ticked again -- they already ran at 0.586.
    They did see them, because the plan resolved ONE range from the OLDEST step
    (`installed_version_for`) and applied that union to all of them: Export still
    sitting at 0.585 dragged the range to (0.585, 0.586], whose union names
    every step 0.586 touched, sweeping in the two already at 0.586.
    """
    monkeypatch.setattr(v, "current_version", lambda: "0.586")
    _table(monkeypatch, {"0.586": ["1. Export", "6. Import", "8. Scripts"]})
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.585")
    v.record_step_run("import_", "Oblivion.esm", "0.586")
    v.record_step_run("scripts", "Oblivion.esm", "0.586")

    plan = v.upgrade_plan("Oblivion.esm")
    assert "import_" not in plan["steps"], "already run at 0.586"
    assert "scripts" not in plan["steps"], "already run at 0.586"
    # The genuinely stale one is still owed.
    assert "export" in plan["steps"]


def test_end_user_bare_tags_reproduce_the_same_selection(state, monkeypatch):
    """End users install a source drop, so every stamp is a BARE tag.

    No `+g<sha>` is involved on their machine -- the per-step regression must
    hold on plain release numbers, not only on developer describe strings.
    """
    monkeypatch.setattr(v, "current_version", lambda: "0.586")
    _table(monkeypatch, {"0.586": ["3. Meshes", "6. Import"]})
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.586")
    v.record_step_run("meshes", "Oblivion.esm", "0.585")

    plan = v.upgrade_plan("Oblivion.esm")
    assert plan["steps"] == ["meshes"]


def test_one_unresolvable_step_does_not_drag_in_the_resolvable_ones(
        state, monkeypatch):
    """A hole affects only the steps whose own range crosses it.

    Failing toward re-running is right for the step that cannot be resolved; it
    must not also re-select steps whose range answers cleanly.
    """
    # 0.586 itself is missing, so any range ENDING at it is unresolvable, while
    # a step already AT 0.586 resolves trivially to "nothing owed".
    monkeypatch.setattr(v, "current_version", lambda: "0.586")
    _table(monkeypatch, {"0.585": ["1. Export"]})       # 0.586 absent
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.586")
    v.record_step_run("meshes", "Oblivion.esm", "0.584")

    plan = v.upgrade_plan("Oblivion.esm")
    assert plan["unknown"] is True
    assert "meshes" in plan["steps"]
    # Everything else sat at 0.586 already and owes nothing.
    assert plan["steps"] == ["meshes"]


def test_no_steps_reads_as_up_to_date_even_when_installed_looks_old(
        state, monkeypatch):
    """`upgraded` and an empty step list can disagree, and the list wins.

    `installed` is the OLDEST recorded step and now includes the shared
    plugin-independent ones, so a plugin whose every step is current can still
    carry an old `installed` -- which printed "upgraded, re-run: nothing".
    """
    monkeypatch.setattr(v, "current_version", lambda: "0.586")
    _table(monkeypatch, {"0.586": []})
    for key, _ in v.STEP_KEYS:
        if key != "modify_body_meshes":
            v.record_step_run(key, "Nehrim.esm", "0.586")
    v.record_step_run("modify_body_meshes", "Oblivion.esm", "0.58")

    plan = v.upgrade_plan("Nehrim.esm")
    assert plan["steps"] == []
    assert "up to date" in v.describe_plan(plan)
    assert "re-run: " not in v.describe_plan(plan)


def test_describe_plan_is_console_safe(state, monkeypatch):
    """Printed to a cp1252 Windows console -- non-ASCII raises there."""
    monkeypatch.setattr(v, "current_version", lambda: "0.58")
    _table(monkeypatch, {"0.58": ["6. Import"]})
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.57")

    for plugin in ("Oblivion.esm", "Never.esm"):
        text = v.describe_plan(v.upgrade_plan(plugin))
        text.encode("cp1252")  # raises UnicodeEncodeError on failure


# ── VERSION as an export-subst template ───────────────────────────────────
# VERSION holds a literal `$Format:%(describe:tags)$` on master and is marked
# `export-subst`, so `git archive` expands it while building the source zip a
# user downloads.  Nothing is ever committed, which is what lets a PR-merge
# release stamp a version at all -- the old scheme needed a commit on protected
# master and therefore only worked for a direct push.

def _version_file(tmp_path, monkeypatch, text):
    path = tmp_path / "VERSION"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(v, "VERSION_FILE", path)
    monkeypatch.setattr(v, "_CURRENT", [None])


def test_unexpanded_placeholder_is_not_a_version(tmp_path, monkeypatch):
    """A real checkout reads the raw template; it must never surface as-is.

    Returning it would put `$Format:...$` in the title bar and make every
    version comparison fail.
    """
    _version_file(tmp_path, monkeypatch, "$Format:%(describe:tags)$\n")
    assert v._read_version_file() is None


def test_expanded_tag_is_the_version(tmp_path, monkeypatch):
    """What the release zip for tag 0.581 actually contains."""
    _version_file(tmp_path, monkeypatch, "0.581\n")
    assert v._read_version_file() == "0.581"
    assert v.current_version() == "0.581"
    assert v.is_dev_version() is False


def test_archive_of_an_untagged_commit_is_a_dev_build(tmp_path, monkeypatch):
    """`%(describe:tags)` expands to `0.581-4-gaaef46e` off a tag.

    That archive is NOT release 0.581, so it must not claim to be one.  With no
    .git beside it the honest answer is a development build.
    """
    _version_file(tmp_path, monkeypatch, "0.581-4-gaaef46e\n")
    assert v._read_version_file() is None
    monkeypatch.setattr(v, "_git_version", lambda: None)
    monkeypatch.setattr(v, "_CURRENT", [None])
    assert v.current_version() == v.DEV_VERSION
    assert v.is_dev_version() is True


# ── The steps table over HTTPS ────────────────────────────────────────────

def _body(*steps, heading=True):
    """A release body shaped like the one release_notes.py writes."""
    lines = ["Release 0.581", "", "Changes since 0.58 (2 commits):", "",
             "  abc1234  did a thing", ""]
    if heading:
        lines += ["Steps to re-run in the GUI:", ""]
        lines += [f"  [x] {s}" for s in steps] or ["  (none -- no conversion "
                                                   "code changed)"]
    return "\n".join(lines) + "\n"


def _serve(monkeypatch, releases=None, fail=False, status=None):
    """Stand in for the `releases?per_page=100` fetch.

    `releases` is a list of (tag_name, body).  `fail` is a TRANSPORT failure
    (genuinely offline); `status` is an HTTP error response, which proves the
    server was reached.
    """
    import io
    import json as _json

    class _Resp:
        def __init__(self, body):
            self._body = body
        def __enter__(self):
            return io.BytesIO(self._body)
        def __exit__(self, *a):
            return False

    payload = [{"tag_name": t, "body": b} for t, b in (releases or [])]

    def _open(*a, **k):
        if fail:
            raise OSError("no network")
        if status:
            raise v.urllib.error.HTTPError(v._RELEASES_API, status, "err",
                                           {}, None)
        return _Resp(_json.dumps(payload).encode())

    monkeypatch.setattr(v.urllib.request, "urlopen", _open)
    monkeypatch.setattr(v, "_TABLE", [None])


def test_checklist_is_read_from_the_release_body(monkeypatch):
    """The release body IS the table -- no asset, nothing committed."""
    _serve(monkeypatch, [("0.581", _body("6. Import", "9. Pack BSAs"))])
    table, reachable = v.steps_table()
    assert reachable is True
    assert table == {"0.581": ["6. Import", "9. Pack BSAs"]}


def test_steps_come_back_in_run_order(monkeypatch):
    """A body listing steps out of order still yields GUI run order."""
    _serve(monkeypatch, [("0.581", _body("9. Pack BSAs", "1. Export"))])
    assert v.steps_table()[0]["0.581"] == ["1. Export", "9. Pack BSAs"]


def test_non_release_tags_are_ignored(monkeypatch):
    """navmesh-cache-* is a different series and must never rank as a version."""
    _serve(monkeypatch, [("navmesh-cache-0.57+", _body("6. Import")),
                         ("0.581", _body("3. Meshes"))])
    assert v.steps_table()[0] == {"0.581": ["3. Meshes"]}


def test_an_empty_checklist_is_an_entry_not_a_hole(monkeypatch):
    """A docs-only release genuinely owes nothing.

    Dropping it would leave a hole, and a hole selects all twelve steps
    forever -- so the heading, not the presence of ticks, decides.
    """
    _serve(monkeypatch, [("0.581", _body())])
    assert v.steps_table()[0] == {"0.581": []}


def test_a_body_with_no_checklist_is_absent(monkeypatch):
    """A release cut by hand has no checklist; it must read as a hole."""
    _serve(monkeypatch, [("0.581", _body("6. Import", heading=False))])
    assert v.steps_table()[0] == {}


def test_unreachable_table_is_reported_not_raised(monkeypatch):
    """A network failure on a plugin-selection handler must never propagate."""
    _serve(monkeypatch, fail=True)
    table, reachable = v.steps_table(timeout=1)
    assert table == {}
    assert reachable is False


def test_offline_selects_everything_never_nothing(state, monkeypatch):
    """No table means UNKNOWN, which owes every step.

    An empty list here would tell a user with stale output they are current --
    the exact silent failure the table exists to prevent.
    """
    _serve(monkeypatch, fail=True)
    monkeypatch.setattr(v, "current_version", lambda: "0.582")
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.581")

    plan = v.upgrade_plan("Oblivion.esm")
    assert plan["unknown"] is True
    assert plan["offline"] is True
    assert plan["steps"] == [key for key, _ in v.STEP_KEYS]


def test_a_hole_in_a_reachable_table_is_not_offline(state, monkeypatch):
    """`offline` must distinguish 'could not ask' from 'asked, table has a gap'.

    Only the first is the user's to fix by reconnecting; the GUI disables the
    button for it and would be wrong to do so for the second.
    """
    _serve(monkeypatch, [("0.581", _body("6. Import"))])          # 0.582 absent
    monkeypatch.setattr(v, "current_version", lambda: "0.582")
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.580")

    plan = v.upgrade_plan("Oblivion.esm")
    assert plan["unknown"] is True
    assert plan["offline"] is False


def test_a_failed_fetch_is_cached(monkeypatch):
    """An offline user must not pay the timeout on every plugin selection."""
    calls = []

    def _open(*a, **k):
        calls.append(1)
        raise OSError("no network")

    monkeypatch.setattr(v.urllib.request, "urlopen", _open)
    monkeypatch.setattr(v, "_TABLE", [None])
    for _ in range(5):
        v.steps_table(timeout=1)
    assert len(calls) == 1, f"re-fetched {len(calls)} times"


def test_a_404_is_not_offline(state, monkeypatch):
    """No data yet != the user has no internet.

    An HTTP response proves GitHub was reached, so it is a hole in our data,
    not a problem with the connection.  Reporting it as "offline" told users
    with working connections to go and fix their connection.
    """
    _serve(monkeypatch, status=404)
    table, reachable = v.steps_table()
    assert table == {}
    assert reachable is True, "a 404 means reached-but-empty, not offline"

    monkeypatch.setattr(v, "current_version", lambda: "0.582")
    for key, _ in v.STEP_KEYS:
        v.record_step_run(key, "Oblivion.esm", "0.581")
    plan = v.upgrade_plan("Oblivion.esm")
    # Still unknown -> still selects everything; only the EXPLANATION differs.
    assert plan["unknown"] is True
    assert plan["offline"] is False
    assert plan["steps"] == [key for key, _ in v.STEP_KEYS]


def test_only_a_transport_failure_is_offline(monkeypatch):
    _serve(monkeypatch, fail=True)
    assert v.steps_table(timeout=1)[1] is False


def test_never_converted_is_not_up_to_date(state, monkeypatch):
    """A plugin with no recorded run owes EVERYTHING, not nothing.

    `upgrade_plan` returns steps=[] here because the shortcut has nothing to
    narrow -- there is no previous version to diff against.  The GUI must not
    read that empty list as "up to date": it renders `never_run` as its own
    state ("Not converted"), because telling a user with no output at all that
    they are current is the exact inversion of the truth.
    """
    _table(monkeypatch, {"0.581": ["6. Import"]})
    monkeypatch.setattr(v, "current_version", lambda: "0.581")

    plan = v.upgrade_plan("NeverTouched.esm")
    assert plan["never_run"] is True
    assert plan["upgraded"] is False
    # The distinguishing flag: steps is empty for BOTH never_run and up-to-date,
    # so never_run is the only thing separating them.
    assert plan["steps"] == []
    assert "no previous conversion" in v.describe_plan(plan)


def test_parser_agrees_with_what_release_notes_actually_writes(monkeypatch):
    """The heading and checkbox shape are a CONTRACT between two modules.

    release_notes.py writes the tag message; version.py parses it back out of
    the release body.  Nothing else links them, so a reworded heading or a
    changed bullet would silently empty the table -- every upgrade would then
    read as "unknown" and select all twelve steps.  Build a real notes body and
    round-trip it.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import tools.release.release_notes as rn

    steps = ["3. Meshes", "8. Scripts", "Create LOD", "Body Slot Patch"]
    monkeypatch.setattr(rn, "commits_between", lambda a, b: [("abc1234", "x")])
    monkeypatch.setattr(rn, "changed_files", lambda a, b: [])
    monkeypatch.setattr(rn, "convert_py_steps", lambda a, b: None)
    monkeypatch.setattr(rn, "steps_for_paths", lambda *a, **k: (steps, [], False))

    notes = rn.build_notes("0.581", "0.58", "HEAD")
    assert v._CHECKLIST_HEADING in notes, "heading drifted from release_notes"
    assert v.steps_from_tag_message(notes) == steps


# ---------------------------------------------------------------------------
#  Shared-asset steps are recorded once per MOD
# ---------------------------------------------------------------------------

def _imported_group(tmp_path, monkeypatch, plugs, label='My Pack'):
    """Point version.py at a temp root holding an imported multi-plugin mod."""
    import json
    monkeypatch.setattr(v, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(v, "STATE_FILE", tmp_path / ".conversion_state.json")
    exp = tmp_path / "export"
    exp.mkdir(parents=True, exist_ok=True)
    reg = {"version": 1, "sources": {
        n: {"kind": "archive", "plugin": n, "group_id": "g1",
            "group_label": label, "group_plugins": list(plugs)} for n in plugs}}
    (exp / "sources.json").write_text(json.dumps(reg), encoding="utf-8")
    return exp


def test_shared_asset_step_counts_for_every_plugin_in_the_mod(tmp_path,
                                                              monkeypatch):
    """Meshes converts the mod's ONE payload, so it is done for all of them.

    Recorded per plugin, a three-plugin resource pack showed Meshes as still
    owed for two of its members right after it had been converted.
    """
    _imported_group(tmp_path, monkeypatch, ("A.esm", "B.esp", "C.esm"))
    v.record_step_run("meshes", "A.esm", version="0.600")

    for name in ("A.esm", "B.esp", "C.esm"):
        assert v.steps_run_at(name).get("meshes") == "0.600", name


def test_record_driven_steps_stay_per_plugin(tmp_path, monkeypatch):
    """Export/import/scripts read one plugin's own records, and Sounds writes
    per-plugin voice folders -- none of them carry across the group."""
    _imported_group(tmp_path, monkeypatch, ("A.esm", "B.esp"))
    for step in ("export", "import_", "scripts", "sounds"):
        v.record_step_run(step, "A.esm", version="0.600")

    for step in ("export", "import_", "scripts", "sounds"):
        assert v.steps_run_at("A.esm").get(step) == "0.600"
        assert v.steps_run_at("B.esp").get(step) is None, step


def test_history_recorded_before_group_scoping_still_counts(tmp_path,
                                                            monkeypatch):
    """A sibling's own pre-existing record is read across the group, so an
    already-converted mod is not reported as owing the step again."""
    _imported_group(tmp_path, monkeypatch, ("A.esm", "B.esp"))
    # Simulate the old scheme: stamped directly on one plugin's key.
    state = v._load_state()
    state.setdefault("steps", {})[v._plugin_key("A.esm")] = {"meshes": "0.590"}
    v._save_state(state)

    assert v.steps_run_at("B.esp").get("meshes") == "0.590"


def test_a_data_directory_plugin_is_unaffected(tmp_path, monkeypatch):
    """No registry entry means no group: recording stays exactly as it was."""
    _imported_group(tmp_path, monkeypatch, ("A.esm",))
    v.record_step_run("meshes", "Oblivion.esm", version="0.600")

    assert v.steps_run_at("Oblivion.esm").get("meshes") == "0.600"
    assert v.steps_run_at("A.esm").get("meshes") is None
