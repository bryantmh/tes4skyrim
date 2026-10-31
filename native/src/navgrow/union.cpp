// corridor_union.build_union_mesh: per-sheet coverage, door claims, the CDT,
// surface emission, then the weld / merge / stitch passes.  The Python dicts
// whose order reaches the output are kept as ordered containers.

#include "params.h"
#include "pybuf.h"
#include "pyconv.h"
#include "stitch.h"
#include "union.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>

namespace navgrow {

namespace {

using GeomPtr = std::shared_ptr<Geom>;

struct Ctx {
    UnionArgs& A;
    std::map<int, std::vector<GeomPtr>> junction_extra, junction_drop;
    std::map<int, Group> junction_strips;
    std::vector<std::pair<GeomPtr, Group>> claimed;
    std::set<size_t> claimed_ids;
};

// _has_coverage.
bool has_coverage(const Group& strips, const UnionArgs& A) {
    std::vector<const GEOSGeometry*> polys;
    for (const UStrip* s : valid_ribbons(strips)) polys.push_back(ribbon_polygon(*s));
    if (polys.empty() || !A.has_bounds) return !polys.empty();
    const double* cb = A.bounds;
    bool inside = false, all_out = true;
    for (const GEOSGeometry* p : polys) {
        double b[4];
        if (!bounds(p, b)) { all_out = false; continue; }
        inside = inside || (b[0] > cb[0] && b[2] < cb[2] && b[1] > cb[1] && b[3] < cb[3]);
        all_out = all_out && (b[2] < cb[0] || b[0] > cb[2] || b[3] < cb[1] || b[1] > cb[3]);
    }
    if (inside) return true;
    if (all_out) return false;
    Geom u = need(union_all(polys), "unary_union");
    Geom bx = need(box(cb[0], cb[1], cb[2], cb[3]), "box");
    Geom in = need(intersection(u.get(), bx.get()), "intersection");
    return is_empty(in.get()) == 0;
}

// _node_geometry: node XY (first seen) and widest half, keyed by node.
void node_geometry(const Group& strips, std::map<int, P2>& pts,
                   std::map<int, double>& half) {
    for (const UStrip* s : strips) {
        if (s->ei < 0) continue;
        pts.emplace(s->ei, P2{s->na[0], s->na[1]});
        half[s->ei] = std::max(half.count(s->ei) ? half[s->ei] : 0.0, s->half);
        if (s->ej != s->ei) {
            pts.emplace(s->ej, P2{s->nb[0], s->nb[1]});
            half[s->ej] = std::max(half.count(s->ej) ? half[s->ej] : 0.0, s->half);
        }
    }
}

// _junction_transfers.
void junction_transfers(Ctx& C, const std::vector<Group>& sheets,
                        const std::map<int, P2>& node_pts,
                        const std::map<int, double>& node_half) {
    if (node_pts.empty()) return;
    std::map<int, int> node_owner;
    std::map<std::pair<int, int>, int> sheet_of;
    for (size_t gi = 0; gi < sheets.size(); ++gi)
        for (const UStrip* s : sheets[gi]) {
            sheet_of[{s->ei, s->ej}] = (int)gi;
            if (s->ei < 0) continue;
            node_owner.emplace(s->ei, (int)gi);
            if (s->ej != s->ei) node_owner.emplace(s->ej, (int)gi);
        }
    for (const UStrip* s : C.A.strips) {
        if (s->ei < 0) continue;
        auto sit = sheet_of.find({s->ei, s->ej});
        if (sit == sheet_of.end()) continue;
        const int gi = sit->second;
        const int nds[2] = {s->ei, s->ej};
        for (int k = 0; k < (s->ej == s->ei ? 1 : 2); ++k) {
            const int nd = nds[k];
            auto own = node_owner.find(nd);
            auto np = node_pts.find(nd);
            if (own == node_owner.end() || own->second == gi || np == node_pts.end()) continue;
            const double nx = np->second[0], ny = np->second[1];
            auto nh = node_half.find(nd);
            const double r = std::max(nh == node_half.end() ? 0.0 : nh->second,
                                      P().RIBBON_HALF_WIDTH);
            Geom pt = point(nx, ny);
            Geom disc = pt ? buffer(pt.get(), r) : Geom();
            Geom piece = disc ? intersection(ribbon_polygon(*s), disc.get()) : Geom();
            if (!piece || is_empty(piece.get()) != 0 || area(piece.get()) < 1.0) continue;
            C.A.store->push_back(clip_strip_near(*s, nx, ny, r, piece.get()));
            C.junction_strips[own->second].push_back(&C.A.store->back());
            auto shared = std::make_shared<Geom>(std::move(piece));
            C.junction_extra[own->second].push_back(shared);
            C.junction_drop[gi].push_back(shared);
        }
    }
}

std::vector<const GEOSGeometry*> raw(const std::vector<GeomPtr>& v) {
    std::vector<const GEOSGeometry*> out;
    for (const GeomPtr& g : v) out.push_back(g->get());
    return out;
}

// _sheet_coverage, or a null Geom.
Geom sheet_coverage(Ctx& C, const Group& group, int gi) {
    std::vector<const GEOSGeometry*> gpolys;
    for (const UStrip* s : valid_ribbons(group)) gpolys.push_back(ribbon_polygon(*s));
    auto ex = C.junction_extra.find(gi);
    if (ex != C.junction_extra.end())
        for (const GEOSGeometry* g : raw(ex->second)) gpolys.push_back(g);
    if (gpolys.empty()) return Geom();
    Geom g = need(union_all(gpolys), "unary_union");
    auto dr = C.junction_drop.find(gi);
    if (dr != C.junction_drop.end() && !dr->second.empty()) {
        Geom du = union_all(raw(dr->second));
        Geom cut = du ? difference(g.get(), du.get()) : Geom();
        if (cut && is_empty(cut.get()) == 0) g = std::move(cut);
    }
    if (C.A.has_bounds) {
        const double* b = C.A.bounds;
        Geom bx = need(box(b[0], b[1], b[2], b[3]), "box");
        g = need(intersection(g.get(), bx.get()), "intersection");
    }
    for (const auto& prev : C.claimed) {
        if (is_empty(g.get()) != 0) break;
        Geom shared = intersection(g.get(), prev.first->get());
        if (!shared || is_empty(shared.get()) != 0 || area(shared.get()) < 1.0) continue;
        Geom dup = same_surface_region(group, prev.second, shared.get(), C.A.land);
        if (!dup || is_empty(dup.get()) != 0) continue;
        Geom trimmed = difference(g.get(), dup.get());
        if (trimmed && is_empty(trimmed.get()) == 0) g = std::move(trimmed);
    }
    return is_empty(g.get()) != 0 ? Geom() : std::move(g);
}

// The land-slit wall probe (corridor._land_slit_ok's ok).
bool slit_ok(const GEOSGeometry* piece, const UnionArgs& A) {
    Geom ring = need(line_string(exterior_coords(piece)), "LineString");
    const double len = length(ring.get());
    const double step = P().RIBBON_GROW_STEP;
    const long long n = std::max(1LL, (long long)py_floordiv(len, step));
    for (long long k = 0; k < n; ++k) {
        Geom pt = need(interpolate_normalized(ring.get(), (double)k / (double)n),
                       "interpolate");
        double x, y;
        if (!point_xy(pt.get(), &x, &y)) throw GeosError("interpolate gave no point");
        const double z = A.land->z(x, y);
        if (A.geo->wall_at(x, y, 1.0, 0.0, 0.0, 1.0, z + P().RIBBON_GROW_SLAB_Z_BOTTOM,
                           z + P().AGENT_HEIGHT, step, step))
            return false;
    }
    return true;
}

// _close_land_slits.
Geom close_land_slits(Geom g, const Group& group, const UnionArgs& A) {
    if (!A.geo || !A.land || !land_of(group, A.land)) return g;
    const double h = P().LAND_SLIT_HALF;
    Geom grown = need(buffer(g.get(), h, 1, 2), "buffer");
    Geom closed = need(buffer(grown.get(), -h, 1, 2), "buffer");
    if (is_valid(closed.get()) != 1) closed = need(make_valid(closed.get()), "make_valid");
    Geom fill = difference(closed.get(), g.get());
    if (!fill) return g;
    std::vector<const GEOSGeometry*> keep{g.get()};
    for (const GEOSGeometry* p : parts_or_self(fill.get())) {
        const int t = type_id(p);
        if ((t != kPolygon && t != kMultiPolygon) || is_empty(p) != 0 || !(area(p) > 1.0))
            continue;
        if (t == kMultiPolygon) throw GeosError("MultiPolygon slit has no exterior");
        if (slit_ok(p, A)) keep.push_back(p);
    }
    if (keep.size() == 1) return g;
    Geom u = union_all(keep);
    return u ? std::move(u) : std::move(g);
}

// _wedge_fits_part.
bool wedge_fits_part(const DoorEdge& e, const GEOSGeometry* part) {
    if (!e.has_apex) return true;
    Geom wedge = polygon({e.b0, e.b1, e.apex});
    if (!wedge || is_valid(wedge.get()) != 1) return true;
    const double wa = area(wedge.get());
    if (!(wa > 1.0)) return true;
    Geom in = intersection(part, wedge.get());
    if (!in) return true;
    const double ia = area(in.get());
    return std::isnan(ia) || !(ia < P().DOOR_CLAIM_MIN_FRAC * wa);
}

// _storey_holds_door.
bool storey_holds_door(const DoorEdge& e, const Group& group, const Land* land) {
    if (!e.has_z) return true;
    const std::vector<double> lv = levels_at_py(group, land, 0.5 * (e.b0[0] + e.b1[0]),
                                                0.5 * (e.b0[1] + e.b1[1]));
    if (lv.empty()) return true;
    for (double q : lv)
        if (std::fabs(q - e.z) <= P().STOREY_GAP_Z) return true;
    return false;
}

using Levels = std::vector<std::vector<double>>;

std::vector<double> sorted_unique(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    v.erase(std::unique(v.begin(), v.end()), v.end());
    return v;
}

// _apply_door_apex_levels.
void apply_door_apex_levels(const Plan2& v2, Levels& levels,
                            const std::vector<DoorEdge>& doors) {
    if (doors.empty()) return;
    const size_t n = v2.xy.size() / 2;
    std::map<std::pair<double, double>, size_t> idx;
    for (size_t i = 0; i < n; ++i)
        idx.emplace(std::make_pair(py_round3(v2.xy[2 * i]), py_round3(v2.xy[2 * i + 1])), i);
    auto find = [&idx](const P2& p) {
        auto it = idx.find({py_round3(p[0]), py_round3(p[1])});
        return it == idx.end() ? (size_t)-1 : it->second;
    };
    for (const DoorEdge& e : doors) {
        const size_t i0 = find(e.b0), i1 = find(e.b1);
        if (i0 == (size_t)-1 || i1 == (size_t)-1) continue;
        if (e.has_z)
            for (size_t ii : {i0, i1}) {
                bool near = false;
                for (double q : levels[ii]) near = near || std::fabs(q - e.z) <= P().SAME_SURFACE_Z;
                if (!near) {
                    levels[ii].push_back(e.z);
                    levels[ii] = sorted_unique(levels[ii]);
                }
            }
        std::vector<double> base = levels[i0];
        base.insert(base.end(), levels[i1].begin(), levels[i1].end());
        base = sorted_unique(base);
        if (base.empty()) continue;
        std::vector<double> lv_door;
        for (double q : base)
            if (!e.has_z || std::fabs(q - e.z) <= P().STOREY_GAP_Z) lv_door.push_back(q);
        if (lv_door.empty()) lv_door = base;
        double reach = py_hypot(e.b1[0] - e.b0[0], e.b1[1] - e.b0[1]);
        if (e.has_apex) {
            const size_t ia = find(e.apex);
            if (ia != (size_t)-1 && levels[ia].empty()) levels[ia] = lv_door;
            reach = std::max(reach, py_hypot(e.apex[0] - 0.5 * (e.b0[0] + e.b1[0]),
                                             e.apex[1] - 0.5 * (e.b0[1] + e.b1[1])) + 4.0);
        }
        const double mx = 0.5 * (e.b0[0] + e.b1[0]), my = 0.5 * (e.b0[1] + e.b1[1]);
        for (size_t i = 0; i < n; ++i)
            if (levels[i].empty() && py_hypot(v2.xy[2 * i] - mx, v2.xy[2 * i + 1] - my) <= reach)
                levels[i] = lv_door;
    }
}

// --------------------------------------------------------------------------
// _emit_surfaces
// --------------------------------------------------------------------------

using Bands = std::vector<std::vector<double>>;
using Reps = std::vector<std::pair<double, double>>;

// _storeys_of on a sorted list.
Bands storeys_of(const std::vector<double>& lv) {
    Bands out;
    for (double z : lv) {
        if (!out.empty() && z - out.back().back() <= P().STOREY_GAP_Z) out.back().push_back(z);
        else out.push_back({z});
    }
    return out;
}

bool reaches(const Reps& bands, double z) {
    for (const auto& b : bands)
        if (b.first - P().REACH_TOL <= z && z <= b.second + P().REACH_TOL) return true;
    return false;
}

// _triangle_surfaces over one frame's corner reps.
std::vector<double> triangle_surfaces(const std::vector<Reps>& reps, const Tri3& tri) {
    std::vector<const Reps*> present;
    for (int k : tri)
        if (!reps[(size_t)k].empty()) present.push_back(&reps[(size_t)k]);
    std::vector<double> surfaces;
    if (present.empty()) return surfaces;
    std::vector<double> proposals;
    for (const Reps* r : present)
        for (const auto& b : *r) { proposals.push_back(b.first); proposals.push_back(b.second); }
    for (double z : sorted_unique(proposals)) {
        bool all = true;
        for (const Reps* r : present) all = all && reaches(*r, z);
        if (!all) continue;
        if (surfaces.empty() || z - surfaces.back() > P().STOREY_GAP_Z) surfaces.push_back(z);
    }
    return surfaces;
}

struct Surface { double z; int frame; };

struct Emit {
    const Plan2& v2;
    std::vector<Bands> corner_bands;
    std::map<int, Bands> bare;
    std::map<std::pair<int, int>, int> vid;
    std::vector<Vert> verts;

