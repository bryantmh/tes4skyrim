"""Build TESRACT: the whole converter as a Nuitka-compiled folder, `build/exe/TESRACT/`.

    pip install nuitka==4.3.5
    python tools/release/build_exe.py [--out DIR] [--jobs N] [--zip] [--data-root DIR]

The folder holds the launcher TESRACT.exe and app/: the compiled app (TESRACT.exe, its DLLs, the
compiled Python) and every data file the pipeline reads (SHIPPED); nothing is unpacked at run time.
A release is that folder zipped (`--zip`). Scripts the pipeline starts by path run as
`app/TESRACT.exe --tesract-run <module>` (`core.python_cmd`), so every module started that way is
listed in RUN_MODULES.
See: docs/commentary/core_gui.md#folder-build
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import version as version_info
from core.gui import exe_update, splash
from core.navmesh_options import CORRIDOR
from output_layout import DATA_ROOT_FILE, REPO_ROOT
from tes5_import.navmesh import pool as navmesh_pool

#: Modules other TESRACT processes start by name (`core.python_cmd.python_cmd` call sites).
RUN_MODULES = ("convert", "tools.release.package_start_mod", "tools.release.package_runtime_dll",
               "tools.release.pack_lod", "tools.release.create_lod", "tools.misc.convert_ui", "tools.esm.make_master",
               "tools.creature.patch_body_slots", "tools.cellview.server", "tools.cellview.world",
               "tools.navmesh.rescan_mesh_caches", "core.gui.exe_update", "core.gui.eta")
#: Packages that third-party libraries import optionally and nothing TESRACT runs needs.
NOT_FOLLOWED = ("matplotlib", "tkinter", "pytest", "IPython", "pandas", "sympy", "PyQt5", "PySide6")
#: Compiled packages whose folder also ships, source included: see docs/commentary/core_gui.md#folder-build.
SOURCE_PACKAGES = ("pyffi",)
#: Packages imported by name at run time, included whole.
WHOLE_PACKAGES = ("tes5_import.navmesh",)
#: The tracked files and folders the app reads at run time; nothing else ships (folders end in "/").
SHIPPED = ("TESGameSelect/scripts/source/", "asset_convert/generated/", "core/gui/ui/", "docs/assets/banner.png",
           "docs/assets/favicon.ico", "external/", "native/dist/", "navmesh_pins/", "script_convert/data/",
           "script_convert/papyrus_reserved.txt", "script_convert/static_scripts/", "tes4_export/",
           "tes5_import/generated/", "tes_runtime/dist/", "tes_runtime/havok_world_size/HavokWorldSize.ini",
           "tes_runtime/morrowind/MorrowindRuntime.ini", "tools/cellview/static/")
#: Inside SHIPPED folders, what is still not needed: vendored library source compiled into native/dist.
NOT_SHIPPED = ("external/mimalloc/src/",)
#: Source and project files, never data.
SOURCE_SUFFIXES = (".py", ".pyc", ".cpp", ".c", ".h", ".hpp", ".md", ".bat", ".cmd", ".ps1")
#: The exe's icon.
ICON = REPO_ROOT / "docs" / "assets" / "favicon.ico"
#: Builds the launcher (TESRACT.exe at the top of the folder) into the folder its argument names.
LAUNCHER_BUILD = REPO_ROOT / "native" / "src" / "launcher" / "build.bat"
#: A release's DATA_ROOT_FILE: the folder above app/, the TESRACT folder the player unzipped.
RELEASE_DATA_ROOT = ".."


def data_files() -> list:
    """Every file in the tree (tracked, or new and not ignored) under SHIPPED, less sources and NOT_SHIPPED.

    See: docs/commentary/core_gui.md#folder-build
    """
    tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=REPO_ROOT,
                             capture_output=True, encoding="utf-8", check=True).stdout.splitlines()
    return sorted(p for p in tracked if p.startswith(SHIPPED) and not p.startswith(NOT_SHIPPED)
                  and not p.endswith(SOURCE_SUFFIXES))


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
    tags = folder / "tes5_import" / "navmesh" / navmesh_pool.BAKED_TAGS
    tags.parent.mkdir(parents=True, exist_ok=True)
    tags.write_text(json.dumps({CORRIDOR: navmesh_pool.source_tag(False), "lattice": navmesh_pool.source_tag(True)}),
                    encoding="utf-8")
    return len(files)


def numeric(version: str) -> str:
    """"0.689+g3bafe55" -> "0.689.0.0": Windows wants four numbers."""
    parts = [p for p in version.split("+")[0].split("-")[0].split(".") if p.isdigit()]
    return ".".join((parts + ["0"] * 4)[:4])


def nuitka_cmd(staged: Path, out: Path, version: str, jobs: int) -> list:
    """The Nuitka command line: a standalone folder, nothing unpacked at run time."""
    cmd = [sys.executable, "-m", "nuitka", "--standalone", "--assume-yes-for-downloads",
           f"--output-dir={out}", "--output-filename=TESRACT.exe", f"--jobs={jobs}",
           "--windows-console-mode=attach", f"--windows-icon-from-ico={ICON}",
           "--company-name=TESRACT", "--product-name=TESRACT", f"--product-version={numeric(version)}",
           f"--file-version={numeric(version)}", "--file-description=T.E.SR.A.C.T",
           f"--include-raw-dir={staged}=.", "--include-package-data=slint"]
    cmd += [f"--include-module={m}" for m in RUN_MODULES]
    cmd += [f"--include-package={p}" for p in WHOLE_PACKAGES]
    cmd += [f"--nofollow-import-to={p}" for p in NOT_FOLLOWED]
    return cmd + [str(REPO_ROOT / "gui.py")]


def place(dist: Path, folder: Path, data_root: str) -> list:
    """Lay out `folder` (replacing it): Nuitka's `dist` as `app/` with the splash file and DATA_ROOT_FILE
    naming `data_root`, the launcher on top, and the manifest of every program file; returns those files.

    See: docs/commentary/core_gui.md#folder-build
    """
    if folder.exists():
        shutil.rmtree(folder)
    app = folder / exe_update.APP_DIR
    folder.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dist), str(app))
    splash.write_splash_file(app / splash.SPLASH_FILE)
    (app / DATA_ROOT_FILE).write_text(data_root + "\n", encoding="utf-8")
    subprocess.run([str(LAUNCHER_BUILD), str(folder)], check=True, shell=True)
    files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
    (folder / exe_update.MANIFEST).write_text("\n".join(files + [exe_update.MANIFEST]) + "\n", encoding="utf-8")
    return files


def release_zip(folder: Path, target: Path) -> Path:
    """Zip `folder` as `TESRACT/...` into `target`, its DATA_ROOT_FILE naming the release's own top folder."""
    marker = f"{exe_update.APP_DIR}/{DATA_ROOT_FILE}"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            rel = path.relative_to(folder).as_posix()
            if rel == marker:
                archive.writestr("TESRACT/" + rel, RELEASE_DATA_ROOT + "\n")
            elif path.is_file():
                archive.write(path, "TESRACT/" + rel)
    return target


