// Native Phase-2 corridor width-grow for tes5_import.navmesh.corridor_grow.
//
// WHY THIS EXISTS
// ---------------
// Measured on the interior cell Wendir02 (471 nodes / 938 edges):
// _build_corridor_strips alone ran past 50s, and a microbenchmark put the
// reason in one place -- wall_hit costs ~170us and the strip build makes ~890k
// of them, i.e. ~150s for that ONE cell.  Across 8,228 pathgrid cells that is
// hundreds of core-hours, which is why generation was taking far longer than
// the 1.5h that prompted this work.
//
// The cost is NOT one hot arithmetic kernel.  Per wall_hit the grid query
// returns ~140 candidate triangles, and each one runs a pure-Python
// separating-axis test built out of list comprehensions and min()/max() over
// temporary lists.  There is no Python-level fix: the work is ~125M tiny float
// operations per cell, each wrapped in interpreter overhead.
//
// So the WHOLE march moves across the boundary, not just the slab test.
// grow_strips() takes a cell's geometry once and returns every station's grown
// half-width, turning ~890k boundary crossings per cell into ONE.  This mirrors
// decimate.cpp's reasoning (marshal once per cell, keep the loop nest native).
//
// WHAT IT MUST PRESERVE
// ---------------------
// Every predicate mirrors corridor_grow.py operation for operation, because
// each encodes a geometry contract that was expensive to get right:
//
//   * The wall test sweeps the INTERVAL [prev, d] via a slab centered on the
//     midpoint with half the step as depth -- a point probe steps over a wall
//     that falls between samples (that caused 124 through-wall triangles).
//   * On a hit, RIBBON_GROW_BISECT bisections land the rail AT the wall; a
//     step-short stop is what narrowed every doorway.
//   * The walkable-floor test binds only BEYOND the soft floor `lo`, so a node
//     at a threshold or ledge lip does not collapse its corridor.
//   * `lo` is a SOFT minimum that a wall always overrides; the march therefore
//     starts at 0 rather than at `lo`.
//   * The neighbour cap counts only roughly-PARALLEL edges within a Z window,
//     and excludes the edge's own endpoints.
//
// DETERMINISM
// -----------
// The pipeline's output must be byte-reproducible.  Nothing here depends on
// pointer values, hash iteration order, or uninitialised memory; candidate
// triangles are visited in ascending index order (the grid stores sorted
// indices), and the march is a fixed sequence of float ops.  Exact bit equality
// with the Python path is not required (float summation order differs by ~1
// ULP, same as decimate.cpp) but run-to-run determinism is.

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <new>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "params.h"
#include "strip.h"
#include "kernels.h"
#include "trigrid.h"

PyObject* py_bind_geos(PyObject*, PyObject*);   // geos_bind.cpp
PyObject* py_pair_gaps(PyObject*, PyObject*);   // sheets.cpp
PyObject* py_decimate(PyObject*, PyObject*);    // decimate.cpp
PyObject* py_pymath(PyObject*, PyObject*);      // decimate.cpp
PyObject* py_flip2d(PyObject*, PyObject*);      // meshflip.cpp
PyObject* py_hex_refine(PyObject*, PyObject*);  // meshflip.cpp
PyObject* py_bad_welds(PyObject*, PyObject*);   // welds.cpp
PyObject* py_same_surface_pieces(PyObject*, PyObject*);  // sheets.cpp
PyObject* py_stacked_pairs(PyObject*, PyObject*);        // stack.cpp
PyObject* py_notch_overlaps(PyObject*, PyObject*);       // stack.cpp
PyObject* py_candidate_pairs(PyObject*, PyObject*);      // sheets.cpp
PyObject* py_refine_land(PyObject*, PyObject*);          // refine.cpp
PyObject* py_refine_steep(PyObject*, PyObject*);         // refine.cpp
PyObject* py_land_z(PyObject*, PyObject*);               // refine.cpp
PyObject* py_simplify(PyObject*, PyObject*);             // refine.cpp
PyObject* py_cull_slivers(PyObject*, PyObject*);         // cull.cpp
PyObject* py_set_params(PyObject*, PyObject*);           // params.cpp
PyObject* py_union_mesh(PyObject*, PyObject*);           // union.cpp
PyObject* py_corridor_cell(PyObject*, PyObject*);        // pycell.cpp
PyObject* py_seed_graph(PyObject*, PyObject*);           // pycell.cpp
PyObject* py_door_reach(PyObject*, PyObject*);           // pycell.cpp
PyObject* py_pack_nvnm(PyObject*, PyObject*);            // navm.cpp

namespace {

using namespace navgrow;

// --------------------------------------------------------------------------
// Neighbour field: other pathgrid edge centerlines, bucketed in plan.
//
// Used to find where a rail CROSSES another corridor's centerline.  Tested
// during the march (see grow_half_width), so every crossing it reports lies
// on ground the rail has already proven walkable and wall-free.
// --------------------------------------------------------------------------
struct Seg { double ax, ay, az, bx, by, bz; int i, j; };

struct NeighbourField {
    std::vector<Seg> segs;
    double minx = 0.0, miny = 0.0, cell = 256.0;
    std::unordered_map<long long, std::vector<int>> grid;

