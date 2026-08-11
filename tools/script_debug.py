#!/usr/bin/env python3
"""Instrument converted Papyrus scripts with SKSE-free diagnostic logging.

Generic replacement for the per-quest debug tools: point it at ANY converted
plugin and any set of quests/scripts and it writes one log you can diagnose a
runtime symptom from, without another build-and-play round trip.

    Documents/My Games/Skyrim Special Edition/Logs/Script/User/<LOG>.log
    (requires [Papyrus] bEnableLogging=1, bEnableTrace=1 in SkyrimCustom.ini)

Why this exists: a converted script that silently does nothing is the single
most expensive failure mode in this pipeline, because Papyrus logs only ERRORS
— a script whose properties all bind and whose logic simply never reaches the
interesting branch produces NO log output at all, which is indistinguishable
from "not running". These probes make the quiet path visible.

What it captures per script kind:

  QUEST tick    IsRunning / GetStage / every Conditional property, on change
  QUEST STAGE   every stage fragment that fires, with its stage number
  ACTOR         3d-loaded / dead / combat / current package / distance to
                player, on change — plus OnPackageStart/End/Change
  SAY           every Actor.Say() call site: the topic, the speaker, and
                whether the speaker was 3D-loaded when it fired (a Say() on an
                unloaded actor is silently dropped by the engine)
  FRAG          every INFO End fragment, i.e. the line actually PLAYED —
                the ground truth for "did this dialogue reach the player"

Instrumentation is idempotent (a second run is a no-op) and is wiped by any
`convert.py --scripts-only`, which is also how you revert.

Usage:
    # everything owned by one quest (script + fragments + its INFO fragments)
    python tools/script_debug.py -f Morrowind_ob.esm --quest fbmwChargen

    # add specific actor scripts, and trace every Say() in the plugin
    python tools/script_debug.py -f Morrowind_ob.esm --quest fbmwChargen \\
        --actor TES4_mwGenericNPCGreetOnlyScript --say-calls

    # arbitrary scripts by name
    python tools/script_debug.py -f Nehrim.esm --script TES4_MQ00Script

    python tools/script_debug.py -f Morrowind_ob.esm --revert
"""
import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from subprocess_flags import windows_cmd  # noqa: E402

MARKER = 'tools/script_debug.py'

_STATE_DECL_TMPL = '''
; --- TEMP DIAGNOSTIC ({marker}) ---
Bool _dbgOpen = False
String _dbgLast = ""
String _dbgPkg = ""
'''


def src_dir(plugin):
    return os.path.join('output', plugin, 'scripts', 'source')


def out_dir(plugin):
    return os.path.join('output', plugin, 'scripts')


def _open_block(log, indent='  '):
    """Debug.OpenUserLog is per-log-per-session; guard so it runs once."""
    return (f'{indent}If !_dbgOpen\n'
            f'{indent}  _dbgOpen = Debug.OpenUserLog("{log}")\n'
            f'{indent}EndIf\n')


def _add_state(src, marker=MARKER):
    """Insert the diagnostic locals after the script's doc comment/header."""
    if '_dbgOpen' in src:
        return src           # already carries the diagnostic locals
    decl = _STATE_DECL_TMPL.format(marker=marker)
    new, n = re.subn(r'(\{Converted from TES4:[^}]*\}\n)', r'\1' + decl,
                     src, count=1)
    if n:
        return new
    # No doc comment (fragments, static scripts): insert after ScriptName.
    return re.sub(r'(ScriptName[^\n]*\n)', r'\1' + decl, src, count=1)


# --- Quest scripts ---------------------------------------------------------

def _conditional_props(src):
    """The script's Conditional properties — its actual state machine.

    These are exactly the variables the TES4 script used to sequence itself
    (JiubSpeak, convTimer, stage counters), so dumping them on change gives a
    complete trace of the machine without knowing anything about the quest.
    """
    return re.findall(r'^(?:Int|Float|Bool)\s+Property\s+(\w+).*Conditional',
                      src, re.M)


def _quest_block(log, props):
    """Per-tick quest state: running, stage, and every Conditional property."""
    parts = ['"tick run=" + IsRunning() + " st=" + GetStage()']
    for p in props:
        parts.append(f'" {p}=" + {p}')
    expr = ' + '.join(parts)
    return f'''
  ; --- TEMP DIAGNOSTIC ({MARKER}) ---
{_open_block(log)}  String _s = {expr}
  If _s != _dbgLast
    _dbgLast = _s
    Debug.TraceUser("{log}", "QUEST " + _s)
  EndIf
'''


