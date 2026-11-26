"""
Importing a hybrid companion: its furniture and grass, and the placements and land textures they change.

The full import runs every pre-scan and writes side files (music, crime
factions, idle tables, runtime sidecars) for whatever plugin it is handed. A
companion shares its owner's output folder and may change nothing but its own
records, so it never enters that pipeline. This reads its export, converts its
FURN and GRAS, and writes its LTEX and REFR overrides through the override
builder, which adds only the cells and worldspace that hold them. Nothing else
can be written: no other record type is read, and no other phase runs.

See: docs/commentary/tes4_export_morrowind.md#hybrid-companion
"""

from asset_convert.game_paths import namespace_for, set_borrowed, set_namespace
from core.plugin_masters import masters_from_export_header
from output_layout import asset_cache_chain, assets_for

from .base.mesh_bounds import load_mesh_bounds
from .base.text_reader import group_records_by_type, parse_export_directory, set_formid_index_offset
from .base.writer import PluginWriter
from .overrides.manifest import write_manifest
from .overrides.nested import build_nested_overrides, open_masters
from .record_types.items import load_furniture_models
from .record_types.world import set_world_land_extents
from .registry import IMPORT_DISPATCH

#: The new record types a companion adds.
NEW_TYPES = ('FURN', 'GRAS')

#: The overridden types that live inside the cell tree.
WORLD_TYPES = ('CELL', 'WRLD', 'REFR')

#: Every record type a companion's export may hold; nothing else is read.
TYPES = NEW_TYPES + ('LTEX',) + WORLD_TYPES


def import_companion(export_dir: str, output_path: str, output_root: str) -> int:
    """Write the companion exported at `export_dir` to `output_path`; its error count."""
    set_namespace(namespace_for(export_dir))
    set_borrowed(export_dir)
    set_world_land_extents({})
    masters, _count, ctx = open_masters(export_dir, ['Skyrim.esm'], output_root)
    set_formid_index_offset(len(masters) - len(masters_from_export_header(export_dir)))
    by_type = group_records_by_type(parse_export_directory(export_dir, type_filter=set(TYPES)))
    ctx.land_cache = {}
    writer = PluginWriter(masters=masters, is_esm=False, description='Hybrid furniture and grass')
    writer.export_dir = export_dir
    load_mesh_bounds(asset_cache_chain(export_dir, 'mesh_bounds_cache.json'))
    load_furniture_models(str(assets_for(export_dir) / 'meshes'), by_type, ctx, record_dir=export_dir)
    for sig in NEW_TYPES:
        for rec in by_type.get(sig, ()):
            writer.add_record(sig, IMPORT_DISPATCH[sig](rec))
    errors = 0
    for rec in by_type.get('LTEX', ()):
        built = ctx.build(rec, 'LTEX')
        if built is None or not built.record_bytes:
            errors += 1
            continue
        writer.add_record('LTEX', built.record_bytes)
    errors += len(build_nested_overrides(by_type, WORLD_TYPES, ctx, writer, 'companion'))
    writer.write(output_path)
    write_manifest(output_path, output_path.replace('\\', '/').rsplit('/', 1)[-1], writer.manifest())
    set_formid_index_offset(0)
    return errors
