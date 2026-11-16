"""Attach converted TES4 object scripts (SCPT via SCRI) to their records as VMAD.

TES4 object records (ACTI, FLOR, CONT, DOOR, FURN, MISC, KEYM, …) reference a
SCPT record through the ``SCRI`` field.  ``script_convert`` already converts each
such SCPT into a full ``TES4_<EditorID>.psc`` extending ObjectReference/Actor with
its OnActivate / OnLoad / GameMode(→OnUpdate) event handlers, and the pipeline
compiles those to ``.pex``.  What was missing is the binding: without a VMAD
subrecord naming that script on the object record, the engine never attaches it,
so activating an altar showed no message and gave no effect, a nirnroot never
stopped its sound, etc.

This module builds that binding once, up front:

  build_object_script_plan(by_type, xref, fid_to_edid) -> {record_fid_int: VMADinfo}

Each object record's plan carries the script name plus the FormID bindings for its
Object-typed properties (the SCPT's ``ref`` variables and every record the script
names by EditorID), resolved to the OUTPUT plugin's FormID space.  The record
converters then splice the VMAD in right after EDID (Skyrim order: EDID VMAD OBND …).
"""

import re
import struct

from script_convert.assemble import variable_properties
from script_convert.converter import ScriptConverter, sctx_onactivate_consumes
from script_convert.constants import (safe_property_name, papyrus_script_name,
                                      resolve_property_formid,
                                      wants_placed_reference,
                                      PLAYER_ALIAS_EXTENDS)
from script_convert.cross_ref import is_function_script
from script_convert.unequip_guard import SELF_BASE
from script_convert.pipeline import (append_vmad_object_script,
                                     build_vmad_object_script,
                                     build_vmad_quest_fragments)
from .master_export import records_of
from .text_reader import (get_formid_index_offset,
                          remap_formid, unescape_value)
from .writer import (pack_record, pack_string_subrecord, pack_subrecord,
                     pack_uint32_subrecord)
from .constants import ENGINE_GLOBAL_FORMIDS
from .equivalents import (DEFAULT_RACE, RACE_MAP,
                               TES4_ITEM_FORMID_TO_SKYRIM,
                               TES4_RACE_FID_TO_EDID)
from .owned_records import WELL_KNOWN_PROPERTIES
from ..registry import TYPE_MAP

# Papyrus property types that are literal-valued (not bound to a FormID).
_VALUE_TYPES = {'Int', 'Float', 'Bool'}

_PLAYER_FORMID = 0x14
# NPC_ Player.  An ActorBase-typed `Player` property must bind to the BASE, not
# the reference — the VM refuses a reference into an ActorBase property and the
# script's whole init aborts.  Same id in TES4 and TES5.
_PLAYER_BASE_FID = 0x07

# Record types that carry a SCRI in TES4 and become plain object scripts
# in Skyrim.  NPC_/CREA are included: TES4 attaches actor scripts to the BASE
# record, and Skyrim instantiates a base record's VMAD scripts on every placed
# reference — without this, script-typed properties (e.g.
# TES4_FGC01PiranusScript) can never cast and read as None in-game.
# QUST/INFO have their own script pipelines (quest SCRI is attached by
# dialog_converter's QUST VMAD; INFO result scripts become TIF fragments).
SCRIPTABLE_TYPES = {
    'ACTI', 'FLOR', 'CONT', 'DOOR', 'FURN', 'MISC', 'KEYM', 'LIGH',
    'STAT', 'BOOK', 'WEAP', 'ARMO', 'CLOT', 'AMMO', 'INGR', 'ALCH',
    'APPA', 'SLGM', 'SGST', 'SBSP', 'NPC_', 'CREA',
}

# Output (TES5) signatures whose xEdit record definition actually lists a VMAD
# subrecord.  Attaching a VMAD to any other record makes xEdit flag it as an
# "unexpected (or out of order) subrecord" — e.g. ALCH/SLGM/STAT/AMMO have no
# VMAD in the Skyrim def, so a converted object script is dropped for those.
# Sourced from wbDefinitionsTES5.pas (records containing a plain `wbVMAD,`).
VMAD_SUPPORTED_OUTPUT_TYPES = {
    'ACTI', 'APPA', 'ARMO', 'BOOK', 'CONT', 'DOOR', 'EXPL', 'FLOR', 'FURN',
    'INGR', 'KEYM', 'LIGH', 'MGEF', 'MISC', 'NPC_', 'RACE', 'TACT', 'TREE',
    'WEAP',
}