def instrument_quest(path, log):
    src = open(path, encoding='utf-8').read()
    if MARKER in src or 'Event OnUpdate()' not in src:
        return False
    src = _add_state(src)
    props = _conditional_props(src)
    src = src.replace('Event OnUpdate()\n',
                      'Event OnUpdate()\n' + _quest_block(log, props), 1)
    open(path, 'w', encoding='utf-8').write(src)
    return True


def instrument_quest_fragments(path, log):
    """Log every stage fragment that fires, with its stage number."""
    src = open(path, encoding='utf-8').read()
    if MARKER in src:
        return False
    src = _add_state(src)

    def repl(m):
        stage = int(m.group(1))
        return (m.group(0)
                + _open_block(log)
                + f'  Debug.TraceUser("{log}", "STAGE {stage}")\n')

    src, n = re.subn(r'Function Fragment_Stage_(\d+)_Item_\d+\(\)\n', repl, src)
    if not n:
        return False
    open(path, 'w', encoding='utf-8').write(src)
    return True


# --- Actor / object scripts ------------------------------------------------

def _actor_block(log, tag):
    """Actor runtime state — the inputs that decide whether Say() can play.

    A Say() on an actor that is not 3D-loaded is dropped silently, and a
    package that never wins means a force-greet never happens, so these are
    the two things worth watching on any actor that should be speaking.
    """
    return f'''
  ; --- TEMP DIAGNOSTIC ({MARKER}) ---
{_open_block(log)}  Actor _a = Self as Actor
  If _a
    Package _cp = _a.GetCurrentPackage()
    String _p = ""
    If _cp
      _p = _cp as String
    EndIf
    String _pk = "3d=" + _a.Is3DLoaded() + " dead=" + _a.IsDead() + " combat=" + _a.IsInCombat() + " dlg=" + _a.IsInDialogueWithPlayer() + " pkg=" + _p + " dist=" + (_a.GetDistance(Game.GetPlayer()) as Int)
    If _pk != _dbgPkg
      _dbgPkg = _pk
      Debug.TraceUser("{log}", "{tag} " + _pk)
    EndIf
  EndIf
'''


_PKG_EVENTS = '''
Event OnPackageStart(Package akNewPackage)
  Debug.TraceUser("{log}", "{tag} PKGSTART " + akNewPackage)
EndEvent

Event OnPackageEnd(Package akOldPackage)
  Debug.TraceUser("{log}", "{tag} PKGEND " + akOldPackage)
EndEvent

Event OnPackageChange(Package akOldPackage)
  Debug.TraceUser("{log}", "{tag} PKGCHANGE " + akOldPackage)
EndEvent
'''


def instrument_actor(path, log, tag):
    src = open(path, encoding='utf-8').read()
    if MARKER in src:
        return False
    src = _add_state(src)
    if 'Event OnUpdate()\n' in src:
        src = src.replace('Event OnUpdate()\n',
                          'Event OnUpdate()\n' + _actor_block(log, tag), 1)
    # Only add package events the script does not already define — a duplicate
    # Event is a compile error, not a warning.
    events = _PKG_EVENTS.format(log=log, tag=tag)
    for ev in ('OnPackageStart', 'OnPackageEnd', 'OnPackageChange'):
        if f'Event {ev}(' in src:
            events = re.sub(rf'Event {ev}\(.*?EndEvent\n', '', events,
                            flags=re.S)
    src += events
    open(path, 'w', encoding='utf-8').write(src)
    return True


# --- Say() call sites ------------------------------------------------------

# `(x as Actor).Say(Topic)` / `x.Say(Topic)` as emitted by script_convert.
_SAY_RE = re.compile(
    r'^(?P<indent>\s*)(?P<call>\(?(?P<obj>[\w.]+)(?: as Actor\))?'
    r'\.Say\((?P<topic>\w+)[^)]*\))\s*$', re.M)


