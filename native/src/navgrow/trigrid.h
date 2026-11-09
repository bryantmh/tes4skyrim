// Uniform XY triangle index and the collision queries built on it,
// shared by the width-grow march (grow.cpp) and the Python-facing
// samplers (sampler.cpp).
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <vector>

#include "pymath.h"

namespace navgrow {

// Hard ceiling on the dense bucket grid a TriGrid may allocate.
//
// TriGrid indexes buckets DENSELY (counts/starts are nx*ny entries), so the
// allocation is driven by the soup's XY *extent*, not its triangle count. A
// single garbage vertex is therefore unbounded-allocation-shaped: Nehrim's
// interior cell 011E4FEC ships refs with PosY = 8.936e17 (a real, exported
// value), which put the extent at 8.9e17 units -> 5.4e14 buckets -> a 4-billion-
// GB request. std::bad_alloc from inside Py_BEGIN_ALLOW_THREADS with no handler
// anywhere in the extension called std::terminate(), so the pool worker died by
// abort() with no Python traceback -- surfacing in the parent only as an opaque
// BrokenProcessPool (see docs/performance_notes.md).
//
// 16M buckets is ~128 MB across counts+starts, and is orders of magnitude above
// anything real: at the 128-unit bucket size a whole 4096-unit exterior cell is
// 33x33 = 1,089 buckets, and 16M would be a soup ~512,000 units across -- more
// than twice the width of the entire Tamriel worldspace. Exceeding it means the
// input is corrupt, so the call raises ValueError rather than clamping: a
// silently reduced grid would emit a wrong navmesh instead of a diagnosable
// failure.
inline constexpr long long kMaxBuckets = 16LL * 1024 * 1024;

// How far past its XY box triangle `t` (extent `ext`) can still pass
// tri_height's -0.02 barycentric tolerance: two coordinates at -0.02 put the
// point within 0.04 * ext of the box, padded here to 0.1 * ext + 1.  When the
// plan determinant is so small that rounding in the coordinates could exceed
// the tolerance for a point a bucket neighbourhood away, the pad is infinite.
inline double height_pad(const double* t, double ext) {
    const double d = (t[4] - t[7]) * (t[0] - t[6]) + (t[6] - t[3]) * (t[1] - t[7]);
    if (!(std::fabs(d) > 1e-12 * ext * (ext + 1024.0)))
        return std::numeric_limits<double>::infinity();
    return 0.1 * ext + 1.0;
}

// --------------------------------------------------------------------------
// Uniform XY bucket grid over a triangle soup.
//
// Mirrors corridor_grow._TriGrid: buckets of `cell` units, queried over the
// 3x3 neighbourhood so a triangle near a bucket boundary is never missed (a
// miss on the wall soup means growth walks straight through a wall).
// --------------------------------------------------------------------------
struct TriGrid {
    std::vector<double> vx;      // 9 doubles per triangle (3 verts x xyz)
    std::vector<double> x0, x1, y0, y1, z0, z1;
    std::vector<double> pad;     // box growth that still holds tri_height hits
    std::vector<double> pbox;    // per bucket entry: x0, x1, y0, y1 padded
    std::vector<double> bhull;   // per bucket: the hull of its entries' pbox
    double minx = 0.0, miny = 0.0, cell = 128.0;
    long long nx = 0, ny = 0;    // grid dimensions
    // CSR-style bucket index: starts[b]..starts[b+1] into `items`.
    // `starts` is 64-bit because total bucket membership (one triangle spans
    // many buckets) overflowed a 32-bit accumulator; `items` holds triangle
    // indices, which are bounded by the triangle count, so int is fine.
    std::vector<long long> starts;
    std::vector<int> items;
    size_t ntri = 0;

