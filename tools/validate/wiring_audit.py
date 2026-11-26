"""Wiring audit: every script-to-record link in a converted plugin, checked offline.

Reads the converted plugin, its converted masters and their compiled scripts
once, then reports the links that fail silently in game: an attached script
with no .pex, a fragment the script lacks, an object property that reads None
on that attachment, a property bound to a missing or wrong-typed record, a
script on a host it cannot run on, a condition reading a variable the target
script does not expose, and TES4 scripts whose converted record carries nothing.

Usage:
    python -m tools.validate.wiring_audit -f Oblivion.esm [--max 15] [--tsv F] [--md F]
    python -m tools.validate.wiring_audit -f Oblivion.esm --check unbound --script TES4_MQ01Script

See: docs/audits/wiring.md#checks
"""

import argparse
import os
import re
import struct
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from asset_convert.game_paths import namespace_for, set_namespace
from asset_convert.sources import output_files
from asset_convert.sources.skyrim_assets import find_skyrim_data
from core.plugin_masters import masters_from_export_header
from output_layout import paths
from script_convert.constants import (is_generated_script_type,
                                      papyrus_script_name, safe_property_name,
                                      sanitize_name)
from script_convert.quest_fragments import stage_fragments
from script_convert.tes4.nodes import Assign, Blank, Comment, walk_stmts
from script_convert.tes4.parser import Mode, parse
from tes5_import.base.conditions import UNRESOLVED_VAR_SENTINEL
from tes5_import.base.text_reader import info_result_script, parse_export_file
from tools.dialog.quest_walkthrough import (parse_ctdas, parse_info_fragments,
                                            parse_psc, parse_qust_alias_scripts,
                                            parse_qust_fragments, parse_vmad)
from tools.esm.tes5_esm_reader import read_tes5_file
from tools.validate.vmad_property_typecheck import ACCEPTS, PERMISSIVE

#: Record types whose subrecords the audit reads; every other record is a header (FormID + type).
PARSE = frozenset({
    'QUST', 'INFO', 'PACK', 'NPC_', 'MGEF', 'ACHR', 'REFR', 'ACTI', 'ALCH',
    'ARMO', 'BOOK', 'CONT', 'DOOR', 'FLOR', 'FURN', 'INGR', 'KEYM', 'LIGH',
    'MISC', 'WEAP', 'APPA', 'SLGM', 'AMMO', 'SCRL', 'SCEN', 'PERK'})
PLACED = frozenset({'REFR', 'ACHR'})
BASE_OBJECTS = PARSE - {'QUST', 'INFO', 'PACK', 'MGEF', 'ACHR', 'REFR', 'SCEN', 'PERK'}
#: Native script type -> record signatures a script of that type can be attached to.
HOSTS = {
    'quest': {'QUST'}, 'topicinfo': {'INFO'}, 'activemagiceffect': {'MGEF'},
    'actor': {'NPC_', 'ACHR'}, 'objectreference': PLACED | BASE_OBJECTS,
    'package': {'PACK'}, 'scene': {'SCEN'}, 'perk': {'PERK'},
}
VALUE_TYPES = frozenset({'int', 'float', 'bool', 'string'})
#: Forms the engine creates at startup, so no plugin file holds them: PlayerRef.
ENGINE_FORMS = frozenset({0x00000014})
#: Property-name prefixes the converter leaves unbound on purpose (a cell with no movers gets no list).
UNBOUND_BY_DESIGN = ('tes4movers_',)
#: Skyrim record types that cannot carry a VMAD: 0 of 363 vanilla ALCH and 0 of 17 SLGM do.
NO_SCRIPT_HOSTS = frozenset({'ALCH', 'SLGM'})
F_GETVMQUESTVAR, F_GETVMSCRIPTVAR = 629, 630
#: TES4 record types whose SCRI names an object script, for the coverage check.
TES4_SCRIPTED = ('ACTI', 'ALCH', 'APPA', 'ARMO', 'BOOK', 'CLOT', 'CONT', 'CREA',
                 'DOOR', 'FLOR', 'FURN', 'INGR', 'KEYM', 'LIGH', 'MISC', 'NPC_',
                 'SGST', 'SLGM', 'WEAP', 'AMMO', 'QUST')
_IDENT = re.compile(r'[A-Za-z_]\w*')
_ASSIGNED = re.compile(r'\b([A-Za-z_]\w*)\s*=(?!=)')
_REMOTE_WRITE = re.compile(r'\.([A-Za-z_]\w*)\s*=(?!=)')
_TES4_REMOTE_SET = re.compile(r'\bset\s+\w+\s*\.\s*(\w+)\s+to\b', re.I)


