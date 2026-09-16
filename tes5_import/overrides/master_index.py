"""Random-access index over a converted master plugin.

A plugin that has masters emits each override as the MASTER's converted record
with the author's changes applied (see override_builder). This module provides
the master side of that: the converted record bytes for a given FormID.

There is deliberately no merging heuristic here. An earlier version tried to
reconcile two independent conversion runs by comparing their output bytes and
guessing which differences were authored and which were re-derivation
artifacts. That is not decidable from the output, and guessing wrong produced
an unloadable plugin — 1821 NPC_ races rewritten to vanilla Skyrim ones the
authors never touched. Authorship now comes from diffing the two TES4 exports
(export_diff), which answers the question directly.
"""

import os
import struct
import zlib
from output_layout import paths

from ..base.tes5_reader import REC_HDR as _HEADER_SIZE
from ..base.tes5_reader import first_sub, masters, walk


def _plugin_name(path: str) -> str:
    """The lowercased plugin filename a MasterIndex was loaded from.

    Converted masters live at output/<plugin>/<plugin>, so the basename IS the
    plugin name that other files list in their MAST entries.
    """
    return os.path.basename(path or '').lower()


class MasterIndex:
    """FormID -> converted record body, read from a converted master plugin.

    `masters` is this file's own master list, LOWERCASED, and `own_index` the
    index byte its OWN records carry (= that list's length).  Both are needed
    to translate between this master's id space and a child's, see
    `ChainedMasterIndex`.
    """

    def __init__(self, path: str):
        self.path = path
        self._data = b''
        self._offsets = {}      # formid -> (signature, offset, total_size)
        self._paths = {}        # formid -> ((grup_type, label), ...)
        self._land_by_cell = {}  # cell formid -> LAND formid
        self._navm_by_cell = {}
        self.masters = []
        self.own_index = 0
        self._load()

    def _load(self):
        with open(self.path, 'rb') as f:
            self._data = f.read()
        d = self._data
        if len(d) < 8 or d[:4] != b'TES4':
            raise ValueError(f"Not a plugin file: {self.path}")
        self.masters = masters(d)
        self.own_index = len(self.masters)
        self._scan()

    def _scan(self):
        """Index every record by FormID: signature, byte extent, GRUP path.

        A LAND is additionally keyed by its owning cell, named by the enclosing
        type-6 GRUP label -- its own FormID is not recoverable by arithmetic,
        see `land`.
        """
        for rec, stack in walk(self._data, bodies=()):
            self._offsets[rec.form_id] = (rec.sig, rec.offset,
                                          _HEADER_SIZE + rec.size)
            self._paths[rec.form_id] = stack.path()
            if rec.sig == b'LAND' and stack.cell is not None:
                self._land_by_cell[stack.cell] = rec.form_id
            elif rec.sig == b'NAVM' and stack.cell is not None:
                self._navm_by_cell.setdefault(stack.cell, []).append(
                    rec.form_id)

    def group_path(self, formid: int) -> tuple:
        """The GRUP nesting a record sits in, as ((type, label), ...).

        A CELL is only reachable by the engine from inside its block/sub-block
        hierarchy (interior: CELL -> type 2 -> type 3; exterior: WRLD -> type 1
        -> type 4 -> type 5). A CELL written flat under the top-level group is
        never indexed, which is what left every renamed cell black and empty.
        An override must therefore reproduce the master's exact nesting — read
        here rather than recomputed, so there is no formula to get wrong.
        """
        return self._paths.get(formid, ())

    def land(self, cell_formid: int) -> int:
        """FormID of the LAND inside a converted cell, or 0 if it has none.

        LAND is the one override type whose own FormID cannot be derived from
        its source id. Every other record either has a manifest entry or keeps
        its id with only the load-order index shifted, but the master's
        conversion REALLOCATES land ids (Oblivion.esm: 0 of 3,999 sampled
        source ids resolve to a real output LAND by that arithmetic — the
        0x00034A7D that owns cell 0x000082F3 comes out as 0x0100F8AF).

        The lookup that does hold is structural: a cell owns at most one LAND,
        and the cell's own id DOES resolve. Without this every LAND override
        from a dependent plugin was classified 'no-base' and dropped, and
        because a plugin's child GRUP REPLACES the master's rather than
        merging with it, the emitted cell then shadowed the master's terrain
        with a children group that had no LAND in it — land blank and missing
        in-game while the cell's placed references still rendered.
        """
        return self._land_by_cell.get(cell_formid, 0)

    def navms(self, cell_formid: int) -> list:
        """Every NAVM FormID in a cell, file order.

        See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
        """
        return self._navm_by_cell.get(cell_formid, [])

    def __contains__(self, formid: int) -> bool:
        return formid in self._offsets

    def __len__(self) -> int:
        return len(self._offsets)

    def formids(self) -> set:
        return set(self._offsets)

    def signature(self, formid: int) -> bytes:
        entry = self._offsets.get(formid)
        return entry[0] if entry else b''

    def record(self, formid: int) -> bytes:
        """Full record bytes (header + body) for a FormID, or b'' if absent."""
        entry = self._offsets.get(formid)
        if not entry:
            return b''
        _, off, size = entry
        return self._data[off:off + size]

    def find_by_edid(self, signature: bytes, edid: str) -> int:
        """FormID of the master's record with this signature + EditorID (0 if none).

        For SYNTHETIC records the master minted with no TES4 source, so the
        companion manifest (keyed by source FormID) cannot name them — the
        generic dialogue quest is the case that matters. EDID is always the
        first subrecord of the records this is used for, so this stops at the
        first one rather than parsing the whole body.
        """
        want = edid.encode('ascii', 'replace')
        for fid, (sig, off, size) in self._offsets.items():
            if sig != signature:
                continue
            # Compressed bodies start with a u32 decompressed size, not EDID.
            if struct.unpack_from('<I', self._data, off + 8)[0] & 0x00040000:
                continue
            body = self._data[off + _HEADER_SIZE:off + size]
            if body[:4] != b'EDID':
                continue
            got = first_sub(body, b'EDID')
            if got is not None and got.rstrip(b'\0') == want:
                return fid
        return 0


