"""A STAT whose mesh has a simulated body ships as MSTT.

Oblivion simulates a free dynamic body even on a STAT (the log trap knocks the
loose MiddleChestBrokenTop03 lid off its chest); Skyrim only simulates one on
MSTT/ACTI, so physics flag bit 0 must mark free bodies as well as constrained
islands, and convert_STAT must retype such a base.

See: docs/commentary/asset_convert_collision.md#stat-simulated-mstt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asset_convert.collision.collision_extract as ce
from tes5_import.record_types import items


class bhkRigidBody(object):
    """A converted Skyrim rigid body (class name is what the scan keys on)."""

    def __init__(self, motion, mass, constraints=0):
        """Hold the fields physics_flags_from_data reads."""
        self.motion_system = motion
        self.mass = mass
        self.num_constraints = constraints


class _Data(object):
    """A NIF data stand-in exposing `blocks`."""

    def __init__(self, *blocks):
        """Hold the file's blocks."""
        self.blocks = list(blocks)


def test_free_dynamic_body_sets_bit0():
    """The chest lid: one free box-inertia body with mass, no constraint."""
    assert ce.physics_flags_from_data(_Data(bhkRigidBody(3, 10.0))) == 1


def test_static_and_keyframed_bodies_do_not_set_bit0():
    """A fixed mass-0 body is scenery; a held keyframed body is bit 1 only."""
    assert ce.physics_flags_from_data(_Data(bhkRigidBody(5, 0.0))) == 0
    assert ce.physics_flags_from_data(_Data(bhkRigidBody(4, 0.0))) == 0
    assert ce.physics_flags_from_data(_Data(bhkRigidBody(4, 500.0))) == 2


def test_stat_with_simulated_body_is_written_as_mstt(monkeypatch):
    """Bit 0 retypes the base to MSTT; the FormID stays."""
    rec = {'Signature': 'STAT', 'FormID': '0009D9A3', 'RecordFlags': '0',
           'EditorID': 'MiddleChestBrokenTop03',
           'Model.MODL': 'Clutter\\MiddleClass\\MiddleChestBrokenTop03.NIF'}
    monkeypatch.setattr(items, 'get_mesh_physics_flags', lambda _k: 1)
    assert items.convert_STAT(rec)[:4] == b'MSTT'
    monkeypatch.setattr(items, 'get_mesh_physics_flags', lambda _k: 0)
    assert items.convert_STAT(rec)[:4] == b'STAT'
