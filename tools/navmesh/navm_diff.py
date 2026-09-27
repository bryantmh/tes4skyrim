"""Snapshot every NAVM in a built plugin and diff two snapshots, to prove a navmesh change stayed focused.

    # before a navmesh change: record the current build
    python tools/navmesh/navm_diff.py snapshot output/Oblivion.esm/Oblivion.esm before.json

    # after rebuilding: which navmeshes changed, and are the named cells untouched?
    python tools/navmesh/navm_diff.py snapshot output/Oblivion.esm/Oblivion.esm after.json
    python tools/navmesh/navm_diff.py diff before.json after.json --must-match CharacterGen

A NAVM is compared by the SHA-1 of its whole record body, so any byte of NVNM,
flags or door links that moves counts as a change.  `--must-match PREFIX`
(repeatable) exits 1 when any navmesh in a cell whose EditorID starts with a
prefix changed, appeared or disappeared.
"""

import argparse
import hashlib
import json
import struct
import sys
import zlib

_COMPRESSED = 0x00040000


def _walk(data, off, end, cell, out, cell_edid):
    """Fill `out` {navm_fid: (cell_fid, sha1)} and `cell_edid` {cell_fid: edid}."""
    while off < end:
        sig = data[off:off + 4]
        size = struct.unpack_from('<I', data, off + 4)[0]
        if sig == b'GRUP':
            label, gtype = struct.unpack_from('<Ii', data, off + 8)
            inner = label if gtype in (6, 8, 9, 10) else cell
            _walk(data, off + 24, off + size, inner, out, cell_edid)
            off += size
            continue
        flags, fid = struct.unpack_from('<II', data, off + 8)
        body = data[off + 24:off + 24 + size]
        if sig == b'NAVM':
            out[fid] = (cell, hashlib.sha1(body).hexdigest())
        elif sig == b'CELL':
            raw = zlib.decompress(body[4:]) if flags & _COMPRESSED else body
            if raw[:4] == b'EDID':
                n = struct.unpack_from('<H', raw, 4)[0]
                cell_edid[fid] = raw[6:6 + n].rstrip(b'\0').decode('cp1252', 'replace')
        off += 24 + size


def snapshot(esm: str, out_path: str) -> None:
    """Write {navm FormID: [cell FormID, cell EditorID, sha1]} for every NAVM."""
    data = open(esm, 'rb').read()
    navms, edids = {}, {}
    _walk(data, 24 + struct.unpack_from('<I', data, 4)[0], len(data), None, navms, edids)
    table = {f'{fid:08X}': [f'{cell or 0:08X}', edids.get(cell, ''), sha]
             for fid, (cell, sha) in navms.items()}
    with open(out_path, 'w', encoding='utf8') as fh:
        json.dump(table, fh, indent=0, sort_keys=True)
    print(f'{len(table)} NAVM records -> {out_path}')


def diff(a_path: str, b_path: str, must_match) -> int:
    """Print changed/added/removed NAVMs; 1 when a --must-match cell moved."""
    a = json.load(open(a_path, encoding='utf8'))
    b = json.load(open(b_path, encoding='utf8'))
    changed = sorted(f for f in a.keys() & b.keys() if a[f][2] != b[f][2])
    added, removed = sorted(b.keys() - a.keys()), sorted(a.keys() - b.keys())
    print(f'{len(a)} -> {len(b)} NAVM: {len(changed)} changed, '
          f'{len(added)} added, {len(removed)} removed')
    rows = ([('changed', f, b[f]) for f in changed] + [('added', f, b[f]) for f in added]
            + [('removed', f, a[f]) for f in removed])
    for kind, fid, (cell, edid, _sha) in rows:
        print(f'  {kind:8s} NAVM {fid} cell {cell} {edid}')
    bad = [r for r in rows if any(r[2][1].startswith(p) for p in must_match)]
    for kind, fid, (cell, edid, _sha) in bad:
        print(f'MUST-MATCH VIOLATION: {kind} NAVM {fid} in {edid} ({cell})')
    return 1 if bad else 0


def main() -> int:
    """Run the `snapshot` or `diff` subcommand; returns the process exit code."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('snapshot')
    s.add_argument('esm')
    s.add_argument('out')
    d = sub.add_parser('diff')
    d.add_argument('before')
    d.add_argument('after')
    d.add_argument('--must-match', action='append', default=[],
                   help='cell EditorID prefix whose navmeshes must be identical')
    args = ap.parse_args()
    if args.cmd == 'snapshot':
        snapshot(args.esm, args.out)
        return 0
    return diff(args.before, args.after, args.must_match)


if __name__ == '__main__':
    sys.exit(main())
