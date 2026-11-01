// Native shape repair for a finished corridor mesh (clean_decimate.decimate).
//
// Mirrors clean_decimate.py operation for operation: the same candidate order,
// the same Python-exact float helpers (pymath.h), the same tie-breaks.  The
// two collision oracles it needs -- ground_ok (a concave notch may be cut) and
// surface (the height a needle split sits on) -- come in as an Oracles, which
// the native cleanup stage answers from its own samplers and the Python
// binding answers with the reference's callables.
//
// A vertex's Z keeps its Python TYPE as well as its value: the reference stores
// numpy.float64 where a sampler produced the height (and anything averaged from
// it), plain float elsewhere, and Python's sum() rounds the two differently.

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <array>
#include <iterator>
#include <map>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "clean.h"
#include "params.h"
#include "pyconv.h"
#include "pymath.h"

namespace navgrow {

namespace {

struct Params {
    double min_edge, max_ratio, max_aspect, outline_tol, sawtooth, area_loss;
    double pin_radius, half_width, max_climb, concave_frac;
};

// clean_decimate's tunables, from the shared parameter set.
Params decimate_params() {
    const NavParams& N = P();
    return {N.DECIMATE_MIN_EDGE, N.MAX_EDGE_RATIO, N.MAX_TRI_ASPECT,
            N.DECIMATE_OUTLINE_TOL, N.DECIMATE_SAWTOOTH_DEV,
            N.DECIMATE_MAX_AREA_LOSS, N.DECIMATE_PIN_RADIUS,
            N.RIBBON_HALF_WIDTH, N.MAX_CLIMB, N.CONCAVE_CUT_FRAC};
}

inline double area2(const Vert& p, const Vert& q, const Vert& r) {
    return plan_area2(p, q, r);
}

inline double area2(const std::vector<Vert>& V, const Tri& t) {
    return area2(V[t[0]], V[t[1]], V[t[2]]);
}

// _badness.
double badness(const Vert& p, const Vert& q, const Vert& r, const Params& P) {
    return shape_badness(p.x, p.y, q.x, q.y, r.x, r.y, P.max_ratio,
                         P.max_aspect);
}

inline double badness(const std::vector<Vert>& V, const Tri& t,
                      const Params& P) {
    return badness(V[t[0]], V[t[1]], V[t[2]], P);
}

double mesh_area(const std::vector<Vert>& V, const std::vector<Tri>& tris) {
    return mesh_plan_area(V, tris);
}

inline bool flagged(const std::vector<char>& set, int v) {
    return v >= 0 && (size_t)v < set.size() && set[(size_t)v];
}

// --------------------------------------------------------------------------
// Outline (_boundary_verts, _outline_error)
// --------------------------------------------------------------------------
struct Outline {
    std::vector<char> on;                    // boundary vertex set
    std::vector<std::vector<int>> nbr;       // boundary neighbours, dict order
};

Outline boundary_verts(const std::vector<Tri>& tris, size_t nverts) {
    std::unordered_map<long long, size_t> slot;
    std::vector<std::pair<Key, int>> order;
    for (const Tri& t : tris) {
        for (int k = 0; k < 3; ++k) {
            const Key key = edge_key(t[k], t[(k + 1) % 3]);
            const long long h = ((long long)key.first << 32) | (unsigned)key.second;
            auto it = slot.find(h);
            if (it == slot.end()) {
                slot.emplace(h, order.size());
                order.push_back({key, 1});
            } else {
                order[it->second].second++;
            }
        }
    }
    Outline o;
    o.on.assign(nverts, 0);
    o.nbr.assign(nverts, {});
    for (const auto& e : order) {
        if (e.second != 1) continue;
        const int a = e.first.first, b = e.first.second;
        o.on[(size_t)a] = 1;
        o.on[(size_t)b] = 1;
        o.nbr[(size_t)a].push_back(b);
        o.nbr[(size_t)b].push_back(a);
    }
    return o;
}

double outline_error(const std::vector<Vert>& V, const Outline& O, int vi) {
    const std::vector<int>& ns = O.nbr[(size_t)vi];
    if (ns.size() != 2) return 1e9;
    const Vert& p = V[(size_t)vi];
    const Vert& a = V[(size_t)ns[0]];
    const Vert& b = V[(size_t)ns[1]];
    const double dx = b.x - a.x, dy = b.y - a.y;
    const double d2 = dx * dx + dy * dy;
    if (d2 < 1e-12) return py_hypot(p.x - a.x, p.y - a.y);
    const double t = std::max(0.0, std::min(1.0,
        ((p.x - a.x) * dx + (p.y - a.y) * dy) / d2));
    return py_hypot(p.x - (a.x + dx * t), p.y - (a.y + dy * t));
}

// --------------------------------------------------------------------------
// One collapse round's state (_Collapse)
// --------------------------------------------------------------------------
struct Ctx {
    std::vector<Vert>& V;
    const std::vector<char>& pin;
    const double* seam;          // minx, miny, maxx, maxy, or nullptr
    const Oracles& O;
    const Params& P;

