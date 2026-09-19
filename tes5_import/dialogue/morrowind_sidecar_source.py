"""
What the MorrowindRuntime sidecar takes straight from the TES3 BINARIES.

Two things cannot come from one plugin's text export:

  * DIALOGUE IS CUMULATIVE. A plugin adds responses to its masters' topics,
    and an NPC answers from the union -- "join the Fighters Guild" is almost
    entirely Morrowind.esm's. Each INFO names the response it follows (PNAM)
    and precedes (NNAM), and the merged order IS the filter precedence.
  * THE AUTHORED STRINGS. An NPC's race, class and faction are names in TES3
    and the dialogue filter compares names; the export holds minted FormIDs.

So the plugin and every TES3 master that can be found are read here, once
each and in load order, and merged the way OpenMW's InfoOrder merges them.

See: docs/commentary/morrowind_runtime.md#sidecar
"""

import os
import struct

from asset_convert.sources import source_registry
from core.plugin_masters import get_masters_from_binary
from tes4_export.export_morrowind import format_record
from tes4_export.record_types.morrowind_dialog import (DIAL_SIG, INFO_SIG,
                                                       export_DIAL,
                                                       export_INFO, info_id)
from tes4_export.tes3_reader import (get_all_subrecords, get_string,
                                       get_subrecord, read_file)

from .morrowind_autocalc import (autocalc_attributes, autocalc_skills,
                                 parse_class, parse_race, parse_skill)

#: The 52-byte NPDT: level, 8 attributes, 27 skills, health, magicka, fatigue, disposition, reputation, rank, gold.
_NPDT_FULL = '<h8B27BxHHHBBBxi'

#: The 12-byte autocalc NPDT: level, disposition, reputation, rank, gold.
_NPDT_AUTOCALC = '<hBBB3xi'

#: TES3 attribute and skill indices the persuasion formula reads.
_PERSONALITY, _LUCK = 6, 7
_MERCANTILE, _SPEECHCRAFT = 24, 25

#: NPC_ FLAG bits: female, and autocalc (services come from the class).
_FEMALE = 0x1
_AUTOCALC = 0x10

#: An unaffiliated stranger's disposition, when no NPDT says otherwise.
_NEUTRAL_DISPOSITION = 50

#: GMST value subrecords and the type letter each is staged under.
_GMST_VALUES = (('STRV', 's'), ('INTV', 'i'), ('FLTV', 'f'))

#: TES3 record types that can sit in an inventory, and every type that can be placed AND carry a script.
_ITEM_TYPES = ('ALCH', 'APPA', 'ARMO', 'BOOK', 'CLOT', 'INGR', 'LIGH',
               'LOCK', 'MISC', 'PROB', 'REPA', 'WEAP')
_OBJECT_TYPES = _ITEM_TYPES + ('NPC_', 'CREA', 'ACTI', 'CONT', 'DOOR')

#: FADT: 2 judged attributes, ten 5-int rank rows, 7 skills, flags.
_FADT_INTS = 60
_FADT_RANKS = 10
_FADT_RANK_AT = 2
_FADT_SKILLS_AT = 52


def _binary(root: str, plugin: str):
    """Where `plugin`'s binary is: an imported mod's copy, else a game dir."""
    found = source_registry.plugin_binary(root, plugin)
    if found:
        return str(found)
    folder = source_registry.directory_for(root, plugin)
    return os.path.join(folder, plugin) if folder else None


def plugin_chain(root: str, plugin: str) -> list:
    """`(name, path)` for each TES3 master that can be found, then `plugin`,
    in load order. A master that is not installed is reported and skipped."""
    own = _binary(root, plugin)
    if not own:
        return []
    chain = []
    for master in get_masters_from_binary(own):
        path = _binary(root, master)
        if path:
            chain.append((master, path))
        else:
            print(f'    sidecar: master {master} not found -- its dialogue '
                  f'and actors are left out')
    return chain + [(plugin, own)]


def _text(rec, sig: str) -> str:
    """A string subrecord, or ''."""
    sub = get_subrecord(rec, sig)
    return get_string(sub) if sub is not None else ''


def _escape(text: str) -> str:
    """The export's escaping, which the runtime's `Unescape` reverses."""
    return (text.replace('\\', '\\\\').replace('\n', '\\n')
            .replace('\r', '\\r').replace('\t', '\\t'))


def _place(order: list, entry: dict) -> None:
    """InfoOrder::insertInfo: replace in place, else after PNAM, else before
    NNAM, else first when it names no predecessor, else last."""
    ids = [row['id'] for row in order]
    if entry['id'] in ids:
        order[ids.index(entry['id'])] = entry
    elif entry['prev'] in ids:
        order.insert(ids.index(entry['prev']) + 1, entry)
    elif entry['next'] in ids:
        order.insert(ids.index(entry['next']), entry)
    elif not entry['prev']:
        order.insert(0, entry)
    else:
        order.append(entry)


def _merge_info(order: list, rec) -> None:
    """One INFO into its topic's order; a deleted one leaves it."""
    entry = {'id': info_id(rec), 'prev': _text(rec, 'PNAM'),
             'next': _text(rec, 'NNAM'), 'rec': rec}
    if rec.deleted:
        order[:] = [row for row in order if row['id'] != entry['id']]
    else:
        _place(order, entry)


