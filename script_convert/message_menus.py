"""TES4 multi-button MessageBox → Skyrim MESG menu plan.

Oblivion scripts drive every in-world choice menu with

    MessageBox "Would you like a blessing?" "Yes" "No"
    ...
    set button to GetButtonPressed          ; polled from GameMode

Skyrim has no dynamic message boxes: buttons live on an authored MESG record
and `Message.Show()` parks its calling thread until the player clicks, then
returns the button index. So every button-carrying MessageBox call site needs
a MESG record in the output plugin, and the script needs the Show()/consume
idiom instead of the poll.

This module is the SHARED analysis both sides run so they agree exactly:

  * the importer (tes5_import.pipeline) builds the plan and writes one MESG
    per call site — EDID `TES4Msg_<Script>_<NN>`, DESC = the message text,
    ITXT per button — then registers each EDID in _WELL_KNOWN_PROPERTIES so
    the VMAD property pass can bind them;
  * the script pipeline (script_convert.pipeline) ships the plan to its
    workers, and the converter emits `TES4_MsgButton = TES4Msg_X_01.Show()`
    at the call site plus a consume-on-read helper for GetButtonPressed
    (TES4 semantics: the clicked index is returned once, then -1 again).

Sites are numbered in SOURCE order within each script, but the converter can
process blocks out of source order (MenuMode merges into the GameMode poll),
so it matches sites by (text, buttons) content and takes the next unused name
for that content — identical text twice in one script yields _01 then _02 in
either walk order.

A MessageBox with only its message string (no buttons) stays a
Debug.MessageBox, and a GetButtonPressed in a script that shows no button box
of its own (a handful poll a box some OTHER script showed — cross-script
GetButtonPressed was global state in TES4) keeps the old `-1` conversion:
those readers were dead before this plan existed and stay explicitly dead
rather than silently miswired.

FO3/FNV spell the same idiom as `ShowMessage <MESG>` against an AUTHORED MESG
record, polled by the same GetButtonPressed. Such a site enters the plan under
the MESG's own EDID with text None: the importer converts that record itself
and writes nothing for it, and the converter matches the site by name.
See: docs/commentary/script_convert.md#fnv-showmessage-menus
"""

import re

from tes4_export.record_types.common import escape_value

# ---------------------------------------------------------------------------
# Button MessageBox sites (TES4) and authored button MESGs (FO3/FNV)
# ---------------------------------------------------------------------------

MESG_PREFIX = 'TES4Msg_'
# Both engines cap a message box at 10 buttons.
MAX_BUTTONS = 10

_QUOTED = re.compile(r'"([^"]*)"')
# A MessageBox STATEMENT: first token on its line (TES4 is one statement per
# line, and a leading `;` comment never matches). Oblivion tolerates a comma
# straight after the command name.
_MSGBOX_LINE = re.compile(r'(?im)^[ \t]*messagebox\b(,?[^\n]*)')
_MSGBOXEX_LINE = re.compile(r'(?im)^[ \t]*messageboxex\b(,?[^\n]*)')
#: `set <var> to sv_Construct "<literal>"`: a string_var given a fixed text.
_SV_SET = re.compile(r'(?im)^[ \t]*set[ \t]+([A-Za-z0-9_]+)[ \t]+to[ \t]+sv_construct[ \t,]+"([^"]*)"')
#: An OBSE format specifier: flags, width, precision, then the letter (or `%%`).
_SPEC = re.compile(r'%[-+ #0]*\d*(?:\.\d+)?([a-zA-Z%])')
_SHOWMESSAGE_LINE = re.compile(r'(?im)^[ \t]*showmessage\b[ \t,]+([A-Za-z0-9_]+)')


def parse_button_box(args_str):
    """(text, [buttons]) from a MessageBox argument string, or None when the
    call carries no buttons (a plain notification box).

    Buttons are the quoted strings AFTER the first one; unquoted tokens in
    between are printf-style format arguments (`"...%.0f Drakes?" cost "Yes"
    "No"`) and are not part of the menu. MESG DESC text is static, so such a
    specifier survives literally — rare, and better than losing the menu.
    """
    if not args_str:
        return None
    quoted = _QUOTED.findall(args_str)
    if len(quoted) < 2:
        return None
    return quoted[0], quoted[1:1 + MAX_BUTTONS]


def _string_literals(source: str, line: int) -> dict:
    """{var_lower: literal, None if formatted} from the sv_Constructs above `line`."""
    above = ''.join((source or '').splitlines(keepends=True)[:max(line - 1, 0)])
    return {m.group(1).lower(): None if '%' in m.group(2) else m.group(2)
            for m in _SV_SET.finditer(above)}