    void build(const double* tris, size_t n, double cellsize) {
        ntri = n;
        cell = cellsize;
        vx.assign(tris, tris + n * 9);
        if (!n) return;
        x0.resize(n); x1.resize(n); y0.resize(n);
        y1.resize(n); z0.resize(n); z1.resize(n); pad.resize(n);
        double gminx = 1e300, gminy = 1e300, gmaxx = -1e300, gmaxy = -1e300;
        // NaN/Inf must be rejected before the extent maths: they propagate
        // through min/max, make (gmaxx-gminx)/cell non-finite, and the cast to
        // long long is then undefined behaviour (observed as a garbage nx*ny
        // that allocates until bad_alloc).
        for (size_t i = 0; i < n * 9; ++i) {
            if (!std::isfinite(vx[i]))
                throw std::invalid_argument(
                    "non-finite coordinate in triangle soup");
        }
        for (size_t i = 0; i < n; ++i) {
            const double* t = &vx[i * 9];
            x0[i] = std::min(std::min(t[0], t[3]), t[6]);
            x1[i] = std::max(std::max(t[0], t[3]), t[6]);
            y0[i] = std::min(std::min(t[1], t[4]), t[7]);
            y1[i] = std::max(std::max(t[1], t[4]), t[7]);
            z0[i] = std::min(std::min(t[2], t[5]), t[8]);
            z1[i] = std::max(std::max(t[2], t[5]), t[8]);
            pad[i] = height_pad(t, std::max(x1[i] - x0[i], y1[i] - y0[i]));
            gminx = std::min(gminx, x0[i]); gmaxx = std::max(gmaxx, x1[i]);
            gminy = std::min(gminy, y0[i]); gmaxy = std::max(gmaxy, y1[i]);
        }
        minx = gminx; miny = gminy;
        const double spanx = (gmaxx - gminx) / cell;
        const double spany = (gmaxy - gminy) / cell;
        // Check the span in DOUBLE before casting: a span beyond long long's
        // range makes the cast undefined behaviour, so the guard cannot be
        // written on nx/ny after the fact.
        if (spanx > (double)kMaxBuckets || spany > (double)kMaxBuckets ||
            spanx * spany > (double)kMaxBuckets)
            throw std::invalid_argument("triangle soup XY extent too large");
        nx = (long long)spanx + 1;
        ny = (long long)spany + 1;
        if (nx < 1) nx = 1;
        if (ny < 1) ny = 1;
        if (nx * ny > kMaxBuckets)
            throw std::invalid_argument("triangle soup XY extent too large");

        // Two-pass CSR build: count per bucket, prefix-sum, then fill.  A
        // dense array beats the Python dict-of-lists and keeps each bucket's
        // indices ASCENDING, which is what makes the visit order deterministic.
        std::vector<long long> counts((size_t)(nx * ny) + 1, 0);
        for (size_t i = 0; i < n; ++i) {
            long long a, b, c, d; span(i, a, b, c, d);
            for (long long gx = a; gx <= b; ++gx)
                for (long long gy = c; gy <= d; ++gy)
                    counts[(size_t)(gy * nx + gx)]++;
        }
        // `acc` is the total bucket-membership count: one triangle spanning a
        // wide XY box lands in many buckets, so this is >> n and was an `int`
        // -- it overflowed to negative on a large soup, and the following
        // (size_t)acc turned that into a huge allocation and an out-of-bounds
        // fill. 64-bit throughout, with an explicit check before the cast.
        starts.assign((size_t)(nx * ny) + 1, 0);
        long long acc = 0;
        for (size_t b = 0; b < (size_t)(nx * ny); ++b) {
            starts[b] = acc; acc += counts[b];
        }
        starts[(size_t)(nx * ny)] = acc;
        if (acc < 0)
            throw std::invalid_argument("bucket membership count overflow");
        items.assign((size_t)acc, 0);
        std::vector<long long> cur(starts.begin(), starts.end() - 1);
        for (size_t i = 0; i < n; ++i) {
            long long a, b, c, d; span(i, a, b, c, d);
            for (long long gx = a; gx <= b; ++gx)
                for (long long gy = c; gy <= d; ++gy)
                    items[(size_t)cur[(size_t)(gy * nx + gx)]++] = (int)i;
        }
        build_padded_boxes();
    }

