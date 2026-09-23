"""FO3/FNV gun animations: the player's clips retargeted onto the Skyrim body.

The binding between a gun and its clips is AUTHORED on the WEAP record:
`DNAM.FalloutAnimType` names the class (`1hp` pistol, `2hr` rifle, `2ha`
automatic, `2hh` handle, `2hl` launcher), `DNAM.ReloadAnim` the reload letter
and `DNAM.AttackAnim` the attack, so `<class>reload<letter>` is the exact clip
a gun reloads with. Clip stems decompose as
`[sneak|pa|pasneak] <class> <action> [letter] [is] [up|down]`; the
locomotion set (`<class>forward`, `<class>fastleft`, `<class>turnright`) is
named the same way.

Every selected clip is retargeted (clip_retarget) onto the humanoid
skeleton.hkx bone order and written 64-bit under the plugin-independent
`meshes\\actors\\character\\animations\\<game>guns\\` (one corpus, one
humanoid graph), with a `guns_manifest.json` beside them for the graph and
cache stages (gun_graph_falloutnv).
See: docs/commentary/asset_convert_falloutnv.md#gun-animations
"""

from asset_convert.game_paths import current_namespace
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed

from asset_convert import paths
from asset_convert.character.skyrim_overrides_falloutnv import bone_map_for
from asset_convert.havok.clip_retarget import (TRANSLATED_BONES, Skeleton,
                                               fill_missing_tracks,
                                               first_frame_pose,
                                               load_pose_deltas,
                                               retarget_clip,
                                               retarget_error)
from asset_convert.havok.creature_pipeline import foot_enum_map
from asset_convert.havok.gun_vocabulary_falloutnv import (ANIM_TYPE_CLASS,
                                                          ATTACK_ACTIONS,
                                                          ATTACK_ANIMS,
                                                          PART_PREFIX,
                                                          RELOAD_LETTERS)
from asset_convert.havok.hkx_anim import (decode_clip, event_annotations,
                                          parse_kf_events, verify_hkx,
                                          write_clip_hkx)
from asset_convert.havok.hkx_xml import convert_hkx_to_amd64
from output_layout import assets_for
from tes5_import.base.text_reader import parse_export_file
from core.worker_budget import worker_count

#: Actions every gun of a class plays, regardless of its DNAM.
SHARED_ACTIONS = frozenset({'aim', 'equip', 'unequip', 'holster', 'idle'})

#: The locomotion actions (meshes\\characters\\_male\\locomotion and turns).
LOCO_ACTIONS = ('forward', 'backward', 'left', 'right', 'fastforward',
                'fastbackward', 'fastleft', 'fastright', 'turnleft',
                'turnright')

_STEM = re.compile(r'^(?P<prefix>pasneak|sneak|pa)?'
                   r'(?P<cls>1hp|2hr|2ha|2hh|2hl)(?P<rest>[a-z0-9]+)$')
_ACTION = re.compile(
    r'^(?P<action>aim|attack(?:left|right|loop|spin2|spin|throw[0-9]?|[0-9])'
    r'|reload|equip|unequip|holster|idle|jam|placemine[0-9]?'
    r'|fastforward|fastbackward|fastleft|fastright|forward|backward|left'
    r'|right|turnleft|turnright)'
    r'(?P<letter>[a-z])?(?P<start>start)?$')

GUN_MANIFEST = 'guns_manifest.json'

#: (source clip folder under characters, project subfolder, manifest key).
VIEWS = (('_male', '', 'clips'), ('_1stperson', '_1stperson', 'first_person'))

#: The bone only the FNV first-person skeleton has; it selects the rig below.
FIRST_PERSON_MARKER = 'Bip01 Looking'
#: First-person map on top of the body map; None drops a body-map entry.
FIRST_PERSON_BONE_MAP = {
    'Bip01 Looking': 'NPC LookNode [Look]',
    'Bip01 Translate': 'NPC Translate [Pos ]',
    'Bip01 Rotate': 'NPC Rotate [Rot ]',
    'Bip': 'NPC COM [COM ]', 'Camera1st': 'Camera1st [Cam1]',
    'Bip01 NonAccum': None}
