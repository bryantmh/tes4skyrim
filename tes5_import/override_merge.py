"""Random-access index over a converted master plugin.

A plugin that has masters emits each override as the MASTER's converted record
with the author's changes applied (see override_builder). This module provides
the master side of that: the converted record bytes for a given FormID.

There is deliberately no merging heuristic here. An earlier version tried to
reconcile two independent conversion runs by comparing their output bytes and
guessing which differences were authored and which were re-derivation
artifacts. That is not decidable from the output, and guessing wrong produced
an unloadable plugin — 1821 NPC_ races rewritten to vanilla Skyrim ones the
authors never touched. Authorship now comes from diffing the two TES4 exports
(export_diff), which answers the question directly.
"""

import os
import struct

_HEADER_SIZE = 24


class MasterIndex:
    """FormID -> converted record body, read from a converted master plugin."""

    def __init__(self, path: str):
        self.path = path
        self._data = b''
        self._offsets = {}      # formid -> (signature, offset, total_size)
        self._paths = {}        # formid -> ((grup_type, label), ...)
        self._load()

    def _load(self):
        with open(self.path, 'rb') as f:
            self._data = f.read()
        d = self._data
        if len(d) < 8 or d[:4] != b'TES4':
            raise ValueError(f"Not a plugin file: {self.path}")
        start = _HEADER_SIZE + struct.unpack_from('<I', d, 4)[0]
        self._scan(start, len(d))

    def _scan(self, off: int, end: int, path: tuple = ()):
        d = self._data
        while off + _HEADER_SIZE <= end:
            sig = d[off:off + 4]
            size = struct.unpack_from('<I', d, off + 4)[0]
            if sig == b'GRUP':
                # GRUP header: 'GRUP'(4) size(4) label(4) type(4) ...
                label = d[off + 8:off + 12]
                gtype = struct.unpack_from('<i', d, off + 12)[0]
                self._scan(off + _HEADER_SIZE, off + size,
                           path + ((gtype, label),))
                off += size
            else:
                fid = struct.unpack_from('<I', d, off + 12)[0]
                self._offsets[fid] = (sig, off, _HEADER_SIZE + size)
                self._paths[fid] = path
                off += _HEADER_SIZE + size

    def group_path(self, formid: int) -> tuple:
        """The GRUP nesting a record sits in, as ((type, label), ...).

        A CELL is only reachable by the engine from inside its block/sub-block
        hierarchy (interior: CELL -> type 2 -> type 3; exterior: WRLD -> type 1
        -> type 4 -> type 5). A CELL written flat under the top-level group is
        never indexed, which is what left every renamed cell black and empty.
        An override must therefore reproduce the master's exact nesting — read
        here rather than recomputed, so there is no formula to get wrong.
        """
        return self._paths.get(formid, ())

    def __contains__(self, formid: int) -> bool:
        return formid in self._offsets

    def __len__(self) -> int:
        return len(self._offsets)

    def formids(self) -> set:
        return set(self._offsets)

    def signature(self, formid: int) -> bytes:
        entry = self._offsets.get(formid)
        return entry[0] if entry else b''

    def record(self, formid: int) -> bytes:
        """Full record bytes (header + body) for a FormID, or b'' if absent."""
        entry = self._offsets.get(formid)
        if not entry:
            return b''
        _, off, size = entry
        return self._data[off:off + size]


class ChainedMasterIndex:
    """Several converted masters queried in load order (last one wins).

    Each MasterIndex owns its own file buffer, so offsets are only meaningful
    against their own index — lookups delegate rather than merging offset
    tables into one dict.
    """

    def __init__(self, indices: list):
        self._indices = list(indices)

    def _find(self, formid: int):
        for idx in reversed(self._indices):
            if formid in idx:
                return idx
        return None

    def __contains__(self, formid: int) -> bool:
        return self._find(formid) is not None

    def __len__(self) -> int:
        return len({f for idx in self._indices for f in idx._offsets})

    def formids(self) -> set:
        return {f for idx in self._indices for f in idx._offsets}

    def signature(self, formid: int) -> bytes:
        idx = self._find(formid)
        return idx.signature(formid) if idx else b''

    def record(self, formid: int) -> bytes:
        idx = self._find(formid)
        return idx.record(formid) if idx else b''

    def group_path(self, formid: int) -> tuple:
        idx = self._find(formid)
        return idx.group_path(formid) if idx else ()


class MissingMasterOutputError(RuntimeError):
    """A plugin's converted master output is required but absent."""


def resolve_master_outputs(masters: list, tes4_master_count: int,
                           output_root: str) -> list:
    """Converted-master plugin paths for a plugin's TES4 masters.

    `masters` is the TES5 master list (new masters prepended); only the trailing
    `tes4_master_count` entries are TES4 masters we convert ourselves. Skyrim.esm
    and friends are vanilla and are never merged against.

    Returns [(master_name, path_or_None), ...] in load order.
    """
    if not tes4_master_count:
        return []
    out = []
    for name in masters[len(masters) - tes4_master_count:]:
        # convert.py writes output/<plugin>/<plugin>
        path = os.path.join(output_root, name, name)
        out.append((name, path if os.path.isfile(path) else None))
    return out


def load_master_index(masters: list, tes4_master_count: int,
                      output_root: str):
    """Index the converted TES4 masters, or raise if any is missing.

    An override IS the master's converted record, so without it there is
    nothing to override and the conversion cannot proceed.
    """
    resolved = resolve_master_outputs(masters, tes4_master_count, output_root)
    if not resolved:
        return None

    missing = [(n, os.path.join(output_root, n, n))
               for n, p in resolved if p is None]
    if missing:
        lines = [
            "Converted master output not found - cannot convert overrides.",
            "",
            "This plugin's records mostly OVERRIDE its master, and an override",
            "is emitted as the master's converted record with the author's",
            "changes applied. Without it there is nothing to override.",
            "",
            "Missing:",
        ]
        lines += [f"  {name}  (expected at {path})" for name, path in missing]
        lines += ["", "Convert the master first:"]
        lines += [f"  python convert.py -f {name}" for name, _ in missing]
        raise MissingMasterOutputError("\n".join(lines))

    indices = [MasterIndex(path) for _, path in resolved]
    if len(indices) == 1:
        return indices[0]
    return ChainedMasterIndex(indices)
