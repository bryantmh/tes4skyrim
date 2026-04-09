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
"""

import argparse
import os
import struct
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from .constants import IMPORT_DISPATCH, SKIP_TYPES, TYPE_MAP
from .record_types.dialog_misc import (
    build_voice_type_ctdas_for_info,
    convert_DIAL,
    convert_INFO,
    convert_QUST,
    convert_SOUN,
    is_bark_topic,
    make_dlbr,
    make_dlvw,
    should_skip_dial,
)
from .skyrim_overrides import (
    CUSTOM_VTYP_EDIDS,
    TES4_RACE_FID_TO_EDID,
    VOICE_TYPE_MAP,
    set_voice_type,
)
from .record_types.world import (
    convert_ACHR,
    convert_CELL,
    convert_LAND,
    convert_LTEX,
    convert_REFR,
    convert_WRLD,
)
from .text_reader import (
    get_float,
    get_formid,
    get_int,
    get_str,
    group_records_by_type,
    parse_export_directory,
    set_formid_index_offset,
)
from .writer import (
    PluginWriter,
    pack_group,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint32_subrecord,
)


def _create_vtyp_records(writer: PluginWriter):
    """Create custom VTYP records for all Oblivion races in the output plugin.

    All voice types are created from scratch — we never reference Skyrim.esm
    VTYPs.  Voice files go in Sound/Voice/<plugin>/<EditorID>/ and must match
    the EditorIDs created here (TES4Male*, TES4Female*).

    Updates VOICE_TYPE_MAP at runtime so NPC_ converters pick the right FormID.

    DNAM flags: bit 0 = AllowDefaultDialogue, bit 1 = Female
      Male   voices: DNAM = 1
      Female voices: DNAM = 3
    """
    for vtyp_edid, (race_edid, gender) in CUSTOM_VTYP_EDIDS.items():
        fid = writer.alloc_formid()
        dnam = 3 if gender == 'Female' else 1
        subs = pack_string_subrecord('EDID', vtyp_edid)
        subs += pack_subrecord('DNAM', struct.pack('<B', dnam))
        writer.add_record('VTYP', pack_record('VTYP', fid, 0, subs))
        set_voice_type(race_edid, gender, fid)


def import_plugin(export_dir: str, output_path: str, masters: list = None,
                  is_esm: bool = True, skip_types: set = None):
    """
    Main import entry point. Reads exports and writes a TES5 plugin.

    Args:
        export_dir: Directory containing per-type .txt export files
        output_path: Path for the output .esm/.esp file
        masters: List of master file names (e.g., ['Skyrim.esm'])
        is_esm: Whether to create an ESM (True) or ESP (False)
        skip_types: Additional types to skip
    """
    if masters is None:
        masters = ['Skyrim.esm']
    if skip_types is None:
        skip_types = set()
    all_skip = SKIP_TYPES | skip_types

    print(f"Reading exports from: {export_dir}")
    t0 = time.time()

    # Parse all records
    all_records = parse_export_directory(export_dir)
    by_type = group_records_by_type(all_records)

    t1 = time.time()
    print(f"  Parsed {len(all_records)} records in {len(by_type)} types ({t1-t0:.2f}s)")

    for sig in sorted(by_type.keys()):
        special_types = {'LTEX', 'SOUN', 'CELL', 'WRLD', 'REFR', 'ACHR', 'ACRE', 'LAND', 'DIAL', 'INFO'}
        status = "SKIP" if sig in all_skip else ("CONVERT" if sig in IMPORT_DISPATCH or sig in special_types else "UNKNOWN")
        print(f"  {sig}: {len(by_type[sig])} records [{status}]")

    # Create writer
    writer = PluginWriter(masters=masters, is_esm=is_esm,
                          description="Converted from TES4 by tes4_export")

    # Set FormID remapping: adding N new masters shifts all load order indices
    # For Oblivion.esm with masters=['Skyrim.esm'], offset=1:
    #   0x00XXXXXX (was index 0 in TES4) → 0x01XXXXXX (now index 1 in TES5)
    num_new_masters = len(masters)
    set_formid_index_offset(num_new_masters)

    # Determine next FormID from the maximum in the export (before remapping applies)
    # Must scan ALL FormIDs including those in cross-references to avoid collisions
    max_formid = 0
    for rec in all_records:
        fid_raw = int(rec.get('FormID', '0'), 16)
        low = fid_raw & 0x00FFFFFF
        if low > max_formid:
            max_formid = low
    file_index = num_new_masters  # Our file's index in the TES5 master list
    # Start well above the highest FormID to avoid collision with companion records
    writer.next_object_id = (file_index << 24) | (max_formid + 0x1000)

    # --- Phase 0: Create custom VTYP records for voice types not in Skyrim.esm ---
    _create_vtyp_records(writer)

    # --- Phase 0b: Pre-scan for dialogue conversion ---
    # 1) Collect QUSTs that own DIAL records → force dialogue flags
    # 2) Build NPC FormID → VTYP FormID mapping for voice type injection
    dialogue_quest_fids = _collect_dialogue_quest_fids(by_type)
    npc_to_vtyp = _build_npc_to_vtyp_map(by_type, num_new_masters)

    # --- Phase 0c: Create vendor factions for merchant NPCs ---
    from .record_types.actors import create_vendor_factions
    create_vendor_factions(by_type, writer)

    # --- Phase 1: Simple record types (flat top-level groups) ---
    print("\nConverting records...")
    t2 = time.time()

    simple_types = set()
    for sig in sorted(by_type.keys()):
        if sig in all_skip:
            continue
        if sig in ('CELL', 'WRLD', 'DIAL', 'INFO', 'REFR', 'ACHR', 'ACRE', 'LAND',
                    'LTEX', 'SOUN', 'PGRD', 'QUST'):
            continue  # Handled separately
        if sig not in IMPORT_DISPATCH:
            continue
        simple_types.add(sig)

    # Types that need the writer passed in (for companion record generation)
    _WRITER_TYPES = {'ARMO', 'CLOT', 'WEAP', 'AMMO', 'NPC_', 'CREA'}

    converted = 0
    errors = 0

    # Build work items: list of (sig, target_sig, rec) tuples
    work_items = []
    for sig in sorted(simple_types):
        target_sig = TYPE_MAP.get(sig, sig)
        for rec in by_type[sig]:
            work_items.append((sig, target_sig, rec))

    _worker_count = max(1, (os.cpu_count() or 4) - 1)

    def _convert_one(item):
        sig, target_sig, rec = item
        converter = IMPORT_DISPATCH[sig]
        if sig in _WRITER_TYPES:
            record_bytes = converter(rec, writer=writer)
        else:
            record_bytes = converter(rec)
        return sig, target_sig, record_bytes, None, None

    with ThreadPoolExecutor(max_workers=_worker_count) as ex:
        future_map = {ex.submit(_convert_one, item): item for item in work_items}
        for future in as_completed(future_map):
            sig, target_sig, rec = future_map[future]
            try:
                _, target_sig, record_bytes, _, _ = future.result()
                writer.add_record(target_sig, record_bytes)
                converted += 1
            except Exception as e:
                edid = get_str(rec, 'EditorID', '?')
                print(f"  ERROR converting {sig} '{edid}': {e}")
                errors += 1

    # --- Phase 2: LTEX (creates TXST companion records) ---
    ltex_records = by_type.get('LTEX', [])
    if ltex_records:
        print(f"  Converting {len(ltex_records)} LTEX records (with TXST creation)...")
        for rec in ltex_records:
            try:
                ltex_bytes, txst_bytes, txst_fid = convert_LTEX(rec, writer)
                writer.add_record('LTEX', ltex_bytes)
                if txst_bytes:
                    writer.add_record('TXST', txst_bytes)
                converted += 1
            except Exception as e:
                print(f"  ERROR converting LTEX '{get_str(rec, 'EditorID', '?')}': {e}")
                errors += 1

    # --- Phase 3: SOUN (creates SNDR companion records) ---
    soun_records = by_type.get('SOUN', [])
    if soun_records:
        print(f"  Converting {len(soun_records)} SOUN records (with SNDR creation)...")
        for rec in soun_records:
            try:
                soun_bytes, sndr_bytes, sndr_fid = convert_SOUN(rec, writer)
                writer.add_record('SOUN', soun_bytes)
                if sndr_bytes:
                    writer.add_record('SNDR', sndr_bytes)
                converted += 1
            except Exception as e:
                print(f"  ERROR converting SOUN '{get_str(rec, 'EditorID', '?')}': {e}")
                errors += 1

    # --- Phase 3b: QUST (needs dialogue_quest_fids from pre-scan) ---
    qust_records = by_type.get('QUST', [])
    if qust_records and 'QUST' not in all_skip:
        print(f"  Converting {len(qust_records)} QUST records ({len(dialogue_quest_fids)} are dialogue quests)...")
        for rec in qust_records:
            try:
                qust_bytes = convert_QUST(rec, dialogue_quest_fids=dialogue_quest_fids)
                writer.add_record('QUST', qust_bytes)
                converted += 1
            except Exception as e:
                print(f"  ERROR converting QUST '{get_str(rec, 'EditorID', '?')}': {e}")
                errors += 1

    # --- Phase 4: CELL/WRLD hierarchy ---
    _build_cell_groups(by_type, writer)
    _build_world_groups(by_type, writer)

    # --- Phase 5: DIAL/INFO hierarchy ---
    _build_dialog_groups(by_type, writer, npc_to_vtyp)

    t3 = time.time()
    print(f"\nConverted {converted} records ({errors} errors) in {t3-t2:.2f}s")

    # Write output
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    writer.write(output_path)
    file_size = os.path.getsize(output_path)
    print(f"Wrote {output_path} ({file_size:,} bytes)")

    # Reset FormID remapping
    set_formid_index_offset(0)

    return converted, errors


def _build_cell_groups(by_type: dict, writer: PluginWriter):
    """Build CELL group hierarchy (interior cells only — exterior in WRLD)."""
    cells = by_type.get('CELL', [])
    refrs = by_type.get('REFR', [])
    achrs = by_type.get('ACHR', []) + by_type.get('ACRE', [])
    lands = by_type.get('LAND', [])

    # Group children by parent CELL
    refr_by_cell = defaultdict(list)
    for rec in refrs:
        cell_fid = get_formid(rec, 'ParentCELL')
        refr_by_cell[cell_fid].append(rec)

    achr_by_cell = defaultdict(list)
    for rec in achrs:
        cell_fid = get_formid(rec, 'ParentCELL')
        achr_by_cell[cell_fid].append(rec)

    land_by_cell = defaultdict(list)
    for rec in lands:
        cell_fid = get_formid(rec, 'ParentCELL')
        land_by_cell[cell_fid].append(rec)

    # Interior cells = those NOT in a worldspace
    interior_cells = [c for c in cells if not get_formid(c, 'ParentWRLD')]

    if not interior_cells:
        return

    print(f"  Building CELL hierarchy ({len(interior_cells)} interior cells)...")

    # Group interior cells into blocks/sub-blocks
    # Block = formID last 2 bytes >> 12, Sub-block = formID >> 8 & 0xF (simplified)
    blocks = defaultdict(lambda: defaultdict(list))
    for cell in interior_cells:
        fid = get_formid(cell, 'FormID')
        block_num = fid % 10  # Simplified block assignment
        sub_block_num = (fid // 10) % 10
        blocks[block_num][sub_block_num].append(cell)

    all_cell_content = b''
    converted = 0

    for block_num in sorted(blocks.keys()):
        block_content = b''
        for sub_block_num in sorted(blocks[block_num].keys()):
            sub_block_content = b''
            for cell_rec in blocks[block_num][sub_block_num]:
                cell_fid = get_formid(cell_rec, 'FormID')
                try:
                    cell_bytes = convert_CELL(cell_rec)
                    sub_block_content += cell_bytes

                    # Cell children
                    children_content = b''

                    # Persistent children (group type 8)
                    persistent = b''

                    def is_persistent(r):
                        return get_int(r, 'RecordFlags') & 0x400  # Persistent flag
                    for refr_rec in refr_by_cell.get(cell_fid, []):
                        if is_persistent(refr_rec):
                            persistent += convert_REFR(refr_rec)
                            converted += 1
                    for achr_rec in achr_by_cell.get(cell_fid, []):
                        if is_persistent(achr_rec):
                            persistent += convert_ACHR(achr_rec)
                            converted += 1
                    if persistent:
                        children_content += pack_group(8, struct.pack('<I', cell_fid), persistent)

                    # Temporary children (group type 9)
                    temporary = b''
                    for refr_rec in refr_by_cell.get(cell_fid, []):
                        if not is_persistent(refr_rec):
                            temporary += convert_REFR(refr_rec)
                            converted += 1
                    for achr_rec in achr_by_cell.get(cell_fid, []):
                        if not is_persistent(achr_rec):
                            temporary += convert_ACHR(achr_rec)
                            converted += 1
                    for land_rec in land_by_cell.get(cell_fid, []):
                        temporary += convert_LAND(land_rec)
                        converted += 1
                    if temporary:
                        children_content += pack_group(9, struct.pack('<I', cell_fid), temporary)

                    if children_content:
                        sub_block_content += pack_group(6, struct.pack('<I', cell_fid), children_content)

                    converted += 1
                except Exception as e:
                    print(f"  ERROR building CELL group for {get_str(cell_rec, 'EditorID', '?')}: {e}")

            if sub_block_content:
                block_content += pack_group(3, struct.pack('<i', sub_block_num), sub_block_content)
        if block_content:
            all_cell_content += pack_group(2, struct.pack('<i', block_num), block_content)

    if all_cell_content:
        writer.add_raw_group('CELL', all_cell_content)

    print(f"    Interior cells: {len(interior_cells)}, children: {converted}")


def _build_world_groups(by_type: dict, writer: PluginWriter):
    """Build WRLD group hierarchy (worldspaces + exterior cells)."""
    worlds = by_type.get('WRLD', [])
    cells = by_type.get('CELL', [])
    refrs = by_type.get('REFR', [])
    achrs = by_type.get('ACHR', []) + by_type.get('ACRE', [])
    lands = by_type.get('LAND', [])

    if not worlds:
        return

     # Index exterior cells by worldspace
    ext_cells_by_wrld = defaultdict(list)
    for cell in cells:
        wrld_fid = get_formid(cell, 'ParentWRLD')
        if wrld_fid:
            ext_cells_by_wrld[wrld_fid].append(cell)

    # Index children by parent cell
    refr_by_cell = defaultdict(list)
    for rec in refrs:
        cell_fid = get_formid(rec, 'ParentCELL')
        refr_by_cell[cell_fid].append(rec)

    achr_by_cell = defaultdict(list)
    for rec in achrs:
        cell_fid = get_formid(rec, 'ParentCELL')
        achr_by_cell[cell_fid].append(rec)

    land_by_cell = defaultdict(list)
    for rec in lands:
        cell_fid = get_formid(rec, 'ParentCELL')
        land_by_cell[cell_fid].append(rec)

    print(f"  Building WRLD hierarchy ({len(worlds)} worldspaces)...")
    converted = 0
    all_wrld_content = b''

    for wrld_rec in sorted(worlds, key=lambda w: get_formid(w, 'FormID')):
        wrld_fid = get_formid(wrld_rec, 'FormID')
        try:
            wrld_bytes = convert_WRLD(wrld_rec)
            wrld_children = b''

            wrld_cells = ext_cells_by_wrld.get(wrld_fid, [])

            # Separate the persistent worldspace cell from regular exterior cells.
            # The persistent cell has RecordFlags & 0x400 (Persistent bit) set and is
            # placed directly under the WRLD type=1 group without block/sub-block wrapping.
            # It often has XCLC=(0,0) so the old empty-string check was wrong.
            # Some worldspaces have multiple persistent cells (IC districts, Oblivion planes).
            persistent_cells = []
            exterior_cells = []
            for cell in wrld_cells:
                if get_int(cell, 'RecordFlags') & 0x400:
                    persistent_cells.append(cell)
                else:
                    exterior_cells.append(cell)

            # Persistent worldspace cells (group type 6 per cell)
            def is_persistent(r):
                return get_int(r, 'RecordFlags') & 0x400
            for persistent_cell in persistent_cells:
                pcell_fid = get_formid(persistent_cell, 'FormID')
                pcell_bytes = convert_CELL(persistent_cell)
                wrld_children += pcell_bytes

                # Persistent cell children
                pcell_children = b''
                persistent = b''

                for refr in refr_by_cell.get(pcell_fid, []):
                    if is_persistent(refr):
                        persistent += convert_REFR(refr)
                        converted += 1
                for achr in achr_by_cell.get(pcell_fid, []):
                    if is_persistent(achr):
                        persistent += convert_ACHR(achr)
                        converted += 1
                if persistent:
                    pcell_children += pack_group(8, struct.pack('<I', pcell_fid), persistent)

                temporary = b''
                for refr in refr_by_cell.get(pcell_fid, []):
                    if not is_persistent(refr):
                        temporary += convert_REFR(refr)
                        converted += 1
                for achr in achr_by_cell.get(pcell_fid, []):
                    if not is_persistent(achr):
                        temporary += convert_ACHR(achr)
                        converted += 1
                if temporary:
                    pcell_children += pack_group(9, struct.pack('<I', pcell_fid), temporary)

                if pcell_children:
                    wrld_children += pack_group(6, struct.pack('<I', pcell_fid), pcell_children)

            # Exterior cells — grouped by block/sub-block
            if exterior_cells:
                ext_blocks = defaultdict(lambda: defaultdict(list))
                for cell in exterior_cells:
                    grid_x = get_int(cell, 'XCLC.X')
                    grid_y = get_int(cell, 'XCLC.Y')
                    # Block = floor(grid / 32), Sub-block = floor(grid / 8)
                    # Use Python // which is floor division (correct for negatives)
                    block_x = grid_x // 32
                    block_y = grid_y // 32
                    sub_x = grid_x // 8
                    sub_y = grid_y // 8
                    block_label = struct.pack('<hh', block_y, block_x)
                    sub_label = struct.pack('<hh', sub_y, sub_x)
                    ext_blocks[block_label][sub_label].append(cell)

                for block_label in sorted(ext_blocks.keys()):
                    block_content = b''
                    for sub_label in sorted(ext_blocks[block_label].keys()):
                        sub_content = b''
                        for cell_rec in sorted(ext_blocks[block_label][sub_label],
                                               key=lambda c: (get_int(c, 'XCLC.Y'), get_int(c, 'XCLC.X'))):
                            cell_fid = get_formid(cell_rec, 'FormID')
                            cell_bytes = convert_CELL(cell_rec)
                            sub_content += cell_bytes

                            cell_children = b''
                            persistent = b''
                            for refr in refr_by_cell.get(cell_fid, []):
                                if get_int(refr, 'RecordFlags') & 0x400:
                                    persistent += convert_REFR(refr)
                                    converted += 1
                            for achr in achr_by_cell.get(cell_fid, []):
                                if get_int(achr, 'RecordFlags') & 0x400:
                                    persistent += convert_ACHR(achr)
                                    converted += 1
                            if persistent:
                                cell_children += pack_group(8, struct.pack('<I', cell_fid), persistent)

                            temporary = b''
                            for refr in refr_by_cell.get(cell_fid, []):
                                if not (get_int(refr, 'RecordFlags') & 0x400):
                                    temporary += convert_REFR(refr)
                                    converted += 1
                            for achr in achr_by_cell.get(cell_fid, []):
                                if not (get_int(achr, 'RecordFlags') & 0x400):
                                    temporary += convert_ACHR(achr)
                                    converted += 1
                            for land in land_by_cell.get(cell_fid, []):
                                temporary += convert_LAND(land)
                                converted += 1
                            if temporary:
                                cell_children += pack_group(9, struct.pack('<I', cell_fid), temporary)

                            if cell_children:
                                sub_content += pack_group(6, struct.pack('<I', cell_fid), cell_children)

                            converted += 1
                        if sub_content:
                            block_content += pack_group(5, sub_label, sub_content)
                    if block_content:
                        wrld_children += pack_group(4, block_label, block_content)

            # Wrap in world children group (type 1)
            wrld_group_content = wrld_bytes
            if wrld_children:
                wrld_group_content += pack_group(1, struct.pack('<I', wrld_fid), wrld_children)

            all_wrld_content += wrld_group_content
            converted += 1

        except Exception as e:
            print(f"  ERROR building WRLD group for {get_str(wrld_rec, 'EditorID', '?')}: {e}")

    if all_wrld_content:
        writer.add_raw_group('WRLD', all_wrld_content)

    print(f"    Worldspaces: {len(worlds)}, children: {converted}")


def _collect_dialogue_quest_fids(by_type: dict) -> set:
    """Pre-scan DIAL records and collect all quest FormIDs that own dialogue.

    These QUSTs need StartGameEnabled + StartsEnabled flags.
    Must run BEFORE Phase 1 so QUST conversion can use the result.

    Only includes quests from non-skipped DIAL topics — persuasion,
    service UI, creature responses etc. are excluded so their quests
    don't get forced StartGameEnabled.
    """
    dialogue_quest_fids = set()
    for rec in by_type.get('DIAL', []):
        if should_skip_dial(rec):
            continue
        qcount = get_int(rec, 'QuestCount')
        for i in range(qcount):
            quest_fid = get_formid(rec, f'Quest[{i}]')
            if quest_fid:
                dialogue_quest_fids.add(quest_fid)
    return dialogue_quest_fids


def _build_npc_to_vtyp_map(by_type: dict, num_new_masters: int) -> dict:
    """Build NPC FormID → VTYP FormID mapping from NPC_ + CREA export data.

    Used to derive GetIsVoiceType conditions for INFO records:
    - INFO conditions contain GetIsID(npc_formid) → we look up the NPC's
      voice type via this mapping
    - For generic INFOs (no GetIsID) → all voice types are added

    FormIDs are stored in remapped form (with load-order offset applied).
    """
    npc_to_vtyp = {}
    offset = num_new_masters

    for sig in ('NPC_', 'CREA'):
        for rec in by_type.get(sig, []):
            raw_fid = int(rec.get('FormID', '0'), 16)
            # Apply same load-order remapping as text_reader
            if (raw_fid >> 24) == 0x00 and (raw_fid & 0x00FFFFFF) >= 0x100:
                remapped_fid = (raw_fid & 0x00FFFFFF) | (offset << 24)
            else:
                remapped_fid = raw_fid

            # Resolve race → voice type
            tes4_race_fid = get_formid(rec, 'RNAM.Race')
            race_edid = TES4_RACE_FID_TO_EDID.get(
                tes4_race_fid & 0x00FFFFFF, 'Imperial')
            tes4_flags = get_int(rec, 'ACBS.Flags')
            gender = 'Female' if (tes4_flags & 1) else 'Male'

            vtyp = VOICE_TYPE_MAP.get((race_edid, gender))
            if not vtyp:
                vtyp = VOICE_TYPE_MAP.get(('Imperial', gender), 0)
            if vtyp:
                npc_to_vtyp[remapped_fid] = vtyp

    return npc_to_vtyp


def _build_dialog_groups(by_type: dict, writer: PluginWriter,
                         npc_to_vtyp: dict):
    """Build DIAL/INFO/DLBR/DLVW group hierarchy.

    This is the core of Skyrim dialogue conversion.  For each DIAL topic:

    1. Determine bark vs conversation:
       - Barks (greetings, combat, detection, idle): Category 3/5/7, NO DLBR
       - Conversation topics: Category 0, needs DLBR branch

    2. For conversation topics, allocate and create a DLBR record:
       - DLBR links QNAM→quest, SNAM→dial, DNAM=TopLevel
       - DIAL gets BNAM→DLBR FormID

    3. For each INFO, build GetIsVoiceType CTDA conditions:
       - Extract GetIsID(npc_fid) from existing conditions → map to VTYP
       - If no GetIsID (generic line) → add ALL voice types
       - Conditions are OR'd (any matching voice type fires)

    4. Optionally create DLVW records (CK UI metadata, not strictly required
       for runtime but helps if the plugin is opened in the Creation Kit).
    """
    dials = by_type.get('DIAL', [])
    infos = by_type.get('INFO', [])

    if not dials:
        return

    # Group INFOs by parent DIAL
    info_by_dial = defaultdict(list)
    for rec in infos:
        dial_fid = get_formid(rec, 'ParentDIAL')
        info_by_dial[dial_fid].append(rec)

    print(f"  Building DIAL hierarchy ({len(dials)} topics, {len(infos)} infos, "
          f"{len(npc_to_vtyp)} NPC->VTYP mappings)...")

    # Create a catch-all dialogue quest for orphan DIALs (no TES4 quest).
    # ALL Skyrim DIALs MUST have QNAM — engine ignores topics without one.
    # Matches Skyrim's DialogueGeneric pattern: StartGameEnabled + StartsEnabled.
    catchall_quest_fid = writer.alloc_formid()
    catchall_subs = pack_string_subrecord('EDID', 'TES4DialogueGeneric')
    catchall_subs += pack_string_subrecord('FULL', 'TES4 Dialogue Generic')
    catchall_subs += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))
    catchall_subs += pack_subrecord('NEXT', b'')
    catchall_subs += pack_uint32_subrecord('ANAM', 0)
    writer.add_record('QUST', pack_record('QUST', catchall_quest_fid, 0, catchall_subs))
    orphan_count = 0

    dial_converted = 0
    info_converted = 0
    dlbr_created = 0
    dlvw_created = 0
    skipped_count = 0
    all_dial_content = b''
    all_dlbr_records = b''
    all_dlvw_records = b''

    # Per-quest DLBR tracking (for DLVW creation)
    quest_branches: dict[int, list] = defaultdict(list)  # quest_fid → [dlbr_fid, ...]
    quest_topics: dict[int, list] = defaultdict(list)    # quest_fid → [dial_fid, ...]

    for dial_rec in dials:
        dial_fid = get_formid(dial_rec, 'FormID')
        dial_edid = get_str(dial_rec, 'EditorID', '')
        quest_fid = get_formid(dial_rec, 'Quest[0]')
        dtype = get_int(dial_rec, 'DATA.Type')

        if should_skip_dial(dial_rec):
            skipped_count += 1
            continue

        # Assign orphan DIALs (no quest) to the catch-all quest
        if not quest_fid:
            quest_fid = catchall_quest_fid
            orphan_count += 1

        try:
            # Determine if this is a bark or conversation topic
            bark = is_bark_topic(dial_edid, dtype=dtype)
            dlbr_fid = 0

            if not bark and quest_fid:
                # Conversation topic — create a DLBR
                dlbr_fid = writer.alloc_formid()
                dlbr_edid = f'TES4_{dial_edid}_Branch' if dial_edid else f'TES4_DLBR_{dlbr_fid:08X}'
                dlbr_bytes = make_dlbr(dlbr_fid, dlbr_edid, quest_fid, dial_fid,
                                       top_level=True)
                all_dlbr_records += dlbr_bytes
                dlbr_created += 1
                quest_branches[quest_fid].append(dlbr_fid)

            quest_topics[quest_fid].append(dial_fid)

            # Convert child INFOs with voice type injection
            topic_children = b''
            child_info_count = 0
            for info_rec in info_by_dial.get(dial_fid, []):
                try:
                    voice_ctdas = build_voice_type_ctdas_for_info(
                        info_rec, npc_to_vtyp)
                    info_bytes = convert_INFO(info_rec, voice_type_ctdas=voice_ctdas,
                                              is_bark=bark)
                    topic_children += info_bytes
                    child_info_count += 1
                    info_converted += 1
                except Exception as e:
                    print(f"  ERROR converting INFO: {e}")

            # Convert DIAL with DLBR linkage and correct category
            # Pass quest_fid_override for orphan DIALs so QNAM is always set
            orig_quest = get_formid(dial_rec, 'Quest[0]')
            override_fid = quest_fid if not orig_quest else 0
            dial_bytes = convert_DIAL(dial_rec, info_count=child_info_count,
                                      dlbr_fid=dlbr_fid,
                                      quest_fid_override=override_fid)
            dial_group_content = dial_bytes

            if topic_children:
                dial_group_content += pack_group(
                    7, struct.pack('<I', dial_fid), topic_children)

            all_dial_content += dial_group_content
            dial_converted += 1

        except Exception as e:
            print(f"  ERROR building DIAL group for {dial_edid or '?'}: {e}")

    # Create DLVW records (one per quest that has branches)
    for qfid, branch_list in quest_branches.items():
        try:
            dlvw_fid = writer.alloc_formid()
            dlvw_edid = f'TES4_DLVW_{qfid:08X}'
            topic_list = quest_topics.get(qfid, [])
            dlvw_bytes = make_dlvw(dlvw_fid, dlvw_edid, qfid,
                                   branch_list, topic_list)
            all_dlvw_records += dlvw_bytes
            dlvw_created += 1
        except Exception as e:
            print(f"  ERROR creating DLVW for quest {qfid:08X}: {e}")

    # Write all groups
    if all_dial_content:
        writer.add_raw_group('DIAL', all_dial_content)

    if all_dlbr_records:
        writer.add_raw_group('DLBR', all_dlbr_records)

    if all_dlvw_records:
        writer.add_raw_group('DLVW', all_dlvw_records)

    print(f"    Topics: {dial_converted}, infos: {info_converted}, "
          f"branches: {dlbr_created}, views: {dlvw_created}, "
          f"skipped: {skipped_count}, "
          f"orphan DIALs assigned to catch-all quest: {orphan_count}")


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


if __name__ == "__main__":
    main()
