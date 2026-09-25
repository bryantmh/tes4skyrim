"""animationdata / animationsetdata emission as CreatureRuntime cache fragments.

The Skyrim engine loads a creature behavior project ONLY if it is registered
in the two merged text databases shipped in ``Skyrim - Animations.bsa``:

  meshes/animationdatasinglefile.txt    (clip metadata + root-motion curves)
  meshes/animationsetdatasinglefile.txt (attack-event -> clip map + preload
                                         CRC list per project)

Each is ONE global file, so the converter never writes it: every plugin
emits one fragment (``SKSE/Plugins/CreatureRuntime/animation/<plugin>.json``)
holding its own projects' already-formatted blocks, and the CreatureRuntime plugin
composes ``vanilla base + every fragment`` in memory when the engine parses
the files. ``compose_animationdata`` / ``compose_animationsetdata`` are the
reference composition the DLL mirrors line for line.
See: docs/reference/tes_runtime_fragments.md

Grammar (verified line-exact against the LE extraction in
references/Skyrim Animations):

animationdatasinglefile.txt:
  <N projects>, then N project txt names, then per project:
    <line count>            (excludes the count line itself)
    <project block>         = content of animationdata/<name>.txt
    <line count>            (only when the has-clip-data flag — the line
                             AFTER the project file list, NOT line 1 — is "1")
    <motion block>          = content of animationdata/boundanims/anims_<name>.txt
  project block: "1", <n files>, files (behaviors/character/skeleton), "1",
    then per clip: name, uid, playbackspeed, cropstart, cropend,
    <n triggers>, "Event:time" lines, blank line.
  motion block: per clip uid: uid, duration, <n translation rows>,
    "time x y z" rows, <n rotation rows>, "time x y z w" rows, blank line.
    Rows are cumulative root displacement in game units, quats xyzw.

animationsetdatasinglefile.txt:
  <N projects>, then N "<Project>Data\\<Project>.txt" names, then per project:
    <n set files>, set file names, then per set file a V3 block:
    "V3", <n swap events>, events, <n hand variables>, per variable 3 lines,
    <n attacks>, per attack (event name, "0", <n clips>, clip generator
    names), <n anim files>, per file 3 hash lines: crc(dir), crc(filename),
    crc("hkx").
  Hash = CRC-32 (poly 0xEDB88320, reflected) with init=0 and xorout=0 over
  the lowercase string — EXCEPT strings of <= 4 chars, which are stored as
  their ASCII bytes packed little-endian ("hkx" -> 7891816). Dir strings
  include the meshes prefix ("meshes\\actors\\deer\\animations"), verified
  against 5 vanilla projects.
"""

import json
import os
import zlib

VANILLA_SINGLEFILES = ('animationdatasinglefile.txt',
                       'animationsetdatasinglefile.txt')

#: Mod-root-relative folder every fragment lives in; the DLL reads all *.json here.
FRAGMENT_DIR = os.path.join('SKSE', 'Plugins', 'CreatureRuntime', 'animation')
#: Where a build from before the runtime split put it; CreatureRuntime still reads it (deprecated).
LEGACY_FRAGMENT_DIR = os.path.join('SKSE', 'Plugins', 'TESRuntime', 'animation')
FRAGMENT_VERSION = 1


# ---------------------------------------------------------------------------
# Bethesda animationsetdata hash
# ---------------------------------------------------------------------------

def beth_anim_hash(s: str) -> int:
    """Hash used in animationsetdata CRC triples (see module docstring)."""
    b = s.lower().encode('cp1252', 'replace')
    if len(b) <= 4:
        return int.from_bytes(b, 'little')
    # crc32 with init=0/xorout=0 == zlib.crc32 conjugated on both ends
    return (zlib.crc32(b, 0xFFFFFFFF) ^ 0xFFFFFFFF) & 0xFFFFFFFF


def _fmt(v: float) -> str:
    """Vanilla-style float formatting (6 significant digits, no exponent
    for ordinary magnitudes)."""
    out = f'{float(v):.6g}'
    return out


