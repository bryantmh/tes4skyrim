// Door footprints (corridor_doors + corridor._door_quad_strip), routes for
// doors the corridor never reaches (door_routes) and spawn nodes
// (spawn_nodes).  Mirrors the Python statement for statement.

#include "cell.h"
#include "params.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <queue>
#include <tuple>

namespace navgrow {

namespace {

double clamp01(double t) { return std::max(0.0, std::min(1.0, t)); }

// spawn_nodes.floor_below; false for None.
bool floor_below(const CellCtx& C, double x, double y, double z, double* out) {
    bool have = false;
    for (double h : C.layers(x, y))
        if (h <= z + P().MAX_CLIMB) { *out = h; have = true; }
    return have;
}

// spawn_nodes.body_clear.
bool body_clear(const CellCtx& C, double x, double y, double z) {
    return !C.wall_hit(x, y, 1.0, 0.0, 0.0, 1.0, z + P().RIBBON_GROW_SLAB_Z_BOTTOM,
                       z + P().AGENT_HEIGHT, P().RIBBON_GROW_SLAB_HALF_WIDTH,
                       P().RIBBON_GROW_SLAB_HALF_WIDTH);
}

// --------------------------------------------------------------------------
// corridor_doors.door_footprints
// --------------------------------------------------------------------------

struct Footprint {
    bool has_base = false;
    P2 b0{}, b1{}, apex{};
    Ring poly;
    double z = 0.0, z_far = 0.0;
};

struct Probe {
    const UMesh& M;
    std::map<std::pair<long long, long long>, std::vector<int>> zgrid;
    std::vector<std::pair<int, int>> edges;   // sorted

    explicit Probe(const UMesh& m) : M(m) {
        std::set<std::pair<int, int>> es;
        for (size_t ti = 0; ti < M.t.size(); ++ti) {
            const Tri3& t = M.t[ti];
            double x0 = M.v[(size_t)t[0]].x, x1 = x0, y0 = M.v[(size_t)t[0]].y, y1 = y0;
            for (int k = 0; k < 3; ++k) {
                const Vert& p = M.v[(size_t)t[(size_t)k]];
                x0 = std::min(x0, p.x); x1 = std::max(x1, p.x);
                y0 = std::min(y0, p.y); y1 = std::max(y1, p.y);
                const int a = t[(size_t)k], b = t[(size_t)((k + 1) % 3)];
                es.insert({std::min(a, b), std::max(a, b)});
            }
            for (long long gx = (long long)py_floordiv(x0, 128.0); gx <= (long long)py_floordiv(x1, 128.0); ++gx)
                for (long long gy = (long long)py_floordiv(y0, 128.0); gy <= (long long)py_floordiv(y1, 128.0); ++gy)
                    zgrid[{gx, gy}].push_back((int)ti);
        }
        edges.assign(es.begin(), es.end());
    }