    void build(const double* nodes, size_t nnodes,
               const int* edges, size_t nedges, const double* node_z) {
        double gminx = 1e300, gminy = 1e300;
        for (size_t e = 0; e < nedges; ++e) {
            const int i = edges[e * 2], j = edges[e * 2 + 1];
            if (i < 0 || j < 0 || (size_t)i >= nnodes || (size_t)j >= nnodes
                || i == j) continue;
            const double ax = nodes[(size_t)i * 2], ay = nodes[(size_t)i * 2 + 1];
            const double bx = nodes[(size_t)j * 2], by = nodes[(size_t)j * 2 + 1];
            // A non-finite node makes the bucket span below non-finite and the
            // insert loop unbounded; skip the edge as the TriGrid builder does.
            if (!std::isfinite(ax) || !std::isfinite(ay) ||
                !std::isfinite(bx) || !std::isfinite(by) ||
                !std::isfinite(node_z[i]) || !std::isfinite(node_z[j]))
                continue;
            const double dx = bx - ax, dy = by - ay;
            if (std::sqrt(dx * dx + dy * dy) < 1e-6) continue;
            Seg s;
            s.ax = ax; s.ay = ay; s.bx = bx; s.by = by;
            s.az = node_z[i]; s.bz = node_z[j];
            s.i = i; s.j = j;
            segs.push_back(s);
            gminx = std::min(gminx, std::min(ax, bx));
            gminy = std::min(gminy, std::min(ay, by));
        }
        if (segs.empty()) return;
        minx = gminx; miny = gminy;
        for (size_t si = 0; si < segs.size(); ++si) {
            const Seg& s = segs[si];
            const double spanx =
                (std::max(s.ax, s.bx) - std::min(s.ax, s.bx)) / cell;
            const double spany =
                (std::max(s.ay, s.by) - std::min(s.ay, s.by)) / cell;
            if (spanx > (double)kMaxBuckets || spany > (double)kMaxBuckets ||
                spanx * spany > (double)kMaxBuckets)
                throw std::invalid_argument("pathgrid edge span too large");
            const long long gx0 = (long long)std::floor(
                (std::min(s.ax, s.bx) - minx) / cell);
            const long long gx1 = (long long)std::floor(
                (std::max(s.ax, s.bx) - minx) / cell);
            const long long gy0 = (long long)std::floor(
                (std::min(s.ay, s.by) - miny) / cell);
            const long long gy1 = (long long)std::floor(
                (std::max(s.ay, s.by) - miny) / cell);
            for (long long gx = gx0 - 1; gx <= gx1 + 1; ++gx)
                for (long long gy = gy0 - 1; gy <= gy1 + 1; ++gy)
                    grid[(gx << 32) ^ (gy & 0xffffffffLL)].push_back((int)si);
        }
    }