# GRUP types whose 4-byte label is a FormID (the owning record), not a
# block/sub-block coordinate pair. xEdit wbImplementation: 1=World Children,
# 6=Cell Children, 7=Topic Children, 8/9/10=the cell's persistent/temporary/
# visible-when-distant child groups (all labelled with the parent CELL).
_FORMID_LABEL_GROUPS = frozenset({1, 6, 7, 8, 9, 10})

# Subrecords in the cell tree whose payload is one or more FormIDs, with the
# byte offsets that hold them. Verified against the xEdit TES5 definitions and
# a census of the converted output; only these are rewritten, so a field like
# LAND's VHGT/VNML (raw terrain bytes that can look like anything) is never
# touched. `None` means "the whole payload is a run of u32 FormIDs".
_FORMID_FIELDS = {
    # Whole payload is one FormID (or a run of them).
    b'NAME': None,    # base object / linked record
    b'XLCN': None,    # persistent location
    b'XOWN': None,    # owner (our writer emits a bare FormID)
    b'XCLR': None,    # wbArrayS(XCLR, 'Regions', ...) — a RUN of FormIDs
    b'LTMP': None,    # lighting template
    b'XLTW': None,    # lit water
    b'XEZN': None,    # encounter zone
    b'XCWT': None,    # cell water
    b'XCIM': None,    # image space
    b'XCAS': None,    # acoustic space
    b'XCMO': None,    # music type
    b'XLIB': None,    # leveled item base
    b'XATR': None,    # attach ref
    b'XEMI': None,    # emittance
    b'XMBR': None,    # multibound ref
    b'XLRT': None,    # location ref type
    b'XLRL': None,    # location reference
    # Structs: only these byte offsets hold a FormID.
    b'XESP': (0,),    # parent ref + 4 flag bytes
    b'XNDP': (0,),    # navmesh ref + u16 index + pad
    b'XTEL': (0,),    # door ref + 6 floats + flags
    b'XLOC': (4,),    # u32 level, key FormID, flags
    b'XPWR': (0,),    # wbStructSK(XPWR, [0], 'Water', ...)
    b'XLKR': (0,),    # linked-ref keyword + ref
    b'BTXT': (0,),    # LTEX + quadrant/layer
    b'ATXT': (0,),    # LTEX + quadrant/layer
    b'TNAM': None,    # LTEX -> TXST (wbDefinitionsTES5 wbRecord(LTEX))
    b'GNAM': None,    # LTEX -> GRAS
    # DELIBERATELY ABSENT — verified against wbDefinitionsTES5.pas, these are
    # NOT FormIDs and rewriting them corrupts real data:
    #   XLCM  wbInteger  (level modifier)
    #   XPRD  wbFloat    (patrol idle time)
    #   XPPA  wbEmpty    (patrol script marker)
    #   XRGD / XRGB      (ragdoll/biped data blobs)
}


