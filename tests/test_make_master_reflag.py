"""A plugin the user chose to flag ESM is flagged again whenever it is rebuilt."""

import struct

from tools.esm import make_master


def _plugin(path, flags: int = 0) -> None:
    """A minimal TES4 header with `flags`."""
    path.write_bytes(b"TES4" + struct.pack("<II", 0, flags) + b"\0" * 12)


def test_a_chosen_plugin_is_flagged_and_others_are_left_alone(tmp_path):
    """Only a name on the config's list (any case) gets the ESM bit; a missing file is skipped."""
    chosen, other = tmp_path / "Tamriel.esp", tmp_path / "Other.esp"
    _plugin(chosen)
    _plugin(other)
    config = {make_master.CONFIG_KEY: ["tamriel.ESP", "Gone.esp"]}
    assert make_master.reflag_if_chosen(str(chosen), config)
    assert not make_master.reflag_if_chosen(str(other), config)
    assert not make_master.reflag_if_chosen(str(tmp_path / "Gone.esp"), config)
    assert make_master.read_header(str(chosen))[0] & make_master.FLAG_ESM
    assert not make_master.read_header(str(other))[0] & make_master.FLAG_ESM


def test_changing_the_flag_drops_the_mods_saved_summary(tmp_path):
    """Install & play rereads a mod whose plugin Masters reflagged; an unchanged flag keeps the summary."""
    from output_layout import SUMMARY_NAME

    plugin, saved = tmp_path / "Tamriel.esp", tmp_path / SUMMARY_NAME
    _plugin(plugin, make_master.FLAG_ESM)
    saved.write_text("{}")
    make_master.set_flag(str(plugin), True)
    assert saved.is_file()
    make_master.set_flag(str(plugin), False)
    assert not saved.is_file()
