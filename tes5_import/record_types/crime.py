"""Bounty realms and jails: the crime faction every converted NPC reports to.

A realm is the set of cells reachable, through teleport doors, from the root
worldspaces one plugin defines.  A door whose script calls
`SetPlayerInSEWorld` is a border: the cells behind a `1` door form a second
realm with its own bounty, which is how Oblivion keeps the Shivering Isles
apart.  A plugin that defines no root worldspace shares its nearest master's
realm, so every Morrowind or Nehrim plugin adds to one bounty.

Each realm owns one Track Crime faction.  A plugin's FormList
`TES4CrimeFactions_<plugin>` lists its realms, main first, for the converted
scripts.  A jail is a prison marker whose teleport lands in another interior
cell; its evidence chest is the engine's stolen-goods container in that cell.
TESRuntime points every crime faction at the nearest enabled jail, read from
the sidecar written here, because both source engines pick the jail that way.
"""

import json
import os
import re
import struct
from collections import Counter

from core.worldspace_names import (converted_worldspace_edid,
                                   converted_worldspace_name, renames_for)
from output_layout import paths

from ..base.owned_records import WELL_KNOWN_PROPERTIES
from ..base.text_reader import get_int, get_str, remap_formid
from ..base.writer import (pack_formid_subrecord, pack_record,
                           pack_string_subrecord, pack_subrecord)
from ..overrides.nested import (export_master_names, export_root,
                                master_export_dir)

#: Where TESRuntime reads its sidecars, under a plugin's output folder.
SIDECAR_DIR = os.path.join('SKSE', 'Plugins', 'TESRuntime')

#: The TES4 prison marker base; the TES3 export writes the same id.
_PRISON_MARKER = 0x00000004

#: The evidence container's id after `_fold`: StolenGoods, stolen_goods, 0stolenUgoods.
_EVIDENCE_KEY = 'stolengoods'

#: The prison marker's id after `_fold`, for a plugin that copies it as a record.
_PRISON_KEY = 'prisonmarker'

#: Skyrim.esm IsGuardFaction, which runs a guard's arrest and pursuit AI.
IS_GUARD_FACTION = 0x00086EEE

#: FACT DATA: Track Crime | Can Be Owner.
_POOL_FLAGS = 0x0040 | 0x8000

#: CRVA shared by all 14 real Skyrim crime factions.
_POOL_CRVA = struct.pack('<BBHHHHHfHH', 1, 1, 1000, 40, 5, 25, 0, 1.0, 100, 0)

#: A `SetPlayerInSEWorld n` line in a door's script, comments excluded.
_REALM_TOGGLE = re.compile(r'^[ \t]*setplayerinseworld[ \t]+([01])\b',
                           re.I | re.M)

#: The Papyrus property converted crime calls read the realm list from.
REALM_PROPERTY = 'TES4CrimeFactions'

#: TES4 CLAS DATA.Flags Guard.
_CLASS_GUARD = 0x02

#: (remapped) NPC_ FormID -> the crime faction it reports to.
_NPC_POOL: dict = {}

#: (remapped) guard CLAS FormIDs.
_GUARD_CLASSES: set = set()

#: [file, FormID] of this plugin's default crime faction, or [].
_DEFAULT_REF: list = []

#: REFR record flag Persistent.
_PERSISTENT_FLAG = 0x400


def _raw(rec: dict, key: str) -> int:
    """A raw export FormID field as an int, 0 when absent or malformed."""
    try:
        return int(rec.get(key) or '0', 16)
    except ValueError:
        return 0


def _pos(rec: dict) -> tuple:
    """A reference's (x, y)."""
    return float(rec.get('PosX') or 0), float(rec.get('PosY') or 0)


def _fold(edid: str) -> str:
    """An EditorID with Morroblivion's `_`->`U` and leading `0` mangling undone."""
    return re.sub('[_u]', '', (edid or '').lower()).lstrip('0')


def _is_interior(cell: dict) -> bool:
    """TES4 CELL interior test, as cell_family reads it."""
    return not cell.get('ParentWRLD') or bool(get_int(cell, 'DATA.Flags') & 1)


