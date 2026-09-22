"""Audio conversion: MP3/WAV → XWM/FUZ (Skyrim voice formats) + lip sync.

Pipeline per voice line:
  1. ffmpeg: MP3 → WAV (PCM, mono, 44100 Hz)
  2. LipGenerator.exe (ships with the SSE Creation Kit): WAV + transcript
     → .lip FaceFX sync track. The transcript comes from the importer's
     `<esm>.liptext.txt` (INFO response text keyed by fid24 + response num).
  3. xWMAEncode.exe: WAV → XWM (proper Microsoft xWMA format)
  4. lip + xwm packed into a .fuz container — SSE only reads lip data from
     .fuz, loose .lip files are ignored. Lines with no transcript stay .xwm.

Handles two operations:
  convert_sounds()       – Parallel batch conversion of all extracted sounds.
  organize_voice_files() – Reorganise TES4 voice files to TES5 directory layout.

xWMAEncode.exe is a Microsoft DirectX SDK utility. It must be placed in
external/xwmaencode/ or on PATH. See README for download instructions.
LipGenerator.exe is auto-detected from the SSE install
(Tools/LipGen/LipGenerator/) — it must sit next to its FonixData.cdf.

All conversion is multithreaded: one worker per file in ThreadPoolExecutor.
Each job gets its own temp directory because LipGenerator writes a
tmp16khz.wav scratch file into its working directory. Lip generation
additionally runs against a pool of mutex-renamed LipGenerator copies
(build_lipgen_pool) — the stock exe serializes ALL instances machine-wide
on a named Fonix mutex, capping throughput at ~8 lips/s regardless of
process count.
"""
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core.subprocess_flags import POPEN_FLAGS, windows_cmd, to_wine_path
from core.worker_budget import worker_count


from output_layout import (asset_root as _asset_root, plugin_out_root,
                           record_dir)
from asset_convert.game_paths import namespace_for
from asset_convert import paths

#: Assumed when a caller omits extract_dir; the source registry lives here.
_DEFAULT_EXPORT = paths.EXPORT


def _out_root(output_dir, source_name, extract_dir=None):
    """This plugin's output folder (shared-folder rules: output_layout)."""
    return plugin_out_root(output_dir, source_name,
                           str(extract_dir or _DEFAULT_EXPORT))


# Use most CPUs – wmav2 is fast so many parallel ffmpeg processes help.
_WORKER_COUNT = worker_count()

# Voice batches with lip sync use more threads than CPUs: each job spends
# most of its wall time waiting on the LipGenerator subprocess (~0.3 s of
# mostly-idle wait), so the CPU-bound ffmpeg/xWMAEncode stages of other jobs
# fill the gaps.
_LIP_WORKER_COUNT = max(_WORKER_COUNT, min(64, _WORKER_COUNT * 2))

# ---------------------------------------------------------------------------
# Tool detection + single-file conversion
# ---------------------------------------------------------------------------

def find_ffmpeg(ffmpeg_path: str = 'ffmpeg',
                need_decoder: str = '') -> 'str | None':
    """Return the ffmpeg executable path if found, else None.

    An explicit `ffmpeg_path` is used alone; otherwise the bundled build
    is preferred over PATH. `need_decoder` skips a candidate that cannot
    decode that codec.

    See: docs/commentary/asset_convert_audio.md#which-ffmpeg-a-run-uses
    """
    if ffmpeg_path and ffmpeg_path != 'ffmpeg':
        candidates = [ffmpeg_path]
    else:
        candidates = []
        bundled = paths.EXTERNAL / 'ffmpeg' / 'ffmpeg.exe'
        if bundled.is_file():
            candidates.append(str(bundled))
        candidates.append('ffmpeg')

    for cand in candidates:
        try:
            r = subprocess.run(
                [cand, '-version'],
                capture_output=True,
                timeout=10,
                **POPEN_FLAGS,
            )
            if b'ffmpeg version' not in r.stdout                     and b'ffmpeg version' not in r.stderr:
                continue
            if need_decoder and not _ffmpeg_has_decoder(cand, need_decoder):
                continue
            return cand
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def _ffmpeg_has_decoder(ffmpeg: str, decoder: str) -> bool:
    """True when this ffmpeg build can decode *decoder*."""
    try:
        r = subprocess.run([ffmpeg, '-hide_banner', '-decoders'],
                           capture_output=True, timeout=10, **POPEN_FLAGS)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    out = (r.stdout or b'') + (r.stderr or b'')
    return any(line.split()[1:2] == [decoder.encode()]
               for line in out.splitlines() if line.strip())


