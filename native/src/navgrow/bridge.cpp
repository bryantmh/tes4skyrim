// seeding.Ground's walk test, and the two passes that add pathgrid edges with
// it: obstacle_bridge.bridge_obstacles and seeding.seed_graph.

#include <algorithm>
#include <array>
#include <cmath>
#include <functional>
#include <map>
#include <set>
#include <tuple>

#include "params.h"
#include "world.h"

namespace navgrow {

using Box = std::array<double, 6>;
using Key = std::pair<int, int>;

// How far above its floor a generated point is tested for lying inside an object: a point standing ON a
// rock's top sits on that rock's surface, where the enclosure test reads half in.
constexpr double kEnclosedLift = 8.0;

bool Ground::blocked(double x, double y, double z, double ux, double uy) const {
    return geo.wall_at(x, y, ux, uy, -uy, ux, z + P().RIBBON_GROW_SLAB_Z_BOTTOM,
                       z + P().AGENT_HEIGHT, P().RIBBON_GROW_SLAB_DEPTH,
                       P().RIBBON_GROW_SLAB_HALF_WIDTH);
}

bool Ground::walk(const P3& a, const P3& b) {
    const double dx = b[0] - a[0], dy = b[1] - a[1];
    const double dist = py_hypot(dx, dy);
    if (dist < 1e-6) return true;
    const double ux = dx / dist, uy = dy / dist;
    double z = a[2];
    const long long steps = std::max(1LL, (long long)std::ceil(dist / P().WALK_STEP));
    for (long long k = 1; k <= steps; ++k) {
        const double x = a[0] + dx * (double)k / (double)steps;
        const double y = a[1] + dy * (double)k / (double)steps;
        double nz;
        if (!floor(x, y, z, &nz) || std::fabs(nz - z) > P().MAX_CLIMB || blocked(x, y, nz, ux, uy)
            || enclosed(x, y, nz + kEnclosedLift))
            return false;
        z = nz;
    }
    return std::fabs(z - b[2]) <= P().MAX_CLIMB;
}

namespace {

// --- obstacle_bridge ----------------------------------------------------------

bool overlap(const Box& a, const Box& b) {
    return a[0] <= b[3] && b[0] <= a[3] && a[1] <= b[4] && b[1] <= a[4];
}

Box merge(const Box& a, const Box& b) {
    return {std::min(a[0], b[0]), std::min(a[1], b[1]), std::min(a[2], b[2]),
            std::max(a[3], b[3]), std::max(a[4], b[4]), std::max(a[5], b[5])};
}

// _clusters: the boxes merged wherever they overlap in plan.
std::vector<Box> clusters(const std::vector<Box>& boxes) {
    std::vector<Box> out;
    for (Box box : boxes) {
        for (bool touched = true; touched;) {
            touched = false;
            std::vector<Box> keep;
            for (const Box& c : out) {
                if (overlap(c, box)) touched = true;
                else keep.push_back(c);
            }
            if (!touched) break;
            for (const Box& c : out)
                if (overlap(c, box)) box = merge(box, c);
            out.swap(keep);
        }
        out.push_back(box);
    }
    return out;
}

// _labels: each node's component label, the lowest node index it connects to.
std::vector<int> labels(size_t count, const std::vector<std::pair<int, int>>& edges) {
    std::vector<std::vector<int>> adj(count);
    for (const auto& e : edges) {
        adj[(size_t)e.first].push_back(e.second);
        adj[(size_t)e.second].push_back(e.first);
    }
    std::vector<int> label(count, -1);
    for (size_t start = 0; start < count; ++start) {
        if (label[start] >= 0) continue;
        label[start] = (int)start;
        std::vector<int> todo{(int)start};
        while (!todo.empty()) {
            const int at = todo.back();
            todo.pop_back();
            for (int n : adj[(size_t)at])
                if (label[(size_t)n] < 0) {
                    label[(size_t)n] = (int)start;
                    todo.push_back(n);
                }
        }
    }
    return label;
}

double gap(const Box& box, double x, double y) {
    return py_hypot(std::max(std::max(box[0] - x, 0.0), x - box[3]),
                    std::max(std::max(box[1] - y, 0.0), y - box[4]));
}

// _nearest_per_component: each component's node nearest the box, sorted.
std::vector<int> nearest_per_component(const std::vector<P3>& points,
                                       const std::vector<int>& label, const Box& box) {
    const double reach = 0.5 * py_hypot(box[3] - box[0], box[4] - box[1]);
    std::map<int, std::pair<double, int>> nearest;
    for (size_t i = 0; i < points.size(); ++i) {
        const double g = gap(box, points[i][0], points[i][1]);
        const double z = points[i][2];
        if (!(g <= reach && box[2] - P().AGENT_HEIGHT <= z && z <= box[5])) continue;
        auto it = nearest.find(label[i]);
        if (it == nearest.end()) nearest[label[i]] = {g, (int)i};
        else if (g < it->second.first) it->second = {g, (int)i};
    }
    std::vector<int> out;
    for (const auto& kv : nearest) out.push_back(kv.second.second);
    std::sort(out.begin(), out.end());
    return out;
}

// _bridge_cluster: join the components reaching `box` where an actor can walk.
int bridge_cluster(const std::vector<P3>& points, std::vector<std::pair<int, int>>& edges,
                   const Box& box, Ground& ground) {
    std::vector<int> label = labels(points.size(), edges);
    const std::vector<int> nodes = nearest_per_component(points, label, box);
    std::vector<std::tuple<double, int, int>> pairs;
    for (size_t i = 0; i < nodes.size(); ++i)
        for (size_t j = i + 1; j < nodes.size(); ++j)
            pairs.emplace_back(py_dist3(points[(size_t)nodes[i]].data(), points[(size_t)nodes[j]].data()),
                               nodes[i], nodes[j]);
    std::sort(pairs.begin(), pairs.end());
    int added = 0;
    for (const auto& p : pairs) {
        const int a = std::get<1>(p), b = std::get<2>(p);
        if (label[(size_t)a] == label[(size_t)b] || !ground.walk(points[(size_t)a], points[(size_t)b]))
            continue;
        edges.emplace_back(a, b);
        ++added;
        const int joined = label[(size_t)b];
        for (int& lab : label)
            if (lab == joined) lab = label[(size_t)a];
    }
    return added;
}

// --- seeding flood --------------------------------------------------------------

// seeding._lattice: lattice nodes standing on open walkable ground, none inside a placed object's
// collision (terrain buried under a rock).
std::map<Key, P3> lattice(Ground& ground, double ox, double oy) {
    const int count = (int)py_floordiv(4096.0, P().LATTICE_SPACING);
    std::map<Key, P3> nodes;
    for (int j = 0; j < count; ++j)
        for (int i = 0; i < count; ++i) {
            const double x = ox + (i + 0.5) * P().LATTICE_SPACING;
            const double y = oy + (j + 0.5) * P().LATTICE_SPACING;
            const double near_z = ground.land ? ground.land->z(x, y) : 0.0;
            double z;
            if (ground.floor(x, y, near_z, &z) && !ground.blocked(x, y, z, 1.0, 0.0)
                && !ground.enclosed(x, y, z + kEnclosedLift))
                nodes[{i, j}] = {x, y, z};
        }
    return nodes;
}

// seeding._lattice_edges: walkable links between orthogonal neighbours.
std::vector<std::pair<Key, Key>> lattice_edges(Ground& ground, const std::map<Key, P3>& nodes) {
    std::vector<std::pair<Key, Key>> out;
    for (const auto& kv : nodes) {
        const Key k = kv.first;
        for (const Key nb : {Key{k.first + 1, k.second}, Key{k.first, k.second + 1}}) {
            auto it = nodes.find(nb);
            if (it != nodes.end() && ground.walk(kv.second, it->second)) out.emplace_back(k, nb);
        }
    }
    return out;
}

// seeding._seed_links: the seeds on the ground and their (seed, lattice key) links.
std::vector<std::pair<int, Key>> seed_links(Ground& ground, const std::vector<P3>& seeds,
                                            const std::map<Key, P3>& nodes,
                                            std::vector<P3>& placed) {
    std::vector<std::pair<int, Key>> links;
    for (const P3& s : seeds) {
        double z;
        if (!ground.floor(s[0], s[1], s[2], &z)) continue;
        const P3 seed{s[0], s[1], z};
        std::vector<std::tuple<double, Key>> near;
        for (const auto& kv : nodes)
            near.emplace_back(py_hypot(kv.second[0] - s[0], kv.second[1] - s[1]), kv.first);
        std::sort(near.begin(), near.end());
        std::vector<Key> reach;
        for (size_t n = 0; n < near.size() && n < 4; ++n)
            if (std::get<0>(near[n]) <= P().LATTICE_SPACING * 1.5
                && ground.walk(seed, nodes.at(std::get<1>(near[n]))))
                reach.push_back(std::get<1>(near[n]));
        if (reach.empty()) continue;
        for (const Key& k : reach) links.emplace_back((int)placed.size(), k);
        placed.push_back(seed);
    }
    return links;
}

// seeding._flooded: the lattice keys reachable from any seed.
std::set<Key> flooded(const std::vector<std::pair<int, Key>>& links,
                      const std::vector<std::pair<Key, Key>>& ledges) {
    std::map<Key, std::vector<Key>> adj;
    for (const auto& e : ledges) {
        adj[e.first].push_back(e.second);
        adj[e.second].push_back(e.first);
    }
    std::set<Key> reached;
    for (const auto& l : links) reached.insert(l.second);
    std::vector<Key> todo(reached.begin(), reached.end());
    while (!todo.empty()) {
        const Key at = todo.back();
        todo.pop_back();
        auto it = adj.find(at);
        if (it == adj.end()) continue;
        for (const Key& nb : it->second)
            if (reached.insert(nb).second) todo.push_back(nb);
    }
    return reached;
}

// The flood over `nodes` from `seeds`: the seeds that link in, then the reached nodes, with their links
// and lattice edges (empty when nothing floods).
void flood_lattice(const std::vector<P3>& seeds, Ground& ground, const std::map<Key, P3>& nodes,
                   std::vector<P3>& points, std::vector<std::pair<int, int>>& edges) {
    std::vector<P3> placed;
    const auto links = seed_links(ground, seeds, nodes, placed);
    const auto ledges = lattice_edges(ground, nodes);
    const std::set<Key> reached = flooded(links, ledges);
    std::map<Key, int> index;
    points = placed;
    for (const Key& k : reached) {
        index[k] = (int)points.size();
        points.push_back(nodes.at(k));
    }
    edges.clear();
    for (const auto& l : links) edges.emplace_back(l.first, index[l.second]);
    for (const auto& e : ledges)
        if (reached.count(e.first) && reached.count(e.second))
            edges.emplace_back(index[e.first], index[e.second]);
    if (edges.empty()) points.clear();
}

// True when a face of `grid` lies over (x, y) above z and below an actor's height over it.
bool covered_above(const TriGrid& grid, double x, double y, double z) {
    std::vector<double> zs;
    bucket_heights(grid, x, y, zs);
    for (double h : zs)
        if (h > z + 1.0 && h < z + P().AGENT_HEIGHT) return true;
    return false;
}

}  // namespace

bool Ground::open_land(double x, double y, double z) const {
    return land && std::fabs(z - land->z(x, y)) <= 1.0 && !covered_above(geo.walk, x, y, z)
           && !covered_above(geo.wall, x, y, z);
}

int bridge_obstacles(const std::vector<P3>& points, std::vector<std::pair<int, int>>& edges,
                     const std::vector<PlacedRef>& obstacles, Ground& ground) {
    std::vector<Box> boxes;
    for (const PlacedRef& r : obstacles) {
        Box b;
        if (footprint(r, b.data())) boxes.push_back(b);
    }
    if (boxes.empty() || points.empty()) return 0;
    int added = 0;
    for (const Box& box : clusters(boxes)) added += bridge_cluster(points, edges, box, ground);
    return added;
}

void seed_graph(const std::vector<P3>& seeds, Ground& ground, double ox, double oy,
                std::vector<P3>& points, std::vector<std::pair<int, int>>& edges) {
    flood_lattice(seeds, ground, lattice(ground, ox, oy), points, edges);
}

void gap_graph(const std::vector<P3>& seeds, Ground& ground, double ox, double oy,
               const std::function<bool(const P3&)>& covered,
               std::vector<P3>& points, std::vector<std::pair<int, int>>& edges) {
    std::map<Key, P3> nodes = lattice(ground, ox, oy);
    for (auto it = nodes.begin(); it != nodes.end();)
        it = covered(it->second) ? nodes.erase(it) : std::next(it);
    flood_lattice(seeds, ground, nodes, points, edges);
}

}  // namespace navgrow