    bool on_seam(int vi) const { return on_seam_rect(V[(size_t)vi].x, V[(size_t)vi].y, seam); }
    bool pinned(int v) const { return flagged(pin, v); }
};

struct Collapse {
    Ctx& C;
    const Outline& O;
    std::vector<Tri> tris;
    std::vector<char> alive;
    std::vector<std::vector<int>> vmap;      // sorted triangle indices
    std::vector<char> gone;
    std::vector<int> remap;
    bool changed = false;

    Collapse(Ctx& c, const Outline& o, const std::vector<Tri>& t)
        : C(c), O(o), tris(t), alive(t.size(), 1), vmap(c.V.size()),
          gone(c.V.size(), 0), remap(c.V.size(), -1) {
        for (size_t ti = 0; ti < tris.size(); ++ti)
            for (int i : tris[ti]) {
                std::vector<int>& m = vmap[(size_t)i];
                if (m.empty() || m.back() != (int)ti) m.push_back((int)ti);
            }
    }

    int resolve(int i) const {
        while (remap[(size_t)i] >= 0) i = remap[(size_t)i];
        return i;
    }

    double outline_error_of(int vi) const { return outline_error(C.V, O, vi); }
    bool boundary(int v) const { return flagged(O.on, v); }

    bool is_convex(int vi) const {
        const std::vector<int>& ns = O.nbr[(size_t)vi];
        if (ns.size() != 2) return false;
        const Vert& a0 = C.V[(size_t)ns[0]];
        const Vert& a1 = C.V[(size_t)ns[1]];
        const Vert& p = C.V[(size_t)vi];
        const double dx = a1.x - a0.x, dy = a1.y - a0.y;
        const double side_v = dx * (p.y - a0.y) - dy * (p.x - a0.x);
        double cx = 0.0, cy = 0.0;
        int cnt = 0;
        for (int ti : vmap[(size_t)vi]) {
            if (!alive[(size_t)ti]) continue;
            PySum sx, sy;
            for (int i : tris[(size_t)ti]) {
                sx.add(C.V[(size_t)i].x);
                sy.add(C.V[(size_t)i].y);
            }
            cx += sx.value() / 3.0;
            cy += sy.value() / 3.0;
            ++cnt;
        }
        if (!cnt) return false;
        const double side_c = dx * (cy / cnt - a0.y) - dy * (cx / cnt - a0.x);
        if (std::fabs(side_v) < 1e-9 || std::fabs(side_c) < 1e-9) return false;
        return (side_v > 0) != (side_c > 0);
    }

    bool neighbours(int a, int b) const {
        const std::vector<int>& ns = O.nbr[(size_t)a];
        return std::find(ns.begin(), ns.end(), b) != ns.end();
    }

    bool pins_are_redundant(int a, int b) const {
        if (!(boundary(a) && boundary(b))) return false;
        if (!neighbours(a, b)) return false;
        return std::min(outline_error_of(a), outline_error_of(b))
            <= C.P.outline_tol;
    }

    bool notch_is_open_ground(int vi, double err) const {
        if (!C.O.ground_ok || err > C.P.sawtooth) return false;
        const std::vector<int>& ns = O.nbr[(size_t)vi];
        return C.O.ground_ok(C.V[(size_t)ns[0]], C.V[(size_t)vi], C.V[(size_t)ns[1]]);
    }

    bool outline_move_ok(int vi, bool budget_left) const {
        const double err = outline_error_of(vi);
        const double tol = C.P.outline_tol;
        if (err <= tol) return true;
        if (!budget_left || C.on_seam(vi)) return false;
        if (is_convex(vi)) return err <= C.P.sawtooth;
        if (err <= std::max(tol, C.P.concave_frac * C.P.half_width)) return true;
        return notch_is_open_ground(vi, err);
    }

