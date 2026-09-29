r"""Ground-truth SpeedTree branch geometry, straight from Oblivion's own engine.

`asset_convert/speedtree/spt_generator.py` REIMPLEMENTS SpeedTreeRT 4.x in Python.  This
module instead runs the real thing: `native/dist/spt_engine_dump.exe` maps
the configured Oblivion.exe as DATA (the game is never launched) and calls
`CSpeedTreeRT::LoadTree` / `Compute` / `GetGeometry`, dumping the engine's own
branch vertex buffers.  Those buffers are turned into the same `TreeGeometry`
the Python generator produces, so everything downstream -- texture resolution,
the bark normal map, `BSXFlags`, and the Havok collision shape -- is shared.

**This is the DEFAULT geometry path.**  `spt_generator.build_tree` is the
FALLBACK: it is untouched, needs no executable, and runs per tree whenever the
engine path is unavailable (no Oblivion.exe configured, harness missing, or the
dump fails for that tree).  Pass `use_engine=False` -- or
`--no-engine-branches` / `"speedtreeEngineBranches": false` -- to force it
everywhere.  See `build_tree_engine`.

Leaves are NOT taken from here yet -- the engine's leaf buffers are billboards,
which Skyrim cannot render.  Leaves continue to come from the Python generator
and are grafted onto the engine bark.

Dump format (written by spt_engine_dump.cpp):

    'SPTG'                     4 bytes
    vertexCount   uint32
    coords        float32[n*3]      (engine units; x10 -> generator world)
    normals       float32[n*3]
    texcoords0    float32[n*2]
    stripCount    uint32
    per strip:  indexCount uint32, indices uint32[indexCount]
    'SPTL'                     4 bytes   (optional, leaf chunk)
    leafCount     uint32
    centers       float32[count*3]  one XYZ per leaf, same space as coords

Only vertices a strip references carry valid data -- the engine leaves each
buffer's tail uninitialised (NaN) -- so the reader re-indexes to the used set.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

from core.subprocess_flags import POPEN_FLAGS, windows_cmd
from source_paths import find_game_path, load_config

from asset_convert.speedtree.spt_generator import (TreeGeometry, WORLD_SCALE, COLLISION_MIN_RADIUS,
                            build_tree)
from asset_convert import paths

#: Committed build artifact from native/src/spt_engine/; absent = use the Python fallback.
HARNESS = paths.SPT_ENGINE_DUMP

# The dump is pure geometry, so it is worth caching: one subprocess per
# (spt, seed) instead of one per TREE record sharing that .spt.
_DUMP_CACHE_DIR = 'spt_engine_dumps'

# Engine coords are in SpeedTree units; the Python generator works in
# world units (see spt_generator.WORLD_SCALE).
ENGINE_TO_WORLD = WORLD_SCALE

# Minimum agreement between a repaired block's face normals and the engine's
# own per-vertex normals before the block is kept.
#
# Chosen from a measured sweep, not guessed (see decomp section 6z).  Per-tree
# repair triangles / aggregate agreement at each threshold:
#
#   threshold          0.00     0.60     0.70     0.80     0.90
#   treewhitepinefree  22415    22307    16430     3528      240
#                      73.6%    73.7%    76.6%    82.1%    90.0%
#   swampcypress       12869    11250    11250    11250     6264
#                      85.8%    90.5%    90.5%    90.5%    93.0%
#   cottonwood           784      784      784      784      784   (100%)
#   dogwood             1200     1200     1200     1200     1200   (100%)
#
# 0.60 is the knee: swamp cypress sheds 1,619 genuinely mis-stitched triangles
# (85.8% -> 90.5%, then FLAT to 0.85, so that is a real defect boundary) while
# white pine keeps 99.5% of its geometry.  Higher thresholds have no further
# effect on cypress but strip white pine bare -- it degrades smoothly with no
# knee, i.e. it is simply a dense conifer whose smooth needle normals disagree
# with flat face normals, not a broken reconstruction.  Trees whose repair is
# clean (cottonwood, dogwood) are untouched at every setting.
_REPAIR_MIN_AGREE = 0.60

# The orphan repair only REPAIRS when it is filling gaps in geometry the engine
# mostly returned.  When the strip list covers only a small fraction of the
# buffer, "repair" means fabricating most of the tree from inferred ring sizes,
# and the result is worse than shipping what the engine actually gave us.
#
# Measured across 202 dumps: 73 are >50% repair triangles, worst
# shrubmugopine at 98.2% (6 strips, 1.7% of 7,016 vertices covered).  On
# 00llltreevwelmforestancientmosssu -- reported in-game as having a bad trunk
# -- the repair added 11,250 triangles and 8,484 vertices to the engine's
# 3,045/1,634, and the written bark UV V then spanned -635..1652 instead of
# the engine's own -583..519.
#
# Trees the repair was BUILT for sit well above this: treecottonwoodsu covers
# 72.6% (its missing block is the flared trunk base) and treeginkgo 100%.
_REPAIR_MIN_COVERAGE = 0.50


class EngineUnavailable(RuntimeError):
    """The engine path cannot run -- caller should fall back to build_tree."""


# ---------------------------------------------------------------------------
# locating the configured Oblivion.exe
# ---------------------------------------------------------------------------

def find_oblivion_exe(tes4_data: str | os.PathLike | None = None) -> str:
    """Absolute path to the Oblivion.exe the APPLICATION is configured with.

    The exe sits beside the configured Data directory, so this follows
    whatever `source_paths.find_game_path('oblivion')` resolves -- the config's
    `tes4DataPath` first, then the registry.  Never hardcodes an install.

    Returns '' when no configured install has an Oblivion.exe.
    """
    data = str(tes4_data or '') or find_game_path('oblivion', load_config())
    if not data:
        return ''
    exe = Path(os.path.dirname(os.path.normpath(str(data)))) / 'Oblivion.exe'
    return str(exe) if exe.is_file() else ''


def engine_available(tes4_data: str | os.PathLike | None = None) -> bool:
    """True when the engine path can actually run (harness + configured exe)."""
    return bool(HARNESS.is_file()) and bool(find_oblivion_exe(tes4_data))


# ---------------------------------------------------------------------------
# running the harness / reading its dump
# ---------------------------------------------------------------------------

def run_dump(spt_path: Path, out_bin: Path, seed: int = 1,
             exe: str = '', timeout: float = 120.0) -> Path:
    """Drive the engine for one .spt and return the dump path."""
    if not HARNESS.is_file():
        raise EngineUnavailable(f'harness not built: {HARNESS}')
    exe = exe or find_oblivion_exe()
    if not exe:
        raise EngineUnavailable('no configured Oblivion.exe '
                                '(set tes4DataPath in conversion_config.json)')
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    cmd = windows_cmd([str(HARNESS), str(exe), str(spt_path), str(out_bin),
                       str(int(seed))])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, **POPEN_FLAGS)
    except subprocess.TimeoutExpired as e:
        raise EngineUnavailable(f'{spt_path.name}: engine dump timed out') from e
    if p.returncode != 0 or not out_bin.is_file():
        tail = (p.stdout or '')[-400:] + (p.stderr or '')[-400:]
        raise EngineUnavailable(
            f'{spt_path.name}: engine dump failed (rc={p.returncode}) {tail}')
    return out_bin


def read_dump(path: Path):
    """Parse an SPTG dump -> (coords, normals, uvs, strips).  Engine units."""
    d = Path(path).read_bytes()
    if d[:4] != b'SPTG':
        raise EngineUnavailable(f'{path}: not an SPTG dump')
    n = struct.unpack_from('<I', d, 4)[0]
    off = 8
    co = np.frombuffer(d, np.float32, n * 3, off).reshape(n, 3).copy()
    off += n * 12
    no = np.frombuffer(d, np.float32, n * 3, off).reshape(n, 3).copy()
    off += n * 12
    uv = np.frombuffer(d, np.float32, n * 2, off).reshape(n, 2).copy()
    off += n * 8
    ns = struct.unpack_from('<I', d, off)[0]
    off += 4
    strips = []
    for _ in range(ns):
        cnt = struct.unpack_from('<I', d, off)[0]
        off += 4
        strips.append(np.frombuffer(d, np.uint32, cnt, off).astype(np.int64))
        off += cnt * 4
    return co, no, uv, strips


def read_leaf_centers(path: Path) -> np.ndarray | None:
    """The dump's optional 'SPTL' chunk: (N,3) engine-space leaf centers.

    Returns None for a dump written before leaf support, so an old cached
    .bin degrades to bark-only rather than failing.
    """
    d = Path(path).read_bytes()
    i = d.find(b'SPTL')
    if i < 0:
        return None
    n = struct.unpack_from('<I', d, i + 4)[0]
    if n == 0:
        # An EXPLICIT zero: the engine generated no leaves for this tree (a
        # bare dead tree gates them off with child_freq = 0).  Return an empty
        # array, NOT None -- None means "this dump has no leaf data at all",
        # and the caller must not confuse the two.
        return np.zeros((0, 3), np.float32)
    if (i + 8 + n * 12) > len(d):
        return None
    return np.frombuffer(d, np.float32, n * 3, i + 8).reshape(n, 3).copy()


def _rebase_uv(uv: np.ndarray, tris: np.ndarray | None = None) -> np.ndarray:
    """Strip the wasted whole-tile offset from bark UVs, PER TRIANGLE ISLAND.

    The engine emits bark UVs with a large tile offset -- treecottonwoodsu runs
    v = -827.35..-819.35 and 00llltreevwelmforestancientmosssu (reported
    in-game with a bad trunk) v = 1640.71..1651.71.  Sampling only uses
    frac(v), so that integer part is DISCARDED by the GPU, yet it is still
    carried through float32 interpolation where it eats mantissa bits:

        |v|        float32 spacing    error on a 512px texture
        0.0        0.0000000          0.0000 px
        800.0      0.0000610          0.0312 px
        1640.7     0.0001221          0.0625 px

    A trunk ring at v~1641 resolves to 459 distinct float32 values; the same
    ring at v~0 resolves to 27.6 MILLION.  That quantisation is the
    alternating banding seen marching up the trunk -- and why NifSkope, which
    interpolates in double, shows nothing wrong.

    The shift must be per CONNECTED ISLAND, not per shape: one tree's branches
    carry wildly different offsets, so a single whole-shape shift merely moves
    the problem (measured: it improved cottonwood 62x but made the elm trunk
    2x WORSE, because that tree's minimum sits on a distant branch).

    Shifting by a whole number of tiles keeps frac(v) exact, so the texture
    lands identically -- only the wasted magnitude goes away.  Triangles are
    required to find the islands; without them nothing is changed, because a
    blind per-vertex shift would tear every triangle that straddles a tile
    boundary.
    """
    if uv is None or not len(uv) or tris is None or not len(tris):
        return uv
    out = np.array(uv, np.float32, copy=True)

    # Union-find over triangle corners: vertices sharing a triangle must keep
    # a consistent offset or the UVs tear.
    n = len(out)
    parent = np.arange(n, dtype=np.int64)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for tri in tris:
        a0 = find(int(tri[0]))
        for k in (1, 2):
            b0 = find(int(tri[k]))
            if a0 != b0:
                parent[b0] = a0

    roots = np.array([find(i) for i in range(n)], dtype=np.int64)
    for root in np.unique(roots):
        m = roots == root
        for axis in (0, 1):
            col = out[m, axis]
            good = col[np.isfinite(col)]
            if not len(good):
                continue
            shift = np.floor(good.min())
            if shift:
                out[m, axis] = col - np.float32(shift)
    return out


def orphan_ring_triangles(co: np.ndarray, strips, no_arr: np.ndarray | None = None,
                           ring: int = 0) -> np.ndarray:
    """Triangles for vertex blocks the strip list never references.

    The engine's LOD-0 strip list is INCOMPLETE and one unreferenced block is
    the FLARED TRUNK BASE.  Each block is a tube -- consecutive rings of `ring`
    vertices -- stitched back into open quad runs; `ring` is inferred as the
    largest common divisor of the block lengths.  Winding is DERIVED, never
    assumed: each quad points its geometric normal away from the tube
    centerline.
    See: docs/commentary/asset_convert_speedtree.md#6z-lod-0-strip-list
    """
    n = len(co)
    covered = np.zeros(n, bool)
    for s in strips:
        a = np.asarray(s)
        if len(a):
            covered[a[a < n]] = True
    idx = np.flatnonzero(~covered)
    if not len(idx):
        return np.zeros((0, 3), np.int32)

    # Refuse to "repair" a tree the engine barely returned: see
    # _REPAIR_MIN_COVERAGE.  Filling the flare on a 72%-covered cottonwood is
    # a repair; rebuilding 98% of a shrub from inferred ring sizes is not.
    finite_n = int(np.isfinite(co).all(1).sum())
    if finite_n and covered.sum() / finite_n < _REPAIR_MIN_COVERAGE:
        return np.zeros((0, 3), np.int32)
    finite = np.isfinite(co[idx]).all(1)
    idx = idx[finite]
    if not len(idx):
        return np.zeros((0, 3), np.int32)

    # contiguous runs
    brk = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[brk + 1]))
    ends = np.concatenate((idx[brk], [idx[-1]]))
    lengths = (ends - starts + 1).astype(np.int64)

    if ring <= 0:
        from math import gcd
        g = 0
        for L in lengths:
            g = gcd(g, int(L))
        # a tube ring is at least a triangle; fall back to no repair if the
        # blocks share no plausible ring size
        ring = g if g >= 3 else 0
    if ring < 3:
        return np.zeros((0, 3), np.int32)

    base_ring = ring
    tris = []
    for s, L in zip(starts, lengths):
        # Pick THIS block's ring size: the shared divisor is only a lower
        # bound, and stitching a 28-vertex ring as two 14s joins vertices on
        # opposite sides of the tube -- which showed up as 2 triangles
        # spanning 25% of the tree.  Choose the candidate whose consecutive
        # rings are most nearly parallel slices (smallest spread in the ring's
        # own centroid-to-vertex distance).
        # Consider every plausible ring size that divides this block, not just
        # multiples of the corpus-wide gcd: dogwood's blocks are 24/48/72/96
        # with gcd 24, but its real ring is 12, and forcing 24 merged two
        # rings into one and drew 361 triangles across the tree.
        cands = [k for k in range(3, min(int(L) // 2, 64) + 1)
                 if L % k == 0 and L >= 2 * k]
        if base_ring >= 3 and base_ring not in cands and L % base_ring == 0:
            cands.append(base_ring)
        if not cands:
            continue
        best, best_score = None, None
        for k in cands:
            blk = co[s:s + L].reshape(-1, k, 3)
            # A real ring is a closed loop AND a thin slice: its vertices sit
            # at a similar distance from the ring centroid (roundness) and
            # span far less along the tube than the gap between rings
            # (flatness).  Roundness alone picked 14 for the 84-vertex block
            # [560..643], which is 3 rings of 28 -- that wrapped index 615
            # back to 588 and drew two 33-unit triangles across the tube.
            cen = blk.mean(1, keepdims=True)
            d = blk - cen
            rad = np.linalg.norm(d, axis=2)
            roundness = float(np.mean(rad.std(1) / (rad.mean(1) + 1e-9)))
            if len(blk) > 1:
                axis = blk.mean(1)[-1] - blk.mean(1)[0]
                nrm = np.linalg.norm(axis)
                if nrm > 1e-9:
                    axis = axis / nrm
                    along = np.abs(d @ axis)          # spread within a ring
                    step = float(np.mean(np.linalg.norm(
                        np.diff(blk.mean(1), axis=0), axis=1)))
                    flatness = float(np.mean(along)) / (step + 1e-9)
                else:
                    flatness = 0.0
            else:
                flatness = 0.0
            score = roundness + flatness
            if best_score is None or score < best_score:
                best, best_score = k, score
        ring_k = best
        if L < 2 * ring_k or L % ring_k:
            continue                       # not a whole tube
        nrings = int(L // ring_k)
        # Only stitch CONSECUTIVE rings that actually belong to one tube.  A
        # block can hold several tubes back to back ([560..643] is 3 rings of
        # 28 spanning two separate limbs), and bridging that seam drew two
        # 33-unit triangles across the tree.  A seam shows up as a ring-to-ring
        # centroid step far larger than the block's typical step.
        blk = co[s:s + L].reshape(nrings, ring_k, 3)
        cents = blk.mean(1)
        steps = np.linalg.norm(np.diff(cents, axis=0), axis=1)
        med = float(np.median(steps)) if len(steps) else 0.0
        joinable = steps <= max(med * 3.0, 1e-6) if len(steps) else steps

        # REFUSE to repair a block that is not actually a tube.  On
        # treedogwoodsu the unreferenced vertices are not ring-structured, and
        # stitching them anyway produced 361 triangles spanning up to 46% of
        # the tree.  A real tube's ring radius is small next to the distance
        # its rings advance; require that before emitting anything.
        rad = np.linalg.norm(blk - blk.mean(1, keepdims=True), axis=2)
        ring_r = float(np.median(rad))
        if med <= 1e-9 or ring_r > med * 6.0:
            continue
        # Do NOT wrap the ring closed.  Vertices 588 and 615 in block
        # [560..643] are a ring apart, so joining last-to-first bridged the
        # tube's open seam and drew two 33-unit triangles across it.  The
        # engine's own rings already repeat their first vertex where the tube
        # is meant to close, so an OPEN quad run reproduces it exactly and a
        # genuinely open ring stays open.
        base = np.arange(ring_k - 1, dtype=np.int64)
        nxt = base + 1
        block_tris = []
        for r in range(nrings - 1):
            if len(joinable) and not joinable[r]:
                continue                   # seam between two tubes
            a0 = s + r * ring_k + base
            a1 = s + r * ring_k + nxt
            b0 = a0 + ring_k
            b1 = a1 + ring_k
            t0 = np.stack([a0, b0, a1], 1)
            t1 = np.stack([a1, b0, b1], 1)
            axis_c = 0.5 * (cents[r] + cents[r + 1])
            pa, pb, pc = co[t0[:, 0]], co[t0[:, 1]], co[t0[:, 2]]
            nrm = np.cross(pb - pa, pc - pa)
            outward = ((pa + pb + pc) / 3.0) - axis_c
            votes = np.einsum('ij,ij->i', nrm, outward)
            if np.sum(votes) < 0.0:        # majority point inward -> flip
                t0 = t0[:, ::-1]
                t1 = t1[:, ::-1]
            block_tris.append(t0)
            block_tris.append(t1)

        # Accept a block only if its reconstruction agrees with the engine's
        # own per-vertex normals often enough.  Threshold is module-level so
        # it can be swept against real trees rather than guessed.
        if block_tris and (no_arr is None or _REPAIR_MIN_AGREE <= 0.0):
            tris.extend(block_tris)
        elif block_tris:
            bt = np.concatenate(block_tris)
            pa, pb, pc2 = co[bt[:, 0]], co[bt[:, 1]], co[bt[:, 2]]
            fn = np.cross(pb - pa, pc2 - pa)
            vn = (no_arr[bt[:, 0]] + no_arr[bt[:, 1]] + no_arr[bt[:, 2]]) / 3.0
            dv = np.einsum('ij,ij->i', fn, vn)
            good = np.isfinite(dv)
            if good.any() and (dv[good] > 0).mean() >= _REPAIR_MIN_AGREE:
                tris.append(bt)
    if not tris:
        return np.zeros((0, 3), np.int32)
    out = np.concatenate(tris).astype(np.int32)

    # FINAL GUARANTEE: keep only repair triangles that are actually small.
    #
    # Ring-size inference cannot be made reliable for every tree -- dogwood's
    # unreferenced blocks (24/48/72/96 verts) admit several plausible ring
    # sizes and three successive scoring heuristics all mis-chose for some of
    # them, yielding 118-361 triangles spanning up to 46% of the tree.  Rather
    # than keep tuning a guess, discard whatever the inference got wrong: a
    # real tube quad is tiny next to the tree, so anything long is by
    # definition a mis-stitch.  This bounds the damage no matter what the
    # ring inference does.
    a = co[out[:, 0]]
    b = co[out[:, 1]]
    c = co[out[:, 2]]
    edge = np.maximum(np.maximum(np.linalg.norm(b - a, axis=1),
                                 np.linalg.norm(c - b, axis=1)),
                      np.linalg.norm(a - c, axis=1))
    fin = np.isfinite(co).all(1)
    span = float(np.linalg.norm(co[fin].max(0) - co[fin].min(0))) if fin.any() else 0.0
    if span > 0.0:
        out = out[edge <= 0.10 * span]
    return out.astype(np.int32)


def strips_to_triangles(strips) -> np.ndarray:
    """Expand triangle strips, dropping degenerate joins and honouring the
    alternating winding a strip implies."""
    tris = []
    for idx in strips:
        for k in range(len(idx) - 2):
            a, b, c = int(idx[k]), int(idx[k + 1]), int(idx[k + 2])
            if a == b or b == c or a == c:
                continue                      # degenerate: strip stitching
            tris.append((a, b, c) if k % 2 == 0 else (a, c, b))
    return np.asarray(tris, np.int32).reshape(-1, 3)


# ---------------------------------------------------------------------------
# dump -> TreeGeometry
# ---------------------------------------------------------------------------

def geometry_from_dump(dump: Path) -> TreeGeometry:
    """Build a bark-only TreeGeometry from an engine dump.

    Coordinates are scaled into generator world units so the result drops into
    `build_tree_nif` unchanged.  Vertex colors use the bark default (opaque,
    alpha = wind weight 0): the engine's own wind weights live in a buffer we
    do not currently read, and inventing a ramp here would be a guess.
    """
    co, no, uv, strips = read_dump(dump)
    tris = strips_to_triangles(strips)
    # Re-attach vertex blocks the strip list never references (see
    # orphan_ring_triangles): without them the flared trunk base is missing.
    orphan = orphan_ring_triangles(co, strips, no)
    if len(orphan):
        tris = np.concatenate([tris, orphan]) if len(tris) else orphan
    if not len(tris):
        raise EngineUnavailable(f'{dump}: engine produced no triangles')

    # Re-index to referenced vertices: the engine leaves buffer tails
    # uninitialised, and writing them yields NaNs and a garbage bound.
    used = np.unique(tris)
    remap = np.full(len(co), -1, np.int32)
    remap[used] = np.arange(len(used), dtype=np.int32)

    v = (co[used] * ENGINE_TO_WORLD).astype(np.float32)
    n = no[used].astype(np.float32)
    t = uv[used].astype(np.float32)
    f = remap[tris].astype(np.int32)

    # Guard against any residual non-finite value surviving the re-index.
    bad = ~np.isfinite(v).all(1)
    if bad.any():
        v[bad] = 0.0
    bad_n = ~np.isfinite(n).all(1)
    if bad_n.any():
        n[bad_n] = (0.0, 0.0, 1.0)
    t[~np.isfinite(t)] = 0.0

    geo = TreeGeometry()
    geo.bark_verts = v
    geo.bark_normals = n
    geo.bark_uvs = _rebase_uv(t, f)
    geo.bark_colors = np.ones((len(v), 4), np.float32)
    geo.bark_tris = f
    geo.height = float(v[:, 2].max())
    geo.radius = float(np.hypot(v[:, 0], v[:, 1]).max())
    geo.n_stems = len(strips)
    return geo


def _collision_from_engine(geo: TreeGeometry, strips, remap, verts) -> None:
    """Populate collision from the engine bark.

    The Python generator adds collision PER STEM, gating on base radius
    (`COLLISION_MIN_RADIUS`) so twigs are not collidable.  An engine dump has
    no stem identity -- it is one flat buffer -- but each STRIP is one branch's
    tube, so the strip is the natural unit.  A strip's radius is estimated from
    its own cross-section: the median distance from its vertices to its
    centerline axis.  Strips thinner than the same threshold are skipped, so
    the collision shape matches the generator's trunk-and-thick-limbs rule
    rather than making every twig solid.
    """
    for idx in strips:
        loc = remap[idx]
        loc = loc[loc >= 0]
        if len(loc) < 6:
            continue
        pts = verts[np.unique(loc)]
        if len(pts) < 6:
            continue
        # Cross-sectional radius: spread perpendicular to the strip's dominant
        # axis (a tube's long axis is its branch direction).
        c = pts.mean(0)
        d = pts - c
        # dominant axis via the largest-variance direction
        try:
            _, _, vt = np.linalg.svd(d, full_matrices=False)
            axis = vt[0]
        except np.linalg.LinAlgError:
            continue
        perp = d - np.outer(d @ axis, axis)
        r = float(np.median(np.linalg.norm(perp, axis=1)))
        if r < COLLISION_MIN_RADIUS:
            continue
        tri_local = strips_to_triangles([loc])
        if not len(tri_local):
            continue
        sub = np.unique(tri_local)
        sub_remap = np.full(len(verts), -1, np.int32)
        sub_remap[sub] = np.arange(len(sub), dtype=np.int32)
        geo.collision_verts.append(verts[sub].astype(np.float32))
        geo.collision_tris.append(sub_remap[tri_local].astype(np.int32))


def _card_extents(m, tree_size: float):
    """Leaf card (width, height) in world units.

    Section 4006 (`world_size`) is the engine's OWN cached card size, already
    premultiplied by the tree Size -- so the world-unit card is
    `world_size * WORLD_SCALE`.  Measured across the corpus this is exactly
    2x what `size * K * 0.5` gives, which is what the previous code used.

    4006 is STALE in a minority of shrubs (section 6t measured 263/465 maps
    within 1% of `4005 * Size`; dtree01leaves stores 4006 = 7.6 for three maps
    whose 4005 values differ), so it is only trusted where it agrees with
    `4005 * Size`; otherwise the card falls back to that product, which is
    what 4006 is supposed to hold.
    """
    sx = max(float(m.size[0]), 1e-6)
    sy = max(float(m.size[1]), 1e-6)
    wx, wy = float(m.world_size[0]), float(m.world_size[1])
    ex, ey = sx * tree_size, sy * tree_size          # what 4006 should equal
    if wx > 1e-6 and abs(wx - ex) <= 0.01 * max(ex, 1e-6):
        w = wx * WORLD_SCALE
    else:
        w = ex * WORLD_SCALE
    if wy > 1e-6 and abs(wy - ey) <= 0.01 * max(ey, 1e-6):
        h = wy * WORLD_SCALE
    else:
        h = ey * WORLD_SCALE
    return w, h


def _leaf_groups_from_centers(tree, centers: np.ndarray, seed: int | None):
    """Leaf-card groups built on the ENGINE's own leaf positions.

    Each engine center becomes two crossed quads: the one sanctioned deviation,
    since Skyrim cannot render the engine's camera-facing billboards.  Position,
    count, map selection (uniform random index) and card size
    (`size.x/size.y * K * 0.5`, where `K = tree.size * WORLD_SCALE`) all come
    from the engine.  Returns (leaf_groups, n_leaves).
    See: docs/commentary/asset_convert_speedtree.md#6x-leaf-geometry-extractable-leaf
    """
    from asset_convert.speedtree.spt_generator import leaf_card

    leaf_maps = [m for m in tree.leaf_maps
                 if m.texture and 'fileloaderror' not in m.texture.lower()]
    if not leaf_maps or not len(centers):
        return [], 0

    rng = np.random.default_rng((seed or 0) & 0xFFFFFFFF)
    verts = centers * ENGINE_TO_WORLD
    canopy = verts.mean(axis=0)

    # UV source: composite-map quads (section 10002) when the tree ships them,
    # exactly as the Python path does -- the shipped .dds IS the composite.
    map_indices = [i for i, m in enumerate(tree.leaf_maps) if m in leaf_maps]
    use_composite = len(tree.leaf_quads) >= len(tree.leaf_maps) > 0
    _FULL_QUAD = (1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0)   # TR TL BL BR

    if use_composite:
        groups = {'__composite__': {'v': [], 'n': [], 'uv': [], 'c': [], 't': []}}
    else:
        groups = {m.texture: {'v': [], 'n': [], 'uv': [], 'c': [], 't': []}
                  for m in leaf_maps}

    # Uniform pick per leaf: RandomRange(0,100000) % n at 0x79a1fe (section
    # 6g) -- a plain modulo, no blossom weighting (those sections were
    # measured to be leaf-SYSTEM fields, not per-map ones).
    picks = rng.integers(0, len(leaf_maps), size=len(verts))

    for pos, k in zip(verts, picks):
        m = leaf_maps[int(k)]
        if use_composite:
            g = groups['__composite__']
            quad = tree.leaf_quads[map_indices[int(k)]]
        else:
            g = groups[m.texture]
            quad = _FULL_QUAD
        w, h = _card_extents(m, tree.size)
        leaf_card(g, pos.astype(np.float64), w, h, m, quad, canopy, rng)

    out = []
    for tex_key, g in groups.items():
        if not g['v']:
            continue
        out.append({
            'texture': tex_key,
            'verts': np.asarray(np.concatenate(g['v']), np.float32),
            'normals': np.asarray(np.concatenate(g['n']), np.float32),
            'uvs': np.asarray(np.concatenate(g['uv']), np.float32),
            'colors': np.asarray(np.concatenate(g['c']), np.float32),
            'tris': np.asarray(np.concatenate(g['t']), np.int32),
        })
    return out, len(verts)


def build_tree_engine(tree, spt_path: Path, seed: int | None = None,
                      exe: str = '', cache_dir: Path | None = None,
                      with_leaves: bool = True) -> TreeGeometry:
    """Engine bark (+ optional engine leaves) as a TreeGeometry.

    Raises `EngineUnavailable` when the engine path cannot run; callers fall
    back to `spt_generator.build_tree`.  `with_leaves` builds foliage on the
    ENGINE's leaf centers; an EMPTY center array means zero leaves and the tree
    keeps none.  Only a dump with NO leaf chunk falls back to Python foliage.
    See: docs/commentary/asset_convert_speedtree.md#6aa-no-leaf-chunk-vs
    """
    spt_path = Path(spt_path)
    # Availability is checked BEFORE the cache lookup: a stale dump left over
    # from an earlier run must not make an unavailable engine look available,
    # or the caller silently keeps using engine geometry it can no longer
    # regenerate (and the fallback never fires).
    if not HARNESS.is_file():
        raise EngineUnavailable(f'harness not built: {HARNESS}')
    if not (exe or find_oblivion_exe()):
        raise EngineUnavailable('no configured Oblivion.exe '
                                '(set tes4DataPath in conversion_config.json)')
    sd = 1 if seed is None else int(seed)
    cache_dir = Path(cache_dir) if cache_dir else spt_path.parent / _DUMP_CACHE_DIR
    dump = cache_dir / f'{spt_path.stem}_{sd}.bin'
    if not dump.is_file():
        run_dump(spt_path, dump, seed=sd, exe=exe)

    co, no, uv, strips = read_dump(dump)
    tris = strips_to_triangles(strips)
    # Re-attach vertex blocks the strip list never references (see
    # orphan_ring_triangles): without them the flared trunk base is missing.
    orphan = orphan_ring_triangles(co, strips, no)
    if len(orphan):
        tris = np.concatenate([tris, orphan]) if len(tris) else orphan
    if not len(tris):
        raise EngineUnavailable(f'{spt_path.name}: engine produced no triangles')

    geo = geometry_from_dump(dump)

    # Collision needs the same re-index the geometry used.
    used = np.unique(tris)
    remap = np.full(len(co), -1, np.int32)
    remap[used] = np.arange(len(used), dtype=np.int32)
    _collision_from_engine(geo, strips, remap, geo.bark_verts)

    if with_leaves:
        centers = read_leaf_centers(dump)
        if centers is not None:
            geo.leaf_groups, geo.n_leaves = _leaf_groups_from_centers(
                tree, centers, seed)
        else:
            try:
                py = build_tree(tree, seed=seed)
                geo.leaf_groups = py.leaf_groups
                geo.n_leaves = py.n_leaves
            except Exception:
                pass
        if geo.leaf_groups:
            zs = [geo.height] + [g['verts'][:, 2].max() for g in geo.leaf_groups]
            rs = [geo.radius] + [float(np.hypot(g['verts'][:, 0],
                                                g['verts'][:, 1]).max())
                                 for g in geo.leaf_groups]
            geo.height = float(max(zs))
            geo.radius = float(max(rs))

    if not geo.collision_verts and geo.bark_verts is not None:
        # Never ship a tree with no collision: fall back to the whole bark
        # mesh rather than leaving the player able to walk through the trunk.
        geo.collision_verts.append(geo.bark_verts)
        geo.collision_tris.append(geo.bark_tris)
    return geo
