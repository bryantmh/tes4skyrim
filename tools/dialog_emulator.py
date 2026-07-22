#!/usr/bin/env python3
"""
Skyrim Dialog Engine Emulator — simulates how Skyrim routes dialog to NPCs.

Parses a TES5 ESM/ESP binary and evaluates which dialog topics and responses
each NPC would see, based on Skyrim's actual condition evaluation logic.

Usage:
    # Test a single NPC
    python tools/dialog_emulator.py output/oblivion.esm/Oblivion.esm --npc Jauffre

    # Compare what an NPC sees in two ESMs
    python tools/dialog_emulator.py output/oblivion.esm/Oblivion.esm --npc Jauffre --compare Skyrim.esm

    # Batch test all NPCs, report issues
    python tools/dialog_emulator.py output/oblivion.esm/Oblivion.esm --batch

    # Test a specific quest's dialog
    python tools/dialog_emulator.py output/oblivion.esm/Oblivion.esm --quest MQ00

    # Full report with cross-NPC collision detection
    python tools/dialog_emulator.py output/oblivion.esm/Oblivion.esm --detect-collisions
"""
import argparse
import struct
import sys
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# Add project root for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.tes5_esm_reader import read_tes5_file, TES5Record, _get, _all, _zstring

# ---------------------------------------------------------------------------
# Engine dialogue tables
#
# Extracted from the unpacked GOG/Anniversary SkyrimSE.exe by
# tools/dialog_engine_extract.py -- see docs/dialogue_engine_contracts.md.
# These are the engine's own tables, not xEdit's alphabetical listing, so the
# ordering and the category each subtype belongs to are authoritative.
# ---------------------------------------------------------------------------
SUBTYPE_NOT_FOUND = 103   # the engine's own "no matching row" sentinel

# INFO response flags. Bit meanings are xEdit's; the containing subrecord's
# layout is the engine's (u16 flags + pad + float, read as 8 bytes).
INFO_FLAG_GOODBYE = 1 << 0
INFO_FLAG_RANDOM = 1 << 1
INFO_FLAG_SAY_ONCE = 1 << 2
INFO_FLAG_REQUIRES_PLAYER_ACTIVATION = 1 << 3
INFO_FLAG_INFO_REFUSAL = 1 << 4
INFO_FLAG_RANDOM_END = 1 << 5
INFO_FLAG_INVISIBLE_CONTINUE = 1 << 6
INFO_FLAG_WALK_AWAY = 1 << 7
INFO_FLAG_WALK_AWAY_INVISIBLE_IN_MENU = 1 << 8
INFO_FLAG_FORCE_SUBTITLE = 1 << 9
INFO_FLAG_CAN_MOVE_WHILE_GREETING = 1 << 10
INFO_FLAG_NO_LIP_FILE = 1 << 11
INFO_FLAG_REQUIRES_POST_PROCESSING = 1 << 12
INFO_FLAG_AUDIO_OUTPUT_OVERRIDE = 1 << 13
INFO_FLAG_SPENDS_FAVOR_POINTS = 1 << 14

# The engine stores the DATA float as trunc(value * this) in a u16.
INFO_RESET_SCALE = 65535.0

_TABLES_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'tes5_import', 'dialog_engine_tables.json')


def _load_engine_tables():
    import json
    with open(_TABLES_PATH, encoding='utf-8') as f:
        tables = json.load(f)
    by_tag = {}
    cat_by_tag = {}
    for row in tables['subtypes']:
        by_tag[row['tag']] = row['index']
        cat_by_tag[row['tag']] = row['category']
    names = {c['id']: c['name'] for c in tables['categories']}
    sub_names = {r['index']: r['name'] for r in tables['subtypes']}
    selectable = {r['index'] for r in tables['subtypes']
                  if r['player_selectable']}
    # A CTDA names its function by `opcode - 0x1000`, so the engine's own table
    # supplies every condition function name and its parameter types. This is
    # broader than xEdit's list (737 vs 402) and free of its transcription
    # errors.
    funcs = {f['ctda_index']: f for f in tables['functions']
             if f.get('ctda_index') is not None}
    func_names = {i: f['name'] for i, f in funcs.items()}
    return by_tag, cat_by_tag, names, sub_names, selectable, funcs, func_names


(SUBTYPE_BY_TAG, CATEGORY_BY_TAG, CATEGORY_NAMES, SUBTYPE_NAMES,
 PLAYER_SELECTABLE_SUBTYPES, ENGINE_FUNCTIONS, FUNC_NAMES) = _load_engine_tables()

# ---------------------------------------------------------------------------
# Condition function IDs
# ---------------------------------------------------------------------------
FUNC_GETACTORVALUE = 14
FUNC_GETDISEASE = 39
FUNC_GETDETECTED = 45
FUNC_GETTALKEDTOPC = 50
FUNC_GETQUESTRUNNING = 56
FUNC_GETSTAGE = 58
FUNC_GETSTAGEDONE = 59
FUNC_GETINCELL = 67
FUNC_GETISCLASS = 68
FUNC_GETISRACE = 69
FUNC_GETISSEX = 70
FUNC_GETINFACTION = 71
FUNC_GETISID = 72
FUNC_GETFACTIONRANK = 73
FUNC_GETGLOBALVALUE = 74
FUNC_GETRANDOMPERC = 77
FUNC_GETQUESTVARIABLE = 79  # GetQuestVariable (NOT GetIsPlayerBirthsign)
FUNC_GETLEVEL = 80
FUNC_GETDEADCOUNT = 84
FUNC_GETDEAD = 46
FUNC_GETQUESTCOMPLETED = 543
FUNC_GETCURRENTAIPROCEDURE = 143
FUNC_ISGUARD = 125
FUNC_GETPCISRACE = 130
# Skyrim's disposition system: relationship rank -4..4, default 0
# (Acquaintance). This is what Oblivion's GetDisposition converts into.
# CTDA 403 (opcode 0x1193); the function sits at ROW 419 of the engine table,
# which is a different number -- 419 as a CTDA index is GetObjectiveCompleted.
FUNC_GETRELATIONSHIPRANK = 403


def _verify_function_indices():
    """Fail loudly if a FUNC_* constant disagrees with the engine's table.

    Six of these were wrong before the engine table existed -- GetInCell was
    reading as GetInFaction, GetDead as GetDeadCount, GetQuestCompleted as
    GetHeadingAngle -- so every condition using them silently evaluated the
    wrong thing. Checking at import makes that class of error impossible to
    reintroduce.
    """
    expected = {
        'FUNC_GETACTORVALUE': 'GetActorValue',
        'FUNC_GETDISEASE': 'GetDisease',
        'FUNC_GETDETECTED': 'GetDetected',
        'FUNC_GETTALKEDTOPC': 'GetTalkedToPC',
        'FUNC_GETQUESTRUNNING': 'GetQuestRunning',
        'FUNC_GETSTAGE': 'GetStage',
        'FUNC_GETSTAGEDONE': 'GetStageDone',
        'FUNC_GETINCELL': 'GetInCell',
        'FUNC_GETISCLASS': 'GetIsClass',
        'FUNC_GETISRACE': 'GetIsRace',
        'FUNC_GETISSEX': 'GetIsSex',
        'FUNC_GETINFACTION': 'GetInFaction',
        'FUNC_GETISID': 'GetIsID',
        'FUNC_GETFACTIONRANK': 'GetFactionRank',
        'FUNC_GETGLOBALVALUE': 'GetGlobalValue',
        'FUNC_GETRANDOMPERC': 'GetRandomPercent',
        'FUNC_GETQUESTVARIABLE': 'GetQuestVariable',
        'FUNC_GETLEVEL': 'GetLevel',
        'FUNC_GETDEAD': 'GetDead',
        'FUNC_GETDEADCOUNT': 'GetDeadCount',
        'FUNC_GETQUESTCOMPLETED': 'GetQuestCompleted',
        'FUNC_ISGUARD': 'IsGuard',
        'FUNC_GETPCISRACE': 'GetPCIsRace',
        'FUNC_GETISVOICETYPE': 'GetIsVoiceType',
        'FUNC_GETRELATIONSHIPRANK': 'GetRelationshipRank',
    }
    wrong = []
    for const, want in expected.items():
        idx = globals().get(const)
        if idx is None:
            continue
        got = FUNC_NAMES.get(idx)
        if got is not None and got != want:
            wrong.append(f'{const}={idx} is {got}, not {want}')
    if wrong:
        raise AssertionError('condition function indices disagree with '
                             'SkyrimSE.exe: ' + '; '.join(wrong))
