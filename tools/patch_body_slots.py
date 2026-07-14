#!/usr/bin/env python3
"""Generate a patch plugin making a Skyrim plugin's body items compatible
with the TES4 conversion's biped slot 44 (Lower Body) and the split body
meshes produced by asset_convert/modify_body_meshes.py.

Why this is needed
------------------
The TES4 conversion gives Oblivion LowerBody items (greaves/pants) their own
biped slot 44, and modify_body_meshes.py splits the character body mesh's
part-32 skin partition into torso (32) + lower body (44, hips + thighs +
underwear), alongside the vanilla calves (38) and forearms (34) partitions.

Skyrim's naked skin only works with this if the skin ARMAs are restructured.
Two engine constraints drive the design:

1. A skin partition renders in-game only when the wearing ARMA claims its
   biped slot — the vanilla NakedTorso ARMA (32/34/35/36/38) never renders
   the new part-44 skin, leaving naked thighs invisible.
2. An armor addon whose slot set overlaps an equipped item's slots gets
   hidden as a whole (this is why Bethesda ships separate NakedTorso /
   NakedHands / NakedFeet addons — so gauntlets don't hide the body).  If
   NakedTorso simply also claimed 44, equipping slot-44 greaves would hide
   the ENTIRE torso skin, and equipping a slot-32 shirt would hide the legs.

So the patch splits the skin by region, one single-purpose addon per body
partition group (each partition has exactly one owner, making addon-level
and partition-level hiding equivalent):

    NakedTorso*   override -> slots {32,34,35,36}  (drops 38)
    <EDID>Thighs  new ARMA -> slot  {44}           (thighs+hips+underwear)
    <EDID>Calves  new ARMA -> slot  {38}           (calves)

and appends the two new armatures to every skin ARMO referencing a split
torso ARMA (SkinNaked, SkinNakedBeast, ArmorAfflicted).

Equip combinations then behave like Oblivion:
    naked            -> all three skin addons render (full body)
    shirt (32)       -> torso skin hidden, legs + underwear stay
    greaves (44+38)  -> leg skin + underwear hidden, torso stays
    boots (37+38)    -> calf skin hidden, thighs stay
    Skyrim cuirass   -> patched to 32+44 below: hides torso AND legs
                        (its mesh models the legs), and conflicts with
                        Oblivion greaves so they can't clip

For PLAYABLE items the patch adds slot 44 to every slot-32 ARMO/ARMA:
Skyrim cuirasses/robes model the legs as part of the slot-32 mesh, so they
must both hide the new leg skin and equip-conflict with Oblivion greaves.

All plugins passed in are merged into a SINGLE output patch (ESL-flagged by
default), each becoming one of its masters — this is meant to be run once
across the user's whole load order (Skyrim.esm + DLCs + any third-party
armor mods) rather than once per plugin, so equipping any of them behaves
correctly against the same "Slot44 Patch.esp". Every FormID is remapped
from each source plugin's own master-slot indices into the merged master
list before Clean Masters runs once over the combined output, dropping
whichever merged masters ended up unreferenced (e.g. Update.esm, when
nothing the patch actually touches references it). Load the patch after
every plugin it lists as a master.

Localized masters (Skyrim.esm has the localized flag) store FULL/DESC as
string-table indices; since the patch itself is not localized, the tool
resolves those indices from each master's .strings/.dlstrings files (loose
or inside a BSA next to the plugin) and inlines the text.

Usage:
    python tools/patch_body_slots.py "C:/.../Data/Skyrim.esm"
    python tools/patch_body_slots.py Skyrim.esm Dawnguard.esm Dragonborn.esm MyArmorMod.esp
    python tools/patch_body_slots.py Skyrim.esm --language german --no-esl
"""

import argparse
import os
import struct
import sys

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TOOLS_DIR)
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, _TOOLS_DIR)

import tes5_esm_reader as t5r  # noqa: E402
from tes5_import.writer import (  # noqa: E402
    FORM_VERSION_SSE,
    HEDR_VERSION_SSE,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_top_group,
)

