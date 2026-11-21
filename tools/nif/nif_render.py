"""Render NIFs to PNG: z-buffered, textured where a texture resolves, in labeled contact sheets.

Usage:
    python -m tools.nif.nif_render a.nif [b.nif ...] --out sheet.png [--textures DIR ...]
    python -m tools.nif.nif_render --list items.tsv --out-dir DIR [--per-sheet 12]
        [--textures DIR ...] [--views 0,90,180] [--size 220] [--cols 2]

`items.tsv` holds one `label<TAB>nif path` per line; a path may be several
NIFs joined by `|`, drawn together (a hair on its head), each later file's
untextured shapes in a darker color.  Textures are matched by file stem under
the `--textures` roots, so a NIF naming `foo.bmp` finds `foo.dds`.  Views are
yaw angles in degrees; 0 looks at a Morrowind actor's face.
A NIF that fails to read shows its error in its tile instead of stopping the sheet.
"""

import argparse
import math
import os
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from asset_convert.nif.shaders import base_texture_path
from asset_convert.nif.sse_nif import NifFormat, read_nif

BACKGROUND = (24, 26, 30)
UNTEXTURED = (np.array([196.0, 196.0, 204.0]), np.array([120.0, 72.0, 40.0]),
              np.array([60.0, 90.0, 150.0]))
LIGHT = np.array([0.5, -0.6, 0.62]) / np.linalg.norm([0.5, -0.6, 0.62])

#: stem (lowercase) -> texture path, filled per process by `index_textures`.
TEXTURES = {}


def node_transform(node) -> np.ndarray:
    """The 4x4 local transform of a NiAVObject; identity when it has none."""
    m = np.eye(4)
    r = getattr(node, 'rotation', None)
    if r is None:
        return m
    m[:3, :3] = np.array([[r.m_11, r.m_21, r.m_31],
                          [r.m_12, r.m_22, r.m_32],
                          [r.m_13, r.m_23, r.m_33]]) * node.scale
    t = node.translation
    m[:3, 3] = (t.x, t.y, t.z)
    return m


def is_drawn(node) -> bool:
    """False for a hidden node, a RootCollisionNode, or a shadow shape: none shows in game."""
    if getattr(node, 'flags', 0) & 1 or isinstance(node, NifFormat.RootCollisionNode):
        return False
    return b'shadow' not in bytes(getattr(node, 'name', b'') or b'').lower()


def shape_texture(shape) -> str:
    """The base texture file name a shape names, or ''."""
    props = list(getattr(shape, 'properties', None) or [])
    props.append(getattr(shape, 'shader_property', None))
    for p in props:
        if hasattr(p, 'has_base_texture') and base_texture_path(p):
            return bytes(base_texture_path(p)).decode('latin-1')
        tset = getattr(p, 'texture_set', None)
        if tset is not None and tset.num_textures:
            return tset.textures[0].decode('latin-1')
    return ''


def _shape_arrays(data, m: np.ndarray):
    """(verts, tris, uvs or None) of one geometry data block, `m` applied."""
    verts = np.array([(p.x, p.y, p.z, 1.0) for p in data.vertices], np.float64)
    verts = (m @ verts.T).T[:, :3]
    tris = np.array(list(data.get_triangles()), np.int64).reshape(-1, 3)
    uv_sets = getattr(data, 'uv_sets', None)
    uvs = None
    if uv_sets is not None and len(uv_sets) and len(uv_sets[0]) == len(verts):
        uvs = np.array([(uv.u, uv.v) for uv in uv_sets[0]], np.float64)
    return verts, tris, uvs


def _skin_bind(shape, world: dict):
    """World transform of a skinned shape's vertices: bone world @ skin transform, or None.

    None also when the bones disagree, i.e. the file's skeleton is not in bind pose.
    """
    skin = getattr(shape, 'skin_instance', None)
    if skin is None or skin.data is None or not skin.num_bones:
        return None
    binds = [world[id(b)] @ node_transform(d.skin_transform)
             for b, d in zip(skin.bones, skin.data.bone_list) if id(b) in world]
    if not binds or max(np.abs(b - binds[0]).max() for b in binds) > 0.5:
        return None
    return binds[0]


