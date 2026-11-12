"""`playgroup` on a ref VARIABLE is traced to what the variable holds.

A variable is no EditorID, so its base signature reads '' and the call took
the actor event: `set targetref to getParentRef` /
`targetref.playgroup forward 0` became
`Debug.SendAnimationEvent(targetref, "moveStart")`, an event for an actor's
behavior graph, even when the enable parent is an activator.

The variable takes PlayAnimation only on proof: one traceable source
(`getSelf`, `getParentRef`), assigned by no other script, whose every owner
resolves to the same animated-object base.  Anything short of that keeps the
inert event, because PlayAnimation on an actor corrupts its behavior graph.
See: docs/commentary/script_convert.md#playgroup-variable-target
"""

from script_convert.converter import ScriptConverter
from script_convert.cross_ref import (
    CrossRefGraph, _new_scan_out, _scan_record_lines, _script_texts,
)
from tes5_import.pipeline import _index_xref_record

PARENT_SRC = ('scn T\nref targetref\nbegin onActivate\n'
              '  set targetref to getParentRef\n'
              '  targetref.playgroup forward 0\nend\n')
SELF_SRC = ('scn T\nref selfRef\nbegin onActivate\n'
            '  set selfRef to getSelf\n'
            '  selfRef.playgroup backward 0\nend\n')


def _attach(x, script, slot, owner_sig, parents):
    """Put `script` on one new `owner_sig` base, placed once per entry of `parents`.

    Each entry is the base signature of that placement's enable parent, or ''
    for a placement that has none.  Placements are named `<script>Ref<slot>_<n>`.
    """
    script_fid = f'{0x00020000 + ord(script):08X}'
    owner = f'{0x00040000 + slot * 0x100:08X}'
    x.script_formid_to_edid[script_fid] = script
    x.record_type[owner] = owner_sig
    x.record_scri[owner] = script_fid
    for i, parent_sig in enumerate(parents):
        ref, parent, parent_base = (
            f'{int(owner, 16) + 1 + i * 3 + k:08X}' for k in range(3))
        x.edid_to_formid[f'{script}Ref{slot}_{i}'.lower()] = ref
        x.record_type[ref] = 'REFR'
        x.record_base[ref] = owner
        if parent_sig:
            x.record_parent[ref] = parent
            x.record_type[parent] = 'REFR'
            x.record_base[parent] = parent_base
            x.record_type[parent_base] = parent_sig


def _xref(owner_sig='ACTI', parents=('ACTI',), more_owners=()):
    """Script T on one base (plus one per `more_owners` entry), beside an unrelated script U.

    U sits on an NPC whose enable parent is an NPC, so a trace that strays to
    another script's records reads an actor.
    """
    x = CrossRefGraph()
    _attach(x, 'U', 0, 'NPC_', ('NPC_',))
    _attach(x, 'T', 1, owner_sig, parents)
    for n, extra in enumerate(more_owners):
        _attach(x, 'T', 2 + n, owner_sig, extra)
    return x


def _convert(src, xref, extends='ObjectReference'):
    """Convert `src` as the standalone script T."""
    return ScriptConverter(xref).convert_standalone('T', src, extends, 'T')


def _assert_inert(out):
    """The call kept the graph event and did not play a sequence."""
    assert 'SendAnimationEvent' in out, out
    assert 'PlayAnimation' not in out, out


def test_enable_parent_activator_gets_playanimation():
    """The enable parent behind `getParentRef` is an activator: play its sequence."""
    out = _convert(PARENT_SRC, _xref())
    assert 'targetref.PlayAnimation("Forward")' in out, out
    assert 'SendAnimationEvent' not in out, out


def test_every_placement_and_owner_agreeing_still_promotes():
    """Two placements of one base and a second base, every parent an activator."""
    xref = _xref(parents=('ACTI', 'ACTI'), more_owners=(('ACTI',),))
    assert 'targetref.PlayAnimation("Forward")' in _convert(PARENT_SRC, xref)


