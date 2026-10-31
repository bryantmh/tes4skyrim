// Sheet stitching and T-junction splits (union_mesh.stitch_shared_nodes,
// union_mesh.split_t_junctions), operation for operation.
//
// The reference walks dicts in insertion order and sorts with stable keys;
// every container here reproduces the order the reference's output depends
// on.  Plan-overlap tests skip GEOS only with a kClipMargin margin.

#include "stitch.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <optional>
#include <unordered_map>
#include <unordered_set>

#include "clip_area.h"
#include "geos_api.h"
#include "params.h"
#include "pymath.h"

namespace navgrow {

namespace {

constexpr double kTsplitTol = 2.0;
constexpr double kTsplitCrackTol = 6.0;
constexpr double kTsplitZTol = 12.0;

double tri_area(const std::vector<Vert>& V, const Tri& t) {
    const Vert &a = V[(size_t)t[0]], &b = V[(size_t)t[1]], &c = V[(size_t)t[2]];
    return std::fabs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)) * 0.5;
}

double tri_span(const std::vector<Vert>& V, const Tri& t) {
    double best = 0.0;
    for (int k = 0; k < 3; ++k) {
        const Vert &p = V[(size_t)t[k]], &q = V[(size_t)t[(k + 1) % 3]];
        const double d = std::sqrt(py_square(p.x - q.x) + py_square(p.y - q.y)
                                   + py_square(p.z - q.z));
        best = std::max(best, d);
    }
    return best;
}

double round1(double v, bool np) { return np ? np_round1(v) : py_round1(v); }

struct RoundKey {
    double x, y, z;
    bool operator==(const RoundKey& o) const { return x == o.x && y == o.y && z == o.z; }
};

struct RoundKeyHash {
    size_t operator()(const RoundKey& k) const {
        const std::hash<double> h;
        return h(k.x) ^ (h(k.y) * 31) ^ (h(k.z) * 1009);
    }
};

// ---------------------------------------------------------------------------
// One stitch round's state
// ---------------------------------------------------------------------------
using ByComp = std::map<int, std::vector<std::pair<int, Key>>>;

struct Round {
    std::vector<Vert>& V;
    const std::vector<Tri>& T;
    std::vector<int> comp;
    std::vector<std::vector<int>> vtris, vcomp;
    std::unordered_map<int, std::vector<Key>> inc;
    std::unordered_map<uint64_t, int> counts, owner, extra;
    std::unordered_map<int, std::vector<Tri>> replaced;
    std::vector<Tri> added;
    EdgeOwners owners;

    Round(std::vector<Vert>& v, const std::vector<Tri>& t)
        : V(v), T(t), comp(tri_components(t)), owners(t) {
        for (const Tri& tr : T)
            for (int k = 0; k < 3; ++k) counts[key64(tr[k], tr[(k + 1) % 3])]++;
        vtris.assign(V.size(), {});
        vcomp.assign(V.size(), {});
        for (size_t ti = 0; ti < T.size(); ++ti) {
            const Tri& tr = T[ti];
            for (int i : tr) {
                std::vector<int>& vc = vcomp[(size_t)i];
                if (std::find(vc.begin(), vc.end(), comp[ti]) == vc.end()) vc.push_back(comp[ti]);
                vtris[(size_t)i].push_back((int)ti);
            }
            for (int k = 0; k < 3; ++k) {
                const int a = tr[k], b = tr[(k + 1) % 3];
                if (count(a, b) != 1) continue;
                owner[key64(a, b)] = comp[ti];
                inc[a].push_back(edge_key(a, b));
                inc[b].push_back(edge_key(a, b));
            }
        }
        for (auto& vc : vcomp) std::sort(vc.begin(), vc.end());
    }

    int count(int a, int b) const {
        auto it = counts.find(key64(a, b));
        return it == counts.end() ? 0 : it->second;
    }

