"""Tunable parameters for navmesh generation.

All distances are GAME UNITS.  Values are derived from Skyrim's actual actor
dimensions rather than guessed, so they should only be changed with a reason.
"""

# --- Voxel grid ---------------------------------------------------------------
# XY size of a heightfield column.  A Skyrim humanoid's path radius is ~20-35u,
CS = 16.0
# Exteriors are a full 4096u cell: at 16u that is a 256x256+ column grid, and the
# rasterize/region passes are O(columns).  Terrain has no doorway-scale detail,
# so a coarser grid costs nothing there and is ~4x less work.
CS_EXTERIOR = 32.0
# Z resolution of a span.  Must be well under MAX_CLIMB or stairs blur together.
CH = 8.0

# --- Agent ---------------------------------------------------------------------
# Required headroom.  Kills crawlspaces under stairs and low shelves.
AGENT_HEIGHT = 128.0
# Step height.  THE key constant: it decides stairs (connected) vs ledges
# (not connected), and it is what lets an NPC walk over a rug or a low sack
# instead of pathing around it.
MAX_CLIMB = 34.0

# Minimum XY-projected area (game units^2) for a triangle to be kept at all.
# A triangle standing in a near-vertical plane covers no ground, so no actor can
# stand on it however tall it is — it is a wall-hugging sliver, not a stair
# riser (a riser is steep but still has real footprint).  Deliberately TINY: this
# only removes the genuinely degenerate-in-plan case, never a real surface.
# 1.0u^2 is far below one voxel quad (CS^2 = 256u^2 at CS 16).
MIN_XY_FOOTPRINT = 1.0

# --- Surfaces -------------------------------------------------------------------
# Walkable if the surface normal is within this of straight up.  Mirrors
MAX_SLOPE_DEG = 46.0

# --- Contour / polygon ----------------------------------------------------------
# Max deviation when simplifying a region contour.  This is what turns the raw
# 16u voxel staircase into straight edges that follow the real wall.
MAX_SIMPLIFY_ERR = 12.0
# Contours shorter than this many voxels are noise (specks behind furniture).
MIN_REGION_VOXELS = 8
#: Hex-lattice spacing and outline densify step (u); 192 was measured and rejected, see #tri-target-edge-192.
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
# create a triangle whose edges differ by more than this factor.  2.0 is the
# shape CONTRACT: the long side of a triangle may not exceed twice its short
# side — near-equilateral is what NPC pathfinding wants.
MAX_EDGE_RATIO = 2.0
# Aspect bound (longest_edge^2 / (4 * area)) for the CLEANUP passes' shape
# contract.  Edge ratio alone cannot see a CAP: an obtuse triangle whose apex
# sits just off a long base has all three edges of comparable length (ratio
# < 2) and near-zero height — visually the worst sliver there is.  Equilateral
# scores 0.58; every ratio-2-legal non-cap shape stays under ~2.0; thin caps
# score 2.5+.  A triangle is BAD when either bound is exceeded.
MAX_TRI_ASPECT = 2.5
# Simplification rounds (collapse + flip + smooth per round).  Converges fast;
# rounds after the third change little.
SIMPLIFY_PASSES = 4

# --- Pathgrid coupling -----------------------------------------------------------
# A pathgrid node associates with a walkable span within this XY distance.  The
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
# Z window for claiming mesh vertices into the quad — a door only restructures
# the floor it stands on, never a storey above/below.
DOOR_QUAD_ZTOL = 128.0

# --- Island pruning ---------------------------------------------------------------
# A disconnected component smaller than this is noise (a scrap behind a shelf, a
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
# A component with a mesh vertex within this of a PATHGRID sample carries the
# walked line and is never dropped as an island — the pathgrid is the one input
# that asserts an actor walks there.  Sized to the ribbon half-width so a
# ribbon's own vertices always qualify.
ISLAND_PGRD_RADIUS = 48.0