def test_door_parent_gets_playanimation():
    """A door is an animated object too."""
    out = _convert(PARENT_SRC, _xref(parents=('DOOR',)))
    assert 'targetref.PlayAnimation("Forward")' in out, out


def test_getself_on_an_activator_script_gets_playanimation():
    """`getSelf` is the record the script sits on, here an activator."""
    out = _convert(SELF_SRC, _xref())
    assert 'selfRef.PlayAnimation("Backward")' in out, out
    assert 'SendAnimationEvent' not in out, out


def test_actor_enable_parent_keeps_the_event():
    """A traced ACTOR is still an actor, a leveled spawn included."""
    for sig in ('NPC_', 'CREA', 'LVLC', 'LVLN'):
        out = _convert(PARENT_SRC, _xref(parents=(sig,)))
        assert 'Debug.SendAnimationEvent(targetref, "moveStart")' in out, (sig, out)
        assert 'PlayAnimation' not in out, (sig, out)


def test_getself_on_an_actor_script_keeps_the_event():
    """`getSelf` on an NPC's script is that NPC."""
    _assert_inert(_convert(SELF_SRC, _xref(owner_sig='NPC_'), 'Actor'))


def test_only_an_animated_object_base_is_proof():
    """A quest or a weapon is no actor, and no animated object either."""
    for sig, extends in (('QUST', 'Quest'), ('WEAP', 'ObjectReference')):
        out = _convert(SELF_SRC, _xref(owner_sig=sig), extends)
        assert 'PlayAnimation' not in out, (sig, out)
        assert 'SendAnimationEvent' in out, (sig, out)


def test_placement_without_a_parent_keeps_the_event():
    """One placement with no enable parent leaves the target unproven."""
    _assert_inert(_convert(PARENT_SRC, _xref(parents=('ACTI', ''))))


def test_unplaced_owner_keeps_the_event():
    """An owner placed nowhere proves nothing, whatever the other owner says."""
    _assert_inert(_convert(PARENT_SRC, _xref(parents=())))
    _assert_inert(_convert(PARENT_SRC, _xref(more_owners=((),))))


def test_unattached_script_keeps_the_event():
    """A script no record carries has no self and no parent to trace."""
    x = CrossRefGraph()
    _attach(x, 'U', 0, 'ACTI', ('ACTI',))
    _assert_inert(_convert(PARENT_SRC, x))
    _assert_inert(_convert(SELF_SRC, x))


def test_disagreeing_parents_keep_the_event():
    """One script, two placements, an activator parent and a door one."""
    _assert_inert(_convert(PARENT_SRC, _xref(parents=('ACTI', 'DOOR'))))


def test_disagreeing_owners_keep_the_event():
    """`getSelf` on two bases of different kinds names no one signature."""
    x = _xref()
    _attach(x, 'T', 5, 'DOOR', ('ACTI',))
    assert 'selfRef.PlayAnimation' in _convert(SELF_SRC, _xref())
    _assert_inert(_convert(SELF_SRC, x))


def test_two_assignment_sources_keep_the_event():
    """Nothing says which of two assignments the call sees."""
    src = ('scn T\nref targetref\nbegin onActivate\n'
           '  set targetref to getParentRef\n'
           '  set targetref to getSelf\n'
           '  targetref.playgroup forward 0\nend\n')
    _assert_inert(_convert(src, _xref()))


def test_a_named_ref_beside_the_traceable_source_keeps_the_event():
    """`set t to SomeNPCRef` anywhere in the script could be what the call sees."""
    src = ('scn T\nref targetref\nbegin onActivate\n'
           '  set targetref to getParentRef\n'
           '  targetref.playgroup forward 0\nend\n'
           'begin onLoad\n  set targetref to URef0_0\nend\n')
    _assert_inert(_convert(src, _xref()))


