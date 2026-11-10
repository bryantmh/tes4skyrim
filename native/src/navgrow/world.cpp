// world.gather_cell_geometry, bit-exact.  numpy's (N,3,3) @ (3,3) runs through
// BLAS, which accumulates each output as fma(a2, r2, fma(a1, r1, fma(a0, r0, 0)));
// np.cross and np.linalg.norm(axis=1) are plain sequential arithmetic.

#include "world.h"

#include <algorithm>
#include <cmath>

#include "params.h"

namespace navgrow {

namespace {

// world.place: one ref's soup in world space, appended to `out`.
void place(const SoupView& s, const PlacedRef& r, std::vector<double>& out) {
    const size_t base = out.size();
    out.resize(base + s.n / 9 * 9);
    double* o = &out[base];
    for (size_t v = 0; v + 3 <= s.n / 9 * 9; v += 3) {
        const double a0 = s.at(v) * r.scale, a1 = s.at(v + 1) * r.scale;
        const double a2 = s.at(v + 2) * r.scale;
        for (int k = 0; k < 3; ++k) {
            const double* m = &r.rot[k * 3];
            o[v + k] = std::fma(a2, m[2], std::fma(a1, m[1], std::fma(a0, m[0], 0.0))) + r.pos[k];
        }
    }
}

// The highest Z of soup rows [from, end), or -inf.
double top_z(const std::vector<double>& s, size_t from) {
    double t = -INFINITY;
    for (size_t i = from + 2; i < s.size(); i += 3) t = std::max(t, s[i]);
    return t;
}

// world._split_by_slope over the triangles of `soup`, appended to walk / block.
void split_by_slope(const std::vector<double>& soup, std::vector<double>& walk,
                    std::vector<double>* block) {
    const double thr = crt_cos(P().MAX_SLOPE_DEG * (3.14159265358979323846 / 180.0));
    for (size_t t = 0; t + 9 <= soup.size(); t += 9) {
        const double* p = &soup[t];
        const double e1[3] = {p[3] - p[0], p[4] - p[1], p[5] - p[2]};
        const double e2[3] = {p[6] - p[0], p[7] - p[1], p[8] - p[2]};
        const double n0 = e1[1] * e2[2] - e1[2] * e2[1];
        const double n1 = e1[2] * e2[0] - e1[0] * e2[2];
        const double n2 = e1[0] * e2[1] - e1[1] * e2[0];
        const double ln = std::sqrt((n0 * n0 + n1 * n1) + n2 * n2);
        if (!(ln > 1e-9)) continue;
        std::vector<double>* dst = std::fabs(n2) / ln >= thr ? &walk : block;
        if (dst) dst->insert(dst->end(), p, p + 9);
    }
}

// world._land_tris: the LAND height field, t1 = (v00, v10, v11) rows then t2 = (v00, v11, v01).
std::vector<double> land_tris(const Land& L) {
    std::vector<double> out;
    out.reserve(32 * 32 * 2 * 9);
    auto vert = [&](int row, int col) {
        out.push_back(L.ox + (double)col * 128.0);
        out.push_back(L.oy + (double)row * 128.0);
        out.push_back(L.g[row * 33 + col]);
    };
    for (int half = 0; half < 2; ++half)
        for (int r = 0; r < 32; ++r)
            for (int c = 0; c < 32; ++c) {
                vert(r, c);
                if (half == 0) { vert(r, c + 1); vert(r + 1, c + 1); }
                else { vert(r + 1, c + 1); vert(r + 1, c); }
            }
    return out;
}

}  // namespace

// One object's walkable and blocking triangles joined, with their bounding box.
static Solid solid_of(const std::vector<double>& w, const std::vector<double>& b) {
    Solid s;
    s.tris = w;
    s.tris.insert(s.tris.end(), b.begin(), b.end());
    for (size_t i = 0; i < s.tris.size(); ++i) {
        const int k = (int)(i % 3);
        const double v = s.tris[i];
        s.box[k] = i < 3 ? v : std::min(s.box[k], v);
        s.box[k + 3] = i < 3 ? v : std::max(s.box[k + 3], v);
    }
    return s;
}

// Generalised winding number of p against triangles (9 doubles each): 1 inside a closed shape, 0 outside;
// an open-bottomed shape (a rock sunk into the ground) still reads well over 0.5 inside.
static double winding(const std::vector<double>& t, double px, double py, double pz) {
    double sum = 0.0;
    for (size_t i = 0; i + 9 <= t.size(); i += 9) {
        const double a[3] = {t[i] - px, t[i + 1] - py, t[i + 2] - pz};
        const double b[3] = {t[i + 3] - px, t[i + 4] - py, t[i + 5] - pz};
        const double c[3] = {t[i + 6] - px, t[i + 7] - py, t[i + 8] - pz};
        const double la = std::sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
        const double lb = std::sqrt(b[0] * b[0] + b[1] * b[1] + b[2] * b[2]);
        const double lc = std::sqrt(c[0] * c[0] + c[1] * c[1] + c[2] * c[2]);
        const double det = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                           + a[2] * (b[0] * c[1] - b[1] * c[0]);
        const double ab = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
        const double bc = b[0] * c[0] + b[1] * c[1] + b[2] * c[2];
        const double ca = c[0] * a[0] + c[1] * a[1] + c[2] * a[2];
        sum += 2.0 * std::atan2(det, la * lb * lc + ab * lc + bc * la + ca * lb);
    }
    return sum / (4.0 * 3.14159265358979323846);
}

bool Ground::enclosed(double x, double y, double z) const {
    if (!solids) return false;
    for (const Solid& s : *solids)
        if (s.box[0] <= x && x <= s.box[3] && s.box[1] <= y && y <= s.box[4] && s.box[2] <= z && z <= s.box[5]
            && winding(s.tris, x, y, z) >= 0.5)
            return true;
    return false;
}

Soups gather_cell(const std::vector<PlacedRef>& refs, const Land* land, const double* water_z) {
    std::vector<double> walk_faces, block_faces, door_faces;
    std::vector<Solid> solids;
    for (const PlacedRef& r : refs) {
        std::vector<double> w, b;
        place(r.w, r, w);
        place(r.b, r, b);
        if (w.empty() && b.empty()) continue;
        if (water_z && std::max(top_z(w, 0), top_z(b, 0)) < *water_z - P().AGENT_HEIGHT) continue;
        std::vector<double>& wd = r.door ? door_faces : walk_faces;
        std::vector<double>& bd = r.door ? door_faces : block_faces;
        wd.insert(wd.end(), w.begin(), w.end());
        bd.insert(bd.end(), b.begin(), b.end());
        if (!r.door) solids.push_back(solid_of(w, b));
    }
    Soups S;
    S.solids = std::move(solids);
    walk_faces.insert(walk_faces.end(), block_faces.begin(), block_faces.end());
    split_by_slope(walk_faces, S.walk, &S.block);
    split_by_slope(door_faces, S.walk, nullptr);
    S.land_from = S.walk.size() / 9;
    if (land && land->g) split_by_slope(land_tris(*land), S.walk, &S.block);
    return S;
}

bool footprint(const PlacedRef& ref, double box[6]) {
    PlacedRef r = ref;
    r.door = false;
    const Soups S = gather_cell({r}, nullptr, nullptr);
    bool any = false;
    for (const std::vector<double>* s : {&S.walk, &S.block})
        for (size_t i = 0; i + 3 <= s->size(); i += 3)
            for (int k = 0; k < 3; ++k) {
                const double v = (*s)[i + k];
                box[k] = any ? std::min(box[k], v) : v;
                box[k + 3] = any ? std::max(box[k + 3], v) : v;
                if (k == 2) any = true;
            }
    return any;
}

}  // namespace navgrow
