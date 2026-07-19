"""Tunable parameters for navmesh generation.

All distances are GAME UNITS.  Values are derived from Skyrim's actual actor
dimensions rather than guessed, so they should only be changed with a reason.
"""

# --- Voxel grid ---------------------------------------------------------------
# XY size of a heightfield column.  A Skyrim humanoid's path radius is ~20-35u,
# so 16u gives sub-radius precision without exploding the grid.  Interiors are
# tight (doorways, furniture gaps) and need it.
CS = 16.0
# Exteriors are a full 4096u cell: at 16u that is a 256x256+ column grid, and the
# rasterize/region passes are O(columns).  Terrain has no doorway-scale detail,
# so a coarser grid costs nothing there and is ~4x less work.
CS_EXTERIOR = 32.0
# Z resolution of a span.  Must be well under MAX_CLIMB or stairs blur together.
CH = 8.0

# --- Agent ---------------------------------------------------------------------
# Radius to erode the walkable set by, so the mesh keeps a correct standoff from
# every wall.  This replaces the old hand-tuned EXCLUSION_MARGIN fudge.
AGENT_RADIUS = 24.0
# Required headroom.  Kills crawlspaces under stairs and low shelves.
AGENT_HEIGHT = 128.0
# Step height.  THE key constant: it decides stairs (connected) vs ledges
# (not connected), and it is what lets an NPC walk over a rug or a low sack
# instead of pathing around it.
MAX_CLIMB = 34.0

# --- Surfaces -------------------------------------------------------------------
# Walkable if the surface normal is within this of straight up.  Mirrors
# asset_convert.collision_extract.MAX_SLOPE_DEG (which bakes the classification
# into the cache); kept here for the LAND terrain, which is classified live.
MAX_SLOPE_DEG = 46.0

# --- Contour / polygon ----------------------------------------------------------
# Max deviation when simplifying a region contour.  This is what turns the raw
# 16u voxel staircase into straight edges that follow the real wall.
MAX_SIMPLIFY_ERR = 12.0
# Contours shorter than this many voxels are noise (specks behind furniture).
MIN_REGION_VOXELS = 8
# Target navmesh triangle edge length (game units).  Simplification never makes
# an edge longer than this, so triangles come out roughly uniform in size rather
# than as fans of long thin slivers.  ~vanilla interior tri scale.  Scaled by the
# heightfield's cell size, so an exterior (CS 32) allows 2x longer edges.
TRI_TARGET_EDGE = 128.0
# Triangle shape bound during simplification: longest_edge^2 / (4 * area).  An
# equilateral triangle scores 0.58; slivers score high.  A collapse or a smooth
# move may not create a triangle worse than this.  (The old bound of 6 let
# decimation fill rooms with visible near-degenerate fans.)
MAX_ASPECT = 4.0
# Edge-ratio bound (longest/shortest edge) during simplification.  The aspect
# metric alone lets through "one side way shorter than the others" triangles:
# a 16u voxel edge with two ~100u edges scores aspect ~3 (its area is healthy)
# yet reads as an obvious needle radiating from a wall corner.  No move may
# create a triangle whose edges differ by more than this factor.
MAX_EDGE_RATIO = 4.0
# Simplification rounds (collapse + flip + smooth per round).  Converges fast;
# rounds after the third change little.
SIMPLIFY_PASSES = 4

# --- Pathgrid coupling -----------------------------------------------------------
# A pathgrid node associates with a walkable span within this XY distance.  The
# pathgrid is Bethesda's own annotation of where NPCs walk, so it selects which
# of the many physically-standable surfaces (floor vs tabletop vs roof) we keep.
SEED_SNAP = 64.0
# When a node's column has several spans (multi-floor), take the span whose Z is
# within this of the node's Z.  Node Z states which floor the designer meant.
SEED_Z_TOLERANCE = 96.0
# How far a walkable span may be from the pathgrid — measured as WALKED
# (geodesic) distance over the span graph, not straight-line XY — and still be
# kept (region.keep_pathgrid_heights).  The pathgrid is SPARSE: Bethesda ran a
# line down the middle of a room, not around it, and the whole point of
# voxelizing real collision is to EXPAND from that line and fill the walkable
# floor.  Geodesic distance wraps around furniture but cannot pass through
# walls, so a big reach fills the room without painting the street outside the
# shell.  (160u straight-line, the old gate, trimmed real floor in large rooms.)
PGRD_XY_REACH = 384.0
# Exterior reach.  An exterior cell is open terrain: Bethesda's own exterior
# navmeshes cover essentially the WHOLE cell, while the pathgrid is just the
# roads.  A tight reach gate carved the open ground into arbitrary blobs around
# the pathgrid — mesh missing over half a cell with no obstacle in sight — so
# outdoors the flood may reach the entire cell (geodesic walking still cannot
# climb cliffs steeper than MAX_CLIMB per step or cross water gaps, and roofs
# remain unreachable, so the wrong-surface protection is intact).
PGRD_XY_REACH_EXTERIOR = 8192.0
# Radius of the flood barrier stamped over each TELEPORT door of an interior
# cell.  The reach flood may arrive at these columns (the doorstep keeps its
# mesh, so the Door Triangle exists) but never expands from them, so the mesh
# ends at the threshold — like vanilla — instead of escaping through the open
# doorway onto the decorative street outside the shell.  Must comfortably
# exceed a doorway's half-width so the flood cannot slip around the corners.
DOOR_BARRIER_RADIUS = 64.0
# Half-width of the band stamped along every pathgrid line (voxel.stamp_pathgrid).
# This band is UNCONDITIONAL navmesh: the pathgrid is the only part of the input
# we know to be correct, so a strip of this width around every pathgrid line is
# always in the final mesh, whatever the collision says.  Nothing culls it — no
# ledge/headroom filter, no region cull, no agent erosion — and the stamp yields
# to nothing.  (Making it yield to blocking collision silently refused to stamp
# staircases, whose own faces are steep enough to be classed blocking, and left
# the storeys of a house as disconnected islands.)
#
# Sized to the agent so a staircase or a doorway comes out genuinely walkable
# rather than a sliver.
PGRD_BAND = 24.0
# How far the stamp reaches in Z to SNAP a pathgrid sample onto real walkable
# collision.  A pathgrid is coarse on stairs (the Anvil Fighters Guild runs a whole
# flight on two nodes ~100u apart in Z), so a sample interpolated along such an
# edge can float above the tread it is meant to stand on and must reach down for
# it.  But reaching too far is worse than not reaching at all: at 128u the band
# starts latching onto whatever surface happens to lie under a balcony, and the
# layer count goes UP.  A step height plus a stair riser is the right order.
PGRD_SNAP_Z = 48.0
# The UPWARD half of the snap window is MAX_CLIMB (see voxel.surface_near):
# reaching DOWN is what stairs and gullies need (a chord-paced sample floats
# above the surface that dips under it); reaching UP more than a single step
# could only latch onto something standing ON the walked surface (chest and
# counter tops hoisted the mesh onto the furniture), while less than a step
# loses a climbing cave passage and stamps the ribbon inside the hill.

