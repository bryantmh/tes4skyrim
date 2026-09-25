"""Tests for the creature animation pipeline:
kf_decode (B-spline decode) → hkx_skeleton (skeleton.hkx) → hkx_anim (clip hkx).

Uses the Oblivion dog as the fixture creature (small, mixed interpolator types,
real root motion). Tests that need source assets or hkxcmd skip cleanly when
they are absent.
"""

import os
import subprocess
import sys

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from asset_convert.havok.kf_decode import (decode_kf, eval_bspline,
                                     split_root_motion, knots,
                                     basis_weights)
from asset_convert.havok.hkx_xml import HKXCMD

DOG_DIR = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                       'creatures', 'dog')
DOG_SKEL = os.path.join(DOG_DIR, 'skeleton.nif')
DOG_FORWARD = os.path.join(DOG_DIR, 'forward.kf')
DOG_IDLE = os.path.join(DOG_DIR, 'idle.kf')

needs_assets = pytest.mark.skipif(
    not os.path.exists(DOG_FORWARD), reason='Oblivion export assets missing')
needs_hkxcmd = pytest.mark.skipif(
    not os.path.exists(HKXCMD), reason='hkxcmd.exe missing')


# ---------------------------------------------------------------------------
# B-spline evaluation math
# ---------------------------------------------------------------------------

class TestBSplineEval:
    def test_endpoints_interpolated(self):
        ctrl = np.array([[0.0, 0], [1, 5], [2, -3], [3, 1], [4, 4]])
        v = np.array([0.0, float(len(ctrl) - 3)])
        out = eval_bspline(ctrl, v)
        assert np.allclose(out[0], ctrl[0])
        assert np.allclose(out[-1], ctrl[-1])

    def test_partition_of_unity(self):
        n = 9
        u = knots(n)
        for v in np.linspace(0.0, n - 3 - 1e-6, 20):
            w = basis_weights(n, v, u)
            assert abs(w.sum() - 1.0) < 1e-9

    def test_curve_stays_in_convex_hull(self):
        ctrl = np.array([[0.0], [1], [2], [3], [4], [5]])
        out = eval_bspline(ctrl, np.linspace(0, 3, 50))
        assert out.min() >= -1e-9 and out.max() <= 5 + 1e-9

    def test_single_control_point(self):
        ctrl = np.array([[7.0, 8, 9]])
        out = eval_bspline(ctrl, np.array([0.0, 0.5]))
        assert np.allclose(out, [[7, 8, 9], [7, 8, 9]])


# ---------------------------------------------------------------------------
# KF decoding
# ---------------------------------------------------------------------------

@needs_assets
class TestDecodeKf:
    def test_forward_clip(self):
        clip = decode_kf(DOG_FORWARD)[0]
        assert clip.name == 'Forward'
        assert abs(clip.duration - 4.0 / 3.0) < 1e-3
        assert len(clip.tracks) == 45          # all transform tracks decoded
        bones = {t.bone for t in clip.tracks}
        assert 'Bip01' in bones and 'Bip01 Spine0' in bones
        # only float channels skipped
        assert all('FloatInterpolator' in why or 'rest pose' in why
                   for _, why in clip.skipped_blocks)

    def test_quaternions_unit_and_finite(self):
        clip = decode_kf(DOG_FORWARD)[0]
        for tr in clip.tracks:
            if tr.rotations is not None:
                n = np.linalg.norm(tr.rotations, axis=1)
                assert abs(n - 1).max() < 1e-5
                assert np.isfinite(tr.rotations).all()
            if tr.translations is not None:
                assert np.isfinite(tr.translations).all()

    def test_text_keys(self):
        clip = decode_kf(DOG_FORWARD)[0]
        texts = [s.strip() for _, s in clip.text_keys]
        assert texts[0] == 'start' and texts[-1] == 'end'
        assert any(s.startswith('Enum:') for s in texts)

    def test_static_rotation_fallback(self):
        # idle.kf Spine0 B-spline has no rotation channel — must fall back to
        # the interpolator's static transform, not drop the channel
        clip = decode_kf(DOG_IDLE)[0]
        sp0 = next(t for t in clip.tracks if t.bone == 'Bip01 Spine0')
        assert sp0.rotations is not None

    def test_root_motion_split_forward(self):
        clip = decode_kf(DOG_FORWARD)[0]
        motion = split_root_motion(clip)
        assert motion is not None and motion['bone'] == 'Bip01'
        assert np.linalg.norm(motion['translations'][-1]) > 70
        track = next(t for t in clip.tracks if t.bone == 'Bip01')
        assert np.allclose(track.translations, track.translations[0])

    def test_root_motion_split_idle_none(self):
        clip = decode_kf(DOG_IDLE)[0]
        assert split_root_motion(clip) is None


# ---------------------------------------------------------------------------
# Skeleton hkx generation
# ---------------------------------------------------------------------------

@needs_assets
class TestSkeletonHkx:
    def test_bone_collection(self):
        from asset_convert.havok.hkx_skeleton import load_skeleton_bones
        bones = load_skeleton_bones(DOG_SKEL)
        # engine contract: rig root renamed to the name SSE binds by
        # (all 30 vanilla creature rigs use exactly this root bone name)
        assert bones[0].name == 'NPC Root [Root]' and bones[0].parent == -1
        assert len(bones) == 45
        # parent-before-child ordering (required by Havok)
        for i, b in enumerate(bones):
            assert b.parent < i

    def test_quat_matrix_roundtrip(self):
        from asset_convert.nif.pyffi_monkey_patch import apply_patches
        apply_patches()
        from asset_convert.havok.hkx_skeleton import (mat33_to_quat_xyzw,
                                                find_skeleton_root,
                                                quat_xyzw_to_mat33)
        from pyffi.formats.nif import NifFormat
        data = NifFormat.Data()
        with open(DOG_SKEL, 'rb') as f:
            data.read(f)
        root = find_skeleton_root(data)
        stack = [root]
        while stack:
            nd = stack.pop()
            stack.extend(c for c in nd.children
                         if isinstance(c, NifFormat.NiNode))
            m = nd.rotation
            orig = [[m.m_11, m.m_12, m.m_13], [m.m_21, m.m_22, m.m_23],
                    [m.m_31, m.m_32, m.m_33]]
            rec = quat_xyzw_to_mat33(mat33_to_quat_xyzw(m))
            assert np.abs(np.array(rec) - np.array(orig)).max() < 1e-5

    @needs_hkxcmd
    def test_generate_and_roundtrip(self, tmp_path):
        from asset_convert.havok.hkx_skeleton import generate_skeleton_hkx
        from asset_convert.havok.hkx_xml import decompile_hkx
        out = str(tmp_path / 'skeleton.hkx')
        bones = generate_skeleton_hkx(DOG_SKEL, out)
        assert os.path.getsize(out) > 1000
        back = str(tmp_path / 'skeleton.xml')
        decompile_hkx(out, back)
        txt = open(back, encoding='ascii', errors='replace').read()
        assert 'hkaSkeleton' in txt
        for b in bones:
            assert b.name in txt


# ---------------------------------------------------------------------------
# Animation hkx generation (full path)
# ---------------------------------------------------------------------------

@needs_assets
@needs_hkxcmd
class TestAnimHkx:
    def test_convert_and_verify(self, tmp_path):
        from asset_convert.havok.hkx_anim import convert_clip_hkx, verify_hkx
        from asset_convert.havok.hkx_skeleton import load_skeleton_bones
        bones = load_skeleton_bones(DOG_SKEL)
        out = str(tmp_path / 'forward.hkx')
        clip, motion = convert_clip_hkx(DOG_FORWARD, bones, out)
        assert motion is not None
        stats = verify_hkx(out, clip, [b.name for b in bones])
        assert stats['tracks'] == len(bones)
        # spline-compressed round trip must be lossless within quantization
        assert stats['max_trans_err'] < 0.01
        assert stats['max_rot_err_deg'] < 0.1

    def test_hkxcmd_can_deserialize(self, tmp_path):
        # the real Havok deserializer (what the engine uses) must accept it
        from asset_convert.havok.hkx_anim import convert_clip_hkx
        from asset_convert.havok.hkx_skeleton import load_skeleton_bones
        bones = load_skeleton_bones(DOG_SKEL)
        out = str(tmp_path / 'forward.hkx')
        convert_clip_hkx(DOG_FORWARD, bones, out)
        res = subprocess.run(
            [HKXCMD, 'convert', '-v:XML', os.path.abspath(out),
             os.path.abspath(str(tmp_path / 'back.xml'))],
            capture_output=True, text=True)
        assert res.returncode == 0

    def test_annotations_carried(self, tmp_path):
        """Annotations must be TRANSLATED Skyrim events, never Oblivion's raw
        text keys — raw `Sound: X`/`Enum: Y` embedded verbatim was the
        confirmed root cause of totally silent creatures (2026-08-07)."""
        from asset_convert.havok.hkx_anim import convert_clip_hkx
        from asset_convert.havok.hkx_skeleton import load_skeleton_bones
        from external.pynifly_hkx.anim_skyrim import load_skyrim_animation
        bones = load_skeleton_bones(DOG_SKEL)
        kf = os.path.join(DOG_DIR, 'handtohandattackleft.kf')
        out = str(tmp_path / 'attack.hkx')
        convert_clip_hkx(kf, bones, out)
        back = load_skyrim_animation(out)
        texts = {a.text for a in back.annotations}
        assert any(t == 'HitFrame' for t in texts)
        assert any(t.startswith('SoundPlay.TES4_') and t.endswith('_SNDR')
                   for t in texts)
        low = {t.lower() for t in texts}
        assert not any(t.startswith(('sound:', 'enum:')) for t in low)

    def test_foot_enums_translated(self, tmp_path):
        """`Enum: Left/BackLeft/...` gait keys become engine footstep events
        at their AUTHORED times (FSTP.ANAM matches these names verbatim)."""
        from asset_convert.havok.hkx_anim import convert_clip_hkx
        from asset_convert.havok.hkx_skeleton import load_skeleton_bones
        from external.pynifly_hkx.anim_skyrim import load_skyrim_animation
        bones = load_skeleton_bones(DOG_SKEL)
        out = str(tmp_path / 'forward.hkx')
        convert_clip_hkx(DOG_FORWARD, bones, out)
        back = load_skyrim_animation(out)
        texts = {a.text for a in back.annotations}
        assert 'FootFront' in texts and 'FootBack' in texts


