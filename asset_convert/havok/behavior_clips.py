"""Classify an Oblivion creature's .kf files into the behaviour graph's roles.

Split out of hkx_behavior.py, which held the clip taxonomy and the Havok XML
builders in one 2100-line file. The taxonomy references nothing from the
builders, so the dependency is one-directional: the builders import from here.

Claim ORDER is load-bearing throughout -- each pass marks what it took, so a
table matched later cannot steal a name an earlier one claimed.

See: docs/commentary/asset_convert_creature.md#fo3fnv-creature-clip-naming
"""

import os
import re
import struct

from asset_convert.havok.hkx_behavior_falloutnv import alias_clips


# ---------------------------------------------------------------------------
# Clip classification (the Oblivion filename convention)
# ---------------------------------------------------------------------------

#: Gait role -> (kf stems, enter, exit). See: docs/commentary/asset_convert_creature.md#strafes-are-blends-not-states
LOCOMOTION_STATES = {
    'MoveForward': (['forward', 'runforward', 'fastforward'],
                    'moveStart', 'moveStop'),
    'MoveBackward': (['backward'], 'moveBackward', 'moveStop'),
    'TurnLeft': (['turnleft'], 'turnLeft', 'turnStop'),
    'TurnRight': (['turnright'], 'turnRight', 'turnStop'),
    'StrafeLeft': (['left', 'handtohandleft', 'onehandleft', 'twohandleft',
                    'staffleft', 'bowleft'], None, None),
    'StrafeRight': (['right', 'handtohandright', 'onehandright',
                     'twohandright', 'staffright', 'bowright'],
                    None, None),
}

#: Swim role -> kf stems, best first. See: docs/commentary/asset_convert_creature.md#swim-clips
SWIM_CLIPS = {
    'forward': ['swimforward'],
    'fast': ['swimfastforward'],
    'idle': ['swimidle'],
    'backward': ['swimbackward'],
    'left': ['swimturnleft'],
    'right': ['swimturnright'],
}

#: Block role -> kf stems, generic first. See: docs/commentary/asset_convert_creature.md#block-clips
BLOCK_CLIPS = {
    'idle': ['blockidle', 'block', 'handtohandblockidle', 'onehandblockidle',
             'twohandblockidle', 'staffblockidle', 'bowblockidle'],
    'hit': ['blockhit', 'handtohandblockhit', 'onehandblockhit',
            'twohandblockhit', 'staffblockhit', 'bowblockhit'],
}
IDLE_CANDIDATES = ['idle']

#: Interrupt role -> (kf stems, enter, exit|None). See: docs/commentary/asset_convert_creature.md#single-play-clips
SINGLE_PLAY = {
    'Recoil': (['recoil'], 'recoilStart', 'recoilStop'),
    'Stagger': (['stagger'], 'staggerStart', 'staggerStop'),
    'Death': (['death', 'dies'], 'deathStart', None),
}

#: Stance -> (equip, unequip, hand types). See: docs/commentary/asset_convert_creature.md#equip-clips
EQUIP_STANCES = {
    'H2H':   (['handtohandequip', 'handtohandattackequip',
               'swimhandtohandequip', 'swimhandtohandattackequip',
               'equip', 'swimequip'],
              ['handtohandunequip', 'handtohandattackunequip',
               'swimhandtohandunequip', 'swimhandtohandattackunequip',
               'unequip', 'swimunequip'],
              (0,)),
    'OneH':  (['onehandequip', 'onehandattackequip'],
              ['onehandunequip', 'onehandattackunequip'], (1, 2, 3, 4)),
    'TwoH':  (['twohandequip', 'twohandattackequip'],
              ['twohandunequip', 'twohandattackunequip'], (5, 6)),
    'Bow':   (['bowequip', 'bowattackequip'],
              ['bowunequip', 'bowattackunequip'], (7,)),
    'Staff': (['staffequip', 'staffattackequip'],
              ['staffunequip', 'staffattackunequip'], (8,)),
}

#: Combat idles, most-armed stance first. See: docs/commentary/asset_convert_creature.md#combat-idle-clips
COMBAT_IDLE_CANDIDATES = ['twohandidle', 'onehandidle', 'staffidle',
                          'bowidle', 'handtohandidle']

