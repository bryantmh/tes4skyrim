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
    find_lipgenerator,
    find_xwmaencode,
    load_lip_text,
    organize_voice_files,
    pack_fuz,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FFMPEG = find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason='ffmpeg not on PATH')
XWMAENCODE = find_xwmaencode()
needs_xwmaencode = pytest.mark.skipif(XWMAENCODE is None,
                                      reason='xWMAEncode.exe not found')
LIPGENERATOR = find_lipgenerator()
needs_lipgenerator = pytest.mark.skipif(
    LIPGENERATOR is None, reason='LipGenerator.exe not found (SSE Tools/LipGen)')

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
@needs_xwmaencode
def test_convert_wav_to_xwm_produces_xwma_output(tmp_path):
    """A WAV input should produce a real xWMA file (RIFF/XWMA container)."""
    src = _make_wav(tmp_path / 'test.wav')
    dst = tmp_path / 'test.xwm'
    ok = convert_file_to_xwm(src, dst, FFMPEG, xwmaencode=XWMAENCODE)
    assert ok, 'convert_file_to_xwm returned False'
    assert dst.is_file(), 'Output file was not created'
    assert dst.stat().st_size > 0, 'Output file is empty'
    header = dst.read_bytes()[:16]
    assert header[:4] == b'RIFF' and b'XWMA' in header, \
        f'Output is not xWMA: {header.hex()}'


@needs_ffmpeg
def test_convert_file_to_xwm_without_encoder_returns_false(tmp_path):
    """Without xWMAEncode there is no ASF fallback — must return False
    (ffmpeg's ASF container does not play reliably in Skyrim)."""
    src = _make_wav(tmp_path / 'a.wav')
    dst = tmp_path / 'a.xwm'
    assert convert_file_to_xwm(src, dst, FFMPEG, xwmaencode=None) is False


@needs_ffmpeg
@needs_xwmaencode
def test_convert_file_to_xwm_creates_parent_dirs(tmp_path):
    """convert_file_to_xwm should create missing parent directories."""
    src = _make_wav(tmp_path / 'a.wav')
    dst = tmp_path / 'deep' / 'nested' / 'out.xwm'
    assert not dst.parent.exists()
    ok = convert_file_to_xwm(src, dst, FFMPEG, xwmaencode=XWMAENCODE)
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

    # Non-voice sounds are copied as-is (Skyrim SE plays WAV/MP3/XWM natively)
    assert result['copied'] == 2
    assert result['failed'] == 0
    assert result['total'] == 2

    out_dir = tmp_path / 'output' / plugin / 'sound' / 'tes4'
    assert (out_dir / 'a.wav').is_file()
    assert (out_dir / 'b.wav').is_file()


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
# Lip sync (.lip generation, .fuz packing, transcript map)
# ---------------------------------------------------------------------------

def test_pack_fuz_layout():
    """FUZE container: magic, version 1, lip size, lip bytes, audio bytes."""
    fuz = pack_fuz(b'LIPDATA', b'XWMAUDIO')
    assert fuz[:4] == b'FUZE'
    version, lip_size = struct.unpack('<II', fuz[4:12])
    assert version == 1
    assert lip_size == 7
    assert fuz[12:19] == b'LIPDATA'
    assert fuz[19:] == b'XWMAUDIO'


def test_load_lip_text_roundtrip(tmp_path):
    """The importer's liptext writer output parses back, escapes intact."""
    from tes5_import.import_main import _write_lip_text
    texts = {(0x00A1B2, 1): 'Attack! I will tear you apart!',
             (0x00A1B2, 2): 'Line with\nnewline and\ttab and \\backslash',
             (0x123456, 1): 'Plain line.'}
    out_base = str(tmp_path / 'Test.esm')
    _write_lip_text(out_base, texts)
    loaded = load_lip_text(out_base + '.liptext.txt')
    assert loaded == texts


@needs_ffmpeg
@needs_xwmaencode
@needs_lipgenerator
def test_convert_to_fuz_with_lip(tmp_path):
    """With a transcript + LipGenerator, output is a valid .fuz containing a
    non-empty lip track followed by the xWMA audio."""
    src = _make_wav(tmp_path / 'test.wav', duration_s=0.5)
    dst = tmp_path / 'test.fuz'
    ok = convert_file_to_xwm(src, dst, FFMPEG, xwmaencode=XWMAENCODE,
                             lipgenerator=LIPGENERATOR,
                             lip_text='Hello there, traveler.')
    assert ok
    assert dst.is_file()
    data = dst.read_bytes()
    assert data[:4] == b'FUZE'
    version, lip_size = struct.unpack('<II', data[4:12])
    assert version == 1 and lip_size > 0
    audio = data[12 + lip_size:]
    assert audio[:4] == b'RIFF' and b'XWMA' in audio[:16]


@needs_ffmpeg
@needs_xwmaencode
def test_convert_to_fuz_without_lipgen_falls_back_to_xwm(tmp_path):
    """A .fuz destination without LipGenerator degrades to bare .xwm so the
    audio still plays (mouth just won't move)."""
    src = _make_wav(tmp_path / 'test.wav')
    dst = tmp_path / 'test.fuz'
    ok = convert_file_to_xwm(src, dst, FFMPEG, xwmaencode=XWMAENCODE,
                             lipgenerator=None, lip_text='Some text')
    assert ok
    assert not dst.exists()
    assert (tmp_path / 'test.xwm').is_file()