def instrument_say_calls(path, log, tag):
    """Log every Say() with its topic and whether the speaker was loaded.

    This is the probe that distinguishes the two ways a line goes missing:
    the call never happened (no SAY line at all) versus the call happened but
    the engine selected no INFO (SAY line present, no matching FRAG line).

    Deliberately NOT marker-guarded: this runs as part of the same pass that
    adds the tick probes, so the marker is normally already present. Its own
    idempotence comes from the SAY-trace check below.
    """
    src = open(path, encoding='utf-8').read()
    if f'"SAY {tag} ' in src:
        return False

    hits = []

    def repl(m):
        obj = m.group('obj')
        topic = m.group('topic')
        hits.append(topic)
        ind = m.group('indent')
        # Dump the SPEAKER's runtime identity alongside the call. The injected
        # INFO gates are evaluated against these exact values, so a gate that
        # cannot pass is visible here rather than guessed at:
        #   base -> what GetIsID(base) compares against
        #   race -> what GetIsPlayableRace reads
        # A None in either is a dead gate. (Voice type has no Papyrus
        # accessor — GetVoiceType does not exist on Actor or ActorBase — so
        # GetIsVoiceType has to be inferred from the actor's VTCK in the ESM.)
        probe = (
            f'{ind}Actor _sa = {obj} as Actor\n'
            f'{ind}ActorBase _sb = None\n'
            f'{ind}If _sa\n'
            f'{ind}  _sb = _sa.GetActorBase()\n'
            f'{ind}EndIf\n'
            f'{ind}Debug.TraceUser("{log}", "SAY {tag} {topic}'
            f' spk={obj} 3d=" + (({obj} as ObjectReference).Is3DLoaded())'
            f' + " base=" + _sb + " race=" + _sb.GetRace()'
            f' + " dead=" + _sa.IsDead()'
            f' + " indlg=" + _sa.IsInDialogueWithPlayer())\n')
        return probe + m.group(0)

    new = _SAY_RE.sub(repl, src)
    if not hits:
        return False
    new = _add_state(new)
    open(path, 'w', encoding='utf-8').write(new)
    return True


# --- INFO fragments --------------------------------------------------------

def instrument_fragment(path, log, fid):
    """Log an INFO's End fragment — proof the line actually PLAYED.

    The engine runs this only after the response finishes, so a FRAG line is
    the ground truth that dialogue reached the player. Its absence next to a
    SAY line means the topic was called but no INFO passed its conditions.
    """
    src = open(path, encoding='utf-8').read()
    if MARKER in src:
        return False
    src = _add_state(src)
    src, n = re.subn(
        r'(Function Fragment_\d+\(ObjectReference akSpeakerRef\)\n)',
        r'\1' + _open_block(log)
        + f'  Debug.TraceUser("{log}", "FRAG {fid} spk=" + akSpeakerRef)\n',
        src, count=1)
    if not n:
        return False
    open(path, 'w', encoding='utf-8').write(src)
    return True


# --- Discovery -------------------------------------------------------------

def quest_scripts(plugin, quest_edid):
    """(quest script, QF fragment script) stems for a quest, if present.

    The quest's own converted script keeps the TES4 SCPT EditorID, which is
    not derivable from the quest EditorID, so match on the QF_ fragment (which
    IS derived from it) and on any script naming the quest as a property.
    """
    d = src_dir(plugin)
    qf = f'TES4_QF_{quest_edid}'
    stems = []
    if os.path.isfile(os.path.join(d, qf + '.psc')):
        stems.append(('qf', qf))
    # The state-machine script is whatever the QF binds as a script-typed
    # property named after the quest.
    qf_path = os.path.join(d, qf + '.psc')
    if os.path.isfile(qf_path):
        txt = open(qf_path, encoding='utf-8').read()
        m = re.search(rf'^(TES4_\w+)\s+Property\s+{re.escape(quest_edid)}\s',
                      txt, re.M | re.I)
        if m and os.path.isfile(os.path.join(d, m.group(1) + '.psc')):
            stems.append(('quest', m.group(1)))
    return stems


def _candidate_source_fids(out_fid, shift):
    """Possible TES4 source FormIDs for an output FormID.

    The importer shifts every load-order index up by the number of masters
    prepended (Skyrim.esm, and any converted TES4 masters), so the source id is
    the output id with that shift undone. Engine-fixed ids (< 0x100) are never
    shifted, and a plugin's own records may or may not have had an index at
    all, so try the unshifted value too rather than guessing wrong.
    """
    idx = (out_fid >> 24) & 0xFF
    low = out_fid & 0x00FFFFFF
    out = []
    # Try every plausible un-shift, nearest first. Skyrim.esm is prepended for
    # every plugin but a converted TES4 master may or may not be, and an
    # override keeps its master's index, so the exact shift for a given record
    # is not knowable from the header alone — probe rather than assume.
    for s in range(shift, -1, -1):
        if idx >= s:
            out.append(((idx - s) << 24) | low)
    out.append(out_fid)
    out.append(low)
    return list(dict.fromkeys(out))


