// corridor.build_corridors after the collision gather: node Z snap, standing
// spawns, strips, doors (and routes for the unreached), the union, then the
// cleanup stage -- one native call per cell.

#include "cell.h"
#include "params.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace navgrow {

namespace {

// corridor._snap_node_z.
double snap_node_z(const CellCtx& C, double x, double y, double z) {
    if (!C.has_sample()) return z;
    double s = 0.0, c = 0.0;
    bool have = C.sample(x, y, z, &s);
    for (double r : {8.0, 16.0}) {
        const double off[4][2] = {{r, 0.0}, {-r, 0.0}, {0.0, r}, {0.0, -r}};
        for (const auto& o : off)
            if (C.sample(x + o[0], y + o[1], z, &c) && (!have || std::fabs(c - z) < std::fabs(s - z))) {
                s = c;
                have = true;
            }
    }
    if (!have) return z;
    if (s <= z + P().SEED_Z_TOLERANCE && s >= z - P().SEED_SNAP) return s;
    if (s < z) return z - P().SEED_SNAP;
    return z;
}

struct Built {
    Group corridors;
    std::set<int> kept;
    DoorGeometry doors;
};

// corridor._strips_and_doors.
Built strips_and_doors(CellCtx& C, const std::set<int>& standing,
                       std::vector<DoorRef>* unreached) {
    Built b;
    const std::set<int> lone = C.lone;
    C.lone = standing;
    const Group strips = build_corridor_strips(C);
    C.lone = lone;
    b.corridors = drop_covered_discs(strips, lone, C.land, b.kept);
    b.doors = door_geometry(C, b.corridors, unreached);
    return b;
}

// corridor._centerline_samples.
void centerline_samples(const CellCtx& C, std::vector<Sample>& out) {
    for (const Edge2& e : C.edges) {
        const int i = e.first, j = e.second, n = (int)C.nodes.size();
        if (i >= n || j >= n || i == j) continue;
        const P3 &a = C.nodes[(size_t)i], &b = C.nodes[(size_t)j];
        const double run = py_hypot(b[0] - a[0], b[1] - a[1]);
        if (run < 1e-4) continue;
        const double ux = (b[0] - a[0]) / run, uy = (b[1] - a[1]) / run;
        const long long steps = std::max(2LL, (long long)py_floordiv(run, P().RIBBON_STEP));
        for (long long s = 0; s <= steps; ++s) {
            const double f = (double)s / (double)steps;
            out.push_back({a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f,
                           C.node_z[(size_t)i] + (C.node_z[(size_t)j] - C.node_z[(size_t)i]) * f,
                           ux, uy});
        }
    }
}

// corridor._spawn_samples.
std::vector<P3> spawn_samples(const CellCtx& C, const Built& b, const std::set<int>& standing) {
    std::vector<P3> out;
    for (const UStrip* s : b.corridors)
        if (s->ei == s->ej && b.kept.count(s->ei))
            for (const P2& p : s->poly) out.push_back({p[0], p[1], C.node_z[(size_t)s->ei]});
    for (int i : standing)
        out.push_back({C.nodes[(size_t)i][0], C.nodes[(size_t)i][1], C.node_z[(size_t)i]});
    return out;
}

Sample point_sample(const P3& p) {
    const double nan = std::numeric_limits<double>::quiet_NaN();
    return {p[0], p[1], p[2], nan, nan};
}

}  // namespace

// The cell's collision gathered and indexed, the pathgrid bridged across its
// obstacles (obstacle_bridge), then the corridor build.
CleanOutput build_cell(CellCtx& C, const std::vector<PlacedRef>& refs,
                       const std::vector<PlacedRef>& obstacles, const double* water_z) {
    if (C.nodes.empty() || (C.edges.empty() && obstacles.empty())) return CleanOutput();
    const Soups S = gather_cell(refs, C.land, water_z);
    C.land_from = (int)std::min<size_t>(S.land_from, 0x7fffffff);
    C.geo.build(S.walk.data(), S.walk.size() / 9, S.block.data(), S.block.size() / 9);
    Ground ground{C.geo, C.land};
    bridge_obstacles(C.nodes, C.edges, obstacles, ground);
    return build_corridor_cell(C);
}

// corridor.build_corridors from the samplers on.
CleanOutput build_corridor_cell(CellCtx& C) {
    CleanOutput none;
    if (C.nodes.empty() || C.edges.empty()) return none;
    for (const P3& n : C.nodes) C.node_z.push_back(snap_node_z(C, n[0], n[1], n[2]));
    const std::set<int> standing = standing_spawns(C);
    std::vector<DoorRef> unreached;
    Built b = strips_and_doors(C, standing, &unreached);
    if (route_doors(C, unreached)) b = strips_and_doors(C, standing, nullptr);
    UnionArgs A;
    A.store = &C.store;
    A.strips = b.corridors;
    A.strips.insert(A.strips.end(), b.doors.strips.begin(), b.doors.strips.end());
    A.doors = b.doors.edges;
    A.has_bounds = C.has_clip;
    std::copy(C.clip, C.clip + 4, A.bounds);
    A.land = C.land;
    A.geo = C.land ? &C.geo : nullptr;
    UMesh M = b.corridors.empty() ? UMesh() : build_union_mesh(A);
    if (M.t.empty()) return none;
    CleanInput in;
    in.V = std::move(M.v);
    in.T = std::move(M.t);
    for (const DoorRef& d : C.doors) in.doors.push_back({d.x, d.y, d.z});
    in.has_bounds = C.has_clip;
    std::copy(C.clip, C.clip + 4, in.bounds);
    const std::vector<P3> spawn = spawn_samples(C, b, standing);
    for (const P3& d : in.doors) in.pin_xy.push_back(point_sample(d));
    for (const P3& p : b.doors.pins) in.pin_xy.push_back(point_sample(p));
    centerline_samples(C, in.pin_xy);
    for (const P3& p : spawn) in.pin_xy.push_back(point_sample(p));
    in.door_pins = b.doors.pins;
    for (size_t i = 0; i < C.nodes.size(); ++i)
        if (!C.lone.count((int)i)) in.node_pins.push_back(C.nodes[i]);
    in.node_pins.insert(in.node_pins.end(), spawn.begin(), spawn.end());
    in.keep_xy = in.doors;
    in.keep_xy.insert(in.keep_xy.end(), spawn.begin(), spawn.end());
    return finalize(std::move(in), C.geo);
}

}  // namespace navgrow
