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

import struct
from dataclasses import replace

from ..base.writer import (pack_record, pack_string_subrecord, pack_subrecord)
from .templates import (ESCORT, T_BOOL, T_FLOAT, T_INT, T_LOCATION,
                        T_SINGLEREF, Template)

#: EditorID of the root; a dependent plugin adopts its master's copy by this name.
ESCORT_WHEN_NEAR_EDID = 'TES4EscortWhenNear'

#: Radius outside which the escorter waits; MQ102 Hadvar/Ralof's open-terrain player escorts write 1500.
NEAR_RADIUS = 1500.0

#: CTDA function index of GetWithinDistance (EscortPlayerWhenNear's Escort branch gate).
_GET_WITHIN_DISTANCE = 0x027F

#: CTDA flag: both parameters name package data inputs.
_CTDA_USE_PACKDATA = 0x08

#: Package data input indices (UNAM) of the escort target, the radius, and Wait's two inputs.
_IN_TARGET, _IN_RADIUS, _IN_WAIT_SECONDS, _IN_STOP_MOVING = 11, 15, 20, 21

#: EscortPlayerWhenNear's Escort procedure inputs (PKC2), verbatim.
_ESCORT_INPUTS = (11, 2, 3, 4, 5, 6, 13, 17, 19)

#: PlayerRef, EscortPlayerWhenNear's default escort target.
_PLAYER_REF = 0x14

#: Root PKDT (type 19) and PSDT, verbatim from EscortPlayerWhenNear.
_ROOT_PKDT = bytes.fromhex('0000000013000204fffe0000')
_ROOT_PSDT = bytes.fromhex('ffff00ffff00000000000000')

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
    defaults={1: 1, 3: ESCORT.defaults[3], 4: NEAR_RADIUS,
              5: ESCORT.defaults[4], 6: ESCORT.defaults[5], 7: 0,
              8: ESCORT.defaults[7], 9: ESCORT.defaults[8], 10: 0.0, 11: 1},
    slots={'target': 0, 'location': 2, 'ride_horse': 7},
)

_installed = {'fid': 0}


def set_escort_template_fid(fid: int) -> None:
    """Record the FormID converted escorts point at; 0 falls back to vanilla Escort."""
    _installed['fid'] = fid


def escort_template() -> Template:
    """The template a converted escort instance uses."""
    fid = _installed['fid']
    return replace(_BASE, formid=fid) if fid else ESCORT


def _procedure(name: str, flags: int, inputs, ctda: bytes = b'') -> bytes:
    """One Procedure branch: ANAM CITC [CTDA] PNAM FNAM PKC2*."""
    out = pack_string_subrecord('ANAM', 'Procedure')
    out += pack_subrecord('CITC', struct.pack('<I', 1 if ctda else 0)) + ctda
    out += pack_string_subrecord('PNAM', name)
    out += pack_subrecord('FNAM', struct.pack('<I', flags))
    return out + b''.join(pack_subrecord('PKC2', bytes([i])) for i in inputs)


def _procedure_tree() -> bytes:
    """Stacked { Escort if GetWithinDistance(target, radius) == 1 ; Wait }."""
    gate = pack_subrecord('CTDA', struct.pack(
        '<B3xfHHIIIIi', _CTDA_USE_PACKDATA, 1.0, _GET_WITHIN_DISTANCE, 0,
        _IN_TARGET, _IN_RADIUS, 0, 0, -1))
    out = pack_string_subrecord('ANAM', 'Stacked')
    out += pack_subrecord('CITC', struct.pack('<I', 0))
    out += pack_subrecord('PRCB', struct.pack('<II', 2, 0))
    out += _procedure('Escort', 1, _ESCORT_INPUTS, gate)
    return out + _procedure('Wait', 0, (_IN_WAIT_SECONDS, _IN_STOP_MOVING))


def escort_root_record(fid: int) -> bytes:
    """The PKDT type-19 root record at `fid`, defaulting its target to the player.

    Imports `converter` here: converter imports this module for escort_template.
    """
    from .converter import Inputs, package_markers
    root_inputs = Inputs(_BASE)
    root_inputs.set_slot(0, (0, _PLAYER_REF, 0))
    subs = pack_string_subrecord('EDID', ESCORT_WHEN_NEAR_EDID)
    subs += pack_subrecord('PKDT', _ROOT_PKDT)
    subs += pack_subrecord('PSDT', _ROOT_PSDT)
    subs += pack_subrecord('PKCU', struct.pack('<III', len(_BASE.inputs), 0,
                                               _BASE.version))
    subs += root_inputs.emit() + _procedure_tree()
    for index, label, public in _INPUT_NAMES:
        subs += pack_subrecord('UNAM', bytes([index]))
        subs += pack_string_subrecord('BNAM', label)
        subs += pack_subrecord('PNAM', struct.pack('<I', public))
    return pack_record('PACK', fid, 0, subs + package_markers())