    // Triangle i's bucket span, clamped to the grid.
    void span(size_t i, long long& a, long long& b, long long& c, long long& d) const {
        a = std::max(0LL, std::min((long long)py_floordiv(x0[i] - minx, cell), nx - 1));
        b = std::max(0LL, std::min((long long)py_floordiv(x1[i] - minx, cell), nx - 1));
        c = std::max(0LL, std::min((long long)py_floordiv(y0[i] - miny, cell), ny - 1));
        d = std::max(0LL, std::min((long long)py_floordiv(y1[i] - miny, cell), ny - 1));
    }

    // ----------------------------------------------------------------------
    // Fine index for walk_sample: each triangle listed under every kFineCell
    // cell its padded box overlaps (an unbounded pad goes to `unbounded`), plus
    // its clamped bucket span, from which the 3x3 visit rank follows.
    // ----------------------------------------------------------------------
    static constexpr double kFineCell = 32.0;
    std::vector<long long> span4, fstarts;
    std::vector<int> fitems, unbounded;
    double fminx = 0.0, fminy = 0.0;
    long long fnx = 0, fny = 0;

    bool fine_span(size_t i, long long& a, long long& b, long long& c, long long& d) const {
        if (!std::isfinite(pad[i])) return false;
        a = (long long)std::floor((x0[i] - pad[i] - fminx) / kFineCell);
        b = (long long)std::floor((x1[i] + pad[i] - fminx) / kFineCell);
        c = (long long)std::floor((y0[i] - pad[i] - fminy) / kFineCell);
        d = (long long)std::floor((y1[i] + pad[i] - fminy) / kFineCell);
        return true;
    }

    void build_walk_index() {
        if (!ntri) return;
        span4.resize(ntri * 4);
        double ex0 = 1e300, ex1 = -1e300, ey0 = 1e300, ey1 = -1e300;
        for (size_t i = 0; i < ntri; ++i) {
            span(i, span4[i * 4], span4[i * 4 + 1], span4[i * 4 + 2], span4[i * 4 + 3]);
            if (!std::isfinite(pad[i])) { unbounded.push_back((int)i); continue; }
            ex0 = std::min(ex0, x0[i] - pad[i]); ex1 = std::max(ex1, x1[i] + pad[i]);
            ey0 = std::min(ey0, y0[i] - pad[i]); ey1 = std::max(ey1, y1[i] + pad[i]);
        }
        if (ex0 > ex1) { walk_fine = true; return; }
        fminx = ex0; fminy = ey0;
        const double sx = (ex1 - ex0) / kFineCell, sy = (ey1 - ey0) / kFineCell;
        if (sx * sy > (double)kMaxBuckets) return;
        fnx = (long long)sx + 1; fny = (long long)sy + 1;
        fstarts.assign((size_t)(fnx * fny) + 1, 0);
        for (int pass = 0; pass < 2; ++pass) {
            std::vector<long long> cur(fstarts.begin(), fstarts.end() - 1);
            for (size_t i = 0; i < ntri; ++i) {
                long long a, b, c, d;
                if (!fine_span(i, a, b, c, d)) continue;
                for (long long gy = std::max(c, 0LL); gy <= std::min(d, fny - 1); ++gy)
                    for (long long gx = std::max(a, 0LL); gx <= std::min(b, fnx - 1); ++gx) {
                        const size_t f = (size_t)(gy * fnx + gx);
                        if (pass) fitems[(size_t)cur[f]++] = (int)i;
                        else fstarts[f + 1]++;
                    }
            }
            if (pass) break;
            for (size_t f = 0; f < (size_t)(fnx * fny); ++f) fstarts[f + 1] += fstarts[f];
            fitems.assign((size_t)fstarts.back(), 0);
        }
        walk_fine = true;
    }

    bool walk_fine = false;      // build_walk_index succeeded

