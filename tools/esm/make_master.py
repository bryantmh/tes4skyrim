#!/usr/bin/env python3
"""Set (or clear) the ESM flag on a converted plugin, in place.

WHY: a plugin that is not flagged ESM has EVERY reference it contains treated
by the engine as persistent -- always active, regardless of where the player
is -- and every one of those counts against the engine's reference handle cap
of 2**20 = 1,048,576. Flagging a worldspace plugin ESM lets its temporary
references load on demand per cell instead, which is the only way a plugin
holding hundreds of thousands of REFRs can coexist with vanilla under that cap.
Measured on this project: TWMP_Valenwood_Elsweyr 818,294 REFR + Tamriel 554 +
ElsweyrAnequina 161,541 + ElsweyrPelletine 45,066 = 1,025,455 references, or
97.9% of the cap from four plugins before Skyrim.esm contributes anything.
The failure signature is a main-menu hang with no crash and no log, because the
handle table is built while the files are parsed.

The flag is one bit at a fixed offset -- byte 8 of the TES4 header record, bit
0x00000001 -- so this rewrites 4 bytes in place and touches nothing else. No
record is reserialized, so FormIDs cannot drift and the file stays otherwise
byte-identical.

FILENAMES ARE NOT CHANGED, deliberately. The engine keys master-ness off this
flag, not the extension, while a dependent plugin's MAST entries name its
masters by their EXACT filename. Renaming Tamriel.esp to Tamriel.esm would
invalidate the MAST entry in every plugin that masters it. An ESM-flagged
.esp is legal and loads as a master.

MASTER ORDERING: a master must load before its dependents, so an ESM may not
master a plain ESP. When a dependency chain is converted, EVERY file in it
must be flagged. This tool reads each plugin's MAST list and refuses (exit 2)
if flagging the requested set would leave an ESM mastering an unflagged ESP,
naming the file that is missing.

Usage:
  python tools/esm/make_master.py Tamriel.esp ElsweyrAnequina.esp
  python tools/esm/make_master.py --all-dependents Tamriel.esp
  python tools/esm/make_master.py --clear Tamriel.esp        # revert to ESP
  python tools/esm/make_master.py --check Tamriel.esp        # report only
  python tools/esm/make_master.py --output-dir output Tamriel.esp
"""
import argparse
import os
import struct
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from output_layout import forget_summary, paths

FLAG_ESM = 0x00000001
FLAG_ESL = 0x00000200
REC_HDR = 24
FLAGS_OFFSET = 8          # byte offset of the flags dword in the TES4 header

# Engine reference handle cap: 2**20 handles. Every reference in a non-ESM
# plugin is persistent and permanently occupies one.
HANDLE_CAP = 1 << 20


#: The config key holding the plugins the user chose to flag ESM; a rebuilt plugin on it is flagged again.
CONFIG_KEY = "masterPlugins"


def reflag_if_chosen(path, config: dict) -> bool:
    """Flag the plugin at `path` ESM when its name is on the config's chosen list; True when it was.

    See: docs/commentary/core_gui.md#load-order
    """
    chosen = {name.lower() for name in config.get(CONFIG_KEY, ())}
    if os.path.basename(path).lower() not in chosen or not os.path.isfile(path):
        return False
    set_flag(path, True)
    return True


def read_header(path):
    """Return (flags, [master filenames]) from a plugin's TES4 header."""
    with open(path, 'rb') as fh:
        hdr = fh.read(REC_HDR)
        if len(hdr) < REC_HDR:
            raise ValueError(f'{path}: truncated, not a plugin')
        sig, size, flags = struct.unpack('<4sII', hdr[:12])
        if sig != b'TES4':
            raise ValueError(f'{path}: first record is {sig!r}, not TES4')
        body = fh.read(size)
    if len(body) < size:
        raise ValueError(f'{path}: header record truncated')

    masters, off = [], 0
    while off + 6 <= len(body):
        sub, length = struct.unpack('<4sH', body[off:off + 6])
        off += 6
        if sub == b'MAST':
            masters.append(body[off:off + length].rstrip(b'\x00').decode('cp1252'))
        off += length
    return flags, masters


def set_flag(path, enable):
    """Set or clear the ESM bit in place, so Install & play rereads the plugin's mod. Returns (old_flags, new_flags).

    See: docs/commentary/core_gui.md#install-summaries
    """
    flags, _ = read_header(path)
    new = (flags | FLAG_ESM) if enable else (flags & ~FLAG_ESM)
    if new != flags:
        with open(path, 'r+b') as fh:
            fh.seek(FLAGS_OFFSET)
            fh.write(struct.pack('<I', new))
        forget_summary(os.path.dirname(os.path.abspath(path)))
    return flags, new


