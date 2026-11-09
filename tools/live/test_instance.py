"""An isolated Skyrim for headless, automated in-game tests.

    python -m tools.live.test_instance build [--steam-exe] [--plugin X.esm ...]
    python -m tools.live.test_instance launch
    python -m tools.live.test_instance status
    python -m tools.live.test_instance smoke [--cell QASmoke]
    python -m tools.live.test_instance shot [--out x.png]
    python -m tools.live.test_instance menus
    python -m tools.live.test_instance stop
    python -m tools.live.test_instance mwtest [--cases X.mwtest|folder] [--cell QASmoke]
    python -m tools.live.test_instance questtest --cases X.tes4test|folder [--new-game N]

`build` makes the instance folder from HARD LINKS to only the vanilla files of
the Steam install, plus SKSE, the game bridge and the named plugins, and
writes its INIs and Plugins.txt into a sandbox profile inside it. `launch`
starts it muted on a hidden desktop with the profile redirected
(TESSandbox.dll), so it never touches the player's screen, speakers, input,
INIs, saves or logs.

See: docs/commentary/headless_testing.md
"""
import argparse
import contextlib
import dataclasses
import glob
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time

from asset_convert.lod.esm_scan import plugin_masters
from core.chargen_source import candidates
from core.subprocess_flags import POPEN_FLAGS
from source_paths import find_game_path
from tools.live import hidden_launch, tes4_cases
from tools.live.game_bridge import Bridge, BridgeError
from tools.release.package_runtime_dll import (EXPORT_ROOT, MOD_NAME, MODS, chargen_table,
                                               configured_choice, morrowind_menus)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: Instance 1's folder; instance N > 1 lives in "<this> N" with its own pipe and desktop.
FIRST_INSTANCE = r'C:\Other Games\Skyrim Test'
INSTANCE = FIRST_INSTANCE
PROFILE = os.path.join(INSTANCE, '_profile')
DOCUMENTS = os.path.join(PROFILE, 'Documents')
LOCAL_APPDATA = os.path.join(PROFILE, 'LocalAppData')
MY_GAMES = os.path.join(DOCUMENTS, 'My Games', 'Skyrim Special Edition')
PID_FILE = os.path.join(PROFILE, 'pid.txt')
#: The test instance's own bridge pipe, never the player's tes_game_bridge.
PIPE = r'\\.\pipe\tes_test_bridge'
DESKTOP = 'TESConversionTest'
WINDOW_CLASS = 'Skyrim Special Edition'
MENU_MANAGER_ID = 400327
#: tes_runtime/common/engine_ids.h: BSFixedString ctor, UIManager::AddMessage, UIManager singleton.
FIXED_STRING_CTOR_ID = 69161
UI_ADD_MESSAGE_ID = 13631
UI_MANAGER_ID = 400445
#: UI message kForceHide; kHide (3) leaves a MessageBoxMenu open.
MESSAGE_FORCE_HIDE = 4
#: MainMenu's `StartNewGame` callback (1.6.1170 0x9426a0), what clicking New runs.
NEW_GAME_ID = 52118
#: MessageBoxMenu's `buttonPress` callback (1.6.1170 0x94bbe0): runs the box's own callback.
BUTTON_PRESS_ID = 52279
#: Startup popups that pause the game, RTTI class -> registered menu name.
STARTUP_POPUPS = (('MessageBoxMenu', 'MessageBoxMenu'), ('loginMenu@LoginMenu', 'Login Menu'))
APP_ID = '489830'
UNPACKED_EXE = (r'C:\Program Files (x86)\Steam\steamapps\content\app_489830'
                r'\depot_489833\SkyrimSE.1.6.1170.unpacked.exe')
SKSE_DLL = 'skse64_1_6_1170.dll'
#: The SKSE release archive, extracted here: its Data\Scripts are the .pex its Papyrus functions need.
SKSE_RELEASE = os.path.join(INSTANCE, '_skse')
ADDRESS_LIBRARY = os.path.join('SKSE', 'Plugins', 'versionlib-1-6-1170-0.bin')
SANDBOX_DLL = os.path.join(REPO, 'game_bridge', 'sandbox', 'TESSandbox.dll')
BRIDGE_DLL = os.path.join(REPO, 'game_bridge', 'TESGameBridge.dll')
#: Game-root files the vanilla game and SKSE need; the CK, mod loaders and ENB stay out.
ROOT_FILES = ('steam_api64.dll', 'bink2w64.dll', 'd3dcompiler_46.dll', 'tbb.dll',
              'tbbmalloc.dll', 'Skyrim_Default.ini', 'Low.ini', 'Medium.ini', 'High.ini',
              'Ultra.ini', SKSE_DLL)
#: The vanilla masters and archives; Creation Club content is left out on purpose.
DATA_FILES = ('Skyrim.esm', 'Update.esm', 'Dawnguard.esm', 'HearthFires.esm',
              'Dragonborn.esm', '_ResourcePack.esl', '_ResourcePack.bsa',
              'MarketplaceTextures.bsa')
#: Tick unfocused, no intro, no Bethesda.net (its AE reply opens a game-pausing "Thanks for buying" box).
SKYRIM_INI = (('General', 'bAlwaysActive', '1'), ('General', 'sIntroSequence', ''),
              ('Bethesda.net', 'bEnablePlatform', '0'), ('Bethesda.net', 'bAutoSkipMainMenuLogin', '1'))
