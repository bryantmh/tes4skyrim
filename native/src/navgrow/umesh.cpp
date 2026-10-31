// union_mesh / corridor_union mesh passes after triangulation: the sheet weld,
// the pathgrid-node merge, destack, wall drop, notch fill and point-attached
// drop.  Python dict orders that reach the output are kept explicitly.

#include "params.h"
#include "stitch.h"
#include "union.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <unordered_map>

namespace navgrow {

namespace {

long long ifloor(double a, double b) { return (long long)py_floordiv(a, b); }

std::vector<double> flat_verts(const std::vector<Vert>& v) {
    std::vector<double> out;
    out.reserve(v.size() * 3);
    for (const Vert& p : v) out.insert(out.end(), {p.x, p.y, p.z});
    return out;
}

// --------------------------------------------------------------------------
// _weld_sheets
// --------------------------------------------------------------------------

struct WeldEntry { int j; double x, y, z; int src; };
using WeldGrid = std::map<std::array<long long, 3>, std::vector<WeldEntry>>;

// _weld_match: (rep or -1, risky).
std::pair<int, bool> weld_match(const WeldGrid& grid, const Vert& v, int isrc,
                                const std::array<long long, 3>& g) {
    const double wr = P().WELD_R, xy2 = py_square(P().SAME_PART_WELD_XY);
    const long long reach = ifloor(P().MAX_CLIMB, wr) + 1;
    for (long long dx = -1; dx <= 1; ++dx)
        for (long long dy = -1; dy <= 1; ++dy)
            for (long long dz = -reach; dz <= reach; ++dz) {
                auto it = grid.find({g[0] + dx, g[1] + dy, g[2] + dz});
                if (it == grid.end()) continue;
                for (const WeldEntry& e : it->second) {
                    const double dxy2 = py_square(e.x - v.x) + py_square(e.y - v.y);
                    if (isrc != e.src && dxy2 <= xy2
                        && std::fabs(e.z - v.z) <= P().MAX_CLIMB)
                        return {e.j, false};
                    if (dxy2 + py_square(e.z - v.z) > wr * wr) continue;
                    return {e.j, isrc == e.src && dxy2 > xy2};
                }
            }
    return {-1, false};
}

std::vector<Tri3> weld_tris(const std::vector<Tri3>& tris, const std::vector<int>& remap) {
    std::vector<Tri3> out;
    for (const Tri3& t : tris) {
        const int a = remap[(size_t)t[0]], b = remap[(size_t)t[1]], c = remap[(size_t)t[2]];
        if (a != b && b != c && a != c) out.push_back({a, b, c});
    }
    return out;
}

// --------------------------------------------------------------------------
// _merge_at_pathgrid_nodes
// --------------------------------------------------------------------------

struct Merge {
    const std::vector<Vert>& V;
    std::vector<int> remap;
    std::map<int, std::set<int>> vcomp;
    std::map<std::pair<long long, long long>, std::vector<int>> buckets;
    double cell = 1.0;
    bool fresh = false;

    int resolve(int i) {
        while (remap[(size_t)i] != i) {
            remap[(size_t)i] = remap[(size_t)remap[(size_t)i]];
            i = remap[(size_t)i];
        }
        return i;
    }

    // _merge_state.
    void rebuild(const std::vector<Tri3>& tris, double grid_r) {
        const std::vector<int> comp = tri_components(tris);
        vcomp.clear();
        std::vector<int> order;
        for (size_t ti = 0; ti < tris.size(); ++ti)
            for (int i : tris[ti]) {
                const int r = resolve(i);
                if (!vcomp.count(r)) order.push_back(r);
                vcomp[r].insert(comp[ti]);
            }
        cell = std::max(grid_r, 1.0);
        buckets.clear();
        for (int i : order)
            buckets[{ifloor(V[(size_t)i].x, cell), ifloor(V[(size_t)i].y, cell)}].push_back(i);
        fresh = true;
    }

    std::vector<Tri3> collapse(const std::vector<Tri3>& soup) {
        std::vector<Tri3> out;
        for (const Tri3& t : soup) {
            const Tri3 r{resolve(t[0]), resolve(t[1]), resolve(t[2])};
            if (r[0] != r[1] && r[1] != r[2] && r[0] != r[2]) out.push_back(r);
        }
        return out;
    }

