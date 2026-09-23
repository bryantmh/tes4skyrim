"""The journal patch lands in frame 1, only on journal movies, exactly once.

Built against a synthetic movie whose class pool carries each journal's
identifying strings, so nothing here needs a game install.
See: docs/commentary/morrowind_runtime.md#journal-stage-text
"""

import struct

from asset_convert.ui.avm1 import UNDEFINED, Register, assemble
from asset_convert.ui.journal_patch import (KIND_QJO, KIND_QUESTS_PAGE, MARKER,
                                            is_patched, patch_movie)
from asset_convert.ui.swf import (TAG_DO_ACTION, TAG_DO_INIT_ACTION, TAG_END,
                                  TAG_SHOW_FRAME, TWIPS, Swf, Tag, pack_rect)
from core.gui.config import CLR
from core.gui.journal import (FAILED, PATCHED, SKIPPED, _archive_rank,
                              report_status)
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
    assert b'MWRT_ShowRow\0' in ours.data


def test_patch_never_reaches_for_skse():
    """QJO loads its movie itself, so SKSE never adds `skse` to it; the patch
    must flag itself for the runtime and call only `_root.MWRT_Runtime`."""
    for raw in (_quests_page_movie(),
                _movie('QuestJournal', 'SetObjectives', 'ObjectivesList_mc',
                       'QuestDescription')):
        patched, _ = patch_movie(raw)
        ours = [t for t in Swf.parse(patched).tags
                if t.code == TAG_DO_ACTION][0].data
        assert b'skse\0' not in ours
        assert b'MWRT_Patched\0' in ours and b'MWRT_Runtime\0' in ours


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
    assert report_status([PATCHED, FAILED]) == ('Patch Failed', CLR['red'])
    assert report_status([PATCHED, SKIPPED]) == ('Patched Successfully',
                                                 CLR['green'])
    assert report_status([SKIPPED]) == ('Nothing Patched', CLR['yellow'])


def test_archive_belongs_to_its_plugin():
    """`X.bsa` and `X - Part.bsa` both load with plugin X."""
    ranks = {'skyui_se': 40, 'skyrim': 0}
    assert _archive_rank('SkyUI_SE.bsa', ranks) == 40
    assert _archive_rank('Skyrim - Interface.bsa', ranks) == 0
    assert _archive_rank('Loose Textures.bsa', ranks) == -1
