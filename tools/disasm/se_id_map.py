"""Derive the SE-era (1.5.x) Address Library id for each AE-era (1.6+) id.

🛑 INCOMPLETE -- NOTHING CONSUMES THIS YET, AND 1.5.x IS NOT SUPPORTED.
`VersionDb::Load` still refuses every pre-AE runtime. This tool exists so the
derivation is recorded and rerunnable; wiring it up is future work, and it
CANNOT be wired up as it stands. It maps 69 of the 84 ids a plugin looks up.
The other 15 -- the whole menu stack (MenuManager, GFxLoader, Scaleform, the
UI manager), the Quest stage/objective calls, BSFixedString's ctor and
Game.GetFormFromFile -- are internal engine functions with no Papyrus
registration and no RTTI name, so no anchor here reaches them. Shipping the
69 alone would resolve the menu ids to wrong addresses, which is worse than
refusing the build.
See: docs/reference/address_library_formats.md#deriving-the-se-ids

Why a mapping is needed at all: AE renumbered every id, because functions were
inserted and removed. Both databases are correct; they are different
dictionaries. Measured on 1.5.97, all 69 checkable ids are PRESENT and every one
names a different function.

The mapping is DERIVED, never hand-written, so an id added to `ids.h` costs a
rerun rather than a research session. Each AE id is anchored to something both
builds name identically, the anchor gives the function's real 1.5.x address,
and the 1.5.x table converts that back to an SE id:

    papyrus  the script's own Papyrus registration site names the native
    vtable   the class's RTTI type descriptor names its primary vtable
    slot     a vtable slot's contents, once the vtable is anchored

Every anchor is checked against the AE build before it is trusted on SE, so a
shifted anchor is reported rather than silently producing a wrong id.

Usage:
    python tools/disasm/se_id_map.py               # derive and verify
    python tools/disasm/se_id_map.py --write       # also write ids_se.h
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

from tools.disasm import address_lib
from tools.disasm.skyrim_disasm import Binary
from tools.script import papyrus_native_locate as locate

#: The build each generation's ids were authored against.
AE_VERSION = '1.6.659'
SE_VERSION = '1.5.97'

#: Where every unpacked per-build exe lives.
DEPOT = (r'C:/Program Files (x86)/Steam/steamapps/content/app_489830'
         r'/depot_489833')

#: The unpacked binaries the anchors are read from.
AE_EXE = '%s/SkyrimSE.%s.unpacked.exe' % (DEPOT, AE_VERSION)
SE_EXE = '%s/SkyrimSE.%s.unpacked.exe' % (DEPOT, SE_VERSION)

#: The header declaring AE ids, and the generated header beside it.
PLUGIN = Path('tes_runtime/morrowind/plugin')
HEADER = PLUGIN / 'ids.h'
GENERATED = PLUGIN / 'ids_se.h'

#: The sources whose `Native<>` calls pair a Papyrus name with an id.
SOURCES = tuple(PLUGIN / ('game_calls%s.cpp' % part)
                for part in ('', '_move', '_ai', '_query'))

#: TESObjectREFR::Activate's byte offset in a form vtable.
ACTIVATE_SLOT = 0x1b8

_DECL = re.compile(r'constexpr\s+std::uint64_t\s+(\w+)\s*=\s*(\d+)\s*;')
_NATIVE = re.compile(r'Native<\w+>\(\s*"(\w+)\.(\w+)",\s*ids::(\w+)\)')
_ROW = re.compile(r'\{"(\w+)",\s*(\d+),\s*(\d+)\}')
#: Every id that reaches a database lookup; the rest are plain constants.
_USE = re.compile(r'(?:Resolve\([^;]*?|Native<[^>]+>\([^;]*?)ids::(\w+)', re.S)


def declared_ids(header: Path) -> dict:
    """`{constant name: AE id}` for every id the header declares."""
    return {n: int(v) for n, v in _DECL.findall(header.read_text(encoding='utf-8'))}


def looked_up(plugin: Path) -> set:
    """Constant names reaching `Resolve`/`Native<>`, so needing a database."""
    names = set()
    for cpp in sorted(plugin.glob('*.cpp')):
        names |= set(_USE.findall(cpp.read_text(encoding='utf-8', errors='replace')))
    return names


def papyrus_claims(sources) -> list:
    """`(script, function, constant)` for every `Native<>` call."""
    out = []
    for source in sources:
        out += _NATIVE.findall(Path(source).read_text(encoding='utf-8', errors='replace'))
    return out


def activate_rows(header: Path) -> list:
    """`(class, vtable id, activate id)` per scriptable form type."""
    return [(n, int(v), int(a))
            for n, v, a in _ROW.findall(header.read_text(encoding='utf-8'))]


class Build:
    """One build's binary and id table, with a reverse RVA lookup."""

    def __init__(self, version: str, exe: str):
        """Loads the binary and that build's Address Library database."""
        self.version = version
        self.binary = Binary(exe)
        self.table = address_lib.load(address_lib.find_versionlib(version))
        self.by_rva = {}
        for stable, rva in self.table.items():
            self.by_rva.setdefault(rva, stable)

    def id_at(self, rva: int):
        """The stable id naming `rva`, or None when the build omits it."""
        return self.by_rva.get(rva)

    def primary_vtable(self, class_name: str):
        """The class's primary vtable -- the first its RTTI descriptor names."""
        vts = self.binary.vtables_for(class_name)
        return vts[0] if vts else None

    def slot_target(self, vtable: int, slot: int):
        """The function RVA a vtable slot holds."""
        off = self.binary.rva_to_off(vtable + slot)
        if off is None:
            return None
        va = struct.unpack_from('<Q', self.binary.data, off)[0]
        return va - self.binary.base if va > self.binary.base else None

    def sites_for(self, names: set) -> dict:
        """`lea_sites_for` over every string in `names`."""
        return locate.lea_sites_for(
            self.binary,
            {r for n in names for r in locate.find_strings(self.binary, n)})

    def candidates(self, script: str, name: str, sites: dict) -> list:
        """Sorted lea candidates at `script`'s registration of `name`."""
        for owner, _site, cands in locate.registrations(self.binary, name, sites):
            if owner == script:
                return sorted(cands)
        return []


