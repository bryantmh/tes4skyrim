"""Everything that turns the window's choices into processes and output, with no toolkit.

CLASSIFYING A LINE. Ordering is the whole contract. A failure VERDICT must be judged before the
generic word rules: "Pipeline completed with errors." used to fall past the `error` rule and
land on `complete`, painting the run's most important line green. A tally is judged by its
NUMBER before any word rule sees "failed", and a failure the stage RECOVERED from is orange, not
red, so red keeps meaning "this broke the run".

WRITING THE LOG. A run is one or more convert.py processes and THIS process owns the log, so the
window's own lines (header, error summary) land in it too; children are told a log exists and
write nothing, since two processes appending to one file corrupt it on Windows.

COMMANDS AND STALENESS. Three tables keyed alike: `_CMD_FNS` says how to invoke a load-order
action, `_ARTIFACTS` what file proves it ran, `_STAMP_FNS` what makes that file stale. A stamp
only has to CHANGE when the inputs change.

EXECUTING. Plugins owing the same steps convert in ONE convert.py, which reads its indexes once
for all of them; its phase banners say which step is running.
See: docs/reference/pipeline.md#run-logs
"""

import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

import version as version_info
from asset_convert.sources import source_registry
from core import run_log
from core.python_cmd import python_cmd
from core.gui.config import EXPORT_DIR, GLOBAL_ACTIONS, REPO_ROOT, STEPS, load_config, navmesh_pins_dir, run_process
from core.phase_info import STEP_BY_BANNER
from core.process_job import RC_CANCELLED
from output_layout import BODY_SLOTS_PATCH, DATA_ROOT, FINISHED_DIR_NAME, plugin_esm

# ---------------------------------------------------------------------------
#  Classifying a log line
# ---------------------------------------------------------------------------

#: Verdicts that mean the run broke, in the words each producer prints.
FAIL_MARKERS = (
    "completed with errors",
    "traceback (most recent call last)",
    "stopped - missing dependency",
    "fatal error",
    "fatal:",
)

#: Stages that ran fine and had nothing to do. Informational; these exit 0.
NOTHING_MARKERS = (
    "nothing to generate",
    "nothing to do",
    "nothing to convert",
    "nothing to pack",
    "nothing to patch",
    "nothing to audit",
    "no work to do",
)

#: A count line: "N failed" / "N failures" / "N errors".
COUNT_RE = re.compile(
    r"(?<![\w.])(\d+)\s+(?:\w+\s+){0,2}?(?:failed|failures?|errors?)(?![\w])")

#: A standalone FAILED verdict, never a tally.
FAILED_RE = re.compile(
    r"(?:^|[:\-]\s*|\s)fail(?:ed|ure)"
    r"(?:\s*[(:/,.;]|\s+for\b|\s+-\s|\s*$)")

#: The stage says so in the same breath and carries on, exiting 0.
RECOVERED_RE = re.compile(
    r"falling back|fall back|generating normally|will be (?:copied|skipped)"
    r"|is allowed to continue|continuing|ignored|retrying|using .* instead")

#: Deliberately-skipped work, matched only when the sentence leads with it.
SKIP_RE = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)?skipp?(?:ing|ed)\b"
    r"|\bskipping\s+(?:the\s+)?[\w.\-]+\s+(?:generation|phase|stage|step)"
    r"|[:\-;]\s*skipp?(?:ing|ed)\b"
    r"|,\s*skipping\b|\bnot\s+overlaid\b")


def count_verdict(low: str):
    """'err' if a count line reports >0, 'ok' if it reports 0, else None."""
    hits = COUNT_RE.findall(low)
    if not hits:
        return None
    return "err" if any(int(n) for n in hits) else "ok"


def is_recovered_line(low: str) -> bool:
    """True for a failure the stage recovered from and carried on past."""
    return bool(RECOVERED_RE.search(low))


def is_failure_line(low: str) -> bool:
    """True for a line stating the run actually broke."""
    if any(m in low for m in FAIL_MARKERS):
        return True
    return bool(FAILED_RE.search(low)) and count_verdict(low) != "ok"


def is_nothing_line(low: str) -> bool:
    """True for a stage reporting it had no work to do."""
    return any(m in low for m in NOTHING_MARKERS)


