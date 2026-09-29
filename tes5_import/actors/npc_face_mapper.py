"""NPC face feature mapping: Oblivion → Skyrim head parts and face data.

TES4 face data available per NPC_:
  HNAM.Hair     — FormID of HAIR record (converted to HDPT, same FormID preserved)
  ENAM.Eyes     — FormID of EYES record (→ mapped Skyrim HDPT by eye color)
  HCLR.R/G/B   — Hair color bytes     (→ CLFM FormID via map_hair_color)
  LNAM.HairLength — Hair length float (blend weight for the hair .tri's
                    HairMorph; BAKED per variant — see hair_variants)
  FGGS          — FaceGen Geometry-Symmetric: hex string, 200 bytes = 50 float32 PCA coefficients
  FGGA          — FaceGen Geometry-Asymmetric: hex string, 120 bytes = 30 float32 PCA coefficients
  FGTS          — FaceGen Texture-Symmetric: hex string, 200 bytes = 50 float32 PCA coefficients

TES5 face subrecords emitted (by the functions in this module):
  PNAM[]  — Head parts array: hair HDPT (from HNAM.Hair) + eyes HDPT
  FTST    — Head texture set TXST FormID (race+gender table, Skyrim.esm)
  QNAM    — Texture lighting: the NPC's effective skin color as 3 floats
             (must match the skin-tone tint layer or face and body diverge)
  NAM9    — Face morphs: 19 floats mapped from Oblivion FGGS PCA coefficients
  NAMA    — Face part preset indices: (0, 0, 0, 0)  (nose, ?, eyes, mouth)
  TINI/TINC/TINV/TIAS — the race's Skin Tone tint layer.  The engine colors
             the BODY skin from this layer; an NPC without one renders with
             untinted (pale white) body skin regardless of race.

Hair PNAM note:
  Oblivion HAIR records are converted to Skyrim HDPT records (Type=3/Hair) by
  convert_HAIR().  The BASE length keeps the source FormID, so an NPC whose
  LNAM is 0 resolves straight through get_formid(rec,'HNAM.Hair').  Any other
  authored length names a baked variant whose HDPT id is
  derive_formid('HDPT_HAIR', (hair_fid, bucket)) — see _resolve_hair_part.

FTST note:
  FormIDs listed are from the sequential block 0x000FDFE6–0x000FDFF5 in
  Skyrim.esm (verified pattern: 8 playable races × 2 genders = 16 entries,
  DarkElf first, Redguard last).  Argonian/Khajiit have separate face TXST
  records.  If a race is absent from the table, FTST is omitted and the
  engine falls back to the race record's own head-texture assignment.

NAM9 / FGGS mapping note:
  Oblivion's FGGS is a 200-byte array of 50 little-endian float32 PCA
  (Principal Component Analysis) coefficients from FaceGen Modeller's
  "Geometry-Symmetric" basis for each Oblivion race head mesh.

  Skyrim's NAM9 is 19 direct slider floats, each approximately in [-1, 1].
  The two systems are fundamentally different (PCA basis vs. direct sliders),
  so an exact conversion is impossible.  However, the early PCA components
  tend to capture the most visually prominent facial variations — overall
  face proportions — and can be loosely mapped to the closest Skyrim slider.

  Mapping strategy:
    1. Parse the 50 FGGS floats.
    2. Normalize them: the typical magnitude of Oblivion FGGS coefficients
       observed in Oblivion.esm spans roughly ±3.  We clamp/normalize to
       [-1, 1] by dividing by a per-slot scale factor (empirically chosen).
    3. Each Skyrim slider receives a weighted sum of the FGGS coefficients
       that most strongly influence that facial region, as documented by
       community reverse-engineering of the FaceGen SDK.

  The result is a best-effort approximation: NPCs will not look identical
  to their Oblivion counterparts, but will have meaningfully varied faces
  rather than the flat neutral default (all zeros).

  FGGS PCA component → dominant facial region (from community research):
    [0]  overall face width / jaw width
    [1]  face height / vertical proportions
    [2]  nose prominence / nose length
    [3]  brow ridge depth
    [4]  eye vertical placement
    [5]  cheekbone height
    [6]  chin shape / jaw angle
    [7]  lip fullness / mouth height
    [8]  nose bridge / nose width
    [9]  eye depth / socket depth
    [10] brow convergence
    [11] jaw forward/back
    [12] cheek depth
    [13] eye in/out (convergence)
    [14] lip protrusion
    [15] chin width
    [16] chin vertical
    [17] eye socket forward/back
    [18-49] higher-order detail (diminishing influence; spread across nearest)
"""

