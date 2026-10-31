// union_sheets: grouping ribbons into storeys and splitting sheets that stack.
// Every loop runs in the Python order; sets the Python only tests membership
// in are plain containers here.

#include "params.h"
#include "sheets.h"
#include "union.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <set>

namespace navgrow {

namespace {

using PairSet = std::set<std::pair<int, int>>;

// Path-compressed union-find over `n` items.
struct UnionFind {
    std::vector<int> parent;
    explicit UnionFind(size_t n) : parent(n) {
        for (size_t i = 0; i < n; ++i) parent[i] = (int)i;
    }
    int find(int x) {
        while (parent[(size_t)x] != x) {
            parent[(size_t)x] = parent[(size_t)parent[(size_t)x]];
            x = parent[(size_t)x];
        }
        return x;
    }
    void join(int a, int b) {
        const int ra = find(a), rb = find(b);
        if (ra != rb) parent[(size_t)ra] = rb;
    }
};

bool truth(int v, const char* what) {
    if (v < 0) throw GeosError(std::string(what) + ": " + geos()->last_error());
    return v == 1;
}

// _node_partition: ribbons sharing a node at the same height are joined.
UnionFind node_partition(const Group& strips) {
    UnionFind uf(strips.size());
    std::map<int, std::vector<std::pair<int, double>>> at_node;
    for (size_t si = 0; si < strips.size(); ++si) {
        const UStrip& s = *strips[si];
        if (s.ei < 0) continue;
        at_node[s.ei].push_back({(int)si, s.na[2]});
        if (s.ej != s.ei) at_node[s.ej].push_back({(int)si, s.nb[2]});
    }
    for (const auto& kv : at_node) {
        const auto& e = kv.second;
        for (size_t a = 0; a < e.size(); ++a)
            for (size_t b = a + 1; b < e.size(); ++b)
                if (!(std::fabs(e[a].second - e[b].second) > P().SAME_SURFACE_Z))
                    uf.join(e[a].first, e[b].first);
    }
    return uf;
}

// _best_group_for_door: the group the door footprint touches at an agreeing
// height, or -1.
int best_group_for_door(const std::vector<Group>& out, const GEOSGeometry* dp,
                        double z) {
    int best_gi = -1;
    double best_hz = 0.0;
    for (size_t gi = 0; gi < out.size(); ++gi) {
        bool have = false;
        double hz = 0.0;
        for (const UStrip* x : out[gi]) {
            if (!truth(intersects(ribbon_polygon(*x), dp), "intersects")) continue;
            const double d = std::min(std::fabs(x->na[2] - z), std::fabs(x->nb[2] - z));
            if (!have || d < hz) { have = true; hz = d; }
        }
        if (!have || hz > P().STOREY_GAP_Z) continue;
        if (best_gi < 0 || hz < best_hz) { best_hz = hz; best_gi = (int)gi; }
    }
    return best_gi;
}

// _bonded_pairs: index pairs meeting at a node where their heights agree.
PairSet bonded_pairs(const Group& items) {
    std::map<int, std::vector<std::pair<int, double>>> node_h;
    for (size_t k = 0; k < items.size(); ++k) {
        const UStrip& s = *items[k];
        if (s.ei < 0) continue;
        node_h[s.ei].push_back({(int)k, s.na[2]});
        if (s.ej != s.ei) node_h[s.ej].push_back({(int)k, s.nb[2]});
    }
    PairSet bonded;
    for (const auto& kv : node_h) {
        const auto& e = kv.second;
        for (size_t x = 0; x < e.size(); ++x)
            for (size_t y = x + 1; y < e.size(); ++y)
                if (std::fabs(e[x].second - e[y].second) <= P().SAME_SURFACE_Z)
                    bonded.insert({std::min(e[x].first, e[y].first),
                                   std::max(e[x].first, e[y].first)});
    }
    return bonded;
}

// _z_ranges: each ribbon's height span.
std::vector<std::pair<double, double>> z_ranges(const Group& items) {
    const double inf = std::numeric_limits<double>::infinity();
    std::vector<std::pair<double, double>> out;
    for (const UStrip* s : items) {
        if (s->land) {
            out.push_back({-inf, inf});
        } else if (s->prof.size() >= 2) {
            double lo = s->prof[0][2], hi = lo;
            for (const P3& q : s->prof) { lo = std::min(lo, q[2]); hi = std::max(hi, q[2]); }
            out.push_back({lo, hi});
        } else {
            const double za = s->a[2], zb = s->b[2];
            out.push_back(za <= zb ? std::make_pair(za, zb) : std::make_pair(zb, za));
        }
    }
    return out;
}

// _partition_by_agreement: joins agreeing overlaps; returns the conflicts.
PairSet partition_by_agreement(const Group& items, const PairSet& bonded,
                               UnionFind& uf, const Land* land) {
    PairSet conflicts;
    const auto zr = z_ranges(items);
    std::vector<const GEOSGeometry*> polys;
    for (const UStrip* s : items) polys.push_back(ribbon_polygon(*s));
    std::vector<std::pair<int, int>> measured;
    if (polys.size() >= 2) {
        for (const auto& ab : candidate_pairs(polys)) {
            if (!bonded.count(ab)) {
                const auto& A = zr[(size_t)ab.first];
                const auto& B = zr[(size_t)ab.second];
                const double sep = B.first > A.second ? B.first - A.second : A.first - B.second;
                if (sep > P().STOREY_GAP_Z) { conflicts.insert(ab); continue; }
            }
            measured.push_back(ab);
        }
    }
    const FlatStrips F = flat_strips(items, land);
    const Land* gl = land_of(items, land);
    const Land none;
    const std::vector<double> gaps = pair_gaps(polys, F.strips, F.prof.data(),
                                               gl ? *gl : none, measured,
                                               P().STOREY_GAP_Z);
    for (size_t k = 0; k < measured.size(); ++k) {
        if (std::isnan(gaps[k])) continue;
        if (gaps[k] > P().STOREY_GAP_Z && !bonded.count(measured[k]))
            conflicts.insert(measured[k]);
        else
            uf.join(measured[k].first, measured[k].second);
    }
    for (const auto& ab : bonded) uf.join(ab.first, ab.second);
    return conflicts;
}

using Adj = std::map<int, std::set<int>>;

const std::set<int>* adj_of(const Adj& adj, int i) {
    auto it = adj.find(i);
    return it == adj.end() || it->second.empty() ? nullptr : &it->second;
}

bool disjoint(const std::set<int>& a, const std::vector<int>& b) {
    for (int x : b)
        if (a.count(x)) return false;
    return true;
}

double mid_z(const UStrip& s) { return 0.5 * (s.na[2] + s.nb[2]); }

// _sub_sheets: each ribbon joins the conflict-free sub-sheet it agrees with best.
std::vector<std::vector<int>> sub_sheets(const Group& items, std::vector<int> members,
                                         const Adj& cadj, const Adj& badj) {
    std::stable_sort(members.begin(), members.end(), [&](int a, int b) {
        return -0.5 * (items[(size_t)a]->na[2] + items[(size_t)a]->nb[2])
             < -0.5 * (items[(size_t)b]->na[2] + items[(size_t)b]->nb[2]);
    });
    std::vector<std::vector<int>> subs;
    std::vector<double> sub_h;
    for (int i : members) {
        const double zi = mid_z(*items[(size_t)i]);
        const std::set<int>* ci = adj_of(cadj, i);
        const std::set<int>* bi = adj_of(badj, i);
        int best_rank = 0, best_si = -1;
        double best_d = 0.0;
        for (size_t si = 0; si < subs.size(); ++si) {
            if (ci && !disjoint(*ci, subs[si])) continue;
            const double d = std::fabs(sub_h[si] - zi);
            const int rank = (bi && !disjoint(*bi, subs[si])) ? 0 : 1;
            if (best_si < 0 || rank < best_rank || (rank == best_rank && d < best_d)) {
                best_rank = rank; best_d = d; best_si = (int)si;
            }
        }
        if (best_si < 0) {
            subs.push_back({i});
            sub_h.push_back(zi);
        } else {
            auto& sub = subs[(size_t)best_si];
            sub.push_back(i);
            sub_h[(size_t)best_si] = (sub_h[(size_t)best_si] * (double)(sub.size() - 1) + zi)
                                     / (double)sub.size();
        }
    }
    return subs;
}

// _subs_same_floor.
bool subs_same_floor(const Group& items, const std::vector<int>& sa,
                     const std::vector<int>& sb, const Land* land) {
    for (int i : sa) {
        const GEOSGeometry* pi = ribbon_polygon(*items[(size_t)i]);
        for (int j : sb) {
            const GEOSGeometry* pj = ribbon_polygon(*items[(size_t)j]);
            if (!truth(intersects(pi, pj), "intersects")) continue;
            Geom inter = intersection(pi, pj);
            if (!inter || is_empty(inter.get()) != 0 || area(inter.get()) < 1.0) continue;
            double cx, cy;
            Geom c = need(centroid(inter.get()), "centroid");
            if (!point_xy(c.get(), &cx, &cy))
                throw GeosError("centroid of an empty overlap");
            if (std::fabs(height_on(*items[(size_t)i], land, cx, cy)
                          - height_on(*items[(size_t)j], land, cx, cy))
                <= P().SAME_SURFACE_Z)
                return true;
        }
    }
    return false;
}

// _merge_same_floor.
std::vector<std::vector<int>> merge_same_floor(const Group& items,
                                               const std::vector<std::vector<int>>& subs,
                                               const Adj& cadj, const Land* land) {
    std::vector<std::vector<int>> merged;
    for (const auto& sub : subs) {
        std::set<int> conf;
        for (int i : sub) {
            const std::set<int>* c = adj_of(cadj, i);
            if (c) conf.insert(c->begin(), c->end());
        }
        int target = -1;
        for (size_t mi = 0; mi < merged.size(); ++mi) {
            if (!conf.empty() && !disjoint(conf, merged[mi])) continue;
            if (subs_same_floor(items, sub, merged[mi], land)) { target = (int)mi; break; }
        }
        if (target < 0) merged.push_back(sub);
        else merged[(size_t)target].insert(merged[(size_t)target].end(), sub.begin(), sub.end());
    }
    return merged;
}

Group pick(const Group& items, const std::vector<int>& idx) {
    Group g;
    for (int i : idx) g.push_back(items[(size_t)i]);
    return g;
}

// _split_one_group.
void split_one_group(const Group& group, std::vector<Group>& out, const Land* land) {
    const Group items = valid_ribbons(group);
    if (items.empty()) return;
    UnionFind uf(items.size());
    const PairSet bonded = bonded_pairs(items);
    const PairSet conflicts = partition_by_agreement(items, bonded, uf, land);
    std::vector<int> roots;
    std::map<int, std::vector<int>> buckets;
    for (size_t i = 0; i < items.size(); ++i) {
        const int r = uf.find((int)i);
        if (!buckets.count(r)) roots.push_back(r);
        buckets[r].push_back((int)i);
    }
    for (int r : roots) {
        const std::vector<int>& members = buckets[r];
        const std::set<int> in(members.begin(), members.end());
        Adj cadj, badj;
        for (const auto& ab : conflicts)
            if (in.count(ab.first) && in.count(ab.second)) {
                cadj[ab.first].insert(ab.second);
                cadj[ab.second].insert(ab.first);
            }
        for (const auto& ab : bonded)
            if (in.count(ab.first) && in.count(ab.second)) {
                badj[ab.first].insert(ab.second);
                badj[ab.second].insert(ab.first);
            }
        if (cadj.empty()) {
            out.push_back(pick(items, members));
            continue;
        }
        for (const auto& sub : merge_same_floor(items, sub_sheets(items, members, cadj, badj),
                                                cadj, land))
            out.push_back(pick(items, sub));
    }
}

}  // namespace

std::vector<Group> storey_groups(const Group& strips) {
    UnionFind uf = node_partition(strips);
    std::vector<int> roots;
    std::map<int, Group> groups;
    for (size_t si = 0; si < strips.size(); ++si) {
        if (strips[si]->ei < 0) continue;
        const int r = uf.find((int)si);
        if (!groups.count(r)) roots.push_back(r);
        groups[r].push_back(strips[si]);
    }
    std::vector<Group> out;
    for (int r : roots) out.push_back(groups[r]);
    for (const UStrip* s : strips) {
        if (s->ei >= 0) continue;
        const int gi = best_group_for_door(out, ribbon_polygon(*s), s->a[2]);
        if (gi < 0) out.push_back({s});
        else out[(size_t)gi].push_back(s);
    }
    if (out.empty()) out.push_back(strips);
    return out;
}

std::vector<Group> split_plan_overlaps(const std::vector<Group>& groups,
                                       const Land* land) {
    std::vector<Group> out;
    for (const Group& g : groups) split_one_group(g, out, land);
    return out;
}

Geom same_surface_region(const Group& a, const Group& b, const GEOSGeometry* shared,
                         const Land* land) {
    if (b.empty()) return Geom();
    std::vector<const GEOSGeometry*> pa, pb;
    for (const UStrip* s : a) pa.push_back(ribbon_polygon(*s));
    for (const UStrip* s : b) pb.push_back(ribbon_polygon(*s));
    Group ab = a;
    ab.insert(ab.end(), b.begin(), b.end());
    const FlatStrips F = flat_strips(ab, land);
    const Land* gl = land_of(ab, land);
    const Land none;
    const std::vector<Geom> dup = same_surface_pieces(pa, pb, shared, F.strips,
                                                      F.prof.data(), gl ? *gl : none,
                                                      P().SAME_SURFACE_Z);
    if (dup.empty()) return Geom();
    std::vector<const GEOSGeometry*> raw;
    for (const Geom& g : dup) raw.push_back(g.get());
    return union_all(raw);
}

}  // namespace navgrow
