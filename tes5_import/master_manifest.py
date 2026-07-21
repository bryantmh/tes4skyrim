"""Companion manifest produced by a master's import, consumed by its plugins.

Converting one TES4 record often creates several TES5 records: an ARMO gets an
ARMA armature, an NPC_ gets an OTFT outfit and a VTYP voice type, an AMMO gets
a PROJ projectile. Those companions take FormIDs from `writer.alloc_formid()`,
a bare sequential counter, so their identity depends on the exact order the
whole file was converted in.

A plugin that OVERRIDES such a record must point at the MASTER's companions —
minting its own would duplicate content the master already has, and the
duplicates then compete with the originals. Re-deriving them is not an option:
the plugin's run converts a few thousand records, not the master's ~700k, so
the counter lands somewhere completely different.

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

    def load(self, path: str):
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if payload.get('version') != MANIFEST_VERSION:
            raise MissingManifestError(
                f"{path} was written by a different converter version "
                f"(found {payload.get('version')!r}, need {MANIFEST_VERSION}). "
                f"Re-convert the master.")
        # Later masters win, matching load order.
        self._records.update(payload.get('records', {}))


def load_master_manifests(masters: list, tes4_master_count: int,
                          output_root: str) -> 'MasterManifest | None':
    """Load the manifests for a plugin's TES4 masters.

    Only the trailing `tes4_master_count` entries of the TES5 master list are
    masters we convert; Skyrim.esm and friends are vanilla and have none.

    Raises MissingManifestError (with the command to fix it) rather than
    converting without the pairings — doing so silently duplicates every
    companion record the master already defines.
    """
    if not tes4_master_count:
        return None

    names = masters[len(masters) - tes4_master_count:]
    manifest = MasterManifest()
    missing = []
    for name in names:
        plugin_out = os.path.join(output_root, name, name)
        path = manifest_path(plugin_out)
        if not os.path.isfile(path):
            missing.append((name, path))
            continue
        manifest.load(path)

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
