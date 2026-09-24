"""Records the conversion OWNS: everything with no TES4 source record.

Globals and factions the converted Papyrus needs, the MESG menus a scripted
MessageBox becomes, the force-combat faction pair, the GetDestroyed formlist,
Oblivion's ambient-dialogue GMST pacing, and one VTYP per voiced race.

Split out of import_main.py: measured as its largest zero-coupling cluster —
these call nothing else in that file, and only import_plugin calls them.
"""

import struct

from .constants import AMBIENT_GMST_OVERRIDES
from .equivalents import CUSTOM_VTYP_EDIDS, VTYP_EDID_BY_FID, set_voice_type
from .text_reader import get_str
from .writer import (
    PluginWriter,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
)

#: EditorID -> FormID for VMAD name binding; a SINGLE shared instance.
WELL_KNOWN_PROPERTIES: dict[str, int] = {}


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
        dnam = 3 if gender == 'Female' else 1
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
