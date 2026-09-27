"""
Morroblivion's magic restored to Morrowind's own effects by the gap patch, and
the vanilla start scripts that can run beside Morroblivion.

Morroblivion stood a script effect in for each Morrowind effect Oblivion had
no mechanism for. Where the vanilla record it was made from now converts --
every effect native to Skyrim or carried by MorrowindRuntime -- the patch
overrides Morroblivion's record with vanilla's effects, unless something else
reads state only the replaced scripts keep.

See: docs/commentary/tes4_export_morrowind.md#restored-magic
"""

import os
import re

from tes5_import.base.text_reader import parse_export_file
from tes5_import.record_types.magic_morrowind import mw_converts, mw_needs_runtime

from .morroblivion_pairs import master_records
from .record_types.common import escape_value
from .record_types.morrowind import tes4_signature
from .tes3_reader import get_string, get_subrecord, read_file

#: The magic record types whose effect lists are restored.
MAGIC_TYPES = frozenset({'SPEL', 'ENCH', 'ALCH'})

#: TES3 Intervention effect index -> the marker kind the runtime lands on.
INTERVENTIONS = {62: 'divine', 63: 'temple'}

#: The export fields holding script source.
_SOURCE_KEYS = ('SCTX',)
_RESULT_SUFFIX = 'ResultScript'

#: Vanilla types whose ids name no object a script acts on.
_NOT_OBJECTS = frozenset({'CELL', 'REGN', 'LAND', 'PGRD', 'SSCR', 'INFO'})

_WORD = r'[A-Za-z_][A-Za-z0-9_]*'
_LOCAL = re.compile(rf'^\s*(?:short|long|float|int|ref|reference|array_var|string_var)'
                    rf'\s+({_WORD})', re.I | re.M)
_SET = re.compile(rf'\b(?:set|let)\s+({_WORD}(?:\s*\.\s*{_WORD})?)\s*(?:to\b|:=)', re.I)
_MOVE = re.compile(rf'\b({_WORD})\s*\.\s*(?:moveto|movetomarker|positioncell|'
                   rf'positionworld|setpos|enable|disable)\b', re.I)
_QUEST_EVENT = re.compile(r'\b(?:setstage|startquest|stopquest)\b', re.I)
_PLAYER_MOVE = re.compile(rf'\bplayer\s*\.\s*moveto\s+({_WORD})', re.I)
_TOKEN = re.compile(rf'"([^"]*)"|({_WORD})')
#: What names no record in MWScript source: comments, GetPCCell's cell NAME argument, and the keywords.
_NOT_NAMES = re.compile(r';[^\n]*|\bgetpccell\s*,?\s*"[^"]*"', re.I)
_KEYWORDS = frozenset({'begin', 'end', 'if', 'elseif', 'else', 'endif', 'set', 'to',
                       'short', 'long', 'float', 'return', 'while', 'endwhile'})
#: A script line that does nothing: its name, a block edge, a declaration, or `return`.
_NO_STATEMENT = re.compile(r'\s*(?:scn|scriptname|begin|end|return|short|long|float|int|ref|'
                           r'reference|array_var|string_var)\b', re.I)


def _unescape(text: str) -> str:
    """Export-escaped script source as its lines."""
    return text.replace('\\r', '').replace('\\t', ' ').replace('\\n', '\n')


def _writes(text: str) -> set:
    """Each global, quest variable or reference one script writes, lowercased."""
    own = {name.lower() for name in _LOCAL.findall(text)} | {'player'}
    names = {re.sub(r'\s', '', name).lower() for name in _SET.findall(text) + _MOVE.findall(text)}
    return {name for name in names if name.split('.')[0] not in own}


def _condition_forms(rec: dict) -> set:
    """The FormIDs a record's conditions pass as parameters, from each raw CTDA."""
    out = set()
    for key, value in rec.items():
        if 'Condition' in key and key.endswith('.Raw') and len(value) >= 40:
            out.update(value[i + 6:i + 8] + value[i + 4:i + 6] + value[i + 2:i + 4] + value[i:i + 2]
                       for i in (24, 32))
    return {fid.upper() for fid in out}


