"""Shared navmesh geometry cache: hashing, gating and the publish contract.

The cache is shipped as a GitHub Release asset (tools/navmesh/navmesh_cache.py), so
three properties have to hold or downloaders silently lose the benefit -- or
worse, get stale geometry:

  * the cache tag must be MACHINE-INDEPENDENT (no mtime, no absolute paths),
  * one changed mesh must invalidate only the cells that place it,
  * the pre-push gate must fire for every file that can change cached geometry.
"""
import glob
import json
import os
import pickle
import sys
import zipfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asset_convert.collision_extract as ce  # noqa: E402
from tes5_import import import_main as im  # noqa: E402
from tes5_import.pgrd_to_navm import _geom_hash  # noqa: E402
from tools.navmesh import navmesh_cache as nc  # noqa: E402
from tools.navmesh import navmesh_cache_hook as hook  # noqa: E402
from tools.navmesh import navmesh_adopt as adopt
from tes5_import import navm_verify
from tes5_import.pgrd_to_navm import geom_equal, geom_quantize


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fake_collision(monkeypatch, table):
    monkeypatch.setattr(ce, '_COLLISION', table)
    monkeypatch.setattr(ce, '_DIGESTS', {})


def _soup(seed, n=4):
    rng = np.arange(n * 9, dtype=np.float32) + float(seed)
    return {'w': rng.copy(), 'b': (rng * 2).copy()}


def _refr(fid):
    return {'NAME': '%06X' % fid, 'PosX': '1', 'PosY': '2', 'PosZ': '3',
            'RotX': '0', 'RotY': '0', 'RotZ': '0'}


HASH_ARGS = dict(tag='T', points=[(0, 0)], edges=[], doors=[], land_rec=None,
                 origin_x=0, origin_y=0)


# ---------------------------------------------------------------------------
# Tag stability
# ---------------------------------------------------------------------------

def test_tag_ignores_collision_mtime(tmp_path, monkeypatch):
    """The tag must not move when only the collision cache's mtime changes.

    It used to hash (size, mtime).  mtime is machine-local and survives neither
    git nor a zip round-trip, so every downloader computed a different tag and
    a published cache would have missed 100% of the time.
    """
    col = tmp_path / 'collision_cache.bin'
    col.write_bytes(b'collision-payload')
    first = im._navmesh_geom_cache(str(col))
    assert first is not None
    st = os.stat(col)
    os.utime(col, (st.st_atime, st.st_mtime + 3600))
    assert im._navmesh_geom_cache(str(col))[1] == first[1]


def test_tag_tracks_navmesh_sources(tmp_path):
    """Editing a navmesh source must change the tag (self-invalidation)."""
    col = tmp_path / 'collision_cache.bin'
    col.write_bytes(b'x')
    before = im._navmesh_geom_cache(str(col))[1]
    src = os.path.join(REPO, 'tes5_import', 'navmesh', 'params.py')
    original = open(src, 'rb').read()
    try:
        with open(src, 'ab') as fh:
            fh.write(b'\n# cache-tag probe\n')
        assert im._navmesh_geom_cache(str(col))[1] != before
    finally:
        with open(src, 'wb') as fh:
            fh.write(original)
    assert im._navmesh_geom_cache(str(col))[1] == before


# ---------------------------------------------------------------------------
# Per-mesh invalidation
# ---------------------------------------------------------------------------

def test_one_changed_mesh_spares_other_cells(monkeypatch):
    """A replaced mesh must invalidate ONLY the cells that place it.

    With the whole-file collision hash this was false: swapping one mesh
    invalidated all ~8,200 Oblivion entries and forced a full regeneration.
    """
    _fake_collision(monkeypatch, {'a.nif': _soup(1), 'b.nif': _soup(2)})
    models = {0x111111: 'a.nif', 0x222222: 'b.nif'}
    cell_a = [_refr(0x111111)]
    cell_b = [_refr(0x222222)]

    a1 = _geom_hash(refr_recs=cell_a, base_model_by_fid=models, **HASH_ARGS)
    b1 = _geom_hash(refr_recs=cell_b, base_model_by_fid=models, **HASH_ARGS)

    _fake_collision(monkeypatch, {'a.nif': _soup(99), 'b.nif': _soup(2)})
    a2 = _geom_hash(refr_recs=cell_a, base_model_by_fid=models, **HASH_ARGS)
    b2 = _geom_hash(refr_recs=cell_b, base_model_by_fid=models, **HASH_ARGS)

    assert a1 != a2, 'cell placing the changed mesh must miss'
    assert b1 == b2, 'cell placing only unchanged meshes must still hit'


def test_geom_hash_is_refr_order_independent_for_collision(monkeypatch):
    """Collision digests are folded in sorted, so REFR order cannot perturb them.

    REFR order still contributes through the per-REFR lines above (position
    matters); this pins the *collision* contribution specifically.
    """
    _fake_collision(monkeypatch, {'a.nif': _soup(1), 'b.nif': _soup(2)})
    models = {0x111111: 'a.nif', 0x222222: 'b.nif'}
    refrs = [_refr(0x111111), _refr(0x222222)]
    h1 = _geom_hash(refr_recs=refrs, base_model_by_fid=models, **HASH_ARGS)
    _fake_collision(monkeypatch, {'b.nif': _soup(2), 'a.nif': _soup(1)})
    h2 = _geom_hash(refr_recs=refrs, base_model_by_fid=models, **HASH_ARGS)
    assert h1 == h2


def test_missing_collision_digest_is_empty(monkeypatch):
    """A mesh with no collision entry digests to '' rather than raising."""
    _fake_collision(monkeypatch, {})
    assert ce.collision_digest('nope.nif') == ''


def test_digest_accepts_lists_and_arrays(monkeypatch):
    """The scanners build float LISTS; load_collision builds numpy arrays.

    Both shapes reach collision_digest, so it must accept either and produce
    the SAME digest -- `.tobytes()` on a list raises AttributeError, which
    would crash any import that digested a freshly-scanned table.
    """
    w, b = [1.5] * 9, [2.5] * 9
    _fake_collision(monkeypatch, {'a.nif': {'w': w, 'b': b}})
    as_list = ce.collision_digest('a.nif')
    _fake_collision(monkeypatch, {'a.nif': {'w': np.array(w, np.float32),
                                            'b': np.array(b, np.float32)}})
    assert ce.collision_digest('a.nif') == as_list


def test_digests_cleared_on_reload(tmp_path, monkeypatch):
    """A reload must drop memoised digests, or they describe the OLD cache."""
    _fake_collision(monkeypatch, {'a.nif': _soup(1)})
    first = ce.collision_digest('a.nif')
    path = tmp_path / 'c.bin'
    path.write_bytes(ce._serialize({'a.nif': {'w': list(_soup(9)['w']),
                                              'b': list(_soup(9)['b'])}}))
    ce.load_collision(str(path), quiet=True)
    assert ce.collision_digest('a.nif') != first


