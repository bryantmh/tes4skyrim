"""The choices behind the load-order mods, with no toolkit: masters, distant LOD, the body patch.

Masters: ticking a plugin ticks the masters it depends on, because the tool refuses a batch that
would leave an ESM mastering a plain ESP. LOD: plugin ORDER is conflict resolution (the last
plugin applied wins every tile two change), and a worldspace is offered only while a ticked
plugin ships LOD for it. Each picker scans its inputs once, so every toggle is a dict lookup.
See: docs/commentary/core_gui.md#load-order
"""

from asset_convert.lod.bake_record import bake_of, is_stale, read_bakes
from asset_convert.lod.sibling_lod import (LOD_DIR_NAME, converted_plugins, create_lod_order,
                                           defined_worldspaces_by_plugin, dependents_of, merge_worldspaces,
                                           worldspaces_by_plugin_diagnosed)
from core.gui.config import EXPORT_DIR, scan_skyrim_load_order
from core.plugin_masters import masters_first, masters_from_export_header
from output_layout import plugin_esm, record_dir
from tools.esm.make_master import FLAG_ESM, read_header, resolve

# ---------------------------------------------------------------------------
#  Converted plugins
# ---------------------------------------------------------------------------

def plugin_masters(session, name: str) -> list:
    """The MAST list of a converted plugin, or [] if it cannot be read."""
    try:
        return read_header(resolve(name, str(session.out_root())))[1]
    except Exception:
        return []


def esm_flags(session, names: list) -> dict:
    """name -> whether it is already flagged ESM, or None when unreadable."""
    out = {}
    for n in names:
        try:
            out[n] = bool(read_header(resolve(n, str(session.out_root())))[0] & FLAG_ESM)
        except Exception:
            out[n] = None
    return out


def converted(session) -> list:
    """Every converted plugin, sorted; [] when the output cannot be read."""
    try:
        return sorted(converted_plugins(session.out_root()))
    except Exception:
        return []


def default_master_plugins(session) -> list:
    """Converted plugins in dependency order: a plugin always follows its masters."""
    names = converted(session)
    known = set(names)
    deps = {n: [m for m in plugin_masters(session, n) if m in known] for n in names}
    ordered, seen = [], set()

    def _visit(n, stack=()):
        """Depth-first, leaving a cycle wherever it lands."""
        if n in seen or n in stack:
            return
        for m in deps.get(n, ()):
            _visit(m, stack + (n,))
        seen.add(n)
        ordered.append(n)

    for n in names:
        _visit(n)
    return ordered


def default_lod_plugins(session) -> list:
    """Converted plugins in plugins.txt order, the rest appended (as `create_lod_order` applies them)."""
    try:
        return create_lod_order(converted_plugins(session.out_root()), EXPORT_DIR)
    except Exception:
        return []


def refresh_patch_plugins(session) -> list:
    """The Skyrim load order as [(name, ticked)], keeping ticks already made."""
    names, default_on = scan_skyrim_load_order(session.tes5)
    old = dict(session.selection.patch_plugins)
    session.selection.patch_plugins = [(n, old.get(n, n in default_on)) for n in names]
    return session.selection.patch_plugins


# ---------------------------------------------------------------------------
#  Masters
# ---------------------------------------------------------------------------

class MasterPick:
    """The masters dialog's state: which converted plugins to flag ESM."""

    def __init__(self, session):
        """Read every converted plugin's masters and current flag once."""
        self.names = default_master_plugins(session)
        known = set(self.names)
        self.deps = {n: [m for m in plugin_masters(session, n) if m in known] for n in self.names}
        self.is_esm = esm_flags(session, self.names)
        chosen = session.selection.master_plugins
        self.ticked = set(chosen) & known if chosen else set(self.names)

    def toggle(self, name: str, on: bool) -> None:
        """Tick a plugin and its masters, or untick it and everything resting on it."""
        if on:
            stack = [name]
            while stack:
                cur = stack.pop()
                self.ticked.add(cur)
                stack += [m for m in self.deps.get(cur, ()) if m not in self.ticked]
            return
        self.ticked.discard(name)
        changed = True
        while changed:
            gone = {n for n in self.ticked if any(m not in self.ticked for m in self.deps.get(n, ()))}
            self.ticked -= gone
            changed = bool(gone)

    def set_all(self, on: bool) -> None:
        """Tick or clear every plugin."""
        self.ticked = set(self.names) if on else set()

    def problems(self) -> str:
        """Why the ticked set is invalid, or "" when it is valid."""
        bad = [f"{n} needs {m}" for n in self.names if n in self.ticked
               for m in self.deps.get(n, ()) if m not in self.ticked and not self.is_esm.get(m)]
        return "; ".join(bad[:3]) + (f" (+{len(bad) - 3} more)" if len(bad) > 3 else "")

    def rows(self) -> list:
        """(name, detail, tag, ticked) per plugin, in dependency order."""
        return [(n, "needs " + ", ".join(self.deps[n]) if self.deps.get(n) else "",
                 "already ESM" if self.is_esm.get(n) else "", n in self.ticked) for n in self.names]

    def chosen(self) -> list:
        """The ticked plugins, in dependency order."""
        return [n for n in self.names if n in self.ticked]


