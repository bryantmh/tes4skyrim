"""An isolated Skyrim for headless, automated in-game tests.

    python -m tools.live.test_instance checkout --who "<task>"   # then --instance N --token T on every command
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
from tools.live import crash_capture, fail_snapshot, hidden_launch, instance_lock, obs_record, silences, tes4_cases
from tools.live.game_bridge import Bridge, BridgeError
from tools.navmesh import navmesh_rescue
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
LOADING_MENU = 'LoadingMenu'
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
#: Tick unfocused, no intro, no Bethesda.net "Thanks for buying" box, no idle camera orbit around the player.
SKYRIM_INI = (('General', 'bAlwaysActive', '1'), ('General', 'sIntroSequence', ''),
              ('Bethesda.net', 'bEnablePlatform', '0'), ('Bethesda.net', 'bAutoSkipMainMenuLogin', '1'),
              ('Camera', 'fAutoVanityModeDelay', '1000000'))
#: Low preset + a small hidden window; FULL volume (sandbox mutes): docs/commentary/headless_testing.md#sandbox-mute
PREFS_INI = (('Display', 'bFull Screen', '0'), ('Display', 'bBorderless', '1'),
             ('Display', 'iSize W', '640'), ('Display', 'iSize H', '360'),
             ('AudioMenu', 'fAudioMasterVolume', '1.0000'), ('General', 'bUpsellOwned', '1'))
#: The window size of a recorded run (`--record`), width then height.
RECORD_SIZE = (('Display', 'iSize W', '2560'), ('Display', 'iSize H', '1440'))
#: Skyrim.ini lines of a recorded run: field of view 90 (the user's choice; the game's own is ~65).
RECORD_SKYRIM_INI = (('Display', 'fDefaultWorldFOV', '90'), ('Display', 'fDefault1stPersonFOV', '90'))


def instance_folder(slot: int) -> str:
    """Test instance `slot`'s game folder (1 = the first)."""
    return FIRST_INSTANCE + ('' if slot == 1 else ' %d' % slot)


def my_games(slot: int) -> str:
    """Test instance `slot`'s My Games folder (SKSE logs, Saves)."""
    return os.path.join(instance_folder(slot), '_profile', 'Documents', 'My Games', 'Skyrim Special Edition')


def use_instance(slot: int) -> None:
    """Points the folder, profile, bridge pipe and hidden desktop at test instance `slot` (1 = the first).

    See: docs/commentary/headless_testing.md#parallel-instances
    """
    global INSTANCE, PROFILE, DOCUMENTS, LOCAL_APPDATA, MY_GAMES, PID_FILE, PIPE, DESKTOP
    suffix = '' if slot == 1 else ' %d' % slot
    INSTANCE = instance_folder(slot)
    PROFILE = os.path.join(INSTANCE, '_profile')
    DOCUMENTS = os.path.join(PROFILE, 'Documents')
    LOCAL_APPDATA = os.path.join(PROFILE, 'LocalAppData')
    MY_GAMES = my_games(slot)
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


def write_prefs(root: str, preset: str, extra=()) -> None:
    """SkyrimPrefs.ini from graphics preset file `preset`, PREFS_INI and `extra`."""
    with open(os.path.join(root, preset), encoding='utf-8', errors='replace') as f:
        apply_ini('SkyrimPrefs.ini', PREFS_INI + tuple(extra), f.read())


def write_skyrim_ini(root: str) -> None:
    """Skyrim.ini from the game's Skyrim_Default.ini and SKYRIM_INI."""
    with open(os.path.join(root, 'Skyrim_Default.ini'), encoding='utf-8', errors='replace') as f:
        apply_ini('Skyrim.ini', SKYRIM_INI, f.read())