# ---------------------------------------------------------------------------
# Root-motion curve simplification (Ramer-Douglas-Peucker)
# ---------------------------------------------------------------------------

def _rdp_keep(times, values, tol):
    """Indices to keep so linear interpolation stays within `tol` of every
    sample. `values` = list of equal-length tuples."""
    n = len(times)
    if n <= 2:
        return list(range(n))
    keep = [0, n - 1]
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ta, tb = times[a], times[b]
        span = (tb - ta) or 1.0
        worst, worst_err = -1, tol
        for i in range(a + 1, b):
            f = (times[i] - ta) / span
            err = max(abs(values[i][d] -
                          (values[a][d] + f * (values[b][d] - values[a][d])))
                      for d in range(len(values[i])))
            if err > worst_err:
                worst, worst_err = i, err
        if worst >= 0:
            keep.append(worst)
            stack.append((a, worst))
            stack.append((worst, b))
    return sorted(set(keep))


# ---------------------------------------------------------------------------
# Per-project block emitters (consume hkx_behavior project_manifest.json)
# ---------------------------------------------------------------------------

def anim_file_index(manifest: dict) -> dict:
    """anim path -> index into the character hkx's animationNames.

    THE ENGINE CONTRACT (2026-08-08 root cause of the dead creature
    ragdolls): an animationdata clip block's second line is the index into
    `hkbCharacterStringData.animationNames` — the DEDUPLICATED animation
    file list — NOT the clip's ordinal position.  The character emitter
    builds that list as `dict.fromkeys(c['anim'] for c in clip_meta)`
    (hkx_behavior.generate_creature_project); this mirrors it exactly.
    Writing `enumerate()` ordinals instead kept the first six clips (one
    file each) correct — idle and locomotion, so live creatures LOOKED
    fine — then desynced at the first shared-file clip (CombatStance):
    every attack played the next attack's file, the run gait played the
    aware-vocal clip, and every index past the file count (the parametric
    gait children and FullyRagdollPose, the death-state pose source) was
    OUT OF RANGE and never bound.  An unbindable death pose source killed
    the whole ragdoll handoff regardless of the graph: corpse teleports,
    limbs rigid, no corpse collision."""
    return {a: i for i, a in enumerate(
        dict.fromkeys(c['anim'] for c in manifest['clips']))}


def clip_block_lines(clip: dict, anim_index: int) -> list:
    """One animationdata clip block: name, file index, rate, crops, triggers.

    `anim_index` is the clip's index into the character file's
    animationNames (anim_file_index for a generated project). Sound triggers
    always name the converted SOUN's descriptor
    (`SoundPlay.TES4_<EDID>_SNDR`); footstep events are the engine's own,
    and raw graph events pass through.
    See: docs/commentary/asset_convert_creature.md#runtime-animation-cache-composition
    """
    timed = [(t, f'SoundPlay.TES4_{edid}_SNDR')
             for t, edid in clip.get('sounds', []) if edid]
    timed += [(t, name) for t, name in clip.get('feet', [])]
    timed += [(t, name) for t, name in clip.get('events', [])]
    for t in clip.get('hits', []):
        timed.append((max(0.0, t - 0.3), 'weaponSwing'))
        timed.append((max(0.0, t - 0.1), 'preHitFrame'))
        timed.append((t, 'HitFrame'))
    triggers = [f'{name}:{_fmt(t)}' for t, name in sorted(timed)]
    if clip.get('end_event'):
        triggers.append(f"{clip['end_event']}:{_fmt(clip['duration'])}")
    lines = [clip['name'], str(anim_index), '%g' % clip.get('rate', 1),
             '0', '0', str(len(triggers))]
    lines += triggers
    lines.append('')
    return lines


def project_block_lines(manifest: dict) -> list:
    """The animationdata/<project>.txt content."""
    anim_index = anim_file_index(manifest)
    lines = ['1', str(len(manifest['project_files']))]
    lines += manifest['project_files']
    lines.append('1')
    for clip in manifest['clips']:
        lines += clip_block_lines(clip, anim_index[clip['anim']])
    return lines