FUNC_GETTRESPASSWARNINGLEVEL = 144
FUNC_ISTRESPASSING = 145
FUNC_ISINMYOWNEDCELL = 146
FUNC_GETISCURRENTPACKAGE = 161
FUNC_GETISPLAYABLERACE = 254
FUNC_ISSNEAKING = 286
FUNC_GETISVOICETYPE = 426

_verify_function_indices()

# Category names
CAT_NAMES = CATEGORY_NAMES   # the engine's own category names

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Condition:
    """A parsed CTDA condition."""
    type_byte: int
    comp_type: int   # 0==, 1!=, 2>, 3>=, 4<, 5<=
    is_or: bool
    use_global: bool
    comp_value: float
    func_idx: int
    param1: int
    param2: int
    run_on: int      # 0=Subject, 1=Target, 2=Reference, 3=CombatTarget, 4=LinkedRef, 5=QuestAlias, 14=PackData
    reference: int
    raw: bytes

    @staticmethod
    def from_bytes(data: bytes) -> Optional['Condition']:
        if len(data) < 32:
            return None
        tb = data[0]
        return Condition(
            type_byte=tb,
            comp_type=(tb >> 5) & 0x07,
            is_or=bool(tb & 0x01),
            use_global=bool(tb & 0x04),
            comp_value=struct.unpack_from('<f', data, 4)[0],
            func_idx=struct.unpack_from('<H', data, 8)[0],
            param1=struct.unpack_from('<I', data, 12)[0],
            param2=struct.unpack_from('<I', data, 16)[0],
            run_on=struct.unpack_from('<I', data, 20)[0],
            reference=struct.unpack_from('<I', data, 24)[0],
            raw=data[:32],
        )


@dataclass
class NPCData:
    """Parsed NPC data for condition evaluation."""
    form_id: int
    editor_id: str
    voice_type: int      # VTCK FormID
    race: int            # RACE FormID
    is_female: bool      # from ACBS flags
    is_guard: bool       # from ACBS flags bit 5
    factions: dict       # faction_fid → rank
    level: int
    name: str            # FULL
    cell_fids: set = field(default_factory=set)  # CELL FormIDs from ACHR placement
    wrld_fids: set = field(default_factory=set)  # WRLD FormIDs from ACHR placement
    is_interior: bool = False                     # True if any placement is in an interior cell


@dataclass
class DIALData:
    """Parsed DIAL topic data."""
    form_id: int
    editor_id: str
    full_name: str       # display name
    quest_fid: int       # QNAM
    category: int        # DATA category
    subtype: int         # DATA subtype
    snam_code: str       # 4-char subtype ('HELO','CUST','GBYE',etc.)
    has_branch: bool     # has BNAM (DLBR link)
    branch_fid: int      # BNAM FormID
    priority: float      # PNAM priority


@dataclass
class INFOData:
    """Parsed INFO record data."""
    form_id: int
    editor_id: str
    parent_dial: int
    conditions: list     # list of Condition
    responses: list      # list of (text, emotion_type, emotion_value)
    tclt_targets: list   # TCLT FormIDs (next topic links)
    enam_flags: int      # ENAM flags
    favor_level: int     # CNAM
    # INFO DATA, per TESTopicInfo::LoadForm (SkyrimSE.exe 0x3aa9a4): the
    # subrecord is 8 bytes -- u16 flags, 2 bytes padding, then a float that the
    # engine multiplies by 65535 and truncates to a u16 reset counter. It is
    # NOT a count of hours.
    info_flags: int = 0
    reset_fraction: float = 0.0

    @property
    def reset_ticks(self) -> int:
        """The u16 the engine actually stores, i.e. trunc(fraction * 65535)."""
        return int(self.reset_fraction * 65535.0)

    @property
    def say_once(self) -> bool:
        return bool(self.info_flags & INFO_FLAG_SAY_ONCE)

    @property
    def is_random(self) -> bool:
        return bool(self.info_flags & INFO_FLAG_RANDOM)

    @property
    def is_goodbye(self) -> bool:
        return bool(self.info_flags & INFO_FLAG_GOODBYE)


@dataclass
class QUSTData:
    """Parsed QUST data."""
    form_id: int
    editor_id: str
    dnam_flags: int
    priority: int
    full_name: str
    is_start_game_enabled: bool
    is_starts_enabled: bool
    dials: list  # FormIDs of DIALs owned by this quest


@dataclass
class DLBRData:
    """Parsed DLBR (dialog branch) data."""
    form_id: int
    editor_id: str
    quest_fid: int    # QNAM
    start_dial: int   # SNAM (starting topic)
    branch_type: int  # DNAM (0=Normal, 1=TopLevel, 2=Blocking, 4=Exclusive)


# ---------------------------------------------------------------------------
# ESM Parser - extracts dialog-relevant records
# ---------------------------------------------------------------------------

