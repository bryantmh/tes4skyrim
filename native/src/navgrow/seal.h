// The last cleanup step: every crack the earlier stages left is closed.
#pragma once

#include <vector>

#include "cellgeo.h"
#include "mesh.h"

namespace navgrow {

// Seals (V, T) in place: an open edge with a vertex lying on it (within a step
// up or down) is split there, and where triangles meet at a single vertex the
// gap between the two pieces is filled with one triangle when that gap is
// open, level, walkable ground.  Adds no vertices.
void seal_mesh(const std::vector<Vert>& V, std::vector<Tri>& T, const CellGeo& G);

}  // namespace navgrow