#: Over the Low preset: a small hidden window; silence (volume 0 keeps sound-timed lines real).
PREFS_INI = (('Display', 'bFull Screen', '0'), ('Display', 'bBorderless', '1'),
             ('Display', 'iSize W', '640'), ('Display', 'iSize H', '360'),
             ('AudioMenu', 'fAudioMasterVolume', '0.0000'), ('General', 'bUpsellOwned', '1'))


def use_instance(slot: int) -> None:
    """Points the folder, profile, bridge pipe and hidden desktop at test instance `slot` (1 = the first).

    See: docs/commentary/headless_testing.md#parallel-instances
    """
    global INSTANCE, PROFILE, DOCUMENTS, LOCAL_APPDATA, MY_GAMES, PID_FILE, PIPE, DESKTOP
    suffix = '' if slot == 1 else ' %d' % slot
    INSTANCE = FIRST_INSTANCE + suffix
    PROFILE = os.path.join(INSTANCE, '_profile')
    DOCUMENTS = os.path.join(PROFILE, 'Documents')
    LOCAL_APPDATA = os.path.join(PROFILE, 'LocalAppData')
    MY_GAMES = os.path.join(DOCUMENTS, 'My Games', 'Skyrim Special Edition')
    PID_FILE = os.path.join(PROFILE, 'pid.txt')
    PIPE = r'\\.\pipe\tes_test_bridge' + suffix.strip()
    DESKTOP = 'TESConversionTest' + suffix.strip()


def share_saves() -> None:
    """Makes this instance's Saves a junction to instance 1's, so checkpoints load in either.

    Never `rmtree` a profile holding it: rmtree follows the junction into instance 1's saves.
    """
    saves = os.path.join(MY_GAMES, 'Saves')
    first = os.path.join(FIRST_INSTANCE, '_profile', 'Documents', 'My Games', 'Skyrim Special Edition', 'Saves')
    if INSTANCE == FIRST_INSTANCE or os.path.exists(saves):
        return
    os.makedirs(MY_GAMES, exist_ok=True)
    os.makedirs(first, exist_ok=True)
    subprocess.run(['cmd', '/c', 'mklink', '/J', saves, first], check=True, capture_output=True, **POPEN_FLAGS)


def steam_dir() -> str:
    """The Steam install's game root, from the registry."""
    data = find_game_path('skyrimse')
    if not data:
        sys.exit('Skyrim SE install not found in the registry')
    return os.path.dirname(data)


def link(src: str, dst: str) -> None:
    """Hard-links `src` to `dst` inside the instance, replacing a stale `dst`."""
    if not os.path.isfile(src):
        sys.exit('missing source file: %s' % src)
    if os.path.exists(dst):
        if os.path.samefile(src, dst):
            return
        os.remove(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.link(src, dst)


def _section_bounds(lines: list, section: str):
    """(start, end) line indexes of `[section]` in `lines`, or None when absent."""
    header = '[%s]' % section.lower()
    start = next((i for i, l in enumerate(lines) if l.strip().lower() == header), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('[')), len(lines))
    return start, end


def set_ini(text: str, settings) -> str:
    """`text` with each (section, key, value) in `settings` set, adding what is missing."""
    lines = text.splitlines()
    for section, key, value in settings:
        entry = '%s=%s' % (key, value)
        bounds = _section_bounds(lines, section)
        if bounds is None:
            lines += ['', '[%s]' % section, entry]
            continue
        start, end = bounds
        hit = next((i for i in range(start + 1, end)
                    if lines[i].split('=', 1)[0].strip().lower() == key.lower()), None)
        if hit is None:
            lines.insert(start + 1, entry)
        else:
            lines[hit] = entry
    return '\n'.join(lines) + '\n'


def apply_ini(name: str, settings, base: str = None) -> None:
    """Sets `settings` in the profile's INI `name`, on top of `base` or the file as it is."""
    path = os.path.join(MY_GAMES, name)
    if base is None:
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                base = f.read()
        except OSError:
            base = ''
    with open(path, 'w', encoding='utf-8') as f:
        f.write(set_ini(base, settings))


def write_profile(root: str, plugins) -> None:
    """Writes the sandbox profile: Skyrim.ini, SkyrimPrefs.ini and Plugins.txt."""
    os.makedirs(MY_GAMES, exist_ok=True)
    with open(os.path.join(root, 'Skyrim_Default.ini'), encoding='utf-8', errors='replace') as f:
        apply_ini('Skyrim.ini', SKYRIM_INI, f.read())
    with open(os.path.join(root, 'Low.ini'), encoding='utf-8', errors='replace') as f:
        apply_ini('SkyrimPrefs.ini', PREFS_INI, f.read())
    plugin_dir = os.path.join(LOCAL_APPDATA, 'Skyrim Special Edition')
    os.makedirs(plugin_dir, exist_ok=True)
    with open(os.path.join(plugin_dir, 'Plugins.txt'), 'w', encoding='utf-8') as f:
        f.write(''.join('*%s\n' % name for name in plugins))


def link_mod(name: str, data: str) -> list:
    """Links the converted `output/<name>` tree into `data`; the plugins at its root.

    The root's other files (manifests, voice maps) are build records, not game data.
    """
    root = os.path.join(REPO, 'output', name)
    if not os.path.isdir(root):
        sys.exit('no converted output at %s' % root)
    plugins = []
    for folder, _, files in os.walk(root):
        rel = os.path.relpath(folder, root)
        for file in files:
            is_plugin = file.lower().endswith(('.esm', '.esp', '.esl'))
            if rel == '.' and not is_plugin:
                continue
            if rel == '.':
                plugins.append(file)
            link(os.path.join(folder, file), os.path.normpath(os.path.join(data, rel, file)))
    return plugins