class DialogDB:
    """Database of all dialog-relevant records from an ESM."""

    def __init__(self, filepath: str, label: str = ''):
        self.filepath = filepath
        self.label = label or os.path.basename(filepath)
        self.npcs: dict[int, NPCData] = {}
        self.dials: dict[int, DIALData] = {}
        self.infos: dict[int, INFOData] = {}
        self.qusts: dict[int, QUSTData] = {}
        self.dlbrs: dict[int, DLBRData] = {}
        self.vtyps: dict[int, str] = {}  # fid → editor_id
        self.facts: dict[int, str] = {}  # fid → editor_id
        self.globs: dict[int, str] = {}         # fid → editor_id
        self.glob_values: dict[int, float] = {}  # fid → authored FLTV
        self.glob_by_edid: dict[str, int] = {}   # lowercased editor_id → fid
        self.cells: dict[int, str] = {}  # cell_fid → editor_id
        self.npc_cells: dict[int, set[int]] = defaultdict(set)  # npc_fid → {cell_fids}

        # Indexes built after loading
        self.infos_by_dial: dict[int, list[INFOData]] = defaultdict(list)
        self.dials_by_quest: dict[int, list[DIALData]] = defaultdict(list)
        self.npc_by_edid: dict[str, NPCData] = {}
        self.quest_by_edid: dict[str, QUSTData] = {}

        self._load(filepath)
        self._build_indexes()

    def _load(self, filepath: str):
        """Parse all dialog-relevant records from ESM."""
        print(f"Loading {filepath}...", file=sys.stderr)
        header, records, is_localized = read_tes5_file(filepath)
        print(f"  {len(records)} records, localized={is_localized}", file=sys.stderr)

        # Temporary: collect CELL data and ACHR placements for NPC→cell mapping
        cell_data: dict[int, tuple[str, int, int]] = {}  # cell_fid → (edid, flags, parent_wrld)
        achr_placements: list[tuple[int, int, int]] = []  # (npc_base_fid, cell_fid, wrld_fid)

        for rec in records:
            if rec.type == 'NPC_':
                self._parse_npc(rec, is_localized)
            elif rec.type == 'DIAL':
                self._parse_dial(rec, is_localized)
            elif rec.type == 'INFO':
                self._parse_info(rec)
            elif rec.type == 'QUST':
                self._parse_qust(rec, is_localized)
            elif rec.type == 'DLBR':
                self._parse_dlbr(rec)
            elif rec.type == 'VTYP':
                edid_sub = _get(rec, 'EDID')
                if edid_sub:
                    self.vtyps[rec.form_id] = _zstring(edid_sub.data)
            elif rec.type == 'FACT':
                edid_sub = _get(rec, 'EDID')
                if edid_sub:
                    self.facts[rec.form_id] = _zstring(edid_sub.data)
            elif rec.type == 'GLOB':
                # The converter re-expresses Oblivion's AddTopic gating as one
                # TES4Unlock_<topic> global per gated topic, so a global's
                # authored value is the starting state of that gate.
                edid_sub = _get(rec, 'EDID')
                fltv_sub = _get(rec, 'FLTV')
                if edid_sub:
                    name = _zstring(edid_sub.data)
                    self.globs[rec.form_id] = name
                    self.glob_by_edid[name.lower()] = rec.form_id
                    if fltv_sub and len(fltv_sub.data) >= 4:
                        self.glob_values[rec.form_id] = struct.unpack_from(
                            '<f', fltv_sub.data, 0)[0]
            elif rec.type == 'CELL':
                edid_sub = _get(rec, 'EDID')
                edid = _zstring(edid_sub.data) if edid_sub else ''
                # DATA flags: bit 0 = Is Interior Cell
                data_sub = _get(rec, 'DATA')
                cell_flags = struct.unpack_from('<H', data_sub.data, 0)[0] if data_sub and len(data_sub.data) >= 2 else 0
                cell_data[rec.form_id] = (edid, cell_flags, rec.parent_wrld)
                if edid:
                    self.cells[rec.form_id] = edid
            elif rec.type == 'ACHR':
                # NAME = base NPC FormID
                name_sub = _get(rec, 'NAME')
                if name_sub and len(name_sub.data) >= 4:
                    base_fid = struct.unpack_from('<I', name_sub.data, 0)[0]
                    achr_placements.append((base_fid, rec.parent_cell, rec.parent_wrld))

        # Build NPC→cell/worldspace mapping from ACHR placements
        for base_fid, cell_fid, wrld_fid in achr_placements:
            if base_fid in self.npcs:
                npc = self.npcs[base_fid]
                if cell_fid:
                    npc.cell_fids.add(cell_fid)
                    self.npc_cells[base_fid].add(cell_fid)
                if wrld_fid:
                    npc.wrld_fids.add(wrld_fid)
                # Check if placed in interior cell
                if cell_fid in cell_data:
                    _, cell_flags, _ = cell_data[cell_fid]
                    if cell_flags & 0x0001:  # Is Interior Cell
                        npc.is_interior = True

        placed_npcs = sum(1 for n in self.npcs.values() if n.cell_fids)
        print(f"  Loaded: {len(self.npcs)} NPCs ({placed_npcs} with cell placement), "
              f"{len(self.dials)} DIALs, {len(self.infos)} INFOs, {len(self.qusts)} QUSTs, "
              f"{len(self.dlbrs)} DLBRs, {len(self.vtyps)} VTYPs, {len(cell_data)} CELLs, "
              f"{len(achr_placements)} ACHRs", file=sys.stderr)

    def _parse_npc(self, rec: TES5Record, is_localized: bool):
        edid_sub = _get(rec, 'EDID')
        edid = _zstring(edid_sub.data) if edid_sub else f'NPC_{rec.form_id:08X}'

        # Voice type
        vtck_sub = _get(rec, 'VTCK')
        vtck = struct.unpack_from('<I', vtck_sub.data, 0)[0] if vtck_sub and len(vtck_sub.data) >= 4 else 0

        # Race (TES5 uses RNAM, not RACE)
        race_sub = _get(rec, 'RNAM')
        race = struct.unpack_from('<I', race_sub.data, 0)[0] if race_sub and len(race_sub.data) >= 4 else 0

        # ACBS flags (female = bit 0, guard = bit 5)
        acbs_sub = _get(rec, 'ACBS')
        is_female = False
        is_guard = False
        level = 1
        if acbs_sub and len(acbs_sub.data) >= 24:
            flags = struct.unpack_from('<I', acbs_sub.data, 0)[0]
            is_female = bool(flags & 0x01)
            is_guard = bool(flags & 0x20)  # Bit 5 = IsGuard
            level = struct.unpack_from('<H', acbs_sub.data, 4)[0]

        # Factions
        factions = {}
        for s in rec.subrecords:
            if s.type == 'SNAM' and len(s.data) >= 8:
                fact_fid = struct.unpack_from('<I', s.data, 0)[0]
                rank = struct.unpack_from('<i', s.data, 4)[0]
                factions[fact_fid] = rank

        # Name
        full_sub = _get(rec, 'FULL')
        if full_sub:
            if is_localized and len(full_sub.data) == 4:
                name = f'LString:{struct.unpack_from("<I", full_sub.data, 0)[0]:08X}'
            else:
                name = _zstring(full_sub.data)
        else:
            name = edid

        self.npcs[rec.form_id] = NPCData(
            form_id=rec.form_id, editor_id=edid, voice_type=vtck,
            race=race, is_female=is_female, is_guard=is_guard,
            factions=factions, level=level, name=name,
        )

    def _parse_dial(self, rec: TES5Record, is_localized: bool):
        edid_sub = _get(rec, 'EDID')
        edid = _zstring(edid_sub.data) if edid_sub else ''

        full_sub = _get(rec, 'FULL')
        if full_sub:
            if is_localized and len(full_sub.data) == 4:
                full_name = f'LString:{struct.unpack_from("<I", full_sub.data, 0)[0]:08X}'
            else:
                full_name = _zstring(full_sub.data)
        else:
            full_name = edid

        qnam_sub = _get(rec, 'QNAM')
        quest_fid = struct.unpack_from('<I', qnam_sub.data, 0)[0] if qnam_sub and len(qnam_sub.data) >= 4 else 0

        # TESTopic::LoadForm (SkyrimSE.exe 0x3a6fa8) derives BOTH the subtype and
        # the category by looking SNAM's 4-character tag up in the engine's
        # subtype table and taking the matching row's index; the values stored
        # in DATA are overwritten, because SNAM is parsed after DATA in every
        # one of Skyrim.esm's 15,037 DIAL records. A tag with no row resolves to
        # 103, the engine's not-found sentinel.
        snam_sub = _get(rec, 'SNAM')
        snam_code = ''
        if snam_sub and len(snam_sub.data) >= 4:
            snam_code = snam_sub.data[:4].decode('ascii', errors='replace')
        subtype = SUBTYPE_BY_TAG.get(snam_code, SUBTYPE_NOT_FOUND)
        category = CATEGORY_BY_TAG.get(snam_code, 0)

        bnam_sub = _get(rec, 'BNAM')
        has_branch = bnam_sub is not None
        branch_fid = struct.unpack_from('<I', bnam_sub.data, 0)[0] if bnam_sub and len(bnam_sub.data) >= 4 else 0

        pnam_sub = _get(rec, 'PNAM')
        priority = struct.unpack_from('<f', pnam_sub.data, 0)[0] if pnam_sub and len(pnam_sub.data) >= 4 else 50.0

        self.dials[rec.form_id] = DIALData(
            form_id=rec.form_id, editor_id=edid, full_name=full_name,
            quest_fid=quest_fid, category=category, subtype=subtype,
            snam_code=snam_code, has_branch=has_branch, branch_fid=branch_fid,
            priority=priority,
        )

    def _parse_info(self, rec: TES5Record):
        edid_sub = _get(rec, 'EDID')
        edid = _zstring(edid_sub.data) if edid_sub else ''

        # Conditions
        conditions = []
        for s in rec.subrecords:
            if s.type == 'CTDA':
                c = Condition.from_bytes(s.data)
                if c:
                    conditions.append(c)

        # Responses
        responses = []
        resp_emo_type = 0
        resp_emo_val = 0
        for s in rec.subrecords:
            if s.type == 'TRDT' and len(s.data) >= 24:
                resp_emo_type = struct.unpack_from('<I', s.data, 0)[0]
                resp_emo_val = struct.unpack_from('<I', s.data, 4)[0]
            elif s.type == 'NAM1':
                text = _zstring(s.data) if s.data else ''
                responses.append((text, resp_emo_type, resp_emo_val))

        # TCLT targets
        tclt_targets = []
        for s in rec.subrecords:
            if s.type == 'TCLT' and len(s.data) >= 4:
                tclt_targets.append(struct.unpack_from('<I', s.data, 0)[0])

        # ENAM
        enam_sub = _get(rec, 'ENAM')
        enam_flags = struct.unpack_from('<H', enam_sub.data, 0)[0] if enam_sub and len(enam_sub.data) >= 2 else 0

        # CNAM (favor level)
        cnam_sub = _get(rec, 'CNAM')
        favor_level = cnam_sub.data[0] if cnam_sub and len(cnam_sub.data) >= 1 else 0

        # DATA carries the response flags that decide whether a line is said
        # once, picked at random, or ends the conversation. ENAM holds the same
        # u16; whichever is present wins, and DATA additionally carries the
        # reset float.
        info_flags = enam_flags
        reset_fraction = 0.0
        data_sub = _get(rec, 'DATA')
        if data_sub and len(data_sub.data) >= 8:
            info_flags = struct.unpack_from('<H', data_sub.data, 0)[0]
            reset_fraction = struct.unpack_from('<f', data_sub.data, 4)[0]

        self.infos[rec.form_id] = INFOData(
            form_id=rec.form_id, editor_id=edid, parent_dial=rec.parent_dial,
            conditions=conditions, responses=responses, tclt_targets=tclt_targets,
            enam_flags=enam_flags, favor_level=favor_level,
            info_flags=info_flags, reset_fraction=reset_fraction,
        )

    def _parse_qust(self, rec: TES5Record, is_localized: bool):
        edid_sub = _get(rec, 'EDID')
        edid = _zstring(edid_sub.data) if edid_sub else ''

        dnam_sub = _get(rec, 'DNAM')
        dnam_flags = 0
        priority = 0
        if dnam_sub and len(dnam_sub.data) >= 4:
            dnam_flags = struct.unpack_from('<H', dnam_sub.data, 0)[0]
            priority = dnam_sub.data[2]

        full_sub = _get(rec, 'FULL')
        if full_sub:
            if is_localized and len(full_sub.data) == 4:
                full_name = f'LString:{struct.unpack_from("<I", full_sub.data, 0)[0]:08X}'
            else:
                full_name = _zstring(full_sub.data)
        else:
            full_name = edid

        self.qusts[rec.form_id] = QUSTData(
            form_id=rec.form_id, editor_id=edid, dnam_flags=dnam_flags,
            priority=priority, full_name=full_name,
            is_start_game_enabled=bool(dnam_flags & 0x0001),
            is_starts_enabled=bool(dnam_flags & 0x0010),
            dials=[],
        )

    # DLBR DNAM is a flags field, not an enum (xEdit wbDefinitionsTES5.pas
    # 7193): bit 0 Top-Level, bit 1 Blocking, bit 2 Exclusive. Vanilla
    # Skyrim.esm carries 203 Normal, 2116 Top-Level and 712 Blocking branches,
    # so all three combinations really occur.
    BRANCH_TOP_LEVEL = 1 << 0
    BRANCH_BLOCKING = 1 << 1
    BRANCH_EXCLUSIVE = 1 << 2

    def is_top_level_branch(self, dial) -> bool:
        """Whether this topic is offered in the NPC's topic menu."""
        branch = self.dlbrs.get(dial.branch_fid)
        if branch is None:
            return False
        return bool(branch.branch_type & self.BRANCH_TOP_LEVEL)

    def _parse_dlbr(self, rec: TES5Record):
        edid_sub = _get(rec, 'EDID')
        edid = _zstring(edid_sub.data) if edid_sub else ''

        qnam_sub = _get(rec, 'QNAM')
        quest_fid = struct.unpack_from('<I', qnam_sub.data, 0)[0] if qnam_sub and len(qnam_sub.data) >= 4 else 0

        snam_sub = _get(rec, 'SNAM')
        start_dial = struct.unpack_from('<I', snam_sub.data, 0)[0] if snam_sub and len(snam_sub.data) >= 4 else 0

        dnam_sub = _get(rec, 'DNAM')
        branch_type = struct.unpack_from('<I', dnam_sub.data, 0)[0] if dnam_sub and len(dnam_sub.data) >= 4 else 0

        self.dlbrs[rec.form_id] = DLBRData(
            form_id=rec.form_id, editor_id=edid, quest_fid=quest_fid,
            start_dial=start_dial, branch_type=branch_type,
        )

    def _build_indexes(self):
        """Build cross-reference indexes."""
        for info in self.infos.values():
            self.infos_by_dial[info.parent_dial].append(info)
        for dial in self.dials.values():
            self.dials_by_quest[dial.quest_fid].append(dial)
            # Also register in quest.dials
            if dial.quest_fid in self.qusts:
                self.qusts[dial.quest_fid].dials.append(dial.form_id)
        for npc in self.npcs.values():
            self.npc_by_edid[npc.editor_id] = npc
        for qust in self.qusts.values():
            self.quest_by_edid[qust.editor_id] = qust


