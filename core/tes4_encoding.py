"""TES3/TES4 plugin text encoding: Western cp1252 or a localised codepage.

Oblivion and Morrowind store text in the install's Windows codepage, not UTF-8: Western
installs use cp1252, the Russian 1C install cp1251. Decoding Russian bytes as
cp1252 mangles every name into mojibake and turns five Cyrillic capitals into
U+FFFD, which then mismatches every voice folder and filename on disk.

Resolution order (see :func:`choice` and :func:`current`):

  1. ``TESCONV_TES4_ENCODING`` env var, set by ``--tes4-encoding``, the GUI's
     Settings menu, or pinned per plugin by the export stage. It travels in
     the environment so workers agree on the source codec.
  2. The ``tes4Encoding`` key in conversion_config.json (export only).
  3. ``auto``: the export stage scans the plugin binary (see
     ``tes4_reader.detect_codec`` or ``tes3_reader.detect_codec``); every
     other stage defaults to cp1252.

The export writes the resolved source codec as ``ENCODING=`` into
``_HEADER.txt`` for source-byte lookups such as BSA voice-folder spellings.
Skyrim output strings are always UTF-8 (``tes5_import.base.writer``).
An unknown source codec falls back to cp1252.

See: docs/reference/pipeline.md#tes4-text-encoding
"""

import os
import re

__all__ = [
    "ENCODING_ENV_VAR",
    "ENCODING_CONFIG_KEY",
    "ENCODING_DEFAULT",
    "ENCODING_AUTO",
    "ENCODING_CHOICES",
    "normalize",
    "choice",
    "pin",
    "current",
    "decode",
]

#: Env var carrying the choice to every child process and pool worker.
ENCODING_ENV_VAR = "TESCONV_TES4_ENCODING"

#: conversion_config.json key for the same choice.
ENCODING_CONFIG_KEY = "tes4Encoding"

#: What an undecided or unknown choice decodes as: today's behaviour.
ENCODING_DEFAULT = "cp1252"

#: Decide per plugin binary at export time.
ENCODING_AUTO = "auto"

#: Valid settings-menu / CLI values.
ENCODING_CHOICES = ("auto", "cp1252", "cp1251", "cp1250")

#: Accepted spellings per codec (separators stripped); all else is the default.
_CODECS = {
    "cp1252": "cp1252", "windows1252": "cp1252", "1252": "cp1252",
    "cp1251": "cp1251", "windows1251": "cp1251", "1251": "cp1251",
    "cp1250": "cp1250", "windows1250": "cp1250", "1250": "cp1250",
}


def normalize(name) -> str:
    """Canonical codec for `name`; unknown (and `auto`) means cp1252."""
    key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return _CODECS.get(key, ENCODING_DEFAULT)


def choice() -> str:
    """The raw ``TESCONV_TES4_ENCODING`` choice, ``auto`` when unset."""
    return (os.environ.get(ENCODING_ENV_VAR) or ENCODING_AUTO).strip().lower()


def pin(codec: str) -> str:
    """Fix the env var to `codec` and return the resolved codec.

    `auto` is stored as-is so the export stage still detects per binary;
    anything else is canonicalised, unknown names falling back to cp1252.
    """
    raw = str(codec or "").strip().lower()
    os.environ[ENCODING_ENV_VAR] = raw if raw in ENCODING_CHOICES else normalize(codec)
    return current()


def current() -> str:
    """The codec to decode source bytes with right now (never ``auto``)."""
    return normalize(choice())


def decode(data: bytes) -> str:
    """Plugin bytes as text, in the current codec, never raising."""
    return bytes(data).decode(current(), errors="replace")
