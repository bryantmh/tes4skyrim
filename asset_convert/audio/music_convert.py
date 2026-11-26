"""Music conversion: TES4 loose Data/Music -> TES5 xWMA + a track manifest.

Music is the one asset class NO record names a file for.  Oblivion's engine
scans Data/Music/<Category>/ and shuffles whatever it finds, so CELL.XCMT and
WRLD.SNAM carry only the 3-value {Default, Public, Dungeon} enum.  That makes
the FOLDER the authored unit of meaning, and this module preserves it:

    export/<plugin>/music/Explore/Atmosphere_01.mp3
      -> output/<plugin>/music/tes4/<plugin>/Explore/Atmosphere_01.xwm

The `tes4/<plugin>/` scoping is load-bearing.  Oblivion and Nehrim BOTH ship an
`Explore/` folder, so converting two plugins into a shared `Music/Explore/`
would have one silently overwrite the other.

Output is xWMA, not PCM .wav.  Vanilla SSE ships loose music as RIFF/XWMA
(wFormatTag=0x161, 44.1 kHz stereo) and decoding the 329 MB of source mp3 to
PCM would inflate it to ~1.5-3 GB.  Unlike the voice path this encodes STEREO
at a music bitrate -- `convert_file_to_xwm` downmixes to mono, which is right
for dialogue and wrong for a soundtrack.

The manifest this writes (`music_tracks.json`) is what the importer turns into
MUST/MUSC records; it carries the duration ffmpeg measured, because MUST.FLTV
is a real float in seconds the engine schedules against.

xWMAEncode's legal and native bitrate sets, the measured SNR ladder behind
`BITRATE_LADDER`, and the vanilla 48 kb/s calibration are recorded in
docs/commentary/asset_convert_audio.md#bitrate-native-rates-only-scaled.
"""
from asset_convert.game_paths import current_namespace
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core.subprocess_flags import POPEN_FLAGS, windows_cmd, to_wine_path
from core.worker_budget import worker_count
from output_layout import asset_root as _asset_root, plugin_out_root

from asset_convert.audio.audio_converter import find_ffmpeg, find_xwmaencode
from asset_convert import paths
from asset_convert.sources import output_files

_DEFAULT_EXPORT = paths.EXPORT

BS = chr(92)

#: Convertible source extensions; an .xwm source is re-encoded to normalize its bitrate.
MUSIC_SRC_EXTS = ('.mp3', '.wav', '.xwm')

#: The only rates xWMAEncode accepts; anything else fails XWMA_E_UNSUPPORTED_BITRATE.
XWMA_BITRATES = (20000, 32000, 48000, 64000, 96000, 160000, 192000)

#: Of those, the subset NATIVE at 44.1 kHz stereo -- 64000/160000 silently resample to 48 kHz.
NATIVE_44K_STEREO = (32000, 48000, 96000, 192000)

#: The subset native at 44.1 kHz mono.
NATIVE_44K_MONO = (32000, 48000)

#: (source kb/s <=, target bits/sec): the target tracks the source, whose quality is the ceiling.
BITRATE_LADDER = (
    (64,   32000),
    (128,  48000),
    (224,  96000),
    (10 ** 9, 192000),
)

#: Used when the source bitrate is undeterminable: mid-ladder, native, double vanilla's 48k.
MUSIC_BITRATE_DEFAULT = 96000


def pick_bitrate(src_kbps, channels: int = 2) -> int:
    """Native xWMA bitrate for a source of `src_kbps`, as bits/sec.

    Always returns a rate native at 44.1 kHz for the given channel count --
    convert_music_file normalizes every input to 44.1 kHz before the encoder
    sees it -- clamping to the top of that set when the ladder overshoots it
    (mono tops out at 48000).

    See: docs/commentary/asset_convert_audio.md#bitrate-native-rates-only-scaled
    """
    allowed = NATIVE_44K_MONO if channels == 1 else NATIVE_44K_STEREO
    if not src_kbps:
        target = MUSIC_BITRATE_DEFAULT
    else:
        target = next(t for cap, t in BITRATE_LADDER if src_kbps <= cap)
    return target if target in allowed else max(allowed)

MANIFEST_NAME = 'music_tracks.json'


def _out_root(output_dir, source_name, extract_dir=None):
    return plugin_out_root(output_dir, source_name,
                           str(extract_dir or _DEFAULT_EXPORT))


_AUDIO_RE = re.compile(r"Audio:.*?, (\d+) Hz, (\w+),.*?(\d+) kb/s")


