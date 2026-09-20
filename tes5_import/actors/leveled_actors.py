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
from ..base.equivalents import resolve_creature_race
from ..base.text_reader import get_formid, get_int, get_str
from ..base.writer import (pack_formid_subrecord, pack_obnd, pack_record,
                     pack_string_subrecord, pack_subrecord)

# ACBS Template Flags: inherit every category (Traits, Stats, Factions, Spell
# List, AI Data, AI Packages, Model, Base Data, Inventory, Script, Def Pack
# List, Attack Data, Keywords).  The shell is a pure indirection, so unlike the
# vanilla shells — which vary because CK authors kept a few local overrides —
# it should own nothing.  40 vanilla shells use exactly this value.
_TEMPLATE_FLAGS = 0x1FFF

# Bit 9 = 'Script' (wbTemplateFlags, wbDefinitionsCommon.pas:7715).  Set, the
# engine inherits the script list from the template.  A shell that carries the
# LVLC's own script must therefore CLEAR it -- see _shell_acbs(own_script).
_USE_SCRIPT = 1 << 9

_CLAS_DEFAULT = 0x00017008      # CLAS EncClassDremoraMelee (vanilla shell class)
_NAM8_SOUND_LEVEL = 1           # Normal

# 20-byte AIDT copied from vanilla LvlDwarvenCenturionAmbush (0010FCE7).
# Inherited via Use AI Data, but the subrecord is Required so it must be here.
_SHELL_AIDT = bytes.fromhex('0004320000000000000000000000000000000000')

# NPC_ DNAM "Player Skills" (wbDefinitionsTES5.pas): 18 skill values + 18 skill
# offsets (36 bytes), then the CACHED derived pools — Health(U16) @36,
# Magicka(U16) @38, Stamina(U16) @40 — then unused(2), far-away model distance
# (float), geared-up weapons(U8), unused(3).  52 bytes total.
#
# This cache must NOT be left at zero.  The engine seeds an actor's actual
# values from the base record's own DNAM when the reference is created; Use
# Stats makes the TEMPLATE supply the ACBS offsets/level, but the placed actor
# still comes up with the cached pool it read here.  A zero Health cache means
# the actor spawns at 0 HP and dies the instant its cell loads — which is why
# every leveled-placed animal (chicken/pig/boar/deer/mudcrab/river crab) keeled
# over on load while the very same base record spawned healthy from the console
# (`placeatme` on the base NPC_ never goes through the shell) and hand-placed
# animals (sheep, pack mule — direct ACHR→NPC_) were fine.
#
# Vanilla census, 508 Skyrim.esm shells whose TPLT is an LVLN: ZERO write
# Health=0; the minimum is 47 and the dominant triple is 55/37/49 (379/508
# Health, 370/508 Magicka and Stamina).  Those are the values used here — the
# template's real pools override them at spawn, so this only has to be a sane
# non-zero seed rather than any particular creature's stats.
_SHELL_HEALTH, _SHELL_MAGICKA, _SHELL_STAMINA = 55, 37, 49


def _shell_dnam() -> bytes:
    """52-byte DNAM for a template shell: zero skills, non-zero pool cache."""
    return (bytes(36)
            + struct.pack('<HHH', _SHELL_HEALTH, _SHELL_MAGICKA, _SHELL_STAMINA)
            + bytes(10))


