"""The AI package pool is minted only for a plugin that can issue AI commands.

See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
"""

import os

from tes5_import.dialogue.ai_packages_morrowind import (ALIASES_TABLE,
                                                        write_ai_packages)


class _Writer:
    """What `write_ai_packages` needs from the plugin writer."""

    def __init__(self):
        """Record every record added."""
        self.records = []

    def derive_formid(self, _site: str, key: str) -> int:
        """A stable id per key."""
        return 0x01000000 | (sum(map(ord, key)) & 0xFFFF)

    def add_record(self, sig: str, data: bytes) -> None:
        """Keep the record's signature."""
        self.records.append(sig)


def test_plugin_without_scripts_or_actors_gets_no_pool(tmp_path):
    """A grass-only export writes no QUST/PACK and removes an earlier table."""
    export, side = tmp_path / 'export', tmp_path / 'side'
    export.mkdir()
    side.mkdir()
    (export / 'STAT.txt').write_text('', encoding='utf-8')
    (side / ALIASES_TABLE).write_text('quest=Old.esp|01000001\n', encoding='utf-8')
    writer = _Writer()
    assert write_ai_packages(writer, str(side), 'Grass.esp', str(export)) == 0
    assert writer.records == []
    assert not os.path.exists(side / ALIASES_TABLE)


def test_plugin_with_scripts_gets_the_pool(tmp_path):
    """An export with SCPT records gets the quest, every PACK and the table."""
    export, side = tmp_path / 'export', tmp_path / 'side'
    export.mkdir()
    side.mkdir()
    (export / 'SCPT.txt').write_text('', encoding='utf-8')
    writer = _Writer()
    packs = write_ai_packages(writer, str(side), 'Mod.esp', str(export))
    assert packs == 160
    assert writer.records.count('PACK') == 160 and writer.records.count('QUST') == 1
    assert os.path.isfile(side / ALIASES_TABLE)
