"""The winding repair's render oracle hands the nearest rule the verdicts a full scan of the render mesh gives.

See: docs/commentary/asset_convert_collision.md#the-render-oracles-grid
"""

import random

import pytest

from asset_convert.nif.pyffi_monkey_patch import apply_patches

apply_patches()

from asset_convert.collision import collision_winding as W


class _FullScan:
    """An oracle that offers every face to every query, as the repair did before its grid."""

    def __init__(self, visual):
        """The full-scan oracle of render triangles `visual`."""
        self.faces = W._render_faces(visual)

    def near(self, _center, _reach):
        """Every face."""
        return self.faces


def _soup(rng, count, spread, size):
    """`count` random triangles of about `size` within a cube of `spread`, half of them near-flat."""
    out = []
    for i in range(count):
        x, y, z = (rng.uniform(0, spread) for _ in range(3))
        flat = i % 2 == 0
        out.append(tuple((x + rng.uniform(-size, size), y + rng.uniform(-size, size),
                          z + (rng.uniform(-size, size) * (0.05 if flat else 1.0))) for _ in range(3)))
    return out


@pytest.mark.parametrize('seed', range(6))
def test_the_grid_gives_the_full_scans_verdicts(seed):
    """Nearest verdicts through the grid equal the full scan's on a random collision/render pair, with a
    face whose reach spans every cell."""
    rng = random.Random(seed)
    big = ((-20.0, -20.0, 3.0), (40.0, -20.0, 3.0), (-20.0, 40.0, 3.0))
    visual = _soup(rng, 300, 6.0, 0.6) + [big]
    collision = _soup(rng, 120, 6.0, 0.8) + [(t[0], t[2], t[1]) for t in visual[:40]] + [big, big[::-1]]
    grid, full = W.RenderOracle(visual), _FullScan(visual)
    for tri in collision:
        n = W.face_normal(tri)
        assert W._nearest_says_inverted(tri, n, grid) == W._nearest_says_inverted(tri, n, full)
