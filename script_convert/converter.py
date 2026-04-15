"""ScriptConverter class — core TES4→Papyrus line-by-line conversion."""

import re
from typing import Optional

from script_convert.constants import (
    BLOCK_MAP, TYPE_MAP, ACTOR_VALUE_MAP, KNOWN_GLOBALS,
    _PAPYRUS_RESERVED, FUNCTION_MAP, _BARE_BOOL_FUNCTIONS,
    _ACTOR_ONLY_FUNCTIONS, _OBJREF_SHARED_FUNCTIONS,
    _safe_property_name, _canonical_global, _record_type_to_papyrus,
)
from script_convert.cross_ref import CrossRefGraph


class ScriptConverter:
    """Converts Oblivion script source to Papyrus .psc source."""

    def __init__(self, xref: CrossRefGraph):
        self.xref = xref
        self._property_refs: dict[str, str] = {}
        self._has_gamemode = False
        self._has_menumode = False
        self._has_scripteffectupdate = False
        self._uses_getsecondspassed = False
        self._uses_timer = False
        self._local_vars = set()
        self._var_renames: dict[str, str] = {}  # orig_lower -> safe_name
        self._var_types: dict[str, str] = {}  # lower_name -> papyrus_type
        self._current_event: str = ''  # Current event header for context-aware conversion
        self._line_comments: list[str] = []  # Comments accumulated during expression conversion

    def _is_ref_typed_access(self, dotted_expr: str) -> bool:
        """Check if a dotted expression (e.g. 'SEHerdirRef.TargetRef') accesses a ref-typed variable.

        Checks both via EditorID resolution and via property type → script_all_vars.
        """
        if '.' not in dotted_expr:
            return False
        parts = dotted_expr.strip().split('.', 1)
        prop_low = parts[0].lower()
        var_low = parts[1].lower()
        # Method 1: resolve via EditorID
        if self.xref and self.xref.is_remote_ref_var(parts[0], parts[1]):
            return True
        # Method 2: resolve via property type declaration
        if self.xref:
            for pname, ptype in self._property_refs.items():
                if pname.lower() == prop_low and ptype.startswith('TES4_'):
                    script_name = ptype[5:].lower()
                    all_vars = self.xref.script_all_vars.get(script_name, {})
                    if all_vars.get(var_low) in ('ObjectReference', 'Actor'):
                        if (script_name, var_low) not in self.xref.ref_as_int:
                            return True
        return False

    def _ref_has_script_var(self, ref_name: str, var_name: str) -> bool:
        """Check if ref_name resolves to a script that declares var_name as a variable.

        Used to disambiguate Quest.variable vs Quest.function().
        """
        if not self.xref:
            return False
        var_low = var_name.lower()
        # Method 1: direct script name or via property type
        for pname, ptype in self._property_refs.items():
            if pname.lower() == ref_name.lower() and ptype.startswith('TES4_'):
                script_name = ptype[5:].lower()
                all_vars = self.xref.script_all_vars.get(script_name, {})
                if var_low in all_vars:
                    return True
        # Method 2: look up via EditorID → SCRI → script vars
        fid = self.xref.edid_to_formid.get(ref_name.lower(), '')
        if fid:
            scri_fid = self.xref.record_scri.get(fid, '')
            if scri_fid:
                se = self.xref.script_formid_to_edid.get(scri_fid, '')
                if se:
                    all_vars = self.xref.script_all_vars.get(se.lower(), {})
                    if var_low in all_vars:
                        return True
        return False

    def _is_ref_as_int_crossscript(self, dotted_expr: str) -> bool:
        """Check if a cross-script dotted var (e.g. 'MQConversations.OGDeadDaedra') was retyped to Int.

        Returns True if the variable exists in ref_as_int for the owning script.
        """
        if '.' not in dotted_expr or not self.xref:
            return False
        parts = dotted_expr.strip().split('.', 1)
        prop_low = parts[0].lower()
        var_low = parts[1].lower()
        # Resolve property type to script name
        for pname, ptype in self._property_refs.items():
            if pname.lower() == prop_low:
                if ptype.startswith('TES4_'):
                    script_name = ptype[5:].lower()
                else:
                    script_name = ptype.lower()
                if (script_name, var_low) in self.xref.ref_as_int:
                    return True
        # Also try EditorID resolution
        prop_upper = parts[0]
        edid = self.xref.edid_to_formid.get(prop_upper) or self.xref.edid_to_formid.get(prop_upper.lower())
        if edid:
            scri_fid = self.xref.record_scri.get(edid)
            if scri_fid:
                script_edid = self.xref.script_formid_to_edid.get(scri_fid, '').lower()
                if script_edid and (script_edid, var_low) in self.xref.ref_as_int:
                    return True
        return False

    @staticmethod
    def _infer_extends(source: str, extends: str) -> str:
        """Pre-scan source for bare Actor-only function calls; upgrade extends."""
        for func in _ACTOR_ONLY_FUNCTIONS:
            # Match bare calls (not preceded by '.') anywhere in source
            if re.search(r'(?<!\.)(?<!\w)' + re.escape(func) + r'(?:\s|$|\()',
                         source, re.IGNORECASE):
                return 'Actor'
        return extends

    def convert_standalone(self, name: str, source: str, extends: str = 'ObjectReference',
                           editor_id: str = '') -> str:
        """Convert a standalone SCPT record to a full .psc file."""
        saved_refs = dict(self._property_refs)
        self._reset()
        self._property_refs = saved_refs

        # Pre-scan: if script uses Actor-only functions on self (no ref prefix),
        # upgrade extends to Actor
        if extends == 'ObjectReference':
            extends = self._infer_extends(source, extends)

        variables, blocks = self._parse_source(source)
        # Store locally declared variable names for expression disambiguation
        self._local_vars = {_safe_property_name(v[1]).lower() for v in variables}
        # Store variable types for type-aware assignment conversion
        _edid_low = editor_id.lower()
        for v in variables:
            vtype_low = v[0].lower()
            vname_safe = _safe_property_name(v[1])
            ptype = TYPE_MAP.get(vtype_low, 'Int')
            if ptype == 'ObjectReference' and _edid_low and \
               (_edid_low, vname_safe.lower()) in self.xref.ref_as_int:
                ptype = 'Int'
            self._var_types[vname_safe.lower()] = ptype
        # Build rename map: original_lower -> safe_name (only when they differ)
        for _, vname in variables:
            safe = _safe_property_name(vname)
            if safe.lower() != vname.lower():
                self._var_renames[vname.lower()] = safe

        source_low = source.lower()
        self._uses_getsecondspassed = 'getsecondspassed' in source_low
        self._uses_timer = bool(re.search(r'\btimer\b', source_low))
        self._has_gamemode = any(b[0] == 'gamemode' for b in blocks)
        self._has_menumode = any(b[0] == 'menumode' for b in blocks)
        self._has_scripteffectupdate = any(b[0] == 'scripteffectupdate' for b in blocks)

        out = []
        out.append(f'ScriptName TES4_{name} extends {extends}')
        out.append(f'{{Converted from TES4: {editor_id or name}}}')
        out.append('')

        # Variable declarations as properties (type may be upgraded after conversion)
        _var_info = []
        _seen_vars = set()
        for vtype, vname in variables:
            ptype = TYPE_MAP.get(vtype, 'Int')
            safe_vname = _safe_property_name(vname)
            if safe_vname.lower() in _seen_vars:
                continue  # skip duplicate declarations
            _seen_vars.add(safe_vname.lower())
            # Override ref vars that are only used with integers (cross-script analysis)
            if ptype == 'ObjectReference' and _edid_low and \
               (_edid_low, safe_vname.lower()) in self.xref.ref_as_int:
                ptype = 'Int'
            _var_info.append((safe_vname, ptype))
        var_start_idx = len(out)
        for safe_vname, ptype in _var_info:
            if ptype == 'Float':
                out.append(f'{ptype} Property {safe_vname} = 0.0 Auto')
            else:
                out.append(f'{ptype} Property {safe_vname} Auto')

        if variables:
            out.append('')

        # Convert blocks — merge duplicate event types
        needs_oninit_update = self._has_gamemode or self._has_scripteffectupdate
        gamemode_body = []

        # Group blocks by event type to merge duplicates (Papyrus forbids
        # duplicate Event declarations)
        from collections import defaultdict
        merged_blocks: dict[str, list] = defaultdict(list)
        block_order: list[str] = []

        for block_type, block_lines in blocks:
            if block_type in ('gamemode', 'menumode', 'scripteffectupdate'):
                gamemode_body.extend(block_lines)
                continue

            # Merge blocks by their target Papyrus event name, not TES4 block type
            # This prevents duplicate events (e.g. onadd+ondrop→OnContainerChanged)
            mapping = BLOCK_MAP.get(block_type)
            merge_key = mapping[0] if mapping else block_type
            if merge_key not in merged_blocks:
                block_order.append(merge_key)
            merged_blocks[merge_key].extend(block_lines)

        for merge_key in block_order:
            all_lines = merged_blocks[merge_key]
            # merge_key is already the event_begin string (or the block_type if unmapped)
            self._current_event = merge_key
            if merge_key.startswith('Event ') or merge_key.startswith(';'):
                event_begin = merge_key
                event_end = 'EndEvent' if merge_key.startswith('Event ') else ''
                if not event_begin.startswith(';'):
                    out.append(event_begin)
                    for bline in all_lines:
                        converted = self._convert_line(bline, extends)
                        out.append(f'  {converted}')
                    out.append(event_end)
                else:
                    # Unsupported event — comment out all code to avoid top-level errors
                    out.append(event_begin)
                    for bline in all_lines:
                        converted = self._convert_line(bline, extends)
                        out.append(f'  ;{converted}')
                    if event_end:
                        out.append(event_end)
                out.append('')
            else:
                # Unknown block type — comment it out
                out.append(f';TODO: Unknown event block: {merge_key}')
                for bline in all_lines:
                    converted = self._convert_line(bline, extends)
                    out.append(f'  ;{converted}')
                out.append('')

        # Emit OnUpdate for GameMode/MenuMode/ScriptEffectUpdate
        if gamemode_body:
            interval = self._get_update_interval()
            self._current_event = 'Event OnUpdate()'
            out.append('Event OnUpdate()')
            for bline in gamemode_body:
                converted = self._convert_line(bline, extends)
                out.append(f'  {converted}')
            out.append(f'  RegisterForSingleUpdate({interval})')
            out.append('EndEvent')
            out.append('')

        # Emit OnInit for RegisterForSingleUpdate
        if needs_oninit_update:
            has_oninit = any(b[0] == 'oninit' for b in blocks)
            if not has_oninit:
                interval = self._get_update_interval()
                out.append('Event OnInit()')
                out.append(f'  RegisterForSingleUpdate({interval})')
                out.append('EndEvent')
                out.append('')

        # Balance If/EndIf within event blocks (some TES4 scripts have extra EndIf)
        out = self._balance_if_endif(out)

        # Remove dead code after Return statements within event/function blocks
        out = self._remove_dead_code_after_return(out)

        # Apply shared post-processing (TES4-only functions, type mismatches, etc.)
        out = self._postprocess_lines(out)

        # Post-process: retype ObjectReference variables that are only used as integers
        # TES4 'ref' type was general-purpose; scripts often used ref vars as int flags
        if _var_info:
            _ref_typed_vars = {name.lower(): idx for idx, (name, ptype) in enumerate(_var_info)
                            if ptype == 'ObjectReference'}
            if _ref_typed_vars:
                _assign_re = re.compile(r'^\s*(\w+)\s*=\s*(.+)', re.IGNORECASE)
                for var_low, vi in list(_ref_typed_vars.items()):
                    has_int_assign = False
                    has_ref_assign = False
                    has_ref_usage = False
                    for line in out[var_start_idx + len(_var_info) + 1:]:
                        # Check if variable is used as a reference (method calls, comparisons with refs)
                        if re.search(r'\b' + re.escape(var_low) + r'\.\w+\s*\(', line, re.IGNORECASE):
                            has_ref_usage = True
                            break
                        # Check for comparisons with None or ref variables
                        if re.search(r'\b' + re.escape(var_low) + r'\s*[!=]=\s*None\b', line, re.IGNORECASE):
                            has_ref_usage = True
                            break
                        # Check if variable is used as a function argument (not on LHS of =)
                        stripped = line.lstrip()
                        if not stripped.startswith(var_low) and re.search(
                                r'\(\s*' + re.escape(var_low) + r'\b', line, re.IGNORECASE):
                            has_ref_usage = True
                            break
                        am = _assign_re.match(line)
                        if not am:
                            continue
                        if am.group(1).lower() != var_low:
                            continue
                        val = am.group(2).split(';')[0].strip()
                        # Integer literal assignments (0, 1, 2, etc.)
                        if re.match(r'^-?\d+$', val):
                            has_int_assign = True
                        # None assignment (already converted from ref = 0)
                        elif val == 'None':
                            has_ref_assign = True
                        # Math expressions producing int
                        elif re.match(r'^[\w.]+ [+\-*/] \d+$', val):
                            has_int_assign = True
                        else:
                            has_ref_assign = True
                    if has_int_assign and not has_ref_assign and not has_ref_usage:
                        # Retype: ObjectReference → Int
                        decl_idx = var_start_idx + vi
                        if decl_idx < len(out):
                            out[decl_idx] = out[decl_idx].replace(
                                'ObjectReference Property', 'Int Property', 1)
                            real_name = _var_info[vi][0]
                            self._var_types[real_name.lower()] = 'Int'
                            # Also replace = None back to = 0 in body
                            none_re = re.compile(
                                r'^(\s*' + re.escape(real_name) + r'\s*=\s*)None\b',
                                re.IGNORECASE)
                            for bidx in range(var_start_idx + len(_var_info) + 1, len(out)):
                                if none_re.match(out[bidx]):
                                    out[bidx] = none_re.sub(r'\g<1>0', out[bidx])

        # Post-process: upgrade ObjectReference/Actor variables to more specific types
        # based on usage (Actor from actor-only functions, or script type from SCRO/xref)
        if _var_info and self._property_refs:
            # Build case-insensitive lookup for type upgrades
            _ci_refs = {k.lower(): v for k, v in self._property_refs.items()}
            for idx in range(var_start_idx, var_start_idx + len(_var_info)):
                if idx >= len(out):
                    break
                line = out[idx]
                # Upgrade ObjectReference → script type or Actor
                if 'ObjectReference Property ' in line:
                    parts = line.split('Property ', 1)
                    if len(parts) >= 2:
                        prop_name = parts[1].split()[0]
                        new_type = _ci_refs.get(prop_name.lower(), '')
                        if new_type and new_type != 'ObjectReference':
                            out[idx] = line.replace('ObjectReference Property ',
                                                    f'{new_type} Property ', 1)
                            self._var_types[prop_name.lower()] = new_type
                # Upgrade Actor → TES4_ script type when cross-script property access needed
                elif 'Actor Property ' in line:
                    parts = line.split('Property ', 1)
                    if len(parts) >= 2:
                        prop_name = parts[1].split()[0]
                        new_type = _ci_refs.get(prop_name.lower(), '')
                        if new_type and new_type.startswith('TES4_'):
                            out[idx] = line.replace('Actor Property ',
                                                    f'{new_type} Property ', 1)
                            self._var_types[prop_name.lower()] = new_type

        # Post-process: add 'as Actor' casts for ObjRef-returning assignments to Actor vars
        _actor_vars = {k.lower() for k, v in self._property_refs.items() if v == 'Actor'}
        _actor_vars |= {k for k, v in self._var_types.items() if v == 'Actor'}
        if _actor_vars:
            _objref_re = self._OBJREF_RETURNING
            _objref_params = self._OBJREF_PARAMS
            # Build set of variables known to be ObjectReference
            _objref_vars = {k for k, v in self._var_types.items() if v == 'ObjectReference'}
            _objref_vars |= {k.lower() for k, v in self._property_refs.items() if v == 'ObjectReference'}
            for idx in range(len(out)):
                line = out[idx]
                s = line.lstrip()
                # Match: VarName = expr  (not already cast)
                eq_m = re.match(r'^(\w+)\s*=\s*(.+)', s)
                if not eq_m:
                    continue
                var_name = eq_m.group(1)
                val = eq_m.group(2).rstrip()
                if var_name.lower() not in _actor_vars:
                    continue
                if 'as Actor' in val:
                    continue
                # Strip inline comments for checking
                val_check = val.split(';')[0].strip() if ';' in val else val
                needs_cast = False
                if _objref_re.search(val_check):
                    needs_cast = True
                elif val_check.lower().strip('() ') in _objref_params:
                    needs_cast = True
                elif val_check.lower() in _objref_vars:
                    needs_cast = True
                elif val_check == 'Self' and extends != 'Actor':
                    needs_cast = True
                elif '.' in val_check and self._is_ref_typed_access(val_check):
                    needs_cast = True
                if needs_cast:
                    indent = line[:len(line) - len(s)]
                    # Insert 'as Actor' before any inline comment
                    if ';' in val and not val.startswith(';'):
                        code_part = val.split(';')[0].rstrip()
                        comment_part = ';' + val.split(';', 1)[1]
                        out[idx] = f'{indent}{var_name} = {code_part} as Actor  {comment_part}'
                    else:
                        out[idx] = f'{indent}{var_name} = {val} as Actor'

        # Post-process: cast ObjRef-typed args to Actor in actor-parameter functions
        _actor_param_funcs = re.compile(
            r'\b(?:StartCombat|IsDetectedBy|PushActorAway|SendAssaultAlarm|GetRelationshipRank'
            r'|SetRelationshipRank|SetPlayerTeammate)\s*\(',
            re.IGNORECASE)
        _all_objref = _objref_vars if '_objref_vars' in dir() else set()
        _all_objref |= {k for k, v in self._var_types.items() if v == 'ObjectReference'}
        _all_objref |= {k.lower() for k, v in self._property_refs.items() if v == 'ObjectReference'}
        if _all_objref:
            for idx in range(len(out)):
                line = out[idx]
                if not _actor_param_funcs.search(line):
                    continue
                # Replace ObjRef variables with 'var as Actor' in function args
                for var in _all_objref:
                    # Match var as a whole word inside parentheses, not already cast
                    pattern = r'(\b' + re.escape(var) + r')(\b)(?!\s+as\s+Actor)'
                    if re.search(pattern, line, re.IGNORECASE):
                        line = re.sub(pattern, r'\1 as Actor\2', line, flags=re.IGNORECASE)
                out[idx] = line

        # Post-process: convert integer literal assignments to None for ref-typed variables
        # Handles both local (someActorVar = 0) and cross-script (Quest.Var = 1)
        if self._property_refs or self._var_types:
            _ref_types = ('ObjectReference', 'Actor', 'ActorBase')
            _assign_int_re = re.compile(r'^(\s*)([\w.]+)\s*=\s*(-?\d+)\s*(;.*)?$')
            for idx in range(len(out)):
                m = _assign_int_re.match(out[idx])
                if not m:
                    continue
                tgt = m.group(2)
                int_val = m.group(3)
                is_ref = False
                if '.' in tgt:
                    # Cross-script: check remote ref type via xref graph
                    parts = tgt.split('.', 1)
                    if self.xref and self.xref.is_remote_ref_var(parts[0], parts[1]):
                        is_ref = True
                else:
                    low = tgt.lower()
                    vtype = self._var_types.get(low, '')
                    if not vtype:
                        vtype = self._property_refs.get(tgt, self._property_refs.get(low, ''))
                    if vtype in _ref_types or vtype.startswith('TES4_'):
                        is_ref = True
                if is_ref:
                    cmt = m.group(4) or ''
                    out[idx] = f'{m.group(1)}{tgt} = None  {cmt}'.rstrip()

        # Post-process: add 'as Int' for cross-script Float args in item count functions
        # (RemoveItem/AddItem count param should be Int but cross-script may be Float)
        _item_count_re = re.compile(
            r'(\.(RemoveItem|AddItem)\s*\(\s*\w+\s*,\s*)(\w+\.\w+)(\s*\))',
            re.IGNORECASE)
        for idx in range(len(out)):
            m = _item_count_re.search(out[idx])
            if m and ' as Int' not in m.group(3):
                out[idx] = out[idx][:m.start(3)] + m.group(3) + ' as Int' + out[idx][m.end(3):]

        # Post-process: fix conditions containing embedded comments that break parsing
        # e.g. "If (False  ;comment == 0)" → the ; eats the ==0 part
        # Only fix if the ; appears BEFORE operators/comparands (not trailing comments)
        _cond_with_embedded_comment = re.compile(
            r'^(\s*(?:If|ElseIf)\s+)(.*);(.+(?:==|!=|>=|<=|>|<|&&|\|\||\)).*)$',
            re.IGNORECASE)
        for idx in range(len(out)):
            m = _cond_with_embedded_comment.match(out[idx])
            if m:
                full_cond = m.group(2) + ';' + m.group(3)
                out[idx] = f'{m.group(1)}True  ;{full_cond.strip()}'

        # Post-process: fix assignments where RHS contains embedded comment that eats operators
        # e.g. "temp = (False  ;comment == 0)" → just the comment
        for idx in range(len(out)):
            line = out[idx]
            assign_m = re.match(r'^(\s*)(\w[\w.]*)\s*=\s*(.*)$', line)
            if assign_m:
                rhs = assign_m.group(3)
                semi_pos = rhs.find(';')
                if semi_pos >= 0:
                    # Check if there's meaningful code after the comment that was eaten
                    after_semi = rhs[semi_pos+1:]
                    if re.search(r'==|!=|>=|<=|>|<|&&|\|\||\)', after_semi):
                        out[idx] = f'{assign_m.group(1)}{rhs[semi_pos:]}'

        # Post-process: remove spurious commas from conditions
        # e.g. "if((, expr, == , 1, ))" → "if(expr == 1)"
        for idx in range(len(out)):
            line = out[idx]
            if ',  ==' in line or ', ==' in line or '==,' in line or '== ,' in line:
                # Strip commas that are not inside string literals
                cleaned = re.sub(r',\s*', ' ', line)
                # Collapse multiple spaces
                cleaned = re.sub(r'  +', ' ', cleaned)
                # Restore indentation
                indent = len(line) - len(line.lstrip())
                cleaned = line[:indent] + cleaned.lstrip()
                out[idx] = cleaned

        # Post-process: fix "None as Int/Float" casts (can't cast None to Int/Float)
        # These arise when a TODO function returns None but variable is Int/Float
        for idx in range(len(out)):
            line = out[idx]
            if 'None as Int' in line:
                out[idx] = line.replace('None as Int', '0')
            elif 'None as Float' in line:
                out[idx] = line.replace('None as Float', '0.0')

        # Post-process: promote local variables used across events to properties
        # TES4 locals are script-scoped; Papyrus locals are event-scoped
        _event_re = re.compile(r'^\s*Event\s+(\w+)', re.IGNORECASE)
        _endevent_re = re.compile(r'^\s*EndEvent\b', re.IGNORECASE)
        _local_decl_re = re.compile(r'^(\s*)(Int|Float|Bool|String|ObjectReference|Actor)\s+(\w+)\s*=', re.IGNORECASE)
        _local_use_re = {}  # Populated per-variable below
        # Pass 1: find local declarations and their owning events
        event_locals = {}  # var_name_lower -> (event_name, decl_line_idx, type, indent)
        current_event = None
        for idx, line in enumerate(out):
            em = _event_re.match(line)
            if em:
                current_event = em.group(1)
                continue
            if _endevent_re.match(line):
                current_event = None
                continue
            if current_event:
                dm = _local_decl_re.match(line)
                if dm:
                    vname = dm.group(3)
                    vtype = dm.group(2)
                    event_locals[vname.lower()] = (current_event, idx, vtype, dm.group(1))
        # Pass 2: find variables used in events OTHER than where declared
        promote = {}  # var_name_lower -> (vtype, decl_idx)
        if event_locals:
            for var_low, (decl_event, decl_idx, vtype, indent) in event_locals.items():
                current_event = None
                for idx, line in enumerate(out):
                    em = _event_re.match(line)
                    if em:
                        current_event = em.group(1)
                        continue
                    if _endevent_re.match(line):
                        current_event = None
                        continue
                    if current_event and current_event != decl_event:
                        if re.search(r'\b' + re.escape(var_low) + r'\b', line, re.IGNORECASE):
                            promote[var_low] = (vtype, decl_idx)
                            break
        # Pass 2b: promote variables accessed from OTHER scripts (cross-script access)
        if event_locals and self.xref and _edid_low:
            cross_vars = self.xref.cross_script_vars.get(_edid_low, set())
            for var_low in cross_vars:
                if var_low in event_locals and var_low not in promote:
                    _, decl_idx, vtype, _ = event_locals[var_low]
                    promote[var_low] = (vtype, decl_idx)
        # Promote: remove local declaration, add as property at top
        _promoted_props = []
        for var_low, (vtype, decl_idx) in promote.items():
            # Comment out the local declaration
            out[decl_idx] = f';{out[decl_idx].lstrip()}  ;promoted to property'
            _promoted_props.append((_safe_property_name(var_low), vtype))

        # Insert property declarations for referenced FormIDs
        if self._property_refs or _promoted_props:
            # Collect declared variable names (case-insensitive) to avoid collisions
            declared = {v[0].lower() for v in _var_info}
            insert_idx = 3 + len(_var_info) + (1 if _var_info else 0)
            prop_lines = []
            # Insert promoted local→property declarations first
            for pname, ptype in _promoted_props:
                if pname.lower() not in declared:
                    default = ' = 0.0' if ptype == 'Float' else (' = None' if ptype in ('ObjectReference', 'Actor') else '')
                    prop_lines.append(f'{ptype} Property {pname} Auto')
                    declared.add(pname.lower())
            if self._property_refs:
                prop_lines.append('; --- External references (auto-linked via VMAD) ---')
                # Merge case-variant keys: prefer the most specific type (non-Quest wins)
                _merged: dict[str, tuple[str, str]] = {}
                for pname, ptype in sorted(self._property_refs.items()):
                    key = pname.lower()
                    if key in _merged:
                        _, ex_type = _merged[key]
                        if ex_type == 'Quest' and ptype != 'Quest':
                            _merged[key] = (pname, ptype)
                    else:
                        _merged[key] = (pname, ptype)
                for pname, ptype in sorted(_merged.values(), key=lambda x: x[0].lower()):
                    safe_name = _safe_property_name(pname)
                    if safe_name.lower() in declared:
                        continue  # skip if already declared as a variable
                    declared.add(safe_name.lower())
                    prop_lines.append(f'{ptype} Property {safe_name} Auto')
            prop_lines.append('')
            for i, pl in enumerate(prop_lines):
                out.insert(insert_idx + i, pl)

        return '\n'.join(out)

    def convert_fragment(self, source: str, extends: str = 'Quest') -> list[str]:
        """Convert a script fragment body (not a full script).

        Returns list of converted lines (indented for function body).
        Preserves _property_refs across calls (quest fragments share a converter).
        """
        # Reset conversion state but preserve accumulated property_refs
        saved_refs = dict(self._property_refs)
        self._reset()
        self._property_refs = saved_refs
        lines = source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        result = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped:
                result.append('')
                continue
            low = stripped.lower()
            if low.startswith('scriptname ') or low.startswith('scn '):
                continue
            if re.match(r'^(short|long|int|float|ref|reference)\s+\w+', stripped, re.IGNORECASE):
                m = re.match(r'^(short|long|int|float|ref|reference)\s+(\w+)', stripped, re.IGNORECASE)
                if m:
                    ptype = TYPE_MAP.get(m.group(1).lower(), 'Int')
                    vname = m.group(2)
                    if ptype == 'Float':
                        result.append(f'  {ptype} {vname} = 0.0')
                    else:
                        result.append(f'  {ptype} {vname} = 0')
                continue
            if low.startswith('begin ') or low == 'end':
                continue
            result.append(f'  {self._convert_line(raw_line, extends)}')
        # Apply shared post-processes to fragment lines
        result = self._postprocess_lines(result)
        return result

    def _postprocess_lines(self, lines: list[str]) -> list[str]:
        """Shared post-processing for both standalone and fragment scripts."""
        # Fix akActionRef used in events that don't define it
        # TES4 scripts could use GetActionRef across blocks; Papyrus scopes params to events
        _event_re2 = re.compile(r'^\s*Event\s+(\w+)', re.IGNORECASE)
        _endevent_re2 = re.compile(r'^\s*EndEvent\b', re.IGNORECASE)
        _EVENTS_WITH_ACTIONREF = {'ontriggerenter', 'ontrigger', 'onactivate'}
        current_event = None
        has_actionref = False
        for idx in range(len(lines)):
            em = _event_re2.match(lines[idx])
            if em:
                current_event = em.group(1).lower()
                has_actionref = current_event in _EVENTS_WITH_ACTIONREF
                continue
            if _endevent_re2.match(lines[idx]):
                current_event = None
                has_actionref = False
                continue
            if current_event and not has_actionref and 'akActionRef' in lines[idx]:
                # Replace undefined akActionRef with Self
                lines[idx] = lines[idx].replace('akActionRef', 'Self')
        # Fix cross-script Float args in item count functions
        _item_count_re = re.compile(
            r'(\.(RemoveItem|AddItem)\s*\(\s*\w+\s*,\s*)(\w+\.\w+)(\s*\))',
            re.IGNORECASE)
        for idx in range(len(lines)):
            m = _item_count_re.search(lines[idx])
            if m and ' as Int' not in m.group(3):
                lines[idx] = lines[idx][:m.start(3)] + m.group(3) + ' as Int' + lines[idx][m.end(3):]
        # Fix conditions containing embedded comments that break parsing
        _cond_with_embedded_comment = re.compile(
            r'^(\s*(?:If|ElseIf)\s+)(.*);(.+(?:==|!=|>=|<=|>|<|&&|\|\||\)).*)$',
            re.IGNORECASE)
        for idx in range(len(lines)):
            m = _cond_with_embedded_comment.match(lines[idx])
            if m:
                full_cond = m.group(2) + ';' + m.group(3)
                lines[idx] = f'{m.group(1)}True  ;{full_cond.strip()}'
        # Fix assignments where RHS contains embedded comment that eats operators
        for idx in range(len(lines)):
            line = lines[idx]
            assign_m = re.match(r'^(\s*)(\w[\w.]*)\s*=\s*(.*)$', line)
            if assign_m:
                rhs = assign_m.group(3)
                semi_pos = rhs.find(';')
                if semi_pos >= 0:
                    after_semi = rhs[semi_pos+1:]
                    if re.search(r'==|!=|>=|<=|>|<|&&|\|\||\)', after_semi):
                        lines[idx] = f'{assign_m.group(1)}{rhs[semi_pos:]}'
        # Fix standalone no-op results (bare "0  ;comment" statements)
        _standalone_noop_re = re.compile(r'^(\s*)0\s+(;.*)$')
        for idx in range(len(lines)):
            m = _standalone_noop_re.match(lines[idx])
            if m:
                lines[idx] = f'{m.group(1)}{m.group(2)}'
        # Fix None as Int/Float
        for idx in range(len(lines)):
            line = lines[idx]
            if 'None as Int' in line:
                lines[idx] = line.replace('None as Int', '0')
            elif 'None as Float' in line:
                lines[idx] = line.replace('None as Float', '0.0')
        # Replace TES4-only condition functions used as property accesses
        # e.g. Game.GetPlayer().HasVampireFed == 1 → True  ;TODO: HasVampireFed
        _tes4_only_props = re.compile(
            r'\b(HasVampireFed|GetIsCreature|getClothingValue)\b',
            re.IGNORECASE)
        for idx in range(len(lines)):
            m = _tes4_only_props.search(lines[idx])
            if not m:
                continue
            func_name = m.group(1)
            line = lines[idx]
            indent = len(line) - len(line.lstrip())
            stripped = line.strip()
            # If it's an If/ElseIf condition, replace with True + TODO
            if re.match(r'(?:If|ElseIf)\b', stripped, re.IGNORECASE):
                kw = stripped.split()[0]
                lines[idx] = ' ' * indent + f'{kw} True  ;TODO: {func_name} - No Papyrus equivalent ({stripped})'
            else:
                # Assignment or other statement — comment it out
                lines[idx] = ' ' * indent + f';TODO: {func_name} - No Papyrus equivalent: {stripped}'
        # Fix spurious commas in conditions
        for idx in range(len(lines)):
            line = lines[idx]
            if ',  ==' in line or ', ==' in line or '==,' in line or '== ,' in line:
                cleaned = re.sub(r',\s*', ' ', line)
                cleaned = re.sub(r'  +', ' ', cleaned)
                indent = len(line) - len(line.lstrip())
                lines[idx] = line[:indent] + cleaned.lstrip()
        # Fix Int vs Package/Topic/Form comparisons (TES4 GetCurrentAIPackage → TODO)
        # e.g. "If (0 == SE10GoldenSaintPray2x12)" where SE10GoldenSaintPray2x12 is Package
        _type_mismatch_types = {'Package', 'Topic', 'MiscObject'}
        _cond_re = re.compile(r'^(\s*(?:If|ElseIf)\s+).*\b(\w+)\b.*$', re.IGNORECASE)
        for idx in range(len(lines)):
            m = _cond_re.match(lines[idx])
            if not m:
                continue
            line_stripped = lines[idx].strip()
            # Check if any identifier in the condition is a Package/Topic/MiscObject property
            for ident in re.findall(r'\b([a-zA-Z_]\w*)\b', line_stripped):
                ptype = self._property_refs.get(ident, self._property_refs.get(ident.lower(), ''))
                if ptype in _type_mismatch_types:
                    kw = line_stripped.split()[0]
                    indent = len(lines[idx]) - len(line_stripped)
                    lines[idx] = ' ' * indent + f'{kw} True  ;TODO: Type mismatch fix ({line_stripped})'
                    break
        # Fix integer assignments to cross-script ref-typed variables
        if self.xref:
            _assign_int_re = re.compile(r'^(\s*)([\w.]+)\s*=\s*(-?\d+)\s*(;.*)?$')
            for idx in range(len(lines)):
                m = _assign_int_re.match(lines[idx])
                if not m:
                    continue
                tgt = m.group(2)
                # fQuestDelayTime → RegisterForSingleUpdate (TES4 built-in)
                if tgt.lower().endswith('.fquestdelaytime'):
                    val = m.group(3)
                    fval = float(val) if val != '0' else 0
                    indent = m.group(1)
                    if fval > 0:
                        lines[idx] = f'{indent}RegisterForSingleUpdate({val}.0)  ;fQuestDelayTime'
                    else:
                        lines[idx] = f'{indent}UnregisterForUpdate()  ;fQuestDelayTime = 0'
                    continue
                if '.' in tgt:
                    if self._is_ref_typed_access(tgt) and not self._is_ref_as_int_crossscript(tgt):
                        lines[idx] = f'{m.group(1)}{tgt} = None  {m.group(4) or ""}'.rstrip()
        # Fix TODO comments inside function call arguments
        # e.g. "RemoveItem(Gold001, ;TODO: ...)" -> "RemoveItem(Gold001)  ;TODO: ..."
        for idx in range(len(lines)):
            line = lines[idx]
            if ';TODO' not in line or '(' not in line:
                continue
            semi_idx = line.find(';TODO')
            if semi_idx < 0:
                continue
            code_before = line[:semi_idx]
            open_count = code_before.count('(') - code_before.count(')')
            if open_count > 0:
                # TODO is inside unclosed parens - close properly
                todo_text = line[semi_idx:].rstrip().rstrip(')')
                code = code_before.rstrip().rstrip(',').rstrip()
                code += ')' * open_count
                lines[idx] = f'{code}  {todo_text}'
        # Fix Say() with Int variable as Topic arg (TES4 used FormIDs interchangeably)
        for idx in range(len(lines)):
            say_m = re.search(r'\.Say\((\w+)\)', lines[idx])
            if say_m:
                arg = say_m.group(1)
                arg_type = self._var_types.get(arg.lower(), '') or self._property_refs.get(arg, self._property_refs.get(arg.lower(), ''))
                if arg_type == 'Int':
                    indent = len(lines[idx]) - len(lines[idx].lstrip())
                    lines[idx] = ' ' * indent + f';TODO: Say() needs Topic form, not Int ({lines[idx].strip()})'
        # Fix Self assigned to Actor property: Self → Self as Actor
        for idx in range(len(lines)):
            m = re.match(r'^(\s*)(\w+)\s*=\s*Self\s*(;.*)?$', lines[idx])
            if m:
                tgt_low = m.group(2).lower()
                ptype = self._var_types.get(tgt_low, '') or self._property_refs.get(m.group(2), self._property_refs.get(tgt_low, ''))
                if ptype == 'Actor':
                    comment = m.group(3) or ''
                    lines[idx] = f'{m.group(1)}{m.group(2)} = Self as Actor  {comment}'.rstrip()
        # Fix ActorBase vs Script type comparisons (e.g. GetActorBase() == ScriptProperty)
        # TES4 compared refs directly to base objects - in Papyrus, base form needs GetBaseObject()
        for idx in range(len(lines)):
            line = lines[idx]
            if '.GetActorBase()' not in line:
                continue
            comp_m = re.search(r'(GetActorBase\(\))\s*(==|!=)\s*(\w+)', line)
            if comp_m:
                rhs = comp_m.group(3)
                ptype = self._property_refs.get(rhs, self._property_refs.get(rhs.lower(), ''))
                if ptype and ptype.startswith('TES4_'):
                    # Replace GetActorBase() == ScriptProp with GetActorBase() == ScriptProp.GetActorBase()
                    indent = len(line) - len(line.lstrip())
                    kw = line.strip().split()[0]
                    lines[idx] = ' ' * indent + f'{kw} True  ;TODO: ActorBase vs Script type ({line.strip()})'
        # Fix ObjectReference vs MiscObject/Ingredient/etc comparisons
        # TES4: "akActionRef == SomeMiscObject" → Papyrus: "akActionRef.GetBaseObject() == SomeMiscObject"
        _base_form_types = {'MiscObject', 'Ingredient', 'Potion', 'Weapon', 'Armor', 'Book', 'Key'}
        for idx in range(len(lines)):
            line = lines[idx]
            comp_m = re.search(r'\b(akActionRef|\w+Ref)\s*(==|!=)\s*(\w+)', line)
            if comp_m:
                rhs = comp_m.group(3)
                ptype = self._property_refs.get(rhs, self._property_refs.get(rhs.lower(), ''))
                if ptype in _base_form_types:
                    old_expr = comp_m.group(0)
                    new_expr = f'{comp_m.group(1)}.GetBaseObject() {comp_m.group(2)} {rhs}'
                    lines[idx] = line.replace(old_expr, new_expr)
        return lines

    def get_property_refs(self) -> dict[str, str]:
        """Get accumulated external property references."""
        return dict(self._property_refs)

    # -----------------------------------------------------------------------
    # Private
    # -----------------------------------------------------------------------

    def _reset(self):
        self._property_refs = {}
        self._has_gamemode = False
        self._has_menumode = False
        self._has_scripteffectupdate = False
        self._uses_getsecondspassed = False
        self._uses_timer = False
        self._local_vars = set()
        self._var_renames = {}
        self._var_types = {}

    @staticmethod
    def _balance_if_endif(lines: list[str]) -> list[str]:
        """Balance If/EndIf within event/function blocks.

        Remove extra EndIf/Else/ElseIf that don't have matching If.
        Insert missing EndIf before EndEvent/EndFunction.
        """
        result = []
        depth = 0
        in_event = False
        for line in lines:
            stripped = line.strip().lower()
            # Strip inline comments for keyword matching
            code_part = stripped.split(';')[0].strip() if ';' in stripped else stripped
            if code_part.startswith('event ') or code_part.startswith('function '):
                in_event = True
                depth = 0
            elif code_part in ('endevent', 'endfunction'):
                # Insert missing EndIf statements before closing
                while depth > 0:
                    result.append('EndIf')
                    depth -= 1
                in_event = False
                depth = 0
            elif in_event:
                if code_part.startswith('if ') or code_part.startswith('if(') or code_part == 'if':
                    depth += 1
                elif code_part.startswith('elseif '):
                    if depth <= 0:
                        continue  # orphaned ElseIf
                elif code_part == 'else':
                    if depth <= 0:
                        continue  # orphaned Else
                elif code_part == 'endif':
                    if depth <= 0:
                        # Extra EndIf — skip it
                        continue
                    depth -= 1
            result.append(line)
        return result

    @staticmethod
    def _remove_dead_code_after_return(lines: list[str]) -> list[str]:
        """Comment out executable code after Return at event/function top-level."""
        result = []
        in_dead_zone = False
        depth = 0  # if/while nesting depth
        for line in lines:
            stripped = line.strip().lower()
            if stripped.startswith('event ') or stripped.startswith('function '):
                in_dead_zone = False
                depth = 0
            elif stripped in ('endevent', 'endfunction'):
                in_dead_zone = False
                depth = 0
            elif in_dead_zone:
                # Allow empty lines and comments through
                if stripped and not stripped.startswith(';'):
                    result.append(f';  {line.strip()}  ;dead code after Return')
                    continue
            else:
                # Track nesting
                if stripped.startswith('if ') or stripped == 'if':
                    depth += 1
                elif stripped == 'endif':
                    depth = max(0, depth - 1)
                elif stripped.startswith('while '):
                    depth += 1
                elif stripped == 'endwhile':
                    depth = max(0, depth - 1)
                elif stripped == 'return' and depth == 0:
                    in_dead_zone = True
            result.append(line)
        return result

    def _get_update_interval(self) -> str:
        if self._uses_getsecondspassed:
            return '0.1'
        if self._uses_timer:
            return '0.25'
        return '0.5'

    def _parse_source(self, source: str):
        """Parse Oblivion source into (variables, blocks)."""
        lines = source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        variables = []
        blocks = []
        current_block = None
        current_lines = []
        _seen_vars = set()

        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(';'):
                if current_block is not None:
                    current_lines.append(raw_line)
                continue

            low = stripped.lower()

            if low.startswith('scriptname ') or low.startswith('scn '):
                continue

            # Variable declarations — TES4 vars are ALWAYS script-global,
            # even if declared inside a begin/end block
            m = re.match(r'^(short|long|int|float|ref|reference)\s+(\w+)', stripped, re.IGNORECASE)
            if m:
                vname_low = m.group(2).lower()
                if vname_low not in _seen_vars:
                    variables.append((m.group(1).lower(), m.group(2)))
                    _seen_vars.add(vname_low)
                continue  # Don't add var decls to block lines

            begin_m = re.match(r'^begin\s+(\w+)(.*)', stripped, re.IGNORECASE)
            if begin_m:
                current_block = begin_m.group(1).lower()
                current_lines = []
                continue

            if low == 'end':
                if current_block is not None:
                    blocks.append((current_block, current_lines))
                    current_block = None
                    current_lines = []
                continue

            if current_block is not None:
                current_lines.append(raw_line)

        return variables, blocks

    def _convert_line(self, line: str, extends: str) -> str:
        """Convert a single Oblivion script line to Papyrus."""
        stripped = line.strip()
        if not stripped:
            return ''
        if stripped.startswith(';'):
            return stripped

        # Strip inline comments (;) that aren't inside string literals
        # TES4 uses ; for comments, these must not leak into Papyrus expressions
        inline_comment = ''
        in_str = False
        for ci, ch in enumerate(stripped):
            if ch == '"':
                in_str = not in_str
            elif ch == ';' and not in_str:
                inline_comment = '  ' + stripped[ci:]
                stripped = stripped[:ci].rstrip()
                break

        if not stripped:
            return inline_comment.strip() if inline_comment else ''

        # Clear accumulated expression-level comments before conversion
        self._line_comments.clear()

        result = self._convert_line_inner(stripped, extends)

        # Append any accumulated expression-level comments (from no-op functions)
        if self._line_comments:
            comments = '  '.join(self._line_comments)
            self._line_comments.clear()
            # If result is just '0' (standalone no-op), replace with comment
            if result.strip() == '0':
                result = comments
            elif not result.lstrip().startswith(';'):
                result = f'{result}  {comments}'

        if inline_comment and not result.lstrip().startswith(';'):
            return result + inline_comment
        return result

    def _convert_line_inner(self, stripped: str, extends: str) -> str:
        """Core line conversion logic (no inline-comment handling)."""
        low = stripped.lower()

        # Variable declarations inside blocks — already declared as Properties by _parse_source
        var_m = re.match(r'^(short|long|int|float|ref|reference)\s+(\w+)', stripped, re.IGNORECASE)
        if var_m:
            # Variable already declared as a Property; skip the inline declaration
            return ''

        # set X to Y
        set_m = re.match(r'^set\s+(\S+)\s+to\s+(.*)', stripped, re.IGNORECASE)
        if set_m:
            target = self._convert_ref(set_m.group(1), extends)
            value = self._convert_expression(set_m.group(2), extends)
            # Can't assign to Self/GetTargetActor()/akSpeakerRef in Papyrus
            if target in ('Self', 'GetTargetActor()', 'akSpeakerRef'):
                return f';{target} = {value}  ;cannot assign to Self in Papyrus'
            # akSpeakerRef is ObjectReference; cast when assigned to Actor-typed fields
            if extends == 'TopicInfo' and value == 'akSpeakerRef':
                value = '(akSpeakerRef as Actor)'
            # In AME/TopicInfo scripts, Self refers to the target actor, not the script
            if value == 'Self':
                if extends == 'ActiveMagicEffect':
                    value = 'GetTargetActor()'
                elif extends == 'TopicInfo':
                    value = '(akSpeakerRef as Actor)'
            if value.lstrip().startswith(';TODO:'):
                # Use None for ref-typed targets, 0 for others
                tgt_low_todo = target.lower().split('.')[-1]
                tgt_type_todo = self._var_types.get(tgt_low_todo, '') or self._property_refs.get(target, self._property_refs.get(tgt_low_todo, ''))
                if tgt_type_todo == 'GlobalVariable':
                    return f'{target}.SetValue(0)  {value}'
                dflt = 'None' if tgt_type_todo in ('ObjectReference', 'Actor', 'ActorBase') or tgt_type_todo.startswith('TES4_') else '0'
                return f'{target} = {dflt}  {value}'
            value = self._fix_ref_zero(target, value)
            # Say() returns None in Papyrus but TES4 returned audio duration
            if '.Say(' in value or value.startswith('Say('):
                # Extract the Say() call using balanced-paren matching
                # TES4: "set timer to (ref.Say topic args) + delay" → "(ref.Say(topic)) + 0.2"
                say_idx = value.find('.Say(')
                if say_idx < 0:
                    say_idx = value.find('Say(')
                if say_idx >= 0:
                    # Find the closing paren of the Say call args
                    paren_start = value.index('(', say_idx)
                    depth = 0
                    paren_end = paren_start
                    for ci in range(paren_start, len(value)):
                        if value[ci] == '(':
                            depth += 1
                        elif value[ci] == ')':
                            depth -= 1
                            if depth == 0:
                                paren_end = ci
                                break
                    # Find the start of the Say expression: scan backward with paren depth
                    expr_start = 0
                    bk_depth = 0
                    for ci in range(say_idx - 1, -1, -1):
                        ch = value[ci]
                        if ch == ')':
                            bk_depth += 1
                        elif ch == '(':
                            if bk_depth > 0:
                                bk_depth -= 1
                            else:
                                expr_start = ci + 1
                                break
                        elif ch in ' \t' and bk_depth == 0:
                            expr_start = ci + 1
                            break
                    say_call = value[expr_start:paren_end + 1]
                    # Strip balanced outer wrapping parens: "(ref.Say(topic))" → "ref.Say(topic)"
                    if say_call.startswith('(') and say_call.endswith(')'):
                        inner = say_call[1:-1]
                        d = 0
                        balanced = True
                        for ch in inner:
                            if ch == '(':
                                d += 1
                            elif ch == ')':
                                d -= 1
                                if d < 0:
                                    balanced = False
                                    break
                        if balanced and d == 0:
                            say_call = inner
                    remainder = value[paren_end + 1:].strip()
                    # If remainder is just "+ number", extract the delay for the timer
                    delay_m = re.match(r'[+\-]\s*([\d.]+)', remainder) if remainder else None
                    delay_val = delay_m.group(1) if delay_m else '0.0'
                    return f'{say_call}\n  {target} = {delay_val}  ;Say() returns None in Papyrus; delay approximated'
                return f'{value}\n  {target} = 0.0  ;Say() returns None in Papyrus'
            # GlobalVariable: use SetValue() instead of direct assignment
            tgt_low = target.lower().split('.')[-1]
            if self._property_refs.get(target, self._property_refs.get(tgt_low, '')) == 'GlobalVariable':
                # Strip inline TODO comments from value to avoid broken parentheses
                val_clean = value.split(';TODO')[0].rstrip() if ';TODO' in value else value
                todo_part = '  ;TODO' + value.split(';TODO', 1)[1] if ';TODO' in value else ''
                return f'{target}.SetValue({val_clean}){todo_part}'
            # fQuestDelayTime cross-script access → RegisterForUpdate
            if target.endswith('.fQuestDelayTime'):
                quest_ref = target.rsplit('.', 1)[0]
                return f'{quest_ref}.RegisterForUpdate({value})'
            # Float→Int coercion: if target is Int and value is from a Float-returning function, cast
            value = self._coerce_float_to_int(target, value)
            # ObjectReference→Actor coercion: if target is Actor and value is ObjectReference param, cast
            value = self._coerce_ref_to_actor(target, value)
            # Cross-script ref→Int mismatch: TES4 allowed storing refs in short variables
            if '.' in target:
                parts = target.split('.', 1)
                owner_type = self._property_refs.get(parts[0], self._property_refs.get(parts[0].lower(), ''))
                if owner_type and owner_type.startswith('TES4_'):
                    remote_script = owner_type[5:].lower()
                    remote_vars = self.xref.script_all_vars.get(remote_script, {})
                    remote_type = remote_vars.get(parts[1].lower(), '')
                    val_low = value.strip().lower()
                    is_ref_value = (
                        'gettargetactor()' in val_low or
                        'getself' in val_low or
                        val_low == 'self' or
                        val_low == 'akspeakerref' or
                        self._OBJREF_RETURNING.search(value.strip()) is not None
                    )
                    if remote_type == 'Int' and is_ref_value:
                        return f';{target} = {value}  ;TES4 stored ref in short'
            return f'{target} = {value}'

        # let X := Y (OBSE)
        let_m = re.match(r'^let\s+(\S+)\s*:=\s*(.*)', stripped, re.IGNORECASE)
        if let_m:
            target = self._convert_ref(let_m.group(1), extends)
            value = self._convert_expression(let_m.group(2), extends)
            if value.lstrip().startswith(';TODO:'):
                tgt_low_todo = target.lower().split('.')[-1]
                tgt_type_todo = self._var_types.get(tgt_low_todo, '') or self._property_refs.get(target, self._property_refs.get(tgt_low_todo, ''))
                dflt = 'None' if tgt_type_todo in ('ObjectReference', 'Actor', 'ActorBase') or tgt_type_todo.startswith('TES4_') else '0'
                return f'{target} = {dflt}  {value}'
            value = self._fix_ref_zero(target, value)
            return f'{target} = {value}'

        # if / elseif
        if_m = re.match(r'^(if|elseif)\s+(.*)', stripped, re.IGNORECASE)
        if if_m:
            keyword = 'If' if if_m.group(1).lower() == 'if' else 'ElseIf'
            condition = self._convert_expression(if_m.group(2), extends)
            # If the condition converted entirely to a ;TODO comment, keep the
            # block structure valid by using True as placeholder
            if condition.lstrip().startswith(';TODO:'):
                return f'{keyword} True  {condition}'
            return f'{keyword} {condition}'

        if low == 'else':
            return 'Else'
        # TES4 allows "else <condition>" as equivalent to "elseif <condition>"
        if low.startswith('else ') and not low.startswith('elseif'):
            rest = stripped[5:].strip()
            if rest:
                # TES4 also allows "else if <cond>" — strip leading 'if' keyword
                rest_low = rest.lower()
                if rest_low.startswith('if '):
                    rest = rest[3:].strip()
                condition = self._convert_expression(rest, extends)
                if condition.lstrip().startswith(';TODO:'):
                    return f'ElseIf True  {condition}'
                return f'ElseIf {condition}'
            return 'Else'
        if low == 'endif' or low.startswith('endif') and not low[5:6].isalpha():
            return 'EndIf'
        if low == 'return':
            return 'Return'
        # return followed by a comment or anything (TES4 return has no value)
        if low.startswith('return ') or low.startswith('return;'):
            rest = stripped[6:].strip()
            if rest.startswith(';'):
                return f'Return  {rest}'
            value = self._fix_ref_zero(target, value)
            return f'{target} = {value}'

        return self._convert_function_call(stripped, extends)

    def _fix_ref_zero(self, target: str, value: str) -> str:
        """If target is a ref-typed variable and value is an integer literal, return 'None'.

        TES4 scripts often use ref vars as boolean flags (set refVar to 0/1).
        In Papyrus, Actor/ObjectReference cannot hold integers, so convert to None.
        """
        val_stripped = value.strip()
        if not re.match(r'^-?\d+$', val_stripped):
            return value
        # Check local/declared var type first (takes priority)
        tgt_low = target.lower().split('.')[-1]  # handle quest.var as var
        vtype = self._var_types.get(tgt_low, '')
        if vtype in ('ObjectReference', 'Actor', 'ActorBase') or vtype.startswith('TES4_'):
            return 'None'
        if vtype:
            return value  # Known non-ref type, don't convert
        # Check property refs (cross-script variables) only if not a declared var
        ptype = self._property_refs.get(target, self._property_refs.get(tgt_low, ''))
        if ptype in ('ObjectReference', 'Actor', 'ActorBase') or ptype.startswith('TES4_'):
            # But if the cross-script var was retyped to Int via ref_as_int, keep integer
            if '.' in target and self.xref and self._is_ref_as_int_crossscript(target):
                return value  # retyped to Int, keep integer
            return 'None'
        # Check cross-script ref type via xref graph (e.g. MQ00.nearOblivionGate)
        if '.' in target:
            parts = target.split('.', 1)
            if self.xref and self.xref.is_remote_ref_var(parts[0], parts[1]):
                if self._is_ref_as_int_crossscript(target):
                    return value
                return 'None'
            # Also check via property type → script_all_vars (property name != EditorID)
            if self._is_ref_typed_access(target):
                if self._is_ref_as_int_crossscript(target):
                    return value
                return 'None'
        return value

    # Functions that return Float in Papyrus (need 'as Int' cast when assigned to Int vars)
    _FLOAT_RETURNING_FUNCS = re.compile(
        r'(?:GetBaseActorValue|GetActorValue|GetAV|GetSecondsPassed|GetDistance|'
        r'GetPosition[XYZ]|GetAngle[XYZ]|GetHeadingAngle|GetScale|GetLevel|'
        r'GetPos[XYZ]|GetWalkSpeed|GetCurrentTime|RandomFloat|Utility\.RandomFloat|'
        r'GetHeight|GetWidth|GetLength|GetValue)\s*\(', re.IGNORECASE)

    def _coerce_float_to_int(self, target: str, value: str) -> str:
        """Add 'as Int' cast when assigning Float-returning function to Int variable."""
        tgt_low = target.lower().split('.')[-1]
        vtype = self._var_types.get(tgt_low, '')
        if not vtype:
            vtype = self._property_refs.get(target, self._property_refs.get(tgt_low, ''))
        # Cross-script type resolution: Owner.Var → look up var type on remote script
        if not vtype and '.' in target and self.xref:
            parts = target.split('.', 1)
            owner_type = self._property_refs.get(parts[0], self._property_refs.get(parts[0].lower(), ''))
            if owner_type and owner_type.startswith('TES4_'):
                remote_script = owner_type[5:].lower()
                remote_vars = self.xref.script_all_vars.get(remote_script, {})
                vtype = remote_vars.get(parts[1].lower(), '')
        if vtype != 'Int':
            return value
        if self._FLOAT_RETURNING_FUNCS.search(value):
            # Wrap in parens if expression contains arithmetic to prevent binding issues
            if re.search(r'[+\-*/]', value):
                return f'({value}) as Int'
            return f'{value} as Int'
        # Also detect float literals in arithmetic (e.g. X * 0.8, -50 * 0.5)
        if re.search(r'\d+\.\d+', value):
            return f'({value}) as Int'
        # Detect Float variables in arithmetic expressions (e.g. totalTime - timer / 60)
        if re.search(r'[+\-*/]', value):
            # Check if any identifier in the expression is a Float variable
            for ident in re.findall(r'\b([a-zA-Z_]\w*)\b', value):
                id_type = self._var_types.get(ident.lower(), '')
                if not id_type:
                    id_type = self._property_refs.get(ident, self._property_refs.get(ident.lower(), ''))
                if id_type == 'Float':
                    return f'({value}) as Int'
        # Bool→Int coercion: functions like IsDetectedBy return Bool, TES4 assigns to Int
        if re.search(r'\.(?:IsDetectedBy|HasLOS|CanSee|IsInDialogueWithPlayer|'
                     r'IsRidingMount|IsInCombat|IsAnimPlaying|GetDetected)\s*\(', value, re.IGNORECASE):
            return f'{value} as Int'
        return value

    # ObjectReference event parameter names that may need Actor cast
    _OBJREF_PARAMS = {'akactionref', 'aknewcontainer', 'akoldcontainer', 'akcastref',
                      'akactionref', 'akaggressor', 'akcaster'}

    # Functions that return ObjectReference in Papyrus
    _OBJREF_RETURNING = re.compile(
        r'(?:GetLinkedRef|PlaceAtMe|GetParentRef|PlaceActorAtMe|GetEditorLocation|'
        r'GetItemInSlot|GetCombatTarget)\s*\(', re.IGNORECASE)

    def _coerce_ref_to_actor(self, target: str, value: str) -> str:
        """Add 'as Actor' cast when assigning ObjectReference to Actor variable."""
        val_stripped = value.strip()
        val_low = val_stripped.lower()
        # Check if value is an ObjectReference event param, an ObjRef-returning function,
        # or the bare 'akActionRef' identifier
        is_objref_value = (
            val_low in self._OBJREF_PARAMS
            or self._OBJREF_RETURNING.search(val_stripped)
            or val_low == 'akactionref'
        )
        # Check if value is a known ObjectReference variable/property
        if not is_objref_value and '.' not in val_stripped:
            val_type = self._var_types.get(val_low, '')
            if not val_type:
                val_type = self._property_refs.get(val_stripped, self._property_refs.get(val_low, ''))
            if val_type == 'ObjectReference':
                is_objref_value = True
        # Also check cross-script property access returning ObjectReference
        if not is_objref_value and '.' in val_stripped:
            is_objref_value = self._is_ref_typed_access(val_stripped)
            # Even if _is_ref_typed_access returns False (e.g. ref_as_int),
            # cross-script dot access to a ref variable still resolves as ObjectReference
            if not is_objref_value:
                parts = val_stripped.split('.', 1)
                ref_part = parts[0].strip()
                if self.xref.is_quest_ref(ref_part) or ref_part in self._property_refs:
                    is_objref_value = True
        if not is_objref_value:
            return value
        tgt_low = target.lower().split('.')[-1]
        vtype = self._var_types.get(tgt_low, '')
        if vtype in ('Actor', 'ActorBase') or vtype.startswith('TES4_'):
            return f'{value} as Actor'
        # Check property refs too
        ptype = self._property_refs.get(target, self._property_refs.get(tgt_low, ''))
        if ptype in ('Actor', 'ActorBase') or (ptype and ptype.startswith('TES4_')):
            return f'{value} as Actor'
        # Cross-script target: resolve remote property type
        if '.' in target and self.xref:
            parts = target.split('.', 1)
            owner_type = self._property_refs.get(parts[0], self._property_refs.get(parts[0].lower(), ''))
            if owner_type and owner_type.startswith('TES4_'):
                remote_script = owner_type[5:].lower()
                remote_vars = self.xref.script_all_vars.get(remote_script, {})
                remote_type = remote_vars.get(parts[1].lower(), '')
                # Remote ref vars may be upgraded to Actor by post-processing
                # on the target script. Add cast preemptively for safety.
                if remote_type == 'ObjectReference':
                    return f'{value} as Actor'
        return value

        # Direct assignment: X.Y = Z or X = Z (OBSE-style, no 'set' prefix)
        assign_m = re.match(r'^(\S+)\s*=\s*(.*)', stripped)
        if assign_m:
            target = self._convert_ref(assign_m.group(1), extends)
            value = self._convert_expression(assign_m.group(2), extends)
            # akSpeakerRef is ObjectReference; cross-script fields expecting Actor need a cast
            if extends == 'TopicInfo' and value == 'akSpeakerRef':
                value = '(akSpeakerRef as Actor)'
            return f'{target} = {value}'

        return self._convert_function_call(stripped, extends)

    @staticmethod
    def _split_logical(expr: str, op: str) -> list[str] | None:
        """Split *expr* on a logical operator (``||`` or ``&&``) only at
        top-level — i.e. not inside parentheses.  Returns ``None`` if the
        operator does not appear at top level.
        """
        parts: list[str] = []
        depth = 0
        start = 0
        i = 0
        while i < len(expr):
            c = expr[i]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            elif depth == 0 and expr[i:i+len(op)] == op:
                parts.append(expr[start:i])
                start = i + len(op)
                i += len(op)
                continue
            i += 1
        if len(parts) == 0:
            return None
        parts.append(expr[start:])
        return parts

    def _convert_expression(self, expr: str, extends: str) -> str:
        """Convert an Oblivion expression to Papyrus."""
        expr = expr.strip()
        if not expr:
            return expr

        # Quoted EditorID → property ref (TES4 allows quoting form names)
        if len(expr) > 2 and expr[0] == '"' and expr[-1] == '"':
            inner_name = expr[1:-1]
            fid = self.xref.edid_to_formid.get(inner_name.lower(), '')
            if fid:
                rtype = self.xref.record_type.get(fid, '')
                ptype = _record_type_to_papyrus(rtype)
                script_type = self.xref.get_record_script_type(inner_name)
                if script_type:
                    ptype = script_type
                safe = _safe_property_name(inner_name)
                self._property_refs[safe] = ptype
                if ptype == 'GlobalVariable':
                    return f'{safe}.GetValue() as Int'
                return safe

        # Strip balanced outer parens and recurse — TES4 conditions always
        # wrap in parens e.g. "( GetStage Quest >= 10 )" which blocks regex
        if expr.startswith('(') and expr.endswith(')'):
            depth = 0
            balanced = True
            for i, c in enumerate(expr):
                if c == '(': depth += 1
                elif c == ')': depth -= 1
                if depth == 0 and i < len(expr) - 1:
                    balanced = False
                    break
            if balanced:
                inner = self._convert_expression(expr[1:-1].strip(), extends)
                # If inner contains a ;TODO comment, close parens before the comment
                if ';TODO:' in inner or ';TODO ' in inner:
                    semi_idx = inner.index(';TODO')
                    code_part = inner[:semi_idx].rstrip()
                    comment_part = inner[semi_idx:]
                    if not code_part:
                        # Entirely TODO'd — pass through as bare TODO
                        return comment_part
                    return f'({code_part}){comment_part}'
                return f'({inner})'

        expr = expr.replace('<>', '!=')

        # Fix spaces around dots in method chains (e.g. "Player. GetItemCount" → "Player.GetItemCount")
        expr = re.sub(r'(\w)\.\s+(\w)', r'\1.\2', expr)

        # Split on logical operators first, convert each part independently
        # Handle || and && — paren-aware to avoid splitting inside subexprs
        or_parts = self._split_logical(expr, '||')
        if or_parts is not None:
            converted = [self._convert_expression(p.strip(), extends) for p in or_parts]
            if any(';TODO:' in c for c in converted):
                return f';TODO: {expr}  ;partially unconvertible'
            return ' || '.join(converted)
        and_parts = self._split_logical(expr, '&&')
        if and_parts is not None:
            converted = [self._convert_expression(p.strip(), extends) for p in and_parts]
            if any(';TODO:' in c for c in converted):
                return f';TODO: {expr}  ;partially unconvertible'
            return ' && '.join(converted)

        # TES4 boolean functions compared to 1/0 (e.g., "IsActionRef player == 1", "ref.GetIsRace Argonian == 1")
        _BOOL_FUNC_NAMES = (
            r'IsActionRef|GetDead|IsDead|IsInCombat|IsSneaking|IsWeaponOut|IsSwimming|'
            r'IsGhost|GetLocked|IsEnabled|HasSpell|GetInFaction|GetQuestRunning|GetStageDone|'
            r'GetDetected|IsActorDetected|GetIsID|GetIsRace|GetPCIsRace|GetIsRef|'
            r'GetInCell|GetInSameCell|GetIsSex|IsInFaction|IsEssential|IsInInterior|'
            r'GetIsCurrentPackage|IsOwner|GetTalkedToPCParam|GetTalkedToPC|'
            r'IsActorUsingATorch|IsRidingHorse')
        bool_comp_m = re.match(
            r'^(?:(\w+)\.)?' + r'(' + _BOOL_FUNC_NAMES + r')(?:\s+(.+?))?\s*==\s*([01])\s*$',
            expr, re.IGNORECASE)
        if bool_comp_m:
            ref_part = bool_comp_m.group(1)
            fname = bool_comp_m.group(2)
            args_part = bool_comp_m.group(3) or ''
            bool_val = bool_comp_m.group(4)
            converted_call = self._emit_function(ref_part, fname, args_part, extends)
            # If function converted to TODO, propagate it
            if converted_call.lstrip().startswith(';TODO'):
                return converted_call
            if bool_val == '0':
                return f'!({converted_call})'
            return converted_call

        # Pre-split: strip trailing TES4 truth test "== 1" / "== 0"
        # TES4 comparisons return 0/1, so "a == b == 1" means "(a == b) is true"
        trail_m = re.match(r'^(.+\S)\s*==\s*([01])\s*$', expr)
        if trail_m and re.search(r'\s[=!<>]{1,2}\s', trail_m.group(1)):
            inner = trail_m.group(1)
            converted = self._convert_expression(inner, extends)
            if trail_m.group(2) == '1':
                return converted
            return f'!({converted})'

        # Handle comparison operators: split into LHS op RHS (depth-aware)
        # Scan left-to-right for comparison operators at paren depth 0
        comp_m = None
        _depth = 0
        for _ci in range(len(expr)):
            ch = expr[_ci]
            if ch == '(':
                _depth += 1
            elif ch == ')':
                _depth -= 1
            elif _depth == 0:
                # Check for 2-char operators first (==, !=, >=, <=)
                two = expr[_ci:_ci+2]
                if two in ('==', '!=', '>=', '<='):
                    comp_m = (expr[:_ci].strip(), two, expr[_ci+2:].strip())
                    break
                # Single-char: > or < (but not part of >= or <=)
                if ch in ('>', '<') and (_ci + 1 >= len(expr) or expr[_ci+1] != '='):
                    comp_m = (expr[:_ci].strip(), ch, expr[_ci+1:].strip())
                    break
        if comp_m:
            lhs = self._convert_expression(comp_m[0], extends)
            op = comp_m[1]
            rhs = self._convert_expression(comp_m[2], extends)
            # If LHS is entirely a TODO comment, propagate it
            if lhs.lstrip().startswith(';TODO'):
                return lhs
            # If LHS contains an inline TODO comment, extract code part for comparison
            if ';TODO' in lhs:
                semi_idx = lhs.index(';TODO')
                code_part = lhs[:semi_idx].rstrip()
                comment_part = lhs[semi_idx:]
                return f'{code_part} {op} {rhs}  {comment_part}'
            # Fix ref == 0 / ref != 0 → ref == None / ref != None
            if rhs.strip() == '0' and op in ('==', '!='):
                lhs_var = lhs.strip().lower().split('.')[-1]
                lhs_raw = lhs.strip().split('.')[-1]
                lhs_type = self._var_types.get(lhs_var, '') or self._property_refs.get(lhs_raw, self._property_refs.get(lhs_var, ''))
                is_ref = lhs_type in ('ObjectReference', 'Actor', 'ActorBase') or lhs_type.startswith('TES4_')
                # Self is always a ref type
                if not is_ref and lhs.strip() == 'Self':
                    is_ref = True
                # Ref-returning function calls: GetContainer(), GetLinkedRef(), etc.
                if not is_ref and re.search(r'\.Get(?:Container|LinkedRef|ParentRef)\(\)', lhs.strip(), re.IGNORECASE):
                    is_ref = True
                # Also check cross-script ref type (e.g. MQ00.nearOblivionGate == 0)
                if not is_ref and '.' in lhs.strip():
                    is_ref = self._is_ref_typed_access(lhs.strip())
                if is_ref:
                    rhs = 'None'
            # Reversed: 0 == ref / 0 != ref → None == ref / None != ref
            if lhs.strip() == '0' and op in ('==', '!='):
                rhs_var = rhs.strip().lower().split('.')[-1]
                rhs_raw = rhs.strip().split('.')[-1]
                rhs_type = self._var_types.get(rhs_var, '') or self._property_refs.get(rhs_raw, self._property_refs.get(rhs_var, ''))
                is_ref = rhs_type in ('ObjectReference', 'Actor', 'ActorBase') or rhs_type.startswith('TES4_')
                if not is_ref and rhs.strip() == 'Self':
                    is_ref = True
                if not is_ref and '.' in rhs.strip():
                    is_ref = self._is_ref_typed_access(rhs.strip())
                if is_ref:
                    lhs = 'None'
            # ObjectReference variable in numeric comparison (<, <=, >, >=) with a number:
            # TES4 undeclared variables default to 0; name collision with a form reference
            if op in ('<', '<=', '>', '>=') and re.match(r'^-?\d+(\.\d+)?$', rhs.strip()):
                lhs_var = lhs.strip().lower().split('.')[-1]
                lhs_raw = lhs.strip().split('.')[-1]
                lhs_type = self._var_types.get(lhs_var, '') or self._property_refs.get(lhs_raw, self._property_refs.get(lhs_var, ''))
                if lhs_type == 'ObjectReference':
                    lhs = '0  ;undeclared TES4 var'
            # ref > 0 / ref >= 1 → ref != None (null check pattern for ref types)
            if rhs.strip() in ('0', '1') and op in ('>', '>='):
                lhs_var = lhs.strip().lower().split('.')[-1]
                lhs_raw = lhs.strip().split('.')[-1]
                lhs_type = self._var_types.get(lhs_var, '') or self._property_refs.get(lhs_raw, self._property_refs.get(lhs_var, ''))
                is_ref = lhs_type in ('ObjectReference', 'Actor', 'ActorBase') or lhs_type.startswith('TES4_')
                if not is_ref and '.' in lhs.strip():
                    is_ref = self._is_ref_typed_access(lhs.strip())
                if not is_ref and '.' in lhs.strip():
                    parts = lhs.strip().split('.', 1)
                    if self.xref and self.xref.is_remote_ref_var(parts[0], parts[1]):
                        is_ref = True
                if is_ref:
                    return f'{lhs} != None'
            # TES4 boolean comparison: (comparison_expr) == 1 → comparison_expr
            # In TES4, comparisons return 0/1 so "== 1" checks truth, "== 0" checks false
            if op in ('==', '!=') and rhs.strip() in ('0', '1'):
                # Check if LHS already contains a comparison (implying boolean result)  
                inner = lhs.strip()
                if inner.startswith('(') and inner.endswith(')'):
                    inner_content = inner[1:-1]
                    if re.search(r'\s(==|!=|>=|<=|>|<)\s', inner_content):
                        if (op == '==' and rhs.strip() == '1') or (op == '!=' and rhs.strip() == '0'):
                            return lhs  # Already a boolean, == 1 is redundant
                        if (op == '==' and rhs.strip() == '0') or (op == '!=' and rhs.strip() == '1'):
                            return f'!{lhs}'  # Negate
            # Bool function compared to 0/1: IsDisabled() == 0 → !IsDisabled()
            if op in ('==', '!=') and rhs.strip() in ('0', '1'):
                inner = lhs.strip()
                # Match ref.Func() or Func() patterns
                bool_call_m = re.match(
                    r'^(?:.*\.)?('
                    r'Is(?:Disabled|Enabled|Dead|InCombat|Sneaking|WeaponOut|Swimming|Ghost|'
                    r'InInterior|Essential|Guard|ActionRef|ChildOf|InDialogueWithPlayer|'
                    r'Running|InFaction|Arrested|BleedingOut|UnconscIous|Commanded|'
                    r'PlayerTeammate|Hostile|Sprinting|OnMount|Alerted|EquipPed|'
                    r'Mounted|Trespassing|AVRecoveryDisabled|FurnitureInUse|FlightBlocked)|'
                    r'Get(?:Dead|Disabled|Locked|Ghost|IsAlerted|InCombat|NoBleedoutRecovery|'
                    r'IsPlayableRace|CurrentWeatherPercent|IsCurrentPackage)|'
                    r'Has(?:Spell|MagicEffect|Perk|EffectKeyword|KeyWord|Node|LOSToRef|RefType)|'
                    r'(?:IsInterior|IsInInterior|WornHasKeyword|PathToReference)'
                    r')\s*\(', inner, re.IGNORECASE)
                if bool_call_m:
                    if (op == '==' and rhs.strip() == '1') or (op == '!=' and rhs.strip() == '0'):
                        return lhs  # Already bool, == 1 is redundant
                    if (op == '==' and rhs.strip() == '0') or (op == '!=' and rhs.strip() == '1'):
                        return f'!({lhs})'  # Negate
            return f'{lhs} {op} {rhs}'

        # Handle arithmetic operators at top level (+, -, *, /)
        # Scan right-to-left for + and - (lowest precedence), then * and /
        # This lets recursive conversion handle each operand properly
        for ops in (('+', '-'), ('*', '/', '%')):
            depth = 0
            best_i = -1
            for i in range(len(expr) - 1, 0, -1):  # right-to-left, skip pos 0 (unary)
                c = expr[i]
                if c == ')': depth += 1
                elif c == '(': depth -= 1
                elif depth == 0 and c in ops:
                    # Don't split on +/- that are part of a number (.2, 1e+5)
                    # or are unary (preceded by operator or open paren)
                    # Look back past whitespace to find the significant prev char
                    sig_prev = ' '
                    for k in range(i - 1, -1, -1):
                        if expr[k] not in ' \t':
                            sig_prev = expr[k]
                            break
                    if sig_prev in '(,=<>!+-*/%':
                        continue
                    best_i = i
                    break
            if best_i > 0:
                lhs = self._convert_expression(expr[:best_i].strip(), extends)
                op = expr[best_i]
                rhs = self._convert_expression(expr[best_i+1:].strip(), extends)
                return f'{lhs} {op} {rhs}'

        # Handle function calls in expressions: "funcname arg1 arg2"
        # Route through _emit_function for special-case handling
        func_in_expr = re.match(r'^(\w+)\s+(.+)$', expr)
        if func_in_expr:
            fname = func_in_expr.group(1).lower()
            if fname in FUNCTION_MAP or fname in ('getstage', 'getstagedone', 'setstage',
                    'startquest', 'stopquest', 'getquestrunning', 'getrandompercent',
                    'completequest', 'isquestcompleted',
                    'getpos', 'getangle', 'setpos', 'setangle', 'getstartingangle', 'getself',
                    'getactionref', 'isactionref', 'message', 'messagebox',
                    'getisid', 'getisrace', 'getpcisrace', 'getisref',
                    'getincell', 'getinsamecell', 'getissex', 'getcrimeknown',
                    'sme', 'pme', 'setdisplayname', 'placeatme',
                    'createfullactorcopy', 'wakeuppc', 'isexpelled',
                    'sayto', 'say', 'saycustom', 'getpcissex',
                    'getcontainer', 'getbookread', 'bookread', 'showclassmenu',
                    'showbirthsignmenu', 'showracemenu', 'setinchargen',
                    'setplayerinseworld', 'forcecloseobliviongate',
                    'closecurrentobliviongate', 'isinfaction'):
                return self._emit_function(None, func_in_expr.group(1),
                                           func_in_expr.group(2).strip(), extends)

        # Handle ref.Func in expressions (only if no parens yet — avoid re-matching)
        # Require ref to start with a letter (not digit) to avoid matching floats like 0.5
        ref_func = re.match(r'^([a-zA-Z_]\w*)\.(\w+)\s*((?:[^(].*)?)', expr)
        if ref_func and '(' not in ref_func.group(2):
            args_rest = ref_func.group(3).strip()
            ref_name = ref_func.group(1)
            prop_name = ref_func.group(2)
            prop_low = prop_name.lower()
            # Sanitize property name to avoid Papyrus reserved word collisions
            safe_prop = _safe_property_name(prop_name)
            # If "args" starts with arithmetic op, it's property access not function call
            # e.g. Quest.Var + 1 -> Quest.Var + 1, not Quest.Var(+, 1)
            if args_rest and args_rest[0] in '+-*/%':
                ref = self._convert_ref(ref_name, extends)
                rest = self._convert_expression(args_rest, extends)
                return f'{ref}.{safe_prop} {rest}'
            # If "args" starts with comparison op, it's also a property comparison
            # e.g. Quest.Var > 0, Quest.Var == 1
            # BUT: if prop is a known function, route through _emit_function instead
            if args_rest and re.match(r'^(==|!=|>=|<=|>|<)\s*', args_rest):
                if prop_low in FUNCTION_MAP or prop_low in _BARE_BOOL_FUNCTIONS or prop_low in (
                        'isininterior', 'isanimplaying', 'getparentcell',
                        'getdead', 'isdead', 'getdisabled', 'isdisabled',
                        'isinfaction', 'isessential', 'getisrace',
                        'getisid', 'getissex', 'getincell', 'getinsamecell',
                        'isactionref', 'getactionref', 'getisplayablerace',
                        'isactorusingatorch', 'getdetected', 'isdetectedby',
                        'getismurderer', 'isguard', 'getnosneakwaterpenalty',
                        'getstartingangle', 'getstartingpos'):
                    # It's a function call followed by comparison — split and handle
                    comp = re.match(r'^(==|!=|>=|<=|>|<)\s*(.*)', args_rest)
                    func_result = self._emit_function(ref_name, prop_name, '', extends)
                    if comp:
                        rhs = self._convert_expression(comp.group(2).strip(), extends)
                        return f'{func_result} {comp.group(1)} {rhs}'
                    return func_result
                ref = self._convert_ref(ref_name, extends)
                rest = self._convert_expression(args_rest, extends)
                return f'{ref}.{safe_prop} {rest}'
            # Cross-script variable access: if ref's script declares this variable, always property
            if self._ref_has_script_var(ref_name, prop_name):
                ref = self._convert_ref(ref_name, extends)
                if args_rest:
                    rest = self._convert_expression(args_rest, extends)
                    return f'{ref}.{safe_prop} {rest}'
                return f'{ref}.{safe_prop}'
            # If ref is a known quest and prop is NOT a known function, treat as property access
            if self.xref.is_quest_ref(ref_name) and prop_low not in FUNCTION_MAP and prop_low not in (
                    'getstage', 'setstage', 'getstagedone', 'start', 'stop', 'isrunning',
                    'iscompleted', 'completequest', 'setstage', 'getstage'):
                ref = self._convert_ref(ref_name, extends)
                if args_rest:
                    rest = self._convert_expression(args_rest, extends)
                    return f'{ref}.{safe_prop} {rest}'
                return f'{ref}.{safe_prop}'
            # No args and not a known function — treat as property access
            # e.g. NpcRef.someVar (cross-script variable)
            if not args_rest and prop_low not in FUNCTION_MAP and prop_low not in _BARE_BOOL_FUNCTIONS and prop_low not in (
                    'getstage', 'setstage', 'getstagedone', 'start', 'stop', 'isrunning',
                    'iscompleted', 'completequest', 'evaluatepackage', 'enable', 'disable',
                    'delete', 'activate', 'reset', 'kill', 'resurrect', 'moveto',
                    'getparentcell', 'getself', 'getactionref', 'getlinkedref',
                    'getparentref', 'getbaseobject', 'getactorbase',
                    'isactorusingatorch', 'isridinghorse', 'createfullactorcopy'):
                ref = self._convert_ref(ref_name, extends)
                return f'{ref}.{safe_prop}'
            return self._emit_function(ref_name, prop_name,
                                       args_rest, extends)

        # Bare function names used as values (no ref, no args)
        # e.g. "getParentRef" -> "GetLinkedRef()", "GetActionRef" -> "akActionRef"
        if re.match(r'^[a-zA-Z_]\w*$', expr):
            bare_low = expr.lower()
            # Local variables ALWAYS take priority over function name matching
            if bare_low in self._local_vars:
                safe = self._var_renames.get(bare_low, expr)
                return safe
            # Special bare identifiers
            if bare_low in ('getactionref', 'isactionref'):
                return self._get_action_ref_param()
            if bare_low == 'isanimplaying':
                self._line_comments.append(';IsAnimPlaying has no Papyrus equivalent')
                return 'False'
            if bare_low == 'isxbox':
                return 'False'
            if bare_low in ('getdayofweek', 'getdayoftheweek'):
                self._property_refs['GameDaysPassed'] = 'GlobalVariable'
                return '(GameDaysPassed.GetValue() as Int) % 7'
            if bare_low in ('getrandompercent', 'getrandpercent'):
                return 'Utility.RandomInt(0, 99)'
            if bare_low in ('getcurrenttime', 'gamehour'):
                self._property_refs['GameHour'] = 'GlobalVariable'
                return 'GameHour.GetValue() as Int'
            if bare_low == 'getpcfame':
                self._property_refs['TES4Fame'] = 'GlobalVariable'
                return 'TES4Fame.GetValueInt()'
            if bare_low in ('getpcinfamy', 'getinfame'):
                self._property_refs['TES4Infamy'] = 'GlobalVariable'
                return 'TES4Infamy.GetValueInt()'
            if bare_low in ('isplayerinprison', 'getplayerinjail', 'isplayerinjail'):
                self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
                return 'TES4CyrodiilCrimeFaction.IsPlayerExpelled()'
            if bare_low in ('getpcissleeping', 'ispcsleeping', 'isplayersleeping'):
                return 'Game.GetPlayer().GetSleepState()'
            if bare_low == 'isininterior':
                if extends == 'ActiveMagicEffect':
                    return 'GetTargetActor().GetParentCell().IsInterior()'
                return 'Self.GetParentCell().IsInterior()'
            if bare_low == 'getdestroyed':
                return 'IsDisabled()'
            # Handle bare function references that need special handling
            if bare_low == 'getbuttonpressed':
                return '-1'
            if bare_low in ('getcrimegold',):
                self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
                return 'TES4CyrodiilCrimeFaction.GetCrimeGold()'
            if bare_low in ('getdisposition',):
                return '50'
            if bare_low == 'getdetectionlevel':
                return '0'
            if bare_low in ('getisalerted', 'israining', 'menumode',
                            'istimepassing', 'getplayerinseworld',
                            'getcurrentaiprocedure', 'getcurrentaipackage',
                            'getiscurrentpackage', 'isidleplaying',
                            'getcontainer', 'getbookread', 'gettalkedtopc',
                            'getcrimeknown', 'getstartingpos',
                            'getplayercontrolsdisabled', 'getisplayerbirthsign',
                            'hasbeenpickedup', 'getweatherpercent',
                            'getgameloaded', 'hasvariable', 'getownership',
                            'isonguard', 'isindangerouswater',
                            'getarmorrating', 'isspelltarget', 'isswimming',
                            'isactor', 'getspellcount',
                            'getrestrained', 'ispcamurderer', 'getpcismurderer',
                            'getpcfactionattack', 'getpcfactionsteal',
                            'getpcfactionmurder'):
                return '0'
            if bare_low == 'reset':
                if extends == 'ActiveMagicEffect':
                    return 'GetTargetActor().Reset()'
                if extends == 'TopicInfo':
                    return 'akSpeakerRef.Reset()'
                return 'Self.Reset()'
            # Only check FUNCTION_MAP if NOT a declared local variable
            if bare_low not in self._local_vars:
                entry = FUNCTION_MAP.get(bare_low)
                if entry and entry[0] is not None:
                    return self._emit_function(None, expr, '', extends)
            # Check if it's a known EditorID -> property ref
            fid = self.xref.edid_to_formid.get(bare_low, '')
            if fid:
                rtype = self.xref.record_type.get(fid, '')
                ptype = _record_type_to_papyrus(rtype)
                # Prefer attached script type for cross-script property access
                script_type = self.xref.get_record_script_type(expr)
                if script_type:
                    ptype = script_type
                safe = _safe_property_name(expr)
                self._property_refs[safe] = ptype
                if ptype == 'GlobalVariable':
                    return f'{safe}.GetValue() as Int'
                return safe

        # Terminal substitutions (applied last, after all function matching)
        expr = re.sub(r'\bplayer\b', 'Game.GetPlayer()', expr, flags=re.IGNORECASE)
        # In AME/TopicInfo scripts, Self/GetSelf refers to the target actor
        if extends == 'ActiveMagicEffect':
            expr = re.sub(r'\bgetSelf\b', 'GetTargetActor()', expr, flags=re.IGNORECASE)
            expr = re.sub(r'\bthis\b', 'GetTargetActor()', expr, flags=re.IGNORECASE)
            # Replace bare Self (not followed by '.') with GetTargetActor() for comparisons
            expr = re.sub(r'\bSelf\b(?!\.)', 'GetTargetActor()', expr)
        elif extends == 'TopicInfo':
            expr = re.sub(r'\bgetSelf\b', 'akSpeakerRef', expr, flags=re.IGNORECASE)
            expr = re.sub(r'\bthis\b', 'akSpeakerRef', expr, flags=re.IGNORECASE)
            # Replace bare Self (not followed by '.') with akSpeakerRef for comparisons
            expr = re.sub(r'\bSelf\b(?!\.)', 'akSpeakerRef', expr)
        else:
            expr = re.sub(r'\bgetSelf\b', 'Self', expr, flags=re.IGNORECASE)
            expr = re.sub(r'\bthis\b', 'Self', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bGetSecondsPassed\b', '0.5', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bScriptEffectElapsedSeconds\b', '0.5', expr, flags=re.IGNORECASE)

        # Fix bare decimals: .5 -> 0.5 (Papyrus requires leading zero)
        expr = re.sub(r'(?<![.\w])\.(\d)', r'0.\1', expr)

        # Known TES4 globals -> GlobalVariable.GetValue()
        if expr.lower() in KNOWN_GLOBALS:
            canonical = _canonical_global(expr)
            self._property_refs[canonical] = 'GlobalVariable'
            return f'{canonical}.GetValue()'

        # Actor value name substitution
        for ob_av, sk_av in ACTOR_VALUE_MAP.items():
            expr = re.sub(r'\b' + ob_av + r'\b', sk_av, expr, flags=re.IGNORECASE)

        # Rename reserved-word variables (e.g. next -> myNext)
        for orig_low, safe in self._var_renames.items():
            expr = re.sub(r'\b' + re.escape(orig_low) + r'\b', safe, expr, flags=re.IGNORECASE)

        return expr

    def _convert_ref(self, name: str, extends: str) -> str:
        """Convert an Oblivion reference name to Papyrus."""
        low = name.lower()
        if low in ('player', 'playerref'):
            return 'Game.GetPlayer()'
        if low in ('getself', 'myself', 'self'):
            if extends == 'ActiveMagicEffect':
                return 'GetTargetActor()'
            if extends == 'TopicInfo':
                return 'akSpeakerRef'
            return 'Self'

        # Known TES4 globals -> property
        if low in KNOWN_GLOBALS:
            canonical = _canonical_global(name)
            self._property_refs[canonical] = 'GlobalVariable'
            return canonical

        if '.' in name:
            parts = name.split('.', 1)
            ref_part = self._convert_ref(parts[0], extends)
            return f'{ref_part}.{_safe_property_name(parts[1])}'

        if self.xref.is_quest_ref(name):
            # Use the canonical EditorID (original case from export) as the key
            # so this matches what _add_scro_ref stores (both use formid_to_edid).
            canon_fid = self.xref.edid_to_formid.get(low, '')
            canon_edid = self.xref.formid_to_edid.get(canon_fid, name) if canon_fid else name
            self._property_refs[canon_edid] = self.xref.get_quest_script_type(name)
            return canon_edid

        # Local variables take precedence over game form EditorIDs (name collision)
        if low in self._local_vars or low in self._var_types:
            return _safe_property_name(name)

        # Check if this is any known EditorID from the export
        fid = self.xref.edid_to_formid.get(low, '')
        if fid:
            # Use canonical EditorID (original case) as key to match _add_scro_ref
            canon_edid = self.xref.formid_to_edid.get(fid, name)
            rtype = self.xref.record_type.get(fid, '')
            ptype = _record_type_to_papyrus(rtype)
            # Prefer attached script type over generic Actor/ObjectReference
            # so cross-script property access works (e.g., NPCRef.rent)
            script_type = self.xref.get_record_script_type(name)
            if script_type:
                ptype = script_type
            safe = _safe_property_name(canon_edid)
            # Don't downgrade a more specific type (e.g., Actor from
            # _resolve_self_ref) back to a generic one (ObjectReference).
            cur = self._property_refs.get(safe, '')
            _generic = ('', 'ObjectReference')
            if not cur or ptype not in _generic or cur in _generic:
                self._property_refs[safe] = ptype
            return safe

        return _safe_property_name(name)

    def _convert_args(self, args_str: str, func_name: str, extends: str) -> str:
        """Convert Oblivion function arguments to Papyrus."""
        if not args_str:
            return ''

        # Actor value functions: first arg is AV name -> quoted string
        av_funcs = {'getactorvalue', 'setactorvalue', 'modactorvalue', 'forceactorvalue',
                     'getav', 'setav', 'modav', 'forceav', 'getbaseactorvalue', 'getbaseav',
                     'modpcskill', 'advancepcskill'}
        if func_name in av_funcs:
            parts = args_str.split(None, 1)
            av_name = parts[0].rstrip(',').strip('"\'')
            sk_av = ACTOR_VALUE_MAP.get(av_name.lower(), av_name)
            rest = ''
            if len(parts) > 1:
                rest_str = parts[1].lstrip(', ')
                if rest_str:
                    rest = f', {self._convert_expression(rest_str, extends)}'
            return f'"{sk_av}"{rest}'

        # Default: split on commas first, then whitespace within each part
        # Oblivion scripts use both "func arg1 arg2" and "func arg1, arg2"
        if ',' in args_str:
            parts = [p.strip() for p in args_str.split(',') if p.strip()]
        else:
            parts = args_str.split()
        converted = [self._convert_expression(p, extends) for p in parts]
        return ', '.join(converted)

    def _convert_function_call(self, line: str, extends: str) -> str:
        """Convert an Oblivion function call line to Papyrus."""
        stripped = line.strip()
        # Fix space after dot in ref. function patterns (TES4 typo)
        stripped = re.sub(r'(\w)\.\s+(\w)', r'\1.\2', stripped)

        # ref.function pattern
        ref_m = re.match(r'^(\w+)\.(\w+)\s*(.*)', stripped, re.IGNORECASE)
        if ref_m:
            return self._emit_function(ref_m.group(1), ref_m.group(2), ref_m.group(3).strip(), extends)

        # Standalone function
        func_m = re.match(r'^(\w+)\s*(.*)', stripped, re.IGNORECASE)
        if func_m:
            return self._emit_function(None, func_m.group(1), func_m.group(2).strip(), extends)

        return f'{stripped}  ;TODO: Could not parse'

    def _get_action_ref_param(self) -> str:
        """Return the correct event parameter for GetActionRef/IsActionRef.
        
        TES4 GetActionRef is available in every block. Papyrus scopes event params.
        Map to the appropriate parameter based on the current event being converted.
        """
        ev = self._current_event.lower()
        if 'onactivate' in ev or 'ontrigger' in ev:
            return 'akActionRef'
        if 'onequipped' in ev or 'onunequipped' in ev:
            return 'akActor'
        if 'onhit' in ev:
            return 'akAggressor'
        if 'ondeath' in ev:
            return 'akKiller'
        if 'oncontainerchanged' in ev:
            return 'akNewContainer'
        if 'oncombatstate' in ev:
            return 'akTarget'
        # OnUpdate/OnInit/other events have no action ref - use None as fallback
        if 'onupdate' in ev or 'oninit' in ev:
            return 'None'
        # Fallback: akActionRef (may be undefined, but most common case)
        return 'akActionRef'

    def _resolve_self_ref(self, ref_name, extends, actor_func=False):
        """Resolve the reference for a function call.
        
        For ActiveMagicEffect scripts, bare (no ref) or Self-prefixed actor/objref
        functions need GetTargetActor() instead of Self.
        For TopicInfo scripts, bare actor functions need akSpeakerRef.
        """
        if ref_name:
            ref_low = ref_name.lower()
            # Self in ActiveMagicEffect/TopicInfo should redirect actor functions
            if actor_func and ref_low in ('self', 'myself', 'getself'):
                if extends == 'ActiveMagicEffect':
                    return 'GetTargetActor()'
                if extends == 'TopicInfo':
                    return '(akSpeakerRef as Actor)'
            # Upgrade property type to Actor when used with actor-only functions
            canon = self._convert_ref(ref_name, extends)
            if actor_func:
                # akSpeakerRef is a fixed ObjectReference parameter; cast it rather than upgrading
                if canon == 'akSpeakerRef':
                    return '(akSpeakerRef as Actor)'
                cur = self._property_refs.get(canon, '')
                if cur in ('', 'ObjectReference'):
                    self._property_refs[canon] = 'Actor'
            return canon
        if actor_func:
            if extends == 'ActiveMagicEffect':
                return 'GetTargetActor()'
            if extends == 'TopicInfo':
                return '(akSpeakerRef as Actor)'
        return 'Self'

    def _emit_function(self, ref_name: Optional[str], func_name: str,
                       args_str: str, extends: str) -> str:
        """Emit a converted function call."""
        fname_low = func_name.lower()

        # --- Special case functions ---

        if fname_low == 'getself':
            if extends == 'ActiveMagicEffect':
                return 'GetTargetActor()'
            if extends == 'TopicInfo':
                return 'akSpeakerRef'
            return 'Self'

        if fname_low == 'getpcissex':
            arg = args_str.strip().lower() if args_str else 'male'
            sex_val = '1' if 'female' in arg else '0'
            return f'Game.GetPlayer().GetActorBase().GetSex() == {sex_val}'

        if fname_low == 'getactionref':
            return self._get_action_ref_param()

        if fname_low == 'isactionref':
            arg = self._convert_expression(args_str, extends) if args_str else ''
            return f'{self._get_action_ref_param()} == {arg}'

        # GetPos/GetAngle/GetStartingAngle: axis param -> GetPositionX/Y/Z or GetAngleX/Y/Z
        if fname_low in ('getpos', 'getangle', 'getstartingangle'):
            axis = args_str.strip().upper() if args_str else 'X'
            if axis not in ('X', 'Y', 'Z'):
                axis = 'X'
            if fname_low == 'getpos':
                papyrus = f'GetPosition{axis}'
            else:
                papyrus = f'GetAngle{axis}'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.{papyrus}()' if ref_name else f'{papyrus}()'

        # SetPos/SetAngle: axis param -> SetPosition(x,y,z) / SetAngle(x,y,z)
        if fname_low in ('setpos', 'setangle'):
            parts = args_str.split(None, 1) if args_str else ['X', '0']
            axis = parts[0].upper() if parts else 'X'
            value = self._convert_expression(parts[1], extends) if len(parts) > 1 else '0'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            if fname_low == 'setpos':
                axes = {'X': (value, f'{ref}.GetPositionY()', f'{ref}.GetPositionZ()'),
                        'Y': (f'{ref}.GetPositionX()', value, f'{ref}.GetPositionZ()'),
                        'Z': (f'{ref}.GetPositionX()', f'{ref}.GetPositionY()', value)}
                x, y, z = axes.get(axis, (value, f'{ref}.GetPositionY()', f'{ref}.GetPositionZ()'))
                return f'{ref}.SetPosition({x}, {y}, {z})'
            else:
                axes = {'X': (value, f'{ref}.GetAngleY()', f'{ref}.GetAngleZ()'),
                        'Y': (f'{ref}.GetAngleX()', value, f'{ref}.GetAngleZ()'),
                        'Z': (f'{ref}.GetAngleX()', f'{ref}.GetAngleY()', value)}
                x, y, z = axes.get(axis, (value, f'{ref}.GetAngleY()', f'{ref}.GetAngleZ()'))
                return f'{ref}.SetAngle({x}, {y}, {z})'

        # GetRandomPercent -> Utility.RandomInt(0, 99)
        if fname_low == 'getrandompercent':
            return 'Utility.RandomInt(0, 99)'

        # SetStage/GetStage/GetStageDone: first arg is quest, second is stage
        if fname_low in ('setstage', 'getstage', 'getstagedone'):
            if args_str and ',' in args_str:
                parts = [p.strip() for p in args_str.split(',')]
            else:
                parts = args_str.split() if args_str else []
            if len(parts) >= 2:
                quest_ref = parts[0].rstrip(',')
                stage = parts[1].rstrip(',')
            elif len(parts) == 1:
                quest_ref = parts[0].rstrip(',')
                stage = '0'
            else:
                quest_ref = 'quest'
                stage = '0'
            quest_type = self.xref.get_quest_script_type(quest_ref) if self.xref.is_quest_ref(quest_ref) else 'Quest'
            self._property_refs[quest_ref] = quest_type
            papyrus = {'setstage': 'SetStage', 'getstage': 'GetStage',
                        'getstagedone': 'GetStageDone'}[fname_low]
            if fname_low in ('getstage', 'getstagedone') and len(parts) < 2:
                return f'{quest_ref}.{papyrus}()'
            if fname_low in ('getstage', 'getstagedone') and stage == '0' and len(parts) < 2:
                return f'{quest_ref}.{papyrus}()'
            return f'{quest_ref}.{papyrus}({stage})'

        # StartQuest/StopQuest/GetQuestRunning/CompleteQuest/IsQuestCompleted: arg is quest
        if fname_low in ('startquest', 'stopquest', 'getquestrunning', 'completequest', 'isquestcompleted'):
            quest_ref = args_str.strip() if args_str else 'quest'
            existing = self._property_refs.get(quest_ref, self._property_refs.get(quest_ref.lower(), ''))
            if not existing:
                # No type known yet — use Quest (base type sufficient for
                # Start/Stop/IsRunning). TES4 SCPT-derived names from xref
                # (e.g. TES4_FGC01Script) would be wrong here because in TES5
                # the quest's VMAD script is TES4_QF_<EditorID>, not the SCPT name.
                self._property_refs[quest_ref] = 'Quest'
            # else: keep existing type — if already TES4_XxxScript (extends Quest),
            # .Start()/.Stop() still work and cross-script var access still works.
            papyrus = {'startquest': 'Start', 'stopquest': 'Stop',
                        'getquestrunning': 'IsRunning',
                        'completequest': 'CompleteQuest',
                        'isquestcompleted': 'IsCompleted'}[fname_low]
            return f'{quest_ref}.{papyrus}()'

        # Message/MessageBox
        if fname_low == 'message':
            return f'Debug.Notification({self._quote_msg(args_str)})'
        if fname_low == 'messagebox':
            return f'Debug.MessageBox({self._quote_msg(args_str)})'

        # --- Compound player.Function ---
        compound = f'{ref_name}.{func_name}'.lower() if ref_name else ''
        if compound in FUNCTION_MAP:
            entry = FUNCTION_MAP[compound]
            papyrus_func, _, note = entry
            if papyrus_func:
                args = self._convert_args(args_str, fname_low, extends) if args_str else ''
                result = f'{papyrus_func}({args})'
                return f'{result}  {note}' if note else result

        # GetPCExpelled / SetPCExpelled: faction arg
        # Skyrim has no Expel/IsExpelled — use faction rank manipulation
        if fname_low in ('getpcexpelled', 'ispcexpelled'):
            faction = self._convert_expression(args_str, extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Faction'
            return f'(Game.GetPlayer().GetFactionRank({faction}) < 0)'
        if fname_low == 'setpcexpelled':
            parts = args_str.split(None, 1) if args_str else []
            faction = self._convert_expression(parts[0], extends) if parts else 'None'
            if parts:
                self._property_refs[parts[0].strip()] = 'Faction'
            val = parts[1].strip() if len(parts) > 1 else '1'
            if val == '0':
                return f'{faction}.SetPlayerExpelled(false)'
            return f'{faction}.SetPlayerExpelled(true)'

        # GotoJail → faction.SendPlayerToJail()
        if fname_low == 'gotojail':
            self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
            return 'TES4CyrodiilCrimeFaction.SendPlayerToJail()'

        # Crime gold functions → TES4CyrodiilCrimeFaction proxy
        if fname_low == 'getcrimegold':
            self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
            return 'TES4CyrodiilCrimeFaction.GetCrimeGold()'
        if fname_low == 'setcrimegold':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else '0'
            self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
            # SetCrimeGold takes Int; TES4 float vars need cast
            arg_type = self._var_types.get(arg.lower(), '') or self._property_refs.get(arg, self._property_refs.get(arg.lower(), ''))
            if arg_type == 'Float':
                arg = f'{arg} as Int'
            return f'TES4CyrodiilCrimeFaction.SetCrimeGold({arg})'
        if fname_low == 'modcrimegold':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else '0'
            self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
            return f'TES4CyrodiilCrimeFaction.ModCrimeGold({arg} as Int, false)'
        if fname_low in ('payfine', 'payfinethief'):
            self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
            return 'TES4CyrodiilCrimeFaction.PlayerPayCrimeGold(false, false)'
        if fname_low in ('isplayerinjail', 'getplayerinjail', 'isplayerinprison'):
            self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
            return 'TES4CyrodiilCrimeFaction.IsPlayerExpelled()'
        if fname_low in ('senttojail',):
            self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
            return 'TES4CyrodiilCrimeFaction.IsPlayerExpelled()'

        # Fame/Infamy → GlobalVariable
        if fname_low in ('getpcfame',):
            self._property_refs['TES4Fame'] = 'GlobalVariable'
            return 'TES4Fame.GetValueInt()'
        if fname_low in ('getpcinfamy', 'getinfame'):
            self._property_refs['TES4Infamy'] = 'GlobalVariable'
            return 'TES4Infamy.GetValueInt()'
        if fname_low == 'modpcfame':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else '0'
            self._property_refs['TES4Fame'] = 'GlobalVariable'
            return f'TES4Fame.Mod({arg} as Float)'
        if fname_low == 'modpcinfamy':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else '0'
            self._property_refs['TES4Infamy'] = 'GlobalVariable'
            return f'TES4Infamy.Mod({arg} as Float)'
        if fname_low == 'setpcfame':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else '0'
            self._property_refs['TES4Fame'] = 'GlobalVariable'
            arg_type = self._var_types.get(arg.lower(), '') or self._property_refs.get(arg, self._property_refs.get(arg.lower(), ''))
            if arg_type == 'Float':
                arg = f'{arg} as Int'
            return f'TES4Fame.SetValueInt({arg})'
        if fname_low == 'setpcinfamy':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else '0'
            self._property_refs['TES4Infamy'] = 'GlobalVariable'
            arg_type = self._var_types.get(arg.lower(), '') or self._property_refs.get(arg, self._property_refs.get(arg.lower(), ''))
            if arg_type == 'Float':
                arg = f'{arg} as Int'
            return f'TES4Infamy.SetValueInt({arg})'

        # Weather functions
        if fname_low in ('forceweather', 'fw'):
            parts = [p.strip() for p in (args_str.replace(',', ' ').split() if args_str else ['None'])]
            weather = self._convert_expression(parts[0], extends) if parts else 'None'
            if parts and parts[0].strip():
                self._property_refs[parts[0].strip()] = 'Weather'
            flag = 'true' if (len(parts) <= 1 or parts[1] in ('1', 'true')) else 'false'
            return f'{weather}.ForceActive({flag})'
        if fname_low in ('setweather', 'sw'):
            parts = [p.strip() for p in (args_str.replace(',', ' ').split() if args_str else ['None'])]
            weather = self._convert_expression(parts[0], extends) if parts else 'None'
            if parts and parts[0].strip():
                self._property_refs[parts[0].strip()] = 'Weather'
            flag = 'true' if (len(parts) > 1 and parts[1] in ('1', 'true')) else 'false'
            return f'{weather}.SetActive({flag}, false)'
        if fname_low == 'releaseweatheroverride':
            return 'Weather.ReleaseOverride()'
        if fname_low in ('getiscurrentweather', 'getweatherpercent'):
            if fname_low == 'getweatherpercent':
                self._line_comments.append(';NE: GetWeatherPercent approximated')
                return '50'
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str and args_str.strip():
                self._property_refs[args_str.strip()] = 'Weather'
            return f'(Weather.GetCurrentWeather() == {arg})'

        # Sound functions
        if fname_low == 'playsound':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str and args_str.strip():
                self._property_refs[args_str.strip()] = 'Sound'
            return f'{arg}.Play(Game.GetPlayer())'
        if fname_low == 'playsound3d':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str and args_str.strip():
                self._property_refs[args_str.strip()] = 'Sound'
            ref = self._resolve_self_ref(ref_name, extends) if ref_name else 'Self'
            return f'{arg}.Play({ref})'
        if fname_low == 'stopsound':
            self._line_comments.append(';NE: StopSound has no Papyrus equivalent')
            return '0'

        # Magic shader/effect functions
        if fname_low in ('pms', 'playmagicshadervisuals'):
            parts = args_str.strip().split() if args_str else []
            shader_name = parts[0] if parts else None
            duration = parts[1] if len(parts) > 1 else '-1.0'
            if shader_name:
                safe = _safe_property_name(shader_name)
                self._property_refs[safe] = 'EffectShader'
                ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
                dur = self._convert_expression(duration, extends)
                return f'{safe}.Play({ref}, {dur})'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'Self.Play({ref})'
        if fname_low in ('sms', 'stopmagicshadervisuals'):
            parts = args_str.strip().split() if args_str else []
            shader_name = parts[0] if parts else None
            if shader_name:
                safe = _safe_property_name(shader_name)
                self._property_refs[safe] = 'EffectShader'
                ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
                return f'{safe}.Stop({ref})'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'Self.Stop({ref})'
        if fname_low == 'triggerhitshader':
            return 'Game.TriggerScreenBlood(3)'

        # StopCombatAlarmOnActor / SCAOnActor / SCA
        if fname_low in ('scaonactor', 'sca', 'stopcombatalarmonactor'):
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.StopCombat()'

        # ShowMap → marker.AddToMap(true)
        if fname_low == 'showmap':
            parts = args_str.strip().split() if args_str else []
            marker_name = parts[0] if parts else 'None'
            if marker_name != 'None':
                safe = _safe_property_name(marker_name)
                self._property_refs[safe] = 'ObjectReference'
                return f'{safe}.AddToMap(true)'
            return 'Self.AddToMap(true)'

        # Disposition (removed in Skyrim)
        if fname_low == 'moddisposition':
            parts = [p.strip() for p in (args_str.replace(',', ' ').split() if args_str else [])]
            if len(parts) >= 2:
                try:
                    val = int(parts[-1])
                    if val <= -100:
                        target = self._convert_expression(parts[0], extends)
                        tgt_key = target  # already canonical from _convert_expression
                        cur = self._property_refs.get(tgt_key, '')
                        if cur in ('', 'ObjectReference'):
                            self._property_refs[tgt_key] = 'Actor'
                        ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
                        return f'{target}.StartCombat({ref})'
                except (ValueError, IndexError):
                    pass
            self._line_comments.append(f';NE: ModDisposition')
            return '0'
        if fname_low == 'getdisposition':
            return '50'

        # SetAlert → DrawWeapon / no-op
        if fname_low == 'setalert':
            arg = args_str.strip() if args_str else '0'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            if arg in ('1', 'true'):
                # DrawWeapon is Actor-only; cast if ref is ObjectReference
                if ref == 'Self' and extends not in ('Actor',):
                    ref = '(Self as Actor)'
                return f'{ref}.DrawWeapon()'
            self._line_comments.append(';NE: SetAlert 0')
            return '0'

        # StartConversation → Say(None)
        if fname_low == 'startconversation':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.Say(None)'

        # Wait → no-op (TES4 Wait is a package instruction, not a time delay)
        if fname_low == 'wait':
            self._line_comments.append(';NE: Wait is a package instruction')
            return '0'

        # Reset3DState → MoveTo self (reloads 3D)
        if fname_low == 'reset3dstate':
            ref = self._resolve_self_ref(ref_name, extends)
            return f'{ref}.MoveTo({ref})'

        # ClearOwnership
        if fname_low == 'clearownership':
            ref = self._resolve_self_ref(ref_name, extends)
            return f'{ref}.SetActorOwner(Game.GetPlayer().GetActorBase())'

        # SetRestrained → SetDontMove
        if fname_low == 'setrestrained':
            arg = args_str.strip() if args_str else '0'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            val = 'true' if arg in ('1', 'true') else 'false'
            return f'{ref}.SetDontMove({val})'
        if fname_low == 'getrestrained':
            self._line_comments.append(';NE: GetRestrained')
            return '0'

        # SetForceRun → SpeedMult
        if fname_low == 'setforcerun':
            arg = args_str.strip() if args_str else '0'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            if arg in ('1', 'true'):
                return f'{ref}.SetActorValue("SpeedMult", 150.0)'
            return f'{ref}.SetActorValue("SpeedMult", 100.0)'

        # Faction crime tracking
        if fname_low in ('setpcfactionmurder', 'setpcfactionattack'):
            parts = [p.strip() for p in (args_str.replace(',', ' ').split() if args_str else [])]
            if parts:
                faction = self._convert_expression(parts[0], extends)
                self._property_refs[parts[0].strip()] = 'Faction'
                val = parts[1] if len(parts) > 1 else '1'
                if val in ('0', '0.0'):
                    return f'{faction}.SetCrimeGoldViolent(0)'
                return f'{faction}.SetCrimeGoldViolent(1000)'
            return ';NE: SetPCFactionMurder missing faction arg'
        if fname_low == 'setpcfactionsteal':
            parts = [p.strip() for p in (args_str.replace(',', ' ').split() if args_str else [])]
            if parts:
                faction = self._convert_expression(parts[0], extends)
                self._property_refs[parts[0].strip()] = 'Faction'
                val = parts[1] if len(parts) > 1 else '1'
                if val in ('0', '0.0'):
                    return f'{faction}.SetCrimeGold(0)'
                return f'{faction}.SetCrimeGold(100)'
            return ';NE: SetPCFactionSteal missing faction arg'
        if fname_low in ('getpcfactionmurder', 'getpcfactionattack'):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str and args_str.strip():
                self._property_refs[args_str.strip()] = 'Faction'
            return f'{arg}.GetCrimeGoldViolent()'
        if fname_low == 'getpcfactionsteal':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str and args_str.strip():
                self._property_refs[args_str.strip()] = 'Faction'
            return f'{arg}.GetCrimeGoldNonViolent()'

        # GetIsReference → equality check
        if fname_low == 'getisreference':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref} == {arg}'

        # GetInWorldSpace → WorldSpace comparison
        if fname_low in ('getinworldspace', 'getplayerinseworld'):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str and args_str.strip():
                self._property_refs[args_str.strip()] = 'WorldSpace'
            if ref_name:
                ref = self._resolve_self_ref(ref_name, extends)
                return f'{ref}.GetWorldSpace() == {arg}'
            return f'Game.GetPlayer().GetWorldSpace() == {arg}'

        # ModAmountSoldStolen
        if fname_low == 'modamountsoldstolen':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else '1'
            return f'Game.IncrementStat("Items Stolen", {arg})'

        # Reset → ref.Reset()
        if fname_low == 'reset':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.Reset()'

        # GetDetectionLevel → approximate
        if fname_low == 'getdetectionlevel':
            self._line_comments.append(';NE: GetDetectionLevel')
            return '0'

        # SetActorFullName → no-op (SKSE required for SetDisplayName)
        if fname_low == 'setactorfullname':
            self._line_comments.append(';NE: SetActorFullName')
            return '0'
        if fname_low == 'setdisplayname':
            self._line_comments.append(';NE: SetDisplayName')
            return '0'

        # SetCellFullName no-op
        if fname_low in ('setcellfullname', 'setcellownership'):
            self._line_comments.append(f';NE: {func_name}')
            return '0'

        # SetCombatStyle → no-op (managed by CK/race)
        if fname_low == 'setcombatstyle':
            self._line_comments.append(';NE: SetCombatStyle')
            return '0'

        # ForceFlee → StartCombat avoidance (approximate)
        if fname_low == 'forceflee':
            self._line_comments.append(';NE: ForceFlee')
            return '0'

        # SetActorsAI → no-op
        if fname_low == 'setactorsai':
            self._line_comments.append(';NE: SetActorsAI')
            return '0'

        # GetDayOfWeek → GameDaysPassed % 7
        if fname_low == 'getdayofweek':
            self._property_refs['GameDaysPassed'] = 'GlobalVariable'
            return '(GameDaysPassed.GetValueInt() % 7)'

        # IsPlayerSleeping
        if fname_low == 'isplayersleeping':
            return 'Game.GetPlayer().GetSleepState()'

        # GetIsPlayableRace
        if fname_low == 'getisplayablerace':
            return 'true'

        # DeleteFullActorCopy
        if fname_low == 'deletefullactorcopy':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.Delete()'

        # ResetInterior → cell.Reset()
        if fname_low == 'resetinterior':
            if args_str and args_str.strip():
                cell_name = _safe_property_name(args_str.strip().split()[0])
                self._property_refs[cell_name] = 'Cell'
                return f'{cell_name}.Reset()'
            ref = self._resolve_self_ref(ref_name, extends)
            return f'{ref}.Reset()'

        # IsPCRace → Game.GetPlayer().GetRace() == arg
        if fname_low in ('ispcrace', 'getpcisrace'):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str and args_str.strip():
                self._property_refs[args_str.strip()] = 'Race'
            return f'Game.GetPlayer().GetRace() == {arg}'

        # IsSwimming → no vanilla equivalent, approximate with submerged check
        if fname_low == 'isswimming':
            self._line_comments.append(';NE: IsSwimming')
            return '0'

        # GetTalkedToPC
        if fname_low in ('gettalkedtopc', 'gettalkedtopcp'):
            self._line_comments.append(';NE: GetTalkedToPC')
            return '0'

        # SetItemValue → no-op
        if fname_low == 'setitemvalue':
            self._line_comments.append(';NE: SetItemValue')
            return '0'

        # SetLevel → no-op
        if fname_low == 'setlevel':
            self._line_comments.append(';NE: SetLevel')
            return '0'

        # IsPCAMurderer / GetPCIsMurderer
        if fname_low in ('ispcamurderer', 'ispcanmurderer', 'getpcismurderer'):
            self._property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
            return '(TES4CyrodiilCrimeFaction.GetCrimeGoldViolent() > 0)'

        # SetForceSneaking
        if fname_low in ('setforcesneak',):
            self._line_comments.append(';NE: SetForceSneak')
            return '0'

        # Expel → faction.SetPlayerExpelled(true)
        if fname_low == 'expel':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str and args_str.strip():
                self._property_refs[args_str.strip()] = 'Faction'
            return f'{arg}.SetPlayerExpelled(true)'

        # SetDoorDefaultOpen → SetOpen
        if fname_low in ('setdoordefaultopen', 'opendoor'):
            ref = self._resolve_self_ref(ref_name, extends)
            return f'{ref}.SetOpen(true)'
        if fname_low == 'closedoor':
            ref = self._resolve_self_ref(ref_name, extends)
            return f'{ref}.SetOpen(false)'

        # SetScale / SetSize
        if fname_low in ('setsize',):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else '1.0'
            ref = self._resolve_self_ref(ref_name, extends)
            return f'{ref}.SetScale({arg})'
        if fname_low in ('getsize',):
            ref = self._resolve_self_ref(ref_name, extends)
            return f'{ref}.GetScale()'

        # Rotate → no-op
        if fname_low == 'rotate':
            self._line_comments.append(';NE: Rotate')
            return '0'

        # SetRigidBodyMass → no-op
        if fname_low == 'setrigidbodymass':
            self._line_comments.append(';NE: SetRigidBodyMass')
            return '0'

        # ResetFallDamageTimer → no-op
        if fname_low == 'resetfalldamagetimer':
            self._line_comments.append(';NE: ResetFallDamageTimer')
            return '0'

        # Comprehensive no-ops (functions with no meaningful Skyrim equivalent)
        _NO_OP_FUNCS = {
            'addtopic', 'removetopic', 'refreshtopiclist', 'setquestobject',
            'setcellpublicflag', 'disablelinkedpathpoints', 'enablelinkedpathpoints',
            'addachievement', 'closecurrentobliviongate', 'forcecloseobliviongate',
            'closeobliviongate', 'setignorefriendlyhits', 'setsceneiscomplex',
            'setnorumors', 'trapupdate', 'setdoordisabletakeoff',
            'setinvestmentgold', 'setpackduration', 'purgecellbuffers', 'pcb',
            'showdialogsubtitles', 'setpublic', 'essentialdeathreload',
            'showenchantment', 'playbink', 'showspellmaking',
            'showbirthsignmenu', 'setallreachable', 'setallvisible',
            'setshowquestitems', 'opencurrentcontainer', 'sendtrespassalarm',
            'setnoavoidance', 'respawnhorse', 'offerhorse', 'getisplayerbirthsign',
            'attachashpile', 'menumode', 'istimepassing',
            'getisalerted', 'getcrimeknown', 'isidleplaying',
            'iscurrentfurnitureref', 'iscurrentfurnitureobj',
            'getbookread', 'isonguard', 'isindangerouswater',
            'getplayercontrolsdisabled', 'getstartingpos',
            'getcurrentaiprocedure', 'getcurrentaipackage', 'getcurrentpackage',
            'getiscurrentpackage', 'hasvariable', 'hasbeenpickedup',
            'ispcanmurderer', 'gettalkedtopc', 'gettalkedtopcp',
            'setclass', 'setcellfullname', 'modamountsoldstolen',
        }
        if fname_low in _NO_OP_FUNCS:
            self._line_comments.append(f';NE: {func_name}')
            return '0'

        # Say: ref.Say topic [force] [headRef] -> ref.Say(topic)
        # SayTo: ref.SayTo target topic [force] -> ref.Say(topic)
        if fname_low in ('say', 'sayto', 'saycustom'):
            if args_str and ',' in args_str:
                pparts = [p.strip() for p in args_str.split(',')]
            else:
                pparts = args_str.split() if args_str else []
            if fname_low == 'sayto' and len(pparts) >= 2:
                # SayTo target topic [force] -> first arg is target, second is topic
                # If topic part has a trailing number (force flag), strip it
                topic_str = pparts[1].strip().split()[0] if pparts[1].strip() else 'None'
                topic = self._convert_expression(topic_str, extends)
                self._property_refs[topic_str] = 'Topic'
            else:
                topic = self._convert_expression(pparts[0], extends) if pparts else 'None'
                if pparts:
                    self._property_refs[pparts[0].strip()] = 'Topic'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.Say({topic})'

        # Functions whose Papyrus equivalent takes fewer args - drop the extra
        _DROP_ARGS_FUNCS = {'addscriptpackage', 'removescriptpackage', 'stopcombat', 'resurrect'}
        if fname_low in _DROP_ARGS_FUNCS:
            args_str = ''

        # SetFactionReaction/ModFactionReaction: TES4 setfactionreaction f1 f2 val
        # -> Papyrus f1.SetReaction(f2, val)
        if fname_low in ('setfactionreaction', 'modfactionreaction'):
            parts = args_str.split(',') if args_str and ',' in args_str else (args_str.split() if args_str else [])
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) >= 3:
                f1 = self._convert_expression(parts[0], extends)
                f2 = self._convert_expression(parts[1], extends)
                val = self._convert_expression(parts[2], extends)
                self._property_refs[parts[0].strip()] = 'Faction'
                self._property_refs[parts[1].strip()] = 'Faction'
                papyrus_fn = 'SetReaction' if fname_low == 'setfactionreaction' else 'ModReaction'
                return f'{f1}.{papyrus_fn}({f2}, {val})'
            # Fallback: not enough args
            return f';TODO: {func_name} {args_str}  ;needs faction1.SetReaction(faction2, val)'

        # GetGameSetting/getgs: arg is GMST name → quoted string
        if fname_low in ('getgamesetting', 'getgs'):
            setting = args_str.strip().strip('"') if args_str else 'fUnknown'
            # Use Int/Float/String variant based on naming convention (i=int, f=float, s=string)
            if setting.startswith('i'):
                return f'Game.GetGameSettingInt("{setting}")'
            elif setting.startswith('s'):
                return f'Game.GetGameSettingString("{setting}")'
            return f'Game.GetGameSettingFloat("{setting}")'

        # GetDeadCount: TES4 GetDeadCount ActorRef -> ref.IsDead() (no Skyrim equivalent for count)
        if fname_low == 'getdeadcount':
            if ref_name:
                ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
                return f'{ref}.IsDead()'
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'Self'
            if args_str:
                self._property_refs[arg] = 'Actor'  # arg is already canonical
            return f'{arg}.IsDead()'

        # ResetHealth: TES4 ResetHealth -> RestoreActorValue("Health", 9999)
        if fname_low == 'resethealth':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.RestoreActorValue("Health", 9999)'

        # EvaluatePackage/EVP/AddScriptPackage/RemoveScriptPackage/StopWaiting:
        # Skyrim version takes no args (drop TES4 package arg)
        if fname_low in ('evaluatepackage', 'evp', 'addscriptpackage', 'removescriptpackage', 'stopwaiting'):
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.EvaluatePackage()'

        # ClearLookAt / StopLook: Skyrim version takes no args (drop TES4 target arg)
        if fname_low in ('clearlookat', 'stoplook', 'stoplooking'):
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.ClearLookAt()'

        # GetAmountSoldStolen / GetPCMiscStat: -> Game.QueryStat("Items Stolen")
        if fname_low in ('getamountsoldstolen', 'getpcmiscstat'):
            stat = args_str.strip().strip('"') if args_str else 'Items Stolen'
            return f'Game.QueryStat("{stat}")'

        # GetEquippedItemType: Skyrim requires hand param (0=left, 1=right)
        if fname_low in ('getweaponanimtype', 'getequippeditemtype'):
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetEquippedItemType(1)'

        # IsActorUsingATorch: check if left hand has torch equipped (type 11)
        if fname_low == 'isactorusingatorch':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'({ref}.GetEquippedItemType(0) == 11)'

        # IsRidingHorse: Actor.IsOnMount() in Skyrim
        if fname_low == 'isridinghorse':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.IsOnMount()'

        # GetRace: ref.GetRace() -> ref.GetRace()
        if fname_low == 'getrace':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetRace()'

        # Expel: ref.Expel(faction) -> Game.GetPlayer().SetFactionRank(faction, -1)
        if fname_low == 'expel':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Faction'
            return f'Game.GetPlayer().SetFactionRank({arg}, -1)  ;TODO: Expel mapped to faction rank -1'

        # IsExpelled/IsPCExpelled/GetPCExpelled: check faction rank < 0
        if fname_low in ('isexpelled', 'ispcexpelled', 'getpcexpelled'):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Faction'
            return f'(Game.GetPlayer().GetFactionRank({arg}) < 0)'

        # IsInInterior: ref.IsInInterior -> ref.GetParentCell().IsInterior()
        if fname_low == 'isininterior':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetParentCell().IsInterior()'

        # Unlock: ref.Unlock -> ref.Lock(false)
        if fname_low == 'unlock':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.Lock(false)'

        # Cast: TES4 ref.cast spell [target] -> Papyrus spell.Cast(ref, target)
        # Cast is a method on Spell in Papyrus, not on ObjectReference
        if fname_low == 'cast':
            parts = args_str.split(',') if args_str and ',' in args_str else (args_str.split() if args_str else [])
            parts = [p.strip() for p in parts if p.strip()]
            spell = self._convert_expression(parts[0], extends) if parts else 'None'
            if parts:
                self._property_refs[parts[0].strip()] = 'Spell'
            source = self._resolve_self_ref(ref_name, extends, actor_func=True)
            if len(parts) > 1:
                target = self._convert_expression(parts[1], extends)
                return f'{spell}.Cast({source}, {target})'
            return f'{spell}.Cast({source})'

        # GetIsId: ref.GetIsId ActorBase -> (ref as Actor).GetActorBase() == actorBase
        if fname_low == 'getisid':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'ActorBase'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'({ref} as Actor).GetActorBase() == {arg}'

        # GetIsRace: ref.GetIsRace RaceRef -> ref.GetRace() == raceRef
        if fname_low in ('getisrace', 'getpcisrace'):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Race'
            if fname_low == 'getpcisrace':
                return f'Game.GetPlayer().GetRace() == {arg}'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetRace() == {arg}'

        # GetIsRef: ref.GetIsRef otherRef -> ref == otherRef
        if fname_low == 'getisref':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref} == {arg}'

        # GetInCell: ref.GetInCell CellEditorID -> ref.GetParentCell() == cellRef
        if fname_low == 'getincell':
            arg = args_str.strip().strip('"') if args_str else 'None'
            self._property_refs[arg] = 'Cell'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetParentCell() == {arg}'

        # GetInSameCell: ref.GetInSameCell otherRef -> ref.GetParentCell() == otherRef.GetParentCell()
        if fname_low in ('getinsamecell', 'getinsamecellas'):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'Game.GetPlayer()'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetParentCell() == {arg}.GetParentCell()'

        # GetIsSex: ref.GetIsSex Male/Female -> ref.GetActorBase().GetSex() == 0/1
        if fname_low == 'getissex':
            arg = args_str.strip().lower() if args_str else 'male'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            sex_val = '1' if 'female' in arg else '0'
            return f'({ref} as Actor).GetActorBase().GetSex() == {sex_val}'

        # PlayGroup: PlayGroup Forward 0 -> Debug.SendAnimationEvent(ref, "Forward")
        if fname_low == 'playgroup':
            parts = args_str.strip().split() if args_str else ['Idle']
            anim_name = parts[0].rstrip(',').strip('"').strip("'") if parts else 'Idle'
            # Map common Oblivion animation groups to Skyrim events
            _anim_map = {
                'forward': 'moveStart', 'backward': 'moveStartBackward',
                'left': 'moveStartStrafeLeft', 'right': 'moveStartStrafeRight',
                'idle': 'IdleForceDefaultState', 'specialidle': 'SpecialIdle',
                'unequip': 'Unequip', 'equip': 'Equip',
                'torchidle': 'IdleForceDefaultState',
                'castself': 'MagicCastSelf', 'casttouch': 'attackStart',
                'casttarget': 'attackStart',
                'jumpstart': 'JumpStandingStart', 'jumpland': 'JumpLand',
                'handstohandsattack': 'attackStart',
            }
            event = _anim_map.get(anim_name.lower(), anim_name)
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'Debug.SendAnimationEvent({ref}, "{event}")'

        # PickIdle / PlayIdle: -> Debug.SendAnimationEvent(ref, "IdleForceDefaultState")
        if fname_low in ('pickidle', 'playidle'):
            idle_name = args_str.strip() if args_str else 'IdleForceDefaultState'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'Debug.SendAnimationEvent({ref}, "{idle_name}")'

        # SetEssential: SetEssential ref 1/0 -> ref.GetActorBase().SetEssential(true/false)
        if fname_low == 'setessential':
            normalized = args_str.replace(',', ' ').strip() if args_str else ''
            parts = normalized.split() if normalized else []
            if len(parts) >= 2:
                target = self._convert_expression(parts[0], extends)
                val = 'true' if parts[1].strip() in ('1', 'true') else 'false'
                self._property_refs[target] = 'Actor'  # target is already canonical
                return f'({target} as Actor).GetActorBase().SetEssential({val})'
            elif ref_name:
                ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
                val = 'true' if args_str and args_str.strip() in ('1', 'true') else 'false'
                return f'({ref} as Actor).GetActorBase().SetEssential({val})'
            return f'; SetEssential {args_str or ""}  ;could not parse'

        # GetIsID: ref.GetIsID baseForm -> ref.GetBaseObject() == baseForm
        if fname_low == 'getisid':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'ActorBase'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetActorBase() == {arg}'

        # SetOwnership: ref.SetOwnership owner -> ref.SetActorOwner/SetFactionOwner
        if fname_low == 'setownership':
            ref = self._resolve_self_ref(ref_name, extends)
            if args_str:
                arg = self._convert_expression(args_str.strip(), extends)
                arg_low = args_str.strip().lower()
                # Check if arg is a faction
                arg_fid = self.xref.edid_to_formid.get(arg_low, '') if self.xref else ''
                arg_rtype = self.xref.record_type.get(arg_fid, '') if arg_fid else ''
                pref_type = self._property_refs.get(arg, self._property_refs.get(_safe_property_name(args_str.strip()), ''))
                if arg_rtype == 'FACT' or pref_type == 'Faction':
                    return f'{ref}.SetFactionOwner({arg})'
                else:
                    return f'{ref}.SetActorOwner({arg}.GetActorBase())'
            return f'{ref}.SetActorOwner(Game.GetPlayer().GetActorBase())'

        # MoveTo: ref.MoveTo target [X Y Z] -> ref.MoveTo(target, X, Y, Z)
        if fname_low in ('moveto', 'movetomarker'):
            normalized = args_str.replace(',', ' ') if args_str else ''
            parts = [p.strip() for p in normalized.split() if p.strip()]
            target = self._convert_expression(parts[0], extends) if parts else 'None'
            offsets = ', '.join(parts[1:4]) if len(parts) > 1 else ''
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            if offsets:
                return f'{ref}.MoveTo({target}, {offsets})'
            return f'{ref}.MoveTo({target})'

        # PlaceAtMe: ref.PlaceAtMe base [count] [distance] -> ref.PlaceAtMe(base, count)
        if fname_low == 'placeatme':
            # Normalize: replace commas with spaces, then split on whitespace
            normalized = args_str.replace(',', ' ') if args_str else ''
            parts = [p.strip() for p in normalized.split() if p.strip()]
            base = self._convert_expression(parts[0], extends) if parts else 'None'
            count = parts[1] if len(parts) > 1 else '1'
            # PlaceAtMe is on ObjectReference — don't promote type to Actor
            ref = self._resolve_self_ref(ref_name, extends, actor_func=False)
            if ref == 'Self' and extends == 'ActiveMagicEffect':
                ref = 'GetTargetActor()'
            elif ref == 'Self' and extends == 'TopicInfo':
                ref = 'akSpeakerRef'
            return f'{ref}.PlaceAtMe({base}, {count})'

        # CreateFullActorCopy: approximate with PlaceAtMe
        if fname_low == 'createfullactorcopy':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.PlaceAtMe({ref}.GetActorBase())'

        # WakeUpPC -> Game.GetPlayer().RestoreActorValue("Health", 0)
        if fname_low == 'wakeuppc':
            return 'Game.ForceThirdPerson()'

        # IsExpelled: faction arg -> faction rank check
        if fname_low == 'isexpelled':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str and args_str.strip():
                self._property_refs[args_str.strip()] = 'Faction'
            return f'({arg}.IsPlayerExpelled())'

        # GetContainer: item.GetContainer -> item.GetContainer()
        if fname_low == 'getcontainer':
            ref = self._resolve_self_ref(ref_name, extends)
            return f'{ref}.GetContainer()'

        # GetBookRead -> no direct equivalent, return 0
        if fname_low in ('getbookread', 'bookread'):
            self._line_comments.append(';NE: GetBookRead')
            return '0'

        # ShowClassMenu, ShowBirthSignMenu etc - no-ops
        if fname_low in ('showclassmenu', 'showbirthsignmenu', 'showracemenu'):
            self._line_comments.append(f';NE: {func_name}')
            return '0'

        # SetInCharGen: no-op
        if fname_low == 'setinchargen':
            self._line_comments.append(';NE: SetInCharGen')
            return '0'

        # SetPlayerInSEWorld: no-op
        if fname_low == 'setplayerinseworld':
            self._line_comments.append(';NE: SetPlayerInSEWorld')
            return '0'

        # ForceCloseOblivionGate / CloseCurrentOblivionGate: no-op
        if fname_low in ('forcecloseobliviongate', 'closecurrentobliviongate'):
            self._line_comments.append(f';NE: {func_name}')
            return '0'

        # IsInFaction: ref.IsInFaction faction -> ref.IsInFaction(faction)  
        if fname_low == 'isinfaction':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Faction'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.IsInFaction({arg})'

        # --- Standard function map lookup ---
        # Default args for TES4 functions that implicitly use "player" when
        # called with no arguments, but the Papyrus equivalent requires them.
        _DEFAULT_ARGS = {
            'activate': 'Game.GetPlayer()',
            'startconversation': 'Game.GetPlayer()',
            'sayto': 'Game.GetPlayer()',
            'getrandompercent': '0, 99',
            'isactordetected': 'Game.GetPlayer()',
            'getdetected': 'Game.GetPlayer()',
            'isdetectedby': 'Game.GetPlayer()',
            'setownership': 'Game.GetPlayer().GetActorBase()',
            'setactorowner': 'Game.GetPlayer().GetActorBase()',
        }
        entry = FUNCTION_MAP.get(fname_low)
        if entry:
            papyrus_func, needs_self, note = entry
            if papyrus_func is None:
                orig = f'{ref_name}.{func_name} {args_str}'.strip() if ref_name else f'{func_name} {args_str}'.strip()
                if note and not note.startswith(';TODO'):
                    # No-op: function has no Skyrim equivalent
                    # Return clean value (0) for expression contexts,
                    # store comment for line-level append
                    comment = f';NE: {orig}  {note}'
                    self._line_comments.append(comment)
                    return '0'
                return f';TODO: {orig}' + (f'  {note}' if note else '')
            if not args_str and fname_low in _DEFAULT_ARGS:
                args = _DEFAULT_ARGS[fname_low]
            else:
                args = self._convert_args(args_str, fname_low, extends) if args_str else ''
            if ref_name:
                ref = self._convert_ref(ref_name, extends)
                papyrus_low = papyrus_func.lower() if papyrus_func else ''
                is_actor_func = fname_low in _ACTOR_ONLY_FUNCTIONS or papyrus_low in _ACTOR_ONLY_FUNCTIONS
                # ActiveMagicEffect Self doesn't have actor/objref methods
                if ref == 'Self' and extends == 'ActiveMagicEffect':
                    ref = 'GetTargetActor()'
                elif ref == 'Self' and extends == 'TopicInfo' and is_actor_func:
                    ref = 'akSpeakerRef'
                # Cast ObjectReference refs to Actor for truly actor-only functions
                # (skip ObjectReference-shared methods like PlaceAtMe, AddItem, etc.)
                if is_actor_func and fname_low not in _OBJREF_SHARED_FUNCTIONS:
                    # akSpeakerRef is a fixed ObjectReference parameter in TopicInfo scripts
                    if ref == 'akSpeakerRef':
                        ref = f'(akSpeakerRef as Actor)'
                    else:
                        cur = self._property_refs.get(ref, '')
                        if cur == 'ObjectReference':
                            ref = f'({ref} as Actor)'
                        elif cur in ('',):
                            self._property_refs[ref] = 'Actor'
                result = f'{ref}.{papyrus_func}({args})'
            else:
                # No ref — infer implicit target based on script context
                if needs_self and fname_low in _ACTOR_ONLY_FUNCTIONS:
                    if extends == 'TopicInfo':
                        result = f'(akSpeakerRef as Actor).{papyrus_func}({args})'
                    elif extends == 'ActiveMagicEffect':
                        result = f'GetTargetActor().{papyrus_func}({args})'
                    elif extends not in ('Actor',):
                        result = f'(Self as Actor).{papyrus_func}({args})'
                    else:
                        result = f'{papyrus_func}({args})'
                else:
                    result = f'{papyrus_func}({args})'
            return f'{result}  {note}' if note else result
            
        # --- Fallback: unknown function ---
        args = self._convert_args(args_str, fname_low, extends) if args_str else ''
        if ref_name:
            ref = self._convert_ref(ref_name, extends)
            if fname_low in _ACTOR_ONLY_FUNCTIONS:
                if ref == 'akSpeakerRef':
                    ref = f'(akSpeakerRef as Actor)'
                else:
                    cur = self._property_refs.get(ref, '')
                    if cur == 'ObjectReference':
                        ref = f'({ref} as Actor)'
                    elif cur in ('',):
                        self._property_refs[ref] = 'Actor'
            return f'{ref}.{func_name}({args})  ;TODO: Verify'
        if fname_low in _ACTOR_ONLY_FUNCTIONS:
            if extends == 'TopicInfo':
                return f'(akSpeakerRef as Actor).{func_name}({args})  ;TODO: Verify'
            if extends == 'ActiveMagicEffect':
                return f'GetTargetActor().{func_name}({args})  ;TODO: Verify'
        return f'{func_name}({args})  ;TODO: Verify'

    def _quote_msg(self, args_str: str) -> str:
        """Quote a message argument if not already quoted.
        For MessageBox with buttons (e.g. '"text" "Yes" "No"'), extract only the message."""
        s = args_str.strip()
        if s.startswith('"'):
            # Find the end of the first quoted string
            end = s.index('"', 1) if '"' in s[1:] else len(s)
            first_str = s[:end + 1]
            # If there are more quoted strings (button labels), strip them
            return first_str
        return f'"{s}"'


