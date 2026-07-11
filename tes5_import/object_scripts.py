"""Attach converted TES4 object scripts (SCPT via SCRI) to their records as VMAD.

TES4 object records (ACTI, FLOR, CONT, DOOR, FURN, MISC, KEYM, …) reference a
SCPT record through the ``SCRI`` field.  ``script_convert`` already converts each
such SCPT into a full ``TES4_<EditorID>.psc`` extending ObjectReference/Actor with
its OnActivate / OnLoad / GameMode(→OnUpdate) event handlers, and the pipeline
compiles those to ``.pex``.  What was missing is the binding: without a VMAD
subrecord naming that script on the object record, the engine never attaches it,
so activating an altar showed no message and gave no effect, a nirnroot never
stopped its sound, etc.

This module builds that binding once, up front:

  build_object_script_plan(by_type, xref, fid_to_edid) -> {record_fid_int: VMADinfo}

Each object record's plan carries the script name plus the FormID bindings for its
Object-typed properties (the SCPT's ``ref`` variables and every record the script
names by EditorID), resolved to the OUTPUT plugin's FormID space.  The record
converters then splice the VMAD in right after EDID (Skyrim order: EDID VMAD OBND …).
"""

from script_convert.converter import ScriptConverter
from script_convert.constants import _safe_property_name
from script_convert.pipeline import build_vmad_object_script
from .text_reader import parse_export_file, get_formid_index_offset

# Papyrus property types that are literal-valued (not bound to a FormID).
_VALUE_TYPES = {'Int', 'Float', 'Bool'}

_PLAYER_FORMID = 0x14

# Object record types that carry a SCRI in TES4 and become plain object scripts
# in Skyrim.  Actors (NPC_/CREA) and QUST/INFO have their own script pipelines
# and are intentionally excluded.
SCRIPTABLE_TYPES = {
    'ACTI', 'FLOR', 'CONT', 'DOOR', 'FURN', 'MISC', 'KEYM', 'LIGH',
    'STAT', 'BOOK', 'WEAP', 'ARMO', 'CLOT', 'AMMO', 'INGR', 'ALCH',
    'APPA', 'SLGM', 'SGST', 'SBSP',
}

# Output (TES5) signatures whose xEdit record definition actually lists a VMAD
# subrecord.  Attaching a VMAD to any other record makes xEdit flag it as an
# "unexpected (or out of order) subrecord" — e.g. ALCH/SLGM/STAT/AMMO have no
# VMAD in the Skyrim def, so a converted object script is dropped for those.
# Sourced from wbDefinitionsTES5.pas (records containing a plain `wbVMAD,`).
VMAD_SUPPORTED_OUTPUT_TYPES = {
    'ACTI', 'APPA', 'ARMO', 'BOOK', 'CONT', 'DOOR', 'EXPL', 'FLOR', 'FURN',
    'INGR', 'KEYM', 'LIGH', 'MGEF', 'MISC', 'NPC_', 'RACE', 'TACT', 'TREE',
    'WEAP',
}

# record FormID (int, output space) -> packed VMAD bytes.  Filled by
# build_object_script_plan(); read by the record converters via get_object_vmad().
_OBJECT_VMAD: dict[int, bytes] = {}


def get_object_vmad(record_fid: int) -> bytes:
    """Packed VMAD subrecord for a record's attached object script (b'' if none)."""
    return _OBJECT_VMAD.get(record_fid, b'')


def _remap(fid: int, offset: int) -> int:
    """Remap an Oblivion.esm (index-0) FormID into the output plugin space.

    Mirrors text_reader.get_formid: only index-0 forms with low bits >= 0x100
    are shifted (engine-hardcoded low forms like Player 0x14 stay put).
    """
    if offset and fid and (fid >> 24) == 0x00 and (fid & 0x00FFFFFF) >= 0x100:
        return (fid & 0x00FFFFFF) | (offset << 24)
    return fid


def build_object_script_plan(by_type: dict, xref, fid_to_edid: dict) -> int:
    """Compute and cache the VMAD for every object record with an attached SCPT.

    by_type: {signature: [record dicts]} from the export.
    xref: CrossRefGraph (already populated with edid/formid/record_type +
          script_all_vars/ref_as_int via build_ref_as_int_map).
    fid_to_edid: {raw_formid_int: editor_id} for resolving property targets.

    Returns the number of records that received a script VMAD.
    """
    _OBJECT_VMAD.clear()
    offset = get_formid_index_offset()

    # SCPT FormID -> (EditorID, SCTX source, extends class)
    scpt_by_fid: dict[str, tuple] = {}
    for rec in by_type.get('SCPT', []):
        fid = rec.get('FormID', '')
        sctx = rec.get('SCTX', '')
        if not fid or not sctx or not sctx.strip():
            continue
        scpt_by_fid[fid] = (rec.get('EditorID', ''), sctx,
                            xref.get_extends_class(fid))

    from .constants import TYPE_MAP

    count = 0
    for sig in SCRIPTABLE_TYPES:
        # Skip types whose Skyrim output record has no VMAD field in its def;
        # binding a script there only produces an "unexpected subrecord" error
        # (ALCH, SLGM, STAT, AMMO, and SGST→SCRL / SBSP→STAT map here).
        out_sig = TYPE_MAP.get(sig, sig)
        if out_sig not in VMAD_SUPPORTED_OUTPUT_TYPES:
            continue
        for rec in by_type.get(sig, []):
            scri = rec.get('SCRI', '')
            if not scri or scri not in scpt_by_fid:
                continue
            rec_fid_str = rec.get('FormID', '')
            if not rec_fid_str:
                continue
            try:
                rec_fid = _remap(int(rec_fid_str, 16), offset)
            except ValueError:
                continue

            edid, sctx, extends = scpt_by_fid[scri]
            script_name = f'TES4_{_safe_property_name(edid or f"Script_{scri}")}'

            try:
                obj_props = _resolve_props(sctx, edid, extends, xref,
                                           fid_to_edid, offset)
            except Exception:
                obj_props = {}

            from .writer import pack_subrecord
            _OBJECT_VMAD[rec_fid] = pack_subrecord(
                'VMAD', build_vmad_object_script(script_name, obj_props))
            count += 1

    return count


def _resolve_props(sctx: str, edid: str, extends: str, xref,
                   fid_to_edid: dict, offset: int) -> dict:
    """Run the converter to learn the script's property refs, then bind the
    Object-typed ones to their target record FormIDs (output space).

    Value-typed properties (Int/Float/Bool locals) are left unbound — the engine
    defaults them to zero, which matches the TES4 script's initial state.
    """
    conv = ScriptConverter(xref)
    name = _safe_property_name(edid or 'Script')
    conv.convert_standalone(name, sctx, extends, edid)

    obj_props: dict[str, int] = {}
    for pname, ptype in conv.get_property_refs().items():
        if ptype in _VALUE_TYPES:
            continue
        safe = _safe_property_name(pname)
        low = pname.lower()
        if low in ('player', 'playerref'):
            obj_props[safe] = _PLAYER_FORMID
            continue
        fid_hex = xref.edid_to_formid.get(low, '')
        if not fid_hex:
            continue
        try:
            raw = int(fid_hex, 16)
        except ValueError:
            continue
        if raw == 0:
            continue
        obj_props[safe] = _remap(raw, offset)
    return obj_props
