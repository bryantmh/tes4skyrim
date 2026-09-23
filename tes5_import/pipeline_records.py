"""Phases 1-4: convert every record and build the group hierarchy.

Split out of `import_main` (see pipeline.py).  Nothing here calls back into
the driver: `run_record_phases` is the only entry point, and it reads and
fills the `ImportState` the driver passes in.

🛑 Phase 1 is a SERIAL loop on purpose.  A thread pool made converters
that emit companion records (ARMA, aimed-MGEF clones) add them in completion
order, so record order shuffled between runs and the ESM stopped being
byte-reproducible.

See: docs/reference/tes5_import_architecture.md#4-invariants
"""

"""
TES5 Import Orchestrator — Reads TES4 exports and writes TES5 ESM/ESP files.

Handles:
- Reading per-type export files from a directory
- Converting each record using tes5_import converters
- Building proper group hierarchies (CELL/WRLD/DIAL)
- FormID remapping (load order adjustment)
- Writing the final binary file

Usage:
    python -m tes5_import export/Oblivion.esm -o output/Oblivion.esm

Navmesh gathering, scheduling and caching live in `navmesh/pool.py`, which the
cache tag hashes directly -- so any edit there republishes the shared navmesh
cache.  See `tools/navmesh/navmesh_cache_hook.py --check`.
"""

import json
import os
import struct
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from .registry import IMPORT_DISPATCH, TYPE_MAP
from .overrides.nested import (build_nested_overrides)
from .dialogue.quest import compute_quest_priorities, convert_QUST
from .dialogue.morrowind_sidecar import write_morrowind_sidecar
from .record_types.bodypart_falloutnv import write_falloutnv_sidecars
from .record_types.sound import convert_SOUN
from .base.owned_records import (
    WELL_KNOWN_PROPERTIES,
)
from .navmesh.navi import NAVI_SINGLETON_FID, build_navi_record
from .navmesh import pool as navm_pool
from .actors.lava_placement import LavaPlanner
from .base.locations import build_marker_locations
from .record_types.world import (
    convert_ACHR,
    convert_CELL,
    convert_LAND,
    convert_LTEX,
    convert_REFR,
    convert_WRLD,
    restamp_wrld_mnam,
    set_cell_locations,
    set_teleport_grid,
    set_world_land_extents,
    set_door_navmesh_links,
)
from .base.text_reader import (
    get_float,
    get_formid,
    get_formid_index_offset,
    get_int,
    get_str,
    get_injected_formids,
)
from .base.writer import PluginWriter, pack_group

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from output_layout import asset_cache_chain




#: Below this many LAND records a process pool costs more than it saves.
_LAND_PARALLEL_MIN = 64

_LAND_CHUNK = 24

WORLD_EXTENT_CACHE = 'world_extents.json'

def _phase1_simple_records(st, export_dir: str, phase_done, skip_types) -> None:
    """Phase 1: convert every flat top-level record type.

    Serial on purpose, and an override reuses the master's bytes.
    See: docs/commentary/tes5_import_pipeline.md#phase-1-is-serial-on-purpose"""
    print("\nConverting records...")
    st.t2 = time.time()

    simple_types = set()
    for sig in sorted(st.by_type.keys()):
        if sig in st.all_skip:
            continue
        if sig in ('CELL', 'WRLD', 'DIAL', 'INFO', 'REFR', 'ACHR', 'ACRE', 'LAND',
                    'LTEX', 'SOUN', 'PGRD', 'QUST'):
            continue  # Handled separately
        if sig not in IMPORT_DISPATCH:
            continue
        simple_types.add(sig)

    _WRITER_TYPES = {'ARMO', 'CLOT', 'WEAP', 'AMMO', 'NPC_', 'CREA', 'BOOK',
                     'ENCH', 'SPEL', 'SGST', 'HAIR', 'PROJ', 'IPCT', 'IPDS',
                     'EXPL', 'ADDN', 'MUSC'}

    st.converted = 0
    st.errors = 0

    work_items = [(sig, TYPE_MAP.get(sig, sig), rec)
                  for sig in sorted(simple_types)
                  for rec in st.by_type[sig]]

    from .record_types.weather import record_sunless_climate, reset_sunless_climates
    reset_sunless_climates()
    if st.ctx and getattr(st.ctx, 'master_export', None):
        for mrec in st.ctx.master_export.values():
            if (mrec.get('Signature') or '') == 'CLMT':
                record_sunless_climate(mrec)
    for rec in st.by_type.get('CLMT', []):
        record_sunless_climate(rec)

    for sig, target_sig, rec in work_items:
        converter = IMPORT_DISPATCH[sig]
        try:
            src_fid = (rec.get('FormID') or '').upper()

            ov = st.ctx.build(rec, sig) if st.ctx else None
            if ov is not None and ov.status != 'reconvert':
                if ov.record_bytes:
                    st.writer.add_record(
                        ov.record_bytes[:4].decode('ascii', 'replace'),
                        ov.record_bytes)
                    st.converted += 1
                continue

            with st.writer.converting(src_fid, get_formid(rec, 'FormID')):
                if sig in _WRITER_TYPES:
                    record_bytes = converter(rec, writer=st.writer)
                else:
                    record_bytes = converter(rec)
            if not record_bytes:
                continue
            st.writer.add_record(record_bytes[:4].decode('ascii', 'replace'),
                              record_bytes)
            st.converted += 1
        except Exception as e:
            edid = get_str(rec, 'EditorID', '?')
            print(f"  ERROR converting {sig} '{edid}': {e}")
            st.errors += 1
    write_falloutnv_sidecars(st.by_type, st.writer, st.output_path)
    staged = write_morrowind_sidecar(export_dir, st.output_path,
                                     os.path.basename(st.output_path),
                                     writer=st.writer)
    if staged:
        print(f'  Staged {staged} sidecar file(s) for MorrowindRuntime')
    phase_done(f'simple records ({len(work_items)})')