# record FormID (int, output space) -> packed VMAD bytes.  Filled by
# build_object_script_plan(); read by the record converters via get_object_vmad().
_OBJECT_VMAD: dict[int, bytes] = {}

# QUST FormID (int, output space) -> (script_name, {prop: formid}).  Filled by
# build_quest_script_plan(); consumed by dialog_converter.convert_QUST, which
# splices the script into the quest's VMAD alongside the QF fragment script.
_QUEST_SCRIPT: dict[int, tuple] = {}

#: Host QUST FormID (the function script's own, output space) -> (EditorID, script_name, props).
_UDF_HOSTS: dict[int, tuple] = {}

# [(script_name, {prop: formid}), ...] for TES4 scripts attached to the PLAYER
# BASE record (NPC_ 0x00000007).  Filled by build_player_alias_plan(); consumed
# by dialog_converter._make_player_script_quest, which hosts them on a
# start-game-enabled quest's PlayerRef reference alias.  See
# script_convert.constants.PLAYER_ALIAS_EXTENDS for why the base record itself
# cannot carry them.
_PLAYER_ALIAS_SCRIPTS: list[tuple] = []

# TES4 FormID of the player's base NPC_ record.  Oblivion and Skyrim both
# hardcode it; a plugin scripting the player attaches its SCPT here.
_PLAYER_BASE_FORMID = 0x07

# Base-record FormIDs (TES4 space, as written in the export) whose attached
# script calls GetParentRef.  Filled by build_object_script_plan(); read by
# convert_REFR to decide whether a placed reference needs its enable parent
# MIRRORED into XLKR.
#
# TES4 `GetParentRef` returns the reference's ENABLE PARENT (the XESP field --
# xEdit names it 'Enable Parent', and the UESP modding guide says it outright:
# "make the container its Parent Ref" then `set rCont to GetParentRef`).
# Skyrim has no getter for the enable parent, so the converter maps
# GetParentRef -> GetLinkedRef(), which reads XLKR instead.  Nothing was ever
# writing XLKR, so every converted `GetParentRef` returned None: the Vilverin
# pressure plate found no mace to Activate() and the trap stayed frozen in
# mid-air, and the same held for the tripwire.
_GETPARENTREF_BASES: set = set()

# DOOR base FormIDs (raw export hex strings) whose TES4 script CONSUMES
# activation (OnActivate with no unconditional bare `Activate`).  convert_REFR
# keeps a level-100 keyless lock on these as MASTER (100) instead of Requires
# Key (255): Skyrim's pathfinder excludes 255 doors outright (live-process
# probe: Glenroy stood pathless at the 255 CharacterGen back gate, door state
# machine never ticking), while a Master lock routes through and the script's
# OnActivate preamble performs the unlock/open/relock.  The label difference
# is invisible in practice — these doors are activation-blocked, so the
# player can never reach the lockpick UI either way.
_CONSUME_DOOR_BASES: set = set()


def base_uses_parent_ref(base_fid: str) -> bool:
    """True if this base record's TES4 script calls GetParentRef."""
    return base_fid in _GETPARENTREF_BASES


def base_is_consume_door(base_fid: str) -> bool:
    """True if this DOOR base's TES4 script consumes activation."""
    return base_fid in _CONSUME_DOOR_BASES


def get_object_vmad(record_fid: int) -> bytes:
    """Packed VMAD subrecord for a record's attached object script (b'' if none)."""
    return _OBJECT_VMAD.get(record_fid, b'')


# Context captured by build_object_script_plan so a LATER pass can attach a
# script to a record that does not exist yet.  build_leveled_actor_shells mints
# its shell NPC_ records long after the plan is built, and each shell has to
# carry the script its source LVLC named -- see attach_script_to_record().
_PLAN_CTX: dict = {}