def _shell_acbs(own_script: bool = False) -> bytes:
    """NPC_ ACBS (24 bytes) for a template shell.

    `own_script` clears the Use Script template bit (bit 9) so a script this
    shell carries in its OWN VMAD survives.  With the bit set the engine takes
    the script list from whatever the LVLN rolls -- which is a generic creature
    with no VMAD -- and silently discards the shell's.

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
                       _TEMPLATE_FLAGS & ~(_USE_SCRIPT if own_script else 0),
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
            from ..base.constants import DEFAULT_RACE, RACE_MAP
            from ..base.equivalents import TES4_RACE_FID_TO_EDID
            edid = TES4_RACE_FID_TO_EDID.get(
                get_formid(npc, 'RNAM.Race') & 0x00FFFFFF, 'Imperial')
            return RACE_MAP.get(edid, DEFAULT_RACE)

        child = lvlc_by_fid.get(fid)
        if child is not None:
            for i in range(get_int(child, 'EntryCount')):
                stack.append(get_formid(child, f'Entry[{i}].FormID'))

    from ..base.constants import DEFAULT_RACE
    return DEFAULT_RACE


def _build_shell(shell_fid: int, lvln_fid: int, race_fid: int,
                 edid: str, own_script: bool = False) -> bytes:
    """Pack one shell NPC_.

    Subrecord order follows the TES5 NPC_ definition (EDID OBND ACBS ... TPLT
    RNAM ... AIDT ... CNAM ... DATA DNAM ... NAM5 NAM6 NAM7 NAM8 ... QNAM).
    Everything after TPLT/RNAM is inherited from the template at spawn time but
    is marked Required in the record definition, so it is written anyway with
    the same neutral values the vanilla shells use.
    """
    subs = pack_string_subrecord('EDID', edid)
    # Skyrim NPC_ order is EDID VMAD OBND ...  _attach_lvlc_script registered
    # the VMAD against this shell's FormID before we were called.
    if own_script:
        from ..base.object_scripts import get_object_vmad
        subs += get_object_vmad(shell_fid)
    subs += pack_obnd(-12, -12, 0, 12, 12, 60)
    subs += pack_subrecord('ACBS', _shell_acbs(own_script))
    subs += pack_formid_subrecord('TPLT', lvln_fid)
    subs += pack_formid_subrecord('RNAM', race_fid)
    subs += pack_subrecord('AIDT', _SHELL_AIDT)
    subs += pack_formid_subrecord('CNAM', _CLAS_DEFAULT)
    subs += pack_subrecord('DATA', b'')
    subs += pack_subrecord('DNAM', _shell_dnam())
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
    n_scripted = 0

    for refr in refrs:
        lvlc = lvlc_by_fid.get(get_formid(refr, 'NAME'))
        if lvlc is None:
            keep_refrs.append(refr)
            continue

        lvln_fid = get_formid(lvlc, 'FormID')
        shell_fid = shell_by_lvlc.get(lvln_fid)
        if shell_fid is None:
            shell_fid = writer.derive_formid('LVLN_SHELL', lvln_fid)
            race = _shell_race(lvlc, crea_by_fid, npc_by_fid, lvlc_by_fid)
            edid = (get_str(lvlc, 'EditorID') or f'LVLN{lvln_fid:08X}') + '_Lvl'
            scripted = _attach_lvlc_script(lvlc, shell_fid,
                                           crea_by_fid, npc_by_fid)
            writer.add_record('NPC_', _build_shell(shell_fid, lvln_fid, race,
                                                   edid, scripted))
            shell_by_lvlc[lvln_fid] = shell_fid
            if scripted:
                n_scripted += 1

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
    if n_scripted:
        print(f"  Leveled-actor shells carrying the list's own script: "
              f"{n_scripted}")
    return len(new_achrs)


def _attach_lvlc_script(lvlc: dict, shell_fid: int, crea_by_fid: dict,
                        npc_by_fid: dict) -> bool:
    """Give the shell NPC_ the scripts its source LVLC ran on spawned creatures.

    TES4 attaches scripts to a leveled creature in two INDEPENDENT places, and
    Skyrim's LVLN has a field for neither:

      * ``SCRI`` on the LVLC RECORD ITSELF -- runs on the placed leveled marker
        (27 lists in Oblivion.esm).  Typically housekeeping, e.g.
        MS48PlazaCreatureFinalLLScript's ``setdestroyed`` on an already-cleared
        plaza.
      * ``TNAM`` -- the template CREA, whose OWN ``SCRI`` runs on the creature
        the list actually spawns (176 lists).  This is where the gameplay logic
        lives: MS48PlazaCreatureFinalScript increments ``MS48.monsterskilled``
        on each death and sets stage 80 at six.

    Both are needed, and they are NOT alternatives -- keying on SCRI alone
    attaches the ``setdestroyed`` marker script and still loses the counter.
    Skyrim's VMAD is a script LIST, so both attach side by side and each keeps
    its own event handlers, which is what Oblivion did.

    Both died in the ACHR -> shell -> LVLN chain: the shell's TPLT points at
    the LVLN, whose entries are ordinary unscripted creatures, so Use Script
    inherited an empty list.  Measured on Oblivion.esm, that silently dropped
    the script on **1063 placed refs across 96 leveled lists** -- MS48's Kvatch
    southern plaza (six creatures that could never advance the quest past
    stage 70), MS49's castle courtyard, MQ12/15/16, MG14/16, SE03/04/10/12 and
    Dark07 among them.

    The caller must also clear the Use Script template bit, or the empty
    inherited list wins.
    """
    scris = [(lvlc.get('SCRI') or '').strip()]

    tnam = get_formid(lvlc, 'TNAM.Template')
    if tnam:
        base = crea_by_fid.get(tnam) or npc_by_fid.get(tnam)
        if base is not None:
            scris.append((base.get('SCRI') or '').strip())

    if not any(scris):
        return False

    from ..base.object_scripts import attach_scripts_to_record
    return attach_scripts_to_record(shell_fid, scris) > 0


def _index_offset() -> int:
    from ..base.text_reader import get_formid_index_offset
    return get_formid_index_offset()


# Raw TES4 FormIDs (this plugin's source space) known to be leveled creatures,
# including the MASTERS'. build_leveled_actor_shells only sees the plugin's own
# LVLC records, so a dependent plugin placing a MASTER's leveled creature is
# invisible to it — see is_leveled_creature_base.
_LEVELED_BASES = set()


def register_leveled_bases(fids) -> None:
    """Record every LVLC FormID reachable from this plugin, masters included."""
    _LEVELED_BASES.update(fids)


def register_from(by_type: dict, master_export: dict = None) -> int:
    """Register every LVLC this plugin can place; return how many.

    Ids stay RAW (this plugin's source space), which is what
    `is_leveled_creature_base` compares a REFR's unremapped NAME against.
    """
    fids = {int(r['FormID'], 16) for r in by_type.get('LVLC', [])
            if r.get('FormID')}
    fids |= {int(k, 16) for k, r in (master_export or {}).items()
             if r.get('Signature') == 'LVLC'}
    register_leveled_bases(fids)
    return len(fids)


def is_leveled_creature_base(refr: dict) -> bool:
    """True when this REFR places a leveled creature (its NAME is an LVLC).

    Needed by the OVERRIDE path. A dependent plugin's REFR can place one of its
    MASTER's LVLCs, in which case this plugin never sees the LVLC record and
    cannot mint (or find) the shell NPC_ the master's run created — so its
    standalone conversion resolves NAME to the raw LVLN, and an ACHR whose base
    is not an actor crashes the engine on load.
    """
    if not _LEVELED_BASES:
        return False
    raw = (refr.get('NAME') or '').upper()
    try:
        return int(raw, 16) in _LEVELED_BASES
    except (TypeError, ValueError):
        return False
