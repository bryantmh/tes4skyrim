"""
Morrowind AI packages: the inline `AI_*` subrecords as TES4 PACK records.

Morrowind stores an actor's packages inline on the actor; TES4 stores them as
PACK records the actor lists in order. Each `AI_*` becomes one PACK with a
derived FormID, emitted after the cells so a travel destination can be placed
as a marker in the cell the actor stands in.

See: docs/commentary/tes4_export_morrowind.md#ai-packages
"""

import struct

from ..morrowind_ids import encode_editor_id
from ..morrowind_world import cell_grid
from ..tes3_reader import Tes3Record, get_subrecord

#: TES4 PKDT.Type per Morrowind package: Find, Follow, Escort, Wander, Travel.
_FIND, _FOLLOW, _ESCORT, _WANDER, _TRAVEL = 0, 1, 2, 5, 6

#: TES4 PLDT.Type near a reference, and near the editor location.
_NEAR_REFERENCE, _NEAR_EDITOR_LOCATION = 0, 3

#: TES4 PTDT.Type for a specific reference, and for any object of a base.
_TARGET_REFERENCE, _TARGET_OBJECT = 0, 1

#: TES4's XMarker, the destination a travel package walks to.
_XMARKER = '0000003B'

#: Morrowind writes FLT_MAX for "no destination" on follow and escort.
_NO_DESTINATION = 1.0e38

#: TES4 record flag for a persistent reference.
_PERSISTENT = 1024


def _tes3_id(raw: bytes) -> str:
    """A fixed 32-byte Morrowind ID field as text."""
    return raw.split(b'\x00', 1)[0].decode('cp1252', 'replace')


def _wander(fields) -> tuple:
    """AI_W: distance, duration, time of day, eight idle chances."""
    return 'wander', {'distance': fields[0], 'duration': fields[1]}


def _travel(fields) -> tuple:
    """AI_T: a destination in cell-local coordinates."""
    return 'travel', {'pos': fields[:3]}


def _follow(fields, kind: str = 'follow') -> tuple:
    """AI_F / AI_E: optional destination, duration and the target actor."""
    pos = None if abs(fields[0]) >= _NO_DESTINATION else fields[:3]
    return kind, {'pos': pos, 'duration': fields[3], 'target': _tes3_id(fields[4])}


def _escort(fields) -> tuple:
    """AI_E shares AI_F's layout."""
    return _follow(fields, 'escort')


def _activate(fields) -> tuple:
    """AI_A: the object to go and activate."""
    return 'activate', {'target': _tes3_id(fields[0])}


#: Subrecord -> (struct layout, parser), for every Morrowind package kind.
_PARSERS = {
    'AI_W': ('<hhB8BB', _wander),
    'AI_T': ('<fffi', _travel),
    'AI_F': ('<fffh32sh', _follow),
    'AI_E': ('<fffh32sh', _escort),
    'AI_A': ('<32sB', _activate),
}


def _packages(rec: Tes3Record) -> list:
    """Every AI package on the actor, in file order, as (kind, fields)."""
    out = []
    for sub in rec.subrecords:
        parser = _PARSERS.get(sub.type)
        if parser is None or len(sub.data) < struct.calcsize(parser[0]):
            continue
        out.append(parser[1](struct.unpack_from(parser[0], sub.data, 0)))
    return out


def package_id(actor_id: str, index: int) -> str:
    """The derivation key of one actor's package."""
    return 'pack:%s:%d' % (actor_id.lower(), index)


def _hello(rec: Tes3Record) -> int:
    """The actor's AIDT Hello setting; 0 means it never greets or chatters.

    See: docs/commentary/tes4_export_morrowind.md#when-a-bark-fires
    """
    aidt = get_subrecord(rec, 'AIDT')
    if aidt is None or len(aidt.data) < 2:
        return 0
    return struct.unpack_from('<H', aidt.data, 0)[0]


def emit_packages(lines: list, rec: Tes3Record, ctx) -> None:
    """List the actor's packages by FormID and queue them for emission."""
    packages = _packages(rec)
    hello = _hello(rec)
    lines.append(f'AIPackageCount={len(packages)}')
    for index, (kind, fields) in enumerate(packages):
        fields['hello'] = hello
        form_id = ctx.derive(package_id(rec.record_id, index))
        lines.append(f'AIPackage[{index}]={form_id}')
        ctx.pending_packages.append((rec.record_id, form_id, kind, fields))


def _schedule(duration: int) -> list:
    """PSDT: any time, for `duration` hours (0 = no limit)."""
    return ['PSDT.Month=-1', 'PSDT.DayOfWeek=-1', 'PSDT.Date=0', 'PSDT.Time=-1',
            f'PSDT.Duration={max(0, duration)}']


