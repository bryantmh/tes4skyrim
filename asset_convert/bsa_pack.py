"""Pack converted output assets into Skyrim SE-compatible BSA archives.

Produces BSAs in ``output/<plugin>/``, alongside the converted ESM:
  Oblivion.bsa           meshes/ + misc directories (everything except textures)
  <stem> - Textures.bsa  textures/ sub-tree

Uses BSArch.exe (from xEdit / SSEEdit) for BSA5 (SSE) format creation.
BSArch is searched in common locations; pass ``bsarch_path`` to override.

Size limit / overflow
---------------------
The BSA format addresses file data with 32-bit offsets, so a single archive
cannot exceed 2 GiB (2,147,483,648 bytes).  Content that does not fit is split
across additional archives.  Skyrim only auto-mounts ``<PluginStem>.bsa`` and
``<PluginStem> - Textures.bsa`` for a plugin that is in the load order, so each
overflow archive is paired with a generated dummy ESL "loader" plugin whose
stem matches the archive name:

  oblivion_loader.esl     mounts oblivion_loader.bsa / oblivion_loader - Textures.bsa
  oblivion_loader_1.esl   mounts oblivion_loader_1.bsa / ...

Staging strategy: for each BSA a temporary directory is created inside
``output/<plugin>/_bsa_staging_<type>/`` containing only hardlinks to the
relevant files (near-instant on the same drive).  The directory is removed
after BSArch finishes.  Hardlinks fall back to full copies if the staging
directory is on a different drive.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subprocess_flags import POPEN_FLAGS  # noqa: E402
from tes5_import.writer import pack_tes4_header  # noqa: E402

# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------

# Hard engine limit: BSA file-data offsets are 32-bit.
BSA_HARD_LIMIT = 2_147_483_648

# BSArch writes a header, a folder table, a file table and two name tables in
# addition to the raw file bytes.  Budget for that overhead (plus per-file
# alignment slack) so the finished archive stays under the hard limit.
BSA_OVERHEAD_BUDGET = 64 * 1024 * 1024          # 64 MiB
BSA_SIZE_LIMIT = BSA_HARD_LIMIT - BSA_OVERHEAD_BUDGET   # ~2.0 GiB of payload


# ---------------------------------------------------------------------------
# Staging helpers
# ---------------------------------------------------------------------------

def _link_or_copy(src: Path, dst: Path) -> None:
    """Create a hardlink dst → src; fall back to copy on cross-device error."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _collect_files(plugin_dir: Path, subdir_names: 'list[str]') -> 'list[tuple[Path, Path, int]]':
    """Enumerate every file under plugin_dir/<subdir>/ for packing.

    Returns a list of (absolute_source, archive_relative_path, size_bytes),
    sorted by archive path so binning is deterministic across runs.
    """
    out: 'list[tuple[Path, Path, int]]' = []
    for name in subdir_names:
        src = plugin_dir / name
        if not src.is_dir():
            continue
        for f in src.rglob('*'):
            if not f.is_file():
                continue
            # Archive path keeps the top-level dir (meshes/..., textures/...)
            rel = Path(name) / f.relative_to(src)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            out.append((f, rel, size))
    out.sort(key=lambda t: str(t[1]).lower())
    return out


def _bin_files(
    files: 'list[tuple[Path, Path, int]]',
    limit: int = BSA_SIZE_LIMIT,
) -> 'list[list[tuple[Path, Path, int]]]':
    """Split files into ordered bins, each with a total payload under `limit`.

    Greedy first-fit-decreasing is deliberately NOT used: keeping the natural
    path order groups related assets into the same archive, which makes the
    split reproducible and keeps a given directory mostly in one BSA.

    A single file larger than `limit` cannot be split; it gets a bin of its own
    and the caller is expected to warn about it.
    """
    bins: 'list[list[tuple[Path, Path, int]]]' = []
    current: 'list[tuple[Path, Path, int]]' = []
    current_size = 0

    for entry in files:
        size = entry[2]
        if current and current_size + size > limit:
            bins.append(current)
            current = []
            current_size = 0
        current.append(entry)
        current_size += size

    if current:
        bins.append(current)
    return bins


