"""Extract assets from BSA archives with caching support.

Extracts meshes, textures, and sounds from BSA files into the export directory,
organized by source file. Uses a manifest file to track what has already been
extracted, preventing redundant re-extraction on reruns.

Reads TES4, FO3 and Skyrim LE/SE archives natively; Morrowind's are read by
bsa_extract_morrowind.

Voice file organization:
  TES4 voice path: Sound\\Voice\\<plugin>\\<Race>\\<Gender>\\<quest>_<topic>_<infoFID>_<resp>.mp3
  TES5 voice path: Sound\\Voice\\<plugin>\\<VoiceType>\\<quest>_<topic>_<infoFID>_<resp>.xwm
  Use organize_voice_files() to rename/move extracted voice files to TES5 layout.
  Note: audio format conversion (MP3 → XWM) is handled by ffmpeg (wmav2 96kbps mono).
"""
import json
import os
import shutil
import struct
import zlib
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
import lz4.frame
from asset_convert.sources.bsa_extract_morrowind import (
    is_morrowind_bsa, read_table as read_morrowind_table)
from asset_convert.sources.bsa_extract_worker import extract_chunk
from asset_convert.sources.bsa_extract_morrowind_sounds import (
    extract_loose_audio)
from core.worker_budget import worker_count


#: Extra archive bases per stem, in probe order. See: docs/commentary/asset_convert_mod_ingest.md#update-bsa
_EXTRA_BSA_BASES = {
    "nehrim": ["N", "L"],
    "falloutnv": ["Fallout", "Update"],
    "fallout3": ["Fallout"],
    "Arktwend_English": ["Morrowind", "Bloodmoon", "Tribunal"],
    "Arktwend 2.0.0 (DE)": ["Morrowind", "Bloodmoon", "Tribunal"]
}

# GOTY Oblivion.esm has Shivering Isles MERGED INTO IT: every SI record lives
# in the master, but the SI assets stayed in "DLCShiveringIsles - *.bsa" and
# DLCShiveringIsles.esp is left as an 85-byte header-only stub. So Oblivion.esm
# must extract the SI BSAs too, or its own SI records resolve to no mesh —
# OBND falls back to the STAT type default (100 units, under
# LOD_SIZE_THRESHOLD) so no SI static is ever flagged Visible-When-Distant and
# SEWorld renders with no distant objects, and the 8 SI grasses count as
# "missing" in the grass step.
#
# This claim is EXCLUSIVE: because the merged-into plugin extracts the DLC's
# BSAs, the DLC plugin itself must NOT extract them again (see
# _SUPPRESSED_BSA_BASES). Its records all live in the master, so a second copy
# of the same 15,914 files (~1.2 GB, byte-identical) buys nothing.
_MERGED_DLC_BSA_BASES = {
    "oblivion": ["DLCShiveringIsles"],
}

# The reverse side of _MERGED_DLC_BSA_BASES: plugin stem → BSA name bases that
# some OTHER plugin already extracts, so this plugin skips them entirely.
# DLCShiveringIsles.esp is the header-only GOTY stub (HEDR.NumRecords=0) whose
# every record was merged into Oblivion.esm; Oblivion.esm therefore owns the SI
# assets and the stub extracts nothing. Derived from the table above so the two
# halves cannot drift apart.
_SUPPRESSED_BSA_BASES = {
    base.lower()
    for bases in _MERGED_DLC_BSA_BASES.values()
    for base in bases
}


