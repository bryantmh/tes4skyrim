"""Tests for Nemesis interoperability (asset_convert/nemesis.py).

The risky part is the SPLITTERS. `animationdatasinglefile.txt` is a single
218k-line database with no per-record delimiters: a block boundary off by one
line silently shifts every project after it onto the wrong data, which the
engine then reads as garbage. So the invariants worth pinning are
split->reassemble identity, strip->merge round-tripping, and above all that a
baseline merge keeps Skyrim's own projects AND adds ours -- exercised against a
synthetic file whose shape mirrors the real one, plus Nemesis's shipped
baseline where the reference copy is present.
"""

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from asset_convert import animation_data as ad  # noqa: E402
from asset_convert import nemesis  # noqa: E402


def _manifest(name, clips=2, files=2):
    """A project_manifest.json shaped like creature_pipeline writes them."""
    return {
        'name': name,
        'project_txt': f'tes4{name}project.txt',
        'project_files': [f'Behaviors\\tes4{name}behavior.hkx',
                          f'Characters\\tes4{name}character.hkx'][:files],
        'anim_dir': f'meshes\\actors\\tes4\\{name}\\animations',
        'attacks': [(f'attackStart_TES4_{name}hit', 'Attack_hit')],
        'clips': [
            {'name': ['Idle', 'MoveForward'][i % 2] + (str(i) if i > 1 else ''),
             'stem': f'clip{i}',
             'anim': f'Animations\\clip{i}.hkx',
             'duration': 1.0 + i,
             'looping': True,
             'end_event': None,
             'sounds': [], 'feet': [(0.3, 'FootFront')], 'hits': []}
            for i in range(clips)],
        'motions': {},
    }


def _vanilla_pair():
    """A minimal but structurally real vanilla base for both singlefiles."""
    ad_lines = [
        '2', 'AProject.txt', 'BProject.txt',
        # AProject: has files, has cache, one clip
        '11', '1', '1', 'Behaviors\\a.hkx', '1',
        'AClip', '0', '1', '0', '0', '0', '',
        # AProject motion block
        '7', '0', '1', '1', '1 0 0 0', '1', '1 0 0 0 1', '',
        # BProject: has files, NO cache
        '4', '1', '1', 'Behaviors\\b.hkx', '0',
    ]
    asd_lines = [
        '1', 'AProjectData\\AProject.txt',
        '1', 'FullCharacter.txt', 'V3', '0', '0',
        '1', 'attackStart_x', '0', '1', 'AClip',
        '0',
    ]
    return ad_lines, asd_lines


def test_split_animationdata_roundtrips():
    lines, _ = _vanilla_pair()
    names, blocks = ad.split_animationdata(lines)
    assert names == ['AProject.txt', 'BProject.txt']
    # the cacheless project must come back with no motion block, not an empty
    # one -- an empty block would be written back as a spurious '0' line
    assert blocks[1][1] is None
    out = [str(len(names))] + names
    for proj, motion in blocks:
        out += [str(len(proj))] + proj
        if motion is not None:
            out += [str(len(motion))] + motion
    assert out == lines


def test_split_animationsetdata_roundtrips():
    _, lines = _vanilla_pair()
    names, blocks = ad.split_animationsetdata(lines)
    out = [str(len(names))] + names
    for b in blocks:
        out += b
    assert out == lines


def test_inject_is_idempotent_and_replaces(tmp_path):
    """A second inject must not duplicate, and must pick up rebuilt clips."""
    ad_lines, asd_lines = _vanilla_pair()
    base = tmp_path / 'meshes'
    base.mkdir()
    nemesis._write(str(base / nemesis.CACHE_FILES[0]), ad_lines)
    nemesis._write(str(base / nemesis.CACHE_FILES[1]), asd_lines)

    m = _manifest('dog', clips=2)
    counts = nemesis.inject_into_cache([m], str(base), log=lambda *a: None)
    assert counts[nemesis.CACHE_FILES[0]] == 3       # 2 vanilla + 1 ours
    assert counts[nemesis.CACHE_FILES[1]] == 2

    # rebuild the creature with an extra clip, then inject again
    m2 = _manifest('dog', clips=3)
    counts = nemesis.inject_into_cache([m2], str(base), log=lambda *a: None)
    assert counts[nemesis.CACHE_FILES[0]] == 3       # replaced, not appended

    got = (base / nemesis.CACHE_FILES[0]).read_text('latin-1').splitlines()
    names, blocks = ad.split_animationdata(got)
    assert names.count('tes4dogproject.txt') == 1
    ours = blocks[names.index('tes4dogproject.txt')][0]
    # the rebuilt project must carry the third clip (clips cycle
    # Idle/MoveForward, so index 2 is 'Idle2')
    assert any(line == 'Idle2' for line in ours)
    # vanilla projects survive untouched
    assert 'AProject.txt' in names and 'BProject.txt' in names


