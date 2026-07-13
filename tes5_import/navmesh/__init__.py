"""Collision-driven navmesh generation (voxelize -> regions -> contours -> tris).

Replaces the old pathgrid-buffering approach.  See docs/navmesh_rebuild_plan.md.
"""

from .build import build_navmesh  # noqa: F401