def alpha_cut(shape) -> int:
    """Texel alpha below which the shape is cut: 128 if it alpha-tests (bit 9), 1 if it only blends, else 0."""
    props = list(getattr(shape, 'properties', None) or [])
    props.append(getattr(shape, 'alpha_property', None))
    flags = [p.flags for p in props if isinstance(p, NifFormat.NiAlphaProperty)]
    if any(f & 0x200 for f in flags):
        return 128
    return 1 if any(f & 1 for f in flags) else 0


def collect_shapes(nif_data, rgb=UNTEXTURED[0]) -> list:
    """[(verts, tris, uvs or None, texture name, untextured rgb, cut)] per drawn shape, in world space.

    `cut` is `alpha_cut`: only when nonzero does the texture's alpha cut pixels away.

    A skinned shape is placed through its first bone, not its own node chain.
    Subtrees `is_drawn` rejects are skipped.
    """
    world, geoms = {}, []

    def walk(node, xform):
        """Record `node`'s world transform and geometry, then walk its children."""
        m = xform @ node_transform(node)
        world[id(node)] = m
        d = getattr(node, 'data', None)
        if d is not None and getattr(d, 'num_vertices', 0) and hasattr(d, 'get_triangles'):
            geoms.append((node, d, m))
        for child in getattr(node, 'children', None) or []:
            if child is not None and is_drawn(child):
                walk(child, m)

    for root in nif_data.roots:
        walk(root, np.eye(4))
    shapes = []
    for node, d, m in geoms:
        bind = _skin_bind(node, world)
        verts, tris, uvs = _shape_arrays(d, m if bind is None else bind)
        if len(tris):
            shapes.append((verts, tris, uvs, shape_texture(node), rgb, alpha_cut(node)))
    return shapes


def _texture_keys(rel: str) -> tuple:
    """(path key, stem key) of a texture path: lowercase, no extension, path relative to `textures`."""
    rel = os.path.splitext(rel.replace('\\', '/').lower())[0].lstrip('/')
    rel = rel[len('textures/'):] if rel.startswith('textures/') else rel
    return rel, rel.rsplit('/', 1)[-1]


def index_textures(roots) -> None:
    """Fill TEXTURES with relative path and stem -> file for every image under `roots`."""
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if os.path.splitext(name)[1].lower() in ('.dds', '.tga', '.bmp', '.png'):
                    full = os.path.join(dirpath, name)
                    path_key, stem = _texture_keys(os.path.relpath(full, root))
                    TEXTURES.setdefault(path_key, full)
                    TEXTURES.setdefault(stem, full)


@lru_cache(maxsize=256)
def load_texture(name: str, opaque: bool = False):
    """RGBA float array of the texture `name` names (path first, else stem), alpha 255 if `opaque`; or None."""
    path_key, stem = _texture_keys(name)
    path = TEXTURES.get(path_key) or TEXTURES.get(stem)
    if not path:
        return None
    try:
        with Image.open(path) as img:
            tex = np.array(img.convert('RGBA'), np.float32)
    except (OSError, ValueError):
        return None
    if opaque:
        tex[..., 3] = 255.0
    return tex


def view_rotation(yaw_deg: float, pitch_deg: float) -> np.ndarray:
    """World -> camera rotation; camera x is screen x, z is up, y is depth."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    ry = np.array([[math.cos(yaw), -math.sin(yaw), 0],
                   [math.sin(yaw), math.cos(yaw), 0],
                   [0, 0, 1]])
    rp = np.array([[1, 0, 0],
                   [0, math.cos(pitch), -math.sin(pitch)],
                   [0, math.sin(pitch), math.cos(pitch)]])
    return rp @ ry


def face_shades(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Two-sided Lambert brightness per triangle, in [0.3, 1]."""
    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    fn = np.cross(p1 - p0, p2 - p0)
    fn /= np.maximum(np.linalg.norm(fn, axis=1), 1e-12)[:, None]
    return 0.3 + 0.7 * np.abs(fn @ LIGHT)


