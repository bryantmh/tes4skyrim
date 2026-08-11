"""Build TESGameSelect.esp — the "Threads of Prophecy" new-game game selector.

A standalone, redistributable Skyrim SE plugin. On a new game it detects which
converted TES games are present in the load order (Oblivion, Nehrim,
Morroblivion) and offers the player a choice of which game to begin; picking one
hands control to that game's own character generation. Picking Skyrim leaves the
vanilla opening untouched.

Structure (all authored from scratch — no TES4 source):

  GLOB x4   TESGS_HasSkyrim / HasOblivion / HasNehrim / HasMorroblivion.
            Set by the quest script from its detection pass. The three
            converted-game globals each gate one menu button so absent games
            are not offered; TESGS_HasSkyrim gates nothing (Skyrim's button is
            unconditional) and exists so the detection state is inspectable
            in-game with `sqv` / `getglobalvalue` when diagnosing a menu that
            offered the wrong set.
  MESG      TESGSGameSelectMSG — the prompt. All four buttons are declared; the
            three converted-game buttons carry a GetGlobalValue(<its global>)
            == 1 condition, which is how vanilla builds a menu whose buttons
            vary at runtime (dunMiddenNamesMenuMSG uses the same pattern).
  QUST      TESGSGameSelect — Start Game Enabled, script-only (no stages, no
            aliases), carrying the VMAD that attaches TESGameSelectQuest.psc
            with its Message/GlobalVariable properties bound.

Only Skyrim.esm is a master: every foreign form is resolved at runtime with
Game.GetFormFromFile(), so the plugin loads with any subset of the games
installed, in any order.

Usage:
  python tools/make_game_select_esp.py                    # -> output/TESGameSelect/
  python tools/make_game_select_esp.py --outdir some/dir
  python tools/make_game_select_esp.py --no-compile       # skip Papyrus compile
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from subprocess_flags import windows_cmd  # noqa: E402

from tes5_import.writer import (pack_record, pack_subrecord, pack_tes4_header,
                                pack_top_group, pack_string_subrecord,
                                pack_formid_subrecord, pack_uint32_subrecord,
                                _count_records_and_groups)
from tes5_import.dialog_conditions import build_ctda
from script_convert.pipeline import build_vmad_object_script

PLUGIN_NAME = 'TESGameSelect.esp'
SCRIPT_NAME = 'TESGameSelectQuest'
MQ101_SCRIPT_NAME = 'TESGameSelectMQ101'

# This plugin's own FormIDs (mod index is assigned by load order at runtime;
# 0x01 here is the placeholder the engine rewrites, matching how every ESP is
# authored — the low 24 bits are what identify the record).
FID_GLOB_SKYRIM       = 0x01000800
FID_GLOB_OBLIVION     = 0x01000801
FID_GLOB_NEHRIM       = 0x01000802
FID_GLOB_MORROBLIVION = 0x01000803
FID_MESG              = 0x01000810
FID_QUST              = 0x01000820

# --- Vanilla Skyrim.esm forms we override or reference -----------------------
# MQ101 "Unbound", the opening. We OVERRIDE this record: the only point where a
# new game can be diverted cleanly is its stage-0 fragment, which runs before
# the cart ride starts. A separate quest that stops MQ101 afterwards leaves the
# player bound and mid-scene (see TESGameSelectMQ101.psc).
FID_MQ101 = 0x0003372B
# Skyrim's own empty interior holding cell and the XMarker inside it. Reusing
# these means no cell authoring, and the player waits somewhere genuinely blank
# instead of inside the moving cart.
FID_HOLDING_CELL_MARKER = 0x001037F2   # WIDeadBodyCleanupCellMarker
FID_GAMEHOUR            = 0x00000038   # GameHour (Skyrim.esm GLOB)

# The fragment the takeover REPLACES: stage 0, log entry 0 — vanilla points it
# at QF_MQ101_0003372B.Fragment_2 (`GameHour.SetValue(7); SetStage(10)`), and
# its QSDT condition GetGlobalValue(MQQuickstart) == 0 makes it the real
# new-game path (entries 1-4 are the ==1..4 debug quickstarts). Retargeting it
# means nothing of the opening runs until the player has chosen; an APPENDED
# unconditional entry — the previous design — ran ALONGSIDE Fragment_2, so
# stage 10 still fired: title credits, cart-roll audio, and a tug-of-war over
# the player. Vanilla stage 0 must still have exactly 5 entries or the layout
# has drifted and the takeover would replace the wrong one.
MQ101_TAKEOVER_STAGE = 0
MQ101_TAKEOVER_LOG_ENTRY = 0
MQ101_VANILLA_STAGE0_ENTRIES = 5
MQ101_VANILLA_FRAGMENT_SCRIPT = 'QF_MQ101_0003372B'

# QUST DNAM flags: StartGameEnabled (0x01) | StartsEnabled (0x10).
SGE_FLAGS = 0x0011
# Journal-invisible control quest — this quest has no stages and must not be
# listed in the player's journal (vanilla type 0; see dialog_converter._quest_dnam).
QUEST_TYPE_NONE = 0

# CTDA function index 74 = GetGlobalValue(Global). Operator 0x00 is '=='.
FUNC_GET_GLOBAL_VALUE = 74

PROLOGUE = (
    "The threads of prophecy gather, and fate has not yet chosen its weave.\n\n"
    "Countless worlds turn upon this moment, each with a door standing open and "
    "no one yet walking through it. An Emperor dreams of a stranger in a cell. A "
    "prisoner wakes to the smell of ash and salt. A cart rolls toward Helgen. A "
    "land without gods waits for someone who owes them nothing.\n\n"
    "All of them are true until you choose. Where do the threads of prophecy "
    "bind you?"
)

# Button order here MUST match the GAME_* constants in TESGameSelectQuest.psc.
# A hidden button does NOT renumber the others — Message.Show() returns the
# button's own index regardless of which conditions passed (vanilla's
# dunMiddenHandSculptureSCRIPT depends on exactly this) — so index == game id.
#
# Skyrim's button carries NO condition and is therefore always drawn, matching
# dunMiddenNamesMenuMSG, whose final "do nothing" button is likewise
# unconditional. That guarantees the menu always has at least one valid choice
# and can never trap the player with nothing to click.
BUTTONS = [
    ('Skyrim  -  the cart rolls toward Helgen',           None),
    ('Cyrodiil  -  an Emperor has dreamt of you',         FID_GLOB_OBLIVION),
    ('Nehrim  -  a land that owes the gods nothing',      FID_GLOB_NEHRIM),
    ('Vvardenfell  -  an old prophecy stirs in the ash',  FID_GLOB_MORROBLIVION),
]

GLOBALS = [
    (FID_GLOB_SKYRIM,       'TESGS_HasSkyrim'),
    (FID_GLOB_OBLIVION,     'TESGS_HasOblivion'),
    (FID_GLOB_NEHRIM,       'TESGS_HasNehrim'),
    (FID_GLOB_MORROBLIVION, 'TESGS_HasMorroblivion'),
]


def build_glob(fid: int, edid: str) -> bytes:
    """A short-typed global, value 0. FNAM 's' = short; vanilla writes the value
    as a float regardless of the declared type."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_subrecord('FNAM', b's')
    subs += pack_subrecord('FLTV', struct.pack('<f', 0.0))
    return pack_record('GLOB', fid, 0, subs)


