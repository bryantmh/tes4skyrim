// The NVNM serialiser and the per-triangle passes convert_PGRD runs before it
// (from_pgrd: _compute_water_flags, compute_adjacency, build_door_links,
// _resolve_ledge_links, pack_nvnm, lookup_grid.build_navmesh_grid), plus the
// NVMI center.  Byte for byte: every float step mirrors the Python, and a
// value struct.pack would refuse raises here too.

#include "mesh.h"
#include "pymath.h"
#include "pyconv.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

using namespace navgrow;

namespace {

constexpr uint32_t kNvnmVersion = 12;
constexpr uint32_t kPathingCellCrc = 0xA5E9A03C;
constexpr uint32_t kPathingDoorCrc = 0xE48B73F3;
constexpr int kFlagWater = 0x0200, kFlagDoor = 0x0400, kFlagFound = 0x0800;
constexpr int kEdgeLinkBit[3] = {0x0001, 0x0002, 0x0004};
constexpr uint32_t kLedgeUp = 1, kLedgeDown = 2;
constexpr double kGridTargetCell = 600.0;
constexpr int kGridDivMin = 2, kGridDivMax = 12;
constexpr double kDoorLinkMaxDist = 220.0, kDoorAlongWeight = 2.0;

struct Door { double x, y, z, rot; uint32_t fid; };
struct Frame {
    uint32_t wrld, cell, navm;
    int gx, gy;
    bool exterior, has_water;
    double water_z;
};
struct Link { uint32_t typ, nav; int ti; };

// --- struct.pack, with its range checks -----------------------------------
struct Buf {
    std::string b;
    void raw(const void* p, size_t n) { b.append((const char*)p, n); }
    void u32(long long v) {
        if (v < 0 || v > 0xFFFFFFFFLL) throw std::out_of_range("'I' format requires 0 <= number <= 4294967295");
        const uint32_t x = (uint32_t)v;
        raw(&x, 4);
    }
    void i16(long long v) {
        if (v < -32768 || v > 32767) throw std::out_of_range("'h' format requires -32768 <= number <= 32767");
        const int16_t x = (int16_t)v;
        raw(&x, 2);
    }
    void u16(long long v) {
        if (v < 0 || v > 65535) throw std::out_of_range("'H' format requires 0 <= number <= 65535");
        const uint16_t x = (uint16_t)v;
        raw(&x, 2);
    }
    void f32(double v) {
        const float x = (float)v;
        if (std::isinf(x) && !std::isinf(v)) throw std::overflow_error("float too large to pack with f format");
        raw(&x, 4);
    }
};

// Python's min()/max() over a sequence: the first item, replaced only by a
// strictly smaller (larger) one.
double py_min3(double a, double b, double c) {
    double m = a;
    if (b < m) m = b;
    if (c < m) m = c;
    return m;
}
double py_max3(double a, double b, double c) {
    double m = a;
    if (b > m) m = b;
    if (c > m) m = c;
    return m;
}

// int(round(x)): half to even, raising where Python would.
long long py_round_int(double x) {
    if (!std::isfinite(x)) throw std::overflow_error("cannot convert float to integer");
    return (long long)std::nearbyint(x);
}

std::vector<int> water_flags(const std::vector<Vert>& V, const std::vector<Tri>& T, const Frame& F) {
    std::vector<int> flags(T.size(), 0);
    if (!F.has_water) return flags;
    for (size_t i = 0; i < T.size(); ++i) {
        const double cz = (V[T[i][0]].z + V[T[i][1]].z + V[T[i][2]].z) / 3.0;
        if (cz < F.water_z) flags[i] = kFlagWater;
    }
    return flags;
}

std::vector<Tri> adjacency(const std::vector<Tri>& T) {
    std::unordered_map<long long, std::vector<std::pair<int, int>>> edges;
    for (size_t ti = 0; ti < T.size(); ++ti)
        for (int s = 0; s < 3; ++s) {
            const long long a = T[ti][s], b = T[ti][(s + 1) % 3];
            edges[std::min(a, b) * 4294967296LL + std::max(a, b)].push_back({(int)ti, s});
        }
    std::vector<Tri> adj(T.size(), Tri{-1, -1, -1});
    for (const auto& kv : edges) {
        if (kv.second.size() != 2) continue;
        const auto [ti, si] = kv.second[0];
        const auto [tj, sj] = kv.second[1];
        adj[(size_t)ti][(size_t)si] = tj;
        adj[(size_t)tj][(size_t)sj] = ti;
    }
    return adj;
}

// _containing_triangle: the unused triangle holding (dx, dy) nearest dz in
// steps of 32, largest first; -1 for none.
int containing_triangle(const std::vector<Vert>& V, const std::vector<Tri>& T,
                        const std::vector<char>& used, const Door& d) {
    int best = -1;
    long long best_r = 0;
    double best_a = 0.0;
    for (size_t ti = 0; ti < T.size(); ++ti) {
        if (used[ti]) continue;
        const Vert &a = V[T[ti][0]], &b = V[T[ti][1]], &c = V[T[ti][2]];
        const double det = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y);
        if (std::fabs(det) < 1e-9) continue;
        const double l0 = ((b.y - c.y) * (d.x - c.x) + (c.x - b.x) * (d.y - c.y)) / det;
        const double l1 = ((c.y - a.y) * (d.x - c.x) + (a.x - c.x) * (d.y - c.y)) / det;
        const double l2 = 1.0 - l0 - l1;
        if (l0 < -0.01 || l1 < -0.01 || l2 < -0.01) continue;
        const double z = l0 * a.z + l1 * b.z + l2 * c.z;
        const double dzz = std::fabs(z - d.z);
        const long long r = py_round_int(dzz / 32.0);
        const double area = -0.5 * std::fabs(det);
        if (dzz <= 128.0 && (best < 0 || r < best_r || (r == best_r && area < best_a))) {
            best = (int)ti;
            best_r = r;
            best_a = area;
        }
    }
    return best;
}

