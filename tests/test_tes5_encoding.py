"""Skyrim output must not mix source codepages with UTF-8 HUD captions."""

import struct

import pytest

from core.tes4_encoding import ENCODING_ENV_VAR, decode
from tes5_import.base.tes5_reader import sub_map, subrecords
from tes5_import.base.writer import (pack_record, pack_string_subrecord,
                                     pack_subrecord)
from tes5_import.overrides.builder import apply_changes
from tes5_import.record_types.items import convert_DOOR


SOURCE_TEXTS = [
    ("cp1251", "Герминия Цинна"),
    ("cp1252", "Grüße"),
    ("cp1250", "Łódź"),
]


@pytest.mark.parametrize("codec,text", SOURCE_TEXTS)
def test_source_text_becomes_utf8(monkeypatch, codec, text):
    """Read the source codepage, then serialize Unicode for Skyrim."""
    monkeypatch.setenv(ENCODING_ENV_VAR, codec)
    source = text.encode(codec)
    packed = pack_string_subrecord("FULL", decode(source))
    payload = dict(subrecords(packed))[b"FULL"]
    assert payload == text.encode("utf-8") + b"\0"
    assert struct.unpack_from("<H", packed, 4)[0] == len(payload)


@pytest.mark.parametrize("codec", ["auto", "cp1251", "cp1252", "cp1250"])
def test_output_preserves_text_outside_source_codepage(monkeypatch, codec):
    """Mixed-language output cannot be replaced with question marks."""
    monkeypatch.setenv(ENCODING_ENV_VAR, codec)
    text = "Герминия — Grüße — Łódź — 日本語"
    payload = dict(subrecords(pack_string_subrecord("DESC", text)))[b"DESC"]
    assert payload[:-1].decode("utf-8") == text


def test_converted_door_rollover_accepts_utf8_caption(monkeypatch):
    """The Russian target name must keep the entire HUD string valid UTF-8."""
    monkeypatch.setenv(ENCODING_ENV_VAR, "cp1251")
    name = decode("Деревянная дверь".encode("cp1251"))
    record = convert_DOOR({"FormID": "01000001", "EditorID": "TestDoor",
                           "FULL": name})
    fields = sub_map(record[24:])
    rollover = "Открыть\n".encode("utf-8") + fields[b"FULL"][:-1]
    assert rollover.decode("utf-8") == "Открыть\nДеревянная дверь"
    assert fields[b"EDID"] == b"TestDoor\0"


@pytest.mark.parametrize("codec,text", SOURCE_TEXTS)
@pytest.mark.parametrize("field", ["FULL", "DESC"])
def test_translation_override_writes_utf8(monkeypatch, codec, text, field):
    """Translation substitutions use the same codec as new records."""
    monkeypatch.setenv(ENCODING_ENV_VAR, codec)
    binary = bytes(range(16))
    master = pack_record("BOOK", 0x01000001, 0,
                         pack_string_subrecord("EDID", "TestBook")
                         + pack_string_subrecord(field, "Original")
                         + pack_subrecord("DATA", binary))
    translated = decode(text.encode(codec))
    override, _, unmapped = apply_changes(
        master, {field: translated}, {"Signature": "BOOK", field: translated})
    fields = sub_map(override[24:])
    assert fields[field.encode("ascii")] == text.encode("utf-8") + b"\0"
    assert fields[b"EDID"] == b"TestBook\0"
    assert fields[b"DATA"] == binary
    assert not unmapped
    assert struct.unpack_from("<I", override, 4)[0] == len(override) - 24


@pytest.mark.parametrize("codec,text", SOURCE_TEXTS)
def test_dialogue_override_writes_utf8(monkeypatch, codec, text):
    """Indexed response text is UTF-8 while untouched responses are kept."""
    monkeypatch.setenv(ENCODING_ENV_VAR, codec)
    master = pack_record("INFO", 0x01000002, 0,
                         pack_string_subrecord("EDID", "TestDialogue")
                         + pack_subrecord("TRDT", bytes(24))
                         + pack_string_subrecord("NAM1", "Original")
                         + pack_subrecord("TRDT", bytes(24))
                         + pack_string_subrecord("NAM1", "Untouched"))
    plugin = {"Signature": "INFO", "ResponseCount": "1",
              "Response[0].ResponseText": decode(text.encode(codec))}
    override, applied, unmapped = apply_changes(master, {"Response[]": ""}, plugin)
    fields = subrecords(override[24:])
    assert [data for tag, data in fields if tag == b"NAM1"] == [
        text.encode("utf-8") + b"\0", b"Untouched\0"]
    assert [data for tag, data in fields if tag == b"TRDT"] == [bytes(24)] * 2
    assert applied == {"Response[]"}
    assert not unmapped


def test_utf8_expansion_uses_extended_subrecord_size(monkeypatch):
    """A codepage-sized book can exceed 65535 bytes after UTF-8 encoding."""
    monkeypatch.setenv(ENCODING_ENV_VAR, "cp1251")
    text = "Я" * 40000
    packed = pack_string_subrecord("DESC", text)
    assert packed[:4] == b"XXXX"
    assert struct.unpack_from("<I", packed, 6)[0] == 80001
    assert dict(subrecords(packed))[b"DESC"] == text.encode("utf-8") + b"\0"
