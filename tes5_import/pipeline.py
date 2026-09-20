"""The import orchestrator: read the export, run the phases, write.

Renamed from `import_main` and split three ways (the file was 1,695 code
lines).  This module DRIVES: it sets up the writer and the override context,
runs the phase-0 pre-scans that build every index, then hands an
`ImportState` to `pipeline_records` and `pipeline_finalize`.

The driver calls down and nothing calls up -- neither of the other two
modules imports this one, nor each other.

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

import argparse
import os
import sys
import time

from core.plugin_masters import masters_from_export_header
from asset_convert.game_paths import namespace_for, set_namespace
from .registry import IMPORT_DISPATCH, RUNTIME_ONLY_TYPES, SKIP_TYPES
from .navmesh.pool import collision_cache_chain
from .overrides.nested import (DELETED_FLAG as OVERRIDE_DELETED_FLAG,
                        OverrideContext, detect_injected_records)
from .actors.magic_effects import set_tes4_effect_names
from .dialogue.converter import build_npc_to_vtyp_map
from .base.owned_records import (
    WELL_KNOWN_PROPERTIES,
    create_ambient_gmst_overrides,
    create_chargen_menu_records,
    create_destroyed_formlist,
    create_force_combat_factions,
    create_message_menu_records,
    create_tes4_special_records,
    create_vtyp_records,
)
from .base.equivalents import VTYP_EDID_BY_FID
from .record_types.world import (
    set_cloud_bank_output,
)
from .base.text_reader import (
    get_formid,
    get_int,
    group_records_by_type,
    parse_export_directory,
    set_formid_index_offset,
    set_injected_formids,
)
from .base.writer import PluginWriter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from output_layout import asset_cache_chain, assets_for



from .pipeline_records import run_record_phases
from .pipeline_finalize import run_finalize_phases

#: Record types an MGEF Assoc. Item can name; LVLC covers summon indirection.
_ASSOC_ITEM_SIGS = ('CREA', 'NPC_', 'WEAP', 'ARMO', 'CLOT', 'LIGH', 'LVLC')

#: Synthesized stand-in records -> signature; a mastered plugin adopts the master's.
_TES4_SPECIAL_RECORD_SIGS = {
    'TES4Fame': b'GLOB',
    'TES4Infamy': b'GLOB',
    'TES4GoldFenced': b'GLOB',
    'TES4ControlsDisabled': b'GLOB',
    'TES4CyrodiilCrimeFaction': b'FACT',
}

class ImportState:
    """Everything one import run shares across its phases.

    Honest about genuinely shared state: the phases mutate `st` in place
    rather than threading twenty parameters through twenty signatures.  Each
    phase function names in its docstring what it fills and what it needs.

    See: docs/reference/tes5_import_architecture.md#4-invariants
    """

    __slots__ = ('all_skip', 'output_path', 'plugin_out_dir', 'num_tes4_masters', 'ctx', 'output_root', 'by_type', 'writer', 'npc_to_vtyp', 'unlock_plan', 'unlock_globals', 'fid_to_edid', 'xref', '_script_vars', 'pack_plan', 'pack_ctx', 'converted', 'errors', 't2', 'sge_quest_fids', 'navm_cache', 'navm_metas', 'base_model_by_fid', 'door_fids')

    def __init__(self, **kw):
        """Every field starts None; the caller supplies the run's inputs."""
        for name in self.__slots__:
            setattr(self, name, None)
        for name, value in kw.items():
            setattr(self, name, value)


def _mgef_records_with_masters(by_type: dict, ctx) -> list:
    """Every MGEF this plugin can reference: its own, plus its masters'.

    A dependent plugin normally defines no magic effects and points its items
    at the master's — so the effect index must include them, exactly as the
    outfit index includes the master's wardrobe.  The plugin's own records are
    appended LAST so an override of a master effect wins.
    """
    own = by_type.get('MGEF', [])
    if not ctx or not getattr(ctx, 'master_export', None):
        return own
    from_master = [rec for rec in ctx.master_export.values()
                   if rec.get('Signature') == 'MGEF']
    own_codes = {rec.get('EditorID') for rec in own}
    return [r for r in from_master
            if r.get('EditorID') not in own_codes] + own


def _build_assoc_item_index(by_type: dict, ctx=None) -> tuple:
    """(lvlc_first_entry, formid_signature) for MGEF Assoc. Item resolution.

    Skyrim's magic-effect archetypes are typed about what their Assoc. Item may
    be (`wbMGEFAssocItemDecider`): Summon Creature takes an NPC_, Bound Weapon
    a WEAP or ARMO.  Oblivion's MGEF just stores a FormID and says which KIND
    it is in its flags, so the converter has to look the target up to know
    whether it survives the type check.

    Two summon effects in Oblivion point at an LVLC (leveled creature list),
    which converts to an LVLN — a type Summon Creature does NOT accept.  Their
    first list entry stands in so the spell still summons something.

    The masters' records are indexed too: a dependent plugin's effects name
    the master's creatures, not its own.

    Returns ({lvlc out-FormID: first entry out-FormID}, {out-FormID: TES4 sig}).
    """
    sigs = {}
    lvlc_recs = []

    sources = []
    if ctx and getattr(ctx, 'master_export', None):
        sources.append(r for r in ctx.master_export.values()
                       if r.get('Signature') in _ASSOC_ITEM_SIGS)
    sources.append(r for sig in _ASSOC_ITEM_SIGS for r in by_type.get(sig, []))

    for source in sources:
        for rec in source:
            fid = get_formid(rec, 'FormID')
            if not fid:
                continue
            sig = rec.get('Signature')
            sigs[fid] = sig
            if sig == 'LVLC':
                lvlc_recs.append((fid, rec))

    lvlc_first = {}
    for fid, rec in lvlc_recs:
        best = None
        for i in range(get_int(rec, 'EntryCount')):
            entry = get_formid(rec, f'Entry[{i}].FormID')
            if not entry:
                continue
            level = get_int(rec, f'Entry[{i}].Level', 1)
            if best is None or level < best[0]:
                best = (level, entry)
        if best:
            lvlc_first[fid] = best[1]

    for fid in list(lvlc_first):
        target = lvlc_first[fid]
        for _ in range(len(lvlc_first)):
            if sigs.get(target) != 'LVLC':
                break
            nxt = lvlc_first.get(target)
            if not nxt or nxt == target:
                break
            target = nxt
        lvlc_first[fid] = target if sigs.get(target) != 'LVLC' else 0

    return lvlc_first, sigs


def master_export_dirs(ctx) -> list:
    """The export directory of each TES4 master, in _HEADER.txt order.

    `load_master_export` resolves masters exactly this way (sibling directories
    of the plugin's own export, named after the master file). Both the master's
    VTYP creation and this plugin's adoption of it must read the SAME RACE.txt,
    so the derivation is shared rather than reproduced.
    """
    export_dir = getattr(ctx, 'export_dir', None)
    if not export_dir:
        return []
    header = os.path.join(export_dir, '_HEADER.txt')
    if not os.path.isfile(header):
        return []
    from .overrides.nested import export_root, master_export_dir
    root = export_root(export_dir)
    try:
        with open(header, 'r', encoding='utf-8') as f:
            names = [line.partition('=')[2].strip() for line in f
                     if line.startswith('Master[')]
    except OSError:
        return []
    dirs = [master_export_dir(root, n) for n in names]
    return [d for d in dirs if os.path.isdir(d)]