def test_inject_preserves_foreign_projects(tmp_path):
    """Whatever Nemesis added must still be there afterwards."""
    ad_lines, asd_lines = _vanilla_pair()
    base = tmp_path / 'meshes'
    base.mkdir()
    nemesis._write(str(base / nemesis.CACHE_FILES[0]), ad_lines)
    nemesis._write(str(base / nemesis.CACHE_FILES[1]), asd_lines)
    nemesis.inject_into_cache([_manifest('dog')], str(base),
                              log=lambda *a: None)
    got = (base / nemesis.CACHE_FILES[0]).read_text('latin-1').splitlines()
    names, blocks = ad.split_animationdata(got)
    a_block = blocks[names.index('AProject.txt')]
    assert a_block[0] == ['1', '1', 'Behaviors\\a.hkx', '1',
                          'AClip', '0', '1', '0', '0', '0', '']


def test_baseline_keeps_originals_and_adds_ours(tmp_path):
    """The whole point: Skyrim's creatures AND ours, never one at the cost of
    the other. The baseline pair is what Nemesis regenerates FROM, so anything
    dropped here is dropped from the finished game files too."""
    ad_lines, asd_lines = _vanilla_pair()
    nem = tmp_path / 'nemesis' / 'meshes'
    nem.mkdir(parents=True)
    nemesis._write(str(nem / nemesis.BASELINE_FILES[0]), ad_lines)
    nemesis._write(str(nem / nemesis.BASELINE_FILES[1]), asd_lines)
    out = tmp_path / 'ours' / 'meshes'

    counts = nemesis.write_baseline_override(
        [_manifest('dog'), _manifest('wolf')], str(nem), str(out),
        log=lambda *a: None)
    assert counts[nemesis.BASELINE_FILES[0]] == 4       # 2 original + 2 ours
    assert counts[nemesis.BASELINE_FILES[1]] == 3       # 1 original + 2 ours

    got = (out / nemesis.BASELINE_FILES[0]).read_text('latin-1').splitlines()
    names, _blocks = ad.split_animationdata(got)
    assert 'AProject.txt' in names and 'BProject.txt' in names
    assert 'tes4dogproject.txt' in names and 'tes4wolfproject.txt' in names

    # the Nemesis installation must be left exactly as it was -- we only read it
    assert (nem / nemesis.BASELINE_FILES[0]).read_text(
        'latin-1').splitlines() == ad_lines
    assert not (nem / nemesis.CACHE_FILES[0]).exists()


def test_load_manifests_finds_namespaced_layout(tmp_path):
    """Projects moved to actors/tes4/<plugin namespace>/<folder>/.

    A depth-hardcoded scan finds nothing after that move and reports it as
    "no creatures to register", so the loader must not care about depth.
    """
    import json as _json
    meshes = tmp_path / 'meshes'
    flat = meshes / 'actors' / 'tes4' / 'dog'
    nested = meshes / 'actors' / 'tes4' / 'oblivion' / 'wolf'
    for d, m in ((flat, _manifest('dog')),
                 (nested, _manifest('oblivion_wolf'))):
        d.mkdir(parents=True)
        (d / 'project_manifest.json').write_text(_json.dumps(m), 'utf-8')

    got = {m['project_txt'] for m in nemesis.load_manifests(str(meshes))}
    assert got == {'tes4dogproject.txt', 'tes4oblivion_wolfproject.txt'}
    # and the namespaced name is still recognised as one of ours, which is
    # what strip/merge key on
    assert ad.is_generated_project('tes4oblivion_wolfproject.txt')


