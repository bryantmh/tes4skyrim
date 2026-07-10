"""Pack converted output assets into Skyrim SE-compatible BSA archives.

Produces up to three BSAs in ``output/<plugin>/``, alongside the converted ESM:
  oblivion.bsa           meshes/ + misc directories (everything except textures)
  <stem> - Textures.bsa  textures/ sub-tree

Uses BSArch.exe (from xEdit / SSEEdit) for BSA5 (SSE) format creation.
BSArch is searched in common locations; pass ``bsarch_path`` to override.

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

# ---------------------------------------------------------------------------
# Staging helpers
# ---------------------------------------------------------------------------

def _link_or_copy(src: Path, dst: Path) -> None:
    """Create a hardlink dst → src; fall back to copy on cross-device error."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _stage_subdirs(
    plugin_dir: Path,
    subdir_names: 'list[str]',
    stage_root: Path,
) -> int:
    """Hardlink files from plugin_dir/<subdir>/ into stage_root/<subdir>/.

    Returns the number of files staged.
    """
    count = 0
    for name in subdir_names:
        src = plugin_dir / name
        if not src.is_dir():
            continue
        dst_base = stage_root / name
        for f in src.rglob('*'):
            if not f.is_file():
                continue
            rel = f.relative_to(src)
            dst = dst_base / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _link_or_copy(f, dst)
            count += 1
    return count


# ---------------------------------------------------------------------------
# Main packing logic
# ---------------------------------------------------------------------------

# (subdir_names_in_plugin, bsa_suffix, compress)
_BSA_SPECS: 'list[tuple[list[str], str, bool]]' = [
    (['textures'], 'Textures', False),
]

# All directory names covered by the explicit BSA specs + meshes + Sound
# (meshes are combined with misc into oblivion.bsa; Sound is excluded entirely)
_KNOWN_DIRS: frozenset = frozenset(
    n.lower()
    for spec in _BSA_SPECS
    for n in spec[0]
) | frozenset(['meshes', 'sound'])


def pack_bsas(
    source_file: str,
    output_dir: str = 'output',
    bsarch_path: str = None,
    compress_textures: bool = False,
) -> dict:
    """Pack converted assets into Skyrim SE BSA archives.

    Produces up to three BSAs inside ``output_dir/<source_name>/``:
      * ``oblivion.bsa``          from meshes/ + remaining sub-directories
      * ``<stem> - Textures.bsa`` from textures/

    The source folder structure is NOT modified; original folders are left intact.

    Args:
        source_file:        Plugin filename (e.g. 'Oblivion.esm').
        output_dir:         Root output directory (default: 'output').
        bsarch_path:        Optional explicit path to BSArch.exe.
        compress_textures:  Compress the textures BSA (-z flag). Default False.

    Returns:
        dict with keys: packed (list of BSA paths), skipped (list), errors (list).
    """
    bsarch = str(Path(__file__).parent.parent / 'tools' / 'BSArch.exe')
    if not Path(bsarch).is_file():
        msg = (
            "BSArch.exe not found.  Place BSArch.exe in tools/BSArch.exe under the "
            "project root, or set bsarchPath in conversion_config.json, or add "
            "BSArch.exe to the system PATH."
        )
        print(f"  ERROR: {msg}")
        return {'packed': [], 'skipped': [], 'errors': [msg]}

    print(f"  BSArch: {bsarch}")

    source_name = Path(source_file).name
    plugin_dir  = Path(output_dir).resolve() / source_name
    if not plugin_dir.is_dir():
        msg = f"Plugin output directory not found: {plugin_dir}"
        print(f"  ERROR: {msg}")
        return {'packed': [], 'skipped': [], 'errors': [msg]}

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

    # Combine meshes + misc into a single BSA named 'oblivion.bsa'
    combined_dirs = ['meshes'] + misc_dirs
    specs.append((combined_dirs, '__OBLIVION__', False))

    results: dict = {'packed': [], 'skipped': [], 'errors': []}

    for subdir_names, bsa_suffix, compress in specs:
        if bsa_suffix == '__OBLIVION__':
            # User-requested: combine meshes + misc into 'oblivion.bsa'
            bsa_name = 'Oblivion.bsa'
        else:
            bsa_name = f"{stem} - {bsa_suffix}.bsa"
        bsa_path = plugin_dir / bsa_name

        # Check at least one source dir has files
        has_content = any(
            (plugin_dir / d).is_dir() and
            any((plugin_dir / d).rglob('*'))
            for d in subdir_names
        )
        if not has_content:
            print(f"  SKIP  {bsa_name} (no source content)")
            results['skipped'].append(bsa_name)
            continue

        # Create staging directory
        stage_root = plugin_dir / f'_bsa_staging_{bsa_suffix.lower()}'
        if stage_root.exists():
            shutil.rmtree(stage_root)
        stage_root.mkdir(parents=True)

        try:
            n_files = _stage_subdirs(plugin_dir, subdir_names, stage_root)
            if n_files == 0:
                print(f"  SKIP  {bsa_name} (staging produced 0 files)")
                results['skipped'].append(bsa_name)
                continue

            print(f"  PACK  {bsa_name}  ({n_files} files "
                  f"from {', '.join(subdir_names)})")

            cmd = [bsarch, 'pack', str(stage_root), str(bsa_path), '-sse', '-mt']
            if compress:
                cmd.append('-z')

            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,      # 10-minute cap for very large archives
                **POPEN_FLAGS,
            )

            if completed.returncode != 0:
                # BSArch may emit errors on stderr or stdout
                err_out = (completed.stderr or completed.stdout or '').strip()
                err_msg = f"{bsa_name}: BSArch exit {completed.returncode}: {err_out[:200]}"
                print(f"  ERROR {err_msg}")
                results['errors'].append(err_msg)
                if bsa_path.exists():
                    bsa_path.unlink()   # remove partial archive
            else:
                size_mb = bsa_path.stat().st_size / 1_048_576 if bsa_path.exists() else 0
                print(f"  OK    {bsa_name}  ({size_mb:.1f} MB)")
                results['packed'].append(str(bsa_path))

        except subprocess.TimeoutExpired:
            err_msg = f"{bsa_name}: BSArch timed out after 600 s"
            print(f"  ERROR {err_msg}")
            results['errors'].append(err_msg)
        except Exception as exc:
            err_msg = f"{bsa_name}: {exc}"
            print(f"  ERROR {err_msg}")
            results['errors'].append(err_msg)
        finally:
            if stage_root.exists():
                shutil.rmtree(stage_root, ignore_errors=True)

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
    a = parser.parse_args()
    r = pack_bsas(a.source_file, output_dir=a.output_dir,
                  bsarch_path=a.bsarch,
                  compress_textures=a.compress_textures)
    print(f"\nPacked: {len(r['packed'])}  Skipped: {len(r['skipped'])}  Errors: {len(r['errors'])}")
