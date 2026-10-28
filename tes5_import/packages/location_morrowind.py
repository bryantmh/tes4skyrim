"""
Where a converted Morrowind wander package keeps its actor.

OpenMW's AiWander with distance 0 never moves its actor, so an NPC a script
has positioned stays where it was put. Skyrim's "near editor location" walks
it back home instead; "near self" with radius 0 keeps it where it stands.

See: docs/commentary/tes4_export_morrowind.md#ai-packages
"""

import struct

from ..record_types.common import get_int
from .interrupt_morrowind import HELLO_FIELD

#: TES4 PKDT.Type of a wander, and the TES4 PLDT.Type the Morrowind export gives it.
_WANDER, _NEAR_EDITOR_LOCATION = 5, 3

#: TES5 PLDT "near self" with radius 0: wherever the actor stands.
_STAY_PUT = struct.pack('<iIi', 12, 0, 0)


def standing_wander_location(rec: dict):
    """The PLDT of a Morrowind wander that never moves its actor, else None."""
    if HELLO_FIELD not in rec or get_int(rec, 'PKDT.Type') != _WANDER:
        return None
    if get_int(rec, 'PLDT.Type', -1) != _NEAR_EDITOR_LOCATION or get_int(rec, 'PLDT.Radius', 0) > 0:
        return None
    return _STAY_PUT
