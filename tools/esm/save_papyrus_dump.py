"""Dump the Papyrus state a Skyrim SE save holds for chosen scripts.

For every script instance whose script name contains `--script`, prints the
form the instance is bound to (a placed reference, a quest, ...) and the value
of each of its variables as the save recorded them.  Answers "what state did
the game actually leave this script in" without launching the game.

SSE layout, measured on real saves (UESP documents the LE one): the string
count is u32 and string references widen to u32 once it exceeds 0xFFFF;
object ids (instances, ref variables) are u64 handles.  The tables between the
instance list and the per-instance data do not match the documented LE layout,
so each instance's data record is found by its id and checked against the
script's member count.

Usage:
  python -m tools.esm.save_papyrus_dump <save.ess | saves dir> --script NAME
"""

import argparse
import glob
import os
import struct

from tools.esm.save_formid_scan import formid_array, location_table, read_body

#: Global data type of the Papyrus block in globalDataTable3.
PAPYRUS_TYPE = 1001


class _Cursor:
    """Sequential little-endian reader over the save body."""

    def __init__(self, data, pos, wide=False):
        self.data, self.pos, self.wide = data, pos, wide

    def take(self, fmt):
        """The next value of struct format `fmt`."""
        value = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += struct.calcsize(fmt)
        return value

    def sref(self):
        """The next string-table reference."""
        return self.take('<I' if self.wide else '<H')

    def wstring(self):
        """The next u16-length-prefixed string."""
        n = self.take('<H')
        self.pos += n
        return self.data[self.pos - n:self.pos].decode('utf-8', 'replace')

    def refid(self):
        """The next 3-byte big-endian save RefID."""
        b0, b1, b2 = self.data[self.pos:self.pos + 3]
        self.pos += 3
        return (b0 << 16) | (b1 << 8) | b2


def papyrus_offset(body, shot_end):
    """Body offset of the Papyrus block's data."""
    pos = location_table(body)[5] - shot_end
    for _ in range(8):
        kind, length = struct.unpack_from('<II', body, pos)
        if kind == PAPYRUS_TYPE:
            return pos + 8
        pos += 8 + length
    raise ValueError('no Papyrus block in globalDataTable3')


def read_scripts(cur, strings):
    """{script name: [member names]} from the script table."""
    members = {}
    for _ in range(cur.take('<I')):
        name = strings[cur.sref()]
        cur.sref()
        members[name] = []
        for _ in range(cur.take('<I')):
            members[name].append(strings[cur.sref()])
            cur.sref()
    return members


def bound_form(refid, fids):
    """The FormID a save RefID names."""
    kind, value = refid >> 22, refid & 0x3FFFFF
    if kind == 0:
        return fids[value - 1] if value else 0
    if kind == 2:
        return 0xFF000000 | value
    return value


def read_instances(cur, strings, needle, fids):
    """[(id, script, bound FormID)] for instances whose script name contains `needle`."""
    found = []
    for _ in range(cur.take('<I')):
        sid = cur.take('<Q')
        name = strings[cur.sref()]
        cur.pos += 4
        refid = cur.refid()
        cur.pos += 1
        if needle.lower() in name.lower():
            found.append((sid, name, bound_form(refid, fids)))
    return found


def variable(cur, strings):
    """One saved Papyrus variable, as a printable value."""
    kind = cur.take('<B')
    if kind == 1:
        return f'{strings[cur.sref()]} handle {cur.take("<Q"):#x}'
    if kind == 2:
        return repr(strings[cur.sref()])
    if kind == 11:
        return f'{strings[cur.sref()]}[] array {cur.take("<I")}'
    raw = cur.take('<I')
    if kind == 3:
        return str(struct.unpack('<i', struct.pack('<I', raw))[0])
    if kind == 4:
        return str(struct.unpack('<f', struct.pack('<I', raw))[0])
    if kind == 5:
        return str(bool(raw))
    return 'None' if kind == 0 else f'array({kind}) {raw}'


def instance_data(body, start, sid, names, strings, wide):
    """The variable values of instance `sid`, or None when no matching record is found."""
    key = struct.pack('<Q', sid)
    at = body.find(key, start)
    while at != -1:
        cur = _Cursor(body, at + 8, wide)
        try:
            flag = cur.take('<B')
            cur.sref()
            cur.pos += 8 if flag & 4 else 4
            if cur.take('<I') == len(names):
                return [variable(cur, strings) for _ in names]
        except (IndexError, struct.error):
            pass
        at = body.find(key, at + 1)
    return None


def dump(path, needle):
    """Print every matching instance of the save at `path`."""
    body, shot_end = read_body(path)
    cur = _Cursor(body, papyrus_offset(body, shot_end) + 2)
    count = cur.take('<I')
    strings = [cur.wstring() for _ in range(count)]
    cur.wide = count > 0xFFFF
    members = read_scripts(cur, strings)
    found = read_instances(cur, strings, needle, formid_array(body, shot_end))
    print(f'=== {os.path.basename(path)}: {len(found)} instance(s)')
    for sid, name, form in found:
        print(f'{name} on {form:#010x} (instance {sid:#x})')
        values = instance_data(body, cur.pos, sid, members[name], strings,
                               cur.wide)
        if values is None:
            print('  (no variable record found)')
            continue
        for member, value in zip(members[name], values):
            print(f'  {member} = {value}')


def newest_save(target):
    """`target` itself, or the newest .ess in it when it is a directory."""
    if not os.path.isdir(target):
        return target
    return max(glob.glob(os.path.join(target, '*.ess')), key=os.path.getmtime)


def main():
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', help='.ess file, or a saves directory (newest save)')
    ap.add_argument('--script', required=True,
                    help='substring of the Papyrus script name, case-insensitive')
    args = ap.parse_args()
    dump(newest_save(args.target), args.script)


if __name__ == '__main__':
    main()
