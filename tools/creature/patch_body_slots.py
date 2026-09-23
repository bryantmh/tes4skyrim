#!/usr/bin/env python3
"""Generate the body-slot patch: a Skyrim load order made to share the conversion's body slots.

The conversion wears the lower body in slot 49, the left hand in 33 and the
right hand in 59 (Morrowind pauldrons in 57/58, which hide nothing). Vanilla
Skyrim draws the body and both hands as single files, so the patch points
every vanilla skin addon wearing them at the split meshes -- torso / legs,
left / right hand, first person included -- and adds the split-off section as
a new addon on the same skins. Every other slot-32 item also claims 49 and
every slot-33 item also claims 59, so Skyrim armor hides exactly what it hid;
every race drawing slot 33 in first person draws 59 there too.

All input plugins merge into ONE patch, each a master, FormIDs remapped into
the merged master list and unreferenced masters dropped; each record is its
load-order winner. The conversion's own plugins already use the layout and are
skipped. Localized FULL/DESC are inlined from the masters' string tables.

Usage:
    python tools/creature/patch_body_slots.py "C:/.../Data/Skyrim.esm" -o patch.esp
    python tools/creature/patch_body_slots.py Skyrim.esm Dawnguard.esm --meshes-root out/meshes
    python tools/creature/patch_body_slots.py Skyrim.esm --language german --no-esl

See: docs/commentary/asset_convert_armor.md#body-slot-layout
"""

import argparse
import os
import struct
import sys
from typing import NamedTuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

import tools.esm.tes5_esm_reader as t5r
from asset_convert import paths
from asset_convert.sources.bsa_extract import read_bsa_files
from asset_convert.character.body_slots import SECTIONS, SPLIT_STEMS, split_mesh_path, write_split_meshes
from output_layout import BODY_SLOTS_PATCH, finished_dir
from tes5_import.base.tes5_reader import subrecords
from tes5_import.base.writer import (
    FORM_VERSION_SSE,
    HEDR_VERSION_SSE,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_top_group,
)

#: Header author of the conversion's own plugins, which already use the layout.
CONVERTER_AUTHOR = 'TES4-to-TES5 Converter'

#: BOD2 bits of slots 32, 33, 38, 49 and 59.
_SLOT_32, _SLOT_33, _SLOT_38, _SLOT_49, _SLOT_59 = (1 << 2, 1 << 3, 1 << 8, 1 << 19, 1 << 29)

#: Split file suffix -> (the slot its new addon claims and its skin ARMO gains, EditorID suffix).
_ADDON_SPLITS = {'legs': (_SLOT_49, 'Legs'), 'calves': (_SLOT_38, 'Calves'), 'right': (_SLOT_59, 'RightHand')}

#: (slot an item claims, the slot it then also claims).
_ITEM_EXTRAS = ((_SLOT_32, _SLOT_49), (_SLOT_33, _SLOT_59))

#: Record type -> its extras; a race's slots are the ones it draws in first person, so it gains 59 alone.
_EXTRAS = {'ARMO': _ITEM_EXTRAS, 'ARMA': _ITEM_EXTRAS, 'RACE': ((_SLOT_33, _SLOT_59),)}

#: Record flag of a zlib-compressed record; the patch writes every record uncompressed.
_FLAG_COMPRESSED = 0x00040000

#: ARMA worn-model subrecords, and the texture hashes that describe the model they follow.
_MODEL_SUBS = frozenset({'MOD2', 'MOD3', 'MOD4', 'MOD5'})
_MODEL_HASHES = frozenset({'MO2T', 'MO3T', 'MO4T', 'MO5T'})

#: Records the patch reads: the armor, and the races and NPCs naming their skins (WNAM).
_READ_TYPES = {'ARMO', 'ARMA', 'RACE', 'NPC_'}

#: Master-index byte the patch's own new records carry until they are remapped.
_PATCH_LOCAL = 0xFE

FLAG_ESL = 0x00000200

#: Localized-string subrecords of an ARMO -> the string table holding them.
_LSTRING_SUBS = {'FULL': 'strings', 'DESC': 'dlstrings'}

