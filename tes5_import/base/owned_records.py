"""Records the conversion OWNS: everything with no TES4 source record.

Globals and factions the converted Papyrus needs, the MESG menus a scripted
MessageBox becomes, the force-combat faction pair, the GetDestroyed formlist,
Oblivion's ambient-dialogue GMST pacing, and one VTYP per voiced race.

Split out of import_main.py: measured as its largest zero-coupling cluster —
these call nothing else in that file, and only import_plugin calls them.
"""

import struct

from .constants import AMBIENT_GMST_OVERRIDES
from .equivalents import (CUSTOM_VTYP_EDIDS, SPELL_EQUIP_EITHER_HAND,
                          VTYP_EDID_BY_FID, set_voice_type)
from .text_reader import get_str
from .writer import (
    PluginWriter,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
)

#: EditorID -> FormID for VMAD name binding; a SINGLE shared instance.
WELL_KNOWN_PROPERTIES: dict[str, int] = {}

#: Output MGEF FormID -> the family KYWD it and every copy of it carry.
MGEF_FAMILY_KEYWORDS: dict[int, int] = {}

#: VTYP DNAM Female bit; Allow Default Dialogue (bit 0) stays clear so vanilla lines never reach converted NPCs.
_VTYP_FEMALE = 0x02


#: Conversion-owned globals: EditorID -> FNAM type char ('f' float, 's' short).
_OWNED_GLOBALS = (
    ('TES4Fame', 'f'),
    ('TES4Infamy', 'f'),
    ('TES4GoldFenced', 'f'),
    ('TES4ControlsDisabled', 's'),
)

def _emit_global(writer: PluginWriter, edid: str, type_char: str) -> int:
    """Write one GlobalVariable, register it by name, and return its FormID."""
    fid = writer.derive_formid('GLOB', edid)
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_subrecord('FNAM', struct.pack('<B', ord(type_char)))
    subs += pack_subrecord('FLTV', struct.pack('<f', 0.0))
    writer.add_record('GLOB', pack_record('GLOB', fid, 0, subs))
    WELL_KNOWN_PROPERTIES[edid] = fid
    return fid


def create_tes4_special_records(writer: PluginWriter):
    """Create the globals converted Papyrus scripts need, bound by name.

    See: docs/commentary/tes5_import_dialogue.md#the-conversion-owned-globals
    """
    made = {edid: _emit_global(writer, edid, ch)
            for (edid, ch) in _OWNED_GLOBALS}
    print('  Created TES4 special records: '
          + ', '.join(f'{k}={v:08X}' for k, v in made.items()))


def create_message_menu_records(writer: PluginWriter, plan: dict) -> dict:
    """One MESG per button-MessageBox call site (message_menus.py plan).

    Returns {mesg_edid: formid} for WELL_KNOWN_PROPERTIES.  A site with text
    None is an authored FO3/FNV MESG, converted as a record of its own.

    See: docs/commentary/tes5_import_dialogue.md#synthesized-menus-factions-and-formlists
    """
    name_to_fid = {}
    for edid_low in sorted(plan):
        for name, text, buttons in plan[edid_low]:
            if text is None:
                continue
            fid = writer.derive_formid('SCRIPT_MESG', name)
            subs = pack_string_subrecord('EDID', name)
            subs += pack_string_subrecord('DESC', text)
            subs += pack_subrecord('INAM', struct.pack('<I', 0))
            subs += pack_subrecord('DNAM', struct.pack('<I', 1))
            for button in buttons:
                subs += pack_string_subrecord('ITXT', button)
            writer.add_record('MESG', pack_record('MESG', fid, 0, subs))
            name_to_fid[name] = fid
    return name_to_fid