#: Delivery -> kf stems; _a/_b/_c are alternates. See: docs/commentary/asset_convert_creature.md#cast-clip-preference
CAST_MODES = {
    'Self':   ['castself', 'castself_a', 'castself_b', 'castself_c'],
    'Target': ['casttarget', 'casttarget_a', 'casttarget_b', 'casttarget_c'],
    'Touch':  ['casttouch', 'casttouch_a', 'casttouch_b', 'casttouch_c'],
}

#: The chain plays one gesture; aimed throw first. See: docs/commentary/asset_convert_creature.md#cast-clip-preference
CAST_CLIP_PREFERENCE = ('Target', 'Touch', 'Self')


# ---------------------------------------------------------------------------
# AnimGroup fallback (the AUTHORED clip binding)
# ---------------------------------------------------------------------------

#: AnimGroup name (lowercased) -> the locomotion state it binds.
_ANIMGROUP_LOCOMOTION = {
    'forward': 'MoveForward',
    'fastforward': 'MoveForward',
    'runforward': 'MoveForward',
    'backward': 'MoveBackward',
    'fastbackward': 'MoveBackward',
    'turnleft': 'TurnLeft',
    'turnright': 'TurnRight',
    'left': 'StrafeLeft',
    'right': 'StrafeRight',
}

#: AnimGroup -> (state, run slot); feeds the speed blend, does not replace the walk.
_ANIMGROUP_RUN = {
    'fastforward': ('MoveForward', 'run'),
    'runforward': ('MoveForward', 'run'),
    'fastbackward': ('MoveBackward', 'run_back'),
    'runbackward': ('MoveBackward', 'run_back'),
}


def _skip_nif_header(d: bytes, o: int, ver: int, uver: int) -> int:
    """Offset of block 0's payload, given the offset just past the version.

    Walks nif.xml's `Header` compound field by field: Export Info and User
    Version 2 share the `User Version >= 10` gate, Block Size starts at
    20.2.0.7 and the string table at 20.1.0.3.
    """
    nblocks, = struct.unpack_from('<I', d, o)
    o += 4
    if uver >= 10 or (uver == 1 and ver != 0x0A020000):
        o += 4
        for _ in range(3):
            o += 1 + d[o]
    ntypes, = struct.unpack_from('<H', d, o)
    o += 2
    for _ in range(ntypes):
        ln, = struct.unpack_from('<I', d, o)
        o += 4 + ln
    o += 2 * nblocks
    if ver >= 0x14020007:
        o += 4 * nblocks
    if ver >= 0x14010003:
        nstr, = struct.unpack_from('<I', d, o)
        o += 8
        for _ in range(nstr):
            ln, = struct.unpack_from('<I', d, o)
            o += 4 + ln
    return o + 4


def read_animgroup(kf_path: str):
    """The NiControllerSequence name of a .kf -- its AnimGroup, or None.

    Header-only read: the name is the first field of block 0, so nothing past
    the header is decoded (0.06 ms/file vs a full pyffi parse).

    See: docs/commentary/asset_convert_creature.md#9-clip-claiming-animgroup-binding
    """
    try:
        with open(kf_path, 'rb') as f:
            d = f.read(131072)
    except OSError:
        return None
    nl = d.find(b'\n')
    if nl < 0:
        return None
    try:
        o = nl + 1
        ver, = struct.unpack_from('<I', d, o)
        o += 4
        if ver >= 0x14000004:
            o += 1
        uver, = struct.unpack_from('<I', d, o)
        o = _skip_nif_header(d, o + 4, ver, uver)
        ln, = struct.unpack_from('<I', d, o)
    except (struct.error, IndexError):
        return None
    if not 0 < ln < 128:
        return None
    name = d[o + 4:o + 4 + ln]
    if len(name) != ln:
        return None
    try:
        return name.decode('ascii')
    except UnicodeDecodeError:
        return None


def _unclaimed_land_stems(kfs: dict, used: set) -> list:
    """Stems no pass has claimed; swim clips are excluded."""
    return sorted(s for s in kfs
                  if s not in used and not s.startswith('swim'))


