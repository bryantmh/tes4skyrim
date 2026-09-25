"""Everything that turns a ticked selection into processes and output.

CLASSIFYING A LINE. Ordering is the whole contract. A failure VERDICT must be
judged before the generic word rules: "Pipeline completed with errors." used to
fall past the `error` rule -- guarded on "errors" not in the line, so plural
counts like "0 errors" stay neutral -- and land on the `complete` rule, painting
the single most important line of the run green. A tally is judged by its NUMBER
before any word rule sees "failed" in it, so COUNT_RE tolerates a couple of
adjectives ("4 compile errors") and FAILED_RE is anchored on what PRECEDES
`failed`. A failure the stage RECOVERED from is orange, not red: red is reserved
for what actually broke the run, and coloring recoverable notices red trains
the user to ignore red. SKIP_RE is deliberately narrow -- bare "skip" shows up in
tallies and routine chatter, so only a sentence that LEADS with the skip counts.

WRITING THE LOG. A GUI run is several convert.py processes and THIS process owns
the log, so mirroring every line here also captures the GUI's own -- the header,
the error summary -- which are in no child's stdout. Children are told a log
exists and write nothing; two processes appending to one file would interleave
and, on Windows, corrupt it. The summary is capped: a stage that fails per-file
would otherwise flood it.

RUN STATE. `set_running` is the single switch; everything that changes when a
run starts or stops hangs off it. The progress bar is restored with a bare
`grid()`, because `grid_remove()` remembers the cell and re-stating the options
is how the bar used to reappear at the top of the sidebar.

COMMANDS AND STALENESS. Three tables keyed alike, so a new global action is
added in one place each: `_CMD_FNS` says how to invoke it, `_ARTIFACTS` says
what file proves it ran, `_STAMP_FNS` says what makes that file stale. "Done" is
not permanent -- these actions consume the set of things CONVERTED SO FAR, so a
merged LOD folder built before ElsweyrAnequina existed knows nothing about its
tiles. The ARTEFACT is the evidence, not the run record: work already on disk is
done however it got there. `make_master` has no artefact -- it flips a bit inside
plugins that already exist -- so its stamp reads the flags back off disk. A stamp
only has to CHANGE when the inputs change, never to contain them: falling back
to the derived worldspace default parsed every converted ESM (Oblivion.esm alone
is 613 MB), 0.01s -> 1.63s before the window first paints.

EXECUTING. The one-process fast path fires when the selection IS the default,
computed the way the checkboxes are. It lists its steps explicitly rather than
using a bare `convert.py -f <plugin>`, which would take convert's own default
path and switch Body Slot Patch on -- rewriting the shared patch behind the user's
back. The summary is emitted by the DRAIN, never the worker: the worker finishes
while its last lines are still queued.

See: docs/reference/pipeline.md#run-logs
"""


import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

import version as version_info
from core import run_log
from core.gui.config import (
    CLR,
    EXPORT_DIR,
    GLOBAL_ACTIONS,
    RC_CANCELLED,
    REPO_ROOT,
    STEPS,
    default_on_steps,
    load_config,
    run_process,
    step_names,
)
from core.gui.selection import runnable
from output_layout import BODY_SLOTS_PATCH

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
    """The tag while a multi-line banner is open, or None.

    preflight emits two banners, each `HEADER / rule / body / rule`, whose
    bodies are install instructions that would otherwise classify as plain text
    and lose the visual grouping. Only each banner's own HEADER line opens one:
    the one-line "STOPPED - MISSING DEPENDENCY" summary the GUI prints
    afterwards also says "missing dependency" and must not re-open it.
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

    `banner` is a one-element list holding the open-banner state across calls.
    The count verdict stays outside the rule table because it has a third
    outcome the (predicate, tag) shape cannot carry: None meaning "claimed,
    but deliberately uncolored".
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



#: Error lines kept for the end-of-run summary.
ERR_SUMMARY_CAP = 40

_RULE = "-" * 54


# ---------------------------------------------------------------------------
#  Writing the log
# ---------------------------------------------------------------------------

def _shown_path(path) -> str:
    """`path` relative to the repo when it lives there, else in full."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