def find_xwmaencode(search_dir: 'str | None' = None) -> 'str | None':
    """Return the xWMAEncode.exe path if found, else None.

    Search order:
      1. Explicit search_dir (if provided)
      2. external/xwmaencode/ under the project root
      3. System PATH
    """
    candidates = []
    if search_dir:
        candidates.append(Path(search_dir) / 'xWMAEncode.exe')
    candidates.append(paths.EXTERNAL / 'xwmaencode' / 'xWMAEncode.exe')

    for cand in candidates:
        if cand.is_file():
            return str(cand)

    # Try PATH
    try:
        r = subprocess.run(
            ['xWMAEncode'],
            capture_output=True,
            timeout=5,
            **POPEN_FLAGS,
        )
        if b'xWMA Encoding Tool' in r.stdout or b'xWMA Encoding Tool' in r.stderr:
            return 'xWMAEncode'
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def find_lipgenerator(search_dir: 'str | None' = None) -> 'str | None':
    """Return the path to LipGenerator.exe (SSE Creation Kit lip-sync tool).

    Search order:
      1. Explicit search_dir (if provided)
      2. external/lipgen/ under the project root
      3. <SSE install>/Tools/LipGen/LipGenerator/ (via registry)

    The exe reads FonixData.cdf from its own directory, so it must be found
    in place (or copied together with the .cdf).
    """
    candidates = []
    if search_dir:
        candidates.append(Path(search_dir) / 'LipGenerator.exe')
    candidates.append(paths.EXTERNAL / 'lipgen' / 'LipGenerator.exe')
    try:
        import winreg
        for subkey in (r'SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition',
                       r'SOFTWARE\Bethesda Softworks\Skyrim Special Edition'):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
                    install, _ = winreg.QueryValueEx(key, 'Installed Path')
                candidates.append(Path(install) / 'Tools' / 'LipGen'
                                  / 'LipGenerator' / 'LipGenerator.exe')
            except (FileNotFoundError, OSError):
                continue
    except ImportError:
        pass
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


#: Renamed per worker. See: docs/commentary/asset_convert_audio.md#lipgenerator-fonix-mutex
FONIX_MUTEX_NAME = b'FonixMemoryMutex'


def build_lipgen_pool(lipgenerator: str, pool_dir, count: int) -> 'list[str]':
    """Create *count* copies of LipGenerator.exe with unique Fonix mutex names.

    Each copy lands in its own subdirectory of *pool_dir* with FonixData.cdf
    hard-linked (or copied) beside it, since the exe loads the .cdf from its
    own directory. Returns the list of patched exe paths.

    Falls back to ``[lipgenerator]`` (the stock, machine-serialized exe) if
    the mutex name is not found exactly once in the binary — an unknown exe
    version is left untouched rather than patched blind.
    """
    src_exe = Path(lipgenerator)
    exe_bytes = src_exe.read_bytes()
    idx = exe_bytes.find(FONIX_MUTEX_NAME)
    if idx < 0 or exe_bytes.find(FONIX_MUTEX_NAME, idx + 1) >= 0:
        return [lipgenerator]
    src_cdf = src_exe.parent / 'FonixData.cdf'
    if not src_cdf.is_file():
        return [lipgenerator]

    pool_dir = Path(pool_dir)
    exes = []
    for i in range(count):
        d = pool_dir / f'lg{i:03d}'
        d.mkdir(parents=True, exist_ok=True)
        new_name = b'FonixMemMtx_%04d' % i
        assert len(new_name) == len(FONIX_MUTEX_NAME)
        exe = d / 'LipGenerator.exe'
        exe.write_bytes(exe_bytes[:idx] + new_name
                        + exe_bytes[idx + len(FONIX_MUTEX_NAME):])
        cdf = d / 'FonixData.cdf'
        if not cdf.exists():
            try:
                os.link(src_cdf, cdf)
            except OSError:
                shutil.copyfile(src_cdf, cdf)
        exes.append(str(exe))
    return exes


def generate_lip(lipgenerator: str, wav_path, text: str,
                 timeout: int = 120) -> 'bytes | None':
    """Run LipGenerator on a WAV + transcript; return the .lip bytes or None.

    LipGenerator resamples internally (writes tmp16khz.wav into its CWD), so
    the process cwd is set to the WAV's own directory — callers must give
    each parallel job a private directory. Output is <wav basename>.lip
    next to the input.
    """
    wav_path = Path(wav_path)
    # The transcript is a single command-line argument; newlines/tabs never
    # help phoneme alignment and double quotes break list2cmdline round-trip.
    clean = ' '.join(text.replace('"', "'").split())
    if not clean:
        return None
    try:
        r = subprocess.run(
            windows_cmd([lipgenerator, wav_path.name, clean]),
            cwd=str(wav_path.parent),
            capture_output=True, timeout=timeout,
            **POPEN_FLAGS,
        )
        if r.returncode != 0:
            return None
        lip_path = wav_path.with_suffix('.lip')
        if lip_path.is_file() and lip_path.stat().st_size > 0:
            return lip_path.read_bytes()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def pack_fuz(lip_bytes: bytes, audio_bytes: bytes) -> bytes:
    """Pack lip-sync data + xWMA audio into a Skyrim .fuz container.

    Layout: 'FUZE' magic, u32 version (1), u32 lip size, lip data, audio.
    """
    return (b'FUZE' + struct.pack('<II', 1, len(lip_bytes))
            + lip_bytes + audio_bytes)