    const Bands* bands_of(int k) const {
        if (!corner_bands[(size_t)k].empty()) return &corner_bands[(size_t)k];
        auto it = bare.find(k);
        return it == bare.end() || it->second.empty() ? nullptr : &it->second;
    }

    // _slot_of.
    int slot_of(int k, double z) const {
        const Bands* bands = bands_of(k);
        if (!bands) return 0;
        int best_i = 0;
        bool have = false;
        double best_d = 0.0;
        for (size_t i = 0; i < bands->size(); ++i)
            for (double x : (*bands)[i]) {
                const double d = x >= z ? x - z : z - x;
                if (!have || d < best_d) { have = true; best_d = d; best_i = (int)i; }
            }
        return best_i;
    }

    // _emit_vertex.
    int vertex(int corner, int slot, double fallback) {
        auto it = vid.find({corner, slot});
        if (it != vid.end()) return it->second;
        const Bands* bands = bands_of(corner);
        double zz = fallback;
        if (bands && slot >= 0 && (size_t)slot < bands->size()) {
            std::vector<double> band = (*bands)[(size_t)slot];
            std::sort(band.begin(), band.end());
            zz = band[band.size() / 2];
        }
        const int got = (int)verts.size();
        verts.push_back({v2.xy[2 * (size_t)corner], v2.xy[2 * (size_t)corner + 1], zz, false});
        vid[{corner, slot}] = got;
        return got;
    }
};

UMesh emit_surfaces(const Plan2& v2, Levels levels, const Land* land) {
    const size_t n = v2.xy.size() / 2;
    std::vector<std::vector<double>> shifts{std::vector<double>(n, 0.0)};
    if (land) {
        shifts.emplace_back(n);
        for (size_t i = 0; i < n; ++i) shifts[1][i] = land->z(v2.xy[2 * i], v2.xy[2 * i + 1]);
    }
    Emit E{v2, {}, {}, {}, {}};
    std::vector<Reps> reps_all(n);
    for (size_t i = 0; i < n; ++i) {
        std::vector<double> lv = levels[i];
        std::sort(lv.begin(), lv.end());
        E.corner_bands.push_back(storeys_of(lv));
        for (const auto& b : E.corner_bands.back())
            reps_all[i].push_back({*std::min_element(b.begin(), b.end()),
                                   *std::max_element(b.begin(), b.end())});
    }
    std::vector<std::vector<Surface>> tri_surfaces(v2.t.size());
    for (size_t f = 0; f < shifts.size(); ++f) {
        std::vector<Reps> rel(n);
        for (size_t i = 0; i < n; ++i)
            for (const auto& b : reps_all[i])
                rel[i].push_back({b.first - shifts[f][i], b.second - shifts[f][i]});
        for (size_t ti = 0; ti < v2.t.size(); ++ti)
            for (double z : triangle_surfaces(rel, v2.t[ti]))
                tri_surfaces[ti].push_back({z, (int)f});
    }
    std::map<int, std::vector<double>> bare_z;
    for (size_t ti = 0; ti < v2.t.size(); ++ti)
        for (const Surface& s : tri_surfaces[ti])
            for (int k : v2.t[ti])
                if (levels[(size_t)k].empty())
                    bare_z[k].push_back(s.z + shifts[(size_t)s.frame][(size_t)k]);
    for (auto& kv : bare_z) {
        std::sort(kv.second.begin(), kv.second.end());
        E.bare[kv.first] = storeys_of(kv.second);
    }
    UMesh out;
    std::set<Tri3> seen;
    for (size_t ti = 0; ti < v2.t.size(); ++ti) {
        const Tri3& tri = v2.t[ti];
        for (const Surface& s : tri_surfaces[ti]) {
            const auto& sh = shifts[(size_t)s.frame];
            int slot[3];
            for (int c = 0; c < 3; ++c)
                slot[c] = E.slot_of(tri[(size_t)c], s.z + sh[(size_t)tri[(size_t)c]]);
            const double fallback = s.z + sh[(size_t)tri[0]];
            const int ia = E.vertex(tri[0], slot[0], fallback);
            const int ib = E.vertex(tri[1], slot[1], fallback);
            const int ic = E.vertex(tri[2], slot[2], fallback);
            if (ia == ib || ib == ic || ia == ic) continue;
            Tri3 key{ia, ib, ic};
            std::sort(key.begin(), key.end());
            if (seen.count(key)) continue;
            const Vert &pa = E.verts[(size_t)ia], &pb = E.verts[(size_t)ib], &pc = E.verts[(size_t)ic];
            const double area2 = std::fabs((pb.x - pa.x) * (pc.y - pa.y) - (pb.y - pa.y) * (pc.x - pa.x));
            if (area2 * 0.5 < P().MIN_XY_FOOTPRINT) continue;
            seen.insert(key);
            out.t.push_back({ia, ib, ic});
        }
    }
    out.v = std::move(E.verts);
    return out;
}

double target_edge(const Group& g, const Land* land) {
    return land_of(g, land) ? P().LAND_TRI_TARGET_EDGE : P().TRI_TARGET_EDGE;
}

// _mesh_one_part.
UMesh mesh_one_part(Ctx& C, const GEOSGeometry* part, const Group& group,
                    const std::vector<double>& steep) {
    std::vector<DoorEdge> fixed;
    for (size_t ei = 0; ei < C.A.doors.size(); ++ei) {
        const DoorEdge& e = C.A.doors[ei];
        if (C.claimed_ids.count(ei) || !door_edge_on_part(e, part)) continue;
        if (!wedge_fits_part(e, part) || !storey_holds_door(e, group, C.A.land)) continue;
        C.claimed_ids.insert(ei);
        fixed.push_back(e);
    }
    const Land* land = land_of(group, C.A.land);
    const double target = target_edge(group, C.A.land);
    const Plan2 v2 = triangulate(part, target, fixed, steep, land,
                                 P().LAND_MAX_EDGE_RATIO * target);
    if (v2.t.empty()) return UMesh();
    const FlatStrips F = flat_strips(group, C.A.land);
    const Land none;
    Levels levels = levels_at_points(F.strips, F.poly.data(), F.prof.data(),
                                     land ? *land : none, v2.xy.data(), v2.xy.size() / 2,
                                     P().SAME_SURFACE_Z);
    apply_door_apex_levels(v2, levels, fixed);
    return emit_surfaces(v2, std::move(levels), land);
}

// _mesh_sheets: every sheet's parts meshed into one soup, with each vertex's
// emission (its part's first vertex index) in `src`.
UMesh mesh_sheets(Ctx& C, const std::vector<Group>& sheets, std::vector<int>& src) {
    UMesh out;
    for (size_t gi = 0; gi < sheets.size(); ++gi) {
        Geom g = sheet_coverage(C, sheets[gi], (int)gi);
        if (!g) continue;
        g = close_land_slits(std::move(g), sheets[gi], C.A);
        auto shared = std::make_shared<Geom>(std::move(g));
        C.claimed.push_back({shared, sheets[gi]});
        Group group = sheets[gi];
        auto js = C.junction_strips.find((int)gi);
        if (js != C.junction_strips.end())
            group.insert(group.end(), js->second.begin(), js->second.end());
        const std::vector<double> steep = steep_seeds(group, target_edge(group, C.A.land));
        for (const GEOSGeometry* part : parts_or_self(shared->get())) {
            if (type_id(part) != kPolygon || area(part) < 1.0) continue;
            const UMesh m = mesh_one_part(C, part, group, steep);
            const int base = (int)out.v.size();
            out.v.insert(out.v.end(), m.v.begin(), m.v.end());
            src.insert(src.end(), m.v.size(), base);
            for (const Tri3& t : m.t) out.t.push_back({t[0] + base, t[1] + base, t[2] + base});
        }
    }
    return out;
}

}  // namespace

UMesh build_union_mesh(UnionArgs& A) {
    if (A.strips.empty()) return UMesh();
    if (!has_coverage(A.strips, A)) return UMesh();
    const std::vector<Group> sheets = split_plan_overlaps(storey_groups(A.strips), A.land);
    std::map<int, P2> node_pts;
    std::map<int, double> node_half;
    node_geometry(A.strips, node_pts, node_half);
    Ctx C{A, {}, {}, {}, {}, {}};
    junction_transfers(C, sheets, node_pts, node_half);
    std::vector<int> src;
    UMesh M = mesh_sheets(C, sheets, src);
    weld_sheets(M, src);
    M.t = split_t_junctions(M.v, M.t);
    std::vector<std::pair<int, P2>> nodes(node_pts.begin(), node_pts.end());
    std::vector<double> halves;
    for (const auto& kv : nodes) halves.push_back(node_half[kv.first]);
    merge_at_pathgrid_nodes(M, nodes, halves);
    M.t = destack(M.v, M.t);
    M.t = stitch_shared_nodes(M.v, M.t);
    M.t = split_t_junctions(M.v, M.t);
    M.t = drop_walls(M.v, M.t, A.strips, A.land);
    M.t = split_t_junctions(M.v, M.t);
    if (A.probe_only) return M;
    M.t = fill_boundary_notches(M.v, M.t, A.strips);
    M.t = drop_point_attached(M.t);
    return M;
}

}  // namespace navgrow

