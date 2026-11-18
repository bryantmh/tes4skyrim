"""Morrowind's class quiz: Morrowind.ini's questions dumped by the export, staged for the runtime."""
from tes4_export.record_types import morrowind_quiz
from tes5_import.actors.attribute_tables import quiz_lines
from tes5_import.base.owned_records import BUTTON_LINE, message_record
from tes5_import.base.tes5_reader import subrecords


def test_a_long_button_is_wrapped_so_the_box_fits_the_screen():
    """Skyrim sets a box's buttons side by side; a long answer runs off the screen unwrapped."""
    answer = 'Do not interfere in the natural evolution of events, but rather take the opportunity'
    record = message_record(0x800, 'TES4Msg_Quiz_01', 'On a clear day?', [answer, 'No'])
    texts = [data.rstrip(b'\0').decode() for tag, data in subrecords(record[24:]) if tag == b'ITXT']
    assert texts[1] == 'No'
    assert all(len(line) <= BUTTON_LINE for line in texts[0].split('\n'))
    assert texts[0].replace('\n', ' ') == answer

_INI = ('[General]\nQuestion=not a quiz\n[Question 2]\nQuestion=Second?\nAnswerOne=Fight\nAnswerTwo=Cast\n'
        'AnswerThree=Sneak\nSound=Vo\\Misc\\CharGen QA2.wav\n[Question 1]\nQuestion=First?\nAnswerOne=A\n'
        'AnswerTwo=B\nAnswerThree=C|D\nSound=Vo\\Misc\\CharGen QA1.wav\n[Fonts]\nAnswerOne=stray\n')


class _Ctx:
    """The export context's two members the quiz reads."""

    def __init__(self, masters):
        """A plugin with these masters."""
        self.master_names = masters

    def derive(self, key):
        """A stand-in FormID per key."""
        return '%08X' % (abs(hash(key)) & 0xFFFFFF)


def _install(tmp_path):
    """A plugin path whose install holds the ini beside `Data Files`."""
    data = tmp_path / 'Data Files'
    data.mkdir()
    (tmp_path / 'Morrowind.ini').write_text(_INI, encoding='cp1252')
    return str(data / 'Morrowind.esm')


def test_only_question_sections_are_read(tmp_path):
    """Other sections' keys never leak into a question."""
    quiz = morrowind_quiz.read_quiz(morrowind_quiz.ini_path(_install(tmp_path)), 'cp1252')
    assert sorted(quiz) == [1, 2]
    assert quiz[2] == {'Question': 'Second?', 'AnswerOne': 'Fight', 'AnswerTwo': 'Cast',
                       'AnswerThree': 'Sneak', 'Sound': 'Vo\\Misc\\CharGen QA2.wav'}


def test_a_masterless_plugin_dumps_each_question_and_its_sound(tmp_path):
    """One MWQZ per question and one mouthless Say SOUN per recording."""
    out = morrowind_quiz.quiz_records(_install(tmp_path), 'cp1252', _Ctx([]))
    assert [lines[1] for _fid, lines in out['MWQZ']] == ['Number=1', 'Number=2']
    assert any('FNAM.Filename=Vo\\\\Misc\\\\CharGen QA1.wav' in line for line in out['SOUN'][0][1])


def test_a_plugin_with_masters_dumps_nothing(tmp_path):
    """The ini is the game's, so only its masterless plugin carries it."""
    out = morrowind_quiz.quiz_records(_install(tmp_path), 'cp1252', _Ctx(['Morrowind.esm']))
    assert out == {'MWQZ': [], 'SOUN': []}


def test_the_sidecar_rows_are_in_question_order_with_a_say_key(tmp_path):
    """`quiz.<i>=question|one|two|three|sound`; a `|` in the text cannot split a field."""
    out = morrowind_quiz.quiz_records(_install(tmp_path), 'cp1252', _Ctx([]))
    text = ''.join('---RECORD_BEGIN---\nSignature=MWQZ\nFormID=%s\n%s\n---RECORD_END---\n\n' % (fid, '\n'.join(lines))
                   for fid, lines in reversed(out['MWQZ']))
    (tmp_path / 'MWQZ.txt').write_text(text, encoding='utf-8')
    rows = quiz_lines(str(tmp_path))
    assert rows[0].startswith('quiz.0=First?|A|B|C/D|vo')
    assert rows[0].endswith('chargen qa1.wav') and rows[1].startswith('quiz.1=Second?')
