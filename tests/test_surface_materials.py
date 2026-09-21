"""Surface materials: the TES4 MATT table and Morrowind's inferred materials.

Every test builds its inputs by hand, so none needs a game installed. The
invariants are recorded in docs/commentary/tes4_export_morrowind.md and
docs/commentary/tes5_import_landscape.md.
"""

from tes4_export.record_types.morrowind_materials import (
    DIRT, GRASS, GRAVEL, ICE, METAL, MUD, SAND, SNOW, STONE, WOOD,
    material_of, matt_formid)
from tes5_import.base.constants import MATT_MAP

#: Vanilla Skyrim MATT FormIDs, from references/Skyrim.esm/MATT.txt.
_MATT = {
    'Stone': 0x00012F34, 'BrokenStone': 0x00012F35,
    'HeavyStone': 0x00012F36, 'Cloth': 0x00012F37, 'Dirt': 0x00012F38,
    'Glass': 0x00012F39, 'ChainMetal': 0x00012F3A, 'HeavyMetal': 0x00012F3B,
    'SolidMetal': 0x00012F3C, 'Organic': 0x00012F3D, 'Sand': 0x00012F3E,
    'Skin': 0x00012F3F, 'Water': 0x00012F40, 'WoodHeavy': 0x00012F42,
    'Snow': 0x00012F45, 'Grass': 0x00012F46, 'Ice': 0x00012F47,
    'Mud': 0x00012F48, 'Gravel': 0x0001C151, 'WoodMedium': 0x00043DCC,
    'StairsStone': 0x0002EE2C, 'StairsWood': 0x0002EE2D,
    'StairsGlass': 0x000C6FB1, 'StairsSnow': 0x00052ED0,
}


def test_matt_map_matches_tes4_enum_order():
    """Each TES4 index resolves to the MATT of the SAME material.

    The shipped table was shifted: index 2 (Dirt) wrote MaterialChainMetal and
    index 4 (Grass) wrote MaterialHeavyMetal.
    """
    expected = {
        0: _MATT['Stone'], 1: _MATT['Cloth'], 2: _MATT['Dirt'],
        3: _MATT['Glass'], 4: _MATT['Grass'], 5: _MATT['SolidMetal'],
        6: _MATT['Organic'], 7: _MATT['Skin'], 8: _MATT['Water'],
        9: _MATT['WoodMedium'], 10: _MATT['HeavyStone'],
        11: _MATT['HeavyMetal'], 12: _MATT['WoodHeavy'],
        13: _MATT['ChainMetal'], 14: _MATT['Snow'],
    }
    for index, formid in expected.items():
        assert MATT_MAP[index] == formid, f'index {index}'


def test_matt_map_covers_every_tes4_index():
    """All 32 TES4 material values map, so no LTEX silently falls back."""
    assert set(MATT_MAP) == set(range(32))


def test_matt_map_stairs_keep_their_material():
    """Skyrim has 4 stairs materials to TES4's 15, so they group by material."""
    expected = {15: _MATT['StairsStone'], 24: _MATT['StairsWood'],
                18: _MATT['StairsGlass'], 29: _MATT['StairsSnow']}
    for index, formid in expected.items():
        assert MATT_MAP[index] == formid, f'index {index}'


def test_material_is_found_anywhere_in_the_name():
    """Tamriel Rebuilt prepends a province, so position cannot be assumed."""
    assert material_of('tx_wood_siding.tga') == WOOD
    assert material_of('tx_skyrim_wood_brown_03.dds') == WOOD
    assert material_of('textures/tr/arc/tr_skyrim_wood_brown_03.dds') == WOOD


def test_editor_id_beats_the_file_name():
    """LTEX passes its authored NAME first; the file name is the tiebreak."""
    assert material_of('Road Dirt', 'Tx_dirtroad_01.dds') == DIRT
    assert material_of('Sand', 'Tx_sand_01.dds') == SAND
    assert material_of('', 'Tx_BM_snow_01.dds') == SNOW


def test_unclassifiable_name_keeps_the_stone_fallback():
    """An unknown surface is never worse than the old hardcoded behaviour."""
    assert material_of('') == STONE
    assert material_of('tx_hlaalu_wall2_01.dds') == STONE
    assert material_of(None, '') == STONE


def test_vanilla_land_textures_classify():
    """Real Morrowind.esm LTEX EditorIDs, with the material each implies."""
    cases = {'Rock_Coastal': STONE, 'AI_Grass_Cobbles': GRASS,
             'AL_ash_01': SAND, 'MA_crackedearth': DIRT,
             'WG_cobblestones': STONE, 'AI_mudflats_01.tga': MUD,
             'AC_darkgravel': GRAVEL, 'Tx_BM_ice_05.dds': ICE}
    for edid, expected in cases.items():
        assert material_of(edid) == expected, edid


def test_sub_tes4_materials_name_their_matt_directly():
    """Sand, Mud, Gravel and Ice have no TES4 enum value to route through."""
    assert matt_formid(SAND) == _MATT['Sand']
    assert matt_formid(MUD) == _MATT['Mud']
    assert matt_formid(GRAVEL) == _MATT['Gravel']
    assert matt_formid(ICE) == _MATT['Ice']


def test_tes4_expressible_materials_have_no_direct_matt():
    """A material the enum CAN name routes through MATT_MAP, not around it."""
    for material in (STONE, DIRT, GRASS, WOOD, METAL, SNOW):
        assert matt_formid(material) == 0
        assert material in MATT_MAP
