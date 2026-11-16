"""A TES4 source's character-sheet tables, in the Morrowind sidecar's formats.

MorrowindRuntime's sheet reads every folder under `SKSE/Plugins/MorrowindRuntime/`.
A TES4 plugin stages there what the sheet needs from it, keyed as the Morrowind
tables are so one reader serves every game: SKIL rows under the namesake TES3
skill, RACE rows under the Skyrim races a player of the race wears, NPC_ and
CREA rows under the EditorID, and the player attribute globals it owns.

See: docs/commentary/morrowind_runtime.md#tes4-tables
"""

import os

from script_convert.message_menus import (CHARGEN_BIRTHSIGN_GLOBAL, CHARGEN_CLASS_GLOBAL,
                                          table_field)
from tes4_export.record_types.morrowind import MW_SKILL_TO_TES4

from ..base.equivalents import RACE_MAP, SKYRIM_VAMPIRE_RACES
from ..packages.escort_when_near import ESCORT_HOLD_EDID, player_escorts
from ..base.owned_records import (CHARGEN_REQUEST_GLOBAL, DIALOGUE_OPEN_GLOBAL, PLAYER_ATTRIBUTE_GLOBALS,
                                  TES4_ATTRIBUTE_NAMES, WELL_KNOWN_PROPERTIES, owner_row)
from ..base.race_lookup import tes4_race_edid
from ..base.text_reader import get_float, get_int, get_str
from ..dialogue.morrowind_sidecar import (ACTORS_TABLE, RACES_TABLE, SIDECAR_DIR,
                                          SKILLS_TABLE, is_tes3_export, plugin_stem)
from ..dialogue.morrowind_teleport import TELEPORTS_TABLE, copy_rows
from ..record_types.crime import bounty_rows
from .misc_stats import stat_lines
from .stat_factions import faction_rows

#: Player attribute globals `strength=Plugin.esm|FormID`, then stat factions `faction.<TES4 av>=...`.
ATTRIBUTES_TABLE = 'attributes_formid.txt'

#: The class and birthsign menus' rows and globals, by menu index.
CHARGEN_TABLE = 'chargen.txt'

#: The Statistics tab's standing globals, misc stats and their labels.
STATS_TABLE = 'stats.txt'

#: TES4 skill index (actor value - 12) -> its namesake TES3 skill index.
TES4_SKILL_TO_MW = {0: 1, 1: 8, 2: 5, 3: 0, 4: 4, 5: 26, 6: 3, 7: 16, 8: 11, 9: 13,
                    10: 10, 11: 12, 12: 14, 13: 15, 14: 20, 15: 21, 16: 23, 17: 24,
                    18: 18, 19: 19, 20: 25}

#: The 21 skills in TES4 index order, as NPC_ DATA names them.
SKILL_NAMES = ('Armorer', 'Athletics', 'Blade', 'Block', 'Blunt', 'HandToHand',
               'HeavyArmor', 'Alchemy', 'Alteration', 'Conjuration', 'Destruction',
               'Illusion', 'Mysticism', 'Restoration', 'Acrobatics', 'LightArmor',
               'Marksman', 'Mercantile', 'Security', 'Sneak', 'Speechcraft')

#: CREA DATA's skill per specialization (Combat, Magic, Stealth).
_CREATURE_SKILLS = ('CombatSkill', 'MagicSkill', 'StealthSkill')

#: TES4 skills' first actor value; RACE DATA.Flags Playable; ACBS.Flags Female.
_FIRST_SKILL_AV, _PLAYABLE, _FEMALE = 12, 0x1, 0x1

#: TES3 indices of the NPC_ row's own Speechcraft and Mercantile columns.
_MW_SPEECHCRAFT, _MW_MERCANTILE = 25, 24


def skill_lines(skills: list) -> tuple:
    """(`{TES4 index: specialization}`, SKIL.txt rows `m=attribute|specialization|use1,use2`)."""
    specs, lines = {}, []
    for rec in skills:
        tes4 = get_int(rec, 'DATA.Action', -1) - _FIRST_SKILL_AV
        if tes4 not in TES4_SKILL_TO_MW:
            continue
        specs[tes4] = get_int(rec, 'DATA.Specialization')
        uses = ','.join(f"{get_float(rec, f'DATA.UseValue{n}'):g}" for n in (1, 2))
        lines.append(f"{TES4_SKILL_TO_MW[tes4]}={get_int(rec, 'DATA.Attribute')}|"
                     f"{specs[tes4]}|{uses}")
    return specs, sorted(lines, key=lambda line: int(line.split('=')[0]))