class TestPerPluginProjectNamespace:
    """Two plugins' same-named creature folders must never share a path.

    Oblivion's scamp and Morrowind_ob's Morrowind scamp both came from a
    folder called `scamp`; under one shared `actors\\tes4\\scamp` path only
    one survived in Data, and a Stunted Scamp in Oblivion ran Morrowind_ob's
    graph — no Spell_FireForget_LH, none of Oblivion's attackStart_TES4_*
    events (read back from the engine's live event map, 2026-08-23). The
    owning plugin is now part of the directory, every project file stem and
    the project name the animation caches are keyed on.
    """

    def test_layout_is_namespaced_everywhere(self):
        from asset_convert.game_paths import set_namespace
        from asset_convert.havok.hkx_behavior import project_layout
        set_namespace('tes4')
        a = project_layout('scamp', 'oblivion')
        b = project_layout('scamp', 'morrowind_ob')
        for key in ('project_hkx', 'project_txt', 'behavior_hkx', 'anim_dir',
                    'skeleton_nif', 'body_dir', 'fs_dir'):
            assert a[key] != b[key], key
        assert a['project_hkx'] == \
            'Actors\\tes4\\oblivion\\scamp\\tes4oblivion_scampproject.hkx'
        assert a['behavior_hkx'] == ('Actors\\tes4\\oblivion\\scamp\\'
                                     'Behaviors\\tes4oblivion_scampbehavior.hkx')
        assert a['project_txt'] == 'tes4oblivion_scampproject.txt'
        assert a['fs_dir'] == os.path.join('actors', 'tes4', 'oblivion',
                                           'scamp')

    def test_layout_is_namespaced_per_GAME(self):
        """A second game's creature of the same name is a distinct project."""
        from asset_convert.game_paths import set_namespace
        from asset_convert.havok.hkx_behavior import project_layout
        set_namespace('tes4')
        obl = project_layout('scamp', 'oblivion')
        set_namespace('falloutnv')
        fnv = project_layout('scamp', 'oblivion')
        set_namespace('tes4')
        for key in ('project_hkx', 'project_txt', 'behavior_hkx', 'anim_dir',
                    'skeleton_nif', 'body_dir', 'fs_dir'):
            assert obl[key] != fnv[key], key
        assert fnv['project_txt'] == 'falloutnvoblivion_scampproject.txt'

    def test_namespace_is_the_plugin_stem(self):
        from asset_convert.havok.creature_pipeline import plugin_namespace
        assert plugin_namespace('Oblivion.esm') == 'oblivion'
        assert plugin_namespace('Morrowind_ob.esm') == 'morrowind_ob'
        assert plugin_namespace('DLCShiveringIsles.esp') == 'dlcshiveringisles'
        assert plugin_namespace(
            'Morrowind_ob - Chargen and Transport Mod.esp') == \
            'morrowind_ob_chargen_and_transport_mod'

    def test_sibling_union_keeps_both_plugins_projects(self, tmp_path):
        """Same folder name in two plugins = two distinct projects, so each
        plugin's fragment is built from its own tree alone."""
        import json
        from asset_convert.havok.creature_pipeline import manifests_under
        for plug, ns in (('Oblivion.esm', 'oblivion'),
                         ('Morrowind_ob.esm', 'morrowind_ob')):
            d = tmp_path / plug / 'meshes' / 'actors' / 'tes4' / ns / 'scamp'
            d.mkdir(parents=True)
            (d / 'project_manifest.json').write_text(json.dumps(
                {'name': 'scamp', 'namespace': ns,
                 'project_txt': f'tes4{ns}_scampproject.txt'}))
        a = manifests_under(str(tmp_path / 'Oblivion.esm' / 'meshes'))
        b = manifests_under(str(tmp_path / 'Morrowind_ob.esm' / 'meshes'))
        assert set(a) == {'tes4oblivion_scampproject.txt'}
        assert set(b) == {'tes4morrowind_ob_scampproject.txt'}
        assert not (set(a) & set(b))

    def test_every_clip_index_is_in_range(self):
        """No clip may index past the character file list."""
        from asset_convert.havok.animation_data import anim_file_index
        m = {'project_txt': 'tes4xproject.txt',
             'clips': [{'anim': 'Animations\\a%d.hkx' % i}
                       for i in range(17)]}
        idx = anim_file_index(m)
        n_files = len(dict.fromkeys(c['anim'] for c in m['clips']))
        assert all(0 <= i < n_files for i in idx.values())


def _manifest(ns, folder, n_clips=2):
    """A minimal project manifest the block emitters accept."""
    return {'name': folder, 'namespace': ns,
            'project_txt': f'tes4{ns}_{folder}project.txt',
            'project_files': ['Behaviors\\x.hkx', 'Characters\\x.hkx',
                              'Character Assets\\skeleton.HKX'],
            'anim_dir': f'meshes\\actors\\tes4\\{ns}\\{folder}\\animations',
            'clips': [{'name': f'Clip{i}', 'stem': f'clip{i}',
                       'anim': f'Animations\\clip{i}.hkx', 'duration': 1.0,
                       'looping': True, 'end_event': None, 'sounds': [],
                       'feet': [], 'hits': []} for i in range(n_clips)],
            'motions': {}, 'attacks': []}


def _base_ad():
    """A one-project vanilla animationdata file (wolf: one clip, motion)."""
    return ['1', 'wolfproject.txt', '11', '1', '1', 'Behaviors\\w.hkx',
            '1', 'Idle', '0', '1', '0', '0', '0', '', '7', '0', '1', '1',
            '1 0 0 0', '1', '1 0 0 0 1', '']


def _base_asd():
    """The matching one-project animationsetdata file."""
    return ['1', 'WolfProjectData\\WolfProject.txt', '1',
            'FullCharacter.txt', 'V3', '0', '0', '0', '0']


class TestAnimCacheFragments:
    """The converter writes fragments; CreatureRuntime.dll composes them onto
    the vanilla singlefiles at load. compose_* here is the DLL's reference:
    the same rule (append, dedupe by name) the old build-time merge used.
    See docs/reference/tes_runtime_fragments.md."""

    def test_writing_a_fragment_removes_its_pre_split_copy(self, tmp_path):
        """A rebuild deletes the plugin's fragment from the old TESRuntime
        folder, and only that plugin's."""
        from asset_convert.havok.animation_data import write_fragment
        legacy = tmp_path / 'SKSE' / 'Plugins' / 'TESRuntime' / 'animation'
        legacy.mkdir(parents=True)
        (legacy / 'Oblivion.json').write_text('{}', encoding='utf-8')
        (legacy / 'Other.json').write_text('{}', encoding='utf-8')
        write_fragment([_manifest('oblivion', 'dog')], str(tmp_path / 'meshes'),
                       'Oblivion.esm')
        assert sorted(p.name for p in legacy.iterdir()) == ['Other.json']

    def test_fragment_holds_only_own_projects(self, tmp_path):
        """A plugin's fragment lists its own projects, sorted, and no
        singlefile is written beside it."""
        import json
        from asset_convert.havok.animation_data import write_fragment
        path = write_fragment([_manifest('oblivion', 'scamp'),
                               _manifest('oblivion', 'dog')],
                              str(tmp_path / 'meshes'), 'Oblivion.esm')
        assert path == str(tmp_path / 'SKSE' / 'Plugins' / 'CreatureRuntime'
                           / 'animation' / 'Oblivion.json')
        frag = json.load(open(path, encoding='utf-8'))
        assert frag['version'] == 1 and frag['source'] == 'Oblivion.esm'
        assert [e['project'] for e in frag['animdata']] == \
            ['tes4oblivion_dogproject.txt', 'tes4oblivion_scampproject.txt']
        assert frag['animsetdata'][0]['entry'] == \
            'tes4oblivion_dogprojectData\\tes4oblivion_dogproject.txt'
        assert (tmp_path / 'meshes' / 'animationdata'
                / 'tes4oblivion_dogproject.txt').is_file()
        assert not (tmp_path / 'meshes'
                    / 'animationdatasinglefile.txt').exists()

    def test_compose_registers_every_fragment_once(self):
        """Distinct project names all register; a duplicate is skipped and
        every registered block is wrapped by its own line count."""
        from asset_convert.havok.animation_data import (
            compose_animationdata, compose_animationsetdata,
            manifest_fragment)
        base_ad = _base_ad()
        frags = []
        for ns in ('oblivion', 'morrowind_ob', 'oblivion'):
            ad, asd = manifest_fragment(_manifest(ns, 'scamp'))
            frags.append({'animdata': [ad], 'animsetdata': [asd]})
        ad = compose_animationdata(base_ad, frags)
        asd = compose_animationsetdata(_base_asd(), frags)
        assert ad[0] == '3' and ad[1:4] == [
            'wolfproject.txt', 'tes4oblivion_scampproject.txt',
            'tes4morrowind_ob_scampproject.txt']
        assert asd[0] == '3'
        body = ad[4:]
        assert body[:len(base_ad) - 2] == base_ad[2:]
        n = int(body[len(base_ad) - 2])
        assert n == len(frags[0]['animdata'][0]['clip_block'])

    def test_compose_is_a_no_op_without_fragments(self):
        """No fragments: the base comes back untouched."""
        from asset_convert.havok.animation_data import compose_animationdata
        assert compose_animationdata(_base_ad(), []) == _base_ad()

    def test_append_splices_into_an_existing_project(self):
        """Append ops land inside the named project and rewrite its counts."""
        from asset_convert.havok.animation_data import (
            compose_animationdata, compose_animationsetdata)
        frag = {'animdata_append': [
                    {'project': 'wolfproject.txt',
                     'clips': ['Gun', '0', '1', '0', '0', '0', ''],
                     'motions': ['0', '2', '1', '2 0 0 0', '1',
                                 '2 0 0 0 1', '']}],
                'animsetdata_append': [
                    {'entry': 'WolfProjectData\\WolfProject.txt',
                     'set_file': '_gun.txt',
                     'block': ['V3', '0', '1', 'iRightHandType', '10', '10',
                               '0', '0']}]}
        ad = compose_animationdata(_base_ad(), [frag])
        assert ad[0] == '1'
        assert ad[2] == str(11 + 7) and ad[3 + 11:3 + 18] == \
            ['Gun', '0', '1', '0', '0', '0', '']
        assert ad[3 + 18] == str(7 + 7) and ad[-7:] == \
            frag['animdata_append'][0]['motions']
        asd = compose_animationsetdata(_base_asd(), [frag])
        assert asd[2] == '2' and asd[3:5] == ['FullCharacter.txt', '_gun.txt']
        assert asd[-8:] == frag['animsetdata_append'][0]['block']

    def test_cpp_composer_matches_python(self, tmp_path):
        """tes_runtime/creature/compose_test.exe (the DLL's composer, built by
        tes_runtime/creature/build.bat) must produce byte-identical files to the
        Python reference over the same base and fragments."""
        import json
        import subprocess
        from asset_convert.havok.animation_data import (
            compose_singlefiles, read_fragments, write_composed,
            write_fragment)
        exe = os.path.join(REPO, 'tes_runtime', 'creature', 'compose_test.exe')
        if not os.path.isfile(exe):
            pytest.skip('tes_runtime/creature/compose_test.exe not built')
        base_dir = tmp_path / 'base'
        base_dir.mkdir()
        base = {'animationdatasinglefile.txt': _base_ad(),
                'animationsetdatasinglefile.txt': _base_asd()}
        write_composed(base, str(base_dir))
        frag_dir = tmp_path / 'SKSE' / 'Plugins' / 'CreatureRuntime' / 'animation'
        write_fragment([_manifest('oblivion', 'scamp', 3),
                        _manifest('oblivion', 'dog')],
                       str(tmp_path / 'meshes'), 'Oblivion.esm')
        (frag_dir / 'zz_append.json').write_text(json.dumps({
            'version': 1, 'source': 't', 'animdata': [], 'animsetdata': [],
            'animdata_append': [{'project': 'wolfproject.txt',
                                 'clips': ['Gun', '0', '1', '0', '0', '0',
                                           ''],
                                 'motions': ['0', '2', '1', '2 0 0 0', '1',
                                             '2 0 0 0 1', '']}],
            'animsetdata_append': [{
                'entry': 'WolfProjectData\\WolfProject.txt',
                'set_file': '_gun.txt',
                'block': ['V3', '0', '1', 'iRightHandType', '10', '10',
                          '0', '0']}]}))
        py_dir, cpp_dir = tmp_path / 'py', tmp_path / 'cpp'
        py_dir.mkdir()
        cpp_dir.mkdir()
        write_composed(compose_singlefiles(base, read_fragments(str(frag_dir))),
                       str(py_dir))
        assert subprocess.run([exe, str(base_dir), str(frag_dir),
                               str(cpp_dir)]).returncode == 0
        for fn in base:
            assert (py_dir / fn).read_bytes() == (cpp_dir / fn).read_bytes()


