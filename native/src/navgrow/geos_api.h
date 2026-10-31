// GEOS, bound at runtime to the copy shapely itself loaded.
//
// The Python reference does its polygon work through shapely, which wraps
// GEOS.  Calling that SAME library (found among the process's loaded modules,
// never a second copy) is what makes the native results bit-identical to the
// Python ones: a different GEOS build, or a hand-written overlay, would round
// differently.  Geometry pointers come from shapely's C API capsule.
#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <string>
#include <vector>

namespace navgrow {

typedef void* GEOSContextHandle_t;
struct GEOSGeom_t;
typedef struct GEOSGeom_t GEOSGeometry;
struct GEOSCoordSeq_t;
typedef struct GEOSCoordSeq_t GEOSCoordSequence;
struct GEOSPrepGeom_t;
typedef struct GEOSPrepGeom_t GEOSPreparedGeometry;
typedef void (*GEOSMessageHandler_r)(const char* message, void* userdata);

struct GeosApi {
    // This thread's GEOS context (each build thread gets its own on first use)
    // and the last error GEOS reported on it.
    GEOSContextHandle_t ctx() const;
    const std::string& last_error() const;

    GEOSContextHandle_t (*init_r)();
    GEOSMessageHandler_r (*set_error_handler)(GEOSContextHandle_t,
                                              GEOSMessageHandler_r, void*);
    void (*destroy)(GEOSContextHandle_t, GEOSGeometry*);
    GEOSGeometry* (*intersection)(GEOSContextHandle_t, const GEOSGeometry*,
                                  const GEOSGeometry*);
    char (*is_empty)(GEOSContextHandle_t, const GEOSGeometry*);
    int (*area)(GEOSContextHandle_t, const GEOSGeometry*, double*);
    GEOSGeometry* (*centroid)(GEOSContextHandle_t, const GEOSGeometry*);
    GEOSGeometry* (*point_on_surface)(GEOSContextHandle_t, const GEOSGeometry*);
    int (*get_x)(GEOSContextHandle_t, const GEOSGeometry*, double*);
    int (*get_y)(GEOSContextHandle_t, const GEOSGeometry*, double*);
    int (*xmin)(GEOSContextHandle_t, const GEOSGeometry*, double*);
    int (*ymin)(GEOSContextHandle_t, const GEOSGeometry*, double*);
    int (*xmax)(GEOSContextHandle_t, const GEOSGeometry*, double*);
    int (*ymax)(GEOSContextHandle_t, const GEOSGeometry*, double*);
    GEOSGeometry* (*point_xy)(GEOSContextHandle_t, double, double);
    char (*intersects)(GEOSContextHandle_t, const GEOSGeometry*,
                       const GEOSGeometry*);
    GEOSCoordSequence* (*seq_create)(GEOSContextHandle_t, unsigned, unsigned);
    int (*seq_set_xy)(GEOSContextHandle_t, GEOSCoordSequence*, unsigned, double,
                      double);
    GEOSGeometry* (*linear_ring)(GEOSContextHandle_t, GEOSCoordSequence*);
    GEOSGeometry* (*polygon)(GEOSContextHandle_t, GEOSGeometry*, GEOSGeometry**,
                             unsigned);
    char (*is_valid)(GEOSContextHandle_t, const GEOSGeometry*);
    const GEOSPreparedGeometry* (*prepare)(GEOSContextHandle_t,
                                           const GEOSGeometry*);
    void (*prepared_destroy)(GEOSContextHandle_t, const GEOSPreparedGeometry*);
    char (*prepared_intersects)(GEOSContextHandle_t, const GEOSPreparedGeometry*,
                                const GEOSGeometry*);
    int (*type_id)(GEOSContextHandle_t, const GEOSGeometry*);
    const GEOSGeometry* (*exterior_ring)(GEOSContextHandle_t,
                                         const GEOSGeometry*);
    int (*num_interior_rings)(GEOSContextHandle_t, const GEOSGeometry*);
    const GEOSCoordSequence* (*coord_seq)(GEOSContextHandle_t,
                                          const GEOSGeometry*);
    int (*seq_size)(GEOSContextHandle_t, const GEOSCoordSequence*, unsigned*);
    int (*seq_get_xy)(GEOSContextHandle_t, const GEOSCoordSequence*, unsigned,
                      double*, double*);
    void (*finish)(GEOSContextHandle_t);
    GEOSGeometry* (*unary_union)(GEOSContextHandle_t, const GEOSGeometry*);
    GEOSGeometry* (*difference)(GEOSContextHandle_t, const GEOSGeometry*,
                                const GEOSGeometry*);
    GEOSGeometry* (*make_valid)(GEOSContextHandle_t, const GEOSGeometry*);
    GEOSGeometry* (*buffer_style)(GEOSContextHandle_t, const GEOSGeometry*,
                                  double, int, int, int, double);
    GEOSGeometry* (*collection)(GEOSContextHandle_t, int, GEOSGeometry**,
                                unsigned);
    GEOSGeometry* (*clone)(GEOSContextHandle_t, const GEOSGeometry*);
    int (*num_geometries)(GEOSContextHandle_t, const GEOSGeometry*);
    const GEOSGeometry* (*geometry_n)(GEOSContextHandle_t, const GEOSGeometry*,
                                      int);
    const GEOSGeometry* (*interior_ring_n)(GEOSContextHandle_t,
                                           const GEOSGeometry*, int);
    GEOSGeometry* (*boundary)(GEOSContextHandle_t, const GEOSGeometry*);
    int (*distance)(GEOSContextHandle_t, const GEOSGeometry*,
                    const GEOSGeometry*, double*);
    char (*contains)(GEOSContextHandle_t, const GEOSGeometry*,
                     const GEOSGeometry*);
    GEOSGeometry* (*cdt)(GEOSContextHandle_t, const GEOSGeometry*);
    int (*length)(GEOSContextHandle_t, const GEOSGeometry*, double*);
    GEOSGeometry* (*interpolate_normalized)(GEOSContextHandle_t,
                                            const GEOSGeometry*, double);
    GEOSGeometry* (*line_string)(GEOSContextHandle_t, GEOSCoordSequence*);
    char (*prepared_contains_xy)(GEOSContextHandle_t,
                                 const GEOSPreparedGeometry*, double, double);
    GEOSGeometry* (*empty_collection)(GEOSContextHandle_t, int);
    char (*is_simple)(GEOSContextHandle_t, const GEOSGeometry*);
    // shapely.lib C API: PyGEOS_GetGEOSGeometry, and PyGEOS_CreateGeometry,
    // which takes ownership of the geometry it wraps.
    char (*shapely_geom)(PyObject*, GEOSGeometry**);
    PyObject* (*shapely_wrap)(GEOSGeometry*, GEOSContextHandle_t);
};

// The bound API, or nullptr (with a Python error set) when bind_geos has not
// run or failed.
GeosApi* geos();

// A new 2D polygon over the closed ring xy[0..n), as shapely.Polygon(coords)
// builds it (the first point repeated to close), or nullptr when GEOS rejects
// it.  The caller owns the result.
GEOSGeometry* make_polygon(GeosApi* G, const double* xy, unsigned n);

// The shell of a polygon without holes as open x, y pairs (GEOS's closing
// point dropped); false for any other geometry.
bool polygon_shell(GeosApi* G, const GEOSGeometry* g, std::vector<double>& xy);

// Owns one GEOS result and destroys it on scope exit.
struct GeosOwned {
    GeosApi* api;
    GEOSGeometry* g;
    GeosOwned(GeosApi* a, GEOSGeometry* p) : api(a), g(p) {}
    ~GeosOwned() { if (g) api->destroy(api->ctx(), g); }
    GeosOwned(const GeosOwned&) = delete;
    GeosOwned& operator=(const GeosOwned&) = delete;
};

}  // namespace navgrow