def main() -> int:
    """Stage the data, compile, and report where the TESRACT folder (and zip) went."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO_ROOT / "build" / "exe"), help="build folder")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="C compiler jobs")
    ap.add_argument("--zip", action="store_true", help=f"also write the release zip ({exe_update.ASSET_NAME})")
    ap.add_argument("--data-root", default=str(REPO_ROOT),
                    help="where this build reads and writes export/, output/ and logs/ (default: this checkout); "
                         f"'{RELEASE_DATA_ROOT}' is the TESRACT folder itself, as in a release")
    args = ap.parse_args()
    out = Path(args.out)
    version = version_info.current_version()
    count = stage(out / "data", version)
    print(f"Staged {count} data files for {version}", flush=True)
    code = subprocess.call(nuitka_cmd(out / "data", out, version, args.jobs), cwd=out)
    dist = out / "gui.dist"
    if code != 0 or not (dist / "TESRACT.exe").is_file():
        return code or 1
    folder = out / "TESRACT"
    files = place(dist, folder, args.data_root)
    print(f"Built {folder} ({len(files)} files); data in {args.data_root}")
    if args.zip:
        target = release_zip(folder, out / exe_update.ASSET_NAME)
        print(f"Release zip {target} ({target.stat().st_size / 1048576:,.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
