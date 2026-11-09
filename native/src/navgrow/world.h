// One cell's collision gather (world.gather_cell_geometry), the walk tests
// over it (seeding.Ground), and the two pathgrid passes built on them:
// obstacle_bridge.bridge_obstacles and seeding.seed_graph.
#pragma once

#include <utility>
#include <vector>

#include "cellgeo.h"
#include "mesh.h"
#include "strip.h"

namespace navgrow {

// A flat game-unit soup, 9 values per triangle, borrowed from the collision
// cache (float32) or a scanner's list (float64).
struct SoupView {
    const float* f = nullptr;
    const double* d = nullptr;
    size_t n = 0;                    // values, not triangles
    double at(size_t i) const { return f ? (double)f[i] : d[i]; }
};

// One placed REFR: its base mesh's soups and world._placed_soup's transform.
struct PlacedRef {
    SoupView w, b;
    double scale = 1.0;
    double pos[3] = {0, 0, 0};
    double rot[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};   // world.rot_matrix, row-major
    bool door = false;                              // a door base: no blocking faces
};

// The gathered soups: walkable rows [0, land_from) are placed, the rest LAND.
struct Soups {
    std::vector<double> walk, block;
    size_t land_from = 0;            // triangles
};

// gather_cell_geometry(split_land=True) with the LAND rows appended after the
// placed walkable ones (corridor._cell_geometry).  `water_z` is null for none.
Soups gather_cell(const std::vector<PlacedRef>& refs, const Land* land, const double* water_z);

// obstacle_bridge._footprint: (xmin, ymin, zmin, xmax, ymax, zmax); false for none.
bool footprint(const PlacedRef& ref, double box[6]);

// seeding.Ground over one cell's indexed soups.
struct Ground {
    CellGeo& geo;
    const Land* land;
    bool floor(double x, double y, double near_z, double* z) { return geo.walk_at(x, y, near_z, z); }
    bool blocked(double x, double y, double z, double ux, double uy) const;
    bool walk(const P3& a, const P3& b);
};

// bridge_obstacles: append walkable edges across each obstacle cluster.
int bridge_obstacles(const std::vector<P3>& points, std::vector<std::pair<int, int>>& edges,
                     const std::vector<PlacedRef>& obstacles, Ground& ground);

// seeding.seed_graph: the flooded synthetic pathgrid (empty when none walks in).
void seed_graph(const std::vector<P3>& seeds, Ground& ground, double ox, double oy,
                std::vector<P3>& points, std::vector<std::pair<int, int>>& edges);

// door_reach.door_reach: per seed, the walkable lattice path to the nearest anchor it
// reaches (joins: (point, anchor)); with no anchors, the whole flood from the seeds.
void door_reach(const std::vector<P3>& seeds, const std::vector<P3>& anchors, Ground& ground,
                std::vector<P3>& points, std::vector<std::pair<int, int>>& edges,
                std::vector<std::pair<int, int>>& joins);

}  // namespace navgrow
