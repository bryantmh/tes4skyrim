// Shapely's geometry calls, natively: an owning GEOS geometry handle and one
// function per shapely operation the corridor build makes.
//
// Each function makes the SAME GEOS call shapely 2.1 makes on the SAME GEOS
// library (geos_api.h), with the same parameters, so results are identical to
// the Python reference.  A GEOS exception comes back as a null Geom (or -1 / NaN
// from a predicate or measure); a caller whose Python twin does not guard the
// call uses need() to raise GeosError instead.
#pragma once

#include <array>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "geos_api.h"

namespace navgrow {

using P2 = std::array<double, 2>;
using Ring = std::vector<P2>;

// A GEOS call the reference does not guard failed.
struct GeosError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// GEOS type ids (GEOSGeomTypeId).
enum GeomType { kPoint = 0, kLineString = 1, kLinearRing = 2, kPolygon = 3,
                kMultiPoint = 4, kMultiLineString = 5, kMultiPolygon = 6,
                kCollection = 7 };

class Geom {
public:
    Geom() = default;
    explicit Geom(GEOSGeometry* g) : g_(g) {}
    ~Geom() { reset(); }
    Geom(Geom&& o) noexcept : g_(o.g_) { o.g_ = nullptr; }
    Geom& operator=(Geom&& o) noexcept {
        if (this != &o) { reset(); g_ = o.g_; o.g_ = nullptr; }
        return *this;
    }
    Geom(const Geom&) = delete;
    Geom& operator=(const Geom&) = delete;

    GEOSGeometry* get() const { return g_; }
    explicit operator bool() const { return g_ != nullptr; }
    GEOSGeometry* release() { GEOSGeometry* g = g_; g_ = nullptr; return g; }
    void reset();

private:
    GEOSGeometry* g_ = nullptr;
};

// `g`, or GeosError naming `what` when it is null.
Geom need(Geom g, const char* what);

// --- constructors ---------------------------------------------------------
// shapely.Polygon(shell, holes): each ring closed as shapely closes it (a
// 3-coordinate ring always, a longer one when its ends differ); null where
// shapely raises (a ring under 4 coordinates).
Geom polygon(const Ring& shell, const std::vector<Ring>& holes = {});
// shapely.box(minx, miny, maxx, maxy) (counter-clockwise from (maxx, miny)).
Geom box(double minx, double miny, double maxx, double maxy);
// shapely.Point(x, y).
Geom point(double x, double y);
// shapely.LineString(coords).
Geom line_string(const Ring& coords);
// A copy of `g`.
Geom clone(const GEOSGeometry* g);

// --- operations -----------------------------------------------------------
// g.buffer(d, quad_segs=16, cap_style, join_style, mitre_limit=5.0).
Geom buffer(const GEOSGeometry* g, double d, int cap = 1, int join = 1);
// shapely.ops.unary_union(geoms): one GEOMETRYCOLLECTION of copies, unioned.
Geom union_all(const std::vector<const GEOSGeometry*>& geoms);
Geom difference(const GEOSGeometry* a, const GEOSGeometry* b);
Geom intersection(const GEOSGeometry* a, const GEOSGeometry* b);
Geom make_valid(const GEOSGeometry* g);
Geom boundary(const GEOSGeometry* g);
Geom centroid(const GEOSGeometry* g);
Geom point_on_surface(const GEOSGeometry* g);
Geom constrained_delaunay(const GEOSGeometry* g);
Geom interpolate_normalized(const GEOSGeometry* line, double frac);

// --- predicates and measures (-1 / NaN on a GEOS exception) ---------------
int is_valid(const GEOSGeometry* g);
int is_empty(const GEOSGeometry* g);
int intersects(const GEOSGeometry* a, const GEOSGeometry* b);
int contains(const GEOSGeometry* a, const GEOSGeometry* b);
double area(const GEOSGeometry* g);
double length(const GEOSGeometry* g);
double distance(const GEOSGeometry* a, const GEOSGeometry* b);
// (minx, miny, maxx, maxy); false for an empty geometry or a GEOS exception.
bool bounds(const GEOSGeometry* g, double out[4]);
bool point_xy(const GEOSGeometry* g, double* x, double* y);

// --- structure ------------------------------------------------------------
int type_id(const GEOSGeometry* g);
// True for a multi-geometry or collection (shapely's `.geoms`).
bool has_parts(const GEOSGeometry* g);
int num_parts(const GEOSGeometry* g);
const GEOSGeometry* part(const GEOSGeometry* g, int i);
// `.geoms` when it has parts, else [g].
std::vector<const GEOSGeometry*> parts_or_self(const GEOSGeometry* g);
// A polygon's exterior / interior ring coordinates, closing point included
// (shapely's `.coords`).
Ring exterior_coords(const GEOSGeometry* poly);
std::vector<Ring> interior_coords(const GEOSGeometry* poly);
// A line or ring's coordinates.
Ring line_coords(const GEOSGeometry* line);

// A prepared geometry for repeated tests (shapely.contains_xy, intersects):
// 1 / 0, or -1 on a GEOS exception.
class Prepared {
public:
    explicit Prepared(const GEOSGeometry* g);
    ~Prepared();
    Prepared(const Prepared&) = delete;
    Prepared& operator=(const Prepared&) = delete;
    explicit operator bool() const { return p_ != nullptr; }
    int contains_xy(double x, double y) const;
    int intersects(const GEOSGeometry* g) const;

private:
    const GEOSPreparedGeometry* p_ = nullptr;
};

}  // namespace navgrow
