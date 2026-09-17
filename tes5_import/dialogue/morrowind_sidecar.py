"""
Stage a plugin's TES3 dialogue where MorrowindRuntime.dll can read it.

The runtime is an SKSE plugin in the player's install, so it never sees this
repo's `export/`. The DIAL/INFO text is copied into the plugin's own output
under `SKSE/Plugins/MorrowindRuntime/<plugin>/`, which installs with every
other asset and keeps one plugin's dialogue separate from another's.

Copied rather than re-serialized: the exporter already writes the format the
runtime parses, so a second writer here would be a second thing to keep in
step with it.

See: docs/commentary/morrowind_runtime.md#sidecar
"""

import os
import shutil

#: Where the runtime looks, relative to a plugin's output root.
SIDECAR_DIR = os.path.join('SKSE', 'Plugins', 'MorrowindRuntime')

#: Export files the runtime reads; topics first, since a response needs one.
DIALOGUE_FILES = ('MWDI.txt', 'MWIN.txt')

#: The FormID -> TES3 id index the activation hook routes on.
ACTOR_INDEX = 'MWAC.txt'

#: The export this index is built from, and the two keys it needs.
_NPC_EXPORT = 'NPC_.txt'
_RECORD_MARK = '---RECORD_BEGIN---'


def plugin_stem(plugin_name: str) -> str:
    """`Morrowind.esm` -> `Morrowind`, the per-plugin sidecar folder name."""
    return os.path.splitext(os.path.basename(plugin_name))[0]


def _index_lines(handle) -> list:
    """`FormID=EditorID` per record, from an open NPC export."""
    lines = []
    form = ''
    for line in handle:
        line = line.rstrip('\n')
        if line == _RECORD_MARK:
            form = ''
        elif line.startswith('FormID='):
            form = line[7:]
        elif line.startswith('EditorID=') and form:
            lines.append(f'{form}={line[9:]}')
            form = ''
    return lines


def _actor_index(export_dir: str) -> str:
    """`FormID=EditorID` for every NPC: the TES3 id dialogue filters on.

    The runtime routes activation by FormID but filters by TES3 id, so without
    this index it can tell an actor is Morrowind's and still not know who they
    are. Built here because the FormID is minted during import.
    See: docs/commentary/morrowind_runtime.md#activation
    """
    path = os.path.join(export_dir, _NPC_EXPORT)
    if not os.path.isfile(path):
        return ''
    with open(path, encoding='utf-8', errors='replace') as handle:
        lines = _index_lines(handle)
    return '\n'.join(lines) + ('\n' if lines else '')


def write_morrowind_sidecar(export_dir: str, output_path: str,
                            plugin_name: str) -> int:
    """Copy this plugin's dialogue export into its SKSE sidecar folder.

    Returns the number of files staged; 0 when the plugin has no dialogue,
    which is every non-TES3 source and any TES3 plugin that defines none.
    """
    present = [name for name in DIALOGUE_FILES
               if os.path.isfile(os.path.join(export_dir, name))]
    if not present:
        return 0
    out_dir = os.path.join(os.path.dirname(output_path), SIDECAR_DIR,
                           plugin_stem(plugin_name))
    os.makedirs(out_dir, exist_ok=True)
    for name in present:
        shutil.copyfile(os.path.join(export_dir, name),
                        os.path.join(out_dir, name))
    index = _actor_index(export_dir)
    if not index:
        return len(present)
    with open(os.path.join(out_dir, ACTOR_INDEX), 'w',
              encoding='utf-8') as handle:
        handle.write(index)
    return len(present) + 1
