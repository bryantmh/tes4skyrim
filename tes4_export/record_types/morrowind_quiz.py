"""
Morrowind's class quiz, as the install's Morrowind.ini states it.

The game reads `[Question 1]` .. `[Question 10]` from Morrowind.ini, not from a
plugin; OpenMW imports them as its `Question_<n>_*` fallback settings and plays
each `Sound` as the question is asked. The ini sits beside the install's
`Data Files`, so only a masterless plugin's export carries it. Each question is
dumped as an `MWQZ` record, and each recording becomes a mouthless `Say` sound
the runtime plays.

See: docs/commentary/tes4_export_morrowind.md#class-quiz
"""

import os
import re

from ..record_types.common import escape_value
from .morrowind_say import say_sound

#: The export type of one quiz question.
QUIZ_SIG = 'MWQZ'

#: The keys of one `[Question <n>]` section, as Morrowind.ini spells them.
QUIZ_KEYS = ('Question', 'AnswerOne', 'AnswerTwo', 'AnswerThree', 'Sound')

_SECTION = re.compile(r'^\[Question (\d+)\]$', re.IGNORECASE)


def ini_path(source_path: str) -> str:
    """The Morrowind.ini of the install `source_path` sits in, beside its `Data Files`."""
    data = os.path.dirname(os.path.abspath(source_path))
    return os.path.join(os.path.dirname(data), 'Morrowind.ini')


def read_quiz(path: str, codec: str) -> dict:
    """{question number: {key: value}} from the ini's `[Question <n>]` sections."""
    quiz, current = {}, None
    if not os.path.isfile(path):
        return quiz
    with open(path, encoding=codec, errors='replace') as handle:
        for line in handle:
            text = line.strip()
            found = _SECTION.match(text)
            if found or text.startswith('['):
                current = quiz.setdefault(int(found.group(1)), {}) if found else None
                continue
            key, equals, value = text.partition('=')
            if current is not None and equals and key in QUIZ_KEYS:
                current[key] = value
    return quiz


def quiz_records(source_path: str, codec: str, ctx) -> dict:
    """{'MWQZ', 'SOUN'} records of the install's class quiz; nothing for a plugin with masters."""
    out = {QUIZ_SIG: [], 'SOUN': []}
    if ctx.master_names:
        return out
    for number, fields in sorted(read_quiz(ini_path(source_path), codec).items()):
        out[QUIZ_SIG].append((ctx.derive(f'chargenquiz:{number}'),
                              [f'EditorID=MWQuiz{number}', f'Number={number}']
                              + [f'{key}={escape_value(fields.get(key, ""))}' for key in QUIZ_KEYS]))
        sound = fields.get('Sound', '').replace('/', chr(92))
        if sound:
            out['SOUN'].append(say_sound(sound.lower(), sound, ctx))
    return out
