"""FO3/FNV biped bits as the converter reads them: body parts and gender.

FO3/FNV shares only bits 0-2 with Oblivion's BMDT, so reading its flags
through the Oblivion table turns a glove into a lower-body piece, a backpack
into a ring and an earring into a shield.  The table below mirrors the
importer's FNV_BIPED_SLOT_MAP: each body part is that slot number, and head
gear takes the converter's hair partition 131.

See: docs/commentary/tes4_export_falloutnv.md#fnv-biped-slots
"""

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert.collision.collision_falloutnv import is_fallout_source

#: FO3/FNV BMDT bit -> Skyrim body part; head-ward entries first so they win.
FNV_BIPED_BIT_BODY_PART = [
    (0, 131), (1, 131), (10, 131), (14, 131),
    (9, 42), (11, 42),
    (12, 43), (13, 43), (16, 43),
    (2, 32),
    (3, 33), (4, 59),
    (6, 34),
    (7, 46),
    (8, 35), (15, 35),
    (17, 47), (18, 48), (19, 60),
]


def biped_bit_body_parts(oblivion_table: list) -> list:
    """The source game's (biped bit, body part) table."""
    return FNV_BIPED_BIT_BODY_PART if is_fallout_source() else oblivion_table


def shield_flags(biped_flags: int) -> bool:
    """Oblivion bit 13 is Shield; FO3/FNV has no shield slot (13 is Earrings)."""
    return bool(biped_flags & (1 << 13)) and not is_fallout_source()
