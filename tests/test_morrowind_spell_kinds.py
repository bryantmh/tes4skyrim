"""The sidecar's SPEL and WEAP rows carry the TES3 types that the export folds away."""

import struct

from tes4_export import tes3_reader as reader
from tes5_import.dialogue.morrowind_sidecar_source import spell_kinds, weapon_kinds


def _spell(record_id: str, kind: int, deleted: bool = False):
    """A SPEL whose SPDT holds `kind`."""
    rec = reader.Tes3Record(type='SPEL', flags=0, record_id=record_id, deleted=deleted)
    rec.subrecords = [reader.Subrecord(type='SPDT', data=struct.pack('<iiI', kind, 0, 0))]
    return rec


def test_spell_kinds_separates_blight_from_disease():
    """Blight (2) and disease (3) stay apart, and ids are lowercased."""
    kinds = spell_kinds([_spell('Ataxia', 3), _spell('Chanthrax Blight', 2)])
    assert kinds == {'ataxia': 3, 'chanthrax blight': 2}


def test_spell_kinds_last_plugin_wins_and_deletion_removes():
    """A later plugin's record overrides, and its deletion drops the spell."""
    kinds = spell_kinds([_spell('Ataxia', 3), _spell('ataxia', 0), _spell('Gone', 3),
                         _spell('Gone', 3, deleted=True)])
    assert kinds == {'ataxia': 0}


def _weapon(record_id: str, kind: int):
    """A WEAP whose WPDT holds weight, value and then `kind`, as OpenMW lays it out."""
    rec = reader.Tes3Record(type='WEAP', flags=0, record_id=record_id, deleted=False)
    rec.subrecords = [reader.Subrecord(type='WPDT', data=struct.pack('<fihH', 24.0, 100, kind, 800))]
    return rec


def test_weapon_kinds_reads_the_wpdt_type():
    """The type is the int16 after weight and value; a spell in the list is ignored."""
    kinds = weapon_kinds([_weapon('BM huntsman axe', 7), _weapon('steel saber_elberoth', 1),
                          _spell('Ataxia', 3)])
    assert kinds == {'bm huntsman axe': 7, 'steel saber_elberoth': 1}