def _master_shift(esm_path):
    """How many masters the converted plugin prepended (the index shift)."""
    import struct
    data = open(esm_path, 'rb').read(8192)
    p = 24
    n = 0
    while p + 6 <= len(data):
        sig = data[p:p + 4]
        sz = struct.unpack_from('<H', data, p + 4)[0]
        if sig == b'GRUP':
            break
        if sig == b'MAST':
            n += 1
        p += 6 + sz
    return n


def quest_info_fragments(plugin, quest_edid, esm_path=None):
    """TIF__ fragment stems for INFOs owned by this quest.

    Read from the built ESM when available so the set is exact; otherwise fall
    back to every TIF in the plugin (still correct, just noisier).
    """
    d = src_dir(plugin)
    if not esm_path or not os.path.isfile(esm_path):
        return []
    import struct
    data = open(esm_path, 'rb').read()
    shift = _master_shift(esm_path)

    # Resolve the quest EditorID -> FormID.
    quest_fid = 0
    want = quest_edid.encode('ascii', 'replace')
    pos = 0
    while True:
        i = data.find(b'QUST', pos)
        if i < 0 or i + 24 > len(data):
            break
        pos = i + 1
        size, flags, fid = struct.unpack_from('<III', data, i + 4)
        if not (6 < size < 1_000_000) or (flags & 0x40000):
            continue
        body = data[i + 24:i + 24 + size]
        if body[:4] != b'EDID':
            continue
        sz = struct.unpack_from('<H', body, 4)[0]
        if body[6:6 + sz - 1] == want:
            quest_fid = fid
            break
    if not quest_fid:
        return []

    # Every INFO whose parent DIAL names that quest (QNAM).
    stems = []
    pos = 0
    while True:
        i = data.find(b'DIAL', pos)
        if i < 0 or i + 24 > len(data):
            break
        pos = i + 1
        size, flags, fid = struct.unpack_from('<III', data, i + 4)
        if not (6 < size < 1_000_000):
            continue
        body = data[i + 24:i + 24 + size]
        p = 0
        owner = 0
        while p + 6 <= len(body):
            sg = body[p:p + 4]
            sz = struct.unpack_from('<H', body, p + 4)[0]
            if sg == b'QNAM' and sz == 4:
                owner = struct.unpack_from('<I', body, p + 6)[0]
            p += 6 + sz
        if owner != quest_fid:
            continue
        # Its Topic Children GRUP follows the record.
        end = i + 24 + size
        if data[end:end + 4] != b'GRUP':
            continue
        gsize = struct.unpack_from('<I', data, end + 4)[0]
        q = end + 24
        while q < end + gsize and q + 24 <= len(data):
            csig = data[q:q + 4]
            csize, cflags, cfid = struct.unpack_from('<III', data, q + 4)
            if csig == b'INFO':
                # A TIF is named for the INFO's SOURCE FormID, which keeps its
                # TES4 load-order index (TES4_TIF__01F8E968) — the output id is
                # that index shifted by the new masters, so undo the shift
                # rather than masking it off (masking gives 00F8E968, a file
                # that does not exist).
                for src_fid in _candidate_source_fids(cfid, shift):
                    stem = f'TES4_TIF__{src_fid:08X}'
                    if os.path.isfile(os.path.join(d, stem + '.psc')):
                        stems.append((stem, f'{src_fid:08X}'))
                        break
            q += 24 + csize
    return stems


# --- Compile ---------------------------------------------------------------

def find_headers():
    p = (r'C:\Program Files (x86)\Steam\steamapps\common'
         r'\Skyrim Special Edition\Data\Source\Scripts')
    return p if os.path.isdir(p) else ''