# ---------------------------------------------------------------------------
# Spellcasting lane (the magic handshake the engine waits on)
# ---------------------------------------------------------------------------

SCAMP_DIR = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                         'creatures', 'scamp')
needs_scamp = pytest.mark.skipif(
    not os.path.exists(os.path.join(SCAMP_DIR, 'castself.kf')),
    reason='Oblivion scamp export assets missing')


@needs_scamp
class TestCastLane:
    """Cast clips must become the vanilla FireForget chain wired to the
    ENGINE's magic handshake (decompiled from atronachflamebehavior.hkx):
    the graph raises BeginCast* TO the engine, the engine replies with
    Spell_FireForget_LH/RH (the state-entry events), Magic_Pre_Out chains
    In->Loop, Spell_Release moves to the Out, and the Out clip's
    MLh_SpellFire_Event trigger is what actually fires the spell."""

    def _clips(self):
        from asset_convert.havok.hkx_behavior import classify_clips
        return classify_clips(SCAMP_DIR)

    def test_cast_clips_are_claimed_not_dropped(self):
        clips = self._clips()
        assert set(clips['cast']) == {'Self', 'Target'}
        # nothing named cast* may survive in the dead bucket
        assert not [p for p in clips['extra']
                    if os.path.basename(p).lower().startswith('cast')]

    def test_cast_states_are_three_phase(self):
        """A Skyrim cast is In -> Loop -> Out (vanilla Mag_FF_RH_In/_Loop/
        _Out), ONE chain per graph — vanilla creature casters route
        everything through the left hand (the atronach's RH states are dead
        code and even its RH Out fires MLh_SpellFire_Event)."""
        from asset_convert.havok.hkx_behavior import cast_phase_defs
        names = [s for s, _kf, _p, _st in cast_phase_defs(self._clips())]
        assert names == ['Mag_FF_In', 'Mag_FF_Loop', 'Mag_FF_Out']

    def test_cast_plays_the_aimed_clip_first(self):
        """The engine's entry event carries no delivery, so the chain plays
        the most cast-like gesture available: casttarget over castself."""
        from asset_convert.havok.behavior_clips import cast_clip
        assert os.path.basename(cast_clip(self._clips())).lower() \
            == 'casttarget.kf'

    def test_only_the_loop_repeats(self):
        from asset_convert.havok.hkx_behavior import state_defs
        loops = {n: l for n, _k, l, _e, _x in state_defs(self._clips())
                 if n.startswith('Mag_FF_')}
        assert loops == {'Mag_FF_In': False, 'Mag_FF_Loop': True,
                         'Mag_FF_Out': False}

    def test_each_phase_gets_its_own_animation(self):
        """The phases are CUT from one source clip, so they cannot share
        one animation file."""
        from asset_convert.havok.hkx_behavior import cast_anim_stems
        stems = cast_anim_stems(self._clips())
        assert stems == {'Mag_FF_In': 'casttarget_In',
                         'Mag_FF_Loop': 'casttarget_Loop',
                         'Mag_FF_Out': 'casttarget_Out'}

    def test_split_is_anchored_on_the_authored_hit_key(self):
        """The release moment is the clip's authored 'Hit' key; the Out
        opens CAST_PRE_RELEASE before it (vanilla's Out carries ~0.23s of
        wind-up before its SpellFire trigger) and the returned offset points
        the trigger back at the authored moment exactly."""
        from asset_convert.havok.hkx_anim import (decode_clip, split_cast_clip,
                                            CAST_LOOP_SECONDS,
                                            CAST_PRE_RELEASE)
        clip, _m = decode_clip(self._clips()['cast']['Target'], 30.0)
        hit = [t for t, v in clip.text_keys if v.strip().lower() == 'hit'][0]
        i, l, o, rel = split_cast_clip(clip)
        cut = max(0.0, hit - CAST_PRE_RELEASE)
        assert abs(i.duration - cut) < 0.05
        assert abs(l.duration - CAST_LOOP_SECONDS) < 1e-6
        assert abs(o.duration - (clip.duration - cut)) < 0.05
        assert abs((cut + rel) - hit) < 1e-6
        # the slices must carry real animation, not empty tracks
        assert len(i.tracks) == len(clip.tracks) == len(o.tracks)
        assert len(i.times) > 1 and len(o.times) > 1

    def test_graph_declares_the_engine_magic_interface(self):
        """Without these variables the engine cannot ask for a cast, and
        without the entry/release vocabulary it can never drive one."""
        from asset_convert.havok.hkx_behavior import build_behavior_xml
        xml = build_behavior_xml('TES4ScampBehavior', self._clips())
        for var in ('bWantCastRight', 'bWantCastLeft', 'bMRh_Ready',
                    'bMLh_Ready', 'IsCasting'):
            assert var in xml, var
        for evt in ('MRh_SpellFire_Event', 'MLh_SpellFire_Event',
                    'BeginCastRight', 'BeginCastLeft',
                    'Spell_FireForget_LH', 'Spell_FireForget_RH',
                    'Magic_Pre_Out', 'Spell_Release', 'Spell_Stop'):
            assert evt in xml, evt
        # the vanilla gating conditions, verbatim — and ONLY those: an
        # expression can read variables, never events, so no invented
        # event-conditioned expressions may exist
        assert ('BeginCastRight if (bWantCastRight && bMRh_Ready '
                '&& !IsCasting)') in xml
        assert ('BeginCastLeft if (bWantCastLeft && bMLh_Ready '
                '&& !IsCasting)') in xml
        assert 'if (BeginCast' not in xml

    def test_ready_and_want_variables_init_to_zero(self):
        """Vanilla inits bM*h_Ready/bWantCast*/IsCasting to 0 — readiness is
        the ENGINE's to grant. (A working caster, the atronach, ships all
        five at 0 in its wordVariableValues.)"""
        import re
        from asset_convert.havok.hkx_behavior import build_behavior_xml
        xml = build_behavior_xml('TES4ScampBehavior', self._clips())
        names = re.search(r'name="variableNames"[^>]*>(.*?)</hkparam>',
                          xml, re.S).group(1)
        order = re.findall(r'<hkcstring>(.*?)</hkcstring>', names)
        vals = xml[xml.index('wordVariableValues'):
                   xml.index('quadVariableValues')]
        words = re.findall(r'name="value">(-?\d+)</hkparam>', vals)
        for var in ('bWantCastRight', 'bWantCastLeft', 'bMRh_Ready',
                    'bMLh_Ready', 'IsCasting'):
            assert int(words[order.index(var)]) == 0, var

    def test_release_fires_from_the_out_phase(self):
        """The Out clip's trigger array must carry the SpellFire release —
        it is what actually fires the spell — plus Spell_Stop at clip end;
        vanilla's Mag_FF_RH_Out block is exactly that pair."""
        import re
        from asset_convert.havok.hkx_behavior import build_behavior_xml
        xml = build_behavior_xml('TES4ScampBehavior', self._clips())
        names = re.search(r'name="eventNames"[^>]*>(.*?)</hkparam>',
                          xml, re.S).group(1)
        order = re.findall(r'<hkcstring>(.*?)</hkcstring>', names)
        fire_id = order.index('MLh_SpellFire_Event')
        stop_id = order.index('Spell_Stop')
        assert f'<hkparam name="id">{fire_id}</hkparam>' in xml
        assert f'<hkparam name="id">{stop_id}</hkparam>' in xml


# ---------------------------------------------------------------------------
# Ghost/wraith dissolve: NiVisController -> bone scale (docs/creature_conversion
# ---------------------------------------------------------------------------

GHOST_DIR = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                         'creatures', 'ghost')
GHOST_DEATH = os.path.join(GHOST_DIR, 'death.kf')

needs_ghost = pytest.mark.skipif(
    not os.path.exists(GHOST_DEATH), reason='ghost export assets missing')


