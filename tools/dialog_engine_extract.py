#!/usr/bin/env python3
"""Extract the engine's authoritative dialogue tables from an unpacked SkyrimSE.exe.

Why this exists: every prior description of Skyrim's dialogue behaviour in this
project came from xEdit's definitions (which record *layout*, not *behaviour*,
and list subtypes alphabetically with no ordering or category information) or
from observing vanilla data (which shows only what Bethesda used, never what the
engine accepts).  The GOG/Anniversary SkyrimSE.exe is NOT DRM-packed -- .text
entropy is 6.04, versus 8.00 for the Steam build -- so the real tables are
readable directly.

Read-only interoperability analysis: this never patches or redistributes
anything.  It reads tables out of a binary the user already owns and prints
them.

What it recovers
----------------
`--categories`  the 8 dialogue categories, in engine order, each with the
                subtype index its range starts at.
`--subtypes`    all 103 DIAL subtypes, in engine order, with the category each
                belongs to, its 4-character DATA tag, its numeric subtype id
                (the value that actually goes in DIAL DATA), and the engine
                flag byte that marks player-selectable topics.
`--json`        write the whole lot to a JSON file for the emulator to load.

Usage:
    python tools/dialog_engine_extract.py --subtypes
    python tools/dialog_engine_extract.py --json tes5_import/dialog_engine_tables.json
"""

import argparse
import json
import re
import struct
import sys

try:
    import pefile
except ImportError:
    sys.exit('pefile required: pip install pefile')


DEFAULT_EXE = r'D:\Other Games\Skyrim Anniversary Edition\SkyrimSE.exe'

# Anchors located by cross-referencing the pointer to the "PlayerDialogue"
# string literal into .data.  Both tables are plain static arrays.
CATEGORY_TABLE_RVA = 0x1e638d0
CATEGORY_COUNT = 8
CATEGORY_STRIDE = 16

SUBTYPE_TABLE_RVA = 0x1e63950
SUBTYPE_STRIDE = 40
SUBTYPE_MAX = 200          # walk stops at the first entry with no name pointer

# The script/condition function table. Each 0x50-byte row is:
#   +0   name pointer
#   +16  script opcode (u16), e.g. 0x103A GetStage, 0x1048 GetIsID
#   +32  byte 0: function takes a reference (the "identity" functions)
#        byte 2: parameter count
#   +40  pointer to the parameter descriptor array, stride 16:
#        (name pointer, type code)
#   +48  the evaluation routine
FUNCTION_TABLE_RVA = 0x1e45510
FUNCTION_STRIDE = 0x50
FUNCTION_MAX = 1400
PARAM_STRIDE = 16

# Parameter type codes that are PLAIN VALUES rather than FormIDs. Load-order
# shifting one of these corrupts it: Skyrim indexes several straight into an
# array, so a shifted GetBaseActorValue param reads millions of entries past
# the actor-value table and crashes the process.
VALUE_PARAM_TYPES = {
    0x00,  # String / Filename
    0x01,  # Integer (Count, Cell X, Cell Y, ...)
    0x02,  # Float
    0x05,  # Actor Value
    0x08,  # Axis
    0x0A,  # Animation Group
    0x12,  # Sex
    0x16,  # Variable Name
    0x17,  # Stage
    0x1C,  # Crime Type
    0x20,  # Form Type
    0x29,  # Miscellaneous Stat
    0x36,  # Alignment
    0x37,  # EquipType
    0x3A,  # CriticalStage
    0x44,  # Casting Source
    0x46,  # Ward State
    0x48,  # PackageData (Numeric)
    0x49,  # Furniture Anim Type
    0x4A,  # Furniture Entry Type
    0x4C,  # VM Variable Name
    0x50,  # Skill Action
    # Selectors and indices rather than records: an event function/member is an
    # enum chosen in the CK, a quest alias is an index into the quest's alias
    # array, and package data is addressed by index. None of them are forms, so
    # none may be load-order shifted.
    0x2E,  # Event Function -- selects which event, not a form
    0x3F,  # QuestAlias
    0x47,  # PackageData (Possibly Null)
    0x4E,  # PackageData (Location)
}


class Exe:
    def __init__(self, path):
        self.path = path
        pe = pefile.PE(path, fast_load=True)
        self.base = pe.OPTIONAL_HEADER.ImageBase
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
        """Read a NUL-terminated ASCII string at a virtual address."""
        if va < self.base:
            return None
        off = self.rva_to_off(va - self.base)
        if off is None:
            return None
        end = self.data.find(b'\0', off)
        try:
            return self.data[off:end].decode('ascii')
        except UnicodeDecodeError:
            return None

    def entry(self, rva, size):
        off = self.rva_to_off(rva)
        if off is None:
            return b''
        return self.data[off:off + size]

    def is_packed(self):
        """Steam builds are Denuvo/Steam-encrypted; .text entropy hits 8.00."""
        import collections
        import math
        off = self.rva_to_off(0x1000)
        chunk = self.data[off:off + 0x100000]
        counts = collections.Counter(chunk)
        n = len(chunk)
        entropy = -sum(c / n * math.log2(c / n) for c in counts.values())
        return entropy > 7.5, entropy


def read_categories(exe):
    out = []
    for i in range(CATEGORY_COUNT):
        e = exe.entry(CATEGORY_TABLE_RVA + i * CATEGORY_STRIDE, CATEGORY_STRIDE)
        name_va, = struct.unpack('<Q', e[:8])
        first, cat_id = struct.unpack('<II', e[8:16])
        out.append({'id': cat_id, 'name': exe.cstring(name_va),
                    'first_subtype': first})
    return out