def _base_gait_first(stem: str) -> tuple:
    """Sort key ranking a base gait ahead of a weapon-stance variant."""
    prefixes = [p for p, _stance in ATTACK_STANCE_PREFIXES]
    return (next((i for i, p in enumerate(prefixes)
                  if stem.startswith(p)), -1) + 1, stem)


def _claim_by_animgroup(out: dict, kfs: dict, used: set) -> None:
    """Fill still-empty locomotion slots from unclaimed clips' AnimGroups.

    Purely additive: a slot the stem pass filled is never overwritten, and a
    clip the stem pass claimed is never re-read.
    """
    unclaimed = _unclaimed_land_stems(kfs, used)
    if not unclaimed:
        return
    unclaimed.sort(key=_base_gait_first)
    groups = {}
    for stem in unclaimed:
        g = read_animgroup(kfs[stem])
        if g:
            groups[stem] = g.lower()
    _claim_walks_then_runs(out, kfs, used, unclaimed, groups)
    _promote_fast_to_walk(out, kfs, used, unclaimed, groups)


def _claim_walks_then_runs(out: dict, kfs: dict, used: set,
                           unclaimed: list, groups: dict) -> None:
    """Fill the gait slots, walks before runs.

    Walk gaits go first so a `fastforward` cannot take the walk slot while a
    plain `forward` is still available for it, and a run gait is claimed only
    once its own walk slot is filled.
    """
    for want_run in (False, True):
        for stem in unclaimed:
            g = groups.get(stem)
            if stem in used or not g or (g in _ANIMGROUP_RUN) != want_run:
                continue
            if want_run:
                state, slot = _ANIMGROUP_RUN[g]
                if not out['locomotion'].get(state) or out.get(slot):
                    continue
                out[slot] = kfs[stem]
                used.add(stem)
                continue
            state = _ANIMGROUP_LOCOMOTION.get(g)
            if not state or out['locomotion'].get(state):
                continue
            out['locomotion'][state] = kfs[stem]
            used.add(stem)


def _promote_fast_to_walk(out: dict, kfs: dict, used: set,
                          unclaimed: list, groups: dict) -> None:
    """Use a fast forward clip as the walk when no walk gait was found.

    A creature whose ONLY forward clip is a fast one still needs to walk, so
    it is promoted rather than leaving MoveForward empty and the actor sliding.
    """
    for stem in unclaimed:
        if stem in used or out['locomotion'].get('MoveForward'):
            continue
        if groups.get(stem) in ('fastforward', 'runforward'):
            out['locomotion']['MoveForward'] = kfs[stem]
            used.add(stem)


def _claim_gaits(out: dict, kfs: dict, used: set) -> None:
    """Claim the idle, the combat-ready idle and the locomotion gait set.

    The combat idle is claimed BEFORE locomotion so a stance idle cannot be
    stolen, and before the attack sweep so an 'attack'-infixed spelling is not
    mistaken for an attack clip. The run gaits feed the MoveForward parametric
    speed blend (vanilla ForwardWalkBlend layout).
    """
    for cand in IDLE_CANDIDATES:
        if cand in kfs:
            out['idle'] = kfs[cand]
            used.add(cand)
            break
    for cand in COMBAT_IDLE_CANDIDATES:
        if cand in kfs and cand not in used:
            out['combat_idle'] = kfs[cand]
            used.add(cand)
            break
    for state, (names, _e, _x) in LOCOMOTION_STATES.items():
        for n in names:
            if n in kfs:
                out['locomotion'][state] = kfs[n]
                used.add(n)
                break
    for gait, base, names in (('run', 'forward', ('runforward', 'fastforward')),
                              ('run_back', 'backward',
                               ('runbackward', 'fastbackward'))):
        if base not in used:
            continue
        for n in names:
            if n in kfs and n not in used:
                out[gait] = kfs[n]
                used.add(n)
                break


def _claim_single_play(out: dict, kfs: dict, used: set) -> None:
    """Claim the single-play interrupt clips (recoil, stagger, death)."""
    for state, (names, _e, _x) in SINGLE_PLAY.items():
        for n in names:
            if n in kfs:
                out['single'][state] = kfs[n]
                used.add(n)
                break