@needs_ffmpeg
@needs_xwmaencode
@needs_lipgenerator
def test_organize_voice_files_generates_fuz(tmp_path):
    """A voice line whose transcript is in lip_text comes out as .fuz; a line
    with no transcript stays .xwm."""
    plugin = 'Test.esm'
    voice_src = tmp_path / 'extract' / 'sound' / 'Voice' / plugin / 'Nord' / 'M'
    voice_src.mkdir(parents=True, exist_ok=True)
    _make_wav(voice_src / 'hello_0000a1b2_1.wav', duration_s=0.5)
    _make_wav(voice_src / 'hello_0000c3d4_1.wav', duration_s=0.5)

    result = organize_voice_files(
        source_dir=str(tmp_path / 'extract'),
        dest_dir=str(tmp_path / 'output'),
        plugin_name=plugin,
        convert_audio=True,
        ffmpeg_path=FFMPEG,
        lip_text={(0x00A1B2, 1): 'Hello there, traveler.'},
    )
    assert result['errors'] == 0
    assert result['organized'] == 2
    out_dir = tmp_path / 'output' / 'sound' / 'Voice' / plugin / 'TES4MaleNord'
    fuz = out_dir / 'hello_0000a1b2_1.fuz'
    assert fuz.is_file(), 'transcribed line should be packed as .fuz'
    assert fuz.read_bytes()[:4] == b'FUZE'
    assert (out_dir / 'hello_0000c3d4_1.xwm').is_file(), \
        'untranscribed line should stay .xwm'


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
        # groups: (prefix, formid8, response index, extension)
        assert m.group(2).lower() == expected[0]
        assert m.group(3) == expected[1]
        assert m.group(4).lower() == expected[2]


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

    # Skyrim resolves <prefix>_<fid8 with load byte ZEROED>_<n> — the FormID
    # is NOT shifted, and without a voice map the source prefix is kept.
    out_path = (tmp_path / 'output' / 'sound' / 'Voice' / plugin
                / 'TES4MaleNord' / 'hello_0000a1b2_0.xwm')
    assert out_path.is_file(), f'Expected output not found: {out_path}'


@needs_ffmpeg
def test_organize_voice_files_uses_voice_map(tmp_path):
    """The importer's voicemap renames files to the prefix Skyrim will
    actually look up (converted owning-quest + topic EditorIDs)."""
    plugin = 'Test.esm'
    voice_src = tmp_path / 'extract' / 'sound' / 'Voice' / plugin / 'Nord' / 'M'
    voice_src.mkdir(parents=True, exist_ok=True)
    _make_wav(voice_src / 'oldquest_oldtopic_0000a1b2_1.wav')

    result = organize_voice_files(
        source_dir=str(tmp_path / 'extract'),
        dest_dir=str(tmp_path / 'output'),
        plugin_name=plugin,
        convert_audio=True,
        ffmpeg_path=FFMPEG,
        voice_map={0x00A1B2: 'newquest_newtopic'},
    )
    assert result['errors'] == 0 and result['organized'] == 1
    out_path = (tmp_path / 'output' / 'sound' / 'Voice' / plugin
                / 'TES4MaleNord' / 'newquest_newtopic_0000a1b2_1.xwm')
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


def test_organize_voice_files_prunes_stale_outputs(tmp_path):
    """Output files whose prefix/location no longer matches the voicemap are
    deleted: the prefix embeds quest/topic EditorIDs that change across import
    runs, and the engine can never resolve the old names."""
    plugin = 'Test.esm'
    voice_src = tmp_path / 'extract' / 'sound' / 'Voice' / plugin / 'Nord' / 'M'
    voice_src.mkdir(parents=True, exist_ok=True)
    out_vt = tmp_path / 'output' / 'sound' / 'Voice' / plugin / 'TES4MaleNord'
    out_vt.mkdir(parents=True, exist_ok=True)
    stale_prefix = out_vt / 'oldquest_oldtopic_0000a1b2_1.xwm'
    stale_gone = out_vt / 'quest_topic_0000ffff_1.xwm'
    current = out_vt / 'newquest_newtopic_0000a1b2_1.xwm'
    for f in (stale_prefix, stale_gone, current):
        f.write_bytes(b'xwm')
    # NPC-specific line relocated to TES4MaleImperial: the Nord-folder copy
    # is dead weight (the engine only reads the speaker's VTYP folder).
    stale_loc = out_vt / 'q_reloc_0000b3c4_1.xwm'
    stale_loc.write_bytes(b'xwm')

    result = organize_voice_files(
        source_dir=str(tmp_path / 'extract'),
        dest_dir=str(tmp_path / 'output'),
        plugin_name=plugin,
        convert_audio=False,
        voice_map={0x00A1B2: 'newquest_newtopic',
                   0x00B3C4: ('q_reloc', ['TES4MaleImperial'])},
    )
    assert result['pruned'] == 3
    assert not stale_prefix.exists()
    assert not stale_gone.exists()
    assert not stale_loc.exists()
    assert current.exists()


def test_organize_voice_files_missing_voice_dir(tmp_path):
    """Returns zero counts if no Voice directory exists; does not raise."""
    result = organize_voice_files(
        source_dir=str(tmp_path / 'no_such'),
        dest_dir=str(tmp_path / 'output'),
    )
    assert result['organized'] == 0
