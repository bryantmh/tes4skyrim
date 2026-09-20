"""
Which voice types a converted Morrowind bark is allowed to reach.

A TES3 bark states its speaker as a race plus a gender, and the export leaves
both on the INFO -- `BarkRace` only when the race is one the importer can turn
into a voice type, since every other race exports as Imperial and a race gate
would then put an Ayleid line on every Imperial. Those other lines carry
`GetIsID` conditions naming their real speakers instead, so they need nothing
here.

The gate has to be the VOICE TYPE and never a race condition: `convert_ctda`
rewrites a race param to a vanilla Skyrim race, so a race-gated line bleeds
onto every actor of that race in the load order, while a VTYP is minted by
this plugin and cannot.

See: docs/commentary/tes4_export_morrowind.md#voiced-barks
"""

from ..record_types.common import get_formid


def bark_voice_types(info_rec: dict) -> set:
    """The VTYPs a Morrowind bark is for, from its authored race and sex.

    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    from ..base.equivalents import TES4_RACE_FID_TO_EDID, VOICE_TYPE_MAP
    race = get_formid(info_rec, 'BarkRace')
    if not race:
        return set()
    edid = TES4_RACE_FID_TO_EDID.get(race & 0x00FFFFFF)
    if not edid:
        return set()
    sex = info_rec.get('BarkSex')
    genders = ('Female',) if sex == '1' else (
        ('Male',) if sex == '0' else ('Male', 'Female'))
    return {VOICE_TYPE_MAP[(edid, g)] for g in genders
            if (edid, g) in VOICE_TYPE_MAP}