def _claim_equip(out: dict, kfs: dict, used: set) -> None:
    """Claim the weapon draw/sheathe pair for each stance (see EQUIP_STANCES).

    Claimed BEFORE the attack sweep: 'handtohandattackequip' contains 'attack'
    and would otherwise be mistaken for an attack clip.
    """
    for stance, (eq_names, uneq_names, _types) in EQUIP_STANCES.items():
        eq = next((kfs[n] for n in eq_names if n in kfs and n not in used),
                  None)
        if eq is None:
            continue
        uneq = next((kfs[n] for n in uneq_names if n in kfs and n not in used),
                    None)
        out['equip'][stance] = (eq, uneq)
        used.add(os.path.splitext(os.path.basename(eq))[0].lower())
        if uneq:
            used.add(os.path.splitext(os.path.basename(uneq))[0].lower())


def _claim_tables(out: dict, kfs: dict, used: set) -> None:
    """Claim the cast, swim and block clip sets from their name tables.

    All three are claimed before the attack sweep so a substring like
    'blockattack' cannot be stolen by it.
    """
    for mode, names in CAST_MODES.items():
        for n in names:
            if n in kfs and n not in used:
                out['cast'][mode] = kfs[n]
                used.add(n)
                break
    for table, key in ((SWIM_CLIPS, 'swim'), (BLOCK_CLIPS, 'block')):
        for role, names in table.items():
            for n in names:
                if n in kfs and n not in used:
                    out[key][role] = kfs[n]
                    used.add(n)
                    break


#: Knockdown-recovery kf stems under idleanims/, every authored spelling -> the role naming its clips.
GETUP_ROLES = (
    (('getupfaceup', 'getup_faceup'), 'FaceUp'),
    (('getupfacedown', 'getup_facedown'), 'FaceDown'),
    (('getupleft', 'getup_left'), 'Left'),
    (('getupright', 'getup_right'), 'Right'),
)


def getup_clips(creature_dir: str) -> list:
    """[(role, kf path)] for the getup clips Oblivion keeps in the folder's idleanims/.

    See: docs/commentary/asset_convert_creature.md#getup-from-ragdoll
    """
    sub = next((os.path.join(creature_dir, d) for d in os.listdir(creature_dir)
                if d.lower() == 'idleanims'
                and os.path.isdir(os.path.join(creature_dir, d))), None)
    if not sub:
        return []
    kfs = {os.path.splitext(f)[0].lower(): os.path.join(sub, f)
           for f in os.listdir(sub) if f.lower().endswith('.kf')}
    out = []
    for names, role in GETUP_ROLES:
        kf = next((kfs[n] for n in names if n in kfs), None)
        if kf:
            out.append((role, kf))
    return out


def classify_clips(creature_dir: str) -> dict:
    """Scan an Oblivion creature folder into the v1 graph's clip roles.

    Returns {'idle': path, 'locomotion': {state: path}, 'attacks': [paths],
             'single': {state: path}, 'extra': [paths]}.

    Pass ORDER is load-bearing: each pass marks what it claimed, so equip runs
    before the attack sweep ('handtohandattackequip' contains 'attack'), and
    the AnimGroup fallback runs last, filling only slots every stem table left
    empty.

    See: docs/commentary/asset_convert_creature.md#the-clip-claim-tables
    """
    kfs = {}
    for fn in os.listdir(creature_dir):
        if fn.lower().endswith('.kf'):
            kfs[os.path.splitext(fn)[0].lower()] = os.path.join(
                creature_dir, fn)
    alias_clips(creature_dir, kfs)

    out = {'idle': None, 'locomotion': {}, 'attacks': [], 'single': {},
           'extra': [], 'run': None, 'equip': {}, 'cast': {}, 'swim': {},
           'block': {}, 'combat_idle': None, 'run_back': None}
    used = set()
    _claim_gaits(out, kfs, used)
    _claim_single_play(out, kfs, used)
    _claim_equip(out, kfs, used)
    _claim_tables(out, kfs, used)
    _claim_by_animgroup(out, kfs, used)

    claimed_paths = {kfs[n] for n in used if n in kfs}
    for name, path in kfs.items():
        if name in used or path in claimed_paths:
            continue
        claimed_paths.add(path)
        if 'attack' in name:
            out['attacks'].append(path)
        else:
            out['extra'].append(path)
    out['attacks'].sort()
    out['getup'] = getup_clips(creature_dir)
    _promote_water_native(out, kfs)
    out['attack_stance'] = {p: attack_stance(p) for p in out['attacks']}
    return out