def _shift_formid(fid: int, index_map: dict) -> int:
    """Restate a FormID's index byte via `index_map`, leaving 0 (null) alone.

    An index the map does not mention is left ALONE. That is the common case
    and the important one: a master and its child usually share their own low
    masters (Skyrim.esm at 0, Oblivion.esm at 1), so a reference to one of
    those is already correct and moving it would silently retarget it — a
    blanket +1 turned every Oblivion.esm reference into a Tamriel.esp one.
    """
    if not fid:
        return fid
    mapped = index_map.get((fid >> 24) & 0xFF)
    if mapped is None:
        return fid
    return (mapped << 24) | (fid & 0x00FFFFFF)


def _shift_vmad(payload: bytes, index_map: dict) -> bytes:
    """VMAD with every OBJECT-property FormID restated for the child.

    VMAD cannot go in `_FORMID_FIELDS`: its FormIDs sit at positions that
    depend on the preceding variable-length script and property NAMES, so no
    fixed offset table can reach them. It has to be walked.

    Skipping it shipped the master's own ids verbatim inside an override whose
    header WAS restated. Measured 2026-08-12 on ElsweyrPelletine.esp: the
    override of ElsweyrAnequina's QUST 02061ABC (ANQMerchantsGuild04) was
    written at 03061ABC, correctly, while its VMAD still bound
    `TES4Unlock_ANQMCG04Bookkeeper` to 020E0E24 — index 02 is Tamriel.esp in
    the child, so the GLOB property resolved into the wrong FILE. 23 such
    properties across 7 QF_ scripts.

    Parsed defensively: a payload this does not fully understand is returned
    UNCHANGED rather than half-rewritten, since a partial rewrite would corrupt
    a script binding rather than merely mis-target one property.
    """
    if len(payload) < 7:
        return payload
    try:
        ver, fmt, nscripts = struct.unpack_from('<hhH', payload, 0)
        if ver != 5 or fmt != 2:
            return payload
        buf = bytearray(payload)
        p = 6

        def _props(pos, count):
            for _ in range(count):
                ln = struct.unpack_from('<H', buf, pos)[0]
                pos += 2 + ln
                ptype = buf[pos]
                pos += 2                       # type + status
                if ptype == 1:                 # object: u32 unused + FormID
                    v = struct.unpack_from('<I', buf, pos + 4)[0]
                    struct.pack_into('<I', buf, pos + 4,
                                     _shift_formid(v, index_map))
                    pos += 8
                elif ptype in (2, 3, 4, 5):    # string/int/float/bool
                    if ptype == 2:
                        sl = struct.unpack_from('<H', buf, pos)[0]
                        pos += 2 + sl
                    else:
                        pos += 4
                elif ptype == 11:              # array of objects
                    n = struct.unpack_from('<I', buf, pos)[0]
                    pos += 4
                    for _ in range(n):
                        v = struct.unpack_from('<I', buf, pos + 4)[0]
                        struct.pack_into('<I', buf, pos + 4,
                                         _shift_formid(v, index_map))
                        pos += 8
                else:
                    raise ValueError('unhandled VMAD property type %d' % ptype)
            return pos

        for _ in range(nscripts):
            ln = struct.unpack_from('<H', buf, p)[0]
            p += 2 + ln + 1                    # name + flags byte
            n = struct.unpack_from('<H', buf, p)[0]
            p += 2
            p = _props(p, n)
        return bytes(buf)
    except (struct.error, ValueError, IndexError):
        return payload


