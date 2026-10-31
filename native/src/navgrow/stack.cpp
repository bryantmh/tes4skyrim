// Native union_mesh._stacked_pairs: triangle pairs overlapping in plan on the
// SAME surface, for _destack.
//
// The Python form builds a shapely polygon per triangle, an STRtree, and a
// GEOS overlay per candidate pair.  Here candidates come from a bucket grid, a
// triangle clip (clip_area.h) rejects every pair it shows by kClipMargin to
// overlap under 4 units^2 or to sit over 40 apart at the overlap's centroid,
// and GEOS judges the rest exactly as the Python does.  Kept pairs carry
// GEOS's own areas, which order the Python's sort, so the result is identical.

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

constexpr double kCell = 64.0;
constexpr double kValidMargin = 1e-7;

struct Plan {
    GeosApi* G;
    const double* v;
    const int* t;
    size_t m;
    double min_area;
    std::vector<std::array<double, 4>> box;
    std::vector<signed char> keep;
    std::vector<GEOSGeometry*> poly;

    Plan(GeosApi* g, const double* verts, const int* tris, size_t n, double ma)
        : G(g), v(verts), t(tris), m(n), min_area(ma), box(n), keep(n, 0),
          poly(n, nullptr) {}

    ~Plan() {
        for (GEOSGeometry* g : poly)
            if (g) G->destroy(G->ctx, g);
    }

    void xy(size_t i, double out[6]) const {
        for (int k = 0; k < 3; ++k) {
            const double* p = &v[3 * (size_t)t[3 * i + k]];
            out[2 * k] = p[0];
            out[2 * k + 1] = p[1];
        }
    }

    GEOSGeometry* geos_poly(size_t i) {
        if (!poly[i]) {
            double q[6];
            xy(i, q);
            poly[i] = make_polygon(G, q, 3);
        }
        return poly[i];
    }

    // A valid polygon of at least min_area (_plan_polys), or over it when
    // `strict` (corridor_union._plan_index).
    void admit(size_t i, bool strict) {
        double q[6];
        xy(i, q);
        box[i] = {std::min({q[0], q[2], q[4]}), std::max({q[0], q[2], q[4]}),
                  std::min({q[1], q[3], q[5]}), std::max({q[1], q[3], q[5]})};
        const double a = 0.5 * std::fabs(tri_area2(q));
        if (a > min_area + kValidMargin) { keep[i] = 1; return; }
        if (a < min_area - kValidMargin) return;
        double ga = 0.0;
        GEOSGeometry* g = geos_poly(i);
        keep[i] = g && G->is_valid(G->ctx, g) == 1
                  && G->area(G->ctx, g, &ga) == 1
                  && (strict ? ga > min_area : !(ga < min_area));
    }

    // union_mesh._z_at for triangle i.
    double z_at(size_t i, double x, double y) const {
        const double* a = &v[3 * (size_t)t[3 * i]];
        const double* b = &v[3 * (size_t)t[3 * i + 1]];
        const double* c = &v[3 * (size_t)t[3 * i + 2]];
        const double d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
        if (std::fabs(d) < 1e-9) return (a[2] + b[2] + c[2]) / 3.0;
        const double l0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d;
        const double l1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d;
        return l0 * a[2] + l1 * b[2] + (1.0 - l0 - l1) * c[2];
    }

    // GEOS's overlap area of i and j, and whether its centroid finds them on
    // one surface; false when GEOS fails or the overlap is not over 4.
    bool exact_pair(size_t i, size_t j, double* area) {
        GEOSGeometry* pi = geos_poly(i);
        GEOSGeometry* pj = geos_poly(j);
        if (!pi || !pj) return false;
        GeosOwned inter(G, G->intersection(G->ctx, pi, pj));
        if (!inter.g || G->area(G->ctx, inter.g, area) != 1 || *area <= 4.0)
            return false;
        GeosOwned c(G, G->centroid(G->ctx, inter.g));
        double cx, cy;
        if (!c.g || G->get_x(G->ctx, c.g, &cx) != 1
            || G->get_y(G->ctx, c.g, &cy) != 1)
            return false;
        return !(std::fabs(z_at(i, cx, cy) - z_at(j, cx, cy)) > 40.0);
    }

