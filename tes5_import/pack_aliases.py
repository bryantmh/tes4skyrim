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

from .text_reader import get_formid, get_int, get_str

# TES4 condition functions whose first parameter is a quest FormID.
QUEST_PARAM_FUNCS = frozenset({
    58,   # GetStage
    59,   # GetStageDone
    79,   # GetQuestVariable
    56,   # GetQuestRunning
})

PLAYER_FID = 0x00000014

# TES4 PKDT.Type values whose behaviour targets a specific reference.  These are
# the ones that need alias routing when quest-owned.
REF_TARGET_TYPES = frozenset({0, 1, 2, 7, 8, 9, 11})


def build_script_var_map(by_type: dict) -> dict:
    """ref_fid -> {var_index: var_name} for every scripted actor/object.

    A TES4 GetScriptVariable condition stores the variable's *script-local
    index* (SLSD) in param2 — the name exists only in the SCPT record.  To turn
    such a condition back into a named Papyrus property we need, for the
    reference the condition tests, the variable table of the script attached to
    that reference's BASE record.

    Chain: condition names a REFR -> the REFR's base NPC_/CREA/ACTI -> its SCRI
    -> the SCPT's Variable[i].Index/.Name.
    """
    # 1. SCPT fid -> {index: name}
    script_vars = {}
    for rec in by_type.get('SCPT', []):
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

    # 2. base record fid -> its script's variable table
    base_vars = {}
    for sig in ('NPC_', 'CREA', 'ACTI', 'CONT', 'DOOR', 'QUST'):
        for rec in by_type.get(sig, []):
            scri = get_formid(rec, 'SCRI') & 0x00FFFFFF
            if scri in script_vars:
                base_vars[get_formid(rec, 'FormID') & 0x00FFFFFF] = \
                    script_vars[scri]

    # 3. REFR/ACHR/ACRE fid -> base's table (conditions name the *reference*)
    out = dict(base_vars)
    for sig in ('REFR', 'ACHR', 'ACRE'):
        for rec in by_type.get(sig, []):
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


def _scriptvar_refs_from_conditions(rec: dict) -> list:
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
              scriptvar_owner: dict = None) -> None:
        packs = {}
        for rec in by_type.get('PACK', []):
            fid = get_formid(rec, 'FormID')
            if fid:
                packs[fid] = rec

        scriptvar_owner = scriptvar_owner or {}

        # 1. Quest ownership, from the package's own conditions.
        #
        # Two gates appear in Oblivion, and BOTH must be handled:
        #   * GetStage/GetQuestVariable  -> the quest is named directly.
        #   * GetScriptVariable(ref,var) -> the package is gated on an actor's
        #     script variable, which a quest's dialogue/stage script sets.  The
        #     quest is found by asking who WRITES that variable (scriptvar_owner,
        #     built from INFO/QUST result scripts).  FGC01Rats' escort package
        #     uses exactly this form, so skipping it loses the case we care about.
        for fid, rec in packs.items():
            owner = None
            for qfid in _quest_fids_from_conditions(rec):
                if qfid in quest_fids:
                    owner = qfid
                    break
            if owner is None:
                for ref in _scriptvar_refs_from_conditions(rec):
                    owner = scriptvar_owner.get(ref & 0x00FFFFFF)
                    if owner:
                        break
            if owner:
                self.owner_quest[fid] = owner

        # 2. Which actor runs which package (TES4 AIPackage order preserved).
        #
        # A quest alias fills a *reference* (ALFR), not a base actor, so the
        # actor's persistent ACHR is what gets the alias — and it is also what a
        # GetScriptVariable condition names.  Actors with no ACHR (levelled
        # spawns) can't take a quest alias; their packages stay on the base
        # record's PKID list.
        base_to_ref = {}
        for sig in ('ACHR', 'ACRE'):
            for r in by_type.get(sig, []):
                base = get_formid(r, 'NAME')
                if base and base not in base_to_ref:
                    base_to_ref[base] = get_formid(r, 'FormID')

        for rec in by_type.get('NPC_', []) + by_type.get('CREA', []):
            afid = get_formid(rec, 'FormID')
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
                self.quest_packages.setdefault(q, {}).setdefault(aref, []) \
                    .append(pfid)
                # The actor running a quest package needs an alias on that quest.
                self.needed_aliases.setdefault(q, set()).add(aref)
                self.alias_actor[aref] = afid

        # 3. Refs the quest packages point AT (escort/follow targets, e.g. the
        #    player; and PLDT "near reference" destinations).
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
