"""A transform controller on a mesh's root moves to an inner node, leaving the root to the placement.

See: docs/commentary/asset_convert_nif.md#root-transform-controller
"""
import pytest

from asset_convert.nif import grass_profile, nif_converter
from asset_convert.nif.nif_passes import ROOT_MOVER_SUFFIX, move_root_controller
from pyffi.formats.nif import NifFormat

#: NiTimeController.flags of the cases below: Active + clamp, clamp alone, and the manager bit.
ACTIVE, INACTIVE, MANAGED = 0x0C, 0x04, 0x20

#: Bit 3 of NiTimeController.flags, Active.
ACTIVE_BIT = 0x08

#: A quarter turn about Z, as the rows of a root's rotation.
QUARTER_TURN = ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))


def _node(name, rows=None):
    """A NiNode at identity, or turned by `rows`."""
    node = NifFormat.NiNode()
    node.name = name
    node.scale = 1.0
    node.rotation.set_identity()
    for r, row in enumerate(rows or (), 1):
        for c, value in enumerate(row, 1):
            setattr(node.rotation, 'm_%d%d' % (r, c), value)
    return node


def _keys(group, values):
    """Fill one key group with linear keys a second apart."""
    group.num_keys = len(values)
    group.interpolation = 1
    group.keys.update_size()
    for i, value in enumerate(values):
        group.keys[i].time = float(i)
        if isinstance(value, tuple):
            group.keys[i].value.x, group.keys[i].value.y, group.keys[i].value.z = value
        else:
            group.keys[i].value = value


def _transform_controller(flags, turn=(), slide=(), grow=(), data=True):
    """A NiTransformController keyed with Z angles `turn`, positions `slide` and scales `grow`."""
    ctrl = NifFormat.NiTransformController()
    ctrl.flags = flags
    ctrl.frequency = 1.0
    ctrl.stop_time = 2.0
    ctrl.interpolator = NifFormat.NiTransformInterpolator()
    ctrl.interpolator.scale = 1.0
    ctrl.interpolator.rotation.w = 1.0
    if data:
        keys = ctrl.interpolator.data = NifFormat.NiTransformData()
        keys.rotation_type = 4
        keys.num_rotation_keys = 1 if turn else 0
        _keys(keys.xyz_rotations[2], turn)
        _keys(keys.translations, slide)
        _keys(keys.scales, grow)
    return ctrl


def _box_collision(node, motion):
    """Give `node` a box body of Oblivion motion system `motion` (7 fixed, 6 keyframed)."""
    node.collision_object = NifFormat.bhkCollisionObject()
    node.collision_object.target = node
    body = node.collision_object.body = NifFormat.bhkRigidBody()
    body.motion_system = motion
    body.shape = NifFormat.bhkBoxShape()
    body.shape.dimensions.x = body.shape.dimensions.y = body.shape.dimensions.z = 1.0


def _mesh(flags=ACTIVE, rows=None, collision=0, part=b'Part', **keys):
    """(data, controller) of a source mesh whose root `Top` carries a transform controller.

    `collision` is the motion system of a body on the child `part`, 0 for none.
    """
    root = _node(b'Top', rows)
    root.add_child(_node(part))
    if collision:
        _box_collision(root.children[0], collision)
    ctrl = root.controller = _transform_controller(flags, **keys)
    ctrl.target = root
    data = NifFormat.Data(version=0x14000005, user_version=11, user_version_2=11)
    data.roots = [root]
    return data, ctrl


def _convert(**mesh):
    """(root, controller, stats) after the converter ran over `_mesh(**mesh)`."""
    data, ctrl = _mesh(**mesh)
    stats = nif_converter._convert_nif(data, src_path='mesh.nif')
    return data.roots[0], ctrl, stats


def _names(node):
    """The names of a node's children."""
    return [bytes(c.name) for c in node.children if c is not None]


