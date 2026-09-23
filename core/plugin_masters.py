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


def is_master_export(record_dir: str) -> bool:
    """Whether the export's `_HEADER.txt` Flags carry the source's ESM bit."""
    return any(key == 'Flags' and int(value or 0) & 1
               for key, value in _export_header(record_dir))


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


def _visit(name: str, deps: dict, visited: set, order: list) -> None:
    """Depth-first walk placing `name` after every master it declares."""
    if name in visited:
        return
    visited.add(name)
    for master in deps.get(name, []):
        if master in deps:
            _visit(master, deps, visited, order)
    order.append(name)
