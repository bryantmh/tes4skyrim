"""
What the MorrowindRuntime sidecar takes straight from the TES3 BINARIES.

Three things cannot come from one plugin's text export:

  * DIALOGUE LINKS. Each INFO names the response it follows (PNAM); the
    runtime merges the plugins' lists with OpenMW's own InfoOrder, so each
    sidecar carries only its own responses, links and deletions.
  * THE AUTHORED STRINGS. An NPC's race, class and faction are names in TES3
    and the dialogue filter compares names; the export holds minted FormIDs.
  * THE CLASS AND RACE TABLES an autocalc NPC's stats and services derive from.

So the plugin and every TES3 master that can be found are read here, once
each and in load order.

See: docs/commentary/morrowind_runtime.md#sidecar
"""

import os
import struct

from asset_convert.sources import source_registry
from core.plugin_masters import get_masters_from_binary
from tes4_export.export_morrowind import format_record
from tes4_export.morrowind_patch import PATCH_NAME
from tes4_export.record_types.morrowind_actors import RACE_FORMIDS
from tes4_export.record_types.morrowind_dialog import (DIAL_SIG, INFO_SIG,
                                                       export_DIAL,
                                                       export_INFO, info_id)
from tes4_export.tes3_reader import (get_all_subrecords, get_string,
                                       get_subrecord, read_file)

from ..base.equivalents import (RACE_MAP, SKYRIM_VAMPIRE_RACES,
                                TES4_RACE_FID_TO_EDID)
from .morrowind_travel import take_place
from .morrowind_autocalc import (autocalc_attributes, autocalc_pools, autocalc_skills,
                                 parse_class, parse_race, parse_skill)

#: The 52-byte NPDT: level, 8 attributes, 27 skills, health, magicka, fatigue, disposition, reputation, rank, gold.
_NPDT_FULL = '<h8B27BxHHHBBBxi'

#: The 12-byte autocalc NPDT: level, disposition, reputation, rank, gold.
_NPDT_AUTOCALC = '<hBBB3xi'

#: The 96-byte CREA NPDT: type, level, 8 attributes, health/magicka/fatigue, soul, combat/magic/stealth, 6 attacks, gold.
_CREA_NPDT = '<10i4i3i6ii'
_CREA_LEVEL, _CREA_ATTRIBUTES, _CREA_SPECIALIZED, _CREA_GOLD = 1, slice(2, 10), slice(14, 17), 23

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


def source_binary(root: str, plugin: str):
    """Where `plugin`'s binary is: an imported mod's copy, else a game dir."""
    found = source_registry.plugin_binary(root, plugin)
    if found:
        return str(found)
    folder = source_registry.directory_for(root, plugin)
    return os.path.join(folder, plugin) if folder else None


#: The vanilla TES3 plugins; in Morroblivion mode their data is the compat patch's.
VANILLA_PLUGINS = ('Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm')


def vanilla_chain(root: str) -> list:
    """`(name, path)` for each vanilla TES3 plugin that can be found."""
    found = [(name, source_binary(root, name)) for name in VANILLA_PLUGINS]
    return [(name, path) for name, path in found if path]


def source_chain(root: str, plugin: str) -> list:
    """`plugin_chain`, or the vanilla plugins for the patch that stages them."""
    if os.path.basename(plugin) == PATCH_NAME:
        return vanilla_chain(root)
    return plugin_chain(root, plugin)


def plugin_chain(root: str, plugin: str) -> list:
    """`(name, path)` for each TES3 master that can be found, then `plugin`,
    in load order. A master that is not installed is reported and skipped."""
    own = source_binary(root, plugin)
    if not own:
        return []
    chain = []
    for master in get_masters_from_binary(own):
        path = source_binary(root, master)
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


def _npc_flags(rec) -> int:
    """The NPC_ FLAG word, or 0."""
    flag = get_subrecord(rec, 'FLAG')
    return int.from_bytes(flag.data[:4], 'little') if flag is not None else 0


def _npdt(rec) -> bytes:
    """The NPC_'s NPDT bytes, or b''."""
    data = get_subrecord(rec, 'NPDT')
    return data.data if data is not None else b''