def _adopt_master_special_records(ctx) -> None:
    """Point the well-known registry at the MASTER's synthesized records.

    `create_tes4_special_records` runs only for a root master, so a dependent
    plugin's registry is empty — yet its converted scripts still declare
    TES4ControlsDisabled / TES4Fame / … properties. An unbound property is None
    at runtime and the first call on it aborts the entire Papyrus function
    (Morroblivion's chargen stage 1 died on `TES4ControlsDisabled.SetValue(1)`
    before it could set JiubSpeak). These records have no TES4 source FormID,
    so the companion manifest cannot name them — find_by_edid exists for
    exactly this case.
    """
    index = getattr(ctx, 'master_index', None)
    if index is None:
        return
    found = 0
    for edid, sig in _TES4_SPECIAL_RECORD_SIGS.items():
        try:
            fid = index.find_by_edid(sig, edid)
        except Exception:
            continue
        if fid:
            WELL_KNOWN_PROPERTIES[edid] = fid
            found += 1
    if found:
        print(f"  Adopted {found} synthesized master records "
              f"(TES4ControlsDisabled, TES4Fame, ...)")

    from .base.equivalents import CUSTOM_VTYP_EDIDS, set_voice_type
    voices = 0

    def _adopt(vtyp_edid: str, race_edid: str, gender: str) -> bool:
        try:
            fid = index.find_by_edid(b'VTYP', vtyp_edid)
        except Exception:
            return False
        if not fid:
            return False
        set_voice_type(race_edid, gender, fid)
        return True

    for vtyp_edid, (race_edid, gender) in CUSTOM_VTYP_EDIDS.items():
        if _adopt(vtyp_edid, race_edid, gender):
            voices += 1

    derived = 0
    try:
        from asset_convert.audio.voice_races import load_race_voices
        from asset_convert.audio.voice_races import vtyp_edid as _vtyp_edid
    except ImportError:
        load_race_voices = None     # asset_convert unavailable — fixed set only
    if load_race_voices is not None:
        for mdir in master_export_dirs(ctx):
            try:
                races = load_race_voices(mdir)
            except OSError:
                continue
            for race_edid, key in sorted(races.by_race_edid.items()):
                for gender in ('Male', 'Female'):
                    if _adopt(_vtyp_edid(key, gender), race_edid, gender):
                        derived += 1
    if voices or derived:
        extra = f"; {derived} from the master's own races" if derived else ''
        print(f"  Adopted {voices + derived} master voice types (VTYP){extra}")