def attach_scripts_to_record(record_fid: int, scris) -> int:
    """Bind one or more SCPTs (raw TES4 FormID strings) onto output `record_fid`.

    For records minted after build_object_script_plan() has already run.  Uses
    the same SCPT index and property resolution as the main pass, so each
    script's VMAD entry is byte-identical to the one it would have received
    inline.  Skyrim's VMAD is a script LIST, so several TES4 scripts that ran
    on one Oblivion object attach side by side.  Returns how many attached.
    """
    if not _PLAN_CTX:
        return 0
    packed = b''
    n = 0
    seen = set()
    for scri in scris:
        scri = (scri or '').strip()
        entry = _PLAN_CTX['scpt_by_fid'].get(scri)
        if entry is None or scri in seen:
            continue
        seen.add(scri)
        packed = append_vmad_object_script(
            packed, papyrus_script_name(entry[0] or f'Script_{scri}'),
            _memo_props(scri))
        n += 1

    if n:
        _OBJECT_VMAD[record_fid] = pack_subrecord('VMAD', packed)
    return n


def get_quest_script(record_fid: int):
    """(script_name, props) for a QUST's converted TES4 quest script, or None."""
    return _QUEST_SCRIPT.get(record_fid)


def get_player_alias_scripts() -> list:
    """[(script_name, props)] to host on a quest's PlayerRef alias."""
    return list(_PLAYER_ALIAS_SCRIPTS)


def _remap(fid: int, offset: int) -> int:
    """Remap a TES4 FormID into the output plugin space.

    Mirrors text_reader.get_formid (engine-hardcoded Player 0x14 stays put);
    overrides keep their master's shifted index rather than becoming ours.
    """
    return remap_formid(fid, offset)


def _scro_forms(rec: dict) -> frozenset:
    """Low 24 bits of every form the compiled script references (its SCRO list)."""
    return frozenset(int(v, 16) & 0x00FFFFFF for k, v in rec.items()
                     if k.startswith('SCRO[') and v)


def _collect_scpts(by_type: dict, xref, master_export: dict = None) -> dict:
    """SCPT FormID -> (EditorID, SCTX source, extends class, SCRO forms).

    Masters' scripts are included (first, so an override wins), keyed on their
    `master_export` key, which is in this plugin's FormID space.

    See: docs/commentary/script_convert.md#master-scpt-keying
    """
    scpt_by_fid: dict[str, tuple] = {}
    sources = [records_of(master_export, 'SCPT')]
    sources.append((r.get('FormID', ''), r) for r in by_type.get('SCPT', []))
    for fid, rec in (p for src in sources for p in src):
        sctx = rec.get('SCTX', '')
        if not fid or not sctx or not sctx.strip():
            continue
        scpt_by_fid[fid] = (rec.get('EditorID', ''), sctx,
                            xref.get_extends_class(fid), _scro_forms(rec))
    return scpt_by_fid


def build_quest_script_plan(by_type: dict, xref, fid_to_edid: dict,
                            master_export: dict = None) -> int:
    """Resolve every QUST's attached TES4 quest script (SCRI) to a
    (script_name, bound-properties) plan for convert_QUST to splice into the
    quest VMAD.  Without this the converted TES4_<QuestScript>.pex is never
    attached, so its GameMode logic never runs and every property another
    script declares with that type (e.g. TES4_FGQuestTrack) fails to cast and
    reads None in-game.

    Returns the number of quests with a script plan.
    """
    _QUEST_SCRIPT.clear()
    offset = get_formid_index_offset()
    scpt_by_fid = _collect_scpts(by_type, xref, master_export)

    for rec in by_type.get('QUST', []):
        scri = rec.get('SCRI', '')
        if not scri or scri not in scpt_by_fid:
            continue
        rec_fid_str = rec.get('FormID', '')
        if not rec_fid_str:
            continue
        try:
            rec_fid = _remap(int(rec_fid_str, 16), offset)
        except ValueError:
            continue
        _QUEST_SCRIPT[rec_fid] = _script_plan(scri, scpt_by_fid, xref, offset)

    _UDF_HOSTS.clear()
    for rec in by_type.get('SCPT', []):
        fid = rec.get('FormID', '')
        if fid in scpt_by_fid and is_function_script(rec.get('SCTX', '')):
            _UDF_HOSTS[_remap(int(fid, 16), offset)] = (
                scpt_by_fid[fid][0],
                *_script_plan(fid, scpt_by_fid, xref, offset))

    return len(_QUEST_SCRIPT)