def npc_is_autocalc(rec) -> bool:
    """Whether OpenMW derives this NPC_'s stats: its NPDT is not the 52-byte form."""
    return len(_npdt(rec)) < struct.calcsize(_NPDT_FULL)


def npc_stats(rec, tables: dict, magicka_mult: float = None) -> dict:
    """`{level, disposition, reputation, rank, gold, attributes, skills}` for
    one NPC_: authored from the 52-byte NPDT, else derived from its race and
    class as OpenMW derives them for the 12-byte form, plus the derived
    `health`/`magicka`/`fatigue` when `magicka_mult` (fNPCbaseMagickaMult) is given.

    See: docs/commentary/morrowind_runtime.md#npc-stats
    """
    raw = _npdt(rec)
    if not npc_is_autocalc(rec):
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
        if magicka_mult is not None:
            out.update(autocalc_pools(out['attributes'], clazz, out['level'], magicka_mult))
    return out


def creature_stats(rec, tables: dict) -> dict:
    """`npc_stats`' keys for one CREA, from its NPDT: each skill is the combat,
    magic or stealth value its specialization names, as OpenMW's
    Creature::getSkill reads it."""
    out = {'level': 1, 'disposition': _NEUTRAL_DISPOSITION, 'reputation': 0,
           'rank': 0, 'gold': 0, 'attributes': [0] * 8, 'skills': [0] * 27}
    raw = _npdt(rec)
    if len(raw) < struct.calcsize(_CREA_NPDT):
        return out
    fields = struct.unpack_from(_CREA_NPDT, raw)
    specialized = fields[_CREA_SPECIALIZED]
    out.update(level=fields[_CREA_LEVEL], gold=fields[_CREA_GOLD],
               attributes=list(fields[_CREA_ATTRIBUTES]),
               skills=[specialized[tables['skills'][index]['specialization']]
                       if index in tables['skills'] else 0 for index in range(27)])
    return out


def npc_services(rec, tables: dict) -> int:
    """`Npc::getServices`: the class's services for an autocalc NPC, else
    the AIDT's own."""
    if _npc_flags(rec) & _AUTOCALC:
        clazz = tables['classes'].get(_text(rec, 'CNAM').lower())
        return clazz['services'] if clazz else 0
    return aidt_services(rec)


