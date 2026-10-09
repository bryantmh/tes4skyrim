"""Companion manifest produced by a master's import, consumed by its plugins.

Converting one TES4 record often creates several TES5 records: an ARMO gets an
ARMA armature, an NPC_ gets an OTFT outfit and a VTYP voice type, an AMMO gets
a PROJ projectile. Those companions take FormIDs from `writer.derive_formid()`.

A plugin that OVERRIDES such a record must point at the MASTER's companions —
minting its own would duplicate content the master already has, and the
duplicates then compete with the originals. Re-deriving them in the plugin's
own run does NOT reproduce the master's ids: derive_formid hashes into a
window chosen from the CURRENT plugin's authored ids and tags it with that
plugin's load-order index, and neither matches the master's.

So the master's import records the pairing at the moment it is created and
writes it next to the converted plugin. The plugin's import reads it. Nothing
is inferred from FormID arithmetic, proximity, or record type.

Format (`<Master>.manifest.json`):

    {"version": 1,
     "source": "Nehrim.esm",
     "records": {"0001A2B3": {"fid": 16852147,
                              "companions": [16852148, 16852149]}}}

Keys are the RAW TES4 FormIDs from the export, so a plugin can look up its own
override's source id directly.
"""

import json
import os

from core.plugin_masters import master_export_dir, master_index_map
from output_layout import converted_master_path

MANIFEST_VERSION = 1


def manifest_path(plugin_output_path: str) -> str:
    """Where the manifest for a converted plugin lives."""
    return plugin_output_path + '.manifest.json'


def write_manifest(plugin_output_path: str, source_name: str,
                   manifest: dict) -> str:
    """Persist the writer's source->companions map beside the converted plugin."""
    path = manifest_path(plugin_output_path)
    payload = {
        'version': MANIFEST_VERSION,
        'source': source_name,
        'records': {k: v for k, v in manifest.items() if v.get('fid')},
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, separators=(',', ':'))
    return path


class MissingManifestError(RuntimeError):
    """A master's companion manifest is required but absent or stale."""


def _index_map(export_root: str, name: str, slot: int, names: list,
               new_master_count: int) -> tuple:
    """(source-space map, output-space map) for one master's manifest.

    Both restate the master's own index bytes as the ones THIS plugin uses:

      * SOURCE space (the manifest's keys) is the raw TES4 numbering. The
        master's own records sit at its TES4 master count; lower bytes name ITS
        masters and are matched BY NAME against this plugin's list rather than
        assuming the two load orders line up.
      * OUTPUT space (`fid` / `companions`) is the master's CONVERTED numbering,
        which has the new masters (Skyrim.esm) prepended — so every index byte
        there is the source one shifted up by `new_master_count`, and this
        plugin's converted ids are shifted by the same amount.

    Returns None when the master's export is unavailable, keeping the old
    verbatim merge for the single-master case (no collision is possible there).
    """
    if not export_root:
        return None
    src = master_index_map(master_export_dir(export_root, name), slot, names)
    out = {k + new_master_count: v + new_master_count for k, v in src.items()}
    return src, out


class MasterManifest:
    """Loaded manifests for one or more converted masters, keyed by source id."""

    def __init__(self):
        self._records = {}

    def __len__(self):
        return len(self._records)

    def __contains__(self, source_formid: str) -> bool:
        return (source_formid or '').upper() in self._records

    def output_formid(self, source_formid: str) -> int:
        """Converted FormID for a source record (0 if the master skipped it)."""
        entry = self._records.get((source_formid or '').upper())
        return entry.get('fid', 0) if entry else 0

    def companions(self, source_formid: str) -> list:
        """FormIDs of the records generated alongside a source record."""
        entry = self._records.get((source_formid or '').upper())
        return entry.get('companions', []) if entry else []

    def load(self, path: str, index_map: dict = None):
        """Merge one master's manifest.

        `index_map` translates that master's OWN source index bytes into the
        index bytes THIS plugin uses to name the same records. It is required
        whenever more than one master is loaded: each manifest is keyed by its
        own raw TES4 ids, so merging them unmapped collapses every master's id
        space into one and the last-loaded master silently wins ids belonging
        to an earlier one — `output_formid` then answers with a completely
        different record's converted id. On TWMP Valenwood/Elsweyr that wrote
        2,553 records at ids Tamriel.esp owns as another type (xEdit: "Record
        [CELL:0201C4B3] in Tamriel.esp is being overridden by record
        [REFR:0201C4B3]"), which hangs the engine on the main menu.
        """
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if payload.get('version') != MANIFEST_VERSION:
            raise MissingManifestError(
                f"{path} was written by a different converter version "
                f"(found {payload.get('version')!r}, need {MANIFEST_VERSION}). "
                f"Re-convert the master.")
        records = payload.get('records', {})
        if index_map is None:
            self._records.update(records)
            return
        # KEYS are in the master's TES4 SOURCE space; `fid`/`companions` are in
        # its converted OUTPUT space. Both are the master's own numbering and
        # both must be restated in this plugin's, or an override is emitted at
        # a FormID that belongs to some other file's record.
        src_map, out_map = index_map

        def _remap(fid, table):
            m = table.get((fid >> 24) & 0xFF)
            return None if m is None else ((m << 24) | (fid & 0x00FFFFFF))

        for key, entry in records.items():
            try:
                raw = int(key, 16)
            except (TypeError, ValueError):
                continue
            new_key = _remap(raw, src_map)
            if new_key is None:
                # Names a file this plugin does not load — unreachable here.
                continue
            fid = _remap(entry.get('fid', 0), out_map)
            if not fid:
                continue
            comps = [c for c in (_remap(c, out_map)
                                 for c in entry.get('companions', ())) if c]
            self._records['%08X' % new_key] = {'fid': fid,
                                               'companions': comps}


def load_master_manifests(masters: list, tes4_master_count: int,
                          output_root: str,
                          export_root: str = None) -> 'MasterManifest | None':
    """Load the manifests for a plugin's TES4 masters.

    Only the trailing `tes4_master_count` entries of the TES5 master list are
    masters we convert; Skyrim.esm and friends are vanilla and have none.

    Each manifest is re-keyed from its master's own TES4 id space into the one
    THIS plugin uses (see MasterManifest.load) — without that, masters overwrite
    each other's ids and overrides are emitted at another record's FormID.

    Raises MissingManifestError (with the command to fix it) rather than
    converting without the pairings — doing so silently duplicates every
    companion record the master already defines.
    """
    if not tes4_master_count:
        return None

    names = masters[len(masters) - tes4_master_count:]
    new_master_count = len(masters) - tes4_master_count
    manifest = MasterManifest()
    missing = []
    for slot, name in enumerate(names):
        plugin_out = str(converted_master_path(output_root, name))
        path = manifest_path(plugin_out)
        if not os.path.isfile(path):
            missing.append((name, path))
            continue
        manifest.load(path, _index_map(export_root, name, slot, names,
                                       new_master_count))

    if missing:
        lines = [
            "Master companion manifest not found - cannot convert overrides.",
            "",
            "This plugin overrides records whose conversion generates",
            "companion records (ARMA/OTFT/VTYP/PROJ/...). Without the master's",
            "manifest their FormIDs cannot be reused, so the plugin would",
            "duplicate content the master already defines.",
            "",
            "Missing:",
        ]
        lines += [f"  {name}  (expected at {path})" for name, path in missing]
        lines += ["", "Re-convert the master to produce it:"]
        lines += [f"  python convert.py -f {name}" for name, _ in missing]
        raise MissingManifestError("\n".join(lines))

    return manifest