def motion_entry_lines(uid: int, duration: float, motion: dict,
                       trans_tol: float = 0.5, rot_tol: float = 0.002) -> list:
    """One motion block: file index, duration, translation and rotation rows.

    A file without root motion gets a single zero row at the clip duration.
    Both row sets drop the t=0 sample the engine implies; rotations are
    stored w,x,y,z by kf_decode and emitted x,y,z,w.
    """
    t_rows, r_rows = [], []
    if motion:
        times = motion['times']
        if motion.get('translations'):
            vals = [tuple(v) for v in motion['translations']]
            for i in _rdp_keep(times, vals, trans_tol)[1:]:
                x, y, z = vals[i]
                t_rows.append(
                    f'{_fmt(times[i])} {_fmt(x)} {_fmt(y)} {_fmt(z)}')
        if motion.get('rotations'):
            vals = [tuple(v) for v in motion['rotations']]
            for i in _rdp_keep(times, vals, rot_tol)[1:]:
                w, x, y, z = vals[i]
                r_rows.append(f'{_fmt(times[i])} {_fmt(x)} {_fmt(y)} '
                              f'{_fmt(z)} {_fmt(w)}')
    if not t_rows:
        t_rows = [f'{_fmt(duration)} 0 0 0']
    if not r_rows:
        r_rows = [f'{_fmt(duration)} 0 0 0 1']
    lines = [str(uid), _fmt(duration), str(len(t_rows))]
    lines += t_rows
    lines.append(str(len(r_rows)))
    lines += r_rows
    lines.append('')
    return lines


def motion_block_lines(manifest: dict, trans_tol: float = 0.5,
                       rot_tol: float = 0.002) -> list:
    """The animationdata/boundanims/anims_<project>.txt content.

    One block per ANIMATION FILE INDEX (the same index space as the clip
    blocks — see anim_file_index; vanilla stores root motion per animation,
    not per clip).
    """
    anim_index = anim_file_index(manifest)
    per_file = {}               # index -> representative clip (first user)
    for clip in manifest['clips']:
        per_file.setdefault(anim_index[clip['anim']], clip)
    lines = []
    for uid in sorted(per_file):
        clip = per_file[uid]
        lines += motion_entry_lines(
            uid, clip['duration'], manifest['motions'].get(clip['stem']),
            trans_tol, rot_tol)
    return lines


def crc_triple_lines(anim_dir: str, stems) -> list:
    """The animationsetdata preload list: count, then 3 hash lines per file."""
    dir_hash = str(beth_anim_hash(anim_dir))
    ext_hash = str(beth_anim_hash('hkx'))
    stems = sorted({s.lower() for s in stems})
    lines = [str(len(stems))]
    for stem in stems:
        lines += [dir_hash, str(beth_anim_hash(stem)), ext_hash]
    return lines


def setdata_block_lines(manifest: dict) -> list:
    """The per-project animationsetdata section (set file list + V3 block)."""
    lines = ['1', 'FullCharacter.txt', 'V3', '0', '0']
    attacks = manifest.get('attacks', [])
    lines.append(str(len(attacks)))
    for event, clip_name in attacks:
        lines += [event, '0', '1', clip_name]
    lines += crc_triple_lines(manifest['anim_dir'],
                              (c['stem'] for c in manifest['clips']))
    return lines


# ---------------------------------------------------------------------------
# Fragments: what one plugin contributes to the two global files
# ---------------------------------------------------------------------------

def manifest_fragment(manifest: dict) -> tuple:
    """A generated project's (animdata entry, animsetdata entry)."""
    stem = os.path.splitext(manifest['project_txt'])[0]
    return ({'project': manifest['project_txt'],
             'clip_block': project_block_lines(manifest),
             'motion_block': motion_block_lines(manifest)},
            {'entry': f'{stem}Data\\{manifest["project_txt"]}',
             'block': setdata_block_lines(manifest)})


def fragment_path(plugin_out_dir: str, plugin_name: str) -> str:
    """Where a plugin's fragment lives under its mod root."""
    stem = os.path.splitext(os.path.basename(plugin_name))[0]
    return os.path.join(plugin_out_dir, FRAGMENT_DIR, f'{stem}.json')