import os
import struct

from ..base.equivalents import (
    RACE_DEFAULT_EYES,
    map_eye_formid,
    resolve_eye_by_fid,
)
from asset_convert.character.hair_plan import variant_edid, variant_tag
from . import hair_variants
from ..base.text_reader import get_formid, get_str
from asset_convert import case_paths
from output_layout import assets_for
from ..base.writer import pack_formid_subrecord, pack_subrecord

# ---------------------------------------------------------------------------
# Head texture set (FTST) — race + gender → TXST FormID (Skyrim.esm)
_RACE_HEAD_TXST: dict[str, dict[str, int]] = {
    'DarkElf':     {'Male': 0x000FDFE6, 'Female': 0x000FDFE7},
    'Breton':      {'Male': 0x000FDFE8, 'Female': 0x000FDFE9},
    'HighElf':     {'Male': 0x000FDFEA, 'Female': 0x000FDFEB},
    'Imperial':    {'Male': 0x000FDFEC, 'Female': 0x000FDFED},
    'Nord':        {'Male': 0x000FDFEE, 'Female': 0x000FDFEF},
    'WoodElf':     {'Male': 0x000FDFF0, 'Female': 0x000FDFF1},
    'Orc':         {'Male': 0x000FDFF2, 'Female': 0x000FDFF3},
    'Redguard':    {'Male': 0x000FDFF4, 'Female': 0x000FDFF5},
    # Argonian and Khajiit have distinct scales/fur — different TXST block.
    # Omitting them here causes the engine to fall back to the race default.
}


# See: docs/commentary/asset_convert_facegen.md#skin-tone-tint-layers

_SKIN_RACE_ALIAS = {
    # Must follow RACE_MAP's target Skyrim race -- tint indices are per-race.
    'GoldenSaint': 'HighElf',
    'DarkSeducer': 'DarkElf',
    'SEDremora':   'Dremora',
    'Sheogorath':  'Imperial',
}

# (race, gender) -> skin-tone TINI index, from the RACE tint-mask definitions
# in Skyrim.esm (tools/generators/gen_npc_skin_table.py).  The index is a property of the
# TARGET Skyrim race, so it stays a table; only the color comes from Oblivion.
_SKIN_TINI: dict[tuple, int] = {
    ('Imperial', 'Male'): 1,  ('Imperial', 'Female'): 13,
    ('Nord', 'Male'):     1,  ('Nord', 'Female'):     24,
    ('Breton', 'Male'):   2,  ('Breton', 'Female'):   16,
    ('Redguard', 'Male'): 1,  ('Redguard', 'Female'): 23,
    ('DarkElf', 'Male'):  1,  ('DarkElf', 'Female'):  24,
    ('HighElf', 'Male'):  1,  ('HighElf', 'Female'):  24,
    ('WoodElf', 'Male'):  1,  ('WoodElf', 'Female'):  24,
    ('Orc', 'Male'):      1,  ('Orc', 'Female'):      13,
    ('Argonian', 'Male'): 38, ('Argonian', 'Female'): 16,
    ('Khajiit', 'Male'):  1,  ('Khajiit', 'Female'):  4,
    ('Dremora', 'Male'):  1,  ('Dremora', 'Female'):  24,
}

