// union_geom: ribbon polygons, strip heights and distances, and the strip
// records the native kernels read.  Each function mirrors its Python twin.

#include "params.h"
#include "union.h"

#include <algorithm>
#include <cmath>

namespace navgrow {

namespace {

// _repair_invalid_outline: every lobe of buffer(0) plus a band over the
// centerline, unioned.
Geom repair_invalid_outline(const GEOSGeometry* poly, const UStrip& s) {
    std::vector<Geom> pieces;
    Geom fixed = need(buffer(poly, 0.0), "buffer");
    if (is_empty(fixed.get()) == 0) {
        for (const GEOSGeometry* g : parts_or_self(fixed.get()))
            if (type_id(g) == kPolygon && area(g) > 0.0) pieces.push_back(clone(g));
    }
    Geom spine = need(line_string({{s.a[0], s.a[1]}, {s.b[0], s.b[1]}}), "LineString");
    pieces.push_back(need(buffer(spine.get(), std::max(P().RIBBON_GROW_MIN_HALF, 1.0), 2, 1),
                          "buffer"));
    std::vector<const GEOSGeometry*> raw;
    for (const Geom& g : pieces) raw.push_back(g.get());
    Geom out = union_all(raw);
    if (!out) return std::move(pieces.back());
    if (type_id(out.get()) != kPolygon && !has_parts(out.get())) return std::move(pieces.back());
    return out;
}

Geom ribbon_uncached(const UStrip& s) {
    if (s.has_poly) {
        Geom p = need(polygon(s.poly), "Polygon");
        return is_valid(p.get()) == 1 ? std::move(p) : repair_invalid_outline(p.get(), s);
    }
    const double h = s.half;
    return need(polygon({{s.a[0] + s.wx * h, s.a[1] + s.wy * h},
                         {s.b[0] + s.wx * h, s.b[1] + s.wy * h},
                         {s.b[0] - s.wx * h, s.b[1] - s.wy * h},
                         {s.a[0] - s.wx * h, s.a[1] - s.wy * h}}),
                "Polygon");
}

double clamp01(double t) { return std::max(0.0, std::min(1.0, t)); }

bool point_in_ring(double px, double py, const Ring& poly) {
    bool inside = false;
    const size_t n = poly.size();
    for (size_t i = 0; i < n; ++i) {
        const P2& p1 = poly[i];
        const P2& p2 = poly[(i + 1) % n];
        if ((p1[1] > py) != (p2[1] > py)) {
            const double dy = (p2[1] - p1[1]) != 0.0 ? (p2[1] - p1[1]) : 1e-12;
            if (px < p1[0] + (py - p1[1]) * (p2[0] - p1[0]) / dy) inside = !inside;
        }
    }
    return inside;
}

double seg_dist_py(double px, double py, const P2& a, const P2& b) {
    const double dx = b[0] - a[0], dy = b[1] - a[1];
    const double d2 = dx * dx + dy * dy;
    const double t = d2 < 1e-9 ? 0.0 : clamp01(((px - a[0]) * dx + (py - a[1]) * dy) / d2);
    return py_hypot(px - (a[0] + dx * t), py - (a[1] + dy * t));
}

}  // namespace

const GEOSGeometry* ribbon_polygon(const UStrip& s) {
    if (!s.ribbon) s.ribbon = std::make_shared<Geom>(ribbon_uncached(s));
    return s.ribbon->get();
}

Group valid_ribbons(const Group& strips) {
    Group out;
    for (const UStrip* s : strips) {
        const GEOSGeometry* p = ribbon_polygon(*s);
        if (is_valid(p) == 1 && is_empty(p) == 0) out.push_back(s);
    }
    return out;
}

double height_on(const UStrip& s, const Land* land, double px, double py) {
    if (s.prof.size() >= 2) {
        bool have = false;
        double best_d2 = 0.0, best_z = s.prof[0][2];
        for (size_t k = 0; k + 1 < s.prof.size(); ++k) {
            const P3& qa = s.prof[k];
            const P3& qb = s.prof[k + 1];
            const double dx = qb[0] - qa[0], dy = qb[1] - qa[1];
            const double d2 = dx * dx + dy * dy;
            const double t = d2 < 1e-9 ? 0.0
                : clamp01(((px - qa[0]) * dx + (py - qa[1]) * dy) / d2);
            const double cx = qa[0] + dx * t, cy = qa[1] + dy * t;
            const double dd = py_square(px - cx) + py_square(py - cy);
            if (!have || dd < best_d2) {
                have = true;
                best_d2 = dd;
                best_z = qa[2] + (qb[2] - qa[2]) * t;
            }
        }
        return best_z;
    }
    const double dx = s.b[0] - s.a[0], dy = s.b[1] - s.a[1];
    const double d2 = dx * dx + dy * dy;
    const double t = d2 < 1e-9 ? 0.0
        : clamp01(((px - s.a[0]) * dx + (py - s.a[1]) * dy) / d2);
    if (s.land && land) return land->z(px, py);
    return s.a[2] + (s.b[2] - s.a[2]) * t;
}

double distance_to(const UStrip& s, double px, double py) {
    if (s.has_poly) {
        if (point_in_ring(px, py, s.poly)) return 0.0;
        double best = 0.0;
        for (size_t i = 0; i < s.poly.size(); ++i) {
            const double d = seg_dist_py(px, py, s.poly[i], s.poly[(i + 1) % s.poly.size()]);
            if (i == 0 || d < best) best = d;
        }
        return best;
    }
    return seg_dist_py(px, py, {s.a[0], s.a[1]}, {s.b[0], s.b[1]});
}

UStrip clip_strip_near(const UStrip& s, double nx, double ny, double r,
                       const GEOSGeometry* piece) {
    UStrip out = s;
    out.ribbon.reset();
    const double ax = s.a[0], ay = s.a[1], az = s.a[2];
    const double bx = s.b[0], by = s.b[1], bz = s.b[2];
    const double run = py_hypot(bx - ax, by - ay);
    if (run > 1e-6) {
        const double t_node = clamp01(((nx - ax) * (bx - ax) + (ny - ay) * (by - ay))
                                      / (run * run));
        if (py_hypot(ax - nx, ay - ny) <= py_hypot(bx - nx, by - ny)) {
            const double f = std::min(1.0, t_node + r / run);
            out.b = {ax + (bx - ax) * f, ay + (by - ay) * f, az + (bz - az) * f};
        } else {
            const double f = std::max(0.0, t_node - r / run);
            out.a = {ax + (bx - ax) * f, ay + (by - ay) * f, az + (bz - az) * f};
        }
    }
    const GEOSGeometry* best = nullptr;
    double best_area = 0.0;
    for (const GEOSGeometry* g : parts_or_self(piece)) {
        if (type_id(g) != kPolygon) continue;
        const double a = area(g);
        if (!best || a > best_area) { best = g; best_area = a; }
    }
    if (best) {
        out.has_poly = true;
        out.poly = exterior_coords(best);
    }
    return out;
}

const Land* land_of(const Group& g, const Land* land) {
    for (const UStrip* s : g)
        if (s->land) return land;
    return nullptr;
}

FlatStrips flat_strips(const Group& g, const Land* land) {
    FlatStrips F;
    for (const UStrip* s : g) {
        double off = 0, n = 0, poff = 0, pn = 0;
        if (s->has_poly) {
            off = (double)(F.poly.size() / 2);
            n = (double)s->poly.size();
            for (const P2& p : s->poly) { F.poly.push_back(p[0]); F.poly.push_back(p[1]); }
        }
        if (s->prof.size() >= 2) {
            poff = (double)(F.prof.size() / 3);
            pn = (double)s->prof.size();
            for (const P3& p : s->prof) F.prof.insert(F.prof.end(), p.begin(), p.end());
        }
        F.rows.insert(F.rows.end(), {s->a[0], s->a[1], s->a[2], s->b[0], s->b[1], s->b[2],
                                     s->half, off, n, poff, pn, s->land ? 1.0 : 0.0});
    }
    const Land* gl = land_of(g, land);
    F.strips = load_strips(F.rows.data(), g.size(), F.poly.data(), F.prof.size() / 3,
                           gl != nullptr);
    return F;
}

std::vector<double> levels_at_py(const Group& g, const Land* land, double px,
                                 double py) {
    const Land* gl = land_of(g, land);
    std::vector<double> zs;
    for (const UStrip* s : g) {
        const double d = distance_to(*s, px, py);
        if (s->has_poly ? d <= 1e-6 : d <= s->half + 1e-6)
            zs.push_back(height_on(*s, gl, px, py));
    }
    std::vector<double> out;
    if (zs.empty()) return out;
    std::sort(zs.begin(), zs.end());
    PySum sum;
    size_t n = 0;
    double last = zs[0];
    for (double z : zs) {
        if (n && z - last > P().SAME_SURFACE_Z) {
            out.push_back(sum.value() / (double)n);
            sum = PySum();
            n = 0;
        }
        sum.add(z);
        ++n;
        last = z;
    }
    out.push_back(sum.value() / (double)n);
    return out;
}

}  // namespace navgrow