def _reconcile_masters(masters: list, tes4_master_names: list) -> list:
    """`masters` reduced to the export header's list, plus Skyrim.esm.

    The header is the authority on what this plugin was BUILT against; the
    caller's binary-derived list can name files the conversion replaced.
    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    if not tes4_master_names:
        return masters
    wanted = {n.lower() for n in tes4_master_names}
    if [n.lower() for n in masters] == [n.lower() for n in tes4_master_names]:
        return masters
    kept = [m for m in masters
            if m.lower() not in wanted and m.lower() == 'skyrim.esm']
    merged = kept + tes4_master_names
    print(f"  Masters (from export header): {', '.join(merged)}")
    return merged


def _prescan_special_records(by_type: dict, ctx, writer, export_dir: str, _step_done):
    """Create the VTYP/GLOB/FACT support records, or adopt the master's.

    A root master creates them; a plugin WITH TES4 masters adopts the
    master's FormIDs instead, since re-creating them duplicates master
    content and the duplicates compete with the originals.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-dependent-skips-support-records
    """
    _step_t = time.time()
    from .record_types.actor_common import (create_origin_faction, reset_origin_faction)
    reset_origin_faction(getattr(ctx, 'master_index', None))
    if not ctx:
        create_vtyp_records(writer, export_dir, by_type)

        _origin_fact = create_origin_faction(writer)
        print(f"  Plugin-origin faction: {_origin_fact:08X} "
              f"(TES4PluginOriginFaction)")

        create_tes4_special_records(writer)

        create_ambient_gmst_overrides(writer, by_type)
    else:
        _adopt_master_special_records(ctx)
    _step_done('vtyp/special records')


def _prescan_npc_voice_map(by_type: dict, ctx, writer, num_new_masters: int, _step_done):
    """Build and register {NPC FormID -> VTYP FormID}; returns the map.

    Also scans speak-as topics and builds their voiced TACT+REFR
    stand-ins.  The MASTERS' races and actors are fed in: a dependent
    plugin's actors overwhelmingly use them, and without them voice
    routing resolves to a folder that does not exist.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-voice-map-reads-masters
    """
    _vtyp_by_type = by_type
    if ctx and getattr(ctx, 'master_export', None):
        _master_races = [r for r in ctx.master_export.values()
                         if r.get('Signature') == 'RACE']
        if _master_races:
            _vtyp_by_type = dict(by_type)
            _vtyp_by_type['RACE'] = _master_races + by_type.get('RACE', [])
    npc_to_vtyp = build_npc_to_vtyp_map(_vtyp_by_type, num_new_masters,
                                        ctx.master_export if ctx else None)
    from .record_types.actor_common import set_npc_voice_map
    set_npc_voice_map(npc_to_vtyp)

    from .dialogue.speak_as import scan_speak_as_topics
    from .base.conditions import set_speak_as_topics
    _speak_as = scan_speak_as_topics(by_type)
    set_speak_as_topics(_speak_as)
    print(f"  Speak-as topics: {len(_speak_as)} (spoken by a non-actor)")

    from .dialogue.speak_as import build_speaker_activators, reset as _spk_reset
    _spk_reset()
    _n_spk = build_speaker_activators(by_type, writer, npc_to_vtyp,
                                      num_new_masters)
    from .dialogue.speak_as import export_speaker_map, speaker_property_name
    for (_em, _vo), _fid in export_speaker_map().items():
        WELL_KNOWN_PROPERTIES[speaker_property_name(_em, _vo)] = _fid
    print(f"  Speaker activators: {_n_spk} TACT+REFR pairs "
          f"(voiced stand-ins for Say speak-as markers)")
    _step_done('npc voice map')
    return npc_to_vtyp


def _prescan_unlock_plan(by_type: dict, writer, _step_done):
    """Plan the AddTopic unlock gates; returns (plan, globals, ScriptConverter).

    Gated topics get one GLOB each plus `GetGlobalValue` conditions,
    which revealer INFO/stage fragments set.  Created before the QUST
    pass so quest VMADs can bind them as properties.

    See: docs/commentary/tes5_import_pipeline.md#reserved-ids-and-preflight
    """
    from .dialogue.unlocks import build_unlock_plan, create_unlock_globals
    unlock_plan = build_unlock_plan(by_type)
    unlock_globals = create_unlock_globals(writer, unlock_plan)

    from script_convert.converter import ScriptConverter as _SC
    _SC.topic_unlock_globals = {
        (d.get('EditorID') or '').lower(): gname
        for d in by_type.get('DIAL', [])
        if (d.get('EditorID') or '')
        for gname in [unlock_plan['gated'].get(
            int(d.get('FormID', '0'), 16) & 0xFFFFFF)]
        if gname
    }
    WELL_KNOWN_PROPERTIES.update(unlock_globals)

    print(f"  AddTopic unlocks: {len(unlock_globals)} gated topics, "
          f"{len(unlock_plan['info_reveals'])} revealer INFOs, "
          f"{len(unlock_plan['stage_reveals'])} revealer quest stages, "
          f"{len(_SC.topic_unlock_globals)} topic->global names for scripts")
    _step_done('addtopic unlock plan')
    return (unlock_plan, unlock_globals, _SC)


def _prescan_menu_records(by_type: dict, writer, _SC, _step_done):
    """Create the button-menu and chargen-menu MESG records.

    Chargen pages live at FIXED ids in the reserved FormID gap because
    the page/button block must be contiguous and ordered.  Chargen
    identity conditions are routed through the menus' choice globals.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-chargen-menu-ids
    """
    from script_convert.message_menus import build_message_plan
    message_plan = build_message_plan(by_type.get('SCPT', []),
                                      by_type.get('MESG', []))
    message_mesgs = create_message_menu_records(writer, message_plan)
    _SC.message_menus = message_plan
    WELL_KNOWN_PROPERTIES.update(message_mesgs)
    print(f"  Button menus: {len(message_mesgs)} MESG records for "
          f"{len(message_plan)} scripts")
    _step_done('button menu MESGs')

    from script_convert.message_menus import build_chargen_menus
    _spel_map = {int(r['FormID'], 16) & 0xFFFFFF: r['EditorID']
                 for r in by_type.get('SPEL', [])
                 if r.get('FormID') and r.get('EditorID')}
    chargen_plan = build_chargen_menus(by_type.get('BSGN', []),
                                       by_type.get('CLAS', []), _spel_map)
    _SC.chargen_menus = chargen_plan
    chargen_mesgs = create_chargen_menu_records(writer, chargen_plan)
    if chargen_mesgs:
        WELL_KNOWN_PROPERTIES.update(chargen_mesgs)
        print(f"  Chargen menus: {len(chargen_mesgs)} MESG pages+globals "
              f"(fixed ids from {writer.chargen_fid_base:08X})")
    from .base.conditions import set_chargen_choice
    set_chargen_choice({})
    for func_idx, key in ((224, 'birthsign'), (129, 'class')):
        menu = chargen_plan.get(key)
        if menu and menu['choice_global'] in chargen_mesgs:
            set_chargen_choice(
                {func_idx: (chargen_mesgs[menu['choice_global']],
                            menu['fid_to_index'])}, merge=True)
    WELL_KNOWN_PROPERTIES.update(create_force_combat_factions(writer))
    WELL_KNOWN_PROPERTIES.update(create_destroyed_formlist(writer))
    _step_done('chargen menu MESGs')


def _prescan_fid_to_edid(all_records: list, ctx, _step_done):
    """Build {FormID -> EditorID} for VMAD property resolution.

    Masters FIRST, then this plugin's own records, so an overridden
    record reports the overriding EditorID.  A master's record is keyed
    on its `master_export` KEY, never on `rec['FormID']`.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-master-key-not-formid
    """
    fid_to_edid = {}
    _edid_sources = [ctx.master_export.items()] if (
        ctx and getattr(ctx, 'master_export', None)) else []
    _edid_sources.append((r.get('FormID', ''), r) for r in all_records)
    for fid_str, rec in [p for src in _edid_sources for p in src]:
        edid_str = rec.get('EditorID', '')
        if fid_str and edid_str:
            try:
                fid_to_edid[int(fid_str, 16)] = edid_str
            except ValueError:
                pass
    print(f"  Built FormID->EditorID map: {len(fid_to_edid)} entries")
    _step_done('fid->edid map')
    return fid_to_edid


def _index_xref_record(xref, fid_str: str, rec: dict, rekey) -> None:
    """Index one record into the CrossRefGraph: ids, type, SCPT, packages.

    `rekey` corrects a master record's id FIELDS (SCRI, NAME, AIPackage) the
    same way its outer key was corrected, so every chain the graph stores is
    keyed in THIS plugin's index space.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-xref-mirrors-cli-scan
    """
    edid_str = rec.get('EditorID', '')
    sig = rec.get('Signature', '')
    try:
        own_key = int(fid_str, 16)
        own_raw = int(rec.get('FormID', '') or fid_str, 16)
    except (ValueError, TypeError):
        own_key = own_raw = None
    if edid_str:
        edid_low = edid_str.lower()
        xref.edid_to_formid[edid_low] = fid_str
        xref.formid_to_edid[fid_str] = edid_str
        if sig == 'QUST':
            xref.quest_edids.add(edid_low)
    xref.record_type[fid_str] = sig
    if sig == 'SCPT':
        _index_xref_script(xref, fid_str, rec, edid_str)
    scri = rekey(rec.get('SCRI', ''), own_raw, own_key)
    if scri:
        xref.record_scri[fid_str] = scri
    if sig in ('NPC_', 'CREA'):
        xref.npc_formids.add(fid_str)
        packs = _xref_actor_packages(rec, rekey, own_raw, own_key)
        if packs:
            xref.actor_packages[fid_str] = packs
    if sig == 'PACK':
        _index_xref_pack(xref, fid_str, rec)
    if sig == 'CELL':
        _index_xref_cell(xref, fid_str, rec,
                         rekey(rec.get('ParentWRLD', ''), own_raw, own_key))
    if sig in ('ACHR', 'ACRE', 'REFR'):
        name_fid = rekey(rec.get('NAME', ''), own_raw, own_key)
        if name_fid:
            xref.record_base[fid_str] = name_fid


def _index_xref_pack(xref, fid_str: str, rec: dict) -> None:
    """Record a PACK's procedure type for GetCurrentAIPackage conversion."""
    pkdt = rec.get('PKDT.Type')
    if pkdt is None:
        return
    try:
        xref.pack_type[fid_str] = int(pkdt)
    except ValueError:
        pass


def _xclc(rec: dict, key: str):
    """One XCLC grid coordinate as an int, or None when absent."""
    try:
        return int(rec.get(key))
    except (TypeError, ValueError):
        return None


def _index_xref_cell(xref, fid_str: str, rec: dict, wrld: str) -> None:
    """Record a CELL's interior flag, worldspace and grid square.

    Without this `split_cell_family` sees no geometry and calls every member of
    a GetInCell family interior, so an exterior is bound as a Cell property --
    which cannot bind -- and no WorldSpace property is emitted at all.

    See: docs/commentary/script_convert.md#worldspace-property-rename
    """
    flags = rec.get('DATA.Flags')
    if flags is None:
        return
    try:
        is_interior = bool(int(str(flags).split()[0], 0) & 1)
    except ValueError:
        return
    xref.cell_geom[fid_str] = (is_interior, wrld or '',
                               _xclc(rec, 'XCLC.X'), _xclc(rec, 'XCLC.Y'))


