"""Beast-race head gear: per-race meshes and the per-race ARMA that names them.

A hood or helmet is ONE Oblivion record worn by every race, so unlike hair
(whose EditorID names its race) there is nothing on the record to route on.
Fitted to the shared human skull, that single mesh sits inside a khajiit or
argonian head.  Vanilla Skyrim answers this with one ARMA per race family,
each naming its own reshaped NIF (ArmorIronHelmet -> IronHelmetAA /
IronHelmetKhajiitAA / IronHelmetArgonianAA); these tests pin that structure.
"""

import struct

import pytest

from asset_convert.character.first_person import first_person_path
from tes5_import.record_types import equipment as eq
from tes5_import.base.equivalents import (
    ARMA_ADDITIONAL_RACES,
    ARMA_ADDITIONAL_RACES_NONBEAST,
    ARMA_BEAST_RACES,
)

KHAJIIT_RACE = 0x00013745
ARGONIAN_RACE = 0x00013740
DEFAULT_RACE = 0x00000019


class _Writer:
    """Minimal writer stub: records what convert_ARMO emits."""

    def __init__(self):
        self.records = []
        self._n = 0

    def derive_formid(self, site, key):
        self._n += 1
        return 0x0F000000 + self._n

    def add_record(self, sig, data):
        self.records.append((sig, data))


def _subrecords(record_bytes):
    """{sig: [payload, ...]} for a packed record (24-byte TES5 header)."""
    body = record_bytes[24:]
    out, i = {}, 0
    while i + 6 <= len(body):
        sig = body[i:i + 4].decode('latin1')
        size = struct.unpack('<H', body[i + 4:i + 6])[0]
        out.setdefault(sig, []).append(body[i + 6:i + 6 + size])
        i += 6 + size
    return out


def _text(payload):
    return payload.rstrip(b'\x00').decode('latin1')


def _fid(payload):
    return struct.unpack('<I', payload)[0]


def _record(biped_flags, edid='TestPiece', model='armor\\test\\m\\piece.nif'):
    return {
        'FormID': '0x00001234',
        'EditorID': edid,
        'BMDT.BipedFlags': str(biped_flags),
        'Male.BipedModel.MODL': model,
        'Female.BipedModel.MODL': model,
        'DATA.Value': '100',
        'DATA.Weight': '2.0',
        'DATA.ArmorRating': '5',
        'RecordFlags': '0',
    }


def _armas(rec):
    w = _Writer()
    armo = eq.convert_ARMO(rec, writer=w)
    return armo, [_subrecords(b) for sig, b in w.records if sig == 'ARMA']


# --- the gate ---------------------------------------------------------------

@pytest.mark.parametrize('bit,expect_beast', [
    (0, True),    # Head
    (1, True),    # Hair -- the slot Oblivion hoods/helmets are authored into
    (2, False),   # UpperBody
    (3, False),   # LowerBody
    (4, False),   # Hand
    (5, False),   # Foot
    (13, False),  # Shield
])
def test_only_head_gear_gets_beast_armatures(bit, expect_beast):
    """Head gear is decided by the AUTHORED BMDT flags, never by filename."""
    races = eq._beast_arma_races(_record(1 << bit))
    assert bool(races) is expect_beast
    if expect_beast:
        assert set(races) == set(ARMA_BEAST_RACES)


def test_multi_slot_suit_gets_no_beast_armature():
    """A suit claiming head AND body slots is fitted to the BODY.

    Knight of Order (head + torso + legs + hands + feet in one NIF, flags
    0x3D) resolves to a body piece in asset_convert, so no per-race mesh is
    written for it -- a beast ARMA would point at a missing file and the
    wearer would render invisible.  Measured on Oblivion.esm: 14 of 484 beast
    ARMAs pointed at a missing mesh before this gate, all of them that suit.
    """
    assert eq._beast_arma_races(_record(0x3D)) == ()
    _armo, armas = _armas(_record(0x3D))
    assert len(armas) == 1
    assert [_fid(m) for m in armas[0]['MODL']] == ARMA_ADDITIONAL_RACES


