"""Tests for asset_convert.audio_converter.

Tests cover:
  - find_ffmpeg(): detection on PATH vs missing
  - convert_file_to_xwm(): WAV -> XWM, failure on bad input, zero-size check
  - convert_sounds(): parallel directory batch (with and without ffmpeg)
  - organize_voice_files(): TES4->TES5 layout reorganisation
"""
import shutil
import struct
import tempfile
import wave
from pathlib import Path

import pytest

from asset_convert.audio_converter import (
    _TES4_VOICE_TYPE_MAP,
    _VOICE_FILENAME_RE,
    convert_file_to_xwm,
    convert_sounds,
    find_ffmpeg,
    organize_voice_files,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FFMPEG = find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason='ffmpeg not on PATH')

# ASF (WMA / XWM) container magic bytes (first 16 bytes)
ASF_MAGIC = bytes([
    0x30, 0x26, 0xB2, 0x75, 0x8E, 0x66, 0xCF, 0x11,
    0xA6, 0xD9, 0x00, 0xAA, 0x00, 0x62, 0xCE, 0x6C,
])


def _make_wav(path: Path, duration_s: float = 0.1, sample_rate: int = 44100) -> Path:
    """Write a minimal silent WAV file; returns *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(sample_rate * duration_s)
    with wave.open(str(path), 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b'\x00\x00' * n_samples)
    return path


# ---------------------------------------------------------------------------
# find_ffmpeg
# ---------------------------------------------------------------------------

def test_find_ffmpeg_with_valid_path():
    """find_ffmpeg returns a non-None path when ffmpeg is available."""
    if FFMPEG is None:
        pytest.skip('ffmpeg not on PATH')
    result = find_ffmpeg()
    assert result is not None
    assert 'ffmpeg' in result.lower() or result == 'ffmpeg'


def test_find_ffmpeg_with_invalid_path():
    """find_ffmpeg returns None for a non-existent executable."""
    result = find_ffmpeg('/no/such/ffmpeg_xyz_not_real')
    assert result is None


# ---------------------------------------------------------------------------
# convert_file_to_xwm
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_convert_wav_to_xwm_produces_asf_output(tmp_path):
    """A WAV input should produce a file with ASF magic bytes."""
    src = _make_wav(tmp_path / 'test.wav')
    dst = tmp_path / 'test.xwm'
    ok = convert_file_to_xwm(src, dst, FFMPEG)
    assert ok, 'convert_file_to_xwm returned False'
    assert dst.is_file(), 'Output file was not created'
    assert dst.stat().st_size > 0, 'Output file is empty'
    header = dst.read_bytes()[:16]
    assert header == ASF_MAGIC, f'Output does not have ASF magic: {header.hex()}'


@needs_ffmpeg
def test_convert_file_to_xwm_creates_parent_dirs(tmp_path):
    """convert_file_to_xwm should create missing parent directories."""
    src = _make_wav(tmp_path / 'a.wav')
    dst = tmp_path / 'deep' / 'nested' / 'out.xwm'
    assert not dst.parent.exists()
    ok = convert_file_to_xwm(src, dst, FFMPEG)
    assert ok
    assert dst.parent.exists()
    assert dst.is_file()


@needs_ffmpeg
def test_convert_file_to_xwm_returns_false_for_invalid_input(tmp_path):
    """convert_file_to_xwm returns False when ffmpeg cannot process the input."""
    src = tmp_path / 'garbage.wav'
    src.write_bytes(b'not a real audio file xxxxxxx')
    dst = tmp_path / 'out.xwm'
    ok = convert_file_to_xwm(src, dst, FFMPEG)
    assert not ok


def test_convert_file_to_xwm_returns_false_for_missing_ffmpeg(tmp_path):
    """convert_file_to_xwm returns False when ffmpeg is not found."""
    src = _make_wav(tmp_path / 'a.wav')
    dst = tmp_path / 'a.xwm'
    ok = convert_file_to_xwm(src, dst, '/no/such/ffmpeg_xyz')
    assert not ok


# ---------------------------------------------------------------------------
# convert_sounds (batch)
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_convert_sounds_converts_wav_files(tmp_path):
    """WAV files in the extract dir should be converted to XWM in output dir."""
    plugin = 'Test.esm'
    snd_dir = tmp_path / 'export' / plugin / 'sound'
    for name in ('a.wav', 'b.wav'):
        _make_wav(snd_dir / name)

    result = convert_sounds(
        source_file=plugin,
        extract_dir=str(tmp_path / 'export'),
        output_dir=str(tmp_path / 'output'),
    )

    assert result['converted'] == 2
    assert result['copied'] == 0
    assert result['failed'] == 0
    assert result['total'] == 2

    out_dir = tmp_path / 'output' / plugin / 'sound' / 'tes4'
    assert (out_dir / 'a.xwm').is_file()
    assert (out_dir / 'b.xwm').is_file()


@needs_ffmpeg
def test_convert_sounds_copies_non_audio_files(tmp_path):
    """Non-audio files (e.g. .lip) should be copied as-is."""
    plugin = 'Test.esm'
    snd_dir = tmp_path / 'export' / plugin / 'sound'
    snd_dir.mkdir(parents=True, exist_ok=True)
    (snd_dir / 'file.lip').write_bytes(b'LIP DATA')

    result = convert_sounds(
        source_file=plugin,
        extract_dir=str(tmp_path / 'export'),
        output_dir=str(tmp_path / 'output'),
    )

    assert result['copied'] == 1
    out = tmp_path / 'output' / plugin / 'sound' / 'tes4' / 'file.lip'
    assert out.is_file()


def test_convert_sounds_no_ffmpeg_falls_back_to_copy(tmp_path):
    """Without ffmpeg, all files should be copied and counts reflected."""
    plugin = 'Test.esm'
    snd_dir = tmp_path / 'export' / plugin / 'sound'
    for name in ('x.wav', 'y.wav', 'z.lip'):
        p = snd_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'dummy')

    result = convert_sounds(
        source_file=plugin,
        extract_dir=str(tmp_path / 'export'),
        output_dir=str(tmp_path / 'output'),
        ffmpeg_path='/no/such/ffmpeg_xyz',
    )

    # All files copied; none "converted"
    assert result['converted'] == 0
    assert result['total'] == 3


def test_convert_sounds_missing_sound_dir(tmp_path):
    """Returns zero counts when no sound directory exists; does not raise."""
    result = convert_sounds(
        source_file='NoPlugin.esm',
        extract_dir=str(tmp_path / 'empty_extract'),
        output_dir=str(tmp_path / 'output'),
    )
    assert result == {'converted': 0, 'copied': 0, 'failed': 0, 'total': 0}


# ---------------------------------------------------------------------------
# _VOICE_FILENAME_RE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('name,expected', [
    ('arenadialogue_attack_0018bd5d_1.mp3',   ('0018bd5d', '1', 'mp3')),
    ('goodbye_0002a7d3_1.mp3',                ('0002a7d3', '1', 'mp3')),
    ('hello_world_00abcdef_10.xwm',           ('00abcdef', '10', 'xwm')),
    ('topic_00000001_0.wav',                  ('00000001', '0', 'wav')),
    ('notavoicefile.mp3',                     None),
    ('tooShort_abc_1.mp3',                    None),
])
def test_voice_filename_re(name, expected):
    m = _VOICE_FILENAME_RE.match(name)
    if expected is None:
        assert m is None, f'Expected no match for {name!r}'
    else:
        assert m is not None, f'Expected a match for {name!r}'
        assert m.group(1).lower() == expected[0]
        assert m.group(2) == expected[1]
        assert m.group(3).lower() == expected[2]


# ---------------------------------------------------------------------------
# _TES4_VOICE_TYPE_MAP
# ---------------------------------------------------------------------------

def test_voice_type_map_has_standard_races():
    """All expected playable Oblivion races should be in the map."""
    races = ['Argonian', 'Breton', 'DarkElf', 'HighElf', 'Imperial',
             'Khajiit', 'Nord', 'Orc', 'Redguard', 'WoodElf']
    for race in races:
        assert (race, 'M') in _TES4_VOICE_TYPE_MAP
        assert (race, 'F') in _TES4_VOICE_TYPE_MAP


def test_voice_type_map_includes_shivering_isles_races():
    assert ('DarkSeducer', 'M') in _TES4_VOICE_TYPE_MAP
    assert ('GoldenSaint', 'F') in _TES4_VOICE_TYPE_MAP


# ---------------------------------------------------------------------------
# organize_voice_files
# ---------------------------------------------------------------------------

@needs_ffmpeg
def test_organize_voice_files_basic(tmp_path):
    """Voice files should be reorganised from TES4 layout to TES5 layout."""
    plugin = 'Test.esm'
    # Build TES4 voice layout: sound/Voice/<plugin>/Nord/M/<topic>_<fid>_0.wav
    voice_src = tmp_path / 'extract' / 'sound' / 'Voice' / plugin / 'Nord' / 'M'
    voice_src.mkdir(parents=True, exist_ok=True)
    wav = _make_wav(voice_src / 'hello_0000a1b2_0.wav')

    result = organize_voice_files(
        source_dir=str(tmp_path / 'extract'),
        dest_dir=str(tmp_path / 'output'),
        plugin_name=plugin,
        convert_audio=True,
        ffmpeg_path=FFMPEG,
        formid_index=1,
    )

    assert result['errors'] == 0
    assert result['organized'] == 1

    expected_fid = (0x0000a1b2 & 0x00FFFFFF) | (1 << 24)
    expected_name = f'{expected_fid:08X}_0.xwm'
    out_path = (tmp_path / 'output' / 'Sound' / 'Voice' / plugin
                / 'TES4MaleNord' / expected_name)
    assert out_path.is_file(), f'Expected output not found: {out_path}'


def test_organize_voice_files_no_match_counted(tmp_path):
    """Files that don't match the voice filename pattern are counted as no_match."""
    plugin = 'Test.esm'
    voice_src = tmp_path / 'extract' / 'sound' / 'Voice' / plugin / 'Nord' / 'M'
    voice_src.mkdir(parents=True, exist_ok=True)
    (voice_src / 'notavoicefile.mp3').write_bytes(b'dummy')

    result = organize_voice_files(
        source_dir=str(tmp_path / 'extract'),
        dest_dir=str(tmp_path / 'output'),
        plugin_name=plugin,
        convert_audio=False,
    )
    assert result['no_match'] == 1
    assert result['organized'] == 0


def test_organize_voice_files_missing_voice_dir(tmp_path):
    """Returns zero counts if no Voice directory exists; does not raise."""
    result = organize_voice_files(
        source_dir=str(tmp_path / 'no_such'),
        dest_dir=str(tmp_path / 'output'),
    )
    assert result['organized'] == 0
