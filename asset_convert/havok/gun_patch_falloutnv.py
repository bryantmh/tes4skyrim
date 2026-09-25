"""Splice the gun machines into the SSE humanoid graphs and register them.

Reads the vanilla behavior/character packfiles (skyrim_assets), patches
them (humanoid_graph) with the gun_graph_falloutnv machines and writes the
patched copies under the plugin's `meshes\\actors\\character\\`, once for
the third-person project and once for `_1stperson` over its own clip set.
Every other humanoid graph keyed on a hand type gets the crossbow slot
copied for type 13, so no selector or machine is ever short of an entry.

Also returns the fragment entries that bind the new clip generators in the
vanilla DefaultMale/DefaultFemale/FirstPerson animation cache projects
(animation_data `animdata_append` / `animsetdata_append`).
See: docs/commentary/asset_convert_falloutnv.md#gun-graph
"""

import os

from asset_convert.havok.animation_data import (clip_block_lines,
                                                crc_triple_lines,
                                                motion_entry_lines)
from asset_convert.havok.gun_graph_falloutnv import (
    BASH_EVENTS, CROSSBOW_ATTACK, ENGINE_RELOAD, FIRE_EVENT, GUN_EVENTS, GUN_HAND_TYPE,
    GUN_VARIABLES,
    LOOP_SPEED_VAR, RELOAD_ALLOWED, RELOAD_REQUEST, ZOOM_BLEND_VAR, GunClips, GunGraphBuilder,
    attack_machine, class_selector, equip_gen, loco_machine, ready_machine,
    unequip_gen)
from asset_convert.havok.gun_moves_falloutnv import (SPRINT_SELECTORS,
                                                     jump_replacements,
                                                     sprint_selector)
from asset_convert.havok.humanoid_graph import (LAST_VANILLA_TYPE,
                                                CharacterFile, HumanoidGraph,
                                                param_text)
from asset_convert.sources.skyrim_assets import get_asset_bytes

CHARACTER = 'meshes\\actors\\character\\'
#: The five graphs that get real gun nodes.
PATCHED = ('0_master', '1hm_behavior', 'weapequip', '1hm_locomotion',
           'sprintbehavior')
#: Every other humanoid graph that may key a slot on a hand type.
CLONED = ('mt_behavior', 'bashbehavior', 'blockbehavior',
          'staggerbehavior', 'magicbehavior', 'horsebehavior',
          'shout_behavior', 'idlebehavior', 'bow_direction_behavior',
          'crossbow_direction_behavior', 'magicmountedbehavior',
          'shoutmounted_behavior', 'magic_readied_direction_behavior')
#: Per view: graph subfolder, work-dir stem prefix, (character hkx, project txt) pairs, manifest section.
VIEWS = (('', '', (('characters\\defaultmale.hkx', 'DefaultMale.txt'),
                   ('characters female\\defaultfemale.hkx',
                    'DefaultFemale.txt')), 'clips'),
         ('_1stperson', '1st_',
          (('_1stperson\\characters\\firstperson.hkx', 'FirstPerson.txt'),),
          'first_person'))
#: The swap events the vanilla CrossBow set file lists (animationsetdata V3).
SET_SWAP_EVENTS = ('MagicWeap_ForceEquip', 'swimForceEquip', 'WeapEquip',
                   'WeapOutRightReplaceForceEquip')
SET_FILE = 'TES4Guns.txt'


# ---------------------------------------------------------------------------
# Per-file patches
# ---------------------------------------------------------------------------

def _load(rel: str, work_dir: str, stem: str, cls=HumanoidGraph):
    """The vanilla packfile at `rel` decompiled, or None when absent."""
    raw = get_asset_bytes(CHARACTER + rel)
    if raw is None:
        return None
    return cls.from_hkx(raw, work_dir, stem)


def _prepare(g: HumanoidGraph, clips: GunClips) -> GunGraphBuilder:
    """Add the gun tables to a graph; a builder whose ids follow the file's."""
    for v in GUN_VARIABLES:
        g.add_variable(v)
    g.add_variable(LOOP_SPEED_VAR, 1.0, real=True)
    g.add_variable(ZOOM_BLEND_VAR, 0.0, real=True)
    parts = [s for s, e in clips.entries.items() if e.get('parts')]
    for e in (*GUN_EVENTS, *parts):
        if e not in g.events:
            g.add_event(e)
    return GunGraphBuilder(g.events, g.variables, g.next_id(), clips)