# --------------------------------------------------------------------------
# The converted plugin and its masters
# --------------------------------------------------------------------------


@dataclass
class Rec:
    """One record as the audit sees it, FormID in the audited plugin's index space."""
    sig: str
    fid: int
    edid: str = ''
    base: int = 0
    scripts: list = field(default_factory=list)
    alias_scripts: list = field(default_factory=list)
    frags: list = field(default_factory=list)
    ctdas: list = field(default_factory=list)


@dataclass
class World:
    """The audited plugin, its masters and every script any of them ship."""
    plugin: str
    masters: list = field(default_factory=list)
    recs: dict = field(default_factory=dict)
    mine: list = field(default_factory=list)
    sigs: dict = field(default_factory=dict)
    known: set = field(default_factory=set)
    unchecked_index: set = field(default_factory=set)
    psc: dict = field(default_factory=dict)
    pex: set = field(default_factory=set)
    remote_writes: set = field(default_factory=set)
    tes4_vars: dict = field(default_factory=dict)
    tes4_remote: set = field(default_factory=set)


def _zstr(data: bytes) -> str:
    """A null-terminated subrecord string."""
    return data.rstrip(b'\0').decode('utf-8', errors='replace')


def _vmad(rec, sub_data: bytes, out: Rec) -> None:
    """Fill `out` with the scripts, fragments and alias scripts of one VMAD."""
    try:
        scripts, tail = parse_vmad(sub_data)
        out.scripts = scripts
        if rec.type == 'QUST':
            out.frags = [(s, f) for _st, _lg, s, f in parse_qust_fragments(tail)]
            out.alias_scripts = parse_qust_alias_scripts(tail)
        elif rec.type == 'INFO':
            out.frags = parse_info_fragments(tail)
    except (ValueError, struct.error, IndexError):
        out.scripts = [('<unparsable VMAD>', {})]


def _rec(rec, remap) -> Rec:
    """A parsed reader record restated as a Rec, its FormIDs remapped."""
    out = Rec(rec.type, remap(rec.form_id))
    for sub in rec.subrecords:
        if sub.type == 'EDID':
            out.edid = _zstr(sub.data)
        elif sub.type == 'NAME' and rec.type in PLACED and len(sub.data) == 4:
            out.base = remap(struct.unpack('<I', sub.data)[0])
        elif sub.type == 'VMAD':
            _vmad(rec, sub.data, out)
    if rec.type in ('INFO', 'PACK', 'QUST'):
        out.ctdas = parse_ctdas(rec)
        for c in out.ctdas:
            if c.func in (F_GETVMQUESTVAR, F_GETVMSCRIPTVAR):
                c.p1 = remap(c.p1)
    return out


def _mast(header) -> list:
    """MAST names of a file header, in index order."""
    return [_zstr(s.data) for s in header.subrecords if s.type == 'MAST']


def _remapper(file_masters: list, plugin_masters: list, own_index: int):
    """FormID remap from a file's own index space into the audited plugin's."""
    table = {i: plugin_masters.index(n) for i, n in enumerate(file_masters)
             if n in plugin_masters}
    own = len(file_masters)

    def remap(fid: int) -> int:
        """`fid` in the plugin's space; an unmapped index byte keeps its value."""
        idx = fid >> 24
        new = own_index if idx == own else table.get(idx, idx)
        return (new << 24) | (fid & 0xFFFFFF)
    return remap


def _load_file(world: World, path: str, plugin_masters: list, own_index: int,
               parse: bool) -> list:
    """Index a file into `world`, first come first kept; the FormIDs it added records for.

    The audited plugin keeps every record, overrides included; a master keeps
    only the records it owns.
    """
    header, recs, _loc = read_tes5_file(path, parse_types=PARSE if parse else frozenset())
    masters = _mast(header)
    remap = _remapper(masters, plugin_masters, own_index)
    is_plugin = own_index == len(plugin_masters)
    added = []
    for rec in recs:
        if not is_plugin and rec.form_id >> 24 != len(masters):
            continue
        fid = remap(rec.form_id)
        world.known.add(fid)
        world.sigs.setdefault(fid, rec.type)
        if parse and rec.subrecords and fid not in world.recs:
            world.recs[fid] = _rec(rec, remap)
            added.append(fid)
    return added


