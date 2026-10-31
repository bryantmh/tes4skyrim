// Every navmesh tunable the native build reads, set once from Python.
//
// The values come from tes5_import/navmesh/params.py (plus a few module-level
// constants of the corridor modules) through native_loader.native_params(), so
// tuning a value in Python retunes the native build too.  set_params runs
// before any cell is built and the struct is read-only afterwards, which is
// what makes it safe to share across build threads.
#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

namespace navgrow {

#define NAV_PARAMS(X)                                                        \
    X(AGENT_HEIGHT) X(MAX_CLIMB) X(MIN_XY_FOOTPRINT) X(MAX_SLOPE_DEG)        \
    X(TRI_TARGET_EDGE) X(LAND_TRI_TARGET_EDGE) X(MAX_EDGE_RATIO)             \
    X(MAX_TRI_ASPECT) X(SEED_SNAP) X(SEED_Z_TOLERANCE) X(DOOR_QUAD_ZTOL)     \
    X(ISLAND_DOOR_RADIUS) X(ISLAND_DOOR_ZTOL) X(ISLAND_EDGE_MARGIN)          \
    X(ISLAND_PGRD_RADIUS) X(RIBBON_HALF_WIDTH) X(ISLAND_BRIDGE_XY)           \
    X(ISLAND_BRIDGE_MAX_DROP) X(ISLAND_BRIDGE_MIN_DROP)                      \
    X(LEDGE_LINKS_PER_PAIR) X(RIBBON_STEP) X(RIBBON_WELD_EPS)                \
    X(RIBBON_END_EXTEND) X(RIBBON_STAIR_END_EXTEND)                          \
    X(RIBBON_STAIR_HALF_WIDTH) X(RIBBON_GROW_STEP)                           \
    X(RIBBON_GROW_SLAB_HALF_WIDTH) X(RIBBON_GROW_SLAB_DEPTH)                 \
    X(RIBBON_GROW_SLAB_Z_BOTTOM) X(RIBBON_GROW_BISECT)                       \
    X(RIBBON_GROW_MAX_SLOPE) X(RIBBON_RAIL_SIMPLIFY)                         \
    X(RIBBON_GROW_DISC_RAYS) X(DECIMATE_MIN_EDGE) X(DECIMATE_ROUNDS)         \
    X(DECIMATE_OUTLINE_TOL) X(DECIMATE_SAWTOOTH_DEV)                         \
    X(DECIMATE_MAX_AREA_LOSS) X(CULL_SLIVER_MAX_AREA) X(MIN_TRI_AREA)        \
    X(CULL_SLIVER_AREA_FRAC) X(DECIMATE_PIN_RADIUS)                          \
    X(DECIMATE_PIN_CENTER_RADIUS) X(DECIMATE_PIN_NODE_RADIUS)                \
    X(RIBBON_GROW_MIN_HALF) X(RIBBON_GROW_MAX_HALF)                          \
    X(RIBBON_GROW_MAX_HALF_EXTERIOR) X(CS) X(CS_EXTERIOR)                    \
    X(CONCAVE_CUT_FRAC) X(FLAP_EDGE_DROP) X(LIP_MARCH_STEP)                  \
    X(LIP_STANDOFF) X(SAME_SURFACE_Z) X(STOREY_GAP_Z) X(REACH_TOL)          \
    X(WALL_SLOPE_COS) X(DOOR_SNAP_PERP) X(RIBBON_SEED_STEP)                  \
    X(STEEP_REFINE_EDGE) X(LAND_RELIEF_TOL) X(LAND_MIN_EDGE)                 \
    X(DOOR_CLAIM_MIN_FRAC) X(LAND_MAX_EDGE_RATIO) X(LAND_SLIT_HALF)          \
    X(NOTCH_MAX_MOUTH) X(NOTCH_MIN_DEPTH_RATIO) X(NOTCH_NEAR_LINE)           \
    X(TSPLIT_TOL) X(TSPLIT_CRACK_TOL) X(TSPLIT_Z_TOL) X(WELD_R)              \
    X(SAME_PART_WELD_XY) X(PG_CELL) X(RIBBON_GROW) X(DISC_RAY_TRIM)          \
    X(DISC_LEVEL_TOL) X(FAR_WIDTH_WINDOW) X(DOOR_BRIDGE_RADIUS)              \
    X(DOOR_LINE_HALF) X(DOOR_LINE_HALF_MAX) X(DOOR_TRI_MIN_DEPTH)            \
    X(DOOR_SELF_CLEARANCE) X(DOOR_MIN_DEPTH) X(DOOR_OVERLAP) X(ROUTE_STEP)   \
    X(ROUTE_REACH) X(WALK_STEP) X(LATTICE_SPACING)

struct NavParams {
#define NAV_PARAM_FIELD(name) double name = 0.0;
    NAV_PARAMS(NAV_PARAM_FIELD)
#undef NAV_PARAM_FIELD
};

// The tunables; valid once set_params has succeeded.
const NavParams& P();

// Fill the tunables from a {name: number} dict; false (Python error set) when
// a name is missing or not a number.
bool set_params(PyObject* dict);

// True once set_params has succeeded.
bool params_ready();

// corridor_grow.grow_cap: the grown half-width cap, the wide one on an exterior
// (a cell with LAND).
inline double grow_cap(bool exterior) {
    return exterior ? P().RIBBON_GROW_MAX_HALF_EXTERIOR : P().RIBBON_GROW_MAX_HALF;
}

}  // namespace navgrow