def map_papyrus(ae: Build, se: Build, claims: list, ids: dict) -> tuple:
    """`({constant: SE id}, [(constant, what, why)])` for Papyrus natives.

    The AE id identifies WHICH candidate at the registration is the native;
    the same position on the SE build is that build's native.
    """
    names = {fn for _s, fn, _c in claims}
    ae_sites, se_sites = ae.sites_for(names), se.sites_for(names)
    mapped, failed = {}, []
    for script, fn, const in claims:
        label = '%s.%s' % (script, fn)
        ae_rva = ae.table.get(ids.get(const, -1), 0)
        ae_cands = ae.candidates(script, fn, ae_sites)
        se_cands = se.candidates(script, fn, se_sites)
        if ae_rva not in ae_cands:
            failed.append((const, label, 'AE id is not at its own registration'))
        elif ae_cands.index(ae_rva) >= len(se_cands):
            failed.append((const, label, 'no matching SE candidate'))
        else:
            se_id = se.id_at(se_cands[ae_cands.index(ae_rva)])
            if se_id is None:
                failed.append((const, label, 'SE address has no id'))
            else:
                mapped[const] = se_id
    return mapped, failed


def map_vtable_row(ae: Build, se: Build, row: tuple, by_value: dict) -> tuple:
    """`({constant: SE id}, [(class, what, why)])` for one form type.

    Checks each anchor against the AE build first: the primary vtable must BE
    the AE id and the slot must hold the AE Activate, or the row is reported.
    """
    class_name, ae_vt, ae_act = row
    mapped, failed = {}, []
    ae_vtable, se_vtable = ae.primary_vtable(class_name), se.primary_vtable(class_name)
    if ae_vtable is None or se_vtable is None:
        return mapped, [(class_name, 'vtable', 'no RTTI vtable')]
    if ae.id_at(ae_vtable) != ae_vt:
        return mapped, [(class_name, 'vtable', 'AE primary vtable is id %s, not %d'
                         % (ae.id_at(ae_vtable), ae_vt))]
    se_id = se.id_at(se_vtable)
    if se_id is None:
        failed.append((class_name, 'vtable', 'SE vtable has no id'))
    elif by_value.get(ae_vt):
        mapped[by_value[ae_vt]] = se_id

    ae_target = ae.slot_target(ae_vtable, ACTIVATE_SLOT)
    se_target = se.slot_target(se_vtable, ACTIVATE_SLOT)
    if ae_target is None or se_target is None:
        failed.append((class_name, 'activate', 'slot unreadable'))
    elif ae.id_at(ae_target) != ae_act:
        failed.append((class_name, 'activate', 'AE slot holds id %s, not %d'
                       % (ae.id_at(ae_target), ae_act)))
    else:
        se_id = se.id_at(se_target)
        if se_id is None:
            failed.append((class_name, 'activate', 'SE address has no id'))
        elif by_value.get(ae_act):
            mapped[by_value[ae_act]] = se_id
    return mapped, failed