def _barycentric(p: np.ndarray, x0: int, y0: int, x1: int, y1: int):
    """Barycentric weights (w0, w1, w2) of the pixel centers in a box, or None."""
    (ax, ay), (bx, by), (cx, cy) = p
    den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(den) < 1e-12:
        return None
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1] + 0.5
    w0 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
    w1 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / den
    return w0, w1, 1.0 - w0 - w1


def _texels(tex: np.ndarray, uv: np.ndarray, w) -> np.ndarray:
    """RGBA samples of `tex` at the uv interpolated by weights `w` (wrapping)."""
    u = w[0] * uv[0, 0] + w[1] * uv[1, 0] + w[2] * uv[2, 0]
    v = w[0] * uv[0, 1] + w[1] * uv[1, 1] + w[2] * uv[2, 1]
    th, tw = tex.shape[:2]
    tx = (np.mod(u, 1.0) * tw).astype(np.int64).clip(0, tw - 1)
    ty = (np.mod(v, 1.0) * th).astype(np.int64).clip(0, th - 1)
    return tex[ty, tx]


def raster_triangle(color, zbuf, p, z, shade, tex=None, uv=None, rgb=UNTEXTURED[0], cut=128) -> None:
    """Draw one screen triangle into `color`/`zbuf` in `rgb`, or `tex` without texels under alpha `cut`."""
    size = zbuf.shape[0]
    x0, y0 = np.maximum(np.floor(p.min(axis=0)).astype(int), 0)
    x1, y1 = np.minimum(np.ceil(p.max(axis=0)).astype(int), size - 1)
    if x1 < x0 or y1 < y0:
        return
    w = _barycentric(p, x0, y0, x1, y1)
    if w is None:
        return
    depth = w[0] * z[0] + w[1] * z[1] + w[2] * z[2]
    sub_z = zbuf[y0:y1 + 1, x0:x1 + 1]
    hit = (w[0] >= 0) & (w[1] >= 0) & (w[2] >= 0) & (depth < sub_z)
    if not hit.any():
        return
    if tex is not None:
        texel = _texels(tex, uv, tuple(c[hit] for c in w))
        keep = texel[:, 3] >= cut
        hit[hit] = keep
        rgb = texel[keep, :3]
    sub_z[hit] = depth[hit]
    color[y0:y1 + 1, x0:x1 + 1][hit] = rgb * shade


def render(shapes, size: int = 300, yaw_deg: float = 180.0,
           pitch_deg: float = 10.0) -> Image.Image:
    """Z-buffered render of `collect_shapes` output from one orbit angle; a flat model is seen from above."""
    color = np.empty((size, size, 3), np.float32)
    color[:] = BACKGROUND
    if not shapes:
        return Image.fromarray(color.astype(np.uint8))
    span = np.ptp(np.vstack([s[0] for s in shapes]), axis=0)
    if span[2] < 0.2 * max(span[0], span[1]):
        pitch_deg = 70.0
    zbuf = np.full((size, size), np.inf)
    rot = view_rotation(yaw_deg, pitch_deg)
    cams = [s[0] @ rot.T for s in shapes]
    every = np.vstack(cams)
    ctr = (every.min(axis=0) + every.max(axis=0)) / 2
    scale = size * 0.46 / (np.abs(every - ctr).max() or 1.0)
    for (verts, tris, uvs, tex_name, rgb, cut), cam in zip(shapes, cams):
        cam = cam - ctr
        screen = np.stack([cam[:, 0] * scale + size / 2,
                           -cam[:, 2] * scale + size / 2], axis=1)
        tex = load_texture(tex_name, not cut) if uvs is not None and tex_name else None
        shades = face_shades(verts, tris)
        for tri, shade in zip(tris, shades):
            raster_triangle(color, zbuf, screen[tri], cam[tri, 1], shade,
                            tex, None if tex is None else uvs[tri], rgb, cut)
    return Image.fromarray(color.clip(0, 255).astype(np.uint8))


