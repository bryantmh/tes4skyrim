// The last cleanup step (seal_mesh): no crack survives the build.
//
// The engine paths only across an edge two triangles share.  Two defects leave
// an edge unshared where the floor is continuous:
//   * a T-junction -- a vertex lies on another triangle's open edge, a step
//     above or below it (the union's own split allows only 12, so 17-25 rises
//     on sloped land stayed open);
//   * a single-point join -- two pieces touch at one vertex only, with a gap
//     between them.
// Each fix only adds triangles over existing vertices; nothing moves.

#include "seal.h"

#include <algorithm>
#include <cmath>

#include "params.h"
#include "stitch.h"

namespace navgrow {

namespace {

constexpr int kFillRounds = 4;
constexpr double kTwoPi = 6.283185307179586;
//: Twice the plan area a gap fill must have (a flatter one is a sliver).
constexpr double kMinFillArea2 = 2.0;
//: Plan size of a wall probe's slab around each sample point.
constexpr double kWallProbe = 4.0;

double cross2(const Vert& o, const Vert& a, const Vert& b) {
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

// Slope of triangle abc from level, in degrees.
double slope_deg(const Vert& a, const Vert& b, const Vert& c) {
    const double ux = b.x - a.x, uy = b.y - a.y, uz = b.z - a.z;
    const double vx = c.x - a.x, vy = c.y - a.y, vz = c.z - a.z;
    const double nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const double len = std::sqrt(nx * nx + ny * ny + nz * nz);
    return len > 0.0 ? std::acos(std::min(1.0, std::fabs(nz) / len)) * 57.29577951308232 : 90.0;
}

// Height of triangle abc at (x, y) when the point lies inside it in plan.
bool height_in(const Vert& a, const Vert& b, const Vert& c, double x, double y, double* z) {
    const double d = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y);
    if (std::fabs(d) < 1e-9) return false;
    const double l0 = ((b.y - c.y) * (x - c.x) + (c.x - b.x) * (y - c.y)) / d;
    const double l1 = ((c.y - a.y) * (x - c.x) + (a.x - c.x) * (y - c.y)) / d;
    const double l2 = 1.0 - l0 - l1;
    if (l0 < 1e-6 || l1 < 1e-6 || l2 < 1e-6) return false;
    *z = l0 * a.z + l1 * b.z + l2 * c.z;
    return true;
}

// Groups of v's fan that share an edge through v; one id per fan entry.
std::vector<int> fan_groups(const std::vector<Tri>& T, const std::vector<int>& fan, int v) {
    std::vector<int> g(fan.size());
    for (size_t i = 0; i < fan.size(); ++i) g[i] = (int)i;
    auto find = [&](int x) {
        while (g[(size_t)x] != x) x = g[(size_t)x] = g[(size_t)g[(size_t)x]];
        return x;
    };
    for (size_t i = 0; i < fan.size(); ++i)
        for (size_t j = i + 1; j < fan.size(); ++j)
            for (int w : T[(size_t)fan[i]])
                if (w != v && has(T[(size_t)fan[j]], w)) {
                    g[(size_t)find((int)i)] = find((int)j);
                    break;
                }
    for (size_t i = 0; i < fan.size(); ++i) g[i] = find((int)i);
    return g;
}

// Points inside the wedge the checks sample: its centroid and each edge's
// midpoint pulled halfway to the centroid.
std::vector<P3> wedge_samples(const Vert& a, const Vert& b, const Vert& c) {
    const P3 m{(a.x + b.x + c.x) / 3, (a.y + b.y + c.y) / 3, (a.z + b.z + c.z) / 3};
    std::vector<P3> out{m};
    for (const auto& e : {std::make_pair(&a, &b), std::make_pair(&b, &c), std::make_pair(&c, &a)})
        out.push_back({(m[0] + (e.first->x + e.second->x) / 2) / 2,
                       (m[1] + (e.first->y + e.second->y) / 2) / 2,
                       (m[2] + (e.first->z + e.second->z) / 2) / 2});
    return out;
}

// Whether a mesh triangle within a storey already covers any sample, or an
// edge of one crosses the wedge's closing edge (w, u).
bool covered(const std::vector<Vert>& V, const std::vector<Tri>& T,
             const std::vector<P3>& S, int w, int u) {
    const double band = P().STOREY_GAP_Z;
    const Vert &W = V[(size_t)w], &U = V[(size_t)u];
    for (const Tri& t : T) {
        const Vert &a = V[(size_t)t[0]], &b = V[(size_t)t[1]], &c = V[(size_t)t[2]];
        double z;
        for (const P3& s : S)
            if (height_in(a, b, c, s[0], s[1], &z) && std::fabs(z - s[2]) < band) return true;
        for (int k = 0; k < 3; ++k) {
            const int p = t[k], q = t[(k + 1) % 3];
            if (p == w || p == u || q == w || q == u) continue;
            const Vert &P0 = V[(size_t)p], &Q0 = V[(size_t)q];
            if (std::fabs((P0.z + Q0.z - W.z - U.z) / 2) >= band) continue;
            if (cross2(W, U, P0) * cross2(W, U, Q0) < 0 && cross2(P0, Q0, W) * cross2(P0, Q0, U) < 0)
                return true;
        }
    }
    return false;
}

// Walkable floor within a step of every sample, and no wall in the body's band.
bool open_floor(const CellGeo& G, const std::vector<P3>& S) {
    const NavParams& N = P();
    for (const P3& s : S) {
        double z;
        if (!G.surface(s[0], s[1], s[2], &z) || std::fabs(z - s[2]) > N.MAX_CLIMB) return false;
        if (G.wall_at(s[0], s[1], 1.0, 0.0, 0.0, 1.0, s[2] + N.MAX_CLIMB, s[2] + N.AGENT_HEIGHT,
                      kWallProbe, kWallProbe))
            return false;
    }
    return true;
}

// Open spokes of v (edges with one owner), counter-clockwise by angle.
std::vector<int> open_spokes(const std::vector<Vert>& V, const std::vector<Tri>& T,
                             const EdgeOwners& own, const std::vector<int>& fan, int v) {
    std::vector<std::pair<double, int>> sp;
    for (int ti : fan)
        for (int w : T[(size_t)ti])
            if (w != v && own.count(v, w) == 1)
                sp.push_back({std::atan2(V[(size_t)w].y - V[(size_t)v].y, V[(size_t)w].x - V[(size_t)v].x), w});
    std::sort(sp.begin(), sp.end());
    std::vector<int> out;
    for (const auto& p : sp) out.push_back(p.second);
    return out;
}

// Whether the sector at v from spoke w counter-clockwise to spoke u holds none
// of v's triangles (it is the gap, not a piece).
bool empty_sector(const std::vector<Vert>& V, const std::vector<Tri>& T,
                  const std::vector<int>& fan, int v, int w, int u) {
    const Vert& o = V[(size_t)v];
    const double a0 = std::atan2(V[(size_t)w].y - o.y, V[(size_t)w].x - o.x);
    double a1 = std::atan2(V[(size_t)u].y - o.y, V[(size_t)u].x - o.x);
    if (a1 <= a0) a1 += kTwoPi;
    const double mid = 0.5 * (a0 + a1);
    const double px = o.x + std::cos(mid), py = o.y + std::sin(mid);
    double z;
    for (int ti : fan) {
        const Tri& t = T[(size_t)ti];
        if (height_in(V[(size_t)t[0]], V[(size_t)t[1]], V[(size_t)t[2]], px, py, &z)) return false;
    }
    return true;
}

// The first fillable gap at single-point join v, as a triangle; false if none.
bool gap_fill(const std::vector<Vert>& V, const std::vector<Tri>& T, const EdgeOwners& own,
              const CellGeo& G, const std::vector<int>& fan, const std::vector<int>& group,
              int v, Tri* out) {
    const std::vector<int> spokes = open_spokes(V, T, own, fan, v);
    auto group_of = [&](int w) {
        for (size_t i = 0; i < fan.size(); ++i) if (has(T[(size_t)fan[i]], w)) return group[i];
        return -1;
    };
    for (size_t k = 0; k < spokes.size(); ++k) {
        const int w = spokes[k], u = spokes[(k + 1) % spokes.size()];
        if (w == u || group_of(w) == group_of(u) || own.count(w, u) > 1) continue;
        const Vert &A = V[(size_t)v], &B = V[(size_t)w], &C = V[(size_t)u];
        if (cross2(A, B, C) < kMinFillArea2) continue;
        if (slope_deg(A, B, C) > P().MAX_SLOPE_DEG || !empty_sector(V, T, fan, v, w, u)) continue;
        const std::vector<P3> S = wedge_samples(A, B, C);
        if (covered(V, T, S, w, u) || !open_floor(G, S)) continue;
        *out = {v, w, u};
        return true;
    }
    return false;
}

// One round of gap fills, at most one per single-point join; false if none.
bool fill_round(const std::vector<Vert>& V, std::vector<Tri>& T, const CellGeo& G) {
    std::vector<std::vector<int>> fans(V.size());
    for (size_t i = 0; i < T.size(); ++i) for (int v : T[i]) fans[(size_t)v].push_back((int)i);
    const EdgeOwners own(T);
    std::vector<Tri> add;
    for (size_t v = 0; v < V.size(); ++v) {
        if (fans[v].size() < 2) continue;
        const std::vector<int> group = fan_groups(T, fans[v], (int)v);
        if (std::all_of(group.begin(), group.end(), [&](int g) { return g == group[0]; })) continue;
        Tri t;
        if (gap_fill(V, T, own, G, fans[v], group, (int)v, &t)) add.push_back(t);
    }
    for (const Tri& t : add) {
        const EdgeOwners now(T);
        if (now.count(t[0], t[1]) < 2 && now.count(t[1], t[2]) < 2 && now.count(t[2], t[0]) < 2)
            T.push_back(t);
    }
    return !add.empty();
}

}  // namespace

void seal_mesh(const std::vector<Vert>& V, std::vector<Tri>& T, const CellGeo& G) {
    const double step = P().MAX_CLIMB;
    T = split_t_junctions(V, std::move(T), step);
    for (int round = 0; round < kFillRounds; ++round)
        if (!fill_round(V, T, G)) break;
    T = split_t_junctions(V, std::move(T), step);
}

}  // namespace navgrow