def probe_audio(ffmpeg: str, path) -> dict:
    """{'duration', 'kbps', 'channels'} for `path`; zeros when undeterminable.

    ONE ffmpeg invocation for all three: duration feeds MUST.FLTV and the
    bitrate/channels pick the encode rate, and probing twice per file would
    double the cost of the stage for no gain.

    Uses ffmpeg rather than ffprobe: ffprobe is not guaranteed to sit beside the
    bundled ffmpeg, and everything needed is on ffmpeg's stderr banner.
    """
    out = {'duration': 0.0, 'kbps': 0, 'channels': 2}
    try:
        r = subprocess.run([ffmpeg, '-i', str(path)], capture_output=True,
                           timeout=60, **POPEN_FLAGS)
    except (subprocess.TimeoutExpired, OSError):
        return out
    err = (r.stderr or b'').decode('utf-8', 'replace')

    for line in err.splitlines():
        line = line.strip()
        if line.startswith('Duration:'):
            stamp = line.split('Duration:', 1)[1].split(',', 1)[0].strip()
            try:
                hh, mm, ss = stamp.split(':')
                out['duration'] = int(hh) * 3600 + int(mm) * 60 + float(ss)
            except ValueError:
                pass
        m = _AUDIO_RE.search(line)
        if m:
            out['kbps'] = int(m.group(3))
            out['channels'] = 1 if m.group(2) == 'mono' else 2
    return out


def convert_music_file(src_path, dst_path, ffmpeg: str,
                       xwmaencode: 'str | None', bitrate: int = None) -> bool:
    """Encode one music file to xWMA, preserving stereo.

    Mirrors convert_file_to_xwm's two-stage shape (ffmpeg -> PCM wav ->
    xWMAEncode -> xwm) but keeps 2 channels at 44.1 kHz.

    `bitrate` is bits/sec and MUST be native at 44.1 kHz (see pick_bitrate);
    None falls back to the middle of the ladder.
    """
    src_path, dst_path = Path(src_path), Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if not xwmaencode:
        return False

    tmp_dir = Path(tempfile.mkdtemp(prefix='music_'))
    wav_path = tmp_dir / 'track.wav'
    xwm_path = tmp_dir / 'track.xwm'
    try:
        cmd_wav = [
            ffmpeg, '-y', '-i', str(src_path),
            '-ac', '2',            # STEREO -- music, not voice
            '-ar', '44100',
            '-c:a', 'pcm_s16le',
            str(wav_path),
        ]
        r1 = subprocess.run(cmd_wav, capture_output=True, timeout=300,
                            **POPEN_FLAGS)
        if r1.returncode != 0 or not wav_path.is_file():
            return False

        # xWMAEncode parses a leading '/' as a switch prefix (same bug as
        # hkxcmd), so paths go through to_wine_path exactly as the voice path.
        cmd_xwm = [
            xwmaencode, '-b', str(bitrate or MUSIC_BITRATE_DEFAULT),
            to_wine_path(str(wav_path)), to_wine_path(str(xwm_path)),
        ]
        r2 = subprocess.run(windows_cmd(cmd_xwm), capture_output=True,
                            timeout=300, **POPEN_FLAGS)
        if (r2.returncode != 0 or not xwm_path.is_file()
                or not xwm_path.stat().st_size):
            return False

        shutil.copyfile(xwm_path, dst_path)
        return dst_path.is_file() and dst_path.stat().st_size > 0
    except (subprocess.TimeoutExpired, OSError):
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def music_rel_dir(source_name: str) -> str:
    """Output-relative folder holding this plugin's tracks.

    Scoped per plugin because the category folder names collide: Oblivion and
    Nehrim both have Explore/, Dungeon/, Public/, Battle/, Special/.
    """
    return 'music/' + current_namespace() + '/' + source_name


def track_entry(rel, source_name, duration=0.0, src_kbps=0, bitrate=0):
    """One manifest entry for a source file at `rel` under the music root.

    Everything the IMPORTER reads is derived from the PATH alone; the numeric
    fields are encode-side and stay 0 in a scan-only manifest.
    """
    parts = rel.as_posix().split('/')
    game_rel = rel.with_suffix('.xwm').as_posix().replace('/', BS)
    return {
        # Category = the TOP folder, the authored unit of meaning in TES4
        # (Battle/Dungeon/Explore/Public/Special).
        'category': parts[0] if len(parts) > 1 else '',
        # Source path as the plugin's own scripts spell it, so a StreamMusic
        # "data\\music\\special\\x.mp3" can be resolved back.
        'source_rel': ('music/' + rel.as_posix()).lower(),
        'game_path': (BS.join(['Data', 'Music', current_namespace(),
                               source_name]) + BS + game_rel),
        'duration': round(duration, 3),
        'stem': rel.stem,
        'source_kbps': src_kbps,
        'bitrate': bitrate,
    }


def _music_sources(src_root):
    """Every convertible source under `src_root`, relative to it, sorted."""
    rels = []
    for root, _dirs, files in os.walk(src_root):
        for fname in sorted(files):
            src = Path(root) / fname
            if src.suffix.lower() in MUSIC_SRC_EXTS:
                rels.append(src.relative_to(src_root))
    return rels


