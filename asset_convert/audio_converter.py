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
# We need to PRESERVE the quest_topic_ prefix — Skyrim constructs the expected
# filename as QuestEDID_TopicEDID_InfoFormID(masked)_RespNum at runtime.
# The InfoFormID in the filename uses the 24-bit value (load-order byte stripped,
# padded to 8 hex).  We must NOT shift the FormID.
_VOICE_FILENAME_RE = re.compile(
    r'^(.+)_([0-9a-fA-F]{8})_(\d+)\.(mp3|wav|xwm|fuz)$',
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
    TES5 layout: <dest_dir>/Sound/Voice/<plugin>/<VoiceType>/<infoFID_shifted>_<idx>.xwm

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
        print('  ffmpeg found -- converting MP3 -> XWM (wmav2 96 kbps mono 44100 Hz)')

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

                    prefix           = m.group(1)   # quest_topic prefix
                    info_fid_hex     = m.group(2)    # original 8-hex FormID
                    resp_idx         = m.group(3)   # 1-based index from source filename
                    src_ext          = m.group(4).lower()

                    # Skyrim voice filename: QuestEDID_TopicEDID_InfoFormID_RespNum
                    # InfoFormID = original 24-bit value padded to 8 hex (NO load-order shift).
                    # The prefix from the source filename already contains Quest_Topic.
                    # We keep the FormID as-is from the source (it's already the 24-bit value).
                    dst_name = (f'{prefix}_{info_fid_hex}_{resp_idx}.xwm'
                                if ffmpeg and src_ext in ('mp3', 'wav')
                                else f'{prefix}_{info_fid_hex}_{resp_idx}.{src_ext}')
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
