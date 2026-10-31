// Polygon overlap AREA by clipping, for skipping a GEOS overlay whose area can
// only land on one side of the threshold the caller tests.
//
// Sutherland-Hodgman clipping of any simple polygon by a convex window keeps
// its winding number inside the window, so the clipped ring's signed area is
// the overlap's area even where the polygon is concave.  A second simple
// polygon is a signed fan of triangles, so the overlap of two simple polygons
// is a signed sum of triangle clips.  This matches GEOS's overlay area to
// within rounding (~1e-9 at cell coordinates).  Callers trust it only when it
// clears their threshold by kClipMargin and run GEOS otherwise, so every
// verdict -- and the navmesh -- is identical to the Python reference.
#pragma once

#include <algorithm>
#include <cmath>
#include <vector>

namespace navgrow {

// Margin by which a clipped area must clear its threshold to be trusted.
inline constexpr double kClipMargin = 1e-4;

inline double cross3(double ox, double oy, double ax, double ay, double bx,
                     double by) {
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox);
}

// Twice the signed plan area of the triangle xy[0..6).
inline double tri_area2(const double* xy) {
    return cross3(xy[0], xy[1], xy[2], xy[3], xy[4], xy[5]);
}

// Signed area of the ring xy[0..2n) (open: the last point is not repeated).
inline double ring_area(const double* xy, size_t n) {
    double a2 = 0.0;
    for (size_t i = 0; i < n; ++i) {
        const size_t j = (i + 1) % n;
        a2 += xy[2 * i] * xy[2 * j + 1] - xy[2 * j] * xy[2 * i + 1];
    }
    return 0.5 * a2;
}

// The integral of ring `p`'s winding number over the CCW triangle `t`: the
// signed area of p clipped to t.  `cur` / `next` are the caller's scratch.
inline double winding_area_in(const double* p, size_t n, const double t[6],
                              std::vector<double>& cur,
                              std::vector<double>& next) {
    cur.assign(p, p + 2 * n);
    for (int e = 0; e < 3 && cur.size() >= 6; ++e) {
        const double ax = t[2 * e], ay = t[2 * e + 1];
        const double bx = t[2 * ((e + 1) % 3)], by = t[2 * ((e + 1) % 3) + 1];
        next.clear();
        const size_t nc = cur.size() / 2;
        for (size_t i = 0; i < nc; ++i) {
            const size_t j = (i + 1) % nc;
            const double sx = cur[2 * i], sy = cur[2 * i + 1];
            const double tx = cur[2 * j], ty = cur[2 * j + 1];
            const double ds = cross3(ax, ay, bx, by, sx, sy);
            const double dt = cross3(ax, ay, bx, by, tx, ty);
            if (ds >= 0) { next.push_back(sx); next.push_back(sy); }
            if ((ds >= 0) != (dt >= 0)) {
                const double f = ds / (ds - dt);
                next.push_back(sx + (tx - sx) * f);
                next.push_back(sy + (ty - sy) * f);
            }
        }
        cur.swap(next);
    }
    return cur.size() >= 6 ? ring_area(cur.data(), cur.size() / 2) : 0.0;
}

// Area and centroid of the overlap of two triangles (x, y pairs, either
// winding, each with non-zero area); false when the overlap has no area.
inline bool tri_overlap(const double* a, const double* b, double* area,
                        double* cx, double* cy) {
    double t[6] = {b[0], b[1], b[2], b[3], b[4], b[5]};
    if (tri_area2(t) < 0) {
        std::swap(t[2], t[4]);
        std::swap(t[3], t[5]);
    }
    std::vector<double> cur, next;
    winding_area_in(a, 3, t, cur, next);
    const size_t k = cur.size() / 2;
    if (k < 3) return false;
    const double ox = cur[0], oy = cur[1];
    double sx = 0.0, sy = 0.0, a2 = 0.0;
    for (size_t i = 1; i + 1 < k; ++i) {
        const double x1 = cur[2 * i] - ox, y1 = cur[2 * i + 1] - oy;
        const double x2 = cur[2 * i + 2] - ox, y2 = cur[2 * i + 3] - oy;
        const double c = x1 * y2 - x2 * y1;
        a2 += c;
        sx += c * (x1 + x2);
        sy += c * (y1 + y2);
    }
    if (a2 == 0.0) return false;
    *area = 0.5 * std::fabs(a2);
    *cx = ox + sx / (3.0 * a2);
    *cy = oy + sy / (3.0 * a2);
    return true;
}

// Area of the overlap of two simple rings (open x, y pairs, either winding).
inline double overlap_area(const double* a, size_t na, const double* b,
                           size_t nb) {
    if (na < 3 || nb < 3) return 0.0;
    const double ox = b[0], oy = b[1];
    std::vector<double> pa(2 * na), cur, next;
    for (size_t i = 0; i < na; ++i) {
        pa[2 * i] = a[2 * i] - ox;
        pa[2 * i + 1] = a[2 * i + 1] - oy;
    }
    double total = 0.0;
    for (size_t i = 1; i + 1 < nb; ++i) {
        double t[6] = {0.0, 0.0, b[2 * i] - ox, b[2 * i + 1] - oy,
                       b[2 * i + 2] - ox, b[2 * i + 3] - oy};
        const double s = tri_area2(t);
        if (s == 0.0) continue;
        if (s < 0) {
            std::swap(t[2], t[4]);
            std::swap(t[3], t[5]);
        }
        const double w = winding_area_in(pa.data(), na, t, cur, next);
        total += s < 0 ? -w : w;
    }
    const double sa = ring_area(pa.data(), na);
    const double sb = ring_area(b, nb);
    return (sa < 0) != (sb < 0) ? -total : total;
}

