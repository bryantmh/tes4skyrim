"""Which plugin wrote which script in a `scripts/` folder several plugins share.

An imported mod's plugins convert into ONE output folder, so their scripts sit
side by side. Each plugin's run records the names it wrote in
`scripts/<owner>.owned.txt`; a rebuild clears only its own, and the compiler
compiles only its own.

See: docs/commentary/script_convert.md#wipe-output-dir
"""

import os
from pathlib import Path

#: Suffix of the per-plugin list of scripts written into a shared `scripts/` folder.
OWNED_SUFFIX = '.owned.txt'


def owner_key(export_dir) -> str:
    """The name a plugin's ownership list is filed under: its record folder's."""
    return os.path.basename(os.path.normpath(str(export_dir)))


def owned_list_path(source_dir, owner: str) -> str:
    """`scripts/<owner>.owned.txt`, beside the `scripts/source/` tree `source_dir`."""
    return os.path.join(os.path.dirname(str(source_dir)), owner + OWNED_SUFFIX)


def read_owned(source_dir, owner: str) -> set:
    """The script names `owner`'s last run wrote; empty when it kept no list."""
    try:
        with open(owned_list_path(source_dir, owner), encoding='utf-8') as fh:
            return {line.strip() for line in fh if line.strip()}
    except OSError:
        return set()


def write_owned(source_dir, owner: str, names) -> None:
    """Record the script names `owner` wrote this run."""
    with open(owned_list_path(source_dir, owner), 'w', encoding='utf-8') as fh:
        fh.write(''.join(f'{n}\n' for n in sorted(set(names))))


def sibling_owned(source_dir, owner: str) -> set:
    """Every script name another plugin sharing this folder claims."""
    pex_dir = os.path.dirname(str(source_dir))
    names = os.listdir(pex_dir) if os.path.isdir(pex_dir) else []
    others = [n[:-len(OWNED_SUFFIX)] for n in names
              if n.endswith(OWNED_SUFFIX) and n != owner + OWNED_SUFFIX]
    return set().union(*(read_owned(source_dir, o) for o in others))


def prune_removed_owners(source_dir, record_dir) -> None:
    """Remove deleted folder members' ownership and exclusively owned scripts.

    Only a reachable original folder can establish that a member was removed.
    Archive imports, offline folders and unregistered exports keep their lists.
    """
    from asset_convert.sources import source_registry
    from core.plugin_masters import export_root

    root = export_root(record_dir)
    wanted = os.path.normcase(os.path.abspath(record_dir))
    plugin = next((name for name, entry in source_registry.load(root)['sources'].items()
                   if entry.get('kind') == 'folder'
                   and os.path.normcase(os.path.abspath(
                       source_registry.record_dir(root, name))) == wanted), None)
    if plugin is None:
        return
    original = source_registry.get(root, plugin).get('archive_original')
    if not original or not Path(original).is_dir():
        return

    active = {owner_key(source_registry.record_dir(root, name)).lower()
              for name in source_registry.group_members(root, plugin)
              if source_registry.source_available(root, name)}
    src = Path(source_dir)
    lists = list(src.parent.glob('*' + OWNED_SUFFIX))
    removed = [p for p in lists if p.name[:-len(OWNED_SUFFIX)].lower() not in active]
    kept = set().union(*(read_owned(src, p.name[:-len(OWNED_SUFFIX)])
                         for p in lists if p not in removed))
    kept = {name.lower() for name in kept}
    stale = set().union(*(read_owned(src, p.name[:-len(OWNED_SUFFIX)])
                          for p in removed))
    for name in stale:
        if name.lower() in kept or os.path.basename(name) != name:
            continue
        (src / (name + '.psc')).unlink(missing_ok=True)
        (src.parent / (name + '.pex')).unlink(missing_ok=True)
    for path in removed:
        path.unlink()
        print(f'  Removed stale script ownership: {path.name}')
