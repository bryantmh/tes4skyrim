"""The GLOB table MorrowindRuntime reads, valued as OpenMW reads each FLTV.

See: docs/commentary/tes4_export_morrowind.md#globals-are-always-filled
"""

import os

from tes5_import.dialogue.morrowind_sidecar import write_morrowind_sidecar


def _globals(tmp_path, records):
    """The staged GLOB.txt rows for a TES3 export holding these globals."""
    folder = tmp_path / 'export' / 'Plugin.esm'
    folder.mkdir(parents=True)
    (folder / '_HEADER.txt').write_text('Source=TES3\n', encoding='utf-8')
    (folder / 'GLOB.txt').write_text(''.join(
        '---RECORD_BEGIN---\n' + ''.join(f'{k}={v}\n' for k, v in rec.items())
        + '---RECORD_END---\n' for rec in records), encoding='utf-8')
    out = tmp_path / 'output' / 'Plugin.esm'
    out.mkdir(parents=True)
    write_morrowind_sidecar(str(folder), str(out / 'Plugin.esm'), 'Plugin.esm')
    path = os.path.join(str(out), 'SKSE', 'Plugins', 'MorrowindRuntime', 'Plugin', 'GLOB.txt')
    with open(path, encoding='utf-8') as handle:
        return {line.split('=')[0]: line.split('=')[1].split(',')[1]
                for line in handle.read().splitlines() if '=' in line}


def test_out_of_range_short_reads_as_zero_and_long_as_int32_min(tmp_path):
    """OpenMW's floatCast: INT32_MIN past range, and a short keeps its low 16 bits."""
    rows = _globals(tmp_path, [
        {'FormID': '00000801', 'EditorID': 'MuseumDonations', 'FNAM.Type': 's',
         'FLTV.Value': '7.5757866783926e+23'},
        {'FormID': '00000802', 'EditorID': 'BigLong', 'FNAM.Type': 'l',
         'FLTV.Value': '7.5757866783926e+23'},
        {'FormID': '00000803', 'EditorID': 'Wrapped', 'FNAM.Type': 's',
         'FLTV.Value': '40000.7'},
        {'FormID': '00000804', 'EditorID': 'Junk', 'FNAM.Type': 's',
         'FLTV.Value': '7.1e-31'},
    ])
    assert rows == {'MuseumDonations': '0', 'BigLong': '-2147483648',
                    'Wrapped': '-25536', 'Junk': '0'}
