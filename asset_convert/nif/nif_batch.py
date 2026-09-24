"""Batch NIF conversion: the worker pool, its progress and its report.

Also owns the PyFFI warning capture, because a converted mesh's warnings are
only ever read in aggregate at the end of a run: the handler swallows every
WARNING and ERROR record, and the categoriser turns the pile into per-cause
counts. PyFFI's INFO progress chatter is not captured.

See: docs/commentary/performance.md
See: docs/commentary/asset_convert_nif.md#pyffi-log-capture
"""

import argparse
import collections as _collections
import logging as _logging
import multiprocessing as mp
import os
from pathlib import Path

from asset_convert.collision.mesh_scan_fragments import set_fragment_dir
from asset_convert.nif.nif_converter import convert_nif
from asset_convert.game_paths import current_namespace, set_namespace
from asset_convert.nif.shaders import (default_normal_texture, SPEC_STRENGTH,
                                       master_texture_roots)
from core.process_job import join_pool_job
from core.worker_budget import worker_count

#: Path segments (case-insensitive) whose NIFs batch conversion never touches.
SKIP_PATHS = frozenset({
    'menus',
    'creatures',
    'characters'
})

#: Pool size for a batch run. See: docs/commentary/performance.md
WORKER_COUNT = worker_count()


#: Warnings seen so far, reset per file. Each worker process has its own copy.
worker_warn_log: list = []


class _PyFFICapture(_logging.Handler):
    """Capture PyFFI log messages at WARNING+ without printing them.

    Carries its own level: constructing any PyFFI Toaster lowers the shared
    'pyffi' logger to INFO, which the handler level overrides.
    See: docs/commentary/asset_convert_nif.md#pyffi-log-capture
    """

    def __init__(self) -> None:
        """Install at WARNING so INFO chatter cannot reach `emit`."""
        super().__init__(level=_logging.WARNING)

    def emit(self, record: _logging.LogRecord) -> None:
        """Accumulate the message instead of printing it."""
        worker_warn_log.append(record.getMessage())


def pyffi_capture_init(namespace: str = None, scan_dir: str = None) -> None:
    """Install silent PyFFI log capture and the parent's asset namespace.

    Called as a multiprocessing.Pool initializer (once per worker) and
    directly before single-worker processing. The worker joins the parent's
    containment job first, so it cannot outlive a parent that dies without
    cleanup; that is a no-op off Windows.

    A worker is a fresh interpreter, so without `namespace` it rewrites every
    texture path under the default.  `scan_dir` receives its scan records.
    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    """
    join_pool_job()
    if namespace:
        set_namespace(namespace)
    set_fragment_dir(scan_dir)

    global worker_warn_log
    worker_warn_log = []
    pyffi_log = _logging.getLogger('pyffi')
    pyffi_log.propagate = False
    pyffi_log.setLevel(_logging.WARNING)
    pyffi_log.handlers = []
    pyffi_log.addHandler(_PyFFICapture())


_WARN_CATEGORIES = {
    'improper_geometry':           lambda m: m.startswith('improper'),
    'block_size_check':            lambda m: 'block size check' in m,
    'end_of_file_not_reached':     lambda m: 'end of file not reached' in m,
    'nan_generic':                 lambda m: 'nan' in m,
    'mopp_read_fail':              lambda m: 'bhkmoppbvtreeshape' in m or ('mopp' in m and ('fail' in m or 'error' in m)),
    'havok_block_invalid':         lambda m: 'bhk' in m and ('invalid' in m or 'not in nif' in m),
    'havok_shape':                 lambda m: 'bhkconvex' in m or 'bhkbox' in m or 'bhkcapsule' in m or 'bhksphere' in m,
    'havok_rigidbody':             lambda m: 'bhkrigid' in m,
    'invalid_enum_extravectors':   lambda m: 'extravectorsflag' in m,
    'invalid_enum_shader':         lambda m: 'slsf' in m or ('shader_flags' in m and 'invalid' in m),
    'texture_path_issue':          lambda m: 'texture' in m and ('not found' in m or 'missing' in m or 'invalid' in m),
    'skin_partition':              lambda m: 'niskinpartition' in m or 'skin partition' in m,
    'skin_data':                   lambda m: 'niskindata' in m or 'skin data' in m,
    'bone_invalid':                lambda m: 'bone' in m and ('invalid' in m or 'not found' in m or 'missing' in m),
    'particle_system':             lambda m: 'nipsys' in m or 'particle system' in m,
    'controller_invalid':          lambda m: 'nicontroller' in m and ('invalid' in m or 'not in nif' in m),
    'controller_target':           lambda m: 'controller' in m and 'target' in m,
    'string_palette':              lambda m: 'nistringpalette' in m or 'string palette' in m or 'stringpalette' in m,
    'keyframe_data':               lambda m: 'nikeyframedata' in m or 'nitransformdata' in m or 'keyframe' in m,
    'tristrips_data':              lambda m: 'nitristripsdata' in m,
    'trishape_data':               lambda m: 'nitrishapedata' in m,
    'geometry_morphdata':          lambda m: 'nimorphdata' in m or 'geommorph' in m,
    'av_object_palette':           lambda m: 'avobject' in m or 'objectpalette' in m,
    'linked_block_invalid':        lambda m: 'linked block' in m,
    'missing_from_nif_tree':       lambda m: 'missing from the nif tree' in m or 'not in nif tree' in m,
    'value_out_of_range':          lambda m: 'out of range' in m,
    'invalid_nif_value':           lambda m: 'invalid' in m and ('nif' in m or 'value' in m),
    'unexpected_end_stream':       lambda m: 'unexpected end' in m or 'end of stream' in m,
    'unknown_block_type':          lambda m: 'unknown block type' in m or 'unrecognised block' in m,
}


