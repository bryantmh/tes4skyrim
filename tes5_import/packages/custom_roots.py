"""Building blocks of the converter's own package template roots (PKDT type 19 records with a procedure tree).

Each root copies its procedure inputs, flags and editor names from vanilla templates; this module only lays the
subrecords out. Roots: escort_when_near.py, ambush_near.py, sit_at.py.
See: docs/commentary/tes5_import_package.md#escort-restarts-when-the-target-returns
"""

import struct

from ..base.writer import pack_record, pack_string_subrecord, pack_subrecord

#: CTDA function index of GetWithinDistance.
GET_WITHIN_DISTANCE = 0x027F

#: CTDA function index of GetInFaction.
GET_IN_FACTION = 71

#: CTDA flag: both parameters name package data inputs.
CTDA_USE_PACKDATA = 0x08

#: CTDA Run On value: a package data input, named by the condition's third parameter.
RUN_ON_PACKAGE_DATA = 6

#: CTDA comparison bits: less than.
CTDA_LESS_THAN = 0x80

#: CTDA function index of GetDistance.
GET_DISTANCE = 1

#: PlayerRef, the default single-reference target of a root.
PLAYER_REF = 0x14

#: Root PSDT, verbatim from every vanilla template root.
ROOT_PSDT = bytes.fromhex('ffff00ffff00000000000000')


def procedure(name: str, flags: int, inputs, ctdas=()) -> bytes:
    """One Procedure branch: ANAM CITC CTDA* PNAM FNAM PKC2*; `ctdas` are packed CTDA subrecords, ANDed."""
    out = pack_string_subrecord('ANAM', 'Procedure')
    out += pack_subrecord('CITC', struct.pack('<I', len(ctdas))) + b''.join(ctdas)
    out += pack_string_subrecord('PNAM', name)
    out += pack_subrecord('FNAM', struct.pack('<I', flags))
    return out + b''.join(pack_subrecord('PKC2', bytes([i])) for i in inputs)


def within_distance_gate(target_input: int, radius_input: int, on_input: int = -1) -> bytes:
    """GetWithinDistance(target, radius), run on SingleRef input `on_input` if >= 0."""
    run_on = RUN_ON_PACKAGE_DATA if on_input >= 0 else 0
    return pack_subrecord('CTDA', struct.pack('<B3xfHHIIIIi', CTDA_USE_PACKDATA, 1.0, GET_WITHIN_DISTANCE, 0,
                                              target_input, radius_input, run_on, 0, on_input))


def nearer_than_gate(target_input: int, distance: float) -> bytes:
    """CTDA GetDistance(target) < distance over one package data input."""
    return pack_subrecord('CTDA', struct.pack('<B3xfHHIIIIi', CTDA_LESS_THAN | CTDA_USE_PACKDATA, distance,
                                              GET_DISTANCE, 0, target_input, 0, 0, 0, -1))


def in_faction_gate(faction: int, value: float) -> bytes:
    """CTDA GetInFaction(faction) == value on the subject."""
    return pack_subrecord('CTDA', struct.pack('<B3xfHHIIIIi', 0, value, GET_IN_FACTION, 0, faction, 0, 0, 0, -1))


def branch(kind: str, children, flags: int = 0, ctdas=()) -> bytes:
    """A Stacked / Sequence / Simultaneous node over `children`."""
    out = pack_string_subrecord('ANAM', kind)
    out += pack_subrecord('CITC', struct.pack('<I', len(ctdas))) + b''.join(ctdas)
    return out + pack_subrecord('PRCB', struct.pack('<II', len(children), flags)) + b''.join(children)


def stacked(first: bytes, second: bytes) -> bytes:
    """A Stacked branch running `first` while its condition holds, else `second`."""
    return branch('Stacked', (first, second))


def root_record(fid: int, edid: str, pkdt: bytes, template, inputs_blob: bytes, tree: bytes, names,
                tail: bytes) -> bytes:
    """The PACK root at `fid`: PKDT, PSDT, PKCU, the emitted data inputs, `tree`, the editor names, then `tail`.

    `template` is the root's own Template; `names` holds (UNAM index, label, public flag).
    """
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_subrecord('PKDT', pkdt) + pack_subrecord('PSDT', ROOT_PSDT)
    subs += pack_subrecord('PKCU', struct.pack('<III', len(template.inputs), 0, template.version))
    subs += inputs_blob + tree
    for index, label, public in names:
        subs += pack_subrecord('UNAM', bytes([index]))
        subs += pack_string_subrecord('BNAM', label)
        subs += pack_subrecord('PNAM', struct.pack('<I', public))
    return pack_record('PACK', fid, 0, subs + tail)
