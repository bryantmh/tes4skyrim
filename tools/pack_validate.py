#!/usr/bin/env python3
"""Validate converted PACK records against the engine's real load/run contract.

Every rule here was derived from disassembling the GOG (unencrypted) SkyrimSE.exe
and/or a census of Skyrim.esm — not from xEdit field names.  See
docs/package_conversion_plan.md §7-10 and the engine notes below.

Engine facts this encodes (GOG 1.6.659 RVAs):
  * TESPackage::LoadBuffer = 0x451990.  Subrecords it reads: PKDT PSDT PLDT PTDT
    PTDA PKCU PKPT PLD2 PTD2 PKE2 PKW3 PKDD PKFD PT2A CTDA IDLA-F POBA POEA POCA
    EDID OBND VMAD.  CNAM/QNAM are package-level u32s (0x45217d).  ANAM/UNAM/XNAM
    are NOT in that switch — they are consumed by the data-input reader.
  * 0x404710 is the data-input reader.  It is driven by PKCU.DataInputCount and
    runs REGARDLESS of PKCU.Template, reading exactly that many ANAM entries.
    => #ANAM must equal PKCU.DataInputCount or inputs are lost/over-read.
  * 0x404e10 resolves a procedure's logical slot: the container holds a value
    array and a parallel INDEX-BYTE array (the UNAM list); a procedure asking for
    slot N takes the entry whose UNAM byte == N.  => the UNAM list must match the
    template root's, and its length must equal the input count.
  * PKDT byte 4 = Type; 18 (Package) and 19 (Package Template) both dispatch
    through 0x450c68.  A root is 19, an instance is 18.
  * PackageLocation::Type (CommonLibSSE-NG RE/P/PackageLocation.h):
    0 kNearReference, 1 kInCell, 2 kNearPackageStartLocation, 3 kNearEditorLocation,
    4 kObjectID, 5 kObjectType, 6 kNearLinkedReference, 7 kAtPackagelocation,
    8 kAlias_Reference, 9 kAlias_Location, 12 kNearSelf.
    Type 9 wants a LOCATION alias; a reference alias must use 8 (vanilla: type 8
    x585, type 9 x1 out of 6838).

Usage:
    python tools/pack_validate.py output/Oblivion.esm/Oblivion.esm
    python tools/pack_validate.py <esm> --ref <Skyrim.esm>   # compare templates
    python tools/pack_validate.py <esm> --fid 036632         # one package
    python tools/pack_validate.py <esm> --summary
"""

import argparse
import mmap
import struct
import sys
import zlib
from collections import Counter, defaultdict

REC_HDR = 24
GRP_HDR = 24

PKDT_TYPE_PACKAGE = 18
PKDT_TYPE_TEMPLATE = 19

LOC_TYPES = {
    0: 'kNearReference', 1: 'kInCell', 2: 'kNearPackageStartLocation',
    3: 'kNearEditorLocation', 4: 'kObjectID', 5: 'kObjectType',
    6: 'kNearLinkedReference', 7: 'kAtPackagelocation',
    8: 'kAlias_Reference', 9: 'kAlias_Location', 12: 'kNearSelf',
}
# Location types a converted package may legitimately emit. 9 is excluded: it
# resolves a LOCATION alias, so a reference-alias index there resolves to nothing
# and the actor starts the package but never moves.
LOC_TYPES_OK = {0, 1, 2, 3, 4, 5, 6, 7, 8, 12}

TARGET_TYPES_OK = {0, 1, 2, 3, 4, 6}   # PTDA: 4 = Ref Alias (236 vanilla uses)


def iter_records(data, start, end):
    pos = start
    while pos < end - 8:
        sig = data[pos:pos + 4]
        if sig == b'GRUP':
            size = struct.unpack('<I', data[pos + 4:pos + 8])[0]
            if size < GRP_HDR:
                return
            yield from iter_records(data, pos + GRP_HDR, pos + size)
            pos += size
            continue
        size, flags = struct.unpack('<II', data[pos + 4:pos + 12])
        fid = struct.unpack('<I', data[pos + 12:pos + 16])[0]
        ver = struct.unpack('<H', data[pos + 20:pos + 22])[0]
        body = data[pos + REC_HDR:pos + REC_HDR + size]
        if flags & 0x00040000:
            try:
                body = zlib.decompress(body[4:])
            except zlib.error:
                body = b''
        yield sig.decode('latin1'), fid, ver, body
        pos += REC_HDR + size


