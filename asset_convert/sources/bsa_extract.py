"""Extract assets from Oblivion BSA archives with caching support.

Extracts meshes, textures, and sounds from BSA files into the export directory,
organized by source file. Uses a manifest file to track what has already been
extracted, preventing redundant re-extraction on reruns.

Uses a native BSA reader (no external dependencies) that handles both
uncompressed and zlib-compressed Oblivion BSAs.

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
from pathlib import Path
from asset_convert.sources.bsa_extract_morrowind import (
    copy_loose_sounds, is_morrowind_bsa, iter_bsa as iter_morrowind_bsa)
from asset_convert.audio.audio_converter import (
    organize_voice_files,
)
from core.worker_budget import worker_count

# Worker count used by all parallel operations in this module.
_WORKER_COUNT = worker_count()


def read_bsa_files(bsa_path, wanted_names):
    """Read specific files out of a TES4/FO3/Skyrim LE/Skyrim SE BSA
    (versions 103/104/105) without extracting the archive.

    Layout differences (verified against xEdit wbBSArchive.pas):
      - v105 (SSE) folder record = hash(8) count(4) unk(4) offset(8);
        v103/104 = hash(8) count(4) offset(4)
      - archiveFlags 0x100 (v104/105): file data prefixed with bstring name
      - compression: zlib (v103/104), LZ4 *frame* (v105); compressed data =
        u32 uncompressed size + payload

    `wanted_names`: full archive paths (``folder\\file``, any case/slashes).
    Returns {normalized_path: bytes} for entries found; only matched entries
    are decompressed.
    """
    wanted = {w.lower().replace('/', '\\') for w in wanted_names}
    found = {}
    with open(bsa_path, 'rb') as fh:
        head = fh.read(36)
        if head[:4] != b'BSA\x00':
            raise ValueError(f'Not a BSA file: {bsa_path}')
        (version, dir_offset, flags, folder_count, _file_count, _,
         total_fname_len, _) = struct.unpack_from('<IIIIIIII', head, 4)
        compress_default = bool(flags & 0x0004)
        embedded_names = version >= 104 and bool(flags & 0x0100)

        fh.seek(dir_offset)
        folder_counts = []
        for _ in range(folder_count):
            if version >= 105:
                _h, cnt, _unk, _off = struct.unpack('<QIIq', fh.read(24))
            else:
                _h, cnt, _off = struct.unpack('<QII', fh.read(16))
            folder_counts.append(cnt)

        records = []   # [folder, size, offset]
        for cnt in folder_counts:
            name_len = fh.read(1)[0]
            folder = fh.read(name_len).rstrip(b'\x00').decode('latin-1')
            for _ in range(cnt):
                _h, size, offset = struct.unpack('<QII', fh.read(16))
                records.append([folder, size, offset])

        names = fh.read(total_fname_len).split(b'\x00')
        for folder, size, offset in records:
            if not names:
                break
            fname = names.pop(0).decode('latin-1')
            path = (folder + '\\' + fname if folder else fname).lower()
            if path not in wanted:
                continue
            fh.seek(offset)
            compressed = bool(size & 0x40000000) ^ compress_default
            size &= ~0x40000000
            if embedded_names:
                nlen = fh.read(1)[0]
                fh.read(nlen)
                size -= 1 + nlen
            data = fh.read(size)
            if compressed:
                if version >= 105:
                    import lz4.frame   # only needed for SSE archives
                    data = lz4.frame.decompress(data[4:])
                else:
                    data = zlib.decompress(data[4:])
            found[path] = data
            if len(found) == len(wanted):
                break
    return found


#: Extra archive bases per stem, in probe order. See: docs/commentary/asset_convert_mod_ingest.md#update-bsa
_EXTRA_BSA_BASES = {
    "nehrim": ["N", "L"],
    "falloutnv": ["Fallout", "Update"],
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


# Manifest file tracks what BSAs have been extracted
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


# ---------------------------------------------------------------------------
# BSA archive reading
# ---------------------------------------------------------------------------
def _read_bsa_directory(data, dir_offset, folder_count, compressed_by_default,
                        file_compress_flag):
    """Every file record in a BSA directory, and the offset the name block starts at.

    Returns ([(folder_name, size, offset, is_compressed)], name_block_offset).
    A record's size carries the per-file compress flag, which INVERTS the
    archive default rather than setting it.
    """
    folders = []
    pos = dir_offset
    for _ in range(folder_count):
        _, f_count, f_offset = struct.unpack_from('<QII', data, pos)
        folders.append((f_count, f_offset))
        pos += 16

    file_records = []
    pos = dir_offset + folder_count * 16
    for f_count, _ in folders:
        name_len = data[pos]
        folder_name = data[pos + 1: pos + name_len].rstrip(b'\x00').decode('latin-1')
        pos += 1 + name_len
        for _ in range(f_count):
            _, f_size, f_offset = struct.unpack_from('<QII', data, pos)
            pos += 16
            is_comp = compressed_by_default ^ bool(f_size & file_compress_flag)
            file_records.append((folder_name, f_size & ~file_compress_flag,
                                 f_offset, is_comp))
    return file_records, pos


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
    ARCH_COMPRESS  = 0x0004
    ARCH_EMBED_NAME = 0x0100
    FILE_COMPRESS  = 0x40000000  # per-file size flag that inverts default

    data = Path(bsa_path).read_bytes()
    if data[:4] != BSA_MAGIC:
        raise ValueError(f"Not a BSA file: {bsa_path}")

    (version, dir_offset, archive_flags,
     folder_count, _, _, total_file_name_len, _
    ) = struct.unpack_from('<IIIIIIII', data, 4)

    compressed_by_default = bool(archive_flags & ARCH_COMPRESS)
    embedded_names = version >= 104 and bool(archive_flags & ARCH_EMBED_NAME)

    file_records, pos = _read_bsa_directory(
        data, dir_offset, folder_count, compressed_by_default, FILE_COMPRESS)
    file_names = data[pos: pos + total_file_name_len].split(b'\x00')

    # --- Yield files ---
    for idx, (folder_name, f_size, f_offset, is_comp) in enumerate(file_records):
        if idx >= len(file_names):
            break
        file_name = file_names[idx].decode('latin-1')
        filepath   = folder_name + '\\' + file_name if folder_name else file_name

        start, size = f_offset, f_size
        if embedded_names:
            start += 1 + data[f_offset]
            size -= 1 + data[f_offset]
        raw = data[start: start + size]
        if is_comp:
            try:
                raw = zlib.decompress(raw[4:])   # first 4 bytes = uncompressed size
            except zlib.error:
                continue   # skip corrupt/unsupported compressed entry

        yield filepath, raw


def _open_archive(bsa_path):
    """An iterator of (path, bytes) for a BSA of any supported generation.

    Morrowind's archive shares no structure with Oblivion's, so it is read by
    its own module rather than by a branch inside this one.
    """
    if is_morrowind_bsa(bsa_path):
        return iter_morrowind_bsa(bsa_path)
    return _iter_bsa(bsa_path)


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
    # `source_name` is an already-resolved FOLDER name from the caller,
    # not a raw plugin name.
    base_dir = extract_dir / source_name if source_name else extract_dir   # noqa: plugin-path (resolved folder)
    manifest = load_manifest(base_dir)

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
        file_iter = _open_archive(bsa_path)
    except Exception as e:
        print(f"    ERROR opening BSA {bsa_key}: {e}")
        return stats

    for filepath, file_data in file_iter:
        stats['total_files'] += 1

        if not should_extract_file(filepath):
            stats['skipped'] += 1
            continue

        category, rest = split_category(filepath)

        # BSA internal paths are always Bethesda/Windows-style backslash-
        # separated, regardless of host OS -- os.sep is '/' on Linux, so a
        # plain '/'-only replace leaves every inner backslash as a literal
        # character in one flat filename instead of real subdirectories.
        # Split explicitly and rejoin with Path so every segment becomes a
        # real path component on any platform.
        out_path = base_dir / category
        for part in rest.replace('/', '\\').split('\\'):
            out_path = out_path / part

        try:
            os.makedirs(out_path.parent, exist_ok=True)
            # UNLINK first, never truncate in place.  An ordered merge seeds
            # its base by HARD-LINKING (mod_ingest.seed_from_export), so an
            # extracted file can share an inode with the base export tree --
            # `write_bytes` alone would truncate through the link and rewrite
            # export/<base>/ with this mod's content.  Breaking the link is
            # what the tree's other two writers already do (_place_payload,
            # _link_or_copy); this one has to match them.
            if out_path.is_file():
                out_path.unlink()
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
    save_manifest(base_dir, manifest)

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

    print(f"Found {len(bsa_files)} BSA(s) for {source_file}:")
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

    for bsa_file in bsa_files:
        stats = extract_bsa(bsa_file, extract_dir, force=force,
                            source_name=asset_dir_name)
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
        owned = _owned_sounds(extract_dir, source_file)
        totals['sounds'] = copy_loose_sounds(
            data_path, Path(extract_dir) / asset_dir_name, owned)
        print(f"Loose Morrowind sounds: {totals['sounds']} of {len(owned)} "
              f"owned files copied")
    return totals


def _owned_sounds(extract_dir, source_file) -> set:
    """The loose sounds this plugin ships: what it names, less what a master names.

    Replaces the masterless gate loose music still uses: music is folder-scanned
    by the engine and has no per-file owner, but every sound is named by a SOUN,
    so an expansion ships exactly the files it adds.
    See: docs/commentary/tes4_export_morrowind.md#which-sounds-a-plugin-ships
    """
    from output_layout import record_dir
    from asset_convert.lod.terrain_lod import master_names
    from asset_convert.sources.morrowind_sound_scope import owned_files
    own = str(record_dir(str(extract_dir), source_file))
    masters = [str(record_dir(str(extract_dir), name))
               for name in master_names(own)]
    return owned_files(own, masters)


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
        from asset_convert.lod.terrain_lod import master_names
        return not master_names(record_dir(str(extract_dir), source_file))
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
        from asset_convert.audio.audio_converter import find_voice_map
        source_dir = Path(args.extract_dir) / args.source_file
        dest_dir = Path(args.organize_voice)
        organize_voice_files(source_dir, dest_dir, args.source_file,
                             convert_audio=not args.no_convert_audio,
                             ffmpeg_path=args.ffmpeg,
                             formid_index=args.formid_index,
                             voice_map=find_voice_map('output', args.source_file))
