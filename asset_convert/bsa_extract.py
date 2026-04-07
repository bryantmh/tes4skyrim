"""Extract assets from Oblivion BSA archives with caching support.

Extracts meshes, textures, and sounds from BSA files into the export directory,
organized by source file. Uses a manifest file to track what has already been
extracted, preventing redundant re-extraction on reruns.

Uses a native BSA reader (no external dependencies) that handles both
uncompressed and zlib-compressed Oblivion BSAs.

Voice file organization:
  TES4 voice path: Sound\\Voice\\<plugin>\\<Race>\\<Gender>\\<dialFID>_<infoFID>.mp3
  TES5 voice path: Sound\\Voice\\<plugin>\\<VoiceType>\\<infoFID>_0.mp3
  Use organize_voice_files() to rename/move extracted voice files to TES5 layout.
  Note: audio format conversion (MP3 → XWM/FUZ) is a separate step not automated here.
"""
import json
import os
import os
import re
import shutil
import struct
import subprocess
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Worker count used by all parallel operations in this module.
# cpu_count() - 3 to leave headroom for the OS and other processes.
_WORKER_COUNT = max(1, (os.cpu_count() or 4) - 3)


def _get_bsa_files(data_path, source_file):
    """Determine which BSA files to extract for a given source plugin.

    Oblivion BSA naming:
    - Oblivion.esm → Oblivion - Meshes.bsa, Oblivion - Textures - Compressed.bsa,
                      Oblivion - Sounds.bsa, Oblivion - Misc.bsa
    - Knights.esp  → Knights.bsa (single BSA for smaller DLCs)
    - DLCShiveringIsles.esp → DLCShiveringIsles - Meshes.bsa, etc.
    """
    data_dir = Path(data_path)
    base = Path(source_file).stem  # e.g. "Oblivion", "Knights"

    candidates = []
    # Try split BSAs first (Oblivion - Meshes.bsa, etc.)
    for pattern in [
        f"{base} - Meshes.bsa",
        f"{base} - Textures - Compressed.bsa",
        f"{base} - Textures.bsa",
        f"{base} - Sounds.bsa",
        f"{base} - Misc.bsa",
        f"{base} - Faces.bsa",
        f"{base} - Voices.bsa",
        f"{base} - Voices1.bsa",  # Oblivion splits voices across two BSAs
        f"{base} - Voices2.bsa",
    ]:
        bsa_file = data_dir / pattern
        if bsa_file.exists():
            candidates.append(bsa_file)

    # Try single BSA (Knights.bsa, etc.)
    single_bsa = data_dir / f"{base}.bsa"
    if single_bsa.exists():
        candidates.append(single_bsa)

    return candidates


def _should_extract_file(filepath):
    """Check if a file from BSA is an asset we want to extract.

    We extract: meshes (.nif), textures (.dds), sounds (.wav, .mp3),
    and misc assets (.kf animations, .tri face data, .egt eye glow).
    We skip: lip files.
    """
    fp = str(filepath).lower()

    # Skip lip files
    if fp.endswith('.lip'):
        return False

    # Accept these extensions
    ext = os.path.splitext(fp)[1]
    return ext in {'.nif', '.dds', '.wav', '.mp3', '.kf', '.tri', '.egt',
                   '.hkx', '.txt', '.xml'}


# Manifest file tracks what BSAs have been extracted
MANIFEST_NAME = '.bsa_extract_manifest.json'


def _load_manifest(extract_dir):
    """Load extraction manifest to check what's already been extracted."""
    manifest_path = Path(extract_dir) / MANIFEST_NAME
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return {'extracted_bsas': {}}


def _save_manifest(extract_dir, manifest):
    """Save extraction manifest."""
    manifest_path = Path(extract_dir) / MANIFEST_NAME
    os.makedirs(extract_dir, exist_ok=True)
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)