# ---------------------------------------------------------------------------
# Condition Evaluator
# ---------------------------------------------------------------------------

class ConditionEvaluator:
    """Evaluates Skyrim dialog conditions against an NPC."""

    # Results for conditions we can evaluate statically
    PASS = True
    FAIL = False
    UNKNOWN = None  # Can't determine (quest stage, etc.)

    def __init__(self, db: DialogDB, npc: NPCData, game_state: dict = None):
        self.db = db
        self.npc = npc
        # game_state allows setting quest stages, etc.
        # For default: all StartGameEnabled quests are running, all at stage 0
        self.game_state = game_state or {}
        # Quest running state
        self.running_quests = set()
        for qust in db.qusts.values():
            if qust.is_start_game_enabled or self.game_state.get(f'quest_running_{qust.form_id}', False):
                self.running_quests.add(qust.form_id)
        # A quest that has been advanced past stage 0 is running by definition,
        # whether or not it is start-game-enabled.
        for key, value in self.game_state.items():
            if key.startswith('stage_') and value:
                try:
                    self.running_quests.add(int(key[len('stage_'):]))
                except ValueError:
                    pass

    def evaluate_condition(self, cond: Condition) -> bool | None:
        """Evaluate a single condition. Returns True, False, or None (unknown)."""
        func = cond.func_idx
        p1 = cond.param1
        p2 = cond.param2
        cv = cond.comp_value
        comp = cond.comp_type

        # Determine the subject for RunOn
        if cond.run_on == 0:  # Subject = NPC being talked to
            subject = self.npc
        elif cond.run_on == 1:  # Target = Player
            # Player-specific conditions with sensible defaults
            if func == FUNC_GETISID:
                # Player is never an NPC — always false
                return self._compare(0.0, comp, cv)
            elif func == FUNC_GETISVOICETYPE:
                return self._compare(0.0, comp, cv)
            elif func == FUNC_GETISRACE:
                return self._compare(0.0, comp, cv)  # Player race unknown, assume no match
            elif func == FUNC_GETPCISRACE:
                return self._compare(0.0, comp, cv)  # Player race unknown, assume no match
            elif func == FUNC_GETQUESTVARIABLE:
                return self._compare(0.0, comp, cv)  # Quest vars never set
            elif func == FUNC_GETRANDOMPERC:
                return self._compare(50.0, comp, cv)  # Mid-range
            elif func == FUNC_GETINFACTION:
                return self._compare(0.0, comp, cv)  # Player not in NPC factions
            elif func == FUNC_GETFACTIONRANK:
                return self._compare(-1.0, comp, cv)  # Not in faction
            elif func == FUNC_GETDEAD:
                return self._compare(0.0, comp, cv)  # Player alive
            elif func == FUNC_GETLEVEL:
                return self._compare(1.0, comp, cv)  # Player level 1 at start
            elif func == FUNC_GETISSEX:
                return self._compare(0.0, comp, cv)  # Assume male player (sex=0)
            # For quest functions, subject doesn't matter
            elif func in (FUNC_GETSTAGE, FUNC_GETQUESTRUNNING,
                          FUNC_GETQUESTCOMPLETED, FUNC_GETSTAGEDONE):
                subject = self.npc  # dummy, quest funcs don't use subject
            else:
                return self._compare(0.0, comp, cv)  # Unknown player condition → 0
        elif cond.run_on == 2:  # Reference
            # Look up the reference NPC
            ref_npc = self.db.npcs.get(cond.reference)
            if ref_npc:
                subject = ref_npc
            else:
                # Reference NPC not found — assume condition fails
                return self._compare(0.0, comp, cv)
        else:
            # Unknown RunOn type — assume condition fails
            return self._compare(0.0, comp, cv)

        if subject is None:
            return self._compare(0.0, comp, cv)  # No subject → assume fails

        # Evaluate based on function
        if func == FUNC_GETISID:
            actual = 1.0 if subject.form_id == p1 else 0.0
        elif func == FUNC_GETISVOICETYPE:
            actual = 1.0 if subject.voice_type == p1 else 0.0
        elif func == FUNC_GETISRACE:
            actual = 1.0 if subject.race == p1 else 0.0
        elif func == FUNC_GETISSEX:
            # p1: 0=Male, 1=Female
            actual = 1.0 if subject.is_female == (cv == 1.0) else 0.0
            # For GetIsSex, comparison with cv
            return self._compare(actual, comp, cv)
        elif func == FUNC_GETINFACTION:
            actual = 1.0 if p1 in subject.factions else 0.0
        elif func == FUNC_GETFACTIONRANK:
            actual = float(subject.factions.get(p1, -1))
        elif func == FUNC_GETDEAD:
            actual = 0.0  # Assume alive
        elif func == FUNC_GETLEVEL:
            actual = float(subject.level)
        elif func == FUNC_GETSTAGE:
            stage = self.game_state.get(f'stage_{p1}', 0)
            actual = float(stage)
        elif func == FUNC_GETQUESTRUNNING:
            actual = 1.0 if p1 in self.running_quests else 0.0
        elif func == FUNC_GETQUESTCOMPLETED:
            completed = self.game_state.get(f'completed_{p1}', False)
            actual = 1.0 if completed else 0.0
        elif func == FUNC_GETSTAGEDONE:
            # GetStageDone(quest, stage): param1=quest, param2=stage number.
            # An explicit stagedone_ entry wins; otherwise a quest that has been
            # advanced to stage N has necessarily run every stage up to N, so
            # asking about an earlier stage answers yes.
            key = f'stagedone_{p1}_{p2}'
            if key in self.game_state:
                done = self.game_state[key]
            else:
                done = p2 <= self.game_state.get(f'stage_{p1}', 0)
            actual = 1.0 if done else 0.0
        elif func == FUNC_GETRELATIONSHIPRANK:
            # Both games start an NPC neutral: Oblivion at disposition 50,
            # Skyrim at rank 0 (Acquaintance). --relationship-rank moves it.
            actual = float(self.game_state.get('relationship_rank', 0))
        elif func == FUNC_GETGLOBALVALUE:
            # Condition function 74 (engine opcode 0x104A). The converter's
            # AddTopic gates are globals, so this is what decides whether a
            # topic has been unlocked. An explicit override wins; otherwise the
            # global's authored starting value applies.
            key = f'global_{p1}'
            if key in self.game_state:
                actual = float(self.game_state[key])
            else:
                actual = float(self.db.glob_values.get(p1, 0.0))
        elif func == FUNC_GETQUESTVARIABLE:
            actual = 0.0  # Quest variables never set (TES4 scripts don't run)
        elif func == 53:  # GetScriptVariable
            actual = 0.0  # Script variables never set
        elif func == FUNC_GETTALKEDTOPC:
            actual = 0.0  # NPC hasn't talked to player yet
        elif func == FUNC_GETDISEASE:
            actual = 0.0  # No disease
        elif func == FUNC_GETISCLASS:
            actual = 0.0  # Class data not loaded — assume no match
        elif func == FUNC_ISGUARD:
            # Check if NPC is a guard — ACBS flag or guard faction name
            is_guard = subject.is_guard
            if not is_guard:
                for fid in subject.factions:
                    fname = self.db.facts.get(fid, '')
                    if 'guard' in fname.lower():
                        is_guard = True
                        break
            actual = 1.0 if is_guard else 0.0
        elif func == FUNC_GETTRESPASSWARNINGLEVEL:
            actual = 0.0  # Default: no warning
        elif func == FUNC_ISTRESPASSING:
            actual = 0.0  # Not trespassing
        elif func == FUNC_ISINMYOWNEDCELL:
            actual = 0.0  # Not in owned cell at game start
        elif func == FUNC_GETISCURRENTPACKAGE:
            actual = 0.0  # Not in any specific package at dialog time
        elif func == FUNC_GETISPLAYABLERACE:
            actual = 1.0  # Most NPCs are playable races
        elif func == FUNC_ISSNEAKING:
            actual = 0.0  # Not sneaking
        elif func == FUNC_GETINCELL:
            # Check if the NPC is placed in the specified cell
            actual = 1.0 if p1 in subject.cell_fids else 0.0
        elif func == FUNC_GETCURRENTAIPROCEDURE:
            actual = 0.0  # Default procedure (wander/stand)
        elif func == FUNC_GETRANDOMPERC:
            actual = 50.0  # Mid-range; most thresholds will pass
        elif func == FUNC_GETACTORVALUE:
            actual = 50.0  # Default actor value (mid-range)
        elif func == 47:  # GetItemCount
            actual = 0.0  # No items at game start
        elif func == 48:  # GetGold
            actual = 0.0  # No gold tracked
        elif func == 84:  # GetDeadCount
            actual = 0.0  # Nobody dead at game start
        elif func == 182:  # GetEquipped
            actual = 0.0  # Can't determine equipment
        elif func == 14:  # GetActorValue
            actual = 50.0  # Default mid-range
        elif func == 192:  # GetIgnoreCrime
            actual = 0.0
        elif func == 193:  # GetPCExpelled
            actual = 0.0  # Not expelled from any faction
        elif func == 300:  # IsInInterior
            actual = 1.0 if subject.is_interior else 0.0
        elif func == 310:  # GetInWorldspace
            actual = 1.0 if p1 in subject.wrld_fids else 0.0
        else:
            actual = 0.0  # Unknown function — assume 0 (safe default)

        return self._compare(actual, comp, cv)

    def _compare(self, actual: float, comp: int, expected: float) -> bool:
        if comp == 0: return actual == expected
        if comp == 1: return actual != expected
        if comp == 2: return actual > expected
        if comp == 3: return actual >= expected
        if comp == 4: return actual < expected
        if comp == 5: return actual <= expected
        return None

    def evaluate_conditions(self, conditions: list[Condition]) -> bool:
        """
        Evaluate a list of conditions using Skyrim's AND/OR logic.

        Skyrim conditions use a chain system:
        - Conditions flagged OR are grouped together
        - An OR group passes if ANY condition in it passes
        - All groups are ANDed together

        Returns True (all pass) or False (fails).
        All conditions are evaluated deterministically — no unknowns.
        """
        if not conditions:
            return True  # No conditions = always passes

        # Split into AND groups (OR chains)
        groups = []
        current_group = []
        for cond in conditions:
            current_group.append(cond)
            if not cond.is_or:
                groups.append(current_group)
                current_group = []
        if current_group:
            groups.append(current_group)

        # Evaluate each group — ALL must pass
        for group in groups:
            if not self._evaluate_or_group(group):
                return False
        return True

    def _evaluate_or_group(self, group: list[Condition]) -> bool:
        """Evaluate an OR group. Any True → True, all False → False."""
        for cond in group:
            if self.evaluate_condition(cond):
                return True
        return False