def compile_one(plugin, stem, headers):
    exe = os.path.join('external', 'papyrus-compiler', 'papyrus.exe')
    cmd = [exe, 'compile', '-nocache',
           '-i', os.path.join(src_dir(plugin), stem + '.psc'),
           '-o', out_dir(plugin), '-h', headers, '-h', src_dir(plugin)]
    r = subprocess.run(windows_cmd(cmd), capture_output=True, text=True)
    out = r.stdout + r.stderr
    ok = r.returncode == 0 and 'error' not in out.lower()
    if not ok:
        for line in out.splitlines():
            if 'error' in line.lower():
                print(f'    {line.strip()}')
    return ok


def main():
    ap = argparse.ArgumentParser(
        description='Add diagnostic logging to converted Papyrus scripts.')
    ap.add_argument('-f', '--file', required=True,
                    help='plugin name, e.g. Morrowind_ob.esm')
    ap.add_argument('--quest', action='append', default=[],
                    help='quest EditorID: instruments its script, its stage '
                         'fragments and its INFO End fragments')
    ap.add_argument('--actor', action='append', default=[],
                    help='actor/object script stem to probe')
    ap.add_argument('--script', action='append', default=[],
                    help='any script stem: quest-style tick logging')
    ap.add_argument('--say-calls', action='store_true',
                    help='log every Actor.Say() in the touched scripts')
    ap.add_argument('--log', default=None,
                    help='user-log name (default TES4Debug)')
    ap.add_argument('--no-compile', action='store_true')
    ap.add_argument('--revert', action='store_true',
                    help='re-run convert.py --scripts-only for clean output')
    args = ap.parse_args()

    plugin = args.file
    log = args.log or 'TES4Debug'

    if args.revert:
        return subprocess.call([sys.executable, 'convert.py',
                                '--scripts-only', '-f', plugin])

    d = src_dir(plugin)
    if not os.path.isdir(d):
        print(f'ERROR: no converted scripts at {d}')
        return 1
    if not (args.quest or args.actor or args.script):
        print('ERROR: nothing selected — pass --quest/--actor/--script')
        return 1

    headers = find_headers()
    if not headers and not args.no_compile:
        print('ERROR: Skyrim Papyrus headers not found')
        return 1

    esm = os.path.join('output', plugin, plugin)
    touched = []
    say_targets = []

    for q in args.quest:
        for kind, stem in quest_scripts(plugin, q):
            path = os.path.join(d, stem + '.psc')
            fn = instrument_quest_fragments if kind == 'qf' else instrument_quest
            if fn(path, log):
                touched.append(stem)
                print(f'  instrumented {stem} [{kind}]')
            say_targets.append(stem)
        frags = quest_info_fragments(plugin, q, esm)
        n = 0
        for stem, fid in frags:
            if instrument_fragment(os.path.join(d, stem + '.psc'), log, fid):
                touched.append(stem)
                n += 1
        if frags:
            print(f'  instrumented {n} INFO fragment(s) for {q}')

    for stem in args.actor:
        path = os.path.join(d, stem + '.psc')
        if not os.path.isfile(path):
            print(f'  SKIP {stem} (not found)')
            continue
        tag = stem.replace('TES4_', '').upper()
        if instrument_actor(path, log, tag):
            touched.append(stem)
            print(f'  instrumented {stem} [{tag}]')
        say_targets.append(stem)

    for stem in args.script:
        path = os.path.join(d, stem + '.psc')
        if not os.path.isfile(path):
            print(f'  SKIP {stem} (not found)')
            continue
        if instrument_quest(path, log):
            touched.append(stem)
            print(f'  instrumented {stem}')
        say_targets.append(stem)

    if args.say_calls:
        for stem in dict.fromkeys(say_targets):
            path = os.path.join(d, stem + '.psc')
            if not os.path.isfile(path):
                continue
            tag = stem.replace('TES4_', '').upper()
            if instrument_say_calls(path, log, tag):
                if stem not in touched:
                    touched.append(stem)
                print(f'  say-probes in {stem}')

    print(f'\n{len(touched)} script(s) instrumented')
    if args.no_compile:
        return 0

    print('Compiling...')
    bad = [s for s in touched if not compile_one(plugin, s, headers)]
    print(f'  {len(touched) - len(bad)}/{len(touched)} compiled')
    if bad:
        print('  FAILED: ' + ', '.join(bad))
        return 1
    print(f'\nLog: Documents/My Games/Skyrim Special Edition/'
          f'Logs/Script/User/{log}.log')
    return 0


if __name__ == '__main__':
    sys.exit(main())