def get_bsa_files(data_path, source_file):
    """Every BSA a source plugin claims, by name, in the game's Data folder.

    Candidates come from the plugin stem plus any extra bases the plugin is
    registered with; a plugin whose archives another already claims gets none.

    See: docs/commentary/asset_convert_mod_ingest.md#bsa-naming-per-game
    """
    data_dir = Path(data_path)
    stem = Path(source_file).stem  # e.g. "Oblivion", "Knights", "Nehrim"

    # A plugin whose BSAs another plugin already extracts claims none of its
    # own. The GOTY DLCShiveringIsles.esp stub is the case: Oblivion.esm holds
    # every SI record and extracts "DLCShiveringIsles - *.bsa" on its behalf.
    if stem.lower() in _SUPPRESSED_BSA_BASES:
        return []

    # Probe the plugin stem, any hardcoded extra bases (e.g. Nehrim → N, L),
    # and the BSAs of a DLC merged into this plugin (GOTY Oblivion.esm owns
    # every Shivering Isles record, so it must also read
    # "DLCShiveringIsles - *.bsa" to get those records' meshes/textures).
    # Non-existent BSAs are skipped by _try(), so a non-GOTY install that
    # lacks them is unaffected.
    bases = ([stem] + _EXTRA_BSA_BASES.get(stem.lower(), [])
             + _MERGED_DLC_BSA_BASES.get(stem.lower(), []))

    candidates = []
    seen = set()

    def _try(name):
        bsa_file = data_dir / name
        key = str(bsa_file).lower()
        if key not in seen and bsa_file.exists():
            seen.add(key)
            candidates.append(bsa_file)

    for base in bases:
        # Split BSAs (Oblivion - Meshes.bsa, N - Textures1.bsa, etc.)
        for pattern in [
            f"{base} - Main.bsa",  # Fallout 3 DLC (Anchorage - Main.bsa)
            f"{base} - Meshes.bsa",
            f"{base} - Meshes2.bsa",
            f"{base} - Textures - Compressed.bsa",
            f"{base} - Textures.bsa",
            f"{base} - Textures1.bsa",  # Nehrim splits textures across two BSAs
            f"{base} - Textures2.bsa",
            f"{base} - Sounds.bsa",
            f"{base} - Sound.bsa",
            f"{base} - Misc.bsa",
            f"{base} - Faces.bsa",
            f"{base} - Voices.bsa",
            f"{base} - Voices1.bsa",  # Oblivion splits voices across two BSAs
            f"{base} - Voices2.bsa",
            f"{base} - MenuVoices.bsa",
        ]:
            _try(pattern)

        # Single BSA (Knights.bsa, etc.)
        _try(f"{base}.bsa")

    return candidates


def should_extract_file(filepath):
    """Check if a file from BSA is an asset we want to extract.

    We extract: meshes (.nif), textures (.dds), sounds (.wav, .mp3),
    and misc assets (.kf animations, .tri face data, .egt eye glow).
    We skip: lip files.
    """
    fp = str(filepath).lower()

    # Skip lip files
    if fp.endswith('.lip'):
        return False

    return True


# The top-level asset folders that keep their own name under export/<plugin>/.
# Anything else is filed under misc/ (Oblivion's DistantLOD\, Docs\, ...).
ASSET_CATEGORIES = ('meshes', 'trees', 'textures', 'sound', 'music')


def split_category(filepath):
    """Split an asset path into (category, rest-of-path).

    ONE definition, shared by BSA extraction and mod-archive ingest: a loose
    file and the same file inside a BSA must land in exactly the same place, or
    every stage downstream sees two different trees for the same mod.

    `rest` keeps its original separators; callers split it themselves, because
    BSA paths are always Windows-style backslash regardless of host OS.
    """
    lower = str(filepath).lower().replace('/', '\\')
    for cat in ASSET_CATEGORIES:
        if lower.startswith(cat + '\\'):
            return cat, str(filepath)[len(cat) + 1:]
    return 'misc', str(filepath)


def categorize(filepath):
    """`split_category` as one forward-slash relative path.

    e.g. 'Meshes\\foo\\bar.nif' -> 'meshes/foo/bar.nif'.
    """
    category, rest = split_category(filepath)
    parts = [p for p in rest.replace('\\', '/').split('/') if p]
    return '/'.join([category] + parts)


# ---------------------------------------------------------------------------
# Extraction manifest
# ---------------------------------------------------------------------------

#: Records each extracted BSA's path, size and file count in the folder it filled.
MANIFEST_NAME = '.bsa_extract_manifest.json'


def load_manifest(extract_dir):
    """Load extraction manifest to check what's already been extracted."""
    manifest_path = Path(extract_dir) / MANIFEST_NAME
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return {'extracted_bsas': {}}