def test_content_hash_ignores_key_order(monkeypatch):
    """collision_content_hash certifies CONTENT, not dict/file layout."""
    _fake_collision(monkeypatch, {'a.nif': _soup(1), 'b.nif': _soup(2)})
    h1 = ce.collision_content_hash()
    _fake_collision(monkeypatch, {'b.nif': _soup(2), 'a.nif': _soup(1)})
    assert ce.collision_content_hash() == h1


# ---------------------------------------------------------------------------
# Pre-push gate
# ---------------------------------------------------------------------------

def test_gate_watches_every_tag_source():
    """Every file feeding the tag must be gated, or a push ships a dead cache.

    import_main._navmesh_geom_cache hashes tes5_import/navmesh/*.py plus
    pgrd_to_navm.py; the hook's NAVMESH_PATHS must cover exactly those.
    """
    watched = set(hook.NAVMESH_PATHS)
    assert 'tes5_import/pgrd_to_navm.py' in watched
    assert 'tes5_import/navmesh/' in watched
    # Anything new in the navmesh package is covered by the directory prefix.
    for src in glob.glob(os.path.join(REPO, 'tes5_import', 'navmesh', '*.py')):
        rel = os.path.relpath(src, REPO).replace('\\', '/')
        assert any(rel.startswith(w) for w in watched), rel


def test_gate_covers_cache_defining_modules():
    """import_main and collision_extract change caching without feeding the tag."""
    assert '_navmesh_geom_cache' in hook.NAVMESH_FUNCS['tes5_import/import_main.py']
    assert '_gather_navm_jobs' in hook.NAVMESH_FUNCS['tes5_import/import_main.py']
    assert 'collision_digest' in \
        hook.NAVMESH_FUNCS['asset_convert/collision_extract.py']


def test_gate_ignores_post_cache_stitching():
    """navm_edge_links runs AFTER the cache, so it must not gate a push."""
    assert hook.touches_navmesh(['tes5_import/navm_edge_links.py']) == []


def test_gate_matches_expected_paths():
    assert hook.touches_navmesh(['tes5_import/navmesh/corridor.py'])
    assert hook.touches_navmesh(['tes5_import/pgrd_to_navm.py'])
    assert hook.touches_navmesh(['docs/x.md', 'tools/y.py']) == []


def test_stamp_written_only_by_a_real_build(tmp_path):
    """Computing the tag must NOT certify the cache.

    _navmesh_geom_cache is called by tools that merely want to know the tag; if
    it stamped CACHE_TAG, reading the tag would make a stale cache look freshly
    built and the gate would wave it through.
    """
    col = tmp_path / 'collision_cache.bin'
    col.write_bytes(b'payload')
    geom = im._navmesh_geom_cache(str(col))
    stamp = os.path.join(geom[0], 'CACHE_TAG')
    assert not os.path.exists(stamp), 'reading the tag must not stamp'

    im._stamp_navmesh_cache_tag(geom)
    assert open(stamp).read().strip() == geom[1]


def test_cache_matches_tag_is_exact(tmp_path, monkeypatch):
    """A correct cache passes regardless of mtime; a stale one never does."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    cdir = tmp_path / 'export' / 'Test.esm' / 'navmesh_geom_cache'
    cdir.mkdir(parents=True)

    assert hook.cache_matches_tag('Test.esm', 'TAG') is False   # unstamped
    (cdir / 'CACHE_TAG').write_text('TAG')
    assert hook.cache_matches_tag('Test.esm', 'TAG') is True
    # An old mtime must not matter -- a checkout or unzip rewrites mtimes, and
    # rejecting on that would cry wolf on a perfectly valid cache.
    os.utime(cdir / 'CACHE_TAG', (0, 0))
    assert hook.cache_matches_tag('Test.esm', 'TAG') is True
    assert hook.cache_matches_tag('Test.esm', 'OTHER') is False


def test_next_tag_format(monkeypatch):
    """The manifest's starting tag must match tag-on-push.yml's MAJOR.MMM."""
    monkeypatch.setattr(hook.subprocess, 'run', lambda *a, **k: None)
    monkeypatch.setattr(hook, 'git',
                        lambda *a: '0.54\n0.55\nnot-a-tag' if a[0] == 'tag' else '')
    assert hook.next_tag() == '0.551'


def test_next_tag_widens_legacy_two_digit_tags(monkeypatch):
    """A 2-digit tag is worth TEN new units: 0.58 is 0.580, so next is 0.581.

    Reading '0.58' as 58 thousandths would emit 0.059 and march the version
    backwards past every existing name.
    """
    monkeypatch.setattr(hook.subprocess, 'run', lambda *a, **k: None)
    monkeypatch.setattr(hook, 'git',
                        lambda *a: '0.57\n0.58' if a[0] == 'tag' else '')
    assert hook.next_tag() == '0.581'


def test_next_tag_ranks_three_digit_above_two_digit(monkeypatch):
    """0.580 outranks 0.58 (equal) and must not be beaten by a stray 0.59."""
    monkeypatch.setattr(hook.subprocess, 'run', lambda *a, **k: None)
    monkeypatch.setattr(hook, 'git',
                        lambda *a: '0.58\n0.581\n0.582' if a[0] == 'tag' else '')
    assert hook.next_tag() == '0.583'


def test_next_tag_rolls_over_to_v1_at_999(monkeypatch):
    """The thousandths scheme reaches v1 at 0.999 -> 1.000."""
    monkeypatch.setattr(hook.subprocess, 'run', lambda *a, **k: None)
    monkeypatch.setattr(hook, 'git',
                        lambda *a: '0.998\n0.999' if a[0] == 'tag' else '')
    assert hook.next_tag() == '1.000'


def test_next_tag_skips_names_already_taken(monkeypatch):
    """Must not predict a tag the remote already has.

    tag-on-push.yml fetches tags and then advances past any name already in
    use.  Reading only local refs predicted 0.56 while the remote already had
    it, which would have labelled the cache one version behind the code CI
    actually tagged (0.57).
    """
    monkeypatch.setattr(hook.subprocess, 'run', lambda *a, **k: None)
    monkeypatch.setattr(hook, 'git',
                        lambda *a: '0.54\n0.55\n0.551' if a[0] == 'tag' else '')
    assert hook.next_tag() == '0.552'


def test_next_tag_fetches_before_computing(monkeypatch):
    """A stale clone must not decide the version on its own."""
    ran = []
    monkeypatch.setattr(hook.subprocess, 'run',
                        lambda a, **k: ran.append(a))
    monkeypatch.setattr(hook, 'git', lambda *a: '0.55' if a[0] == 'tag' else '')
    hook.next_tag()
    assert any('fetch' in c and '--tags' in c for c in ran), \
        'next_tag must fetch remote tags first'


# ---------------------------------------------------------------------------
# Publish contract
# ---------------------------------------------------------------------------

def test_archive_excludes_collision_and_big_indexes(tmp_path, monkeypatch):
    """Only navmesh_geom_cache/*.pkl ships.

    collision_cache.bin is keyed-by-name Bethesda collision geometry and must
    never be redistributed; navmesh_index.pkl / audit_index3.pkl are ~2.1 GB
    each.  A glob over export/**/*.pkl would sweep in the latter, so the
    archiver names the cache directory explicitly.
    """
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    exp = tmp_path / 'export' / 'Test.esm'
    (exp / 'navmesh_geom_cache').mkdir(parents=True)
    (exp / 'collision_cache.bin').write_bytes(b'SECRET-COLLISION')
    (exp / 'navmesh_index.pkl').write_bytes(b'huge')
    for i in range(3):
        with open(exp / 'navmesh_geom_cache' / ('%08X_%08X.pkl' % (i, i)), 'wb') as fh:
            pickle.dump({'hash': 'h%d' % i,
                         'verts': np.zeros((3, 3), np.float32),
                         'tris': np.zeros((1, 3), np.int32),
                         'ledges': []}, fh)

    monkeypatch.setattr(nc, 'source_tag', lambda p: 'tag123')
    monkeypatch.setattr(nc, 'collision_hash', lambda p: 'col456')
    zpath = nc.archive('Test.esm', str(tmp_path / 'out'), '0.56', quiet=True)
    assert zpath is not None

    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        assert sorted(n for n in names if n.endswith('.pkl')) == [
            '00000000_00000000.pkl', '00000001_00000001.pkl',
            '00000002_00000002.pkl']
        assert not any('collision' in n for n in names)
        assert 'navmesh_index.pkl' not in names
        blob = b''.join(zf.read(n) for n in names)
        assert b'SECRET-COLLISION' not in blob
        manifest = json.loads(zf.read(nc.MANIFEST_NAME))

    assert manifest['source_tag'] == 'tag123'
    assert manifest['starting_tag'] == '0.56'
    assert manifest['entries'] == 3


def test_archive_refuses_corrupt_cache(tmp_path, monkeypatch):
    """A truncated entry must block the publish, not ship silently."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    cdir = tmp_path / 'export' / 'Test.esm' / 'navmesh_geom_cache'
    cdir.mkdir(parents=True)
    (cdir / 'bad.pkl').write_bytes(b'not a pickle')
    monkeypatch.setattr(nc, 'source_tag', lambda p: 'tag')
    monkeypatch.setattr(nc, 'collision_hash', lambda p: 'col')
    assert nc.archive('Test.esm', str(tmp_path / 'out'), '0.56', quiet=True) is None


