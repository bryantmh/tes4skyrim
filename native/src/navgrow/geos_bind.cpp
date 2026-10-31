// Runtime binding of GEOS (see geos_api.h): find shapely's loaded geos_c among
// the process's modules and resolve the functions the native kernels call.

#include "geos_api.h"

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h>
#pragma comment(lib, "psapi.lib")
#else
#include <dlfcn.h>
#include <link.h>
#endif

#include <algorithm>
#include <cstring>

namespace navgrow {

namespace {

GeosApi g_api;
bool g_bound = false;

// One GEOS context per thread: GEOS contexts are not thread-safe, geometries
// are, so build threads share nothing but the bound function pointers.  The
// context is finished when its thread exits (a pool thread at shutdown).
struct ThreadGeos {
    GEOSContextHandle_t ctx = nullptr;
    std::string last_error;
    ~ThreadGeos() {
        if (ctx && g_bound) g_api.finish(ctx);
    }
};

thread_local ThreadGeos t_geos;

void on_geos_error(const char* message, void* userdata) {
    static_cast<ThreadGeos*>(userdata)->last_error = message ? message : "";
}

#ifdef _WIN32
// The loaded module whose file name starts with "geos_c", or nullptr.
void* find_geos_module() {
    HMODULE mods[1024];
    DWORD need = 0;
    if (!EnumProcessModules(GetCurrentProcess(), mods, sizeof(mods), &need))
        return nullptr;
    const size_t n = std::min<size_t>(need / sizeof(HMODULE), 1024);
    for (size_t i = 0; i < n; ++i) {
        char name[MAX_PATH];
        if (!GetModuleBaseNameA(GetCurrentProcess(), mods[i], name, MAX_PATH))
            continue;
        if (_strnicmp(name, "geos_c", 6) == 0) return (void*)mods[i];
    }
    return nullptr;
}

void* symbol(void* mod, const char* name) {
    return (void*)GetProcAddress((HMODULE)mod, name);
}
#else
int find_cb(struct dl_phdr_info* info, size_t, void* data) {
    const char* path = info->dlpi_name;
    const char* base = path ? std::strrchr(path, '/') : nullptr;
    base = base ? base + 1 : path;
    if (base && std::strncmp(base, "libgeos_c", 9) == 0) {
        *(std::string*)data = path;
        return 1;
    }
    return 0;
}

void* find_geos_module() {
    std::string path;
    dl_iterate_phdr(find_cb, &path);
    if (path.empty()) return nullptr;
    return dlopen(path.c_str(), RTLD_NOW | RTLD_NOLOAD);
}

void* symbol(void* mod, const char* name) { return dlsym(mod, name); }
#endif

template <class F>
bool resolve(void* mod, const char* name, F& slot) {
    slot = reinterpret_cast<F>(symbol(mod, name));
    if (!slot)
        PyErr_Format(PyExc_ImportError, "geos_c has no %s", name);
    return slot != nullptr;
}

bool resolve_all(void* mod, GeosApi& a) {
    return resolve(mod, "GEOS_init_r", a.init_r) &&
        resolve(mod, "GEOSContext_setErrorMessageHandler_r",
                a.set_error_handler) &&
        resolve(mod, "GEOSGeom_destroy_r", a.destroy) &&
        resolve(mod, "GEOSIntersection_r", a.intersection) &&
        resolve(mod, "GEOSisEmpty_r", a.is_empty) &&
        resolve(mod, "GEOSArea_r", a.area) &&
        resolve(mod, "GEOSGetCentroid_r", a.centroid) &&
        resolve(mod, "GEOSPointOnSurface_r", a.point_on_surface) &&
        resolve(mod, "GEOSGeomGetX_r", a.get_x) &&
        resolve(mod, "GEOSGeomGetY_r", a.get_y) &&
        resolve(mod, "GEOSGeom_getXMin_r", a.xmin) &&
        resolve(mod, "GEOSGeom_getYMin_r", a.ymin) &&
        resolve(mod, "GEOSGeom_getXMax_r", a.xmax) &&
        resolve(mod, "GEOSGeom_getYMax_r", a.ymax) &&
        resolve(mod, "GEOSGeom_createPointFromXY_r", a.point_xy) &&
        resolve(mod, "GEOSIntersects_r", a.intersects) &&
        resolve(mod, "GEOSCoordSeq_create_r", a.seq_create) &&
        resolve(mod, "GEOSCoordSeq_setXY_r", a.seq_set_xy) &&
        resolve(mod, "GEOSGeom_createLinearRing_r", a.linear_ring) &&
        resolve(mod, "GEOSGeom_createPolygon_r", a.polygon) &&
        resolve(mod, "GEOSisValid_r", a.is_valid) &&
        resolve(mod, "GEOSPrepare_r", a.prepare) &&
        resolve(mod, "GEOSPreparedGeom_destroy_r", a.prepared_destroy) &&
        resolve(mod, "GEOSPreparedIntersects_r", a.prepared_intersects) &&
        resolve(mod, "GEOSGeomTypeId_r", a.type_id) &&
        resolve(mod, "GEOSGetExteriorRing_r", a.exterior_ring) &&
        resolve(mod, "GEOSGetNumInteriorRings_r", a.num_interior_rings) &&
        resolve(mod, "GEOSGeom_getCoordSeq_r", a.coord_seq) &&
        resolve(mod, "GEOSCoordSeq_getSize_r", a.seq_size) &&
        resolve(mod, "GEOSCoordSeq_getXY_r", a.seq_get_xy) &&
        resolve(mod, "GEOS_finish_r", a.finish) &&
        resolve(mod, "GEOSUnaryUnion_r", a.unary_union) &&
        resolve(mod, "GEOSDifference_r", a.difference) &&
        resolve(mod, "GEOSMakeValid_r", a.make_valid) &&
        resolve(mod, "GEOSBufferWithStyle_r", a.buffer_style) &&
        resolve(mod, "GEOSGeom_createCollection_r", a.collection) &&
        resolve(mod, "GEOSGeom_clone_r", a.clone) &&
        resolve(mod, "GEOSGetNumGeometries_r", a.num_geometries) &&
        resolve(mod, "GEOSGetGeometryN_r", a.geometry_n) &&
        resolve(mod, "GEOSGetInteriorRingN_r", a.interior_ring_n) &&
        resolve(mod, "GEOSBoundary_r", a.boundary) &&
        resolve(mod, "GEOSDistance_r", a.distance) &&
        resolve(mod, "GEOSContains_r", a.contains) &&
        resolve(mod, "GEOSConstrainedDelaunayTriangulation_r", a.cdt) &&
        resolve(mod, "GEOSLength_r", a.length) &&
        resolve(mod, "GEOSInterpolateNormalized_r", a.interpolate_normalized) &&
        resolve(mod, "GEOSGeom_createLineString_r", a.line_string) &&
        resolve(mod, "GEOSPreparedContainsXY_r", a.prepared_contains_xy) &&
        resolve(mod, "GEOSGeom_createEmptyCollection_r", a.empty_collection) &&
        resolve(mod, "GEOSisSimple_r", a.is_simple);
}

}  // namespace

GEOSContextHandle_t GeosApi::ctx() const {
    if (!t_geos.ctx) {
        t_geos.ctx = init_r();
        if (t_geos.ctx) set_error_handler(t_geos.ctx, on_geos_error, &t_geos);
    }
    return t_geos.ctx;
}

const std::string& GeosApi::last_error() const { return t_geos.last_error; }

GeosApi* geos() {
    if (!g_bound) {
        PyErr_SetString(PyExc_RuntimeError,
                        "GEOS not bound: call bind_geos() first");
        return nullptr;
    }
    return &g_api;
}

GEOSGeometry* make_polygon(GeosApi* G, const double* xy, unsigned n) {
    GEOSCoordSequence* seq = G->seq_create(G->ctx(), n + 1, 2);
    if (!seq) return nullptr;
    for (unsigned k = 0; k <= n; ++k) {
        const unsigned s = k % n;
        G->seq_set_xy(G->ctx(), seq, k, xy[2 * s], xy[2 * s + 1]);
    }
    GEOSGeometry* ring = G->linear_ring(G->ctx(), seq);
    if (!ring) return nullptr;
    return G->polygon(G->ctx(), ring, nullptr, 0);
}

bool polygon_shell(GeosApi* G, const GEOSGeometry* g, std::vector<double>& xy) {
    xy.clear();
    if (G->type_id(G->ctx(), g) != 3 || G->num_interior_rings(G->ctx(), g) != 0)
        return false;
    const GEOSGeometry* shell = G->exterior_ring(G->ctx(), g);
    const GEOSCoordSequence* seq = shell ? G->coord_seq(G->ctx(), shell) : nullptr;
    unsigned n = 0;
    if (!seq || !G->seq_size(G->ctx(), seq, &n) || n < 4) return false;
    xy.resize(2 * (size_t)(n - 1));
    for (unsigned k = 0; k + 1 < n; ++k)
        if (!G->seq_get_xy(G->ctx(), seq, k, &xy[2 * k], &xy[2 * k + 1]))
            return false;
    return true;
}

}  // namespace navgrow

