// union_sheets' GEOS-heavy kernels as plain C++ (sheets.cpp).  Each throws
// GeosError where the Python reference does not guard a GEOS call.
#pragma once

#include <utility>
#include <vector>

#include "geom.h"
#include "strip.h"

namespace navgrow {

// Every index pair a < b whose polygons intersect, ascending
// (union_sheets._candidate_pairs).
std::vector<std::pair<int, int>> candidate_pairs(
    const std::vector<const GEOSGeometry*>& polys);

// _overlap_height_gap per pair: NaN where the two share no ground (empty, or
// under 1 unit^2).  A gap may differ from Python's, never across `thr`.
std::vector<double> pair_gaps(const std::vector<const GEOSGeometry*>& polys,
                              const std::vector<Strip>& strips, const double* fp,
                              const Land& land,
                              const std::vector<std::pair<int, int>>& pairs,
                              double thr);

// _same_surface_pieces: each (pa & pb) & shared over 1 unit^2 whose height gap
// is within `tol`, in the Python order.  `strips` holds the A strips then B's.
// A pair overlap disjoint from `shared` skips its overlay: the result would be
// empty and dropped, and that overlay is most of the cost on dense cells.
std::vector<Geom> same_surface_pieces(const std::vector<const GEOSGeometry*>& pa,
                                      const std::vector<const GEOSGeometry*>& pb,
                                      const GEOSGeometry* shared,
                                      const std::vector<Strip>& strips,
                                      const double* fp, const Land& land,
                                      double tol);

// _overlap_height_gap for strips A and B over `inter` (inf when unsampled).
double overlap_height_gap(const Strip& A, const Strip& B, const double* fp,
                          const Land& land, const GEOSGeometry* inter);

}  // namespace navgrow