def _attributes(rec: dict, prefix: str) -> str:
    """The eight attributes under `prefix`, comma-joined in TES3 order."""
    return ','.join(str(get_int(rec, f'{prefix}{name}')) for name in TES4_ATTRIBUTE_NAMES)


def race_lines(races: list) -> list:
    """RACE.txt rows `FORMID=race|8 male|8 female` for each playable race, under its
    Skyrim race and that race's vampire; a race Oblivion knows wins over a stand-in."""
    rows = {}
    for rec in sorted(races, key=lambda r: tes4_race_edid(int(r['FormID'], 16)) is None):
        edid = tes4_race_edid(int(rec['FormID'], 16))
        race = RACE_MAP.get(edid) if get_int(rec, 'DATA.Flags') & _PLAYABLE else None
        if race not in SKYRIM_VAMPIRE_RACES:
            continue
        value = (f"{get_str(rec, 'EditorID')}|{_attributes(rec, 'ATTR.Male.')}|"
                 f"{_attributes(rec, 'ATTR.Female.')}")
        for form in (race, SKYRIM_VAMPIRE_RACES[race]):
            rows.setdefault(form, f'{form:08X}={value}')
    return [rows[form] for form in sorted(rows)]


def _mw_skills(value_of) -> list:
    """The 27 TES3 skill slots, each its TES4 fold's value; Enchant 0."""
    return [value_of(MW_SKILL_TO_TES4[m]) if MW_SKILL_TO_TES4[m] < len(SKILL_NAMES) else 0
            for m in range(len(MW_SKILL_TO_TES4))]


def _actor_line(rec: dict, skills: list) -> str:
    """One NPC_.txt row: identity, level, attributes and skills; the AI and
    faction columns TES4 authors differently stay 0."""
    attributes = _attributes(rec, 'DATA.')
    race = tes4_race_edid(int(get_str(rec, 'RNAM.Race', '0') or '0', 16), '')
    female = get_int(rec, 'ACBS.Flags') & _FEMALE
    fields = (race, '', '', 0, 50, 1 if female else 0, get_str(rec, 'FULL'),
              get_int(rec, 'ACBS.Level'), 0, get_int(rec, 'DATA.Personality'),
              get_int(rec, 'DATA.Luck'), skills[_MW_SPEECHCRAFT], skills[_MW_MERCANTILE],
              0, 0, 0, 0, 0, 0, attributes, ','.join(str(v) for v in skills))
    return f"{get_str(rec, 'EditorID')}=" + '|'.join(str(f).replace('|', ' ') for f in fields)


def actor_lines(npcs: list, creatures: list, specs: dict) -> list:
    """NPC_.txt rows for every NPC_ and CREA with an EditorID. A creature's skill is
    its Combat, Magic or Stealth skill by the skill's specialization, as OpenMW reads one."""
    lines = []
    for rec in npcs:
        if get_str(rec, 'EditorID'):
            lines.append(_actor_line(rec, _mw_skills(
                lambda t, r=rec: get_int(r, f'DATA.{SKILL_NAMES[t]}'))))
    for rec in creatures:
        if get_str(rec, 'EditorID'):
            lines.append(_actor_line(rec, _mw_skills(
                lambda t, r=rec: get_int(r, f'DATA.{_CREATURE_SKILLS[specs.get(t, 0)]}'))))
    return lines


def global_lines(plugin: str, masters: list) -> list:
    """attributes_formid.txt rows for the attribute globals this plugin itself holds, and its player escorts.

    See: docs/commentary/tes5_import_package.md#escort-restarts-when-the-target-returns
    """
    rows = []
    names = [name.lower() for name in TES4_ATTRIBUTE_NAMES] + ['dialogueopen']
    for name, edid in zip(names, list(PLAYER_ATTRIBUTE_GLOBALS) + [DIALOGUE_OPEN_GLOBAL]):
        fid = WELL_KNOWN_PROPERTIES.get(edid, 0)
        if fid and fid >> 24 == len(masters):
            rows.append(f'{name}={plugin}|{fid:08X}')
    escorts = [f'escortplayer={plugin}|{fid:08X}' for fid in player_escorts() if fid >> 24 == len(masters)]
    hold = WELL_KNOWN_PROPERTIES.get(ESCORT_HOLD_EDID, 0)
    if escorts and hold:
        rows += escorts + [owner_row('escorthold', hold, plugin, masters)]
    return rows