    // Each bucket entry's padded box (pbox, in `items` order, so a bucket scan
    // reads it in sequence) and each bucket's hull of them (bhull).
    void build_padded_boxes() {
        pbox.resize(items.size() * 4);
        bhull.assign((size_t)(nx * ny) * 4, 0.0);
        for (size_t b = 0; b < (size_t)(nx * ny); ++b) {
            double h[4] = {1e300, -1e300, 1e300, -1e300};
            for (long long p = starts[b]; p < starts[b + 1]; ++p) {
                const size_t k = (size_t)items[(size_t)p];
                double* q = &pbox[(size_t)p * 4];
                q[0] = x0[k] - pad[k]; q[1] = x1[k] + pad[k];
                q[2] = y0[k] - pad[k]; q[3] = y1[k] + pad[k];
                h[0] = std::min(h[0], q[0]); h[1] = std::max(h[1], q[1]);
                h[2] = std::min(h[2], q[2]); h[3] = std::max(h[3], q[3]);
            }
            std::copy(h, h + 4, &bhull[b * 4]);
        }
    }

    // The bucket holding (x, y) as Python's `int((x - minx) // cell)` names it;
    // false when the soup is empty or the point is past any bucket.
    bool bucket_of(double x, double y, long long* bx, long long* by) const {
        if (!ntri || items.empty()) return false;
        const double fx = py_floordiv(x - minx, cell);
        const double fy = py_floordiv(y - miny, cell);
        if (!(std::fabs(fx) < 4e18) || !(std::fabs(fy) < 4e18)) return false;
        *bx = (long long)fx;
        *by = (long long)fy;
        return true;
    }