    // _mesh_z_near; false for None.
    bool z_near(double px, double py, double near_z, double ztol, double* out) const {
        auto it = zgrid.find({(long long)py_floordiv(px, 128.0), (long long)py_floordiv(py, 128.0)});
        if (it == zgrid.end()) return false;
        bool have = false;
        for (int ti : it->second) {
            const Tri3& t = M.t[(size_t)ti];
            const Vert &a = M.v[(size_t)t[0]], &b = M.v[(size_t)t[1]], &c = M.v[(size_t)t[2]];
            const double d = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y);
            if (std::fabs(d) < 1e-9) continue;
            const double l0 = ((b.y - c.y) * (px - c.x) + (c.x - b.x) * (py - c.y)) / d;
            const double l1 = ((c.y - a.y) * (px - c.x) + (a.x - c.x) * (py - c.y)) / d;
            const double l2 = 1.0 - l0 - l1;
            if (l0 < -0.02 || l1 < -0.02 || l2 < -0.02) continue;
            const double z = l0 * a.z + l1 * b.z + l2 * c.z;
            if (std::fabs(z - near_z) > ztol) continue;
            if (!have || std::fabs(z - near_z) < std::fabs(*out - near_z)) { *out = z; have = true; }
        }
        return have;
    }
};

struct Cand { double d2, mx, my; int a, b; };

// _blocked_between.
bool blocked_between(const CellCtx& C, double x0, double y0, double z0, double x1, double y1) {
    const double dx = x1 - x0, dy = y1 - y0;
    const double dist = py_hypot(dx, dy);
    if (dist < 1e-6) return false;
    const double ux = dx / dist, uy = dy / dist, tx = -uy, ty = ux;
    const double z_lo = z0 + P().RIBBON_GROW_SLAB_Z_BOTTOM, z_hi = z0 + P().AGENT_HEIGHT;
    const double step = P().RIBBON_GROW_STEP;
    const double start = P().DOOR_SELF_CLEARANCE, end = dist - P().DOOR_SELF_CLEARANCE;
    if (end <= start) return false;
    for (double q = start; q < end;) {
        const double seg = std::min(step, end - q);
        const double mid = q + 0.5 * seg;
        if (C.wall_hit(x0 + ux * mid, y0 + uy * mid, ux, uy, tx, ty, z_lo, z_hi,
                       0.5 * seg + P().RIBBON_GROW_SLAB_DEPTH, P().RIBBON_GROW_SLAB_HALF_WIDTH))
            return true;
        q += seg;
    }
    return false;
}

// _sides_disconnected.
bool sides_disconnected(const CellCtx& C, const DoorRef& d, double fcx, double fcy, double ztol) {
    if (C.nodes.empty() || C.edges.empty()) return false;
    std::vector<int> parent(C.nodes.size());
    for (size_t i = 0; i < parent.size(); ++i) parent[i] = (int)i;
    auto find = [&parent](int a) {
        while (parent[(size_t)a] != a) {
            parent[(size_t)a] = parent[(size_t)parent[(size_t)a]];
            a = parent[(size_t)a];
        }
        return a;
    };
    for (const Edge2& e : C.edges)
        if (e.first < (int)C.nodes.size() && e.second < (int)C.nodes.size()) {
            const int ri = find(e.first);
            parent[(size_t)ri] = find(e.second);
        }
    std::map<int, std::pair<double, int>> best;
    for (size_t ni = 0; ni < C.nodes.size(); ++ni) {
        const P3& n = C.nodes[ni];
        if (std::fabs(n[2] - d.z) > ztol) continue;
        const double proj = (n[0] - d.x) * fcx + (n[1] - d.y) * fcy;
        if (std::fabs(proj) < 1.0) continue;
        const int side = proj > 0 ? 1 : -1;
        const double d2 = py_square(n[0] - d.x) + py_square(n[1] - d.y);
        auto it = best.find(side);
        if (it == best.end() || d2 < it->second.first) best[side] = {d2, (int)ni};
    }
    if (best.size() < 2) return false;
    return find(best[1].second) != find(best[-1].second);
}

// _served_side.
int served_side(const CellCtx& C, const DoorRef& d) {
    if (C.nodes.empty()) return 0;
    const double ftx = crt_cos(d.rz), fty = -crt_sin(d.rz);
    bool have = false;
    double bd = 0.0, bp = 0.0;
    for (const P3& n : C.nodes) {
        const double proj = (n[0] - d.x) * ftx + (n[1] - d.y) * fty;
        if (std::fabs(proj) < 1.0) continue;
        const double d2n = py_square(n[0] - d.x) + py_square(n[1] - d.y);
        if (!have || d2n < bd) { have = true; bd = d2n; bp = proj; }
    }
    if (!have) return 0;
    return bp > 0 ? 1 : -1;
}

struct QuadCtx {
    double dx, dy, fx, fy, apex_depth, blx, bly, brx, bry, ztol;
};

// _sweep_quad.
Footprint sweep_quad(const Probe& pr, const QuadCtx& q, int side, const Cand& e, bool with_base) {
    Footprint fp;
    const double s_z = 0.5 * (pr.M.v[(size_t)e.a].z + pr.M.v[(size_t)e.b].z);
    double depth = std::fabs((e.mx - q.dx) * q.fx + (e.my - q.dy) * q.fy);
    depth = (double)side * std::max({depth + P().DOOR_OVERLAP, P().DOOR_MIN_DEPTH,
                                     q.apex_depth + P().DOOR_OVERLAP});
    const double sflx = q.blx + q.fx * depth, sfly = q.bly + q.fy * depth;
    const double sfrx = q.brx + q.fx * depth, sfry = q.bry + q.fy * depth;
    fp.has_base = with_base;
    fp.b0 = {q.blx, q.bly};
    fp.b1 = {q.brx, q.bry};
    fp.apex = {q.dx + q.fx * (double)side * q.apex_depth, q.dy + q.fy * (double)side * q.apex_depth};
    fp.poly = {{q.blx, q.bly}, {q.brx, q.bry}, {sfrx, sfry}, {sflx, sfly}};
    fp.z = s_z;
    if (!pr.z_near(0.5 * (sflx + sfrx), 0.5 * (sfly + sfry), s_z, q.ztol, &fp.z_far)) fp.z_far = s_z;
    return fp;
}

// _door_quads.
std::vector<Footprint> door_quads(const CellCtx& C, const Probe& pr, const DoorRef& d) {
    std::vector<Footprint> out;
    const double ztol = P().DOOR_QUAD_ZTOL;
    double w_half = d.width != 0.0 ? 0.5 * d.width : P().DOOR_LINE_HALF;
    w_half = std::min(w_half, P().DOOR_LINE_HALF_MAX);
    const double fcx = crt_cos(d.rz), fcy = -crt_sin(d.rz);
    const double ttx = crt_sin(d.rz), tty = crt_cos(d.rz);
    const double strip_half = w_half + P().RIBBON_HALF_WIDTH;
    const double br2 = py_square(P().DOOR_BRIDGE_RADIUS);
    std::vector<Cand> cands;
    for (const auto& ab : pr.edges) {
        const Vert &va = pr.M.v[(size_t)ab.first], &vb = pr.M.v[(size_t)ab.second];
        if (std::fabs(0.5 * (va.z + vb.z) - d.z) > ztol) continue;
        const double mx = 0.5 * (va.x + vb.x), my = 0.5 * (va.y + vb.y);
        const double d2 = py_square(mx - d.x) + py_square(my - d.y);
        if (d2 > br2) continue;
        if (std::fabs((mx - d.x) * ttx + (my - d.y) * tty) > strip_half) continue;
        cands.push_back({d2, mx, my, ab.first, ab.second});
    }
    std::stable_sort(cands.begin(), cands.end(),
                     [](const Cand& a, const Cand& b) { return a.d2 < b.d2; });
    std::map<int, Cand> best;
    for (const Cand& c : cands) {
        const int side = (c.mx - d.x) * fcx + (c.my - d.y) * fcy >= 0.0 ? 1 : -1;
        if (best.count(side)) continue;
        if (blocked_between(C, d.x, d.y, d.z, c.mx, c.my)) continue;
        best[side] = c;
        if (best.size() == 2) break;
    }
    if (best.empty()) return out;
    const int want = served_side(C, d);
    const int primary = best.count(want) ? want : best.begin()->first;
    const QuadCtx q{d.x, d.y, tty, -ttx, std::max(w_half, P().DOOR_TRI_MIN_DEPTH),
                    d.x + ttx * w_half, d.y + tty * w_half, d.x - ttx * w_half,
                    d.y - tty * w_half, ztol};
    out.push_back(sweep_quad(pr, q, primary, best[primary], true));
    const int other = -primary;
    if (!d.teleport && best.count(other) && sides_disconnected(C, d, fcx, fcy, ztol))
        out.push_back(sweep_quad(pr, q, other, best[other], false));
    return out;
}

// corridor_union._poly_strip, ramped by corridor._ramped_strip.
UStrip door_strip(const Ring& poly, double zb, double zf, double sweep, P2 bm, P2 fm) {
    UStrip s;
    s.a = {poly[0][0], poly[0][1], zb};
    s.b = {poly[1][0], poly[1][1], zb};
    double length = py_hypot(s.b[0] - s.a[0], s.b[1] - s.a[1]);
    if (length == 0.0) length = 1.0;
    const double ux = (s.b[0] - s.a[0]) / length, uy = (s.b[1] - s.a[1]) / length;
    s.na = s.a; s.nb = s.b;
    s.wx = -uy; s.wy = ux;
    s.half = 0.5 * length;
    s.len = length;
    s.has_poly = true;
    s.poly = poly;
    if (std::fabs(zf - zb) <= 1.0 || sweep <= 1e-6) return s;
    const double rx = (fm[0] - bm[0]) / sweep, ry = (fm[1] - bm[1]) / sweep;
    s.a = {bm[0], bm[1], zb};
    s.b = {fm[0], fm[1], zf};
    s.na = s.a; s.nb = s.b;
    s.wx = -ry; s.wy = rx;
    s.len = sweep;
    s.half = std::max(s.half, sweep) + 8.0;
    return s;
}

// corridor._door_quad_strip; false when it degenerates.
bool door_quad_strip(CellCtx& C, const Footprint& fp, const Group& steep, DoorGeometry& out) {
    Ring poly = fp.poly;
    const double zb = fp.z;
    double zf = fp.z_far;
    const P2 bm{0.5 * (poly[0][0] + poly[1][0]), 0.5 * (poly[0][1] + poly[1][1])};
    const P2 fm{0.5 * (poly[2][0] + poly[3][0]), 0.5 * (poly[2][1] + poly[3][1])};
    const double sweep = py_hypot(fm[0] - bm[0], fm[1] - bm[1]);
    if (std::fabs(zf - zb) > 0.5 * std::max(sweep, 1.0)) zf = zb;
    const auto qz = [zb, zf, sweep, bm, fm](double px, double py) {
        if (sweep < 1e-6) return zb;
        const double t = ((px - bm[0]) * (fm[0] - bm[0]) + (py - bm[1]) * (fm[1] - bm[1]))
                       / (sweep * sweep);
        return zb + (zf - zb) * clamp01(t);
    };
    if (!steep.empty() && poly.size() >= 3) {
        double ax, ay;
        if (fp.has_base) {
            ax = 0.5 * (fp.b0[0] + fp.b1[0]);
            ay = 0.5 * (fp.b0[1] + fp.b1[1]);
        } else {
            PySum sx, sy;
            for (const P2& p : poly) { sx.add(p[0]); sy.add(p[1]); }
            ax = sx.value() / (double)poly.size();
            ay = sy.value() / (double)poly.size();
        }
        poly = clip_flat_poly_off_level(poly, ax, ay, qz, steep);
    }
    if (poly.size() < 3) return false;
    C.store.push_back(door_strip(poly, zb, zf, sweep, bm, fm));
    out.strips.push_back(&C.store.back());
    if (!fp.has_base) return true;
    DoorEdge e;
    e.b0 = fp.b0; e.b1 = fp.b1; e.apex = fp.apex;
    e.has_apex = e.has_z = true;
    e.z = fp.z;
    out.edges.push_back(e);
    out.pins.push_back({fp.b0[0], fp.b0[1], fp.z});
    out.pins.push_back({fp.b1[0], fp.b1[1], fp.z});
    out.pins.push_back({0.5 * (fp.b0[0] + fp.b1[0]), 0.5 * (fp.b0[1] + fp.b1[1]), fp.z});
    out.pins.push_back({fp.apex[0], fp.apex[1], fp.z});
    return true;
}

// --------------------------------------------------------------------------
// door_routes
// --------------------------------------------------------------------------

struct Target { int i; double x, y, z; };

bool station_floor(const CellCtx& C, double x, double y, double z, double* out) {
    double floor;
    if (!floor_below(C, x, y, z, &floor) || z - floor > P().MAX_CLIMB || !body_clear(C, x, y, floor))
        return false;
    *out = floor;
    return true;
}

bool straight_walk(const CellCtx& C, const P3& s, const P3& e) {
    const double run = py_hypot(e[0] - s[0], e[1] - s[1]);
    const long long stations = std::max(1LL, (long long)std::ceil(run / P().RIBBON_GROW_STEP));
    double z = s[2];
    for (long long k = 1; k <= stations; ++k)
        if (!station_floor(C, s[0] + (e[0] - s[0]) * (double)k / (double)stations,
                           s[1] + (e[1] - s[1]) * (double)k / (double)stations, z, &z))
            return false;
    return true;
}

int target_at(const std::vector<Target>& targets, double x, double y, double z) {
    for (const Target& t : targets)
        if (py_hypot(x - t.x, y - t.y) <= P().RIBBON_HALF_WIDTH && std::fabs(z - t.z) <= P().MAX_CLIMB)
            return t.i;
    return -1;
}

using GridKey = std::pair<long long, long long>;

// _route: the stations from `start` and the reached target, or false.
bool route(const CellCtx& C, const P3& start, const std::vector<Target>& targets,
           std::vector<P3>* path_out, int* hit_out) {
    static const int kNeighbours[8][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1},
                                          {1, 1}, {1, -1}, {-1, 1}, {-1, -1}};
    const double step = P().ROUTE_STEP;
    std::map<GridKey, double> floors{{{0, 0}, start[2]}};
    std::map<GridKey, GridKey> came;
    using Item = std::tuple<double, long long, long long>;
    std::priority_queue<Item, std::vector<Item>, std::greater<Item>> heap;
    heap.push({0.0, 0, 0});
    while (!heap.empty()) {
        const auto [h, i, j] = heap.top();
        heap.pop();
        const int hit = target_at(targets, start[0] + (double)i * step, start[1] + (double)j * step,
                                  floors[{i, j}]);
        if (hit >= 0) {
            std::vector<GridKey> path{{i, j}};
            for (auto it = came.find(path.back()); it != came.end(); it = came.find(path.back()))
                path.push_back(it->second);
            for (auto it = path.rbegin(); it != path.rend(); ++it)
                path_out->push_back({start[0] + (double)it->first * step,
                                     start[1] + (double)it->second * step, floors[*it]});
            *hit_out = hit;
            return true;
        }
        for (const auto& nb : kNeighbours) {
            const GridKey key{i + nb[0], j + nb[1]};
            if (floors.count(key) || came.count(key)
                || py_hypot((double)key.first, (double)key.second) * step > P().ROUTE_REACH)
                continue;
            came[key] = {i, j};
            const double x = start[0] + (double)key.first * step;
            const double y = start[1] + (double)key.second * step;
            double floor;
            if (!station_floor(C, x, y, floors[{i, j}], &floor)) continue;
            floors[key] = floor;
            double best = 0.0;
            for (size_t k = 0; k < targets.size(); ++k) {
                const double dd = py_hypot(x - targets[k].x, y - targets[k].y);
                if (k == 0 || dd < best) best = dd;
            }
            heap.push({best, key.first, key.second});
        }
    }
    return false;
}

