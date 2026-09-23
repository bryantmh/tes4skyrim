"""FO3/FNV gun clips: the authored WEAP binding, the clip-stem grammar and
the world-space retarget onto the Skyrim humanoid skeleton."""

import os

import numpy as np
import pytest

from asset_convert.havok.gun_anim_falloutnv import classify_stem, select_stems
from asset_convert.havok.gun_vocabulary_falloutnv import RELOAD_LETTERS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FNV_MALE = os.path.join(REPO, 'export', 'FalloutNV.esm', 'meshes',
                        'characters', '_male')
needs_fnv = pytest.mark.skipif(
    not os.path.exists(os.path.join(FNV_MALE, '1hpreloada.kf')),
    reason='FalloutNV export assets missing')


class TestClipGrammar:
    def test_reload_letter_and_start(self):
        """A reload stem yields its letter and the `start` half flag."""
        c = classify_stem('2hrreloadxstart')
        assert (c['cls'], c['action'], c['letter'], c['start']) == \
            ('2hr', 'reload', 'x', True)
        assert classify_stem('1hpreloada')['start'] is False

    def test_iron_sight_and_pitch(self):
        """`is` and `up`/`down` suffixes come off as iron and pitch."""
        c = classify_stem('sneak1hpattack3isup')
        assert c['prefix'] == 'sneak' and c['action'] == 'attack3'
        assert c['iron'] and c['pitch'] == 'up'
        assert classify_stem('2hraimisdown')['pitch'] == 'down'
        assert classify_stem('2hraim')['iron'] is False

    def test_non_gun_stems_are_rejected(self):
        """Melee, movement-type and malformed stems classify as None."""
        assert classify_stem('mtidle') is None
        assert classify_stem('1hmattackleft') is None
        assert classify_stem('1hpattack3b') is None

    def test_reload_enum_order_is_xedits(self):
        """RELOAD_LETTERS follows wbReloadAnimEnum: A-S then W X Y Z."""
        assert RELOAD_LETTERS[0] == 'a' and RELOAD_LETTERS[18] == 's'
        assert RELOAD_LETTERS[19:] == 'wxyz'

    def test_selection_follows_the_authored_binding(self):
        """Only the letters/attacks the WEAPs name, plus the shared and
        locomotion sets, survive; of the iron-sight set the aim poses and
        the bound attacks (for the zoom key), and power armor never."""
        bindings = [{'cls': '1hp', 'reload': 'c', 'attack': 'attack3'},
                    {'cls': '2hr', 'reload': 'a', 'attack': None}]
        stems = ['1hpreloada', '1hpreloadc', 'sneak1hpreloadc', '1hpattack3',
                 '1hpattack4', '1hpaimis', '1hpattack3is', 'pa1hpaim',
                 '2hrreloada', '2hrattack3', '2hrattackleft', '2hlreloada',
                 '1hpequip', '2hrfastforward', '1hpturnleft', '2hrreloadastart']
        assert select_stems(bindings, stems) == [
            '1hpaimis', '1hpattack3', '1hpattack3is', '1hpequip', '1hpreloadc', '1hpturnleft',
            '2hrattack3', '2hrattackleft', '2hrfastforward', '2hrreloada',
            '2hrreloadastart', 'sneak1hpreloadc']

    def test_locomotion_stems_classify(self):
        """Walk, run and turn stems are actions of their class."""
        assert classify_stem('2hrfastleft')['action'] == 'fastleft'
        assert classify_stem('1hpturnright')['action'] == 'turnright'
        assert classify_stem('2hlbackward')['action'] == 'backward'


