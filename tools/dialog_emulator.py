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
# Condition function IDs
# ---------------------------------------------------------------------------
FUNC_GETACTORVALUE = 14
FUNC_GETDISEASE = 39
FUNC_GETDETECTED = 45
FUNC_GETTALKEDTOPC = 50
FUNC_GETQUESTRUNNING = 56
FUNC_GETSTAGE = 58
FUNC_GETSTAGEDONE = 59
FUNC_GETCURRENTAIPROCEDURE = 67
FUNC_GETISCLASS = 68
FUNC_GETISRACE = 69
FUNC_GETISSEX = 70
FUNC_GETINCELL = 71
FUNC_GETISID = 72
FUNC_GETINFACTION = 73
FUNC_GETFACTIONRANK = 74
FUNC_GETRANDOMPERC = 77
FUNC_GETISPLAYERBIRTHSIGN = 79
FUNC_GETLEVEL = 80
FUNC_GETDEAD = 84
FUNC_GETQUESTCOMPLETED = 99
FUNC_ISGUARD = 125
FUNC_GETPCISRACE = 130
FUNC_GETTRESPASSWARNINGLEVEL = 144
FUNC_ISTRESPASSING = 145
FUNC_ISINMYOWNEDCELL = 146
FUNC_GETISCURRENTPACKAGE = 161
FUNC_GETISPLAYABLERACE = 254
FUNC_ISSNEAKING = 286
FUNC_GETISVOICETYPE = 426

