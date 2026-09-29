"""Terrain-LOD landscape textures are found case-blind and every lookup is counted.

Records spell textures in the author's case (`tes4\\Landscape\\...`) while the
converted tree is lowercase, so a verbatim join missed every real texture on
a case-sensitive filesystem and the compositor painted neutral grey. The loader now resolves
case-blind (exact first), and each lookup -- cache hits included -- is counted
so a run that found nothing fails instead of shipping grey tiles.

See: docs/commentary/asset_convert_terrain.md#terrain-lod-texture-counts
"""

from collections import Counter

import numpy as np
import pytest
from PIL import Image

from asset_convert import case_paths
from asset_convert.lod import terrain_lod
from asset_convert.lod import terrain_lod_textures as tlt

RED = (200, 10, 10)
GREEN = (10, 200, 10)


@pytest.fixture(autouse=True)
def _fresh():
    """Empty the texture cache, the counters and the folder listings."""
    tlt._TEX_CACHE.clear()
    tlt.texture_stats()
    case_paths.invalidate()
    yield
    tlt._TEX_CACHE.clear()
    tlt.texture_stats()
    case_paths.invalidate()


def _tex(path, rgb):
    """Write a solid 8x8 image at `path` (PIL reads it by content, not name)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8), rgb).save(path, format='PNG')


@pytest.mark.usefixtures('case_twins')
def test_mixed_case_name_finds_the_file_past_an_empty_case_twin(tmp_path):
    """An empty `Dementia/` listed before `dementia/x.dds` must not hide it."""
    (tmp_path / 'tes4' / 'landscape' / 'Dementia').mkdir(parents=True)
    _tex(tmp_path / 'tes4' / 'landscape' / 'dementia' / 'x.dds', RED)

    img = tlt.load_texture_rgb('textures\\tes4\\Landscape\\Dementia\\X.dds',
                               [tmp_path], 8)

    assert tuple(img[4, 4]) == RED, 'the real texel, not the grey fallback'
    assert tlt.texture_stats() == Counter(
        {('tes4\\landscape\\dementia\\x.dds', 'resolved'): 1})


@pytest.mark.usefixtures('case_twins')
def test_exact_spelling_wins_over_a_case_variant(tmp_path):
    """Two spellings on disk: the one the record names exactly is used."""
    _tex(tmp_path / 'tes4' / 'A' / 'b.dds', RED)
    _tex(tmp_path / 'tes4' / 'a' / 'b.dds', GREEN)

    assert tuple(tlt.load_texture_rgb('tes4\\A\\b.dds', tmp_path, 8)[0, 0]) == RED
    tlt._TEX_CACHE.clear()
    assert tuple(tlt.load_texture_rgb('tes4\\a\\b.dds', tmp_path, 8)[0, 0]) == GREEN
    assert {oc for (_p, oc) in tlt.texture_stats()} == {'exact'}


def test_root_order_is_kept_across_case(tmp_path):
    """A case-blind hit in the first root beats an exact one in the second."""
    first, second = tmp_path / 'own', tmp_path / 'master'
    _tex(first / 'tes4' / 'landscape' / 'grass.dds', RED)
    _tex(second / 'tes4' / 'Landscape' / 'Grass.dds', GREEN)

    img = tlt.load_texture_rgb('tes4\\Landscape\\Grass.dds', [first, second], 8)

    assert tuple(img[0, 0]) == RED


def test_misses_and_cache_hits_are_all_counted(tmp_path):
    """Every lookup counts, so a cached miss still shows in the report."""
    _tex(tmp_path / 'tes4' / 'landscape' / 'dirt.dds', RED)
    for _ in range(3):
        tlt.load_texture_rgb('tes4\\Landscape\\Nope.dds', tmp_path, 8)
    tlt.load_texture_rgb('tes4\\landscape\\dirt.dds', tmp_path, 8)

    stats = tlt.texture_stats()
    assert stats[('tes4\\landscape\\nope.dds', 'missing')] == 3
    assert stats[('tes4\\landscape\\dirt.dds', 'exact')] == 1
    assert tlt.texture_stats() == Counter(), 'a snapshot resets the counters'

    lines, ok = tlt.texture_report(stats)
    assert ok, 'a partial miss is reported, not fatal'
    assert '2 requested' in lines[0] and '1 missing' in lines[0]
    assert any('nope.dds (3 lookups)' in line for line in lines)


def test_nothing_found_is_a_failure_and_nothing_requested_is_not():
    """`exact + resolved == 0` with requests fails; no requests passes."""
    _lines, ok = tlt.texture_report(Counter({('a.dds', 'missing'): 5,
                                             ('b.dds', 'decode_error'): 1}))
    assert not ok
    assert tlt.texture_report(Counter())[1]


def test_worker_deltas_merge_and_an_all_grey_bake_fails(capsys):
    """The parent sums per-tile deltas and refuses a run that found nothing."""
    results = [('t.4.0.0', True, None, {'textures': Counter({('a.dds', 'missing'): 2})}),
               ('t.4.4.0', True, None, {'textures': Counter({('a.dds', 'missing'): 1})})]
    work = [(0, 0, 4, 't'), (4, 0, 4, 't')]

    per_level, failed, stats = terrain_lod._collect(results, work)

    assert per_level == {4: 2} and failed == 0
    assert stats['textures'][('a.dds', 'missing')] == 3
    assert terrain_lod._report_bake(per_level, failed, stats) is False
    assert 'ERROR' in capsys.readouterr().out


def test_unreadable_texture_is_counted_as_such(tmp_path):
    """A file that exists but will not decode is grey AND counted unreadable."""
    bad = tmp_path / 'tes4' / 'landscape' / 'bad.dds'
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b'not an image')

    img = tlt.load_texture_rgb('tes4\\landscape\\bad.dds', tmp_path, 8)

    assert np.all(img == 128)
    assert tlt.texture_stats() == Counter(
        {('tes4\\landscape\\bad.dds', 'decode_error'): 1})
