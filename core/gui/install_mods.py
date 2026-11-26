"""The mods Install & play lists: every mod folder in output/.

A mod is a folder holding a plugin at its root (a game, an added mod, the start mod), or a folder a
Load order action writes, named in `ROLES` (the runtime DLLs, distant LOD, the UI, the body and journal
patches). Each is installed from its folder and zipped from it on demand for a manual install.
The files of a mod leave out what only the converter reads (`SIDECAR_SUFFIXES`, `SIDECAR_NAMES`).
See: docs/commentary/core_gui.md#mod-managers
"""

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from asset_convert.lod.bake_record import BAKE_RECORD_NAME
from asset_convert.sources.hybrid_assets import MOD_NAME as HYBRID_MOD

from output_layout import FINISHED_DIR_NAME, SUMMARY_NAME, finished_dir, write_mod_zip

#: Plugin file extensions, masters first when a mod's plugins are listed.
PLUGIN_EXTS = (".esm", ".esl", ".esp")
#: Files beside a plugin that only the converter reads.
SIDECAR_SUFFIXES = (".manifest.json", ".liptext.txt", ".voicemap.txt", ".voicecopies.txt")
#: Other converter-only files (the packer's two `output_files` records, the LOD bake record), and the dev patch.
SIDECAR_NAMES = frozenset({SUMMARY_NAME.lower(), "music_tracks.json", "tesgameselect_nonehrimstart.esp",
                           ".bsa_pack_manifest.json", ".bsa_pack_discarded.txt", BAKE_RECORD_NAME})
#: Mods that install first and last; every other sits between, in name order.
FIRST, LAST = ("TESRuntime",), ("AutoConvertLOD",)
#: Folders in output/ that are not mods (Finished Mods, and `sibling_lod.MERGED_DIR_NAME` lowercase).
NOT_MODS = frozenset({FINISHED_DIR_NAME.lower(), "zzz merged sibling lod"})
#: What each load-order mod is, shown beside its name; these folders are mods without a plugin at their root.
ROLES = {"TESRuntime": "SKSE runtime", "CreatureRuntime": "Creature runtime", "HavokWorldSize": "Havok world size",
         "TESGameSelect": "Start mod", "AutoConvertLOD": "Distant LOD", "Oblivion UI": "Oblivion UI",
         "Body Slots Patch": "Body patch", "Quest Journal Stage Text": "Journal patch",
         HYBRID_MOD: "Morroblivion assets"}
#: Seconds of slack when a zip's time is compared with its files' (the two clocks differ by under 2 s).
SLACK = 2


@dataclass(frozen=True)
class Mod:
    """One installable mod: its output folder and the zip a manual install makes of it."""

    name: str
    folder: Path
    zip: Path


def _has_plugin(folder) -> bool:
    """Whether `folder` holds a plugin file at its root."""
    return any(e.is_file() and e.name.lower().endswith(PLUGIN_EXTS) for e in os.scandir(folder))


def list_mods(out_root) -> list:
    """Every mod in `out_root` in install order: the runtime first, LOD last."""
    out_root = Path(out_root)
    zips = out_root / FINISHED_DIR_NAME
    found = {}
    for entry in os.scandir(out_root) if out_root.is_dir() else []:
        wanted = entry.is_dir() and entry.name.lower() not in NOT_MODS
        if wanted and (entry.name in ROLES or _has_plugin(entry.path)):
            found[entry.name] = Mod(entry.name, Path(entry.path), zips / f"{entry.name}.zip")
    middle = sorted((n for n in found if n not in FIRST + LAST), key=str.lower)
    return [found[n] for n in [*(n for n in FIRST if n in found), *middle, *(n for n in LAST if n in found)]]


def is_ours(rel: str) -> bool:
    """Whether a file of a mod folder belongs in the installed mod."""
    name = rel.rsplit("/", 1)[-1].lower()
    return name not in SIDECAR_NAMES and not name.endswith(SIDECAR_SUFFIXES)


