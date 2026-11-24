"""What Setup shows: Skyrim and its version, SKSE and the Address Library, the tools each step
needs, the output folder and the mod managers. Every check reads the disk; none runs anything.

Skyrim 1.6 and 1.7 are fully supported. Older builds (1.5.97, VR) convert every game except
Morrowind, whose runtime needs addresses only 1.6+ provides.
See: docs/commentary/core_gui.md#setup
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import preflight
from asset_convert.paths import XWMAENCODE
from asset_convert.sources.archive import SEVENZIP_EXE
from core.gui import install
from core.gui.exe_update import download
from core.subprocess_flags import POPEN_FLAGS
from core.setup_check import ADDRESS_LIBRARY_URL, CK_URL, MIN_VERSION, SKSE_URL, address_library_name, exe_version
from papyrus_compile import find_skse_source_scripts
from tes4_export.export_morrowind import MORROWIND_SOURCE_KEY

#: The SDK installer itself, its size, and xWMAEncode's path inside it (as TES Auto-Convert.cmd).
DXSDK_DOWNLOAD = ("https://download.microsoft.com/download/A/E/7/AE743F1F-632B-4809-87A9-AA1BB3458E31/"
                  "DXSDK_Jun10.exe")
DXSDK_SIZE = 599455936
DXSDK_MEMBER = r"DXSDK\Utilities\bin\x86\xWMAEncode.exe"
#: Where a located xWMAEncode.exe is copied, so every step finds it.
XWMA_TARGET = XWMAENCODE
#: The phases whose tools Setup reports, in run order.
PHASES = ("export", "extract", "meshes", "speedtrees", "creatures", "import", "sounds", "scripts", "lod",
          "pack_bsa", "pack_zip")


# ---------------------------------------------------------------------------
#  Lines
# ---------------------------------------------------------------------------

def item(key, title, detail="", status="", tone="idle", action="", action2="", highlight=False, mono=False) -> dict:
    """One Setup line, in the shape the window's CheckItem takes."""
    return {"key": key, "title": title, "detail": detail, "status": status, "tone": tone, "action": action,
            "action2": action2, "highlight": highlight, "mono": mono}


# ---------------------------------------------------------------------------
#  Skyrim
# ---------------------------------------------------------------------------

def skyrim_folders(data_path: str) -> tuple:
    """(game folder, Data folder) from the Skyrim Data path, or (None, None)."""
    if not data_path:
        return None, None
    data = Path(data_path)
    if data.name.lower() != "data":
        return data, data / "Data"
    return data.parent, data


def skyrim_version(game) -> tuple:
    """The game's version as a tuple, () when unreadable."""
    if game is None:
        return ()
    return exe_version(game / "SkyrimSE.exe") or exe_version(game / "SkyrimVR.exe")


def morrowind_allowed(version: tuple) -> bool:
    """False for Skyrim builds older than 1.6 (unknown counts as allowed)."""
    return not version or version[:2] >= MIN_VERSION


def _store(game: Path) -> str:
    """Which store the install came from, judged by its folder."""
    text = str(game).lower()
    return "Steam" if "steamapps" in text else "GOG" if "gog" in text else ""


def _address_library(data: Path, version: tuple, mo2) -> bool:
    """True when the Address Library file for `version` is in Data or an MO2 mod."""
    name = address_library_name(version)
    if (data / "SKSE" / "Plugins" / name).is_file():
        return True
    return mo2 is not None and mo2.mods.is_dir() and any(mo2.mods.glob(f"*/SKSE/Plugins/{name}"))


def skyrim_items(session, mo2) -> list:
    """Skyrim, its version, SKSE and the Address Library."""
    game, data = skyrim_folders(session.tes5)
    if game is None or not game.is_dir():
        return [item("skyrim", "Skyrim Special Edition", "Not found. Install it from Steam and start it once.",
                     "Not found", "bad", "Locate…", highlight=True)]
    version = skyrim_version(game)
    shown = ".".join(str(n) for n in version[:3]) or "unknown"
    out = [item("skyrim", "Skyrim Special Edition", str(game), "Found", "good", "Change", mono=True)]
    full = morrowind_allowed(version)
    out.append(item("version", "Game version", " · ".join(p for p in (shown, _store(game)) if p),
                    "Full support" if full else "No Morrowind", "good" if full else "bad", "Supported versions"))
    skse = game / "skse64_loader.exe"
    scripts = bool(find_skse_source_scripts(session.cfg))
    if not skse.is_file():
        out.append(item("skse", "SKSE64", "Copy all of its files into your Skyrim folder.", "Missing", "bad",
                        "Download page", highlight=True))
    else:
        detail = ".".join(str(n) for n in exe_version(skse)[:3]) + (" · script sources installed" if scripts else "")
        out.append(item("skse", "SKSE64", detail if scripts else "Its script files are missing: copy ALL of "
                        "SKSE's files, the Data folder included.", "Found" if scripts else "Incomplete",
                        "good" if scripts else "warn", "" if scripts else "Download page", highlight=not scripts))
    if version:
        found = _address_library(data, version, mo2)
        out.append(item("addresslib", "Address Library", address_library_name(version),
                        "Found" if found else "Missing", "good" if found else "bad",
                        "" if found else "Download page", highlight=not found, mono=True))
    ck = (game / "CreationKit.exe").is_file()
    out.append(item("ck", "Creation Kit", "LipGenerator for lip sync · Papyrus script sources for Scripts",
                    "Found" if ck else "Missing", "good" if ck else "warn", "" if ck else "Store page",
                    highlight=not ck))
    return out