std::vector<P3> pull_tight(const CellCtx& C, const std::vector<P3>& path) {
    std::vector<P3> out{path[0]};
    size_t cur = 0;
    while (cur + 1 < path.size()) {
        size_t j = path.size() - 1;
        for (; j > cur + 1; --j)
            if (straight_walk(C, path[cur], path[j])) break;
        cur = j;
        out.push_back(path[cur]);
    }
    return out;
}

}  // namespace

DoorGeometry door_geometry(CellCtx& C, const Group& corridors, std::vector<DoorRef>* unreached) {
    DoorGeometry out;
    if (C.doors.empty()) return out;
    UnionArgs A;
    A.store = &C.store;
    A.strips = corridors;
    A.has_bounds = C.has_clip;
    std::copy(C.clip, C.clip + 4, A.bounds);
    A.probe_only = true;
    A.land = C.land;
    const UMesh probe = build_union_mesh(A);
    if (probe.t.empty()) return out;
    const Group steep = steep_strips(corridors);
    const Probe pr(probe);
    for (const DoorRef& d : C.doors) {
        const std::vector<Footprint> quads = door_quads(C, pr, d);
        if (quads.empty() && unreached) unreached->push_back(d);
        for (const Footprint& fp : quads) door_quad_strip(C, fp, steep, out);
    }
    return out;
}

