"""
Disassemble the ActionScript 2 bytecode inside a SWF.

Walks the tag stream, decompresses CWS/ZWS bodies, and prints the AVM1 action
records carried by DoAction, DoInitAction and DefineSprite tags. Constant pools
are resolved so pushes read as their literal strings, which is what makes a
`--trace` search useful: it reports every action block naming a symbol, so a
property's writers and readers can be found without a full decompiler.

AS3 (DoABC) is reported but not disassembled -- it is a different VM.

See: docs/reference/swf_as2_bytecode.md#tracing-a-data-path
"""

import argparse
import lzma
import struct
import zlib

#: Tags carrying AVM1 records, mapped to the header bytes preceding the actions.
ACTION_TAGS = {12: 0, 59: 2}

#: Tag codes named in output.
TAG_NAMES = {
    0: 'End', 1: 'ShowFrame', 12: 'DoAction', 26: 'PlaceObject2',
    34: 'DefineButton2', 37: 'DefineEditText', 39: 'DefineSprite',
    56: 'ExportAssets', 59: 'DoInitAction', 76: 'SymbolClass',
    82: 'DoABC', 72: 'DoABCShort',
}

#: AVM1 opcodes: code -> (name, operand layout).
OPS = {
    0x04: ('NextFrame', ''), 0x06: ('Play', ''), 0x07: ('Stop', ''),
    0x0A: ('Add', ''), 0x0B: ('Subtract', ''), 0x0C: ('Multiply', ''),
    0x0D: ('Divide', ''), 0x0E: ('Equals', ''), 0x0F: ('Less', ''),
    0x10: ('And', ''), 0x11: ('Or', ''), 0x12: ('Not', ''),
    0x13: ('StringEquals', ''), 0x17: ('Pop', ''), 0x18: ('ToInteger', ''),
    0x1C: ('GetVariable', ''), 0x1D: ('SetVariable', ''),
    0x20: ('SetTarget2', ''), 0x21: ('StringAdd', ''),
    0x22: ('GetProperty', ''), 0x23: ('SetProperty', ''),
    0x24: ('CloneSprite', ''), 0x25: ('RemoveSprite', ''),
    0x26: ('Trace', ''), 0x28: ('EndDrag', ''),
    0x2B: ('CastOp', ''), 0x2C: ('ImplementsOp', ''),
    0x30: ('RandomNumber', ''), 0x34: ('GetTime', ''),
    0x3A: ('Delete', ''), 0x3B: ('Delete2', ''), 0x3C: ('DefineLocal', ''),
    0x3D: ('CallFunction', ''), 0x3E: ('Return', ''), 0x3F: ('Modulo', ''),
    0x40: ('NewObject', ''), 0x41: ('DefineLocal2', ''),
    0x42: ('InitArray', ''), 0x43: ('InitObject', ''),
    0x44: ('TypeOf', ''), 0x46: ('Enumerate', ''), 0x47: ('Add2', ''),
    0x48: ('Less2', ''), 0x49: ('Equals2', ''), 0x4A: ('ToNumber', ''),
    0x4B: ('ToString', ''), 0x4C: ('PushDuplicate', ''),
    0x4E: ('GetMember', ''), 0x4F: ('SetMember', ''),
    0x50: ('Increment', ''), 0x51: ('Decrement', ''),
    0x52: ('CallMethod', ''), 0x53: ('NewMethod', ''),
    0x54: ('InstanceOf', ''), 0x55: ('Enumerate2', ''),
    0x60: ('BitAnd', ''), 0x61: ('BitOr', ''), 0x62: ('BitXor', ''),
    0x67: ('Greater', ''), 0x69: ('Extends', ''),
    0x81: ('GotoFrame', 'H'), 0x83: ('GetURL', 'ss'),
    0x87: ('StoreRegister', 'B'), 0x88: ('ConstantPool', 'pool'),
    0x8A: ('WaitForFrame', 'H'), 0x8B: ('SetTarget', 's'),
    0x8C: ('GotoLabel', 's'), 0x8E: ('DefineFunction2', 'func'),
    0x8F: ('Try', ''), 0x94: ('With', 'H'), 0x96: ('Push', 'push'),
    0x99: ('Jump', 'h'), 0x9A: ('GetURL2', 'B'),
    0x9B: ('DefineFunction', 'func'), 0x9D: ('If', 'h'),
    0x9E: ('Call', ''), 0x9F: ('GotoFrame2', 'B'),
}

