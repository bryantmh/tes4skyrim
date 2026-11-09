// One cell's corridor build (corridor.build_corridors after the collision
// gather): the cell's samplers and pathgrid, and the stages that read them.
#pragma once

#include <map>
#include <set>
#include <utility>
#include <vector>

#include "cellgeo.h"
#include "clean.h"
#include "union.h"
#include "world.h"

namespace navgrow {

using Edge2 = std::pair<int, int>;

// A door placement: (x, y, z, rot_z, is_teleport, width).
struct DoorRef {
    double x, y, z, rz;
    bool teleport;
    double width;     // 0 = unmeasured
};

struct CellCtx {
    CellGeo geo;                     // walkable + blocking soups, indexed
    const Land* land = nullptr;      // the cell's LAND, or null
    int land_from = 0x7fffffff;      // first LAND triangle in the walkable soup
    std::vector<P3> nodes;           // pathgrid nodes (spawn nodes appended)
    std::vector<double> node_z;
    std::vector<Edge2> edges;
    std::set<int> lone;              // edgeless spawn nodes
    std::vector<DoorRef> doors;
    bool has_clip = false;
    double clip[4] = {0, 0, 0, 0};
    std::deque<UStrip> store;        // owns every strip of the build

    // corridor._surface_sampler (false for None).
    bool sample(double x, double y, double near_z, double* z) const {
        return geo.surface(x, y, near_z, z);
    }
    bool has_sample() const { return geo.has_walkable; }
    // The sampler's .layers: every distinct height at (x, y), ascending, 2u dedupe.
    std::vector<double> layers(double x, double y) const;
    // corridor_grow.wall_slab_sampler with its default depth / half width.
    bool wall_hit(double cx, double cy, double ux, double uy, double tx, double ty,
                  double z_lo, double z_hi, double depth, double half_w,
                  bool exact = false) const {
        return geo.wall_at(cx, cy, ux, uy, tx, ty, z_lo, z_hi, depth, half_w, exact);
    }
};

// --- ccell.cpp ----------------------------------------------------------------
// corridor.build_corridors after the collision gather.
CleanOutput build_corridor_cell(CellCtx& C);
// Gather `refs`, bridge the pathgrid across `obstacles`, then build_corridor_cell.
CleanOutput build_cell(CellCtx& C, const std::vector<PlacedRef>& refs,
                       const std::vector<PlacedRef>& obstacles, const double* water_z);

// --- cstrips.cpp --------------------------------------------------------------
// corridor._build_corridor_strips: the corridor ribbons and node discs.
Group build_corridor_strips(CellCtx& C);
// corridor._steep_strips.
Group steep_strips(const Group& strips);
// corridor._clip_flat_poly_off_level with a height function for the polygon.
Ring clip_flat_poly_off_level(const Ring& poly, double nx, double ny,
                              const std::function<double(double, double)>& nz,
                              const Group& steep);
// corridor._simplify.
Ring simplify_ring(const Ring& pts, double tol);

// --- cdoors.cpp ---------------------------------------------------------------
struct DoorGeometry {
    Group strips;                    // door footprint strips
    std::vector<DoorEdge> edges;     // base lines
    std::vector<P3> pins;            // wedge pins
};
// corridor._door_geometry; doors without a footprint go to `unreached`.
DoorGeometry door_geometry(CellCtx& C, const Group& corridors,
                           std::vector<DoorRef>* unreached);
// door_routes.route_doors: true when any door or island spawn was routed (nodes / edges grown).
bool route_doors(CellCtx& C, const std::vector<DoorRef>& doors, const std::vector<P3>& spawns);
// spawn_nodes.isolated_spawns: each kept spawn disc no ribbon reaches, as the point to route from.
std::vector<P3> isolated_spawns(const CellCtx& C, const Group& strips, const std::set<int>& lone,
                                const std::set<int>& kept);
// spawn_nodes.standing_spawns (drops each into node_z).
std::set<int> standing_spawns(CellCtx& C);
// spawn_nodes.drop_covered_discs: the kept strips and kept spawn nodes.
Group drop_covered_discs(const Group& strips, const std::set<int>& lone,
                         const Land* land, std::set<int>& kept);

}  // namespace navgrow
