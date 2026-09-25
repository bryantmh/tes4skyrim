"""Does every Address Library id a runtime plugin uses exist on every build?

Why this exists: an id can be present on the build we DISASSEMBLE (GOG
1.6.659) and absent on the build the user PLAYS. `ObjectReference.GetAngleX/Y/Z`
(56162-56164) are exactly that -- three-instruction leaf functions the database
stopped covering after 1.6.659 -- and `Resolve` answering 0 is silent: the hook
is simply never called, so every rotation read 0 and four commands did nothing
while the log said only "UNRESOLVED".

A per-session check of the ids that session ADDED cannot find this, because the
broken ids were years old. So this scrapes every `constexpr std::uint64_t` out
of the header and tests all of them against all installed versionlibs.

Usage:
    python tools/validate/stable_id_check.py
    python tools/validate/stable_id_check.py --header <ids.h> --played 1.6.1170
    python tools/validate/stable_id_check.py --dir <folder with versionlibs>

    python tools/validate/stable_id_check.py --identity

`--identity` answers a second question: is each id the function its `Native<>`
call SAYS it is. Several Papyrus scripts register one function name, and the id
of the wrong one resolves everywhere and crashes at runtime.

Exit code is non-zero when any id is missing anywhere, so it can gate a build.
See: docs/commentary/morrowind_runtime.md#the-angle-getters-have-no-id
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from tools.disasm import address_lib
from tools.disasm.skyrim_disasm import Binary
from tools.script import papyrus_native_locate as locate

#: Every runtime header that declares stable ids: each plugin's ids.h and common/engine_ids.h.
DEFAULT_HEADERS = tuple(sorted(str(p) for p in Path('tes_runtime').rglob('*ids.h')))

#: The sources whose `Native<>` calls pair a Papyrus name with an id.
DEFAULT_SOURCES = tuple(
    'tes_runtime/morrowind/plugin/game_calls%s.cpp' % part
    for part in ('', '_move', '_ai', '_query'))

#: The unpacked build the ids were found in, and its version.
IDENTITY_EXE = ('C:/Program Files (x86)/Steam/steamapps/content/app_489830'
                '/depot_489833/SkyrimSE.1.6.659.unpacked.exe')
IDENTITY_VERSION = '1.6.659'

#: `Native<Fn>("Script.Function", ids::kName)`; a `::` name is not Papyrus.
_NATIVE = re.compile(r'Native<\w+>\(\s*"(\w+)\.(\w+)",\s*ids::(\w+)\)')

#: `constexpr std::uint64_t kName = 12345;`
_DECL = re.compile(r'constexpr\s+std::uint64_t\s+(\w+)\s*=\s*(\d+)\s*;')


def declared_ids(header: Path) -> dict:
    """`{constant name: stable id}` from one header."""
    text = header.read_text(encoding='utf-8', errors='replace')
    return {name: int(value) for name, value in _DECL.findall(text)}


def versionlibs(extra: str | None) -> list:
    """Every installed AE-era database, oldest build first.

    Only `versionlib-*.bin` (1.6+). The pre-AE `version-*.bin` files are a
    different id generation, checked through their own derived map rather than
    against the AE ids here.
    See: docs/reference/address_library_formats.md#two-id-generations
    """
    folders = [Path(extra)] if extra else list(address_lib.DEFAULT_DIRS)
    found = []
    for folder in folders:
        if folder.is_dir():
            found.extend(sorted(folder.glob('versionlib-*.bin')))
    return found


def missing_by_build(ids: dict, libs: list) -> dict:
    """`{versionlib name: [constant names absent from it]}`."""
    missing = {}
    for lib in libs:
        table = address_lib.load(lib)
        gone = sorted(name for name, i in ids.items() if i not in table)
        if gone:
            missing[lib.name] = gone
    return missing


def wrong_identities(sources: list, ids: dict, exe: str,
                     version: str = IDENTITY_VERSION) -> list:
    """`(constant, claimed Script.Function, scripts that DO own the address)`
    for every `Native<>` whose id is not that script's registration.

    `exe` must be the unpacked binary for `version`, since the ids are read
    through that build's database.
    """
    claims = [claim for source in sources
              for claim in _NATIVE.findall(
                  Path(source).read_text(encoding='utf-8', errors='replace'))]
    binary = Binary(exe)
    table = address_lib.load(address_lib.find_versionlib(version))
    strings = {rva for _s, name, _c in claims
               for rva in locate.find_strings(binary, name)}
    sites = locate.lea_sites_for(binary, strings)
    wrong = []
    for script, name, constant in claims:
        rva = table.get(ids.get(constant, -1))
        owners = [owner for owner, _site, callbacks
                  in locate.registrations(binary, name, sites)
                  if rva in callbacks]
        if script not in owners:
            wrong.append((constant, f'{script}.{name}', owners))
    return wrong


def report_identities(sources: list, ids: dict, exe: str,
                      version: str = IDENTITY_VERSION) -> int:
    """Print every misidentified native; non-zero when there is one."""
    wrong = wrong_identities(sources, ids, exe, version)
    print('identity on %s (%s)' % (version, exe))
    for constant, claimed, owners in wrong:
        print('   WRONG FUNCTION %-26s claims %s, address belongs to %s'
              % (constant, claimed, ', '.join(owners) or 'no registration'))
    print('%s: %d misidentified native(s)'
          % ('FAIL' if wrong else 'OK', len(wrong)))
    return 1 if wrong else 0


def parse_args():
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--header', action='append',
                        help='a header declaring stable ids (repeatable)')
    parser.add_argument('--dir', help='extra directory holding versionlibs')
    parser.add_argument('--identity', action='store_true',
                        help='check each Native<> id IS the function it names')
    parser.add_argument('--source', action='append',
                        help='a source holding Native<> calls (repeatable)')
    parser.add_argument('--exe', default=IDENTITY_EXE,
                        help='the unpacked exe to read registrations from')
    parser.add_argument('--identity-version', default=IDENTITY_VERSION,
                        help='the build --exe is, whose database the ids are '
                             'read through (default %s)' % IDENTITY_VERSION)
    parser.add_argument('--played', default='1.6.1170',
                        help='the build the user plays, flagged in the report')
    return parser.parse_args()


def main() -> int:
    """Report ids missing from any build; non-zero when any is."""
    args = parse_args()
    headers = [Path(h) for h in (args.header or DEFAULT_HEADERS)]
    ids = {}
    for header in headers:
        if header.is_file():
            ids.update(declared_ids(header))
    if not ids:
        print('no stable ids found in: %s'
              % ', '.join(str(h) for h in headers))
        return 1

    if args.identity:
        return report_identities(args.source or list(DEFAULT_SOURCES), ids,
                                 args.exe, args.identity_version)

    libs = versionlibs(args.dir)
    if not libs:
        print('no versionlib-*.bin found')
        return 1

    played = args.played.replace('.', '-')
    missing = missing_by_build(ids, libs)
    print('%d stable id(s) across %d header(s), %d versionlib(s)'
          % (len(ids), len(headers), len(libs)))
    if not missing:
        print('OK: every id resolves on every build')
        return 0
    for name in sorted(missing):
        mark = '   <-- THE BUILD BEING PLAYED' if played in name else ''
        print('\n%s%s' % (name, mark))
        for gone in missing[name]:
            print('   MISSING %-30s (id %d)' % (gone, ids[gone]))
    print('\n%d build(s) miss at least one id -- a Resolve of 0 is SILENT, so '
          'read the field directly or find another native' % len(missing))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
