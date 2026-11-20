"""Oblivion's No Rumors flag keeps an actor's Rumors topic hidden after conversion.

See: docs/commentary/tes5_import_dialogue.md#no-rumors
"""
import struct

from tes5_import.dialogue.converter import RUMORS_TOPIC, no_rumors_gate
from tes5_import.record_types.npc import npc_acbs

#: TES4 ACBS No Rumors, which Skyrim's GetNoRumors reads as bit 13 of the same field.
NO_RUMORS = 0x2000


def test_rumors_lines_are_gated_on_get_no_rumors():
    """Every line of the engine's Rumors topic gets GetNoRumors (320) == 0 on the speaker."""
    gate = no_rumors_gate({'FormID': f'{RUMORS_TOPIC:08X}'})
    assert gate[:4] == b'CTDA'
    comp, func, run_on = struct.unpack_from('<4xfH10xI', gate, 6)
    assert (comp, func, run_on) == (0.0, 320, 0)


def test_other_topics_are_not_gated():
    """A plugin's own topic at the same object id under another index is not the engine's Rumors."""
    assert no_rumors_gate({'FormID': '010000D7'}) == b''
    assert no_rumors_gate({'FormID': '0000010F'}) == b''


def test_npc_keeps_the_no_rumors_flag():
    """The ACBS flags word keeps 0x2000, e.g. Baurus's 0xA002 keeps Essential and No Rumors."""
    flags = struct.unpack_from('<I', npc_acbs({'ACBS.Flags': '40962', 'EditorID': 'Baurus'}), 0)[0]
    assert flags & NO_RUMORS
    assert flags & 0x2
