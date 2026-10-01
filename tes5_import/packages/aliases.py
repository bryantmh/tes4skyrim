"""Quest-package planning: which TES4 packages belong to which quest, and the
reference aliases those packages need.

Why this exists
---------------
In Oblivion a quest package is just a package sitting at the top of an actor's
AI list with a `GetStage MyQuest >= 50` condition.  The actor re-evaluates, the
condition passes, the package wins.

Skyrim has no such thing.  A package that outranks an actor's standing schedule
must be attached to a QUST *reference alias* via ALPC, and its actor/location
targets resolve through alias indices (PTDA type 4 / PLDT type 9) rather than
raw FormIDs.  Vanilla Skyrim.esm carries 4,125 ALPC entries — this is the
normal way quest AI works, not an exotic path.

So for each TES4 package we must answer:
  1. Is it quest-owned?  (Does any condition reference a quest?)
  2. Which quest?        (-> PACK.QNAM, and which quest gets the ALPC)
  3. Which actor runs it? (-> that actor needs an alias on that quest)
  4. Which refs does it name? (-> those need aliases too, e.g. the player)

Quest ownership is inferred from the package's own conditions.  TES4 condition
functions that name a quest:
    58  GetStage            param1 = QUST
    59  GetStageDone        param1 = QUST
    79  GetQuestVariable    param1 = QUST
    * plus GetQuestRunning (56) / GetQuestCompleted (?) forms
This is exactly the gate Oblivion used, so reading it back gives us the same
"this package belongs to this quest" relation the original content author meant.
"""

import re
import struct

from ..base.text_reader import (get_formid, get_int, get_str, remap_formid,
                          get_formid_index_offset)


def _master_records(master_export, *sigs):
    """Yield (own-space FormID, record) for the masters' records of `sigs`.

    A master record's `FormID` FIELD is in the MASTER's index space, so
    `get_formid()` — which applies OUR offset — returns the wrong id for it.
    `load_master_export` already re-keyed the dict into the space THIS plugin
    names the master by, so the KEY is the correct source id; shift it the same
    way get_formid would and every id this module produces stays in one space.
    """
    if not master_export:
        return
    want = frozenset(sigs)
    offset = get_formid_index_offset()
    for key, rec in master_export.items():
        if rec.get('Signature') not in want:
            continue
        try:
            yield remap_formid(int(key, 16), offset), rec
        except (TypeError, ValueError):
            continue

# TES4 condition functions whose first parameter is a quest FormID.
QUEST_PARAM_FUNCS = frozenset({
    58,   # GetStage
    59,   # GetStageDone
    79,   # GetQuestVariable
    56,   # GetQuestRunning
})

# TES4 PKDT.Type values whose behaviour targets a specific reference.  These are
# the ones that need alias routing when quest-owned.
REF_TARGET_TYPES = frozenset({0, 1, 2, 7, 8, 9, 11})


# Every TES4 record type that carries SCRI (the 21 records in xEdit
# wbDefinitionsTES4 that include wbSCRI).  A GetScriptVariable condition may
# name a REFR of any of them.
SCRIPTABLE_BASE_SIGS = (
    'ACTI', 'ALCH', 'APPA', 'ARMO', 'BOOK', 'CLOT', 'CONT', 'CREA', 'DOOR',
    'FLOR', 'FURN', 'INGR', 'KEYM', 'LIGH', 'LVLC', 'MISC', 'NPC_', 'QUST',
    'SGST', 'SLGM', 'WEAP',
)


#: `set [Quest.]name to` -- the only way TES4 script code writes a variable.
_SET_TARGET_RE = re.compile(
    r'(?:^|[^A-Za-z0-9_])set\s+(?:[A-Za-z0-9_]+\.)?([A-Za-z0-9_]+)\s+to'
    r'(?:[^A-Za-z0-9_]|$)', re.IGNORECASE)

#: The export escapes newlines and tabs as literal backslash sequences.
_ESCAPED_WS_RE = re.compile(r'\\+[rnt]')


def build_assigned_var_names(by_type: dict, master_export: dict = None) -> set:
    """Lowercased name of every script variable the plugin ever ASSIGNS.

    A variable declared and tested but never written holds its 0 default
    forever, so a `== nonzero` test on it can never pass.

    See: docs/commentary/tes5_import_quest.md#stage-log-entry-conditions
    """
    names = set()
    sources = list(master_export.values()) if master_export else []
    for sig in ('SCPT', 'QUST', 'INFO'):
        sources.extend(by_type.get(sig, []))
    for rec in sources:
        for key, val in rec.items():
            if not val or (key != 'SCTX' and 'Script' not in key):
                continue
            for m in _SET_TARGET_RE.finditer(_ESCAPED_WS_RE.sub(' ', val)):
                names.add(m.group(1).lower())
    return names


