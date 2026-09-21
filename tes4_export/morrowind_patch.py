"""
The Morroblivion patch: one shared master holding every object Morroblivion
never converted.

Morroblivion resolves 89.3% of vanilla Morrowind's base records, so in
Morroblivion mode a dependent plugin routinely places objects its master
cannot supply. Dropping those placements loses authored content; minting them
in each plugin's own space makes every plugin needing the same object ship a
rival copy of it.

So the gap is filled ONCE, by a standalone plugin built from the vanilla ESMs
holding every base record Morroblivion lacks. Every Morroblivion-mode
conversion declares it as a master and borrows from it exactly as it borrows
from Morroblivion itself, and a conversion that cannot find it is refused like
any other missing master.

Only the assets those records name are extracted, so the patch ships the ~10%
of Morrowind's tree Morroblivion is missing rather than all of it.

`build_patch` runs that whole pass when the user asks for a build -- export,
assets AND the plugin itself, because one user action has to leave something
installable -- and the rest answers where the patch is and what it supplies.

See: docs/commentary/tes4_export_morrowind.md#morroblivion-gap-patch
"""

import hashlib
import os
import re
import time

from asset_convert.sources.bsa_extract_morrowind import (is_morrowind_bsa,
                                                         read_index,
                                                         iter_bsa)
from asset_convert.sources.source_registry import asset_root
from output_layout import (DEFAULT_OUTPUT, plugin_esm, plugin_out_root,
                           record_dir)
from tes5_import.base.text_reader import parse_export_file

from .morroblivion import (MORROBLIVION_CREATURES, MorroblivionModels,
                           archive_path)
from .morroblivion_axis import SUBSTITUTION_BLACKLIST
from .morrowind_ids import BASE_TYPES, IdIndex, load_index
from .record_types.morrowind import as_dds
from .tes3_reader import get_string, get_subrecord, read_file

#: Morroblivion's EditorID separators: a leading '0' and '_' written 'U'.
_EDID_SEP = re.compile(r'[_u]')

#: Derivation site for a gap fill; fixed so ids never move between builds.
PATCH_SITE = 'mwpatch'

#: The generated patch plugin, a master of every Morroblivion-mode conversion.
PATCH_NAME = 'Morrowind-Morroblivion-Compatibility.esp'

#: The vanilla ESMs the patch is built from, in load order.
PATCH_SOURCES = ('Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm')

#: The BSAs holding the assets those ESMs name.
PATCH_ARCHIVES = ('Morrowind.bsa', 'Tribunal.bsa', 'Bloodmoon.bsa')

#: Base object types a placement can name; cells and terrain are never filled.
GAP_TYPES = frozenset(BASE_TYPES) - {'CELL', 'LAND', 'WRLD'}

#: Filled from vanilla even where Morroblivion supplies the id: read, not placed.
ALWAYS_FILLED = frozenset({'GLOB'})

#: What a voiced bark needs -- its topic stream and who may speak it -- plus the creature sound generators.
BARK_TYPES = frozenset({'DIAL', 'INFO', 'NPC_', 'CREA', 'SNDG'})

#: TES3 allocates this many magic effect indices.
_MAGIC_EFFECT_COUNT = 143

#: Derived ids live above Morroblivion's blocks, matching the export's own span.
_DERIVED_BASE = 0x00200000
_DERIVED_SPAN = 0x00D00000


def patch_formid(key, own_index: int = 0) -> str:
    """The FormID of one gap fill, hashed from its (type, id) authored key.

    Keyed on authored data alone, so an id survives a rebuild and a plugin
    exported against an earlier patch still resolves. The type is part of the
    key because Morrowind's ids are unique only within one. `own_index` is the
    patch's load-order byte: how many Morroblivion plugins it declares.
    See: docs/commentary/tes4_export_morrowind.md#per-type-id-namespaces
    """
    signature, record_id = key
    payload = ('%s\x00%s\x00%s' % (PATCH_SITE, signature,
                                   record_id.lower())).encode('utf-8')
    offset = int.from_bytes(hashlib.md5(payload).digest()[:4], 'little')
    return '%08X' % ((own_index << 24)
                     | (_DERIVED_BASE + offset % _DERIVED_SPAN))


def patch_dir(export_dir: str) -> str:
    """Where the shared patch's export lives."""
    return str(record_dir(export_dir, PATCH_NAME))