# --- Island bridging (drop-downs) -------------------------------------------------
# Oblivion had no pathgrid edge for a DROP: a balcony and the floor below it are

# --- Corridor ribbons (Phase 1, corridor.py) --------------------------------------
# Half-width of the flat ribbon laid down each pathgrid edge.  ~80u total sits
RIBBON_HALF_WIDTH = 40.0

# --- Island bridging reach (see the ISLAND_BRIDGE block above) --------------------
# Matched between BOUNDARY EDGE PAIRS, not single vertices, so the threshold has
ISLAND_BRIDGE_XY = 2.0 * RIBBON_HALF_WIDTH
# Vertical drop the bridge may span.  A one-storey fall; Skyrim NPCs take this
# routinely and Oblivion's design relies on it (the Ambush A mezzanine is 192u
# above the room floor).  More than this would weld a tower's storeys together.
ISLAND_BRIDGE_MAX_DROP = 220.0
# Below MAX_CLIMB the two sides are a step apart, not a storey -- ordinary
# adjacency, not a drop, and not this pass's business.
ISLAND_BRIDGE_MIN_DROP = MAX_CLIMB
# Ledge links emitted along ONE lip (component pair).  Vanilla puts a link on
# several triangles along a balcony edge (half of Skyrim.esm's mesh->target
# ledge pairs carry 2-12+ links) so an actor drops from wherever it reaches
# the lip; a single link forced every actor through one small triangle and
# most simply stopped at the edge.
LEDGE_LINKS_PER_PAIR = 8

# Spacing of cross-sections along an edge, so a long edge is several quads and
# the ribbon can follow the pathgrid line's slope in Z rather than one flat
# quad bridging the whole span.
#
# This ALSO sets how finely the overlap cut is resolved.  A quad between two
# cross-sections is a straight sheet, but the boundary between two corridors'
# owned ground bends at every junction, so a long quad paints outside the region
# it owns.  Measured on AnvilFightersGuild (coverage / double-covered ground):
#   32u -> 85.7% / 14.9%     16u -> 94.7% / 6.0%     8u -> 97.8% / 4.1%
# 8u costs more triangles but is the difference between a corridor network that
# is visibly stacked and one that is not.
RIBBON_STEP = 8.0
# How close a door-quad corner must be to a ribbon vertex to weld onto it
# (share the index, hence a shared edge -> adjacency).  Half a ribbon width:
# tight enough not to fuse across a real gap, wide enough to catch the strip.
RIBBON_WELD_EPS = 24.0
# A dead-end pathgrid node (degree 1) has its ribbon extended this far past the
# node along the edge direction: the pathgrid ends before the room does, so a
# corridor that stopped at the last node would leave a gap in front of the wall
# or door it was heading for.  ~one ribbon half-width reaches the threshold.
RIBBON_END_EXTEND = 40.0
# A STEEP (stair/ramp) ribbon extends this far past BOTH its end nodes, junction
# or not.  A steep edge is never width-grown, so it keeps the narrow Phase-1
# width while the flat landing it meets grows to 100u+; the stair mouth is then
# much narrower than the landing and the two meet only at the landing's CORNER
# vertices.  At the top of Pinarus's stairs that left the whole descent routed
# through two 27-degree wedges (one of edge ratio 6.1) hanging off those corners
# — one connected component, but not walkable.  Extending the flight onto the
# flat at each end gives the boolean union a real overlap, so the mouth becomes a
# span of shared edges.  Kept modest: the extension carries the LINE's slope, so
# too much would push ramp mesh out across the landing floor.
RIBBON_STAIR_END_EXTEND = 48.0
# Half-width of a STEEP ribbon.  Wider than RIBBON_HALF_WIDTH because a flight
# has to present a mouth comparable to the landing it joins, and because a
# staircase in Oblivion is a full-width architectural feature, not a corridor.
# Still well inside a standard flight so the ribbon does not overhang the
# stringers. Only used for edges the width-grow refuses (see RIBBON_GROW_MAX_SLOPE).
RIBBON_STAIR_HALF_WIDTH = 64.0