def _fill_strings(fmt: str, names: list, literals: dict):
    """`fmt` with each `%z` replaced by its variable's literal and `%r` by a newline, or None."""
    names, out, at = iter(names), [], 0
    for m in _SPEC.finditer(fmt):
        out.append(fmt[at:m.start()])
        kind, at = m.group(1), m.end()
        if kind == 'z':
            literal = literals.get(next(names, '').lower())
            if literal is None:
                return None
            out.append(literal)
        else:
            out.append({'r': '\n', '%': '%'}.get(kind, m.group(0)))
            if kind not in 'r%':
                next(names, None)
    return ''.join(out) + fmt[at:]


def resolve_pipe_box(args_str: str, source: str = '', line: int = 0):
    """(text, [buttons]) from OBSE MessageBoxEX's `"text|button|..."`, or None.

    A `%z` takes the literal its string variable was last `sv_Construct`ed to
    above `line` in `source`; a box still dynamic after that (a string from
    anything else, a number in a button) is None, since a MESG's text is fixed.
    See: docs/commentary/script_convert.md#messageboxex-buttons
    """
    quoted = _QUOTED.findall(args_str or '')
    if not quoted:
        return None
    names = re.findall(r'[A-Za-z_][A-Za-z0-9_.]*', (args_str or '').split('"', 2)[-1])
    filled = _fill_strings(quoted[0], names, _string_literals(source, line))
    parts = filled.split('|') if filled is not None else []
    if len(parts) < 2 or any('%' in b for b in parts[1:]):
        return None
    return parts[0], parts[1:1 + MAX_BUTTONS]


def mesg_edid(script_edid: str, index: int) -> str:
    base = re.sub(r'[^A-Za-z0-9_]', '_', script_edid or 'Script')
    return f'{MESG_PREFIX}{base}_{index:02d}'


def button_messages(mesg_records: list) -> dict:
    """{edid_lower: (edid, [buttons])} for the authored MESGs that have buttons."""
    out = {}
    for rec in mesg_records:
        edid = rec.get('EditorID', '')
        buttons = []
        while (text := rec.get(f'Button[{len(buttons)}].Text')) is not None:
            buttons.append(text)
        if edid and buttons:
            out[edid.lower()] = (edid, buttons[:MAX_BUTTONS])
    return out


def sites_for_source(script_edid: str, source: str, authored: dict = None) -> list:
    """Ordered [(mesg_edid, text, buttons)] for one script source (real
    newlines, i.e. the SCTX value parse_export_file produces); `authored` is
    `button_messages`, whose MESGs the source shows enter with text None."""
    source = source or ''
    boxes = [parse_button_box(m.group(1)) for m in _MSGBOX_LINE.finditer(source)]
    boxes += [resolve_pipe_box(m.group(1), source, source.count('\n', 0, m.start()) + 1)
              for m in _MSGBOXEX_LINE.finditer(source)]
    sites = []
    for parsed in filter(None, boxes):
        sites.append((mesg_edid(script_edid, len(sites) + 1),) + parsed)
    for m in _SHOWMESSAGE_LINE.finditer(source or ''):
        hit = (authored or {}).get(m.group(1).lower())
        if hit and all(s[0] != hit[0] for s in sites):
            sites.append((hit[0], None, hit[1]))
    return sites


def authored_site(plan: dict, script_edid: str, mesg_edid: str) -> str:
    """The planned authored MESG EDID `script_edid` shows via ShowMessage, or ''."""
    for name, text, _buttons in plan.get((script_edid or '').lower(), ()):
        if text is None and name.lower() == mesg_edid.lower():
            return name
    return ''


def build_message_plan(scpt_records: list, mesg_records: list = ()) -> dict:
    """{script_edid_lower: [(mesg_edid, text, buttons)]} over SCPT records."""
    authored = button_messages(mesg_records)
    plan = {}
    for rec in scpt_records:
        edid = rec.get('EditorID', '')
        sites = sites_for_source(edid, rec.get('SCTX', ''), authored)
        if sites and edid:
            plan[edid.lower()] = sites
    return plan


# ---------------------------------------------------------------------------
# TES4 chargen menus (ShowBirthsignMenu / ShowClassMenu)
# ---------------------------------------------------------------------------
#
# TES4's chargen menus were MODAL: the game paused until the player chose,
# and scripted scenes depend on that beat.  CharacterGen stage 43 is the
# canonical case — the Emperor's birthsign INFOs carry an authored Goodbye
# (the modal menu takes over from there) and stage 44 re-force-greets him to
# continue the conversation.  With the menus converted to no-ops the player
# was dumped into a free-roam gap in the middle of the scene, where any other
# pending force-greet (Baurus's torch GREETING, legal for stages 40-80) could
# steal them and desync the conversation.  Message.Show() is Skyrim's modal
# equivalent: it parks the calling thread AND pauses gameplay until a button
# is clicked, restoring both the choice and the authored pacing.
#
# The plan is data-driven from the plugin's own BSGN/CLAS records so any
# plugin with birthsigns/classes gets its own menus; a plugin without them
# keeps the no-op conversion.

