"""The arrest force-greet: a guard's greeting, offered on Skyrim's arrest channel.

Oblivion's guard walks up and opens dialogue with an ordinary GREETING, whose
arrest lines ask `IsGuard == 1`.  Skyrim's pursuing guard opens dialogue with
the ForceGreet subtype (PFGT) instead -- vanilla's DGCrimeForcegreetTopic -- so
every GREETING INFO that only a guard can say is offered there too, under the
same quest.

Each copy is a shared INFO: DNAM names the converted greeting, so it speaks
that line's response and voice, and keeps its own conditions, choices and
fragment.  It carries no replay lockout, since a guard may arrest again.
"""

import struct

from ..base.writer import (pack_group, pack_record, pack_string_subrecord,
                           pack_subrecord)
from ..record_types.common import get_formid
from ..record_types.crime import is_guard_class
from ..record_types.npc_morrowind import is_morrowind_npc
from .converter import convert_DIAL, make_generic_quest

#: The quest owning the Morrowind arrest topic; a dependent reuses its master's.
_MORROWIND_ARREST_QUEST = 'TES4MorrowindArrest'

#: TES4 IsGuard.
_FUNC_IS_GUARD = 125

#: The ForceGreet subtype (DIAL SNAM) and its category; vanilla DATA is 00 01 00 00.
_PFGT = b'PFGT'
_PFGT_CATEGORY = 1

#: Response subrecords a shared INFO leaves to the INFO it names.
_RESPONSE_SUBS = (b'TRDT', b'NAM1', b'NAM2', b'NAM3', b'EDID')

#: TES4 condition byte 0: compare operator (high 3 bits), Or / RunOnTarget flags.
_OP_EQUAL, _OP_NOT_EQUAL = 0x00, 0x20
_FLAG_OR, _FLAG_RUN_ON_TARGET = 0x01, 0x02


def _conditions(info_rec: dict):
    """Yield each raw TES4 CTDA of an INFO."""
    i = 0
    while info_rec.get(f'Condition[{i}].Raw') is not None:
        try:
            yield bytes.fromhex(info_rec[f'Condition[{i}].Raw'])
        except ValueError:
            pass
        i += 1


def requires_guard(info_rec: dict) -> bool:
    """Whether this INFO can only be said by a guard: an AND-ed IsGuard true test."""
    for raw in _conditions(info_rec):
        if len(raw) < 12 or struct.unpack_from('<H', raw, 8)[0] != _FUNC_IS_GUARD:
            continue
        if raw[0] & (_FLAG_OR | _FLAG_RUN_ON_TARGET):
            continue
        op, value = raw[0] & 0xE0, struct.unpack_from('<f', raw, 4)[0]
        if (op == _OP_EQUAL and value == 1.0) or (op == _OP_NOT_EQUAL and value == 0.0):
            return True
    return False


def _subrecords(body: bytes):
    """Yield (signature, payload) for each subrecord of a record body."""
    pos = 0
    while pos + 6 <= len(body):
        size = struct.unpack_from('<H', body, pos + 4)[0]
        yield body[pos:pos + 4], body[pos + 6:pos + 6 + size]
        pos += 6 + size


def _records(children: bytes):
    """Yield (FormID, flags, body) for each INFO in a topic's packed children."""
    pos = 0
    while pos + 24 <= len(children):
        size, flags, fid = struct.unpack_from('<III', children, pos + 4)
        yield fid, flags, children[pos + 24:pos + 24 + size]
        pos += 24 + size


def _shared_copy(body: bytes, source_fid: int, fid: int, flags: int) -> bytes:
    """A shared INFO naming `source_fid`, with no response and no lockout."""
    subs, placed = b'', False
    for sig, data in _subrecords(body):
        if sig in _RESPONSE_SUBS:
            continue
        if sig == b'ENAM' and len(data) >= 4:
            data = data[:2] + b'\x00\x00'
        if sig == b'CTDA' and not placed:
            subs += pack_subrecord('DNAM', struct.pack('<I', source_fid))
            placed = True
        subs += pack_subrecord(sig.decode('ascii'), data)
    if not placed:
        subs += pack_subrecord('DNAM', struct.pack('<I', source_fid))
    return pack_record('INFO', fid, flags, subs)