    const std::set<int>& comps(int i) const {
        static const std::set<int> none;
        auto it = vcomp.find(i);
        return it == vcomp.end() ? none : it->second;
    }

    // _near_vertices.
    std::vector<int> near(double nx, double ny, double r) const {
        std::vector<int> out;
        const long long gx = ifloor(nx, cell), gy = ifloor(ny, cell);
        for (long long dx = -1; dx <= 1; ++dx)
            for (long long dy = -1; dy <= 1; ++dy) {
                auto it = buckets.find({gx + dx, gy + dy});
                if (it == buckets.end()) continue;
                for (int i : it->second)
                    if (py_hypot(V[(size_t)i].x - nx, V[(size_t)i].y - ny) <= r) out.push_back(i);
            }
        std::sort(out.begin(), out.end());
        return out;
    }

    // _storey_bands.
    std::vector<std::vector<int>> bands(std::vector<int> near_i) const {
        std::stable_sort(near_i.begin(), near_i.end(), [&](int a, int b) {
            const double za = V[(size_t)a].z, zb = V[(size_t)b].z;
            return za < zb || (za == zb && a < b);
        });
        std::vector<std::vector<int>> out{{near_i[0]}};
        for (size_t k = 1; k < near_i.size(); ++k) {
            const int i = near_i[k];
            if (V[(size_t)i].z - V[(size_t)out.back().back()].z <= P().STOREY_GAP_Z)
                out.back().push_back(i);
            else
                out.push_back({i});
        }
        return out;
    }

    // _weld_band.
    bool weld_band(const std::vector<int>& band, double nx, double ny) {
        std::map<int, std::vector<int>> comps_of;
        for (int i : band)
            for (int c : comps(i)) comps_of[c].push_back(i);
        if (band.size() < 2 || comps_of.size() < 2) return false;
        int keep = band[0];
        double keep_d = py_hypot(V[(size_t)keep].x - nx, V[(size_t)keep].y - ny);
        for (int i : band) {
            const double d = py_hypot(V[(size_t)i].x - nx, V[(size_t)i].y - ny);
            if (d < keep_d || (d == keep_d && i < keep)) { keep = i; keep_d = d; }
        }
        const std::set<int> keep_comps = comps(keep);
        std::vector<int> anchor;
        for (int i : band)
            for (int c : comps(i))
                if (keep_comps.count(c)) { anchor.push_back(i); break; }
        bool welded = false;
        for (const auto& kv : comps_of) {
            if (keep_comps.count(kv.first)) continue;
            bool have = false;
            double bd = 0.0;
            int bi = 0, bj = 0;
            for (int i : kv.second)
                for (int j : anchor) {
                    const Vert& p = V[(size_t)i];
                    const Vert& q = V[(size_t)j];
                    const double d2 = py_square(p.x - q.x) + py_square(p.y - q.y)
                                    + py_square(p.z - q.z);
                    if (!have || d2 < bd || (d2 == bd && (i < bi || (i == bi && j < bj)))) {
                        have = true; bd = d2; bi = i; bj = j;
                    }
                }
            if (!have || bd > py_square(P().RIBBON_HALF_WIDTH)) continue;
            if (bi != bj) {
                remap[(size_t)bi] = bj;
                welded = true;
            }
        }
        return welded;
    }
};

// --------------------------------------------------------------------------
// _adjacency_probe
// --------------------------------------------------------------------------

struct Adjacency {
    const std::vector<Tri3>& T;
    const std::vector<char>& alive;
    std::map<std::pair<int, int>, std::vector<int>> edge_tris;

