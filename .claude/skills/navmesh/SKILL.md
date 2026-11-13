---
name: navmesh
description: >-
  How to work on navmesh: diagnose a walk stall or bad mesh in a cell, draw the
  standard walk picture (route, offline predictor, key), hand a cell to the user
  for cellview, pin a cell and ship the pin into the ESM, and check a generator
  change (protected cells, pinned cells, every health trait, blast radius). Use
  for any navmesh question, NPC or test walk that stalls, pin, or change to
  native/src/navgrow or tes5_import/navmesh.
---

# Navmesh

The hard rules are in CLAUDE.md ("Navmesh changes"): protected cells, pinned
cells, no whole-plugin rebuilds without asking. This is the how-to.

## A navmesh problem in a cell: show the user

Whenever you hit a navmesh issue in a cell (a stall, a hole, mesh under a slab, a
pinch), send it to the user to look at in cellview. Every time, give:

- the cell's EditorID **and** plugin (e.g. `NordtorX25Y0` in `Arktwend_English.esm`);
- a clickable link to the picture below;
- one plain line on what looks wrong and where (coordinates).

The user opens it in cellview (`python tools/cellview/server.py`; it prints its
URL). Don't stop the rest of the work while they look.

### The picture: `tools/navmesh/route_pic.py`

Use this tool. Never write another one-off renderer (several were rewritten
across compactions before it existed).

```bash
python tools/navmesh/route_pic.py --plugin <P> --world <WRLD> "<start x,y,z>" "<target x,y,z>" temp/<task>/<cell>_walk.png \
    [--box x0,y0,x1,y1] [--game-stop x,y,z] [--track <file of x,y,z lines>]
python tools/navmesh/route_pic.py --plugin <P> --interior <CELL> ...
```

It draws every cell the walk crosses (named), collision layers, the shipped
navmesh (triangle numbers = height above the floor), the planned route colored
green/orange/red by how the mesh sits on the floor, the **offline predictor's
stop**, the game's stop and logged track, and a key. Each cell loads its
neighbors' collision, so a long walk is slow (a 6-cell Arktwend walk passed
110 s; a 2-cell box finished): `--box` the 1-3 cells around the problem.

Other views: `tools/navmesh/render.py "<cell>" --export export/<plugin> --collision --z-shade`
(one cell), `slab_map.py` (floor layers vs mesh), `fix_analyze.py --cell X` (a
user's hand fix: only `result` vs `base` counts), and `test_instance shot` in game
(the **test-instances** skill).

## A walk stalls: predictor first

Before any theory, run the engine-matched offline predictor from the walk's start
to its target and set its stop beside the game's:

- `tools/navmesh/pathfind.py` (the CK's A* costs), `pathfind_body.py` (body test),
  `pathfind_calibrate.py` (`predict()`; scores logged walks from `walk_outcomes.py`).
- `route_pic.py` already draws the predictor's stop; use it as the worked example.
- Disagreement with the game = a calibration gap in the tool: fix the tool.
- The engine often stops an actor where it can *see* the problem (on entering the
  problem cell), so the mesh under the stop point proves nothing.
- Z samples return stacked storeys: take the surface nearest the expected height.
  The pathgrid line's slope is the reference for a ramp or stair.

## Pinning a cell

Bad mesh on a quest route gets a pin, not a generator change or a case workaround.

1. Back up `navmesh_pins/<plugin>.json` and the output ESM.
2. Plan on the **shipped** mesh: `floor_points.py` for real floor heights,
   `pin_region.py --plugin P --cell X ... [--pin]` (or edit in cellview and save).
3. Ship by in-place patch, never a reimport: `tools.cellview.bake.mesh_to_esm(plugin, cell, {})`
   for each pinned cell (and any neighbor a pinned flood node regrew). Unlink the
   ESM from a running test game first. Then `test_instance build`.
4. Check: `tools/navmesh/mesh_clean.py` at 0/0/0/0 (overlaps, T-junctions, slivers,
   holes), a zoomed render of the shipped mesh, the predictor, then rerun the
   failing walk in game. Never touch a pin the user made.

## Changing the generator

Code is C++ in `native/src/navgrow/` (`python native/build.py --module _navgrow_native`),
inside the per-cell build cellview runs. A regression gets the smallest fix in the
step that regressed.

All three checks use `tools/navmesh/navmesh_ab.py`: `build` rebuilds cells with the
current code into a pickle (run it before your change and after); the other
subcommands compare the two pickles.

1. **Protected cells** (Oblivion `ImperialDungeon*`, Nehrim `SchattenrufMine*`):
   `build --cells protected` on each side (about 20 s, mostly parsing the export),
   then `protected --plugin P before.pkl after.pkl`. Any difference: stop and ask.
2. **Pinned cells**: the same `protected` run prints them on their own line.
3. **Every health trait** on the changed cells: `build --cells all` (or the cells
   the change can touch) on each side, then `compare`, plus
   `tools/navmesh/engine_check.py`.
4. **One plugin build**, cellview's own (`world.plugin_meshes`), for the plugin the
   user will look at; copy the saved cellview pickle first as the baseline.
5. Report per plugin, interiors and exteriors apart: changed cells, merges/splits,
   pathgrid nodes covered/uncovered, OFF/ON timing, protected and pinned cells by
   name, the worst few cells read with a verdict and a picture link each.
