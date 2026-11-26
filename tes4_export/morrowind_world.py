"""
Morrowind exterior geometry: cell grids, quadrants and the worldspace.

Morrowind has no WRLD record and one implicit exterior, located purely by a
cell's grid X/Y. Its cells are 8192 units across where Oblivion's are 4096, so
one Morrowind cell covers a 2x2 block of Oblivion cells.

World POSITIONS are carried across unchanged -- Morroblivion did not scale
them, measured on identical ref extents in both games. Only the grid changes,
and only because the cell size halved.

See: docs/commentary/tes4_export_morrowind.md#coordinates-and-cell-splitting
"""

#: Oblivion/Skyrim exterior cell size in world units.
TES4_CELL_SIZE = 4096

#: Morrowind's exterior cell size in world units: one covers 2x2 Oblivion cells.
TES3_CELL_SIZE = 8192

#: The worldspace every converted Morrowind exterior joins.
WORLDSPACE_EDID = 'WrldMorrowind'


def cell_grid(pos_x: float, pos_y: float) -> tuple:
    """The Oblivion cell grid containing this Morrowind world position.

    See: docs/commentary/tes4_export_morrowind.md#coordinates-and-cell-splitting
    """
    return (int(pos_x // TES4_CELL_SIZE), int(pos_y // TES4_CELL_SIZE))


def quadrant_suffix(grid_x: int, grid_y: int) -> str:
    """Morroblivion's 2-digit suffix for one quarter of a Morrowind cell.

    See: docs/commentary/tes4_export_morrowind.md#coordinates-and-cell-splitting
    """
    return '%02d' % ((grid_x & 1) + 2 * (grid_y & 1))


def cell_editor_id(base_name: str, grid_x: int, grid_y: int) -> str:
    """The EditorID for one Oblivion-sized piece of a Morrowind cell."""
    return '%s%s%d%s%d' % (base_name, 'X' if grid_x >= 0 else 'XN',
                           abs(grid_x), 'Y' if grid_y >= 0 else 'YN',
                           abs(grid_y))


def tes3_cell_quadrants(cell_x: int, cell_y: int):
    """The four Oblivion cell grids a Morrowind cell occupies."""
    return [(cell_x * 2 + dx, cell_y * 2 + dy)
            for dy in (0, 1) for dx in (0, 1)]
