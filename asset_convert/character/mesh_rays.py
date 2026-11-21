"""Ray-triangle hits (Moller-Trumbore) for many rays against a triangle soup."""

import numpy as np

#: Rays tested per chunk, bounding the (rays, triangles) work arrays.
RAY_CHUNK = 128


def _chunks(origins, dirs, corners, near: float, far: float):
    """Yield (ray slice, (rays, triangles) hit distance, inf where a ray misses or hits outside (near, far))."""
    a = corners[:, 0]
    e1, e2 = corners[:, 1] - a, corners[:, 2] - a
    for i0 in range(0, len(origins), RAY_CHUNK):
        span = slice(i0, min(i0 + RAY_CHUNK, len(origins)))
        d = np.broadcast_to(dirs[span][:, None, :], (span.stop - span.start,) + e2.shape)
        pv = np.cross(d, e2[None])
        det = np.einsum('ti,pti->pt', e1, pv)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = origins[span][:, None, :] - a[None]
        u = np.einsum('pti,pti->pt', tv, pv) * inv
        qv = np.cross(tv, e1[None])
        v = np.einsum('pti,pti->pt', d, qv) * inv
        t = np.einsum('ti,pti->pt', e2, qv) * inv
        hit = ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6) & (t > near) & (t < far)
        yield span, np.where(hit, t, np.inf)


def ray_hits(origins, dirs, corners, near: float, far: float) -> np.ndarray:
    """(rays, triangles) mask of ray `origins + t * dirs` crossing triangle `corners` (n, 3, 3) at near < t < far.

    `t` is in units of each `dirs` row; both faces count.
    """
    out = np.zeros((len(origins), len(corners)), dtype=bool)
    for span, t in _chunks(origins, dirs, corners, near, far):
        out[span] = np.isfinite(t)
    return out


def ray_distance(origins, dirs, corners, near: float, far: float) -> np.ndarray:
    """Per ray the least `t` in (near, far) at which it crosses any of `corners`; inf when none."""
    out = np.full(len(origins), np.inf)
    for span, t in _chunks(origins, dirs, corners, near, far):
        if t.shape[1]:
            out[span] = t.min(axis=1)
    return out