    Adjacency(const std::vector<Tri3>& t, const std::vector<char>& a) : T(t), alive(a) {
        for (size_t ti = 0; ti < T.size(); ++ti)
            for (int k = 0; k < 3; ++k)
                edge_tris[key(T[ti][(size_t)k], T[ti][(size_t)((k + 1) % 3)])].push_back((int)ti);
    }
    static std::pair<int, int> key(int a, int b) {
        return a < b ? std::make_pair(a, b) : std::make_pair(b, a);
    }
    std::vector<int> neighbours(int ti) const {
        std::vector<int> out;
        for (int k = 0; k < 3; ++k) {
            auto it = edge_tris.find(key(T[(size_t)ti][(size_t)k], T[(size_t)ti][(size_t)((k + 1) % 3)]));
            for (int tj : it->second)
                if (tj != ti && alive[(size_t)tj]) out.push_back(tj);
        }
        return out;
    }
    bool removable(int ti) const {
        const std::vector<int> nbrs = neighbours(ti);
        if (nbrs.size() <= 1) return true;
        std::set<int> target(nbrs.begin() + 1, nbrs.end());
        std::set<int> seen{nbrs[0], ti};
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
        return target.empty();
    }
};

std::vector<Tri3> keep_alive(const std::vector<Tri3>& t, const std::vector<char>& alive) {
    std::vector<Tri3> out;
    for (size_t i = 0; i < t.size(); ++i)
        if (alive[i]) out.push_back(t[i]);
    return out;
}

// --------------------------------------------------------------------------
// _drop_walls
// --------------------------------------------------------------------------

using PgGrid = std::map<std::pair<long long, long long>, std::vector<P3>>;

PgGrid pathgrid_samples(const Group& strips, const Land* land) {
    PgGrid grid;
    const double cell = P().PG_CELL;
    for (const UStrip* s : strips) {
        const double ax = s->a[0], ay = s->a[1], bx = s->b[0], by = s->b[1];
        const double run = py_hypot(bx - ax, by - ay);
        const long long n = std::max(1LL, ifloor(run, 32.0));
        for (long long k = 0; k <= n; ++k) {
            const double t = (double)k / (double)n;
            const double px = ax + (bx - ax) * t, py = ay + (by - ay) * t;
            grid[{ifloor(px, cell), ifloor(py, cell)}].push_back(
                {px, py, height_on(*s, land, px, py)});
        }
    }
    return grid;
}

bool carries_pathgrid(const std::vector<Vert>& V, const Tri3& t, const PgGrid& grid) {
    const Vert &A = V[(size_t)t[0]], &B = V[(size_t)t[1]], &C = V[(size_t)t[2]];
    const double d = (B.y - C.y) * (A.x - C.x) + (C.x - B.x) * (A.y - C.y);
    if (std::fabs(d) < 1e-9) return false;
    const double cell = P().PG_CELL;
    const long long gx1 = ifloor(std::max({A.x, B.x, C.x}), cell);
    const long long gy0 = ifloor(std::min({A.y, B.y, C.y}), cell);
    const long long gy1 = ifloor(std::max({A.y, B.y, C.y}), cell);
    for (long long gx = ifloor(std::min({A.x, B.x, C.x}), cell); gx <= gx1; ++gx)
        for (long long gy = gy0; gy <= gy1; ++gy) {
            auto it = grid.find({gx, gy});
            if (it == grid.end()) continue;
            for (const P3& p : it->second) {
                const double l0 = ((B.y - C.y) * (p[0] - C.x) + (C.x - B.x) * (p[1] - C.y)) / d;
                const double l1 = ((C.y - A.y) * (p[0] - C.x) + (A.x - C.x) * (p[1] - C.y)) / d;
                const double l2 = 1.0 - l0 - l1;
                if (l0 < -0.02 || l1 < -0.02 || l2 < -0.02) continue;
                if (std::fabs(l0 * A.z + l1 * B.z + l2 * C.z - p[2]) <= 80.0) return true;
            }
        }
    return false;
}

std::vector<std::pair<double, int>> steep_triangles(const std::vector<Vert>& V,
                                                    const std::vector<Tri3>& T,
                                                    const PgGrid& grid, const Land* land) {
    std::vector<std::pair<double, int>> steep;
    for (size_t ti = 0; ti < T.size(); ++ti) {
        const Vert &a = V[(size_t)T[ti][0]], &b = V[(size_t)T[ti][1]], &c = V[(size_t)T[ti][2]];
        if (land && std::fabs(a.z - land->z(a.x, a.y)) < 0.5
            && std::fabs(b.z - land->z(b.x, b.y)) < 0.5
            && std::fabs(c.z - land->z(c.x, c.y)) < 0.5)
            continue;
        const double area2 = std::fabs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x));
        const double ux = b.x - a.x, uy = b.y - a.y, uz = b.z - a.z;
        const double wx = c.x - a.x, wy = c.y - a.y, wz = c.z - a.z;
        const double nx = uy * wz - uz * wy, ny = uz * wx - ux * wz, nz = ux * wy - uy * wx;
        const double area3 = std::sqrt(nx * nx + ny * ny + nz * nz);
        if (area3 <= 1e-9 || area2 / area3 >= P().WALL_SLOPE_COS) continue;
        if (!grid.empty() && carries_pathgrid(V, T[ti], grid)) continue;
        steep.push_back({area2 / area3, (int)ti});
    }
    return steep;
}

