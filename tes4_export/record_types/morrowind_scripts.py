"""
Morrowind scripts: SCPT records in the TES4 vocabulary the script converter reads.

See: docs/commentary/tes4_export_morrowind.md#scripts
"""

from core.tes4_encoding import decode

from ..record_types.common import escape_value
from ..tes3_reader import Tes3Record, get_string, get_subrecord

#: TES4 SCHR.Type for an object script, which is how every Morrowind script attaches.
_OBJECT_SCRIPT = 0


def export_SCPT(rec: Tes3Record, ctx) -> list:
    """A script: its source text and the local variables SCVR names in order."""
    lines = [f'EditorID={escape_value(rec.record_id)}',
             f'SCHR.Type={_OBJECT_SCRIPT}']
    sctx = get_subrecord(rec, 'SCTX')
    if sctx is not None:
        lines.append(f'SCTX={escape_value(get_string(sctx))}')
    scvr = get_subrecord(rec, 'SCVR')
    names = [decode(n) for n in scvr.data.split(b'\x00') if n] if scvr else []
    for index, name in enumerate(names):
        lines.append(f'Variable[{index}].Index={index + 1}')
        lines.append(f'Variable[{index}].Name={escape_value(name)}')
    if names:
        lines.append(f'VariableCount={len(names)}')
    return lines


#: TES3 signature -> exporter for scripts.
MORROWIND_SCRIPT_EXPORTERS = {'SCPT': export_SCPT}