    // Distance along the step (x0,y0)->(x1,y1), as a fraction in (0, 1], at
    // which it first CROSSES another edge's centerline at the rail's own level;
    // -1 when it crosses none.  ex_a/ex_b are the marching station's own nodes:
    // an edge touching either is never counted.
    double first_crossing(double x0, double y0, double x1, double y1, double z,
                          int ex_a, int ex_b, double ztol) const {
        if (segs.empty()) return -1.0;
        const long long gx = (long long)std::floor((x1 - minx) / cell);
        const long long gy = (long long)std::floor((y1 - miny) / cell);
        auto it = grid.find((gx << 32) ^ (gy & 0xffffffffLL));
        if (it == grid.end()) return -1.0;
        const double rx = x1 - x0, ry = y1 - y0;
        double best = -1.0;
        for (int si : it->second) {
            const Seg& s = segs[(size_t)si];
            if (s.i == ex_a || s.j == ex_a || s.i == ex_b || s.j == ex_b)
                continue;
            const double sx = s.bx - s.ax, sy = s.by - s.ay;
            const double den = rx * sy - ry * sx;
            if (std::fabs(den) < 1e-12) continue;
            const double qx = s.ax - x0, qy = s.ay - y0;
            const double t = (qx * sy - qy * sx) / den;
            const double u = (qx * ry - qy * rx) / den;
            if (t <= 0.0 || t > 1.0 || u < 0.0 || u > 1.0) continue;
            if (std::fabs(s.az + (s.bz - s.az) * u - z) > ztol) continue;
            if (best < 0.0 || t < best) best = t;
        }
        return best;
    }
};

// Tunables mirrored from params.py, passed in so the two can never drift.
struct Params {
    double step, min_half, half_width, slab_half_w, slab_depth;
    double slab_z_bottom, agent_height, max_climb, ztol, cap;
    int bisect, land_from;
};

// --------------------------------------------------------------------------
// One outward march (mirrors grow_half_width).
// --------------------------------------------------------------------------
double grow_half_width(const TriGrid& wall, const TriGrid* walk,
                       const NeighbourField& field, const Land* land,
                       double cx, double cy, double floor_z,
                       double dirx, double diry, double tanx, double tany,
                       int ex_a, int ex_b, double lo, const Params& P, bool exact) {

    // A terrain-standing rail keeps its height ABOVE THE LAND, not its height.
    const double land_c = land ? land->z(cx, cy) : 0.0;
    auto level = [&](double at) {
        return land ? floor_z + land->z(cx + dirx * at, cy + diry * at) - land_c
                    : floor_z;
    };

    double grown = 0.0, d = 0.0, limit = -1.0, built = 0.0, beyond = 0.0;
    for (;;) {
        const double nd_step = P.step;
        const double prev = d;
        d += nd_step;
        // (a) WALL -- sweep the interval [prev, d] so a wall between two
        // samples cannot be stepped over.
        const double mid = 0.5 * (prev + d);
        const double sweep = 0.5 * nd_step + P.slab_depth;
        const double z_lo = level(mid) + P.slab_z_bottom;
        const double z_hi = level(mid) + P.agent_height;
        if (wall_hit(wall, cx + dirx * mid, cy + diry * mid, dirx, diry,
                     tanx, tany, z_lo, z_hi, sweep, P.slab_half_w, exact)) {
            // Bisect so the rail ends AT the wall, not a step short of it.
            double lo_d = prev, hi_d = d;
            for (int b = 0; b < P.bisect; ++b) {
                const double md = 0.5 * (lo_d + hi_d);
                const double mm = 0.5 * (lo_d + md);
                if (wall_hit(wall, cx + dirx * mm, cy + diry * mm, dirx, diry,
                             tanx, tany, z_lo, z_hi,
                             0.5 * (md - lo_d) + P.slab_depth, P.slab_half_w, exact))
                    hi_d = md;
                else
                    lo_d = md;
            }
            grown = std::max(grown, lo_d);
            break;
        }
        // (b) another corridor's CENTERLINE -- the rail ends where it crosses
        // one (never short of the soft floor).  Ground past that line is the
        // other corridor's to cover, and it grows from there by these rules.
        const double t = field.first_crossing(
            cx + dirx * prev, cy + diry * prev, cx + dirx * d, cy + diry * d,
            level(d), ex_a, ex_b, P.ztol);
        if (t >= 0.0 && limit < 0.0)
            limit = std::max(prev + t * nd_step, std::max(P.cap, lo));
        const bool last = limit >= 0.0 && limit <= d;
        if (last) d = limit;
        // (c) past the cell's own LAND the neighbour's terrain carries on: a
        // terrain rail overshoots the cell plane by one cap, so the sheet is
        // CUT on the plane by the cell clip instead of ending short of it.
        if (land && !land->covers(cx + dirx * d, cy + diry * d)) {
            beyond += nd_step;
            grown = d;
            if (last || beyond >= P.cap) break;
            continue;
        }
        // (d) walkable floor edge -- binds only BEYOND the soft floor `lo`.
        const double ref = level(d);
        double s = ref;
        int tri = -1;
        const bool ground = walk && walk_sample(
            *walk, cx + dirx * d, cy + diry * d, ref, &s, &tri);
        if (walk && d > lo && (!ground || std::fabs(s - ref) > P.max_climb))
            break;
        grown = d;
        // (e) the BUILT-floor budget: terrain is free, authored floor is not.
        if (!(ground && tri >= P.land_from)) built += nd_step;
        if (last || built >= std::max(P.cap, lo)) break;
    }
    return grown;
}

// Every station's grown half-width into `out` (stations: (N, 9) rows of cx,
// cy, cz, dirx, diry, tanx, tany, lo, edge_index; on_land per station or null).
// The discs of `exact_nodes` (actor spawns) test walls by what stands in the slab.
void grow_march(const TriGrid& wall, const TriGrid* walk, const double* nodesp,
                size_t nnode, const int* edgesp, size_t nedge, const double* nzp,
                const Land* land, const double* stp, size_t nst, const double* onlandp,
                const Params& P, double* out, const std::set<int>* exact_nodes = nullptr) {
    NeighbourField field;
    if (nedge) field.build(nodesp, nnode, edgesp, nedge, nzp);
    for (size_t s = 0; s < nst; ++s) {
        const double* r = &stp[s * 9];
        // Exclude the station's OWN centerline(s) from the crossing test.
        // Column 9 is >= 0 for an edge station (an index into `edges`), and
        // NEGATIVE for a node disc, encoding -1 - node_index: a disc's rays
        // start where every edge at that node meets.
        int ex_a = -1, ex_b = -1;
        const long long ei = (long long)r[8];
        if (ei >= 0 && (size_t)ei < nedge) {
            ex_a = edgesp[(size_t)ei * 2];
            ex_b = edgesp[(size_t)ei * 2 + 1];
        } else if (ei < 0) {
            ex_a = ex_b = (int)(-1 - ei);
        }
        const bool exact = ei < 0 && exact_nodes && exact_nodes->count((int)(-1 - ei));
        out[s] = grow_half_width(wall, walk, field,
                                 (onlandp && onlandp[s] > 0.0 && land->g) ? land : nullptr,
                                 r[0], r[1], r[2], r[3], r[4], r[5], r[6], ex_a, ex_b, r[7], P,
                                 exact);
    }
}

// --------------------------------------------------------------------------
// Python entry point
// --------------------------------------------------------------------------
PyArrayObject* as_f64(PyObject* o) {
    return (PyArrayObject*)PyArray_FROMANY(
        o, NPY_DOUBLE, 0, 0, NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED);
}

// grow_strips(blocking, walkable, stations, params, nodes, edges, node_z,
//             land, on_land)
//
// `stations` is an (N, 9) float64 array, one row per march to perform:
//   cx, cy, cz, dirx, diry, tanx, tany, lo, edge_index
// edge_index indexes `edges`, so the march can exclude its OWN edge from the
// crossing test; a node disc passes -1 - node_index and excludes that node.
//
// nodes (M,2) float64, edges (E,2) int32 and node_z (M,) float64 build the
// neighbour field.  Passing None for any of them disables the crossing stop.
//
// land (33,33) float64 is the cell's LAND grid (origin in params land_ox /
// land_oy) and on_land (N,) float64 flags the stations that follow it.
//
// Returns a float64 array of N grown half-widths.
PyObject* py_grow_strips(PyObject*, PyObject* args) {
    PyObject *o_block, *o_walk, *o_st, *o_par;
    PyObject *o_nodes = Py_None, *o_edges = Py_None, *o_nz = Py_None;
    PyObject *o_land = Py_None, *o_onland = Py_None;
    if (!PyArg_ParseTuple(args, "OOOO|OOOOO", &o_block, &o_walk, &o_st, &o_par,
                          &o_nodes, &o_edges, &o_nz, &o_land, &o_onland))
        return nullptr;

    PyArrayObject* a_block = as_f64(o_block);
    PyArrayObject* a_st = as_f64(o_st);
    PyArrayObject* a_walk = (o_walk == Py_None) ? nullptr : as_f64(o_walk);
    if (!a_block || !a_st || (o_walk != Py_None && !a_walk)) {
        Py_XDECREF(a_block); Py_XDECREF(a_st); Py_XDECREF(a_walk);
        return nullptr;
    }
    const bool want_field =
        (o_nodes != Py_None && o_edges != Py_None && o_nz != Py_None);
    PyArrayObject* a_nodes = want_field ? as_f64(o_nodes) : nullptr;
    PyArrayObject* a_nz = want_field ? as_f64(o_nz) : nullptr;
    PyArrayObject* a_edges = want_field ? (PyArrayObject*)PyArray_FROMANY(
        o_edges, NPY_INT32, 0, 0,
        NPY_ARRAY_C_CONTIGUOUS | NPY_ARRAY_ALIGNED) : nullptr;
    const bool want_land = (o_land != Py_None && o_onland != Py_None);
    PyArrayObject* a_land = want_land ? as_f64(o_land) : nullptr;
    PyArrayObject* a_onland = want_land ? as_f64(o_onland) : nullptr;
    const bool bad_land = want_land && (!a_land || !a_onland ||
        PyArray_SIZE(a_land) != 33 * 33 ||
        PyArray_SIZE(a_onland) != PyArray_SIZE(a_st) / 9);
    if (bad_land && !PyErr_Occurred())
        PyErr_SetString(PyExc_ValueError, "land must be 33x33, on_land (N,)");
    if (bad_land || (want_field && (!a_nodes || !a_nz || !a_edges))) {
        Py_XDECREF(a_block); Py_XDECREF(a_st); Py_XDECREF(a_walk);
        Py_XDECREF(a_nodes); Py_XDECREF(a_nz); Py_XDECREF(a_edges);
        Py_XDECREF(a_land); Py_XDECREF(a_onland);
        return nullptr;
    }

    Params P;
    auto getd = [&](const char* k, double dflt) {
        PyObject* v = PyDict_GetItemString(o_par, k);
        return v ? PyFloat_AsDouble(v) : dflt;
    };
    P.step = getd("step", 8.0);
    P.min_half = getd("min_half", 16.0);
    P.half_width = getd("half_width", 40.0);
    P.slab_half_w = getd("slab_half_w", 20.0);
    P.slab_depth = getd("slab_depth", 6.0);
    P.slab_z_bottom = getd("slab_z_bottom", 12.0);
    P.agent_height = getd("agent_height", 128.0);
    P.max_climb = getd("max_climb", 34.0);
    P.ztol = getd("ztol", 96.0);
    P.cap = getd("cap", 160.0);
    P.land_from = (int)getd("land_from", 2147483647.0);

    P.bisect = (int)getd("bisect", 4.0);
    Land land;
    land.g = a_land ? (const double*)PyArray_DATA(a_land) : nullptr;
    land.ox = getd("land_ox", 0.0);
    land.oy = getd("land_oy", 0.0);
    const double* onlandp =
        a_onland ? (const double*)PyArray_DATA(a_onland) : nullptr;

    const size_t nblock = (size_t)(PyArray_SIZE(a_block) / 9);
    const size_t nwalk = a_walk ? (size_t)(PyArray_SIZE(a_walk) / 9) : 0;
    const size_t nst = (size_t)(PyArray_SIZE(a_st) / 9);

    const double* blockp = (const double*)PyArray_DATA(a_block);
    const double* walkp = a_walk ? (const double*)PyArray_DATA(a_walk) : nullptr;
    const double* stp = (const double*)PyArray_DATA(a_st);
    const size_t nnode = a_nodes ? (size_t)(PyArray_SIZE(a_nodes) / 2) : 0;
    const size_t nedge = a_edges ? (size_t)(PyArray_SIZE(a_edges) / 2) : 0;
    const double* nodesp =
        a_nodes ? (const double*)PyArray_DATA(a_nodes) : nullptr;
    const int* edgesp = a_edges ? (const int*)PyArray_DATA(a_edges) : nullptr;
    const double* nzp = a_nz ? (const double*)PyArray_DATA(a_nz) : nullptr;

    npy_intp dims[1] = {(npy_intp)nst};
    PyArrayObject* out = (PyArrayObject*)PyArray_SimpleNew(1, dims, NPY_DOUBLE);
    if (!out) {
        Py_DECREF(a_block); Py_DECREF(a_st); Py_XDECREF(a_walk);
        Py_XDECREF(a_nodes); Py_XDECREF(a_nz); Py_XDECREF(a_edges);
        Py_XDECREF(a_land); Py_XDECREF(a_onland);
        return nullptr;
    }
    double* outp = (double*)PyArray_DATA(out);

    // Index building and the march are pure C on copied data -- no Python
    // objects are touched, so the GIL can be dropped for the whole run.
    //
    // EVERYTHING between the GIL macros MUST be inside the try. A C++ exception
    // that escapes here cannot become a Python error (the GIL is not held) and
    // there is no handler further out, so it reaches std::terminate() and the
    // process dies by abort() -- in a pool worker that surfaces in the parent as
    // an opaque BrokenProcessPool with no traceback, and under pythonw.exe the
    // worker's stderr goes nowhere either. That is exactly how Nehrim's cell
    // 011E4FEC (a garbage exported PosY of 8.9e17) killed the import.
    // So: stash the message, reacquire the GIL, then raise normally.
    std::string err;
    Py_BEGIN_ALLOW_THREADS
    try {
        TriGrid wall, walk;
        wall.build(blockp, nblock, 128.0);
        if (walkp) {
            walk.build(walkp, nwalk, 128.0);
            walk.build_walk_index();
        }
        grow_march(wall, walkp ? &walk : nullptr, nodesp, nnode, edgesp, nedge, nzp,
                   &land, stp, nst, onlandp, P, outp);
    } catch (const std::bad_alloc&) {
        err = "out of memory building the navmesh triangle index";
    } catch (const std::exception& e) {
        err = e.what();
    } catch (...) {
        err = "unknown error in grow_strips";
    }
    Py_END_ALLOW_THREADS

    Py_DECREF(a_block); Py_DECREF(a_st); Py_XDECREF(a_walk);
    Py_XDECREF(a_nodes); Py_XDECREF(a_nz); Py_XDECREF(a_edges);
    Py_XDECREF(a_land); Py_XDECREF(a_onland);
    if (!err.empty()) {
        Py_DECREF(out);
        PyErr_SetString(PyExc_ValueError, err.c_str());
        return nullptr;
    }
    return (PyObject*)out;
}

}  // namespace

