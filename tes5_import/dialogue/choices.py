"""Resolve INFO choices against the records actually emitted by conversion.

TES4 conversation topics can be suppressed or split into generated TES5 topics.
Their old FormIDs must not survive in TCLT. Skyrim removes unresolved choices
at startup, but its diagnostic can overflow a 260-byte sprintf_s buffer when
the response text is long. See docs/reference/dialogue_engine_contracts.md.
"""

import struct
import zlib

from ..base.tes5_reader import FLAG_COMPRESSED, FLAG_DELETED, records, walk
from ..base.writer import pack_subrecord


def prune_invalid_choices(writer, master_index=None, hidden_targets=()) -> int:
    """Remove choices whose targets are absent/deleted or are not DIALs.

    Run after all dialogue and adopted overrides have been emitted: forward
    references and inherited topics are valid. A master absent from the index
    cannot prove a dangling reference, so its unresolved choices are retained.
    Local overrides take precedence over the master's version.
    """
    blobs = writer._top_groups.get('DIAL', [])
    targets = {
        struct.unpack('<I', value)[0]
        for blob in blobs for rec in records(blob, b'INFO', span=(0, len(blob)))
        for tag, value in rec.subs() if tag == b'TCLT' and len(value) == 4
    }
    if not targets:
        return 0

    local = {}
    for group in writer._top_groups.values():
        for blob in group:
            for rec in records(blob, bodies=(), span=(0, len(blob))):
                if rec.form_id in targets:
                    local[rec.form_id] = b'' if rec.deleted else rec.sig

    def valid(fid):
        if not fid:
            return False
        if fid in local:
            return local[fid] == b'DIAL'
        if fid >> 24 == writer.own_index:
            return False
        if master_index is None:
            return True
        sig = master_index.signature(fid)
        if not sig:
            return not master_index.covers_slot(fid >> 24)
        if sig != b'DIAL':
            return False
        raw = master_index.record(fid)
        return not (struct.unpack_from('<I', raw, 8)[0] & FLAG_DELETED)

    # NPC continuations are replayed from the preserved source graph. They
    # must not become player-selectable choices merely because their DIALs
    # now survive conversion.
    invalid = {fid for fid in targets if not valid(fid)} | (targets & set(hidden_targets))
    if not invalid:
        return 0

    removed = 0
    for i, blob in enumerate(blobs):
        edits = []
        group_deltas = {}
        for rec, stack in walk(blob, b'INFO', span=(0, len(blob))):
            subs = rec.subs()
            kept = [(tag, value) for tag, value in subs
                    if not (tag == b'TCLT' and len(value) == 4
                            and struct.unpack('<I', value)[0] in invalid)]
            count = len(subs) - len(kept)
            if not count:
                continue
            removed += count
            body = b''.join(pack_subrecord(tag.decode('ascii'), value)
                            for tag, value in kept)
            if rec.flags & FLAG_COMPRESSED:
                body = struct.pack('<I', len(body)) + zlib.compress(body)
            header = bytearray(blob[rec.offset:rec.offset + 24])
            struct.pack_into('<I', header, 4, len(body))
            replacement = bytes(header) + body
            edits.append((rec.offset, rec.end, replacement))
            delta = len(replacement) - (rec.end - rec.offset)
            for group in stack.groups:
                size, total = group_deltas.get(
                    group.start, (group.end - group.start, 0))
                group_deltas[group.start] = (size, total + delta)
        for start, (size, delta) in group_deltas.items():
            edits.append((start + 4, start + 8, struct.pack('<I', size + delta)))
        if edits:
            parts, pos = [], 0
            for start, end, replacement in sorted(edits):
                parts.extend((blob[pos:start], replacement))
                pos = end
            parts.append(blob[pos:])
            blobs[i] = b''.join(parts)
    return removed