#: Subrecords that are FormIDs or FormID arrays, by record type (xEdit wbDefinitionsTES5).
_FORMID_SUBS = {
    'ARMO': {'EITM', 'BIDS', 'BAMT', 'RNAM', 'TNAM', 'MODL', 'KWDA', 'YNAM', 'ZNAM'},
    'ARMA': {'RNAM', 'NAM0', 'NAM1', 'NAM2', 'NAM3', 'SNDD', 'ONAM', 'MODL'},
    'RACE': {'WNAM', 'SPLO', 'KWDA', 'VTCK', 'DNAM', 'HCLF', 'ATKR', 'HNAM', 'ENAM', 'GNAM',
             'NAM4', 'NAM5', 'NAM7', 'ONAM', 'LNAM', 'MTYP', 'QNAM', 'UNES', 'WKMV', 'RNMV',
             'SWMV', 'FLMV', 'SNMV', 'SPMV', 'HEAD', 'RPRM', 'RPRF', 'AHCM', 'AHCF', 'FTSM',
             'FTSF', 'DFTM', 'DFTF', 'TIND', 'TINC', 'NAM8', 'RNAM'},
}

#: Struct subrecords holding one FormID at a byte offset: a race's attack spell.
_FORMID_AT = {'ATKD': 8}

#: Alternate-texture lists: a count, then per entry a sized name, a TXST FormID and an index.
_ALT_TEXTURES = frozenset({'MODS', 'MO2S', 'MO3S', 'MO4S', 'MO5S'})


class _Source(NamedTuple):
    """A load-order-winning record and how to read it: its plugin's master map and strings."""

    rec: object
    plugin: str
    to_merged: dict
    tables: dict
    localized: bool


# ---------------------------------------------------------------------------
# String tables (.strings / .dlstrings, loose or in a BSA)
# ---------------------------------------------------------------------------

def _parse_strings_file(raw: bytes, length_prefixed: bool) -> dict:
    """Parse a Bethesda .strings/.dlstrings blob -> {id: bytes}."""
    count, _data_size = struct.unpack_from('<II', raw, 0)
    data_start = 8 + count * 8
    table = {}
    for i in range(count):
        sid, off = struct.unpack_from('<II', raw, 8 + i * 8)
        pos = data_start + off
        if length_prefixed:
            if pos + 4 > len(raw):
                continue
            (slen,) = struct.unpack_from('<I', raw, pos)
            table[sid] = raw[pos + 4:pos + 4 + slen].split(b'\x00')[0]
        else:
            end = raw.find(b'\x00', pos)
            table[sid] = raw[pos:end if end >= 0 else len(raw)]
    return table


def _string_langs(language: str) -> list:
    """`language` first, then every other shipped language as a fallback."""
    langs = [language.lower()]
    for fallback in ('english', 'french', 'german', 'italian', 'spanish',
                     'polish', 'russian', 'japanese', 'chinese'):
        if fallback not in langs:
            langs.append(fallback)
    return langs


def _bsa_strings(plugin_dir: str, stem: str, langs: list, missing: list) -> dict:
    """{ext: raw table} found in the BSAs beside the plugin, language order first."""
    found_raw = {}
    wanted = [f'strings\\{stem}_{lang}.{ext}' for ext in missing for lang in langs]
    bsas = sorted((f for f in os.listdir(plugin_dir) if f.lower().endswith('.bsa')),
                  key=lambda n: ('interface' not in n.lower(),
                                 not n.lower().startswith(stem.lower()), n.lower()))
    for bsa in bsas:
        try:
            found = read_bsa_files(os.path.join(plugin_dir, bsa), wanted)
        except Exception:
            continue
        for ext in missing:
            for lang in langs:
                key = f'strings\\{stem}_{lang}.{ext}'.lower()
                if ext not in found_raw and key in found:
                    found_raw[ext] = found[key]
        if all(ext in found_raw for ext in missing):
            break
    return found_raw


def _load_string_tables(plugin_path: str, language: str) -> dict:
    """{'strings': {id: bytes}, 'dlstrings': {id: bytes}} for a localized plugin; empty when absent."""
    plugin_dir = os.path.dirname(os.path.abspath(plugin_path))
    stem = os.path.splitext(os.path.basename(plugin_path))[0]
    langs = _string_langs(language)
    raws = {}
    for ext in ('strings', 'dlstrings'):
        for lang in langs:
            loose = os.path.join(plugin_dir, 'Strings', f'{stem}_{lang}.{ext}')
            if ext not in raws and os.path.exists(loose):
                with open(loose, 'rb') as f:
                    raws[ext] = f.read()
    missing = [ext for ext in ('strings', 'dlstrings') if ext not in raws]
    if missing:
        raws.update(_bsa_strings(plugin_dir, stem, langs, missing))
    return {'strings': _parse_strings_file(raws['strings'], False) if 'strings' in raws else {},
            'dlstrings': (_parse_strings_file(raws['dlstrings'], True)
                          if 'dlstrings' in raws else {})}


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------

