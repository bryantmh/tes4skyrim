"""Patch a quest journal movie so a clicked objective shows its stage's text.

Recognises two journal UIs by CONTENT, never by filename:

- the `QuestsPage` class of vanilla's and SkyUI's `quest_journal.swf`, whose
  `onObjectiveListSelect` does nothing outside the Miscellaneous view;
- the `QuestJournal` class of Quest Journal Overhaul's `questjournal.swf`,
  whose objective rows take no clicks at all.

Nothing already in the movie is rewritten. One DoAction is appended to frame
1, after every class definition, and it replaces the class methods by name --
the lists dispatch clicks to `scope[callbackName]`, so a replaced method is
what they call. The text comes from
`_root.MWRT_Runtime.GetObjectiveLog(formID, instance, row)`, an object
MorrowindRuntime gives every open movie carrying `_root.MWRT_Patched` -- NOT
SKSE's `skse.plugins`, which only movies the engine's own LoadMovie loaded
get, and Quest Journal Overhaul loads its own. "" (or no runtime at all)
leaves the journal behaving exactly as it did.

See: docs/commentary/morrowind_runtime.md#journal-stage-text
"""

from asset_convert.ui.avm1 import UNDEFINED, Register, assemble
from asset_convert.ui.swf import (TAG_DO_ACTION, TAG_DO_INIT_ACTION,
                                  TAG_SHOW_FRAME, Swf, Tag)

#: Pushed and popped first thing, so a re-patch can find and replace our tag.
MARKER = 'MorrowindRuntimeJournalPatch'

#: Root flag asking the runtime for its functions (ids.h kJournalPatchMarker).
PATCHED_FLAG = 'MWRT_Patched'

#: Where the runtime puts them (ids.h kJournalFunctionsPath).
RUNTIME = ('_root', 'MWRT_Runtime')

#: Journal UI kinds, each keyed by the class-pool strings that identify it.
KIND_QUESTS_PAGE = 'QuestsPage'
KIND_QJO = 'QuestJournal'
_SIGNATURES = (
    (KIND_QUESTS_PAGE, (b'QuestsPage\0', b'onObjectiveListSelect\0',
                        b'SetDescriptionText\0', b'ObjectiveList\0')),
    (KIND_QJO, (b'QuestJournal\0', b'SetObjectives\0',
                b'ObjectivesList_mc\0', b'QuestDescription\0')),
)


# ---------------------------------------------------------------------------
#  Instruction helpers
# ---------------------------------------------------------------------------

def _get(*path) -> list:
    """Push the value at a variable, then down its members."""
    code = [('push', path[0]), 'GetVariable']
    for name in path[1:]:
        code += [('push', name), 'GetMember']
    return code


def _call(target: tuple, method: str, args: list) -> list:
    """Call `target.method(*args)`; each arg is code pushing one value."""
    code = []
    for arg in reversed(args):
        code += arg
    return code + [('push', len(args))] + _get(*target) + [
        ('push', method), 'CallMethod']


def _set(target: tuple, name: str, value: list) -> list:
    """`target.name = value`."""
    return _get(*target) + [('push', name)] + value + ['SetMember']


def _local(name: str, value: list) -> list:
    """`var name = value` inside a function."""
    return [('push', name)] + value + ['DefineLocal']


def _assign(name: str, value: list) -> list:
    """`name = value`, to a local if one is defined, else the timeline."""
    return [('push', name)] + value + ['SetVariable']


def _objective_log(form_id: list, instance: list, row: list) -> list:
    """`_root.MWRT_Runtime.GetObjectiveLog(form, instance, row)`."""
    return _call(RUNTIME, 'GetObjectiveLog',
                 [form_id + ['ToNumber'], instance + ['ToNumber'], row])


def _trace(*values: list) -> list:
    """`_root.MWRT_Runtime.JournalTrace(...)`, one runtime log line."""
    return _call(RUNTIME, 'JournalTrace', list(values)) + ['Pop']


def _text(value: str) -> list:
    """Code pushing a string literal."""
    return [('push', value)]


