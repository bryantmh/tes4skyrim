"""Door sounds lifted from a model's `sound:` text keys (tes5_import/record_types/items.py).

See: docs/commentary/asset_convert_audio.md#mesh-door-sounds
"""

import pytest

from asset_convert.audio import door_sounds
from tes5_import.record_types import items

DOOR = {'Signature': 'DOOR', 'FormID': '01000800', 'EditorID': 'FRDoor',
        'Model.MODL': 'Dungeons\\FRDoorUpper.NIF'}


@pytest.fixture
def parsed(monkeypatch):
    """Every door NIF parses to an open sound `DRSOpen`; the table is cleared after."""
    monkeypatch.setattr(door_sounds, 'door_sounds_from_nif', lambda path: {'open': 'DRSOpen'})
    yield
    items._DOOR_MODEL_SOUNDS.clear()


def _nif(root):
    """A door model under `root` in the author's mixed case, carrying a sound key."""
    path = root / 'Dungeons' / 'FRDoorUpper.NIF'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'..sound: DRSOpen..')


def test_a_sound_only_a_master_defines_resolves_to_its_master_key(tmp_path, parsed):
    """The SOUN comes from master_export, keyed in this plugin's index space."""
    _nif(tmp_path)
    master = {'00012345': {'Signature': 'SOUN', 'EditorID': 'DRSOpen'}}
    assert items.load_door_model_sounds([tmp_path], {'DOOR': [DOOR]}, master) == 1
    assert items._door_model_sounds(DOOR) == {'open': 0x00012345}


def test_a_model_only_a_master_ships_is_found(tmp_path, parsed):
    """The own tree lacks the model; the master's mixed-case copy supplies it, own SOUN wins."""
    own, master_tree = tmp_path / 'own', tmp_path / 'master'
    own.mkdir()
    _nif(master_tree)
    by_type = {'DOOR': [DOOR], 'SOUN': [{'FormID': '01000900', 'EditorID': 'drsopen'}]}
    master = {'00012345': {'Signature': 'SOUN', 'EditorID': 'DRSOpen'}}
    assert items.load_door_model_sounds([own, master_tree], by_type, master) == 1
    assert items._door_model_sounds(DOOR) == {'open': 0x01000900}