# Category names
CAT_NAMES = {0: 'Topic', 1: 'Favor', 2: 'Scene', 3: 'Combat',
             4: 'Favors', 5: 'Detection', 6: 'Service', 7: 'Misc'}

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

        print(f"  Loaded: {len(self.npcs)} NPCs, {len(self.dials)} DIALs, "
              f"{len(self.infos)} INFOs, {len(self.qusts)} QUSTs, "
              f"{len(self.dlbrs)} DLBRs, {len(self.vtyps)} VTYPs", file=sys.stderr)

    def _parse_npc(self, rec: TES5Record, is_localized: bool):
        edid_sub = _get(rec, 'EDID')
        edid = _zstring(edid_sub.data) if edid_sub else f'NPC_{rec.form_id:08X}'

        # Voice type
        vtck_sub = _get(rec, 'VTCK')
        vtck = struct.unpack_from('<I', vtck_sub.data, 0)[0] if vtck_sub and len(vtck_sub.data) >= 4 else 0

        # Race
        race_sub = _get(rec, 'RACE')
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

        data_sub = _get(rec, 'DATA')
        category = 0
        subtype = 0
        if data_sub and len(data_sub.data) >= 4:
            category = data_sub.data[1]
            subtype = struct.unpack_from('<H', data_sub.data, 2)[0]

        snam_sub = _get(rec, 'SNAM')
        snam_code = ''
        if snam_sub and len(snam_sub.data) >= 4:
            snam_code = snam_sub.data[:4].decode('ascii', errors='replace')

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

        self.infos[rec.form_id] = INFOData(
            form_id=rec.form_id, editor_id=edid, parent_dial=rec.parent_dial,
            conditions=conditions, responses=responses, tclt_targets=tclt_targets,
            enam_flags=enam_flags, favor_level=favor_level,
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
                return None  # Player race unknown
            elif func == FUNC_GETPCISRACE:
                return None  # Player race unknown
            elif func == FUNC_GETISPLAYERBIRTHSIGN:
                return self._compare(0.0, comp, cv)  # No birthsigns in TES5
            elif func == FUNC_GETRANDOMPERC:
                return None  # Random
            elif func == FUNC_GETINFACTION:
                return self._compare(0.0, comp, cv)  # Player not in NPC factions
            elif func == FUNC_GETFACTIONRANK:
                return self._compare(-1.0, comp, cv)  # Not in faction
            elif func == FUNC_GETDEAD:
                return self._compare(0.0, comp, cv)  # Player alive
            elif func == FUNC_GETLEVEL:
                return self._compare(1.0, comp, cv)  # Player level 1 at start
            elif func == FUNC_GETISSEX:
                return None  # Player sex unknown
            # For quest functions, subject doesn't matter
            elif func in (FUNC_GETSTAGE, FUNC_GETQUESTRUNNING,
                          FUNC_GETQUESTCOMPLETED, FUNC_GETSTAGEDONE):
                subject = self.npc  # dummy, quest funcs don't use subject
            else:
                return None  # Unknown player condition
        elif cond.run_on == 2:  # Reference
            # Look up the reference NPC
            ref_npc = self.db.npcs.get(cond.reference)
            if ref_npc:
                subject = ref_npc
            else:
                return None
        else:
            return None

        if subject is None:
            return None

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
            done = self.game_state.get(f'stagedone_{p1}_{int(cv)}', False)
            actual = 1.0 if done else 0.0
        elif func == FUNC_GETTALKEDTOPC:
            actual = 0.0  # NPC hasn't talked to player yet
        elif func == FUNC_GETDISEASE:
            actual = 0.0  # No disease
        elif func == FUNC_GETISCLASS:
            return None  # Would need class data
        elif func == FUNC_ISGUARD:
            # Check if NPC is a guard — in TES4 this was ACBS bit 20, in TES5 
            # it's faction-based. Check both ACBS flag and guard faction names.
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
            actual = 0.0  # Can't determine
        elif func == FUNC_GETISCURRENTPACKAGE:
            actual = 0.0  # No current package
        elif func == FUNC_GETISPLAYABLERACE:
            actual = 1.0  # Most NPCs are playable races
        elif func == FUNC_ISSNEAKING:
            actual = 0.0  # Not sneaking
        elif func == FUNC_GETINCELL:
            return None  # Can't evaluate location
        elif func == FUNC_GETCURRENTAIPROCEDURE:
            return None  # Can't determine
        elif func == FUNC_GETRANDOMPERC:
            return None  # Random
        else:
            return None  # Unknown function

        return self._compare(actual, comp, cv)

    def _compare(self, actual: float, comp: int, expected: float) -> bool:
        if comp == 0: return actual == expected
        if comp == 1: return actual != expected
        if comp == 2: return actual > expected
        if comp == 3: return actual >= expected
        if comp == 4: return actual < expected
        if comp == 5: return actual <= expected
        return None

    def evaluate_conditions(self, conditions: list[Condition]) -> bool | None:
        """
        Evaluate a list of conditions using Skyrim's AND/OR logic.

        Skyrim conditions use a chain system:
        - Conditions flagged OR are grouped together
        - An OR group passes if ANY condition in it passes
        - All groups are ANDed together

        Returns True (all pass), False (definitely fails), None (unknown)
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

        # Evaluate each group
        has_unknown = False
        for group in groups:
            group_result = self._evaluate_or_group(group)
            if group_result is False:
                return False  # Definitely fails
            elif group_result is None:
                has_unknown = True

        return None if has_unknown else True

    def _evaluate_or_group(self, group: list[Condition]) -> bool | None:
        """Evaluate an OR group. Any True → True, all False → False, else Unknown."""
        has_unknown = False
        for cond in group:
            result = self.evaluate_condition(cond)
            if result is True:
                return True
            elif result is None:
                has_unknown = True
        if has_unknown:
            return None
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
            'topics': [],      # (DIALData, [matching INFOs])
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
                    if cond_result is not False:  # True or Unknown
                        matching_infos.append((info, cond_result))

                if not matching_infos:
                    continue

                # Classify by category
                if dial.snam_code in ('HELO', 'GREE'):
                    for info, certainty in matching_infos:
                        result['greetings'].append((info, dial, certainty))
                elif dial.category in (3, 5, 7):
                    # Bark
                    bark_type = dial.snam_code or CAT_NAMES.get(dial.category, '?')
                    for info, certainty in matching_infos:
                        result['barks'][bark_type].append((info, dial, certainty))
                elif dial.category == 0 and dial.has_branch:
                    # Conversation topic
                    result['topics'].append((dial, matching_infos, quest))

        return result

    def walk_npc(self, npc_edid: str, verbose: bool = True):
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

        dialog = self.get_npc_dialog(npc)

        # Greetings
        print(f"\n--- Greetings ({len(dialog['greetings'])}) ---")
        for info, dial, certainty in dialog['greetings'][:10]:
            cert_str = 'OK' if certainty is True else '?'
            resp = info.responses[0][0][:80] if info.responses else '(no response text)'
            quest = self.db.qusts.get(dial.quest_fid)
            qname = quest.editor_id if quest else '?'
            print(f"  [{cert_str}] [{info.form_id:08X}] (Quest: {qname}) {resp}")
            if verbose:
                self._print_conditions(info.conditions, '      ')

        # Topics
        print(f"\n--- Conversation Topics ({len(dialog['topics'])}) ---")
        for dial, matching_infos, quest in dialog['topics']:
            cert_counts = sum(1 for _, c in matching_infos if c is True)
            unk_counts = sum(1 for _, c in matching_infos if c is None)
            print(f"  [{dial.form_id:08X}] \"{dial.full_name}\" (Quest: {quest.editor_id})"
                  f" {cert_counts}OK {unk_counts}? of {len(matching_infos)} INFOs")
            if verbose:
                for info, certainty in matching_infos[:5]:
                    cert_str = 'OK' if certainty is True else '??'
                    resp = info.responses[0][0][:60] if info.responses else '(no text)'
                    print(f"    [{cert_str}] [{info.form_id:08X}] {resp}")
                    self._print_conditions(info.conditions, '        ')

        # Barks
        print(f"\n--- Barks ---")
        for bark_type, bark_infos in sorted(dialog['barks'].items()):
            print(f"  {bark_type}: {len(bark_infos)} INFOs")
            if verbose:
                for info, dial, certainty in bark_infos[:3]:
                    cert_str = 'OK' if certainty is True else '?'
                    resp = info.responses[0][0][:60] if info.responses else '(no text)'
                    print(f"    [{cert_str}] [{info.form_id:08X}] {resp}")

        return dialog

    def _print_conditions(self, conditions: list[Condition], indent: str = '  '):
        """Print conditions in readable form."""
        FUNC_NAMES = {
            72: 'GetIsID', 426: 'GetIsVoiceType', 58: 'GetStage', 59: 'GetStageDone',
            56: 'GetQuestRunning', 99: 'GetQuestCompleted', 71: 'GetInCell',
            73: 'GetInFaction', 74: 'GetFactionRank', 69: 'GetIsRace',
            70: 'GetIsSex', 84: 'GetDead', 47: 'GetLevel', 50: 'GetActorValue',
            67: 'GetCurrentAIProcedure',
        }
        COMP_NAMES = {0: '==', 1: '!=', 2: '>', 3: '>=', 4: '<', 5: '<='}
        for c in conditions:
            fname = FUNC_NAMES.get(c.func_idx, f'Func{c.func_idx}')
            comp = COMP_NAMES.get(c.comp_type, '?')
            or_str = ' OR' if c.is_or else ''
            run_str = f' [RunOn={c.run_on}]' if c.run_on != 0 else ''

            # Resolve param names where possible
            p1_str = f'{c.param1:08X}'
            if c.func_idx == FUNC_GETISID:
                npc = self.db.npcs.get(c.param1)
                if npc:
                    p1_str = npc.editor_id
            elif c.func_idx == FUNC_GETISVOICETYPE:
                vtyp = self.db.vtyps.get(c.param1)
                if vtyp:
                    p1_str = vtyp
            elif c.func_idx in (FUNC_GETSTAGE, FUNC_GETQUESTRUNNING, FUNC_GETQUESTCOMPLETED, FUNC_GETSTAGEDONE):
                qust = self.db.qusts.get(c.param1)
                if qust:
                    p1_str = qust.editor_id
            elif c.func_idx == FUNC_GETINFACTION or c.func_idx == FUNC_GETFACTIONRANK:
                fact = self.db.facts.get(c.param1)
                if fact:
                    p1_str = fact

            print(f"{indent}{fname}({p1_str}) {comp} {c.comp_value}{or_str}{run_str}")


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
            # Check if any INFO definitely passes
            definite = [info for info, cert in matching_infos if cert is True]
            unknown = [info for info, cert in matching_infos if cert is None]

            if definite:
                topic_npc_map[dial.form_id].append((npc, 'definite'))
            elif unknown:
                topic_npc_map[dial.form_id].append((npc, 'possible'))

            # Check if this topic has GetIsID for a DIFFERENT NPC
            for info, cert in matching_infos:
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
        definite_npcs = [npc for npc, cert in npc_entries if cert == 'definite']
        if len(definite_npcs) > 1:
            collision_count += 1
            if collision_count <= 50:
                quest = db.qusts.get(dial.quest_fid)
                qname = quest.editor_id if quest else '?'
                print(f"  [{dial_fid:08X}] \"{dial.full_name}\" (Quest: {qname})")
                print(f"    Definite: {', '.join(n.editor_id for n in definite_npcs[:10])}")

    print(f"\nTotal topic collisions (same topic, multiple NPCs definite): {collision_count}")

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
            for info, cert in matching_infos:
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

def main():
    parser = argparse.ArgumentParser(description='Skyrim Dialog Engine Emulator')
    parser.add_argument('esm', help='ESM/ESP file to analyze')
    parser.add_argument('--npc', help='Walk through dialog for a specific NPC (EditorID)')
    parser.add_argument('--quest', help='Walk through dialog for a specific quest (EditorID)')
    parser.add_argument('--batch', action='store_true', help='Batch test all NPCs')
    parser.add_argument('--detect-collisions', action='store_true', help='Detect topic collisions across NPCs')
    parser.add_argument('--max-npcs', type=int, default=0, help='Limit number of NPCs in batch mode')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed conditions')
    args = parser.parse_args()

    db = DialogDB(args.esm)

    if args.npc:
        sim = DialogSimulator(db)
        sim.walk_npc(args.npc, verbose=args.verbose)
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
