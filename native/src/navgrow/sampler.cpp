// Native collision samplers for the corridor navmesh (TriIndex).
//
// The Python reference builds a dict-of-lists bucket grid per soup and answers
// each query in interpreted float code: corridor._surface_sampler,
// corridor_grow.walkable_sampler and corridor_grow.wall_slab_sampler.  Those
// three were the largest pure-Python cost left in a cell build.  TriIndex
// answers the same queries over the shared TriGrid, with the same candidate
// order and the same float operations, so every answer is bit-identical to the
// Python one (tie-breaks included).

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <new>
#include <string>
#include <vector>

#include "pybuf.h"
#include "trigrid.h"

namespace {

using namespace navgrow;

struct TriIndexObject {
    PyObject_HEAD
    TriGrid* grid;
    std::vector<unsigned>* stamp;
    unsigned epoch;
};

// Parse `n` float arguments; false (with a Python error set) on a bad call.
bool doubles(PyObject* const* args, Py_ssize_t nargs, Py_ssize_t n,
             double* out, const char* name) {
    if (nargs != n) {
        PyErr_Format(PyExc_TypeError, "%s takes %zd arguments", name, n);
        return false;
    }
    for (Py_ssize_t i = 0; i < n; ++i) {
        out[i] = PyFloat_AsDouble(args[i]);
        if (out[i] == -1.0 && PyErr_Occurred()) return false;
    }
    return true;
}

// numpy.float64, which the Python samplers return (their heights come out of
// numpy arithmetic).  Matching the type matters downstream: Python's sum()
// compensates only exact floats, so a float and a float64 sum differently.
PyObject* g_float64 = nullptr;

PyObject* as_float64(double z) {
    PyObject* f = PyFloat_FromDouble(z);
    if (!f) return nullptr;
    PyObject* out = PyObject_CallOneArg(g_float64, f);
    Py_DECREF(f);
    return out;
}

PyObject* nearest_or_none(bool have, double z) {
    if (!have) Py_RETURN_NONE;
    return as_float64(z);
}

// surface(x, y, near_z) -> the single-bucket height nearest near_z, or None.
PyObject* ti_surface(PyObject* self, PyObject* const* args, Py_ssize_t nargs) {
    double a[3], z = 0.0;
    if (!doubles(args, nargs, 3, a, "surface")) return nullptr;
    const bool have = surface_nearest(*((TriIndexObject*)self)->grid, a[0], a[1], a[2], &z);
    return nearest_or_none(have, z);
}

// layers(x, y) -> every distinct single-bucket height, ascending, 2u dedupe.
PyObject* ti_layers(PyObject* self, PyObject* const* args, Py_ssize_t nargs) {
    double a[2];
    if (!doubles(args, nargs, 2, a, "layers")) return nullptr;
    std::vector<double> zs;
    bucket_heights(*((TriIndexObject*)self)->grid, a[0], a[1], zs);
    std::sort(zs.begin(), zs.end());
    std::vector<double> out;
    for (double z : zs)
        if (out.empty() || z - out.back() > 2.0) out.push_back(z);
    PyObject* lst = PyList_New((Py_ssize_t)out.size());
    if (!lst) return nullptr;
    for (size_t k = 0; k < out.size(); ++k) {
        PyObject* f = as_float64(out[k]);
        if (!f) { Py_DECREF(lst); return nullptr; }
        PyList_SET_ITEM(lst, (Py_ssize_t)k, f);
    }
    return lst;
}

// walk(x, y, near_z) -> the 3x3-neighbourhood height nearest near_z, or None
// (corridor_grow.walkable_sampler, Python candidate order).
PyObject* ti_walk(PyObject* self, PyObject* const* args, Py_ssize_t nargs) {
    double a[3], z = 0.0;
    if (!doubles(args, nargs, 3, a, "walk")) return nullptr;
    TriIndexObject* o = (TriIndexObject*)self;
    const bool have = walk_nearest_py(*o->grid, a[0], a[1], a[2], *o->stamp, o->epoch, &z);
    return nearest_or_none(have, z);
}

// wall(cx, cy, ux, uy, tx, ty, z_lo, z_hi, depth, half_w) -> bool
// (corridor_grow.wall_slab_sampler; any hit, so visit order is irrelevant).
PyObject* ti_wall(PyObject* self, PyObject* const* args, Py_ssize_t nargs) {
    double a[10];
    if (!doubles(args, nargs, 10, a, "wall")) return nullptr;
    const bool hit = wall_hit(*((TriIndexObject*)self)->grid, a[0], a[1],
                              a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9]);
    return PyBool_FromLong(hit);
}

