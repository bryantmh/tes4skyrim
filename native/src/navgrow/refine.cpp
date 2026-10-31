// Native union_cdt._refine_land / _refine_steep (conforming longest-edge
// bisection of a sheet's 2D triangulation) and batched LandField.z.
//
// Both refiners follow the Python statement for statement: squared edge
// lengths through py_square, seed buckets through py_floordiv, triangles
// visited in list order, midpoints appended in the order the Python appends
// them, and _fan_split's ring order.  The output matches exactly.

#include "kernels.h"
#include "pybuf.h"
#include "pymath.h"
#include "strip.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <map>
#include <set>
#include <unordered_map>
#include <utility>
#include <vector>

namespace navgrow {

namespace {

using Tri = std::array<int, 3>;
using Edge = std::pair<int, int>;

Edge edge_of(int a, int b) { return a < b ? Edge{a, b} : Edge{b, a}; }

struct Mesh {
    std::vector<double> v;   // x, y per vertex
    std::vector<Tri> t;

    // _longest_edge: (squared plan length, a, b), the first longest wins.
    double longest(const Tri& tri, int* a, int* b) const {
        double best = -1.0;
        for (int k = 0; k < 3; ++k) {
            const int p = tri[k], q = tri[(k + 1) % 3];
            const double d2 = py_square(v[2 * p] - v[2 * q])
                            + py_square(v[2 * p + 1] - v[2 * q + 1]);
            if (best < 0.0 || d2 > best) { best = d2; *a = p; *b = q; }
        }
        return best;
    }

    int midpoint(int a, int b) {
        const int id = (int)(v.size() / 2);
        const double mx = 0.5 * (v[2 * a] + v[2 * b]);
        const double my = 0.5 * (v[2 * a + 1] + v[2 * b + 1]);
        v.push_back(mx);
        v.push_back(my);
        return id;
    }

    // _fan_split of every triangle by `split` (edge -> midpoint).
    void fan_split(const std::map<Edge, int>& split, bool from_midpoint) {
        std::vector<Tri> out;
        out.reserve(t.size() * 2);
        for (const Tri& tri : t) {
            std::vector<int> ring;
            int first_mid = -1;
            for (int k = 0; k < 3; ++k) {
                const int a = tri[k], b = tri[(k + 1) % 3];
                ring.push_back(a);
                auto it = split.find(edge_of(a, b));
                if (it != split.end()) {
                    if (first_mid < 0) first_mid = (int)ring.size();
                    ring.push_back(it->second);
                }
            }
            if (ring.size() == 3) { out.push_back(tri); continue; }
            if (from_midpoint)
                std::rotate(ring.begin(), ring.begin() + first_mid, ring.end());
            for (size_t i = 1; i + 1 < ring.size(); ++i) {
                const Tri f{ring[0], ring[i], ring[i + 1]};
                if (f[0] != f[1] && f[1] != f[2] && f[0] != f[2]) out.push_back(f);
            }
        }
        t.swap(out);
    }
};

// _cuts_relief: the LAND leaves an edge's chord by more than `tol`.
bool cuts_relief(const Mesh& M, const Tri& tri, const Land& land, double tol) {
    for (int k = 0; k < 3; ++k) {
        const int a = tri[k], b = tri[(k + 1) % 3];
        const double ax = M.v[2 * a], ay = M.v[2 * a + 1];
        const double bx = M.v[2 * b], by = M.v[2 * b + 1];
        const double chord = 0.5 * (land.z(ax, ay) + land.z(bx, by));
        if (std::fabs(land.z(0.5 * (ax + bx), 0.5 * (ay + by)) - chord) > tol)
            return true;
    }
    return false;
}

// _has_plan_area: the corners are not collinear in plan.
bool has_plan_area(const Mesh& M, const Tri& tri) {
    const double ax = M.v[2 * tri[0]], ay = M.v[2 * tri[0] + 1];
    const double bx = M.v[2 * tri[1]], by = M.v[2 * tri[1] + 1];
    const double cx = M.v[2 * tri[2]], cy = M.v[2 * tri[2] + 1];
    return std::fabs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) >= 2.0;
}

struct LandRefine { double max_e2, min_e2, tol; };

void refine_land(Mesh& M, const Land& land, const LandRefine& R,
                 const std::set<Edge>& protect) {
    for (int round = 0; round < 6; ++round) {
        std::map<Edge, int> split;
        for (const Tri& tri : M.t) {
            int a, b;
            const double d2 = M.longest(tri, &a, &b);
            const Edge key = edge_of(a, b);
            if (protect.count(key) || split.count(key) || d2 <= R.min_e2
                || !has_plan_area(M, tri))
                continue;
            if (d2 <= R.max_e2 && !cuts_relief(M, tri, land, R.tol)) continue;
            split[key] = M.midpoint(a, b);
        }
        if (split.empty()) break;
        M.fan_split(split, true);
    }
}

// _seed_grid + _carries_seed.
struct Seeds {
    double cell;
    std::unordered_map<long long, std::vector<std::pair<double, double>>> at;