def _finish(g: HumanoidGraph, gb: GunGraphBuilder, top, replacements: dict,
            out_path: str) -> list:
    """Splice, fill every hand-type slot, widen conditions, compile."""
    g.splice(gb.render(top))
    g.extend_type_slots(GUN_HAND_TYPE, replacements)
    g.widen_type_conditions(LAST_VANILLA_TYPE, GUN_HAND_TYPE)
    g.write(out_path)
    return gb.generators


def _camera_on_upper(g: HumanoidGraph, gb: GunGraphBuilder, lower_bw, upper_bw) -> tuple:
    """(lower, upper) bone weights for the fire and reload clips: the vanilla
    pair, or in the first-person graph copies with the camera bone moved to
    the upper body, so the clips' camera kick reaches the view.
    See: docs/commentary/asset_convert_falloutnv.md#camera-kick
    """
    bone = gb.clips.camera_bone
    if bone is None:
        return lower_bw, upper_bw
    out = []
    for ref, weight in ((lower_bw, 0.0), (upper_bw, 1.0)):
        values = param_text(g.obj(ref), 'boneWeights').split()
        values += ['0.000000'] * (bone + 1 - len(values))
        values[bone] = f'{weight:.6f}'
        arr = gb.add('hkbBoneWeightArray')
        arr.param('variableBindingSet', 'null')
        arr.param_array('boneWeights', values)
        out.append(arr.ref)
    return tuple(out)


def patch_1hm(g: HumanoidGraph, clips: GunClips, out_path: str) -> list:
    """The readied slot and the attack state, entered on FalloutRuntime's
    TES4GunFire: the engine's own attack actions are swallowed for a gun
    holder, so nothing of the crossbow's attack path reaches the graph.
    See: docs/commentary/tes_runtime_guns.md#own-the-click
    """
    gb = _prepare(g, clips)
    blend = g.find('CrossBow_AttackUpperLowerBody3rdPBlend')
    lower_bw, upper_bw = [param_text(g.obj(c), 'boneWeights')
                          for c in g.ref_list(blend, 'children')]
    ready = class_selector(gb, 'TES4Gun_Ready_MSG',
                           lambda c: ready_machine(gb, c, lower_bw, upper_bw))
    fire_bw = _camera_on_upper(g, gb, lower_bw, upper_bw)
    attack = class_selector(
        gb, 'TES4Gun_Attack_MSG',
        lambda c: attack_machine(gb, c, *fire_bw))
    reload = class_selector(
        gb, 'TES4Gun_ReloadEntry_MSG',
        lambda c: attack_machine(gb, c, *fire_bw, True))
    g.splice(gb.render(ready))
    _gun_root_states(g, attack.ref, reload.ref)
    g.extend_type_slots(GUN_HAND_TYPE,
                        {'1HM_Readied_BehaviorGraph': ready.ref})
    g.widen_type_conditions(LAST_VANILLA_TYPE, GUN_HAND_TYPE, BASH_EVENTS)
    g.write(out_path)
    return gb.generators


def _gun_root_states(g: HumanoidGraph, attack_ref: str, reload_ref: str):
    """Two root states: the attack entered on TES4GunFire, and the
    same machine started at Reload, entered on the reload request and on
    the engine's own reloadStart (ammo equipped) while the magazine is not
    full; both leave on TES4GunAttackEnd. The vanilla crossbow transitions
    on reloadStart and crossbowAttackStart are gated to non-gun hand types.
    See: docs/commentary/tes_runtime_guns.md#bash-and-reload-events
    """
    root = g.find('1HM_Behavior', 'hkbStateMachine')
    sid = max(int(param_text(g.obj(r), 'stateId'))
              for r in g.ref_list(root, 'states')) + 1
    g.add_state(root, sid, 'TES4Gun_AttackState', attack_ref)
    g.add_state(root, sid + 1, 'TES4Gun_ReloadState', reload_ref)
    for s in (sid, sid + 1):
        g.add_transition(g.state_of(root, s), 'TES4GunAttackEnd', 0)
    gun = f'iRightHandType == {GUN_HAND_TYPE}'
    for event in (ENGINE_RELOAD, CROSSBOW_ATTACK):
        g.gate_transitions(event, f'iRightHandType != {GUN_HAND_TYPE}')
    reload_cond = f'({gun}) && ({RELOAD_ALLOWED})'
    for entry in (0, 1, 2):
        for event, to, cond in ((FIRE_EVENT, sid, gun),
                                (RELOAD_REQUEST, sid + 1, reload_cond),
                                (ENGINE_RELOAD, sid + 1, reload_cond)):
            g.add_transition(g.state_of(root, entry), event, to,
                             condition=cond, priority=1, first=True)