#: Checked in order; the FIRST predicate that answers claims the line.
_RULES = (
    (lambda low, raw: "missing dependency" in low, "err"),
    (lambda low, raw: raw.startswith("===") or "phase" in low[:20], "head"),
    (lambda low, raw: is_recovered_line(low), "warn"),
    (lambda low, raw: is_failure_line(low), "err"),
    (lambda low, raw: is_nothing_line(low), "warn"),
    (lambda low, raw: bool(SKIP_RE.search(low)), "warn"),
    (lambda low, raw: "error" in low and "errors" not in low, "err"),
    (lambda low, raw: "warning" in low or "warn" in low, "warn"),
    (lambda low, raw: low.strip() in ("done", "ok") or "complete" in low
     or "success" in low, "ok"),
    (lambda low, raw: raw.startswith("Running:"), "cmd"),
)


def _banner_tag(banner, raw: str, low: str):
    """The tag while a multi-line preflight banner is open, or None.

    Only each banner's own HEADER opens one: the one-line "STOPPED - MISSING DEPENDENCY" summary
    printed afterwards also says "missing dependency" and must not re-open it.
    """
    stripped = raw.strip()
    is_rule = bool(stripped) and set(stripped) in ({"="}, {"-"})
    if banner[0] is not None:
        tag, seen = banner[0]
        if is_rule:
            seen += 1
            banner[0] = None if seen >= 2 else (tag, seen)
        return tag
    if low.startswith("missing dependency"):
        banner[0] = ("err", 0)
        return "err"
    if low.startswith("warning: python version"):
        banner[0] = ("warn", 0)
        return "warn"
    return None


def classify(banner, line: str):
    """The log tag for one output line, or None to leave it uncolored.

    `banner` is a one-element list holding the open-banner state across calls. A count verdict
    can answer "claimed, but uncolored" (None), which the rule table cannot express.
    """
    low = line.lower()
    tag = _banner_tag(banner, line, low)
    if tag is not None:
        return tag
    cnt = count_verdict(low)
    if cnt is not None:
        return "err" if cnt == "err" else (
            "ok" if ("succeeded" in low or "success" in low) else None)
    for predicate, tag in _RULES:
        if predicate(low, line):
            return tag
    return None


def phase_step(line: str):
    """The step key a convert.py phase banner announces, or None for any other line."""
    return STEP_BY_BANNER.get(line.strip())


# ---------------------------------------------------------------------------
#  Writing the log
# ---------------------------------------------------------------------------

#: Error lines kept for the end-of-run summary.
ERR_SUMMARY_CAP = 40

_RULE = "-" * 54


