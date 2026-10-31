// Corridor strips (ribbons) as flat records, and the height a strip carries at
// a point -- shared by levels_at (grow.cpp) and the sheet split (sheets.cpp).
#pragma once

#include <algorithm>
#include <cmath>
#include <vector>

#include "pymath.h"

namespace navgrow {

// A cell's LAND heights (33x33, row = y, 128u spacing), interpolated on the
// diagonal world._land_tris cuts.  MUST mirror world.LandField.z.
struct Land {
    const double* g = nullptr;
    double ox = 0.0, oy = 0.0;
    // True inside this cell's own 4096u LAND square.
    bool covers(double x, double y) const {
        return x >= ox && x <= ox + 4096.0 && y >= oy && y <= oy + 4096.0;
    }
    double z(double x, double y) const {
        const double fx = std::min(32.0, std::max(0.0, (x - ox) / 128.0));
        const double fy = std::min(32.0, std::max(0.0, (y - oy) / 128.0));
        const int ix = std::min(31, (int)fx), iy = std::min(31, (int)fy);
        const double tx = fx - ix, ty = fy - iy;
        const double z00 = g[iy * 33 + ix], z10 = g[iy * 33 + ix + 1];
        const double z01 = g[(iy + 1) * 33 + ix];
        const double z11 = g[(iy + 1) * 33 + ix + 1];
        if (tx >= ty) return z00 + (z10 - z00) * tx + (z11 - z10) * ty;
        return z00 + (z11 - z01) * tx + (z01 - z00) * ty;
    }
};

struct Strip {
    // Centerline (a -> b), carrying the strip's own slope.
    double ax, ay, az, bx, by, bz;
    double half;                 // admission radius for a rectangle strip
    int poly_off, poly_n;        // outline vertices in `poly` (0 = rectangle)
    int prof_off, prof_n;        // height profile points (0 = straight chord)
    bool on_land = false;        // height follows the LAND sideways
    double x0, x1, y0, y1;       // XY bounds, for bucket rejection
};

// Strips from union_geom.flat_strips' (S, 12) rows: ax,ay,az,bx,by,bz,half,
// poly_off,poly_n,prof_off,prof_n,on_land.  `nf` profile points exist; a
// profile indexing past them is a caller bug and reads as "no profile".
inline std::vector<Strip> load_strips(const double* sp, size_t ns,
                                      const double* pp, size_t nf,
                                      bool have_land) {
    std::vector<Strip> strips(ns);
    for (size_t i = 0; i < ns; ++i) {
        const double* r = &sp[i * 12];
        Strip& S = strips[i];
        S.ax = r[0]; S.ay = r[1]; S.az = r[2];
        S.bx = r[3]; S.by = r[4]; S.bz = r[5];
        S.half = r[6];
        S.poly_off = (int)r[7];
        S.poly_n = (int)r[8];
        S.prof_off = (int)r[9];
        S.prof_n = (int)r[10];
        S.on_land = have_land && r[11] > 0.0;
        if (S.prof_n < 2 || (size_t)(S.prof_off + S.prof_n) > nf) {
            S.prof_off = 0; S.prof_n = 0;
        }
        if (S.poly_n > 0) {
            double x0 = 1e300, x1 = -1e300, y0 = 1e300, y1 = -1e300;
            for (int k = 0; k < S.poly_n; ++k) {
                const double x = pp[(size_t)(S.poly_off + k) * 2];
                const double y = pp[(size_t)(S.poly_off + k) * 2 + 1];
                x0 = std::min(x0, x); x1 = std::max(x1, x);
                y0 = std::min(y0, y); y1 = std::max(y1, y);
            }
            S.x0 = x0; S.x1 = x1; S.y0 = y0; S.y1 = y1;
        } else {
            S.x0 = std::min(S.ax, S.bx) - S.half;
            S.x1 = std::max(S.ax, S.bx) + S.half;
            S.y0 = std::min(S.ay, S.by) - S.half;
            S.y1 = std::max(S.ay, S.by) + S.half;
        }
    }
    return strips;
}

inline bool point_in_poly(const double* p, int n, double px, double py) {
    bool inside = false;
    for (int i = 0; i < n; ++i) {
        const int j = (i + 1) % n;
        const double x1 = p[i * 2], y1 = p[i * 2 + 1];
        const double x2 = p[j * 2], y2 = p[j * 2 + 1];
        if ((y1 > py) != (y2 > py)) {
            double dy = y2 - y1;
            if (dy == 0.0) dy = 1e-12;
            const double xin = x1 + (py - y1) * (x2 - x1) / dy;
            if (px < xin) inside = !inside;
        }
    }
    return inside;
}

inline double seg_dist(double px, double py, double ax, double ay,
                       double bx, double by) {
    const double dx = bx - ax, dy = by - ay;
    const double d2 = dx * dx + dy * dy;
    double t = (d2 < 1e-9) ? 0.0
                           : ((px - ax) * dx + (py - ay) * dy) / d2;
    t = std::max(0.0, std::min(1.0, t));
    return std::hypot(px - (ax + dx * t), py - (ay + dy * t));
}

// Height of strip S at (px, py) (union_geom._height_on): the closest point of
// its stair profile when it has one, else the LAND under a terrain strip, else
// the straight a->b chord.  PyExact squares profile distances as Python does;
// levels_at multiplies (its long-standing behavior, kept as shipped).
template <bool PyExact>
inline double strip_height(const Strip& S, const double* fp, const Land& land,
                           double px, double py) {
    if (S.prof_n >= 2) {
        const double* f = &fp[(size_t)S.prof_off * 3];
        double best_d2 = -1.0;
        double best_z = f[2];
        for (int k = 0; k < S.prof_n - 1; ++k) {
            const double qax = f[k * 3],     qay = f[k * 3 + 1];
            const double qaz = f[k * 3 + 2];
            const double qbx = f[k * 3 + 3], qby = f[k * 3 + 4];
            const double qbz = f[k * 3 + 5];
            const double dx = qbx - qax, dy = qby - qay;
            const double d2 = dx * dx + dy * dy;
            double t = (d2 < 1e-9) ? 0.0
                : ((px - qax) * dx + (py - qay) * dy) / d2;
            t = std::max(0.0, std::min(1.0, t));
            const double cx = qax + dx * t, cy = qay + dy * t;
            const double dd = PyExact
                ? py_square(px - cx) + py_square(py - cy)
                : (px - cx) * (px - cx) + (py - cy) * (py - cy);
            if (best_d2 < 0.0 || dd < best_d2) {
                best_d2 = dd;
                best_z = qaz + (qbz - qaz) * t;
            }
        }
        return best_z;
    }
    if (S.on_land) return land.z(px, py);
    const double dx = S.bx - S.ax, dy = S.by - S.ay;
    const double d2 = dx * dx + dy * dy;
    double t = (d2 < 1e-9) ? 0.0
        : ((px - S.ax) * dx + (py - S.ay) * dy) / d2;
    t = std::max(0.0, std::min(1.0, t));
    return S.az + (S.bz - S.az) * t;
}

}  // namespace navgrow
