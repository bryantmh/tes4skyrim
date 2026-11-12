"""Audit the parallax conversion, on either side of it.

  census  — read the SOURCE meshes: how many shapes did Oblivion flag for
            parallax, which diffuse textures do they share, and how many of
            those actually carry height data.  This is the measurement that
            decides how much there is to convert; over half the flagged
            textures normally have nothing in them, which is not a defect (see
            asset_convert/texture/parallax.py).

  verify  — read the CONVERTED meshes: is every parallax shape built the way
            Skyrim wants it (shader type 3, SLSF1_Parallax, height in slot 3,
            vertex colors present) and does the height map it names exist as
            a real BC4 DDS.

  pack    — point it at ANY texture folder, including a third-party pack, and
            see what the correction would do to it: which files it refuses
            outright, which come through bit-identical, which it changes and
            why.  Runs the real correction; writes nothing.

Usage:
  python tools/audit/parallax_check.py census [plugin] [--subdir X] [--max N]
                                        [--workers N] [--all]
  python tools/audit/parallax_check.py verify [plugin] [--output-dir DIR]
                                        [--subdir X] [--max N] [--workers N]
                                        [--all]
  python tools/audit/parallax_check.py regen [plugin] [--output-dir DIR]
                                       [--strength F] [--max-range N]
                                       [--target-range N] [--blur N]
                                       [--depth F]
                                       [--only SUBSTRING] [--workers N]
  python tools/audit/parallax_check.py pack <textures dir> [--all]

`verify` only parses output meshes whose raw bytes contain '_p.dds'.  That is
a memchr over ~20,000 files instead of a pyffi parse of each, and the shader
type is only ever set together with a slot-3 path, so nothing a full walk would
find is missed.  Files that hold the string but parse to no parallax shape are
reported separately.
"""
import os
import struct
import sys
from collections import Counter
from multiprocessing import Pool, cpu_count
from pathlib import Path

sys.path.insert(0, '.')

from asset_convert.texture import parallax as px

_DXGI_BC4_UNORM = 80


# ---------------------------------------------------------------------------
# census — the source side
# ---------------------------------------------------------------------------

