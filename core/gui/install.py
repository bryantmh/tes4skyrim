"""Installing the converted mods with a mod manager, without zips: each mod's files are hardlinked from
output/ into the manager's mods or staging folder (copied file by file when that folder is on another
drive).

A `tesract.json` in each installed mod lists exactly the files placed, and the stamp of the source they
came from. Reinstalling places new files and removes the ones the source no longer has; uninstalling one
mod removes only its listed files (a hardlink removed leaves the output file alone). MO2 lists the mods in
the profile's modlist.txt and plugins.txt, in install order (the LAST installed has the highest
priority, so LOD wins); MO2 rewrites those when it exits, so it must be closed while we write them.
Vortex lists a mod only once it is in its state database, so the mods are registered through Vortex's
own `--set` command line, with Vortex closed; a deployed mod's files leave the Data folder first.
See: docs/commentary/core_gui.md#mod-managers
"""

import configparser
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from core.gui import install_mods as mods, vortex_deploy, vortex_state
from core.subprocess_flags import POPEN_FLAGS

#: Written into each mod folder we install: the files placed and the source's stamp.
MARKER = "tesract.json"
#: The MO2 executable title SKSE is launched through.
SKSE_TITLE = "SKSE"
#: Vortex's id for Skyrim Special Edition.
VORTEX_GAME = "skyrimse"
#: Files between progress lines while placing a big mod.
LOG_EVERY = 5000
#: `-s`/`--del` pairs per Vortex start: 24 worked on a real Vortex, 100 exited -1 with nothing changed.
VORTEX_PAIRS = 24
#: Vortex's state key of the folder it found a game in.
VORTEX_GAME_PATH = "settings###gameMode###discovered###{game}###path"
#: Vortex's state path of a game's "deployment needed" flag.
VORTEX_NEED_DEPLOY = "persistent.deployment.needToDeploy.{game}"

# ---------------------------------------------------------------------------
#  Placing files
# ---------------------------------------------------------------------------


def volume(path) -> int:
    """The volume id of the nearest existing folder at or above `path`, or -1 when none can be read."""
    path = Path(path).absolute()
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return path.stat().st_dev
    except OSError:
        return -1


def same_volume(a, b) -> bool:
    """Whether two paths are on one drive, so hardlinks between them can work."""
    return volume(a) == volume(b) != -1


def drive_notice(out_root, targets: list) -> str:
    """Setup's warning for each (label, folder) of `targets` on another drive than `out_root`; "" when none."""
    out = Path(out_root)
    lines = []
    for label, folder in targets:
        if folder and not same_volume(out, folder):
            there = Path(folder).drive or str(folder)
            lines.append(f"The output folder is on {out.absolute().drive or out}, but {label} ({folder}) is on "
                         f"{there}. Installing will copy the files instead of hardlinking them; put the output "
                         f"folder on {there} to avoid that.")
    return " ".join(lines)


def _same_copy(src: Path, dst: Path) -> bool:
    """Whether `dst` is a copy of `src` by size and time."""
    a, b = src.stat(), dst.stat()
    return a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns


def _place(src: Path, dst: Path, link: bool) -> bool:
    """Make `dst` a hardlink to `src` (a copy when `link` is false or linking fails); True when it is a hardlink."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if os.path.samefile(src, dst):
            return True
        if not link and _same_copy(src, dst):
            return False
        dst.unlink()
    if link:
        try:
            os.link(src, dst)
            return True
        except OSError:
            pass
    shutil.copy2(src, dst)
    return False


def _place_tree(entries: list, dest: Path, link: bool, log) -> str:
    """Place every file of a folder mod under `dest`; returns "link" or "copy" for what was done."""
    for i, (rel, src, _size, _mtime) in enumerate(entries, 1):
        if link and not _place(src, dest / rel, True):
            link = False
            log("  Hardlinks are not possible here: copying the rest")
        elif not link:
            _place(src, dest / rel, False)
        if i % LOG_EVERY == 0:
            log(f"  {i:,} of {len(entries):,}")
    return "link" if link else "copy"


def read_marker(mods_dir, name: str) -> dict:
    """The marker of an installed mod, or {} when it is not installed by us."""
    try:
        return json.loads((Path(mods_dir) / name / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _inside(dest: Path, rel: str) -> Path:
    """`dest / rel`, refusing anything that would leave `dest`."""
    target = (dest / rel).resolve()
    if dest.resolve() not in target.parents:
        raise ValueError(f"{rel} is outside {dest}")
    return target


def _prune(dest: Path, rels) -> None:
    """Remove the folders `rels` left empty, deepest first, and `dest` itself when nothing else is in it."""
    folders = {dest / Path(rel).parent for rel in rels}
    for folder in sorted({p for f in folders for p in [f, *f.parents] if dest in p.parents or p == dest},
                         key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass


def _remove_files(dest: Path, rels) -> None:
    """Delete the listed files under `dest` (hardlinks: only the link goes), then the folders they emptied."""
    for rel in rels:
        _inside(dest, rel).unlink(missing_ok=True)
    _prune(dest, rels)


def install_mod(mods_dir, mod: mods.Mod, entries: list, link: bool, log) -> list:
    """Install one mod under `mods_dir`; returns its plugins (masters first)."""
    dest = Path(mods_dir) / mod.name
    old = read_marker(mods_dir, mod.name)
    if old and "files" not in old:
        shutil.rmtree(dest, ignore_errors=True)
    _remove_files(dest, set(old.get("files", ())) - {e[0] for e in entries})
    dest.mkdir(parents=True, exist_ok=True)
    log(f"Installing {mod.name}: {len(entries):,} files")
    how = _place_tree(entries, dest, link, log)
    rels = [e[0] for e in entries]
    (dest / MARKER).write_text(json.dumps({"stamp": mods.summarize(entries), "files": rels, "mode": how}),
                               encoding="utf-8")
    return mods.plugins(rels)


def install_all(mods_dir, wanted: list, out_root, log) -> dict:
    """Install every (mod, entries) of `wanted` under `mods_dir`; returns {name: plugins}.

    Links when output and `mods_dir` share a drive; otherwise copies, and says so.
    """
    link = same_volume(out_root, mods_dir)
    if not link:
        log(f"{mods_dir} is on another drive than the output folder: copying instead of hardlinking")
    Path(mods_dir).mkdir(parents=True, exist_ok=True)
    return {mod.name: install_mod(mods_dir, mod, entries, link, log) for mod, entries in wanted}


def mod_status(mods_dir, mod: mods.Mod, summary: dict) -> str:
    """"new", "update" or "installed" for one mod in a mods folder."""
    marker = read_marker(mods_dir, mod.name)
    if not marker:
        return "new"
    return "installed" if marker.get("stamp") == summary else "update"


def uninstall_mod(mods_dir, name: str, log) -> list:
    """Remove mod `name` we installed under `mods_dir` (only its listed files); returns its plugins."""
    marker, dest = read_marker(mods_dir, name), Path(mods_dir) / name
    if not marker:
        return []
    log(f"Removing {name} from {mods_dir}")
    if "files" in marker:
        (dest / MARKER).unlink(missing_ok=True)
        _remove_files(dest, marker["files"])
    else:
        shutil.rmtree(dest, ignore_errors=True)
    return mods.plugins(marker.get("files", ()))


# ---------------------------------------------------------------------------
#  Mod Organizer 2
# ---------------------------------------------------------------------------

def _read_ini(path: Path) -> configparser.ConfigParser:
    """MO2's Qt-style ini, read leniently."""
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    ini.read(path, encoding="utf-8")
    return ini


def _qt_value(raw: str) -> str:
    """A value with Qt's `@ByteArray(...)` wrapper removed."""
    raw = (raw or "").strip()
    return raw[len("@ByteArray("):-1] if raw.startswith("@ByteArray(") and raw.endswith(")") else raw


