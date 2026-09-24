"""SOUN conversion: sound descriptors (SNDR), output models (SOPM), assets.

Split out of the former dialog_misc.py, which held SOUN and WTHR together and
no dialogue at all.
"""

import os
import struct

from ..base.tes5_reader import REC_HDR, decompress, first_sub
from .common import (
    prefix_path,
    get_formid,
    get_int,
    get_str,
    pack_formid_subrecord,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint32_subrecord,
)


#: TES4 SNDX/SNDD flag bits (xEdit wbDefinitionsTES4, SOUN).
_TES4_SND_RANDOM_FREQ_SHIFT = 0x0001
_TES4_SND_LOOP              = 0x0010
_TES4_SND_MENU_SOUND        = 0x0020
_TES4_SND_2D                = 0x0040


#: SOMDialogue2D: non-attenuating, menu/2D only. See: docs/commentary/tes5_import_sound.md#sopm-defaults
_SOPM_2D = 0x000B5183

#: Falloff for a 3D sound whose TES4 record authored no max attenuation distance.
_DEFAULT_3D_MAX_DIST = 1800.0
_SOPM_ONAM_CHANNELS = bytes.fromhex(
    '646400003232323264000000640064000064000000640064')
#: ANAM lead: unknown[4] before minDistance(f32) maxDistance(f32) curve[5] unknown[3].
_SOPM_ANAM_LEAD = bytes.fromhex('809dfa00')
_SOPM_ANAM_TAIL = b'\x00\x00\x00'
#: Standard vanilla falloff curve, shared by every SOMMono*/SOMStereoRad* model.
_SOPM_CURVE = bytes((100, 50, 20, 5, 0))


def _build_sopm(writer, min_dist: float, max_dist: float, stereo: bool) -> int:
    """Get-or-create a Sound Output Model with the given attenuation distances.

    Skyrim does not store falloff distances on the sound itself — they live in
    the SOPM the SNDR's ONAM points at (vanilla ships one per distance:
    SOMMono00400, SOMMono03000, SOMMono10000, ...).  Oblivion instead stores the
    distances per-SOUN in SNDX, so we mint a SOPM per distinct distance pair and
    cache it, rather than pinning every sound to a single model.

    Returns the SOPM FormID.
    """
    cache = getattr(writer, '_sopm_cache', None)
    if cache is None:
        cache = writer._sopm_cache = {}
    key = (round(min_dist), round(max_dist), stereo)
    if key in cache:
        return cache[key]

    fid = writer.derive_formid('SOPM', key)
    kind = 'Stereo' if stereo else 'Mono'
    subs = pack_string_subrecord(
        'EDID', f'TES4_SOM{kind}{round(max_dist):05d}_{round(min_dist):05d}')
    subs += pack_subrecord('NAM1', struct.pack('<BHB', 0x01, 0, 30))
    subs += pack_uint32_subrecord('MNAM', 1 if stereo else 0)
    if stereo:
        subs += pack_subrecord('ONAM', _SOPM_ONAM_CHANNELS)
    subs += pack_subrecord('ANAM', _SOPM_ANAM_LEAD
                           + struct.pack('<ff', min_dist, max_dist)
                           + _SOPM_CURVE + _SOPM_ANAM_TAIL)
    writer.add_record('SOPM', pack_record('SOPM', fid, 0, subs))
    cache[key] = fid
    return fid


#: Extracted-asset root for the plugin being imported; set by set_sound_source_dir().
_SOUND_SOURCE_DIR = None

#: Audio containers Skyrim SE plays natively, in convert_sounds' copy order.
_AUDIO_EXTS = ('.wav', '.xwm', '.mp3')


def set_sound_source_dir(asset_root: str) -> None:
    """Point the SOUN converter at this plugin's ASSET root, not its record dir."""
    global _SOUND_SOURCE_DIR
    _SOUND_SOURCE_DIR = asset_root


#: TES4 SOUN FormID (low 24) -> its SNDR; filled during Phase 3. See: docs/commentary/tes5_import_sound.md#sndr-map-phase-3

_SNDR_FOR_SOUN = {}


