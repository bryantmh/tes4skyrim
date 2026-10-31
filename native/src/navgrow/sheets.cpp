// Native height-gap measurement for union_sheets (the plan-overlap split).
//
// _partition_by_agreement intersects every pair of overlapping ribbon polygons
// and samples both ribbons' heights over the shared ground
// (_overlap_height_gap).  On a dense cell that is ~16k GEOS intersections each
// wrapped in a dozen shapely calls, and the wrapping cost as much as GEOS.
// pair_gaps runs the same GEOS calls on the same geometries in one native call
// and mirrors the Python sampling point for point.

#include "clip_area.h"
#include "geos_api.h"
#include "pybuf.h"
#include "strip.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <string>
#include <vector>

namespace navgrow {

namespace {

// Raised when a GEOS call the Python reference does NOT guard fails, so the
// native path fails where the Python one would.
struct GeosFailure {
    std::string what;
};

// The point's (x, y), or false when it is empty or GEOS cannot read it.
bool point_xy(GeosApi* G, const GEOSGeometry* p, double* x, double* y) {
    if (!p || G->is_empty(G->ctx, p) != 0) return false;
    return G->get_x(G->ctx, p, x) == 1 && G->get_y(G->ctx, p, y) == 1;
}

// The 3x3 bounds-grid samples inside `inter`, appended to pts; nothing when any
// GEOS call fails (the Python reference wraps the whole grid in one try).
void grid_samples(GeosApi* G, const GEOSGeometry* inter,
                  std::vector<double>& pts) {
    double minx, miny, maxx, maxy;
    if (G->xmin(G->ctx, inter, &minx) != 1 || G->ymin(G->ctx, inter, &miny) != 1
        || G->xmax(G->ctx, inter, &maxx) != 1
        || G->ymax(G->ctx, inter, &maxy) != 1)
        return;
    static const double fr[3] = {0.25, 0.5, 0.75};
    std::vector<double> hits;
    for (double fx : fr) {
        for (double fy : fr) {
            const double gx = minx + (maxx - minx) * fx;
            const double gy = miny + (maxy - miny) * fy;
            GeosOwned pt(G, G->point_xy(G->ctx, gx, gy));
            if (!pt.g) return;
            const char hit = G->intersects(G->ctx, inter, pt.g);
            if (hit == 2) return;
            if (hit == 1) { hits.push_back(gx); hits.push_back(gy); }
        }
    }
    pts.insert(pts.end(), hits.begin(), hits.end());
}

// union_sheets._overlap_height_gap for strips A and B over `inter`.
double overlap_height_gap(GeosApi* G, const Strip& A, const Strip& B,
                          const double* fp, const Land& land,
                          const GEOSGeometry* inter) {
    std::vector<double> pts;
    double x, y;
    {
        GeosOwned c(G, G->centroid(G->ctx, inter));
        if (point_xy(G, c.g, &x, &y)) { pts.push_back(x); pts.push_back(y); }
    }
    {
        GeosOwned r(G, G->point_on_surface(G->ctx, inter));
        if (point_xy(G, r.g, &x, &y)) { pts.push_back(x); pts.push_back(y); }
    }
    grid_samples(G, inter, pts);
    if (pts.empty()) return std::numeric_limits<double>::infinity();
    double best = 0.0;
    for (size_t k = 0; k < pts.size(); k += 2) {
        const double d = std::fabs(
            strip_height<true>(A, fp, land, pts[k], pts[k + 1]) -
            strip_height<true>(B, fp, land, pts[k], pts[k + 1]));
        if (k == 0 || d < best) best = d;
    }
    return best;
}

// The gap for one pair, or NaN when the overlap is empty or under 1 unit^2.
double pair_gap(GeosApi* G, const GEOSGeometry* ga, const GEOSGeometry* gb,
                const Strip& A, const Strip& B, const double* fp,
                const Land& land) {
    GeosOwned inter(G, G->intersection(G->ctx, ga, gb));
    if (!inter.g) throw GeosFailure{"intersection: " + G->last_error};
    const char empty = G->is_empty(G->ctx, inter.g);
    if (empty == 2) throw GeosFailure{"is_empty: " + G->last_error};
    if (empty) return std::numeric_limits<double>::quiet_NaN();
    double area;
    if (G->area(G->ctx, inter.g, &area) != 1)
        throw GeosFailure{"area: " + G->last_error};
    if (area < 1.0) return std::numeric_limits<double>::quiet_NaN();
    return overlap_height_gap(G, A, B, fp, land, inter.g);
}

// A ribbon's outline (when it is one simple ring) and the span of every
// height strip_height can give over it: its profile or chord, or, on the LAND,
// the grid heights under its box (an interpolated LAND height never leaves
// its quad's corners).  `land_only` marks a ribbon whose height IS Land::z.
struct Ribbon {
    std::vector<double> shell;
    bool simple = false;
    bool land_only = false;
    double zlo = 0.0, zhi = 0.0;
};

// The LAND grid's height range over the vertices whose quads cover x0..x1,
// y0..y1 (Land::z's clamp to the rim included).
void land_range(const Land& land, double x0, double x1, double y0, double y1,
                double* lo, double* hi) {
    auto idx = [](double f) {
        return std::min(32, std::max(0, (int)std::floor(std::min(32.0, std::max(0.0, f)))));
    };
    const int ix0 = std::min(31, idx((x0 - land.ox) / 128.0));
    const int ix1 = std::min(32, idx((x1 - land.ox) / 128.0) + 1);
    const int iy0 = std::min(31, idx((y0 - land.oy) / 128.0));
    const int iy1 = std::min(32, idx((y1 - land.oy) / 128.0) + 1);
    *lo = std::numeric_limits<double>::infinity();
    *hi = -*lo;
    for (int iy = iy0; iy <= iy1; ++iy)
        for (int ix = ix0; ix <= ix1; ++ix) {
            *lo = std::min(*lo, land.g[iy * 33 + ix]);
            *hi = std::max(*hi, land.g[iy * 33 + ix]);
        }
}

Ribbon ribbon_of(GeosApi* G, const GEOSGeometry* g, const Strip& S,
                 const double* fp, const Land& land) {
    Ribbon r;
    r.simple = polygon_shell(G, g, r.shell);
    if (S.on_land && S.prof_n < 2) {
        r.land_only = true;
        r.zlo = -std::numeric_limits<double>::infinity();
        r.zhi = std::numeric_limits<double>::infinity();
        if (!r.simple) return r;
        double x0 = r.shell[0], x1 = x0, y0 = r.shell[1], y1 = y0;
        for (size_t k = 1; k < r.shell.size() / 2; ++k) {
            x0 = std::min(x0, r.shell[2 * k]); x1 = std::max(x1, r.shell[2 * k]);
            y0 = std::min(y0, r.shell[2 * k + 1]);
            y1 = std::max(y1, r.shell[2 * k + 1]);
        }
        land_range(land, x0, x1, y0, y1, &r.zlo, &r.zhi);
    } else if (S.prof_n >= 2) {
        r.zlo = r.zhi = fp[(size_t)S.prof_off * 3 + 2];
        for (int k = 1; k < S.prof_n; ++k) {
            const double z = fp[(size_t)(S.prof_off + k) * 3 + 2];
            r.zlo = std::min(r.zlo, z);
            r.zhi = std::max(r.zhi, z);
        }
    } else {
        r.zlo = std::min(S.az, S.bz);
        r.zhi = std::max(S.az, S.bz);
    }
    return r;
}

// pair_gap's verdict without GEOS for two ribbons spanning under `thr` in
// height together: NaN (no ground) or the span (a gap under `thr`; exactly 0
// for two ribbons that both read Land::z).  False when the span is too wide or
// the clipped area is too near 1 unit^2 to trust.
bool same_floor_gap(const Ribbon& A, const Ribbon& B, double thr,
                    double* gap) {
    const double span = A.land_only && B.land_only
        ? 0.0 : std::max(A.zhi, B.zhi) - std::min(A.zlo, B.zlo);
    if (!A.simple || !B.simple || !(span <= thr - kClipMargin)) return false;
    const double area = overlap_area(A.shell.data(), A.shell.size() / 2,
                                     B.shell.data(), B.shell.size() / 2);
    if (area < 1.0 - kClipMargin) {
        *gap = std::numeric_limits<double>::quiet_NaN();
        return true;
    }
    if (area > 1.0 + kClipMargin) {
        *gap = span;
        return true;
    }
    return false;
}

// True when no part of A x B can be a same-surface piece: their overlap is
// clearly under 1 unit^2, or their heights are clearly more than `tol` apart
// everywhere.  Either way the Python loop skips the pair.
bool never_same_surface(const Ribbon& A, const Ribbon& B, double tol) {
    const double sep = std::max(A.zlo - B.zhi, B.zlo - A.zhi);
    if (sep > tol + kClipMargin) return true;
    return A.simple && B.simple
        && overlap_area(A.shell.data(), A.shell.size() / 2, B.shell.data(),
                        B.shell.size() / 2) < 1.0 - kClipMargin;
}

// The GEOS geometries behind a sequence of shapely objects; false with a
// Python error set when it is not one, or its length is not `n`.
bool read_geoms(GeosApi* G, PyObject* o, size_t n,
                std::vector<GEOSGeometry*>& out) {
    PyObject* seq = PySequence_Fast(o, "expected a sequence of geometries");
    if (!seq) return false;
    bool ok = (size_t)PySequence_Fast_GET_SIZE(seq) == n;
    out.assign(n, nullptr);
    for (size_t i = 0; i < n && ok; ++i)
        ok = G->shapely_geom(PySequence_Fast_GET_ITEM(seq, i), &out[i])
             && out[i];
    Py_DECREF(seq);
    if (!ok) PyErr_SetString(PyExc_TypeError, "one shapely geometry per strip");
    return ok;
}

// A GEOS predicate's verdict, raising GeosFailure on its exception value.
bool verdict(GeosApi* G, char v, const char* what) {
    if (v == 2) throw GeosFailure{std::string(what) + ": " + G->last_error};
    return v == 1;
}

// `(pa & pb) & shared`, or nullptr when GEOS fails (the Python caller skips).
GEOSGeometry* triple_cut(GeosApi* G, const GEOSGeometry* pa,
                         const GEOSGeometry* pb, const GEOSGeometry* shared) {
    GeosOwned ab(G, G->intersection(G->ctx, pa, pb));
    if (!ab.g) return nullptr;
    return G->intersection(G->ctx, ab.g, shared);
}

// union_sheets._same_surface_region's loop: the pieces of `shared` where an
// A ribbon and a B ribbon describe the same surface, in the Python order.
// Each piece is appended to `out` as a new shapely geometry.
bool same_surface_pieces(GeosApi* G, const std::vector<GEOSGeometry*>& pa,
                         const std::vector<GEOSGeometry*>& pb,
                         const GEOSGeometry* shared,
                         const std::vector<Strip>& strips, const double* fp,
                         const Land& land, double tol, PyObject* out) {
    const size_t na = pa.size();
    std::vector<Ribbon> rib(strips.size());
    for (size_t i = 0; i < strips.size(); ++i)
        rib[i] = ribbon_of(G, i < na ? pa[i] : pb[i - na], strips[i], fp, land);
    for (size_t a = 0; a < na; ++a) {
        if (verdict(G, G->is_empty(G->ctx, pa[a]), "is_empty")
            || !verdict(G, G->intersects(G->ctx, pa[a], shared), "intersects"))
            continue;
        const GEOSPreparedGeometry* prep = G->prepare(G->ctx, pa[a]);
        if (!prep) throw GeosFailure{"prepare: " + G->last_error};
        std::vector<size_t> hits;
        try {
            for (size_t b = 0; b < pb.size(); ++b)
                if (!verdict(G, G->is_empty(G->ctx, pb[b]), "is_empty")
                    && verdict(G, G->prepared_intersects(G->ctx, prep, pb[b]),
                               "intersects"))
                    hits.push_back(b);
        } catch (...) {
            G->prepared_destroy(G->ctx, prep);
            throw;
        }
        G->prepared_destroy(G->ctx, prep);
        for (size_t b : hits) {
            if (never_same_surface(rib[a], rib[na + b], tol)) continue;
            GEOSGeometry* piece = triple_cut(G, pa[a], pb[b], shared);
            if (!piece) continue;
            GeosOwned own(G, piece);
            double area = 0.0;
            if (G->is_empty(G->ctx, piece) != 0
                || G->area(G->ctx, piece, &area) != 1 || area < 1.0)
                continue;
            if (!(overlap_height_gap(G, strips[a], strips[na + b], fp, land,
                                     piece) <= tol))
                continue;
            PyObject* g = G->shapely_wrap(own.g, G->ctx);
            if (!g) return false;
            own.g = nullptr;
            const int rc = PyList_Append(out, g);
            Py_DECREF(g);
            if (rc < 0) return false;
        }
    }
    return true;
}

}  // namespace

}  // namespace navgrow

