"""The mesh-safety screen must never cache a verdict for a file that is
not there yet.

LODGen casts every listed LOD mesh's root block to NiNode without checking, and
one bad mesh kills the WHOLE worldspace — so each mesh gets a header read first
and the result is memoised.  The memo is warmed concurrently before the serial
loop, because those reads are pure file-open latency.

The trap is that the serial loop STAGES meshes into the tree it screens
(`_import_master_mesh` copies a master-owned mesh in, because LODGen resolves
everything under one PathData root) and screens them immediately afterwards.
So a mesh's existence is not stable across the warm-up: caching "missing =
unsafe" up front answers the later question with a stale verdict.

That went unnoticed while `output_dir` was the plugin's own output — its meshes
were already there and only master-owned ones were ever staged.  Once
`e4b8d91` made `output_dir` the shared LOD mod, that tree is EMPTY at warm-up
time, every mesh cached as unsafe, and all 18 worldspaces ended with
"No LOD references found".
"""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.lod import lod_gen


@pytest.fixture
def screen(monkeypatch):
    """Isolate the cache and stub the header read.

    What is under test is the RESOLUTION and CACHING, not the NIF header
    parser, so the parser is replaced by "a file that exists is fine".  That
    keeps the test from needing a hand-built NIF and makes a failure point at
    the logic that actually changed.
    """
    monkeypatch.setattr(lod_gen, '_NIF_ROOT_SAFE_CACHE', {})
    monkeypatch.setattr(lod_gen, '_root_is_ninode',
                        lambda full: Path(full).exists())
    return lod_gen


REL = 'architecture\\lazeon\\wall01.nif'
REL2 = 'architecture\\lazeon\\wall02.nif'


def _tree(tmp_path):
    """An empty shared LOD mod beside a plugin output that owns the meshes.

    Two meshes, because `_prescreen_meshes` does not spin up a thread pool for
    a single file — one would exercise the on-demand path instead of the
    warm-up this is about.
    """
    out = tmp_path / 'AutoConvertLOD' / 'meshes'
    src = tmp_path / 'Nehrim.esm' / 'meshes'
    (src / 'architecture' / 'lazeon').mkdir(parents=True)
    for rel in (REL, REL2):
        (src / rel.replace('\\', '/')).write_bytes(b'NIF')
    out.mkdir(parents=True)
    return out, src


def _stage(out, src):
    dst = out / 'architecture' / 'lazeon' / 'wall01.nif'
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src / 'architecture' / 'lazeon' / 'wall01.nif', dst)


class TestScreenDoesNotCacheAbsence:

    def test_a_mesh_staged_after_the_warm_up_is_still_judged_safe(
            self, screen, tmp_path):
        """The regression, in the order it actually happens.

        Warm-up runs against an empty tree, the loop stages the mesh, then
        screens it. Before the fix the screen answered from a cached False and
        the object was dropped — with its worldspace, once enough of them were.
        """
        out, src = _tree(tmp_path)
        screen._prescreen_meshes([REL, REL2], out)          # no source dirs
        _stage(out, src)
        assert screen._lod_mesh_is_safe(REL, out) is True

    def test_the_warm_up_reads_the_source_tree_when_this_one_is_empty(
            self, screen, tmp_path):
        """...and it must still be a warm-up, not a no-op.

        `_import_master_mesh` stages with `shutil.copy2`, so the source and the
        staged copy are byte-identical and screening either gives the same
        answer. Reading the source is what keeps the concurrent prefetch worth
        doing in the shared-LOD-mod case, where nothing is staged yet.
        """
        out, src = _tree(tmp_path)
        screen._prescreen_meshes([REL, REL2], out, source_meshes=[src])
        key = str(screen._mesh_screen_path(REL, out)).lower()
        assert screen._NIF_ROOT_SAFE_CACHE.get(key) is True, \
            'source not consulted — the prefetch degenerates to a no-op'

    def test_a_mesh_in_no_tree_at_all_is_not_cached(self, screen, tmp_path):
        out, src = _tree(tmp_path)
        screen._prescreen_meshes([REL, REL2, 'nowhere\\b.nif'], out,
                                 source_meshes=[src])
        gone = str(screen._mesh_screen_path('nowhere\\b.nif', out)).lower()
        assert gone not in screen._NIF_ROOT_SAFE_CACHE
        # and it still reads as unsafe on demand, which is the safe answer
        assert screen._lod_mesh_is_safe('nowhere\\b.nif', out) is False
        assert gone not in screen._NIF_ROOT_SAFE_CACHE, \
            'an absent mesh must never be memoised — it may be staged later'

    def test_a_real_verdict_is_still_cached(self, screen, tmp_path):
        """The memo has to keep working, or Tamriel's 2,582 header reads go
        back to being serial file-open latency."""
        out, src = _tree(tmp_path)
        _stage(out, src)
        assert screen._lod_mesh_is_safe(REL, out) is True
        key = str(screen._mesh_screen_path(REL, out)).lower()
        assert screen._NIF_ROOT_SAFE_CACHE[key] is True
        # cached, so a later deletion cannot change the answer within one bake
        (out / 'architecture' / 'lazeon' / 'wall01.nif').unlink()
        assert screen._lod_mesh_is_safe(REL, out) is True

    def test_an_unsafe_mesh_is_still_rejected(self, screen, tmp_path,
                                              monkeypatch):
        """The screen's actual job: a root LODGen cannot cast is excluded.

        The fix must not turn the screen into a mere existence check — a mesh
        that IS there and has a geometry root still has to be dropped, or one
        of them aborts the bake and the worldspace loses all its object LOD.
        """
        out, src = _tree(tmp_path)
        _stage(out, src)
        monkeypatch.setattr(screen, '_root_is_ninode', lambda full: False)
        assert screen._lod_mesh_is_safe(REL, out) is False