def _load_scripts(world: World, out_dir) -> None:
    """Add one plugin's compiled script names and parsed sources to `world`."""
    folder = os.path.join(str(out_dir), 'scripts')
    output_files.unpack(folder)
    if not os.path.isdir(folder):
        return
    world.pex |= {n[:-4].lower() for n in os.listdir(folder) if n.lower().endswith('.pex')}
    src = os.path.join(folder, 'Source')
    for name in os.listdir(src) if os.path.isdir(src) else ():
        if name.lower().endswith('.psc'):
            info = parse_psc(os.path.join(src, name))
            world.psc.setdefault((info.name or name[:-4]).lower(), info)
            for body in info.functions.values():
                world.remote_writes |= {w.lower() for w in _REMOTE_WRITE.findall(body)}


def load_world(plugin: str) -> World:
    """The plugin, every converted master, Skyrim's own masters as FormID sets, and all scripts."""
    world = World(plugin)
    esm = str(paths(plugin).esm)
    header, _r, _l = read_tes5_file(esm, parse_types=frozenset())
    masters = world.masters = _mast(header)
    world.mine = _load_file(world, esm, masters, len(masters), True)
    _load_scripts(world, paths(plugin).out)
    skyrim = find_skyrim_data()
    for idx, name in enumerate(masters):
        converted = paths(name).esm
        vanilla = os.path.join(str(skyrim or ''), name)
        if converted.is_file():
            _load_file(world, str(converted), masters, idx, True)
            _load_scripts(world, paths(name).out)
        elif skyrim and os.path.isfile(vanilla):
            _load_file(world, vanilla, masters, idx, False)
        else:
            world.unchecked_index.add(idx)
    return world


# --------------------------------------------------------------------------
# Scripts: inheritance, what a body uses, what a record carries
# --------------------------------------------------------------------------


def chain(world: World, script: str) -> list:
    """`script` and every ancestor, lowercased; the last is the native type."""
    out, cur = [], script.lower()
    while cur and cur not in out:
        out.append(cur)
        info = world.psc.get(cur)
        cur = (info.extends or '').lower() if info else ''
    return out


def read_only_names(info) -> set:
    """Lowercased identifiers a script's bodies read but never assign: the ones a binding must supply."""
    used, assigned = set(), set()
    for body in info.functions.values():
        used |= {t.lower() for t in _IDENT.findall(body)}
        assigned |= {t.lower() for t in _ASSIGNED.findall(body)}
    return used - assigned


def carried(world: World, fid: int) -> set:
    """Every script (and ancestor) a record runs: its own, and for a placed ref its base's."""
    rec = world.recs.get(fid)
    if rec is None:
        return set()
    own = [s for s, _p in rec.scripts]
    base = world.recs.get(rec.base)
    own += [s for s, _p in base.scripts] if base else []
    return {a for s in own for a in chain(world, s)}


def where(rec: Rec) -> str:
    """A record as a report names it."""
    return f'{rec.sig} {rec.edid or "-"} [{rec.fid:08X}]'


# --------------------------------------------------------------------------
# Checks on each attachment
# --------------------------------------------------------------------------


@dataclass
class Finding:
    """One wiring defect."""
    check: str
    script: str
    host: str
    detail: str


def _ours(world: World, script: str) -> bool:
    """True for a script this pipeline generates or ships."""
    low = script.lower()
    return low in world.psc or is_generated_script_type(script) or low.startswith('tes4')


def _compiled(world: World, rec: Rec, out: list) -> None:
    """not-compiled and missing-fragment for every script and fragment `rec` names."""
    for name, _props in rec.scripts + rec.alias_scripts:
        if _ours(world, name) and name.lower() not in world.pex:
            out.append(Finding('not-compiled', name, where(rec), 'no .pex: the engine binds nothing'))
    for name, func in rec.frags:
        info = world.psc.get(name.lower())
        if name.lower() not in world.pex and _ours(world, name):
            out.append(Finding('not-compiled', name, where(rec), f'fragment {func}: no .pex'))
        elif info and func.lower() not in info.functions:
            out.append(Finding('missing-fragment', name, where(rec),
                               f'VMAD names {func}, the script does not define it'))


def _type_problem(world: World, ptype: str, fid: int):
    """(check, why) a record cannot bind a property of `ptype`; None when it can or cannot be known."""
    low = ptype.lower()
    if ptype in PERMISSIVE or low in VALUE_TYPES:
        return None
    if low in world.psc and low not in HOSTS:
        if world.recs.get(fid) is None or low in carried(world, fid):
            return None
        return ('script-type', f'bound record does not carry {ptype}: casts to None')
    sig = world.sigs.get(fid)
    accepts = ACCEPTS.get(ptype)
    if sig is None or accepts is None or sig in accepts:
        return None
    return ('type', f'{ptype} bound to a {sig}')


