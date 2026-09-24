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

`SUBSTITUTION_BLACKLIST` names Morroblivion replacements that are a DIFFERENT
object, not a moved one -- a hanging lamp became a floor candlestick, and three
entries name a mesh no game ships at all.  Those are never substituted, so the
Morrowind mesh converts instead.

See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
See: docs/audits/morroblivion_mesh_axis_rotation.md#substitution-blacklist
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
    'morroblivion/lights/common/candle_05.nif': 270,
    'morroblivion/lights/common/candle_12.nif': 270,
    'morroblivion/lights/dunmer/candle_13.nif': 270,
    'morroblivion/lights/dunmer/candle_16.nif': 270,
    'morroblivion/lights/dunmer/candle_17.nif': 270,
}

#: Base EditorID (lowercase) -> Z offset its references gained.
Z_RESEAT: dict = {
    '0lightufireu400': 13.30,
    '0lightufire': 13.30,
    '0lightufireunosmokeu400': 13.30,
    '0lightufireunosmokeu177': 13.30,
    '0lightufireunosmokeu128': 13.30,
    '0lightufireunosmoke': 13.30,
    '0barrelu01udeupos1': 3.00,
    '0barrelu01ucheapfood5': 3.00,
    '0barrelu01ucheapfood20': 3.00,
    '0barrelu01udrinks': 3.00,
    '0barrelu01uashyams': 3.00,
    '0lightudeucandleu24u64': -2.80,
    '0chestusmallu02uingfine3': -0.80,
    '0chestusmallu02uingfine4': -0.80,
    '0bkuoldways': -0.60,
}

#: Morroblivion replacements too unlike the original to substitute.
SUBSTITUTION_BLACKLIST: frozenset = frozenset((
    'lights/middlecandlestickfloor02.nif',
    'lights/candlefat02.nif',
    'lights/candleskinny01.nif',
    'lights/torch01fake.nif',
    'dungeons/misc/fx/fxmist01.nif',
    'dungeons/misc/fx/fxcloudthick01.nif',
    'dungeons/misc/cobweb04.nif',
    'dungeons/misc/root03.nif',
    'clutter/sack01.nif',
    'mip/container/barrels/mwbarrel10.nif',
    'mip/container/urns/clay_urn02.nif',
    'mip/container/urns/clay_urn03.nif',
    'mip/container/urns/clay_urn05.nif',
    'clutter/ingredskooma.nif',
    'clutter/morro/n/nbbread01.nif',
    'clutter/morro/m/misculwucup.nif',
    'mip/container/crates/common_chest01.nif',
    'clutter/morro/m/nbgold025.nif',
    'clutter/morro/m/nbpitcher01.nif',
    'clutter/morro/m/nbtankard02.nif',
    'mip/container/chests/chest02.nif',
    'morro/f/furnudeubenchu03.nif',
    'morro/f/furnudeubookshelfu02.nif',
    'morro/f/furnudeutableu01.nif',
    'morro/o/containudeuchestu01.nif',
    'morroblivion/lights/common/candle_03.nif',
    'morroblivion/lights/common/candle_07.nif',
    'morroblivion/lights/common/candle_08.nif',
    'morroblivion/lights/common/candle_09.nif',
    'morroblivion/lights/common/candle_10.nif',
    'morroblivion/lights/common/candle_14.nif',
    'morroblivion/lights/common/candle_16.nif',
    'morroblivion/lights/common/lantern_01.nif',
    'morroblivion/lights/common/lantern_02.nif',
    'morroblivion/lights/dungeons/tikilamp.nif',
    'morroblivion/lights/dunmer/buglamp_01.nif',
    'morroblivion/lights/dunmer/candle_01.nif',
    'morroblivion/lights/dunmer/candle_02.nif',
    'morroblivion/lights/dunmer/candle_04.nif',
    'morroblivion/lights/dunmer/candle_05.nif',
    'morroblivion/lights/dunmer/candle_07.nif',
    'morroblivion/lights/dunmer/candle_08.nif',
    'morroblivion/lights/dunmer/candle_09.nif',
    'morroblivion/lights/dunmer/candle_15.nif',
    'morroblivion/lights/dunmer/candle_18.nif',
    'morroblivion/lights/dunmer/candle_19.nif',
    'morroblivion/lights/dunmer/candle_blue_01.nif',
    'morroblivion/lights/dunmer/candle_blue_02.nif',
    'morroblivion/lights/dunmer/candle_green_01.nif',
    'morroblivion/lights/dunmer/candle_ivory_01.nif',
    'morroblivion/lights/dunmer/candle_red_01.nif',
    'morroblivion/lights/dunmer/lamp_05.nif',
    'morroblivion/lights/dunmer/lamp_06.nif',
    'morroblivion/lights/dunmer/lantern_01cc.nif',
    'morroblivion/lights/dunmer/lantern_06s.nif',
    'morroblivion/lights/dunmer/lantern_02.nif',
    'morroblivion/lights/dunmer/lantern_02s.nif',
    'morroblivion/lights/dunmer/lantern_06.nif',
    'morroblivion/lights/dunmer/lantern_07cc.nif',
    'morroblivion/lights/dunmer/lantern_10cc.nif',
    'morroblivion/lights/torchnohavok.nif',
))

#: Mesh -> (refs, delta, share, why) for deltas one value cannot express.
AXIS_PITCH_UNSURE: dict = {
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