using namespace navgrow;

namespace {

// Strips from union records (S, 22): ei, ej, na, nb, a, b, wx, wy, half, land,
// poly_off, poly_n, prof_off, prof_n, with the outline / profile rows they index.
bool read_union_strips(const BufView& sv, const BufView& pv, const BufView& fv,
                       std::deque<UStrip>& store) {
    const double* r = sv.data<double>();
    const size_t np = pv.count() / 2, nf = fv.count() / 3;
    for (size_t i = 0; i < sv.count() / 22; ++i, r += 22) {
        UStrip s;
        s.ei = (int)r[0];
        s.ej = (int)r[1];
        for (int k = 0; k < 3; ++k) {
            s.na[(size_t)k] = r[2 + k]; s.nb[(size_t)k] = r[5 + k];
            s.a[(size_t)k] = r[8 + k];  s.b[(size_t)k] = r[11 + k];
        }
        s.wx = r[14]; s.wy = r[15]; s.half = r[16]; s.land = r[17] > 0.0;
        const size_t po = (size_t)r[18], pn = (size_t)r[19], fo = (size_t)r[20], fn = (size_t)r[21];
        if (po + pn > np || fo + fn > nf) {
            PyErr_SetString(PyExc_ValueError, "strip outline or profile out of range");
            return false;
        }
        s.has_poly = pn > 0;
        for (size_t k = 0; k < pn; ++k)
            s.poly.push_back({pv.data<double>()[2 * (po + k)], pv.data<double>()[2 * (po + k) + 1]});
        for (size_t k = 0; k < fn; ++k) {
            const double* q = &fv.data<double>()[3 * (fo + k)];
            s.prof.push_back({q[0], q[1], q[2]});
        }
        store.push_back(std::move(s));
    }
    return true;
}

// Door edges from (D, 8) rows: b0, b1, apex (NaN = none), has_apex, z (NaN = none).
std::vector<DoorEdge> read_doors(const BufView& dv) {
    std::vector<DoorEdge> out;
    const double* r = dv.data<double>();
    for (size_t i = 0; i < dv.count() / 8; ++i, r += 8) {
        DoorEdge e;
        e.b0 = {r[0], r[1]};
        e.b1 = {r[2], r[3]};
        e.apex = {r[4], r[5]};
        e.has_apex = r[6] > 0.0;
        e.has_z = !std::isnan(r[7]);
        e.z = r[7];
        out.push_back(e);
    }
    return out;
}

}  // namespace