def patch_weapequip(g: HumanoidGraph, clips: GunClips, out_path: str) -> list:
    """Every equip selector keyed on the right hand gets the gun draw clip."""
    gb = _prepare(g, clips)
    equip = class_selector(gb, 'TES4Gun_Equip_MSG', lambda c: equip_gen(gb, c))
    names = [param_text(el, 'name') for el, kind in g.hand_type_slots()
             if kind == 'msg' and 'equip' in param_text(el, 'name').lower()
             and g.bound_variable(el, 'selectedGeneratorIndex') == 'iRightHandType']
    return _finish(g, gb, equip, {n: equip.ref for n in names}, out_path)


def patch_master(g: HumanoidGraph, clips: GunClips, out_path: str) -> list:
    """The sheathe slot and the six jump selectors of 0_master; everything
    else keyed on a type copies."""
    gb = _prepare(g, clips)
    unequip = class_selector(gb, 'TES4Gun_Unequip_MSG',
                             lambda c: unequip_gen(gb, c))
    replacements = {'WeapUnequipTypeBehavior': unequip.ref}
    replacements.update(jump_replacements(g, gb))
    return _finish(g, gb, unequip, replacements, out_path)


def patch_sprint(g: HumanoidGraph, clips: GunClips, out_path: str) -> list:
    """Both sprint side selectors play the class' run clip."""
    gb = _prepare(g, clips)
    sprint = sprint_selector(gb)
    return _finish(g, gb, sprint, {n: sprint.ref for n in SPRINT_SELECTORS},
                   out_path)


def patch_locomotion(g: HumanoidGraph, clips: GunClips, out_path: str) -> list:
    """The drawn-gun direction blend as the type-13 locomotion state."""
    gb = _prepare(g, clips)
    loco = class_selector(gb, 'TES4Gun_Loco_MSG', lambda c: loco_machine(gb, c))
    return _finish(g, gb, loco, {'Melee_Direction_Behavior': loco.ref},
                   out_path)


def clone_slots(g: HumanoidGraph, out_path: str) -> bool:
    """Copy the last vanilla type's slots for type 13; False when untouched."""
    if not g.hand_type_slots():
        return False
    g.extend_type_slots(GUN_HAND_TYPE, {})
    g.widen_type_conditions(LAST_VANILLA_TYPE, GUN_HAND_TYPE)
    g.write(out_path)
    return True


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

_PATCHERS = {'0_master': patch_master, '1hm_behavior': patch_1hm,
             'weapequip': patch_weapequip, '1hm_locomotion': patch_locomotion,
             'sprintbehavior': patch_sprint}


def patch_behaviors(clips: GunClips, char_out: str, work_dir: str,
                    view=VIEWS[0], log=print) -> list:
    """Patch one view's humanoid graphs; the clip generators created."""
    sub, prefix = view[0], view[1]
    rel_dir = f'{sub}\\behaviors' if sub else 'behaviors'
    out_dir = os.path.join(char_out, *rel_dir.split('\\'))
    generators = []
    for stem in PATCHED:
        g = _load(f'{rel_dir}\\{stem}.hkx', work_dir, prefix + stem)
        if g is None:
            raise FileNotFoundError(f'vanilla {rel_dir}\\{stem}.hkx is not '
                                    'available')
        generators += _PATCHERS[stem](g, clips,
                                      os.path.join(out_dir, f'{stem}.hkx'))
    cloned = 0
    for stem in CLONED:
        g = _load(f'{rel_dir}\\{stem}.hkx', work_dir, prefix + stem)
        if g is not None and clone_slots(g, os.path.join(out_dir,
                                                         f'{stem}.hkx')):
            cloned += 1
    log(f'  Gun graph: patched {len(PATCHED)} {rel_dir} files '
        f'({len(generators)} clip generators), {cloned} cloned')
    return generators