def link_runtime(data: str, plugins: list) -> None:
    """Links the runtime DLLs and their INIs, and writes the menus and tables the package composes,
    with the chargen of the first of `plugins` that has one (else the config's)."""
    required, optional = MODS[MOD_NAME]
    for source, target in required + optional:
        if os.path.isfile(source):
            link(str(source), os.path.join(data, str(target)))
    games = {name.lower() for name in candidates(EXPORT_ROOT)}
    chosen = next((name for name in plugins if name.lower() in games), None)
    composed = ((morrowind_menus(EXPORT_ROOT, configured_choice(), chosen) or [])
                + chargen_table(EXPORT_ROOT, chosen))
    for target, payload in composed:
        path = os.path.join(data, str(target))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.remove(path)
        with open(path, 'wb') as f:
            f.write(payload)
    print('wrote %d composed menu and table file(s)' % len(composed))


def link_skse_scripts(data: str) -> int:
    """Links the SKSE release's Papyrus scripts into `data`; how many it linked."""
    scripts = glob.glob(os.path.join(SKSE_RELEASE, '*', 'Data', 'Scripts', '*.pex'))
    for path in scripts:
        link(path, os.path.join(data, 'Scripts', os.path.basename(path)))
    if not scripts:
        print('no SKSE scripts under %s: extract the SKSE release archive there' % SKSE_RELEASE)
    return len(scripts)


def loadable(data: str, plugins: list) -> list:
    """The `plugins` whose every master is in `data`, so a linked folder's dev patch
    for a game not in the instance stays off."""
    keep = []
    for name in plugins:
        missing = [m for m in plugin_masters(os.path.join(data, name)) if not os.path.exists(os.path.join(data, m))]
        if missing:
            print('not enabling %s: missing master(s) %s' % (name, ', '.join(missing)))
        else:
            keep.append(name)
    return keep


def unlink_plugins(data: str) -> None:
    """Removes plugin hard links an earlier build left in `data`."""
    for path in glob.glob(os.path.join(data, '*.es[mpl]')):
        if os.stat(path).st_nlink > 1:
            os.remove(path)


def build(steam_exe: bool, plugins, mods=()) -> None:
    """Links the vanilla game, SKSE, the bridge and each converted mod, and writes the profile."""
    root = steam_dir()
    data = os.path.join(INSTANCE, 'Data')
    unlink_plugins(data)
    enabled = list(plugins)
    for mod in mods:
        enabled += link_mod(mod, data)
    if mods:
        link_runtime(data, enabled)
    link(os.path.join(root, 'SkyrimSE.exe') if steam_exe else UNPACKED_EXE,
         os.path.join(INSTANCE, 'SkyrimSE.exe'))
    for name in ROOT_FILES:
        link(os.path.join(root, name), os.path.join(INSTANCE, name))
    archives = [os.path.basename(p) for p in glob.glob(os.path.join(root, 'Data', 'Skyrim - *.bsa'))]
    for name in list(DATA_FILES) + archives + list(plugins):
        link(os.path.join(root, 'Data', name), os.path.join(data, name))
    link(os.path.join(root, 'Data', ADDRESS_LIBRARY), os.path.join(data, ADDRESS_LIBRARY))
    link(BRIDGE_DLL, os.path.join(data, 'SKSE', 'Plugins', 'TESGameBridge.dll'))
    link_skse_scripts(data)
    with open(os.path.join(INSTANCE, 'steam_appid.txt'), 'w') as f:
        f.write(APP_ID)
    enabled = loadable(data, enabled)
    write_profile(root, enabled)
    share_saves()
    print('built %s (%s exe, %d archives, plugins: %s)' % (
        INSTANCE, 'Steam' if steam_exe else 'unpacked', len(archives), ', '.join(enabled) or 'none'))


def running_pid() -> int:
    """The pid of the running test instance, or 0."""
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return 0
    return pid if hidden_launch.process_image(pid) else 0


def launch(extra_env=None) -> None:
    """Starts the instance hidden, muted, sandboxed and under SKSE; refuses a second copy.

    The INI settings are re-applied first, because the game rewrites
    SkyrimPrefs.ini on every run. `extra_env` adds variables for the game.
    """
    if running_pid():
        sys.exit('test instance already running (pid %d)' % running_pid())
    apply_ini('Skyrim.ini', SKYRIM_INI)
    apply_ini('SkyrimPrefs.ini', PREFS_INI)
    env = {'SteamAppId': APP_ID, 'SteamGameId': APP_ID, 'TES_BRIDGE_PIPE': PIPE,
           'TES_SANDBOX_MUTE': '1',
           'TES_SANDBOX_DOCUMENTS': DOCUMENTS, 'TES_SANDBOX_LOCALAPPDATA': LOCAL_APPDATA,
           **(extra_env or {})}
    pid = hidden_launch.launch(os.path.join(INSTANCE, 'SkyrimSE.exe'), INSTANCE, DESKTOP,
                               [SANDBOX_DLL, (os.path.join(INSTANCE, SKSE_DLL), 1)], env)
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    print('launched pid %d on hidden desktop %s' % (pid, DESKTOP))