def convert_file_to_xwm(src_path, dst_path, ffmpeg: str,
                         xwmaencode: 'str | None' = None,
                         lipgenerator: 'str | None' = None,
                         lip_text: 'str | None' = None) -> bool:
    """Convert a single audio file to XWM — or, with a transcript, to FUZ.

    Stages (xWMAEncode required; there is no ASF fallback):
      1. ffmpeg: source → WAV (PCM mono 44100 Hz) in a private temp dir
      2. LipGenerator: WAV + lip_text → .lip  (only when dst is .fuz)
      3. xWMAEncode: WAV → XWM
      4. dst .fuz: FUZE container (lip + xwm); dst .xwm: the xwm itself.
         If lip generation fails, the audio is preserved as .xwm next to
         the intended .fuz.

    Args:
        src_path:      Source audio (.mp3/.wav or any ffmpeg-readable format).
        dst_path:      Destination path (.xwm, or .fuz for voice+lip).
        ffmpeg:        Path to the ffmpeg executable.
        xwmaencode:    Path to xWMAEncode.exe (None = failure).
        lipgenerator:  Path to LipGenerator.exe (needed for .fuz output).
        lip_text:      Spoken transcript for lip sync (needed for .fuz).

    Returns:
        True if a non-empty output file was produced, False on any failure.
    """
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    if not xwmaencode:
        # xWMAEncode not available — cannot produce proper xWMA
        return False

    # Private temp dir per job: LipGenerator writes tmp16khz.wav into its CWD.
    tmp_dir = Path(tempfile.mkdtemp(prefix='voice_'))
    wav_path = tmp_dir / 'voice.wav'
    xwm_path = tmp_dir / 'voice.xwm'
    try:
        # Stage 1: ffmpeg → WAV
        cmd_wav = [
            ffmpeg,
            '-y',
            '-i', str(src_path),
            '-ac', '1',             # mono
            '-ar', '44100',         # 44.1 kHz
            '-c:a', 'pcm_s16le',   # 16-bit PCM
            str(wav_path),
        ]
        r1 = subprocess.run(cmd_wav, capture_output=True, timeout=60,
                            **POPEN_FLAGS)
        if r1.returncode != 0 or not wav_path.is_file():
            return False

        # Stage 2: lip sync track (only meaningful for .fuz destinations)
        lip_bytes = None
        if dst_path.suffix.lower() == '.fuz' and lipgenerator and lip_text:
            lip_bytes = generate_lip(lipgenerator, wav_path, lip_text)

        # Stage 3: xWMAEncode → XWM
        # xWMAEncode parses its own argv and treats a leading '/' as a switch
        # prefix (same bug as hkxcmd, verified under Wine 11.0) -- to_wine_path
        # no-ops on Windows and on the relative names above.
        cmd_xwm = [
            xwmaencode,
            '-b', '48000',          # 48 kbps (good balance for voice)
            to_wine_path(str(wav_path)),
            to_wine_path(str(xwm_path)),
        ]
        r2 = subprocess.run(windows_cmd(cmd_xwm), capture_output=True, timeout=60,
                            **POPEN_FLAGS)
        if (r2.returncode != 0 or not xwm_path.is_file()
                or xwm_path.stat().st_size == 0):
            return False

        # Stage 4: write destination
        if dst_path.suffix.lower() == '.fuz':
            if lip_bytes:
                dst_path.write_bytes(pack_fuz(lip_bytes, xwm_path.read_bytes()))
                # A pre-lip-sync run may have left the same line as .xwm;
                # remove it so the engine unambiguously picks the .fuz.
                stale = dst_path.with_suffix('.xwm')
                if stale.exists():
                    stale.unlink()
            else:
                # No lip track — keep the audio playable as a bare .xwm
                dst_path = dst_path.with_suffix('.xwm')
                shutil.copyfile(xwm_path, dst_path)
        else:
            shutil.copyfile(xwm_path, dst_path)
        return dst_path.is_file() and dst_path.stat().st_size > 0
    except (subprocess.TimeoutExpired, OSError):
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Batch sound conversion
# ---------------------------------------------------------------------------