def _script_plan(scpt_fid: str, scpt_by_fid: dict, xref, offset: int) -> tuple:
    """(script_name, bound props) for one indexed SCPT."""
    entry = scpt_by_fid[scpt_fid]
    try:
        props = _resolve_props(entry, xref, offset)
    except Exception:
        props = {}
    return papyrus_script_name(entry[0] or f'Script_{scpt_fid}'), props


def write_udf_host_quests(writer) -> int:
    """One never-started QUST per OBSE function script, at that script's own FormID.

    Every caller's property already names the SCPT's FormID, so the host
    quest makes those bindings resolve. Returns how many were written.

    See: docs/commentary/script_convert.md#udf-host-quest
    """
    for fid, (edid, script_name, props) in sorted(_UDF_HOSTS.items()):
        q = pack_string_subrecord('EDID', edid)
        q += pack_subrecord('VMAD', build_vmad_quest_fragments(
            edid, [], attached_script=(script_name, props)))
        q += pack_subrecord('DNAM', struct.pack('<HBBII', 0, 0, 0, 0, 0))
        q += pack_subrecord('NEXT', b'')
        q += pack_uint32_subrecord('ANAM', 0)
        writer.add_record('QUST', pack_record('QUST', fid, 0, q))
    return len(_UDF_HOSTS)


# TES4 SCPT FormID (raw hex string) -> packed VMAD for a Script-archetype MGEF.
# Filled by build_magic_effect_script_plan(); read by record_types/magic.py
# when it emits the per-script SEFF variants.
_MAGIC_EFFECT_VMAD: dict[str, bytes] = {}


def get_magic_effect_vmad(scpt_fid: str) -> bytes:
    """Packed VMAD for a TES4 magic-effect script (b'' when there is none)."""
    return _MAGIC_EFFECT_VMAD.get(scpt_fid, b'')


def build_magic_effect_script_plan(by_type: dict, xref, fid_to_edid: dict,
                                   master_export: dict = None) -> int:
    """Resolve every magic-effect script (SCHR.Type 256) to a packed VMAD.

    A TES4 `SEFF` effect names its script per EFFECT — `ScriptEffect[i].FormID`
    on the owning SPEL/ENCH/ALCH — not on the MGEF, so the same SEFF record is
    a different script on every item that uses it.  Skyrim moved the script
    onto the MGEF (archetype 1 Script + a VMAD carrying an ActiveMagicEffect),
    so record_types/magic.py emits one MGEF per distinct script and needs the
    VMAD for each here, where the property-resolution machinery lives.

    Returns the number of scripts that produced a VMAD.
    """
    _MAGIC_EFFECT_VMAD.clear()
    offset = get_formid_index_offset()
    scpt_by_fid = _collect_scpts(by_type, xref, master_export)

    # Only the scripts actually referenced by an effect are worth resolving —
    # _resolve_props re-runs the whole converter per script.
    wanted = set()
    for sig in ('SPEL', 'ENCH', 'ALCH', 'INGR', 'SGST'):
        for rec in by_type.get(sig, []):
            for i in range(int(rec.get('EffectCount', 0) or 0)):
                fid = rec.get(f'ScriptEffect[{i}].FormID', '')
                if fid and fid in scpt_by_fid:
                    wanted.add(fid)

    from .writer import pack_subrecord
    for scpt_fid in sorted(wanted):
        entry = scpt_by_fid[scpt_fid]
        script_name = papyrus_script_name(entry[0] or f'Script_{scpt_fid}')
        try:
            props = _resolve_props(entry, xref, offset)
        except Exception:
            props = {}
        _MAGIC_EFFECT_VMAD[scpt_fid] = pack_subrecord(
            'VMAD', build_vmad_object_script(script_name, props))

    return len(_MAGIC_EFFECT_VMAD)