def resolve(name, output_dir):
    """Map a plugin name to its built path in the output tree."""
    if os.path.isfile(name):
        return name
    candidate = str(paths(name, out_root=output_dir).esm)
    if os.path.isfile(candidate):
        return candidate
    # Last resort: a plugin written as a bare FILE at the output root (a
    # plugin with no asset phase used to land there). Not a folder lookup, so
    # the plugin-name-onto-a-root rule does not apply.
    candidate = os.path.join(output_dir, name)   # noqa: plugin-path (bare-file fallback)
    if os.path.isfile(candidate):
        return candidate
    raise FileNotFoundError(f'cannot find plugin {name!r} under {output_dir}/')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plugins', nargs='+', help='plugin filenames or paths')
    ap.add_argument('--output-dir', default='output',
                    help='where built plugins live (default: output)')
    ap.add_argument('--clear', action='store_true',
                    help='clear the ESM flag instead of setting it')
    ap.add_argument('--check', action='store_true',
                    help='report current state only, write nothing')
    ap.add_argument('--force', action='store_true',
                    help='apply even if the master-ordering check fails')
    args = ap.parse_args()

    enable = not args.clear

    try:
        paths = {p: resolve(p, args.output_dir) for p in args.plugins}
    except FileNotFoundError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2

    state = {}
    for name, path in paths.items():
        try:
            flags, masters = read_header(path)
        except ValueError as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            return 2
        state[name] = (flags, masters, path)

    print(f'{"plugin":<34} {"size":>13}  flags       ESM  masters')
    for name, (flags, masters, path) in state.items():
        size = os.path.getsize(path)
        print(f'{name:<34} {size:>13,}  0x{flags:08X}  '
              f'{"yes" if flags & FLAG_ESM else "no ":<4} {len(masters)}')
        for m in masters:
            print(f'{"":<36}   master: {m}')

    # Master-ordering check: every master of a to-be-ESM plugin must itself be
    # an ESM (already flagged, or in this same batch). Vanilla .esm masters
    # are fine by definition.
    #
    # This is a HARD ERROR, not a warning. An ESM that masters a plain ESP is
    # an invalid load order: the game must load every master before the plugin
    # that needs it, and it cannot place an ESP ahead of an ESM. Flagging
    # ElsweyrPelletine.esp on its own would produce exactly that -- an ESM
    # whose three masters are all still ESPs -- so the tool names every missing
    # file and writes nothing.
    if enable and not args.check:
        selected = set(paths)
        unflagged, missing = [], []
        for name, (flags, masters, _path) in state.items():
            for m in masters:
                if m.lower().endswith('.esm') or m in selected:
                    continue
                try:
                    mflags, _ = read_header(resolve(m, args.output_dir))
                except FileNotFoundError:
                    missing.append((name, m))
                    continue
                except ValueError as exc:
                    missing.append((name, f'{m} ({exc})'))
                    continue
                if not (mflags & FLAG_ESM):
                    unflagged.append((name, m))

        if unflagged or missing:
            print('\n' + '=' * 72)
            print('ERROR: this would produce an INVALID load order.')
            print('=' * 72)
            print('An ESM may not master a plain ESP -- every master must load '
                  'before\nthe plugin that needs it, and an ESP cannot be '
                  'ordered ahead of an ESM.\n')
            if unflagged:
                print('These masters are still plain ESPs:')
                for name, m in unflagged:
                    print(f'  {name}')
                    print(f'      masters {m}  <-- not flagged ESM')
            if missing:
                print('\nThese masters could not be found:')
                for name, m in missing:
                    print(f'  {name}')
                    print(f'      masters {m}  <-- missing from '
                          f'{args.output_dir}/')
            need = {m for _n, m in unflagged}
            if need:
                # Order the suggestion so masters really do come first,
                # otherwise the "lowest first" advice contradicts itself.
                # Depth = how many of the other named plugins a file masters,
                # transitively; a plugin with fewer dependencies loads earlier.
                chain = sorted(need | selected)
                mast = {}
                for n in chain:
                    try:
                        _f, ms = read_header(resolve(n, args.output_dir))
                    except (FileNotFoundError, ValueError):
                        ms = []
                    mast[n] = [m for m in ms if m in set(chain)]

                ordered, placed = [], set()

                def _visit(n, stack=()):
                    if n in placed or n in stack:
                        return
                    for m in mast.get(n, ()):
                        _visit(m, stack + (n,))
                    placed.add(n)
                    ordered.append(n)

                for n in chain:
                    _visit(n)
                print('\nFix: run again with the whole chain, lowest first:')
                print(f'  python tools/esm/make_master.py {" ".join(ordered)}')
            if not args.force:
                print('\nRefusing to write -- no file was modified.')
                return 2
            print('\n--force given, writing anyway. The load order will be '
                  'invalid until\nthe files above are also flagged.')

    if args.check:
        return 0

    print()
    changed = 0
    locked = []
    for name, (_flags, _masters, path) in state.items():
        try:
            old, new = set_flag(path, enable)
        except PermissionError:
            # Almost always the file is open in xEdit/the CK, or the game is
            # running. Report it plainly -- a traceback here reads as a bug in
            # the tool when it is a lock on the user's side.
            locked.append(name)
            print(f'{name:<34} ERROR: file is locked (open in xEdit, the CK, '
                  f'or the game?)')
            continue
        except OSError as exc:
            locked.append(name)
            print(f'{name:<34} ERROR: {exc}')
            continue
        if old == new:
            print(f'{name:<34} unchanged (0x{new:08X})')
        else:
            changed += 1
            print(f'{name:<34} 0x{old:08X} -> 0x{new:08X}  '
                  f'ESM {"set" if enable else "cleared"}')

    print(f'\n{changed} plugin(s) changed, '
          f'{len(state) - changed - len(locked)} already correct'
          f'{f", {len(locked)} FAILED" if locked else ""}.')
    if locked:
        print('Close the program holding these files and run again:')
        for n in locked:
            print(f'  - {n}')
        return 2
    if enable and changed:
        print('References in these plugins are no longer forced persistent; '
              f'the engine handle cap is {HANDLE_CAP:,}.')
        print('NOTE: refs that genuinely must stay loaded at a distance now '
              'need their persistent flag set explicitly.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