class TestRootControllerMovesInward:
    """The root ends without a controller; one inner node holds it and the root's children."""

    def test_an_active_turning_controller_moves(self):
        """The controller, still active, sits on the new node with the root's children."""
        root, ctrl, stats = _convert(turn=(0.0, 0.7))
        mover = root.children[0]
        assert root.controller is None and _names(root) == [b'Top' + ROOT_MOVER_SUFFIX]
        assert mover.controller is ctrl and ctrl.target is mover
        assert ctrl.flags & ACTIVE_BIT and _names(mover) == [b'Part']
        assert stats['root_controllers_moved'] == 1

    @pytest.mark.parametrize('first, second', [(ACTIVE, INACTIVE), (INACTIVE, ACTIVE)])
    def test_a_chain_with_one_active_controller_moves_whole(self, first, second):
        """The inactive one rides along and stays inactive."""
        data, ctrl = _mesh(flags=first, turn=(0.0, 0.7))
        ctrl.next_controller = _transform_controller(second, turn=(0.0, 0.7))
        ctrl.next_controller.target = data.roots[0]
        nif_converter._convert_nif(data, src_path='mesh.nif')
        mover = data.roots[0].children[0]
        assert data.roots[0].controller is None and mover.controller is ctrl
        assert ctrl.next_controller.target is mover
        assert [bool(c.flags & ACTIVE_BIT) for c in (ctrl, ctrl.next_controller)] == [
            first == ACTIVE, second == ACTIVE]

    @pytest.mark.parametrize('keys', [{'grow': (1.0, 2.0)}, {'turn': (0.3, 0.3)}])
    def test_scale_keys_and_constant_keys_are_keys(self, keys):
        """The rule asks whether a key is held, not which channel or whether it changes."""
        root, ctrl, stats = _convert(**keys)
        assert root.controller is None and ctrl.target is root.children[0]
        assert stats['root_controllers_moved'] == 1

    def test_a_sliding_controller_moves_with_its_keys(self):
        """Translation keys reach the new node unchanged."""
        root, ctrl, _ = _convert(slide=((0.0, 0.0, 0.0), (0.0, -8.0, 0.0)))
        keys = ctrl.interpolator.data.translations.keys
        assert root.controller is None and ctrl.target is root.children[0]
        assert [k.value.y for k in keys] == [0.0, -8.0]

    def test_the_mover_sits_at_identity_under_an_unturned_root(self):
        """The new node adds no transform of its own and takes the node flags a converted root also has."""
        root = _convert(turn=(0.0, 0.7))[0]
        mover = root.children[0]
        assert mover.controller is not None and mover.flags == root.flags
        assert mover.rotation.is_identity() and mover.scale == 1.0
        assert (mover.translation.x, mover.translation.y, mover.translation.z) == (0.0, 0.0, 0.0)

    def test_a_turned_root_s_wrapper_takes_the_controller(self):
        """The wrapper's node holds the root's turn and the controller; no second node is added."""
        root, ctrl, stats = _convert(rows=QUARTER_TURN, turn=(0.0, 0.7))
        mover = root.children[0]
        assert stats['root_rotation_baked'] == 1 and root.controller is None
        assert _names(root) == [b'Top'] and _names(mover) == [b'Part']
        assert mover.controller is ctrl and ctrl.target is mover
        assert (mover.rotation.m_12, mover.rotation.m_21) == (1.0, -1.0)

    def test_an_unturned_root_s_child_of_the_same_name_is_not_taken_for_the_wrapper(self):
        """Only a root the wrapper split reuses a child."""
        root = _convert(part=b'Top', turn=(0.0, 0.7))[0]
        assert _names(root) == [b'Top' + ROOT_MOVER_SUFFIX] and _names(root.children[0]) == [b'Top']

    def test_a_skinned_mesh_keeps_its_root_controller(self, monkeypatch):
        """The converter's own skin verdict reaches the pass."""
        monkeypatch.setattr(nif_converter, '_prepare_rig', lambda *args: True)
        root, ctrl, stats = _convert(turn=(0.0, 0.7))
        assert root.controller is ctrl and ctrl.target is root and _names(root) == [b'Part']
        assert 'root_controllers_moved' not in stats


def _rooted(ctrl, name=b'Top', kids=(b'Part',)):
    """A root holding `ctrl` and one child node per name in `kids`."""
    root = _node(name)
    for kid in kids:
        root.add_child(_node(kid))
    root.controller = ctrl
    ctrl.target = root
    return root


