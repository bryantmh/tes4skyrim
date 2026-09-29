"""Session-wide fixtures: undo the process state a real pipeline call sets."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert.game_paths import NAMESPACE_ENV, current_namespace, set_namespace
from asset_convert.sources.source_registry import SELECTED_DIR_ENV, select_directory


@pytest.fixture(autouse=True)
def _restore_process_selection():
    """Put back the asset namespace and selected Data folder a pipeline call sets for the process."""
    before, env = current_namespace(), os.environ.get(NAMESPACE_ENV)
    selected = os.environ.get(SELECTED_DIR_ENV)
    yield
    set_namespace(before)
    if env is None:
        os.environ.pop(NAMESPACE_ENV, None)
    select_directory(selected)


def fs_is_case_sensitive(directory) -> bool:
    """True when `directory`'s filesystem keeps `a` and `A` apart."""
    probe = os.path.join(directory, '.case_probe')
    open(probe, 'wb').close()
    try:
        return not os.path.exists(os.path.join(directory, '.CASE_PROBE'))
    finally:
        os.remove(probe)


def require_case_twins(directory) -> None:
    """Skip the running test unless `directory` can hold names differing only by case."""
    if not fs_is_case_sensitive(directory):
        pytest.skip('case twins need a case-sensitive filesystem (Windows and '
                    'macOS default volumes fold case)')


@pytest.fixture
def case_twins(tmp_path):
    """`tmp_path` for a case-twin test; skipped where the filesystem folds case."""
    require_case_twins(tmp_path)
    return tmp_path
