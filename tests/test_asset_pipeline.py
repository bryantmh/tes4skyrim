"""convert_meshes: a --mesh-subdirs run converts the named NIFs and nothing else."""
import pytest

from asset_convert import asset_pipeline
from pathlib import Path

POST_PASSES = ('_profile_hair_and_grass', '_split_magic_art',
               '_copy_and_fix_textures')


def _small_nif(path, name=b'Scene Root'):
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data(version=0x14000005, user_version=11, user_version_2=11)
    data.header.endian_type = 1
    root = NifFormat.NiNode()
    root.name = name
    data.roots = [root]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as fh:
        data.write(fh)


def test_mod_mesh_reuse_keeps_output_and_reconverts_changed_inputs(tmp_path, monkeypatch):
    from asset_convert.nif import nif_batch
    from asset_convert.character import wearable_plan as wp
    from asset_convert.nif.fixture_plan import FIXTURE_KEY
    monkeypatch.setattr(nif_batch, 'WORKER_COUNT', 1)
    src = tmp_path / 'export' / 'meshes' / 'tree.nif'
    out = tmp_path / 'output' / 'meshes' / 'tes4'
    _small_nif(src)
    def run(token='run1', plan=None, **options):
        return nif_batch.batch_convert(src.parent, out, reuse_token=token,
                                       wearable_plan=plan, **options)
    plan = {'tree.nif': wp.BASE, FIXTURE_KEY: {'tree.nif'}}
    first = run(plan=plan)
    assert first['converted'] == 1 and first['errors'] == 0
    original = (out / src.name).read_bytes()
    stamp = (out / src.name).stat().st_mtime_ns
    # Another plugin can add unrelated models without changing this NIF.
    same = run(plan={**plan, 'unrelated.nif': wp.W0,
                     FIXTURE_KEY: {'tree.nif', 'unrelated.nif'}})
    assert same['reused'] == 1 and same['converted'] == 0
    assert (out / src.name).read_bytes() == original
    assert (out / src.name).stat().st_mtime_ns == stamp
    # Relevant context changes and A -> B -> A must each reconvert.
    changed = {**plan, FIXTURE_KEY: set()}
    assert run(plan=changed)['converted'] == 1
    assert run(plan=plan)['converted'] == 1
    assert run(plan=plan, parallax=True)['converted'] == 1
    assert run(plan=plan)['converted'] == 1
    (out / src.name).unlink()
    assert run(plan=plan)['converted'] == 1
    _small_nif(src, b'Changed Root')
    assert run(plan=plan)['converted'] == 1
    assert (out / src.name).read_bytes() != original
    assert run(token='run2', plan=plan)['converted'] == 1


def test_mod_mesh_reuse_preserves_weight_variants_and_retries_failure(tmp_path, monkeypatch):
    from asset_convert.nif import nif_batch
    from asset_convert.character import wearable_plan as wp
    monkeypatch.setattr(nif_batch, 'WORKER_COUNT', 1)
    src = tmp_path / 'export' / 'meshes' / 'armor.nif'
    out = tmp_path / 'output' / 'meshes' / 'tes4'
    _small_nif(src)
    def run():
        return nif_batch.batch_convert(src.parent, out, reuse_token='run',
                                       wearable_plan={'armor.nif': wp.W0 | wp.W1})
    first = run()
    assert first['errors'] == 0 and first['converted'] == 1
    assert not (out / src.name).exists()
    assert run()['reused'] == 1
    (out / 'armor_1.nif').unlink()
    assert run()['converted'] == 1
    assert (out / 'armor_1.nif').exists()
    src.write_bytes(b'broken NIF')
    assert run()['errors'] == 1
    assert run()['errors'] == 1