def write_profile(root: str, plugins) -> None:
    """Writes the sandbox profile: Skyrim.ini, SkyrimPrefs.ini and Plugins.txt."""
    os.makedirs(MY_GAMES, exist_ok=True)
    write_skyrim_ini(root)
    write_prefs(root, 'Low.ini')
    plugin_dir = os.path.join(LOCAL_APPDATA, 'Skyrim Special Edition')
    os.makedirs(plugin_dir, exist_ok=True)
    with open(os.path.join(plugin_dir, 'Plugins.txt'), 'w', encoding='utf-8') as f:
        f.write(''.join('*%s\n' % name for name in plugins))


def link_mod(name: str, data: str) -> list:
    """Links the converted `output/<name>` tree into `data` and COPIES the plugins at its root.

    The game holds a plugin open, so a linked one made the next import fail at
    its final rename. The root's other files are build records, not game data.
    See: docs/commentary/headless_testing.md#parallel-instances
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
            place = copy_runtime_file if rel == '.' else link
            if rel == '.':
                plugins.append(file)
            place(os.path.join(folder, file), os.path.normpath(os.path.join(data, rel, file)))
    return plugins


def copy_runtime_file(src: str, dst: str) -> None:
    """Copies `src` to `dst`, so a runtime rebuild or another instance never shares this instance's file.

    See: docs/commentary/headless_testing.md#parallel-instances
    """
    if os.path.exists(dst):
        os.remove(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def link_runtime(data: str, plugins: list) -> None:
    """Copies the runtime DLLs and their INIs, and writes the menus and tables the package composes,
    with the chargen of the first of `plugins` that has one (else the config's)."""
    required, optional = MODS[MOD_NAME]
    for source, target in required + optional:
        if os.path.isfile(source):
            copy_runtime_file(str(source), os.path.join(data, str(target)))
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


def link_extra_skse(data: str, dlls) -> None:
    """Links third-party SKSE plugin `dlls` into `data`, first removing those an earlier build linked."""
    listed = os.path.join(PROFILE, 'extra_skse_plugins.txt')
    folder = os.path.join(data, 'SKSE', 'Plugins')
    old = open(listed, encoding='utf-8').read().split() if os.path.exists(listed) else []
    for name in old:
        if os.path.exists(os.path.join(folder, name)):
            os.remove(os.path.join(folder, name))
    for dll in dlls:
        link(dll, os.path.join(folder, os.path.basename(dll)))
    os.makedirs(PROFILE, exist_ok=True)
    with open(listed, 'w', encoding='utf-8') as f:
        f.write('\n'.join(os.path.basename(dll) for dll in dlls))


def build(steam_exe: bool, plugins, mods=(), skse_plugins=()) -> None:
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
    copy_runtime_file(BRIDGE_DLL, os.path.join(data, 'SKSE', 'Plugins', 'TESGameBridge.dll'))
    link_extra_skse(data, skse_plugins)
    link_skse_scripts(data)
    if os.path.exists(os.path.join(INSTANCE, 'steam_appid.txt')):
        os.remove(os.path.join(INSTANCE, 'steam_appid.txt'))
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


def slot_running(slot: int) -> bool:
    """Whether instance `slot`'s game runs; points the module at that instance."""
    use_instance(slot)
    return bool(running_pid())


def launch(extra_env=None, prefs=(), skyrim=()) -> None:
    """Starts the instance hidden, muted, sandboxed and under SKSE; refuses a second copy.

    The INI settings are re-applied first, because the game rewrites
    SkyrimPrefs.ini on every run. `extra_env` adds variables for the game; `prefs` overrides PREFS_INI and
    `skyrim` SKYRIM_INI.
    """
    if running_pid():
        sys.exit('test instance already running (pid %d)' % running_pid())
    apply_ini('Skyrim.ini', SKYRIM_INI + tuple(skyrim))
    apply_ini('SkyrimPrefs.ini', PREFS_INI + tuple(prefs))
    env = {'TES_BRIDGE_PIPE': PIPE,
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


#: Seconds the runtime log may stay unchanged before the game counts as hung (passing lanes' longest gap: 74 s).
HUNG_SECONDS = 60

#: Seconds the runner may write nothing before a stall is reported (report only; the run goes on).
STALLED_SECONDS = 180


class Quiet:
    """How long a file has gone unchanged."""

    def __init__(self, path: str):
        """Starts the clock at the file's current state."""
        self.path, self.state, self.since = path, file_state(path), time.time()

    def seconds(self) -> float:
        """Seconds since the file last changed."""
        now = file_state(self.path)
        if now != self.state:
            self.state, self.since = now, time.time()
        return time.time() - self.since


def game_answers(bridge: Bridge) -> bool:
    """True when the game's main thread still runs a console line (a long quiet `until` is not a hang)."""
    try:
        return bool(bridge.console('GetCurrentTime'))
    except BridgeError:
        return False


def wait_results(path: str, seconds: float, bridge: Bridge, checkpoints: bool, game) -> list:
    """The result lines in `path` once its `done` line lands, the game exits or hangs (its runtime log
    unchanged for HUNG_SECONDS and no answer to a console line), or `seconds` pass, answering the runner's requests (`game.respond`)."""
    deadline = time.time() + seconds
    lines, handled = [], 0
    respond = game.respond(bridge) if game.respond else None
    log_path = os.path.join(MY_GAMES, 'SKSE', game.log)
    log_state, log_moved = file_state(log_path), time.time()
    runner = Quiet(path)
    while time.time() < deadline:
        lines = [json.loads(l) for l in tail(path, 10000, game.encoding) if l.strip()]
        handled = handle_lines(bridge, path, lines, handled, checkpoints, respond)
        if bridge.unanswered:
            print('GAME HUNG after %d result line(s): no reply to %s' % (len(lines), bridge.unanswered))
            crash_capture.snapshot(running_pid(), tag='hang_%s' % time.strftime('%m%d_%H%M%S'))
            break
        if lines and lines[-1].get('done'):
            break
        if runner.seconds() > STALLED_SECONDS:
            print('RUN STALLED after %d result line(s): nothing from the runner for %d s -- still waiting'
                  % (len(lines), STALLED_SECONDS), flush=True)
            runner = Quiet(path)
        if not running_pid():
            print('GAME EXITED after %d result line(s)' % len(lines))
            break
        if file_state(log_path) != log_state:
            log_state, log_moved = file_state(log_path), time.time()
        elif time.time() - log_moved > HUNG_SECONDS and game_answers(bridge):
            log_moved = time.time()
        elif time.time() - log_moved > HUNG_SECONDS:
            print('GAME HUNG after %d result line(s): %s unchanged for %d s' % (len(lines), game.log, HUNG_SECONDS))
            crash_capture.snapshot(running_pid(), tag='hang_%s' % time.strftime('%m%d_%H%M%S'))
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


#: How long a `load` may take to raise "Creations are currently loaded ... continue loading this save?" (Yes = 0).
LOAD_PROMPT_SECONDS = 5


def wait_message_box(bridge: Bridge, seconds: float) -> bool:
    """Whether a MessageBoxMenu opens within `seconds`."""
    return wait_menu(bridge, 'MessageBoxMenu', seconds)


def wait_menu_closed(bridge: Bridge, menu: str, seconds: float) -> bool:
    """Whether menu `menu` is gone within `seconds`; a menu stack read that fails mid-load counts as still open."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if all(name != menu for name, _flags in open_menus(bridge)):
                return True
        except BridgeError as error:
            print('menu stack not readable yet (%s); retrying' % error)
        time.sleep(0.5)
    return False


def wait_menu(bridge: Bridge, menu: str, seconds: float) -> bool:
    """Whether menu `menu` opens within `seconds`; a menu stack read that fails mid-load is retried."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if any(name == menu for name, _flags in open_menus(bridge)):
                return True
        except BridgeError as error:
            print('menu stack not readable yet (%s); retrying' % error)
        time.sleep(0.5)
    return False


def ready_after_load(bridge: Bridge, seconds: float) -> None:
    """Waits until the game answers again, through the bridge errors a long load leaves behind.

    See: docs/commentary/headless_testing.md#new-game-click
    """
    deadline = time.time() + seconds
    while True:
        try:
            bridge.wait_ready(120000)
            return
        except BridgeError as error:
            if time.time() > deadline:
                raise
            print('new game: bridge not ready yet (%s); waiting' % error)
            time.sleep(2)


def start_new_game(bridge: Bridge, settle: float, game: int) -> None:
    """Clicks New at the main menu and answers TESGameSelect's prompt with button `game`.

    The click is sent ONCE: the load it starts can hold the main thread longer
    than the bridge waits (E_LOADING), and a resent click is a second New Game.
    See: docs/commentary/headless_testing.md#new-game-click
    """
    bridge.wait_ready(300000)
    time.sleep(settle)
    dismiss_popups(bridge)
    retries, bridge.loading_retries = bridge.loading_retries, 1
    try:
        bridge.call(id=NEW_GAME_ID, args_=[0])
    except BridgeError as error:
        if error.code not in ('E_LOADING', 'E_TRANSPORT'):
            raise
        print('new game: still loading after the click (%s); waiting' % error.code)
    finally:
        bridge.loading_retries = retries
    ready_after_load(bridge, 300)
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
        if args.fast:
            god_mode_on(bridge)
        bridge.console('load %s' % save)
        if wait_message_box(bridge, LOAD_PROMPT_SECONDS):
            press_message_button(bridge, 0)
        wait_menu(bridge, LOADING_MENU, LOAD_PROMPT_SECONDS)
        ready_after_load(bridge, 300)
        wait_menu_closed(bridge, LOADING_MENU, 300)
        bridge.wait_ready(120000)
    elif args.new_game is not None:
        start_new_game(bridge, args.settle, args.new_game)
    else:
        enter_cell(bridge, args.cell, args.settle)
    if args.fast:
        god_mode_on(bridge)


def god_mode_on(bridge: Bridge) -> None:
    """God mode on: `tgm` toggles, so one answering "disabled" is sent again.

    See: docs/commentary/headless_testing.md#god-mode-after-a-load
    """
    answer = bridge.console('tgm').strip()
    if 'disabled' in answer.lower():
        answer = bridge.console('tgm').strip()
    print('god mode: %s' % answer, flush=True)


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


def mw_respond(_bridge: Bridge):
    """The line handler for one MorrowindRuntime run: answers a stalled walk's `onmesh` request.
    See: docs/commentary/headless_testing.md#quest-runs"""
    plugins = tes4_cases.load_order(os.path.join(LOCAL_APPDATA, 'Skyrim Special Edition', 'Plugins.txt'))
    worlds = {}

    def reply(line: dict):
        """The rescue point for an `onmesh` line, None for every other line."""
        if 'onmesh' not in line:
            return None
        answer = navmesh_rescue.harness_reply(plugins, line, worlds)
        print('onmesh %s -> %s' % (line['onmesh'], answer))
        return answer
    return reply


#: MorrowindRuntime runs MWScript itself; the harness runs console lines and answers stalled walks.
MW_GAME = CaseGame('TES_MW_TEST_', '.mwtest', 'MorrowindRuntime.log', 0.5, mw_respond)


def print_failure(bridge: Bridge, line: dict, names) -> None:
    """Prints and files the game's state for the failed case `line`, with a screenshot.

    See: docs/commentary/headless_testing.md#failure-snapshot
    """
    try:
        for text in fail_snapshot.snapshot(bridge, line, names, open_menus, PROFILE):
            print('  state: %s' % text)
        shot(os.path.join(PROFILE, 'fail_%s.png' % line['case']))
    except (OSError, ValueError, BridgeError) as error:
        print('  state: snapshot failed: %s' % error)


def tes4_game() -> CaseGame:
    """TESRuntime's runner for converted TES4 plugins: the harness resolves names,
    runs console lines and judges conditions, and reports each case's Papyrus errors.
    See: docs/commentary/headless_testing.md#tes4-quest-cases
    """
    plugins_txt = os.path.join(LOCAL_APPDATA, 'Skyrim Special Edition', 'Plugins.txt')
    names = tes4_cases.build_names(plugins_txt)
    lengths = silences.Lengths(silences.export_dirs(plugins_txt))

    def respond(bridge: Bridge):
        """The line handler for one run; reads the Papyrus log from its start."""
        papyrus = tes4_cases.PapyrusLog(os.path.join(MY_GAMES, 'Logs', 'Script', 'Papyrus.0.log'))
        quiet = silences.Silences(lengths)

        def reply(line: dict):
            """A request's answer; None for every other line, printing spoken lines and noting a case's
            Papyrus errors and its silences."""
            quiet.note(line)
            if 'judge' in line and silences.QUIET.match(line['judge']):
                return quiet.judge(line['judge'])
            if 'speaker' in line:
                tes4_cases.print_spoken(line, names)
            if 'case' in line:
                line['papyrus'] = papyrus.new_errors()
                for error in line['papyrus']:
                    print('  papyrus: %s' % error)
                if not line['ok']:
                    print_failure(bridge, line, names)
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
    with recording(args.record):
        launch(env, RECORD_SIZE if args.record else (), RECORD_SKYRIM_INI if args.record else ())
        debugger_attach(args)
        try:
            with connect(300) as bridge:
                enter_game(bridge, args, cases)
                lines = wait_results(results, args.timeout, bridge, args.checkpoints, game)
        except BridgeError:
            crash_capture.snapshot(running_pid(), tag='nostart_%s' % time.strftime('%m%d_%H%M%S'))
            raise
        finally:
            if not args.keep:
                stop()
    return report(lines, results, game) | debugger_report(args)


def debugger_attach(args) -> None:
    """With `--crash-capture`, cdb on the game, logging faults and `--break` hits.

    See: docs/reference/python_tools.md#crash-capture
    """
    if args.crash_capture:
        crash_capture.attach(str(running_pid()), args.crash_capture, 'run',
                             crash_capture.break_commands(args.break_at))


def debugger_report(args) -> int:
    """Prints the `--break` hit count and any fault's stack; 1 when the game faulted."""
    if not args.crash_capture:
        return 0
    log = os.path.join(args.crash_capture, 'run.txt')
    hits, faulted = crash_capture.run_summary(log)
    print('breakpoint hits: %d; game %s (%s)' % (hits, 'FAULTED' if faulted else 'did not fault', log))
    if faulted:
        crash_capture.report(log, 'MARKER_STACK', 40)
    return int(faulted)


@contextlib.contextmanager
def recording(out_dir: str):
    """With `out_dir`: the game at RECORD_SIZE on the High preset, recorded by OBS on its hidden desktop; Low
    preset again afterwards. Without it: nothing."""
    if not out_dir:
        yield
        return
    write_prefs(INSTANCE, 'High.ini', RECORD_SIZE)
    rec = obs_record.start(DESKTOP, out_dir, *(int(v) for _s, _k, v in RECORD_SIZE))
    try:
        yield
    finally:
        obs_record.stop(rec)
        write_prefs(INSTANCE, 'Low.ini')
        write_skyrim_ini(INSTANCE)


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
    """Copies the runtime log `name`, and every other runtime's log as failed_<log>, into the profile."""
    kept = os.path.join(PROFILE, 'mwtest_failed.log')
    shutil.copyfile(os.path.join(MY_GAMES, 'SKSE', name), kept)
    print('runtime log kept at %s' % kept)
    for other in glob.glob(os.path.join(MY_GAMES, 'SKSE', '*Runtime.log')):
        if os.path.basename(other) != name:
            shutil.copyfile(other, os.path.join(PROFILE, 'failed_' + os.path.basename(other)))


def launch_hd() -> None:
    """Launches at RECORD_SIZE on the High preset; `stop` puts the Low preset back."""
    write_prefs(INSTANCE, 'High.ini', RECORD_SIZE)
    launch(prefs=RECORD_SIZE, skyrim=RECORD_SKYRIM_INI)


def stop() -> None:
    """Terminates the test instance, never a process outside its folder, and restores the Low preset."""
    write_prefs(INSTANCE, 'Low.ini')
    write_skyrim_ini(INSTANCE)
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
    parser.add_argument('--record', metavar='DIR', help='record the run with OBS at 1440p60, High preset, into DIR')
    parser.add_argument('--crash-capture', metavar='DIR',
                        help="attach cdb: a fault's stack and full dump, and --break hits, land in DIR/run.*")
    parser.add_argument('--break', dest='break_at', action='append', default=[], metavar='RVA[|COND[|CMDS]]',
                        help='with --crash-capture: log registers and stack at SkyrimSE+RVA when the cdb '
                             'condition holds, e.g. "0xd976b0|@dl==0xff"')


def parse_args():
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--instance', type=int, default=1,
                        help='which test instance: 1 is the first; N > 1 is "Skyrim Test N", run in parallel')
    parser.add_argument('--token', help="the lease token `checkout` printed; every subcommand but "
                                        "checkout, checkouts and status needs it")
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('checkout', help='lease a free instance; prints its --instance and --token').add_argument(
        '--who', required=True, help='what the lease is for, shown to other sessions')
    sub.add_parser('checkouts', help='list who holds each instance')
    sub.add_parser('checkin', help='stop the instance and release its lease')
    b = sub.add_parser('build', help='link the instance and write its profile')
    b.add_argument('--steam-exe', action='store_true', help="use Steam's exe, not the unpacked one")
    b.add_argument('--plugin', action='append', default=[], help='plugin to link and enable')
    b.add_argument('--mod', action='append', default=[],
                   help='converted output/ folder to link, plugins enabled, with the runtime DLLs')
    b.add_argument('--skse-plugin', action='append', default=[], metavar='DLL',
                   help='third-party SKSE plugin DLL to link for this build only (e.g. a recording run)')
    s = sub.add_parser('shot', help='screenshot the hidden game window')
    s.add_argument('--out', help="PNG path; the instance profile's shot.png by default")
    _launched_parsers(sub)
    c = sub.add_parser('close', help="post the engine's close message to a menu")
    c.add_argument('menu', help='registered menu name, e.g. MessageBoxMenu')
    sub.add_parser('launch').add_argument('--hd', action='store_true',
                                          help='High preset at the 1440p recording size, for proof screenshots')
    for name in ('status', 'menus', 'stop'):
        sub.add_parser(name)
    return parser.parse_args()


def main() -> int:
    """Command-line entry point."""
    args = parse_args()
    if args.cmd in ('checkout', 'checkouts'):
        return (instance_lock.checkout(FIRST_INSTANCE, args.who, slot_running) if args.cmd == 'checkout'
                else instance_lock.print_leases(FIRST_INSTANCE) or 0)
    if args.cmd not in instance_lock.OPEN_COMMANDS:
        instance_lock.require(FIRST_INSTANCE, args.instance, args.token)
    use_instance(args.instance)
    if args.cmd == 'checkin':
        stop()
        instance_lock.checkin(FIRST_INSTANCE, args.instance)
        return 0
    extra =(FAST_INI if getattr(args, 'fast', False) else []) + (PAPYRUS_INI if args.cmd == 'questtest' else [])
    with ini_overrides(getattr(args, 'ini', []) + extra):
        return run(args)


def run(args) -> int:
    """Runs the parsed subcommand; its exit code."""
    actions = {'build': lambda: build(args.steam_exe, args.plugin, args.mod, args.skse_plugin),
               'launch': lambda: launch_hd() if args.hd else launch(),
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
