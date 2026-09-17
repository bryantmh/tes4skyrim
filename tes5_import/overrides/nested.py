"""Override orchestration for a plugin that has TES4 masters.

This is the plugin-side counterpart to `override_merge` (the master's converted
bytes), `master_manifest` (the master's companion pairings), `export_diff`
(what the author changed) and `override_builder` (applying those changes to the
master's record). It owns everything import_main needs to route a record down
the override path:

    ctx = OverrideContext(export_dir, masters, num_tes4_masters, output_root)
    ov = ctx.build(rec)          # None -> not an override, convert normally
    if ov.record_bytes: ...      # emit; else correctly dropped

The rule, stated once: an OVERRIDE is the master's converted record bytes
EXACTLY, with only the fields the author changed substituted in — and
authorship comes from diffing the two TES4 EXPORTS, never from comparing two
conversion runs (see export_diff for why that distinction is load-bearing).
"""

import os
import struct
from collections import Counter, namedtuple

from .diff import diff_records
from .manifest import load_master_manifests
from .builder import (RECONVERT_KEYS, apply_changes, rebuild_sndr_override,
                      soun_companion_changes, split_subrecords)
from .master_index import load_master_index
from ..record_types.world import restamp_wrld_mnam
from ..base.text_reader import parse_export_directory, remap_formid
from ..base.writer import (PluginWriter, RECORD_HEADER_SIZE, pack_group)

# Types whose override CANNOT be expressed against the master's output because
# conversion does not produce a corresponding record to substitute into
# (ROAD converts to nothing at all). These are counted and reported, never
# silently dropped.
#
# PGRD is NOT in this set. A pathgrid converts to a NAVM, which is a BRAND NEW
# record with its own FormID — it patches nothing in the master, so "no record
# to substitute into" never applied to it. Treating it as unmappable dropped
# the pathgrid before it ever reached the navmesh generator, and because the
# drop happened in the override pass the generator never saw a job for it:
# ElsweyrAnequina overrides 863 of Oblivion.esm's Tamriel cells and every one
# lost its navmesh, shipping 487 navmeshed cells where 1,351 pathgrids exist.
# The CELL/LAND/REFR all converted fine, so the landmass looked complete while
# nothing in it could path. An overriding PGRD is routed like a new LAND
# instead — nested under the master's cell in the type-9 temporary group.
OVERRIDE_UNMAPPABLE_TYPES = frozenset({'ROAD'})

# Record header flag bit 5. Both games use it, and the meaning is the same:
# the author DELETED this record. xEdit treats bit 5 as 'Deleted' for every
# signature (wbFlagsList's aDeleted branch, Core/wbInterface.pas:5874).
#
# A deleted override is an empty stub — FormID and flags only, with every
# subrecord stripped. That shape has to be recognised BEFORE the field diff
# runs, because a field-by-field comparison reads all those absent subrecords
# as authored changes: DLCBattlehornCastle deletes 74 references, and the diff
# reported them as 74 XSCL.Scale + 73 NAME "edits" with no mapping, so all 74
# shipped ALIVE at the master's position.
DELETED_FLAG = 0x20

#: status is 'emitted'|'deleted'|'unchanged'|'no-base'|'no-path'|'reconvert'.
Override = namedtuple('Override', ['status', 'out_fid', 'record_bytes'])