def build_script_var_map(by_type: dict, master_export: dict = None) -> dict:
    """ref_fid -> {var_index: var_name} for every scripted actor/object.

    A TES4 GetScriptVariable condition stores the variable's *script-local
    index* (SLSD) in param2 — the name exists only in the SCPT record.  To turn
    such a condition back into a named Papyrus property we need, for the
    reference the condition tests, the variable table of the script attached to
    that reference's BASE record.

    Chain: condition names a REFR -> the REFR's base NPC_/CREA/ACTI -> its SCRI
    -> the SCPT's Variable[i].Index/.Name.

    `master_export` is the MASTERS' export records and is REQUIRED for a plugin
    with masters: any link in that chain may live in the master (a plugin REFR
    of a master base, or a master script), and a break anywhere leaves the
    condition without a variable NAME, so it emits no CIS2 and can never pass.

    Keys stay LOW-24 on purpose.  The only consumer is
    dialog_conditions.convert_script_var_ctda, which looks up a CTDA's param1
    — a RAW TES4 FormID with no load-order offset applied — so it masks to the
    low 24 bits.  Keying this map on the remapped fid instead makes every
    GetScriptVariable/GetQuestVariable condition miss, which silently un-gates
    all of them (Jiub repeats his first line, wrong quests fire).

    Master records are yielded BEFORE the plugin's own so that, with the
    original last-duplicate-wins assignment preserved, a plugin record still
    overrides the master's.  (Using setdefault instead flips precedence to
    first-wins among the plugin's OWN duplicates — many refs share a base and
    many bases share a script — which silently re-gates conditions: DAAzura
    started at game load and its followers packaged onto the player.)
    """
    def _recs(sig):
        if master_export:
            for r in master_export.values():
                if r.get('Signature') == sig:
                    yield r
        yield from by_type.get(sig, [])

    # 1. SCPT fid -> {index: name}
    script_vars = {}
    for rec in _recs('SCPT'):
        sfid = get_formid(rec, 'FormID') & 0x00FFFFFF
        n = get_int(rec, 'VariableCount')
        table = {}
        for i in range(n):
            idx = get_int(rec, f'Variable[{i}].Index')
            name = get_str(rec, f'Variable[{i}].Name')
            if name:
                table[idx] = name
        if table:
            script_vars[sfid] = table

    # 2. base record fid -> its script's variable table.  EVERY base type
    #    TES4 lets a script attach to: a Find gated on a scripted WEAP (the
    #    goblin leaders' totem staffs, CreatureGoblinLeaderFindHead*) or a
    #    scripted MISC (11 INFO conditions) resolved to nothing under the
    #    shorter list and lost its variable NAME.
    base_vars = {}
    for sig in SCRIPTABLE_BASE_SIGS:
        for rec in _recs(sig):
            scri = get_formid(rec, 'SCRI') & 0x00FFFFFF
            if scri in script_vars:
                base_vars[get_formid(rec, 'FormID') & 0x00FFFFFF] = \
                    script_vars[scri]

    # 3. REFR/ACHR/ACRE fid -> base's table (conditions name the *reference*)
    out = dict(base_vars)
    for sig in ('REFR', 'ACHR', 'ACRE'):
        for rec in _recs(sig):
            base = get_formid(rec, 'NAME') & 0x00FFFFFF
            table = base_vars.get(base)
            if table:
                out[get_formid(rec, 'FormID') & 0x00FFFFFF] = table
    return out


def _quest_fids_from_conditions(rec: dict) -> list:
    """Quest FormIDs named by a TES4 package's conditions.

    TES4 CTDA (24 bytes): Type u8, unused[3], ComparisonValue f32,
    FunctionIndex u32, Param1 u32, Param2 u32, unused[4].
    """
    out = []
    i = 0
    while True:
        raw = rec.get(f'Condition[{i}].Raw')
        if raw is None:
            break
        i += 1
        if not raw:
            continue
        try:
            blob = bytes.fromhex(raw)
            if len(blob) < 20:
                continue
            func = struct.unpack('<I', blob[8:12])[0]
            param1 = struct.unpack('<I', blob[12:16])[0]
        except (ValueError, struct.error):
            continue
        if func in QUEST_PARAM_FUNCS and param1:
            out.append(param1)
    return out