def register_clips(clips: GunClips, generators: list, char_out: str,
                   work_dir: str, view=VIEWS[0]) -> dict:
    """Append the clips to the view's character files; the fragment appends."""
    anims = list(dict.fromkeys(clips.entries[g['stem']]['anim']
                               for g in generators))
    animdata, animsetdata = [], []
    for rel, project in view[2]:
        cf = _load(rel, work_dir, os.path.splitext(os.path.basename(rel))[0],
                   CharacterFile)
        if cf is None:
            raise FileNotFoundError(f'vanilla {rel} is not available')
        index = cf.append_animations(anims)
        cf.write(os.path.join(char_out, *rel.split('\\')))
        animdata.append({'project': project,
                         'clips': _clip_lines(clips, generators, index),
                         'motions': _motion_lines(clips, index)})
        stem = os.path.splitext(project)[0]
        animsetdata.append({'entry': f'{stem}Data\\{project}',
                            'set_file': SET_FILE,
                            'block': _set_block(clips, generators)})
    return {'animdata_append': animdata, 'animsetdata_append': animsetdata}


def _clip_lines(clips: GunClips, generators: list, index: dict) -> list:
    """animationdata clip blocks for every generator, against `index`."""
    lines = []
    for g in generators:
        e = clips.entries[g['stem']]
        lines += clip_block_lines(
            {'name': g['name'], 'rate': g['rate'], 'sounds': e['sounds'],
             'feet': e['feet'], 'events': g['events'],
             'duration': e['duration']}, index[e['anim']])
    return lines


def _motion_lines(clips: GunClips, index: dict) -> list:
    """One root-motion block per appended animation index."""
    lines = []
    for anim, uid in sorted(index.items(), key=lambda kv: kv[1]):
        e = next(c for c in clips.entries.values() if c['anim'] == anim)
        lines += motion_entry_lines(uid, e['duration'], e.get('motion'))
    return lines


def _set_block(clips: GunClips, generators: list) -> list:
    """The V3 set file: hand types 13, the fire clips as the attack data."""
    fires = [g['name'] for g in generators if 'attack' in g['stem']]
    lines = ['V3', str(len(SET_SWAP_EVENTS)), *SET_SWAP_EVENTS, '3',
             'iLeftHandType', str(GUN_HAND_TYPE), str(GUN_HAND_TYPE),
             'iRightHandType', str(GUN_HAND_TYPE), str(GUN_HAND_TYPE),
             'iWantMountedWeaponAnims', '0', '0',
             '1', FIRE_EVENT, '0', str(len(fires)), *fires]
    lines += crc_triple_lines(clips.anim_dir, clips.entries)
    return lines


def build_gun_graphs(manifest: dict, out_meshes_dir: str, work_dir: str,
                     log=print) -> dict:
    """Patch both humanoid projects for the manifest's guns; fragment appends.

    Returns {} when the manifest has no clips.
    See: docs/commentary/asset_convert_falloutnv.md#first-person-rig
    """
    appends = {'animdata_append': [], 'animsetdata_append': []}
    char_out = os.path.join(out_meshes_dir, 'actors', 'character')
    for view in VIEWS:
        section = manifest if view[3] == 'clips' else manifest.get(view[3])
        if not section or not section.get('clips'):
            continue
        clips = GunClips(section)
        if not clips.present_classes():
            continue
        generators = patch_behaviors(clips, char_out, work_dir, view, log)
        got = register_clips(clips, generators, char_out, work_dir, view)
        for k in appends:
            appends[k] += got[k]
        log(f'  Gun graph: {len(generators)} {view[0] or "3rd-person"} clips '
            f'registered in {len(got["animdata_append"])} projects')
    return appends if appends['animdata_append'] else {}
