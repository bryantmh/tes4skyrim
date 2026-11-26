"""A journal is its originating plugin's QUST; a dependent overrides it.

See: docs/plans/morrowind_object_scripts.md#cumulative-gather-must-go
"""

import os

from tes5_import.dialogue.objective_text import load_objective_text
from tes5_import.dialogue.quest_morrowind import (QUESTS_TABLE, journal_quests,
                                                  objective_line,
                                                  write_journal_quests)


def _records(signature: str, rows: list) -> str:
    """Export-format blocks of `signature`, one per `{key: value}` row."""
    blocks = []
    for row in rows:
        body = ''.join(f'{key}={value}\n' for key, value in row.items())
        blocks.append(f'---RECORD_BEGIN---\nSignature={signature}\n{body}'
                      '---RECORD_END---\n')
    return '\n'.join(blocks)


def _sidecar(folder, journals: list, pages: list, table: str = '') -> str:
    """A sidecar folder holding `journals` (DIAL ids), their `pages`, and a
    quest table."""
    os.makedirs(folder, exist_ok=True)
    dials = [{'EditorID': name, 'DialType': 'Journal'} for name in journals]
    with open(os.path.join(folder, 'DIAL.txt'), 'w', encoding='utf-8') as fh:
        fh.write(_records('MWDI', dials))
    with open(os.path.join(folder, 'INFO.txt'), 'w', encoding='utf-8') as fh:
        fh.write(_records('MWIN', pages))
    if table:
        with open(os.path.join(folder, QUESTS_TABLE), 'w',
                  encoding='utf-8') as fh:
            fh.write(table)
    return str(folder)


def _page(topic: str, index: int, text: str, **extra) -> dict:
    """One journal INFO."""
    return {'Topic': topic, 'InfoType': 'Journal', 'JournalIndex': index,
            'Response': text, **extra}


class _Writer:
    """What `write_journal_quests` needs from the plugin writer."""

    def __init__(self, masters: list):
        """Record every QUST added."""
        self.masters = masters
        self.records = []

    def derive_formid(self, _site: str, key: str) -> int:
        """This plugin's own id, index byte = its master count."""
        return (len(self.masters) << 24) | (sum(map(ord, key)) & 0xFFFF)

    def add_record(self, sig: str, data: bytes) -> None:
        """Keep the record."""
        self.records.append((sig, data))


def test_a_dependent_overrides_its_masters_journal(tmp_path):
    """A master's journal is overridden at the master's FormID with the whole
    chain's pages, and only the plugin's own journal gets a table row."""
    master = _sidecar(tmp_path / 'Master', ['MQ'], [_page('MQ', 10, 'Began.')],
                      'MQ=Master.esp|01000ABC\n')
    own = _sidecar(tmp_path / 'Child', ['MQ', 'CQ'],
                   [_page('MQ', 20, 'Went on.'), _page('CQ', 5, 'Mine.')])
    writer = _Writer(['Skyrim.esm', 'Master.esp'])
    assert write_journal_quests(writer, own, 'Child.esp',
                                [('Master.esp', master)]) == 2
    by_formid = {int.from_bytes(data[12:16], 'little'): data
                 for _sig, data in writer.records}
    override = by_formid[0x01000ABC]
    assert override.count(b'INDX') == 2
    with open(os.path.join(own, QUESTS_TABLE), encoding='utf-8') as fh:
        rows = fh.read().splitlines()
    assert [row.split('=')[0] for row in rows] == ['CQ']


def test_a_redeclared_journal_with_no_pages_writes_nothing(tmp_path):
    """Re-declaring a master's journal without pages adds no record and no
    table, and a stale table from an older build is removed."""
    master = _sidecar(tmp_path / 'Master', ['MQ'], [_page('MQ', 10, 'Began.')],
                      'MQ=Master.esp|01000ABC\n')
    own = _sidecar(tmp_path / 'Child', ['MQ'], [],
                   'MQ=Child.esp|02000001\n')
    writer = _Writer(['Skyrim.esm', 'Master.esp'])
    assert write_journal_quests(writer, own, 'Child.esp',
                                [('Master.esp', master)]) == 0
    assert not writer.records
    assert not os.path.exists(os.path.join(own, QUESTS_TABLE))


def test_a_deleted_page_is_not_a_stage(tmp_path):
    """A tombstoned journal INFO never becomes a stage."""
    own = _sidecar(tmp_path / 'Child', ['CQ'],
                   [_page('CQ', 5, 'Mine.'), _page('CQ', 9, '', Deleted=1)])
    assert sorted(journal_quests(own)['cq']['stages']) == [5]


def test_a_page_morroblivion_shortened_takes_its_line():
    """A vanilla page in either short-objective table gets that line as NNAM."""
    load_objective_text(quiet=True)
    verbatim = ("I'm told that Addhiranirr is hiding because a Census and Excise "
                "agent is in the area. She's probably down in the underworks.")
    reworded = ('%Name gave me a copy of "The Pilgrim\'s Path" and asked me to '
                'make the Pilgrimages of the Seven Graces.')
    assert objective_line(verbatim) == 'Search the underworks for Addhiranirr'
    assert objective_line(reworded) == 'Make the Pilgrimage of the Seven Graces'
    assert objective_line('Not in any table. Second.') == 'Not in any table.'