# ---------------------------------------------------------------------------
# Dialog Simulator
# ---------------------------------------------------------------------------

class DialogSimulator:
    """Simulates dialog interaction with an NPC."""

    def __init__(self, db: DialogDB, game_state: dict = None):
        self.db = db
        self.game_state = game_state or {}

    def get_npc_dialog(self, npc: NPCData) -> dict:
        """
        Get all dialog available to an NPC in the current game state.

        Returns dict with:
        - 'greetings': list of (INFO, DIAL) that would show as greetings
        - 'topics': list of (DIAL, [INFOs]) that would show in menu
        - 'barks': dict of bark_type → list of INFOs
        - 'issues': list of detected problems
        """
        evaluator = ConditionEvaluator(self.db, npc, self.game_state)
        result = {
            'greetings': [],
            'topics': [],      # (DIALData, [matching INFOs]) -- menu topics
            'choice_only': [],  # same, but Normal branches: reachable only by
                                # following a choice link mid-conversation
            'barks': defaultdict(list),
            'issues': [],
        }

        # Iterate all DIALs in running quests
        for quest_fid in evaluator.running_quests:
            quest = self.db.qusts.get(quest_fid)
            if not quest:
                continue

            for dial_fid in quest.dials:
                dial = self.db.dials.get(dial_fid)
                if not dial:
                    continue

                infos = self.db.infos_by_dial.get(dial_fid, [])
                matching_infos = []

                for info in infos:
                    cond_result = evaluator.evaluate_conditions(info.conditions)
                    if cond_result:  # True only (deterministic)
                        matching_infos.append(info)

                if not matching_infos:
                    continue

                # Classify by category
                if dial.snam_code in ('HELO', 'GREE'):
                    for info in matching_infos:
                        result['greetings'].append((info, dial))
                elif dial.category in (3, 5, 7):
                    # Bark
                    bark_type = dial.snam_code or CAT_NAMES.get(dial.category, '?')
                    for info in matching_infos:
                        result['barks'][bark_type].append((info, dial))
                elif dial.category == 0 and dial.has_branch:
                    # Only a TOP-LEVEL branch is offered in the NPC's topic
                    # menu. A Normal branch (DNAM bit 0 clear) is reachable
                    # solely by following a choice link out of a line already
                    # being spoken, which is how Oblivion's mid-conversation
                    # replies -- SadGeneral, AngerReceive, AnswerPositive, the
                    # emotional-response family -- are meant to behave. Listing
                    # them as menu topics made every NPC look like they offered
                    # a dozen permanent topics they cannot actually offer.
                    if self.db.is_top_level_branch(dial):
                        result['topics'].append((dial, matching_infos, quest))
                    else:
                        result['choice_only'].append(
                            (dial, matching_infos, quest))

        return result

    def _print_topic_tree(self, dial, matching_infos, by_dial, show_infos,
                          all_conditions, depth, seen):
        """Print one topic and, nested beneath it, the topics its lines unlock.

        `seen` guards against cycles: Oblivion conversations loop back on
        themselves constantly (a "never mind" reply returning to the topic that
        offered it), and without the guard the walk would not terminate.
        """
        indent = '  ' + '    ' * depth
        all_infos = self.db.infos_by_dial.get(dial.form_id, [])
        marker = '' if depth == 0 else '-> '
        print(f"{indent}{marker}[{dial.form_id:08X}] \"{dial.full_name}\" "
              f"(DIAL: {dial.editor_id}) "
              f"{len(matching_infos)}/{len(all_infos)} passing INFOs")

        if dial.form_id in seen:
            print(f"{indent}    (already shown above)")
            return
        seen = seen | {dial.form_id}

        passing_fids = {i.form_id for i in matching_infos}
        shown = all_infos if all_conditions else matching_infos
        # Several lines of a topic commonly offer the SAME follow-up (each
        # variant of "will you buy the manor?" links to the one "yes" reply), so
        # a target is printed once per topic rather than once per line.
        printed_children = set()
        for info in shown[:10]:
            if show_infos:
                passed = info.form_id in passing_fids
                resp = (info.responses[0][0][:60] if info.responses
                        else '(no text)')
                tag = 'PASS' if passed else 'FAIL'
                print(f"{indent}    [{tag}] [{info.form_id:08X}] {resp}")
                self._print_conditions(info.conditions, indent + '        ')
            # Follow this line's choice links down to their target topics.
            for target in info.tclt_targets:
                child = by_dial.get(target)
                if child is None or target in printed_children:
                    continue
                child_dial, child_infos = child
                if self.db.is_top_level_branch(child_dial):
                    continue   # a real menu topic; it prints in its own right
                printed_children.add(target)
                self._rendered_children.add(child_dial.form_id)
                self._print_topic_tree(child_dial, child_infos, by_dial,
                                       show_infos, all_conditions,
                                       depth + 1, seen)

    def walk_npc(self, npc_edid: str, verbose: bool = True, all_conditions: bool = False):
        """Walk through all dialog for an NPC by EditorID."""
        npc = self.db.npc_by_edid.get(npc_edid)
        if not npc:
            print(f"ERROR: NPC '{npc_edid}' not found")
            return None

        vtyp_name = self.db.vtyps.get(npc.voice_type, f'0x{npc.voice_type:08X}')
        print(f"\n{'='*70}")
        print(f"NPC: {npc.editor_id} ({npc.name}) [{npc.form_id:08X}]")
        print(f"Voice Type: {vtyp_name} [{npc.voice_type:08X}]")
        print(f"Race: {npc.race:08X}, {'Female' if npc.is_female else 'Male'}")
        print(f"Factions: {len(npc.factions)}")
        for fid, rank in npc.factions.items():
            fname = self.db.facts.get(fid, f'{fid:08X}')
            print(f"  {fname}: rank {rank}")
        # Cell placement
        if npc.cell_fids:
            cell_names = [self.db.cells.get(c, f'{c:08X}') for c in npc.cell_fids]
            print(f"Cells: {', '.join(cell_names)}")
            print(f"Interior: {npc.is_interior}")

        dialog = self.get_npc_dialog(npc)

        # Greetings
        print(f"\n--- Greetings ({len(dialog['greetings'])}) ---")
        if not dialog['greetings']:
            print("  *** BUG: No greetings found! NPC will show fallback 'Hello.' ***")
        for info, dial in dialog['greetings']:
            resp = info.responses[0][0][:80] if info.responses else '(no response text)'
            print(f"  [PASS] [{info.form_id:08X}] (DIAL: {dial.editor_id}/{dial.form_id:08X}) {resp}")
            if verbose or all_conditions:
                self._print_conditions(info.conditions, '      ')

        if all_conditions:
            # Show failing INFOs that reference this NPC by FormID (GetIsID) in any greeting DIAL
            passing_info_fids = {info.form_id for info, _ in dialog['greetings']}
            seen_dial_fids = {dial.form_id for _, dial in dialog['greetings']}
            for dial_fid in seen_dial_fids:
                dial = self.db.dials.get(dial_fid)
                if not dial:
                    continue
                for info in self.db.infos_by_dial.get(dial_fid, []):
                    if info.form_id in passing_info_fids:
                        continue
                    # Only show INFOs that reference this NPC directly
                    references_npc = any(
                        c.func_idx == FUNC_GETISID and c.param1 == npc.form_id
                        for c in info.conditions
                    )
                    if not references_npc:
                        continue
                    resp = info.responses[0][0][:80] if info.responses else '(no response text)'
                    print(f"  [FAIL] [{info.form_id:08X}] (DIAL: {dial.editor_id}/{dial.form_id:08X}) {resp}")
                    self._print_conditions(info.conditions, '      ')

        # Topics
        print(f"\n--- Conversation Topics ({len(dialog['topics'])}) ---")
        # A line can offer follow-up topics through its choice links (TCLT).
        # Those targets are Normal branches, so they never appear in the menu on
        # their own -- they exist only as the yes/no (or longer) continuation of
        # the line the player just picked. Render them as children so the shape
        # of the conversation survives.
        self._rendered_children = set()
        by_dial = {d.form_id: (d, m) for d, m, _ in
                   dialog['topics'] + dialog['choice_only']}
        for dial, matching_infos, quest in dialog['topics']:
            self._print_topic_tree(dial, matching_infos, by_dial,
                                   verbose or all_conditions, all_conditions,
                                   depth=0, seen=set())

        # Normal-branch topics reached from a menu topic are printed as children
        # of the line that offers them, above. Anything left here is reachable
        # only from a bark or greeting, so it gets its own section.
        shown_as_child = getattr(self, '_rendered_children', set())
        orphans = [t for t in dialog['choice_only']
                   if t[0].form_id not in shown_as_child]
        if orphans:
            print(f"\n--- Choice-only replies reached from greetings/barks "
                  f"({len(orphans)}) ---")
            for dial, matching_infos, quest in orphans:
                all_infos = self.db.infos_by_dial.get(dial.form_id, [])
                print(f"  [{dial.form_id:08X}] \"{dial.full_name}\" "
                      f"(DIAL: {dial.editor_id}) "
                      f"{len(matching_infos)}/{len(all_infos)} passing INFOs")

        # Barks
        print(f"\n--- Barks ---")
        for bark_type, bark_infos in sorted(dialog['barks'].items()):
            print(f"  {bark_type}: {len(bark_infos)} INFOs")
            if verbose or all_conditions:
                for info, dial in bark_infos[:3]:
                    resp = info.responses[0][0][:60] if info.responses else '(no text)'
                    print(f"    [PASS] [{info.form_id:08X}] {resp}")

        return dialog

    def _fmt_condition(self, c: Condition) -> str:
        """Format a single condition as a string."""
        COMP_NAMES = {0: '==', 1: '!=', 2: '>', 3: '>=', 4: '<', 5: '<='}
        fname = FUNC_NAMES.get(c.func_idx, f'Func{c.func_idx}')
        comp = COMP_NAMES.get(c.comp_type, '?')
        run_str = f' [RunOn={c.run_on}]' if c.run_on != 0 else ''

        p1_fid = f'{c.param1:08X}'
        p1_str = p1_fid
        if c.func_idx == FUNC_GETISID:
            npc = self.db.npcs.get(c.param1)
            if npc:
                p1_str = f'{npc.editor_id}/{p1_fid}'
        elif c.func_idx == FUNC_GETISVOICETYPE:
            vtyp = self.db.vtyps.get(c.param1)
            if vtyp:
                p1_str = f'{vtyp}/{p1_fid}'
        elif c.func_idx in (FUNC_GETSTAGE, FUNC_GETQUESTRUNNING, FUNC_GETQUESTCOMPLETED, FUNC_GETSTAGEDONE):
            qust = self.db.qusts.get(c.param1)
            if qust:
                p1_str = f'{qust.editor_id}/{p1_fid}'
        elif c.func_idx in (FUNC_GETINFACTION, FUNC_GETFACTIONRANK):
            fact = self.db.facts.get(c.param1)
            if fact:
                p1_str = f'{fact}/{p1_fid}'
        elif c.func_idx == FUNC_GETINCELL:
            cell = self.db.cells.get(c.param1)
            if cell:
                p1_str = f'{cell}/{p1_fid}'

        return f'{fname}({p1_str}) {comp} {c.comp_value}{run_str}'

    def _print_conditions(self, conditions: list[Condition], indent: str = '  '):
        """Print conditions grouped by AND/OR logic.

        Each AND group is printed as one line. Single-condition groups are
        printed plain. Multi-condition OR groups are printed as:
            AND( cond1 OR cond2 OR cond3 )
        """
        if not conditions:
            return

        # Split into AND groups (a group ends on the first non-OR condition)
        groups: list[list[Condition]] = []
        current: list[Condition] = []
        for c in conditions:
            current.append(c)
            if not c.is_or:
                groups.append(current)
                current = []
        if current:
            groups.append(current)

        for group in groups:
            if len(group) == 1:
                print(f'{indent}AND {self._fmt_condition(group[0])}')
            else:
                parts = ' OR '.join(self._fmt_condition(c) for c in group)
                print(f'{indent}AND ({parts})')


