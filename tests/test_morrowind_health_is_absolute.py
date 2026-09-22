"""A Morrowind NPC's authored health survives conversion; TES4's does not change.

See: docs/commentary/tes5_import_actors.md#morrowind-health-is-absolute
"""

import struct

from tes5_import.record_types.npc import npc_acbs
from tes5_import.record_types.npc_morrowind import TES5_RACE_BASE_HEALTH

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


def test_oblivion_npc_keeps_the_level_term():
    """An NPC with no Morrowind marker converts exactly as before."""
    rec = {'DATA.Health': '397', 'ACBS.Level': '100'}
    level, offset = _decode(npc_acbs(rec))
    assert (level, offset) == (100, 397 - TES5_RACE_BASE_HEALTH - 99 * 5)