def _zstring(data: bytes) -> str:
    """A null-terminated subrecord string."""
    return data.rstrip(b'\x00').decode('cp1252', errors='replace')


def _biped_flags(rec):
    """(slot flags, the BOD2/BODT subrecord), or (None, None)."""
    sub = next((s for s in rec.subrecords if s.type in ('BOD2', 'BODT')), None)
    if sub is None or len(sub.data) < 4:
        return None, None
    return struct.unpack_from('<I', sub.data, 0)[0], sub


def _with_biped_flags(sub, flags: int) -> bytes:
    """BOD2/BODT data with its slot flags replaced."""
    return struct.pack('<I', flags) + sub.data[4:]


def _mesh(sub):
    """(stem, weight) of a worn model `<stem>_<weight>.nif`, or None."""
    base = _zstring(sub.data).lower().replace('/', '\\').rsplit('\\', 1)[-1]
    stem, _, weight = os.path.splitext(base)[0].rpartition('_')
    return (stem, int(weight)) if stem and weight.isdigit() else None


def _skin_split(rec):
    """The split kind of an ARMA wearing a vanilla skin mesh the layout splits, or None."""
    for sub in rec.subrecords:
        mesh = _mesh(sub) if sub.type in ('MOD2', 'MOD3') else None
        if mesh and mesh[0] in SPLIT_STEMS:
            return SPLIT_STEMS[mesh[0]]
    return None


def _model_data(sub, suffix: str):
    """A worn model pointed at split file `suffix`; unsplit models stay on the vanilla addon ('') only."""
    mesh = _mesh(sub)
    if mesh and mesh[0] in SPLIT_STEMS:
        return split_mesh_path(mesh[0], mesh[1], suffix).encode('cp1252') + b'\x00'
    return None if suffix else sub.data


def _arma_sub_data(sub, flags: int, suffix: str, edid):
    """One ARMA subrecord's data in the split layout, or None to drop it."""
    if sub.type == 'EDID' and edid:
        return edid.encode('cp1252') + b'\x00'
    if sub.type in ('BOD2', 'BODT'):
        return _with_biped_flags(sub, flags)
    if sub.type in _MODEL_SUBS:
        return _model_data(sub, suffix)
    return None if sub.type in _MODEL_HASHES else sub.data


def _arma_subs(rec, flags: int, suffix: str, edid=None) -> bytes:
    """An ARMA's subrecords wearing split file `suffix` (see _arma_sub_data)."""
    return b''.join(pack_subrecord(sub.type, data) for sub in rec.subrecords
                    if (data := _arma_sub_data(sub, flags, suffix, edid)) is not None)


def _extended(flags: int, extras=_ITEM_EXTRAS) -> int:
    """Slot flags with each (claimed, extra) pair's extra added."""
    for have, extra in extras:
        flags |= extra if flags & have else 0
    return flags


def _localized(sub, src: _Source, warnings: list):
    """Subrecord data with a FULL/DESC string index inlined; None drops an unnamed FULL."""
    if not (src.localized and sub.type in _LSTRING_SUBS and len(sub.data) == 4):
        return sub.data
    (sid,) = struct.unpack_from('<I', sub.data, 0)
    text = src.tables[_LSTRING_SUBS[sub.type]].get(sid, b'') if sid else b''
    if sid and not text:
        warnings.append(f'{src.rec.type} {src.rec.form_id:08X}: {sub.type} string '
                        f'{sid:08X} not found in string tables -- emitting empty')
    if sub.type == 'FULL' and not text:
        return None
    return text + b'\x00'


def _alt_texture_offsets(data: bytes) -> list:
    """Byte offsets of the TXST FormIDs in an alternate-texture list."""
    offsets, pos = [], 4
    for _ in range(struct.unpack_from('<I', data, 0)[0] if len(data) >= 4 else 0):
        pos += 4 + struct.unpack_from('<I', data, pos)[0]
        offsets.append(pos)
        pos += 8
    return offsets