bool route_doors(CellCtx& C, const std::vector<DoorRef>& doors) {
    std::set<int> used;
    for (const Edge2& e : C.edges) { used.insert(e.first); used.insert(e.second); }
    std::vector<Target> targets;
    for (int i : used)
        targets.push_back({i, C.nodes[(size_t)i][0], C.nodes[(size_t)i][1], C.node_z[(size_t)i]});
    if (!C.has_sample() || targets.empty()) return false;
    bool routed = false;
    for (const DoorRef& d : doors) {
        const double fx = crt_cos(d.rz), fy = -crt_sin(d.rz);
        std::vector<P3> path;
        int hit = -1;
        for (double side : {1.0, -1.0}) {
            const double x = d.x + fx * side * P().DOOR_TRI_MIN_DEPTH;
            const double y = d.y + fy * side * P().DOOR_TRI_MIN_DEPTH;
            double floor;
            if (floor_below(C, x, y, d.z, &floor) && body_clear(C, x, y, floor)
                && route(C, {x, y, floor}, targets, &path, &hit))
                break;
            path.clear();
        }
        if (path.empty()) continue;
        std::vector<int> chain;
        const std::vector<P3> tight = pull_tight(C, path);
        for (size_t k = 0; k + 1 < tight.size(); ++k) {
            chain.push_back((int)C.nodes.size());
            C.nodes.push_back(tight[k]);
            C.node_z.push_back(tight[k][2]);
        }
        chain.push_back(hit);
        for (size_t k = 0; k + 1 < chain.size(); ++k)
            C.edges.push_back({std::min(chain[k], chain[k + 1]), std::max(chain[k], chain[k + 1])});
        routed = true;
    }
    return routed;
}

