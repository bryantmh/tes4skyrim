"""Audio conversion: MP3/WAV → XWM (Skyrim xWMA format) using ffmpeg.

Handles two operations:
  convert_sounds()       – Parallel batch conversion of all extracted sounds.
  organize_voice_files() – Reorganise TES4 voice files to TES5 directory layout.

XWM format: ASF container + wmav2 audio, 96 kbps, mono, 44100 Hz.
Skyrim reads files with the .xwm extension as xWMA natively.

All conversion is multithreaded: one ffmpeg process per ThreadPoolExecutor
worker, giving near-linear speedup since wmav2 is single-threaded per file.
"""
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Use most CPUs – wmav2 is fast so many parallel ffmpeg processes help.
_WORKER_COUNT = max(1, (os.cpu_count() or 4) - 1)

# ---------------------------------------------------------------------------
# ffmpeg detection + single-file conversion
# ---------------------------------------------------------------------------

def find_ffmpeg(ffmpeg_path: str = 'ffmpeg') -> 'str | None':
    """Return the ffmpeg executable path if found, else None."""
    try:
        r = subprocess.run(
            [ffmpeg_path, '-version'],
            capture_output=True,
            timeout=10,
        )
        if b'ffmpeg version' in r.stdout or b'ffmpeg version' in r.stderr:
            return ffmpeg_path
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def convert_file_to_xwm(src_path, dst_path, ffmpeg: str) -> bool:
    """Convert a single audio file to XWM using ffmpeg.

    Args:
        src_path: Source audio file (.mp3, .wav, or any ffmpeg-readable format).
        dst_path: Destination path; the .xwm extension is expected but not enforced.
        ffmpeg:   Path to the ffmpeg executable (from find_ffmpeg).

    Returns:
        True if conversion produced a non-empty .xwm file, False on any failure.
    """
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        '-y',                   # overwrite without prompt
        '-i', str(src_path),
        '-c:a', 'wmav2',
        '-b:a', '96k',
        '-ac', '1',             # mono  — all Oblivion/Skyrim voice files are mono
        '-ar', '44100',         # standard Skyrim sample rate
        '-f', 'asf',            # ASF container (Skyrim reads as xWMA)
        str(dst_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        return (r.returncode == 0
                and dst_path.is_file()
                and dst_path.stat().st_size > 0)
    except (subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# Batch sound conversion
# ---------------------------------------------------------------------------

def convert_sounds(
    source_file: str,
    extract_dir: str = 'export',
    output_dir: str = 'output',
    ffmpeg_path: str = 'ffmpeg',
) -> dict:
    """Convert all extracted sounds (MP3/WAV → XWM) with multi-threaded ffmpeg.

    Walks ``extract_dir/<source_name>/sound/``, converts .mp3/.wav to .xwm,
    copies all other files (including already-XWM) directly.

    Args:
        source_file:  Plugin filename (e.g. 'Oblivion.esm').
        extract_dir:  Root extraction directory (default: export).
        output_dir:   Final output root (default: output).
        ffmpeg_path:  Path to ffmpeg executable (default: 'ffmpeg' from PATH).

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
    ffmpeg = find_ffmpeg(ffmpeg_path)

    if ffmpeg is None:
        print('  WARNING: ffmpeg not found — copying sound files as-is (no XWM conversion).')
        print('  Install ffmpeg and add it to PATH to enable XWM conversion.')
        count = 0
        for root_dir, _, files in os.walk(snd_src):
            for fname in files:
                src = Path(root_dir) / fname
                dst = snd_dst / src.relative_to(snd_src)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                count += 1
        print(f'  Copied {count} files (no conversion) → {snd_dst}')
        return {'converted': 0, 'copied': count, 'failed': 0, 'total': count}

    print(f'  ffmpeg: {ffmpeg}  |  workers: {_WORKER_COUNT}')

    # Collect all jobs: (src_path, dst_path, needs_conversion)
    jobs: list[tuple] = []
    for root_dir, _, files in os.walk(snd_src):
        for fname in files:
            src = Path(root_dir) / fname
            rel = src.relative_to(snd_src)
            if src.suffix.lower() in ('.mp3', '.wav'):
                jobs.append((src, snd_dst / rel.with_suffix('.xwm'), True))
            else:
                jobs.append((src, snd_dst / rel, False))

    if not jobs:
        print('  No sound files found.')
        return {'converted': 0, 'copied': 0, 'failed': 0, 'total': 0}

    total_jobs = len(jobs)
    stats: dict[str, int] = {'converted': 0, 'copied': 0, 'failed': 0}
    failed_names: list[str] = []

    def _do_job(job):
        src, dst, needs_conv = job
        if not needs_conv:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return 'copied'
        return 'converted' if convert_file_to_xwm(src, dst, ffmpeg) else 'failed'

    done = 0
    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as pool:
        futures = {pool.submit(_do_job, job): job for job in jobs}
        for fut in as_completed(futures):
            outcome = fut.result()
            stats[outcome] += 1
            done += 1
            if outcome == 'failed':
                src_path = futures[fut][0]
                failed_names.append(src_path.name)
                if len(failed_names) <= 5:
                    print(f'    FAILED: {src_path.name}')
            if done % 500 == 0 or done == total_jobs:
                print(
                    f'  {done}/{total_jobs} — '
                    f'converted={stats["converted"]} '
                    f'copied={stats["copied"]} '
                    f'failed={stats["failed"]}'
                )

    total = sum(stats.values())
    print(
        f'\n  Sound conversion complete: '
        f'{stats["converted"]} XWM, {stats["copied"]} copied, '
        f'{stats["failed"]} failed / {total} total'
    )
    print(f'  Output: {snd_dst}')
    return {**stats, 'total': total}


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

# Oblivion voice filename: <topic>_<infoFID8hex>_<idx>.<ext>
_VOICE_FILENAME_RE = re.compile(
    r'^.+_([0-9a-fA-F]{8})_(\d+)\.(mp3|wav|xwm|fuz)$',
    re.IGNORECASE,
)


def organize_voice_files(
    source_dir,
    dest_dir,
    plugin_name=None,
    copy=True,
    convert_audio=True,
    ffmpeg_path='ffmpeg',
    formid_index: int = 1,
) -> dict:
    """Reorganise extracted TES4 voice files into TES5 directory layout.

    TES4 layout: <source_dir>/sound/Voice/<plugin>/<Race>/<Gender>/<topic>_<infoFID>_<idx>.mp3
    TES5 layout: <dest_dir>/Sound/Voice/<plugin>/<VoiceType>/<infoFID_shifted>_0.xwm

    When ``convert_audio=True`` (default) each MP3/WAV is converted to XWM
    using ffmpeg in parallel.  Raises RuntimeError if ffmpeg is unavailable
    and convert_audio=True.

    Args:
        source_dir:    Root extracted asset directory (contains 'sound/' subfolder).
        dest_dir:      Root output directory for organised files.
        plugin_name:   Override the plugin folder name (auto-detected from BSA path).
        copy:          If True (default), copy files; if False, move source files.
        convert_audio: Convert MP3/WAV → XWM via ffmpeg (default True).
        ffmpeg_path:   Path to ffmpeg executable (default 'ffmpeg').
        formid_index:  Load-order index byte for the plugin (default 1).

    Returns:
        dict with keys: organized, skipped, no_match, errors, unmapped_races.
    """
    source_dir = Path(source_dir)
    dest_dir   = Path(dest_dir)

    voice_root = source_dir / 'sound' / 'Voice'
    if not voice_root.exists():
        print(f'  Voice directory not found: {voice_root}')
        return {'organized': 0, 'skipped': 0, 'no_match': 0, 'errors': 0,
                'unmapped_races': set()}

    ffmpeg = None
    if convert_audio:
        ffmpeg = find_ffmpeg(ffmpeg_path)
        if not ffmpeg:
            raise RuntimeError(
                'ffmpeg not found but convert_audio=True.  '
                'Install ffmpeg and make sure it is on PATH, or pass ffmpeg_path= explicitly.'
            )
        print('  ffmpeg found — converting MP3 → XWM (wmav2 96 kbps mono 44100 Hz)')

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

                out_dir = dest_dir / 'Sound' / 'Voice' / effective_plugin / voice_type
                out_dir.mkdir(parents=True, exist_ok=True)

                for audio_file in gender_dir.iterdir():
                    if not audio_file.is_file():
                        continue
                    m = _VOICE_FILENAME_RE.match(audio_file.name)
                    if not m:
                        stats['no_match'] += 1
                        continue

                    info_fid_int     = int(m.group(1), 16)
                    info_fid_shifted = (info_fid_int & 0x00FFFFFF) | (formid_index << 24)
                    info_fid         = f'{info_fid_shifted:08X}'
                    src_ext          = m.group(3).lower()

                    dst_name = (f'{info_fid}_0.xwm'
                                if ffmpeg and src_ext in ('mp3', 'wav')
                                else f'{info_fid}_0.{src_ext}')
                    dst_path = out_dir / dst_name

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
                return 'ok' if convert_file_to_xwm(src_path, dst_path, ffmpeg) else 'error'
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
