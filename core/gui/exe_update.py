"""Updating TESRACT: download the release zip, unpack it beside the running copy, swap the program files.

The program is a folder (the launcher TESRACT.exe and app/, the compiled app) whose files are listed
in MANIFEST. An update unpacks the release zip into `.update/new`, then starts the NEW app there
(`.update/new/app/TESRACT.exe --tesract-run core.gui.exe_update finish <folder> <pid>`) and closes
this window. That process waits
for the old one to exit, deletes exactly the files the old MANIFEST lists, copies the new folder in,
and starts the updated TESRACT.exe; that start deletes `.update`. Export, output, logs, the config and
a located xWMAEncode.exe are never in the manifest, so they stay, and only the steps the new version
changed run again. From source there is nothing to replace: Update opens the release page instead.
See: docs/commentary/core_gui.md#updates
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

import version as version_info
from core.python_cmd import RUN_FLAG, frozen

#: The compiled app's folder inside the install folder (the launcher sits above it).
APP_DIR = "app"
#: Every program file, relative to the install folder, one per line; written by tools/release/build_exe.py.
MANIFEST = "tesract_files.txt"
#: Where an update is downloaded and unpacked, beside TESRACT.exe.
UPDATE_DIR = ".update"
#: Finish the update without opening TESRACT afterwards (the user closed it with an update waiting).
NO_START = "--no-start"
#: How long the finishing process waits for the old window to close, in milliseconds.
EXIT_WAIT_MS = 60000
#: Bytes read per chunk while downloading.
CHUNK = 1 << 20


def exe_path() -> Path:
    """The running app's TESRACT.exe (`sys.argv` is rewritten by `--tesract-run`, `sys.executable` is not)."""
    return Path(sys.executable).resolve()


def install_root() -> Path:
    """The install folder: the one holding the launcher and APP_DIR."""
    return exe_path().parent.parent


def asset_name(tag: str) -> str:
    """The zip release `tag` carries, the program folder under TESRACT/: "TESRACT-0.690.zip"."""
    return f"TESRACT-{tag}.zip"


def can_update() -> bool:
    """True inside TESRACT.exe, where the update replaces the program folder."""
    return frozen()


def release_asset(tag: str, timeout: int = 15) -> tuple:
    """(download url, size in bytes) of the release's zip; raises when it has none."""
    url = f"https://api.github.com/repos/{version_info.REPO}/releases/tags/{tag}"
    request = urllib.request.Request(url, headers={"User-Agent": "TESRACT-update"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        release = json.load(response)
    for asset in release.get("assets", ()):
        if asset.get("name") == asset_name(tag):
            return asset["browser_download_url"], int(asset.get("size", 0))
    raise RuntimeError(f"Release {tag} has no {asset_name(tag)}.")


def download(url: str, size: int, target: Path, log) -> None:
    """Stream `url` into `target`, logging every tenth of the way."""
    request = urllib.request.Request(url, headers={"User-Agent": "TESRACT-update"})
    done, shown = 0, -1
    with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
        while chunk := response.read(CHUNK):
            out.write(chunk)
            done += len(chunk)
            tenth = done * 10 // size if size else 0
            if tenth != shown:
                shown = tenth
                log(f"Downloaded {done / 1048576:,.0f} of {size / 1048576:,.0f} MB")
    if size and done != size:
        raise RuntimeError(f"Download stopped at {done:,} of {size:,} bytes.")


def unpack(archive: Path, target: Path) -> None:
    """Extract the release zip's TESRACT/ folder into `target` (never outside it)."""
    root = target.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            rel = Path(*Path(info.filename).parts[1:])
            dest = (root / rel).resolve()
            if info.is_dir() or not rel.parts or root not in dest.parents:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def install_update(tag: str, log) -> Path:
    """Download and unpack release `tag` beside this copy; returns its TESRACT.exe, which `restart` runs."""
    stage = install_root() / UPDATE_DIR
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    url, size = release_asset(tag)
    archive = stage / asset_name(tag)
    download(url, size, archive, log)
    log("Unpacking")
    unpack(archive, stage / "new")
    archive.unlink()
    log(f"TESRACT {tag} is ready; restart to finish installing it.")
    return stage / "new" / APP_DIR / exe_path().name


def restart(exe: Path, start: bool = True) -> None:
    """Start the unpacked update's app `exe` to finish the swap once this process exits, then (with
    `start`) open the updated TESRACT; the caller closes the window."""
    folder = install_root()
    cmd = [str(exe), RUN_FLAG, "core.gui.exe_update", "finish", str(folder), str(os.getpid())]
    subprocess.Popen(cmd + ([] if start else [NO_START]), cwd=str(folder), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def wait_for_exit(pid: int) -> None:
    """Wait (up to EXIT_WAIT_MS) for process `pid` to end."""
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if handle:
        kernel.WaitForSingleObject(ctypes.c_void_p(handle), EXIT_WAIT_MS)
        kernel.CloseHandle(ctypes.c_void_p(handle))


def program_files(folder: Path) -> list:
    """The files `folder`'s MANIFEST lists, each confirmed to lie inside `folder`."""
    root = folder.resolve()
    try:
        lines = (folder / MANIFEST).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    paths = [(root / line.strip()).resolve() for line in lines if line.strip()]
    return [p for p in paths if root in p.parents]


def finish(folder: Path, pid: int, new: Path, start: bool = True) -> None:
    """Replace `folder`'s program files with `new`'s once process `pid` has exited, then (with `start`)
    start the result."""
    wait_for_exit(pid)
    old = program_files(folder)
    for path in old:
        path.unlink(missing_ok=True)
    for path in sorted({p.parent for p in old}, key=lambda p: len(p.parts), reverse=True):
        if path != folder.resolve() and path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    shutil.copytree(new, folder, dirs_exist_ok=True)
    if start:
        subprocess.Popen([str(folder / exe_path().name)], cwd=str(folder), close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def clean_after_update() -> None:
    """Delete a finished update's unpacked copy (called at every start)."""
    stage = install_root() / UPDATE_DIR
    if frozen() and stage.is_dir() and stage not in exe_path().parents:
        shutil.rmtree(stage, ignore_errors=True)


def main() -> int:
    """`finish <folder> <pid> [--no-start]`: the second half of an update, run from the unpacked new copy."""
    args = sys.argv[1:]
    if len(args) not in (3, 4) or args[0] != "finish" or args[3:] not in ([], [NO_START]):
        print(f"usage: {RUN_FLAG} core.gui.exe_update finish <folder> <pid> [{NO_START}]")
        return 2
    finish(Path(args[1]), int(args[2]), install_root(), start=NO_START not in args)
    return 0