def _fetch_unless_shown(shown: list, row: list) -> list:
    """`var text = ""`, then fetch `row` of quest `q` unless `shown` -- what
    identifies the clicked row -- is the row already on screen."""
    return (_local('text', [('push', '')])
            + _get('this', 'MWRT_Shown') + shown
            + ['StrictEquals', ('if', 'fetched')]
            + _assign('text', _objective_log(_get('q', 'formID'),
                                             _get('q', 'instance'), row))
            + [('label', 'fetched')])


def _return() -> list:
    """`return;`"""
    return [('push', UNDEFINED), 'Return']


# ---------------------------------------------------------------------------
#  Vanilla and SkyUI: QuestsPage
# ---------------------------------------------------------------------------

def _row_of_selected_objective() -> list:
    """`var i` = the clicked objective's index in the quest's objectives."""
    return (_local('i', [('push', 0)])
            + [('label', 'loop')]
            + _get('i') + _get('q', 'objectives', 'length')
            + ['Less2', 'Not', ('if', 'found')]
            + _get('q', 'objectives') + _get('i') + ['GetMember']
            + _get('o') + ['StrictEquals', ('if', 'found')]
            + _assign('i', _get('i') + ['Increment'])
            + [('jump', 'loop'), ('label', 'found')])


def _quests_page_select() -> list:
    """onObjectiveListSelect: show the clicked row's text, or put it back.

    A second click on the shown row restores the quest's current text. With no
    text for the row the original handler runs, which is the Miscellaneous
    view's set-active toggle.
    """
    return (_local('q', _get('this', 'TitleList', 'selectedEntry'))
            + _local('o', _get('this', 'ObjectiveList', 'selectedEntry'))
            + _row_of_selected_objective()
            + _trace(_text('QuestsPage click'), _get('i'),
                     _get('q', 'formID'), _get('q', 'instance'))
            + _fetch_unless_shown(_get('o'), _get('i'))
            + _get('text') + ['Not', ('if', 'restore')]
            + _local('d', _get('q', 'description'))
            + _set(('q',), 'description', _get('text'))
            + _call(('this',), 'MWRT_SetDescription', []) + ['Pop']
            + _set(('q',), 'description', _get('d'))
            + _set(('this',), 'MWRT_Shown', _get('o'))
            + _return()
            + [('label', 'restore')]
            + _get('this', 'MWRT_Shown') + [('push', UNDEFINED),
                                             'StrictEquals', ('if', 'original')]
            + _call(('this',), 'SetDescriptionText', []) + ['Pop']
            + [('label', 'original')]
            + _call(('this',), 'MWRT_Select', []) + ['Pop'])


def _quests_page_set_description() -> list:
    """SetDescriptionText: the original, and nothing is shown from a row."""
    return (_set(('this',), 'MWRT_Shown', [('push', UNDEFINED)])
            + _call(('this',), 'MWRT_SetDescription', []) + ['Pop'])


def quests_page_program() -> list:
    """Replace QuestsPage's objective click and description setter."""
    page = ('MWRT_QuestsPage',)
    return (_assign(page[0], _get('_global', 'QuestsPage', 'prototype'))
            + _set(page, 'MWRT_Select', _get(*page, 'onObjectiveListSelect'))
            + _set(page, 'MWRT_SetDescription',
                   _get(*page, 'SetDescriptionText'))
            + _set(page, 'onObjectiveListSelect',
                   [('function', (), _quests_page_select())])
            + _set(page, 'SetDescriptionText',
                   [('function', (), _quests_page_set_description())]))


# ---------------------------------------------------------------------------
#  Quest Journal Overhaul: QuestJournal
# ---------------------------------------------------------------------------