std::vector<double> navgrow::grow_widths(const TriGrid& wall, const TriGrid* walk,
                                         const std::vector<double>& nodes_xy,
                                         const std::vector<int>& edges,
                                         const std::vector<double>& node_z, const Land* land,
                                         const std::vector<double>& stations,
                                         const std::vector<double>& on_land, int land_from,
                                         const std::set<int>* exact_nodes) {
    const NavParams& N = P();
    Params G;
    G.step = N.RIBBON_GROW_STEP;
    G.min_half = N.RIBBON_GROW_MIN_HALF;
    G.half_width = N.RIBBON_HALF_WIDTH;
    G.slab_half_w = N.RIBBON_GROW_SLAB_HALF_WIDTH;
    G.slab_depth = N.RIBBON_GROW_SLAB_DEPTH;
    G.slab_z_bottom = N.RIBBON_GROW_SLAB_Z_BOTTOM;
    G.agent_height = N.AGENT_HEIGHT;
    G.max_climb = N.MAX_CLIMB;
    G.ztol = N.SEED_Z_TOLERANCE;
    G.cap = N.RIBBON_GROW_MAX_HALF;
    G.bisect = (int)N.RIBBON_GROW_BISECT;
    G.land_from = land_from;
    const Land none;
    std::vector<double> out(stations.size() / 9);
    grow_march(wall, walk, nodes_xy.data(), nodes_xy.size() / 2, edges.data(), edges.size() / 2,
               node_z.data(), land ? land : &none, stations.data(), out.size(),
               land ? on_land.data() : nullptr, G, out.data(), exact_nodes);
    return out;
}