def _iter_bsa(bsa_path):
    """Yield (filepath_str, data_bytes) for every file in an Oblivion BSA.

    Handles both uncompressed and zlib-compressed BSAs natively.

    BSA layout (Oblivion, version 0x67):
      Header (36 bytes):
        magic(4) version(4) dirOffset(4) archiveFlags(4)
        folderCount(4) fileCount(4) totalFolderNameLen(4)
        totalFileNameLen(4) fileFlags(4)
      Folder records (folderCount × 16):  hash(8) fileCount(4) dataOffset(4)
      Per-folder data block:
        nameLen(1) folderName(nameLen)  [null-terminated, nameLen includes null]
        File records (fileCount × 16):  hash(8) size(4) offset(4)
      File name block:  null-terminated strings, one per file in folder order
    """
    BSA_MAGIC      = b'BSA\x00'
    ARCH_COMPRESS  = 0x0004   # default-compress flag in archiveFlags
    FILE_COMPRESS  = 0x40000000  # per-file size flag that inverts default

    data = Path(bsa_path).read_bytes()
    if data[:4] != BSA_MAGIC:
        raise ValueError(f"Not a BSA file: {bsa_path}")

    (_, dir_offset, archive_flags,
     folder_count, _, _, total_file_name_len, _
    ) = struct.unpack_from('<IIIIIIII', data, 4)

    compressed_by_default = bool(archive_flags & ARCH_COMPRESS)

    # --- Read folder records ---
    folders = []   # list of (file_count, data_offset)
    pos = dir_offset
    for _ in range(folder_count):
        _, f_count, f_offset = struct.unpack_from('<QII', data, pos)
        folders.append((f_count, f_offset))
        pos += 16

    # --- Read per-folder name+file-record blocks ---
    # These start immediately after the folder records.
    file_records = []   # (folder_name, size, offset, is_compressed)
    pos = dir_offset + folder_count * 16
    for f_count, _ in folders:
        name_len = data[pos]
        folder_name = data[pos + 1: pos + name_len].rstrip(b'\x00').decode('latin-1')
        pos += 1 + name_len
        for _ in range(f_count):
            _, f_size, f_offset = struct.unpack_from('<QII', data, pos)
            pos += 16
            per_file_flag = bool(f_size & FILE_COMPRESS)
            actual_size   = f_size & ~FILE_COMPRESS
            is_comp       = compressed_by_default ^ per_file_flag
            file_records.append((folder_name, actual_size, f_offset, is_comp))

    # --- Read file name block ---
    names_raw = data[pos: pos + total_file_name_len]
    file_names = names_raw.split(b'\x00')  # last entry may be empty

    # --- Yield files ---
    for idx, (folder_name, f_size, f_offset, is_comp) in enumerate(file_records):
        if idx >= len(file_names):
            break
        file_name = file_names[idx].decode('latin-1')
        filepath   = folder_name + '\\' + file_name if folder_name else file_name

        raw = data[f_offset: f_offset + f_size]
        if is_comp:
            try:
                raw = zlib.decompress(raw[4:])   # first 4 bytes = uncompressed size
            except zlib.error:
                continue   # skip corrupt/unsupported compressed entry

        yield filepath, raw


def extract_bsa(bsa_path, extract_dir, force=False, source_name=None):
    """Extract assets from a single BSA file.

    Args:
        bsa_path: Path to BSA file.
        extract_dir: Root directory for extracted files.
        force: If True, re-extract even if already done.
        source_name: Plugin filename (e.g. 'Oblivion.esm') used as a subfolder
                     under extract_dir to keep multiple sources separate.

    Returns:
        dict with stats: total_files, extracted, skipped, errors
    """
    bsa_path = Path(bsa_path)
    extract_dir = Path(extract_dir)
    base_dir = extract_dir / source_name if source_name else extract_dir
    manifest = _load_manifest(base_dir)

    bsa_key  = bsa_path.name
    bsa_size = bsa_path.stat().st_size

    if not force and bsa_key in manifest['extracted_bsas']:
        prev = manifest['extracted_bsas'][bsa_key]
        if prev.get('size') == bsa_size:
            print(f"  Skipping {bsa_key} (already extracted, {prev['file_count']} files)")
            return {'total_files': 0, 'extracted': 0, 'skipped_cached': True,
                    'skipped': 0, 'errors': 0}

    print(f"  Extracting {bsa_key} ({bsa_size / 1024 / 1024:.1f} MB)...")

    stats = {'total_files': 0, 'extracted': 0, 'skipped': 0, 'errors': 0,
             'skipped_cached': False}

    try:
        file_iter = _iter_bsa(bsa_path)
    except Exception as e:
        print(f"    ERROR opening BSA {bsa_key}: {e}")
        return stats

    for filepath, file_data in file_iter:
        stats['total_files'] += 1

        if not _should_extract_file(filepath):
            stats['skipped'] += 1
            continue

        fp_lower = filepath.lower().replace('/', '\\')
        if fp_lower.startswith('meshes\\'):
            out_rel = 'meshes/' + filepath[len('meshes\\'):]
        elif fp_lower.startswith('textures\\'):
            out_rel = 'textures/' + filepath[len('textures\\'):]
        elif fp_lower.startswith('sound\\'):
            out_rel = 'sound/' + filepath[len('sound\\'):]
        else:
            out_rel = 'misc/' + filepath

        out_path = base_dir / out_rel.replace('/', os.sep)

        try:
            os.makedirs(out_path.parent, exist_ok=True)
            out_path.write_bytes(file_data)
            stats['extracted'] += 1
        except Exception as e:
            stats['errors'] += 1
            if stats['errors'] <= 10:
                print(f"    ERROR writing {filepath}: {e}")

    manifest['extracted_bsas'][bsa_key] = {
        'size': bsa_size,
        'file_count': stats['extracted'],
        'total_in_bsa': stats['total_files'],
    }
    _save_manifest(base_dir, manifest)

    print(f"    Extracted {stats['extracted']} files, "
          f"skipped {stats['skipped']}, errors {stats['errors']}")
    return stats