# --- Door threshold quads -----------------------------------------------------------
# Every door REFR (teleport or interior) gets an exact oriented quad stamped
# into the mesh at its threshold: the two triangles the Door Triangle link can
# land on.  Half-extent along the door's width (local X) and depth (local Y,
# the walk-through direction).  96u total width stays inside a standard ~110u
# Oblivion doorway so the quad never pokes into the jambs; 64u total depth
# straddles the threshold line the way vanilla door triangles do.
DOOR_QUAD_HALF_WIDTH = 48.0
DOOR_QUAD_HALF_DEPTH = 32.0
# Z window for claiming mesh vertices into the quad — a door only restructures
# the floor it stands on, never a storey above/below.
DOOR_QUAD_ZTOL = 128.0

# --- Boundary cleanup ---------------------------------------------------------------
# A triangle on the outline with at most one neighbour (a protruding flap/ear)
# is deleted when smaller than this (game units^2, interior scale; scales with
# the voxel size squared).  These flaps are voxel-quantization noise at wall
# corners — too small to route through, ugly, and they read as "small triangles
# around corners" in-game.  192 = 1.5 voxel-scale triangles at CS 16.
# Removal is inherently safe: a triangle with <=1 neighbour cannot be a bridge,
# so deleting it can never disconnect the mesh.  Triangles near the pathgrid
# or a door threshold are exempt.
EAR_MIN_AREA = 192.0
# A flap is exempt when any densified pathgrid sample lies within this XY
# distance of it.  Containment-only exemption still let the cull eat ribbon
# ends and narrow cave ledges the pathgrid walks (2 wrong-floor nodes in
# XPGloomstonePassage02); a distance buffer protects the walked line and its
# fringe while still cleaning wall corners elsewhere.
EAR_PGRD_RADIUS = 64.0
# Cull rounds.  Each round exposes new boundary edges; more rounds chain-eat
# the outline (a cave boundary is legitimately jagged).
EAR_ROUNDS = 2

# --- Island pruning ---------------------------------------------------------------
# A disconnected component smaller than this is noise (a scrap behind a shelf, a
# ribbon fragment on a wall top) unless a door anchors it.  NPCs cannot use a
# 1-4 triangle island for anything.
MIN_ISLAND_TRIS = 5
# A component counts as door-anchored when a mesh vertex lies within this XY
# distance of a teleport-door REFR (and within door Z tolerance).  The doorstep
# strip in front of a door must always survive so its Door Triangle exists.
ISLAND_DOOR_RADIUS = 150.0
# Z window for the door-anchor test: a door's PosZ sits at its threshold, so the
# doorstep mesh is within a step or two of it.  Wide enough for tall thresholds,
# narrow enough that a door on a balcony never anchors the floor below it.
ISLAND_DOOR_ZTOL = 128.0
# Exterior: a component that comes within this of the cell border "runs over
# into the next cell" and is kept — its continuation lives in the neighbour
# cell's navmesh.  ~1.5 exterior cells.
ISLAND_EDGE_MARGIN = 48.0

# --- Limits ----------------------------------------------------------------------
# Hard cap on grid dimension per cell; beyond this CS is coarsened.  Guards
# memory on huge exterior cells.
MAX_GRID_DIM = 512
# Per-cell wall-clock budget.  On overrun the cell is abandoned (the caller
# falls back), so one pathological cell can never stall the whole run.
CELL_TIME_BUDGET = 20.0