class RunLogSink:
    """This run's log file, if one is open, and the errors seen so far.

    A GUI run is several convert.py processes, and THIS process owns the log:
    every line already flows through `write`, so mirroring it here also
    captures the GUI's own lines -- the header, the error summary -- which are
    in no child's stdout. Children are told a log exists and write nothing;
    two processes appending to one file would interleave and, on Windows,
    corrupt it.

    Nothing here may fail a conversion, so every filesystem step is guarded.
    """

    __slots__ = ("log", "errors", "banner")

    def __init__(self):
        """Start with no log open and no errors recorded."""
        self.log = None
        self.errors = []
        self.banner = [None]

    def begin(self, header: dict, logs_dir) -> None:
        """Prune old logs and open this run's, named for the header's File.

        A global action has no File, so its log is named for NO_PLUGIN.
        Never fails a run.
        """
        self.end()
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
            path = run_log.free_log_path(logs_dir, header.get("File"))
            log = run_log.RunLog(path, full)
            if log.active:
                self.log = log
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

    def note(self, emit) -> None:
        """Say where this run is being recorded."""
        if self.log is not None:
            emit(f"Log: {_shown_path(self.log.path)}")

    def size_note(self, emit) -> None:
        """Report the finished log's size, so a runaway file is visible."""
        if self.log is None:
            return
        try:
            size = self.log.path.stat().st_size
        except OSError:
            return
        emit(f"Log saved: {_shown_path(self.log.path)} "
             f"({run_log.format_size(size)})")

    def write(self, line: str) -> None:
        """Mirror one line into the run log, if one is open."""
        if self.log is None:
            return
        try:
            self.log.write_line(line)
        except Exception:
            pass

    def record_error(self, line: str, tag: str) -> None:
        """Remember an error line for the end-of-run summary.

        The verdict lines are filtered out: they are restatements printed by
        the summary itself, and leaving them in opened every run's summary with
        "Pipeline completed with errors." summarising itself.
        """
        if tag != "err":
            return
        text = line.strip()
        if not text or text.strip(" -") in ("FAILED", "DONE", "CANCELLED"):
            return
        low = text.lower()
        if low.startswith("failed (exit "):
            return
        if low.startswith(("pipeline completed with errors", "error summary",
                           "stopped - missing dependency")):
            return
        if text.strip("- ") == "" or set(text) == {"-"}:
            return
        if len(self.errors) < ERR_SUMMARY_CAP + 1:
            self.errors.append(text)


def error_summary(sink, emit) -> None:
    """Print the collected errors under the FAILED verdict.

    Runs on the UI thread after the queue has drained, so it lands at the very
    bottom with every error already recorded. A failed run with no captured
    error lines still says so, rather than leaving a bare FAILED unexplained.
    """
    emit("")
    emit(_RULE)
    if not sink.errors:
        emit("  ERROR SUMMARY: no error lines were captured -- check the "
             "stage output above for the failure.")
        emit(_RULE)
        return
    shown = sink.errors[:ERR_SUMMARY_CAP]
    extra = len(sink.errors) - len(shown)
    emit(f"  ERROR SUMMARY ({len(sink.errors)}"
         f"{'+' if extra else ''} error line"
         f"{'' if len(sink.errors) == 1 else 's'}):")
    for text in shown:
        emit(f"    - {text}")
    if extra:
        emit(f"    ... and {extra} more (see the log above)")
    emit(_RULE)


#: Slack in the scrollbar fraction that still counts as "at the bottom".
_BOTTOM_EPSILON = 0.0001


def _is_at_bottom(log_text) -> bool:
    """Whether the pane is scrolled to the end, so new lines should follow.

    Read BEFORE the insert: afterwards the fraction has already moved.  A
    pane too short to scroll yields (0.0, 1.0), which counts as the bottom.
    """
    try:
        return log_text.yview()[1] >= 1.0 - _BOTTOM_EPSILON
    except Exception:
        return True


def bind(app, sink, log_text) -> None:
    """Bind `app.log` and `app.clear_log` onto the carrier."""
    def _log(line: str, follow: bool = None):
        """Color one line into the pane and mirror it to the run log.

        `follow` is the caller's already-made decision for a whole burst of
        lines; None means decide for this line alone.
        """
        if follow is None:
            follow = _is_at_bottom(log_text)
        log_text.configure(state="normal")
        tag = classify(sink.banner, line)
        sink.record_error(line, tag)
        log_text.insert("end", line + "\n", tag) if tag else \
            log_text.insert("end", line + "\n")
        if follow:
            log_text.see("end")
        log_text.configure(state="disabled")
        sink.write(line)

    def _clear():
        """Empty the pane, leaving the run log alone."""
        log_text.configure(state="normal")
        log_text.delete("1.0", "end")
        log_text.configure(state="disabled")

    app.log = _log
    app.clear_log = _clear