def _convert_ltex(st, export_dir: str, phase_done, skip_types) -> None:
    """Phase 2: LTEX, creating a TXST companion for each."""
    ltex_records = st.by_type.get('LTEX', [])
    if ltex_records:
        print(f"  Converting {len(ltex_records)} LTEX records (with TXST creation)...")
        for rec in ltex_records:
            try:
                ov = st.ctx.build(rec, 'LTEX') if st.ctx else None
                if ov is not None:
                    if ov.record_bytes:
                        st.writer.add_record('LTEX', ov.record_bytes)
                        st.converted += 1
                    continue
                ltex_bytes, txst_bytes, txst_fid = convert_LTEX(rec, st.writer)
                st.writer.add_record('LTEX', ltex_bytes)
                if txst_bytes:
                    st.writer.add_record('TXST', txst_bytes)
                st.converted += 1
            except Exception as e:
                print(f"  ERROR converting LTEX '{get_str(rec, 'EditorID', '?')}': {e}")
                st.errors += 1

    from .record_types.weather import convert_WTHR
    wthr_records = st.by_type.get('WTHR', [])
    if wthr_records:
        print(f"  Converting {len(wthr_records)} WTHR records (with IMGS creation)...")
        for rec in wthr_records:
            try:
                ov = st.ctx.build(rec, 'WTHR') if st.ctx else None
                if ov is not None:
                    if ov.record_bytes:
                        st.writer.add_record('WTHR', ov.record_bytes)
                        st.converted += 1
                    continue
                wthr_bytes, imgs_list = convert_WTHR(rec, st.writer)
                for imgs_bytes in imgs_list:
                    st.writer.add_record('IMGS', imgs_bytes)
                st.writer.add_record('WTHR', wthr_bytes)
                st.converted += 1
            except Exception as e:
                print(f"  ERROR converting WTHR '{get_str(rec, 'EditorID', '?')}': {e}")
                st.errors += 1



def _record_master_sndr(st, rec):
    """Map an overridden SOUN to the MASTER's SNDR companion."""
    from .record_types.sound import record_sndr_for_soun
    try:
        for fid in st.ctx.master_manifest.companions(
                (rec.get('FormID') or '').upper()):
            if st.ctx.master_index.record(fid)[:4] == b'SNDR':
                record_sndr_for_soun(get_formid(rec, 'FormID'), fid)
                return
    except Exception:
        pass


def _convert_soun(st, export_dir: str, phase_done, skip_types) -> None:
    """Phase 3: SOUN, creating an SNDR descriptor for each."""
    soun_records = st.by_type.get('SOUN', [])
    if soun_records:
        print(f"  Converting {len(soun_records)} SOUN records (with SNDR creation)...")
        for rec in soun_records:
            try:
                ov = st.ctx.build(rec, 'SOUN') if st.ctx else None
                if ov is not None:
                    if ov.record_bytes:
                        st.writer.add_record('SOUN', ov.record_bytes)
                        st.converted += 1
                        sndr_ov = st.ctx.build_soun_companion(rec, st.writer)
                        if sndr_ov:
                            st.writer.add_record('SNDR', sndr_ov)
                        _record_master_sndr(st, rec)
                    continue
                soun_bytes, sndr_bytes, sndr_fid = convert_SOUN(rec, st.writer)
                st.writer.add_record('SOUN', soun_bytes)
                if sndr_bytes:
                    st.writer.add_record('SNDR', sndr_bytes)
                st.converted += 1
            except Exception as e:
                print(f"  ERROR converting SOUN '{get_str(rec, 'EditorID', '?')}': {e}")
                st.errors += 1

    compute_quest_priorities(st.by_type)
    st.sge_quest_fids = set()


def _convert_qust(st, export_dir: str, phase_done, skip_types) -> None:
    """Phase 3b: QUST, tracking StartGameEnabled ids for the .seq file."""
    qust_records = st.by_type.get('QUST', [])
    if qust_records and 'QUST' not in st.all_skip:
        print(f"  Converting {len(qust_records)} QUST records...")
        for rec in qust_records:
            try:
                ov = st.ctx.build(rec, 'QUST') if st.ctx else None
                if ov is not None:
                    if ov.record_bytes:
                        st.writer.add_record('QUST', ov.record_bytes)
                        st.converted += 1
                    continue

                qust_bytes = convert_QUST(rec, fid_to_edid=st.fid_to_edid,
                                          well_known_props=WELL_KNOWN_PROPERTIES,
                                          unlock_plan=st.unlock_plan,
                                          unlock_globals=st.unlock_globals,
                                          pack_plan=st.pack_plan, xref=st.xref,
                                          script_vars=st._script_vars)
                st.writer.add_record('QUST', qust_bytes)
                st.converted += 1
                fid = get_formid(rec, 'FormID')
                flags = get_int(rec, 'DATA.Flags')
                if flags & 0x01:
                    st.sge_quest_fids.add(fid)
            except Exception as e:
                print(f"  ERROR converting QUST '{get_str(rec, 'EditorID', '?')}': {e}")
                st.errors += 1


