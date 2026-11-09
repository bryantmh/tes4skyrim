// Sheet stitching and T-junction splits (union_mesh), shared by the union and
// cleanup stages.
#pragma once

#include <vector>

#include "mesh.h"

namespace navgrow {

// union_mesh.stitch_shared_nodes: `T` with sheets meeting at a vertex given
// shared edges.  New midpoint vertices are appended to `V`.  Needs GEOS bound.
std::vector<Tri> stitch_shared_nodes(std::vector<Vert>& V, std::vector<Tri> T);

// union_mesh.split_t_junctions: `T` with border edges split where another
// sheet's vertex lies on them.
std::vector<Tri> split_t_junctions(const std::vector<Vert>& V, std::vector<Tri> T);

// The same with a vertex allowed `ztol` above or below the edge.
std::vector<Tri> split_t_junctions(const std::vector<Vert>& V, std::vector<Tri> T, double ztol);

// union_geom._tri_components: a component id per triangle, joined over every
// shared edge (three owners included).
std::vector<int> tri_components(const std::vector<Tri>& T);

// union_mesh._fuse_coincident: `T` with vertices at the same rounded position
// merged (the lowest index wins); unchanged when nothing fuses.
std::vector<Tri> fuse_coincident(const std::vector<Vert>& V, const std::vector<Tri>& T);

}  // namespace navgrow
