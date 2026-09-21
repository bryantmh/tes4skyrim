"""Creature conversion orchestrator: Oblivion creature folders → complete
Skyrim LE actor projects.

Per creature folder (``<export>/meshes/creatures/<name>/``), emits under
``<out_meshes>/actors/tes4/<name>/``:

  tes4<name>project.hkx / characters / behaviors / character assets/skeleton.hkx
  animations/*.hkx                      (spline-compressed, from the .kf files)
  character assets/skeleton.nif         (converted, ragdoll bhk kept on bones)
  <body part>.nif                       (converted, plain NiSkinInstance)
  project_manifest.json                 (contract for animation_data + import)

Then registers every generated project in the two merged singlefiles
(meshes/animationdatasinglefile.txt + animationsetdatasinglefile.txt — the
engine only loads projects listed there) and writes
``<export>/creature_projects.json`` for the record-side import (RACE/ARMA/
ARMO generation reads project paths, attack events and body-part lists
from it).

NPCs are NOT processed here: humanoid NPC_ records keep the Skyrim race
override system. This pipeline is for everything CREA.
"""

from asset_convert.game_paths import current_namespace
import json
import os
from asset_convert.havok.behavior_vocabulary import movement_type_names
import re
import shutil

from asset_convert.havok.creature_sounds import sound_data_by_folder
from asset_convert.havok.creature_split_morrowind import split_creatures
from concurrent.futures import ProcessPoolExecutor, as_completed


from core.worker_budget import worker_count
from output_layout import assets_for

_WORKERS = worker_count()

# Not real creatures: 'boxtest' is a Bethesda test asset; 'endgame' is the
# KFM-driven Mehrunes Dagon avatar cinematic (morph-controller NIFs PyFFI
# cannot parse — needs its own conversion path if ever wanted). The playable
# Dagon creature is the separate 'mehrunesdagon' folder, which converts.
_EXCLUDE = {'boxtest', 'endgame'}


def plugin_namespace(plugin: str) -> str:
    """The creature-path namespace for a plugin: its file stem, lowercase,
    [a-z0-9_] only ('Morrowind_ob.esm' -> 'morrowind_ob'). One per plugin,
    so two plugins' same-named creature folders never share a path — see
    hkx_behavior.project_layout."""
    stem = os.path.splitext(os.path.basename(str(plugin).rstrip('\\/')))[0]
    return re.sub(r'[^a-z0-9_]+', '_', stem.lower()).strip('_') or 'plugin'


def _namespace_for(out_meshes_dir: str) -> str:
    """output/<plugin>/meshes -> that plugin's namespace."""
    return plugin_namespace(os.path.basename(
        os.path.dirname(os.path.normpath(out_meshes_dir))))


def _widen_hkx_to_amd64(proj_dir, convert):
    """Rewrite every generated .hkx in place as 64-bit.

    SSE silently fails the behavior-graph load on a 32-bit project, leaving an
    invisible actor.  Generation and validation run 32-bit WIN32 because hkxcmd
    cannot read AMD64 back, so this is the LAST step.
    """
    for dirpath, _dirs, files in os.walk(proj_dir):
        for fn in files:
            if fn.lower().endswith('.hkx'):
                convert(os.path.join(dirpath, fn))


def _clear_stale_nifs(proj_dir):
    """Drop last run's part and merged NIFs from the project root.

    Root level only: the skeleton lives in 'character assets'.
    """
    for fn in os.listdir(proj_dir):
        if not fn.lower().endswith('.nif'):
            continue
        try:
            os.remove(os.path.join(proj_dir, fn))
        except OSError:
            pass


