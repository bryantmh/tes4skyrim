"""The length of an MP3, read from its frame headers with no decoder.

Frames are walked rather than a header trusted: the voice files are CBR but
carry ID3 tags and stray bytes between frames, and ship no Xing/Info count.

See: docs/commentary/script_convert.md#polled-conversations
"""

#: Layer III bitrates in kbps by header index, for MPEG-1 and for MPEG-2/2.5; 0 marks an invalid index.
_BITRATES_V1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224,
                   256, 320, 0]
_BITRATES_V2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144,
                   160, 0]

#: Sample rates by the header's version field: 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5.
_SAMPLE_RATES = {3: [44100, 48000, 32000],
                 2: [22050, 24000, 16000],
                 0: [11025, 12000, 8000]}

#: The header's version value for MPEG-1, and its layer value for Layer III.
_MPEG1, _LAYER3 = 3, 1


def _id3_size(data: bytes) -> int:
    """How many leading bytes an ID3v2 tag occupies, 0 without one."""
    if data[:3] != b'ID3' or len(data) < 10:
        return 0
    return 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14
                 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))


def _frame(data: bytes, p: int) -> tuple:
    """`(byte length, seconds)` of the Layer III frame at `p`, or (0, 0.0)."""
    if data[p] != 0xFF or (data[p + 1] & 0xE0) != 0xE0:
        return 0, 0.0
    version = (data[p + 1] >> 3) & 3
    rates = _SAMPLE_RATES.get(version)
    rate_index = (data[p + 2] >> 2) & 3
    if ((data[p + 1] >> 1) & 3) != _LAYER3 or not rates or rate_index == 3:
        return 0, 0.0
    table = _BITRATES_V1_L3 if version == _MPEG1 else _BITRATES_V2_L3
    bitrate = table[(data[p + 2] >> 4) & 0xF] * 1000
    if not bitrate:
        return 0, 0.0
    samples = 1152 if version == _MPEG1 else 576
    rate = rates[rate_index]
    padding = (data[p + 2] >> 1) & 1
    return int((samples / 8 * bitrate) / rate) + padding, samples / rate


def mp3_duration(path: str) -> float:
    """Duration in seconds by summing MPEG frame durations; 0.0 if unreadable."""
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError:
        return 0.0
    p = _id3_size(data)
    total = 0.0
    while p + 4 <= len(data):
        length, seconds = _frame(data, p)
        total += seconds
        p += length or 1
    return total
