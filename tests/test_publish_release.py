"""The pre-push release step: which pushes release, and what the release zip is called.

See: docs/commentary/core_gui.md#releases
"""

from core.gui import exe_update
from tools.release import publish_release

SHA = "a" * 40
OLD = "b" * 40


def test_a_push_moving_master_releases_its_commit():
    """git's line for master names the commit to release, whatever else the push carries."""
    lines = [f"refs/tags/0.690 {OLD} refs/tags/0.690 {publish_release.NULL_SHA}",
             f"refs/heads/master {SHA} refs/heads/master {OLD}"]
    assert publish_release.master_update(lines) == SHA


def test_other_pushes_do_not_release():
    """A branch, a tag (the step's own tag push) or deleting master releases nothing."""
    assert publish_release.master_update([f"refs/heads/fix {SHA} refs/heads/fix {OLD}"]) is None
    assert publish_release.master_update([f"refs/tags/0.690 {SHA} refs/tags/0.690 {OLD}"]) is None
    assert publish_release.master_update(
        [f"(delete) {publish_release.NULL_SHA} refs/heads/master {OLD}"]) is None
    assert publish_release.master_update([""]) is None


def test_the_updater_looks_for_the_zip_the_build_names():
    """Release 0.690 carries TESRACT-0.690.zip, the name exe_update downloads."""
    assert exe_update.asset_name("0.690") == "TESRACT-0.690.zip"