def test_body_gear_armature_is_unchanged():
    """A cuirass keeps exactly one armature listing every race and its vampire race."""
    _armo, armas = _armas(_record(1 << 2))
    assert len(armas) == 1
    assert _fid(armas[0]['RNAM'][0]) == DEFAULT_RACE
    assert [_fid(m) for m in armas[0]['MODL']] == ARMA_ADDITIONAL_RACES


def test_armature_races_match_vanilla_iron_boots():
    """Base/vampire pairs as Skyrim.esm IronBootsAA lists them, minus Elder and Manakin.

    Vampire races carry no armor-race link, so an armature that skips them
    renders nothing on a vampire. See: docs/commentary/asset_convert_armor.md#arma-race-lists
    """
    assert ARMA_ADDITIONAL_RACES == [
        0x00013740, 0x0008883A, 0x00013741, 0x0008883C, 0x00013742, 0x0008883D,
        0x00013743, 0x00088840, 0x00013744, 0x00088844, 0x00013745, 0x00088845,
        0x00013746, 0x00088794, 0x00013747, 0x000A82B9, 0x00013748, 0x00088846,
        0x00013749, 0x00088884,
    ]


# --- the split --------------------------------------------------------------

def test_head_gear_emits_three_armatures():
    """One default + one per beast race, mirroring vanilla ArmorIronHelmet."""
    armo, armas = _armas(_record(1 << 1, edid='TestHood',
                                 model='clothes\\test\\hood.nif'))
    assert len(armas) == 3

    by_race = {_fid(a['RNAM'][0]): a for a in armas}
    assert set(by_race) == {DEFAULT_RACE, KHAJIIT_RACE, ARGONIAN_RACE}

    # The ARMO references every armature it generated.
    assert len(_subrecords(armo)['MODL']) == 3

    # Default armature: the beast races are REMOVED, or the engine would
    # satisfy a khajiit with the human-fitted mesh and never reach the
    # beast one.
    default = by_race[DEFAULT_RACE]
    default_races = [_fid(m) for m in default['MODL']]
    assert default_races == ARMA_ADDITIONAL_RACES_NONBEAST
    for race in (KHAJIIT_RACE, ARGONIAN_RACE, 0x00088845, 0x0008883A):
        assert race not in default_races
    assert 0x00088794 in default_races

    # Beast armatures: that race's vampire variant as the additional race,
    # exactly as vanilla lists it.
    assert [_fid(m) for m in by_race[KHAJIIT_RACE]['MODL']] == [0x00088845]
    assert [_fid(m) for m in by_race[ARGONIAN_RACE]['MODL']] == [0x0008883A]


def test_beast_armatures_name_the_per_race_mesh():
    """Each armature points at the NIF fitted to its own race's skull."""
    _armo, armas = _armas(_record(1 << 1, edid='TestHood',
                                  model='clothes\\test\\hood.nif'))
    by_race = {_fid(a['RNAM'][0]): a for a in armas}

    for race_fid, suffix in ((DEFAULT_RACE, ''),
                             (KHAJIIT_RACE, '_khajiit'),
                             (ARGONIAN_RACE, '_argonian')):
        arma = by_race[race_fid]
        for field in ('MOD2', 'MOD3'):
            assert _text(arma[field][0]).lower().endswith(
                'hood%s.nif' % suffix), (race_fid, field)


def test_beast_armature_edids_are_distinct():
    _armo, armas = _armas(_record(1 << 1, edid='TestHood'))
    edids = sorted(_text(a['EDID'][0]) for a in armas)
    assert edids == ['TestHood_AA', 'TestHood_ArgonianAA',
                     'TestHood_KhajiitAA']


