"""TES4 source text encoding: setting resolution, decoding and auto-detect.

The Russian install stores cp1251 bytes; decoding them as cp1252 (the old
hardcoded behaviour) mangles every name and breaks voice-folder matching.
Default source decoding stays cp1252 for Western installs.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from asset_convert.audio.voice_races import _bsa_spelling, load_race_voices
from core.plugin_masters import export_encoding
from core.tes4_encoding import (ENCODING_ENV_VAR, choice, current, decode,
                                normalize, pin)
from tes4_export.tes4_reader import Record, configure_for_source, detect_codec


def test_normalize_accepts_aliases_and_rejects_unknown():
    """Canonical names for spellings; cp1252 for garbage."""
    assert normalize("windows-1251") == "cp1251"
    assert normalize("CP_1250") == "cp1250"
    assert normalize("bogus-codec") == "cp1252"
    assert normalize("") == "cp1252"
    assert normalize("auto") == "cp1252"


def test_default_choice_is_auto_decoding_cp1252(monkeypatch):
    """Unset env means auto choice but cp1252 right now."""
    monkeypatch.delenv(ENCODING_ENV_VAR, raising=False)
    assert choice() == "auto"
    assert current() == "cp1252"


def test_pin_canonicalises(monkeypatch):
    """Pin stores the canonical codec and returns it."""
    assert pin("windows-1251") == "cp1251"
    assert os.environ[ENCODING_ENV_VAR] == "cp1251"
    assert current() == "cp1251"


def test_pin_keeps_auto_for_detection(monkeypatch):
    """`auto` survives pinning so the export stage still scans the binary."""
    monkeypatch.delenv(ENCODING_ENV_VAR, raising=False)
    assert pin("auto") == "cp1252"
    assert choice() == "auto"


def test_russian_source_decoding(monkeypatch):
    """cp1251 source bytes decode to the original Cyrillic text."""
    monkeypatch.setenv(ENCODING_ENV_VAR, "cp1251")
    raw = "Аргонианин".encode("cp1251")
    assert decode(raw) == "Аргонианин"


def test_western_default_unchanged(monkeypatch):
    """Umlauts still decode as cp1252 when nothing is configured."""
    monkeypatch.delenv(ENCODING_ENV_VAR, raising=False)
    assert decode(b"Gr\xfc\xdfe") == "Grüße"


def test_decode_never_raises(monkeypatch):
    """Undefined bytes become U+FFFD, not an exception, in any codec."""
    monkeypatch.setenv(ENCODING_ENV_VAR, "cp1251")
    assert decode(b"\x98") == "\ufffd"


def _write_header(tmp_path, encoding_line=None):
    """A minimal export dir whose _HEADER.txt optionally declares a codec."""
    lines = ["Flags=1"]
    if encoding_line is not None:
        lines.append(f"ENCODING={encoding_line}")
    (tmp_path / "_HEADER.txt").write_text("\n".join(lines) + "\n",
                                          encoding="utf-8")


def test_export_encoding_reads_header(tmp_path):
    """Declared codec wins; missing or bogus lines mean cp1252."""
    _write_header(tmp_path, "cp1251")
    assert export_encoding(str(tmp_path)) == "cp1251"


def test_export_encoding_defaults_without_header(tmp_path):
    """Old exports predate ENCODING and decode exactly as before."""
    assert export_encoding(str(tmp_path)) == "cp1252"
    _write_header(tmp_path, "bogus")
    assert export_encoding(str(tmp_path)) == "cp1252"


def test_bsa_spelling_is_identity_for_ascii():
    """Western names need no second index key."""
    assert _bsa_spelling("Argonian", "cp1251") == ""


def test_bsa_spelling_reproduces_extractor_bytes():
    """The alias re-encoded as latin-1 is the original plugin bytes."""
    name = "Аргонианин"
    alias = _bsa_spelling(name, "cp1251")
    assert alias != name
    assert alias.encode("latin-1") == name.encode("cp1251")


def _write_race_export(tmp_path, full):
    """An export dir with one RACE record and a cp1251 header."""
    _write_header(tmp_path, "cp1251")
    (tmp_path / "RACE.txt").write_text(
        "---RECORD_BEGIN---\nEditorID=R\nFULL=" + full +
        "\n---RECORD_END---\n", encoding="utf-8")


def test_race_voices_match_both_folder_spellings(tmp_path):
    """Loose (proper Cyrillic) and BSA-extracted (latin-1) folders resolve."""
    full = "Аргонианин"
    _write_race_export(tmp_path, full)
    voices = load_race_voices(str(tmp_path))
    assert voices.folder_key(full) is not None
    assert voices.folder_key(_bsa_spelling(full, "cp1251")) == \
        voices.folder_key(full)


def test_race_voices_match_spaced_folder(tmp_path):
    """A BSA folder with spaces ('dark seducer') resolves like its EDID.

    Without the collapsed fallback it misses every index key and synthesises
    a duplicate ASCII voice type next to the race's real one.
    """
    _write_header(tmp_path, "cp1251")
    (tmp_path / "RACE.txt").write_text(
        "---RECORD_BEGIN---\nEditorID=DarkSeducer\nFULL=Dark Seducer\n"
        "---RECORD_END---\n", encoding="utf-8")
    voices = load_race_voices(str(tmp_path))
    assert voices.folder_key("dark seducer") == \
        voices.folder_key("DarkSeducer")


def _plugin_binary(tmp_path, payloads):
    """A TES4 file with one record holding FULL `payloads`; its offset."""
    subs = b"".join(b"FULL" + struct.pack("<H", len(p)) + p for p in payloads)
    rec = b"ARMO" + struct.pack("<III I", len(subs), 0, 1, 0) + subs
    head = b"TES4" + struct.pack("<III I", 18, 0, 0, 0) + b"HEDR" + \
        struct.pack("<H", 12) + bytes(12)
    grup_at = len(head)
    rec_at = grup_at + 20
    grup = b"GRUP" + struct.pack("<I", 20 + 20 + len(subs)) + bytes(4) + \
        struct.pack("<II", 0, 0)
    path = tmp_path / "sample.esm"
    path.write_bytes(head + grup + rec)
    return str(path), [Record(type="ARMO", data_size=len(subs), flags=0,
                              form_id=1, offset=rec_at)]


def test_detect_russian_binary(tmp_path):
    """Dense high bytes in FULLs mean cp1251."""
    payload = "Аргонианин".encode("cp1251")
    source, records = _plugin_binary(tmp_path, [payload] * 8)
    assert detect_codec(source, records) == "cp1251"


def test_detect_western_binary(tmp_path):
    """ASCII FULLs stay cp1252."""
    source, records = _plugin_binary(tmp_path, [b"Argonian"] * 8)
    assert detect_codec(source, records) == "cp1252"


def test_detect_signal_byte_decides_at_once(tmp_path):
    """One byte cp1252 cannot represent is enough, whatever the density."""
    source, records = _plugin_binary(tmp_path, [b"\x81"])
    assert detect_codec(source, records) == "cp1251"


def test_configure_pins_detection(monkeypatch, tmp_path):
    """Auto mode scans the binary and pins the result for the workers."""
    monkeypatch.delenv(ENCODING_ENV_VAR, raising=False)
    payload = "Аргонианин".encode("cp1251")
    source, records = _plugin_binary(tmp_path, [payload] * 8)
    assert configure_for_source(source, records) == "cp1251"
    assert os.environ[ENCODING_ENV_VAR] == "cp1251"