def _convert_creature(creature_dir: str, name: str, out_meshes_dir: str,
                      part_sets: list = None, fps: float = 30.0,
                      sound_slots: dict = None,
                      sound_chances: dict = None,
                      attr_speed: int = 0,
                      namespace: str = '') -> dict:
    """Full conversion of one creature folder. Returns its manifest
    (with added 'skeleton_nif'/'bodies' keys) or raises.

    part_sets: the distinct NIFZ part groupings that CREA records in this
    folder actually use (e.g. dog's [('dogbody.nif','doghead.nif',
    'dogeyes01.nif'), ('wolfbody.nif',...), ...]).  Each set is merged into
    ONE skinned NIF named after its body part (vanilla one-file layout).  If
    None, every .nif in the folder is treated as one set."""
    from asset_convert.havok.hkx_behavior import generate_creature_project
    from asset_convert.havok.hkx_xml import convert_hkx_to_amd64
    from asset_convert.nif.nif_converter import convert_nif
    from asset_convert.havok.creature_mesh import (
        extract_death_pile, merge_creature_body, source_attachment_node,
        source_hidden_attachment_nodes)

    manifest = generate_creature_project(creature_dir, name, out_meshes_dir,
                                         fps=fps, sound_slots=sound_slots,
                                         sound_chances=sound_chances,
                                         attr_speed=attr_speed,
                                         namespace=namespace)
    proj_dir = os.path.join(out_meshes_dir, manifest['dir'])

    _widen_hkx_to_amd64(proj_dir, convert_hkx_to_amd64)
    _clear_stale_nifs(proj_dir)

    # Convert every non-skeleton part NIF once into an ISOLATED staging dir.
    # Merges must never read a file another merge has written: creature
    # variants (goblin tribes, zombie limb combos, sheep fleeces) SHARE parts
    # across their NIFZ sets, and the old in-place layout let later merges
    # pick up earlier whole-body merge outputs as "parts" — compounding the
    # entire body into every subsequent file (mangled overlapping geometry,
    # 70x file sizes, quadratic merge times).
    # Attachment nodes the SOURCE skeleton hides at rest (ghost's
    # AttachmentsShrink is the only one in all of Oblivion) -- the
    # merge re-applies the bit to the shapes hanging off them.
    src_skel = next(
        (os.path.join(creature_dir, f)
         for f in sorted(os.listdir(creature_dir))
         if f.lower().startswith('skeleton')
         and f.lower().endswith('.nif')), None)
    hidden_nodes = (source_hidden_attachment_nodes(src_skel)
                    if src_skel else set())

    parts_dir = os.path.join(proj_dir, '_parts')
    converted = {}       # lower filename -> pristine converted part path
    attachments = {}     # converted part path -> attachment node name
    nif_failures = []

    # A dissolving creature (ghost/wraith) leaves an AUTHORED pile that
    # lives inside skeleton.nif under the node its death clip reveals.
    # Lift it into its own NIF so the import can place Oblivion's own
    # ectoplasm rather than Skyrim's DefaultAshPileGhost.
    death_pile = None
    if src_skel and manifest.get('dissolves_on_death'):
        pile_name = f'{name.lower()}deathpile.nif'
        try:
            # Extract to a staging path first: the pile comes straight out of
            # the SOURCE skeleton, so it is still Oblivion-format (uv2=11) and
            # SSE cannot load it.  convert_nif does the version upgrade,
            # shader conversion and texture fixups the same way it does for
            # every other creature mesh.
            raw_pile = os.path.join(parts_dir, pile_name)
            if extract_death_pile(
                    src_skel, raw_pile,
                    reveal_holders=manifest.get('death_reveals'),
                    holder_offsets=manifest.get('death_offsets')):
                res = convert_nif(raw_pile,
                                  os.path.join(proj_dir, pile_name),
                                  creature=True)
                if os.path.exists(os.path.join(proj_dir, pile_name)):
                    death_pile = pile_name
                else:
                    nif_failures.append(
                        (pile_name, res.get('error')
                         or f"skipped ({res.get('skip_reason')})"))
        except Exception as e:
            nif_failures.append((pile_name, f'{type(e).__name__}: {e}'))
    manifest['death_pile'] = death_pile
    for fn in sorted(os.listdir(creature_dir)):
        if not fn.lower().endswith('.nif'):
            continue
        if fn.lower().startswith('skeleton'):
            dst = os.path.join(proj_dir, 'character assets', fn.lower())
            convert_nif(os.path.join(creature_dir, fn), dst, creature=True)
            continue
        dst = os.path.join(parts_dir, fn.lower())
        # Read the part's attachment node from the SOURCE, before
        # convert_nif strips the `Prn` extra data.  The death animation
        # hides these nodes (kf_decode NiVisController -> bone scale), so
        # the merge has to hang each shape off the right one.
        try:
            attachments[dst] = source_attachment_node(
                os.path.join(creature_dir, fn))
        except Exception:
            pass
        res = convert_nif(os.path.join(creature_dir, fn), dst, creature=True)
        if res.get('error'):
            nif_failures.append((fn, res['error']))
            continue
        converted[fn.lower()] = dst

    # A single Oblivion creature folder holds several DISTINCT creatures (dog,
    # wolf, skeletal-hound) each with its own NIFZ part set.  Merge EACH set
    # into one skinned NIF (whole animal under one root), the vanilla layout —
    # the engine renders only the single BODY-slot ARMA, so separate head/eyes
    # NIFs never show.  Merged names are unique per set (first part's stem,
    # numbered on collision); the exact set→file mapping ships in the
    # manifest as body_map so the record side never has to re-derive it.
    if not part_sets:
        part_sets = [tuple(converted.keys())] if converted else []
    bodies = []          # merged NIF filenames (one per distinct part set)
    body_map = {}        # '|'.join(pset) -> merged NIF filename
    used_stems, used = set(), set()
    for pset in part_sets:
        paths = [converted[p] for p in pset if p in converted]
        if not paths:
            continue
        base_stem = os.path.splitext(os.path.basename(pset[0]))[0]
        stem, n = base_stem, 2
        while stem in used_stems:
            stem = f'{base_stem}_{n:02d}'
            n += 1
        used_stems.add(stem)
        merged_name = f'{stem}.nif'
        try:
            merge_creature_body(
                paths, os.path.join(proj_dir, merged_name),
                skeleton_path=os.path.join(proj_dir, 'character assets',
                                           'skeleton.nif'),
                attachments=attachments,
                hidden_nodes=hidden_nodes)
        except Exception as e:
            nif_failures.append((merged_name, f'{type(e).__name__}: {e}'))
            shutil.copy2(paths[0], os.path.join(proj_dir, merged_name))
        bodies.append(merged_name)
        body_map['|'.join(pset)] = merged_name
        used.update(paths)

    # Parts no set consumed stay as standalone meshes next to the merges.
    for fn_l, p in converted.items():
        if p not in used and not os.path.exists(os.path.join(proj_dir, fn_l)):
            shutil.move(p, os.path.join(proj_dir, fn_l))
    shutil.rmtree(parts_dir, ignore_errors=True)

    manifest['bodies'] = bodies
    manifest['body_map'] = body_map
    manifest['nif_failures'] = nif_failures

    # keep the on-disk manifest in sync (includes the mesh keys)
    with open(os.path.join(proj_dir, 'project_manifest.json'), 'w',
              encoding='utf-8') as f:
        json.dump(manifest, f)
    return manifest


