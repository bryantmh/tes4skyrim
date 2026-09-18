"""
Morrowind's journal as Skyrim quests, in the simplest faithful form.

A TES3 journal is a DIAL of type Journal whose INFOs are its pages: each has an
index and a text, one may carry the quest's display NAME, and one may mark it
FINISHED. That maps onto a QUST directly -- a stage per index, the page as the
stage's log entry -- and MorrowindRuntime calls `SetStage` whenever a result
script runs `Journal`, so Skyrim's own journal fills in as the player talks.

An objective per page rides alongside, because a QUST with none never displays
its name; the runtime displays one as it stages the quest.

The pages are read from the STAGED SIDECAR, not this plugin's export, because
the sidecar's dialogue is already merged over the plugin's TES3 masters and a
result script names its masters' quests freely.

See: docs/commentary/morrowind_runtime.md#journal-quests
"""

import os
import re
import struct

from ..base.text_reader import unescape_value
from ..record_types.common import (
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    pack_uint32_subrecord,
)
from .morrowind_sidecar import DIALOGUE_FILES, export_records
from .objective_text import OBJECTIVE_MAX_CHARS
from .quest import QUST_ALLOW_REPEATED_STAGES

#: `quest id=Plugin.esm|FormID`, which is how the runtime finds the QUST to stage.
QUESTS_TABLE = 'MWQS.txt'

#: The derive_formid site; the key is the authored journal id, lowercased.
_FORMID_SITE = 'MW_JOURNAL'

#: DNAM: a side quest, so it lists in the journal, at a middling priority.
_QUEST_TYPE_SIDE = 8
_PRIORITY = 50

#: QSDT: this log entry completes the quest.
_COMPLETES_QUEST = 0x01

#: Skyrim EditorIDs are safest as plain identifiers; TES3 ids are free text.
_NOT_IDENTIFIER = re.compile(r'[^A-Za-z0-9_]')

#: The INFO fields a journal page is built from.
_PAGE_KEYS = ('Topic', 'InfoType', 'JournalIndex', 'Response', 'QuestStatus')


def journal_quests(side_dir: str) -> dict:
    """`{lower id: {'id', 'name', 'stages': {index: (text, finished)}}}` from
    a staged sidecar."""
    topics_file, infos_file = (os.path.join(side_dir, name)
                               for name in DIALOGUE_FILES)
    quests = {}
    for rec in export_records(topics_file, ('EditorID', 'DialType')):
        if rec.get('DialType') == 'Journal' and rec.get('EditorID'):
            quest_id = unescape_value(rec['EditorID'])
            quests[quest_id.lower()] = {'id': quest_id, 'name': '',
                                        'stages': {}}
    for rec in export_records(infos_file, _PAGE_KEYS):
        quest = quests.get(unescape_value(rec.get('Topic', '')).lower())
        if quest is None or rec.get('InfoType') != 'Journal':
            continue
        text = unescape_value(rec.get('Response', ''))
        if rec.get('QuestStatus') == 'Name':
            quest['name'] = text
            continue
        index = int(rec.get('JournalIndex', '0') or 0)
        quest['stages'].setdefault(
            index, (text, rec.get('QuestStatus') == 'Finished'))
    return quests


def editor_id(quest_id: str) -> str:
    """A Skyrim-safe EditorID for a TES3 journal id."""
    return 'MWJ_' + _NOT_IDENTIFIER.sub('_', quest_id)


def objective_line(text: str) -> str:
    """A page shortened to fit NNAM's 71-character cap.

    The first sentence, else a cut on a word boundary. Morrowind pages are
    paragraphs with no authored short form, so this derives one.
    """
    line = ' '.join(text.split())
    ends = [at for at in (line.find(mark) for mark in '.!?') if at > 0]
    cut = min(ends) if ends else -1
    if 0 < cut < OBJECTIVE_MAX_CHARS:
        return line[:cut + 1]
    if len(line) <= OBJECTIVE_MAX_CHARS:
        return line
    clipped = line[:OBJECTIVE_MAX_CHARS - 3]
    space = clipped.rfind(' ')
    if space > OBJECTIVE_MAX_CHARS // 2:
        clipped = clipped[:space]
    return clipped.rstrip(' ,;:') + '...'


def _stages(quest: dict) -> bytes:
    """INDX + QSDT + CNAM per journal page, in index order."""
    subs = b''
    for index in sorted(quest['stages']):
        text, finished = quest['stages'][index]
        subs += pack_subrecord('INDX', struct.pack('<HBB', index, 0, 0))
        subs += pack_uint8_subrecord('QSDT',
                                     _COMPLETES_QUEST if finished else 0)
        if text:
            subs += pack_string_subrecord('CNAM', text)
    return subs


def _objectives(quest: dict) -> bytes:
    """QOBJ + FNAM + NNAM per page that has text.

    🛑 The objectives are why the quest shows at all: a QUST with none never
    displays its name, however its stages are set. The runtime displays one as
    it stages the quest; there are no QSTA targets because a Morrowind page
    names no world object.
    See: docs/commentary/morrowind_runtime.md#objectives-must-be-displayed
    """
    subs = b''
    for index in sorted(quest['stages']):
        text = quest['stages'][index][0]
        if not text:
            continue
        subs += pack_subrecord('QOBJ', struct.pack('<H', index))
        subs += pack_uint32_subrecord('FNAM', 0)
        subs += pack_string_subrecord('NNAM', objective_line(text))
    return subs


def as_record(quest: dict, formid: int) -> bytes:
    """One journal topic as a QUST, at exactly `formid`.

    Order: EDID FULL DNAM NEXT [stages] [objectives] ANAM. A journal page
    carries no conditions, targets or aliases, so ANAM is zero and `formid` is
    written as given -- `derive_formid` already returns this plugin's final id.
    """
    subs = pack_string_subrecord('EDID', editor_id(quest['id']))
    subs += pack_string_subrecord('FULL', quest['name'] or quest['id'])
    subs += pack_subrecord('DNAM', struct.pack(
        '<HBBII', QUST_ALLOW_REPEATED_STAGES, _PRIORITY, 0, 0,
        _QUEST_TYPE_SIDE))
    subs += pack_subrecord('NEXT', b'')
    subs += _stages(quest)
    subs += _objectives(quest)
    subs += pack_uint32_subrecord('ANAM', 0)
    return pack_record('QUST', formid, 0, subs)


def write_journal_quests(writer, side_dir: str, plugin_name: str) -> int:
    """Add a QUST per journal topic in `side_dir`'s dialogue and stage the id
    table beside it. Returns how many quests were written."""
    quests = journal_quests(side_dir)
    lines = []
    for key in sorted(quests):
        quest = quests[key]
        if not quest['stages']:
            continue
        formid = writer.derive_formid(_FORMID_SITE, key)
        writer.add_record('QUST', as_record(quest, formid))
        lines.append(f"{quest['id']}={plugin_name}|{formid:08X}")
    if lines:
        with open(os.path.join(side_dir, QUESTS_TABLE), 'w',
                  encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
    return len(lines)
