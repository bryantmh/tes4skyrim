// crt_pow / crt_cos / crt_sin: the C library functions the interpreter calls
// for `x ** y`, math.cos and math.sin (pymath.h).

#include "pymath.h"

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
typedef void* FARPROC;
#endif

namespace navgrow {

namespace {

typedef double (*PowFn)(double, double);
typedef double (*UnaryFn)(double);

// ucrtbase.dll's `name` -- the DLL C runtime python3xx.dll links -- or null
// where the interpreter shares this module's C library (Linux).
FARPROC ucrt(const char* name) {
#ifdef _WIN32
    HMODULE crt = GetModuleHandleW(L"ucrtbase.dll");
    return crt ? GetProcAddress(crt, name) : nullptr;
#else
    return nullptr;
#endif
}

PowFn resolve_pow() {
    PowFn fn = (PowFn)ucrt("pow");
    return fn ? fn : [](double x, double y) { return std::pow(x, y); };
}

UnaryFn resolve_cos() {
    UnaryFn fn = (UnaryFn)ucrt("cos");
    return fn ? fn : [](double x) { return std::cos(x); };
}

UnaryFn resolve_sin() {
    UnaryFn fn = (UnaryFn)ucrt("sin");
    return fn ? fn : [](double x) { return std::sin(x); };
}

}  // namespace

double crt_pow(double x, double y) {
    static const PowFn fn = resolve_pow();
    return fn(x, y);
}

double crt_cos(double x) {
    static const UnaryFn fn = resolve_cos();
    return fn(x);
}

double crt_sin(double x) {
    static const UnaryFn fn = resolve_sin();
    return fn(x);
}

}  // namespace navgrow