#: Land-gait stems, any spelling. See: docs/commentary/asset_convert_creature.md#water-native-promotion
_LAND_GAIT_MARKERS = ('forward', 'backward', 'turnleft', 'turnright')


def _is_water_native(out: dict, kfs: dict) -> bool:
    """Whether every gait this creature ships is a swim clip."""
    if not out['swim'].get('forward'):
        return False
    return not any(any(m in stem for m in _LAND_GAIT_MARKERS)
                   for stem in kfs if not stem.startswith('swim'))


def _promote_water_native(out: dict, kfs: dict) -> None:
    """Move a pure swimmer's swim clips into the land locomotion slots.

    Amphibians keep the land graph + SwimState split; only a creature with no
    land gait at all is promoted.

    See: docs/commentary/asset_convert_creature.md#water-native-promotion
    """
    if not _is_water_native(out, kfs):
        return
    sw = out['swim']
    out['locomotion']['MoveForward'] = sw['forward']
    if sw.get('fast'):
        out['run'] = sw['fast']
    if sw.get('backward'):
        out['locomotion']['MoveBackward'] = sw['backward']
    if sw.get('left'):
        out['locomotion']['TurnLeft'] = sw['left']
    if sw.get('right'):
        out['locomotion']['TurnRight'] = sw['right']
    _swap_in_swim_idles(out, kfs, sw)
    out['swim'] = {}


def _swap_in_swim_idles(out: dict, kfs: dict, sw: dict) -> None:
    """Make the swim idles the resting and guard-up poses.

    A creature that never leaves the water rests in its swim idle (vanilla
    fish MainIdle plays exactly that), so any land idle is retired to extra.
    The guard-up idle is spelled with the swim prefix -- slaughterfish
    swimhandtohandidle -- which the land-name sweep misses.
    """
    if sw.get('idle'):
        if out['idle']:
            out['extra'].append(out['idle'])
        out['idle'] = sw['idle']
    if out['combat_idle']:
        return
    for cand in COMBAT_IDLE_CANDIDATES:
        p = kfs.get('swim' + cand)
        if p:
            out['combat_idle'] = p
            if p in out['extra']:
                out['extra'].remove(p)
            break


#: kf filename prefix -> EQUIP_STANCES key; longest first, so 'swimhandtohand' beats 'swim'.
ATTACK_STANCE_PREFIXES = (
    ('swimhandtohand', 'H2H'), ('swimonehand', 'OneH'),
    ('swimtwohand', 'TwoH'), ('swimstaff', 'Staff'), ('swimbow', 'Bow'),
    ('handtohand', 'H2H'), ('onehand', 'OneH'), ('twohand', 'TwoH'),
    ('staff', 'Staff'), ('bow', 'Bow'),
)


def attack_stance(kf_path: str):
    """EQUIP_STANCES key an attack clip belongs to, or None when unprefixed.

    An unprefixed clip (plain `attackpower.kf`) is the creature's only version
    of that attack and must stay ungated -- gating it would leave the creature
    unable to attack at all."""
    base = os.path.splitext(os.path.basename(kf_path))[0].lower()
    for prefix, stance in ATTACK_STANCE_PREFIXES:
        if base.startswith(prefix):
            return stance
    return None


def combat_idle_clip(clips: dict):
    """The stance idle if the creature ships one, else the plain idle."""
    return clips.get('combat_idle') or clips.get('idle')


def clip_state_name(kf_path: str) -> str:
    """The clip's stem, which names its state and its Animations/ file."""
    base = os.path.splitext(os.path.basename(kf_path))[0]
    return re.sub(r'[^A-Za-z0-9]', '', base)


def build_attack_events(clips: dict) -> list:
    """attackStart event names, shared verbatim with RACE ATKE."""
    return [f'attackStart_TES4_{clip_state_name(p)}'
            for p in clips['attacks']]


def cast_clip(clips: dict):
    """The one .kf the cast chain plays, or None for a non-caster."""
    for mode in CAST_CLIP_PREFERENCE:
        kf = clips.get('cast', {}).get(mode)
        if kf:
            return kf
    return None


