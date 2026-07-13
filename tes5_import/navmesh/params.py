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
# Target navmesh triangle edge length (game units).  Interior Steiner points are
# seeded on a grid of this spacing so triangles come out roughly uniform in size
# rather than as earcut fans of long thin slivers.  ~vanilla interior tri scale.
TRI_TARGET_EDGE = 128.0
# Exteriors are a whole 4096u cell of smooth terrain — a dense grid there just
# explodes the triangle/vertex count and build time, so use much larger tris.
TRI_TARGET_EDGE_EXTERIOR = 320.0

# --- Pathgrid coupling -----------------------------------------------------------
# A pathgrid node associates with a walkable span within this XY distance.  The
# pathgrid is Bethesda's own annotation of where NPCs walk, so it selects which
# of the many physically-standable surfaces (floor vs tabletop vs roof) we keep.
SEED_SNAP = 64.0
# When a node's column has several spans (multi-floor), take the span whose Z is
# within this of the node's Z.  Node Z states which floor the designer meant.
SEED_Z_TOLERANCE = 96.0
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

# --- Limits ----------------------------------------------------------------------
# Hard cap on grid dimension per cell; beyond this CS is coarsened.  Guards
# memory on huge exterior cells.
MAX_GRID_DIM = 512
# Per-cell wall-clock budget.  On overrun the cell is abandoned (the caller
# falls back), so one pathological cell can never stall the whole run.
CELL_TIME_BUDGET = 20.0
