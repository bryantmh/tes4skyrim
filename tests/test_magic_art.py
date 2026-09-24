"""Per-phase magic-effect meshes (asset_convert/nif/magic_art.py).

See: docs/commentary/asset_convert_magic_art.md#phase-meshes
"""

import struct

from asset_convert.nif import magic_art


def test_phases_are_read_from_length_prefixed_names_only():
    """A bare `SpecialIdle_` match without its length prefix is not a phase."""
    name = b'SpecialIdle_Cast'
    blob = struct.pack('<I', len(name)) + name + b'..SpecialIdle_HitEffect'
    assert magic_art.effect_phases(blob) == (magic_art.PHASE_CAST,)


def test_phase_mesh_paths_sit_beside_the_model():
    """Phase meshes are `<model stem>_<suffix>.nif`, lowercased."""
    assert magic_art.phase_mesh('MagicEffects\\FireBall.NIF', magic_art.PHASE_HIT) == \
        'magiceffects\\fireball_hit.nif'
