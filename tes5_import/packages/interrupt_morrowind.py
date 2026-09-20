"""
Which unprompted speech a converted Morrowind package allows.

Skyrim asks the RUNNING PACKAGE whether its actor may greet, chatter or react,
through the PKDT interrupt flags. OpenMW answers the same question from the
actor's authored `Hello` setting and the kind of package it is running, so the
flags are read off those two facts.

See: docs/commentary/tes4_export_morrowind.md#when-a-bark-fires
"""

from ..record_types.common import get_int

#: PKDT interrupt flags: hellos to player, reaction to player actions, idle chatter.
_HELLOS, _REACTIONS, _IDLE_CHATTER = 0x0001, 0x0010, 0x0080

#: TES4 PKDT.Type of the packages OpenMW keeps silent: follow and escort.
_FOLLOW, _ESCORT = 1, 2

#: TES4 PKDT.Type of the packages OpenMW greets under: wander and travel.
_GREETING_TYPES = frozenset({5, 6})

#: The export field carrying the owning actor's AIDT Hello setting.
_HELLO_FIELD = 'MorrowindHello'


def interrupt_for_kind(package_type: int, hello: int, default: int) -> int:
    """The interrupt flags for one Morrowind package kind and Hello setting."""
    flags = default | _REACTIONS
    if hello <= 0:
        return flags
    if package_type in _GREETING_TYPES:
        flags |= _HELLOS
    if package_type not in (_FOLLOW, _ESCORT):
        flags |= _IDLE_CHATTER
    return flags


def morrowind_interrupt(rec: dict, default: int) -> int:
    """`default` for a package Morrowind did not author, else its speech flags."""
    if _HELLO_FIELD not in rec:
        return default
    return interrupt_for_kind(get_int(rec, 'PKDT.Type'),
                              get_int(rec, _HELLO_FIELD), default)