#: Push value types that are a fixed-width struct: type -> (format, width).
PUSH_STRUCTS = {1: ('<f', 4), 7: ('<i', 4)}

#: Push value types that are a literal needing no operand bytes.
PUSH_LITERALS = {2: 'null', 3: 'undefined'}

#: Push value types that index the constant pool: type -> operand width.
PUSH_POOLED = {8: 1, 9: 2}


def read_swf(path):
    """Return the uncompressed bytes of a SWF, header included."""
    data = open(path, 'rb').read()
    sig = data[:3]
    if sig == b'CWS':
        return data[:8] + zlib.decompress(data[8:])
    if sig == b'ZWS':
        return data[:8] + lzma.decompress(data[12:], format=lzma.FORMAT_ALONE)
    return data


def body_offset(data):
    """Return the offset of the first tag, past the frame rect and rate."""
    nbits = data[8] >> 3
    return 8 + (5 + 4 * nbits + 7) // 8 + 4


def iter_tags(data):
    """Yield (code, payload, offset) for every top-level tag."""
    off = body_offset(data)
    while off < len(data) - 1:
        code_and_len = struct.unpack('<H', data[off:off + 2])[0]
        off += 2
        code, length = code_and_len >> 6, code_and_len & 0x3F
        if length == 0x3F:
            length = struct.unpack('<I', data[off:off + 4])[0]
            off += 4
        yield code, data[off:off + length], off
        off += length
        if code == 0:
            break


def _cstring(buf, pos):
    """Return (text, position after the terminator) for a NUL-terminated string."""
    end = buf.index(b'\0', pos)
    return buf[pos:end].decode('utf-8', 'replace'), end + 1


def _push_one(buf, pos, pool):
    """Return (display text, new position) for one typed Push value."""
    kind = buf[pos]
    pos += 1
    if kind == 0:
        val, pos = _cstring(buf, pos)
        return repr(val), pos
    if kind in PUSH_LITERALS:
        return PUSH_LITERALS[kind], pos
    if kind in PUSH_STRUCTS:
        fmt, width = PUSH_STRUCTS[kind]
        return str(struct.unpack(fmt, buf[pos:pos + width])[0]), pos + width
    if kind == 4:
        return 'reg%d' % buf[pos], pos + 1
    if kind == 5:
        return ('true' if buf[pos] else 'false'), pos + 1
    if kind == 6:
        halves = buf[pos + 4:pos + 8] + buf[pos:pos + 4]
        return str(struct.unpack('<d', halves)[0]), pos + 8
    if kind in PUSH_POOLED:
        width = PUSH_POOLED[kind]
        idx = buf[pos] if width == 1 else struct.unpack('<H', buf[pos:pos + 2])[0]
        text = repr(pool[idx]) if idx < len(pool) else 'const%d' % idx
        return text, pos + width
    return '?type%d' % kind, len(buf)


def _push_values(buf, pool):
    """Return the display strings for a Push action's whole value list."""
    out, pos = [], 0
    while pos < len(buf):
        text, pos = _push_one(buf, pos, pool)
        out.append(text)
    return out


def _decode_pool(buf):
    """Return the string list declared by a ConstantPool action."""
    count = struct.unpack('<H', buf[:2])[0]
    pos, items = 2, []
    for _ in range(count):
        val, pos = _cstring(buf, pos)
        items.append(val)
    return items


def _decode_operand(code, buf, pool):
    """Return (text, pool) for one action, updating the pool if it declares one."""
    name, layout = OPS.get(code, ('Unknown%02X' % code, ''))
    if layout == 'pool':
        items = _decode_pool(buf)
        return '%s (%d entries)' % (name, len(items)), items
    if layout == 'push':
        return '%s %s' % (name, ', '.join(_push_values(buf, pool))), pool
    if layout == 'func':
        fname, pos = _cstring(buf, 0)
        nargs = struct.unpack('<H', buf[pos:pos + 2])[0]
        return '%s %s(%d args)' % (name, fname or '<anon>', nargs), pool
    if layout == 's':
        return '%s %s' % (name, _cstring(buf, 0)[0]), pool
    if layout == 'ss':
        first, pos = _cstring(buf, 0)
        return '%s %s %s' % (name, first, _cstring(buf, pos)[0]), pool
    if layout in ('h', 'H'):
        return '%s %d' % (name, struct.unpack('<' + layout, buf[:2])[0]), pool
    if layout == 'B':
        return '%s %d' % (name, buf[0]), pool
    return name, pool


