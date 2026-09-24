"""Phase 5 and the run's output: dialogue groups, side files, the write.

Split out of `import_main` (see pipeline.py).  Terminal by construction --
it reads the `ImportState` the earlier phases filled and produces nothing
they consume.

`_write_lava_mesh` runs BEFORE `writer.write()` so `--import-only` alone
still yields a working result.

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

import os
import struct
import sys
import time
from collections import defaultdict

from .overrides.manifest import write_manifest
from .overrides.nested import (build_nested_overrides)
from .dialogue.arrest import morrowind_arrest_topic
from .dialogue.groups import build_dialog_groups
from .base.owned_records import (
    WELL_KNOWN_PROPERTIES,
)
from .base.text_reader import (
    get_formid,
    get_str,
    set_formid_index_offset,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))




def _build_dialogue(st, voice_map: dict) -> set:
    """Phase 5: emit the DIAL/INFO hierarchy.  Returns its SGE quest fids.

    A master's dialogue is OVERRIDDEN, never re-derived, but a plugin may
    still add whole topics of its own, which do run the real pipeline.
    `build_dialog_groups` reads several signatures beyond DIAL/INFO and
    silently no-ops on an empty list, so those are back-filled from the
    plugin's full record set.

    See: docs/commentary/tes5_import_pipeline.md#phase-5-dialogue-overrides
    """
    if not st.ctx:
        return build_dialog_groups(
            st.by_type, st.writer, st.npc_to_vtyp, fid_to_edid=st.fid_to_edid,
            xref=st.xref, well_known_props=WELL_KNOWN_PROPERTIES,
            voice_map=voice_map, unlock_plan=st.unlock_plan,
            unlock_globals=st.unlock_globals, script_vars=st._script_vars,
            plugin_stem=os.path.splitext(os.path.basename(st.output_path))[0])

    unattached_dial = build_nested_overrides(st.by_type, ('DIAL', 'INFO'),
                                             st.ctx, st.writer, 'DIAL/INFO')
    if not unattached_dial:
        return set()

    own = defaultdict(list)
    for sig, rec in unattached_dial:
        own[sig].append(rec)
    for sig in ('QUST', 'SCPT', 'ACHR', 'ACRE', 'REFR',
                'NPC_', 'CREA', 'RACE', 'FACT'):
        if not own.get(sig):
            own[sig] = st.by_type.get(sig, [])
    print(f"  Building this plugin's OWN dialogue "
          f"({len(own.get('DIAL', []))} DIAL, "
          f"{len(own.get('INFO', []))} INFO)")
    return build_dialog_groups(
        own, st.writer, st.npc_to_vtyp, fid_to_edid=st.fid_to_edid,
        xref=st.xref, well_known_props=WELL_KNOWN_PROPERTIES,
        voice_map=voice_map, unlock_plan=st.unlock_plan,
        unlock_globals=st.unlock_globals, script_vars=st._script_vars,
        master_index=st.ctx.master_index)


def _patch_sounds(st, export_dir: str = '') -> None:
    """Point every sound slot at an SNDR, not the TES4 SOUN id Phase 1 wrote.

    See: docs/commentary/tes5_import_pipeline.md#sound-slot-patching
    """
    from .record_types.creature import patch_actor_sounds
    n_snd = patch_actor_sounds(st.writer)
    if n_snd:
        print(f"  Actor sound entries bound/flattened: {n_snd} actors")
    _own_souns = {get_formid(r, 'FormID') & 0x00FFFFFF
                  for r in st.by_type.get('SOUN', [])}

    def _master_sound_descriptor(soun_fid: int) -> int:
        """MASTER-owned TES4 SOUN id -> the SNDR the master's conversion made.

        Read out of the master's converted SOUN (whose SDSC names the
        companion) rather than re-derived, which would mint an id in THIS
        plugin's index space.  Required, not optional: Morrowind_ob's containers
        and torches point at Oblivion.esm sounds it never overrides, so the
        master manifest carries no entry for them and every one of those ~2,400
        slots would otherwise be left wrong-typed.
        """
        if not st.ctx or not getattr(st.ctx, 'master_index', None):
            return 0
        blob = st.ctx.master_index.record(soun_fid)
        if not blob or blob[:4] != b'SOUN':
            return 0
        pos = 24
        while pos + 6 <= len(blob):
            sig = blob[pos:pos + 4]
            size = struct.unpack_from('<H', blob, pos + 4)[0]
            if sig == b'SDSC' and size == 4:
                return struct.unpack_from('<I', blob, pos + 6)[0]
            pos += 6 + size
        return 0

    from .record_types.items import patch_sound_descriptor_slots
    bound = []
    for _sig, _label in (('ACTI', 'activators'), ('CONT', 'containers'),
                         ('DOOR', 'doors'), ('LIGH', 'lights')):
        _n = patch_sound_descriptor_slots(
            st.writer, _sig, _own_souns, _master_sound_descriptor)
        if _n:
            bound.append(f"{_n} {_label}")
    if bound:
        print(f"  Sound descriptors bound: {', '.join(bound)}")
    from .record_types.weather import patch_weather_sounds
    n_wsnd = patch_weather_sounds(st.writer, _own_souns)
    if n_wsnd:
        print(f"  Weather sound descriptors bound: {n_wsnd} weathers")
    _stage_sound_table(st, export_dir)


def _stage_sound_table(st, export_dir: str) -> None:
    """Stage the runtime's sound table, for a TES3 source only.

    Here rather than in the sidecar pass: the SNDR ids only exist once Phase 3
    has run, and this is where every other sound slot is bound.

    Measured on TR_Mainland: of the 99 sound ids its scripts name, 2 are its
    own and 97 belong to masters, so a table of only this plugin's SOUNs is
    empty of what the scripts actually ask for.
    See: docs/commentary/tes5_import_sound.md#the-runtime-sound-table
    """
    from .dialogue.morrowind_sidecar import stage_sound_table
    from .record_types.sound import get_sndr_for_soun
    plugin = os.path.basename(st.output_path)
    own = {(rec.get('EditorID') or '').strip():
           get_sndr_for_soun(get_formid(rec, 'FormID'))
           for rec in st.by_type.get('SOUN', [])}
    named = stage_sound_table(export_dir, st.output_path, plugin, own)
    if named:
        print(f"  Runtime sound table: {named} sound(s)")


def _patch_late_bindings(st, export_dir: str) -> None:
    """Bind everything that could only be resolved once all records existed.

    ForceGreet topics, the player-alias quest, actor and weather sounds, the
    sound-descriptor slots, and the creature voice/footstep/body-part chains.
    Each patches already-written bytes rather than reordering a phase.

    See: docs/commentary/tes5_import_pipeline.md#patch-pass-runs-last
    """
    from .dialogue.converter import make_player_script_quest
    player_quest_fid = make_player_script_quest(
        st.writer, master_index=(st.ctx.master_index if st.ctx else None))
    if player_quest_fid and not (st.ctx and st.ctx.master_index is not None
                                 and player_quest_fid in st.ctx.master_index):
        st.sge_quest_fids.add(player_quest_fid)
    from .packages.converter import patch_forcegreet_topics
    n_fg = patch_forcegreet_topics(st.writer)
    if n_fg:
        print(f"  ForceGreet packages bound to a greeting topic: {n_fg}")
    _patch_sounds(st, export_dir)
    _patch_creature_chains(st, export_dir)


def _patch_creature_chains(st, export_dir: str) -> None:
    """Allocate and bind the creature voice, footstep and body-part records.

    Allocated LAST so no other generated FormID moves, then patched into the
    already-written actors, ARMAs and races.

    See: docs/commentary/tes5_import_pipeline.md#patch-pass-runs-last
    """
    from .actors.creature_races import (build_creature_voice_types,
                                        patch_creature_voices)
    n_cv = build_creature_voice_types(st.writer)
    if n_cv:
        n_cvp = patch_creature_voices(st.writer)
        print(f"  Creature voice types: {n_cv} generated, "
              f"{n_cvp} records bound")
    from .actors.creature_footsteps import (build_creature_footsteps,
                                     patch_creature_footsteps)
    from .actors.creature_races import get_creature_arma_folders
    from .record_types.sound import get_sndr_for_soun
    _crea_slots = _creature_sound_slots(export_dir)
    _soun_fid = {get_str(r, 'EditorID'): get_formid(r, 'FormID')
                 for r in st.by_type.get('SOUN', []) if get_str(r, 'EditorID')}
    n_fs = build_creature_footsteps(
        st.writer, _crea_slots,
        lambda edid: get_sndr_for_soun(_soun_fid.get(edid, 0)))
    if n_fs:
        n_fsp = patch_creature_footsteps(st.writer, get_creature_arma_folders())
        print(f"  Creature footstep sets: {n_fs} generated, "
              f"{n_fsp} ARMAs bound")
    from .actors.creature_races import build_creature_body_parts, patch_creature_bptd
    n_bp = build_creature_body_parts(st.writer)
    if n_bp:
        n_bpp = patch_creature_bptd(st.writer)
        print(f"  Creature body part data: {n_bp} generated, "
              f"{n_bpp} races bound")


def run_finalize_phases(st, export_dir: str, phase_done,
                        is_esm: bool, masters: list) -> tuple:
    """Phase 5 onward: dialogue, side files, and the write.

    Returns (converted, errors).  Terminal: nothing it binds
    is read by an earlier phase.
    """
    voice_map = {}
    st.sge_quest_fids |= _build_dialogue(st, voice_map) | morrowind_arrest_topic(
        st.by_type, st.writer, st.ctx.master_index if st.ctx else None)

    _patch_late_bindings(st, export_dir)

    _write_voice_map(st.output_path, voice_map)
    from .dialogue.converter import get_lip_texts
    _write_lip_text(st.output_path, get_lip_texts())
    phase_done('DIAL/INFO groups')

    if st.ctx:
        st.ctx.report()

    t3 = time.time()
    print(f"\nConverted {st.converted} records ({st.errors} errors) in {t3-st.t2:.2f}s")

    if getattr(st.writer, '_lava_stat_emitted', False):
        _write_lava_mesh(st.plugin_out_dir, st.by_type)

    os.makedirs(os.path.dirname(st.output_path) or '.', exist_ok=True)
    st.writer.write(st.output_path)
    phase_done('write output file')

    mpath = write_manifest(st.output_path, os.path.basename(st.output_path),
                           st.writer.manifest())
    print(f"  Wrote {mpath} ({len(st.writer.manifest())} source records)")

    ds = st.writer.derive_stats()
    if ds['derived']:
        pct = 100.0 * ds['collisions'] / ds['derived']
        print(f"  Derived FormIDs: {ds['derived']:,} hashed from source, "
              f"{ds['collisions']} collisions ({pct:.3f}%), "
              f"max {ds['max_probe']} rehash")

    file_size = os.path.getsize(st.output_path)
    print(f"Wrote {st.output_path} ({file_size:,} bytes)")

    _write_seq_file(st.output_path, st.sge_quest_fids)

    set_formid_index_offset(0)

    return st.converted, st.errors


def _write_voice_map(output_path: str, voice_map: dict):
    """Write the INFO FormID -> voice filename prefix map next to the ESM.

    The audio pipeline (organize_voice_files) uses it to name extracted voice
    files the way the Skyrim engine resolves them at runtime: the prefix is
    built from the CONVERTED records' owning-quest + topic EditorIDs, so the
    Oblivion filename prefix cannot be trusted.
    """
    if not voice_map:
        return
    map_path = output_path + '.voicemap.txt'
    with open(map_path, 'w', encoding='utf-8') as f:
        f.write('# InfoFormID(low24,hex)=prefix[\\tVTYP1,VTYP2] '
                '(questEDID_topicEDID; optional tab-separated target voice-type '
                'folders for NPC-specific lines whose speaker VTYP differs from '
                'the Oblivion source race folder)\n')
        for fid in sorted(voice_map):
            f.write(f'{fid:06X}={voice_map[fid]}\n')
    print(f"  Wrote {map_path} ({len(voice_map)} voice filename prefixes)")


def _write_lip_text(output_path: str, lip_texts: dict):
    """Write the {(info_fid24, resp_num): spoken text} map next to the ESM.

    The audio pipeline pairs each voice WAV with its transcript and runs the
    Creation Kit's LipGenerator to produce the .lip sync track, packing both
    into a .fuz (SSE only reads lip data from .fuz containers).

    Format: one line per response — `<fid24 hex6>_<resp_num>=<text>` with
    backslash escapes for \\ \n \r \t (same scheme as the text export).
    """
    if not lip_texts:
        return
    map_path = output_path + '.liptext.txt'
    with open(map_path, 'w', encoding='utf-8') as f:
        f.write('# InfoFormID(low24,hex)_ResponseNumber=spoken text '
                '(for .lip generation; escapes: \\\\ \\n \\r \\t)\n')
        for (fid, num) in sorted(lip_texts):
            text = (lip_texts[(fid, num)]
                    .replace('\\', '\\\\').replace('\n', '\\n')
                    .replace('\r', '\\r').replace('\t', '\\t'))
            f.write(f'{fid:06X}_{num}={text}\n')
    print(f"  Wrote {map_path} ({len(lip_texts)} response transcripts)")


def _write_seq_file(output_path: str, sge_quest_fids: set):
    """Write a .seq file listing all StartGameEnabled quest FormIDs.

    The Skyrim engine reads this file on game start to initialize SGE quests.
    Without it, quests with the StartGameEnabled flag don't actually start,
    which means dialogue quests never run and no dialog appears.

    Format: raw binary array of uint32 FormIDs (little-endian), no header.
    Path:   <Data>/seq/<PluginName>.seq  (next to the ESM in Data folder)
    """
    if not sge_quest_fids:
        print("  No StartGameEnabled quests — skipping .seq generation")
        return

    output_dir = os.path.dirname(output_path)
    plugin_name = os.path.basename(output_path)
    seq_dir = os.path.join(output_dir, 'seq')
    os.makedirs(seq_dir, exist_ok=True)

    seq_path = os.path.join(seq_dir, os.path.splitext(plugin_name)[0] + '.seq')
    sorted_fids = sorted(sge_quest_fids)
    with open(seq_path, 'wb') as f:
        for fid in sorted_fids:
            f.write(struct.pack('<I', fid))

    print(f"  Wrote {seq_path} ({len(sorted_fids)} SGE quests, "
          f"{len(sorted_fids) * 4} bytes)")


def _write_lava_mesh(plugin_out_dir: str, by_type: dict) -> None:
    """Generate the emissive lava plane the placed lava REFRs point at.


    The texture comes from the AUTHORED WATR: Oblivion's lava records name
    their surface image in TNAM (OblivionLavaTest01 names
    ``Water\\OblivionLava06.dds``), and the asset stage deploys it under
    ``textures\\tes4\\``.  Records that name no texture fall back to the one a
    sibling lava record does name, so a stub record cannot leave the plane
    untextured.
    """
    from .actors.lava_placement import (lava_mesh_rel, collect_lava_water_fids,
                                 scroll_for)
    from .record_types.common import prefix_path

    lava_fids = collect_lava_water_fids(by_type)
    if not lava_fids:
        return

    texture = ''
    for rec in by_type.get('WATR', []):
        if get_formid(rec, 'FormID') not in lava_fids:
            continue
        tex = get_str(rec, 'TNAM.Texture', '').strip()
        if tex:
            texture = prefix_path(tex)
            break
    if not texture:
        print('    Lava surface: no authored texture on any lava WATR '
              '- surface not generated')
        return

    scroll_x, scroll_y = scroll_for(by_type, lava_fids)

    try:
        from asset_convert.lava_surface import write_lava_nif
    except Exception as exc:
        print(f'    Lava surface: generator unavailable ({exc})')
        return

    dst = os.path.join(plugin_out_dir, 'meshes',
                       *lava_mesh_rel().split('\\'))
    if write_lava_nif(dst, 'textures\\' + texture,
                      scroll_x=scroll_x, scroll_y=scroll_y):
        print(f'    Lava surface mesh: {dst} (texture {texture}, '
              f'scroll {scroll_x:g}/{scroll_y:g})')


def _creature_sound_slots(export_dir: str) -> dict:
    """{folder: {csdt_type: SOUN EditorID}} for the footstep chain.

    Reuses the creature pipeline's resolver so the records and the generated
    assets agree on which sound belongs to which slot (it handles CSCR
    inheritance — 817 of Oblivion's 909 CREA records inherit their sound set
    rather than defining one). Import must not hard-depend on asset_convert,
    so a missing/unimportable pipeline is simply "no footsteps".
    """
    try:
        from asset_convert.havok.creature_sounds import sound_slots_by_folder
    except ImportError:
        return {}
    try:
        return sound_slots_by_folder(export_dir) or {}
    except OSError:
        return {}