GET_SCRIPT_VARIABLE = 53


def scriptvar_refs_from_conditions(rec: dict) -> list:
    """References tested by a GetScriptVariable condition (param1)."""
    out = []
    i = 0
    while True:
        raw = rec.get(f'Condition[{i}].Raw')
        if raw is None:
            break
        i += 1
        if not raw:
            continue
        try:
            blob = bytes.fromhex(raw)
            if len(blob) < 20:
                continue
            func = struct.unpack('<I', blob[8:12])[0]
            param1 = struct.unpack('<I', blob[12:16])[0]
        except (ValueError, struct.error):
            continue
        if func == GET_SCRIPT_VARIABLE and param1:
            out.append(param1)
    return out


def build_scriptvar_owner_map(by_type: dict, fid_to_edid: dict) -> dict:
    """ref_fid -> quest_fid, for refs whose script variables a quest writes.

    An Oblivion quest package gated on `GetScriptVariable(SomeRef, var)` belongs
    to whichever quest SETS that variable.  Quests set it from two places:
      * a dialogue INFO result script  (INFO.Quest names the quest)
      * a quest stage result script    (the QUST itself)
    Both look like `set SomeRef.var to N` / `SomeRef.var = N`, so we scan the
    result-script text for `<EditorID>.<anything>` and attribute the ref to that
    quest.  This recovers the same "package belongs to quest" relation the
    original author expressed.
    """
    edid_to_fid = {v.lower(): k for k, v in fid_to_edid.items() if v}
    owner = {}

    def _scan(text: str, qfid: int):
        if not text or not qfid:
            return
        for m in re.finditer(r'\b(\w+)\s*\.\s*\w+', text):
            ref = edid_to_fid.get(m.group(1).lower())
            if ref:
                owner.setdefault(ref & 0x00FFFFFF, qfid)

    for rec in by_type.get('INFO', []):
        qfid = get_formid(rec, 'Quest')
        _scan(get_str(rec, 'ResultScript'), qfid)

    for rec in by_type.get('QUST', []):
        qfid = get_formid(rec, 'FormID')
        s = 0
        while f'Stage[{s}].Index' in rec:
            lc = get_int(rec, f'Stage[{s}].LogCount')
            for j in range(max(lc, 1)):
                _scan(get_str(rec, f'Stage[{s}].Log[{j}].ResultScript'), qfid)
            s += 1
    return owner


GET_IS_ID_FUNCS = frozenset({72, 73})   # GetIsID, GetIsCreature


def _speaker_from_conditions(rec: dict) -> int:
    """The actor a dialogue INFO is restricted to, via its GetIsID condition.

    Returns 0 when the INFO is not actor-specific (a shared topic), in which
    case a bare `AddScriptPackage` has no single target to resolve.
    """
    i = 0
    while True:
        raw = rec.get(f'Condition[{i}].Raw')
        if raw is None:
            return 0
        i += 1
        if not raw:
            continue
        try:
            blob = bytes.fromhex(raw)
            if len(blob) < 20:
                continue
            func = struct.unpack('<I', blob[8:12])[0]
            param1 = struct.unpack('<I', blob[12:16])[0]
        except (ValueError, struct.error):
            continue
        if func in GET_IS_ID_FUNCS and param1:
            # A CTDA param is a RAW TES4 id; the caller remaps it like every
            # other id it produces.
            return param1


#   ref.AddScriptPackage PkgEdid   /   AddScriptPackage PkgEdid   (implicit)
# Quoted and comma forms both occur: `AddScriptPackage "Pkg"`, `ref.foo, Pkg`.
#
# The export escapes the script's newlines and tabs as the LITERAL two-character
# sequences \r \n \t (a backslash followed by the letter), so the text arrives
# as one physical line.  Both matter here:
#   * `\t` before a ref name would otherwise be captured INTO it, turning
#     `CelebroRef` into `tCelebroRef` and resolving nothing.
#   * a `;` comment runs to the next `\r\n`, not to a real newline, so comment
#     stripping has to work on the escaped form or every commented-out call is
#     read as live code.  Nehrim's StartCelleTrigZonePlayerStoryvar01SCRIPT has
#     `;\tCelebroRef.AddScriptPackage, ...` — disabled by its author, and
#     attaching it would resurrect content the mod deliberately cut.
_ADD_SCRIPT_PACKAGE_RE = re.compile(
    r'(?:([A-Za-z]\w*)\s*\.\s*)?AddScriptPackage[\s,]+"?(\w+)"?', re.I)