#: Cast phases, named as vanilla names its clips; only the Loop repeats.
CAST_PHASES = ('In', 'Loop', 'Out')


def cast_phase_defs(clips: dict) -> list:
    """(state_name, kf_path, phase, anim_stem) per cast STATE.

    Oblivion authors one clip per delivery; Skyrim needs a charge (In), a
    hold (Loop) and a release (Out) — see CAST_MODES for the full engine
    handshake. The three are cut from ONE source .kf around the authored
    'Hit' key, so each phase gets its OWN animation file (anim_stem).
    State names follow the vanilla clip naming (Mag_FF_*).
    """
    kf = cast_clip(clips)
    if kf is None:
        return []
    stem_base = clip_state_name(kf)
    return [(f'Mag_FF_{phase}', kf, phase, f'{stem_base}_{phase}')
            for phase in CAST_PHASES]


def state_defs(clips: dict) -> list:
    """The graph's clip-generator list, shared by the behavior XML and the
    animationdata manifest: (state_name, kf_path, looping, enter_evt,
    end_evt) tuples, Idle first.

    See: docs/commentary/asset_convert_creature.md#state-defs-end-events
    """
    defs = []
    if clips['idle']:
        defs.append(('Idle', clips['idle'], True, None, None))
    for st, (_names, enter, _exit) in LOCOMOTION_STATES.items():
        if st in clips['locomotion']:
            defs.append((st, clips['locomotion'][st], True, enter, None))
    ci = combat_idle_clip(clips)
    if ci:
        defs.append(('CombatStance', ci, True,
                     'combatStanceStart', None))
    for st, (_names, enter, _stop) in SINGLE_PLAY.items():
        if st in clips['single']:
            defs.append((st, clips['single'][st], False, enter,
                         'returnToDefault' if st != 'Death' else None))
    for kf, evt in zip(clips['attacks'], build_attack_events(clips)):
        defs.append((f'Attack_{clip_state_name(kf)}', kf, False, evt,
                     'returnToDefault'))
    _append_equip_states(defs, clips)
    _append_swim_states(defs, clips)
    _append_block_states(defs, clips)
    _append_cast_states(defs, clips)
    return defs


def _append_equip_states(defs: list, clips: dict) -> None:
    """Add the single-play draw/sheathe states, one pair per stance.

    The clip END fires the reply the combat controller waits for -- weaponDraw
    once armed, weaponSheathe once stowed -- so the actor is cleared to attack.
    """
    for stance, (eq, uneq) in sorted(clips.get('equip', {}).items()):
        defs.append((f'Equip_{stance}', eq, False,
                     f'equipStart_{stance}', 'weaponDraw'))
        if uneq:
            defs.append((f'Unequip_{stance}', uneq, False,
                         f'unequipStart_{stance}', 'weaponSheathe'))


def _append_swim_states(defs: list, clips: dict) -> None:
    """Add the swim sub-machine, mirroring the land standing/locomotion split.

    It is driven by the same engine events as the land states.
    """
    sw = clips.get('swim', {})
    if not sw.get('forward'):
        return
    defs.append(('SwimIdle', sw.get('idle') or sw['forward'], True,
                 'swimStart', None))
    defs.append(('SwimMove', sw['forward'], True, 'moveStart', None))
    if sw.get('fast'):
        defs.append(('SwimMoveFast', sw['fast'], True, None, None))
    if sw.get('backward'):
        defs.append(('SwimBack', sw['backward'], True, 'moveBackward', None))
    for role, st in (('left', 'SwimTurnLeft'), ('right', 'SwimTurnRight')):
        if sw.get(role):
            defs.append((st, sw[role], True,
                         'turnLeft' if role == 'left' else 'turnRight', None))


def _append_block_states(defs: list, clips: dict) -> None:
    """Add the looping guard and its single-play flinch."""
    blk = clips.get('block', {})
    if not blk.get('idle'):
        return
    defs.append(('Block', blk['idle'], True, 'blockStart', None))
    if blk.get('hit'):
        defs.append(('BlockHit', blk['hit'], False, 'blockHitStart',
                     'blockHitStop'))


