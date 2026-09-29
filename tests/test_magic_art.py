"""Per-phase magic-effect meshes (asset_convert/nif/magic_art.py).

See: docs/commentary/asset_convert_magic_art.md#phase-meshes
"""

import struct

from asset_convert.nif import magic_art
from tes5_import.record_types import magic_art as art


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


def _mixed_case_model(root):
    """A lowercase `magiceffects/fireball.nif` under `root` authoring Cast and Hit."""
    (root / 'magiceffects').mkdir(parents=True)
    (root / 'magiceffects' / 'fireball.nif').write_bytes(
        _phase_blob(magic_art.PHASE_CAST, magic_art.PHASE_HIT))


def test_import_reads_phases_through_a_mixed_case_model_path(tmp_path):
    """A MODL `MagicEffects\\Fireball.NIF` finds the lowercase file and names its art by stem."""
    _mixed_case_model(tmp_path)
    art.begin(None, tmp_path, [])
    assert art.phases_for('MagicEffects\\Fireball.NIF') == (
        magic_art.PHASE_CAST, magic_art.PHASE_HIT)
    assert art._edid('MagicEffects\\Fireball.NIF', 'CastArt') == 'TES4FireballCastArt'


def test_phase_meshes_are_split_for_a_mixed_case_model(tmp_path, monkeypatch):
    """The asset pass finds source and converted meshes whatever case the MGEF records."""
    src, dst = tmp_path / 'src', tmp_path / 'dst'
    _mixed_case_model(src)
    _mixed_case_model(dst)
    (tmp_path / 'MGEF.txt').write_text('Model.MODL=MagicEffects\\\\Fireball.NIF\n')
    monkeypatch.setattr(magic_art, 'split_effect_mesh', lambda path, phases: [path] * len(phases))
    stats = magic_art.split_effect_meshes(tmp_path, src, dst)
    assert stats == {'sources': 1, 'written': 2, 'missing': 0}