def build_pane(app, parent):
    """Build the Output Log header and text widget; returns the Text."""
    import tkinter as tk
    from tkinter import ttk


    header = tk.Frame(parent, bg=CLR["panel"], height=34)
    header.pack(fill=tk.X)
    header.pack_propagate(False)
    tk.Label(header, text="Output Log", bg=CLR["panel"], fg=CLR["subtext"],
             font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=12, pady=8)

    text = tk.Text(parent, wrap=tk.WORD, font=("Consolas", 9),
                   bg=CLR["log_bg"], fg=CLR["log_fg"],
                   insertbackground=CLR["text"],
                   selectbackground=CLR["accent"], relief="flat",
                   borderwidth=0, state=tk.DISABLED, padx=10, pady=8)
    scroll = ttk.Scrollbar(parent, command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    text.pack(fill=tk.BOTH, expand=True)

    bold = ("Consolas", 9, "bold")
    text.tag_configure("head", foreground=CLR["accent"], font=bold)
    text.tag_configure("ok", foreground=CLR["log_ok"])
    text.tag_configure("err", foreground=CLR["log_err"])
    text.tag_configure("warn", foreground=CLR["log_warn"])
    text.tag_configure("cmd", foreground=CLR["blue"], font=bold)
    text.tag_configure("dim", foreground=CLR["subtext"])
    app.log_text = text
    return text


# ---------------------------------------------------------------------------
#  Running / idle state
# ---------------------------------------------------------------------------

def plugin_esm(app, out_root, plugin: str):
    """The converted plugin file, wherever its mod's folder is."""
    from output_layout import plugin_esm as resolve

    return resolve(out_root, plugin, EXPORT_DIR)


def _format_elapsed(seconds: float) -> str:
    """`mm:ss`, or `hh:mm:ss` once a run passes the hour."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def bind_timer(app) -> None:
    """Bind `app.start_timer` and `app.stop_timer` onto the carrier."""
    job = [None]
    started = [0.0]

    def _tick():
        """Repaint the elapsed time and schedule the next second."""
        app.timer_var.set(_format_elapsed(time.monotonic() - started[0]))
        job[0] = app.root.after(1000, _tick)

    def _start():
        """Reset the clock and begin ticking."""
        started[0] = time.monotonic()
        _tick()

    def _stop():
        """Cancel the pending tick, if any."""
        if job[0] is not None:
            app.root.after_cancel(job[0])
            job[0] = None

    app.start_timer = _start
    app.stop_timer = _stop


def _enter_running(app) -> None:
    """Show the progress bar and start the clock."""
    app.prog_bar.grid()
    app.prog_bar.start(12)
    app.status_var.set("Running...")
    app.start_timer()


def _leave_running(app) -> None:
    """Hide the progress bar and refresh what the run may have invalidated.

    A conversion that just added a plugin changes what the global actions
    would produce, so a previously-done one goes live again.
    """
    app.prog_bar.stop()
    app.prog_bar.grid_remove()
    app.status_var.set("Ready")
    app.stop_timer()
    app.refresh_upgrade_notice(auto_apply=False)
    app.refresh_global_btns()


def bind_set_running(app) -> None:
    """Bind `app.set_running`, the one switch for the whole window's state."""
    def _set_running(state: bool):
        """Move the window into or out of its running state."""
        app.running.set() if state else app.running.clear()
        if not state:
            app.cancel_evt.clear()
        app.run_btn.configure(state="disabled" if state else "normal")
        app.cancel_btn.configure(state="normal" if state else "disabled",
                                 text="Cancel")
        app.file_combo.configure(state="disabled" if state else "normal")
        (_enter_running if state else _leave_running)(app)
        app.update_run_btn()

    app.set_running = _set_running


def cancel_run(app) -> None:
    """Ask the in-flight run to stop, and say so in the window."""
    if not app.running.is_set():
        return
    app.cancel_evt.set()
    app.status_var.set("Cancelling...")
    app.cancel_btn.configure(state="disabled", text="Cancelling...")
    app.log("")
    app.log("  Cancelling — killing running processes...")


# ---------------------------------------------------------------------------
#  Building commands, and what is already done
# ---------------------------------------------------------------------------

def winding_flag(app) -> str:
    """The explicit collision-winding flag matching the setting."""
    return ("--collision-winding-fix" if app.winding_on()
            else "--no-collision-winding-fix")


def _mesh_flags(app, selected_subdirs) -> list:
    """The extra flags the Meshes step takes."""
    flags = []
    if selected_subdirs:
        flags += ["--mesh-subdirs"] + list(selected_subdirs)
    flags.append(winding_flag(app))
    if app.parallax_var.get():
        flags.append("--parallax")
        if app.tex_only_var.get():
            flags.append("--textures-only")
    return flags


def build_cmd(app, step_key: str, fname: str, out_dir: str,
              selected_subdirs=None) -> list:
    """The convert.py command for a single pipeline step."""
    _, flag, _, _, _, needs_file = next(s for s in STEPS if s[0] == step_key)
    cmd = [sys.executable, "-u", str(REPO_ROOT / "convert.py"), flag]
    if needs_file and fname:
        cmd += ["-f", fname]
    if out_dir:
        cmd += ["--output-dir", out_dir]
    if step_key == "meshes":
        cmd += _mesh_flags(app, selected_subdirs)
    return cmd


def _tool(*parts) -> list:
    """`python -u <repo>/<parts...>` as the head of a command."""
    return [sys.executable, "-u", str(REPO_ROOT.joinpath(*parts))]


def _with_out(cmd: list, out_dir: str) -> list:
    """Append `--output-dir` when one is configured."""
    return cmd + (["--output-dir", out_dir] if out_dir else [])


def _cmd_start_mod(app, out_dir: str) -> list:
    """Zip the prebuilt starter mod."""
    return _with_out(_tool("tools", "release", "package_start_mod.py"), out_dir)


def _cmd_runtime_dll(app, out_dir: str) -> list:
    """Zip the built runtime DLLs as one SKSE mod."""
    return _with_out(_tool("tools", "release", "package_runtime_dll.py"),
                     out_dir)


def _cmd_pack_lod(app, out_dir: str) -> list:
    """Zip the baked AutoConvertLOD folder."""
    return _with_out(_tool("tools", "release", "pack_lod.py"), out_dir)


def _cmd_convert_ui(app, out_dir: str) -> list:
    """Build the UI reskin, limited to the movies the dialog chose."""
    cmd = _with_out(_tool("tools", "misc", "convert_ui.py"), out_dir)
    chosen = app.selection.convert_ui
    if not chosen["messagebox"]:
        cmd += ["--no-messagebox"]
    if not chosen["cursor"]:
        cmd += ["--no-cursor"]
    return cmd


def _cmd_make_master(app, out_dir: str) -> list:
    """Flag the chosen plugins ESM, in dependency order."""
    from core.gui.panels import default_master_plugins

    cmd = _tool("tools", "esm", "make_master.py")
    cmd += app.selection.master_plugins or default_master_plugins(app)
    return _with_out(cmd, out_dir)


def _cmd_create_lod(app, out_dir: str) -> list:
    """Bake LOD for the whole load order.

    The lists are passed only once the dialog has been confirmed: the ORDER is
    the conflict resolution, so a confirmed run must apply exactly what the
    user saw. Before that the tool derives both itself, which keeps a
    menu-less run correct as their load order changes.
    """
    cmd = _with_out(_tool("tools", "release", "create_lod.py"), out_dir)
    if app.selection.lod_plugins:
        cmd += ["--plugins"] + list(app.selection.lod_plugins)
    if app.selection.lod_worldspaces:
        cmd += ["--worldspaces"] + list(app.selection.lod_worldspaces)
    return cmd


def _cmd_body_patch(app, out_dir: str) -> list:
    """Build the slot-44 body patch for the chosen Skyrim plugins."""
    cmd = _with_out(_tool("convert.py") + ["--modify-body-meshes"], out_dir)
    pairs = app.selection.patch_plugins
    chosen = [n for n, v in pairs if v.get()]
    if chosen and chosen != [n for n, _ in pairs]:
        cmd += ["--patch-plugins"] + chosen
    return cmd


#: Global action -> the function building its argv.
_CMD_FNS = {
    "package_start_mod": _cmd_start_mod,
    "package_runtime_dll": _cmd_runtime_dll,
    "pack_lod": _cmd_pack_lod,
    "convert_ui": _cmd_convert_ui,
    "make_master": _cmd_make_master,
    "create_lod": _cmd_create_lod,
}


def global_cmd(app, key: str, out_dir: str) -> list:
    """The argv for one global action."""
    return _CMD_FNS.get(key, _cmd_body_patch)(app, out_dir)


def _artifact_start_mod():
    """The starter mod's zip name."""
    return "TESGameSelect.zip"


def _artifact_convert_ui():
    """The UI mod's zip name, spelled once in the tool that produces it."""
    from tools.misc.convert_ui import MOD_NAME

    return f"{MOD_NAME}.zip"


def _artifact_pack_lod():
    """The LOD mod's zip name."""
    from asset_convert.lod.sibling_lod import LOD_DIR_NAME

    return f"{LOD_DIR_NAME}.zip"


#: Global action -> the file under Finished Mods that proves it ran.
_ARTIFACTS = {
    "package_start_mod": _artifact_start_mod,
    "package_runtime_dll": lambda: "TESRuntime.zip",
    "convert_ui": _artifact_convert_ui,
    "pack_lod": _artifact_pack_lod,
    "modify_body_meshes": lambda: f"{BODY_SLOTS_PATCH}.zip",
}


def global_artifact(app, key: str):
    """The file this action produces, or None when it has no single one.

    Uses FINISHED_DIR_NAME rather than `finished_dir()`: this only ASKS whether
    the artefact is there, and the helper would CREATE the folder as a side
    effect -- opening the GUI would leave an empty "Finished Mods" promising
    deliverables nothing has produced.
    """
    from output_layout import FINISHED_DIR_NAME

    name_fn = _ARTIFACTS.get(key)
    if name_fn is None:
        return None
    return Path(str(app.out_root())) / FINISHED_DIR_NAME / name_fn()


def _master_flags_done(stamp: str) -> bool:
    """True when every plugin in a make_master stamp reads back as ESM."""
    return bool(stamp) and all(part.endswith(":1")
                               for part in stamp.split("\x1f"))


def global_is_current(app, key: str, last_stamp: dict, stamp_of) -> bool:
    """Has this action run, and is its result still up to date?"""
    art = global_artifact(app, key)
    if art is not None and not art.exists():
        return False
    try:
        stamp = stamp_of(key)
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


# ---------------------------------------------------------------------------
#  Global-action input fingerprints
# ---------------------------------------------------------------------------

#: Field separator inside a stamp; never appears in a path or a name.
SEP = "\x1f"


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


def _stamp_start_mod(app) -> str:
    """The starter mod's SOURCES: packaging builds from them."""
    return (_tree_stamp(REPO_ROOT / "TESGameSelect" / "scripts" / "source")
            + _stat_part(REPO_ROOT / "tools" / "release"
                         / "make_game_select_esp.py", "gen", "missing"))


def _stamp_runtime_dll(app) -> str:
    """The built DLL alone, not the tree: obj/ churns on every compile."""
    return _stat_part(REPO_ROOT / "tes_runtime" / "dist" / "TESRuntime.dll",
                      "dll", "missing")


def _convert_ui_wanted(ui, cur, ob_dir, sk_dir) -> list:
    """(data dir, relative path) for every file the UI reskin reads."""
    from tools.misc.convert_ui import CURSOR_SWF, MESSAGE_BOX_SWF

    wanted = [(ob_dir, ui.MESSAGE_MENU_XML),
              (ob_dir, ui.GENERIC_BACKGROUND_XML),
              (sk_dir, MESSAGE_BOX_SWF),
              (sk_dir, CURSOR_SWF)]
    for directory, names in ((ui.BACKGROUND_TEXTURE_DIR, ui.BACKGROUND_TEXTURES),
                             (ui.FOCUS_TEXTURE_DIR, ui.FOCUS_TEXTURES),
                             (cur.CURSOR_TEXTURE_DIR, cur.CURSOR_TEXTURES)):
        wanted += [(ob_dir, os.path.join(directory, n)) for n in names]
    return wanted


def _stamp_convert_ui(app) -> str:
    """The game files the UI reskin reads, loose first then the archives.

    That is the same resolution order the tool uses, and only a handful of
    stats, because every global stamp runs before the window first paints.
    """
    from asset_convert.ui import ui_cursor as cur
    from asset_convert.ui import ui_menus as ui
    from tools.misc.convert_ui import find_data_dirs

    ob_dir, sk_dir = find_data_dirs()
    parts = []
    for data_dir, rel in _convert_ui_wanted(ui, cur, ob_dir, sk_dir):
        if not data_dir:
            parts.append(f"{rel}:?")
            continue
        loose = Path(data_dir) / Path(*rel.split("\\"))
        parts.append(_stat_part(loose, rel, "bsa"))
    archives = ((ob_dir, {"Oblivion - Misc.bsa",
                          "Oblivion - Textures - Compressed.bsa"}),
                (sk_dir, {"Skyrim - Interface.bsa"}))
    for data_dir, names in archives:
        for name in sorted(names):
            parts.append(_stat_part(Path(data_dir) / name if data_dir
                                    else Path(name), name, "?"))
    return SEP.join(parts)


def _stamp_pack_lod(app) -> str:
    """The baked LOD folder this zip would copy."""
    from asset_convert.lod.sibling_lod import LOD_DIR_NAME

    return _tree_stamp(Path(str(app.out_root())) / LOD_DIR_NAME)


def _stamp_make_master(app) -> str:
    """Each selected plugin's ESM flag, read back off disk."""
    import sys

    from core.gui.panels import default_master_plugins

    names = app.selection.master_plugins or default_master_plugins(app)
    out_dir = str(app.out_root())
    out = []
    for n in names:
        try:
            sys.path.insert(0, str(REPO_ROOT))
            from tools.esm.make_master import FLAG_ESM, read_header, resolve
            flags, _m = read_header(resolve(n, out_dir))
            out.append(f"{n}:{1 if flags & FLAG_ESM else 0}")
        except Exception:
            out.append(f"{n}:?")
    return SEP.join(out)


def _converted_names(out_dir: str) -> list:
    """Every converted plugin in `out_dir`, sorted; [] if unreadable."""
    try:
        from asset_convert.lod.sibling_lod import converted_plugins
        return sorted(converted_plugins(Path(out_dir)))
    except Exception:
        return []


def _lod_extra(app, out_dir: str, plugin_esm) -> list:
    """Create LOD's own inputs: the plugin order, then the worldspace filter."""
    from core.gui.panels import default_lod_plugins

    names = app.selection.lod_plugins or default_lod_plugins(app)
    parts = ["|"] + list(names)
    if app.selection.lod_worldspaces:
        return parts + ["|"] + list(app.selection.lod_worldspaces)
    root = Path(out_dir)
    sig = []
    for n in names:
        try:
            st = plugin_esm(root, n).stat()
            sig.append(f"{n}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            sig.append(f"{n}:-")
    return parts + ["|"] + sig


def _stamp_converted_set(app, key: str, plugin_esm) -> str:
    """The converted plugin set, plus whatever else `key` depends on."""
    out_dir = str(app.out_root())
    parts = _converted_names(out_dir)
    if key == "modify_body_meshes":
        parts += ["|"] + sorted(n for n, v in app.selection.patch_plugins
                                if v.get())
    if key == "create_lod":
        parts += _lod_extra(app, out_dir, plugin_esm)
    return SEP.join(parts)


#: Actions whose inputs are NOT the converted plugin set.
_STAMP_FNS = {
    "package_start_mod": _stamp_start_mod,
    "package_runtime_dll": _stamp_runtime_dll,
    "convert_ui": _stamp_convert_ui,
    "pack_lod": _stamp_pack_lod,
    "make_master": _stamp_make_master,
}


def global_stamp(app, key: str, plugin_esm) -> str:
    """A fingerprint of everything `key`'s result depends on."""
    fn = _STAMP_FNS.get(key)
    if fn is not None:
        return fn(app)
    return _stamp_converted_set(app, key, plugin_esm)


# ---------------------------------------------------------------------------
#  Preparing and executing a run
# ---------------------------------------------------------------------------

#: Exit code -> the verdict line the log ends with.
_VERDICTS = {
    RC_CANCELLED: "  CANCELLED",
    0: "  DONE",
}



def verdict_line(ret: int, missing_dep: int) -> str:
    """The closing line for a run that exited `ret`."""
    if ret == missing_dep:
        return "  STOPPED - MISSING DEPENDENCY (see above)"
    return _VERDICTS.get(ret, f"  FAILED (exit {ret})")


def validate_run(app, info):
    """(fname, out_dir, steps, subdirs), or None with a card already shown.

    The plugin box is typable, so the text may not name a real plugin.
    `all_plugins` tracks the ACTIVE source, so an imported mod's plugins pass
    this check -- and the message has to name the right source too.
    """
    fname = app.file_var.get()
    out_dir = app.output_var.get().strip()
    steps = [key for key, *_ in STEPS
             if app.step_vars[key].get() and runnable(app, key)]
    if not steps:
        info("No Steps", "Select at least one pipeline step.")
        return None
    if app.all_plugins and fname not in app.all_plugins:
        row = app.scope_rows.get(app.scope_var.get()) or {}
        info("Unknown Plugin",
             f"{fname!r} is not a plugin in "
             f"{row.get('label') or 'the selected source'}.\n"
             "Pick one from the list.")
        return None

    subdirs = None
    if "meshes" in steps and app.mesh_subdir_vars:
        chosen = [n for n, v in app.mesh_subdir_vars if v.get()]
        every = [n for n, _ in app.mesh_subdir_vars]
        if chosen and chosen != every:
            subdirs = chosen
    return fname, out_dir, steps, subdirs


def log_run_header(app, fname, steps, out_dir, subdirs) -> None:
    """Echo the run's settings into the log, before anything starts."""
    log = app.log
    log(f"File: {fname or '(none)'}")
    log(f"Steps: {step_names(steps)}")
    log(f"Output: {out_dir}")
    log(f"Workers: {app.get_workers()} (of {app.cpu_max})")
    if subdirs:
        log(f"Mesh subdirs: {', '.join(subdirs)}")
    if "meshes" in steps:
        log(f"Collision winding fix: {'on' if app.winding_on() else 'off'}")
        parallax = app.parallax_var.get()
        log(f"Parallax: {'on' if parallax else 'off'}"
            + (" (Community Shaders or ENB required in game)" if parallax
               else ""))
        if app.tex_only_var.get():
            log("Textures only: no meshes written (for PGPatcher)")


def _is_default_selection(app, steps) -> bool:
    """True when the ticked set is exactly the default one."""
    default_set = {k for k, *rest in STEPS if rest[3]}
    default_set &= default_on_steps(app.pack_default_var.get())
    return set(steps) == default_set


def pipeline_argv(app, fname, out_dir, steps, subdirs) -> list:
    """Every command this run executes, in order.

    One command when the selection is the default and nothing narrows it;
    otherwise one per step, so a failure can stop the rest.
    """
    if _is_default_selection(app, steps) and fname and not subdirs:
        cmd = [sys.executable, "-u", str(REPO_ROOT / "convert.py"),
               "-f", fname, winding_flag(app)]
        cmd += [flag for key, flag, *_ in STEPS if key in set(steps)]
        if out_dir:
            cmd += ["--output-dir", out_dir]
        return [cmd]
    return [build_cmd(app, step, fname, out_dir, subdirs) for step in steps]


def run_commands(app, cmds, q, env, missing_dep) -> int:
    """Run each command until one fails; returns the run's exit code.

    A missing dependency stops the rest: each step is its own process, so
    nothing else would prevent the remainder from producing half-converted
    output.
    """
    ret = 0
    for cmd in cmds:
        if app.cancel_evt.is_set():
            return RC_CANCELLED
        q.put(f"Running: {' '.join(cmd)}")
        r = run_process(cmd, q.put, env=env, cancel_event=app.cancel_evt)
        if r == RC_CANCELLED:
            return RC_CANCELLED
        if r == missing_dep:
            return r
        if r != 0:
            ret = r
    return ret


def make_drain(app, q, want_summary, on_idle):
    """A UI-thread pump that logs queued lines until the run ends.

    `on_idle` runs once, on the final pass: the worker is done and the queue is
    empty, so every error line has been through the log and recorded.
    """
    def _drain():
        """Log whatever is queued, then reschedule or finish.

        Whether to follow the tail is decided ONCE per burst: Tk does not
        recompute the scroll fraction until idle, so a per-line check reads a
        stale value mid-burst and the pane stops following.
        """
        follow = _is_at_bottom(app.log_text)
        try:
            while True:
                app.log(q.get_nowait(), follow=follow)
        except queue.Empty:
            pass
        if follow:
            app.log_text.see("end")
        if app.running.is_set():
            app.root.after(50, _drain)
            return
        on_idle(want_summary)

    return _drain


def start_worker(app, cmds, q, env, missing_dep, want_summary) -> None:
    """Run `cmds` on a background thread and post the verdict.

    The summary flag is set BEFORE `running` clears, so the drain pass that
    sees the run finished is guaranteed to see it too.
    """
    def _worker():
        """Execute the commands, then close the log with a verdict."""
        app.set_running(True)
        try:
            ret = run_commands(app, cmds, q, env, missing_dep)
            q.put("")
            q.put(verdict_line(ret, missing_dep))
            want_summary[0] = ret not in (0, RC_CANCELLED)
        except Exception as exc:
            q.put("")
            q.put(f"ERROR: {exc}")
            q.put("  FAILED")
            want_summary[0] = True
        finally:
            app.root.after(0, lambda: app.set_running(False))

    threading.Thread(target=_worker, daemon=True).start()


def start_global_worker(app, cmd, q, env, key, want_summary,
                        on_success) -> None:
    """Run one global action on a background thread and post the verdict.

    `on_success` runs only when the action exits 0: a failed or cancelled run
    must leave the button lit, not quietly mark the work done.
    """
    def _worker():
        """Execute the single command, then record the outcome."""
        app.set_running(True)
        ret = 1
        try:
            q.put(f"Running: {' '.join(cmd)}")
            ret = run_process(cmd, q.put, env=env,
                              cancel_event=app.cancel_evt)
            q.put("")
            q.put(_VERDICTS.get(ret, f"  FAILED (exit {ret})"))
        finally:
            want_summary[0] = ret not in (0, RC_CANCELLED)
            if ret == 0:
                on_success(key)
            app.root.after(0, lambda: app.set_running(False))

    threading.Thread(target=_worker, daemon=True).start()



def bind_log(app, log_pane):
    """Build the log pane, bind its sink, and return it."""
    text = build_pane(app, log_pane)
    sink = RunLogSink()
    bind(app, sink, text)
    app.log_sink = sink
    app.logs_dir = REPO_ROOT / "logs"
    return sink


def _run_finished(app, want_summary) -> None:
    """Close out a finished run: summary, size note, log end.

    Entered only when there is something to write -- with run logging disabled
    (logRunsKept: 0) the summary must still print.
    """
    sink = app.log_sink
    if not (want_summary[0] or sink.log is not None):
        return
    if want_summary[0]:
        want_summary[0] = False
        error_summary(sink, app.log)
    sink.size_note(app.log)
    sink.end(f"EXIT: {'OK' if not sink.errors else 'ERRORS'}")


def _run_env(app) -> dict:
    """The environment every child of this run inherits.

    The cache opt-out is only set when the user turned it OFF: the variable
    reads as "1/true means skip", so an unset value is the enabled default and
    a stale "1" inherited from the parent can never silently disable a run the
    user re-enabled.
    """
    from core.worker_budget import WORKERS_ENV_VAR
    from tools.navmesh.navmesh_cache import NO_DOWNLOAD_ENV_VAR

    env = {WORKERS_ENV_VAR: str(app.get_workers())}
    env[NO_DOWNLOAD_ENV_VAR] = "" if app.cache_dl_var.get() else "1"
    env.update(app.log_sink.env())
    return env


def _begin_run(app, header: dict) -> None:
    """Clear the pane and open this run's log, then echo where it went."""
    app.clear_log()
    app.log_sink.begin(header, app.logs_dir)
    app.log_sink.errors.clear()


def run_clicked(app, missing_dep) -> None:
    """Validate the selection, then run every step it asks for."""
    if app.running.is_set():
        return
    got = validate_run(app, app.info)
    if got is None:
        return
    fname, out_dir, steps, subdirs = got

    _begin_run(app, {"Command": "Pipeline run",
                     "File": fname,
                     "Steps": step_names(steps),
                     "Output": out_dir,
                     "Workers": str(app.get_workers())})
    log_run_header(app, fname, steps, out_dir, subdirs)
    app.log_sink.note(app.log)
    app.log("")

    q = queue.Queue()
    want_summary = [False]
    cmds = pipeline_argv(app, fname, out_dir, steps, subdirs)
    start_worker(app, cmds, q, _run_env(app), missing_dep, want_summary)
    app.root.after(50, make_drain(app, q, want_summary,
                                  lambda ws: _run_finished(app, ws)))


def start_global_action(app, key: str, record_done) -> None:
    """Launch one global action with the selection already settled."""
    from core.worker_budget import WORKERS_ENV_VAR

    if app.running.is_set():
        return
    out_dir = app.output_var.get().strip()
    cmd = global_cmd(app, key, out_dir)
    label = next(l for k, l, _t, _s, _r in GLOBAL_ACTIONS if k == key)

    _begin_run(app, {"Command": label, "Output": out_dir})
    app.log(label)
    app.log(f"Output: {out_dir}")
    app.log_sink.note(app.log)
    app.log("")

    q = queue.Queue()
    want_summary = [False]
    env = {WORKERS_ENV_VAR: str(app.get_workers())}
    env.update(app.log_sink.env())
    start_global_worker(app, cmd, q, env, key, want_summary, record_done)
    app.root.after(50, make_drain(app, q, want_summary,
                                  lambda ws: _run_finished(app, ws)))


def refresh_global_btns(app, is_current) -> None:
    """Grey out the actions whose result is current; light up the rest."""

    for gkey, _label, _tip, gshort, _row in GLOBAL_ACTIONS:
        btn = app.global_btns.get(gkey)
        if btn is None:
            continue
        if is_current(gkey):
            btn.configure(text=f"\u2713 {gshort}", style="GlobalDone.TButton")
        else:
            btn.configure(text=gshort, style="Global.TButton")
