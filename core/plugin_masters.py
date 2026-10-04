"""
Plugin master lists, and the load order they imply.

Reading a plugin's masters is a property of the file format, not of the
pipeline: TES4 and FO3/FNV differ by four header bytes, and Morrowind's header
is a different shape again. Both callers -- export ordering and the import
stage's master list -- want the same answer, so it lives in one place.

See: docs/commentary/tes4_export_morrowind.md#the-tes3-container
"""

import os
import struct

import output_layout
from core.tes4_encoding import normalize as _normalize_codec
from tes4_export.tes3_reader import read_masters as _tes3_masters

#: Where the TES4 header record ends; FO3/FNV push HEDR four bytes later.
_TES4_HEADER_SIZES = (20, 24)


def get_masters_from_binary(filepath: str) -> list:
    """The master filenames a plugin declares, in load order.

    Empty for a file that is neither TES3 nor TES4-family, which is what keeps
    an unreadable plugin from breaking the whole ordering pass.
    """
    with open(filepath, 'rb') as fh:
        sig = fh.read(4)
        if sig == b'TES3':
            return _tes3_masters(filepath)
        if sig != b'TES4':
            return []
        data_size = struct.unpack('<I', fh.read(4))[0]
        fh.seek(_TES4_HEADER_SIZES[0] if fh.read(16)[12:16] == b'HEDR'
                else _TES4_HEADER_SIZES[1])
        return _tes4_masters(fh.read(data_size))


def masters_from_export_header(record_dir: str) -> list:
    """The `Master[N]=` names an export's `_HEADER.txt` declares, in order."""
    return [value for key, value in _export_header(record_dir)
            if key.startswith('Master[') and value]


# ---------------------------------------------------------------------------
#  Where a master's export lives -- the ONE resolver
# ---------------------------------------------------------------------------

def export_root(record_dir) -> str:
    """The `export/` root, given any plugin's record folder.

    `export/<plugin>/` for a game-Data plugin, `export/<Mod>/<plugin>/` for an
    imported mod, so the parent is not reliably the root; `sources.json` marks it.
    See: docs/reference/pipeline.md#master-resolution
    """
    d = os.path.dirname(os.path.normpath(str(record_dir)))
    for cand in (d, os.path.dirname(d)):
        if cand and os.path.isfile(
                os.path.join(cand, output_layout.REGISTRY_FILENAME)):
            return cand
    return d


def master_export_dir(root, name: str) -> str:
    """Where master `name`'s records live under the export `root`."""
    got = str(output_layout.record_dir(root, name))
    return got if os.path.isdir(got) else os.path.join(str(root), name)


def master_dir(record_dir, name: str) -> str:
    """Where master `name`'s records live, given a dependent's record folder."""
    return master_export_dir(export_root(record_dir), name)


def master_dirs(record_dir) -> list:
    """The record folder of each direct master that was exported, in header order."""
    dirs = [master_dir(record_dir, n)
            for n in masters_from_export_header(str(record_dir))]
    return [d for d in dirs if os.path.isdir(d)]


def master_index_map(master_folder, slot: int, header: list) -> dict:
    """{index byte in a master's own FormIDs -> the dependent's index byte}.

    `header` is the dependent's master list and `slot` this master's place in
    it. The master's own records sit at its master count; each of ITS masters
    is matched by name.
    See: docs/commentary/tes5_import_override.md#re-keying-the-masters-ids
    """
    own = masters_from_export_header(str(master_folder))
    slot_of = {n.lower(): i for i, n in enumerate(header)}
    remap = {k: slot_of[s.lower()] for k, s in enumerate(own)
             if s.lower() in slot_of}
    remap[len(own)] = slot
    return remap


def master_chain(record_dir) -> list:
    """Every master `record_dir` inherits from, transitively, each after its own masters.

    Cycle-safe. A master that was never exported is still named (its own masters
    are unknown), so a caller can report it rather than silently lose it.
    """
    root = export_root(record_dir)
    seen = {os.path.basename(os.path.normpath(str(record_dir))).lower()}
    ordered = []

    def visit(folder):
        """Append `folder`'s unseen masters to `ordered`, deepest first."""
        for name in masters_from_export_header(folder):
            if name.lower() not in seen:
                seen.add(name.lower())
                visit(master_export_dir(root, name))
                ordered.append(name)

    visit(str(record_dir))
    return ordered


def is_master_export(record_dir: str) -> bool:
    """Whether the export's `_HEADER.txt` Flags carry the source's ESM bit."""
    return any(key == 'Flags' and int(value or 0) & 1
               for key, value in _export_header(record_dir))


def export_source(record_dir: str) -> str:
    """The `Source=` game an export's `_HEADER.txt` names; '' for a TES4 dump."""
    return next((value for key, value in _export_header(record_dir)
                 if key == 'Source'), '')


def export_encoding(record_dir: str) -> str:
    """The `ENCODING=` codec an export's `_HEADER.txt` declares.

    Old exports predate the line and read back as cp1252, which is what they
    were decoded with. Never raises: unknown names fall back to cp1252.
    """
    return _normalize_codec(next((value for key, value in _export_header(record_dir)
                                  if key == 'ENCODING'), ''))


def _export_header(record_dir: str) -> list:
    """(key, value) for every line of an export's `_HEADER.txt`; [] if none."""
    header = os.path.join(record_dir, '_HEADER.txt')
    if not os.path.isfile(header):
        return []
    with open(header, encoding='utf-8') as fh:
        return [(key, value.strip()) for key, _eq, value
                in (line.partition('=') for line in fh)]


def _tes4_masters(data: bytes) -> list:
    """Every MAST string in a TES4-family header record's data."""
    masters = []
    pos = 0
    while pos + 6 <= len(data):
        sub_sig = data[pos:pos + 4].decode('ascii', errors='replace')
        sub_size = struct.unpack_from('<H', data, pos + 4)[0]
        pos += 6
        if pos + sub_size > len(data):
            break
        if sub_sig == 'MAST':
            masters.append(
                data[pos:pos + sub_size].decode('latin-1').rstrip('\0'))
        pos += sub_size
    return masters


def topological_order(files: list, resolve) -> list:
    """Plugin names sorted so every master precedes what depends on it.

    `resolve` maps a plugin name to its binary path; passing it in keeps this
    module free of the pipeline's path resolution.
    """
    names = [f if isinstance(f, str) else f['name'] for f in files]
    deps = {}
    for name in names:
        source = resolve(name)
        deps[name] = (get_masters_from_binary(source)
                      if source and os.path.isfile(source) else [])

    order, visited = [], set()
    for name in names:
        _visit(name, deps, visited, order)
    return order


def binary_master_chain(names: list, resolve) -> list:
    """Every master the plugins `names` inherit, transitively, read from their binaries.

    `resolve` maps a plugin name to its binary path, as for `topological_order`.
    """
    seen, todo = set(), list(names)
    while todo:
        source = resolve(todo.pop())
        for master in (get_masters_from_binary(source)
                       if source and os.path.isfile(source) else []):
            if master.lower() not in seen:
                seen.add(master.lower())
                todo.append(master)
    return sorted(seen)


def _visit(name: str, deps: dict, visited: set, order: list) -> None:
    """Depth-first walk placing `name` after every master it declares."""
    if name in visited:
        return
    visited.add(name)
    for master in deps.get(name, []):
        if master in deps:
            _visit(master, deps, visited, order)
    order.append(name)
