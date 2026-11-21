"""Morroblivion texture substitution: the one map reaches every downstream mesh.

`prove_map` proves which vanilla textures Morroblivion's twin can replace and
writes the one map to `map_dir`. A plugin swaps only when it is the patch, masters
it, or was exported in hybrid mode, and the choice has to survive the
spawned mesh workers, which inherit the environment and nothing else.
See: docs/commentary/asset_convert_texture.md#morroblivion-texture-substitution
"""

import multiprocessing as mp
import os

import numpy as np
import pytest

from asset_convert.nif import morroblivion_textures as mbt
from asset_convert.nif.tex_paths import rewrite_tex_path
from tes4_export.morroblivion_texture_map import pair_set, proven_twins

#: The patch whose export carries the map.
PATCH = 'Morrowind-Morroblivion-Compatibility.esp'

#: Morroblivion's twin of `tx_hlaalu_wall2_01`, as the patch would prove it.
TWIN = 'textures\\morroblivion\\improved\\architecture\\hlaalu\\txuhlaaluuwall2u01.dds'


def _plugin(root, name, masters=()):
    """An export dir for `name` whose _HEADER.txt lists `masters`."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / '_HEADER.txt').write_text(
        ''.join('Master[%d]=%s\n' % (i, m) for i, m in enumerate(masters)),
        encoding='utf-8')
    return d


@pytest.fixture
def tree(tmp_path):
    """An export tree: Morroblivion, the patch with a map, and three consumers."""
    _plugin(tmp_path, 'Oblivion.esm')
    _plugin(tmp_path, 'Morrowind_ob.esm', ['Oblivion.esm'])
    _plugin(tmp_path, PATCH, ['Oblivion.esm', 'Morrowind_ob.esm'])
    mbt.write_map(mbt.map_dir(tmp_path), {'tx_hlaalu_wall2_01': TWIN})
    return {
        'map': mbt.map_dir(tmp_path),
        'patch': tmp_path / PATCH,
        'tamriel': _plugin(tmp_path, 'Tamriel_Data.esm',
                           ['Morrowind_ob.esm', PATCH]),
        'authored': _plugin(tmp_path, 'Morrowind.esm'),
        'knights': _plugin(tmp_path, 'Knights.esp', ['Oblivion.esm']),
    }


@pytest.fixture(autouse=True)
def _restore_env():
    """The active map is process-global, so no test may leak it into another."""
    before = os.environ.get(mbt.MAP_ENV)
    yield
    if before is None:
        os.environ.pop(mbt.MAP_ENV, None)
    else:
        os.environ[mbt.MAP_ENV] = before


def _spawned_substitute(raw):
    """Run in a SPAWNED worker: what `substitute` answers there."""
    return mbt.substitute(raw)


def _in_spawned_child(raw):
    """`substitute(raw)` as a freshly spawned mesh worker sees it."""
    with mp.get_context('spawn').Pool(1) as pool:
        return pool.map(_spawned_substitute, [raw])[0]


class TestWhoGetsTheMap:
    """Only a plugin downstream of the patch swaps."""

    def test_a_dependent_inherits_the_patch_map(self, tree):
        """Tamriel Data masters the patch, so it reads the one map."""
        assert mbt.map_folder(tree['tamriel']) == tree['map']

    def test_the_patch_uses_its_own(self, tree):
        """The patch's own vanilla meshes wear the twins too."""
        assert mbt.map_folder(tree['patch']) == tree['map']

    def test_authored_morrowind_never_swaps(self, tree):
        """Authored mode keeps vanilla art; Morroblivion's is not its own."""
        assert mbt.map_folder(tree['authored']) == ''

    def test_an_unrelated_plugin_never_swaps(self, tree):
        """An Oblivion dependent never reaches the Morrowind map."""
        assert mbt.map_folder(tree['knights']) == ''


