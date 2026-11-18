"""The converter's own escort template root: Escort while the target is near, else Wait.

A converted TES4 escort points at this root instead of vanilla's Escort. It is
vanilla EscortPlayerWhenNear (00069665) with its Travel fallback replaced by
vanilla StayAtCurrentLocation's Wait (0 seconds, stop moving): the Escort
branch runs only while GetWithinDistance(target, radius) holds, so it restarts
fresh whenever the escorted target comes back in range, and the escorter stays
put while the target is away.  Every subrecord size and value not named here is
copied from those two vanilla roots.
See: docs/commentary/tes5_import_package.md#escort-restarts-when-the-target-returns
"""

from dataclasses import replace

from .custom_roots import PLAYER_REF, procedure, root_record, stacked, within_distance_gate
from .templates import (ESCORT, T_BOOL, T_FLOAT, T_INT, T_LOCATION,
                        T_SINGLEREF, Template)

#: EditorID of the root; a dependent plugin adopts its master's copy by this name.
ESCORT_WHEN_NEAR_EDID = 'TES4EscortWhenNear'

#: EditorID of the retired escort-hold faction; still written so saves keep its FormID.
ESCORT_HOLD_EDID = 'TES4EscortHold'

#: Radius outside which the escorter waits: both games' fAIEscortWaitDistanceInterior.
NEAR_RADIUS = 512.0

#: The Escort procedure's own Distance to Wait, out of reach: its waiting state does not reliably end for a player.
NEVER_WAIT = 100000.0

#: Distance to Wait for an escorted NPC, which follows only while the escorter waits for it: vanilla Escort's.
NPC_WAIT = ESCORT.defaults[3]

#: The radius for an escorted NPC: it follows only while escorted, so it can never come back into range by itself.
NPC_NEAR_RADIUS = 1000000.0

#: Package data input indices (UNAM) of the escort target, the radius, and Wait's two inputs.
_IN_TARGET, _IN_RADIUS, _IN_WAIT_SECONDS, _IN_STOP_MOVING = 11, 15, 20, 21

#: EscortPlayerWhenNear's Escort procedure inputs (PKC2), verbatim.
_ESCORT_INPUTS = (11, 2, 3, 4, 5, 6, 13, 17, 19)

#: Root PKDT (type 19), verbatim from EscortPlayerWhenNear.
_ROOT_PKDT = bytes.fromhex('0000000013000204fffe0000')

#: Editor names (UNAM index, BNAM label, public flag): EscortPlayerWhenNear's, then DoNothing's Wait pair.
_INPUT_NAMES = (
    (0, 'Destination', 1), (1, 'EscortedActor(s)', 1),
    (2, 'Number of Followers:', 0), (3, 'Destination:', 1),
    (4, 'Distance to Wait for Player:', 1), (5, 'Follower Min Distance:', 0),
    (6, 'Follower Max Distance:', 0), (7, 'Destination', 1),
    (8, 'Initial Location to find Follower(s)', 1), (9, 'Follower(s)', 1),
    (10, 'Follower(s) ObjectList', 0), (11, 'Target to Escort', 0),
    (12, 'Destination', 1), (13, 'RideHorseIfPossible', 1),
    (15, 'Wait if target is outside this radius:', 1),
    (17, 'Preferred Pathing?', 1), (18, 'Run if Behind', 1),
    (19, 'Run if Behind Distance', 1), (20, 'ActualSeconds', 1),
    (21, 'StopMovement', 1),
)

_BASE = Template(
    formid=0, edid=ESCORT_WHEN_NEAR_EDID, xnam=22, version=8,
    index_list=(11, 2, 3, 4, 15, 5, 6, 13, 17, 19, 20, 21),
    inputs=(T_SINGLEREF, T_INT, T_LOCATION, T_FLOAT, T_FLOAT, T_FLOAT,
            T_FLOAT, T_BOOL, T_BOOL, T_FLOAT, T_FLOAT, T_BOOL),
    defaults={1: 1, 3: NEVER_WAIT, 4: NEAR_RADIUS,
              5: ESCORT.defaults[4], 6: ESCORT.defaults[5], 7: 0,
              8: ESCORT.defaults[7], 9: ESCORT.defaults[8], 10: 0.0, 11: 1},
    slots={'target': 0, 'location': 2, 'wait': 3, 'radius': 4, 'ride_horse': 7},
)

_installed = {'fid': 0}


def set_escort_template_fid(fid: int) -> None:
    """Record the root converted escorts use (0 = vanilla Escort); a new run."""
    _installed['fid'] = fid


def escort_template() -> Template:
    """The template a converted escort instance uses."""
    fid = _installed['fid']
    return replace(_BASE, formid=fid) if fid else ESCORT


def _procedure_tree() -> bytes:
    """Stacked { Escort if within the radius ; Wait }."""
    gate = (within_distance_gate(_IN_TARGET, _IN_RADIUS),)
    return stacked(procedure('Escort', 1, _ESCORT_INPUTS, gate),
                   procedure('Wait', 0, (_IN_WAIT_SECONDS, _IN_STOP_MOVING)))


def escort_root_record(fid: int) -> bytes:
    """The PKDT type-19 root record at `fid`, defaulting its target to the player.

    Imports `converter` here: converter imports this module for escort_template.
    See: docs/commentary/tes5_import_package.md#escort-restarts-when-the-target-returns
    """
    from .converter import Inputs, package_markers
    root_inputs = Inputs(_BASE)
    root_inputs.set_slot(0, (0, PLAYER_REF, 0))
    return root_record(fid, ESCORT_WHEN_NEAR_EDID, _ROOT_PKDT, _BASE, root_inputs.emit(), _procedure_tree(),
                       _INPUT_NAMES, package_markers())
