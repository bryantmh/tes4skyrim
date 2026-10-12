"""Asset references owned by a plugin, including base objects it places."""

from pathlib import Path

from core.plugin_masters import (master_chain, master_dir,
                                 masters_from_export_header)

MODEL_TYPES = frozenset({
    'ACTI', 'ALCH', 'AMMO', 'APPA', 'ARMO', 'BODY', 'BOOK', 'CLOT',
    'CONT', 'CREA', 'DOOR', 'EFSH', 'FLOR', 'FURN', 'GRAS', 'HAIR',
    'INGR', 'LIGH', 'MGEF', 'MISC', 'NPC_', 'RACE', 'STAT', 'TREE', 'WEAP',
    'ADDN', 'ARMA', 'DEBR', 'EXPL', 'IMOD', 'MSTT', 'PROJ', 'SCOL', 'TERM',
})
PLACEMENT_TYPES = frozenset({'REFR', 'ACRE', 'ACHR'})


def _records(folder, types):
    """Stream exports without loading large placement files into memory."""
    for sig in sorted(types):
        path = Path(folder) / f'{sig}.txt'
        if not path.is_file():
            continue
        with path.open(encoding='utf-8', errors='replace') as fh:
            rec = None
            for line in fh:
                line = line.strip()
                if line == '---RECORD_BEGIN---':
                    rec = {}
                elif line == '---RECORD_END---':
                    if rec:
                        yield sig, rec
                    rec = None
                elif rec is not None and '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    rec[key] = value


def _identity(value, owners):
    try:
        fid = int(value, 16)
    except (ValueError, TypeError):
        return None
    index = fid >> 24
    if index >= len(owners):
        return None
    return owners[index].lower(), fid & 0xffffff


def related_records(folder, types=MODEL_TYPES):
    """Own model records plus the master base objects placed by this plugin.

    FormIDs are matched by owning plugin and local ID, so a master's index
    changing between exports cannot select another master's unrelated model.
    """
    wanted = set()
    own_targets = {}
    owners = masters_from_export_header(str(folder)) + [Path(folder).name]
    placements = {'ACRE'} if types == {'CREA'} else PLACEMENT_TYPES
    for sig, rec in _records(folder, types | placements):
        if sig in PLACEMENT_TYPES:
            key = _identity(rec.get('NAME'), owners)
            if key:
                wanted.add(key)
        else:
            key = _identity(rec.get('FormID'), owners)
            if key:
                own_targets[key] = sig, rec
            yield sig, rec
    if not wanted:
        return
    targets = {}
    for name in master_chain(str(folder)):
        base = master_dir(str(folder), name)
        base_owners = masters_from_export_header(base) + [name]
        for sig, rec in _records(base, types):
            key = _identity(rec.get('FormID'), base_owners)
            if key in wanted:
                targets[key] = sig, rec
    targets.update(own_targets)
    for key in wanted - own_targets.keys():
        if key in targets:
            yield targets[key]


def model_paths(folder):
    """Lowercase mesh-relative NIF paths, including worn and ground variants."""
    out = set()
    for _sig, rec in related_records(folder):
        for value in rec.values():
            path = value.strip().lower().replace('\\', '/')
            while '//' in path:
                path = path.replace('//', '/')
            path = path.lstrip('/')
            if path.startswith('meshes/'):
                path = path[7:]
            if path.endswith('.nif'):
                out.add(path)
    return out