def build_object_script_plan(by_type: dict, xref, fid_to_edid: dict,
                             master_export: dict = None) -> int:
    """Compute and cache the VMAD for every object record with an attached SCPT.

    by_type: {signature: [record dicts]} from the export.
    xref: CrossRefGraph (already populated with edid/formid/record_type +
          script_all_vars/ref_as_int via build_ref_as_int_map).
    fid_to_edid: {raw_formid_int: editor_id} for resolving property targets.

    Returns the number of records that received a script VMAD.
    """
    _OBJECT_VMAD.clear()
    _GETPARENTREF_BASES.clear()
    _CONSUME_DOOR_BASES.clear()
    offset = get_formid_index_offset()
    _PLAN_CTX.clear()
    _PLAN_CTX.update(scpt_by_fid=_collect_scpts(by_type, xref, master_export),
                     props_memo={}, xref=xref, offset=offset)

    count = sum(_plan_record(sig, rec) for sig in _vmad_signatures()
                for rec in by_type.get(sig, []))

    n_player = build_player_alias_plan(by_type, xref, fid_to_edid, master_export)
    if n_player:
        print(f"  Player-base scripts rehosted on a PlayerRef quest alias: "
              f"{n_player}")

    n_moved = _relocate_actor_scripts_to_refs(by_type, offset, master_export)
    if n_moved:
        print(f"  Actor scripts relocated to placed refs (reference events / "
              f"self-ref calls / "
              f"GetVMScriptVariable package gates): {n_moved}")
    return count


def _vmad_signatures() -> list:
    """The SCRIPTABLE_TYPES whose Skyrim output record defines a VMAD."""
    return sorted(sig for sig in SCRIPTABLE_TYPES
                  if TYPE_MAP.get(sig, sig) in VMAD_SUPPORTED_OUTPUT_TYPES)


def _plan_record(sig: str, rec: dict) -> int:
    """Plan one record's VMAD from `_PLAN_CTX`; 1 when it got one.

    The PLAYER base is skipped: no actor instantiates our copy of NPC_ 0x07,
    so build_player_alias_plan rehosts its script on a PlayerRef alias.
    """
    scri = rec.get('SCRI', '')
    entry = _PLAN_CTX['scpt_by_fid'].get(scri)
    fid_str = rec.get('FormID', '')
    if entry is None or not re.fullmatch(r'[0-9A-Fa-f]+', fid_str):
        return 0
    raw_fid = int(fid_str, 16)
    if sig == 'NPC_' and (raw_fid & 0x00FFFFFF) == _PLAYER_BASE_FORMID:
        return 0
    rec_fid = _remap(raw_fid, _PLAN_CTX['offset'])
    _note_base_flags(sig, fid_str, entry[1])
    props = _memo_props(scri)
    if SELF_BASE in props:
        props = dict(props, **{SELF_BASE: rec_fid})
    _OBJECT_VMAD[rec_fid] = pack_subrecord('VMAD', build_vmad_object_script(
        papyrus_script_name(entry[0] or f'Script_{scri}'), props))
    return 1


def _note_base_flags(sig: str, fid_str: str, sctx: str) -> None:
    """Record a base in `_GETPARENTREF_BASES` / `_CONSUME_DOOR_BASES` when its script needs it.

    A gate closer counts as reading its enable parent: Skyrim refuses Disable()
    on a reference with an enable-state parent, so TurnGateOff switches the
    parent off through XLKR (MS48OblivionGateScript never calls GetParentRef).
    """
    if re.search(r'\bgetparentref\b|close(?:current)?obliviongate', sctx,
                 re.IGNORECASE):
        _GETPARENTREF_BASES.add(fid_str)
    if sig == 'DOOR' and sctx_onactivate_consumes(sctx):
        _CONSUME_DOOR_BASES.add(fid_str)


def _memo_props(scri: str) -> dict:
    """Indexed SCPT `scri`'s bound properties, resolved once per script (each is a converter pass)."""
    memo = _PLAN_CTX['props_memo']
    if scri not in memo:
        try:
            memo[scri] = _resolve_props(_PLAN_CTX['scpt_by_fid'][scri],
                                        _PLAN_CTX['xref'], _PLAN_CTX['offset'])
        except Exception:
            memo[scri] = {}
    return memo[scri]


