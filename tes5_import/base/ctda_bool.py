"""Which outcomes of a 0/1 condition function a CTDA comparison lets through."""

import struct

_CTDA_USE_GLOBAL = 0x04

_COMPARE = {
    0: lambda a, b: a == b,
    1: lambda a, b: a != b,
    2: lambda a, b: a > b,
    3: lambda a, b: a >= b,
    4: lambda a, b: a < b,
    5: lambda a, b: a <= b,
}


def bool_outcomes(type_byte: int, comp_raw: int) -> 'tuple | None':
    """(passes when the function returns 1, passes when it returns 0).

    None for a comparison against a global or an unknown operator, whose
    outcome cannot be known at conversion time.
    """
    op = (type_byte >> 5) & 0x7
    if type_byte & _CTDA_USE_GLOBAL or op not in _COMPARE:
        return None
    comp = struct.unpack('<f', struct.pack('<I', comp_raw))[0]
    return _COMPARE[op](1.0, comp), _COMPARE[op](0.0, comp)