# ---------------------------------------------------------------------------
# Collision Detector
# ---------------------------------------------------------------------------

def detect_collisions(db: DialogDB, max_npcs: int = 0):
    """
    Detect dialog collisions: topics appearing on wrong NPCs.

    A collision is when an NPC sees a dialog topic that has NO INFO
    with a definitive pass (only unknowns), or when the topic clearly
    belongs to a different NPC based on the conditions.
    """
    sim = DialogSimulator(db)
    npcs = list(db.npcs.values())
    if max_npcs:
        npcs = npcs[:max_npcs]

    print(f"\n{'='*70}")
    print(f"COLLISION DETECTION: {len(npcs)} NPCs")
    print(f"{'='*70}")

    # Track which topics appear on which NPCs
    topic_npc_map = defaultdict(list)  # dial_fid → [(npc, certainty)]
    npc_problems = defaultdict(list)   # npc_edid → [issue descriptions]

    for npc in npcs:
        dialog = sim.get_npc_dialog(npc)

        for dial, matching_infos, quest in dialog['topics']:
            if matching_infos:
                topic_npc_map[dial.form_id].append(npc)

            # Check if this topic has GetIsID for a DIFFERENT NPC
            for info in matching_infos:
                for cond in info.conditions:
                    if cond.func_idx == FUNC_GETISID and cond.run_on == 0:
                        if cond.param1 != npc.form_id:
                            # This INFO has GetIsID for a different NPC but passed
                            # (shouldn't happen unless condition is OR'd)
                            target_npc = db.npcs.get(cond.param1)
                            target_name = target_npc.editor_id if target_npc else f'{cond.param1:08X}'
                            npc_problems[npc.editor_id].append(
                                f'Topic "{dial.full_name}" [{dial.form_id:08X}] '
                                f'has INFO with GetIsID({target_name}) but appears on {npc.editor_id}'
                            )

    # Report topics appearing on multiple NPCs
    print(f"\n--- Topics on Multiple NPCs (potential collisions) ---")
    collision_count = 0
    for dial_fid, npc_entries in sorted(topic_npc_map.items(), key=lambda x: -len(x[1])):
        if len(npc_entries) <= 1:
            continue
        dial = db.dials.get(dial_fid)
        if not dial:
            continue
        if len(npc_entries) > 1:
            collision_count += 1
            if collision_count <= 50:
                quest = db.qusts.get(dial.quest_fid)
                qname = quest.editor_id if quest else '?'
                print(f"  [{dial_fid:08X}] \"{dial.full_name}\" (Quest: {qname})")
                print(f"    NPCs: {', '.join(n.editor_id for n in npc_entries[:10])}")

    print(f"\nTotal topic collisions (same topic, multiple NPCs): {collision_count}")

    # Report per-NPC problems
    if npc_problems:
        print(f"\n--- Per-NPC Issues ---")
        for npc_edid, issues in sorted(npc_problems.items()):
            if len(issues) > 0:
                print(f"  {npc_edid}: {len(issues)} issues")
                for iss in issues[:5]:
                    print(f"    {iss}")

    return topic_npc_map, npc_problems