    int add_mid(int p, int q) {
        const Vert &a = V[(size_t)p], &b = V[(size_t)q];
        V.push_back({0.5 * (a.x + b.x), 0.5 * (a.y + b.y), 0.5 * (a.z + b.z), a.znp || b.znp});
        vtris.emplace_back();
        vcomp.emplace_back();
        return (int)V.size() - 1;
    }

    bool open_fan(int i, int c, ByComp& by_comp);
    bool bridge_ok(const Tri& tri, bool allow_overlap) const;
    bool try_bridge(const std::vector<std::pair<int, Key>>& base,
                    const std::vector<std::pair<int, Key>>& other, bool allow_overlap);
    void halve_long_border(int c, const ByComp& by_comp);
};

// _open_fan.
bool Round::open_fan(int i, int c, ByComp& by_comp) {
    std::vector<int> cand;
    for (int ti : vtris[(size_t)i])
        if (comp[(size_t)ti] == c && !replaced.count(ti)) cand.push_back(ti);
    std::stable_sort(cand.begin(), cand.end(), [&](int x, int y) {
        return -tri_area(V, T[(size_t)x]) < -tri_area(V, T[(size_t)y]);
    });
    const NavParams& N = P();
    for (int ti : cand) {
        const Tri& t = T[(size_t)ti];
        const int k = (int)(std::find(t.begin(), t.end(), i) - t.begin());
        const int p = t[(k + 1) % 3], q = t[(k + 2) % 3];
        std::vector<int> own, nb;
        if (const EdgeOwners::Entry* e = owners.find(p, q))
            for (int tj : e->tris) if (tj != ti) own.push_back(tj);
        for (int tj : own) if (!replaced.count(tj)) nb.push_back(tj);
        if (nb.size() < own.size()) continue;
        const Vert &vp = V[(size_t)p], &vq = V[(size_t)q];
        if (std::fabs(vp.z - vq.z) > std::max(N.MAX_CLIMB, 0.7 * py_hypot(vp.x - vq.x, vp.y - vq.y)))
            continue;
        if (tri_area(V, t) < 4.0 * N.MIN_XY_FOOTPRINT) continue;
        const int mid = add_mid(p, q);
        replaced[ti] = {Tri{i, p, mid}, Tri{i, mid, q}};
        for (int tj : nb) {
            const Tri& tt = T[(size_t)tj];
            int opp = -1, n_opp = 0;
            for (int x : tt) if (x != p && x != q) { opp = x; ++n_opp; }
            if (n_opp == 1) replaced[tj] = {Tri{opp, p, mid}, Tri{opp, mid, q}};
        }
        const Key key = edge_key(i, mid);
        owner[key64(i, mid)] = c;
        counts[key64(i, mid)] = 1;
        by_comp[c].push_back({i, key});
        counts.erase(key64(p, q));
        return true;
    }
    return false;
}

// shapely Polygon(plan).is_valid for a triangle; only a near-degenerate one asks GEOS.
bool tri_poly_valid(const double xy[6]) {
    if (std::fabs(tri_area2(xy)) * 0.5 > kClipMargin) return true;
    GeosApi* G = geos();
    GeosOwned g(G, make_polygon(G, xy, 3));
    return g.g && G->is_valid(G->ctx(), g.g) == 1;
}

// shapely Polygon(plan): valid with area > eps.
bool tri_poly_exceeds(const double xy[6], double eps) {
    const double area = std::fabs(tri_area2(xy)) * 0.5;
    if (area <= eps - kClipMargin) return false;
    if (area > eps + kClipMargin) return true;
    GeosApi* G = geos();
    GeosOwned g(G, make_polygon(G, xy, 3));
    if (!g.g || G->is_valid(G->ctx(), g.g) != 1) return false;
    double a = 0.0;
    return G->area(G->ctx(), g.g, &a) && a > eps;
}