    // (keep, drop), or false when neither outline vertex may move.
    bool boundary_pair(int a, int b, bool budget_left, int* keep,
                       int* drop) const {
        if (!neighbours(a, b)) return false;
        const bool ok_a = outline_move_ok(a, budget_left);
        const bool ok_b = outline_move_ok(b, budget_left);
        if (!(ok_a || ok_b)) return false;
        const bool pin_a = C.pinned(a), pin_b = C.pinned(b);
        if (pin_a && pin_b) {
            if (outline_error_of(b) <= outline_error_of(a)) {
                *keep = a; *drop = b;
            } else {
                *keep = b; *drop = a;
            }
            return true;
        }
        if (pin_a || pin_b) {
            if ((pin_a && !ok_b) || (pin_b && !ok_a)) return false;
            if (pin_a) { *keep = a; *drop = b; } else { *keep = b; *drop = a; }
            return true;
        }
        if (ok_a && (!ok_b || outline_error_of(a) <= outline_error_of(b))) {
            *keep = b; *drop = a;
        } else {
            *keep = a; *drop = b;
        }
        return true;
    }

    bool link_condition_ok(int a, int b) const {
        std::vector<int> la, lb, opp;
        int shared = 0;
        for (int ti : vmap[(size_t)a]) {
            if (!alive[(size_t)ti]) continue;
            const Tri& t = tris[(size_t)ti];
            const bool hb = has(t, b);
            shared += hb;
            for (int v : t) {
                if (v == a || v == b) continue;
                la.push_back(v);
                if (hb) opp.push_back(v);
            }
        }
        for (int ti : vmap[(size_t)b]) {
            if (!alive[(size_t)ti]) continue;
            for (int v : tris[(size_t)ti])
                if (v != a && v != b) lb.push_back(v);
        }
        for (std::vector<int>* s : {&la, &lb, &opp}) {
            std::sort(s->begin(), s->end());
            s->erase(std::unique(s->begin(), s->end()), s->end());
        }
        for (int v : la) {
            if (std::binary_search(lb.begin(), lb.end(), v) &&
                !std::binary_search(opp.begin(), opp.end(), v))
                return shared == 1;
        }
        return true;
    }

    std::vector<int> incident(int keep, int drop) const {
        std::vector<int> out;
        std::set_union(vmap[(size_t)keep].begin(), vmap[(size_t)keep].end(),
                       vmap[(size_t)drop].begin(), vmap[(size_t)drop].end(),
                       std::back_inserter(out));
        return out;
    }

    std::vector<Tri> affected(int keep, int drop) const {
        std::vector<Tri> out;
        for (int ti : incident(keep, drop)) {
            const Tri& t = tris[(size_t)ti];
            if (alive[(size_t)ti] && !(has(t, keep) && has(t, drop)))
                out.push_back(t);
        }
        return out;
    }

    bool shape_ok(double before, const std::vector<Tri>& new_tris,
                  const std::vector<char>& old_signs) const {
        for (size_t k = 0; k < new_tris.size(); ++k) {
            if (!distinct(new_tris[k])) return false;
            const double ar = area2(C.V, new_tris[k]);
            if (std::fabs(ar) < 1e-6 || ((ar > 0) != (bool)old_signs[k]))
                return false;
        }
        double after = 1.0;
        for (size_t k = 0; k < new_tris.size(); ++k) {
            const double b = badness(C.V, new_tris[k], C.P);
            if (k == 0 || b > after) after = b;
        }
        return after <= std::max(before, 1.0) + 1e-9;
    }

    double cut_area(int keep, int drop, const std::vector<Tri>& affected_tris,
                    const std::vector<Tri>& new_tris, const Vert& target,
                    const Vert& old) {
        std::vector<Tri> both = affected_tris;
        for (int ti : incident(keep, drop)) {
            const Tri& t = tris[(size_t)ti];
            if (alive[(size_t)ti] && has(t, keep) && has(t, drop))
                both.push_back(t);
        }
        C.V[(size_t)keep] = old;
        const double before_area = mesh_area(C.V, both);
        C.V[(size_t)keep] = target;
        return std::max(0.0, before_area - mesh_area(C.V, new_tris));
    }

