r"""SpeedTree (.spt) -> Skyrim NIF converter.

Real procedural conversion: parses the SpeedTree CAD 4.x parameter file
(asset_convert.spt_parser), generates baked tree geometry from those
parameters (asset_convert.spt_generator), and writes a Skyrim NIF using the
vanilla flora structure:

    BSLeafAnimNode "<name>"  flags=14
      BSXFlags = 130 (0x82)
      bhkCollisionObject -> bhkRigidBody -> bhkCapsuleShape (trunk, wood)
      NiTriShape "<name>:Bark"     bark diffuse+normal, vertex colors
      NiTriShape "<name>:Leaves*"  composite leaf texture, alpha test,
                                   double-sided, SLSF2 Tree Anim,
                                   vertex alpha = wind weight

One NIF is generated PER TREE RECORD (named by lowercase EditorID): Oblivion
resolves the leaf composite texture from the TREE record's ICON field and
seeds the generator from the record's SNAM seed, so records sharing one .spt
(Mania/Dementia recolors) genuinely differ.  The manifest is read from
<export>/TREE.txt.  SPT files with no TREE record are converted once under
their own stem name.

Texture paths point into the tes4 namespace copied by the asset pipeline:
    textures\tes4\trees\branches\<bark>.dds (+_n)
    textures\tes4\trees\leaves\<icon>.dds

Usage (CLI):
    python -m asset_convert.spt_converter <src_dir> <dst_dir> [--workers N]
"""

import io
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from worker_budget import worker_count  # noqa: E402

_WORKER_COUNT = worker_count()

# ---------------------------------------------------------------------------
# PyFFI monkey-patch (must be before NifFormat import)
# ---------------------------------------------------------------------------
from . import pyffi_monkey_patch as _patch  # noqa: F401

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
except ImportError:
    _PYFFI = False

from .spt_parser import parse_spt, SptTree
from .spt_generator import build_tree, TreeGeometry
from .collision import _set_havok_material

NIF_FLAGS = 14
BSX_FLAGS = 130            # 0x82: complex + havok (vanilla flora value)
# Generated tree geometry is in game/render units (Size*10).  Skyrim Havok
# collision is game_units / 69.9904 (1 havok unit = 69.9904 game units) —
# verified against vanilla wrtempletree01.nif (Gildergreen): its CMS collision
# decodes to ~5.5 havok units tall for a ~389-game-unit trunk stub.  Using
# 0.1 here (the source-NIF havok scale used elsewhere) made collision ~7x too
# big because that path's source verts are already in havok units, not game.
_GAME_TO_HAVOK = 1.0 / 69.9904
_SKY_MAT_WOOD = 500811281  # SKY_HAV_MAT_WOOD

BARK_TEX_DIR = 'textures\\tes4\\trees\\branches\\'
LEAF_TEX_DIR = 'textures\\tes4\\trees\\leaves\\'


# ---------------------------------------------------------------------------
# TREE record manifest (EditorID / ICON / seed per .spt)
# ---------------------------------------------------------------------------

def load_tree_manifest(export_dir: Path) -> dict:
    """{spt_stem_lower: [(editorid, icon, seed), ...]} from TREE.txt."""
    out: dict = {}
    tf = Path(export_dir) / 'TREE.txt'
    if not tf.exists():
        return out
    cur: dict = {}
    for line in open(tf, encoding='utf-8', errors='replace'):
        line = line.strip()
        if line == '---RECORD_BEGIN---':
            cur = {}
        elif line == '---RECORD_END---':
            modl = cur.get('Model.MODL', '').replace('\\\\', '/').replace('\\', '/').strip('/')
            stem = modl.rsplit('/', 1)[-1].lower().replace('.spt', '')
            if stem:
                out.setdefault(stem, []).append(
                    (cur.get('EditorID', ''), cur.get('ICON', ''),
                     int(cur.get('Seed[0]', '0') or 0)))
        elif '=' in line:
            k, v = line.split('=', 1)
            cur[k] = v
    return out