def build_mesg() -> bytes:
    """The message box. DNAM bit 0 = Message Box (a full modal with buttons,
    not a corner notification); bit 1 (Auto Display) stays clear because the
    script shows it explicitly and reads the button index back."""
    subs = pack_string_subrecord('EDID', 'TESGSGameSelectMSG')
    subs += pack_string_subrecord('DESC', PROLOGUE)
    subs += pack_string_subrecord('FULL', 'The Threads of Prophecy')
    # INAM is a required leftover ("Icon (unused)") and is always NULL in vanilla.
    subs += pack_formid_subrecord('INAM', 0)
    subs += pack_uint32_subrecord('DNAM', 0x00000001)   # Message Box
    for text, gate_fid in BUTTONS:
        subs += pack_string_subrecord('ITXT', text)
        if gate_fid is not None:
            subs += pack_subrecord('CTDA', build_ctda(
                FUNC_GET_GLOBAL_VALUE, param1=gate_fid, comp_value=1.0,
                operator=0x00))
    return pack_record('MESG', FID_MESG, 0, subs)


def build_qust() -> bytes:
    """The selector quest: script-only, no stages, no aliases, no objectives.

    NOT Start Game Enabled. It is started and driven by the MQ101 takeover
    fragment, which runs exactly once at stage 0 of a new game. Relying on
    OnInit instead is what made the menu appear twice — OnInit fires again
    whenever the quest restarts or is re-added to a save.
    """
    vmad = build_vmad_object_script(
        SCRIPT_NAME,
        object_props={
            'GameSelectMenu':  FID_MESG,
            'HasSkyrim':       FID_GLOB_SKYRIM,
            'HasOblivion':     FID_GLOB_OBLIVION,
            'HasNehrim':       FID_GLOB_NEHRIM,
            'HasMorroblivion': FID_GLOB_MORROBLIVION,
        })

    subs = pack_string_subrecord('EDID', 'TESGSGameSelect')
    subs += pack_subrecord('VMAD', vmad)
    subs += pack_string_subrecord('FULL', 'Threads of Prophecy')
    # DNAM: Flags(U16) Priority(U8) FormVer(U8) Unknown(U32) Type(U32)
    subs += pack_subrecord('DNAM', struct.pack('<HBBII', 0, 0, 0, 0,
                                               QUEST_TYPE_NONE))
    subs += pack_subrecord('NEXT', b'')
    # ANAM (next alias id) is written even with no aliases — vanilla always
    # carries it, and the CK adds it on load regardless.
    subs += pack_uint32_subrecord('ANAM', 0)
    return pack_record('QUST', FID_QUST, 0, subs)