using namespace navgrow;

// same_surface_pieces(a_polys, b_polys, shared, strips, poly, prof, land,
//                     land_ox, land_oy, tol) -> [piece, ...]
//
// a_polys / b_polys: the two sheets' ribbon polygons; strips/poly/prof:
// union_geom.flat_strips over the A strips then the B strips.  Returns, as
// shapely geometries, each `(pa & pb) & shared` over 1 unit^2 whose height gap
// is within `tol` (union_sheets._same_surface_region before its union).
PyObject* py_same_surface_pieces(PyObject*, PyObject* args) {
    PyObject *o_pa, *o_pb, *o_shared, *o_strips, *o_poly, *o_prof, *o_land;
    Land land;
    double tol;
    if (!PyArg_ParseTuple(args, "OOOOOOOddd", &o_pa, &o_pb, &o_shared,
                          &o_strips, &o_poly, &o_prof, &o_land, &land.ox,
                          &land.oy, &tol))
        return nullptr;
    GeosApi* G = geos();
    if (!G) return nullptr;
    BufView sv(o_strips, 'd', 8), pv(o_poly, 'd', 8), fv(o_prof, 'd', 8);
    BufView lv(o_land, 'd', 8);
    if (!sv.ok || !pv.ok || !fv.ok || !lv.ok) return nullptr;
    if (o_land != Py_None && lv.count() != 33 * 33) {
        PyErr_SetString(PyExc_ValueError, "land must be 33x33");
        return nullptr;
    }
    land.g = o_land != Py_None ? lv.data<double>() : nullptr;
    const size_t ns = sv.count() / 12;
    const Py_ssize_t na_py = PyObject_Length(o_pa);
    if (na_py < 0) return nullptr;
    const size_t na = (size_t)na_py;
    GEOSGeometry* shared = nullptr;
    std::vector<GEOSGeometry*> pa, pb;
    if (na > ns || !read_geoms(G, o_pa, na, pa) || !read_geoms(G, o_pb, ns - na, pb)
        || !G->shapely_geom(o_shared, &shared) || !shared) {
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_ValueError, "one polygon per strip row");
        return nullptr;
    }
    const std::vector<Strip> strips = load_strips(
        sv.data<double>(), ns, pv.data<double>(), fv.count() / 3, land.g);
    PyObject* out = PyList_New(0);
    if (!out) return nullptr;
    try {
        if (!same_surface_pieces(G, pa, pb, shared, strips, fv.data<double>(),
                                 land, tol, out)) {
            Py_DECREF(out);
            return nullptr;
        }
    } catch (const GeosFailure& f) {
        Py_DECREF(out);
        PyErr_SetString(PyExc_ValueError, ("GEOS " + f.what).c_str());
        return nullptr;
    }
    return out;
}

