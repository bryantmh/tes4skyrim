"""Build the portable Windows package: the converter plus its own Python.

A user unzips it and double-clicks `TES Auto-Convert.cmd`. No Python install,
no pip, no PATH: the package carries a CPython whose version matches the
committed native extensions, with requirements.txt already installed.

Usage:
  python tools/release/build_portable.py                # -> dist/TESAutoConvert-<version>-win64.zip
  python tools/release/build_portable.py --python DIR   # copy this CPython install
  python tools/release/build_portable.py --no-zip       # stage dist/TESAutoConvert/ only

See: docs/commentary/tools_portable_build.md#design
"""

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from core.subprocess_flags import POPEN_FLAGS
from version import current_version, is_dev_version

PACKAGE_NAME = "TESAutoConvert"

DIST_DIR = SCRIPT_DIR / "dist"

REQUIREMENTS = SCRIPT_DIR / "requirements.txt"

LAUNCHER_NAME = "TES Auto-Convert.cmd"

#: Double-clicked by the user; pythonw runs gui.py directly, so no console stays open.
LAUNCHER_TEXT = '@echo off\r\nstart "" "%~dp0python\\pythonw.exe" "%~dp0gui.py"\r\n'

#: Top-level entries of the interpreter install that the package never needs.
INTERPRETER_SKIP = frozenset({"doc", "include", "libs", "scripts", "__install__.json"})

#: Directories under the interpreter's Lib/ that are dropped or rebuilt.
LIB_SKIP = frozenset({"site-packages", "test", "idlelib", "__pycache__"})

#: Tracked paths that serve development only; everything else tracked ships.
APP_SKIP_PREFIXES = ("tests/", ".github/", ".claude/", ".vscode/")


def required_abi() -> str:
    """The `cpXY-win_amd64` tag every committed native extension shares.

    See: docs/commentary/tools_portable_build.md#abi
    """
    tags = {p.name.split(".")[1] for p in (SCRIPT_DIR / "native" / "dist").glob("*.pyd")}
    if len(tags) != 1:
        raise SystemExit(f"native/dist/ must hold one ABI tag, found {sorted(tags)}")
    return tags.pop()


def check_interpreter(prefix: Path, abi: str) -> None:
    """Refuse an install whose ABI differs from `abi` or that lacks tkinter."""
    exe = prefix / "python.exe"
    if not exe.is_file() or not (prefix / "pythonw.exe").is_file():
        raise SystemExit(f"{prefix} is not a CPython install (no python.exe/pythonw.exe)")
    probe = "import sysconfig, tkinter; print(sysconfig.get_config_var('EXT_SUFFIX'))"
    out = subprocess.run([str(exe), "-c", probe], capture_output=True, text=True,
                         **POPEN_FLAGS)
    suffix = out.stdout.strip()
    if out.returncode or suffix != f".{abi}.pyd":
        raise SystemExit(f"{exe} does not match {abi} with tkinter "
                         f"(got {suffix!r}, {out.stderr.strip()[-200:]!r})")


def _skip_in_interpreter(prefix: Path):
    """copytree `ignore` callback dropping INTERPRETER_SKIP and LIB_SKIP."""
    lib = prefix / "Lib"

    def ignore(directory, names):
        """The entries of `directory` to leave out of the copy."""
        here = Path(directory)
        if here == prefix:
            return [n for n in names if n.lower() in INTERPRETER_SKIP]
        if here == lib:
            return [n for n in names if n.lower() in LIB_SKIP]
        return [n for n in names if n == "__pycache__"]
    return ignore


def copy_interpreter(prefix: Path, dest: Path) -> Path:
    """Copy the interpreter without its packages; returns the copied python.exe."""
    shutil.copytree(prefix, dest, ignore=_skip_in_interpreter(prefix))
    (dest / "Lib" / "site-packages").mkdir()
    return dest / "python.exe"


def install_requirements(python_exe: Path) -> None:
    """Install pip, then requirements.txt, into the copied interpreter only."""
    env = dict(os.environ, PYTHONNOUSERSITE="1")
    env.pop("PYTHONPATH", None)
    for args in (["-m", "ensurepip", "--default-pip"],
                 ["-m", "pip", "install", "--disable-pip-version-check",
                  "--no-warn-script-location", "-r", str(REQUIREMENTS)]):
        if subprocess.run([str(python_exe), *args], env=env, **POPEN_FLAGS).returncode:
            raise SystemExit(f"{python_exe} {' '.join(args)} failed")


def app_files() -> list:
    """Repo-relative paths of every tracked file that ships."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=SCRIPT_DIR, capture_output=True,
                         check=True, **POPEN_FLAGS).stdout.decode("utf-8")
    return [p for p in out.split("\0")
            if p and not p.startswith(APP_SKIP_PREFIXES) and (SCRIPT_DIR / p).is_file()]


def copy_app(dest: Path) -> int:
    """Copy the shipped app files into `dest`; returns how many were copied."""
    files = app_files()
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SCRIPT_DIR / rel, target)
    return len(files)


def zip_tree(stage: Path, zip_path: Path) -> None:
    """Zip `stage` so the archive holds one top-level folder named after it."""
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, Path(stage.name) / path.relative_to(stage))


def build(prefix: Path, make_zip: bool) -> Path:
    """Stage dist/TESAutoConvert/ and optionally zip it; returns what was produced."""
    abi = required_abi()
    check_interpreter(prefix, abi)
    stage = DIST_DIR / PACKAGE_NAME
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    print(f"Interpreter: {prefix} ({abi})")
    python_exe = copy_interpreter(prefix, stage / "python")
    install_requirements(python_exe)
    print(f"App files:   {copy_app(stage)}")
    version = current_version()
    (stage / "VERSION").write_text(version + "\n", encoding="utf-8")
    print(f"Version:     {version}")
    if is_dev_version(version):
        print("NOTE: not a release tag, so the package reports itself as a "
              "development build. Build from a tagged commit to ship a release.")
    (stage / LAUNCHER_NAME).write_text(LAUNCHER_TEXT, encoding="ascii", newline="")
    if not make_zip:
        print(f"Staged -> {stage}")
        return stage

    zip_path = DIST_DIR / f"{PACKAGE_NAME}-{version}-win64.zip"
    zip_tree(stage, zip_path)
    print(f"Packaged -> {zip_path} ({zip_path.stat().st_size / 1e6:.0f} MB)")
    return zip_path


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="Build the portable Windows package.")
    ap.add_argument("--python", metavar="DIR", default=sys.base_prefix,
                    help="CPython install to copy (default: the one running this)")
    ap.add_argument("--no-zip", action="store_true",
                    help="Stage dist/TESAutoConvert/ without zipping it")
    args = ap.parse_args()
    build(Path(args.python), not args.no_zip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