CHARGEN_BIRTHSIGN_EDID = 'TES4Msg_ChargenBirthsign_%02d'
CHARGEN_CLASS_EDID = 'TES4Msg_ChargenClass_%02d'
# GLOB records persisting the player's menu choice as (menu index + 1); 0 =
# not chosen yet.  The converter's menu emission writes them on selection and
# the dialogue-condition conversion reads them back: TES4 GetIsPlayerBirthsign
# (func 224, dead in Skyrim) / GetPCIsClass (129, dead — the player never has
# a TES4 class) become GetGlobalValue(<choice>) == index+1, which is how the
# Emperor's post-birthsign line matches the sign actually picked.
CHARGEN_BIRTHSIGN_GLOBAL = 'TES4ChargenBirthsignChoice'
CHARGEN_CLASS_GLOBAL = 'TES4ChargenClassChoice'
# Every page but the last carries this many real choices plus a trailing
# "More ..." button in slot PAGE_OPTIONS; the last page holds up to
# MAX_BUTTONS real choices.  Global choice index = sum of prior pages'
# PAGE_OPTIONS + the clicked button — the emitted script chains pages with
# exactly this arithmetic, so page composition here is a cross-module
# contract with the converter's ShowBirthsignMenu/ShowClassMenu emission.
PAGE_OPTIONS = MAX_BUTTONS - 1


def _paged(edid_fmt: str, title: str, labels: list) -> list:
    """[(mesg_edid, title, buttons)] pages over `labels` (see PAGE_OPTIONS)."""
    pages = []
    i = 0
    n = 1
    while i < len(labels):
        rest = len(labels) - i
        if rest <= MAX_BUTTONS:
            take, more = rest, False
        else:
            take, more = PAGE_OPTIONS, True
        pages.append((edid_fmt % n, title,
                      list(labels[i:i + take]) + (['More ...'] if more else [])))
        i += take
        n += 1
    return pages


#: The file-name prefixes each game's birthsign pictures carry, and Morrowind's two shortened names.
_SIGN_PREFIXES = ('tx_birth_', 'birthsign_the ', 'birthsign_', 'small_the_')
_SIGN_ALIASES = {'apprent': 'apprentice', 'atron': 'atronach'}


def birthsign_key(path: str) -> str:
    """The sign a birthsign picture shows, from its file name: Morrowind's
    `tx_birth_apprent` and Oblivion's `birthsign_the apprentice` are both
    `apprentice`. The movie names its pictures by it and the sidecar its rows."""
    stem = re.split(r'[\\/]', path or '')[-1].rsplit('.', 1)[0].lower()
    for prefix in _SIGN_PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    stem = _SIGN_ALIASES.get(stem, stem)
    return re.sub(r'[^a-z0-9]+', '_', stem).strip('_')


def _int(rec: dict, key: str, default: int = 0) -> int:
    """An export field as an int, `default` when absent or not a number."""
    try:
        return int(rec.get(key, default))
    except (TypeError, ValueError):
        return default


def _fid24(rec: dict) -> int:
    """A record's FormID without its index byte."""
    try:
        return int(rec.get('FormID', '0'), 16) & 0xFFFFFF
    except ValueError:
        return 0


def _spell_id(rec: dict, i: int, fid: int) -> str:
    """How the runtime grants a BSGN's `i`th spell: its TES3 id, else
    `Owner.esm@FormID` read off the sign's own masters ('' when unknown)."""
    tes3 = rec.get(f'SpellId[{i}]', '')
    if tes3:
        return tes3
    masters, slot = rec.get('_masters') or [], fid >> 24
    owner = masters[slot] if slot < len(masters) else rec.get('_plugin', '')
    return f'{owner}@{fid & 0xFFFFFF:08X}' if owner else ''


def _sign_spells(rec: dict, spells: dict) -> tuple:
    """(EditorIDs, display names, runtime ids) of a BSGN's spells that convert."""
    edids, names, ids = [], [], []
    for i in range(_int(rec, 'SpellCount', 99)):
        fid = rec.get(f'Spell[{i}]')
        if fid is None:
            break
        spell = spells.get(int(fid or '0', 16) & 0xFFFFFF)
        if spell:
            edids.append(spell.get('EditorID'))
            names.append(spell.get('FULL') or spell.get('EditorID'))
            ids.append(_spell_id(rec, i, int(fid or '0', 16)))
    return edids, names, ids


