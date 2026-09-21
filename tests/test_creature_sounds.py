"""
Creature sound slots per mesh folder, across a master chain.

See: docs/commentary/tes4_export_morrowind.md#sound-gen-creature
"""

from asset_convert.havok.creature_sounds import sound_slots_by_folder
from tes4_export.record_types.morrowind_actors import _emit_sound_slots
from tes4_export.tes3_reader import Tes3Record
from tes4_export.tes4_reader import Subrecord


def _block(**fields) -> str:
    """One export record block."""
    body = ''.join(f'{key}={value}\n' for key, value in fields.items())
    return f'---RECORD_BEGIN---\n{body}---RECORD_END---\n'


def _plugin(root, name: str, masters=(), crea='', soun='') -> str:
    """Write a minimal export folder; its path."""
    folder = root / name
    folder.mkdir()
    header = ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters))
    (folder / '_HEADER.txt').write_text(header, encoding='utf-8')
    (folder / 'CREA.txt').write_text(crea, encoding='utf-8')
    (folder / 'SOUN.txt').write_text(soun, encoding='utf-8')
    return str(folder)


def test_a_creature_inherits_sounds_from_a_masters_creature(tmp_path):
    """The inherit source and its SOUN both live in the master.

    The master's own records carry byte 00; in the child's list the master is
    slot 0 and the child byte 01, so the child names the master's rat 00001000.
    """
    (tmp_path / 'sources.json').write_text('{}', encoding='utf-8')
    _plugin(tmp_path, 'Master.esm',
            crea=_block(Signature='CREA', FormID='00001000',
                        **{'Model.MODL': 'Creatures/Rat/Skeleton.nif',
                           'SoundTypeCount': '1', 'SoundType[0].Type': '5',
                           'SoundType[0].Sound': '00002000'}),
            soun=_block(Signature='SOUN', FormID='00002000',
                        EditorID='RatAware'))
    child = _plugin(
        tmp_path, 'Child.esm', masters=['Master.esm'],
        crea=_block(Signature='CREA', FormID='01003000',
                    **{'Model.MODL': 'Creatures/BigRat/Skeleton.nif',
                       'SoundTypeCount': '0',
                       'CSCR.InheritSound': '00001000'}))
    slots = sound_slots_by_folder(child)
    assert slots['bigrat'] == {5: 'RatAware'}
    assert slots['rat'] == {5: 'RatAware'}, (
        'a folder only the master voices keeps the master\'s slots')


class _Ctx:
    """The little of MorrowindContext `_emit_sound_slots` asks for."""

    def __init__(self, sound_gens):
        """`sound_gens` is what this plugin's own SNDG records supplied."""
        self.sound_gens = sound_gens

    def resolve(self, record_id: str, signature: str = '') -> str:
        """Only the vanilla rat resolves, as a master's record would."""
        return '00001000' if record_id.lower() == 'rat' else ''


def _creature(record_id: str, original: str = '') -> Tes3Record:
    """A CREA naming `original` as its sound-gen creature."""
    subs = [Subrecord(type='CNAM', data=original.encode() + b'\x00')] \
        if original else []
    rec = Tes3Record(type='CREA', flags=0, subrecords=subs)
    rec.record_id = record_id
    return rec


def test_the_sound_gen_creature_is_exported_as_the_inherit_source():
    """OpenMW looks generators up under `CNAM` before the creature's own id."""
    lines = []
    _emit_sound_slots(lines, _creature('T_Mw_Fau_RatCave', 'Rat'), _Ctx({}))
    assert 'CSCR.InheritSound=00001000' in lines
    assert 'SoundTypeCount=0' in lines


def test_generators_this_plugin_owns_are_written_as_slots():
    """The sound-gen creature's own-plugin generators need no indirection."""
    lines = []
    _emit_sound_slots(lines, _creature('rat_diseased', 'Rat'),
                      _Ctx({'rat': {5: '00002000'}}))
    assert 'SoundType[0].Sound=00002000' in lines
    assert not any(line.startswith('CSCR') for line in lines)
