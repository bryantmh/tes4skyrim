"""A keyless exporter transform stub is not animation.

Oblivion writes a NiTransformController entry into the Idle sequence for the
root and NonAccum node of nearly every mesh; on a static its interpolator
carries no NiTransformData, so the node never moves.  Treating those as
animation made the collision-hoist gate leave a static's collision on the
child NonAccum node instead of the root, where Skyrim expects it.

See: docs/commentary/asset_convert_collision.md#keyless-transform-stubs
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert.collision import collision_anim


class _Node(object):
    """A node identified only by name."""

    def __init__(self, name):
        """Hold the node's name as pyffi would, in bytes."""
        self.name = name


class _TransformData(object):
    """NiTransformData with the three key-count channels."""

    def __init__(self, rot=0, trans=0, scale=0):
        """Set each channel's key count."""
        self.num_rotation_keys = rot
        self.translations = type('K', (), {'num_keys': trans})()
        self.scales = type('K', (), {'num_keys': scale})()


class _Interp(object):
    """NiTransformInterpolator wrapping optional key data."""

    def __init__(self, data=None):
        """Hold the interpolator's data block, which may be None."""
        self.data = data


class _ControlledBlock(object):
    """One sequence entry naming a node and a controller type."""

    def __init__(self, node_name, ctrl_type, interp):
        """Hold what the sequence says this block drives."""
        self._node = node_name
        self._type = ctrl_type
        self.interpolator = interp

    def get_node_name(self):
        """The driven node's name."""
        return self._node

    def get_controller_type(self):
        """The controller's type name."""
        return self._type


class _Sequence(object):
    """A stand-in NiControllerSequence."""

    def __init__(self, blocks):
        """Hold the sequence's controlled blocks."""
        self.controlled_blocks = blocks


class _Data(object):
    """A stand-in NIF data object exposing `blocks`."""

    def __init__(self, blocks):
        """Hold every block in the file."""
        self.blocks = blocks


def _patch_sequence_type(monkeypatch):
    """Make isinstance(_Sequence, NiControllerSequence) hold for the test."""
    monkeypatch.setattr(collision_anim.NifFormat, 'NiControllerSequence',
                        _Sequence, raising=False)


def test_keyless_stub_is_not_animated(monkeypatch):
    """data=None means the node never moves."""
    _patch_sequence_type(monkeypatch)
    node = _Node(b'Candle NonAccum')
    seq = _Sequence([_ControlledBlock(b'Candle NonAccum',
                                      b'NiTransformController', _Interp(None))])
    assert not collision_anim.node_transform_is_animated(_Data([seq]), node)


def test_real_keys_are_animated(monkeypatch):
    """A single rotation key makes the node genuinely animated."""
    _patch_sequence_type(monkeypatch)
    node = _Node(b'Portcullis NonAccum')
    seq = _Sequence([_ControlledBlock(b'Portcullis NonAccum',
                                      b'NiTransformController',
                                      _Interp(_TransformData(rot=2)))])
    assert collision_anim.node_transform_is_animated(_Data([seq]), node)


def test_translation_only_keys_are_animated(monkeypatch):
    """Keys in any one channel count."""
    _patch_sequence_type(monkeypatch)
    node = _Node(b'Lift')
    seq = _Sequence([_ControlledBlock(b'Lift', b'NiTransformController',
                                      _Interp(_TransformData(trans=4)))])
    assert collision_anim.node_transform_is_animated(_Data([seq]), node)


def test_other_node_keys_do_not_count(monkeypatch):
    """Animation on a DIFFERENT node leaves this one static."""
    _patch_sequence_type(monkeypatch)
    node = _Node(b'Candle NonAccum')
    seq = _Sequence([_ControlledBlock(b'FlameNode0', b'NiTransformController',
                                      _Interp(_TransformData(rot=8)))])
    assert not collision_anim.node_transform_is_animated(_Data([seq]), node)


def test_non_transform_controller_does_not_count(monkeypatch):
    """A colour controller on the node is not motion."""
    _patch_sequence_type(monkeypatch)
    node = _Node(b'Candle NonAccum')
    seq = _Sequence([_ControlledBlock(b'Candle NonAccum',
                                      b'NiMaterialColorController',
                                      _Interp(_TransformData(rot=8)))])
    assert not collision_anim.node_transform_is_animated(_Data([seq]), node)