def _shift_record_formids(rec: bytes, index_map: dict) -> bytes:
    """A converted record restated in the child's load order.

    `index_map` is {master's index byte -> child's index byte}, holding ONLY
    the bytes that actually move. Rewrites the record's own FormID in the
    header plus every reference in a known FormID-bearing subrecord.
    Compressed bodies are decompressed, rewritten and left DECOMPRESSED with
    the flag cleared — the engine accepts either form, and the override builder
    needs to read the subrecords anyway.
    """
    if len(rec) < _HEADER_SIZE:
        return rec
    size = struct.unpack_from('<I', rec, 4)[0]
    flags = struct.unpack_from('<I', rec, 8)[0]
    fid = struct.unpack_from('<I', rec, 12)[0]
    body = rec[_HEADER_SIZE:_HEADER_SIZE + size]
    if flags & 0x00040000:
        try:
            body = zlib.decompress(body[4:])
        except zlib.error:
            return rec
        flags &= ~0x00040000

    out = bytearray()
    j = 0
    while j + 6 <= len(body):
        ssig = body[j:j + 4]
        ssize = struct.unpack_from('<H', body, j + 4)[0]
        payload = bytearray(body[j + 6:j + 6 + ssize])
        if ssig == b'VMAD':
            payload = bytearray(_shift_vmad(bytes(payload), index_map))
        elif ssig in _FORMID_FIELDS:
            spots = _FORMID_FIELDS[ssig]
            offsets = (range(0, len(payload) - 3, 4) if spots is None
                       else spots)
            for off in offsets:
                if off + 4 <= len(payload):
                    v = struct.unpack_from('<I', payload, off)[0]
                    struct.pack_into('<I', payload, off,
                                     _shift_formid(v, index_map))
        out += ssig + struct.pack('<H', ssize) + bytes(payload)
        j += 6 + ssize
    out += body[j:]        # any trailing bytes, untouched

    head = bytearray(rec[:_HEADER_SIZE])
    struct.pack_into('<I', head, 4, len(out))
    struct.pack_into('<I', head, 8, flags)
    struct.pack_into('<I', head, 12, _shift_formid(fid, index_map))
    return bytes(head) + bytes(out)


