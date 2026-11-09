"""Drawing layers for the navmesh renderer.

Split out of render.py so each layer (collision, mesh, pathgrid, chrome) is one
small function against a shared `Canvas` rather than one long procedure.

Colour is a CONTRACT: red / orange / yellow mean a MESH DEFECT and nothing else.
Collision and the authored-navmesh underlay draw cool and dim, so a wall can
never be mistaken for a sliver.  A new layer must honour it.

See: docs/commentary/tes5_import_navmesh.md#renderer-colour-contract
"""

import math

from PIL import Image, ImageDraw

from tes5_import.navmesh import clean_validate, params

#: Mesh defect fills by severity.  These hues are reserved for defects.
DEFECT_BAD2 = (220, 40, 40, 160)
DEFECT_BAD1 = (225, 135, 40, 155)
DEFECT_TINY = (215, 215, 45, 155)
HEALTHY = (50, 160, 100, 140)

#: Collision: cool and dim, so it reads as context rather than as a defect.
COLL_BLOCK = (95, 120, 165, 34)
COLL_BLOCK_EDGE = (130, 160, 205, 70)
COLL_WALK = (105, 105, 105, 30)

PATH_EDGE = (40, 130, 255, 200)
PATH_NODE = (60, 200, 255, 255)
DOOR = (240, 60, 240, 255)
CRACK = (255, 30, 30, 255)
NOTCH = (255, 0, 255, 255)
AUTHORED = (70, 90, 130, 150)
AUTHORED_EDGE = (150, 175, 215, 200)

_BG = (16, 16, 16)
_GRID = (52, 52, 58, 255)
_GRID_TEXT = (150, 150, 155, 230)
_CHROME = (235, 235, 235, 255)

#: Candidate grid spacings in game units; the first giving <= 12 lines wins.
_GRID_STEPS = (64, 128, 256, 512, 1024, 2048, 4096)


class Canvas(object):
    """An image plus the game-units -> pixels mapping every layer draws with."""

    def __init__(self, bbox, width, margin=0):
        """Size the image from `bbox` (game units) at `width` pixels."""
        self.minx, self.miny, self.maxx, self.maxy = bbox
        self.margin = margin
        span = max(self.maxx - self.minx, 1.0)
        self.scale = (width - 2 * margin) / span
        h = int((self.maxy - self.miny) * self.scale) + 2 * margin
        self.width, self.height = width, max(1, h)
        self.img = Image.new('RGB', (self.width, self.height), _BG)
        self.dr = ImageDraw.Draw(self.img, 'RGBA')

    def P(self, x, y):
        """Game-unit (x, y) -> pixel (px, py), Y flipped so north is up."""
        return (self.margin + (x - self.minx) * self.scale,
                self.height - self.margin - (y - self.miny) * self.scale)

    def poly(self, tri, fill, outline=None):
        """Fill one triangle given as three (x, y, z) points."""
        self.dr.polygon([self.P(tri[0][0], tri[0][1]),
                         self.P(tri[1][0], tri[1][1]),
                         self.P(tri[2][0], tri[2][1])],
                        fill=fill, outline=outline)

    def save(self, out):
        """Write the PNG."""
        self.img.save(out)


def mesh_bbox(verts, pad=50.0):
    """Framing bbox around `verts`, padded by `pad` game units."""
    xs = [p[0] for p in verts]
    ys = [p[1] for p in verts]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def storey_bands(verts, tris, gap=120.0):
    """Cluster triangle centroid Z into `(zlo, zhi, count)` storey bands.

    Printed so `--z` is chosen from the cell's own data instead of guessed.
    """
    zs = sorted(sum(verts[k][2] for k in t) / 3.0 for t in tris)
    if not zs:
        return []
    bands, lo, prev, n = [], zs[0], zs[0], 0
    for z in zs:
        if z - prev > gap:
            bands.append((lo, prev, n))
            lo, n = z, 0
        prev, n = z, n + 1
    bands.append((lo, prev, n))
    return bands


def clip_collision(coll, zlo, zhi):
    """Drop collision triangles that miss the [zlo, zhi] slab entirely.

    Removes the ceiling and roof drawn over the floor plan -- 37% of blocking on
    BrumaCastleGreatHall.
    """
    def keep(ts):
        """Triangles overlapping the slab."""
        return [t for t in ts
                if min(p[2] for p in t) <= zhi and max(p[2] for p in t) >= zlo]
    return keep(coll[0]), keep(coll[1])


