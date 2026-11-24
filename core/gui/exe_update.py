"""Updating TESRACT.exe: download the release's exe beside the running one, swap them, restart.

Windows will not overwrite a running exe but will rename it, so the swap renames TESRACT.exe to
TESRACT.exe.old, moves the download into its place, and the next start deletes the .old file and the
unpacked copies (`.tesract/<version>`) of every other version. Export, output, logs and the
conversion records sit beside the exe and are untouched, so only the steps the new version changed
run again. From source there is no exe to replace: Update opens the release page instead.
See: docs/commentary/core_gui.md#updates
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import version as version_info
from core.python_cmd import frozen

#: The release asset every published version carries.
ASSET_NAME = "TESRACT.exe"
#: The folder each version unpacks into, beside the exe (build_exe's --onefile-tempdir-spec).
UNPACK_DIR = ".tesract"
#: Bytes read per chunk while downloading.
CHUNK = 1 << 20


def exe_path() -> Path:
    """The running TESRACT.exe (the file the player started, not its unpacked copy)."""
    return Path(sys.argv[0]).resolve()


def can_update() -> bool:
    """True inside TESRACT.exe, where the update replaces the exe itself."""
    return frozen()


def release_asset(tag: str, timeout: int = 15) -> tuple:
    """(download url, size in bytes) of the release's TESRACT.exe; raises when it has none."""
    url = f"https://api.github.com/repos/{version_info.REPO}/releases/tags/{tag}"
    request = urllib.request.Request(url, headers={"User-Agent": "TESRACT-update"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        release = json.load(response)
    for asset in release.get("assets", ()):
        if asset.get("name") == ASSET_NAME:
            return asset["browser_download_url"], int(asset.get("size", 0))
    raise RuntimeError(f"Release {tag} has no {ASSET_NAME}.")


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


def install_update(tag: str, log) -> Path:
    """Download release `tag`'s exe and swap it in; returns the exe to start."""
    exe = exe_path()
    url, size = release_asset(tag)
    new, old = exe.with_name(exe.name + ".new"), exe.with_name(exe.name + ".old")
    download(url, size, new, log)
    old.unlink(missing_ok=True)
    os.replace(exe, old)
    os.replace(new, exe)
    log(f"Installed TESRACT {tag}; restart to use it.")
    return exe


def restart(exe: Path) -> None:
    """Start `exe` as a new process (the caller then closes the window)."""
    subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def clean_after_update() -> None:
    """Delete the replaced exe and other versions' unpacked copies (called at every start)."""
    if not frozen():
        return
    exe = exe_path()
    exe.with_name(exe.name + ".old").unlink(missing_ok=True)
    unpacked = exe.parent / UNPACK_DIR
    running = Path(sys.executable).resolve()
    for folder in unpacked.iterdir() if unpacked.is_dir() else ():
        if folder.is_dir() and folder not in running.parents:
            shutil.rmtree(folder, ignore_errors=True)
