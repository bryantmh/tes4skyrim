// One cell's collision samplers, built once and shared by every native stage
// of that cell's build (corridor._surface_sampler, corridor_grow's
// walkable_sampler and wall_slab_sampler).
#pragma once

#include <algorithm>
#include <vector>

#include "trigrid.h"

namespace navgrow {

struct CellGeo {
    TriGrid walk;                // the walkable soup
    TriGrid wall;                // the blocking soup
    bool has_walkable = false;   // a non-empty walkable soup (else no surface sampler)
    std::vector<unsigned> stamp;
    unsigned epoch = 0;
    bool walk_indexed = false;

    // Index both soups (9 doubles per triangle).
    void build(const double* walkable, size_t nwalk, const double* blocking, size_t nblock) {
        walk.build(walkable, nwalk, 128.0);
        wall.build(blocking, nblock, 128.0);
        has_walkable = nwalk > 0;
        stamp.assign(walk.ntri, 0u);
        epoch = 0;
    }

    // The fine walk index the width-grow's march samples through (built once).
    void ensure_walk_index() {
        if (!walk_indexed) walk.build_walk_index();
        walk_indexed = true;
    }

    // corridor._surface_sampler; false for None.
    bool surface(double x, double y, double near_z, double* z) const {
        return has_walkable && surface_nearest(walk, x, y, near_z, z);
    }

    // corridor_grow.walkable_sampler; false for None.
    bool walk_at(double x, double y, double near_z, double* z) {
        return walk_nearest_py(walk, x, y, near_z, stamp, epoch, z);
    }

    // Every distinct walkable height at (x, y), ascending, 2u dedupe.
    std::vector<double> layers(double x, double y) const {
        std::vector<double> zs, out;
        if (!has_walkable) return out;
        bucket_heights(walk, x, y, zs);
        std::sort(zs.begin(), zs.end());
        for (double z : zs)
            if (out.empty() || z - out.back() > 2.0) out.push_back(z);
        return out;
    }

    // corridor_grow.wall_slab_sampler.
    bool wall_at(double cx, double cy, double ux, double uy, double tx, double ty,
                 double z_lo, double z_hi, double depth, double half_w,
                 bool exact = false) const {
        return wall_hit(wall, cx, cy, ux, uy, tx, ty, z_lo, z_hi, depth, half_w, exact);
    }
};

}  // namespace navgrow