class ChainedMasterIndex:
    """Several converted masters, addressed in the CHILD plugin's FormID space.

    Every FormID reaching this class is in the child's space, where the index
    byte names one specific master: the child's TES5 master list puts the
    master converted from `indices[k]` at slot `base_slot + k`, so an id whose
    high byte is that slot belongs to that master and to no other.

    Routing by index byte rather than by "first file that happens to contain
    the integer" is the whole point. Each converted master renumbers into its
    OWN space, so two masters' id ranges overlap almost completely, and a
    first-match scan silently answers from the wrong file. TWMP Valenwood/
    Elsweyr hit this exactly: 0202E438 is a Tamriel.esp exterior CELL and also
    an ElsweyrAnequina.esp WRLD (ANQVerkarthHillsWorld). The reverse-order scan
    returned ANQ's worldspace for the Tamriel cell, so the writer emitted a
    phantom worldspace and 4,992 duplicate FormIDs (4,552 REFR, 237 CELL, 178
    LAND) — the same id twice with conflicting types and group nesting. The
    engine builds its FormID table while parsing the plugin, before any cell
    loads, so the game hung on the main menu with no crash.

    A master's own records carry ITS index (its master count), which is not the
    slot it occupies in the child. Both directions are translated here so
    callers never have to think about whose space an id is in.
    """

    def __init__(self, indices: list, base_slot: int = None,
                 child_masters: list = None):
        self._indices = list(indices)
        # Default: masters occupy the LAST len(indices) slots of the child's
        # master list, i.e. the TES4 masters after any prepended new ones.
        # Callers that know the real layout pass it explicitly.
        self._base_slot = (base_slot if base_slot is not None
                           else len(self._indices))
        self._by_slot = {self._base_slot + k: idx
                         for k, idx in enumerate(self._indices)}
        # {index -> {its index byte -> the child's}} for the record rewriter.
        # Built from the CHILD's full master list so a name shared by both (the
        # usual Skyrim.esm/Oblivion.esm prefix) maps to itself and is left
        # alone; only indices that genuinely move appear here.
        child = [n.lower() for n in (child_masters or [])]
        self._index_maps = {}
        for k, idx in enumerate(self._indices):
            slot = self._base_slot + k
            own_masters = [n.lower() for n in (idx.masters or ())]
            m = {}
            if idx.own_index != slot:
                m[idx.own_index] = slot
            for j, sub in enumerate(own_masters):
                target = child.index(sub) if sub in child else None
                if target is not None and target != j:
                    m[j] = target
            self._index_maps[id(idx)] = m

    def _route(self, formid: int):
        """(index, id in that master's own space) for a child-space FormID.

        The index byte names the master that DEFINES the record, and that is
        what fixes its identity and its group nesting. But a later master may
        OVERRIDE it, and in load order the last file to define a record is the
        one the engine uses -- so that is the version an override of ours must
        be built on top of.

        Tamriel.esp overrides Oblivion.esm's Tamriel worldspace 0100003C to
        WIDEN its NAM0/NAM9 bounds (+/-262144 -> +/-786432) for the 99,946
        exterior cells it adds. Routing by owner alone handed back Oblivion's
        ORIGINAL rectangle as the base, TWMP_ValenwoodImproved's WRLD override
        was spliced onto that, and the output reverted the worldspace to the
        narrow bounds -- leaving 92,745 of Tamriel.esp's cells outside the
        worldspace extent. The engine builds its cell grid from NAM0/NAM9
        while PARSING the file, so the game hung on the main menu forever with
        no crash and no log, and deleting the WRLD record in xEdit made it
        load again.
        """
        owner_slot = (formid >> 24) & 0xFF
        owner = self._by_slot.get(owner_slot)
        if owner is None:
            return None, 0
        own_id = (owner.own_index << 24) | (formid & 0x00FFFFFF)

        # Later masters win, exactly as load order resolves it -- but ONLY a
        # genuine override counts. Two masters routinely use the SAME index
        # byte for their own records (Tamriel.esp and ElsweyrAnequina.esp are
        # both the 2nd file in their own load order), so "this later file
        # happens to contain the same integer" is NOT evidence of an override:
        # 0202E438 is a CELL in Tamriel.esp and the WRLD ANQVerkarthHillsWorld
        # in ElsweyrAnequina.esp. Believing the latter emitted that worldspace
        # TWICE and anchored a Tamriel cell's children group under it.
        #
        # A real override satisfies both tests:
        #   * the overriding file lists the owner among ITS masters, so the id
        #     means the same record in both files, and
        #   * the record carries the owner's signature.
        # Only the record CONTENT comes from the winning override; identity
        # questions -- which slot an answer belongs in -- stay the OWNER's.
        # `record()` and `group_path()` restate ids through `_index_maps`, and
        # `land()` guards its answer on `idx.own_index` for exactly this.
        owner_name = _plugin_name(owner.path)
        want_sig = owner.signature(own_id)
        for slot in sorted(self._by_slot, reverse=True):
            if slot <= owner_slot:
                break
            idx = self._by_slot[slot]
            if not any(m.lower() == owner_name for m in (idx.masters or ())):
                continue
            if formid in idx and (not want_sig
                                  or idx.signature(formid) == want_sig):
                return idx, formid
        return owner, own_id

    def _to_child(self, idx, formid: int) -> int:
        """Translate one of `idx`'s own-space ids back into the child's space."""
        for slot, cand in self._by_slot.items():
            if cand is idx:
                return (slot << 24) | (formid & 0x00FFFFFF)
        return formid

    def __contains__(self, formid: int) -> bool:
        idx, own = self._route(formid)
        return idx is not None and own in idx

    def __len__(self) -> int:
        return len(self.formids())

    def formids(self) -> set:
        return {(slot << 24) | (f & 0x00FFFFFF)
                for slot, idx in self._by_slot.items()
                for f in idx._offsets
                if (f >> 24) & 0xFF == idx.own_index}

    def signature(self, formid: int) -> bytes:
        idx, own = self._route(formid)
        return idx.signature(own) if idx else b''

    def record(self, formid: int) -> bytes:
        """The master's converted record, RESTATED in the child's id space.

        The bytes on disk are numbered in the master's own converted space, so
        when the child gives that master a different slot every FormID inside
        them — the record's own id in the header, and every reference in the
        body — names the wrong file. Shipping them verbatim emitted an override
        at an id another master owns as a different record type: 2,553 of them
        on TWMP Valenwood/Elsweyr, e.g. ElsweyrAnequina's REFR 0201C4B3 written
        over Tamriel.esp's CELL 0201C4B3 (xEdit: "Record [CELL:0201C4B3] in
        Tamriel.esp is being overridden by record [REFR:0201C4B3]"), which
        hangs the engine on the main menu.

        A master at the same slot it uses itself needs no rewrite, which is why
        this stayed invisible until a plugin declared a SECOND TES4 master.
        """
        idx, own = self._route(formid)
        if idx is None:
            return b''
        rec = idx.record(own)
        imap = self._index_maps.get(id(idx))
        return _shift_record_formids(rec, imap) if (rec and imap) else rec

    def group_path(self, formid: int) -> tuple:
        """The master's nesting, with its GRUP labels restated for the child.

        A type-1/6/7 label IS a FormID (the owning WRLD/CELL), so it needs the
        same translation the record bytes do or the override nests under a
        group the child resolves to a different record.
        """
        idx, own = self._route(formid)
        if idx is None:
            return ()
        path = idx.group_path(own)
        imap = self._index_maps.get(id(idx))
        if not imap or not path:
            return path
        out = []
        for gtype, label in path:
            if gtype in _FORMID_LABEL_GROUPS and len(label) == 4:
                fid = struct.unpack('<I', label)[0]
                shifted = _shift_formid(fid, imap)
                if shifted != fid:
                    label = struct.pack('<I', shifted)
            out.append((gtype, label))
        return tuple(out)

    def land(self, cell_formid: int) -> int:
        """The LAND inside a cell, answered by the file that WINS the cell.

        The answer is restated in the slot of whichever master supplied it: a
        LAND that Tamriel.esp defines carries Tamriel.esp's index byte, while
        one it merely inherits from Oblivion.esm keeps Oblivion's. Translating
        through the answering index unconditionally moved every inherited LAND
        into the overriding master's slot, where no such record exists.
        """
        idx, own = self._route(cell_formid)
        if idx is None:
            return 0
        fid = idx.land(own)
        if not fid:
            return 0
        # `fid` is in `idx`'s own converted space; its index byte names the
        # file that DEFINES the land, which may be one of idx's own masters.
        if (fid >> 24) == idx.own_index:
            return self._to_child(idx, fid)
        # Defined further down the chain: the byte already matches the child's
        # numbering when that master occupies the same slot in both lists.
        return fid

    def navms(self, cell_formid: int) -> list:
        """The navmeshes inside a cell, answered by the file that WINS it.

        Each id is restated in the slot of whichever master defines it, as
        `land` does.

        See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
        """
        idx, own = self._route(cell_formid)
        if idx is None:
            return []
        return [self._to_child(idx, fid) if (fid >> 24) == idx.own_index
                else fid
                for fid in idx.navms(own)]

    def find_by_edid(self, signature: bytes, edid: str) -> int:
        """Later masters win, matching load order."""
        for idx in reversed(self._indices):
            fid = idx.find_by_edid(signature, edid)
            if fid:
                return self._to_child(idx, fid)
        return 0


