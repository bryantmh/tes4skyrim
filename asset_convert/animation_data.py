"""animationdata / animationsetdata emission + singlefile merging.

The Skyrim engine loads a creature behavior project ONLY if it is registered
in the two merged text databases shipped in ``Skyrim - Animations.bsa``:

  meshes/animationdatasinglefile.txt    (clip metadata + root-motion curves)
  meshes/animationsetdatasinglefile.txt (attack-event -> clip map + preload
                                         CRC list per project)

A loose file overrides the BSA copy wholesale, so our merged output must be
``vanilla base + generated TES4 projects``. The base is pulled from the
user's Skyrim installation (loose file, or extracted from the BSA — LE v104
zlib / SSE v105 LZ4, via bsa_extract.read_bsa_files) and cached.

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
    "V3", "0", "0", <n attacks>, per attack (event name, "0", <n clips>,
    clip generator names), <n anim files>, per file 3 hash lines:
    crc(dir), crc(filename), crc("hkx").
  Hash = CRC-32 (poly 0xEDB88320, reflected) with init=0 and xorout=0 over
  the lowercase string — EXCEPT strings of <= 4 chars, which are stored as
  their ASCII bytes packed little-endian ("hkx" -> 7891816). Dir strings
  include the meshes prefix ("meshes\\actors\\deer\\animations"), verified
  against 5 vanilla projects.
"""

import os
import struct
import zlib

VANILLA_SINGLEFILES = ('animationdatasinglefile.txt',
                       'animationsetdatasinglefile.txt')


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

def _anim_file_index(manifest: dict) -> dict:
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


def project_block_lines(manifest: dict) -> list:
    """The animationdata/<project>.txt content."""
    anim_index = _anim_file_index(manifest)
    lines = ['1', str(len(manifest['project_files']))]
    lines += manifest['project_files']
    lines.append('1')
    for uid, clip in enumerate(manifest['clips']):
        # Sound triggers ALWAYS carry a descriptor name: `SoundPlay.<SNDR
        # EditorID>`. Verified by disassembling the SSE annotation handler
        # (0x140565c90 in the GOG build) — it measures the payload with
        # 0x140c60eb0 and jumps to the exit when the length is zero, so a bare
        # `SoundPlay:` does nothing at all. Only a named payload reaches the
        # by-name lookup at 0x140c260f0.
        #
        # Vanilla creature projects DO contain bare `SoundPlay:` entries (bear
        # 14, wolf 66); by the same code path those are inert leftovers, not a
        # mechanism to copy. Ours name the converted SOUN's companion
        # descriptor, minted as TES4_<EDID>_SNDR by
        # tes5_import.record_types.dialog_misc.convert_SOUN.
        timed = [(t, f'SoundPlay.TES4_{edid}_SNDR')
                 for t, edid in clip.get('sounds', []) if edid]
        # Footstep events (FootFront/FootBack) — the engine's own, routed
        # through the race's footstep/impact set, not through a descriptor.
        timed += [(t, name) for t, name in clip.get('feet', [])]
        # Raw graph events (e.g. the death pose clip's `Ragdoll` release —
        # the vanilla wolf Death block's `Ragdoll:0.267`).
        timed += [(t, name) for t, name in clip.get('events', [])]
        for t in clip.get('hits', []):
            timed.append((max(0.0, t - 0.3), 'weaponSwing'))
            timed.append((max(0.0, t - 0.1), 'preHitFrame'))
            timed.append((t, 'HitFrame'))
        triggers = [f'{name}:{_fmt(t)}' for t, name in sorted(timed)]
        if clip.get('end_event'):
            triggers.append(f"{clip['end_event']}:{_fmt(clip['duration'])}")
        lines += [clip['name'], str(anim_index[clip['anim']]),
                  '%g' % clip.get('rate', 1),
                  '0', '0', str(len(triggers))]
        lines += triggers
        lines.append('')
    return lines