def build_player_alias_plan(by_type: dict, xref, fid_to_edid: dict,
                            master_export: dict = None) -> int:
    """Plan the rehosting of PLAYER-BASE scripts onto a PlayerRef quest alias.

    Oblivion let a plugin script the player by attaching a SCPT to the player's
    base NPC_ record (0x00000007).  Nehrim relies on this completely: its
    GlobalplayerScript holds the whole XP/level/learning-point/gold economy AND
    the `SetStage MQ00 1` that is the ONLY thing that starts the main quest, so
    losing it means the intro never begins and no character ever levels.

    Skyrim cannot honour that attachment.  The acting player is PlayerRef 0x14,
    whose record signature is PLYR — not ACHR, so a plugin cannot author an
    override of it and there is no placed reference to relocate onto (which is
    why _relocate_actor_scripts_to_refs, walking ACHR/ACRE, never sees this
    case).  PlayerRef's base is Skyrim's OWN Player 0x07; the converted
    plugin's copy is shifted into our index (0x01000007) and is a dead record
    nothing instantiates.

    Vanilla's mechanism for "code that runs on the player forever" is a
    start-game-enabled quest holding a reference alias forced to 0x14 — 71
    Skyrim.esm quests do exactly this.  The script rides that alias, so it is
    emitted as `extends ReferenceAlias` (see PLAYER_ALIAS_EXTENDS) and every
    implicit-self call routes through GetReference()/GetActorReference().

    Returns the number of scripts planned.
    """
    _PLAYER_ALIAS_SCRIPTS.clear()
    offset = get_formid_index_offset()
    scpt_by_fid = _collect_scpts(by_type, xref, master_export)

    for rec in by_type.get('NPC_', []):
        try:
            if (int(rec.get('FormID', ''), 16)
                    & 0x00FFFFFF) != _PLAYER_BASE_FORMID:
                continue
        except ValueError:
            continue
        scri = rec.get('SCRI', '')
        if not scri or scri not in scpt_by_fid:
            continue
        entry = scpt_by_fid[scri]
        script_name = papyrus_script_name(entry[0] or f'Script_{scri}')
        try:
            props = _resolve_props(entry, xref, offset, PLAYER_ALIAS_EXTENDS)
        except Exception:
            props = {}
        _PLAYER_ALIAS_SCRIPTS.append((script_name, props))

    return len(_PLAYER_ALIAS_SCRIPTS)


# Reference-only events, per the vanilla Papyrus base classes (Scripts.zip):
# Actor.psc defines OnPackageEnd/OnPackageStart/OnDeath; ObjectReference.psc
# defines OnActivate/OnCellAttach/OnLoad/OnHit.  A base NPC_ record is an
# ActorBase (a Form), NOT an Actor, so a VMAD attached there receives NONE of
# these — they are delivered only to the placed REFERENCE.
#
# TES4 spellings of the same events (the converter maps these onto the Papyrus
# events above); matched against the raw SCTX source.
_TES4_REFERENCE_EVENTS = frozenset({
    'onpackagedone', 'onpackagestart', 'onpackagechange',
    'onactivate', 'ondeath', 'onknockout', 'onhit', 'onalarm', 'onstartcombat',
    'onload', 'onequip', 'onunequip', 'onadd', 'ondrop', 'onsell',
})


_BEGIN_BLOCK_RE = re.compile(r'(?:^|[\r\n;])\s*begin\s+(\w+)', re.IGNORECASE)


# Functions that act on the CALLING REFERENCE when written bare (no `ref.`
# prefix).  On a base ActorBase there is no reference for them to act on, so a
# base-attached script calling these is inert no matter which event drives it.
#
# `enable` is the load-bearing one: Oblivion's standard idiom for a scripted
# entrance is an initially-disabled placement whose OWN GameMode block enables
# it on a cue (`if GetStage MQ00 == 5 / enable`).  Left on the base, the call
# has no target and the actor never appears — that is exactly why Celebro, the
# Nehrim intro companion, was missing from the start cell.
_TES4_SELF_REF_FUNCS = frozenset({
    'enable', 'disable', 'moveto', 'startcombat', 'stopcombat',
    'kill', 'resurrect', 'playgroup', 'setalert', 'evp',
    'addscriptpackage', 'removescriptpackage',
})

# A bare call: start of line (after optional whitespace) and NOT preceded by a
# `.`, which would make it someone else's method (`CelebroRef.Disable`).
_BARE_CALL_RE = re.compile(r'(?:^|\n)[^\S\n]*(\w+)\b', re.MULTILINE)


