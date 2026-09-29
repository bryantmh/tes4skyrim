"""A plugin's bases are listed NEAREST first, so a later master wins.

`_HEADER.txt` numbers masters in load order, root first, and a later master
overrides an earlier one. Nearly every consumer takes the first root holding a
file, so a root-first list let the base game's copy of an asset beat an
unofficial patch's -- silently, since both copies exist.
See: docs/commentary/tes5_import_mod_merge.md#base-order-nearest-first
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.sources import base_plugins
from asset_convert.nif import shaders


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------

def _header(d, masters):
    """Export dir `d` whose `_HEADER.txt` lists `masters` in load order."""
    d.mkdir(parents=True, exist_ok=True)
    (d / '_HEADER.txt').write_text(
        ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)),
        encoding='utf-8')
    return d


def _recorded(d, bases):
    """Record `bases` in `d`'s `_source/.base_plugins`, as `--base` does."""
    s = d / '_source'
    s.mkdir(parents=True, exist_ok=True)
    (s / base_plugins.FILE_NAME).write_text('\n'.join(bases) + '\n',
                                            encoding='utf-8')


def _texture(tree, rel, data):
    """Write `data` to `tree/textures/rel`; the path."""
    p = tree / 'textures' / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------------------
#  The source: names_for / export_dirs / subdirs
# ---------------------------------------------------------------------------

def test_names_for_puts_the_last_header_master_first(tmp_path):
    mod = _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp', 'Addon.esp'])
    assert base_plugins.names_for(mod) == ['Addon.esp', 'Patch.esp', 'Base.esm']


def test_recorded_bases_are_load_order_and_reversed_too(tmp_path):
    """`--base A B` is load order (later wins), read after the header."""
    mod = tmp_path / 'Pack'
    mod.mkdir()
    _recorded(mod, ['Base.esm', 'Retex.esp'])
    assert base_plugins.names_for(mod) == ['Retex.esp', 'Base.esm']
    _header(mod, ['Base.esm'])
    assert base_plugins.names_for(mod) == ['Retex.esp', 'Base.esm']


def test_export_dirs_are_nearest_first(tmp_path):
    """The existing base export trees come back last master first."""
    for n in ('Base.esm', 'Patch.esp'):
        (tmp_path / n).mkdir()
    mod = _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp'])
    got = [Path(d).name for d in base_plugins.export_dirs(mod)]
    assert got == ['Patch.esp', 'Base.esm']


# ---------------------------------------------------------------------------
#  First-match consumers
# ---------------------------------------------------------------------------

def test_a_later_masters_texture_wins_the_fallback(tmp_path):
    """Base and Patch both ship rock/stone.dds; the mod gets Patch's."""
    base = tmp_path / 'Base.esm'
    patch = tmp_path / 'Patch.esp'
    _texture(base, 'rock/stone.dds', b'DDS base')
    want = _texture(patch, 'rock/stone.dds', b'DDS patch')
    mod = _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp'])
    (mod / 'meshes').mkdir()
    roots = shaders.master_texture_roots(mod / 'meshes')
    assert [Path(r).parent.name for r in roots] == ['Patch.esp', 'Base.esm']
    got = shaders.resolve_source_texture(
        'textures\\tes4\\rock\\stone.dds', str(mod / 'meshes' / 'a.nif'), roots)
    assert got == str(want)


def test_a_texture_only_the_root_ships_still_resolves(tmp_path):
    """Control: a key only the root master has is still found."""
    base = tmp_path / 'Base.esm'
    want = _texture(base, 'rock/stone.dds', b'DDS base')
    (tmp_path / 'Patch.esp' / 'textures').mkdir(parents=True)
    mod = _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp'])
    (mod / 'meshes').mkdir()
    got = shaders.resolve_source_texture(
        'textures\\tes4\\rock\\stone.dds', str(mod / 'meshes' / 'a.nif'),
        shaders.master_texture_roots(mod / 'meshes'))
    assert got == str(want)


def test_master_export_dirs_are_nearest_first(tmp_path):
    """The importer's master dirs follow the same nearest-first order."""
    from tes5_import.pipeline import master_export_dirs
    for n in ('Base.esm', 'Patch.esp'):
        (tmp_path / n).mkdir()
    rec = _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp'])

    class _Ctx:
        export_dir = str(rec)

    assert [Path(d).name for d in master_export_dirs(_Ctx())] == \
        ['Patch.esp', 'Base.esm']


def _projects(d, name, projects):
    """Write `projects` as `d/creature_projects.json` for plugin `name`."""
    from tes5_import.base.artifact_schema import write_artifact
    d.mkdir(parents=True, exist_ok=True)
    write_artifact(str(d / 'creature_projects.json'), name, projects)


def _proj(tag, bodies=True):
    """One creature project; `bodies=False` gives a bodyless one."""
    return {'project_hkx': f'{tag}\\p.hkx', 'behavior_hkx': f'{tag}\\b.hkx',
            'body_dir': tag, 'skeleton_nif': f'{tag}\\skeleton.nif',
            'bodies': [f'{tag}.nif'] if bodies else []}


def test_a_later_masters_creature_project_wins(tmp_path):
    """Both masters build `rat`; the later one's project is inherited."""
    from tes5_import.actors.creature_projects import load_projects
    _projects(tmp_path / 'Base.esm', 'Base.esm', {'rat': _proj('base')})
    _projects(tmp_path / 'Patch.esp', 'Patch.esp', {'rat': _proj('patch')})
    rec = _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp'])
    got, owner_slot = load_projects(str(rec))
    assert got['rat']['body_dir'] == 'patch'
    assert owner_slot == {'rat': 1}