# TES4 CREA CSDT sound type -> the clip roles that should fire it, as
# (clip-role, position) where position is 'start' | 'end' | 'foot'.
#
# Skyrim voices a creature from ANIMATION ANNOTATIONS, not from the actor
# record: a census of all 5118 vanilla Skyrim NPC_ records finds only 36 CSDT
# entries total (31 Hit, 4 Attack, 1 Left Foot) and ZERO Idle/Aware/Death,
# while 23 of the 33 wolf/bear voice SNDRs are referenced by no record at all —
# they are bound purely by name from `SoundPlay.<SNDR EditorID>` triggers in
# animationdata.  Oblivion instead put the whole voice in the CREA record and
# its .kf files carry almost no sound keys (exactly 1 of the goblin's 56, the
# bow string), so converting the records alone leaves every creature silent.
# This table moves that data across: each CSDT slot becomes a trigger on the
# clip that represents it.
CSDT_TO_CLIP = {
    0: ('locomotion', 'foot'),    # Left Foot
    1: ('locomotion', 'foot'),    # Right Foot
    2: ('locomotion', 'foot'),    # Left Back Foot
    3: ('locomotion', 'foot'),    # Right Back Foot
    6: ('attacks', 'start'),      # Attack
    8: ('death', 'start'),        # Death (only creatures WITH a death anim)
    # 4 (Idle) and 5 (Aware) must NOT be annotated onto the base clips: the
    # Idle/CombatStance clips LOOP, so an embedded SoundPlay fires every
    # cycle — the confirmed "same squeak over and over, even after death"
    # bug (the ragdoll wrapper states also play the idle clip as their pose
    # source). They become dedicated single-play vocal states instead,
    # paced by the engine's own idle system (ActionIdle / ActionIdleWarn
    # IDLE records — the vanilla WolfIdleHowl / WolfIdleWarn pattern). See
    # hkx_behavior.generate_creature_project + tes5_import/creature_idles.
    # 7 (Hit) is driven by the engine's own hit event, and is the ONE slot
    # vanilla still writes on the record (31/36) — left to the CSDT array.
}


