// Native union_cdt._hex_refine and union_cdt._flip2d.
//
// Both are pure-Python loops over a sheet's 2D triangulation: the hex lattice
// inserts interior vertices into the triangles that strictly contain them, and
// the flip pass swaps diagonals that improve the worse edge ratio.  The ports
// follow the Python statement for statement -- bucket keys through
// py_floordiv, distances through py_square / py_hypot, edges visited in sorted
// key order -- so the output matches the reference exactly.

#include "kernels.h"
#include "pybuf.h"
#include "pymath.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace navgrow {

namespace {

using Tri = std::array<int, 3>;

// Interleaved x, y per vertex.
using Pts = std::vector<double>;

// Python's sorted (a, b) edge tuples, a < b, as one ordered integer.
long long edge_key(int a, int b) {
    if (a > b) std::swap(a, b);
    return ((long long)a << 32) | (unsigned)b;
}

long long bucket_key(long long gx, long long gy) {
    return (gx << 32) ^ (gy & 0xffffffffLL);
}

// _flip_gain's ratio: longest / shortest plan edge, 1e9 when degenerate.
double ratio(const Pts& p, int x, int y, int z) {
    const double e0 = py_hypot(p[2 * x] - p[2 * y], p[2 * x + 1] - p[2 * y + 1]);
    const double e1 = py_hypot(p[2 * y] - p[2 * z], p[2 * y + 1] - p[2 * z + 1]);
    const double e2 = py_hypot(p[2 * z] - p[2 * x], p[2 * z + 1] - p[2 * x + 1]);
    const double lo = std::min(std::min(e0, e1), e2);
    const double hi = std::max(std::max(e0, e1), e2);
    return lo > 1e-9 ? hi / lo : 1e9;
}

double area2(const Pts& p, int x, int y, int z) {
    return (p[2 * y] - p[2 * x]) * (p[2 * z + 1] - p[2 * x + 1])
         - (p[2 * y + 1] - p[2 * x + 1]) * (p[2 * z] - p[2 * x]);
}

int apex_of(const Tri& t, int a, int b) {
    for (int v : t)
        if (v != a && v != b) return v;
    return -1;
}

// One _flip2d round; true when any diagonal flipped.
bool flip_round(const Pts& p, std::vector<Tri>& tris) {
    std::vector<std::pair<long long, int>> owners;
    owners.reserve(tris.size() * 3);
    for (size_t ti = 0; ti < tris.size(); ++ti)
        for (int k = 0; k < 3; ++k)
            owners.push_back({edge_key(tris[ti][k], tris[ti][(k + 1) % 3]),
                              (int)ti});
    std::sort(owners.begin(), owners.end());
    std::vector<long long> keys;
    keys.reserve(owners.size());
    for (const auto& o : owners)
        if (keys.empty() || keys.back() != o.first) keys.push_back(o.first);
    std::vector<char> done(tris.size(), 0);
    std::unordered_set<long long> new_edges;
    bool changed = false;
    for (size_t s = 0; s < owners.size();) {
        size_t e = s;
        while (e < owners.size() && owners[e].first == owners[s].first) ++e;
        const size_t n = e - s;
        const long long key = owners[s].first;
        const int ti = owners[s].second;
        const int tj = n == 2 ? owners[s + 1].second : -1;
        s = e;
        if (n != 2 || done[(size_t)ti] || done[(size_t)tj]) continue;
        const int a = (int)(key >> 32), b = (int)(key & 0xffffffffLL);
        const Tri& t1 = tris[(size_t)ti];
        const Tri& t2 = tris[(size_t)tj];
        const int c = apex_of(t1, a, b), d = apex_of(t2, a, b);
        if (c < 0 || d < 0 || c == d) continue;
        const long long ckey = edge_key(c, d);
        if (std::binary_search(keys.begin(), keys.end(), ckey)
            || new_edges.count(ckey))
            continue;
        if (std::max(ratio(p, c, d, a), ratio(p, c, d, b))
            >= std::max(ratio(p, t1[0], t1[1], t1[2]),
                        ratio(p, t2[0], t2[1], t2[2])) - 1e-9)
            continue;
        const double s_a = area2(p, c, d, a), s_b = area2(p, c, d, b);
        if (s_a * s_b >= 0 || std::fabs(s_a) <= 1e-6 || std::fabs(s_b) <= 1e-6)
            continue;
        tris[(size_t)ti] = s_b > 0 ? Tri{c, d, b} : Tri{d, c, b};
        tris[(size_t)tj] = s_a > 0 ? Tri{c, d, a} : Tri{d, c, a};
        new_edges.insert(ckey);
        done[(size_t)ti] = done[(size_t)tj] = 1;
        changed = true;
    }
    return changed;
}

void flip2d(const Pts& p, std::vector<Tri>& tris, int rounds) {
    for (int r = 0; r < rounds; ++r)
        if (!flip_round(p, tris)) break;
}

long long cell_of(double v, double cell) {
    return (long long)py_floordiv(v, cell);
}

// union_cdt._Grid: triangles and vertices bucketed at `cell`.
struct Grid {
    const Pts& p;
    const std::vector<Tri>& T;
    double cell;
    std::unordered_map<long long, std::vector<int>> tri_at, vert_at;

