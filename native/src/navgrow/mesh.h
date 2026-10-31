// Mesh types and topology helpers shared by the native cleanup stages.
//
// Python dicts iterate in insertion order, and the reference walks its edge
// dicts that way, so EdgeOwners keeps first-seen order alongside a hash index.
#pragma once

#include <array>
#include <cmath>
#include <cstdint>
#include <unordered_map>
#include <utility>
#include <vector>

#include "pymath.h"

namespace navgrow {

// A mesh vertex.  `znp` is set where the reference holds Z as a numpy.float64
// (a sampler produced it, or it was averaged from one): Python's sum() rounds a
// float64 differently from a plain float.
struct Vert {
    double x, y, z;
    bool znp;
};

using Tri = std::array<int, 3>;
using P3 = std::array<double, 3>;
using Key = std::pair<int, int>;

inline Key edge_key(int a, int b) { return a < b ? Key(a, b) : Key(b, a); }

inline uint64_t key64(int a, int b) {
    if (a > b) std::swap(a, b);
    return ((uint64_t)(uint32_t)a << 32) | (uint32_t)b;
}

inline bool distinct(const Tri& t) {
    return t[0] != t[1] && t[1] != t[2] && t[0] != t[2];
}

inline bool has(const Tri& t, int v) {
    return t[0] == v || t[1] == v || t[2] == v;
}

// Edge -> owning (triangle, side) pairs, in the order a Python dict built by
// one pass over `tris` would hold them.
struct EdgeOwners {
    struct Entry {
        Key key;
        std::vector<int> tris;
        std::vector<int> sides;
    };
    std::vector<Entry> order;
    std::unordered_map<uint64_t, size_t> slot;

    explicit EdgeOwners(const std::vector<Tri>& tris) {
        slot.reserve(tris.size() * 2);
        for (size_t ti = 0; ti < tris.size(); ++ti)
            for (int k = 0; k < 3; ++k) add(tris[ti][k], tris[ti][(k + 1) % 3], (int)ti, k);
    }

    void add(int a, int b, int ti, int side) {
        auto ins = slot.emplace(key64(a, b), order.size());
        if (ins.second) order.push_back({edge_key(a, b), {}, {}});
        Entry& e = order[ins.first->second];
        e.tris.push_back(ti);
        e.sides.push_back(side);
    }

    const Entry* find(int a, int b) const {
        auto it = slot.find(key64(a, b));
        return it == slot.end() ? nullptr : &order[it->second];
    }

    size_t count(int a, int b) const {
        const Entry* e = find(a, b);
        return e ? e->tris.size() : 0;
    }
};

// clean_validate.edge_adjacency: neighbour per side, -1 for none; an edge
// with three or more owners links none of them.
inline std::vector<std::array<int, 3>> edge_adjacency(const std::vector<Tri>& tris) {
    std::vector<std::array<int, 3>> adj(tris.size(), {-1, -1, -1});
    const EdgeOwners own(tris);
    for (const auto& e : own.order) {
        if (e.tris.size() != 2) continue;
        adj[(size_t)e.tris[0]][e.sides[0]] = e.tris[1];
        adj[(size_t)e.tris[1]][e.sides[1]] = e.tris[0];
    }
    return adj;
}

// clean_validate.components: triangle-index lists over edge adjacency, each
// in the reference's stack-walk order.
inline std::vector<std::vector<int>> components(const std::vector<Tri>& tris) {
    const auto adj = edge_adjacency(tris);
    std::vector<char> seen(tris.size(), 0);
    std::vector<std::vector<int>> comps;
    std::vector<int> stack;
    for (size_t s = 0; s < tris.size(); ++s) {
        if (seen[s]) continue;
        std::vector<int> comp;
        stack.assign(1, (int)s);
        seen[s] = 1;
        while (!stack.empty()) {
            const int x = stack.back();
            stack.pop_back();
            comp.push_back(x);
            for (int nb : adj[(size_t)x]) {
                if (nb >= 0 && !seen[(size_t)nb]) {
                    seen[(size_t)nb] = 1;
                    stack.push_back(nb);
                }
            }
        }
        comps.push_back(std::move(comp));
    }
    return comps;
}

inline size_t component_count(const std::vector<Tri>& tris) {
    return components(tris).size();
}

// Twice the signed plan area of p, q, r (clean_decimate._area2).
inline double plan_area2(const Vert& p, const Vert& q, const Vert& r) {
    return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
}

// clean_decimate._mesh_area: builtin sum() of |area2|, halved.
inline double mesh_plan_area(const std::vector<Vert>& V, const std::vector<Tri>& T) {
    PySum s;
    for (const Tri& t : T)
        s.add(std::fabs(plan_area2(V[(size_t)t[0]], V[(size_t)t[1]], V[(size_t)t[2]])));
    return s.value() * 0.5;
}

}  // namespace navgrow