    // False when the clip shows the pair clearly is not stacked: its overlap
    // is under 4, or its heights at the overlap's centroid are over 40 apart.
    bool could_stack(size_t i, size_t j) const {
        double a[6], b[6], area, cx, cy;
        xy(i, a);
        xy(j, b);
        if (!tri_overlap(a, b, &area, &cx, &cy) || area < 4.0 - kClipMargin)
            return false;
        if (area <= 4.0 + kClipMargin) return true;
        return std::fabs(z_at(i, cx, cy) - z_at(j, cx, cy)) <= 40.0 + kClipMargin;
    }

    bool share_vertex(size_t i, size_t j) const {
        for (int a = 0; a < 3; ++a)
            for (int b = 0; b < 3; ++b)
                if (t[3 * i + a] == t[3 * j + b]) return true;
        return false;
    }
};

// corridor_union._overlaps_existing for candidate triangle `c` (three vertex
// indices) against the admitted mesh triangles (`P.keep`, min_area 1) that
// `cands` lists: True when c is degenerate or would stack within `zgap` on
// more than 4 units^2 of them.
bool overlaps_existing(Plan& P, const int c[3], const std::vector<int>& cands,
                       double zgap) {
    double q[6], zlo = 1e300, zhi = -1e300;
    for (int k = 0; k < 3; ++k) {
        const double* p = &P.v[3 * (size_t)c[k]];
        q[2 * k] = p[0];
        q[2 * k + 1] = p[1];
        zlo = std::min(zlo, p[2]);
        zhi = std::max(zhi, p[2]);
    }
    const double qa = 0.5 * std::fabs(tri_area2(q));
    if (qa <= 1.0 - kValidMargin) return true;
    GEOSGeometry* cg = nullptr;
    GeosOwned own(P.G, nullptr);
    if (!(qa > 1.0 + kValidMargin)) {
        double ga = 0.0;
        own.g = cg = make_polygon(P.G, q, 3);
        if (!cg || P.G->is_valid(P.G->ctx, cg) != 1
            || P.G->area(P.G->ctx, cg, &ga) != 1 || ga <= 1.0)
            return true;
    }
    const double x0 = std::min({q[0], q[2], q[4]}), x1 = std::max({q[0], q[2], q[4]});
    const double y0 = std::min({q[1], q[3], q[5]}), y1 = std::max({q[1], q[3], q[5]});
    for (int jj : cands) {
        const size_t j = (size_t)jj;
        const auto& b = P.box[j];
        if (b[0] > x1 || b[1] < x0 || b[2] > y1 || b[3] < y0) continue;
        bool shared = false;
        double tzlo = 1e300, tzhi = -1e300;
        for (int k = 0; k < 3; ++k) {
            const int vk = P.t[3 * j + k];
            shared = shared || vk == c[0] || vk == c[1] || vk == c[2];
            tzlo = std::min(tzlo, P.v[3 * (size_t)vk + 2]);
            tzhi = std::max(tzhi, P.v[3 * (size_t)vk + 2]);
        }
        if (shared || tzlo > zhi + zgap || tzhi < zlo - zgap) continue;
        double tj[6];
        P.xy(j, tj);
        const double clip = overlap_area(q, 3, tj, 3);
        if (clip > 4.0 + kClipMargin) return true;
        if (clip < 4.0 - kClipMargin) continue;
        if (!cg) own.g = cg = make_polygon(P.G, q, 3);
        GEOSGeometry* pj = P.geos_poly(j);
        if (!cg || !pj) return true;
        GeosOwned inter(P.G, P.G->intersection(P.G->ctx, cg, pj));
        double ia = 0.0;
        if (!inter.g || P.G->area(P.G->ctx, inter.g, &ia) != 1 || ia > 4.0)
            return true;
    }
    return false;
}