def _index_xref_script(xref, fid_str: str, rec: dict, edid_str: str) -> None:
    """Record a SCPT's EditorID and SCHR type for get_extends_class binding."""
    if edid_str:
        xref.script_formid_to_edid[fid_str] = edid_str
    schr_type = rec.get('SCHR.Type')
    if schr_type is not None:
        try:
            xref.script_formid_to_type[fid_str] = int(schr_type)
        except ValueError:
            pass


def _xref_actor_packages(rec: dict, rekey, own_raw, own_key) -> list:
    """An actor's AIPackage list, re-keyed; backs `GetCurrentAIPackage`.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-xref-mirrors-cli-scan
    """
    packs = []
    i = 0
    while True:
        p = rec.get(f'AIPackage[{i}]', '')
        if not p:
            break
        packs.append(rekey(p, own_raw, own_key))
        i += 1
    return packs


def _prescan_cross_ref_graph(all_records: list, ctx, export_dir: str, _step_done):
    """Build the CrossRefGraph for script property type detection.

    Hand-built rather than loaded, so it must mirror what the CLI scan
    collects or the converter takes a different branch than it did when
    the .psc was written.  Master ids AND their id fields are re-keyed.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-xref-mirrors-cli-scan
    """
    from script_convert.cross_ref import CrossRefGraph
    xref = CrossRefGraph()
    _xref_sources = [ctx.master_export.items()] if (
        ctx and getattr(ctx, 'master_export', None)) else []
    _xref_sources.append((r.get('FormID', ''), r) for r in all_records)
    def _rekey_ref(ref_hex, own_raw, own_key):
        """Apply the same index-byte correction the record's own key carries."""
        if not ref_hex or own_raw is None:
            return ref_hex
        try:
            ref = int(ref_hex, 16)
        except (ValueError, TypeError):
            return ref_hex
        shift = ((own_key >> 24) & 0xFF) - ((own_raw >> 24) & 0xFF)
        if not shift:
            return ref_hex
        return '%08X' % ((((ref >> 24) & 0xFF) + shift) << 24 | (ref & 0xFFFFFF))

    for fid_str, rec in [p for src in _xref_sources for p in src]:
        if not fid_str:
            continue
        _index_xref_record(xref, fid_str, rec, _rekey_ref)
    scpt_path = os.path.join(export_dir, 'SCPT.txt')
    if os.path.exists(scpt_path):
        xref.build_ref_as_int_map(scpt_path)
    print(f"  Built CrossRefGraph: {len(xref.edid_to_formid)} entries, "
          f"{len(xref.quest_edids)} quests, {len(xref.script_formid_to_edid)} scripts")
    _step_done('cross-ref graph')
    return xref


def _prescan_script_plans(by_type: dict, ctx, xref, fid_to_edid: dict, _step_done):
    """Plan object + quest script VMADs; returns the master export or None.

    Each scriptable record gets a VMAD naming its compiled Papyrus
    script plus FormID bindings.  The MASTERS' SCPTs are indexed: a
    SCRI that misses the index drops the record's VMAD entirely.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-master-key-not-formid
    """
    _scpt_master_export = ctx.master_export if ctx else None
    from .base.object_scripts import build_object_script_plan, build_quest_script_plan
    n_obj_scripts = build_object_script_plan(by_type, xref, fid_to_edid,
                                             _scpt_master_export)
    print(f"  Object scripts: attached {n_obj_scripts} SCPT scripts to records via VMAD")
    n_qust_scripts = build_quest_script_plan(by_type, xref, fid_to_edid,
                                             _scpt_master_export)
    print(f"  Quest scripts: planned {n_qust_scripts} SCRI attachments for QUST VMADs")
    _step_done('object/quest script plans')
    return _scpt_master_export


def _prescan_magic_effects(by_type: dict, ctx, writer, xref, fid_to_edid: dict, _scpt_master_export, _step_done):
    """Register MGEF ids, the AssocItem index, AV/script variants and ENCH.

    All of these must exist before any SPEL/ENCH/ALCH/INGR/SGST record
    converts.  A dependent plugin usually defines no MGEF at all, so the
    effect table comes from the MASTER's export.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-magic-effect-prerequisites
    """
    from .record_types.magic import (build_av_variants, build_seff_variants,
                                     register_mgef_formids,
                                     set_assoc_item_index)
    from .base.object_scripts import build_magic_effect_script_plan

    _mgefs = _mgef_records_with_masters(by_type, ctx)
    register_mgef_formids(_mgefs)
    set_assoc_item_index(*_build_assoc_item_index(by_type, ctx))

    _effect_recs = [r for sig in ('SPEL', 'ENCH', 'ALCH', 'INGR', 'SGST')
                    for r in by_type.get(sig, [])]
    n_av = build_av_variants(_mgefs, _effect_recs, writer)
    n_mescript = build_magic_effect_script_plan(by_type, xref, fid_to_edid,
                                                _scpt_master_export)
    n_seff = build_seff_variants(_mgefs, _effect_recs, writer,
                                 {f'{k:08X}': v for k, v in fid_to_edid.items()})
    from .record_types.equipment import set_ench_index
    _enchs = list(by_type.get('ENCH', []))
    if ctx and getattr(ctx, 'master_export', None):
        _enchs = [r for r in ctx.master_export.values()
                  if r.get('Signature') == 'ENCH'] + _enchs
    set_ench_index(_enchs)

    print(f"  Magic effects: {len(_mgefs)} MGEF + {n_av} per-actor-value "
          f"variants + {n_seff} script variants ({n_mescript} effect scripts); "
          f"{len(_enchs)} ENCH indexed for enchanted-book scrolls")
    _step_done('magic effect plans')


def _prescan_vendor_trainer(by_type: dict, ctx, writer, _step_done):
    """Create vendor factions and the trainer faction + CLAS clones.

    Root masters only: these are support records a dependent plugin
    inherits from its master rather than duplicating.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-dependent-skips-support-records
    """
    if not ctx:
        from .record_types.actor_common import (create_trainer_records, create_vendor_factions)
        create_vendor_factions(by_type, writer)
        create_trainer_records(by_type, writer)
    _step_done('vendor/trainer records')


