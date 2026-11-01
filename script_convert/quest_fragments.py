"""Quest-stage fragment scripts (`_QF_<quest>`) from a QUST's stage logs.

One `Fragment_Stage_NNNN_Item_N` function per stage log entry that has
journal text or a result script; the VMAD the importer writes names exactly
these, so the set here must match `quest_stage_fragments` on the import side.

See: docs/commentary/script_convert.md#quest-fragments
"""

from script_convert.constants import papyrus_script_name, script_prefix
from script_convert.context_setup import add_topic_call
from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph
from script_convert.objective_completion import (
    objective_lines,
    superseded_stages,
    sweep_targets,
)
from script_convert.scro_refs import (
    preload_scro_refs,
    preload_stage_scro_refs,
    resolve_scro_aliases,
    scro_list,
)
from script_convert.symbols import property_declarations

#: Script-scope re-entrancy latch for the modal chargen menus.
_CHARGEN_LATCH = 'Bool TES4_ChargenMenuBusy = False'


def stage_fragments(rec: dict) -> list:
    """(stage_idx, log_idx, text, script, completes, stage_i, log_j) per fragment."""
    out = []
    for i in range(int(rec.get('StageCount', '0') or 0)):
        stage_idx = int(rec.get(f'Stage[{i}].Index', '0') or 0)
        for j in range(int(rec.get(f'Stage[{i}].LogCount', '0') or 0)):
            text = rec.get(f'Stage[{i}].Log[{j}].Text', '')
            script = rec.get(f'Stage[{i}].Log[{j}].ResultScript', '')
            flags = int(rec.get(f'Stage[{i}].Log[{j}].Flags', '0') or 0)
            if text or script.strip():
                out.append((stage_idx, j, text, script, bool(flags & 0x01),
                            i, j))
    return out


def scripted_count(fragments: list) -> int:
    """How many fragments carry a TES4 result script (the stats unit)."""
    return sum(1 for f in fragments if f[3].strip())


def _fragment_lines(conv, rec: dict, xref, edid: str, frag: tuple,
                    plan: tuple, stage_reveals: dict,
                    show_objective: bool) -> list:
    """The Papyrus function for one stage log entry.

    See: docs/commentary/script_convert.md#complete-flag-ends-the-quest
    """
    stage_idx, log_idx, _text, script_src, completes, i, j = frag
    preload_stage_scro_refs(conv, rec, xref, i, j)
    conv.set_scro_aliases(resolve_scro_aliases(
        script_src or '', scro_list(rec, f'Stage[{i}].Log[{j}].'), xref))
    out = [f'Function Fragment_Stage_{stage_idx:04d}_Item_{log_idx}()']
    if show_objective:
        out.extend(objective_lines(plan[0], plan[1], stage_idx, log_idx))
    if completes:
        out += ['  CompleteAllObjectives()', '  CompleteQuest()']
    for gname in stage_reveals.get((edid.lower(), stage_idx), []):
        out.append(f'  {add_topic_call(ScriptConverter.topic_reveals, gname)}')
    if script_src.strip():
        out.extend(conv.convert_fragment(script_src, 'Quest'))
    out += ['EndFunction', '']
    return out


def _merge_property_types(prop_refs: dict) -> dict:
    """lower name -> (first-seen name, most specific type).

    See: docs/commentary/script_convert.md#property-type-merge
    """
    merged = {}
    for pname, ptype in sorted(prop_refs.items()):
        key = pname.lower()
        if key not in merged:
            merged[key] = (pname, ptype)
            continue
        name, cur = merged[key]
        if (cur == 'Quest' and ptype != 'Quest') or (
                ptype == 'ActorBase' and cur != 'ActorBase'):
            merged[key] = (name, ptype)
    return merged


def _declarations(conv, quest_globals: list, chargen_latch: bool) -> list:
    """Property and latch declarations that follow the ScriptName line.

    See: docs/commentary/script_convert.md#unlock-globals-declared-once
    """
    out = []
    prop_refs = conv.get_property_refs()
    if prop_refs:
        merged = _merge_property_types(prop_refs)
        out += property_declarations({n: t for n, t in merged.values()},
                                     {g.lower() for g in quest_globals})
        out.append('')
    out += [f'GlobalVariable Property {g} Auto' for g in quest_globals]
    if chargen_latch:
        out.append(_CHARGEN_LATCH)
    return out


def quest_fragment_psc(rec: dict, edid: str, xref: CrossRefGraph,
                       fragments: list, stage_reveals: dict) -> tuple:
    """(script name, Papyrus source) for one quest's stage fragments.

    A quest with authored objectives displays none of its own: its stage
    indices are not objective indices.
    See: docs/commentary/script_convert.md#one-objective-per-stage
    """
    plan = (superseded_stages(rec, fragments),
            sweep_targets(rec, fragments, edid))
    synthesize = 'ObjectiveCount' not in rec
    conv = ScriptConverter(xref)
    preload_scro_refs(conv, rec, xref)
    script_name = papyrus_script_name(edid, script_prefix('_QF_'))
    body, shown, chargen_latch = [], set(), False
    for frag in fragments:
        show = synthesize and bool(frag[2]) and frag[0] not in shown
        if show:
            shown.add(frag[0])
        body += _fragment_lines(conv, rec, xref, edid, frag, plan,
                                stage_reveals, show)
        chargen_latch = chargen_latch or conv.sc.uses_chargen_menus
    quest_globals = sorted({g for (q, _s), gs in stage_reveals.items()
                            if q == edid.lower() for g in gs})
    lines = ([f'ScriptName {script_name} extends Quest Hidden', '']
             + _declarations(conv, quest_globals, chargen_latch)
             + body + conv.get_cell_family_helpers())
    return script_name, '\n'.join(lines)