// _nearest_to_threshold: the unused triangle centroid nearest the door,
// weighted along its facing; -1 when none is within DOOR_LINK_MAX_DIST.
int nearest_to_threshold(const std::vector<std::pair<double, double>>& cents,
                         const std::vector<char>& used, const Door& d) {
    const double fx = crt_cos(d.rot), fy = -crt_sin(d.rot);
    const double max2 = py_square(kDoorLinkMaxDist);
    int best = -1;
    double best_cost = 0.0;
    for (size_t ti = 0; ti < cents.size(); ++ti) {
        if (used[ti]) continue;
        const double ox = cents[ti].first - d.x, oy = cents[ti].second - d.y;
        const double dist2 = ox * ox + oy * oy;
        if (dist2 > max2) continue;
        const double along = std::fabs(ox * fx + oy * fy);
        const double cost = dist2 + py_square(along * kDoorAlongWeight);
        if (best < 0 || cost < best_cost) {
            best = (int)ti;
            best_cost = cost;
        }
    }
    return best;
}

std::vector<std::pair<int, uint32_t>> door_links(const std::vector<Vert>& V, const std::vector<Tri>& T,
                                                 const std::vector<Door>& doors) {
    std::vector<std::pair<int, uint32_t>> links;
    if (doors.empty() || T.empty()) return links;
    std::vector<std::pair<double, double>> cents;
    for (const Tri& t : T)
        cents.push_back({(V[t[0]].x + V[t[1]].x + V[t[2]].x) / 3.0,
                         (V[t[0]].y + V[t[1]].y + V[t[2]].y) / 3.0});
    std::vector<char> used(T.size(), 0);
    for (const Door& d : doors) {
        if (!d.fid) continue;
        int ti = containing_triangle(V, T, used, d);
        if (ti < 0) ti = nearest_to_threshold(cents, used, d);
        if (ti < 0) continue;
        links.push_back({ti, d.fid});
        used[(size_t)ti] = 1;
    }
    return links;
}

// _open_edge_towards: ti's neighbourless edge nearest other's centroid; -1 for none.
int open_edge_towards(const std::vector<Vert>& V, const std::vector<Tri>& T,
                      const std::vector<Tri>& adj, int ti, int other) {
    PySum sx, sy;
    for (int k = 0; k < 3; ++k) {
        sx.add(V[T[(size_t)other][k]].x);
        sy.add(V[T[(size_t)other][k]].y);
    }
    const double ox = sx.value() / 3.0, oy = sy.value() / 3.0;
    int best = -1;
    double best_d = 0.0;
    for (int s = 0; s < 3; ++s) {
        if (adj[(size_t)ti][s] >= 0) continue;
        const Vert &a = V[T[(size_t)ti][s]], &b = V[T[(size_t)ti][(s + 1) % 3]];
        const double mx = 0.5 * (a.x + b.x), my = 0.5 * (a.y + b.y);
        const double d = py_square(mx - ox) + py_square(my - oy);
        if (best < 0 || d < best_d) {
            best = s;
            best_d = d;
        }
    }
    return best;
}

