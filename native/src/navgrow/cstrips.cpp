// corridor._build_corridor_strips: one ribbon per pathgrid edge (grown to the
// walls, stair edges following their treads) plus a disc at every node.
// Mirrors the Python statement for statement; the march itself is grow.cpp's.

#include "cell.h"
#include "params.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace navgrow {

namespace {

const double kInf = std::numeric_limits<double>::infinity();
const double kNodeSnapRadius0 = 8.0;   // corridor.NODE_SNAP_RADII[0]

struct Frame { P3 a, b; double ux, uy, length; };

// _edge_frame.
bool edge_frame(const CellCtx& C, int i, int j, Frame* f) {
    const int n = (int)C.nodes.size();
    if (i >= n || j >= n || i == j) return false;
    const double ax = C.nodes[(size_t)i][0], ay = C.nodes[(size_t)i][1];
    const double bx = C.nodes[(size_t)j][0], by = C.nodes[(size_t)j][1];
    const double length = py_hypot(bx - ax, by - ay);
    if (length < 1e-4) return false;
    *f = {{ax, ay, C.node_z[(size_t)i]}, {bx, by, C.node_z[(size_t)j]},
          (bx - ax) / length, (by - ay) / length, length};
    return true;
}

using Degree = std::map<int, int>;

int degree_of(const Degree& d, int i) {
    auto it = d.find(i);
    return it == d.end() ? 0 : it->second;
}

// _extended_ends.
void extended_ends(const Frame& f, const Degree& degree, int i, int j, P3* pa, P3* pb) {
    const double ext = P().RIBBON_END_EXTEND;
    const double dz = f.b[2] - f.a[2];
    const double ea = degree_of(degree, i) <= 1 ? ext : 0.0;
    const double eb = degree_of(degree, j) <= 1 ? ext : 0.0;
    *pa = {f.a[0] - f.ux * ea, f.a[1] - f.uy * ea, f.a[2] - dz * (ea / f.length)};
    *pb = {f.b[0] + f.ux * eb, f.b[1] + f.uy * eb, f.b[2] + dz * (eb / f.length)};
}

bool is_steep(const P3& pa, const P3& pb) {
    const double run = py_hypot(pb[0] - pa[0], pb[1] - pa[1]);
    return std::fabs(pb[2] - pa[2]) / std::max(run, 1e-6) > P().RIBBON_GROW_MAX_SLOPE;
}

double clamp01(double t) { return std::max(0.0, std::min(1.0, t)); }

// _prof_at: (height, |slope|) of a profile at (x, y).
std::pair<double, double> prof_at(const std::vector<P3>& prof, const P3& pa, const P3& pb,
                                  double x, double y) {
    const double dx = pb[0] - pa[0], dy = pb[1] - pa[1];
    const double d2 = dx * dx + dy * dy;
    const double t = d2 < 1e-9 ? 0.0 : clamp01(((x - pa[0]) * dx + (y - pa[1]) * dy) / d2);
    const double f = t * (double)(prof.size() - 1);
    const long long k = std::min((long long)prof.size() - 2, std::max(0LL, (long long)f));
    const P3& q0 = prof[(size_t)k];
    const P3& q1 = prof[(size_t)k + 1];
    double run = py_dist(q0[0], q0[1], q1[0], q1[1]);
    if (run == 0.0) run = 1.0;
    return {q0[2] + (q1[2] - q0[2]) * (f - (double)k), std::fabs(q1[2] - q0[2]) / run};
}

bool stands_on_land(const CellCtx& C, int i, int j) {
    if (!C.land) return false;
    for (int n : {i, j}) {
        const P3& p = C.nodes[(size_t)n];
        if (C.land->covers(p[0], p[1])
            && !(std::fabs(C.node_z[(size_t)n] - C.land->z(p[0], p[1])) <= kNodeSnapRadius0))
            return false;
    }
    return true;
}

// --------------------------------------------------------------------------
// Stair profiles (_surface_profile and its helpers)
// --------------------------------------------------------------------------

struct Station { double x, y; std::vector<double> zs; };

bool leaves_chord(const CellCtx& C, const P3& pa, const P3& pb) {
    if (!C.has_sample()) return false;
    const long long n = std::max(2LL, (long long)py_floordiv(
        py_hypot(pb[0] - pa[0], pb[1] - pa[1]), 32.0));
    const double lo = std::min(pa[2], pb[2]) - P().MAX_CLIMB;
    const double hi = std::max(pa[2], pb[2]) + P().MAX_CLIMB;
    for (long long s = 1; s < n; ++s) {
        double q[3];
        for (int k = 0; k < 3; ++k) q[k] = pa[(size_t)k] + (pb[(size_t)k] - pa[(size_t)k]) * (double)s / (double)n;
        bool any = false;
        double best = 0.0;
        for (double c : C.layers(q[0], q[1])) {
            if (!(lo <= c && c <= hi)) continue;
            const double d = std::fabs(c - q[2]);
            if (!any || d < best) { any = true; best = d; }
        }
        if (any && best > P().MAX_CLIMB) return true;
    }
    return false;
}

std::vector<Station> profile_stations(const CellCtx& C, const P3& pa, const P3& pb,
                                      long long n, double half) {
    const double ax = pa[0], ay = pa[1], az = pa[2], bx = pb[0], by = pb[1], bz = pb[2];
    double run = py_hypot(bx - ax, by - ay);
    if (run == 0.0) run = 1.0;
    const double wx = -(by - ay) / run, wy = (bx - ax) / run;
    const double lo = std::min(az, bz) - P().MAX_CLIMB, hi = std::max(az, bz) + P().MAX_CLIMB;
    std::vector<Station> out;
    for (long long s = 0; s <= n; ++s) {
        const double t = (double)s / (double)n;
        const double x = ax + (bx - ax) * t, y = ay + (by - ay) * t;
        std::vector<double> cand;
        for (double off : {0.0, 0.5 * half, -0.5 * half, half, -half})
            for (double z : C.layers(x + wx * off, y + wy * off))
                if (lo <= z && z <= hi) cand.push_back(np_round1(z));
        std::sort(cand.begin(), cand.end());
        cand.erase(std::unique(cand.begin(), cand.end()), cand.end());
        if (cand.empty()) cand.push_back(az + (bz - az) * t);
        out.push_back({x, y, cand});
    }
    return out;
}

bool clamped_chord(const std::vector<Station>& st, double az, double bz, long long n,
                   std::vector<P3>* out) {
    const double step = P().MAX_CLIMB;
    auto holds = [&](long long s, double z) {
        for (double c : st[(size_t)s].zs)
            if (std::fabs(c - z) <= step) return true;
        return false;
    };
    long long ka = 0;
    while (ka + 1 <= n && holds(ka + 1, az)) ++ka;
    long long kb = n;
    while (kb - 1 >= 0 && holds(kb - 1, bz)) --kb;
    if (ka >= kb) return false;
    for (long long s = 0; s <= n; ++s) {
        const double f = clamp01((double)(s - ka) / (double)(kb - ka));
        out->push_back({st[(size_t)s].x, st[(size_t)s].y, az + (bz - az) * f});
    }
    return true;
}

// _surface_profile; false for None.
bool surface_profile(const CellCtx& C, const P3& pa, const P3& pb, std::vector<P3>* prof) {
    if (!C.has_sample()) return false;
    const double az = pa[2], bz = pb[2];
    const double run = py_hypot(pb[0] - pa[0], pb[1] - pa[1]);
    if (run < 32.0) return false;
    const long long n = std::max(2LL, (long long)py_floordiv(run, 16.0));
    const std::vector<Station> st = profile_stations(C, pa, pb, n, P().RIBBON_STAIR_HALF_WIDTH);
    const double step = P().MAX_CLIMB;
    std::vector<std::vector<double>> costs(1);
    std::vector<std::vector<size_t>> back;
    for (double z : st[0].zs) costs[0].push_back(std::fabs(z - az) <= step ? std::fabs(z - az) : kInf);
    for (long long s = 1; s <= n; ++s) {
        const auto& cand = st[(size_t)s].zs;
        const auto& prev_z = st[(size_t)s - 1].zs;
        std::vector<double> row(cand.size(), kInf);
        std::vector<size_t> bk(cand.size(), 0);
        for (size_t i = 0; i < cand.size(); ++i)
            for (size_t j = 0; j < prev_z.size(); ++j) {
                const double pc = costs.back()[j];
                if (pc == kInf || std::fabs(cand[i] - prev_z[j]) > step) continue;
                const double c = pc + std::fabs(cand[i] - prev_z[j]);
                if (c < row[i]) { row[i] = c; bk[i] = j; }
            }
        costs.push_back(row);
        back.push_back(bk);
    }
    bool have = false;
    double bd = 0.0, bc = 0.0;
    size_t bi = 0;
    for (size_t i = 0; i < st[(size_t)n].zs.size(); ++i) {
        const double z = st[(size_t)n].zs[i];
        const double c = costs[(size_t)n][i];
        if (c == kInf || std::fabs(z - bz) > step) continue;
        const double d = std::fabs(z - bz);
        if (!have || d < bd || (d == bd && (c < bc || (c == bc && i < bi)))) {
            have = true; bd = d; bc = c; bi = i;
        }
    }
    if (!have) return clamped_chord(st, az, bz, n, prof);
    std::vector<double> zs((size_t)n + 1);
    size_t idx = bi;
    for (long long s = n; s >= 0; --s) {
        zs[(size_t)s] = st[(size_t)s].zs[idx];
        if (s > 0) idx = back[(size_t)s - 1][idx];
    }
    for (long long s = 0; s <= n; ++s) prof->push_back({st[(size_t)s].x, st[(size_t)s].y, zs[(size_t)s]});
    prof->front() = pa;
    prof->back() = pb;
    return true;
}

// --------------------------------------------------------------------------
// March stations (_plan_stations) and the width passes
// --------------------------------------------------------------------------

struct PlanEntry {
    bool disc = false;
    int i = -1, j = -1;                // the edge, or the disc's node in i
    P3 pa{}, pb{};
    double ux = 0, uy = 0, length = 0;
    long long k = 0;
    size_t base = 0;
    double nx = 0, ny = 0, nz = 0;     // disc centre
};

struct Grow {
    std::map<Edge2, std::vector<P3>> profiles;   // a profile per profiled edge
    std::set<Edge2> profiled;                    // edges in `profiles` (incl. None)
    std::set<Edge2> flat;
    std::vector<double> rows;                    // (N, 9)
    std::vector<double> on_land;
    std::vector<PlanEntry> plan;
    std::vector<double> widths;
};

const std::vector<P3>* profile_of(const Grow& G, const Edge2& e) {
    auto it = G.profiles.find(e);
    return it == G.profiles.end() || it->second.empty() ? nullptr : &it->second;
}

void edge_station_rows(Grow& G, const P3& pa, const P3& pb, double ux, double uy,
                       double total, long long k, const std::vector<P3>* prof, int ei,
                       bool steep) {
    const double ramp = P().RIBBON_HALF_WIDTH;
    const double lo0 = P().RIBBON_HALF_WIDTH, lo1 = P().RIBBON_GROW_MIN_HALF;
    const double wx = -uy, wy = ux;
    for (long long s = 0; s <= k; ++s) {
        const double t = (double)s / (double)k;
        const double cxs = pa[0] + (pb[0] - pa[0]) * t;
        const double cys = pa[1] + (pb[1] - pa[1]) * t;
        double czs = pa[2] + (pb[2] - pa[2]) * t;
        bool flight = steep;
        if (prof) {
            const auto zs = prof_at(*prof, pa, pb, cxs, cys);
            czs = zs.first;
            flight = zs.second > P().RIBBON_GROW_MAX_SLOPE;
        }
        const double d_end = std::min(t, 1.0 - t) * total;
        const double frac = ramp > 1e-6 ? std::min(1.0, d_end / ramp) : 1.0;
        const double floor_h = flight ? P().RIBBON_STAIR_HALF_WIDTH : lo0 + (lo1 - lo0) * frac;
        G.rows.insert(G.rows.end(), {cxs, cys, czs, wx, wy, ux, uy, floor_h, (double)ei});
        G.rows.insert(G.rows.end(), {cxs, cys, czs, -wx, -wy, ux, uy, floor_h, (double)ei});
    }
}

void plan_stations(CellCtx& C, Grow& G, const Degree& degree) {
    std::map<Edge2, int> edge_index;
    for (size_t e = 0; e < C.edges.size(); ++e) edge_index[C.edges[e]] = (int)e;
    for (const Edge2& e : C.edges) {
        Frame f;
        if (!edge_frame(C, e.first, e.second, &f)) continue;
        PlanEntry pe;
        pe.i = e.first; pe.j = e.second;
        extended_ends(f, degree, e.first, e.second, &pe.pa, &pe.pb);
        const double total = py_hypot(pe.pb[0] - pe.pa[0], pe.pb[1] - pe.pa[1]);
        pe.k = std::max(1LL, (long long)std::nearbyint(total / P().RIBBON_STEP));
        pe.ux = f.ux; pe.uy = f.uy; pe.length = f.length;
        pe.base = G.rows.size() / 9;
        edge_station_rows(G, pe.pa, pe.pb, f.ux, f.uy, total, pe.k, profile_of(G, e),
                          edge_index[e], !G.flat.count(e) && is_steep(pe.pa, pe.pb));
        G.plan.push_back(pe);
    }
    const int nrays = (int)P().RIBBON_GROW_DISC_RAYS;
    for (const auto& kv : degree) {
        const int ni = kv.first;
        if (ni >= (int)C.nodes.size()) continue;
        PlanEntry pe;
        pe.disc = true;
        pe.i = pe.j = ni;
        pe.nx = C.nodes[(size_t)ni][0]; pe.ny = C.nodes[(size_t)ni][1];
        pe.nz = C.node_z[(size_t)ni];
        pe.base = G.rows.size() / 9;
        for (int kk = 0; kk < nrays; ++kk) {
            const double ang = 2.0 * 3.141592653589793 * kk / nrays;
            const double ddx = crt_cos(ang), ddy = crt_sin(ang);
            G.rows.insert(G.rows.end(), {pe.nx, pe.ny, pe.nz, ddx, ddy, -ddy, ddx, 0.0,
                                         (double)(-1 - ni)});
        }
        G.plan.push_back(pe);
    }
    G.on_land.assign(G.rows.size() / 9, 0.0);
    for (size_t at = 0; at < G.plan.size(); ++at) {
        const PlanEntry& pe = G.plan[at];
        const size_t end = at + 1 < G.plan.size() ? G.plan[at + 1].base : G.rows.size() / 9;
        if (!stands_on_land(C, pe.i, pe.j)) continue;
        for (size_t r = pe.base; r < end; ++r) {
            G.on_land[r] = 1.0;
            G.rows[r * 9 + 2] = C.land->z(G.rows[r * 9], G.rows[r * 9 + 1]);
        }
    }
}

// _bridge_blocked_stations.
void bridge_blocked_stations(Grow& G) {
    std::vector<double>& w = G.widths;
    for (const PlanEntry& pe : G.plan) {
        if (pe.disc) continue;
        const size_t b = pe.base;
        auto open = [&](long long s) { return w[b + 2 * (size_t)s] > 0.0 || w[b + 2 * (size_t)s + 1] > 0.0; };
        std::vector<long long> open_s;
        for (long long s = 0; s <= pe.k; ++s)
            if (open(s)) open_s.push_back(s);
        if (open_s.empty() || (long long)open_s.size() == pe.k + 1) continue;
        for (long long s = 0; s <= pe.k; ++s) {
            if (open(s)) continue;
            long long before = -1, after = -1;
            for (long long o : open_s) {
                if (o < s) before = o;
                if (o > s && after < 0) after = o;
            }
            if (before < 0 || after < 0) {
                const long long src = before < 0 ? after : before;
                w[b + 2 * (size_t)s] = w[b + 2 * (size_t)src];
                w[b + 2 * (size_t)s + 1] = w[b + 2 * (size_t)src + 1];
                continue;
            }
            const double f = (double)(s - before) / (double)(after - before);
            for (size_t side = 0; side < 2; ++side) {
                const double w0 = w[b + 2 * (size_t)before + side];
                const double w1 = w[b + 2 * (size_t)after + side];
                w[b + 2 * (size_t)s + side] = w0 + (w1 - w0) * f;
            }
        }
    }
}

// One run of _erode_far_widths: indices `idx` into the widths.
void erode_run(std::vector<double>& w, const std::vector<size_t>& idx, long long win, bool wrap,
               double cap) {
    const long long n = (long long)idx.size();
    std::vector<double> src(idx.size());
    for (size_t k = 0; k < idx.size(); ++k) src[k] = w[idx[k]];
    for (long long k = 0; k < n; ++k) {
        double m = kInf;
        for (long long d = -win; d <= win; ++d) {
            long long q = k + d;
            if (wrap) q = ((q % n) + n) % n;
            else q = std::max(0LL, std::min(n - 1, q));
            m = std::min(m, src[(size_t)q]);
        }
        w[idx[(size_t)k]] = std::min(src[(size_t)k], std::max(cap, m));
    }
}

void erode_far_widths(Grow& G, double cap) {
    const size_t nrays = (size_t)P().RIBBON_GROW_DISC_RAYS;
    for (const PlanEntry& pe : G.plan) {
        if (pe.disc) {
            std::vector<size_t> idx;
            for (size_t k = 0; k < nrays; ++k) idx.push_back(pe.base + k);
            erode_run(G.widths, idx, 1, true, cap);
            continue;
        }
        const size_t end = pe.base + 2 * (size_t)(pe.k + 1);
        for (size_t side = 0; side < 2; ++side) {
            std::vector<size_t> idx;
            for (size_t r = pe.base + side; r < end; r += 2) idx.push_back(r);
            erode_run(G.widths, idx, (long long)P().FAR_WIDTH_WINDOW, false, cap);
        }
    }
}

// --------------------------------------------------------------------------
// Strips
// --------------------------------------------------------------------------

bool on_flight(const std::vector<P3>* prof, const P3& pa, const P3& pb, double x, double y) {
    if (!prof) return is_steep(pa, pb);
    return prof_at(*prof, pa, pb, x, y).second > P().RIBBON_GROW_MAX_SLOPE;
}

// _grown_outline.
void grown_outline(UStrip& s, const PlanEntry& pe, const std::vector<double>& w,
                   const std::vector<P3>* prof) {
    Ring left, right;
    double max_h = P().RIBBON_HALF_WIDTH;
    for (long long st = 0; st <= pe.k; ++st) {
        const double t = (double)st / (double)pe.k;
        const double cxs = pe.pa[0] + (pe.pb[0] - pe.pa[0]) * t;
        const double cys = pe.pa[1] + (pe.pb[1] - pe.pa[1]) * t;
        double hl = w[pe.base + 2 * (size_t)st], hr = w[pe.base + 2 * (size_t)st + 1];
        if (!s.land && on_flight(prof, pe.pa, pe.pb, cxs, cys))
            hl = hr = P().RIBBON_STAIR_HALF_WIDTH;
        left.push_back({cxs + s.wx * hl, cys + s.wy * hl});
        right.push_back({cxs - s.wx * hr, cys - s.wy * hr});
        max_h = std::max({max_h, hl, hr});
    }
    left = simplify_ring(left, P().RIBBON_RAIL_SIMPLIFY);
    right = simplify_ring(right, P().RIBBON_RAIL_SIMPLIFY);
    s.has_poly = true;
    s.poly = left;
    s.poly.insert(s.poly.end(), right.rbegin(), right.rend());
    s.half = max_h;
}

// _edge_strip; false when the edge is unusable.
bool edge_strip(CellCtx& C, const Grow& G, const Degree& degree,
                const std::map<Edge2, const PlanEntry*>& grown, const Edge2& e, UStrip* out) {
    Frame f;
    if (!edge_frame(C, e.first, e.second, &f)) return false;
    UStrip s;
    s.ei = e.first; s.ej = e.second;
    s.na = f.a; s.nb = f.b;
    extended_ends(f, degree, e.first, e.second, &s.a, &s.b);
    s.wx = -f.uy; s.wy = f.ux;
    s.len = f.length;
    s.land = G.flat.count(e) && C.land;
    const bool steep = !s.land && is_steep(s.a, s.b);
    const std::vector<P3>* prof = profile_of(G, e);
    auto it = grown.find(e);
    if (G.widths.empty() || it == grown.end()) {
        s.half = steep ? P().RIBBON_STAIR_HALF_WIDTH : P().RIBBON_HALF_WIDTH;
    } else {
        grown_outline(s, *it->second, G.widths, steep ? prof : nullptr);
    }
    if (prof) s.prof = *prof;
    *out = std::move(s);
    return true;
}

// _level_reach.
double level_reach(const CellCtx& C, const PlanEntry& pe, double ddx, double ddy,
                   double start, double d) {
    double good = start, dd = start;
    while (dd < d - 1e-6) {
        dd = std::min(d, dd + 8.0);
        bool any = false;
        for (double z : C.layers(pe.nx + ddx * dd, pe.ny + ddy * dd))
            any = any || std::fabs(z - pe.nz) <= P().DISC_LEVEL_TOL;
        if (!any) break;
        good = dd;
    }
    return good;
}

// _trim_disc_ray.
double trim_disc_ray(const CellCtx& C, const PlanEntry& pe, double ddx, double ddy, double d) {
    double zcur = pe.nz, good = P().RIBBON_HALF_WIDTH, dd = good;
    while (dd < d - 1e-6) {
        dd = std::min(d, dd + 8.0);
        bool any = false;
        double zc = 0.0;
        for (double z : C.layers(pe.nx + ddx * dd, pe.ny + ddy * dd)) {
            if (!(std::fabs(z - zcur) <= P().MAX_CLIMB)) continue;
            if (!any || std::fabs(z - zcur) < std::fabs(zc - zcur)) zc = z;
            any = true;
        }
        if (!any) { good = dd; continue; }
        if (std::fabs(zc - pe.nz) > P().MAX_CLIMB) break;
        zcur = zc;
        good = dd;
    }
    return good;
}

// _disc_strip; false when it degenerates.
bool disc_strip(const CellCtx& C, const PlanEntry& pe, const std::vector<double>& w,
                const Group& steep, bool trim, double cap, UStrip* out) {
    const int nrays = (int)P().RIBBON_GROW_DISC_RAYS;
    Ring disc;
    for (int kk = 0; kk < nrays; ++kk) {
        const double ang = 2.0 * 3.141592653589793 * kk / nrays;
        const double ddx = crt_cos(ang), ddy = crt_sin(ang);
        double d = w[pe.base + (size_t)kk];
        if (d > cap) d = C.has_sample() ? level_reach(C, pe, ddx, ddy, cap, d) : cap;
        if (trim && d > P().RIBBON_HALF_WIDTH) d = trim_disc_ray(C, pe, ddx, ddy, d);
        disc.push_back({pe.nx + ddx * d, pe.ny + ddy * d});
    }
    disc = simplify_ring(disc, P().RIBBON_RAIL_SIMPLIFY);
    if (disc.size() < 3) return false;
    const double nz = pe.nz;
    disc = clip_flat_poly_off_level(disc, pe.nx, pe.ny, [nz](double, double) { return nz; },
                                    steep);
    if (disc.size() < 3) return false;
    double rmax = 0.0;
    for (size_t k = 0; k < disc.size(); ++k) {
        const double r = py_hypot(disc[k][0] - pe.nx, disc[k][1] - pe.ny);
        if (k == 0 || r > rmax) rmax = r;
    }
    UStrip s;
    s.ei = s.ej = pe.i;
    s.na = s.nb = s.a = s.b = {pe.nx, pe.ny, pe.nz};
    s.wx = 0.0; s.wy = 1.0;
    s.len = s.half = std::max(rmax, 1.0);
    s.has_poly = true;
    s.poly = disc;
    *out = std::move(s);
    return true;
}

// The anchored stations' cut quads for one steep strip (_strip_cut_quads).
std::vector<Ring> strip_cut_quads(const UStrip& s, double nx, double ny,
                                  const std::function<double(double, double)>& nz, double rmax,
                                  const std::function<const Prepared*()>& anchor) {
    std::vector<Ring> hits;
    const double ax = s.a[0], ay = s.a[1], az = s.a[2], bx = s.b[0], by = s.b[1], bz = s.b[2];
    const double run = py_hypot(bx - ax, by - ay);
    if (run < 1e-6) return hits;
    const double half = s.half, dx = bx - ax, dy = by - ay;
    const double t0 = clamp01(((nx - ax) * dx + (ny - ay) * dy) / (run * run));
    if (py_hypot(nx - (ax + dx * t0), ny - (ay + dy * t0)) > rmax + half + 8.0) return hits;
    const Prepared* ap = anchor();
    if (!ap) return hits;
    const long long n = std::max(2LL, (long long)py_floordiv(run, 8.0));
    std::vector<char> mask((size_t)n + 1);
    for (long long k = 0; k <= n; ++k) {
        const double t = (double)k / (double)n;
        double zt = az + (bz - az) * t;
        if (!s.prof.empty()) {
            const double f = t * (double)(s.prof.size() - 1);
            const long long q = std::min((long long)s.prof.size() - 2, std::max(0LL, (long long)f));
            zt = s.prof[(size_t)q][2] + (s.prof[(size_t)q + 1][2] - s.prof[(size_t)q][2]) * (f - (double)q);
        }
        mask[(size_t)k] = std::fabs(zt - nz(ax + (bx - ax) * t, ay + (by - ay) * t)) > P().MAX_CLIMB;
    }
    std::set<long long> anchored;
    for (long long k = 0; k <= n; ++k) {
        if (mask[(size_t)k]) continue;
        const double t = (double)k / (double)n;
        const int in = ap->contains_xy(ax + dx * t, ay + dy * t);
        if (in < 0) return hits;
        if (in == 1) anchored.insert(k);
    }
    if (anchored.empty()) return hits;
    const double ux = dx / run, uy = dy / run, wx = -dy / run, wy = dx / run;
    for (long long k = 0; k <= n;) {
        if (!mask[(size_t)k]) { ++k; continue; }
        long long k2 = k;
        while (k2 + 1 <= n && mask[(size_t)k2 + 1]) ++k2;
        if (anchored.count(k - 1) || anchored.count(k2 + 1)) {
            const double d0 = run * (double)k / (double)n, d1 = run * (double)k2 / (double)n;
            if (d1 - d0 > 1.0)
                hits.push_back({{ax + ux * d0 + wx * half, ay + uy * d0 + wy * half},
                                {ax + ux * d0 - wx * half, ay + uy * d0 - wy * half},
                                {ax + ux * d1 - wx * half, ay + uy * d1 - wy * half},
                                {ax + ux * d1 + wx * half, ay + uy * d1 + wy * half}});
        }
        k = k2 + 1;
    }
    return hits;
}

// _subtract_quads: `poly` itself when the cut cannot be made.
Ring subtract_quads(const Ring& poly, double nx, double ny, const std::vector<Ring>& hits) {
    Geom dp = polygon(poly);
    if (!dp) return poly;
    if (is_valid(dp.get()) != 1) dp = buffer(dp.get(), 0.0);
    std::vector<Geom> quads;
    for (const Ring& q : hits) {
        quads.push_back(polygon(q));
        if (!quads.back()) return poly;
    }
    std::vector<const GEOSGeometry*> raw;
    for (const Geom& q : quads) raw.push_back(q.get());
    Geom u = union_all(raw);
    Geom cut = dp && u ? difference(dp.get(), u.get()) : Geom();
    if (!cut) return poly;
    if (is_empty(cut.get()) != 0) return poly;
    Geom pt = point(nx, ny);
    const GEOSGeometry* best = nullptr;
    double best_d = 0.0;
    for (const GEOSGeometry* g : parts_or_self(cut.get())) {
        if (type_id(g) != kPolygon || !(area(g) > 1.0)) continue;
        const double d = distance(g, pt.get());
        if (std::isnan(d)) return poly;
        if (!best || d < best_d) { best = g; best_d = d; }
    }
    if (!best) return poly;
    Ring ring = exterior_coords(best);
    if (ring.size() > 1 && ring.front() == ring.back()) ring.pop_back();
    return ring;
}

}  // namespace

