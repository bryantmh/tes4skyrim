"""Held bodies: loose trap/breakaway pieces a script releases to fall.

A cave-in's rocks and a log trap's logs are Oblivion ms=6 bodies with mass on
OL_TRAP whose clip only pins them; they must ship held (keyframed, mass kept)
so ReleaseBreakaway can drop them.  A scoped mesh rebuild's physics flags must
also reach the bounds cache, or the script stage never emits that release.

See: docs/commentary/asset_convert_collision.md#held-bodies
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asset_convert.collision.collision_extract as ce
from asset_convert.collision import collision, collision_anim
from asset_convert.collision import mesh_scan_fragments as frags
from tes5_import import pipeline


class _Vec(object):
    """A translation key value."""

    def __init__(self, x):
        """Place the key on the X axis."""
        self.x, self.y, self.z = x, 0.0, 0.0


class _Quat(object):
    """A rotation key value."""

    def __init__(self, w):
        """Hold an identity-ish quaternion varying in w."""
        self.w, self.x, self.y, self.z = w, 0.0, 0.0, 0.0


class _Key(object):
    """One animation key."""

    def __init__(self, value):
        """Hold the key's value."""
        self.value = value


class _TransformData(object):
    """NiTransformData with quaternion rotation and translation keys."""

    def __init__(self, trans=(), rot=()):
        """Build the key lists from plain values."""
        self.translations = type('T', (), {'keys': [_Key(_Vec(v)) for v in trans]})()
        self.rotation_type = 1
        self.quaternion_keys = [_Key(_Quat(w)) for w in rot]


class _Interp(object):
    """A NiTransformInterpolator stand-in."""

    def __init__(self, data):
        """Hold the key data."""
        self.data = data


class _Block(object):
    """One controlled block driving a node's transform."""

    def __init__(self, node, data):
        """Name the driven node and its keys."""
        self._node = node
        self.interpolator = _Interp(data)

    def get_node_name(self):
        """The driven node's name."""
        return self._node

    def get_controller_type(self):
        """Always a transform controller."""
        return b'NiTransformController'


class _Filter(object):
    """A havok collision filter."""

    def __init__(self, layer):
        """Hold the Oblivion layer."""
        self.layer = layer


class _Body(object):
    """An Oblivion rigid body."""

    def __init__(self, layer, mass=500.0, ms=6):
        """Keyframed, with mass, on `layer`, owning no constraint."""
        self.motion_system = ms
        self.mass = mass
        self.num_constraints = 0
        self.havok_col_filter = _Filter(layer)
        self.havok_col_filter_copy = _Filter(layer)


class _Node(object):
    """A NiNode stand-in with optional collision and children."""

    def __init__(self, name, body=None, children=(), controller=None):
        """Wire up the node."""
        self.name = name
        self.collision_object = type('C', (), {'body': body})() if body else None
        self.children = list(children)
        self.controller = controller

    def tree(self):
        """This node and every descendant."""
        out = [self]
        for c in self.children:
            out += c.tree()
        return out


def _mesh(piece, blocks):
    """A root holding `piece` and one sequence with `blocks`."""
    seq = type('S', (), {'controlled_blocks': blocks})()
    mgr = type('M', (), {'controller_sequences': [seq], 'next_controller': None})()
    return _Node(b'Root', children=[piece], controller=mgr)


def _patch(monkeypatch):
    """Make the stand-in interpolator pass the NiTransformInterpolator check."""
    monkeypatch.setattr(collision_anim.NifFormat, 'NiTransformInterpolator',
                        _Interp, raising=False)


def test_pinned_trap_piece_is_held(monkeypatch):
    """ctrapcavein01: an OL_TRAP rock whose keys hold one pose falls on release."""
    _patch(monkeypatch)
    rock = _Node(b'Stone16', _Body(14))
    root = _mesh(rock, [_Block(b'Stone16', _TransformData(rot=(1.0, 1.0)))])
    assert collision._body_is_held(rock, root, rock.collision_object.body)
    assert collision.mesh_has_held_body(root)


