"""Completion latch of a quest-gated Once Per Day package: a global its OnEnd fragment sets to the next day.

See: docs/commentary/tes5_import_package.md#once-per-day-completion-latch
"""

import struct

from script_convert.pipeline import build_vmad_package_fragment
from ..base.writer import pack_record, pack_string_subrecord, pack_subrecord

#: The shared OnEnd fragment script; its `Latch` property is the package's global.
DONE_SCRIPT = 'TES4_PackageDone'

#: Skyrim.esm's GameDaysPassed global.
GAME_DAYS_PASSED = 0x00000039

#: CTDA function index of GetGlobalValue.
_GET_GLOBAL_VALUE = 74

#: CTDA operator byte: `>=` (3 << 5) with the comparison value read from a global (0x04).
_GE_GLOBAL = 0x64


def create_latch(writer, pack_fid: int) -> int:
    """Writes the package's latch GLOB (a short at 0) and returns its FormID."""
    name = 'TES4PackDone_%06X' % (pack_fid & 0x00FFFFFF)
    fid = writer.derive_formid('GLOB', name)
    subs = pack_string_subrecord('EDID', name)
    subs += pack_subrecord('FNAM', struct.pack('<B', ord('s')))
    subs += pack_subrecord('FLTV', struct.pack('<f', 0.0))
    writer.add_record('GLOB', pack_record('GLOB', fid, 0, subs))
    return fid


def latch_vmad(latch: int) -> bytes:
    """The PACK's VMAD subrecord: the OnEnd fragment bound to `latch`."""
    return pack_subrecord('VMAD', build_vmad_package_fragment(DONE_SCRIPT, object_props={'Latch': latch}))


def latch_condition(latch: int) -> bytes:
    """The PACK's CTDA subrecord `GetGlobalValue(GameDaysPassed) >= latch`."""
    return pack_subrecord('CTDA', struct.pack('<B3xIHHIIII I', _GE_GLOBAL, latch, _GET_GLOBAL_VALUE, 0,
                                              GAME_DAYS_PASSED, 0, 0, 0, 0xFFFFFFFF))