def build_mq101_override(skyrim_esm: str) -> bytes:
    """Override vanilla MQ101 with the stage-0 takeover.

    Everything about the record is preserved verbatim except the VMAD, where
    two edits are made (see _splice_mq101_vmad): our script is appended to the
    attached-scripts array, and the stage-0 / log-entry-0 fragment — vanilla's
    real new-game path — is retargeted from QF_MQ101_0003372B.Fragment_2 to
    TESGameSelectMQ101.RunTakeover. No log entry is added or removed: the
    retargeted entry keeps its GetGlobalValue(MQQuickstart) == 0 condition, so
    debug quickstarts (1-4) bypass the takeover exactly as they bypass
    Fragment_2.

    Reading the vanilla record rather than authoring one from scratch matters:
    MQ101 is ~23 KB of aliases, stages and conditions, and dropping any of it
    would break the opening for anyone who picks Skyrim.
    """
    from tools.tes5_esm_reader import read_tes5_file
    _hdr, recs, _loc = read_tes5_file(skyrim_esm)
    src = next((r for r in recs if r.type == 'QUST' and r.form_id == FID_MQ101),
               None)
    if src is None:
        raise SystemExit(f'MQ101 ({FID_MQ101:08X}) not found in {skyrim_esm}')

    out = b''
    stage_index = None
    stage0_entries = 0

    for sub in src.subrecords:
        if sub.type == 'VMAD':
            out += pack_subrecord('VMAD', _splice_mq101_vmad(sub.data))
            continue
        if sub.type == 'INDX':
            stage_index = struct.unpack_from('<H', sub.data, 0)[0]
        if sub.type == 'QSDT' and stage_index == MQ101_TAKEOVER_STAGE:
            stage0_entries += 1
        out += pack_subrecord(sub.type, sub.data)

    if stage0_entries != MQ101_VANILLA_STAGE0_ENTRIES:
        raise SystemExit(
            f'MQ101 stage 0 has {stage0_entries} log entries, expected '
            f'{MQ101_VANILLA_STAGE0_ENTRIES}. The stage layout has drifted — '
            f're-check which entry is the real new-game path against the '
            f'installed Skyrim.esm.')

    return pack_record('QUST', FID_MQ101, src.flags, out)


