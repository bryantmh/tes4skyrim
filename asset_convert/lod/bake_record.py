"""When each worldspace's distant LOD was last baked, and from which plugins.

`create_lod.py` writes it into the LOD mod after every worldspace it bakes; the GUI reads it to tick
only the worldspaces whose LOD is out of date. It never ships (`install_mods.SIDECAR_NAMES`).
See: docs/commentary/core_gui.md#distant-lod-worldspaces
"""

import json
from pathlib import Path

#: The record's file name inside the LOD mod folder.
BAKE_RECORD_NAME = ".lod_bake_record.json"


def read_bakes(lod_dir) -> dict:
    """{worldspace: {"time": bake start (epoch s), "plugins": run order, "used": owner and overlays}}; {} if absent."""
    try:
        got = json.loads(Path(lod_dir, BAKE_RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def bake_of(lod_dir, bakes: dict, edid: str, plugins: list):
    """`edid`'s record; for a LOD mod an older build baked, one dated by its terrain folder (order taken as current).

    Legacy: drop the fallback once every user's LOD mod carries a record.
    """
    if edid in bakes:
        return bakes[edid]
    try:
        baked = Path(lod_dir, "meshes", "terrain", edid).stat().st_mtime
    except OSError:
        return None
    return {"time": baked, "plugins": list(plugins), "used": []}


def record_bake(lod_dir, edid: str, started: float, plugins=None, used=()) -> None:
    """Remember `edid`'s bake: the run's plugin order and the plugins it read (`plugins` None forgets it)."""
    bakes = read_bakes(lod_dir)
    if plugins is None:
        bakes.pop(edid, None)
    else:
        bakes[edid] = {"time": started, "plugins": list(plugins), "used": list(used)}
    Path(lod_dir).mkdir(parents=True, exist_ok=True)
    Path(lod_dir, BAKE_RECORD_NAME).write_text(json.dumps(bakes, indent=1, sort_keys=True), encoding="utf-8")


def is_stale(bake, owners: list, plugins: list, esm_mtime) -> bool:
    """Whether a bake no longer matches its plugins.

    Out of date when never baked, a plugin it read is no longer chosen, the owners (plugins shipping LOD for
    it) changed set or order, or one of them was rewritten since. `plugins` is the order Create LOD will read;
    `esm_mtime(name)` is a plugin's converted ESM time, or None when it has none.
    """
    if not bake:
        return True
    used = bake.get("used", ())
    if set(used) - set(plugins):
        return True
    if [p for p in plugins if p in owners] != [p for p in bake.get("plugins", ()) if p in owners]:
        return True
    return any((esm_mtime(p) or 0) > bake.get("time", 0) for p in set(owners) | set(used))