# subdir preference for resolving a texture stem.  `billboards/` holds
# whole-tree render images (NOT leaf atlases) — a leaf stem can collide with
# a billboard of the same name (e.g. shrubrhododendronsu.dds exists in both
# leaves/ and billboards/); mapping leaf UV quads onto the billboard produces
# hard-edged garbage in-game.  Prefer leaves/branches; never billboards.
_TEX_SUBDIR_RANK = {'leaves': 0, 'branches': 1, '': 2, 'billboards': 9}


def _tex_index(tex_root: Path) -> dict:
    """{stem_lower: relative_subdir} for all DDS under the trees texture dir.

    When the same stem exists in several subdirs, the lowest-ranked subdir
    wins (leaves > branches > root >> billboards) so leaf atlases are never
    shadowed by a same-named whole-tree billboard render.
    """
    idx = {}
    if tex_root and tex_root.is_dir():
        for p in tex_root.rglob('*.dds'):
            stem = p.stem.lower()
            sub = p.relative_to(tex_root).parent.as_posix()
            rank = _TEX_SUBDIR_RANK.get(sub, 5)
            prev = idx.get(stem)
            if prev is None or rank < _TEX_SUBDIR_RANK.get(prev, 5):
                idx[stem] = sub
    return idx


# ---------------------------------------------------------------------------
# NIF building
# ---------------------------------------------------------------------------

def _fill_shape_data(tsd, verts, norms, uvs, colors, tris):
    tsd.has_vertices = True
    tsd.has_normals = True
    tsd.has_uv = True
    tsd.num_uv_sets = 1
    tsd.has_vertex_colors = True
    tsd.extra_vectors_flags = 16      # tangent space present
    tsd.num_vertices = len(verts)
    tsd.vertices.update_size()
    tsd.normals.update_size()
    tsd.tangents.update_size()
    tsd.bitangents.update_size()
    tsd.uv_sets.update_size()
    tsd.vertex_colors.update_size()
    for i in range(len(verts)):
        v = tsd.vertices[i]
        v.x, v.y, v.z = (float(verts[i, 0]), float(verts[i, 1]), float(verts[i, 2]))
        n = tsd.normals[i]
        n.x, n.y, n.z = (float(norms[i, 0]), float(norms[i, 1]), float(norms[i, 2]))
        uv = tsd.uv_sets[0][i]
        uv.u, uv.v = (float(uvs[i, 0]), float(uvs[i, 1]))
        c = tsd.vertex_colors[i]
        c.r, c.g, c.b, c.a = (float(colors[i, 0]), float(colors[i, 1]),
                              float(colors[i, 2]), float(colors[i, 3]))
    tsd.num_triangles = len(tris)
    tsd.num_triangle_points = len(tris) * 3
    tsd.has_triangles = True
    tsd.triangles.update_size()
    for i in range(len(tris)):
        t = tsd.triangles[i]
        t.v_1, t.v_2, t.v_3 = (int(tris[i, 0]), int(tris[i, 1]), int(tris[i, 2]))
    # bounding sphere
    mins = verts.min(axis=0)
    maxs = verts.max(axis=0)
    ctr = (mins + maxs) / 2.0
    tsd.center.x, tsd.center.y, tsd.center.z = (float(ctr[0]), float(ctr[1]),
                                                float(ctr[2]))
    tsd.radius = float(np.linalg.norm(verts - ctr, axis=1).max())


def _make_shader(tex0: str, tex1: str, leaves: bool):
    texset = NifFormat.BSShaderTextureSet()
    texset.num_textures = 9
    texset.textures.update_size()
    texset.textures[0] = tex0.encode()
    if tex1:
        texset.textures[1] = tex1.encode()

    sh = NifFormat.BSLightingShaderProperty()
    sh.texture_set = texset
    # PyFFI-created shader props default uv_scale to (0,0) -> invisible
    sh.uv_scale.u = 1.0
    sh.uv_scale.v = 1.0
    sh.uv_offset.u = 0.0
    sh.uv_offset.v = 0.0
    sh.glossiness = 80.0
    sh.specular_strength = 1.0
    sh.alpha = 1.0
    sh.emissive_multiple = 1.0
    sh.texture_clamp_mode = 3
    f1 = sh.shader_flags_1
    f1.slsf_1_z_buffer_test = 1
    f1.slsf_1_recieve_shadows = 1
    f1.slsf_1_cast_shadows = 1
    f1.slsf_1_specular = 0
    f2 = sh.shader_flags_2
    f2.slsf_2_z_buffer_write = 1
    f2.slsf_2_vertex_colors = 1
    if leaves:
        f1.slsf_1_vertex_alpha = 1     # vertex alpha = wind weight
        f2.slsf_2_double_sided = 1
        f2.slsf_2_tree_anim = 1
    return sh