def save_manifest(extract_dir, manifest):
    """Save extraction manifest."""
    manifest_path = Path(extract_dir) / MANIFEST_NAME
    os.makedirs(extract_dir, exist_ok=True)
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)


def extracted_from(base_dir, bsa_files) -> list:
    """Where `base_dir`'s earlier archives came from, when not among `bsa_files`.

    An entry written before paths were recorded is named by its archive alone.
    """
    current = {os.path.normcase(str(b)) for b in bsa_files}
    names = {Path(b).name for b in bsa_files}
    out = set()
    for name, row in load_manifest(base_dir)['extracted_bsas'].items():
        path = row.get('path')
        if path and os.path.normcase(path) not in current:
            out.add(str(Path(path).parent))
        elif not path and name not in names:
            out.add(f'{name} (folder not recorded)')
    return sorted(out)


def _warn_mixed_sources(base_dir, data_path, bsa_files) -> None:
    """Say where extraction reads from, and warn when `base_dir` already holds other archives' files."""
    print(f"Extracting from {data_path} into {base_dir}")
    for other in extracted_from(base_dir, bsa_files):
        print(f"  WARNING: {base_dir} also holds files extracted from {other}; "
              f"clear it for a clean extraction from {data_path}")


# ---------------------------------------------------------------------------
# BSA archive reading
# ---------------------------------------------------------------------------
#: Archive flag: files are compressed unless their size carries `_FILE_COMPRESS`.
_ARCH_COMPRESS = 0x0004

#: Archive flag (v104+): each file's data starts with its own bstring path.
_ARCH_EMBED_NAME = 0x0100

#: File size bit that INVERTS the archive's compression default for that file.
_FILE_COMPRESS = 0x40000000

#: Bits the TES4 BSA hash sets in its low word for these extensions.
_EXT_HASH_BITS = {'.kf': 0x80, '.nif': 0x8000, '.dds': 0x8080, '.wav': 0x80000000}


def read_bsa_directory(fh):
    """Parse the directory of an open TES4/FO3/Skyrim LE/SE BSA (v103/104/105).

    Returns (version, archive_flags, [(path, size, offset, compressed)]).
    Folder records are 24 bytes in v105 (64-bit offset) and 16 before it; file
    records are 16 in every version (xEdit wbBSArchive.pas).
    """
    head = fh.read(36)
    if head[:4] != b'BSA\x00':
        raise ValueError(f'Not a BSA file: {fh.name}')
    version, dir_offset, flags, folder_count, _, _, names_len, _ = struct.unpack_from('<8I', head, 4)
    fh.seek(dir_offset)
    record_size = 24 if version >= 105 else 16
    counts = [struct.unpack_from('<I', fh.read(record_size), 8)[0] for _ in range(folder_count)]
    records = []
    for count in counts:
        folder = fh.read(fh.read(1)[0]).rstrip(b'\x00').decode('latin-1')
        records += [(folder, *struct.unpack('<8xII', fh.read(16))) for _ in range(count)]
    names = fh.read(names_len).decode('latin-1').split('\x00')
    default = bool(flags & _ARCH_COMPRESS)
    return version, flags, [
        ('\\'.join(filter(None, (folder, name))), size & ~_FILE_COMPRESS, offset,
         default ^ bool(size & _FILE_COMPRESS))
        for (folder, size, offset), name in zip(records, names)]


def read_bsa_folders(fh) -> list:
    """The folder names of an open TES4/FO3/Skyrim LE/SE BSA, skipping every file record and name.

    See: docs/commentary/core_gui.md#idle-steps
    """
    head = fh.read(36)
    if head[:4] != b'BSA\x00':
        raise ValueError(f'Not a BSA file: {fh.name}')
    version, dir_offset, _flags, folder_count = struct.unpack_from('<4I', head, 4)
    fh.seek(dir_offset)
    record_size = 24 if version >= 105 else 16
    table = fh.read(record_size * folder_count)
    counts = [struct.unpack_from('<I', table, i * record_size + 8)[0] for i in range(folder_count)]
    folders = []
    for count in counts:
        folders.append(fh.read(fh.read(1)[0]).rstrip(b'\x00').decode('latin-1').lower())
        fh.seek(16 * count, 1)
    return folders