def _binding_expected(world: World, pname: str, tes4_vars: set) -> bool:
    """False if a script assigns `pname`, it is a TES4 variable, or by design.

    See: docs/commentary/script_convert.md#resetinterior-sends-moved-refs-home
    """
    return not (pname in world.remote_writes or pname in tes4_vars
                or pname.startswith(UNBOUND_BY_DESIGN))


def _property(world: World, ctx: tuple, pname: str, ptype: str, bound, out: list) -> None:
    """unbound / dangling / type / script-type for one declared object property."""
    script, host, used, tes4_vars = ctx
    kind, val = bound if bound else (None, 0)
    if kind not in (None, 'obj') or ptype.lower() in VALUE_TYPES or ptype.endswith('[]'):
        return
    if not val:
        if pname in used and _binding_expected(world, pname, tes4_vars):
            out.append(Finding('unbound', script, host, f'{ptype} {pname} has no value: reads None'))
        return
    if (val not in world.known and val not in ENGINE_FORMS
            and val >> 24 not in world.unchecked_index):
        out.append(Finding('dangling', script, host, f'{ptype} {pname} -> {val:08X}: no such record'))
        return
    problem = _type_problem(world, ptype, val)
    if problem:
        out.append(Finding(problem[0], script, host,
                           f'{pname} -> {world.sigs.get(val, "?")} [{val:08X}]: {problem[1]}'))


def _properties(world: World, rec: Rec, uses: dict, out: list) -> None:
    """Every declared object property of every script attached to `rec`."""
    for name, props in rec.scripts + rec.alias_scripts:
        info = world.psc.get(name.lower())
        if info is None:
            continue
        used = uses.setdefault(name.lower(), read_only_names(info))
        ctx = (name, where(rec), used, world.tes4_vars.get(name.lower(), (set(), set()))[0])
        for pname, ptype in info.props.items():
            _property(world, ctx, pname, ptype, props.get(pname), out)


def _host(world: World, rec: Rec, out: list) -> None:
    """host: a script whose native type cannot run on the record it is attached to."""
    for name, _props in rec.scripts:
        native = chain(world, name)[-1]
        allowed = HOSTS.get(native)
        if allowed is not None and rec.sig not in allowed:
            out.append(Finding('host', name, where(rec),
                               f'extends {native}, attached to a {rec.sig}'))


# --------------------------------------------------------------------------
# Conditions that read script variables
# --------------------------------------------------------------------------


def _var_problem(world: World, scripts: list, var: str):
    """Why no script in `scripts` exposes `var` to conditions, or None when one does."""
    infos = [world.psc.get(s.lower()) for s in scripts]
    infos = [i for i in infos if i]
    if any(var in i.cond_vars for i in infos):
        return None
    if not scripts:
        return 'the target carries no script'
    if any(var in i.props for i in infos):
        return f'{var} is declared but not Conditional'
    return f'no attached script declares {var}'


def _conditions(world: World, rec: Rec, out: list) -> None:
    """condition-var: GetVMQuestVariable / GetVMScriptVariable that can never read its variable."""
    for c in rec.ctdas:
        if c.func not in (F_GETVMQUESTVAR, F_GETVMSCRIPTVAR) or not c.p1:
            continue
        if (c.cis2 or '').lower() == UNRESOLVED_VAR_SENTINEL.lower():
            continue
        var = (c.cis2 or '').replace('::', '').removesuffix('_var').lower()
        target = world.recs.get(c.p1)
        if target is None:
            if c.p1 not in world.known and c.p1 >> 24 not in world.unchecked_index:
                out.append(Finding('condition-var', '-', where(rec), f'target {c.p1:08X} missing'))
            continue
        base = world.recs.get(target.base)
        hosted = target.scripts + (base.scripts if base else []) + target.alias_scripts
        scripts = [s for s, _p in hosted]
        problem = _var_problem(world, scripts, var)
        if problem:
            out.append(Finding('condition-var', ','.join(scripts) or '-', where(rec),
                               f'reads {var} on {where(target)}: {problem}'))


# --------------------------------------------------------------------------
# Coverage: what TES4 attached, against what the output carries
# --------------------------------------------------------------------------


