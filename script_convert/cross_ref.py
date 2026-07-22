"""Cross-reference graph for TES4 FormID/EditorID/Script lookups."""

import os
import re
from pathlib import Path

from script_convert.constants import papyrus_script_name
from tes5_import.text_reader import parse_export_file, unescape_value
from worker_budget import worker_count

# ===========================================================================
# Cross-reference graph builder
# ===========================================================================

# Record types the scan skips entirely: they have no EditorIDs and can never
# be referenced from a script, and together they are ~85% of the export bytes
# (LAND.txt alone is ~1.4 GB).
_SCAN_SKIP_SIGS = {'LAND', 'PGRD', 'ROAD'}

# Byte size of one scan job; big files split across workers at this grain.
_SCAN_CHUNK_BYTES = 16 * 1024 * 1024


def _new_scan_out() -> dict:
    return {
        'formid_to_edid': {}, 'edid_to_formid': {},
        'script_formid_to_edid': {}, 'script_formid_to_type': {},
        'record_scri': {}, 'record_base': {}, 'record_type': {},
        'quest_edids': set(), 'npc_formids': set(),
        'mgef_shaders': {}, 'spell_effects': {},
    }


def _scan_record_lines(sig: str, lines: list, out: dict):
    """Scan one record's KEY=VALUE lines into the partial result dicts."""
    formid = edid = scri = name_fid = None
    schr_type = None
    mgef_shader = mgef_ench = None
    mgef_school = -1
    spel_effects: list[tuple[str, int]] = []

    for line in lines:
        line = line.rstrip()
        if line.startswith('FormID='):
            formid = line[7:]
        elif line.startswith('EditorID='):
            edid = line[9:]
        elif line.startswith('SCRI='):
            scri = line[5:]
        elif line.startswith('NAME='):
            name_fid = line[5:]
        elif line.startswith('SCHR.Type='):
            try:
                schr_type = int(line[10:])
            except ValueError:
                pass
        elif sig == 'MGEF' and line.startswith('DATA.EffectShader='):
            mgef_shader = line[18:]
        elif sig == 'MGEF' and line.startswith('DATA.EnchantEffect='):
            mgef_ench = line[19:]
        elif sig == 'MGEF' and line.startswith('DATA.School='):
            try:
                mgef_school = int(line[12:])
            except ValueError:
                pass
        elif sig == 'SPEL' and line.startswith('Effect['):
            m = re.match(r'Effect\[(\d+)\]\.(EFID|ActorValue)=(.*)', line)
            if m:
                idx, key, val = int(m.group(1)), m.group(2), m.group(3)
                while len(spel_effects) <= idx:
                    spel_effects.append(('', -1))
                code, av = spel_effects[idx]
                if key == 'EFID':
                    code = val
                else:
                    try:
                        av = int(val)
                    except ValueError:
                        pass
                spel_effects[idx] = (code, av)

    if not formid:
        return
    if edid:
        out['formid_to_edid'][formid] = edid
        out['edid_to_formid'][edid.lower()] = formid
    if sig == 'SCPT':
        if edid:
            out['script_formid_to_edid'][formid] = edid
        if schr_type is not None:
            out['script_formid_to_type'][formid] = schr_type
    if scri:
        out['record_scri'][formid] = scri
    if name_fid and sig in ('ACHR', 'ACRE', 'REFR'):
        out['record_base'][formid] = name_fid
    out['record_type'][formid] = sig
    if sig == 'QUST' and edid:
        out['quest_edids'].add(edid.lower())
    if sig in ('NPC_', 'CREA'):
        out['npc_formids'].add(formid)
    if sig == 'MGEF' and edid:
        out['mgef_shaders'][edid.lower()] = (
            mgef_shader or '', mgef_ench or '', mgef_school)
    if sig == 'SPEL' and edid and spel_effects:
        out['spell_effects'][edid.lower()] = spel_effects