def _mentions(name: str, text: str) -> bool:
    """Whether `text` names `name`, a qualified `quest.variable` included."""
    pattern = r'\s*\.\s*'.join(re.escape(part) for part in name.split('.'))
    return re.search(rf'\b{pattern}\b', text, re.I) is not None


class _Corpus:
    """Every script source the Morroblivion exports hold, by owning script (`''` for a result script)."""

    def __init__(self, folders):
        """Read each export folder's script sources, conditions, globals and quests."""
        self.scripts = {}
        self.texts = []
        self.cited = set()
        self.forms = {}
        for folder in sorted(folders):
            for sig in ('SCPT', 'QUST', 'INFO', 'PACK', 'GLOB'):
                for rec in parse_export_file(os.path.join(folder, f'{sig}.txt')):
                    self._take(sig, rec)
        self._written = {}

    def _take(self, sig: str, rec: dict) -> None:
        """Keep one record's source text, conditions and name; a script's FormID -> (EditorID, text)."""
        owner = rec.get('EditorID', '').lower() if sig == 'SCPT' else ''
        if sig in ('QUST', 'GLOB'):
            self.forms[rec.get('EditorID', '').lower()] = rec.get('FormID', '').upper()
        self.cited |= _condition_forms(rec)
        for key, value in rec.items():
            if key in _SOURCE_KEYS or key.endswith(_RESULT_SUFFIX):
                text = _unescape(value)
                self.texts.append((owner, text))
                if sig == 'SCPT':
                    self.scripts[rec.get('FormID', '').upper()] = (owner, text)

    def written(self, index: int) -> set:
        """What text `index` writes, worked out once."""
        if index not in self._written:
            self._written[index] = _writes(self.texts[index][1])
        return self._written[index]

    def unobserved(self, text: str, dropped: set) -> bool:
        """Whether dropping a script with source `text` loses nothing another reads.

        A script that stages, starts or stops a quest is content and always
        stays. Each thing it writes must be read nowhere outside `dropped` --
        by name in a script, or by FormID in a condition -- or written there too.
        """
        if _QUEST_EVENT.search(text):
            return False
        outside = [i for i, (owner, _t) in enumerate(self.texts) if owner not in dropped]
        for name in _writes(text):
            readers = [i for i in outside if _mentions(name, self.texts[i][1])]
            if any(name in self.written(i) for i in readers):
                continue
            if readers or self.forms.get(name.split('.')[0]) in self.cited:
                return False
        return True


def _script_effects(rec: dict):
    """The script FormIDs a record's script effects name, or None when it has none."""
    count = int(rec.get('EffectCount') or 0)
    seffs = [i for i in range(count) if rec.get(f'Effect[{i}].EFID') == 'SEFF']
    if not seffs:
        return None
    return {rec.get(f'ScriptEffect[{i}].FormID', '').upper() for i in seffs} - {'', '00000000'}


def _effects(lines: list) -> list:
    """(TES3 index, actor value) of each effect an exported record carries."""
    fields = dict(line.split('=', 1) for line in lines if '=' in line)
    return [(int(fields[f'Effect[{i}].MorrowindIndex']),
             int(fields.get(f'Effect[{i}].ActorValue') or -1))
            for i in range(int(fields.get('EffectCount') or 0))
            if fields.get(f'Effect[{i}].MorrowindIndex')]


def _vanilla_magic(esms, ctx) -> dict:
    """{(signature, FormID): vanilla record} for each spell, enchantment or potion
    Morroblivion supplies under the same type; a later file's record wins."""
    out = {}
    for path in esms:
        for rec in read_file(path)[1]:
            if rec.type not in MAGIC_TYPES or rec.deleted:
                continue
            fid = ctx.index.lookup(rec.record_id)
            sig = tes4_signature(rec)
            if fid and ctx.index.lookup_signature(rec.record_id) == sig:
                out[(sig, fid.upper())] = rec
    return out


