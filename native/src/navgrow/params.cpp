// The shared tunables (params.h) and their Python setter.

#include "params.h"

namespace navgrow {

namespace {

NavParams g_params;
bool g_ready = false;

bool read_one(PyObject* dict, const char* name, double* slot) {
    PyObject* v = PyDict_GetItemString(dict, name);
    if (!v) {
        PyErr_Format(PyExc_KeyError, "navmesh param %s missing", name);
        return false;
    }
    *slot = PyFloat_AsDouble(v);
    return !(*slot == -1.0 && PyErr_Occurred());
}

}  // namespace

const NavParams& P() { return g_params; }

bool params_ready() { return g_ready; }

bool set_params(PyObject* dict) {
    if (!PyDict_Check(dict)) {
        PyErr_SetString(PyExc_TypeError, "set_params wants a dict");
        return false;
    }
    NavParams p;
#define NAV_PARAM_READ(name) \
    if (!read_one(dict, #name, &p.name)) return false;
    NAV_PARAMS(NAV_PARAM_READ)
#undef NAV_PARAM_READ
    g_params = p;
    g_ready = true;
    return true;
}

}  // namespace navgrow

// set_params(dict) -> None: the tunables every native stage reads.
PyObject* py_set_params(PyObject*, PyObject* dict) {
    if (!navgrow::set_params(dict)) return nullptr;
    Py_RETURN_NONE;
}
