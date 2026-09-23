#!/usr/bin/env python3
"""Cross-reference finder for SkyrimSE.exe (unpacked GOG build).

Why this exists: `tools/disasm/skyrim_disasm.py` can disassemble a known RVA, but many
engine questions start from the other end -- "which code reads this INI setting
/ this global / this string?".  That needs a full .text scan with resynchronise
-on-error (capstone stops dead at the first non-instruction byte, and .text is
full of jump tables and alignment padding, so a naive single disasm() pass
decodes only a few thousand instructions out of ~5.7 million).

Read-only interoperability analysis: never patches or redistributes anything.

Usage:
    # who references these RVAs (data addresses)?
    python tools/disasm/skyrim_xref.py --exe <exe> --xref 0x1e64fc8 0x1e64fe0

    # find a Setting object by INI name, print its default value
    python tools/disasm/skyrim_xref.py --exe <exe> --setting fActivatePickRadius

    # find every INI setting whose name matches a substring
    python tools/disasm/skyrim_xref.py --exe <exe> --setting-search Pick

    # xref a setting by name in one step (finds object, xrefs its value field)
    python tools/disasm/skyrim_xref.py --exe <exe> --setting-xref fActivatePickRadius

The instruction index is cached in the scratch dir so repeated queries are fast.
"""

import argparse
import os
import pickle
import re
import struct
import sys
import tempfile

try:
    import pefile
except ImportError:
    sys.exit('pefile required: pip install pefile')
try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
    from capstone.x86 import X86_OP_MEM, X86_REG_RIP
except ImportError:
    sys.exit('capstone required: pip install capstone')


class Image:
    def __init__(self, path):
        self.path = path
        self.pe = pefile.PE(path, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.data = open(path, 'rb').read()
        self.sections = [(s.VirtualAddress,
                          s.VirtualAddress + max(s.Misc_VirtualSize,
                                                 s.SizeOfRawData),
                          s.PointerToRawData,
                          s.Name.rstrip(b'\0').decode())
                         for s in self.pe.sections]

    def rva_to_off(self, rva):
        for va, ve, pr, _ in self.sections:
            if va <= rva < ve:
                off = pr + (rva - va)
                return off if off < len(self.data) else None
        return None

    def off_to_rva(self, off):
        for va, ve, pr, _ in self.sections:
            if pr <= off < pr + (ve - va):
                return va + (off - pr)
        return None

    def read(self, rva, n):
        off = self.rva_to_off(rva)
        return self.data[off:off + n] if off is not None else b''

    def cstr_at_va(self, va, maxlen=256):
        off = self.rva_to_off(va - self.base)
        if off is None:
            return None
        end = self.data.find(b'\0', off, off + maxlen)
        if end < 0:
            return None
        try:
            return self.data[off:end].decode('latin1')
        except Exception:
            return None

    def text(self):
        for va, ve, pr, name in self.sections:
            if name == '.text' and (ve - va) > 0x100000:
                return va, ve, pr
        raise RuntimeError('no .text')


def build_index(img, cache_dir):
    """Disassemble .text with resync, return {target_rva: [(rva, mnem, ops)]}
    for every RIP-relative memory operand. Direct call and jump targets are
    immediates, not memory operands, so a function's callers are NOT indexed."""
    tva, tve, tpr = img.text()
    size = tve - tva
    key = '%s-%d-%x' % (os.path.basename(img.path),
                        os.path.getsize(img.path), size)
    cache = os.path.join(cache_dir, 'xref-%s.pkl' % key)
    if os.path.exists(cache):
        with open(cache, 'rb') as f:
            return pickle.load(f)

    tdata = img.data[tpr:tpr + size]
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    xrefs = {}
    pos = 0
    count = 0
    base_va = img.base + tva
    while pos < size:
        progressed = False
        for insn in md.disasm(tdata[pos:], base_va + pos):
            progressed = True
            count += 1
            for op in insn.operands:
                if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP:
                    tgt = insn.address + insn.size + op.mem.disp - img.base
                    xrefs.setdefault(tgt, []).append(
                        (insn.address - img.base, insn.mnemonic, insn.op_str))
            pos = insn.address + insn.size - base_va
        if not progressed:
            pos += 1
        else:
            pos += 1  # resync one byte past the last decoded instruction
    result = {'xrefs': xrefs, 'count': count}
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache, 'wb') as f:
        pickle.dump(result, f, protocol=4)
    return result


# ---------------------------------------------------------------- settings

SETTING_STRIDE = 0x18   # {const char* name; void* vtable; union value;}


def find_settings(img, pattern):
    """Locate Setting objects in .data whose name string matches `pattern`."""
    rx = re.compile(re.escape(pattern.encode()), re.I)
    hits = []
    for m in rx.finditer(img.data):
        srva = img.off_to_rva(m.start())
        if srva is None:
            continue
        # must be the start of a C string
        if m.start() > 0 and img.data[m.start() - 1] not in (0, 0x20):
            continue
        sva = img.base + srva
        ptr = struct.pack('<Q', sva)
        for pm in re.finditer(re.escape(ptr), img.data):
            orva = img.off_to_rva(pm.start())
            if orva is None:
                continue
            name = img.cstr_at_va(sva)
            blk = img.read(orva, SETTING_STRIDE)
            if len(blk) < SETTING_STRIDE:
                continue
            nm, vt, val = struct.unpack('<QQQ', blk)
            hits.append({
                'name': name,
                'obj_rva': orva,
                'value_rva': orva + 0x10,
                'raw': val,
                'f32': struct.unpack('<f', blk[0x10:0x14])[0],
                'u32': struct.unpack('<I', blk[0x10:0x14])[0],
            })
    return hits


def fmt_setting(s):
    n = s['name'] or '?'
    if n.startswith('f'):
        v = 'float %.4f' % s['f32']
    elif n.startswith(('i', 'u')):
        v = 'int %d' % s['u32']
    elif n.startswith('b'):
        v = 'bool %d' % (s['u32'] & 0xff)
    else:
        v = 'raw 0x%x' % s['raw']
    return '%-42s obj=0x%08x value=0x%08x  %s' % (
        n, s['obj_rva'], s['value_rva'], v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--cache', default=os.path.join(
        tempfile.gettempdir(), 'skyrim_xref_cache'))
    ap.add_argument('--xref', nargs='*', help='RVAs (hex) to cross-reference')
    ap.add_argument('--setting', help='exact-ish INI setting name to resolve')
    ap.add_argument('--setting-search', help='substring search over settings')
    ap.add_argument('--setting-xref', help='resolve setting then xref its value')
    args = ap.parse_args()

    img = Image(args.exe)
    print('%s imagebase=0x%x' % (os.path.basename(args.exe), img.base))

    if args.setting_search or args.setting:
        pat = args.setting_search or args.setting
        for s in find_settings(img, pat):
            print(' ', fmt_setting(s))
        return

    idx = None
    targets = []
    if args.setting_xref:
        hits = find_settings(img, args.setting_xref)
        if not hits:
            sys.exit('setting not found: %s' % args.setting_xref)
        for s in hits:
            print(' ', fmt_setting(s))
            targets.append(s['value_rva'])
    if args.xref:
        targets += [int(x, 16) for x in args.xref]

    if targets:
        idx = build_index(img, args.cache)
        print('indexed %d instructions' % idx['count'])
        for t in targets:
            refs = idx['xrefs'].get(t, [])
            print('=== xrefs to 0x%08x : %d' % (t, len(refs)))
            for rva, mn, ops in refs:
                print('    0x%08x  %s %s' % (rva, mn, ops))


if __name__ == '__main__':
    main()
