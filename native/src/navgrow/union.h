// The corridor union stage (corridor_union.build_union_mesh and the
// union_geom / union_sheets / union_cdt / union_mesh helpers it calls), as
// plain C++ on the shapely-equivalent GEOS calls of geom.h.
#pragma once

#include <array>
#include <deque>
#include <functional>
#include <memory>
#include <vector>

#include "cellgeo.h"
#include "geom.h"
#include "kernels.h"
#include "mesh.h"
#include "strip.h"

namespace navgrow {

// A corridor strip (the Python dict): pathgrid edge (-1 for a door), node and
// centerline ends, perpendicular `w`, half width, optional outline and stair
// profile, and whether its height follows the cell's LAND.
struct UStrip {
    int ei = -1, ej = -1;
    P3 na{}, nb{}, a{}, b{};
    double wx = 0.0, wy = 0.0, half = 0.0, len = 0.0;
    bool has_poly = false;
    Ring poly;
    std::vector<P3> prof;
    bool land = false;
    mutable std::shared_ptr<Geom> ribbon;
};

using Group = std::vector<const UStrip*>;

// A door base line: (b0, b1, apex or none, z or none).
struct DoorEdge {
    P2 b0{}, b1{}, apex{};
    bool has_apex = false, has_z = false;
    double z = 0.0;
};

struct UnionArgs {
    std::deque<UStrip>* store = nullptr;     // owns every strip, incl. donated ones
    Group strips;                            // corridors then extra (door) strips
    std::vector<DoorEdge> doors;
    bool has_bounds = false;
    double bounds[4] = {0, 0, 0, 0};
    bool probe_only = false;
    const Land* land = nullptr;              // the cell's LandField, or null
    CellGeo* geo = nullptr;                  // for the land-slit wall test
};

struct UMesh {
    std::vector<Vert> v;
    std::vector<Tri3> t;
};

// corridor_union.build_union_mesh.
UMesh build_union_mesh(UnionArgs& A);

// --- union_geom (ustrip.cpp) -----------------------------------------------
// The strip's memoised ribbon polygon (_ribbon_polygon).
const GEOSGeometry* ribbon_polygon(const UStrip& s);
// valid_ribbons: the strips whose ribbon is valid and non-empty.
Group valid_ribbons(const Group& strips);
// _height_on.
double height_on(const UStrip& s, const Land* land, double px, double py);
// _distance_to.
double distance_to(const UStrip& s, double px, double py);
// _clip_strip_near.
UStrip clip_strip_near(const UStrip& s, double nx, double ny, double r,
                       const GEOSGeometry* piece);
// The group's LAND when any strip follows it (_land_of), else null.
const Land* land_of(const Group& g, const Land* land);
// union_geom.flat_strips: the native strip records over `g`.
struct FlatStrips {
    std::vector<double> rows, poly, prof;
    std::vector<Strip> strips;
};
FlatStrips flat_strips(const Group& g, const Land* land);
// corridor_union.levels_at (the Python reference, used by the door claim).
std::vector<double> levels_at_py(const Group& g, const Land* land, double px,
                                 double py);

// --- union_sheets (usheets.cpp) ---------------------------------------------
std::vector<Group> storey_groups(const Group& strips);
std::vector<Group> split_plan_overlaps(const std::vector<Group>& groups,
                                       const Land* land);
// _same_surface_region, or a null Geom.
Geom same_surface_region(const Group& a, const Group& b, const GEOSGeometry* shared,
                         const Land* land);

// --- union_cdt (ucdt.cpp) ---------------------------------------------------
struct Plan2 {
    std::vector<double> xy;   // interleaved plan points
    std::vector<Tri3> t;
};
// _triangulate.
Plan2 triangulate(const GEOSGeometry* poly, double target_edge,
                  const std::vector<DoorEdge>& fixed, const std::vector<double>& steep,
                  const Land* land, double max_edge);
// _ribbon_seeds' steep points, interleaved.
std::vector<double> steep_seeds(const Group& strips, double target_edge);
// _door_edge_on_part.
bool door_edge_on_part(const DoorEdge& e, const GEOSGeometry* part);

// --- union_mesh (umesh.cpp) -------------------------------------------------
void weld_sheets(UMesh& M, const std::vector<int>& src);
void merge_at_pathgrid_nodes(UMesh& M, const std::vector<std::pair<int, P2>>& nodes,
                             const std::vector<double>& node_half);
std::vector<Tri3> destack(const std::vector<Vert>& v, std::vector<Tri3> t);
std::vector<Tri3> drop_walls(const std::vector<Vert>& v, std::vector<Tri3> t,
                             const Group& strips, const Land* land);
std::vector<Tri3> fill_boundary_notches(const std::vector<Vert>& v,
                                        std::vector<Tri3> t, const Group& strips);
std::vector<Tri3> drop_point_attached(std::vector<Tri3> t);

}  // namespace navgrow
