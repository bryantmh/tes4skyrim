"""Vanilla Skyrim art borrowed for Morrowind magic effects.

TES3 names each effect's casting and hit visuals by the ID of a shared VFX
record -- one set per school plus a few specials -- and tints it per effect
with a particle texture.  Skyrim splits its art the same way, so each VFX ID
names the vanilla effect that does the same job, and the converted effect
takes that effect's art.  The three elemental damage effects outrank their
VFX IDs: TES3 draws them through the generic destruction meshes and tells
them apart only by the particle texture.

See: docs/commentary/tes5_import_magic.md#morrowind-borrowed-art
"""

from ..base.text_reader import get_int, get_str

#: FireDamageFFAimed.
FIRE = 0x00012F03
#: FrostDamageFFAimed.
FROST = 0x0001CEA2
#: ShockDamageFFAimed.
SHOCK = 0x0001CEA8
#: DamageHealthConcSelf, the generic hostile destruction art.
DAMAGE = 0x000F4995
#: AbsorbStaminaConcAimed, the green destruction art.
POISON = 0x000F1D01
#: RestoreHealthFFSelf.
RESTORE = 0x0001CEA6
#: ArmorFFSelf0 (Oakflesh), the generic alteration art.
ALTERATION = 0x00051B15
#: WaterbreathingFFSelf, the airy alteration art.
AIR = 0x0001EA73
#: PerkMasterMindAggDownFFAimed (Calm), the generic illusion art.
ILLUSION = 0x0009E0BB
#: SoulTrapFFActor, the purple art Mysticism's effects share in Skyrim.
MYSTICISM = 0x0004DBA3
#: SummonFlameAtronach.
CONJURE = 0x0001CEAA
#: FireCloakFFSelf.
FIRE_CLOAK = 0x0003AE9E
#: FrostCloakFFSelf.
FROST_CLOAK = 0x0003AEA0
#: ShockCloakFFSelf.
SHOCK_CLOAK = 0x0003AEA1

#: Casting VFX ID (lowercase) -> the vanilla effect whose casting art and light stand in.
CAST_DONORS = {
    'vfx_alterationcast': ALTERATION, 'vfx_alterationarea': ALTERATION,
    'vfx_shieldcast': ALTERATION, 'vfx_levitatecast': AIR,
    'vfx_conjurecast': CONJURE, 'vfx_destructcast': DAMAGE,
    'vfx_frostcast': FROST, 'vfx_lightningcast': SHOCK_CLOAK,
    'vfx_poisoncast': POISON, 'vfx_illusioncast': ILLUSION,
    'vfx_illusionarea': ILLUSION, 'vfx_mysticismcast': MYSTICISM,
    'vfx_restorationcast': RESTORE, 'vfx_fortifycast': RESTORE,
}

#: Hit VFX ID (lowercase) -> the vanilla effect whose hit art and hit shader stand in.
HIT_DONORS = {
    'vfx_alterationhit': ALTERATION, 'vfx_shieldhit': ALTERATION,
    'vfx_levitatehit': AIR, 'vfx_destructhit': DAMAGE,
    'vfx_poisonhit': DAMAGE, 'vfx_corprushit': DAMAGE,
    'vfx_frosthit': FROST, 'vfx_lightninghit': SHOCK,
    'vfx_fireshield': FIRE_CLOAK, 'vfx_frostshield': FROST_CLOAK,
    'vfx_lightningshield': SHOCK_CLOAK, 'vfx_illusionhit': ILLUSION,
    'vfx_mysticismhit': MYSTICISM, 'vfx_soultraphit': MYSTICISM,
    'vfx_restorationhit': RESTORE, 'vfx_curehit': RESTORE,
}

#: Engine effect index of Fire, Shock and Frost Damage -> the element's own vanilla effect.
DAMAGE_DONORS = {14: FIRE, 15: SHOCK, 16: FROST}

#: Every donor, which the vanilla MGEF table must carry.
DONORS = frozenset({*CAST_DONORS.values(), *HIT_DONORS.values(),
                    *DAMAGE_DONORS.values()})


def donors(rec: dict) -> list:
    """[(role, vanilla donor FormID)] for a Morrowind effect's 'cast' and 'hit' art; [] for any other."""
    element = DAMAGE_DONORS.get(get_int(rec, 'MorrowindEffectIndex', -1))
    out = []
    for role, key, table in (('cast', 'MorrowindArt.Cast', CAST_DONORS),
                             ('hit', 'MorrowindArt.Hit', HIT_DONORS)):
        vfx = get_str(rec, key).lower()
        donor = (element or table.get(vfx)) if vfx else 0
        if donor:
            out.append((role, donor))
    return out
