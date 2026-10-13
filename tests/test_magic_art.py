"""Per-phase magic-effect meshes (asset_convert/nif/magic_art.py).

See: docs/commentary/asset_convert_magic_art.md#phase-meshes
"""

import struct

import pytest

from asset_convert.nif import magic_art
from tes5_import.record_types import magic_art as art

MODEL = 'MagicEffects\\Fireball.NIF'


def test_phases_are_read_from_length_prefixed_names_only():
    """A bare `SpecialIdle_` match without its length prefix is not a phase."""
    name = b'SpecialIdle_Cast'
    blob = struct.pack('<I', len(name)) + name + b'..SpecialIdle_HitEffect'
    assert magic_art.effect_phases(blob) == (magic_art.PHASE_CAST,)


def test_phase_mesh_paths_sit_beside_the_model():
    """Phase meshes are `<model stem>_<suffix>.nif`, lowercased."""
    assert magic_art.phase_mesh('MagicEffects\\FireBall.NIF', magic_art.PHASE_HIT) == \
        'magiceffects\\fireball_hit.nif'


def _phase_blob(*phases) -> bytes:
    """NIF bytes naming each phase's `SpecialIdle_` sequence, length-prefixed."""
    out = b''
    for phase in phases:
        name = (magic_art.SEQUENCE_PREFIX + phase).encode('ascii')
        out += struct.pack('<I', len(name)) + name
    return out


def _lowercase_model(root):
    """`magiceffects/fireball.nif` under `root`, authoring Cast and HitEffect; its path."""
    path = root / 'magiceffects' / 'fireball.nif'
    path.parent.mkdir(parents=True)
    path.write_bytes(_phase_blob(magic_art.PHASE_CAST, magic_art.PHASE_HIT))
    return path


class _Writer:
    """A plugin writer that keeps what it is handed."""

    def __init__(self):
        self.records = []

    def derive_formid(self, sig, key):
        """One fixed id: these tests write a single companion."""
        return 0x01000800

    def add_record(self, sig, record):
        """Remember the packed record."""
        self.records.append((sig, record))


@pytest.mark.parametrize('model', [MODEL, 'MagicEffects/Fireball.NIF',
                                   'magiceffects\\fireball.nif'])
def test_import_reads_the_phases_of_a_model_spelled_in_another_case(tmp_path, model):
    """A MODL in the record's case and separators finds the extracted lowercase file."""
    _lowercase_model(tmp_path)
    art.begin(None, tmp_path, [])
    assert art.phases_for(model) == (magic_art.PHASE_CAST, magic_art.PHASE_HIT)


def test_a_model_the_plugin_does_not_ship_authors_no_phase(tmp_path):
    """A name that differs beyond case, or no name at all, is still a miss."""
    _lowercase_model(tmp_path)
    art.begin(None, tmp_path, [])
    assert art.phases_for('MagicEffects\\Frostball.NIF') == ()
    assert art.phases_for('') == ()


@pytest.mark.parametrize('model', [MODEL, 'MagicEffects/Fireball.NIF'])
def test_a_companion_is_named_for_the_models_file_stem(model):
    """The folder never reaches the EditorID, whichever separator the host uses."""
    assert art._edid(model, 'CastArt') == 'TES4FireballCastArt'


def test_casting_art_is_written_for_a_model_spelled_in_another_case(tmp_path):
    """The ARTO names the lowercase cast mesh and takes its EditorID from the stem."""
    _lowercase_model(tmp_path)
    writer = _Writer()
    art.begin(writer, tmp_path, [])
    assert art.casting_art({'Model.MODL': MODEL}) == 0x01000800
    (sig, record), = writer.records
    assert sig == 'ARTO' and b'TES4FireballCastArt\x00' in record
    assert b'\\magiceffects\\fireball_cast.nif\x00' in record


def _splits(monkeypatch):
    """Replace the mesh writer; returns the (converted path, phases) it is asked for."""
    asked = []
    monkeypatch.setattr(magic_art, 'split_effect_mesh',
                        lambda path, phases: asked.append((str(path), phases)) or list(phases))
    return asked


def _export_naming(folder, model=MODEL):
    """An export folder whose only MGEF names `model`."""
    folder.mkdir()
    (folder / 'MGEF.txt').write_text('Model.MODL=%s\n' % model.replace('\\', '\\\\'))
    return folder


def test_phase_meshes_are_split_for_a_model_spelled_in_another_case(tmp_path, monkeypatch):
    """Phases come from the source model; the split is asked of the converted one."""
    src, dst = tmp_path / 'src', tmp_path / 'dst'
    _lowercase_model(src)
    converted = _lowercase_model(dst)
    converted.write_bytes(b'converted')
    asked = _splits(monkeypatch)
    stats = magic_art.split_effect_meshes(_export_naming(tmp_path / 'records'), src, dst)
    assert stats == {'sources': 1, 'written': 2, 'missing': 0}
    assert asked == [(str(converted), (magic_art.PHASE_CAST, magic_art.PHASE_HIT))]


@pytest.mark.parametrize('shipped', ['src', 'dst'])
def test_a_model_missing_on_either_side_is_counted_and_not_split(tmp_path, monkeypatch,
                                                                 shipped):
    """Only a model with both its source and its converted mesh is split."""
    _lowercase_model(tmp_path / shipped)
    asked = _splits(monkeypatch)
    stats = magic_art.split_effect_meshes(
        _export_naming(tmp_path / 'records'), tmp_path / 'src', tmp_path / 'dst')
    assert stats == {'sources': 0, 'written': 0, 'missing': 1} and asked == []


@pytest.fixture
def twins(tmp_path):
    """Skip where the filesystem ignores case: two spellings cannot coexist there."""
    (tmp_path / 'probe').write_bytes(b'')
    if (tmp_path / 'PROBE').exists():
        pytest.skip('the filesystem ignores case')
    return tmp_path


def _exact_model(root, *phases):
    """`MagicEffects/Fireball.NIF` under `root`, in the record's own spelling; its path."""
    path = root / 'MagicEffects' / 'Fireball.NIF'
    path.parent.mkdir(parents=True)
    path.write_bytes(_phase_blob(*phases))
    return path


def test_of_two_spellings_the_import_reads_the_records_own(twins):
    """The exact join answers before the case-blind match."""
    _lowercase_model(twins)
    _exact_model(twins, magic_art.PHASE_AREA)
    art.begin(None, twins, [])
    assert art.phases_for(MODEL) == (magic_art.PHASE_AREA,)


def test_of_two_converted_spellings_the_lowercase_one_is_split(twins, monkeypatch):
    """The converted mesh is the lowercase file even beside one in the record's case."""
    src, dst = twins / 'src', twins / 'dst'
    _lowercase_model(src)
    converted = _lowercase_model(dst)
    _exact_model(dst)
    asked = _splits(monkeypatch)
    magic_art.split_effect_meshes(_export_naming(twins / 'records'), src, dst)
    assert asked == [(str(converted), (magic_art.PHASE_CAST, magic_art.PHASE_HIT))]


def test_of_two_source_spellings_the_records_own_gives_the_phases(twins, monkeypatch):
    """The source model is read in the record's spelling when that file exists."""
    src, dst = twins / 'src', twins / 'dst'
    _lowercase_model(src)
    _exact_model(src, magic_art.PHASE_AREA)
    _lowercase_model(dst)
    asked = _splits(monkeypatch)
    magic_art.split_effect_meshes(_export_naming(twins / 'records'), src, dst)
    assert [phases for _path, phases in asked] == [(magic_art.PHASE_AREA,)]