def _stage_bin(
    entries: 'list[tuple[Path, Path, int]]',
    stage_root: Path,
) -> int:
    """Hardlink one bin's files into stage_root, preserving archive paths."""
    count = 0
    for src, rel, _size in entries:
        dst = stage_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        _link_or_copy(src, dst)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Dummy ESL loader plugins
# ---------------------------------------------------------------------------

ESL_FLAG = 0x0200   # "Light Master" (ESL) flag on the TES4 header record
ESM_FLAG = 0x0001


def write_loader_esl(path: Path, description: str = "") -> None:
    """Write a minimal, record-free ESL whose only job is to mount a BSA.

    Skyrim mounts ``<stem>.bsa`` and ``<stem> - Textures.bsa`` for every plugin
    in the load order.  An empty ESL is the cheapest way to get an extra BSA
    mounted: it holds no records, so it consumes no FormID space, and the ESL
    flag keeps it out of the 255-plugin limit.
    """
    header = pack_tes4_header(
        masters=[],
        num_records=0,
        next_object_id=0x800,
        description=description or "BSA loader (no records)",
        is_esm=True,
    )
    # pack_tes4_header only sets the ESM flag; add the ESL/light flag so the
    # plugin loads out of the ESL space and never eats a load-order slot.
    # TES4 header layout: sig[4] size[4] flags[4] ...
    flags = int.from_bytes(header[8:12], 'little') | ESM_FLAG | ESL_FLAG
    header = header[:8] + flags.to_bytes(4, 'little') + header[12:]
    path.write_bytes(header)


# ---------------------------------------------------------------------------
# Main packing logic
# ---------------------------------------------------------------------------

# (subdir_names_in_plugin, bsa_suffix, compress)
#   bsa_suffix '' means the plugin-stem archive (Oblivion.bsa)
_BSA_SPECS: 'list[tuple[list[str], str, bool]]' = [
    (['textures'], 'Textures', False),
]

# Directory names already claimed by an explicit BSA spec, plus 'meshes' (which
# is added to the main spec by hand).  Everything else in the plugin output dir
# — sound/, scripts/, etc. — is auto-discovered as a misc dir and packed into
# the main archive alongside meshes.
_KNOWN_DIRS: frozenset = frozenset(
    n.lower()
    for spec in _BSA_SPECS
    for n in spec[0]
) | frozenset(['meshes'])


def _loader_stem(index: int) -> str:
    """Name of the Nth overflow loader plugin (0-based)."""
    return 'oblivion_loader' if index == 0 else f'oblivion_loader_{index}'


def _run_bsarch(
    bsarch: str,
    stage_root: Path,
    bsa_path: Path,
    compress: bool,
    results: dict,
) -> bool:
    """Invoke BSArch on a staged directory.  Returns True on success."""
    bsa_name = bsa_path.name
    cmd = [bsarch, 'pack', str(stage_root), str(bsa_path), '-sse', '-mt']
    if compress:
        cmd.append('-z')

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,     # 30-minute cap for very large archives
            **POPEN_FLAGS,
        )
    except subprocess.TimeoutExpired:
        err_msg = f"{bsa_name}: BSArch timed out after 1800 s"
        print(f"  ERROR {err_msg}")
        results['errors'].append(err_msg)
        return False
    except Exception as exc:
        err_msg = f"{bsa_name}: {exc}"
        print(f"  ERROR {err_msg}")
        results['errors'].append(err_msg)
        return False

    if completed.returncode != 0:
        # BSArch may emit errors on stderr or stdout
        err_out = (completed.stderr or completed.stdout or '').strip()
        err_msg = f"{bsa_name}: BSArch exit {completed.returncode}: {err_out[:200]}"
        print(f"  ERROR {err_msg}")
        results['errors'].append(err_msg)
        if bsa_path.exists():
            bsa_path.unlink()   # remove partial archive
        return False

    size = bsa_path.stat().st_size if bsa_path.exists() else 0
    if size > BSA_HARD_LIMIT:
        err_msg = (
            f"{bsa_name}: archive is {size:,} bytes, over the "
            f"{BSA_HARD_LIMIT:,}-byte BSA limit — Skyrim cannot read it"
        )
        print(f"  ERROR {err_msg}")
        results['errors'].append(err_msg)
        return False

    print(f"  OK    {bsa_name}  ({size / 1_048_576:.1f} MB)")
    results['packed'].append(str(bsa_path))
    return True