@needs_fnv
class TestRetarget:
    """The matched pose maps onto the Skyrim rest exactly, and a real clip
    lands its hands on the Skyrim body, not on FNV's lower-armed rest."""

    def _rig(self):
        """The cached source/target rig of the FNV male skeleton."""
        from asset_convert.havok.gun_anim_falloutnv import _rig
        return _rig(os.path.join(FNV_MALE, 'skeleton.nif'))

    def test_every_map_target_is_a_havok_bone(self):
        """The NIF spelling `WEAPON` resolves to the hkx bone `Weapon`."""
        rig = self._rig()
        assert rig['bone_map']['Weapon'] == 'Weapon'
        assert not [d for s, d in rig['bone_map'].items()
                    if s in rig['src'].index and d not in rig['dst'].index]

    def test_posed_source_is_the_target_rest(self):
        """Retargeting the matched pose reproduces every Skyrim rest rotation."""
        from asset_convert.havok.clip_retarget import (
            VERBATIM_BONES, posed_world, retarget_clip, mat_to_quat_wxyz)
        from asset_convert.havok.kf_decode import BoneTrack, DecodedClip
        rig = self._rig()
        src, dst = rig['src'], rig['dst']
        pose = posed_world(src, rig['deltas'])
        tracks = []
        for i, name in enumerate(src.names):
            p = src.parents[i]
            local = pose[i] if p < 0 else pose[i] @ np.linalg.inv(pose[p])
            scale = np.linalg.norm(local[0, :3])
            q = mat_to_quat_wxyz(local[:3, :3] / scale)
            tracks.append(BoneTrack(bone=name, rotations=np.array([q, q]),
                                    translations=np.array([local[3, :3]] * 2)))
        clip = DecodedClip(name='pose', duration=1 / 30, cycle_type=2,
                           frequency=1.0, times=np.array([0.0, 1 / 30]),
                           tracks=tracks)
        out = retarget_clip(clip, src, dst, rig['bone_map'], rig['deltas'])
        for tr in out.tracks:
            if tr.bone in VERBATIM_BONES:
                continue
            i = dst.index[tr.bone]
            rest = dst.local[i][:3, :3] / np.linalg.norm(dst.local[i][0, :3])
            q_rest = mat_to_quat_wxyz(rest)
            assert abs(abs(np.dot(q_rest, tr.rotations[0])) - 1) < 1e-5, \
                tr.bone

    @pytest.mark.parametrize('stem', ['1hpreloada', '2hraimis'])
    def test_clip_lands_where_the_source_put_it(self, stem):
        """Hands, forearms, head and pelvis land within the two skeletons'
        proportion difference of the source's own world positions, at the
        first frame and mid-clip (measured 1-8 units)."""
        from asset_convert.havok.clip_retarget import retarget_error
        from asset_convert.havok.gun_anim_falloutnv import (
            CHECK_BONES, retarget_gun_clip)
        rig = self._rig()
        src_clip, clip, _motion, ann, _ev = retarget_gun_clip(
            os.path.join(FNV_MALE, stem + '.kf'),
            os.path.join(FNV_MALE, 'skeleton.nif'))
        assert len(clip.tracks) >= 50
        assert {t.bone for t in clip.tracks} <= set(rig['dst'].names)
        for frame in (0, len(clip.times) // 2):
            err = retarget_error(src_clip, clip, rig['src'], rig['dst'],
                                 rig['bone_map'], CHECK_BONES, frame)
            assert len(err) == len(CHECK_BONES)
            assert all(v < 12.0 for v in err.values()), (frame, err)
        if stem == '1hpreloada':
            assert any(a.startswith('SoundPlay.TES4_') for _t, a in ann)


FNV_FIRST = os.path.join(os.path.dirname(FNV_MALE), '_1stperson')


@needs_fnv
class TestFirstPersonHands:
    """First-person hands land where FNV put them relative to the camera."""

    @pytest.mark.parametrize('stem', ['1hpattackleftis', '2hraimis'])
    def test_hands_reach_the_source_hands(self, stem):
        """The arm IK puts both hands (and so the gun's sight) on FNV's
        camera-relative positions, which the rotation retarget missed by
        2-8 units."""
        from asset_convert.havok.clip_retarget import world_positions
        from asset_convert.havok.gun_anim_falloutnv import (
            _rig, retarget_gun_clip)
        skel = os.path.join(FNV_FIRST, 'skeleton.nif')
        src_clip, clip, *_ = retarget_gun_clip(
            os.path.join(FNV_FIRST, stem + '.kf'), skel)
        rig = _rig(skel)
        s, d = rig['src'], rig['dst']
        sw, dw = (world_positions(src_clip, s, 0),
                  world_positions(clip, d, 0))
        scam = sw[s.index['Camera1st']]
        dcam = dw[d.index['Camera1st [Cam1]']]
        for src_bone in ('Bip01 R Hand', 'Bip01 L Hand', 'Weapon'):
            a = (sw[s.index[src_bone]] @ np.linalg.inv(scam))[3, :3]
            b = (dw[d.index[rig['bone_map'][src_bone]]]
                 @ np.linalg.inv(dcam))[3, :3]
            assert np.linalg.norm(a - b) < 0.05, (src_bone, a, b)

    def test_iron_clip_fills_from_the_iron_aim_first(self):
        """An iron-sight fire clip is composed over the iron aim, then the
        hip aim; a hip aim is composed over nothing."""
        from asset_convert.havok.gun_anim_falloutnv import _fill_clips
        corpus = {'1hpaim': 'aim.kf', '1hpattackleftis': 'leftis.kf',
                  '1hpattack3is': '3is.kf'}
        assert _fill_clips(corpus, '1hpattack3is') == ('leftis.kf', 'aim.kf')
        assert _fill_clips(corpus, '1hpaimis') == ('leftis.kf', 'aim.kf')
        assert _fill_clips(corpus, '1hpaim') == ()