# Fallback skin colors, measured from Oblivion's own race textures, used when
# the export lacks the RACE part/FGTS data (an older export, or a custom race
# whose textures could not be resolved).  Keyed by TES4 race EditorID.
_SKIN_FALLBACK_RGB: dict[str, tuple] = {
    'Imperial':    (186, 120, 80),
    'Nord':        (234, 162, 145),
    'Breton':      (224, 164, 120),
    'HighElf':     (212, 183, 83),
    'WoodElf':     (198, 139, 107),
    'Redguard':    (118, 60, 35),
    'DarkElf':     (89, 88, 83),
    'Orc':         (103, 111, 45),
    'Khajiit':     (189, 124, 58),
    'Argonian':    (101, 56, 33),
    'Dremora':     (58, 38, 38),
    'GoldenSaint': (173, 119, 51),
    'DarkSeducer': (65, 51, 59),
}

# Interpolation written on the skin-tone layer.  The engine blends the tint
# toward the base head texture by this fraction, and QNAM must agree with it
# or the face is lit a different color than the body.  Vanilla writes
# fractional values on real tint layers; a full-strength 100 paints the raw
# color flat and reads noticeably darker than the source.
_SKIN_TINV = 80

# ---------------------------------------------------------------------------
# Race skin tones
# ---------------------------------------------------------------------------

#: race EditorID -> {gender: (r, g, b)}, filled by load_race_skin_tones().
RACE_SKIN_RGB: dict[str, dict] = {}


def _first(rec: dict, key: str):
    """First value for `key` in an export record dict (values may be lists)."""
    v = rec.get(key)
    if isinstance(v, list):
        return v[0] if v else None
    return v


def load_race_skin_tones(by_type: dict, export_dirs=None) -> None:
    """Index each TES4 race's authored skin color for the skin-tone layer.

    Reconstructs base_texture + FGTS_SCALE * sum(race_FGTS[i] * egt_mode[i])
    per race and gender.  Safe to call repeatedly; races already indexed are
    not recomputed.  A race whose assets cannot be resolved is left out, and
    _SKIN_FALLBACK_RGB supplies its color.
    """
    races = by_type.get('RACE') or []
    if not races:
        return
    try:
        from asset_convert.character.facegen_egt import (
            load_egt_mode_means, sample_texture_rgb, reconstruct_skin_rgb,
            parse_fgts_hex)
    except ImportError:
        return

    roots = [assets_for(d) / sub for d in (export_dirs or []) if d
             for sub in ('textures', 'meshes')]

    def _asset(rel):
        """A TES4 asset path found under the export asset trees, any case, or None."""
        return case_paths.resolve(roots, rel, 'race_skin_tones')

    for rec in races:
        edid = _first(rec, 'EditorID')
        if not edid or edid in RACE_SKIN_RGB:
            continue
        fgts = parse_fgts_hex(_first(rec, 'FGTS'))
        modes = None
        if fgts:
            head_model = _first(rec, 'FacePart[0].Model')
            if head_model:
                egt = _asset(os.path.splitext(head_model)[0] + '.egt')
                if egt:
                    modes = load_egt_mode_means(egt)
        per_gender = {}
        for gender, key in (('Male', 'MalePart'), ('Female', 'FemalePart')):
            tex = (_first(rec, key + '[0].Texture')      # upper body
                   or _first(rec, 'FacePart[0].Texture'))  # head
            path = _asset(tex) if tex else None
            base = sample_texture_rgb(path) if path else None
            if not base:
                continue
            rgb = reconstruct_skin_rgb(base, fgts, modes)
            if rgb:
                per_gender[gender] = rgb
        if per_gender:
            RACE_SKIN_RGB[edid] = per_gender


def _pick_skin_tone(race_edid: str, gender: str, fid: int):
    """Return (tini_index, (r, g, b), tinv) for this NPC's skin-tone layer.

    The color is the race's authored Oblivion skin tone.  `fid` is unused and
    kept so callers need not care whether variation exists: Oblivion NPCs of
    one race share a skin tone to within ~1/255, so there is nothing to vary.
    """
    alias = _SKIN_RACE_ALIAS.get(race_edid, race_edid)
    rgb = (RACE_SKIN_RGB.get(race_edid, {}).get(gender)
           or RACE_SKIN_RGB.get(alias, {}).get(gender)
           or _SKIN_FALLBACK_RGB.get(race_edid)
           or _SKIN_FALLBACK_RGB.get(alias)
           or _SKIN_FALLBACK_RGB['Imperial'])
    tini = (_SKIN_TINI.get((alias, gender))
            or _SKIN_TINI.get(('Imperial', gender))
            or _SKIN_TINI[('Imperial', 'Male')])
    return tini, tuple(rgb), _SKIN_TINV