// TriIndex(tris): tris is a C-contiguous float64 buffer of N*9 values.
int ti_init(PyObject* self, PyObject* args, PyObject* kw) {
    PyObject* src;
    if (kw && PyDict_Size(kw)) {
        PyErr_SetString(PyExc_TypeError, "TriIndex takes no keywords");
        return -1;
    }
    if (!PyArg_ParseTuple(args, "O", &src)) return -1;
    BufView view(src, 'd', 8);
    if (!view.ok) return -1;
    if (src == Py_None || view.count() % 9 != 0) {
        PyErr_SetString(PyExc_ValueError,
                        "TriIndex wants float64 triangles, 9 values each");
        return -1;
    }
    TriIndexObject* o = (TriIndexObject*)self;
    std::string err;
    try {
        TriGrid* g = new TriGrid();
        delete o->grid;
        o->grid = g;
        g->build(view.data<double>(), view.count() / 9, 128.0);
        delete o->stamp;
        o->stamp = new std::vector<unsigned>(g->ntri, 0u);
        o->epoch = 0;
    } catch (const std::bad_alloc&) {
        err = "out of memory building TriIndex";
    } catch (const std::exception& e) {
        err = e.what();
    }
    if (!err.empty()) {
        PyErr_SetString(PyExc_ValueError, err.c_str());
        return -1;
    }
    return 0;
}

void ti_dealloc(PyObject* self) {
    TriIndexObject* o = (TriIndexObject*)self;
    delete o->grid;
    delete o->stamp;
    Py_TYPE(self)->tp_free(self);
}

PyMethodDef ti_methods[] = {
    {"surface", (PyCFunction)(void (*)(void))ti_surface, METH_FASTCALL,
     "Single-bucket height nearest near_z, or None."},
    {"layers", (PyCFunction)(void (*)(void))ti_layers, METH_FASTCALL,
     "Distinct single-bucket heights, ascending."},
    {"walk", (PyCFunction)(void (*)(void))ti_walk, METH_FASTCALL,
     "3x3-neighbourhood height nearest near_z, or None."},
    {"wall", (PyCFunction)(void (*)(void))ti_wall, METH_FASTCALL,
     "True when a triangle reaches into the oriented slab."},
    {nullptr, nullptr, 0, nullptr}
};

PyTypeObject TriIndexType = {PyVarObject_HEAD_INIT(nullptr, 0)};

}  // namespace

int register_sampler(PyObject* module) {
    PyObject* np = PyImport_ImportModule("numpy");
    if (!np) return -1;
    g_float64 = PyObject_GetAttrString(np, "float64");
    Py_DECREF(np);
    if (!g_float64) return -1;
    TriIndexType.tp_name = "_navgrow_native.TriIndex";
    TriIndexType.tp_basicsize = sizeof(TriIndexObject);
    TriIndexType.tp_flags = Py_TPFLAGS_DEFAULT;
    TriIndexType.tp_doc = "XY bucket index over a triangle soup.";
    TriIndexType.tp_new = PyType_GenericNew;
    TriIndexType.tp_init = ti_init;
    TriIndexType.tp_dealloc = ti_dealloc;
    TriIndexType.tp_methods = ti_methods;
    if (PyType_Ready(&TriIndexType) < 0) return -1;
    Py_INCREF(&TriIndexType);
    if (PyModule_AddObject(module, "TriIndex", (PyObject*)&TriIndexType) < 0) {
        Py_DECREF(&TriIndexType);
        return -1;
    }
    return 0;
}
