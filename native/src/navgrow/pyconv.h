// Python <-> native conversions shared by the stage bindings.
#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <vector>

#include "clean.h"
#include "mesh.h"

namespace navgrow {

// A Python exception is pending; unwind to the entry point.
struct PyErrorSet {};

// Vertices from a sequence of 3-sequences.  With `keep_types`, a Z that is not
// an exact float is marked znp; otherwise every Z counts as a plain float.
bool read_verts(PyObject* o, std::vector<Vert>& V, bool keep_types);

// Triangles from a sequence of 3-sequences of indices below `nverts`.
bool read_tris(PyObject* o, size_t nverts, std::vector<Tri>& T);

// (x, y[, r]) pins, `default_r` where the radius is absent; None is no pins.
bool read_pins(PyObject* o, double default_r, std::vector<Pin>& pins);

// (minx, miny, maxx, maxy) into `out`, or *has = false for None.
bool read_bounds(PyObject* o, double out[4], bool* has);

// Walked samples (x, y[, z[, ux, uy]]) with NaN where the reference holds None.
bool read_samples(PyObject* o, std::vector<Sample>& S);

// numpy.float64 when `np`, else a plain float.
PyObject* py_number(double v, bool np);

// [[x, y, z], ...] with Z a numpy.float64 where znp is set.
PyObject* verts_to_python(const std::vector<Vert>& V);

// [(a, b, c), ...].
PyObject* tris_to_python(const std::vector<Tri>& T);

}  // namespace navgrow