def verify(se: Build, mapped: dict, claims: list) -> list:
    """`[(constant, why)]` for each mapped Papyrus id that is NOT its native.

    The check `stable_id_check --identity` runs, against the SE build.
    """
    names = {fn for _s, fn, _c in claims}
    sites = se.sites_for(names)
    wrong = []
    for script, fn, const in claims:
        if const not in mapped:
            continue
        rva = se.table.get(mapped[const], 0)
        owners = [o for o, _s, cands in locate.registrations(se.binary, fn, sites)
                  if rva in cands]
        if script not in owners:
            wrong.append((const, 'SE id %d is not %s.%s'
                          % (mapped[const], script, fn)))
    return wrong


def render(mapped: dict, ids: dict, unmapped: list) -> str:
    """The generated header's text."""
    lines = [
        '// GENERATED by tools/disasm/se_id_map.py -- DO NOT EDIT.',
        '//',
        '// 🛑 INCOMPLETE: %d of the ids a plugin looks up have no anchor and'
        % len(unmapped),
        '// are ABSENT below. Resolving them through this table would give a',
        '// WRONG address, so nothing includes this header yet and 1.5.x stays',
        '// refused by VersionDb::Load.',
        '// See: docs/reference/address_library_formats.md#deriving-the-se-ids',
        '//',
        '// Missing: %s' % ', '.join(unmapped),
        '',
        '#pragma once',
        '',
        '#include <cstdint>',
        '',
        'namespace tesruntime::mw::ids {',
        '',
        '//: AE id -> the SE-era id naming the same function.',
        'struct SeId { std::uint64_t ae, se; };',
        'constexpr SeId kSeIds[] = {',
    ]
    for const, se_id in sorted(mapped.items(), key=lambda kv: ids[kv[0]]):
        lines.append('    {%d, %d},  // %s' % (ids[const], se_id, const))
    lines += ['};', '', '}  // namespace tesruntime::mw::ids', '']
    return '\n'.join(lines)


def parse_args():
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--write', action='store_true',
                        help='write the generated header as well as checking')
    parser.add_argument('--ae-exe', default=AE_EXE, help='unpacked %s exe' % AE_VERSION)
    parser.add_argument('--se-exe', default=SE_EXE, help='unpacked %s exe' % SE_VERSION)
    parser.add_argument('--header', default=str(HEADER), help='the AE ids header')
    parser.add_argument('--out', default=str(GENERATED), help='the generated header')
    return parser.parse_args()


def main() -> int:
    """Derive, verify and optionally write; non-zero when an id is unmapped."""
    args = parse_args()
    header = Path(args.header)
    ids = declared_ids(header)
    needed = {n: v for n, v in ids.items() if n in looked_up(header.parent)}
    by_value = {v: n for n, v in ids.items()}
    claims = papyrus_claims(SOURCES)

    ae, se = Build(AE_VERSION, args.ae_exe), Build(SE_VERSION, args.se_exe)
    mapped, failed = map_papyrus(ae, se, claims, ids)
    for row in activate_rows(header):
        row_mapped, row_failed = map_vtable_row(ae, se, row, by_value)
        mapped.update(row_mapped)
        failed += row_failed

    wrong = verify(se, mapped, claims)
    unmapped = sorted(set(needed) - set(mapped))

    print('%d id(s) need a database; mapped %d, unmapped %d'
          % (len(needed), len(mapped), len(unmapped)))
    for const, what, why in failed:
        print('   FAILED   %-28s %s: %s' % (const, what, why))
    for const, why in wrong:
        print('   WRONG    %-28s %s' % (const, why))
    for const in unmapped:
        print('   NO ANCHOR %-27s (AE id %d)' % (const, needed[const]))

    if args.write and not wrong:
        Path(args.out).write_text(render(mapped, ids, unmapped), encoding='utf-8')
        print('wrote %s' % args.out)
    if unmapped:
        print('\n%d id(s) have no anchor, so this map CANNOT be used yet -- '
              'resolving them would give wrong addresses.' % len(unmapped))
    return 1 if (wrong or unmapped) else 0


if __name__ == '__main__':
    sys.exit(main())
