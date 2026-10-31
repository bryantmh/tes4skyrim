// Python <-> native conversions shared by the stage bindings (pyconv.h).

#include "pyconv.h"

#include <algorithm>
#include <cmath>

namespace navgrow {

namespace {

PyObject* g_float64 = nullptr;

bool float64_type() {
    if (g_float64) return true;
    PyObject* np = PyImport_ImportModule("numpy");
    if (!np) return false;
    g_float64 = PyObject_GetAttrString(np, "float64");
    Py_DECREF(np);
    return g_float64 != nullptr;
}

bool item_double(PyObject* seq, Py_ssize_t k, double* out) {
    *out = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(seq, k));
    return !(*out == -1.0 && PyErr_Occurred());
}

}  // namespace

bool read_verts(PyObject* o, std::vector<Vert>& V, bool keep_types) {
    PyObject* f = PySequence_Fast(o, "verts must be a sequence");
    if (!f) return false;
    const Py_ssize_t n = PySequence_Fast_GET_SIZE(f);
    V.resize((size_t)n);
    bool ok = true;
    for (Py_ssize_t i = 0; ok && i < n; ++i) {
        PyObject* v = PySequence_Fast(PySequence_Fast_GET_ITEM(f, i), "a vertex must be a sequence");
        ok = v && PySequence_Fast_GET_SIZE(v) >= 3;
        Vert& out = V[(size_t)i];
        ok = ok && item_double(v, 0, &out.x) && item_double(v, 1, &out.y)
            && item_double(v, 2, &out.z);
        if (ok) out.znp = keep_types && !PyFloat_CheckExact(PySequence_Fast_GET_ITEM(v, 2));
        Py_XDECREF(v);
    }
    Py_DECREF(f);
    if (!ok && !PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, "bad vertex");
    return ok;
}

bool read_tris(PyObject* o, size_t nverts, std::vector<Tri>& T) {
    PyObject* f = PySequence_Fast(o, "tris must be a sequence");
    if (!f) return false;
    const Py_ssize_t n = PySequence_Fast_GET_SIZE(f);
    T.resize((size_t)n);
    bool ok = true;
    for (Py_ssize_t i = 0; ok && i < n; ++i) {
        PyObject* t = PySequence_Fast(PySequence_Fast_GET_ITEM(f, i), "a triangle must be a sequence");
        ok = t && PySequence_Fast_GET_SIZE(t) >= 3;
        for (int k = 0; ok && k < 3; ++k) {
            const long v = PyLong_AsLong(PySequence_Fast_GET_ITEM(t, k));
            ok = !(v == -1 && PyErr_Occurred()) && v >= 0 && (size_t)v < nverts;
            T[(size_t)i][k] = (int)v;
        }
        Py_XDECREF(t);
    }
    Py_DECREF(f);
    if (!ok && !PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, "bad triangle");
    return ok;
}

bool read_pins(PyObject* o, double default_r, std::vector<Pin>& pins) {
    if (o == Py_None) return true;
    PyObject* f = PySequence_Fast(o, "pins must be a sequence");
    if (!f) return false;
    bool ok = true;
    for (Py_ssize_t i = 0; ok && i < PySequence_Fast_GET_SIZE(f); ++i) {
        PyObject* p = PySequence_Fast(PySequence_Fast_GET_ITEM(f, i), "a pin must be a sequence");
        ok = p && PySequence_Fast_GET_SIZE(p) >= 2;
        Pin pin{0.0, 0.0, default_r};
        ok = ok && item_double(p, 0, &pin.x) && item_double(p, 1, &pin.y)
            && (PySequence_Fast_GET_SIZE(p) < 3 || item_double(p, 2, &pin.r));
        if (ok) pins.push_back(pin);
        Py_XDECREF(p);
    }
    Py_DECREF(f);
    return ok;
}

bool read_bounds(PyObject* o, double out[4], bool* has) {
    *has = o != Py_None;
    if (!*has) return true;
    PyObject* f = PySequence_Fast(o, "bounds must be a 4-sequence");
    if (!f) return false;
    bool ok = PySequence_Fast_GET_SIZE(f) == 4;
    for (int k = 0; ok && k < 4; ++k) ok = item_double(f, k, &out[k]);
    Py_DECREF(f);
    if (!ok && !PyErr_Occurred()) PyErr_SetString(PyExc_ValueError, "bounds need 4 values");
    return ok;
}

bool read_samples(PyObject* o, std::vector<Sample>& S) {
    if (o == Py_None) return true;
    PyObject* f = PySequence_Fast(o, "samples must be a sequence");
    if (!f) return false;
    bool ok = true;
    for (Py_ssize_t i = 0; ok && i < PySequence_Fast_GET_SIZE(f); ++i) {
        PyObject* p = PySequence_Fast(PySequence_Fast_GET_ITEM(f, i), "a sample must be a sequence");
        ok = p && PySequence_Fast_GET_SIZE(p) >= 2;
        Sample s{0.0, 0.0, NAN, NAN, NAN};
        const Py_ssize_t n = ok ? PySequence_Fast_GET_SIZE(p) : 0;
        ok = ok && item_double(p, 0, &s.x) && item_double(p, 1, &s.y);
        if (ok && n > 2 && PySequence_Fast_GET_ITEM(p, 2) != Py_None) ok = item_double(p, 2, &s.z);
        if (ok && n > 4) ok = item_double(p, 3, &s.ux) && item_double(p, 4, &s.uy);
        if (ok) S.push_back(s);
        Py_XDECREF(p);
    }
    Py_DECREF(f);
    return ok;
}

PyObject* py_number(double v, bool np) {
    PyObject* f = PyFloat_FromDouble(v);
    if (!f || !np) return f;
    if (!float64_type()) { Py_DECREF(f); return nullptr; }
    PyObject* out = PyObject_CallOneArg(g_float64, f);
    Py_DECREF(f);
    return out;
}

PyObject* verts_to_python(const std::vector<Vert>& V) {
    PyObject* pv = PyList_New((Py_ssize_t)V.size());
    for (size_t i = 0; pv && i < V.size(); ++i) {
        PyObject* z = py_number(V[i].z, V[i].znp);
        PyObject* row = z ? Py_BuildValue("[ddN]", V[i].x, V[i].y, z) : nullptr;
        if (!row) { Py_DECREF(pv); return nullptr; }
        PyList_SET_ITEM(pv, (Py_ssize_t)i, row);
    }
    return pv;
}

PyObject* tris_to_python(const std::vector<Tri>& T) {
    PyObject* pt = PyList_New((Py_ssize_t)T.size());
    for (size_t i = 0; pt && i < T.size(); ++i) {
        PyObject* row = Py_BuildValue("(iii)", T[i][0], T[i][1], T[i][2]);
        if (!row) { Py_DECREF(pt); return nullptr; }
        PyList_SET_ITEM(pt, (Py_ssize_t)i, row);
    }
    return pt;
}

}  // namespace navgrow
