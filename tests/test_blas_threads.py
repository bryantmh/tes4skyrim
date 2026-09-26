"""Pool workers must start with one BLAS thread each.

See: docs/commentary/performance.md#blas-threads-commit
"""
import os

from core.subprocess_flags import configure_multiprocessing


def test_blas_threads_default_to_one(monkeypatch):
    """With nothing set, workers inherit OPENBLAS_NUM_THREADS=1."""
    monkeypatch.delenv('OPENBLAS_NUM_THREADS', raising=False)
    configure_multiprocessing()
    assert os.environ['OPENBLAS_NUM_THREADS'] == '1'


def test_user_blas_threads_win(monkeypatch):
    """A value the user already set is kept."""
    monkeypatch.setenv('OPENBLAS_NUM_THREADS', '4')
    configure_multiprocessing()
    assert os.environ['OPENBLAS_NUM_THREADS'] == '4'