class TestWhichNodeTakesIt:
    """The wrapper's node only when the root still has the wrapper's shape; else a new node."""

    @pytest.mark.parametrize('wrapped, kids', [
        (False, (b'Top',)),            # an unwrapped root whose only child shares its name
        (True, (b'Top', b'Part')),     # more than one child
        (True, (b'Part',)),            # one child under another name
    ])
    def test_a_new_node_is_added(self, wrapped, kids):
        """A root that lacks the wrapper's exact shape gets its own node."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)), kids=kids)
        assert move_root_controller(root, False, wrapped, {})
        mover = root.children[0]
        assert _names(root) == [b'Top' + ROOT_MOVER_SUFFIX] and _names(mover) == list(kids)
        assert mover.controller is not None and root.controller is None

    def test_a_shape_named_like_the_root_is_not_the_wrapper(self):
        """Geometry cannot hold the children, so it is never reused."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)), kids=())
        shape = NifFormat.NiTriShape()
        shape.name = b'Top'
        root.add_child(shape)
        assert move_root_controller(root, False, True, {})
        assert root.children[0].children[0] is shape and shape.controller is None

    def test_a_keyframe_controller_moves_like_a_transform_controller(self):
        """The older controller class writes the same transform."""
        ctrl = NifFormat.NiKeyframeController()
        ctrl.flags = ACTIVE
        ctrl.interpolator = _transform_controller(ACTIVE, turn=(0.0, 0.7)).interpolator
        root = _rooted(ctrl)
        assert move_root_controller(root, False, False, {})
        assert root.controller is None and ctrl.target is root.children[0]

    def test_a_keyless_controller_rides_along_with_a_keyed_one(self):
        """One running controller that holds a key is enough; the others follow in their order."""
        still = _transform_controller(ACTIVE, data=False)
        keyed = still.next_controller = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        root = _rooted(still)
        assert move_root_controller(root, False, False, {})
        mover = root.children[0]
        assert root.controller is None and mover.controller is still
        assert still.next_controller is keyed and keyed.target is mover

    def test_the_new_node_takes_the_node_flags_not_the_root_s(self):
        """A root that keeps other flags does not hand them on."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        root.flags = 22
        assert move_root_controller(root, False, False, {})
        assert root.flags == 22 and root.children[0].flags == 14

    def test_the_new_node_s_name_is_the_root_s_plus_anim(self):
        """The suffix is part of the output: the docs and other tools name it."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        move_root_controller(root, False, False, {})
        assert ROOT_MOVER_SUFFIX == b' Anim' and _names(root) == [b'Top Anim']

    def test_a_node_root_without_children_gets_an_empty_node(self):
        """A childless NiNode root is not exempt: the controller still leaves the root."""
        root, stats = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)), kids=()), {}
        assert move_root_controller(root, False, False, stats)
        mover = root.children[0]
        assert _names(root) == [b'Top' + ROOT_MOVER_SUFFIX] and mover.num_children == 0
        assert root.controller is None and mover.controller.target is mover
        assert stats == {'root_controllers_moved': 1}

    def test_an_empty_child_slot_does_not_hide_the_wrapper_s_node(self):
        """A cleared slot is no child: the wrapper's node is still the only one."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)), kids=(b'Top',))
        root.num_children = 2
        root.children.update_size()
        assert move_root_controller(root, False, True, {})
        assert _names(root) == [b'Top'] and root.children[0].controller is not None

    def test_a_padded_root_name_gives_a_clean_node_name(self):
        """A trailing NUL in the root's name stays out of the new node's name."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)), name=b'Top\x00')
        assert move_root_controller(root, False, False, {})
        assert _names(root) == [b'Top Anim']


def _chain(node):
    """A node's controllers in chain order."""
    chain, ctrl = [], node.controller
    while ctrl is not None:
        chain.append(ctrl)
        ctrl = ctrl.next_controller
    return chain


def _other(cls, flags=ACTIVE):
    """A controller of another class, by name."""
    ctrl = getattr(NifFormat, cls)()
    ctrl.flags = flags
    return ctrl


def _chained(ctrls, **rooted):
    """A root whose controller chain is `ctrls` in order, each targeting the root."""
    for ctrl, after in zip(ctrls, ctrls[1:]):
        ctrl.next_controller = after
    root = _rooted(ctrls[0], **rooted)
    for ctrl in ctrls:
        ctrl.target = root
    return root