def _make_shape(name: bytes, verts, norms, uvs, colors, tris,
                tex0: str, tex1: str = '', leaves: bool = False):
    tsd = NifFormat.NiTriShapeData()
    _fill_shape_data(tsd, verts, norms, uvs, colors, tris)

    ts = NifFormat.NiTriShape()
    ts.name = name
    ts.flags = NIF_FLAGS
    ts.data = tsd
    ts.bs_properties[0] = _make_shader(tex0, tex1, leaves)
    if leaves:
        alpha = NifFormat.NiAlphaProperty()
        alpha.flags = 0x92EC          # vanilla flora alpha-test config
        alpha.threshold = 128
        ts.bs_properties[1] = alpha
    try:
        ts.update_tangent_space(as_extra=False)
    except Exception:
        pass
    return ts


def _capsule_shape(capsule):
    """Fallback trunk capsule.  capsule = (p0, p1, radius) world units."""
    p0, p1, r = capsule
    r_h = max(float(r) * _GAME_TO_HAVOK, 0.02)
    a = np.asarray(p0, float) * _GAME_TO_HAVOK
    b = np.asarray(p1, float) * _GAME_TO_HAVOK
    # inset endpoints by the radius so hemisphere caps stay inside the trunk
    axis = b - a
    ln = float(np.linalg.norm(axis))
    if ln > 2.0 * r_h:
        axis /= ln
        a = a + axis * r_h
        b = b - axis * r_h
    cap = NifFormat.bhkCapsuleShape()
    cap.radius = r_h
    cap.radius_1 = r_h
    cap.radius_2 = r_h
    cap.first_point.x, cap.first_point.y, cap.first_point.z = map(float, a)
    cap.second_point.x, cap.second_point.y, cap.second_point.z = map(float, b)
    _set_havok_material(cap.material, _SKY_MAT_WOOD)
    return cap


def _make_collision(root, geo: TreeGeometry):
    """Exact trunk/thick-limb mesh collision.

    Builds bhkMoppBvTreeShape -> bhkCompressedMeshShape from the generated
    tube triangles through the real Havok bridge (cms_builder), so collision
    matches the rendered trunk exactly — the vanilla Gildergreen pattern
    (wrtempletree01.nif: BSLeafAnimNode root + plain identity static body +
    CMS).  Falls back to a trunk capsule if the bridge fails.
    """
    from .cms_builder import build_cms_collision

    shape = None
    tris_hu = []
    for verts, tris in zip(geo.collision_verts, geo.collision_tris):
        v = np.asarray(verts, float) * _GAME_TO_HAVOK
        for a, b, c in tris:
            tris_hu.append((tuple(v[a]), tuple(v[b]), tuple(v[c])))
    if tris_hu:
        try:
            mopp = build_cms_collision(tris_hu, _SKY_MAT_WOOD, NifFormat)
        except Exception:
            mopp = None
        if mopp is not None:
            mopp.shape.target = root
            shape = mopp
    if shape is None:
        if geo.trunk_capsule is None:
            return None
        shape = _capsule_shape(geo.trunk_capsule)

    rb = NifFormat.bhkRigidBody()
    rb.shape = shape
    rb.mass = 0.0
    rb.friction = 0.5
    rb.restitution = 0.4
    rb.linear_damping = 0.0996
    rb.angular_damping = 0.0498
    rb.max_linear_velocity = 104.4
    rb.max_angular_velocity = 31.57
    rb.motion_system = 5          # MO_SYS_BOX_STABILIZED (static)
    rb.quality_type = 0           # MO_QUAL_INVALID (static)
    rb.deactivator_type = 1
    rb.havok_col_filter.layer = 1         # SKYL_STATIC
    rb.havok_col_filter_copy.layer = 1
    rb.unknown_int_1 = 0
    rb.unknown_int_2 = 1
    rb.unknown_3_ints[0] = 0
    rb.unknown_3_ints[1] = 0
    rb.unknown_3_ints[2] = -2147483648
    rb.unknown_byte = 116
    rb.unknown_time_factor_or_gravity_factor_1 = 1.0
    rb.unknown_time_factor_or_gravity_factor_2 = 1.0
    rb.unknown_6_shorts[2] = 0
    rb.unknown_6_shorts[3] = 0

    co = NifFormat.bhkCollisionObject()
    co.flags = 129
    co.target = root
    co.body = rb
    return co


