"""Converted MGEFs carry the Power Affects bit vanilla's census predicts.

See: docs/commentary/tes5_import_magic.md#power-affects
"""

import struct

from tes5_import.record_types.magic import (F_POWER_AFFECTS_DURATION,
                                           F_POWER_AFFECTS_MAGNITUDE,
                                           convert_MGEF)

#: TES4 DATA.Flags: Self delivery, No Duration, No Magnitude.
_T4_SELF = 0x10
_T4_NO_DURATION = 0x80
_T4_NO_MAGNITUDE = 0x100

_POWER = F_POWER_AFFECTS_MAGNITUDE | F_POWER_AFFECTS_DURATION


def _power_bits(t4_flags: int, code: str = 'REHE') -> int:
    """The Power Affects bits of the converted record's DATA.Flags."""
    blob = convert_MGEF({'Signature': 'MGEF', 'FormID': '00001234',
                         'RecordFlags': '0', 'EditorID': code,
                         'DATA.Flags': str(t4_flags), 'DATA.BaseCost': '1.0',
                         'DATA.School': '5'})
    pos = 24
    while pos < len(blob):
        sig = blob[pos:pos + 4]
        size = struct.unpack_from('<H', blob, pos + 4)[0]
        if sig == b'DATA':
            return struct.unpack_from('<I', blob, pos + 6)[0] & _POWER
        pos += 6 + size
    raise AssertionError('no DATA subrecord')


def test_an_effect_with_magnitude_scales_its_magnitude():
    """Magnitude and duration both authored: power scales the magnitude."""
    assert _power_bits(_T4_SELF) == F_POWER_AFFECTS_MAGNITUDE


def test_an_effect_without_magnitude_scales_its_duration():
    """No Magnitude, as invisibility or paralysis: power scales duration."""
    assert _power_bits(_T4_SELF | _T4_NO_MAGNITUDE) == F_POWER_AFFECTS_DURATION


def test_an_effect_with_neither_scales_nothing():
    """No Magnitude and No Duration leave both bits clear."""
    assert _power_bits(_T4_SELF | _T4_NO_MAGNITUDE | _T4_NO_DURATION) == 0