def foot_tags(slots: dict) -> dict:
    """{footstep tag: SOUN EditorID} for a creature's CSDT foot slots (0-3).

    The tag is simultaneously the animation event the clips fire AND the
    FSTP.ANAM string the engine matches that event against — vanilla FSTPs
    carry exactly the event name (NPCWolfFootFrontWalkFootstep ANAM=FootFront).
    Both sides MUST derive it from this one function, or the fired event and
    the record tag drift apart and every footstep goes silent.

    Quadrupeds (an authored back-foot slot) collapse to the vanilla wolf pair
    FootFront/FootBack; bipeds get FootLeft/FootRight like vanilla two-legged
    creatures. Slot layout: 0=LeftFoot, 1=RightFoot, 2=LBackFoot, 3=RBackFoot.
    """
    slots = slots or {}
    front = slots.get(0) or slots.get(1)
    back = slots.get(2) or slots.get(3)
    if back:
        out = {'FootBack': back}
        if front:
            out['FootFront'] = front
        return out
    if not front:
        return {}
    return {'FootLeft': slots.get(0) or front,
            'FootRight': slots.get(1) or front}


def foot_enum_map(slots: dict) -> dict:
    """Oblivion kf 'Enum: <X>' foot text key (lowercased, spaces stripped) →
    the Skyrim footstep event to fire, consistent with foot_tags()."""
    if 'FootLeft' in foot_tags(slots):
        return {'left': 'FootLeft', 'right': 'FootRight',
                'backleft': 'FootLeft', 'backright': 'FootRight'}
    return {'left': 'FootFront', 'right': 'FootFront',
            'backleft': 'FootBack', 'backright': 'FootBack'}


def _speed_attr_by_folder(export_dir: str) -> dict:
    """folder(lower) -> MAX TES4 DATA.Speed attribute across its CREA records.

    Feeds the speed bake (hkx_behavior.generate_creature_project attr_speed):
    Oblivion moved creatures at the Speed-attribute GMST formula, not at the
    clip's root motion. MAX because the combat variants are the fast ones —
    dead/prop variants (Speed ~9-12) never move — and one behavior project
    serves the whole folder.
    """
    from tes5_import.base.text_reader import parse_export_file

    crea_path = os.path.join(export_dir, 'CREA.txt')
    if not os.path.exists(crea_path):
        return {}
    out = {}
    for rec in parse_export_file(crea_path):
        model = (rec.get('Model.MODL') or '').replace('/', '\\')
        parts = [p for p in model.lower().split('\\') if p]
        folder = parts[-2] if len(parts) >= 2 else ''
        if not folder:
            continue
        try:
            spd = int(rec.get('DATA.Speed', 0) or 0)
        except (TypeError, ValueError):
            spd = 0
        out[folder] = max(out.get(folder, 0), spd)
    return out


