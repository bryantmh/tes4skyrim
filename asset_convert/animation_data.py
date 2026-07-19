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

def project_block_lines(manifest: dict) -> list:
    """The animationdata/<project>.txt content."""
    lines = ['1', str(len(manifest['project_files']))]
    lines += manifest['project_files']
    lines.append('1')
    for uid, clip in enumerate(manifest['clips']):
        timed = [(t, 'SoundPlay') for t in clip.get('sounds', [])]
        for t in clip.get('hits', []):
            timed.append((max(0.0, t - 0.3), 'weaponSwing'))
            timed.append((max(0.0, t - 0.1), 'preHitFrame'))
            timed.append((t, 'HitFrame'))
        triggers = [f'{name}:{_fmt(t)}' for t, name in sorted(timed)]
        if clip.get('end_event'):
            triggers.append(f"{clip['end_event']}:{_fmt(clip['duration'])}")
        lines += [clip['name'], str(uid), '%g' % clip.get('rate', 1),
                  '0', '0', str(len(triggers))]
        lines += triggers
        lines.append('')
    return lines


def motion_block_lines(manifest: dict, trans_tol: float = 0.5,
                       rot_tol: float = 0.002) -> list:
    """The animationdata/boundanims/anims_<project>.txt content.

    Every clip gets a block (vanilla does the same); clips without root
    motion get a single zero row at the clip duration.
    """
    lines = []
    for uid, clip in enumerate(manifest['clips']):
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
    new_names, new_body = [], []
    for m in manifests:
        pb = project_block_lines(m)
        mb = motion_block_lines(m)
        new_names.append(m['project_txt'])
        new_body += [str(len(pb))] + pb + [str(len(mb))] + mb
    return ([str(n + len(manifests))] + names + new_names + body + new_body)


def merge_animationsetdata(base_lines: list, manifests: list) -> list:
    n = int(base_lines[0])
    names = base_lines[1:1 + n]
    body = base_lines[1 + n:]
    new_names, new_body = [], []
    for m in manifests:
        stem = os.path.splitext(m['project_txt'])[0]
        new_names.append(f'{stem}Data\\{m["project_txt"]}')
        new_body += setdata_block_lines(m)
    return ([str(n + len(manifests))] + names + new_names + body + new_body)


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
                      skyrim_data_path: str, cache_dir: str) -> dict:
    """Merge all generated project manifests onto the vanilla base and write
    both singlefiles (plus the per-project debug sources) under
    `out_meshes_dir`. Always merges from the VANILLA base so re-runs are
    idempotent. Returns {filename: total project count}."""
    base = get_vanilla_singlefiles(skyrim_data_path, cache_dir)
    os.makedirs(out_meshes_dir, exist_ok=True)

    merged_ad = merge_animationdata(
        base['animationdatasinglefile.txt'], manifests)
    merged_asd = merge_animationsetdata(
        base['animationsetdatasinglefile.txt'], manifests)
    for fn, lines in (('animationdatasinglefile.txt', merged_ad),
                      ('animationsetdatasinglefile.txt', merged_asd)):
        with open(os.path.join(out_meshes_dir, fn), 'w', encoding='latin-1',
                  newline='\r\n') as f:
            f.write('\n'.join(lines) + '\n')

    # per-project source files (engine ignores these; kept for debugging)
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

    return {'animationdatasinglefile.txt':
            int(merged_ad[0]) if merged_ad else 0,
            'animationsetdatasinglefile.txt':
            int(merged_asd[0]) if merged_asd else 0}
