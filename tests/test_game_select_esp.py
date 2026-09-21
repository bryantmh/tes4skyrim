"""Structural tests for the TESGameSelect starter plugin (tools/release/make_game_select_esp.py).

These lock the two contracts that are invisible in-game until they are already
broken: the MESG button/condition layout (a wrong one silently starts the wrong
game) and the .seq listing (without it the Start-Game-Enabled quest never runs
and the menu simply never appears).
"""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.release.make_game_select_esp import (BUTTONS, FID_MESG, FID_QUST, FID_MQ101,
                                        GLOBALS,
                                        QUEST_TYPE_NONE, FID_GAMEHOUR,
                                        FID_HOLDING_CELL_MARKER,
                                        MQ101_TAKEOVER_STAGE,
                                        MQ101_TAKEOVER_LOG_ENTRY,
                                        MQ101_VANILLA_STAGE0_ENTRIES,
                                        MQ101_VANILLA_FRAGMENT_SCRIPT,
                                        FUNC_GET_GLOBAL_VALUE, SCRIPT_NAME,
                                        MQ101_SCRIPT_NAME, build_plugin,
                                        MESG_VARIANTS, prologue_for,
                                        _skip_script_entry)


def test_every_installed_set_has_a_menu_variant(built):
    """MenuFor() indexes by mask, so all 2**gated variants must exist."""
    _data, _count, recs = built
    for mask in range(MESG_VARIANTS):
        assert ('MESG', FID_MESG + mask) in recs, f'no MESG for mask {mask}'


def test_prologue_names_only_installed_games():
    """A game absent from the mask must not be described; Skyrim's line is
    unconditional and always present.

    Two games may share a line — Morroblivion and vanilla Morrowind are the
    same world, deliberately presented identically — so the invariant is per
    LINE rather than per button: it shows when any game using it is installed,
    and is absent only when none of them are.
    """
    gated = [i for i, b in enumerate(BUTTONS) if b[1] is not None]
    for mask in range(MESG_VARIANTS):
        text = prologue_for(mask)
        assert BUTTONS[0][2] in text, 'Skyrim line is unconditional'
        for idx in gated:
            line = BUTTONS[idx][2]
            users = [b for j, b in enumerate(gated) if BUTTONS[b][2] == line]
            shown = any(mask & (1 << gated.index(u)) for u in users)
            assert (line in text) == shown, (
                f'mask {mask}: {line!r} should '
                f'{"appear" if shown else "be hidden"}')
from tes5_import.base.tes5_reader import records


def _parse(data):
    """{(type, formid): [(subtype, payload), ...]}, TES4 header included."""
    return {(rec.sig.decode('ascii'), rec.form_id):
            [(t.decode('ascii'), d) for t, d in rec.subs()]
            for rec in records(data, span=(0, len(data)))}


@pytest.fixture(scope='module')
def skyrim_esm():
    """The MQ101 override is spliced from the installed Skyrim.esm, so these
    tests need the real game files."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from convert import load_config
    from source_paths import find_game_path
    try:
        cfg = load_config()
    except (FileNotFoundError, OSError):
        cfg = {}
    data_path = find_game_path('skyrimse', cfg)
    if not data_path:
        pytest.skip('Skyrim SE install not found')
    path = os.path.join(data_path, 'Skyrim.esm')
    if not os.path.isfile(path):
        pytest.skip(f'Skyrim.esm not found at {path}')
    return path


@pytest.fixture(scope='module')
def built(skyrim_esm):
    data, count = build_plugin(skyrim_esm)
    return data, count, _parse(data)


def test_header_declares_only_skyrim_master(built):
    """Every foreign form is resolved at runtime via GetFormFromFile, so the
    plugin must NOT master the converted games — mastering one would make the
    plugin fail to load for users who don't have that game installed."""
    data, _count, recs = built
    masters = [p.rstrip(b'\0').decode() for t, p in recs[('TES4', 0)]
               if t == 'MAST']
    assert masters == ['Skyrim.esm']


def test_hedr_count_matches_contents(built):
    """HEDR must count records + GRUPs; the engine walks the file by it, so an
    undercount silently drops records. One GLOB per game, one MESG per
    installed-game set, two QUST (selector + MQ101), three top-level GRUPs."""
    data, count, recs = built
    hedr = dict(recs[('TES4', 0)])['HEDR']
    assert struct.unpack('<I', hedr[4:8])[0] == count
    assert count == len(GLOBALS) + MESG_VARIANTS + 2 + 3