def scan_source(path):
    """(shape name, diffuse path, has vertex colors) for each flagged shape."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    out = []
    try:
        d = NifFormat.Data()
        with open(path, 'rb') as f:
            d.inspect(f)
            d.read(f)
    except Exception:
        return out
    for b in d.blocks:
        if not isinstance(b, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        for p in getattr(b, 'properties', []):
            if not isinstance(p, NifFormat.NiTexturingProperty):
                continue
            if int(getattr(p, 'apply_mode', 0)) != px.APPLY_HILIGHT2:
                continue
            bt = getattr(p, 'base_texture', None)
            if bt is None or bt.source is None:
                continue
            tex = bytes(bt.source.file_name).decode('latin-1', 'replace')
            vc = bool(getattr(b.data, 'has_vertex_colors', False)) \
                if b.data is not None else False
            out.append((str(path), tex, vc))
    return out


def census(plugin, subdir, mx, workers, show_all):
    from asset_convert.nif.shaders import (resolve_source_texture,
                                           rewrite_tex_path)
    meshes = Path('export') / plugin / 'meshes'
    if subdir:
        meshes = meshes / subdir
    if not meshes.is_dir():
        print(f'no such mesh tree: {meshes}')
        return 1
    paths = [str(p) for p in sorted(meshes.rglob('*.nif'))]
    if mx:
        paths = paths[:mx]
    print(f'scanning {len(paths)} source meshes under {meshes} '
          f'({workers} workers)', flush=True)

    hits = []
    with Pool(workers) as pool:
        for n, res in enumerate(pool.imap_unordered(scan_source, paths,
                                                    chunksize=8), 1):
            hits += res
            if n % 1000 == 0:
                print(f'  {n}/{len(paths)} ... {len(hits)} flagged shapes',
                      flush=True)

    if not hits:
        print('\nno shapes flagged for parallax')
        return 0

    print(f'\nflagged shapes: {len(hits)} in '
          f'{len({h[0] for h in hits})} meshes')
    print(f'  with vertex colors already: {sum(1 for h in hits if h[2])}'
          f' (the rest are given white ones on conversion)')

    # One classification per distinct texture, not per shape.  Keyed on the
    # LOWERCASED path: Oblivion meshes spell the same file several ways
    # (Nehrim's Lazeon walls appear as both `Lazeon\` and `lazeon\`), and
    # counting those separately inflates every figure downstream.  The raw
    # count is printed too, because any other measurement that keys on the
    # verbatim string will disagree with this one by exactly that difference.
    by_tex = {}
    for path, tex, _vc in hits:
        by_tex.setdefault(tex.lower(), (tex, path))
    raw_keys = len({tex for _p, tex, _vc in hits})
    print(f'  distinct diffuse textures: {len(by_tex)}'
          + (f'  ({raw_keys} distinct as spelled — {raw_keys - len(by_tex)} '
             f'are case variants of a file already counted)'
             if raw_keys != len(by_tex) else ''), flush=True)

    kinds = Counter()
    shapes_per_kind = Counter()
    per_tex = {}
    for low, (tex, any_path) in sorted(by_tex.items()):
        src = resolve_source_texture(rewrite_tex_path(tex.encode()), any_path)
        if src is None:
            info_kind, fmt = 'unresolved', '-'
        else:
            with open(src, 'rb') as f:
                info = px.classify_alpha(f.read())
            info_kind, fmt = info.kind, info.fmt
        per_tex[low] = (info_kind, fmt, tex)
        kinds[info_kind] += 1
    for _path, tex, _vc in hits:
        shapes_per_kind[per_tex[tex.lower()][0]] += 1

    # Same verdicts counted the naive way, one per spelling — only so a
    # disagreement with an older measurement can be attributed instead of
    # investigated.
    raw_per_kind = Counter(per_tex[t.lower()][0]
                           for t in {tex for _p, tex, _vc in hits})

    print('\n  texture verdicts (distinct textures / shapes they cover):')
    total = sum(kinds.values())
    for kind, cnt in kinds.most_common():
        dup = raw_per_kind[kind] - cnt
        print(f'    {kind:<11} {cnt:>5} ({cnt / total * 100:5.1f}%)'
              f'   {shapes_per_kind[kind]:>5} shapes'
              + (f'   [+{dup} case variants]' if dup else ''))
    usable = kinds.get('height', 0)
    print(f'\n  CONVERTIBLE: {usable} textures, '
          f'{shapes_per_kind["height"]} shapes')
    print('  Everything else is flagged by the author but holds no height '
          'data;\n  Oblivion renders no parallax there either, so skipping it '
          'is the faithful\n  conversion, not a gap.')

    # An unresolvable texture is the one verdict that might be OUR bug rather
    # than the author's, so name them instead of leaving a count.
    missing = sorted(tex for kind, _fmt, tex in per_tex.values()
                     if kind == 'unresolved')
    if missing:
        print(f'\n  UNRESOLVED texture paths ({len(missing)}) — check these '
              f'against the export tree\n  before accepting them as dead '
              f'references:')
        for tex in missing:
            print(f'    {tex}')

    shown = [t for t in sorted(per_tex.values()) if t[0] == 'height']
    print(f'\n  height textures ({len(shown)}):')
    for kind, fmt, tex in (shown if show_all else shown[:25]):
        print(f'    [{fmt}] {tex}')
    if not show_all and len(shown) > 25:
        print(f'    ... ({len(shown) - 25} more, pass --all)')
    return 0


# ---------------------------------------------------------------------------
# verify — the output side
# ---------------------------------------------------------------------------

def scan_output(path):
    """(shape name, problem or None, slot0, slot3) per parallax shape."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    out = []
    try:
        d = NifFormat.Data()
        with open(path, 'rb') as f:
            d.inspect(f)
            d.read(f)
    except Exception as e:
        return [(str(path), '', f'unreadable: {e}', '', '')]
    for b in d.blocks:
        if not isinstance(b, NifFormat.NiTriShape):
            continue
        shader = None
        for p in getattr(b, 'bs_properties', []):
            if isinstance(p, NifFormat.BSLightingShaderProperty):
                shader = p
                break
        if shader is None or shader.texture_set is None:
            continue
        slot3 = bytes(shader.texture_set.textures[3]).decode('latin-1')
        typed = int(shader.skyrim_shader_type) == px.SHADER_TYPE_HEIGHTMAP
        if not slot3 and not typed:
            continue
        slot0 = bytes(shader.texture_set.textures[0]).decode('latin-1')
        name = bytes(b.name).rstrip(b'\x00').decode('latin-1', 'replace')
        problems = []
        if not typed:
            problems.append(f'height in slot 3 but shader type is '
                            f'{int(shader.skyrim_shader_type)}, not 3')
        if not slot3:
            problems.append('shader type 3 with an empty slot 3')
        if not int(shader.shader_flags_1.slsf_1_parallax):
            problems.append('SLSF1_Parallax not set')
        if int(shader.shader_flags_1.slsf_1_environment_mapping):
            problems.append('environment mapping set (excludes parallax)')
        if int(shader.shader_flags_2.slsf_2_glow_map):
            problems.append('glow map set (excludes parallax)')
        if b.data is None or not b.data.has_vertex_colors:
            problems.append('no vertex colors (renders unlit-black)')
        elif not int(shader.shader_flags_2.slsf_2_vertex_colors):
            problems.append('vertex colors present but the shader flag is off')
        out.append((str(path), name, '; '.join(problems), slot0, slot3))
    return out


