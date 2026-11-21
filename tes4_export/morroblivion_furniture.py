"""
The hybrid mode's furniture plugin: a Morrowind root's beds, benches and stools as Skyrim furniture.

Hybrid mode gives the furniture Morroblivion's meshes, whose markers let an
actor sit or sleep, but Morrowind places them as STAT and ACTI records, which
nobody can use. A record cannot change type in an override, so each masterless
Morrowind-format root gets `<root> - Morroblivion Furniture.esp`: one FURN per
such base, keeping its name, model and script, and an override of every
placement of the base that places the FURN instead. Its masters are the root
and every plugin exported in hybrid mode whose masters all lead back to it.

See: docs/commentary/tes4_export_morrowind.md#hybrid-furniture
"""

import os
import time
from pathlib import Path

from core.plugin_masters import masters_from_export_header
from output_layout import assets_for, record_dir
from source_paths import resolve_plugin_path
from tes5_import.base.text_reader import parse_export_file

from .export_morrowind import morroblivion_exports, write_export, write_header
from .morroblivion import archive_path
from .morroblivion_hybrid import FURNITURE_MARKER, MANIFEST_NAME, find_under
from .morrowind_ids import remap_form_id
from .morrowind_patch import patch_formid
from .record_types.common import escape_value
from .tes3_reader import is_tes3, read_masters

#: Ends every furniture plugin's name, after its root's stem.
FURNITURE_SUFFIX = ' - Morroblivion Furniture.esp'

#: FormID derivation site of its FURN records.
FURNITURE_SITE = 'mwfurn'

#: The only steps a furniture plugin has work for.
FURNITURE_STEPS = ('export', 'import')

#: Base record types a Morrowind furniture piece is exported as.
BASE_TYPES = ('STAT', 'ACTI')

#: A base's fields the FURN keeps.
BASE_FIELDS = ('EditorID', 'FULL', 'Model.MODL', 'Model.MODB', 'Model.OriginShift', 'SCRI')

#: Fields of a base or placement that hold a FormID.
ID_FIELDS = ('FormID', 'NAME', 'ParentCELL', 'XOWN.Owner', 'XTEL.Door', 'XLOC.Key', 'SCRI')

#: TES4 record flag of a deleted record.
_DELETED = 0x20


def furniture_name(root: str) -> str:
    """The furniture plugin of masterless plugin `root`."""
    return os.path.splitext(root)[0] + FURNITURE_SUFFIX


def is_furniture(plugin: str) -> bool:
    """Whether `plugin` is a hybrid furniture plugin."""
    return plugin.endswith(FURNITURE_SUFFIX)


def tes3_roots(plugin: str, tes4_data: str, export_dir: str) -> list:
    """The masterless Morrowind-format plugins `plugin`'s master chain starts from (itself when masterless)."""
    path = resolve_plugin_path(plugin, tes4_data, export_dir)
    if not os.path.isfile(path) or not is_tes3(path):
        return []
    masters = read_masters(path)
    if not masters:
        return [plugin]
    roots = []
    for master in masters:
        roots += [r for r in tes3_roots(master, tes4_data, export_dir) if r not in roots]
    return roots


def furniture_targets(order: list, tes4_data: str, export_dir: str) -> list:
    """The furniture plugins a hybrid run over `order` rebuilds, one per root its plugins reach."""
    out = []
    for plugin in order:
        for root in [] if is_furniture(plugin) else tes3_roots(plugin, tes4_data, export_dir):
            if furniture_name(root) not in out + order:
                out.append(furniture_name(root))
    return out


def _hybrid_exports(export_dir: str) -> dict:
    """{plugin: record dir} of every plugin exported in hybrid mode, mod folders included."""
    headers = list(Path(export_dir).glob('*/_HEADER.txt')) + list(Path(export_dir).glob('*/*/_HEADER.txt'))
    return {h.parent.name: str(h.parent) for h in headers if (h.parent / MANIFEST_NAME).is_file()}


def family(export_dir: str, root: str) -> list:
    """[(name, record dir)]: `root`, then each hybrid export whose masters are all already listed, in load order."""
    exports = _hybrid_exports(export_dir)
    if root not in exports:
        return []
    out = [(root, exports.pop(root))]
    ready = [None]
    while ready:
        listed = {n.lower() for n, _p in out}
        ready = sorted((n for n, p in exports.items() if _within(masters_from_export_header(p), listed)),
                       key=lambda n: _load_key(export_dir, n))
        out += [(n, exports.pop(n)) for n in ready]
    return out


def _load_key(export_dir: str, plugin: str) -> tuple:
    """Morrowind's load order: masters first, then the source file's date, then the name."""
    path = resolve_plugin_path(plugin, '', export_dir)
    when = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    return (not plugin.lower().endswith('.esm'), when, plugin.lower())


def _within(masters: list, listed: set) -> bool:
    """Whether a plugin has masters and every one is already `listed`."""
    return bool(masters) and all(m.lower() in listed for m in masters)


