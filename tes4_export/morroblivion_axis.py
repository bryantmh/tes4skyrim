"""Rotation and height corrections Morroblivion hand-applied to its placements.

Morroblivion shipped meshes on the wrong axis and re-seated others, then
corrected every reference IT placed.  Morrowind's own references carry neither
correction, so a plugin converted against Morroblivion's meshes renders those
objects on their side, or floating, unless the same correction is applied here.

Every entry is a measured DELTA -- Morroblivion's value minus Morrowind's, per
reference, paired by cell and horizontal position.  A delta is the only correct
measure: an object Morrowind itself places rotated, and Morroblivion leaves
alone, cancels to zero and never becomes a candidate.  An absolute reading
needed a veto for that case, and the veto in turn hid real fixes -- the lava
rocks are tilted in Morrowind AND turned a further 180 by Morroblivion.

The fixes were made BY HAND, so they are not uniform: a base may be re-seated
while another base sharing the mesh is not.  Entries are therefore accepted on
a spike -- one value carrying >=80% of the references -- and `Z_RESEAT` keys on
the base EditorID rather than the mesh.

`MESH_SWAPS` is measured but deliberately NOT applied.  Those records were
repointed at a DIFFERENT object and their references dragged to suit -- a
hanging lamp became a floor candlestick, so a -127 offset puts a candlestick
where the author wanted a ceiling lamp.  The object is wrong, not its height,
and the fix is to restore the Morrowind mesh; the list is kept for that
separate pass.

See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
"""

import math

#: Mesh (archive path, lowercase '/') -> pitch degrees the refs gained.
AXIS_PITCH_DEG: dict = {
    'fire/fireopenmedium.nif': 270,
    'fire/fireopenmediumsmoke.nif': 270,
    'morro/i/inulavaurocku14.nif': 180,
    'morro/i/inulavaurocku16.nif': 180,
    'morro/i/inulavaurocku17.nif': 180,
    'morro/i/inulavaurocku18.nif': 180,
    'morro/i/inumoldurocku17.nif': 180,
    'morro/i/inupyurocku13.nif': 180,
    'morroblivion/lights/common/candle_01.nif': 270,
    'morroblivion/lights/common/candle_02.nif': 270,
    'morroblivion/lights/common/candle_03.nif': 270,
    'morroblivion/lights/common/candle_05.nif': 270,
    'morroblivion/lights/common/candle_08.nif': 270,
    'morroblivion/lights/common/candle_09.nif': 270,
    'morroblivion/lights/common/candle_10.nif': 270,
    'morroblivion/lights/common/candle_12.nif': 270,
    'morroblivion/lights/common/candle_14.nif': 270,
    'morroblivion/lights/common/candle_16.nif': 270,
    'morroblivion/lights/common/lantern_01.nif': 270,
    'morroblivion/lights/common/lantern_02.nif': 270,
    'morroblivion/lights/dunmer/buglamp_01.nif': 270,
    'morroblivion/lights/dunmer/candle_05.nif': 270,
    'morroblivion/lights/dunmer/candle_13.nif': 270,
    'morroblivion/lights/dunmer/candle_16.nif': 270,
    'morroblivion/lights/dunmer/candle_17.nif': 270,
    'morroblivion/lights/dunmer/candle_blue_01.nif': 270,
    'morroblivion/lights/dunmer/candle_blue_02.nif': 270,
    'morroblivion/lights/dunmer/candle_green_01.nif': 270,
    'morroblivion/lights/dunmer/candle_ivory_01.nif': 270,
    'morroblivion/lights/dunmer/candle_red_01.nif': 270,
    'morroblivion/lights/dunmer/lantern_02.nif': 270,
    'morroblivion/lights/dunmer/lantern_06.nif': 270,
    'morroblivion/lights/dunmer/lantern_07cc.nif': 270,
}

#: Base EditorID (lowercase) -> Z offset its references gained.
Z_RESEAT: dict = {
    '0barrelu02umarshmerrow': -25.00,
    '0torchu256': -5.00,
    '0torchu157': -5.00,
    '0torchu128': -5.00,
    '0torchu64': -5.00,
    '0torchu77': -5.00,
    '0barrelu01udeupos1': 3.00,
    '0barrelu01ucheapfood5': 3.00,
    '0barrelu01ucheapfood20': 3.00,
    '0barrelu01udrinks': 3.00,
    '0lightudecandleu24u64': -2.50,
    '0lightucomulanternu02u200uboat': 1.00,
    '0chestusmallu02uingfine3': -0.80,
    '0chestusmallu02uingfine4': -0.80,
    '0bkuoldways': -0.60,
}