def convert_sounds(
    source_file: str,
    extract_dir: str = 'export',
    output_dir: str = 'output',
    ffmpeg_path: str = 'ffmpeg',
    formid_index: int = 1,
) -> dict:
    """Convert all extracted sounds (MP3/WAV → XWM) with multi-threaded ffmpeg.

    Handles two distinct subtrees under ``extract_dir/<source_name>/sound/``:

    * ``sound/Voice/`` — reorganised into TES5 voice layout via
      :func:`organize_voice_files` (race/gender folders → VoiceType folders,
      FormIDs shifted by *formid_index*).
    * Everything else — ENCODED to xWMA under
      ``output/<source_name>/sound/tes4/``. SSE has no MP3 support (no '.mp3'
      string in the exe) and does not play raw PCM .wav for actor sounds; the
      SNDR record still names .wav, exactly as vanilla does, and the engine
      resolves it to the .xwm on disk.

    Args:
        source_file:   Plugin filename (e.g. 'Oblivion.esm').
        extract_dir:   Root extraction directory (default: export).
        output_dir:    Final output root (default: output).
        ffmpeg_path:   Path to ffmpeg executable (default: 'ffmpeg' from PATH).
        formid_index:  Load-order index byte for this plugin (default 1 —
                       Oblivion.esm is index 1 when Skyrim.esm is master 0).

    Returns:
        dict with keys: converted, copied, failed, total.
    """
    extract_dir = Path(extract_dir)
    output_dir  = Path(output_dir)
    source_name = Path(source_file).name

    print('\n' + '=' * 60)
    print('Sound Conversion')
    print('=' * 60)

    snd_src = _asset_root(extract_dir, source_name) / 'sound'
    if not snd_src.exists():
        print(f'  No sound directory found at {snd_src}')
        return {'converted': 0, 'copied': 0, 'failed': 0, 'total': 0}

    snd_dst = (_out_root(output_dir, source_name, extract_dir) / 'sound'
               / namespace_for(record_dir(extract_dir, source_name)))
    ffmpeg    = find_ffmpeg(ffmpeg_path)

    # ── Voice files: reorganise into TES5 layout ────────────────────────────
    print('\n  [Voice files]')
    voice_stats = organize_voice_files(
        source_dir=_asset_root(extract_dir, source_name),
        dest_dir=_out_root(output_dir, source_name, extract_dir),
        plugin_name=source_name,
        copy=True,
        convert_audio=(ffmpeg is not None),
        ffmpeg_path=ffmpeg_path,
        formid_index=formid_index,
        voice_map=find_voice_map(output_dir, source_name),
        lip_text=find_lip_text(output_dir, source_name),
    )

    # ── Non-voice sounds: keep .wav, transcode only .mp3 ────────────────────
    # Non-voice (actor/effect) sounds are PCM .wav in BOTH games, so the file
    # extension must survive the copy unchanged.
    #
    # An earlier version encoded these to xWMA on the theory that "SSE only
    # plays xWMA, and vanilla ANAMs name .wav because the engine substitutes
    # the extension". Both halves are wrong, and together they made every
    # creature silent:
    #   * Vanilla ships real PCM .wav for these. The cached vanilla asset
    #     sound/fx/npc/bear/npc_bear_idlerooting_01.wav is RIFF/WAVE with
    #     wFormatTag=0x1 (PCM, 32 kHz mono) — not xWMA. Vanilla ANAM names
    #     .wav because a .wav is genuinely there.
    #   * No extension substitution exists. The only exe code touching the
    #     ".wav"/".xwm"/".fuz" string trio (0x140512485, GOG build) is the
    #     sound\ / data\sound\ PATH-PREFIX helper; the other .xwm strings are
    #     music paths and the BSA archive-type table. Nothing rewrites a .wav
    #     reference into .xwm.
    # So renaming the files to .xwm left all ~2000 SNDR ANAMs pointing at
    # paths with no file behind them. Only VOICE lines are xWMA/.fuz (that
    # path is separate, above, and keeps its own encoding).
    #
    # MP3 still has to go: the SSE exe contains no '.mp3' string at all. Those
    # transcode to PCM .wav, which is what vanilla would have shipped.
    print('\n  [Non-voice sounds]')
    jobs = []
    for root_dir, dirs, files in os.walk(snd_src):
        # Skip the Voice subtree — already handled by organize_voice_files above
        if Path(root_dir).resolve() == snd_src.resolve():
            dirs[:] = [d for d in dirs if d.lower() != 'voice']
        for fname in files:
            src = Path(root_dir) / fname
            rel = src.relative_to(snd_src)
            if src.suffix.lower() == '.mp3':
                dst = snd_dst / rel.with_suffix('.wav')   # SSE cannot read mp3
            else:
                dst = snd_dst / rel                       # .wav/.xwm: as-is
            if dst.exists():
                continue
            jobs.append((src, dst))

    count = failed = copied = 0
    if not jobs:
        print('  No non-voice sound files to convert (all already present).')
    else:
        need_ffmpeg = any(s.suffix.lower() == '.mp3' for s, _ in jobs)
        if need_ffmpeg and not ffmpeg:
            print('  WARNING: ffmpeg not found — .mp3 sounds will be copied '
                  'unconverted and will NOT play in SSE.')

        def _encode(job):
            src, dst = job
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix.lower() != '.mp3':
                shutil.copy2(src, dst)
                return None
            if not ffmpeg:
                shutil.copy2(src, dst.with_suffix(src.suffix))
                return None
            # 16-bit PCM, matching the vanilla non-voice container.
            r = subprocess.run(
                [ffmpeg, '-y', '-loglevel', 'error', '-i', str(src),
                 '-acodec', 'pcm_s16le', str(dst)],
                capture_output=True, timeout=120, **POPEN_FLAGS)
            return r.returncode == 0 and dst.is_file() and dst.stat().st_size > 0

        # I/O- and subprocess-bound: threads are the right pool here.
        with ThreadPoolExecutor(max_workers=(os.cpu_count() or 4)) as pool:
            for ok in pool.map(_encode, jobs):
                if ok is None:
                    copied += 1
                elif ok:
                    count += 1
                else:
                    failed += 1
        print(f'  Copied {copied} sounds'
              + (f', transcoded {count} mp3 -> wav' if count else '')
              + (f', {failed} FAILED' if failed else ''))

    non_voice = copied + count
    total = voice_stats.get('organized', 0) + non_voice
    print(
        f'\n  Sound conversion complete: '
        f'{non_voice} non-voice | '
        f'{voice_stats.get("organized", 0)} voice organised to TES5 layout'
    )
    return {'converted': count, 'copied': total,
            'failed': voice_stats.get('errors', 0) + failed,
            'total': total}


