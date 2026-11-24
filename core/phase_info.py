"""The pipeline's phases as convert.py announces them, shared with the window that follows a run.

convert.py prints each phase's banner title between two rules before the phase starts; the GUI
reads those titles back to know which step is running. One table, so the two cannot drift.
"""

#: step -> (banner, version-record key, scope). 'plugin' skips asset-only mods; 'global' runs once.
PHASE_INFO = {
    'export': ("Phase 1: EXPORT TES4 RECORDS", 'export', 'plugin'),
    'extract': ("Phase 2: EXTRACT TES4 ARCHIVES", 'extract', 'all'),
    'meshes': ("Phase 3: CONVERT MESHES AND TEXTURES", 'meshes', 'all'),
    'speedtrees': ("Phase 4: CONVERT SPEEDTREES", 'speedtrees', 'all'),
    'creatures': ("Phase 5: CONVERT CREATURES", 'creatures', 'plugin'),
    'import': ("Phase 6: BUILD TES5 PLUGIN", 'import_', 'plugin'),
    'sounds': ("Phase 7: CONVERT SOUNDS", 'sounds', 'all'),
    'scripts': ("Phase 8: CONVERT SCRIPTS", 'scripts', 'plugin'),
    'lod': ("GENERATE LOD", 'create_lod', 'global'),
    'skyrim_patch': ("Phase 10: PATCH SKYRIM (BODY SLOTS)", 'modify_body_meshes',
                     'global'),
    'pack_bsa': ("Phase 11: PACK BSA ARCHIVES", 'pack', 'all'),
    'pack_zip': ("Phase 12: PACK ZIP ARCHIVES", 'pack_zip', 'all'),
}

#: banner title -> version-record key, the key the GUI's step table uses.
STEP_BY_BANNER = {title: key for title, key, _scope in PHASE_INFO.values()}