def _formid_offsets(rec_type: str, sig: str, data: bytes) -> list:
    """Byte offsets of the non-null FormIDs in one subrecord."""
    if sig in _FORMID_SUBS.get(rec_type, ()) and len(data) % 4 == 0:
        offsets = range(0, len(data), 4)
    elif rec_type == 'RACE' and sig in _FORMID_AT and len(data) >= _FORMID_AT[sig] + 4:
        offsets = [_FORMID_AT[sig]]
    else:
        offsets = _alt_texture_offsets(data) if sig in _ALT_TEXTURES else []
    return [o for o in offsets if struct.unpack_from('<I', data, o)[0]]


def _remap(rec_bytes: bytes, slot_map: dict) -> bytes:
    """A packed record with its own FormID and every FormID it holds moved through slot_map."""
    rec_type = rec_bytes[:4].decode('ascii')
    flags, form_id = struct.unpack_from('<II', rec_bytes, 8)
    form_version = struct.unpack_from('<H', rec_bytes, 20)[0]
    form_id = (slot_map.get(form_id >> 24, form_id >> 24) << 24) | (form_id & 0xFFFFFF)
    out = b''
    for tag, data in subrecords(rec_bytes[24:]):
        sig = tag.decode('ascii')
        data = bytearray(data)
        for off in _formid_offsets(rec_type, sig, data):
            data[off + 3] = slot_map.get(data[off + 3], data[off + 3])
        out += pack_subrecord(sig, bytes(data))
    return pack_record(rec_type, form_id, flags, out, form_version or FORM_VERSION_SSE)


def _formid_tops(rec_bytes: bytes) -> set:
    """The master-index bytes a packed record's own FormID and the FormIDs it holds use."""
    rec_type = rec_bytes[:4].decode('ascii')
    tops = {struct.unpack_from('<I', rec_bytes, 12)[0] >> 24}
    for tag, data in subrecords(rec_bytes[24:]):
        tops.update(data[off + 3] for off in _formid_offsets(rec_type, tag.decode('ascii'), data))
    return tops


# ---------------------------------------------------------------------------
# Load order
# ---------------------------------------------------------------------------

def _header_masters(header) -> list:
    """A plugin header's MAST list."""
    return [_zstring(s.data) for s in header.subrecords if s.type == 'MAST']


def _is_converted(header) -> bool:
    """True for the conversion's own plugins and an earlier patch."""
    return any(s.type == 'CNAM' and _zstring(s.data) == CONVERTER_AUTHOR
               for s in header.subrecords)


def _merged_masters(plugins: list) -> list:
    """Every plugin's masters and the plugin itself, each after its own masters."""
    merged = []
    for path, header in plugins:
        for name in _header_masters(header) + [os.path.basename(path)]:
            if name not in merged:
                merged.append(name)
    return merged


def _merged(raw: int, to_merged: dict):
    """A FormID in a plugin's own master space moved to merged space, or None."""
    top = to_merged.get(raw >> 24)
    return None if top is None else (top << 24) | (raw & 0xFFFFFF)


def _resolve(plugins: list, merged: list, language: str) -> tuple:
    """(merged FormID -> _Source of every ARMO/ARMA/RACE's load-order winner, the skins RACE/NPC_ name)."""
    resolved, skins = {}, set()
    for path, _header in plugins:
        header, records, localized = t5r.read_tes5_file(path, parse_types=_READ_TYPES)
        own = _header_masters(header)
        to_merged = {i: merged.index(m) for i, m in enumerate(own)}
        to_merged[len(own)] = merged.index(os.path.basename(path))
        tables = _load_string_tables(path, language) if localized else {}
        for rec in records:
            if rec.type in _EXTRAS:
                resolved[_merged(rec.form_id, to_merged)] = _Source(
                    rec, os.path.basename(path), to_merged, tables, localized)
            skins.update(_merged(struct.unpack_from('<I', s.data)[0], to_merged)
                         for s in rec.subrecords if s.type == 'WNAM' and len(s.data) == 4)
    return resolved, skins


def _merged_refs(src: _Source) -> list:
    """The merged FormIDs of an ARMO's armature (MODL) list."""
    out = []
    for sub in src.rec.subrecords:
        if sub.type == 'MODL' and len(sub.data) == 4:
            raw = struct.unpack_from('<I', sub.data)[0]
            if (raw >> 24) in src.to_merged:
                out.append((src.to_merged[raw >> 24] << 24) | (raw & 0xFFFFFF))
    return out


# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------

