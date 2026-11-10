"""Which navmesh generator the import stage runs: the lattice or the corridor.

The ``TESCONV_NAVMESH_GENERATOR`` env var carries the choice from convert.py
(``--navmesh-generator``) and the GUI's Settings menu down to the navmesh pool
workers, which inherit the environment.  Anything unset or unrecognised reads
as the default.
"""
import os

NAVMESH_GENERATOR_ENV_VAR = "TESCONV_NAVMESH_GENERATOR"
LATTICE, CORRIDOR = "lattice", "corridor"
GENERATORS = (LATTICE, CORRIDOR)
DEFAULT_GENERATOR = CORRIDOR


def set_navmesh_generator(name) -> None:
    """Pin the generator for this process and its children; None leaves it alone."""
    if name:
        os.environ[NAVMESH_GENERATOR_ENV_VAR] = name


def navmesh_generator() -> str:
    """The generator this process should build navmeshes with."""
    raw = os.environ.get(NAVMESH_GENERATOR_ENV_VAR, "").strip().lower()
    return raw if raw in GENERATORS else DEFAULT_GENERATOR


#: Carries the user's navmesh pin folder (``--navmesh-pins``) to the pool workers.
NAVMESH_PINS_ENV_VAR = "TESCONV_NAVMESH_PINS"


def set_navmesh_pins_dir(path) -> None:
    """Set the user's pin folder for this process and its children; None skips."""
    if path:
        os.environ[NAVMESH_PINS_ENV_VAR] = os.path.abspath(path)


def navmesh_pins_dir() -> str:
    """The user's pin folder, read over the shipped pins; '' when none is set.

    See: docs/commentary/tes5_import_navmesh.md#user-pin-folder
    """
    return os.environ.get(NAVMESH_PINS_ENV_VAR, "").strip()


#: Carries the plugin being imported to the pool workers, which read its pin file.
NAVMESH_PLUGIN_ENV_VAR = "TESCONV_NAVMESH_PLUGIN"


def set_navmesh_plugin(name) -> None:
    """Name the plugin whose pins this process and its children read."""
    os.environ[NAVMESH_PLUGIN_ENV_VAR] = name or ""


#: Set to "edge" when a build applies only the pins that reach a cell's edge (cellview's saved build).
NAVMESH_PIN_SCOPE_ENV_VAR = "TESCONV_NAVMESH_PIN_SCOPE"


def set_edge_pins_only(on) -> None:
    """Apply only the pins that reach a cell's edge in this process and its children."""
    os.environ[NAVMESH_PIN_SCOPE_ENV_VAR] = "edge" if on else ""


def edge_pins_only() -> bool:
    """True when only the pins that reach a cell's edge apply."""
    return os.environ.get(NAVMESH_PIN_SCOPE_ENV_VAR, "").strip() == "edge"


#: "1" when a build keeps each cell's gap-flood graph in its meta and skips the geometry cache (cellview).
NAVMESH_KEEP_FLOOD_ENV_VAR = "TESCONV_NAVMESH_KEEP_FLOOD"


def set_keep_flood(on) -> None:
    """Keep the gap-flood graph, in this process and its children."""
    os.environ[NAVMESH_KEEP_FLOOD_ENV_VAR] = "1" if on else ""


def keep_flood() -> bool:
    """True when builds keep the gap-flood graph."""
    return os.environ.get(NAVMESH_KEEP_FLOOD_ENV_VAR, "").strip() == "1"


def navmesh_plugin() -> str:
    """The plugin whose pins apply, e.g. `TR_Mainland.esm`; '' when none is set.

    See: docs/commentary/tes5_import_navmesh.md#pins-key-on-the-plugin
    """
    return os.environ.get(NAVMESH_PLUGIN_ENV_VAR, "").strip()