    void add_tri(int ti) {
        const Tri& t = T[(size_t)ti];
        double x0 = p[2 * t[0]], x1 = x0, y0 = p[2 * t[0] + 1], y1 = y0;
        for (int k = 1; k < 3; ++k) {
            x0 = std::min(x0, p[2 * t[k]]); x1 = std::max(x1, p[2 * t[k]]);
            y0 = std::min(y0, p[2 * t[k] + 1]);
            y1 = std::max(y1, p[2 * t[k] + 1]);
        }
        const long long gy0 = cell_of(y0, cell), gy1 = cell_of(y1, cell);
        for (long long gx = cell_of(x0, cell); gx <= cell_of(x1, cell); ++gx)
            for (long long gy = gy0; gy <= gy1; ++gy)
                tri_at[bucket_key(gx, gy)].push_back(ti);
    }

    void add_vert(int i) {
        vert_at[bucket_key(cell_of(p[2 * i], cell), cell_of(p[2 * i + 1], cell))]
            .push_back(i);
    }

    bool crowded(double px, double py, double min_d2) const {
        const long long gx = cell_of(px, cell), gy = cell_of(py, cell);
        for (long long dx = -1; dx <= 1; ++dx)
            for (long long dy = -1; dy <= 1; ++dy) {
                auto it = vert_at.find(bucket_key(gx + dx, gy + dy));
                if (it == vert_at.end()) continue;
                for (int i : it->second)
                    if (py_square(p[2 * i] - px) + py_square(p[2 * i + 1] - py)
                        < min_d2)
                        return true;
            }
        return false;
    }
};

// _containing_triangle: the first live triangle strictly holding (px, py).
int containing(const Pts& p, const std::vector<Tri>& T,
               const std::vector<char>& alive, const std::vector<int>& cands,
               double px, double py) {
    for (int ti : cands) {
        if (!alive[(size_t)ti]) continue;
        const Tri& t = T[(size_t)ti];
        const double ax = p[2 * t[0]], ay = p[2 * t[0] + 1];
        const double bx = p[2 * t[1]], by = p[2 * t[1] + 1];
        const double cx = p[2 * t[2]], cy = p[2 * t[2] + 1];
        const double d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
        if (std::fabs(d) < 1e-9) continue;
        const double l0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d;
        const double l1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d;
        if (l0 >= 0.05 && l1 >= 0.05 && 1.0 - l0 - l1 >= 0.05) return ti;
    }
    return -1;
}

// _hex_refine after the lattice is known: inserts into `p` and returns the
// live triangles, flipped.
std::vector<Tri> hex_refine(Pts& p, std::vector<Tri> T, const double* cand,
                            size_t ncand, double spacing,
                            const std::function<int(double, double)>& pid) {
    std::vector<char> alive(T.size(), 1);
    Grid grid{p, T, spacing, {}, {}};
    for (size_t ti = 0; ti < T.size(); ++ti) grid.add_tri((int)ti);
    std::vector<int> used;
    for (const Tri& t : T) used.insert(used.end(), t.begin(), t.end());
    std::sort(used.begin(), used.end());
    used.erase(std::unique(used.begin(), used.end()), used.end());
    for (int i : used) grid.add_vert(i);
    const double min_d2 = py_square(0.45 * spacing);
    static const std::vector<int> none;
    for (size_t k = 0; k < ncand; ++k) {
        const double px = cand[2 * k], py = cand[2 * k + 1];
        if (grid.crowded(px, py, min_d2)) continue;
        auto it = grid.tri_at.find(
            bucket_key(cell_of(px, spacing), cell_of(py, spacing)));
        const int hit = containing(p, T, alive,
                                   it == grid.tri_at.end() ? none : it->second,
                                   px, py);
        if (hit < 0) continue;
        const int pi = pid(px, py);
        grid.add_vert(pi);
        const Tri t = T[(size_t)hit];
        alive[(size_t)hit] = 0;
        for (const Tri& nt : {Tri{t[0], t[1], pi}, Tri{t[1], t[2], pi},
                              Tri{t[2], t[0], pi}}) {
            T.push_back(nt);
            alive.push_back(1);
            grid.add_tri((int)T.size() - 1);
        }
    }
    std::vector<Tri> live;
    for (size_t ti = 0; ti < T.size(); ++ti)
        if (alive[ti]) live.push_back(T[ti]);
    flip2d(p, live, 4);
    return live;
}

// Python (N, 2) float64 points and (M, 3) int32 triangles; false with an error
// set when either is malformed.
bool read_mesh(PyObject* o_pts, PyObject* o_tris, Pts& p,
               std::vector<Tri>& tris) {
    BufView pv(o_pts, 'd', 8), tv(o_tris, 'i', 4);
    if (!pv.ok || !tv.ok) return false;
    const size_t nv = pv.count() / 2;
    p.assign(pv.data<double>(), pv.data<double>() + nv * 2);
    const int* t = tv.data<int>();
    tris.resize(tv.count() / 3);
    for (size_t i = 0; i < tris.size(); ++i)
        for (int k = 0; k < 3; ++k) {
            const int v = t[i * 3 + k];
            if (v < 0 || (size_t)v >= nv) {
                PyErr_SetString(PyExc_ValueError, "triangle index out of range");
                return false;
            }
            tris[i][k] = v;
        }
    return true;
}

PyObject* tri_list(const std::vector<Tri>& tris) {
    PyObject* out = PyList_New((Py_ssize_t)tris.size());
    if (!out) return nullptr;
    for (size_t i = 0; i < tris.size(); ++i) {
        PyObject* t = Py_BuildValue("(iii)", tris[i][0], tris[i][1], tris[i][2]);
        if (!t) { Py_DECREF(out); return nullptr; }
        PyList_SET_ITEM(out, (Py_ssize_t)i, t);
    }
    return out;
}

}  // namespace

