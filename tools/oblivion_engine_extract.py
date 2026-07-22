#!/usr/bin/env python3
"""Extract Oblivion's authoritative dialogue tables from an unpacked Oblivion.exe.

The TES4 counterpart of tools/dialog_engine_extract.py. Same motivation: the
only descriptions of Oblivion's dialogue behaviour available to this project
came from xEdit's record definitions (layout, not behaviour) and from reading
vanilla data (what Bethesda used, never what the engine accepts). Oblivion.exe
ships unpacked -- .text entropy 6.58 -- so the real tables are readable.

Read-only interoperability analysis: nothing is patched or redistributed.

Oblivion.exe is 32-bit x86 (ImageBase 0x400000), unlike the 64-bit SkyrimSE.exe,
so pointers are 4 bytes and table strides differ. What is striking is that the
CONDITION FUNCTION OPCODES ARE THE SAME in both games -- GetIsID is 0x1048 and
GetGlobalValue 0x104A in Oblivion exactly as in Skyrim -- which is what makes a
condition-level comparison between the two emulators meaningful.

What it recovers
----------------
`--types`      the 71 dialogue type names the engine knows, in engine order,
               grouped by the DIAL DATA.Type category they belong to.
`--functions`  the condition/script functions with their opcodes, parameter
               counts and parameter types.
`--json`       write the tables for the emulator to load.

Usage:
    python tools/oblivion_engine_extract.py --types
    python tools/oblivion_engine_extract.py --functions GetStage
    python tools/oblivion_engine_extract.py --json tes4_export/oblivion_engine_tables.json
"""

import argparse
import collections
import json
import math
import struct
import sys

try:
    import pefile
except ImportError:
    sys.exit('pefile required: pip install pefile')


DEFAULT_EXE = r"D:\Other Games\Nehrim At Fate's Edge\Oblivion.exe"

# Dialogue type-name table, located by cross-referencing the pointer to the
# "GREETING" string literal into .data. Stride 12: name pointer, zero, and a
# UI message id. The row's ORDER is what matters -- rows are grouped by the
# DIAL DATA.Type category, verified against all 3,817 vanilla Oblivion DIALs.
TYPE_TABLE_RVA = 0x710da8
TYPE_STRIDE = 12
TYPE_MAX = 200

# Condition/script function table, found the same way from "GetIsID". Stride
# 0x28:
#   +0   name pointer
#   +4   description pointer
#   +8   opcode (u16) -- the SAME numbering Skyrim uses
#   +16  byte 0: takes a reference ("X.Function" form)
#        byte 2: parameter count
#   +20  pointer to the parameter descriptor array (stride 12: name pointer,
#        type code, and a trailing word)
FUNCTION_TABLE_RVA = 0x70c8c0   # row 0 is opcode 0x1000, MessageBox
FUNCTION_STRIDE = 0x28
FUNCTION_MAX = 370              # the table ends at RVA 0x710290
PARAM_STRIDE = 12

# DIAL DATA.Type, the category enum. Confirmed against the vanilla export:
# every name in the engine's type table falls in exactly one of these groups.
CATEGORY_NAMES = {
    0: 'Topic',
    1: 'Conversation',
    2: 'Combat',
    3: 'Persuasion',
    4: 'Detection',
    5: 'Service',
    6: 'Miscellaneous',
}


class Exe:
    def __init__(self, path):
        self.path = path
        pe = pefile.PE(path, fast_load=True)
        self.base = pe.OPTIONAL_HEADER.ImageBase
        self.is_32bit = pe.FILE_HEADER.Machine == 0x14c
        with open(path, 'rb') as f:
            self.data = f.read()
        self._secs = [(s.VirtualAddress,
                       s.VirtualAddress + max(s.Misc_VirtualSize,
                                              s.SizeOfRawData),
                       s.PointerToRawData) for s in pe.sections]

    def rva_to_off(self, rva):
        for va, vend, praw in self._secs:
            if va <= rva < vend:
                return praw + (rva - va)
        return None

    def cstring(self, va):
        if not va or va < self.base:
            return None
        off = self.rva_to_off(va - self.base)
        if off is None:
            return None
        end = self.data.find(b'\0', off)
        try:
            s = self.data[off:end].decode('ascii')
        except UnicodeDecodeError:
            return None
        return s if s.isprintable() else None

    def entry(self, rva, size):
        off = self.rva_to_off(rva)
        if off is None:
            return b''
        return self.data[off:off + size]

    def entropy(self):
        off = self.rva_to_off(0x1000)
        chunk = self.data[off:off + 0x100000]
        counts = collections.Counter(chunk)
        n = len(chunk)
        return -sum(c / n * math.log2(c / n) for c in counts.values())


