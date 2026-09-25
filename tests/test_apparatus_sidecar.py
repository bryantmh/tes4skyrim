"""The apparatus sidecar TESRuntime's alchemy hooks read.

See: docs/commentary/tes_runtime_alchemy.md#alchemy-apparatus
"""

import json
import os

import pytest

from tes5_import.dialogue.morrowind_sidecar import write_morrowind_sidecar
from tes5_import.record_types.apparatus import (tes3_quality,
                                                write_apparatus_sidecar)


def _export(tmp_path, records, tes3=False):
    """An export dir holding `records` as APPA.txt, marked TES3 when `tes3`."""
    folder = tmp_path / 'export' / 'Plugin.esm'
    folder.mkdir(parents=True)
    body = ''.join('---RECORD_BEGIN---\n'
                   + ''.join(f'{key}={value}\n' for key, value in rec.items())
                   + '---RECORD_END---\n' for rec in records)
    (folder / 'APPA.txt').write_text(body, encoding='utf-8')
    if tes3:
        (folder / '_HEADER.txt').write_text('Source=TES3\n', encoding='utf-8')
    return str(folder)


def _output(tmp_path):
    """The output ESM path the sidecar is staged beside."""
    out = tmp_path / 'output' / 'Plugin.esm'
    out.mkdir(parents=True)
    return str(out / 'Plugin.esm')


def _sidecar(output_path):
    """TESRuntime's sidecar path for Plugin.esm."""
    return os.path.join(os.path.dirname(output_path), 'SKSE', 'Plugins',
                        'TESRuntime', 'Plugin.apparatus.json')


def _read(output_path):
    """The staged sidecar, parsed."""
    with open(_sidecar(output_path), encoding='utf-8') as handle:
        return json.load(handle)


_CALCINATOR = {'FormID': '00C98B40', 'EditorID': 'apparatus_a_calcinator_01',
               'DATA.Type': '2', 'DATA.Quality': '0.5'}


@pytest.mark.parametrize('tes4, tes3', [(10.0, 0.15), (25.0, 0.5),
                                        (50.0, 1.0), (75.0, 1.2),
                                        (100.0, 1.5), (150.0, 2.0)])
def test_each_morroblivion_grade_maps_back_to_its_morrowind_quality(tes4,
                                                                    tes3):
    """Every grade Morroblivion ported lands on its Morrowind quality."""
    assert tes3_quality(tes4) == pytest.approx(tes3)


def test_quality_between_and_past_the_grades_is_straight_line():
    """Between grades and past the last one, the mapping is linear."""
    assert tes3_quality(37.5) == pytest.approx(0.75)
    assert tes3_quality(200.0) == pytest.approx(2.5)
    assert tes3_quality(5.0) == pytest.approx(0.075)


def test_a_tes4_plugin_stages_only_its_apparatus_sidecar(tmp_path):
    """A TES4 plugin writes TESRuntime's sidecar alone, its own rows, on
    TES3's scale, and nothing for MorrowindRuntime."""
    export = _export(tmp_path, [
        {'FormID': '000105E3', 'EditorID': 'MortarPestle01',
         'DATA.Type': '0', 'DATA.Quality': '10.0'},
        {'FormID': '0100AAAA', 'EditorID': 'MasterOverride',
         'DATA.Type': '3', 'DATA.Quality': '100.0'},
    ])
    output = _output(tmp_path)
    assert write_morrowind_sidecar(export, output, 'Plugin.esm') == 1
    doc = _read(output)
    assert doc['apparatus'] == [{'id': 'MortarPestle01',
                                 'form': ['Plugin.esm', 0x0105E3],
                                 'type': 0, 'quality': pytest.approx(0.15)}]
    assert 'settings' not in doc
    assert not os.path.isdir(os.path.join(os.path.dirname(output), 'SKSE',
                                          'Plugins', 'MorrowindRuntime'))


def test_a_tes3_plugin_keeps_its_authored_quality(tmp_path):
    """A TES3 plugin's quality is already on the formula's scale."""
    export = _export(tmp_path, [_CALCINATOR], tes3=True)
    output = _output(tmp_path)
    write_morrowind_sidecar(export, output, 'Plugin.esm')
    assert _read(output)['apparatus'] == [
        {'id': 'apparatus_a_calcinator_01', 'form': ['Plugin.esm', 0xC98B40],
         'type': 2, 'quality': 0.5}]


def test_a_tes3_chain_adds_its_settings_and_player(tmp_path):
    """The chain's potion GMSTs, messages and the player's Int and Luck."""
    export = _export(tmp_path, [_CALCINATOR], tes3=True)
    output = _output(tmp_path)
    gathered = {
        'gmsts': {'fpotiont1magmult': 'fPotionT1MagMult=f,1.5',
                  'snotifymessage45': 'sNotifyMessage45=s,Need a\\tMortar',
                  'sother': 'sOther=s,unused'},
        'actors': {'player': 'player=Dark Elf|Acrobat||0|50|0|player|1|0|30|'
                             '40|5|5|0|0|0|0|0|0|30,35,30,30,30,30,30,40|'
                             '5,5,5'},
    }
    assert write_apparatus_sidecar(export, output, 'Plugin.esm', gathered) == 1
    doc = _read(output)
    assert doc['settings'] == {'fPotionT1MagMult': 1.5,
                               'sNotifyMessage45': 'Need a\tMortar'}
    assert doc['player'] == {'intelligence': 35, 'luck': 40}


def test_a_plugin_with_no_apparatus_stages_nothing(tmp_path):
    """No apparatus means no sidecar, and a stale one is removed."""
    export = _export(tmp_path, [])
    output = _output(tmp_path)
    os.makedirs(os.path.dirname(_sidecar(output)))
    with open(_sidecar(output), 'w', encoding='utf-8') as handle:
        handle.write('{}')
    assert write_morrowind_sidecar(export, output, 'Plugin.esm') == 0
    assert not os.path.isfile(_sidecar(output))