def test_imported_mesh_reuse_between_invocations_tracks_texture_dependencies(tmp_path, monkeypatch):
    from asset_convert.nif import nif_batch
    monkeypatch.setattr(nif_batch, 'WORKER_COUNT', 1)
    src = tmp_path / 'export' / 'meshes' / 'tree.nif'
    out = tmp_path / 'output' / 'meshes'
    _small_nif(src)
    texture = src.parent.parent / 'textures' / 'source.dds'
    texture.parent.mkdir()
    texture.write_bytes(b'first')
    def run(token='imported-mod'):
        return nif_batch.batch_convert(src.parent, out, reuse_token=token)
    assert run()['converted'] == 1
    assert run()['reused'] == 1
    monkeypatch.setenv('TESCONV_RUN_LOG', 'another-run.log')
    assert run()['reused'] == 1
    texture.write_bytes(b'changed texture')
    assert run()['converted'] == 1
    assert run()['reused'] == 1
    assert run(None)['converted'] == 1
    (out / src.name).unlink()
    assert run()['converted'] == 1


def test_reused_textured_mesh_keeps_texture_and_overlay_manifests(tmp_path, monkeypatch):
    from asset_convert.nif import nif_batch
    monkeypatch.setattr(nif_batch, 'WORKER_COUNT', 1)
    src = tmp_path / 'src' / 'meshes' / 'a.nif'
    _small_nif(src)
    real_convert = nif_batch.convert_nif
    def convert(*args, **kwargs):
        result = real_convert(*args, **kwargs)
        # Exercise transport of all worker metadata independently of shader
        # heuristics: dropping these sets would break pruning and alpha repair.
        generated = out.parent.parent / 'textures' / 'tes4' / 'generated_p.dds'
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b'height map')
        result.update(textures={'tes4/transparent.dds', 'tes4/generated_p.dds'},
                      alpha_opacity_diffuse={'tes4/transparent.dds'},
                      overlay_diffuses={'tes4/overlay.dds'})
        return result
    monkeypatch.setattr(nif_batch, 'convert_nif', convert)
    out = tmp_path / 'out' / 'meshes' / 'tes4'
    def run():
        return nif_batch.batch_convert(src.parent, out, reuse_token='run')
    first = run()
    reused = run()
    assert first['errors'] == 0 and first['textures_used']
    assert first['overlay_diffuses']
    assert reused['reused'] == 1 and reused['errors'] == 0
    for key in ('textures_used', 'alpha_opacity_diffuse', 'overlay_diffuses'):
        assert reused[key] == first[key]
    generated = out.parent.parent / 'textures' / 'tes4' / 'generated_p.dds'
    generated.unlink()
    assert run()['converted'] == 1
    assert generated.read_bytes() == b'height map'


@pytest.fixture
def calls(tmp_path, monkeypatch):
    """Stub every step of convert_meshes; return the list of steps that ran."""
    ran = []
    (tmp_path / 'export' / 'Test.esm' / 'meshes').mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(asset_pipeline, '_activate_namespace', lambda _d: 'tes4')
    monkeypatch.setattr(asset_pipeline, 'assemble_armor', lambda *_a: 0)
    monkeypatch.setattr(asset_pipeline, '_persist_mesh_manifests',
                        lambda *_a: None)
    monkeypatch.setattr(asset_pipeline, '_convert_mesh_tree',
                        lambda *_a: ran.append('batch') or {})
    for name in POST_PASSES:
        monkeypatch.setattr(asset_pipeline, name,
                            lambda *_a, _n=name: ran.append(_n))
    monkeypatch.setattr(asset_pipeline.landscape_normals, 'ensure_ltex_normals',
                        lambda *_a: ran.append('ltex_normals') or (0, 0))
    return ran


def test_filtered_run_converts_only_the_meshes(calls):
    """A --mesh-subdirs run stops after the NIF batch: no whole-tree pass runs."""
    asset_pipeline.convert_meshes('Test.esm', mesh_subdirs=['dungeons/a.nif'])
    assert calls == ['batch']


def test_unfiltered_run_runs_every_pass(calls):
    """A full run still runs the hair/grass, magic art, texture and LTEX passes."""
    asset_pipeline.convert_meshes('Test.esm')
    assert calls == ['batch', *POST_PASSES, 'ltex_normals']