def subrecords(body):
    out = []
    p = 0
    while p < len(body) - 5:
        sig = body[p:p + 4].decode('latin1')
        ln = struct.unpack('<H', body[p + 4:p + 6])[0]
        out.append((sig, body[p + 6:p + 6 + ln]))
        p += 6 + ln
    return out


class Pack:
    """The engine-relevant view of one PACK record."""

    def __init__(self, fid, ver, subs):
        self.fid = fid
        self.ver = ver
        self.edid = ''
        self.pkdt = None
        self.pkcu = None
        self.anams = []
        self.unams = []
        self.xnam = None
        self.plds = []
        self.ptdas = []
        self.qnam = None
        for sig, d in subs:
            if sig == 'EDID':
                self.edid = d.rstrip(b'\0').decode('latin1', 'replace')
            elif sig == 'PKDT':
                self.pkdt = d
            elif sig == 'PKCU' and self.pkcu is None:
                self.pkcu = d
            elif sig == 'ANAM':
                self.anams.append(d.rstrip(b'\0').decode('latin1', 'replace'))
            elif sig == 'UNAM':
                self.unams.append(d[0] if d else None)
            elif sig == 'XNAM':
                self.xnam = d[0] if d else None
            elif sig == 'PLDT':
                self.plds.append(d)
            elif sig == 'PTDA':
                self.ptdas.append(d)
            elif sig == 'QNAM':
                self.qnam = struct.unpack('<I', d)[0] if len(d) == 4 else None

    @property
    def type(self):
        return self.pkdt[4] if self.pkdt and len(self.pkdt) > 4 else None

    @property
    def input_count(self):
        return struct.unpack_from('<I', self.pkcu, 0)[0] if self.pkcu else None

    @property
    def template(self):
        return struct.unpack_from('<I', self.pkcu, 4)[0] if self.pkcu else None

    @property
    def version(self):
        return struct.unpack_from('<I', self.pkcu, 8)[0] if self.pkcu else None

    def check(self, roots=None):
        """[(severity, message)] — engine-contract violations."""
        out = []
        if self.pkdt is None:
            out.append(('ERROR', 'no PKDT (engine cannot type the package)'))
            return out
        if len(self.pkdt) < 12:
            out.append(('ERROR', f'PKDT is {len(self.pkdt)}B, expected 12'))
        if self.type not in (PKDT_TYPE_PACKAGE, PKDT_TYPE_TEMPLATE):
            out.append(('ERROR', f'PKDT.Type={self.type} is neither 18 nor 19'))
        if self.pkcu is None:
            out.append(('ERROR', 'no PKCU (template instance data missing)'))
            return out
        if len(self.pkcu) != 12:
            out.append(('ERROR', f'PKCU is {len(self.pkcu)}B, expected 12'))

        # The data-input reader (0x404710) is driven by this count.
        if len(self.anams) != self.input_count:
            out.append(('ERROR',
                        f'PKCU.DataInputCount={self.input_count} but '
                        f'{len(self.anams)} ANAM entries — the reader consumes '
                        f'exactly the count, so inputs are lost or over-read'))

        # UNAM is the index-byte array 0x404e10 searches.
        if self.type == PKDT_TYPE_PACKAGE:
            if not self.unams:
                out.append(('ERROR', 'instance has no UNAM index list — every '
                                     'procedure slot lookup will miss'))
            elif len(self.unams) != self.input_count:
                out.append(('WARN',
                            f'{len(self.unams)} UNAM bytes vs input count '
                            f'{self.input_count}'))
            if self.template in (None, 0):
                out.append(('ERROR', 'PKDT.Type=18 (instance) but '
                                     'PKCU.Template=0'))

        for d in self.plds:
            if len(d) != 12:
                out.append(('ERROR', f'PLDT is {len(d)}B, expected 12'))
                continue
            lt = struct.unpack_from('<i', d, 0)[0]
            if lt not in LOC_TYPES_OK:
                out.append(('ERROR',
                            f'PLDT type {lt} ({LOC_TYPES.get(lt, "?")}) — '
                            f'type 9 needs a LOCATION alias; use 8 '
                            f'(kAlias_Reference) for a reference alias'))
        for d in self.ptdas:
            if len(d) != 12:
                out.append(('ERROR', f'PTDA is {len(d)}B, expected 12'))
                continue
            tt = struct.unpack_from('<i', d, 0)[0]
            if tt not in TARGET_TYPES_OK:
                out.append(('WARN', f'PTDA type {tt} unattested in vanilla'))

        # Template agreement: the instance's UNAM list and input count must
        # match the root's, because the procedure resolves slots by UNAM byte.
        if roots is not None and self.template:
            root = roots.get(self.template & 0x00FFFFFF)
            if root is None:
                out.append(('WARN', f'template 0x{self.template:08x} not found '
                                    f'in the reference master'))
            else:
                if self.input_count != root.input_count:
                    out.append(('ERROR',
                                f'input count {self.input_count} != template '
                                f'{root.edid} count {root.input_count}'))
                if self.version != root.version:
                    out.append(('ERROR',
                                f'PKCU version {self.version} != template '
                                f'{root.edid} version {root.version}'))
                if self.unams and root.unams:
                    r = root.unams[:root.input_count or len(root.unams)]
                    if self.unams != r:
                        out.append(('ERROR',
                                    f'UNAM {self.unams} != template {root.edid} '
                                    f'{r} — procedure slot lookups resolve to '
                                    f'the wrong inputs'))
                if self.anams and root.anams:
                    ra = root.anams[:root.input_count or len(root.anams)]
                    if self.anams != ra:
                        out.append(('ERROR',
                                    f'ANAM types {self.anams} != template '
                                    f'{ra}'))
        return out


