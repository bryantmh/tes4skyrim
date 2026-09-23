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