def _check_height_dds(p: Path):
    """None if this is a usable BC4 height map, else what is wrong with it."""
    try:
        raw = p.read_bytes()
    except OSError:
        return 'missing'
    if len(raw) < 148 or raw[:4] != b'DDS ':
        return 'not a DDS'
    if raw[84:88] != b'DX10':
        return f'not DX10 ({raw[84:88].decode("latin-1", "replace")})'
    dxgi = struct.unpack_from('<I', raw, 128)[0]
    if dxgi != _DXGI_BC4_UNORM:
        return f'DXGI {dxgi}, expected {_DXGI_BC4_UNORM} (BC4_UNORM)'
    if struct.unpack_from('<I', raw, 28)[0] < 2:
        return 'no mip chain (shimmers at distance)'
    return None


def _height_map_file(root: Path, rel: str) -> Path:
    """The file slot-3 path `rel` names under `root`, matched ignoring case."""
    from asset_convert.game_paths import find_nocase
    return find_nocase(root, rel) or root.joinpath(*rel.replace('\\', '/').split('/'))


def verify(plugin, output_dir, subdir, mx, workers, show_all):
    from output_layout import plugin_out_root
    root = plugin_out_root(output_dir, plugin, export_dir='export')
    meshes = root / 'meshes' / 'tes4'
    if subdir:
        meshes = meshes / subdir
    if not meshes.is_dir():
        print(f'no converted mesh tree at {meshes} — run --meshes-only first')
        return 1

    # Cheap prefilter: only meshes that name a height map can hold one.
    print(f'prefiltering {meshes} for meshes naming a _p.dds ...', flush=True)
    paths = []
    for p in sorted(meshes.rglob('*.nif')):
        try:
            if b'_p.dds' in p.read_bytes():
                paths.append(str(p))
        except OSError:
            continue
    if mx:
        paths = paths[:mx]
    print(f'  {len(paths)} candidate meshes, parsing with {workers} workers',
          flush=True)
    if not paths:
        print('\nno parallax shapes in the output. If you expected some, the '
              'build\nwas not run with --parallax.')
        return 0

    shapes, broken = [], []
    with Pool(workers) as pool:
        for n, res in enumerate(pool.imap_unordered(scan_output, paths,
                                                    chunksize=8), 1):
            for row in res:
                shapes.append(row)
                if row[2]:
                    broken.append(row)
            if n % 200 == 0:
                print(f'  {n}/{len(paths)} ... {len(shapes)} parallax shapes',
                      flush=True)

    print(f'\nparallax shapes: {len(shapes)} in '
          f'{len({s[0] for s in shapes})} meshes')

    # Every distinct height map named, checked once.
    named = {}
    for path, _n, _p, _s0, slot3 in shapes:
        if slot3:
            named.setdefault(slot3.lower(), slot3)
    bad_tex = {}
    for low, rel in sorted(named.items()):
        why = _check_height_dds(_height_map_file(root, rel))
        if why:
            bad_tex[rel] = why
    print(f'height maps named: {len(named)}, '
          f'{len(named) - len(bad_tex)} present and valid BC4')

    # Where to actually LOOK for the effect in game.  A height map on three
    # shapes is invisible in practice; one on four hundred is a whole dungeon
    # set, and that is the difference between a useful spot-check and a hunt.
    per_map = {}
    for path, _n, _p, _s0, slot3 in shapes:
        if not slot3:
            continue
        e = per_map.setdefault(slot3.lower(), {'rel': slot3, 'shapes': 0,
                                               'meshes': set()})
        e['shapes'] += 1
        e['meshes'].add(os.path.relpath(path, str(meshes)))
    print('\nheight maps by how much geometry uses them:')
    for e in sorted(per_map.values(), key=lambda x: -x['shapes']):
        folders = Counter(m.split(os.sep)[0] if os.sep in m else '.'
                          for m in e['meshes'])
        where = ', '.join(f'{f}' for f, _c in folders.most_common(3))
        print(f'  {e["shapes"]:>4} shapes  {len(e["meshes"]):>3} meshes  '
              f'{e["rel"]}')
        print(f'                          in: {where}   '
              f'e.g. {sorted(e["meshes"])[0]}')

    if bad_tex:
        print(f'\nBAD HEIGHT MAPS ({len(bad_tex)}):')
        for rel, why in (sorted(bad_tex.items()) if show_all
                         else sorted(bad_tex.items())[:20]):
            print(f'  {why}: {rel}')
        if not show_all and len(bad_tex) > 20:
            print(f'  ... ({len(bad_tex) - 20} more, pass --all)')

    if broken:
        print(f'\nMALFORMED SHAPES ({len(broken)}):')
        for path, name, why, _s0, _s3 in (broken if show_all else broken[:20]):
            print(f'  {why}\n    "{name}" in '
                  f'{os.path.relpath(path, str(meshes))}')
        if not show_all and len(broken) > 20:
            print(f'  ... ({len(broken) - 20} more, pass --all)')

    empty = [p for p in paths if not any(s[0] == p for s in shapes)]
    if empty:
        print(f'\nnamed a _p.dds but parsed to no parallax shape '
              f'({len(empty)}):')
        for p in (empty if show_all else empty[:10]):
            print(f'  {os.path.relpath(p, str(meshes))}')

    ok = not bad_tex and not broken
    print('\nOK — every parallax shape is well formed and its height map '
          'exists.' if ok else '\nPROBLEMS FOUND (see above).')
    return 0 if ok else 2


