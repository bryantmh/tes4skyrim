// Native boundary-sliver cull (corridor_clean.cull_boundary_slivers).
//
// Mirrors _Cull / _sliver_candidates / _cull_round operation for operation:
// the same bucket order (ascending triangle index, walked samples in input
// order), the same first-wins ties in cover_z, the same fuel-limited LIFO walk
// in bridges, and the Python-exact float helpers of pymath.h.  The door pins
// and the area budget are computed by the Python wrapper and passed in.

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "pybuf.h"
#include "pymath.h"

namespace navgrow {

namespace {

using Tri = std::array<int, 3>;

constexpr double kPCell = 128.0;
constexpr double kTCell = 128.0;

struct CullParams {
    double max_climb, max_ratio, max_aspect, sliver_max_area, min_tri_area;
};

// A walked-line sample: z and the direction are NaN where the reference holds None.
struct Sample { double x, y, z, ux, uy; };

inline long long cell_of(double v, double cell) {
    return (long long)py_floordiv(v, cell);
}

inline uint64_t cell_key(long long gx, long long gy) {
    return ((uint64_t)(uint32_t)gx << 32) | (uint32_t)gy;
}

inline uint64_t edge_key(int a, int b) {
    if (a > b) std::swap(a, b);
    return ((uint64_t)(uint32_t)a << 32) | (uint32_t)b;
}

using Grid = std::unordered_map<uint64_t, std::vector<int>>;

struct Mesh {
    const double* V;             // (N, 3)
    const uint8_t* inexact;      // per vertex: bit k set = coordinate k is not an exact float
    const uint8_t* pin;
    const double* seam;          // minx, miny, maxx, maxy, or nullptr
    const std::vector<Sample>& samples;
    const Grid& pgrid;           // _bucket_pathgrid: sample indices per plan cell
    CullParams P;

    const double* v(int i) const { return V + 3 * (size_t)i; }

    bool on_seam(int vi) const {
        if (!seam) return false;
        const double x = v(vi)[0], y = v(vi)[1];
        return std::fabs(x - seam[0]) <= 0.5 || std::fabs(x - seam[2]) <= 0.5
            || std::fabs(y - seam[1]) <= 0.5 || std::fabs(y - seam[3]) <= 0.5;
    }

    // _bary_z: interpolated Z of t at (x, y); false when outside.
    bool bary_z(const Tri& t, double x, double y, double& z) const {
        const double *a = v(t[0]), *b = v(t[1]), *c = v(t[2]);
        const double d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
        if (std::fabs(d) < 1e-9) return false;
        const double l0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / d;
        const double l1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / d;
        const double l2 = 1.0 - l0 - l1;
        if (l0 < -0.02 || l1 < -0.02 || l2 < -0.02) return false;
        z = l0 * a[2] + l1 * b[2] + l2 * c[2];
        return true;
    }
};

// _bucket_tris.
Grid bucket_tris(const Mesh& M, const std::vector<Tri>& tris, double cell) {
    Grid grid;
    for (int tj = 0; tj < (int)tris.size(); ++tj) {
        const Tri& t = tris[(size_t)tj];
        double x0 = M.v(t[0])[0], x1 = x0, y0 = M.v(t[0])[1], y1 = y0;
        for (int k = 1; k < 3; ++k) {
            x0 = std::min(x0, M.v(t[k])[0]);
            x1 = std::max(x1, M.v(t[k])[0]);
            y0 = std::min(y0, M.v(t[k])[1]);
            y1 = std::max(y1, M.v(t[k])[1]);
        }
        const long long gx1 = cell_of(x1, cell), gy1 = cell_of(y1, cell);
        for (long long gx = cell_of(x0, cell); gx <= gx1; ++gx)
            for (long long gy = cell_of(y0, cell); gy <= gy1; ++gy)
                grid[cell_key(gx, gy)].push_back(tj);
    }
    return grid;
}

// One round's state (_Cull).
struct Round {
    const Mesh& M;
    const std::vector<Tri>& tris;
    std::unordered_map<uint64_t, int> counts;
    std::unordered_map<uint64_t, std::vector<int>> edge_tris;
    std::vector<char> alive;
    Grid tgrid;