class _Patch:
    """The patch's records, each in its source plugin's master space with its map into merged space.

    A merged index can pass 255 before unreferenced masters are dropped, so
    records are only renumbered once the final master list is known.
    See: docs/commentary/asset_convert_armor.md#body-slot-layout
    """

    def __init__(self, resolved: dict, patch_index: int):
        """Empty patch over `resolved`; its own records take merged master index `patch_index`."""
        self.resolved = resolved
        self.patch_index = patch_index
        self.records = {'RACE': [], 'ARMO': [], 'ARMA': []}
        self.next_id = 0x800
        self.warnings = []

    def emit(self, src: _Source, form_id: int, subs: bytes) -> None:
        """Queue a record built in `src`'s own master space."""
        rec_bytes = pack_record(src.rec.type, form_id, src.rec.flags & ~_FLAG_COMPRESSED, subs,
                                src.rec.form_version or FORM_VERSION_SSE)
        self.records[src.rec.type].append((rec_bytes, {**src.to_merged, _PATCH_LOCAL: self.patch_index}))

    def split_skins(self) -> dict:
        """Split every skin addon wearing a split mesh; {its merged FormID: [(new addon's local id, its bit)]}."""
        added = {}
        for fid, src in self.resolved.items():
            kind = _skin_split(src.rec) if src.rec.type == 'ARMA' else None
            flags, _bod = _biped_flags(src.rec)
            if kind is None or flags is None:
                continue
            splits = [_ADDON_SPLITS[suffix] + (suffix,) for suffix in SECTIONS[kind] if suffix]
            drop = sum(bit for bit, _tag, _suffix in splits)
            self.emit(src, src.rec.form_id, _arma_subs(src.rec, flags & ~drop, ''))
            edid = next((_zstring(s.data) for s in src.rec.subrecords if s.type == 'EDID'),
                        f'{src.rec.form_id:08X}')
            added[fid] = []
            for bit, tag, suffix in splits:
                new_id = (_PATCH_LOCAL << 24) | self.next_id
                self.next_id += 1
                self.emit(src, new_id, _arma_subs(src.rec, bit, suffix, f'TES4{edid}{tag}'))
                added[fid].append((new_id, bit))
        return added

    def rearmature(self, added: dict) -> set:
        """Append the new addons, and their skin bits, to every ARMO wearing a split addon.

        An addon draws only when its slots share a bit with its ARMO's, and an
        armor wearing a naked addon (Astrid's) still claims the item slots;
        returns the ARMOs patched.
        See: docs/commentary/asset_convert_armor.md#body-slot-layout
        """
        patched = set()
        for fid, src in self.resolved.items():
            refs = [ref for r in _merged_refs(src) if r in added for ref in added[r]] \
                if src.rec.type == 'ARMO' else []
            flags, bod = _biped_flags(src.rec)
            if not refs or flags is None:
                continue
            flags = _extended(flags)
            for _new_id, skin_bit in refs:
                flags |= skin_bit
            last = [s for s in src.rec.subrecords if s.type == 'MODL'][-1]
            subs = b''
            for sub in src.rec.subrecords:
                data = _with_biped_flags(sub, flags) if sub is bod else _localized(sub, src, self.warnings)
                if data is not None:
                    subs += pack_subrecord(sub.type, data)
                if sub is last:
                    subs += b''.join(pack_subrecord('MODL', struct.pack('<I', r)) for r, _bit in refs)
            self.emit(src, src.rec.form_id, subs)
            patched.add(fid)
        return patched

    def extend_items(self, skip: set) -> int:
        """Give every record its `_EXTRAS`, skins aside; how many changed.

        See: docs/commentary/asset_convert_armor.md#body-slot-layout
        """
        changed = 0
        for fid, src in self.resolved.items():
            flags, bod = _biped_flags(src.rec)
            new = flags if fid in skip or flags is None else _extended(flags, _EXTRAS[src.rec.type])
            if new == flags:
                continue
            subs = b''.join(pack_subrecord(s.type, _with_biped_flags(s, new) if s is bod else d)
                            for s in src.rec.subrecords
                            if (d := _localized(s, src, self.warnings)) is not None)
            self.emit(src, src.rec.form_id, subs)
            changed += 1
        return changed


def _skin_parts(resolved: dict, skins: set) -> set:
    """The skins races and NPCs wear and every addon they hold: never given item slots."""
    parts = set(skins)
    for fid in skins:
        if fid in resolved:
            parts.update(_merged_refs(resolved[fid]))
    return parts