#: Target bones that follow the source's camera-relative position.
FIRST_PERSON_TRANSLATED = ('NPC LookNode [Look]', 'NPC COM [COM ]',
                           'Camera1st [Cam1]')
FIRST_PERSON_ANCHOR = ('Bip01 Looking', 'NPC LookNode [Look]')
#: Arms whose hands reach FNV's camera-relative hand positions (IK).
FIRST_PERSON_ARMS = tuple((f'Bip01 {s} UpperArm', f'Bip01 {s} Forearm',
                           f'Bip01 {s} Hand') for s in 'RL')


# ---------------------------------------------------------------------------
# Classification and selection
# ---------------------------------------------------------------------------

def anim_prefix() -> str:
    """Clip folder relative to the PROJECT folder (clip generators)."""
    return 'Animations\\' + current_namespace().upper() + 'Guns\\'


def classify_stem(stem: str) -> dict:
    """Decompose a clip stem, or None when it is not a gun clip.

    Returns {'stem', 'prefix', 'cls', 'action', 'letter', 'start', 'iron',
    'pitch'}; `letter` is the reload/jam letter, `iron` the iron-sight
    variant, `pitch` 'up'/'down' or None.
    """
    m = _STEM.match(stem.lower())
    if not m:
        return None
    rest, pitch, iron = m['rest'], None, False
    for p in ('up', 'down'):
        if rest.endswith(p):
            pitch, rest = p, rest[:-len(p)]
            break
    if rest.endswith('is'):
        iron, rest = True, rest[:-2]
    a = _ACTION.match(rest)
    if not a or (a['letter'] and a['action'] not in ('reload', 'jam')):
        return None
    return {'stem': stem.lower(), 'prefix': m['prefix'] or '',
            'cls': m['cls'], 'action': a['action'], 'letter': a['letter'],
            'start': bool(a['start']), 'iron': iron, 'pitch': pitch}


def weapon_bindings(export_dir: str) -> list:
    """Every gun WEAP's authored clip binding, from the export."""
    weap = os.path.join(export_dir, 'WEAP.txt')
    if not os.path.exists(weap):
        return []
    out = []
    for rec in parse_export_file(weap):
        cls = ANIM_TYPE_CLASS.get(int(rec.get('DNAM.FalloutAnimType', -1)
                                      or 0))
        if not cls:
            continue
        reload = int(rec.get('DNAM.ReloadAnim', 0) or 0)
        out.append({
            'formid': rec.get('FormID'), 'edid': rec.get('EditorID'),
            'cls': cls,
            'reload': (RELOAD_LETTERS[reload]
                       if 0 <= reload < len(RELOAD_LETTERS) else 'a'),
            'attack': ATTACK_ANIMS.get(int(rec.get('DNAM.AttackAnim', 255)
                                           or 255)),
            'clip_size': int(rec.get('DATA.ClipSize', 0) or 0),
            'automatic': bool(int(rec.get('DNAM.Flags1', 0) or 0) & 0x02),
        })
    return out


def _wanted(c: dict, classes, reloads, attacks, any_default) -> bool:
    """Whether one classified stem belongs to the selected set."""
    if c['prefix'].startswith('pa') or c['cls'] not in classes:
        return False
    act = c['action']
    if c['iron'] and not (act == 'aim' or act.startswith('attack')):
        return False
    if act in SHARED_ACTIONS or act in LOCO_ACTIONS:
        return True
    if act == 'reload':
        return (c['cls'], c['letter']) in reloads
    return (act.startswith('attack')
            and ((c['cls'], act) in attacks or c['cls'] in any_default))