def tes4_hash(name: str, folder: bool = False) -> int:
    """The TES4-family BSA hash of a file or (`folder`) folder name, lower case with backslashes, as stored.

    A folder is hashed whole: `textures\\faces\\oblivion.esm` has no extension.
    See: docs/commentary/asset_convert_bsa.md#bsa-name-hash
    """
    stem, dot, ext = (name, '', '') if folder or '.' not in name else name.rpartition('.')
    ext = dot + ext
    low = (ord(stem[-1]) if stem else 0) | ((ord(stem[-2]) << 8) if len(stem) > 2 else 0)
    low |= (len(stem) << 16) | ((ord(stem[0]) << 24) if stem else 0)
    low |= _EXT_HASH_BITS.get(ext, 0)
    high = 0
    for ch in stem[1:-2]:
        high = (high * 0x1003F + ord(ch)) & 0xFFFFFFFF
    tail = 0
    for ch in ext:
        tail = (tail * 0x1003F + ord(ch)) & 0xFFFFFFFF
    return (((high + tail) & 0xFFFFFFFF) << 32) | (low & 0xFFFFFFFF)


def bsa_has(bsa_path, rel: str) -> bool:
    """Whether the TES4-family BSA at `bsa_path` stores `rel`, found by hash and confirmed by name.

    Reads the folder table and one folder's file records, not the whole directory.
    See: docs/commentary/asset_convert_bsa.md#bsa-name-hash
    """
    folder, _, file = rel.lower().replace('/', chr(92)).rpartition(chr(92))
    with open(bsa_path, 'rb') as fh:
        head = fh.read(36)
        if head[:4] != b'BSA\x00':
            return False
        version, dir_offset, _flags, folder_count, _, _, names_len, _ = struct.unpack_from('<8I', head, 4)
        record_size = 24 if version >= 105 else 16
        fh.seek(dir_offset)
        table = fh.read(folder_count * record_size)
        folders = [struct.unpack_from('<QI', table, i * record_size) for i in range(folder_count)]
        want = tes4_hash(folder, folder=True)
        at = next((i for i, (h, _c) in enumerate(folders) if h == want), None)
        if at is None:
            return False
        before = 0
        for _h, count in folders[:at]:
            fh.seek(fh.read(1)[0] + count * 16, 1)
            before += count
        if fh.read(fh.read(1)[0]).rstrip(b'\x00').decode('latin-1').lower() != folder:
            return False
        hashes = [struct.unpack('<Q8x', fh.read(16))[0] for _ in range(folders[at][1])]
        if tes4_hash(file) not in hashes:
            return False
        for h, count in folders[at + 1:]:
            fh.seek(fh.read(1)[0] + count * 16, 1)
        names = fh.read(names_len).decode('latin-1').split('\x00')
        return names[before + hashes.index(tes4_hash(file))].lower() == file


def iter_bsa(bsa_path, wanted=None):
    """Yield (path, bytes) for every entry, or those whose lowercase path is in `wanted`.

    Reads v103/104 (zlib) and v105 (LZ4 frame) archives; an entry whose
    compressed payload is corrupt is skipped.
    """
    with open(bsa_path, 'rb') as fh:
        version, flags, entries = read_bsa_directory(fh)
        embedded = version >= 104 and flags & _ARCH_EMBED_NAME
        decompress = lz4.frame.decompress if version >= 105 else zlib.decompress
        for path, size, offset, compressed in entries:
            if wanted is not None and path.lower() not in wanted:
                continue
            fh.seek(offset)
            if embedded:
                name_len = fh.read(1)[0]
                fh.seek(name_len, 1)
                size -= 1 + name_len
            data = fh.read(size)
            try:
                yield path, decompress(data[4:]) if compressed else data
            except (zlib.error, RuntimeError):
                continue


def read_bsa_files(bsa_path, wanted_names):
    """{lowercase path: bytes} for the named entries (any case or slashes) found."""
    wanted = {w.lower().replace('/', '\\') for w in wanted_names}
    return {path.lower(): data for path, data in iter_bsa(bsa_path, wanted)}