long long cell_of(double x) { return (long long)std::floor(x / kCell); }
long long key_of(long long gx, long long gy) {
    return (gx << 32) ^ (gy & 0xffffffffLL);
}

struct Stacked { double neg_area; int ti, tj; };

using BoxGrid = std::unordered_map<long long, std::vector<int>>;

// Admit every triangle and bucket the admitted ones by their plan boxes.
BoxGrid admit_all(Plan& P, bool strict) {
    BoxGrid grid;
    for (size_t i = 0; i < P.m; ++i) {
        P.admit(i, strict);
        if (!P.keep[i]) continue;
        const auto& b = P.box[i];
        for (long long gx = cell_of(b[0]); gx <= cell_of(b[1]); ++gx)
            for (long long gy = cell_of(b[2]); gy <= cell_of(b[3]); ++gy)
                grid[key_of(gx, gy)].push_back((int)i);
    }
    return grid;
}

// The admitted triangles bucketed under the box x0..x1, y0..y1, ascending and
// unique, those not above `after` dropped.
void box_query(const BoxGrid& grid, double x0, double x1, double y0, double y1,
               long long after, std::vector<int>& out) {
    out.clear();
    for (long long gx = cell_of(x0); gx <= cell_of(x1); ++gx)
        for (long long gy = cell_of(y0); gy <= cell_of(y1); ++gy) {
            auto it = grid.find(key_of(gx, gy));
            if (it == grid.end()) continue;
            for (int j : it->second)
                if (j > after) out.push_back(j);
        }
    std::sort(out.begin(), out.end());
    out.erase(std::unique(out.begin(), out.end()), out.end());
}

// Every stacked pair (ti < tj) with GEOS's overlap area.
std::vector<Stacked> stacked_pairs(Plan& P) {
    const BoxGrid grid = admit_all(P, false);
    std::vector<Stacked> out;
    std::vector<int> cand;
    for (size_t i = 0; i < P.m; ++i) {
        if (!P.keep[i]) continue;
        const auto& b = P.box[i];
        box_query(grid, b[0], b[1], b[2], b[3], (long long)i, cand);
        for (int jj : cand) {
            const size_t j = (size_t)jj;
            const auto& c = P.box[j];
            if (c[0] > b[1] || c[1] < b[0] || c[2] > b[3] || c[3] < b[2]
                || P.share_vertex(i, j))
                continue;
            double area = 0.0;
            if (!P.could_stack(i, j) || !P.exact_pair(i, j, &area)) continue;
            out.push_back({-area, (int)i, (int)j});
        }
    }
    return out;
}

}  // namespace

}  // namespace navgrow

using namespace navgrow;