def _marker(ctx, actor_id: str, pos: tuple, key: str, out: list) -> str:
    """Place an XMarker at `pos` in the actor's cell; its FormID, or '' when no cell.

    An exterior destination names the grid it falls in; an interior one the
    cell the actor is placed in, because Morrowind's coordinates are cell-local
    and the package lives on the base actor.
    """
    grid = cell_grid(pos[0], pos[1])
    if grid in ctx.known_grids or ctx.index.lookup_exterior(grid):
        cell = ctx.exterior_cell_id(grid)
    else:
        placed = ctx.placements.get(actor_id.lower(), ())
        cell = placed[0][1] if placed else ''
    if not cell:
        return ''
    form_id = ctx.derive(key)
    out.append((form_id, [f'NAME={_XMARKER}', f'ParentCELL={cell}',
                          f'PosX={pos[0]}', f'PosY={pos[1]}', f'PosZ={pos[2]}',
                          'RotX=0.0', 'RotY=0.0', 'RotZ=0.0',
                          f'RecordFlags={_PERSISTENT}']))
    return form_id


def _target_lines(ctx, target_id: str) -> list:
    """PTDT naming the target actor's placed reference, or [] when unplaced."""
    placed = ctx.placements.get(target_id.lower(), ())
    if not placed:
        return []
    return [f'PTDT.Type={_TARGET_REFERENCE}', f'PTDT.Target={placed[0][0]}',
            'PTDT.Count=0']


def _location_lines(marker: str) -> list:
    """PLDT walking to a placed marker."""
    return [f'PLDT.Type={_NEAR_REFERENCE}', f'PLDT.Location={marker}',
            'PLDT.Radius=0']


def _package_lines(ctx, actor_id: str, form_id: str, kind: str, fields: dict,
                   markers: list):
    """The TES4 PACK lines for one queued package, or None when it cannot resolve."""
    lines = [f'EditorID={encode_editor_id(actor_id)}Pack{form_id[-6:]}',
             'PKDT.Flags=0', f'MorrowindHello={fields.get("hello", 0)}']
    if kind == 'wander':
        lines += [f'PKDT.Type={_WANDER}', f'PLDT.Type={_NEAR_EDITOR_LOCATION}',
                  'PLDT.Location=0', f'PLDT.Radius={max(0, fields["distance"])}']
        return lines + _schedule(fields['duration'])
    if kind == 'travel':
        marker = _marker(ctx, actor_id, fields['pos'], 'xmarker:' + form_id, markers)
        if not marker:
            return None
        return lines + [f'PKDT.Type={_TRAVEL}'] + _location_lines(marker) + _schedule(0)
    if kind == 'activate':
        target = ctx.resolve(fields['target'])
        if not target:
            return None
        return lines + [f'PKDT.Type={_FIND}', f'PTDT.Type={_TARGET_OBJECT}',
                        f'PTDT.Target={target}', 'PTDT.Count=1'] + _schedule(0)
    target = _target_lines(ctx, fields['target'])
    if not target:
        return None
    lines += [f'PKDT.Type={_ESCORT if kind == "escort" else _FOLLOW}'] + target
    if fields['pos'] is not None:
        marker = _marker(ctx, actor_id, fields['pos'], 'xmarker:' + form_id, markers)
        if marker:
            lines += _location_lines(marker)
    return lines + _schedule(fields['duration'])


def package_records(ctx) -> tuple:
    """(PACK records, marker REFRs) for every queued package; unresolvable ones are counted.

    Records the FormIDs that failed to resolve in `ctx.dropped_packages`, so
    the actors listing them can drop the reference too.
    """
    packs, markers = [], []
    for actor_id, form_id, kind, fields in ctx.pending_packages:
        lines = _package_lines(ctx, actor_id, form_id, kind, fields, markers)
        if lines is None:
            ctx.unresolved['package:' + kind] += 1
            ctx.dropped_packages.add(form_id)
            continue
        packs.append((form_id, lines))
    return packs, markers


def prune_dropped_packages(records: list, ctx) -> None:
    """Drop every AIPackage line naming a package that never emitted, in place.

    An actor listing a PACK no record defines makes the engine resolve a form
    that does not exist; it warns per actor at load and the count is rewritten
    so the surviving indices stay contiguous.
    See: docs/commentary/tes4_export_morrowind.md#ai-packages
    """
    if not ctx.dropped_packages:
        return
    for _, lines in records:
        kept = [line[line.index('=') + 1:] for line in lines
                if line.startswith('AIPackage[')]
        if not kept:
            continue
        kept = [fid for fid in kept if fid not in ctx.dropped_packages]
        lines[:] = [line for line in lines
                    if not line.startswith(('AIPackage[', 'AIPackageCount='))]
        lines.append(f'AIPackageCount={len(kept)}')
        lines.extend(f'AIPackage[{i}]={fid}' for i, fid in enumerate(kept))
