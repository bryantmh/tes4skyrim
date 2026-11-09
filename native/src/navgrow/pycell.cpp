// The per-cell Python entries: corridor_cell (one whole cell build) and
// seed_graph (seeding's flood).  Inputs are read with the GIL held, then the
// build runs with it released, so cells build in parallel on Python threads.

#include "cell.h"
#include "pybuf.h"
#include "pyconv.h"

#include <cmath>
#include <string>

using namespace navgrow;

namespace {

// Buffers borrowed for one call, released (GIL held) when the call returns.
struct Held {
    std::vector<Py_buffer> bufs;
    Held() { bufs.reserve(64); }
    ~Held() { for (Py_buffer& b : bufs) PyBuffer_Release(&b); }
};

// (N, k) float64 rows as a flat buffer; false with an error set.
bool rows_of(size_t k, BufView& v) {
    if (!v.ok) return false;
    if (v.count() % k) {
        PyErr_SetString(PyExc_ValueError, "malformed row array");
        return false;
    }
    return true;
}

// A float32 or float64 soup (None reads empty); false with an error set.
bool soup_of(PyObject* o, Held& held, SoupView& s) {
    if (o == Py_None) return true;
    Py_buffer b{};
    if (PyObject_GetBuffer(o, &b, PyBUF_C_CONTIGUOUS | PyBUF_FORMAT) < 0) return false;
    const char* f = b.format ? b.format : "B";
    while (*f == '<' || *f == '=' || *f == '@') ++f;
    const bool f32 = f[0] == 'f' && b.itemsize == 4, f64 = f[0] == 'd' && b.itemsize == 8;
    if ((!f32 && !f64) || f[1] != '\0') {
        PyBuffer_Release(&b);
        PyErr_SetString(PyExc_ValueError, "a collision soup must be float32 or float64");
        return false;
    }
    held.bufs.push_back(b);
    s.n = (size_t)(b.len / b.itemsize);
    if (f32) s.f = (const float*)b.buf;
    else s.d = (const double*)b.buf;
    return true;
}

// Placements from (R, 14) rows [scale, x, y, z, rot 0..8, door] and R (w, b) soups.
bool refs_of(PyObject* o_rows, PyObject* o_soups, Held& held, std::vector<PlacedRef>& out) {
    BufView rv(o_rows, 'd', 8);
    if (!rows_of(14, rv)) return false;
    PyObject* seq = PySequence_Fast(o_soups, "soups must be a sequence");
    if (!seq) return false;
    const size_t n = rv.count() / 14;
    bool ok = (size_t)PySequence_Fast_GET_SIZE(seq) == n;
    if (!ok) PyErr_SetString(PyExc_ValueError, "one (w, b) soup pair per placement row");
    for (size_t i = 0; ok && i < n; ++i) {
        const double* r = &rv.data<double>()[i * 14];
        PlacedRef p;
        p.scale = r[0];
        std::copy(r + 1, r + 4, p.pos);
        std::copy(r + 4, r + 13, p.rot);
        p.door = r[13] != 0.0;
        PyObject* pair = PySequence_Fast_GET_ITEM(seq, (Py_ssize_t)i);
        ok = PyTuple_Check(pair) && PyTuple_GET_SIZE(pair) == 2
             && soup_of(PyTuple_GET_ITEM(pair, 0), held, p.w)
             && soup_of(PyTuple_GET_ITEM(pair, 1), held, p.b);
        if (!ok && !PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, "soups are (w, b) pairs");
        out.push_back(p);
    }
    Py_DECREF(seq);
    return ok;
}

// The (33, 33) LAND grid or None into `land`; false with an error set.
bool land_of(PyObject* o, BufView& lv, double ox, double oy, Land& land) {
    if (!lv.ok) return false;
    if (o != Py_None && lv.count() != 33 * 33) {
        PyErr_SetString(PyExc_ValueError, "land must be 33x33");
        return false;
    }
    land.g = o != Py_None ? lv.data<double>() : nullptr;
    land.ox = ox;
    land.oy = oy;
    return true;
}

// A float or None; false with an error set.
bool water_of(PyObject* o, double* z, bool* has) {
    *has = o != Py_None;
    if (*has) *z = PyFloat_AsDouble(o);
    return !PyErr_Occurred();
}

std::vector<P3> points_of(const BufView& v) {
    std::vector<P3> out;
    for (size_t i = 0; i < v.count() / 3; ++i)
        out.push_back({v.data<double>()[3 * i], v.data<double>()[3 * i + 1], v.data<double>()[3 * i + 2]});
    return out;
}

// The pathgrid, doors and spawn indices of a corridor_cell call into C.
bool graph_of(PyObject* o_lone, const BufView& nv, const BufView& ev, const BufView& dv, CellCtx& C) {
    C.nodes = points_of(nv);
    for (size_t i = 0; i < ev.count() / 2; ++i)
        C.edges.push_back({ev.data<int>()[2 * i], ev.data<int>()[2 * i + 1]});
    for (size_t i = 0; i < dv.count() / 6; ++i) {
        const double* r = &dv.data<double>()[6 * i];
        C.doors.push_back({r[0], r[1], r[2], r[3], r[4] != 0.0, r[5]});
    }
    PyObject* it = PyObject_GetIter(o_lone);
    if (!it) return false;
    for (PyObject* x; (x = PyIter_Next(it));) {
        C.lone.insert((int)PyLong_AsLong(x));
        Py_DECREF(x);
    }
    Py_DECREF(it);
    return !PyErr_Occurred();
}

PyObject* cell_result(const CleanOutput& out) {
    PyObject* pv = PyList_New((Py_ssize_t)out.V.size());
    for (size_t i = 0; pv && i < out.V.size(); ++i)
        PyList_SET_ITEM(pv, (Py_ssize_t)i, Py_BuildValue("(ddd)", out.V[i].x, out.V[i].y, out.V[i].z));
    PyObject* pt = tris_to_python(out.T);
    PyObject* pl = PyList_New((Py_ssize_t)out.ledges.size());
    for (size_t i = 0; pl && i < out.ledges.size(); ++i)
        PyList_SET_ITEM(pl, (Py_ssize_t)i,
                        Py_BuildValue("(iid)", out.ledges[i].hi, out.ledges[i].lo, out.ledges[i].drop));
    if (!pv || !pt || !pl) {
        Py_XDECREF(pv); Py_XDECREF(pt); Py_XDECREF(pl);
        return nullptr;
    }
    return Py_BuildValue("(NNN)", pv, pt, pl);
}

PyObject* graph_result(const std::vector<P3>& points, const std::vector<std::pair<int, int>>& edges) {
    PyObject* pp = PyList_New((Py_ssize_t)points.size());
    for (size_t i = 0; pp && i < points.size(); ++i)
        PyList_SET_ITEM(pp, (Py_ssize_t)i, Py_BuildValue("(ddd)", points[i][0], points[i][1], points[i][2]));
    PyObject* pe = PyList_New((Py_ssize_t)edges.size());
    for (size_t i = 0; pe && i < edges.size(); ++i)
        PyList_SET_ITEM(pe, (Py_ssize_t)i, Py_BuildValue("(ii)", edges[i].first, edges[i].second));
    if (!pp || !pe) {
        Py_XDECREF(pp); Py_XDECREF(pe);
        return nullptr;
    }
    return Py_BuildValue("(NN)", pp, pe);
}

// Run `body` with the GIL released; false (ValueError set) when it threw.
template <class F>
bool released(const char* what, F&& body) {
    std::string err;
    Py_BEGIN_ALLOW_THREADS
    try {
        body();
    } catch (const std::exception& e) {
        err = e.what();
    }
    Py_END_ALLOW_THREADS
    if (err.empty()) return true;
    PyErr_SetString(PyExc_ValueError, (std::string(what) + ": " + err).c_str());
    return false;
}

}  // namespace