# ---------------------------------------------------------------------------
# TES4 voice file organisation (TES4 layout → TES5 layout)
# ---------------------------------------------------------------------------

#: Oblivion voice filename. See: docs/commentary/asset_convert_audio.md#voice-file-naming-prefix
VOICE_FILENAME_RE = re.compile(
    r'^(.+)_([0-9a-fA-F]{8})_(\d+)\.(mp3|ogg|wav|xwm|fuz)$',
    re.IGNORECASE,
)


def load_voice_map(map_path) -> dict:
    """Load the importer's `<esm>.voicemap.txt`.

    Returns {info_fid24: (prefix, [target_vtyp_edids])}. The optional
    tab-separated VTYP list names the folder(s) the line's speaker resolves to
    when that differs from the Oblivion source race folder (e.g. Arvena Thelas
    is a Dark Elf but her recordings sit under high elf/f/). Empty list = keep
    the source race folder (generic lines are recorded per race, correctly)."""
    voice_map = {}
    with open(map_path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line or line.startswith('#') or '=' not in line:
                continue
            fid_hex, value = line.split('=', 1)
            if '\t' in value:
                prefix, vt = value.split('\t', 1)
                vtyps = [v for v in vt.split(',') if v]
            else:
                prefix, vtyps = value, []
            try:
                voice_map[int(fid_hex, 16) & 0xFFFFFF] = (prefix, vtyps)
            except ValueError:
                continue
    return voice_map


def find_voice_map(output_dir, source_name) -> 'dict | None':
    """Locate and load the voicemap written next to the converted ESM
    (output/<plugin>/<plugin>.voicemap.txt), if present."""
    map_path = (_out_root(output_dir, source_name)
                / (source_name + '.voicemap.txt'))
    if map_path.exists():
        return load_voice_map(map_path)
    return None


def load_lip_text(map_path) -> dict:
    """Load the importer's `<esm>.liptext.txt`.

    Returns {(info_fid24, resp_num): spoken text}. Lines are
    `<fid24 hex>_<resp_num>=<text>` with backslash escapes (\\ \n \r \t).
    """
    lip_text = {}
    with open(map_path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, text = line.split('=', 1)
            if '_' not in key:
                continue
            fid_hex, _, num = key.rpartition('_')
            try:
                fid24 = int(fid_hex, 16) & 0xFFFFFF
                resp_num = int(num)
            except ValueError:
                continue
            text = (text.replace('\\\\', '\x00').replace('\\n', '\n')
                    .replace('\\r', '\r').replace('\\t', '\t')
                    .replace('\x00', '\\'))
            lip_text[(fid24, resp_num)] = text
    return lip_text


def find_lip_text(output_dir, source_name) -> 'dict | None':
    """Locate and load the lip transcript map written next to the converted
    ESM (output/<plugin>/<plugin>.liptext.txt), if present."""
    map_path = (_out_root(output_dir, source_name)
                / (source_name + '.liptext.txt'))
    if map_path.exists():
        return load_lip_text(map_path)
    return None


from asset_convert.audio.audio_falloutnv import (folder_gender,
                                                  is_fallout_voice_root,
                                                  load_voice_type_edids,
                                                  voice_type_edid)
from asset_convert.audio.voice_races import (load_race_voices,
                                             voice_key,
                                             vtyp_edid as _vtyp_edid)

_VOICE_OUTPUT_EXTS = frozenset(('.fuz', '.xwm', '.wav', '.mp3', '.ogg', '.lip'))


def _voice_decoder(voice_root) -> str:
    """ffmpeg decoder this voice tree needs, or '' when plain MP3/WAV.

    See: docs/commentary/asset_convert_audio.md#which-ffmpeg-a-run-uses
    """
    for path in voice_root.rglob('*.ogg'):
        del path
        return 'vorbis'
    return ''


def _resolve_voice_type(race: str, gender: str, fallout: bool,
                        race_voices, unmapped_races: set,
                        fnv_edids: dict = None) -> str:
    """VTYP EditorID a source voice folder maps to.

    A FO3/FNV folder IS the voice type. Oblivion resolves the race through the
    RACE records the importer built its VTYPs from -- the plugin's own and its
    masters' -- falling back to a synthesised name recorded in *unmapped_races*.

    See: docs/commentary/asset_convert_audio.md#race-identity-spans-the-masters
    """
    if fallout:
        return voice_type_edid(race, fnv_edids)
    key = race_voices.folder_key(race)
    if key:
        return _vtyp_edid(key, gender)
    unmapped_races.add((race, gender))
    return _vtyp_edid(voice_key(race), gender)


def _voice_destination(m, voice_map, voice_type, lip_text, ffmpeg,
                       lipgenerator):
    """(dst_name, destination VTYPs, transcript) for one source filename.

    The prefix comes from the CONVERTED records via *voice_map*, keyed on the
    24-bit InfoFormID; a transcript yields lip-synced .fuz, otherwise .xwm.
    Oblivion holds ONE take per VOICE, so a multi-speaker line emits only into
    the VTYP this source folder speaks for -- all of them when none is its own.

    See: docs/commentary/asset_convert_audio.md#vnam-voice-routing
    """
    prefix, src_ext = m.group(1), m.group(4).lower()
    fid24 = int(m.group(2), 16) & 0xFFFFFF
    targets = []
    if voice_map and voice_map.get(fid24) is not None:
        prefix, targets = voice_map[fid24]
    text = None
    dst_ext = src_ext
    if ffmpeg and src_ext in ('mp3', 'ogg', 'wav'):
        if lipgenerator and lip_text:
            text = lip_text.get((fid24, int(m.group(3))))
        dst_ext = 'fuz' if text else 'xwm'
    dst_name = f'{prefix}_{fid24:08x}_{m.group(3)}.{dst_ext}'.lower()
    return dst_name, [v for v in targets if v == voice_type] or targets, text


def _voice_leaf_dirs(race_dir, fallout: bool) -> list:
    """Directories holding this voice folder's recordings."""
    if fallout:
        return [race_dir]
    return [d for d in race_dir.iterdir() if d.is_dir()]


def prune_stale_voice_files(touched_dirs: set, intended: set,
                             plugin_roots: 'set | None' = None) -> list:
    """Delete voice files this run did not intend to produce.

    `plugin_roots` are the `Sound/Voice/<plugin>` directories this run owns;
    their VTYP subfolders are swept too, since a voice-type relocation empties
    the old folder entirely. Only files with a voice extension are removed.
    See: docs/commentary/asset_convert_audio.md#pruning-stale-voice-output
    """
    sweep = set(touched_dirs)
    for root in (plugin_roots or ()):
        if root.is_dir():
            sweep.update(d.resolve() for d in root.iterdir() if d.is_dir())
    removed = []
    for d in sorted(sweep):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in _VOICE_OUTPUT_EXTS:
                continue
            if f.resolve() in intended:
                continue
            try:
                f.unlink()
                removed.append(f)
            except OSError as exc:
                print(f'  WARN could not remove stale {f.name}: {exc}')
    return removed


def _find_voice_tools(convert_audio, voice_root, ffmpeg_path,
                      xwmaencode_path, lipgenerator_path, lip_text):
    """(ffmpeg, xwmaencode, lipgenerator) for this run, each None when unused.

    Raises RuntimeError when conversion is requested but ffmpeg is absent.
    """
    if not convert_audio:
        return None, None, None
    decoder = _voice_decoder(voice_root)
    ffmpeg = find_ffmpeg(ffmpeg_path, need_decoder=decoder)
    if not ffmpeg:
        raise RuntimeError(
            f'no ffmpeg with the {decoder or "mp3"} decoder: '
            'external/ffmpeg/ffmpeg.exe is missing or stale -- rebuild it '
            'with python tools/generators/build_ffmpeg.py')
    xwmaencode = xwmaencode_path or find_xwmaencode()
    if xwmaencode:
        print('  ffmpeg + xWMAEncode found -- converting MP3 -> WAV -> XWM '
              '(proper xWMA)')
    else:
        print('  WARNING: xWMAEncode.exe not found -- falling back to ffmpeg '
              'ASF container')
        print('           Voice audio may not play in Skyrim! See README for '
              'xWMAEncode setup.')
    lipgenerator = None
    if lip_text:
        lipgenerator = lipgenerator_path or find_lipgenerator()
        if lipgenerator:
            print(f'  LipGenerator found -- generating .lip sync tracks, '
                  f'packing voice as .fuz ({len(lip_text)} transcripts)')
        else:
            print('  WARNING: LipGenerator.exe not found (SSE Tools/LipGen) '
                  '-- voice converts without lip sync (.xwm only)')
    return ffmpeg, xwmaencode, lipgenerator


def _lip_worker_pool(lipgenerator, conversion_jobs):
    """(n_workers, lip_pool, lip_pool_dir) for the transcode stage.

    Stock LipGenerator instances serialize machine-wide on a named Fonix mutex
    (~8 lips/s total), so each worker gets its own mutex-renamed copy.

    See: docs/commentary/asset_convert_audio.md#lipgenerator-fonix-mutex
    """
    if not (lipgenerator and any(job[2] for job in conversion_jobs)):
        return _WORKER_COUNT, None, None
    lip_pool_dir = Path(tempfile.mkdtemp(prefix='lipgen_pool_'))
    lip_exes = build_lipgen_pool(lipgenerator, lip_pool_dir, _LIP_WORKER_COUNT)
    lip_pool = queue.Queue()
    for exe in lip_exes:
        lip_pool.put(exe)
    print(f'  LipGenerator: {len(lip_exes)} parallel copies' if len(lip_exes) > 1
          else '  WARNING: unrecognised LipGenerator.exe layout -- running '
               'unpatched; lip generation serializes at ~8 lips/s')
    return _LIP_WORKER_COUNT, lip_pool, lip_pool_dir


def _convert_one(job, ffmpeg, xwmaencode, lipgenerator, lip_pool, copy):
    """Transcode or copy one (src, dst, text) job; 'ok'/'error'/'exception:..'."""
    src_path, dst_path, text = job
    try:
        if ffmpeg and dst_path.suffix in ('.xwm', '.fuz'):
            lip_exe = lipgenerator
            if text and lip_pool is not None:
                lip_exe = lip_pool.get()
            try:
                return 'ok' if convert_file_to_xwm(
                    src_path, dst_path, ffmpeg, xwmaencode=xwmaencode,
                    lipgenerator=lip_exe, lip_text=text) else 'error'
            finally:
                if text and lip_pool is not None:
                    lip_pool.put(lip_exe)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if copy:
            shutil.copy2(src_path, dst_path)
        else:
            shutil.move(str(src_path), dst_path)
        return 'ok'
    except Exception as e:
        return f'exception:{e}'


def _run_conversion_jobs(conversion_jobs, stats, tools, copy):
    """Run every conversion job on a thread pool, accumulating into *stats*.

    *tools* is (ffmpeg, xwmaencode, lipgenerator); the LipGenerator pool is
    built here and torn down before returning.
    """
    ffmpeg, xwmaencode, lipgenerator = tools
    n_workers, lip_pool, lip_pool_dir = _lip_worker_pool(lipgenerator,
                                                         conversion_jobs)
    print(f'  Processing {len(conversion_jobs)} voice files '
          f'({n_workers} workers)...')
    try:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_convert_one, job, ffmpeg, xwmaencode,
                                   lipgenerator, lip_pool, copy): job
                       for job in conversion_jobs}
            for fut in as_completed(futures):
                result = fut.result()
                stats['organized' if result == 'ok' else 'errors'] += 1
                if result != 'ok' and stats['errors'] <= 5:
                    detail = (result[10:] if result.startswith('exception:')
                              else f'ffmpeg failed on {futures[fut][0].name}')
                    print(f'    ERROR: {detail}')
    finally:
        if lip_pool_dir is not None:
            shutil.rmtree(lip_pool_dir, ignore_errors=True)