def draw_collision(cv, coll, alpha=None):
    """Walkable, then blocking as low-alpha fill plus a brighter rim.

    The rim carries the shape, so stacked wall triangles stay distinguishable.
    """
    for t in coll[0]:
        cv.poly(t, COLL_WALK)
    fill = COLL_BLOCK if alpha is None else COLL_BLOCK[:3] + (int(alpha),)
    for t in coll[1]:
        cv.poly(t, fill, outline=COLL_BLOCK_EDGE)


#: Shape-contract verdicts, worst first; `TRI_CLASSES[tri_class(...)]` is a fill.
TRI_CLASSES = (DEFECT_BAD2, DEFECT_BAD1, DEFECT_TINY, HEALTHY)


def tri_class(verts, tri):
    """Index into `TRI_CLASSES` for one triangle, by the shape contract.

    Separate from the fill so the web editor can ship the verdict as an int
    and colour it itself, rather than re-deriving the thresholds.
    """
    p, q, r = (verts[k] for k in tri)
    bad = clean_validate.badness(verts, tri)
    area = abs((q[0] - p[0]) * (r[1] - p[1])
               - (q[1] - p[1]) * (r[0] - p[0])) * 0.5
    if bad > 2.0:
        return 0
    if bad > 1.0:
        return 1
    return 2 if area < params.MIN_TRI_AREA else 3


def _tri_fill(verts, tri):
    """Defect colour for one triangle, by the shape contract."""
    return TRI_CLASSES[tri_class(verts, tri)]


def _shade(fill, f):
    """Darken a fill toward 30% for f=0, keeping its hue and alpha."""
    k = 0.30 + 0.90 * f
    return tuple(min(255, int(c * k)) for c in fill[:3]) + (fill[3],)


def draw_mesh(cv, verts, tris, ids=False, z_shade=False):
    """The generated mesh: fill = shape contract, lightness = height."""
    zc = [sum(verts[k][2] for k in t) / 3.0 for t in tris]
    zlo, zhi = (min(zc), max(zc)) if zc else (0.0, 1.0)
    zspan = max(zhi - zlo, 1.0)
    for ti, tri in enumerate(tris):
        fill = _tri_fill(verts, tri)
        if z_shade:
            fill = _shade(fill, (zc[ti] - zlo) / zspan)
        pts = [cv.P(verts[k][0], verts[k][1]) for k in tri]
        cv.dr.polygon(pts, fill=fill, outline=(230, 230, 230, 255))
        if ids:
            cx = sum(p[0] for p in pts) / 3.0
            cy = sum(p[1] for p in pts) / 3.0
            cv.dr.text((cx - 6, cy - 5), str(ti), fill=(255, 255, 255, 220))


def draw_authored(cv, averts, atris):
    """An authored navmesh as a filled underlay, in the cool palette."""
    for tri in atris:
        pts = [cv.P(averts[k][0], averts[k][1]) for k in tri]
        cv.dr.polygon(pts, fill=AUTHORED, outline=AUTHORED_EDGE)


def draw_pathgrid(cv, nodes, edges, doors, alpha=200, ids=False):
    """The authored pathgrid: edges, nodes, optional indices, door squares."""
    ecol = PATH_EDGE[:3] + (int(alpha),)
    for (a, b) in edges:
        pa, pb = nodes[a], nodes[b]
        cv.dr.line([cv.P(pa[0], pa[1]), cv.P(pb[0], pb[1])], fill=ecol, width=2)
    for i, n in enumerate(nodes):
        x, y = cv.P(n[0], n[1])
        cv.dr.ellipse([x - 3, y - 3, x + 3, y + 3], fill=PATH_NODE)
        if ids:
            cv.dr.text((x + 5, y - 5), str(i), fill=PATH_NODE)
    for (x, y, _z, _r, _f, _tp, _w) in doors:
        px, py = cv.P(x, y)
        cv.dr.rectangle([px - 4, py - 4, px + 4, py + 4], fill=DOOR)


