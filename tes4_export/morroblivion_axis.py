"""Pitch a placed reference needs when Morroblivion built its mesh on Y or X.

The original Morroblivion converter shipped a set of meshes whose geometry runs
along Y or X instead of Z, then compensated by baking a pitch into every
reference IT placed.  Morrowind's own references carry no such pitch, so a
plugin converted against Morroblivion's meshes renders those objects on their
side unless the same pitch is added here.

Measured on the exports: Morroblivion's 686 references to these bases are 99%
already pitched, while Tamriel Rebuilt's 2,997 are 96% upright — which is why
the correction keys on who placed the reference, not on the base.  Only a
plugin that does NOT own the mesh needs it.

`AXIS_PITCH_DEG` holds the meshes one right-angle pitch demonstrably fixes;
`AXIS_PITCH_UNSURE` records the mis-authored ones it cannot, with the
measurement that disqualified each, so a later pass re-measures instead of
rediscovering them.

See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
"""

import math

#: Morroblivion mesh (archive path, lowercase '/') -> pitch degrees to add.
AXIS_PITCH_DEG: dict = {
    'morroblivion/lights/common/candle_01.nif': 270,
    'morroblivion/lights/common/candle_03.nif': 270,
    'morroblivion/lights/common/candle_05.nif': 270,
    'morroblivion/lights/common/candle_08.nif': 270,
    'morroblivion/lights/common/candle_09.nif': 270,
    'morroblivion/lights/common/candle_10.nif': 270,
    'morroblivion/lights/common/candle_14.nif': 270,
    'morroblivion/lights/common/candle_16.nif': 270,
    'morroblivion/lights/common/lantern_01.nif': 270,
    'morroblivion/lights/common/lantern_02.nif': 270,
    'morroblivion/lights/common/lantern_02_green.nif': 270,
    'morroblivion/lights/common/lantern_02_red.nif': 270,
    'morroblivion/lights/common/lantern_02_yellow.nif': 270,
    'morroblivion/lights/dunmer/buglamp_01.nif': 270,
    'morroblivion/lights/dunmer/candle_05.nif': 270,
    'morroblivion/lights/dunmer/candle_16.nif': 270,
}

#: Mis-authored but NOT corrected: (refs, pitch, share agreeing, why).
AXIS_PITCH_UNSURE: dict = {
    'morroblivion/lights/torchnohavok.nif': (
        602, 270, 0.77, 'bimodal: 463 refs at 270, 101 at +90 - torches '
        'mount pointing up or down, so intent is per-ref'),
    'morroblivion/lights/dunmer/lantern_06s.nif': (
        136, 270, 0.52, 'bimodal 43%; geometry diagonal (sizeY 34.9, '
        'sizeZ 36.2) - lantern plus chain, no right-angle fix'),
    'morroblivion/lights/dunmer/lantern_02.nif': (
        130, 270, 0.83, 'bimodal 11%; long axis measures Z'),
    'morroblivion/lights/dunmer/lantern_06.nif': (
        114, 270, 0.83, 'bimodal 6%; long axis measures Z'),
    'morroblivion/lights/dunmer/candle_06.nif': (
        86, 270, 0.78, 'bimodal 15%; long axis measures Z'),
    'morroblivion/lights/dunmer/candle_ivory_01.nif': (
        135, 270, 0.98, 'diagonal: sizeY 14.9 AND sizeZ 28.5 - leans in the '
        'YZ plane, so a right-angle pitch does not stand it upright'),
    'morroblivion/lights/dunmer/candle_red_01.nif': (
        47, 270, 0.96, 'diagonal, geometry identical to candle_ivory_01'),
    'morroblivion/lights/dunmer/candle_blue_01.nif': (
        34, 270, 1.0, 'diagonal: sizeY 28.3, sizeZ 28.5'),
    'morroblivion/lights/dunmer/candle_blue_02.nif': (
        30, 270, 1.0, 'diagonal, long axis measures Z'),
    'morroblivion/lights/dunmer/candle_green_01.nif': (
        9, 270, 1.0, 'diagonal, long axis measures Z'),
    'morroblivion/lights/dunmer/candle_13.nif': (
        23, 270, 1.0, 'long axis measures Z'),
    'morroblivion/lights/dunmer/candle_15.nif': (
        54, 270, 0.78, 'bimodal; Y-axis but pitch split'),
    'morroblivion/lights/dunmer/candle_17.nif': (
        46, 270, 0.74, 'bimodal; Y-axis but pitch split'),
    'morroblivion/lights/dunmer/candle_18.nif': (
        18, 270, 0.50, 'even +/-90 split'),
    'morroblivion/lights/dunmer/candle_19.nif': (
        15, 90, 0.53, 'even +/-90 split'),
    'morroblivion/lights/common/candle_12.nif': (
        26, 270, 0.77, 'bimodal; Y-axis but pitch split'),
    'morroblivion/lights/common/candle_02.nif': (
        6, 270, 1.0, 'long axis measures Z'),
    'morroblivion/lights/dunmer/lantern_01cc.nif': (
        55, 270, 0.76, 'bimodal'),
    'morroblivion/lights/dunmer/lantern_07cc.nif': (
        19, 270, 0.84, 'bimodal'),
    'morroblivion/lights/dunmer/lantern_10cc.nif': (
        13, 270, 0.77, 'bimodal'),
    'morroblivion/lights/dunmer/lantern_02s.nif': (
        4, 90, 0.50, 'even +/-90 split'),
    'morro/f/furnucolonyuhook01.nif': (
        24, 90, 0.58, 'STAT; bimodal, no Morrowind counterpart'),
    'morroblivion/fire/smokemedium.nif': (
        7, 270, 0.71, 'ACTI; too few refs to call'),
    'morroblivion/clutter/books/paudcutls.nif': (
        4, 270, 0.75, 'BOOK; Morrowind originals ARE tilted (100%)'),
    'morroblivion/clutter/books/paudcucpu02.nif': (
        4, 90, 1.0, 'BOOK; Morrowind originals ARE tilted (100%)'),
}


def pitch_for_model(model: str) -> float:
    """Radians to add to a reference's RotX for this Morroblivion model."""
    if not model:
        return 0.0
    key = model.lower().replace('\\', '/').lstrip('/')
    deg = AXIS_PITCH_DEG.get(key)
    return math.radians(deg) if deg else 0.0


def is_unsure(model: str) -> bool:
    """Whether this model is mis-authored but deliberately not corrected."""
    if not model:
        return False
    return model.lower().replace('\\', '/').lstrip('/') in AXIS_PITCH_UNSURE
