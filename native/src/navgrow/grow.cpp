// Native Phase-2 corridor width-grow for tes5_import.navmesh.corridor_grow.
//
// WHY THIS EXISTS
// ---------------
// Measured on the interior cell Wendir02 (471 nodes / 938 edges):
// _build_corridor_strips alone ran past 50s, and a microbenchmark put the
// reason in one place -- wall_hit costs ~170us and the strip build makes ~890k
// of them, i.e. ~150s for that ONE cell.  Across 8,228 pathgrid cells that is
// hundreds of core-hours, which is why generation was taking far longer than
// the 1.5h that prompted this work.
//
// The cost is NOT one hot arithmetic kernel.  Per wall_hit the grid query
// returns ~140 candidate triangles, and each one runs a pure-Python
// separating-axis test built out of list comprehensions and min()/max() over
// temporary lists.  There is no Python-level fix: the work is ~125M tiny float
// operations per cell, each wrapped in interpreter overhead.
//
// So the WHOLE march moves across the boundary, not just the slab test.
// grow_strips() takes a cell's geometry once and returns every station's grown
// half-width, turning ~890k boundary crossings per cell into ONE.  This mirrors
// decimate.cpp's reasoning (marshal once per cell, keep the loop nest native).
//
// WHAT IT MUST PRESERVE
// ---------------------
// Every predicate mirrors corridor_grow.py operation for operation, because
// each encodes a geometry contract that was expensive to get right:
//
//   * The wall test sweeps the INTERVAL [prev, d] via a slab centered on the
//     midpoint with half the step as depth -- a point probe steps over a wall
//     that falls between samples (that caused 124 through-wall triangles).
//   * On a hit, RIBBON_GROW_BISECT bisections land the rail AT the wall; a
//     step-short stop is what narrowed every doorway.
//   * The walkable-floor test binds only BEYOND the soft floor `lo`, so a node
//     at a threshold or ledge lip does not collapse its corridor.
//   * `lo` is a SOFT minimum that a wall always overrides; the march therefore
//     starts at 0 rather than at `lo`.
//   * The neighbour cap counts only roughly-PARALLEL edges within a Z window,
//     and excludes the edge's own endpoints.
//
// DETERMINISM
// -----------
// The pipeline's output must be byte-reproducible.  Nothing here depends on
// pointer values, hash iteration order, or uninitialised memory; candidate
// triangles are visited in ascending index order (the grid stores sorted
// indices), and the march is a fixed sequence of float ops.  Exact bit equality
// with the Python path is not required (float summation order differs by ~1
// ULP, same as decimate.cpp) but run-to-run determinism is.

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <new>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

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
static const long long kMaxBuckets = 16LL * 1024 * 1024;

namespace {

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
        y1.resize(n); z0.resize(n); z1.resize(n);
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
        auto span = [&](size_t i, long long& a, long long& b,
                        long long& c, long long& d) {
            a = (long long)std::floor((x0[i] - minx) / cell);
            b = (long long)std::floor((x1[i] - minx) / cell);
            c = (long long)std::floor((y0[i] - miny) / cell);
            d = (long long)std::floor((y1[i] - miny) / cell);
            a = std::max(0LL, std::min(a, nx - 1));
            b = std::max(0LL, std::min(b, nx - 1));
            c = std::max(0LL, std::min(c, ny - 1));
            d = std::max(0LL, std::min(d, ny - 1));
        };
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
    }
};

// --------------------------------------------------------------------------
// Thin oriented slab vs triangle (mirrors _tri_hits_slab).
//
// The slab is centered at (cx,cy): half_w along the edge tangent (~actor
// width), `depth` along the march direction (thin), Z span [z_lo, z_hi].
// Tested by projecting into the slab's own 2D frame and running SAT against
// the axis-aligned rectangle, gated by Z overlap.
// --------------------------------------------------------------------------
inline bool tri_hits_slab(const double* t, double cx, double cy,
                          double ux, double uy, double half_w,
                          double tx, double ty, double depth,
                          double z_lo, double z_hi) {
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
    return true;
}

// Wall test over the 3x3 bucket neighbourhood.
inline bool wall_hit(const TriGrid& g, double cx, double cy,
                     double ux, double uy, double tx, double ty,
                     double z_lo, double z_hi, double depth, double half_w) {
    if (!g.ntri || g.items.empty()) return false;
    const long long gx = (long long)std::floor((cx - g.minx) / g.cell);
    const long long gy = (long long)std::floor((cy - g.miny) / g.cell);
    for (long long dy = -1; dy <= 1; ++dy) {
        const long long by = gy + dy;
        if (by < 0 || by >= g.ny) continue;
        for (long long dx = -1; dx <= 1; ++dx) {
            const long long bx = gx + dx;
            if (bx < 0 || bx >= g.nx) continue;
            const size_t b = (size_t)(by * g.nx + bx);
            for (long long p = g.starts[b]; p < g.starts[b + 1]; ++p) {
                const int i = g.items[(size_t)p];
                if (g.z1[(size_t)i] < z_lo || g.z0[(size_t)i] > z_hi) continue;
                if (tri_hits_slab(&g.vx[(size_t)i * 9], cx, cy, ux, uy,
                                  half_w, tx, ty, depth, z_lo, z_hi))
                    return true;
            }
        }
    }
    return false;
}