// candidate_pairs(polys) -> [(a, b), ...]
//
// Every index pair a < b whose polygons intersect, ascending
// (union_sheets._candidate_pairs).  Boxes filter; rings_intersect settles a
// pair of simple rings that is clearly apart or clearly crossing; GEOS's
// predicate settles the rest.
PyObject* py_candidate_pairs(PyObject*, PyObject* args) {
    PyObject* o_polys;
    if (!PyArg_ParseTuple(args, "O", &o_polys)) return nullptr;
    GeosApi* G = geos();
    if (!G) return nullptr;
    const Py_ssize_t n_py = PyObject_Length(o_polys);
    if (n_py < 0) return nullptr;
    const size_t n = (size_t)n_py;
    std::vector<GEOSGeometry*> geoms;
    if (!read_geoms(G, o_polys, n, geoms)) return nullptr;
    std::vector<std::vector<double>> shells(n);
    std::vector<char> simple(n);
    std::vector<std::array<double, 4>> box(n);
    for (size_t i = 0; i < n; ++i) {
        simple[i] = polygon_shell(G, geoms[i], shells[i]);
        auto& b = box[i];
        if (G->is_empty(G->ctx, geoms[i]) != 0
            || G->xmin(G->ctx, geoms[i], &b[0]) != 1
            || G->xmax(G->ctx, geoms[i], &b[1]) != 1
            || G->ymin(G->ctx, geoms[i], &b[2]) != 1
            || G->ymax(G->ctx, geoms[i], &b[3]) != 1)
            b = {1.0, -1.0, 1.0, -1.0};
    }
    std::vector<size_t> order(n);
    for (size_t i = 0; i < n; ++i) order[i] = i;
    std::sort(order.begin(), order.end(),
              [&](size_t a, size_t b) { return box[a][0] < box[b][0]; });
    std::vector<std::pair<int, int>> pairs;
    for (size_t oi = 0; oi < n; ++oi) {
        const size_t a = order[oi];
        if (box[a][0] > box[a][1]) continue;
        for (size_t oj = oi + 1; oj < n && box[order[oj]][0] <= box[a][1]; ++oj) {
            const size_t b = order[oj];
            if (box[b][0] > box[b][1] || box[b][2] > box[a][3]
                || box[b][3] < box[a][2])
                continue;
            const int fast = simple[a] && simple[b]
                ? rings_intersect(shells[a].data(), shells[a].size() / 2,
                                  shells[b].data(), shells[b].size() / 2)
                : -1;
            bool hit = fast == 1;
            if (fast < 0) {
                const char v = G->intersects(G->ctx, geoms[a], geoms[b]);
                if (v == 2) {
                    PyErr_SetString(PyExc_ValueError,
                                    ("GEOS intersects: " + G->last_error).c_str());
                    return nullptr;
                }
                hit = v == 1;
            }
            if (hit) pairs.push_back({(int)std::min(a, b), (int)std::max(a, b)});
        }
    }
    std::sort(pairs.begin(), pairs.end());
    PyObject* out = PyList_New((Py_ssize_t)pairs.size());
    if (!out) return nullptr;
    for (size_t k = 0; k < pairs.size(); ++k)
        PyList_SET_ITEM(out, (Py_ssize_t)k,
                        Py_BuildValue("(ii)", pairs[k].first, pairs[k].second));
    return out;
}