def _rescan_mesh_caches(export_dir, mesh_dir: str) -> bool:
    """Rebuild one export's bounds+collision caches if either is stale.

    True when a scan ran.  One scan fills both, since they share the expensive
    NIF parse.  A bounds cache predating the current entry schema counts as
    missing: it would parse cleanly and read as all-zeroes for the new field.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-stale-bounds-cache
    """
    from asset_convert.collision.collision_extract import (
        scan_mesh_data, bounds_cache_is_current, collision_cache_is_current)
    from asset_convert.collision.mesh_scan_fragments import (clear_fragments,
                                                             merge_fragments)
    assets_dir = assets_for(export_dir)
    cache_path = str(assets_dir / 'mesh_bounds_cache.json')
    col_path = str(assets_dir / 'collision_cache.bin')
    if (bounds_cache_is_current(cache_path)
            and collision_cache_is_current(col_path)) \
            or not os.path.isdir(mesh_dir):
        return False
    print(f"  Mesh bounds/collision cache missing or stale, "
          f"scanning {mesh_dir}...")
    seed_b, seed_c = merge_fragments(assets_dir)
    scan_mesh_data(mesh_dir, col_path, cache_path,
                   seed_bounds=seed_b, seed_collision=seed_c)
    clear_fragments(assets_dir)
    return True


def _refresh_master_mesh_caches(export_dir: str) -> None:
    """Rescan every MASTER whose bounds/collision cache is stale.

    A cache-format bump strands each master not re-run since: the chain loads
    masters-first and skips a reject per-path, so the plugin navmeshes with
    only the meshes it ships itself and master-owned statics carve nothing.

    See: docs/commentary/tes5_import_pipeline.md#stale-master-asset-caches
    """
    from output_layout import paths as plugin_paths
    from .overrides.nested import export_root, master_export_dir
    header = os.path.join(export_dir, '_HEADER.txt')
    if not os.path.isfile(header):
        return
    try:
        with open(header, 'r', encoding='utf-8') as fh:
            names = [line.partition('=')[2].strip() for line in fh
                     if line.startswith('Master[')]
    except OSError:
        return
    root = export_root(export_dir)
    for name in names:
        mdir = master_export_dir(root, name)
        if os.path.isdir(mdir):
            _rescan_mesh_caches(
                mdir, os.path.join(str(plugin_paths(name).out), 'meshes'))


def _prescan_mesh_caches(export_dir: str, plugin_out_dir: str, _step_done):
    """Load the mesh-bounds and collision caches, scanning them if stale.

    The MASTERS' caches are refreshed FIRST: the chain loads masters-first and
    a stale entry is skipped rather than fatal.

    See: docs/commentary/tes5_import_pipeline.md#stale-master-asset-caches
    """
    from .base.mesh_bounds import load_mesh_bounds
    from asset_convert.collision.collision_extract import (
        load_collision, door_axis_cache_is_current, scan_door_axes)
    axis_path = str(assets_for(export_dir) / 'door_panel_axis_cache.json')
    _refresh_master_mesh_caches(export_dir)
    _rescan_mesh_caches(export_dir, os.path.join(plugin_out_dir, 'meshes'))
    if not door_axis_cache_is_current(axis_path):
        print("  Door threshold cache missing or stale, measuring door "
              "panels...")
        scan_door_axes(export_dir, axis_path)
    load_mesh_bounds(asset_cache_chain(export_dir,
                                       'mesh_bounds_cache.json'))
    load_collision(collision_cache_chain(export_dir))
    _step_done('mesh bounds + collision caches')


def _prescan_furniture_and_actors(by_type: dict, ctx, writer, export_dir: str, _step_done):
    """Load furniture seat lists, objective text and the actor indexes.

    FURN MNAM/FNPR must index the converted NIF's clustered seat
    positions, and re-origined furniture REFRs need z compensation.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-stale-bounds-cache
    """
    from .record_types.items import load_furniture_models
    load_furniture_models(str(assets_for(export_dir) / 'meshes'), by_type, ctx)
    _step_done('furniture seats')

    from .dialogue.objective_text import load_objective_text
    load_objective_text()
    _step_done('objective text')

    from .actors.indexes import build_actor_indexes
    build_actor_indexes(by_type, writer, export_dir, ctx, _step_done)


def _prescan_package_plan(by_type: dict, ctx, writer, fid_to_edid: dict, _step_done):
    """Build the package plan; returns (plan, context, script var map).

    The MASTERS' packages, quests, actors and placements are indexed --
    an unresolved one silently drops the actor to its standing Sandbox
    schedule.  Hunt chains derive their ids from authored ids only.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-hunt-chains-and-script-packages
    """
    from .packages.aliases import (PackagePlan, build_script_var_map,
                               build_scriptvar_owner_map,
                               build_assigned_var_names,
                               build_script_assigned_packages)
    from .dialogue.quest import set_assigned_var_names
    from .packages.actor_wiring import load_package_types
    _master_export = ctx.master_export if ctx else None
    load_package_types(by_type, _master_export)

    _script_vars = build_script_var_map(by_type, _master_export)
    set_assigned_var_names(
        build_assigned_var_names(by_type, _master_export))
    _sv_owner = build_scriptvar_owner_map(by_type, fid_to_edid)
    pack_plan = PackagePlan()
    _script_assigned = build_script_assigned_packages(by_type, fid_to_edid,
                                                      _master_export)
    print(f"  Script-forced packages (AddScriptPackage) bound to an alias: "
          f"{len(_script_assigned)}")
    pack_plan.build(by_type,
                    {get_formid(r, 'FormID') for r in by_type.get('QUST', [])},
                    _sv_owner, _master_export, _script_assigned)
    print(f"  Package plan: {pack_plan.summary()}")

    from .packages.converter import PackContext, hunt_chain_targets
    from .packages.indexes import build_pack_indexes
    pack_ctx = PackContext(plan=pack_plan, script_vars=_script_vars,
                           **build_pack_indexes(by_type, _master_export))

    _chains = {}
    for _rec in by_type.get('PACK', []):
        _src = get_formid(_rec, 'FormID')
        _refs = hunt_chain_targets(_rec, pack_ctx, _src)
        if _refs:
            _chains[_src] = [
                (writer.derive_formid('PACK', (_rec.get('FormID', ''),
                                               f'{_ref:08X}')), _ref)
                for _ref in _refs]
    pack_ctx.hunt_chains = _chains
    pack_plan.expand_packages({k: [c for c, _ in v]
                               for k, v in _chains.items()})
    from .packages.actor_wiring import set_quest_packages, set_package_chains
    set_package_chains({k: [c for c, _ in v] for k, v in _chains.items()})
    if _chains:
        print(f"  Hunt chains: {len(_chains)} Find-at-actor-base packages -> "
              f"{sum(len(v) for v in _chains.values())} Follow links")
    set_quest_packages(pack_plan.owner_quest.keys())
    _step_done('package plan')
    return (pack_plan, pack_ctx, _script_vars)


def _prescan_leveled_actors(by_type: dict, ctx, writer, _step_done):
    """Register leveled creature bases and build their ACHR shell NPCs.

    Every LVLC reachable from here is indexed -- this plugin's and its
    masters' -- because a dependent plugin can place a master's
    leveled creature.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-ordering-constraints
    """
    from .actors.leveled_actors import build_leveled_actor_shells, register_from
    n_bases = register_from(by_type,
                            ctx.master_export if ctx is not None else None)
    n_lvl_achr = build_leveled_actor_shells(by_type, writer)
    print(f"  Leveled creature placements: {n_lvl_achr} REFR -> ACHR "
          f"via generated shell NPCs ({n_bases} LVLC bases known)")
    _step_done('leveled actor shells')


