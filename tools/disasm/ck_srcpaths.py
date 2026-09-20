#!/usr/bin/env python3
r"""Mine CreationKit.exe for the documentation it carries that the game exe lacks.

Why this exists: the CK is a superset build of the engine compiled with asserts
and diagnostics left ENABLED.  It carries Bethesda's original source file paths
(e:\_skyrimhd\code\gamesln\...), ~17k diagnostic strings the retail exe strips,
the authoring/generation RTTI classes (Recast, navmesh edit, warnings handler),
and 433 record-editor DIALOG templates.  For "why was my record rejected"
questions this beats SkyrimSE.exe outright.

Unlike the retail Steam exe, the Steam CK is NOT DRM-packed, so it disassembles
statically -- `--pe-check` proves it.

Read-only interoperability analysis: never patches or redistributes anything.

Usage:
    python tools/disasm/ck_srcpaths.py --pe-check              # DRM/entropy verdict
    python tools/disasm/ck_srcpaths.py --pe-check --compare <other.exe>

    python tools/disasm/ck_srcpaths.py --tree                  # source modules + counts
    python tools/disasm/ck_srcpaths.py --tree --filter navmesh # matching source files

    python tools/disasm/ck_srcpaths.py --strings navmesh       # CK-only diagnostics
    python tools/disasm/ck_srcpaths.py --strings "refinaliz" --limit 100

    python tools/disasm/ck_srcpaths.py --rtti Recast           # CK-only RTTI classes
    python tools/disasm/ck_srcpaths.py --dialogs               # record editor forms
    python tools/disasm/ck_srcpaths.py --dialogs --filter Nav

`--strings` and `--rtti` diff against the retail game exe so you only see what
the CK adds; pass --no-diff for the raw set.  Follow up with
`tools/disasm/ck_strref.py --pattern <regex>` to get the referencing code RVAs, then
`tools/disasm/skyrim_disasm.py --exe <ck> --disasm <rva>` to read the check.

See docs/commentary/ck_exe_disassembly.md for the measured findings.
"""

import argparse
import collections
import math
import os
import re
import struct
import sys

CK_DEFAULT = (r'C:\Program Files (x86)\Steam\steamapps\common'
              r'\Skyrim Special Edition\CreationKit.exe')
GAME_DEFAULT = (r'C:\Program Files (x86)\Steam\steamapps\content\app_489830'
                r'\depot_489833\SkyrimSE.1.6.659.unpacked.exe')
SEP = chr(92)
TREE_ROOT = 'e:' + SEP + '_skyrimhd' + SEP


def _read(path):
    if not os.path.exists(path):
        sys.exit('not found: %s' % path)
    with open(path, 'rb') as fh:
        return fh.read()


def _sections(d):
    """(name, vsize, vaddr, rawsize, rawoff, flags) for each PE section."""
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    if d[pe:pe + 4] != b'PE\0\0':
        sys.exit('not a PE file')
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    optsz = struct.unpack_from('<H', d, pe + 20)[0]
    opt = pe + 24
    out = []
    base = opt + optsz
    for i in range(nsec):
        raw = d[base + i * 40:base + (i + 1) * 40]
        name = raw[0:8].rstrip(b'\0').decode('latin1')
        vsz, vaddr, rsz, roff = struct.unpack_from('<IIII', raw, 8)
        flags = struct.unpack_from('<I', raw, 36)[0]
        out.append((name, vsz, vaddr, rsz, roff, flags))
    magic = struct.unpack_from('<H', d, opt)[0]
    ep = struct.unpack_from('<I', d, opt + 16)[0]
    return out, ep, magic


def _entropy(buf):
    if not buf:
        return 0.0
    counts = collections.Counter(buf)
    n = len(buf)
    return -sum((v / n) * math.log2(v / n) for v in counts.values())


def pe_check(path):
    d = _read(path)
    secs, ep, magic = _sections(d)
    print('%s' % path)
    print('  size %d  %s  entrypoint RVA %08x'
          % (len(d), 'PE32+' if magic == 0x20b else 'PE32', ep))
    ep_sec, text_ent = None, None
    for name, vsz, vaddr, rsz, roff, flags in secs:
        ent = _entropy(d[roff:roff + min(rsz, 1 << 20)]) if rsz else 0.0
        if vaddr <= ep < vaddr + max(vsz, rsz):
            ep_sec = name
        if name == '.text' and text_ent is None and rsz:
            text_ent = ent
        print('    %-9s vaddr=%08x rawsize=%08x entropy=%.3f'
              % (name, vaddr, rsz, ent))
    names = [s[0] for s in secs]
    packed = ('.bind' in names or (text_ent or 0) > 7.5
              or ep_sec not in ('.text', None))
    print('  entrypoint section: %s' % ep_sec)
    print('  VERDICT: %s'
          % ('DRM-PACKED - static disassembly yields garbage' if packed
             else 'CLEAN - disassembles statically'))
    return not packed


def _strings(d, minlen=6):
    pat = ('[ -~]{%d,300}' % minlen).encode() + rb'\x00'
    return set(m.group(0)[:-1].decode('latin1')
               for m in re.finditer(pat, d))


def _rtti(d):
    return set(m.group(0).decode('latin1')
               for m in re.finditer(rb'\.\?A[VU][A-Za-z0-9_@?$]{2,200}@@', d))


def _srcpaths(strs):
    return sorted(s for s in strs
                  if re.search(r'[.](cpp|h|inl)$', s, re.I)
                  and s.lower().startswith(TREE_ROOT))