// cp.intersection(tp).area > eps for two valid triangles.
bool overlap_exceeds(const double a[6], const double b[6], double eps) {
    double area = 0.0, cx, cy;
    if (!tri_overlap(a, b, &area, &cx, &cy)) area = 0.0;
    if (area > eps + kClipMargin) return true;
    if (area < eps - kClipMargin) return false;
    GeosApi* G = geos();
    GeosOwned pa(G, make_polygon(G, a, 3)), pb(G, make_polygon(G, b, 3));
    if (!pa.g || !pb.g) return false;
    GeosOwned inter(G, G->intersection(G->ctx(), pa.g, pb.g));
    double ar = 0.0;
    return inter.g && G->area(G->ctx(), inter.g, &ar) && ar > eps;
}

// _tri_overlaps_mesh over every triangle of the round's soup.
bool tri_overlaps_mesh(const std::vector<Vert>& V, const std::vector<Tri>& T,
                       const Tri& cand, double eps) {
    const Vert &pa = V[(size_t)cand[0]], &pb = V[(size_t)cand[1]], &pc = V[(size_t)cand[2]];
    const double cxy[6] = {pa.x, pa.y, pb.x, pb.y, pc.x, pc.y};
    if (!tri_poly_exceeds(cxy, eps)) return false;
    const double minx = std::min({pa.x, pb.x, pc.x}) - 1.0, maxx = std::max({pa.x, pb.x, pc.x}) + 1.0;
    const double miny = std::min({pa.y, pb.y, pc.y}) - 1.0, maxy = std::max({pa.y, pb.y, pc.y}) + 1.0;
    const double zlo = std::min({pa.z, pb.z, pc.z}) - 40.0, zhi = std::max({pa.z, pb.z, pc.z}) + 40.0;
    for (const Tri& t : T) {
        const Vert &qa = V[(size_t)t[0]], &qb = V[(size_t)t[1]], &qc = V[(size_t)t[2]];
        if (std::max({qa.x, qb.x, qc.x}) < minx || std::min({qa.x, qb.x, qc.x}) > maxx
            || std::max({qa.y, qb.y, qc.y}) < miny || std::min({qa.y, qb.y, qc.y}) > maxy
            || std::max({qa.z, qb.z, qc.z}) < zlo || std::min({qa.z, qb.z, qc.z}) > zhi)
            continue;
        const double txy[6] = {qa.x, qa.y, qb.x, qb.y, qc.x, qc.y};
        if (tri_poly_valid(txy) && overlap_exceeds(cxy, txy, eps)) return true;
    }
    return false;
}

// _bridge_ok.
bool Round::bridge_ok(const Tri& tri, bool allow_overlap) const {
    if (tri_span(V, tri) > 160.0) return false;
    double plan_span = 0.0;
    double zmin = V[(size_t)tri[0]].z, zmax = zmin;
    for (int k = 0; k < 3; ++k) {
        const Vert &p = V[(size_t)tri[k]], &q = V[(size_t)tri[(k + 1) % 3]];
        plan_span = std::max(plan_span, py_hypot(p.x - q.x, p.y - q.y));
        zmin = std::min(zmin, p.z);
        zmax = std::max(zmax, p.z);
    }
    if (zmax - zmin > std::max(P().MAX_CLIMB, 0.7 * plan_span)) return false;
    for (int k = 0; k < 3; ++k) {
        const uint64_t e = key64(tri[k], tri[(k + 1) % 3]);
        auto ex = extra.find(e);
        if (count(tri[k], tri[(k + 1) % 3]) + (ex == extra.end() ? 0 : ex->second) >= 2)
            return false;
    }
    return !tri_overlaps_mesh(V, T, tri, allow_overlap ? 250.0 : 2.0);
}