SLOT_32_BODY      = 1 << 2    # biped slot 32 (Body)
SLOT_34_FOREARMS  = 1 << 4    # biped slot 34 (Forearms)
SLOT_35_AMULET    = 1 << 5    # biped slot 35 (Amulet)
SLOT_36_RING      = 1 << 6    # biped slot 36 (Ring)
SLOT_38_CALVES    = 1 << 8    # biped slot 38 (Calves)
SLOT_44_LOWERBODY = 1 << 14   # biped slot 44 (Lower Body / greaves)

# Split-torso claim set: torso keeps body/forearms (+ vanilla's inert
# amulet/ring bits); calves and lower body move to their own addons.
TORSO_MASK  = SLOT_32_BODY | SLOT_34_FOREARMS | SLOT_35_AMULET | SLOT_36_RING
THIGH_MASK  = SLOT_44_LOWERBODY
CALVES_MASK = SLOT_38_CALVES

FLAG_ESM       = 0x00000001
FLAG_LOCALIZED = 0x00000080
FLAG_ESL       = 0x00000200

# Exact worn-model basenames modified by asset_convert/modify_body_meshes.py.
# Only skin ARMAs pointing at these get the three-way split; skins with their
# own body meshes (e.g. werewolf, child) keep vanilla behavior.
_SPLIT_BODY_BASENAMES = ('malebody_1.nif', 'femalebody_1.nif')

# Localized-string subrecords appearing in ARMO (ARMA has none):
#   FULL → .strings, DESC → .dlstrings
_LSTRING_SUBS = {'FULL': 'strings', 'DESC': 'dlstrings'}

# Subrecords NOT copied into the generated Thighs/Calves ARMAs: EDID/BODT/BOD2
# are rewritten, and the 1st-person models (MOD4/MOD5) are omitted — the
# 1st-person body has no part-44/38 partitions and a second copy would
# z-fight with NakedTorso's.
_SKIN_CLONE_SKIP = {'EDID', 'BODT', 'BOD2', 'MOD4', 'MO4T', 'MOD5', 'MO5T'}


# ---------------------------------------------------------------------------
# String table loading (.strings / .dlstrings, loose or BSA)
# ---------------------------------------------------------------------------

def _parse_strings_file(raw: bytes, length_prefixed: bool) -> dict:
    """Parse a Bethesda .strings/.dlstrings/.ilstrings blob → {id: bytes}."""
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
            if end < 0:
                end = len(raw)
            table[sid] = raw[pos:end]
    return table


def _load_string_tables(plugin_path: str, language: str) -> dict:
    """Locate and parse the plugin's string tables.

    Returns {'strings': {id: bytes}, 'dlstrings': {id: bytes}} (values may be
    empty dicts when a table cannot be found).
    """
    plugin_dir = os.path.dirname(os.path.abspath(plugin_path))
    stem = os.path.splitext(os.path.basename(plugin_path))[0]

    langs = [language.lower()]
    for fallback in ('english', 'french', 'german', 'italian', 'spanish',
                     'polish', 'russian', 'japanese', 'chinese'):
        if fallback not in langs:
            langs.append(fallback)

    tables = {'strings': {}, 'dlstrings': {}}
    raws = {}

    # 1. Loose files: <dir>/Strings/<stem>_<lang>.<ext>
    for ext in ('strings', 'dlstrings'):
        for lang in langs:
            loose = os.path.join(plugin_dir, 'Strings', f'{stem}_{lang}.{ext}')
            if os.path.exists(loose):
                with open(loose, 'rb') as f:
                    raws[ext] = f.read()
                break

    # 2. BSA archives next to the plugin
    missing = [ext for ext in ('strings', 'dlstrings') if ext not in raws]
    if missing:
        try:
            from asset_convert.bsa_extract import read_bsa_files
        except ImportError:
            read_bsa_files = None
        if read_bsa_files is not None:
            wanted = [f'strings\\{stem}_{lang}.{ext}'
                      for ext in missing for lang in langs]
            bsas = sorted(
                (f for f in os.listdir(plugin_dir) if f.lower().endswith('.bsa')),
                # Strings usually live in "<game> - Interface.bsa" or "<stem>*.bsa"
                key=lambda n: (('interface' not in n.lower()),
                               (not n.lower().startswith(stem.lower())), n.lower()))
            for bsa in bsas:
                try:
                    found = read_bsa_files(os.path.join(plugin_dir, bsa), wanted)
                except Exception:
                    continue
                # Pick per extension in language-priority order, not archive order
                for ext in missing:
                    if ext in raws:
                        continue
                    for lang in langs:
                        key = f'strings\\{stem}_{lang}.{ext}'.lower()
                        if key in found:
                            raws[ext] = found[key]
                            break
                if all(ext in raws for ext in missing):
                    break

    if 'strings' in raws:
        tables['strings'] = _parse_strings_file(raws['strings'], length_prefixed=False)
    if 'dlstrings' in raws:
        tables['dlstrings'] = _parse_strings_file(raws['dlstrings'], length_prefixed=True)
    return tables


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------