    void commit(int keep, int drop) {
        remap[(size_t)drop] = keep;
        gone[(size_t)drop] = 1;
        const std::vector<int> owned = vmap[(size_t)drop];
        for (int ti : owned) {
            if (!alive[(size_t)ti]) continue;
            Tri nt = tris[(size_t)ti];
            for (int i : nt) {
                std::vector<int>& m = vmap[(size_t)i];
                auto it = std::lower_bound(m.begin(), m.end(), ti);
                if (it != m.end() && *it == ti) m.erase(it);
            }
            for (int& i : nt) if (i == drop) i = keep;
            if (!distinct(nt)) { alive[(size_t)ti] = 0; continue; }
            tris[(size_t)ti] = nt;
            for (int i : nt) {
                std::vector<int>& m = vmap[(size_t)i];
                auto it = std::lower_bound(m.begin(), m.end(), ti);
                if (it == m.end() || *it != ti) m.insert(it, ti);
            }
        }
        changed = true;
    }

    std::vector<Tri> survivors() const {
        std::vector<Tri> out;
        for (size_t ti = 0; ti < tris.size(); ++ti)
            if (alive[ti]) out.push_back(tris[ti]);
        return out;
    }
};

// _collapse_target: false when the edge may not collapse.
bool collapse_target(const Collapse& st, int a, int b, bool budget_left,
                     int* keep, int* drop, Vert* target) {
    const bool a_b = st.boundary(a), b_b = st.boundary(b);
    const bool pin_a = st.C.pinned(a), pin_b = st.C.pinned(b);
    if (pin_a && pin_b && !st.pins_are_redundant(a, b)) return false;
    if (a_b && b_b) {
        if (!st.boundary_pair(a, b, budget_left, keep, drop)) return false;
    } else if (a_b) {
        *keep = a; *drop = b;
    } else if (b_b) {
        *keep = b; *drop = a;
    } else if (pin_a || pin_b) {
        if (pin_a) { *keep = a; *drop = b; } else { *keep = b; *drop = a; }
    } else {
        const Vert& va = st.C.V[(size_t)a];
        const Vert& vb = st.C.V[(size_t)b];
        *keep = a; *drop = b;
        *target = {(va.x + vb.x) * 0.5, (va.y + vb.y) * 0.5,
                   (va.z + vb.z) * 0.5, va.znp || vb.znp};
        return true;
    }
    *target = st.C.V[(size_t)*keep];
    return true;
}

// _try_collapse: true (adding the cost to *loss) when the collapse landed.
bool try_collapse(Collapse& st, int a, int b, double area_lost, double budget,
                  double* loss) {
    int keep, drop;
    Vert target;
    if (!collapse_target(st, a, b, area_lost < budget, &keep, &drop, &target))
        return false;
    if (!st.link_condition_ok(a, b)) return false;
    std::vector<Vert>& V = st.C.V;
    const std::vector<Tri> aff = st.affected(keep, drop);
    const Vert old = V[(size_t)keep];
    std::vector<char> old_signs;
    double before = 1.0;
    for (size_t k = 0; k < aff.size(); ++k) {
        old_signs.push_back(area2(V, aff[k]) > 0);
        const double bd = badness(V, aff[k], st.C.P);
        if (k == 0 || bd > before) before = bd;
    }
    V[(size_t)keep] = target;
    std::vector<Tri> new_tris = aff;
    for (Tri& t : new_tris) for (int& i : t) if (i == drop) i = keep;
    *loss = 0.0;
    bool ok = st.shape_ok(before, new_tris, old_signs);
    if (ok && st.boundary(a) && st.boundary(b)) {
        *loss = st.cut_area(keep, drop, aff, new_tris, target, old);
        if (*loss > 1.0 && area_lost + *loss > budget) ok = false;
    }
    if (!ok) {
        V[(size_t)keep] = old;
        return false;
    }
    st.commit(keep, drop);
    return true;
}

// --------------------------------------------------------------------------
// Flips (_flip_round, _flip_pass)
// --------------------------------------------------------------------------
std::map<Key, std::vector<int>> edge_owners(const std::vector<Tri>& tris) {
    std::map<Key, std::vector<int>> out;
    for (size_t ti = 0; ti < tris.size(); ++ti)
        for (int k = 0; k < 3; ++k)
            out[edge_key(tris[ti][k], tris[ti][(k + 1) % 3])]
                .push_back((int)ti);
    return out;
}

bool all_pinned(const Ctx& C, const Tri& t) {
    return C.pinned(t[0]) && C.pinned(t[1]) && C.pinned(t[2]);
}

int opposite(const Tri& t, int a, int b) {
    for (int v : t) if (v != a && v != b) return v;
    return -1;
}

bool flip_improves(const Ctx& C, const Tri& t1, const Tri& t2, int a, int b,
                   int c, int d) {
    if (all_pinned(C, t1) || all_pinned(C, t2)) return false;
    const std::vector<Vert>& V = C.V;
    const double worst_old = std::max(badness(V, t1, C.P), badness(V, t2, C.P));
    const double worst_new = std::max(badness(V, Tri{c, d, a}, C.P),
                                      badness(V, Tri{c, d, b}, C.P));
    if (worst_new >= worst_old - 1e-9) return false;
    const double dz_old = std::fabs(V[(size_t)a].z - V[(size_t)b].z);
    return std::fabs(V[(size_t)c].z - V[(size_t)d].z) <= dz_old + C.P.max_climb;
}

bool flip_round(const Ctx& C, std::vector<Tri>& tris) {
    const std::map<Key, std::vector<int>> owners = edge_owners(tris);
    std::vector<char> done(tris.size(), 0);
    std::vector<Key> new_edges;
    bool changed = false;
    for (const auto& e : owners) {
        if (e.second.size() != 2) continue;
        const int ti = e.second[0], tj = e.second[1];
        if (done[(size_t)ti] || done[(size_t)tj]) continue;
        const Tri t1 = tris[(size_t)ti], t2 = tris[(size_t)tj];
        const int a = e.first.first, b = e.first.second;
        const int c = opposite(t1, a, b), d = opposite(t2, a, b);
        if (c < 0 || d < 0 || c == d) continue;
        const Key ck = edge_key(c, d);
        if (owners.count(ck) ||
            std::find(new_edges.begin(), new_edges.end(), ck) != new_edges.end())
            continue;
        if (!flip_improves(C, t1, t2, a, b, c, d)) continue;
        const double s_a = area2(C.V[(size_t)c], C.V[(size_t)d], C.V[(size_t)a]);
        const double s_b = area2(C.V[(size_t)c], C.V[(size_t)d], C.V[(size_t)b]);
        if (s_a * s_b >= 0 || std::fabs(s_a) <= 1e-6 || std::fabs(s_b) <= 1e-6)
            continue;
        tris[(size_t)ti] = s_b > 0 ? Tri{c, d, b} : Tri{d, c, b};
        tris[(size_t)tj] = s_a > 0 ? Tri{c, d, a} : Tri{d, c, a};
        new_edges.push_back(ck);
        done[(size_t)ti] = done[(size_t)tj] = 1;
        changed = true;
    }
    return changed;
}

bool flip_pass(const Ctx& C, std::vector<Tri>& tris, int rounds = 3) {
    bool any = false;
    for (int r = 0; r < rounds; ++r) {
        if (!flip_round(C, tris)) break;
        any = true;
    }
    return any;
}

// --------------------------------------------------------------------------
// Needle splits (_split_needles)
// --------------------------------------------------------------------------
Vert apex_split_point(const Ctx& C, int a, int b, const Vert& apex, double d) {
    const std::vector<Vert>& V = C.V;
    const Vert& va = V[(size_t)a];
    const Vert& vb = V[(size_t)b];
    const double dx = vb.x - va.x, dy = vb.y - va.y;
    double tproj = ((apex.x - va.x) * dx + (apex.y - va.y) * dy) / (d * d);
    const double tlo = C.P.min_edge / d;
    tproj = std::max(tlo, std::min(1.0 - tlo, tproj));
    Vert m{va.x + dx * tproj, va.y + dy * tproj,
           va.z + (vb.z - va.z) * tproj, va.znp || vb.znp};
    if (!C.O.surface) return m;
    const double near = tproj <= 0.5 ? va.z : vb.z;
    double z;
    bool znp;
    if (C.O.surface(m.x, m.y, near, &z, &znp) && std::fabs(z - m.z) <= C.P.max_climb) {
        m.z = z;
        m.znp = znp;
    }
    return m;
}

struct Half { int owner, slot, apex; };

// _split_halves: false when an owner does not carry the edge.
bool split_halves(const Ctx& C, const std::vector<Tri>& tris,
                  const std::vector<int>& owners, int a, int b, const Vert& mid,
                  std::vector<Half>* halves, double* after) {
    *after = 0.0;
    for (int o : owners) {
        const Tri& to = tris[(size_t)o];
        bool found = false;
        for (int kk = 0; kk < 3; ++kk) {
            const int p = to[kk], q = to[(kk + 1) % 3];
            if (!((p == a && q == b) || (p == b && q == a))) continue;
            const int cvi = to[(kk + 2) % 3];
            const Vert& vc = C.V[(size_t)cvi];
            const double b1 = badness(C.V[(size_t)p], mid, vc, C.P);
            const double b2 = badness(mid, C.V[(size_t)q], vc, C.P);
            *after = std::max(std::max(*after, b1), b2);
            halves->push_back({o, kk, cvi});
            found = true;
            break;
        }
        if (!found) return false;
    }
    return true;
}

// The longest edge (a, b) of t, its length, and the apex index; ties go to the
// later edge, as Python's tuple max does.
int longest_edge(const std::vector<Vert>& V, const Tri& t, double* d) {
    int k = 0;
    for (int i = 0; i < 3; ++i) {
        const Vert& p = V[(size_t)t[i]];
        const Vert& q = V[(size_t)t[(i + 1) % 3]];
        const double di = py_dist(p.x, p.y, q.x, q.y);
        if (i == 0 || di >= *d) { *d = di; k = i; }
    }
    return k;
}

bool split_needles(Ctx& C, std::vector<Tri>& tris) {
    const std::map<Key, std::vector<int>> owners_of = edge_owners(tris);
    const double min_len = 2.0 * C.P.min_edge;
    std::vector<char> consumed(tris.size(), 0), dead(tris.size(), 0);
    std::vector<Tri> made;
    for (size_t ti = 0; ti < tris.size(); ++ti) {
        if (consumed[ti]) continue;
        const Tri t = tris[ti];
        if (badness(C.V, t, C.P) <= 1.0) continue;
        double d = 0.0;
        const int k = longest_edge(C.V, t, &d);
        if (d < min_len) continue;
        const int a = t[k], b = t[(k + 1) % 3];
        if (C.on_seam(a) && C.on_seam(b)) continue;
        auto it = owners_of.find(edge_key(a, b));
        static const std::vector<int> none;
        const std::vector<int>& owners = it == owners_of.end() ? none : it->second;
        bool skip = owners.size() > 2;
        for (int o : owners)
            skip = skip || consumed[(size_t)o] || all_pinned(C, tris[(size_t)o]);
        if (skip) continue;
        const Vert apex = C.V[(size_t)t[(k + 2) % 3]];
        const Vert mid = apex_split_point(C, a, b, apex, d);
        std::vector<Half> halves;
        double after;
        if (!split_halves(C, tris, owners, a, b, mid, &halves, &after)) continue;
        double worst = 0.0;
        for (size_t n = 0; n < owners.size(); ++n) {
            const double bd = badness(C.V, tris[(size_t)owners[n]], C.P);
            if (n == 0 || bd > worst) worst = bd;
        }
        if (after >= worst - 1e-9) continue;
        const int m = (int)C.V.size();
        C.V.push_back(mid);
        for (const Half& h : halves) {
            const Tri& to = tris[(size_t)h.owner];
            made.push_back({to[h.slot], m, h.apex});
            made.push_back({m, to[(h.slot + 1) % 3], h.apex});
            dead[(size_t)h.owner] = consumed[(size_t)h.owner] = 1;
        }
    }
    if (made.empty()) return false;
    std::vector<Tri> out;
    for (size_t ti = 0; ti < tris.size(); ++ti)
        if (!dead[ti]) out.push_back(tris[ti]);
    out.insert(out.end(), made.begin(), made.end());
    tris.swap(out);
    return true;
}

// --------------------------------------------------------------------------
// Driver (_decimate_round, decimate)
// --------------------------------------------------------------------------
std::vector<std::pair<double, Key>> short_edges(const std::vector<Vert>& V,
                                                const std::vector<Tri>& tris,
                                                double min_edge) {
    std::vector<std::pair<double, Key>> out;
    std::vector<Key> seen;
    for (const Tri& t : tris)
        for (int k = 0; k < 3; ++k) seen.push_back(edge_key(t[k], t[(k + 1) % 3]));
    std::sort(seen.begin(), seen.end());
    seen.erase(std::unique(seen.begin(), seen.end()), seen.end());
    for (const Key& key : seen) {
        const Vert& p = V[(size_t)key.first];
        const Vert& q = V[(size_t)key.second];
        const double d = py_dist(p.x, p.y, q.x, q.y);
        if (d < min_edge) out.push_back({d, key});
    }
    std::sort(out.begin(), out.end());
    return out;
}

// One collapse+flip round; false when no short edge was left to consider.
bool decimate_round(Ctx& C, std::vector<Tri>& tris, double budget,
                    double* area_lost, bool* progressed) {
    const Outline O = boundary_verts(tris, C.V.size());
    const auto cands = short_edges(C.V, tris, C.P.min_edge);
    if (cands.empty()) return false;
    Collapse st(C, O, tris);
    for (const auto& c : cands) {
        const int a = st.resolve(c.second.first);
        const int b = st.resolve(c.second.second);
        if (a == b || st.gone[(size_t)a] || st.gone[(size_t)b]) continue;
        double loss;
        if (try_collapse(st, a, b, *area_lost, budget, &loss)) *area_lost += loss;
    }
    tris = st.survivors();
    const bool flipped = flip_pass(C, tris);
    *progressed = st.changed || flipped;
    return true;
}

void run_decimate(Ctx& C, std::vector<Tri>& tris, int rounds, bool allow_split) {
    if (tris.empty() || C.P.min_edge <= 0.0) return;
    const double budget = C.P.area_loss * mesh_area(C.V, tris);
    double area_lost = 0.0;
    for (int r = 0; r < rounds; ++r) {
        bool progressed;
        if (!decimate_round(C, tris, budget, &area_lost, &progressed)) break;
        const bool split = allow_split && split_needles(C, tris);
        if (!progressed && !split) break;
    }
}

}  // namespace

// --------------------------------------------------------------------------
// Pins (_pin_verts)
// --------------------------------------------------------------------------
std::vector<char> pin_verts(const std::vector<Vert>& V,
                            const std::vector<Pin>& pins) {
    std::vector<char> out(V.size(), 0);
    if (pins.empty()) return out;
    double rmax = pins[0].r;
    for (const Pin& p : pins) if (p.r > rmax) rmax = p.r;
    const double cell = std::max(rmax, 1.0);
    std::map<std::pair<long long, long long>, std::vector<size_t>> buckets;
    for (size_t i = 0; i < pins.size(); ++i)
        buckets[{(long long)py_floordiv(pins[i].x, cell),
                 (long long)py_floordiv(pins[i].y, cell)}].push_back(i);
    for (size_t vi = 0; vi < V.size(); ++vi) {
        const long long gx = (long long)py_floordiv(V[vi].x, cell);
        const long long gy = (long long)py_floordiv(V[vi].y, cell);
        bool near = false;
        for (long long dx = -1; dx <= 1 && !near; ++dx) {
            for (long long dy = -1; dy <= 1 && !near; ++dy) {
                auto it = buckets.find({gx + dx, gy + dy});
                if (it == buckets.end()) continue;
                for (size_t pi : it->second) {
                    const Pin& p = pins[pi];
                    if (py_square(V[vi].x - p.x) + py_square(V[vi].y - p.y)
                            <= p.r * p.r) {
                        near = true;
                        break;
                    }
                }
            }
        }
        out[vi] = near;
    }
    return out;
}

void decimate_mesh(std::vector<Vert>& V, std::vector<Tri>& T,
                   const std::vector<Pin>& pins, const double* seam, int rounds,
                   bool allow_split, const Oracles& O) {
    const Params Pd = decimate_params();
    const std::vector<char> pin = pin_verts(V, pins);
    Ctx C{V, pin, seam, O, Pd};
    run_decimate(C, T, rounds, allow_split);
}

}  // namespace navgrow