class TestOnlyTransformControllersMove:
    """The root's own transform controllers leave; every other controller stays where it was."""

    @pytest.mark.parametrize('place', [0, 1, 2])
    def test_the_others_stay_on_the_root_in_their_order(self, place):
        """First, middle or last in the chain: one moves, two stay, both chains end."""
        turn = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        others = [_other('NiVisController'), _other('NiFloatExtraDataController')]
        root = _chained(others[:place] + [turn] + others[place:])
        assert move_root_controller(root, False, False, {})
        mover = root.children[0]
        assert [c is o for c, o in zip(_chain(root), others)] == [True, True]
        assert [c.target is root for c in others] == [True, True]
        assert _chain(mover) == [turn] and turn.target is mover
        assert turn.next_controller is None and others[1].next_controller is None

    def test_two_transform_controllers_keep_their_order_around_others(self):
        """An inactive one rides along behind the active one; the rest close ranks."""
        first = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        second = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        seen, value = _other('NiVisController'), _other('NiFloatExtraDataController')
        root = _chained([first, seen, second, value])
        assert move_root_controller(root, False, False, {})
        mover = root.children[0]
        assert [c is o for c, o in zip(_chain(mover), (first, second))] == [True, True]
        assert [c is o for c, o in zip(_chain(root), (seen, value))] == [True, True]
        assert len(_chain(mover)) == len(_chain(root)) == 2
        assert first.target is mover and second.target is mover
        assert seen.target is root and value.target is root
        assert not second.flags & ACTIVE_BIT

    def test_an_extra_data_controller_stays_with_its_extra_data(self):
        """The value it writes is the root's, so both stay on the root."""
        turn = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        value = _other('NiFloatExtraDataController')
        root = _chained([turn, value])
        amount = NifFormat.NiFloatExtraData()
        amount.name = b'Amount'
        root.add_extra_data(amount)
        assert move_root_controller(root, False, False, {})
        mover = root.children[0]
        assert root.controller is value and value.target is root
        assert list(root.extra_data_list) == [amount] and mover.num_extra_data_list == 0

    def test_a_visibility_controller_stays_through_the_converter(self):
        """End to end: the root keeps the visibility controller, the mover takes the turn."""
        data, ctrl = _mesh(turn=(0.0, 0.7))
        seen = ctrl.next_controller = _other('NiVisController')
        seen.target = data.roots[0]
        nif_converter._convert_nif(data, src_path='mesh.nif')
        root = data.roots[0]
        mover = root.children[0]
        assert root.controller is seen and seen.target is root
        assert seen.next_controller is None and ctrl.next_controller is None
        assert mover.controller is ctrl and ctrl.target is mover

    def test_a_transform_controller_a_manager_drives_stays(self):
        """It is not the root's own, so it stays beside the one that moves."""
        turn = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        driven = _transform_controller(ACTIVE | MANAGED, turn=(0.0, 0.7))
        root = _chained([driven, turn])
        assert move_root_controller(root, False, False, {})
        mover = root.children[0]
        assert _chain(root) == [driven] and driven.target is root
        assert _chain(mover) == [turn] and turn.target is mover