def test_install_refuses_mismatched_manifest(tmp_path, monkeypatch):
    """A cache from different navmesh code must not install without --force."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    (tmp_path / 'export' / 'Test.esm').mkdir(parents=True)
    zpath = tmp_path / 'c.zip'
    with zipfile.ZipFile(zpath, 'w') as zf:
        zf.writestr(nc.MANIFEST_NAME, json.dumps(
            {'plugin': 'Test.esm', 'starting_tag': '0.56',
             'source_tag': 'OLD', 'collision_hash': 'OLD'}))
    monkeypatch.setattr(nc, 'source_tag', lambda p: 'NEW')
    monkeypatch.setattr(nc, 'collision_hash', lambda p: 'NEW')
    # INSTALL_MISMATCH, not a bare failure: auto_install() reports the CAUSE to
    # the user, and "built by different navmesh code" and "that file is not a
    # usable zip" send them to completely different fixes.
    assert nc.install('Test.esm', None, str(zpath)) == nc.INSTALL_MISMATCH
    assert nc.install('Test.esm', None, str(zpath), force=True) == nc.INSTALL_OK


def test_install_certifies_only_a_matching_cache(tmp_path, monkeypatch):
    """CACHE_TAG is written on a clean install and withheld under --force.

    A downloaded cache that matches must be certified, or the next verify calls
    a perfectly good download stale.  A --force install of a KNOWN-mismatched
    archive must not be certified, or the stamp would vouch for a cache the
    user was just warned about.
    """
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    (tmp_path / 'export' / 'Test.esm').mkdir(parents=True)
    zpath = tmp_path / 'c.zip'
    with zipfile.ZipFile(zpath, 'w') as zf:
        zf.writestr(nc.MANIFEST_NAME, json.dumps(
            {'plugin': 'Test.esm', 'starting_tag': '0.56',
             'source_tag': 'MATCHING', 'collision_hash': 'C'}))
        zf.writestr('CACHE_TAG', 'MATCHING')
        zf.writestr('a.pkl', b'x')
    stamp = tmp_path / 'export' / 'Test.esm' / 'navmesh_geom_cache' / 'CACHE_TAG'

    monkeypatch.setattr(nc, 'source_tag', lambda p: 'MATCHING')
    monkeypatch.setattr(nc, 'collision_hash', lambda p: 'C')
    assert nc.install('Test.esm', None, str(zpath)) == 0
    assert stamp.read_text() == 'MATCHING'

    # Now the local tree moves on: the same archive is stale, and --force must
    # clear the inherited stamp rather than leave it vouching for the cache.
    monkeypatch.setattr(nc, 'source_tag', lambda p: 'MOVED_ON')
    assert nc.install('Test.esm', None, str(zpath), force=True) == 0
    assert not stamp.exists()


def test_install_rejects_path_traversal(tmp_path, monkeypatch):
    """A crafted archive must not write outside the cache directory."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    (tmp_path / 'export' / 'Test.esm').mkdir(parents=True)
    zpath = tmp_path / 'evil.zip'
    with zipfile.ZipFile(zpath, 'w') as zf:
        zf.writestr(nc.MANIFEST_NAME, json.dumps(
            {'plugin': 'Test.esm', 'starting_tag': '0.56',
             'source_tag': 'T', 'collision_hash': 'C'}))
        zf.writestr('../../../evil.pkl', b'x')
    monkeypatch.setattr(nc, 'source_tag', lambda p: 'T')
    monkeypatch.setattr(nc, 'collision_hash', lambda p: 'C')
    assert nc.install('Test.esm', None, str(zpath)) == 1
    assert not (tmp_path.parent / 'evil.pkl').exists()


