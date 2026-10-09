"""Generated records a child plugin shares with its masters.

A generator that builds one record purely from its source (an MGEF clone, a
script's message box, a creature race) names it with a deterministic EditorID.
When a master already defines a record of that signature and EditorID, the
child's copy IS that record: it takes the master's FormID and ships as the
master's record carrying the child's text, or not at all when the text agrees.

See: docs/commentary/tes5_import_override.md#generated-records-reuse-the-masters
"""

import struct
from collections import Counter

from ..base.tes5_reader import decompress, subrecords
from ..base.writer import RECORD_HEADER_SIZE, pack_record, pack_subrecord

#: String subrecords that carry a generated record's text: name, description, buttons.
_TEXT_SIGS = (b'FULL', b'DESC', b'ITXT')


def _formid(record: bytes) -> int:
    """The FormID in a packed record's header."""
    return struct.unpack_from('<I', record, 12)[0]


def _with_text(master: bytes, ours: bytes) -> bytes:
    """`master` carrying `ours`' text runs, uncompressed; b'' when the text already agrees.

    A run is taken only when both records hold the same number of it, so the
    master's structure never changes.
    """
    flags = struct.unpack_from('<I', master, 8)[0]
    m_subs = subrecords(decompress(master[RECORD_HEADER_SIZE:], flags))
    o_subs = subrecords(ours[RECORD_HEADER_SIZE:])
    runs = {sig: [p for s, p in o_subs if s == sig] for sig in _TEXT_SIGS}
    counts = Counter(s for s, _p in m_subs)
    seen = Counter()
    body, changed = b'', False
    for sig, payload in m_subs:
        run = runs.get(sig)
        if run and len(run) == counts[sig]:
            changed |= run[seen[sig]] != payload
            payload = run[seen[sig]]
            seen[sig] += 1
        body += pack_subrecord(sig.decode('ascii'), payload)
    if not changed:
        return b''
    return pack_record(master[:4].decode('ascii'), _formid(master), flags, body)


class MasterAdoption:
    """The master records a child's generators adopted, and the renamed copies queued."""

    def __init__(self, master_index):
        """Adopt from `master_index`; nothing adopted or queued yet."""
        self.master_index = master_index
        self.adopted = set()
        self.copies = {}

    def find(self, sig: str, edid: str) -> int:
        """The master's FormID for record `sig`/`edid`, registered as adopted; 0 if none."""
        fid = self.master_index.find_by_edid(sig.encode('ascii'), edid) if edid else 0
        if fid:
            self.adopted.add(fid)
        return fid

    def queue_copy(self, record: bytes) -> None:
        """Queue a renamed master copy, written unless a record at its FormID already is."""
        self.copies[_formid(record)] = record

    def finalize(self, writer) -> tuple:
        """Swap each adopted record for the master's with our text, then add the queued copies.

        An adopted record whose text agrees with the master's is dropped.
        Returns (kept, added).
        """
        ours = [r for r in writer.top_records() if _formid(r) in self.adopted]
        writer.remove_records(lambda r: _formid(r) in self.adopted)
        kept = 0
        for record in ours:
            fid = _formid(record)
            master = self.master_index.record(fid)
            if not master:
                # The adopted id names no record in the loaded masters
                # (truncated master output, an unroutable slot, or an EDID
                # that resolved to another file's override copy). Crashing
                # here killed the whole import (Tamriel_Data.esm:
                # struct.error on a 0-byte buffer); the record we built is
                # kept as-is so the plugin still defines the id, loudly.
                print(f"  WARNING: adopted {record[:4].decode('ascii', 'replace')} "
                      f"{fid:08X} has no master record; keeping ours")
                writer.add_record(record[:4].decode('ascii', 'replace'), record)
                kept += 1
                continue
            merged = _with_text(master, record)
            if merged:
                writer.add_record(merged[:4].decode('ascii'), merged)
                kept += 1
        present = {_formid(rec) for rec in writer.top_records()}
        added = 0
        for fid, record in sorted(self.copies.items()):
            if fid not in present:
                writer.add_record(record[:4].decode('ascii', 'replace'), record)
                added += 1
        return kept, added


def adopted_formid(writer, sig: str, edid: str) -> int:
    """The master's FormID for generated record `sig`/`edid`, else 0."""
    adoption = getattr(writer, 'adoption', None)
    return adoption.find(sig, edid) if adoption is not None else 0


def generated_formid(writer, sig: str, edid: str, site: str, key) -> int:
    """The master's FormID for `sig`/`edid`, else `derive_formid(site, key)`."""
    return adopted_formid(writer, sig, edid) or writer.derive_formid(site, key)