def force_greet_topic(writer, src_dial: dict, owner_qfid: int, infos: list,
                      children: bytes) -> bytes:
    """The PFGT topic offering a quest's guard-only greetings, or b''.

    `infos` pairs each source INFO of the quest's converted GREETING topic with
    its output FormID; `children` are its packed INFO records.  New ids are
    keyed on the source INFO and quest FormIDs.
    """
    guard = {out_fid: rec for out_fid, rec in infos if requires_guard(rec)}
    copies, count = b'', 0
    for fid, flags, body in _records(children):
        if fid in guard:
            copy_fid = writer.derive_formid('ARREST_INFO', guard[fid]['FormID'])
            copies += _shared_copy(body, fid, copy_fid, flags)
            count += 1
    if not count:
        return b''
    source_quest = next(iter(guard.values())).get('QSTI.Quest', '')
    dial_fid = writer.derive_formid('ARREST_DIAL', source_quest)
    dial = convert_DIAL(src_dial, info_count=count, dlbr_fid=0,
                        quest_fid=owner_qfid, category=_PFGT_CATEGORY,
                        subtype=0, snam=_PFGT,
                        edid_override=f'TES4ForceGreet_{owner_qfid:08X}',
                        formid_override=dial_fid)
    return dial + pack_group(7, struct.pack('<I', dial_fid), copies)


def _is_guard_ctda() -> bytes:
    """TES5 CTDA: the subject's IsGuard == 1."""
    return pack_subrecord('CTDA', struct.pack('<B3xfHHIIIIi', _OP_EQUAL, 1.0,
                                              _FUNC_IS_GUARD, 0, 0, 0, 0, 0, -1))


def _blank_info(fid: int) -> bytes:
    """One silent, text-less line a guard may open dialogue with."""
    subs = pack_subrecord('ENAM', struct.pack('<HH', 0, 0))
    subs += pack_subrecord('CNAM', struct.pack('<B', 0))
    subs += pack_subrecord('TRDT', struct.pack('<IiI B3x I B3x', 0, 50, 0, 1, 0, 1))
    subs += pack_string_subrecord('NAM2', '')
    subs += pack_string_subrecord('NAM3', '')
    subs += _is_guard_ctda()
    return pack_record('INFO', fid, 0, subs)


def morrowind_arrest_topic(by_type: dict, writer, master_index=None) -> set:
    """Let a Morrowind guard open dialogue when he comes to arrest; SGE quest fids.

    A Morrowind speaker's lines live in the Morrowind runtime, which Skyrim's
    PFGT channel cannot reach.  This one blank guard line lets the pursuing
    guard open Skyrim's dialogue menu, which the runtime then replaces with the
    guard's own Morrowind dialogue.  Written once, by the first plugin that
    has a Morrowind guard.
    """
    if not any(is_morrowind_npc(r) and is_guard_class(get_formid(r, 'CNAM.Class'))
               for r in by_type.get('NPC_', [])):
        return set()
    if master_index is not None and \
            master_index.find_by_edid(b'QUST', _MORROWIND_ARREST_QUEST):
        return set()
    quest = make_generic_quest(writer, _MORROWIND_ARREST_QUEST,
                               'TES4 Morrowind Arrest')
    dial_fid = writer.derive_formid('ARREST_DIAL', _MORROWIND_ARREST_QUEST)
    dial = convert_DIAL({'RecordFlags': '0'}, info_count=1, dlbr_fid=0,
                        quest_fid=quest, category=_PFGT_CATEGORY, subtype=0,
                        snam=_PFGT, edid_override='TES4MorrowindForceGreet',
                        formid_override=dial_fid)
    info = _blank_info(writer.derive_formid('ARREST_INFO', _MORROWIND_ARREST_QUEST))
    writer.add_raw_group('DIAL', dial + pack_group(7, struct.pack('<I', dial_fid), info))
    return {quest}
