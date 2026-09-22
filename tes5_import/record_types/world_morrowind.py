"""Morrowind-specific placed-reference conversion: the exit-only door lock.

TES3 locks the door face the player activates; TES4 and TES5 lock the doorway,
resolving the lock through the teleport pair.  A TES3 pair locked on its
interior face alone is therefore passable inward in Morrowind and sealed in
both directions here.

See: docs/commentary/tes5_import_world.md#tes3-exit-only-door-locks
"""

from ..dialogue.morrowind_sidecar import is_tes3_export
from .common import get_formid, get_int

#: Placed refs whose authored TES3 lock is exit-only; see register_tes3_locks.
_EXIT_ONLY_LOCKS: set = set()

#: True while converting a TES3 source; set once per plugin at import start.
_IS_TES3_SOURCE: list = []


def _scoped(by_type: dict, master_export: dict, sig: str):
    """Every `sig` record in scope: the masters' plus this plugin's own."""
    if master_export:
        own = {get_formid(rec, 'FormID') for rec in by_type.get(sig, [])}
        yield from (rec for rec in master_export.values()
                    if rec.get('Signature') == sig
                    and get_formid(rec, 'FormID') not in own)
    yield from by_type.get(sig, [])


def _interior_cells(records) -> set:
    """FormIDs of the CELL records flagged interior (DATA bit 0)."""
    return {get_formid(rec, 'FormID') for rec in records
            if get_int(rec, 'DATA.Flags') & 1}


def _teleport_doors(records) -> dict:
    """`{ref FormID: (partner FormID, parent CELL, locked, keyed)}`."""
    out = {}
    for rec in records:
        partner = get_formid(rec, 'XTEL.Door')
        if not partner:
            continue
        out[get_formid(rec, 'FormID')] = (
            partner, get_formid(rec, 'ParentCELL'),
            get_int(rec, 'XLOC.Level', -1) >= 0,
            bool(get_formid(rec, 'XLOC.Key')))
    return out


def register_tes3_locks(by_type: dict, export_dir: str,
                        master_export: dict = None) -> int:
    """Index the exit-only door locks this run must drop; returns the count.

    CLEARS first and fills only for a TES3 source -- the set is module state a
    later plugin in the same run would inherit.  A lock qualifies when its pair
    is locked on ONE face, that face is interior, and it has no key.  Masters'
    refs are indexed too; an unpaired lock is left alone.

    See: docs/commentary/tes5_import_world.md#tes3-exit-only-door-locks
    """
    _EXIT_ONLY_LOCKS.clear()
    _IS_TES3_SOURCE.clear()
    if not is_tes3_export(export_dir):
        return 0
    _IS_TES3_SOURCE.append(True)
    interiors = _interior_cells(_scoped(by_type, master_export, 'CELL'))
    doors = _teleport_doors(_scoped(by_type, master_export, 'REFR'))
    for fid, (partner, cell, locked, keyed) in doors.items():
        if not locked or keyed or cell not in interiors:
            continue
        other = doors.get(partner)
        if other is not None and not other[2]:
            _EXIT_ONLY_LOCKS.add(fid)
    return len(_EXIT_ONLY_LOCKS)


def tes3_lock_state() -> tuple:
    """`(is_tes3, exit-only FormIDs)` for replay into a pool worker."""
    return bool(_IS_TES3_SOURCE), frozenset(_EXIT_ONLY_LOCKS)


def set_tes3_lock_state(state: tuple) -> None:
    """Replay `tes3_lock_state` in a child; without it no worker drops a lock."""
    is_tes3, locks = state or (False, ())
    _IS_TES3_SOURCE.clear()
    if is_tes3:
        _IS_TES3_SOURCE.append(True)
    _EXIT_ONLY_LOCKS.clear()
    _EXIT_ONLY_LOCKS.update(locks)


def is_tes3_source() -> bool:
    """True when this run's source plugin is Morrowind."""
    return bool(_IS_TES3_SOURCE)


def lock_is_exit_only(rec: dict) -> bool:
    """Whether this REFR's authored lock seals only the way out of a cell."""
    return get_formid(rec, 'FormID') in _EXIT_ONLY_LOCKS