def _categorize_pyffi_warnings(messages: list) -> dict:
    """Convert raw PyFFI WARNING messages to a {category: count} dict.

    Unrecognised messages are grouped by their leading word, typically the NIF
    block type, so the summary breaks down rather than showing one 'other'.
    """
    c: _collections.Counter = _collections.Counter()
    for msg in messages:
        m = msg.lower()
        matched = False
        for cat, test in _WARN_CATEGORIES.items():
            if test(m):
                c[cat] += 1
                matched = True
                break
        if not matched:
            first_word = msg.split()[0].rstrip(':').lower() if msg.split() else 'unknown'
            c[f'type_{first_word}'] += 1
    return dict(c)


def _empty_batch_stats(total):
    """The stats dict asset_pipeline expects, with every bucket present."""
    return {
        'total': total, 'converted': 0, 'copied': 0, 'skipped': 0,
        'errors': 0, 'strips': 0, 'properties': 0, 'roots': 0, 'rotations': 0,
        'warn_counts': _collections.Counter(),
        #: Union of the textures every written mesh references.
        'textures_used': set(),
        #: Per-category parallax accounting, empty unless parallax=True.
        'parallax': _collections.Counter(),
        #: Diffuses some shape reads as opacity; never stripped to BC1.
        'alpha_opacity_diffuse': set(),
        #: Of those, the APPLY_HILIGHT2 overlays: alpha is a blend weight.
        'overlay_diffuses': set(),
    }


def _collect_nifs(mesh_path, subdir_filter):
    """(files to convert, how many the filters dropped).

    Each `subdir_filter` entry is a path prefix under the mesh root: a root
    folder (`clutter`), a nested one (`tr/l`) or a single mesh.
    """
    allowed = ([tuple(s.lower().replace('\\', '/').strip('/').split('/'))
                for s in subdir_filter]
               if subdir_filter is not None else None)
    keep, skipped = [], 0
    for nf in mesh_path.rglob('*.nif'):
        parts = tuple(p.lower() for p in nf.relative_to(mesh_path).parts)
        if any(seg in parts for seg in SKIP_PATHS):
            skipped += 1
        elif allowed is not None and not any(parts[:len(a)] == a for a in allowed):
            skipped += 1
        else:
            keep.append(nf)
    return keep, skipped


def _merge_result(stats, skipped_list, mesh_path, nif_str, r):
    """Fold one worker result into the run totals."""
    stats['warn_counts'].update(r.get('warn_counts', {}))
    stats['textures_used'].update(r.get('textures', ()))
    stats['parallax'].update(r.get('parallax') or {})
    stats['alpha_opacity_diffuse'].update(r.get('alpha_opacity_diffuse') or ())
    stats['overlay_diffuses'].update(r.get('overlay_diffuses', ()))
    rel = str(Path(nif_str).relative_to(mesh_path))
    if r.get('error'):
        stats['errors'] += 1
        skipped_list.append((rel, str(r['error'])))
    elif r.get('converted'):
        stats['converted'] += 1
        for flag, bucket in (('strips_fixed', 'strips'),
                             ('properties_converted', 'properties'),
                             ('root_converted', 'roots'),
                             ('root_rotation_baked', 'rotations')):
            if r[flag]:
                stats[bucket] += 1
    elif r.get('copied'):
        stats['copied'] += 1
    else:
        stats['skipped'] += 1
        skipped_list.append((rel, r.get('skip_reason', '?')))