def create_chargen_menu_records(writer: PluginWriter, plan: dict) -> dict:
    """MESG pages for the TES4 chargen menus (ShowBirthsignMenu/ShowClassMenu).

    Allocates FIXED ids from a reserved window, not derive_formid(): the pages
    are a contiguous, order-significant block.

    See: docs/commentary/tes5_import_dialogue.md#synthesized-menus-factions-and-formlists
    """
    name_to_fid = {}
    k = 0
    for key in sorted(plan):
        for name, title, buttons in plan[key]['pages']:
            fid = writer.chargen_fid_base + k
            k += 1
            subs = pack_string_subrecord('EDID', name)
            subs += pack_string_subrecord('DESC', title)
            subs += pack_subrecord('INAM', struct.pack('<I', 0))
            subs += pack_subrecord('DNAM', struct.pack('<I', 1))
            for button in buttons:
                subs += pack_string_subrecord('ITXT', button)
            writer.add_record('MESG', pack_record('MESG', fid, 0, subs))
            name_to_fid[name] = fid

    assert k <= 0x40, f'chargen menu pages overflow the fixed-id window ({k})'
    for slot, key in ((0x40, 'birthsign'), (0x41, 'class')):
        menu = plan.get(key)
        if not menu:
            continue
        gname = menu['choice_global']
        fid = writer.chargen_fid_base + slot
        subs = pack_string_subrecord('EDID', gname)
        subs += pack_subrecord('FNAM', struct.pack('<B', ord('s')))
        subs += pack_subrecord('FLTV', struct.pack('<f', 0.0))
        writer.add_record('GLOB', pack_record('GLOB', fid, 0, subs))
        name_to_fid[gname] = fid
    return name_to_fid


def create_force_combat_factions(writer: PluginWriter) -> dict:
    """The conversion-owned enemy-faction pair TES4Polyfill.ForceCombat uses.

    ForceCombat puts the attacker in one and the victim in the other; the
    mutual XNAM Enemy reaction is what makes StartCombat stick. Fixed ids.

    See: docs/commentary/tes5_import_dialogue.md#synthesized-menus-factions-and-formlists
    """
    atk_fid = writer.chargen_fid_base + 0x42
    vic_fid = writer.chargen_fid_base + 0x43
    for fid, edid, other in ((atk_fid, 'TES4ForceCombatAttackers', vic_fid),
                             (vic_fid, 'TES4ForceCombatVictims', atk_fid)):
        subs = pack_string_subrecord('EDID', edid)
        subs += pack_subrecord('XNAM', struct.pack('<IiI', other, 0, 1))
        subs += pack_subrecord('DATA', struct.pack('<I', 0x1))
        writer.add_record('FACT', pack_record('FACT', fid, 0, subs))
    return {'TES4ForceCombatAttackers': atk_fid,
            'TES4ForceCombatVictims': vic_fid}


def create_destroyed_formlist(writer: PluginWriter) -> dict:
    """The FormList backing TES4 GetDestroyed, which Skyrim has no reader for.

    See: docs/commentary/tes5_import_dialogue.md#synthesized-menus-factions-and-formlists
    """
    fid = writer.chargen_fid_base + 0x44
    subs = pack_string_subrecord('EDID', 'TES4DestroyedRefs')
    writer.add_record('FLST', pack_record('FLST', fid, 0, subs))
    return {'TES4DestroyedRefs': fid}


#: Seconds one ResetFallDamageTimer protects for; a caller polling every tick keeps renewing it.
_FALL_WINDOW_SECONDS = 10

#: PERK entry point 58 Mod Falling Damage, function 3 Multiply Value, 1 condition tab (vanilla Cushioned).
_FALL_ENTRY_POINT = bytes((58, 3, 1))

#: MGEF DATA flags: No Magnitude | Hide in UI.
_MGEF_FALL_FLAGS = 0x400 | 0x8000

#: MGEF archetype 0 Value Modifier on actor value 24 Health, at magnitude 0 (vanilla NN01PerkEffect).
_MGEF_ARCHETYPE_VALUE_MOD, _AV_HEALTH = 0, 24

#: Casting type 1 Fire and Forget, delivery 0 Self.
_FIRE_AND_FORGET, _DELIVERY_SELF = 1, 0