class TestSubstitution:
    """A swap happens for a proven texture named by any mesh, never otherwise."""

    def test_nothing_swaps_without_a_map(self, tree):
        """Arming a plugin with no map clears whatever was active."""
        mbt.activate(tree['tamriel'])
        mbt.activate(tree['authored'])
        assert mbt.substitute(b'tx_hlaalu_wall2_01.tga') is None

    def test_a_proven_texture_swaps(self, tree):
        """The proven texture wears Morroblivion's twin."""
        mbt.activate(tree['tamriel'])
        assert mbt.substitute(b'tx_hlaalu_wall2_01.tga') == TWIN.encode()

    def test_an_unproven_texture_keeps_vanilla(self, tree):
        """A texture the patch never proved is never guessed at."""
        mbt.activate(tree['tamriel'])
        assert mbt.substitute(b'tx_not_in_the_map.tga') is None

    @pytest.mark.parametrize('raw', [b'Tx_Hlaalu_Wall2_01.dds',
                                     b'textures\\tx_hlaalu_wall2_01.tga',
                                     b'data/textures/tx_hlaalu_wall2_01.tga'])
    def test_spelling_does_not_matter(self, tree, raw):
        """Case, extension and a textures prefix name the same file."""
        mbt.activate(tree['tamriel'])
        assert mbt.substitute(raw) == TWIN.encode()

    def test_another_folder_is_another_texture(self, tree):
        """A plugin's own texture that shares a vanilla name keeps its art."""
        mbt.activate(tree['tamriel'])
        assert mbt.substitute(b'tr\\tx_hlaalu_wall2_01.dds') is None

    def test_the_twin_survives_the_namespace_rewrite(self, tree):
        """The swap lands where Morroblivion's own meshes point."""
        mbt.activate(tree['tamriel'])
        twin = mbt.substitute(b'tx_hlaalu_wall2_01.tga')
        assert rewrite_tex_path(twin).lower().startswith(
            'textures\\tes4\\morroblivion\\')

    def test_the_map_reaches_a_spawned_worker(self, tree):
        """Mesh conversion runs in spawned workers; module state never gets there."""
        mbt.activate(tree['tamriel'])
        assert _in_spawned_child(b'tx_hlaalu_wall2_01.tga') == TWIN.encode()

    def test_a_cleared_map_stays_cleared_in_a_worker(self, tree):
        """A later plugin without a map must not inherit the earlier one."""
        mbt.activate(tree['tamriel'])
        mbt.activate(tree['authored'])
        assert _in_spawned_child(b'tx_hlaalu_wall2_01.tga') is None


class TestMapFile:
    """The map file the patch writes is the one the converter reads."""

    def test_it_round_trips(self, tmp_path):
        """Every entry written comes back unchanged."""
        table = {'tx_a_b': 'textures\\x\\txuaub.dds', 'f\\tx_c': 'textures\\txuc.dds'}
        mbt.write_map(tmp_path, table)
        assert mbt.read_map(tmp_path) == table

    def test_a_folder_without_one_reads_empty(self, tmp_path):
        """No map is an empty map, never an error."""
        assert mbt.read_map(tmp_path) == {}


#: A vanilla mesh and Morroblivion's record-paired replacement of it, when exported.
VANILLA_WALL = 'export/Morrowind.esm/meshes/x/ex_hlaalu_wall_01.nif'
MORRO_WALL = 'export/Morrowind_ob.esm/meshes/morro/x/exuhlaaluuwallu01.nif'


@pytest.mark.skipif(not (os.path.isfile(VANILLA_WALL)
                         and os.path.isfile(MORRO_WALL)),
                    reason='needs the Morrowind and Morroblivion exports')
def test_the_hlaalu_wall_proves_all_four_twins():
    """Morroblivion reordered this mesh's shapes yet kept every UV: four proofs."""
    twins = dict(proven_twins(VANILLA_WALL, MORRO_WALL))
    assert sorted(twins) == ['tx_hlaalu_topedge_02', 'tx_hlaalu_wall2_01',
                             'tx_hlaalu_wall2_02', 'tx_hlaalu_wall2_03']


class TestPairSet:
    """The comparison that proves a twin: UV-sensitive, order-blind."""

    def test_a_permuted_shape_still_matches(self):
        """Morroblivion permutes vertices; that is not a UV difference."""
        verts = np.random.RandomState(0).rand(40, 3) * 100
        uv = np.random.RandomState(1).rand(40, 2)
        order = np.random.RandomState(2).permutation(40)
        assert pair_set(verts, uv) == pair_set(verts[order], uv[order])

    def test_a_shifted_uv_does_not(self):
        """A re-unwrapped shape must never read as the same shape."""
        verts = np.random.RandomState(0).rand(40, 3) * 100
        uv = np.random.RandomState(1).rand(40, 2)
        moved = uv.copy()
        moved[7, 0] += 0.01
        assert pair_set(verts, uv) != pair_set(verts, moved)

    def test_float32_noise_in_position_is_tolerated(self):
        """float32 storage makes identical geometry differ at 1e-4."""
        verts = np.array([[-111.2258, 50.1503, 0.3925]])
        uv = np.array([[0.0, 0.5343]])
        assert pair_set(verts, uv) == pair_set(verts + 1e-4, uv)