def _script_uses_self_reference_call(sctx: str) -> bool:
    """True when a TES4 script calls a reference function on ITSELF (bare, no
    ``ref.`` prefix).

    Such a script only functions when attached to a placed reference; on the
    base record the call has no reference to act on.  Comment lines are skipped
    so a commented-out ``;evp`` does not trigger a move.
    """
    text = unescape_value(sctx)
    for m in _BARE_CALL_RE.finditer(text):
        line = text[m.start():text.find('\n', m.start()) if
                    text.find('\n', m.start()) != -1 else len(text)]
        if line.lstrip().startswith(';'):
            continue
        if m.group(1).lower() in _TES4_SELF_REF_FUNCS:
            return True
    return False


def _script_uses_reference_event(sctx: str) -> bool:
    """True when a TES4 script DECLARES an event the engine delivers only to a
    placed reference.

    Must match the ``begin <event>`` declaration, not a bare substring: a
    comment mentioning an event name is not a handler, and relocating on that
    would move scripts that have no reason to leave the base record.
    """
    return any(m.group(1).lower() in _TES4_REFERENCE_EVENTS
               for m in _BEGIN_BLOCK_RE.finditer(sctx))


def _scriptvar_read_refs(by_type: dict) -> set:
    """Refs (raw low-24) whose script variables a PACK condition reads."""
    from ..packages.aliases import scriptvar_refs_from_conditions

    wanted_low = set()
    for rec in by_type.get('PACK', []):
        for ref in scriptvar_refs_from_conditions(rec):
            wanted_low.add(ref & 0x00FFFFFF)
    return wanted_low


def _reference_script_bases(by_type: dict, master_export: dict = None) -> set:
    """Base actors (raw low-24) whose script needs a placed reference.

    Qualifies on a reference-only event handler or a bare self-reference call.
    The MASTERS' scripts are indexed alongside this plugin's, so a dependent
    plugin's actor carrying a master's script still relocates.

    See: docs/commentary/tes5_import_quest.md#actor-script-relocation
    """
    scpt_src = {}
    scpt_src.update({k: r.get('SCTX', '') for k, r in records_of(master_export, 'SCPT')})
    scpt_src.update({r.get('FormID', ''): r.get('SCTX', '')
                     for r in by_type.get('SCPT', [])})
    event_bases = set()
    for sig in ('NPC_', 'CREA'):
        for rec in by_type.get(sig, []):
            src = scpt_src.get(rec.get('SCRI', ''), '')
            if src and (_script_uses_reference_event(src)
                        or _script_uses_self_reference_call(src)):
                try:
                    event_bases.add(int(rec.get('FormID', ''), 16) & 0x00FFFFFF)
                except ValueError:
                    pass
    return event_bases


def _relocate_actor_scripts_to_refs(by_type: dict, offset: int,
                                    master_export: dict = None) -> int:
    """Copy an actor's script VMAD from the base NPC_/CREA onto its placed ACHR.

    Qualifies a placement by EITHER trigger: a package condition reads this
    ref's script variables, or the base's script handles a reference-only event
    / makes a bare self-reference call.  The base keeps its entry, since actors
    spawned from it at runtime (PlaceAtMe, leveled lists) need the script too.
    Returns the number relocated.

    See: docs/commentary/tes5_import_quest.md#actor-script-relocation
    """
    wanted_low = _scriptvar_read_refs(by_type)
    event_bases = _reference_script_bases(by_type, master_export)
    if not wanted_low and not event_bases:
        return 0

    moved = 0
    for sig in ('ACHR', 'ACRE'):
        for rec in by_type.get(sig, []):
            fid_str = rec.get('FormID', '')
            if not fid_str:
                continue
            try:
                ref_raw = int(fid_str, 16)
            except ValueError:
                continue
            base_str = rec.get('NAME', '')
            if not base_str:
                continue
            try:
                base_raw = int(base_str, 16)
            except ValueError:
                continue
            if ((ref_raw & 0x00FFFFFF) not in wanted_low
                    and (base_raw & 0x00FFFFFF) not in event_bases):
                continue
            base_out = _remap(base_raw, offset)
            vmad = _OBJECT_VMAD.get(base_out)
            if not vmad:
                continue
            _OBJECT_VMAD[_remap(ref_raw, offset)] = vmad
            moved += 1
    return moved


