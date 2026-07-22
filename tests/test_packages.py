"""TES4 PACK -> TES5 PACK conversion tests.

Every invariant asserted here was verified against real Skyrim.esm records (see
docs/package_conversion_plan.md); the constants are not guesses.
"""

import struct

import pytest

from tes5_import.dialog_conditions import (
    GET_VM_SCRIPT_VARIABLE,
    convert_ctda_list_with_strings,
    papyrus_var_name,
)
from tes5_import.pack_aliases import PackagePlan, build_script_var_map
from tes5_import.pack_converter import (
    PackContext,
    SPEED_RUN,
    T5_MUST_COMPLETE,
    T5_OFFERS_SERVICES,
    T5_WEAPON_DRAWN,
    build_psdt,
    convert_PACK,
    convert_flags,
)
from tes5_import.pack_templates import (
    ESCORT,
    FOLLOW,
    PKDT_TYPE_PACKAGE,
    SANDBOX,
    SLEEP,
    TRAVEL,
)


def _subrecords(record: bytes) -> list:
    """[(sig, data)] from a packed TES5 record (24-byte header)."""
    out = []
    i = 24
    while i < len(record):
        sig = record[i:i + 4].decode('latin1')
        size = struct.unpack('<H', record[i + 4:i + 6])[0]
        out.append((sig, record[i + 6:i + 6 + size]))
        i += 6 + size
    return out


def _first(subs, sig):
    return next(d for s, d in subs if s == sig)


def _pack(ptype, **kw):
    rec = {
        'Signature': 'PACK', 'FormID': '00001000', 'EditorID': 'TestPack',
        'RecordFlags': '0', 'PKDT.Flags': '0', 'PKDT.Type': str(ptype),
        'PSDT.Month': '-1', 'PSDT.DayOfWeek': '-1', 'PSDT.Date': '0',
        'PSDT.Time': '-1', 'PSDT.Duration': '0',
    }
    rec.update({k: str(v) for k, v in kw.items()})
    return rec


# --- The template-instance contract -------------------------------------
# Verified by census: 5,764 of 5,961 vanilla packages are Type-18 instances
# pointing at a Type-19 root. Emitting a root (19) would give an actor a package
# with no instance data.

def test_emits_type_18_instance_not_template_root():
    b = convert_PACK(_pack(6), PackContext())
    pkdt = _first(_subrecords(b), 'PKDT')
    assert pkdt[4] == PKDT_TYPE_PACKAGE == 18


@pytest.mark.parametrize('ptype,template', [
    (6, TRAVEL),     # Travel
    (5, SANDBOX),    # Wander -> Sandbox
    (4, SLEEP),      # Sleep -> dedicated Sleep template (not Sandbox+flag)
    (1, FOLLOW),     # Follow
    (2, ESCORT),     # Escort
    (7, FOLLOW),     # Accompany -> Follow(Accompany?=1)
])
def test_type_maps_to_expected_template(ptype, template):
    b = convert_PACK(_pack(ptype), PackContext())
    count, tmpl, ver = struct.unpack('<III', _first(_subrecords(b), 'PKCU'))
    assert tmpl == template.formid
    assert count == len(template.inputs)
    assert ver == template.version


def test_data_inputs_match_template_signature_positionally():
    """The ANAM list must be the root's declared input order, and the UNAM index
    list + XNAM must be copied verbatim. A mismatch silently feeds the wrong
    value into a slot (e.g. max radius into min radius)."""
    b = convert_PACK(_pack(2, **{
        'PTDT.Type': 0, 'PTDT.Target': '00000014', 'PTDT.Count': 0,
        'PLDT.Type': 0, 'PLDT.Location': '0003662C', 'PLDT.Radius': 1000,
    }), PackContext())
    subs = _subrecords(b)

    anams = [d.rstrip(b'\0').decode('latin1') for s, d in subs if s == 'ANAM']
    assert anams == list(ESCORT.inputs)

    unams = [struct.unpack('<b', d)[0] for s, d in subs if s == 'UNAM']
    assert unams == list(ESCORT.index_list)
    assert _first(subs, 'XNAM')[0] == ESCORT.xnam


def test_all_three_procedure_markers_present():
    subs = _subrecords(convert_PACK(_pack(6), PackContext()))
    sigs = [s for s, _ in subs]
    for marker in ('POBA', 'POEA', 'POCA'):
        assert marker in sigs


# --- Locations and targets are COPIED, not approximated -------------------

def test_location_type_and_radius_survive():
    """TES4 PLDT types 0..5 are the same enum in TES5, and vanilla uses them
    (type 1 'in cell' appears 448x), so a cell-scoped package stays cell-scoped."""
    b = convert_PACK(_pack(6, **{
        'PLDT.Type': 1, 'PLDT.Location': '0001ABCD', 'PLDT.Radius': 512,
    }), PackContext())
    ltype, value, radius = struct.unpack('<iIi', _first(_subrecords(b), 'PLDT'))
    assert (ltype, radius) == (1, 512)
    assert value & 0x00FFFFFF == 0x0001ABCD