class MissingMasterOutputError(RuntimeError):
    """A plugin's converted master output is required but absent."""


def resolve_master_outputs(masters: list, tes4_master_count: int,
                           output_root: str) -> list:
    """Converted-master plugin paths for a plugin's TES4 masters.

    `masters` is the TES5 master list (new masters prepended); only the trailing
    `tes4_master_count` entries are TES4 masters we convert ourselves. Skyrim.esm
    and friends are vanilla and are never merged against.

    Returns [(master_name, path_or_None), ...] in load order.
    """
    if not tes4_master_count:
        return []
    out = []
    for name in masters[len(masters) - tes4_master_count:]:
        # convert.py writes the ESM into the plugin's output folder, which
        # for an imported mod is its MOD's folder, not one named for it.
        path = str(paths(name, out_root=output_root).esm)
        out.append((name, path if os.path.isfile(path) else None))
    return out


def load_master_index(masters: list, tes4_master_count: int,
                      output_root: str):
    """Index the converted TES4 masters, or raise if any is missing.

    An override IS the master's converted record, so without it there is
    nothing to override and the conversion cannot proceed.

    A lone master is returned unwrapped only when it already numbers itself
    at `base_slot`; otherwise its bytes still name its OWN space and must be
    restated by `ChainedMasterIndex`.
    """
    resolved = resolve_master_outputs(masters, tes4_master_count, output_root)
    if not resolved:
        return None

    missing = [(n, str(paths(n, out_root=output_root).esm))
               for n, p in resolved if p is None]
    if missing:
        lines = [
            "Converted master output not found - cannot convert overrides.",
            "",
            "This plugin's records mostly OVERRIDE its master, and an override",
            "is emitted as the master's converted record with the author's",
            "changes applied. Without it there is nothing to override.",
            "",
            "Missing:",
        ]
        lines += [f"  {name}  (expected at {path})" for name, path in missing]
        lines += ["", "Convert the master first:"]
        lines += [f"  python convert.py -f {name}" for name, _ in missing]
        raise MissingMasterOutputError("\n".join(lines))

    indices = [MasterIndex(path) for _, path in resolved]
    base_slot = len(masters) - tes4_master_count
    if len(indices) == 1 and indices[0].own_index == base_slot:
        return indices[0]
    return ChainedMasterIndex(indices, base_slot=base_slot,
                              child_masters=masters)
