"""Dump vanilla Skyrim PACK template roots (PKDT.Type=19) and their public
data-input signatures.

A TES5 package instance (PKDT.Type=18) carries no procedure tree: it points
PKCU.PackageTemplate at a template root and supplies data inputs positionally.
The instance's UNAM index list and XNAM value are copied verbatim from the root,
and its ANAM value list must match the root's input order exactly.

This tool prints, for a template root:
  * its procedure tree (PNAM procedure names)
  * its declared inputs: UNAM index -> BNAM name, PNAM flags (1 = Public)
  * the XNAM marker value
  * the ANAM value types an instance must supply, in order

Feed the output into tes5_import/pack_templates.py.

Usage:
    python -m tools.pack_template_dump --list
    python -m tools.pack_template_dump Travel Sleep Eat
    python -m tools.pack_template_dump 00019714
    python -m tools.pack_template_dump --instances-of Follow --limit 2
    python -m tools.pack_template_dump --python Travel Sandbox Eat Sleep
"""

import argparse
import re
import struct
from pathlib import Path

DEFAULT_PACK = Path('references/Skyrim.esm/PACK.txt')


def _records(path: Path):
    txt = path.read_text(encoding='utf-8', errors='replace')
    for chunk in txt.split('---RECORD_BEGIN---')[1:]:
        yield chunk.split('---RECORD_END---')[0]


def _hex(rec: str, key: str):
    m = re.search(rf'^{key}\.hex=(\S+)', rec, re.M)
    return bytes.fromhex(m.group(1)) if m else None


def _field(rec: str, key: str):
    m = re.search(rf'^{key}=(.*)$', rec, re.M)
    return m.group(1).strip() if m else None


class Pack:
    def __init__(self, rec: str):
        self.raw = rec
        self.edid = _field(rec, 'EditorID') or ''
        self.fid = _field(rec, 'FormID') or ''
        pkdt = _hex(rec, 'PKDT') or b'\0' * 12
        self.flags, self.type, self.interrupt_override, self.speed = \
            struct.unpack('<IBBB', pkdt[:7])
        self.interrupt_flags = struct.unpack('<H', pkdt[8:10])[0]
        pkcu = _hex(rec, 'PKCU')
        if pkcu:
            self.input_count, self.template, self.version = struct.unpack('<III', pkcu)
        else:
            self.input_count = self.template = self.version = 0
        self.is_root = 'PRCB' in rec
        self.xnam = _field(rec, 'XNAM')
        # Procedure tree: PNAM entries that are procedure names (post-PRCB).
        tree = rec.split('PRCB', 1)[1] if self.is_root else ''
        # Stop at the UNAM/BNAM signature block.
        tree = tree.split('\nUNAM=', 1)[0]
        self.procedures = re.findall(r'^PNAM=([A-Za-z]\w*)$', tree, re.M)
        self.signature = self._parse_signature(rec)
        self.values = self._parse_values(rec)

    def _parse_signature(self, rec: str):
        """The root's UNAM/BNAM/PNAM public-input declaration (trailing block)."""
        out = []
        # Declarations look like: UNAM=<idx> / BNAM=<name> / PNAM=<u32 flags>
        for m in re.finditer(r'^UNAM=(-?\d+)\nBNAM=(.*)\nPNAM=(\S+)', rec, re.M):
            idx, name, flags = int(m.group(1)), m.group(2).strip(), m.group(3)
            public = flags.startswith('00000001')
            out.append((idx, name, public))
        return out

    def _parse_values(self, rec: str):
        """The ordered ANAM value entries an instance supplies (type + value)."""
        # Package Data lives between PKCU and the first UNAM-only index list.
        body = rec.split('PKCU.hex=', 1)[-1]
        body = re.split(r'^XNAM=', body, maxsplit=1, flags=re.M)[0]
        out = []
        for m in re.finditer(
                r'^ANAM=(\w+)(?:\n(CNAM|PLDT|PTDA)[^\n]*)?', body, re.M):
            out.append((m.group(1), m.group(2) or ''))
        return out

    def index_list(self):
        """The UNAM index list an instance repeats verbatim (pre-XNAM)."""
        body = rec_before_xnam = self.raw
        body = body.split('PKCU.hex=', 1)[-1]
        body = re.split(r'^XNAM=', body, maxsplit=1, flags=re.M)[0]
        # In the instance/root Package Data block the bare UNAM lines (no BNAM
        # following) are the index list.
        return [int(x) for x in re.findall(r'^UNAM=(-?\d+)(?!\nBNAM)', body, re.M)]


