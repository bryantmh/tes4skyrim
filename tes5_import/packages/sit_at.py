"""The converter's own root for a TES4 Travel to one seat: vanilla Sit once the seat is near, else Travel to it.

TES4 Travel whose exact destination is a furniture reference = go and sit in it, wherever it is. Vanilla Sit
(00019715) runs Find beside its Travel, and Find never finds a seat in another cell: Baurus stood on such a
package in the cell next to the chair for 3600 ticks. Here the Sit tree is vanilla's node for node, and only
its Find carries GetDistance(seat) < SIT_REACH, so the Travel beside it takes the actor to the seat first.
The condition reads a SingleRef input (as every vanilla package-data GetDistance does); a TargetSelector there
faults the engine.
See: docs/commentary/tes5_import_package.md#sit-in-a-seat-in-another-cell
"""

from dataclasses import replace

from .custom_roots import branch, nearer_than_gate, procedure, root_record
from .templates import SIT, T_SINGLEREF

#: EditorID of the root; a dependent plugin adopts its master's copy by this name.
SIT_AT_EDID = 'TES4SitAt'

#: Editor index (UNAM) of the root's own SingleRef input naming the seat; Sit's own indices end at 13.
SEAT_UNAM = 14

#: Sit's tree runs while the seat is nearer than this (the converted packages' Sit location radius).
SIT_REACH = 500.0

#: Root PKDT (type 19), verbatim from vanilla Sit.
_ROOT_PKDT = bytes.fromhex('0000000013000200fffe0000')

#: Editor names (UNAM index, BNAM label, public flag), vanilla Sit's.
_INPUT_NAMES = (
    (0, 'Sit Location', 1), (1, 'Search Criteria', 1), (2, 'Chairs', 0), (3, 'Wait Time', 0),
    (4, 'Stop Movement Flag', 0), (6, 'Wander Preferred Path Only?', 0), (8, 'False', 0),
    (9, 'Destination', 1), (10, 'RideHorseIfPossible', 1), (11, 'PreferPreferredPath', 1), (12, 'Target', 1),
    (13, 'Destination', 1), (SEAT_UNAM, 'Seat', 1),
)

#: Sit's data inputs plus the seat as a SingleRef (slot `seat`).
_BASE = replace(SIT, formid=0, edid=SIT_AT_EDID, index_list=SIT.index_list + (SEAT_UNAM,),
                inputs=SIT.inputs + (T_SINGLEREF,), slots={**SIT.slots, 'seat': len(SIT.inputs)})

_installed = {'fid': 0}


def set_sit_at_template_fid(fid: int) -> None:
    """Record the root's FormID; 0 keeps such Travels on vanilla Sit."""
    _installed['fid'] = fid


def sit_at_template():
    """The installed root as a Template, or None when there is none."""
    fid = _installed['fid']
    return replace(_BASE, formid=fid) if fid else None


def _procedure_tree() -> bytes:
    """Vanilla Sit's tree, node for node, with its Find gated on the seat being within SIT_REACH."""
    find = procedure('Find', 0, (0, 1, 2, 8, 0xFF), ctdas=(nearer_than_gate(SEAT_UNAM, SIT_REACH),))
    reach = branch('Sequence', (procedure('Travel', 1, (0, 8, 8)), procedure('Wander', 0, (0, 6, 8, 0xFF))))
    sit = branch('Simultaneous', (procedure('Sit', 1, (2,)), procedure('Wait', 0, (3, 4))), flags=2)
    return branch('Sequence', (branch('Simultaneous', (find, reach)), sit))


def sit_at_root_record(fid: int) -> bytes:
    """The PKDT type-19 root record at `fid`, with vanilla Sit's default criteria (object type 10).

    Imports `converter` here: converter imports this module for sit_at_template.
    """
    from .converter import Inputs, package_markers
    root_inputs = Inputs(_BASE)
    root_inputs.set_slot(0, (3, 0, 500))
    root_inputs.set_slot(1, (2, 10, 0))
    return root_record(fid, SIT_AT_EDID, _ROOT_PKDT, _BASE, root_inputs.emit(), _procedure_tree(),
                       _INPUT_NAMES, package_markers())