def test_a_bodyless_later_project_never_shadows_a_usable_one(tmp_path):
    """The body rule still holds between masters."""
    from tes5_import.actors.creature_projects import load_projects
    _projects(tmp_path / 'Base.esm', 'Base.esm', {'rat': _proj('base')})
    _projects(tmp_path / 'Patch.esp', 'Patch.esp',
              {'rat': _proj('patch', bodies=False)})
    rec = _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp'])
    got, owner_slot = load_projects(str(rec))
    assert got['rat']['body_dir'] == 'base'
    assert owner_slot == {'rat': 0}


def test_papyrus_header_chain_is_nearest_first(tmp_path):
    """The `-h` master dirs put the patch before the root."""
    from papyrus_compile import _master_chain
    for n in ('Base.esm', 'Patch.esp'):
        (tmp_path / n).mkdir()
    _header(tmp_path / 'Patch.esp', ['Base.esm'])
    _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp'])
    assert _master_chain('Mod.esp', str(tmp_path)) == ['Patch.esp', 'Base.esm']


def test_a_later_masters_worldspace_edid_wins(tmp_path):
    """A worldspace both masters define takes the later one's EditorID."""
    from asset_convert.lod.terrain_lod import worldspace_edids
    for n, edid in (('Base.esm', 'Tamriel'), ('Patch.esp', 'TamrielFixed')):
        d = tmp_path / n
        d.mkdir()
        (d / 'WRLD.txt').write_text(f'FormID=0000003C\nEditorID={edid}\n',
                                    encoding='utf-8')
    rec = _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp'])
    assert worldspace_edids(rec)[0x3C] == 'TamrielFixed'


# ---------------------------------------------------------------------------
#  Last-writer-wins consumers reverse the list, so the later master still wins
# ---------------------------------------------------------------------------

def _armo(d, model, flags):
    """Export dir `d` with one ARMO wearing `model` under biped `flags`."""
    lines = ['---RECORD_BEGIN---', 'Signature=ARMO', 'FormID=0004938C',
             f'BMDT.BipedFlags={flags}', f'Male.BipedModel.MODL={model}',
             '---RECORD_END---']
    d.mkdir(parents=True, exist_ok=True)
    (d / 'ARMO.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_a_later_masters_wearable_plan_entry_wins(tmp_path):
    """`_inherit` is last-writer-wins, so the bases are applied farthest first."""
    from asset_convert.character import wearable_plan as wp
    _armo(tmp_path / 'Base.esm', 'armor\\iron\\cuirass.nif', 0)
    _armo(tmp_path / 'Patch.esp', 'armor\\iron\\cuirass.nif', 4)
    mod = _header(tmp_path / 'Mod.esp', ['Base.esm', 'Patch.esp'])
    patch = wp.build_plan(tmp_path / 'Patch.esp')
    base = wp.build_plan(tmp_path / 'Base.esm')
    key = next(k for k in patch if not k.startswith('*'))
    assert patch[key] != base[key], 'fixture must disagree'
    assert wp.build_plan(mod)[key] == patch[key]


def test_the_spt_borrow_searches_the_later_master_first(tmp_path, monkeypatch):
    """The `.spt` borrow's master tree dirs are nearest first."""
    from asset_convert import asset_pipeline
    seen = {}

    def _fake(src, dst, export_dir=None, master_tree_dirs=None, use_engine=True):
        """Record the dirs instead of converting."""
        seen['dirs'] = [Path(d).parent.name for d in master_tree_dirs]
        return {'ok': 0, 'fail': 0, 'skip': 0}

    monkeypatch.setattr(asset_pipeline.spt_converter, 'convert_spt_directory',
                        _fake)
    exp = tmp_path / 'export'
    for n in ('Base.esm', 'Patch.esp'):
        (exp / n / 'trees').mkdir(parents=True)
    mod = _header(exp / 'Mod.esp', ['Base.esm', 'Patch.esp'])
    (mod / 'trees').mkdir()
    asset_pipeline.convert_speedtrees('Mod.esp', extract_dir=str(exp),
                                      output_dir=str(tmp_path / 'out'))
    assert seen['dirs'] == ['Patch.esp', 'Base.esm']


def test_race_voices_let_the_later_master_win(tmp_path, monkeypatch):
    """`set_voice_type` is last-writer-wins, so the nearest is applied last."""
    from tes5_import.base import adopted_records as ar

    class _Races:
        def __init__(self, key):
            self.by_race_edid = {'Imperial': key}

    keys = {'Base.esm': 'base', 'Patch.esp': 'patch'}
    monkeypatch.setattr(ar, 'load_race_voices',
                        lambda folder: _Races(keys[Path(folder).name]))
    monkeypatch.setattr(ar, 'vtyp_edid', lambda key, gender: key)
    got = {}
    monkeypatch.setattr(ar, 'set_voice_type',
                        lambda race, gender, fid: got.__setitem__(race, fid))

    class _Index:
        def find_by_edid(self, sig, edid):
            return {'base': 1, 'patch': 2}[edid]

    nearest_first = [str(tmp_path / 'Patch.esp'), str(tmp_path / 'Base.esm')]
    ar._adopt_race_voices(_Index(), nearest_first)
    assert got == {'Imperial': 2}