@needs_ghost
class TestVisibilityChannels:
    """The ghost's whole death is visibility, not motion.  Dropping these
    channels left the corpse hovering upright at standing height."""

    def _clip(self):
        return decode_kf(GHOST_DEATH)[0]

    def test_death_clip_never_lowers_the_body(self):
        # The premise of the bug: holding the last frame is NOT a corpse on
        # the ground for this creature.
        clip = self._clip()
        na = next(t for t in clip.tracks if t.bone == 'Bip01 NonAccum')
        z = na.translations[:, 2]
        assert z.min() > 60.0, f'expected standing height, got {z.min()}'

    def test_visibility_channels_are_captured(self):
        clip = self._clip()
        got = {b for b, _ in clip.vis_tracks}
        for bone in ('SkinAttachment', 'AttachmentsBip', 'AttachmentsShrink',
                     'AttachmentsHead', 'AttachmentsLeftHand',
                     'AttachmentsRightHand'):
            assert bone in got, f'{bone} visibility channel dropped'

    def test_body_hides_and_ectoplasm_reveals(self):
        clip = self._clip()
        vis = dict(clip.vis_tracks)
        # body: visible -> hidden
        for bone in ('SkinAttachment', 'AttachmentsHead',
                     'AttachmentsLeftHand', 'AttachmentsRightHand'):
            v = vis[bone]
            assert v[0] == 1.0 and v[-1] == 0.0, f'{bone} {v[0]}->{v[-1]}'
        # ectoplasm: hidden -> visible
        v = vis['AttachmentsBip']
        assert v[0] == 0.0 and v[-1] == 1.0
        # shrink blob: appears then goes again
        v = vis['AttachmentsShrink']
        assert v[0] == 0.0 and v[-1] == 0.0 and v.max() == 1.0

    def test_visibility_never_becomes_an_animation_track(self):
        # A Havok clip carries bone transforms only.  Converting these curves
        # to a bone-SCALE collapse was tried and REVERTED: our merged bodies
        # skin the torso to the posing Bip01 bones (Oblivion attaches the skin
        # parts into `SkinAttachment` at RUNTIME, and that node is a SIBLING of
        # the rig), so only the single-bone head/hand parts would hide -- a
        # decapitated corpse.  The dissolve is done with AttachAshPile instead.
        clip = self._clip()
        assert clip.vis_tracks, 'visibility curves should still be decoded'
        vis_bones = {b for b, _ in clip.vis_tracks}
        stray = [t.bone for t in clip.tracks
                 if t.bone in vis_bones and t.scales is not None
                 and t.translations is None and t.rotations is None]
        assert not stray, f'visibility leaked into animation tracks: {stray}'

    def test_bool_keys_are_step_not_lerped(self):
        # every sample must be exactly shown or hidden, never in between
        clip = self._clip()
        for bone, v in clip.vis_tracks:
            assert set(np.unique(v)).issubset({0.0, 1.0}), bone


@needs_ghost
class TestTrackMerge:
    """A bone driven by BOTH a transform and a vis controller must keep both
    channels -- the old dict comprehension kept only the last one, which
    dropped the visibility scale on exactly the two ectoplasm bones."""

    def test_duplicate_bone_tracks_merge_not_overwrite(self):
        from asset_convert.havok.hkx_anim import (clip_to_animation_data,
                                            reference_pose_from_bones)
        from asset_convert.havok.hkx_skeleton import load_skeleton_bones

        skel = os.path.join(REPO, 'output', 'Oblivion.esm', 'meshes',
                            'actors', 'tes4', 'oblivion', 'ghost',
                            'character assets', 'skeleton.nif')
        if not os.path.exists(skel):
            pytest.skip('converted ghost skeleton missing')

        from asset_convert.havok.kf_decode import BoneTrack

        bones = load_skeleton_bones(skel)
        order = [b.name for b in bones]
        clip = decode_kf(GHOST_DEATH)[0]

        # Build the duplicate directly: one sequence CAN drive a node from
        # several controlled blocks with disjoint channels, and the old dict
        # comprehension kept only the last, silently dropping the other.
        bone = 'Bip01 Pelvis'
        base = next(t for t in clip.tracks if t.bone == bone)
        n = len(clip.times)
        clip.tracks.append(BoneTrack(bone=bone, translations=None,
                                     rotations=None,
                                     scales=np.linspace(1.0, 0.25, n)))

        anim = clip_to_animation_data(clip, order,
                                      reference_pose_from_bones(bones))
        idx = {n2: i for i, n2 in enumerate(order)}
        td = anim.tracks[idx[bone]]
        s = np.array([v[0] for v in td.scales])
        # the appended SCALE survived...
        assert abs(s[-1] - 0.25) < 1e-6, s[-1]
        # ...and so did the original TRANSLATION channel
        assert base.translations is not None
        t0 = np.array(td.translations[0])
        assert np.allclose(t0, base.translations[0], atol=1e-4), t0

    def test_merge_does_not_mutate_the_source_clip(self):
        # a clip can be written more than once (cast splits reuse one decode)
        from asset_convert.havok.hkx_anim import (clip_to_animation_data,
                                            reference_pose_from_bones)
        from asset_convert.havok.hkx_skeleton import load_skeleton_bones

        skel = os.path.join(REPO, 'output', 'Oblivion.esm', 'meshes',
                            'actors', 'tes4', 'oblivion', 'ghost',
                            'character assets', 'skeleton.nif')
        if not os.path.exists(skel):
            pytest.skip('converted ghost skeleton missing')
        bones = load_skeleton_bones(skel)
        clip = decode_kf(GHOST_DEATH)[0]
        before = [(t.bone, t.translations is None, t.rotations is None,
                   t.scales is None) for t in clip.tracks]
        clip_to_animation_data(clip, [b.name for b in bones],
                               reference_pose_from_bones(bones))
        after = [(t.bone, t.translations is None, t.rotations is None,
                  t.scales is None) for t in clip.tracks]
        assert before == after

# ---------------------------------------------------------------------------
# Ghost dissolve: authored detection + the two-script VMAD
# ---------------------------------------------------------------------------

class TestDissolveDetection:
    """The trigger is the AUTHORED animation -- a death clip that HIDES the
    actor's own skin holder instead of dropping the body -- never a name."""

    def _detect(self, path):
        from asset_convert.havok.hkx_behavior import detect_dissolve
        clip = decode_kf(path)[0]
        return detect_dissolve({'death': (clip, None)})

    @needs_ghost
    def test_ghost_death_is_a_dissolve(self):
        got = self._detect(GHOST_DEATH)
        assert got['dissolves'] is True
        assert got['duration'] > 1.0

    @needs_ghost
    def test_duration_is_the_clip_length(self):
        clip = decode_kf(GHOST_DEATH)[0]
        got = self._detect(GHOST_DEATH)
        assert abs(got['duration'] - clip.duration) < 1e-6

    def test_wraith_death_is_a_dissolve(self):
        p = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                         'creatures', 'wraith', 'death.kf')
        if not os.path.exists(p):
            pytest.skip('wraith assets missing')
        assert self._detect(p)['dissolves'] is True

    def test_ordinary_creature_is_not_a_dissolve(self):
        # willothewisp is the trap: it HAS a 'Bip01 ContainerGoo01' node and
        # visibility channels, but never hides its body -- so it must not be
        # mistaken for a dissolving creature.
        p = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                         'creatures', 'willothewisp', 'death.kf')
        if not os.path.exists(p):
            pytest.skip('willothewisp assets missing')
        assert self._detect(p)['dissolves'] is False

    def test_missing_death_clip_is_not_a_dissolve(self):
        from asset_convert.havok.hkx_behavior import detect_dissolve
        assert detect_dissolve({})['dissolves'] is False
        assert detect_dissolve(None)['dissolves'] is False


class TestVmadAppend:
    """A dissolving creature that ALSO has a converted TES4 script must carry
    both -- build_vmad_object_script writes a fixed '1 attached script'."""

    def _parse(self, data):
        import struct
        _v, _f, count = struct.unpack_from('<HHH', data, 0)
        off = 6
        out = []
        for _ in range(count):
            ln = struct.unpack_from('<H', data, off)[0]
            off += 2
            name = data[off:off + ln].decode()
            off += ln + 1
            nprops = struct.unpack_from('<H', data, off)[0]
            off += 2
            props = {}
            for _p in range(nprops):
                pl = struct.unpack_from('<H', data, off)[0]
                off += 2
                pn = data[off:off + pl].decode()
                off += pl
                ptype = data[off]
                off += 2
                if ptype == 1:
                    props[pn] = struct.unpack_from('<HhI', data, off)[2]
                    off += 8
                elif ptype == 4:
                    props[pn] = struct.unpack_from('<f', data, off)[0]
                    off += 4
                elif ptype == 3:
                    props[pn] = struct.unpack_from('<i', data, off)[0]
                    off += 4
            out.append((name, props))
        return out, off

    def test_append_to_existing_keeps_both(self):
        from script_convert.pipeline import (build_vmad_object_script,
                                             append_vmad_object_script)
        first = build_vmad_object_script('TES4_Existing', {'X': 0x00012345})
        both = append_vmad_object_script(
            first, 'TES4_GhostDissolve',
            object_props={'AshPile': 0x00101048},
            value_props={'DeathAnimSeconds': ('float', 1.25)})
        scripts, consumed = self._parse(both)
        assert [n for n, _ in scripts] == ['TES4_Existing',
                                           'TES4_GhostDissolve']
        assert scripts[0][1]['X'] == 0x00012345
        assert scripts[1][1]['AshPile'] == 0x00101048
        assert abs(scripts[1][1]['DeathAnimSeconds'] - 1.25) < 1e-6
        # every byte accounted for -- a short read here means a corrupt VMAD
        assert consumed == len(both)

    def test_append_to_empty_builds_one(self):
        from script_convert.pipeline import append_vmad_object_script
        only = append_vmad_object_script(
            b'', 'TES4_GhostDissolve',
            object_props={'AshPile': 0x00101048})
        scripts, consumed = self._parse(only)
        assert [n for n, _ in scripts] == ['TES4_GhostDissolve']
        assert consumed == len(only)


class TestDeathPileExtraction:
    """The pile a ghost leaves is AUTHORED geometry inside its own skeleton,
    not Skyrim's DefaultAshPileGhost (docs/commentary/asset_convert_creature.md
    "THE PILE IS OBLIVION'S OWN")."""

    def _extract(self, folder, tmp_path):
        import numpy as np
        from asset_convert.havok.creature_mesh import extract_death_pile
        from asset_convert.havok.hkx_behavior import detect_dissolve
        from pyffi.formats.nif import NifFormat

        skel = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                            'creatures', folder, 'skeleton.nif')
        kf = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                          'creatures', folder, 'death.kf')
        if not (os.path.exists(skel) and os.path.exists(kf)):
            pytest.skip(f'{folder} assets missing')
        info = detect_dissolve({'death': (decode_kf(kf)[0], None)})
        dst = str(tmp_path / f'{folder}pile.nif')
        ok = extract_death_pile(skel, dst,
                                reveal_holders=info['reveals'],
                                holder_offsets=info['offsets'])
        if not ok:
            return None
        d = NifFormat.Data()
        with open(dst, 'rb') as f:
            d.read(f)
        out = []
        for b in d.roots[0].tree():
            cn = b.__class__.__name__
            if ('TriShape' in cn or 'TriStrips' in cn) and 'Data' not in cn:
                v = np.array([[p.x, p.y, p.z] for p in b.data.vertices])
                wz = v[:, 2] * b.scale + b.translation.z
                out.append((bytes(b.name).rstrip(b'\x00').decode('latin-1'),
                            len(v), float(wz.min()), float(wz.max()),
                            int(b.flags) & 1))
        return out

    def test_ghost_pile_is_the_authored_ectoplasm(self, tmp_path):
        got = self._extract('ghost', tmp_path)
        assert got, 'no pile extracted'
        names = [g[0] for g in got]
        assert 'Bip01 ectoplasm:0' in names, names

    def test_pile_rests_on_the_ground(self, tmp_path):
        # Scene Root is world Z 0; a pile left at body height means the clip's
        # final holder position was not applied (or was applied twice).
        for folder in ('ghost', 'wraith'):
            got = self._extract(folder, tmp_path)
            if not got:
                continue
            _nm, _n, zmin, zmax, _h = got[0]
            assert -20.0 < zmin < 30.0, f'{folder} pile at Z {zmin}..{zmax}'

    def test_pile_shapes_are_visible(self, tmp_path):
        # the source hides this geometry until the death clip reveals it
        for folder in ('ghost', 'wraith'):
            got = self._extract(folder, tmp_path)
            for nm, _n, _a, _b, hidden in (got or []):
                assert hidden == 0, f'{folder}/{nm} still hidden'

    def test_each_shape_extracted_once(self, tmp_path):
        # pyffi's tree() yields a block once PER REFERENCE and the ghost's
        # ectoplasm is referenced twice -- an un-deduped loop applies the
        # placement offset twice and the pile ends up at body height.
        got = self._extract('ghost', tmp_path)
        assert got is not None
        names = [g[0] for g in got]
        assert len(names) == len(set(names)), names

    def test_no_pile_when_nothing_is_revealed(self, tmp_path):
        from asset_convert.havok.creature_mesh import extract_death_pile
        skel = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                            'creatures', 'ghost', 'skeleton.nif')
        if not os.path.exists(skel):
            pytest.skip('ghost assets missing')
        dst = str(tmp_path / 'none.nif')
        assert extract_death_pile(skel, dst, reveal_holders=()) is False
        assert not os.path.exists(dst)