#: Stored bytes in one pool task, and the entry count that closes one early.
_CHUNK_BYTES = 24 * 1024 * 1024
_CHUNK_FILES = 1500

#: Below this many stored bytes in a run, extracting in-process beats starting a pool.
_POOL_MIN_BYTES = 16 * 1024 * 1024


def _list_entries(bsa_path):
    """(codec, embedded names, [(path, size, offset, compressed)]) for a BSA of any generation."""
    if is_morrowind_bsa(bsa_path):
        table = read_morrowind_table(bsa_path)
        return 'zlib', False, [(name, size, start, False) for name, start, size in table]
    with open(bsa_path, 'rb') as fh:
        version, flags, entries = read_bsa_directory(fh)
    return ('lz4' if version >= 105 else 'zlib',
            bool(version >= 104 and flags & _ARCH_EMBED_NAME), entries)


def _out_path(base_dir, filepath) -> str:
    """Where an archive path lands under `base_dir`, one real component per backslash-separated part."""
    category, rest = split_category(filepath)
    return str(Path(base_dir, category, *rest.replace('/', '\\').split('\\')))


def _plan_archive(bsa_path, base_dir, manifest, force):
    """A job for one archive, or a finished stats dict when it is cached or unreadable.

    A job carries `entries` [(out_path, size, offset, compressed)] to write and
    counts the lip files it skips.
    """
    bsa_key, bsa_size = bsa_path.name, bsa_path.stat().st_size
    prev = manifest['extracted_bsas'].get(bsa_key)
    if (not force and prev and prev.get('size') == bsa_size
            and prev.get('path', str(bsa_path)) == str(bsa_path)):
        print(f"  Skipping {bsa_key} (already extracted, {prev['file_count']} files)")
        return {'total_files': 0, 'extracted': 0, 'skipped_cached': True, 'skipped': 0, 'errors': 0}
    print(f"  Extracting {bsa_key} ({bsa_size / 1024 / 1024:.1f} MB)...")
    stats = {'total_files': 0, 'extracted': 0, 'skipped': 0, 'errors': 0, 'skipped_cached': False}
    try:
        codec, embedded, listed = _list_entries(bsa_path)
    except Exception as e:
        print(f"    ERROR opening BSA {bsa_key}: {e}")
        return stats
    wanted = [e for e in listed if should_extract_file(e[0])]
    stats.update(total_files=len(listed), skipped=len(listed) - len(wanted))
    entries = [(_out_path(base_dir, path), size, offset, comp)
               for path, size, offset, comp in sorted(wanted, key=lambda e: e[2])]
    return {'stats': stats, 'path': bsa_path, 'size': bsa_size, 'codec': codec,
            'embedded': embedded, 'entries': entries, 'shadowed': 0}


def _drop_shadowed(jobs) -> None:
    """Remove every entry a later entry (this archive's or a later one's) overwrites on disk.

    Serial extraction let the last writer win; dropping the earlier ones makes
    the parallel result the same whatever order the workers run in.
    """
    seen = set()
    for job in reversed(jobs):
        kept = []
        for entry in reversed(job['entries']):
            key = entry[0].lower()
            if key in seen:
                job['shadowed'] += 1
            else:
                seen.add(key)
                kept.append(entry)
        job['entries'] = kept[::-1]


def _chunks(entries):
    """`entries` cut into runs of about `_CHUNK_BYTES` stored bytes, in order."""
    run, weight = [], 0
    for entry in entries:
        run.append(entry)
        weight += entry[1]
        if weight >= _CHUNK_BYTES or len(run) >= _CHUNK_FILES:
            yield run
            run, weight = [], 0
    if run:
        yield run


def _submit(pool, job, chunk):
    """Run one chunk on the pool, or in-process (a finished future) when there is none."""
    args = (str(job['path']), job['codec'], job['embedded'], chunk)
    if pool is not None:
        return pool.submit(extract_chunk, *args)
    done = Future()
    done.set_result(extract_chunk(*args))
    return done


