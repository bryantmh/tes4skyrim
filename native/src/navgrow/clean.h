// The cleanup stage's native entry points (corridor_clean, clean_decimate).
#pragma once

#include <cmath>
#include <functional>
#include <vector>

#include "cellgeo.h"
#include "mesh.h"

namespace navgrow {

// A decimation pin: vertices within `r` of (x, y) never move.
struct Pin { double x, y, r; };

// A walked-line sample; z and the direction are NaN where the reference holds None.
struct Sample { double x, y, z, ux, uy; };

// The collision oracles decimation consults.  An empty function is the
// reference's None.  `surface` reports whether a height was found and whether
// the reference would hold it as a numpy.float64.
struct Oracles {
    std::function<bool(const Vert& a, const Vert& v, const Vert& b)> ground_ok;
    std::function<bool(double x, double y, double near, double* z, bool* znp)> surface;
};

// clean_decimate.on_seam_rect: (x, y) on a side of the `seam` rectangle (minx, miny, maxx,
// maxy); always false for nullptr.
inline bool on_seam_rect(double x, double y, const double* seam) {
    return seam && (std::fabs(x - seam[0]) <= 0.5 || std::fabs(x - seam[2]) <= 0.5
                    || std::fabs(y - seam[1]) <= 0.5 || std::fabs(y - seam[3]) <= 0.5);
}

// clean_decimate.decimate on (V, T) in place; `seam` is minx, miny, maxx,
// maxy or nullptr.  Vertex Z types are taken as given.
void decimate_mesh(std::vector<Vert>& V, std::vector<Tri>& T,
                   const std::vector<Pin>& pins, const double* seam, int rounds,
                   bool allow_split, const Oracles& O);

// clean_decimate._pin_verts: 1 for each vertex inside a pin's radius.
std::vector<char> pin_verts(const std::vector<Vert>& V, const std::vector<Pin>& pins);

// corridor_clean.cull_boundary_slivers' three rounds: the surviving original
// triangle indices.  `budget` is the area the cull may remove.
std::vector<int> cull_slivers_mesh(const std::vector<Vert>& V, const std::vector<Tri>& T,
                                   const std::vector<char>& pin,
                                   const std::vector<Sample>& samples,
                                   const double* seam, double budget);

// A NVNM ledge link: upper triangle, lower triangle, drop.
struct Ledge { int hi, lo; double drop; };

// corridor_clean.finalize's inputs (and the build_corridors tail's).
struct CleanInput {
    std::vector<Vert> V;
    std::vector<Tri> T;
    std::vector<P3> doors;           // door thresholds (x, y, z)
    bool has_bounds = false;
    double bounds[4] = {0, 0, 0, 0};
    std::vector<Sample> pin_xy;      // walked samples
    std::vector<P3> door_pins;       // wedge ring points
    std::vector<P3> node_pins;       // pathgrid nodes + spawn points
    std::vector<P3> keep_xy;         // door thresholds + spawn points
};

struct CleanOutput {
    std::vector<Vert> V;
    std::vector<Tri> T;
    std::vector<Ledge> ledges;
};

// corridor_clean.finalize plus the rest of corridor.build_corridors.
CleanOutput finalize(CleanInput in, CellGeo& G);

}  // namespace navgrow