def write_music_manifest(out_root, source_name, tracks) -> int:
    """Write music_tracks.json for `tracks`; returns the track count."""
    tracks.sort(key=lambda t: t['source_rel'])
    Path(out_root).mkdir(parents=True, exist_ok=True)
    payload = {'plugin': source_name, 'tracks': tracks}
    with open(Path(out_root) / MANIFEST_NAME, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    return len(tracks)


def scan_music(
    source_file: str,
    extract_dir: str = 'export',
    output_dir: str = 'output',
) -> int:
    """Write music_tracks.json from the extracted tree WITHOUT encoding.

    Everything the importer reads -- source_rel, category, stem, game_path --
    comes from the directory walk, so this needs no ffmpeg, no xWMAEncode and
    no subprocess.  It exists because the stage order is import (6) then
    sounds (7) and music rides the SOUND stage: without this the first run of
    a plugin builds zero MUST/MUSC, and the audio ships with nothing pointing
    at it.  Called by tes5_import.record_types.music.load_music_manifest.

    Returns the number of tracks written (0 when the plugin has no music).
    """
    source_name = _asset_root(extract_dir, source_file).name
    src_root = Path(_asset_root(extract_dir, source_file)) / 'music'
    out_root = Path(_out_root(output_dir, source_file, extract_dir))
    if not src_root.is_dir():
        return 0
    rels = _music_sources(src_root)
    if not rels:
        return 0

    # Two kinds of entry need a real duration in the record:
    #   * a SILENT track carries no ANAM, so MUST.FLTV is the only thing
    #     telling the engine how long the gap lasts;
    #   * a BATTLE track's FNAM cue points tile its length, and without them
    #     the combat MUSC is selected but never engages.
    # Everything else takes its length from its own file at playback, so this
    # probes ~9 files rather than all 76.
    needs_duration = [r for r in rels
                      if 'silence' in r.stem.lower()
                      or r.as_posix().split('/')[0].lower() == 'battle']
    durations = {}
    if needs_duration:
        ffmpeg = find_ffmpeg(None)
        if ffmpeg:
            for r in needs_duration:
                durations[r] = probe_audio(ffmpeg, src_root / r)['duration']
        else:
            print('  WARNING: ffmpeg not found; silent and combat music '
                  'tracks will get no duration (run --sounds-only to write '
                  'the real values).')

    tracks = [track_entry(rel, source_name, duration=durations.get(rel, 0.0))
              for rel in rels]
    return write_music_manifest(out_root, source_name, tracks)


def convert_music(
    source_file: str,
    extract_dir: str = 'export',
    output_dir: str = 'output',
    ffmpeg_path: str = None,
    force: bool = False,
) -> dict:
    """Convert every extracted music file and write the track manifest.

    Returns a stats dict; the manifest lands in the plugin's output root as
    `music_tracks.json` for the importer to build MUST/MUSC from.  The import
    stage can also write that manifest itself via `scan_music`; this rewrites
    it with the measured encode data.
    """
    source_name = _asset_root(extract_dir, source_file).name
    src_root = Path(_asset_root(extract_dir, source_file)) / 'music'
    out_root = Path(_out_root(output_dir, source_file, extract_dir))
    dst_root = out_root / music_rel_dir(source_name)

    stats = {'converted': 0, 'cached': 0, 'failed': 0, 'tracks': 0}
    if not src_root.is_dir():
        print('  No extracted music for this plugin.')
        return stats

    ffmpeg = find_ffmpeg(ffmpeg_path)
    xwmaencode = find_xwmaencode()
    if not ffmpeg or not xwmaencode:
        print('  ERROR: music needs ffmpeg (%s) and xWMAEncode (%s); skipping.'
              % (bool(ffmpeg), bool(xwmaencode)))
        stats['failed'] = 1
        return stats

    jobs = [(src_root / rel, dst_root / rel.with_suffix('.xwm'), rel)
            for rel in _music_sources(src_root)]

    if not jobs:
        print('  No music files to convert.')
        return stats

    print('  Converting %d music files to xWMA '
          '(stereo 44.1 kHz, bitrate scaled to each source)...' % len(jobs))

    def _one(job):
        src, dst, rel = job
        # One probe per file: the bitrate/channels choose the encode rate, and
        # the duration is recorded for diagnostics (no record reads it).
        info = probe_audio(ffmpeg, src)
        rate = pick_bitrate(info['kbps'], info['channels'])
        cached = (output_files.signature(dst) or [0])[0] > 0 and not force
        ok = True if cached else convert_music_file(src, dst, ffmpeg,
                                                    xwmaencode, rate)
        return src, dst, rel, ok, cached, info['duration'], rate, info['kbps']

    tracks = []
    with ThreadPoolExecutor(max_workers=worker_count()) as pool:
        futs = [pool.submit(_one, j) for j in jobs]
        for fut in as_completed(futs):
            src, dst, rel, ok, cached, dur, rate, src_kbps = fut.result()
            if not ok:
                stats['failed'] += 1
                print('    FAILED ' + rel.as_posix())
                continue
            stats['cached' if cached else 'converted'] += 1
            tracks.append(track_entry(rel, source_name, duration=dur,
                                       src_kbps=src_kbps, bitrate=rate))

    stats['tracks'] = write_music_manifest(out_root, source_name, tracks)

    import collections
    spread = collections.Counter(t['bitrate'] // 1000 for t in tracks)
    if spread:
        print('    bitrates: ' + ', '.join(
            '%d kbps x%d' % (k, n) for k, n in sorted(spread.items())))
    return stats