_SCRIPT_ESCAPES = (('\\r\\n', '\n'), ('\\n', '\n'), ('\\r', '\n'),
                   ('\\t', ' '))


def _script_lines(text: str):
    """Yield the script's LIVE source lines, comments and escapes resolved."""
    if not text:
        return
    for old, new in _SCRIPT_ESCAPES:
        text = text.replace(old, new)
    for line in text.split('\n'):
        yield line.split(';', 1)[0]


def _raw_fid(rec: dict, field: str) -> int:
    """The record's `field` FormID as a RAW int, 0 when absent or malformed."""
    try:
        return int(rec.get(field, '0') or '0', 16)
    except ValueError:
        return 0


def _script_actor_owners(recs) -> dict:
    """script fid -> [actor fid, ...] carrying it, by inverting SCRI.

    A bare `AddScriptPackage` acts on the script's OWN actor, the majority
    form.  An object script attached to several actors forces the package on
    each, which is what TES4 does.  Ids stay RAW, like every other id the
    caller's `_scan` sees.
    """
    script_actors = {}
    for sig in ('NPC_', 'CREA'):
        for rec in recs(sig):
            sfid = _raw_fid(rec, 'SCRI')
            if sfid:
                script_actors.setdefault(sfid, []).append(
                    _raw_fid(rec, 'FormID'))
    return script_actors


def _scan_scpt_packages(recs, scan) -> None:
    """Scan every SCPT's text, attributing a bare call to each owning actor."""
    script_actors = _script_actor_owners(recs)
    for rec in recs('SCPT'):
        text = get_str(rec, 'ScriptText') or get_str(rec, 'SCTX')
        if not text:
            continue
        owners = script_actors.get(_raw_fid(rec, 'FormID'), [])
        if owners:
            for owner in owners:
                scan(text, owner)
        else:
            scan(text)


def _scan_info_packages(recs, scan) -> None:
    """Scan every INFO result script, attributing a bare call to the SPEAKER.

    The speaker is named by the INFO's own GetIsID/GetIsCreature condition —
    authored data, exactly how Oblivion restricts a response to one NPC.
    """
    for rec in recs('INFO'):
        text = get_str(rec, 'ResultScript')
        if not text:
            continue
        scan(text, _speaker_from_conditions(rec))


def _scan_qust_packages(recs, scan) -> None:
    """Scan every QUST stage log's result script; no implicit actor applies."""
    for rec in recs('QUST'):
        s = 0
        while f'Stage[{s}].Index' in rec:
            lc = get_int(rec, f'Stage[{s}].LogCount')
            for j in range(max(lc, 1)):
                scan(get_str(rec, f'Stage[{s}].Log[{j}].ResultScript'))
            s += 1


def build_script_assigned_packages(by_type: dict, fid_to_edid: dict,
                                   master_export: dict = None) -> dict:
    """pack_fid -> set(actor ref_fid) for packages forced on by script.

    TES4's `AddScriptPackage` puts a package on an actor that does NOT list it
    in its AI package array — the package exists only to be forced on later.
    Skyrim has no equivalent call (`SetOverridePackage` is a Fallout 4 API and
    is absent from SkyrimSE.exe 1.6; only `EvaluatePackage` and
    `KeepOffsetFromActor` exist), so the forced package must instead be
    attached to the actor's quest alias as an ALPC.  Then the converted
    `EvaluatePackage()` can actually select it, because it is finally ON the
    stack the engine arbitrates.

    Without this the package is invisible to arbitration and silently never
    runs: 35 of Nehrim's 43 script-assigned packages and 75 of Oblivion's 97
    appear on no actor at all.  MQ00CalebroPackage04 is the visible case —
    Nehrim's Celebro stops following the player because the package that should
    take over was never anywhere the engine could find it.

    Both call forms are recovered.  `ref.AddScriptPackage Pkg` names the actor
    explicitly; a bare `AddScriptPackage Pkg` targets the script's own owner,
    which for an INFO result script is the SPEAKER — resolved by the caller
    via `speaker_refs`, since only the dialogue converter knows who that is.
    """
    # `fid_to_edid` is keyed on RAW export FormIDs, but every id this plan
    # produces has to be in the same space as `get_formid()` — which applies
    # the load-order index offset — or PackagePlan's base_to_ref/alias lookups
    # miss and the ALPC is never written.
    edid_to_fid = {v.lower(): k for k, v in fid_to_edid.items() if v}
    offset = get_formid_index_offset()
    out = {}

    def _scan(text: str, implicit_ref: int = 0):
        if not text or 'addscriptpackage' not in text.lower():
            return
        for line in _script_lines(text):
            for m in _ADD_SCRIPT_PACKAGE_RE.finditer(line):
                pfid = edid_to_fid.get(m.group(2).lower())
                if not pfid:
                    continue
                ref = (edid_to_fid.get(m.group(1).lower()) if m.group(1)
                       else implicit_ref)
                if ref:
                    out.setdefault(remap_formid(pfid, offset), set()).add(
                        remap_formid(ref, offset))

    def _recs(sig):
        if master_export:
            for r in master_export.values():
                if r.get('Signature') == sig:
                    yield r
        yield from by_type.get(sig, [])

    _scan_scpt_packages(_recs, _scan)
    _scan_info_packages(_recs, _scan)
    _scan_qust_packages(_recs, _scan)
    return out