def _finish_archive(job, futures, base_dir, manifest) -> dict:
    """Collect an archive's chunk results, record it in the manifest and return its stats."""
    stats, shown = job['stats'], 0
    for future in futures:
        extracted, corrupt, errors, messages = future.result()
        stats['extracted'] += extracted
        stats['errors'] += errors
        stats['total_files'] -= corrupt
        for message in messages[:max(0, 10 - shown)]:
            print(f"    ERROR writing {message}")
        shown += len(messages)
    stats['extracted'] += job['shadowed']
    manifest['extracted_bsas'][job['path'].name] = {
        'path': str(job['path']), 'size': job['size'],
        'file_count': stats['extracted'], 'total_in_bsa': stats['total_files']}
    save_manifest(base_dir, manifest)
    print(f"    Extracted {stats['extracted']} files, "
          f"skipped {stats['skipped']}, errors {stats['errors']}")
    return stats


def extract_archives(bsa_paths, extract_dir, force=False, source_name=None) -> list:
    """Extract several archives into `extract_dir/<source_name>/`; one stats dict per archive, in order.

    Every archive's files are decompressed and written by a process pool, each
    worker reading its own byte ranges; later archives still win a path clash.
    Stats: total_files, extracted, skipped, errors, skipped_cached.
    See: docs/commentary/asset_convert_bsa.md#parallel-extraction
    """
    base_dir = Path(extract_dir, source_name or '')
    manifest = load_manifest(base_dir)
    plans = [_plan_archive(Path(b), base_dir, manifest, force) for b in bsa_paths]
    jobs = [p for p in plans if 'entries' in p]
    _drop_shadowed(jobs)
    stored = sum(e[1] for job in jobs for e in job['entries'])
    pool = ProcessPoolExecutor(worker_count()) if stored >= _POOL_MIN_BYTES else None
    try:
        pending = {id(job): [_submit(pool, job, c) for c in _chunks(job['entries'])] for job in jobs}
        return [_finish_archive(p, pending[id(p)], base_dir, manifest) if 'entries' in p else p
                for p in plans]
    finally:
        if pool is not None:
            pool.shutdown()


def extract_bsa(bsa_path, extract_dir, force=False, source_name=None):
    """Extract one BSA; `extract_archives` for a single archive. Returns its stats dict."""
    return extract_archives([bsa_path], extract_dir, force, source_name)[0]


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
    # Distinguish "another plugin owns these BSAs" from "we found nothing" —
    # the former is the intended outcome, not a missing-asset warning.
    if Path(source_file).stem.lower() in _SUPPRESSED_BSA_BASES:
        print(f"Skipping BSA extraction for {source_file}: its archives are "
              f"extracted by the plugin they were merged into "
              f"(see _MERGED_DLC_BSA_BASES)")
        return {'bsas_found': 0, 'suppressed': True}

    bsa_files = get_bsa_files(data_path, source_file)

    if not bsa_files:
        print(f"No BSA files found for {source_file} in {data_path}")
        return {'bsas_found': 0}

    print(f"Found {len(bsa_files)} BSA(s) for {source_file} in {data_path}:")
    for b in bsa_files:
        print(f"  {b.name} ({b.stat().st_size / 1024 / 1024:.1f} MB)")

    totals = {'bsas_found': len(bsa_files), 'bsas_extracted': 0,
              'bsas_cached': 0, 'total_extracted': 0, 'total_errors': 0}

    # Where this plugin's assets live. For a game-Data plugin that is a folder
    # named after it; an imported mod's plugins share their MOD's folder. Ask
    # the resolver rather than assuming (see output_layout).
    try:
        from output_layout import asset_root
        asset_dir_name = asset_root(extract_dir, source_file).name
    except ImportError:
        asset_dir_name = source_file
    _warn_mixed_sources(Path(extract_dir) / asset_dir_name, data_path, bsa_files)

    for stats in extract_archives(bsa_files, extract_dir, force=force, source_name=asset_dir_name):
        if stats.get('skipped_cached'):
            totals['bsas_cached'] += 1
        else:
            totals['bsas_extracted'] += 1
            totals['total_extracted'] += stats['extracted']
            totals['total_errors'] += stats['errors']

    print(f"\nBSA extraction complete: {totals['bsas_extracted']} extracted, "
          f"{totals['bsas_cached']} cached, "
          f"{totals['total_extracted']} files written")

    totals['music'] = extract_loose_music(source_file, data_path, extract_dir,
                                          asset_dir_name, force=force)
    if any(is_morrowind_bsa(b) for b in bsa_files):
        totals.update(extract_loose_audio(data_path, extract_dir,
                                          asset_dir_name, source_file))
    return totals