def test_another_references_parent_is_not_this_scripts():
    """`other.getParentRef` is the parent of whatever `other` holds."""
    src = ('scn T\nref other\nref targetref\nbegin onActivate\n'
           '  set targetref to other.getParentRef\n'
           '  targetref.playgroup forward 0\nend\n')
    _assert_inert(_convert(src, _xref()))


def test_untraceable_source_keeps_the_event():
    """`getActionRef` is whoever activated it -- unknowable statically."""
    src = ('scn T\nref targetref\nbegin onActivate\n'
           '  set targetref to getActionRef\n'
           '  targetref.playgroup forward 0\nend\n')
    _assert_inert(_convert(src, _xref()))


def test_actor_typed_variable_is_never_promoted():
    """Actor-only use declares the variable Actor, whatever the trace says."""
    src = ('scn T\nref targetref\nbegin onActivate\n'
           '  set targetref to getParentRef\n'
           '  targetref.evaluatePackage\n'
           '  targetref.playgroup forward 0\nend\n')
    out = _convert(src, _xref())
    assert 'Actor targetref' in out or 'Actor Property targetref' in out, out
    _assert_inert(out)


def test_a_variable_another_script_assigns_keeps_the_event():
    """`set TRef1_0.targetref to X` reaches T through the placed ref's base."""
    xref = _xref()
    xref.index_remote_writes(
        ['begin gamemode\n\tSet TRef1_0.targetref To URef0_0 ; hand over\nend'])
    assert xref.remote_writes == {('t', 'targetref')}
    _assert_inert(_convert(PARENT_SRC, xref))


def test_a_write_naming_the_script_itself_keeps_the_event():
    """`set T.targetref to X` names the owning script directly."""
    xref = _xref()
    xref.index_remote_writes(['set T.targetref to URef0_0'])
    assert xref.remote_writes == {('t', 'targetref')}
    _assert_inert(_convert(PARENT_SRC, xref))


def test_a_write_through_a_record_carrying_the_script_keeps_the_event():
    """`set TBase.targetref to X` names the record T sits on, as a quest's write does."""
    xref = _xref()
    xref.edid_to_formid['tbase'] = '00040100'
    xref.index_remote_writes(['set TBase.targetref to URef0_0'])
    assert xref.remote_writes == {('t', 'targetref')}
    _assert_inert(_convert(PARENT_SRC, xref))


def test_without_a_graph_the_variable_keeps_the_event():
    """No graph, no proof."""
    _assert_inert(_convert(PARENT_SRC, None))


def test_a_masters_scripts_are_read_for_remote_writes(tmp_path):
    """A master's script can assign a variable of a script the plugin overrides."""
    (tmp_path / 'M.esm').mkdir()
    (tmp_path / 'M.esm' / 'SCPT.txt').write_text(
        'SCTX=scn W\\nset Nowhere.v to 1\n', encoding='utf-8')
    (tmp_path / 'P.esp').mkdir()
    (tmp_path / 'P.esp' / '_HEADER.txt').write_text('Master[0]=M.esm\n', encoding='utf-8')
    assert _script_texts(str(tmp_path / 'P.esp')) == ['scn W\nset Nowhere.v to 1']


def test_a_write_through_an_unknown_owner_vetoes_the_name():
    """An owner no script is found for could be this script's: refuse."""
    xref = _xref()
    xref.index_remote_writes(['let Nowhere.targetref := URef0_0'])
    assert xref.remote_write_names == {'targetref'}
    _assert_inert(_convert(PARENT_SRC, xref))


def test_another_scripts_same_named_variable_does_not_veto():
    """U's `targetref` is not T's; a commented-out write is no write."""
    xref = _xref()
    xref.index_remote_writes(['set URef0_0.targetref to player\n'
                              '; set TRef1_0.targetref to player\n'
                              'set U.targetref to player'])
    assert xref.remote_writes == {('u', 'targetref')}
    assert not xref.remote_write_names
    assert 'targetref.PlayAnimation("Forward")' in _convert(PARENT_SRC, xref)