def _prescan_outfits_hair_skin(by_type: dict, ctx, export_dir: str):
    """Load the item/outfit index, faction reactions, hair lengths, skin tones.

    Master exports are indexed throughout: a dependent plugin's actors
    wear their MASTER's hair and use their MASTER's races, so a
    plugin-only scan would leave every one of them on the fallback.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-hair-and-skin-index-masters
    """
    from .actors.outfits import load_item_index
    load_item_index(by_type, ctx.master_export if ctx else None)

    from .record_types.actor_common import load_faction_player_reactions
    load_faction_player_reactions(by_type)

    try:
        from .actors import hair_variants
        hair_variants.load(getattr(ctx, 'export_dir', None) or export_dir,
                           master_export_dirs(ctx) if ctx else ())
        print(f"  Hair length variants: "
              f"{sum(len(v) for v in hair_variants._BUCKETS.values())} baked "
              f"lengths across {len(hair_variants._BUCKETS)} hair records")
    except Exception as _hair_exc:
        print(f"  WARNING: hair length index unavailable: {_hair_exc}")

    try:
        from .actors.npc_face_mapper import load_race_skin_tones, RACE_SKIN_RGB
        _skin_dirs = [getattr(ctx, 'export_dir', None) or export_dir]
        if ctx:
            _skin_dirs.extend(master_export_dirs(ctx))
        _skin_by_type = dict(by_type)
        if ctx and getattr(ctx, 'master_export', None):
            _m_races = [r for r in ctx.master_export.values()
                        if r.get('Signature') == 'RACE']
            if _m_races:
                _skin_by_type['RACE'] = _m_races + (by_type.get('RACE') or [])
        load_race_skin_tones(_skin_by_type, _skin_dirs)
        print(f"  Race skin tones: {len(RACE_SKIN_RGB)} races resolved "
              f"from authored textures")
    except Exception as _skin_exc:
        print(f"  WARNING: race skin tone index unavailable: {_skin_exc}")


def _apply_dobj_battle_override(battle, writer) -> None:
    """Override the master's DOBJ for our Battle music and the lockpick slots.

    The engine reaches combat music and the lockpicking minigame ONLY through
    these default objects, so a Battle MUSC nothing points at never plays and a
    converted lockpick nothing names is inert clutter.  LKPK/SKLK keep the
    master's own forms, which the item substitutions already redirect
    references to.  Every other entry is copied unchanged -- the same
    full-array override each official DLC ships.

    See: docs/commentary/tes5_import_pipeline.md#phase-0c-dobj-btms
    """
    try:
        from .base.equivalents import (
            build_DOBJ_override, DOBJ_BATTLE_MUSIC_TAG, DOBJ_ITEM_TAGS)
        from asset_convert.sources.skyrim_assets import find_skyrim_data
        _sk = find_skyrim_data()
        _esm = os.path.join(_sk, 'Skyrim.esm') if _sk else None
        if not (_esm and os.path.isfile(_esm)):
            print('  WARNING: Skyrim.esm not found; combat music '
                  'stays vanilla.')
            return
        _tags = dict(DOBJ_ITEM_TAGS)
        if battle:
            _tags[DOBJ_BATTLE_MUSIC_TAG] = battle
        _dobj = build_DOBJ_override(_tags, _esm)
        if _dobj:
            writer.add_record('DOBJ', _dobj[1])
            print('  Music: combat music bound to the converted Battle track')
            print('  Items: lockpick/skeleton key bound to the vanilla forms')
        else:
            print('  WARNING: master DOBJ has no overridable entry; '
                  'combat music stays vanilla.')
    except Exception as _de:
        print(f'  WARNING: DOBJ override failed: {_de}')


def _prescan_music_records(by_type: dict, writer, export_dir: str, plugin_out_dir: str, output_path: str):
    """Emit MUSC/MUST records and the DOBJ BTMS override.

    Must precede Phase 1: convert_REGN reads the enum->MUSC table for
    RDMO, and CELL/WRLD later read it for XCMO/ZNAM.  Combat music is
    reachable ONLY through DOBJ's BTMS default object.

    See: docs/commentary/tes5_import_pipeline.md#phase-0c-dobj-btms
    """
    try:
        from .record_types.music import (build_music_records,
                                         load_music_manifest)
        from .record_types.common import (register_music_types,
                                          register_world_music)
        from .base.text_reader import get_formid as _gf, get_int as _gi
        register_world_music({
            _gf(_w, 'FormID'): _gi(_w, 'SNAM.Music', None)
            for _w in by_type.get('WRLD', [])
            if _gi(_w, 'SNAM.Music', None) is not None})
        _music_manifest = load_music_manifest(
            plugin_out_dir,
            export_dir=os.path.dirname(os.path.normpath(export_dir)),
            plugin=os.path.basename(output_path))
        if _music_manifest.get('tracks'):
            _plugin_name = _music_manifest.get('plugin') or os.path.basename(
                os.path.normpath(plugin_out_dir))
            _music = build_music_records(_music_manifest, writer, _plugin_name)
            for _fid, _b in _music['must']:
                writer.add_record('MUST', _b)
            for _fid, _b in _music['musc']:
                writer.add_record('MUSC', _b)
            register_music_types(_music['by_enum'])
            if _music.get('battle'):
                _apply_dobj_battle_override(_music['battle'], writer)
            print(f"  Music: {len(_music['must'])} MUST + "
                  f"{len(_music['musc'])} MUSC records "
                  f"({len(_music['by_enum'])} enum categories)")
    except Exception as e:
        print(f"  ERROR building music records: {e}")


def drop_author_deleted_records(all_records: list, ctx) -> list:
    """`all_records` without the plugin's OWN records the author deleted.

    There is nothing to remove for one of those, so converting it would emit a
    live record the author had thrown away -- and the export carries it as an
    empty stub, so it converts to a contentless REFR with no NAME.  An
    OVERRIDE is kept: a deleted override is meaningful and becomes a proper
    deletion stub.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-ordering-constraints
    """
    if not all_records:
        return all_records
    keep = []
    dropped_deleted = 0
    for rec in all_records:
        if (int(rec.get('RecordFlags') or 0) & OVERRIDE_DELETED_FLAG
                and (ctx is None or ctx.master_record(rec) is None)):
            dropped_deleted += 1
            continue
        keep.append(rec)
    if not dropped_deleted:
        return all_records
    print(f"  Dropped {dropped_deleted} record(s) the author deleted "
          f"and that this plugin itself defines")
    return keep


