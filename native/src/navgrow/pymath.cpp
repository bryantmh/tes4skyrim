// crt_pow: the C library pow the interpreter calls for `x ** y` (pymath.h).

#include "pymath.h"

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace navgrow {

namespace {

typedef double (*PowFn)(double, double);

// ucrtbase.dll's pow -- the DLL C runtime python3xx.dll links -- or the
// linked pow where the interpreter shares this module's C library (Linux).
PowFn resolve_pow() {
#ifdef _WIN32
    HMODULE crt = GetModuleHandleW(L"ucrtbase.dll");
    if (crt) {
        PowFn fn = (PowFn)GetProcAddress(crt, "pow");
        if (fn) return fn;
    }
#endif
    return [](double x, double y) { return std::pow(x, y); };
}

}  // namespace

double crt_pow(double x, double y) {
    static const PowFn fn = resolve_pow();
    return fn(x, y);
}

}  // namespace navgrow