def build_tree_nif(geo: TreeGeometry, name: str,
                   bark_tex: str, bark_norm: str, leaf_tex: str) -> bytes:
    """Assemble the NIF from generated geometry.  Returns raw bytes."""
    if not _PYFFI:
        raise RuntimeError('pyffi not available')

    root = NifFormat.BSLeafAnimNode()
    root.name = name.encode()
    root.flags = NIF_FLAGS

    bsx = NifFormat.BSXFlags()
    bsx.name = b'BSX'
    bsx.integer_data = BSX_FLAGS
    root.num_extra_data_list = 1
    root.extra_data_list.update_size()
    root.extra_data_list[0] = bsx

    co = _make_collision(root, geo)
    if co is not None:
        root.collision_object = co

    shapes = [_make_shape((name + ':Bark').encode(),
                          geo.bark_verts, geo.bark_normals, geo.bark_uvs,
                          geo.bark_colors, geo.bark_tris,
                          bark_tex, bark_norm)]
    for gi, g in enumerate(geo.leaf_groups):
        tex = leaf_tex if g['texture'] == '__composite__' else \
            LEAF_TEX_DIR + Path(g['texture'].replace('\\', '/')).stem.lower() + '.dds'
        if not tex:
            continue
        shapes.append(_make_shape(
            (name + f':Leaves{gi}').encode(),
            g['verts'], g['normals'], g['uvs'], g['colors'], g['tris'],
            tex, '', leaves=True))

    root.num_children = len(shapes)
    root.children.update_size()
    for i, s in enumerate(shapes):
        root.children[i] = s

    data = NifFormat.Data()
    data.version = 0x14020007
    data.user_version = 12
    data.user_version_2 = 83
    data.header.endian_type = 1
    data.roots = [root]
    buf = io.BytesIO()
    data.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Conversion driver
# ---------------------------------------------------------------------------

def _resolve_leaf_tex(tree: SptTree, icon: str, tex_idx: dict) -> str:
    """Pick the composite leaf texture path for a tree instance."""
    for cand in (icon, tree.composite_map,
                 tree.leaf_maps[0].texture if tree.leaf_maps else ''):
        if not cand:
            continue
        stem = Path(str(cand).replace('\\', '/')).stem.lower()
        if stem in tex_idx:
            return f'textures\\tes4\\trees\\{tex_idx[stem]}\\{stem}.dds'.replace('\\\\', '\\')
    return ''


def convert_one(spt_path: Path, out_path: Path, icon: str = '',
                seed: int | None = None, tex_idx: dict | None = None,
                name: str | None = None) -> bool:
    """Convert one .spt (one TREE-record variant) to a NIF file."""
    tree = parse_spt(spt_path)
    geo = build_tree(tree, seed=seed)

    bark_stem = Path(tree.bark_texture.replace('\\', '/')).stem.lower()
    tex_idx = tex_idx or {}
    bark_tex = BARK_TEX_DIR + bark_stem + '.dds'
    bark_norm = BARK_TEX_DIR + bark_stem + '_n.dds' \
        if (bark_stem + '_n') in tex_idx else ''
    leaf_tex = _resolve_leaf_tex(tree, icon, tex_idx)

    nif = build_tree_nif(geo, name or spt_path.stem, bark_tex,
                         bark_norm, leaf_tex)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(nif)
    return True