// _try_bridge.
bool Round::try_bridge(const std::vector<std::pair<int, Key>>& base,
                       const std::vector<std::pair<int, Key>>& other, bool allow_overlap) {
    for (const auto& [i0, k0] : base) {
        const int a0 = k0.second == i0 ? k0.first : k0.second;
        for (const auto& [i1, k1] : other) {
            const int a1 = k1.second == i1 ? k1.first : k1.second;
            Tri tri{a0, i0, a1};
            if (!distinct(tri)) tri = {a0, i0, i1};
            if (!distinct(tri)) continue;
            if (!bridge_ok(tri, allow_overlap)) continue;
            for (int k = 0; k < 3; ++k) extra[key64(tri[k], tri[(k + 1) % 3])]++;
            added.push_back(tri);
            return true;
        }
    }
    return false;
}

// _halve_long_border.
void Round::halve_long_border(int c, const ByComp& by_comp) {
    auto it = by_comp.find(c);
    if (it == by_comp.end()) return;
    bool have = false;
    double best_ln = 0.0;
    Key bkey;
    for (const auto& [i, key] : it->second) {
        const int far = key.second == i ? key.first : key.second;
        const double ln = py_hypot(V[(size_t)far].x - V[(size_t)i].x, V[(size_t)far].y - V[(size_t)i].y);
        if (ln > 80.0 && (!have || ln < best_ln)) {
            have = true;
            best_ln = ln;
            bkey = key;
        }
    }
    if (!have) return;
    const int p = bkey.first, q = bkey.second;
    int owner_t = -1;
    for (int ti : vtris[(size_t)p])
        if (!replaced.count(ti) && has(T[(size_t)ti], q)) { owner_t = ti; break; }
    if (owner_t < 0) return;
    const Tri t = T[(size_t)owner_t];
    for (int k = 0; k < 3; ++k) {
        const int u = t[k], w = t[(k + 1) % 3];
        if (!((u == p && w == q) || (u == q && w == p))) continue;
        const int cc = t[(k + 2) % 3];
        const int m = add_mid(p, q);
        replaced[owner_t] = {Tri{u, m, cc}, Tri{m, w, cc}};
        counts.erase(key64(p, q));
        for (const uint64_t nk : {key64(u, m), key64(m, w)}) {
            counts[nk] = 1;
            owner[nk] = c;
        }
        return;
    }
}

// _stitch_round: 0 finished, 1 unchanged, 2 changed.
int stitch_round(std::vector<Vert>& V, std::vector<Tri>& T, bool allow_overlap) {
    T = fuse_coincident(V, T);
    Round R(V, T);
    bool any_border = false;
    for (const auto& kv : R.counts) any_border = any_border || kv.second == 1;
    if (!any_border) return 0;
    std::vector<int> junctions;
    for (size_t i = 0; i < R.vcomp.size(); ++i)
        if (R.vcomp[i].size() > 1) junctions.push_back((int)i);
    for (int i0v : junctions) {
        ByComp by_comp;
        auto inc = R.inc.find(i0v);
        if (inc != R.inc.end())
            for (const Key& key : inc->second) by_comp[R.owner[key64(key.first, key.second)]].push_back({i0v, key});
        const std::vector<int> cs = R.vcomp[(size_t)i0v];
        for (int c : cs)
            if (!by_comp.count(c)) R.open_fan(i0v, c, by_comp);
        if (by_comp.size() < 2) continue;
        const int base_c = by_comp.begin()->first;
        for (auto it = std::next(by_comp.begin()); it != by_comp.end(); ++it) {
            const int other_c = it->first;
            if (R.try_bridge(by_comp[base_c], by_comp[other_c], allow_overlap)) continue;
            R.halve_long_border(base_c, by_comp);
            R.halve_long_border(other_c, by_comp);
        }
    }
    if (R.added.empty() && R.replaced.empty()) return 1;
    std::vector<Tri> out;
    for (size_t ti = 0; ti < T.size(); ++ti) {
        auto it = R.replaced.find((int)ti);
        if (it == R.replaced.end()) {
            if (distinct(T[ti])) out.push_back(T[ti]);
        } else {
            for (const Tri& t : it->second) if (distinct(t)) out.push_back(t);
        }
    }
    for (const Tri& t : R.added) if (distinct(t)) out.push_back(t);
    T.swap(out);
    return 2;
}