def forcegreet_reeval_owners(by_type: dict, master_export: dict = None) -> dict:
    """{gating-quest EditorID (lower) -> sorted [force-greet OWNER EditorID]}.

    PIECE 1 (coverage).  A force-greet whose gate is `GetStage(Q) [>=|==] N`
    fires when Q's stage advances, by injecting the OWNER actor's ref into Q's
    TES4SetStage re-eval set (assemble._reeval_actors).  This reaches owners NOT
    otherwise re-eval'd -- including the SCRIPTLESS ones -- because the nudge
    fires from the gating QUEST's fragment, not the owner's own script.

    Keyed on `is_forcegreet` + the GetStage condition->quest join, never a
    FormID/quest-name allowlist.  Every id is matched on its low 24 bits, so the
    result is independent of the load-order offset.  The OWNER's EditorID becomes
    the property name the quest VMAD binds (resolve_property_formid -> the base's
    placed ref), so no converter-computed key can shift a save.

    This mirrors `PackagePlan._own_quests`/`_wire_actor_packages` but is a
    self-contained pass on the raw export: the full PackagePlan is not built
    until after the quest-script VMADs are resolved, and the binding needs the
    owner names in hand before then.
    """
    from .converter import is_forcegreet_pack, forcegreet_stage_quest

    quest_edid = {}            # quest low24 -> EditorID
    for fid, rec in _master_records(master_export, 'QUST'):
        e = get_str(rec, 'EditorID')
        if e:
            quest_edid.setdefault(fid & 0x00FFFFFF, e)
    for rec in by_type.get('QUST', []):
        e = get_str(rec, 'EditorID')
        if e:
            quest_edid[get_formid(rec, 'FormID') & 0x00FFFFFF] = e

    pack_quest = {}            # force-greet pack low24 -> gating quest low24
    for fid, rec in _master_records(master_export, 'PACK'):
        if is_forcegreet_pack(rec):
            q = forcegreet_stage_quest(rec)
            if q:
                pack_quest[fid & 0x00FFFFFF] = q
    for rec in by_type.get('PACK', []):
        low = get_formid(rec, 'FormID') & 0x00FFFFFF
        q = forcegreet_stage_quest(rec) if is_forcegreet_pack(rec) else 0
        if q:
            pack_quest[low] = q           # own override wins
        else:
            pack_quest.pop(low, None)     # no longer a stage-gated force-greet

    owners = {}
    actor_recs = list(_master_records(master_export, 'NPC_', 'CREA'))
    actor_recs += [(get_formid(r, 'FormID'), r)
                   for r in by_type.get('NPC_', []) + by_type.get('CREA', [])]
    for _afid, rec in actor_recs:
        aedid = get_str(rec, 'EditorID')
        if not aedid:
            continue
        for i in range(get_int(rec, 'AIPackageCount')):
            p = get_formid(rec, f'AIPackage[{i}]')
            q = pack_quest.get(p & 0x00FFFFFF) if p else 0
            if not q:
                continue
            qe = quest_edid.get(q)
            if qe:
                owners.setdefault(qe.lower(), set()).add(aedid)
    return {k: sorted(v) for k, v in owners.items()}