def cmd_tree(ck, filt):
    paths = _srcpaths(_strings(_read(ck)))
    if filt:
        low = filt.lower()
        hits = [s for s in paths if low in s.lower()]
        print('source files matching %r: %d' % (filt, len(hits)))
        for s in hits:
            print('   ', s)
        return
    counts = collections.Counter()
    for s in paths:
        parts = s.lower().split(SEP)
        counts[SEP.join(parts[4:-1])] += 1
    print('compiled-in source paths under %s: %d' % (TREE_ROOT, len(paths)))
    for key, val in sorted(counts.items()):
        print('  %4d  %s' % (val, key))


def cmd_strings(ck, game, pattern, limit, diff):
    pool = _strings(_read(ck), minlen=8)
    if diff and os.path.exists(game):
        pool -= _strings(_read(game), minlen=8)
    rx = re.compile(pattern, re.I)
    hits = sorted(s for s in pool if rx.search(s))
    print('%sstrings matching %r: %d'
          % ('CK-only ' if diff else '', pattern, len(hits)))
    for s in hits[:limit]:
        print('   |', s)
    if len(hits) > limit:
        print('   ... %d more (raise --limit)' % (len(hits) - limit))


def cmd_rtti(ck, game, pattern, limit, diff):
    pool = _rtti(_read(ck))
    if diff and os.path.exists(game):
        pool -= _rtti(_read(game))

    def clean(n):
        return n[4:].rstrip('@')

    names = sorted(clean(n) for n in pool)
    if pattern:
        rx = re.compile(pattern, re.I)
        names = [n for n in names if rx.search(n)]
    print('%sRTTI classes%s: %d'
          % ('CK-only ' if diff else '',
             ' matching %r' % pattern if pattern else '', len(names)))
    for n in names[:limit]:
        print('   ', n)
    if len(names) > limit:
        print('   ... %d more (raise --limit)' % (len(names) - limit))


def cmd_dialogs(ck, filt, limit):
    try:
        import pefile
    except ImportError:
        sys.exit('pefile required: pip install pefile')
    pe = pefile.PE(ck)
    if not hasattr(pe, 'DIRECTORY_ENTRY_RESOURCE'):
        sys.exit('no resource directory')

    def rdsz(buf, off):
        val = struct.unpack_from('<H', buf, off)[0]
        if val == 0:
            return None, off + 2
        if val == 0xffff:
            return struct.unpack_from('<H', buf, off + 2)[0], off + 4
        out = []
        while True:
            ch = struct.unpack_from('<H', buf, off)[0]
            off += 2
            if ch == 0:
                break
            out.append(chr(ch))
        return ''.join(out), off

    rows = []
    for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if entry.id != 5:            # RT_DIALOG
            continue
        for sub in entry.directory.entries:
            rid = sub.id if sub.id is not None else str(sub.name)
            for leaf in sub.directory.entries:
                buf = pe.get_data(leaf.data.struct.OffsetToData,
                                  leaf.data.struct.Size)
                try:
                    sig, ext = struct.unpack_from('<HH', buf, 0)
                    off = 26 if (sig == 1 and ext == 0xffff) else 18
                    _, off = rdsz(buf, off)     # menu
                    _, off = rdsz(buf, off)     # class
                    title, off = rdsz(buf, off)
                except Exception:
                    continue
                if title:
                    rows.append((str(rid), str(title)))
    if filt:
        low = filt.lower()
        rows = [r for r in rows if low in r[1].lower()]
    print('DIALOG resources%s: %d'
          % (' matching %r' % filt if filt else '', len(rows)))
    for rid, title in sorted(rows, key=lambda r: r[1])[:limit]:
        print('  %-24s %s' % (rid, title))


def main():
    ap = argparse.ArgumentParser(
        description='Mine CreationKit.exe for engine documentation.')
    ap.add_argument('--exe', default=CK_DEFAULT, help='CreationKit.exe')
    ap.add_argument('--game', default=GAME_DEFAULT,
                    help='retail exe to diff against (GOG/AE, unpacked)')
    ap.add_argument('--pe-check', action='store_true',
                    help='report DRM/entropy verdict')
    ap.add_argument('--compare', help='second exe for --pe-check')
    ap.add_argument('--tree', action='store_true',
                    help='compiled-in source paths by module')
    ap.add_argument('--strings', metavar='REGEX',
                    help='diagnostic strings matching REGEX')
    ap.add_argument('--rtti', nargs='?', const='', metavar='REGEX',
                    help='RTTI class names matching REGEX')
    ap.add_argument('--dialogs', action='store_true',
                    help='record-editor DIALOG resources')
    ap.add_argument('--filter', help='substring filter for --tree/--dialogs')
    ap.add_argument('--limit', type=int, default=60)
    ap.add_argument('--no-diff', action='store_true',
                    help='do not subtract the retail exe set')
    a = ap.parse_args()

    did = False
    if a.pe_check:
        pe_check(a.exe)
        if a.compare:
            print()
            pe_check(a.compare)
        did = True
    if a.tree:
        cmd_tree(a.exe, a.filter)
        did = True
    if a.strings:
        cmd_strings(a.exe, a.game, a.strings, a.limit, not a.no_diff)
        did = True
    if a.rtti is not None:
        cmd_rtti(a.exe, a.game, a.rtti, a.limit, not a.no_diff)
        did = True
    if a.dialogs:
        cmd_dialogs(a.exe, a.filter, a.limit)
        did = True
    if not did:
        ap.print_help()


if __name__ == '__main__':
    main()