def select_stems(bindings: list, stems) -> list:
    """The clip stems the exported guns actually name (sneak set included).

    Power-armor (`pa`) sets are skipped: Skyrim has no power armor; of the
    iron-sight (`is`) variants the aim poses and the attacks come, for the
    zoom key.
    A gun whose attack is DEFAULT takes every attack of its class;
    every pitch and `<letter>start` variant of a wanted action comes along.
    """
    classes = {b['cls'] for b in bindings}
    reloads = {(b['cls'], b['reload']) for b in bindings}
    attacks = {(b['cls'], b['attack']) for b in bindings if b['attack']}
    any_default = {b['cls'] for b in bindings if not b['attack']}
    reloads |= _reload_fallbacks(reloads, stems)
    out = []
    for stem in stems:
        c = classify_stem(stem)
        if c and _wanted(c, classes, reloads, attacks, any_default):
            out.append(stem)
    return sorted(out)


def _reload_fallbacks(reloads, stems) -> set:
    """The class' first available reload, for a letter the corpus lacks.

    See: docs/commentary/asset_convert_falloutnv.md#gun-animations
    """
    have = {}
    for stem in stems:
        c = classify_stem(stem)
        if c and c['action'] == 'reload' and not c['prefix'] and c['letter']:
            have.setdefault(c['cls'], set()).add(c['letter'])
    out = set()
    for cls, letter in reloads:
        avail = have.get(cls)
        if avail and letter not in avail:
            out.add((cls, sorted(avail)[0]))
    return out


def clip_corpus(kf_dir: str) -> dict:
    """{stem: kf path} over the body clips and the locomotion folder."""
    out = {}
    for sub in ('', 'locomotion'):
        d = os.path.join(kf_dir, sub) if sub else kf_dir
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.lower().endswith('.kf'):
                out.setdefault(os.path.splitext(f)[0].lower(),
                               os.path.join(d, f))
    return out


# ---------------------------------------------------------------------------
# Per-clip conversion (runs in a worker process)
# ---------------------------------------------------------------------------

_RIG = {}


def _rig(skeleton_nif: str) -> dict:
    """Source/target skeletons, bone map, deltas and translation rule, once.

    The first-person skeleton (`Bip01 Looking`) targets the first-person
    hkx rig with the camera-relative translation rule. The bone map names
    Skyrim's NIF nodes (`WEAPON`); the Havok skeleton spells the same bone
    `Weapon`, so targets resolve case-insensitively.
    See: docs/commentary/asset_convert_falloutnv.md#first-person-rig
    """
    if skeleton_nif not in _RIG:
        src = Skeleton.from_nif(skeleton_nif)
        first = FIRST_PERSON_MARKER in src.index
        gen = paths.REPO / 'asset_convert' / 'generated'
        dst = Skeleton.from_hkx_json(str(gen / (
            'skeleton_hkx_skyrim_1stperson.json' if first
            else 'skeleton_hkx_skyrim_male.json')))
        bone_map = bone_map_for({n: None for n in src.names})
        if first:
            bone_map = {**bone_map, **FIRST_PERSON_BONE_MAP}
        hkx_name = {n.lower(): n for n in dst.names}
        _RIG[skeleton_nif] = dict(
            src=src, dst=dst, dst_bones=dst.bones(),
            bone_map={s: hkx_name.get(d.lower(), d)
                      for s, d in bone_map.items() if d},
            deltas=load_pose_deltas(str(
                gen / 'best_animation_pose_falloutnv.json')),
            translated=FIRST_PERSON_TRANSLATED if first else TRANSLATED_BONES,
            anchor=FIRST_PERSON_ANCHOR if first else None,
            ik=FIRST_PERSON_ARMS if first else ())
    return _RIG[skeleton_nif]