def export_master_names(export_dir: str) -> list:
    """This plugin's TES4 master names, in load order, from its export header."""
    header = os.path.join(export_dir, '_HEADER.txt')
    if not os.path.isfile(header):
        return []
    names = []
    with open(header, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('Master['):
                _, _, val = line.partition('=')
                names.append(val.strip())
    return names


def export_root(export_dir: str) -> str:
    """The `export/` root, given any plugin's record folder.

    A game-Data plugin sits directly under it (`export/Oblivion.esm/`), but an
    imported mod's plugins are nested inside their mod's shared folder
    (`export/<Mod>/<plugin>/`), so the parent of a record dir is not reliably
    the root. The registry file marks the real one.
    """
    d = os.path.dirname(os.path.normpath(export_dir))
    # At most two levels: <root>/<mod>/<plugin> is the deepest shape there is.
    for cand in (d, os.path.dirname(d)):
        if cand and os.path.isfile(os.path.join(cand, 'sources.json')):
            return cand
    return d


def master_export_dir(root: str, name: str) -> str:
    """Where master `name`'s records live under `root`.

    Masters resolve as sibling directories, EXCEPT that an imported mod's
    plugins live inside their mod's folder. Consulting the registry is what
    lets a plugin master a resource pack's ESM after that pack moved into a
    group folder; without it the master silently reads as missing and every
    override is diffed against nothing.
    """
    try:
        from output_layout import record_dir
        got = record_dir(root, name)
        if os.path.isdir(got):
            return str(got)
    except ImportError:
        pass
    return os.path.join(root, name)


def load_master_export(export_dir: str) -> dict:
    """The masters' export records, keyed by the TES4 FormID THIS PLUGIN uses.

    This is the baseline for deciding what a plugin's author actually changed.
    Diffing against the master's EXPORT (rather than against a second
    conversion) is what makes the override path deterministic: a field neither
    export touches is never rewritten, so it cannot drift.

    Each master's ids are re-keyed into THIS plugin's index space, and
    `remap` carries that mapping for everything a master can name: its own
    records, plus each of ITS masters by name.

    See: docs/commentary/tes5_import_override.md#re-keying-the-masters-ids
    """
    names = export_master_names(export_dir)
    if not names:
        return {}
    slot_of = {n.lower(): i for i, n in enumerate(names)}

    root = export_root(export_dir)
    out = {}
    for slot, name in enumerate(names):
        mdir = master_export_dir(root, name)
        if not os.path.isdir(mdir):
            print(f"  WARNING: master export not found ({mdir}); "
                  f"overrides cannot be diffed against it")
            continue
        own = export_master_names(mdir)
        remap = {len(own): slot}
        for k, sub in enumerate(own):
            target = slot_of.get(sub.lower())
            if target is not None:
                remap[k] = target
        for rec in parse_export_directory(mdir):
            fid = rec.get('FormID')
            if not fid:
                continue
            try:
                raw = int(fid, 16)
            except ValueError:
                continue
            mapped = remap.get((raw >> 24) & 0xFF)
            if mapped is None:
                # Names a file this plugin does not load: unreachable from here.
                continue
            out['%08X' % ((mapped << 24) | (raw & 0x00FFFFFF))] = rec
    return out


# A converted record may legitimately carry a signature the plugin's source
# type does not name. A REFR that places a LEVELLED CREATURE (LVLC) becomes an
# ACHR aimed at a generated shell NPC_ (see leveled_actors), so the master's
# converted record for a source REFR can be either. Keyed by source signature.
_ALSO_ACCEPTED = {
    'REFR': (b'ACHR',),
}


def _expected_output_sig(tes4_sig: str) -> bytes:
    """The TES5 signature a TES4 record converts to, or b'' if unknown.

    Several types are RENAMED by conversion (CREA->NPC_, CLOT->ARMO, ...), so
    the master's record legitimately carries a different signature than the
    plugin's source; TYPE_MAP is the single definition of those renames and is
    reused here rather than restated.
    """
    if not tes4_sig:
        return b''
    from ..registry import TYPE_MAP
    return TYPE_MAP.get(tes4_sig, tes4_sig).encode('ascii', 'replace')


def _signature_mismatch(tes4_sig: str, base_sig: bytes) -> bool:
    """True when the master's record is the WRONG record to override.

    See the call site: a source id can land on an unrelated master record, and
    adopting it ships one record type's body under another's signature.
    """
    want = _expected_output_sig(tes4_sig)
    if not want or base_sig == want:
        return False
    return base_sig not in _ALSO_ACCEPTED.get(tes4_sig, ())


def make_deleted_record(base: bytes) -> bytes:
    """The master's record restated as a DELETED override.

    The body is type-dependent: REFR/ACHR/ACRE keep only NAME, NAVM alone is
    emptied, and every other type keeps the master's body verbatim.  Only the
    Deleted flag is added, so a compressed record stays compressed.

    See: docs/commentary/tes5_import_override.md#deleting-masters-record-three-shapes
    """
    if len(base) < RECORD_HEADER_SIZE:
        return base
    sig = base[:4]
    flags = struct.unpack_from('<I', base, 8)[0] | DELETED_FLAG
    if sig in (b'REFR', b'ACHR', b'ACRE'):
        # Reference records keep ONLY NAME — the base object they placed.
        body = b''
        for sub_sig, payload in split_subrecords(base):
            if sub_sig == b'NAME':
                body = sub_sig + struct.pack('<H', len(payload)) + payload
                break
    elif sig == b'NAVM':
        # The one type vanilla empties.
        body = b''
    else:
        body = base[RECORD_HEADER_SIZE:]
    return (sig + struct.pack('<II', len(body), flags)
            + base[12:RECORD_HEADER_SIZE] + body)


def master_output_formid(src_fid: str, master_manifest) -> int:
    """The master's converted FormID for one of its source records.

    The manifest is authoritative — it was recorded when the record was
    created. Records the master emits as pre-built GRUP bytes (CELL/WRLD/REFR/
    ACHR/ACRE) never pass through the conversion loop and so have no manifest
    entry, but they also never change FormID: their id is the source id with
    the load-order index shifted, exactly like any reference to them. Falling
    back to that shift is arithmetic, not a guess, and the caller verifies it
    against the master index — a wrong id yields no record, and the override is
    dropped rather than emitted wrong.
    """
    if master_manifest is not None:
        fid = master_manifest.output_formid(src_fid)
        if fid:
            return fid
    try:
        return remap_formid(int(src_fid, 16), is_own_id=True)
    except (ValueError, TypeError):
        return 0


def detect_injected_records(all_records: list, master_export: dict,
                            num_tes4_masters: int,
                            writer: PluginWriter) -> dict:
    """{raw TES4 FormID -> our-space FormID} for INJECTED records.

    Oblivion let a plugin ADD a record carrying a MASTER's load-order index.
    Remapping those like an override puts them in the converted master's range
    at ids it does not define, so the engine resolves against the master, finds
    nothing, and hangs on load. They are new records — the caller redirects
    them into our own space (text_reader.set_injected_formids) before anything
    converts.

    Authority is the master's EXPORT (its raw TES4 FormIDs), never its
    converted output: conversion re-keys DIAL/INFO and skips whole types, so
    judging by the output called 1693 records injected instead of the real 3.
    """
    injected = {}
    if not master_export:
        return injected
    for rec in all_records:
        fid_raw = int(rec.get('FormID', '0'), 16)
        if not fid_raw or (fid_raw >> 24) & 0xFF >= num_tes4_masters:
            continue          # our own record, not master-indexed
        if (rec.get('FormID') or '').upper() in master_export:
            continue          # a real override of a master record
        # Keyed on the raw TES4 id: an injected record keeps a stable identity
        # across builds, which other plugins and existing saves both rely on.
        injected[fid_raw] = writer.derive_formid('INJECTED', fid_raw)
    return injected


class OverrideContext:
    """Everything a plugin's import needs to emit overrides of its masters."""

    def __init__(self, export_dir: str, masters: list, num_tes4_masters: int,
                 output_root: str):
        self.export_dir = export_dir
        self.master_index = load_master_index(
            masters, num_tes4_masters, output_root)
        self.master_manifest = load_master_manifests(
            masters, num_tes4_masters, output_root,
            export_root=export_root(export_dir))
        self.master_export = load_master_export(export_dir)
        self.stats = Counter()
        self.unmapped_keys = Counter()
        # WRLD overrides this plugin emitted, {out FormID -> record bytes}.
        # A plugin that adds cells to a MASTER's worldspace needs the WRLD
        # record in front of their type-1 group, and it must be THIS record
        # rather than the master's — emitting the master's copy as well would
        # ship the same FormID twice and the engine keeps the LAST one, which
        # silently reverted the author's own worldspace edits (Tamriel.esp
        # widens NAM0/NAM9 to hold the land it adds; the stale duplicate put
        # the vanilla bounds back and clipped the new terrain off the map).
        self.emitted_wrld = {}
        # Records emit_nested_overrides pulled from the master as an anchor.
        # The WRLD builder runs afterwards and must not anchor the same record
        # again — that ships the FormID twice and the engine drops one copy.
        self.anchored_wrld = set()
        # {LAND source FormID -> (ok, bytes)} from the parallel precompute, set
        # by import_plugin before the override pass. A NEW LAND nested under a
        # master's cell (_attach_new_records) is converted from this rather
        # than re-running convert_LAND, which is the heaviest per-record
        # converter in the pipeline (~1 ms each).
        self.land_cache = None

    def __len__(self):
        return len(self.master_export)

    def master_record(self, rec: dict):
        """The master's EXPORT record this plugin record overrides, or None."""
        src_fid = (rec.get('FormID') or '').upper()
        return self.master_export.get(src_fid)

    def build(self, rec: dict, sig: str = None):
        """Build the override for one plugin record.

        Returns None when the record is NOT an override (a new record — the
        caller converts it normally), else an Override whose `record_bytes` is
        set only for status 'emitted':

          emitted    the master's bytes with the author's changes applied
          unchanged  authorially identical to the master — pure bloat, drop
          no-base    the master's conversion has no record to override
          no-path    the type's conversion output has no record to patch
        """
        # A PGRD is never an override in OUTPUT space, even when this plugin
        # edits the master's pathgrid: it converts to a NAVM carrying a NEW
        # FormID, so there is nothing of the master's to patch. Route it as a
        # new record ALWAYS — `_attach_new_records` nests the generated navmesh
        # under the master's cell. Returning an Override here instead marked it
        # inexpressible and dropped it, which is why every master cell a plugin
        # touched lost its navmesh (863 Tamriel cells in ElsweyrAnequina).
        # The author's edited pathgrid is the one that converts, so the
        # navmesh reflects their changes rather than the master's original.
        if sig == 'PGRD':
            return None

        master_rec = self.master_record(rec)
        if master_rec is None:
            return None

        src_fid = (rec.get('FormID') or '').upper()
        if sig in OVERRIDE_UNMAPPABLE_TYPES:
            self.stats['no-path'] += 1
            return Override('no-path', 0, b'')

        out_fid = master_output_formid(src_fid, self.master_manifest)
        base = self.master_index.record(out_fid) if out_fid else b''
        if not base:
            # The master's conversion dropped this record, so there is nothing
            # to override. Emitting it would leave a record the engine cannot
            # resolve against the master.
            self.stats['no-base'] += 1
            return Override('no-base', out_fid, b'')

        # The id must resolve to a record of the SAME TYPE. A plugin's source
        # id can land on an unrelated master record — Elsweyr Anequina's NPC_
        # 0100110C converts to 0200110C, which in Oblivion.esm's own space is
        # a REFR — and adopting that record's bytes and nesting shipped the
        # NPC_ as a "REFR" inside a bogus top-level GRUP (xEdit: "File contains
        # top level group without known sort order: GRUP Top 'REFR'"). Treat a
        # type mismatch as "no master record", so the caller converts it as the
        # new record it actually is.
        if _signature_mismatch(sig or rec.get('Signature') or '', base[:4]):
            self.stats['no-base'] += 1
            return Override('no-base', 0, b'')

        if int(rec.get('RecordFlags') or 0) & DELETED_FLAG:
            # The author DELETED this record. Deletion is expressed by the
            # header flag, not by the field diff — the plugin's record is an
            # empty stub, so diffing it against the master reports every
            # subrecord the master has as an unmappable "change" and the
            # record ships alive. Emit the master's header with the flag set
            # and NO body, which is exactly what the deleting plugin itself
            # ships and what the engine reads as "remove this reference".
            self.stats['deleted'] += 1
            return Override('deleted', out_fid,
                            make_deleted_record(base))

        changes = diff_records(master_rec, rec)
        if not changes:
            # An override that changes nothing is pure bloat.
            self.stats['unchanged'] += 1
            return Override('unchanged', out_fid, b'')

        if any((sig or rec.get('Signature'), key) in RECONVERT_KEYS
               for key in changes):
            # The authored change rewrites content whose conversion mints
            # companion records (spell effect lists -> aimed-MGEF clones).
            # That cannot be spliced into the master's bytes, so the caller
            # reconverts the record from the plugin's export instead — its
            # FormID still lands on the master's, keeping it an override.
            self.stats['reconverted'] += 1
            return Override('reconvert', out_fid, b'')

        record_bytes, _applied, unmapped = apply_changes(
            base, changes, rec, master_rec)
        for key in unmapped:
            self.unmapped_keys[key] += 1

        # An override whose every authored change was UNMAPPABLE comes back
        # byte-identical to the master.  diff_records saw a difference, so the
        # earlier `not changes` drop did not fire, but apply_changes expressed
        # none of it — shipping the result adds a record that overrides the
        # master with the master's own bytes.
        #
        # Measured on Knights.esp: 43 PACK overrides, every one byte-identical
        # to Oblivion.esm's, all from 42 unmappable Condition[] changes.
        #
        # Drop it: the master already says exactly this.
        if record_bytes == base:
            self.stats['unchanged'] += 1
            return Override('unchanged', out_fid, b'')

        self.stats['emitted'] += 1
        return Override('emitted', out_fid, record_bytes)

    def build_soun_companion(self, rec: dict, writer) -> bytes:
        """Override of the master's SNDR when a SOUN's volume/falloff changed.

        Returns b'' when nothing sound-related was authored (the master's SNDR
        already says the right thing) or when the master's companion cannot be
        located. The SNDR keeps the MASTER's FormID, so every SOUN already
        pointing at it — including this override's own SDSC — still resolves.
        """
        master_rec = self.master_record(rec)
        if master_rec is None:
            return b''
        changes = diff_records(master_rec, rec)
        if not soun_companion_changes(changes):
            return b''
        src_fid = (rec.get('FormID') or '').upper()
        for fid in self.master_manifest.companions(src_fid):
            base = self.master_index.record(fid)
            if base[:4] == b'SNDR':
                out = rebuild_sndr_override(base, rec, writer)
                if out:
                    self.stats['soun-companion'] += 1
                    for key in changes:
                        self.unmapped_keys.pop(key, None)
                return out
        return b''

    def report(self):
        print(f"  Overrides: {self.stats['emitted']} emitted, "
              f"{self.stats['deleted']} deleted by the author, "
              f"{self.stats['reconverted']} reconverted (effect-list change), "
              f"{self.stats['unchanged']} unchanged (dropped), "
              f"{self.stats['no-base']} without a converted master record, "
              f"{self.stats['no-path']} inexpressible (PGRD/ROAD)")
        if self.unmapped_keys:
            print(f"  NOTE: {sum(self.unmapped_keys.values())} authored "
                  f"changes in {len(self.unmapped_keys)} field(s) have no "
                  f"output mapping and kept the master's value:")
            for key, count in self.unmapped_keys.most_common(10):
                print(f"    {key}: {count}")


# GRUP types whose label is the FormID of the record that OWNS the group, and
# which the engine binds to that record ONLY by physical adjacency: xEdit's
# TwbGroupRecord.InformPrevMainRecord (wbImplementation.pas ~18023) attaches
# the group to the previous record iff
#     grsGroupType in [1, 6, 7] and aPrevMainRecord.FixedFormID = GroupLabel
# 1 = world children (under WRLD), 6 = cell children (under CELL),
# 7 = topic children (under DIAL). A group of one of these types that is NOT
# immediately preceded by its owning record is attached to nothing, so every
# record inside it is unreachable — as invisible as if it were never written.
OWNED_GROUP_TYPES = frozenset({1, 6, 7})


def _group_sort_key(step: tuple):
    """Ordering key for one GRUP nesting step, matching vanilla's file order.

    A worldspace's children group holds the PERSISTENT cell (its own type-6
    group, labelled by that cell's FormID) followed by the exterior blocks
    (type 4). Vanilla puts the persistent cell FIRST: in the converted
    Oblivion.esm, Tamriel's type-1 group opens with CELL 01023777 and its
    type-6 group, and only then the 21 type-4 blocks.

    Sorting by (group type, label bytes) put it LAST instead, because 6 > 4.
    The engine walks a worldspace's block tree to build the cell grid and
    reaches the persistent cell through that walk; finding an unexpected
    type-6 group after the blocks left TWMP_ValenwoodImproved hanging on the
    main menu, and deleting the exterior blocks in xEdit made it load.

    Within the blocks, order by the UNSIGNED 16-bit halves of the label with
    **X major, Y minor**. The label packs Y into the LOW word, so sorting on
    its own word order yields the TRANSPOSE and lets X descend mid-list; the
    engine's parse-time grid walk never terminates on such a run. See
    import_main._grid_sort_key for the Skyrim.esm census and the symptom.
    """
    gtype, label = step[0], bytes(step[1])
    if gtype in OWNED_GROUP_TYPES:
        # Persistent cell / topic groups lead, in FormID order.
        return (0, gtype, 0, 0, label)
    if gtype in (4, 5) and len(label) == 4:
        y, x = struct.unpack('<HH', label)
        return (1, gtype, x, y, b'')
    return (1, gtype, 0, 0, label)


def _sort_land_first(by_path: dict) -> None:
    """Stably move the LAND to index 0 of every type-9 group, in place."""
    for path, bodies in by_path.items():
        if path[-1][0] == 9 and any(b[:4] == b'LAND' for b in bodies):
            bodies.sort(key=lambda b: b[:4] != b'LAND')


def _build_emitted_at(by_path: dict, master_index) -> dict:
    """{path -> {FormID}} of records serving as their own anchor at that path.

    Also backfills the master's LAND (at index 0) into any type-9 group this
    plugin emits without one, MUTATING `by_path`: a children group REPLACES
    the master's rather than merging, so overriding one REFR would otherwise
    delete the terrain under it.

    See: docs/commentary/tes5_import_override.md#nested-override-emission-passes
    """
    emitted_at = {}
    for path, bodies in by_path.items():
        for body in bodies:
            emitted_at.setdefault(path, set()).add(
                struct.unpack_from('<I', body, 12)[0])

    land_of = getattr(master_index, 'land', None)
    if not callable(land_of):
        return emitted_at

    for path in list(by_path):
        if not path or path[-1][0] != 9 or len(path[-1][1]) != 4:
            continue
        if any(b[:4] == b'LAND' for b in by_path[path]):
            continue
        land_fid = land_of(struct.unpack('<I', path[-1][1])[0])
        land_rec = master_index.record(land_fid) if land_fid else b''
        if land_rec:
            by_path[path].insert(0, land_rec)
            emitted_at.setdefault(path, set()).add(land_fid)
    return emitted_at


def _anchor_for(path: tuple, fid: int, state: dict) -> bytes:
    """The owning record's bytes, pulled from the master if we lack it.

    Counts the pull in `state['anchored']` and records the FormID in
    `state['anchored_fids']` so no later pass anchors it twice.

    See: docs/commentary/tes5_import_override.md#nested-override-emission-passes
    """
    master_index = state['master_index']
    if fid in state['emitted_at'].get(path, ()) or master_index is None:
        return b''
    rec = master_index.record(fid)
    if rec:
        state['anchored'] += 1
        state['anchored_fids'].add(fid)
        if rec[:4] == b'WRLD':
            rec = restamp_wrld_mnam(rec, fid)
    return rec


def _build_body(prefix: tuple, depth: int, state: dict) -> bytes:
    """Serialize everything at `prefix`, recursing into deeper paths.

    A record's own children group must directly FOLLOW that record, so records
    and their owned groups interleave rather than being emitted in two runs.

    See: docs/commentary/tes5_import_override.md#nested-override-emission-passes
    """
    by_path = state['by_path']
    deeper = {p[:depth + 1] for p in by_path
              if len(p) > depth and p[:depth] == prefix}
    owned = {struct.unpack('<I', child[depth][1])[0]: child
             for child in deeper
             if child[depth][0] in OWNED_GROUP_TYPES
             and len(child[depth][1]) == 4}
    body = b''
    for record_bytes in by_path.get(prefix, ()):
        body += record_bytes
        child = owned.pop(struct.unpack_from('<I', record_bytes, 12)[0], None)
        if child is not None:
            inner = _build_body(child, depth + 1, state)
            if inner:
                body += pack_group(child[depth][0], child[depth][1], inner)

    rest = [c for c in deeper
            if c[depth][0] not in OWNED_GROUP_TYPES or c in owned.values()]
    for child in sorted(rest, key=lambda p: _group_sort_key(p[depth])):
        inner = _build_body(child, depth + 1, state)
        if not inner:
            continue
        gtype, label = child[depth][0], child[depth][1]
        if gtype in OWNED_GROUP_TYPES and len(label) == 4:
            body += _anchor_for(prefix, struct.unpack('<I', label)[0], state)
        body += pack_group(gtype, label, inner)
    return body


def _emit_paths(by_path: dict, writer: PluginWriter, master_index,
                emitted_at: dict, anchored_fids: set) -> int:
    """Serialize every path in `by_path` into `writer`; returns anchors pulled.

    See: docs/commentary/tes5_import_override.md#nested-override-emission-passes
    """
    state = {'by_path': by_path, 'master_index': master_index,
             'emitted_at': emitted_at, 'anchored_fids': anchored_fids,
             'anchored': 0}
    for top in sorted({p[0] for p in by_path},
                      key=lambda t: (t[0], bytes(t[1]))):
        body = _build_body((top,), 1, state)
        if body:
            writer.add_raw_group(top[1].decode('ascii', 'replace'), body)
    return state['anchored']


def emit_nested_overrides(records: list, writer: PluginWriter,
                          master_index=None, anchored_fids: set = None) -> tuple:
    """Write override records back into the master's exact GRUP nesting.

    A CELL is only reachable by the engine from inside its block/sub-block
    hierarchy, and a REFR from its parent cell's type-6 children group; the
    nesting is COPIED from the master (MasterIndex.group_path), never
    recomputed. `records` is [(output_formid, record_bytes, path), ...] where
    `path` is the ((grup_type, label_bytes), ...) nesting it belongs in.
    A record with no path is counted as an orphan and skipped.

    Returns (emitted_count, orphan_count, anchored_count).

    See: docs/commentary/tes5_import_override.md#nested-override-emission-passes
    """
    by_path = {}
    orphans = 0
    for _out_fid, record_bytes, path in records:
        if not path:
            orphans += 1
            continue
        by_path.setdefault(path, []).append(record_bytes)

    _sort_land_first(by_path)
    emitted_at = _build_emitted_at(by_path, master_index)
    if anchored_fids is None:
        anchored_fids = set()
    anchored = _emit_paths(by_path, writer, master_index, emitted_at,
                           anchored_fids)
    return len(records) - orphans, orphans, anchored


def _append_relinked_navms(pending: list, ctx) -> None:
    """Queue the MASTER navmeshes that edge-linking rewrote, ONCE.

    `build_nested_overrides` runs per signature group (CELL/WRLD/REFR, then
    DIAL/INFO), so the list is cleared as it is consumed: appending it in both
    passes shipped all 751 master navmeshes twice, and the duplicate record
    shadowed the real one.

    See: docs/commentary/tes5_import_navmesh.md#cross-plugin-edge-links
    """
    relinked = getattr(ctx, 'relinked_master_navms', None)
    if not relinked:
        return
    for record_bytes, meta in relinked:
        pending.append((meta['fid'], record_bytes,
                        ctx.master_index.group_path(meta['fid'])))
    ctx.relinked_master_navms = []


def _readd_bark_parents(by_type: dict, unattached: list) -> int:
    """Re-add the DIAL parents of handed-back bark INFOs; return how many.

    A bark INFO needs its parent DIAL in the same batch or the normal
    builder has no topic to group it under.  That parent is the MASTER's
    shared GREETING/HELLO record, counted as an unchanged override and
    dropped, so this plugin's own copy is re-added.  The builder re-keys it
    into per-quest topics of our own; the master's record stays untouched.
    """
    parents = {(r.get('ParentDIAL') or '').upper()
               for s, r in unattached if s == 'INFO'}
    if not parents:
        return 0
    seen = {(r.get('FormID') or '').upper()
            for s, r in unattached if s == 'DIAL'}
    added = 0
    for rec in by_type.get('DIAL', []):
        fid = (rec.get('FormID') or '').upper()
        if fid in parents and fid not in seen:
            unattached.append(('DIAL', rec))
            added += 1
    return added


def build_nested_overrides(by_type: dict, sigs: tuple, ctx: OverrideContext,
                           writer: PluginWriter, label: str) -> list:
    """Emit overrides for record types that live inside GRUP hierarchies.

    Used for CELL/WRLD/REFR/ACHR/ACRE/LAND (the cell tree) and DIAL/INFO (the
    topic tree). The shared reasoning: a record's child GRUP REPLACES the
    master's — it is not merged. A plugin that rebuilds the hierarchy therefore
    deletes every child the master put there: renaming 571 cells emptied them
    all (black, contentless interiors in-game), and a rebuilt DIAL would drop
    the master's INFO list. So each override goes out as a flat record carrying
    only the author's changes, placed in the master's exact nesting, with no
    children of its own.

    Ship ONLY the records this plugin changes — never the master's whole child
    list. Verified against BS_DLC_patch.esp: 54 cell overrides, ~5 REFRs each
    (278 total). The master's other references stay visible because ONAM (built
    by the writer at save time) tells the engine to keep loading a cell's
    temporary children on demand even though the cell record is overridden.

    Returns the records that are NEW to this plugin and sit in its OWN
    hierarchy rather than the master's. A master-dependent plugin can carry a
    whole world of its own (Morroblivion lists Oblivion.esm as a master but
    every one of its 387,813 world records is new), and those must be handed
    back to the normal group builders — the override path cannot express them.
    """
    pending = []
    new_records = []
    dropped = 0
    for sig in sigs:
        for rec in by_type.get(sig, []):
            ov = ctx.build(rec, sig)
            if ov is None:
                new_records.append((sig, rec))
                continue
            # 'deleted' carries real bytes (the author's deletion) and must be
            # shipped in the master's nesting exactly like an edit — dropping
            # it would leave the master's reference alive in-game.
            if ov.status not in ('emitted', 'deleted'):
                dropped += 1
                continue
            if sig == 'WRLD':
                ctx.emitted_wrld[ov.out_fid] = ov.record_bytes
            pending.append((ov.out_fid, ov.record_bytes,
                            ctx.master_index.group_path(ov.out_fid)))

    _append_relinked_navms(pending, ctx)

    new_done, unattached = _attach_new_records(new_records, ctx, pending)

    dropped -= _readd_bark_parents(by_type, unattached)

    emitted, orphaned, anchored = emit_nested_overrides(
        pending, writer, ctx.master_index, ctx.anchored_wrld)
    msg = (f"  {label} overrides: {emitted} emitted in the master's "
           f"group nesting, {dropped} unchanged")
    if anchored:
        msg += (f", {anchored} unchanged parent record(s) pulled from the "
                f"master to anchor their children group")
    if new_done:
        msg += f", {new_done} NEW records nested under master parents"
    if unattached:
        msg += (f", {len(unattached)} NEW records in this plugin's OWN "
                f"hierarchy (built by the normal builders)")
    if orphaned:
        msg += f", {orphaned} SKIPPED (no master nesting)"
    print(msg)
    return unattached


# New (non-override) records inside GRUP trees: which export key names the
# parent, and how the child group chain under the parent is built.
#
# LAND belongs here for the same reason REFR does. A plugin can lay BRAND-NEW
# terrain into a cell a MASTER owns: the cell already exists, so the plugin
# ships no CELL of its own, but its LAND is a new record with an id in the
# plugin's own space. Without an entry here `_attach_new_records` handed those
# LANDs back as `unattached`, and the own-hierarchy builders then dropped every
# one of them — `_build_world_groups` only builds worldspaces this plugin
# defines, and their worldspace is the master's.
#
# Measured on TWMP_ValenwoodImproved.esp (masters Oblivion.esm + Tamriel.esp):
# 1,754 of its 2,626 LAND records name Tamriel worldspace 0000003C with a
# parent CELL owned by Tamriel.esp, and all 1,754 were silently discarded —
# the output shipped 1,819 LANDs instead of 2,626. In-game that is terrain
# that never draws while the cell's placed references still render, which is
# exactly the "LOD objects but no land" symptom.
_NEW_NESTED_PARENT = {
    'REFR': 'ParentCELL',
    'ACHR': 'ParentCELL',
    'ACRE': 'ParentCELL',
    'LAND': 'ParentCELL',
    'PGRD': 'ParentCELL',
    'INFO': 'ParentDIAL',
}


def _is_bark_parent(rec: dict, ctx: OverrideContext) -> bool:
    """True when this new INFO's parent DIAL is a MASTER-owned BARK topic.

    GREETING/HELLO/Attack/Idle and friends are engine-named topics shared by
    every plugin, so a dependent plugin's own bark lines all point at the
    master's copy. They must go through the normal bark builder (which splits
    them into per-quest topics and gates them) rather than being nested under
    the master's topic — see the comment at the call site.

    Read from the MASTER export, since the parent record is the master's.
    """
    from ..dialogue.converter import classify_topic
    from ..base.text_reader import get_int
    parent_src = (rec.get('ParentDIAL') or '').upper()
    if not parent_src:
        return False
    parent = (ctx.master_export or {}).get(parent_src)
    if parent is None:
        return False
    _cat, _sub, _snam, is_bark = classify_topic(
        parent.get('EditorID') or '', get_int(parent, 'DATA.Type'))
    return is_bark


def _restamp_formid(record_bytes: bytes, new_fid: int) -> bytes:
    """Rewrite a packed record's own FormID, leaving everything else alone."""
    out = bytearray(record_bytes)
    struct.pack_into('<I', out, 12, new_fid)
    return bytes(out)


def _convert_land(rec: dict, ctx: OverrideContext) -> bytes:
    """Converted bytes for a NEW LAND, reusing the parallel precompute.

    Mirrors import_main._land_bytes: the cache is keyed by the record's own
    (remapped) FormID and a failed precompute re-raises so the caller's
    per-record handler reports it exactly as a serial conversion would.
    """
    from ..record_types.world import convert_LAND
    from ..base.text_reader import get_formid

    cache = getattr(ctx, 'land_cache', None)
    if cache is not None:
        entry = cache.pop(get_formid(rec, 'FormID'), None)
        if entry is not None:
            ok, payload = entry
            if not ok:
                raise RuntimeError(payload)
            return payload
    return convert_LAND(rec)


def _navm_of(rec: dict, ctx: OverrideContext) -> tuple:
    """(navm_bytes, meta) for a PGRD, from the parallel navmesh precompute.

    Keyed by (ParentCELL, PGRD) in the plugin's own FormID space — exactly the
    key `_gather_navm_jobs` built and the group builders look up, so a pathgrid
    nested under a master's cell reuses the same generated geometry instead of
    re-running the (scipy-bound, ~seconds-per-cell) generator.

    Returns (b'', {}) when the precompute produced nothing for this cell, which
    is normal: convert_PGRD declines a pathgrid too sparse to form a ribbon.
    """
    from ..base.text_reader import get_formid

    cache = getattr(ctx, 'navm_cache', None)
    if not cache:
        return b'', {}
    key = (get_formid(rec, 'ParentCELL'), get_formid(rec, 'FormID'))
    navm_bytes, meta = cache.get(key, (None, None))
    return (navm_bytes or b''), (meta or {})


def _attach_navmesh(rec: dict, ctx: OverrideContext, parent_out: int,
                    parent_path: tuple, pending: list) -> tuple:
    """(navm_bytes, group_chain) for a PGRD nested under a MASTER's cell.

    Returns (b'', ()) when the precompute declined the pathgrid.  Appends the
    meta to `ctx.navm_metas`, which is what registers the navmesh in NAVI, and
    any split extras to `pending`.  The bytes are NOT restamped: the precompute
    already built them under the master's NAVM id where there is one.

    See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
    """
    record_bytes, meta = _navm_of(rec, ctx)
    if not record_bytes:
        return b'', ()

    metas = getattr(ctx, 'navm_metas', None)
    if metas is not None:
        metas.append(meta)
        for _xb, _xm in meta.get('extra_navms', ()):
            metas.append(_xm)

    label = struct.pack('<I', parent_out)
    chain = ((6, label), (9, label))
    for _xb, _xm in meta.get('extra_navms', ()):
        pending.append((struct.unpack_from('<I', _xb, 12)[0], _xb,
                        parent_path + chain))
    return record_bytes, chain


def _attach_new_records(new_records: list, ctx: OverrideContext,
                        pending: list) -> tuple:
    """Convert NEW records that live inside a MASTER's GRUP tree.

    A plugin can add its own references to a master's cell (Translation.esp
    injects a map-marker REFR) or its own INFO to a master's topic. They are
    new records — converted normally — but they must sit under the master
    parent's children group or the engine never indexes them. Anchoring that
    group (pulling the unchanged parent's bytes in VERBATIM when this plugin
    does not override it) is handled generically by emit_nested_overrides for
    every type-1/6/7 group, so this function only has to place the record at
    the right path.

    Returns (attached_count, unattached) where `unattached` is every record
    whose parent is NOT the master's — those belong to this plugin's own
    hierarchy and must be built by the normal group builders instead.
    """
    from ..record_types.world import convert_ACHR, convert_REFR
    from ..base.text_reader import get_formid, get_int

    done = 0
    unattached = []
    for sig, rec in new_records:
        parent_key = _NEW_NESTED_PARENT.get(sig)
        parent_src = (rec.get(parent_key) or '') if parent_key else ''
        # A NEW INFO under a master BARK topic must NOT be nested here.
        # GREETING/HELLO are shared, engine-named topics every plugin fills, so
        # every one of this plugin's own greetings resolves to the master's
        # single GREETING DIAL — and nesting them there:
        #   * bypasses the dialogue pipeline entirely (the bare convert_INFO
        #     below emits no GetIsVoiceType, no quest gate, no unlock gate:
        #     0 of Morroblivion's 2,727 greetings had a voice gate, against
        #     15,574 everywhere else), and
        #   * defeats the one-bark-topic-per-quest split. Skyrim honours ONE
        #     HELO topic per owning quest, and the master's GREETING is owned
        #     by the MASTER's quest, so 2,727 greetings spanning 384 different
        #     quests all landed under a topic gated on Oblivion's Charactergen
        #     — dead for the whole game. Vanilla splits GREETING into 271
        #     per-quest topics for exactly this reason.
        # Sending them back as `unattached` makes the normal builder emit this
        # plugin's OWN per-quest bark topics, fully gated.
        if sig == 'INFO' and _is_bark_parent(rec, ctx):
            unattached.append((sig, rec))
            continue
        parent_out = (master_output_formid(parent_src.upper(),
                                           ctx.master_manifest)
                      if parent_key else 0)
        parent_path = (ctx.master_index.group_path(parent_out)
                       if parent_out else ())
        if not parent_key or not parent_path:
            # NOT an error: the record's parent is this plugin's OWN, not the
            # master's (Morroblivion declares Oblivion.esm as a master but its
            # entire world — 387,813 CELL/REFR/LAND/PGRD records — is new).
            # These belong to the normal group builders; dropping them here
            # deleted every cell and reference in the game world.
            unattached.append((sig, rec))
            continue

        try:
            if sig == 'INFO':
                from ..dialogue.converter import convert_INFO
                record_bytes = convert_INFO(rec)
                chain = ((7, struct.pack('<I', parent_out)),)
            elif sig == 'LAND':
                # Terrain is never persistent: it goes in the type-9 temporary
                # children group, and FIRST within it (emit_nested_overrides
                # re-sorts every type-9 group so the LAND leads — vanilla
                # Skyrim.esm does this in 15,564 of 15,564 such groups, and
                # behind the references the engine does not draw it at all).
                #
                # A CELL OWNS AT MOST ONE LAND. When the master's cell already
                # has terrain, this record REPLACES it and must carry that
                # LAND's FormID — shipping our own new id instead puts two
                # LAND records in one cell, which the engine cannot resolve
                # while it parses the file (main-menu hang, no crash, no log).
                # Measured on TWMP_ValenwoodImproved: 1,754 of its cells are
                # Tamriel.esp cells that already have a LAND, and every one
                # got a duplicate.
                record_bytes = _convert_land(rec, ctx)
                label = struct.pack('<I', parent_out)
                chain = ((6, label), (9, label))
                master_land = 0
                _land_of = getattr(ctx.master_index, 'land', None)
                if callable(_land_of):
                    master_land = _land_of(parent_out) or 0
                if master_land:
                    record_bytes = _restamp_formid(record_bytes, master_land)
            elif sig == 'PGRD':
                record_bytes, chain = _attach_navmesh(rec, ctx, parent_out,
                                                      parent_path, pending)
                if not record_bytes:
                    continue
            else:
                conv = convert_ACHR if sig in ('ACHR', 'ACRE') else convert_REFR
                record_bytes = conv(rec)
                # Persistent refs (flag 0x400) sit in the type-8 children
                # group, temporary ones in type 9 — mirroring the master's
                # own builders.
                gtype = 8 if get_int(rec, 'RecordFlags') & 0x400 else 9
                label = struct.pack('<I', parent_out)
                chain = ((6, label), (gtype, label))
        except Exception as e:
            print(f"    SKIPPED new {sig} {rec.get('FormID', '?')}: "
                  f"conversion failed: {e}")
            continue

        # No anchor is added here: emit_nested_overrides pulls the owner of any
        # type-1/6/7 group from the master when this plugin does not override
        # it, so the parent CELL/DIAL is anchored by the same generic path that
        # anchors an unchanged WRLD above a worldspace's cells.
        # The id the record actually SHIPS with — a LAND replacing the
        # master's terrain was restamped above and no longer carries the
        # plugin's own id.
        new_fid = (struct.unpack_from('<I', record_bytes, 12)[0]
                   if len(record_bytes) >= 16 else get_formid(rec, 'FormID'))
        pending.append((new_fid, record_bytes, parent_path + chain))
        done += 1
    return done, unattached
