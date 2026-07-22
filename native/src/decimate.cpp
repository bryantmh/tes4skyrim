// Native mesh decimation for tes5_import.navmesh.spanmesh.
//
// WHY THIS EXISTS
// ---------------
// Profiling (tools/navmesh_profile.py) put ~61% of navmesh generation inside
// spanmesh's decimation.  The cost is not one hot kernel: it is ~12M tiny
// Python calls per exterior cell, each doing a handful of float ops.  There is
// no algorithmic fat left (a pure-Python optimisation pass moved less than the
// 11% run-to-run noise), and Numba measured only 3.0x with a ~0.5s per-process
// JIT tax that the pipeline's short-lived workers pay 17-28 times.  A compiled
// extension has no per-process tax and keeps the whole 4-pass loop on this side
// of the boundary, so the marshalling happens ONCE per cell instead of per pass.
//
// WHAT IT MUST PRESERVE
// ---------------------
// These passes encode geometry contracts that were expensive to get right:
// needle rules (MAX_EDGE_RATIO), fold rejection (normal dot > 0), crease
// preservation (cos_flat), boundary/outline budgets (acc), and pinned door
// corners.  Every predicate below mirrors the Python original operation for
// operation, including comparison ORDER and threshold constants -- an earlier
// attempt that "simplified" _tri_shape's `area <= 1e-6` into a normal-length
// test silently moved vertices and changed the mesh.
//
// Vertex VISIT ORDER is part of the result, not an implementation detail: each
// accepted move mutates the mesh, so a later vertex sees its neighbour's new
// position.  The caller passes the exact order the Python dicts produced.
//
// Exact bit-reproducibility against the Python version is NOT required (the
// user accepts minor vertex drift; measured divergence was 1 ULP from float
// summation order).  Run-to-run determinism IS required -- the pipeline's
// output must be byte-reproducible -- so nothing here depends on pointer
// values, hash iteration, or uninitialised memory.

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <unordered_map>
#include <unordered_set>
#include <vector>

// MSVC does not define M_PI without _USE_MATH_DEFINES; spell the constants out
// so the build does not depend on that macro being set.
static constexpr double kDeg10Rad = 10.0 * 3.14159265358979323846 / 180.0;
static constexpr double kDeg5Rad = 5.0 * 3.14159265358979323846 / 180.0;