    Round(const Mesh& m, const std::vector<Tri>& t)
        : M(m), tris(t), alive(t.size(), 1), tgrid(bucket_tris(m, t, kTCell)) {
        for (int ti = 0; ti < (int)tris.size(); ++ti) {
            for (int k = 0; k < 3; ++k) {
                const uint64_t key = edge_key(tris[(size_t)ti][k], tris[(size_t)ti][(k + 1) % 3]);
                counts[key]++;
                edge_tris[key].push_back(ti);
            }
        }
    }

    int count(int a, int b) const {
        auto it = counts.find(edge_key(a, b));
        return it == counts.end() ? 0 : it->second;
    }

    std::vector<int> neighbours(int ti) const {
        std::vector<int> out;
        const Tri& t = tris[(size_t)ti];
        for (int k = 0; k < 3; ++k) {
            auto it = edge_tris.find(edge_key(t[k], t[(k + 1) % 3]));
            if (it == edge_tris.end()) continue;
            for (int tj : it->second)
                if (tj != ti && alive[(size_t)tj]) out.push_back(tj);
        }
        return out;
    }

    int boundary_edges(const Tri& t) const {
        int n = 0;
        for (int k = 0; k < 3; ++k) n += count(t[k], t[(k + 1) % 3]) == 1;
        return n;
    }

    bool on_seam_edge(const Tri& t) const {
        for (int k = 0; k < 3; ++k) {
            const int a = t[k], b = t[(k + 1) % 3];
            if (count(a, b) == 1 && M.on_seam(a) && M.on_seam(b)) return true;
        }
        return false;
    }

    // samples_in: walked samples inside t, as indices into M.samples.
    std::vector<int> samples_in(const Tri& t) const {
        std::vector<int> out;
        const double *a = M.v(t[0]), *b = M.v(t[1]), *c = M.v(t[2]);
        const double d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
        if (std::fabs(d) < 1e-9) return out;
        const double x0 = std::min({a[0], b[0], c[0]}), x1 = std::max({a[0], b[0], c[0]});
        const double y0 = std::min({a[1], b[1], c[1]}), y1 = std::max({a[1], b[1], c[1]});
        const long long gx1 = cell_of(x1, kPCell), gy1 = cell_of(y1, kPCell);
        for (long long gx = cell_of(x0, kPCell); gx <= gx1; ++gx) {
            for (long long gy = cell_of(y0, kPCell); gy <= gy1; ++gy) {
                auto it = M.pgrid.find(cell_key(gx, gy));
                if (it == M.pgrid.end()) continue;
                for (int si : it->second) {
                    const Sample& s = M.samples[(size_t)si];
                    const double l0 = ((b[1] - c[1]) * (s.x - c[0]) + (c[0] - b[0]) * (s.y - c[1])) / d;
                    const double l1 = ((c[1] - a[1]) * (s.x - c[0]) + (a[0] - c[0]) * (s.y - c[1])) / d;
                    if (l0 >= -0.02 && l1 >= -0.02 && (1.0 - l0 - l1) >= -0.02) out.push_back(si);
                }
            }
        }
        return out;
    }

    const std::vector<int>* bucket(double x, double y) const {
        auto it = tgrid.find(cell_key(cell_of(x, kTCell), cell_of(y, kTCell)));
        return it == tgrid.end() ? nullptr : &it->second;
    }

    bool samples_still_covered(int ti, const std::vector<int>& sis) const {
        const double tol = M.P.max_climb + 8.0;
        for (int si : sis) {
            const Sample& s = M.samples[(size_t)si];
            double zref = s.z;
            bool has_ref = !std::isnan(zref) || M.bary_z(tris[(size_t)ti], s.x, s.y, zref);
            bool ok = false;
            const std::vector<int>* b = bucket(s.x, s.y);
            for (size_t k = 0; b && !ok && k < b->size(); ++k) {
                const int tj = (*b)[k];
                double z;
                if (tj == ti || !alive[(size_t)tj] || !M.bary_z(tris[(size_t)tj], s.x, s.y, z))
                    continue;
                ok = !has_ref || std::fabs(z - zref) <= tol;
            }
            if (!ok) return false;
        }
        return true;
    }

    bool cover_z(double x, double y, double zc, double& best) const {
        bool found = false;
        const std::vector<int>* b = bucket(x, y);
        if (!b) return false;
        for (int tj : *b) {
            double z;
            if (!alive[(size_t)tj] || !M.bary_z(tris[(size_t)tj], x, y, z)) continue;
            if (std::fabs(z - zc) <= 40.0 && (!found || std::fabs(z - zc) < std::fabs(best - zc))) {
                best = z;
                found = true;
            }
        }
        return found;
    }

