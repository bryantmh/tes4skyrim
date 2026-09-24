"""TES4 PACK -> TES5 PACK conversion tests.

Every invariant asserted here was verified against real Skyrim.esm records (see
docs/commentary/tes5_import_package.md); the constants are not guesses.
"""

import struct

import pytest

from tes5_import.base.conditions import (
    GET_VM_SCRIPT_VARIABLE,
    convert_ctda_list_with_strings,
    papyrus_var_name,
)
from tes5_import.packages.aliases import PackagePlan, build_script_var_map
from tes5_import.packages.converter import (
    PackContext,
    SPEED_RUN,
    T5_MUST_COMPLETE,
    T5_OFFERS_SERVICES,
    T5_UNLOCK_DOORS_START,
    T5_WEAPON_DRAWN,
    build_psdt,
    convert_PACK,
    convert_flags,
)
from tes5_import.packages.templates import (
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


def test_unlock_doors_at_location_opens_the_shop():
    """TES4 unlock-at-location (0x100) becomes TES5 unlock-at-start."""
    flags, _ = convert_flags(0x311, 6)
    assert flags == T5_OFFERS_SERVICES | T5_UNLOCK_DOORS_START | 0x200


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


def test_unresolvable_script_variable_reads_zero_like_tes4():
    """A GetScriptVariable we cannot name must still be emitted, against a
    sentinel that no script declares.

    TES4 returns 0 for a variable that does not exist (scriptless base,
    missing index, deleted ref), and Skyrim's GetVMScriptVariable returns 0
    for a name no attached script has — so the sentinel reproduces the
    authored outcome. Dropping the condition failed OPEN: SE08's five
    Xedilian victims (base SE08XeddefenNPC01 has no SCRI) force-greeted and
    fled unconditionally."""
    from tes5_import.base.conditions import _UNRESOLVED_VAR_SENTINEL
    rec = {
        'ConditionCount': '1',
        'Condition[0].Raw':
            '000000000000803f3500000072bc00000100000000000000',
    }
    out = convert_ctda_list_with_strings(rec, {})
    assert len(out) == 1
    ctda, cis2 = out[0]
    assert cis2 == _UNRESOLVED_VAR_SENTINEL
    assert struct.unpack_from('<H', ctda, 8)[0] == 630   # GetVMScriptVariable
    assert struct.unpack_from('<I', ctda, 12)[0] & 0xFFFFFF == 0xBC72
    assert struct.unpack_from('<f', ctda, 4)[0] == 1.0    # authored compare


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


def test_script_var_map_covers_every_scriptable_base():
    """A scripted WEAP/MISC ref resolves too: the goblin leaders' totem staffs
    (CreatureGoblinLeaderFindHead*, 21 PACK conditions) and 11 INFO gates on
    scripted MISC refs lost their variable name under an NPC_/CREA/ACTI/CONT/
    DOOR/QUST-only list."""
    by_type = {
        'SCPT': [{'FormID': '00000100', 'VariableCount': '1',
                  'Variable[0].Index': '2', 'Variable[0].Name': 'totemHeld'}],
        'WEAP': [{'FormID': '00000200', 'SCRI': '00000100'}],
        'MISC': [{'FormID': '00000201', 'SCRI': '00000100'}],
        'REFR': [{'FormID': '00000300', 'NAME': '00000200'},
                 {'FormID': '00000301', 'NAME': '00000201'}],
    }
    vars_by_ref = build_script_var_map(by_type)
    assert vars_by_ref[0x300] == {2: 'totemHeld'}
    assert vars_by_ref[0x301] == {2: 'totemHeld'}


def test_alias_location_uses_reference_alias_type_8():
    """A quest package's location alias must be PLDT type 8 'Alias (reference)'.

    Type 9 is 'Alias (location)' and expects an LCTN-type alias; given a
    reference-alias index it resolves to nothing, so the procedure starts (the
    actor stands up) and never travels.  Skyrim.esm census: type 8 = 585 uses,
    type 9 = 1 use out of 6,838 PLDTs.
    """
    from tes5_import.packages.converter import build_alias_location
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
    from tes5_import.base import object_scripts as os_
    from tes5_import.base.text_reader import set_formid_index_offset
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


def test_addscriptpackage_reaches_the_actors_quest_alias():
    """A package forced on by `AddScriptPackage` must land on the actor's alias.

    TES4's `AddScriptPackage` puts a package on an actor that does NOT list it
    in its AI array — that is the point of the call. Skyrim has no equivalent
    (`SetOverridePackage` is a Fallout 4 API; it is absent from SkyrimSE.exe
    1.6, which exports only `EvaluatePackage` and `KeepOffsetFromActor`), and
    the converter maps the call to a bare `EvaluatePackage()`. That only
    re-runs arbitration over packages the actor ALREADY has, so unless the
    forced package is attached to the actor's quest alias as an ALPC the engine
    can never select it and the package silently never runs.

    Nehrim's MQ00 is the visible case: Celebro stops following the player
    because MQ00CalebroPackage04 — added by INFO 0x11D3, which is gated
    `GetIsID Celebro02` — was on no actor at all.
    """
    from tes5_import.packages.aliases import (PackagePlan,
                                          build_script_assigned_packages)

    # INFO gated on GetIsID (func 72) -> Celebro02 (0x11C7), forcing Package04.
    getisid = struct.pack('<BBBBfIIIiI', 0, 0, 0, 0, 1.0,
                          72, 0x000011C7, 0, 0, 0xFFFFFFFF)
    getstage = struct.pack('<BBBBfIIIiI', 0x60, 0, 0, 0, 20.0,
                           58, 0x00000811, 0, 0, 0xFFFFFFFF)
    by_type = {
        'PACK': [{'FormID': '000011DD', 'EditorID': 'MQ00CalebroPackage04',
                  'Condition[0].Raw': getstage.hex()}],
        'QUST': [{'FormID': '00000811', 'EditorID': 'MQ00'}],
        'NPC_': [{'FormID': '000011C7', 'EditorID': 'Celebro02'}],
        'ACHR': [{'FormID': '000011CA', 'EditorID': 'Celebro2Ref',
                  'NAME': '000011C7'}],
        'INFO': [{'FormID': '000011D3', 'Condition[0].Raw': getisid.hex(),
                  'ResultScript':
                      'SetStage MQ00 20\r\nAddScriptPackage '
                      'MQ00CalebroPackage04'}],
    }
    fid_to_edid = {0x000011DD: 'MQ00CalebroPackage04',
                   0x000011C7: 'Celebro02', 0x000011CA: 'Celebro2Ref'}

    assigned = build_script_assigned_packages(by_type, fid_to_edid)
    assert assigned.get(0x000011DD) == {0x000011C7}, \
        'the INFO speaker (GetIsID) is who the bare call acts on'

    plan = PackagePlan()
    plan.build(by_type, {0x00000811}, {}, None, assigned)
    # The package hangs off the SPEAKER'S PLACED REF, which is what an alias
    # fills — not the base actor.
    assert plan.quest_packages[0x00000811][0x000011CA] == [0x000011DD]
    assert 0x000011CA in plan.needed_aliases[0x00000811]


def test_commented_out_addscriptpackage_is_not_resurrected():
    """A disabled call must stay disabled.

    The export escapes script newlines/tabs as the LITERAL sequences \r\n and
    \t, so a `;` comment runs to the escaped newline rather than a real one.
    Scanning the raw text treats every commented-out call as live — Nehrim's
    StartCelleTrigZonePlayerStoryvar01SCRIPT carries
    `;\tCelebroRef.AddScriptPackage, ...`, and attaching it would resurrect
    content the author deliberately cut. The escaped `\t` also has to be
    resolved or the ref name is captured as `tCelebroRef` and resolves to
    nothing.
    """
    from tes5_import.packages.aliases import build_script_assigned_packages

    by_type = {'SCPT': [{
        'FormID': '00001111', 'EditorID': 'S',
        'SCTX': 'scn S\r\n;\tCelebroRef.AddScriptPackage, Pkg\r\n'
                '\tCelebroRef.AddScriptPackage, LivePkg\r\n',
    }]}
    fid_to_edid = {0x00000D91: 'CelebroRef', 0x00000E9D: 'Pkg',
                   0x00000E9E: 'LivePkg'}
    assigned = build_script_assigned_packages(by_type, fid_to_edid)
    assert 0x00000E9D not in assigned, 'commented-out call was resurrected'
    assert assigned.get(0x00000E9E) == {0x00000D91}, \
        'the live call on the next line must still resolve (tab-stripped)'


def test_cross_cell_follow_stays_follow_not_escort():
    """A TES4 Follow whose PLDT is in ANOTHER cell must stay Follow.

    A Follow carrying a destination is rerouted to Skyrim's Escort so the
    package can ARRIVE and fire OnPackageEnd (CGEmperorToMarkerB ends
    CharacterGen stage 16). But Escort makes the DESTINATION the goal, and
    there is no navmesh route between two interiors — so a cross-cell
    destination leaves the actor with no path and he never moves at all.

    Verified live on Nehrim MQ00 (2026-08-17): Celebro stands in StartCelle,
    MQ00CelebroPosition01 is in SchattenrufMinePart01, and he moved 5 units in
    6 seconds with the alias filled, condition passing, SpeedMult 100 and
    Paralysis 0.

    The split is authored: same-cell destinations are the arrival-driven ones
    (9 in Oblivion); cross-cell ones are "follow me elsewhere" quests by name
    (MQ16MartinFollowPCToPalace, MG01ErthorFollowPlayer, FGD07AjumFollow).
    """
    from tes5_import.packages.converter import _choose, PackContext, T4_FOLLOW
    from tes5_import.packages.templates import ESCORT, FOLLOW

    rec = {'Signature': 'PACK', 'FormID': '00000E9D',
           'EditorID': 'MQ00CalebroPackage02', 'PKDT.Type': str(T4_FOLLOW),
           'PKDT.Flags': '8192', 'PLDT.Type': '0',
           'PLDT.Location': '000010D1', 'PLDT.Radius': '0',
           'PTDT.Type': '0', 'PTDT.Target': '00000014', 'PTDT.Count': '200'}

    cross = PackContext(ref_cell={0x0010D1: 0x000F21},
                        pack_runner_cells={0x000E9D: {0x000B9B}})
    assert _choose(rec, cross, 0x00000E9D).t is FOLLOW, \
        'cross-cell Escort has no navmesh route; the actor never moves'

    # Same cell keeps the arrival behaviour the reroute exists for.
    same = PackContext(ref_cell={0x0010D1: 0x000B9B},
                       pack_runner_cells={0x000E9D: {0x000B9B}})
    assert _choose(rec, same, 0x00000E9D).t is ESCORT

    # An unknown cell must NOT silently downgrade a package that was fine.
    assert _choose(rec, PackContext(), 0x00000E9D).t is ESCORT


def test_hunt_at_actor_base_becomes_a_follow_chain_nearest_first():
    """A Find at an actor BASE with several placements is a CHAIN of Follow
    packages (one per placed target, nearest the hunter first), each gated on
    the target being in the hunter's cell, enabled and alive, followed by the
    source package as the tail.

    Measured live 2026-08-18 on FGC06: with a wander-only in-cell Sandbox all
    three fighters were RUNNING their hunt package (getiscurrentpackage) and
    stayed within ~300 units of spawn; a PLDT type-4 'Object ID' location
    patched into the live package left them standing.  Skyrim seeks a
    REFERENCE (Follow) — so the chain does the seeking, and the engine walks
    it as each target dies.
    """
    from tes5_import.packages.converter import (hunt_chain_targets, PackContext,
                                            convert_PACK_records,
                                            T4_FIND, CTDA_GET_DEAD,
                                            CTDA_GET_DISABLED,
                                            CTDA_GET_IN_SAME_CELL)
    from tes5_import.packages.templates import FOLLOW, SANDBOX
    from tes5_import.base.text_reader import set_formid_index_offset
    set_formid_index_offset(1)
    try:
        rec = {'Signature': 'PACK', 'FormID': '000292CD',
               'EditorID': 'FGC06RiennaGoblinHunt', 'RecordFlags': '0',
               'PKDT.Type': str(T4_FIND), 'PKDT.Flags': '4096',
               'PTDT.Type': '1', 'PTDT.Target': '0002888E',
               'PTDT.Count': '10', 'ConditionCount': '1',
               # GetStage(FGC06Courier) >= 30
               'Condition[0].Raw': '600000000000f0413a0000008e7f0200'
                                   '0000000000000000'}
        far, near, mid = 0x01048F45, 0x010288A4, 0x01048F41
        ctx = PackContext(
            base_sig={0x02888E: 'CREA'},
            base_placements={0x02888E: ((far, 0x028869), (near, 0x028869),
                                        (mid, 0x028869))},
            interior_cells={0x028869},
            pack_runner_refs={0x0292CD: {0x0288AA}},
            actor_pos={0x0288AA: (1295.0, 7.0, 2.0),
                       0x048F45: (3378.0, 1143.0, -345.0),
                       0x0288A4: (2560.0, 224.0, -352.0),
                       0x048F41: (2552.0, 400.0, -341.0)},
            pack_runner_cells={0x0292CD: {0x028869}})
        assert hunt_chain_targets(rec, ctx, 0x010292CD) == [near, mid, far], \
            'nearest to the hunter first'

        ctx.hunt_chains = {0x010292CD: [(0x01F00001, near), (0x01F00002, mid),
                                        (0x01F00003, far)]}
        recs = convert_PACK_records(rec, ctx)
        assert len(recs) == 4, 'three seek links + the source tail'
        # Every link is a Follow of its target under the source's gates plus
        # the three target gates.
        for k, (b, ref) in enumerate(zip(recs[:3], (near, mid, far)), 1):
            subs = _subrecords(b)
            assert struct.unpack('<I', b[12:16])[0] == 0x01F00000 + k
            assert _first(subs, 'EDID').rstrip(b'\0') == \
                f'FGC06RiennaGoblinHuntSeek{k:02d}'.encode()
            pkcu = struct.unpack('<III', _first(subs, 'PKCU'))
            assert pkcu[1] == FOLLOW.formid
            ctdas = [d for s, d in subs if s == 'CTDA']
            funcs = [struct.unpack_from('<H', d, 8)[0] for d in ctdas]
            assert funcs[0] == 58, 'the source GetStage gate is kept'
            assert funcs[1:] == [CTDA_GET_IN_SAME_CELL, CTDA_GET_DISABLED,
                                 CTDA_GET_DEAD]
            # GetInSameCell(target) runs on the hunter; the other two run ON
            # the target reference (RunOn 2 + reference).
            assert struct.unpack_from('<I', ctdas[1], 12)[0] == ref
            for d in ctdas[2:]:
                run_on, reference = struct.unpack_from('<II', d, 20)
                assert (run_on, reference) == (2, ref)
                assert struct.unpack_from('<f', d, 4)[0] == 0.0
            ptda = struct.unpack('<iIi', _first(subs, 'PTDA'))
            assert ptda == (0, ref, 0), 'Follow THIS placed goblin'
        tail = _subrecords(recs[3])
        assert struct.unpack('<III', _first(tail, 'PKCU'))[1] == SANDBOX.formid
    finally:
        set_formid_index_offset(0)


def test_hunt_chain_runs_ahead_of_its_source_on_alias_and_pkid_lists():
    from tes5_import.packages.actor_wiring import (npc_packages, set_package_chains,
                                      set_quest_packages)
    plan = PackagePlan()
    plan.owner_quest[0x0100AAAA] = 0x01000900
    plan.quest_packages[0x01000900] = {0x01000BB1: [0x0100AAAA, 0x0100AAAB]}
    plan.expand_packages({0x0100AAAA: [0x0100C001, 0x0100C002]})
    assert plan.quest_packages[0x01000900][0x01000BB1] == \
        [0x0100C001, 0x0100C002, 0x0100AAAA, 0x0100AAAB]
    assert plan.owner_quest[0x0100C001] == 0x01000900

    set_quest_packages(())
    set_package_chains({0x0100DDDD: [0x0100E001]})
    try:
        assert npc_packages([0x0100DDDD, 0x0100DDDE]) == \
            [0x0100E001, 0x0100DDDD, 0x0100DDDE]
    finally:
        set_package_chains({})