// pair_gaps(polys, strips, poly, prof, pairs, land, land_ox, land_oy, thr)
//     -> list
//
// polys: the ribbon polygons (shapely), one per strip row.  strips/poly/prof:
// union_geom.flat_strips' arrays.  pairs: (P, 2) float64 strip indices.  land:
// (33, 33) float64 or None.  Returns one entry per pair: None when the two
// polygons share no ground (empty, or under 1 unit^2), else the smallest
// height disagreement over the shared ground (inf when nothing was sampled).
//
// When both ribbons' heights span less than `thr` together, every gap over
// their overlap is under `thr`, so only the 1 unit^2 test matters: a clipped
// area decides it (clip_area.h) and the gap returned is that span, on the same
// side of `thr` as the exact value.  The caller compares gaps only to `thr`.
PyObject* py_pair_gaps(PyObject*, PyObject* args) {
    PyObject *o_polys, *o_strips, *o_poly, *o_prof, *o_pairs, *o_land;
    Land land;
    double thr;
    if (!PyArg_ParseTuple(args, "OOOOOOddd", &o_polys, &o_strips, &o_poly,
                          &o_prof, &o_pairs, &o_land, &land.ox, &land.oy, &thr))
        return nullptr;
    GeosApi* G = geos();
    if (!G) return nullptr;
    BufView sv(o_strips, 'd', 8), pv(o_poly, 'd', 8), fv(o_prof, 'd', 8);
    BufView qv(o_pairs, 'd', 8), lv(o_land, 'd', 8);
    if (!sv.ok || !pv.ok || !fv.ok || !qv.ok || !lv.ok) return nullptr;
    if (o_land != Py_None && lv.count() != 33 * 33) {
        PyErr_SetString(PyExc_ValueError, "land must be 33x33");
        return nullptr;
    }
    land.g = o_land != Py_None ? lv.data<double>() : nullptr;
    const size_t ns = sv.count() / 12;
    std::vector<GEOSGeometry*> geoms;
    if (!read_geoms(G, o_polys, ns, geoms)) return nullptr;
    const std::vector<Strip> strips = load_strips(
        sv.data<double>(), ns, pv.data<double>(), fv.count() / 3, land.g);
    const double* fp = fv.data<double>();
    std::vector<Ribbon> ribbons(ns);
    for (size_t i = 0; i < ns; ++i)
        ribbons[i] = ribbon_of(G, geoms[i], strips[i], fp, land);
    const double* qp = qv.data<double>();
    const size_t np = qv.count() / 2;
    std::vector<double> gaps(np);
    std::string err;
    for (size_t k = 0; k < np && err.empty(); ++k) {
        const size_t a = (size_t)qp[k * 2], b = (size_t)qp[k * 2 + 1];
        if (a >= ns || b >= ns) { err = "pair index out of range"; break; }
        if (same_floor_gap(ribbons[a], ribbons[b], thr, &gaps[k])) continue;
        try {
            gaps[k] = pair_gap(G, geoms[a], geoms[b], strips[a], strips[b],
                               fp, land);
        } catch (const GeosFailure& f) {
            err = "GEOS " + f.what;
        }
    }
    if (!err.empty()) {
        PyErr_SetString(PyExc_ValueError, err.c_str());
        return nullptr;
    }
    PyObject* out = PyList_New((Py_ssize_t)np);
    if (!out) return nullptr;
    for (size_t k = 0; k < np; ++k) {
        PyObject* v;
        if (std::isnan(gaps[k])) { v = Py_None; Py_INCREF(v); }
        else v = PyFloat_FromDouble(gaps[k]);
        if (!v) { Py_DECREF(out); return nullptr; }
        PyList_SET_ITEM(out, (Py_ssize_t)k, v);
    }
    return out;
}