def read_sidecar(name: str, output_root: str) -> dict:
    """A converted plugin's crime sidecar, or {} when it has none."""
    try:
        out_dir = os.path.dirname(str(paths(name, out_root=output_root).esm))
    except (OSError, ValueError, TypeError):
        return {}
    stem = os.path.splitext(name)[0]
    path = os.path.join(out_dir, SIDECAR_DIR, f'{stem}.crime.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class _Masters:
    """The masters' export records and crime sidecars, in this plugin's raw space."""

    def __init__(self, export_dir: str, master_export: dict, output_root: str):
        """Index every master's own master list and read its sidecar."""
        self.names = export_master_names(export_dir)
        self.records = master_export or {}
        lower = [n.lower() for n in self.names]
        root = export_root(export_dir)
        self.maps, self.sidecars, self.renames = [], [], []
        for slot, name in enumerate(self.names):
            own = export_master_names(master_export_dir(root, name))
            index = {len(own): slot}
            index.update({k: lower.index(s.lower()) for k, s in enumerate(own)
                          if s.lower() in lower})
            self.maps.append(index)
            self.sidecars.append(read_sidecar(name, output_root))
            self.renames.append(renames_for([name, *own]))

    def get(self, raw: int) -> dict:
        """The master record this plugin names `raw`, or {}."""
        return self.records.get('%08X' % raw) or {}

    def side(self, raw: int) -> dict:
        """The sidecar of the master that defines `raw`, or {}."""
        slot = raw >> 24
        return self.sidecars[slot] if slot < len(self.sidecars) else {}

    def rekey(self, raw: int, slot: int) -> int:
        """A FormID read from master `slot`'s own record, restated in our space."""
        target = self.maps[slot].get(raw >> 24) if slot < len(self.maps) else None
        return 0 if target is None else (target << 24) | (raw & 0xFFFFFF)

    def world_realm(self, raw: int) -> str:
        """The realm of a master worldspace, from its owner's sidecar."""
        side = self.side(raw)
        found = side.get('worlds', {}).get(self.get(raw).get('EditorID', ''))
        return found or side.get('default', '')

    def cell_realm(self, raw: int) -> str:
        """The realm a master cell belongs to, from its owner's sidecar."""
        cell = self.get(raw)
        if cell and not _is_interior(cell):
            return self.world_realm(self.rekey(_raw(cell, 'ParentWRLD'), raw >> 24))
        side = self.side(raw)
        return (side.get('cells', {}).get('%06X' % (raw & 0xFFFFFF))
                or side.get('default', ''))

    def default_realm(self) -> dict:
        """The main realm of the nearest master that owns one, or {}."""
        for side in reversed(self.sidecars):
            for realm in side.get('realms', []):
                if realm.get('main'):
                    return realm
        return {}


class _Union:
    """Union-find over hashable nodes."""

    def __init__(self):
        """Start with every node alone."""
        self.parent = {}

    def find(self, node):
        """The representative of `node`'s set."""
        self.parent.setdefault(node, node)
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def join(self, a, b) -> None:
        """Merge the sets holding `a` and `b`."""
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


class _World:
    """This plugin's cells, worldspaces, bases and placed references."""

    def __init__(self, by_type: dict, masters: _Masters, plugin: str):
        """Index this plugin's own records of every type the crime pass reads."""
        self.m = masters
        self.own = len(masters.names)
        self.plugin = plugin
        self.stem = re.sub(r'\W', '', os.path.splitext(plugin)[0])
        self.cells = self._own(by_type, 'CELL')
        self.worlds = self._own(by_type, 'WRLD')
        self.bases = {}
        for sig in ('DOOR', 'CONT', 'SCPT'):
            self.bases.update(self._own(by_type, sig))
        self.refs = [r for r in by_type.get('REFR', [])
                     if _raw(r, 'FormID') >> 24 == self.own]
        self.pool_ids = {}

    def _own(self, by_type: dict, sig: str) -> dict:
        """{raw FormID: record} for this plugin's own records of one type."""
        return {_raw(r, 'FormID'): r for r in by_type.get(sig, [])
                if _raw(r, 'FormID') >> 24 == self.own}

    def base(self, raw: int) -> dict:
        """A base record, ours or a master's."""
        return self.bases.get(raw) or self.m.get(raw)

    def base_field(self, raw: int, key: str) -> int:
        """A FormID field of a base record, restated in our space."""
        value = _raw(self.base(raw), key)
        return value if raw in self.bases else self.m.rekey(value, raw >> 24)

    def cell(self, raw: int) -> dict:
        """A cell record, ours or a master's."""
        return self.cells.get(raw) or self.m.get(raw)

    def cell_world(self, raw: int) -> int:
        """The worldspace an exterior cell sits in, in our space."""
        world = _raw(self.cell(raw), 'ParentWRLD')
        return world if raw in self.cells else self.m.rekey(world, raw >> 24)

    def root(self, world: int):
        """('w', own root world) or ('r', master realm) for a worldspace."""
        seen = set()
        while world in self.worlds and world not in seen:
            seen.add(world)
            parent = _raw(self.worlds[world], 'WNAM.Parent')
            if not parent:
                return ('w', world)
            world = parent
        if self.claims(world):
            return ('w', world)
        return ('r', self.m.world_realm(world))

    def claims(self, world: int) -> bool:
        """Whether a master's worldspace becomes this plugin's own when converted.

        Arktwend builds on Morrowind's exterior grid, and the conversion renames
        that worldspace for it (WrldArktwend); a rename every plugin shares
        (Tamriel -> TES4Tamriel) claims nothing.
        See: docs/commentary/tes_runtime_crime.md#bounty-realms
        """
        slot = world >> 24
        edid = self.m.get(world).get('EditorID', '')
        return bool(edid) and slot < len(self.m.renames) and (
            converted_worldspace_edid(edid)
            != converted_worldspace_edid(edid, self.m.renames[slot]))

    def world_record(self, world: int) -> dict:
        """A worldspace record, ours or a master's."""
        return self.worlds.get(world) or self.m.get(world)

    def cell_node(self, raw: int):
        """A cell's node in the realm graph: ours, its worldspace's, or its master's realm."""
        if raw in self.cells:
            return ('c', raw)
        cell = self.m.get(raw)
        if cell and not _is_interior(cell):
            return self.root(self.cell_world(raw))
        return ('r', self.m.cell_realm(raw))

    def toggle(self, door_base: int) -> set:
        """The `SetPlayerInSEWorld` values this door's script sets."""
        script = self.base_field(door_base, 'SCRI')
        text = get_str(self.base(script), 'SCTX') if script else ''
        text = (text or '').replace('\\r\\n', '\n').replace('\\t', '\t')
        return set(_REALM_TOGGLE.findall(text))

    def file_local(self, raw: int) -> list:
        """[file, local id] naming `raw` in the running load order."""
        slot = raw >> 24
        name = self.plugin if slot >= len(self.m.names) else self.m.names[slot]
        return [name, raw & 0xFFFFFF]


def _door_targets(world: _World) -> dict:
    """{target door raw: (its cell raw, x, y)} for every door our refs lead to."""
    wanted = {_raw(r, 'XTEL.Door') for r in world.refs if r.get('XTEL.Door')}
    found = {}
    for ref in world.refs:
        fid = _raw(ref, 'FormID')
        if fid in wanted:
            found[fid] = (_raw(ref, 'ParentCELL'),) + _pos(ref)
    for fid in wanted - set(found):
        ref = world.m.get(fid)
        if ref:
            found[fid] = (world.m.rekey(_raw(ref, 'ParentCELL'), fid >> 24),) + _pos(ref)
    return found


def _doors(world: _World) -> list:
    """(source ref, source cell, target cell, target x, y, toggles) per teleport door."""
    targets = _door_targets(world)
    out = []
    for ref in world.refs:
        target = targets.get(_raw(ref, 'XTEL.Door'))
        if target:
            out.append((ref, _raw(ref, 'ParentCELL'), target[0], target[1],
                        target[2], world.toggle(_raw(ref, 'NAME'))))
    return out


def _link_cells(world: _World, union: _Union, doors: list) -> tuple:
    """Join the realm graph; returns (cells behind a `1` border, cells before it).

    A door pair is one passage: when either side's script toggles the realm,
    neither side joins the cells it links.
    """
    for raw, cell in world.cells.items():
        if not _is_interior(cell):
            union.join(('c', raw), world.root(_raw(cell, 'ParentWRLD')))
    borders = {f for ref, *_rest, toggles in doors if toggles
               for f in (_raw(ref, 'FormID'), _raw(ref, 'XTEL.Door'))}
    inside, outside = set(), set()
    for ref, src, dst, _x, _y, toggles in doors:
        if '1' in toggles:
            inside.add(dst)
            outside.add(src)
        elif _raw(ref, 'FormID') not in borders:
            union.join(world.cell_node(src), world.cell_node(dst))
    return inside, outside


def _behind_border_name(world: _World, roots: list, cells: set) -> str:
    """The EditorID naming a realm behind a border: its first root worldspace."""
    rec = world.worlds.get(roots[0], {}) if roots else world.cells[min(cells)]
    return re.sub(r'\W', '', get_str(rec, 'EditorID') or '')


def _component_realms(world: _World, union: _Union, borders: tuple,
                      default: 'str | None') -> dict:
    """{component representative: realm EditorID}; None where only the default fits."""
    inside, outside = borders
    groups = {}
    for node in list(union.parent):
        groups.setdefault(union.find(node), set()).add(node)
    main = f'TES4CrimeFaction_{world.stem}'
    realms = {}
    for rep, nodes in groups.items():
        cells = {n[1] for n in nodes if n[0] == 'c'}
        roots = sorted(n[1] for n in nodes if n[0] == 'w')
        masters = sorted(n[1] for n in nodes if n[0] == 'r' and n[1])
        if cells & inside and not cells & outside:
            realms[rep] = f'{main}_{_behind_border_name(world, roots, cells & inside)}'
        elif roots:
            realms[rep] = main
        else:
            realms[rep] = masters[0] if masters else default
    return realms


def _seed_anchors(world: _World, doors: list) -> tuple:
    """({interior: (world, x, y)} for doors onto the world, {cell: linked cells})."""
    anchors, links = {}, {}
    for ref, src, dst, tx, ty, _t in doors:
        scell, dcell = world.cell(src), world.cell(dst)
        if src in world.cells and _is_interior(scell) and dcell \
                and not _is_interior(dcell):
            anchors.setdefault(src, (world.cell_world(dst), tx, ty))
        elif scell and not _is_interior(scell) and dst in world.cells:
            anchors.setdefault(dst, (world.cell_world(src),) + _pos(ref))
        links.setdefault(src, set()).add(dst)
        links.setdefault(dst, set()).add(src)
    return anchors, links


def _anchors(world: _World, doors: list) -> dict:
    """{own interior cell raw: (world raw, x, y)}: where it opens onto the world."""
    anchors, links = _seed_anchors(world, doors)
    frontier = sorted(anchors)
    while frontier:
        nxt = []
        for cell in frontier:
            for other in sorted(links.get(cell, ())):
                if other in world.cells and other not in anchors:
                    anchors[other] = anchors[cell]
                    nxt.append(other)
        frontier = nxt
    return anchors


def _chests(world: _World) -> dict:
    """{cell raw: evidence chest ref raw}, the first stolen-goods container per cell."""
    out = {}
    for ref in world.refs:
        base = world.base(_raw(ref, 'NAME'))
        if base.get('Signature') == 'CONT' and \
                _fold(base.get('EditorID', '')) == _EVIDENCE_KEY:
            out.setdefault(_raw(ref, 'ParentCELL'), _raw(ref, 'FormID'))
    return out


def _jail_spot(world: _World, ref: dict, src: int, anchors: dict):
    """(world, x, y) a jail marker stands at, or None."""
    scell = world.cell(src)
    if scell and not _is_interior(scell):
        return (world.cell_world(src),) + _pos(ref)
    return anchors.get(src)


def _is_prison_marker(world: _World, base: int) -> bool:
    """The engine prison marker, or a plugin's copy of it (the Morroblivion patch)."""
    return (base == _PRISON_MARKER
            or _fold(world.base(base).get('EditorID', '')) == _PRISON_KEY)


def _jails(world: _World, doors: list, anchors: dict, cell_realm) -> list:
    """Every jail: its marker, where it stands, its evidence chest and realm."""
    chests = _chests(world)
    jails = []
    for ref, src, dst, _x, _y, _t in doors:
        dcell = world.cell(dst)
        if not _is_prison_marker(world, _raw(ref, 'NAME')) or dst == src \
                or not dcell or not _is_interior(dcell):
            continue
        spot = _jail_spot(world, ref, src, anchors)
        if spot:
            jails.append({'marker': _raw(ref, 'FormID'), 'chest': chests.get(dst, 0),
                          'world': spot[0], 'x': spot[1], 'y': spot[2],
                          'realm': cell_realm(src)})
    return sorted(jails, key=lambda j: j['marker'])


def _majority_realm(cell_realms: dict, owned: list, fallback: str) -> str:
    """The realm most of a plugin's own cells joined: its default.

    Knights of the Nine owns only a test worldspace, so a plugin's own realm is
    its default only where most of its cells are.
    See: docs/commentary/tes_runtime_crime.md#bounty-realms
    """
    counts = Counter(r for r in cell_realms.values() if r)
    if counts:
        return max(sorted(counts), key=counts.get)
    return owned[0] if owned else fallback


class _Plan:
    """Everything the crime pass decides for one plugin."""

    def __init__(self, world: _World, default: dict):
        """Build the realm graph and settle every cell's realm and every jail."""
        self.world = world
        doors = _doors(world)
        union = _Union()
        borders = _link_cells(world, union, doors)
        self.default = default.get('edid', '')
        realms = _component_realms(world, union, borders, None)
        self.main = f'TES4CrimeFaction_{world.stem}'
        self.cell_realm_map = {raw: realms.get(union.find(('c', raw)))
                               for raw in world.cells}
        owned = {r for r in realms.values() if r and r.startswith(self.main)}
        self.owned = (sorted(owned, key=lambda e: (e != self.main, e))
                      if self.main in owned else [])
        self.default = _majority_realm(self.cell_realm_map, self.owned,
                                       self.default)
        self.flst = (f'TES4CrimeFactions_{world.stem}'
                     if self.default in self.owned else default.get('flst', ''))
        self.anchors = _anchors(world, doors)
        self.jails = _jails(world, doors, self.anchors, self.cell_realm)
        self.root_cell = {}
        for raw, rec in sorted(world.cells.items()):
            if not _is_interior(rec):
                self.root_cell.setdefault(world.root(_raw(rec, 'ParentWRLD')), raw)

    def cell_realm(self, raw: int) -> str:
        """The realm of any cell this plugin names."""
        if raw in self.world.cells:
            return self.cell_realm_map.get(raw) or self.default
        return self.world.m.cell_realm(raw) or self.default

    def world_realm(self, raw: int) -> str:
        """The realm of one of our worldspaces: the realm its cells joined."""
        root = self.world.root(raw)
        if root[0] == 'r':
            return root[1] or self.default
        cell = self.root_cell.get(root)
        return self.cell_realm(cell) if cell else self.default


def _pool_full(plan: _Plan, edid: str) -> str:
    """The name a bounty message shows: the realm's biggest root worldspace's.

    A nameless biggest root (Oblivion's Tamriel) falls back to the plugin name.
    """
    counts = {}
    for raw, cell in plan.world.cells.items():
        if not _is_interior(cell) and plan.cell_realm_map.get(raw) == edid:
            key = plan.world.root(_raw(cell, 'ParentWRLD'))
            counts[key] = counts.get(key, 0) + 1
    biggest = max(sorted(counts), key=counts.get, default=('r', ''))
    rec = plan.world.world_record(biggest[1]) if biggest[0] == 'w' else {}
    full = converted_worldspace_name(get_str(rec, 'EditorID') or '',
                                     get_str(rec, 'FULL') or '')
    return full or os.path.splitext(plan.world.plugin)[0]


def _pool_record(plan: _Plan, edid: str, fid: int) -> bytes:
    """One realm's crime faction, pointed at the realm's first jail."""
    jail = next((j for j in plan.jails if j['realm'] == edid), None)
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_string_subrecord('FULL', _pool_full(plan, edid))
    subs += pack_subrecord('DATA', struct.pack('<I', _POOL_FLAGS))
    if jail:
        marker = remap_formid(jail['marker'])
        subs += pack_formid_subrecord('JAIL', marker)
        subs += pack_formid_subrecord('WAIT', marker)
        if jail['chest']:
            subs += pack_formid_subrecord('STOL', remap_formid(jail['chest']))
            subs += pack_formid_subrecord('PLCN', remap_formid(jail['chest']))
    subs += pack_subrecord('CRVA', _POOL_CRVA)
    return pack_record('FACT', fid, 0, subs)


def _write_pools(plan: _Plan, writer) -> dict:
    """Write our realms' factions and FormList; {realm EditorID: FACT FormID}."""
    pools = {}
    for edid in plan.owned:
        pools[edid] = writer.derive_formid('FACT', edid)
        writer.add_record('FACT', _pool_record(plan, edid, pools[edid]))
    if plan.default in plan.owned:
        fid = writer.derive_formid('FLST', plan.flst)
        subs = pack_string_subrecord('EDID', plan.flst)
        for edid in plan.owned:
            subs += pack_formid_subrecord('LNAM', pools[edid])
        writer.add_record('FLST', pack_record('FLST', fid, 0, subs))
        WELL_KNOWN_PROPERTIES[REALM_PROPERTY] = fid
    return pools


def _pool_fid(pools: dict, edid: str, master_index) -> int:
    """A realm's FACT FormID: ours, else the master's by EditorID."""
    if edid not in pools:
        pools[edid] = (master_index.find_by_edid(b'FACT', edid)
                       if edid and master_index is not None else 0)
    return pools[edid]


def _assign_npcs(by_type: dict, plan: _Plan, pools: dict, master_index) -> None:
    """Each NPC_ reports to the realm of its first placement's cell."""
    own_npcs = {_raw(r, 'FormID') for r in by_type.get('NPC_', [])}
    first = {}
    for ref in sorted(by_type.get('ACHR', []), key=lambda r: _raw(r, 'FormID')):
        base = _raw(ref, 'NAME')
        if base in own_npcs and base not in first:
            first[base] = _raw(ref, 'ParentCELL')
    for base in own_npcs:
        realm = plan.cell_realm(first[base]) if base in first else plan.default
        fid = _pool_fid(pools, realm, master_index)
        if fid:
            _NPC_POOL[remap_formid(base)] = fid


def _collect_guards(by_type: dict, masters: _Masters) -> None:
    """Remember every CLAS whose TES4 Guard flag is set, ours and the masters'."""
    for rec in by_type.get('CLAS', []):
        if get_int(rec, 'DATA.Flags') & _CLASS_GUARD:
            _GUARD_CLASSES.add(remap_formid(_raw(rec, 'FormID')))
    for key, rec in masters.records.items():
        if rec.get('Signature') == 'CLAS' and \
                get_int(rec, 'DATA.Flags') & _CLASS_GUARD:
            _GUARD_CLASSES.add(remap_formid(int(key, 16)))


def _root_raw(world: _World, raw: int) -> int:
    """The topmost worldspace above `raw` that this plugin or a master names."""
    seen = set()
    while raw not in seen:
        seen.add(raw)
        if raw in world.worlds:
            parent = _raw(world.worlds[raw], 'WNAM.Parent')
        else:
            parent = world.m.rekey(_raw(world.m.get(raw), 'WNAM.Parent'), raw >> 24)
        if not parent:
            break
        raw = parent
    return raw


def _sidecar(plan: _Plan) -> dict:
    """The JSON TESRuntime and our dependents read."""
    world = plan.world
    own = world.own << 24
    return {
        'version': 1, 'plugin': world.plugin,
        'default': plan.default, 'default_flst': plan.flst,
        'realms': [{'edid': e, 'flst': plan.flst, 'main': e == plan.default}
                   for e in plan.owned],
        'pools': [world.file_local(own | (world.pool_ids[e] & 0xFFFFFF))
                  for e in plan.owned],
        'worlds': {get_str(rec, 'EditorID'): plan.world_realm(raw)
                   for raw, rec in world.worlds.items() if get_str(rec, 'EditorID')},
        'world_roots': [world.file_local(raw) + world.file_local(_root_raw(world, raw))
                        for raw in sorted(world.worlds)],
        'cells': {'%06X' % (raw & 0xFFFFFF): realm
                  for raw, realm in sorted(plan.cell_realm_map.items())
                  if realm and _is_interior(world.cells[raw])},
        'anchors': [world.file_local(c) + world.file_local(a[0]) + [a[1], a[2]]
                    for c, a in sorted(plan.anchors.items())],
        'jails': [{'marker': world.file_local(j['marker']),
                   'chest': world.file_local(j['chest']) if j['chest'] else [],
                   'world': world.file_local(j['world']), 'x': j['x'], 'y': j['y']}
                  for j in plan.jails],
    }


def _write_sidecar(plan: _Plan, plugin_out_dir: str) -> None:
    """Write `<plugin>.crime.json` beside the other TESRuntime sidecars."""
    out_dir = os.path.join(plugin_out_dir, SIDECAR_DIR)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(plan.world.plugin)[0]
    with open(os.path.join(out_dir, f'{stem}.crime.json'), 'w',
              encoding='utf-8') as f:
        json.dump(_sidecar(plan), f, indent=1, sort_keys=True)


def _make_persistent(plan: _Plan) -> int:
    """Flag interior jail markers and chests persistent; exterior ones not already so.

    An exterior persistent ref belongs in its worldspace's persistent cell,
    which the export already does for the refs its author made persistent.
    """
    wanted = {f for j in plan.jails for f in (j['marker'], j['chest']) if f}
    exterior = 0
    for ref in plan.world.refs:
        if _raw(ref, 'FormID') not in wanted or \
                get_int(ref, 'RecordFlags') & _PERSISTENT_FLAG:
            continue
        if _is_interior(plan.world.cell(_raw(ref, 'ParentCELL'))):
            ref['RecordFlags'] = str(get_int(ref, 'RecordFlags') | _PERSISTENT_FLAG)
        else:
            exterior += 1
    return exterior


def plan_crime(by_type: dict, ctx, writer, export_dir: str, plugin_out_dir: str,
               output_path: str, output_root: str) -> None:
    """Decide realms, write their factions, assign NPCs and write the sidecar."""
    _NPC_POOL.clear()
    _GUARD_CLASSES.clear()
    _DEFAULT_REF.clear()
    masters = _Masters(export_dir, getattr(ctx, 'master_export', None) or {},
                       output_root)
    plan = _Plan(_World(by_type, masters, os.path.basename(output_path)),
                 masters.default_realm())
    pools = _write_pools(plan, writer)
    plan.world.pool_ids.update(pools)
    master_index = getattr(ctx, 'master_index', None) if ctx else None
    if plan.default not in plan.owned and plan.flst and master_index is not None:
        WELL_KNOWN_PROPERTIES[REALM_PROPERTY] = master_index.find_by_edid(
            b'FLST', plan.flst)
    _assign_npcs(by_type, plan, pools, master_index)
    default = _pool_fid(pools, plan.default, master_index)
    slot = default >> 24
    _DEFAULT_REF[:] = ([writer.masters[slot] if slot < len(writer.masters)
                        else plan.world.plugin, default] if default else [])
    _collect_guards(by_type, masters)
    loose = _make_persistent(plan)
    _write_sidecar(plan, plugin_out_dir)
    print(f"  Crime: realms {plan.owned or [plan.default]}, "
          f"{len(plan.jails)} jail(s) ({loose} exterior ref(s) not persistent), "
          f"{len(_NPC_POOL)} NPC(s) assigned, {len(_GUARD_CLASSES)} guard class(es)")


def crime_faction(npc_fid: int) -> int:
    """The crime faction a converted NPC_ reports to, or 0."""
    return _NPC_POOL.get(npc_fid, 0)


def default_crime_rows() -> list:
    """`crime=plugin|FormID` naming the realm's crime faction, for MorrowindRuntime."""
    if not _DEFAULT_REF:
        return []
    return [f'crime={_DEFAULT_REF[0]}|{_DEFAULT_REF[1]:08X}']


def is_guard_class(class_fid: int) -> bool:
    """Whether this (remapped) CLAS is a guard class."""
    return class_fid in _GUARD_CLASSES