class TestParticleColor:
    """A converted particle system must keep its AUTHORED color, and its
    shader tint must stay neutral when the particles carry their own color
    (docs/commentary/asset_convert_creature.md -- the ghost's smoke rendered black)."""

    GHOST_SKEL = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                              'creatures', 'ghost', 'skeleton.nif')

    def _convert(self, tmp_path):
        from asset_convert.nif.nif_converter import convert_nif
        from pyffi.formats.nif import NifFormat
        if not os.path.exists(self.GHOST_SKEL):
            pytest.skip('ghost assets missing')
        dst = str(tmp_path / 'skeleton.nif')
        convert_nif(self.GHOST_SKEL, dst, creature=True)
        d = NifFormat.Data()
        with open(dst, 'rb') as f:
            d.read(f)
        return d

    def test_authored_color_survives(self, tmp_path):
        """The source NiColorData pale green (0.70, 0.83, 0.75) must survive.

        The converter used to overwrite every system with a fire palette.
        """
        d = self._convert(tmp_path)
        checked = 0
        for b in d.roots[0].tree():
            if b.__class__.__name__ != 'NiParticleSystem':
                continue
            for m in b.modifiers:
                if m is None or m.__class__.__name__ != \
                        'BSPSysSimpleColorModifier':
                    continue
                c = m.colors[0]
                assert c.g >= c.r and c.g >= c.b, (c.r, c.g, c.b)
                assert abs(c.g - 0.83) < 0.05, c.g
                checked += 1
            if checked:
                break
        assert checked, 'no color modifier found'

    def test_shader_tint_is_neutral_when_particles_are_colored(self,
                                                                tmp_path):
        """Emissive color must stay bright when the particles carry the color.

        BSEffectShaderProperty.emissive_color MULTIPLIES the texture, so the
        source's near-black (0.04) NiMaterialProperty made the smoke black.
        """
        d = self._convert(tmp_path)
        checked = 0
        for b in d.roots[0].tree():
            if b.__class__.__name__ != 'NiParticleSystem':
                continue
            for p in getattr(b, 'bs_properties', []):
                if p is None or p.__class__.__name__ != \
                        'BSEffectShaderProperty':
                    continue
                ec = p.emissive_color
                assert min(ec.r, ec.g, ec.b) > 0.5, (ec.r, ec.g, ec.b)
                checked += 1
            if checked:
                break
        assert checked, 'no effect shader found'


class TestPileCollision:
    """The pile has to be clickable: it needs vanilla's ash-pile PHANTOM.

    A fixed bhkRigidBodyT on layer 15 shipped first with the box measured
    correct on the pile geometry, and the pile was still unselectable in
    game (2026-08-26) — the crosshair pick only sees the phantom.  Every
    vanilla ash pile is Box01 -> bhkSPCollisionObject ->
    bhkSimpleShapePhantom(layer 15) -> bhkTransformShape -> bhkBoxShape."""

    def _pile(self, folder, tmp_path):
        from asset_convert.havok.creature_mesh import extract_death_pile
        from asset_convert.nif.nif_converter import convert_nif
        from asset_convert.havok.hkx_behavior import detect_dissolve
        from pyffi.formats.nif import NifFormat
        skel = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                            'creatures', folder, 'skeleton.nif')
        kf = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                          'creatures', folder, 'death.kf')
        if not (os.path.exists(skel) and os.path.exists(kf)):
            pytest.skip(f'{folder} assets missing')
        info = detect_dissolve({'death': (decode_kf(kf)[0], None)})
        raw = str(tmp_path / 'raw.nif')
        out = str(tmp_path / 'pile.nif')
        if not extract_death_pile(skel, raw, reveal_holders=info['reveals'],
                                  holder_offsets=info['offsets']):
            return None
        convert_nif(raw, out, creature=True)
        if not os.path.exists(out):
            return None
        d = NifFormat.Data()
        with open(out, 'rb') as f:
            d.read(f)
        return d

    def test_pile_has_collision(self, tmp_path):
        d = self._pile('ghost', tmp_path)
        assert d is not None
        names = {b.__class__.__name__ for b in d.blocks}
        assert any(n.startswith('bhk') for n in names), sorted(names)

    def test_pile_collision_is_the_vanilla_phantom(self, tmp_path):
        """Pile collision is a phantom whose transform centers the box on the
        geometry (z ~10 game units)."""
        d = self._pile('ghost', tmp_path)
        assert d is not None
        names = [b.__class__.__name__ for b in d.blocks]
        # a rigid body is exactly what did NOT work in game
        assert 'bhkRigidBody' not in names and 'bhkRigidBodyT' not in names
        phantoms = [b for b in d.blocks
                    if b.__class__.__name__ == 'bhkSimpleShapePhantom']
        assert phantoms, names
        ph = phantoms[0]
        assert int(ph.havok_col_filter.layer) == 15, ph.havok_col_filter.layer
        xf = ph.shape
        assert xf.__class__.__name__ == 'bhkTransformShape', xf
        box = xf.shape
        assert box.__class__.__name__ == 'bhkBoxShape', box
        # box covers the full pile: the ghost geometry is ~21x21x3.5 game
        # units, so half-extents in Skyrim havok units (x69.99) are ~0.15
        # in X/Y with the 8-unit Z floor (~0.114)
        assert 0.10 < float(box.dimensions.x) < 0.25, box.dimensions.x
        assert 0.10 < float(box.dimensions.y) < 0.25, box.dimensions.y
        assert 0.08 < float(box.dimensions.z) < 0.25, box.dimensions.z
        assert 0.05 < float(xf.transform.m_34) < 0.30, xf.transform.m_34
        spco = [b for b in d.blocks
                if b.__class__.__name__ == 'bhkSPCollisionObject']
        assert spco and int(spco[0].flags) == 129

    def test_pile_declares_havok_in_bsx(self, tmp_path):
        d = self._pile('ghost', tmp_path)
        assert d is not None
        bsx = [b for b in d.blocks if b.__class__.__name__ == 'BSXFlags']
        assert bsx, 'no BSXFlags'
        assert int(bsx[0].integer_data) & 2, bsx[0].integer_data


# ---------------------------------------------------------------------------
# Swim-prefixed equip clips, the water-native promotion, and pinning a
# ---------------------------------------------------------------------------

FISH_DIR = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                        'creatures', 'slaughterfish')
needs_fish = pytest.mark.skipif(
    not os.path.exists(os.path.join(
        FISH_DIR, 'swimhandtohandattackequip.kf')),
    reason='Oblivion slaughterfish export assets missing')


@needs_fish
class TestSwimEquipClips:
    """A water creature spells its whole clip set with a `swim` prefix.

    The slaughterfish was the ONLY creature in a 235-folder census with no
    equip stance at all: EQUIP_STANCES listed no swim spellings, so the equip
    claim missed and the attack sweep stole the clips on its bare
    `'attack' in name` test -- filing Equip and Unequip as two of its five
    attacks.  Their own NiControllerSequence names are literally 'Equip' and
    'Unequip', which is the authored proof they are not attacks.
    """

    def _clips(self):
        from asset_convert.havok.hkx_behavior import classify_clips
        return classify_clips(FISH_DIR)

    def test_equip_stance_is_claimed(self):
        eq = self._clips()['equip']
        assert 'H2H' in eq, eq
        equip, unequip = eq['H2H']
        assert os.path.basename(equip) == 'swimhandtohandattackequip.kf'
        assert os.path.basename(unequip) == 'swimhandtohandattackunequip.kf'

    def test_equip_clips_are_not_attacks(self):
        atks = [os.path.basename(a) for a in self._clips()['attacks']]
        assert not [a for a in atks if 'equip' in a], atks

    def test_only_the_real_attacks_remain(self):
        atks = sorted(os.path.basename(a) for a in self._clips()['attacks'])
        assert atks == ['swimhandtohandattackleftpower.kf',
                        'swimhandtohandattackpower.kf',
                        'swimhandtohandattackrightpower.kf'], atks

    def test_sequence_names_confirm_the_classification(self):
        # the authored indicator: the clips say what they are
        for stem, seq in (('swimhandtohandattackequip', 'Equip'),
                          ('swimhandtohandattackunequip', 'Unequip')):
            c = decode_kf(os.path.join(FISH_DIR, stem + '.kf'))
            c = c[0] if isinstance(c, list) else c
            assert c.name == seq, (stem, c.name)