def chargen_lines(plan: dict, plugin: str, masters: list) -> list:
    """chargen.txt rows: the request and choice globals, then the plugin's own
    `class.<i>=name` and `sign.<i>=name` in its menu order. The menus' rows are
    the shared table's (core.chargen_source)."""
    lines = []
    for row, edid in (('form.request', CHARGEN_REQUEST_GLOBAL),
                      ('form.class', CHARGEN_CLASS_GLOBAL),
                      ('form.birthsign', CHARGEN_BIRTHSIGN_GLOBAL)):
        fid = WELL_KNOWN_PROPERTIES.get(edid, 0)
        if fid:
            lines.append(owner_row(row, fid, plugin, masters))
    for kind, key in (('class', 'class'), ('sign', 'birthsign')):
        lines += [f"{kind}.{i}={table_field(row['name'])}"
                  for i, row in enumerate(plan.get(key, {}).get('rows', ()))]
    return lines


def write_stats_table(by_type: dict, writer, output_path: str, export_dir: str) -> int:
    """Stage the Statistics tab's rows: a TES4 plugin's standing, stats, page and
    labels (misc_stats), and every plugin's own realms' bounties; 1 if written.

    See: docs/commentary/morrowind_runtime.md#statistics-tab
    """
    plugin = os.path.basename(output_path)
    out_dir = os.path.join(os.path.dirname(output_path), SIDECAR_DIR, plugin_stem(plugin))
    lines = bounty_rows(plugin, writer.masters)
    if not is_tes3_export(export_dir):
        lines += stat_lines(by_type, plugin, writer.masters)
    if lines:
        os.makedirs(out_dir, exist_ok=True)
    return _write(os.path.join(out_dir, STATS_TABLE), lines)


def write_chargen_table(plan: dict, writer, output_path: str) -> int:
    """Stage the chargen menus' table for MorrowindRuntime; 1 if written.

    See: docs/commentary/morrowind_runtime.md#chargen-menus
    """
    plugin = os.path.basename(output_path)
    out_dir = os.path.join(os.path.dirname(output_path), SIDECAR_DIR, plugin_stem(plugin))
    lines = chargen_lines(plan, plugin, writer.masters) if plan else []
    if lines:
        os.makedirs(out_dir, exist_ok=True)
    return _write(os.path.join(out_dir, CHARGEN_TABLE), lines)


def _write(path: str, lines: list) -> int:
    """Write a table, or delete a stale one when there is nothing to write; 1 if written."""
    if not lines:
        if os.path.isfile(path):
            os.remove(path)
        return 0
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    return 1


def write_attribute_tables(by_type: dict, writer, output_path: str) -> int:
    """Stage this TES4 plugin's own SKIL, RACE, NPC_/CREA, global, attribute-effect
    and statistics tables; files written."""
    plugin = os.path.basename(output_path)
    out_dir = os.path.join(os.path.dirname(output_path), SIDECAR_DIR, plugin_stem(plugin))
    specs, skills = skill_lines(by_type.get('SKIL', []))
    tables = {SKILLS_TABLE: skills, RACES_TABLE: race_lines(by_type.get('RACE', [])),
              ACTORS_TABLE: actor_lines(by_type.get('NPC_', []), by_type.get('CREA', []), specs),
              ATTRIBUTES_TABLE: (global_lines(plugin, writer.masters)
                                 + faction_rows(plugin, writer.masters)),
              TELEPORTS_TABLE: copy_rows(plugin, len(writer.masters))}
    if not any(tables.values()) and not os.path.isdir(out_dir):
        return 0
    os.makedirs(out_dir, exist_ok=True)
    written = sum(_write(os.path.join(out_dir, table), lines) for table, lines in tables.items())
    print(f'  Attribute tables: {len(skills)} skills, {len(tables[RACES_TABLE])} race rows, '
          f'{len(tables[ACTORS_TABLE])} actors, {len(tables[ATTRIBUTES_TABLE])} globals, '
          f'{len(tables[TELEPORTS_TABLE])} attribute effects')
    return written