def tail(path: str, lines: int, encoding: str = 'utf-8') -> list:
    """The last `lines` lines of `path`, or [] when it does not exist."""
    try:
        with open(path, encoding=encoding, errors='replace') as f:
            return f.read().splitlines()[-lines:]
    except OSError:
        return []


def status() -> int:
    """Prints whether the instance runs, its log tails and a bridge ping; 0 when it answers."""
    pid = running_pid()
    print('process: %s' % ('pid %d' % pid if pid else 'not running'))
    for log in (os.path.join(DOCUMENTS, 'TESSandbox.log'),
                os.path.join(MY_GAMES, 'SKSE', 'skse64.log'),
                os.path.join(MY_GAMES, 'SKSE', 'TESGameBridge.log')):
        print('--- %s' % log)
        for line in tail(log, 8):
            print('    ' + line)
    try:
        with Bridge(pipe=PIPE, timeout=5) as bridge:
            print('bridge: %s' % bridge.status())
            return 0
    except BridgeError as err:
        print('bridge: %s' % err)
        return 1


def shot(path: str) -> None:
    """Saves a screenshot of the hidden game window to `path`."""
    image = hidden_launch.capture(DESKTOP, WINDOW_CLASS)
    if image is None:
        sys.exit('no game window on desktop %s' % DESKTOP)
    image.save(path)
    print('saved %s' % path)


def _read_u(bridge: Bridge, address: int, size: int) -> int:
    """The little-endian unsigned `size`-byte value at `address` in the game."""
    return int.from_bytes(bytes.fromhex(bridge.readmem(address=address, length=size)['hex']), 'little')


def open_menus(bridge: Bridge, with_address: bool = False) -> list:
    """(RTTI class, flags) of every open menu, bottom of the stack first; with
    `with_address`, (class, flags, IMenu address).

    Layout: MenuManager singleton id 400327, IMenu* array at +0x110 and count
    at +0x120 (tes_runtime/tes/ids.h); IMenu flags at +0x1C, bit 0 pauses the game.
    The RTTI offsets are relative to the module defining the class -- a plugin's
    menu lives in its DLL -- and the locator's own offset at +0x14 gives that base.
    """
    manager = _read_u(bridge, int(bridge.readmem(id=MENU_MANAGER_ID, length=8)['address']), 8)
    stack = _read_u(bridge, manager + 0x110, 8)
    menus = []
    for i in range(_read_u(bridge, manager + 0x120, 4)):
        menu = _read_u(bridge, stack + 8 * i, 8)
        locator = _read_u(bridge, _read_u(bridge, menu, 8) - 8, 8)
        base = locator - _read_u(bridge, locator + 0x14, 4)
        type_name = base + _read_u(bridge, locator + 12, 4) + 16
        name = bridge.readmem(address=type_name, length=96, as_string=True)['string']
        entry = (name[4:-2], _read_u(bridge, menu + 0x1C, 4))
        menus.append(entry + (menu,) if with_address else entry)
    return menus


def press_message_button(bridge: Bridge, button: int) -> bool:
    """Clicks `button` of the open MessageBoxMenu through its own `buttonPress`
    callback, so a Papyrus `Message.Show` gets its answer; False with no box open.

    FxDelegateArgs: the handler (the menu) at +0x18, the GFxValue array at
    +0x28 and its count at +0x30; the first value is a number (type 3 at +0x8)
    whose double (+0x10) is the button index.
    """
    boxes = [menu for name, _flags, menu in open_menus(bridge, True) if name == 'MessageBoxMenu']
    if not boxes:
        return False
    args = bridge.alloc(0x60)
    value = args + 0x40
    bridge.writemem(args, bytes(0x60))
    bridge.writemem(args + 0x18, struct.pack('<Q', boxes[-1]))
    bridge.writemem(args + 0x28, struct.pack('<QI', value, 1))
    bridge.writemem(value + 0x8, struct.pack('<Iid', 3, 0, float(button)))
    bridge.call(id=BUTTON_PRESS_ID, args_=[args])
    return True


def close_menu(bridge: Bridge, name: str) -> None:
    """Posts the engine's force-hide message to menu `name`, as PostMenuMessage does.

    Same calls as tes_runtime/common/ui_message.cpp: intern the name with the
    BSFixedString constructor (AddMessage compares by pointer), then
    UIManager::AddMessage(manager, &name, 4, null).
    """
    slot = bridge.alloc(16)
    bridge.writemem(slot, bytes(16))
    bridge.call(id=FIXED_STRING_CTOR_ID, args_=[slot, bridge.put_string(name)])
    if not _read_u(bridge, slot, 8):
        sys.exit('interning %r produced nothing' % name)
    manager = _read_u(bridge, int(bridge.readmem(id=UI_MANAGER_ID, length=8)['address']), 8)
    bridge.call(id=UI_ADD_MESSAGE_ID, args_=[manager, slot, MESSAGE_FORCE_HIDE, 0])


def dismiss_popups(bridge: Bridge) -> list:
    """Force-hides each open startup popup; the registered names it closed.

    The Bethesda.net login box still appears on some launches despite the INIs.
    See: docs/commentary/headless_testing.md#startup-popups
    """
    open_now = {name for name, flags in open_menus(bridge) if flags & 1}
    closed = [menu for rtti, menu in STARTUP_POPUPS if rtti in open_now]
    for menu in closed:
        close_menu(bridge, menu)
    return closed


def close_named(name: str) -> None:
    """Closes menu `name` in the running instance and prints the menus left open."""
    with Bridge(pipe=PIPE, timeout=10) as bridge:
        close_menu(bridge, name)
        print_menus(bridge)