    double width_at(double px, double py, double pz, double ux, double uy) const {
        const double wx = -uy, wy = ux;
        double width = 4.0;
        for (double sgn : {1.0, -1.0}) {
            double zc = pz;
            for (int s = 1; s < 16; ++s) {
                double z;
                if (!cover_z(px + sgn * wx * s * 4.0, py + sgn * wy * s * 4.0, zc, z)) break;
                zc = z;
                width += 4.0;
            }
        }
        return width;
    }

    bool tight_near(double px, double py, double pz, double r) const {
        const long long gx1 = cell_of(px + r, kPCell), gy1 = cell_of(py + r, kPCell);
        for (long long gx = cell_of(px - r, kPCell); gx <= gx1; ++gx) {
            for (long long gy = cell_of(py - r, kPCell); gy <= gy1; ++gy) {
                auto it = M.pgrid.find(cell_key(gx, gy));
                if (it == M.pgrid.end()) continue;
                for (int si : it->second) {
                    const Sample& q = M.samples[(size_t)si];
                    if (std::isnan(q.ux)) continue;
                    if (py_square(q.x - px) + py_square(q.y - py) > r * r
                        || (!std::isnan(q.z) && std::fabs(q.z - pz) > 60.0))
                        continue;
                    if (width_at(q.x, q.y, std::isnan(q.z) ? pz : q.z, q.ux, q.uy) < 56.0)
                        return true;
                }
            }
        }
        return false;
    }

    bool narrows_corridor(const Tri& t) const {
        PyMixedSum sx, sy, sz;
        for (int k = 0; k < 3; ++k) {
            const double* p = M.v(t[k]);
            const uint8_t f = M.inexact[t[k]];
            sx.add(p[0], !(f & 1));
            sy.add(p[1], !(f & 2));
            sz.add(p[2], !(f & 4));
        }
        for (int k = 0; k < 3; ++k) {
            const double* p = M.v(t[k]);
            if (tight_near(p[0], p[1], p[2], 44.0)) return true;
        }
        return tight_near(sx.value() / 3.0, sy.value() / 3.0, sz.value() / 3.0, 44.0);
    }

    bool bridges(int ti) const {
        const std::vector<int> nbrs = neighbours(ti);
        if (nbrs.size() <= 1) return false;
        std::unordered_set<int> target(nbrs.begin() + 1, nbrs.end());
        std::unordered_set<int> seen{nbrs[0], ti};
        std::vector<int> queue{nbrs[0]};
        int fuel = 128;
        while (!queue.empty() && !target.empty() && fuel) {
            --fuel;
            const int cur = queue.back();
            queue.pop_back();
            for (int tj : neighbours(cur)) {
                if (!seen.insert(tj).second) continue;
                target.erase(tj);
                queue.push_back(tj);
            }
        }
        return !target.empty();
    }
};

struct Candidate {
    double neg_ratio;
    int ti;
    double area;
    std::vector<int> samples;
};

std::vector<Candidate> sliver_candidates(const Round& R) {
    std::vector<Candidate> out;
    const Mesh& M = R.M;
    for (int ti = 0; ti < (int)R.tris.size(); ++ti) {
        const Tri& t = R.tris[(size_t)ti];
        if (R.boundary_edges(t) < 2) continue;
        const double *p = M.v(t[0]), *q = M.v(t[1]), *r = M.v(t[2]);
        const double ratio = shape_badness(p[0], p[1], q[0], q[1], r[0], r[1],
                                           M.P.max_ratio, M.P.max_aspect);
        const double area = std::fabs((q[0] - p[0]) * (r[1] - p[1])
                                      - (q[1] - p[1]) * (r[0] - p[0])) * 0.5;
        if (!((ratio > 1.0 && area < M.P.sliver_max_area) || area < M.P.min_tri_area))
            continue;
        if (M.pin[t[0]] || M.pin[t[1]] || M.pin[t[2]] || R.on_seam_edge(t)) continue;
        out.push_back({-ratio, ti, area, R.samples_in(t)});
    }
    std::sort(out.begin(), out.end(), [](const Candidate& a, const Candidate& b) {
        return a.neg_ratio < b.neg_ratio || (a.neg_ratio == b.neg_ratio && a.ti < b.ti);
    });
    return out;
}

// _cull_round over `ids` (original triangle indices); true if anything went.
bool cull_round(const Mesh& M, std::vector<Tri>& tris, std::vector<int>& ids,
                double& removed, double budget) {
    Round R(M, tris);
    bool changed = false;
    for (const Candidate& c : sliver_candidates(R)) {
        if (!R.alive[(size_t)c.ti] || removed + c.area > budget) continue;
        if (!c.samples.empty() && !R.samples_still_covered(c.ti, c.samples)) continue;
        if (R.narrows_corridor(tris[(size_t)c.ti]) || R.bridges(c.ti)) continue;
        R.alive[(size_t)c.ti] = 0;
        removed += c.area;
        changed = true;
    }
    std::vector<Tri> kt;
    std::vector<int> ki;
    for (size_t i = 0; i < tris.size(); ++i) {
        if (!R.alive[i]) continue;
        kt.push_back(tris[i]);
        ki.push_back(ids[i]);
    }
    tris.swap(kt);
    ids.swap(ki);
    return changed;
}

}  // namespace

}  // namespace navgrow