# ---------------------------------------------------------------------------
# FGGS → NAM9 morph mapping
# ---------------------------------------------------------------------------

# NAM9 slider indices (matching xEdit / wbDefinitionsTES5.pas order):
#  0  Nose Long/Short
#  1  Nose Up/Down
#  2  Jaw Up/Down
#  3  Jaw Narrow/Wide
#  4  Jaw Forward/Back
#  5  Cheeks Up/Down
#  6  Cheeks Forward/Back
#  7  Eyes Up/Down
#  8  Eyes In/Out
#  9  Brows Up/Down
#  10 Brows In/Out
#  11 Brows Forward/Back
#  12 Lips Up/Down
#  13 Lips In/Out
#  14 Chin Narrow/Wide
#  15 Chin Up/Down
#  16 Chin Underbite/Overbite
#  17 Eyes Forward/Back
#  18 Unknown

# Each entry: (fggs_index, weight, skyrim_slider_index)
# The weight accounts for both direction (sign) and relative importance.
# Scale factors convert the typical Oblivion PCA magnitude (~±3) to Skyrim
# slider range (~±1).  Multiple FGGS components can contribute to one slider.
_FGGS_TO_NAM9: list[tuple[int, float, int]] = [
    # FGGS[0]: face width  → Jaw Narrow/Wide (3), Chin Narrow/Wide (14)
    (0,  0.60, 3),
    (0,  0.50, 14),
    # FGGS[1]: face height → Jaw Up/Down (2), Nose Up/Down (1)
    (1,  0.60, 2),
    (1,  0.40, 1),
    # FGGS[2]: nose size   → Nose Long/Short (0)
    (2,  0.75, 0),
    (2,  0.25, 1),
    # FGGS[3]: brow depth  → Brows Forward/Back (11), Brows Up/Down (9)
    (3,  0.75, 11),
    (3,  0.40, 9),
    # FGGS[4]: eye height  → Eyes Up/Down (7)
    (4,  0.75, 7),
    # FGGS[5]: cheeks      → Cheeks Up/Down (5)
    (5,  0.75, 5),
    # FGGS[6]: chin shape  → Chin Up/Down (15), Chin Underbite/Overbite (16)
    (6,  0.60, 15),
    (6,  0.40, 16),
    # FGGS[7]: lip shape   → Lips Up/Down (12), Lips In/Out (13)
    (7,  0.60, 12),
    (7,  0.40, 13),
    # FGGS[8]: nose bridge → Nose Long/Short (0) secondary
    (8,  0.35, 0),
    # FGGS[9]: eye depth   → Eyes Forward/Back (17)
    (9,  0.60, 17),
    # FGGS[10]: brow in/out → Brows In/Out (10)
    (10, 0.60, 10),
    # FGGS[11]: jaw fwd/back → Jaw Forward/Back (4)
    (11, 0.60, 4),
    # FGGS[12]: cheek depth  → Cheeks Forward/Back (6)
    (12, 0.60, 6),
    # FGGS[13]: eye convergence → Eyes In/Out (8)
    (13, 0.60, 8),
    # FGGS[14]: lip out    → Lips In/Out (13) secondary
    (14, 0.35, 13),
    # FGGS[15]: chin width → Chin Narrow/Wide (14) secondary
    (15, 0.35, 14),
    # FGGS[16]: chin vert  → Chin Up/Down (15) secondary
    (16, 0.35, 15),
    # FGGS[17]: eye fwd    → Eyes Forward/Back (17) secondary
    (17, 0.35, 17),
    # Higher-order components (18–49): small contributions spread across nearby sliders.
    (18, 0.15, 0),  (19, 0.15, 1),  (20, 0.15, 2),  (21, 0.15, 3),
    (22, 0.15, 4),  (23, 0.15, 5),  (24, 0.15, 6),  (25, 0.15, 7),
    (26, 0.15, 8),  (27, 0.15, 9),  (28, 0.15, 10), (29, 0.15, 11),
    (30, 0.15, 12), (31, 0.15, 13), (32, 0.15, 14), (33, 0.15, 15),
    (34, 0.15, 16), (35, 0.15, 17),
]

