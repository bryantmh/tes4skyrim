"""Converted MGEFs carry the Power Affects bit vanilla's census predicts.

See: docs/commentary/tes5_import_magic.md#power-affects
"""

import struct

from tes5_import.base.owned_records import _fall_damage_effect
from tes5_import.record_types.magic import (F_NO_MAGNITUDE,
                                           F_POWER_AFFECTS_DURATION,
                                           F_POWER_AFFECTS_MAGNITUDE,
                                           convert_MGEF)

#: TES4 DATA.Flags: Self delivery, No Duration, No Magnitude.
_T4_SELF = 0x10
_T4_NO_DURATION = 0x80
_T4_NO_MAGNITUDE = 0x100

_POWER = F_POWER_AFFECTS_MAGNITUDE | F_POWER_AFFECTS_DURATION


def _data_flags(blob: bytes, pos: int = 24) -> int:
    """The DATA.Flags of an MGEF record whose subrecords start at ``pos``."""
    while pos < len(blob):
        sig = blob[pos:pos + 4]
        size = struct.unpack_from('<H', blob, pos + 4)[0]
        if sig == b'DATA':
            return struct.unpack_from('<I', blob, pos + 6)[0]
        pos += 6 + size
    raise AssertionError('no DATA subrecord')


def _flags(t4_flags: int, code: str = 'REHE') -> int:
    """The converted record's DATA.Flags."""
    return _data_flags(convert_MGEF({'Signature': 'MGEF', 'FormID': '00001234',
                                     'RecordFlags': '0', 'EditorID': code,
                                     'DATA.Flags': str(t4_flags), 'DATA.BaseCost': '1.0',
                                     'DATA.School': '5'}))


def _power_bits(t4_flags: int, code: str = 'REHE') -> int:
    """The Power Affects bits of the converted record's DATA.Flags."""
    return _flags(t4_flags, code) & _POWER


def test_an_effect_with_magnitude_scales_its_magnitude():
    """Magnitude and duration both authored: power scales the magnitude."""
    assert _power_bits(_T4_SELF) == F_POWER_AFFECTS_MAGNITUDE


def test_an_effect_without_magnitude_scales_its_duration():
    """No Magnitude, as invisibility or paralysis: power scales duration."""
    assert _power_bits(_T4_SELF | _T4_NO_MAGNITUDE, 'INVI') == F_POWER_AFFECTS_DURATION


def test_an_effect_with_neither_scales_nothing():
    """No Magnitude and No Duration leave both bits clear."""
    assert _power_bits(_T4_SELF | _T4_NO_MAGNITUDE | _T4_NO_DURATION, 'INVI') == 0


def test_no_magnitude_survives_only_on_a_switch_actor_value():
    """No Magnitude means 1.0: kept for Invisibility, dropped for Magicka.

    See: docs/commentary/tes5_import_magic.md#no-magnitude-forces-one
    """
    assert _flags(_T4_SELF | _T4_NO_MAGNITUDE, 'INVI') & F_NO_MAGNITUDE
    assert not _flags(_T4_SELF | _T4_NO_MAGNITUDE, 'SLNC') & F_NO_MAGNITUDE
    assert not _flags(_T4_SELF | _T4_NO_MAGNITUDE, 'REHE') & F_NO_MAGNITUDE


def test_fall_damage_effect_never_heals():
    """The fall-damage Health value modifier carries no No Magnitude."""
    assert not _data_flags(_fall_damage_effect(0x100, 'X', 0x200)) & F_NO_MAGNITUDE