def print_menus(bridge: Bridge) -> None:
    """Prints the open menus, marking each one that pauses the game."""
    for name, flags in open_menus(bridge):
        print('%-24s flags=%#07x%s' % (name, flags, '  PAUSES GAME' if flags & 1 else ''))


def menus() -> None:
    """Connects and prints the open menus."""
    with Bridge(pipe=PIPE, timeout=10) as bridge:
        print_menus(bridge)


def connect(seconds: float) -> Bridge:
    """A bridge connection once the game answers `ping`, polling up to `seconds`."""
    deadline = time.time() + seconds
    while True:
        try:
            bridge = Bridge(pipe=PIPE, timeout=30).connect()
            bridge.ping()
            return bridge
        except BridgeError:
            if time.time() > deadline:
                raise
            time.sleep(1)


def game_hour(bridge: Bridge) -> float:
    """The GameHour global, read through the console."""
    return float(bridge.console('show GameHour').split('=')[1])


def enter_cell(bridge: Bridge, cell: str, settle: float) -> None:
    """Waits `settle` s at the main menu, then coc into `cell`, dismissing startup popups."""
    time.sleep(settle)
    closed = dismiss_popups(bridge)
    bridge.console('coc %s' % cell)
    bridge.wait_ready(60000)
    closed += dismiss_popups(bridge)
    if closed:
        print('dismissed startup popup(s): %s' % ', '.join(closed))


def smoke(cell: str, settle: float) -> int:
    """Checks the launched instance end to end; 0 when every check passes.

    Waits `settle` s at the main menu (startup popups arrive late), then coc
    into `cell` with no input, no menu pausing the game, game time advancing
    (timescale 1000 for 2 s), and a screenshot.
    """
    started = time.time()
    with connect(120) as bridge:
        print('bridge up after %.1fs' % (time.time() - started))
        enter_cell(bridge, cell, settle)
        pausing = [name for name, flags in open_menus(bridge) if flags & 1]
        bridge.console('set timescale to 1000')
        before = game_hour(bridge)
        time.sleep(2)
        advanced = game_hour(bridge) - before
    shot(os.path.join(PROFILE, 'smoke.png'))
    checks = {'no menu pauses the game %s' % pausing: not pausing,
              'game time advanced %.3f h in 2 s' % advanced: advanced > 0.1}
    for name, ok in checks.items():
        print('%s  %s' % ('ok  ' if ok else 'FAIL', name))
    return 0 if all(checks.values()) else 1


#: Address Library ids of the two per-frame deltas: game time (scaled) and real time.
FRAME_DELTA_IDS = (('game delta', 410199), ('real delta', 410200))


def frame_deltas(bridge: Bridge) -> dict:
    """The engine's last frame deltas, in seconds, by name."""
    out = {}
    for name, stable_id in FRAME_DELTA_IDS:
        raw = bytes.fromhex(bridge.readmem(id=stable_id, length=4)['hex'])
        out[name] = struct.unpack('<f', raw)[0]
    return out


@contextlib.contextmanager
def ini_overrides(settings: list):
    """Sets each `File.ini:Section:key=value` in the profile's INI of that name for
    the block, then restores each file, so one run's settings never outlive it."""
    saved = {}
    for setting in settings:
        name, section, entry = setting.split(':', 2)
        key, value = entry.split('=', 1)
        path = os.path.join(MY_GAMES, name)
        if name not in saved:
            with open(path, encoding='utf-8', errors='replace') as f:
                saved[name] = f.read()
        apply_ini(name, [(section, key, value)])
    try:
        yield
    finally:
        for name, text in saved.items():
            with open(os.path.join(MY_GAMES, name), 'w', encoding='utf-8') as f:
                f.write(text)


