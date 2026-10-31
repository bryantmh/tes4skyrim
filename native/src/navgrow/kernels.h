// The union stage's numeric kernels as plain C++: each mirrors one Python
// function exactly and also backs a py_* binding for the A/B harness.
#pragma once

#include <array>
#include <functional>
#include <set>
#include <unordered_map>
#include <utility>
#include <vector>

#include "strip.h"
#include "trigrid.h"

namespace navgrow {

using Tri3 = std::array<int, 3>;
using EdgeSet = std::set<std::pair<int, int>>;

// --- grow.cpp ---------------------------------------------------------------
// The native corridor_union._levels_batch: per query point (qp interleaved),
// the mean height of each SAME_SURFACE_Z cluster of covering strips.
std::vector<std::vector<double>> levels_at_points(const std::vector<Strip>& strips,
                                                  const double* pp, const double* fp,
                                                  const Land& land, const double* qp,
                                                  size_t nq, double same_z);

// corridor_grow.grow_batch: each march station's grown half-width.  `walk`
// needs its walk index built; nodes_xy / edges (pairs) / node_z feed the
// crossing stop; on_land flags the stations that follow `land`.
std::vector<double> grow_widths(const TriGrid& wall, const TriGrid* walk,
                                const std::vector<double>& nodes_xy,
                                const std::vector<int>& edges,
                                const std::vector<double>& node_z, const Land* land,
                                const std::vector<double>& stations,
                                const std::vector<double>& on_land, int land_from);

// --- meshflip.cpp (xy: interleaved plan points) ----------------------------
// union_cdt._flip2d.
void flip2d_mesh(const std::vector<double>& xy, std::vector<Tri3>& tris,
                 int rounds);
// union_cdt._hex_refine after _lattice_points; `pid` mints (or dedupes) the
// index of an inserted point, appending to `xy` as needed.
std::vector<Tri3> hex_refine_mesh(std::vector<double>& xy, std::vector<Tri3> tris,
                                  const std::vector<double>& cand, double spacing,
                                  const std::function<int(double, double)>& pid);

// --- refine.cpp -------------------------------------------------------------
// union_cdt._refine_land.
void refine_land_mesh(std::vector<double>& xy, std::vector<Tri3>& tris,
                      const Land& land, double max_e2, double min_e2, double tol,
                      const EdgeSet& protect);
// corridor._simplify's Douglas-Peucker: 1 for each kept point of the
// interleaved polyline, both ends included.
std::vector<char> simplify_keep(const std::vector<double>& xy, double tol);
// union_cdt._refine_steep; `seeds` interleaved, in _seed_grid insertion order.
void refine_steep_mesh(std::vector<double>& xy, std::vector<Tri3>& tris,
                       const std::vector<double>& seeds, double cell,
                       double max_e2, const EdgeSet& protect);

// --- stack.cpp (v3: interleaved x, y, z) ------------------------------------
struct StackedPair { double neg_area; int ti, tj; };
// union_mesh._stacked_with_areas: the pairs, and each one's GEOS plan area.
std::vector<StackedPair> stacked_pairs_mesh(const std::vector<double>& v3,
                                            const std::vector<Tri3>& tris,
                                            double min_area,
                                            std::unordered_map<int, double>& areas);
// corridor_union._overlaps_existing per candidate triangle.
std::vector<char> notch_overlaps_mesh(const std::vector<double>& v3,
                                      const std::vector<Tri3>& tris,
                                      const std::vector<Tri3>& cands, double zgap);

// --- welds.cpp --------------------------------------------------------------
// union_mesh._revert_bad_welds' verdict: the reps (ascending) any of whose
// triangles overlaps other mesh.
std::vector<int> bad_welds_mesh(const std::vector<double>& v3,
                                const std::vector<Tri3>& tris,
                                const std::vector<int>& reps,
                                const std::vector<std::vector<int>>& rep_tris);

}  // namespace navgrow
