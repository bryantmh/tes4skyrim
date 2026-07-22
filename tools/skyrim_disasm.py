#!/usr/bin/env python3
"""Static analysis helper for SkyrimSE.exe — RTTI class/vtable lookup + disassembly.

Why this exists: some TES4->TES5 contracts (which PLDT location types the engine
actually resolves, what a procedure needs before it will move an actor) cannot be
settled by comparing records against Skyrim.esm — vanilla only shows what
Bethesda *used*, not what the engine *accepts*.  SkyrimSE.exe ships with RTTI
type names intact (BGSProcedureEscort, BGSPackageDataLocation, BGSLocAlias, ...),
so the real behaviour is readable.

Read-only interoperability analysis: this never patches or redistributes anything.

Usage:
    # list RTTI classes matching a substring
    python tools/skyrim_disasm.py --find Procedure
    python tools/skyrim_disasm.py --find PackageDataLocation

    # vtable for a class (RVA + the function pointers it holds)
    python tools/skyrim_disasm.py --vtable BGSPackageDataLocation

    # disassemble at an RVA (or a vtable slot: --vtable X --slot N)
    python tools/skyrim_disasm.py --disasm 0x1234567 --count 80
    python tools/skyrim_disasm.py --vtable BGSProcedureEscort --slot 3 --count 120

    # follow calls/jumps found while disassembling
    python tools/skyrim_disasm.py --disasm 0x1234567 --count 200 --show-targets

Default exe path is auto-detected from the registry-installed SSE, override with
--exe.
"""

import argparse
import mmap
import os
import re
import struct
import sys

try:
    import pefile
except ImportError:
    sys.exit('pefile required: pip install pefile')
try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
except ImportError:
    sys.exit('capstone required: pip install capstone')


DEFAULT_EXE = (r'C:\Program Files (x86)\Steam\steamapps\common'
               r'\Skyrim Special Edition\SkyrimSE.exe')


