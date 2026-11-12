"""Header flags of an override REFERENCE (REFR/ACHR/ACRE).

An override is the master's converted bytes with the author's subrecord
changes spliced in. Whether a placed reference starts disabled, or shows at a
distance, is authored in its record HEADER, which that splice never reads.

See: docs/commentary/tes5_import_override.md#override-reference-flags
"""

import struct

from ..base.text_reader import get_int

#: TES4 signatures of placed references.
REF_SIGS = frozenset({'REFR', 'ACHR', 'ACRE'})

DELETED = 0x20
PERSISTENT = 0x400
INITIALLY_DISABLED = 0x800
VISIBLE_WHEN_DISTANT = 0x8000

#: Header bits an override takes from its author; neither decides the record's group.
TAKEN = INITIALLY_DISABLED | VISIBLE_WHEN_DISTANT


def merge_flags(base: int, master_export: int, plugin_export: int) -> int:
    """`base` with the TAKEN bits the author changed set to the plugin's value.

    `base` comes back as it is when the author changed Persistent as well (the
    record would belong in its cell's other children group) or the master's
    record is a deleted stub (there is nothing to compare the plugin with).
    """
    changed = master_export ^ plugin_export
    if changed & PERSISTENT or master_export & DELETED:
        return base
    authored = changed & TAKEN
    return (base & ~authored) | (plugin_export & authored)


def with_authored_flags(record: bytes, master_rec: dict, rec: dict) -> bytes:
    """`record` with the header flags the author of a reference override set.

    See: docs/commentary/tes5_import_override.md#override-reference-flags
    """
    if rec.get('Signature') not in REF_SIGS:
        return record
    head = struct.unpack_from('<I', record, 8)[0]
    flags = merge_flags(head, get_int(master_rec, 'RecordFlags'),
                        get_int(rec, 'RecordFlags'))
    return record[:8] + struct.pack('<I', flags) + record[12:]