_NAM9_CLAMP = 1.5  # allow up to ±1.5 for more pronounced morphs


def _parse_fggs(rec: dict) -> list[float]:
    """Parse FGGS hex string into list of 50 float32 values, or empty list."""
    hex_str = get_str(rec, 'FGGS')
    if not hex_str:
        return []
    try:
        data = bytes.fromhex(hex_str)
    except ValueError:
        return []
    count = len(data) // 4
    if count == 0:
        return []
    return list(struct.unpack_from(f'<{count}f', data))


def _fggs_to_nam9(fggs: list[float]) -> list[float]:
    """Convert up to 50 FGGS PCA coefficients to 19 NAM9 face morph floats.

    Each NAM9 slot accumulates weighted contributions from the FGGS components
    that most influence that facial region.  The result is clamped to [-1, 1].
    """
    morphs = [0.0] * 19
    n = len(fggs)
    for fggs_idx, weight, nam9_idx in _FGGS_TO_NAM9:
        if fggs_idx < n:
            morphs[nam9_idx] += fggs[fggs_idx] * weight
    return [max(-_NAM9_CLAMP, min(_NAM9_CLAMP, v)) for v in morphs]


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _resolve_eyes_hdpt(rec: dict, race_edid: str, gender: str) -> int:
    """Return the Skyrim eye HDPT FormID for this NPC.

    Priority:
      1. Race-specific override (Argonian, Khajiit have unique eye geometry)
      2. Mapped from the TES4 EYES FormID via the full per-race/gender table
      3. Generic brown default (gender-appropriate)
    """
    fid = RACE_DEFAULT_EYES.get(race_edid)
    if fid:
        return fid
    tes4_eye_fid = get_formid(rec, 'ENAM.Eyes')
    if tes4_eye_fid:
        return resolve_eye_by_fid(tes4_eye_fid, gender)
    # Fallback: name-based lookup on an empty string returns gender default
    return map_eye_formid('', '', gender)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _resolve_hair_part(rec: dict, hair_fid: int, race_edid: str,
                       gender: str, writer=None) -> int:
    """The HDPT FormID for this NPC's hair: its length, gender and race's head family.

    A variant this plugin emits derives here; one a master's plan baked is that
    master's record, found by EditorID in the converted master; anything else
    falls back to the base FormID the load-order remap resolves.
    record_types.npc is imported here because it imports this module.

    See: docs/commentary/asset_convert_armor.md#hair-variants-follow-the-wearer
    """
    from ..record_types.npc import hair_variant_formid
    e = hair_variants.entry(hair_fid)
    if writer is None or e is None:
        return hair_fid
    v = hair_variants.npc_variant(e, rec, race_edid, gender)
    if v in e['variants']:
        return hair_variant_formid(writer, hair_fid, v.bucket, v.female,
                                   e['base'].female, variant_tag(v, e))
    if v in e['master_variants']:
        return _master_hdpt(writer, e, v) or hair_fid
    return hair_fid


def _master_hdpt(writer, e: dict, v) -> int:
    """A converted master's HDPT for a variant its plan baked, by EditorID; 0 if none."""
    adoption = getattr(writer, 'adoption', None)
    if adoption is None:
        return 0
    family = None if v.family == e['home'] else v.family
    return adoption.master_index.find_by_edid(
        b'HDPT', variant_edid(e['edid'], v.bucket, v.female, family))


