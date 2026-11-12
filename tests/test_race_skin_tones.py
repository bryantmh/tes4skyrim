"""Race skin tones read from the race's own textures (tes5_import/actors/npc_face_mapper.py).

See: docs/commentary/asset_convert_texture.md#case-blind-lookups
"""

import os
import struct

import pytest

from asset_convert.character import facegen_egt
from tes5_import.actors import npc_face_mapper as mapper

BODY = 'Characters\\Imperial\\UpperBodyHumanMale.dds'
RACE = {'EditorID': 'TestImperial', 'MalePart[0].Texture': BODY}
LOWERCASE = ('characters', 'imperial', 'upperbodyhumanmale.dds')


@pytest.fixture
def sampled(monkeypatch):
    """Every texture samples to (10, 20, 30); the paths sampled, on an empty race table."""
    paths = []
    monkeypatch.setattr(facegen_egt, 'sample_texture_rgb',
                        lambda path: paths.append(path) or (10, 20, 30))
    monkeypatch.setattr(mapper, 'RACE_SKIN_RGB', {})
    return paths


def _file(root, *parts):
    """An asset file `root/<parts>`; its path as a string."""
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'asset')
    return str(path)


def _is(found, path):
    """`found` is one string that names the file `path`, in any case."""
    assert len(found) == 1 and isinstance(found[0], str)
    assert os.path.samefile(found[0], path)


def test_a_body_texture_spelled_in_another_case_is_found(tmp_path, sampled):
    """`Characters\\Imperial\\UpperBodyHumanMale.dds` finds the extracted lowercase file."""
    tex = _file(tmp_path, 'textures', *LOWERCASE)
    mapper.load_race_skin_tones({'RACE': [RACE]}, [str(tmp_path)])
    _is(sampled, tex)
    assert mapper.RACE_SKIN_RGB == {'TestImperial': {'Male': (10, 20, 30)}}
    assert mapper._pick_skin_tone('TestImperial', 'Male', 0)[1] == (10, 20, 30)


def test_the_head_egt_is_found_under_meshes(tmp_path, sampled, monkeypatch):
    """A recoloured race's `.egt` sits beside its head mesh, in the mesh tree."""
    _file(tmp_path, 'textures', *LOWERCASE)
    egt = _file(tmp_path, 'meshes', 'characters', 'imperial', 'headhuman.egt')
    read = []
    monkeypatch.setattr(facegen_egt, 'load_egt_mode_means',
                        lambda path: read.append(path) or [(4.0, 8.0, 12.0)])
    race = dict(RACE, FGTS=struct.pack('<50f', 1.0, *[0.0] * 49).hex())
    race['FacePart[0].Model'] = 'Characters\\Imperial\\HeadHuman.NIF'
    mapper.load_race_skin_tones({'RACE': [race]}, [str(tmp_path)])
    _is(read, egt)
    assert mapper.RACE_SKIN_RGB == {'TestImperial': {'Male': (11, 22, 33)}}


def test_the_plugins_own_tree_answers_before_a_masters(tmp_path, sampled):
    """Trees are searched in order, each in any case, as a case-blind filesystem does."""
    own = _file(tmp_path / 'own', 'textures', *LOWERCASE)
    _file(tmp_path / 'master', 'textures', *BODY.split('\\'))
    mapper.load_race_skin_tones(
        {'RACE': [RACE]}, [str(tmp_path / 'own'), str(tmp_path / 'master')])
    _is(sampled, own)


def test_a_masters_tree_is_searched_when_the_plugin_ships_no_texture(tmp_path, sampled):
    """A dependent plugin's races read their master's textures."""
    (tmp_path / 'own' / 'textures').mkdir(parents=True)
    tex = _file(tmp_path / 'master', 'textures', *LOWERCASE)
    mapper.load_race_skin_tones(
        {'RACE': [RACE]}, [str(tmp_path / 'own'), str(tmp_path / 'master')])
    _is(sampled, tex)


def test_an_exactly_spelled_texture_is_the_plain_join(tmp_path, sampled):
    """With the record's spelling on disk, the path sampled is the old join."""
    _file(tmp_path, 'textures', *BODY.split('\\'))
    mapper.load_race_skin_tones({'RACE': [RACE]}, [str(tmp_path)])
    assert sampled == [os.path.join(str(tmp_path), 'textures', *BODY.split('\\'))]


def test_a_texture_that_is_not_shipped_leaves_the_fallback_colour(tmp_path, sampled):
    """A name that differs beyond case is still a miss, and the race keeps its fallback."""
    _file(tmp_path, 'textures', 'characters', 'imperial', 'upperbodyhumanmale2.dds')
    mapper.load_race_skin_tones({'RACE': [RACE]}, [str(tmp_path)])
    assert sampled == [] and mapper.RACE_SKIN_RGB == {}
    assert mapper._pick_skin_tone('TestImperial', 'Male', 0)[1] == \
        mapper._SKIN_FALLBACK_RGB['Imperial']
