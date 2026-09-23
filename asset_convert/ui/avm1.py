"""Assemble AVM1 (ActionScript 2) bytecode from a flat instruction list.

An instruction is one of:

- an opcode name from `OPCODES` (`'GetMember'`), for a bare stack operation;
- `('push', value, ...)`: a str, bool, int, float, `None` (null), `UNDEFINED`,
  or `Register(n)`;
- `('store', n)`: StoreRegister n, which copies the stack top without popping;
- `('label', name)`, `('if', name)`, `('jump', name)`: branches resolve to
  labels in the same body, so no offset is ever written by hand;
- `('function', (param, ...), body)`: an anonymous DefineFunction whose body is
  another instruction list; it pushes the function object.

See: docs/reference/swf_as2_bytecode.md#writing-bytecode
"""

import struct

#: Bare (operand-less) AVM1 opcodes by name.
OPCODES = {
    'Not': 0x12, 'Pop': 0x17, 'GetVariable': 0x1C, 'SetVariable': 0x1D,
    'DefineLocal': 0x3C, 'CallFunction': 0x3D, 'Return': 0x3E,
    'DefineLocal2': 0x41, 'InitObject': 0x43, 'TypeOf': 0x44, 'Add2': 0x47,
    'Less2': 0x48,
    'Equals2': 0x49, 'ToNumber': 0x4A, 'PushDuplicate': 0x4C,
    'GetMember': 0x4E, 'SetMember': 0x4F, 'Increment': 0x50,
    'CallMethod': 0x52, 'Enumerate2': 0x55, 'StrictEquals': 0x66,
}

ACTION_PUSH = 0x96
ACTION_JUMP = 0x99
ACTION_IF = 0x9D
ACTION_DEFINE_FUNCTION = 0x9B
ACTION_STORE_REGISTER = 0x87
ACTION_END = 0x00

#: Byte size of an If or Jump: opcode, u16 length, s16 offset.
BRANCH_SIZE = 5


class Register(int):
    """A register number, pushed as AVM1 push type 4."""


class _Undefined:
    """The AVM1 `undefined` value, pushed as type 3."""


UNDEFINED = _Undefined()


def _push_value(value) -> bytes:
    """Encode one Push operand with its type byte."""
    if value is None:
        return b'\x02'
    if value is UNDEFINED:
        return b'\x03'
    if isinstance(value, Register):
        return bytes((4, value))
    if isinstance(value, bool):
        return bytes((5, int(value)))
    if isinstance(value, int):
        return b'\x07' + struct.pack('<i', value)
    if isinstance(value, float):
        packed = struct.pack('<d', value)
        return b'\x06' + packed[4:] + packed[:4]
    if isinstance(value, str):
        return b'\x00' + value.encode('utf-8') + b'\x00'
    raise TypeError(f'cannot push {value!r}')


def _record(code: int, operand: bytes) -> bytes:
    """An action record carrying a u16-length operand block."""
    return bytes((code,)) + struct.pack('<H', len(operand)) + operand


def _define_function(params, body) -> bytes:
    """An anonymous DefineFunction record followed by its body."""
    code = assemble(body, terminate=False)
    operand = b'\x00' + struct.pack('<H', len(params))
    operand += b''.join(p.encode('utf-8') + b'\x00' for p in params)
    operand += struct.pack('<H', len(code))
    return _record(ACTION_DEFINE_FUNCTION, operand) + code


def _encode(item) -> bytes:
    """The bytes of one non-branch instruction."""
    if isinstance(item, str):
        return bytes((OPCODES[item],))
    kind = item[0]
    if kind == 'push':
        return _record(ACTION_PUSH, b''.join(_push_value(v) for v in item[1:]))
    if kind == 'store':
        return _record(ACTION_STORE_REGISTER, bytes((item[1],)))
    if kind == 'function':
        return _define_function(item[1], item[2])
    if kind == 'label':
        return b''
    raise ValueError(f'unknown instruction {item!r}')


def _is_branch(item) -> bool:
    """Whether the instruction is an If or Jump to a label."""
    return isinstance(item, tuple) and item[0] in ('if', 'jump')


def assemble(program, terminate: bool = True) -> bytes:
    """The bytecode of `program`, END-terminated unless `terminate` is False.

    Two passes: sizes and label offsets first (every branch is 5 bytes), then
    each branch's offset relative to the instruction after it.
    """
    encoded, labels, position = [], {}, 0
    for item in program:
        if isinstance(item, tuple) and item[0] == 'label':
            labels[item[1]] = position
            continue
        chunk = None if _is_branch(item) else _encode(item)
        encoded.append((item, chunk, position))
        position += BRANCH_SIZE if chunk is None else len(chunk)
    out = bytearray()
    for item, chunk, at in encoded:
        if chunk is None:
            code = ACTION_IF if item[0] == 'if' else ACTION_JUMP
            offset = labels[item[1]] - (at + BRANCH_SIZE)
            chunk = _record(code, struct.pack('<h', offset))
        out += chunk
    if terminate:
        out.append(ACTION_END)
    return bytes(out)