# --- Corridor width-grow (Phase 2, corridor.py) -----------------------------------
# Phase 2 replaces the fixed RIBBON_HALF_WIDTH with a per-cross-section, per-side
RIBBON_GROW = True
# Step size of the outward march (game units).  Finer = tighter fit to a wall at
# more cost.  Half the agent radius resolves a doorway jamb without over-sampling.
RIBBON_GROW_STEP = 8.0

# The probe is a THIN vertical slab, not an agent-radius box: actor HEIGHT and
# ~actor WIDTH along the edge tangent, but only a sliver DEEP along the march
# direction.  A fat box stops an agent-radius short of every wall, which
# narrowed doorways by 24u a side — the navmesh must instead come right up to
# the collision.  Depth is a sliver so "hit" means the slab genuinely touches
# the wall at this step, not that a wall is somewhere nearby.
RIBBON_GROW_SLAB_HALF_WIDTH = 20.0
# Depth is a sliver so "hit" means the slab genuinely touches collision at this
# step, not that something is vaguely nearby — but not razor-thin: a few units
# of depth give the ribbon a small standoff from furniture instead of pressing
# right against the bed frames, and cost nothing at a wall.
RIBBON_GROW_SLAB_DEPTH = 6.0
# The slab starts above the floor so collision the actor simply STEPS ONTO is
# not read as a wall, and rises to AGENT_HEIGHT so anything the actor's body
# would hit stops the growth.  The floor is MAX_CLIMB: a step, curb, or stair
# tread whose top is within one climb of the floor is walkable, so its riser
# face must be ignored — otherwise a 20u curb across a passable route (the Anvil
# main-gate ramp, the center-circle steps) walls the corridor shut and splits
# the navmesh into disconnected islands.  A real wall extends far above
# MAX_CLIMB and is still caught by the band above it.
RIBBON_GROW_SLAB_Z_BOTTOM = MAX_CLIMB
# Bisection rounds used to place the stop exactly at the wall once the swept
# step has detected one.  4 rounds resolve an 8u step to 0.5u.
RIBBON_GROW_BISECT = 4
# An edge steeper than this (rise/run) is a STAIRCASE or ramp and is NOT grown:
# its ribbon is a tilted plane, so a perpendicular rail leaves the treads at
# once — off the side of the flight, or through the stairwell wall.  Stairs keep
# the Phase-1 width, which is what the pathgrid asserts.  0.20 ~ 11 degrees:
# well above a floor's slop, well below any real flight.
RIBBON_GROW_MAX_SLOPE = 0.20
# Douglas-Peucker tolerance applied to each grown RAIL before it becomes the
# ribbon outline.  The march samples a width every RIBBON_STEP, and the
# triangulator FORCES every outline corner as a Steiner point — an un-simplified
# rail therefore seeds a vertex every 8u and fills rooms with sliver fans.  At
# 12u the rail still hugs a wall it followed, but a straight run collapses to
# two points and the hex lattice governs the interior.
RIBBON_RAIL_SIMPLIFY = 12.0
# Rays in the radial fan grown around each pathgrid NODE.  Ribbons grow only
# perpendicular to their own edge, so the outer corner where two edges meet at
# an angle is a notch no ribbon reaches (a right-angle junction leaves a square
# bite out of the mesh).  16 rays = one every 22.5 deg, enough to resolve a
# square corner without a fan of near-duplicate boundary points.
RIBBON_GROW_DISC_RAYS = 16

