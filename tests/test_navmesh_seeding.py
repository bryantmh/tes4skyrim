"""seeding's playable-area filter: the vectorised even-odd test against the scalar one it replaced."""

import random

import numpy as np

from tes5_import.navmesh.seeding import inside_any


def _inside(x, y, poly):
    """The scalar even-odd test: (x, y) lies inside the polygon."""
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _polygon(rng):
    """A random polygon on a coarse grid, so horizontal edges and shared vertices occur."""
    return [(float(rng.randint(-8, 8) * 512), float(rng.randint(-8, 8) * 512))
            for _ in range(rng.randint(1, 12))]


def test_inside_any_matches_the_scalar_test():
    """Every point's answer equals any() of the scalar test over the polygons, vertex hits included."""
    rng = random.Random(3)
    for _ in range(60):
        polys = [_polygon(rng) for _ in range(rng.randint(0, 4))]
        pts = [(rng.randint(-10, 10) * 256.0, rng.randint(-10, 10) * 256.0) for _ in range(200)]
        px = np.array([p[0] for p in pts]).reshape(20, 10)
        py = np.array([p[1] for p in pts]).reshape(20, 10)
        want = [any(_inside(x, y, poly) for poly in polys) for x, y in pts]
        assert inside_any(px, py, polys).ravel().tolist() == want
