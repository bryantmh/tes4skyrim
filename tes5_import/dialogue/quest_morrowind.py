"""
Morrowind's journal as Skyrim quests, in the simplest faithful form.

A TES3 journal is a DIAL of type Journal whose INFOs are its pages: each has an
index and a text, one may carry the quest's display NAME, and one may mark it
FINISHED. That maps onto a QUST directly -- a stage per index, the page as the
stage's log entry -- and MorrowindRuntime calls `SetStage` whenever a result
script runs `Journal`, so Skyrim's own journal fills in as the player talks.

An objective per page rides alongside, because a QUST with none never displays
its name; the runtime displays one as it stages the quest.

The pages are read from the STAGED SIDECARS. A journal is the QUST of the
plugin that originates it; a dependent that adds pages writes an OVERRIDE of
that QUST carrying every page of the chain, and stages no row of its own.

See: docs/commentary/morrowind_runtime.md#journal-quests
"""

import functools
import json
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
from .objective_text import OBJECTIVE_MAX_CHARS, short_objective
from .quest import QUST_ALLOW_REPEATED_STAGES

#: `quest id=Plugin.esm|FormID`, which is how the runtime finds the QUST to stage.
QUESTS_TABLE = 'quests_formid.txt'

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
_PAGE_KEYS = ('Topic', 'InfoType', 'JournalIndex', 'Response', 'QuestStatus',
              'Deleted')

#: Names for journals that author none, hand-written first, then UESP's.
_NAME_TABLES = tuple(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'generated', name)
    for name in ('morrowind_quest_names_authored.json',
                 'morrowind_quest_names.json'))


def journal_quests(side_dir: str, files: tuple = DIALOGUE_FILES) -> dict:
    """`{lower id: {'id', 'name', 'stages': {index: (text, finished)}}}` from
    a staged sidecar, or from an export when `files` is EXPORT_DIALOGUE."""
    topics_file, infos_file = (os.path.join(side_dir, name)
                               for name in files)
    quests = {}
    for rec in export_records(topics_file, ('EditorID', 'DialType')):
        if rec.get('DialType') == 'Journal' and rec.get('EditorID'):
            quest_id = unescape_value(rec['EditorID'])
            quests[quest_id.lower()] = {'id': quest_id, 'name': '',
                                        'stages': {}}
    for rec in export_records(infos_file, _PAGE_KEYS):
        quest = quests.get(unescape_value(rec.get('Topic', '')).lower())
        if (quest is None or rec.get('InfoType') != 'Journal'
                or rec.get('Deleted') == '1'):
            continue
        text = unescape_value(rec.get('Response', ''))
        if rec.get('QuestStatus') == 'Name':
            quest['name'] = text
            continue
        index = int(rec.get('JournalIndex', '0') or 0)
        quest['stages'].setdefault(
            index, (text, rec.get('QuestStatus') == 'Finished'))
    return quests


@functools.lru_cache(maxsize=None)
def _table_names() -> dict:
    """`{lower journal id: name}` over `_NAME_TABLES`, the first one winning."""
    names = {}
    for path in reversed(_NAME_TABLES):
        with open(path, encoding='utf-8') as handle:
            names.update(json.load(handle)['entries'])
    return names


def quest_name(quest: dict) -> str:
    """FULL: the journal's QSTN name, else a table's, else its raw id.

    See: docs/commentary/morrowind_runtime.md#quest-names
    """
    return (quest['name'] or _table_names().get(quest['id'].lower())
            or quest['id'])


def editor_id(quest_id: str) -> str:
    """A Skyrim-safe EditorID for a TES3 journal id."""
    return 'MWJ_' + _NOT_IDENTIFIER.sub('_', quest_id)


def objective_line(text: str) -> str:
    """A page's NNAM line, within the 71-character cap.

    The short line written for the same page in Morroblivion when the
    objective tables have one, else the first sentence, else a cut on a word
    boundary.
    See: docs/commentary/morrowind_runtime.md#objective-lines
    """
    short = short_objective(text)
    if short != text:
        return short
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
    subs += pack_string_subrecord('FULL', quest_name(quest))
    subs += pack_subrecord('DNAM', struct.pack(
        '<HBBII', QUST_ALLOW_REPEATED_STAGES, _PRIORITY, 0, 0,
        _QUEST_TYPE_SIDE))
    subs += pack_subrecord('NEXT', b'')
    subs += _stages(quest)
    subs += _objectives(quest)
    subs += pack_uint32_subrecord('ANAM', 0)
    return pack_record('QUST', formid, 0, subs)


def _owned_quests(masters: list) -> dict:
    """`{lower journal id: (owner plugin, local FormID)}` from the masters'
    quest tables, the most-master owner winning."""
    owners = {}
    for plugin, side_dir in masters:
        for rec in _table_rows(os.path.join(side_dir, QUESTS_TABLE)):
            quest_id, _, value = rec.partition('=')
            owner, _, formid = value.partition('|')
            if owner.lower() == plugin.lower() and formid:
                owners.setdefault(quest_id.lower(),
                                  (owner, int(formid, 16) & 0xFFFFFF))
    return owners


def _table_rows(path: str) -> list:
    """The lines of a staged table, [] when there is none."""
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8') as handle:
        return handle.read().splitlines()


def _chain_pages(key: str, chains: list, own: dict) -> dict:
    """`own` with every page of the journal `key` across the masters'
    sidecar `chains` (load order), a later plugin's page of an index winning."""
    merged = {'id': own['id'], 'name': '', 'stages': {}}
    for quests in chains + [{key: own}]:
        quest = quests.get(key)
        if quest:
            merged['name'] = quest['name'] or merged['name']
            merged['stages'].update(quest['stages'])
    return merged


def _override_formid(writer, owner: str, local: int) -> int:
    """The owner's QUST in this plugin's FormID space."""
    names = [name.lower() for name in writer.masters]
    return (names.index(owner.lower()) << 24) | local


def write_journal_quests(writer, side_dir: str, plugin_name: str,
                         masters: list) -> int:
    """Add a QUST per journal topic in `side_dir`'s dialogue and stage the id
    table beside it; returns how many quests were written. `masters` is
    `[(plugin, sidecar dir)]` in load order. A journal a master originates is
    written as an override of the master's QUST, with the chain's pages.

    See: docs/plans/morrowind_object_scripts.md#cumulative-gather-must-go
    """
    quests = journal_quests(side_dir)
    owners = _owned_quests(masters)
    chains = None
    lines, written = [], 0
    for key in sorted(quests):
        quest = quests[key]
        if not quest['stages'] and not quest['name']:
            continue
        if key in owners and owners[key][0].lower() in (
                name.lower() for name in writer.masters):
            chains = chains or [journal_quests(folder) for _p, folder in masters]
            formid = _override_formid(writer, *owners[key])
            quest = _chain_pages(key, chains, quest)
        elif quest['stages']:
            formid = writer.derive_formid(_FORMID_SITE, key)
            lines.append(f"{quest['id']}={plugin_name}|{formid:08X}")
        else:
            continue
        writer.add_record('QUST', as_record(quest, formid))
        written += 1
    table = os.path.join(side_dir, QUESTS_TABLE)
    if lines:
        with open(table, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
    elif os.path.isfile(table):
        os.remove(table)
    return written
