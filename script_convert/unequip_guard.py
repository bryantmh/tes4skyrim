"""Keeps a converted OnUnequip block from running when the item was taken out of the actor's inventory.

See: docs/commentary/script_convert.md#unequip-on-removal
"""

#: The Papyrus event of a TES4 OnUnequip block.
UNEQUIP = 'Event OnUnequipped(Actor akActor)'

#: Property the importer binds, per base record, to the record the script is attached to.
SELF_BASE = 'TES4_SelfBase'

_GUARD = ['  Utility.WaitMenuMode(0.1)', '  If %s && akActor.GetItemCount(%s) < 1' % (SELF_BASE, SELF_BASE),
          '    Return', '  EndIf']


def unequip_guard(conv, body: list) -> list:
    """`body` whose OnUnequipped returns when the actor no longer holds the item; unchanged without the event."""
    if not any(line.strip() == UNEQUIP for line in body):
        return body
    conv.sc.property_refs[SELF_BASE] = 'Form'
    out = []
    for line in body:
        out.append(line)
        if line.strip() == UNEQUIP:
            out += _GUARD
    return out