std::vector<std::vector<double>> navgrow::levels_at_points(
    const std::vector<Strip>& strips, const double* pp, const double* fp,
                                                  const Land& land, const double* qp,
                                                  size_t nq, double same_z) {
    const size_t ns = strips.size();
    const double cell = 256.0;
    double gx0 = 1e300, gy0 = 1e300;
    for (const Strip& S : strips) {
        gx0 = std::min(gx0, S.x0); gy0 = std::min(gy0, S.y0);
    }
    if (!ns) { gx0 = gy0 = 0.0; }
    std::unordered_map<long long, std::vector<int>> grid;
    for (size_t i = 0; i < ns; ++i) {
        const Strip& S = strips[i];
        const double spanx = (S.x1 - S.x0) / cell;
        const double spany = (S.y1 - S.y0) / cell;
        if (spanx < 0.0 || spany < 0.0 ||
            spanx > (double)kMaxBuckets || spany > (double)kMaxBuckets ||
            spanx * spany > (double)kMaxBuckets)
            throw std::invalid_argument("strip XY extent too large");
        const long long bx0 = (long long)std::floor((S.x0 - gx0) / cell);
        const long long bx1 = (long long)std::floor((S.x1 - gx0) / cell);
        const long long by0 = (long long)std::floor((S.y0 - gy0) / cell);
        const long long by1 = (long long)std::floor((S.y1 - gy0) / cell);
        for (long long bx = bx0; bx <= bx1; ++bx)
            for (long long by = by0; by <= by1; ++by)
                grid[(bx << 32) ^ (by & 0xffffffffLL)].push_back((int)i);
    }
    std::vector<std::vector<double>> out(nq);
    std::vector<double> zs;
    for (size_t n = 0; n < nq; ++n) {
        const double px = qp[n * 2], py = qp[n * 2 + 1];
        zs.clear();
        const long long bx = (long long)std::floor((px - gx0) / cell);
        const long long by = (long long)std::floor((py - gy0) / cell);
        auto it = grid.find((bx << 32) ^ (by & 0xffffffffLL));
        if (it != grid.end()) {
            for (int si : it->second) {
                const Strip& S = strips[(size_t)si];
                if (px < S.x0 || px > S.x1 || py < S.y0 || py > S.y1) continue;
                bool hit;
                if (S.poly_n > 0) {
                    const double* poly = &pp[(size_t)S.poly_off * 2];
                    hit = point_in_poly(poly, S.poly_n, px, py);
                    if (!hit) {
                        double best = 1e300;
                        for (int k = 0; k < S.poly_n; ++k) {
                            const int k2 = (k + 1) % S.poly_n;
                            best = std::min(best, seg_dist(
                                px, py, poly[k * 2], poly[k * 2 + 1],
                                poly[k2 * 2], poly[k2 * 2 + 1]));
                        }
                        hit = best <= 1e-6;
                    }
                } else {
                    hit = seg_dist(px, py, S.ax, S.ay, S.bx, S.by)
                          <= S.half + 1e-6;
                }
                if (!hit) continue;
                zs.push_back(strip_height<false>(S, fp, land, px, py));
            }
        }
        if (zs.empty()) continue;
        std::sort(zs.begin(), zs.end());
        std::vector<double>& res = out[n];
        double acc = zs[0];
        int cnt = 1;
        double last = zs[0];
        for (size_t k = 1; k < zs.size(); ++k) {
            if (zs[k] - last <= same_z) {
                acc += zs[k]; ++cnt;
            } else {
                res.push_back(acc / cnt);
                acc = zs[k]; cnt = 1;
            }
            last = zs[k];
        }
        res.push_back(acc / cnt);
    }
    return out;
}