def motion_block_lines(manifest: dict, trans_tol: float = 0.5,
                       rot_tol: float = 0.002) -> list:
    """The animationdata/boundanims/anims_<project>.txt content.

    One block per ANIMATION FILE INDEX (the same index space as the clip
    blocks — see _anim_file_index; vanilla stores root motion per animation,
    not per clip).  Files without root motion get a single zero row at the
    clip duration.
    """
    anim_index = _anim_file_index(manifest)
    per_file = {}               # index -> representative clip (first user)
    for clip in manifest['clips']:
        per_file.setdefault(anim_index[clip['anim']], clip)
    lines = []
    for uid in sorted(per_file):
        clip = per_file[uid]
        motion = manifest['motions'].get(clip['stem'])
        dur = clip['duration']
        t_rows, r_rows = [], []
        if motion:
            times = motion['times']
            if motion.get('translations'):
                vals = [tuple(v) for v in motion['translations']]
                for i in _rdp_keep(times, vals, trans_tol)[1:]:  # skip t=0
                    x, y, z = vals[i]
                    t_rows.append(
                        f'{_fmt(times[i])} {_fmt(x)} {_fmt(y)} {_fmt(z)}')
            if motion.get('rotations'):
                # stored w,x,y,z (kf_decode) -> emitted x,y,z,w
                vals = [tuple(v) for v in motion['rotations']]
                for i in _rdp_keep(times, vals, rot_tol)[1:]:
                    w, x, y, z = vals[i]
                    r_rows.append(f'{_fmt(times[i])} {_fmt(x)} {_fmt(y)} '
                                  f'{_fmt(z)} {_fmt(w)}')
        if not t_rows:
            t_rows = [f'{_fmt(dur)} 0 0 0']
        if not r_rows:
            r_rows = [f'{_fmt(dur)} 0 0 0 1']
        lines += [str(uid), _fmt(dur), str(len(t_rows))]
        lines += t_rows
        lines.append(str(len(r_rows)))
        lines += r_rows
        lines.append('')
    return lines


def setdata_block_lines(manifest: dict) -> list:
    """The per-project animationsetdata section (set file list + V3 block)."""
    lines = ['1', 'FullCharacter.txt', 'V3', '0', '0']
    attacks = manifest.get('attacks', [])
    lines.append(str(len(attacks)))
    for event, clip_name in attacks:
        lines += [event, '0', '1', clip_name]
    stems = sorted({c['stem'].lower() for c in manifest['clips']})
    dir_hash = str(beth_anim_hash(manifest['anim_dir']))
    ext_hash = str(beth_anim_hash('hkx'))
    lines.append(str(len(stems)))
    for stem in stems:
        lines += [dir_hash, str(beth_anim_hash(stem)), ext_hash]
    return lines


# ---------------------------------------------------------------------------
# Singlefile merging (vanilla base + generated projects)
# ---------------------------------------------------------------------------

def merge_animationdata(base_lines: list, manifests: list) -> list:
    n = int(base_lines[0])
    names = base_lines[1:1 + n]
    body = base_lines[1 + n:]
    # Never register a project the base already lists: one extra name with no
    # matching data block desyncs the whole database (see _is_merged).
    have = {x.lower() for x in names}
    new_names, new_body = [], []
    for m in manifests:
        if m['project_txt'].lower() in have:
            continue
        pb = project_block_lines(m)
        mb = motion_block_lines(m)
        new_names.append(m['project_txt'])
        new_body += [str(len(pb))] + pb + [str(len(mb))] + mb
    return ([str(n + len(new_names))] + names + new_names + body + new_body)


def merge_animationsetdata(base_lines: list, manifests: list) -> list:
    n = int(base_lines[0])
    names = base_lines[1:1 + n]
    body = base_lines[1 + n:]
    have = {x.lower() for x in names}      # see merge_animationdata
    new_names, new_body = [], []
    for m in manifests:
        stem = os.path.splitext(m['project_txt'])[0]
        entry = f'{stem}Data\\{m["project_txt"]}'
        if entry.lower() in have:
            continue
        new_names.append(entry)
        new_body += setdata_block_lines(m)
    return ([str(n + len(new_names))] + names + new_names + body + new_body)


# ---------------------------------------------------------------------------
# Block splitting -- what lets us REPLACE a generated project, not just append
# ---------------------------------------------------------------------------

def _read_count_block(lines, pos):
    """MultiLineBlock: a count line followed by that many lines.
    Returns (block_lines, next_pos)."""
    n = int(lines[pos])
    return lines[pos + 1:pos + 1 + n], pos + 1 + n