def extract_assets_for_file(source_file, data_path, extract_dir, force=False):
    """Extract all BSA assets needed by a given plugin file.

    Args:
        source_file: Plugin filename (e.g. 'Oblivion.esm').
        data_path: Path to Oblivion Data directory.
        extract_dir: Root directory for extracted assets.
        force: Force re-extraction.

    Returns:
        dict with overall stats.
    """
    bsa_files = _get_bsa_files(data_path, source_file)

    if not bsa_files:
        print(f"No BSA files found for {source_file} in {data_path}")
        return {'bsas_found': 0}

    print(f"Found {len(bsa_files)} BSA(s) for {source_file}:")
    for b in bsa_files:
        print(f"  {b.name} ({b.stat().st_size / 1024 / 1024:.1f} MB)")

    totals = {'bsas_found': len(bsa_files), 'bsas_extracted': 0,
              'bsas_cached': 0, 'total_extracted': 0, 'total_errors': 0}

    for bsa_file in bsa_files:
        stats = extract_bsa(bsa_file, extract_dir, force=force, source_name=source_file)
        if stats.get('skipped_cached'):
            totals['bsas_cached'] += 1
        else:
            totals['bsas_extracted'] += 1
            totals['total_extracted'] += stats['extracted']
            totals['total_errors'] += stats['errors']

    print(f"\nBSA extraction complete: {totals['bsas_extracted']} extracted, "
          f"{totals['bsas_cached']} cached, "
          f"{totals['total_extracted']} files written")
    return totals


# ---------------------------------------------------------------------------
# Asset utilities
# ---------------------------------------------------------------------------

def _get_asset_category(asset_path: str) -> str:
    """Return the top-level category ('meshes', 'textures', 'sound', etc.)
    for a BSA-relative asset path.  Case-insensitive."""
    parts = asset_path.replace('\\', '/').split('/')
    return parts[0].lower() if parts else ''


# ---------------------------------------------------------------------------
# Voice file organization: TES4 layout → TES5 layout
# ---------------------------------------------------------------------------

