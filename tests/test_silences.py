"""The harness's `expect quiet`: silences a player at normal speed hears, measured at any test speed."""
from tools.live import silences


def _line(ms, game, text='a'):
    """One spoken line's result row."""
    return {'speaker': '05000001', 'text': text, 'ms': ms, 'game': game}


def test_a_timer_released_event_counts_game_time_less_the_line():
    """At 10x, a 21.16 s timer on a 9.82 s line leaves 11.3 s of silence."""
    gap = silences.silence(_line(0, 1000), {'menu': 'MessageBoxMenu', 'ms': 2116, 'game': 22160}, 9.82)
    assert round(gap, 2) == 11.34


def test_the_same_gap_reads_the_same_at_real_speed():
    """At 1x the game and real clocks agree, and so does the silence."""
    gap = silences.silence(_line(0, 1000), {'menu': 'MessageBoxMenu', 'ms': 21160, 'game': 22160}, 9.82)
    assert round(gap, 2) == 11.34


def test_a_pause_after_the_audio_is_not_scaled_up():
    """0.3 real s after a line's audio is 3 game s at 10x; the smaller counts."""
    gap = silences.silence(_line(0, 1000), _line(10120, 102200, 'b'), 9.82)
    assert round(gap, 2) == 0.3


def test_the_next_response_of_the_same_info_is_not_measured(tmp_path):
    """The engine plays an INFO's responses back to back; only a script can open a gap."""
    (tmp_path / 'INFO.txt').write_text(
        'FormID=0002AD84\nResponse[0].ResponseNumber=1\nResponse[0].ResponseText=Let me see your face...\n'
        'Response[1].ResponseNumber=2\nResponse[1].ResponseText=You are the one from my dreams...\n')
    quiet = silences.Silences(silences.Lengths([str(tmp_path)]))
    quiet.note(_line(0, 1000, 'Let me see your face...'))
    quiet.note(_line(170, 4600, 'You are the one from my dreams...'))
    assert quiet.judge('quiet <= 2') == '1 0.00'


def test_a_missing_start_line_does_not_pass():
    """`since <text>` with no such line judges -1, never a silent pass."""
    quiet = silences.Silences(silences.Lengths([]))
    quiet.note(_line(0, 1000, 'Stay put, prisoner.'))
    assert quiet.judge('quiet <= 2 since seen you') == '-1 null'
