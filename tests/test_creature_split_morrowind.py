"""Morrowind creature split: bipedal layering, multi-action groups, source folders.

See: docs/commentary/tes4_export_morrowind.md#bipedal-creatures
"""

import os
import numpy as np
import pytest

from asset_convert.havok import creature_split_morrowind as split
from asset_convert.havok.creature_pipeline import _creature_folders


def _write_crea(export_dir, records):
    """Write CREA.txt holding `records` (dicts of KEY=VALUE lines)."""
    lines = []
    for rec in records:
        lines.append('---RECORD_BEGIN---')
        lines.extend(f'{k}={v}' for k, v in rec.items())
        lines.append('---RECORD_END---')
    (export_dir / 'CREA.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


SKELETON = {'Signature': 'CREA', 'EditorID': 'skeleton', 'ACBS.Flags': '5',
            'Model.MODL': 'r\\\\skeleton\\\\skeleton.nif', 'NIFZCount': '1',
            'NIFZ[0]': 'skeleton_body.nif', 'MorrowindModel': 'r\\\\Skeleton.NIF'}


def test_weapon_group_is_cut_into_equip_and_attack_clips():
    """A weapon group yields stance-prefixed equip, unequip and attack clips, the attack hitting at `<a> hit`."""
    events = {'equip start': 1.0, 'equip stop': 1.5, 'chop start': 2.0,
              'chop hit': 2.8, 'chop large follow stop': 3.2,
              'unequip start': 4.0, 'unequip stop': 4.4}
    plan = split._plan({'weapononehand': events}, known_only=True)
    assert sorted(plan) == ['onehandattackchop', 'onehandequip', 'onehandunequip']
    span, chop = plan['onehandattackchop']
    assert span == (2.0, 3.2, False)
    assert chop['hit'] == 2.8


def test_spellcast_segments_hit_at_release():
    """Each spellcast delivery becomes a cast clip hitting at its release."""
    events = {'target start': 5.0, 'target release': 5.9, 'target stop': 7.0,
              'self start': 1.0, 'self release': 1.5, 'self stop': 2.0}
    plan = split._plan({'spellcast': events}, known_only=False)
    assert plan['casttarget'][1]['hit'] == 5.9
    assert plan['castself'][0] == (1.0, 2.0, False)


def test_known_only_drops_groups_no_claim_table_reads():
    """The base layer keeps only mapped groups; a creature's own file keeps all."""
    plain = {'start': 0.0, 'stop': 1.0}
    plan = split._plan({'idle': plain, 'sneakforward': plain, 'idle1h': plain},
                       known_only=True)
    assert sorted(plan) == ['idle', 'onehandidle']
    assert 'sneakforward' in split._plan({'sneakforward': plain}, known_only=False)


def test_sources_carry_body_name_and_bipedal_flag(tmp_path):
    """A source is bipedal when any record using it is, and keeps its NIFZ body name."""
    _write_crea(tmp_path, [SKELETON, dict(SKELETON, **{'ACBS.Flags': '4'})])
    assert split._sources(tmp_path) == {
        'r\\Skeleton.NIF': ('r/skeleton', 'skeleton_body.nif', True)}
    assert split.source_dirs(tmp_path) == {'r'}


def test_source_folder_is_not_a_creature(tmp_path):
    """A folder holding a source model named skeleton.nif is not claimed as a creature."""
    _write_crea(tmp_path, [SKELETON])
    meshes = tmp_path / 'meshes'
    for folder, files in (('r', ('skeleton.nif', 'xskeleton.kf')),
                          (os.path.join('r', 'skeleton'), ('skeleton.nif', 'idle.kf'))):
        (meshes / folder).mkdir(parents=True, exist_ok=True)
        for name in files:
            (meshes / folder / name).write_bytes(b'')
    found = _creature_folders(str(tmp_path), str(meshes), None, lambda _m: None)
    assert [name for _dir, name, _ref in found] == ['skeleton']


def _animated_model(path, keys):
    """A TES3 rig whose bone keeps moving after a possible held Idle."""
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data(version=0x04000002)
    root, bone = NifFormat.NiNode(), NifFormat.NiNode()
    root.name, bone.name = b'Bip01', b'Bone'
    bone.translation.y = 4.0
    root.add_child(bone)
    controller = NifFormat.NiKeyframeController()
    controller.target = bone
    controller.start_time, controller.stop_time = 0.0, 1.0
    controller.data = NifFormat.NiKeyframeData()
    channel = controller.data.translations
    channel.interpolation = 1
    channel.num_keys = 2
    channel.keys.update_size()
    channel.keys[0].time, channel.keys[1].time = 0.0, 1.0
    channel.keys[1].value.x = 30.0
    bone.add_controller(controller)
    text = NifFormat.NiTextKeyExtraData()
    text.num_text_keys = len(keys)
    text.text_keys.update_size()
    for row, (time, value) in zip(text.text_keys, keys):
        row.time, row.value = time, value.encode('ascii')
    root.add_extra_data(text)
    data.roots = [root]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        data.write(stream)


def _split_idle(tmp_path, keys):
    from asset_convert.havok.behavior_clips import classify_clips
    from asset_convert.havok.kf_decode import decode_kf
    model = tmp_path / 'source.nif'
    _animated_model(model, keys)
    folder = tmp_path / 'creature'
    split.split_creature(model, str(folder), 'body.nif')
    idle = classify_clips(str(folder))['idle']
    assert idle is not None
    return decode_kf(idle)[0], folder


def test_held_idle_samples_only_its_authored_instant(tmp_path):
    idle, _ = _split_idle(tmp_path, [(0.5, 'Idle: Start'), (0.5, 'Idle: Stop'),
                                   (0.6, 'Idle2: Start'), (1.0, 'Idle2: Stop')])
    assert idle.duration > 0 and idle.cycle_type == split.CYCLE_LOOP
    track = next(t for t in idle.tracks if t.bone == 'Bone')
    np.testing.assert_allclose(track.translations, [[15.0, 0.0, 0.0]] * 2)


def test_numbered_idle_uses_its_authored_loop_and_keeps_original(tmp_path):
    idle, folder = _split_idle(tmp_path, [(0.0, 'Idle2: Start'),
        (0.2, 'Idle2: Loop Start'), (0.8, 'Idle2: Loop Stop'), (1.0, 'Idle2: Stop')])
    assert idle.duration == pytest.approx(0.6)
    assert idle.cycle_type == split.CYCLE_LOOP and (folder / 'idle2.kf').is_file()
    track = next(t for t in idle.tracks if t.bone == 'Bone')
    np.testing.assert_allclose(track.translations[[0, -1], 0], [6.0, 24.0], atol=1e-5)


def test_numbered_one_shot_gets_reference_pose_not_a_looping_gesture(tmp_path):
    from asset_convert.havok.hkx_anim import clip_to_animation_data
    idle, folder = _split_idle(tmp_path, [(0.0, 'Idle2: Start'), (1.0, 'Idle2: Stop')])
    assert idle.tracks == [] and (folder / 'idle2.kf').is_file()
    pose = {'Bone': ((2.0, 4.0, 6.0), (1.0, 0.0, 0.0, 0.0), 1.0)}
    animation = clip_to_animation_data(idle, ['Bone'], pose)
    np.testing.assert_allclose(animation.tracks[0].translations, [[2.0, 4.0, 6.0]] * 2)


def test_authored_base_idle_keeps_priority_over_numbered_loop(tmp_path):
    idle, _ = _split_idle(tmp_path, [(0.0, 'Idle: Start'), (0.5, 'Idle: Stop'),
        (0.5, 'Idle2: Start'), (0.6, 'Idle2: Loop Start'),
        (0.9, 'Idle2: Loop Stop'), (1.0, 'Idle2: Stop')])
    assert idle.duration == pytest.approx(0.5)


def test_missing_idle_fails_before_creature_project_outputs(tmp_path):
    from asset_convert.havok.hkx_behavior import generate_creature_project
    model, folder = tmp_path / 'source.nif', tmp_path / 'creature'
    _animated_model(model, [(0.0, 'WalkForward: Start'), (1.0, 'WalkForward: Stop')])
    split.split_creature(model, str(folder), 'body.nif')
    with pytest.raises(ValueError, match='no idle clip: .*available clips: forward.kf'):
        generate_creature_project(str(folder), 'creature', str(tmp_path / 'output'))
    assert not (tmp_path / 'output').exists()


def test_scoped_creature_split_leaves_other_sources_untouched(tmp_path):
    meshes = tmp_path / 'meshes'
    records = []
    for name in ('wanted', 'other'):
        _animated_model(meshes / f'{name}.nif', [(0.0, 'Idle: Start'), (0.0, 'Idle: Stop')])
        records.append({'MorrowindModel': f'{name}.nif',
                        'Model.MODL': f'{name}/skeleton.nif', 'NIFZ[0]': 'body.nif'})
    _write_crea(tmp_path, records)
    assert split.split_creatures(tmp_path, meshes, workers=1, names=['wanted']) == 1
    assert (meshes / 'wanted' / 'idle.kf').is_file()
    assert not (meshes / 'other').exists()
    # An older process may have written a skeleton after the fix was installed.
    # An explicit repair must reconstruct its missing idle regardless of dates.
    (meshes / 'wanted' / 'idle.kf').unlink()
    assert split.split_creatures(tmp_path, meshes, workers=1, names=['wanted']) == 1
    assert (meshes / 'wanted' / 'idle.kf').is_file()
    assert not (meshes / 'other').exists()