// union_mesh(strips, poly, prof, n_corridors, doors, cell_bounds, probe_only,
//            land, land_ox, land_oy, blocking) -> (verts, tris)
//
// corridor_union.build_union_mesh over union strip records (the corridors,
// then the extra door strips).  land: the cell's (33, 33) LAND or None;
// blocking: the (N, 3, 3) wall soup the land-slit test probes, or None to skip
// the slit closing (the probe build).
PyObject* py_union_mesh(PyObject*, PyObject* args) {
    PyObject *o_s, *o_p, *o_f, *o_d, *o_bounds, *o_land, *o_block;
    Py_ssize_t n_corr;
    int probe_only;
    Land land;
    if (!PyArg_ParseTuple(args, "OOOnOOpOddO", &o_s, &o_p, &o_f, &n_corr, &o_d, &o_bounds,
                          &probe_only, &o_land, &land.ox, &land.oy, &o_block))
        return nullptr;
    if (!geos()) return nullptr;
    BufView sv(o_s, 'd', 8), pv(o_p, 'd', 8), fv(o_f, 'd', 8), dv(o_d, 'd', 8);
    BufView lv(o_land, 'd', 8), bv(o_block, 'd', 8);
    if (!sv.ok || !pv.ok || !fv.ok || !dv.ok || !lv.ok || !bv.ok) return nullptr;
    if (o_land != Py_None && lv.count() != 33 * 33) {
        PyErr_SetString(PyExc_ValueError, "land must be 33x33");
        return nullptr;
    }
    land.g = o_land != Py_None ? lv.data<double>() : nullptr;
    std::deque<UStrip> store;
    UnionArgs A;
    A.store = &store;
    A.probe_only = probe_only != 0;
    A.land = land.g ? &land : nullptr;
    if (!read_union_strips(sv, pv, fv, store) || !read_bounds(o_bounds, A.bounds, &A.has_bounds))
        return nullptr;
    A.doors = read_doors(dv);
    for (const UStrip& s : store) A.strips.push_back(&s);
    CellGeo geo;
    if (o_block != Py_None && A.land) {
        geo.build(nullptr, 0, bv.data<double>(), bv.count() / 9);
        A.geo = &geo;
    }
    UMesh M;
    std::string err;
    Py_BEGIN_ALLOW_THREADS
    try {
        if (n_corr > 0) M = build_union_mesh(A);
    } catch (const std::exception& e) {
        err = e.what();
    }
    Py_END_ALLOW_THREADS
    if (!err.empty()) {
        PyErr_SetString(PyExc_ValueError, ("union_mesh: " + err).c_str());
        return nullptr;
    }
    PyObject* v = verts_to_python(M.v);
    PyObject* t = v ? tris_to_python(M.t) : nullptr;
    if (!t) { Py_XDECREF(v); return nullptr; }
    return Py_BuildValue("(NN)", v, t);
}
