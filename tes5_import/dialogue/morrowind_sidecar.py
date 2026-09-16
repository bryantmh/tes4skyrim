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

#: Export files the runtime reads; DIAL first, since an INFO needs its topic.
DIALOGUE_FILES = ('DIAL.txt', 'INFO.txt')


def plugin_stem(plugin_name: str) -> str:
    """`Morrowind.esm` -> `Morrowind`, the per-plugin sidecar folder name."""
    return os.path.splitext(os.path.basename(plugin_name))[0]


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
    return len(present)
