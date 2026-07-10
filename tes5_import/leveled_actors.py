"""Placed leveled creatures: TES4 `REFR → LVLC` becomes TES5 `ACHR → NPC_ → LVLN`.

Oblivion spawns a leveled creature by placing the leveled list itself: a plain
REFR whose NAME is an LVLC (8005 of them in Oblivion.esm).  Skyrim has no such
thing.  Its engine only instantiates actors from ACHR records, and an ACHR's
NAME must be a concrete NPC_ (`wbFormIDCk(NAME, 'Base', [NPC_])` in
wbDefinitionsTES5.pas).  A REFR pointing at an LVLN is inert — the record loads,
nothing ever spawns.  That is why converted hand-placed actors show up in-game
while every leveled creature is missing.

Vanilla Skyrim's equivalent is a three-record chain, verified against a
Skyrim.esm dump (LCharDwarvenCenturion 0010FCE5 → LvlDwarvenCenturion 0010FCE6):

    ACHR ──NAME──▶ NPC_ "shell" ──TPLT──▶ LVLN

The shell NPC_ carries no real data of its own; its ACBS Template Flags tell the
engine to pull every field from whichever actor the LVLN rolls at spawn time.
508 of Skyrim's 527 LVLNs have exactly one such shell; 10504/10504 of its ACHRs
point at an NPC_ and zero point at an LVLN.

This module runs before the CELL/WRLD group builders and does two things:

  1. mints one shell NPC_ per LVLC that is actually placed by a REFR, and
  2. rewrites those REFR record dicts into ACHR records aimed at the shell,
     moving them from ``by_type['REFR']`` to ``by_type['ACHR']``.

The rewritten dicts keep every field convert_ACHR reads (EditorID, XESP, XSCL,
Pos/Rot, ParentCELL/ParentWRLD, RecordFlags), so no other pass needs to change.
"""

import struct

from .creature_races import get_creature_race
from .skyrim_overrides import resolve_creature_race
from .text_reader import get_formid, get_int, get_str
from .writer import (pack_formid_subrecord, pack_obnd, pack_record,
                     pack_string_subrecord, pack_subrecord)

# ACBS Template Flags: inherit every category (Traits, Stats, Factions, Spell
# List, AI Data, AI Packages, Model, Base Data, Inventory, Script, Def Pack
# List, Attack Data, Keywords).  The shell is a pure indirection, so unlike the
# vanilla shells — which vary because CK authors kept a few local overrides —
# it should own nothing.  40 vanilla shells use exactly this value.
_TEMPLATE_FLAGS = 0x1FFF

_CLAS_DEFAULT = 0x00017008      # CLAS EncClassDremoraMelee (vanilla shell class)
_NAM8_SOUND_LEVEL = 1           # Normal

# 20-byte AIDT copied from vanilla LvlDwarvenCenturionAmbush (0010FCE7).
# Inherited via Use AI Data, but the subrecord is Required so it must be here.
_SHELL_AIDT = bytes.fromhex('0004320000000000000000000000000000000000')


def _shell_acbs() -> bytes:
    """NPC_ ACBS (24 bytes) for a template shell.

    Layout (wbDefinitionsTES5.pas): Flags(U32) MagickaOffset(S16)
    StaminaOffset(S16) Level(S16) CalcMin(U16) CalcMax(U16) SpeedMult(U16)
    Disposition(S16) TemplateFlags(U16) HealthOffset(S16) BleedoutOverride(U16).

    Every stat field is inherited via Use Stats, so they are left at the
    vanilla shell's values (level 1, speed 100).
    """
    return struct.pack('<IhhhHHHhHhH',
                       0,                  # Flags
                       0, 0,               # Magicka / Stamina offset
                       1,                  # Level
                       0, 0,               # Calc min / max
                       100,                # Speed multiplier
                       0,                  # Disposition (unused)
                       _TEMPLATE_FLAGS,
                       0,                  # Health offset
                       0)                  # Bleedout override


def _shell_race(lvlc_rec: dict, crea_by_fid: dict, npc_by_fid: dict,
                lvlc_by_fid: dict) -> int:
    """Race for the shell NPC_.

    RNAM is `Required=True` on NPC_ even when Use Traits makes the engine
    ignore it, so it must be a real RACE.  Walk the leveled list (depth-first,
    cycle-guarded) to the first concrete actor and borrow its race; that keeps
    the shell's record self-consistent if a tool ever reads it without
    resolving the template.
    """
    seen = set()
    stack = [get_formid(lvlc_rec, 'FormID')]
    while stack:
        fid = stack.pop(0)
        if fid in seen:
            continue
        seen.add(fid)

        crea = crea_by_fid.get(fid)
        if crea is not None:
            race = get_creature_race(get_formid(crea, 'FormID') & 0x00FFFFFF)
            if race is None:
                race, _src, _alt = resolve_creature_race(
                    get_str(crea, 'EditorID'), get_str(crea, 'FULL'))
            return race

        npc = npc_by_fid.get(fid)
        if npc is not None:
            from .constants import DEFAULT_RACE, RACE_MAP
            from .skyrim_overrides import TES4_RACE_FID_TO_EDID
            edid = TES4_RACE_FID_TO_EDID.get(
                get_formid(npc, 'RNAM.Race') & 0x00FFFFFF, 'Imperial')
            return RACE_MAP.get(edid, DEFAULT_RACE)

        child = lvlc_by_fid.get(fid)
        if child is not None:
            for i in range(get_int(child, 'EntryCount')):
                stack.append(get_formid(child, f'Entry[{i}].FormID'))

    from .constants import DEFAULT_RACE
    return DEFAULT_RACE