std::set<int> standing_spawns(CellCtx& C) {
    std::set<int> standing;
    if (!C.has_sample()) return standing;
    for (int i : C.lone) {
        const P3& n = C.nodes[(size_t)i];
        double floor;
        if (floor_below(C, n[0], n[1], n[2], &floor) && body_clear(C, n[0], n[1], floor)) {
            C.node_z[(size_t)i] = floor;
            standing.insert(i);
        }
    }
    return standing;
}

Group drop_covered_discs(const Group& strips, const std::set<int>& lone, const Land* land,
                         std::set<int>& kept) {
    auto spawn_disc = [&lone](const UStrip* s) { return s->ei == s->ej && lone.count(s->ei); };
    Group ribbons, out;
    for (const UStrip* s : strips)
        if (!spawn_disc(s)) ribbons.push_back(s);
    for (const UStrip* s : strips) {
        if (!spawn_disc(s)) continue;
        bool covered = false;
        for (double z : levels_at_py(ribbons, land, s->na[0], s->na[1]))
            covered = covered || std::fabs(z - s->na[2]) <= P().SAME_SURFACE_Z;
        if (!covered) kept.insert(s->ei);
    }
    for (const UStrip* s : strips)
        if (!spawn_disc(s) || kept.count(s->ei)) out.push_back(s);
    return out;
}

}  // namespace navgrow
