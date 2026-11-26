"""Which plugin wrote which script in a `scripts/` folder several plugins share.

An imported mod's plugins convert into ONE output folder, so their scripts sit
side by side. Each plugin's run records the names it wrote in
`scripts/<owner>.owned.txt`; a rebuild clears only its own, and the compiler
compiles only its own.

See: docs/commentary/script_convert.md#wipe-output-dir
"""

import os

from asset_convert.sources import output_files, source_registry
from core.plugin_masters import export_root

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
        text = output_files.read_bytes(owned_list_path(source_dir, owner)).decode('utf-8')
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}


def write_owned(source_dir, owner: str, names) -> None:
    """Record the script names `owner` wrote this run."""
    with open(owned_list_path(source_dir, owner), 'w', encoding='utf-8') as fh:
        fh.write(''.join(f'{n}\n' for n in sorted(set(names))))


def listed_owners(source_dir) -> list:
    """Every owner with a list beside the `scripts/source/` tree `source_dir`."""
    pex_dir = os.path.dirname(str(source_dir))
    loose = [n for n in (os.listdir(pex_dir) if os.path.isdir(pex_dir) else []) if n.endswith(OWNED_SUFFIX)]
    archived = [n for n in output_files.files_under(pex_dir, OWNED_SUFFIX) if '\\' not in n]
    names = {n.casefold(): n for n in archived} | {n.casefold(): n for n in loose}
    return sorted(n[:-len(OWNED_SUFFIX)] for n in names.values())


def sibling_owned(source_dir, owner: str) -> set:
    """Every script name another plugin sharing this folder claims."""
    others = [o for o in listed_owners(source_dir) if o.casefold() != owner.casefold()]
    return set().union(*(read_owned(source_dir, o) for o in others))


def departed_owners(source_dir, export_dir) -> list:
    """Owners listed here that are no longer live members of `export_dir`'s mod.

    A member is gone once unregistered or `removed_from_folder`. Only a
    registered mod is judged; any other folder keeps every list.
    See: docs/commentary/script_convert.md#departed-owners
    """
    root = export_root(export_dir)
    plugin = owner_key(export_dir)
    if not source_registry.get(root, plugin):
        return []
    live = {owner_key(source_registry.record_dir(root, m)).casefold()
            for m in source_registry.group_members(root, plugin)
            if not source_registry.removed_from_folder(root, m)}
    live.add(plugin.casefold())
    return [o for o in listed_owners(source_dir) if o.casefold() not in live]


def drop_owners(source_dir, owners) -> set:
    """Delete `owners`' lists; returns the script names they claimed."""
    names = set().union(*(read_owned(source_dir, o) for o in owners))
    for owner in owners:
        output_files.discard(owned_list_path(source_dir, owner))
        print(f'  Dropped the script list of departed member {owner}')
    return names