def build_forcegreet_retire_latches(by_type: dict, writer,
                                    master_export: dict = None) -> dict:
    """Mint one latch GLOB (float 0.0) per MONOTONIC force-greet; return
    {force-greet PACK low24 -> latch GLOB FormID} (PIECE 2 retire).

    The package's OnEnd fragment (TES4_ForceGreetRetire) sets the latch to 1 once
    the greeting has run, and convert_PACK ANDs a `GetGlobalValue(latch) == 0`
    guard onto the package, so a never-re-closing `>=` stage greet fires once and
    then stops (the CGBaurusGreetPlayer loop).  `is_monotonic_forcegreet` is the
    shared discriminator (allowlist-free); the latch is named/keyed off the
    PACK's AUTHORED EditorID so derive_formid is a stable pure hash (no
    save-shift).  Own overrides win over a master's record, and each latch is
    minted exactly once.
    """
    from .converter import is_monotonic_forcegreet, retire_latch_name
    from ..overrides.adoption import generated_formid
    from ..record_types.common import (pack_record, pack_string_subrecord,
                                       pack_subrecord)

    monotonic = {}             # pack low24 -> (edid, fid); own wins
    for fid, rec in _master_records(master_export, 'PACK'):
        if is_monotonic_forcegreet(rec):
            monotonic[fid & 0x00FFFFFF] = (get_str(rec, 'EditorID'), fid)
    for rec in by_type.get('PACK', []):
        fid = get_formid(rec, 'FormID')
        low = fid & 0x00FFFFFF
        if is_monotonic_forcegreet(rec):
            monotonic[low] = (get_str(rec, 'EditorID'), fid)
        else:
            monotonic.pop(low, None)      # own override is no longer monotonic

    out = {}
    for low, (edid, fid) in monotonic.items():
        name = retire_latch_name(edid, fid)
        gfid = generated_formid(writer, 'GLOB', name, 'FORCEGREET_RETIRE',
                                edid or f'{low:06X}')
        subs = pack_string_subrecord('EDID', name)
        subs += pack_subrecord('FNAM', b'f')
        subs += pack_subrecord('FLTV', struct.pack('<f', 0.0))
        writer.add_record('GLOB', pack_record('GLOB', gfid, 0, subs))
        out[low] = gfid
    return out


