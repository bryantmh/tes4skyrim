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
asset_convert/havok/hkx_behavior.py) and only while the graph's IsAttackReady /
bEquipOK variables read 1 (vanilla initial values).  Death routes through
the DeathWait tree (DeathAnimation conditioned / Ragdoll fall-through,
vanilla dog layout) into the graph's ragdoll wrapper states.

Layouts mirror the vanilla dog set byte-for-byte (DATA group/flag bytes and
the swim IsSwimming CTDA copied verbatim from Skyrim.esm DogMoveStart/
DogSwimRoot/DogSwimStart/DogSwimStop etc.).
"""


from ..base.writer import pack_record, pack_subrecord, pack_string_subrecord

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
    'ActionGetUp': 0x000D1FDD,
    'ActionIdle': 0x00013002,
    'ActionIdleWarn': 0x00098886,
    # magic / block action entry points (vanilla creature casters and
    # blockers route these through per-creature IDLE trees — see
    # _build_cast_idles/_build_block_idles)
    'ActionLeftAttack': 0x00013004,
    'ActionRightAttack': 0x00013005,
    'ActionLeftRelease': 0x00013451,
    'ActionLeftReady': 0x00013452,
    'ActionLeftInterrupt': 0x00013453,
    'ActionRightRelease': 0x00013454,
    'ActionRightReady': 0x00013455,
    'ActionRightInterrupt': 0x00013456,
    'ActionForceEquip': 0x0002ADF1,
    'ActionBlockAnticipate': 0x000193CE,
    'ActionBlockHit': 0x00013AF4,
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
    ('GetUp', 'GetUpBegin', 'ActionGetUp', '000000720000'),
]

#: Leaves whose event only a ragdoll graph handles; written without ENAM otherwise (build_creature_idles).
_RAGDOLL_ONLY_LEAVES = {'Knockdown', 'RagdollInstant', 'GetUp'}

# IsSwimming == 1 condition, verbatim from vanilla DogSwimStart
_SWIM_CTDA = bytes.fromhex(
    '000F8B000000803FB900933300000000000000000000000000000000FFFFFFFF')
# Aware-vocal DATA bytes, verbatim from vanilla WolfIdleWarn.
#
# NEVER add an IDLE record under ActionIdle (0x00013002) for these graphs:
# it sends the actor into an engine-tracked dynamic idle whose lifecycle our
# minimal graphs do not complete, and every creature stopped walking and
# floated in its idle (2026-08-07). Vanilla parents its vocal idles
# (WolfIdleHowl) under per-creature NonCombatIdle chains with graph-side
# idle handling we do not generate yet.
_VOCAL_WARN_DATA = bytes.fromhex('000000000000')
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
    # `edid` is TES4<folder><suffix> from the _LEAVES constant — a stable
    # name, not an ordinal, so it keys the derived id directly.
    fid = writer.derive_formid('CREA_IDLE', edid)
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


# CTDA function indices (xEdit wbDefinitionsTES5 condition table) used by
# the vanilla cast/block IDLE trees. Casting source enum: 0 Left, 1 Right.
_FN_GET_WANT_BLOCKING = 0
_FN_HAS_EQUIPPED_SPELL = 570
_FN_GET_CURRENT_CASTING_TYPE = 571
_FN_GET_EQUIPPED_ITEM_TYPE = 597
_CASTING_TYPE_FIRE_FORGET = 1.0


def _ctda(func: int, value: float, param1: int = 0) -> bytes:
    """One 32-byte CTDA: `func(param1) == value`, run on subject."""
    import struct
    return struct.pack('<B3xfHHIIIIi', 0, value, func, 0, param1, 0, 0, 0,
                       -1)


def _build_cast_idles(writer, base: str, dnam: str, hand: str) -> None:
    """The magic action tree — the ENGINE side of creature spellcasting,
    copied node-for-node from the vanilla flame atronach's IDLE set:

      Action<hand>Attack (AACT) -> <X>AttackRoot -> <X>AttackMagic
        -> <X>MagicFireForgetRoot -> <X>MagicFireForget
           (ENAM Spell_FireForget_LH/RH — the graph's cast-chain entry)
      Action<hand>Release   -> root -> leaf ENAM Spell_Release
      Action<hand>Ready     -> root -> leaf ENAM Spell_Ready
      Action<hand>Interrupt -> root -> leaf ENAM Spell_Interrupt
      ActionForceEquip      -> root -> node -> leaf ENAM Magic_Equip

    Without this tree the AI's cast actions reach NO graph event and the
    creature never casts, no matter how correct the graph and records are —
    the same action-routing gate as movement (this module's docstring).

    THE CONDITIONS ARE LOAD-BEARING. The engine walks this same tree for an
    ordinary MELEE left attack: vanilla gates the magic branch with
    `HasEquippedSpell(Left) == 1` and the FireForget root with
    `GetCurrentCastingType(Left) == 1`, so a melee swing falls through and
    the graph's ATKE attack fires instead. Shipping the branch unconditioned
    (2026-08-22) routed EVERY melee left attack into the cast chain, where
    the actor parked waiting for a release that never came — the scamp
    "chases but can't melee and never casts" report. The concentration
    branch is omitted: every converted TES4 spell is FireForget.

    hand: 'Left' (vanilla caster convention — atronach/hagraven/spriggan)
    unless the creature also blocks, which owns the left-hand actions
    (the vanilla wisp splits exactly this way: block left, cast right).
    """
    fire_evt = ('Spell_FireForget_LH' if hand == 'Left'
                else 'Spell_FireForget_RH')
    src = 0 if hand == 'Left' else 1            # wbCastingSourceEnum
    has_spell = _ctda(_FN_HAS_EQUIPPED_SPELL, 1.0, src)
    is_ff = _ctda(_FN_GET_CURRENT_CASTING_TYPE, _CASTING_TYPE_FIRE_FORGET,
                  src)
    aroot = _idle(writer, f'{base}{hand}AttackRoot', dnam, '',
                  _ACTIONS[f'Action{hand}Attack'], 0,
                  bytes.fromhex('0000006F0000'))
    amagic = _idle(writer, f'{base}{hand}AttackMagic', dnam, '', aroot, 0,
                   bytes.fromhex('000000410000'), ctda=has_spell)
    ffroot = _idle(writer, f'{base}MagicFireForgetRoot', dnam, '', amagic, 0,
                   bytes.fromhex('000000460000'), ctda=is_ff)
    _idle(writer, f'{base}MagicFireForget', dnam, fire_evt, ffroot, 0,
          bytes.fromhex('000000310000'))
    for action, suffix, evt, data, cond in (
            ('Release', 'Release', 'Spell_Release', '000000460000',
             has_spell),
            ('Ready', 'Ready', 'Spell_Ready', '000000370000', None),
            ('Interrupt', 'Interrupt', 'Spell_Interrupt', '000000360000',
             None)):
        root = _idle(writer, f'{base}{hand}{suffix}Root', dnam, '',
                     _ACTIONS[f'Action{hand}{action}'], 0,
                     bytes.fromhex('000000410000'))
        _idle(writer, f'{base}{hand}{suffix}', dnam, evt, root, 0,
              bytes.fromhex(data), ctda=cond)
    feroot = _idle(writer, f'{base}ForceEquipRoot', dnam, '',
                   _ACTIONS['ActionForceEquip'], 0,
                   bytes.fromhex('000000320000'))
    fe = _idle(writer, f'{base}ForceEquip', dnam, '', feroot, 0,
               bytes.fromhex('000000330000'))
    # vanilla AtronachFlameActionEquipMagic: GetEquippedItemType(Left) == 9
    # (9 = spell in the equipped-item-type enum)
    _idle(writer, f'{base}EquipMagic', dnam, 'Magic_Equip', fe, 0,
          bytes.fromhex('000000330000'),
          ctda=_ctda(_FN_GET_EQUIPPED_ITEM_TYPE, 9.0, src))


def _build_block_idles(writer, base: str, dnam: str) -> None:
    """The block action tree — the ENGINE side of creature blocking, copied
    from the vanilla frost atronach (the unarmed-blocker layout):

      ActionBlockAnticipate -> root -> leaf ENAM blockStart
      ActionBlockHit        -> root -> leaf ENAM blockHitStart
      ActionLeftAttack      -> root -> leaf ENAM blockStart
      ActionLeftRelease     -> root -> leaf ENAM blockStop

    An unarmed creature raises its guard by 'holding the left attack'
    (AtronachFrostLeftAttack ENAM=blockStart, ...LeftRelease ENAM=blockStop
    — verbatim), so the left-hand actions belong to blocking; a creature
    that also casts uses the right-hand actions for magic (wisp split).
    """
    aroot = _idle(writer, f'{base}AnticipateBlockRoot', dnam, '',
                  _ACTIONS['ActionBlockAnticipate'], 0,
                  bytes.fromhex('0000003F0000'))
    _idle(writer, f'{base}AnticipateBlock', dnam, 'blockStart', aroot, 0,
          bytes.fromhex('000000200000'))
    hroot = _idle(writer, f'{base}BlockHitRoot', dnam, '',
                  _ACTIONS['ActionBlockHit'], 0,
                  bytes.fromhex('000000000000'))
    _idle(writer, f'{base}BlockHit', dnam, 'blockHitStart', hroot, 0,
          bytes.fromhex('000000690000'))
    # Gated exactly as vanilla gates them (AtronachFrostLeftAttack /
    # ...LeftRelease, WispLeftAttack / ...LeftRelease, byte-identical
    # conditions): `GetWantBlocking == 0` on the raise, `== 1` on the drop.
    # Unconditioned, every melee left attack would raise the guard instead.
    laroot = _idle(writer, f'{base}LeftAttackBlockRoot', dnam, '',
                   _ACTIONS['ActionLeftAttack'], 0,
                   bytes.fromhex('0000003A0000'))
    _idle(writer, f'{base}LeftAttackBlock', dnam, 'blockStart', laroot, 0,
          bytes.fromhex('000000000000'),
          ctda=_ctda(_FN_GET_WANT_BLOCKING, 0.0))
    lrroot = _idle(writer, f'{base}LeftReleaseBlockRoot', dnam, '',
                   _ACTIONS['ActionLeftRelease'], 0,
                   bytes.fromhex('0000003A0000'))
    _idle(writer, f'{base}LeftReleaseBlock', dnam, 'blockStop', lrroot, 0,
          bytes.fromhex('000000740000'),
          ctda=_ctda(_FN_GET_WANT_BLOCKING, 1.0))


def build_creature_idles(writer, folder: str, proj: dict) -> None:
    """The per-project action-routing IDLE set (once per creature folder)."""
    # The engine picks a creature's IDLE root (several unchained roots hang
    # under each AACT) by matching DNAM against the actor's root behavior
    # path, which it resolves from the loaded project — so this must be the
    # path the project really ships (CK: "resolve root behavior name").
    dnam = proj['behavior_hkx']
    base = f'TES4{folder}'

    # A creature whose skeleton yielded no ragdoll (ghost/wraith/spectre:
    # hkx_ragdoll extracts nothing) gets no ragdoll wrapper states in its
    # graph, so the ragdoll events below would fire into nothing.  Vanilla
    # strips exactly these for the witchlight -- ENAM omitted, so the IDLE
    # resolves and sends no event.
    has_ragdoll = bool(proj.get('has_ragdoll'))
    for suffix, event, action, data_hex in _LEAVES:
        if not has_ragdoll and suffix in _RAGDOLL_ONLY_LEAVES:
            event = ''
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
    # graph routes DeathAnimation into AnimateToRagdoll (whose enter raises
    # AddRagdollToWorld — the ONLY raiser) and Ragdoll straight into Fully
    # Ragdoll (fired when the engine already ragdolled the actor);
    # without this tree `kill` leaves the actor idling upright forever.
    # A RAGDOLL-LESS creature still needs a death IDLE -- it just sends a
    # different event.  Skyrim's own ragdoll-less-but-animated creature is
    # the DRAGON PRIEST (it dissolves to a pile like our ghosts), and it
    # ships DragonPriestDeathWaitRoot (parent ActionDeathWait, no ENAM) ->
    # DragonPriestDeathWait (ENAM `deathStart`) -- the same AACT slot,
    # sending the event our generated graph's Death state is entered on,
    # instead of the DeathAnimation/Ragdoll pair that would fire into
    # ragdoll wrapper states a ragdoll-less graph does not have.
    #
    # The witchlight, which has NO death animation at all, ships no death
    # IDLE -- that is why it looked like the tree should be dropped
    # entirely.  Doing so left our ghosts with no death event and their
    # authored death animation never played.
    droot = _idle(writer, f'{base}DeathWaitRoot', dnam, '',
                  _ACTIONS['ActionDeathWait'], 0, _DEATH_ROOT_DATA)
    if has_ragdoll:
        danim = _idle(writer, f'{base}DeathWait', dnam, 'DeathAnimation',
                      droot, 0, _DEATH_ANIM_DATA, ctda=_DEATH_ANIM_CTDAS)
        _idle(writer, f'{base}DeathWaitRagdoll', dnam, 'Ragdoll', droot,
              danim, _DEATH_RAGDOLL_DATA)
    else:
        _idle(writer, f'{base}DeathWait', dnam, 'deathStart', droot, 0,
              _DEATH_ANIM_DATA)

    # Spellcasting / blocking action routing (see the helpers above). A
    # creature with both lanes blocks on the LEFT-hand actions and casts on
    # the RIGHT (the vanilla wisp split); a single lane takes the left.
    has_cast = bool(proj.get('has_cast'))
    has_block = bool(proj.get('has_block'))
    if has_cast:
        _build_cast_idles(writer, base, dnam,
                          'Right' if has_block else 'Left')
    if has_block:
        _build_block_idles(writer, base, dnam)

    # Aware vocal: the entry point for the graph's AwareVocal state (CSDT
    # Aware slot). ActionIdleWarn fires during an aggro warning — the exact
    # vanilla WolfIdleWarn layout, and the engine-native equivalent of
    # Oblivion's 'Aware' sound. (The Idle slot has NO record here — see the
    # ActionIdle warning above.)
    for v in proj.get('vocal_events') or []:
        if v.get('event') == 'awareVocalStart':
            _idle(writer, f'{base}IdleWarn', dnam, 'awareVocalStart',
                  _ACTIONS['ActionIdleWarn'], 0, _VOCAL_WARN_DATA)