class TestRootControllerLeftAlone:
    """Roots the rule does not touch."""

    def _unmoved(self, root, has_skin=False):
        """True when the pass leaves `root`'s controller and children as they were."""
        ctrl, stats = root.controller, {}
        moved = move_root_controller(root, has_skin, False, stats)
        return (not moved and not stats and root.controller is ctrl
                and ctrl.target is root and _names(root) == [b'Part'])

    def test_a_root_with_a_controller_manager(self):
        """A manager first in the chain keeps everything on the root."""
        manager = NifFormat.NiControllerManager()
        manager.next_controller = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        assert self._unmoved(_rooted(manager))

    def test_a_manager_after_the_transform_controller(self):
        """A manager anywhere in the chain keeps everything on the root."""
        ctrl = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        ctrl.next_controller = NifFormat.NiControllerManager()
        assert self._unmoved(_rooted(ctrl))

    def test_a_controller_a_manager_drives(self):
        """The manager bit alone exempts the controller."""
        assert self._unmoved(_rooted(
            _transform_controller(ACTIVE | MANAGED, turn=(0.0, 0.7))))

    @pytest.mark.parametrize('mesh', [
        {'turn': (0.0, 0.7)},
        {'collision': 7, 'turn': (0.0, 0.7)},
        {'rows': QUARTER_TURN, 'turn': (0.0, 0.7)},
        {'data': False},
    ])
    def test_a_root_whose_transform_controllers_are_all_inactive(self, mesh):
        """Nothing is moved: no node, no count, the bit left clear."""
        root, ctrl, stats = _convert(flags=INACTIVE, **mesh)
        holder = root.children[0] if mesh.get('rows') else root
        assert root.controller is ctrl and ctrl.target is root and not ctrl.flags & ACTIVE_BIT
        assert _names(holder) == ([] if mesh.get('collision') else [b'Part'])
        assert 'root_controllers_moved' not in stats

    @pytest.mark.parametrize('keys', [{'data': False}, {}])
    def test_an_active_controller_that_holds_no_key(self, keys):
        """No key data, or key data without a key: the rule asks for a key."""
        assert self._unmoved(_rooted(_transform_controller(ACTIVE, **keys)))

    def test_an_active_controller_without_an_interpolator(self):
        """Nothing to read keys from is no key."""
        ctrl = NifFormat.NiKeyframeController()
        ctrl.flags = ACTIVE
        assert self._unmoved(_rooted(ctrl))

    def test_an_inactive_keyed_controller_beside_an_active_keyless_one(self):
        """Running and keyed must be one and the same controller."""
        still = _transform_controller(ACTIVE, data=False)
        still.next_controller = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        assert self._unmoved(_rooted(still))

    def test_a_dataless_stub_stays_through_the_converter(self):
        """End to end: the root keeps the controller and gets no new node."""
        root, ctrl, stats = _convert(data=False)
        assert root.controller is ctrl and ctrl.target is root and _names(root) == [b'Part']
        assert ctrl.interpolator.data is None and 'root_controllers_moved' not in stats

    def test_two_inactive_controllers(self):
        """No controller in the chain is active, so nothing moves."""
        ctrl = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        ctrl.next_controller = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        assert self._unmoved(_rooted(ctrl))

    def test_an_active_controller_a_manager_drives_beside_an_inactive_own_one(self):
        """Only the root's own controllers are asked for the active bit."""
        ctrl = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        ctrl.next_controller = _transform_controller(ACTIVE | MANAGED, turn=(0.0, 0.7))
        assert self._unmoved(_rooted(ctrl))

    def test_a_root_with_no_transform_controller(self):
        """Another controller class, active or not, is not a reason to move."""
        ctrl = NifFormat.NiVisController()
        ctrl.flags = ACTIVE
        assert self._unmoved(_rooted(ctrl))

    def test_a_skinned_mesh(self):
        """Skinned meshes are exempt."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        assert self._unmoved(root, has_skin=True)

    def test_a_rig_root(self):
        """The rig root keeps its controller: the engine binds the actor by that node."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)),
                            name=b'NPC Root [Root]')
        assert self._unmoved(root)

    def test_a_rig_root_whose_name_is_padded(self):
        """A trailing NUL does not hide the rig root."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)),
                       name=b'NPC Root [Root]\x00')
        assert self._unmoved(root)

    def test_a_root_that_holds_no_children(self):
        """A geometry root has no children to hand over and is left alone."""
        shape = NifFormat.NiTriShape()
        ctrl = shape.controller = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        assert not move_root_controller(shape, False, False, {}) and shape.controller is ctrl

    def test_the_same_roots_move_without_the_exemption(self):
        """The control: the same root without an exemption does move."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        assert move_root_controller(root, False, False, {}) and root.controller is None


class TestCollisionStaysOnTheRoot:
    """A child's collision is hoisted to the root first, so it sits above the node the controller moves."""

    def test_child_collision_ends_on_the_root(self):
        """The body is the root's; the mover carries only the controller."""
        root, ctrl, _ = _convert(collision=7, turn=(0.0, 0.7))
        mover = root.children[0]
        owners = [b for b in root.tree() if getattr(b, 'collision_object', None) is not None]
        assert owners == [root] and root.collision_object.target is root
        assert mover.controller is ctrl and root.controller is None

    def test_the_emptied_collision_node_is_gone_before_the_move(self):
        """The hoist clears the node it emptied only among the root's own children."""
        root = _convert(collision=7, turn=(0.0, 0.7))[0]
        assert _names(root.children[0]) == []

    @pytest.mark.parametrize('motion', [7, 6])
    def test_the_root_body_is_static(self, motion):
        """A keyframed source body too: nothing named like the root moves."""
        body = _convert(collision=motion, turn=(0.0, 0.7))[0].collision_object.body
        assert body.mass == 0.0 and body.motion_system == 5

    def test_a_wrapped_root_keeps_child_collision_below_the_wrapper_s_node(self):
        """The hoist skips a wrapped root, so there the body stays under the node that takes the controller."""
        root, ctrl, _ = _convert(rows=QUARTER_TURN, collision=7, turn=(0.0, 0.7))
        mover = root.children[0]
        assert root.collision_object is None and mover.controller is ctrl
        assert mover.children[0].collision_object is not None


