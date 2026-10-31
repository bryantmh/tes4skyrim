// union_cdt._triangulate: one sheet part's constrained-Delaunay mesh, hex
// refined and flipped, with the door wedges re-added and terrain / stair
// refinement.  Mirrors the Python call for call, GEOS included.

#include "mapbox/earcut.hpp"
#include "params.h"
#include "union.h"

#include <cmath>
#include <map>
#include <utility>

namespace navgrow {

namespace {

// A door base line as the unit-vector record _snapped_ring and the densify
// guard read: q0, q1, along-direction (ux, uy), length.
struct Line { P2 q0, q1; double ux, uy, dl; };

std::vector<Line> door_lines(const std::vector<DoorEdge>& fixed) {
    std::vector<Line> out;
    for (const DoorEdge& e : fixed) {
        const double dx = e.b1[0] - e.b0[0], dy = e.b1[1] - e.b0[1];
        const double dl = py_hypot(dx, dy);
        if (dl > 1e-9) out.push_back({e.b0, e.b1, dx / dl, dy / dl, dl});
    }
    return out;
}

// _snapped_ring.
Ring snapped_ring(const Ring& coords, const std::vector<Line>& lines) {
    const double snap = P().DOOR_SNAP_PERP;
    Ring out;
    for (P2 c : coords) {
        for (const Line& L : lines) {
            const double vx = c[0] - L.q0[0], vy = c[1] - L.q0[1];
            if (std::fabs(-vx * L.uy + vy * L.ux) > snap) continue;
            const double t = vx * L.ux + vy * L.uy;
            if (!(snap < t && t < L.dl - snap)) continue;
            c = t < 0.5 * L.dl ? L.q0 : L.q1;
            break;
        }
        if (out.empty() || std::fabs(out.back()[0] - c[0]) > 1e-9
            || std::fabs(out.back()[1] - c[1]) > 1e-9)
            out.push_back(c);
    }
    while (out.size() > 1 && out.front() == out.back()) out.pop_back();
    return out;
}

// _snap_outline_to_door_lines: `poly` itself (a clone) when nothing better.
Geom snap_outline(const GEOSGeometry* poly, const std::vector<DoorEdge>& fixed) {
    const std::vector<Line> lines = door_lines(fixed);
    if (lines.empty()) return clone(poly);
    const Ring shell = snapped_ring(exterior_coords(poly), lines);
    if (shell.size() < 3) return clone(poly);
    std::vector<Ring> holes;
    for (const Ring& r : interior_coords(poly)) {
        Ring h = snapped_ring(r, lines);
        if (h.size() >= 3) holes.push_back(std::move(h));
    }
    Geom out = polygon(shell, holes);
    if (!out) return clone(poly);
    if (is_valid(out.get()) != 1) out = buffer(out.get(), 0.0);
    if (!out || is_empty(out.get()) != 0 || is_valid(out.get()) != 1
        || type_id(out.get()) != kPolygon)
        return clone(poly);
    const double pa = area(poly), oa = area(out.get());
    if (std::isnan(pa) || std::isnan(oa)
        || std::fabs(oa - pa) > 0.02 * std::max(pa, 1.0))
        return clone(poly);
    return out;
}

struct DoorTri { P2 b0, b1, apex; };

// _reserve_door_wedges: the parts left once each door wedge is cut out.
std::vector<Geom> reserve_door_wedges(const GEOSGeometry* poly,
                                      const std::vector<DoorEdge>& fixed,
                                      std::vector<DoorTri>& door_tris) {
    std::vector<Geom> reserved, parts;
    for (const DoorEdge& e : fixed) {
        if (!e.has_apex) continue;
        Geom tri = need(polygon({e.b0, e.b1, e.apex}), "Polygon");
        if (is_valid(tri.get()) != 1 || area(tri.get()) < 1.0) continue;
        reserved.push_back(std::move(tri));
        door_tris.push_back({e.b0, e.b1, e.apex});
    }
    if (reserved.empty()) {
        parts.push_back(clone(poly));
        return parts;
    }
    Geom cut = clone(poly);
    for (const Geom& r : reserved) {
        cut = difference(cut.get(), r.get());
        if (!cut) break;
    }
    if (cut && type_id(cut.get()) == kCollection) {
        std::vector<const GEOSGeometry*> gs;
        for (const GEOSGeometry* g : parts_or_self(cut.get()))
            if (type_id(g) == kPolygon) gs.push_back(g);
        if (!gs.empty()) cut = union_all(gs);
    }
    if (!cut) {
        door_tris.clear();
        parts.push_back(clone(poly));
        return parts;
    }
    if (is_empty(cut.get()) != 0) return parts;
    if (type_id(cut.get()) == kPolygon) {
        parts.push_back(std::move(cut));
    } else if (type_id(cut.get()) == kMultiPolygon) {
        for (const GEOSGeometry* g : parts_or_self(cut.get()))
            if (type_id(g) == kPolygon && area(g) >= 1.0) parts.push_back(clone(g));
    }
    return parts;
}

// _runs_along_door_line.
bool runs_along_door_line(const std::vector<Line>& guard, double x0, double y0,
                          double x1, double y1) {
    for (const Line& L : guard) {
        bool ok = true;
        const double pts[3][2] = {{x0, y0}, {x1, y1}, {0.5 * (x0 + x1), 0.5 * (y0 + y1)}};
        for (const auto& p : pts) {
            const double vx = p[0] - L.q0[0], vy = p[1] - L.q0[1];
            const double t = vx * L.ux + vy * L.uy;
            if (std::fabs(-vx * L.uy + vy * L.ux) > 4.0 || !(-4.0 <= t && t <= L.dl + 4.0)) {
                ok = false;
                break;
            }
        }
        if (ok) return true;
    }
    return false;
}

// _grid_stations.
void grid_stations(double x0, double y0, double x1, double y1, double target,
                   Ring& out) {
    const double dx = x1 - x0, dy = y1 - y0;
    const double length = py_hypot(dx, dy);
    if (length < 1e-9) return;
    const double ux = dx / length, uy = dy / length;
    const double s0 = x0 * ux + y0 * uy;
    const double gap = 0.25 * target;
    for (double k = std::floor(s0 / target) + 1.0; k * target < s0 + length; k += 1.0) {
        const double d = k * target - s0;
        if (gap <= d && d <= length - gap) out.push_back({x0 + ux * d, y0 + uy * d});
    }
}

Ring densify_ring(const Ring& coords, const std::vector<Line>& guard, double target) {
    Ring out;
    for (size_t i = 0; i + 1 < coords.size(); ++i) {
        const double x0 = coords[i][0], y0 = coords[i][1];
        const double x1 = coords[i + 1][0], y1 = coords[i + 1][1];
        out.push_back(coords[i]);
        if (runs_along_door_line(guard, x0, y0, x1, y1)) continue;
        grid_stations(x0, y0, x1, y1, target, out);
    }
    return out;
}

// _densified; a null Geom where shapely would raise.
Geom densified(const GEOSGeometry* part, const std::vector<Line>& guard, double target) {
    const Ring shell = densify_ring(exterior_coords(part), guard, target);
    std::vector<Ring> holes;
    for (const Ring& r : interior_coords(part)) {
        Ring h = densify_ring(r, guard, target);
        if (h.size() >= 3) holes.push_back(std::move(h));
    }
    Geom dense = polygon(shell, holes);
    if (!dense) return dense;
    return is_valid(dense.get()) == 1 ? std::move(dense) : buffer(dense.get(), 0.0);
}

using Pid = std::function<int(double, double)>;

// _cdt_part.
std::vector<Tri3> cdt_part(const GEOSGeometry* part, const std::vector<Line>& guard,
                           double target, const Pid& pid) {
    std::vector<Tri3> tris;
    Geom dense = densified(part, guard, target);
    if (!dense) return tris;
    Geom out = constrained_delaunay(dense.get());
    if (!out || !has_parts(out.get())) return tris;
    for (const GEOSGeometry* g : parts_or_self(out.get())) {
        if (type_id(g) != kPolygon) continue;
        const Ring c = exterior_coords(g);
        if (c.size() != 4) continue;
        const int ia = pid(c[0][0], c[0][1]);
        const int ib = pid(c[1][0], c[1][1]);
        const int ic = pid(c[2][0], c[2][1]);
        if (ia != ib && ib != ic && ia != ic) tris.push_back({ia, ib, ic});
    }
    return tris;
}

// _lattice_points.
std::vector<double> lattice_points(const GEOSGeometry* part, double spacing) {
    std::vector<double> out;
    Geom eroded = buffer(part, -0.45 * spacing);
    if (!eroded || is_empty(eroded.get()) != 0) return out;
    double b[4];
    if (!bounds(part, b)) return out;
    const double row_h = spacing * 0.8660254037844386;
    std::vector<double> cand;
    int row = 0;
    for (double y = b[1] + 0.5 * row_h; y < b[3]; y += row_h, ++row)
        for (double x = b[0] + (row % 2 == 0 ? 0.25 : 0.75) * spacing; x < b[2]; x += spacing) {
            cand.push_back(x);
            cand.push_back(y);
        }
    if (cand.empty()) return out;
    Prepared ready(eroded.get());
    for (size_t k = 0; k < cand.size(); k += 2)
        if (ready.contains_xy(cand[k], cand[k + 1]) == 1) {
            out.push_back(cand[k]);
            out.push_back(cand[k + 1]);
        }
    return out;
}

// _hex_refine.
std::vector<Tri3> hex_refine_part(const GEOSGeometry* part, std::vector<double>& pts,
                                  const Pid& pid, std::vector<Tri3> tris, double spacing) {
    if (tris.empty()) return tris;
    const std::vector<double> cand = lattice_points(part, spacing);
    if (cand.empty()) {
        flip2d_mesh(pts, tris, 4);
        return tris;
    }
    return hex_refine_mesh(pts, std::move(tris), cand, spacing, pid);
}

// _earcut_fallback.
Plan2 earcut_fallback(const GEOSGeometry* poly) {
    using Pt = std::array<double, 2>;
    std::vector<std::vector<Pt>> rings;
    auto add = [&rings](const Ring& r) {
        if (r.size() >= 4) rings.emplace_back(r.begin(), r.end() - 1);
    };
    add(exterior_coords(poly));
    for (const Ring& r : interior_coords(poly)) add(r);
    Plan2 out;
    if (rings.empty()) return out;
    for (const auto& r : rings)
        for (const Pt& p : r) { out.xy.push_back(p[0]); out.xy.push_back(p[1]); }
    const std::vector<uint32_t> idx = mapbox::earcut<uint32_t>(rings);
    for (size_t i = 0; i + 2 < idx.size(); i += 3)
        out.t.push_back({(int)idx[i], (int)idx[i + 1], (int)idx[i + 2]});
    return out;
}

}  // namespace

bool door_edge_on_part(const DoorEdge& e, const GEOSGeometry* part) {
    Geom p = point(0.5 * (e.b0[0] + e.b1[0]), 0.5 * (e.b0[1] + e.b1[1]));
    if (!p) return false;
    const int in = contains(part, p.get());
    if (in < 0) return false;
    if (in == 1) return true;
    Geom bd = boundary(part);
    if (!bd) return false;
    const double d = distance(bd.get(), p.get());
    return !std::isnan(d) && d <= 2.0;
}

std::vector<double> steep_seeds(const Group& strips, double target_edge) {
    std::vector<double> out;
    const double gap = P().STOREY_GAP_Z;
    for (const UStrip* s : strips) {
        const double ax = s->a[0], ay = s->a[1], bx = s->b[0], by = s->b[1];
        const double run = py_hypot(bx - ax, by - ay);
        if (run < 1e-3) continue;
        const double rise = std::fabs(s->b[2] - s->a[2]);
        if (s->land || !(rise / run * target_edge > gap * 0.5)) continue;
        const double step = std::max(P().RIBBON_SEED_STEP,
                                     gap * 0.33 * run / std::max(rise, 1e-6));
        const double h = s->half;
        const double offs[3] = {-h * 0.6, 0.0, h * 0.6};
        const long long n = std::max(1LL, (long long)(run / step));
        for (long long k = 0; k <= n; ++k) {
            const double f = (double)k / (double)n;
            const double cx = ax + (bx - ax) * f, cy = ay + (by - ay) * f;
            for (double off : offs) {
                out.push_back(cx + s->wx * off);
                out.push_back(cy + s->wy * off);
            }
        }
    }
    return out;
}

Plan2 triangulate(const GEOSGeometry* poly_in, double target_edge,
                  const std::vector<DoorEdge>& fixed, const std::vector<double>& steep,
                  const Land* land, double max_edge) {
    Plan2 out;
    if (exterior_coords(poly_in).size() < 4) return out;
    Geom poly = fixed.empty() ? clone(poly_in) : snap_outline(poly_in, fixed);
    std::vector<DoorTri> door_tris;
    const std::vector<Geom> parts = reserve_door_wedges(poly.get(), fixed, door_tris);
    std::map<std::pair<double, double>, int> index;
    std::vector<double>& pts = out.xy;
    const Pid pid = [&index, &pts](double x, double y) {
        const auto key = std::make_pair(py_round3(x), py_round3(y));
        auto it = index.find(key);
        if (it != index.end()) return it->second;
        const int i = (int)(pts.size() / 2);
        pts.push_back(x);
        pts.push_back(y);
        index.emplace(key, i);
        return i;
    };
    const std::vector<Line> guard = door_lines(fixed);
    for (const Geom& part : parts) {
        std::vector<Tri3> t = hex_refine_part(part.get(), pts, pid,
                                              cdt_part(part.get(), guard, target_edge, pid),
                                              target_edge);
        out.t.insert(out.t.end(), t.begin(), t.end());
    }
    EdgeSet ring_edges;
    for (const DoorTri& d : door_tris) {
        const int ia = pid(d.b0[0], d.b0[1]), ib = pid(d.b1[0], d.b1[1]);
        const int ic = pid(d.apex[0], d.apex[1]);
        if (ia == ib || ib == ic || ia == ic) continue;
        const double cross = (d.b1[0] - d.b0[0]) * (d.apex[1] - d.b0[1])
                           - (d.apex[0] - d.b0[0]) * (d.b1[1] - d.b0[1]);
        out.t.push_back(cross > 0 ? Tri3{ia, ib, ic} : Tri3{ia, ic, ib});
        for (const auto& uv : {std::make_pair(ia, ib), std::make_pair(ib, ic),
                               std::make_pair(ia, ic)})
            ring_edges.insert({std::min(uv.first, uv.second), std::max(uv.first, uv.second)});
    }
    if (out.t.empty()) return earcut_fallback(poly.get());
    if (land) {
        const double min_edge = P().LAND_MIN_EDGE;
        refine_land_mesh(out.xy, out.t, *land, max_edge * max_edge, min_edge * min_edge,
                         P().LAND_RELIEF_TOL, ring_edges);
        flip2d_mesh(out.xy, out.t, 4);
    }
    if (!steep.empty()) {
        const double e = P().STEEP_REFINE_EDGE;
        refine_steep_mesh(out.xy, out.t, steep, e * 2.0, e * e, ring_edges);
    }
    return out;
}

}  // namespace navgrow