std::vector<double> CellCtx::layers(double x, double y) const {
    std::vector<double> zs, out;
    if (!geo.has_walkable) return out;
    bucket_heights(geo.walk, x, y, zs);
    std::sort(zs.begin(), zs.end());
    for (double z : zs)
        if (out.empty() || z - out.back() > 2.0) out.push_back(z);
    return out;
}

Ring simplify_ring(const Ring& pts, double tol) {
    if (tol <= 0.0 || pts.size() < 3) return pts;
    std::vector<double> xy;
    for (const P2& p : pts) { xy.push_back(p[0]); xy.push_back(p[1]); }
    const std::vector<char> keep = simplify_keep(xy, tol);
    Ring out;
    for (size_t i = 0; i < pts.size(); ++i)
        if (keep[i]) out.push_back(pts[i]);
    return out;
}

Ring clip_flat_poly_off_level(const Ring& poly, double nx, double ny,
                              const std::function<double(double, double)>& nz,
                              const Group& steep) {
    bool built = false;
    Geom ap;
    std::unique_ptr<Prepared> prep;
    auto anchor = [&]() -> const Prepared* {
        if (!built) {
            built = true;
            Geom p = polygon(poly);
            if (p && is_valid(p.get()) != 1) p = buffer(p.get(), 0.0);
            if (p) ap = buffer(p.get(), 8.0);
            if (ap) prep = std::make_unique<Prepared>(ap.get());
        }
        return prep && *prep ? prep.get() : nullptr;
    };
    double rmax = 0.0;
    for (size_t k = 0; k < poly.size(); ++k) {
        const double r = py_hypot(poly[k][0] - nx, poly[k][1] - ny);
        if (k == 0 || r > rmax) rmax = r;
    }
    std::vector<Ring> hits;
    for (const UStrip* s : steep) {
        std::vector<Ring> h = strip_cut_quads(*s, nx, ny, nz, rmax, anchor);
        hits.insert(hits.end(), h.begin(), h.end());
    }
    return hits.empty() ? poly : subtract_quads(poly, nx, ny, hits);
}

