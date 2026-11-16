"""The record indexes a PackContext needs, built once per import.

Everything pack_converter asks about a package's targets and locations — what
KIND of thing a ref or a base is, where each base is placed, which cells are
interiors, where each package's runners stand — comes from these tables.
They are built here, in one place, so `import_main` (the real import) and
`tools/esm/pack_audit.py` (the routing census) see the same context and the
audit measures the production routing rather than a stand-in.

Every map is keyed on the RAW LOW-24 FormID, which is identical in a master's
index space and ours, so a master's record is seeded straight from its own
`FormID` field with no remapping.  The masters go in FIRST so this plugin's
own records win — without them a package targeting one of the master's refs
resolves to no signature at all, and _operate_target / the Find branches fall
through to the default (see CLAUDE.md, master-export blindness).
"""

from ..base.master_export import values_of
from ..base.text_reader import get_formid

# Every TES4 base signature a placed REFR/ACHR/ACRE (and so a package target)
# can have.
PLACEABLE_BASE_SIGS = (
    'NPC_', 'CREA', 'ACTI', 'FURN', 'DOOR', 'CONT', 'STAT', 'MISC', 'LIGH',
    'WEAP', 'ARMO', 'CLOT', 'BOOK', 'INGR', 'ALCH', 'KEYM', 'SGST', 'SLGM',
    'AMMO', 'APPA', 'FLOR', 'TREE', 'GRAS', 'SBSP', 'LVLC', 'LVLI', 'SOUN', 'SPEL',
)


def _low24(rec: dict, key: str = 'FormID') -> int:
    """The record's `key` FormID masked to its low 24 bits."""
    return int(rec.get(key, '0') or '0', 16) & 0xFFFFFF


def _build_base_sig(iter_bases) -> dict:
    """BASE fid -> its signature, for every placeable base.

    A package target may be a REFR of any of them, and a type-1 Object-ID
    target names one of these bases directly, where the KIND (actor / item /
    furniture) picks the template.  See pack_converter._find_object_id.
    """
    base_sig = {}
    for sig, r in iter_bases(PLACEABLE_BASE_SIGS):
        try:
            base_sig[_low24(r)] = sig
        except ValueError:
            pass
    return base_sig


def _build_ref_base_sig(iter_bases, base_sig: dict) -> dict:
    """REFR/ACHR/ACRE fid -> the signature of the base it places.

    Lets UseItemAt tell furniture (sit) from a switch/lever/door (activate),
    and lets a Find tell "seek this actor" from "operate this object".
    """
    ref_base_sig = {}
    for sig, r in iter_bases(('REFR',)):
        b = r.get('NAME')
        if not b:
            continue
        try:
            bs = base_sig.get(int(b, 16) & 0xFFFFFF)
            if bs:
                ref_base_sig[_low24(r)] = bs
        except ValueError:
            pass
    for sig, bs in (('ACHR', 'NPC_'), ('ACRE', 'CREA')):
        for _, r in iter_bases((sig,)):
            try:
                ref_base_sig[_low24(r)] = bs
            except ValueError:
                pass
    return ref_base_sig


def _record_actor_placement(r: dict, fid: int, base: int, cell: int,
                            base_cell: dict, base_refs: dict,
                            actor_pos: dict) -> None:
    """Record one placed ACHR/ACRE into the actor-only maps.

    Only non-REFR placements reach here: a base actor's cells, the refs that
    place it, and each ref's position.
    """
    if cell:
        base_cell.setdefault(base, set()).add(cell)
    base_refs.setdefault(base, set()).add(fid)
    actor_pos[fid] = (float(r.get('PosX', 0) or 0),
                      float(r.get('PosY', 0) or 0),
                      float(r.get('PosZ', 0) or 0))