def test_deferred_mesh_run_does_not_copy_or_fix_shared_textures(calls):
    asset_pipeline.convert_meshes('Test.esm', defer_textures=True)
    assert calls == ['batch', '_profile_hair_and_grass', '_split_magic_art']


def test_shared_textures_copy_once_and_preserve_all_plugins_opacity(tmp_path, monkeypatch):
    import json
    from asset_convert.texture import texture_prune, parallax

    exp, out = tmp_path / 'export', tmp_path / 'output'
    exp.mkdir()
    (exp / 'sources.json').write_text(json.dumps({'version': 1, 'sources': {
        name: {'kind': 'archive', 'plugin': name, 'group_id': 'g1',
               'group_label': 'My Pack', 'group_plugins': ['A.esm', 'B.esp']}
        for name in ['A.esm', 'B.esp']}}), encoding='utf-8')
    assets = exp / 'My Pack'
    source = assets / 'textures'
    source.mkdir(parents=True)
    (source / 'diffuse.dds').write_bytes(b'original texture')
    for name, refs in [('A.esm', {'tes4/transparent.dds'}),
                       ('B.esp', {'tes4/overlay.dds'})]:
        texture_prune.write_manifest(assets / name, refs,
                                      texture_prune.OPACITY_MANIFEST_NAME)
    monkeypatch.setattr(asset_pipeline, '_activate_namespace', lambda _: 'tes4')
    copied, repaired, opacity = [], [], []
    original_copy = asset_pipeline._copy_tree
    def copy(src, dst):
        copied.append(str(src))
        return original_copy(src, dst)
    monkeypatch.setattr(asset_pipeline, '_copy_tree', copy)
    monkeypatch.setattr(asset_pipeline.image_transcode, 'run', lambda _: (0, 0, 0))
    monkeypatch.setattr(asset_pipeline.luminance_textures, 'run',
                        lambda _: repaired.append('luminance') or (0, 0))
    monkeypatch.setattr(asset_pipeline.landscape_normals, 'run', lambda _: (0, 0))
    monkeypatch.setattr(asset_pipeline.landscape_normals, 'normalize_specular_alpha',
                        lambda *_a, **_kw: (0, 0, {}))
    monkeypatch.setattr(asset_pipeline, 'owns_namespace', lambda _: False)
    monkeypatch.setattr(asset_pipeline.landscape_normals, 'ensure_ltex_normals',
                        lambda *_: (0, 0))
    monkeypatch.setattr(parallax, 'strip_diffuse_alpha',
                        lambda _root, keep: opacity.append(set(keep)) or (0, 0, 0, 0))
    stats = asset_pipeline.convert_shared_textures(
        'A.esm', ['A.esm', 'B.esp'], extract_dir=exp, output_dir=out)
    assert stats['textures_copied'] == 1 and len(copied) == 1
    assert repaired == ['luminance']
    assert opacity == [{'tes4/transparent.dds', 'tes4/overlay.dds'}]
    assert (out / 'My Pack' / 'textures' / 'tes4' / 'diffuse.dds').read_bytes() == b'original texture'
    def run():
        return asset_pipeline.convert_shared_textures(
            'A.esm', ['A.esm', 'B.esp'], extract_dir=exp, output_dir=out, reuse=True)
    assert run()['textures_copied'] == 1
    assert run()['textures_reused'] is True
    assert len(copied) == 2
    target = out / 'My Pack' / 'textures' / 'tes4' / 'diffuse.dds'
    target.unlink()
    assert run()['textures_copied'] == 1
    assert target.read_bytes() == b'original texture'
    (assets / 'textures' / 'diffuse.dds').write_bytes(b'new texture')
    assert run()['textures_copied'] == 1
    assert target.read_bytes() == b'new texture'
    texture_prune.write_manifest(assets / 'B.esp', {'tes4/new_opacity.dds'},
                                  texture_prune.OPACITY_MANIFEST_NAME)
    assert run()['textures_copied'] == 1
    assert 'tes4/new_opacity.dds' in opacity[-1]


