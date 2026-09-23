"""
The Morrowind reference body: the wrap field's source surface and the bind skeleton.

A Morrowind actor's naked body is its race's skin BODY parts hung on the
skeleton, picked as OpenMW `NpcAnimation::getBodyParts` does: skin type,
playable, third person, the actor's own gender first with male parts as the
female fallback, each part on both sides. This assembles them for the
reference race into one NIF per wrap group, labels every vertex with the part
slot it draws, and reads the T-posed bind skeleton every vanilla skinned part
shares from its chest part.

See: docs/commentary/asset_convert_armor.md#morrowind-wrap-field
See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
"""

import os
import struct
from functools import partial

import numpy as np

from asset_convert import paths
from asset_convert.character.morrowind_armor import (PART_ATTACH_NODES, SKELETON_NIFS,
                                                     assemble, bind_worlds,
                                                     rest_skeleton)
from asset_convert.sources import source_registry
from asset_convert.sources.morrowind_assets import resolve_mesh
from tes4_export.tes3_reader import get_string, get_subrecord, read_file

#: Race the reference body is taken from, as Oblivion's field takes the imperial head.
REFERENCE_RACE = 'imperial'

#: BODY BYDT part -> the INDX slots it fills (OpenMW npcanimation.cpp sBodyPartMap).
_PART_SLOTS = {2: (2,), 3: (3,), 4: (4,), 5: (6, 7), 6: (8, 9), 7: (11, 12),
               8: (13, 14), 9: (15, 16), 10: (17, 18), 11: (19, 20),
               12: (21, 22)}

#: INDX slots a skin part can draw; the labels a reference-body vertex can carry.
_LABELLED = frozenset(s for slots in _PART_SLOTS.values() for s in slots)

#: Paired INDX slots, right -> left; +X is the actor's right.
_PAIRS = {6: 7, 8: 9, 11: 12, 13: 14, 15: 16, 17: 18, 19: 20, 21: 22, 23: 24}

#: Wrap group -> the INDX slots assembled into it; every other slot is the body.
_GROUP_SLOTS = {'hands': (6, 7), 'feet': (15, 16)}

#: INDX slot of the chest, whose skinned part carries the bind skeleton.
_CHEST_SLOT = 3

#: Shape-name prefix a part's shapes carry before the attach node name.
_TRI_PREFIX = 'tri '

#: BYDT mesh type of a skin part.
_MT_SKIN = 0

#: BYDT flags: female part, and a part no playable actor wears.
_BPF_FEMALE, _BPF_NOT_PLAYABLE = 0x1, 0x2

#: Id suffix of a first-person part (OpenMW isFirstPersonBodyPart).
_FIRST_PERSON = '1st'

#: The master whose BODY records define the vanilla races.
_MASTER = 'Morrowind.esm'


def _skin_parts(records, race: str) -> list:
    """(part, female, mesh) of every third-person playable skin part of `race`."""
    out = []
    for rec in records:
        if rec.type != 'BODY' or rec.deleted:
            continue
        bydt, race_sub, modl = (get_subrecord(rec, s) for s in ('BYDT', 'FNAM', 'MODL'))
        if None in (bydt, race_sub, modl):
            continue
        part, _vampire, flags, mesh_type = struct.unpack_from('<4B', bydt.data, 0)
        if (mesh_type != _MT_SKIN or flags & _BPF_NOT_PLAYABLE
                or rec.record_id.endswith(_FIRST_PERSON)
                or get_string(race_sub).lower() != race):
            continue
        out.append((part, bool(flags & _BPF_FEMALE), get_string(modl).replace('/', chr(92))))
    return out


def body_slots(records, female: bool, race: str = REFERENCE_RACE) -> dict:
    """{INDX slot: mesh} of the race's naked body, own gender first then male."""
    slots = {}
    parts = _skin_parts(records, race)
    for want_female in ((True, False) if female else (False,)):
        for part, is_female, mesh in parts:
            if is_female != want_female:
                continue
            for slot in _PART_SLOTS.get(part, ()):
                slots.setdefault(slot, mesh)
    return slots


def part_slot_labels(shape, verts) -> np.ndarray:
    """Per vertex, the INDX slot of the body part a reference-body shape draws; -1 if none.

    Shapes are named for their attach node (`Tri Right Upper Leg`); a paired
    part's side is the vertex's own (+X right), since the vanilla `Tri Right`
    leg and foot shapes hold both legs.
    See: docs/commentary/asset_convert_armor.md#morrowind-skin-fill
    """
    name = bytes(shape.name).rstrip(b'\x00').decode('latin-1', 'replace').lower()
    name = name[len(_TRI_PREFIX):] if name.startswith(_TRI_PREFIX) else name
    matches = [(len(node), -slot, slot) for slot, node in PART_ATTACH_NODES.items()
               if slot in _LABELLED and name.startswith(node.lower())]
    if not matches:
        return np.full(len(verts), -1, dtype=np.int16)
    slot = max(matches)[2]
    pair = next(((r, l) for r, l in _PAIRS.items() if slot in (r, l)), None)
    if pair is None:
        return np.full(len(verts), slot, dtype=np.int16)
    return np.where(np.asarray(verts)[:, 0] >= 0, pair[0], pair[1]).astype(np.int16)


def _group_of(slot: int) -> str:
    """The wrap group an INDX slot's part is fitted into."""
    return next((g for g, slots in _GROUP_SLOTS.items() if slot in slots), 'body')


def _master_records():
    """Every record of the registered Morrowind.esm."""
    data_dir = source_registry.directory_for(str(paths.EXPORT), _MASTER)
    if not data_dir:
        raise FileNotFoundError(f'{_MASTER} is not registered')
    return read_file(os.path.join(data_dir, _MASTER))[1]


def bind_skeleton(female: bool) -> dict:
    """{Bip01 bone: 4x4} bind skeleton of the reference race's chest part, or {}."""
    mesh = body_slots(_master_records(), female).get(_CHEST_SLOT)
    path = resolve_mesh([], mesh, paths.EXPORT) if mesh else None
    return bind_worlds(path) if path is not None else {}


def body_groups(gender: str) -> dict:
    """{wrap group: [(name, factory of a fresh assembled Data)]} for one gender."""
    female = gender == 'female'
    nif = (resolve_mesh([], SKELETON_NIFS[female], paths.EXPORT)
           or resolve_mesh([], SKELETON_NIFS[False], paths.EXPORT))
    skel = rest_skeleton(nif, female)
    by_group = {}
    for slot, mesh in sorted(body_slots(_master_records(), female).items()):
        by_group.setdefault(_group_of(slot), []).append((slot, mesh))
    return {group: [(f'{REFERENCE_RACE}_{group}.nif',
                     partial(assemble, skel, parts, [], group, rigid_world=True))]
            for group, parts in by_group.items()}