// ---------------------------------------------------------------------------
// T-junction splits
// ---------------------------------------------------------------------------
std::vector<int> hanging_on_edge(const std::vector<Vert>& V,
                                 const std::unordered_map<uint64_t, std::vector<int>>& grid,
                                 const std::unordered_map<uint64_t, int>& counts,
                                 const std::vector<char>& bverts, Key edge, double cell) {
    const int a = edge.first, b = edge.second;
    const Vert &pa = V[(size_t)a], &pb = V[(size_t)b];
    const double dx = pb.x - pa.x, dy = pb.y - pa.y, dz = pb.z - pa.z;
    const double span2 = dx * dx + dy * dy;
    std::vector<std::pair<double, int>> hits;
    if (span2 < 1e-9) return {};
    auto cnt = [&](int u, int w) {
        auto it = counts.find(key64(u, w));
        return it == counts.end() ? 0 : it->second;
    };
    const long long gx1 = (long long)py_floordiv(std::max(pa.x, pb.x), cell);
    const long long gy1 = (long long)py_floordiv(std::max(pa.y, pb.y), cell);
    for (long long gx = (long long)py_floordiv(std::min(pa.x, pb.x), cell); gx <= gx1; ++gx) {
        for (long long gy = (long long)py_floordiv(std::min(pa.y, pb.y), cell); gy <= gy1; ++gy) {
            auto it = grid.find(((uint64_t)(uint32_t)gx << 32) | (uint32_t)gy);
            if (it == grid.end()) continue;
            for (int i : it->second) {
                if (i == a || i == b) continue;
                const Vert& p = V[(size_t)i];
                const double s = ((p.x - pa.x) * dx + (p.y - pa.y) * dy) / span2;
                if (!(0.02 < s && s < 0.98)) continue;
                const double qx = pa.x + dx * s, qy = pa.y + dy * s, qz = pa.z + dz * s;
                const double r = bverts[(size_t)i] ? kTsplitCrackTol : kTsplitTol;
                if (py_square(p.x - qx) + py_square(p.y - qy) > r * r
                    || std::fabs(p.z - qz) > kTsplitZTol)
                    continue;
                if (cnt(a, i) <= 1 && cnt(i, b) <= 1) hits.push_back({s, i});
            }
        }
    }
    std::sort(hits.begin(), hits.end());
    std::vector<int> out;
    for (const auto& h : hits) out.push_back(h.second);
    return out;
}

// _plan_splits + _apply_splits: false when nothing was split.
bool split_round(const std::vector<Vert>& V, std::vector<Tri>& T) {
    std::unordered_map<uint64_t, int> counts;
    for (const Tri& t : T)
        for (int k = 0; k < 3; ++k) counts[key64(t[k], t[(k + 1) % 3])]++;
    std::vector<char> bverts(V.size(), 0);
    bool any = false;
    for (const auto& kv : counts) {
        if (kv.second != 1) continue;
        any = true;
        bverts[(size_t)(kv.first >> 32)] = 1;
        bverts[(size_t)(uint32_t)kv.first] = 1;
    }
    if (!any) return false;
    const double cell = 64.0;
    std::unordered_map<uint64_t, std::vector<int>> grid;
    std::vector<char> used(V.size(), 0);
    for (const Tri& t : T) for (int i : t) used[(size_t)i] = 1;
    for (size_t i = 0; i < V.size(); ++i) {
        if (!used[i]) continue;
        const long long gx = (long long)py_floordiv(V[i].x, cell);
        const long long gy = (long long)py_floordiv(V[i].y, cell);
        grid[((uint64_t)(uint32_t)gx << 32) | (uint32_t)gy].push_back((int)i);
    }
    std::unordered_map<uint64_t, std::vector<int>> splits;
    for (const auto& kv : counts) {
        if (kv.second != 1) continue;
        const Key e((int)(kv.first >> 32), (int)(uint32_t)kv.first);
        std::vector<int> got = hanging_on_edge(V, grid, counts, bverts, e, cell);
        if (!got.empty()) splits[kv.first] = std::move(got);
    }
    if (splits.empty()) return false;
    std::vector<Tri> out;
    bool changed = false;
    for (const Tri& t : T) {
        bool fanned = false;
        for (int k = 0; k < 3 && !fanned; ++k) {
            const int a = t[k], b = t[(k + 1) % 3];
            auto it = splits.find(key64(a, b));
            if (it == splits.end()) continue;
            std::vector<int> seq{a};
            if (a < b) seq.insert(seq.end(), it->second.begin(), it->second.end());
            else seq.insert(seq.end(), it->second.rbegin(), it->second.rend());
            seq.push_back(b);
            const int opp = t[(k + 2) % 3];
            for (size_t m = 0; m + 1 < seq.size(); ++m) {
                const Tri f{seq[m], seq[m + 1], opp};
                if (distinct(f)) out.push_back(f);
            }
            fanned = changed = true;
        }
        if (!fanned && distinct(t)) out.push_back(t);
    }
    T.swap(out);
    return changed;
}

}  // namespace