def build_pnam_subs(rec: dict, race_edid: str, gender: str = 'Male',
                    writer=None) -> bytes:
    """Build PNAM[] subrecords for NPC_ head parts.

    Writes (in order):
      • Hair HDPT  — TES4 HAIR FormID mapped to Skyrim HDPT via race+gender table
      • Eyes HDPT  — mapped from TES4 EYES FormID or race override

    A missing hair FormID (NPC has no hair, e.g. skeleton or creature
    with no hair record) produces a bald NPC rather than a bad reference.
    """
    subs = b''

    # Hair head part.  The converted HAIR record IS the head part (convert_HAIR
    # emits an HDPT keeping the source FormID for the base length), so an
    # NPC resolves to its own plugin's hair rather than a substituted vanilla
    # Skyrim hairstyle.  Its authored length (NPC_.LNAM) selects which baked
    # variant — see hair_variants / asset_convert.character.hair_pipeline.
    hair_fid = get_formid(rec, 'HNAM.Hair')
    if hair_fid:
        subs += pack_formid_subrecord('PNAM', _resolve_hair_part(
            rec, hair_fid, race_edid, gender, writer))

    # Eyes head part: map TES4 EYES FormID → Skyrim HDPT FormID
    eyes_fid = _resolve_eyes_hdpt(rec, race_edid, gender)
    subs += pack_formid_subrecord('PNAM', eyes_fid)

    return subs


def build_face_tail_subs(rec: dict, race_edid: str, gender: str) -> bytes:
    """Build the trailing face subrecords for NPC_: FTST, QNAM, NAM9, NAMA,
    and the skin-tone tint layer (TINI/TINC/TINV/TIAS).

    These must appear *after* DOFT/SOFT/DPLT/CRIF in the record.

    FTST — head texture set (race+gender default from Skyrim.esm)
    QNAM — texture lighting: the NPC's effective skin color (tint color
             blended toward white by the interpolation value), as vanilla
             does — QNAM must agree with the skin-tone layer or the face
             is lit a different color than the body
    NAM9 — 19 face-morph floats mapped from Oblivion FGGS PCA coefficients;
             falls back to all-zero neutral if FGGS is absent or unparseable
    NAMA — face-part preset indices: nose=0, unknown=0, eyes=0, mouth=0
    TINI/TINC/TINV/TIAS — skin-tone tint layer; the engine derives the BODY
             skin color from this layer, so omitting it leaves every body
             pale white no matter the race
    """
    subs = b''

    # FTST — head texture set
    txst_fid = _RACE_HEAD_TXST.get(race_edid, {}).get(gender, 0)
    if txst_fid:
        subs += pack_formid_subrecord('FTST', txst_fid)

    # Skin tone: the race's authored Oblivion color (see _pick_skin_tone)
    fid = get_formid(rec, 'FormID')
    tini, (r, g, b), tinv = _pick_skin_tone(race_edid, gender, fid)

    # QNAM — texture lighting (stored as three 0–1 floats; xEdit × 255 → 0–255)
    # Effective color = lerp(white, tint color, interpolation).
    v = tinv / 100.0
    qnam = tuple((255.0 * (1.0 - v) + c * v) / 255.0 for c in (r, g, b))
    subs += pack_subrecord('QNAM', struct.pack('<3f', *qnam))

    # NAM9 — face morphs: map from FGGS PCA coefficients when available
    fggs = _parse_fggs(rec)
    if fggs:
        morphs = _fggs_to_nam9(fggs)
    else:
        morphs = [0.0] * 19
    subs += pack_subrecord('NAM9', struct.pack('<19f', *morphs))

    # NAMA — face part preset indices (Nose U32, Unknown S32, Eyes U32, Mouth U32)
    subs += pack_subrecord('NAMA', struct.pack('<IiII', 0, 0, 0, 0))

    # Skin-tone tint layer.  TINI = race tint-mask index (U16), TINC = RGBA
    # (alpha always 0 in vanilla), TINV = interpolation ×100 (U32),
    # TIAS = preset index (S16, -1 = explicit color, no preset).
    subs += pack_subrecord('TINI', struct.pack('<H', tini))
    subs += pack_subrecord('TINC', struct.pack('<4B', r, g, b, 0))
    subs += pack_subrecord('TINV', struct.pack('<I', tinv))
    subs += pack_subrecord('TIAS', struct.pack('<h', -1))

    return subs