def speed(cell: str, settle: float, commands: list, seconds: float) -> None:
    """Launches, coc, runs console `commands`, and prints game seconds per real
    second over `seconds`, with sampled frame deltas.
    See: docs/commentary/headless_testing.md#speed
    """
    launch()
    try:
        with connect(120) as bridge:
            enter_cell(bridge, cell, settle)
            for command in commands:
                print('%s -> %s' % (command, bridge.console(command).strip()))
            scale = float(bridge.console('show TimeScale').split('=')[1])
            started, before = time.time(), game_hour(bridge)
            samples = []
            while time.time() - started < seconds:
                samples.append(frame_deltas(bridge))
                time.sleep(0.5)
            elapsed, advanced = time.time() - started, game_hour(bridge) - before
    finally:
        stop()
    print('game seconds per real second: %.2f (timescale %g)' % (advanced * 3600 / scale / elapsed, scale))
    for name, _id in FRAME_DELTA_IDS:
        values = sorted(s[name] for s in samples)
        print('%s: median %.4f s (%.1f per second), min %.4f, max %.4f' % (
            name, values[len(values) // 2], 1 / max(values[len(values) // 2], 1e-6), values[0], values[-1]))


#: Settings `--fast` adds: uncapped frames of a fixed game step, which the runtime then steers to ~25x real time.
FAST_INI = ['Skyrim.ini:General:iFPSClamp=60', 'Skyrim.ini:Display:iVSyncPresentInterval=0',
            'Skyrim.ini:Display:bLockFramerate=0']
#: Settings `questtest` adds: the Papyrus log, and subtitles, without which no spoken line is recorded.
PAPYRUS_INI = ['Skyrim.ini:Papyrus:bEnableLogging=1', 'SkyrimPrefs.ini:Interface:bGeneralSubtitles=1',
               'SkyrimPrefs.ini:Interface:bDialogueSubtitles=1']


def checkpoint_name(case: str) -> str:
    """The sandbox save made after `case` passed."""
    return 'mwtest_' + re.sub(r'\W', '_', case)


def file_state(path: str) -> tuple:
    """`path`'s (mtime, size), or (0, 0) when it does not exist."""
    return (os.path.getmtime(path), os.path.getsize(path)) if os.path.exists(path) else (0, 0)


def saved(bridge: Bridge, name: str, seconds: float = 15) -> bool:
    """Saves the game as `name` in the sandbox; whether the save file was written
    and then held still for a poll within `seconds`.

    See: docs/commentary/headless_testing.md#quest-runs
    """
    path = os.path.join(MY_GAMES, 'Saves', name + '.ess')
    before = file_state(path)
    bridge.console('save %s' % name)
    deadline = time.time() + seconds
    last = before
    while time.time() < deadline:
        time.sleep(0.5)
        now = file_state(path)
        if now != before and now == last:
            return True
        last = now
    return False


def run_console(bridge: Bridge, text: str) -> str:
    """Runs a console line, `<FormID>.<command>` on that reference; its output, or the error."""
    on_ref = re.match(r'([0-9A-Fa-f]{8})\.(.+)$', text)
    try:
        if on_ref:
            return bridge.console(on_ref.group(2), ref=int(on_ref.group(1), 16)).strip()
        return bridge.console(text).strip()
    except BridgeError as error:
        return 'ERROR %s' % error


def handle_lines(bridge: Bridge, results: str, lines: list, handled: int, checkpoints: bool,
                 respond=None) -> int:
    """Runs each new console request and, with `checkpoints`, saves after each newly
    passed case (retrying once), then acknowledges them to the runner; the lines handled.

    `respond(line)` sees every line first; text it returns is the line's answer,
    written to `<results>.answer` before the ack.
    """
    for line in lines[handled:]:
        reply = respond(line) if respond else None
        if reply is not None:
            with open(results + '.answer', 'w', encoding='utf-8') as f:
                f.write(reply)
        elif 'console' in line:
            print('console %s -> %s' % (line['console'], run_console(bridge, line['console'])))
        elif checkpoints and line.get('ok'):
            name = checkpoint_name(line['case'])
            ok = saved(bridge, name) or saved(bridge, name)
            print('checkpoint %s%s' % (name, '' if ok else ' -- NOT SAVED'))
    if len(lines) > handled:
        with open(results + '.ack', 'w', encoding='utf-8') as f:
            f.write(str(len(lines)))
    return len(lines)


def wait_results(path: str, seconds: float, bridge: Bridge, checkpoints: bool, game) -> list:
    """The result lines in `path` once its `done` line lands, the game exits, or
    `seconds` pass, answering the runner's requests on the way (`game.respond`)."""
    deadline = time.time() + seconds
    lines, handled = [], 0
    respond = game.respond(bridge) if game.respond else None
    while time.time() < deadline:
        lines = [json.loads(l) for l in tail(path, 10000, game.encoding) if l.strip()]
        handled = handle_lines(bridge, path, lines, handled, checkpoints, respond)
        if lines and lines[-1].get('done'):
            break
        if not running_pid():
            print('GAME EXITED after %d result line(s)' % len(lines))
            break
        time.sleep(game.poll)
    return lines


def case_names(path: str) -> list:
    """The case names in case file `path`, in order."""
    with open(path, encoding='utf-8') as f:
        return re.findall(r'(?im)^\s*case\s+(.+?)\s*$', f.read())


def _case_start(text: str, name: str, path: str) -> int:
    """Where case `name` begins in case-file `text`."""
    found = re.search(r'(?im)^\s*case\s+%s\s*$' % re.escape(name), text)
    if not found:
        raise SystemExit('no case %r in %s' % (name, path))
    return found.start()


def cases_from(path: str, first: str, last: str = None) -> str:
    """A copy of case file `path` in the profile from case `first` (else the start) through `last` (else the end)."""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    start = _case_start(text, first, path) if first else 0
    end = len(text)
    if last:
        names = case_names(path)
        after = names.index(last) + 1 if last in names else None
        if after is None:
            raise SystemExit('no case %r in %s' % (last, path))
        end = _case_start(text, names[after], path) if after < len(names) else len(text)
    out = os.path.join(PROFILE, 'cases_from.mwtest')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text[start:end])
    return out


def wait_message_box(bridge: Bridge, seconds: float) -> bool:
    """Whether a MessageBoxMenu opens within `seconds`."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if any(name == 'MessageBoxMenu' for name, _flags in open_menus(bridge)):
            return True
        time.sleep(0.5)
    return False


def start_new_game(bridge: Bridge, settle: float, game: int) -> None:
    """Clicks New at the main menu and answers TESGameSelect's prompt with button `game`."""
    time.sleep(settle)
    dismiss_popups(bridge)
    bridge.call(id=NEW_GAME_ID, args_=[0])
    bridge.wait_ready(120000)
    if not wait_message_box(bridge, 120):
        raise SystemExit('no game selector prompt after New Game; is TESGameSelect.esp linked?')
    time.sleep(1)
    press_message_button(bridge, game)


def save_to_load(args, cases: str) -> str:
    """The sandbox save a run starts from: `--load`, else the checkpoint before `--from`, else ''."""
    if not args.from_case:
        return args.load or ''
    names = case_names(cases)
    if names.index(args.from_case) == 0:
        raise SystemExit('--from names the first case; run without it')
    return checkpoint_name(names[names.index(args.from_case) - 1])


def enter_game(bridge: Bridge, args, cases: str) -> None:
    """Into the world for a run: a sandbox save (`--load`, `--from`), a New Game
    with `--new-game`, else coc `--cell`."""
    save = save_to_load(args, cases)
    if save:
        if not os.path.isfile(os.path.join(MY_GAMES, 'Saves', save + '.ess')):
            raise SystemExit('no save %s in the sandbox; make it with --checkpoints' % save)
        time.sleep(args.settle)
        dismiss_popups(bridge)
        bridge.console('load %s' % save)
        bridge.wait_ready(120000)
    elif args.new_game is not None:
        start_new_game(bridge, args.settle, args.new_game)
    else:
        enter_cell(bridge, args.cell, args.settle)
    if args.fast:
        bridge.console('tgm')


def join_cases(path: str, ext: str) -> str:
    """`path` itself, or for a folder every `*<ext>` in it, by name, joined into the profile."""
    if os.path.isfile(path):
        return os.path.abspath(path)
    joined = os.path.join(PROFILE, 'cases' + ext)
    with open(joined, 'w', encoding='utf-8') as out:
        for name in sorted(glob.glob(os.path.join(path, '*' + ext))):
            with open(name, encoding='utf-8') as f:
                out.write(f.read() + '\n')
    return joined


@dataclasses.dataclass
class CaseGame:
    """Which runtime runs a case run, and what the harness does for it."""

    prefix: str
    ext: str
    log: str
    poll: float
    #: (bridge) -> respond(line), the answers to the runner's requests (handle_lines).
    respond: object = None
    #: (case file) -> the case file the runner reads.
    prepare: object = None
    #: How the runner's results file is encoded.
    encoding: str = 'utf-8'


#: MorrowindRuntime runs MWScript itself; the harness only runs console lines.
MW_GAME = CaseGame('TES_MW_TEST_', '.mwtest', 'MorrowindRuntime.log', 0.5)


def tes4_game() -> CaseGame:
    """TESRuntime's runner for converted TES4 plugins: the harness resolves names,
    runs console lines and judges conditions, and reports each case's Papyrus errors.
    See: docs/commentary/headless_testing.md#tes4-quest-cases
    """
    names = tes4_cases.build_names(os.path.join(LOCAL_APPDATA, 'Skyrim Special Edition', 'Plugins.txt'))

    def respond(bridge: Bridge):
        """The line handler for one run; reads the Papyrus log from its start."""
        papyrus = tes4_cases.PapyrusLog(os.path.join(MY_GAMES, 'Logs', 'Script', 'Papyrus.0.log'))

        def reply(line: dict):
            """A request's answer; None for every other line, printing spoken lines and noting a case's
            Papyrus errors."""
            if 'speaker' in line:
                tes4_cases.print_spoken(line, names)
            if 'case' in line:
                line['papyrus'] = papyrus.new_errors()
                for error in line['papyrus']:
                    print('  papyrus: %s' % error)
            return tes4_cases.answer(bridge, line, names) if 'run' in line or 'judge' in line else None
        return reply

    prepared = os.path.join(PROFILE, 'cases_prepared.tes4test')
    return CaseGame('TES_TEST_', '.tes4test', 'TESRuntime.log', 0.1, respond,
                    lambda cases: tes4_cases.prepare(cases, names, prepared), tes4_cases.GAME_ENCODING)


def mwtest(args) -> int:
    """Runs MWScript cases under MorrowindRuntime (test_runner.cpp)."""
    return run_cases(args, MW_GAME)


def run_cases(args, game: CaseGame) -> int:
    """Launches with `game`'s case file, enters the game, reports, stops unless `--keep`; 0 when all pass.

    The runtime runs the cases from its tick once the player is in a cell and
    writes one JSON line per case. With `--checkpoints` the harness saves after
    each passing case.
    See: docs/commentary/headless_testing.md#quest-runs
    """
    results = os.path.join(PROFILE, 'mwtest.jsonl')
    for stale in (results, results + '.ack', results + '.answer'):
        if os.path.exists(stale):
            os.remove(stale)
    cases = join_cases(args.cases, game.ext)
    chosen = cases_from(cases, args.from_case, args.to_case) if args.from_case or args.to_case else cases
    env = {game.prefix + 'CASES': game.prepare(chosen) if game.prepare else chosen,
           game.prefix + 'RESULTS': results}
    flags = {game.prefix + 'STOP_ON_FAIL': args.stop_on_fail, game.prefix + 'CHECKPOINTS': args.checkpoints}
    env.update({name: '1' for name, on in flags.items() if on})
    launch(env)
    try:
        with connect(120) as bridge:
            enter_game(bridge, args, cases)
            lines = wait_results(results, args.timeout, bridge, args.checkpoints, game)
    finally:
        if not args.keep:
            stop()
    return report(lines, results, game)


def report(lines: list, results: str, game: CaseGame) -> int:
    """Prints each case and the summary; 0 when the run finished with no failure."""
    for line in lines:
        if 'case' in line:
            print('%s  %s%s' % ('ok  ' if line['ok'] else 'FAIL', line['case'],
                                '' if line['ok'] else '  ' + json.dumps(line)))
    done = lines[-1] if lines and lines[-1].get('done') else None
    if done:
        print('%s passed, %s failed, %s skipped; %d real command(s) no case names'
              % (done['passed'], done['failed'], done['skipped'], len(done.get('uncovered', []))))
    else:
        print('runner did not finish; see %s and the %s log' % (results, game.log))
    passed = bool(done) and not done['failed']
    if not passed:
        keep_failed_log(game.log)
    return 0 if passed else 1


def keep_failed_log(name: str) -> None:
    """Copies the runtime log `name` into the profile, safe from the next launch."""
    kept = os.path.join(PROFILE, 'mwtest_failed.log')
    shutil.copyfile(os.path.join(MY_GAMES, 'SKSE', name), kept)
    print('runtime log kept at %s' % kept)


def stop() -> None:
    """Terminates the test instance; never any process outside the instance folder."""
    pid = running_pid()
    if not pid:
        print('not running')
        return
    print('stopped pid %d' % pid if hidden_launch.terminate(pid, INSTANCE)
          else 'pid %d is not the test instance; left alone' % pid)


def _launched_parsers(sub) -> None:
    """The subcommands that launch the game: smoke, mwtest, questtest and speed."""
    m = sub.add_parser('smoke', help='coc into a cell and check the instance runs unattended')
    w = sub.add_parser('mwtest', help='launch, run MWScript opcode cases in game, stop')
    q = sub.add_parser('questtest', help='launch, run converted TES4 (.tes4test) cases in game, stop')
    p = sub.add_parser('speed', help='measure game seconds per real second under INI and console settings')
    for each in (m, w, q, p):
        each.add_argument('--ini', action='append', default=[],
                          help='File.ini:Section:key=value set in the profile for this run only')
        each.add_argument('--cell', default='QASmoke')
        each.add_argument('--settle', type=float, default=10, help='seconds at the main menu before coc')
    w.add_argument('--cases', default=os.path.join(REPO, 'tes_runtime', 'morrowind', 'testdata', 'ingame'),
                   help='a case file, or a folder whose *.mwtest files run in name order')
    q.add_argument('--cases', required=True,
                   help='a case file, or a folder whose *.tes4test files run in name order')
    for each in (w, q):
        _case_options(each)
    p.add_argument('--console', action='append', default=[], help='console command run after coc')
    p.add_argument('--seconds', type=float, default=10)


def _case_options(parser) -> None:
    """The options every case run takes."""
    parser.add_argument('--timeout', type=float, default=60, help='seconds to wait for the results')
    parser.add_argument('--keep', action='store_true', help='leave the instance running afterwards')
    parser.add_argument('--stop-on-fail', action='store_true', help='end the run at the first failing case')
    parser.add_argument('--new-game', type=int, metavar='BUTTON',
                        help="click New Game and answer TESGameSelect's prompt with this button")
    parser.add_argument('--fast', action='store_true', help='uncapped frames of a fixed 1/60 s (about 27x in QASmoke) and god mode')
    parser.add_argument('--checkpoints', action='store_true', help='save in the sandbox after each passing case')
    parser.add_argument('--from', dest='from_case', help='load the save of the case before this one and resume')
    parser.add_argument('--to', dest='to_case', help='stop after this case (`--from X --to X` runs just X)')
    parser.add_argument('--load', help='start from this sandbox save (e.g. mwtest_chargen_class)')


def parse_args():
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--instance', type=int, default=1,
                        help='which test instance: 1 is the first; N > 1 is "Skyrim Test N", run in parallel')
    sub = parser.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build', help='link the instance and write its profile')
    b.add_argument('--steam-exe', action='store_true', help="use Steam's exe, not the unpacked one")
    b.add_argument('--plugin', action='append', default=[], help='plugin to link and enable')
    b.add_argument('--mod', action='append', default=[],
                   help='converted output/ folder to link, plugins enabled, with the runtime DLLs')
    s = sub.add_parser('shot', help='screenshot the hidden game window')
    s.add_argument('--out', help="PNG path; the instance profile's shot.png by default")
    _launched_parsers(sub)
    c = sub.add_parser('close', help="post the engine's close message to a menu")
    c.add_argument('menu', help='registered menu name, e.g. MessageBoxMenu')
    for name in ('launch', 'status', 'menus', 'stop'):
        sub.add_parser(name)
    return parser.parse_args()


def main() -> int:
    """Command-line entry point."""
    args = parse_args()
    use_instance(args.instance)
    extra =(FAST_INI if getattr(args, 'fast', False) else []) + (PAPYRUS_INI if args.cmd == 'questtest' else [])
    with ini_overrides(getattr(args, 'ini', []) + extra):
        return run(args)


def run(args) -> int:
    """Runs the parsed subcommand; its exit code."""
    actions = {'build': lambda: build(args.steam_exe, args.plugin, args.mod), 'launch': launch,
               'shot': lambda: shot(args.out or os.path.join(PROFILE, 'shot.png')), 'menus': menus, 'stop': stop,
               'close': lambda: close_named(args.menu),
               'speed': lambda: speed(args.cell, args.settle, args.console, args.seconds)}
    if args.cmd == 'mwtest':
        return mwtest(args)
    if args.cmd == 'questtest':
        return run_cases(args, tes4_game())
    if args.cmd in ('status', 'smoke'):
        return status() if args.cmd == 'status' else smoke(args.cell, args.settle)
    actions[args.cmd]()
    return 0


if __name__ == '__main__':
    sys.exit(main())