// corridor_cell(nodes, edges, lone, doors, land, land_ox, land_oy, cell_clip,
//               refs, soups, obstacles, obstacle_soups, water_z)
//   -> (verts, tris, ledges)
//
// corridor.build_corridors for one cell, collision gather and obstacle bridge
// included.  nodes (N, 3) and doors (D, 6: x, y, z, rot_z, is_teleport, width)
// float64; edges (E, 2) int32; lone the spawn node indices; land the (33, 33)
// LAND or None; refs / obstacles (R, 14) placement rows (world.placements)
// with their (w, b) soups; water_z the open water height or None.
PyObject* py_corridor_cell(PyObject*, PyObject* args) {
    PyObject *o_n, *o_e, *o_lone, *o_d, *o_land, *o_clip, *o_r, *o_rs, *o_o, *o_os, *o_wz;
    double ox, oy;
    if (!PyArg_ParseTuple(args, "OOOOOddOOOOOO", &o_n, &o_e, &o_lone, &o_d, &o_land, &ox, &oy,
                          &o_clip, &o_r, &o_rs, &o_o, &o_os, &o_wz))
        return nullptr;
    if (!geos()) return nullptr;
    BufView nv(o_n, 'd', 8), ev(o_e, 'i', 4), dv(o_d, 'd', 8), lv(o_land, 'd', 8);
    Held held;
    CellCtx C;
    Land land;
    std::vector<PlacedRef> refs, obstacles;
    double wz = 0.0;
    bool has_wz = false;
    if (!rows_of(3, nv) || !rows_of(2, ev) || !rows_of(6, dv) || !land_of(o_land, lv, ox, oy, land)
        || !refs_of(o_r, o_rs, held, refs) || !refs_of(o_o, o_os, held, obstacles)
        || !water_of(o_wz, &wz, &has_wz) || !graph_of(o_lone, nv, ev, dv, C)
        || !read_bounds(o_clip, C.clip, &C.has_clip))
        return nullptr;
    C.land = land.g ? &land : nullptr;
    CleanOutput out;
    if (!released("corridor_cell", [&] { out = build_cell(C, refs, obstacles, has_wz ? &wz : nullptr); }))
        return nullptr;
    return cell_result(out);
}