void flip2d_mesh(const std::vector<double>& xy, std::vector<Tri3>& tris,
                 int rounds) {
    flip2d(xy, tris, rounds);
}

std::vector<Tri3> hex_refine_mesh(std::vector<double>& xy, std::vector<Tri3> tris,
                                  const std::vector<double>& cand, double spacing,
                                  const std::function<int(double, double)>& pid) {
    return hex_refine(xy, std::move(tris), cand.data(), cand.size() / 2, spacing,
                      pid);
}

}  // namespace navgrow

using namespace navgrow;

// flip2d(pts, tris, rounds) -> [(a, b, c), ...]   (union_cdt._flip2d)
PyObject* py_flip2d(PyObject*, PyObject* args) {
    PyObject *o_pts, *o_tris;
    int rounds;
    if (!PyArg_ParseTuple(args, "OOi", &o_pts, &o_tris, &rounds)) return nullptr;
    Pts p;
    std::vector<Tri> tris;
    if (!read_mesh(o_pts, o_tris, p, tris)) return nullptr;
    flip2d(p, tris, rounds);
    return tri_list(tris);
}

// hex_refine(pts, tris, cand, spacing) -> ([(x, y), ...], [(a, b, c), ...])
//
// The lattice points inserted, in order (they take indices len(pts), ...),
// and the refined, flipped triangles (union_cdt._hex_refine after
// _lattice_points).
PyObject* py_hex_refine(PyObject*, PyObject* args) {
    PyObject *o_pts, *o_tris, *o_cand;
    double spacing;
    if (!PyArg_ParseTuple(args, "OOOd", &o_pts, &o_tris, &o_cand, &spacing))
        return nullptr;
    Pts p;
    std::vector<Tri> tris;
    if (!read_mesh(o_pts, o_tris, p, tris)) return nullptr;
    BufView cv(o_cand, 'd', 8);
    if (!cv.ok) return nullptr;
    const size_t base = p.size() / 2;
    const std::vector<Tri> out = hex_refine(
        p, std::move(tris), cv.data<double>(), cv.count() / 2, spacing,
        [&p](double x, double y) {
            p.push_back(x);
            p.push_back(y);
            return (int)(p.size() / 2) - 1;
        });
    const size_t nnew = p.size() / 2 - base;
    PyObject* added = PyList_New((Py_ssize_t)nnew);
    if (!added) return nullptr;
    for (size_t k = 0; k < nnew; ++k) {
        PyObject* xy = Py_BuildValue("(dd)", p[2 * (base + k)],
                                     p[2 * (base + k) + 1]);
        if (!xy) { Py_DECREF(added); return nullptr; }
        PyList_SET_ITEM(added, (Py_ssize_t)k, xy);
    }
    PyObject* tl = tri_list(out);
    if (!tl) { Py_DECREF(added); return nullptr; }
    return Py_BuildValue("(NN)", added, tl);
}