def _convert_pack(st, export_dir: str, phase_done, skip_types) -> None:
    """Phase 3b2: PACK, then the phase-3c LCTN pass."""
    pack_records = st.by_type.get('PACK', [])
    if pack_records and 'PACK' not in st.all_skip:
        from .packages.converter import convert_PACK_records
        print(f"  Converting {len(pack_records)} PACK records...")
        for rec in pack_records:
            try:
                ov = st.ctx.build(rec, 'PACK') if st.ctx else None
                if ov is not None:
                    if ov.record_bytes:
                        st.writer.add_record('PACK', ov.record_bytes)
                        st.converted += 1
                    continue
                for _pack_bytes in convert_PACK_records(rec, st.pack_ctx):
                    st.writer.add_record('PACK', _pack_bytes)
                st.converted += 1
            except Exception as e:
                print(f"  ERROR converting PACK "
                      f"'{get_str(rec, 'EditorID', '?')}': {e}")
                st.errors += 1

    phase_done('LTEX/SOUN/QUST/PACK')


def _phases23_records(st, export_dir: str, phase_done,
                      skip_types) -> None:
    """Phases 2-3: LTEX, SOUN, QUST and PACK."""
    _convert_ltex(st, export_dir, phase_done, skip_types)
    _convert_soun(st, export_dir, phase_done, skip_types)
    _convert_qust(st, export_dir, phase_done, skip_types)
    _convert_pack(st, export_dir, phase_done, skip_types)



def _phase4a_navmesh(st, export_dir: str, phase_done, skip_types) -> None:
    """Phase 4a: generate navmeshes, then the three ordered post-passes.

    See: docs/commentary/tes5_import_pipeline.md#phase-4-navmesh-post-passes"""
    def _is_own_record(rec) -> bool:
        """True if THIS plugin owns the record, by raw index byte."""
        try:
            return (int(rec.get('FormID', '0'), 16) >> 24) >= st.num_tes4_masters
        except (ValueError, TypeError):
            return False

    _owns_new_world = any(_is_own_record(r) for sig in ('CELL', 'WRLD')
                          for r in st.by_type.get(sig, []))
    if not st.ctx or _owns_new_world:
        set_cell_locations(*build_marker_locations(st.by_type, st.writer))

    set_teleport_grid(*navm_pool.build_teleport_grid(
        st.by_type, st.ctx.master_export if st.ctx else None))


    _navm_master_export = st.ctx.master_export if st.ctx else None
    st.base_model_by_fid = navm_pool.build_base_model_index(st.by_type,
                                                         _navm_master_export)
    st.door_fids = navm_pool.build_door_fid_set(st.by_type, _navm_master_export)
    st.navm_metas = []

    st.navm_cache = navm_pool.precompute_navmeshes(
        st.by_type, st.writer, st.base_model_by_fid, st.door_fids,
        collision_cache=navm_pool.collision_cache_chain(export_dir),
        master_index=getattr(st.ctx, 'master_index', None) if st.ctx else None,
        master_export=getattr(st.ctx, 'master_export', None) if st.ctx else None)

    _dump_to = os.environ.get('TESCONV_DUMP_NAVM_CACHE', '').strip()
    if _dump_to:
        import pickle as _pickle
        with open(_dump_to, 'wb') as _fh:
            _pickle.dump(st.navm_cache, _fh, protocol=_pickle.HIGHEST_PROTOCOL)
        print(f"  [debug] navm_cache dumped to {_dump_to} "
              f"({len(st.navm_cache)} entries)")

    from .navmesh.edge_links import build_edge_links
    _t_el = time.time()
    _mi = getattr(st.ctx, 'master_index', None) if st.ctx else None
    _relinked = []
    build_edge_links(
        st.navm_cache, master_index=_mi,
        master_navms=navm_pool.master_navm_grid(
            getattr(st.ctx, 'master_export', None) if st.ctx else None,
            _mi),
        relinked_masters=_relinked)
    if st.ctx is not None:
        st.ctx.relinked_master_navms = _relinked
    print(f"    Edge links: {time.time() - _t_el:.1f}s")

    from .navmesh.split import split_disconnected_interiors
    _t_sp = time.time()
    door_xtel_target = {}
    _xtel_sources = [st.ctx.master_export.items()] if (
        st.ctx and getattr(st.ctx, 'master_export', None)) else []
    _xtel_sources.append((r.get('FormID', ''), r) for r in st.by_type.get('REFR', []))
    for fid_str, rec in (p for src in _xtel_sources for p in src):
        if 'XTEL.Door' not in rec:
            continue
        if not fid_str or rec.get('Signature', 'REFR') != 'REFR':
            continue
        tgt = get_formid(rec, 'XTEL.Door')
        if tgt:
            src_fid = get_formid({'FormID': fid_str}, 'FormID')
            if src_fid:
                door_xtel_target[src_fid] = tgt
    n_split = split_disconnected_interiors(st.navm_cache, st.writer, door_xtel_target)
    print(f"    Interior split: {time.time() - _t_sp:.1f}s")
    if n_split:
        print(f"  Navmesh split: {n_split} interior meshes split into "
              f"per-component NAVMs")

    door_xndp = {}
    for _key, (_bytes, _meta) in st.navm_cache.items():
        if _meta:
            door_xndp.update(_meta.get('door_xndp') or {})
    set_door_navmesh_links(door_xndp)
    print(f"  Navmesh door links: {len(door_xndp)} doors bound to a "
          f"navmesh triangle (XNDP)")
    phase_done('navmesh generation')