def _npc_flags(rec) -> int:
    """The NPC_ FLAG word, or 0."""
    flag = get_subrecord(rec, 'FLAG')
    return int.from_bytes(flag.data[:4], 'little') if flag is not None else 0


def _npc_stats(rec, tables: dict) -> dict:
    """`{level, disposition, reputation, rank, gold, attributes, skills}` for
    one NPC_: authored from the 52-byte NPDT, else derived from its race and
    class as OpenMW derives them for the 12-byte form.

    See: docs/commentary/morrowind_runtime.md#npc-stats
    """
    data = get_subrecord(rec, 'NPDT')
    raw = data.data if data is not None else b''
    if len(raw) >= struct.calcsize(_NPDT_FULL):
        fields = struct.unpack_from(_NPDT_FULL, raw)
        return {'level': fields[0], 'attributes': list(fields[1:9]),
                'skills': list(fields[9:36]), 'disposition': fields[39],
                'reputation': fields[40], 'rank': fields[41],
                'gold': fields[42]}
    out = {'level': 1, 'disposition': _NEUTRAL_DISPOSITION, 'reputation': 0,
           'rank': 0, 'gold': 0, 'attributes': [0] * 8, 'skills': [0] * 27}
    if len(raw) >= struct.calcsize(_NPDT_AUTOCALC):
        level, disposition, reputation, rank, gold = struct.unpack_from(
            _NPDT_AUTOCALC, raw)
        out.update(level=level, disposition=disposition,
                   reputation=reputation, rank=rank, gold=gold)
    race = tables['races'].get(_text(rec, 'RNAM').lower())
    clazz = tables['classes'].get(_text(rec, 'CNAM').lower())
    if race and clazz:
        female = bool(_npc_flags(rec) & _FEMALE)
        out['attributes'] = autocalc_attributes(race, clazz, tables['skills'],
                                                out['level'], female)
        out['skills'] = autocalc_skills(race, clazz, tables['skills'],
                                        out['level'])
    return out


def _npc_services(rec, tables: dict) -> int:
    """`Npc::getServices`: the class's services for an autocalc NPC, else
    the AIDT's own."""
    if _npc_flags(rec) & _AUTOCALC:
        clazz = tables['classes'].get(_text(rec, 'CNAM').lower())
        return clazz['services'] if clazz else 0
    aidt = get_subrecord(rec, 'AIDT')
    if aidt is None or len(aidt.data) < 12:
        return 0
    return struct.unpack_from('<i', aidt.data, 8)[0]


def _ai_settings(rec) -> tuple:
    """The AIDT's authored (hello, fight, flee, alarm), zeros without one."""
    aidt = get_subrecord(rec, 'AIDT')
    if aidt is None or len(aidt.data) < 5:
        return (0, 0, 0, 0)
    return struct.unpack_from('<HBBB', aidt.data, 0)


def _actor_line(rec, tables: dict) -> str:
    """`id=race|class|faction|rank|disposition|female|name|level|reputation|
    personality|luck|speechcraft|mercantile|services|gold|hello|fight|flee|
    alarm` for one NPC_."""
    stats = _npc_stats(rec, tables)
    fields = (_text(rec, 'RNAM'), _text(rec, 'CNAM'), _text(rec, 'ANAM'),
              stats['rank'], stats['disposition'],
              1 if _npc_flags(rec) & _FEMALE else 0, _text(rec, 'FNAM'),
              stats['level'], stats['reputation'],
              stats['attributes'][_PERSONALITY], stats['attributes'][_LUCK],
              stats['skills'][_SPEECHCRAFT], stats['skills'][_MERCANTILE],
              _npc_services(rec, tables), stats['gold'], *_ai_settings(rec))
    return f'{rec.record_id}=' + '|'.join(str(field) for field in fields)


def _faction_line(rec) -> str:
    """`id=attr1,attr2|skills|rows|rank names` for one FACT.

    The rows are what RankRequirement measures the player against; the names
    are what `%PCRank` and `%NextPCRank` print.
    See: docs/commentary/morrowind_runtime.md#rank-requirements
    """
    data = get_subrecord(rec, 'FADT')
    if data is None or len(data.data) < _FADT_INTS * 4:
        return ''
    values = struct.unpack(f'<{_FADT_INTS}i', data.data[:_FADT_INTS * 4])
    rows = []
    for rank in range(_FADT_RANKS):
        at = _FADT_RANK_AT + rank * 5
        rows.append(','.join(str(value) for value in values[at:at + 5]))
    skills = ','.join(
        str(value) for value in values[_FADT_SKILLS_AT:_FADT_SKILLS_AT + 7]
        if value >= 0)
    names = ','.join(get_string(sub).replace(',', ' ').replace('|', ' ')
                     for sub in get_all_subrecords(rec, 'RNAM'))
    return (f'{rec.record_id}={values[0]},{values[1]}|{skills}|'
            + ';'.join(rows) + '|' + names)