def _part_sets_by_folder(export_dir: str) -> dict:
    """folder(lower) -> list of distinct NIFZ part sets (each a tuple of
    lowercase .nif filenames), read from the CREA export.

    A single creature folder holds several distinct creatures (dog/wolf/
    skeletal-hound) each listing its own body parts in NIFZ.  Each distinct
    set is merged into its own whole-animal NIF, so the record side can point
    each CREA at the right merged mesh."""
    from tes5_import.base.text_reader import parse_export_file

    crea_path = os.path.join(export_dir, 'CREA.txt')
    if not os.path.exists(crea_path):
        return {}
    out = {}
    for rec in parse_export_file(crea_path):
        model = (rec.get('Model.MODL') or '').replace('/', '\\')
        parts = [p for p in model.lower().split('\\') if p]
        folder = parts[-2] if len(parts) >= 2 else ''
        if not folder:
            continue
        n = int(rec.get('NIFZCount', 0) or 0)
        pset = tuple((rec.get(f'NIFZ[{i}]') or '').lower()
                     for i in range(n))
        pset = tuple(p for p in pset if p.endswith('.nif'))
        if pset:
            out.setdefault(folder, [])
            if pset not in out[folder]:
                out[folder].append(pset)
    return out


def _crea_model_dirs(export_dir: str) -> set:
    """The mesh directories CREA records point their Model.MODL at, as
    lowercase paths relative to the meshes root (Model.MODL is already
    meshes-relative: "Creatures\\Rat\\skeleton.nif").

    Used to break ties when two folders share a leaf name (Morrowind_ob has
    both meshes\\characters\\draugr — a humanoid body-part folder — and
    meshes\\creatures\\aa_blood\\draugr, which is the one its CREA records
    actually reference). Picking by what the records use beats any
    walk-order heuristic."""
    from tes5_import.base.text_reader import parse_export_file

    crea_path = os.path.join(export_dir, 'CREA.txt')
    if not os.path.exists(crea_path):
        return set()
    out = set()
    for rec in parse_export_file(crea_path):
        model = (rec.get('Model.MODL') or '').replace('/', '\\')
        model = model.replace('\\\\', '\\').lower().lstrip('\\')
        parts = [p for p in model.split('\\') if p]
        if parts and parts[0] == 'meshes':
            parts = parts[1:]
        if len(parts) >= 2:
            out.add('\\'.join(parts[:-1]))
    return out


def _remove_unnamespaced_projects(meshes_dir: str, log=print) -> None:
    """Delete project trees from the pre-namespace layout
    (actors/tes4/<folder>/project_manifest.json directly under tes4).

    They are generated output, and leaving them beside the namespaced tree
    would re-create the very collision the namespace removes the moment the
    whole meshes folder is deployed.
    """
    root = os.path.join(meshes_dir, 'actors', current_namespace())
    if not os.path.isdir(root):
        return
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if os.path.isfile(os.path.join(p, 'project_manifest.json')):
            shutil.rmtree(p, ignore_errors=True)
            log(f'  [cleanup] removed pre-namespace project tree '
                f'actors\\{current_namespace()}\\{d}')


def manifests_under(meshes_dir: str) -> dict:
    """{project_txt: manifest} for every generated project in a plugin's
    meshes tree: actors/tes4/<namespace>/<folder>/project_manifest.json.

    Keyed on the project file name, which carries the plugin namespace, so
    two plugins' manifests can never collide (hkx_behavior.project_layout).
    """
    out = {}
    root = os.path.join(meshes_dir, 'actors', current_namespace())
    if not os.path.isdir(root):
        return out
    for ns in sorted(os.listdir(root)):
        ns_dir = os.path.join(root, ns)
        if not os.path.isdir(ns_dir):
            continue
        for d in sorted(os.listdir(ns_dir)):
            mp = os.path.join(ns_dir, d, 'project_manifest.json')
            if not os.path.exists(mp):
                continue
            with open(mp, encoding='utf-8') as f:
                m = json.load(f)
            out[m['project_txt'].lower()] = m
    return out


def convert_guns(export_dir: str, out_meshes_dir: str, workers: int = None,
                 log=print) -> dict:
    """A FO3/FNV plugin's gun clips and humanoid graph patch; the fragment
    appends that register them ({} for a plugin without guns).

    See: docs/commentary/asset_convert_falloutnv.md#gun-graph
    """
    from asset_convert import paths
    from asset_convert.havok.gun_anim_falloutnv import convert_gun_clips
    from asset_convert.havok.gun_patch_falloutnv import build_gun_graphs

    manifest = convert_gun_clips(export_dir, out_meshes_dir, workers=workers,
                                 log=log)
    if not manifest:
        return {}
    work = os.path.join(str(paths.EXPORT), 'skyrim_assets', 'humanoid_graph')
    return build_gun_graphs(manifest, out_meshes_dir, work, log)
