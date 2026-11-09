// door_reach.door_reach: walkable lattice paths from door seeds into a cell's
// pathgrid, or, with no pathgrid, everything the seeds flood.
// See: docs/commentary/tes5_import_navmesh.md#door-reach

#include <algorithm>
#include <cmath>
#include <deque>
#include <map>
#include <tuple>

#include "params.h"
#include "world.h"

namespace navgrow {

namespace {

// A lattice node: steps from the flood's origin, and its storey band.
using Node = std::tuple<int, int, int>;

struct Flood {
    std::map<Node, int> index;      // node -> position in `at`
    std::vector<P3> at;
    std::vector<Node> key;
    std::vector<int> parent;        // tree parent, -1 for a seed
    std::vector<int> depth;
    std::vector<std::pair<int, int>> links;    // the tree's (parent, child) links

    int add(const Node& k, const P3& p, int from) {
        index[k] = (int)at.size();
        at.push_back(p);
        key.push_back(k);
        parent.push_back(from);
        depth.push_back(from < 0 ? 0 : depth[(size_t)from] + 1);
        if (from >= 0) links.emplace_back(from, (int)at.size() - 1);
        return (int)at.size() - 1;
    }
};

// How far above a step's height its floor is sampled: a stair tread over a floor that
// runs on beneath the stair is taken, a ceiling an agent's height up is not.
double climb() { return 0.5 * P().MAX_CLIMB; }

// Ground::walk for the door flood, from `a` to b's plan position: a sample every
// half WALK_STEP, so every stair tread is stood on; each sample's floor nearest
// climb() above the last; and a sample that lands in a hairline gap between
// treads nudged along the way. *end gets the floor height it arrives at.
bool stair_walk(Ground& ground, const P3& a, const P3& b, double* end) {
    const double dx = b[0] - a[0], dy = b[1] - a[1];
    const double dist = py_hypot(dx, dy);
    *end = a[2];
    if (dist < 1e-6) return true;
    const double ux = dx / dist, uy = dy / dist;
    const long long steps = std::max(1LL, (long long)std::ceil(dist / (0.5 * P().WALK_STEP)));
    double z = a[2];
    for (long long k = 1; k <= steps; ++k) {
        const double t = dist * (double)k / (double)steps;
        bool stood = false;
        double nz = z;
        for (const double nudge : {0.0, 4.0, -4.0}) {
            const double x = a[0] + ux * (t + nudge), y = a[1] + uy * (t + nudge);
            if (ground.floor(x, y, z + climb(), &nz) && std::fabs(nz - z) <= P().MAX_CLIMB
                && !ground.blocked(x, y, nz, ux, uy)) {
                stood = true;
                break;
            }
        }
        if (!stood) return false;
        z = nz;
    }
    *end = z;
    return true;
}

Node node_of(const P3& p, const P3& origin, double step) {
    return {(int)std::lround((p[0] - origin[0]) / step), (int)std::lround((p[1] - origin[1]) / step),
            (int)std::lround(p[2] / P().AGENT_HEIGHT)};
}

// The anchor `p` walks to within reach, or -1.
int joined(const P3& p, const std::vector<P3>& anchors, Ground& ground, double reach) {
    int best = -1;
    double best_d = reach;
    for (size_t a = 0; a < anchors.size(); ++a) {
        const double d = py_hypot(anchors[a][0] - p[0], anchors[a][1] - p[1]);
        double z;
        if (d <= best_d && std::fabs(anchors[a][2] - p[2]) <= P().SEED_Z_TOLERANCE
            && stair_walk(ground, p, anchors[a], &z) && std::fabs(z - anchors[a][2]) <= P().SEED_Z_TOLERANCE) {
            best = (int)a;
            best_d = d;
        }
    }
    return best;
}

// Breadth-first flood from `seeds` (each on the floor). Stops at the first node
// that joins an anchor and returns it with its anchor, else (-1, -1) once the
// flood is exhausted or holds max_nodes.
std::pair<int, int> flood(Flood& f, const std::vector<P3>& seeds, const std::vector<P3>& anchors,
                          Ground& ground) {
    const double step = P().DOOR_REACH_SPACING;
    const P3 origin = seeds.front();
    std::deque<int> todo;
    for (const P3& s : seeds) {
        const Node k = node_of(s, origin, step);
        if (!f.index.count(k)) todo.push_back(f.add(k, s, -1));
    }
    const int dirs[8][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}, {1, 1}, {-1, 1}, {1, -1}, {-1, -1}};
    while (!todo.empty()) {
        const int cur = todo.front();
        todo.pop_front();
        const P3 here = f.at[(size_t)cur];
        if (!anchors.empty()) {
            const int a = joined(here, anchors, ground, step * 1.5);
            if (a >= 0) return {cur, a};
        }
        if ((double)f.at.size() >= P().DOOR_REACH_MAX_NODES) continue;
        const Node k = node_of(here, origin, step);
        for (const auto& d : dirs) {
            const double x = origin[0] + (std::get<0>(k) + d[0]) * step;
            const double y = origin[1] + (std::get<1>(k) + d[1]) * step;
            double z;
            if (!stair_walk(ground, here, {x, y, here[2]}, &z)) continue;
            const P3 next{x, y, z};
            const Node nk = node_of(next, origin, step);
            if (!f.index.count(nk)) todo.push_back(f.add(nk, next, cur));
        }
    }
    return {-1, -1};
}

bool straight(Ground& ground, const P3& a, const P3& b) {
    double z;
    return stair_walk(ground, a, b, &z) && std::fabs(z - b[2]) <= P().MAX_CLIMB;
}

// The tree path from node u to node v; empty when they are in different trees.
std::vector<int> tree_path(const Flood& f, int u, int v) {
    std::vector<int> up, down;
    while (u != v) {
        if (u < 0 || v < 0) return {};
        if (f.depth[(size_t)u] >= f.depth[(size_t)v]) {
            up.push_back(u);
            u = f.parent[(size_t)u];
        } else {
            down.push_back(v);
            v = f.parent[(size_t)v];
        }
    }
    up.push_back(u);
    up.insert(up.end(), down.rbegin(), down.rend());
    return up;
}

// `path` string-pulled: from each kept node, the farthest of the next 16 an actor
// walks straight to.
std::vector<int> pull(const Flood& f, const std::vector<int>& path, Ground& ground) {
    std::vector<int> out{path.front()};
    for (size_t at = 0; at + 1 < path.size();) {
        size_t next = at + 1;
        for (size_t j = std::min(path.size() - 1, at + 16); j > at + 1; --j)
            if (straight(ground, f.at[(size_t)path[at]], f.at[(size_t)path[j]])) {
                next = j;
                break;
            }
        out.push_back(path[next]);
        at = next;
    }
    return out;
}

// The output graph, built from flood nodes: each node once, chains as edges.
struct Out {
    std::vector<P3>& points;
    std::vector<std::pair<int, int>>& edges;
    std::map<int, int> kept;