def remove_legacy_fragment(plugin_out_dir: str, plugin_name: str) -> bool:
    """Delete the plugin's pre-split fragment, if any; whether one went.

    See: docs/reference/tes_runtime_fragments.md#legacy-sidecar-paths
    """
    stem = os.path.splitext(os.path.basename(plugin_name))[0]
    path = os.path.join(plugin_out_dir, LEGACY_FRAGMENT_DIR, f'{stem}.json')
    if not os.path.isfile(path):
        return False
    os.remove(path)
    folder = os.path.dirname(path)
    if not os.listdir(folder):
        os.rmdir(folder)
    return True


def write_fragment(manifests: list, out_meshes_dir: str,
                   plugin_name: str, appends: dict = None,
                   plugin_out_dir: str = None) -> str:
    """Write the plugin's fragment from its OWN project manifests.

    Goes in `plugin_out_dir`'s SKSE/Plugins/CreatureRuntime/animation (defaulting
    to the parent of `out_meshes_dir`). Entries are ordered by project name
    so composition is deterministic; `appends` carries the entries that
    extend a VANILLA project. Returns the fragment path.
    See: docs/reference/tes_runtime_fragments.md#the-runtime-composer
    """
    manifests = sorted(manifests, key=lambda m: m['project_txt'].lower())
    animdata, animsetdata = [], []
    for m in manifests:
        ad, asd = manifest_fragment(m)
        animdata.append(ad)
        animsetdata.append(asd)
    frag = {'version': FRAGMENT_VERSION,
            'source': os.path.basename(plugin_name),
            'animdata': animdata,
            'animsetdata': animsetdata}
    frag.update(appends or {})
    root = plugin_out_dir or os.path.dirname(os.path.normpath(out_meshes_dir))
    path = fragment_path(root, plugin_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(frag, f)
    remove_legacy_fragment(root, plugin_name)
    _write_project_sources(manifests, out_meshes_dir)
    _remove_stale_singlefiles(out_meshes_dir)
    return path


def _remove_stale_singlefiles(out_meshes_dir: str) -> None:
    """Delete merged singlefiles left by the old build-time mechanism.

    One would still deploy and race the vanilla file for its path.
    """
    for fn in VANILLA_SINGLEFILES:
        stale = os.path.join(out_meshes_dir, fn)
        if os.path.exists(stale):
            os.remove(stale)


def _write_project_sources(manifests: list, out_meshes_dir: str) -> None:
    """Per-project debug copies of each block (never read by the engine)."""
    ad_dir = os.path.join(out_meshes_dir, 'animationdata')
    ba_dir = os.path.join(ad_dir, 'boundanims')
    os.makedirs(ba_dir, exist_ok=True)
    for m in manifests:
        stem = os.path.splitext(m['project_txt'])[0]
        with open(os.path.join(ad_dir, m['project_txt']), 'w',
                  encoding='latin-1', newline='\r\n') as f:
            f.write('\n'.join(project_block_lines(m)) + '\n')
        with open(os.path.join(ba_dir, f'anims_{stem}.txt'), 'w',
                  encoding='latin-1', newline='\r\n') as f:
            f.write('\n'.join(motion_block_lines(m)) + '\n')
        sd_dir = os.path.join(out_meshes_dir, 'animationsetdata',
                              f'{stem}Data')
        os.makedirs(sd_dir, exist_ok=True)
        with open(os.path.join(sd_dir, m['project_txt']), 'w',
                  encoding='latin-1', newline='\r\n') as f:
            f.write('\n'.join(setdata_block_lines(m)) + '\n')


def read_fragments(fragment_dir: str) -> list:
    """Every *.json fragment under `fragment_dir`, sorted by filename."""
    out = []
    if not os.path.isdir(fragment_dir):
        return out
    for fn in sorted(os.listdir(fragment_dir), key=str.lower):
        if not fn.lower().endswith('.json'):
            continue
        with open(os.path.join(fragment_dir, fn), encoding='utf-8') as f:
            out.append(json.load(f))
    return out


# ---------------------------------------------------------------------------
# Composition: vanilla base + fragments (the DLL's reference behaviour)
# ---------------------------------------------------------------------------

def _split_registry(base_lines: list) -> tuple:
    """(names, body) of a singlefile: the leading count + name list, rest."""
    n = int(base_lines[0])
    return list(base_lines[1:1 + n]), list(base_lines[1 + n:])


def _wrapped(block: list) -> list:
    """A block preceded by its line count, as the singlefile wraps it."""
    return [str(len(block))] + list(block)


def _project_block_spans(names: list, body: list) -> dict:
    """{project name lower: (start, end)} of each project's wrapped blocks.

    A project's span covers its clip wrapper and, when the has-clip-data
    flag is set, its motion wrapper.
    """
    spans, pos = {}, 0
    for name in names:
        start = pos
        count = int(body[pos])
        flag_at = pos + 2
        if body[pos + 1] == '1':
            flag_at += 1 + int(body[pos + 2])
        has_cache = flag_at < pos + 1 + count and body[flag_at] == '1'
        pos += 1 + count
        if has_cache:
            pos += 1 + int(body[pos])
        spans[name.lower()] = (start, pos)
    return spans


def compose_animationdata(base_lines: list, fragments: list) -> list:
    """Base singlefile + every fragment's `animdata` entries appended.

    A project the base already registers is skipped: one extra name with no
    matching data block desyncs the whole database. `animdata_append`
    entries add clip and motion blocks to an EXISTING project (the humanoid
    gun clips ride on the vanilla defaultmale project).
    """
    names, body = _split_registry(base_lines)
    have = {x.lower() for x in names}
    new_names, new_body = [], []
    for frag in fragments:
        for e in frag.get('animdata', []):
            if e['project'].lower() in have:
                continue
            have.add(e['project'].lower())
            new_names.append(e['project'])
            new_body += _wrapped(e['clip_block'])
            if e.get('motion_block') is not None:
                new_body += _wrapped(e['motion_block'])
    appends = [e for frag in fragments
               for e in frag.get('animdata_append', [])]
    if appends:
        body = _append_animdata(names, body, appends)
    return [str(len(names) + len(new_names))] + names + new_names \
        + body + new_body


def _append_animdata(names: list, body: list, appends: list) -> list:
    """Splice appended clip/motion lines into their existing project blocks."""
    spans = _project_block_spans(names, body)
    out = list(body)
    for e in sorted(appends, key=lambda a: spans.get(a['project'].lower(),
                                                     (0, 0))[0],
                    reverse=True):
        span = spans.get(e['project'].lower())
        if not span:
            continue
        start, end = span
        clip_count = int(out[start])
        clip_end = start + 1 + clip_count
        motion = list(e.get('motions') or [])
        clips = list(e.get('clips') or [])
        if clip_end < end and motion:
            m_count = int(out[clip_end])
            out[clip_end] = str(m_count + len(motion))
            out[end:end] = motion
        out[clip_end:clip_end] = clips
        out[start] = str(clip_count + len(clips))
    return out


def compose_animationsetdata(base_lines: list, fragments: list) -> list:
    """Base setdata singlefile + every fragment's `animsetdata` entries.

    `animsetdata_append` entries add one set file (name + V3 block) to an
    EXISTING project's section.
    """
    names, body = _split_registry(base_lines)
    have = {x.lower() for x in names}
    new_names, new_body = [], []
    for frag in fragments:
        for e in frag.get('animsetdata', []):
            if e['entry'].lower() in have:
                continue
            have.add(e['entry'].lower())
            new_names.append(e['entry'])
            new_body += list(e['block'])
    appends = [e for frag in fragments
               for e in frag.get('animsetdata_append', [])]
    if appends:
        body = _append_animsetdata(names, body, appends)
    return [str(len(names) + len(new_names))] + names + new_names \
        + body + new_body


def _v3_block_end(body: list, pos: int) -> int:
    """Index just past the V3 block starting at `pos` (set-file grammar).

    Walks it in order: the 'V3' tag, swap events, hand variables (3 lines
    each), then per attack an event + mirrored flag and its clip names, and
    finally the crc triples.
    """
    pos += 1
    pos += 1 + int(body[pos])
    pos += 1 + 3 * int(body[pos])
    n_attacks = int(body[pos])
    pos += 1
    for _ in range(n_attacks):
        pos += 2
        pos += 1 + int(body[pos])
    pos += 1 + 3 * int(body[pos])
    return pos


def _setdata_spans(names: list, body: list) -> dict:
    """{entry lower: (start, names_end, end)} of each project's section."""
    spans, pos = {}, 0
    for name in names:
        start = pos
        n_sets = int(body[pos])
        pos += 1 + n_sets
        names_end = pos
        for _ in range(n_sets):
            pos = _v3_block_end(body, pos)
        spans[name.lower()] = (start, names_end, pos)
    return spans


def _append_animsetdata(names: list, body: list, appends: list) -> list:
    """Splice appended set files into their existing project sections."""
    spans = _setdata_spans(names, body)
    out = list(body)
    for e in sorted(appends, key=lambda a: spans.get(a['entry'].lower(),
                                                     (0, 0, 0))[0],
                    reverse=True):
        span = spans.get(e['entry'].lower())
        if not span:
            continue
        start, names_end, end = span
        out[end:end] = list(e['block'])
        out[names_end:names_end] = [e['set_file']]
        out[start] = str(int(out[start]) + 1)
    return out


def compose_singlefiles(base: dict, fragments: list) -> dict:
    """{filename: composed lines} for both singlefiles."""
    return {'animationdatasinglefile.txt': compose_animationdata(
                base['animationdatasinglefile.txt'], fragments),
            'animationsetdatasinglefile.txt': compose_animationsetdata(
                base['animationsetdatasinglefile.txt'], fragments)}


def write_composed(lines_by_file: dict, out_dir: str) -> None:
    """Write composed singlefiles the way the DLL serves them: CRLF, latin-1."""
    os.makedirs(out_dir, exist_ok=True)
    for fn, lines in lines_by_file.items():
        with open(os.path.join(out_dir, fn), 'w', encoding='latin-1',
                  newline='\r\n') as f:
            f.write('\n'.join(lines) + '\n')


# ---------------------------------------------------------------------------
# The vanilla base (validators and tests only; the DLL reads the game's own)
# ---------------------------------------------------------------------------

def get_vanilla_singlefiles(skyrim_data_path: str, cache_dir: str) -> dict:
    """Locate the two vanilla singlefiles: cache -> loose file in the game
    Data folder -> extraction from Skyrim - Animations.bsa (LE or SSE).
    Returns {filename: list-of-lines} and populates the cache."""
    out = {}
    os.makedirs(cache_dir, exist_ok=True)
    missing = []
    for fn in VANILLA_SINGLEFILES:
        cached = os.path.join(cache_dir, fn)
        if os.path.exists(cached):
            with open(cached, encoding='latin-1') as f:
                out[fn] = f.read().splitlines()
        else:
            missing.append(fn)
    if not missing:
        return out

    sources = {}
    for fn in list(missing):
        loose = os.path.join(skyrim_data_path or '', 'meshes', fn)
        if skyrim_data_path and os.path.exists(loose):
            with open(loose, 'rb') as f:
                sources[fn] = f.read()
            missing.remove(fn)
    if missing:
        bsa = os.path.join(skyrim_data_path or '', 'Skyrim - Animations.bsa')
        if not (skyrim_data_path and os.path.exists(bsa)):
            raise FileNotFoundError(
                'Cannot find vanilla animation singlefiles: no loose copies '
                f'and no Skyrim - Animations.bsa under {skyrim_data_path!r}')
        from asset_convert.sources.bsa_extract import read_bsa_files
        got = read_bsa_files(bsa, [f'meshes\\{fn}' for fn in missing])
        for fn in missing:
            key = f'meshes\\{fn}'
            if key not in got:
                raise FileNotFoundError(f'{fn} not found inside {bsa}')
            sources[fn] = got[key]

    for fn, data in sources.items():
        with open(os.path.join(cache_dir, fn), 'wb') as f:
            f.write(data)
        out[fn] = data.decode('latin-1').splitlines()
    return out