# ---------------------------------------------------------------------------
# Loose music
# ---------------------------------------------------------------------------

# Music is the one asset class no record names a file for: the TES4 engine
# scans the Data/Music/<Category>/ folder and shuffles whatever it finds, so
# XCMT/SNAM carry only the 3-value {Default, Public, Dungeon} enum.  Measured:
# Oblivion.esm references ZERO music paths and Nehrim.esm references 35 (all
# from SCPT/SOUN) while 76 files sit on disk -- so an "only take what the plugin
# references" filter would ship nothing for Oblivion and 36% for Nehrim,
# silencing every town and every fight.  The whole folder comes across.
MUSIC_EXTS = ('.mp3', '.wav', '.xwm')


def _is_masterless(extract_dir, source_file) -> bool:
    """True when this plugin declares no TES4 masters.

    Music lives loose in a SHARED Data folder: Nehrim's holds five plugins
    (Nehrim.esm, ORN.esp, Translation.esp, ...) beside one Music folder.  Only
    the game's own masterless plugin owns it -- attributing it to every plugin
    in the folder would re-ship 329 MB per dependent ESP.
    """
    try:
        from output_layout import record_dir
        from core.plugin_masters import masters_from_export_header
        return not masters_from_export_header(
            str(record_dir(str(extract_dir), source_file)))
    except Exception:
        return False


def extract_loose_music(source_file, data_path, extract_dir, asset_dir_name,
                        force=False) -> dict:
    """Copy loose Data/Music into extract_dir/<plugin>/music/.

    Loose-only by design: the BSA pass already routes a music entry here via
    ASSET_CATEGORIES, and Nehrim/Oblivion both ship music as loose files that no
    BSA contains.  Files already present are left alone so a re-run is cheap.
    """
    stats = {'copied': 0, 'cached': 0, 'bytes': 0, 'skipped_reason': None}

    if not _is_masterless(extract_dir, source_file):
        stats['skipped_reason'] = 'has masters'
        return stats

    src_root = Path(data_path) / 'Music'
    if not src_root.is_dir():
        stats['skipped_reason'] = 'no loose Music folder'
        return stats

    dst_root = Path(extract_dir) / asset_dir_name / 'music'
    for root, _dirs, files in os.walk(src_root):
        for fname in files:
            src = Path(root) / fname
            if src.suffix.lower() not in MUSIC_EXTS:
                continue
            dst = dst_root / src.relative_to(src_root)
            if dst.is_file() and not force and dst.stat().st_size:
                stats['cached'] += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            stats['copied'] += 1
            stats['bytes'] += src.stat().st_size

    if stats['copied'] or stats['cached']:
        print(f"Loose music: {stats['copied']} copied, {stats['cached']} cached "
              f"({stats['bytes'] / 1024 / 1024:.1f} MB)")
    return stats


# ---------------------------------------------------------------------------
# Asset utilities
# ---------------------------------------------------------------------------

def get_asset_category(asset_path: str) -> str:
    """Return the top-level category ('meshes', 'textures', 'sound', etc.)
    for a BSA-relative asset path.  Case-insensitive."""
    parts = asset_path.replace('\\', '/').split('/')
    return parts[0].lower() if parts else ''


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
        from asset_convert.audio.audio_converter import find_voice_map, organize_voice_files
        source_dir = Path(args.extract_dir) / args.source_file
        dest_dir = Path(args.organize_voice)
        organize_voice_files(source_dir, dest_dir, args.source_file,
                             convert_audio=not args.no_convert_audio,
                             ffmpeg_path=args.ffmpeg,
                             formid_index=args.formid_index,
                             voice_map=find_voice_map('output', args.source_file))