def _get_sub(rec, sig):
    return next((s for s in rec.subrecords if s.type == sig), None)


def _biped_flags(rec):
    """Return (flags, sub) from BOD2/BODT, or (None, None)."""
    sub = next((s for s in rec.subrecords if s.type in ('BOD2', 'BODT')), None)
    if sub is None or len(sub.data) < 4:
        return None, None
    return struct.unpack_from('<I', sub.data, 0)[0], sub


def _is_skin_torso_arma(rec) -> bool:
    """ARMA whose worn model is one of the split character body meshes."""
    if rec.type != 'ARMA':
        return False
    flags, _ = _biped_flags(rec)
    if flags is None or not flags & SLOT_32_BODY:
        return False
    for s in rec.subrecords:
        if s.type in ('MOD2', 'MOD3'):
            path = t5r._zstring(s.data).lower().replace('/', '\\')
            if any(path.endswith(b) for b in _SPLIT_BODY_BASENAMES):
                return True
    return False


def _repack(rec, sub_transform):
    """Rebuild record bytes, mapping each Sub through sub_transform.

    sub_transform(sub) returns bytes-data, None to drop the sub, or a list of
    (type, data) tuples to replace it with several subrecords.
    """
    out = b''
    for sub in rec.subrecords:
        res = sub_transform(sub)
        if res is None:
            continue
        if isinstance(res, list):
            for t, d in res:
                out += pack_subrecord(t, d)
        else:
            out += pack_subrecord(sub.type, res)
    return pack_record(rec.type, rec.form_id, rec.flags, out,
                       rec.form_version or FORM_VERSION_SSE)


def _localize_sub(sub, is_localized, tables, rec, warnings):
    """Inline FULL/DESC lstring indices; returns data bytes or None to drop."""
    data = sub.data
    if is_localized and sub.type in _LSTRING_SUBS and len(data) == 4:
        (sid,) = struct.unpack_from('<I', data, 0)
        if sid == 0:
            text = b''
        else:
            text = tables[_LSTRING_SUBS[sub.type]].get(sid)
            if text is None:
                warnings.append(
                    f'{rec.type} {rec.form_id:08X}: {sub.type} string '
                    f'{sid:08X} not found in string tables — emitting empty')
                text = b''
        if sub.type == 'FULL' and not text:
            return None  # unnamed: drop FULL entirely
        return text + b'\x00'
    return data


def _with_biped_flags(sub, new_flags):
    """Return BOD2/BODT data with the first uint32 replaced."""
    return struct.pack('<I', new_flags) + sub.data[4:]


def _build_skin_clone(rec, new_fid, new_edid, mask):
    """Build a Thighs/Calves ARMA as a filtered copy of a torso skin ARMA."""
    _, bod = _biped_flags(rec)
    subs = pack_string_subrecord('EDID', new_edid)
    subs += pack_subrecord(bod.type, _with_biped_flags(bod, mask))
    for sub in rec.subrecords:
        if sub.type in _SKIN_CLONE_SKIP:
            continue
        subs += pack_subrecord(sub.type, sub.data)
    return pack_record('ARMA', new_fid, 0, subs,
                       rec.form_version or FORM_VERSION_SSE)


def _pack_header(masters: list, num_records: int, next_object_id: int,
                 esl: bool, description: str) -> bytes:
    subs = pack_subrecord('HEDR', struct.pack('<fII', HEDR_VERSION_SSE,
                                              num_records, next_object_id))
    subs += pack_string_subrecord('CNAM', 'TES4-to-TES5 Converter')
    subs += pack_string_subrecord('SNAM', description)
    for m in masters:
        subs += pack_string_subrecord('MAST', m)
        subs += pack_subrecord('DATA', b'\x00' * 8)
    flags = FLAG_ESL if esl else 0
    return pack_record('TES4', 0, flags, subs, FORM_VERSION_SSE)