def disassemble(payload, pool=None):
    """Return a list of '<offset>: <text>' lines for one action block."""
    lines, pos, pool = [], 0, list(pool or [])
    while pos < len(payload):
        code = payload[pos]
        start = pos
        pos += 1
        if code == 0:
            lines.append('%5d: End' % start)
            break
        buf = b''
        if code >= 0x80:
            length = struct.unpack('<H', payload[pos:pos + 2])[0]
            pos += 2
            buf = payload[pos:pos + length]
            pos += length
        text, pool = _decode_operand(code, buf, pool)
        lines.append('%5d: %s' % (start, text))
    return lines


def _sprite_actions(payload, off):
    """Yield (label, action bytes) for DoAction tags nested in a DefineSprite."""
    char_id = struct.unpack('<H', payload[:2])[0]
    pos = 4
    while pos < len(payload) - 1:
        code_and_len = struct.unpack('<H', payload[pos:pos + 2])[0]
        pos += 2
        code, length = code_and_len >> 6, code_and_len & 0x3F
        if length == 0x3F:
            length = struct.unpack('<I', payload[pos:pos + 4])[0]
            pos += 4
        if code == 12:
            yield 'DoAction@%d sprite=%d' % (off + pos, char_id), payload[pos:pos + length]
        pos += length
        if code == 0:
            break


def action_blocks(data):
    """Yield (label, action bytes) for every AVM1-carrying tag, sprites included."""
    for code, payload, off in iter_tags(data):
        if code in ACTION_TAGS:
            label = '%s@%d' % (TAG_NAMES.get(code, code), off)
            if code == 59:
                label += ' sprite=%d' % struct.unpack('<H', payload[:2])[0]
            yield label, payload[ACTION_TAGS[code]:]
        elif code == 39:
            for sub in _sprite_actions(payload, off):
                yield sub
        elif code in (82, 72):
            yield 'DoABC@%d (AS3 -- not disassembled)' % off, b''


def export_names(data):
    """Return {character_id: exported name} from every ExportAssets tag."""
    names = {}
    for code, payload, _ in iter_tags(data):
        if code != 56:
            continue
        count = struct.unpack('<H', payload[:2])[0]
        pos = 2
        for _ in range(count):
            cid = struct.unpack('<H', payload[pos:pos + 2])[0]
            name, pos = _cstring(payload, pos + 2)
            names[cid] = name
    return names


def _print_tags(data):
    """Print the tag histogram and the export table for one movie."""
    counts = {}
    for code, _, _ in iter_tags(data):
        counts[code] = counts.get(code, 0) + 1
    for code in sorted(counts):
        print('%-16s %d' % (TAG_NAMES.get(code, 'Tag%d' % code), counts[code]))
    for cid, name in sorted(export_names(data).items()):
        print('export %5d %s' % (cid, name))


def _context_lines(lines, hits, width):
    """Return the lines around each hit index, deduplicated and in order."""
    if not width:
        return lines
    shown = set()
    for i in hits:
        shown.update(range(max(0, i - width), min(len(lines), i + width + 1)))
    return [lines[i] for i in sorted(shown)]


def main():
    """Disassemble a SWF's AVM1 blocks, or trace the ones naming a symbol."""
    ap = argparse.ArgumentParser(description='Disassemble AS2 bytecode in a SWF.')
    ap.add_argument('swf')
    ap.add_argument('--trace', action='append', default=[],
                    help='only print blocks naming this symbol (repeatable)')
    ap.add_argument('--context', type=int, default=0,
                    help='lines of context around a hit; 0 prints the whole block')
    ap.add_argument('--tags', action='store_true', help='list tags and exports only')
    args = ap.parse_args()

    data = read_swf(args.swf)
    if args.tags:
        _print_tags(data)
        return

    for label, payload in action_blocks(data):
        if not payload:
            continue
        lines = disassemble(payload)
        hits = [i for i, ln in enumerate(lines) if any(t in ln for t in args.trace)]
        if args.trace and not hits:
            continue
        print('===== %s' % label)
        print('\n'.join(_context_lines(lines, hits, args.context) if args.trace
                        else lines))


if __name__ == '__main__':
    main()