def _fall_damage_perk(fid: int, edid: str) -> bytes:
    """Hidden, unconditioned PERK multiplying the owner's falling damage by 0."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_string_subrecord('DESC', '')
    subs += pack_subrecord('DATA', bytes((0, 0, 1, 0, 1)))
    subs += pack_subrecord('PRKE', bytes((2, 0, 0)))
    subs += pack_subrecord('DATA', _FALL_ENTRY_POINT)
    subs += pack_subrecord('EPFT', bytes((1,)))
    subs += pack_subrecord('EPFD', struct.pack('<f', 0.0))
    subs += pack_subrecord('PRKF', b'')
    return pack_record('PERK', fid, 0, subs)


def _fall_damage_effect(fid: int, edid: str, perk: int) -> bytes:
    """MGEF whose only job is PerkToApply, the way an NPC receives a perk."""
    data = bytearray(152)
    struct.pack_into('<I', data, 0, _MGEF_FALL_FLAGS)
    struct.pack_into('<ii', data, 12, -1, -1)
    struct.pack_into('<Ii', data, 64, _MGEF_ARCHETYPE_VALUE_MOD, _AV_HEALTH)
    struct.pack_into('<II', data, 80, _FIRE_AND_FORGET, _DELIVERY_SELF)
    struct.pack_into('<i', data, 88, -1)
    struct.pack_into('<f', data, 104, 1.0)
    struct.pack_into('<I', data, 136, perk)
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_subrecord('DATA', bytes(data))
    return pack_record('MGEF', fid, 0, subs)


def _fall_damage_spell(fid: int, edid: str, effect: int) -> bytes:
    """Fire-and-forget self SPEL (type 0) holding the effect for the protection window."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_obnd()
    subs += pack_subrecord('ETYP', struct.pack('<I', SPELL_EQUIP_EITHER_HAND))
    subs += pack_subrecord('SPIT', struct.pack(
        '<IIIfII12x', 0, 0, 0, 0.0, _FIRE_AND_FORGET, _DELIVERY_SELF))
    subs += pack_subrecord('EFID', struct.pack('<I', effect))
    subs += pack_subrecord('EFIT', struct.pack('<fII', 0.0, 0, _FALL_WINDOW_SECONDS))
    return pack_record('SPEL', fid, 0, subs)


def create_fall_damage_spell(writer: PluginWriter, master_index=None) -> dict:
    """`TES4NoFallDamage`, the spell converted ResetFallDamageTimer casts; a master's is adopted.

    Skyrim reaches falling damage only through perk entry point Mod Falling
    Damage, and a magic effect's PerkToApply is how an NPC holds a perk.
    The name is imported here, not at module scope, to break the cycle
    owned_records -> script_convert (package __init__) -> tes5_import.dialogue
    -> base.conditions -> owned_records.
    See: docs/commentary/script_convert.md#fall-damage-is-a-perk
    """
    from script_convert.constants import FALL_DAMAGE_SPELL as name
    spel = (master_index.find_by_edid(b'SPEL', name)
            if master_index is not None else 0)
    if not spel:
        perk = writer.derive_formid('PERK', name + 'Perk')
        mgef = writer.derive_formid('MGEF', name + 'Effect')
        spel = writer.derive_formid('SPEL', name)
        writer.add_record('PERK', _fall_damage_perk(perk, name + 'Perk'))
        writer.add_record('MGEF', _fall_damage_effect(mgef, name + 'Effect', perk))
        writer.add_record('SPEL', _fall_damage_spell(spel, name, mgef))
    return {name: spel}


def create_ambient_gmst_overrides(writer: PluginWriter, by_type: dict):
    """Carry Oblivion's GLOBAL ambient-dialogue pacing across.

    The TES4 export's own value wins where the record exists; otherwise the
    Oblivion.exe engine default in AMBIENT_GMST_OVERRIDES is used.

    See: docs/commentary/tes5_import_dialogue.md#ambient-dialogue-pacing
    """
    authored = {}
    for rec in by_type.get('GMST', []):
        edid = get_str(rec, 'EditorID', '')
        if edid in AMBIENT_GMST_OVERRIDES:
            try:
                authored[edid] = float(get_str(rec, 'DATA.Value'))
            except (TypeError, ValueError):
                pass

    written = []
    for edid, (default, _is_float) in sorted(AMBIENT_GMST_OVERRIDES.items()):
        value = authored.get(edid, default)
        subs = pack_string_subrecord('EDID', edid)
        subs += pack_subrecord('DATA', struct.pack('<f', value))
        writer.add_record('GMST', pack_record(
            'GMST', writer.derive_formid('GMST', edid), 0, subs))
        written.append(f"{edid}={value:g}"
                       + ('' if edid in authored else ' (exe default)'))
    print(f"  Ambient dialogue pacing (GMST): {', '.join(written)}")