def patch_exists(export_dir: str) -> bool:
    """Whether the shared patch has been built and can be borrowed from."""
    return os.path.isfile(os.path.join(patch_dir(export_dir), '_HEADER.txt'))


def source_paths(data_dir: str, names) -> tuple:
    """(present paths, missing names) for `names` in a Morrowind Data folder."""
    found, missing = [], []
    for name in names:
        path = os.path.join(data_dir or '', name)
        if os.path.isfile(path):
            found.append(path)
        else:
            missing.append(name)
    return found, missing


def supplied_index(export_dir: str, exports) -> IdIndex:
    """What the converted Morroblivion exports can already supply.

    Read unremapped: the patch asks only WHETHER an object exists, never
    under which load-order byte, so the borrower's own re-keying does not
    apply here.
    """
    index = IdIndex()
    for name in exports:
        index.merge(load_index(str(record_dir(export_dir, name))))
    return index


def blacklisted_bases(export_dir: str, exports) -> set:
    """Normalized ids Morroblivion supplies wearing a mesh we refuse.

    The base belongs to Morroblivion, so no mesh substitution reaches it; the
    only way to ship the authored object is to fill it from vanilla instead.
    See: docs/audits/morroblivion_mesh_axis_rotation.md#base-objects-not-meshes
    """
    out = set()
    for name in exports:
        directory = str(record_dir(export_dir, name))
        if not os.path.isdir(directory):
            continue
        for entry in os.listdir(directory):
            if not entry.endswith('.txt') or entry.startswith('_'):
                continue
            for rec in parse_export_file(os.path.join(directory, entry)):
                mesh = archive_path(rec.get('Model.MODL') or '')
                if mesh.replace(chr(92), chr(47)) in SUBSTITUTION_BLACKLIST \
                        and rec.get('EditorID'):
                    out.add(_unmangle(rec['EditorID']))
    return out


def _unmangle(editor_id: str) -> str:
    """Morroblivion's EditorID reduced to the Morrowind id it was made from.

    See: docs/audits/morroblivion_mesh_axis_rotation.md#pairing-the-bases
    """
    return _EDID_SEP.sub('', editor_id.lower()).lstrip('0')


def collect_gap_records(sources, index: IdIndex, refused=()) -> dict:
    """{(type, record_id): TES3 record} for every object `index` cannot supply.

    Earlier sources win, and the key carries the type because Morrowind's ids
    are unique only within one. A creature the Morroblivion table pairs is
    skipped; an id in `refused` is filled although Morroblivion supplies it,
    as is every `ALWAYS_FILLED` type.
    See: docs/commentary/tes4_export_morrowind.md#per-type-id-namespaces
    See: docs/commentary/tes4_export_morrowind.md#globals-are-always-filled
    See: docs/audits/morroblivion_mesh_axis_rotation.md#base-objects-not-meshes
    """
    found = {}
    for path in sources:
        if not os.path.isfile(path):
            continue
        for rec in read_file(path)[1]:
            name = (rec.record_id or '').lower()
            key = (rec.type, name)
            if rec.deleted or not name or key in found or _paired_creature(rec):
                continue
            if rec.type not in GAP_TYPES:
                continue
            if (rec.type in ALWAYS_FILLED
                    or index.lookup(rec.record_id) is None
                    or _unmangle(name) in refused):
                found[key] = rec
    return found


def _paired_creature(rec) -> bool:
    """Whether this CREA wears a vanilla mesh the Morroblivion table pairs."""
    sub = get_subrecord(rec, 'MODL') if rec.type == 'CREA' else None
    return sub is not None and archive_path(get_string(sub)) in MORROBLIVION_CREATURES


def _info_identity(rec) -> str:
    """An INFO's own INAM, which is what makes it unique; '' for other types."""
    sub = get_subrecord(rec, 'INAM') if rec.type == 'INFO' else None
    return get_string(sub) if sub is not None else ''


def collect_bark_records(sources) -> list:
    """Every vanilla record the voiced-bark export needs, in file order.

    The DIAL/INFO stream the exporter walks, the actors its audiences resolve
    against, and the SNDG generators a gap creature's sound slots come from.
    See: docs/commentary/tes4_export_morrowind.md#the-patch-owns-vanilla-barks
    """
    out, seen = [], set()
    for path in sources:
        if not os.path.isfile(path):
            continue
        for rec in read_file(path)[1]:
            if rec.type not in BARK_TYPES or rec.deleted:
                continue
            key = (rec.type, (rec.record_id or '').lower(),
                   _info_identity(rec))
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
    return out


