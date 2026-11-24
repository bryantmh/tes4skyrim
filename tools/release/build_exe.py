"""Build TESRACT.exe: the whole converter as one Nuitka-compiled file.

    pip install nuitka==4.3.5 zstandard
    python tools/release/build_exe.py [--out DIR] [--jobs N]

The exe holds the compiled Python, the window and every data file the pipeline reads
(external tools, generated tables, runtime DLLs). It unpacks once per version into
`.tesract/<version>` beside itself and writes export/, output/ and logs/ beside itself too
(`output_layout.DATA_ROOT`). Scripts the pipeline starts by path run as `TESRACT.exe --tesract-run
<module>` (`core.python_cmd`), so every module started that way is listed in RUN_MODULES.
See: docs/commentary/core_gui.md#single-exe
"""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import version as version_info
from output_layout import REPO_ROOT

#: Modules other TESRACT processes start by name (`core.python_cmd.python_cmd` call sites).
RUN_MODULES = ("convert", "tools.release.package_start_mod", "tools.release.package_runtime_dll",
               "tools.release.pack_lod", "tools.release.create_lod", "tools.misc.convert_ui", "tools.esm.make_master",
               "tools.creature.patch_body_slots", "tools.cellview.server", "tools.cellview.world",
               "tools.navmesh.rescan_mesh_caches")
#: Packages that third-party libraries import optionally and nothing TESRACT runs needs.
NOT_FOLLOWED = ("matplotlib", "tkinter", "pytest", "IPython", "pandas", "sympy", "PyQt5", "PySide6")
#: Compiled packages whose folder also ships, source included: see docs/commentary/core_gui.md#single-exe.
SOURCE_PACKAGES = ("pyffi",)
#: Packages imported by name at run time, included whole.
WHOLE_PACKAGES = ("tes5_import.navmesh",)
#: Tracked folders that are never read at run time.
SKIPPED_DIRS = ("tests/", "docs/", "references/", ".claude/", ".github/", ".vscode/", "native/src/",
                "game_bridge/plugin/")
#: Source and project files the pipeline never reads.
SKIPPED_SUFFIXES = (".py", ".pyc", ".cpp", ".c", ".h", ".hpp", ".vcxproj", ".sln", ".filters", ".gitignore",
                    ".gitattributes", ".md", ".bat", ".cmd", ".ps1", ".yml", ".pyw", ".code-workspace")
#: Top-level files for developers only.
SKIPPED_FILES = ("TODO.txt", "pyproject.toml", "requirements.txt", "VERSION")
#: Files outside the folders above that the window shows.
WINDOW_FILES = ("docs/assets/banner.png", "docs/assets/favicon.ico")
#: The exe's icon.
ICON = REPO_ROOT / "docs" / "assets" / "favicon.ico"


def data_files() -> list:
    """Every file in the tree (tracked, or new and not ignored) the exe needs besides the compiled code."""
    tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=REPO_ROOT,
                             capture_output=True, encoding="utf-8", check=True).stdout.splitlines()
    keep = [p for p in tracked if not p.startswith(SKIPPED_DIRS) and not p.endswith(SKIPPED_SUFFIXES)
            and p not in SKIPPED_FILES]
    return sorted(set(keep) | set(WINDOW_FILES))


def stage(folder: Path, version: str) -> int:
    """Copy the data files into `folder` (emptied first) and stamp VERSION; returns how many."""
    shutil.rmtree(folder, ignore_errors=True)
    files = data_files()
    for rel in files:
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, target)
    for name in SOURCE_PACKAGES:
        source = Path(importlib.util.find_spec(name).origin).parent
        shutil.copytree(source, folder / name, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / "VERSION").write_text(version + "\n", encoding="utf-8")
    return len(files)


def numeric(version: str) -> str:
    """"0.689+g3bafe55" -> "0.689.0.0": Windows wants four numbers."""
    parts = [p for p in version.split("+")[0].split("-")[0].split(".") if p.isdigit()]
    return ".".join((parts + ["0"] * 4)[:4])


def nuitka_cmd(staged: Path, out: Path, version: str, jobs: int) -> list:
    """The Nuitka command line."""
    cmd = [sys.executable, "-m", "nuitka", "--onefile", "--assume-yes-for-downloads",
           f"--output-dir={out}", "--output-filename=TESRACT.exe", f"--jobs={jobs}",
           "--onefile-tempdir-spec={PROGRAM_DIR}/.tesract/{VERSION}", "--onefile-cache-mode=cached",
           "--windows-console-mode=attach", f"--windows-icon-from-ico={ICON}",
           "--company-name=TESRACT", "--product-name=TESRACT", f"--product-version={numeric(version)}",
           f"--file-version={numeric(version)}", "--file-description=T.E.SR.A.C.T",
           f"--include-raw-dir={staged}=.", "--include-package-data=slint"]
    cmd += [f"--include-module={m}" for m in RUN_MODULES]
    cmd += [f"--include-package={p}" for p in WHOLE_PACKAGES]
    cmd += [f"--nofollow-import-to={p}" for p in NOT_FOLLOWED]
    return cmd + [str(REPO_ROOT / "gui.py")]


def link_into_checkout(exe: Path) -> Path:
    """Hardlink `exe` into the checkout's root, where it uses the checkout's export/, output/ and config.

    A running copy cannot be deleted, so it is renamed to `.old`, which the exe removes at its next start.
    See: docs/commentary/core_gui.md#dev-link
    """
    link = REPO_ROOT / exe.name
    try:
        link.unlink(missing_ok=True)
    except PermissionError:
        link.replace(link.with_name(link.name + ".old"))
    os.link(exe, link)
    return link


def main() -> int:
    """Stage the data, compile, and report where TESRACT.exe went."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO_ROOT / "build" / "exe"), help="folder for TESRACT.exe")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="C compiler jobs")
    ap.add_argument("--low-memory", action="store_true",
                    help="compress the payload with less memory (a somewhat larger exe) on a busy machine")
    args = ap.parse_args()
    out = Path(args.out)
    version = version_info.current_version()
    count = stage(out / "data", version)
    print(f"Staged {count} data files for {version}", flush=True)
    cmd = nuitka_cmd(out / "data", out, version, args.jobs) + (["--low-memory"] if args.low_memory else [])
    code = subprocess.call(cmd, cwd=out)
    exe = out / "TESRACT.exe"
    if code == 0 and exe.is_file():
        print(f"Built {exe} ({exe.stat().st_size / 1048576:,.0f} MB); linked as {link_into_checkout(exe)}")
    return code


if __name__ == "__main__":
    sys.exit(main())