def _find_ffmpeg(ffmpeg_path='ffmpeg'):
    """Return the ffmpeg executable path if it is available, else None."""
    try:
        result = subprocess.run(
            [ffmpeg_path, '-version'],
            capture_output=True, timeout=10,
        )
        # ffmpeg exits 1 on -version but still prints version info
        if b'ffmpeg version' in result.stdout or b'ffmpeg version' in result.stderr:
            return ffmpeg_path
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _mp3_to_xwm(src_path: Path, dst_path: Path, ffmpeg: str) -> bool:
    """Convert an audio file to XWM (ASF/wmav2) using ffmpeg.

    XWM files are ASF containers carrying wmav2 audio — the format Skyrim
    uses for compressed voice/sound files (.xwm).

    Quality strategy:
    - Decode source losslessly to PCM (ffmpeg always does this internally).
    - Encode to wmav2 at 96 kbps, mono, 44100 Hz.
      96 kbps wmav2 is perceptually transparent for voice at 44.1 kHz mono
      and exceeds the typical bitrate of Oblivion's compressed MP3 files
      (which are commonly 64–128 kbps stereo → our output is higher quality
      relative to the content while staying within Skyrim's expected range).
    - Force mono (-ac 1): all Oblivion/Skyrim voice files are mono; keeping
      a stereo MP3 would double the file size with no benefit.
    - Force ASF container (-f asf): required because ffmpeg doesn't know the
      .xwm extension; the resulting file is a valid WMA (ASF) file that
      Skyrim reads natively as xWMA.

    Returns True on success, False on failure.
    """
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        '-y',                  # overwrite without prompt
        '-i', str(src_path),
        '-c:a', 'wmav2',
        '-b:a', '96k',
        '-ac', '1',            # force mono
        '-ar', '44100',        # standard Skyrim sample rate
        '-f', 'asf',           # ASF container (Skyrim reads as XWM)
        str(dst_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False

# TES4 Race folder name + Gender folder ('M'/'F') -> custom TES4* VoiceType EditorID.
# These EditorIDs match the VTYP records created by _create_vtyp_records() in
# import_main.py.  Voice files land in Sound/Voice/<plugin>/<EditorID>/.
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
    # Alternate spellings/cases found in Oblivion BSA folder names:
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

# Regex: matches <8hex>_<8hex>.<ext> (TES4 voice filename format)
# Oblivion voice filename format: <topic>_<infoFID8>_<idx>.<ext>
# e.g. arenadialogue_attack_0018bd5d_1.mp3  or  goodbye_0002a7d3_1.mp3
# Capture the 8-hex FormID (second-to-last underscore segment) and track index.
_VOICE_FILENAME_RE = re.compile(
    r'^.+_([0-9a-fA-F]{8})_(\d+)\.(mp3|wav|xwm|fuz)$',
    re.IGNORECASE
)


def organize_voice_files(source_dir, dest_dir, plugin_name=None, copy=True,
                         convert_audio=True, ffmpeg_path='ffmpeg',
                         formid_index: int = 1):
    """Reorganize extracted TES4 voice files into TES5 directory layout.

    TES4 layout: <source_dir>/sound/Voice/<plugin>/<Race>/<Gender>/<topic>_<infoFID>_<idx>.mp3
    TES5 layout: <dest_dir>/Sound/Voice/<plugin>/<VoiceType>/<infoFID_shifted>_0.xwm

    The VoiceType folder names match the custom TES4* VTYP EditorIDs created in
    the output plugin by _create_vtyp_records() (e.g. TES4MaleNord).

    When ``convert_audio=True`` (the default) each MP3 is converted to XWM using
    ffmpeg (wmav2 96 kbps mono 44100 Hz).  Conversion is parallelised across
    _WORKER_COUNT threads.  If ffmpeg is not available and convert_audio=True the
    function raises RuntimeError rather than silently copying incompatible MP3s.

    Args:
        source_dir:    Root extracted asset directory (contains 'sound/' subfolder).
        dest_dir:      Root output directory for organized files.
        plugin_name:   Override the plugin folder name (auto-detected from BSA path).
        copy:          If True (default), copy files; if False, move source files.
        convert_audio: Convert MP3 -> XWM via ffmpeg (default True).
        ffmpeg_path:   Path to ffmpeg executable (default 'ffmpeg' -- uses PATH).
        formid_index:  Index byte of the plugin's own records in the TES5 load order.
                       Default=1 assumes one master (Skyrim.esm) precedes the plugin,
                       shifting Oblivion FormIDs from 0x00XXXXXX to 0x01XXXXXX.

    Returns:
        dict with stats: organized, skipped, no_match, errors, unmapped_races
    """
    source_dir = Path(source_dir)
    dest_dir = Path(dest_dir)

    # Find the voice root: source_dir/sound/Voice/
    voice_root = source_dir / 'sound' / 'Voice'
    if not voice_root.exists():
        print(f"  Voice directory not found: {voice_root}")
        return {'organized': 0, 'skipped': 0, 'no_match': 0, 'errors': 0,
                'unmapped_races': set()}

    # Detect ffmpeg availability
    ffmpeg = None
    if convert_audio:
        ffmpeg = _find_ffmpeg(ffmpeg_path)
        if ffmpeg:
            print("  ffmpeg found -- converting MP3 -> XWM (wmav2 96kbps mono 44100 Hz)")
        else:
            raise RuntimeError(
                "ffmpeg not found but convert_audio=True.  "
                "Install ffmpeg and make sure it is on PATH, or pass ffmpeg_path= explicitly."
            )

    stats = {'organized': 0, 'skipped': 0, 'no_match': 0, 'errors': 0}
    unmapped_races = set()

    # Collect all (src_path, dst_path) pairs first
    conversion_jobs = []  # list of (src_path, dst_path)

    # Walk: sound/Voice/<plugin>/<Race>/<Gender>/<file>
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
                gender = gender_dir.name.upper()[:1]  # 'M' or 'F'

                voice_type = _TES4_VOICE_TYPE_MAP.get((race, gender))
                if voice_type is None:
                    # Case-insensitive fallback
                    for (r, g), vt in _TES4_VOICE_TYPE_MAP.items():
                        if r.lower() == race.lower() and g.upper() == gender:
                            voice_type = vt
                            break
                if voice_type is None:
                    unmapped_races.add((race, gender))
                    # Synthesize a name from the raw folder so files are not lost
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

                    # TES5 name: <infoFormID_shifted>_0.xwm
                    # groups: (1)=infoFID, (2)=idx, (3)=ext
                    info_fid_int = int(m.group(1), 16)
                    info_fid_shifted = ((info_fid_int & 0x00FFFFFF) |
                                        (formid_index << 24))
                    info_fid = f'{info_fid_shifted:08X}'
                    src_ext = m.group(3).lower()

                    if ffmpeg and src_ext in ('mp3', 'wav'):
                        dst_name = f'{info_fid}_0.xwm'
                    else:
                        dst_name = f'{info_fid}_0.{src_ext}'

                    dst_path = out_dir / dst_name
                    if dst_path.exists():
                        stats['skipped'] += 1
                        continue

                    conversion_jobs.append((audio_file, dst_path))

    if not conversion_jobs:
        if unmapped_races:
            print(f"  Warning: unmapped race/gender combos:")
            for r, g in sorted(unmapped_races):
                print(f"    {r}/{g}")
        print(f"  Voice files: 0 organized (all already present or no files found), "
              f"{stats['skipped']} already present")
        return {**stats, 'unmapped_races': unmapped_races}

    print(f"  Processing {len(conversion_jobs)} voice files ({_WORKER_COUNT} workers)...")

    def _process_one(job):
        src_path, dst_path = job
        try:
            if ffmpeg and dst_path.suffix == '.xwm':
                ok = _mp3_to_xwm(src_path, dst_path, ffmpeg)
                return 'ok' if ok else 'error'
            else:
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
                    print(f"    ERROR: ffmpeg failed on {src.name}")
            elif result.startswith('exception:'):
                stats['errors'] += 1
                if stats['errors'] <= 5:
                    print(f"    ERROR: {result[10:]}")

    if unmapped_races:
        print(f"  Warning: unmapped race/gender combos (synthesized folder names):")
        for r, g in sorted(unmapped_races):
            print(f"    {r}/{g}")

    print(f"  Voice files: {stats['organized']} organized, "
          f"{stats['skipped']} already present, "
          f"{stats['errors']} errors, "
          f"{stats['no_match']} unrecognized names")
    return {**stats, 'unmapped_races': unmapped_races}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Extract Oblivion BSA assets and organize voice files')
    parser.add_argument('source_file', help='Plugin filename (e.g. Oblivion.esm)')
    parser.add_argument('--data-path', required=True,
                        help='Path to Oblivion Data directory')
    parser.add_argument('--extract-dir', default='export',
                        help='Output directory for extracted assets (default: export)')
    parser.add_argument('--force', action='store_true',
                        help='Force re-extraction of already-extracted BSAs')
    parser.add_argument('--organize-voice', metavar='OUTPUT_DIR',
                        help='After extraction, organize voice files to TES5 layout in OUTPUT_DIR')
    parser.add_argument('--no-convert-audio', action='store_true',
                        help='Skip MP3→XWM conversion (copy MP3 files as-is)')
    parser.add_argument('--ffmpeg', default='ffmpeg', metavar='PATH',
                        help='Path to ffmpeg executable (default: ffmpeg from PATH)')
    parser.add_argument('--formid-index', type=int, default=1, metavar='N',
                        help='TES5 load-order index byte for the plugin own records '
                             '(default 1 = one master Skyrim.esm precedes the plugin)')
    args = parser.parse_args()

    extract_assets_for_file(args.source_file, args.data_path,
                           args.extract_dir, force=args.force)

    if args.organize_voice:
        source_dir = Path(args.extract_dir) / args.source_file
        dest_dir = Path(args.organize_voice)
        organize_voice_files(source_dir, dest_dir, args.source_file,
                             convert_audio=not args.no_convert_audio,
                             ffmpeg_path=args.ffmpeg,
                             formid_index=args.formid_index)