def _fake_mo2(tmp_path, instances):
    """A `%LOCALAPPDATA%/ModOrganizer` tree. `instances` is
    {name: (base_dir, game_path, [mod names holding a baseline])}."""
    root = tmp_path / 'ModOrganizer'
    for name, (base, game, mods) in instances.items():
        inst = root / name
        inst.mkdir(parents=True)
        # Qt escapes backslashes and wraps paths in @ByteArray(...)
        esc = str(base).replace('\\', '\\\\')
        (inst / 'ModOrganizer.ini').write_text(
            '[General]\n'
            f'gameName=Whatever\n'
            f'gamePath=@ByteArray({str(game).replace(chr(92), chr(92) * 2)})\n'
            '[Settings]\n'
            f'base_directory={esc}\n', 'utf-8')
        for mod in mods:
            meshes = base / 'mods' / mod / 'meshes'
            meshes.mkdir(parents=True)
            ad, asd = _vanilla_pair()
            nemesis._write(str(meshes / nemesis.BASELINE_FILES[0]), ad)
            nemesis._write(str(meshes / nemesis.BASELINE_FILES[1]), asd)
    return root


def test_autodetect_ranks_matching_instance_and_pristine_first(
        tmp_path, monkeypatch):
    """Several MO2 instances is the NORMAL case (SE, VR, Oblivion), and our own
    deployed output also carries a baseline pair. Pick the instance for the game
    being converted, and a pristine copy over one already holding our projects.
    """
    game = tmp_path / 'games' / 'SkyrimSE'
    other_game = tmp_path / 'games' / 'SkyrimVR'
    (game / 'Data').mkdir(parents=True)
    other_game.mkdir(parents=True)
    right = tmp_path / 'right'
    wrong = tmp_path / 'wrong'
    root = _fake_mo2(tmp_path, {
        'SE': (right, game, ['Nemesis Unlimited Behavior Engine', 'OurOutput']),
        'VR': (wrong, other_game, ['Nemesis Unlimited Behavior Engine']),
    })
    # make OurOutput look like ours by merging our projects into it
    nemesis.write_baseline_override(
        [_manifest('dog')],
        str(right / 'mods' / 'OurOutput'),
        str(right / 'mods' / 'OurOutput' / 'meshes'),
        log=lambda *a: None)

    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    got = nemesis.autodetect(str(game / 'Data'))
    paths = [p for p, _s, _t, _o in got]
    assert len(paths) == 3

    # the matching game's pristine Nemesis wins
    assert paths[0] == str(right / 'mods' / 'Nemesis Unlimited Behavior Engine'
                           / 'meshes')
    # our own output is demoted below the pristine copy of the same instance
    assert got[1][3] > 0
    # and the other game's instance comes last
    assert paths[-1].startswith(str(wrong))


def test_autodetect_survives_a_missing_mo2(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'nope'))
    assert nemesis.mo2_instances() == []
    assert nemesis.autodetect(None) == []


def test_baseline_dir_accepts_mod_root_or_meshes(tmp_path):
    """A folder picker lands on the MOD folder, so `meshes` is resolved here.

    Requiring the user to drill into `meshes` is knowledge nobody should need,
    and picking the mod root is the natural action -- it is the folder with the
    recognisable name.
    """
    root = tmp_path / 'Nemesis Unlimited Behavior Engine'
    meshes = root / 'meshes'
    meshes.mkdir(parents=True)
    ad_lines, asd_lines = _vanilla_pair()
    nemesis._write(str(meshes / nemesis.BASELINE_FILES[0]), ad_lines)
    nemesis._write(str(meshes / nemesis.BASELINE_FILES[1]), asd_lines)

    assert nemesis.baseline_dir(str(root)) == str(meshes)
    assert nemesis.baseline_dir(str(meshes)) == str(meshes)
    assert nemesis.baseline_dir(str(tmp_path)) is None
    assert nemesis.baseline_dir('') is None

    # and the merge itself takes either form
    out = tmp_path / 'ours' / 'meshes'
    counts = nemesis.write_baseline_override([_manifest('dog')], str(root),
                                             str(out), log=lambda *a: None)
    assert counts[nemesis.BASELINE_FILES[0]] == 3


