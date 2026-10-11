"""Cut a converted ESP down to one creature's own records.

The importer writes the converter's shared machinery into every plugin
(attribute and AI factions, combat-approach packages, humanoid voice types,
globals); a one-creature test mod needs none of it.  Kept: every record of
the KEEP types, plus a FOLLOW-type record only when a kept record names it.
A kept record's subrecord that names a dropped record of this plugin is cut.

See: skyb_retarget/README.md#the-package
"""
import struct

from tes5_import.base.tes5_reader import (REC_HDR, header_end, masters,
                                          read_group, read_record)

#: Record types the creature itself is made of (IDLE/MOVT bind by name, not FormID).
KEEP = (b'NPC_', b'RACE', b'ARMO', b'ARMA', b'BPTD', b'MOVT', b'IDLE', b'CSTY')
#: Kept only when a kept record references them.
FOLLOW = (b'VTYP',)
#: Subrecords that carry a count elsewhere; cutting one would need that count fixed.
COUNTED = (b'SPLO', b'PRKR', b'KWDA', b'CNTO', b'LLCT')

_FLAG_COMPRESSED = 0x00040000


def _top_records(raw) -> list:
    """[(group header bytes, [Record])] for every top-level GRUP."""
    out, pos = [], header_end(raw)
    while pos < len(raw):
        g = read_group(raw, pos)
        recs, p = [], g.start + 24
        while p < g.end:
            rec, p = read_record(raw, p, g.end)
            recs.append(rec)
        out.append((bytes(raw[g.start:g.start + 24]), recs))
        pos = g.end
    return out


def _refs(rec, own: int) -> set:
    """Own-plugin FormIDs at 4-byte boundaries of `rec`'s subrecords."""
    found = set()
    for _tag, data in rec.subs():
        for o in range(0, len(data) - 3, 4):
            v = struct.unpack_from('<I', data, o)[0]
            if v >> 24 == own:
                found.add(v)
    return found


def _kept_ids(groups, own: int) -> set:
    """FormIDs of the KEEP records and the FOLLOW records they name."""
    keep = {r.form_id for _h, recs in groups for r in recs if r.sig in KEEP}
    named = set().union(*(_refs(r, own) for _h, recs in groups for r in recs
                          if r.form_id in keep))
    return keep | {r.form_id for _h, recs in groups for r in recs
                   if r.sig in FOLLOW and r.form_id in named}


def _record_bytes(raw, rec, own: int, kept: set) -> bytes:
    """The record re-serialized uncompressed, minus subrecords naming dropped records."""
    subs = []
    for tag, data in rec.subs():
        first = struct.unpack_from('<I', data)[0] if len(data) >= 4 else 0
        if first >> 24 == own and first not in kept:
            if tag in COUNTED:
                raise ValueError(f'{rec.sig} {rec.form_id:08X}: counted {tag} names a dropped record')
            continue
        subs.append(tag + struct.pack('<H', len(data)) + data)
    body = b''.join(subs)
    head = bytearray(raw[rec.offset:rec.offset + REC_HDR])
    struct.pack_into('<II', head, 4, len(body), rec.flags & ~_FLAG_COMPRESSED)
    return bytes(head) + body


def prune(raw: bytes) -> tuple:
    """(pruned plugin bytes, {kept signature: count})."""
    own = len(masters(raw))
    groups = _top_records(raw)
    kept = _kept_ids(groups, own)
    out, counts, total = [], {}, 0
    for head, recs in groups:
        body = b''.join(_record_bytes(raw, r, own, kept) for r in recs
                        if r.form_id in kept)
        if not body:
            continue
        head = bytearray(head)
        struct.pack_into('<I', head, 4, len(body) + 24)
        out.append(bytes(head) + body)
        for r in recs:
            if r.form_id in kept:
                counts[r.sig.decode()] = counts.get(r.sig.decode(), 0) + 1
        total += 1 + sum(1 for r in recs if r.form_id in kept)
    header = bytearray(raw[:header_end(raw)])
    hedr = header.index(b'HEDR')
    struct.pack_into('<i', header, hedr + 6 + 4, total)
    return bytes(header) + b''.join(out), counts
