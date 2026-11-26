"""Step 1, Setup: what this PC has, the shared menus, and where things go.

Checked on every start on a worker thread. The window opens on Setup until Skyrim, SKSE and the
Address Library are found; after that it opens on Choose.
See: docs/commentary/core_gui.md#setup
"""

import version as version_info
import slint

from core.gui import checks, exe_update, install, morrowind, native
from core.gui.window import model, paint_rail

#: Setup lines whose problems keep the window opening on Setup.
BLOCKING = frozenset({"skyrim", "skse", "addresslib"})


class SetupView:
    """The Setup screen and what it found, for the other screens to read."""

    def __init__(self, ctx):
        """Wire the screen; nothing is checked until `refresh`."""
        self.ctx = ctx
        self.mo2 = None
        self.vortex = ""
        self.morrowind_ok = True
        self.problems = 0
        self.menus = {}
        ui = ctx.ui
        ui.setup_act = self._act
        ui.recheck = lambda: self.refresh()
        ui.set_look = lambda i: self._menu("look", i)
        ui.set_icons = lambda i: self._menu("icons", i)
        ui.set_chargen = lambda i: self._menu("chargen", i)

    def refresh(self, first: bool = False) -> None:
        """Check everything on a worker, then paint (and on the first run, pick the opening step)."""
        self.ctx.ui.status_step = "Checking this PC…"
        self.ctx.work(self._check, lambda found: self._paint(found, first))

    def _check(self) -> dict:
        """Every Setup line, read from the disk."""
        session = self.ctx.session
        mo2 = install.find_mo2(session.cfg.get("mo2Path", ""))
        vortex = install.find_vortex()
        game, _data = checks.skyrim_folders(session.tes5)
        staging = install.vortex_staging(session.cfg.get("vortex.staging", "")) if vortex else ""
        targets = [("Mod Organizer 2's mods folder", str(mo2.mods) if mo2 else ""), ("Vortex's staging folder", staging)]
        return {"skyrim": checks.skyrim_items(session, mo2), "tools": checks.tool_items(),
                "places": checks.place_items(session, mo2, vortex), "mo2": mo2, "vortex": vortex,
                "drive": install.drive_notice(session.out_root(), targets),
                "morrowind": checks.morrowind_allowed(checks.skyrim_version(game)),
                "menus": morrowind.menu_choices(session.cfg)}

    def _paint(self, found: dict, first: bool) -> None:
        """Show what was found."""
        ui = self.ctx.ui
        self.mo2, self.vortex, self.morrowind_ok = found["mo2"], found["vortex"], found["morrowind"]
        ui.drive_notice = found["drive"]
        ui.skyrim_checks = model(found["skyrim"])
        ui.tool_checks = model(found["tools"])
        ui.place_checks = model(found["places"])
        self.menus = found["menus"]
        ui.look_options, ui.look = model(self.menus["look"][0]), self.menus["look"][1]
        ui.icon_options, ui.icons = model(self.menus["icons"][0]), self.menus["icons"][1]
        ui.chargen_options, ui.chargen = model(self.menus["chargen"][0]), self.menus["chargen"][1]
        lines = found["skyrim"] + found["tools"]
        self.problems = sum(1 for line in lines if line["highlight"])
        blocked = any(line["highlight"] and line["key"] in BLOCKING for line in lines)
        ui.status_step = "Ready"
        for screen in ("install", "choose"):
            self.ctx.screens[screen].setup_changed()
        if first:
            self.ctx.go("setup" if blocked else "choose")
        paint_rail(self.ctx)

    def rail_state(self) -> tuple:
        """(state, summary) for the rail."""
        if self.problems:
            return "warn", f"{self.problems} to fix"
        return "done", "All found"

    def _menu(self, name: str, index: int) -> None:
        """Save one shared-menu choice."""
        values = self.menus.get(name, ([], 0, []))[2]
        if 0 <= index < len(values):
            self.ctx.session.save(morrowind.MENU_KEYS[name], values[index])
            setattr(self.ctx.ui, name, index)

    def _act(self, key: str, button: int) -> None:
        """A button on a Setup line was pressed (1 is the right-hand one, 2 the one beside it)."""
        if key == "update" and button == 1 and exe_update.can_update():
            self._update()
            return
        if key == "update":
            native.open_url(version_info.RELEASES_URL)
        elif key == "version":
            self.ctx.ui.dialog = "versions"
        elif key == "xwma":
            self._fetch_xwma() if button == 1 else self._locate_xwma()
        elif key in checks.PAGES:
            native.open_url(checks.PAGES[key])
        else:
            self._folder(key, button)

    def _update(self) -> None:
        """Download the new version beside this one, then offer to restart into it."""
        tag = self.ctx.ui.update_version
        ctx = self.ctx

        def _restart(exe):
            """Finish the update in a new process and close this window."""
            ctx.pending_update = None
            exe_update.restart(exe)
            slint.quit_event_loop()

        def _ready(exe):
            """Ask whether to restart now; otherwise the update installs when the window closes."""
            ctx.pending_update = exe
            ctx.confirm("Restart TESRACT", f"TESRACT {tag} is downloaded. It installs when TESRACT restarts, or "
                        "when you close it.", "Restart now", "Later", lambda: _restart(exe))

        ctx.confirm(f"Update to {tag}", f"Downloads TESRACT {tag} and replaces this copy's program files. Your "
                    "converted games, settings and logs stay; only the steps the update changed run again.", "Update",
                    "Not now", lambda: ctx.screens["convert"].start_task(
                        f"Updating to {tag}", lambda log: exe_update.install_update(tag, log), _ready, foreground=True))

    def _fetch_xwma(self) -> None:
        """Take xWMAEncode.exe out of Microsoft's SDK installer, then check again."""
        self.ctx.screens["convert"].start_task("Getting xWMAEncode.exe", checks.fetch_xwma, lambda _r: self.refresh(),
                                               foreground=True)

    def _locate_xwma(self) -> None:
        """Pick xWMAEncode.exe and copy it where the steps look."""
        path = self.ctx.pick_file("Find xWMAEncode.exe", [("xWMAEncode.exe", "xWMAEncode.exe")])
        if path and checks.place_xwma(path):
            self.ctx.toast("xWMAEncode.exe found")
            self.refresh()

    def _folder(self, key: str, button: int) -> None:
        """Change or open one of the folders Setup lists."""
        session = self.ctx.session
        current = {"output": str(session.out_root()),
                   "staging": install.vortex_staging(session.cfg.get("vortex.staging", ""))}
        if button == 2:
            native.open_path(current.get(key, ""))
            return
        titles = {"skyrim": "Skyrim Special Edition's folder", "output": "Folder for converted output",
                  "mo2": "Mod Organizer 2's folder", "staging": "Vortex's staging folder for Skyrim SE"}
        folder = self.ctx.pick_folder(titles.get(key, "Folder"))
        if not folder:
            return
        if key == "skyrim":
            session.tes5 = folder
            session.save("tes5DataPath", folder)
        elif key == "output":
            session.set_output(folder)
            self.ctx.ui.output_path = str(session.out_root())
        elif key == "mo2":
            session.save("mo2Path", folder)
        else:
            session.save(f"vortex.{key}", folder)
        self.refresh()