# ---------------------------------------------------------------------------
# FormID field tables, used both for Clean Masters usage-scanning (in
# patch_plugins, before any FormID is packed) and for remapping already-
# packed record bytes below. Scoped to the exact FormID-typed subrecords
# ARMO/ARMA can carry, per the xEdit TES5 definitions
# (references/xEdit/Core/wbDefinitionsTES5.pas, wbRecord(ARMO ...)/
# wbRecord(ARMA ...)): a signature list rather than a blind byte scan, since
# this tool only ever emits these two record types.
# ---------------------------------------------------------------------------

# Single 4-byte FormID subrecords, by owning record type.
_FORMID_SUBS = {
    'ARMO': {'EITM', 'BIDS', 'BAMT', 'RNAM', 'TNAM'},
    'ARMA': {'RNAM', 'NAM0', 'NAM1', 'NAM2', 'NAM3', 'SNDD', 'ONAM'},
}
# MODL is a repeated array entry (ARMO's Armature list / ARMA's Additional
# Races list) — always FormID-typed in both record types, so it's implicit.
_FORMID_ARRAY_SUB = 'MODL'


def _remap_record_bytes(rec_bytes: bytes, slot_map: dict) -> bytes:
    """Rewrite a packed record's own FormID plus its FormID-typed
    subrecords' top byte (master slot) through slot_map, returning
    re-packed record bytes.

    The record's own header FormID must be remapped too: an ARMO/ARMA
    override's top byte encodes which master originally owns it (e.g.
    Dawnguard.esm's own new records use Dawnguard's *local* self-index,
    which is a different number from Dawnguard's index in the merged
    master list) — leaving it unmapped would silently reassign the record
    to the wrong master (or collide with the patch's own synthetic
    records) once multiple plugins share one merged master list.
    """
    rec_type = rec_bytes[:4].decode('ascii')
    form_id  = struct.unpack_from('<I', rec_bytes, 12)[0]
    flags    = struct.unpack_from('<I', rec_bytes, 8)[0]
    form_ver = struct.unpack_from('<H', rec_bytes, 20)[0]
    subs = t5r._parse_subrecords(rec_bytes[24:])

    old_rec_top = form_id >> 24
    new_rec_top = slot_map.get(old_rec_top, old_rec_top)
    form_id = (new_rec_top << 24) | (form_id & 0xFFFFFF)

    wanted = _FORMID_SUBS.get(rec_type, set())
    out = b''
    for sub in subs:
        data = sub.data
        if (sub.type == _FORMID_ARRAY_SUB or sub.type in wanted) and len(data) == 4:
            old_top = data[3]
            new_top = slot_map.get(old_top, old_top)
            if new_top != old_top:
                data = data[:3] + bytes((new_top,))
        out += pack_subrecord(sub.type, data)
    return pack_record(rec_type, form_id, flags, out, form_ver or FORM_VERSION_SSE)


# ---------------------------------------------------------------------------
# Main patching pass
# ---------------------------------------------------------------------------

def _merged_master_list(input_paths: list) -> list:
    """Build the union of every input plugin's own masters plus the plugins
    themselves, in dependency-safe order (each plugin's masters are added
    before the plugin), so all patched plugins can share one output file."""
    merged = []
    seen = set()
    for path in input_paths:
        header, _, _ = t5r.read_tes5_file(path)
        own_masters = [t5r._zstring(s.data) for s in header.subrecords if s.type == 'MAST']
        for m in own_masters:
            if m not in seen:
                merged.append(m)
                seen.add(m)
        name = os.path.basename(path)
        if name not in seen:
            merged.append(name)
            seen.add(name)
    return merged