def _candidates(vanilla: dict, ctx, export_record) -> tuple:
    """({key: (Morroblivion record, vanilla lines, script FormIDs)}, export folders)."""
    found, folders = {}, set()
    for sig, fid, rec, path in master_records(ctx, MAGIC_TYPES):
        key = (sig, fid.upper())
        scripts = _script_effects(rec)
        if key not in vanilla or scripts is None:
            continue
        lines = export_record(vanilla[key], ctx)
        effects = _effects(lines or [])
        if effects and all(mw_converts(index, av) for index, av in effects):
            found[key] = (rec, lines, scripts)
            folders.add(path)
    return found, folders


def _inert(text: str) -> bool:
    """Whether a script holds no statement, only blocks, locals and `return`."""
    return not any(line.strip() and not _NO_STATEMENT.match(line)
                   for line in _NOT_NAMES.sub('', text).splitlines())


def _replaceable(lines: list, scripts: set, corpus: _Corpus) -> bool:
    """Whether each script does nothing, or the runtime carries every effect."""
    if all(_inert(corpus.scripts[s][1]) for s in scripts):
        return True
    return all(mw_needs_runtime(index) for index, _av in _effects(lines))


def _independent(found: dict, corpus: _Corpus) -> set:
    """The keys of `found` whose replaced scripts nothing left behind depends on."""
    keys = {key for key, (_r, lines, scripts) in found.items()
            if all(s in corpus.scripts for s in scripts) and _replaceable(lines, scripts, corpus)}
    while True:
        names = {corpus.scripts[s][0] for key in keys for s in found[key][2]}
        bad = {key for key in keys
               if not all(corpus.unobserved(corpus.scripts[s][1], names) for s in found[key][2])}
        if not bad:
            return keys
        keys -= bad


def _targets(lines: list, scripts: set, corpus: _Corpus) -> list:
    """The marker lines naming where a replaced Intervention script sent the player."""
    kinds = {INTERVENTIONS[index] for index, _av in _effects(lines) if index in INTERVENTIONS}
    targets = sorted({target for s in scripts for target in _PLAYER_MOVE.findall(corpus.scripts[s][1])})
    if len(kinds) != 1 or not targets:
        return []
    return [f'InterventionKind={kinds.pop()}', f"InterventionTargets={','.join(targets)}"]


def restored_magic(esms, ctx, export_record) -> list:
    """(signature, FormID, lines) of each Morroblivion spell, enchantment or potion
    overridden with its vanilla original's effects.

    A record qualifies when it pairs to vanilla by id and type, stands a script
    effect in, and every vanilla effect converts; its Morroblivion EditorID is
    kept. `export_record` is the TES3 exporter's, which imports the patch module.
    See: docs/commentary/tes4_export_morrowind.md#restored-magic
    """
    found, folders = _candidates(_vanilla_magic(esms, ctx), ctx, export_record)
    corpus = _Corpus(folders)
    out = []
    for key in sorted(_independent(found, corpus)):
        rec, lines, scripts = found[key]
        kept = [line for line in lines if not line.startswith('EditorID=')]
        out.append((key[0], key[1], kept + [f"EditorID={escape_value(rec.get('EditorID', ''))}"]
                    + _targets(lines, scripts, corpus)))
    return out


def start_scripts(esms) -> list:
    """Each vanilla SSCR script whose source names no record but itself.

    Such a script keeps only the runtime's own state -- `TribunalMain` turning
    teleporting off in Sotha Sil -- so it can start beside any master.
    See: docs/commentary/tes4_export_morrowind.md#restored-magic
    """
    ids, sources, starts = set(), {}, []
    for path in esms:
        for rec in read_file(path)[1]:
            name = (rec.record_id or '').lower()
            if rec.deleted or not name:
                continue
            if rec.type == 'SSCR' and rec.record_id not in starts:
                starts.append(rec.record_id)
            if rec.type == 'SCPT':
                sub = get_subrecord(rec, 'SCTX')
                sources[name] = _NOT_NAMES.sub('', get_string(sub) if sub is not None else '')
            if rec.type not in _NOT_OBJECTS:
                ids.add(name)
    return [name for name in starts
            if sources.get(name.lower()) and not any(
                token.lower() in ids - _KEYWORDS - {name.lower()}
                for pair in _TOKEN.findall(sources[name.lower()]) for token in pair if token)]
