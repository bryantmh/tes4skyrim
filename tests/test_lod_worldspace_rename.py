"""LOD names a source worldspace the way the importer converts it.

Both the shipped-LOD worldspace list and the source-LOD extents map an
export's WRLD EditorID to the CONVERTED EditorID the bake looks up. They
hard-coded Tamriel -> TES4Tamriel only, so a plugin-scoped rename (Arktwend's
WrldMorrowind -> WrldArktwend), or one reached through a master, was missed.
Both now use `core.worldspace_names` with the plugin + master chain.

See: docs/commentary/script_convert.md#worldspace-property-rename
"""

from asset_convert.lod import lod_gen, terrain_lod


def _export(root, plugin, wrlds, masters=(), tiles=()):
    """A minimal export record dir: WRLD.txt, _HEADER.txt, shipped LOD tiles."""
    d = root / plugin
    lod = d / 'meshes' / 'landscape' / 'lod'
    lod.mkdir(parents=True)
    (d / 'WRLD.txt').write_text(''.join(
        f'---RECORD_BEGIN---\nFormID={fid:08X}\nEditorID={edid}\n---RECORD_END---\n'
        for fid, edid in wrlds))
    (d / '_HEADER.txt').write_text(''.join(
        f'Master[{i}]={m}\n' for i, m in enumerate(masters)))
    for name in tiles:
        (lod / name).write_bytes(b'')
    return d


def test_the_plugin_scoped_arktwend_rename_is_applied(tmp_path):
    """WrldMorrowind in an Arktwend plugin converts to WrldArktwend."""
    d = _export(tmp_path, 'Arktwend_English.esm', [(0x3C, 'WrldMorrowind')],
                tiles=['60.0.0.32.nif'])

    assert terrain_lod.shipped_lod_worldspaces(d) == [('WrldArktwend', 0x3C)]


def test_a_dependent_inherits_its_masters_rename(tmp_path):
    """A patch mastered on Arktwend names the world as the master converts it."""
    _export(tmp_path, 'Arktwend_English.esm', [(0x3C, 'WrldMorrowind')])
    d = _export(tmp_path, 'ArkPatch.esp', [], masters=['Arktwend_English.esm'],
                tiles=['60.-32.0.32.nif'])

    assert terrain_lod.shipped_lod_worldspaces(d) == [('WrldArktwend', 0x3C)]


def test_the_same_edid_elsewhere_is_not_renamed(tmp_path):
    """WrldMorrowind outside an Arktwend chain keeps its name; Tamriel still renames."""
    d = _export(tmp_path, 'Other.esm', [(0x3C, 'WrldMorrowind'), (0x3D, 'Tamriel')],
                tiles=['60.0.0.32.nif', '61.0.0.32.nif', '61.32.0.32.nif'])

    assert sorted(terrain_lod.shipped_lod_worldspaces(d)) == [
        ('TES4Tamriel', 0x3D), ('WrldMorrowind', 0x3C)]


def test_source_extents_find_the_renamed_worldspace(tmp_path):
    """The extents lookup matches the converted name, not only TES4Tamriel."""
    d = _export(tmp_path, 'Arktwend_English.esm', [(0x3C, 'WrldMorrowind')],
                tiles=['60.-32.0.32.nif', '60.0.32.32.nif'])

    assert lod_gen.source_lod_extents([d], 'WrldArktwend') == (-32, 0, 32, 64)
    assert lod_gen.source_lod_extents([d], 'WrldMorrowind') is None