def _remap(path: str, name: str, names: list) -> dict:
    """The index re-keying from one master's export into this plugin's master list."""
    own = masters_from_export_header(path)
    slot = {n.lower(): i for i, n in enumerate(names)}
    remap = {len(own): slot[name.lower()]}
    remap.update({k: slot[m.lower()] for k, m in enumerate(own) if m.lower() in slot})
    return remap


def _rebased(rec: dict, remap: dict) -> dict:
    """`rec` with every FormID field re-keyed; a field naming an unreachable file is dropped."""
    out = {}
    for key, value in rec.items():
        if key in ID_FIELDS:
            value = remap_form_id(value, remap)
        if value is not None:
            out[key] = value
    return out


def _records(masters, names, signatures):
    """Every record of `signatures` the masters export, re-keyed into this plugin's list, in load order."""
    for name, path in masters:
        remap = _remap(path, name, names)
        for sig in signatures:
            for rec in parse_export_file(os.path.join(path, f'{sig}.txt')):
                yield _rebased(rec, remap)


def _marker_meshes(masters):
    """A test of whether a mesh path is a furniture mesh in any master's tree, reading each file once."""
    roots = [assets_for(path) / 'meshes' for _name, path in masters]
    seen = {}

    def is_marker(model: str) -> bool:
        """Whether `model` carries furniture markers."""
        rel = archive_path(model)
        if rel not in seen:
            found = find_under(roots, rel)
            seen[rel] = bool(found) and FURNITURE_MARKER in found.read_bytes()
        return seen[rel]
    return is_marker


def _morroblivion_flags(export_dir: str) -> dict:
    """{Morroblivion furniture model: its MNAM flags}, which open its seats and mark a bed."""
    out = {}
    for name in morroblivion_exports(export_dir):
        for rec in parse_export_file(os.path.join(record_dir(export_dir, name), 'FURN.txt')):
            if rec.get('Model.MODL'):
                out.setdefault(archive_path(rec['Model.MODL']), rec.get('MNAM.Flags', '0'))
    return out


def furniture_bases(masters, names) -> dict:
    """{base FormID: fields} of every STAT/ACTI wearing a furniture mesh, a later master winning."""
    is_marker = _marker_meshes(masters)
    bases = {}
    for rec in _records(masters, names, BASE_TYPES):
        form_id = rec.get('FormID')
        if form_id and is_marker(rec.get('Model.MODL', '')):
            bases[form_id] = rec
        else:
            bases.pop(form_id, None)
    return bases


def furniture_records(bases: dict, flags: dict, own_index: int) -> dict:
    """{base FormID: (FURN FormID, lines)} for every furniture base."""
    out = {}
    for base, rec in sorted(bases.items()):
        form_id = patch_formid(('FURN', rec.get('EditorID') or base), own_index, FURNITURE_SITE)
        lines = ['RecordFlags=0'] + [f'{k}={escape_value(rec[k])}' for k in BASE_FIELDS if rec.get(k)]
        lines.append(f"MNAM.Flags={flags.get(archive_path(rec['Model.MODL']), '0')}")
        out[base] = (form_id, lines)
    return out


def placements(masters, names, furn: dict) -> list:
    """[(FormID, lines)] overriding every live placement of a furniture base to place its FURN."""
    latest = {}
    for rec in _records(masters, names, ('REFR',)):
        if rec.get('FormID'):
            latest[rec['FormID']] = rec
    out = []
    for form_id, rec in sorted(latest.items()):
        if rec.get('NAME') not in furn or int(rec.get('RecordFlags') or 0) & _DELETED:
            continue
        rec['NAME'] = furn[rec['NAME']][0]
        out.append((form_id, [f'{k}={escape_value(v)}' for k, v in rec.items()
                              if k not in ('Signature', 'FormID')]))
    return out


def _root_of(export_dir: str, plugin: str) -> str:
    """The root plugin a furniture plugin belongs to: its stem as a hybrid-exported .esm, else .esp."""
    stem = plugin[:-len(FURNITURE_SUFFIX)]
    exports = _hybrid_exports(export_dir)
    return next((stem + ext for ext in ('.esm', '.esp') if stem + ext in exports), stem + '.esm')


def run_furniture_export(export_dir: str, plugin: str, progress=print) -> bool:
    """Export furniture plugin `plugin` against its root's hybrid family; False with a reason when it cannot."""
    start = time.time()
    root = _root_of(export_dir, plugin)
    masters = family(export_dir, root)
    if not masters:
        progress(f'[{plugin}] ERROR: export {root} in hybrid mode first (--morrowind-source hybrid).')
        return False
    names = [name for name, _path in masters]
    furn = furniture_records(furniture_bases(masters, names),
                             _morroblivion_flags(export_dir), len(names))
    refs = placements(masters, names, furn)
    out_dir = str(record_dir(export_dir, plugin))
    counts = write_export({'FURN': list(furn.values()), 'REFR': refs}, out_dir)
    write_header(out_dir, names, sum(counts.values()), f'{root} furniture for Morroblivion meshes', 0)
    progress(f'[{plugin}] {len(furn)} furniture records, {len(refs)} placements overridden '
             f"across {', '.join(names)}, in {time.time() - start:.1f}s")
    return True