def patch_plugins(input_paths: list, output_path: str, language: str = 'english',
                  esl: bool = True, verbose: bool = False,
                  clean_masters: bool = True) -> int:
    """Scan one or more Skyrim plugins and write ONE merged slot-44 patch.

    Every input plugin becomes a master of the single output patch (so a
    load order's worth of armor mods can share one "Slot44 Patch.esp"); each
    plugin's FormIDs are remapped from its own local master-slot indices
    into the merged master list before records are emitted. Clean Masters
    then runs once over the whole merged output, dropping whichever of the
    merged masters ended up unreferenced (e.g. Update.esm when nothing the
    patch touches actually references it).

    `input_paths` is treated as load order (earlier = lower priority): when
    more than one plugin defines the same FormID (e.g. an armor mod
    overriding a vanilla ARMO's biped flags), only the LAST plugin's version
    of that record is used — matching how the game itself resolves
    overrides — rather than operating on every plugin's copy independently.

    Returns the number of records in the patch (0 = no patch written).
    """
    merged_masters = _merged_master_list(input_paths)

    # ── Resolve overrides: for every ARMO/ARMA FormID touched by more than
    #    one plugin, keep only the LAST plugin's copy (load-order winner) —
    #    matching how the game itself resolves overrides — rather than
    #    operating on every plugin's copy of the record independently.
    #    Everything from here on is keyed by merged_fid (merged-master
    #    space), since the same real-world record carries a different raw
    #    top byte in each plugin's own file and cross-plugin comparisons on
    #    raw local FormIDs would silently miss matches. ──
    # merged_fid -> (rec, input_name, local_to_merged, tables, is_localized)
    resolved: dict = {}
    for input_path in input_paths:
        input_name = os.path.basename(input_path)
        header, records, is_localized = t5r.read_tes5_file(input_path)
        own_masters = [t5r._zstring(s.data) for s in header.subrecords if s.type == 'MAST']
        local_to_merged = {i: merged_masters.index(m) for i, m in enumerate(own_masters)}
        local_to_merged[len(own_masters)] = merged_masters.index(input_name)

        tables = {'strings': {}, 'dlstrings': {}}
        if is_localized:
            tables = _load_string_tables(input_path, language)
            if not tables['strings']:
                print(f'WARNING: {input_name} is localized but no string tables were '
                      f'found — item names in the patch will be empty.')

        for rec in records:
            if rec.type not in ('ARMO', 'ARMA'):
                continue
            merged_fid = (local_to_merged[rec.form_id >> 24] << 24) | (rec.form_id & 0xFFFFFF)
            resolved[merged_fid] = (rec, input_name, local_to_merged, tables, is_localized)

    def _merged_formid_refs(rec, local_to_merged, sig):
        """FormIDs in `sig` subrecords of `rec`, translated to merged space."""
        out = []
        for s in rec.subrecords:
            if s.type == sig and len(s.data) == 4:
                raw = struct.unpack_from('<I', s.data)[0]
                top = local_to_merged.get(raw >> 24)
                if top is not None:
                    out.append((top << 24) | (raw & 0xFFFFFF))
        return out

    # ── Selection (all comparisons in merged space) ──
    skin_arma = {}   # merged_fid -> ctx, for winning ARMAs that are skin torsos
    for merged_fid, ctx in resolved.items():
        rec = ctx[0]
        if rec.type == 'ARMA' and _is_skin_torso_arma(rec):
            skin_arma[merged_fid] = ctx

    skin_armo = {}   # merged_fid -> (ctx, [referenced skin_arma merged_fids])
    for merged_fid, ctx in resolved.items():
        rec, _, local_to_merged, _, _ = ctx
        if rec.type != 'ARMO':
            continue
        refs = [f for f in _merged_formid_refs(rec, local_to_merged, 'MODL') if f in skin_arma]
        if refs:
            skin_armo[merged_fid] = (ctx, refs)

    n32 = {}   # merged_fid -> ctx
    for merged_fid, ctx in resolved.items():
        rec = ctx[0]
        if merged_fid in skin_arma or merged_fid in skin_armo:
            continue
        flags, _ = _biped_flags(rec)
        if flags is None or not flags & SLOT_32_BODY or flags & SLOT_44_LOWERBODY:
            continue
        n32[merged_fid] = ctx

    # ── Which merged master slots are actually needed (record's own slot +
    #    every FormID-typed subrecord field it carries), so Clean Masters can
    #    compact the master list before anything is packed into a FormID's
    #    one-byte slot field. ──
    used_merged: set = set()
    for merged_fid, ctx in {**skin_arma, **{k: v[0] for k, v in skin_armo.items()}, **n32}.items():
        rec, _, local_to_merged, _, _ = ctx
        used_merged.add(merged_fid >> 24)
        for s in rec.subrecords:
            if (s.type == _FORMID_ARRAY_SUB or s.type in _FORMID_SUBS.get(rec.type, ())) \
                    and len(s.data) == 4:
                used_merged.add(local_to_merged.get(s.data[3], s.data[3]))

    final_masters = merged_masters
    merged_to_final = {i: i for i in range(len(merged_masters))}
    if clean_masters:
        final_masters = [m for i, m in enumerate(merged_masters) if i in used_merged]
        merged_to_final = {old: new for new, old in enumerate(
            i for i in range(len(merged_masters)) if i in used_merged)}
        dropped = [m for i, m in enumerate(merged_masters) if i not in used_merged]
        if dropped and verbose:
            print(f'  Clean Masters: dropping unused master(s): {", ".join(dropped)}')

    if len(final_masters) > 254:
        raise SystemExit(
            f'ERROR: this patch would need {len(final_masters)} masters after '
            f'Clean Masters, but a plugin can have at most 254. Deselect some '
            f'of the input plugins (prefer ones without any armor/body records) '
            f'and try again.')

    # Patch's own new-record space always sits one slot past every real
    # master, now that final_masters is guaranteed small.
    patch_master_index = len(final_masters)

    def _to_final(local_to_merged):
        return {local: merged_to_final[merged]
               for local, merged in local_to_merged.items()
               if merged in merged_to_final}

    warnings: list = []
    out_armo: list = []
    out_arma: list = []
    log: list = []
    next_oid = 0x800
    per_plugin_counts: dict = {}   # input_name -> [skin_arma, skin_armo, n32]

    # ── Pass 1: split skin torso ARMAs and create thigh/calf companions ──
    skin_new: dict = {}   # torso ARMA final_fid -> [thigh final_fid, calf final_fid]
    for merged_fid, (rec, input_name, local_to_merged, tables, is_localized) in skin_arma.items():
        local_to_final = _to_final(local_to_merged)
        flags, bod = _biped_flags(rec)
        edid_sub = _get_sub(rec, 'EDID')
        edid = t5r._zstring(edid_sub.data) if edid_sub else f'{rec.form_id:08X}'

        # Torso override: keep body/forearms(+amulet/ring), drop calves —
        # they move to the dedicated Calves addon.
        new_mask = flags & ~(SLOT_38_CALVES | SLOT_44_LOWERBODY) | (flags & TORSO_MASK)
        torso_bytes = _repack(
            rec, lambda s, b=bod, m=new_mask:
                _with_biped_flags(s, m) if s is b else s.data)
        out_arma.append(_remap_record_bytes(torso_bytes, local_to_final))

        fids = []
        for suffix, mask in (('Thighs', THIGH_MASK), ('Calves', CALVES_MASK)):
            fid = (patch_master_index << 24) | next_oid
            next_oid += 1
            clone_bytes = _build_skin_clone(rec, fid, f'TES4{edid}{suffix}', mask)
            out_arma.append(_remap_record_bytes(clone_bytes, local_to_final))
            fids.append(fid)
        skin_new[merged_fid] = fids
        per_plugin_counts.setdefault(input_name, [0, 0, 0])[0] += 1
        log.append(f'  [{input_name}] ARMA {rec.form_id:08X} {edid}: split -> torso '
                   f'{new_mask:08X} + Thighs(44) + Calves(38)')

    # ── Pass 2: skin ARMOs referencing split torso ARMAs get the new
    #    armatures appended (and are excluded from the slot-44 addition) ──
    for merged_fid, ((rec, input_name, local_to_merged, tables, is_localized), refs) \
            in skin_armo.items():
        local_to_final = _to_final(local_to_merged)
        new_refs = [fid for ref in refs for fid in skin_new.get(ref, [])]
        if not new_refs:
            continue
        last_modl = [s for s in rec.subrecords if s.type == 'MODL'][-1]

        def xform(sub, last=last_modl, refs=new_refs, r=rec):
            data = _localize_sub(sub, is_localized, tables, r, warnings)
            if data is None:
                return None
            if sub is last:
                return [(sub.type, data)] + [('MODL', struct.pack('<I', f))
                                             for f in refs]
            return data
        armo_bytes = _repack(rec, xform)
        out_armo.append(_remap_record_bytes(armo_bytes, local_to_final))
        edid_sub = _get_sub(rec, 'EDID')
        per_plugin_counts.setdefault(input_name, [0, 0, 0])[1] += 1
        log.append(f'  [{input_name}] ARMO {rec.form_id:08X} '
                   f'{t5r._zstring(edid_sub.data) if edid_sub else "?"}: '
                   f'+{len(new_refs)} skin armatures')

    # ── Pass 3: playable slot-32 items get slot 44 added ──
    for merged_fid, (rec, input_name, local_to_merged, tables, is_localized) in n32.items():
        local_to_final = _to_final(local_to_merged)
        flags, bod = _biped_flags(rec)
        rec_bytes = _repack(
            rec, lambda s, b=bod, r=rec:
                _with_biped_flags(s, struct.unpack_from('<I', s.data)[0] | SLOT_44_LOWERBODY)
                if s is b else _localize_sub(s, is_localized, tables, r, warnings))
        rec_bytes = _remap_record_bytes(rec_bytes, local_to_final)
        out = (out_armo if rec.type == 'ARMO' else out_arma)
        out.append(rec_bytes)
        per_plugin_counts.setdefault(input_name, [0, 0, 0])[2] += 1

    for input_path in input_paths:
        input_name = os.path.basename(input_path)
        if input_name in per_plugin_counts:
            c = per_plugin_counts[input_name]
            print(f'{input_name}: {c[0]} skin ARMAs split, {c[1]} skin ARMOs '
                  f're-armatured, {c[2]} playable records +slot44')

    total_skin_split = len(skin_arma)
    total_rearmored = len(skin_armo)
    total_n32 = len(n32)
    n = len(out_armo) + len(out_arma)
    for w in warnings[:20]:
        print(f'  WARNING: {w}')
    if verbose:
        print('\n'.join(log))
    print(f'Total: {total_skin_split} skin ARMAs split, {total_rearmored} skin ARMOs '
          f're-armatured, {total_n32} playable records +slot44, {n} records total')
    if n == 0:
        print('Nothing to patch — no output written.')
        return 0

    out = _pack_header(final_masters, n, next_oid, esl,
                       'TES4 conversion body-slot patch: splits naked skin into '
                       'torso/thighs/calves addons and adds biped slot 44 to '
                       'slot-32 body items, across ' + ', '.join(
                           os.path.basename(p) for p in input_paths) + '.')
    if out_armo:
        out += pack_top_group('ARMO', b''.join(out_armo))
    if out_arma:
        out += pack_top_group('ARMA', b''.join(out_arma))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(out)
    print(f'Written: {output_path}  ({n} records, '
          f"{'ESL-flagged ' if esl else ''}masters: {final_masters})")
    return n


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('plugins', nargs='+',
                        help='Path(s) to the TES5 plugin(s) to patch (e.g. Skyrim.esm '
                             'Dawnguard.esm ...). All are merged into one output patch, '
                             'in the order given (load order).')
    parser.add_argument('-o', '--output', default=None,
                        help='Output patch path (default: output/Oblivion.esm/'
                             '"Slot44 Patch.esp")')
    parser.add_argument('--language', default='english',
                        help='String-table language for localized masters (default: english)')
    parser.add_argument('--no-esl', action='store_true',
                        help='Do not set the ESL (light plugin) flag on the patch')
    parser.add_argument('--no-clean-masters', action='store_true',
                        help='Keep every input plugin\'s masters, even if unused '
                             '(default: drop masters not referenced by the patch)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='List every patched record')
    args = parser.parse_args()

    for plugin in args.plugins:
        if not os.path.exists(plugin):
            print(f'ERROR: plugin not found: {plugin}')
            return 1

    out = args.output
    if out is None:
        out = os.path.join(_REPO_ROOT, 'output', 'Oblivion.esm', 'Slot44 Patch.esp')

    patch_plugins(args.plugins, out, language=args.language,
                 esl=not args.no_esl, verbose=args.verbose,
                 clean_masters=not args.no_clean_masters)
    return 0


if __name__ == '__main__':
    sys.exit(main())