class PackagePlan:
    """The quest/alias wiring for every converted package.

    Built once in Phase 0 (before QUST and PACK are converted) so that the QUST
    converter can emit the aliases and ALPCs, and the PACK converter can point
    its PTDA/PLDT at the same alias indices.  Both read this one object, so the
    indices cannot drift apart.
    """

    def __init__(self):
        self.owner_quest = {}      # pack_fid -> qust_fid
        self.quest_packages = {}   # qust_fid -> {actor_fid: [pack_fid, ...]}
        self.needed_aliases = {}   # qust_fid -> set(ref_fid)
        self.alias_index = {}      # (qust_fid, ref_fid) -> alias id
        self.actor_packages = {}   # actor_fid -> [pack_fid,...] (TES4 order)
        self.alias_actor = {}      # ref_fid -> base actor fid

    # -- build ----------------------------------------------------------

    def build(self, by_type: dict, quest_fids: set,
              scriptvar_owner: dict = None, master_export: dict = None,
              script_assigned: dict = None) -> None:
        """Wire packages to quests and aliases.

        `master_export` is the MASTERS' export records and is REQUIRED for a
        plugin with masters. A dependent plugin's actors overwhelmingly run
        THEIR MASTER'S packages (1,637 of ElsweyrAnequina.esp's 2,454 AIPackage
        references), and every lookup below is a resolution step: a package that
        is not in `packs`, an actor whose ACHR is not in `base_to_ref`, or a
        quest that is not in `quest_fids` simply drops out, so the actor falls
        back to its standing Sandbox schedule and the quest package never runs.

        Masters are added FIRST throughout, so this plugin's own record — an
        override included — overwrites the master's on the same key.
        """
        packs = {}
        for fid, rec in _master_records(master_export, 'PACK'):
            packs[fid] = rec
        for rec in by_type.get('PACK', []):
            fid = get_formid(rec, 'FormID')
            if fid:
                packs[fid] = rec

        # The masters' quests own the masters' packages. Keyed low-24 below, so
        # this only has to be the same id space as everything else here.
        quest_fids = set(quest_fids)
        quest_fids.update(fid for fid, _ in
                          _master_records(master_export, 'QUST'))

        scriptvar_owner = scriptvar_owner or {}

        self._own_quests(packs, quest_fids, scriptvar_owner)
        base_to_ref = self._wire_actor_packages(by_type, master_export)
        self._wire_script_assigned(base_to_ref, script_assigned)
        self._wire_package_targets(packs)

    def _own_quests(self, packs: dict, quest_fids: set,
                    scriptvar_owner: dict) -> None:
        """Fill `owner_quest` from each package's own conditions.

        Two gates appear in Oblivion and BOTH are handled: GetStage /
        GetQuestVariable names the quest directly, while
        GetScriptVariable(ref, var) is answered by asking who WRITES that
        variable (`scriptvar_owner`).  A CTDA param1 is a RAW TES4 FormID but
        `quest_fids` has the load-order offset applied, so the match is on the
        low 24 bits and the REMAPPED fid is kept as the owner.
        """
        quest_by_low = {q & 0x00FFFFFF: q for q in quest_fids}

        for fid, rec in packs.items():
            owner = None
            for qfid in _quest_fids_from_conditions(rec):
                owner = quest_by_low.get(qfid & 0x00FFFFFF)
                if owner:
                    break
            if owner is None:
                for ref in scriptvar_refs_from_conditions(rec):
                    owner = scriptvar_owner.get(ref & 0x00FFFFFF)
                    if owner:
                        break
            if owner:
                self.owner_quest[fid] = owner

    @staticmethod
    def _build_base_to_ref(by_type: dict, master_export: dict) -> dict:
        """base actor fid -> the persistent ACHR/ACRE that places it.

        The masters' placements are indexed first, then this plugin's own:
        "first ACHR wins" holds WITHIN each source, but an own placement of the
        same base overwrites the master's, since it is the one this plugin
        actually converts.
        """
        base_to_ref = {}
        for _fid, r in _master_records(master_export, 'ACHR', 'ACRE'):
            base = get_formid(r, 'NAME')
            if base and base not in base_to_ref:
                base_to_ref[base] = _fid
        own_refs = {}
        for sig in ('ACHR', 'ACRE'):
            for r in by_type.get(sig, []):
                base = get_formid(r, 'NAME')
                if base and base not in own_refs:
                    own_refs[base] = get_formid(r, 'FormID')
        base_to_ref.update(own_refs)
        return base_to_ref

    def _wire_actor_packages(self, by_type: dict, master_export: dict) -> dict:
        """Wire each actor's AIPackage list to its quest alias; return
        base_to_ref.

        A quest alias fills a *reference* (ALFR), so the actor's persistent
        ACHR takes the alias; actors with no ACHR (levelled spawns) keep their
        packages on the base record's PKID list.  Actor records are read
        masters-first, so an override re-pointing an AIPackage list replaces
        the master's entry.  Each alias list stays unique: a second ALPC for
        the same package says nothing the first does not.
        """
        base_to_ref = self._build_base_to_ref(by_type, master_export)
        actor_recs = [(fid, r) for fid, r
                      in _master_records(master_export, 'NPC_', 'CREA')]
        actor_recs += [(get_formid(r, 'FormID'), r)
                       for r in by_type.get('NPC_', []) + by_type.get('CREA', [])]
        for afid, rec in actor_recs:
            n = get_int(rec, 'AIPackageCount')
            plist = [get_formid(rec, f'AIPackage[{i}]') for i in range(n)]
            plist = [p for p in plist if p]
            if plist:
                self.actor_packages[afid] = plist
            aref = base_to_ref.get(afid)
            for pfid in plist:
                q = self.owner_quest.get(pfid)
                if q is None or aref is None:
                    continue
                _pkgs = self.quest_packages.setdefault(q, {}).setdefault(
                    aref, [])
                if pfid not in _pkgs:
                    _pkgs.append(pfid)
                self.needed_aliases.setdefault(q, set()).add(aref)
                self.alias_actor[aref] = afid
        return base_to_ref

    def _wire_script_assigned(self, base_to_ref: dict,
                              script_assigned: dict) -> None:
        """Hang `AddScriptPackage` packages off the actor's quest alias.

        These are in no actor's AI array — that is the whole point of the call
        — and Skyrim has no equivalent function, so an ALPC is the only way
        arbitration can ever select one.  A call may name the placed ACHR (the
        usual form) or the base actor; both normalize to the ref an alias
        fills, and anything else cannot take an alias and is skipped.
        """
        ref_to_base = {r: b for b, r in base_to_ref.items()}
        for pfid, refs in (script_assigned or {}).items():
            q = self.owner_quest.get(pfid)
            if q is None:
                continue
            for ref in refs:
                aref = ref if ref in ref_to_base else base_to_ref.get(ref)
                if aref is None:
                    continue
                pkgs = self.quest_packages.setdefault(q, {}).setdefault(aref, [])
                if pfid not in pkgs:
                    pkgs.append(pfid)
                self.needed_aliases.setdefault(q, set()).add(aref)
                self.alias_actor.setdefault(aref, ref_to_base.get(aref, ref))

    def _wire_package_targets(self, packs: dict) -> None:
        """Alias the refs quest packages point AT: escort/follow targets and
        PLDT "near reference" destinations."""
        for pfid, qfid in self.owner_quest.items():
            rec = packs.get(pfid)
            if rec is None:
                continue
            if get_int(rec, 'PTDT.Type', -1) == 0:
                tfid = get_formid(rec, 'PTDT.Target')
                if tfid:
                    self.needed_aliases.setdefault(qfid, set()).add(tfid)
            if get_int(rec, 'PLDT.Type', -1) == 0:
                lfid = get_formid(rec, 'PLDT.Location')
                if lfid:
                    self.needed_aliases.setdefault(qfid, set()).add(lfid)

    # -- alias index assignment -----------------------------------------

    def assign_aliases(self, qfid: int, existing: dict) -> list:
        """Allocate alias ids for a quest's package refs.

        `existing` is {ref_fid: alias_id} for aliases the QUST converter already
        created (its quest targets).  Returns the newly-added [(ref_fid,
        alias_id)] in id order.  Reuses an existing alias when the ref already
        has one — an actor that is both a quest target and a package runner gets
        ONE alias, not two.
        """
        added = []
        next_id = max(existing.values()) + 1 if existing else 0
        for ref in sorted(self.needed_aliases.get(qfid, ())):
            if ref in existing:
                self.alias_index[(qfid, ref)] = existing[ref]
                continue
            self.alias_index[(qfid, ref)] = next_id
            existing[ref] = next_id
            added.append((ref, next_id))
            next_id += 1
        return added

    def expand_packages(self, chains: dict) -> None:
        """Replace each source package with its chain (chain fids first, then
        the source) wherever an alias lists it, and give the chain fids the
        source's owner quest.  `chains`: source pack fid -> [chain fid, ...].
        See pack_converter.hunt_chain_targets."""
        for src, links in chains.items():
            q = self.owner_quest.get(src)
            if q is not None:
                for c in links:
                    self.owner_quest[c] = q
        for per_actor in self.quest_packages.values():
            for aref, pkgs in per_actor.items():
                if not any(p in chains for p in pkgs):
                    continue
                out = []
                for p in pkgs:
                    # Same uniqueness rule as the builder above: a chain link
                    # shared by two source packages, or a source already
                    # present, is not listed twice.
                    for c in chains.get(p, ()):
                        if c not in out:
                            out.append(c)
                    if p not in out:
                        out.append(p)
                per_actor[aref] = out

    def insert_escort_driver(self, qfid: int, escortee_ref: int,
                             driver_fid: int, before_fid: int) -> bool:
        """Inject an escortee-drive package onto the escortee's quest alias,
        immediately ABOVE `before_fid` (the no-op it must beat), and give it the
        quest's ownership.  First-condition-true-wins makes order priority, so
        the driver selects in exactly the no-op's window while any higher real
        mover on the escortee's stack still wins when its own gate opens (no
        over-extension past the window).  See converter.plan_escort_drivers.

        Returns False WITHOUT mutating anything when `before_fid` is not already
        on this escortee's alias list -- the caller guarantees it is (the
        tier-mismatch guard in plan_escort_drivers), so this only fends off a
        silent insert-at-0 that would stack the driver atop the whole alias and
        over-suppress the escortee's later movers."""
        pkgs = self.quest_packages.setdefault(qfid, {}).setdefault(
            escortee_ref, [])
        if driver_fid in pkgs:
            return True
        if before_fid not in pkgs:
            return False
        self.owner_quest[driver_fid] = qfid
        pkgs.insert(pkgs.index(before_fid), driver_fid)
        self.needed_aliases.setdefault(qfid, set()).add(escortee_ref)
        return True

    def alias_of(self, qfid: int, ref_fid: int):
        return self.alias_index.get((qfid, ref_fid))

    def packages_for_alias(self, qfid: int, actor_fid: int) -> list:
        """Packages to hang off this actor's alias on this quest (ALPC)."""
        return self.quest_packages.get(qfid, {}).get(actor_fid, [])

    def is_quest_package(self, pack_fid: int) -> bool:
        return pack_fid in self.owner_quest

    # -- reporting -------------------------------------------------------

    def summary(self) -> str:
        nq = len(set(self.owner_quest.values()))
        na = sum(len(v) for v in self.needed_aliases.values())
        return (f'{len(self.owner_quest)} quest-owned packages across {nq} '
                f'quests; {na} package aliases')
