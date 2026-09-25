"""The journal patch lands in frame 1, only on journal movies, exactly once.

Built against a synthetic movie whose class pool carries each journal's
identifying strings, so nothing here needs a game install.
See: docs/commentary/tes_runtime_journal.md#journal-stage-text
"""

import os
import struct
import zipfile

from asset_convert.ui.avm1 import UNDEFINED, Register, assemble
from asset_convert.ui.journal_patch import (KIND_QJO, KIND_QUESTS_PAGE, MARKER,
                                            is_patched, patch_movie)
from asset_convert.ui.swf import (TAG_DO_ACTION, TAG_DO_INIT_ACTION, TAG_END,
                                  TAG_SHOW_FRAME, TWIPS, Swf, Tag, pack_rect)
from core.gui.config import CLR
from core.gui.journal import (FAILED, MOD_NAME, PATCHED, SKIPPED,
                              _archive_rank, build_journal_mod, report_status)
from output_layout import write_mod_zip
from tools.disasm.swf_as2_disasm import disassemble

#: Skyrim's 1280x720 menu stage.
_FRAME_RECT = pack_rect(0, 1280 * TWIPS, 0, 720 * TWIPS)


def _movie(*pool: str) -> bytes:
    """A one-frame movie whose DoInitAction pool holds `pool`."""
    names = b''.join(s.encode() + b'\0' for s in pool)
    init = struct.pack('<H', 7) + bytes((0x88,)) + struct.pack(
        '<HH', len(names) + 2, len(pool)) + names + b'\0'
    swf = Swf(10, _FRAME_RECT, 0x1800, 1,
              [Tag(TAG_DO_INIT_ACTION, init), Tag(TAG_SHOW_FRAME, b''),
               Tag(TAG_END, b'')])
    return swf.serialize()


def _quests_page_movie() -> bytes:
    """A movie carrying vanilla's QuestsPage signature."""
    return _movie('QuestsPage', 'onObjectiveListSelect', 'SetDescriptionText',
                  'ObjectiveList')


def test_branches_resolve_to_labels():
    """If/Jump offsets count from the instruction after the branch."""
    code = assemble([('label', 'top'), ('push', 1), ('if', 'end'),
                     ('jump', 'top'), ('label', 'end'), 'Pop'])
    lines = disassemble(code)
    assert lines[1].endswith('If 5'), lines
    assert lines[2].endswith('Jump -18'), lines


def test_push_types_round_trip():
    """Every push type reads back as the value that was pushed."""
    code = assemble([('push', 'a', 7, True, None, UNDEFINED, Register(2))])
    assert disassemble(code)[0].endswith(
        "Push 'a', 7, true, null, undefined, reg2")


def test_function_body_length_covers_body():
    """DefineFunction's code size is the body's, so the caller resumes after it."""
    code = assemble([('function', ('x',), ['Pop']), 'Pop'])
    lines = disassemble(code)
    assert 'DefineFunction <anon>(1 args)' in lines[0]
    assert lines[1].endswith('Pop') and lines[2].endswith('Pop'), lines


def test_quests_page_patched_before_first_frame_ends():
    """The DoAction sits after every class and before the first ShowFrame."""
    patched, kinds = patch_movie(_quests_page_movie())
    assert kinds == [KIND_QUESTS_PAGE]
    codes = [t.code for t in Swf.parse(patched).tags]
    assert codes == [TAG_DO_INIT_ACTION, TAG_DO_ACTION, TAG_SHOW_FRAME, TAG_END]
    assert is_patched(patched)


def test_qjo_recognised_by_content():
    """Quest Journal Overhaul's class is patched with its own program."""
    raw = _movie('QuestJournal', 'SetObjectives', 'ObjectivesList_mc',
                 'QuestDescription')
    patched, kinds = patch_movie(raw)
    assert kinds == [KIND_QJO]
    ours = [t for t in Swf.parse(patched).tags if t.code == TAG_DO_ACTION][0]
    assert b'TESRT_ShowRow\0' in ours.data