def _convert_job(args):
    """Module-level worker for ProcessPoolExecutor (must be picklable).

    args = (spt_path_str, out_path_str, icon, seed, tex_idx, out_name).
    Returns (out_name, ok, error_message_or_None).
    """
    spt_path, out_path, icon, seed, tex_idx, out_name = args
    try:
        convert_one(Path(spt_path), Path(out_path), icon=icon, seed=seed,
                    tex_idx=tex_idx, name=out_name)
        return (out_name, True, None)
    except Exception as e:  # noqa: BLE001 — report, don't abort the batch
        return (out_name, False, f'{Path(spt_path).name}: {e}')


def convert_spt_directory(src_dir: Path, dst_dir: Path,
                          export_dir: Path | None = None,
                          workers: int | None = None) -> dict:
    """Convert all .spt files under src_dir into NIFs in dst_dir.

    One NIF per TREE record (named <editorid>.nif, seeded and textured from
    the record), plus one <sptstem>.nif for unreferenced SPT files.

    Conversion is CPU-bound (numpy geometry + PyFFI serialization + the
    Havok MOPP bridge), so it runs across a ProcessPoolExecutor — threads
    would serialize on the GIL and give no speedup.

    Args:
        src_dir:    e.g. export/Oblivion.esm/trees
        dst_dir:    e.g. output/Oblivion.esm/meshes/tes4/speedtrees
        export_dir: dir containing TREE.txt and textures/ (default: src_dir parent)
        workers:    process count (default: cpu_count - 1)
    """
    src_dir = Path(src_dir)
    dst_dir = Path(dst_dir)
    export_dir = Path(export_dir) if export_dir else src_dir.parent
    if workers is None or workers < 1:
        workers = _WORKER_COUNT

    spt_files = sorted(src_dir.rglob('*.spt'))
    if not spt_files:
        print(f'  [SPT] No .spt files found in {src_dir}')
        return {'ok': 0, 'fail': 0, 'skip': 0}

    manifest = load_tree_manifest(export_dir)
    tex_idx = _tex_index(export_dir / 'textures' / 'trees')

    # jobs for the pool: pass everything by value so workers need no globals
    jobs = []
    for p in spt_files:
        entries = manifest.get(p.stem.lower(), [])
        if entries:
            for edid, icon, seed in entries:
                if edid:
                    out_name = edid.lower()
                    jobs.append((str(p), str(dst_dir / (out_name + '.nif')),
                                 icon, seed, tex_idx, out_name))
        else:
            out_name = p.stem.lower()
            jobs.append((str(p), str(dst_dir / (out_name + '.nif')),
                         '', None, tex_idx, out_name))
    n_records = sum(1 for j in jobs if j[3] is not None)
    workers = max(1, min(workers, len(jobs)))
    print(f'  [SPT] {len(spt_files)} SPT files, {len(jobs)} tree variants '
          f'({n_records} from TREE records) with {workers} workers...')

    dst_dir.mkdir(parents=True, exist_ok=True)
    counts = {'ok': 0, 'fail': 0, 'skip': 0}

    if workers == 1:
        results = (_convert_job(j) for j in jobs)
        for _name, ok, err in results:
            counts['ok' if ok else 'fail'] += 1
            if err:
                print(f'  [SPT] ERROR {err}')
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for _name, ok, err in pool.map(_convert_job, jobs, chunksize=1):
                counts['ok' if ok else 'fail'] += 1
                if err:
                    print(f'  [SPT] ERROR {err}')

    print(f"  [SPT] Done: {counts['ok']} ok, {counts['fail']} fail")
    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Convert Oblivion SpeedTree (.spt) files to Skyrim NIFs')
    parser.add_argument('src_dir', help='Source directory with .spt files')
    parser.add_argument('dst_dir', help='Destination directory for .nif output')
    parser.add_argument('--export-dir', default=None,
                        help='Export dir with TREE.txt/textures (default: parent of src_dir)')
    parser.add_argument('--workers', type=int, default=None,
                        help=f'Parallel worker processes (default: {_WORKER_COUNT})')
    args = parser.parse_args()

    if not _PYFFI:
        print('ERROR: pyffi not installed.  Run: pip install PyFFI')
        raise SystemExit(1)

    counts = convert_spt_directory(
        Path(args.src_dir), Path(args.dst_dir),
        export_dir=Path(args.export_dir) if args.export_dir else None,
        workers=args.workers)
    raise SystemExit(0 if counts['fail'] == 0 else 1)