def _scan_range(args: tuple) -> dict:
    """Scan the records whose BEGIN delimiter starts in [start, end).

    args = (fpath, sig, start, end). Module-level so it is picklable for
    ProcessPoolExecutor; boundary rule matches text_reader.parse_file_range.
    """
    import mmap

    from tes5_import.text_reader import (_DELIM_BEGIN, _DELIM_END,
                                         _find_delim_line)

    fpath, sig, start, end = args
    out = _new_scan_out()
    try:
        f = open(fpath, 'rb')
    except OSError:
        return out
    with f:
        try:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # empty file
            return out
        try:
            begin = _find_delim_line(mm, _DELIM_BEGIN, start)
            while begin != -1 and begin < end:
                nl = mm.find(b'\n', begin)
                if nl < 0:
                    break
                rec_end = _find_delim_line(mm, _DELIM_END, nl + 1)
                if rec_end < 0:
                    break
                block = mm[nl + 1:rec_end].decode('utf-8', errors='replace')
                _scan_record_lines(sig, block.split('\n'), out)
                begin = _find_delim_line(mm, _DELIM_BEGIN,
                                         rec_end + len(_DELIM_END))
        finally:
            mm.close()
    return out


class CrossRefGraph:
    """Builds FormID->EditorID and EditorID->ScriptName lookup tables."""

    def __init__(self):
        self.formid_to_edid: dict[str, str] = {}
        self.edid_to_formid: dict[str, str] = {}
        self.script_formid_to_edid: dict[str, str] = {}
        self.script_formid_to_type: dict[str, int] = {}
        self.record_scri: dict[str, str] = {}  # record FormID -> SCRI FormID
        self.record_type: dict[str, str] = {}  # record FormID -> record Signature
        self.record_base: dict[str, str] = {}  # placed ref FormID -> base record FormID (NAME)
        self.quest_edids: set[str] = set()
        self.npc_formids: set[str] = set()
        # Cross-script ref-as-int analysis: set of (script_name_lower, var_name_lower)
        # where the TES4 `ref` variable is only ever assigned/compared with integers
        self.ref_as_int: set[tuple[str, str]] = set()
        # Per-script ref-typed variable names (populated by build_ref_as_int_map)
        self.script_ref_vars: dict[str, set[str]] = {}
        # Cross-script variable accesses: script_name_lower -> set of var_name_lower
        # Variables that are accessed from OTHER scripts (need to be Properties)
        self.cross_script_vars: dict[str, set[str]] = {}
        # Per-script ALL variable declarations: script_name_lower -> dict(var_low -> type_str)
        self.script_all_vars: dict[str, dict[str, str]] = {}
        # MGEF EditorID (lower) -> (EffectShader fid, EnchantEffect fid, school int)
        # Used to convert pme/PlayMagicEffectVisuals into EffectShader.Play().
        self.mgef_shaders: dict[str, tuple[str, str, int]] = {}
        # SPEL EditorID (lower) -> [(effect code, actor value int), ...]
        # Used to convert IsSpellTarget into a HasMagicEffect check on the
        # spell's first converted (Skyrim) magic effect.
        self.spell_effects: dict[str, list[tuple[str, int]]] = {}
    def load_from_export(self, export_dir: str, workers: int = None):
        """Load cross-reference data from all export .txt files.

        The scan is pure-Python line matching over ~2 GB of text, so files are
        split into byte ranges (record-boundary aligned, same contract as
        text_reader.parse_file_range) and scanned across a process pool.
        """
        if not os.path.isdir(export_dir):
            return

        jobs = []
        for fname in sorted(os.listdir(export_dir)):
            if not fname.endswith('.txt'):
                continue
            sig = fname[:-4]
            if sig in _SCAN_SKIP_SIGS:
                continue
            fpath = os.path.join(export_dir, fname)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                continue
            for start in range(0, size, _SCAN_CHUNK_BYTES):
                jobs.append((fpath, sig,
                             start, min(start + _SCAN_CHUNK_BYTES, size)))

        if workers is None:
            workers = worker_count()
        workers = min(workers, max(1, len(jobs)))
        if workers <= 1 or len(jobs) <= 2:
            results = map(_scan_range, jobs)
            for out in results:
                self._merge_scan(out)
        else:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=workers) as ex:
                # map preserves job order -> same last-wins merge semantics
                # as the old serial whole-file scan.
                for out in ex.map(_scan_range, jobs):
                    self._merge_scan(out)

    def _merge_scan(self, out: dict):
        """Fold one scan-range result (see _scan_range) into this graph."""
        self.formid_to_edid.update(out['formid_to_edid'])
        self.edid_to_formid.update(out['edid_to_formid'])
        self.script_formid_to_edid.update(out['script_formid_to_edid'])
        self.script_formid_to_type.update(out['script_formid_to_type'])
        self.record_scri.update(out['record_scri'])
        self.record_base.update(out['record_base'])
        self.record_type.update(out['record_type'])
        self.quest_edids.update(out['quest_edids'])
        self.npc_formids.update(out['npc_formids'])
        self.mgef_shaders.update(out['mgef_shaders'])
        self.spell_effects.update(out['spell_effects'])

    def get_extends_class(self, script_formid: str) -> str:
        """Determine the Papyrus extends class for a script."""
        schr_type = self.script_formid_to_type.get(script_formid, 0)

        if schr_type == 1:
            return 'Quest'
        if schr_type == 256:
            return 'ActiveMagicEffect'

        # Type 0: check if attached to NPC_/CREA -> Actor
        for rec_fid, scri_fid in self.record_scri.items():
            if scri_fid == script_formid:
                rec_sig = self.record_type.get(rec_fid, '')
                if rec_sig in ('NPC_', 'CREA'):
                    return 'Actor'
                if rec_sig == 'QUST':
                    return 'Quest'

        return 'ObjectReference'

    # TES4 magic-school enum -> the EFSH each school's enchantment glow uses.
    # Fallback for MGEFs with neither an EffectShader nor an EnchantEffect
    # (bound armor, summons): the school glow is what Oblivion shows on the
    # enchant anyway, and every one of these EditorIDs exists in Oblivion.esm.
    _SCHOOL_ENCHANT_SHADER = {
        0: 'effectenchantalteration', 1: 'effectenchantconjuration',
        2: 'effectenchantdestruction', 3: 'effectenchantillusion',
        4: 'effectenchantmysticism',  5: 'effectenchantrestoration',
    }

    def get_mgef_shader_edid(self, code: str) -> str:
        """EFSH EditorID for a TES4 magic-effect code (pme/sme argument).

        Preference order mirrors what Oblivion's PlayMagicEffectVisuals shows:
        the effect's own EffectShader, else its EnchantEffect shader, else the
        school's enchantment glow.  Returns '' if the code is unknown.
        """
        entry = self.mgef_shaders.get(code.lower())
        if not entry:
            return ''
        shader_fid, ench_fid, school = entry
        for fid in (shader_fid, ench_fid):
            if fid and int(fid, 16) != 0:
                edid = self.formid_to_edid.get(fid, '')
                if edid:
                    return edid
        fallback = self._SCHOOL_ENCHANT_SHADER.get(school, '')
        if fallback and fallback in self.edid_to_formid:
            return self.formid_to_edid.get(self.edid_to_formid[fallback], '')
        return ''

    def get_spell_first_skyrim_mgef(self, spell_name: str) -> int:
        """Skyrim MGEF FormID the converted spell's first surviving effect uses.

        IsSpellTarget has no Papyrus equivalent, but HasMagicEffect on the
        effect the imported SPEL actually carries is the same runtime test.
        Resolution MUST mirror tes5_import's _pack_effects: first effect whose
        code maps to a Skyrim MGEF wins; if every effect drops (script-effect
        spells), the importer substitutes its first filler effect, so detect
        that instead.  Returns 0 for an unknown spell.
        """
        effects = self.spell_effects.get(spell_name.lower())
        if not effects:
            return 0
        from tes5_import.skyrim_overrides import (MGEF_CODE_TO_SKYRIM,
                                                  MGEF_AV_CODE_TO_SKYRIM)
        for code, av in effects:
            if not code:
                continue
            per_av = MGEF_AV_CODE_TO_SKYRIM.get(code)
            fid = per_av.get(av, 0) if per_av is not None else 0
            fid = fid or MGEF_CODE_TO_SKYRIM.get(code, 0)
            if fid:
                return fid
        from tes5_import.record_types.equipment import _FILLER_EFFECTS
        return _FILLER_EFFECTS[0]

    def is_quest_ref(self, name: str) -> bool:
        """Check if a name refers to a known quest."""
        return name.lower() in self.quest_edids

    def get_quest_script_type(self, quest_name: str) -> str:
        """Get the Papyrus script class name for a quest, e.g. 'TES4_MyQuestScript'.
        Returns 'Quest' if no attached script is found."""
        low = quest_name.lower()
        fid = self.edid_to_formid.get(low, '')
        if not fid:
            return 'Quest'
        scri_fid = self.record_scri.get(fid, '')
        if not scri_fid:
            return 'Quest'
        script_edid = self.script_formid_to_edid.get(scri_fid, '')
        if not script_edid:
            return 'Quest'
        return papyrus_script_name(script_edid)

    def get_record_script_type(self, name: str) -> str:
        """Get the Papyrus script class name for any record with an attached script.
        For placed references (ACHR/ACRE/REFR), follows the NAME chain to the
        base record to find the attached script.
        Returns '' if the record has no attached script."""
        low = name.lower()
        fid = self.edid_to_formid.get(low, '')
        if not fid:
            return ''
        scri_fid = self.record_scri.get(fid, '')
        # For placed refs without own SCRI, follow base form chain
        if not scri_fid:
            base_fid = self.record_base.get(fid, '')
            if base_fid:
                scri_fid = self.record_scri.get(base_fid, '')
        if not scri_fid:
            return ''
        script_edid = self.script_formid_to_edid.get(scri_fid, '')
        if not script_edid:
            return ''
        return papyrus_script_name(script_edid)

    def build_ref_as_int_map(self, scpt_path: str):
        """Scan all SCPT SCTX sources to find ref variables used only as integers.

        TES4 'ref' type can hold both references and integers.  When a ref
        variable is only ever assigned/compared with numeric literals across
        ALL scripts that touch it, it should be typed Int in Papyrus.
        """
        records = parse_export_file(scpt_path)

        # Phase A: collect variable declarations per script
        _decl_re = re.compile(r'^\s*ref\s+(\w+)', re.IGNORECASE)
        _all_decl_re = re.compile(r'^\s*(short|long|float|ref)\s+(\w+)', re.IGNORECASE)
        _TES4_TO_PAPYRUS_TYPE = {'short': 'Int', 'long': 'Int', 'float': 'Float', 'ref': 'ObjectReference'}
        script_ref_vars: dict[str, set[str]] = {}
        script_all_vars: dict[str, dict[str, str]] = {}
        script_sources: dict[str, str] = {}

        for rec in records:
            edid = rec.get('EditorID', '')
            sctx = rec.get('SCTX', '')
            if not edid or not sctx:
                continue
            scn_low = edid.lower()
            script_sources[scn_low] = sctx
            ref_vars = set()
            all_vars: dict[str, str] = {}
            for line in sctx.split('\n'):
                stripped = line.strip()
                m = _decl_re.match(stripped)
                if m:
                    ref_vars.add(m.group(1).lower())
                am = _all_decl_re.match(stripped)
                if am:
                    vtype = am.group(1).lower()
                    vname = am.group(2).lower()
                    all_vars[vname] = _TES4_TO_PAPYRUS_TYPE.get(vtype, 'Int')
            if ref_vars:
                script_ref_vars[scn_low] = ref_vars
            if all_vars:
                script_all_vars[scn_low] = all_vars

        # Persist for cross-script type lookups
        self.script_ref_vars = script_ref_vars
        self.script_all_vars = script_all_vars

        if not script_ref_vars:
            return

        # Phase B: scan ALL scripts for usage of ref vars
        _set_re = re.compile(
            r'\bset\s+(?:(\w+)\.)?(\w+)\s+to\s+(.+)',
            re.IGNORECASE
        )
        # (script_lower, var_lower) -> {'zero', 'int', 'ref'}
        usage: dict[tuple[str, str], set[str]] = {}

        for scn_low, sctx in script_sources.items():
            for raw_line in sctx.split('\n'):
                line = raw_line.strip()
                if not line or line.startswith(';'):
                    continue

                # Detect ref usage: var.method() patterns on local ref variables
                if scn_low in script_ref_vars:
                    for ref_var in script_ref_vars[scn_low]:
                        if re.search(r'\b' + re.escape(ref_var) + r'\.\w+',
                                     line, re.IGNORECASE):
                            key = (scn_low, ref_var)
                            if key not in usage:
                                usage[key] = set()
                            usage[key].add('ref')

                # Check 'set [obj.]var to value' patterns
                sm = _set_re.match(line)
                if sm:
                    target_obj = (sm.group(1) or '').lower()
                    var_name = sm.group(2).lower()
                    value = sm.group(3).strip()
                    # Strip TES4 inline comments ("; comment text")
                    semi_idx = value.find(';')
                    if semi_idx >= 0:
                        value = value[:semi_idx].strip()
                    if target_obj:
                        owner = target_obj
                    else:
                        owner = scn_low
                    # Resolve owner to its script name
                    owner_script = None
                    if owner in script_ref_vars and var_name in script_ref_vars[owner]:
                        owner_script = owner
                    elif owner != scn_low:
                        base_fid = self.edid_to_formid.get(owner, '')
                        if base_fid:
                            scri_fid = self.record_scri.get(base_fid, '')
                            if scri_fid:
                                se = self.script_formid_to_edid.get(scri_fid, '')
                                if se:
                                    se_low = se.lower()
                                    if se_low in script_ref_vars and var_name in script_ref_vars[se_low]:
                                        owner_script = se_low

                    if owner_script:
                        key = (owner_script, var_name)
                        if key not in usage:
                            usage[key] = set()
                        if re.match(r'^-?\d+(\.\d+)?$', value):
                            if value.strip() == '0':
                                usage[key].add('zero')
                            else:
                                usage[key].add('int')
                        else:
                            usage[key].add('ref')

        # Phase C: ref vars with ONLY non-zero integer usage -> retype to Int
        for (script_low, var_low), types in usage.items():
            if 'ref' not in types and 'int' in types:
                self.ref_as_int.add((script_low, var_low))

        # Phase D: detect cross-script variable access (Owner.VarName patterns)
        # These variables must be Properties on the owning script so other scripts
        # can access them. Scans SCPT sources, INFO result scripts, and QUST stage scripts.
        _owner_var_re = re.compile(r'\b(\w+)\.(\w+)\b')
        cross_script_vars: dict[str, set[str]] = {}

        def _scan_text_for_cross_access(text):
            for raw_line in text.split('\n'):
                line = raw_line.strip()
                if not line or line.startswith(';'):
                    continue
                semi = line.find(';')
                if semi >= 0:
                    line = line[:semi]
                for match in _owner_var_re.finditer(line):
                    owner = match.group(1).lower()
                    var = match.group(2).lower()
                    target_script = None
                    if owner in script_all_vars and var in script_all_vars[owner]:
                        target_script = owner
                    else:
                        fid = self.edid_to_formid.get(owner, '')
                        if fid:
                            scri_fid = self.record_scri.get(fid, '')
                            if scri_fid:
                                se = self.script_formid_to_edid.get(scri_fid, '')
                                if se:
                                    se_low = se.lower()
                                    if se_low in script_all_vars and var in script_all_vars[se_low]:
                                        target_script = se_low
                    if target_script:
                        if target_script not in cross_script_vars:
                            cross_script_vars[target_script] = set()
                        cross_script_vars[target_script].add(var)

        # Scan all SCPT sources
        for scn_low, sctx in script_sources.items():
            _scan_text_for_cross_access(sctx)

        # Scan INFO result scripts and QUST stage scripts for cross-script access
        export_dir = os.path.dirname(scpt_path)
        for extra_file, field_name in [('INFO.txt', 'ResultScript'), ('QUST.txt', 'SCTX')]:
            extra_path = os.path.join(export_dir, extra_file)
            if not os.path.isfile(extra_path):
                continue
            try:
                with open(extra_path, 'r', encoding='utf-8') as f:
                    for raw_line in f:
                        if raw_line.startswith(field_name + '='):
                            text = raw_line[len(field_name) + 1:].strip()
                            text = text.replace('\\r\\n', '\n').replace('\\n', '\n')
                            _scan_text_for_cross_access(text)
            except Exception:
                pass

        self.cross_script_vars = cross_script_vars
    def is_remote_ref_var(self, owner_edid: str, var_name: str) -> bool:
        """Check if a variable on a remote record's script is ref-typed in TES4.

        *owner_edid* is the EditorID of the quest/NPC/object (e.g. 'MQ00').
        *var_name* is the property name (e.g. 'nearOblivionGate').
        Returns True if the remote script declares that variable as 'ref'
        AND it is not a ref-as-int variable (used only as integers).
        """
        var_low = var_name.lower()
        owner_low = owner_edid.lower()
        # Direct script name match
        if owner_low in self.script_ref_vars:
            if var_low in self.script_ref_vars[owner_low]:
                return (owner_low, var_low) not in self.ref_as_int
            return False
        # Resolve owner EditorID -> script name
        fid = self.edid_to_formid.get(owner_low, '')
        if not fid:
            return False
        scri_fid = self.record_scri.get(fid, '')
        if not scri_fid:
            return False
        se = self.script_formid_to_edid.get(scri_fid, '')
        if not se:
            return False
        se_low = se.lower()
        if se_low in self.script_ref_vars:
            if var_low in self.script_ref_vars[se_low]:
                return (se_low, var_low) not in self.ref_as_int
        return False


# ===========================================================================
# Script converter
# ===========================================================================