def _append_cast_states(defs: list, clips: dict) -> None:
    """Add the three cast phases: In charges, Loop holds, Out releases.

    Each phase is cut from the same source .kf, so each carries its own
    animation file rather than sharing one.

    See: docs/commentary/asset_convert_creature.md#7-spellcasting-the-magic-handshake-implemented-2026-08-21
    """
    for st, kf, phase, _stem in cast_phase_defs(clips):
        enter = {'In': 'Spell_FireForget_LH', 'Loop': 'Magic_Pre_Out',
                 'Out': 'Spell_Release'}[phase]
        end_evt = {'In': 'Magic_Pre_Out', 'Loop': None,
                   'Out': 'Spell_Stop'}[phase]
        defs.append((st, kf, phase == 'Loop', enter, end_evt))


def cast_anim_stems(clips: dict) -> dict:
    """{state_name: animation stem} for the cast states."""
    return {st: stem for st, _kf, _p, stem in cast_phase_defs(clips)}


def speed_blend_plan(clips: dict, speeds: dict) -> list:
    """Children of the MoveForward parametric speed blend:
    [(clip_gen_name, kf_path, playback_rate, anchor u/s)], anchors strictly
    increasing.  None when the walk clip has no usable root-motion speed.

    See: docs/commentary/asset_convert_creature.md#forward-blend-layout
    """
    fwd = clips['locomotion'].get('MoveForward')
    walk = speeds.get('walk')
    if not fwd or not walk:
        return None
    return [('MoveForwardSlow', fwd, max(0.02, round(5.0 / walk, 3)), 5.0),
            ('MoveForward', fwd, 1.0, walk)]


def run_blend_plan(clips: dict, speeds: dict) -> list:
    """The RUN-family blend — its own state, never mixed with the walk clip.

    None unless the run gait is meaningfully faster than the walk.

    See: docs/commentary/asset_convert_creature.md#forward-blend-layout
    """
    run_kf, run, walk = clips.get('run'), speeds.get('run'), speeds.get('walk')
    if not run_kf or not run or not walk or run <= walk * 1.05:
        return None
    return [('MoveForwardRunSlow', run_kf, 0.75, round(run * 0.75, 3)),
            ('MoveForwardRun', run_kf, 1.0, run)]


def gait_thresholds(speeds: dict) -> tuple:
    """(walkStart below, runStart above) SpeedSampled thresholds, with a 15%
    hysteresis band; the families need not overlap.

    See: docs/commentary/asset_convert_creature.md#forward-blend-layout
    """
    walk, run = speeds['walk'], speeds['run']
    run_bottom = run * 0.75
    hi = max(walk * 1.05, min(run_bottom, (walk + run_bottom) / 2.0))
    lo = max(walk, hi * 0.85)
    return round(lo, 2), round(hi, 2)


def backward_blend_plan(clips: dict, speeds: dict) -> list:
    """Same treatment for MoveBackward (AI backpedals slowly)."""
    back = clips['locomotion'].get('MoveBackward')
    spd = speeds.get('back')
    if not back or not spd:
        return None
    return [('MoveBackwardSlow', back, max(0.02, round(5.0 / spd, 3)), 5.0),
            ('MoveBackward', back, 1.0, spd)]


def swim_blend_plan(clips: dict, speeds: dict) -> list:
    """SwimMove parametric blend: slow-creep child + swim (+ fast) at their
    natural anchors, rate 1.0 — the vanilla monolithic layout (chaurus)."""
    sw = clips.get('swim', {})
    fwd, spd = sw.get('forward'), speeds.get('swim')
    if not fwd or not spd:
        return None
    plan = [('SwimMoveSlow', fwd, max(0.02, 5.0 / spd)),
            ('SwimMove', fwd, 1.0)]
    fast_spd = speeds.get('swimfast')
    if sw.get('fast') and fast_spd and fast_spd > spd * 1.05:
        plan.append(('SwimMoveFast', sw['fast'], 1.0))
    natural = {id(fwd): spd}
    if sw.get('fast'):
        natural[id(sw['fast'])] = fast_spd
    out, last = [], 0.0
    for nm, kf, rate in plan:
        anchor = natural[id(kf)] * rate
        if anchor <= last * 1.01:
            continue
        out.append((nm, kf, rate, anchor))
        last = anchor
    return out if len(out) >= 2 else None