def organize_voice_files(
    source_dir,
    dest_dir,
    plugin_name=None,
    copy=True,
    convert_audio=True,
    ffmpeg_path='ffmpeg',
    formid_index: int = 1,
    xwmaencode_path: 'str | None' = None,
    voice_map: 'dict | str | None' = None,
    lip_text: 'dict | str | None' = None,
    lipgenerator_path: 'str | None' = None,
    prune: bool = True,
) -> dict:
    """Reorganise extracted TES4 voice files into the TES5 directory layout.

    `<source_dir>/sound/Voice/<plugin>/<Race>/<Gender>/<topic>_<infoFID>_<idx>.mp3`
    becomes `<dest_dir>/Sound/Voice/<plugin>/<VoiceType>/<infoFID_shifted>_<idx>.fuz`.

    `voice_map` and `lip_text` are dicts keyed on the 24-bit InfoFormID, or the
    paths of the importer's `<esm>.voicemap.txt` / `<esm>.liptext.txt`; each
    voicemap value is a bare prefix or a (prefix, [vtyps]) pair, normalized here
    to the pair form.  `ffmpeg_path`, `xwmaencode_path` and `lipgenerator_path`
    are auto-detected when None.  `copy=False` moves instead of copying.

    Returns a dict with keys: organized, skipped, no_match, errors,
    unmapped_races.

    See: docs/commentary/asset_convert_audio.md#voice-file-naming-prefix
    and docs/commentary/asset_convert_audio.md#pruning-stale-voice-output
    """
    source_dir = Path(source_dir)
    dest_dir   = Path(dest_dir)
    if isinstance(voice_map, (str, Path)):
        voice_map = load_voice_map(voice_map)
    if voice_map:
        voice_map = {k: (v if isinstance(v, tuple) else (v, []))
                     for k, v in voice_map.items()}
    if voice_map:
        print(f'  Voice map: {len(voice_map)} filename prefixes from importer')
    else:
        print('  WARNING: no voice map — keeping Oblivion filename prefixes; '
              'lines whose quest/topic EditorIDs were truncated differently '
              'will not play')
    if isinstance(lip_text, (str, Path)):
        lip_text = load_lip_text(lip_text)

    # BSA archives store every internal path lowercase (Windows' case-
    # insensitive filesystem never surfaced this); bsa_extract.py preserves
    # that casing verbatim, so the extracted folder is 'voice', not 'Voice'.
    voice_root = source_dir / 'sound' / 'voice'
    if not voice_root.exists():
        print(f'  Voice directory not found: {voice_root}')
        return {'organized': 0, 'skipped': 0, 'no_match': 0, 'errors': 0,
                'unmapped_races': set()}

    ffmpeg, xwmaencode, lipgenerator = _find_voice_tools(
        convert_audio, voice_root, ffmpeg_path, xwmaencode_path,
        lipgenerator_path, lip_text)

    stats = {'organized': 0, 'skipped': 0, 'no_match': 0, 'errors': 0}
    unmapped_races: set = set()

    race_voices = load_race_voices(source_dir)
    if race_voices:
        print(f'  Plugin races: {len(race_voices.keys)} voice identities '
              f'from {len(race_voices.by_race_edid)} RACE records')
    conversion_jobs: list[tuple] = []   # (src_path, dst_path)
    intended: set = set()
    touched_dirs: set = set()
    # `Sound/Voice/<plugin>` roots this run owns — swept wholesale by the
    # prune so a VTYP relocation cannot strand the emptied source-race folder.
    plugin_roots: set = set()

    for plugin_dir in voice_root.iterdir():
        if not plugin_dir.is_dir():
            continue
        effective_plugin = plugin_name or plugin_dir.name
        fallout = is_fallout_voice_root(plugin_dir)
        fnv_edids = load_voice_type_edids(source_dir) if fallout else {}

        for race_dir in plugin_dir.iterdir():
            if not race_dir.is_dir():
                continue
            race = race_dir.name

            for gender_dir in _voice_leaf_dirs(race_dir, fallout):
                gender = (folder_gender(race) if fallout
                          else gender_dir.name.upper()[:1])

                voice_type = _resolve_voice_type(race, gender, fallout,
                                                 race_voices, unmapped_races,
                                                 fnv_edids)

                out_dir = dest_dir / 'sound' / 'Voice' / effective_plugin / voice_type
                out_dir.mkdir(parents=True, exist_ok=True)
                plugin_roots.add(out_dir.parent)

                for audio_file in gender_dir.iterdir():
                    if not audio_file.is_file():
                        continue
                    m = VOICE_FILENAME_RE.match(audio_file.name)
                    if not m:
                        stats['no_match'] += 1
                        continue

                    dst_name, owned, text = _voice_destination(
                        m, voice_map, voice_type, lip_text, ffmpeg,
                        lipgenerator)
                    out_dirs = ([dest_dir / 'sound' / 'Voice' / effective_plugin
                                 / vt for vt in owned]
                                if owned else [out_dir])
                    for od in out_dirs:
                        od.mkdir(parents=True, exist_ok=True)
                        dst_path = od / dst_name
                        # Record it as intended BEFORE the already-present
                        # skip: an existing file is still a wanted file and
                        # must not be pruned below.
                        intended.add(dst_path.resolve())
                        touched_dirs.add(od.resolve())
                        if dst_path.exists():
                            stats['skipped'] += 1
                            continue
                        conversion_jobs.append((audio_file, dst_path, text))

    if not conversion_jobs:
        if unmapped_races:
            print('  Warning: unmapped race/gender combos:')
            for r, g in sorted(unmapped_races):
                print(f'    {r}/{g}')
        pruned = prune_stale_voice_files(touched_dirs, intended,
                                          plugin_roots) if prune else []
        if pruned:
            print(f'  Pruned {len(pruned)} stale voice file(s) under old names')
        print(f'  Voice files: 0 organised (all already present or no files found), '
              f'{stats["skipped"]} already present')
        return {**stats, 'pruned': len(pruned), 'unmapped_races': unmapped_races}

    _run_conversion_jobs(conversion_jobs, stats,
                         (ffmpeg, xwmaencode, lipgenerator), copy)

    if unmapped_races:
        print('  Warning: unmapped race/gender combos (synthesised folder names):')
        for r, g in sorted(unmapped_races):
            print(f'    {r}/{g}')

    pruned = prune_stale_voice_files(touched_dirs, intended,
                                      plugin_roots) if prune else []

    print(f'  Voice files: {stats["organized"]} organised, '
          f'{stats["skipped"]} already present, '
          f'{len(pruned)} stale pruned, '
          f'{stats["errors"]} errors, '
          f'{stats["no_match"]} unrecognised names')
    return {**stats, 'pruned': len(pruned), 'unmapped_races': unmapped_races}