def test_selector_quest_is_not_start_game_enabled(built):
    """The selector must NOT be Start Game Enabled: it is driven by the MQ101
    stage-0 fragment, which runs exactly once. Relying on OnInit instead is
    what made the menu appear twice, since OnInit fires again on quest restart
    or when the plugin is added to an existing save."""
    _data, _count, recs = built
    dnam = dict(recs[('QUST', FID_QUST)])['DNAM']
    flags, priority, _formver, _unknown, qtype = struct.unpack('<HBBII', dnam)
    assert flags & 0x01 == 0, 'StartGameEnabled must be clear'
    assert qtype == QUEST_TYPE_NONE
    assert priority == 0


def test_seq_is_empty(built, tmp_path):
    """No SGE quests means the .seq must be empty — a stale non-empty one from
    an older build would re-enable the OnInit path and double the menu."""
    from tools.release.make_game_select_esp import write_seq
    path = write_seq(str(tmp_path))
    assert os.path.getsize(path) == 0


def test_button_order_matches_game_ids(built):
    """Buttons must be declared in GAME_* order: a hidden button does not
    renumber the rest, so Show()'s return value IS the game id. Reordering
    these without editing the script starts the wrong game.

    The expectation is derived from BUTTONS rather than frozen as a snapshot:
    the contract is that the record's ITXT order IS the declared order, not
    that a given game sits at a given index. Index 0 is the one fixed point —
    it is the unconditional button.
    """
    _data, _count, recs = built
    subs = recs[('MESG', FID_MESG)]
    texts = [p.rstrip(b'\0').decode() for t, p in subs if t == 'ITXT']
    assert texts == [b[0] for b in BUTTONS]
    assert len(texts) == len(BUTTONS) == 6
    assert BUTTONS[0][1] is None
    assert texts[0].startswith('Skyrim')


def test_skyrim_button_is_unconditional_others_are_gated(built):
    """Skyrim's button carries no condition so the menu can never be drawn
    empty (vanilla dunMiddenNamesMenuMSG does the same with its final button);
    each converted game's button is gated on its own global."""
    _data, _count, recs = built
    subs = recs[('MESG', FID_MESG)]

    # Walk ITXT/CTDA in order, attributing each CTDA to the ITXT before it.
    conds = {}
    idx = -1
    for stype, payload in subs:
        if stype == 'ITXT':
            idx += 1
        elif stype == 'CTDA':
            func = struct.unpack_from('<H', payload, 8)[0]
            param1 = struct.unpack_from('<I', payload, 12)[0]
            comp = struct.unpack_from('<f', payload, 4)[0]
            assert func == FUNC_GET_GLOBAL_VALUE
            assert comp == 1.0
            conds.setdefault(idx, []).append(param1)

    assert 0 not in conds, 'Skyrim button must stay unconditional'
    for idx, button in enumerate(BUTTONS):
        gate = button[1]
        if gate is None:
            assert idx not in conds
        else:
            assert conds[idx] == [gate]
    assert sorted(g for gs in conds.values() for g in gs) == sorted(
        b[1] for b in BUTTONS if b[1] is not None)


def test_mesg_is_a_message_box(built):
    """DNAM bit 0 = Message Box. Without it the record renders as a corner
    notification with no buttons and Show() can never return a choice."""
    _data, _count, recs = built
    dnam = dict(recs[('MESG', FID_MESG)])['DNAM']
    assert struct.unpack('<I', dnam)[0] & 0x01


def test_vmad_binds_every_script_property(built):
    """The VMAD property names must match the .psc exactly — a typo binds
    silently to nothing and the menu never appears."""
    _data, _count, recs = built
    vmad = dict(recs[('QUST', FID_QUST)])['VMAD']
    version, obj_format, script_count = struct.unpack_from('<HHH', vmad, 0)
    assert (version, obj_format, script_count) == (5, 2, 1)

    pos = 6
    name_len = struct.unpack_from('<H', vmad, pos)[0]
    name = vmad[pos + 2:pos + 2 + name_len].decode()
    assert name == SCRIPT_NAME
    pos += 2 + name_len + 1                       # + flags byte
    prop_count = struct.unpack_from('<H', vmad, pos)[0]
    pos += 2

    props = {}
    for _ in range(prop_count):
        plen = struct.unpack_from('<H', vmad, pos)[0]
        pname = vmad[pos + 2:pos + 2 + plen].decode()
        pos += 2 + plen
        ptype = vmad[pos]
        pos += 2                                  # type + status
        assert ptype == 1, 'all properties are Object-typed'
        _unused, alias, fid = struct.unpack_from('<HhI', vmad, pos)
        pos += 8
        assert alias == -1
        props[pname] = fid

    expected = {edid.replace('TESGS_', ''): fid for fid, edid in GLOBALS}
    for mask in range(MESG_VARIANTS):
        expected[f'Menu{mask:02d}'] = FID_MESG + mask
    assert props == expected
    assert pos == len(vmad), 'VMAD must be fully consumed'