def retarget_gun_clip(kf_path: str, skeleton_nif: str, fps: float = 30.0,
                      fills=(), pose: bool = False):
    """(source clip, retargeted clip, motion, annotations, events) for one .kf.

    Hit keys are the FIRE frame of a gun clip, not a melee damage window:
    they reach the graph as arrowRelease triggers, never as HitFrame. The
    accum bone keeps its authored facing; bones the clip leaves out take
    the first frame of the first of `fills` (aim pose kfs) that has them.
    `pose` keeps only the clip's first frame, as a looping pose.
    See: docs/commentary/asset_convert_falloutnv.md#accum-root-identity
    """
    rig = _rig(skeleton_nif)
    clip, motion = decode_clip(kf_path, fps)
    if pose:
        clip, motion = first_frame_pose(clip, fps), None
    for kf in fills:
        fill_missing_tracks(clip, decode_clip(kf, fps)[0])
    events = parse_kf_events(clip.text_keys, foot_enum_map({0: '', 1: ''}))
    out = retarget_clip(clip, rig['src'], rig['dst'], rig['bone_map'],
                        rig['deltas'], translated=rig['translated'],
                        anchor=rig['anchor'], ik_chains=rig['ik'])
    return clip, out, motion, event_annotations(events, False), events


#: The bones the offline check measures: where a gun's motion lives.
CHECK_BONES = ('Bip01 R Hand', 'Bip01 L Hand', 'Bip01 R Forearm',
               'Bip01 L Forearm', 'Bip01 Head', 'Bip01 Pelvis')


def _key_times(clip, names) -> dict:
    """{key name lower: first time} for the FNV text keys named; an `a:`
    key (the next attack allowed) is recorded as `next`."""
    out = {}
    for t, s in clip.text_keys:
        k = s.strip().lower()
        if k.startswith('a:'):
            k = 'next'
        if (k in names or k == 'next') and k not in out:
            out[k] = float(t)
    return out


def _part_tracks(src_clip, stem: str) -> list:
    """The weapon-part nodes a clip animates; none for a looping aim pose,
    whose frame-0 trigger would restart the mesh sequence every cycle.
    See: docs/commentary/asset_convert_falloutnv.md#gun-parts
    """
    c = classify_stem(stem)
    if c is None or c['action'] == 'aim':
        return []
    return sorted(t.bone for t in src_clip.tracks if t.bone.startswith(PART_PREFIX))


def convert_one(kf_path: str, out_hkx: str, skeleton_nif: str,
                verify: bool = False, fps: float = 30.0,
                fills=(), stem: str = None,
                pose: bool = False) -> dict:
    """Retarget one clip to a 64-bit hkx; the manifest entry for it."""
    rig = _rig(skeleton_nif)
    src_clip, clip, motion, annotations, events = retarget_gun_clip(
        kf_path, skeleton_nif, fps, fills, pose)
    stem = stem or os.path.splitext(os.path.basename(kf_path))[0].lower()
    write_clip_hkx(clip, rig['dst_bones'], out_hkx, annotations)
    entry = {
        'stem': stem, 'anim': f'{anim_prefix()}{stem}.hkx',
        'duration': float(clip.duration), 'frames': len(clip.times),
        'tracks': len(clip.tracks), 'sounds': events['sounds'],
        'feet': events['feet'], 'hits': events['hits'],
        'keys': _key_times(src_clip, {'attach', 'detach'}),
        'parts': _part_tracks(src_clip, stem),
        'motion': None if motion is None else {
            'bone': motion['bone'], 'times': motion['times'].tolist(),
            'translations': (motion['translations'].tolist()
                             if motion['translations'] is not None else None),
            'rotations': (motion['rotations'].tolist()
                          if motion['rotations'] is not None else None)},
    }
    if verify:
        entry['verify'] = verify_hkx(out_hkx, clip, rig['dst'].names)
        entry['verify']['bones'] = retarget_error(
            src_clip, clip, rig['src'], rig['dst'], rig['bone_map'],
            CHECK_BONES)
    convert_hkx_to_amd64(out_hkx)
    return entry


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def gun_layout(out_meshes_dir: str, sub: str = '') -> dict:
    """Output dir plus the data-relative animation folder (plugin-independent).

    `sub` is the project subfolder (`_1stperson`): a project's animation
    names resolve relative to its own folder.
    See: docs/commentary/asset_convert_falloutnv.md#first-person-rig
    """
    project = ['actors', 'character'] + ([sub] if sub else [])
    leaf = current_namespace() + 'guns'
    return {'dir': os.path.join(out_meshes_dir, *project, 'animations',
                                leaf),
            'anim_dir': '\\'.join(['meshes', *project, 'animations', leaf])}