// _resolve_ledge_links: a pair commits only when both of its sides resolve.
void resolve_ledges(const std::vector<Vert>& V, const std::vector<Tri>& T, const std::vector<Tri>& adj,
                    const std::vector<Ledge>& ledges, uint32_t navm, std::map<int, int>& link_bit,
                    std::map<std::pair<int, int>, int>& link_slot, std::vector<Link>& links) {
    const int n = (int)T.size();
    for (const Ledge& l : ledges) {
        if (!(0 <= l.hi && l.hi < n && 0 <= l.lo && l.lo < n)) continue;
        struct Side { int ti, other, slot; uint32_t typ; };
        const Side sides[2] = {{l.hi, l.lo, -1, kLedgeDown}, {l.lo, l.hi, -1, kLedgeUp}};
        Side pending[2];
        bool ok = true;
        for (int k = 0; k < 2 && ok; ++k) {
            pending[k] = sides[k];
            pending[k].slot = open_edge_towards(V, T, adj, sides[k].ti, sides[k].other);
            ok = pending[k].slot >= 0 && !link_slot.count({sides[k].ti, pending[k].slot});
        }
        if (!ok) continue;
        for (const Side& s : pending) {
            link_bit[s.ti] |= kEdgeLinkBit[s.slot];
            link_slot[{s.ti, s.slot}] = (int)links.size();
            links.push_back({s.typ, navm, s.other});
        }
    }
}

// lookup_grid.tri_hits_rect.
bool tri_hits_rect(const double xs[3], const double ys[3], double x0, double y0, double x1, double y1) {
    if (py_max3(xs[0], xs[1], xs[2]) < x0 || py_min3(xs[0], xs[1], xs[2]) > x1
        || py_max3(ys[0], ys[1], ys[2]) < y0 || py_min3(ys[0], ys[1], ys[2]) > y1)
        return false;
    const double corners[4][2] = {{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}};
    for (int k = 0; k < 3; ++k) {
        const double ax = xs[k], ay = ys[k], bx = xs[(k + 1) % 3], by = ys[(k + 1) % 3];
        const double nx = by - ay, ny = ax - bx;
        const double side = nx * (xs[(k + 2) % 3] - ax) + ny * (ys[(k + 2) % 3] - ay);
        bool all_out = true;
        for (const auto& c : corners)
            if (!((nx * (c[0] - ax) + ny * (c[1] - ay)) * side < 0)) { all_out = false; break; }
        if (all_out) return false;
    }
    return true;
}

// lookup_grid._cell_range as [first, last].
std::pair<int, int> cell_range(double lo, double hi, double origin, double size, int g) {
    auto at = [&](double v) {
        const double q = py_floordiv(v - origin, size);
        if (!std::isfinite(q)) throw std::overflow_error("cannot convert float to integer");
        return (int)std::min(std::max(q, 0.0), (double)(g - 1));
    };
    return {at(lo), at(hi)};
}

// _pack_bounds_and_grid.
void pack_bounds_and_grid(Buf& out, const std::vector<Vert>& V, const std::vector<Tri>& T) {
    double mn[3] = {0, 0, 0}, mx[3] = {0, 0, 0};
    for (size_t i = 0; i < V.size(); ++i) {
        const double c[3] = {V[i].x, V[i].y, V[i].z};
        for (int k = 0; k < 3; ++k) {
            if (i == 0 || c[k] < mn[k]) mn[k] = c[k];
            if (i == 0 || c[k] > mx[k]) mx[k] = c[k];
        }
    }
    const double span_x = mx[0] > mn[0] ? mx[0] - mn[0] : 1.0;
    const double span_y = mx[1] > mn[1] ? mx[1] - mn[1] : 1.0;
    const double span = span_y > span_x ? span_y : span_x;
    const int g = (int)std::max<long long>(kGridDivMin,
                                           std::min<long long>(kGridDivMax, py_round_int(span / kGridTargetCell)));
    out.u32(g);
    out.f32(span_x / g);
    out.f32(span_y / g);
    for (double v : {mn[0], mn[1], mn[2], mx[0], mx[1], mx[2]}) out.f32(v);
    const double cw = span_x / g, ch = span_y / g;
    std::vector<std::vector<int>> grid((size_t)g * (size_t)g);
    for (size_t ti = 0; ti < T.size(); ++ti) {
        const double xs[3] = {V[T[ti][0]].x, V[T[ti][1]].x, V[T[ti][2]].x};
        const double ys[3] = {V[T[ti][0]].y, V[T[ti][1]].y, V[T[ti][2]].y};
        const auto rows = cell_range(py_min3(ys[0], ys[1], ys[2]), py_max3(ys[0], ys[1], ys[2]), mn[1], ch, g);
        const auto cols = cell_range(py_min3(xs[0], xs[1], xs[2]), py_max3(xs[0], xs[1], xs[2]), mn[0], cw, g);
        for (int row = rows.first; row <= rows.second; ++row)
            for (int col = cols.first; col <= cols.second; ++col) {
                const double y0 = mn[1] + row * ch, x0 = mn[0] + col * cw;
                if (tri_hits_rect(xs, ys, x0, y0, x0 + cw, y0 + ch))
                    grid[(size_t)row * (size_t)g + (size_t)col].push_back((int)ti);
            }
    }
    for (const auto& cell : grid) {
        out.u32((long long)cell.size());
        for (int ti : cell) out.i16(ti);
    }
}