class Mo2:
    """One MO2 instance: where its mods and profile are, and how to start it."""

    def __init__(self, instance_dir, exe):
        """Read the instance's ini."""
        self.dir = Path(instance_dir)
        self.exe = Path(exe) if exe else None
        ini = _read_ini(self.dir / "ModOrganizer.ini")
        general = ini["General"] if ini.has_section("General") else {}
        settings = ini["Settings"] if ini.has_section("Settings") else {}
        self.game = _qt_value(general.get("gameName", ""))
        self.profile = _qt_value(general.get("selected_profile", "")) or "Default"
        base = Path(_qt_value(settings.get("base_directory", "")) or self.dir)
        self.mods = self._dir(settings.get("mod_directory"), base, "mods")
        self.profiles = self._dir(settings.get("profiles_directory"), base, "profiles")
        self.portable = (self.dir / "ModOrganizer.exe").is_file()

    @staticmethod
    def _dir(raw, base: Path, default: str) -> Path:
        """A folder setting with %BASE_DIR% expanded, or base/default."""
        value = _qt_value(raw or "").replace("%BASE_DIR%", str(base))
        return Path(value) if value else base / default

    def describe(self) -> str:
        """Where this instance is and which profile it uses."""
        return f'{self.dir} · profile "{self.profile}"'

    def profile_dir(self) -> Path:
        """The selected profile's folder."""
        return self.profiles / self.profile


def nxm_handler() -> str:
    """The program registered for Nexus download links (MO2's nxmhandler or Vortex), or ""."""
    if sys.platform != "win32":
        return ""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\shell\open\command") as key:
            command = winreg.QueryValueEx(key, "")[0]
    except OSError:
        return ""
    parts = command.split('"')
    return parts[1] if command.startswith('"') and len(parts) > 1 else command.split(" ")[0]


def _mo2_exe_from_registry() -> str:
    """ModOrganizer.exe beside the nxm handler MO2 registers, or ""."""
    handler = nxm_handler()
    candidate = Path(handler).with_name("ModOrganizer.exe") if handler else None
    return str(candidate) if candidate and candidate.is_file() else ""


def find_mo2(saved: str = ""):
    """The MO2 instance managing Skyrim Special Edition, or None.

    `saved` is a folder the user picked: a portable instance, or an MO2 install. Otherwise the
    install comes from MO2's nxm handler and the instance from its own folder or %LOCALAPPDATA%.
    """
    exe = ""
    for folder in [saved, os.path.dirname(_mo2_exe_from_registry())]:
        if folder and (Path(folder) / "ModOrganizer.exe").is_file():
            exe = str(Path(folder) / "ModOrganizer.exe")
            if (Path(folder) / "ModOrganizer.ini").is_file():
                return Mo2(folder, exe)
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ModOrganizer"
    for ini in sorted(root.glob("*/ModOrganizer.ini")) if root.is_dir() else []:
        found = Mo2(ini.parent, exe)
        if "skyrim special edition" in found.game.lower():
            return found
    return None


def running(image: str) -> bool:
    """True while a program named `image` (for example ModOrganizer.exe) is running."""
    if sys.platform != "win32":
        return False
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"], capture_output=True,
                         text=True, **POPEN_FLAGS).stdout
    return image in out


def mo2_running() -> bool:
    """True while ModOrganizer.exe is running."""
    return running("ModOrganizer.exe")


def _lines(path: Path) -> list:
    """A profile list file's lines; [] when it does not exist yet."""
    try:
        return path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []


def _write_modlist(profile: Path, names: list) -> None:
    """Put `names` enabled at the top of modlist.txt, last name highest."""
    path = profile / "modlist.txt"
    ours = {n.lower() for n in names}
    lines = [ln for ln in _lines(path) if ln[1:].strip().lower() not in ours]
    head = [ln for ln in lines if ln.startswith("#")]
    rest = [ln for ln in lines if not ln.startswith("#")]
    path.write_text("\n".join(head + [f"+{n}" for n in reversed(names)] + rest) + "\n", encoding="utf-8")


