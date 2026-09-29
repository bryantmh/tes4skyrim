"""Compile converted Papyrus `.psc` sources to `.pex`.

Batch compilation first; on a batch failure (one parser error stops the whole
run) it falls back to per-file so every valid script still produces output.
The vanilla headers come from the CK's two loose layouts or from
`Data/Scripts.zip`, which is unpacked in place.

Split out of convert.py, which orchestrates phases rather than running a
compiler.
See: docs/commentary/script_convert.md#batch-compilation
"""

import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core.subprocess_flags import POPEN_FLAGS as _POPEN_FLAGS, windows_cmd
from core.worker_budget import worker_count
from output_layout import plugin_out_root, record_dir
from source_paths import find_game_path

#: TESConversion root, for the bundled compiler and as the compiler's cwd.
SCRIPT_DIR = Path(__file__).parent.resolve()

#: One compiler diagnostic: `<path>.psc:<line>:<col>: <message>`.
_PSC_ERR_RE = re.compile(r'^.*?([^\\/:]+\.psc):\d+:\d+:\s*(.*)$')

#: How many times a failing batch is retried with its bad scripts removed.
_MAX_BATCH_RETRIES = 25

#: Console cap on reported failures; the full list always goes to the log.
_ERR_SAMPLE_CAP = 10

#: CK vanilla-source layouts, modern first. See: docs/commentary/script_convert.md#vanilla-headers
_HEADER_DIRS = (("Source", "Scripts"), ("Scripts", "Source"))


# ---------------------------------------------------------------------------
# Locating the vanilla headers
# ---------------------------------------------------------------------------

def _is_header_dir(d: Path) -> bool:
    """A directory holding the vanilla headers, identified by Debug.psc."""
    return d.is_dir() and (d / "Debug.psc").is_file()


def find_skse_source_scripts(config: dict) -> str:
    """SKSE's own header directory, or '' when it is not installed.

    SKSE installs alongside the vanilla headers rather than into them (vanilla
    holds Debug.psc, SKSE holds UI.psc), so a build that calls an SKSE native
    needs BOTH on the compiler's include path.
    See: docs/commentary/script_convert.md#menumode-with-a-menu-id
    """
    data = config.get("tes5DataPath") or find_game_path("skyrimse")
    if not data:
        return ""
    data_dir = Path(data)
    if data_dir.name.lower() != "data":
        data_dir = data_dir / "Data"
    for parts in _HEADER_DIRS:
        cand = data_dir.joinpath(*parts)
        if cand.is_dir() and (cand / "UI.psc").is_file():
            return str(cand)
    return ""


def _extract_scripts_zip(zip_path: Path, data_dir: Path) -> str:
    """Unpack the Papyrus sources out of Data/Scripts.zip, in place.

    Returns the directory holding the headers, or '' when it yields none.
    See: docs/commentary/script_convert.md#vanilla-headers
    """
    import zipfile
    dest = data_dir / "Source" / "Scripts"
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if n.lower().endswith('.psc')]
            if not names:
                return ""
            dest.mkdir(parents=True, exist_ok=True)
            for name in names:
                target = dest / Path(name).name
                if target.is_file():
                    continue
                with z.open(name) as fh, open(target, 'wb') as out:
                    shutil.copyfileobj(fh, out)
    except (OSError, zipfile.BadZipFile) as e:
        print(f"  WARNING: could not read {zip_path}: {e}")
        return ""
    return str(dest) if _is_header_dir(dest) else ""


def find_skyrim_source_scripts(config: dict) -> str:
    """The Papyrus header directory, unpacking Scripts.zip if needed.

    The single lookup every caller uses, so preflight, the release tools and
    the compile phase all agree on where the headers are. SKSE's own headers
    sit in a SEPARATE directory; `find_skse_source_scripts` finds those.
    See: docs/commentary/script_convert.md#vanilla-headers
    """
    data = config.get("tes5DataPath") or find_game_path("skyrimse")
    if not data:
        return ""
    data_dir = Path(data)
    if data_dir.name.lower() != "data":
        data_dir = data_dir / "Data"
    for parts in _HEADER_DIRS:
        cand = data_dir.joinpath(*parts)
        if _is_header_dir(cand):
            return str(cand)
    zip_path = data_dir / "Scripts.zip"
    if zip_path.is_file():
        found = _extract_scripts_zip(zip_path, data_dir)
        if found:
            return found
    return ""


# ---------------------------------------------------------------------------
# Running the compiler
# ---------------------------------------------------------------------------

