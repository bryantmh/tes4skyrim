"""The case-twin guard skips twin tests on a case-folding filesystem.

A test that creates `Oblivion/` beside `oblivion/` cannot set up its case on
Windows or a default macOS volume: the second mkdir lands in the first
folder, and the test fails for a reason that is not a bug. Such tests take
the `case_twins` fixture, which probes the tmp filesystem and skips.
"""

import os

import pytest

from tests import conftest


def test_the_probe_reads_this_tmp_filesystem(tmp_path):
    """The probe agrees with a direct twin mkdir, and leaves nothing behind."""
    (tmp_path / 'Twin').mkdir()
    folds = (tmp_path / 'twin').exists()

    assert conftest.fs_is_case_sensitive(tmp_path) is not folds
    assert os.listdir(tmp_path) == ['Twin']


def test_the_probe_sees_a_case_folding_filesystem(tmp_path, monkeypatch):
    """With exists() folding case, as Windows' does, the probe says insensitive."""
    real = os.path.exists
    monkeypatch.setattr(conftest.os.path, 'exists',
                        lambda p: real(p) or real(os.path.join(
                            os.path.dirname(p), os.path.basename(p).lower())))

    assert conftest.fs_is_case_sensitive(tmp_path) is False


def test_a_case_folding_filesystem_skips(tmp_path, monkeypatch):
    """require_case_twins raises pytest's skip when the probe says it folds."""
    monkeypatch.setattr(conftest, 'fs_is_case_sensitive', lambda _d: False)

    with pytest.raises(pytest.skip.Exception, match='case-sensitive'):
        conftest.require_case_twins(tmp_path)


def test_a_case_sensitive_filesystem_runs(tmp_path, monkeypatch):
    """With a sensitive probe the guard lets the test through."""
    monkeypatch.setattr(conftest, 'fs_is_case_sensitive', lambda _d: True)

    assert conftest.require_case_twins(tmp_path) is None