def read_manifest(out_meshes_dir: str) -> dict:
    """The plugin's gun manifest, or {} when it has none."""
    path = os.path.join(gun_layout(out_meshes_dir)['dir'], GUN_MANIFEST)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _level_aim(corpus: dict, prefix: str, cls: str, iron: str = ''):
    """The kf holding the class' level aim pose in this stance (`iron` is
    'is' for the sights): its `aim` clip, else its first attack clip
    (frame 0 is the aim pose), else None.
    See: docs/commentary/asset_convert_falloutnv.md#level-aim-from-the-fire-clip
    """
    return corpus.get(f'{prefix}{cls}aim{iron}') or next(
        (corpus[f'{prefix}{cls}{a}{iron}'] for a in ATTACK_ACTIONS
         if f'{prefix}{cls}{a}{iron}' in corpus), None)


def _fill_clips(corpus: dict, stem: str) -> tuple:
    """The level aim kfs a partial clip is composed over, first wins: an
    iron-sight clip's own stance before the hip aim. A hip aim has none.
    See: docs/commentary/asset_convert_falloutnv.md#first-person-hands-spread
    """
    c = classify_stem(stem)
    if c is None or (c['action'] == 'aim' and not c['iron']):
        return ()
    irons = ('is', '') if c['iron'] else ('',)
    kfs = (_level_aim(corpus, p, c['cls'], i)
           for i in irons for p in (c['prefix'], ''))
    return tuple(dict.fromkeys(k for k in kfs if k))


def _synthesized_aims(corpus: dict, classes) -> list:
    """[(aim stem, attack kf)] for every stance with no level aim clip:
    the attack clip's first frame stands in as the level aim (a hip stance
    only when it has pitched aims; an iron-sight stance whenever it has a
    fire clip, since the first-person set ships no pitched sights).
    See: docs/commentary/asset_convert_falloutnv.md#level-aim-from-the-fire-clip
    """
    out = []
    for cls in classes:
        for prefix in ('', 'sneak'):
            for iron in ('', 'is'):
                stem = f'{prefix}{cls}aim{iron}'
                pitched = f'{prefix}{cls}aim{iron}down' in corpus
                if stem in corpus or not (pitched or iron):
                    continue
                kf = _level_aim(corpus, prefix, cls, iron)
                if kf:
                    out.append((stem, kf))
    return out


def _plan_view(bindings, corpus: dict, names) -> list:
    """[(stem, kf, fill kf, pose only)] to convert: the selected clips, the
    level aims synthesized from an attack clip, and the base `mt`
    locomotion standing in for a class that has none of its own (FNV
    overlays it on the class aim, so it is filled with that aim).
    See: docs/commentary/asset_convert_falloutnv.md#first-person-rig
    """
    wanted = ([s.lower() for s in names] if names
              else select_stems(bindings, corpus))
    plan = [(s, corpus[s], _fill_clips(corpus, s), False) for s in wanted
            if s in corpus]
    if names:
        return plan
    classes = sorted({b['cls'] for b in bindings})
    plan += [(s, kf, _fill_clips(corpus, s), True)
             for s, kf in _synthesized_aims(corpus, classes)]
    for cls in classes:
        aim = _level_aim(corpus, '', cls)
        if f'{cls}forward' in corpus or not aim:
            continue
        plan += [(f'{cls}{d}', corpus[f'mt{d}'], (aim,), False)
                 for d in LOCO_ACTIONS if f'mt{d}' in corpus]
    return plan