#: Base EditorID -> (dZ, Morrowind mesh, Morroblivion mesh, refs). NOT applied.
MESH_SWAPS: dict = {
    '0lightudeulampu05u177': (-134.60, 'l/Light_De_Lamp_05.NIF',
                              'morroblivion/lights/dunmer/lamp_05.nif', 5),
    '0lightudeulampu03': (-133.60, 'l/Light_De_Lamp_03.NIF',
                          'lights/middlecandlestickfloor02.nif', 52),
    '0lightudeulampu01u77': (-128.30, 'l/Light_De_Lamp_01.NIF',
                             'lights/middlecandlestickfloor02.nif', 30),
    '0lightudeulampu01u64': (-128.30, 'l/Light_De_Lamp_01.NIF',
                             'lights/middlecandlestickfloor02.nif', 4),
    '0lightudeulampu01u512': (-127.00, 'l/Light_De_Lamp_01.NIF',
                              'lights/middlecandlestickfloor02.nif', 24),
    '0lightudeulampu05u256': (-127.00, 'l/Light_De_Lamp_05.NIF',
                              'morroblivion/lights/dunmer/lamp_05.nif', 10),
    '0lightudeulampu01u256': (-127.00, 'l/Light_De_Lamp_01.NIF',
                              'lights/middlecandlestickfloor02.nif', 28),
    '0lightudeulampu01u177': (-127.00, 'l/Light_De_Lamp_01.NIF',
                              'lights/middlecandlestickfloor02.nif', 16),
    '0lightudecandleu09': (-18.80, 'l/Light_De_Candle_09.NIF',
                           'morroblivion/lights/dunmer/candle_09.nif', 15),
    '0lightudecandleu09u64': (-18.80, 'l/Light_De_Candle_09.NIF',
                              'morroblivion/lights/dunmer/candle_09.nif', 116),
    '0lightudecandleu01': (-17.30, 'l/Light_De_Candle_01.NIF',
                           'morroblivion/lights/dunmer/candle_01.nif', 14),
    '0lightudecandleu01u64': (-17.30, 'l/Light_De_Candle_01.NIF',
                              'morroblivion/lights/dunmer/candle_01.nif', 122),
    '0lightudecandleu07': (-16.80, 'l/Light_De_Candle_07.NIF',
                           'morroblivion/lights/dunmer/candle_07.nif', 31),
    '0lightudecandleu07u64': (-16.80, 'l/Light_De_Candle_07.NIF',
                              'morroblivion/lights/dunmer/candle_07.nif', 197),
    '0lightufireu400': (13.30, 'l/Light_Fire.NIF',
                        'fire/fireopenmediumsmoke.nif', 30),
    '0lightufireu300': (13.30, 'l/Light_Fire.NIF',
                        'fire/fireopenmediumsmoke.nif', 4),
    '0lightufire': (13.30, 'l/Light_Fire.NIF',
                    'fire/fireopenmediumsmoke.nif', 9),
    '0lightufireunosmokeu400': (13.30, 'l/Light_Fire_NoSmoke.NIF',
                                'fire/fireopenmedium.nif', 9),
    '0lightufireunosmokeu177': (13.30, 'l/Light_Fire_NoSmoke.NIF',
                                'fire/fireopenmedium.nif', 5),
    '0lightufireunosmokeu128': (13.30, 'l/Light_Fire_NoSmoke.NIF',
                                'fire/fireopenmedium.nif', 30),
    '0lightufireunosmoke': (13.30, 'l/Light_Fire_NoSmoke.NIF',
                            'fire/fireopenmedium.nif', 8),
}

#: Mesh -> (refs, delta, share, why) for deltas one value cannot express.
AXIS_PITCH_UNSURE: dict = {
    'morroblivion/lights/torchnohavok.nif': (
        123, 270, 0.67, 'delta splits 270:82 90:36 - torches mount up or '
        'down, so the intent is per-reference'),
    'morroblivion/lights/dunmer/lantern_06s.nif': (
        136, 270, 0.52, 'delta split; geometry diagonal (lantern and chain)'),
    'morroblivion/lights/dunmer/candle_15.nif': (
        54, 270, 0.78, 'delta below the 80% spike threshold'),
    'morroblivion/lights/dunmer/candle_18.nif': (
        18, 270, 0.50, 'even +/-90 split across two bases'),
    'morroblivion/lights/dunmer/candle_19.nif': (
        15, 90, 0.53, 'even split; one base placed entirely upright'),
    'morroblivion/lights/dunmer/lantern_01cc.nif': (
        55, 270, 0.78, 'delta below the spike threshold'),
    'morroblivion/lights/dunmer/lantern_10cc.nif': (
        13, 270, 0.77, 'delta below the spike threshold'),
    'morroblivion/lights/dunmer/lantern_02s.nif': (
        4, 90, 0.50, 'even +/-90 split over 4 references'),
    'morro/f/furnucolonyuhook01.nif': (
        24, 90, 0.58, 'STAT; bimodal, no Morrowind counterpart to diff'),
}


def pitch_for_model(model: str) -> float:
    """Radians to add to a reference's RotX for this model."""
    if not model:
        return 0.0
    key = model.lower().replace('\\', '/').lstrip('/')
    deg = AXIS_PITCH_DEG.get(key)
    return math.radians(deg) if deg else 0.0


def z_reseat_for_base(editor_id: str) -> float:
    """Z offset a reference to this base must add (0.0 if none)."""
    if not editor_id:
        return 0.0
    return Z_RESEAT.get(editor_id.lower(), 0.0)


def is_unsure(model: str) -> bool:
    """Whether this model is mis-authored but deliberately not corrected."""
    if not model:
        return False
    return model.lower().replace('\\', '/').lstrip('/') in AXIS_PITCH_UNSURE
