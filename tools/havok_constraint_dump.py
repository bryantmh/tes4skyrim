#!/usr/bin/env python3
"""Dump Havok physics detail from NIF files: rigid-body filter/solver fields
and full constraint descriptors.  Complements tes5_nif_analyzer.py (which
prints the scene tree but hides collision-filter and constraint internals).

Usage:
    python tools/havok_constraint_dump.py <nif> [<nif> ...]
    python tools/havok_constraint_dump.py <dir>          # all NIFs with constraints

Prints per rigid body: owning node, class, layer (name+value), col-filter
flags/part/group, mass, inertia diagonal, damping, motion system, quality,
solver deactivation, broadphase byte, and per constraint: type, entities,
priority, and every descriptor field (pivots, axes, limits, friction).
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from asset_convert import pyffi_monkey_patch  # noqa: F401  (clock patch)
from pyffi.formats.nif import NifFormat

SKYRIM_LAYERS = {
    0: 'UNIDENTIFIED', 1: 'STATIC', 2: 'ANIMSTATIC', 3: 'TRANSPARENT',
    4: 'CLUTTER', 5: 'WEAPON', 6: 'PROJECTILE', 7: 'SPELL', 8: 'BIPED',
    9: 'TREES', 10: 'PROPS', 11: 'WATER', 12: 'TRIGGER', 13: 'TERRAIN',
    14: 'TRAP', 15: 'NONCOLLIDABLE', 16: 'CLOUD_TRAP', 17: 'GROUND',
    18: 'PORTAL', 19: 'DEBRIS_SMALL', 20: 'DEBRIS_LARGE', 21: 'ACOUSTIC_SPACE',
    22: 'ACTORZONE', 23: 'PROJECTILEZONE', 24: 'GASTRAP', 25: 'SHELLCASING',
    26: 'TRANSPARENT_SMALL', 27: 'INVISIBLE_WALL', 28: 'TRANSPARENT_SMALL_ANIM',
    29: 'WARD', 30: 'CHARCONTROLLER', 31: 'STAIRHELPER', 32: 'DEADBIP',
    33: 'BIPED_NO_CC', 34: 'AVOIDBOX', 35: 'COLLISIONBOX',
    36: 'CAMERASPHERE', 37: 'DOORDETECTION', 38: 'CONEPROJECTILE',
    39: 'CAMERAPICK', 40: 'ITEMPICK', 41: 'LINEOFSIGHT', 42: 'PATHPICK',
    43: 'CUSTOMPICK1', 44: 'CUSTOMPICK2', 45: 'SPELLEXPLOSION',
    46: 'DROPPINGPICK',
}


def _vec(v, n=3):
    if v is None:
        return 'None'
    if n == 4:
        return '(%.4f, %.4f, %.4f, %.4f)' % (v.x, v.y, v.z, v.w)
    return '(%.4f, %.4f, %.4f)' % (v.x, v.y, v.z)


def _filter_str(hf):
    if hf is None:
        return 'None'
    layer = getattr(hf, 'layer', None)
    lname = SKYRIM_LAYERS.get(int(layer), '?') if layer is not None else '?'
    return 'layer=%s(%s) flagsAndParts=%s group=%s' % (
        layer, lname,
        getattr(hf, 'flags_and_part_number', getattr(hf, 'flags', '?')),
        getattr(hf, 'group', getattr(hf, 'unknown_short', '?')))


_DESC_FIELDS = {
    'limited_hinge': ['pivot_a', 'axle_a', 'perp_2_axle_in_a_1', 'perp_2_axle_in_a_2',
                      'pivot_b', 'axle_b', 'perp_2_axle_in_b_1', 'perp_2_axle_in_b_2',
                      'min_angle', 'max_angle', 'max_friction'],
    'hinge': ['pivot_a', 'perp_2_axle_in_a_1', 'perp_2_axle_in_a_2', 'axle_a',
              'pivot_b', 'axle_b', 'perp_2_axle_in_b_1', 'perp_2_axle_in_b_2'],
    'ragdoll': ['pivot_a', 'plane_a', 'twist_a', 'motor_a',
                'pivot_b', 'plane_b', 'twist_b', 'motor_b',
                'cone_max_angle', 'plane_min_angle', 'plane_max_angle',
                'twist_min_angle', 'twist_max_angle', 'max_friction'],
    'ball_and_socket': ['pivot_a', 'pivot_b'],
    'stiff_spring': ['pivot_a', 'pivot_b', 'length'],
    'prismatic': ['pivot_a', 'rotation_a', 'sliding_a', 'plane_a',
                  'pivot_b', 'rotation_b', 'sliding_b', 'plane_b',
                  'min_distance', 'max_distance', 'friction'],
}


def _dump_descriptor(kind, d, indent):
    pad = ' ' * indent
    for f in _DESC_FIELDS.get(kind, []):
        v = getattr(d, f, None)
        if v is None:
            continue
        if hasattr(v, 'x'):
            print('%s%s = %s' % (pad, f, _vec(v, 4 if hasattr(v, 'w') else 3)))
        else:
            print('%s%s = %.4f' % (pad, f, float(v)))


def _dump_constraint(c, node_of_body, indent=6):
    pad = ' ' * indent
    print('%s%s priority=%s' % (pad, type(c).__name__,
                                getattr(c, 'priority', '?')))
    ents = getattr(c, 'entities', [])
    for e in ents:
        nm = node_of_body.get(id(e), '<unlinked>') if e is not None else 'None'
        print('%s  entity -> body of node "%s"' % (pad, nm))
    # Malleable wrapper: recurse into sub constraint
    sub = getattr(c, 'sub_constraint', None)
    if sub is not None:
        print('%s  sub_constraint type=%s' % (pad, getattr(sub, 'type', '?')))
        for kind in _DESC_FIELDS:
            d = getattr(sub, kind, None)
            if d is not None:
                _dump_descriptor(kind, d, indent + 4)
        strength = getattr(c, 'strength', None) or getattr(sub, 'strength', None)
        if strength is not None:
            print('%s  strength = %.4f' % (pad, strength))
        return
    for kind in _DESC_FIELDS:
        d = getattr(c, kind, None)
        if d is not None:
            print('%s  [%s]' % (pad, kind))
            _dump_descriptor(kind, d, indent + 4)
    # Chain constraints (bhkBallSocketConstraintChain)
    pivots = getattr(c, 'pivots', None)
    if pivots is not None:
        for i, p in enumerate(pivots):
            print('%s  pivot[%d] = %s' % (pad, i, _vec(p, 4)))
    for f in ('tau', 'damping', 'constraint_force_mixing', 'max_error_distance'):
        v = getattr(c, f, None)
        if v is not None:
            print('%s  %s = %.4f' % (pad, f, float(v)))


def dump_nif(path):
    print('=' * 78)
    print('NIF:', path)
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.read(f)
    print('Version: 0x%08X UV: %d UV2: %d' % (data.version, data.user_version,
                                              data.user_version_2))
    # Map rigid bodies back to owning node names
    node_of_body = {}
    for b in data.blocks:
        if isinstance(b, NifFormat.NiAVObject) and getattr(b, 'collision_object', None) is not None:
            body = getattr(b.collision_object, 'body', None)
            if body is not None:
                node_of_body[id(body)] = b.name.decode('latin-1') if isinstance(b.name, bytes) else str(b.name)

    # Phantoms (trigger volumes) — under bhkSPCollisionObject
    for b in data.blocks:
        if not isinstance(b, NifFormat.NiAVObject):
            continue
        co = getattr(b, 'collision_object', None)
        if co is None:
            continue
        body = getattr(co, 'body', None)
        if body is None or isinstance(body, NifFormat.bhkRigidBody):
            continue
        nm = b.name.decode('latin-1') if isinstance(b.name, bytes) else str(b.name)
        print('-' * 78)
        print('Phantom (%s under %s) on node "%s"  collFlags=%s' % (
            type(body).__name__, type(co).__name__, nm, getattr(co, 'flags', '?')))
        print('  filter: %s' % _filter_str(getattr(body, 'havok_col_filter', None)))
        bp = getattr(body, 'broad_phase_type', getattr(body, 'unknown_byte', None))
        print('  broadphase=%s' % bp)
        tf = getattr(body, 'transform', None)
        if tf is not None:
            print('  transform trans=(%.4f, %.4f, %.4f) m44=%.4f' % (
                tf.m_14, tf.m_24, tf.m_34, tf.m_44))
            print('  transform rot rows: [%.3f %.3f %.3f][%.3f %.3f %.3f][%.3f %.3f %.3f]' % (
                tf.m_11, tf.m_12, tf.m_13, tf.m_21, tf.m_22, tf.m_23,
                tf.m_31, tf.m_32, tf.m_33))
        shp = getattr(body, 'shape', None)
        if shp is not None:
            print('  shape: %s' % type(shp).__name__)
            if hasattr(shp, 'dimensions'):
                d = shp.dimensions
                print('    dims=(%.4f, %.4f, %.4f) radius=%.4f' % (
                    d.x, d.y, d.z, getattr(shp, 'radius', 0.0)))

    seen_constraints = set()
    for b in data.blocks:
        if not isinstance(b, NifFormat.bhkRigidBody):
            continue
        nm = node_of_body.get(id(b), '<orphan>')
        print('-' * 78)
        print('Body (%s) on node "%s"' % (type(b).__name__, nm))
        print('  filter: %s' % _filter_str(getattr(b, 'havok_col_filter', None)))
        print('  filter_copy: %s' % _filter_str(getattr(b, 'havok_col_filter_copy', None)))
        print('  mass=%.4f  inertiaDiag=(%.5f, %.5f, %.5f)' % (
            b.mass, b.inertia.m_11, b.inertia.m_22, b.inertia.m_33))
        print('  inertiaOffDiag=(%.5f, %.5f, %.5f)' % (
            b.inertia.m_12, b.inertia.m_13, b.inertia.m_23))
        print('  center=%s  translation=%s' % (_vec(b.center, 4), _vec(b.translation, 4)))
        print('  rotation=(%.4f, %.4f, %.4f, %.4f)' % (
            b.rotation.x, b.rotation.y, b.rotation.z, b.rotation.w))
        print('  linDamp=%.4f angDamp=%.4f friction=%.3f restitution=%.3f' % (
            b.linear_damping, b.angular_damping, b.friction, b.restitution))
        print('  motionSystem=%d qualityType=%d deactivator=%d solverDeact=%d' % (
            b.motion_system, b.quality_type, b.deactivator_type,
            b.solver_deactivation))
        print('  broadphase(unknown_byte)=%s  maxLinVel=%.2f maxAngVel=%.2f' % (
            getattr(b, 'unknown_byte', '?'), b.max_linear_velocity,
            b.max_angular_velocity))
        n = getattr(b, 'num_constraints', 0)
        if n:
            print('  Constraints (%d):' % n)
            for c in b.constraints:
                if c is None:
                    continue
                _dump_constraint(c, node_of_body)
                seen_constraints.add(id(c))

    # Orphan constraints (not referenced from any body constraint list)
    for b in data.blocks:
        if isinstance(b, NifFormat.bhkConstraint) and id(b) not in seen_constraints:
            print('-' * 78)
            print('ORPHAN constraint (in file but not in any body constraint list):')
            _dump_constraint(b, node_of_body, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('paths', nargs='+', help='NIF files or directories')
    args = ap.parse_args()
    files = []
    for p in args.paths:
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                files += [os.path.join(root, n) for n in names
                          if n.lower().endswith('.nif')]
        else:
            files.append(p)
    for f in files:
        try:
            dump_nif(f)
        except Exception as e:
            print('ERROR reading %s: %r' % (f, e))


if __name__ == '__main__':
    main()