def run_record_phases(st, export_dir: str, phase_done,
                      skip_types) -> None:
    """Phases 1-4: convert records, then build the cell and world groups."""
    _phase1_simple_records(st, export_dir, phase_done, skip_types)
    _phases23_records(st, export_dir, phase_done, skip_types)
    _phase4a_navmesh(st, export_dir, phase_done, skip_types)

    land_cache = _precompute_land(st.by_type, export_dir)
    if st.ctx:
        st.ctx.land_cache = land_cache
        st.ctx.navm_cache = st.navm_cache
        st.ctx.navm_metas = st.navm_metas
    phase_done('LAND conversion')

    world_sigs = ('CELL', 'WRLD', 'REFR', 'ACHR', 'ACRE', 'LAND', 'PGRD')

    ext_by_wrld = defaultdict(list)
    for cell in st.by_type.get('CELL', []):
        wrld_fid = get_formid(cell, 'ParentWRLD')
        if wrld_fid:
            ext_by_wrld[wrld_fid].append(cell)
    extents = _land_extents_by_wrld(ext_by_wrld) if ext_by_wrld else {}
    if st.output_root:
        extents = _merge_world_extents(st.output_root, extents)
    if extents:
        set_world_land_extents(extents)

    if st.ctx:
        unattached = build_nested_overrides(
            st.by_type, world_sigs, st.ctx, st.writer, 'CELL/WRLD/REFR')
        phase_done('CELL+WRLD overrides')

        if unattached:
            own = defaultdict(list)
            for sig, rec in unattached:
                own[sig].append(rec)
            print(f"  Building this plugin's OWN cell hierarchy "
                  f"({sum(len(v) for v in own.values())} records: "
                  + ', '.join(f'{len(own[s])} {s}' for s in world_sigs
                              if own.get(s)) + ")")
            _build_cell_groups(own, st.writer, st.navm_metas, st.base_model_by_fid,
                               st.door_fids, st.navm_cache, land_cache)
            phase_done('own CELL groups')
            _build_world_groups(own, st.writer, st.navm_metas, st.base_model_by_fid,
                                st.door_fids, st.navm_cache, land_cache, ctx=st.ctx)
            phase_done('own WRLD groups')
    else:
        _build_cell_groups(st.by_type, st.writer, st.navm_metas, st.base_model_by_fid,
                           st.door_fids, st.navm_cache, land_cache)
        phase_done('CELL groups')
        _build_world_groups(st.by_type, st.writer, st.navm_metas, st.base_model_by_fid,
                            st.door_fids, st.navm_cache, land_cache)
        phase_done('WRLD groups')

    if st.navm_metas:
        n_edge = sum(len(m.get('edge_link_fids') or ()) for m in st.navm_metas)
        n_door = sum(len(m.get('door_refs') or ()) for m in st.navm_metas)
        from .navmesh.navi import read_master_nvpp
        from asset_convert.sources.skyrim_assets import find_skyrim_data
        master_nvpp = read_master_nvpp(find_skyrim_data())
        navi_bytes = build_navi_record(NAVI_SINGLETON_FID, st.navm_metas,
                                       master_nvpp=master_nvpp)
        if navi_bytes:
            st.writer.add_raw_group('NAVI', navi_bytes)
        print(f"  Built NAVI: {len(st.navm_metas)} navmeshes registered "
              f"({n_edge} edge links, {n_door} door links, "
              f"NVPP {'vanilla' if master_nvpp else 'EMPTY - no SSE install'})")



def _is_persistent(rec) -> bool:
    """True if the record carries the TES4 Persistent record flag."""
    return bool(get_int(rec, 'RecordFlags') & 0x400)


def _index_by_parent_cell(records) -> dict:
    """Group records into {parent CELL FormID: [record, ...]}."""
    by_cell = defaultdict(list)
    for rec in records:
        by_cell[get_formid(rec, 'ParentCELL')].append(rec)
    return by_cell


def _pack_cell_children(cell_fid, refr_by_cell, achr_by_cell,
                        leading=(), trailing=()):
    """Pack one cell's children as (group 6 payload, records converted).

    Persistent REFR/ACHR go in a type-8 group, everything else in a type-9
    group.  The type-9 order is fixed and load-bearing: `leading` (LAND) comes
    first, then the non-persistent refs, then `trailing` (lava, navmeshes).

    See: docs/commentary/tes5_import_pipeline.md#cell-world-group-builders
    """
    converted = 0
    children_parts = []
    persistent = []
    for refr_rec in refr_by_cell.get(cell_fid, []):
        if _is_persistent(refr_rec):
            persistent.append(convert_REFR(refr_rec))
            converted += 1
    for achr_rec in achr_by_cell.get(cell_fid, []):
        if _is_persistent(achr_rec):
            persistent.append(convert_ACHR(achr_rec))
            converted += 1
    if persistent:
        children_parts.append(pack_group(
            8, struct.pack('<I', cell_fid), b''.join(persistent)))

    temporary = list(leading)
    converted += len(temporary)
    for refr_rec in refr_by_cell.get(cell_fid, []):
        if not _is_persistent(refr_rec):
            temporary.append(convert_REFR(refr_rec))
            converted += 1
    for achr_rec in achr_by_cell.get(cell_fid, []):
        if not _is_persistent(achr_rec):
            temporary.append(convert_ACHR(achr_rec))
            converted += 1
    temporary.extend(trailing)
    converted += len(trailing)
    if temporary:
        children_parts.append(pack_group(
            9, struct.pack('<I', cell_fid), b''.join(temporary)))

    if not children_parts:
        return b'', converted
    return (pack_group(6, struct.pack('<I', cell_fid),
                       b''.join(children_parts)), converted)