def draw_cracks(cv, verts, cracks):
    """Boundary edges a walked pathgrid line crosses."""
    for (a, b) in cracks:
        cv.dr.line([cv.P(verts[a][0], verts[a][1]),
                    cv.P(verts[b][0], verts[b][1])], fill=CRACK, width=5)


def draw_notches(cv, verts, notches):
    """Ring the mouth of every V-bite: invisible to the coverage metrics."""
    for (apex, p, q, _d, _m) in notches:
        cv.dr.line([cv.P(verts[p][0], verts[p][1]),
                    cv.P(verts[apex][0], verts[apex][1]),
                    cv.P(verts[q][0], verts[q][1])], fill=NOTCH, width=4)
        x, y = cv.P(verts[apex][0], verts[apex][1])
        cv.dr.ellipse([x - 7, y - 7, x + 7, y + 7], outline=NOTCH, width=3)


def _grid_step(span):
    """Coarsest grid spacing giving at most 12 lines across `span`."""
    for s in _GRID_STEPS:
        if span / s <= 12:
            return s
    return _GRID_STEPS[-1]


def draw_grid(cv):
    """Labelled X/Y ticks in game units; returns the spacing used.

    Lets a render say which coordinate to move a node to, not just that it is
    too far left.
    """
    step = _grid_step(max(cv.maxx - cv.minx, cv.maxy - cv.miny))
    x = math.ceil(cv.minx / step) * step
    while x <= cv.maxx:
        px = cv.P(x, cv.miny)[0]
        cv.dr.line([(px, 0), (px, cv.height)], fill=_GRID)
        cv.dr.text((px + 3, 3), '%d' % x, fill=_GRID_TEXT)
        x += step
    y = math.ceil(cv.miny / step) * step
    while y <= cv.maxy:
        py = cv.P(cv.minx, y)[1]
        cv.dr.line([(0, py), (cv.width, py)], fill=_GRID)
        cv.dr.text((3, py + 2), '%d' % y, fill=_GRID_TEXT)
        y += step
    return step


def draw_scalebar(cv, step):
    """A bottom-right bar exactly one grid step long, labelled in units."""
    px = int(step * cv.scale)
    x1, y1 = cv.width - 20, cv.height - 20
    x0 = x1 - px
    cv.dr.line([(x0, y1), (x1, y1)], fill=_CHROME, width=3)
    cv.dr.line([(x0, y1 - 5), (x0, y1 + 5)], fill=_CHROME, width=3)
    cv.dr.line([(x1, y1 - 5), (x1, y1 + 5)], fill=_CHROME, width=3)
    cv.dr.text((x0, y1 - 18), '%d units' % step, fill=_CHROME)


def draw_legend(cv, entries):
    """Swatch + label per `(label, colour)`, so a render is self-describing.

    Sits below the top tick row, which carries the X coordinate labels.
    """
    x, y = 8, 20
    cv.dr.rectangle([0, y - 4, 150, y + 16 * len(entries)],
                    fill=(16, 16, 16, 190))
    for label, col in entries:
        cv.dr.rectangle([x, y, x + 12, y + 12], fill=col,
                        outline=(200, 200, 200, 200))
        cv.dr.text((x + 18, y + 1), label, fill=_CHROME)
        y += 16


def legend_for(collision, z_shade, authored, cracks, notches, mesh=True,
               pathgrid=True):
    """Legend entries for the layers actually drawn.

    `mesh` is false on an authored-only render, where naming defect colours
    nothing on screen uses would misread as "the answer key is clean".
    """
    out = []
    if mesh:
        out += [('mesh ok', HEALTHY), ('badness>2', DEFECT_BAD2),
                ('badness>1', DEFECT_BAD1),
                ('area<%d' % params.MIN_TRI_AREA, DEFECT_TINY)]
    if z_shade:
        out.append(('darker = lower Z', _shade(HEALTHY, 0.0)))
    if collision:
        out += [('blocking (wall)', COLL_BLOCK_EDGE), ('walkable', COLL_WALK)]
    if authored:
        out.append(('authored navmesh', AUTHORED_EDGE))
    if pathgrid:
        out += [('pathgrid', PATH_EDGE), ('door', DOOR)]
    if cracks:
        out.append(('crack', CRACK))
    if notches:
        out.append(('notch', NOTCH))
    return out