def _write_plugins(profile: Path, plugins: list) -> None:
    """Enable `plugins` at the end of plugins.txt and loadorder.txt, in order."""
    ours = {p.lower() for p in plugins}
    path = profile / "plugins.txt"
    kept = [ln for ln in _lines(path) if ln.lstrip("*").strip().lower() not in ours]
    path.write_text("\n".join(kept + [f"*{p}" for p in plugins]) + "\n", encoding="utf-8")
    order = profile / "loadorder.txt"
    if order.exists():
        kept = [ln for ln in _lines(order) if ln.strip().lower() not in ours]
        order.write_text("\n".join(kept + plugins) + "\n", encoding="utf-8")


def mo2_install(mo2: Mo2, wanted: list, order: list, out_root, log) -> None:
    """Install `wanted` (mod, entries) pairs in MO2 and list every mod of ours in install order.

    `order` is every mod in install order: the profile lists all of ours, so a later partial install
    keeps LOD above the rest. Raises when MO2 is open.
    """
    if mo2_running():
        raise RuntimeError("Close Mod Organizer first: it rewrites its mod list when it exits.")
    install_all(mo2.mods, wanted, out_root, log)
    ours = [m.name for m in order if read_marker(mo2.mods, m.name)]
    plugins = [p for name in ours for p in mods.plugins(read_marker(mo2.mods, name).get("files", ()))]
    profile = mo2.profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    _write_modlist(profile, ours)
    _write_plugins(profile, plugins)


def mo2_uninstall(mo2: Mo2, names: list, log) -> None:
    """Remove the mods `names` we installed from MO2, and their lines from the profile's lists.

    MO2's virtual Data folder deploys nothing into the game, so this is the whole uninstall. Raises when MO2 is open.
    """
    if mo2_running():
        raise RuntimeError("Close Mod Organizer first: it rewrites its mod list when it exits.")
    plugins = {p.lower() for name in names for p in uninstall_mod(mo2.mods, name, log)}
    names = {n.lower() for n in names}
    profile = mo2.profile_dir()
    for file_name, test in (("modlist.txt", lambda ln: ln[1:].strip().lower() in names),
                            ("plugins.txt", lambda ln: ln.lstrip("*").strip().lower() in plugins),
                            ("loadorder.txt", lambda ln: ln.strip().lower() in plugins)):
        path = profile / file_name
        if path.exists():
            path.write_text("\n".join(ln for ln in _lines(path) if not test(ln)) + "\n", encoding="utf-8")


def mo2_launch(mo2: Mo2) -> bool:
    """Start Skyrim through SKSE in MO2; False when MO2's executable is unknown."""
    if mo2.exe is None or not mo2.exe.is_file():
        return False
    instance = "" if mo2.portable else mo2.dir.name
    subprocess.Popen([str(mo2.exe), f"moshortcut://{instance}:{SKSE_TITLE}"], cwd=str(mo2.exe.parent))
    return True


# ---------------------------------------------------------------------------
#  Vortex
# ---------------------------------------------------------------------------

def find_vortex() -> str:
    """Vortex.exe, from the nxm handler it registers or its default folder; "" when absent."""
    candidates = [Path(nxm_handler()),
                  Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Black Tree Gaming Ltd" / "Vortex"
                  / "Vortex.exe"]
    return next((str(p) for p in candidates if p.name.lower() == "vortex.exe" and p.is_file()), "")


def vortex_staging(saved: str) -> str:
    """The saved staging folder, else the one Vortex's own settings name, else ""."""
    return saved or vortex_state.staging_folder(VORTEX_GAME)


def vortex_owns(staging: str) -> bool:
    """Whether `staging` is the staging folder Vortex itself uses (so registering mods there makes sense)."""
    own = vortex_state.staging_folder(VORTEX_GAME)
    return bool(own) and os.path.normcase(os.path.normpath(own)) == os.path.normcase(os.path.normpath(staging))


def _vortex_key(name: str, *leaf: str) -> str:
    """Vortex's dotted state path of a mod's field (dots in the name escaped as Vortex's parser expects)."""
    return ".".join(["persistent", "mods", VORTEX_GAME, name.replace(".", r"\."), *leaf])