    static long long key(long long gx, long long gy) {
        return (gx << 32) ^ (gy & 0xffffffffLL);
    }
    long long cell_of(double x) const { return (long long)py_floordiv(x, cell); }

    bool carried(const Mesh& M, const Tri& tri) const {
        const double ax = M.v[2 * tri[0]], ay = M.v[2 * tri[0] + 1];
        const double bx = M.v[2 * tri[1]], by = M.v[2 * tri[1] + 1];
        const double cx = M.v[2 * tri[2]], cy = M.v[2 * tri[2] + 1];
        const double d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
        if (std::fabs(d) < 1e-9) return false;
        const long long gx1 = cell_of(std::max({ax, bx, cx}));
        const long long gy0 = cell_of(std::min({ay, by, cy}));
        const long long gy1 = cell_of(std::max({ay, by, cy}));
        for (long long gx = cell_of(std::min({ax, bx, cx})); gx <= gx1; ++gx)
            for (long long gy = gy0; gy <= gy1; ++gy) {
                auto it = at.find(key(gx, gy));
                if (it == at.end()) continue;
                for (const auto& p : it->second) {
                    const double l0 = ((by - cy) * (p.first - cx)
                                       + (cx - bx) * (p.second - cy)) / d;
                    const double l1 = ((cy - ay) * (p.first - cx)
                                       + (ax - cx) * (p.second - cy)) / d;
                    if (l0 >= -0.02 && l1 >= -0.02 && 1.0 - l0 - l1 >= -0.02)
                        return true;
                }
            }
        return false;
    }
};

void refine_steep(Mesh& M, const Seeds& S, double max_e2,
                  const std::set<Edge>& protect) {
    for (int round = 0; round < 6; ++round) {
        std::map<Edge, int> split;
        for (const Tri& tri : M.t) {
            if (!S.carried(M, tri)) continue;
            int a, b;
            const double d2 = M.longest(tri, &a, &b);
            const Edge key = edge_of(a, b);
            if (d2 <= max_e2 || protect.count(key) || split.count(key)) continue;
            split[key] = M.midpoint(a, b);
        }
        if (split.empty()) break;
        M.fan_split(split, false);
    }
}

}  // namespace

void refine_land_mesh(std::vector<double>& xy, std::vector<Tri3>& tris,
                      const Land& land, double max_e2, double min_e2, double tol,
                      const EdgeSet& protect) {
    Mesh M{std::move(xy), std::move(tris)};
    refine_land(M, land, LandRefine{max_e2, min_e2, tol}, protect);
    xy = std::move(M.v);
    tris = std::move(M.t);
}

void refine_steep_mesh(std::vector<double>& xy, std::vector<Tri3>& tris,
                       const std::vector<double>& seeds, double cell,
                       double max_e2, const EdgeSet& protect) {
    Seeds S{cell, {}};
    for (size_t i = 0; i + 1 < seeds.size(); i += 2)
        S.at[Seeds::key(S.cell_of(seeds[i]), S.cell_of(seeds[i + 1]))].push_back(
            {seeds[i], seeds[i + 1]});
    Mesh M{std::move(xy), std::move(tris)};
    refine_steep(M, S, max_e2, protect);
    xy = std::move(M.v);
    tris = std::move(M.t);
}