// Walkable Z nearest `near_z` at (x,y); returns false when no surface covers
// the point (mirrors walkable_sampler's None).
inline bool walk_sample(const TriGrid& g, double x, double y, double near_z,
                        double* out, int* tri = nullptr) {
    if (!g.ntri || g.items.empty()) return false;
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
            for (long long p = g.starts[b]; p < g.starts[b + 1]; ++p) {
                const int i = g.items[(size_t)p];
                const double* t = &g.vx[(size_t)i * 9];
                const double ax = t[0], ay = t[1], az = t[2];
                const double bx2 = t[3], by2 = t[4], bz = t[5];
                const double cx2 = t[6], cy2 = t[7], cz = t[8];
                const double d = (by2 - cy2) * (ax - cx2) +
                                 (cx2 - bx2) * (ay - cy2);
                if (std::fabs(d) < 1e-6) continue;
                const double l0 = ((by2 - cy2) * (x - cx2) +
                                   (cx2 - bx2) * (y - cy2)) / d;
                const double l1 = ((cy2 - ay) * (x - cx2) +
                                   (ax - cx2) * (y - cy2)) / d;
                const double l2 = 1.0 - l0 - l1;
                if (l0 < -0.02 || l1 < -0.02 || l2 < -0.02) continue;
                const double z = l0 * az + l1 * bz + l2 * cz;
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

// --------------------------------------------------------------------------
// Neighbour field: other pathgrid edge centerlines, bucketed in plan.
//
// Used to find where a rail CROSSES another corridor's centerline.  Tested
// during the march (see grow_half_width), so every crossing it reports lies
// on ground the rail has already proven walkable and wall-free.
// --------------------------------------------------------------------------
struct Seg { double ax, ay, az, bx, by, bz; int i, j; };

struct NeighbourField {
    std::vector<Seg> segs;
    double minx = 0.0, miny = 0.0, cell = 256.0;
    std::unordered_map<long long, std::vector<int>> grid;

    void build(const double* nodes, size_t nnodes,
               const int* edges, size_t nedges, const double* node_z) {
        double gminx = 1e300, gminy = 1e300;
        for (size_t e = 0; e < nedges; ++e) {
            const int i = edges[e * 2], j = edges[e * 2 + 1];
            if (i < 0 || j < 0 || (size_t)i >= nnodes || (size_t)j >= nnodes
                || i == j) continue;
            const double ax = nodes[(size_t)i * 2], ay = nodes[(size_t)i * 2 + 1];
            const double bx = nodes[(size_t)j * 2], by = nodes[(size_t)j * 2 + 1];
            // A non-finite node makes the bucket span below non-finite and the
            // insert loop unbounded; skip the edge as the TriGrid builder does.
            if (!std::isfinite(ax) || !std::isfinite(ay) ||
                !std::isfinite(bx) || !std::isfinite(by) ||
                !std::isfinite(node_z[i]) || !std::isfinite(node_z[j]))
                continue;
            const double dx = bx - ax, dy = by - ay;
            if (std::sqrt(dx * dx + dy * dy) < 1e-6) continue;
            Seg s;
            s.ax = ax; s.ay = ay; s.bx = bx; s.by = by;
            s.az = node_z[i]; s.bz = node_z[j];
            s.i = i; s.j = j;
            segs.push_back(s);
            gminx = std::min(gminx, std::min(ax, bx));
            gminy = std::min(gminy, std::min(ay, by));
        }
        if (segs.empty()) return;
        minx = gminx; miny = gminy;
        for (size_t si = 0; si < segs.size(); ++si) {
            const Seg& s = segs[si];
            const double spanx =
                (std::max(s.ax, s.bx) - std::min(s.ax, s.bx)) / cell;
            const double spany =
                (std::max(s.ay, s.by) - std::min(s.ay, s.by)) / cell;
            if (spanx > (double)kMaxBuckets || spany > (double)kMaxBuckets ||
                spanx * spany > (double)kMaxBuckets)
                throw std::invalid_argument("pathgrid edge span too large");
            const long long gx0 = (long long)std::floor(
                (std::min(s.ax, s.bx) - minx) / cell);
            const long long gx1 = (long long)std::floor(
                (std::max(s.ax, s.bx) - minx) / cell);
            const long long gy0 = (long long)std::floor(
                (std::min(s.ay, s.by) - miny) / cell);
            const long long gy1 = (long long)std::floor(
                (std::max(s.ay, s.by) - miny) / cell);
            for (long long gx = gx0 - 1; gx <= gx1 + 1; ++gx)
                for (long long gy = gy0 - 1; gy <= gy1 + 1; ++gy)
                    grid[(gx << 32) ^ (gy & 0xffffffffLL)].push_back((int)si);
        }
    }

    // Distance along the step (x0,y0)->(x1,y1), as a fraction in (0, 1], at
    // which it first CROSSES another edge's centerline at the rail's own level;
    // -1 when it crosses none.  ex_a/ex_b are the marching station's own nodes:
    // an edge touching either is never counted.
    double first_crossing(double x0, double y0, double x1, double y1, double z,
                          int ex_a, int ex_b, double ztol) const {
        if (segs.empty()) return -1.0;
        const long long gx = (long long)std::floor((x1 - minx) / cell);
        const long long gy = (long long)std::floor((y1 - miny) / cell);
        auto it = grid.find((gx << 32) ^ (gy & 0xffffffffLL));
        if (it == grid.end()) return -1.0;
        const double rx = x1 - x0, ry = y1 - y0;
        double best = -1.0;
        for (int si : it->second) {
            const Seg& s = segs[(size_t)si];
            if (s.i == ex_a || s.j == ex_a || s.i == ex_b || s.j == ex_b)
                continue;
            const double sx = s.bx - s.ax, sy = s.by - s.ay;
            const double den = rx * sy - ry * sx;
            if (std::fabs(den) < 1e-12) continue;
            const double qx = s.ax - x0, qy = s.ay - y0;
            const double t = (qx * sy - qy * sx) / den;
            const double u = (qx * ry - qy * rx) / den;
            if (t <= 0.0 || t > 1.0 || u < 0.0 || u > 1.0) continue;
            if (std::fabs(s.az + (s.bz - s.az) * u - z) > ztol) continue;
            if (best < 0.0 || t < best) best = t;
        }
        return best;
    }
};

// A cell's LAND heights (33x33, row = y, 128u spacing), interpolated on the
// diagonal world._land_tris cuts.  MUST mirror world.LandField.z.
struct Land {
    const double* g = nullptr;
    double ox = 0.0, oy = 0.0;
    // True inside this cell's own 4096u LAND square.
    bool covers(double x, double y) const {
        return x >= ox && x <= ox + 4096.0 && y >= oy && y <= oy + 4096.0;
    }
    double z(double x, double y) const {
        const double fx = std::min(32.0, std::max(0.0, (x - ox) / 128.0));
        const double fy = std::min(32.0, std::max(0.0, (y - oy) / 128.0));
        const int ix = std::min(31, (int)fx), iy = std::min(31, (int)fy);
        const double tx = fx - ix, ty = fy - iy;
        const double z00 = g[iy * 33 + ix], z10 = g[iy * 33 + ix + 1];
        const double z01 = g[(iy + 1) * 33 + ix];
        const double z11 = g[(iy + 1) * 33 + ix + 1];
        if (tx >= ty) return z00 + (z10 - z00) * tx + (z11 - z10) * ty;
        return z00 + (z11 - z01) * tx + (z01 - z00) * ty;
    }
};

// Tunables mirrored from params.py, passed in so the two can never drift.
struct Params {
    double step, min_half, half_width, slab_half_w, slab_depth;
    double slab_z_bottom, agent_height, max_climb, ztol, cap;
    int bisect, land_from;
};

// --------------------------------------------------------------------------
// One outward march (mirrors grow_half_width).
// --------------------------------------------------------------------------
double grow_half_width(const TriGrid& wall, const TriGrid* walk,
                       const NeighbourField& field, const Land* land,
                       double cx, double cy, double floor_z,
                       double dirx, double diry, double tanx, double tany,
                       int ex_a, int ex_b, double lo, const Params& P) {

    // A terrain-standing rail keeps its height ABOVE THE LAND, not its height.
    const double land_c = land ? land->z(cx, cy) : 0.0;
    auto level = [&](double at) {
        return land ? floor_z + land->z(cx + dirx * at, cy + diry * at) - land_c
                    : floor_z;
    };

    double grown = 0.0, d = 0.0, limit = -1.0, built = 0.0, beyond = 0.0;
    for (;;) {
        const double nd_step = P.step;
        const double prev = d;
        d += nd_step;
        // (a) WALL -- sweep the interval [prev, d] so a wall between two
        // samples cannot be stepped over.
        const double mid = 0.5 * (prev + d);
        const double sweep = 0.5 * nd_step + P.slab_depth;
        const double z_lo = level(mid) + P.slab_z_bottom;
        const double z_hi = level(mid) + P.agent_height;
        if (wall_hit(wall, cx + dirx * mid, cy + diry * mid, dirx, diry,
                     tanx, tany, z_lo, z_hi, sweep, P.slab_half_w)) {
            // Bisect so the rail ends AT the wall, not a step short of it.
            double lo_d = prev, hi_d = d;
            for (int b = 0; b < P.bisect; ++b) {
                const double md = 0.5 * (lo_d + hi_d);
                const double mm = 0.5 * (lo_d + md);
                if (wall_hit(wall, cx + dirx * mm, cy + diry * mm, dirx, diry,
                             tanx, tany, z_lo, z_hi,
                             0.5 * (md - lo_d) + P.slab_depth, P.slab_half_w))
                    hi_d = md;
                else
                    lo_d = md;
            }
            grown = std::max(grown, lo_d);
            break;
        }
        // (b) another corridor's CENTERLINE -- the rail ends where it crosses
        // one (never short of the soft floor).  Ground past that line is the
        // other corridor's to cover, and it grows from there by these rules.
        const double t = field.first_crossing(
            cx + dirx * prev, cy + diry * prev, cx + dirx * d, cy + diry * d,
            level(d), ex_a, ex_b, P.ztol);
        if (t >= 0.0 && limit < 0.0)
            limit = std::max(prev + t * nd_step, std::max(P.cap, lo));
        const bool last = limit >= 0.0 && limit <= d;
        if (last) d = limit;
        // (c) past the cell's own LAND the neighbour's terrain carries on: a
        // terrain rail overshoots the cell plane by one cap, so the sheet is
        // CUT on the plane by the cell clip instead of ending short of it.
        if (land && !land->covers(cx + dirx * d, cy + diry * d)) {
            beyond += nd_step;
            grown = d;
            if (last || beyond >= P.cap) break;
            continue;
        }
        // (d) walkable floor edge -- binds only BEYOND the soft floor `lo`.
        const double ref = level(d);
        double s = ref;
        int tri = -1;
        const bool ground = walk && walk_sample(
            *walk, cx + dirx * d, cy + diry * d, ref, &s, &tri);
        if (walk && d > lo && (!ground || std::fabs(s - ref) > P.max_climb))
            break;
        grown = d;
        // (e) the BUILT-floor budget: terrain is free, authored floor is not.
        if (!(ground && tri >= P.land_from)) built += nd_step;
        if (last || built >= std::max(P.cap, lo)) break;
    }
    return grown;
}

// --------------------------------------------------------------------------
// Python entry point
// --------------------------------------------------------------------------
PyArrayObject* as_f64(PyObject* o) {
    return (PyArrayObject*)PyArray_FROMANY(
        o, NPY_DOUBLE, 0, 0, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
}

// grow_strips(blocking, walkable, stations, params, nodes, edges, node_z,
//             land, on_land)
//
// `stations` is an (N, 9) float64 array, one row per march to perform:
//   cx, cy, cz, dirx, diry, tanx, tany, lo, edge_index
// edge_index indexes `edges`, so the march can exclude its OWN edge from the
// crossing test; a node disc passes -1 - node_index and excludes that node.
//
// nodes (M,2) float64, edges (E,2) int32 and node_z (M,) float64 build the
// neighbour field.  Passing None for any of them disables the crossing stop.
//
// land (33,33) float64 is the cell's LAND grid (origin in params land_ox /
// land_oy) and on_land (N,) float64 flags the stations that follow it.
//
// Returns a float64 array of N grown half-widths.
PyObject* py_grow_strips(PyObject*, PyObject* args) {
    PyObject *o_block, *o_walk, *o_st, *o_par;
    PyObject *o_nodes = Py_None, *o_edges = Py_None, *o_nz = Py_None;
    PyObject *o_land = Py_None, *o_onland = Py_None;
    if (!PyArg_ParseTuple(args, "OOOO|OOOOO", &o_block, &o_walk, &o_st, &o_par,
                          &o_nodes, &o_edges, &o_nz, &o_land, &o_onland))
        return nullptr;

    PyArrayObject* a_block = as_f64(o_block);
    PyArrayObject* a_st = as_f64(o_st);
    PyArrayObject* a_walk = (o_walk == Py_None) ? nullptr : as_f64(o_walk);
    if (!a_block || !a_st || (o_walk != Py_None && !a_walk)) {
        Py_XDECREF(a_block); Py_XDECREF(a_st); Py_XDECREF(a_walk);
        return nullptr;
    }
    const bool want_field =
        (o_nodes != Py_None && o_edges != Py_None && o_nz != Py_None);
    PyArrayObject* a_nodes = want_field ? as_f64(o_nodes) : nullptr;
    PyArrayObject* a_nz = want_field ? as_f64(o_nz) : nullptr;
    PyArrayObject* a_edges = want_field ? (PyArrayObject*)PyArray_FROMANY(
        o_edges, NPY_INT32, 0, 0,
        NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED) : nullptr;
    const bool want_land = (o_land != Py_None && o_onland != Py_None);
    PyArrayObject* a_land = want_land ? as_f64(o_land) : nullptr;
    PyArrayObject* a_onland = want_land ? as_f64(o_onland) : nullptr;
    const bool bad_land = want_land && (!a_land || !a_onland ||
        PyArray_SIZE(a_land) != 33 * 33 ||
        PyArray_SIZE(a_onland) != PyArray_SIZE(a_st) / 9);
    if (bad_land && !PyErr_Occurred())
        PyErr_SetString(PyExc_ValueError, "land must be 33x33, on_land (N,)");
    if (bad_land || (want_field && (!a_nodes || !a_nz || !a_edges))) {
        Py_XDECREF(a_block); Py_XDECREF(a_st); Py_XDECREF(a_walk);
        Py_XDECREF(a_nodes); Py_XDECREF(a_nz); Py_XDECREF(a_edges);
        Py_XDECREF(a_land); Py_XDECREF(a_onland);
        return nullptr;
    }

    Params P;
    auto getd = [&](const char* k, double dflt) {
        PyObject* v = PyDict_GetItemString(o_par, k);
        return v ? PyFloat_AsDouble(v) : dflt;
    };
    P.step = getd("step", 8.0);
    P.min_half = getd("min_half", 16.0);
    P.half_width = getd("half_width", 40.0);
    P.slab_half_w = getd("slab_half_w", 20.0);
    P.slab_depth = getd("slab_depth", 6.0);
    P.slab_z_bottom = getd("slab_z_bottom", 12.0);
    P.agent_height = getd("agent_height", 128.0);
    P.max_climb = getd("max_climb", 34.0);
    P.ztol = getd("ztol", 96.0);
    P.cap = getd("cap", 160.0);
    P.land_from = (int)getd("land_from", 2147483647.0);

    P.bisect = (int)getd("bisect", 4.0);
    Land land;
    land.g = a_land ? (const double*)PyArray_DATA(a_land) : nullptr;
    land.ox = getd("land_ox", 0.0);
    land.oy = getd("land_oy", 0.0);
    const double* onlandp =
        a_onland ? (const double*)PyArray_DATA(a_onland) : nullptr;

    const size_t nblock = (size_t)(PyArray_SIZE(a_block) / 9);
    const size_t nwalk = a_walk ? (size_t)(PyArray_SIZE(a_walk) / 9) : 0;
    const size_t nst = (size_t)(PyArray_SIZE(a_st) / 9);

    const double* blockp = (const double*)PyArray_DATA(a_block);
    const double* walkp = a_walk ? (const double*)PyArray_DATA(a_walk) : nullptr;
    const double* stp = (const double*)PyArray_DATA(a_st);
    const size_t nnode = a_nodes ? (size_t)(PyArray_SIZE(a_nodes) / 2) : 0;
    const size_t nedge = a_edges ? (size_t)(PyArray_SIZE(a_edges) / 2) : 0;
    const double* nodesp =
        a_nodes ? (const double*)PyArray_DATA(a_nodes) : nullptr;
    const int* edgesp = a_edges ? (const int*)PyArray_DATA(a_edges) : nullptr;
    const double* nzp = a_nz ? (const double*)PyArray_DATA(a_nz) : nullptr;

    npy_intp dims[1] = {(npy_intp)nst};
    PyArrayObject* out = (PyArrayObject*)PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    if (!out) {
        Py_DECREF(a_block); Py_DECREF(a_st); Py_XDECREF(a_walk);
        Py_XDECREF(a_nodes); Py_XDECREF(a_nz); Py_XDECREF(a_edges);
        Py_XDECREF(a_land); Py_XDECREF(a_onland);
        return nullptr;
    }
    double* outp = (double*)PyArray_DATA(out);

    // Index building and the march are pure C on copied data -- no Python
    // objects are touched, so the GIL can be dropped for the whole run.
    //
    // EVERYTHING between the GIL macros MUST be inside the try. A C++ exception
    // that escapes here cannot become a Python error (the GIL is not held) and
    // there is no handler further out, so it reaches std::terminate() and the
    // process dies by abort() -- in a pool worker that surfaces in the parent as
    // an opaque BrokenProcessPool with no traceback, and under pythonw.exe the
    // worker's stderr goes nowhere either. That is exactly how Nehrim's cell
    // 011E4FEC (a garbage exported PosY of 8.9e17) killed the import.
    // So: stash the message, reacquire the GIL, then raise normally.
    std::string err;
    Py_BEGIN_ALLOW_THREADS
    try {
        TriGrid wall, walk;
        wall.build(blockp, nblock, 128.0);
        if (walkp) walk.build(walkp, nwalk, 128.0);

        NeighbourField field;
        if (nedge)
            field.build(nodesp, nnode, edgesp, nedge, nzp);

        for (size_t s = 0; s < nst; ++s) {
            const double* r = &stp[s * 9];
            // Exclude the station's OWN centerline(s) from the crossing test.
            // Column 9 is >= 0 for an edge station (an index into `edges`), and
            // NEGATIVE for a node disc, encoding -1 - node_index: a disc's rays
            // start where every edge at that node meets.
            int ex_a = -1, ex_b = -1;
            const long long ei = (long long)r[8];
            if (ei >= 0 && (size_t)ei < nedge) {
                ex_a = edgesp[(size_t)ei * 2];
                ex_b = edgesp[(size_t)ei * 2 + 1];
            } else if (ei < 0) {
                ex_a = ex_b = (int)(-1 - ei);
            }
            outp[s] = grow_half_width(wall, walkp ? &walk : nullptr, field,
                                      (onlandp && onlandp[s] > 0.0) ? &land
                                                                    : nullptr,
                                      r[0], r[1], r[2], r[3], r[4], r[5], r[6],
                                      ex_a, ex_b, r[7], P);
        }
    } catch (const std::bad_alloc&) {
        err = "out of memory building the navmesh triangle index";
    } catch (const std::exception& e) {
        err = e.what();
    } catch (...) {
        err = "unknown error in grow_strips";
    }
    Py_END_ALLOW_THREADS

    Py_DECREF(a_block); Py_DECREF(a_st); Py_XDECREF(a_walk);
    Py_XDECREF(a_nodes); Py_XDECREF(a_nz); Py_XDECREF(a_edges);
    Py_XDECREF(a_land); Py_XDECREF(a_onland);
    if (!err.empty()) {
        Py_DECREF(out);
        PyErr_SetString(PyExc_ValueError, err.c_str());
        return nullptr;
    }
    return (PyObject*)out;
}

// --------------------------------------------------------------------------
// Surface levels at a batch of points (corridor_union._levels_at).
//
// WHY THIS IS ALSO NATIVE
// -----------------------
// With the width-grow moved to C++, this became the new dominant cost: 29.3s
// of a 31.9s Wendir02 build, 4.5ms per call over 6,491 calls.  The shape is the
// same -- for each output vertex it scans EVERY strip (~1,900 of them), and a
// grown strip's test is a full point-in-polygon plus a min-distance over all
// its outline edges.  That is O(V x S x E) tiny float ops in Python.
//
// Strips are flattened once into arrays and bucketed by their XY bounds, so a
// point only tests strips whose bounding box actually contains it.
// --------------------------------------------------------------------------
struct Strip {
    // Centerline (a -> b), carrying the strip's own slope.
    double ax, ay, az, bx, by, bz;
    double half;                 // admission radius for a rectangle strip
    int poly_off, poly_n;        // outline vertices in `poly` (0 = rectangle)
    int prof_off, prof_n;        // height profile points (0 = straight chord)
    bool on_land = false;        // height follows the LAND sideways
    double x0, x1, y0, y1;       // XY bounds, for bucket rejection
};

inline bool point_in_poly(const double* p, int n, double px, double py) {
    bool inside = false;
    for (int i = 0; i < n; ++i) {
        const int j = (i + 1) % n;
        const double x1 = p[i * 2], y1 = p[i * 2 + 1];
        const double x2 = p[j * 2], y2 = p[j * 2 + 1];
        if ((y1 > py) != (y2 > py)) {
            double dy = y2 - y1;
            if (dy == 0.0) dy = 1e-12;
            const double xin = x1 + (py - y1) * (x2 - x1) / dy;
            if (px < xin) inside = !inside;
        }
    }
    return inside;
}

inline double seg_dist(double px, double py, double ax, double ay,
                       double bx, double by) {
    const double dx = bx - ax, dy = by - ay;
    const double d2 = dx * dx + dy * dy;
    double t = (d2 < 1e-9) ? 0.0
                           : ((px - ax) * dx + (py - ay) * dy) / d2;
    t = std::max(0.0, std::min(1.0, t));
    return std::hypot(px - (ax + dx * t), py - (ay + dy * t));
}

// levels_at(strips_flat, poly_flat, points, same_surface_z[, prof_flat,
//           land, land_ox, land_oy])
//   -> list of lists
//
// strips_flat: (S, 12) float64 -- ax,ay,az,bx,by,bz,half,poly_off,poly_n,
//              prof_off,prof_n,on_land.  An on_land strip's height IS the
//              LAND (33x33 `land`).
// poly_flat  : (P, 2) float64 outline vertices, indexed by poly_off/poly_n.
// points     : (N, 2) float64 query points.
// prof_flat  : (F, 3) float64 height-profile points (x, y, z), indexed by
//              prof_off/prof_n.  A strip with prof_n >= 2 takes its height
//              from the closest projection onto this polyline instead of the
//              a->b chord (the tread-following stair profile).  MUST mirror
//              corridor_union._height_on exactly, tie-breaks included.
PyObject* py_levels_at(PyObject*, PyObject* args) {
    PyObject *o_strips, *o_poly, *o_pts;
    PyObject *o_prof = nullptr, *o_land = Py_None;
    double same_z;
    Land land;
    if (!PyArg_ParseTuple(args, "OOOd|OOdd", &o_strips, &o_poly, &o_pts,
                          &same_z, &o_prof, &o_land, &land.ox, &land.oy))
        return nullptr;
    if (o_prof == Py_None) o_prof = nullptr;

    PyArrayObject* a_s = as_f64(o_strips);
    PyArrayObject* a_p = as_f64(o_poly);
    PyArrayObject* a_q = as_f64(o_pts);
    PyArrayObject* a_f = o_prof ? as_f64(o_prof) : nullptr;
    PyArrayObject* a_l = (o_land != Py_None) ? as_f64(o_land) : nullptr;
    const bool bad_land = (o_land != Py_None) &&
        (!a_l || PyArray_SIZE(a_l) != 33 * 33);
    if (bad_land && !PyErr_Occurred())
        PyErr_SetString(PyExc_ValueError, "land must be 33x33");
    if (!a_s || !a_p || !a_q || (o_prof && !a_f) || bad_land) {
        Py_XDECREF(a_s); Py_XDECREF(a_p); Py_XDECREF(a_q); Py_XDECREF(a_f);
        Py_XDECREF(a_l);
        return nullptr;
    }
    land.g = a_l ? (const double*)PyArray_DATA(a_l) : nullptr;

    const size_t ns = (size_t)(PyArray_SIZE(a_s) / 12);
    const size_t nq = (size_t)(PyArray_SIZE(a_q) / 2);
    const size_t nf = a_f ? (size_t)(PyArray_SIZE(a_f) / 3) : 0;
    const double* sp = (const double*)PyArray_DATA(a_s);
    const double* pp = (const double*)PyArray_DATA(a_p);
    const double* qp = (const double*)PyArray_DATA(a_q);
    const double* fp = a_f ? (const double*)PyArray_DATA(a_f) : nullptr;

    // Same contract as TriGrid::build: non-finite coordinates make the bucket
    // spans below non-finite, the cast to long long undefined, and the
    // push_back loop unbounded. Reject them here rather than allocating until
    // bad_alloc aborts the process.
    {
        const npy_intp checks[4] = {PyArray_SIZE(a_s), PyArray_SIZE(a_p),
                                    PyArray_SIZE(a_q),
                                    a_f ? PyArray_SIZE(a_f) : 0};
        const double* datas[4] = {sp, pp, qp, fp};
        for (int c = 0; c < 4; ++c) {
            for (npy_intp i = 0; i < checks[c]; ++i) {
                if (!std::isfinite(datas[c][i])) {
                    Py_DECREF(a_s); Py_DECREF(a_p); Py_DECREF(a_q);
                    Py_XDECREF(a_f); Py_XDECREF(a_l);
                    PyErr_SetString(PyExc_ValueError,
                                    "non-finite coordinate in levels_at input");
                    return nullptr;
                }
            }
        }
    }

    std::vector<Strip> strips(ns);
    for (size_t i = 0; i < ns; ++i) {
        const double* r = &sp[i * 12];
        Strip& S = strips[i];
        S.ax = r[0]; S.ay = r[1]; S.az = r[2];
        S.bx = r[3]; S.by = r[4]; S.bz = r[5];
        S.half = r[6];
        S.poly_off = (int)r[7];
        S.poly_n = (int)r[8];
        S.prof_off = (int)r[9];
        S.prof_n = (int)r[10];
        S.on_land = land.g && r[11] > 0.0;
        // A profile that indexes past the prof array is a caller bug; treat it
        // as "no profile" rather than reading out of bounds.
        if (S.prof_n < 2 ||
            (size_t)(S.prof_off + S.prof_n) > nf) {
            S.prof_off = 0; S.prof_n = 0;
        }
        if (S.poly_n > 0) {
            double x0 = 1e300, x1 = -1e300, y0 = 1e300, y1 = -1e300;
            for (int k = 0; k < S.poly_n; ++k) {
                const double x = pp[(size_t)(S.poly_off + k) * 2];
                const double y = pp[(size_t)(S.poly_off + k) * 2 + 1];
                x0 = std::min(x0, x); x1 = std::max(x1, x);
                y0 = std::min(y0, y); y1 = std::max(y1, y);
            }
            S.x0 = x0; S.x1 = x1; S.y0 = y0; S.y1 = y1;
        } else {
            S.x0 = std::min(S.ax, S.bx) - S.half;
            S.x1 = std::max(S.ax, S.bx) + S.half;
            S.y0 = std::min(S.ay, S.by) - S.half;
            S.y1 = std::max(S.ay, S.by) + S.half;
        }
    }

    // Bucket strips by bounds so a point tests only plausible ones.
    const double cell = 256.0;
    double gx0 = 1e300, gy0 = 1e300;
    for (const Strip& S : strips) {
        gx0 = std::min(gx0, S.x0); gy0 = std::min(gy0, S.y0);
    }
    if (!ns) { gx0 = gy0 = 0.0; }
    std::unordered_map<long long, std::vector<int>> grid;
    for (size_t i = 0; i < ns; ++i) {
        const Strip& S = strips[i];
        // A single wildly-oversized strip would otherwise insert billions of
        // buckets here (the map is sparse, so there is no dense allocation to
        // trip first -- it just grows until memory runs out). Bound the span
        // per strip; kMaxBuckets is orders of magnitude above any real ribbon.
        const double spanx = (S.x1 - S.x0) / cell;
        const double spany = (S.y1 - S.y0) / cell;
        if (spanx < 0.0 || spany < 0.0 ||
            spanx > (double)kMaxBuckets || spany > (double)kMaxBuckets ||
            spanx * spany > (double)kMaxBuckets) {
            Py_DECREF(a_s); Py_DECREF(a_p); Py_DECREF(a_q); Py_XDECREF(a_f);
            Py_XDECREF(a_l);
            PyErr_SetString(PyExc_ValueError, "strip XY extent too large");
            return nullptr;
        }
        const long long bx0 = (long long)std::floor((S.x0 - gx0) / cell);
        const long long bx1 = (long long)std::floor((S.x1 - gx0) / cell);
        const long long by0 = (long long)std::floor((S.y0 - gy0) / cell);
        const long long by1 = (long long)std::floor((S.y1 - gy0) / cell);
        for (long long bx = bx0; bx <= bx1; ++bx)
            for (long long by = by0; by <= by1; ++by)
                grid[(bx << 32) ^ (by & 0xffffffffLL)].push_back((int)i);
    }

    // Results are gathered natively then converted to Python once at the end.
    std::vector<std::vector<double>> out(nq);
    // Wrapped for the same reason as grow_strips: a throw with the GIL released
    // and no handler reaches std::terminate() and aborts the whole worker.
    std::string err;
    Py_BEGIN_ALLOW_THREADS
    try {
    std::vector<double> zs;
    for (size_t n = 0; n < nq; ++n) {
        const double px = qp[n * 2], py = qp[n * 2 + 1];
        zs.clear();
        const long long bx = (long long)std::floor((px - gx0) / cell);
        const long long by = (long long)std::floor((py - gy0) / cell);
        auto it = grid.find((bx << 32) ^ (by & 0xffffffffLL));
        if (it != grid.end()) {
            for (int si : it->second) {
                const Strip& S = strips[(size_t)si];
                if (px < S.x0 || px > S.x1 || py < S.y0 || py > S.y1) continue;
                bool hit;
                if (S.poly_n > 0) {
                    // A poly strip owns exactly its outline: inside == distance
                    // 0.  Using `half` as a radius would claim ground far
                    // outside a grown ribbon and inject phantom levels.
                    const double* poly = &pp[(size_t)S.poly_off * 2];
                    hit = point_in_poly(poly, S.poly_n, px, py);
                    if (!hit) {
                        double best = 1e300;
                        for (int k = 0; k < S.poly_n; ++k) {
                            const int k2 = (k + 1) % S.poly_n;
                            best = std::min(best, seg_dist(
                                px, py, poly[k * 2], poly[k * 2 + 1],
                                poly[k2 * 2], poly[k2 * 2 + 1]));
                        }
                        hit = best <= 1e-6;
                    }
                } else {
                    hit = seg_dist(px, py, S.ax, S.ay, S.bx, S.by)
                          <= S.half + 1e-6;
                }
                if (!hit) continue;
                if (S.prof_n >= 2) {
                    // Tread-following profile: height at the closest
                    // projection onto the polyline.  Mirror of the prof
                    // branch in corridor_union._height_on -- same iteration
                    // order, same strictly-less comparison, so the scalar
                    // Python path and this batch agree bit-for-bit.
                    const double* f = &fp[(size_t)S.prof_off * 3];
                    double best_d2 = -1.0;
                    double best_z = f[2];
                    for (int k = 0; k < S.prof_n - 1; ++k) {
                        const double qax = f[k * 3],     qay = f[k * 3 + 1];
                        const double qaz = f[k * 3 + 2];
                        const double qbx = f[k * 3 + 3], qby = f[k * 3 + 4];
                        const double qbz = f[k * 3 + 5];
                        const double dx = qbx - qax, dy = qby - qay;
                        const double d2 = dx * dx + dy * dy;
                        double t = (d2 < 1e-9) ? 0.0
                            : ((px - qax) * dx + (py - qay) * dy) / d2;
                        t = std::max(0.0, std::min(1.0, t));
                        const double cx = qax + dx * t, cy = qay + dy * t;
                        const double dd = (px - cx) * (px - cx)
                                        + (py - cy) * (py - cy);
                        if (best_d2 < 0.0 || dd < best_d2) {
                            best_d2 = dd;
                            best_z = qaz + (qbz - qaz) * t;
                        }
                    }
                    zs.push_back(best_z);
                    continue;
                }
                // Height on this strip's own slope at (px, py).
                const double dx = S.bx - S.ax, dy = S.by - S.ay;
                const double d2 = dx * dx + dy * dy;
                double t = (d2 < 1e-9) ? 0.0
                    : ((px - S.ax) * dx + (py - S.ay) * dy) / d2;
                t = std::max(0.0, std::min(1.0, t));
                if (S.on_land) {
                    zs.push_back(land.z(px, py));
                    continue;
                }
                zs.push_back(S.az + (S.bz - S.az) * t);
            }
        }
        if (zs.empty()) continue;
        std::sort(zs.begin(), zs.end());
        // Cluster: a gap wider than same_z starts a new surface.
        std::vector<double>& res = out[n];
        double acc = zs[0];
        int cnt = 1;
        double last = zs[0];
        for (size_t k = 1; k < zs.size(); ++k) {
            if (zs[k] - last <= same_z) {
                acc += zs[k]; ++cnt;
            } else {
                res.push_back(acc / cnt);
                acc = zs[k]; cnt = 1;
            }
            last = zs[k];
        }
        res.push_back(acc / cnt);
    }
    } catch (const std::bad_alloc&) {
        err = "out of memory in levels_at";
    } catch (const std::exception& e) {
        err = e.what();
    } catch (...) {
        err = "unknown error in levels_at";
    }
    Py_END_ALLOW_THREADS

    if (!err.empty()) {
        Py_DECREF(a_s); Py_DECREF(a_p); Py_DECREF(a_q); Py_XDECREF(a_f);
        Py_XDECREF(a_l);
        PyErr_SetString(PyExc_ValueError, err.c_str());
        return nullptr;
    }

    PyObject* pyout = PyList_New((Py_ssize_t)nq);
    if (pyout) {
        for (size_t n = 0; n < nq; ++n) {
            PyObject* lst = PyList_New((Py_ssize_t)out[n].size());
            if (!lst) { Py_DECREF(pyout); pyout = nullptr; break; }
            for (size_t k = 0; k < out[n].size(); ++k)
                PyList_SET_ITEM(lst, (Py_ssize_t)k,
                                PyFloat_FromDouble(out[n][k]));
            PyList_SET_ITEM(pyout, (Py_ssize_t)n, lst);
        }
    }
    Py_DECREF(a_s); Py_DECREF(a_p); Py_DECREF(a_q); Py_XDECREF(a_f);
    Py_XDECREF(a_l);
    return pyout;
}

PyMethodDef methods[] = {
    {"grow_strips", py_grow_strips, METH_VARARGS,
     "Grown half-widths for a batch of corridor march stations."},
    {"levels_at", py_levels_at, METH_VARARGS,
     "Surface heights at a batch of points, one list per point."},
    {nullptr, nullptr, 0, nullptr}
};

PyModuleDef moddef = {
    PyModuleDef_HEAD_INIT, "_navgrow_native",
    "Native Phase-2 corridor width-grow.", -1, methods,
    nullptr, nullptr, nullptr, nullptr
};

}  // namespace

PyMODINIT_FUNC PyInit__navgrow_native(void) {
    import_array();
    return PyModule_Create(&moddef);
}
