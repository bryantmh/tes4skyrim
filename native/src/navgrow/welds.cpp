// Native overlap check for union_mesh._revert_bad_welds.
//
// A provisional weld is reverted when any triangle using its vertex overlaps
// other mesh in plan by more than 2 units^2 (union_mesh._overlap_probe).  The
// Python form builds a shapely polygon for every welded triangle and an STRtree
// over them; here candidates come from a bucket grid over the same envelopes,
// areas come from a clip, and GEOS runs only for an area within
// rounding of a threshold, so the verdicts match exactly (clip_area.h).

#include "clip_area.h"
#include "geos_api.h"
#include "pybuf.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <unordered_map>
#include <vector>

namespace navgrow {

namespace {

// Bucket size of the envelope grid; any size gives the same candidates.
constexpr double kCell = 64.0;

// Margin by which a plan area must clear _welded_polys' 1e-6 floor to skip
// GEOS's validity test; area rounding at cell coordinates is ~1e-9.
constexpr double kValidMargin = 1e-7;

struct Soup {
    GeosApi* G;
    const double* v;                        // (N, 3) welded vertices
    const int* t;                           // (M, 3) welded triangles
    size_t m;
    std::vector<std::array<double, 6>> box; // x0, y0, x1, y1, z0, z1
    std::vector<GEOSGeometry*> poly;        // built on demand
    std::vector<signed char> state;         // -1 unknown, 0 no polygon, 1 ok
    std::vector<double> area;
    std::vector<signed char> probed;        // -1 unknown, else the verdict
    std::unordered_map<long long, std::vector<int>> grid;
    std::vector<unsigned> stamp;
    unsigned epoch = 0;

    Soup(GeosApi* g, const double* verts, const int* tris, size_t n)
        : G(g), v(verts), t(tris), m(n), box(n), poly(n, nullptr),
          state(n, -1), area(n, 0.0), probed(n, -1), stamp(n, 0) {
        for (size_t i = 0; i < m; ++i) {
            auto& b = box[i];
            b = {1e300, 1e300, -1e300, -1e300, 1e300, -1e300};
            for (int k = 0; k < 3; ++k) {
                const double* p = &v[3 * (size_t)t[3 * i + k]];
                b[0] = std::min(b[0], p[0]); b[2] = std::max(b[2], p[0]);
                b[1] = std::min(b[1], p[1]); b[3] = std::max(b[3], p[1]);
                b[4] = std::min(b[4], p[2]); b[5] = std::max(b[5], p[2]);
            }
            for (long long gx = cell(b[0]); gx <= cell(b[2]); ++gx)
                for (long long gy = cell(b[1]); gy <= cell(b[3]); ++gy)
                    grid[key(gx, gy)].push_back((int)i);
        }
    }

    ~Soup() {
        for (GEOSGeometry* g : poly)
            if (g) G->destroy(G->ctx, g);
    }

    static long long cell(double x) { return (long long)std::floor(x / kCell); }
    static long long key(long long gx, long long gy) {
        return (gx << 32) ^ (gy & 0xffffffffLL);
    }

    void plan_xy(size_t i, double xy[6]) const {
        for (int k = 0; k < 3; ++k) {
            const double* p = &v[3 * (size_t)t[3 * i + k]];
            xy[2 * k] = p[0];
            xy[2 * k + 1] = p[1];
        }
    }

    // Triangle i's GEOS polygon, built on first use.
    GEOSGeometry* geos_poly(size_t i) {
        if (!poly[i]) {
            double xy[6];
            plan_xy(i, xy);
            poly[i] = make_polygon(G, xy, 3);
        }
        return poly[i];
    }

    // _welded_polys' test: a valid polygon over 1e-6 in area.  Any triangle
    // whose plan area clears 1e-6 by kValidMargin is valid; GEOS decides the
    // rest.
    bool has_poly(size_t i) {
        if (state[i] >= 0) return state[i] == 1;
        double xy[6];
        plan_xy(i, xy);
        area[i] = 0.5 * std::fabs(tri_area2(xy));
        state[i] = 0;
        if (area[i] > 1e-6 + kValidMargin) {
            state[i] = 1;
        } else if (!(area[i] < 1e-6 - kValidMargin)) {
            GEOSGeometry* g = geos_poly(i);
            if (g && G->is_valid(G->ctx, g) == 1
                && G->area(G->ctx, g, &area[i]) == 1 && area[i] > 1e-6)
                state[i] = 1;
        }
        return state[i] == 1;
    }

    // Triangle i's GEOS area when the plan area is too near `thr` to trust.
    double area_near(size_t i, double thr) {
        if (std::fabs(area[i] - thr) > kClipMargin) return area[i];
        double a = area[i];
        GEOSGeometry* g = geos_poly(i);
        return g && G->area(G->ctx, g, &a) == 1 ? a : area[i];
    }

