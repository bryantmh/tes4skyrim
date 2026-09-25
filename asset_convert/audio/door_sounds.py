"""Open/close sound names authored inside an Oblivion door NIF.

Oblivion drives a door's audio from TWO places and uses whichever the author
filled in:

  * the DOOR record's SNAM/ANAM/BNAM (open/close/loop SOUN), and
  * `sound: <SOUN EditorID>` text keys on the model's own `Open` / `Close`
    `NiControllerSequence`.

Skyrim supports BOTH — the Gamebryo text-key handler at `0x1401db723` (GOG
SkyrimSE.exe) matches the literal `"Sound: "` case-insensitively (`_strnicmp`,
7 chars) and plays the rest of the key, so the converted mesh keeps its
Oblivion keys verbatim: a door gets no behaviour graph, and only graph-driven
meshes have their keys rewritten (nif_passes.graph_sound_text_keys).

This module supplies the RECORD half as well, because that is what vanilla
Skyrim relies on: all 90 sounded DOOR records in Skyrim.esm carry SNAM/ANAM,
and no vanilla door uses a text key for its sound.  Lifting the mesh's
authored names onto SNAM/ANAM makes the converted door match vanilla's own
mechanism instead of depending solely on the mesh channel.

`StoneWallGateDoor01.NIF` is the canonical case: its DOOR records
(StoneWallGateDoor01, KvatchWallGateDoor01, TestGateDoor01, SE32WallGateDoor01)
have no SNAM/ANAM at all, and the gate's iron creak is entirely in the NIF
(`sound: DRSMetalOpen02` / `sound: DRSMetalClose02`).

The sequence NAME is what says which slot a key belongs to, so a raw byte scan
for `sound:` is not enough — the file has to be parsed to keep each key with
its sequence.  Sequence names are Oblivion's engine-fixed animation groups, so
the mapping is exact, not a guess.
"""

import os
import re

# Text key form Oblivion uses: `sound: <SOUN EditorID>`.  Matched
# case-insensitively with optional whitespace, exactly as
# nif_converter._TES4_SOUND_KEY does when it rewrites these to Skyrim's
# `SoundPlay.` form — the two must agree on what counts as a sound key.
_TES4_SOUND_KEY = re.compile(rb'^sound:\s*(\S+)\s*$', re.IGNORECASE)

# NiControllerSequence name -> the TES5 DOOR subrecord its sound belongs in.
# Oblivion's door animation groups are engine-fixed names (the same ones the
# TES4 engine plays on activate), so this is a closed set.
_SEQ_TO_SLOT = {
    b'open': 'open',
    b'close': 'close',
    b'loop': 'loop',
    b'loopopen': 'loop',
}


def _sequence_sound_keys(data):
    """{slot: SOUN EditorID} for every Open/Close/Loop sequence in *data*.

    Reads the sequence's own NiTextKeyExtraData, so a key can never be
    attributed to the wrong slot.  An unrecognised sequence name is ignored
    rather than guessed at.
    """
    from pyffi.formats.nif import NifFormat

    found = {}
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiControllerSequence):
                continue
            slot = _SEQ_TO_SLOT.get(bytes(block.name).rstrip(b'\x00').lower())
            if slot is None or slot in found:
                continue
            tk = getattr(block, 'text_keys', None)
            if tk is None:
                continue
            for key in tk.text_keys:
                m = _TES4_SOUND_KEY.match(bytes(key.value).rstrip(b'\x00'))
                if m:
                    found[slot] = m.group(1).decode('ascii', 'replace')
                    break
    return found


def door_sounds_from_nif(nif_path):
    """{'open'/'close'/'loop': SOUN EditorID} authored in a door NIF.

    Empty dict when the model animates without sound keys (or does not
    animate at all); raises on read errors, which the caller reports.
    """
    import time
    if not hasattr(time, 'clock'):
        time.clock = time.perf_counter  # PyFFI 2.2.3 uses the removed time.clock
    from pyffi.formats.nif import NifFormat

    data = NifFormat.Data()
    with open(nif_path, 'rb') as fh:
        data.inspect(fh)
        data.read(fh)
    return _sequence_sound_keys(data)


def door_sounds_job(args):
    """(key, sounds, error) for one NIF — module level so it pickles for a
    ProcessPoolExecutor (PyFFI parsing is CPU-bound pure Python)."""
    key, nif_path = args
    try:
        return key, door_sounds_from_nif(nif_path), None
    except Exception as exc:
        return key, None, f'{type(exc).__name__}: {exc}'


def has_sound_key(path):
    """True if the file contains a `sound:` text key anywhere.

    Text-key VALUES are plaintext in the NIF body, so this cheap substring
    test rejects the great majority of door models without a PyFFI parse.
    Only a candidate that passes is parsed properly (a match here says
    nothing about WHICH sequence owns the key).
    """
    try:
        with open(path, 'rb') as fh:
            blob = fh.read()
    except OSError:
        return False
    return b'sound:' in blob.lower()


def scan_door_models(meshes_dir, model_keys):
    """{model key: {slot: SOUN EditorID}} for the given door models.

    *model_keys* are lowercase forward-slash paths relative to *meshes_dir*
    (e.g. ``architecture/stonewall/stonewallgatedoor01.nif``) — only door
    models are handed in, so this never walks the whole mesh tree.
    """
    jobs = []
    for key in sorted(model_keys):
        path = os.path.join(meshes_dir, key.replace('/', os.sep))
        if os.path.isfile(path) and has_sound_key(path):
            jobs.append((key, path))

    out = {}
    errors = []
    if not jobs:
        return out, errors

    if len(jobs) < 8:
        results = map(door_sounds_job, jobs)
    else:
        from concurrent.futures import ProcessPoolExecutor
        from core.worker_budget import worker_count
        workers = min(worker_count(), len(jobs))
        ex = ProcessPoolExecutor(max_workers=workers)
        try:
            results = list(ex.map(door_sounds_job, jobs))
        finally:
            ex.shutdown()

    for key, sounds, err in results:
        if err is not None:
            errors.append((key, err))
        elif sounds:
            out[key] = sounds
    return out, errors