class TestCastPin:
    """A caster must be PINNED while the full-body cast plays.

    An Oblivion cast .kf has no root motion, so with the AI still commanding
    ground velocity the actor slides on planted feet (scamp, in game
    2026-08-26).  The pin is bAnimationDriven held over the FireForget
    chain — vanilla chaurusbehavior verbatim (Casting_SpitAttack_MG wraps
    its cast clip in bAnimationDriven_IsActive, pointer-traced).

    An all-zero `Rooted` MOVT switched by `iState = cond((IsCasting==1),..)`
    at the root was tried first and BROKE casting entirely in game — these
    tests also pin its removal.
    """

    def _cast_bindings(self, xml):
        # variable indices as the emitted graph numbers them
        import re
        from asset_convert.havok.behavior_vocabulary import (
            ENGINE_VARIABLES, MAGIC_VARIABLES)
        names = [n for n, _t, _iv in ENGINE_VARIABLES]
        names += [n for n, _t, _iv in MAGIC_VARIABLES]   # scamp: no block/swim
        binding = ('memberPath">bIsActive{slot}</hkparam>\\s*'
                   '<hkparam name="variableIndex">{idx}<')
        return {
            'IsCasting': re.search(binding.format(
                slot=0, idx=names.index('IsCasting')), xml) is not None,
            'bAnimationDriven': re.search(binding.format(
                slot=1, idx=names.index('bAnimationDriven')), xml)
            is not None,
        }

    @needs_scamp
    def test_cast_chain_holds_banimationdriven(self):
        from asset_convert.havok.hkx_behavior import (build_behavior_xml,
                                                classify_clips,
                                                movement_type_names)
        clips = classify_clips(SCAMP_DIR)
        xml = build_behavior_xml('tes4oblivion_scampbehavior', clips,
                                 movement_types=movement_type_names('scamp'))
        got = self._cast_bindings(xml)
        assert got['IsCasting'], 'IsCasting binding missing from cast chain'
        assert got['bAnimationDriven'], \
            'bAnimationDriven binding missing from cast chain'

    @needs_scamp
    def test_cast_chain_allows_rotation(self):
        # pinned caster must still turn to face (falmer ranged guard)
        import re
        from asset_convert.havok.behavior_vocabulary import ENGINE_VARIABLES
        from asset_convert.havok.hkx_behavior import (build_behavior_xml,
                                                classify_clips,
                                                movement_type_names)
        clips = classify_clips(SCAMP_DIR)
        xml = build_behavior_xml('tes4oblivion_scampbehavior', clips,
                                 movement_types=movement_type_names('scamp'))
        names = [n for n, _t, _iv in ENGINE_VARIABLES]
        pat = ('memberPath">bIsActive2</hkparam>\\s*'
               f'<hkparam name="variableIndex">{names.index("bAllowRotation")}<')
        assert re.search(pat, xml), 'bAllowRotation binding missing'

    @needs_scamp
    def test_begincast_is_level_triggered(self):
        # BeginCastLeft -> LeftHandSpellCastHandler is idempotent (acts only
        # in caster state 1); an edge-triggered expression parked a live
        # scamp in state 1 for minutes with the condition already true
        from asset_convert.havok.hkx_behavior import (build_behavior_xml,
                                                classify_clips,
                                                movement_type_names)
        clips = classify_clips(SCAMP_DIR)
        xml = build_behavior_xml('tes4oblivion_scampbehavior', clips,
                                 movement_types=movement_type_names('scamp'))
        i = xml.index('BeginCastLeft if (bWantCastLeft')
        blk = xml[i:i + 400]
        assert 'EVENT_MODE_SEND_ON_TRUE' in blk, blk
        assert 'SEND_ON_FALSE_TO_TRUE' not in blk

    @needs_scamp
    def test_rooted_movt_stays_dead(self):
        from asset_convert.havok.hkx_behavior import (build_behavior_xml,
                                                classify_clips,
                                                movement_type_names)
        mts = movement_type_names('scamp')
        assert mts == ['TES4scampDefault', 'TES4scampRun'], mts
        clips = classify_clips(SCAMP_DIR)
        xml = build_behavior_xml('tes4oblivion_scampbehavior', clips,
                                 movement_types=mts)
        assert 'Rooted' not in xml
        assert 'cond((IsCasting' not in xml


@needs_scamp
class TestDirectionBlend:
    """Strafing is a Direction blend, never an event-entered state.

    No vanilla graph has a moveLeft/moveRight event; the engine writes the
    `Direction` variable and vanilla (slaughterfish/chaurus DirectionalBlend,
    flags 48 PARAMETRIC|CYCLIC) blends the gait clips on it.  As states the
    strafe clips never played and the scamp slid sideways on the forward
    clip (in game 2026-08-26).
    """

    def _xml(self):
        from asset_convert.havok.hkx_behavior import (build_behavior_xml,
                                                classify_clips,
                                                movement_type_names)
        clips = classify_clips(SCAMP_DIR)
        assert 'StrafeLeft' in clips['locomotion']
        assert 'StrafeRight' in clips['locomotion']
        # the scamp's measured root-motion speeds (creature_projects.json)
        speeds = {'walk': 92.8, 'run': 336.4, 'back': 99.0,
                  'left': 31.8, 'right': 32.1}
        return build_behavior_xml('tes4oblivion_scampbehavior', clips,
                                  movement_types=movement_type_names('scamp'),
                                  speeds=speeds)

    def test_no_strafe_states_or_events(self):
        xml = self._xml()
        assert 'StrafeLeftLocomotionState' not in xml
        assert 'moveLeft' not in xml and 'moveRight' not in xml

    def test_direction_blend_anchors(self):
        import re
        from asset_convert.havok.behavior_vocabulary import ENGINE_VARIABLES
        xml = self._xml()
        # one direction blend per gait family (scamp has walk + run)
        for fam in ('Walk', 'Run'):
            assert f'{fam}DirectionalBlend' in xml, fam
            i = xml.index(f'<hkparam name="name">{fam}DirectionalBlend'
                          '</hkparam>')
            blk = xml[i:i + 1200]
            # flags 49 = SYNC|PARAMETRIC|CYCLIC (chaurus/draugr verbatim)
            assert '<hkparam name="flags">49</hkparam>' in blk, blk
        # every strafe child is a SPEED blend (slow creep + natural rate)
        for nm in ('WalkStrafeRightBlend', 'WalkStrafeLeftBlend',
                   'WalkBackwardDirBlend', 'RunStrafeRightBlend'):
            assert f'<hkparam name="name">{nm}</hkparam>' in xml, nm
        assert 'MoveBackwardDir' not in xml.replace('BackwardDirBlend', '')\
            .replace('BackwardDirSlow', '').replace('BackwardDir<', '<')
        names = [n for n, _t, _iv in ENGINE_VARIABLES]
        assert re.search('memberPath">blendParameter</hkparam>\\s*'
                         f'<hkparam name="variableIndex">'
                         f'{names.index("Direction")}<', xml)
        # the fish's anchors: right 0.25, back 0.5, left 0.75
        for w in ('0.250000', '0.500000', '0.750000'):
            assert f'<hkparam name="weight">{w}</hkparam>' in xml, w


@needs_fish
class TestWaterNativePromotion:
    """A creature whose only gait is swimming gets the vanilla slaughterfish
    structure: the swim set IS the base locomotion, no SwimState sibling.

    Bolted onto a land graph as a SwimState, the fish parked there forever
    (the engine sends swimStart at spawn) while the attack transitions —
    local to DefaultState — were unreachable: it chased and never attacked
    (in game 2026-08-26).
    """

    def _clips(self):
        from asset_convert.havok.hkx_behavior import classify_clips
        return classify_clips(FISH_DIR)

    def test_swim_clips_are_the_base_locomotion(self):
        c = self._clips()
        loco = {st: os.path.basename(p) for st, p in c['locomotion'].items()}
        assert loco['MoveForward'] == 'swimforward.kf', loco
        assert loco['TurnLeft'] == 'swimturnleft.kf', loco
        assert loco['TurnRight'] == 'swimturnright.kf', loco
        assert os.path.basename(c['run']) == 'swimfastforward.kf'
        assert os.path.basename(c['idle']) == 'swimidle.kf'
        assert os.path.basename(c['combat_idle']) == 'swimhandtohandidle.kf'
        assert c['swim'] == {}, c['swim']

    def test_amphibians_keep_the_split(self):
        # the mudcrab walks AND swims -- it must keep the land graph
        from asset_convert.havok.hkx_behavior import classify_clips
        crab = os.path.join(os.path.dirname(FISH_DIR), 'mudcrab')
        if not os.path.isdir(crab):
            pytest.skip('mudcrab export assets missing')
        c = classify_clips(crab)
        assert c['swim'].get('forward'), 'mudcrab lost its swim set'
        assert os.path.basename(
            c['locomotion']['MoveForward']) == 'forward.kf'

    def test_attacks_reachable_from_default_state(self):
        # the promoted fish keeps DefaultState active (no SwimState), so
        # the DefaultState-local attackStart transitions can actually fire
        from asset_convert.havok.hkx_behavior import (build_behavior_xml,
                                                movement_type_names)
        c = self._clips()
        xml = build_behavior_xml('tes4oblivion_slaughterfishbehavior', c,
                                 movement_types=movement_type_names(
                                     'slaughterfish'))
        assert 'SwimState' not in xml
        assert 'Attack_swimhandtohandattackpowerState' in xml


# ---------------------------------------------------------------------------
# AnimGroup locomotion fallback (the nix hound slide)
# ---------------------------------------------------------------------------

NIXHOUND_DIR = os.path.join(REPO, 'export', 'Morrowind_ob.esm', 'meshes',
                            'creatures', 'nixhound')
ASHSLAVE_DIR = os.path.join(REPO, 'export', 'Morrowind_ob.esm', 'meshes',
                            'morroblivion', 'creatures', 'sixthhouse',
                            'ashslave')
MURK_DIR = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                        'creatures', 'murkdweller')

needs_mw = pytest.mark.skipif(not os.path.isdir(NIXHOUND_DIR),
                              reason='Morrowind_ob export assets missing')