Group steep_strips(const Group& strips) {
    Group out;
    for (const UStrip* s : strips) {
        if (s->len < 1e-6 || s->land) continue;
        if (std::fabs(s->nb[2] - s->na[2]) / s->len > P().RIBBON_GROW_MAX_SLOPE) out.push_back(s);
    }
    return out;
}

Group build_corridor_strips(CellCtx& C) {
    const bool grow = P().RIBBON_GROW != 0.0;
    Degree degree;
    for (const Edge2& e : C.edges) { ++degree[e.first]; ++degree[e.second]; }
    for (int i : C.lone) degree.emplace(i, 0);
    Grow G;
    for (const Edge2& e : C.edges)
        if (stands_on_land(C, e.first, e.second)) G.flat.insert(e);
    std::map<int, int> steep_count;
    for (const Edge2& e : C.edges) {
        Frame f;
        if (!edge_frame(C, e.first, e.second, &f) || G.flat.count(e)) continue;
        if (std::fabs(C.node_z[(size_t)e.second] - C.node_z[(size_t)e.first]) / f.length
            > P().RIBBON_GROW_MAX_SLOPE) {
            ++steep_count[e.first];
            ++steep_count[e.second];
        }
    }
    for (const Edge2& e : C.edges) {
        Frame f;
        if (!edge_frame(C, e.first, e.second, &f) || G.flat.count(e)) continue;
        P3 pa, pb;
        extended_ends(f, degree, e.first, e.second, &pa, &pb);
        if (!is_steep(pa, pb) && !leaves_chord(C, pa, pb)) continue;
        std::vector<P3> prof;
        if (!C.has_sample() || !surface_profile(C, pa, pb, &prof)) prof.clear();
        G.profiles[e] = prof;
    }
    if (grow) plan_stations(C, G, degree);
    if (!G.rows.empty()) {
        std::vector<double> nodes_xy, nz(C.node_z);
        for (const P3& p : C.nodes) { nodes_xy.push_back(p[0]); nodes_xy.push_back(p[1]); }
        std::vector<int> eg;
        for (const Edge2& e : C.edges) { eg.push_back(e.first); eg.push_back(e.second); }
        C.geo.ensure_walk_index();
        G.widths = grow_widths(C.geo.wall, C.geo.has_walkable ? &C.geo.walk : nullptr,
                               C.edges.empty() ? std::vector<double>() : nodes_xy, eg, nz, C.land,
                               G.rows, G.on_land, C.land_from);
        bridge_blocked_stations(G);
        erode_far_widths(G, grow_cap(C.land != nullptr));
    }
    std::map<Edge2, const PlanEntry*> grown;
    for (const PlanEntry& pe : G.plan)
        if (!pe.disc) grown[{pe.i, pe.j}] = &pe;
    Group strips;
    for (const Edge2& e : C.edges) {
        UStrip s;
        if (!edge_strip(C, G, degree, grown, e, &s)) continue;
        C.store.push_back(std::move(s));
        strips.push_back(&C.store.back());
    }
    if (G.rows.empty()) return strips;
    const Group steep = steep_strips(strips);
    std::map<int, double> reach;
    for (const PlanEntry& pe : G.plan) {
        if (pe.disc) continue;
        const std::pair<int, long long> ends[2] = {{pe.i, 0}, {pe.j, pe.k}};
        for (const auto& nk : ends) {
            const double w = std::max(G.widths[pe.base + 2 * (size_t)nk.second],
                                      G.widths[pe.base + 2 * (size_t)nk.second + 1]);
            auto it = reach.find(nk.first);
            reach[nk.first] = std::max(it == reach.end() ? 0.0 : it->second, w);
        }
    }
    for (const PlanEntry& pe : G.plan) {
        if (!pe.disc) continue;
        auto sc = steep_count.find(pe.i);
        const bool trim = P().DISC_RAY_TRIM != 0.0 && C.has_sample()
                          && sc != steep_count.end() && sc->second >= 1;
        auto rc = reach.find(pe.i);
        const double cap = std::max(P().RIBBON_HALF_WIDTH, rc == reach.end() ? 0.0 : rc->second);
        UStrip s;
        if (!disc_strip(C, pe, G.widths, steep, trim, cap, &s)) continue;
        s.land = stands_on_land(C, pe.i, pe.i);
        C.store.push_back(std::move(s));
        strips.push_back(&C.store.back());
    }
    return strips;
}

}  // namespace navgrow