def test_undriven_trap_piece_is_held(monkeypatch):
    """ctraplogs01: an OL_TRAP log in no clip, in a mesh that has one, falls too."""
    _patch(monkeypatch)
    log = _Node(b'Log01', _Body(14, mass=20.0))
    root = _mesh(log, [_Block(b'Log02', _TransformData(rot=(1.0,)))])
    assert collision._body_is_held(log, root, log.collision_object.body)


def test_moved_trap_piece_stays_keyframed(monkeypatch):
    """A self-actuating trap (spikes, ceiling traps) follows its clip."""
    _patch(monkeypatch)
    spike = _Node(b'Spike', _Body(14))
    root = _mesh(spike, [_Block(b'Spike', _TransformData(trans=(0.0, 40.0)))])
    assert not collision._body_is_held(spike, root, spike.collision_object.body)


def test_anim_static_piece_is_never_held(monkeypatch):
    """Gates and portcullises sit on the anim-static layers."""
    _patch(monkeypatch)
    gate = _Node(b'Gate', _Body(2))
    root = _mesh(gate, [_Block(b'Gate', _TransformData(rot=(1.0, 1.0)))])
    assert not collision._body_is_held(gate, root, gate.collision_object.body)


def test_trap_piece_without_any_clip_is_not_held():
    """No sequence means nothing ever scripts a release."""
    rock = _Node(b'Stone', _Body(14))
    root = _Node(b'Root', children=[rock])
    assert not collision._body_is_held(rock, root, rock.collision_object.body)


def _write_caches(assets, bounds):
    """Current bounds + collision caches holding `bounds`."""
    payload = {k: list(v) for k, v in bounds.items()}
    payload['__schema__'] = [ce.BOUNDS_SCHEMA_VERSION]
    (assets / 'mesh_bounds_cache.json').write_text(json.dumps(payload))
    soup = {'w': [0.0] * 9, 'b': []}
    (assets / 'collision_cache.bin').write_bytes(
        ce._serialize({k: soup for k in bounds}))


def test_scoped_rebuild_reaches_a_current_cache(tmp_path, monkeypatch):
    """A current cache still takes a later mesh run's fragments (the HELD bit)."""
    meshes = tmp_path / 'meshes'
    (meshes / 'traps').mkdir(parents=True)
    for name in ('cavein.nif', 'other.nif'):
        (meshes / 'traps' / name).write_bytes(b'')
    assets = tmp_path / 'assets'
    assets.mkdir()
    _write_caches(assets, {'traps/cavein.nif': (0, 0, 0, 1, 1, 1),
                           'traps/other.nif': (0, 0, 0, 2, 2, 2),
                           'traps/gone.nif': (0, 0, 0, 3, 3, 3)})
    frags.set_fragment_dir(assets)
    frags.record_mesh_entry('traps/cavein.nif', (0, 0, 0, 1, 1, 1), 2,
                            {'w': [1.0] * 9, 'b': []})
    frags.close_fragments()
    monkeypatch.setattr(pipeline, 'assets_for', lambda _d: Path(assets))

    assert pipeline._rescan_mesh_caches('unused', str(meshes))

    cache = json.loads((assets / 'mesh_bounds_cache.json').read_text())
    assert cache['traps/cavein.nif'] == [0, 0, 0, 1, 1, 1, 2]
    assert cache['traps/other.nif'] == [0, 0, 0, 2, 2, 2]
    assert 'traps/gone.nif' not in cache
    assert ce.collision_cache_is_current(str(assets / 'collision_cache.bin'))
    assert not os.path.isdir(frags.fragment_dir(assets)) or \
        not os.listdir(frags.fragment_dir(assets))
    assert not pipeline._rescan_mesh_caches('unused', str(meshes))
