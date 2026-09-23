"""The apparatus table MorrowindRuntime's alchemy hooks read.

See: docs/commentary/morrowind_runtime.md#alchemy-apparatus
"""

import os

import pytest

from tes5_import.dialogue.morrowind_sidecar import (APPARATUS_TABLE,
                                                    tes3_quality,
                                                    write_morrowind_sidecar)


def _export(tmp_path, records, tes3=False):
    """An export dir holding `records` as APPA.txt, plus a TES3 dialogue file
    when `tes3`."""
    folder = tmp_path / 'export' / 'Plugin.esm'
    folder.mkdir(parents=True)
    body = ''.join('---RECORD_BEGIN---\n'
                   + ''.join(f'{key}={value}\n' for key, value in rec.items())
                   + '---RECORD_END---\n' for rec in records)
    (folder / 'APPA.txt').write_text(body, encoding='utf-8')
    if tes3:
        (folder / 'MWDI.txt').write_text('', encoding='utf-8')
    return str(folder)


def _output(tmp_path):
    """The output ESM path the sidecar is staged beside."""
    out = tmp_path / 'output' / 'Plugin.esm'
    out.mkdir(parents=True)
    return str(out / 'Plugin.esm')


def _staged(output_path):
    """`(sidecar folder, sorted file names in it)`."""
    folder = os.path.join(os.path.dirname(output_path), 'SKSE', 'Plugins',
                          'MorrowindRuntime', 'Plugin')
    return folder, sorted(os.listdir(folder)) if os.path.isdir(folder) else []


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


def test_a_tes4_plugin_stages_only_its_apparatus_table(tmp_path):
    """A TES4 plugin writes APPA.txt alone, its own rows, on TES3's scale."""
    export = _export(tmp_path, [
        {'FormID': '000105E3', 'EditorID': 'MortarPestle01',
         'DATA.Type': '0', 'DATA.Quality': '10.0'},
        {'FormID': '0100AAAA', 'EditorID': 'MasterOverride',
         'DATA.Type': '3', 'DATA.Quality': '100.0'},
    ])
    output = _output(tmp_path)
    assert write_morrowind_sidecar(export, output, 'Plugin.esm') == 1
    folder, files = _staged(output)
    assert files == [APPARATUS_TABLE]
    with open(os.path.join(folder, APPARATUS_TABLE), encoding='utf-8') as fh:
        assert fh.read().splitlines() == [
            'MortarPestle01=Plugin.esm|000105E3|0|0.15']


def test_a_tes3_plugin_keeps_its_authored_quality(tmp_path):
    """A TES3 plugin's quality is already on the formula's scale."""
    export = _export(tmp_path, [
        {'FormID': '00C98B40', 'EditorID': 'apparatus_a_calcinator_01',
         'DATA.Type': '2', 'DATA.Quality': '0.5'},
    ], tes3=True)
    output = _output(tmp_path)
    write_morrowind_sidecar(export, output, 'Plugin.esm')
    folder, files = _staged(output)
    assert APPARATUS_TABLE in files
    with open(os.path.join(folder, APPARATUS_TABLE), encoding='utf-8') as fh:
        assert fh.read().splitlines() == [
            'apparatus_a_calcinator_01=Plugin.esm|00C98B40|2|0.5']


def test_a_plugin_with_no_apparatus_stages_nothing(tmp_path):
    """No apparatus means no sidecar folder at all for a TES4 plugin."""
    export = _export(tmp_path, [])
    output = _output(tmp_path)
    assert write_morrowind_sidecar(export, output, 'Plugin.esm') == 0
    assert _staged(output)[1] == []