def _reserve_formid_space(all_records: list, num_tes4_masters: int,
                          file_index: int, writer) -> int:
    """Reserve every authored id plus the chargen/conversation gaps.

    Derived ids are hashed across the plugin's whole id space, so an id a real
    record already owns must be blocked before anything calls `derive_formid`
    or a companion can land on top of one.  Only the plugin's OWN records
    constrain the next object id -- an override's low bytes belong to the
    master's id space.  Returns the highest own object id seen.

    See: docs/commentary/tes5_import_pipeline.md#reserved-ids-and-preflight
    """
    max_formid = 0
    for rec in all_records:
        fid_raw = int(rec.get('FormID', '0'), 16)
        if (fid_raw >> 24) & 0xFF != num_tes4_masters:
            continue
        low = fid_raw & 0x00FFFFFF
        if low > max_formid:
            max_formid = low
    writer.next_object_id = (file_index << 24) | (max_formid + 0x1000)
    writer.chargen_fid_base = (file_index << 24) | (max_formid + 0x800)
    reserved = set()
    for rec in all_records:
        fid_raw = int(rec.get('FormID', '0'), 16)
        if (fid_raw >> 24) & 0xFF == num_tes4_masters:
            reserved.add((file_index << 24) | (fid_raw & 0x00FFFFFF))
    reserved.update((file_index << 24) | (max_formid + 0x800 + k)
                    for k in range(0x800))
    from .dialogue.converter import CONV_FAKE_FID_BASE, CONV_FAKE_FID_COUNT
    reserved.update((file_index << 24) | (CONV_FAKE_FID_BASE + k)
                    for k in range(CONV_FAKE_FID_COUNT))
    writer.reserve_source_ids(reserved)
    return max_formid


def _open_import_run(masters, skip_types, output_path: str,
                     export_dir: str) -> tuple:
    """Resolve the run's masters, skip set, namespace and output paths.

    A per-plugin output dir is named after the plugin (`output/Oblivion.esm/`
    is a FOLDER), so given the folder we write `<folder>/<folder-name>` inside
    it.  Stale stage artifacts fail here, not fifteen pre-scans in.

    Returns (masters, all_skip, output_path, plugin_out_dir).

    See: docs/commentary/tes5_import_pipeline.md#reserved-ids-and-preflight
    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    """
    set_namespace(namespace_for(export_dir))
    if masters is None:
        masters = ['Skyrim.esm']
    if skip_types is None:
        skip_types = set()
    all_skip = SKIP_TYPES | skip_types
    if os.path.isdir(output_path):
        output_path = os.path.join(
            output_path, os.path.basename(os.path.normpath(output_path)))
    plugin_out_dir = os.path.dirname(output_path)
    from .base.artifact_schema import preflight_artifacts
    preflight_artifacts(export_dir)
    set_cloud_bank_output(plugin_out_dir)
    return masters, all_skip, output_path, plugin_out_dir


def _new_plugin_writer(masters: list, is_esm: bool, export_dir: str):
    """A fresh PluginWriter, with the per-plugin registries reset.

    `book_models` holds every distinct BOOK model in the plugin so
    `convert_BOOK` resolves inventory-art basenames through the same
    collision-aware map the asset side uses: two BOOK models can share a leaf
    filename across directories, and only the whole-plugin view can tell which
    one keeps the bare name.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-ordering-constraints
    """
    WELL_KNOWN_PROPERTIES.clear()
    from .base.equivalents import VOICE_TYPE_MAP
    VOICE_TYPE_MAP.clear()
    VTYP_EDID_BY_FID.clear()
    writer = PluginWriter(masters=masters, is_esm=is_esm,
                          description="Converted from TES4 by tes4_export")
    writer.export_dir = export_dir
    from .record_types.sound import set_sound_source_dir
    set_sound_source_dir(str(assets_for(export_dir)))
    try:
        from asset_convert.ui.book_inam import distinct_book_models
        writer.book_models = distinct_book_models(export_dir)
    except Exception as exc:
        print(f"  [book INAM] model list unavailable ({exc}); using leaf names")
        writer.book_models = []
    return writer


def import_plugin(export_dir: str, output_path: str, masters: list = None,
                  is_esm: bool = True, skip_types: set = None,
                  output_root: str = None):
    """
    Main import entry point. Reads exports and writes a TES5 plugin.

    `output_root` holds the converted masters at <root>/<Master>/<Master>.  It
    is required when the plugin has TES4 masters — its overrides are merged
    against them — and defaults to the parent of this plugin's output folder.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-ordering-constraints
    """
    masters, all_skip, output_path, plugin_out_dir = _open_import_run(
        masters, skip_types, output_path, export_dir)

    _phase_t = time.time()

    def _phase_done(label: str):
        nonlocal _phase_t
        now = time.time()
        print(f"  Phase: {label} ({now - _phase_t:.1f}s)")
        _phase_t = now

    _step_t = time.time()

    def _step_done(label: str):
        nonlocal _step_t
        now = time.time()
        if now - _step_t >= 1.0:
            print(f"    Phase: {label} ({now - _step_t:.1f}s)")
        _step_t = now

    tes4_master_names = masters_from_export_header(export_dir)
    num_tes4_masters = len(tes4_master_names)
    masters = _reconcile_masters(masters, tes4_master_names)
    ctx = None
    if num_tes4_masters:
        print(f"  TES4 masters: {num_tes4_masters} "
              f"(records below index {num_tes4_masters:02X} are overrides)")
        if output_root is None:
            output_root = os.path.dirname(plugin_out_dir) or '.'
        _t = time.time()
        ctx = OverrideContext(export_dir, masters, num_tes4_masters,
                              output_root)
        print(f"  Master: {len(ctx.master_index)} converted records, "
              f"{len(ctx.master_manifest)} manifest entries, "
              f"{len(ctx)} exported records ({time.time() - _t:.1f}s)")
        if not len(ctx):
            ctx = None

    print(f"Reading exports from: {export_dir}")
    t0 = time.time()

    all_records = parse_export_directory(export_dir,
                                         exclude=RUNTIME_ONLY_TYPES)
    all_records = drop_author_deleted_records(all_records, ctx)
    by_type = group_records_by_type(all_records)

    t1 = time.time()
    print(f"  Parsed {len(all_records)} records in {len(by_type)} types ({t1-t0:.2f}s)")
    _phase_done('parse export text')

    for sig in sorted(by_type.keys()):
        special_types = {'LTEX', 'SOUN', 'WTHR', 'CELL', 'WRLD', 'REFR', 'ACHR', 'ACRE', 'LAND', 'DIAL', 'INFO'}
        status = "SKIP" if sig in all_skip else ("CONVERT" if sig in IMPORT_DISPATCH or sig in special_types else "UNKNOWN")
        print(f"  {sig}: {len(by_type[sig])} records [{status}]")

    writer = _new_plugin_writer(masters, is_esm, export_dir)

    num_new_masters = len(masters) - num_tes4_masters
    set_formid_index_offset(num_new_masters)

    file_index = len(masters)
    max_formid = _reserve_formid_space(all_records, num_tes4_masters,
                                       file_index, writer)

    _repair_null_land_formids(by_type, num_tes4_masters, max_formid)

    if num_tes4_masters:
        injected = detect_injected_records(
            all_records, ctx.master_export if ctx else {},
            num_tes4_masters, writer)
        set_injected_formids(injected)
        if injected:
            print(f"  Injected records: {len(injected)} moved out of the "
                  f"master's FormID space into ours")

    set_tes4_effect_names(by_type.get('MGEF', []))

    _prescan_special_records(by_type, ctx, writer, export_dir,
                             _step_done)

    npc_to_vtyp = _prescan_npc_voice_map(by_type, ctx, writer,
                                         num_new_masters, _step_done)

    unlock_plan, unlock_globals, _SC = _prescan_unlock_plan(
        by_type, writer, _step_done)

    _prescan_menu_records(by_type, writer, _SC, _step_done)

    fid_to_edid = _prescan_fid_to_edid(all_records, ctx, _step_done)

    xref = _prescan_cross_ref_graph(all_records, ctx, export_dir,
                                    _step_done)

    _scpt_master_export = _prescan_script_plans(by_type, ctx, xref,
                                                fid_to_edid,
                                                _step_done)

    _prescan_magic_effects(by_type, ctx, writer, xref, fid_to_edid,
                           _scpt_master_export, _step_done)

    _prescan_vendor_trainer(by_type, ctx, writer, _step_done)

    _prescan_mesh_caches(export_dir, plugin_out_dir, _step_done)

    _prescan_furniture_and_actors(by_type, ctx, writer, export_dir,
                                  _step_done)

    pack_plan, pack_ctx, _script_vars = _prescan_package_plan(
        by_type, ctx, writer, fid_to_edid, _step_done)

    _prescan_leveled_actors(by_type, ctx, writer, _step_done)

    _prescan_outfits_hair_skin(by_type, ctx, export_dir)

    _phase_done('pre-scans')

    _prescan_music_records(by_type, writer, export_dir,
                           plugin_out_dir, output_path)

    st = ImportState(
        all_skip=all_skip,
        output_path=output_path,
        plugin_out_dir=plugin_out_dir,
        num_tes4_masters=num_tes4_masters,
        ctx=ctx,
        output_root=output_root,
        by_type=by_type,
        writer=writer,
        npc_to_vtyp=npc_to_vtyp,
        unlock_plan=unlock_plan,
        unlock_globals=unlock_globals,
        fid_to_edid=fid_to_edid,
        xref=xref,
        pack_plan=pack_plan,
        pack_ctx=pack_ctx,
        _script_vars=_script_vars)
    run_record_phases(st, export_dir, _phase_done, skip_types)
    return run_finalize_phases(st, export_dir, _phase_done, is_esm,
                               masters)