def test_target_type_survives():
    b = convert_PACK(_pack(1, **{
        'PTDT.Type': 0, 'PTDT.Target': '00000014', 'PTDT.Count': 0,
    }), PackContext())
    ttype, target, _ = struct.unpack('<iIi', _first(_subrecords(b), 'PTDA'))
    assert ttype == 0
    assert target == 0x00000014


# --- Schedule ------------------------------------------------------------

def test_psdt_duration_hours_become_minutes():
    """TES4 duration is HOURS, TES5 is MINUTES. Miss it and a 6-hour sleep
    package becomes a 6-minute nap."""
    rec = _pack(4, **{'PSDT.Time': 22, 'PSDT.Duration': 8})
    month, dow, date, hour, minute, duration = struct.unpack(
        '<bbBbb3xi', build_psdt(rec))
    assert hour == 22
    assert duration == 8 * 60


# --- Flags: re-derived per bit, never blind-copied ------------------------

def test_flags_are_remapped_not_copied():
    """TES4 0x8 = 'lock doors at start'; TES5 0x8 = 'maintain speed at goal'.
    A blind copy would set an unrelated engine behaviour."""
    flags, _ = convert_flags(0x00000008, 6)      # TES4 lock-doors-at-start
    assert flags & 0x00000008 == 0               # must NOT become maintain-speed

    flags, _ = convert_flags(0x00000001, 6)      # offers services: same bit
    assert flags & T5_OFFERS_SERVICES

    flags, _ = convert_flags(0x00000004, 6)      # must complete: same bit
    assert flags & T5_MUST_COMPLETE


def test_always_run_becomes_preferred_speed_field():
    """TES4 'always run' is a FLAG; TES5 speed is a FIELD. The old brainstorm
    proposed mapping it onto an 'Unknown' bit, which would set random behaviour."""
    flags, speed = convert_flags(0x00002000, 6)
    assert speed == SPEED_RUN


def test_ambush_sets_weapon_drawn():
    flags, _ = convert_flags(0, 9)               # TES4 Ambush
    assert flags & T5_WEAPON_DRAWN


# --- The GetScriptVariable gate (the fgc01rats mechanism) -----------------

def test_getscriptvariable_becomes_getvmscriptvariable_with_cis2():
    """Oblivion gates quest packages on GetScriptVariable(ref, varIdx).  Skyrim
    still lists function 53 but the legacy VM is gone — vanilla uses it ZERO
    times.  It must become GetVMScriptVariable(630) with the Papyrus property
    name in a companion CIS2 string, or the package can never fire.

    This is FGC01Rats' escort package: GetScriptVariable(PinarusREF, packageVAR)
    == 1, set by the dialogue INFO that agrees to help.
    """
    rec = {
        'ConditionCount': '1',
        # type=0 comp=1.0 func=53 param1=PinarusREF param2=varIdx 1
        'Condition[0].Raw':
            '000000000000803f3500000072bc00000100000000000000',
    }
    script_vars = {0x0000BC72: {1: 'packageVAR'}}
    out = convert_ctda_list_with_strings(rec, script_vars)
    assert len(out) == 1
    ctda, cis2 = out[0]

    func = struct.unpack_from('<H', ctda, 8)[0]
    assert func == GET_VM_SCRIPT_VARIABLE == 630
    assert cis2 == papyrus_var_name('packageVAR') == '::packageVAR_var'
    # comparison value survives
    assert struct.unpack_from('<f', ctda, 4)[0] == 1.0


def test_unresolvable_script_variable_is_dropped_not_emitted():
    """A condition we cannot name would invoke a dead function and be
    permanently false — silently disabling the package it gates. Drop it."""
    rec = {
        'ConditionCount': '1',
        'Condition[0].Raw':
            '000000000000803f3500000072bc00000100000000000000',
    }
    assert convert_ctda_list_with_strings(rec, {}) == []


# --- Quest ownership / aliasing ------------------------------------------

def test_quest_package_targets_route_through_alias():
    """A quest package names its actor/target through a reference alias (PTDA
    type 4), which is what lets it outrank the actor's standing schedule."""
    plan = PackagePlan()
    plan.owner_quest[0x00001000] = 0x00035713
    plan.alias_index[(0x00035713, 0x00000014)] = 3      # player alias
    ctx = PackContext(plan=plan)

    b = convert_PACK(_pack(2, **{
        'PTDT.Type': 0, 'PTDT.Target': '00000014', 'PTDT.Count': 0,
    }), ctx)
    subs = _subrecords(b)

    ttype, alias, _ = struct.unpack('<iii', _first(subs, 'PTDA'))
    assert (ttype, alias) == (4, 3)          # 4 = Ref Alias
    # and the package declares its owning quest
    assert struct.unpack('<I', _first(subs, 'QNAM'))[0] & 0x00FFFFFF \
        == 0x00035713


