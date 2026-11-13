"""Morrowind vampirism carried by Skyrim's own.

A TES3 clan vampire's blood is a disease its own script gives it, caught from its melee hits;
the player's turn is a script that trades the blood for the Vampirism ability and more gifts.
Each blood becomes Skyrim's vampire disease (Sanguinare Vampiris's effect: its chance, its
three-day timer, PlayerVampireQuest.VampireChange). Each actor whose script gives itself a blood
carries it as the Attack Spell of every humanoid attack, the way vanilla races carry theirs.
The turn's gifts no script tests for are left to Skyrim: MorrowindRuntime skips them and casts
TES3VampireChange for the one carrying Vampirism.
"""

import re
import struct

from ..base.equivalents import SKYRIM_VAMPIRE_RACES
from ..base.master_export import records_of
from ..base.owned_records import adopted_or_derived, owner_row, perk_spell
from ..base.text_reader import get_formid, get_int, get_str
from ..base.writer import pack_formid_subrecord, pack_subrecord

#: Export SPIT.Type of a disease (TES4 Disease; TES3 Disease and Blight).
SPELL_TYPE_DISEASE = 1

#: Skyrim.esm DisDamageHealthVampire, Sanguinare Vampiris's one effect, at Sanguinare's magnitude.
SANGUINARE_EFFECT, SANGUINARE_MAGNITUDE = 0x000B8778, 25.0

#: Skyrim.esm VampireChangeEffect: its script runs PlayerVampireQuest.VampireChange on the player.
VAMPIRE_CHANGE_EFFECT = 0x000C4DDE

#: The spell MorrowindRuntime casts on the player for the turn.
VAMPIRE_CHANGE_SPELL = 'TES3VampireChange'

#: The TES3 effect whose ability marks a vampire (OpenMW's Function_PcVampire reads it).
_VAMPIRISM_EFFECT = 'MW133Vampirism'

#: A spell command in TES3 script text: optional `target->`, the verb, the spell id.
_SPELL_CALL = re.compile(r'(?:(\w+|"[^"\n]*")\s*->\s*)?\b(addspell|removespell|getspell)\b\s*,?\s*'
                         r'(?:"([^"\n]+)"|([^\s,"]+))', re.I)

#: This run's analysis: blood/stand-in spell id -> output FormID, carrier FormID -> blood id, the turn spell.
_bloods: dict = {}
_standins: dict = {}
_carriers: dict = {}
_change: list = []


def _spell_calls(sctx: str) -> list:
    """(target or '', verb, spell id), lowercase, of every spell command outside a comment."""
    calls = []
    for line in sctx.replace('\\r\\n', '\n').replace('\\t', ' ').split('\n'):
        for target, verb, quoted, bare in _SPELL_CALL.findall(line.split(';', 1)[0]):
            calls.append((target.strip('"').lower(), verb.lower(), (quoted or bare).lower()))
    return calls


def _spell_records(by_type: dict, master_export) -> dict:
    """Lowercase spell id -> (export record, owned by this plugin), the masters' first."""
    spells = {get_str(r, 'EditorID').lower(): (r, False) for _key, r in records_of(master_export, 'SPEL')}
    spells.update({get_str(r, 'EditorID').lower(): (r, True) for r in by_type.get('SPEL', [])})
    return spells


def _script_calls(by_type: dict, master_export) -> dict:
    """Script id (a master's re-keyed, as SCRI names it) -> its spell calls."""
    texts = {key: get_str(r, 'SCTX') for key, r in records_of(master_export, 'SCPT')}
    texts.update({get_str(r, 'FormID'): get_str(r, 'SCTX') for r in by_type.get('SCPT', [])})
    return {fid: _spell_calls(text) for fid, text in texts.items() if text}