def reset_sound_descriptors() -> None:
    """Clear the SOUN→SNDR map at the start of an import run."""
    _SNDR_FOR_SOUN.clear()


def record_sndr_for_soun(soun_fid: int, sndr_fid: int) -> None:
    """Note the companion SNDR convert_SOUN just built for a SOUN."""
    _SNDR_FOR_SOUN[soun_fid & 0x00FFFFFF] = sndr_fid


def sndr_map() -> dict:
    """TES4 SOUN id (low 24 bits) → companion SNDR FormID, for CSDI patching."""
    return _SNDR_FOR_SOUN


def get_sndr_for_soun(soun_fid: int) -> int:
    """The SNDR FormID for a TES4 SOUN (low 24 bits keyed), or 0 if it has none."""
    return _SNDR_FOR_SOUN.get(soun_fid & 0x00FFFFFF, 0)


def master_subrecord(master_index, fid: int, sig: bytes, tag: bytes):
    """One subrecord of a master's converted `sig` record, in this plugin's ids; None if absent."""
    blob = master_index.record(fid) if master_index else b''
    if len(blob) < REC_HDR or blob[:4] != sig:
        return None
    flags = struct.unpack_from('<I', blob, 8)[0]
    return first_sub(decompress(blob[REC_HDR:], flags), tag)


def master_sound_descriptor(master_index, soun_fid: int) -> int:
    """A master's SOUN -> the SNDR its SDSC names, 0 for none.

    See: docs/commentary/tes5_import_pipeline.md#sound-slot-patching
    """
    sdsc = master_subrecord(master_index, soun_fid, b'SOUN', b'SDSC')
    return struct.unpack('<I', sdsc)[0] if sdsc and len(sdsc) == 4 else 0


#: TES4 SOUN id (low 24) -> (EditorID, FNAM); loaded up front (incl. the MASTER export) for the weather classifier.
_SOUN_IDENTITY = {}


def reset_soun_identity() -> None:
    """Clear the SOUN identity index at the start of an import run."""
    _SOUN_IDENTITY.clear()


def load_soun_identity(records) -> None:
    """Index (EditorID, filename) for every SOUN record in *records*.

    Safe to call more than once; later calls add without clobbering, so the
    plugin's own SOUNs are loaded after the master's and win on collision.
    """
    for rec in records:
        fid = get_formid(rec, 'FormID')
        if not fid:
            continue
        _SOUN_IDENTITY[fid & 0x00FFFFFF] = (
            get_str(rec, 'EditorID') or '',
            get_str(rec, 'FNAM.Filename') or '',
        )


def get_soun_identity(soun_fid: int) -> tuple:
    """(EditorID, filename) for a TES4 SOUN id, or ('', '') when unknown."""
    return _SOUN_IDENTITY.get(soun_fid & 0x00FFFFFF, ('', ''))


def _shipped_name(name: str) -> str:
    """The on-disk name convert_sounds writes: only .mp3 becomes .wav."""
    stem, ext = os.path.splitext(name)
    return stem + '.wav' if ext.lower() == '.mp3' else name


def _sound_path(name: str) -> str:
    """One SNDR ANAM value: `tes4\\<path>`, relative to `Sound\\`."""
    return prefix_path(_shipped_name(name))


def _sound_anam_paths(filename: str) -> list:
    """The ANAM values for one TES4 SOUN.FNAM, as TES5 sound paths.

    A directory-valued FNAM expands to one ANAM per file, sorted.

    See: docs/commentary/tes5_import_sound.md#anam-paths
    """
    literal = [_sound_path(filename)]
    if os.path.splitext(filename)[1]:
        return literal
    if not _SOUND_SOURCE_DIR:
        return literal

    rel = filename.replace('/', '\\').strip('\\')
    src = os.path.join(_SOUND_SOURCE_DIR, 'sound', *rel.split('\\'))
    try:
        entries = sorted(os.listdir(src))
    except OSError:
        return literal

    variants = [_sound_path(f'{rel}\\{e}') for e in entries
                if e.lower().endswith(_AUDIO_EXTS)]
    return variants or literal


def sndr_editor_id(edid: str, fid: int) -> str:
    """The EditorID of a SOUN's companion SNDR."""
    return f"TES4_{edid}_SNDR" if edid else f"TES4_SOUN_{fid:08X}_SNDR"