// --------------------------------------------------------------------------
// _fill_boundary_notches
// --------------------------------------------------------------------------

using LineGrid = std::map<std::pair<long long, long long>, std::vector<P3>>;

LineGrid walked_line_index(const Group& strips, double cellsz) {
    LineGrid lines;
    for (const UStrip* s : strips) {
        const double ax = s->a[0], ay = s->a[1], az = s->a[2];
        const double bx = s->b[0], by = s->b[1], bz = s->b[2];
        const long long n = std::max(1LL, (long long)(py_hypot(bx - ax, by - ay) / cellsz) + 1);
        for (long long i = 0; i <= n; ++i) {
            const double f = (double)i / (double)n;
            const double x = ax + (bx - ax) * f, y = ay + (by - ay) * f;
            lines[{ifloor(x, cellsz), ifloor(y, cellsz)}].push_back({x, y, az + (bz - az) * f});
        }
    }
    return lines;
}

bool near_line(const LineGrid& lines, double cellsz, double x, double y, double z) {
    const long long gx = ifloor(x, cellsz), gy = ifloor(y, cellsz);
    for (long long dx = -1; dx <= 1; ++dx)
        for (long long dy = -1; dy <= 1; ++dy) {
            auto it = lines.find({gx + dx, gy + dy});
            if (it == lines.end()) continue;
            for (const P3& p : it->second)
                if (py_square(p[0] - x) + py_square(p[1] - y) <= cellsz * cellsz
                    && std::fabs(p[2] - z) <= P().STOREY_GAP_Z)
                    return true;
        }
    return false;
}

// Python's edge-count dict: counts in first-seen order.
struct EdgeCounts {
    std::vector<std::pair<int, int>> order;
    std::map<std::pair<int, int>, int> n;
    explicit EdgeCounts(const std::vector<Tri3>& tris) {
        for (const Tri3& t : tris)
            for (int k = 0; k < 3; ++k) add(Adjacency::key(t[(size_t)k], t[(size_t)((k + 1) % 3)]));
    }
    void add(const std::pair<int, int>& e) {
        auto it = n.find(e);
        if (it == n.end()) { order.push_back(e); n[e] = 1; }
        else ++it->second;
    }
    int get(const std::pair<int, int>& e) const {
        auto it = n.find(e);
        return it == n.end() ? 0 : it->second;
    }
};

// _notch_triangle; false when it is not a notch.
bool notch_triangle(const std::vector<Vert>& V, const EdgeCounts& counts, int apex,
                    int p, int q, const LineGrid& lines, double cellsz, Tri3* out) {
    const Vert &va = V[(size_t)apex], &vp = V[(size_t)p], &vq = V[(size_t)q];
    const double gap = py_dist(vp.x, vp.y, vq.x, vq.y);
    if (gap < 1e-6 || gap > P().NOTCH_MAX_MOUTH) return false;
    const double side = std::min(py_dist(va.x, va.y, vp.x, vp.y), py_dist(va.x, va.y, vq.x, vq.y));
    if (side < 1e-6 || side < gap * P().NOTCH_MIN_DEPTH_RATIO) return false;
    if (counts.get(Adjacency::key(p, q)) >= 1) return false;
    if (!near_line(lines, cellsz, (va.x + vp.x + vq.x) / 3.0, (va.y + vp.y + vq.y) / 3.0,
                   (va.z + vp.z + vq.z) / 3.0))
        return false;
    const double cross = (vp.x - va.x) * (vq.y - va.y) - (vq.x - va.x) * (vp.y - va.y);
    if (std::fabs(cross) < 1.0) return false;
    *out = cross > 0 ? Tri3{apex, p, q} : Tri3{apex, q, p};
    return true;
}