@dataclass
class Tes4Side:
    """What the TES4 export says should be wired, FormIDs in the converted plugin's space."""
    expected: dict = field(default_factory=dict)
    infos: dict = field(default_factory=dict)
    quests: set = field(default_factory=set)


def _tes4_scripts(export_dir: str, world: World) -> dict:
    """SCPT FormID -> Papyrus name; fills the world's TES4 variables and remote `set X.v to` writes."""
    set_namespace(namespace_for(export_dir))
    names = {}
    for r in parse_export_file(os.path.join(export_dir, 'SCPT.txt')):
        if not r.get('EditorID'):
            continue
        name = papyrus_script_name(sanitize_name(r['EditorID']))
        names[r['FormID'].upper()] = name
        tree = parse(r.get('SCTX', ''))
        assigned = {_papyrus_var(getattr(s.target, 'name', '')) for b in tree.blocks
                    for s in walk_stmts(b.body) if isinstance(s, Assign)}
        world.tes4_vars[name.lower()] = ({_papyrus_var(v.name) for v in tree.variables}, assigned)
        world.tes4_remote |= {_papyrus_var(w) for w in _TES4_REMOTE_SET.findall(r.get('SCTX', ''))}
    return names


def _papyrus_var(name: str) -> str:
    """A TES4 variable's converted Papyrus name, lowercased."""
    return safe_property_name(name).lower() if name else ''


def _has_code(text: str) -> bool:
    """True when a TES4 result script holds a statement, not only comments."""
    return any(not isinstance(s, (Comment, Blank)) for s in parse(text, Mode.FRAGMENT).body)


def load_tes4(export_dir: str, world: World) -> Tes4Side:
    """Read the export once: scripted records, result scripts, and TES4 variables into `world`."""
    remap = _remapper(masters_from_export_header(export_dir), world.masters, len(world.masters))
    names = _tes4_scripts(export_dir, world)
    side = Tes4Side()
    for sig in TES4_SCRIPTED:
        path = os.path.join(export_dir, f'{sig}.txt')
        for r in parse_export_file(path) if os.path.isfile(path) else ():
            if r.get('SCRI', '').upper() in names:
                side.expected[remap(int(r['FormID'], 16))] = names[r['SCRI'].upper()]
    for r in parse_export_file(os.path.join(export_dir, 'INFO.txt')):
        text = info_result_script(r)
        world.tes4_remote |= {_papyrus_var(w) for w in _TES4_REMOTE_SET.findall(text)}
        if r.get('ParentDIAL') and _has_code(text):
            side.infos[remap(int(r['FormID'], 16))] = remap(int(r['ParentDIAL'], 16))
    for r in parse_export_file(os.path.join(export_dir, 'QUST.txt')):
        texts = [f[3] for f in stage_fragments(r) if _has_code(f[3])]
        world.tes4_remote |= {_papyrus_var(w) for t in texts for w in _TES4_REMOTE_SET.findall(t)}
        if texts:
            side.quests.add(remap(int(r['FormID'], 16)))
    return side


def _object_coverage(world: World, side: Tes4Side, placed_by_base: dict, out: list) -> None:
    """coverage: a TES4 object or quest script the converted record and its placements lack."""
    alias_hosted = {s.lower() for r in world.recs.values() for s, _p in r.alias_scripts}
    for fid, script in side.expected.items():
        hosts = [fid] + placed_by_base.get(fid, [])
        if script.lower() in alias_hosted or any(script.lower() in carried(world, h) for h in hosts):
            continue
        rec = world.recs.get(fid)
        sig = rec.sig if rec else world.sigs.get(fid, '?')
        why = (f'Skyrim {sig} records cannot carry scripts' if sig in NO_SCRIPT_HOSTS
               else 'the converted record carries none')
        out.append(Finding('coverage', script, where(rec) if rec else f'{sig} [{fid:08X}]',
                           f'TES4 attached this script; {why}'))


def _result_coverage(world: World, side: Tes4Side, out: list) -> None:
    """coverage / dropped-topic: TES4 result scripts with no converted fragment."""
    for fid in sorted(set(side.infos) | side.quests):
        rec = world.recs.get(fid)
        if rec is not None and rec.frags:
            continue
        topic = side.infos.get(fid)
        if rec is None and topic is not None and topic not in world.known:
            out.append(Finding('dropped-topic', '-', f'INFO [{fid:08X}]',
                               f'the importer skipped its topic [{topic:08X}]: result script dropped'))
            continue
        kind = 'INFO result' if fid in side.infos else 'stage results'
        out.append(Finding('coverage', '-', where(rec) if rec else f'[{fid:08X}]',
                           f'TES4 {kind} script; the converted record has no fragment'))