namespace {

struct Vec3 {
    double x, y, z;
};

// ---------------------------------------------------------------------------
// Geometry primitives -- direct transcriptions of spanmesh._tri_shape /
// _seg_dist.  Kept in one place so every pass shares identical arithmetic.
// ---------------------------------------------------------------------------

struct TriShape {
    double aspect;    // longest^2 / (4*area); 1e9 when degenerate
    double longest;
    double nx, ny, nz;  // UNNORMALISED cross product; zero when degenerate
    double ratio;     // longest/shortest edge; 1e9 when shortest ~ 0
};

inline double dist3(const Vec3& a, const Vec3& b) {
    const double dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

// Mirrors _tri_shape exactly, including the `area <= 1e-6` degeneracy cut
// (NOT a normal-length cut -- they are different thresholds).
inline TriShape tri_shape(const Vec3& pa, const Vec3& pb, const Vec3& pc) {
    TriShape s;
    const double e0 = dist3(pa, pb);
    const double e1 = dist3(pb, pc);
    const double e2 = dist3(pc, pa);
    s.longest = std::max(e0, std::max(e1, e2));
    const double shortest = std::min(e0, std::min(e1, e2));
    s.ratio = (shortest > 1e-9) ? (s.longest / shortest) : 1e9;

    const double ux = pb.x - pa.x, uy = pb.y - pa.y, uz = pb.z - pa.z;
    const double wx = pc.x - pa.x, wy = pc.y - pa.y, wz = pc.z - pa.z;
    s.nx = uy * wz - uz * wy;
    s.ny = uz * wx - ux * wz;
    s.nz = ux * wy - uy * wx;

    const double area =
        0.5 * std::sqrt(s.nx * s.nx + s.ny * s.ny + s.nz * s.nz);
    if (area <= 1e-6) {
        s.aspect = 1e9;
        s.nx = s.ny = s.nz = 0.0;
        return s;
    }
    s.aspect = s.longest * s.longest / (4.0 * area);
    return s;
}

// Mirrors _seg_dist: distance from p to segment ab.
inline double seg_dist(const Vec3& p, const Vec3& a, const Vec3& b) {
    const double abx = b.x - a.x, aby = b.y - a.y, abz = b.z - a.z;
    const double apx = p.x - a.x, apy = p.y - a.y, apz = p.z - a.z;
    const double denom = abx * abx + aby * aby + abz * abz;
    double t = 0.0;
    if (denom >= 1e-12) {
        t = (apx * abx + apy * aby + apz * abz) / denom;
        t = std::max(0.0, std::min(1.0, t));
    }
    const double dx = apx - t * abx;
    const double dy = apy - t * aby;
    const double dz = apz - t * abz;
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

// ---------------------------------------------------------------------------
// Mesh state shared by the three passes.
//
// Triangles are stored flat and marked dead rather than erased, matching the
// Python `alive[]` list; vertices are never removed, only union-found onto a
// survivor (`vmap`), exactly as _collapse_pass does.  Compaction happens once
// at the end.
// ---------------------------------------------------------------------------

struct Mesh {
    std::vector<Vec3> verts;
    std::vector<int32_t> tris;   // 3 per triangle
    std::vector<uint8_t> alive;  // per triangle
    std::vector<int32_t> vmap;   // union-find parent

    size_t tri_count() const { return alive.size(); }

    int32_t find(int32_t v) {
        // Path halving; same root as the Python version.
        while (vmap[v] != v) {
            vmap[v] = vmap[vmap[v]];
            v = vmap[v];
        }
        return v;
    }

    void tri_verts(size_t t, int32_t& a, int32_t& b, int32_t& c) {
        a = find(tris[t * 3 + 0]);
        b = find(tris[t * 3 + 1]);
        c = find(tris[t * 3 + 2]);
    }
};

inline uint64_t edge_key(int32_t a, int32_t b) {
    if (a > b) std::swap(a, b);
    return (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 32) |
           static_cast<uint32_t>(b);
}

// ---------------------------------------------------------------------------
// Pass 1: quality-bounded edge collapses, shortest edge first.
// Mirrors spanmesh._collapse_pass.
// ---------------------------------------------------------------------------

struct CollapseParams {
    double target_err;
    double max_aspect;
    double max_ratio;
    double max_edge;
    double cs_local;
};

class CollapsePass {
public:
    CollapsePass(Mesh& m, const CollapseParams& p,
                 const std::vector<uint8_t>& pinned,
                 std::unordered_map<int32_t, double>& acc)
        : m_(m), p_(p), pinned_(pinned), acc_(acc) {}

    int run() {
        build_adjacency();

        // Shortest incident edge first: coarsening eats the dense voxel-scale
        // mesh from the bottom up, driving edge lengths toward uniform.
        std::vector<std::pair<double, int32_t>> order;
        order.reserve(neighbours_.size());
        for (const auto& kv : neighbours_) {
            const int32_t v = kv.first;
            double shortest = 1e300;
            for (const int32_t n : kv.second)
                shortest = std::min(shortest, dist3(m_.verts[v], m_.verts[n]));
            order.emplace_back(shortest, v);
        }
        // Ties broken by vertex index so the sweep is deterministic; Python's
        // sort on (dist, v) tuples does the same.
        std::sort(order.begin(), order.end());

        int collapsed = 0;
        for (const auto& ov : order) {
            const int32_t v = ov.second;
            if (pinned_[v]) continue;
            if (m_.find(v) != v) continue;

            auto bit = bnbrs_.find(v);
            if (bit != bnbrs_.end()) {
                if (try_boundary(v, bit->second)) ++collapsed;
            } else {
                if (try_interior(v)) ++collapsed;
            }
        }
        return collapsed;
    }

private:
    Mesh& m_;
    const CollapseParams& p_;
    const std::vector<uint8_t>& pinned_;
    std::unordered_map<int32_t, double>& acc_;

    std::unordered_map<int32_t, std::vector<int32_t>> vtris_;
    std::unordered_map<int32_t, std::unordered_set<int32_t>> neighbours_;
    std::unordered_map<int32_t, std::unordered_set<int32_t>> bnbrs_;

    void build_adjacency() {
        std::unordered_map<uint64_t, int> edge_count;
        for (size_t t = 0; t < m_.tri_count(); ++t) {
            if (!m_.alive[t]) continue;
            const int32_t a = m_.tris[t * 3 + 0];
            const int32_t b = m_.tris[t * 3 + 1];
            const int32_t c = m_.tris[t * 3 + 2];
            vtris_[a].push_back(static_cast<int32_t>(t));
            vtris_[b].push_back(static_cast<int32_t>(t));
            vtris_[c].push_back(static_cast<int32_t>(t));
            const int32_t pairs[3][2] = {{a, b}, {b, c}, {c, a}};
            for (auto& e : pairs) {
                neighbours_[e[0]].insert(e[1]);
                neighbours_[e[1]].insert(e[0]);
                edge_count[edge_key(e[0], e[1])] += 1;
            }
        }
        // A boundary edge borders exactly one triangle.
        for (const auto& kv : edge_count) {
            if (kv.second != 1) continue;
            const int32_t a = static_cast<int32_t>(kv.first >> 32);
            const int32_t b = static_cast<int32_t>(kv.first & 0xFFFFFFFFu);
            bnbrs_[a].insert(b);
            bnbrs_[b].insert(a);
        }
    }

    // (anchor, unit normal) of v's incident alive triangles, computed once per
    // vertex and reused across candidates.
    void vertex_planes(int32_t v, std::vector<std::array<double, 6>>& out) {
        out.clear();
        auto it = vtris_.find(v);
        if (it == vtris_.end()) return;
        for (const int32_t t : it->second) {
            if (!m_.alive[t]) continue;
            int32_t a, b, c;
            m_.tri_verts(t, a, b, c);
            if (a == b || b == c || a == c) continue;
            const Vec3& va = m_.verts[a];
            const Vec3& vb = m_.verts[b];
            const Vec3& vc = m_.verts[c];
            const double ux = vb.x - va.x, uy = vb.y - va.y, uz = vb.z - va.z;
            const double wx = vc.x - va.x, wy = vc.y - va.y, wz = vc.z - va.z;
            const double nx = uy * wz - uz * wy;
            const double ny = uz * wx - ux * wz;
            const double nz = ux * wy - uy * wx;
            const double ln = std::sqrt(nx * nx + ny * ny + nz * nz);
            if (ln < 1e-9) continue;
            out.push_back({va.x, va.y, va.z, nx / ln, ny / ln, nz / ln});
        }
    }

    double plane_dev(const std::vector<std::array<double, 6>>& planes,
                     int32_t keep) {
        const Vec3& pk = m_.verts[keep];
        double worst = 0.0;
        for (const auto& pl : planes) {
            double d = (pk.x - pl[0]) * pl[3] + (pk.y - pl[1]) * pl[4] +
                       (pk.z - pl[2]) * pl[5];
            if (d < 0.0) d = -d;
            if (d > worst) {
                worst = d;
                if (worst > p_.target_err) return worst;  // early out
            }
        }
        return worst;
    }

    // Worst shape after collapsing v->keep, or <0 if any check fails.
    double collapse_quality(int32_t v, int32_t keep) {
        const Vec3& pk = m_.verts[keep];
        double worst = 0.0;
        auto it = vtris_.find(v);
        if (it == vtris_.end()) return worst;
        for (const int32_t t : it->second) {
            if (!m_.alive[t]) continue;
            int32_t ia, ib, ic;
            m_.tri_verts(t, ia, ib, ic);
            if (v != ia && v != ib && v != ic) continue;
            if (keep == ia || keep == ib || keep == ic) continue;  // dies

            const Vec3 old_a = m_.verts[ia];
            const Vec3 old_b = m_.verts[ib];
            const Vec3 old_c = m_.verts[ic];
            const Vec3 na = (ia == v) ? pk : old_a;
            const Vec3 nb = (ib == v) ? pk : old_b;
            const Vec3 nc = (ic == v) ? pk : old_c;

            const TriShape ns = tri_shape(na, nb, nc);
            if (ns.aspect > p_.max_aspect || ns.longest > p_.max_edge)
                return -1.0;
            const TriShape os = tri_shape(old_a, old_b, old_c);
            // May not leave a needle behind -- unless it was already worse.
            if (ns.ratio > p_.max_ratio && ns.ratio >= os.ratio) return -1.0;
            // May not fold the mesh.
            if (ns.nx * os.nx + ns.ny * os.ny + ns.nz * os.nz <= 0.0)
                return -1.0;
            worst = std::max(worst, ns.aspect);
        }
        return worst;
    }

    // Link condition: the only neighbours v and keep share are the opposite
    // corners of the triangles dying with the edge.  Anything else pinches the
    // mesh into a non-manifold configuration and corrupts NVNM adjacency.
    bool link_ok(int32_t v, int32_t keep) {
        std::unordered_set<int32_t> opp;
        auto it = vtris_.find(v);
        if (it != vtris_.end()) {
            for (const int32_t t : it->second) {
                if (!m_.alive[t]) continue;
                int32_t ia, ib, ic;
                m_.tri_verts(t, ia, ib, ic);
                const bool has_v = (v == ia || v == ib || v == ic);
                const bool has_k = (keep == ia || keep == ib || keep == ic);
                if (!has_v || !has_k) continue;
                const int32_t idx[3] = {ia, ib, ic};
                for (const int32_t x : idx)
                    if (x != v && x != keep) opp.insert(x);
            }
        }

        std::unordered_set<int32_t> kroots;
        auto kit = neighbours_.find(keep);
        if (kit != neighbours_.end())
            for (const int32_t n : kit->second) kroots.insert(m_.find(n));

        std::unordered_set<int32_t> shared;
        auto vit = neighbours_.find(v);
        if (vit != neighbours_.end()) {
            for (const int32_t n : vit->second) {
                const int32_t r = m_.find(n);
                if (r != v && r != keep && kroots.count(r)) shared.insert(r);
            }
        }
        return shared == opp;
    }

    void do_collapse(int32_t v, int32_t keep) {
        m_.vmap[v] = keep;
        auto vit = vtris_.find(v);
        if (vit != vtris_.end()) {
            auto& kt = vtris_[keep];
            kt.insert(kt.end(), vit->second.begin(), vit->second.end());
            for (const int32_t t : vit->second) {
                int32_t a, b, c;
                m_.tri_verts(t, a, b, c);
                if (a == b || b == c || a == c) m_.alive[t] = 0;
            }
        }
        std::unordered_set<int32_t> nv;
        auto nit = neighbours_.find(v);
        if (nit != neighbours_.end()) {
            nv = nit->second;
            neighbours_.erase(nit);
        }
        auto& nk = neighbours_[keep];
        for (const int32_t n : nv) {
            auto n_it = neighbours_.find(n);
            if (n_it != neighbours_.end()) {
                n_it->second.erase(v);
                if (n != keep) n_it->second.insert(keep);
            }
            if (n != keep) nk.insert(n);
        }
        nk.erase(keep);
        nk.erase(v);
    }

    bool try_boundary(int32_t v, const std::unordered_set<int32_t>& bn) {
        // Only a plain 2-neighbour chain vertex may move, only into a chain
        // neighbour, and only when the outline barely changes.  Corners of
        // doorways/junctions (degree != 2) stay put.
        if (bn.size() != 2) return false;
        auto bi = bn.begin();
        const int32_t a = m_.find(*bi++);
        const int32_t b = m_.find(*bi);
        if (a == v || b == v || a == b) return false;

        double dev = seg_dist(m_.verts[v], m_.verts[a], m_.verts[b]);
        auto ai = acc_.find(v);
        if (ai != acc_.end()) dev += ai->second;

        // A vertex on a VOXEL-SCALE outline notch is quantization noise, not
        // signal, and may absorb up to a cell of outline error.
        double lim = p_.target_err;
        const double da = dist3(m_.verts[v], m_.verts[a]);
        const double db = dist3(m_.verts[v], m_.verts[b]);
        if (std::min(da, db) < p_.cs_local * 1.75)
            lim = std::max(p_.target_err, p_.cs_local * 0.9);
        if (dev > lim) return false;

        std::vector<std::array<double, 6>> planes;
        vertex_planes(v, planes);

        bool have = false;
        double best_q = 0.0;
        int32_t best_keep = -1;
        const int32_t cands[2] = {a, b};
        for (const int32_t keep : cands) {
            if (plane_dev(planes, keep) > p_.target_err) continue;
            if (!link_ok(v, keep)) continue;
            const double q = collapse_quality(v, keep);
            if (q < 0.0) continue;
            if (!have || q < best_q) {
                have = true;
                best_q = q;
                best_keep = keep;
            }
        }
        if (!have) return false;

        do_collapse(v, best_keep);
        double& slot = acc_[best_keep];
        slot = std::max(slot, dev);
        const int32_t other = (best_keep == a) ? b : a;
        bnbrs_.erase(v);
        auto& kb = bnbrs_[best_keep];
        kb.erase(v);
        kb.insert(other);
        auto& ob = bnbrs_[other];
        ob.erase(v);
        ob.insert(best_keep);
        return true;
    }

    bool try_interior(int32_t v) {
        std::vector<std::array<double, 6>> planes;
        vertex_planes(v, planes);

        bool have = false;
        double best_q = 0.0;
        int32_t best_keep = -1;

        auto it = neighbours_.find(v);
        if (it == neighbours_.end()) return false;
        // Copy: do_collapse mutates neighbours_ and would invalidate iteration.
        // Sorted so the scan order (and therefore tie-breaking) is stable.
        std::vector<int32_t> cands(it->second.begin(), it->second.end());
        std::sort(cands.begin(), cands.end());

        for (int32_t nb : cands) {
            nb = m_.find(nb);
            if (nb == v) continue;
            if (plane_dev(planes, nb) > p_.target_err) continue;
            if (!link_ok(v, nb)) continue;
            const double q = collapse_quality(v, nb);
            if (q < 0.0) continue;
            if (!have || q < best_q) {
                have = true;
                best_q = q;
                best_keep = nb;
            }
        }
        if (!have) return false;
        do_collapse(v, best_keep);
        return true;
    }
};

// ---------------------------------------------------------------------------
// Pass 2: Lawson edge flips between near-coplanar triangle pairs.
// Mirrors spanmesh._flip_pass.  Operates on the COMPACTED triangle list.
// ---------------------------------------------------------------------------

struct FlipParams {
    double max_edge;
    double max_aspect;
    double max_ratio;
};

int flip_pass_impl(Mesh& m, const FlipParams& p) {
    const double cos_flat = std::cos(kDeg10Rad);
    const double max_aspect_new = p.max_aspect * 2.0;

    std::unordered_map<uint64_t, std::vector<int32_t>> edge_tris;
    for (size_t t = 0; t < m.tri_count(); ++t) {
        if (!m.alive[t]) continue;
        const int32_t a = m.tris[t * 3 + 0];
        const int32_t b = m.tris[t * 3 + 1];
        const int32_t c = m.tris[t * 3 + 2];
        const int32_t pairs[3][2] = {{a, b}, {b, c}, {c, a}};
        for (auto& e : pairs)
            edge_tris[edge_key(e[0], e[1])].push_back(static_cast<int32_t>(t));
    }
    std::unordered_set<uint64_t> all_edges;
    for (const auto& kv : edge_tris) all_edges.insert(kv.first);

    std::vector<uint64_t> stack;
    for (const auto& kv : edge_tris)
        if (kv.second.size() == 2) stack.push_back(kv.first);
    // Deterministic order: the Python version pops a list built from dict
    // iteration; sorting makes the sweep independent of hash layout.
    std::sort(stack.begin(), stack.end());

    int flips = 0;
    int64_t guard = 8 * static_cast<int64_t>(m.tri_count());

    while (!stack.empty() && guard > 0) {
        --guard;
        const uint64_t k = stack.back();
        stack.pop_back();

        auto it = edge_tris.find(k);
        if (it == edge_tris.end() || it->second.size() != 2) continue;
        const int32_t ti = it->second[0], tj = it->second[1];
        if (!m.alive[ti] || !m.alive[tj]) continue;

        int32_t t1[3] = {m.tris[ti * 3], m.tris[ti * 3 + 1], m.tris[ti * 3 + 2]};
        int32_t t2[3] = {m.tris[tj * 3], m.tris[tj * 3 + 1], m.tris[tj * 3 + 2]};

        int32_t u = static_cast<int32_t>(k >> 32);
        int32_t v = static_cast<int32_t>(k & 0xFFFFFFFFu);

        auto has = [](const int32_t* t, int32_t x) {
            return t[0] == x || t[1] == x || t[2] == x;
        };
        if (!has(t1, u) || !has(t1, v) || !has(t2, u) || !has(t2, v)) continue;

        auto third = [](const int32_t* t, int32_t x, int32_t y) {
            for (int i = 0; i < 3; ++i)
                if (t[i] != x && t[i] != y) return t[i];
            return static_cast<int32_t>(-1);
        };
        const int32_t p_op = third(t1, u, v);
        const int32_t q_op = third(t2, u, v);
        if (p_op < 0 || q_op < 0 || p_op == q_op) continue;
        if (all_edges.count(edge_key(p_op, q_op))) continue;  // would duplicate

        // Orient so t1 traverses u->v.
        auto idx_of = [](const int32_t* t, int32_t x) {
            for (int i = 0; i < 3; ++i)
                if (t[i] == x) return i;
            return -1;
        };
        int i1 = idx_of(t1, u);
        if (t1[(i1 + 1) % 3] != v) {
            std::swap(u, v);
            i1 = idx_of(t1, u);
            if (i1 < 0 || t1[(i1 + 1) % 3] != v) continue;
        }
        const int i2 = idx_of(t2, v);
        if (i2 < 0 || t2[(i2 + 1) % 3] != u) continue;  // inconsistent winding

        const Vec3& pu = m.verts[u];
        const Vec3& pv = m.verts[v];
        const Vec3& pp = m.verts[p_op];
        const Vec3& pq = m.verts[q_op];

        const TriShape s1 = tri_shape(pu, pv, pp);
        const TriShape s2 = tri_shape(pv, pu, pq);
        const double l1 =
            std::sqrt(s1.nx * s1.nx + s1.ny * s1.ny + s1.nz * s1.nz);
        const double l2 =
            std::sqrt(s2.nx * s2.nx + s2.ny * s2.ny + s2.nz * s2.nz);
        if (l1 < 1e-9 || l2 < 1e-9) continue;
        if ((s1.nx * s2.nx + s1.ny * s2.ny + s1.nz * s2.nz) / (l1 * l2) <
            cos_flat)
            continue;  // a real crease (stair riser)

        const TriShape n1 = tri_shape(pu, pq, pp);
        const TriShape n2 = tri_shape(pv, pp, pq);
        const double worst_new = std::max(n1.aspect, n2.aspect);
        const double worst_old = std::max(s1.aspect, s2.aspect);
        if (worst_new >= worst_old - 1e-6) continue;  // not an improvement
        if (worst_new > max_aspect_new ||
            std::max(n1.longest, n2.longest) > p.max_edge)
            continue;
        const double nr = std::max(n1.ratio, n2.ratio);
        if (nr > p.max_ratio && nr >= std::max(s1.ratio, s2.ratio)) continue;
        // Both new triangles must face the same way as the old pair.
        if (n1.nx * s1.nx + n1.ny * s1.ny + n1.nz * s1.nz <= 0.0) continue;
        if (n2.nx * s1.nx + n2.ny * s1.ny + n2.nz * s1.nz <= 0.0) continue;

        m.tris[ti * 3 + 0] = u;
        m.tris[ti * 3 + 1] = q_op;
        m.tris[ti * 3 + 2] = p_op;
        m.tris[tj * 3 + 0] = v;
        m.tris[tj * 3 + 1] = p_op;
        m.tris[tj * 3 + 2] = q_op;

        edge_tris.erase(k);
        all_edges.erase(k);
        const uint64_t pqk = edge_key(p_op, q_op);
        edge_tris[pqk] = {ti, tj};
        all_edges.insert(pqk);

        // Two outer edges change owner.
        const int32_t moves[2][4] = {{v, p_op, ti, tj}, {u, q_op, tj, ti}};
        for (auto& mv : moves) {
            auto eit = edge_tris.find(edge_key(mv[0], mv[1]));
            if (eit == edge_tris.end()) continue;
            for (auto& t : eit->second)
                if (t == mv[2]) t = mv[3];
        }
        const int32_t touched[4][2] = {
            {u, p_op}, {v, q_op}, {v, p_op}, {u, q_op}};
        for (auto& e : touched) {
            const uint64_t ek = edge_key(e[0], e[1]);
            auto eit = edge_tris.find(ek);
            if (eit != edge_tris.end() && eit->second.size() == 2)
                stack.push_back(ek);
        }
        ++flips;
    }
    return flips;
}

// ---------------------------------------------------------------------------
// Pass 3: tangential relaxation.  Mirrors spanmesh._smooth_pass.
// ---------------------------------------------------------------------------

struct SmoothParams {
    double max_edge;
    double max_aspect;
    double max_ratio;
};

int smooth_pass(Mesh& m, const SmoothParams& p,
                const std::vector<uint8_t>& pinned) {
    const double cos_flat = std::cos(kDeg5Rad);

    std::unordered_map<int32_t, std::vector<int32_t>> vtris;
    std::unordered_map<int32_t, std::unordered_set<int32_t>> neighbours;
    std::unordered_map<uint64_t, int> edge_count;
    std::vector<int32_t> order;  // first-appearance order, as Python dicts give

    for (size_t t = 0; t < m.tri_count(); ++t) {
        if (!m.alive[t]) continue;
        const int32_t a = m.tris[t * 3 + 0];
        const int32_t b = m.tris[t * 3 + 1];
        const int32_t c = m.tris[t * 3 + 2];
        const int32_t vs[3] = {a, b, c};
        for (const int32_t v : vs) vtris[v].push_back(static_cast<int32_t>(t));
        const int32_t pairs[3][2] = {{a, b}, {b, c}, {c, a}};
        for (auto& e : pairs) {
            if (neighbours.find(e[0]) == neighbours.end()) order.push_back(e[0]);
            neighbours[e[0]].insert(e[1]);
            if (neighbours.find(e[1]) == neighbours.end()) order.push_back(e[1]);
            neighbours[e[1]].insert(e[0]);
            edge_count[edge_key(e[0], e[1])] += 1;
        }
    }

    std::unordered_set<int32_t> boundary;
    for (const auto& kv : edge_count) {
        if (kv.second != 1) continue;
        boundary.insert(static_cast<int32_t>(kv.first >> 32));
        boundary.insert(static_cast<int32_t>(kv.first & 0xFFFFFFFFu));
    }

    int moved = 0;
    for (const int32_t v : order) {
        if (boundary.count(v) || pinned[v]) continue;
        auto rit = vtris.find(v);
        if (rit == vtris.end() || rit->second.empty()) continue;
        const std::vector<int32_t>& ring = rit->second;

        // Average normal; bail if the one-ring is not genuinely flat.
        double ax = 0.0, ay = 0.0, az = 0.0;
        bool bad = false;
        std::vector<double> norms;
        std::vector<double> old_ratios;
        norms.reserve(ring.size() * 3);
        old_ratios.reserve(ring.size());
        for (const int32_t t : ring) {
            const TriShape s = tri_shape(m.verts[m.tris[t * 3 + 0]],
                                         m.verts[m.tris[t * 3 + 1]],
                                         m.verts[m.tris[t * 3 + 2]]);
            const double ln =
                std::sqrt(s.nx * s.nx + s.ny * s.ny + s.nz * s.nz);
            if (ln < 1e-9) {
                bad = true;
                break;
            }
            norms.push_back(s.nx / ln);
            norms.push_back(s.ny / ln);
            norms.push_back(s.nz / ln);
            old_ratios.push_back(s.ratio);
            ax += s.nx / ln;
            ay += s.ny / ln;
            az += s.nz / ln;
        }
        if (bad) continue;
        const double ln = std::sqrt(ax * ax + ay * ay + az * az);
        if (ln < 1e-9) continue;
        ax /= ln;
        ay /= ln;
        az /= ln;

        bool flat = true;
        for (size_t i = 0; i < old_ratios.size(); ++i) {
            if (norms[i * 3] * ax + norms[i * 3 + 1] * ay +
                    norms[i * 3 + 2] * az <
                cos_flat) {
                flat = false;
                break;
            }
        }
        if (!flat) continue;

        const auto& nbs = neighbours[v];
        if (nbs.empty()) continue;
        double cx = 0.0, cy = 0.0, cz = 0.0;
        // Sum in sorted order so the centroid is independent of hash layout.
        std::vector<int32_t> ns(nbs.begin(), nbs.end());
        std::sort(ns.begin(), ns.end());
        for (const int32_t n : ns) {
            cx += m.verts[n].x;
            cy += m.verts[n].y;
            cz += m.verts[n].z;
        }
        const double inv = 1.0 / static_cast<double>(ns.size());
        cx *= inv;
        cy *= inv;
        cz *= inv;

        const Vec3& pv = m.verts[v];
        double dx = cx - pv.x, dy = cy - pv.y, dz = cz - pv.z;
        const double dn = dx * ax + dy * ay + dz * az;
        dx = (dx - dn * ax) * 0.5;
        dy = (dy - dn * ay) * 0.5;
        dz = (dz - dn * az) * 0.5;
        if (dx * dx + dy * dy + dz * dz < 1.0) continue;

        const Vec3 nv3{pv.x + dx, pv.y + dy, pv.z + dz};

        bool ok = true;
        for (size_t ri = 0; ri < ring.size(); ++ri) {
            const int32_t t = ring[ri];
            const int32_t ia = m.tris[t * 3 + 0];
            const int32_t ib = m.tris[t * 3 + 1];
            const int32_t ic = m.tris[t * 3 + 2];
            const Vec3 pa = (ia == v) ? nv3 : m.verts[ia];
            const Vec3 pb = (ib == v) ? nv3 : m.verts[ib];
            const Vec3 pc = (ic == v) ? nv3 : m.verts[ic];
            const TriShape s = tri_shape(pa, pb, pc);
            if (s.aspect > p.max_aspect || s.longest > p.max_edge ||
                s.nx * ax + s.ny * ay + s.nz * az <= 0.0) {
                ok = false;
                break;
            }
            if (s.ratio > p.max_ratio && s.ratio >= old_ratios[ri]) {
                ok = false;
                break;
            }
        }
        if (!ok) continue;

        m.verts[v] = nv3;
        ++moved;
    }
    return moved;
}

// Rebuild tris[] as a dense list of surviving, non-degenerate triangles with
// vertices resolved through the union-find.  Mirrors the tail of
// _collapse_pass.
void compact_after_collapse(Mesh& m) {
    std::vector<int32_t> out;
    out.reserve(m.tris.size());
    for (size_t t = 0; t < m.tri_count(); ++t) {
        if (!m.alive[t]) continue;
        int32_t a, b, c;
        m.tri_verts(t, a, b, c);
        if (a == b || b == c || a == c) continue;
        out.push_back(a);
        out.push_back(b);
        out.push_back(c);
    }
    m.tris.swap(out);
    m.alive.assign(m.tris.size() / 3, 1);
    // Identity map: indices in tris[] are now already roots.
    for (size_t i = 0; i < m.vmap.size(); ++i) m.vmap[i] = static_cast<int32_t>(i);
}

}  // namespace

// ---------------------------------------------------------------------------
// Python entry point
// ---------------------------------------------------------------------------

static PyObject* py_decimate(PyObject* self, PyObject* args) {
    PyObject *verts_obj, *tris_obj, *pinned_obj;
    double cs, max_edge, target_err, max_aspect, max_ratio;
    int passes;

    if (!PyArg_ParseTuple(args, "OOOdddddi", &verts_obj, &tris_obj, &pinned_obj,
                          &cs, &max_edge, &target_err, &max_aspect, &max_ratio,
                          &passes))
        return nullptr;

    PyArrayObject* varr = reinterpret_cast<PyArrayObject*>(
        PyArray_FROMANY(verts_obj, NPY_DOUBLE, 2, 2, NPY_ARRAY_C_CONTIGUOUS));
    if (!varr) return nullptr;
    PyArrayObject* tarr = reinterpret_cast<PyArrayObject*>(
        PyArray_FROMANY(tris_obj, NPY_INT32, 2, 2, NPY_ARRAY_C_CONTIGUOUS));
    if (!tarr) {
        Py_DECREF(varr);
        return nullptr;
    }
    PyArrayObject* parr = reinterpret_cast<PyArrayObject*>(
        PyArray_FROMANY(pinned_obj, NPY_BOOL, 1, 1, NPY_ARRAY_C_CONTIGUOUS));
    if (!parr) {
        Py_DECREF(varr);
        Py_DECREF(tarr);
        return nullptr;
    }

    Mesh m;
    const npy_intp nv = PyArray_DIM(varr, 0);
    const npy_intp nt = PyArray_DIM(tarr, 0);
    const double* vp = static_cast<const double*>(PyArray_DATA(varr));
    const int32_t* tp = static_cast<const int32_t*>(PyArray_DATA(tarr));
    const uint8_t* pp = static_cast<const uint8_t*>(PyArray_DATA(parr));

    m.verts.resize(nv);
    for (npy_intp i = 0; i < nv; ++i)
        m.verts[i] = Vec3{vp[i * 3], vp[i * 3 + 1], vp[i * 3 + 2]};
    m.tris.assign(tp, tp + nt * 3);
    m.alive.assign(nt, 1);
    m.vmap.resize(nv);
    for (npy_intp i = 0; i < nv; ++i) m.vmap[i] = static_cast<int32_t>(i);

    std::vector<uint8_t> pinned(nv, 0);
    for (npy_intp i = 0; i < PyArray_DIM(parr, 0) && i < nv; ++i)
        pinned[i] = pp[i] ? 1 : 0;

    Py_DECREF(varr);
    Py_DECREF(tarr);
    Py_DECREF(parr);

    CollapseParams cp{target_err, max_aspect, max_ratio, max_edge, cs};
    FlipParams fp{max_edge, max_aspect, max_ratio};
    SmoothParams sp{max_edge, max_aspect, max_ratio};
    std::unordered_map<int32_t, double> acc;

    Py_BEGIN_ALLOW_THREADS

    for (int i = 0; i < passes; ++i) {
        CollapsePass cpass(m, cp, pinned, acc);
        const int n_col = cpass.run();
        compact_after_collapse(m);
        const int n_flip = flip_pass_impl(m, fp);
        const int n_smooth = smooth_pass(m, sp, pinned);
        if (n_col + n_flip + n_smooth == 0) break;
    }
    flip_pass_impl(m, fp);

    Py_END_ALLOW_THREADS

    // Compact: keep only referenced vertices, renumbered in first-use order to
    // match the Python `sorted({i for t in tris for i in t})` remap.
    std::vector<int32_t> used;
    used.reserve(m.tris.size());
    for (const int32_t v : m.tris) used.push_back(v);
    std::sort(used.begin(), used.end());
    used.erase(std::unique(used.begin(), used.end()), used.end());

    std::unordered_map<int32_t, int32_t> remap;
    remap.reserve(used.size() * 2);
    for (size_t i = 0; i < used.size(); ++i)
        remap[used[i]] = static_cast<int32_t>(i);

    npy_intp vdims[2] = {static_cast<npy_intp>(used.size()), 3};
    PyObject* out_v = PyArray_SimpleNew(2, vdims, NPY_DOUBLE);
    if (!out_v) return nullptr;
    double* ovp = static_cast<double*>(
        PyArray_DATA(reinterpret_cast<PyArrayObject*>(out_v)));
    for (size_t i = 0; i < used.size(); ++i) {
        const Vec3& p = m.verts[used[i]];
        ovp[i * 3] = p.x;
        ovp[i * 3 + 1] = p.y;
        ovp[i * 3 + 2] = p.z;
    }

    const npy_intp ntri = static_cast<npy_intp>(m.tris.size() / 3);
    npy_intp tdims[2] = {ntri, 3};
    PyObject* out_t = PyArray_SimpleNew(2, tdims, NPY_INT32);
    if (!out_t) {
        Py_DECREF(out_v);
        return nullptr;
    }
    int32_t* otp = static_cast<int32_t*>(
        PyArray_DATA(reinterpret_cast<PyArrayObject*>(out_t)));
    for (size_t i = 0; i < m.tris.size(); ++i) otp[i] = remap[m.tris[i]];

    return Py_BuildValue("NN", out_v, out_t);
}

static PyMethodDef Methods[] = {
    {"decimate", py_decimate, METH_VARARGS,
     "decimate(verts, tris, pinned, cs, max_edge, target_err, max_aspect, "
     "max_ratio, passes) -> (verts, tris)"},
    {nullptr, nullptr, 0, nullptr}};

static struct PyModuleDef Module = {PyModuleDef_HEAD_INIT, "_navmesh_native",
                                    "Native navmesh mesh decimation", -1,
                                    Methods};

PyMODINIT_FUNC PyInit__navmesh_native(void) {
    import_array();
    return PyModule_Create(&Module);
}