def _build_placements(iter_bases) -> tuple:
    """Return (base_placements, ref_cell, base_cell, actor_pos, base_refs).

    `base_placements` maps raw24 base -> [(ref fid, raw24 cell), ...]: a
    type-1 Object-ID target with exactly ONE placement is that reference; with
    several, their cell is the search ground (PackContext.sole_placement /
    search_ground).  `ref_cell` is where every placed thing stands
    (PackContext.location_reachable); `actor_pos` orders a hunt's seek chain
    nearest-first from the hunter's own placement.
    """
    base_placements = {}
    ref_cell = {}
    base_cell = {}
    actor_pos = {}
    base_refs = {}
    for sig, r in iter_bases(('ACHR', 'ACRE', 'REFR')):
        b = r.get('NAME')
        c = r.get('ParentCELL')
        try:
            fid = _low24(r)
            cell = int(c, 16) & 0xFFFFFF if c else 0
            if b:
                base = int(b, 16) & 0xFFFFFF
                base_placements.setdefault(base, []).append(
                    (get_formid(r, 'FormID'), cell))
                if sig != 'REFR':
                    _record_actor_placement(r, fid, base, cell, base_cell,
                                            base_refs, actor_pos)
            if cell:
                ref_cell[fid] = cell
        except ValueError:
            pass
    return base_placements, ref_cell, base_cell, actor_pos, base_refs


def _build_interior_cells(iter_bases) -> set:
    """Cells carrying CELL DATA flag 0x1.

    The only kind a PLDT type-1 "in cell" location may name (448/448 vanilla
    uses are interiors).
    """
    interior_cells = set()
    for _, r in iter_bases(('CELL',)):
        try:
            if int(r.get('DATA.Flags', '0') or '0') & 0x1:
                interior_cells.add(_low24(r))
        except ValueError:
            pass
    return interior_cells


def _runner_packages(r: dict):
    """Yield each raw24 AIPackage fid listed on an actor base record."""
    n = int(r.get('AIPackageCount', '0') or 0)
    for i in range(n):
        p = r.get(f'AIPackage[{i}]')
        if not p:
            continue
        try:
            yield int(p, 16) & 0xFFFFFF
        except ValueError:
            continue


def _build_pack_runners(iter_bases, base_cell: dict, base_refs: dict) -> tuple:
    """Return (pack_runner_cells, pack_runner_refs): PACK fid -> the cells its
    actors stand in, and the actor refs that run it.

    A TES4 Follow naming a destination in ANOTHER cell must not become a
    Skyrim Escort: Escort walks to the destination and there is no navmesh
    route between two interiors, so the actor never moves.
    """
    pack_runner_cells = {}
    pack_runner_refs = {}
    for _, r in iter_bases(('NPC_', 'CREA')):
        try:
            base = _low24(r)
        except ValueError:
            continue
        cells = base_cell.get(base)
        refs = base_refs.get(base)
        if not cells and not refs:
            continue
        for pk in _runner_packages(r):
            if cells:
                pack_runner_cells.setdefault(pk, set()).update(cells)
            if refs:
                pack_runner_refs.setdefault(pk, set()).update(refs)
    return pack_runner_cells, pack_runner_refs


def build_pack_indexes(by_type: dict, master_export: dict = None) -> dict:
    """Return the keyword arguments for PackContext(...) that describe the
    plugin's records (everything except plan/script_vars/greeting_topic).

    Keys: ref_base_sig, base_sig, base_placements, interior_cells, ref_cell,
    pack_runner_cells.
    """
    def _iter_bases(sigs):
        """Yield (signature, record) for `sigs`, the masters' records first."""
        for sig in sigs:
            for r in values_of(master_export, sig):
                yield sig, r
            for r in by_type.get(sig, []):
                yield sig, r

    base_sig = _build_base_sig(_iter_bases)
    ref_base_sig = _build_ref_base_sig(_iter_bases, base_sig)
    (base_placements, ref_cell, base_cell, actor_pos,
     base_refs) = _build_placements(_iter_bases)
    interior_cells = _build_interior_cells(_iter_bases)
    pack_runner_cells, pack_runner_refs = _build_pack_runners(
        _iter_bases, base_cell, base_refs)

    return dict(ref_base_sig=ref_base_sig, base_sig=base_sig,
                base_placements=base_placements,
                interior_cells=interior_cells, ref_cell=ref_cell,
                pack_runner_cells=pack_runner_cells,
                pack_runner_refs=pack_runner_refs, actor_pos=actor_pos)