    // Visit the triangles of the 3x3 neighbourhood of (x, y) in the order the
    // Python _TriGrid.candidates returns them: dx outer, dy inner, each bucket
    // ascending, each triangle once.  `stamp` (ntri entries) and `epoch` are
    // the caller's scratch for the once-only test.
    template <class F>
    void visit_py_order(double x, double y, std::vector<unsigned>& stamp,
                        unsigned& epoch, F&& f) const {
        long long gx, gy;
        if (!bucket_of(x, y, &gx, &gy)) return;
        if (++epoch == 0) {
            std::fill(stamp.begin(), stamp.end(), 0u);
            epoch = 1;
        }
        for (long long dx = -1; dx <= 1; ++dx) {
            const long long bx = gx + dx;
            if (bx < 0 || bx >= nx) continue;
            for (long long dy = -1; dy <= 1; ++dy) {
                const long long by = gy + dy;
                if (by < 0 || by >= ny) continue;
                const size_t b = (size_t)(by * nx + bx);
                for (long long p = starts[b]; p < starts[b + 1]; ++p) {
                    const int i = items[(size_t)p];
                    if (stamp[(size_t)i] == epoch) continue;
                    stamp[(size_t)i] = epoch;
                    f(i);
                }
            }
        }
    }
};

// Barycentric height of triangle `t` (9 doubles) at (x, y), mirroring the
// Python samplers: false for a degenerate plan or a point outside by more than
// the 0.02 tolerance.
inline bool tri_height(const double* t, double x, double y, double* z) {
    const double ax = t[0], ay = t[1], az = t[2];
    const double bx = t[3], by = t[4], bz = t[5];
    const double cx = t[6], cy = t[7], cz = t[8];
    const double d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
    if (std::fabs(d) < 1e-6) return false;
    const double l0 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / d;
    const double l1 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / d;
    const double l2 = 1.0 - l0 - l1;
    if (l0 < -0.02 || l1 < -0.02 || l2 < -0.02) return false;
    *z = l0 * az + l1 * bz + l2 * cz;
    return true;
}

// Heights of every triangle in the single bucket at (x, y), ascending index
// order -- corridor._bucket_heights.
inline void bucket_heights(const TriGrid& g, double x, double y,
                           std::vector<double>& out) {
    out.clear();
    long long bx, by;
    if (!g.bucket_of(x, y, &bx, &by)) return;
    if (bx < 0 || bx >= g.nx || by < 0 || by >= g.ny) return;
    const size_t b = (size_t)(by * g.nx + bx);
    for (long long p = g.starts[b]; p < g.starts[b + 1]; ++p) {
        double z;
        if (tri_height(&g.vx[(size_t)g.items[(size_t)p] * 9], x, y, &z))
            out.push_back(z);
    }
}

// corridor._surface_sampler: the single-bucket height nearest near_z, first
// wins on a tie; false when none covers (x, y).
inline bool surface_nearest(const TriGrid& g, double x, double y, double near_z,
                            double* out) {
    long long bx, by;
    if (!g.bucket_of(x, y, &bx, &by)) return false;
    if (bx < 0 || bx >= g.nx || by < 0 || by >= g.ny) return false;
    const size_t b = (size_t)(by * g.nx + bx);
    bool have = false;
    for (long long p = g.starts[b]; p < g.starts[b + 1]; ++p) {
        double z;
        if (!tri_height(&g.vx[(size_t)g.items[(size_t)p] * 9], x, y, &z)) continue;
        if (!have || std::fabs(z - near_z) < std::fabs(*out - near_z)) {
            *out = z;
            have = true;
        }
    }
    return have;
}

// corridor_grow.walkable_sampler: the 3x3-neighbourhood height nearest near_z
// in the reference's candidate order, first wins on a tie.
inline bool walk_nearest_py(const TriGrid& g, double x, double y, double near_z,
                            std::vector<unsigned>& stamp, unsigned& epoch,
                            double* out) {
    bool have = false;
    g.visit_py_order(x, y, stamp, epoch, [&](int i) {
        double z;
        if (!tri_height(&g.vx[(size_t)i * 9], x, y, &z)) return;
        if (!have || std::fabs(z - near_z) < std::fabs(*out - near_z)) {
            *out = z;
            have = true;
        }
    });
    return have;
}

// --------------------------------------------------------------------------
// Thin oriented slab vs triangle (mirrors _tri_hits_slab).
//
// The slab is centered at (cx,cy): half_w along the edge tangent (~actor
// width), `depth` along the march direction (thin), Z span [z_lo, z_hi].
// Tested by projecting into the slab's own 2D frame and running SAT against
// the axis-aligned rectangle, gated by Z overlap.
// --------------------------------------------------------------------------
// The height range of the part of a triangle, given in the slab frame as
// (along tangent, along march, z), inside |x| <= half_w, |y| <= depth; false
// when none of it is.  Planar, so the clipped polygon's corners bound it.
inline bool clipped_z_range(const double* px, const double* py, const double* pz,
                            double half_w, double depth, double* lo, double* hi) {
    std::vector<std::array<double, 3>> poly{{px[0], py[0], pz[0]}, {px[1], py[1], pz[1]},
                                            {px[2], py[2], pz[2]}};
    const double planes[4][3] = {{1, 0, half_w}, {-1, 0, half_w}, {0, 1, depth}, {0, -1, depth}};
    for (const auto& pl : planes) {
        std::vector<std::array<double, 3>> out;
        for (size_t i = 0; i < poly.size(); ++i) {
            const auto& a = poly[i];
            const auto& b = poly[(i + 1) % poly.size()];
            const double da = pl[2] - (pl[0] * a[0] + pl[1] * a[1]);
            const double db = pl[2] - (pl[0] * b[0] + pl[1] * b[1]);
            if (da >= 0) out.push_back(a);
            if ((da >= 0) != (db >= 0)) {
                const double f = da / (da - db);
                out.push_back({a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f,
                               a[2] + (b[2] - a[2]) * f});
            }
        }
        poly.swap(out);
        if (poly.empty()) return false;
    }
    *lo = 1e300;
    *hi = -1e300;
    for (const auto& p : poly) {
        *lo = std::min(*lo, p[2]);
        *hi = std::max(*hi, p[2]);
    }
    return true;
}

// `exact` gates Z by the part of the triangle inside the slab, not its whole
// span: a body column must not read a slope under its feet as a wall.
inline bool tri_hits_slab(const double* t, double cx, double cy,
                          double ux, double uy, double half_w,
                          double tx, double ty, double depth,
                          double z_lo, double z_hi, bool exact = false) {
    const double tz0 = t[2], tz1 = t[5], tz2 = t[8];
    if (std::max(std::max(tz0, tz1), tz2) < z_lo) return false;
    if (std::min(std::min(tz0, tz1), tz2) > z_hi) return false;

    double px[3], py[3];
    for (int k = 0; k < 3; ++k) {
        const double ox = t[k * 3 + 0] - cx, oy = t[k * 3 + 1] - cy;
        px[k] = ox * tx + oy * ty;      // along tangent
        py[k] = ox * ux + oy * uy;      // along march
    }
    const double pxmin = std::min(std::min(px[0], px[1]), px[2]);
    const double pxmax = std::max(std::max(px[0], px[1]), px[2]);
    if (pxmin > half_w || pxmax < -half_w) return false;
    const double pymin = std::min(std::min(py[0], py[1]), py[2]);
    const double pymax = std::max(std::max(py[0], py[1]), py[2]);
    if (pymin > depth || pymax < -depth) return false;

    // SAT on the triangle's three edge normals vs the rectangle corners.
    const double rx[4] = {-half_w, half_w, half_w, -half_w};
    const double ry[4] = {-depth, -depth, depth, depth};
    for (int k = 0; k < 3; ++k) {
        const int k2 = (k + 1) % 3;
        const double nx = -(py[k2] - py[k]);
        const double ny = (px[k2] - px[k]);
        double tmin = 1e300, tmax = -1e300, rmin = 1e300, rmax = -1e300;
        for (int m = 0; m < 3; ++m) {
            const double p = nx * px[m] + ny * py[m];
            tmin = std::min(tmin, p); tmax = std::max(tmax, p);
        }
        for (int m = 0; m < 4; ++m) {
            const double p = nx * rx[m] + ny * ry[m];
            rmin = std::min(rmin, p); rmax = std::max(rmax, p);
        }
        if (tmin > rmax || tmax < rmin) return false;
    }
    if (!exact) return true;
    const double pz[3] = {tz0, tz1, tz2};
    double lo = 0.0, hi = 0.0;
    return clipped_z_range(px, py, pz, half_w, depth, &lo, &hi) && hi >= z_lo && lo <= z_hi;
}

// Wall test over the 3x3 bucket neighbourhood.
//
// The answer is "does ANY candidate hit", so it does not depend on visit order,
// and two exact supersets of the hitters prune the scan: buckets outside the
// slab's XY box (kept within the 3x3 window, which always holds one bucket of
// every triangle overlapping both), and triangles whose box misses the slab's.
// `kSlabPad` absorbs the SAT's rounding so neither prune can drop a hit.
inline constexpr double kSlabPad = 1e-3;

inline bool wall_hit(const TriGrid& g, double cx, double cy,
                     double ux, double uy, double tx, double ty,
                     double z_lo, double z_hi, double depth, double half_w,
                     bool exact = false) {
    if (!g.ntri || g.items.empty()) return false;
    const long long gx = (long long)std::floor((cx - g.minx) / g.cell);
    const long long gy = (long long)std::floor((cy - g.miny) / g.cell);
    const double ex = std::fabs(tx) * half_w + std::fabs(ux) * depth + kSlabPad;
    const double ey = std::fabs(ty) * half_w + std::fabs(uy) * depth + kSlabPad;
    const double sx0 = cx - ex, sx1 = cx + ex, sy0 = cy - ey, sy1 = cy + ey;
    const long long bx0 = std::max(gx - 1,
        (long long)std::floor((sx0 - g.minx) / g.cell));
    const long long bx1 = std::min(gx + 1,
        (long long)std::floor((sx1 - g.minx) / g.cell));
    const long long by0 = std::max(gy - 1,
        (long long)std::floor((sy0 - g.miny) / g.cell));
    const long long by1 = std::min(gy + 1,
        (long long)std::floor((sy1 - g.miny) / g.cell));
    for (long long by = by0; by <= by1; ++by) {
        if (by < 0 || by >= g.ny) continue;
        for (long long bx = bx0; bx <= bx1; ++bx) {
            if (bx < 0 || bx >= g.nx) continue;
            const size_t b = (size_t)(by * g.nx + bx);
            for (long long p = g.starts[b]; p < g.starts[b + 1]; ++p) {
                const int i = g.items[(size_t)p];
                const size_t k = (size_t)i;
                if (g.z1[k] < z_lo || g.z0[k] > z_hi) continue;
                if (g.x1[k] < sx0 || g.x0[k] > sx1 || g.y1[k] < sy0
                    || g.y0[k] > sy1) continue;
                if (tri_hits_slab(&g.vx[(size_t)i * 9], cx, cy, ux, uy,
                                  half_w, tx, ty, depth, z_lo, z_hi, exact))
                    return true;
            }
        }
    }
    return false;
}

// Walkable Z nearest `near_z` at (x,y); returns false when no surface covers
// the point (mirrors walkable_sampler's None).  Ties go to the first triangle
// visited; a bucket or entry skipped by its padded box holds no triangle that
// can cover the point, so the skips never reorder the ones that do.
//
// With the fine index the same answer comes from the few triangles listed at
// (x, y): a covering triangle wins on a smaller distance, and on a tie the one
// the 3x3 scan meets first, i.e. the lower (first window bucket, index).
inline bool walk_sample_fine(const TriGrid& g, double x, double y, double near_z,
                             double* out, int* tri) {
    const long long gx = (long long)std::floor((x - g.minx) / g.cell);
    const long long gy = (long long)std::floor((y - g.miny) / g.cell);
    bool have = false;
    double best = 0.0, best_d = 0.0;
    long long best_rank = 0;
    int best_i = -1;
    auto consider = [&](int i) {
        const long long* s = &g.span4[(size_t)i * 4];
        const long long ix = std::max(s[0], gx - 1), iy = std::max(s[2], gy - 1);
        if (ix > std::min(s[1], gx + 1) || iy > std::min(s[3], gy + 1)) return;
        double z;
        if (!tri_height(&g.vx[(size_t)i * 9], x, y, &z)) return;
        const double dz = std::fabs(z - near_z);
        const long long rank = ((iy - gy + 1) * 3 + (ix - gx + 1)) * (long long)g.ntri + i;
        if (!have || dz < best_d || (dz == best_d && rank < best_rank)) {
            best = z; best_d = dz; best_rank = rank; best_i = i; have = true;
        }
    };
    const double fx = std::floor((x - g.fminx) / TriGrid::kFineCell);
    const double fy = std::floor((y - g.fminy) / TriGrid::kFineCell);
    if (fx >= 0.0 && fy >= 0.0 && fx < (double)g.fnx && fy < (double)g.fny) {
        const size_t f = (size_t)((long long)fy * g.fnx + (long long)fx);
        for (long long p = g.fstarts[f]; p < g.fstarts[f + 1]; ++p)
            consider(g.fitems[(size_t)p]);
    }
    for (int i : g.unbounded) consider(i);
    if (have) *out = best;
    if (have && tri) *tri = best_i;
    return have;
}

inline bool walk_sample(const TriGrid& g, double x, double y, double near_z,
                        double* out, int* tri = nullptr) {
    if (!g.ntri || g.items.empty()) return false;
    if (g.walk_fine) return walk_sample_fine(g, x, y, near_z, out, tri);
    const long long gx = (long long)std::floor((x - g.minx) / g.cell);
    const long long gy = (long long)std::floor((y - g.miny) / g.cell);
    bool have = false;
    double best = 0.0;
    int best_i = -1;
    for (long long dy = -1; dy <= 1; ++dy) {
        const long long by = gy + dy;
        if (by < 0 || by >= g.ny) continue;
        for (long long dx = -1; dx <= 1; ++dx) {
            const long long bx = gx + dx;
            if (bx < 0 || bx >= g.nx) continue;
            const size_t b = (size_t)(by * g.nx + bx);
            const double* h = &g.bhull[b * 4];
            if (x < h[0] || x > h[1] || y < h[2] || y > h[3]) continue;
            for (long long p = g.starts[b]; p < g.starts[b + 1]; ++p) {
                const double* q = &g.pbox[(size_t)p * 4];
                if (x < q[0] || x > q[1] || y < q[2] || y > q[3]) continue;
                const int i = g.items[(size_t)p];
                double z;
                if (!tri_height(&g.vx[(size_t)i * 9], x, y, &z)) continue;
                if (!have || std::fabs(z - near_z) < std::fabs(best - near_z)) {
                    best = z; best_i = i; have = true;
                }
            }
        }
    }
    if (have) *out = best;
    if (have && tri) *tri = best_i;
    return have;
}

}  // namespace navgrow