    int add(const Flood& f, int n) {
        auto it = kept.find(n);
        if (it != kept.end()) return it->second;
        kept[n] = (int)points.size();
        points.push_back(f.at[(size_t)n]);
        return (int)points.size() - 1;
    }
    void chain(const Flood& f, const std::vector<int>& nodes) {
        for (size_t k = 1; k < nodes.size(); ++k) edges.emplace_back(add(f, nodes[k - 1]), add(f, nodes[k]));
    }
};

int floordiv(int a, int b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }

struct Blocks {
    std::map<Node, int> rep;        // block -> its flood node nearest the block's center
    std::map<Node, Node> up;        // union-find over blocks

    Node find(Node b) {
        while (up[b] != b) b = up[b] = up[up[b]];
        return b;
    }
};

// The flood's blocks of `span` × `span` lattice steps per storey, and each node's block.
Blocks blocks_of(const Flood& f, int span, std::vector<Node>& block) {
    Blocks out;
    auto off = [span](const Node& k, const Node& b) {
        const double ci = std::get<0>(b) * span + 0.5 * (span - 1), cj = std::get<1>(b) * span + 0.5 * (span - 1);
        return py_hypot(std::get<0>(k) - ci, std::get<1>(k) - cj);
    };
    for (size_t n = 0; n < f.at.size(); ++n) {
        const Node& k = f.key[n];
        const Node b{floordiv(std::get<0>(k), span), floordiv(std::get<1>(k), span), std::get<2>(k)};
        block.push_back(b);
        auto it = out.rep.find(b);
        if (it == out.rep.end()) out.up[b] = b;
        if (it == out.rep.end() || off(k, b) < off(f.key[(size_t)it->second], b)) out.rep[b] = (int)n;
    }
    return out;
}

// The whole flood thinned to one node per DOOR_REACH_THIN_SPACING block and storey:
// neighboring blocks' nodes linked where an actor walks straight between them, and
// every tree link those leave apart kept as its string-pulled tree path.
void thin(const Flood& f, Ground& ground, Out& out) {
    const int span = std::max(1, (int)std::lround(P().DOOR_REACH_THIN_SPACING / P().DOOR_REACH_SPACING));
    std::vector<Node> block;
    Blocks bl = blocks_of(f, span, block);
    for (const auto& kv : bl.rep) {
        const auto& [bi, bj, bs] = kv.first;
        const int dirs[4][2] = {{1, 0}, {0, 1}, {1, 1}, {1, -1}};
        for (const auto& d : dirs)
            for (int ds = -1; ds <= 1; ++ds) {
                auto nb = bl.rep.find({bi + d[0], bj + d[1], bs + ds});
                if (nb == bl.rep.end() || !straight(ground, f.at[(size_t)kv.second], f.at[(size_t)nb->second]))
                    continue;
                out.chain(f, {kv.second, nb->second});
                bl.up[bl.find(kv.first)] = bl.find(nb->first);
            }
    }
    for (const auto& [p, c] : f.links) {
        const Node a = bl.find(block[(size_t)p]), b = bl.find(block[(size_t)c]);
        const std::vector<int> left = tree_path(f, bl.rep[block[(size_t)p]], p);
        const std::vector<int> right = tree_path(f, c, bl.rep[block[(size_t)c]]);
        if (a == b || left.empty() || right.empty()) continue;
        std::vector<int> path = pull(f, left, ground);
        const std::vector<int> rest = pull(f, right, ground);
        path.insert(path.end(), rest.begin(), rest.end());
        out.chain(f, path);
        bl.up[a] = b;
    }
}

// A seed's open floor: right under it, else the nearest within DOOR_SEED_SEARCH in
// plan, never far below it (a door's frame stands in a wall or past a ledge, a
// landing marker floats above its floor). False when none.
bool place_seed(const P3& s, Ground& ground, P3* out) {
    const double below = P().DOOR_SEED_DROP, above = 2.0 * P().MAX_CLIMB;
    const double ring = P().WALK_STEP;
    for (double r = 0.0; r <= P().DOOR_SEED_SEARCH; r += ring) {
        const int around = r == 0.0 ? 1 : std::max(8, (int)std::ceil(6.283185307179586 * r / ring));
        for (int k = 0; k < around; ++k) {
            const double a = 6.283185307179586 * k / around;
            const double x = s[0] + r * std::cos(a), y = s[1] + r * std::sin(a);
            double z;
            if (ground.floor(x, y, s[2], &z) && z >= s[2] - below && z <= s[2] + above
                && !ground.blocked(x, y, z, 1.0, 0.0)) {
                *out = {x, y, z};
                return true;
            }
        }
    }
    return false;
}

}  // namespace

void door_reach(const std::vector<P3>& seeds, const std::vector<P3>& anchors, Ground& ground,
                std::vector<P3>& points, std::vector<std::pair<int, int>>& edges,
                std::vector<std::pair<int, int>>& joins) {
    std::vector<P3> placed;
    for (const P3& s : seeds) {
        P3 at;
        if (place_seed(s, ground, &at)) placed.push_back(at);
    }
    if (placed.empty()) return;
    if (anchors.empty()) {
        Flood f;
        flood(f, placed, anchors, ground);
        Out out{points, edges, {}};
        if (!f.links.empty()) thin(f, ground, out);
        return;
    }
    for (const P3& s : placed) {
        Flood f;
        const auto hit = flood(f, {s}, anchors, ground);
        if (hit.first < 0) continue;
        Out out{points, edges, {}};
        std::vector<int> path;
        for (int n = hit.first; n >= 0; n = f.parent[(size_t)n]) path.push_back(n);
        const std::vector<int> pulled = pull(f, path, ground);
        out.chain(f, pulled);
        joins.emplace_back(out.add(f, hit.first), hit.second);
    }
}

}  // namespace navgrow
