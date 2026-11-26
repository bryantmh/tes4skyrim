"""Packing must read source files past Windows' 260-character MAX_PATH.

A long plugin folder name plus creature animdata crosses the limit, so every
source is listed and opened through `long_path`.
See: docs/commentary/asset_convert_bsa.md#staging-past-the-path-limit
"""

import os
import sys

from asset_convert.sources import bsa_pack
from asset_convert.sources.bsa_extract import iter_bsa
from asset_convert.sources.bsa_write import long_path

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


def test_a_source_past_max_path_is_packed(tmp_path):
    """A source file whose absolute path exceeds MAX_PATH is collected, packed and reads back."""
    if WIN_ONLY:
        return
    rel = os.path.join('meshes', *(['d' * 40] * 4), 'f' * 40 + '.nif')
    src = tmp_path / rel
    assert len(str(src)) > 260, 'test must exercise the limit'
    os.makedirs(long_path(src.parent))
    with open(long_path(src), 'wb') as fh:
        fh.write(b'NIF' * 100)
    results = {'packed': [], 'kept': [], 'skipped': [], 'errors': []}
    bsa_pack._pack(tmp_path, 'Long', bsa_pack.BSA_SIZE_LIMIT, results)
    assert dict(iter_bsa(tmp_path / 'Long.bsa')) == {rel.lower(): b'NIF' * 100}