def gap_assets(records) -> set:
    """Every archive path the gap records name, lowercased and backslashed.

    Meshes from MODL and a land texture from LTEX's DATA. Inventory icons are
    deliberately absent: Skyrim renders the 3D model in the inventory and has
    no ICON field, so an extracted icon would ship as a dead asset.
    """
    wanted = set()
    for rec in records:
        _add_asset(wanted, rec, 'MODL', 'meshes')
        if rec.type == 'CREA':
            _add_animation_pair(wanted, rec)
        if rec.type == 'LTEX':
            _add_asset(wanted, rec, 'DATA', 'textures', dds=True)
    return wanted


def _add_animation_pair(wanted: set, rec) -> None:
    """Add the `x<model>.nif`/`.kf` pair a creature's animations may live in."""
    sub = get_subrecord(rec, 'MODL')
    if sub is None:
        return
    path = get_string(sub).replace('/', chr(92)).strip().lower().lstrip(chr(92))
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0]
    for ext in ('.nif', '.kf'):
        wanted.add('meshes' + chr(92) + chr(92).join(
            p for p in (folder, 'x' + stem + ext) if p))


def _add_asset(wanted: set, rec, sig: str, subtree: str,
               dds: bool = False) -> None:
    """Add the archive path one subrecord names, if it names one."""
    sub = get_subrecord(rec, sig)
    if sub is None:
        return
    path = get_string(sub).replace('/', chr(92)).strip().lower()
    if not path:
        return
    if dds:
        path = as_dds(path)
    wanted.add('%s%s%s' % (subtree, chr(92), path.lstrip(chr(92))))


def build_patch(data_dir: str, export_dir: str, morroblivion_exports,
                progress=print, out_root=None) -> dict:
    """Build the shared patch from a Morrowind Data folder; report what it made.

    `morroblivion_exports` are the converted Morroblivion plugins whose records
    define the gap: anything they already supply is not filled. `out_root` is
    the output directory the finished plugin and its assets land in.

    `ok` means the plugin FILE exists: a build that wrote records and assets
    but no plugin is a failure, not a success.
    """
    start = time.time()
    out_root = out_root or DEFAULT_OUTPUT
    esms, missing = source_paths(data_dir, PATCH_SOURCES)
    if missing:
        return {'ok': False, 'error': _missing_message(data_dir, missing)}
    if not morroblivion_exports:
        return {'ok': False, 'error': _no_morroblivion_message()}

    progress(f'Indexing {len(morroblivion_exports)} converted Morroblivion '
             f'plugin(s)...')
    index = supplied_index(export_dir, morroblivion_exports)
    progress(f'  {len(index)} objects already supplied')
    refused = blacklisted_bases(export_dir, morroblivion_exports)
    if refused:
        progress(f'  {len(refused)} supplied on a mesh we refuse, filled here')

    progress(f'Scanning {len(esms)} vanilla master(s) for gaps...')
    gaps = collect_gap_records(esms, index, refused)
    progress(f'  {len(gaps)} base records Morroblivion does not supply')
    if not gaps:
        return {'ok': True, 'records': 0, 'assets': 0, 'output': '',
                'plugin': '', 'seconds': time.time() - start}

    assets = _extract_assets(gaps.values(), esms, data_dir, export_dir,
                             progress)
    assets += _copy_gap_sounds(gaps.values(), data_dir, export_dir, progress)
    progress('Collecting vanilla voiced barks...')
    barks = collect_bark_records(esms)
    out_dir = _write_records(gaps, export_dir, progress, barks,
                             morroblivion_exports)
    assets += _stage_bark_voices(data_dir, export_dir, progress)
    _convert_assets(export_dir, out_root, progress)
    _convert_creatures(export_dir, out_root, progress)
    plugin, error = _import_records(export_dir, out_root, progress)
    return {'ok': bool(plugin), 'records': len(gaps), 'assets': assets,
            'output': out_dir, 'plugin': plugin, 'error': error,
            'seconds': time.time() - start}