def _finalize(patch: _Patch, merged: list) -> tuple:
    """(final masters, {group signature: record bytes}) with unreferenced masters dropped."""
    used = set()
    for group in patch.records.values():
        for rec_bytes, to_merged in group:
            used |= {to_merged.get(top, top) for top in _formid_tops(rec_bytes)}
    final = [m for i, m in enumerate(merged) if i in used]
    to_final = {old: new for new, old in enumerate(i for i in range(len(merged)) if i in used)}
    to_final[patch.patch_index] = len(final)
    if len(final) > 254:
        raise SystemExit(f'ERROR: the patch needs {len(final)} masters; a plugin can have 254. '
                         f'Deselect plugins that carry no armor.')
    return final, {sig: b''.join(_remap(r, {src: to_final[m] for src, m in to_merged.items() if m in to_final})
                                 for r, to_merged in group)
                   for sig, group in patch.records.items()}


def _pack_header(masters: list, num_records: int, next_object_id: int, esl: bool,
                 description: str) -> bytes:
    """The patch's TES4 header record."""
    subs = pack_subrecord('HEDR', struct.pack('<fII', HEDR_VERSION_SSE, num_records,
                                              next_object_id))
    subs += pack_string_subrecord('CNAM', CONVERTER_AUTHOR)
    subs += pack_string_subrecord('SNAM', description)
    for m in masters:
        subs += pack_string_subrecord('MAST', m)
        subs += pack_subrecord('DATA', b'\x00' * 8)
    return pack_record('TES4', 0, FLAG_ESL if esl else 0, subs, FORM_VERSION_SSE)


def patch_plugins(input_paths: list, output_path: str, language: str = 'english',
                  esl: bool = True) -> int:
    """Write ONE body-slot patch over `input_paths` (load order); how many records it holds."""
    plugins = []
    for path in input_paths:
        header = t5r.read_tes5_file(path, parse_types=set())[0]
        if _is_converted(header):
            print(f'{os.path.basename(path)}: a converted plugin, already in the layout -- skipped')
        else:
            plugins.append((path, header))
    merged = _merged_masters(plugins)
    resolved, skins = _resolve(plugins, merged, language)
    patch = _Patch(resolved, len(merged))
    added = patch.split_skins()
    rearmatured = patch.rearmature(added)
    items = patch.extend_items(_skin_parts(resolved, skins) | set(added) | rearmatured)
    for warning in patch.warnings[:20]:
        print(f'  WARNING: {warning}')
    total = sum(len(g) for g in patch.records.values())
    print(f'{len(added)} skin addons split, {len(rearmatured)} skins re-armatured, '
          f'{items} items given the split slots, {total} records')
    if not total:
        return 0
    masters, groups = _finalize(patch, merged)
    out = _pack_header(masters, total, patch.next_id, esl,
                       'TES4 conversion body-slot patch over '
                       + ', '.join(os.path.basename(p) for p, _h in plugins) + '.')
    out += b''.join(pack_top_group(sig, data) for sig, data in groups.items() if data)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(out)
    print(f'Written: {output_path} ({total} records, masters: {masters})')
    return total


def main():
    """CLI: patch the given load order, and write the split skin meshes on request."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('plugins', nargs='+', help='TES5 plugins to patch, in load order')
    parser.add_argument('-o', '--output', default=None,
                        help=f'patch path (default: output/<finished mods>/{BODY_SLOTS_PATCH}.esp)')
    parser.add_argument('--meshes-root', default=None,
                        help='also write the split skin meshes under this meshes folder')
    parser.add_argument('--language', default='english',
                        help='string-table language for localized masters (default: english)')
    parser.add_argument('--no-esl', action='store_true', help='do not flag the patch ESL')
    args = parser.parse_args()
    missing = [p for p in args.plugins if not os.path.exists(p)]
    if missing:
        print(f'ERROR: plugin not found: {missing[0]}')
        return 1
    if args.meshes_root:
        written = write_split_meshes(args.meshes_root)
        print(f'{len(written)} split skin meshes written under {args.meshes_root}')
    out = args.output or str(finished_dir(paths.OUTPUT) / f'{BODY_SLOTS_PATCH}.esp')
    patch_plugins(args.plugins, out, language=args.language, esl=not args.no_esl)
    return 0


if __name__ == '__main__':
    sys.exit(main())