def _gmst_line(rec) -> str:
    """`name=type,value` for one GMST, or '' when it carries no value."""
    for sig, kind in _GMST_VALUES:
        sub = get_subrecord(rec, sig)
        if sub is None:
            continue
        if kind == 's':
            return f'{rec.record_id}=s,{_escape(get_string(sub))}'
        if len(sub.data) < 4:
            return ''
        value = struct.unpack_from('<i' if kind == 'i' else '<f', sub.data)[0]
        return f'{rec.record_id}={kind},{value}'
    return ''


def _skill_line(index: int, skill: dict) -> str:
    """`index=attribute|specialization|use0,use1,use2,use3` for one SKIL."""
    uses = ','.join(f'{value:g}' for value in skill['use'])
    return f"{index}={skill['attribute']}|{skill['specialization']}|{uses}"


#: Records staged as one table line each, by the function that writes it.
_LINE_TABLES = {'GMST': ('gmsts', _gmst_line), 'FACT': ('factions', _faction_line)}

#: Records parsed into the stat tables the autocalc reads.
_STAT_TABLES = {'RACE': ('races', parse_race), 'CLAS': ('classes', parse_class)}


def _take_dial(out: dict, rec, topic: str) -> str:
    """The DIAL/INFO half of `_take`, kept separate so neither nests deep."""
    if rec.type == 'DIAL':
        topic = rec.record_id.lower()
        if rec.deleted:
            return topic
        out['topics'][topic] = rec
        out['infos'].setdefault(topic, [])
    elif rec.type == 'INFO' and topic in out['infos']:
        _merge_info(out['infos'][topic], rec)
    return topic


def _take_tables(out: dict, rec) -> None:
    """One NPC_, RACE, CLAS, GMST or FACT into its table."""
    key = rec.record_id.lower()
    if rec.type == 'NPC_':
        out['npcs'][key] = rec
    elif rec.type in _STAT_TABLES:
        table, parse = _STAT_TABLES[rec.type]
        out[table][key] = parse(rec)
    elif rec.type in _LINE_TABLES:
        table, line_of = _LINE_TABLES[rec.type]
        line = line_of(rec)
        if line:
            out[table][key] = line


def _take(out: dict, rec, topic: str) -> str:
    """Fold one record into `out`; returns the topic INFOs now belong to."""
    if rec.type in ('DIAL', 'INFO'):
        return _take_dial(out, rec, topic)
    if rec.type == 'SKIL':
        index, skill = parse_skill(rec)
        if index is not None:
            out['skills'][index] = skill
        return topic
    if not rec.record_id or rec.deleted:
        return topic
    _take_tables(out, rec)
    if rec.type in _OBJECT_TYPES:
        out['objects'][rec.record_id.lower()] = rec.record_id
    if rec.type in _ITEM_TYPES:
        out['items'][rec.record_id.lower()] = rec.record_id
    if rec.type == 'SOUN':
        out['sounds'][rec.record_id.lower()] = rec.record_id
    if rec.type == 'SSCR':
        out['start_scripts'][rec.record_id.lower()] = f'{rec.record_id}=1'
    return topic


def gather(chain: list) -> dict:
    """The chain's tables, each plugin read ONCE and a later one overriding
    an earlier: `topics` `{lower id: DIAL rec}`, `infos` `{lower id: [entry]}`
    in merged order, `actors` / `factions` / `gmsts` `{lower id: line}`,
    `skills` `{index: line}`, `items` / `objects` / `sounds`
    `{lower id: id}`. Actor lines are made LAST, once every race, class and
    skill is known. `start_scripts` is the LAST plugin's SSCR alone: a master
    stages, and so starts, its own.
    """
    out = {'topics': {}, 'infos': {}, 'npcs': {}, 'races': {}, 'classes': {},
           'skills': {}, 'gmsts': {}, 'factions': {}, 'items': {},
           'objects': {}, 'sounds': {}}
    for _name, path in chain:
        topic = ''
        out['start_scripts'] = {}
        for rec in read_file(path)[1]:
            topic = _take(out, rec, topic)
    out['actors'] = {key: _actor_line(rec, out)
                     for key, rec in out['npcs'].items()}
    out['skills'] = {index: _skill_line(index, skill)
                     for index, skill in sorted(out['skills'].items())}
    return out


def write_merged_dialogue(gathered: dict, out_dir: str) -> tuple:
    """DIAL.txt and INFO.txt for the merged chain. Returns their counts."""
    dial_blocks, info_blocks = [], []
    for key, rec in gathered['topics'].items():
        dial_blocks.append(format_record(DIAL_SIG, rec.record_id,
                                         export_DIAL(rec)))
        for ordinal, entry in enumerate(gathered['infos'].get(key, [])):
            info_blocks.append(format_record(
                INFO_SIG, entry['id'],
                export_INFO(entry['rec'], ordinal, rec.record_id)))
    for name, blocks in (('DIAL.txt', dial_blocks), ('INFO.txt', info_blocks)):
        with open(os.path.join(out_dir, name), 'w', encoding='utf-8') as fh:
            fh.write('\n\n'.join(blocks) + '\n')
    return len(dial_blocks), len(info_blocks)
