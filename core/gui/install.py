"""Installing the finished mods with a mod manager: Mod Organizer 2 directly, Vortex by handing it
each archive, or neither (the zips stay in Finished Mods).

MO2: each zip is extracted into the instance's mods folder under its own name, added enabled to
the profile's modlist.txt in install order (the LAST installed gets the highest priority, so LOD
wins), and its plugins are enabled in plugins.txt. A `tesract.json` beside each mod records which
zip it came from, so an unchanged zip is not reinstalled and uninstall removes only our mods.
MO2 rewrites these files when it exits, so it must be closed while we write them.
See: docs/commentary/core_gui.md#mod-managers
"""

import configparser
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from core.gui import vortex_state
from core.subprocess_flags import POPEN_FLAGS
from output_layout import FINISHED_DIR_NAME

#: Written into each mod folder we install: the zip's name, size and time.
MARKER = "tesract.json"
#: Plugin file extensions MO2 lists in plugins.txt.
PLUGIN_EXTS = (".esm", ".esp", ".esl")
#: Mods that go first and last; every converted game sits between, in name order.
FIRST, LAST = ("TESRuntime",), ("AutoConvertLOD",)
#: The MO2 executable title SKSE is launched through.
SKSE_TITLE = "SKSE"
#: Vortex's id for Skyrim Special Edition.
VORTEX_GAME = "skyrimse"

# ---------------------------------------------------------------------------
#  What to install
# ---------------------------------------------------------------------------


def finished_zips(out_root) -> list:
    """Every zip in Finished Mods, in install order: the runtime first, LOD last."""
    folder = Path(out_root) / FINISHED_DIR_NAME
    zips = sorted(folder.glob("*.zip"), key=lambda p: p.stem.lower()) if folder.is_dir() else []
    first = [z for name in FIRST for z in zips if z.stem == name]
    last = [z for name in LAST for z in zips if z.stem == name]
    return first + [z for z in zips if z not in first and z not in last] + last


def _stamp(zip_path: Path) -> dict:
    """What identifies one build of a zip."""
    st = zip_path.stat()
    return {"zip": zip_path.name, "size": st.st_size, "mtime": int(st.st_mtime)}


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


def mo2_running() -> bool:
    """True while ModOrganizer.exe is running."""
    if sys.platform != "win32":
        return False
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ModOrganizer.exe", "/NH"], capture_output=True,
                         text=True, **POPEN_FLAGS).stdout
    return "ModOrganizer.exe" in out


def folder_status(mods_dir, zip_path: Path) -> str:
    """"new", "update" or "installed" for one finished zip in a mods folder (MO2's or Vortex's staging)."""
    try:
        stamp = json.loads((Path(mods_dir) / zip_path.stem / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "new"
    return "installed" if stamp == _stamp(zip_path) else "update"


def _extract(zip_path: Path, dest: Path) -> list:
    """Replace `dest` with the zip's contents; returns the plugins at its root."""
    tmp = dest.with_name(dest.name + ".tesract-tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    (dest / MARKER).write_text(json.dumps(_stamp(zip_path)), encoding="utf-8")
    return sorted(p.name for p in dest.iterdir() if p.suffix.lower() in PLUGIN_EXTS)


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


def mo2_install(mo2: Mo2, zips: list, log) -> None:
    """Install or update every zip in MO2, in order. Raises when MO2 is open."""
    if mo2_running():
        raise RuntimeError("Close Mod Organizer first: it rewrites its mod list when it exits.")
    plugins = folder_install(mo2.mods, zips, log)
    profile = mo2.profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    _write_modlist(profile, [z.stem for z in zips])
    _write_plugins(profile, plugins)


def folder_install(mods_dir, zips: list, log) -> list:
    """Extract every zip into its own folder under `mods_dir`; returns the plugins they hold."""
    plugins = []
    for zip_path in zips:
        log(f"Installing {zip_path.stem}")
        plugins += _extract(zip_path, Path(mods_dir) / zip_path.stem)
    return plugins


def folder_uninstall(mods_dir, log) -> list:
    """Delete every mod folder we installed under `mods_dir`; returns their names."""
    mods_dir = Path(mods_dir)
    names = [p.parent.name for p in mods_dir.glob(f"*/{MARKER}")] if mods_dir.is_dir() else []
    for name in names:
        log(f"Removing {name}")
        shutil.rmtree(mods_dir / name, ignore_errors=True)
    return names


def mo2_uninstall(mo2: Mo2, log) -> list:
    """Remove every mod we installed from MO2; returns their names."""
    if mo2_running():
        raise RuntimeError("Close Mod Organizer first: it rewrites its mod list when it exits.")
    markers = list(mo2.mods.glob(f"*/{MARKER}")) if mo2.mods.is_dir() else []
    plugins = {p.name.lower() for m in markers for p in m.parent.iterdir() if p.suffix.lower() in PLUGIN_EXTS}
    names = folder_uninstall(mo2.mods, log)
    profile = mo2.profile_dir()
    gone = {n.lower() for n in names}
    for file_name, test in (("modlist.txt", lambda ln: ln[1:].strip().lower() in gone),
                            ("plugins.txt", lambda ln: ln.lstrip("*").strip().lower() in plugins),
                            ("loadorder.txt", lambda ln: ln.strip().lower() in plugins)):
        path = profile / file_name
        if path.exists():
            path.write_text("\n".join(ln for ln in _lines(path) if not test(ln)) + "\n", encoding="utf-8")
    return names


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

#: Vortex's state key for the downloads root; each game downloads into its own subfolder.
DOWNLOADS_KEY = "settings###downloads###path"


def find_vortex() -> str:
    """Vortex.exe, from the nxm handler it registers or its default folder; "" when absent."""
    candidates = [Path(nxm_handler()),
                  Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Black Tree Gaming Ltd" / "Vortex"
                  / "Vortex.exe"]
    return next((str(p) for p in candidates if p.name.lower() == "vortex.exe" and p.is_file()), "")


def vortex_folder(saved: str, kind: str) -> str:
    """The saved staging or downloads folder, else the one Vortex's own settings name, else ""."""
    if saved:
        return saved
    if kind == "staging":
        return vortex_state.staging_folder(VORTEX_GAME)
    root = vortex_state.read_setting(DOWNLOADS_KEY)
    if not isinstance(root, str) or not root:
        return ""
    userdata = str(vortex_state.STATE_DIR.parent)
    return os.path.normpath(os.path.join(root.replace("{USERDATA}", userdata), VORTEX_GAME))


def download_status(downloads, zip_path: Path) -> str:
    """"new", "update" or "installed" (copied) for one zip in Vortex's downloads folder."""
    target = Path(downloads) / zip_path.name
    if not target.is_file():
        return "new"
    here, there = target.stat(), zip_path.stat()
    same = here.st_size == there.st_size and int(here.st_mtime) == int(there.st_mtime)
    return "installed" if same else "update"


def vortex_downloads(downloads, zips: list, log) -> None:
    """Copy each zip into Vortex's downloads folder, where Vortex offers to install it."""
    Path(downloads).mkdir(parents=True, exist_ok=True)
    for zip_path in zips:
        log(f"Copying {zip_path.name} to Vortex's downloads")
        shutil.copy2(zip_path, Path(downloads) / zip_path.name)


def vortex_launch(vortex: str) -> None:
    """Open Vortex on Skyrim Special Edition, where the mods are deployed and the game started."""
    subprocess.Popen([vortex, "--game", VORTEX_GAME])