def pack_bsas(
    source_file: str,
    output_dir: str = 'output',
    bsarch_path: str = None,
    compress_textures: bool = False,
    size_limit: int = BSA_SIZE_LIMIT,
) -> dict:
    """Pack converted assets into Skyrim SE BSA archives.

    Produces, inside ``output_dir/<source_name>/``:
      * ``Oblivion.bsa``          from meshes/ + remaining sub-directories
      * ``<stem> - Textures.bsa`` from textures/

    Content that would push an archive past the 2 GiB BSA limit spills into
    additional archives, each paired with a generated dummy ESL loader plugin
    (``oblivion_loader.esl``, ``oblivion_loader_1.esl``, …) so Skyrim mounts it.

    The source folder structure is NOT modified; original folders are left intact.

    Args:
        source_file:        Plugin filename (e.g. 'Oblivion.esm').
        output_dir:         Root output directory (default: 'output').
        bsarch_path:        Optional explicit path to BSArch.exe.
        compress_textures:  Compress the textures BSA (-z flag). Default False.
        size_limit:         Max payload bytes per archive (default ~2 GiB minus
                            BSA metadata overhead).

    Returns:
        dict with keys: packed (list of BSA paths), skipped (list),
        errors (list), loaders (list of generated .esl paths).
    """
    bsarch = bsarch_path or str(Path(__file__).resolve().parent.parent
                                / 'external' / 'bsarch' / 'BSArch.exe')
    if not Path(bsarch).is_file():
        msg = (
            "BSArch.exe not found.  Place BSArch.exe in external/bsarch/BSArch.exe "
            "under the project root, or set bsarchPath in conversion_config.json, or "
            "add BSArch.exe to the system PATH."
        )
        print(f"  ERROR: {msg}")
        return {'packed': [], 'skipped': [], 'errors': [msg], 'loaders': []}

    print(f"  BSArch: {bsarch}")

    source_name = Path(source_file).name
    plugin_dir  = Path(output_dir).resolve() / source_name
    if not plugin_dir.is_dir():
        msg = f"Plugin output directory not found: {plugin_dir}"
        print(f"  ERROR: {msg}")
        return {'packed': [], 'skipped': [], 'errors': [msg], 'loaders': []}

    stem = Path(source_name).stem   # 'Oblivion'

    # Build the misc spec: any non-empty dirs not covered by the known specs
    misc_dirs = sorted(
        d.name for d in plugin_dir.iterdir()
        if d.is_dir()
        and d.name.lower() not in _KNOWN_DIRS
        and not d.name.startswith('_bsa_staging_')
        and any(d.rglob('*'))  # non-empty
    )

    specs = list(_BSA_SPECS)
    # Override compress for textures if requested
    if compress_textures:
        specs = [
            (dirs, suffix, True if suffix == 'Textures' else compress)
            for dirs, suffix, compress in specs
        ]

    # Combine meshes + misc into a single BSA named 'Oblivion.bsa'
    specs.append((['meshes'] + misc_dirs, '', False))

    results: dict = {'packed': [], 'skipped': [], 'errors': [], 'loaders': []}

    # Overflow archives are mounted by generated loader ESLs.  A loader plugin
    # mounts both '<stem>.bsa' and '<stem> - Textures.bsa', so each spec keeps
    # its own overflow counter and they share the loader plugins by index.
    loaders_needed = 0

    for subdir_names, bsa_suffix, compress in specs:
        base_name = f"{stem} - {bsa_suffix}.bsa" if bsa_suffix else f"{stem}.bsa"

        files = _collect_files(plugin_dir, subdir_names)
        if not files:
            print(f"  SKIP  {base_name} (no source content)")
            results['skipped'].append(base_name)
            continue

        bins = _bin_files(files, size_limit)
        total = sum(f[2] for f in files)

        if len(bins) > 1:
            print(f"  SPLIT {base_name}: {total / 1_048_576:.1f} MB of "
                  f"{', '.join(subdir_names)} exceeds the "
                  f"{size_limit / 1_048_576:.0f} MB per-archive budget "
                  f"-> {len(bins)} archives")

        for bin_idx, entries in enumerate(bins):
            bin_size = sum(e[2] for e in entries)

            # Warn on a single file that cannot possibly fit.
            if bin_size > size_limit and len(entries) == 1:
                print(f"  WARN  {entries[0][1]} is {bin_size / 1_048_576:.1f} MB, "
                      f"larger than a whole BSA — it cannot be split")

            if bin_idx == 0:
                # First bin keeps the name the real plugin auto-mounts.
                bsa_path = plugin_dir / base_name
            else:
                # Overflow: mounted by oblivion_loader[_N].esl
                loader_idx = bin_idx - 1
                lstem = _loader_stem(loader_idx)
                loaders_needed = max(loaders_needed, loader_idx + 1)
                bsa_path = plugin_dir / (
                    f"{lstem} - {bsa_suffix}.bsa" if bsa_suffix else f"{lstem}.bsa"
                )

            stage_root = plugin_dir / (
                f"_bsa_staging_{(bsa_suffix or 'main').lower()}_{bin_idx}"
            )
            if stage_root.exists():
                shutil.rmtree(stage_root)
            stage_root.mkdir(parents=True)

            try:
                n_files = _stage_bin(entries, stage_root)
                print(f"  PACK  {bsa_path.name}  ({n_files} files, "
                      f"{bin_size / 1_048_576:.1f} MB "
                      f"from {', '.join(subdir_names)})")
                _run_bsarch(bsarch, stage_root, bsa_path, compress, results)
            except Exception as exc:
                err_msg = f"{bsa_path.name}: {exc}"
                print(f"  ERROR {err_msg}")
                results['errors'].append(err_msg)
            finally:
                if stage_root.exists():
                    shutil.rmtree(stage_root, ignore_errors=True)

    # Generate one dummy ESL per overflow slot so the game mounts those BSAs.
    for i in range(loaders_needed):
        esl_path = plugin_dir / f"{_loader_stem(i)}.esl"
        try:
            write_loader_esl(esl_path, description=f"BSA loader for {source_name}")
            print(f"  OK    {esl_path.name}  (BSA loader plugin)")
            results['loaders'].append(str(esl_path))
        except Exception as exc:
            err_msg = f"{esl_path.name}: {exc}"
            print(f"  ERROR {err_msg}")
            results['errors'].append(err_msg)

    # Remove stale overflow archives and loaders left by a previous, larger run.
    # This matters beyond tidiness: a later run that needs the loader slot again
    # would otherwise re-create oblivion_loader.esl on top of a stale
    # oblivion_loader.bsa, silently serving assets from the old conversion.
    written = {Path(p).name.lower() for p in results['packed']}
    for stale in sorted(plugin_dir.glob('oblivion_loader*')):
        if stale.suffix.lower() not in ('.bsa', '.esl'):
            continue
        if stale.suffix.lower() == '.esl':
            keep = stale.name in {Path(p).name for p in results['loaders']}
        else:
            keep = stale.name.lower() in written
        if not keep:
            stale.unlink()
            print(f"  CLEAN {stale.name}  (no longer needed)")

    if loaders_needed:
        print(f"\n  NOTE: {loaders_needed} loader plugin(s) generated. "
              f"They must be enabled in the load order (after {source_name}) "
              f"for the overflow BSAs to be mounted.")

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Pack output assets into Skyrim SE BSA archives',
    )
    parser.add_argument('source_file', help='Plugin filename (e.g. Oblivion.esm)')
    parser.add_argument('--output-dir', default='output',
                        help='Root output directory (default: output)')
    parser.add_argument('--bsarch', default=None, metavar='PATH',
                        help='Path to BSArch.exe (auto-detected by default)')
    parser.add_argument('--compress-textures', action='store_true',
                        help='Compress the textures BSA (-z flag)')
    parser.add_argument('--size-limit', type=int, default=BSA_SIZE_LIMIT,
                        metavar='BYTES',
                        help=f'Max payload bytes per BSA (default: {BSA_SIZE_LIMIT})')
    a = parser.parse_args()
    r = pack_bsas(a.source_file, output_dir=a.output_dir,
                  bsarch_path=a.bsarch,
                  compress_textures=a.compress_textures,
                  size_limit=a.size_limit)
    print(f"\nPacked: {len(r['packed'])}  Skipped: {len(r['skipped'])}  "
          f"Loaders: {len(r['loaders'])}  Errors: {len(r['errors'])}")