def _dump(folder, sig, *records):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{sig}.txt').write_text(''.join(
        '---RECORD_BEGIN---\n' + '\n'.join(f'{k}={v}' for k, v in rec.items())
        + '\n---RECORD_END---\n' for rec in records), encoding='utf-8')


def test_plugin_mesh_scope_converts_own_models_and_placed_master_only(tmp_path, monkeypatch):
    """An unrelated master's same local FormID and unused meshes stay untouched."""
    from asset_convert.nif import nif_batch

    exp = tmp_path / 'export'
    a, b, patch = [exp / name for name in ('A.esm', 'B.esm', 'Patch.esp')]
    _dump(a, 'STAT', {'FormID': '00000007', 'Model.MODL': 'unused.nif'})
    _dump(b, 'STAT', {'FormID': '01000007', 'Model.MODL': 'placed.nif'})
    (b / '_HEADER.txt').write_text('Master[0]=A.esm\n', encoding='utf-8')
    _dump(patch, 'ARMO', {'FormID': '02000001',
                          'Male.BipedModel.MODL': 'Meshes\\\\Armor\\\\Worn.nif',
                          'Female.WorldModel.MODL': 'armor/dropped.nif'})
    _dump(patch, 'REFR', {'FormID': '02000002', 'NAME': '00000007'})
    (patch / '_HEADER.txt').write_text('Master[0]=B.esm\nMaster[1]=A.esm\n',
                                       encoding='utf-8')
    meshes, out = patch / 'meshes', tmp_path / 'output'
    for rel in ['unused.nif', 'placed.nif', 'armor/worn.nif', 'armor/dropped.nif']:
        path = meshes / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'source')
    fragments = patch / 'mesh_scan_fragments'
    fragments.mkdir()
    (fragments / 'previous.jsonl').write_bytes(b'keep other meshes')

    def convert_selected(work, stats, *_args, **_kwargs):
        for args in work:
            target = Path(args[1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'converted')
            stats['converted'] += 1

    monkeypatch.setattr(nif_batch, '_run_batch', convert_selected)
    result = asset_pipeline._convert_mesh_tree(
        meshes, out, patch, exp, 'Patch.esp', None, False, False, True)
    assert result['converted'] == 3
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob('*.nif')) == [
        'armor/dropped.nif', 'armor/worn.nif', 'placed.nif']
    assert (fragments / 'previous.jsonl').read_bytes() == b'keep other meshes'


def test_empty_plugin_mesh_scope_does_not_rebuild_shared_assets(tmp_path):
    from asset_convert.sources.plugin_assets import model_paths
    from asset_convert.nif.nif_batch import _collect_nifs

    meshes = tmp_path / 'meshes'
    meshes.mkdir()
    (meshes / 'unrelated.nif').write_bytes(b'source')
    assert _collect_nifs(meshes, None, model_filter=model_paths(tmp_path))[0] == []
    assert _collect_nifs(meshes, None)[0] == [meshes / 'unrelated.nif']


def test_plugin_creature_scope_keeps_only_own_and_placed_creatures(tmp_path):
    from asset_convert.havok.creature_pipeline import _creature_folders, _pick_creature_dirs

    exp = tmp_path / 'export'
    master, patch = exp / 'A.esm', exp / 'Patch.esp'
    _dump(master, 'CREA', {'FormID': '00000001',
                           'Model.MODL': 'creatures/wolf/skeleton.nif'},
                          {'FormID': '00000002',
                           'Model.MODL': 'creatures/unused/skeleton.nif'})
    _dump(patch, 'CREA', {'FormID': '01000003',
                          'Model.MODL': 'Meshes\\\\creatures\\\\rat\\\\skeleton.nif'})
    _dump(patch, 'ACRE', {'FormID': '01000004', 'NAME': '00000001'})
    (patch / '_HEADER.txt').write_text('Master[0]=A.esm\n', encoding='utf-8')
    meshes = patch / 'meshes'
    for rel in ('creatures/rat', 'creatures/wolf', 'creatures/unused', 'characters/rat'):
        folder = meshes / rel
        folder.mkdir(parents=True)
        (folder / 'skeleton.nif').write_bytes(b'skeleton')
        (folder / 'idle.kf').write_bytes(b'animation')
    selected = _pick_creature_dirs(_creature_folders(
        str(patch), str(meshes), None, lambda *_: None, True), lambda *_: None)
    assert [Path(path).relative_to(meshes).as_posix() for path, name in selected] == [
        'creatures/rat', 'creatures/wolf']
    narrowed = _creature_folders(str(patch), str(meshes), ['wolf'],
                                  lambda *_: None, True)
    assert [name for path, name, referenced in narrowed] == ['wolf']
    (patch / 'CREA.txt').unlink()
    (patch / 'ACRE.txt').unlink()
    assert _creature_folders(str(patch), str(meshes), None,
                              lambda *_: None, True) == []