def _birthsign_plan(bsgn_records: list, spells: dict) -> dict:
    """The birthsign menu: signs by display name, each granting its spells; two
    records sharing a name share the slot, the later one's data winning (the
    Morroblivion patch's Morrowind signs over Oblivion's)."""
    signs, fids = {}, []
    for rec in bsgn_records:
        full = rec.get('FULL') or rec.get('EditorID') or ''
        if full:
            edids, names, ids = _sign_spells(rec, spells)
            signs[full.lower()] = (edids, {
                'name': full, 'desc': rec.get('DESC', ''), 'image': birthsign_key(rec.get('ICON')),
                'spells': names, 'ids': ids})
            fids.append((full.lower(), _fid24(rec)))
    names = sorted(signs)
    index_of = {n: i for i, n in enumerate(names)}
    return {'pages': _paged(CHARGEN_BIRTHSIGN_EDID, 'Under which sign were you born?',
                            [signs[n][1]['name'] for n in names]),
            'actions': [signs[n][0] for n in names],
            'fid_to_index': {fid: index_of[n] for n, fid in fids if fid},
            'choice_global': CHARGEN_BIRTHSIGN_GLOBAL,
            'rows': [signs[n][1] for n in names]} if signs else {}


def _class_plan(clas_records: list) -> dict:
    """The class menu: playable classes by display name; two records sharing a
    name share the slot, the later one's data winning. `edid_to_index` is each
    record's slot by lowercase EditorID."""
    rows, fids = {}, []
    for rec in clas_records:
        full = rec.get('FULL') or ''
        if not (_int(rec, 'DATA.Flags') & 0x1 and full):
            continue
        rows[full.lower()] = {
            'name': full, 'desc': rec.get('DESC', ''),
            'spec': _int(rec, 'DATA.Specialization'),
            'attributes': (_int(rec, 'DATA.PrimaryAttribute1', -1),
                           _int(rec, 'DATA.PrimaryAttribute2', -1))}
        fids.append((full.lower(), _fid24(rec), (rec.get('EditorID') or '').lower()))
    names = sorted(rows, key=lambda n: rows[n]['name'].lower())
    index_of = {n: i for i, n in enumerate(names)}
    return {'pages': _paged(CHARGEN_CLASS_EDID, 'Choose your class.',
                            [rows[n]['name'] for n in names]),
            'actions': [[] for _ in names],
            'fid_to_index': {fid: index_of[n] for n, fid, _e in fids if fid},
            'edid_to_index': {edid: index_of[n] for n, _f, edid in fids if edid},
            'choice_global': CHARGEN_CLASS_GLOBAL,
            'rows': [rows[n] for n in names]} if rows else {}


def build_chargen_menus(bsgn_records: list, clas_records: list, spel_records: list) -> dict:
    """Shared birthsign/class menu plan.

    The importer authors one MESG per page and stages each menu's `rows` for
    MorrowindRuntime's own menus; the converter asks the runtime for the menu
    and falls back to the Show() chain, granting a chosen sign's spells (BSGN
    lists them; the spells are converted SPEL records named by EditorID).
    Both sides MUST derive identical page EDIDs and order, so everything is
    sorted by display name.
    See: docs/commentary/morrowind_runtime.md#chargen-menus
    """
    spells = {_fid24(r): r for r in spel_records if r.get('EditorID')}
    plan = {'birthsign': _birthsign_plan(bsgn_records, spells),
            'class': _class_plan(clas_records)}
    return {key: menu for key, menu in plan.items() if menu}


# ---------------------------------------------------------------------------
# The runtime's chargen table rows
# ---------------------------------------------------------------------------

def table_field(text) -> str:
    """One `|`-separated field: export-escaped, with no separator of its own."""
    return escape_value(str(text or '')).replace('|', '/').replace(';', ',')


def class_line(i: int, row: dict) -> str:
    """`class.<i>=name|spec|a1,a2|desc`."""
    first, second = row['attributes']
    return (f"class.{i}={table_field(row['name'])}|{row['spec']}|{first},{second}|"
            f"{table_field(row['desc'])}")


def sign_line(i: int, row: dict) -> str:
    """`sign.<i>=name|picture|desc|spell;spell|id;id|line;line`, each display line
    (`row['display']`: kind, icon key, text) as `kind~key~text`."""
    spells = ';'.join(table_field(name) for name in row['spells'])
    ids = ';'.join(table_field(spell) for spell in row['ids'])
    lines = ';'.join(f'{kind}~{key}~{table_field(text)}'
                     for kind, key, text in row.get('display', ()))
    return (f"sign.{i}={table_field(row['name'])}|{row['image']}|{table_field(row['desc'])}|"
            f"{spells}|{ids}|{lines}")