def _stage_bark_voices(data_dir: str, export_dir: str, progress) -> int:
    """Copy the recordings the patch's barks name; how many files were staged.

    A bark record without its audio is a silent line with a moving mouth, so
    this runs off the INFO dump `_write_records` just produced.
    See: docs/commentary/tes4_export_morrowind.md#the-patch-owns-vanilla-barks
    """
    from pathlib import Path

    from asset_convert.audio.morrowind_voice import stage_bark_voices
    from asset_convert.sources.bsa_extract_morrowind_sounds import (
        collect_bark_voices)
    staged = stage_bark_voices(
        collect_bark_voices(export_dir, PATCH_NAME),
        Path(data_dir) / 'Sound', asset_root(export_dir, PATCH_NAME),
        PATCH_NAME)
    progress(f'  Staged {staged} bark recording(s)')
    return staged

def _copy_gap_sounds(records, data_dir: str, export_dir: str,
                     progress) -> int:
    """Copy the loose sounds the gap SOUNs name; how many files were new.

    Sounds are the one asset class the BSA does not hold, so `_extract_assets`
    cannot reach them. The gap records already ARE "what Morroblivion lacks",
    so their filenames need no further subtraction.
    See: docs/commentary/tes4_export_morrowind.md#which-sounds-a-plugin-ships
    """
    from asset_convert.sources.bsa_extract_morrowind import copy_loose_sounds
    from asset_convert.sources.morrowind_sound_scope import normalize
    owned = set()
    for rec in records:
        if rec.type != 'SOUN':
            continue
        sub = get_subrecord(rec, 'FNAM')
        if sub is not None and get_string(sub).strip():
            owned.add(normalize(get_string(sub).strip()))
    copied = copy_loose_sounds(data_dir, asset_root(export_dir, PATCH_NAME),
                               owned)
    progress(f'  Copied {copied} of {len(owned)} gap sound file(s)')
    return copied


def _convert_creatures(export_dir: str, out_root, progress) -> None:
    """Convert the patch's creatures, so dependent plugins inherit their projects.

    See: docs/commentary/tes4_export_morrowind.md#morroblivion-creatures
    """
    from asset_convert.havok.creature_pipeline import convert_creatures
    rec_dir = str(record_dir(export_dir, PATCH_NAME))
    out_meshes = str(plugin_out_root(out_root, PATCH_NAME, export_dir) / 'meshes')
    progress('  Converting patch creatures')
    result = convert_creatures(rec_dir, out_meshes, log=progress)
    progress(f"  {len(result['projects'])} creature projects, "
             f"{len(result['errors'])} errors")