using namespace navgrow;

namespace {

// Oracles answered by the reference's Python callables (None for either).
Oracles python_oracles(PyObject* ground_ok, PyObject* surface) {
    Oracles O;
    if (ground_ok != Py_None) {
        O.ground_ok = [ground_ok](const Vert& a, const Vert& v, const Vert& b) {
            PyObject* r = PyObject_CallFunction(ground_ok, "[ddd][ddd][ddd]", a.x, a.y, a.z,
                                                v.x, v.y, v.z, b.x, b.y, b.z);
            if (!r) throw PyErrorSet{};
            const int truth = PyObject_IsTrue(r);
            Py_DECREF(r);
            if (truth < 0) throw PyErrorSet{};
            return truth != 0;
        };
    }
    if (surface != Py_None) {
        O.surface = [surface](double x, double y, double near, double* z, bool* znp) {
            PyObject* got = PyObject_CallFunction(surface, "ddd", x, y, near);
            if (!got) throw PyErrorSet{};
            const bool have = got != Py_None;
            if (have) {
                *z = PyFloat_AsDouble(got);
                *znp = !PyFloat_CheckExact(got);
            }
            Py_DECREF(got);
            if (have && *z == -1.0 && PyErr_Occurred()) throw PyErrorSet{};
            return have;
        };
    }
    return O;
}

}  // namespace

