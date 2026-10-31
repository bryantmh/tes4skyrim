// Python's float arithmetic where it differs from the plain C expression.
//
// The native kernels must reproduce the Python reference bit for bit, and a
// handful of Python float operations do NOT round like their obvious C++
// spelling: `a // b`, `x ** 2`, math.hypot / math.dist and the builtin sum().
// Each is mirrored here from CPython (3.12+, the floor this project runs on)
// and fuzz-checked against the interpreter by tests/test_navmesh_native.py.
#pragma once

#include <algorithm>
#include <cfloat>
#include <cmath>

namespace navgrow {

// `a // b` (CPython _float_div_mod).  floor(a / b) differs when the quotient
// rounds across an integer: 1 // 0.1 is 9.0, floor(1 / 0.1) is 10.0.
inline double py_floordiv(double a, double b) {
    double mod = std::fmod(a, b);
    double div = (a - mod) / b;
    if (mod != 0.0 && ((b < 0.0) != (mod < 0.0))) div -= 1.0;
    if (div == 0.0) return std::copysign(0.0, a / b);
    double fl = std::floor(div);
    if (div - fl > 0.5) fl += 1.0;
    return fl;
}

// The C library pow CPython itself calls (pymath.cpp).  Not std::pow: on
// Windows this module links the STATIC C runtime, whose pow rounds differently
// from the DLL runtime's that python3xx.dll uses.
double crt_pow(double x, double y);

// `x ** 2`: CPython's pow, which differs from x*x in the last bit for ~0.05%
// of doubles.
inline double py_square(double x) { return crt_pow(x, 2.0); }

// --------------------------------------------------------------------------
// math.hypot / math.dist: CPython's vector_norm, a lossless-scaled sum of
// squares with one Newton correction -- not the C library hypot.
// --------------------------------------------------------------------------
struct DoubleLength { double hi, lo; };

inline DoubleLength dl_mul(double x, double y) {
    const double z = x * y;
    return {z, std::fma(x, y, -z)};
}

inline DoubleLength dl_fast_sum(double a, double b) {
    const double x = a + b;
    return {x, (a - x) + b};
}

// vector_norm over `n` non-negative finite-or-not magnitudes whose max is `mx`.
inline double py_vector_norm(int n, double* vec, double mx, bool found_nan) {
    if (std::isinf(mx)) return mx;
    if (found_nan) return NAN;
    if (mx == 0.0 || n <= 1) return mx;
    int max_e;
    std::frexp(mx, &max_e);
    if (max_e < -1023) {
        for (int i = 0; i < n; ++i) vec[i] /= DBL_MIN;
        return DBL_MIN * py_vector_norm(n, vec, mx / DBL_MIN, found_nan);
    }
    const double scale = std::ldexp(1.0, -max_e);
    double csum = 1.0, frac1 = 0.0, frac2 = 0.0;
    for (int i = 0; i < n; ++i) {
        const double x = vec[i] * scale;
        const DoubleLength pr = dl_mul(x, x);
        const DoubleLength sm = dl_fast_sum(csum, pr.hi);
        csum = sm.hi;
        frac1 += pr.lo;
        frac2 += sm.lo;
    }
    double h = std::sqrt(csum - 1.0 + (frac1 + frac2));
    const DoubleLength pr = dl_mul(-h, h);
    const DoubleLength sm = dl_fast_sum(csum, pr.hi);
    csum = sm.hi;
    frac1 += pr.lo;
    frac2 += sm.lo;
    const double x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h);
    return h / scale;
}

// math.hypot(x, y).
inline double py_hypot(double x, double y) {
    double v[2] = {std::fabs(x), std::fabs(y)};
    double mx = 0.0;
    bool nan = false;
    for (double a : v) {
        nan = nan || std::isnan(a);
        if (a > mx) mx = a;
    }
    return py_vector_norm(2, v, mx, nan);
}

// math.dist(p, q) for 2D points.
inline double py_dist(double px, double py, double qx, double qy) {
    return py_hypot(px - qx, py - qy);
}

// --------------------------------------------------------------------------
// Builtin sum() over floats, starting from int 0: the first item is taken as
// is, the rest are added with Neumaier compensation, and the compensation is
// applied once at the end (CPython builtin_sum_impl).
// --------------------------------------------------------------------------
struct PySum {
    double total = 0.0, comp = 0.0;
    bool any = false;
    void add(double x) {
        if (!any) { total = 0.0 + x; any = true; return; }
        const double t = total + x;
        if (std::fabs(total) >= std::fabs(x)) comp += (total - t) + x;
        else comp += (x - t) + total;
        total = t;
    }
    double value() const {
        if (comp != 0.0 && std::isfinite(comp)) return total + comp;
        return total;
    }
};

// Builtin sum() over a mix of exact floats and numpy.float64s: compensation
// runs only while every item so far is an exact float; the first other item
// flushes it and the rest add plainly (CPython leaves its float fast path).
struct PyMixedSum {
    double total = 0.0, comp = 0.0;
    bool any = false, generic = false;
    void add(double x, bool exact) {
        if (!any) { total = 0.0 + x; any = true; generic = !exact; return; }
        if (!generic && exact) {
            const double t = total + x;
            if (std::fabs(total) >= std::fabs(x)) comp += (total - t) + x;
            else comp += (x - t) + total;
            total = t;
            return;
        }
        if (!generic) {
            if (comp != 0.0 && std::isfinite(comp)) total += comp;
            generic = true;
        }
        total = total + x;
    }
    double value() const {
        if (!generic && comp != 0.0 && std::isfinite(comp)) return total + comp;
        return total;
    }
};

// clean_decimate._badness on plan coordinates: max(edge ratio / max_ratio,
// aspect / max_aspect), 1e9 for a degenerate triangle.
inline double shape_badness(double px, double py, double qx, double qy,
                            double rx, double ry, double max_ratio,
                            double max_aspect) {
    const double e0 = py_dist(px, py, qx, qy);
    const double e1 = py_dist(qx, qy, rx, ry);
    const double e2 = py_dist(rx, ry, px, py);
    const double lo = std::min(std::min(e0, e1), e2);
    const double hi = std::max(std::max(e0, e1), e2);
    const double area = std::fabs((qx - px) * (ry - py) - (qy - py) * (rx - px)) * 0.5;
    if (lo <= 1e-9 || area <= 1e-9) return 1e9;
    return std::max((hi / lo) / max_ratio, (hi * hi / (4.0 * area)) / max_aspect);
}

}  // namespace navgrow