namespace {

bool read_mesh(PyObject* o_v, PyObject* o_t, Mesh& M) {
    BufView vv(o_v, 'd', 8), tv(o_t, 'i', 4);
    if (!vv.ok || !tv.ok) return false;
    const size_t nv = vv.count() / 2;
    M.v.assign(vv.data<double>(), vv.data<double>() + 2 * nv);
    const int* t = tv.data<int>();
    M.t.resize(tv.count() / 3);
    for (size_t i = 0; i < M.t.size(); ++i)
        for (int k = 0; k < 3; ++k) {
            if (t[3 * i + k] < 0 || (size_t)t[3 * i + k] >= nv) {
                PyErr_SetString(PyExc_ValueError, "triangle index out of range");
                return false;
            }
            M.t[i][k] = t[3 * i + k];
        }
    return true;
}

bool read_edges(PyObject* o, std::set<Edge>& out) {
    BufView ev(o, 'i', 4);
    if (!ev.ok) return false;
    const int* e = ev.data<int>();
    for (size_t i = 0; i + 1 < ev.count(); i += 2) out.insert(edge_of(e[i], e[i + 1]));
    return true;
}

// ([(x, y), ...], [(a, b, c), ...]) for a refined mesh.
PyObject* mesh_result(const Mesh& M) {
    PyObject* verts = PyList_New((Py_ssize_t)(M.v.size() / 2));
    PyObject* tris = PyList_New((Py_ssize_t)M.t.size());
    if (!verts || !tris) { Py_XDECREF(verts); Py_XDECREF(tris); return nullptr; }
    for (size_t i = 0; i < M.v.size() / 2; ++i)
        PyList_SET_ITEM(verts, (Py_ssize_t)i,
                        Py_BuildValue("(dd)", M.v[2 * i], M.v[2 * i + 1]));
    for (size_t i = 0; i < M.t.size(); ++i)
        PyList_SET_ITEM(tris, (Py_ssize_t)i,
                        Py_BuildValue("(iii)", M.t[i][0], M.t[i][1], M.t[i][2]));
    return Py_BuildValue("(NN)", verts, tris);
}

bool read_land(const BufView& gv, double ox, double oy, Land& land) {
    if (!gv.ok) return false;
    if (gv.count() != 33 * 33) {
        PyErr_SetString(PyExc_ValueError, "land must be 33x33");
        return false;
    }
    land.g = gv.data<double>();
    land.ox = ox;
    land.oy = oy;
    return true;
}

}  // namespace

std::vector<char> simplify_keep(const std::vector<double>& p, double tol) {
    const size_t n = p.size() / 2;
    std::vector<char> keep(n, 0);
    if (n > 0) keep[0] = keep[n - 1] = 1;
    std::vector<std::pair<size_t, size_t>> stack{{0, n ? n - 1 : 0}};
    while (!stack.empty()) {
        const auto [i0, i1] = stack.back();
        stack.pop_back();
        if (i1 <= i0 + 1) continue;
        const double ax = p[2 * i0], ay = p[2 * i0 + 1];
        const double dx = p[2 * i1] - ax, dy = p[2 * i1 + 1] - ay;
        const double d2 = dx * dx + dy * dy;
        double worst = -1.0;
        size_t wi = 0;
        for (size_t m = i0 + 1; m < i1; ++m) {
            const double px = p[2 * m], py = p[2 * m + 1];
            double d;
            if (d2 < 1e-12) {
                d = py_hypot(px - ax, py - ay);
            } else {
                double t = ((px - ax) * dx + (py - ay) * dy) / d2;
                t = t < 1.0 ? t : 1.0;
                t = t > 0.0 ? t : 0.0;
                d = py_hypot(px - (ax + dx * t), py - (ay + dy * t));
            }
            if (d > worst) { worst = d; wi = m; }
        }
        if (worst > tol) {
            keep[wi] = 1;
            stack.push_back({i0, wi});
            stack.push_back({wi, i1});
        }
    }
    return keep;
}

}  // namespace navgrow

using namespace navgrow;

// refine_land(verts, tris, grid, ox, oy, max_e2, min_e2, tol, protected)
//     -> (verts, tris)          (union_cdt._refine_land)
PyObject* py_refine_land(PyObject*, PyObject* args) {
    PyObject *o_v, *o_t, *o_g, *o_p;
    double ox, oy;
    LandRefine R;
    if (!PyArg_ParseTuple(args, "OOOdddddO", &o_v, &o_t, &o_g, &ox, &oy,
                          &R.max_e2, &R.min_e2, &R.tol, &o_p))
        return nullptr;
    Mesh M;
    std::set<Edge> protect;
    BufView gv(o_g, 'd', 8);
    Land land;
    if (!read_mesh(o_v, o_t, M) || !read_edges(o_p, protect)
        || !read_land(gv, ox, oy, land))
        return nullptr;
    refine_land(M, land, R, protect);
    return mesh_result(M);
}

