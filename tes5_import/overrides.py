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

from .export_diff import diff_records
from .master_manifest import load_master_manifests
from .override_builder import RECONVERT_KEYS, apply_changes
from .override_merge import load_master_index
from .text_reader import parse_export_directory, remap_formid
from .writer import PluginWriter, pack_group

# Types whose override CANNOT be expressed against the master's output because
# conversion does not produce a corresponding record to substitute into
# (PGRD/ROAD become generated NAVM/nothing). These are counted and reported,
# never silently dropped.
OVERRIDE_UNMAPPABLE_TYPES = frozenset({'PGRD', 'ROAD'})

Override = namedtuple('Override', ['status', 'out_fid', 'record_bytes'])
# status: 'emitted' | 'unchanged' | 'no-base' | 'no-path' | 'reconvert'


def load_master_export(export_dir: str) -> dict:
    """The masters' export records, keyed by raw TES4 FormID.

    This is the baseline for deciding what a plugin's author actually changed.
    Diffing against the master's EXPORT (rather than against a second
    conversion) is what makes the override path deterministic: a field neither
    export touches is never rewritten, so it cannot drift.
    """
    header = os.path.join(export_dir, '_HEADER.txt')
    if not os.path.isfile(header):
        return {}
    names = []
    with open(header, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('Master['):
                _, _, val = line.partition('=')
                names.append(val.strip())

    root = os.path.dirname(os.path.normpath(export_dir))
    out = {}
    for name in names:
        mdir = os.path.join(root, name)
        if not os.path.isdir(mdir):
            print(f"  WARNING: master export not found ({mdir}); "
                  f"overrides cannot be diffed against it")
            continue
        for rec in parse_export_directory(mdir):
            fid = rec.get('FormID')
            if fid:
                out[fid.upper()] = rec
    return out


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
        return remap_formid(int(src_fid, 16))
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
        injected[fid_raw] = writer.alloc_formid()
    return injected


class OverrideContext:
    """Everything a plugin's import needs to emit overrides of its masters."""

    def __init__(self, export_dir: str, masters: list, num_tes4_masters: int,
                 output_root: str):
        self.master_index = load_master_index(
            masters, num_tes4_masters, output_root)
        self.master_manifest = load_master_manifests(
            masters, num_tes4_masters, output_root)
        self.master_export = load_master_export(export_dir)
        self.stats = Counter()
        self.unmapped_keys = Counter()

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
        self.stats['emitted'] += 1
        return Override('emitted', out_fid, record_bytes)

    def report(self):
        print(f"  Overrides: {self.stats['emitted']} emitted, "
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


def emit_nested_overrides(records: list, writer: PluginWriter) -> tuple:
    """Write override records back into the master's exact GRUP nesting.

    A CELL is only reachable by the engine from inside its block/sub-block
    hierarchy (interior: CELL -> type 2 -> type 3; exterior: WRLD -> type 1 ->
    type 4 -> type 5). Writing one flat under the top-level group leaves it
    unindexed, which is what made every renamed cell black and empty in-game.
    A REFR likewise lives under its parent cell's type-6 children group.

    The nesting is COPIED from the master (MasterIndex.group_path) rather than
    recomputed, so there is no block-number formula to get wrong, and a record
    the master nests unusually still lands where the engine expects it.

    `records` is [(output_formid, record_bytes, path), ...] where `path` is
    the ((grup_type, label_bytes), ...) nesting the record belongs in.
    Returns (emitted_count, orphan_count).
    """
    # Bucket the records by the exact GRUP path they belong in.
    by_path = {}
    orphans = 0
    for _out_fid, record_bytes, path in records:
        if not path:
            # No nesting known: emitting it flat would leave it unindexed, so
            # skip rather than ship an invisible record.
            orphans += 1
            continue
        by_path.setdefault(path, []).append(record_bytes)

    def build(prefix: tuple, depth: int) -> bytes:
        """Serialize everything at `prefix`, recursing into deeper paths.

        A record's own children group must directly FOLLOW that record — the
        engine reads `CELL, GRUP(6, cell), CELL, GRUP(6, cell), ...`, so
        emitting the records first and the groups afterwards pairs each group
        with the wrong cell.
        """
        deeper = {p[:depth + 1] for p in by_path
                  if len(p) > depth and p[:depth] == prefix}
        # A type-6 group belongs to the record whose FormID labels it.
        owned = {struct.unpack('<I', child[depth][1])[0]: child
                 for child in deeper
                 if child[depth][0] == 6 and len(child[depth][1]) == 4}
        body = b''
        for record_bytes in by_path.get(prefix, ()):
            body += record_bytes
            fid = struct.unpack_from('<I', record_bytes, 12)[0]
            child = owned.pop(fid, None)
            if child is not None:
                inner = build(child, depth + 1)
                if inner:
                    body += pack_group(6, child[depth][1], inner)

        # Everything else: blocks/sub-blocks, plus any type-6 whose owning
        # record this plugin does not override (its parent cell is unchanged,
        # but some of its references are).
        rest = [c for c in deeper
                if c[depth][0] != 6 or c in owned.values()]
        for child in sorted(rest,
                            key=lambda p: (p[depth][0], bytes(p[depth][1]))):
            inner = build(child, depth + 1)
            if inner:
                body += pack_group(child[depth][0], child[depth][1], inner)
        return body

    # Depth 0 is the top-level group itself; writer.add_raw_group wraps it.
    for top in sorted({p[0] for p in by_path},
                      key=lambda t: (t[0], bytes(t[1]))):
        body = build((top,), 1)
        if body:
            writer.add_raw_group(top[1].decode('ascii', 'replace'), body)

    return len(records) - orphans, orphans


def build_nested_overrides(by_type: dict, sigs: tuple, ctx: OverrideContext,
                           writer: PluginWriter, label: str) -> None:
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
    """
    pending = []
    emitted_fids = set()
    new_records = []
    dropped = 0
    for sig in sigs:
        for rec in by_type.get(sig, []):
            ov = ctx.build(rec, sig)
            if ov is None:
                new_records.append((sig, rec))
                continue
            if ov.status != 'emitted':
                dropped += 1
                continue
            pending.append((ov.out_fid, ov.record_bytes,
                            ctx.master_index.group_path(ov.out_fid)))
            emitted_fids.add(ov.out_fid)

    new_done, new_skipped = _attach_new_records(new_records, ctx, pending,
                                                emitted_fids)

    emitted, orphaned = emit_nested_overrides(pending, writer)
    msg = (f"  {label} overrides: {emitted} emitted in the master's "
           f"group nesting, {dropped} unchanged")
    if new_done:
        msg += f", {new_done} NEW records nested under master parents"
    if new_skipped:
        msg += f", {new_skipped} NEW records SKIPPED (unresolvable parent)"
    if orphaned:
        msg += f", {orphaned} SKIPPED (no master nesting)"
    print(msg)


# New (non-override) records inside GRUP trees: which export key names the
# parent, and how the child group chain under the parent is built.
_NEW_NESTED_PARENT = {
    'REFR': 'ParentCELL',
    'ACHR': 'ParentCELL',
    'ACRE': 'ParentCELL',
    'INFO': 'ParentDIAL',
}


def _attach_new_records(new_records: list, ctx: OverrideContext,
                        pending: list, emitted_fids: set) -> tuple:
    """Convert NEW records that live inside a MASTER's GRUP tree.

    A plugin can add its own references to a master's cell (Translation.esp
    injects a map-marker REFR) or its own INFO to a master's topic. They are
    new records — converted normally — but they must sit under the master
    parent's children group or the engine never indexes them. When the parent
    record itself is not already overridden, its converted bytes are pulled in
    VERBATIM as an anchor, exactly like xEdit's "copy as override" does when
    you copy a reference: the engine pairs a children GRUP with the record
    that precedes it, so the group cannot stand alone.

    Records whose parent cannot be resolved in the master are counted and
    reported by the caller — never silently dropped.
    """
    from .record_types.world import convert_ACHR, convert_REFR
    from .text_reader import get_formid, get_int

    done = 0
    skipped = 0
    for sig, rec in new_records:
        parent_key = _NEW_NESTED_PARENT.get(sig)
        parent_src = (rec.get(parent_key) or '') if parent_key else ''
        parent_out = master_output_formid(parent_src.upper(),
                                          ctx.master_manifest)
        parent_path = ctx.master_index.group_path(parent_out)
        if not parent_key or not parent_path:
            skipped += 1
            print(f"    SKIPPED new {sig} {rec.get('FormID', '?')}: parent "
                  f"{parent_src or '?'} not found in the master")
            continue

        try:
            if sig == 'INFO':
                from .dialog_converter import convert_INFO
                record_bytes = convert_INFO(rec)
                chain = ((7, struct.pack('<I', parent_out)),)
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
            skipped += 1
            print(f"    SKIPPED new {sig} {rec.get('FormID', '?')}: "
                  f"conversion failed: {e}")
            continue

        if parent_out not in emitted_fids:
            # Anchor: the parent record itself, byte-identical to the master.
            pending.append((parent_out, ctx.master_index.record(parent_out),
                            parent_path))
            emitted_fids.add(parent_out)

        new_fid = get_formid(rec, 'FormID')
        pending.append((new_fid, record_bytes, parent_path + chain))
        done += 1
    return done, skipped