    bool overlaps_by_area(size_t i, size_t j) {
        double a[6], b[6];
        plan_xy(i, a);
        plan_xy(j, b);
        const double clip = overlap_area(a, 3, b, 3);
        if (std::fabs(clip - 2.0) > kClipMargin) return clip > 2.0;
        GEOSGeometry* pi = geos_poly(i);
        GEOSGeometry* pj = geos_poly(j);
        if (!pi || !pj || G->intersects(G->ctx, pi, pj) == 0) return false;
        GeosOwned inter(G, G->intersection(G->ctx, pi, pj));
        double got = 0.0;
        return inter.g && G->area(G->ctx, inter.g, &got) == 1 && got > 2.0;
    }

    // _overlap_probe's probe(ti).
    bool probe(size_t i) {
        if (probed[i] >= 0) return probed[i] == 1;
        probed[i] = 0;
        if (!has_poly(i) || !(area_near(i, 2.0) > 2.0)) return false;
        const auto& b = box[i];
        if (++epoch == 0) {
            std::fill(stamp.begin(), stamp.end(), 0u);
            epoch = 1;
        }
        for (long long gx = cell(b[0]); gx <= cell(b[2]); ++gx)
            for (long long gy = cell(b[1]); gy <= cell(b[3]); ++gy) {
                auto it = grid.find(key(gx, gy));
                if (it == grid.end()) continue;
                for (int jj : it->second) {
                    const size_t j = (size_t)jj;
                    if (j == i || stamp[j] == epoch) continue;
                    stamp[j] = epoch;
                    const auto& c = box[j];
                    if (c[0] > b[2] || c[2] < b[0] || c[1] > b[3] || c[3] < b[1])
                        continue;
                    if (c[4] > b[5] + 40.0 || c[5] < b[4] - 40.0) continue;
                    if (has_poly(j) && overlaps_by_area(i, j)) {
                        probed[i] = 1;
                        return true;
                    }
                }
            }
        return false;
    }
};

}  // namespace

}  // namespace navgrow

using namespace navgrow;

// bad_welds(out, welded, reps, starts, rep_tris) -> [rep, ...]
//
// out (N, 3) float64 and welded (M, 3) int32 are the welded soup.  reps are the
// provisional representatives in ascending order; rep k's triangles are
// rep_tris[starts[k]:starts[k + 1]].  Returns the reps whose triangles overlap
// other mesh, ascending.
PyObject* py_bad_welds(PyObject*, PyObject* args) {
    PyObject *o_out, *o_tris, *o_reps, *o_starts, *o_rt;
    if (!PyArg_ParseTuple(args, "OOOOO", &o_out, &o_tris, &o_reps, &o_starts,
                          &o_rt))
        return nullptr;
    GeosApi* G = geos();
    if (!G) return nullptr;
    BufView ov(o_out, 'd', 8), tv(o_tris, 'i', 4), rv(o_reps, 'i', 4);
    BufView sv(o_starts, 'i', 4), qv(o_rt, 'i', 4);
    if (!ov.ok || !tv.ok || !rv.ok || !sv.ok || !qv.ok) return nullptr;
    const size_t nv = ov.count() / 3, m = tv.count() / 3, nr = rv.count();
    const int* t = tv.data<int>();
    const int* st = sv.data<int>();
    const int* rt = qv.data<int>();
    bool bad_input = sv.count() != nr + 1;
    for (size_t i = 0; i < m * 3 && !bad_input; ++i)
        bad_input = t[i] < 0 || (size_t)t[i] >= nv;
    for (size_t k = 0; k < qv.count() && !bad_input; ++k)
        bad_input = rt[k] < 0 || (size_t)rt[k] >= m;
    for (size_t k = 0; k < nr && !bad_input; ++k)
        bad_input = st[k] < 0 || st[k] > st[k + 1]
                    || (size_t)st[k + 1] > qv.count();
    if (bad_input) {
        PyErr_SetString(PyExc_ValueError, "malformed weld soup");
        return nullptr;
    }
    Soup soup(G, ov.data<double>(), t, m);
    PyObject* out = PyList_New(0);
    if (!out) return nullptr;
    const int* reps = rv.data<int>();
    for (size_t k = 0; k < nr; ++k) {
        for (int q = st[k]; q < st[k + 1]; ++q) {
            if (!soup.probe((size_t)rt[q])) continue;
            PyObject* r = PyLong_FromLong(reps[k]);
            if (!r || PyList_Append(out, r) < 0) {
                Py_XDECREF(r);
                Py_DECREF(out);
                return nullptr;
            }
            Py_DECREF(r);
            break;
        }
    }
    return out;
}
