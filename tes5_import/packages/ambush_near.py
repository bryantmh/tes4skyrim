"""The converter's own root for a TES4 Ambush on an actor at a place: Follow the target while it is near, else
Travel to the place and stay there.

TES4 Ambush = wait at the location for the target; once it is within the location radius, act on it (for a
non-hostile ambush, walk up to PTDT.Count). The Follow branch runs only while the target is within the radius
OF THE PLACE (GetWithinDistance run on the Place input; the actor itself stops at the radius's edge) and the
actor is there too (a Follow of a target in another cell never moves an unloaded actor);
otherwise Travel takes the actor to the place, flagged not to complete, so it waits there. Follow's and
Travel's procedure inputs, flags and editor names are vanilla's Follow (00019B2C) and Travel (00016FAA) roots.
See: docs/commentary/tes5_import_package.md#ambush-at-a-place
"""

import struct
from dataclasses import replace

from .custom_roots import PLAYER_REF, procedure, root_record, stacked, within_distance_gate
from .templates import FOLLOW, T_BOOL, T_FLOAT, T_LOCATION, T_SINGLEREF, Template

#: PTDA "Self": the place reference of a location that names none (the check then runs on the actor).
SELF_TARGET = struct.pack('<iii', 6, 0, 0)

#: PLDT types naming a reference (0) or a reference alias (8), and the PTDA types for the same.
_REF_PLACES = {0: 0, 8: 4}


def place_reference(location: bytes) -> bytes:
    """The SingleRef PTDA of the reference a PLDT payload names, else SELF_TARGET."""
    loc_type, value, _radius = struct.unpack('<iIi', location[:12])
    t_type = _REF_PLACES.get(loc_type)
    return struct.pack('<iIi', t_type, value, 0) if t_type is not None and value else SELF_TARGET

#: EditorID of the root; a dependent plugin adopts its master's copy by this name.
AMBUSH_NEAR_EDID = 'TES4AmbushNear'

#: Package data input indices (UNAM): Follow's six, the Travel place, Prefer Preferred Path, radius, place ref.
_IN_TARGET, _IN_PLACE, _IN_PREFERRED, _IN_RADIUS, _IN_PLACE_REF = 0, 10, 12, 14, 16

#: Vanilla Follow's procedure inputs (PKC2), verbatim.
_FOLLOW_INPUTS = (0, 1, 1, 4, 6, 8)

#: Travel's inputs: the place, Follow's Ride Horse (input 6), Prefer Preferred Path.
_TRAVEL_INPUTS = (_IN_PLACE, 6, _IN_PREFERRED)

#: Root PKDT (type 19), verbatim from vanilla Follow.
_ROOT_PKDT = bytes.fromhex('0000040013000200fffe0000')

#: Editor names (UNAM index, BNAM label, public flag): vanilla Follow's, Travel's, then the radius.
_INPUT_NAMES = (
    (0, 'Target to Follow', 1), (1, 'Min Radius:', 1), (2, 'Max Radius:', 1),
    (4, 'Accompany?', 1), (6, 'Ride Horse?', 1), (8, 'Need LOS?', 1),
    (10, 'Place to Travel', 1), (12, 'Prefer Preferred Path?', 1),
    (14, 'Follow while the target is within this radius:', 1), (_IN_PLACE_REF, 'Of this reference:', 1),
)

_BASE = Template(
    formid=0, edid=AMBUSH_NEAR_EDID, xnam=15, version=1,
    index_list=(0, 1, 2, 4, 6, 8, _IN_PLACE, _IN_PREFERRED, _IN_RADIUS, _IN_PLACE_REF),
    inputs=FOLLOW.inputs + (T_LOCATION, T_BOOL, T_FLOAT, T_SINGLEREF),
    defaults={**FOLLOW.defaults, 7: 0, 8: 512.0, 9: SELF_TARGET},
    slots={**FOLLOW.slots, 'location': 6, 'radius': 8, 'place_ref': 9},
)

_installed = {'fid': 0}


def set_ambush_template_fid(fid: int) -> None:
    """Record the root's FormID; 0 keeps such ambushes on Follow."""
    _installed['fid'] = fid


def ambush_template():
    """The installed root as a Template, or None when there is none."""
    fid = _installed['fid']
    return replace(_BASE, formid=fid) if fid else None


def _procedure_tree() -> bytes:
    """Stacked { Follow while the actor and the target are both within the radius of the place ; Travel }."""
    near = within_distance_gate(_IN_TARGET, _IN_RADIUS, on_input=_IN_PLACE_REF)
    there = within_distance_gate(_IN_PLACE_REF, _IN_RADIUS)
    return stacked(procedure('Follow', 0, _FOLLOW_INPUTS, (near, there)),
                   procedure('Travel', 0, _TRAVEL_INPUTS))


def ambush_root_record(fid: int) -> bytes:
    """The PKDT type-19 root record at `fid`, defaulting its target to the player.

    Imports `converter` here: converter imports this module for ambush_template.
    """
    from .converter import Inputs, package_markers
    root_inputs = Inputs(_BASE)
    root_inputs.set_slot(0, (0, PLAYER_REF, 0))
    return root_record(fid, AMBUSH_NEAR_EDID, _ROOT_PKDT, _BASE, root_inputs.emit(), _procedure_tree(),
                       _INPUT_NAMES, package_markers())