def read_subtypes(exe):
    out = []
    for i in range(SUBTYPE_MAX):
        e = exe.entry(SUBTYPE_TABLE_RVA + i * SUBTYPE_STRIDE, SUBTYPE_STRIDE)
        if len(e) < SUBTYPE_STRIDE:
            break
        name_va, = struct.unpack('<Q', e[:8])
        name = exe.cstring(name_va)
        if name is None:
            break
        category, = struct.unpack('<I', e[8:12])
        tag = e[12:16].decode('latin1')
        number, = struct.unpack('<I', e[16:20])
        # offset 20: engine flag byte. 1 == topic can be offered to the player
        # in the dialogue menu; 0 == engine-driven only (barks, combat, etc.)
        player_selectable = e[20] == 1
        # offset 21 marks the three "Custom" rows, one per player-facing
        # category, which are the rows authored topics actually attach to.
        is_custom_root = e[21] == 1
        out.append({'index': i, 'name': name, 'category': category,
                    'tag': tag, 'number': number,
                    'player_selectable': player_selectable,
                    'is_custom_root': is_custom_root})
    return out


def read_functions(exe):
    """Every script/condition function, with real parameter types.

    This is what tools/gen_ctda_param_types.py approximates from xEdit's Pascal
    source. Reading it from the engine removes the guesswork: a parameter is a
    FormID only if its type code is absent from VALUE_PARAM_TYPES.
    """
    out = []
    for i in range(FUNCTION_MAX):
        e = exe.entry(FUNCTION_TABLE_RVA + i * FUNCTION_STRIDE, FUNCTION_STRIDE)
        if len(e) < FUNCTION_STRIDE:
            break
        name_va, = struct.unpack('<Q', e[:8])
        name = exe.cstring(name_va)
        if name is None:
            break
        opcode, = struct.unpack('<H', e[16:18])
        takes_reference = e[32] == 1
        nparams = e[34]
        params_va, = struct.unpack('<Q', e[40:48])

        params = []
        if params_va > exe.base and nparams:
            prva = params_va - exe.base
            for k in range(nparams):
                pe = exe.entry(prva + k * PARAM_STRIDE, 12)
                if len(pe) < 12:
                    break
                pname_va, ptype = struct.unpack('<QI', pe)
                params.append({'name': exe.cstring(pname_va), 'type': ptype,
                               'is_formid': ptype not in VALUE_PARAM_TYPES})
        # A CTDA stores the condition function as `opcode - 0x1000`; the engine
        # adds the base back before looking the function up. Verified across the
        # table: GetStage 0x103A -> 58, GetIsID 0x1048 -> 72,
        # GetGlobalValue 0x104A -> 74, GetFactionRank 0x1049 -> 73.
        ctda_index = opcode - 0x1000 if opcode >= 0x1000 else None
        out.append({'index': i, 'name': name, 'opcode': opcode,
                    'ctda_index': ctda_index,
                    'takes_reference': takes_reference, 'params': params})
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', default=DEFAULT_EXE)
    ap.add_argument('--categories', action='store_true')
    ap.add_argument('--subtypes', action='store_true')
    ap.add_argument('--functions', metavar='NAME', nargs='?', const='',
                    help='list condition functions, optionally filtered')
    ap.add_argument('--json', help='write all tables to this JSON path')
    args = ap.parse_args()

    exe = Exe(args.exe)
    packed, entropy = exe.is_packed()
    if packed:
        sys.exit(f'.text entropy {entropy:.2f} -- this build is DRM-packed, '
                 'its tables cannot be read. Use the GOG/Anniversary exe.')

    cats = read_categories(exe)
    subs = read_subtypes(exe)
    funcs = read_functions(exe)

    show_default = not (args.subtypes or args.json or args.categories
                        or args.functions is not None)

    if args.functions is not None:
        needle = args.functions.lower()
        shown = [f for f in funcs if needle in f['name'].lower()]
        print(f'{len(shown)} of {len(funcs)} condition functions:')
        for f in shown:
            ref = ' [reference]' if f['takes_reference'] else ''
            print(f'  {f["index"]:>4} 0x{f["opcode"]:04X}  {f["name"]}{ref}')
            for k, prm in enumerate(f['params']):
                kind = 'FormID' if prm['is_formid'] else 'value'
                print(f'        param{k}: {prm["name"]} '
                      f'(type 0x{prm["type"]:02X}, {kind})')
        print()

    if args.categories or show_default:
        print(f'{len(cats)} dialogue categories:')
        for c in cats:
            print(f'  {c["id"]}  {c["name"]:<16} '
                  f'subtypes start at {c["first_subtype"]}')
        print()

    if args.subtypes or show_default:
        by_id = {c['id']: c['name'] for c in cats}
        print(f'{len(subs)} dialogue subtypes:')
        for s in subs:
            marks = []
            if s['player_selectable']:
                marks.append('player-selectable')
            if s['is_custom_root']:
                marks.append('custom-root')
            print(f'  {s["number"]:>3}  {s["tag"]}  {s["name"]:<30} '
                  f'{by_id.get(s["category"], "?"):<16} '
                  f'{" ".join(marks)}')

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump({'source': exe.path, 'categories': cats,
                       'subtypes': subs, 'functions': funcs}, f, indent=2)
        print(f'wrote {args.json}: {len(cats)} categories, '
              f'{len(subs)} subtypes, {len(funcs)} functions')


if __name__ == '__main__':
    main()
