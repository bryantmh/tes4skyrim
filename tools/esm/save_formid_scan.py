"""Read a Skyrim SE .ess save and report which FormIDs it persists.

Answers one question: if a converted plugin's generated record changes FormID
between builds, does an existing save break? A save stores raw FormIDs, so the
answer is yes for exactly those ids that appear in its FORM ID ARRAY — the
table every save-resident object reference is indexed through.

That array is therefore ground truth for "which derived record types must have
stable FormIDs". Measured 2026-08-17 against a real converted-Oblivion save:
NAVM appears 564 times (the engine persists navmesh obstacle/door state), as do
DLVW, IPDS, VTYP, MESG and OTFT — so there is no "build-internal" class that can
safely be allocated sequentially. Every derived id is hashed from its source.

Format (SSE, saveVersion 12) per the UESP save-format documentation:
  magic 'TESV_SAVEGAME'(13) | headerSize u32 | header | screenshot
  | formVersion u8 | pluginInfoSize u32 | pluginInfo
  | fileLocationTable (fixed u32 offsets, formIDArrayCountOffset among them)
The FormID array is a u32 count followed by that many u32 ids.

Usage:
  python tools/esm/save_formid_scan.py <save.ess> [--plugin-index N]
  python tools/esm/save_formid_scan.py <dir> --all --plugin-index N
"""

import argparse
import os
import struct
from collections import Counter

import lz4.block


def _u8(d, o):
    return d[o], o + 1


def _u16(d, o):
    return struct.unpack_from('<H', d, o)[0], o + 2


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0], o + 4


def _wstring(d, o):
    n, o = _u16(d, o)
    return d[o:o + n].decode('utf-8', 'replace'), o + n


def _screenshot_end(d):
    """(saveVersion, offset just past the header and screenshot) of raw save bytes `d`."""
    header_size, hs = _u32(d, 13)
    save_version, h = _u32(d, hs)
    _player_name, h = _wstring(d, h + 4)
    _location, h = _wstring(d, h + 4)
    _game_date, h = _wstring(d, h)
    _race, h = _wstring(d, h)
    h += 2 + 4 + 4 + 8
    shot_w, h = _u32(d, h)
    shot_h, h = _u32(d, h)
    bpp = 4 if save_version >= 12 else 3
    return save_version, hs + header_size + shot_w * shot_h * bpp


def read_body(path):
    """(body, shot_end): the decompressed save body, and the prefix its table offsets count.

    SSE saves (saveVersion >= 12) LZ4-compress everything after the screenshot.
    The file location table's offsets are relative to the WHOLE uncompressed
    file, so subtract `shot_end` from them to index `body`.
    """
    with open(path, 'rb') as f:
        d = f.read()
    if d[:13] != b'TESV_SAVEGAME':
        raise ValueError(f'not a Skyrim save: {path}')
    save_version, shot_end = _screenshot_end(d)
    if save_version < 12:
        return d[shot_end:], 0
    uncompressed_size, o = _u32(d, shot_end)
    compressed_size, o = _u32(d, o)
    return (lz4.block.decompress(d[o:o + compressed_size],
                                 uncompressed_size=uncompressed_size),
            shot_end)


def location_table(body):
    """The 12 u32 file-location-table fields, just past the plugin info."""
    plugin_info_size = struct.unpack_from('<I', body, 1)[0]
    return struct.unpack_from('<12I', body, 5 + plugin_info_size)


def formid_array(body, shot_end):
    """The save's FormID array: what a type-0 RefID indexes (value - 1)."""
    n, fo = _u32(body, location_table(body)[0] - shot_end)
    return list(struct.unpack_from('<%dI' % n, body, fo))


def read_save(path):
    """Parse a .ess far enough to return (plugins, formid_array)."""
    body, shot_end = read_body(path)
    count, b = _u8(body, 5)
    plugins = []
    for _ in range(count):
        name, b = _wstring(body, b)
        plugins.append(name)
    return plugins, formid_array(body, shot_end)


def scan(path, plugin_index=None, quiet=False):
    plugins, ids = read_save(path)
    if not quiet:
        print(f'\n=== {os.path.basename(path)} ===')
        print(f'  plugins in save: {len(plugins)}')
        for i, p in enumerate(plugins):
            print(f'    [{i:02X}] {p}')
        print(f'  FormID array entries: {len(ids)}')

    by_index = Counter((f >> 24) & 0xFF for f in ids)
    if not quiet:
        print('  ids by plugin index byte:')
        for idx in sorted(by_index):
            name = plugins[idx] if idx < len(plugins) else '?'
            print(f'    {idx:02X}: {by_index[idx]:7,}  {name}')

    if plugin_index is not None:
        own = sorted(f for f in ids if ((f >> 24) & 0xFF) == plugin_index)
        return plugins, own
    return plugins, ids


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', help='.ess file or directory')
    ap.add_argument('--plugin-index', type=lambda s: int(s, 0), default=None,
                    help='report only ids owned by this load-order index byte')
    ap.add_argument('--all', action='store_true',
                    help='scan every .ess in a directory')
    ap.add_argument('--dump', metavar='FILE',
                    help='write the selected FormIDs, one hex id per line')
    args = ap.parse_args()

    targets = []
    if os.path.isdir(args.target):
        for n in sorted(os.listdir(args.target)):
            if n.lower().endswith('.ess'):
                targets.append(os.path.join(args.target, n))
        if not args.all:
            targets = targets[:1]
    else:
        targets = [args.target]

    collected = set()
    for t in targets:
        try:
            _plugins, ids = scan(t, args.plugin_index,
                                 quiet=(args.all and len(targets) > 3))
            collected.update(ids)
            if args.all and len(targets) > 3:
                print(f'  {os.path.basename(t)}: {len(ids):,} ids')
        except Exception as e:
            print(f'  !! {os.path.basename(t)}: {type(e).__name__}: {e}')

    print(f'\nTOTAL distinct FormIDs collected: {len(collected):,}')
    if args.dump:
        with open(args.dump, 'w') as f:
            for i in sorted(collected):
                f.write('%08X\n' % i)
        print(f'wrote {args.dump}')


if __name__ == '__main__':
    main()
