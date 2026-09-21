"""
Which sounds each creature mesh folder plays, read off the CREA export.

One behavior project serves every creature sharing a mesh folder, so the
creature sound slots (CSDT) are gathered per folder. A creature may hold no
slots of its own and name another to inherit from (`CSCR`) -- 817 of
Oblivion's 909 CREA records do -- and that other creature, like the SOUN a slot
names, may belong to a MASTER. So the lookup walks the master chain, re-keying
each master's FormIDs into the borrowing plugin's own index space.

See: docs/commentary/tes4_export_morrowind.md#creature-sound-generators
"""

import os

from tes5_import.base.text_reader import parse_export_file
from tes5_import.overrides.nested import (export_master_names, export_root,
                                          master_export_dir)

#: How many `CSCR` hops are followed inside one plugin before giving up.
_MAX_INHERIT_DEPTH = 4

#: The play chance of a slot whose export predates the CSDC field.
_DEFAULT_CHANCE = 100


class _Sounds:
    """One plugin's view: SOUN EditorIDs, per-creature slots, per-folder slots."""

    __slots__ = ('edids', 'slots', 'folders')

    def __init__(self):
        """Start empty; `_load` fills the masters' view in first, then its own."""
        self.edids = {}
        self.slots = {}
        self.folders = {}


def _rekeyed(table: dict, remap: dict) -> dict:
    """`table` with each FormID key moved through `remap`; unreachable keys drop."""
    out = {}
    for fid, value in table.items():
        raw = int(fid, 16)
        mapped = remap.get((raw >> 24) & 0xFF)
        if mapped is not None:
            out['%08X' % ((mapped << 24) | (raw & 0x00FFFFFF))] = value
    return out


def _folder(rec: dict) -> str:
    """The mesh folder a CREA's model sits in, lowercased; '' without one."""
    model = (rec.get('Model.MODL') or '').replace('/', chr(92))
    parts = [p for p in model.lower().split(chr(92)) if p]
    return parts[-2] if len(parts) >= 2 else ''


def _authored_slots(rec: dict) -> dict:
    """{CSDT type: (SOUN FormID, chance)} exactly as one CREA states them."""
    out = {}
    for i in range(int(rec.get('SoundTypeCount', 0) or 0)):
        kind = rec.get(f'SoundType[{i}].Type')
        sound = rec.get(f'SoundType[{i}].Sound')
        chance = rec.get(f'SoundType[{i}].Sound.Chance')
        if kind is not None and sound:
            out[int(kind)] = (sound.upper(), int(chance) if chance is not None
                              else _DEFAULT_CHANCE)
    return out


def _creature_slots(rec: dict, by_fid: dict, view: _Sounds, depth: int = 0) -> dict:
    """{CSDT type: (SOUN EditorID, chance)} for one CREA, following `CSCR`."""
    authored = _authored_slots(rec)
    if authored:
        return {kind: (view.edids[sound], chance)
                for kind, (sound, chance) in authored.items()
                if sound in view.edids}
    source = (rec.get('CSCR.InheritSound') or '').upper()
    if source in by_fid and depth < _MAX_INHERIT_DEPTH:
        return _creature_slots(by_fid[source], by_fid, view, depth + 1)
    return view.slots.get(source, {})


def _adopt_masters(view: _Sounds, export_dir: str, seen: dict) -> None:
    """Fold each master's resolved view into `view`, in this plugin's id space."""
    names = export_master_names(export_dir)
    slot_of = {name.lower(): i for i, name in enumerate(names)}
    root = export_root(export_dir)
    for slot, name in enumerate(names):
        folder = master_export_dir(root, name)
        if not os.path.isdir(folder):
            continue
        theirs = _load(folder, seen)
        own = export_master_names(folder)
        remap = {len(own): slot}
        remap.update({k: slot_of[sub.lower()] for k, sub in enumerate(own)
                      if sub.lower() in slot_of})
        view.edids.update(_rekeyed(theirs.edids, remap))
        view.slots.update(_rekeyed(theirs.slots, remap))
        for key, slots in theirs.folders.items():
            if len(slots) > len(view.folders.get(key, {})):
                view.folders[key] = slots


def _load(export_dir: str, seen: dict) -> _Sounds:
    """One plugin's resolved view, its masters' folded in; memoised in `seen`.

    The richest slot set in a folder wins, since the project is shared across
    it; a plugin's own set beats a master's, and a folder only a master voices
    keeps the master's.
    """
    key = os.path.normcase(os.path.normpath(export_dir))
    if key in seen:
        return seen[key]
    view = seen[key] = _Sounds()
    _adopt_masters(view, export_dir, seen)
    for rec in parse_export_file(os.path.join(export_dir, 'SOUN.txt')):
        if rec.get('FormID') and rec.get('EditorID'):
            view.edids[rec['FormID'].upper()] = rec['EditorID']
    records = list(parse_export_file(os.path.join(export_dir, 'CREA.txt')))
    by_fid = {(r.get('FormID') or '').upper(): r for r in records}
    own_folders = {}
    for rec in records:
        slots = _creature_slots(rec, by_fid, view)
        view.slots[(rec.get('FormID') or '').upper()] = slots
        folder = _folder(rec)
        if folder and len(slots) > len(own_folders.get(folder, {})):
            own_folders[folder] = slots
    view.folders.update(own_folders)
    return view


def sound_data_by_folder(export_dir: str) -> dict:
    """folder(lower) -> {CSDT type: (SOUN EditorID, chance)}."""
    return dict(_load(export_dir, {}).folders)


def sound_slots_by_folder(export_dir: str) -> dict:
    """folder(lower) -> {CSDT type: SOUN EditorID}, the chance-less view."""
    return {folder: {kind: edid for kind, (edid, _c) in slots.items()}
            for folder, slots in sound_data_by_folder(export_dir).items()}
