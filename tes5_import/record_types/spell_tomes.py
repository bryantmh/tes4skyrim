"""Spell merchants sell spell tomes through Skyrim's own barter menu.

Oblivion and Morrowind sell a spell straight into the spellbook from a service
menu Skyrim does not have. Skyrim sells spells as BOOKs that teach one when
read, so each spell a merchant offered in its own game becomes a tome in that
merchant's carried inventory, which the barter menu always lists. A tome's
value is the spell's base price in its own game; the barter menu then applies
Skyrim's haggling to it as it does to every other converted item.
"""

import struct

from tes4_export.morrowind_ids import encode_editor_id

from ..base.text_reader import remap_formid
from ..dialogue.morrowind_sidecar import export_root, is_tes3_export
from .common import (VENDOR_KYWD, get_float, get_formid, get_int, get_str,
                     pack_formid_subrecord, pack_keywords, pack_obnd,
                     pack_record, pack_string_subrecord, pack_subrecord)
from .magic import (AV_ALTERATION, AV_CONJURATION, AV_DESTRUCTION, AV_ILLUSION,
                    AV_NONE, AV_RESTORATION, SCHOOL_TO_AV, mgef_school)
from .spell_tomes_morrowind import morrowind_sales

#: AIDT.Services bit Spells, the same bit in both games.
_SERVICE_SPELLS = 1 << 11

#: SPIT.Type Spell; powers, abilities and diseases are never sold.
_SPELL_TYPE_SPELL = 0

#: TES5 BOOK DATA flag Teaches Spell, as every vanilla tome sets it.
_TEACHES_SPELL = 0x04

#: Every vanilla spell tome weighs 1.0.
_TOME_WEIGHT = 1.0

#: What a tome is called, after vanilla's "Spell Tome: Flames".
_TOME_NAME = 'Spell Tome: '

#: EditorID prefix of a generated tome, per pricing rule; a dependent adopts only its own rule's.
_TOME_EDID = {False: 'TES4SpellTome_', True: 'TES3SpellTome_'}

#: Oblivion.exe constructs fSpellmakingGoldMult from `fld1`; Oblivion.esm sets 3.0.
_GOLD_MULT_DEFAULT = 1.0

#: School actor value -> vanilla's tome model and inventory art for that school.
_TOME_ART = {
    AV_ALTERATION: ('Clutter\\Books\\SpellTomeAlterationLowPoly.nif', 0x0002FBB3),
    AV_ILLUSION: ('Clutter\\Books\\SpellTomeIllusionLowPoly.nif', 0x0002FBB4),
    AV_DESTRUCTION: ('Clutter\\Books\\SpellTomeDestructionLowPoly.nif', 0x0002FBB5),
    AV_CONJURATION: ('Clutter\\Books\\SpellTomeConjurationLowPoly.nif', 0x0002FBB6),
    AV_RESTORATION: ('Clutter\\Books\\SpellTomeRestorationLowPoly.nif', 0x0002FBB7),
}

#: (remapped) merchant FormID -> the tome FormIDs it sells.
_tomes_by_actor: dict = {}


def tome_items(actor_fid: int) -> list:
    """`(tome FormID, 1)` for each spell this merchant sells; [] for anyone else."""
    return [(fid, 1) for fid in _tomes_by_actor.get(actor_fid, ())]


def _indexes(by_type: dict, master_export: dict) -> dict:
    """{sig: {raw FormID: record}} for SPEL, MGEF and GMST, own records last."""
    out = {'SPEL': {}, 'MGEF': {}, 'GMST': {}}
    for key, rec in master_export.items():
        table = out.get(rec.get('Signature'))
        if table is not None:
            table[key.upper()] = rec
    for sig, table in out.items():
        table.update((rec.get('FormID', '').upper(), rec)
                     for rec in by_type.get(sig, []))
    return out


def _gold_mult(gmsts: dict) -> float:
    """fSpellmakingGoldMult as the plugin chain sets it."""
    for rec in gmsts.values():
        if get_str(rec, 'EditorID').lower() == 'fspellmakinggoldmult':
            return get_float(rec, 'DATA.Value')
    return _GOLD_MULT_DEFAULT


def _oblivion_school(spell: dict, effects: dict) -> int:
    """The school actor value of the spell's first effect, as its MGEF converts."""
    effect = effects.get(get_str(spell, 'Effect[0].EFID').lower(), {})
    return mgef_school(effect, get_str(effect, 'EditorID'))