def test_every_script_source_is_read_for_remote_writes(tmp_path):
    """Scripts, dialogue results and stage results all assign other scripts' variables."""
    (tmp_path / 'SCPT.txt').write_text(
        'EditorID=S\nSCTX=scn S\\nset E.v to 5\n', encoding='utf-8')
    (tmp_path / 'INFO.txt').write_text(
        'Signature=INFO\nResultScript=set A.x to 1\\r\\n\\tset B.y to 2\n'
        'ResultScriptEnd=set C.z to 3\n', encoding='utf-8')
    (tmp_path / 'QUST.txt').write_text(
        'Stage[3].Log[0].ResultScript=set D.w to 4\n', encoding='utf-8')
    assert _script_texts(str(tmp_path)) == [
        'scn S\nset E.v to 5',
        'set A.x to 1\r\n\tset B.y to 2', 'set C.z to 3', 'set D.w to 4']


def test_enable_parent_is_indexed_from_the_export():
    """XESP.Reference of a placed ref lands in `record_parent`, re-keyed for a master."""
    out = _new_scan_out()
    _scan_record_lines('REFR', [
        'Signature=REFR', 'FormID=03020C8C', 'NAME=000228A9',
        'XESP.Reference=03020C8F', 'XESP.Flags=0'], out)
    _scan_record_lines('ACTI', [
        'Signature=ACTI', 'FormID=000228A9', 'XESP.Reference=03020C8F'], out)
    _scan_record_lines('REFR', [
        'Signature=REFR', 'FormID=01000AAA', 'NAME=000228A9',
        'XESP.Reference=01000BBB'], out, remap={0: 0, 1: 2})
    x = CrossRefGraph()
    x._merge_scan(out)
    assert x.record_parent == {'03020C8C': '03020C8F', '02000AAA': '02000BBB'}


def test_the_importers_graph_indexes_the_enable_parent_too():
    """Both graph builders must agree, or the two conversion passes diverge."""
    x = CrossRefGraph()
    _index_xref_record(
        x, '02000AAA',
        {'Signature': 'REFR', 'FormID': '01000AAA', 'NAME': '000228A9',
         'XESP.Reference': '01000BBB'},
        lambda v, own_raw, own_key: v.replace('01', '02', 1))
    assert x.record_parent == {'02000AAA': '02000BBB'}


def test_a_named_actor_or_leveled_list_keeps_the_event():
    """A leveled list places an actor, so a target whose base is one is an actor."""
    for sig in ('NPC_', 'CREA', 'LVLC', 'LVLN', 'ACHR', 'ACRE'):
        x = CrossRefGraph()
        x.edid_to_formid['spawnref'] = '00010000'
        x.record_type['00010000'] = sig
        if sig not in ('ACHR', 'ACRE'):
            x.record_type['00010000'] = 'ACRE'
            x.record_base['00010000'] = '00010001'
            x.record_type['00010001'] = sig
        out = '\n'.join(ScriptConverter(x).convert_fragment(
            'SpawnRef.playgroup forward 1', 'ObjectReference'))
        assert 'SendAnimationEvent' in out, (sig, out)
        assert 'PlayAnimation' not in out, (sig, out)


def test_every_placeable_sequence_object_kind_is_proof():
    """Each placeable object kind whose mesh can carry a sequence takes PlayAnimation."""
    for sig in ('ACTI', 'DOOR', 'CONT', 'FLOR', 'FURN', 'LIGH', 'MISC', 'STAT'):
        out = _convert(SELF_SRC, _xref(owner_sig=sig))
        assert 'selfRef.PlayAnimation("Backward")' in out, (sig, out)


def _record(fields):
    """One export record in the text exporter's delimiters."""
    return '---RECORD_BEGIN---\n' + '\n'.join(fields) + '\n---RECORD_END---\n\n'