def _splice_mq101_vmad(vmad: bytes) -> bytes:
    """Append our script and retarget the stage-0/log-0 fragment to it.

    The QUST VMAD layout is: version, objectFormat, scripts[], then a Script
    Fragments struct (extra-bind version, fragment count, filename, fragments[]),
    then an Aliases array. Everything but the retargeted entry must be carried
    through untouched — MQ101 has 54 aliases and 158 other fragments, and
    dropping any of them would break the whole opening.
    """
    version, obj_format, script_count = struct.unpack_from('<HHH', vmad, 0)
    pos = 6
    for _ in range(script_count):
        pos = _skip_script_entry(vmad, pos)
    scripts_end = pos

    # Script Fragments header
    extra_version = struct.unpack_from('<b', vmad, pos)[0]
    frag_count = struct.unpack_from('<H', vmad, pos + 1)[0]
    pos += 3
    fname_len = struct.unpack_from('<H', vmad, pos)[0]
    file_name = vmad[pos + 2:pos + 2 + fname_len]
    pos += 2 + fname_len

    frags = b''
    retargeted = False
    for _ in range(frag_count):
        entry_start = pos
        stage, log, unknown = struct.unpack_from('<IIB', vmad, pos)
        pos += 9
        name_end = _skip_wstring(vmad, pos)        # ScriptName
        script_name = vmad[pos + 2:name_end].decode('utf-8')
        pos = _skip_wstring(vmad, name_end)        # FragmentName
        if (stage, log) == (MQ101_TAKEOVER_STAGE, MQ101_TAKEOVER_LOG_ENTRY):
            if script_name != MQ101_VANILLA_FRAGMENT_SCRIPT:
                raise SystemExit(
                    f'MQ101 stage 0 / log 0 fragment belongs to '
                    f'{script_name!r}, expected '
                    f'{MQ101_VANILLA_FRAGMENT_SCRIPT!r} — another mod or a '
                    f'game update changed the record; refusing to retarget.')
            frags += (struct.pack('<IIB', stage, log, unknown)
                      + _pack_wstring(MQ101_SCRIPT_NAME)
                      + _pack_wstring('RunTakeover'))
            retargeted = True
        else:
            frags += vmad[entry_start:pos]
    aliases_tail = vmad[pos:]                      # Aliases array, verbatim

    if not retargeted:
        raise SystemExit('MQ101 stage-0/log-0 fragment not found; cannot '
                         'install takeover')

    our_script = _pack_script_entry(MQ101_SCRIPT_NAME, {
        'Selector':          FID_QUST,
        'HoldingCellMarker': FID_HOLDING_CELL_MARKER,
        'GameHour':          FID_GAMEHOUR,
    })

    return (struct.pack('<HHH', version, obj_format, script_count + 1)
            + vmad[6:scripts_end]
            + our_script
            + struct.pack('<b', extra_version)
            + struct.pack('<H', frag_count)
            + struct.pack('<H', fname_len) + file_name
            + frags
            + aliases_tail)


def _skip_wstring(data: bytes, pos: int) -> int:
    return pos + 2 + struct.unpack_from('<H', data, pos)[0]