def load_packs(path, want_roots_only=False):
    with open(path, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    hsize = struct.unpack('<I', mm[4:8])[0]
    packs = []
    for sig, fid, ver, body in iter_records(mm, REC_HDR + hsize, len(mm)):
        if sig != 'PACK':
            continue
        p = Pack(fid, ver, subrecords(body))
        if want_roots_only and p.type != PKDT_TYPE_TEMPLATE:
            continue
        packs.append(p)
    return packs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('esm')
    ap.add_argument('--ref', help='reference master holding the template roots '
                                  '(usually Skyrim.esm)')
    ap.add_argument('--fid', help='only this package (low-24 hex)')
    ap.add_argument('--summary', action='store_true')
    ap.add_argument('--limit', type=int, default=40)
    args = ap.parse_args()

    roots = None
    if args.ref:
        roots = {p.fid & 0x00FFFFFF: p for p in load_packs(args.ref,
                                                           want_roots_only=True)}
        print(f'loaded {len(roots)} template roots from {args.ref}')

    packs = load_packs(args.esm)
    print(f'{len(packs)} PACK records in {args.esm}\n')

    only = int(args.fid, 16) & 0x00FFFFFF if args.fid else None
    counts = Counter()
    by_msg = defaultdict(list)
    shown = 0
    for p in packs:
        if only is not None and (p.fid & 0x00FFFFFF) != only:
            continue
        problems = p.check(roots)
        for sev, msg in problems:
            counts[sev] += 1
            key = msg.split('—')[0].strip()[:70]
            by_msg[key].append(p.edid or hex(p.fid))
        if problems and not args.summary and shown < args.limit:
            shown += 1
            print(f'PACK 0x{p.fid:08x} {p.edid}')
            print(f'  type={p.type} template=0x{(p.template or 0):08x} '
                  f'inputs={p.input_count} anams={len(p.anams)} '
                  f'unams={p.unams}')
            for sev, msg in problems:
                print(f'  [{sev}] {msg}')
            print()

    print('--- summary ---')
    for sev, n in counts.most_common():
        print(f'  {sev}: {n}')
    if by_msg:
        print('\n  most common:')
        for msg, who in sorted(by_msg.items(), key=lambda kv: -len(kv[1]))[:12]:
            print(f'    {len(who):5d}  {msg}')
            print(f'           e.g. {", ".join(who[:3])}')
    if not counts:
        print('  clean — no engine-contract violations found')


if __name__ == '__main__':
    main()