# ---------------------------------------------------------------------------
#  Distant LOD
# ---------------------------------------------------------------------------

class LodPick:
    """The LOD choice: an ordered, tickable plugin list, and Force (offer worldspaces with no shipped LOD)."""

    def __init__(self, session):
        """Seed from the confirmed selection and scan the exports once."""
        names = default_lod_plugins(session)
        kept = session.selection.lod_order
        self.order = [n for n in kept if n in names] + [n for n in names if n not in kept]
        self.wanted = set(names) - set(session.selection.lod_off)
        self.session, self.names = session, names
        self.deps, self.ws_by, self.ws_why = _scan_lod(session, names)
        self.force = session.selection.lod_force
        self.defined_by = None

    def defined(self) -> dict:
        """Every worldspace each plugin's converted ESM defines; read on first use, since only Force needs it."""
        if self.defined_by is None:
            try:
                self.defined_by = defined_worldspaces_by_plugin(self.names, EXPORT_DIR, self.session.out_root())
            except Exception:
                self.defined_by = {}
        return self.defined_by

    def disabled(self) -> set:
        """Plugins resting on a master the user turned off (deps are already transitive)."""
        out = set()
        for n in self.order:
            if n not in self.wanted:
                out |= set(self.deps.get(n, ()))
        return out - {n for n in self.order if n not in self.wanted}

    def checked(self) -> list:
        """The plugins that will run, in order."""
        off = self.disabled()
        return [n for n in self.order if n in self.wanted and n not in off]

    def toggle_plugin(self, index: int, on: bool) -> None:
        """Tick or untick the plugin in row `index`."""
        name = self.order[index]
        (self.wanted.add if on else self.wanted.discard)(name)

    def move(self, src: int, dst: int) -> None:
        """Move the plugin in row `src` to row `dst`."""
        if 0 <= dst < len(self.order):
            self.order.insert(dst, self.order.pop(src))

    def worlds(self) -> list:
        """The worldspaces the ticked plugins offer, in order."""
        names = self.checked()
        live = merge_worldspaces(names, self.ws_by)
        if self.force:
            live += [w for w in merge_worldspaces(names, self.defined()) if w not in set(live)]
        return live

    def owners(self, world: str) -> list:
        """The ticked plugins that ship LOD for `world` (else, under Force, that define it), in order."""
        names = self.checked()
        shipped = [n for n in names if world in self.ws_by.get(n, ())]
        return shipped or [n for n in names if world in self.defined().get(n, ())]

    def all_plugins(self, on: bool) -> None:
        """Tick or untick every plugin."""
        self.wanted = set(self.order) if on else set()

    def why_empty(self) -> str:
        """Why no worldspace is offered, per plugin."""
        why = [self.ws_why[n] for n in self.checked() if n in self.ws_why]
        why += [v for k, v in self.ws_why.items() if k == ""]
        if not why:
            return "No ticked plugin has worldspace terrain to generate LOD from."
        return "\n\n".join(why[:6]) + (f"\n\n(+{len(why) - 6} more)" if len(why) > 6 else "")

    def plugin_rows(self) -> list:
        """(name, ticked, enabled) per plugin, in order."""
        off = self.disabled()
        on = set(self.checked())
        return [(n, n in on, n not in off) for n in self.order]

    def confirm(self, session) -> str:
        """Store the choice, masters first as Create LOD applies it; returns why it can't be used, or ""."""
        plugins = self.checked()
        if not plugins:
            return "Tick at least one plugin to generate LOD for."
        order = masters_first(self.order, lambda n: masters_from_export_header(str(record_dir(EXPORT_DIR, n))))
        session.selection.lod_order[:] = order
        session.selection.lod_plugins[:] = [n for n in order if n in plugins]
        session.selection.lod_off[:] = [n for n in order if n not in self.wanted]
        session.selection.lod_force = self.force
        return ""


def _scan_lod(session, names: list) -> tuple:
    """(dependents, worldspaces by plugin, why-empty)."""
    try:
        ws_by, ws_why = worldspaces_by_plugin_diagnosed(names, EXPORT_DIR, session.out_root())
        return dependents_of(names, EXPORT_DIR), ws_by, ws_why
    except Exception as exc:
        return {n: set() for n in names}, {n: [] for n in names}, {"": f"Worldspace scan failed: {exc}"}


def world_rows(session, pick: LodPick) -> list:
    """[(worldspace, plugins shipping LOD for it, out of date)] for every worldspace `pick` offers.

    See: docs/commentary/core_gui.md#distant-lod-worldspaces
    """
    out = session.out_root()
    lod_dir = out / LOD_DIR_NAME
    bakes = read_bakes(lod_dir)
    plugins = pick.checked()

    def _mtime(name):
        """The converted ESM's time, or None when there is none."""
        try:
            return plugin_esm(out, name, EXPORT_DIR).stat().st_mtime
        except OSError:
            return None

    rows = []
    for world in pick.worlds():
        owners = pick.owners(world)
        rows.append((world, owners, is_stale(bake_of(lod_dir, bakes, world, plugins), owners, plugins, _mtime)))
    return rows