#: Setup line key -> the page its download button opens.
PAGES = {"skse": SKSE_URL, "addresslib": ADDRESS_LIBRARY_URL, "ck": CK_URL}


# ---------------------------------------------------------------------------
#  Tools the steps use
# ---------------------------------------------------------------------------

def missing_tools() -> dict:
    """{tool name: (purpose, how to install, [steps that need it])} for every unmet dependency."""
    out = {}
    for phase in PHASES:
        for miss in preflight.check_phase(phase):
            entry = out.setdefault(miss.name, (miss.purpose, miss.install, []))
            entry[2].append(preflight.PHASE_LABELS.get(phase, phase))
    return out


def tool_items() -> list:
    """xWMAEncode first (it can't be bundled), then anything else missing, else "all found"."""
    missing = missing_tools()
    xwma = missing.pop("xWMAEncode.exe", None)
    out = [item("xwma", "xWMAEncode", "Needed by Sounds (voice lines). Microsoft doesn't allow it to be bundled: "
                "Download takes it out of Microsoft's DirectX SDK installer (about 600 MB) and deletes the rest.",
                "Missing", "warn", "Download", "Locate xWMAEncode.exe", highlight=True) if xwma else
           item("xwma", "xWMAEncode", "Voice lines for Sounds", "Found", "good")]
    for name, (purpose, how, steps) in sorted(missing.items()):
        out.append(item("tool:" + name, name, f"Needed by {', '.join(steps)}: {purpose}. {how.splitlines()[0]}",
                        "Missing", "warn", highlight=True))
    if not missing:
        out.append(item("bundled", "Bundled tools", "FFmpeg, Papyrus compiler, hkxcmd, LODGen, BSArch", "Ready", "good"))
    return out


def fetch_xwma(log) -> None:
    """Download the DirectX SDK installer, extract only xWMAEncode.exe, and delete the installer."""
    installer = Path(tempfile.gettempdir()) / "DXSDK_Jun10.exe"
    try:
        log("Downloading the DirectX SDK (June 2010) from Microsoft")
        download(DXSDK_DOWNLOAD, DXSDK_SIZE, installer, log)
        log("Taking out xWMAEncode.exe")
        subprocess.run([str(SEVENZIP_EXE), "e", "-y", str(installer), DXSDK_MEMBER, f"-o{XWMA_TARGET.parent}"],
                       check=True, capture_output=True, **POPEN_FLAGS)
    finally:
        installer.unlink(missing_ok=True)
    if not XWMA_TARGET.is_file():
        raise RuntimeError("The SDK installer did not contain xWMAEncode.exe.")
    log(f"xWMAEncode.exe is in {XWMA_TARGET.parent}")


def place_xwma(path: str) -> bool:
    """Copy a located xWMAEncode.exe where every step looks for it."""
    if not path or Path(path).name.lower() != "xwmaencode.exe":
        return False
    XWMA_TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, XWMA_TARGET)
    return True


# ---------------------------------------------------------------------------
#  Output and mod managers
# ---------------------------------------------------------------------------

def _free(path) -> str:
    """Free space on `path`'s drive, in GB."""
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return f"{shutil.disk_usage(probe).free / 1024 ** 3:.0f} GB free"
    except OSError:
        return ""


def place_items(session, mo2, vortex: str) -> list:
    """The output folder, Mod Organizer 2 and Vortex with its two folders."""
    out = [item("output", "Output", f"{session.out_root()} · {_free(session.out_root())}", "", "idle", "Change",
                "Open", mono=True)]
    out.append(item("mo2", "Mod Organizer 2", mo2.describe() if mo2 else "Not found. Point to its folder if you use it.",
                    "Found" if mo2 else "Not found", "good" if mo2 else "idle", "Change" if mo2 else "Locate…",
                    mono=bool(mo2)))
    if not vortex:
        return out + [item("vortex", "Vortex", "Not found.", "Not found", "idle")]
    out.append(item("vortex", "Vortex", vortex, "Found", "good", mono=True))
    for kind, title in (("staging", "Vortex staging folder"), ("downloads", "Vortex downloads folder")):
        folder = install.vortex_folder(session.cfg.get(f"vortex.{kind}", ""), kind)
        out.append(item(kind, title, folder or "Not set", "" if folder else "Not set", "idle" if folder else "warn",
                        "Change", "Open" if folder else "", mono=bool(folder)))
    return out


def morrowind_mode_saved(cfg: dict) -> bool:
    """True once the user has picked a Morrowind mode."""
    return bool(cfg.get(MORROWIND_SOURCE_KEY))