def test_baseline_rejects_a_folder_without_the_pair(tmp_path):
    with pytest.raises(FileNotFoundError):
        nemesis.write_baseline_override([_manifest('dog')], str(tmp_path),
                                        str(tmp_path / 'out'),
                                        log=lambda *a: None)


def test_baseline_rerun_replaces_not_duplicates(tmp_path):
    """Re-running against the same pristine baseline must be stable."""
    ad_lines, asd_lines = _vanilla_pair()
    nem = tmp_path / 'nemesis' / 'meshes'
    nem.mkdir(parents=True)
    nemesis._write(str(nem / nemesis.BASELINE_FILES[0]), ad_lines)
    nemesis._write(str(nem / nemesis.BASELINE_FILES[1]), asd_lines)
    out = tmp_path / 'ours' / 'meshes'
    for _ in range(2):
        counts = nemesis.write_baseline_override(
            [_manifest('dog')], str(nem), str(out), log=lambda *a: None)
    assert counts[nemesis.BASELINE_FILES[0]] == 3
    got = (out / nemesis.BASELINE_FILES[0]).read_text('latin-1').splitlines()
    names, _ = ad.split_animationdata(got)
    assert names.count('tes4dogproject.txt') == 1


_REAL_NEMESIS = os.path.join(
    REPO, 'references', 'Nemesis Unlimited Behavior Engine', 'meshes')


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(_REAL_NEMESIS,
                                    'nemesis_animationdatasinglefile.txt')),
    reason='needs the Nemesis reference copy')
def test_real_nemesis_baseline_parses_and_merges(tmp_path):
    """Against Nemesis's SHIPPED baseline, not a synthetic stand-in.

    references/ is comparison material, so this is a test fixture only -- the
    pipeline resolves the baseline from the user's own install.
    """
    for fn, split in ((nemesis.BASELINE_FILES[0], ad.split_animationdata),
                      (nemesis.BASELINE_FILES[1], ad.split_animationsetdata)):
        lines = open(os.path.join(_REAL_NEMESIS, fn),
                     encoding='latin-1').read().splitlines()
        names, blocks = split(lines)
        assert len(names) == int(lines[0])
        assert not any(ad.is_generated_project(n) for n in names)

    before = open(os.path.join(_REAL_NEMESIS, nemesis.BASELINE_FILES[0]),
                  encoding='latin-1').read().splitlines()
    orig_names, _ = ad.split_animationdata(before)
    out = tmp_path / 'meshes'
    ours = [_manifest('dog'), _manifest('wolf')]
    counts = nemesis.write_baseline_override(ours, _REAL_NEMESIS, str(out),
                                             log=lambda *a: None)
    assert counts[nemesis.BASELINE_FILES[0]] == len(orig_names) + len(ours)
    got = open(out / nemesis.BASELINE_FILES[0],
               encoding='latin-1').read().splitlines()
    new_names, _ = ad.split_animationdata(got)
    # every one of Skyrim's own projects must still be there, in order
    assert new_names[:len(orig_names)] == orig_names


@pytest.mark.skipif(
    not os.path.isdir(os.path.join(REPO, 'output', 'Oblivion.esm', 'meshes')),
    reason='needs a converted output tree')
def test_real_singlefile_splits_and_strips():
    """The invariant that matters on the real 218k-line database."""
    meshes = os.path.join(REPO, 'output', 'Oblivion.esm', 'meshes')
    path = os.path.join(meshes, nemesis.CACHE_FILES[0])
    lines = open(path, encoding='latin-1').read().splitlines()
    names, blocks = ad.split_animationdata(lines)
    out = [str(len(names))] + names
    for proj, motion in blocks:
        out += [str(len(proj))] + proj
        if motion is not None:
            out += [str(len(motion))] + motion
    assert out == lines
    stripped = ad.strip_generated_animationdata(lines)
    assert int(stripped[0]) < int(lines[0])
    assert not any(ad.is_generated_project(x) for x in
                   stripped[1:1 + int(stripped[0])])