def _convert_view(bindings, chars_dir: str, out_meshes_dir: str, view,
                  names, workers, verify, log) -> dict:
    """Retarget one view's clips; its manifest section (None without clips)."""
    folder, sub, _ = view
    kf_dir = os.path.join(chars_dir, folder)
    skeleton = os.path.join(kf_dir, 'skeleton.nif')
    if not os.path.isfile(skeleton):
        return None
    plan = _plan_view(bindings, clip_corpus(kf_dir), names)
    layout = gun_layout(out_meshes_dir, sub)
    os.makedirs(layout['dir'], exist_ok=True)
    log(f'  Retargeting {len(plan)} {folder} gun clips for '
        f'{len(bindings)} guns ({workers or worker_count()} workers)...')
    clips, failures = [], []
    with ProcessPoolExecutor(max_workers=workers or worker_count()) as pool:
        futs = {pool.submit(convert_one, kf,
                            os.path.join(layout['dir'], s + '.hkx'),
                            skeleton, verify, 30.0, fill, s, pose): s
                for s, kf, fill, pose in plan}
        for fut in as_completed(futs):
            stem = futs[fut]
            try:
                clips.append(fut.result())
            except Exception as e:
                failures.append((stem, f'{type(e).__name__}: {e}'))
                log(f'  [FAIL] {folder} {stem}: {failures[-1][1]}')
    clips.sort(key=lambda c: c['stem'])
    log(f'  Gun clips: {len(clips)} written, {len(failures)} failed -> '
        f'{layout["anim_dir"]}')
    return {'anim_dir': layout['anim_dir'], 'clips': clips,
            'classes': {c['stem']: classify_stem(c['stem']) for c in clips},
            'failures': failures}


def convert_gun_clips(export_dir: str, out_meshes_dir: str, names=None,
                      workers: int = None, verify: bool = False,
                      log=print) -> dict:
    """Retarget every gun clip the plugin's WEAPs name; writes the manifest.

    The third-person clips are the manifest's top level, the first-person
    set its `first_person` section. Returns the manifest ({} when the
    plugin has no guns).
    See: docs/commentary/asset_convert_falloutnv.md#first-person-rig
    """
    chars_dir = os.path.join(str(assets_for(export_dir) / 'meshes'),
                             'characters')
    bindings = weapon_bindings(export_dir)
    if not bindings:
        return {}
    manifest = {'weapons': bindings}
    for view in VIEWS:
        section = _convert_view(bindings, chars_dir, out_meshes_dir, view,
                                names, workers, verify, log)
        if section is None:
            continue
        if view[2] == 'clips':
            manifest.update(section)
        else:
            manifest[view[2]] = section
    if 'clips' not in manifest:
        return {}
    with open(os.path.join(gun_layout(out_meshes_dir)['dir'], GUN_MANIFEST),
              'w', encoding='utf-8') as f:
        json.dump(manifest, f)
    return manifest


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(
        description='Retarget FO3/FNV gun clips onto the Skyrim skeleton')
    ap.add_argument('export_dir', help='export/<plugin> record dir')
    ap.add_argument('out_meshes_dir', help='output/<plugin>/meshes')
    ap.add_argument('--names', nargs='+', help='only these clip stems')
    ap.add_argument('--workers', type=int)
    ap.add_argument('--verify', action='store_true',
                    help='read each hkx back and report rest distances')
    args = ap.parse_args()
    m = convert_gun_clips(args.export_dir, args.out_meshes_dir,
                          names=args.names, workers=args.workers,
                          verify=args.verify)
    for c in m.get('clips', []) + m.get('first_person', {}).get('clips', []):
        line = (f"{c['stem']}: {c['frames']} frames, {c['tracks']} tracks, "
                f"{c['duration']:.3f}s")
        if 'verify' in c:
            v = c['verify']
            line += (f", err trans {v['max_trans_err']:.4f} rot "
                     f"{v['max_rot_err_deg']:.3f}deg; src->dst "
                     + ', '.join(f'{k[6:]} {d:.1f}'
                                 for k, d in v['bones'].items()))
        print(line)