def _skip_script_entry(data: bytes, pos: int) -> int:
    """Skip one script entry (name, flags, properties) in objectFormat 2."""
    pos = _skip_wstring(data, pos)
    pos += 1                                        # flags
    prop_count = struct.unpack_from('<H', data, pos)[0]
    pos += 2
    for _ in range(prop_count):
        pos = _skip_wstring(data, pos)              # property name
        prop_type = data[pos]
        pos += 2                                    # type + status
        pos = _skip_property_value(data, pos, prop_type)
    return pos


def _skip_property_value(data: bytes, pos: int, prop_type: int) -> int:
    if prop_type == 1:                              # Object
        return pos + 8
    if prop_type == 2:                              # String
        return _skip_wstring(data, pos)
    if prop_type in (3, 4):                         # Int32 / Float
        return pos + 4
    if prop_type == 5:                              # Bool
        return pos + 1
    if prop_type >= 11:                             # arrays of the above
        count = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        element = prop_type - 10
        for _ in range(count):
            pos = _skip_property_value(data, pos, element)
        return pos
    raise ValueError(f'unhandled VMAD property type {prop_type}')


def _pack_wstring(text: str) -> bytes:
    raw = text.encode('utf-8')
    return struct.pack('<H', len(raw)) + raw


def _pack_script_entry(name: str, object_props: dict) -> bytes:
    """One attached-script entry: name, flags, Object-typed properties."""
    out = _pack_wstring(name) + struct.pack('<B', 0)
    out += struct.pack('<H', len(object_props))
    for prop_name, fid in object_props.items():
        out += _pack_wstring(prop_name)
        out += struct.pack('<BB', 1, 1)             # type=Object, status=Edited
        out += struct.pack('<HhI', 0, -1, fid)
    return out


def build_plugin(skyrim_esm: str) -> bytes:
    groups = [
        pack_top_group('GLOB', b''.join(build_glob(f, e) for f, e in GLOBALS)),
        pack_top_group('MESG', build_mesg()),
        pack_top_group('QUST', build_qust() + build_mq101_override(skyrim_esm)),
    ]
    # HEDR count = records + GRUPs, matching vanilla Skyrim.esm and the main
    # writer. An undercount here is not cosmetic: the engine walks the file by
    # this number, and a wrong one silently drops records.
    count = sum(_count_records_and_groups(g) for g in groups)
    header = pack_tes4_header(
        ['Skyrim.esm'],
        num_records=count,
        next_object_id=0x900,
        author='TESConversion',
        description='Threads of Prophecy - choose which game to begin',
        is_esm=False)
    return header + b''.join(groups), count


def write_seq(outdir: str):
    """No Start-Game-Enabled quests, so the .seq is empty.

    The selector is driven by the MQ101 stage-0 fragment, not by SGE. A .seq is
    still written (empty) so a stale one from an earlier build — which listed
    the selector as SGE and let it also fire from OnInit, the second source of
    the double menu — cannot survive an upgrade in place.
    """
    seq_dir = os.path.join(outdir, 'seq')
    os.makedirs(seq_dir, exist_ok=True)
    path = os.path.join(seq_dir, os.path.splitext(PLUGIN_NAME)[0] + '.seq')
    with open(path, 'wb') as f:
        f.write(b'')
    return path