def _sndr_record(rec: dict, writer, edid: str, filename: str) -> tuple:
    """Build this SOUN's companion SNDR.  Returns (sndr_bytes, sndr_formid).

    SNDR order: EDID CNAM GNAM SNAM ANAM[] ONAM LNAM BNAM.

    See: docs/commentary/tes5_import_sound.md#sndx-field-scaling
    """
    pfx = 'SNDD' if rec.get('SNDD.MaxAttDist') is not None else 'SNDX'
    tes4_flags = get_int(rec, f'{pfx}.Flags') or 0
    min_dist = (get_int(rec, f'{pfx}.MinAttDist') or 0) * 5.0
    max_dist = (get_int(rec, f'{pfx}.MaxAttDist') or 0) * 100.0
    static_atten = get_int(rec, f'{pfx}.StaticAttenuation') or 0
    is_2d = bool(tes4_flags & _TES4_SND_2D)

    fid = get_formid(rec, 'FormID')
    sndr_fid = writer.derive_formid('SNDR', fid)
    record_sndr_for_soun(fid, sndr_fid)

    subs = pack_string_subrecord('EDID', sndr_editor_id(edid, fid))
    subs += pack_uint32_subrecord('CNAM', 0x1EEF540A)
    subs += pack_formid_subrecord('GNAM', _sndr_gnam(tes4_flags, is_2d))
    for anam in _sound_anam_paths(filename):
        subs += pack_string_subrecord('ANAM', anam)
    subs += pack_formid_subrecord('ONAM', _sndr_onam(writer, is_2d, min_dist, max_dist))
    lnam_value = 0x00000800 if (tes4_flags & _TES4_SND_LOOP) else 0
    subs += pack_subrecord('LNAM', struct.pack('<I', lnam_value))
    freq_adj = get_int(rec, f'{pfx}.FreqAdj') or 0
    freq_var = 0 if not (tes4_flags & _TES4_SND_RANDOM_FREQ_SHIFT) else 10
    subs += pack_subrecord(
        'BNAM', struct.pack('<bbBBH', max(-128, min(127, freq_adj)),
                            freq_var, 128, 0, min(65535, static_atten)))
    return pack_record('SNDR', sndr_fid, 0, subs), sndr_fid


def _sndr_gnam(tes4_flags: int, is_2d: bool) -> int:
    """The audio category FormID: AMB for a 2D loop, else SFX.

    See: docs/commentary/tes5_import_sound.md#gnam-2d-test-bit-6
    """
    if bool(tes4_flags & _TES4_SND_LOOP) and is_2d:
        return 0x0007F80B
    return 0x000172A1


def _sndr_onam(writer, is_2d: bool, min_dist: float, max_dist: float) -> int:
    """The Sound Output Model FormID; required, or the CK reports it missing.

    See: docs/commentary/tes5_import_sound.md#onam-3d-max-zero
    """
    if is_2d:
        return _SOPM_2D
    return _build_sopm(writer, min_dist,
                       max_dist if max_dist > 0 else _DEFAULT_3D_MAX_DIST,
                       stereo=False)


def convert_SOUN(rec: dict, writer=None) -> tuple:
    """SOUN — needs a companion SNDR record in TES5.

    Returns (soun_bytes, sndr_bytes_or_None, sndr_formid).  SOUN order is
    EDID OBND SDSC.

    See: docs/commentary/tes5_import_sound.md#sndx-field-scaling
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()

    sndr_fid = 0
    sndr_bytes = None
    filename = get_str(rec, 'FNAM.Filename')
    if filename and writer:
        sndr_bytes, sndr_fid = _sndr_record(rec, writer, edid, filename)

    if sndr_fid:
        subs += pack_formid_subrecord('SDSC', sndr_fid)
        record_sndr_for_soun(get_formid(rec, 'FormID'), sndr_fid)

    soun_bytes = pack_record('SOUN', get_formid(rec, 'FormID'),
                             get_int(rec, 'RecordFlags'), subs)
    return soun_bytes, sndr_bytes, sndr_fid