std::vector<int> tri_components(const std::vector<Tri>& T) {
    std::vector<int> parent(T.size());
    for (size_t i = 0; i < T.size(); ++i) parent[i] = (int)i;
    auto find = [&](int x) {
        while (parent[(size_t)x] != x) {
            parent[(size_t)x] = parent[(size_t)parent[(size_t)x]];
            x = parent[(size_t)x];
        }
        return x;
    };
    const EdgeOwners own(T);
    for (const auto& e : own.order) {
        for (size_t i = 1; i < e.tris.size(); ++i) {
            const int ra = find(e.tris[0]), rb = find(e.tris[i]);
            if (ra != rb) parent[(size_t)ra] = rb;
        }
    }
    std::vector<int> out(T.size());
    for (size_t i = 0; i < T.size(); ++i) out[i] = find((int)i);
    return out;
}

std::vector<Tri> fuse_coincident(const std::vector<Vert>& V, const std::vector<Tri>& T) {
    std::vector<char> used(V.size(), 0);
    for (const Tri& t : T) for (int i : t) used[(size_t)i] = 1;
    std::unordered_map<RoundKey, int, RoundKeyHash> pos;
    std::vector<int> fuse(V.size(), -1);
    bool any = false;
    for (size_t i = 0; i < V.size(); ++i) {
        if (!used[i]) continue;
        const RoundKey k{py_round1(V[i].x), py_round1(V[i].y), round1(V[i].z, V[i].znp)};
        auto ins = pos.emplace(k, (int)i);
        if (!ins.second) {
            fuse[i] = ins.first->second;
            any = true;
        }
    }
    if (!any) return T;
    std::vector<Tri> out;
    for (const Tri& t : T) {
        Tri f;
        for (int k = 0; k < 3; ++k) f[k] = fuse[(size_t)t[k]] >= 0 ? fuse[(size_t)t[k]] : t[k];
        if (distinct(f)) out.push_back(f);
    }
    return out;
}

std::vector<Tri> stitch_shared_nodes(std::vector<Vert>& V, std::vector<Tri> T) {
    if (T.empty()) return T;
    bool allow_overlap = false;
    for (int round = 0; round < 30; ++round) {
        if (round >= 24) allow_overlap = true;
        const int got = stitch_round(V, T, allow_overlap);
        if (got == 0) break;
        if (got == 1) {
            if (allow_overlap) break;
            allow_overlap = true;
            continue;
        }
        allow_overlap = false;
    }
    return T;
}

std::vector<Tri> split_t_junctions(const std::vector<Vert>& V, std::vector<Tri> T) {
    for (int round = 0; round < 3; ++round)
        if (!split_round(V, T)) break;
    return T;
}

}  // namespace navgrow
