"""Morrowind creature split: bipedal layering, multi-action groups, source folders.

See: docs/commentary/tes4_export_morrowind.md#bipedal-creatures
"""

import os

from asset_convert.havok import creature_split_morrowind as split
from asset_convert.havok.creature_pipeline import _creature_folders


def _write_crea(export_dir, records):
    """Write CREA.txt holding `records` (dicts of KEY=VALUE lines)."""
    lines = []
    for rec in records:
        lines.append('---RECORD_BEGIN---')
        lines.extend(f'{k}={v}' for k, v in rec.items())
        lines.append('---RECORD_END---')
    (export_dir / 'CREA.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


SKELETON = {'Signature': 'CREA', 'EditorID': 'skeleton', 'ACBS.Flags': '5',
            'Model.MODL': 'r\\\\skeleton\\\\skeleton.nif', 'NIFZCount': '1',
            'NIFZ[0]': 'skeleton_body.nif', 'MorrowindModel': 'r\\\\Skeleton.NIF'}


def test_weapon_group_is_cut_into_equip_and_attack_clips():
    """A weapon group yields stance-prefixed equip, unequip and attack clips, the attack hitting at `<a> hit`."""
    events = {'equip start': 1.0, 'equip stop': 1.5, 'chop start': 2.0,
              'chop hit': 2.8, 'chop large follow stop': 3.2,
              'unequip start': 4.0, 'unequip stop': 4.4}
    plan = split._plan({'weapononehand': events}, known_only=True)
    assert sorted(plan) == ['onehandattackchop', 'onehandequip', 'onehandunequip']
    span, chop = plan['onehandattackchop']
    assert span == (2.0, 3.2, False)
    assert chop['hit'] == 2.8


def test_spellcast_segments_hit_at_release():
    """Each spellcast delivery becomes a cast clip hitting at its release."""
    events = {'target start': 5.0, 'target release': 5.9, 'target stop': 7.0,
              'self start': 1.0, 'self release': 1.5, 'self stop': 2.0}
    plan = split._plan({'spellcast': events}, known_only=False)
    assert plan['casttarget'][1]['hit'] == 5.9
    assert plan['castself'][0] == (1.0, 2.0, False)


def test_known_only_drops_groups_no_claim_table_reads():
    """The base layer keeps only mapped groups; a creature's own file keeps all."""
    plain = {'start': 0.0, 'stop': 1.0}
    plan = split._plan({'idle': plain, 'sneakforward': plain, 'idle1h': plain},
                       known_only=True)
    assert sorted(plan) == ['idle', 'onehandidle']
    assert 'sneakforward' in split._plan({'sneakforward': plain}, known_only=False)


def test_sources_carry_body_name_and_bipedal_flag(tmp_path):
    """A source is bipedal when any record using it is, and keeps its NIFZ body name."""
    _write_crea(tmp_path, [SKELETON, dict(SKELETON, **{'ACBS.Flags': '4'})])
    assert split._sources(tmp_path) == {
        'r\\Skeleton.NIF': ('r/skeleton', 'skeleton_body.nif', True)}
    assert split.source_dirs(tmp_path) == {'r'}


def test_source_folder_is_not_a_creature(tmp_path):
    """A folder holding a source model named skeleton.nif is not claimed as a creature."""
    _write_crea(tmp_path, [SKELETON])
    meshes = tmp_path / 'meshes'
    for folder, files in (('r', ('skeleton.nif', 'xskeleton.kf')),
                          (os.path.join('r', 'skeleton'), ('skeleton.nif', 'idle.kf'))):
        (meshes / folder).mkdir(parents=True, exist_ok=True)
        for name in files:
            (meshes / folder / name).write_bytes(b'')
    found = _creature_folders(str(tmp_path), str(meshes), None, lambda _m: None)
    assert [name for _dir, name, _ref in found] == ['skeleton']