def _fixed_property(pname: str, ptype: str):
    """The FormID a property binds to whatever the export says, or None.

    The player, engine globals, and SYNTHESIZED records (TES4Fame,
    TES4GoldFenced, TES4Unlock_*): those exist only in the output, so xref
    misses them and the property read None (every TGStolenGoodsScript rank
    gate threw).
    """
    low = pname.lower()
    if low in ('player', 'playerref'):
        return _PLAYER_BASE_FID if ptype == 'ActorBase' else _PLAYER_FORMID
    if low in ENGINE_GLOBAL_FORMIDS:
        return ENGINE_GLOBAL_FORMIDS[low]
    return WELL_KNOWN_PROPERTIES.get(pname)


def _resolve_props(entry: tuple, xref, offset: int, extends: str = '') -> dict:
    """Bind a `_collect_scpts` entry's Object-typed properties to output FormIDs.

    Candidates are the body's property table plus the declared variables.
    Value-typed ones stay unbound (zero, the TES4 initial state). `extends`
    overrides the entry's.

    See: docs/commentary/script_convert.md#object-script-binder
    """
    edid, sctx, own_extends, scros = entry
    conv = ScriptConverter(xref)
    conv.convert_standalone(safe_property_name(edid or 'Script'), sctx,
                            extends or own_extends, edid)
    candidates = {p.lower(): (p, t) for p, t in conv.get_property_refs().items()}
    candidates.update((p.lower(), (p, t))
                      for p, t in variable_properties(conv, conv._tree))
    obj_props: dict[str, int] = {}
    for low, (pname, ptype) in candidates.items():
        if ptype in _VALUE_TYPES:
            continue
        fid = _fixed_property(pname, ptype)
        if fid is None:
            fid = _bound_formid(xref, pname, ptype, offset,
                                scros if low in conv.sc.local_vars else None)
        if fid is not None:
            obj_props[safe_property_name(pname)] = fid
    if SELF_BASE.lower() in candidates:
        obj_props[SELF_BASE] = 0
    return obj_props


def _bound_formid(xref, pname: str, ptype: str, offset: int, scros) -> 'int | None':
    """Output FormID the record named `pname` binds to, or None to leave it unbound.

    `scros` is given for a script's own variable: it binds only a form the
    compiled script referenced. A base named by a reference-typed property binds
    its one placed ref; engine-hardcoded items and races bind to Skyrim's.

    See: docs/commentary/script_convert.md#local-shadows-form
    See: docs/commentary/script_convert.md#base-name-binds-placed-ref
    See: docs/commentary/script_convert.md#engine-hardcoded-bindings
    """
    fid_hex = resolve_property_formid(xref, pname)
    if not re.fullmatch(r'[0-9A-Fa-f]+', fid_hex or '') or not int(fid_hex, 16):
        return None
    if scros is not None and int(fid_hex, 16) & 0x00FFFFFF not in scros:
        return None
    if wants_placed_reference(ptype) and xref.record_type.get(
            fid_hex, '') in ('NPC_', 'CREA', 'ACTI', 'LIGH'):
        fid_hex = xref.unique_placed_ref(fid_hex) or fid_hex
    raw = int(fid_hex, 16)
    return (TES4_ITEM_FORMID_TO_SKYRIM.get(raw) or _skyrim_race_formid(raw)
            or _remap(raw, offset))


def _skyrim_race_formid(raw: int) -> int:
    """Skyrim RACE a TES4 race reference binds to, or 0 if it is not a race.

    Mirrors record_types.actors._resolve_npc_race: mask the load-order byte,
    resolve the TES4 FormID to its EditorID, then map that onto Skyrim's race.
    Unlike that path there is NO DEFAULT_RACE fallback — an unrecognised id is
    left for the normal remap, so only ids we positively know to be races are
    redirected.
    """
    race_edid = TES4_RACE_FID_TO_EDID.get(raw & 0x00FFFFFF)
    if not race_edid:
        return 0
    return RACE_MAP.get(race_edid, DEFAULT_RACE)