def split_animationdata(lines):
    """(names, [(project_lines, motion_lines_or_None)]).

    Same grammar tools/animcache_validate.py walks: a project name list, then
    per project a length-prefixed project block and -- only when that block's
    has-cache flag is 1 -- a length-prefixed motion block.
    """
    names, pos = _read_count_block(lines, 0)
    blocks = []
    for _ in names:
        proj, pos = _read_count_block(lines, pos)
        p = 0
        has_files = proj[p] == '1'
        p += 1
        if has_files:
            p += 1 + int(proj[p])
        motion = None
        if proj[p] == '1':                       # has-cache flag
            motion, pos = _read_count_block(lines, pos)
        blocks.append((proj, motion))
    return names, blocks


def split_animationsetdata(lines):
    """(names, [block_lines]).

    These blocks carry NO length prefix, so the only way to find where one
    ends is to parse it through.
    """
    names, pos = _read_count_block(lines, 0)
    blocks = []
    for _ in names:
        start = pos
        set_files, pos = _read_count_block(lines, pos)
        for _sf in set_files:
            pos += 1                                    # 'V3'
            _swap, pos = _read_count_block(lines, pos)  # swap events
            pos += 1 + int(lines[pos]) * 3              # HandVariableData
            nattacks = int(lines[pos])
            pos += 1
            for _ in range(nattacks):
                pos += 2                                # event, mirrored
                _clips, pos = _read_count_block(lines, pos)
            pos += 1 + int(lines[pos]) * 3              # crc triples
        blocks.append(lines[start:pos])
    return names, blocks


def is_generated_project(name: str) -> bool:
    """True for a project THIS converter emitted (`tes4<folder>project`)."""
    return os.path.basename(name.replace('\\', '/')).lower().startswith(
        _GENERATED_PROJECT_MARK)


def strip_generated_animationdata(lines: list) -> list:
    """Drop every generated project and its data blocks from a merged file.

    `merge_animationdata` skips a project whose NAME is already registered, so
    merging onto a previously-injected file would keep the OLD blocks forever
    -- a creature rebuild would never reach the game. Stripping first turns the
    merge into a true replace.
    """
    names, blocks = split_animationdata(lines)
    keep = [(n, b) for n, b in zip(names, blocks)
            if not is_generated_project(n)]
    out = [str(len(keep))] + [n for n, _ in keep]
    for _n, (proj, motion) in keep:
        out += [str(len(proj))] + proj
        if motion is not None:
            out += [str(len(motion))] + motion
    return out


def strip_generated_animationsetdata(lines: list) -> list:
    """animationsetdata counterpart of strip_generated_animationdata."""
    names, blocks = split_animationsetdata(lines)
    keep = [(n, b) for n, b in zip(names, blocks)
            if not is_generated_project(n)]
    out = [str(len(keep))] + [n for n, _ in keep]
    for _n, block in keep:
        out += block
    return out


# Every project this converter generates is named 'tes4<folder>project'. Its
# presence in a supposedly-VANILLA singlefile means the file is really one of
# our own merged outputs (deployed loose into the game folder, or cached from
# such a copy).
_GENERATED_PROJECT_MARK = 'tes4'