class _Compiler:
    """One plugin's compile run: the paths, the two strategies, the report."""

    def __init__(self, file_name, compiler, headers, script_src, script_out,
                 master_src_dirs, skse_headers=""):
        """Hold the resolved paths for one plugin's compile."""
        self.file_name = file_name
        self.compiler = compiler
        self.headers = headers
        self.skse_headers = skse_headers
        self.script_src = script_src
        self.script_out = script_out
        self.master_src_dirs = master_src_dirs
        self.psc_files = sorted(script_src.glob("*.psc"))

    def _header_args(self) -> list:
        """`-h`: vanilla headers, SKSE's, this plugin's, then its masters'."""
        args = ["-h", str(self.headers)]
        if self.skse_headers:
            args += ["-h", str(self.skse_headers)]
        args += ["-h", str(self.script_src)]
        for d in self.master_src_dirs:
            args += ["-h", str(d)]
        return args

    def _run(self, argv: list, timeout: int):
        """Run the compiler, returning (combined output, return code)."""
        r = subprocess.run(windows_cmd(argv), capture_output=True, text=True,
                           timeout=timeout, cwd=str(SCRIPT_DIR), **_POPEN_FLAGS)
        return (r.stdout or "") + (r.stderr or ""), r.returncode

    def _stage_dir(self, quarantine: set) -> Path:
        """The batch input dir: a staging copy once any file is quarantined.

        Built once and maintained incrementally.
        See: docs/commentary/script_convert.md#batch-compilation
        """
        if not quarantine:
            return self.script_src
        stage = self.script_out / "_batch_src"
        if not stage.is_dir():
            stage.mkdir(parents=True, exist_ok=True)
            for p in self.psc_files:
                shutil.copy2(p, stage / p.name)
        for name in quarantine:
            try:
                (stage / name).unlink()
            except FileNotFoundError:
                pass
        return stage

    def compile_batch(self, quarantine: set) -> tuple:
        """Compile the whole source dir in ONE process -> (ok, errors, bad).

        See: docs/commentary/script_convert.md#batch-compilation
        """
        argv = [str(self.compiler), "compile", "-nocache",
                "-i", str(self._stage_dir(quarantine)),
                "-o", str(self.script_out)] + self._header_args()
        try:
            combined, _rc = self._run(argv, 1800)
        except Exception as e:
            return (False, [f"batch: {e}"], set())

        bad, errors = set(), []
        for line in combined.splitlines():
            m = _PSC_ERR_RE.match(line.strip())
            if m:
                bad.add(m.group(1))
                errors.append(f"{m.group(1)}: {m.group(2).strip()}")
        ok = not bad and "failed to compile" not in combined
        if not ok and not bad:
            errors.append("batch failed without naming a file")
        return (ok, errors, bad)

    def compile_one(self, psc: Path) -> tuple:
        """Compile one script -> (ok, message).

        See: docs/commentary/script_convert.md#creation-kit-papyrus-compiler-contracts
        """
        pex_path = self.script_out / (psc.stem + ".pex")
        argv = ([str(self.compiler), "compile", "-nocache",
                 "-i", str(psc), "-o", str(self.script_out)]
                + self._header_args())
        try:
            combined, rc = self._run(argv, 60)
        except Exception as e:
            return (False, str(e))
        if rc == 0 and pex_path.is_file():
            return (True, "")
        for line in combined.splitlines():
            if "error" in line.lower():
                return (False, line.strip())
        return (False, f"exit code {rc}")

    def run_batches(self) -> tuple:
        """Retry the batch until it passes or names no new file.

        Returns (quarantine, give_up); `give_up` means the caller must fall
        back to per-file compilation.
        """
        quarantine: set = set()
        for _attempt in range(_MAX_BATCH_RETRIES):
            ok, _errs, bad = self.compile_batch(quarantine)
            if ok:
                return quarantine, False
            new_bad = bad - quarantine
            if not new_bad:
                return quarantine, True
            quarantine |= new_bad
            print(f"  batch: quarantining {len(new_bad)} failing script(s), "
                  f"retrying ({len(quarantine)} total)")
        return quarantine, True

    def recheck(self, quarantine: set) -> tuple:
        """Compile the quarantined scripts alone -> (ok_count, errors).

        A file is often dragged into a batch failure by a DEPENDENCY's error.
        """
        ok_count, errors = 0, []
        for name in sorted(quarantine):
            success, msg = self.compile_one(self.script_src / name)
            if success:
                ok_count += 1
            else:
                errors.append(f"{name}: {msg}")
        return ok_count, errors

    def per_file(self) -> tuple:
        """Compile every script individually -> (ok_count, errors)."""
        ok_count, errors = 0, []
        with ThreadPoolExecutor(max_workers=worker_count()) as pool:
            futures = {pool.submit(self.compile_one, p): p
                       for p in self.psc_files}
            for fut in as_completed(futures):
                success, msg = fut.result()
                if success:
                    ok_count += 1
                else:
                    errors.append(f"{futures[fut].name}: {msg}")
        return ok_count, errors


def _master_chain(file_name: str, export_root: str) -> list:
    """Every plugin `file_name` inherits from, nearest master first.

    A header lists masters in load order, so each level is walked from its
    LAST master back. The walk is transitive and cycle-safe. A master is
    resolved through `record_dir`, never by joining its name onto the export
    root: plugins imported from one mod archive share a folder named for the
    MOD, so a plain join misses them.
    See: docs/commentary/script_convert.md#vanilla-headers
    """
    from script_convert.cross_ref import master_names
    ordered, seen, queue = [], {file_name.lower()}, [file_name]
    while queue:
        for name in reversed(master_names(record_dir(export_root, queue.pop(0)))):
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            ordered.append(name)
            queue.append(name)
    return ordered