def _oblivion_sales(by_type: dict, spells: dict, mult: float,
                    effects: dict) -> dict:
    """{merchant FormID: [(spell key, price, school)]}: each ordinary spell in its list at cost x mult."""
    sales = {}
    for sig in ('NPC_', 'CREA'):
        for rec in by_type.get(sig, []):
            if not get_int(rec, 'AIDT.Services') & _SERVICE_SPELLS:
                continue
            keys = dict.fromkeys(rec.get(f'Spell[{i}]', '').upper()
                                 for i in range(get_int(rec, 'SpellCount')))
            offered = [(key, int(get_int(spells[key], 'SPIT.Cost') * mult),
                        _oblivion_school(spells[key], effects))
                       for key in keys if key in spells
                       and get_int(spells[key], 'SPIT.Type') == _SPELL_TYPE_SPELL]
            if offered:
                sales[get_formid(rec, 'FormID')] = offered
    return sales


def _tes3_sales(by_type: dict, spells: dict, export_dir: str, plugin: str) -> dict:
    """{merchant FormID: [(spell key, price, school)]} from the TES3 chain's own records.

    A spell a Morroblivion-mode master defines is found by its escaped EditorID.
    The school is the authored MEDT one: a synthesized MGEF carries none.
    """
    actors = {get_str(rec, 'EditorID').lower(): get_formid(rec, 'FormID')
              for sig in ('NPC_', 'CREA') for rec in by_type.get(sig, [])}
    by_edid = {get_str(rec, 'EditorID').lower(): key
               for key, rec in spells.items()}
    sales = {}
    for actor, offered in morrowind_sales(export_root(export_dir), plugin,
                                          set(actors)).items():
        keys = [(by_edid.get(spell) or by_edid.get(encode_editor_id(spell).lower()),
                 price, SCHOOL_TO_AV.get(school, AV_NONE))
                for spell, price, school in offered]
        keys = [entry for entry in keys if entry[0]]
        if keys:
            sales[actors[actor]] = keys
    return sales


def _tome_subs(edid: str, spell: dict, spell_fid: int, price: int,
               school: int) -> bytes:
    """A tome's subrecords, in vanilla's order: EDID OBND FULL MODL KWDA DATA INAM CNAM."""
    model, art = _TOME_ART.get(school, _TOME_ART[AV_ALTERATION])
    name = get_str(spell, 'FULL') or get_str(spell, 'EditorID')
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_obnd()
    subs += pack_string_subrecord('FULL', _TOME_NAME + name)
    subs += pack_string_subrecord('MODL', model)
    subs += pack_keywords([VENDOR_KYWD['Book'], VENDOR_KYWD['SpellTome']])
    subs += pack_subrecord('DATA', struct.pack(
        '<BBHIIf', _TEACHES_SPELL, 0, 0, spell_fid, price, _TOME_WEIGHT))
    subs += pack_formid_subrecord('INAM', art)
    subs += pack_string_subrecord('CNAM', '')
    return subs


def _tome(writer, sale: tuple, spell: dict, prefix: str, master_index) -> int:
    """The tome teaching `spell`: a master's, adopted by EditorID, else a new BOOK."""
    key, price, school = sale
    edid = prefix + get_str(spell, 'EditorID')
    adopted = (master_index.find_by_edid(b'BOOK', edid)
               if master_index is not None else 0)
    if adopted:
        return adopted
    fid = writer.derive_formid('SPELL_TOME', get_str(spell, 'EditorID'))
    subs = _tome_subs(edid, spell, remap_formid(int(key, 16)), price, school)
    writer.add_record('BOOK', pack_record('BOOK', fid, 0, subs))
    return fid


def create_spell_tomes(by_type: dict, writer, ctx, export_dir: str,
                       plugin: str) -> None:
    """Phase 0c: a tome for every spell this plugin's merchants sell.

    Oblivion-format merchants sell the ordinary spells in their own list at
    SPIT.Cost x fSpellmakingGoldMult; TES3 ones follow `morrowind_sales`.
    """
    _tomes_by_actor.clear()
    master_export = getattr(ctx, 'master_export', None) or {}
    indexes = _indexes(by_type, master_export)
    spells = indexes['SPEL']
    tes3 = is_tes3_export(export_dir)
    if tes3:
        sales = _tes3_sales(by_type, spells, export_dir, plugin)
    else:
        effects = {get_str(rec, 'EditorID').lower(): rec
                   for rec in indexes['MGEF'].values()}
        sales = _oblivion_sales(by_type, spells, _gold_mult(indexes['GMST']),
                                effects)
    master_index = getattr(ctx, 'master_index', None)
    tomes = {}
    for actor_fid, offered in sales.items():
        for sale in offered:
            if sale[0] not in tomes:
                tomes[sale[0]] = _tome(writer, sale, spells[sale[0]],
                                       _TOME_EDID[tes3], master_index)
        _tomes_by_actor[actor_fid] = [tomes[sale[0]] for sale in offered]
    if tomes:
        print(f"  Spell tomes: {len(tomes)} for {len(sales)} spell merchants")
