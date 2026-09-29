"""Race skin tones read from the authored body texture (tes5_import/actors/npc_face_mapper.py).

See: docs/commentary/asset_convert_facegen.md#finding-race-textures
"""

import pytest

from asset_convert.character import facegen_egt
from tes5_import.actors import npc_face_mapper as mapper

RACE = {'EditorID': 'TestImperial',
        'MalePart[0].Texture': 'Characters\\Imperial\\UpperBodyHumanMale.dds'}


@pytest.fixture
def sampled(monkeypatch):
    """Any texture found samples to (10, 20, 30); the race table is cleared after."""
    monkeypatch.setattr(facegen_egt, 'sample_texture_rgb', lambda path: (10, 20, 30))
    yield
    mapper.RACE_SKIN_RGB.pop('TestImperial', None)


def test_mixed_case_texture_in_a_nested_mods_asset_tree_is_found(tmp_path, sampled):
    """Records sit in `<Mod>/<plugin>/`, textures lowercase in `<Mod>/textures/`."""
    (tmp_path / 'sources.json').write_text('{}')
    rec_dir = tmp_path / 'Mod' / 'Mod.esp'
    rec_dir.mkdir(parents=True)
    tex = tmp_path / 'Mod' / 'textures' / 'characters' / 'imperial' / 'upperbodyhumanmale.dds'
    tex.parent.mkdir(parents=True)
    tex.write_bytes(b'dds')
    mapper.load_race_skin_tones({'RACE': [RACE]}, [str(rec_dir)])
    assert mapper.RACE_SKIN_RGB['TestImperial'] == {'Male': (10, 20, 30)}
