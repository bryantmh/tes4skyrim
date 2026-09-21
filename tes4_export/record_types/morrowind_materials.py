"""Morrowind surface materials, inferred from authored texture names.

Morrowind records no material anywhere: TES3 LTEX carries only NAME, INTV and
DATA, and its NIFs hold no havok data, so neither the land nor a mesh says what
it is made of. The one authored signal is the texture NAME -- Bethesda's
`tx_<material>_<detail>` scheme, which Tamriel Rebuilt follows.

The enum emitted is TES4's LTEX HNAM.Material, so an exported Morrowind LTEX
joins the same MATT_MAP path Oblivion's already takes.

Match anywhere in the name, never by position: vanilla puts the material second
("tx_wood_siding") but Tamriel Rebuilt prepends a province
("tx_skyrim_wood_brown_03"), and a positional read scores those as "skyrim".

See: docs/commentary/tes4_export_morrowind.md#surface-materials
"""

#: TES4 LTEX HNAM.Material enum values this module emits.
STONE, CLOTH, DIRT, GLASS, GRASS = 0, 1, 2, 3, 4
METAL, ORGANIC, SKIN, WATER, WOOD = 5, 6, 7, 8, 9
HEAVY_STONE, SNOW = 10, 14

#: Finer materials TES4's enum cannot express, named straight to their MATT.
SAND, MUD, GRAVEL, BROKEN_STONE, ICE = -1, -2, -3, -4, -5

#: The MATT FormID for a material with no TES4 enum value.
_DIRECT_MATT = {SAND: 0x00012F3E, MUD: 0x00012F48, GRAVEL: 0x0001C151,
                BROKEN_STONE: 0x00012F35, ICE: 0x00012F47}

#: Substring -> material; the first match wins, so order is specificity.
_RULES = (
    ('snow', SNOW), ('ice', ICE), ('frost', SNOW),
    ('lava', BROKEN_STONE), ('magma', BROKEN_STONE),
    ('rope', ORGANIC), ('web', ORGANIC), ('straw', ORGANIC),
    ('thatch', ORGANIC), ('reed', ORGANIC), ('wicker', ORGANIC),
    ('vine', ORGANIC), ('wax', ORGANIC), ('cork', ORGANIC),
    ('seaweed', ORGANIC), ('needles', ORGANIC), ('pine', ORGANIC),
    ('bark', WOOD), ('wood', WOOD), ('plank', WOOD), ('timber', WOOD),
    ('crate', WOOD), ('barrel', WOOD), ('log', WOOD),
    ('dwrv', METAL), ('dwemer', METAL), ('metal', METAL), ('iron', METAL),
    ('steel', METAL), ('bronze', METAL), ('copper', METAL),
    ('silver', METAL), ('gold', METAL), ('ebony', METAL),
    ('adamantium', METAL), ('rust', METAL), ('chain', METAL),
    ('glass', GLASS), ('crystal', GLASS), ('mirror', GLASS),
    ('cloth', CLOTH), ('fabric', CLOTH), ('silk', CLOTH), ('linen', CLOTH),
    ('canvas', CLOTH), ('rug', CLOTH), ('carpet', CLOTH), ('wool', CLOTH),
    ('banner', CLOTH), ('curtain', CLOTH),
    ('bone', SKIN), ('skeleton', SKIN), ('skull', SKIN), ('chitin', SKIN),
    ('shell', SKIN), ('horn', SKIN), ('leather', SKIN), ('hide', SKIN),
    ('fur', SKIN), ('skin', SKIN), ('flesh', SKIN), ('creature', SKIN),
    ('water', WATER),
    ('mud', MUD), ('muck', MUD), ('mudflat', MUD), ('bank', MUD),
    ('river', MUD), ('ocean', MUD), ('silt', MUD),
    ('sand', SAND), ('ash', SAND), ('beach', SAND), ('dune', SAND),
    ('gravel', GRAVEL), ('pebble', GRAVEL), ('shingle', GRAVEL),
    ('dirt', DIRT), ('road', DIRT), ('soil', DIRT), ('earth', DIRT),
    ('mold', DIRT), ('loam', DIRT), ('field', DIRT), ('till', DIRT),
    ('farmland', DIRT), ('salt', DIRT), ('crackedearth', DIRT),
    ('grass', GRASS), ('moss', GRASS), ('clover', GRASS), ('scrub', GRASS),
    ('undergrowth', GRASS), ('leaves', GRASS), ('lichen', GRASS),
    ('heather', GRASS), ('fern', GRASS), ('shrub', GRASS),
    ('cobble', STONE), ('stone', STONE), ('rock', STONE), ('cliff', STONE),
    ('granite', STONE), ('slate', STONE), ('marble', STONE),
    ('boulder', BROKEN_STONE), ('rubble', BROKEN_STONE), ('mountain', STONE),
    ('adobe', STONE), ('plaster', STONE), ('stucco', STONE),
    ('cavern', STONE), ('cave', STONE), ('brick', STONE), ('paving', STONE),
    ('clay', STONE), ('pottery', STONE), ('ceramic', STONE),
    ('urn', STONE), ('vase', STONE), ('tile', STONE), ('floor', STONE),
)


def material_of(*names) -> int:
    """The material the first classifiable name in `names` implies.

    A negative result is one of the finer materials TES4 cannot name; pass it
    to `matt_formid`. Falls back to STONE, which every Morrowind surface used
    before this module existed, so an unrecognized name is never worse.
    """
    for name in names:
        if not name:
            continue
        stem = str(name).replace('/', chr(92)).rsplit(chr(92), 1)[-1].lower()
        for needle, material in _RULES:
            if needle in stem:
                return material
    return STONE


def matt_formid(material: int) -> int:
    """The Skyrim MATT FormID for a sub-TES4 material, or 0 when it has none."""
    return _DIRECT_MATT.get(material, 0)