def compile_scripts(outdir: str) -> bool:
    """Compile both plugin scripts against the Skyrim SE headers."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    from convert import _find_skyrim_source_scripts, load_config
    try:
        cfg = load_config()
    except (FileNotFoundError, OSError):
        cfg = {}
    headers = _find_skyrim_source_scripts(cfg)
    if not headers:
        print('  ERROR: Skyrim Papyrus source headers not found '
              '(<Skyrim SE>\\Data\\Source\\Scripts)')
        return False

    src_dir = os.path.join(outdir, 'scripts', 'source')
    out_dir = os.path.join(outdir, 'scripts')
    os.makedirs(out_dir, exist_ok=True)
    compiler = os.path.join(root, 'external', 'papyrus-compiler', 'papyrus.exe')
    if not os.path.isfile(compiler):
        print(f'  ERROR: Papyrus compiler not found at {compiler}')
        return False

    ok = True
    for name in (SCRIPT_NAME, MQ101_SCRIPT_NAME):
        psc = os.path.join(src_dir, name + '.psc')
        # -nocache: the compiler keys its cache on source content alone, so an
        # unchanged file silently produces no .pex without it.
        cmd = [compiler, 'compile', '-nocache', '-i', psc, '-o', out_dir,
               '-h', headers, '-h', src_dir]
        r = subprocess.run(windows_cmd(cmd), capture_output=True, text=True,
                           timeout=90, cwd=root)
        out = (r.stdout or '') + (r.stderr or '')
        pex = os.path.join(out_dir, name + '.pex')
        if r.returncode != 0 or not os.path.isfile(pex):
            print(f'  COMPILE FAILED ({name}):')
            print('   ', out.strip().replace('\n', '\n    '))
            ok = False
            continue
        print(f'  compiled {name}.pex ({os.path.getsize(pex)} bytes)')
    return ok


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--outdir', default='output/TESGameSelect',
                    help='Data-folder-style output root (default: '
                         'output/TESGameSelect)')
    ap.add_argument('--no-compile', action='store_true',
                    help='Skip Papyrus compilation (plugin only)')
    ap.add_argument('--skyrim-esm', default=None,
                    help='Path to Skyrim.esm, whose MQ101 record is overridden '
                         '(default: auto-detected from the installed game)')
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = args.outdir if os.path.isabs(args.outdir) else os.path.join(root, args.outdir)
    os.makedirs(outdir, exist_ok=True)

    skyrim_esm = args.skyrim_esm
    if not skyrim_esm:
        from convert import find_game_path, load_config
        try:
            cfg = load_config()
        except (FileNotFoundError, OSError):
            cfg = {}
        data_path = find_game_path('skyrimse', cfg)
        if not data_path:
            print('  ERROR: Skyrim SE install not found; pass --skyrim-esm')
            return 1
        skyrim_esm = os.path.join(data_path, 'Skyrim.esm')
    if not os.path.isfile(skyrim_esm):
        print(f'  ERROR: Skyrim.esm not found at {skyrim_esm}')
        return 1

    # Stage the hand-written script sources into the output tree so the shipped
    # folder is a complete, self-contained Data folder.
    src_dir = os.path.join(outdir, 'scripts', 'source')
    os.makedirs(src_dir, exist_ok=True)
    for name in (SCRIPT_NAME, MQ101_SCRIPT_NAME):
        shutil.copyfile(
            os.path.join(root, 'TESGameSelect', 'scripts', 'source',
                         name + '.psc'),
            os.path.join(src_dir, name + '.psc'))

    print(f'Reading MQ101 from {skyrim_esm} ...')
    data, count = build_plugin(skyrim_esm)
    esp_path = os.path.join(outdir, PLUGIN_NAME)
    with open(esp_path, 'wb') as f:
        f.write(data)
    print(f'Wrote {esp_path} ({len(data)} bytes, HEDR numRecords={count})')

    seq = write_seq(outdir)
    print(f'Wrote {seq} (empty — no Start-Game-Enabled quests)')

    ok = True
    if not args.no_compile:
        ok = compile_scripts(outdir)

    print('\nShip the contents of this folder as a Data folder:')
    print(f'  {PLUGIN_NAME}')
    print(f'  seq\\{os.path.splitext(PLUGIN_NAME)[0]}.seq')
    for name in (SCRIPT_NAME, MQ101_SCRIPT_NAME):
        print(f'  scripts\\{name}.pex')
        print(f'  scripts\\source\\{name}.psc')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