def aidt_services(rec) -> int:
    """The services an NPC_ or CREA's AIDT authors, or 0."""
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
    alarm|8 attributes|27 skills|creature` for one NPC_ or CREA, the
    attributes and skills comma-joined in TES3's own order. A creature has no
    race, class, faction or sex: its CNAM is its sound creature, and its
    FLAG bits mean other things."""
    creature = rec.type == 'CREA'
    stats = creature_stats(rec, tables) if creature else npc_stats(rec, tables)
    identity = ('', '', '') if creature else tuple(_text(rec, sig) for sig in ('RNAM', 'CNAM', 'ANAM'))
    fields = (*identity,
              stats['rank'], stats['disposition'],
              1 if not creature and _npc_flags(rec) & _FEMALE else 0, _text(rec, 'FNAM'),
              stats['level'], stats['reputation'],
              stats['attributes'][_PERSONALITY], stats['attributes'][_LUCK],
              stats['skills'][_SPEECHCRAFT], stats['skills'][_MERCANTILE],
              aidt_services(rec) if creature else npc_services(rec, tables),
              stats['gold'], *_ai_settings(rec),
              ','.join(str(value) for value in stats['attributes']),
              ','.join(str(value) for value in stats['skills']), 1 if creature else 0)
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


def skyrim_races(race_id: str) -> tuple:
    """The Skyrim race a TES3 race becomes and its vampire race; () if none."""
    edid = TES4_RACE_FID_TO_EDID.get(RACE_FORMIDS.get(race_id.lower(), 0))
    race = RACE_MAP.get(edid) if edid else None
    return (race, SKYRIM_VAMPIRE_RACES[race]) if race in SKYRIM_VAMPIRE_RACES else ()


def race_lines(races: dict) -> dict:
    """`{Skyrim race: 'FORMID=race|8 male|8 female'}`: each TES3 race's starting
    attributes under every Skyrim race a player of it wears.

    See: docs/commentary/morrowind_runtime.md#race-attributes
    """
    out = {}
    for race_id, race in races.items():
        if not race:
            continue
        male, female = (','.join(str(v) for v in race[sex]) for sex in ('male', 'female'))
        for form in skyrim_races(race_id):
            out[form] = f'{form:08X}={race_id}|{male}|{female}'
    return out


#: Records staged as one table line each, by the function that writes it.
_LINE_TABLES = {'GMST': ('gmsts', _gmst_line), 'FACT': ('factions', _faction_line)}

#: Records parsed into the stat tables the autocalc reads.
_STAT_TABLES = {'RACE': ('races', parse_race), 'CLAS': ('classes', parse_class)}


def _take_dial(out: dict, rec, topic: str) -> str:
    """The DIAL/INFO half of `_take`: the plugin's own topics and responses,
    in file order. A deleted topic leaves its responses unowned."""
    if rec.type == 'DIAL':
        topic = '' if rec.deleted else rec.record_id
    if topic:
        out['own_dialogue'].append((topic, rec))
    return topic


def _take_tables(out: dict, rec) -> None:
    """One NPC_ or CREA, RACE, CLAS, GMST or FACT into its table."""
    key = rec.record_id.lower()
    if rec.type in ('NPC_', 'CREA'):
        out['npcs'][key] = rec
        out['own']['npcs'].add(key)
    elif rec.type in _STAT_TABLES:
        table, parse = _STAT_TABLES[rec.type]
        out[table][key] = parse(rec)
        if table in out['own']:
            out['own'][table].add(key)
    elif rec.type in _LINE_TABLES:
        table, line_of = _LINE_TABLES[rec.type]
        line = line_of(rec)
        if line:
            out[table][key] = line
            out['own'][table].add(key)


def _take(out: dict, rec, topic: str) -> str:
    """Fold one record into `out`; returns the topic INFOs now belong to."""
    if rec.type in ('DIAL', 'INFO'):
        return _take_dial(out, rec, topic)
    if rec.type in ('CELL', 'REGN') and not rec.deleted:
        take_place(out, rec)
        if rec.type == 'CELL' and rec.record_id:
            out['cells'].add(rec.record_id.lower())
        return topic
    if rec.type == 'SKIL':
        index, skill = parse_skill(rec)
        if index is not None:
            out['skills'][index] = skill
            out['own']['skills'].add(index)
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
    if rec.type == 'SPEL':
        out['spells'][rec.record_id.lower()] = rec.record_id
    if rec.type == 'SSCR':
        out['start_scripts'][rec.record_id.lower()] = f'{rec.record_id}=1'
    return topic


#: The tables `gather` stages as the plugin's own records alone; a master stages its own.
_OWN_TABLES = ('npcs', 'factions', 'gmsts', 'skills', 'races', 'exteriors')


def _reset_own(out: dict) -> None:
    """Forget which records the plugins read so far defined themselves."""
    out['start_scripts'], out['own_dialogue'] = {}, []
    out['own'] = {table: set() for table in _OWN_TABLES}


def _own_rows(out: dict, table: str) -> dict:
    """`out[table]` cut to the rows the plugin (each plugin, `whole`) defines."""
    return {key: row for key, row in out[table].items()
            if key in out['own'][table]}


#: Record types `gather` keeps whole in `table_records`, for `read_tables`.
TABLE_TYPES = frozenset({'SPEL', 'NPC_', 'CREA', 'RACE', 'CLAS', 'GMST',
                         'SKIL', 'MGEF'})


#: `gather`'s one kept answer, `{(chain, whole): tables}`.
_GATHERED = {}


def gather(chain: list, whole: bool = False) -> dict:
    """`_gather`'s tables, read once per run; each call gets its own copy.

    After `forget_gathered(keep)` only the kept tables remain.
    """
    key = (tuple(chain), whole)
    if key not in _GATHERED:
        _GATHERED.clear()
        _GATHERED[key] = _gather(*key)
    return dict(_GATHERED[key])


def forget_gathered(keep: tuple = ()) -> None:
    """Drop `gather`'s tables but those named in `keep`, once nothing else reads them."""
    for tables in _GATHERED.values():
        for name in set(tables) - set(keep):
            del tables[name]
    if not keep:
        _GATHERED.clear()


def spell_kinds(table_records: list) -> dict:
    """`{lower spell id: SPDT type}` over the chain's SPELs, a later plugin's winning.

    The export folds blight and disease into one TES4 type, so the TES3 type
    is read here. See: docs/commentary/morrowind_runtime.md#disease-queries
    """
    kinds = {}
    for rec in table_records:
        if rec.type != 'SPEL' or not rec.record_id:
            continue
        spdt = get_subrecord(rec, 'SPDT')
        key = rec.record_id.lower()
        if rec.deleted or spdt is None or len(spdt.data) < 4:
            kinds.pop(key, None)
        else:
            kinds[key] = struct.unpack_from('<i', spdt.data)[0]
    return kinds


def _gather(chain: tuple, whole: bool) -> dict:
    """The chain's tables, each plugin read ONCE and a later one overriding
    an earlier: `factions` / `gmsts` `{lower id: line}`, `items` / `objects`
    / `sounds` / `spells` `{lower id: id}`, `cells` lower ids. `npcs` /
    `actors`, `own_factions` / `own_gmsts`, `skills` `{index: line}`,
    `own_dialogue` `[(topic, rec)]` and `start_scripts` are the LAST
    plugin's own alone; `whole` keeps every plugin's. `table_records` is
    the chain's `TABLE_TYPES` records in load order.
    """
    out = {'npcs': {}, 'races': {}, 'classes': {},
           'skills': {}, 'gmsts': {}, 'factions': {}, 'items': {},
           'objects': {}, 'sounds': {}, 'spells': {}, 'exteriors': {},
           'regions': {}, 'cells': set(), 'table_records': []}
    _reset_own(out)
    for _name, path in chain:
        topic = ''
        if not whole:
            _reset_own(out)
        for rec in read_file(path)[1]:
            topic = _take(out, rec, topic)
            if rec.type in TABLE_TYPES:
                out['table_records'].append(rec)
    out['npcs'] = _own_rows(out, 'npcs')
    out['actors'] = {key: _actor_line(rec, out)
                     for key, rec in out['npcs'].items()}
    out['skills'] = {index: _skill_line(index, skill)
                     for index, skill in sorted(_own_rows(out, 'skills').items())}
    out['own_factions'] = _own_rows(out, 'factions')
    out['own_gmsts'] = _own_rows(out, 'gmsts')
    out['own_races'] = race_lines(_own_rows(out, 'races'))
    return out


def _link_lines(rec) -> list:
    """InfoOrder's keys: `Prev` (PNAM), and `Deleted` for a deletion."""
    lines = [f"Prev={_escape(_text(rec, 'PNAM'))}"]
    return lines + ['Deleted=1'] if rec.deleted else lines


def own_dialogue_blocks(gathered: dict) -> tuple:
    """`(DIAL blocks, INFO blocks)` for the plugin's OWN topics and responses,
    each response carrying its links in place of the export's `Ordinal`.

    See: docs/plans/morrowind_object_scripts.md#cumulative-gather-must-go
    """
    dial_blocks, info_blocks, topics = [], [], set()
    for topic, rec in gathered['own_dialogue']:
        if rec.type == 'DIAL':
            if topic.lower() not in topics:
                topics.add(topic.lower())
                dial_blocks.append(format_record(DIAL_SIG, topic,
                                                 export_DIAL(rec)))
            continue
        lines = [line for line in export_INFO(rec, 0, topic)
                 if not line.startswith('Ordinal=')]
        info_blocks.append(format_record(INFO_SIG, info_id(rec),
                                         lines + _link_lines(rec)))
    return dial_blocks, info_blocks
