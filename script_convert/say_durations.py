"""Real spoken-line durations for converted `Say`/`SayTo` timers.

TES4's `Say`/`SayTo` RETURNED the voice line's length in seconds, and every
Oblivion polling conversation stores that in a timer it counts down before
speaking the next line:

    set timer to SayTo player, CharGenTaunt2 1
    ...
    if timer > 0
        set timer to timer - getSecondsPassed

Papyrus `Say()` returns nothing, so the converter has to supply a number.  A
flat constant is not good enough: Valen Dreth's CharacterGen taunts run 8-14.5
seconds, and a 3-second stand-in made the script re-issue `Say` while the
previous line was still playing.  Skyrim drops a `Say` on an actor who is
already talking, so the line restarted forever, its End fragment never ran, and
the `tauntCount` that selects the NEXT taunt never incremented — the tutorial
sat on one repeating line.

The durations are knowable: the exported Oblivion voice files are the very
audio the converted plugin ships.  This module walks the MP3 frame headers (no
external dependency) and reports, per dialogue TOPIC, the longest response —
the safe timer value, since the topic's conditions decide at runtime which
response actually plays.

Durations are cached to `<export>/voice_durations.json` because a full scan is
~39k files.
"""
import json
import os
import re
from asset_convert.audio.mp3_length import mp3_duration
from output_layout import assets_for
from concurrent.futures import ThreadPoolExecutor

# Oblivion voice filenames: <quest>_<topic>_<infofid>_<n>.mp3
_VOICE_NAME_RE = re.compile(
    r'^(?P<quest>.+?)_(?P<topic>.+?)_(?P<fid>[0-9a-fA-F]{8})'
    r'_(?P<resp>\d+)\.mp3$')

CACHE_NAME = 'voice_durations.json'


def scan_voice_durations(export_dir: str, use_cache: bool = True,
                         workers: int = None) -> dict:
    """topic (lowercase) -> longest response duration in seconds.

    Also keyed by `<quest>_<topic>` so a caller can disambiguate a topic name
    reused across quests, and by `info:<FID>` (uppercase 8-hex) for the exact
    per-response length.  The per-INFO entries are what an End fragment uses to
    clear a conversation timer precisely; the per-topic maximum is only the
    fallback for a `Say()` whose response cannot be known statically.
    """
    cache_path = os.path.join(export_dir, CACHE_NAME)
    voice_root = str(assets_for(export_dir) / 'sound' / 'voice')
    if not os.path.isdir(voice_root):
        return {}
    if use_cache and os.path.isfile(cache_path):
        try:
            with open(cache_path, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            pass

    files = []
    for root, _dirs, names in os.walk(voice_root):
        for nm in names:
            if nm.lower().endswith('.mp3'):
                files.append(os.path.join(root, nm))
    if not files:
        return {}

    if workers is None:
        workers = max(1, (os.cpu_count() or 2) - 1)

    # Pure file I/O + a tight byte scan: a thread pool is the right tool.
    durations = {}
    # (info key, voice-type dir) -> summed length of that voice type's
    # responses for the INFO. Collapsed to the per-INFO maximum below.
    per_voice: dict = {}
    # info key -> (topic key, quest_topic key), so the per-topic maximum can be
    # taken over whole LINES once the per-INFO sums are known.
    topic_keys: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for path, secs in zip(files, ex.map(mp3_duration, files)):
            if secs <= 0:
                continue
            m = _VOICE_NAME_RE.match(os.path.basename(path))
            if not m:
                continue
            topic = m.group('topic').lower()
            quest = m.group('quest').lower()
            # Per-topic maximum (the static fallback a Say() call site
            # charges).  Computed from whole LINES below, not from individual
            # response files: the call site cannot know which INFO the engine
            # will pick, so its charge must cover the longest LINE under the
            # topic — and a line is all of its responses played back to back.
            topic_keys.setdefault(f"info:{m.group('fid').upper()}",
                                  (topic, f'{quest}_{topic}'))
            # ... and the exact length of the whole LINE.
            #
            # 🛑 An INFO's responses play BACK TO BACK as one uninterruptible
            # line, so the line's length is their SUM, not the longest one.
            # Taking the max under-charged every multi-response INFO, so the
            # owning script's `timer <= 0` gate reopened mid-line and the
            # poller re-Said over the still-playing line — the engine drops a
            # Say on an actor already talking, so the REMAINING RESPONSES ARE
            # NEVER HEARD and the End fragment never advances the counter.
            # Measured in game 2026-08-15: Uriel Septim's CharacterGen
            # greeting (00032469) is 5.51 + 1.59 + 5.51 = 12.62s but was
            # charged 5.51 — he spoke "They cannot understand why I trust
            # you" and "How can I explain?" then stopped, never reaching
            # "...You know the Nine?", so the quest never left stage 42.
            #
            # Each response is recorded once PER VOICE TYPE, so sum within a
            # voice type (keyed by the file's directory) and keep the longest
            # such total — performances differ by a few frames and the longest
            # is the safe charge.
            ikey = f"info:{m.group('fid').upper()}"
            vkey = (ikey, os.path.dirname(path))
            per_voice[vkey] = per_voice.get(vkey, 0.0) + secs

    # Collapse the per-voice-type sums to one length per INFO.
    for (ikey, _vdir), total in per_voice.items():
        if total > durations.get(ikey, 0.0):
            durations[ikey] = round(total, 2)

    # Then take each topic's maximum over those whole-line lengths.
    for ikey, keys in topic_keys.items():
        line = durations.get(ikey, 0.0)
        for key in keys:
            if line > durations.get(key, 0.0):
                durations[key] = round(line, 2)

    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(durations, f, indent=0, sort_keys=True)
    except OSError:
        pass
    return durations