using namespace navgrow;

// cull_slivers(verts, inexact, tris, pin, samples, seam, budget, params) -> [kept tri index]
//
// verts (N, 3) float64; inexact (N,) uint8, bit k set where coordinate k is a
// numpy scalar in the reference; tris (M, 3) int32; pin (N,) uint8; samples
// (K, 5) float64 rows x, y, z, ux, uy with NaN for None; seam a 4-array or
// None; params (max_climb, max_ratio, max_aspect, sliver_max_area, min_tri_area).
PyObject* py_cull_slivers(PyObject*, PyObject* args) {
    PyObject *o_v, *o_f, *o_t, *o_pin, *o_s, *o_seam;
    double budget;
    CullParams P;
    if (!PyArg_ParseTuple(args, "OOOOOOd(ddddd)", &o_v, &o_f, &o_t, &o_pin, &o_s,
                          &o_seam, &budget, &P.max_climb, &P.max_ratio,
                          &P.max_aspect, &P.sliver_max_area, &P.min_tri_area))
        return nullptr;
    BufView bv(o_v, 'd', 8), bf(o_f, 'B', 1), bt(o_t, 'i', 4), bp(o_pin, 'B', 1),
        bs(o_s, 'd', 8), bseam(o_seam, 'd', 8);
    if (!bv.ok || !bf.ok || !bt.ok || !bp.ok || !bs.ok || !bseam.ok) return nullptr;
    const size_t nv = bv.count() / 3;
    if (bf.count() != nv || bp.count() != nv || (o_seam != Py_None && bseam.count() != 4)) {
        PyErr_SetString(PyExc_ValueError, "cull_slivers: mismatched array sizes");
        return nullptr;
    }
    std::vector<Sample> samples(bs.count() / 5);
    std::copy_n(bs.data<double>(), samples.size() * 5, (double*)samples.data());
    Grid pgrid;
    for (int i = 0; i < (int)samples.size(); ++i)
        pgrid[cell_key(cell_of(samples[(size_t)i].x, kPCell),
                       cell_of(samples[(size_t)i].y, kPCell))].push_back(i);
    std::vector<Tri> tris(bt.count() / 3);
    std::vector<int> ids(tris.size());
    const int* tp = bt.data<int>();
    for (size_t i = 0; i < tris.size(); ++i) {
        tris[i] = {tp[3 * i], tp[3 * i + 1], tp[3 * i + 2]};
        ids[i] = (int)i;
        for (int k = 0; k < 3; ++k) {
            if (tris[i][k] < 0 || (size_t)tris[i][k] >= nv) {
                PyErr_SetString(PyExc_IndexError, "cull_slivers: vertex index out of range");
                return nullptr;
            }
        }
    }
    const Mesh M{bv.data<double>(), bf.data<uint8_t>(), bp.data<uint8_t>(),
                 o_seam != Py_None ? bseam.data<double>() : nullptr, samples, pgrid, P};
    double removed = 0.0;
    try {
        for (int round = 0; round < 3; ++round)
            if (!cull_round(M, tris, ids, removed, budget)) break;
    } catch (const std::bad_alloc&) {
        return PyErr_NoMemory();
    }
    PyObject* out = PyList_New((Py_ssize_t)ids.size());
    for (size_t i = 0; out && i < ids.size(); ++i)
        PyList_SET_ITEM(out, (Py_ssize_t)i, PyLong_FromLong(ids[i]));
    return out;
}