def test_globals_are_short_typed_and_zeroed(built):
    """Short-typed, value 0: the script sets them during detection, and a
    stale 1 from authoring would offer a game that is not installed."""
    _data, _count, recs = built
    for fid, edid in GLOBALS:
        subs = dict(recs[('GLOB', fid)])
        assert subs['EDID'].rstrip(b'\0').decode() == edid
        assert subs['FNAM'] == b's'
        assert struct.unpack('<f', subs['FLTV'])[0] == 0.0


def _decode_quest_vmad(vmad):
    """(script_names, fragments, alias_count) from a QUST VMAD."""
    _version, _fmt, script_count = struct.unpack_from('<HHH', vmad, 0)
    pos = 6
    names = []
    for _ in range(script_count):
        nlen = struct.unpack_from('<H', vmad, pos)[0]
        names.append(vmad[pos + 2:pos + 2 + nlen].decode())
        pos = _skip_script_entry(vmad, pos)

    pos += 1                                        # extra bind version
    frag_count = struct.unpack_from('<H', vmad, pos)[0]
    pos += 2
    pos += 2 + struct.unpack_from('<H', vmad, pos)[0]   # FileName

    frags = []
    for _ in range(frag_count):
        stage, log = struct.unpack_from('<II', vmad, pos)
        pos += 9
        slen = struct.unpack_from('<H', vmad, pos)[0]
        script = vmad[pos + 2:pos + 2 + slen].decode()
        pos += 2 + slen
        flen = struct.unpack_from('<H', vmad, pos)[0]
        frag = vmad[pos + 2:pos + 2 + flen].decode()
        pos += 2 + flen
        frags.append((stage, log, script, frag))

    alias_count = struct.unpack_from('<h', vmad, pos)[0]
    return names, frags, alias_count


def _vanilla_mq101(skyrim_esm):
    from tools.esm.tes5_esm_reader import read_tes5_file
    _hdr, recs, _loc = read_tes5_file(skyrim_esm)
    return next(r for r in recs if r.type == 'QUST' and r.form_id == FID_MQ101)


def test_mq101_override_retargets_stage0_without_losing_vanilla(built, skyrim_esm):
    """The override must be vanilla MQ101 plus one appended script, with
    exactly ONE fragment retargeted: stage 0 / log 0 — the real new-game path
    (its QSDT keeps the MQQuickstart == 0 condition) — now runs RunTakeover
    instead of Fragment_2. Every other fragment and all 54 aliases must
    survive byte-identical: an APPENDED extra entry (the previous design) ran
    ALONGSIDE Fragment_2, so the cart, title credits and stage 10 still fired
    on top of the menu."""
    _data, _count, recs = built
    ours = dict(recs[('QUST', FID_MQ101)])['VMAD']
    vanilla = next(s.data for s in _vanilla_mq101(skyrim_esm).subrecords
                   if s.type == 'VMAD')

    v_names, v_frags, v_aliases = _decode_quest_vmad(vanilla)
    o_names, o_frags, o_aliases = _decode_quest_vmad(ours)

    assert o_names == v_names + [MQ101_SCRIPT_NAME]
    assert len(o_frags) == len(v_frags), 'no fragment entries added or lost'

    key = (MQ101_TAKEOVER_STAGE, MQ101_TAKEOVER_LOG_ENTRY)
    v_by_key = {(s, l): (sc, fn) for s, l, sc, fn in v_frags}
    o_by_key = {(s, l): (sc, fn) for s, l, sc, fn in o_frags}
    assert v_by_key[key] == (MQ101_VANILLA_FRAGMENT_SCRIPT, 'Fragment_2')
    assert o_by_key[key] == (MQ101_SCRIPT_NAME, 'RunTakeover')
    for k in v_by_key:
        if k != key:
            assert o_by_key[k] == v_by_key[k], f'fragment {k} must be intact'
    assert o_aliases == v_aliases, 'all MQ101 aliases must survive'


def test_mq101_override_keeps_stage_log_entries_identical(built, skyrim_esm):
    """No log entry is added or removed anywhere: the takeover reuses vanilla
    stage 0 / entry 0 (and with it the MQQuickstart == 0 condition that keeps
    debug quickstarts on their own fragments)."""
    _data, _count, recs = built

    def stage_entry_counts(subs):
        counts, current = {}, None
        for stype, payload in subs:
            if stype == 'INDX':
                current = struct.unpack_from('<H', payload, 0)[0]
                counts.setdefault(current, 0)
            elif stype == 'QSDT' and current is not None:
                counts[current] += 1
        return counts

    ours = stage_entry_counts(recs[('QUST', FID_MQ101)])
    vanilla = stage_entry_counts(
        [(s.type, s.data) for s in _vanilla_mq101(skyrim_esm).subrecords])

    assert vanilla[MQ101_TAKEOVER_STAGE] == MQ101_VANILLA_STAGE0_ENTRIES
    assert ours == vanilla, 'stage log entries must be untouched'