# --- Decimation (corridor_clean.decimate) ------------------------------------
# Collapse edges shorter than this, turning the needle fans that outline corners
DECIMATE_MIN_EDGE = 64.0
# Passes.  Each round re-derives the boundary and re-sorts candidates.  Five,
# not three: sawtooth removal converges tooth by tooth — cutting a convex
# tooth re-derives the outline and only then exposes the next collapse.
DECIMATE_ROUNDS = 5
# How far the OUTLINE may move when a boundary vertex is decimated away: the
# vertex's distance from the chord between its two boundary neighbours.  The
# outline is the wall standoff, so this is deliberately small — straight runs of
# boundary samples collapse freely, a real corner never moves and the mesh
# cannot cut through a wall.  Freezing the outline entirely (tol 0) left 17-21%
# sliver triangles; this recovers the decimation without the corner-cutting.
DECIMATE_OUTLINE_TOL = 6.0
# SAWTOOTH removal: a boundary vertex that juts OUTWARD (convex — its removal
# can only SHRINK the mesh, never push it through a wall) may be cut even when
# it deviates more than DECIMATE_OUTLINE_TOL from its neighbours' chord, up to
# this deviation.  This is what turns a zigzag union outline into a clean
# polygon: the teeth are cut off inward, real concave corners (which removal
# would EXPAND across) are never touched.  Bounded by DECIMATE_MAX_AREA_LOSS.
DECIMATE_SAWTOOTH_DEV = 32.0
# Total plan area the sawtooth cuts may remove, as a fraction of the mesh's
# area at decimation start.  "Only a minor area reduction along the periphery."
DECIMATE_MAX_AREA_LOSS = 0.10

# --- Peripheral sliver cull (corridor_clean.cull_boundary_slivers) -----------
# After decimation and flips, whatever badly-shaped triangles remain sit on
CULL_SLIVER_RATIO = 2.0
# "Small" for the ratio cull.  3000u^2 ~ a 100x60 wedge: big enough to catch
# every visible boundary needle, still well under the size of a triangle that
# is genuinely load-bearing coverage.
CULL_SLIVER_MAX_AREA = 3000.0
# Hard minimum triangle area: below this a triangle covers no usable ground
# for a Skyrim actor (path radius ~34u -> ~1000u^2 footprint).  Vanilla door
# triangles bottom out at 992.
MIN_TRI_AREA = 1000.0
# Total area the sliver cull may remove, as a fraction of the mesh — the cull
# trims the periphery, it must never eat into real coverage.
CULL_SLIVER_AREA_FRAC = 0.12
# Vertices within this of a door wedge RING POINT (base corners, base
# midpoint, apex — the Door Triangle's own corners) are PINNED — never
# collapsed.  A decimated door corner destroys the Door Triangle and the
# doorway goes dead in the engine.  Tight, because the ring coordinates are
# EXACT mesh vertices: the old 80u blanket around the whole threshold also
# froze every sliver in the doorway's neighbourhood, which no collapse or
# cull could then remove (measured on Pinarus: area-3 MICRO triangles parked
# beside a door forever).
DECIMATE_PIN_RADIUS = 8.0
#: Pin radius about a door CENTER; fallback for doors carrying no wedge ring.
DECIMATE_PIN_CENTER_RADIUS = 24.0
# Vertices within this of a PATHGRID NODE are pinned.  A node is a junction
# the walked lines meet AT; outline-moving collapses and the sliver cull had
# no node awareness and could shave the boundary across one, leaving the
# node's own position a few units outside coverage (measured as single-sample
# holes exactly at nodes in ImperialDungeon01 and BarrenCave).
DECIMATE_PIN_NODE_RADIUS = 24.0
# Lower bound: a rail never grows NARROWER than this half-width even if a wall or
# a neighbour centerline is closer, so a corridor squeezed between two close
# obstacles still carries a walkable strip (the Phase-1 width was unconditional).
RIBBON_GROW_MIN_HALF = 16.0
# Hard cap on grown half-width.  A corridor in open space (no wall, no neighbour)
# stops here rather than ballooning across a whole exterior cell.  ~1.5 doorways;
# wide enough for room coverage, bounded enough that a doorway leak is a nub.
RIBBON_GROW_MAX_HALF = 160.0