def load(path: Path):
    return [Pack(r) for r in _records(path)]


def show(p: Pack):
    kind = 'TEMPLATE ROOT' if p.is_root else 'instance'
    print(f'{"=" * 72}\n{p.fid}  {p.edid}   [{kind}, PKDT.Type={p.type}]')
    if p.template:
        print(f'  template   : {p.template:08X}')
    print(f'  inputCount : {p.input_count}   version: {p.version}')
    print(f'  flags      : 0x{p.flags:08X}  speed={p.speed}  '
          f'interrupt=0x{p.interrupt_flags:04X}')
    print(f'  XNAM       : {p.xnam}')
    if p.procedures:
        print(f'  procedures : {" -> ".join(p.procedures)}')
    if p.values:
        print('  data inputs (positional — an instance MUST match this order):')
        for i, (atype, sub) in enumerate(p.values):
            print(f'     [{i:2d}] ANAM={atype:<16} {sub}')
    idx = p.index_list()
    if idx:
        print(f'  UNAM index list (copy verbatim): {idx}')
    if p.signature:
        print('  declared public inputs (UNAM -> BNAM):')
        for i, name, public in p.signature:
            print(f'     {i:3d}  {"pub " if public else "    "} {name}')


def emit_python(packs, names):
    """Emit a frozen TEMPLATES table for tes5_import/pack_templates.py."""
    print('# Auto-generated by tools/pack_template_dump.py — do not hand-edit.')
    print('# Signatures dumped from references/Skyrim.esm/PACK.txt.')
    print('TEMPLATES = {')
    for p in packs:
        if p.edid not in names:
            continue
        print(f'    {p.edid!r}: Template(')
        print(f'        formid=0x{int(p.fid, 16):08X},')
        print(f'        edid={p.edid!r},')
        print(f'        xnam={p.xnam},')
        print(f'        version={p.version},')
        print(f'        index_list={p.index_list()!r},')
        print(f'        inputs=[  # positional')
        for i, (atype, sub) in enumerate(p.values):
            print(f'            {atype!r},')
        print(f'        ],')
        print('    ),')
    print('}')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('names', nargs='*', help='EditorID or FormID of template(s)')
    ap.add_argument('--pack', type=Path, default=DEFAULT_PACK)
    ap.add_argument('--list', action='store_true',
                    help='list all template roots with instance counts')
    ap.add_argument('--instances-of', metavar='NAME',
                    help='show instances that use this template')
    ap.add_argument('--limit', type=int, default=3)
    ap.add_argument('--python', action='store_true',
                    help='emit a frozen TEMPLATES table')
    args = ap.parse_args()

    packs = load(args.pack)
    by_fid = {int(p.fid, 16): p for p in packs if p.fid}

    if args.list:
        counts = {}
        for p in packs:
            if p.template:
                counts[p.template] = counts.get(p.template, 0) + 1
        roots = [p for p in packs if p.is_root]
        roots.sort(key=lambda p: -counts.get(int(p.fid, 16), 0))
        print(f'{len(roots)} template roots, {len(packs) - len(roots)} instances\n')
        print(f'{"instances":>9}  {"FormID":8}  EditorID')
        for p in roots:
            n = counts.get(int(p.fid, 16), 0)
            print(f'{n:9d}  {p.fid}  {p.edid}')
        return

    if args.instances_of:
        target = next((p for p in packs if p.edid == args.instances_of), None)
        if not target:
            raise SystemExit(f'no template named {args.instances_of}')
        tfid = int(target.fid, 16)
        shown = 0
        for p in packs:
            if p.template == tfid:
                show(p)
                shown += 1
                if shown >= args.limit:
                    break
        return

    sel = []
    for n in args.names:
        try:
            sel.append(by_fid[int(n, 16)])
        except (ValueError, KeyError):
            m = [p for p in packs if p.edid == n]
            if not m:
                raise SystemExit(f'no PACK named {n}')
            sel.extend(m)

    if args.python:
        emit_python(sel, {p.edid for p in sel})
    else:
        for p in sel:
            show(p)


if __name__ == '__main__':
    main()