# ---------------------------------------------------------------------------
# Quest Walk-through
# ---------------------------------------------------------------------------

def walk_quest(db: DialogDB, quest_edid: str):
    """Walk through all dialog for a specific quest."""
    quest = db.quest_by_edid.get(quest_edid)
    if not quest:
        print(f"ERROR: Quest '{quest_edid}' not found")
        return

    print(f"\n{'='*70}")
    print(f"QUEST: {quest.editor_id} ({quest.full_name}) [{quest.form_id:08X}]")
    print(f"Flags: 0x{quest.dnam_flags:04X} (SGE={quest.is_start_game_enabled}, SE={quest.is_starts_enabled})")
    print(f"Priority: {quest.priority}")

    dials = db.dials_by_quest.get(quest.form_id, [])
    print(f"DIALs: {len(dials)}")

    for dial in dials:
        infos = db.infos_by_dial.get(dial.form_id, [])
        cat_name = CAT_NAMES.get(dial.category, '?')
        branch_str = f" Branch={dial.branch_fid:08X}" if dial.has_branch else ""
        print(f"\n  DIAL: {dial.editor_id} \"{dial.full_name}\" [{dial.form_id:08X}]")
        print(f"    Category: {cat_name}({dial.category}) SNAM: {dial.snam_code}{branch_str}")
        print(f"    INFOs: {len(infos)}")

        for info in infos:
            resp = info.responses[0][0][:70] if info.responses else '(no text)'
            conds_summary = []
            for c in info.conditions:
                if c.func_idx == FUNC_GETISID:
                    npc = db.npcs.get(c.param1)
                    conds_summary.append(f"GetIsID({npc.editor_id if npc else f'{c.param1:08X}'})")
                elif c.func_idx == FUNC_GETISVOICETYPE:
                    vtyp = db.vtyps.get(c.param1)
                    conds_summary.append(f"GetIsVoiceType({vtyp or f'{c.param1:08X}'})")
                elif c.func_idx == FUNC_GETSTAGE:
                    q = db.qusts.get(c.param1)
                    COMP = {0: '==', 1: '!=', 2: '>', 3: '>=', 4: '<', 5: '<='}
                    conds_summary.append(f"GetStage({q.editor_id if q else f'{c.param1:08X}'}){COMP.get(c.comp_type,'?')}{c.comp_value:.0f}")
                elif c.func_idx == FUNC_GETQUESTRUNNING:
                    q = db.qusts.get(c.param1)
                    conds_summary.append(f"GetQuestRunning({q.editor_id if q else f'{c.param1:08X}'})")
                else:
                    conds_summary.append(f"Func{c.func_idx}(0x{c.param1:08X})")

            tclt_str = ''
            if info.tclt_targets:
                tclt_names = []
                for t in info.tclt_targets:
                    td = db.dials.get(t)
                    tclt_names.append(td.full_name if td else f'{t:08X}')
                tclt_str = f" -> [{', '.join(tclt_names)}]"

            conds_str = ' | '.join(conds_summary[:5]) if conds_summary else 'NO CONDITIONS'
            print(f"      [{info.form_id:08X}] {resp}")
            print(f"        Conds: {conds_str}{tclt_str}")


# ---------------------------------------------------------------------------
# Batch NPC Testing
# ---------------------------------------------------------------------------