def read_types(exe):
    """The engine's dialogue type names, in table order."""
    out = []
    for i in range(TYPE_MAX):
        e = exe.entry(TYPE_TABLE_RVA + i * TYPE_STRIDE, TYPE_STRIDE)
        if len(e) < TYPE_STRIDE:
            break
        name_va, _zero, message_id = struct.unpack('<III', e)
        name = exe.cstring(name_va)
        if name is None:
            break
        out.append({'index': i, 'name': name, 'message_id': message_id})
    return out


def read_functions(exe):
    """Condition/script functions with opcodes and parameter types."""
    out = []
    for i in range(FUNCTION_MAX):
        e = exe.entry(FUNCTION_TABLE_RVA + i * FUNCTION_STRIDE,
                      FUNCTION_STRIDE)
        if len(e) < FUNCTION_STRIDE:
            break
        name_va, desc_va = struct.unpack('<II', e[:8])
        name = exe.cstring(name_va)
        if name is None:
            continue          # the table has holes; keep scanning
        opcode, = struct.unpack('<H', e[8:10])
        takes_reference = e[16] == 1
        nparams = e[18]
        params_va, = struct.unpack('<I', e[20:24])

        params = []
        if params_va > exe.base and nparams and nparams < 32:
            prva = params_va - exe.base
            for k in range(nparams):
                pe_ = exe.entry(prva + k * PARAM_STRIDE, PARAM_STRIDE)
                if len(pe_) < PARAM_STRIDE:
                    break
                pname_va, ptype = struct.unpack('<II', pe_[:8])
                params.append({'name': exe.cstring(pname_va), 'type': ptype})
        # A CTDA stores the function as `opcode - 0x1000`, the same convention
        # Skyrim uses -- which is what lets the two emulators be compared.
        ctda_index = opcode - 0x1000 if 0x1000 <= opcode < 0x2000 else None
        out.append({'index': i, 'name': name,
                    'description': exe.cstring(desc_va),
                    'opcode': opcode, 'ctda_index': ctda_index,
                    'takes_reference': takes_reference, 'params': params})
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', default=DEFAULT_EXE)
    ap.add_argument('--types', action='store_true')
    ap.add_argument('--functions', metavar='NAME', nargs='?', const='',
                    help='list condition functions, optionally filtered')
    ap.add_argument('--json', help='write all tables to this JSON path')
    args = ap.parse_args()

    exe = Exe(args.exe)
    if not exe.is_32bit:
        sys.exit(f'{args.exe} is not 32-bit x86; this is not an Oblivion build')
    ent = exe.entropy()
    if ent > 7.5:
        sys.exit(f'.text entropy {ent:.2f} -- this build is packed and its '
                 'tables cannot be read')

    types = read_types(exe)
    funcs = read_functions(exe)
    show_default = not (args.types or args.functions is not None or args.json)

    if args.types or show_default:
        print(f'{len(types)} dialogue type names, in engine order:')
        for t in types:
            print(f'  {t["index"]:>3}  {t["name"]}')
        print()

    if args.functions is not None:
        needle = args.functions.lower()
        shown = [f for f in funcs if needle in f['name'].lower()]
        print(f'{len(shown)} of {len(funcs)} functions:')
        for f in shown:
            ref = ' [reference]' if f['takes_reference'] else ''
            idx = ('CTDA %d' % f['ctda_index']
                   if f['ctda_index'] is not None else 'script-only')
            print(f'  0x{f["opcode"]:04X}  {idx:<14} {f["name"]}{ref}')
            for k, prm in enumerate(f['params']):
                print(f'        param{k}: {prm["name"]} '
                      f'(type 0x{prm["type"]:02X})')
        print()

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump({'source': exe.path, 'categories': CATEGORY_NAMES,
                       'types': types, 'functions': funcs}, f, indent=2)
        print(f'wrote {args.json}: {len(types)} type names, '
              f'{len(funcs)} functions')


if __name__ == '__main__':
    main()