def _lost_writes(world: World, scripts: set, out: list) -> None:
    """lost-write: a TES4 variable Oblivion sets, the converted script reads, and nothing sets."""
    for name in sorted(scripts):
        info, entry = world.psc.get(name), world.tes4_vars.get(name)
        if info is None or entry is None:
            continue
        variables, tes4_set = entry
        used, assigned = set(), set()
        for body in info.functions.values():
            used |= {t.lower() for t in _IDENT.findall(body)}
            assigned |= {t.lower() for t in _ASSIGNED.findall(body)}
        for var in sorted(variables & used - assigned - world.remote_writes):
            if var in tes4_set or var in world.tes4_remote:
                out.append(Finding('lost-write', name, '-',
                                   f'{var}: TES4 sets it, no converted script does'))


# --------------------------------------------------------------------------
# Running and reporting
# --------------------------------------------------------------------------


def audit(world: World, export_dir: str = None) -> list:
    """Every Finding for the audited plugin's own records; `export_dir` adds the TES4 comparisons."""
    side = load_tes4(export_dir, world) if export_dir else None
    placed_by_base = defaultdict(list)
    for r in world.recs.values():
        if r.sig in PLACED and r.base:
            placed_by_base[r.base].append(r.fid)
    out, uses, attached = [], {}, set()
    for rec in (world.recs[f] for f in world.mine):
        _compiled(world, rec, out)
        _properties(world, rec, uses, out)
        _host(world, rec, out)
        _conditions(world, rec, out)
        attached |= {s.lower() for s, _p in rec.scripts + rec.alias_scripts}
    if side:
        _object_coverage(world, side, placed_by_base, out)
        _result_coverage(world, side, out)
        _lost_writes(world, attached, out)
    return out


def report(findings: list, limit: int) -> str:
    """Counts per check, then up to `limit` examples each, as Markdown."""
    by_check = defaultdict(list)
    for f in findings:
        by_check[f.check].append(f)
    ranked = sorted(by_check.items(), key=lambda kv: -len(kv[1]))
    lines = ['| Check | Findings | Scripts |', '|---|---:|---:|']
    lines += [f'| `{c}` | {len(fs)} | {len({f.script for f in fs})} |' for c, fs in ranked]
    for check, fs in ranked:
        common = Counter(f.detail.split(':')[-1].strip() for f in fs).most_common(3)
        lines += ['', f'### {check}', '',
                  'Most common: ' + '; '.join(f'{d} ({n})' for d, n in common),
                  '', '| Script | Host | Detail |', '|---|---|---|']
        lines += [f'| `{f.script}` | {f.host} | {f.detail} |' for f in fs[:limit]]
    return '\n'.join(lines) + '\n'


def _wanted(findings: list, checks, scripts) -> list:
    """`findings` limited to the requested checks and scripts."""
    low = {s.lower() for s in scripts or ()}
    return [f for f in findings if (not checks or f.check in checks)
            and (not low or f.script.lower() in low)]


def main():
    """Load, audit and print; optionally write every finding as TSV and the report as Markdown."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-f', '--plugin', required=True)
    ap.add_argument('--no-coverage', action='store_true', help='skip the TES4 export comparison')
    ap.add_argument('--check', nargs='*', help='only these checks')
    ap.add_argument('--script', nargs='*', help='only these scripts')
    ap.add_argument('--max', type=int, default=15)
    ap.add_argument('--tsv')
    ap.add_argument('--md')
    args = ap.parse_args()
    started = time.time()
    world = load_world(args.plugin)
    print(f'{args.plugin}: {len(world.mine)} own records parsed, {len(world.psc)} sources, '
          f'{len(world.pex)} compiled ({time.time() - started:.0f}s)', flush=True)
    export = None if args.no_coverage else str(paths(args.plugin).records)
    findings = _wanted(audit(world, export), args.check, args.script)
    text = report(findings, args.max)
    print(text)
    if args.tsv:
        with open(args.tsv, 'w', encoding='utf-8') as fh:
            fh.writelines(f'{f.check}\t{f.script}\t{f.host}\t{f.detail}\n' for f in findings)
    if args.md:
        with open(args.md, 'w', encoding='utf-8') as fh:
            fh.write(text)
    print(f'{len(findings)} findings ({time.time() - started:.0f}s)')


if __name__ == '__main__':
    main()