class TestAnimGroupFallback:
    """A gait clip is bound by its AnimGroup, not its filename.

    Morrowind_ob spells the nix hound's gaits walkforward.kf /
    walkfastforward.kf, which no stem table matches; every one fell into the
    dead 'extra' bucket, the graph got no MoveForward state, and the engine
    translated the actor with the idle pose playing (it slid).
    """

    def test_read_animgroup_is_the_sequence_name(self):
        from asset_convert.havok.behavior_clips import read_animgroup
        if not os.path.isdir(NIXHOUND_DIR):
            pytest.skip('Morrowind_ob export assets missing')
        assert read_animgroup(
            os.path.join(NIXHOUND_DIR, 'walkforward.kf')) == 'Forward'
        assert read_animgroup(
            os.path.join(NIXHOUND_DIR, 'walkfastforward.kf')) == 'FastForward'

    def test_read_animgroup_rejects_non_nif(self):
        from asset_convert.havok.behavior_clips import read_animgroup
        assert read_animgroup(__file__) is None
        assert read_animgroup(
            os.path.join(REPO, 'no', 'such', 'file.kf')) is None

    @needs_mw
    def test_nixhound_gets_a_forward_state(self):
        from asset_convert.havok.hkx_behavior import classify_clips
        c = classify_clips(NIXHOUND_DIR)
        fwd = c['locomotion'].get('MoveForward')
        assert fwd, 'nix hound has no MoveForward state - it will slide'
        # the BASE gait wins the single MoveForward slot, not the
        # weapon-stance variant (handtohandforward.kf declares `Forward` too)
        assert os.path.basename(fwd) == 'walkforward.kf'
        assert os.path.basename(c['run'] or '') == 'walkfastforward.kf'

    @needs_mw
    def test_ash_slave_gets_a_forward_state(self):
        # same defect, different folder tree (meshes/morroblivion/**)
        from asset_convert.havok.hkx_behavior import classify_clips
        if not os.path.isdir(ASHSLAVE_DIR):
            pytest.skip('Morroblivion sixthhouse assets missing')
        c = classify_clips(ASHSLAVE_DIR)
        assert c['locomotion'].get('MoveForward'), 'ash slave will slide'

    def test_land_run_never_takes_a_swim_clip(self):
        # swimhandtohandfastforward.kf declares AnimGroup 'FastForward' too;
        # the murkdweller ships a full land set AND a full swim set, so the
        # land slots must not be filled from swim-prefixed clips.
        from asset_convert.havok.hkx_behavior import classify_clips
        if not os.path.isdir(MURK_DIR):
            pytest.skip('murkdweller export assets missing')
        c = classify_clips(MURK_DIR)
        for slot in ('run', 'run_back'):
            got = c.get(slot)
            assert not got or not os.path.basename(got).startswith('swim'), (
                '%s took a swim clip: %s' % (slot, got))
        for state, path in c['locomotion'].items():
            assert not os.path.basename(path).startswith('swim'), (
                '%s took a swim clip: %s' % (state, path))
        assert c['swim'].get('forward'), 'murkdweller lost its swim set'

    @needs_assets
    def test_stem_claims_still_win(self):
        # the fallback is additive only: a folder the stem tables already
        # cover must classify exactly as before (dog ships forward.kf)
        from asset_convert.havok.hkx_behavior import classify_clips
        c = classify_clips(DOG_DIR)
        assert os.path.basename(
            c['locomotion']['MoveForward']) == 'forward.kf'


# ---------------------------------------------------------------------------
# Ragdoll bone <-> rigid body bijection, and the engine's ORDERING contract
# ---------------------------------------------------------------------------

ALIT_SKEL = os.path.join(REPO, 'export', 'Morrowind_ob.esm', 'meshes',
                         'creatures', 'alit', 'skeleton.nif')
LANDDREUGH_SKEL = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                               'creatures', 'landdreugh', 'skeleton.nif')
MUDCRAB_SKEL = os.path.join(REPO, 'export', 'Oblivion.esm', 'meshes',
                            'creatures', 'mudcrab', 'skeleton.nif')
needs_alit = pytest.mark.skipif(not os.path.exists(ALIT_SKEL),
                                reason='Morrowind_ob export assets missing')
needs_landdreugh = pytest.mark.skipif(not os.path.exists(LANDDREUGH_SKEL),
                                      reason='Oblivion export assets missing')
needs_mudcrab = pytest.mark.skipif(not os.path.exists(MUDCRAB_SKEL),
                                   reason='Oblivion export assets missing')
TURRET_SKEL = os.path.join(REPO, 'export', 'FalloutNV.esm', 'meshes',
                           'creatures', 'sentryturret', 'skeleton.nif')
needs_turret = pytest.mark.skipif(not os.path.exists(TURRET_SKEL),
                                  reason='FalloutNV export assets missing')


class TestRagdollBijection:

    def _parts(self, skel):
        from asset_convert.havok.hkx_skeleton import load_skeleton_bones
        from asset_convert.havok.hkx_ragdoll import extract_ragdoll
        bones = load_skeleton_bones(skel)
        return bones, extract_ragdoll(skel, bones)

    @pytest.mark.skipif(not os.path.exists(ALIT_SKEL),
                        reason='Morrowind_ob export assets missing')
    def test_alit_duplicate_named_bodies_map_to_distinct_bones(self):
        # alit hangs a CollisionNode/EnableCollisions proxy pair under each
        # of its 12 bones: 24 bodies sharing 2 names.  A name-keyed lookup
        # collapsed all 24 onto anim bones 73/74.
        bones, parts = self._parts(ALIT_SKEL)
        assert parts, 'alit lost its ragdoll'
        idxs = [p.anim_index for p in parts]
        assert len(set(idxs)) == len(idxs), 'ragdoll parts alias an anim bone'
        names = [p.name for p in parts]
        assert len(set(names)) == len(names), 'ragdoll part names collide'
        # every part must land on the bone it is actually named after
        for p in parts:
            assert bones[p.anim_index].name in p.name

    @pytest.mark.skipif(not os.path.exists(ALIT_SKEL),
                        reason='Morrowind_ob export assets missing')
    def test_alit_every_child_part_has_a_constraint(self):
        # a parented part with no constraint would ship null constraint data
        _bones, parts = self._parts(ALIT_SKEL)
        missing = [p.name for p in parts
                   if p.parent >= 0 and p.constraint is None]
        assert not missing, 'parts with a parent but no constraint: %s' % missing

    @needs_assets
    def test_unique_named_rig_keeps_legacy_part_names(self):
        # the uniquifying suffix is additive: a rig that never had duplicates
        # must keep byte-identical part names (no FormID/asset churn)
        bones, parts = self._parts(DOG_SKEL)
        assert parts, 'dog lost its ragdoll'
        assert [p.name for p in parts] == \
            ['Ragdoll_' + bones[p.anim_index].name for p in parts]

    def test_invariant_assert_catches_aliasing(self):
        from asset_convert.havok.hkx_ragdoll import (RagdollPart,
                                               assert_ragdoll_invariants)

        def _part(name, idx, parent, con=('ragdoll', {})):
            p = RagdollPart()
            p.name, p.anim_index, p.parent, p.constraint = name, idx, parent, con
            return p

        assert_ragdoll_invariants([_part('A', 0, -1, None), _part('B', 1, 0)])

        with pytest.raises(ValueError, match='anim bone'):
            assert_ragdoll_invariants([_part('A', 0, -1, None),
                                        _part('B', 0, 0)])
        with pytest.raises(ValueError, match='not unique'):
            assert_ragdoll_invariants([_part('A', 0, -1, None),
                                        _part('A', 1, 0)])
        with pytest.raises(ValueError, match='no constraint'):
            assert_ragdoll_invariants([_part('A', 0, -1, None),
                                        _part('B', 1, 0, None)])

    @needs_alit
    def test_collision_toggle_proxies_are_not_ragdoll_parts(self):
        # alit is ONE creature with 12 limbs.  Each bone also carries a
        # 'CollisionNode' child holding a 95%-scale duplicate of the bone's
        # own capsule at mass 1e-4, plus an 'EnableCollisions' grandchild of
        # radius 0.0: Oblivion collision-toggle proxies, not limbs.
        _bones, parts = self._parts(ALIT_SKEL)
        assert parts
        assert len(parts) == 12, 'expected 12 real limbs, got %d parts' % len(parts)

    # -- the engine's ordering contract ------------------------------------

    def _dfs_bodies(self, skel):
        from asset_convert.havok.hkx_ragdoll import plan_ragdoll_tree, decode_name
        from asset_convert.havok.hkx_skeleton import BONE_RENAMES
        from pyffi.formats.nif import NifFormat
        d = NifFormat.Data()
        with open(skel, 'rb') as f:
            d.read(f)
        plan = plan_ragdoll_tree(d)
        names = [decode_name(n) for n in plan['body_nodes']]
        return plan, ['Ragdoll_' + BONE_RENAMES.get(n, n) for n in names]

    @needs_alit
    def test_alit_parts_follow_nif_dfs_order_root_first(self):
        # Spine and Neck constrain EACH OTHER and nothing constrains the
        # first body (Bip01 NonAccum).  The old cycle-breaker rooted the tree
        # at Spine, which the engine then matched to NIF body 1 while NIF
        # body 0 landed on hkx index 1 -> NIF constraint[-1] -> crash.
        _bones, parts = self._parts(ALIT_SKEL)
        _plan, dfs = self._dfs_bodies(ALIT_SKEL)
        assert [p.name for p in parts] == dfs
        assert parts[0].name == 'Ragdoll_Bip01 NonAccum'
        assert parts[0].parent == -1 and parts[0].constraint is None
        for i, p in enumerate(parts[1:], 1):
            assert p.parent < i, '%s parent %d not before %d' % (p.name, p.parent, i)
            assert p.constraint is not None
        # the duplicate Spine->Neck joint is dropped; Neck keeps its own
        spine = next(p for p in parts if p.name == 'Ragdoll_BBip01 Spine')
        assert parts[spine.parent].name == 'Ragdoll_Bip01 NonAccum'

    @needs_landdreugh
    def test_forward_authored_joint_is_reversed_not_dropped(self):
        # landdreugh's FIRST body (Pelvis) holds a joint to Spine01, a LATER
        # body.  The root cannot have a parent, so the same joint is given to
        # Spine01 with its ends exchanged -- an authored joint, not a
        # synthetic one.
        _bones, parts = self._parts(LANDDREUGH_SKEL)
        plan, dfs = self._dfs_bodies(LANDDREUGH_SKEL)
        assert [p.name for p in parts] == dfs
        assert parts[0].name == 'Ragdoll_Bip01 Pelvis'
        spine = next(p for p in parts if p.name == 'Ragdoll_Bip01 Spine01')
        assert parts[spine.parent].name == 'Ragdoll_Bip01 Pelvis'
        rev = [r for (_con, r) in plan['edge_con'].values() if r]
        assert len(rev) == 1
        assert not plan['synthetic']

    @needs_assets
    def test_dog_order_and_root_unchanged(self):
        _bones, parts = self._parts(DOG_SKEL)
        _plan, dfs = self._dfs_bodies(DOG_SKEL)
        assert [p.name for p in parts] == dfs
        for i, p in enumerate(parts[1:], 1):
            assert p.parent < i

    def test_swap_joint_ends_negates_limits_and_swaps_frames(self):
        from asset_convert.havok.hkx_ragdoll import swap_joint_ends
        info = {'rows_a': 'A', 'rows_b': 'B', 'piv_a': 'pa', 'piv_b': 'pb',
                'cone': 0.5, 'plane_min': -0.2, 'plane_max': 0.7,
                'twist_min': -0.1, 'twist_max': 0.3, 'friction': 0.0}
        out = swap_joint_ends('ragdoll', info)
        assert (out['rows_a'], out['rows_b']) == ('B', 'A')
        assert (out['piv_a'], out['piv_b']) == ('pb', 'pa')
        assert (out['plane_min'], out['plane_max']) == (-0.7, 0.2)
        assert (out['twist_min'], out['twist_max']) == (-0.3, 0.1)
        assert out['cone'] == 0.5
        h = swap_joint_ends('hinge', {'rows_a': 'A', 'rows_b': 'B',
                                       'piv_a': 1, 'piv_b': 2,
                                       'min': -0.8, 'max': 0.1, 'friction': 0})
        assert (h['min'], h['max']) == (-0.1, 0.8)
        # involution: swapping twice is the identity
        assert swap_joint_ends('ragdoll', out) == info

    @needs_alit
    def test_nif_side_lists_match_the_plan(self):
        """Every body's list is exactly its planned joint.

        First body bare, every other body ONE joint to an earlier body --
        the list the engine indexes.
        """
        from asset_convert.collision.collision_constraints import (
            enforce_ragdoll_tree)
        from asset_convert.havok.hkx_ragdoll import plan_ragdoll_tree, decode_name
        from pyffi.formats.nif import NifFormat
        d = NifFormat.Data()
        with open(ALIT_SKEL, 'rb') as f:
            d.read(f)
        root = d.roots[0]
        assert enforce_ragdoll_tree(d, root) > 0
        plan = plan_ragdoll_tree(d)
        bodies = plan['body_nodes']
        assert len(bodies[0].collision_object.body.constraints) == 0
        for n in bodies[1:]:
            cons = list(n.collision_object.body.constraints)
            assert len(cons) == 1, decode_name(n)
            ents = list(cons[0].entities)
            assert ents[0] is n.collision_object.body
            parent = next(b for b in bodies if b.collision_object.body is ents[1])
            assert bodies.index(parent) < bodies.index(n)
        spine = next(n for n in bodies if decode_name(n) == 'BBip01 Spine')
        con = spine.collision_object.body.constraints[0]
        assert con.__class__.__name__ == 'bhkRagdollConstraint'
        assert decode_name(bodies[0]) == 'Bip01 NonAccum'
        assert con.entities[1] is bodies[0].collision_object.body
        # idempotent: a second pass changes nothing
        assert enforce_ragdoll_tree(d, root) == 0

    @needs_mudcrab
    def test_nif_side_drops_second_constraints(self):
        """The mudcrab's three bodies with TWO constraints each are trimmed.

        An extra constraint shifts every later slot the engine indexes.
        """
        from asset_convert.collision.collision_constraints import (
            enforce_ragdoll_tree)
        from asset_convert.havok.hkx_ragdoll import plan_ragdoll_tree
        from pyffi.formats.nif import NifFormat
        d = NifFormat.Data()
        with open(MUDCRAB_SKEL, 'rb') as f:
            d.read(f)
        plan = plan_ragdoll_tree(d)
        doubled = [n for n in plan['body_nodes']
                   if len(n.collision_object.body.constraints) > 1]
        assert doubled, 'fixture changed: mudcrab no longer authors doubles'
        enforce_ragdoll_tree(d, d.roots[0])
        counts = [len(n.collision_object.body.constraints)
                  for n in plan['body_nodes']]
        assert counts == [0] + [1] * (len(counts) - 1)

    @needs_turret
    def test_plain_hinge_is_promoted_to_limited_hinge(self):
        """No bhkHingeConstraint may survive in a ragdoll tree.

        hkpConstraintUtils::convertToPowered accepts only limited hinge and
        ragdoll data and returns NULL for anything else, which the engine's
        ragdoll attach then dereferences (the FalloutNV sentry turret CTD).
        The promoted joint must span the full circle a free hinge means.
        """
        from asset_convert.collision.collision_constraints import (
            enforce_ragdoll_tree)
        from asset_convert.havok.hkx_ragdoll import plan_ragdoll_tree
        from pyffi.formats.nif import NifFormat
        d = NifFormat.Data()
        with open(TURRET_SKEL, 'rb') as f:
            d.read(f)
        plan = plan_ragdoll_tree(d)
        authored = [n for n in plan['body_nodes']
                    for c in n.collision_object.body.constraints
                    if c.__class__.__name__ == 'bhkHingeConstraint']
        assert authored, 'fixture changed: turret no longer authors a plain hinge'

        enforce_ragdoll_tree(d, d.roots[0])

        kinds = [c.__class__.__name__ for n in plan['body_nodes']
                 for c in n.collision_object.body.constraints]
        assert 'bhkHingeConstraint' not in kinds
        assert set(kinds) <= {'bhkLimitedHingeConstraint',
                              'bhkRagdollConstraint'}
        promoted = [c for n in plan['body_nodes']
                    for c in n.collision_object.body.constraints
                    if c.__class__.__name__ == 'bhkLimitedHingeConstraint']
        spans = [(c.limited_hinge.min_angle, c.limited_hinge.max_angle)
                 for c in promoted]
        assert any(lo <= -np.pi + 1e-4 and hi >= np.pi - 1e-4
                   for lo, hi in spans)

    @needs_landdreugh
    def test_nif_side_reverses_a_forward_joint_in_place(self):
        from asset_convert.collision.collision_constraints import (
            enforce_ragdoll_tree, joint_descriptor, reverse_constraint_ends)
        from asset_convert.havok.hkx_ragdoll import plan_ragdoll_tree, decode_name
        from pyffi.formats.nif import NifFormat
        d = NifFormat.Data()
        with open(LANDDREUGH_SKEL, 'rb') as f:
            d.read(f)
        plan = plan_ragdoll_tree(d)
        pelvis, spine = plan['body_nodes'][0], next(
            n for n in plan['body_nodes'] if decode_name(n) == 'Bip01 Spine01')
        con = pelvis.collision_object.body.constraints[0]
        kind, rd = joint_descriptor(con)       # the source wraps it malleable
        lo, hi = (('twist_min_angle', 'twist_max_angle') if kind == 'ragdoll'
                  else ('min_angle', 'max_angle'))
        before = [(v.x, v.y, v.z) for v in (rd.pivot_a, rd.pivot_b)]
        limits = (float(getattr(rd, lo)), float(getattr(rd, hi)))
        enforce_ragdoll_tree(d, d.roots[0])
        assert len(pelvis.collision_object.body.constraints) == 0
        assert list(spine.collision_object.body.constraints) == [con]
        assert con.entities[0] is spine.collision_object.body
        assert con.entities[1] is pelvis.collision_object.body
        after = [(v.x, v.y, v.z) for v in (rd.pivot_a, rd.pivot_b)]
        assert after == before[::-1]
        assert (float(getattr(rd, lo)),
                float(getattr(rd, hi))) == (-limits[1], -limits[0])
        # involution
        reverse_constraint_ends(con)
        assert [(v.x, v.y, v.z) for v in (rd.pivot_a, rd.pivot_b)] == before

    @needs_assets
    def test_marker_exclusion_leaves_normal_rigs_alone(self):
        # the predicate keys on physical content, so a rig with only real
        # capsules must keep every body
        import asset_convert.havok.hkx_ragdoll as R
        from asset_convert.havok.hkx_skeleton import load_skeleton_bones
        bones = load_skeleton_bones(DOG_SKEL)
        real = R.is_marker_body
        try:
            R.is_marker_body = lambda n, b: False
            before = R.extract_ragdoll(DOG_SKEL, bones)
        finally:
            R.is_marker_body = real
        after = R.extract_ragdoll(DOG_SKEL, bones)
        assert len(before) == len(after)