def _creature_folders(export_dir: str, meshes_root: str, names, log) -> list:
    """(folder, leaf name, referenced by a CREA) for every animated creature folder.

    A creature is ANY folder holding a skeleton.nif plus .kf animations, at
    any depth: Morrowind_ob nests 67 such folders where Oblivion keeps them
    flat, and a depth-1 scan found 16 of them.
    See: docs/commentary/tes4_export_morrowind.md#creatures
    """
    referenced = _crea_model_dirs(export_dir)
    wanted = {n.lower() for n in names} if names else None
    candidates = []
    for cdir, _subdirs, files in os.walk(meshes_root):
        lower = {f.lower() for f in files}
        if 'skeleton.nif' not in lower:
            continue
        name = os.path.basename(cdir)
        if wanted is not None and name.lower() not in wanted:
            continue
        if name.lower() in _EXCLUDE and wanted is None:
            log(f'  [skip] {name}: excluded (test/cinematic asset)')
            continue
        if not any(f.endswith('.kf') for f in lower):
            log(f'  [skip] {name}: no animations')
            continue
        rel = os.path.relpath(cdir, meshes_root).lower().replace('/', '\\')
        candidates.append((cdir, name, rel in referenced))
    return candidates


def _pick_creature_dirs(candidates, log) -> list:
    """(folder, name) per distinct leaf name, sorted by name.

    Two folders can share a leaf name (Morrowind_ob ships both
    meshes\\characters\\draugr and meshes\\creatures\\aa_blood\\draugr) and
    would collide in the output tree and the record-side lookup; the one the
    CREA records point at wins, then the shallowest path, then alphabetical.
    """
    seen_names, dirs = {}, []
    for cdir, name, is_ref in sorted(
            candidates,
            key=lambda c: (not c[2], c[0].count(os.sep), c[0].lower())):
        key = name.lower()
        if key in seen_names:
            log(f'  [skip] {cdir}: duplicate creature name "{name}" '
                f'(using {seen_names[key]})')
            continue
        seen_names[key] = cdir
        dirs.append((cdir, name))
    dirs.sort(key=lambda d: d[1].lower())
    return dirs


def _convert_pool(dirs, export_dir, out_meshes_dir, workers, namespace,
                  log) -> tuple:
    """Convert every creature folder in a process pool; (manifests, errors) by name.

    Processes, not threads: the work is CPU-bound pure Python. Distinct NIFZ
    part sets per folder each merge into their own whole-animal NIF; CSDT
    sound slots are replayed as annotations and vocal idle states.
    """
    part_sets = _part_sets_by_folder(export_dir)
    sound_data = sound_data_by_folder(export_dir)
    sound_slots = {f: {t: e for t, (e, _c) in s.items()}
                   for f, s in sound_data.items()}
    sound_chances = {f: {t: c for t, (_e, c) in s.items()}
                     for f, s in sound_data.items()}
    speed_attrs = _speed_attr_by_folder(export_dir)
    projects, errors = {}, {}
    with ProcessPoolExecutor(max_workers=workers or _WORKERS) as pool:
        futs = {pool.submit(_convert_creature, cdir, name, out_meshes_dir,
                            part_sets.get(name.lower()), 30.0,
                            sound_slots.get(name.lower()),
                            sound_chances.get(name.lower()),
                            speed_attrs.get(name.lower(), 0),
                            namespace):
                name for cdir, name in dirs}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                manifest = fut.result()
            except Exception as e:
                errors[name] = f'{type(e).__name__}: {e}'
                log(f'  [FAIL] {name}: {errors[name]}')
                continue
            projects[name] = manifest
            n_fail = len(manifest['failures']) + len(manifest['nif_failures'])
            log(f'  [ok] {name}: {len(manifest["clips"])} clips, '
                f'{len(manifest["bodies"])} body nifs'
                + (f', {n_fail} failures' if n_fail else ''))
    return projects, errors


