// The corridor cleanup stage, natively: corridor_clean.finalize and the tail
// of corridor.build_corridors after it (attach scraps, degenerate drop, CCW
// winding, ledge resolution).
//
// Mirrors the Python operation for operation.  Every dict the reference walks
// is walked in its insertion order (EdgeOwners), every sort keeps its key and
// stability, and the collision oracles (ground_ok, ledge reach, surface) are
// answered from the cell's own samplers (CellGeo) with the reference's
// candidate order.

#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <unordered_map>
#include <unordered_set>

#include "cellgeo.h"
#include "clean.h"
#include "params.h"
#include "pyconv.h"
#include "pybuf.h"
#include "pymath.h"
#include "seal.h"
#include "stitch.h"

namespace navgrow {

namespace {

using Cell = std::pair<long long, long long>;

struct CellHash {
    size_t operator()(const Cell& c) const {
        return std::hash<long long>()(c.first * 1000003LL ^ c.second);
    }
};

template <class T>
using CellGrid = std::unordered_map<Cell, std::vector<T>, CellHash>;

inline Cell cell_of(double x, double y, double cell) {
    return {(long long)py_floordiv(x, cell), (long long)py_floordiv(y, cell)};
}

// The plan bucket range a triangle's box spans.
struct Span { long long gx0, gx1, gy0, gy1; };

Span tri_span(const Vert& a, const Vert& b, const Vert& c, double cell) {
    return {(long long)py_floordiv(std::min({a.x, b.x, c.x}), cell),
            (long long)py_floordiv(std::max({a.x, b.x, c.x}), cell),
            (long long)py_floordiv(std::min({a.y, b.y, c.y}), cell),
            (long long)py_floordiv(std::max({a.y, b.y, c.y}), cell)};
}

double plan_area(const std::vector<Vert>& V, const Tri& t) {
    return std::fabs(plan_area2(V[(size_t)t[0]], V[(size_t)t[1]], V[(size_t)t[2]])) * 0.5;
}

std::vector<Tri> keep_alive(const std::vector<Tri>& T, const std::vector<char>& alive) {
    std::vector<Tri> out;
    for (size_t i = 0; i < T.size(); ++i) if (alive[i]) out.push_back(T[i]);
    return out;
}

std::vector<Tri> without(const std::vector<Tri>& T, const std::vector<char>& drop) {
    std::vector<Tri> out;
    for (size_t i = 0; i < T.size(); ++i) if (!drop[i]) out.push_back(T[i]);
    return out;
}

// ---------------------------------------------------------------------------
// Weld (_weld_coincident)
// ---------------------------------------------------------------------------
void weld_coincident(std::vector<Vert>& V, std::vector<Tri>& T) {
    struct K {
        double x, y, z;
        bool operator==(const K& o) const { return x == o.x && y == o.y && z == o.z; }
    };
    struct H {
        size_t operator()(const K& k) const {
            const std::hash<double> h;
            return h(k.x) ^ (h(k.y) * 31) ^ (h(k.z) * 1009);
        }
    };
    std::unordered_map<K, int, H> key_to_vid;
    std::vector<int> remap(V.size());
    std::vector<Vert> out;
    for (size_t i = 0; i < V.size(); ++i) {
        const Vert& v = V[i];
        const K k{py_round1(v.x), py_round1(v.y), v.znp ? np_round1(v.z) : py_round1(v.z)};
        auto ins = key_to_vid.emplace(k, (int)out.size());
        if (ins.second) out.push_back(v);
        remap[i] = ins.first->second;
    }
    std::vector<Tri> ot;
    for (const Tri& t : T) {
        const Tri r{remap[(size_t)t[0]], remap[(size_t)t[1]], remap[(size_t)t[2]]};
        if (distinct(r)) ot.push_back(r);
    }
    V.swap(out);
    T.swap(ot);
}

// ---------------------------------------------------------------------------
// Make manifold (_make_manifold, _overshare_drops, _covers_any_sample)
// ---------------------------------------------------------------------------
constexpr double kWalkedCell = 128.0;

CellGrid<Sample> bucket_samples(const std::vector<Sample>& S, double cell) {
    CellGrid<Sample> g;
    for (const Sample& s : S) g[cell_of(s.x, s.y, cell)].push_back(s);
    return g;
}

bool covers_any_sample(const std::vector<Vert>& V, const Tri& t, const CellGrid<Sample>& grid) {
    if (grid.empty()) return false;
    const Vert &A = V[(size_t)t[0]], &B = V[(size_t)t[1]], &C = V[(size_t)t[2]];
    const double d = (B.y - C.y) * (A.x - C.x) + (C.x - B.x) * (A.y - C.y);
    if (std::fabs(d) < 1e-9) return false;
    const Span sp = tri_span(A, B, C, kWalkedCell);
    const double tol = P().MAX_CLIMB + 8.0;
    for (long long gx = sp.gx0; gx <= sp.gx1; ++gx) {
        for (long long gy = sp.gy0; gy <= sp.gy1; ++gy) {
            auto it = grid.find({gx, gy});
            if (it == grid.end()) continue;
            for (const Sample& s : it->second) {
                const double l0 = ((B.y - C.y) * (s.x - C.x) + (C.x - B.x) * (s.y - C.y)) / d;
                const double l1 = ((C.y - A.y) * (s.x - C.x) + (A.x - C.x) * (s.y - C.y)) / d;
                const double l2 = 1.0 - l0 - l1;
                if (l0 < -0.01 || l1 < -0.01 || l2 < -0.01) continue;
                if (std::isnan(s.z) || std::fabs(l0 * A.z + l1 * B.z + l2 * C.z - s.z) <= tol)
                    return true;
            }
        }
    }
    return false;
}

std::vector<char> overshare_drops(const std::vector<Vert>& V, const std::vector<Tri>& T,
                                  const EdgeOwners& owners, const CellGrid<Sample>& walked) {
    std::vector<int> nbr(T.size(), 0);
    for (const auto& e : owners.order) {
        if (e.tris.size() != 2) continue;
        nbr[(size_t)e.tris[0]]++;
        nbr[(size_t)e.tris[1]]++;
    }
    std::vector<char> drop(T.size(), 0);
    struct Rank { bool covers; int nbr; double area; };
    for (const auto& e : owners.order) {
        if (e.tris.size() <= 2) continue;
        std::vector<int> live;
        for (int ti : e.tris) if (!drop[(size_t)ti]) live.push_back(ti);
        if (live.size() <= 2) continue;
        std::vector<std::pair<Rank, int>> ranked;
        for (int ti : live)
            ranked.push_back({{covers_any_sample(V, T[(size_t)ti], walked), nbr[(size_t)ti],
                               plan_area(V, T[(size_t)ti])}, ti});
        std::stable_sort(ranked.begin(), ranked.end(), [](const auto& a, const auto& b) {
            if (a.first.covers != b.first.covers) return a.first.covers > b.first.covers;
            if (a.first.nbr != b.first.nbr) return a.first.nbr > b.first.nbr;
            return a.first.area > b.first.area;
        });
        for (size_t k = 2; k < ranked.size(); ++k) drop[(size_t)ranked[k].second] = 1;
    }
    return drop;
}

std::vector<char> drops_that_keep_connectivity(const std::vector<Tri>& T,
                                               const std::vector<char>& drop) {
    std::vector<char> safe(T.size(), 0);
    const size_t base = component_count(T);
    for (size_t ti = 0; ti < T.size(); ++ti) {
        if (!drop[ti]) continue;
        std::vector<Tri> trial;
        for (size_t i = 0; i < T.size(); ++i) if (i != ti && !safe[i]) trial.push_back(T[i]);
        if (component_count(trial) <= base) safe[ti] = 1;
    }
    return safe;
}

void make_manifold(const std::vector<Vert>& V, std::vector<Tri>& T,
                   const std::vector<Sample>& pin_xy) {
    bool have_walked = false;
    CellGrid<Sample> walked;
    for (int round = 0; round < 6; ++round) {
        const EdgeOwners owners(T);
        bool over = false;
        for (const auto& e : owners.order) over = over || e.tris.size() > 2;
        if (!over) break;
        if (!have_walked) {
            walked = bucket_samples(pin_xy, kWalkedCell);
            have_walked = true;
        }
        const std::vector<char> drop = overshare_drops(V, T, owners, walked);
        std::vector<Tri> kept = without(T, drop);
        if (component_count(kept) <= component_count(T)) {
            T.swap(kept);
            continue;
        }
        const std::vector<char> safe = drops_that_keep_connectivity(T, drop);
        if (std::find(safe.begin(), safe.end(), 1) == safe.end()) break;
        T = without(T, safe);
    }
}

// ---------------------------------------------------------------------------
// Guarded drops (_live_neighbours, _neighbours_stay_linked, _drop_degenerate_guarded)
// ---------------------------------------------------------------------------
std::vector<int> live_neighbours(const std::vector<Tri>& T, const EdgeOwners& owners,
                                 const std::vector<char>& alive, int ti) {
    std::vector<int> out;
    const Tri& t = T[(size_t)ti];
    for (int k = 0; k < 3; ++k) {
        const EdgeOwners::Entry* e = owners.find(t[k], t[(k + 1) % 3]);
        if (!e) continue;
        for (int tj : e->tris) if (tj != ti && alive[(size_t)tj]) out.push_back(tj);
    }
    return out;
}

bool neighbours_stay_linked(const std::vector<Tri>& T, const EdgeOwners& owners,
                            const std::vector<char>& alive, int ti, int fuel) {
    const std::vector<int> nbrs = live_neighbours(T, owners, alive, ti);
    if (nbrs.size() <= 1) return true;
    std::unordered_set<int> target(nbrs.begin() + 1, nbrs.end());
    std::unordered_set<int> seen{nbrs[0], ti};
    std::vector<int> queue{nbrs[0]};
    while (!queue.empty() && !target.empty() && fuel) {
        --fuel;
        const int cur = queue.back();
        queue.pop_back();
        for (int tj : live_neighbours(T, owners, alive, cur)) {
            if (!seen.insert(tj).second) continue;
            target.erase(tj);
            queue.push_back(tj);
        }
    }
    return target.empty();
}

bool plan_degenerate(const std::vector<Vert>& V, const Tri& t) {
    return plan_area(V, t) < P().MIN_XY_FOOTPRINT;
}

void drop_degenerate_guarded(const std::vector<Vert>& V, std::vector<Tri>& T) {
    bool any = false;
    for (const Tri& t : T) any = any || plan_degenerate(V, t);
    if (!any) return;
    const EdgeOwners owners(T);
    std::vector<char> alive(T.size(), 1);
    for (size_t ti = 0; ti < T.size(); ++ti)
        if (plan_degenerate(V, T[ti]) && neighbours_stay_linked(T, owners, alive, (int)ti, 256))
            alive[ti] = 0;
    T = keep_alive(T, alive);
}

// ---------------------------------------------------------------------------
// Open flaps (cull_open_flaps)
// ---------------------------------------------------------------------------
constexpr double kPgCell = 128.0;
constexpr double kPgInsideFrac = 0.05;

bool carries_pathgrid(const std::vector<Vert>& V, const CellGrid<Sample>& pg, const Tri& t) {
    const Vert &A = V[(size_t)t[0]], &B = V[(size_t)t[1]], &C = V[(size_t)t[2]];
    const double d = (B.y - C.y) * (A.x - C.x) + (C.x - B.x) * (A.y - C.y);
    if (std::fabs(d) < 1e-9) return false;
    const Span sp = tri_span(A, B, C, kPgCell);
    for (long long gx = sp.gx0; gx <= sp.gx1; ++gx) {
        for (long long gy = sp.gy0; gy <= sp.gy1; ++gy) {
            auto it = pg.find({gx, gy});
            if (it == pg.end()) continue;
            for (const Sample& s : it->second) {
                const double l0 = ((B.y - C.y) * (s.x - C.x) + (C.x - B.x) * (s.y - C.y)) / d;
                const double l1 = ((C.y - A.y) * (s.x - C.x) + (A.x - C.x) * (s.y - C.y)) / d;
                if (std::min({l0, l1, 1.0 - l0 - l1}) >= kPgInsideFrac) return true;
            }
        }
    }
    return false;
}

// A free edge on the cell seam is a portal to the neighbour, never a rim.
void cull_open_flaps(const std::vector<Vert>& V, std::vector<Tri>& T,
                     const std::vector<Sample>& pin_xy, const double* seam) {
    if (T.empty()) return;
    const CellGrid<Sample> pg = bucket_samples(pin_xy, kPgCell);
    const EdgeOwners owners(T);
    std::vector<int> cands;
    for (size_t ti = 0; ti < T.size(); ++ti) {
        const Tri& t = T[ti];
        int free = 0;
        for (int k = 0; k < 3; ++k) {
            const int a = t[k], b = t[(k + 1) % 3];
            const Vert &va = V[(size_t)a], &vb = V[(size_t)b];
            if (owners.count(a, b) != 1
                || (on_seam_rect(va.x, va.y, seam) && on_seam_rect(vb.x, vb.y, seam))) continue;
            if (std::fabs(V[(size_t)a].z - V[(size_t)b].z) >= P().FLAP_EDGE_DROP) ++free;
        }
        if (free >= 2 && !(!pg.empty() && carries_pathgrid(V, pg, t))) cands.push_back((int)ti);
    }
    if (cands.empty()) return;
    std::vector<char> alive(T.size(), 1);
    for (int ti : cands)
        if (neighbours_stay_linked(T, owners, alive, ti, 128)) alive[(size_t)ti] = 0;
    T = keep_alive(T, alive);
}

// ---------------------------------------------------------------------------
// Ledge links (clean_validate.find_ledge_links and its push)
// ---------------------------------------------------------------------------
struct LedgePair { int hi, lo; double drop; Key lip; Tri hi_tri; };

std::vector<Key> boundary_edges(const std::vector<Tri>& T, const std::vector<int>& comp) {
    std::vector<Key> order;
    std::unordered_map<uint64_t, std::pair<size_t, int>> count;
    for (int ti : comp) {
        const Tri& t = T[(size_t)ti];
        for (int k = 0; k < 3; ++k) {
            const int a = t[k], b = t[(k + 1) % 3];
            auto ins = count.emplace(key64(a, b), std::make_pair(order.size(), 0));
            if (ins.second) order.push_back(edge_key(a, b));
            ins.first->second.second++;
        }
    }
    std::vector<Key> out;
    for (const Key& e : order)
        if (count[key64(e.first, e.second)].second == 1) out.push_back(e);
    return out;
}

int edge_tri(const std::vector<Tri>& T, const std::vector<int>& comp, int a, int b) {
    for (int ti : comp) {
        const Tri& t = T[(size_t)ti];
        for (int k = 0; k < 3; ++k) {
            const int u = t[k], w = t[(k + 1) % 3];
            if ((u == a && w == b) || (u == b && w == a)) return ti;
        }
    }
    return -1;
}

struct LedgeCand { double dxy; Key e1, e2; double z1, z2, drop; };

std::vector<LedgeCand> ledge_candidates(const std::vector<Vert>& V, const std::vector<Key>& bi,
                                        const std::vector<Key>& bj) {
    const NavParams& N = P();
    std::vector<LedgeCand> cands;
    for (const Key& e1 : bi) {
        const Vert &p1 = V[(size_t)e1.first], &q1 = V[(size_t)e1.second];
        for (const Key& e2 : bj) {
            const Vert &p2 = V[(size_t)e2.first], &q2 = V[(size_t)e2.second];
            const double dxy = std::min(
                std::max(py_hypot(p1.x - p2.x, p1.y - p2.y), py_hypot(q1.x - q2.x, q1.y - q2.y)),
                std::max(py_hypot(p1.x - q2.x, p1.y - q2.y), py_hypot(q1.x - p2.x, q1.y - p2.y)));
            if (dxy > N.ISLAND_BRIDGE_XY) continue;
            const double z1 = 0.5 * (p1.z + q1.z), z2 = 0.5 * (p2.z + q2.z);
            const double drop = std::fabs(z1 - z2);
            if (!(N.ISLAND_BRIDGE_MIN_DROP <= drop && drop <= N.ISLAND_BRIDGE_MAX_DROP)) continue;
            cands.push_back({dxy, e1, e2, z1, z2, drop});
        }
    }
    std::stable_sort(cands.begin(), cands.end(), [](const LedgeCand& a, const LedgeCand& b) {
        if (a.dxy != b.dxy) return a.dxy < b.dxy;
        if (a.e1 != b.e1) return a.e1 < b.e1;
        return a.e2 < b.e2;
    });
    return cands;
}

void link_pair(const std::vector<Vert>& V, const std::vector<Tri>& T,
               const std::vector<std::vector<int>>& comps, const std::vector<std::vector<Key>>& bounds,
               size_t i, size_t j, std::vector<LedgePair>& out) {
    std::unordered_set<int> used;
    int made = 0;
    for (const LedgeCand& c : ledge_candidates(V, bounds[i], bounds[j])) {
        if (made >= (int)P().LEDGE_LINKS_PER_PAIR) break;
        const int t1 = edge_tri(T, comps[i], c.e1.first, c.e1.second);
        const int t2 = edge_tri(T, comps[j], c.e2.first, c.e2.second);
        if (t1 < 0 || t2 < 0 || used.count(t1) || used.count(t2)) continue;
        used.insert(t1);
        used.insert(t2);
        ++made;
        if (c.z1 >= c.z2) out.push_back({t1, t2, c.drop, c.e1, T[(size_t)t1]});
        else out.push_back({t2, t1, c.drop, c.e2, T[(size_t)t2]});
    }
}

// corridor._ledge_reach: (floor run, wall first) from a lip along (dx, dy).
std::pair<double, bool> ledge_reach(CellGeo& G, double x, double y, double z, double dx,
                                    double dy, double limit) {
    const NavParams& N = P();
    const double step = N.LIP_MARCH_STEP;
    double d = 0.0;
    while (d < limit) {
        const double nd = d + step;
        const double mid = 0.5 * (d + nd);
        if (G.wall_at(x + dx * mid, y + dy * mid, dx, dy, -dy, dx,
                      z + N.RIBBON_GROW_SLAB_Z_BOTTOM, z + N.AGENT_HEIGHT,
                      0.5 * step + N.RIBBON_GROW_SLAB_DEPTH, N.RIBBON_GROW_SLAB_HALF_WIDTH))
            return {d, true};
        double s;
        if (!G.walk_at(x + dx * nd, y + dy * nd, z, &s) || std::fabs(s - z) > N.MAX_CLIMB)
            return {d, false};
        d = nd;
    }
    return {d, false};
}

bool push_lip(std::vector<Vert>& V, const LedgePair& lp, CellGeo& G, std::vector<char>& moved) {
    const int a = lp.lip.first, b = lp.lip.second;
    int apex = -1;
    for (int k : lp.hi_tri) if (k != a && k != b) { apex = k; break; }
    const double mx = 0.5 * (V[(size_t)a].x + V[(size_t)b].x);
    const double my = 0.5 * (V[(size_t)a].y + V[(size_t)b].y);
    const double mz = 0.5 * (V[(size_t)a].z + V[(size_t)b].z);
    const double ex = V[(size_t)b].x - V[(size_t)a].x, ey = V[(size_t)b].y - V[(size_t)a].y;
    const double run = py_hypot(ex, ey);
    if (run < 1e-6) return true;
    double nx = -ey / run, ny = ex / run;
    if ((V[(size_t)apex].x - mx) * nx + (V[(size_t)apex].y - my) * ny > 0.0) {
        nx = -nx;
        ny = -ny;
    }
    auto [dist, wall] = ledge_reach(G, mx, my, mz, nx, ny, P().ISLAND_BRIDGE_XY);
    if (wall) return false;
    dist -= P().LIP_STANDOFF;
    for (int k : {a, b}) {
        if (dist > 0.0 && !moved[(size_t)k]) {
            V[(size_t)k].x += nx * dist;
            V[(size_t)k].y += ny * dist;
            moved[(size_t)k] = 1;
        }
    }
    return true;
}

struct LedgeMark { P3 hi, lo; double drop; };

P3 centroid(const std::vector<Vert>& V, const Tri& t) {
    const Vert &a = V[(size_t)t[0]], &b = V[(size_t)t[1]], &c = V[(size_t)t[2]];
    return {(a.x + b.x + c.x) / 3.0, (a.y + b.y + c.y) / 3.0, (a.z + b.z + c.z) / 3.0};
}

// True when edge a-b lies on a side of the cell square `cell` (x0 y0 x1 y1): the neighbour cell's mesh
// continues it across the seam, so it is never a lip or a landing.
bool on_cell_border(const Vert& a, const Vert& b, const double* cell) {
    const double s = P().SEAM_BAND;
    auto near = [s](double u, double v, double c) { return std::fabs(u - c) <= s && std::fabs(v - c) <= s; };
    return near(a.x, b.x, cell[0]) || near(a.x, b.x, cell[2]) || near(a.y, b.y, cell[1]) || near(a.y, b.y, cell[3]);
}

std::vector<LedgeMark> find_ledge_marks(std::vector<Vert>& V, const std::vector<Tri>& T,
                                        CellGeo& G, const double* cell) {
    std::vector<LedgeMark> marks;
    if (T.empty()) return marks;
    const auto comps = components(T);
    if (comps.size() < 2) return marks;
    std::vector<std::vector<Key>> bounds;
    for (const auto& c : comps) {
        bounds.push_back(boundary_edges(T, c));
        if (cell)
            bounds.back().erase(std::remove_if(bounds.back().begin(), bounds.back().end(), [&](const Key& e) {
                return on_cell_border(V[(size_t)e.first], V[(size_t)e.second], cell);
            }), bounds.back().end());
    }
    std::vector<LedgePair> pairs;
    for (size_t i = 0; i < comps.size(); ++i)
        for (size_t j = i + 1; j < comps.size(); ++j) link_pair(V, T, comps, bounds, i, j, pairs);
    std::vector<char> moved(V.size(), 0);
    std::vector<LedgePair> kept;
    for (const LedgePair& lp : pairs)
        if (push_lip(V, lp, G, moved)) kept.push_back(lp);
    for (const LedgePair& lp : kept)
        marks.push_back({centroid(V, T[(size_t)lp.hi]), centroid(V, T[(size_t)lp.lo]), lp.drop});
    return marks;
}

// ---------------------------------------------------------------------------
// Islands and compaction (_drop_unreachable_islands, _compact)
// ---------------------------------------------------------------------------
void drop_unreachable_islands(const std::vector<Vert>& V, std::vector<Tri>& T,
                              const std::vector<P3>& doors, const double* bounds,
                              const std::vector<Sample>& pins) {
    auto comps = components(T);
    if (comps.size() <= 1) return;
    std::stable_sort(comps.begin(), comps.end(),
                     [](const auto& a, const auto& b) { return a.size() > b.size(); });
    const NavParams& N = P();
    const double dr2 = py_square(N.ISLAND_DOOR_RADIUS), dz = N.ISLAND_DOOR_ZTOL;
    const double margin = N.ISLAND_EDGE_MARGIN, pr2 = py_square(N.ISLAND_PGRD_RADIUS);
    auto reaches_exit = [&](const std::vector<int>& comp) {
        for (int ci : comp) {
            for (int i : T[(size_t)ci]) {
                const Vert& v = V[(size_t)i];
                for (const P3& d : doors)
                    if (py_square(v.x - d[0]) + py_square(v.y - d[1]) <= dr2
                        && std::fabs(v.z - d[2]) <= dz)
                        return true;
                for (const Sample& p : pins)
                    if (py_square(v.x - p.x) + py_square(v.y - p.y) <= pr2
                        && (std::isnan(p.z) || std::fabs(v.z - p.z) <= dz))
                        return true;
                if (bounds && (v.x - bounds[0] <= margin || bounds[2] - v.x <= margin
                               || v.y - bounds[1] <= margin || bounds[3] - v.y <= margin))
                    return true;
            }
        }
        return false;
    };
    std::vector<char> keep(T.size(), 0);
    for (int ti : comps[0]) keep[(size_t)ti] = 1;
    for (size_t c = 1; c < comps.size(); ++c)
        if (reaches_exit(comps[c])) for (int ti : comps[c]) keep[(size_t)ti] = 1;
    T = keep_alive(T, keep);
}

void compact(std::vector<Vert>& V, std::vector<Tri>& T) {
    std::vector<int> remap(V.size(), -1);
    for (const Tri& t : T) for (int i : t) remap[(size_t)i] = 0;
    std::vector<Vert> nv;
    for (size_t i = 0; i < V.size(); ++i) {
        if (remap[i] < 0) continue;
        remap[i] = (int)nv.size();
        nv.push_back({V[i].x, V[i].y, V[i].z, false});
    }
    for (Tri& t : T) for (int& i : t) i = remap[(size_t)i];
    V.swap(nv);
}

// ---------------------------------------------------------------------------
// Build tail (corridor._drop_attach_scraps, ccw_in_plan, _resolve_ledges)
// ---------------------------------------------------------------------------
bool tri_carries_door(const std::vector<Vert>& V, const Tri& t, const std::vector<P3>& pts) {
    const Vert &a = V[(size_t)t[0]], &b = V[(size_t)t[1]], &c = V[(size_t)t[2]];
    const double d = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y);
    if (std::fabs(d) < 1e-9) return false;
    for (const P3& p : pts) {
        const double l0 = ((b.y - c.y) * (p[0] - c.x) + (c.x - b.x) * (p[1] - c.y)) / d;
        const double l1 = ((c.y - a.y) * (p[0] - c.x) + (a.x - c.x) * (p[1] - c.y)) / d;
        const double l2 = 1.0 - l0 - l1;
        if (l0 >= -0.05 && l1 >= -0.05 && l2 >= -0.05
            && std::fabs(l0 * a.z + l1 * b.z + l2 * c.z - p[2]) <= 128.0)
            return true;
    }
    return false;
}

void drop_attach_scraps(const std::vector<Vert>& V, std::vector<Tri>& T,
                        const std::vector<P3>& keep_xy) {
    const auto comps = components(T);
    if (comps.size() <= 1) return;
    std::vector<char> drop(T.size(), 0);
    bool any = false;
    for (const auto& comp : comps) {
        if (comp.size() > 2) continue;
        bool carries = false;
        for (int ti : comp) carries = carries || tri_carries_door(V, T[(size_t)ti], keep_xy);
        if (carries) continue;
        for (int ti : comp) drop[(size_t)ti] = 1;
        any = true;
    }
    if (any) T = without(T, drop);
}

void ccw_in_plan(const std::vector<Vert>& V, std::vector<Tri>& T) {
    for (Tri& t : T) {
        const Vert &a = V[(size_t)t[0]], &b = V[(size_t)t[1]], &c = V[(size_t)t[2]];
        if ((b.x - a.x) * (c.y - a.y) - (c.x - a.x) * (b.y - a.y) < 0) t = {t[0], t[2], t[1]};
    }
}


std::vector<Ledge> resolve_ledges(const std::vector<Vert>& V, const std::vector<Tri>& T,
                                  const std::vector<LedgeMark>& marks) {
    std::vector<Ledge> out;
    if (marks.empty() || T.empty()) return out;
    std::vector<P3> cents;
    for (const Tri& t : T) cents.push_back(centroid(V, t));
    auto find = [&](const P3& m) {
        int best = -1;
        double bd = 0.0;
        for (size_t i = 0; i < cents.size(); ++i) {
            const P3& c = cents[i];
            const double d = py_square(c[0] - m[0]) + py_square(c[1] - m[1]) + py_square(c[2] - m[2]);
            if (d <= 1.0 && (best < 0 || d < bd)) { best = (int)i; bd = d; }
        }
        return best;
    };
    for (const LedgeMark& m : marks) {
        const int hi = find(m.hi), lo = find(m.lo);
        if (hi >= 0 && lo >= 0 && hi != lo) out.push_back({hi, lo, m.drop});
    }
    return out;
}

// ---------------------------------------------------------------------------
// The stage
// ---------------------------------------------------------------------------
Oracles cell_oracles(CellGeo& G, bool with_surface) {
    Oracles O;
    O.ground_ok = [&G](const Vert& a, const Vert& v, const Vert& b) {
        const NavParams& N = P();
        const double dx = b.x - a.x, dy = b.y - a.y;
        const double run = py_hypot(dx, dy);
        if (run < 1e-6) return false;
        const double tx = dx / run, ty = dy / run;
        const double off = (v.x - a.x) * -ty + (v.y - a.y) * tx;
        const double ux = off >= 0 ? -ty : ty, uy = off >= 0 ? tx : -tx;
        const double depth = 0.5 * std::fabs(off) + 1.0;
        const double cx = 0.5 * (a.x + b.x) + ux * depth;
        const double cy = 0.5 * (a.y + b.y) + uy * depth;
        const double z_lo = std::min({a.z, b.z, v.z}) + N.RIBBON_GROW_SLAB_Z_BOTTOM;
        const double z_hi = std::max({a.z, b.z, v.z}) + N.AGENT_HEIGHT;
        if (G.wall_at(cx, cy, ux, uy, tx, ty, z_lo, z_hi, depth, 0.5 * run)) return false;
        const long long n = std::max(1LL, (long long)py_floordiv(run, N.RIBBON_GROW_STEP));
        for (long long s = 0; s <= n; ++s) {
            const double t = (double)s / (double)n;
            const double z = a.z + (b.z - a.z) * t;
            double got;
            if (!G.walk_at(a.x + dx * t, a.y + dy * t, z, &got) || std::fabs(got - z) > N.MAX_CLIMB)
                return false;
        }
        return true;
    };
    if (with_surface && G.has_walkable) {
        O.surface = [&G](double x, double y, double near, double* z, bool* znp) {
            *znp = true;
            return G.surface(x, y, near, z);
        };
    }
    return O;
}

std::vector<Pin> finalize_pins(const CleanInput& in) {
    const NavParams& N = P();
    std::vector<Pin> pins;
    for (const P3& d : in.doors) pins.push_back({d[0], d[1], N.DECIMATE_PIN_CENTER_RADIUS});
    for (const P3& p : in.door_pins) pins.push_back({p[0], p[1], N.DECIMATE_PIN_RADIUS});
    for (const P3& n : in.node_pins) pins.push_back({n[0], n[1], N.DECIMATE_PIN_NODE_RADIUS});
    return pins;
}

void cull_slivers(std::vector<Vert>& V, std::vector<Tri>& T, const std::vector<Pin>& pins,
                  const CleanInput& in, double frac) {
    if (T.empty()) return;
    const double budget = frac * mesh_plan_area(V, T);
    const std::vector<int> keep = cull_slivers_mesh(V, T, pin_verts(V, pins), in.pin_xy,
                                                    in.has_bounds ? in.bounds : nullptr, budget);
    std::vector<Tri> out;
    for (int i : keep) out.push_back(T[(size_t)i]);
    T.swap(out);
}

void plain_floats(std::vector<Vert>& V) {
    for (Vert& v : V) v.znp = false;
}

// Twice the plan area of `t`, signed by which side of edge a-b its third corner lies.
double side_of(const std::vector<Vert>& V, const Tri& t, const Key& e) {
    const int c = t[0] != e.first && t[0] != e.second ? t[0]
                : t[1] != e.first && t[1] != e.second ? t[1] : t[2];
    const Vert &A = V[(size_t)e.first], &B = V[(size_t)e.second], &C = V[(size_t)c];
    return (B.x - A.x) * (C.y - A.y) - (B.y - A.y) * (C.x - A.x);
}

// Whether triangle `t` lies on `others` within a step of its height: its
// centroid and each corner drawn a tenth of the way in.
bool lies_on(const std::vector<Vert>& V, const Tri& t, const std::vector<Tri>& others) {
    const Vert &A = V[(size_t)t[0]], &B = V[(size_t)t[1]], &C = V[(size_t)t[2]];
    const double cx = (A.x + B.x + C.x) / 3.0, cy = (A.y + B.y + C.y) / 3.0,
                 cz = (A.z + B.z + C.z) / 3.0;
    std::vector<Sample> probes{{cx, cy, cz, 0.0, 0.0}};
    for (const Vert* p : {&A, &B, &C})
        probes.push_back({0.9 * p->x + 0.1 * cx, 0.9 * p->y + 0.1 * cy, 0.9 * p->z + 0.1 * cz,
                          0.0, 0.0});
    for (const Sample& s : probes) {
        const CellGrid<Sample> one = bucket_samples({s}, kWalkedCell);
        if (std::none_of(others.begin(), others.end(),
                         [&](const Tri& o) { return covers_any_sample(V, o, one); }))
            return false;
    }
    return true;
}

// `T` without triangle `ti` and whatever that cuts off the mesh, when each of
// them lies on the rest: a duplicate layer, not coverage.
bool drop_with_layer(const std::vector<Vert>& V, std::vector<Tri>& T, size_t ti) {
    std::vector<Tri> rest;
    for (size_t i = 0; i < T.size(); ++i) if (i != ti) rest.push_back(T[i]);
    if (!lies_on(V, T[ti], rest)) return false;
    const auto comps = components(rest);
    if (comps.size() <= component_count(T)) {
        T.swap(rest);
        return true;
    }
    std::vector<char> gone(rest.size(), 0);
    for (const auto& comp : comps) {
        if (comp.size() > 8) continue;
        std::vector<char> mine(rest.size(), 0);
        for (int i : comp) mine[(size_t)i] = 1;
        const std::vector<Tri> others = without(rest, mine);
        bool layer = true;
        for (int i : comp) layer = layer && lies_on(V, rest[(size_t)i], others);
        if (layer) for (int i : comp) gone[(size_t)i] = 1;
    }
    std::vector<Tri> out = without(rest, gone);
    if (component_count(out) > component_count(T) || !lies_on(V, T[ti], out)) return false;
    for (size_t i = 0; i < rest.size(); ++i)
        if (gone[i] && !lies_on(V, rest[i], out)) return false;
    T.swap(out);
    return true;
}

// Two triangles sharing an edge with both third corners on one side of it lie
// folded over each other, sheets welded where they overlap; the seam linker
// reads that edge as interior, so an exterior drops one of each pair, the
// smaller first, wherever that splits nothing off the mesh but a duplicate layer.
// See: docs/commentary/tes5_import_navmesh.md#folded-sheet-welds
void drop_folds(const std::vector<Vert>& V, std::vector<Tri>& T) {
    std::set<Key> kept;
    for (int step = 0; step < 64; ++step) {
        const EdgeOwners owners(T);
        const EdgeOwners::Entry* fold = nullptr;
        for (const auto& e : owners.order)
            if (e.tris.size() == 2 && !kept.count(e.key)
                && side_of(V, T[(size_t)e.tris[0]], e.key) * side_of(V, T[(size_t)e.tris[1]], e.key) > 0.0) {
                fold = &e;
                break;
            }
        if (!fold) return;
        const size_t a = (size_t)fold->tris[0], b = (size_t)fold->tris[1];
        const bool small_a = plan_area(V, T[a]) < plan_area(V, T[b]);
        if (!drop_with_layer(V, T, small_a ? a : b) && !drop_with_layer(V, T, small_a ? b : a))
            kept.insert(fold->key);
    }
}

CleanOutput finalize_impl(CleanInput in, CellGeo& G) {
    const NavParams& N = P();
    std::vector<Vert>& V = in.V;
    std::vector<Tri>& T = in.T;
    const double* bounds = in.has_bounds ? in.bounds : nullptr;
    weld_coincident(V, T);
    make_manifold(V, T, in.pin_xy);
    drop_degenerate_guarded(V, T);
    const std::vector<Pin> pins = finalize_pins(in);
    plain_floats(V);
    decimate_mesh(V, T, pins, bounds, (int)N.DECIMATE_ROUNDS, true, cell_oracles(G, true));
    cull_slivers(V, T, pins, in, N.CULL_SLIVER_AREA_FRAC);
    plain_floats(V);
    decimate_mesh(V, T, pins, bounds, 2, false, cell_oracles(G, false));
    cull_slivers(V, T, pins, in, N.CULL_SLIVER_AREA_FRAC * 0.4);
    cull_open_flaps(V, T, in.pin_xy, bounds);
    const std::vector<LedgeMark> marks = find_ledge_marks(V, T, G, bounds);
    make_manifold(V, T, in.pin_xy);
    plain_floats(V);
    T = split_t_junctions(V, stitch_shared_nodes(V, T));
    make_manifold(V, T, in.pin_xy);
    drop_unreachable_islands(V, T, in.doors, bounds, in.pin_xy);
    compact(V, T);
    drop_attach_scraps(V, T, in.keep_xy);
    drop_degenerate_guarded(V, T);
    if (bounds) {
        drop_folds(V, T);
        compact(V, T);
    }
    seal_mesh(V, T, G);
    ccw_in_plan(V, T);
    CleanOutput out;
    out.ledges = resolve_ledges(V, T, marks);
    out.V.swap(V);
    out.T.swap(T);
    return out;
}

}  // namespace

CleanOutput finalize(CleanInput in, CellGeo& G) { return finalize_impl(std::move(in), G); }

}  // namespace navgrow