def test_install_requires_manifest(tmp_path, monkeypatch):
    """An archive with no manifest is unidentified and must be refused."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    (tmp_path / 'export' / 'Test.esm').mkdir(parents=True)
    zpath = tmp_path / 'nomanifest.zip'
    with zipfile.ZipFile(zpath, 'w') as zf:
        zf.writestr('a.pkl', b'x')
    assert nc.install('Test.esm', None, str(zpath)) == 1


def test_cache_release_never_shadows_the_code_tag():
    """A cache release must not be mistakable for the converter download.

    The repo ships code as annotated TAGS (tag-on-push.yml), and GitHub renders
    a real Release above a plain tag on /releases.  A release named '0.56'
    would therefore sit at the top looking like THE 0.56 download while holding
    only a build cache.
    """
    assert nc.cache_release_tag('0.56') == 'navmesh-cache-0.56+'
    assert nc.cache_release_tag('0.56') != '0.56'

    notes = nc.cache_release_notes('0.56')
    # Must say what it is not, and point at the tag list.
    assert 'not the converter' in notes.lower()
    assert '/tags' in notes
    assert '0.56' in notes
    # Release notes go through gh's argv; non-ASCII has bitten this repo
    # before (6b443f0), so keep the body plain.
    assert all(ord(c) < 128 for c in notes)


def test_latest_cache_release_sorts_numerically(monkeypatch):
    """Sort by version, not as strings, and ignore non-cache releases.

    '0.586' must beat '0.100' (string order puts '0.100' first), and the two
    schemes must interleave correctly: 0.58 IS 0.580, so 0.586 outranks it by
    six thousandths rather than being read as 58 vs 586.
    """
    class _R:
        returncode = 0
        stdout = '\n'.join(('navmesh-cache-0.100+', 'navmesh-cache-0.586+',
                            'navmesh-cache-0.58-0.585',
                            '0.55', 'some-other-release'))

    monkeypatch.setattr(nc.subprocess, 'run', lambda *a, **k: _R())
    assert nc.latest_cache_release() == 'navmesh-cache-0.586+'


def test_version_key_scales_by_minor_width():
    """The minor field's WIDTH sets its scale -- 0.58 means 0.580.

    The range check ("does this cache cover my version?") compares these keys
    directly, so an unscaled '0.57' start would appear to cover 0.100-0.569.
    """
    assert nc._version_key('0.58') == nc._version_key('0.580')
    assert nc._version_key('0.57') < nc._version_key('0.581')
    assert nc._version_key('0.586') > nc._version_key('0.58')
    # String order lies here; numeric order must not.
    assert nc._version_key('0.100') > nc._version_key('0.099')
    assert nc._version_key('not-a-version') is None


def test_version_key_never_hides_a_wider_minor_field():
    """A wider minor field must still yield a key, never None.

    None makes resolve_cache_release(), latest_cache_release() and
    auto_install()'s range check all skip that release SILENTLY, which is
    indistinguishable from "no cache exists" -- the exact failure mode this
    whole change set out to remove.  Scaling is by field WIDTH, so a 4-digit
    tag stays comparable with every 2- and 3-digit one.
    """
    assert nc._version_key('1.0000') is not None
    assert nc._version_key('0.1234') is not None
    assert nc._version_key('1.0') == nc._version_key('1.000')
    assert nc._version_key('0.9') == nc._version_key('0.900')


def test_local_version_key_is_none_for_a_dev_build(monkeypatch):
    """'0.0-dev' must read as UNKNOWN, never as the number 0.0.

    version.DEV_VERSION is the literal '0.0-dev'.  Stripping the suffix leaves
    '0.0', which parses to a perfectly valid (0, 0) -- and (0, 0) sorts BELOW
    every published range start, so the range check rejected every release with
    "no published cache covers this build".  That silently disabled the
    download for dev checkouts and untagged source drops: precisely the
    population range matching was added to serve.  None is permissive; zero is
    excluded from everything.
    """
    import version as _v
    monkeypatch.setattr(_v, 'current_version', lambda: _v.DEV_VERSION)
    assert nc._local_version_key() is None
    # A real tag, and a checkout past one, must still resolve.
    monkeypatch.setattr(_v, 'current_version', lambda: '0.586')
    assert nc._local_version_key() == nc._version_key('0.586')
    monkeypatch.setattr(_v, 'current_version', lambda: '0.586+geefacb3')
    assert nc._local_version_key() == nc._version_key('0.586')
    monkeypatch.setattr(_v, 'current_version', lambda: '0.586-3-gabc123')
    assert nc._local_version_key() == nc._version_key('0.586')


def test_auto_install_downloads_on_a_dev_build(tmp_path, monkeypatch):
    """An unplaceable build must still GET a cache, not be refused one.

    End-to-end guard for the bug above: with a real (unmocked)
    _local_version_key on a dev tree, auto_install must still reach a release.
    """
    import version as _v
    monkeypatch.setattr(_v, 'current_version', lambda: _v.DEV_VERSION)
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(hook, 'cache_matches_tag', lambda *a, **k: False)
    monkeypatch.setattr(nc, 'source_tag', lambda _p: 'MY_TAG')
    monkeypatch.setattr(nc, '_api_releases', lambda: [{
        'tag_name': 'navmesh-cache-0.586+',
        'body': '<!-- navmesh-source-tag: OTHER -->',
        'assets': [{'name': nc.asset_name('Test.esm'), 'size': 1 << 20,
                    'browser_download_url': 'https://example/x.zip'}]}])
    seen = {}
    monkeypatch.setattr(nc, '_download',
                        lambda url, dest, **k: seen.update(url=url) or True)
    monkeypatch.setattr(nc, 'install', lambda *a, **k: 0)
    assert nc.auto_install('Test.esm', quiet=True) is True
    assert seen['url'] == 'https://example/x.zip'


def test_find_dropin_reaches_the_documented_three_levels(tmp_path, monkeypatch):
    """The docstring and navmesh_cache/README.md both promise three levels.

    `depth >= 3` pruned BEFORE scanning level 3's files, so the real reach was
    two -- a silent contradiction of the documented contract.
    """
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    deep = tmp_path / nc.DROPIN_DIRNAME / 'a' / 'b' / 'c'
    deep.mkdir(parents=True)
    (deep / nc.asset_name('Test.esm')).write_bytes(b'zip')
    assert nc._find_dropin('Test.esm') == str(deep / nc.asset_name('Test.esm'))


def test_latest_cache_release_none_when_absent(monkeypatch):
    class _R:
        returncode = 0
        stdout = '0.55\n0.54\n'

    monkeypatch.setattr(nc.subprocess, 'run', lambda *a, **k: _R())
    assert nc.latest_cache_release() is None


def test_release_name_states_the_version_range():
    """The name must say which versions the cache covers.

    Open-ended at publish time (the upper bound is not knowable yet -- nobody
    knows whether 0.56's cache survives to 0.57 or 0.72), closed later by
    close_cache_release when a navmesh change actually invalidates it.
    """
    assert nc.cache_release_tag('0.56') == 'navmesh-cache-0.56+'
    assert nc.cache_release_tag('0.56', '0.72') == 'navmesh-cache-0.56-0.72'
    assert nc.parse_cache_release_tag('navmesh-cache-0.56+') == ('0.56', None)
    assert nc.parse_cache_release_tag(
        'navmesh-cache-0.56-0.72') == ('0.56', '0.72')
    assert nc.parse_cache_release_tag('0.55') is None


def test_previous_tag_matches_tag_on_push_numbering():
    """A closed range ends on the last version the cache was VALID for."""
    # Legacy 2-digit tags keep stepping by a hundredth: those names are the
    # ones actually published, and '0.569' would 404.
    assert nc.previous_tag('0.73') == '0.72'
    assert nc.previous_tag('1.00') == '0.99'   # MAJOR.MM rollover
    assert nc.previous_tag('0.57') == '0.56'
    # 3-digit tags step by a thousandth.
    assert nc.previous_tag('0.582') == '0.581'
    assert nc.previous_tag('0.599') == '0.598'
    # Past the switchover every thousandth is a real published tag, so the
    # v1 rollover steps to 0.999 -- NOT to the 2-digit '0.99'.
    assert nc.previous_tag('1.000') == '0.999'
    assert nc.previous_tag('1.001') == '1.000'
    # The boundary steps back into the old scheme's spelling, since 0.581 was
    # the first 3-digit tag and 0.58 the last 2-digit one.
    assert nc.previous_tag('0.581') == '0.58'
    # A trailing-zero 3-digit tag is the same version as its 2-digit spelling.
    assert nc.previous_tag('0.580') == '0.57'


def _mock_releases(monkeypatch, listing):
    seen = []

    class _R:
        returncode = 0
        stdout = listing

    def _run(args, **kw):
        seen.append(args)
        return _R()

    monkeypatch.setattr(nc.subprocess, 'run', _run)
    return seen


def test_resolve_cache_release_matches_the_range_not_the_name(monkeypatch):
    """`--tag 0.60` must find the release COVERING 0.60.

    Constructing 'navmesh-cache-0.60+' would 404: the cache serving 0.60 is
    published as '0.56+' and later renamed '0.56-0.72'.
    """
    _mock_releases(monkeypatch,
                   'navmesh-cache-0.56-0.72\nnavmesh-cache-0.73+\n0.75\n')
    assert nc.resolve_cache_release('0.56') == 'navmesh-cache-0.56-0.72'
    assert nc.resolve_cache_release('0.60') == 'navmesh-cache-0.56-0.72'
    assert nc.resolve_cache_release('0.72') == 'navmesh-cache-0.56-0.72'
    assert nc.resolve_cache_release('0.73') == 'navmesh-cache-0.73+'
    assert nc.resolve_cache_release('0.90') == 'navmesh-cache-0.73+'
    # Older than every published cache -> nothing covers it.
    assert nc.resolve_cache_release('0.50') is None


def test_close_cache_release_renames_open_range(monkeypatch):
    seen = _mock_releases(monkeypatch, 'navmesh-cache-0.56+\n0.55\n')
    assert nc.close_cache_release('0.73') == 'navmesh-cache-0.56-0.72'
    edit = [c for c in seen if 'edit' in c]
    assert edit and 'navmesh-cache-0.56-0.72' in edit[0]


def test_close_cache_release_leaves_closed_ranges_alone(monkeypatch):
    """An already-closed range must never be renamed again."""
    _mock_releases(monkeypatch, 'navmesh-cache-0.56-0.72\n')
    assert nc.close_cache_release('0.73') is None


def test_close_cache_release_ignores_same_or_older(monkeypatch):
    """Republishing the same version must not close its own range."""
    _mock_releases(monkeypatch, 'navmesh-cache-0.56+\n')
    assert nc.close_cache_release('0.56') is None
    assert nc.close_cache_release('0.50') is None


def test_close_cache_release_never_emits_an_empty_range(monkeypatch):
    """The upper bound must never fall below the start.

    previous_tag() steps in the SPELLING's scheme while the range check
    compares thousandths-scaled keys, so closing '0.581+' against a 2-digit
    successor stepped in hundredths to '0.58' = (0, 580) -- one unit UNDER the
    start's (0, 581).  'navmesh-cache-0.581-0.58' then matched nothing at all,
    silently retiring a good cache for every version including its own.
    """
    seen = _mock_releases(monkeypatch, 'navmesh-cache-0.581+\n')
    closed = nc.close_cache_release('0.59')
    assert closed == 'navmesh-cache-0.581-0.581'

    lo, hi = nc.parse_cache_release_tag(closed)
    assert nc._version_key(hi) >= nc._version_key(lo)
    # The start version is still covered by the range that begins at it.
    assert seen and any('edit' in c for c in seen)


def test_version_key_agrees_with_the_program_wide_comparator():
    """navmesh_cache._version_key and version.version_key are ONE scale.

    _local_version_key() feeds version.py's key into a range check built from
    _version_key()'s keys, so any disagreement compares two different scales.
    They had already drifted: version.py scaled only a 2-digit minor, ranking
    the legacy tag '0.9' as nine THOUSANDTHS -- below '0.10'.
    """
    import version as _v
    for tag in ('0.9', '0.10', '0.56', '0.58', '0.580', '0.581', '0.586',
                '0.59', '0.73', '1.000', '0.100', '0.1234'):
        assert _v.version_key(tag) == nc._version_key(tag), tag
    # The ordering that motivates the scaling, in both directions.
    assert _v.version_key('0.9') > _v.version_key('0.10')
    assert _v.version_key('0.58') == _v.version_key('0.580')
    assert _v.version_key('0.59') > _v.version_key('0.581')


def test_have_gh_requires_auth_not_just_presence(monkeypatch):
    """An installed-but-logged-out gh must not count as usable.

    Otherwise publish builds every archive and then dies inside
    `gh release create` with an opaque error, having already spent the time.
    """
    monkeypatch.setattr(nc.shutil, 'which', lambda _n: None)
    assert nc.have_gh() is False

    monkeypatch.setattr(nc.shutil, 'which', lambda _n: 'C:/gh.exe')

    class _R:
        def __init__(self, rc):
            self.returncode = rc

    monkeypatch.setattr(nc.subprocess, 'run', lambda *a, **k: _R(1))
    assert nc.have_gh() is False, 'logged-out gh must not count as available'
    monkeypatch.setattr(nc.subprocess, 'run', lambda *a, **k: _R(0))
    assert nc.have_gh() is True


def test_auto_install_is_a_noop_when_already_current(tmp_path, monkeypatch):
    """A current cache must not be re-installed or re-downloaded every import."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(hook, 'cache_matches_tag', lambda *a, **k: True)
    called = []
    monkeypatch.setattr(nc, 'install', lambda *a, **k: called.append(a) or 0)
    assert nc.auto_install('Test.esm', quiet=True) is False
    assert not called


def test_auto_install_prefers_dropin_over_download(tmp_path, monkeypatch):
    """An offline drop-in must win, so no network is needed at all."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(hook, 'cache_matches_tag', lambda *a, **k: False)
    ddir = tmp_path / nc.DROPIN_DIRNAME
    ddir.mkdir()
    (ddir / nc.asset_name('Test.esm')).write_bytes(b'zip')

    seen = {}

    def _install(plugin, tag, zip_path, force=False):
        seen['zip'] = zip_path
        return 0

    monkeypatch.setattr(nc, 'install', _install)
    monkeypatch.setattr(nc, 'have_gh', lambda: True)
    monkeypatch.setattr(nc, 'latest_cache_release',
                        lambda: pytest.fail('must not download'))
    assert nc.auto_install('Test.esm', quiet=True) is True
    assert seen['zip'].endswith(nc.asset_name('Test.esm'))


def test_auto_install_tolerates_a_renamed_dropin(tmp_path, monkeypatch):
    """Browsers rename duplicates ('...(1).zip'); still find it."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    ddir = tmp_path / nc.DROPIN_DIRNAME
    ddir.mkdir()
    (ddir / 'navmesh-cache-Test (1).zip').write_bytes(b'zip')
    assert nc._find_dropin('Test.esm').endswith('(1).zip')


def test_auto_install_never_raises(tmp_path, monkeypatch):
    """It runs inside a conversion -- it must never break one."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))

    def _boom(*a, **k):
        raise RuntimeError('network on fire')

    monkeypatch.setattr(hook, 'cache_matches_tag', _boom)
    assert nc.auto_install('Test.esm', quiet=True) is False


def test_auto_install_respects_download_opt_out(tmp_path, monkeypatch):
    """allow_download=False must not touch the network (metered connections)."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(hook, 'cache_matches_tag', lambda *a, **k: False)
    monkeypatch.setattr(nc, 'latest_cache_release',
                        lambda: pytest.fail('must not download'))
    assert nc.auto_install('Test.esm', quiet=True,
                           allow_download=False) is False


def test_api_repo_falls_back_without_a_git_remote(monkeypatch):
    """A source drop has no .git, and must still be able to download.

    The README tells users to "paste a new download over your existing
    folder", so most installs are an unzipped archive.  `git remote get-url
    origin` fails there; api_repo() used to return '' and _api_releases()
    then returned [] -- the download silently did nothing.
    """
    monkeypatch.setattr(nc, 'gh_repo', lambda: [])
    assert nc.api_repo() == nc.FALLBACK_REPO
    assert '/' in nc.api_repo()


def test_install_accepts_a_collision_mismatch(tmp_path, monkeypatch):
    """Differing meshes must NOT block the install -- only differing CODE does.

    collision_content_hash folds in every mesh the user extracted, so it only
    matches someone with an identical game/DLC/mod/extraction state.  Refusing
    on it made the drop-in "not work if it is in the folder" for nearly
    everyone, even though invalidation is per mesh and the rest still hits.
    """
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(nc, 'source_tag', lambda _p: 'TAG')
    monkeypatch.setattr(nc, 'collision_hash', lambda _p: 'MY_OWN_MESHES')

    zp = tmp_path / 'c.zip'
    with zipfile.ZipFile(zp, 'w') as zf:
        zf.writestr(nc.MANIFEST_NAME, json.dumps({
            'plugin': 'Test.esm', 'starting_tag': '0.586',
            'source_tag': 'TAG', 'collision_hash': 'PUBLISHERS_MESHES'}))
        zf.writestr('a.pkl', pickle.dumps({'hash': 'h', 'verts': [],
                                           'tris': [], 'ledges': []}))

    assert nc.install('Test.esm', None, str(zp)) == 0
    dest = nc.cache_dir('Test.esm')
    assert os.path.exists(os.path.join(dest, 'a.pkl'))
    # Built by this navmesh code, so it is still certified.
    with open(os.path.join(dest, 'CACHE_TAG')) as fh:
        assert fh.read().strip() == 'TAG'


def test_install_still_refuses_a_source_tag_mismatch(tmp_path, monkeypatch):
    """A cache from other navmesh code is dead weight -- keep refusing it."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(nc, 'source_tag', lambda _p: 'TAG')
    monkeypatch.setattr(nc, 'collision_hash', lambda _p: None)

    zp = tmp_path / 'c.zip'
    with zipfile.ZipFile(zp, 'w') as zf:
        zf.writestr(nc.MANIFEST_NAME, json.dumps({
            'plugin': 'Test.esm', 'source_tag': 'OTHER_CODE'}))
        zf.writestr('a.pkl', b'x')

    assert nc.install('Test.esm', None, str(zp)) == nc.INSTALL_MISMATCH


def test_install_reports_a_bad_zip_without_raising(tmp_path, monkeypatch):
    """A truncated / 0-byte drop-in is a soft failure, never an exception.

    It used to raise BadZipFile straight out of install().  auto_install()
    swallowed that in its outer handler, so ONE unusable file -- a partial
    download, a `.part`, a 0-byte placeholder -- skipped every remaining
    drop-in candidate AND the HTTPS download that would have fixed it.
    """
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(nc, 'source_tag', lambda _p: 'TAG')
    monkeypatch.setattr(nc, 'collision_hash', lambda _p: None)

    truncated = tmp_path / 'partial.zip'
    truncated.write_bytes(b'PK\x03\x04not-really-a-zip')
    assert nc.install('Test.esm', None, str(truncated)) == nc.INSTALL_FAILED

    empty = tmp_path / 'empty.zip'
    empty.write_bytes(b'')
    assert nc.install('Test.esm', None, str(empty)) == nc.INSTALL_FAILED


def test_auto_install_tries_every_dropin_candidate(tmp_path, monkeypatch):
    """A bad shallow zip must not shadow a good nested one.

    The depth-3 walk made this collision likely: an unusable file the user
    dropped at the top level sorts FIRST, and returning only that candidate
    made it fatal to the whole feature.
    """
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(nc, 'source_tag', lambda _p: 'TAG')
    monkeypatch.setattr(nc, 'collision_hash', lambda _p: None)

    ddir = tmp_path / nc.DROPIN_DIRNAME
    ddir.mkdir()
    want = nc.asset_name('Test.esm')
    # Shallowest match is junk...
    (ddir / want).write_bytes(b'PK\x03\x04truncated')
    # ...and the real archive sits inside an extracted folder.
    sub = ddir / 'tes4skyrim-0.586'
    sub.mkdir()
    with zipfile.ZipFile(sub / want, 'w') as zf:
        zf.writestr(nc.MANIFEST_NAME, json.dumps({
            'plugin': 'Test.esm', 'source_tag': 'TAG',
            'starting_tag': '0.586'}))
        zf.writestr('cell.pkl', b'payload')

    assert len(nc.find_dropins('Test.esm')) == 2
    # Reaches the good one instead of dying on the junk one, and never falls
    # through to the network.
    monkeypatch.setattr(nc, '_api_releases', lambda: [])
    assert nc.auto_install('Test.esm', quiet=True) is True
    assert (nc.cache_dir('Test.esm') and
            os.path.exists(os.path.join(nc.cache_dir('Test.esm'), 'cell.pkl')))


def test_find_dropin_looks_inside_extracted_folders(tmp_path, monkeypatch):
    """Extracting a download leaves the zip one folder down -- still find it."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    nested = tmp_path / nc.DROPIN_DIRNAME / 'tes4skyrim-0.586'
    nested.mkdir(parents=True)
    (nested / nc.asset_name('Test.esm')).write_bytes(b'zip')
    got = nc._find_dropin('Test.esm')
    assert got and got.endswith(nc.asset_name('Test.esm'))


def test_find_dropin_prefers_the_shallowest_match(tmp_path, monkeypatch):
    """A file placed directly beats one left inside an extracted folder."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    ddir = tmp_path / nc.DROPIN_DIRNAME
    (ddir / 'sub').mkdir(parents=True)
    (ddir / 'sub' / nc.asset_name('Test.esm')).write_bytes(b'deep')
    (ddir / nc.asset_name('Test.esm')).write_bytes(b'shallow')
    assert nc._find_dropin('Test.esm') == str(ddir / nc.asset_name('Test.esm'))


def test_auto_install_downloads_a_cache_covering_a_later_version(
        tmp_path, monkeypatch, capsys):
    """'0.586+' must serve 0.590 -- "that version and above".

    The old rule demanded an exact source-tag match, which is strictly narrower
    than the range the release advertises: any build past the tag (every dev
    checkout) was told no cache existed.
    """
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(hook, 'cache_matches_tag', lambda *a, **k: False)
    monkeypatch.setattr(nc, 'source_tag', lambda _p: 'MY_TAG')
    monkeypatch.setattr(nc, '_local_version_key', lambda: (0, 590))
    monkeypatch.setattr(nc, '_api_releases', lambda: [{
        'tag_name': 'navmesh-cache-0.586+',
        'body': '<!-- navmesh-source-tag: OTHER -->',
        'assets': [{'name': nc.asset_name('Test.esm'),
                    'size': 1 << 20,
                    'browser_download_url': 'https://example/x.zip'}]}])

    seen = {}
    monkeypatch.setattr(nc, '_download',
                        lambda url, dest, **k: seen.update(url=url) or True)
    monkeypatch.setattr(nc, 'install', lambda *a, **k: 0)

    assert nc.auto_install('Test.esm', quiet=False) is True
    assert seen['url'] == 'https://example/x.zip'
    # And it must be VISIBLE, not silent.
    assert 'downloading' in capsys.readouterr().out.lower()


def test_auto_install_skips_a_cache_below_its_range(tmp_path, monkeypatch):
    """A closed range must not serve a version above its upper bound."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(hook, 'cache_matches_tag', lambda *a, **k: False)
    monkeypatch.setattr(nc, 'source_tag', lambda _p: 'MY_TAG')
    monkeypatch.setattr(nc, '_local_version_key', lambda: (0, 900))
    monkeypatch.setattr(nc, '_api_releases', lambda: [{
        'tag_name': 'navmesh-cache-0.57-0.585',
        'body': '',
        'assets': [{'name': nc.asset_name('Test.esm'), 'size': 1,
                    'browser_download_url': 'https://example/x.zip'}]}])
    monkeypatch.setattr(nc, '_download',
                        lambda *a, **k: pytest.fail('must not download'))
    assert nc.auto_install('Test.esm', quiet=True) is False


def test_no_download_env_var_is_shared_not_duplicated():
    """The GUI menu item and convert.py must read the SAME variable name.

    Both used to spell 'TESCONV_NO_CACHE_DOWNLOAD' as a literal; a typo in
    either would silently disable the opt-out (the checkbox would appear to do
    nothing). Exporting one constant makes that impossible.
    """
    assert nc.NO_DOWNLOAD_ENV_VAR == 'TESCONV_NO_CACHE_DOWNLOAD'
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ('gui.py', 'convert.py'):
        with open(os.path.join(root, name), encoding='utf-8') as fh:
            src = fh.read()
        assert 'NO_DOWNLOAD_ENV_VAR' in src, name
        # The bare literal must not reappear in CODE (comments may name it for
        # the reader -- it is the documented user-facing switch).
        code = [ln for ln in src.splitlines()
                if nc.NO_DOWNLOAD_ENV_VAR in ln
                and not ln.lstrip().startswith('#')]
        assert not code, (
            '%s hardcodes the env var in code; import NO_DOWNLOAD_ENV_VAR: %s'
            % (name, code))


def test_auto_install_explains_the_download_opt_out(
        tmp_path, monkeypatch, capsys):
    """Opting out must still say why nothing happened, and how to go offline."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(hook, 'cache_matches_tag', lambda *a, **k: False)
    monkeypatch.setattr(nc, 'source_tag', lambda _p: 'TAG')
    monkeypatch.setattr(nc, '_api_releases',
                        lambda: pytest.fail('must not touch the network'))
    assert nc.auto_install('Test.esm', quiet=False,
                           allow_download=False) is False
    out = capsys.readouterr().out
    assert 'TESCONV_NO_CACHE_DOWNLOAD' in out
    assert nc.DROPIN_DIRNAME in out


def test_auto_install_announces_an_already_current_cache(
        tmp_path, monkeypatch, capsys):
    """Silence reads as "it does not work" -- say the cache is in place."""
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    monkeypatch.setattr(hook, 'cache_matches_tag', lambda *a, **k: True)
    assert nc.auto_install('Test.esm', quiet=False) is False
    assert 'up to date' in capsys.readouterr().out.lower()


def _fake_export(tmp_path, *plugins):
    for name in plugins:
        (tmp_path / 'export' / name / nc.CACHE_DIRNAME).mkdir(parents=True)


def test_discover_plugins_publishes_only_the_hosted_three(tmp_path, monkeypatch):
    """A local DLC / landmass cache must never reach the gate or a release.

    Before this filter, discover_plugins() returned every export/*/ with a
    cache dir, so a plugin run once for testing left a 0-entry cache that
    failed verify() and hard-blocked every push to master.
    """
    monkeypatch.setattr(nc, 'repo_root', lambda: str(tmp_path))
    _fake_export(tmp_path, 'Oblivion.esm', 'Nehrim.esm', 'Morrowind_ob.esm',
                 'DLCShiveringIsles.esp', 'ElsweyrAnequina.esp', 'Tamriel.esp')

    assert nc.discover_plugins() == ['Morrowind_ob.esm', 'Nehrim.esm',
                                     'Oblivion.esm']
    # The unfiltered view still sees everything -- `verify` reports local state.
    assert 'Tamriel.esp' in nc.discover_plugins(all_plugins=True)


def test_publishable_set_is_the_documented_three():
    assert set(nc.PUBLISHABLE_PLUGINS) == {
        'Oblivion.esm', 'Nehrim.esm', 'Morrowind_ob.esm'}


def test_is_publishable_ignores_case():
    """export/ folder names come from whatever the user typed after -f."""
    assert nc.is_publishable('nehrim.esm')
    assert nc.is_publishable('OBLIVION.ESM')
    assert not nc.is_publishable('Nehrim.esp')


def test_asset_name_is_stable():
    assert nc.asset_name('Oblivion.esm') == 'navmesh-cache-Oblivion.zip'
    assert nc.asset_name('Morrowind_ob.esm') == 'navmesh-cache-Morrowind_ob.zip'
    # Spaces would break `gh release download --pattern`.
    assert ' ' not in nc.asset_name('Morrowind_ob - Chargen and Transport Mod.esp')


def _job(stratum, i=0):
    """A job dict carrying only what _stratum and mark_jobs look at."""
    base = {'key': (i, i), 'land_rec': None, 'refr_recs': []}
    if stratum == 'exterior':
        base['land_rec'] = {'VHGT': 'x'}
    elif stratum == 'door':
        base['extra_door_refrs'] = [{'NAME': '1'}]
    elif stratum == 'crowded':
        base['refr_recs'] = [{}] * 150
    return base


def test_mark_jobs_never_exceeds_the_budget():
    """The sample is a CEILING: a per-worker budget would multiply by workers."""
    jobs = [_job('exterior', i) for i in range(500)]
    jobs += [_job('interior', 500 + i) for i in range(50)]
    assert navm_verify.mark_jobs(jobs, 40) == 40
    assert sum(1 for j in jobs if j.get('verify')) == 40


def test_mark_jobs_spreads_across_strata():
    """A behaviour change may touch only one kind of cell, so sample all kinds."""
    jobs = []
    for n, stratum in enumerate(('interior', 'exterior', 'door', 'crowded')):
        jobs += [_job(stratum, n * 1000 + i) for i in range(200)]
    navm_verify.mark_jobs(jobs, 40)
    got = {navm_verify._stratum(j) for j in jobs if j.get('verify')}
    assert got == {'interior', 'exterior', 'door', 'crowded'}


def test_mark_jobs_zero_budget_marks_nothing():
    """A zero budget disables verification without touching the jobs."""
    jobs = [_job('interior', i) for i in range(10)]
    assert navm_verify.mark_jobs(jobs, 0) == 0
    assert not any(j.get('verify') for j in jobs)


def test_verify_budget_env_var(monkeypatch):
    """The env var overrides the default; junk falls back, explicit wins."""
    monkeypatch.setenv(navm_verify.VERIFY_ENV_VAR, '7')
    assert navm_verify.verify_budget() == 7
    monkeypatch.setenv(navm_verify.VERIFY_ENV_VAR, '0')
    assert navm_verify.verify_budget() == 0
    monkeypatch.setenv(navm_verify.VERIFY_ENV_VAR, 'nonsense')
    assert navm_verify.verify_budget() == navm_verify.VERIFY_DEFAULT
    assert navm_verify.verify_budget(3) == 3


def test_geom_equal_is_exact_on_float32():
    """Fresh builds are f64; the cache is f32.  Compare AFTER demotion."""
    verts = [(1.0, 2.0, 3.0), (4.5, 5.5, 6.5)]
    tris = [(0, 1, 0)]
    assert geom_equal((verts, tris, []), (list(verts), list(tris), []))
    moved = [(1.5, 2.0, 3.0), (4.5, 5.5, 6.5)]
    assert not geom_equal((verts, tris, []), (moved, tris, []))


def test_geom_equal_ignores_ledge_order():
    """Ledges are a set of links; their order carries no meaning."""
    a = ([(0.0, 0.0, 0.0)], [(0, 0, 0)], [(1, 2), (3, 4)])
    b = ([(0.0, 0.0, 0.0)], [(0, 0, 0)], [(3, 4), (1, 2)])
    assert geom_equal(a, b)


def test_geom_quantize_matches_what_the_cache_stores():
    """The demotion must agree with _geom_cache_store's float32 array."""
    verts = [(1.0000001, 2.0, 3.0)]
    stored = np.asarray(verts, dtype=np.float32).tolist()
    assert geom_quantize(verts) == [tuple(v) for v in stored]


def test_uncertify_removes_only_the_stamp(tmp_path):
    """A failed run must not delete entries other cells are still reading."""
    cdir = tmp_path / 'navmesh_geom_cache'
    cdir.mkdir()
    (cdir / 'CACHE_TAG').write_text('deadbeef')
    (cdir / '00000001_00000002.pkl').write_bytes(b'payload')
    assert navm_verify.uncertify((str(cdir), 'deadbeef')) is True
    assert not (cdir / 'CACHE_TAG').exists()
    assert (cdir / '00000001_00000002.pkl').exists()


def test_rekey_rewrites_only_the_hash(tmp_path):
    """Adoption changes the KEY, never the geometry."""
    path = tmp_path / 'e.pkl'
    blob = {'hash': 'old', 'verts': np.zeros((2, 3), dtype=np.float32),
            'tris': np.zeros((1, 3), dtype=np.int32), 'ledges': [(1, 2)]}
    with open(path, 'wb') as fh:
        pickle.dump(blob, fh)
    assert adopt.rekey(str(path), 'new') is True
    got = pickle.load(open(path, 'rb'))
    assert got['hash'] == 'new'
    assert np.array_equal(got['verts'], blob['verts'])
    assert np.array_equal(got['tris'], blob['tris'])
    assert got['ledges'] == [(1, 2)]
    assert adopt.rekey(str(path), 'new') is False


def test_environment_records_what_the_tag_cannot_see():
    """GEOS can change geometry without moving the source tag."""
    env = adopt.environment()
    assert 'python' in env
    assert 'shapely' in env and 'geos' in env


def test_adopt_skips_when_the_stamp_already_matches(tmp_path):
    """A cache this code built needs no adoption; prepare() must not rebuild."""
    cdir = tmp_path / 'navmesh_geom_cache'
    cdir.mkdir()
    (cdir / 'CACHE_TAG').write_text('tag123')
    (cdir / '00000001_00000002.pkl').write_bytes(b'x')
    assert navm_verify.adopt_if_unchanged([], (str(cdir), 'tag123')) is False


def test_adopt_declines_an_empty_cache(tmp_path):
    """Nothing to adopt when no entries exist -- the run must regenerate."""
    cdir = tmp_path / 'navmesh_geom_cache'
    cdir.mkdir()
    assert navm_verify.adopt_if_unchanged([], (str(cdir), 'newtag')) is False


def test_adopt_declines_when_verification_is_disabled(tmp_path, monkeypatch):
    """--navmesh-verify 0 opts out of adoption too, not just of re-checking."""
    cdir = tmp_path / 'navmesh_geom_cache'
    cdir.mkdir()
    (cdir / '00000001_00000002.pkl').write_bytes(b'x')
    monkeypatch.setenv(navm_verify.VERIFY_ENV_VAR, '0')
    assert navm_verify.adopt_if_unchanged([], (str(cdir), 'newtag')) is False


def test_prepare_is_a_noop_without_a_cache():
    """No geometry cache configured means nothing to prepare."""
    jobs = [_job('interior', 0)]
    navm_verify.prepare(jobs, None)
    assert not any(j.get('verify') for j in jobs)


def test_prepare_refuses_uninitialized_worker_context(tmp_path, monkeypatch):
    """Adoption must never rebuild its sample with empty worker globals."""
    from tes5_import import navm_worker

    geom_cache = (str(tmp_path), 'tag')
    monkeypatch.setattr(navm_worker, '_GEOM_CACHE', None)
    with pytest.raises(RuntimeError, match='initialized before cache prepare'):
        navm_verify.prepare([_job('interior')], geom_cache)