def load_shapes(nif_paths: str) -> list:
    """`collect_shapes` of each `|`-joined NIF, the i-th file's untextured color UNTEXTURED[i]."""
    shapes = []
    for i, path in enumerate(nif_paths.split('|')):
        shapes += collect_shapes(read_nif(path), UNTEXTURED[min(i, len(UNTEXTURED) - 1)])
    return shapes


def render_tile(label: str, nif_paths: str, size: int, yaws) -> Image.Image:
    """One item's views side by side under a large label; a failed read shows the error."""
    tile = Image.new('RGB', (size * len(yaws), size + 30), BACKGROUND)
    draw = ImageDraw.Draw(tile)
    try:
        shapes = load_shapes(nif_paths)
        for i, yaw in enumerate(yaws):
            tile.paste(render(shapes, size, yaw), (i * size, 30))
    except Exception as exc:
        draw.text((6, 40), f'{type(exc).__name__}: {exc}'[:60], fill=(255, 120, 120))
    draw.text((6, 4), label, fill=(255, 240, 160), font=ImageFont.load_default(22))
    return tile


def contact_sheet(items, size: int, yaws, cols: int) -> Image.Image:
    """A grid of `render_tile` for each (label, nif path) in `items`."""
    tiles = [render_tile(label, path, size, yaws) for label, path in items]
    tw, th = tiles[0].size
    rows = -(-len(tiles) // cols)
    sheet = Image.new('RGB', (cols * tw + (cols - 1) * 4, rows * th + (rows - 1) * 4), (0, 0, 0))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % cols) * (tw + 4), (i // cols) * (th + 4)))
    return sheet


def _sheet_job(job) -> str:
    """Pool worker: render one sheet to its path and return the path."""
    items, out, size, yaws, cols = job
    contact_sheet(items, size, yaws, cols).save(out)
    return out


def _read_list(path: str) -> list:
    """(label, nif path) pairs from a tab-separated list file."""
    with open(path, encoding='utf-8') as fh:
        rows = [line.rstrip('\n').split('\t') for line in fh if line.strip()]
    return [(r[0], r[1]) for r in rows]


def _parse_args():
    """Command-line arguments."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('nifs', nargs='*')
    ap.add_argument('--list', help='label<TAB>nif path per line')
    ap.add_argument('--out', default='temp/nif_render.png')
    ap.add_argument('--out-dir', help='sheet_NNN.png per --per-sheet items of --list')
    ap.add_argument('--per-sheet', type=int, default=12)
    ap.add_argument('--textures', nargs='*', default=[], help='roots to match texture stems under')
    ap.add_argument('--views', default='0,90,180', help='comma-separated yaw angles')
    ap.add_argument('--size', type=int, default=220)
    ap.add_argument('--cols', type=int, default=2)
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    return ap.parse_args()


def main():
    """Render the named NIFs to one sheet, or a --list to numbered sheets in --out-dir."""
    args = _parse_args()
    yaws = [float(y) for y in args.views.split(',')]
    items = _read_list(args.list) if args.list else [(Path(n).name, n) for n in args.nifs]
    if not args.out_dir:
        index_textures(args.textures)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        print(_sheet_job((items, args.out, args.size, yaws, args.cols)))
        return
    os.makedirs(args.out_dir, exist_ok=True)
    n = args.per_sheet
    jobs = [(items[i:i + n], os.path.join(args.out_dir, f'sheet_{i // n:03d}.png'),
             args.size, yaws, args.cols) for i in range(0, len(items), n)]
    with ProcessPoolExecutor(args.workers, initializer=index_textures,
                             initargs=(args.textures,)) as pool:
        for out in pool.map(_sheet_job, jobs):
            print(out, flush=True)


if __name__ == '__main__':
    main()
