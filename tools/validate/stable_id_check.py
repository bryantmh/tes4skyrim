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

Exit code is non-zero when any id is missing anywhere, so it can gate a build.
See: docs/commentary/morrowind_runtime.md#the-angle-getters-have-no-id
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from tools.disasm import address_lib

#: Every runtime header that declares stable ids.
DEFAULT_HEADERS = (
    'tes_runtime/morrowind_runtime/plugin/ids.h',
    'tes_runtime/plugin/ids.h',
)

#: `constexpr std::uint64_t kName = 12345;`
_DECL = re.compile(r'constexpr\s+std::uint64_t\s+(\w+)\s*=\s*(\d+)\s*;')


def declared_ids(header: Path) -> dict:
    """`{constant name: stable id}` from one header."""
    text = header.read_text(encoding='utf-8', errors='replace')
    return {name: int(value) for name, value in _DECL.findall(text)}


def versionlibs(extra: str | None) -> list:
    """Every installed versionlib, oldest build first."""
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


def main() -> int:
    """Report ids missing from any build; non-zero when any is."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--header', action='append',
                        help='a header declaring stable ids (repeatable)')
    parser.add_argument('--dir', help='extra directory holding versionlibs')
    parser.add_argument('--played', default='1.6.1170',
                        help='the build the user plays, flagged in the report')
    args = parser.parse_args()

    headers = [Path(h) for h in (args.header or DEFAULT_HEADERS)]
    ids = {}
    for header in headers:
        if header.is_file():
            ids.update(declared_ids(header))
    if not ids:
        print('no stable ids found in: %s'
              % ', '.join(str(h) for h in headers))
        return 1

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