def _regen_one(job):
    """Rebuild one height map.  Module level and argument-only so it pickles
    into a process pool -- the maps are fully independent of one another."""
    src, dst, strength, max_range, target_range, blur, depth = job
    plane = px.decode_alpha_plane(Path(src).read_bytes())
    rng = (max(plane[2]) - min(plane[2])) if plane else 0
    written = px.build_height_map(src, dst, strength=strength,
                                  max_range=max_range,
                                  target_range=target_range,
                                  blur_per_1000=blur, depth=depth)
    return dst, written, rng


def _regen_sources(plugin, output_dir, only):
    """(the maps' folder, [(source diffuse, shipped map)], [(map, missing source)]).

    The maps are read from every top folder named `textures` in any case: the
    texture passes write the lowercase one, and a tree an older build wrote on
    a case-sensitive filesystem also holds them under `Textures`. A map's path
    below that folder is `tes4/folder/name_p.dds`.
    """
    from asset_convert.lod.lod_gen import textures_root
    from output_layout import asset_root, plugin_out_root
    root = plugin_out_root(output_dir, plugin, export_dir='export')
    tops = [d for d in root.iterdir() if d.name.lower() == 'textures'
            and d.is_dir()] if root.is_dir() else []
    src_root = asset_root('export', plugin) / 'textures'
    maps = sorted((m, top) for top in tops for m in top.rglob('*_p.dds'))
    if only:
        maps = [(m, top) for m, top in maps if only.lower() in str(m).lower()]
    found, missing = [], []
    for m, top in maps:
        rel = m.relative_to(top)
        parts = list(rel.parts)
        if parts and parts[0].lower() == 'tes4':
            parts = parts[1:]
        parts[-1] = parts[-1][:-len('_p.dds')] + '.dds'
        src = src_root.joinpath(*parts)
        if src.is_file():
            found.append((src, m))
        else:
            missing.append((rel, src))
    return textures_root(root), found, missing