struct Packed {
    std::string nvnm;
    std::vector<std::pair<int, uint32_t>> door_tris;
    double center[3];
};

// convert_PGRD from the water flags to pack_nvnm, plus the NVMI center.
Packed pack(const std::vector<Vert>& V, const std::vector<Tri>& T, const std::vector<Ledge>& ledges,
            const std::vector<Door>& doors, const Frame& F) {
    Packed P;
    const std::vector<int> flags = water_flags(V, T, F);
    const std::vector<Tri> adj = adjacency(T);
    P.door_tris = door_links(V, T, doors);
    Buf out;
    out.u32(kNvnmVersion);
    out.u32(kPathingCellCrc);
    out.u32(F.wrld);
    if (F.exterior) {
        out.i16(F.gy);
        out.i16(F.gx);
    } else {
        out.u32(F.cell);
    }
    out.u32((long long)V.size());
    for (const Vert& v : V) {
        out.f32(v.x);
        out.f32(v.y);
        out.f32(v.z);
    }
    std::map<int, int> link_bit;
    std::map<std::pair<int, int>, int> link_slot;
    std::vector<Link> links;
    resolve_ledges(V, T, adj, ledges, F.navm, link_bit, link_slot, links);
    std::map<int, uint32_t> door_by_tri;
    for (const auto& dt : P.door_tris) door_by_tri[dt.first] = dt.second;
    out.u32((long long)T.size());
    for (size_t ti = 0; ti < T.size(); ++ti) {
        Tri e = adj[ti];
        for (int s = 0; s < 3; ++s) {
            const auto it = link_slot.find({(int)ti, s});
            if (it != link_slot.end()) e[s] = it->second;
        }
        const auto lb = link_bit.find((int)ti);
        int f = kFlagFound | (lb == link_bit.end() ? 0 : lb->second) | flags[ti];
        if (door_by_tri.count((int)ti)) f |= kFlagDoor;
        for (int k = 0; k < 3; ++k) out.i16(T[ti][k]);
        for (int k = 0; k < 3; ++k) out.i16(e[k]);
        out.u16(f);
        out.u16(0);
    }
    out.u32((long long)links.size());
    for (const Link& l : links) {
        out.u32(l.typ);
        out.u32(l.nav);
        out.i16(l.ti);
    }
    std::vector<std::pair<int, uint32_t>> sorted = P.door_tris;
    std::sort(sorted.begin(), sorted.end());
    out.u32((long long)sorted.size());
    for (const auto& dt : sorted) {
        out.i16(dt.first);
        out.u32(kPathingDoorCrc);
        out.u32(dt.second);
    }
    out.u32(0);
    pack_bounds_and_grid(out, V, T);
    P.nvnm.swap(out.b);
    PySum s[3];
    for (const Vert& v : V) {
        s[0].add(v.x);
        s[1].add(v.y);
        s[2].add(v.z);
    }
    for (int k = 0; k < 3; ++k) P.center[k] = s[k].value() / (double)V.size();
    return P;
}

// --- Python inputs ----------------------------------------------------------
bool u32_of(PyObject* o, uint32_t* out) {
    const unsigned long long v = PyLong_AsUnsignedLongLong(o);
    if (PyErr_Occurred()) return false;
    if (v > 0xFFFFFFFFULL) {
        PyErr_SetString(PyExc_OverflowError, "'I' format requires 0 <= number <= 4294967295");
        return false;
    }
    *out = (uint32_t)v;
    return true;
}