def vortex_fields(name: str, when: str) -> dict:
    """{state path: value} making Vortex list `name` (a folder in its staging) as an installed mod."""
    leaves = {("id",): name, ("installationPath",): name, ("state",): "installed", ("type",): "",
              ("attributes", "name"): name, ("attributes", "logicalFileName"): name,
              ("attributes", "installTime"): when}
    return {_vortex_key(name, *leaf): value for leaf, value in leaves.items()}


def vortex_set_args(names: list, when: str = "") -> list:
    """The `-s path=value` arguments registering each mod in Vortex (the values are JSON)."""
    when = when or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    pairs = [f"{path}={json.dumps(value)}" for name in names for path, value in vortex_fields(name, when).items()]
    return [arg for pair in pairs for arg in ("-s", pair)]


def vortex_del_args(names: list) -> list:
    """The `--del path` arguments removing each mod's registration from Vortex."""
    return [arg for name in names for arg in ("--del", _vortex_key(name))]


def vortex_cli(exe: str, args: list, log) -> None:
    """Run Vortex's own command line (it edits its state and quits); Vortex must be closed."""
    if running("Vortex.exe"):
        raise RuntimeError("Close Vortex first: it holds its mod list open and rewrites it when it exits.")
    env = {k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"}
    for i in range(0, len(args), VORTEX_PAIRS * 2):
        done = subprocess.run([exe, *args[i:i + VORTEX_PAIRS * 2]], capture_output=True, text=True, timeout=120,
                              env=env, **POPEN_FLAGS)
        if done.returncode:
            log(f"Vortex exited with {done.returncode}: {done.stderr.strip()[-300:]}")


def vortex_register(exe: str, names: list, log) -> None:
    """List the installed mods in Vortex, so it shows them on its next start."""
    log("Registering the mods with Vortex")
    vortex_cli(exe, vortex_set_args(names), log)


def vortex_unregister(exe: str, names: list, log) -> None:
    """Take the uninstalled mods out of Vortex's list."""
    log("Removing the mods from Vortex")
    vortex_cli(exe, vortex_del_args(names), log)


def vortex_data_dir(fallback: str) -> str:
    """The Data folder Vortex deploys Skyrim SE into (its found game folder's), else `fallback`."""
    game = vortex_state.read_setting(VORTEX_GAME_PATH.format(game=VORTEX_GAME))
    return str(Path(game) / "Data") if isinstance(game, str) and game else fallback


def _vortex_notice(name: str, done) -> str:
    """What the user must still do in Vortex after `name` left Data, or ""."""
    lines = []
    if done.redeploy:
        lines.append(f"{len(done.redeploy):,} file(s) {name} replaced are also in other mods, for example "
                     f"{done.redeploy[0]}. Open Vortex and deploy to put them back.")
    if done.kept:
        lines.append(f"{len(done.kept):,} file(s) of {name} were changed in the Data folder after Vortex deployed "
                     f"them, so they were left there, for example {done.kept[0]}.")
    return " ".join(lines)


def vortex_uninstall(exe: str, staging: str, data_dir: str, name: str, log) -> str:
    """Uninstall mod `name` from Vortex's own staging: its deployed files out of `data_dir`, its folder, its entry.

    Vortex must be closed. Returns what the user must still do in Vortex, or "".
    See: docs/commentary/core_gui.md#vortex-deployed-uninstall
    """
    if running("Vortex.exe"):
        raise RuntimeError("Close Vortex first: it holds its mod list open and rewrites it when it exits.")
    done = vortex_deploy.undeploy(data_dir, staging, name, VORTEX_GAME) if data_dir else vortex_deploy.Undeployed()
    if done.removed:
        log(f"Took {done.removed:,} deployed file(s) of {name} out of {data_dir}")
    uninstall_mod(staging, name, log)
    if exe:
        vortex_unregister(exe, [name], log)
        if done.redeploy:
            vortex_cli(exe, ["-s", f"{VORTEX_NEED_DEPLOY.format(game=VORTEX_GAME)}=true"], log)
    return _vortex_notice(name, done)


def vortex_launch(vortex: str) -> None:
    """Open Vortex on Skyrim Special Edition, where the mods are enabled, deployed and the game started."""
    subprocess.Popen([vortex, "--game", VORTEX_GAME])