ASHVAMP_DIR = os.path.join(REPO, 'export', 'Morrowind_ob.esm', 'meshes',
                           'morroblivion', 'creatures', 'sixthhouse',
                           'ashvampire')


@needs_assets
class TestAccumBindPoseLeak:
    """The accum bone flattens to its authored frame-0 pose, never identity.

    See: docs/commentary/asset_convert_falloutnv.md#accum-root-identity
    """

    @pytest.mark.parametrize('kf,yaw', [('turnleft.kf', 54.18),
                                        ('stagger.kf', 67.11),
                                        ('idle.kf', 54.18)])
    def test_accum_rotation_flattens_to_first_sample(self, kf, yaw):
        """The accum track ends static at the source's authored `yaw`."""
        from asset_convert.havok.kf_decode import split_root_motion
        path = os.path.join(ASHVAMP_DIR, kf)
        if not os.path.exists(path):
            pytest.skip('ash vampire not exported')
        clip = decode_kf(path)[0]
        pre = {t.bone: np.array(t.rotations[0], copy=True)
               for t in clip.tracks if t.rotations is not None}
        motion = split_root_motion(clip)
        assert motion is not None
        w, x, y, z = pre[motion['bone']]
        got = np.degrees(np.arctan2(2 * (w * z + x * y),
                                    1 - 2 * (y * y + z * z)))
        assert abs(got) == pytest.approx(yaw, abs=0.1)
        for t in clip.tracks:
            if t.bone == motion['bone']:
                assert t.rotations == pytest.approx(
                    np.tile(pre[t.bone], (len(clip.times), 1)))

    def test_accum_translation_is_preserved(self):
        """Flattening the rotation must not disturb the accum height."""
        from asset_convert.havok.kf_decode import split_root_motion
        path = os.path.join(ASHVAMP_DIR, 'turnleft.kf')
        if not os.path.exists(path):
            pytest.skip('ash vampire not exported')
        clip = decode_kf(path)[0]
        pre = {t.bone: np.array(t.translations[0], copy=True)
               for t in clip.tracks if t.translations is not None}
        motion = split_root_motion(clip)
        for t in clip.tracks:
            if t.bone == motion['bone'] and t.translations is not None:
                assert t.translations[0] == pytest.approx(pre[t.bone])


@needs_assets
class TestSpeedBakeFrameFloor:
    """The bake ceiling is a frame floor, and may only ever RAISE the factor.

    See: docs/commentary/asset_convert_creature.md#8-ground-speed-baked-not
    """

    def test_ash_vampire_walk_reaches_its_formula_speed(self):
        """Baked walk speed matches the GMST formula; the old cap clipped
        it to 21.9 u/s of 46.3."""
        from asset_convert.havok.hkx_anim import (MIN_BAKED_FRAMES, decode_clip,
                                            speed_bake_factor)
        path = os.path.join(ASHVAMP_DIR, 'walkforward.kf')
        if not os.path.exists(path):
            pytest.skip('ash vampire not exported')
        clip, motion = decode_clip(path, 30.0)
        end = motion['translations'][-1]
        dur = float(clip.duration)
        natural = float(end[0] ** 2 + end[1] ** 2) ** 0.5 / dur
        formula = 5.0 + (300.0 - 5.0) * 14 / 100.0
        factor = speed_bake_factor((formula, 1.4), clip, motion, 30.0)
        assert factor > 1.4
        assert natural * factor == pytest.approx(formula, rel=0.01)
        assert int(round(dur / factor * 30)) + 1 >= MIN_BAKED_FRAMES

    def test_floor_never_lowers_the_old_cap(self):
        """A clip too short to keep the floor must still get the old cap."""
        from asset_convert.havok.hkx_anim import speed_bake_factor

        class _Clip:
            duration = 0.4

        motion = {'translations': [(0.0, 0.0, 0.0), (0.0, 400.0, 0.0)]}
        for cap in (1.4, 2.0):
            got = speed_bake_factor((10000.0, cap), _Clip(), motion, 30.0)
            assert got == pytest.approx(cap)