// Rows of a sequence, each read by `row`; false with an error set.
template <class F>
bool rows(PyObject* o, const char* what, F&& row) {
    PyObject* seq = PySequence_Fast(o, what);
    if (!seq) return false;
    bool ok = true;
    for (Py_ssize_t i = 0; ok && i < PySequence_Fast_GET_SIZE(seq); ++i) {
        PyObject* r = PySequence_Fast(PySequence_Fast_GET_ITEM(seq, i), what);
        ok = r && row(PySequence_Fast_ITEMS(r), PySequence_Fast_GET_SIZE(r));
        Py_XDECREF(r);
    }
    Py_DECREF(seq);
    return ok && !PyErr_Occurred();
}

bool ledges_of(PyObject* o, std::vector<Ledge>& out) {
    return rows(o, "ledges are (hi, lo, drop) rows", [&](PyObject** it, Py_ssize_t n) {
        if (n < 3) return false;
        out.push_back({(int)PyLong_AsLong(it[0]), (int)PyLong_AsLong(it[1]), PyFloat_AsDouble(it[2])});
        return true;
    });
}

// collect_doors rows (x, y, z, rot_z, ref_fid, is_teleport, width).
bool doors_of(PyObject* o, std::vector<Door>& out) {
    return rows(o, "doors are (x, y, z, rot_z, fid, is_teleport, width) rows", [&](PyObject** it, Py_ssize_t n) {
        if (n < 5) return false;
        Door d{PyFloat_AsDouble(it[0]), PyFloat_AsDouble(it[1]), PyFloat_AsDouble(it[2]),
               PyFloat_AsDouble(it[3]), 0};
        if (PyObject_IsTrue(it[4]) && !u32_of(it[4], &d.fid)) return false;
        out.push_back(d);
        return true;
    });
}

PyObject* packed_result(const Packed& P) {
    PyObject* dt = PyList_New((Py_ssize_t)P.door_tris.size());
    for (size_t i = 0; dt && i < P.door_tris.size(); ++i)
        PyList_SET_ITEM(dt, (Py_ssize_t)i, Py_BuildValue("(ik)", P.door_tris[i].first,
                                                         (unsigned long)P.door_tris[i].second));
    if (!dt) return nullptr;
    return Py_BuildValue("(y#N(ddd))", P.nvnm.data(), (Py_ssize_t)P.nvnm.size(), dt,
                         P.center[0], P.center[1], P.center[2]);
}

}  // namespace

// pack_nvnm(verts, tris, ledges, doors, water_z, wrld_fid, cell_fid, grid_x,
//           grid_y, is_exterior, navm_fid) -> (nvnm, door_tris, center)
//
// from_pgrd.convert_PGRD from the water flags through pack_nvnm, with the
// GIL released: the NVNM bytes, [(triangle, door ref), ...] in link order, and
// the vertex mean.  doors are collect_doors rows; water_z is cell_water_z or
// None.
PyObject* py_pack_nvnm(PyObject*, PyObject* args) {
    PyObject *o_v, *o_t, *o_l, *o_d, *o_wz, *o_wrld, *o_cell, *o_navm;
    int gx, gy, exterior;
    if (!PyArg_ParseTuple(args, "OOOOOOOiipO", &o_v, &o_t, &o_l, &o_d, &o_wz, &o_wrld, &o_cell, &gx,
                          &gy, &exterior, &o_navm))
        return nullptr;
    std::vector<Vert> V;
    std::vector<Tri> T;
    std::vector<Ledge> ledges;
    std::vector<Door> doors;
    Frame F{};
    F.exterior = exterior != 0;
    F.gx = gx;
    F.gy = gy;
    F.has_water = o_wz != Py_None;
    if (F.has_water) F.water_z = PyFloat_AsDouble(o_wz);
    if (PyErr_Occurred() || !read_verts(o_v, V, false) || !read_tris(o_t, V.size(), T)
        || !ledges_of(o_l, ledges) || !doors_of(o_d, doors) || !u32_of(o_wrld, &F.wrld)
        || !u32_of(o_cell, &F.cell) || !u32_of(o_navm, &F.navm))
        return nullptr;
    if (V.empty()) {
        PyErr_SetString(PyExc_ValueError, "pack_nvnm needs vertices");
        return nullptr;
    }
    Packed P;
    std::string err;
    Py_BEGIN_ALLOW_THREADS
    try {
        P = pack(V, T, ledges, doors, F);
    } catch (const std::exception& e) {
        err = e.what();
    }
    Py_END_ALLOW_THREADS
    if (!err.empty()) {
        PyErr_SetString(PyExc_ValueError, ("pack_nvnm: " + err).c_str());
        return nullptr;
    }
    return packed_result(P);
}