// seed_graph(refs, soups, land, ox, oy, water_z, seeds) -> (points, edges)
//
// seeding.seed_graph over the cell's gathered collision; seeds (S, 3) float64.
PyObject* py_seed_graph(PyObject*, PyObject* args) {
    PyObject *o_r, *o_rs, *o_land, *o_wz, *o_s;
    double ox, oy;
    if (!PyArg_ParseTuple(args, "OOOddOO", &o_r, &o_rs, &o_land, &ox, &oy, &o_wz, &o_s))
        return nullptr;
    BufView lv(o_land, 'd', 8), sv(o_s, 'd', 8);
    Held held;
    Land land;
    std::vector<PlacedRef> refs;
    double wz = 0.0;
    bool has_wz = false;
    if (!land_of(o_land, lv, ox, oy, land) || !refs_of(o_r, o_rs, held, refs)
        || !water_of(o_wz, &wz, &has_wz) || !rows_of(3, sv))
        return nullptr;
    const std::vector<P3> seeds = points_of(sv);
    std::vector<P3> points;
    std::vector<std::pair<int, int>> edges;
    if (!released("seed_graph", [&] {
            const Land* L = land.g ? &land : nullptr;
            const Soups S = gather_cell(refs, L, has_wz ? &wz : nullptr);
            CellGeo geo;
            geo.build(S.walk.data(), S.walk.size() / 9, S.block.data(), S.block.size() / 9);
            Ground ground{geo, L};
            seed_graph(seeds, ground, ox, oy, points, edges);
        }))
        return nullptr;
    return graph_result(points, edges);
}

// door_reach(refs, soups, land, ox, oy, water_z, seeds, anchors) -> (points, edges, joins)
//
// door_reach.door_reach over the cell's gathered collision; seeds (S, 3) and
// anchors (A, 3) float64; joins are (point, anchor) index pairs.
PyObject* py_door_reach(PyObject*, PyObject* args) {
    PyObject *o_r, *o_rs, *o_land, *o_wz, *o_s, *o_a;
    double ox, oy;
    if (!PyArg_ParseTuple(args, "OOOddOOO", &o_r, &o_rs, &o_land, &ox, &oy, &o_wz, &o_s, &o_a))
        return nullptr;
    BufView lv(o_land, 'd', 8), sv(o_s, 'd', 8), av(o_a, 'd', 8);
    Held held;
    Land land;
    std::vector<PlacedRef> refs;
    double wz = 0.0;
    bool has_wz = false;
    if (!land_of(o_land, lv, ox, oy, land) || !refs_of(o_r, o_rs, held, refs)
        || !water_of(o_wz, &wz, &has_wz) || !rows_of(3, sv) || !rows_of(3, av))
        return nullptr;
    const std::vector<P3> seeds = points_of(sv), anchors = points_of(av);
    std::vector<P3> points;
    std::vector<std::pair<int, int>> edges, joins;
    if (!released("door_reach", [&] {
            const Land* L = land.g ? &land : nullptr;
            const Soups S = gather_cell(refs, L, has_wz ? &wz : nullptr);
            CellGeo geo;
            geo.build(S.walk.data(), S.walk.size() / 9, S.block.data(), S.block.size() / 9);
            Ground ground{geo, L};
            door_reach(seeds, anchors, ground, points, edges, joins);
        }))
        return nullptr;
    PyObject* graph = graph_result(points, edges);
    PyObject* pj = graph ? graph_result({}, joins) : nullptr;
    if (!pj) {
        Py_XDECREF(graph);
        return nullptr;
    }
    PyObject* out = Py_BuildValue("(OOO)", PyTuple_GET_ITEM(graph, 0), PyTuple_GET_ITEM(graph, 1),
                                  PyTuple_GET_ITEM(pj, 1));
    Py_DECREF(graph);
    Py_DECREF(pj);
    return out;
}