def _cell_temporaries(cell_rec, cell_fid, lava, land_cache, land_by_cell,
                      pgrd_by_cell, navm_cache, navm_metas):
    """One cell's non-ref temporary content as (leading, trailing).

    LAND leads the type-9 group; lava and the PGRD-derived navmeshes trail the
    refs.  Both halves are returned already converted, so the caller never
    needs the order rules.

    See: docs/commentary/tes5_import_pipeline.md#phase-4-navmesh-post-passes
    """
    leading = [_land_bytes(land_cache, land_rec)
               for land_rec in land_by_cell.get(cell_fid, [])]
    trailing = []
    lava_refr = lava.refr_for(cell_rec)
    if lava_refr:
        trailing.append(lava_refr)
    for pgrd_rec in pgrd_by_cell.get(cell_fid, []):
        navm_bytes, meta = navm_cache.get(
            (cell_fid, get_formid(pgrd_rec, 'FormID')), (None, None))
        if not navm_bytes:
            continue
        trailing.append(navm_bytes)
        navm_metas.append(meta)
        for xb, xm in meta.get('extra_navms', ()):
            trailing.append(xb)
            navm_metas.append(xm)
    return leading, trailing


def _build_cell_groups(by_type: dict, writer: PluginWriter,
                       navm_metas: list = None, base_model_by_fid: dict = None,
                       door_fids: set = None, navm_cache: dict = None,
                       land_cache: dict = None):
    """Build CELL group hierarchy (interior cells only — exterior in WRLD)."""
    if navm_metas is None:
        navm_metas = []
    if base_model_by_fid is None:
        base_model_by_fid = {}
    if navm_cache is None:
        navm_cache = {}
    lava = LavaPlanner(by_type, writer)
    cells = by_type.get('CELL', [])
    refrs = by_type.get('REFR', [])
    achrs = by_type.get('ACHR', []) + by_type.get('ACRE', [])
    lands = by_type.get('LAND', [])
    pgrds = by_type.get('PGRD', [])

    refr_by_cell = _index_by_parent_cell(refrs)
    achr_by_cell = _index_by_parent_cell(achrs)
    land_by_cell = _index_by_parent_cell(lands)
    pgrd_by_cell = _index_by_parent_cell(pgrds)

    interior_cells = [c for c in cells if not get_formid(c, 'ParentWRLD')]

    if not interior_cells:
        return

    print(f"  Building CELL hierarchy ({len(interior_cells)} interior cells)...")

    blocks = defaultdict(lambda: defaultdict(list))
    for cell in interior_cells:
        object_id = get_formid(cell, 'FormID') & 0xFFFFFF
        block_num = object_id % 10
        sub_block_num = (object_id // 10) % 10
        blocks[block_num][sub_block_num].append(cell)

    all_cell_parts = []
    converted = 0

    for block_num in sorted(blocks.keys()):
        block_parts = []
        for sub_block_num in sorted(blocks[block_num].keys()):
            sub_block_parts = []
            for cell_rec in blocks[block_num][sub_block_num]:
                cell_fid = get_formid(cell_rec, 'FormID')
                try:
                    cell_bytes = convert_CELL(cell_rec)
                    sub_block_parts.append(cell_bytes)

                    leading, trailing = _cell_temporaries(
                        cell_rec, cell_fid, lava, land_cache, land_by_cell,
                        pgrd_by_cell, navm_cache, navm_metas)
                    child_bytes, n = _pack_cell_children(
                        cell_fid, refr_by_cell, achr_by_cell,
                        leading, trailing)
                    converted += n
                    if child_bytes:
                        sub_block_parts.append(child_bytes)

                    converted += 1
                except Exception as e:
                    print(f"  ERROR building CELL group for {get_str(cell_rec, 'EditorID', '?')}: {e}")

            if sub_block_parts:
                block_parts.append(pack_group(3, struct.pack('<i', sub_block_num), b''.join(sub_block_parts)))
        if block_parts:
            all_cell_parts.append(pack_group(2, struct.pack('<i', block_num), b''.join(block_parts)))

    if lava.placed:
        lava.emit_stat()

    if all_cell_parts:
        writer.add_raw_group('CELL', b''.join(all_cell_parts))

    print(f"    Interior cells: {len(interior_cells)}, children: {converted}")
    if lava.placed:
        print(f"    Lava surfaces placed (interior): {lava.placed}")


def _anchor_worldspaces(worlds, cells, ctx) -> dict:
    """ParentWRLD (output space) -> the WRLD bytes anchoring it, or b''.

    Worldspaces THIS plugin defines are built from its own WRLD records; only
    the rest need the master's as an anchor.  The master's bytes carry the
    master's narrow map rectangle, and the LAST plugin to override a WRLD
    wins, so the MNAM is re-stamped or one verbatim anchor would undo every
    other plugin's widened map.

    See: docs/commentary/tes5_import_pipeline.md#cell-world-group-builders
    """
    anchor_wrld = {}
    if not cells or ctx is None:
        return anchor_wrld
    master_index = getattr(ctx, 'master_index', None)
    emitted = getattr(ctx, 'emitted_wrld', None) or {}
    own_wrld = {get_formid(w, 'FormID') for w in worlds}
    wanted = {get_formid(c, 'ParentWRLD') for c in cells} - own_wrld
    wanted.discard(0)
    already_anchored = getattr(ctx, 'anchored_wrld', None) or set()
    for out_fid in sorted(wanted):
        if out_fid in emitted or out_fid in already_anchored:
            anchor_wrld[out_fid] = b''
            continue
        rec = master_index.record(out_fid) if master_index else b''
        if rec and rec[:4] == b'WRLD':
            anchor_wrld[out_fid] = restamp_wrld_mnam(rec, out_fid)
        else:
            n = sum(1 for c in cells
                    if get_formid(c, 'ParentWRLD') == out_fid)
            print(f"  WARNING: {n} new exterior cells name worldspace "
                  f"{out_fid:08X}, which has no converted master record "
                  f"— they cannot be placed and are SKIPPED")
    return anchor_wrld


def _rehome_misplaced_refs(indexes, cell_grid, grid_cell,
                           grid_cell_raw) -> int:
    """Move each ref to the cell its POSITION falls in.  Returns the count.

    `ParentCELL` is kept in sync as a raw TES4-space string: `get_formid`
    remaps on every read, so storing an already-remapped int would double-
    remap, and downstream code reads the field directly.

    See: docs/commentary/tes5_import_pipeline.md#cell-world-group-builders
    """
    rehomed = 0
    for by_cell in indexes:
        for cell_fid in list(by_cell):
            grid = cell_grid.get(cell_fid)
            if grid is None:
                continue
            wrld_fid, cx, cy = grid
            kept = []
            for rec in by_cell[cell_fid]:
                target = None
                if get_str(rec, 'PosX'):
                    gx = int(get_float(rec, 'PosX') // 4096.0)
                    gy = int(get_float(rec, 'PosY') // 4096.0)
                    if (gx, gy) != (cx, cy):
                        target = grid_cell.get((wrld_fid, gx, gy))
                if target and target != cell_fid:
                    rec['ParentCELL'] = grid_cell_raw[(wrld_fid, gx, gy)]
                    by_cell[target].append(rec)
                    rehomed += 1
                    continue
                kept.append(rec)
            by_cell[cell_fid] = kept
    return rehomed


def _pack_exterior_blocks(exterior_cells, ctx):
    """Pack a worldspace's exterior cells into type-4/5 block groups.

    Blocks are floor(grid / 32) and sub-blocks floor(grid / 8), each labelled
    `<hh>` as (y, x); cells sort by (Y, X) within a sub-block.  Returns
    (list of type-4 group payloads, records converted).

    See: docs/commentary/tes5_import_pipeline.md#cell-world-group-builders
    """
    converted = 0
    ext_blocks = defaultdict(lambda: defaultdict(list))
    for cell in exterior_cells:
        grid_x = get_int(cell, 'XCLC.X')
        grid_y = get_int(cell, 'XCLC.Y')
        block_label = struct.pack('<hh', grid_y // 32, grid_x // 32)
        sub_label = struct.pack('<hh', grid_y // 8, grid_x // 8)
        ext_blocks[block_label][sub_label].append(cell)

    out = []
    for block_label in sorted(ext_blocks.keys(), key=navm_pool.grid_sort_key):
        block_parts = []
        for sub_label in sorted(ext_blocks[block_label].keys(),
                                key=navm_pool.grid_sort_key):
            sub_parts = []
            for cell_rec in sorted(
                    ext_blocks[block_label][sub_label],
                    key=lambda c: (get_int(c, 'XCLC.Y'), get_int(c, 'XCLC.X'))):
                converted += _pack_exterior_cell(cell_rec, sub_parts, ctx)
            if sub_parts:
                block_parts.append(pack_group(5, sub_label, b''.join(sub_parts)))
        if block_parts:
            out.append(pack_group(4, block_label, b''.join(block_parts)))
    return out, converted


def _pack_exterior_cell(cell_rec, sub_parts, ctx) -> int:
    """Append one exterior CELL and its children to `sub_parts`.

    Returns the number of records converted.

    See: docs/commentary/tes5_import_pipeline.md#cell-world-group-builders
    """
    cell_fid = get_formid(cell_rec, 'FormID')
    sub_parts.append(convert_CELL(cell_rec))
    leading, trailing = _cell_temporaries(
        cell_rec, cell_fid, ctx['lava'], ctx['land_cache'],
        ctx['land_by_cell'], ctx['pgrd_by_cell'], ctx['navm_cache'],
        ctx['navm_metas'])
    child_bytes, converted = _pack_cell_children(
        cell_fid, ctx['refr_by_cell'], ctx['achr_by_cell'],
        leading, trailing)
    if child_bytes:
        sub_parts.append(child_bytes)
    return converted + 1


def _build_one_world(wrld_fid, wrld_rec, anchor_wrld, ctx):
    """Build one worldspace's records as (list of payloads, converted).

    The WRLD record leads, followed by its type-1 children group holding the
    persistent cells and the exterior block tree.

    See: docs/commentary/tes5_import_pipeline.md#cell-world-group-builders
    """
    converted = 0
    wrld_bytes = (convert_WRLD(wrld_rec) if wrld_rec is not None
                  else anchor_wrld[wrld_fid])
    wrld_children = []

    persistent_cells = []
    exterior_cells = []
    for cell in ctx['ext_cells_by_wrld'].get(wrld_fid, []):
        if _is_persistent(cell):
            persistent_cells.append(cell)
        else:
            navm_pool.ensure_cell_grid(cell)
            exterior_cells.append(cell)

    for persistent_cell in persistent_cells:
        pcell_fid = get_formid(persistent_cell, 'FormID')
        wrld_children.append(convert_CELL(persistent_cell))
        pcell_bytes, n = _pack_cell_children(
            pcell_fid, ctx['refr_by_cell'], ctx['achr_by_cell'])
        converted += n
        if pcell_bytes:
            wrld_children.append(pcell_bytes)

    if exterior_cells:
        blocks, n = _pack_exterior_blocks(exterior_cells, ctx)
        converted += n
        wrld_children.extend(blocks)

    parts = []
    if wrld_bytes:
        parts.append(wrld_bytes)
    if wrld_children:
        label_fid = (wrld_fid if wrld_rec is not None
                     else (struct.unpack_from('<I', wrld_bytes, 12)[0]
                           if wrld_bytes else wrld_fid))
        parts.append(pack_group(1, struct.pack('<I', label_fid),
                                b''.join(wrld_children)))
    return parts, converted + 1


def _build_world_groups(by_type: dict, writer: PluginWriter,
                        navm_metas: list = None, base_model_by_fid: dict = None,
                        door_fids: set = None, navm_cache: dict = None,
                        land_cache: dict = None, ctx=None):
    """Build WRLD group hierarchy (worldspaces + exterior cells)."""
    if navm_metas is None:
        navm_metas = []
    if base_model_by_fid is None:
        base_model_by_fid = {}
    if door_fids is None:
        door_fids = set()
    if navm_cache is None:
        navm_cache = {}
    lava = LavaPlanner(by_type, writer)
    worlds = by_type.get('WRLD', [])
    cells = by_type.get('CELL', [])
    refrs = by_type.get('REFR', [])
    achrs = by_type.get('ACHR', []) + by_type.get('ACRE', [])
    lands = by_type.get('LAND', [])
    pgrds = by_type.get('PGRD', [])

    anchor_wrld = _anchor_worldspaces(worlds, cells, ctx)

    if not worlds and not anchor_wrld:
        return

    ext_cells_by_wrld = defaultdict(list)
    for cell in cells:
        wrld_fid = get_formid(cell, 'ParentWRLD')
        if wrld_fid:
            ext_cells_by_wrld[wrld_fid].append(cell)

    set_world_land_extents(_land_extents_by_wrld(ext_cells_by_wrld))

    refr_by_cell = _index_by_parent_cell(refrs)
    achr_by_cell = _index_by_parent_cell(achrs)

    grid_cell = {}       # (wrld, gx, gy) -> output-space cell FormID
    grid_cell_raw = {}   # (wrld, gx, gy) -> cell's raw (TES4-space) FormID string
    cell_grid = {}       # output-space cell FormID -> (wrld, gx, gy)
    for cell in cells:
        if get_int(cell, 'RecordFlags') & 0x400:
            continue
        if not get_str(cell, 'XCLC.X'):
            continue
        key = (get_formid(cell, 'ParentWRLD'),
               get_int(cell, 'XCLC.X'), get_int(cell, 'XCLC.Y'))
        fid = get_formid(cell, 'FormID')
        grid_cell.setdefault(key, fid)
        grid_cell_raw.setdefault(key, cell.get('FormID'))
        cell_grid[fid] = key
    rehomed = _rehome_misplaced_refs(
        (refr_by_cell, achr_by_cell), cell_grid, grid_cell, grid_cell_raw)
    if rehomed:
        print(f"  Re-homed {rehomed} misplaced exterior refs to their position's cell")

    land_by_cell = _index_by_parent_cell(lands)
    pgrd_by_cell = _index_by_parent_cell(pgrds)

    if anchor_wrld and worlds:
        print(f"  Building WRLD hierarchy ({len(worlds)} own worldspace(s) "
              f"+ {len(anchor_wrld)} MASTER-owned worldspace(s) anchored "
              f"for this plugin's own cells)...")
    elif anchor_wrld:
        print(f"  Building WRLD hierarchy ({len(anchor_wrld)} MASTER-owned "
              f"worldspace(s) anchored for this plugin's own cells)...")
    else:
        print(f"  Building WRLD hierarchy ({len(worlds)} worldspaces)...")
    converted = 0
    all_wrld_parts = []

    _wrld_jobs = [(get_formid(w, 'FormID'), w) for w in worlds]
    _wrld_jobs += [(fid, None) for fid in anchor_wrld]

    ctx = {
        'lava': lava, 'land_cache': land_cache,
        'land_by_cell': land_by_cell, 'pgrd_by_cell': pgrd_by_cell,
        'navm_cache': navm_cache, 'navm_metas': navm_metas,
        'refr_by_cell': refr_by_cell, 'achr_by_cell': achr_by_cell,
        'ext_cells_by_wrld': ext_cells_by_wrld,
    }
    for wrld_fid, wrld_rec in sorted(_wrld_jobs, key=lambda j: j[0]):
        try:
            parts, n = _build_one_world(
                wrld_fid, wrld_rec, anchor_wrld, ctx)
            all_wrld_parts.extend(parts)
            converted += n
        except Exception as e:
            name = (get_str(wrld_rec, 'EditorID', '?')
                    if wrld_rec is not None
                    else f'master-anchored {wrld_fid:08X}')
            print(f"  ERROR building WRLD group for {name}: {e}")

    if all_wrld_parts:
        writer.add_raw_group('WRLD', b''.join(all_wrld_parts))

    if lava.placed:
        lava.emit_stat()

    print(f"    Worldspaces: {len(_wrld_jobs)}, children: {converted}")
    if lava.placed:
        print(f"    Lava surfaces placed (exterior): {lava.placed}")


def _land_extents_by_wrld(ext_cells_by_wrld: dict) -> dict:
    """Per-worldspace (min_x, min_y, max_x, max_y) over this plugin's LANDs.

        See: docs/commentary/tes5_import_navmesh.md#world-map-camera-clamp-mnams
        """
    extents = {}
    for wrld_fid, cells in ext_cells_by_wrld.items():
        xs = []
        ys = []
        for cell in cells:
            if get_int(cell, 'RecordFlags') & 0x400:      # persistent
                continue
            if not get_str(cell, 'XCLC.X'):
                continue
            xs.append(get_int(cell, 'XCLC.X'))
            ys.append(get_int(cell, 'XCLC.Y'))
        if not xs:
            continue
        extents[wrld_fid] = (min(xs) * 4096.0, min(ys) * 4096.0,
                             (max(xs) + 1) * 4096.0, (max(ys) + 1) * 4096.0)
    return extents


def _merge_world_extents(output_root: str, measured: dict) -> dict:
    """Fold this run's LAND extents into the shared on-disk registry.

        See: docs/commentary/tes5_import_navmesh.md#world-map-camera-clamp-mnams
        """
    path = os.path.join(output_root, WORLD_EXTENT_CACHE)
    stored = {}
    try:
        with open(path, encoding='utf-8') as fh:
            stored = {int(k): tuple(v) for k, v in json.load(fh).items()}
    except (OSError, ValueError, TypeError):
        stored = {}

    merged = dict(stored)
    for fid, rect in measured.items():
        old = merged.get(fid)
        if old is None:
            merged[fid] = tuple(rect)
        else:
            merged[fid] = (min(old[0], rect[0]), min(old[1], rect[1]),
                           max(old[2], rect[2]), max(old[3], rect[3]))

    if merged != stored:
        try:
            os.makedirs(output_root, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump({str(k): list(v) for k, v in sorted(merged.items())},
                          fh, indent=1)
        except OSError:
            pass
    return merged


def _precompute_land(by_type: dict, export_dir: str) -> dict:
    """Convert every LAND up front, in a pool.  Returns {fid: bytes}.

        See: docs/commentary/tes5_import_pipeline.md#phase-4-navmesh-post-passes
        """
    lands = by_type.get('LAND', [])
    if len(lands) < _LAND_PARALLEL_MIN:
        return None

    from .base import convert_worker
    from .base.locations import WORLD_NAMES
    from .record_types import items as items_mod
    from .record_types import world as world_mod
    from .record_types import world_morrowind as world_mw

    initargs = (
        get_formid_index_offset(),
        dict(world_mod._CELL_LOCATION),
        dict(world_mod._GRID_LOCATION),
        dict(world_mod._WORLD_LOCATION),
        dict(WORLD_NAMES),
        dict(items_mod._BASE_ORIGIN_SHIFT),
        asset_cache_chain(export_dir, 'mesh_bounds_cache.json'),
        get_injected_formids(),
        dict(world_mod._DOOR_NAVMESH_LINK),
        set(world_mod._WORLD_GRID_CELLS),
        dict(world_mod._DOOR_PLACEMENT),
        world_mw.tes3_lock_state(),
    )

    chunks = [[('LAND', rec) for rec in lands[i:i + _LAND_CHUNK]]
              for i in range(0, len(lands), _LAND_CHUNK)]
    workers = navm_pool.navm_worker_count(len(chunks))
    print(f"  Converting {len(lands)} LAND records across {workers} processes...")
    t0 = time.time()

    cache: dict = {}
    with ProcessPoolExecutor(max_workers=workers,
                             initializer=convert_worker.init_worker,
                             initargs=initargs) as ex:
        for results in ex.map(convert_worker.convert_chunk, chunks):
            for (kind, fid), ok, payload in results:
                cache[fid] = (ok, payload)
    print(f"    LAND conversion: {len(lands)} records in {time.time() - t0:.2f}s")
    return cache




def _land_bytes(land_cache: dict, land_rec: dict) -> bytes:
    """Converted bytes for a LAND record, from the precompute cache if present.

    Failed precomputes re-raise here so the builders' existing per-cell error
    handling reports them exactly as the serial path would.
    """
    if land_cache is not None:
        entry = land_cache.pop(get_formid(land_rec, 'FormID'), None)
        if entry is not None:
            ok, payload = entry
            if not ok:
                raise RuntimeError(payload)
            return payload
    return convert_LAND(land_rec)