// decimate(verts, tris, pins, seam_bounds, rounds, allow_split, ground_ok,
//          surface) -> (verts, tris)
//
// clean_decimate.decimate's contract: verts come back as [x, y, z] lists
// (split vertices appended), tris as (a, b, c) tuples.  pins is the
// (x, y[, radius]) list or None, seam_bounds (minx, miny, maxx, maxy) or None,
// ground_ok / surface callables or None.  Needs set_params.
PyObject* py_decimate(PyObject*, PyObject* args) {
    PyObject *o_verts, *o_tris, *o_pins, *o_seam, *o_ground, *o_surface;
    int rounds, allow_split;
    if (!PyArg_ParseTuple(args, "OOOOipOO", &o_verts, &o_tris, &o_pins,
                          &o_seam, &rounds, &allow_split, &o_ground, &o_surface))
        return nullptr;
    std::vector<Vert> V;
    std::vector<Tri> T;
    std::vector<Pin> pins;
    double seam[4];
    bool has_seam;
    if (!read_verts(o_verts, V, false) || !read_tris(o_tris, V.size(), T) ||
        !read_pins(o_pins, P().DECIMATE_PIN_RADIUS, pins) ||
        !read_bounds(o_seam, seam, &has_seam))
        return nullptr;
    try {
        decimate_mesh(V, T, pins, has_seam ? seam : nullptr, rounds, allow_split != 0,
                      python_oracles(o_ground, o_surface));
    } catch (const PyErrorSet&) {
        return nullptr;
    } catch (const std::bad_alloc&) {
        return PyErr_NoMemory();
    }
    return Py_BuildValue("(NN)", verts_to_python(V), tris_to_python(T));
}

