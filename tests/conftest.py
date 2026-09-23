"""Session-wide fixtures: undo the process state a real pipeline call sets."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert.game_paths import NAMESPACE_ENV, current_namespace, set_namespace


@pytest.fixture(autouse=True)
def _restore_asset_namespace():
    """Put back the asset namespace and its env var that `import_plugin` sets for the process."""
    before, env = current_namespace(), os.environ.get(NAMESPACE_ENV)
    yield
    set_namespace(before)
    if env is None:
        os.environ.pop(NAMESPACE_ENV, None)