def regen(plugin, output_dir, strength, max_range, target_range, only,
          blur=None, workers=None, depth=None):
    """Rewrite the shipped height maps from their source diffuse.

    Retuning the amplitude or the blur does not change a single mesh -- only
    the `_p.dds` files -- so this exists to avoid a multi-thousand-mesh rebuild
    per experiment.  Each shipped `<name>_p.dds` is regenerated from the
    `<name>.dds` in the EXPORT texture tree, which the diffuse BC1 strip never
    touches, so it stays repeatable however often the output is rebuilt.

    Parallel because it has to be: on a 4K texture pack the per-map cost is
    seconds (decode 8.5 s + encode 4.6 s on a 4096x8192 source, measured), and
    1906 maps single-threaded is hours rather than the "seconds" this was
    originally written for on Nehrim's 38.
    """
    tex_root, found, missing = _regen_sources(plugin, output_dir, only)
    if not found and not missing:
        print(f'no height maps under {tex_root}'
              + (f' matching {only!r}' if only else ''))
        return 1
    jobs = [(str(src), str(m), strength, max_range, target_range, blur, depth)
            for src, m in found]

    n_workers = workers or max(1, cpu_count() - 1)
    shown_blur = px.BLUR_RADIUS_PER_1000 if blur is None else blur
    print(f'regenerating {len(jobs)} height map(s) — strength {strength}'
          + (f', detect >{max_range}' if max_range else '')
          + (f', correct to {target_range}' if target_range else '')
          + f', blur {shown_blur}/1000'
          + f', depth x{px.DEPTH_SCALE if depth is None else depth}'
          + f', {n_workers} workers', flush=True)
    for rel, src in missing:
        print(f'  MISSING SOURCE for {rel}: {src}')

    ok = untouched = 0
    fail = len(missing)
    done = 0
    with Pool(n_workers) as pool:
        for dst, written, rng in pool.imap_unordered(_regen_one, jobs, 8):
            done += 1
            if written:
                ok += 1
                if not (bool(max_range and rng > max_range) or strength < 1.0):
                    untouched += 1
            else:
                fail += 1
                print(f'  FAILED {dst}')
            if done % 200 == 0:
                print(f'  {done}/{len(jobs)} ...', flush=True)

    print(f'\n{ok} written ({untouched} left at authored amplitude), '
          f'{fail} failed')
    print('Remember: --pack-only afterwards if you play from the BSAs.')
    return 0 if not fail else 2


_SUFFIX_MAPS = ('_n', '_g', '_hl', '_p', '_m', '_e', '_em', '_s', '_b', '_sk')


