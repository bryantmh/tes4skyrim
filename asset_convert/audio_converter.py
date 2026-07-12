"""Audio conversion: MP3/WAV → XWM (Skyrim xWMA format).

Two-stage pipeline:
  1. ffmpeg: MP3 → WAV (PCM, mono, 44100 Hz)
  2. xWMAEncode.exe: WAV → XWM (proper Microsoft xWMA format)

Handles two operations:
  convert_sounds()       – Parallel batch conversion of all extracted sounds.
  organize_voice_files() – Reorganise TES4 voice files to TES5 directory layout.

xWMAEncode.exe is a Microsoft DirectX SDK utility. It must be placed in
external/xwmaencode/ or on PATH. See README for download instructions.

All conversion is multithreaded: one worker per file in ThreadPoolExecutor.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subprocess_flags import POPEN_FLAGS  # noqa: E402

# Use most CPUs – wmav2 is fast so many parallel ffmpeg processes help.
_WORKER_COUNT = max(1, (os.cpu_count() or 4) - 1)

# ---------------------------------------------------------------------------
# Tool detection + single-file conversion
# ---------------------------------------------------------------------------

def find_ffmpeg(ffmpeg_path: str = 'ffmpeg') -> 'str | None':
    """Return the ffmpeg executable path if found, else None."""
    try:
        r = subprocess.run(
            [ffmpeg_path, '-version'],
            capture_output=True,
            timeout=10,
            **POPEN_FLAGS,
        )
        if b'ffmpeg version' in r.stdout or b'ffmpeg version' in r.stderr:
            return ffmpeg_path
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


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
    # Project root = parent of this file's directory
    project_root = Path(__file__).resolve().parent.parent
    candidates.append(project_root / 'external' / 'xwmaencode' / 'xWMAEncode.exe')

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


def convert_file_to_xwm(src_path, dst_path, ffmpeg: str,
                         xwmaencode: 'str | None' = None) -> bool:
    """Convert a single audio file to XWM.

    Two-stage process (when xWMAEncode is available):
      1. ffmpeg: source → WAV (PCM mono 44100 Hz)
      2. xWMAEncode: WAV → XWM (proper Microsoft xWMA format)

    Fallback (xWMAEncode missing): ffmpeg wmav2 ASF container (may not play
    correctly in all Skyrim versions).

    Args:
        src_path:     Source audio file (.mp3, .wav, or any ffmpeg-readable format).
        dst_path:     Destination path (.xwm extension expected).
        ffmpeg:       Path to the ffmpeg executable.
        xwmaencode:   Path to xWMAEncode.exe (None = fallback to ffmpeg-only).

    Returns:
        True if conversion produced a non-empty .xwm file, False on any failure.
    """
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    if xwmaencode:
        # Two-stage: ffmpeg → WAV → xWMAEncode → XWM
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            wav_path = tmp.name
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
            if r1.returncode != 0 or not os.path.isfile(wav_path):
                return False

            # Stage 2: xWMAEncode → XWM
            cmd_xwm = [
                xwmaencode,
                '-b', '48000',          # 48 kbps (good balance for voice)
                str(wav_path),
                str(dst_path),
            ]
            r2 = subprocess.run(cmd_xwm, capture_output=True, timeout=60,
                                **POPEN_FLAGS)
            return (r2.returncode == 0
                    and dst_path.is_file()
                    and dst_path.stat().st_size > 0)
        except (subprocess.TimeoutExpired, OSError):
            return False
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
    else:
        # xWMAEncode not available — cannot produce proper xWMA
        return False


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
    * Everything else — copied as-is to ``output/<source_name>/sound/tes4/``
      (Skyrim SE plays MP3/WAV/XWM natively; no conversion needed).

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

    snd_src = extract_dir / source_name / 'sound'
    if not snd_src.exists():
        print(f'  No sound directory found at {snd_src}')
        return {'converted': 0, 'copied': 0, 'failed': 0, 'total': 0}

    snd_dst = output_dir / source_name / 'sound' / 'tes4'
    ffmpeg    = find_ffmpeg(ffmpeg_path)

    # ── Voice files: reorganise into TES5 layout ────────────────────────────
    print('\n  [Voice files]')
    voice_stats = organize_voice_files(
        source_dir=extract_dir / source_name,
        dest_dir=output_dir / source_name,
        plugin_name=source_name,
        copy=True,
        convert_audio=(ffmpeg is not None),
        ffmpeg_path=ffmpeg_path,
        formid_index=formid_index,
        voice_map=find_voice_map(output_dir, source_name),
    )

    # ── Non-voice sounds: copy as-is (Skyrim SE plays MP3/WAV/XWM natively) ──
    print('\n  [Non-voice sounds]')
    count = 0
    for root_dir, dirs, files in os.walk(snd_src):
        # Skip the Voice subtree — already handled by organize_voice_files above
        if Path(root_dir).resolve() == snd_src.resolve():
            dirs[:] = [d for d in dirs if d.lower() != 'voice']
        for fname in files:
            src = Path(root_dir) / fname
            dst = snd_dst / src.relative_to(snd_src)
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
    if count:
        print(f'  Copied {count} files -> {snd_dst}')
    else:
        print('  No non-voice sound files to copy (all already present or none found).')

    total = voice_stats.get('organized', 0) + count
    print(
        f'\n  Sound conversion complete: '
        f'{count} non-voice copied | '
        f'{voice_stats.get("organized", 0)} voice organised to TES5 layout'
    )
    return {'converted': 0, 'copied': total, 'failed': voice_stats.get('errors', 0),
            'total': total}


# ---------------------------------------------------------------------------
# TES4 voice file organisation (TES4 layout → TES5 layout)
# Moved here from bsa_extract.py — identical interface.
# ---------------------------------------------------------------------------

# TES4 Race folder + Gender ('M'/'F') → custom TES5 VoiceType EditorID.
# These match the VTYP records created by _create_vtyp_records() in import_main.py.
_TES4_VOICE_TYPE_MAP = {
    ('Argonian',     'M'): 'TES4MaleArgonian',
    ('Argonian',     'F'): 'TES4FemaleArgonian',
    ('Breton',       'M'): 'TES4MaleBreton',
    ('Breton',       'F'): 'TES4FemaleBreton',
    ('DarkElf',      'M'): 'TES4MaleDarkElf',
    ('DarkElf',      'F'): 'TES4FemaleDarkElf',
    ('HighElf',      'M'): 'TES4MaleHighElf',
    ('HighElf',      'F'): 'TES4FemaleHighElf',
    ('Imperial',     'M'): 'TES4MaleImperial',
    ('Imperial',     'F'): 'TES4FemaleImperial',
    ('Khajiit',      'M'): 'TES4MaleKhajiit',
    ('Khajiit',      'F'): 'TES4FemaleKhajiit',
    ('Nord',         'M'): 'TES4MaleNord',
    ('Nord',         'F'): 'TES4FemaleNord',
    ('Orc',          'M'): 'TES4MaleOrc',
    ('Orc',          'F'): 'TES4FemaleOrc',
    ('Redguard',     'M'): 'TES4MaleRedguard',
    ('Redguard',     'F'): 'TES4FemaleRedguard',
    ('WoodElf',      'M'): 'TES4MaleWoodElf',
    ('WoodElf',      'F'): 'TES4FemaleWoodElf',
    ('DarkSeducer',  'M'): 'TES4MaleDarkSeducer',
    ('DarkSeducer',  'F'): 'TES4FemaleDarkSeducer',
    ('GoldenSaint',  'M'): 'TES4MaleGoldenSaint',
    ('GoldenSaint',  'F'): 'TES4FemaleGoldenSaint',
    ('Sheogorath',   'M'): 'TES4MaleSheogorath',
    ('Dremora',      'M'): 'TES4MaleDremora',
    ('Dremora',      'F'): 'TES4FemaleDremora',
    # Alternate spellings found in Oblivion BSA folder names:
    ('high elf',     'M'): 'TES4MaleHighElf',
    ('high elf',     'F'): 'TES4FemaleHighElf',
    ('dark elf',     'M'): 'TES4MaleDarkElf',
    ('dark elf',     'F'): 'TES4FemaleDarkElf',
    ('wood elf',     'M'): 'TES4MaleWoodElf',
    ('wood elf',     'F'): 'TES4FemaleWoodElf',
    ('dark seducer', 'M'): 'TES4MaleDarkSeducer',
    ('dark seducer', 'F'): 'TES4FemaleDarkSeducer',
    ('golden saint', 'M'): 'TES4MaleGoldenSaint',
    ('golden saint', 'F'): 'TES4FemaleGoldenSaint',
    ('dremora',      'M'): 'TES4MaleDremora',
    ('dremora',      'F'): 'TES4FemaleDremora',
}

# Oblivion voice filename: <quest>_<topic>_<infoFID8hex>_<idx>.<ext>
# Skyrim resolves a voice file as <prefix>_<InfoFormID>_<RespNum>.<ext> where
# <prefix> is built at RUNTIME from the converted plugin's owning-quest and
# topic EditorIDs (with Skyrim's own truncation rules — see
# dialog_converter.voice_file_prefix). The Oblivion filename prefix uses
# Oblivion's truncation of the ORIGINAL quest, so it cannot be trusted;
# files are renamed via the voicemap emitted by the importer, keyed on the
# InfoFormID (24-bit value, load-order byte stripped).
_VOICE_FILENAME_RE = re.compile(
    r'^(.+)_([0-9a-fA-F]{8})_(\d+)\.(mp3|wav|xwm|fuz)$',
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
    map_path = Path(output_dir) / source_name / (source_name + '.voicemap.txt')
    if map_path.exists():
        return load_voice_map(map_path)
    return None


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
) -> dict:
    """Reorganise extracted TES4 voice files into TES5 directory layout.

    TES4 layout: <source_dir>/sound/Voice/<plugin>/<Race>/<Gender>/<topic>_<infoFID>_<idx>.mp3
    TES5 layout: <dest_dir>/Sound/Voice/<plugin>/<VoiceType>/<infoFID_shifted>_<idx>.xwm

    When ``convert_audio=True`` (default) each MP3/WAV is converted to XWM
    using a 2-stage pipeline: ffmpeg → WAV → xWMAEncode → XWM.
    Falls back to ffmpeg-only ASF if xWMAEncode is not available.

    Args:
        source_dir:       Root extracted asset directory (contains 'sound/' subfolder).
        dest_dir:         Root output directory for organised files.
        plugin_name:      Override the plugin folder name (auto-detected from BSA path).
        copy:             If True (default), copy files; if False, move source files.
        convert_audio:    Convert MP3/WAV → XWM (default True).
        ffmpeg_path:      Path to ffmpeg executable (default 'ffmpeg').
        formid_index:     Load-order index byte for the plugin (default 1).
        xwmaencode_path:  Path to xWMAEncode.exe (auto-detected if None).
        voice_map:        {info_fid24: prefix} dict or path to the importer's
                          `<esm>.voicemap.txt`. Files are RENAMED to the
                          mapped prefix so the runtime lookup (built from the
                          converted records' quest/topic EditorIDs) finds
                          them. Without it the Oblivion prefix is kept, which
                          only resolves when the EditorIDs and truncation
                          happen to match.

    Returns:
        dict with keys: organized, skipped, no_match, errors, unmapped_races.
    """
    source_dir = Path(source_dir)
    dest_dir   = Path(dest_dir)
    if isinstance(voice_map, (str, Path)):
        voice_map = load_voice_map(voice_map)
    # Normalise: values may be a bare prefix (str) or (prefix, [vtyps]).
    if voice_map:
        voice_map = {k: (v if isinstance(v, tuple) else (v, []))
                     for k, v in voice_map.items()}
    if voice_map:
        print(f'  Voice map: {len(voice_map)} filename prefixes from importer')
    else:
        print('  WARNING: no voice map — keeping Oblivion filename prefixes; '
              'lines whose quest/topic EditorIDs were truncated differently '
              'will not play')

    voice_root = source_dir / 'sound' / 'Voice'
    if not voice_root.exists():
        print(f'  Voice directory not found: {voice_root}')
        return {'organized': 0, 'skipped': 0, 'no_match': 0, 'errors': 0,
                'unmapped_races': set()}

    ffmpeg = None
    xwmaencode = None
    if convert_audio:
        ffmpeg = find_ffmpeg(ffmpeg_path)
        if not ffmpeg:
            raise RuntimeError(
                'ffmpeg not found but convert_audio=True.  '
                'Install ffmpeg and make sure it is on PATH, or pass ffmpeg_path= explicitly.'
            )
        xwmaencode = xwmaencode_path or find_xwmaencode()
        if xwmaencode:
            print('  ffmpeg + xWMAEncode found -- converting MP3 -> WAV -> XWM (proper xWMA)')
        else:
            print('  WARNING: xWMAEncode.exe not found -- falling back to ffmpeg ASF container')
            print('           Voice audio may not play in Skyrim! See README for xWMAEncode setup.')

    stats = {'organized': 0, 'skipped': 0, 'no_match': 0, 'errors': 0}
    unmapped_races: set = set()
    conversion_jobs: list[tuple] = []   # (src_path, dst_path)

    for plugin_dir in voice_root.iterdir():
        if not plugin_dir.is_dir():
            continue
        effective_plugin = plugin_name or plugin_dir.name

        for race_dir in plugin_dir.iterdir():
            if not race_dir.is_dir():
                continue
            race = race_dir.name

            for gender_dir in race_dir.iterdir():
                if not gender_dir.is_dir():
                    continue
                gender = gender_dir.name.upper()[:1]   # 'M' or 'F'

                voice_type = _TES4_VOICE_TYPE_MAP.get((race, gender))
                if voice_type is None:
                    for (r, g), vt in _TES4_VOICE_TYPE_MAP.items():
                        if r.lower() == race.lower() and g.upper() == gender:
                            voice_type = vt
                            break
                if voice_type is None:
                    unmapped_races.add((race, gender))
                    voice_type = f'TES4{"Male" if gender == "M" else "Female"}{race}'

                out_dir = dest_dir / 'sound' / 'Voice' / effective_plugin / voice_type
                out_dir.mkdir(parents=True, exist_ok=True)

                for audio_file in gender_dir.iterdir():
                    if not audio_file.is_file():
                        continue
                    m = _VOICE_FILENAME_RE.match(audio_file.name)
                    if not m:
                        stats['no_match'] += 1
                        continue

                    prefix           = m.group(1)   # quest_topic prefix
                    info_fid_hex     = m.group(2)    # original 8-hex FormID
                    resp_idx         = m.group(3)   # 1-based index from source filename
                    src_ext          = m.group(4).lower()

                    # Skyrim voice filename: <prefix>_<InfoFormID>_<RespNum>
                    # where prefix comes from the CONVERTED records (voicemap,
                    # keyed by the 24-bit InfoFormID) and the FormID keeps 8
                    # hex digits with the load-order byte zeroed. Lowercase —
                    # the engine's lookup is case-insensitive on disk but BSA
                    # paths are stored lowercase.
                    fid24 = int(info_fid_hex, 16) & 0xFFFFFF
                    target_vtyps = []
                    if voice_map:
                        entry = voice_map.get(fid24)
                        if entry is not None:
                            prefix, target_vtyps = entry
                    dst_ext = ('xwm' if ffmpeg and src_ext in ('mp3', 'wav')
                               else src_ext)
                    dst_name = f'{prefix}_{fid24:08x}_{resp_idx}.{dst_ext}'.lower()

                    # NPC-specific line: the engine reads it from the speaker's
                    # VTYP folder, which may differ from the Oblivion source race
                    # dir (Arvena = Dark Elf, recording under high elf/f/). Emit
                    # into each named VTYP folder; otherwise keep the source race
                    # folder (generic lines are recorded per race).
                    out_dirs = ([dest_dir / 'sound' / 'Voice' / effective_plugin
                                 / vt for vt in target_vtyps]
                                if target_vtyps else [out_dir])
                    for od in out_dirs:
                        od.mkdir(parents=True, exist_ok=True)
                        dst_path = od / dst_name
                        if dst_path.exists():
                            stats['skipped'] += 1
                            continue
                        conversion_jobs.append((audio_file, dst_path))

    if not conversion_jobs:
        if unmapped_races:
            print('  Warning: unmapped race/gender combos:')
            for r, g in sorted(unmapped_races):
                print(f'    {r}/{g}')
        print(f'  Voice files: 0 organised (all already present or no files found), '
              f'{stats["skipped"]} already present')
        return {**stats, 'unmapped_races': unmapped_races}

    print(f'  Processing {len(conversion_jobs)} voice files ({_WORKER_COUNT} workers)...')

    def _process_one(job):
        src_path, dst_path = job
        try:
            if ffmpeg and dst_path.suffix == '.xwm':
                return 'ok' if convert_file_to_xwm(src_path, dst_path, ffmpeg,
                                                    xwmaencode=xwmaencode) else 'error'
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            if copy:
                shutil.copy2(src_path, dst_path)
            else:
                shutil.move(str(src_path), dst_path)
            return 'ok'
        except Exception as e:
            return f'exception:{e}'

    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as pool:
        futures = {pool.submit(_process_one, job): job for job in conversion_jobs}
        for fut in as_completed(futures):
            result = fut.result()
            if result == 'ok':
                stats['organized'] += 1
            elif result == 'error':
                stats['errors'] += 1
                if stats['errors'] <= 5:
                    src, _ = futures[fut]
                    print(f'    ERROR: ffmpeg failed on {src.name}')
            elif result.startswith('exception:'):
                stats['errors'] += 1
                if stats['errors'] <= 5:
                    print(f'    ERROR: {result[10:]}')

    if unmapped_races:
        print('  Warning: unmapped race/gender combos (synthesised folder names):')
        for r, g in sorted(unmapped_races):
            print(f'    {r}/{g}')

    print(f'  Voice files: {stats["organized"]} organised, '
          f'{stats["skipped"]} already present, '
          f'{stats["errors"]} errors, '
          f'{stats["no_match"]} unrecognised names')
    return {**stats, 'unmapped_races': unmapped_races}
