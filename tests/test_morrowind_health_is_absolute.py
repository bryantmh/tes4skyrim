"""An NPC's authored health survives conversion, solved per the engine's formula.

See: docs/commentary/tes5_import_actors.md#morrowind-health-is-absolute
See: docs/commentary/tes5_import_actors.md#health-offset
"""

import struct

from tes5_import.record_types.actor_common import convert_CLAS
from tes5_import.record_types.npc import npc_acbs
from tes5_import.record_types.npc_morrowind import TES5_RACE_BASE_HEALTH

#: Thaurron (Anvil Mages Guild): fixed stats, level 10, 40 health -- spawned dead at -5.
THAURRON = {'DATA.Health': '40', 'ACBS.Level': '10', 'ACBS.Flags': '0'}

#: High Bishop Derminus, the Arktwend intro NPC this regression is named for.
DERMINUS = {'DATA.Health': '397', 'ACBS.Level': '100',
            'MorrowindRace': 'Alamanen'}


def _decode(acbs: bytes) -> tuple:
    """(Level, HealthOffset) out of a packed ACBS payload."""
    level, = struct.unpack_from('<H', acbs, 8)
    offset, = struct.unpack_from('<h', acbs, 20)
    return level, offset


def test_morrowind_health_reaches_the_engine_intact():
    """Derminus keeps all 397 authored hit points, and his level."""
    level, offset = _decode(npc_acbs(DERMINUS))
    assert TES5_RACE_BASE_HEALTH + offset == 397
    assert level == 100


def test_morrowind_high_level_actor_is_not_killed():
    """No authored TES3 pool converts to a non-positive one."""
    for health, level in ((397, 100), (50, 200), (120, 60), (1, 1)):
        rec = {'DATA.Health': str(health), 'ACBS.Level': str(level),
               'MorrowindRace': 'Nord'}
        _, offset = _decode(npc_acbs(rec))
        assert TES5_RACE_BASE_HEALTH + offset > 0


def test_auto_calc_npc_keeps_the_level_term():
    """Only an Auto-calc-stats NPC has the engine's level term solved out."""
    rec = {'DATA.Health': '397', 'ACBS.Level': '100', 'ACBS.Flags': '16'}
    level, offset = _decode(npc_acbs(rec))
    assert (level, offset) == (100, 397 - TES5_RACE_BASE_HEALTH - 99 * 5)


def test_manual_tes4_npc_has_no_level_term():
    """A fixed-stat NPC keeps its whole pool: the engine adds no level bonus back."""
    level, offset = _decode(npc_acbs(THAURRON))
    assert (level, TES5_RACE_BASE_HEALTH + offset) == (10, 40)


def test_class_adds_no_level_up_share():
    """Converted classes carry zero Health/Magicka/Stamina weights."""
    rec = convert_CLAS({'FormID': '00001000', 'EditorID': 'Mage'})
    data = rec[rec.index(b'DATA') + 6:]
    assert data[32:35] == b'\x00\x00\x00'
