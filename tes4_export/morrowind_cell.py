"""
Morrowind CELL parsing: the cell itself and the references stored inside it.

Morrowind has no REFR record. A cell's placed references follow its header
fields as repeating runs that each begin with FRMR, and NAME and DATA occur in
both halves, so the run must be split positionally rather than by scanning for
subrecord names.

See: docs/commentary/tes4_export_morrowind.md#cell-references
"""

import struct
from dataclasses import dataclass, field

from .tes3_reader import Tes3Record, get_string

#: CELL.DATA flags.
FLAG_INTERIOR = 0x01
FLAG_HAS_WATER = 0x02
FLAG_QUASI_EXTERIOR = 0x80

#: A lock level Morrowind stores for "locked, but at level zero".
_ZERO_LOCK = 0x7FFFFFFF


@dataclass
class CellRef:
    """One placed reference from inside a CELL."""
    ref_num: int = 0
    record_id: str = ''
    pos: tuple = (0.0, 0.0, 0.0)
    rot: tuple = (0.0, 0.0, 0.0)
    scale: float = 1.0
    owner: str = ''
    faction: str = ''
    faction_rank: int = -1
    key: str = ''
    trap: str = ''
    soul: str = ''
    lock_level: int = 0
    count: int = 0
    charge: float = None
    dest_cell: str = ''
    dest_pos: tuple = None
    dest_rot: tuple = None
    teleport: bool = False
    blocked: bool = False
    deleted: bool = False


@dataclass
class Cell:
    """A Morrowind cell: its own fields plus every reference it holds."""
    name: str = ''
    flags: int = 0
    grid: tuple = (0, 0)
    region: str = ''
    map_color: int = None
    water_height: float = None
    ambient: tuple = None
    refs: list = field(default_factory=list)

    @property
    def interior(self) -> bool:
        """True when this cell is an interior rather than part of the world."""
        return bool(self.flags & FLAG_INTERIOR)


def parse_cell(rec: Tes3Record) -> Cell:
    """A CELL record split into its header fields and its references."""
    cell = Cell()
    ref = None
    seen_data = False
    for sub in rec.subrecords:
        if sub.type == 'FRMR':
            ref = CellRef(ref_num=int.from_bytes(sub.data[:4], 'little'))
            cell.refs.append(ref)
            continue
        if ref is None:
            seen_data = _read_header_sub(cell, sub, seen_data)
        else:
            _read_ref_sub(ref, sub)
    return cell


def _read_header_sub(cell: Cell, sub, seen_data: bool) -> bool:
    """Apply one pre-FRMR subrecord; return whether DATA has been seen.

    Only the FIRST DATA is the cell's own -- later ones belong to references.
    """
    if sub.type == 'DATA':
        if seen_data or len(sub.data) < 12:
            return seen_data
        flags, grid_x, grid_y = struct.unpack_from('<iii', sub.data, 0)
        cell.flags = flags
        cell.grid = (grid_x, grid_y)
        return True
    handler = _CELL_HANDLERS.get(sub.type)
    if handler is not None:
        handler(cell, sub)
    return seen_data


def _cell_map_color(cell, sub):
    """Set the color this cell draws on the world map."""
    if len(sub.data) >= 4:
        cell.map_color = struct.unpack_from('<i', sub.data, 0)[0]


def _cell_water_float(cell, sub):
    """Set the water height from an interior's float form."""
    if len(sub.data) >= 4:
        cell.water_height = struct.unpack_from('<f', sub.data, 0)[0]


def _cell_water_int(cell, sub):
    """Set the water height from an interior's older integer form."""
    if len(sub.data) >= 4:
        cell.water_height = float(struct.unpack_from('<i', sub.data, 0)[0])


def _cell_ambient(cell, sub):
    """Set the interior ambient/sunlight/fog colors and fog density."""
    if len(sub.data) >= 16:
        cell.ambient = struct.unpack_from('<IIIf', sub.data, 0)


_CELL_HANDLERS = {
    'NAME': lambda c, s: setattr(c, 'name', get_string(s)),
    'RGNN': lambda c, s: setattr(c, 'region', get_string(s)),
    'NAM5': _cell_map_color,
    'WHGT': _cell_water_float,
    'INTV': _cell_water_int,
    'AMBI': _cell_ambient,
}


def _read_ref_sub(ref: CellRef, sub) -> None:
    """Apply one subrecord to the reference currently being built."""
    handler = _REF_HANDLERS.get(sub.type)
    if handler is not None:
        handler(ref, sub)


def _ref_name(ref, sub):
    """Set the base object this reference places."""
    ref.record_id = get_string(sub)


def _ref_data(ref, sub):
    """Set position and rotation; rotation is radians in both games."""
    if len(sub.data) >= 24:
        vals = struct.unpack_from('<6f', sub.data, 0)
        ref.pos, ref.rot = vals[0:3], vals[3:6]


def _ref_scale(ref, sub):
    """Set scale, clamped to the range Morrowind itself enforces."""
    if len(sub.data) >= 4:
        ref.scale = min(max(struct.unpack_from('<f', sub.data, 0)[0], 0.5), 2.0)


def _ref_dodt(ref, sub):
    """Set the teleport destination and mark the reference a door."""
    if len(sub.data) >= 24:
        vals = struct.unpack_from('<6f', sub.data, 0)
        ref.dest_pos, ref.dest_rot = vals[0:3], vals[3:6]
        ref.teleport = True


def _ref_lock(ref, sub):
    """Set the lock level, folding Morrowind's zero-lock sentinel to 0."""
    if len(sub.data) >= 4:
        level = struct.unpack_from('<i', sub.data, 0)[0]
        ref.lock_level = 0 if level == _ZERO_LOCK else level


def _ref_count(ref, sub):
    """Set the stack count for a placed item."""
    if len(sub.data) >= 4:
        ref.count = struct.unpack_from('<i', sub.data, 0)[0]


def _ref_rank(ref, sub):
    """Set the owning faction rank."""
    if len(sub.data) >= 4:
        ref.faction_rank = struct.unpack_from('<i', sub.data, 0)[0]


def _ref_charge(ref, sub):
    """Set the remaining enchantment charge."""
    if len(sub.data) >= 4:
        ref.charge = struct.unpack_from('<f', sub.data, 0)[0]


_REF_HANDLERS = {
    'NAME': _ref_name,
    'DATA': _ref_data,
    'XSCL': _ref_scale,
    'DODT': _ref_dodt,
    'DNAM': lambda r, s: setattr(r, 'dest_cell', get_string(s)),
    'FLTV': _ref_lock,
    'KNAM': lambda r, s: setattr(r, 'key', get_string(s)),
    'TNAM': lambda r, s: setattr(r, 'trap', get_string(s)),
    'ANAM': lambda r, s: setattr(r, 'owner', get_string(s)),
    'CNAM': lambda r, s: setattr(r, 'faction', get_string(s)),
    'XSOL': lambda r, s: setattr(r, 'soul', get_string(s)),
    'INDX': _ref_rank,
    'NAM9': _ref_count,
    'XCHG': _ref_charge,
    'UNAM': lambda r, s: setattr(r, 'blocked', True),
    'DELE': lambda r, s: setattr(r, 'deleted', True),
}