def _qjo_set_objectives() -> list:
    """SetObjectives(quest): the original, then a click handler on each row.

    QJO names each row clip `"objective" + index` into `quest.objectives`, so
    the index is the row the runtime is asked for.
    """
    return (_call(('this',), 'MWRT_SetObjectives', [_get('quest')]) + ['Pop']
            + _trace(_text('QuestJournal SetObjectives'),
                     _get('quest', 'formID'), _get('quest', 'instance'),
                     _get('quest', 'objectives', 'length'))
            + _set(('this',), 'MWRT_Quest', _get('quest'))
            + _set(('this',), 'MWRT_Shown', [('push', UNDEFINED)])
            + _local('list', _get('this', 'ObjectivesList_mc'))
            + _get('quest', 'objectives')
            + ['Enumerate2', ('label', 'next'), ('store', 0), ('push', None),
               'Equals2', ('if', 'done')]
            + _local('k', [('push', Register(0))])
            + _local('clip', _get('list') + [('push', 'objective')]
                     + _get('k') + ['Add2', 'GetMember'])
            + _set(('clip',), 'MWRT_Row', _get('k') + ['ToNumber'])
            + _set(('clip',), 'MWRT_Page', _get('this'))
            + _set(('clip',), 'onPress', _get('this', 'MWRT_Press'))
            + [('jump', 'next'), ('label', 'done')])


def _qjo_show_row() -> list:
    """MWRT_ShowRow(row): the row's text, or the quest's own on a second click."""
    description = ('this', 'QuestDescription', 'textField')
    return (_local('q', _get('this', 'MWRT_Quest'))
            + _trace(_text('QuestJournal click'), _get('row'),
                     _get('q', 'formID'), _get('q', 'instance'))
            + _fetch_unless_shown(_get('row'), _get('row'))
            + _get('text') + ['Not', ('if', 'restore')]
            + _set(description, 'text', _get('text'))
            + _set(('this',), 'MWRT_Shown', _get('row'))
            + _return()
            + [('label', 'restore')]
            + _set(description, 'text', _get('q', 'description'))
            + _set(('this',), 'MWRT_Shown', [('push', UNDEFINED)]))


def qjo_program() -> list:
    """Give QuestJournal's objective rows a click that shows their text."""
    page = ('MWRT_QuestJournal',)
    press = (_call(('this', 'MWRT_Page'), 'MWRT_ShowRow',
                   [_get('this', 'MWRT_Row')]) + ['Pop'])
    return (_assign(page[0], _get('_global', 'QuestJournal', 'prototype'))
            + _set(page, 'MWRT_SetObjectives', _get(*page, 'SetObjectives'))
            + _set(page, 'MWRT_Press', [('function', (), press)])
            + _set(page, 'MWRT_ShowRow', [('function', ('row',), _qjo_show_row())])
            + _set(page, 'SetObjectives',
                   [('function', ('quest',), _qjo_set_objectives())]))


# ---------------------------------------------------------------------------
#  The movie
# ---------------------------------------------------------------------------

_PROGRAMS = ((KIND_QUESTS_PAGE, quests_page_program),
             (KIND_QJO, qjo_program))


def journal_kinds(swf: Swf) -> list:
    """The journal UIs this movie's class definitions contain."""
    classes = b''.join(t.data for t in swf.tags if t.code == TAG_DO_INIT_ACTION)
    return [kind for kind, needles in _SIGNATURES
            if all(n in classes for n in needles)]


def _is_ours(tag: Tag) -> bool:
    """Whether a tag is a DoAction this module added."""
    return tag.code == TAG_DO_ACTION and MARKER.encode() + b'\0' in tag.data


def is_patched(raw: bytes) -> bool:
    """Whether a movie already carries this patch."""
    return any(_is_ours(t) for t in Swf.parse(raw).tags)


def patch_movie(raw: bytes) -> tuple:
    """(patched bytes, kinds) for a journal movie, or (None, []) for any other.

    Idempotent: a previous patch is removed before the current one is added.
    """
    swf = Swf.parse(raw)
    kinds = journal_kinds(swf)
    if not kinds:
        return None, []
    swf.tags = [t for t in swf.tags if not _is_ours(t)]
    program = [('push', MARKER), 'Pop'] + _assign(PATCHED_FLAG, [('push', True)])
    for kind, build in _PROGRAMS:
        if kind in kinds:
            program += build()
    first_frame_end = next(i for i, t in enumerate(swf.tags)
                           if t.code == TAG_SHOW_FRAME)
    swf.tags.insert(first_frame_end, Tag(TAG_DO_ACTION, assemble(program)))
    return swf.serialize(compress=raw[:3] != b'FWS'), kinds