def _is_merged(lines: list) -> bool:
    """True when a singlefile already contains generated TES4 projects.

    Merging onto such a file registers every project a second time while
    appending only one data block, so the name list and the block list fall out
    of step — the engine then reads the WRONG block for every project past the
    first duplicate. Silent creatures were the visible symptom.
    """
    if not lines:
        return False
    try:
        n = int(lines[0])
    except (ValueError, IndexError):
        return False
    return any(_GENERATED_PROJECT_MARK in x.lower() for x in lines[1:1 + n])


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
                lines = f.read().splitlines()
            if _is_merged(lines):
                # A previously cached OUR-OUTPUT copy: drop it and re-source.
                print(f'  [animdata] cached {fn} contains generated projects '
                      f'— discarding and re-extracting a clean base')
                os.remove(cached)
                missing.append(fn)
            else:
                out[fn] = lines
        else:
            missing.append(fn)
    if not missing:
        return out

    sources = {}
    for fn in list(missing):
        loose = os.path.join(skyrim_data_path or '', 'meshes', fn)
        if skyrim_data_path and os.path.exists(loose):
            with open(loose, 'rb') as f:
                data = f.read()
            # The loose file in the game's Data folder is very likely OUR OWN
            # deployed output — merging onto it duplicates every generated
            # project and desyncs the name list from the data blocks, so the
            # engine reads the wrong block for every project after the first
            # duplicate and the whole tail of the database is garbage.
            # Only accept a loose copy that is still pristine vanilla.
            if _is_merged(data.decode('latin-1').splitlines()):
                print(f'  [animdata] loose {fn} in the game folder is already '
                      f'merged (our deployed output) — extracting from the '
                      f'BSA instead')
                continue
            sources[fn] = data
            missing.remove(fn)
    if missing:
        bsa = os.path.join(skyrim_data_path or '', 'Skyrim - Animations.bsa')
        if not (skyrim_data_path and os.path.exists(bsa)):
            raise FileNotFoundError(
                'Cannot find vanilla animation singlefiles: no loose copies '
                f'and no Skyrim - Animations.bsa under {skyrim_data_path!r}')
        from asset_convert.bsa_extract import read_bsa_files
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


def write_singlefiles(manifests: list, out_meshes_dir: str,
                      skyrim_data_path: str, cache_dir: str,
                      singlefile_dir: str = None,
                      own_manifests: list = None) -> dict:
    """Merge all generated project manifests onto the vanilla base and write
    both singlefiles (plus the per-project debug sources) under
    `out_meshes_dir`. Always merges from the VANILLA base so re-runs are
    idempotent. Returns {filename: total project count}.

    `singlefile_dir` redirects the two SHARED singlefiles (and only those)
    elsewhere — a child plugin sends them to its MASTER's meshes dir. The game
    reads exactly ONE animationdatasinglefile.txt out of Data, so a child that
    ships its own copy is not adding a file, it is racing the master for the
    same path: whichever deploys last silently de-registers the other's
    projects. Writing through to the master's single shared copy removes the
    race, and keeps the child's output to the files it genuinely owns. The
    `own_manifests` is this plugin's OWN projects; the per-project source
    files are written for those alone. It defaults to `manifests` so a
    single-plugin (master) run is unchanged."""
    base = get_vanilla_singlefiles(skyrim_data_path, cache_dir)
    os.makedirs(out_meshes_dir, exist_ok=True)

    merged_ad = merge_animationdata(
        base['animationdatasinglefile.txt'], manifests)
    merged_asd = merge_animationsetdata(
        base['animationsetdatasinglefile.txt'], manifests)
    sf_dir = singlefile_dir or out_meshes_dir
    os.makedirs(sf_dir, exist_ok=True)
    for fn, lines in (('animationdatasinglefile.txt', merged_ad),
                      ('animationsetdatasinglefile.txt', merged_asd)):
        with open(os.path.join(sf_dir, fn), 'w', encoding='latin-1',
                  newline='\r\n') as f:
            f.write('\n'.join(lines) + '\n')
        # A child must not leave a stale copy of the shared file in its own
        # tree: it would still deploy and still win the race.
        if singlefile_dir:
            stale = os.path.join(out_meshes_dir, fn)
            if os.path.exists(stale):
                os.remove(stale)

    # Per-project source files (engine ignores these; kept for debugging).
    # Written for the plugin's OWN projects only. `manifests` is the union of
    # every plugin's projects — needed to merge the shared singlefiles above,
    # but writing a per-project file for each one made a child ship the whole
    # master's set (ElsweyrAnequina: 147 files for the 7 creatures it owns).
    ad_dir = os.path.join(out_meshes_dir, 'animationdata')
    ba_dir = os.path.join(ad_dir, 'boundanims')
    os.makedirs(ba_dir, exist_ok=True)
    for m in (own_manifests if own_manifests is not None else manifests):
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

    return {'animationdatasinglefile.txt':
            int(merged_ad[0]) if merged_ad else 0,
            'animationsetdatasinglefile.txt':
            int(merged_asd[0]) if merged_asd else 0}