def _progress(stats, mesh_path, nif_str, done, total):
    """Print one progress line naming the folder currently being converted."""
    try:
        parts = Path(nif_str).relative_to(mesh_path).parts
        folder = parts[0] if len(parts) > 1 else '.'
    except ValueError:
        folder = Path(nif_str).parent.name
    print(f'  {done}/{total} [{folder}] -- converted={stats["converted"]} '
          f'copied={stats["copied"]} errors={stats["errors"]}')


def _run_batch(work_args, stats, skipped_list, mesh_path, workers,
               scan_dir=None):
    """Convert every queued mesh, in a pool or serially."""
    total = len(work_args)

    def handle(done, status, nif_str, payload, every):
        """Fold one result in and print progress every `every` meshes."""
        if status == 'ok':
            _merge_result(stats, skipped_list, mesh_path, nif_str, payload)
        else:
            stats['errors'] += 1
            rel = str(Path(nif_str).relative_to(mesh_path))
            skipped_list.append((rel, 'EXC'))
            if stats['errors'] <= 20:
                print(f'  ERROR: {Path(nif_str).name}: {payload}')
        if done % every == 0 or done == total:
            _progress(stats, mesh_path, nif_str, done, total)

    ns = current_namespace()
    if workers > 1:
        with mp.Pool(processes=workers,
                     initializer=pyffi_capture_init,
                     initargs=(ns, scan_dir)) as pool:
            for done, (status, nif_str, payload) in enumerate(
                    pool.imap_unordered(_batch_worker, work_args), 1):
                handle(done, status, nif_str, payload, 500)
        return
    pyffi_capture_init(ns, scan_dir)
    for done, args in enumerate(work_args, 1):
        status, nif_str, payload = _batch_worker(args)
        handle(done, status, nif_str, payload, 200)


def _report_warnings(stats):
    """Summarise the pyffi warnings the capture handler swallowed."""
    if not stats['warn_counts']:
        return
    total_suppressed = sum(stats['warn_counts'].values())
    top = sorted(stats['warn_counts'].items(), key=lambda x: -x[1])[:30]
    shown = sum(c for _, c in top)
    print(f'\nPyFFI warnings suppressed ({total_suppressed} total):')
    for cat, cnt in top:
        print(f'  {cat}: {cnt}')
    if shown < total_suppressed:
        remaining = len(stats['warn_counts']) - len(top)
        print(f'  ... ({total_suppressed - shown} more in {remaining} '
              f'other categories)')


def _report_parallax(stats):
    """Print the height-map conversion tally."""
    px = stats['parallax']
    print(f'\nParallax: {px.get("parallax_shapes", 0)} shapes converted to '
          f'the heightmap shader (+'
          f'{px.get("parallax_vertex_colors_added", 0)} given white vertex '
          f'colors)')
    for cat, cnt in sorted((k, v) for k, v in px.items()
                           if k.startswith('parallax_skipped_')
                           or k == 'parallax_texture_unresolved'):
        print(f'  left flat, {cat[len("parallax_"):]}: {cnt} shapes')


def _report_glow(stats):
    """Print how many shapes carried an authored glow map across."""
    glow = {k[len('glow_'):]: v for k, v in stats['parallax'].items()
            if k.startswith('glow_')}
    if not glow:
        return
    if glow.get('applied'):
        print(f"\nGlow maps: {glow['applied']} shapes")
    if glow.get('unresolved'):
        print(f"  WARNING: {glow['unresolved']} shapes reference a missing "
              f'glow texture -- left unlit')
    pg = stats['parallax'].get('parallax_skipped_glow', 0)
    if pg:
        print(f'  {pg} also asked for parallax -- glow used instead')


def _report_specular(stats):
    """Print the share of shapes whose specular mask was AUTHORED.

    Only the verdict categories form the base: `normal_from_base` and
    `normal_defaulted` ride the same bucket for plumbing reasons but describe
    where the normal came FROM, and counting them diluted the share from 92.9%
    to a meaningless 86.2%.
    """
    spec = {k[len('spec_'):]: v for k, v in stats['parallax'].items()
            if k.startswith('spec_')}
    if not spec:
        return
    verdicts = ('mask', 'no_alpha', 'flat', 'binary', 'missing_normal')
    on = spec.get('mask', 0)
    tot = sum(spec.get(k, 0) for k in verdicts)
    print(f'\nSpecular: {on}/{tot} shapes ({on * 100.0 / max(1, tot):.0f}%) '
          f"use a mask from the normal map alpha; strength {SPEC_STRENGTH}")
    fallback = [f'{k}={spec[k]}' for k in
                ('no_alpha', 'missing_normal', 'binary', 'flat') if spec.get(k)]
    if fallback:
        print(f"  no mask (constant used): {', '.join(fallback)}")
    if spec.get('normal_from_base'):
        print(f"  {spec['normal_from_base']} shapes share a color variant's "
              f'base normal map')
    if spec.get('normal_defaulted'):
        print(f"  {spec['normal_defaulted']} shapes had no normal map -- "
              f'pointed at {default_normal_texture()}')