def main():
    parser = argparse.ArgumentParser(
        description="TES5 Import — Convert TES4 exports to Skyrim SE plugin"
    )
    parser.add_argument("export_dir", help="Directory containing per-type export .txt files")
    parser.add_argument("-o", "--output", required=True, help="Output .esm/.esp path")
    parser.add_argument("-m", "--masters", nargs="+", default=["Skyrim.esm"],
                        help="Master files (default: Skyrim.esm)")
    parser.add_argument("--esp", action="store_true", help="Create ESP instead of ESM")
    parser.add_argument("--skip", nargs="+", default=[],
                        help="Additional record types to skip")

    args = parser.parse_args()

    if not os.path.isdir(args.export_dir):
        print(f"Error: Export directory not found: {args.export_dir}", file=sys.stderr)
        sys.exit(1)

    import_plugin(
        export_dir=args.export_dir,
        output_path=args.output,
        masters=args.masters,
        is_esm=not args.esp,
        skip_types=set(args.skip),
    )


def _repair_null_land_formids(by_type: dict, own_index: int,
                              max_formid: int) -> None:
    """Give a LAND with FormID 00000000 an id of its own, in place.

    Not a defensive check — real plugins ship them. ElsweyrAnequina.esp has 7
    LAND records whose FormID is literally 0x00000000 (verified by reading the
    ORIGINAL Oblivion .esp, so this is the mod's own data and not an export
    defect); Oblivion.esm and Tamriel.esp have none. Oblivion tolerated it
    because it reaches a cell's landscape through the cell's children group
    rather than by id.

    We cannot. The conversion caches converted land as {FormID: bytes} and
    pops by that key, so every null-id LAND collapses onto key 0: the first
    cell to ask consumes the single entry and the other six get nothing.

    THE ID MUST BE UNIQUE ACROSS THE WHOLE PLUGIN, NOT JUST ACROSS LAND.
    Skyrim's form table is keyed by FormID alone with no per-signature
    namespace, so an id that is free among landscapes but taken by some other
    record is still a duplicate. The previous attempt reused the parent CELL's
    own id verbatim (a cell owns at most one LAND, so it looked unique) and
    shipped 7 LAND records whose FormID equalled their CELL's — measured in the
    built output, `0201C2A8` was both the cell at (-7,-32) and its landscape.
    Only one form per id survives loading, the CELL won, and the landscape
    never became a form at all: terrain blank and missing in-game while the
    cell's placed references — which have ids of their own — still rendered.
    An even earlier attempt OR'd in 0x0F000000 to dodge collisions, which put
    the records at load-order index 0x10 where no plugin exists, so the engine
    could not resolve them either.

    The ids come from the RESERVED GAP above the plugin's highest real FormID,
    which `import_plugin` opens as `max_formid + 0x1000` and nothing else ever
    occupies: real records stop at or below max_formid, companions start above
    the gap. Taking them from the TOP of that gap downward keeps them clear of
    both ends.

    These ids are assigned POSITIONALLY (a counter walking down the gap in
    export order), which is the one place the converter still keys an id on
    something other than authored data: inserting a null-id LAND would
    renumber the ones after it. Hashing the parent CELL instead would fix that
    but MOVES ALL 7 EXISTING IDS in ElsweyrAnequina.esp, so it is deliberately
    NOT done — the drift costs more than the fragility. Do not "fix" this
    without deciding to renumber.
    """
    lands = by_type.get('LAND') or []
    nulls = [r for r in lands
             if not (r.get('FormID') or '').strip().strip('0')]
    if not nulls:
        return

    taken = set()
    for recs in by_type.values():
        for rec in recs:
            try:
                fid = int((rec.get('FormID') or '0').strip(), 16)
            except ValueError:
                continue
            if (fid >> 24) & 0xFF == own_index:
                taken.add(fid & 0xFFFFFF)

    index = (own_index & 0xFF) << 24
    # Top of the reserved gap, walking down. 0x1000 wide, and a plugin has at
    nxt = max_formid + 0xFFF
    floor = max_formid + 1
    fixed = 0
    for rec in nulls:
        if not (rec.get('ParentCELL') or '').strip().strip('0'):
            continue
        while nxt in taken and nxt >= floor:
            nxt -= 1
        if nxt < floor:
            print(f"  WARNING: ran out of reserved FormIDs for null-id LAND; "
                  f"{len(nulls) - fixed} record(s) left unrepaired")
            break
        taken.add(nxt)
        rec['FormID'] = '%08X' % (index | nxt)
        nxt -= 1
        fixed += 1
    if fixed:
        print(f"  Repaired {fixed} LAND record(s) shipped with a null FormID "
              f"(reserved ids above {max_formid:06X})")