class Binary:
    def __init__(self, path: str):
        self.path = path
        self.pe = pefile.PE(path, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        with open(path, 'rb') as f:
            self.data = f.read()
        self._sections = [(s.VirtualAddress,
                           s.VirtualAddress + max(s.Misc_VirtualSize,
                                                  s.SizeOfRawData),
                           s.PointerToRawData, s.Name.rstrip(b'\0').decode())
                          for s in self.pe.sections]
        self.md = Cs(CS_ARCH_X86, CS_MODE_64)
        self.md.detail = True

    # -- address conversion ------------------------------------------------

    def rva_to_off(self, rva: int):
        for va, vend, praw, _ in self._sections:
            if va <= rva < vend:
                return praw + (rva - va)
        return None

    def off_to_rva(self, off: int):
        for va, vend, praw, _ in self._sections:
            size = vend - va
            if praw <= off < praw + size:
                return va + (off - praw)
        return None

    def va_to_rva(self, va: int) -> int:
        return va - self.base if va >= self.base else va

    def read(self, rva: int, n: int) -> bytes:
        off = self.rva_to_off(rva)
        if off is None:
            return b''
        return self.data[off:off + n]

    def u64(self, rva: int):
        b = self.read(rva, 8)
        return struct.unpack('<Q', b)[0] if len(b) == 8 else None

    def u32(self, rva: int):
        b = self.read(rva, 4)
        return struct.unpack('<I', b)[0] if len(b) == 4 else None

    # -- RTTI --------------------------------------------------------------

    def find_rtti_names(self, needle: str = '') -> list:
        """[(mangled, type_descriptor_rva)] for RTTI type names matching needle.

        An MSVC type descriptor is: vftable ptr (8) + spare (8) + name bytes.
        The name we match sits at descriptor+16.
        """
        out = []
        for m in re.finditer(rb'\.\?AV[A-Za-z0-9_@]{2,120}?@@', self.data):
            name = m.group(0).decode('latin1')
            if needle and needle.lower() not in name.lower():
                continue
            name_rva = self.off_to_rva(m.start())
            if name_rva is None:
                continue
            out.append((name, name_rva - 16))   # descriptor start
        return sorted(set(out))

    def vtables_for(self, class_name: str) -> list:
        """RVAs of vtables whose COL names `class_name`.

        Layout: [.. COL ptr][vtable start]. A complete object locator (COL) for
        x64 holds a *relative* pointer to the type descriptor at +12.
        """
        descs = [rva for nm, rva in self.find_rtti_names(class_name)
                 if nm == f'.?AV{class_name}@@']
        if not descs:
            return []
        desc = descs[0]
        cols = []
        # find COLs pointing at this descriptor (field at +12 is desc RVA)
        target = struct.pack('<I', desc)
        for m in re.finditer(re.escape(target), self.data):
            off = m.start()
            col_off = off - 12
            col_rva = self.off_to_rva(col_off)
            if col_rva is None:
                continue
            sig = self.u32(col_rva)
            if sig not in (0, 1):        # COL signature: 1 on x64
                continue
            cols.append(col_rva)
        # a vtable begins right after a pointer to its COL
        vts = []
        for col in cols:
            ptr = struct.pack('<Q', self.base + col)
            for m in re.finditer(re.escape(ptr), self.data):
                vt_rva = self.off_to_rva(m.start() + 8)
                if vt_rva is not None:
                    vts.append(vt_rva)
        return sorted(set(vts))

    def vtable_slots(self, vt_rva: int, n: int = 24) -> list:
        """[(slot, func_rva)] — stops at the first non-code pointer."""
        out = []
        for i in range(n):
            va = self.u64(vt_rva + i * 8)
            if not va or va < self.base:
                break
            rva = va - self.base
            if self.rva_to_off(rva) is None:
                break
            out.append((i, rva))
        return out

    # -- disassembly -------------------------------------------------------

    def disasm(self, rva: int, count: int = 60):
        off = self.rva_to_off(rva)
        if off is None:
            return []
        code = self.data[off:off + count * 16]
        return list(self.md.disasm(code, self.base + rva))[:count]


def _fmt(binary: Binary, insns, show_targets=False):
    lines = []
    targets = []
    for ins in insns:
        rva = ins.address - binary.base
        lines.append(f'  {rva:#010x}  {ins.mnemonic:<7} {ins.op_str}')
        if show_targets and ins.mnemonic in ('call', 'jmp') and \
                ins.op_str.startswith('0x'):
            try:
                targets.append(int(ins.op_str, 16) - binary.base)
            except ValueError:
                pass
    return lines, targets


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', default=DEFAULT_EXE)
    ap.add_argument('--find', help='list RTTI class names containing this')
    ap.add_argument('--vtable', help='show vtable(s) for this class name')
    ap.add_argument('--slot', type=int, help='with --vtable: disassemble this slot')
    ap.add_argument('--disasm', help='disassemble at this RVA (hex ok)')
    ap.add_argument('--count', type=int, default=60)
    ap.add_argument('--show-targets', action='store_true',
                    help='list call/jmp targets found')
    args = ap.parse_args()

    if not os.path.exists(args.exe):
        sys.exit(f'not found: {args.exe}')
    b = Binary(args.exe)
    print(f'{os.path.basename(args.exe)}  imagebase={b.base:#x}')

    if args.find:
        names = b.find_rtti_names(args.find)
        print(f'\n{len(names)} RTTI names matching {args.find!r}:')
        for nm, rva in names:
            print(f'  {rva:#010x}  {nm}')

    if args.vtable:
        vts = b.vtables_for(args.vtable)
        print(f'\nvtables for {args.vtable}: '
              f'{", ".join(hex(v) for v in vts) or "none"}')
        for vt in vts:
            print(f'\n  vtable {vt:#x}:')
            for slot, frva in b.vtable_slots(vt):
                print(f'    [{slot:2d}] {frva:#010x}')
        if args.slot is not None and vts:
            slots = dict(b.vtable_slots(vts[0]))
            if args.slot in slots:
                rva = slots[args.slot]
                print(f'\n  disasm slot {args.slot} @ {rva:#x}:')
                lines, tg = _fmt(b, b.disasm(rva, args.count), args.show_targets)
                print('\n'.join(lines))
                if tg:
                    print('  targets: ' + ', '.join(hex(t) for t in sorted(set(tg))))

    if args.disasm:
        rva = int(args.disasm, 16) if args.disasm.startswith('0x') \
            else int(args.disasm, 16)
        if rva > b.base:
            rva -= b.base
        print(f'\ndisasm @ {rva:#x}:')
        lines, tg = _fmt(b, b.disasm(rva, args.count), args.show_targets)
        print('\n'.join(lines))
        if tg:
            print('  targets: ' + ', '.join(hex(t) for t in sorted(set(tg))))


if __name__ == '__main__':
    main()
