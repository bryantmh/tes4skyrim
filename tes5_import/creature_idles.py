"""Generated IDLE records: the engine action → graph event routing table.

The engine does NOT send behavior events like moveStart directly. It fires
Actor Actions (AACT: ActionMoveStart, ActionDraw, ...) and walks the IDLE
record tree parented under each action, filtered by DNAM == the actor's
root behavior graph file; the matching IDLE's ENAM string is what gets sent
to the graph (vanilla: DogMoveStart = DNAM DogBehavior.hkx, ENAM moveStart,
parent ActionMoveStart — one such set exists per creature project, 36
different MoveStart IDLEs in Skyrim.esm alone). A generated behavior file
with NO IDLE records receives NO events whatsoever: the actor translates
(movement controller works via MOVT) but plays its idle forever and never
shows attack animations — the third and final stuck-in-idle layer
(2026-07-09), after the AI-package and MOVT/iState registration fixes.

Attack events are NOT routed here: the combat controller sends the RACE's
ATKE strings directly — but only after the draw handshake (ActionDraw →
combatStanceStart in, graph replies weaponDraw out via the root-level
StartCombat/StopCombat expression-modifier pair in
asset_convert/hkx_behavior.py) and only while the graph's IsAttackReady /
bEquipOK variables read 1 (vanilla initial values).  Death routes through
the DeathWait tree (DeathAnimation conditioned / Ragdoll fall-through,
vanilla dog layout) into the graph's ragdoll wrapper states.

Layouts mirror the vanilla dog set byte-for-byte (DATA group/flag bytes and
the swim IsSwimming CTDA copied verbatim from Skyrim.esm DogMoveStart/
DogSwimRoot/DogSwimStart/DogSwimStop etc.).
"""

import os

from .writer import pack_record, pack_subrecord, pack_string_subrecord

# Vanilla Skyrim.esm AACT records (master index 0 — written unremapped)
_ACTIONS = {
    'ActionMoveStart': 0x000959F8,
    'ActionMoveStop': 0x000959F9,
    'ActionMoveForward': 0x0005EDC9,
    'ActionMoveBackward': 0x0005EDCC,
    'ActionTurnLeft': 0x000959FD,
    'ActionTurnRight': 0x000959FC,
    'ActionTurnStop': 0x000959FE,
    'ActionResetGraph': 0x000D1FDE,
    'ActionStaggerStart': 0x000138D2,
    'ActionRecoil': 0x00013AF5,
    'ActionRecoilLarge': 0x00013EC8,
    'ActionIdleStop': 0x00018BA8,
    'ActionIdleStopInstant': 0x0007F8E3,
    'ActionDraw': 0x000132AF,
    'ActionSheath': 0x00046BAF,
    'ActionDeathWait': 0x0005DD59,
    'ActionSwimStateChange': 0x00013003,
    'ActionKnockDown': 0x000D1FDC,
    'ActionRagdollInstant': 0x0009BB4E,
}

# (edid suffix, graph event, action, vanilla-dog DATA hex)
_LEAVES = [
    ('MoveStart', 'moveStart', 'ActionMoveStart', '000000C10000'),
    ('MoveStop', 'moveStop', 'ActionMoveStop', '000000C10000'),
    ('MoveForward', 'moveForward', 'ActionMoveForward', '000000800000'),
    ('MoveBackward', 'moveBackward', 'ActionMoveBackward', '000000800000'),
    ('TurnLeft', 'turnLeft', 'ActionTurnLeft', '000000000000'),
    ('TurnRight', 'turnRight', 'ActionTurnRight', '000000000000'),
    ('TurnStop', 'turnStop', 'ActionTurnStop', '000000000000'),
    ('ResetGraph', 'returnToDefault', 'ActionResetGraph', '000000410000'),
    ('Stagger', 'staggerStart', 'ActionStaggerStart', '0000003F0000'),
    ('Recoil', 'recoilStart', 'ActionRecoil', '000000000000'),
    ('RecoilLarge', 'recoilLargeStart', 'ActionRecoilLarge', '0000003F0000'),
    ('IdleStop', 'IdleStop', 'ActionIdleStop', '0000001B0000'),
    ('IdleStopInstant', 'IdleStop', 'ActionIdleStopInstant', '000000650000'),
    ('CombatStance', 'combatStanceStart', 'ActionDraw', '000000110000'),
    ('CombatStanceStop', 'combatStanceStop', 'ActionSheath',
     '000000200000'),
    # death is handled by the DeathWait TREE below (DeathAnimation/Ragdoll —
    # Oblivion creatures have no death anims, the ragdoll IS the death)
    ('Knockdown', 'Ragdoll', 'ActionKnockDown', '000000630000'),
    ('RagdollInstant', 'RagdollInstant', 'ActionRagdollInstant',
     '000000740000'),
]