// pymath(op, values) -> list: the pymath.h helper `op` applied to `values`,
// so the tests can hold each one against the interpreter.  "hypot" takes
// (x, y) pairs, "floordiv" (a, b) pairs, "square" scalars, "sum" one list.
PyObject* py_pymath(PyObject*, PyObject* args) {
    const char* op;
    PyObject* o_vals;
    if (!PyArg_ParseTuple(args, "sO", &op, &o_vals)) return nullptr;
    PyObject* f = PySequence_Fast(o_vals, "values must be a sequence");
    if (!f) return nullptr;
    const Py_ssize_t n = PySequence_Fast_GET_SIZE(f);
    std::vector<double> v((size_t)n);
    for (Py_ssize_t i = 0; i < n; ++i) {
        v[(size_t)i] = PyFloat_AsDouble(PySequence_Fast_GET_ITEM(f, i));
        if (v[(size_t)i] == -1.0 && PyErr_Occurred()) { Py_DECREF(f); return nullptr; }
    }
    Py_DECREF(f);
    std::vector<double> out;
    const std::string name(op);
    if (name == "sum") {
        PySum s;
        for (double x : v) s.add(x);
        out.push_back(s.value());
    } else if (name == "square") {
        for (double x : v) out.push_back(py_square(x));
    } else if (name == "hypot" || name == "floordiv") {
        for (size_t i = 0; i + 1 < v.size(); i += 2)
            out.push_back(name == "hypot" ? py_hypot(v[i], v[i + 1])
                                          : py_floordiv(v[i], v[i + 1]));
    } else {
        PyErr_Format(PyExc_ValueError, "unknown op %s", op);
        return nullptr;
    }
    PyObject* lst = PyList_New((Py_ssize_t)out.size());
    for (size_t i = 0; lst && i < out.size(); ++i)
        PyList_SET_ITEM(lst, (Py_ssize_t)i, PyFloat_FromDouble(out[i]));
    return lst;
}