// --------------------------------------------------------------------------
// Do two simple rings intersect (GEOS `intersects`: closures share a point)?
// 1 yes, 0 no, -1 too close to call.  Yes when two edges cross with every
// endpoint clear of the other edge's line by kSepMargin; no / yes-by-
// containment when the boundaries stay kSepMargin apart.  Anything nearer is
// left to GEOS, so the verdict always matches it.
// --------------------------------------------------------------------------
inline constexpr double kSepMargin = 1e-5;

// Squared distance from (px, py) to segment (ax, ay)-(bx, by).
inline double seg_dist2(double px, double py, double ax, double ay, double bx,
                        double by) {
    const double dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    double t = l2 > 0.0 ? ((px - ax) * dx + (py - ay) * dy) / l2 : 0.0;
    t = std::max(0.0, std::min(1.0, t));
    const double ex = ax + dx * t - px, ey = ay + dy * t - py;
    return ex * ex + ey * ey;
}

// Crossing-number containment of (px, py) in the ring (open pairs).
inline bool ring_contains(const double* r, size_t n, double px, double py) {
    bool in = false;
    for (size_t i = 0, j = n - 1; i < n; j = i++) {
        const double xi = r[2 * i], yi = r[2 * i + 1];
        const double xj = r[2 * j], yj = r[2 * j + 1];
        if ((yi > py) != (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi)
            in = !in;
    }
    return in;
}

// Edge p0-p1 against edge q0-q1: 1 a clear proper crossing, -1 within the
// margin of each other, 0 clearly apart.
inline int edge_contact(const double* p0, const double* p1, const double* q0,
                        const double* q1) {
    const double m = kSepMargin;
    if (std::max(p0[0], p1[0]) + m < std::min(q0[0], q1[0])
        || std::max(q0[0], q1[0]) + m < std::min(p0[0], p1[0])
        || std::max(p0[1], p1[1]) + m < std::min(q0[1], q1[1])
        || std::max(q0[1], q1[1]) + m < std::min(p0[1], p1[1]))
        return 0;
    const double lp = std::hypot(p1[0] - p0[0], p1[1] - p0[1]);
    const double lq = std::hypot(q1[0] - q0[0], q1[1] - q0[1]);
    const double o1 = cross3(p0[0], p0[1], p1[0], p1[1], q0[0], q0[1]);
    const double o2 = cross3(p0[0], p0[1], p1[0], p1[1], q1[0], q1[1]);
    const double o3 = cross3(q0[0], q0[1], q1[0], q1[1], p0[0], p0[1]);
    const double o4 = cross3(q0[0], q0[1], q1[0], q1[1], p1[0], p1[1]);
    if ((o1 < 0) != (o2 < 0) && (o3 < 0) != (o4 < 0)
        && std::min(std::fabs(o1), std::fabs(o2)) > m * lp
        && std::min(std::fabs(o3), std::fabs(o4)) > m * lq)
        return 1;
    const double m2 = m * m;
    if (seg_dist2(q0[0], q0[1], p0[0], p0[1], p1[0], p1[1]) <= m2
        || seg_dist2(q1[0], q1[1], p0[0], p0[1], p1[0], p1[1]) <= m2
        || seg_dist2(p0[0], p0[1], q0[0], q0[1], q1[0], q1[1]) <= m2
        || seg_dist2(p1[0], p1[1], q0[0], q0[1], q1[0], q1[1]) <= m2
        || ((o1 < 0) != (o2 < 0) && (o3 < 0) != (o4 < 0)))
        return -1;
    return 0;
}

inline int rings_intersect(const double* a, size_t na, const double* b,
                           size_t nb) {
    if (na < 3 || nb < 3) return -1;
    const double ox = a[0], oy = a[1];
    std::vector<double> pa(2 * na), pb(2 * nb);
    for (size_t i = 0; i < 2 * na; i += 2) { pa[i] = a[i] - ox; pa[i + 1] = a[i + 1] - oy; }
    for (size_t i = 0; i < 2 * nb; i += 2) { pb[i] = b[i] - ox; pb[i + 1] = b[i + 1] - oy; }
    bool near = false;
    for (size_t i = 0; i < na; ++i) {
        const double *p0 = &pa[2 * i], *p1 = &pa[2 * ((i + 1) % na)];
        for (size_t j = 0; j < nb; ++j) {
            const int c = edge_contact(p0, p1, &pb[2 * j], &pb[2 * ((j + 1) % nb)]);
            if (c == 1) return 1;
            near = near || c < 0;
        }
    }
    if (near) return -1;
    return ring_contains(pb.data(), nb, pa[0], pa[1])
        || ring_contains(pa.data(), na, pb[0], pb[1]) ? 1 : 0;
}

}  // namespace navgrow