std::vector<Tri3> notch_round(const std::vector<Vert>& V, const std::vector<Tri3>& tris,
                              const LineGrid& lines, double cellsz) {
    EdgeCounts counts(tris);
    std::vector<int> apexes;
    std::map<int, std::vector<int>> at;
    auto link = [&](int a, int b) {
        if (!at.count(a)) apexes.push_back(a);
        at[a].push_back(b);
    };
    for (const auto& e : counts.order)
        if (counts.get(e) == 1) { link(e.first, e.second); link(e.second, e.first); }
    std::vector<Tri3> added;
    if (apexes.empty()) return added;
    std::vector<Tri3> cands;
    for (int apex : apexes) {
        const auto& ends = at[apex];
        Tri3 t;
        if (ends.size() == 2 && notch_triangle(V, counts, apex, ends[0], ends[1], lines, cellsz, &t))
            cands.push_back(t);
    }
    const std::vector<char> hit = notch_overlaps_mesh(flat_verts(V), tris, cands,
                                                      P().STOREY_GAP_Z);
    std::map<Tri3, bool> stacks;
    for (size_t k = 0; k < cands.size(); ++k) stacks[cands[k]] = hit[k] != 0;
    std::set<int> used;
    for (int apex : apexes) {
        const auto& ends = at[apex];
        if (ends.size() != 2 || used.count(apex) || used.count(ends[0]) || used.count(ends[1]))
            continue;
        Tri3 t;
        if (!notch_triangle(V, counts, apex, ends[0], ends[1], lines, cellsz, &t) || stacks[t])
            continue;
        added.push_back(t);
        used.insert(t.begin(), t.end());
        counts.add(Adjacency::key(ends[0], ends[1]));
    }
    return added;
}

}  // namespace

void weld_sheets(UMesh& M, const std::vector<int>& src) {
    if (M.v.empty()) return;
    const double cell = P().WELD_R;
    WeldGrid grid;
    std::vector<Vert> out;
    std::vector<int> remap(M.v.size());
    std::map<int, std::vector<int>> provisional;
    for (size_t i = 0; i < M.v.size(); ++i) {
        const Vert& v = M.v[i];
        const std::array<long long, 3> g{ifloor(v.x, cell), ifloor(v.y, cell), ifloor(v.z, cell)};
        auto [got, risky] = weld_match(grid, v, src[i], g);
        if (got < 0) {
            got = (int)out.size();
            out.push_back({v.x, v.y, v.z, false});
            grid[g].push_back({got, v.x, v.y, v.z, src[i]});
        } else if (risky) {
            provisional[got].push_back((int)i);
        }
        remap[i] = got;
    }
    std::vector<Tri3> welded = weld_tris(M.t, remap);
    if (!provisional.empty()) {
        std::vector<int> reps;
        std::vector<std::vector<int>> rep_tris;
        for (const auto& kv : provisional) reps.push_back(kv.first);
        rep_tris.resize(reps.size());
        for (size_t ti = 0; ti < welded.size(); ++ti)
            for (int k : welded[ti]) {
                auto it = std::lower_bound(reps.begin(), reps.end(), k);
                if (it != reps.end() && *it == k) rep_tris[(size_t)(it - reps.begin())].push_back((int)ti);
            }
        const std::vector<int> bad = bad_welds_mesh(flat_verts(out), welded, reps, rep_tris);
        for (int rep : bad)
            for (int i : provisional[rep]) {
                remap[(size_t)i] = (int)out.size();
                out.push_back(M.v[(size_t)i]);
            }
        if (!bad.empty()) welded = weld_tris(M.t, remap);
    }
    M.v = std::move(out);
    M.t = std::move(welded);
}