def test_mq101_takeover_script_properties_bound(built):
    """The takeover needs its selector, holding-cell marker and GameHour
    bound — GameHour because choosing Skyrim replays vanilla Fragment_2
    (`GameHour.SetValue(7); SetStage(10)`) verbatim."""
    _data, _count, recs = built
    vmad = dict(recs[('QUST', FID_MQ101)])['VMAD']
    _version, _fmt, script_count = struct.unpack_from('<HHH', vmad, 0)

    pos = 6
    props = None
    for _ in range(script_count):
        nlen = struct.unpack_from('<H', vmad, pos)[0]
        name = vmad[pos + 2:pos + 2 + nlen].decode()
        entry_start = pos
        pos = _skip_script_entry(vmad, pos)
        if name != MQ101_SCRIPT_NAME:
            continue
        p = entry_start + 2 + nlen + 1
        count = struct.unpack_from('<H', vmad, p)[0]
        p += 2
        props = {}
        for _ in range(count):
            plen = struct.unpack_from('<H', vmad, p)[0]
            pname = vmad[p + 2:p + 2 + plen].decode()
            p += 2 + plen
            assert vmad[p] == 1, 'all takeover properties are Object-typed'
            p += 2
            _unused, alias, fid = struct.unpack_from('<HhI', vmad, p)
            p += 8
            assert alias == -1
            props[pname] = fid

    assert props == {
        'Selector': FID_QUST,
        'HoldingCellMarker': FID_HOLDING_CELL_MARKER,
        'GameHour': FID_GAMEHOUR,
    }


def test_script_source_declares_matching_game_constants():
    """The .psc GAME_* ids must line up with the MESG button order; they are
    the mapping from a clicked button to the game that gets started.

    The button's index IS the game id, so a game's .psc constant must equal its
    position in BUTTONS. Deriving that here means a reorder in one file without
    the other fails loudly instead of silently launching the wrong game.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    psc = os.path.join(root, 'TESGameSelect', 'scripts', 'source',
                       SCRIPT_NAME + '.psc')
    text = open(psc, encoding='utf-8').read()

    glob_to_const = {fid: 'GAME_' + edid.replace('TESGS_Has', '').upper()
                     for fid, edid in GLOBALS}
    glob_to_const[None] = 'GAME_SKYRIM'
    expected = {glob_to_const[b[1]]: idx for idx, b in enumerate(BUTTONS)}
    assert len(expected) == len(BUTTONS) == 6

    for name, value in sorted(expected.items(), key=lambda kv: kv[1]):
        assert f'Property {name}' in text
        line = next(ln for ln in text.splitlines() if f'Property {name}' in ln)
        assert f'= {value}' in line, (
            f'{name} must equal {value} -- it is button {value} in '
            f'make_game_select_esp.BUTTONS, and Show() returns that index')


def test_morrowind_chargen_global_matches_the_conversion():
    """Vanilla Morrowind is started by setting its CharGenState GLOB to 1, so
    the .psc must name the FormID the conversion actually gives that record.

    Unlike every other game there is no chargen quest to SetStage -- the
    opening is object scripts polling this one global.
    See: docs/commentary/morrowind_runtime.md#vanilla-morrowind-chargen
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    psc = os.path.join(root, 'TESGameSelect', 'scripts', 'source',
                       SCRIPT_NAME + '.psc')
    text = open(psc, encoding='utf-8').read()

    line = next((ln for ln in text.splitlines()
                 if 'MorrowindChargenStateID' in ln and 'Property' in ln), None)
    assert line, 'the .psc must declare MorrowindChargenStateID'
    assert 'SetValue(1.0)' in text, (
        'BeginMorrowind must set CharGenState to 1 -- that IS the start')


def test_no_two_records_share_a_formid(built):
    """Every record in the file must have its own FormID.

    The MESG block is 2**gated CONSECUTIVE ids and GROWS when a gated game is
    added -- going from four gated games to five doubled it from 16 to 32 and
    ran it straight through FID_QUST, so the selector quest and menu variant 16
    were the same record and the plugin was broken outright.
    """
    _data, _count, recs = built
    ids = [fid for _sig, fid in recs if fid]
    assert len(ids) == len(set(ids)), 'duplicate FormID in the plugin'

    block = range(FID_MESG, FID_MESG + MESG_VARIANTS)
    for fid in [FID_QUST] + [g[0] for g in GLOBALS]:
        assert fid not in block, (
            f'0x{fid:08X} sits inside the MESG block '
            f'0x{FID_MESG:08X}..0x{FID_MESG + MESG_VARIANTS - 1:08X}')