def test_head_gear_never_gets_a_weight_suffix():
    """Head gear has the slider OFF, so the race suffix can never collide
    with the _1 weight suffix."""
    _armo, armas = _armas(_record(1 << 1, edid='TestHood',
                                  model='clothes\\test\\hood.nif'))
    for arma in armas:
        for field in ('MOD2', 'MOD3'):
            assert '_1.nif' not in _text(arma[field][0]).lower()
        slider_m, slider_f = struct.unpack('<BB', arma['DNAM'][0][2:4])
        assert slider_m == 0 and slider_f == 0


def test_first_person_meshes_are_the_worn_meshes_1stperson_copies():
    """MOD4/MOD5 name the `1stPerson` copy of the human MOD2/MOD3, on body gear and every beast armature."""
    _armo, (body,) = _armas(_record(1 << 2, model='clothes\\test\\shirt.nif'))
    for worn, first in (('MOD2', 'MOD4'), ('MOD3', 'MOD5')):
        assert _text(body[first][0]) == first_person_path(_text(body[worn][0]))
    _armo, armas = _armas(_record(1 << 1, edid='TestHood', model='clothes\\test\\hood.nif'))
    human = next(a for a in armas if _fid(a['RNAM'][0]) == DEFAULT_RACE)
    for arma in armas:
        for field in ('MOD4', 'MOD5'):
            assert _text(arma[field][0]) == first_person_path(_text(human['MOD2'][0])), field


def test_beast_armatures_use_distinct_formids():
    """Keyed on (source FormID, race), so the human ARMA id never moves."""
    w = _Writer()
    eq.convert_ARMO(_record(1 << 1), writer=w)
    fids = [struct.unpack('<I', b[12:16])[0] for sig, b in w.records]
    assert len(fids) == len(set(fids)) == 3


# --- the fit ----------------------------------------------------------------

def test_beast_fields_move_head_gear_off_the_beast_skull():
    """The race field must fit its own skull better than the human field.

    This is the defect itself: fitted through the human field, a hood/helmet
    penetrates the khajiit and argonian skulls far more than the human mesh
    ever penetrates the human one.
    """
    np = pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    from scipy.spatial import cKDTree

    from asset_convert.character import head_fit

    if not head_fit.fit_available(False):
        pytest.skip('head-fit data not built')
    races = head_fit.beast_races_available(False)
    if not races:
        pytest.skip('beast race packs not built')

    fit = head_fit._get(False)

    def penetration(P, race):
        """Total depth of P inside that race's real head."""
        sv, st = fit.races_full[race]
        tree = cKDTree(sv[st].mean(axis=1))
        _, idx = tree.query(P, k=16)
        a, b, c = sv[st[idx, 0]], sv[st[idx, 1]], sv[st[idx, 2]]
        cp = head_fit._closest_point_on_triangles(P[:, None, :], a, b, c)
        dv = P[:, None, :] - cp
        dist = np.linalg.norm(dv, axis=-1)
        j = dist.argmin(axis=1)
        r = np.arange(len(P))
        n = np.cross(b[r, j] - a[r, j], c[r, j] - a[r, j])
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-9)[:, None]
        sgn = np.einsum('ij,ij->i', dv[r, j], n)
        return float(np.where(sgn < 0, dist[r, j], 0.0).sum())

    for race in races:
        # A synthetic shell: that race's own OB head pushed out uniformly,
        # i.e. head gear authored with a constant standoff.
        src = fit.races[race].v
        scalp = src[src[:, 2] > 2.0]
        n = scalp / np.maximum(np.linalg.norm(scalp, axis=1), 1e-6)[:, None]
        shell = scalp + n * 0.8

        human_fit = shell + head_fit.field_deltas(shell, False, race=None)
        race_fit = shell + head_fit.field_deltas(shell, False, race=race)

        assert penetration(race_fit, race) < penetration(human_fit, race), race
