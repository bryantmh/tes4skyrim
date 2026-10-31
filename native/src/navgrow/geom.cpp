// Shapely's geometry calls on GEOS (geom.h).

#include "geom.h"

#include <cmath>
#include <limits>

namespace navgrow {

namespace {

GeosApi* api() { return geos(); }

// shapely's ring closure: a 3-coordinate ring always gains its first point,
// a longer one only when its ends differ.
bool closes(const Ring& coords) {
    return coords.size() == 3
        || (!coords.empty() && (coords.front()[0] != coords.back()[0]
                                || coords.front()[1] != coords.back()[1]));
}

GEOSCoordSequence* sequence(const Ring& coords, bool close) {
    GeosApi* G = api();
    const bool add = close && closes(coords);
    const unsigned n = (unsigned)coords.size() + (add ? 1 : 0);
    GEOSCoordSequence* seq = G->seq_create(G->ctx(), n, 2);
    if (!seq) return nullptr;
    for (unsigned k = 0; k < n; ++k) {
        const P2& p = coords[k < coords.size() ? k : 0];
        G->seq_set_xy(G->ctx(), seq, k, p[0], p[1]);
    }
    return seq;
}

// A closed linear ring, or nullptr where shapely raises (< 4 coordinates).
GEOSGeometry* linear_ring(const Ring& coords) {
    if (coords.size() + (closes(coords) ? 1 : 0) < 4) return nullptr;
    GEOSCoordSequence* seq = sequence(coords, true);
    return seq ? api()->linear_ring(api()->ctx(), seq) : nullptr;
}

Ring read_seq(const GEOSCoordSequence* seq) {
    GeosApi* G = api();
    Ring out;
    unsigned n = 0;
    if (!seq || !G->seq_size(G->ctx(), seq, &n)) return out;
    out.resize(n);
    for (unsigned k = 0; k < n; ++k) G->seq_get_xy(G->ctx(), seq, k, &out[k][0], &out[k][1]);
    return out;
}

}  // namespace

void Geom::reset() {
    if (g_) api()->destroy(api()->ctx(), g_);
    g_ = nullptr;
}

Geom need(Geom g, const char* what) {
    if (!g) throw GeosError(std::string(what) + ": " + api()->last_error());
    return g;
}

Geom polygon(const Ring& shell, const std::vector<Ring>& holes) {
    GeosApi* G = api();
    GEOSGeometry* s = linear_ring(shell);
    if (!s) return Geom();
    std::vector<GEOSGeometry*> hs;
    for (const Ring& h : holes) {
        GEOSGeometry* r = linear_ring(h);
        if (!r) {
            G->destroy(G->ctx(), s);
            for (GEOSGeometry* x : hs) G->destroy(G->ctx(), x);
            return Geom();
        }
        hs.push_back(r);
    }
    return Geom(G->polygon(G->ctx(), s, hs.empty() ? nullptr : hs.data(), (unsigned)hs.size()));
}

Geom box(double minx, double miny, double maxx, double maxy) {
    return polygon({{maxx, miny}, {maxx, maxy}, {minx, maxy}, {minx, miny}, {maxx, miny}});
}

Geom point(double x, double y) { return Geom(api()->point_xy(api()->ctx(), x, y)); }

Geom line_string(const Ring& coords) {
    GEOSCoordSequence* seq = sequence(coords, false);
    return seq ? Geom(api()->line_string(api()->ctx(), seq)) : Geom();
}

Geom clone(const GEOSGeometry* g) { return Geom(api()->clone(api()->ctx(), g)); }

Geom buffer(const GEOSGeometry* g, double d, int cap, int join) {
    return Geom(api()->buffer_style(api()->ctx(), g, d, 16, cap, join, 5.0));
}

Geom union_all(const std::vector<const GEOSGeometry*>& geoms) {
    GeosApi* G = api();
    std::vector<GEOSGeometry*> copies;
    for (const GEOSGeometry* g : geoms) {
        GEOSGeometry* c = G->clone(G->ctx(), g);
        if (!c) {
            for (GEOSGeometry* x : copies) G->destroy(G->ctx(), x);
            return Geom();
        }
        copies.push_back(c);
    }
    Geom coll(copies.empty() ? G->empty_collection(G->ctx(), kCollection)
                             : G->collection(G->ctx(), kCollection, copies.data(),
                                             (unsigned)copies.size()));
    if (!coll) return Geom();
    return Geom(G->unary_union(G->ctx(), coll.get()));
}

Geom difference(const GEOSGeometry* a, const GEOSGeometry* b) {
    return Geom(api()->difference(api()->ctx(), a, b));
}

Geom intersection(const GEOSGeometry* a, const GEOSGeometry* b) {
    return Geom(api()->intersection(api()->ctx(), a, b));
}

Geom make_valid(const GEOSGeometry* g) { return Geom(api()->make_valid(api()->ctx(), g)); }

Geom boundary(const GEOSGeometry* g) { return Geom(api()->boundary(api()->ctx(), g)); }

Geom centroid(const GEOSGeometry* g) { return Geom(api()->centroid(api()->ctx(), g)); }

Geom point_on_surface(const GEOSGeometry* g) {
    return Geom(api()->point_on_surface(api()->ctx(), g));
}

Geom constrained_delaunay(const GEOSGeometry* g) { return Geom(api()->cdt(api()->ctx(), g)); }

Geom interpolate_normalized(const GEOSGeometry* line, double frac) {
    return Geom(api()->interpolate_normalized(api()->ctx(), line, frac));
}

int is_valid(const GEOSGeometry* g) {
    const char v = api()->is_valid(api()->ctx(), g);
    return v == 2 ? -1 : v;
}

int is_empty(const GEOSGeometry* g) {
    const char v = api()->is_empty(api()->ctx(), g);
    return v == 2 ? -1 : v;
}

int intersects(const GEOSGeometry* a, const GEOSGeometry* b) {
    const char v = api()->intersects(api()->ctx(), a, b);
    return v == 2 ? -1 : v;
}

int contains(const GEOSGeometry* a, const GEOSGeometry* b) {
    const char v = api()->contains(api()->ctx(), a, b);
    return v == 2 ? -1 : v;
}

double area(const GEOSGeometry* g) {
    double a = 0.0;
    return api()->area(api()->ctx(), g, &a) == 1 ? a : std::numeric_limits<double>::quiet_NaN();
}

double length(const GEOSGeometry* g) {
    double a = 0.0;
    return api()->length(api()->ctx(), g, &a) == 1 ? a : std::numeric_limits<double>::quiet_NaN();
}

double distance(const GEOSGeometry* a, const GEOSGeometry* b) {
    double d = 0.0;
    return api()->distance(api()->ctx(), a, b, &d) == 1 ? d
                                                        : std::numeric_limits<double>::quiet_NaN();
}

bool bounds(const GEOSGeometry* g, double out[4]) {
    GeosApi* G = api();
    if (is_empty(g) != 0) return false;
    return G->xmin(G->ctx(), g, &out[0]) == 1 && G->ymin(G->ctx(), g, &out[1]) == 1
        && G->xmax(G->ctx(), g, &out[2]) == 1 && G->ymax(G->ctx(), g, &out[3]) == 1;
}

bool point_xy(const GEOSGeometry* g, double* x, double* y) {
    GeosApi* G = api();
    if (!g || is_empty(g) != 0) return false;
    return G->get_x(G->ctx(), g, x) == 1 && G->get_y(G->ctx(), g, y) == 1;
}

int type_id(const GEOSGeometry* g) { return api()->type_id(api()->ctx(), g); }

bool has_parts(const GEOSGeometry* g) {
    const int t = type_id(g);
    return t == kMultiPoint || t == kMultiLineString || t == kMultiPolygon || t == kCollection;
}

int num_parts(const GEOSGeometry* g) { return api()->num_geometries(api()->ctx(), g); }

const GEOSGeometry* part(const GEOSGeometry* g, int i) {
    return api()->geometry_n(api()->ctx(), g, i);
}

std::vector<const GEOSGeometry*> parts_or_self(const GEOSGeometry* g) {
    std::vector<const GEOSGeometry*> out;
    if (!has_parts(g)) {
        out.push_back(g);
        return out;
    }
    const int n = num_parts(g);
    for (int i = 0; i < n; ++i) out.push_back(part(g, i));
    return out;
}

Ring exterior_coords(const GEOSGeometry* poly) {
    GeosApi* G = api();
    const GEOSGeometry* r = G->exterior_ring(G->ctx(), poly);
    return r ? read_seq(G->coord_seq(G->ctx(), r)) : Ring();
}

std::vector<Ring> interior_coords(const GEOSGeometry* poly) {
    GeosApi* G = api();
    std::vector<Ring> out;
    const int n = G->num_interior_rings(G->ctx(), poly);
    for (int i = 0; i < n; ++i) {
        const GEOSGeometry* r = G->interior_ring_n(G->ctx(), poly, i);
        out.push_back(r ? read_seq(G->coord_seq(G->ctx(), r)) : Ring());
    }
    return out;
}

Ring line_coords(const GEOSGeometry* line) {
    return read_seq(api()->coord_seq(api()->ctx(), line));
}

Prepared::Prepared(const GEOSGeometry* g) : p_(api()->prepare(api()->ctx(), g)) {}

Prepared::~Prepared() {
    if (p_) api()->prepared_destroy(api()->ctx(), p_);
}

int Prepared::contains_xy(double x, double y) const {
    const char v = api()->prepared_contains_xy(api()->ctx(), p_, x, y);
    return v == 2 ? -1 : v;
}

int Prepared::intersects(const GEOSGeometry* g) const {
    const char v = api()->prepared_intersects(api()->ctx(), p_, g);
    return v == 2 ? -1 : v;
}

}  // namespace navgrow