def _pack_one(path):
    """(name, verdict, before/after) for one candidate diffuse."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    info = px.classify_alpha(raw)
    if info.kind != 'height':
        return (p.name, info.kind, None)
    plane = px.decode_alpha_plane(raw)
    if plane is None:
        return (p.name, 'undecodable', None)
    a = plane[2]
    out = px.normalize_height(bytearray(a), target_range=140)
    srt = sorted(a)
    med_in, rng_in = srt[len(srt) // 2], max(a) - min(a)
    why = []
    if rng_in > px.DEFAULT_MAX_RANGE:
        why.append('amplitude')
    if med_in < px.MIN_MEDIAN:
        why.append('median')
    used = sorted(set(out))
    gap = max((used[i + 1] - used[i] for i in range(len(used) - 1)), default=0)
    return (p.name, 'height',
            (rng_in, med_in, max(out) - min(out),
             sorted(out)[len(out) // 2], gap, bytes(out) != bytes(a),
             '+'.join(why) or '-'))


def pack(folder, workers, show_all):
    """What would the conversion do to somebody ELSE's textures?

    The safety question a mod author actually has, answered by running the real
    correction rather than describing it.  Nothing is written.
    """
    root = Path(folder)
    if not root.is_dir():
        print(f'no such folder: {root}')
        return 1
    allf = sorted(root.rglob('*.dds'))
    diffuse, suffixed = [], []
    for p in allf:
        stem = p.stem.lower()
        (suffixed if any(stem.endswith(s) for s in _SUFFIX_MAPS)
         else diffuse).append(p)
    print(f'{root}\n  {len(allf)} DDS: {len(diffuse)} diffuse, '
          f'{len(suffixed)} suffixed maps (_n, _g, ...)')
    print('  Suffixed maps are never read: the height comes from the DIFFUSE '
          'named in\n  the shape\'s slot 0, so a normal map\'s alpha is out of '
          'reach by design.\n', flush=True)
    if not diffuse:
        return 0

    with Pool(workers) as pool:
        rows = [r for r in pool.map(_pack_one, [str(p) for p in diffuse], 2)
                if r]

    kinds = Counter(r[1] for r in rows)
    why = {'no_alpha': 'no alpha channel at all — cannot be touched',
           'empty': 'flat alpha — refused',
           'binary': 'cutout mask — refused',
           'bimodal': 'soft-edged mask — refused',
           'quantised': 'too few levels to be a surface — refused',
           'unreadable': 'not a readable DDS',
           'height': 'USABLE height field — enters the correction'}
    print('verdict on each diffuse alpha:')
    for k, c in kinds.most_common():
        print(f'  {k:<11} {c:>4}   {why.get(k, "")}')

    hs = [r for r in rows if r[1] == 'height']
    if not hs:
        print('\nNothing in this folder would be touched.')
        return 0

    print(f'\n{len(hs)} usable height field(s):\n')
    print(f'  {"amp":>4} {"med":>4} -> {"amp":>4} {"med":>4} {"gap":>4}  '
          f'{"detector":<10} texture')
    keep, done = [], []
    for name, _k, d in sorted(hs, key=lambda x: x[2][0]):
        (done if d[5] else keep).append(name)
        print(f'  {d[0]:>4} {d[1]:>4} -> {d[2]:>4} {d[3]:>4} {d[4]:>4}  '
              f'{d[6]:<10} {name}' + ('' if d[5] else '   << bit-identical'))

    print(f'\n  UNCHANGED, bit for bit ({len(keep)}):')
    for n in (keep if show_all else keep[:20]):
        print(f'    {n}')
    if not show_all and len(keep) > 20:
        print(f'    ... ({len(keep) - 20} more, pass --all)')
    print(f'\n  CORRECTED ({len(done)}):')
    for n in (done if show_all else done[:20]):
        print(f'    {n}')
    if not show_all and len(done) > 20:
        print(f'    ... ({len(done) - 20} more, pass --all)')

    gaps = [r[2][4] for r in hs]
    print(f'\n  detectors: amplitude > {px.DEFAULT_MAX_RANGE}, '
          f'median < {px.MIN_MEDIAN}')
    print(f'  largest output gap {max(gaps)} '
          f'(above 8 would be visible posterisation)')
    return 0


def main():
    a = sys.argv[1:]
    if not a or a[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    mode = a[0]
    if mode not in ('census', 'verify', 'regen', 'pack'):
        print(f'unknown mode {mode!r} — expected census, verify, regen or pack')
        return 1
    if mode == 'pack':
        if len(a) < 2:
            print('pack needs a folder: parallax_check.py pack <textures dir>')
            return 1
        return pack(a[1], max(1, cpu_count() - 1), '--all' in a)

    plugin, subdir, output_dir = 'Nehrim.esm', None, 'output'
    mx, workers, show_all = 0, max(1, cpu_count() - 1), '--all' in a
    strength, max_range, only = 1.0, px.DEFAULT_MAX_RANGE, None
    blur = None
    depth = None
    target_range = px.DEFAULT_TARGET_RANGE
    rest = a[1:]
    i = 0
    while i < len(rest):
        x = rest[i]
        if x == '--strength':
            strength = float(rest[i + 1]); i += 2
        elif x == '--max-range':
            max_range = int(rest[i + 1]); i += 2
        elif x == '--target-range':
            target_range = int(rest[i + 1]); i += 2
        elif x == '--only':
            only = rest[i + 1]; i += 2
        elif x == '--blur':
            blur = float(rest[i + 1]); i += 2
        elif x == '--depth':
            depth = float(rest[i + 1]); i += 2
        elif x == '--max':
            mx = int(rest[i + 1]); i += 2
        elif x == '--workers':
            workers = int(rest[i + 1]); i += 2
        elif x == '--subdir':
            subdir = rest[i + 1]; i += 2
        elif x == '--output-dir':
            output_dir = rest[i + 1]; i += 2
        elif x == '--all':
            i += 1
        else:
            plugin = x; i += 1

    if mode == 'census':
        return census(plugin, subdir, mx, workers, show_all)
    if mode == 'regen':
        return regen(plugin, output_dir, strength, max_range, target_range,
                     only, blur, workers, depth)
    return verify(plugin, output_dir, subdir, mx, workers, show_all)


if __name__ == '__main__':
    sys.exit(main())