def _project_summary(all_manifests) -> dict:
    """The tes5_import contract per project (RACE/ARMA/ARMO, MOVT, IDLE, BPTD).

    `.get` defaults keep one manifest an interrupted run left without its
    mesh keys from killing the summary for every other creature.
    See: docs/reference/tes_runtime_fragments.md#the-runtime-composer
    """
    return {name: {
        'project_hkx': m['project_hkx'],
        'behavior_hkx': m['behavior_hkx'],
        'body_dir': m['body_dir'],
        'skeleton_nif': m['skeleton_nif'],
        'bodies': m.get('bodies', []),
        'body_map': m.get('body_map', {}),
        'attacks': m.get('attacks', []),
        'movement_types': m.get('movement_types', movement_type_names(name)),
        'speeds': m.get('speeds', {}),
        'has_ragdoll': m.get('has_ragdoll', False),
        'dissolves_on_death': m.get('dissolves_on_death', False),
        'death_duration': m.get('death_duration', 0.0),
        'death_pile': m.get('death_pile'),
        'has_cast': m.get('has_cast', False),
        'has_block': m.get('has_block', False),
        'clips': [c['name'] for c in m.get('clips', [])],
        'bones': m.get('bones', []),
        'ragdoll_bones': m.get('ragdoll_bones', []),
        'vocal_events': m.get('vocal_events', []),
    } for name, m in all_manifests.items()}


def convert_creatures(export_dir: str, out_meshes_dir: str,
                      names: list = None, workers: int = None,
                      log=print) -> dict:
    """Convert every creature folder; {'projects': {name: manifest}, 'errors': {name: str}}.

    Writes the projects, meshes, the animation cache fragment (from ALL
    projects on disk, so a subset run keeps the rest registered) and
    <export_dir>/creature_projects.json. Morrowind creatures are split first,
    which creates the tree for a plugin that ships no meshes.
    See: docs/reference/tes_runtime_fragments.md#the-runtime-composer
    """
    from asset_convert.havok.animation_data import write_fragment
    from tes5_import.base.artifact_schema import write_artifact

    meshes_root = str(assets_for(export_dir) / 'meshes')
    split_creatures(export_dir, meshes_root, log, workers)
    if not os.path.isdir(meshes_root):
        log(f'  No meshes folder at {meshes_root}')
        return {'projects': {}, 'errors': {}}
    dirs = _pick_creature_dirs(
        _creature_folders(export_dir, meshes_root, names, log), log)
    namespace = _namespace_for(out_meshes_dir)
    _remove_unnamespaced_projects(out_meshes_dir, log)
    log(f'  Converting {len(dirs)} creatures '
        f'({workers or _WORKERS} workers, namespace {namespace})...')
    projects, errors = _convert_pool(dirs, export_dir, out_meshes_dir,
                                     workers, namespace, log)

    all_manifests = dict(projects)
    for m in manifests_under(out_meshes_dir).values():
        if m.get('namespace') == namespace:
            all_manifests.setdefault(m['name'], m)
    appends = {} if names else convert_guns(export_dir, out_meshes_dir,
                                            workers, log)
    if all_manifests or appends:
        plugin_out = os.path.dirname(os.path.normpath(out_meshes_dir))
        path = write_fragment(list(all_manifests.values()), out_meshes_dir,
                              os.path.basename(plugin_out), appends, plugin_out)
        log(f'  Registered {len(all_manifests)} projects in '
            f'{os.path.relpath(path, plugin_out)}')
    write_artifact(os.path.join(export_dir, 'creature_projects.json'),
                   os.path.basename(os.path.normpath(export_dir)),
                   _project_summary(all_manifests))
    return {'projects': projects, 'errors': errors}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(
        description='Convert Oblivion creatures to Skyrim actor projects')
    ap.add_argument('export_dir', help='export/<plugin> directory')
    ap.add_argument('out_meshes_dir', help='output meshes/ directory')
    ap.add_argument('--names', nargs='+', help='only these creature folders')
    ap.add_argument('--workers', type=int)
    args = ap.parse_args()

    out = convert_creatures(args.export_dir, args.out_meshes_dir,
                            names=args.names, workers=args.workers)
    print(f"{len(out['projects'])} projects, {len(out['errors'])} errors")
    for name, err in out['errors'].items():
        print(f'  {name}: {err}')