def test_patch_never_reaches_for_skse():
    """QJO loads its movie itself, so SKSE never adds `skse` to it; the patch
    must flag itself for the runtime and call only `_root.TESRT_Runtime`."""
    for raw in (_quests_page_movie(),
                _movie('QuestJournal', 'SetObjectives', 'ObjectivesList_mc',
                       'QuestDescription')):
        patched, _ = patch_movie(raw)
        ours = [t for t in Swf.parse(patched).tags
                if t.code == TAG_DO_ACTION][0].data
        assert b'skse\0' not in ours
        assert b'TESRT_Patched\0' in ours and b'TESRT_Runtime\0' in ours


def test_repatch_replaces_the_previous_patch():
    """Patching a patched movie leaves exactly one patch."""
    once, _ = patch_movie(_quests_page_movie())
    twice, _ = patch_movie(once)
    ours = [t for t in Swf.parse(twice).tags
            if MARKER.encode() in t.data]
    assert len(ours) == 1 and twice == once


def test_other_movies_untouched():
    """A movie with no journal class is refused, not rewritten."""
    assert patch_movie(_movie('InventoryMenu', 'SetDescriptionText')) == (None, [])


def test_report_headline():
    """A failure reads red, any patch green, and all-skipped yellow."""
    assert report_status([PATCHED, FAILED]) == ('Build Failed', CLR['red'])
    assert report_status([PATCHED, SKIPPED]) == ('Built Successfully',
                                                 CLR['green'])
    assert report_status([SKIPPED]) == ('Nothing Patched', CLR['yellow'])


def test_mod_zip_holds_the_patched_journal(tmp_path):
    """The loaded journal is zipped under Interface/, and the game folder is
    left exactly as it was."""
    data, out = tmp_path / 'Data', tmp_path / 'output'
    (data / 'Interface').mkdir(parents=True)
    original = _quests_page_movie()
    (data / 'Interface' / 'quest_journal.swf').write_bytes(original)
    results, zip_path = build_journal_mod(str(data), out)
    assert [o for o, _ in results] == [PATCHED, SKIPPED]
    assert zip_path == out / 'Finished Mods' / f'{MOD_NAME}.zip'
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ['Interface/quest_journal.swf']
        assert is_patched(zf.read('Interface/quest_journal.swf'))
    assert os.listdir(data / 'Interface') == ['quest_journal.swf']
    assert (data / 'Interface' / 'quest_journal.swf').read_bytes() == original


def test_nothing_to_patch_writes_no_zip(tmp_path):
    """With no journal installed there is no mod to write."""
    (tmp_path / 'Data').mkdir()
    results, zip_path = build_journal_mod(str(tmp_path / 'Data'), tmp_path)
    assert zip_path is None and [o for o, _ in results] == [SKIPPED, SKIPPED]
    assert not (tmp_path / 'Finished Mods').exists()


def test_mod_zip_swaps_in_whole(tmp_path):
    """A failed write leaves the previous zip and no temporary file."""
    zip_path = tmp_path / 'Mod.zip'
    assert write_mod_zip(zip_path, [('a.txt', b'old')]) == 1

    def _boom(_count, _name):
        """Fail after the first member, as an interrupted run would."""
        raise KeyboardInterrupt

    try:
        write_mod_zip(zip_path, [('a.txt', b'new'), ('b.txt', b'x')], _boom)
    except KeyboardInterrupt:
        pass
    assert os.listdir(tmp_path) == ['Mod.zip']
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.read('a.txt') == b'old'


def test_archive_belongs_to_its_plugin():
    """`X.bsa` and `X - Part.bsa` both load with plugin X."""
    ranks = {'skyui_se': 40, 'skyrim': 0}
    assert _archive_rank('SkyUI_SE.bsa', ranks) == 40
    assert _archive_rank('Skyrim - Interface.bsa', ranks) == 0
    assert _archive_rank('Loose Textures.bsa', ranks) == -1