def _report_batch(stats, skipped_list, total, parallax):
    """Print everything the run has to say once the meshes are done."""
    print(f'\nResults: {stats["converted"]} converted, {stats["copied"]} '
          f'copied, {stats["skipped"]} skipped, {stats["errors"]} errors / '
          f'{total} total')
    if skipped_list:
        print(f'\nFailed/Skipped ({len(skipped_list)}) -- '
              f'RD=read fail, WR=write fail, EXC=exception:')
        for rel, reason in sorted(skipped_list):
            print(f'  [{reason}] {rel}')
    _report_warnings(stats)
    if parallax:
        _report_parallax(stats)
    _report_glow(stats)
    _report_specular(stats)
    print(f'\nGeometry: strips->shapes={stats["strips"]}, '
          f'properties={stats["properties"]}, roots={stats["roots"]}, '
          f'rotations baked={stats["rotations"]}')


def batch_convert(mesh_dir, output_dir, *, fix_textures=True,
                  remap_skeleton=None, subdir_filter=None, wearable_plan=None,
                  parallax=False, textures_only=False, scan_dir=None):
    """Convert every NIF under mesh_dir into output_dir; return run stats.

    Skip reasons are VER (unsupported version), RD (read failure) and WR
    (write failure). subdir_filter names root subfolders to include;
    wearable_plan says which _0/_1/plain variants each armor mesh needs;
    parallax carries Oblivion's height field across; textures_only analyses
    every mesh and emits none.
    See: docs/commentary/performance.md#parallelism-rules
    """
    mesh_path = Path(mesh_dir)
    out_base = Path(output_dir)
    nif_files, skipped_by_path = _collect_nifs(mesh_path, subdir_filter)
    total = len(nif_files)
    stats = _empty_batch_stats(total)
    skipped_list = []

    workers = WORKER_COUNT
    tex_fallback = master_texture_roots(mesh_dir)
    print(f'Found {total} NIF files in {mesh_dir} (workers={workers})')
    if tex_fallback:
        names = ', '.join(os.path.basename(os.path.dirname(r))
                          for r in tex_fallback)
        print(f'  Texture fallback: {len(tex_fallback)} master tree(s) '
              f'-- {names}')
    if skipped_by_path:
        print(f'  Skipped {skipped_by_path} files matching SKIP_PATHS: '
              f'{sorted(SKIP_PATHS)}')
    if total == 0:
        return stats

    work_args = [
        (str(nif_file), str(out_base / nif_file.relative_to(mesh_path)),
         fix_textures, remap_skeleton, str(mesh_path), wearable_plan,
         parallax, textures_only, tex_fallback)
        for nif_file in nif_files
    ]
    _run_batch(work_args, stats, skipped_list, mesh_path, workers,
               scan_dir=scan_dir)
    _report_batch(stats, skipped_list, total, parallax)
    return stats


def _batch_worker(args):
    """Convert one NIF in a pool worker; (status, path, payload)."""
    (nif_str, out_path, fix_textures, remap_skeleton, src_meshes_dir,
     wearable_plan, parallax, textures_only, tex_fallback) = args
    global worker_warn_log
    worker_warn_log = []
    try:
        r = convert_nif(nif_str, out_path,
                        fix_textures=fix_textures, remap_skeleton=remap_skeleton,
                        src_meshes_dir=src_meshes_dir,
                        wearable_plan=wearable_plan, parallax=parallax,
                        textures_only=textures_only,
                        tex_fallback=tex_fallback)
        r['warn_counts'] = _categorize_pyffi_warnings(worker_warn_log)
        return ('ok', nif_str, r)
    except Exception as e:
        return ('error', nif_str, str(e))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Convert Oblivion NIFs to Skyrim format')
    parser.add_argument('src', help='Source NIF file or directory')
    parser.add_argument('dst', help='Destination NIF file or directory')
    parser.add_argument('--no-fix-textures', action='store_true')
    a = parser.parse_args()

    if Path(a.src).is_dir():
        batch_convert(a.src, a.dst, fix_textures=not a.no_fix_textures)
    else:
        r = convert_nif(a.src, a.dst, fix_textures=not a.no_fix_textures)
        print(r)