void merge_at_pathgrid_nodes(UMesh& M, const std::vector<std::pair<int, P2>>& nodes,
                             const std::vector<double>& node_half) {
    if (M.t.empty() || nodes.empty()) return;
    const double rhw = P().RIBBON_HALF_WIDTH;
    Merge G{M.v, {}, {}, {}, 1.0, false};
    G.remap.resize(M.v.size());
    for (size_t i = 0; i < M.v.size(); ++i) G.remap[i] = (int)i;
    double grid_r = rhw;
    for (double h : node_half) grid_r = std::max(grid_r, h);
    grid_r += rhw;
    for (size_t k = 0; k < nodes.size(); ++k) {
        const double nx = nodes[k].second[0], ny = nodes[k].second[1];
        const double r = std::max(node_half[k], rhw) + rhw;
        if (!G.fresh) G.rebuild(M.t, grid_r);
        const std::vector<int> near = G.near(nx, ny, r);
        if (near.size() < 2) continue;
        bool welded_any = false;
        for (const auto& band : G.bands(near))
            if (G.weld_band(band, nx, ny)) welded_any = true;
        if (welded_any) {
            M.t = G.collapse(M.t);
            G.fresh = false;
        }
    }
    M.t = G.collapse(M.t);
}

std::vector<Tri3> destack(const std::vector<Vert>& v, std::vector<Tri3> t) {
    if (t.size() < 2) return t;
    std::unordered_map<int, double> area;
    std::vector<StackedPair> pairs = stacked_pairs_mesh(flat_verts(v), t, 4.0, area);
    if (pairs.empty()) return t;
    std::vector<char> alive(t.size(), 1);
    const Adjacency adj(t, alive);
    std::sort(pairs.begin(), pairs.end(), [](const StackedPair& a, const StackedPair& b) {
        if (a.neg_area != b.neg_area) return a.neg_area < b.neg_area;
        if (a.ti != b.ti) return a.ti < b.ti;
        return a.tj < b.tj;
    });
    for (const StackedPair& p : pairs) {
        if (!alive[(size_t)p.ti] || !alive[(size_t)p.tj]) continue;
        const bool ti_small = area[p.ti] <= area[p.tj];
        const int small = ti_small ? p.ti : p.tj, big = ti_small ? p.tj : p.ti;
        for (int victim : {small, big})
            if (adj.removable(victim)) {
                alive[(size_t)victim] = 0;
                break;
            }
    }
    return keep_alive(t, alive);
}

std::vector<Tri3> drop_walls(const std::vector<Vert>& v, std::vector<Tri3> t,
                             const Group& strips, const Land* land) {
    if (t.empty()) return t;
    const PgGrid grid = pathgrid_samples(strips, land);
    std::vector<std::pair<double, int>> steep = steep_triangles(v, t, grid, land_of(strips, land));
    if (steep.empty()) return t;
    std::vector<char> alive(t.size(), 1);
    const Adjacency adj(t, alive);
    std::sort(steep.begin(), steep.end());
    for (const auto& s : steep)
        if (adj.removable(s.second)) alive[(size_t)s.second] = 0;
    return keep_alive(t, alive);
}

std::vector<Tri3> fill_boundary_notches(const std::vector<Vert>& v, std::vector<Tri3> t,
                                        const Group& strips) {
    if (strips.empty()) return t;
    const double cellsz = P().NOTCH_NEAR_LINE;
    const LineGrid lines = walked_line_index(strips, cellsz);
    for (int round = 0; round < 2; ++round) {
        const std::vector<Tri3> added = notch_round(v, t, lines, cellsz);
        if (added.empty()) break;
        t.insert(t.end(), added.begin(), added.end());
    }
    return t;
}

std::vector<Tri3> drop_point_attached(std::vector<Tri3> t) {
    for (int round = 0; round < 4; ++round) {
        const EdgeCounts counts(t);
        std::vector<Tri3> keep;
        for (const Tri3& tri : t)
            for (int k = 0; k < 3; ++k)
                if (counts.get(Adjacency::key(tri[(size_t)k], tri[(size_t)((k + 1) % 3)])) >= 2) {
                    keep.push_back(tri);
                    break;
                }
        if (keep.size() == t.size()) break;
        t = std::move(keep);
    }
    return t;
}

}  // namespace navgrow