class TestWhereThePassRuns:
    """Every root the converter walks but a sky mesh's, each move counted."""

    def test_a_sky_mesh_keeps_its_root_controller(self):
        """Nothing places a sky mesh, so its root is left as the source has it."""
        data, ctrl = _mesh(turn=(0.0, 0.7))
        stats = nif_converter._convert_nif(data, src_path='meshes/sky/stars.nif')
        root = data.roots[0]
        assert type(root).__name__ == 'NiNode' and stats['root_converted'] == 0
        assert root.controller is ctrl and ctrl.target is root and _names(root) == [b'Part']
        assert 'root_controllers_moved' not in stats

    def test_a_skinned_creature_mesh_keeps_its_root_controller(self, monkeypatch):
        """The skin exemption holds on the creature path too."""
        monkeypatch.setattr(nif_converter, '_prepare_rig', lambda *args: True)
        monkeypatch.setattr(nif_converter, 'regen_creature_skins', lambda *args: None)
        data, ctrl = _mesh(turn=(0.0, 0.7))
        stats = nif_converter._convert_nif(data, src_path='mesh.nif', creature=True)
        assert data.roots[0].controller is ctrl and ctrl.target is data.roots[0]
        assert 'root_controllers_moved' not in stats

    def test_a_creature_mesh(self):
        """The creature path runs the pass too."""
        data, ctrl = _mesh(turn=(0.0, 0.7))
        stats = nif_converter._convert_nif(data, src_path='mesh.nif', creature=True)
        assert data.roots[0].controller is None and ctrl.target is data.roots[0].children[0]
        assert stats['root_controllers_moved'] == 1

    def test_two_roots_in_one_file_add_up(self):
        """The counter counts roots, not files."""
        data, _ = _mesh(collision=7, turn=(0.0, 0.7))
        second = _mesh(collision=7, turn=(0.0, 0.7))[0].roots[0]
        second.name = b'Second'
        data.roots = [data.roots[0], second]
        stats = nif_converter._convert_nif(data, src_path='mesh.nif')
        assert [_names(r) for r in data.roots] == [[b'Top Anim'], [b'Second Anim']]
        assert stats['root_controllers_moved'] == 2


def _grass_file(path, inner_name, on_inner=None, on_root=None, loose=False):
    """Write a grass-shaped mesh: a fade root `Top`, one inner node holding a shape, each with its controller.

    `loose` hangs a second shape straight from the root beside the inner node.
    """
    root = NifFormat.BSFadeNode()
    root.name = b'Top'
    root.scale = 1.0
    root.rotation.set_identity()
    inner = _node(inner_name)
    shape = NifFormat.NiTriShape()
    shape.name = b'Blade'
    shape.scale = 1.0
    shape.rotation.set_identity()
    inner.add_child(shape)
    root.add_child(inner)
    if loose:
        blade = NifFormat.NiTriShape()
        blade.name = b'Loose'
        blade.scale = 1.0
        blade.rotation.set_identity()
        root.add_child(blade)
    for node, ctrl in ((inner, on_inner), (root, on_root)):
        if ctrl is not None:
            node.controller = ctrl
            ctrl.target = node
    data = NifFormat.Data(version=0x14000005, user_version=11, user_version_2=11)
    data.roots = [root]
    nif_converter._upgrade_version(data)
    with open(path, 'wb') as out:
        data.write(out)


def _read_root(path):
    """The root of the mesh at `path`."""
    data = NifFormat.Data()
    with open(path, 'rb') as src:
        data.read(src)
    return data.roots[0]


def _kinds(node):
    """The class names of a node's controllers, in chain order."""
    return [type(c).__name__ for c in _chain(node)]