def batch_test(db: DialogDB, max_npcs: int = 0, verbose: bool = False):
    """Test all NPCs and report issues."""
    sim = DialogSimulator(db)
    npcs = list(db.npcs.values())
    if max_npcs:
        npcs = npcs[:max_npcs]

    print(f"\n{'='*70}")
    print(f"BATCH NPC TEST: {len(npcs)} NPCs")
    print(f"{'='*70}")

    stats = {
        'total_npcs': len(npcs),
        'npcs_with_greetings': 0,
        'npcs_with_topics': 0,
        'npcs_with_barks': 0,
        'npcs_no_dialog': 0,
        'npcs_no_vtck': 0,
        'total_topic_matches': 0,
        'wrong_npc_topics': 0,
    }

    problem_npcs = []

    for npc in npcs:
        if npc.voice_type == 0:
            stats['npcs_no_vtck'] += 1
            continue

        dialog = sim.get_npc_dialog(npc)

        has_greetings = len(dialog['greetings']) > 0
        has_topics = len(dialog['topics']) > 0
        has_barks = any(dialog['barks'].values())

        if has_greetings: stats['npcs_with_greetings'] += 1
        if has_topics: stats['npcs_with_topics'] += 1
        if has_barks: stats['npcs_with_barks'] += 1
        if not has_greetings and not has_topics and not has_barks:
            stats['npcs_no_dialog'] += 1

        stats['total_topic_matches'] += len(dialog['topics'])

        # Check for topics that don't belong to this NPC
        issues = []
        for dial, matching_infos, quest in dialog['topics']:
            # If ALL matching INFOs have GetIsID for OTHER NPCs, this is wrong
            all_other_npc = True
            has_getisid = False
            for info in matching_infos:
                info_targets_other = False
                info_has_getisid = False
                for cond in info.conditions:
                    if cond.func_idx == FUNC_GETISID and cond.run_on == 0:
                        info_has_getisid = True
                        if cond.param1 != npc.form_id:
                            info_targets_other = True
                if info_has_getisid:
                    has_getisid = True
                    if not info_targets_other:
                        all_other_npc = False
                else:
                    all_other_npc = False

            if has_getisid and all_other_npc:
                stats['wrong_npc_topics'] += 1
                issues.append(f'Topic "{dial.full_name}" [{dial.form_id:08X}] (Quest: {quest.editor_id}) '
                             f'has NO INFO with GetIsID(self)')

        if issues:
            problem_npcs.append((npc, issues))

    print(f"\n--- Statistics ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print(f"\n--- NPCs with wrong topics ({len(problem_npcs)}) ---")
    for npc, issues in problem_npcs[:50]:
        print(f"  {npc.editor_id}:")
        for iss in issues[:5]:
            print(f"    {iss}")

    return stats, problem_npcs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_game_state(db, stage_args, completed_args, global_args=(),
                     unlock_all=False, relationship_rank=0):
    """Turn --stage/--completed into the dict ConditionEvaluator reads.

    Quests are named by EditorID (or by hex FormID) so that callers do not have
    to look FormIDs up by hand.
    """
    by_edid = {q.editor_id.lower(): q.form_id for q in db.qusts.values()
               if q.editor_id}

    def resolve(name):
        fid = by_edid.get(name.lower())
        if fid is None:
            try:
                fid = int(name, 16)
            except ValueError:
                sys.exit(f'no quest named {name!r}; pass an EditorID or a '
                         'hex FormID')
            if fid not in db.qusts:
                sys.exit(f'no quest with FormID {fid:08X}')
        return fid

    state = {'relationship_rank': relationship_rank}
    for spec in stage_args:
        if ':' not in spec:
            sys.exit(f'--stage wants QUEST:N, got {spec!r}')
        name, _, num = spec.rpartition(':')
        try:
            stage = int(num)
        except ValueError:
            sys.exit(f'--stage wants an integer stage, got {num!r}')
        fid = resolve(name)
        state[f'stage_{fid}'] = stage
        state[f'quest_running_{fid}'] = True
        print(f'  game state: {name} at stage {stage} [{fid:08X}]')
    for name in completed_args:
        fid = resolve(name)
        state[f'completed_{fid}'] = True
        print(f'  game state: {name} completed [{fid:08X}]')

    if unlock_all:
        n = 0
        for fid, edid in db.globs.items():
            if edid.startswith('TES4Unlock_'):
                state[f'global_{fid}'] = 1.0
                n += 1
        print(f'  game state: {n} TES4Unlock_* globals set to 1')

    for spec in global_args:
        if '=' not in spec:
            sys.exit(f'--global wants NAME=VALUE, got {spec!r}')
        name, _, raw = spec.partition('=')
        try:
            value = float(raw)
        except ValueError:
            sys.exit(f'--global wants a number, got {raw!r}')
        fid = db.glob_by_edid.get(name.lower())
        if fid is None:
            try:
                fid = int(name, 16)
            except ValueError:
                sys.exit(f'no global named {name!r}')
        state[f'global_{fid}'] = value
        print(f'  game state: global {name} = {value:g} [{fid:08X}]')
    return state


def main():
    parser = argparse.ArgumentParser(description='Skyrim Dialog Engine Emulator')
    parser.add_argument('esm', help='ESM/ESP file to analyze')
    parser.add_argument('--npc', help='Walk through dialog for a specific NPC (EditorID)')
    parser.add_argument('--quest', help='Walk through dialog for a specific quest (EditorID)')
    parser.add_argument('--batch', action='store_true', help='Batch test all NPCs')
    parser.add_argument('--detect-collisions', action='store_true', help='Detect topic collisions across NPCs')
    parser.add_argument('--max-npcs', type=int, default=0, help='Limit number of NPCs in batch mode')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed conditions')
    parser.add_argument('--all-conditions', '-a', action='store_true',
                        help='Show ALL INFOs (passing and failing) with full conditions inline')
    parser.add_argument('--stage', action='append', metavar='QUEST:N', default=[],
                        help='Set a quest to stage N, e.g. --stage FGC01Rats:40. '
                             'Repeatable. A quest advanced past 0 counts as '
                             'running, and every stage up to N counts as done.')
    parser.add_argument('--completed', action='append', metavar='QUEST', default=[],
                        help='Mark a quest completed. Repeatable.')
    parser.add_argument('--global', dest='globals', action='append',
                        metavar='NAME=VALUE', default=[],
                        help='Set a global, e.g. --global '
                             'TES4Unlock_ratsTOPIC=1. These are the converter\'s '
                             'AddTopic gates, so this is how you model a topic '
                             'having been unlocked. Repeatable.')
    parser.add_argument('--unlock-all', action='store_true',
                        help='Set every TES4Unlock_* global to 1, i.e. assume '
                             'every AddTopic gate has already fired.')
    parser.add_argument('--relationship-rank', type=int, default=0,
                        help="The NPC's relationship rank toward the player "
                             '(-4..4, default 0 Acquaintance). This is what '
                             "Oblivion's disposition converts into; it pairs "
                             'with the Oblivion emulator\'s --disposition.')
    args = parser.parse_args()

    db = DialogDB(args.esm)
    game_state = build_game_state(db, args.stage, args.completed,
                                  args.globals, args.unlock_all,
                                  args.relationship_rank)

    if args.npc:
        sim = DialogSimulator(db, game_state=game_state)
        sim.walk_npc(args.npc, verbose=args.verbose, all_conditions=args.all_conditions)
    elif args.quest:
        walk_quest(db, args.quest)
    elif args.batch:
        batch_test(db, max_npcs=args.max_npcs, verbose=args.verbose)
    elif args.detect_collisions:
        detect_collisions(db, max_npcs=args.max_npcs)
    else:
        # Default: summary + sample NPCs
        print(f"\n=== DIALOG DB SUMMARY ===")
        print(f"NPCs: {len(db.npcs)}")
        print(f"DIALs: {len(db.dials)}")
        print(f"INFOs: {len(db.infos)}")
        print(f"QUSTs: {len(db.qusts)}")
        print(f"DLBRs: {len(db.dlbrs)}")
        print(f"VTYPs: {len(db.vtyps)}")

        # Show running quests with dialog
        running = [q for q in db.qusts.values() if q.is_start_game_enabled and q.dials]
        print(f"\nRunning quests with dialog: {len(running)}")
        for q in sorted(running, key=lambda q: -len(q.dials))[:20]:
            print(f"  {q.editor_id}: {len(q.dials)} DIALs, flags=0x{q.dnam_flags:04X}")


if __name__ == '__main__':
    main()