def _shown_path(path) -> str:
    """`path` relative to the repo when it lives there, else in full."""
    try:
        return str(Path(path).relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


class RunLogSink:
    """This run's log file, if one is open, and the errors seen so far. Never fails a run."""

    __slots__ = ("log", "errors", "banner")

    def __init__(self):
        """Start with no log open and no errors recorded."""
        self.log = None
        self.errors = []
        self.banner = [None]

    def begin(self, header: dict, logs_dir) -> None:
        """Prune old logs and open this run's, named for the header's File."""
        self.end()
        self.errors = []
        self.banner = [None]
        try:
            keep = run_log.runs_kept(load_config())
        except Exception:
            keep = 0
        if keep <= 0:
            return
        try:
            logs_dir = run_log.logs_root(logs_dir)
            if not run_log.prune(logs_dir, keep):
                return
            full = {"Version": version_info.current_version()}
            full.update(header)
            log = run_log.RunLog(run_log.free_log_path(logs_dir, header.get("File")), full)
            self.log = log if log.active else None
        except Exception:
            self.log = None

    def end(self, status: str = None) -> None:
        """Close the open log, if any."""
        log, self.log = self.log, None
        if log is None:
            return
        try:
            log.close(status)
        except Exception:
            pass

    def env(self) -> dict:
        """Tell child processes a log exists, so they do not rotate it."""
        if self.log is None:
            return {}
        return {run_log.RUN_LOG_ENV_VAR: str(self.log.path)}

    def path(self):
        """The open log's file, or None."""
        return None if self.log is None else self.log.path

    def write(self, line: str) -> None:
        """Mirror one line into the run log, if one is open."""
        if self.log is None:
            return
        try:
            self.log.write_line(line)
        except Exception:
            pass

    def record_error(self, line: str, tag: str) -> None:
        """Remember an error line for the end-of-run summary, skipping the verdict restatements."""
        if tag != "err":
            return
        text = line.strip()
        low = text.lower()
        if not text or text.strip(" -") in ("FAILED", "DONE", "CANCELLED") or set(text) == {"-"}:
            return
        if low.startswith(("failed (exit ", "pipeline completed with errors", "error summary",
                           "stopped - missing dependency")):
            return
        if len(self.errors) < ERR_SUMMARY_CAP + 1:
            self.errors.append(text)

    def take(self, line: str):
        """Classify, record and mirror one line; returns its tag."""
        tag = classify(self.banner, line)
        self.record_error(line, tag)
        self.write(line)
        return tag


def error_summary(sink) -> list:
    """The lines summarising a failed run's errors."""
    out = ["", _RULE]
    if not sink.errors:
        return out + ["  ERROR SUMMARY: no error lines were captured -- check the stage output "
                      "above for the failure.", _RULE]
    shown = sink.errors[:ERR_SUMMARY_CAP]
    extra = len(sink.errors) - len(shown)
    out.append(f"  ERROR SUMMARY ({len(sink.errors)}{'+' if extra else ''} error line"
               f"{'' if len(sink.errors) == 1 else 's'}):")
    out += [f"    - {text}" for text in shown]
    if extra:
        out.append(f"    ... and {extra} more (see the log above)")
    return out + [_RULE]


def format_elapsed(seconds: float) -> str:
    """`m:ss`, or `h:mm:ss` once a run passes the hour."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
#  Building commands
# ---------------------------------------------------------------------------

#: Step key -> its convert.py flag.
STEP_FLAGS = {key: flag for key, flag, *_ in STEPS}


def _tool(*parts) -> list:
    """The command that runs the script at `<repo>/<parts...>` (see `core.python_cmd`)."""
    return python_cmd(REPO_ROOT.joinpath(*parts))


def _with_out(cmd: list, out_dir: str) -> list:
    """Append `--output-dir` when one is configured."""
    return cmd + (["--output-dir", out_dir] if out_dir else [])


def mesh_flags(session, winding: bool) -> list:
    """The flags the Meshes step reads: folders, winding fix, parallax."""
    flags = ["--mesh-subdirs"] + list(session.selection.mesh_subdirs) if session.selection.mesh_subdirs else []
    flags.append("--collision-winding-fix" if winding else "--no-collision-winding-fix")
    if session.parallax:
        flags.append("--parallax")
        if session.tex_only:
            flags.append("--textures-only")
    return flags


def convert_cmd(session, plugins: list, steps: list, winding: bool) -> list:
    """One convert.py command converting `plugins` through `steps`, in pipeline order."""
    cmd = _tool("convert.py") + ["-f"] + list(plugins)
    cmd += [STEP_FLAGS[key] for key, *_ in STEPS if key in set(steps)]
    cmd += ["--navmesh-generator", session.navgen, "--navmesh-pins", navmesh_pins_dir()]
    if "meshes" in steps:
        cmd += mesh_flags(session, winding)
    else:
        cmd.append("--collision-winding-fix" if winding else "--no-collision-winding-fix")
    return _with_out(cmd, session.output)


def group_plugins(session, owed: dict) -> list:
    """[(plugins, steps, winding)] grouping plugins that owe the same steps and fix setting.

    `owed` is {plugin: [step keys]}, in load order; each group becomes one convert.py process.
    """
    groups = {}
    for plugin, steps in owed.items():
        if not steps:
            continue
        key = (tuple(k for k, *_ in STEPS if k in set(steps)), session.winding_on(plugin))
        groups.setdefault(key, []).append(plugin)
    return [(plugins, list(steps), winding) for (steps, winding), plugins in groups.items()]


def _cmd_start_mod(session) -> list:
    """Zip the prebuilt starter mod."""
    return _with_out(_tool("tools", "release", "package_start_mod.py"), session.output)


def _cmd_runtime_dll(session) -> list:
    """Zip the built runtime DLLs as one SKSE mod."""
    return _with_out(_tool("tools", "release", "package_runtime_dll.py"), session.output)


def _cmd_pack_lod(session) -> list:
    """Zip the baked AutoConvertLOD folder."""
    return _with_out(_tool("tools", "release", "pack_lod.py"), session.output)


def _cmd_convert_ui(session) -> list:
    """Build the UI reskin, limited to the movies chosen."""
    cmd = _with_out(_tool("tools", "misc", "convert_ui.py"), session.output)
    chosen = session.selection.convert_ui
    cmd += [] if chosen["messagebox"] else ["--no-messagebox"]
    return cmd + ([] if chosen["cursor"] else ["--no-cursor"])


def _cmd_make_master(session) -> list:
    """Flag the chosen plugins ESM, in dependency order."""
    from core.gui.loadorder import default_master_plugins

    cmd = _tool("tools", "esm", "make_master.py")
    cmd += session.selection.master_plugins or default_master_plugins(session)
    return _with_out(cmd, session.output)


def _cmd_create_lod(session) -> list:
    """Bake LOD for the whole load order; the confirmed order and filter only once confirmed."""
    cmd = _with_out(_tool("tools", "release", "create_lod.py"), session.output)
    if session.selection.lod_plugins:
        cmd += ["--plugins"] + list(session.selection.lod_plugins)
    if session.selection.lod_worldspaces:
        cmd += ["--worldspaces"] + list(session.selection.lod_worldspaces)
    return cmd


def _cmd_body_patch(session) -> list:
    """Build the slot-44 body patch for the chosen Skyrim plugins."""
    cmd = _with_out(_tool("convert.py") + ["--modify-body-meshes"], session.output)
    pairs = session.selection.patch_plugins
    chosen = [n for n, on in pairs if on]
    if chosen and chosen != [n for n, _ in pairs]:
        cmd += ["--patch-plugins"] + chosen
    return cmd


#: Load-order action -> the function building its argv.
_CMD_FNS = {
    "package_start_mod": _cmd_start_mod,
    "package_runtime_dll": _cmd_runtime_dll,
    "pack_lod": _cmd_pack_lod,
    "convert_ui": _cmd_convert_ui,
    "make_master": _cmd_make_master,
    "create_lod": _cmd_create_lod,
    "modify_body_meshes": _cmd_body_patch,
}


def global_cmd(session, key: str) -> list:
    """The argv for one load-order action."""
    return _CMD_FNS[key](session)


def global_label(key: str) -> str:
    """A load-order action's display name."""
    return next(label for k, label, *_ in GLOBAL_ACTIONS if k == key)


# ---------------------------------------------------------------------------
#  What is already done
# ---------------------------------------------------------------------------

def _artifact_convert_ui():
    """The UI mod's zip name, spelled once in the tool that produces it."""
    from tools.misc.convert_ui import MOD_NAME

    return f"{MOD_NAME}.zip"


def _artifact_pack_lod():
    """The LOD mod's zip name."""
    from asset_convert.lod.sibling_lod import LOD_DIR_NAME

    return f"{LOD_DIR_NAME}.zip"


#: Load-order action -> the file under Finished Mods that proves it ran.
_ARTIFACTS = {
    "package_start_mod": lambda: "TESGameSelect.zip",
    "package_runtime_dll": lambda: "TESRuntime.zip",
    "convert_ui": _artifact_convert_ui,
    "pack_lod": _artifact_pack_lod,
    "modify_body_meshes": lambda: f"{BODY_SLOTS_PATCH}.zip",
}


def global_artifact(session, key: str):
    """The file this action produces, or None when it has no single one (never creates the folder)."""
    name_fn = _ARTIFACTS.get(key)
    if name_fn is None:
        return None
    return Path(session.out_root()) / FINISHED_DIR_NAME / name_fn()


#: Field separator inside a stamp; never appears in a path or a name.
SEP = "\x1f"


def _master_flags_done(stamp: str) -> bool:
    """True when every plugin in a make_master stamp reads back as ESM."""
    return bool(stamp) and all(part.endswith(":1") for part in stamp.split(SEP))


def global_is_current(session, key: str, last_stamp: dict) -> bool:
    """Has this action run, and is its result still up to date?"""
    art = global_artifact(session, key)
    if art is not None and not art.exists():
        return False
    try:
        stamp = global_stamp(session, key)
    except Exception:
        return False
    if key == "make_master":
        done = _master_flags_done(stamp)
        if done:
            last_stamp[key] = stamp
        return done
    if last_stamp.get(key) == stamp:
        return True
    if art is None:
        return False
    last_stamp[key] = stamp
    return True


def _tree_stamp(root: Path) -> str:
    """name:size:mtime for every file under `root`, sorted; "" if unreadable."""
    try:
        return SEP.join(sorted(
            f"{p.relative_to(root).as_posix()}:{st.st_size}:{int(st.st_mtime)}"
            for p in root.rglob("*") if p.is_file()
            for st in (p.stat(),)))
    except OSError:
        return ""


def _stat_part(path: Path, label: str, missing: str) -> str:
    """`label:size:mtime`, or `label:<missing>` when it cannot be stat'd."""
    try:
        st = path.stat()
        return f"{label}:{st.st_size}:{int(st.st_mtime)}"
    except (OSError, TypeError):
        return f"{label}:{missing}"


def _stamp_start_mod(_session) -> str:
    """The starter mod's SOURCES: packaging builds from them."""
    return (_tree_stamp(REPO_ROOT / "TESGameSelect" / "scripts" / "source")
            + _stat_part(REPO_ROOT / "tools" / "release" / "make_game_select_esp.py", "gen", "missing"))


def _stamp_runtime_dll(_session) -> str:
    """The built DLL, and whether a Morrowind install is registered (the Morrowind menu needs it)."""
    morrowind = bool(source_registry.directory_for(EXPORT_DIR, "Morrowind.esm"))
    return (_stat_part(REPO_ROOT / "tes_runtime" / "dist" / "TESRuntime.dll", "dll", "missing")
            + f"|morrowind:{int(morrowind)}")


def _convert_ui_wanted(ui, cur, ob_dir, sk_dir) -> list:
    """(data dir, relative path) for every file the UI reskin reads."""
    from tools.misc.convert_ui import CURSOR_SWF, MESSAGE_BOX_SWF

    wanted = [(ob_dir, ui.MESSAGE_MENU_XML), (ob_dir, ui.GENERIC_BACKGROUND_XML),
              (sk_dir, MESSAGE_BOX_SWF), (sk_dir, CURSOR_SWF)]
    for directory, names in ((ui.BACKGROUND_TEXTURE_DIR, ui.BACKGROUND_TEXTURES),
                             (ui.FOCUS_TEXTURE_DIR, ui.FOCUS_TEXTURES),
                             (cur.CURSOR_TEXTURE_DIR, cur.CURSOR_TEXTURES)):
        wanted += [(ob_dir, os.path.join(directory, n)) for n in names]
    return wanted


def _stamp_convert_ui(_session) -> str:
    """The game files the UI reskin reads, loose first then the archives, as the tool resolves them."""
    from asset_convert.ui import ui_cursor as cur
    from asset_convert.ui import ui_menus as ui
    from tools.misc.convert_ui import find_data_dirs

    ob_dir, sk_dir = find_data_dirs()
    parts = []
    for data_dir, rel in _convert_ui_wanted(ui, cur, ob_dir, sk_dir):
        loose = Path(data_dir) / Path(*rel.split("\\")) if data_dir else None
        parts.append(_stat_part(loose, rel, "bsa") if loose else f"{rel}:?")
    archives = ((ob_dir, ("Oblivion - Misc.bsa", "Oblivion - Textures - Compressed.bsa")),
                (sk_dir, ("Skyrim - Interface.bsa",)))
    for data_dir, names in archives:
        parts += [_stat_part(Path(data_dir) / name if data_dir else Path(name), name, "?")
                  for name in names]
    return SEP.join(parts)


def _stamp_pack_lod(session) -> str:
    """The baked LOD folder this zip would copy."""
    from asset_convert.lod.sibling_lod import LOD_DIR_NAME

    return _tree_stamp(Path(session.out_root()) / LOD_DIR_NAME)


def _stamp_make_master(session) -> str:
    """Each selected plugin's ESM flag, read back off disk."""
    from core.gui.loadorder import default_master_plugins, esm_flags

    names = session.selection.master_plugins or default_master_plugins(session)
    flags = esm_flags(session, names)
    return SEP.join(f"{n}:{'?' if flags[n] is None else int(flags[n])}" for n in names)


def converted_names(out_dir) -> list:
    """Every converted plugin in `out_dir`, sorted; [] if unreadable."""
    try:
        from asset_convert.lod.sibling_lod import converted_plugins
        return sorted(converted_plugins(Path(out_dir)))
    except Exception:
        return []


def _lod_extra(session) -> list:
    """Create LOD's own inputs: the plugin order, then the worldspace filter or each plugin's stat."""
    from core.gui.loadorder import default_lod_plugins

    names = session.selection.lod_plugins or default_lod_plugins(session)
    parts = ["|"] + list(names)
    if session.selection.lod_worldspaces:
        return parts + ["|"] + list(session.selection.lod_worldspaces)
    sig = []
    for n in names:
        try:
            st = plugin_esm(session.out_root(), n, EXPORT_DIR).stat()
            sig.append(f"{n}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            sig.append(f"{n}:-")
    return parts + ["|"] + sig


def _stamp_converted_set(session, key: str) -> str:
    """The converted plugin set, plus whatever else `key` depends on."""
    parts = converted_names(session.out_root())
    if key == "modify_body_meshes":
        parts += ["|"] + sorted(n for n, on in session.selection.patch_plugins if on)
    if key == "create_lod":
        parts += _lod_extra(session)
    return SEP.join(parts)


#: Actions whose inputs are NOT the converted plugin set.
_STAMP_FNS = {
    "package_start_mod": _stamp_start_mod,
    "package_runtime_dll": _stamp_runtime_dll,
    "convert_ui": _stamp_convert_ui,
    "pack_lod": _stamp_pack_lod,
    "make_master": _stamp_make_master,
}


def global_stamp(session, key: str) -> str:
    """A fingerprint of everything `key`'s result depends on."""
    fn = _STAMP_FNS.get(key)
    return fn(session) if fn is not None else _stamp_converted_set(session, key)


# ---------------------------------------------------------------------------
#  How long steps took
# ---------------------------------------------------------------------------

#: Where each step's last duration is kept, per plugin set, for the next run's estimate.
DURATIONS_FILE = DATA_ROOT / "logs" / "step_times.json"


def _durations() -> dict:
    """Every recorded duration; {} when none or unreadable."""
    try:
        return json.loads(DURATIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def estimate(plugins, step: str):
    """Seconds `step` last took for this plugin set, or None when never timed."""
    return _durations().get(",".join(sorted(p.lower() for p in plugins)), {}).get(step)


def record_duration(plugins, step: str, seconds: float) -> None:
    """Remember how long `step` took for this plugin set."""
    data = _durations()
    data.setdefault(",".join(sorted(p.lower() for p in plugins)), {})[step] = round(seconds, 1)
    try:
        DURATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        DURATIONS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
#  Executing
# ---------------------------------------------------------------------------

#: Exit code -> the verdict line the log ends with.
_VERDICTS = {RC_CANCELLED: "  CANCELLED", 0: "  DONE"}


def verdict_line(ret: int, missing_dep: int) -> str:
    """The closing line for a run that exited `ret`."""
    if ret == missing_dep:
        return "  STOPPED - MISSING DEPENDENCY (see above)"
    return _VERDICTS.get(ret, f"  FAILED (exit {ret})")


def run_env(session, sink) -> dict:
    """The environment every child of a run inherits: workers, cache opt-out, the run log."""
    from core.worker_budget import WORKERS_ENV_VAR
    from tools.navmesh.navmesh_cache import NO_DOWNLOAD_ENV_VAR

    env = {WORKERS_ENV_VAR: str(session.worker_count()),
           NO_DOWNLOAD_ENV_VAR: "" if session.cache_dl else "1"}
    env.update(sink.env())
    return env


class Run:
    """Commands executed one after another on a worker thread, reported as events.

    `events` receives ("line", text), ("cmd", index) as each command starts, and finally
    ("end", exit code). A missing dependency stops the rest: each command is its own process,
    so nothing else would keep the remainder from producing half-converted output.
    """

    def __init__(self, cmds: list, env: dict, missing_dep: int):
        """Prepare, without starting."""
        self.cmds = cmds
        self.env = env
        self.missing_dep = missing_dep
        self.events = queue.Queue()
        self.cancel_evt = threading.Event()
        self.started = 0.0
        self.thread = None

    def start(self) -> None:
        """Run on a daemon thread."""
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def cancel(self) -> None:
        """Ask the running command to stop; the rest are skipped."""
        self.cancel_evt.set()

    def alive(self) -> bool:
        """True until the worker has finished."""
        return self.thread is not None and self.thread.is_alive()

    def _work(self) -> None:
        """Run each command until one fails or the run is cancelled."""
        ret = 0
        try:
            ret = self._run_all()
        except Exception as exc:
            self.events.put(("line", f"ERROR: {exc}"))
            ret = 1
        self.events.put(("end", ret))

    def _run_all(self) -> int:
        """The commands in order; returns the run's exit code."""
        ret = 0
        for i, cmd in enumerate(self.cmds):
            if self.cancel_evt.is_set():
                return RC_CANCELLED
            self.events.put(("cmd", i))
            self.events.put(("line", f"Running: {' '.join(cmd)}"))
            r = run_process(cmd, lambda line: self.events.put(("line", line)), env=self.env,
                            cancel_event=self.cancel_evt)
            if r in (RC_CANCELLED, self.missing_dep):
                return r
            ret = r or ret
        return ret