// notch_overlaps(verts, tris, cands, zgap) -> [bool, ...]
//
// verts (N, 3) float64, tris (M, 3) and cands (K, 3) int32: for each candidate
// triangle, corridor_union._overlaps_existing against the mesh `tris`.
PyObject* py_notch_overlaps(PyObject*, PyObject* args) {
    PyObject *o_v, *o_t, *o_c;
    double zgap;
    if (!PyArg_ParseTuple(args, "OOOd", &o_v, &o_t, &o_c, &zgap)) return nullptr;
    GeosApi* G = geos();
    if (!G) return nullptr;
    BufView vv(o_v, 'd', 8), tv(o_t, 'i', 4), cv(o_c, 'i', 4);
    if (!vv.ok || !tv.ok || !cv.ok) return nullptr;
    const size_t nv = vv.count() / 3;
    const int* t = tv.data<int>();
    const int* c = cv.data<int>();
    bool bad = false;
    for (size_t i = 0; i < tv.count(); ++i) bad = bad || t[i] < 0 || (size_t)t[i] >= nv;
    for (size_t i = 0; i < cv.count(); ++i) bad = bad || c[i] < 0 || (size_t)c[i] >= nv;
    if (bad) {
        PyErr_SetString(PyExc_ValueError, "triangle index out of range");
        return nullptr;
    }
    Plan P(G, vv.data<double>(), t, tv.count() / 3, 1.0);
    const BoxGrid grid = admit_all(P, true);
    const size_t k = cv.count() / 3;
    PyObject* out = PyList_New((Py_ssize_t)k);
    if (!out) return nullptr;
    std::vector<int> near;
    for (size_t i = 0; i < k; ++i) {
        const int* ci = &c[3 * i];
        double x0 = 1e300, x1 = -1e300, y0 = 1e300, y1 = -1e300;
        for (int q = 0; q < 3; ++q) {
            const double* p = &P.v[3 * (size_t)ci[q]];
            x0 = std::min(x0, p[0]); x1 = std::max(x1, p[0]);
            y0 = std::min(y0, p[1]); y1 = std::max(y1, p[1]);
        }
        box_query(grid, x0, x1, y0, y1, -1, near);
        PyObject* b = overlaps_existing(P, ci, near, zgap) ? Py_True : Py_False;
        Py_INCREF(b);
        PyList_SET_ITEM(out, (Py_ssize_t)i, b);
    }
    return out;
}

// stacked_pairs(verts, tris, min_area) -> ([(-area, ti, tj), ...], {ti: area})
//
// verts (N, 3) float64, tris (M, 3) int32.  The pairs union_mesh._stacked_pairs
// returns (in ti, tj order), and the GEOS plan area of every triangle in them.
PyObject* py_stacked_pairs(PyObject*, PyObject* args) {
    PyObject *o_v, *o_t;
    double min_area;
    if (!PyArg_ParseTuple(args, "OOd", &o_v, &o_t, &min_area)) return nullptr;
    GeosApi* G = geos();
    if (!G) return nullptr;
    BufView vv(o_v, 'd', 8), tv(o_t, 'i', 4);
    if (!vv.ok || !tv.ok) return nullptr;
    const size_t nv = vv.count() / 3, m = tv.count() / 3;
    const int* t = tv.data<int>();
    for (size_t i = 0; i < m * 3; ++i)
        if (t[i] < 0 || (size_t)t[i] >= nv) {
            PyErr_SetString(PyExc_ValueError, "triangle index out of range");
            return nullptr;
        }
    Plan P(G, vv.data<double>(), t, m, min_area);
    const std::vector<Stacked> pairs = stacked_pairs(P);
    PyObject* lst = PyList_New((Py_ssize_t)pairs.size());
    PyObject* areas = PyDict_New();
    if (!lst || !areas) { Py_XDECREF(lst); Py_XDECREF(areas); return nullptr; }
    for (size_t k = 0; k < pairs.size(); ++k) {
        const Stacked& s = pairs[k];
        PyList_SET_ITEM(lst, (Py_ssize_t)k,
                        Py_BuildValue("(dii)", s.neg_area, s.ti, s.tj));
        for (int ti : {s.ti, s.tj}) {
            double a = 0.0;
            GEOSGeometry* g = P.geos_poly((size_t)ti);
            if (!g || G->area(G->ctx, g, &a) != 1) a = 0.0;
            PyObject* key = PyLong_FromLong(ti);
            PyObject* val = PyFloat_FromDouble(a);
            if (!key || !val || PyDict_SetItem(areas, key, val) < 0) {
                Py_XDECREF(key); Py_XDECREF(val);
                Py_DECREF(lst); Py_DECREF(areas);
                return nullptr;
            }
            Py_DECREF(key); Py_DECREF(val);
        }
    }
    return Py_BuildValue("(NN)", lst, areas);
}
