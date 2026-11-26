"""Taking one mod's deployed files out of the game's Data folder the way Vortex's own purge does, with Vortex closed.

Vortex records every file it deployed in `vortex.deployment.json` in the deployment target (the game's Data
folder), with a msgpack copy, `vortex.deployment.msgpack`, in the staging folder. Each entry names the mod it
came from (`source`, the mod's staging folder) and its path (`relPath`, under `target`). A file is removed
only when it is still the one Vortex placed from our staging copy; a game file Vortex moved aside
(`<file>.vortex_backup`) is put back; the entries go from both manifests.
See: docs/commentary/core_gui.md#vortex-deployed-uninstall
"""

import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

#: The deployment manifest of Vortex's default mod type, in the Data folder; its JSON copy in staging.
MANIFEST = "vortex.deployment.json"
#: The binary copy of the manifest Vortex keeps in the staging folder.
BINARY_BACKUP = "vortex.deployment.msgpack"
#: Suffix of a game file Vortex renamed to deploy a mod's file in its place.
BACKUP_TAG = ".vortex_backup"
#: Beside a staging file the move method moved into Data: JSON naming where it went.
LNK_EXT = ".vortex_lnk"
#: Deployment methods whose deployed files can be told apart (Vortex's activator ids).
METHODS = frozenset({"hardlink_activator", "symlink_activator", "symlink_activator_elevated", "move_activator"})
#: Milliseconds a deployed file's time may differ from its entry's (Vortex's `isDeployedFile`).
TIME_SLACK_MS = 1000

# ---------------------------------------------------------------------------
#  Reading the manifest and telling our files apart
# ---------------------------------------------------------------------------


@dataclass
class Undeployed:
    """What taking one mod out of Data did."""

    removed: int = 0
    kept: list = field(default_factory=list)
    redeploy: list = field(default_factory=list)


def _norm(path) -> str:
    """A path for comparing: absolute, normalized, case-folded."""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def read_manifest(data_dir) -> dict:
    """The deployment manifest in `data_dir`, or {} when there is none or it cannot be read."""
    try:
        manifest = json.loads((Path(data_dir) / MANIFEST).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) and isinstance(manifest.get("files"), list) else {}


def deployed_from(manifest: dict, staging, game: str) -> bool:
    """Whether `manifest` was written for `game` deploying from `staging` (a field Vortex left out does not count)."""
    staged = manifest.get("stagingPath")
    return manifest.get("gameId", game) == game and (not staged or _norm(staged) == _norm(staging))


def _time_matches(path: Path, entry: dict) -> bool:
    """Whether `path`'s modified time is the entry's, as Vortex checks before it purges a file."""
    return abs(path.lstat().st_mtime_ns / 1e6 - float(entry.get("time", 0))) < TIME_SLACK_MS


def _moved_here(deployed: Path, source: Path) -> bool:
    """Whether the move method's link file beside `source` names `deployed` as where the file went."""
    try:
        target = json.loads(Path(f"{source}{LNK_EXT}").read_text(encoding="utf-8")).get("target", "")
    except (OSError, ValueError, AttributeError):
        return False
    return bool(target) and _norm(target) == _norm(deployed)


def is_ours(method: str, deployed: Path, source: Path, entry: dict) -> bool:
    """Whether `deployed` is still the file Vortex placed from `source` (the staging file) by `method`."""
    if method == "move_activator":
        return _moved_here(deployed, source) and _time_matches(deployed, entry)
    if method.startswith("symlink_activator"):
        return deployed.is_symlink() and _norm(os.path.realpath(deployed)) == _norm(os.path.realpath(source))
    return source.is_file() and not deployed.is_symlink() and os.path.samefile(deployed, source)


def _take_out(deployed: Path, source: Path) -> None:
    """Delete a deployed file, put back the game file Vortex moved aside for it, and drop a move link."""
    deployed.unlink()
    backup = deployed.with_name(deployed.name + BACKUP_TAG)
    if backup.is_file():
        backup.rename(deployed)
    Path(f"{source}{LNK_EXT}").unlink(missing_ok=True)

# ---------------------------------------------------------------------------
#  Another mod's copy of a file
# ---------------------------------------------------------------------------


def _ancestors(rel: str) -> list:
    """Every folder above a /-separated relative path."""
    parts = rel.split("/")[:-1]
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _matching_files(root: str, want: set, folders: set) -> set:
    """The relative paths of `want` (lowercase, /) found under `root`, entering only `folders`."""
    found, stack = set(), [(root, "")]
    while stack:
        folder, prefix = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            rel = prefix + e.name.lower()
            if e.is_dir(follow_symlinks=False) and rel in folders:
                stack.append((e.path, rel + "/"))
            elif rel in want and e.is_file():
                found.add(rel)
    return found


