// Read-only float64 / int32 views of Python buffers (numpy arrays), released
// on scope exit.  Used by the translation units that do not link numpy's C API.
#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <cstring>

namespace navgrow {

struct BufView {
    Py_buffer view{};
    bool ok = false;
    // `code` is the struct format letter wanted ('d' float64, 'i' int32);
    // None reads as an empty view.
    BufView(PyObject* o, char code, Py_ssize_t itemsize) {
        if (o == Py_None) { ok = true; return; }
        if (PyObject_GetBuffer(o, &view, PyBUF_C_CONTIGUOUS | PyBUF_FORMAT) < 0)
            return;
        const char* f = view.format ? view.format : "B";
        while (*f == '<' || *f == '=' || *f == '@') ++f;
        const bool int32_alias = code == 'i' && f[0] == 'l';
        if (view.itemsize != itemsize || (f[0] != code && !int32_alias)
            || f[1] != '\0') {
            PyErr_Format(PyExc_ValueError, "expected a contiguous '%c' array",
                         code);
            PyBuffer_Release(&view);
            view.obj = nullptr;
            return;
        }
        ok = true;
    }
    ~BufView() { if (view.obj) PyBuffer_Release(&view); }
    BufView(const BufView&) = delete;
    BufView& operator=(const BufView&) = delete;
    template <class T> const T* data() const { return (const T*)view.buf; }
    size_t count() const {
        return view.obj ? (size_t)(view.len / view.itemsize) : 0;
    }
};

}  // namespace navgrow