def _build_shell(shell_fid: int, lvln_fid: int, race_fid: int,
                 edid: str) -> bytes:
    """Pack one shell NPC_.

    Subrecord order follows the TES5 NPC_ definition (EDID OBND ACBS ... TPLT
    RNAM ... AIDT ... CNAM ... DATA DNAM ... NAM5 NAM6 NAM7 NAM8 ... QNAM).
    Everything after TPLT/RNAM is inherited from the template at spawn time but
    is marked Required in the record definition, so it is written anyway with
    the same neutral values the vanilla shells use.
    """
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_obnd(-12, -12, 0, 12, 12, 60)
    subs += pack_subrecord('ACBS', _shell_acbs())
    subs += pack_formid_subrecord('TPLT', lvln_fid)
    subs += pack_formid_subrecord('RNAM', race_fid)
    subs += pack_subrecord('AIDT', _SHELL_AIDT)
    subs += pack_formid_subrecord('CNAM', _CLAS_DEFAULT)
    subs += pack_subrecord('DATA', b'')
    subs += pack_subrecord('DNAM', bytes(52))
    subs += pack_subrecord('NAM5', struct.pack('<H', 0xFF))
    subs += pack_subrecord('NAM6', struct.pack('<f', 1.0))
    subs += pack_subrecord('NAM7', struct.pack('<f', 1.0))
    subs += pack_subrecord('NAM8', struct.pack('<I', _NAM8_SOUND_LEVEL))
    subs += pack_subrecord('QNAM', struct.pack('<fff', 0.0, 0.0, 0.0))
    return pack_record('NPC_', shell_fid, 0, subs)


def build_leveled_actor_shells(by_type: dict, writer) -> int:
    """Retarget placed leveled creatures onto shell NPC_ records.

    Mutates ``by_type``: REFRs whose NAME is an LVLC move to ``by_type['ACHR']``
    with NAME rewritten to a freshly minted shell.  Returns the number of REFRs
    converted.
    """
    lvlcs = by_type.get('LVLC', [])
    refrs = by_type.get('REFR', [])
    if not lvlcs or not refrs:
        return 0

    lvlc_by_fid = {get_formid(r, 'FormID'): r for r in lvlcs}
    crea_by_fid = {get_formid(r, 'FormID'): r for r in by_type.get('CREA', [])}
    npc_by_fid = {get_formid(r, 'FormID'): r for r in by_type.get('NPC_', [])}

    offset = _index_offset()
    shell_by_lvlc = {}
    keep_refrs = []
    new_achrs = []

    for refr in refrs:
        lvlc = lvlc_by_fid.get(get_formid(refr, 'NAME'))
        if lvlc is None:
            keep_refrs.append(refr)
            continue

        lvln_fid = get_formid(lvlc, 'FormID')
        shell_fid = shell_by_lvlc.get(lvln_fid)
        if shell_fid is None:
            shell_fid = writer.alloc_formid()
            race = _shell_race(lvlc, crea_by_fid, npc_by_fid, lvlc_by_fid)
            edid = (get_str(lvlc, 'EditorID') or f'LVLN{lvln_fid:08X}') + '_Lvl'
            writer.add_record('NPC_', _build_shell(shell_fid, lvln_fid, race, edid))
            shell_by_lvlc[lvln_fid] = shell_fid

        # convert_ACHR reads NAME through get_formid(), which re-applies the
        # load-order index offset, so store the pre-offset form here.
        high = ((shell_fid >> 24) - offset) & 0xFF
        refr['NAME'] = f'{high:02X}{shell_fid & 0x00FFFFFF:06X}'
        refr['Signature'] = 'ACHR'
        new_achrs.append(refr)

    if not new_achrs:
        return 0

    by_type['REFR'] = keep_refrs
    by_type.setdefault('ACHR', []).extend(new_achrs)
    return len(new_achrs)


def _index_offset() -> int:
    from .text_reader import get_formid_index_offset
    return get_formid_index_offset()