def test_plugin_creature_run_preserves_other_registered_projects(tmp_path, monkeypatch):
    import json
    from asset_convert.havok import creature_pipeline as cp, animation_data

    export = tmp_path / 'export' / 'Patch.esp'
    _dump(export, 'CREA', {'FormID': '00000001',
                           'Model.MODL': 'creatures/rat/skeleton.nif'})
    folder = export / 'meshes' / 'creatures' / 'rat'
    folder.mkdir(parents=True)
    (folder / 'skeleton.nif').write_bytes(b'skeleton')
    (folder / 'idle.kf').write_bytes(b'animation')
    out = tmp_path / 'output' / 'Patch.esp' / 'meshes'
    out.mkdir(parents=True)
    def manifest(name):
        return dict(name=name, namespace='patch', project_hkx=f'{name}/project.hkx',
                    behavior_hkx=f'{name}/behavior.hkx', body_dir=name,
                    skeleton_nif=f'{name}/skeleton.nif')
    converted, registered = [], []
    def convert(dirs, *_args):
        converted.extend(name for folder, name in dirs)
        return {name: manifest(name) for folder, name in dirs}, {}
    def register(projects, out_meshes, source, appends, plugin_out):
        registered.extend(m['name'] for m in projects)
        assert appends == {'animdata_appends': ['existing gun']}
        return str(out / 'fragment.json')
    monkeypatch.setattr(cp, 'split_creatures', lambda *_: 0)
    monkeypatch.setattr(cp, '_convert_pool', convert)
    monkeypatch.setattr(cp, 'manifests_under', lambda *_: {'wolf': manifest('wolf')})
    monkeypatch.setattr(cp, '_kept_appends',
                        lambda *_: {'animdata_appends': ['existing gun']})
    monkeypatch.setattr(cp, 'convert_guns', lambda *_: {})
    monkeypatch.setattr(animation_data, 'write_fragment', register)
    result = cp.convert_creatures(str(export), str(out), log=lambda *_: None,
                                   plugin_assets_only=True)
    assert converted == ['rat'] and result['errors'] == {}
    assert set(registered) == {'rat', 'wolf'}
    artifact = json.loads((export / 'creature_projects.json').read_text(encoding='utf-8'))
    assert set(artifact['data']) == {'rat', 'wolf'}


def test_mesh_scope_finds_a_master_in_a_single_plugin_imported_mod(tmp_path):
    import json
    from asset_convert.sources.plugin_assets import model_paths
    exp = tmp_path / 'export'
    exp.mkdir()
    (exp / 'sources.json').write_text(json.dumps({'version': 1, 'sources': {
        name: {'kind': 'archive', 'plugin': name, 'group_id': name,
               'group_label': label, 'group_plugins': [name]}
        for name, label in [('A.esm', 'Master Pack'), ('B.esp', 'Patch Pack')]}}))
    base, patch = exp / 'Master Pack', exp / 'Patch Pack'
    _dump(base, 'STAT', {'FormID': '00000001', 'MODL': 'tree.nif'})
    _dump(patch, 'REFR', {'FormID': '01000002', 'NAME': '00000001'})
    (patch / '_HEADER.txt').write_text('Master[0]=A.esm\n')
    assert model_paths(patch) == {'tree.nif'}
