"""Staged BSA paths must survive Windows' 260-character MAX_PATH.

Each archive is staged as a hardlink tree under
`output/<plugin>/_bsa_staging_<type>/`, which is LONGER than the source tree it
mirrors.  For a plugin with a long folder name plus creature animdata that
crosses MAX_PATH, and BSArch then fails with a disguised
`EAggregateException` under `-mt`.
See: docs/commentary/asset_convert_bsa.md#staging-past-the-path-limit
"""

import os
import sys
from pathlib import Path
import pytest

from asset_convert.sources.bsa_pack import long_path, _stage_bin

WIN_ONLY = sys.platform != 'win32'


class TestLongPath:
    """The prefix helper itself."""

    def test_absolute_path_gets_the_prefix(self):
        """An ordinary absolute Windows path is prefixed."""
        if WIN_ONLY:
            return
        got = long_path(r'C:\Users\x\output\plugin\meshes\a.nif')
        assert got == r'\\?\C:\Users\x\output\plugin\meshes\a.nif'

    def test_prefix_is_not_applied_twice(self):
        """A path that already carries the prefix is returned unchanged."""
        already = r'\\?\C:\x\y.nif'
        assert long_path(already) == already

    def test_relative_path_is_untouched(self):
        r"""\\?\ disables path parsing, so a relative path must not get it."""
        assert long_path('meshes/a.nif') == 'meshes/a.nif'

    def test_separators_are_normalized(self):
        r"""\\?\ passes '/' through verbatim, so the path must be normalized."""
        if WIN_ONLY:
            return
        assert '/' not in long_path('C:/Users/x/a.nif')


class TestStagingBeyondMaxPath:
    """The staging tree is built even where the result exceeds MAX_PATH."""

    def test_stage_bin_links_a_path_over_260_chars(self, tmp_path):
        """A staged path longer than MAX_PATH is still linked, with content.

        The relative path is built deep enough that the staged result crosses
        the limit, which is the shape of the real Unique Landscapes failure.
        """
        if WIN_ONLY:
            return
        src = tmp_path / 'src.nif'
        src.write_bytes(b'NIF')

        deep = os.path.join(*(['d' * 40] * 5), 'f' * 40 + '.nif')
        stage = tmp_path / '_bsa_staging_main_0'
        staged = stage / deep
        assert len(str(staged)) > 260, 'test must exercise the limit'

        assert _stage_bin([(src, deep, 3)], stage) == 1
        assert os.path.exists(long_path(staged))
        assert open(long_path(staged), 'rb').read() == b'NIF'


@pytest.mark.skipif(WIN_ONLY, reason='bundled Windows BSArch')
def test_pack_localized_voice_path_preserves_utf8_and_audio(tmp_path):
    from asset_convert.sources.bsa_pack import _run_bsarch
    from asset_convert.sources.bsa_extract import iter_bsa
    tool = Path(__file__).resolve().parents[1] / 'external/bsarch/BSArch.exe'
    stage = tmp_path / 'staging'
    payload = b'FUZE' + b'\1\0\0\0' + b'\0'*4 + b'RIFF'
    voice = tmp_path / 'source.fuz'
    voice.write_bytes(payload)
    nif = tmp_path / 'source.nif'
    nif.write_bytes(b'NIF payload')
    rel = Path('sound/Voice/Test.esp/TES4MaleНорд/line.fuz')
    name_map = {}
    _stage_bin([(voice, rel, len(payload)),
                (nif, Path('meshes/test.nif'), 11)], stage, name_map)
    archive = tmp_path / 'Test.bsa'
    result = {'packed': [], 'errors': []}
    assert _run_bsarch(str(tool), stage, archive, False, result, name_map)
    assets = {name.encode('latin-1').decode('utf-8'): body
              for name, body in iter_bsa(archive)}
    expected = str(rel).replace('/', '\\').encode('utf-8').lower().decode('utf-8')
    assert assets == {expected: payload, 'meshes\\test.nif': b'NIF payload'}


@pytest.mark.skipif(WIN_ONLY, reason='bundled Windows BSArch')
def test_pack_compressed_localized_texture_preserves_payload(tmp_path):
    from asset_convert.sources.bsa_pack import _run_bsarch
    from asset_convert.sources.bsa_extract import iter_bsa
    tool = Path(__file__).resolve().parents[1] / 'external/bsarch/BSArch.exe'
    source = tmp_path / 'source.dds'
    payload = b'DDS payload' * 100
    source.write_bytes(payload)
    stage = tmp_path / 'staging'
    rel = Path('textures/Русский/Текстура.dds')
    name_map = {}
    _stage_bin([(source, rel, len(payload))], stage, name_map)
    archive = tmp_path / 'Texture.bsa'
    result = {'packed': [], 'errors': []}
    assert _run_bsarch(str(tool), stage, archive, True, result, name_map)
    assets = {name.encode('latin-1').decode('utf-8'): body
              for name, body in iter_bsa(archive)}
    expected = str(rel).replace('/', '\\').encode('utf-8').lower().decode('utf-8')
    assert assets == {expected: payload}