class TestAGrassModelKeepsItsOldShape:
    """The grass pass puts a moved controller back on the root, so the geometry is flat again.

    See: docs/commentary/asset_convert_nif.md#root-transform-controller
    """

    MOVER = b'Top' + ROOT_MOVER_SUFFIX

    def test_the_moved_controller_goes_back_and_the_node_is_flattened(self, tmp_path):
        """The shape hangs from the root again and the controller targets the root."""
        path = tmp_path / 'blade.nif'
        _grass_file(path, self.MOVER, on_inner=_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        assert grass_profile.apply_grass_profile(path)
        root = _read_root(path)
        assert _names(root) == [b'Blade'] and type(root.children[0]).__name__ == 'NiTriShape'
        assert _kinds(root) == ['NiTransformController'] and root.controller.target is root

    def test_it_goes_behind_the_controllers_the_root_kept(self, tmp_path):
        """The root's own chain stays in front; every returned controller targets the root."""
        path = tmp_path / 'blade.nif'
        moved = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        moved.next_controller = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        _grass_file(path, self.MOVER, on_inner=moved, on_root=_other('NiVisController'))
        grass_profile.apply_grass_profile(path)
        root = _read_root(path)
        assert _kinds(root) == ['NiVisController', 'NiTransformController', 'NiTransformController']
        assert [c.target is root for c in _chain(root)] == [True, True, True]
        assert _names(root) == [b'Blade']

    def test_a_padded_name_is_still_the_mover_s(self, tmp_path):
        """A trailing NUL in either name does not hide the node."""
        path = tmp_path / 'blade.nif'
        _grass_file(path, self.MOVER + b'\x00', on_inner=_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        grass_profile.apply_grass_profile(path)
        assert _names(_read_root(path)) == [b'Blade']

    def test_a_turned_root_s_wrapper_gives_it_back_too(self, tmp_path):
        """On a turned root the wrapper's node, named like the root, took the controller; it goes back the same way."""
        path = tmp_path / 'blade.nif'
        _grass_file(path, b'Top', on_inner=_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        assert grass_profile.apply_grass_profile(path)
        root = _read_root(path)
        assert _names(root) == [b'Blade'] and type(root.children[0]).__name__ == 'NiTriShape'
        assert _kinds(root) == ['NiTransformController'] and root.controller.target is root

    def test_a_node_named_like_the_root_beside_others_is_not_the_wrapper_s(self, tmp_path):
        """The wrapper's node is the root's only child; one with a sibling is the source's own and stays."""
        path = tmp_path / 'blade.nif'
        _grass_file(path, b'Top', on_inner=_transform_controller(ACTIVE, turn=(0.0, 0.7)), loose=True)
        grass_profile.apply_grass_profile(path)
        root = _read_root(path)
        assert _names(root) == [b'Top', b'Loose'] and root.controller is None
        assert _kinds(root.children[0]) == ['NiTransformController']

    def test_another_animated_node_is_left_as_it_was(self, tmp_path):
        """Only the node this rule made is undone; an animated node of the source stays nested."""
        path = tmp_path / 'blade.nif'
        _grass_file(path, b'Sway', on_inner=_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        grass_profile.apply_grass_profile(path)
        root = _read_root(path)
        assert _names(root) == [b'Sway'] and root.controller is None
        assert _kinds(root.children[0]) == ['NiTransformController']

    def test_putting_back_counts_as_a_change(self, tmp_path):
        """The grass copy is written again even when no later step of the pass changes it."""
        path = tmp_path / 'blade.nif'
        _grass_file(path, self.MOVER, on_inner=_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        data = NifFormat.Data()
        with open(path, 'rb') as src:
            data.read(src)
        assert grass_profile._controllers_back_on_root(data) is True

    def test_a_mesh_without_a_fade_root_has_nothing_to_put_back(self):
        """The grass pass only reads a fade-node root."""
        data = _mesh(turn=(0.0, 0.7))[0]
        assert not grass_profile._controllers_back_on_root(data)

    def test_a_plain_wrapper_is_flattened_as_before(self, tmp_path):
        """The control: with no controller the node is baked away and the root gains none."""
        path = tmp_path / 'blade.nif'
        _grass_file(path, self.MOVER)
        assert grass_profile.apply_grass_profile(path)
        root = _read_root(path)
        assert _names(root) == [b'Blade'] and root.controller is None