def shadowed(staging, name: str, rels: list) -> list:
    """The `rels` (lowercase, /) another mod folder in `staging` also has: Vortex deploys that copy once ours is gone."""
    want = {r.replace("\\", "/").lower() for r in rels}
    folders = {f for r in want for f in _ancestors(r)}
    found = set()
    for mod in os.scandir(staging) if want and Path(staging).is_dir() else []:
        if mod.is_dir() and mod.name.lower() != name.lower():
            found |= _matching_files(mod.path, want, folders)
    return sorted(found)

# ---------------------------------------------------------------------------
#  Writing the manifests back
# ---------------------------------------------------------------------------


def _head(size: int, small: int, small_max: int, wide: tuple) -> bytes:
    """A msgpack container or string header: the fixed form up to `small_max`, else the shortest length form."""
    if size <= small_max:
        return bytes([small | size])
    for code, fmt, top in wide:
        if size <= top:
            return bytes([code]) + struct.pack(fmt, size)
    raise ValueError("too large for msgpack")


def _pack_int(n: int) -> bytes:
    """A msgpack integer in its shortest form."""
    if -32 <= n < 128:
        return struct.pack(">b", n) if n < 0 else bytes([n])
    forms = ((0xCC, ">B"), (0xCD, ">H"), (0xCE, ">I"), (0xCF, ">Q")) if n >= 0 else (
        (0xD0, ">b"), (0xD1, ">h"), (0xD2, ">i"), (0xD3, ">q"))
    for code, fmt in forms:
        try:
            return bytes([code]) + struct.pack(fmt, n)
        except struct.error:
            continue
    raise ValueError(f"{n} is too large for msgpack")


def msgpack(value) -> bytes:
    """`value` (JSON data) as msgpack, the format of Vortex's binary manifest copy."""
    if value is None or isinstance(value, bool):
        return bytes([{None: 0xC0, False: 0xC2, True: 0xC3}[value]])
    if isinstance(value, int):
        return _pack_int(value)
    if isinstance(value, float):
        return b"\xcb" + struct.pack(">d", value)
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _head(len(raw), 0xA0, 31, ((0xD9, ">B", 0xFF), (0xDA, ">H", 0xFFFF), (0xDB, ">I", 2**32 - 1))) + raw
    if isinstance(value, list):
        head = _head(len(value), 0x90, 15, ((0xDC, ">H", 0xFFFF), (0xDD, ">I", 2**32 - 1)))
        return head + b"".join(msgpack(v) for v in value)
    head = _head(len(value), 0x80, 15, ((0xDE, ">H", 0xFFFF), (0xDF, ">I", 2**32 - 1)))
    return head + b"".join(msgpack(k) + msgpack(v) for k, v in value.items())


def _write_atomic(path: Path, data: bytes) -> None:
    """Write `path` through a temporary file, so a reader never sees half of it."""
    tmp = path.with_name(path.name + ".tesract")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_manifest(data_dir, staging, manifest: dict) -> None:
    """Write `manifest` back as Vortex's `saveActivation` does: removed with its copy when it lists nothing."""
    main, binary = Path(data_dir) / MANIFEST, Path(staging) / BINARY_BACKUP
    (Path(staging) / MANIFEST).unlink(missing_ok=True)
    if not manifest["files"]:
        main.unlink(missing_ok=True)
        binary.unlink(missing_ok=True)
        return
    _write_atomic(binary, msgpack(manifest))
    _write_atomic(main, json.dumps(manifest, indent=2).encode("utf-8"))

# ---------------------------------------------------------------------------
#  Taking a mod out
# ---------------------------------------------------------------------------


def undeploy(data_dir, staging, name: str, game: str) -> Undeployed:
    """Take mod `name`'s deployed files out of `data_dir` and both manifests; nothing when it is not deployed.

    Raises before anything changes when Vortex deployed it by a method whose files cannot be told apart.
    """
    manifest, done = read_manifest(data_dir), Undeployed()
    if not manifest or not deployed_from(manifest, staging, game):
        return done
    mine = [f for f in manifest["files"] if isinstance(f, dict) and f.get("source") == name]
    method = manifest.get("deploymentMethod") or ""
    if mine and method not in METHODS:
        raise RuntimeError(f"Vortex deployed {name} with {method or 'an unknown method'}: purge it in Vortex first.")
    for entry in mine:
        rel = entry["relPath"]
        deployed, source = Path(data_dir, entry.get("target") or "", rel), Path(staging, name, rel)
        if not os.path.lexists(deployed):
            continue
        if not is_ours(method, deployed, source, entry):
            done.kept.append(rel)
            continue
        _take_out(deployed, source)
        done.removed += 1
    gone = [f["relPath"] for f in mine if f["relPath"] not in done.kept]
    merged = [f["relPath"] for f in manifest["files"] if isinstance(f, dict) and name in (f.get("merged") or ())]
    done.redeploy = sorted(set(shadowed(staging, name, gone)) | {m.replace("\\", "/").lower() for m in merged})
    ours = {id(f) for f in mine}
    manifest["files"] = [f for f in manifest["files"] if id(f) not in ours]
    if mine:
        save_manifest(data_dir, staging, manifest)
    return done