namespace {

// --------------------------------------------------------------------------
// Surface levels at a batch of points (corridor_union._levels_at).
//
// WHY THIS IS ALSO NATIVE
// -----------------------
// With the width-grow moved to C++, this became the new dominant cost: 29.3s
// of a 31.9s Wendir02 build, 4.5ms per call over 6,491 calls.  The shape is the
// same -- for each output vertex it scans EVERY strip (~1,900 of them), and a
// grown strip's test is a full point-in-polygon plus a min-distance over all
// its outline edges.  That is O(V x S x E) tiny float ops in Python.
//
// Strips are flattened once into arrays and bucketed by their XY bounds, so a
// point only tests strips whose bounding box actually contains it.
// --------------------------------------------------------------------------

// levels_at(strips_flat, poly_flat, points, same_surface_z[, prof_flat,
//           land, land_ox, land_oy])
//   -> list of lists
//
// strips_flat: (S, 12) float64 -- ax,ay,az,bx,by,bz,half,poly_off,poly_n,
//              prof_off,prof_n,on_land.  An on_land strip's height IS the
//              LAND (33x33 `land`).
// poly_flat  : (P, 2) float64 outline vertices, indexed by poly_off/poly_n.
// points     : (N, 2) float64 query points.
// prof_flat  : (F, 3) float64 height-profile points (x, y, z), indexed by
//              prof_off/prof_n.  A strip with prof_n >= 2 takes its height
//              from the closest projection onto this polyline instead of the
//              a->b chord (the tread-following stair profile).  MUST mirror
//              corridor_union._height_on exactly, tie-breaks included.
PyObject* py_levels_at(PyObject*, PyObject* args) {
    PyObject *o_strips, *o_poly, *o_pts;
    PyObject *o_prof = nullptr, *o_land = Py_None;
    double same_z;
    Land land;
    if (!PyArg_ParseTuple(args, "OOOd|OOdd", &o_strips, &o_poly, &o_pts,
                          &same_z, &o_prof, &o_land, &land.ox, &land.oy))
        return nullptr;
    if (o_prof == Py_None) o_prof = nullptr;

    PyArrayObject* a_s = as_f64(o_strips);
    PyArrayObject* a_p = as_f64(o_poly);
    PyArrayObject* a_q = as_f64(o_pts);
    PyArrayObject* a_f = o_prof ? as_f64(o_prof) : nullptr;
    PyArrayObject* a_l = (o_land != Py_None) ? as_f64(o_land) : nullptr;
    const bool bad_land = (o_land != Py_None) &&
        (!a_l || PyArray_SIZE(a_l) != 33 * 33);
    if (bad_land && !PyErr_Occurred())
        PyErr_SetString(PyExc_ValueError, "land must be 33x33");
    if (!a_s || !a_p || !a_q || (o_prof && !a_f) || bad_land) {
        Py_XDECREF(a_s); Py_XDECREF(a_p); Py_XDECREF(a_q); Py_XDECREF(a_f);
        Py_XDECREF(a_l);
        return nullptr;
    }
    land.g = a_l ? (const double*)PyArray_DATA(a_l) : nullptr;

    const size_t ns = (size_t)(PyArray_SIZE(a_s) / 12);
    const size_t nq = (size_t)(PyArray_SIZE(a_q) / 2);
    const size_t nf = a_f ? (size_t)(PyArray_SIZE(a_f) / 3) : 0;
    const double* sp = (const double*)PyArray_DATA(a_s);
    const double* pp = (const double*)PyArray_DATA(a_p);
    const double* qp = (const double*)PyArray_DATA(a_q);
    const double* fp = a_f ? (const double*)PyArray_DATA(a_f) : nullptr;

    // Same contract as TriGrid::build: non-finite coordinates make the bucket
    // spans below non-finite, the cast to long long undefined, and the
    // push_back loop unbounded. Reject them here rather than allocating until
    // bad_alloc aborts the process.
    {
        const npy_intp checks[4] = {PyArray_SIZE(a_s), PyArray_SIZE(a_p),
                                    PyArray_SIZE(a_q),
                                    a_f ? PyArray_SIZE(a_f) : 0};
        const double* datas[4] = {sp, pp, qp, fp};
        for (int c = 0; c < 4; ++c) {
            for (npy_intp i = 0; i < checks[c]; ++i) {
                if (!std::isfinite(datas[c][i])) {
                    Py_DECREF(a_s); Py_DECREF(a_p); Py_DECREF(a_q);
                    Py_XDECREF(a_f); Py_XDECREF(a_l);
                    PyErr_SetString(PyExc_ValueError,
                                    "non-finite coordinate in levels_at input");
                    return nullptr;
                }
            }
        }
    }

    const std::vector<Strip> strips = load_strips(sp, ns, pp, nf, land.g);
    std::vector<std::vector<double>> out;
    std::string err;
    Py_BEGIN_ALLOW_THREADS
    try {
        out = levels_at_points(strips, pp, fp, land, qp, nq, same_z);
    } catch (const std::bad_alloc&) {
        err = "out of memory in levels_at";
    } catch (const std::exception& e) {
        err = e.what();
    }
    Py_END_ALLOW_THREADS

    if (!err.empty()) {
        Py_DECREF(a_s); Py_DECREF(a_p); Py_DECREF(a_q); Py_XDECREF(a_f);
        Py_XDECREF(a_l);
        PyErr_SetString(PyExc_ValueError, err.c_str());
        return nullptr;
    }

    PyObject* pyout = PyList_New((Py_ssize_t)nq);
    if (pyout) {
        for (size_t n = 0; n < nq; ++n) {
            PyObject* lst = PyList_New((Py_ssize_t)out[n].size());
            if (!lst) { Py_DECREF(pyout); pyout = nullptr; break; }
            for (size_t k = 0; k < out[n].size(); ++k)
                PyList_SET_ITEM(lst, (Py_ssize_t)k,
                                PyFloat_FromDouble(out[n][k]));
            PyList_SET_ITEM(pyout, (Py_ssize_t)n, lst);
        }
    }
    Py_DECREF(a_s); Py_DECREF(a_p); Py_DECREF(a_q); Py_XDECREF(a_f);
    Py_XDECREF(a_l);
    return pyout;
}

PyMethodDef methods[] = {
    {"grow_strips", py_grow_strips, METH_VARARGS,
     "Grown half-widths for a batch of corridor march stations."},
    {"levels_at", py_levels_at, METH_VARARGS,
     "Surface heights at a batch of points, one list per point."},
    {"bind_geos", py_bind_geos, METH_NOARGS,
     "Bind GEOS from shapely's loaded library (idempotent)."},
    {"pair_gaps", py_pair_gaps, METH_VARARGS,
     "Height disagreement over each overlapping ribbon pair."},
    {"decimate", py_decimate, METH_VARARGS,
     "Collapse, flip and split a corridor mesh (clean_decimate.decimate)."},
    {"pymath", py_pymath, METH_VARARGS,
     "Apply a Python-exact float helper, for tests."},
    {"flip2d", py_flip2d, METH_VARARGS,
     "Ratio-improving diagonal flips (union_cdt._flip2d)."},
    {"hex_refine", py_hex_refine, METH_VARARGS,
     "Hex-lattice interior refine, then flips (union_cdt._hex_refine)."},
    {"bad_welds", py_bad_welds, METH_VARARGS,
     "Provisional welds whose triangles overlap other mesh."},
    {"same_surface_pieces", py_same_surface_pieces, METH_VARARGS,
     "Shared ground two sheets mesh at the same height."},
    {"stacked_pairs", py_stacked_pairs, METH_VARARGS,
     "Triangle pairs stacked on the same surface (union_mesh._destack)."},
    {"notch_overlaps", py_notch_overlaps, METH_VARARGS,
     "Which notch fills would stack on meshed ground (_overlaps_existing)."},
    {"candidate_pairs", py_candidate_pairs, METH_VARARGS,
     "Index pairs of intersecting polygons (union_sheets._candidate_pairs)."},
    {"refine_land", py_refine_land, METH_VARARGS,
     "Bisect a terrain sheet's triangles (union_cdt._refine_land)."},
    {"refine_steep", py_refine_steep, METH_VARARGS,
     "Bisect stair triangles carrying seeds (union_cdt._refine_steep)."},
    {"land_z", py_land_z, METH_VARARGS,
     "LandField.z at a batch of points."},
    {"simplify", py_simplify, METH_VARARGS,
     "Douglas-Peucker kept indices (corridor._simplify)."},
    {"cull_slivers", py_cull_slivers, METH_VARARGS,
     "Surviving triangle indices (corridor_clean.cull_boundary_slivers)."},
    {"set_params", py_set_params, METH_O,
     "Set the tunables every native stage reads."},
    {"corridor_cell", py_corridor_cell, METH_VARARGS,
     "One cell's whole corridor build, collision gather included (build_corridors)."},
    {"seed_graph", py_seed_graph, METH_VARARGS,
     "A seeded cell's flooded pathgrid (seeding.seed_graph)."},
    {"door_reach", py_door_reach, METH_VARARGS,
     "Walkable paths from door seeds into a cell's pathgrid (door_reach.door_reach)."},
    {"pack_nvnm", py_pack_nvnm, METH_VARARGS,
     "A built cell's NVNM bytes, door triangles and center (convert_PGRD's tail)."},
    {"union_mesh", py_union_mesh, METH_VARARGS,
     "The corridor union stage (corridor_union.build_union_mesh)."},
    {nullptr, nullptr, 0, nullptr}
};

PyModuleDef moddef = {
    PyModuleDef_HEAD_INIT, "_navgrow_native",
    "Native Phase-2 corridor width-grow.", -1, methods,
    nullptr, nullptr, nullptr, nullptr
};

}  // namespace

int register_sampler(PyObject* module);  // sampler.cpp

PyMODINIT_FUNC PyInit__navgrow_native(void) {
    import_array();
    PyObject* m = PyModule_Create(&moddef);
    if (m && register_sampler(m) < 0) {
        Py_DECREF(m);
        return nullptr;
    }
    return m;
}