def _turn_spells(scripts: dict, spells: dict) -> tuple:
    """(bloods, stand-ins): the diseases a Vampirism-giving script names, and what a turn
    script gives the player that no script tests for with GetSpell."""
    vampirism = {sid for sid, (rec, _own) in spells.items()
                 if any(k.endswith('.EFID') and v == _VAMPIRISM_EFFECT for k, v in rec.items())}
    diseases = {sid for sid, (rec, _own) in spells.items() if get_int(rec, 'SPIT.Type') == SPELL_TYPE_DISEASE}
    tested = {sid for calls in scripts.values() for _t, verb, sid in calls if verb == 'getspell'}
    bloods, standins = set(), set()
    for calls in scripts.values():
        given = [(target, sid) for target, verb, sid in calls if verb == 'addspell']
        if not any(sid in vampirism for _t, sid in given):
            continue
        bloods |= {sid for _t, verb, sid in calls if verb != 'getspell'} & diseases
        if any(target == 'player' and sid in vampirism for target, sid in given):
            standins |= {sid for target, sid in given if target == 'player'} - tested - diseases
    return bloods, standins


def _output_fid(sid: str, spells: dict, master_index) -> int:
    """The spell's FormID in the plugin being written: its own record's, else its master's."""
    rec, own = spells[sid]
    if own:
        return get_formid(rec, 'FormID')
    found = master_index.find_by_edid(b'SPEL', get_str(rec, 'EditorID')) if master_index else 0
    return found or get_formid(rec, 'FormID')


def _carriers_of(by_type: dict, scripts: dict) -> dict:
    """This plugin's NPC FormID -> the blood its script gives the NPC itself."""
    found = {}
    for rec in by_type.get('NPC_', []):
        own = [sid for target, verb, sid in scripts.get(get_str(rec, 'SCRI'), ())
               if verb == 'addspell' and not target and sid in _bloods]
        if own:
            found[get_str(rec, 'FormID')] = own[0]
    return found


def create_vampirism(writer, by_type: dict, master_export, master_index, wanted: bool) -> dict:
    """Read the chain's vampirism and write TES3VampireChange; {EditorID: FormID}, {} unless found.

    A master's TES3VampireChange is adopted. Nothing is written for a chain with no turn script.
    """
    for table in (_bloods, _standins, _carriers, _change):
        table.clear()
    if not wanted:
        return {}
    spells = _spell_records(by_type, master_export)
    scripts = _script_calls(by_type, master_export)
    bloods, standins = _turn_spells(scripts, spells)
    _bloods.update({sid: _output_fid(sid, spells, master_index) for sid in bloods})
    _standins.update({sid: _output_fid(sid, spells, master_index) for sid in standins})
    _carriers.update(_carriers_of(by_type, scripts))
    print(f"  Vampirism: {len(_bloods)} bloods, {len(_standins)} stand-ins, {len(_carriers)} carriers")
    if not _standins:
        return {}
    fid, own = adopted_or_derived(writer, master_index, 'SPEL', VAMPIRE_CHANGE_SPELL)
    if own:
        writer.add_record('SPEL', perk_spell(fid, VAMPIRE_CHANGE_SPELL, VAMPIRE_CHANGE_EFFECT))
    _change.append(fid)
    return {VAMPIRE_CHANGE_SPELL: fid}


def vampire_disease_effects(rec: dict) -> bytes:
    """A blood's EFID/EFIT: Sanguinare Vampiris's effect; b'' for any other spell."""
    if get_str(rec, 'EditorID').lower() not in _bloods:
        return b''
    return (pack_formid_subrecord('EFID', SANGUINARE_EFFECT)
            + pack_subrecord('EFIT', struct.pack('<fII', SANGUINARE_MAGNITUDE, 0, 0)))


def carrier_bloods(rec: dict) -> list:
    """[the output FormID of the blood the NPC's script gives itself], else []."""
    blood = _carriers.get(get_str(rec, 'FormID'))
    return [_bloods[blood]] if blood else []


def vampirism_rows(plugin: str, masters: list) -> list:
    """effects_formid.txt rows: the turn spell, each stand-in by its id, each Skyrim vampire race."""
    if not _change:
        return []
    rows = [owner_row('vampire_change', _change[0], plugin, masters)]
    rows += [owner_row(f'vampire_standin:{sid}', fid, plugin, masters) for sid, fid in sorted(_standins.items())]
    rows += [owner_row(f'vampire_race:{race:08X}', race, plugin, masters)
             for race in sorted(SKYRIM_VAMPIRE_RACES.values())]
    return rows
