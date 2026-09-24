"""
An override that swaps the script its master's record binds for a child of it.

The patch's key names the parent and the child (`Parent>Child`); every
length-prefixed occurrence of the parent's name in the master's VMAD -- the
script entry and a fragment's file name -- becomes the child's. The child
extends the parent, so the properties the master bound still bind.

See: docs/commentary/tes5_import_override.md#script-swap
"""


import re
import struct

#: Export key of a script swap: `<parent script>><child script>`.
SCRIPT_SWAP_KEY = 'VMAD.ScriptSwap'

#: Every type whose converter attaches object scripts via get_object_vmad.
SCRIPTED_TYPES = ('ACTI', 'ALCH', 'APPA', 'ARMO', 'BOOK', 'CLOT', 'CONT',
                  'CREA', 'DOOR', 'FLOR', 'FURN', 'INGR', 'KEYM', 'LIGH',
                  'MISC', 'NPC_', 'SGST', 'SLGM', 'STAT', 'WEAP')


def swap_vmad_script(old: bytes, rec: dict) -> bytes:
    """`old` VMAD with the parent script `rec` names renamed to its child, wherever it is named."""
    parent, _, child = (rec.get(SCRIPT_SWAP_KEY) or '').partition('>')
    if not parent or not child:
        return old
    name = re.compile(re.escape(struct.pack('<H', len(parent)))
                      + b'(?i:' + re.escape(parent.encode('ascii')) + b')')
    return name.sub(lambda _m: struct.pack('<H', len(child)) + child.encode('ascii'), old)