def _master_source_dirs(file_name: str, out_root: Path) -> list:
    """Header dirs for the masters' converted scripts, ancestors included.

    A plugin two levels from the masterless root still needs that root's
    static scripts -- TES4Polyfill is owned by the masterless plugin alone,
    and every generated body calls it.
    See: docs/commentary/script_convert.md#vanilla-headers
    """
    export_root = str(SCRIPT_DIR / "export")
    dirs = []
    for m in _master_chain(file_name, export_root):
        d = plugin_out_root(out_root, m, export_root) / "scripts" / "source"
        if d.is_dir():
            dirs.append(d)
        else:
            print(f"[{file_name}] WARNING: master scripts not found ({d}); "
                  f"scripts referencing {m}'s script types will not compile")
    return dirs


def _write_error_log(script_out: Path, errors: list) -> None:
    """Dump every failure beside the scripts; remove a stale log on success.

    The console list is capped, which hides the long tail; a log left behind
    by an earlier run makes a green build look red.
    """
    log_path = script_out / "compile_errors.log"
    try:
        if errors:
            log_path.write_text("\n".join(sorted(errors)) + "\n",
                                encoding="utf-8")
            print(f"  full error list: {log_path}")
        else:
            log_path.unlink(missing_ok=True)
    except OSError:
        pass


def _report(file_name: str, ok_count: int, total: int, errors: list,
            script_out: Path) -> None:
    """Print the compile summary and write the full error log."""
    print(f"[{file_name}] Compilation: {ok_count}/{total} succeeded, "
          f"{len(errors)} failed")
    for sample in sorted(errors)[:_ERR_SAMPLE_CAP]:
        print(f"  {sample}")
    if len(errors) > _ERR_SAMPLE_CAP:
        print(f"  ... and {len(errors) - _ERR_SAMPLE_CAP} more failures")
    _write_error_log(script_out, errors)


def _prepare(file_name: str, config: dict, output_dir):
    """The compiler run for this plugin, or (None, result) if it cannot run.

    `result` is True when there is simply nothing to compile -- a plugin may
    legitimately convert zero scripts, which every other phase reports as
    success -- and False for a missing compiler or missing vanilla headers.
    """
    out_root = Path(output_dir) if output_dir else SCRIPT_DIR / "output"
    pout = plugin_out_root(out_root, file_name, str(SCRIPT_DIR / "export"))
    script_src, script_out = pout / "scripts" / "source", pout / "scripts"

    if not script_src.is_dir() or not any(script_src.glob("*.psc")):
        print(f"[{file_name}] No .psc scripts found, skipping compile")
        return None, True

    compiler = SCRIPT_DIR / "external" / "papyrus-compiler" / "papyrus.exe"
    if not compiler.is_file():
        print(f"[{file_name}] ERROR: papyrus compiler not found at {compiler}")
        return None, False

    headers = find_skyrim_source_scripts(config)
    if not headers:
        print(f"[{file_name}] ERROR: Skyrim Papyrus source headers not found")
        print("  Expected at: <Skyrim SE>\\Data\\Source\\Scripts\\")
        return None, False

    script_out.mkdir(parents=True, exist_ok=True)
    return _Compiler(file_name, compiler, headers, script_src, script_out,
                     _master_source_dirs(file_name, out_root),
                     find_skse_source_scripts(config)), None


def phase_compile(file_name: str, config: dict, output_dir: str = None):
    """Compile this plugin's converted .psc to .pex.

    One compiler process for the whole directory, so a healthy build never
    spawns thousands; only the files the batch names as broken are retried
    alone, and a batch that cannot isolate its failure falls back to per-file.
    See: docs/commentary/script_convert.md#batch-compilation
    """
    run, early = _prepare(file_name, config, output_dir)
    if run is None:
        return early

    total = len(run.psc_files)
    print(f"[{file_name}] Compiling {total} Papyrus scripts...")
    started = time.time()

    quarantine, give_up = run.run_batches()
    shutil.rmtree(run.script_out / "_batch_src", ignore_errors=True)

    if give_up:
        print("  batch compile could not isolate the failure; "
              "falling back to per-file compilation")
        ok_count, errors = run.per_file()
        print(f"  Per-file compile: {time.time() - started:.1f}s")
    else:
        ok_count = total - len(quarantine)
        errors = []
        if quarantine:
            print(f"  batch: {ok_count} compiled; re-checking "
                  f"{len(quarantine)} quarantined script(s) individually...")
            rechecked, errors = run.recheck(quarantine)
            ok_count += rechecked
        print(f"  Batch compile: {time.time() - started:.1f}s")

    _report(file_name, ok_count, total, errors, run.script_out)
    return ok_count > 0