def _import_records(export_dir: str, out_root, progress) -> tuple:
    """Build the patch plugin itself from the records just exported.

    Returns (path, '') on success and ('', refusal) otherwise. ESM-flagged
    under its `.esp` extension.
    See: docs/commentary/tes4_export_morrowind.md#the-patch-builds-its-own-plugin

    Imported inside the function to keep the export package from loading the
    whole import stage just to answer where the patch lives.
    """
    from tes5_import.pipeline import import_plugin

    dest = plugin_esm(out_root, PATCH_NAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    progress(f'  Building {PATCH_NAME}')
    try:
        _converted, errors = import_plugin(
            export_dir=patch_dir(export_dir), output_path=str(dest),
            masters=['Skyrim.esm'], is_esm=True, output_root=str(out_root))
    except Exception as exc:
        return '', _import_failed_message(f'{type(exc).__name__}: {exc}')
    if not dest.is_file():
        return '', _import_failed_message('the importer wrote no plugin file')
    if errors:
        return '', _import_failed_message(f'{errors} record error(s)')
    progress(f'  Wrote {dest}')
    return str(dest), ''


def _convert_assets(export_dir: str, out_root, progress) -> None:
    """Convert the extracted patch assets into `out_root`.

    Building the patch is ONE user action, so it has to leave installable
    files behind. Extraction alone populates `export/` only, and every texture
    it pulled stayed invisible to the game until an unrelated stage happened
    to run.
    """
    from asset_convert.asset_pipeline import convert_meshes
    progress('  Converting patch assets to output')
    try:
        stats = convert_meshes(PATCH_NAME, extract_dir=export_dir,
                               output_dir=out_root)
    except Exception as exc:
        progress(f'  Asset conversion FAILED: {exc}')
        return
    mesh = stats.get('mesh_conversion') or {}
    progress(f"  Converted {mesh.get('converted', 0)} meshes, "
             f"{stats.get('textures_copied', 0)} textures")


def _patch_ownership(export_dir: str) -> MorroblivionModels:
    """Ownership for the patch: the meshes it extracted, and no replacements."""
    return MorroblivionModels(export_dir, [], '',
                              asset_root(export_dir, PATCH_NAME) / 'meshes')


def _patch_context(gaps: dict, export_dir: str, vanilla, morroblivion) -> tuple:
    """`(ctx, ids)`: a context that resolves through Morroblivion, and each gap's id.

    Gap ids are reserved before anything is derived, since they share the span
    a bark is minted in; `vanilla` supplies the SNDG generators a gap creature's
    sound slots come from.
    See: docs/commentary/tes4_export_morrowind.md#the-patch-masters-morroblivion

    Imported inside the function to break the cycle with `export_morrowind`,
    which needs PATCH_NAME from this module at its own import time.
    """
    from .export_morrowind import load_context, register_magic_effects
    from .record_types.morrowind import tes4_signature
    from .record_types.morrowind_actors import register_sound_gens
    from .record_types.morrowind_magic import effect_editor_id

    ctx = load_context(export_dir, [(name, str(record_dir(export_dir, name)))
                                    for name in morroblivion])
    ids = {key: patch_formid(key, ctx.own_index) for key in gaps}
    ctx.taken.update(ids.values())
    ctx.morroblivion = _patch_ownership(export_dir)
    register_magic_effects(list(gaps.values()), ctx)
    for index in range(_MAGIC_EFFECT_COUNT):
        edid = effect_editor_id(index)
        ctx.gap_ids[('MGEF', edid.lower())] = patch_formid(
            ('MGEF', edid), ctx.own_index)
    for key, rec in gaps.items():
        signature = tes4_signature(rec)
        ctx.register_own(rec.record_id, signature)
        ctx.gap_ids[(signature, key[1])] = ids[key]
    register_sound_gens(vanilla, ctx)
    return ctx, ids


def _write_records(gaps: dict, export_dir: str, progress,
                   barks=(), morroblivion=()) -> str:
    """Export every gap record under its shared derived FormID.

    The Morroblivion plugins are declared as MASTERS, so a gap record or a bark
    can name the factions, classes and actors only Morroblivion holds.
    See: docs/commentary/tes4_export_morrowind.md#the-patch-masters-morroblivion

    Imported inside the function to break the cycle with `export_morrowind`,
    which needs PATCH_NAME from this module at its own import time.
    """
    from .export_morrowind import (export_record, magic_effect_records,
                                   write_export, write_header)
    from .record_types.morrowind import (filled_soulgem_id, filled_soulgems,
                                         tes4_signature)

    ctx, ids = _patch_context(gaps, export_dir, barks, morroblivion)
    records = list(gaps.values())
    out = {}
    for key, rec in sorted(gaps.items()):
        lines = export_record(rec, ctx)
        if lines:
            out.setdefault(tes4_signature(rec), []).append((ids[key], lines))
    out['MGEF'] = magic_effect_records(records, ctx)
    for gem_id, soul, lines in filled_soulgems(records):
        edid = filled_soulgem_id(gem_id, soul)
        out.setdefault('SLGM', []).append(
            (patch_formid(('SLGM', edid), ctx.own_index), lines))
    ctx.taken.update(fid for rows in out.values() for fid, _lines in rows)
    _add_barks(out, ctx, barks, progress)
    out_dir = patch_dir(export_dir)
    counts = write_export(out, out_dir)
    write_header(out_dir, list(morroblivion), sum(counts.values()),
                 'Objects Morroblivion does not convert')
    progress(f'  Wrote {sum(counts.values())} records to {out_dir}')
    return out_dir



def _add_barks(out: dict, ctx, barks, progress) -> None:
    """Merge the vanilla voiced barks into the patch's own records.

    The audience resolves against the bark records' OWN actors -- vanilla's,
    which the patch carries -- so the gate names the same speakers Morrowind
    filtered on.
    See: docs/commentary/tes4_export_morrowind.md#the-patch-owns-vanilla-barks
    """
    from .record_types.morrowind_dialog import dialogue_records
    if not barks:
        return
    produced = dialogue_records(barks, ctx, say=False)
    for sig in ('DIAL', 'INFO'):
        rows = produced.get(sig) or []
        if rows:
            out.setdefault(sig, []).extend(rows)
    progress(f"  Voiced barks: {len(produced.get('INFO') or [])} lines "
             f"under {len(produced.get('DIAL') or [])} topic(s); "
             f"{ctx.unresolved['bark audience']} name a class, faction or "
             f"actor only Morroblivion holds")

def orphan_meshes(sources, data_dir: str) -> set:
    """Archive meshes no vanilla record names, as `meshes\\...` keys.

    Morroblivion can replace only what a record names, so these are the
    patch's to convert. `base_anim*` and a creature's `x<model>` animation
    pair are not objects and stay out.
    See: docs/commentary/tes4_export_morrowind.md#morroblivion-meshes
    """
    named = set()
    for path in sources:
        if not os.path.isfile(path):
            continue
        for rec in read_file(path)[1]:
            _add_asset(named, rec, 'MODL', 'meshes')
    stored = set()
    for name in PATCH_ARCHIVES:
        path = os.path.join(data_dir, name)
        if os.path.isfile(path) and is_morrowind_bsa(path):
            stored.update(k for k in read_index(path)
                          if k.startswith('meshes' + chr(92)) and k.endswith('.nif'))
    orphans = set()
    for key in stored:
        base = os.path.basename(key)
        paired = key[:-len(base)] + base[1:]
        if key in named or base.startswith('base_anim') \
                or (base.startswith('x') and paired in stored):
            continue
        orphans.add(key)
    return orphans


def _extract_assets(records, sources, data_dir: str, export_dir: str,
                    progress) -> int:
    """Extract the meshes the gap records name, the ownerless vanilla meshes, and EVERY texture.

    Meshes come per record; textures cannot, because third-party content
    references vanilla names from meshes that are not gap records. Both are
    taken in ONE pass per archive: `iter_bsa` holds the whole BSA in memory
    (Morrowind.bsa is ~800 MB), so walking it twice ran out of it.
    See: docs/commentary/tes4_export_morrowind.md#morroblivion-gap-patch
    """
    asset_dir = asset_root(export_dir, PATCH_NAME)
    wanted = gap_assets(records) | orphan_meshes(sources, data_dir)
    progress(f'  {len(wanted)} meshes wanted; extracting those and all textures')
    written = 0
    for name in PATCH_ARCHIVES:
        path = os.path.join(data_dir, name)
        if os.path.isfile(path) and is_morrowind_bsa(path):
            written += _extract_one(path, wanted, asset_dir)
    progress(f'  Extracted {written} files')
    return written


def _extract_one(bsa_path: str, wanted: set, asset_dir) -> int:
    """Write every entry of one BSA `wanted` names OR that is a texture."""
    prefix = 'textures' + chr(92)
    written = 0
    for name, data in iter_bsa(bsa_path):
        key = name.replace('/', chr(92)).strip().lower()
        if key not in wanted and not key.startswith(prefix):
            continue
        dest = asset_dir / key
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written += 1
    return written


def _missing_message(data_dir: str, missing: list) -> str:
    """The refusal naming each vanilla master the chosen folder lacks."""
    lines = ['That folder is not a Morrowind Data Files directory.', '',
             f'Looked in: {data_dir or "(nothing chosen)"}', '', 'Missing:']
    lines += [f'  {name}' for name in missing]
    return '\n'.join(lines)


def _import_failed_message(reason: str) -> str:
    """The refusal when the records exported but the plugin did not build."""
    lines = [f'{PATCH_NAME} did not build: {reason}', '',
             'The records and assets are in export/, so nothing is lost -- '
             'but no plugin was written, and a Morroblivion-mode conversion '
             'will still refuse until one is.', '',
             'Re-run the build; if it fails again the log above names the '
             'record that stopped it.']
    return chr(10).join(lines)


def _no_morroblivion_message() -> str:
    """The refusal when nothing defines what the gap actually is."""
    return ('No converted Morroblivion plugin was found.\n\n'
            'The patch holds what Morroblivion does NOT supply, so '
            'Morroblivion has to be converted first:\n\n'
            '  python convert.py -f Morrowind_ob.esm')