// refine_steep(verts, tris, seeds, cell, max_e2, protected) -> (verts, tris)
//
// seeds (S, 2) float64 in _seed_grid's insertion order (union_cdt._refine_steep).
PyObject* py_refine_steep(PyObject*, PyObject* args) {
    PyObject *o_v, *o_t, *o_s, *o_p;
    double cell, max_e2;
    if (!PyArg_ParseTuple(args, "OOOddO", &o_v, &o_t, &o_s, &cell, &max_e2, &o_p))
        return nullptr;
    Mesh M;
    std::set<Edge> protect;
    BufView sv(o_s, 'd', 8);
    if (!read_mesh(o_v, o_t, M) || !read_edges(o_p, protect) || !sv.ok)
        return nullptr;
    Seeds S{cell, {}};
    const double* s = sv.data<double>();
    for (size_t i = 0; i + 1 < sv.count(); i += 2)
        S.at[Seeds::key(S.cell_of(s[i]), S.cell_of(s[i + 1]))].push_back(
            {s[i], s[i + 1]});
    refine_steep(M, S, max_e2, protect);
    return mesh_result(M);
}

// simplify(pts, tol) -> [index, ...]     (corridor._simplify)
//
// The indices Douglas-Peucker keeps from a polyline of (x, y) pairs, both
// endpoints included, ascending.  Distances are math.hypot's.
PyObject* py_simplify(PyObject*, PyObject* args) {
    PyObject* o_pts;
    double tol;
    if (!PyArg_ParseTuple(args, "Od", &o_pts, &tol)) return nullptr;
    PyObject* seq = PySequence_Fast(o_pts, "pts must be a sequence");
    if (!seq) return nullptr;
    const Py_ssize_t n = PySequence_Fast_GET_SIZE(seq);
    std::vector<double> p(2 * (size_t)n);
    for (Py_ssize_t i = 0; i < n && !PyErr_Occurred(); ++i) {
        PyObject* xy = PySequence_Fast(PySequence_Fast_GET_ITEM(seq, i),
                                       "each point must be (x, y)");
        if (!xy) break;
        if (PySequence_Fast_GET_SIZE(xy) < 2) {
            PyErr_SetString(PyExc_ValueError, "each point must be (x, y)");
        } else {
            p[2 * i] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(xy, 0));
            p[2 * i + 1] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(xy, 1));
        }
        Py_DECREF(xy);
    }
    Py_DECREF(seq);
    if (PyErr_Occurred()) return nullptr;
    const std::vector<char> keep = simplify_keep(p, tol);
    PyObject* out = PyList_New(0);
    if (!out) return nullptr;
    for (Py_ssize_t i = 0; i < n; ++i) {
        if (!keep[(size_t)i]) continue;
        PyObject* v = PyLong_FromSsize_t(i);
        if (!v || PyList_Append(out, v) < 0) {
            Py_XDECREF(v);
            Py_DECREF(out);
            return nullptr;
        }
        Py_DECREF(v);
    }
    return out;
}

// land_z(grid, ox, oy, xy) -> float64 array    (LandField.z per point)
PyObject* py_land_z(PyObject*, PyObject* args) {
    PyObject *o_g, *o_xy;
    double ox, oy;
    if (!PyArg_ParseTuple(args, "OddO", &o_g, &ox, &oy, &o_xy)) return nullptr;
    BufView gv(o_g, 'd', 8), pv(o_xy, 'd', 8);
    Land land;
    if (!read_land(gv, ox, oy, land) || !pv.ok) return nullptr;
    const size_t n = pv.count() / 2;
    PyObject* out = PyList_New((Py_ssize_t)n);
    if (!out) return nullptr;
    const double* p = pv.data<double>();
    for (size_t i = 0; i < n; ++i)
        PyList_SET_ITEM(out, (Py_ssize_t)i,
                        PyFloat_FromDouble(land.z(p[2 * i], p[2 * i + 1])));
    return out;
}