def _write_export(folder, writer=''):
    """A one-gate export: script T on a switch whose enable parent is an activator.

    `writer` is the source of a second script, which may assign T's variable.
    """
    sctx = PARENT_SRC.replace('\n', '\\r\\n')
    scripts = _record(['Signature=SCPT', 'FormID=01000800', 'EditorID=T',
                       'SCHR.Type=0', 'SCTX=' + sctx])
    if writer:
        scripts += _record(['Signature=SCPT', 'FormID=01000801', 'EditorID=W',
                            'SCHR.Type=0', 'SCTX=' + writer])
    (folder / 'SCPT.txt').write_text(scripts, encoding='utf-8')
    (folder / 'ACTI.txt').write_text(
        _record(['Signature=ACTI', 'FormID=01000900', 'EditorID=TSwitch',
                 'SCRI=01000800'])
        + _record(['Signature=ACTI', 'FormID=01000901', 'EditorID=TGate']),
        encoding='utf-8')
    (folder / 'REFR.txt').write_text(
        _record(['Signature=REFR', 'FormID=01000A00', 'EditorID=TSwitchRef',
                 'NAME=01000900', 'XESP.Reference=01000A01', 'XESP.Flags=0'])
        + _record(['Signature=REFR', 'FormID=01000A01', 'EditorID=TGateRef',
                   'NAME=01000901']),
        encoding='utf-8')


def test_the_script_stage_graph_proves_the_gate(tmp_path):
    """The graph the script stage builds from an export traces the switch's parent."""
    from script_convert.context_setup import build_xref
    _write_export(tmp_path)
    out = _convert(PARENT_SRC, build_xref(str(tmp_path)))
    assert 'targetref.PlayAnimation("Forward")' in out, out


def test_the_script_stage_graph_reads_another_scripts_write(tmp_path):
    """A second script handing the switch's variable another target vetoes the trace."""
    from script_convert.context_setup import build_xref
    _write_export(tmp_path, 'scn W\\r\\nbegin gamemode\\r\\n'
                            '  set TSwitchRef.targetref to player\\r\\nend')
    xref = build_xref(str(tmp_path))
    assert xref.remote_writes == {('t', 'targetref')}
    _assert_inert(_convert(PARENT_SRC, xref))


def test_the_importers_graph_reads_remote_writes_too(tmp_path):
    """The importer's hand-built graph indexes the same writes as the script stage's."""
    from tes5_import.pipeline import _prescan_cross_ref_graph
    _write_export(tmp_path, 'scn W\\r\\nset Nowhere.targetref to player')
    xref = _prescan_cross_ref_graph([], None, str(tmp_path), lambda *_: None)
    assert xref.remote_write_names == {'targetref'}


def test_a_foreach_iterator_keeps_the_event():
    """An OBSE `forEach` header assigns its iterator too, so one source is no proof."""
    src = ('scn T\nref targetref\nbegin onActivate\n'
           '  set targetref to getParentRef\n'
           '  foreach targetref <- player\n'
           '    targetref.playgroup forward 0\n'
           '  loop\nend\n')
    _assert_inert(_convert(src, _xref()))


def test_a_foreach_iterator_is_matched_in_any_case():
    """TES4 names are case-blind: `forEach TargetRef` is the iterator `targetref`."""
    src = ('scn T\nref targetref\nbegin onActivate\n'
           '  set targetref to getParentRef\n'
           '  foreach TargetRef <- player\n'
           '    targetref.playgroup forward 0\n'
           '  loop\nend\n')
    _assert_inert(_convert(src, _xref()))


def test_a_foreach_over_no_name_leaves_other_variables_traced():
    """A header whose iterator is not a name assigns no variable of the script."""
    src = ('scn T\nref targetref\nbegin onActivate\n'
           '  set targetref to getParentRef\n'
           '  foreach 5 <- player\n'
           '  loop\n'
           '  targetref.playgroup forward 0\nend\n')
    assert 'targetref.PlayAnimation("Forward")' in _convert(src, _xref())