using namespace navgrow;

// bind_geos(): resolve GEOS from shapely's loaded geos_c and its C API
// capsule.  Importing the capsule imports shapely.lib, which is what loads
// geos_c, so the module search after it finds shapely's copy.  Idempotent.
PyObject* py_bind_geos(PyObject*, PyObject*) {
    if (g_bound) Py_RETURN_NONE;
    void** capi = (void**)PyCapsule_Import("shapely.lib._C_API", 0);
    if (!capi) return nullptr;
    void* mod = find_geos_module();
    if (!mod) {
        PyErr_SetString(PyExc_ImportError,
                        "shapely's geos_c is not loaded in this process");
        return nullptr;
    }
    GeosApi a;
    if (!resolve_all(mod, a)) return nullptr;
    a.shapely_geom = reinterpret_cast<char (*)(PyObject*, GEOSGeometry**)>(
        capi[1]);
    a.shapely_wrap = reinterpret_cast<PyObject* (*)(GEOSGeometry*,
                                                    GEOSContextHandle_t)>(
        capi[0]);
    g_api = a;
    if (!g_api.ctx()) {
        PyErr_SetString(PyExc_RuntimeError, "GEOS_init_r failed");
        return nullptr;
    }
    g_bound = true;
    Py_RETURN_NONE;
}