def test_script_var_map_walks_refr_to_base_to_script():
    """A condition names a REFR; the variable table lives on the SCPT attached
    to the REFR's BASE record."""
    by_type = {
        'SCPT': [{'FormID': '00036634', 'VariableCount': '1',
                  'Variable[0].Index': '1', 'Variable[0].Name': 'packageVAR'}],
        'NPC_': [{'FormID': '0000A29D', 'SCRI': '00036634'}],
        'ACHR': [{'FormID': '0000BC72', 'NAME': '0000A29D'}],
    }
    vars_by_ref = build_script_var_map(by_type)
    assert vars_by_ref[0x0000BC72] == {1: 'packageVAR'}


def test_alias_location_uses_reference_alias_type_8():
    """A quest package's location alias must be PLDT type 8 'Alias (reference)'.

    Type 9 is 'Alias (location)' and expects an LCTN-type alias; given a
    reference-alias index it resolves to nothing, so the procedure starts (the
    actor stands up) and never travels.  Skyrim.esm census: type 8 = 585 uses,
    type 9 = 1 use out of 6,838 PLDTs.
    """
    from tes5_import.pack_converter import build_alias_location
    ltype, alias, radius = struct.unpack('<iii', build_alias_location(5, 1000))
    assert (ltype, alias, radius) == (8, 5, 1000)


def test_quest_escort_location_routes_through_alias_as_type_8():
    """End-to-end: a quest-owned Escort whose PLDT names a ref gets type 8."""
    plan = PackagePlan()
    plan.owner_quest[0x00001000] = 0x00035713
    plan.alias_index[(0x00035713, 0x0003662C)] = 5
    ctx = PackContext(plan=plan)

    b = convert_PACK(_pack(2, **{
        'PLDT.Type': 0, 'PLDT.Location': '0003662C', 'PLDT.Radius': 1000,
        'PTDT.Type': 0, 'PTDT.Target': '00000014', 'PTDT.Count': 0,
    }), ctx)
    ltype, alias, _ = struct.unpack('<iii', _first(_subrecords(b), 'PLDT'))
    assert (ltype, alias) == (8, 5)


# --- GetVMScriptVariable actor scripts move base->placed ref --------------
# GetVMScriptVariable(ref, "::var_var") reads the property off the script on the
# REFERENCE named in param1, not the base actor.  So an actor gated by such a
# package condition must carry the variable-bearing script on its placed ACHR,
# or the condition never passes and the quest package never wins (Pinarus stays
# put).  Verified against Skyrim.esm: 100% of vanilla func-630 package
# conditions name a REFR that carries its own VMAD.

def _reloc_setup(monkeypatch, placements):
    """Seed _OBJECT_VMAD with a base-attached actor script and a PACK condition
    reading its variable via GetScriptVariable(func 53) on a placed ACHR."""
    from tes5_import import object_scripts as os_
    from tes5_import.text_reader import set_formid_index_offset
    set_formid_index_offset(0)          # keep raw fids for a clean assertion
    os_._OBJECT_VMAD.clear()
    os_._OBJECT_VMAD[0x0000A29D] = b'VMAD\x04\x00base'   # marker bytes

    # PACK gated on GetScriptVariable(PinarusRef=0xBC72, var index 1).
    ctda = struct.pack('<B3xIHHIIII I',
                       0, struct.unpack('<I', struct.pack('<f', 1.0))[0],
                       53, 0, 0x0000BC72, 1, 0, 0, 0xFFFFFFFF)
    achrs = [{'FormID': '0000BC72', 'NAME': '0000A29D'}]
    achrs += [{'FormID': f'000B{n:04X}', 'NAME': '0000A29D'}
              for n in range(placements - 1)]
    by_type = {
        'PACK': [{'FormID': '00036633',
                  'Condition[0].Raw': ctda.hex()}],
        'ACHR': achrs,
    }
    return os_, by_type


def test_actor_script_relocated_to_placed_ref(monkeypatch):
    os_, by_type = _reloc_setup(monkeypatch, placements=1)
    moved = os_._relocate_actor_scripts_to_refs(by_type, 0)
    assert moved == 1
    # Script now lives on the placed ACHR ...
    assert os_._OBJECT_VMAD.get(0x0000BC72) == b'VMAD\x04\x00base'
    # ... and ONLY there (single placement -> moved off the base).
    assert 0x0000A29D not in os_._OBJECT_VMAD


def test_shared_base_keeps_script_and_adds_ref(monkeypatch):
    """A base placed more than once keeps its script (siblings need it) and the
    read ref gains its own copy."""
    os_, by_type = _reloc_setup(monkeypatch, placements=3)
    moved = os_._relocate_actor_scripts_to_refs(by_type, 0)
    assert moved == 1
    assert os_._OBJECT_VMAD.get(0x0000BC72) == b'VMAD\x04\x00base'
    assert os_._OBJECT_VMAD.get(0x0000A29D) == b'VMAD\x04\x00base'