def _tree_files(root) -> list:
    """[(relative path with /, path, size, mtime_ns)] for every shipped file under `root`.

    A folder or file that vanishes while it is read (a pack deleting loose copies) is skipped.
    """
    out, stack = [], [(str(root), "")]
    while stack:
        folder, prefix = stack.pop()
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except FileNotFoundError:
            continue
        stack += [(e.path, f"{prefix}{e.name}/") for e in entries if e.is_dir(follow_symlinks=False)]
        for e in entries:
            if e.is_file() and is_ours(f"{prefix}{e.name}"):
                try:
                    st = e.stat()
                except FileNotFoundError:
                    continue
                out.append((f"{prefix}{e.name}", Path(e.path), st.st_size, st.st_mtime_ns))
    return out


def _zip_names(path: Path) -> list:
    """Every file name in the zip at `path`."""
    with zipfile.ZipFile(path) as zf:
        return [i.filename for i in zf.infolist() if not i.is_dir()]


def files(mod: Mod) -> list:
    """The mod's files as (relative path, source path, size, mtime_ns), sorted by path."""
    return sorted(_tree_files(mod.folder))


def digest(names) -> str:
    """A fingerprint of a set of file names."""
    return hashlib.sha1("|".join(sorted(names)).encode("utf-8")).hexdigest()


def summarize(entries: list) -> dict:
    """What identifies one state of a mod's files: how many, how big, the newest time and a fingerprint of the names."""
    return {"count": len(entries), "size": sum(e[2] for e in entries), "mtime": max((e[3] for e in entries), default=0),
            "digest": digest(e[0] for e in entries)}


def _plugin_newer(folder: Path, saved: Path) -> bool:
    """Whether a plugin at `folder`'s root changed after `saved` was written (a tool edited it in place)."""
    since = saved.stat().st_mtime
    return any(e.is_file() and e.name.lower().endswith(PLUGIN_EXTS) and e.stat().st_mtime > since
               for e in os.scandir(folder))


def summary(mod: Mod) -> dict:
    """The mod's `summarize` result: its saved one when the folder has it, else read from the files and saved.

    See: docs/commentary/core_gui.md#install-summaries
    """
    saved = mod.folder / SUMMARY_NAME
    try:
        found = json.loads(saved.read_text(encoding="utf-8"))
        if set(found) == {"count", "size", "mtime", "digest"} and not _plugin_newer(mod.folder, saved):
            return found
    except (OSError, ValueError):
        pass
    return save_summary(mod, files(mod))


def save_summary(mod: Mod, entries: list) -> dict:
    """Summarize `entries` (the mod's files, freshly read) and save that as the mod's summary; returns it."""
    found = summarize(entries)
    try:
        (mod.folder / SUMMARY_NAME).write_text(json.dumps(found), encoding="utf-8")
    except OSError:
        pass
    return found


def plugins(rels) -> list:
    """The plugins at the root of a mod, masters first."""
    roots = [r for r in rels if "/" not in r and r.lower().endswith(PLUGIN_EXTS)]
    return sorted(roots, key=lambda r: (not r.lower().endswith(PLUGIN_EXTS[:2]), r.lower()))


def zip_state(mod: Mod, summary: dict) -> str:
    """"ready" when the zip holds exactly the mod's files and is newer than them, else "missing" or "stale"."""
    if not mod.zip.is_file():
        return "missing"
    if mod.zip.stat().st_mtime + SLACK < summary["mtime"] / 1e9:
        return "stale"
    return "ready" if digest(_zip_names(mod.zip)) == summary["digest"] else "stale"


def build_zip(mod: Mod, entries: list, out_root, log) -> Path:
    """Zip a folder mod's files into Finished Mods; returns the zip."""
    target = finished_dir(out_root) / mod.zip.name
    log(f"Zipping {mod.name}: {len(entries):,} files")

    def _progress(count, _name):
        """Thousands of files: report every 2000th."""
        if count % 2000 == 0:
            log(f"  {count:,} of {len(entries):,}")

    write_mod_zip(target, [(rel, src) for rel, src, _s, _m in entries], _progress)
    return target
