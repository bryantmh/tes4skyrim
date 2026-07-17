"""Process-pool worker for CPU-bound CELL-hierarchy record conversion.

convert_CELL / convert_REFR / convert_ACHR / convert_LAND are pure functions
of the record dict *plus* a handful of module globals that import_main's
Phase 0 populates in the parent process (FormID load-order offset, cell→LCTN
location maps, worldspace display names, furniture origin shifts, mesh-bounds
cache).  This module replays that state into each spawned child via the pool
initializer, then converts record chunks — the same pattern navm_worker uses.

A *process* pool (not threads) is used because the converters are pure-Python
struct/string work that holds the GIL; threads serialise on one core.
"""

from .text_reader import get_formid, set_formid_index_offset


def init_worker(formid_offset: int, cell_loc: dict, grid_loc: dict,
                world_loc: dict, world_names: dict, origin_shift: dict,
                mesh_bounds_path: str):
    """Pool initializer: replay parent-process module state into this child."""
    set_formid_index_offset(formid_offset)

    from .record_types.world import set_cell_locations
    set_cell_locations(cell_loc, grid_loc, world_loc)

    from .locations import WORLD_NAMES
    WORLD_NAMES.clear()
    WORLD_NAMES.update(world_names)

    from .record_types import items
    items._BASE_ORIGIN_SHIFT.clear()
    items._BASE_ORIGIN_SHIFT.update(origin_shift)

    if mesh_bounds_path:
        from .mesh_bounds import load_mesh_bounds
        load_mesh_bounds(mesh_bounds_path, quiet=True)


def convert_chunk(chunk: list) -> list:
    """Convert a chunk of (kind, rec) items.

    Returns a list of (key, ok, payload) aligned with the input:
    key = (kind, converted FormID); payload = record bytes on success or the
    error message on failure (re-raised at assembly time so the builders'
    existing per-cell error handling still owns the reporting).
    """
    from .record_types.world import (convert_ACHR, convert_CELL, convert_LAND,
                                     convert_REFR)
    dispatch = {
        'CELL': convert_CELL,
        'REFR': convert_REFR,
        'ACHR': convert_ACHR,
        'LAND': convert_LAND,
    }
    out = []
    for kind, rec in chunk:
        key = (kind, get_formid(rec, 'FormID'))
        try:
            out.append((key, True, dispatch[kind](rec)))
        except Exception as e:  # noqa: BLE001 — mirrors builders' broad catch
            out.append((key, False, f'{type(e).__name__}: {e}'))
    return out