#: FO3/FNV voice folder name (lowercased VTYP EditorID) -> VTYP FormID.
FALLOUT_VTYP_BY_EDID: dict = {}


def _emit_authored_vtyps(records: list, emit) -> None:
    """Write a FO3/FNV plugin's own VTYP records under their own EditorIDs.

    Gender comes from the EditorID prefix, which is how FNV names them
    (FemaleAdult01Default); anything else -- robots, creatures -- is male.
    """
    for rec in records:
        edid = (get_str(rec, 'EditorID') or '').strip()
        if not edid:
            continue
        gender = 'Female' if edid.lower().startswith('female') else 'Male'
        FALLOUT_VTYP_BY_EDID[edid.lower()] = emit(edid, gender)
    print(f"  Voice types: {len(FALLOUT_VTYP_BY_EDID)} authored VTYP records "
          f"kept under their own EditorIDs")


def _emit_race_vtyps(export_dir, emit, edid_to_fid: dict) -> None:
    """Write one VTYP per (plugin race display name, gender) and bind them.

    See: docs/commentary/tes5_import_dialogue.md#voice-types-are-created-from-scratch
    """
    try:
        from asset_convert.audio.voice_races import load_race_voices
        from asset_convert.audio.voice_races import vtyp_edid as _vtyp_edid
    except ImportError:
        return
    try:
        races = load_race_voices(export_dir)
    except OSError:
        return
    if not races:
        return
    for key in races.keys:
        for gender in ('Male', 'Female'):
            emit(_vtyp_edid(key, gender), gender)
    for race_edid, key in sorted(races.by_race_edid.items()):
        for gender in ('Male', 'Female'):
            set_voice_type(race_edid, gender,
                           edid_to_fid[_vtyp_edid(key, gender)])
    print(f"  Voice types: {len(edid_to_fid)} VTYP records "
          f"({len(races.keys)} plugin races by display name), "
          f"{len(races.by_race_edid)} race EditorIDs bound")


def create_vtyp_records(writer: PluginWriter, export_dir: str = None,
                        by_type: dict = None):
    """Create custom VTYP records for every race the output plugin can voice.

    Emits the fixed Oblivion set first so its FormIDs never move, then the
    plugin's own races. Updates VOICE_TYPE_MAP so NPC_ converters resolve them.
    A FO3/FNV plugin authors its voice types outright, so those are emitted
    under their own EditorIDs instead of being derived from race.

    See: docs/commentary/tes5_import_dialogue.md#voice-types-are-created-from-scratch
    See: docs/commentary/tes4_export_falloutnv.md#voice-files
    """
    edid_to_fid: dict = {}

    def _emit(vtyp_edid: str, gender: str) -> int:
        """Write one VTYP once, returning its FormID."""
        fid = edid_to_fid.get(vtyp_edid)
        if fid is not None:
            return fid
        fid = writer.derive_formid('VTYP', vtyp_edid)
        dnam = _VTYP_FEMALE if gender == 'Female' else 0
        subs = pack_string_subrecord('EDID', vtyp_edid)
        subs += pack_subrecord('DNAM', struct.pack('<B', dnam))
        writer.add_record('VTYP', pack_record('VTYP', fid, 0, subs))
        edid_to_fid[vtyp_edid] = fid
        VTYP_EDID_BY_FID[fid] = vtyp_edid
        return fid

    for vtyp_edid, (race_edid, gender) in CUSTOM_VTYP_EDIDS.items():
        set_voice_type(race_edid, gender, _emit(vtyp_edid, gender))

    if by_type is not None and by_type.get('VTYP'):
        _emit_authored_vtyps(by_type['VTYP'], _emit)
        return

    if export_dir:
        _emit_race_vtyps(export_dir, _emit, edid_to_fid)