# IsSwimming == 1 condition, verbatim from vanilla DogSwimStart
_SWIM_CTDA = bytes.fromhex(
    '000F8B000000803FB900933300000000000000000000000000000000FFFFFFFF')
_SWIM_DATA = bytes.fromhex('0000003F0000')
# vanilla DogDeathWait conditions (verbatim) — gate the DeathAnimation
# branch; when false the walk falls through to the Ragdoll sibling
_DEATH_ANIM_CTDAS = [bytes.fromhex(
    '00AC8D00000000004402B92000000000000000000000000000000000FFFFFFFF'),
    bytes.fromhex(
    '00AC8D00000000003901B92000000000000000000000000000000000FFFFFFFF')]
_DEATH_ROOT_DATA = bytes.fromhex('000000000000')
_DEATH_ANIM_DATA = bytes.fromhex('000000730000')
_DEATH_RAGDOLL_DATA = bytes.fromhex('000000740000')


def _idle(writer, edid: str, dnam: str, enam: str, parent: int,
          previous: int, data: bytes, ctda=None) -> int:
    """One IDLE record (subrecord order: EDID CTDA* DNAM ENAM ANAM DATA);
    ANAM = (parent, previous sibling). ctda: bytes or list of bytes.
    Returns the new FormID."""
    fid = writer.alloc_formid()
    subs = pack_string_subrecord('EDID', edid)
    for c in ([ctda] if isinstance(ctda, bytes) else (ctda or [])):
        subs += pack_subrecord('CTDA', c)
    subs += pack_string_subrecord('DNAM', dnam)
    if enam:
        subs += pack_string_subrecord('ENAM', enam)
    subs += pack_subrecord('ANAM', parent.to_bytes(4, 'little')
                           + previous.to_bytes(4, 'little'))
    subs += pack_subrecord('DATA', data)
    writer.add_record('IDLE', pack_record('IDLE', fid, 0, subs))
    return fid


def build_creature_idles(writer, folder: str, proj: dict) -> None:
    """The per-project action-routing IDLE set (once per creature folder)."""
    proj_dir = os.path.dirname(proj['project_hkx'])
    dnam = f'{proj_dir}\\Behaviors\\tes4{folder}behavior.hkx'
    base = f'TES4{folder}'

    for suffix, event, action, data_hex in _LEAVES:
        _idle(writer, f'{base}{suffix}', dnam, event, _ACTIONS[action], 0,
              bytes.fromhex(data_hex))

    # Swim: root under ActionSwimStateChange with two children — swimStart
    # gated on IsSwimming, swimStop as the fallback (vanilla dog pattern;
    # children are evaluated following the previous-sibling chain).
    root = _idle(writer, f'{base}SwimRoot', dnam, '',
                 _ACTIONS['ActionSwimStateChange'], 0, _SWIM_DATA)
    start = _idle(writer, f'{base}SwimStart', dnam, 'swimStart', root, 0,
                  _SWIM_DATA, ctda=_SWIM_CTDA)
    _idle(writer, f'{base}SwimStop', dnam, 'swimStop', root, start,
          _SWIM_DATA)

    # Death: vanilla dog tree — ActionDeathWait root, DeathAnimation child
    # (conditioned) with Ragdoll as the fall-through sibling.  The generated
    # graph handles both (AnimateToRagdoll / Fully Ragdoll wrapper states);
    # without this tree `kill` leaves the actor idling upright forever.
    droot = _idle(writer, f'{base}DeathWaitRoot', dnam, '',
                  _ACTIONS['ActionDeathWait'], 0, _DEATH_ROOT_DATA)
    danim = _idle(writer, f'{base}DeathWait', dnam, 'DeathAnimation', droot,
                  0, _DEATH_ANIM_DATA, ctda=_DEATH_ANIM_CTDAS)
    _idle(writer, f'{base}DeathWaitRagdoll', dnam, 'Ragdoll', droot, danim,
          _DEATH_RAGDOLL_DATA)
