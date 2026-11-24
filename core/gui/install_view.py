"""Step 5, Install & play: the finished mods put where a mod manager uses them, in order.

Four ways: straight into Mod Organizer 2 (mods folder and profile lists), into Vortex's staging
folder (Vortex lists them as mods on its next start), into Vortex's downloads folder (Vortex offers
to install each), or nothing (the zips stay in Finished Mods).
See: docs/commentary/core_gui.md#mod-managers
"""

from core.gui import install, native
from core.gui.session import MANAGER_CONFIG_KEY
from core.gui.window import model, paint_rail
from output_layout import FINISHED_DIR_NAME

#: (saved key, label) per way of installing, in the order the switch shows them.
MANAGERS = (("mo2", "Mod Organizer 2"), ("vortex", "Vortex staging"), ("vortex_downloads", "Vortex downloads"),
            ("zips", "Zips only"))
#: Status -> (pill text, tone).
STATUS = {"new": ("New", "accent"), "update": ("Update", "warn"), "installed": ("Installed", "good"),
          "zip": ("Ready", "idle")}


class InstallView:
    """The Install & play screen."""

    def __init__(self, ctx):
        """Wire the screen; rows are read when Setup has found the managers."""
        self.ctx = ctx
        self.rows = []
        keys = [k for k, _ in MANAGERS]
        saved = ctx.session.manager
        self.index = keys.index(saved) if saved in keys else 0
        ui = ctx.ui
        ui.manager_options = model(label for _k, label in MANAGERS)
        ui.manager = self.index
        ui.set_manager = self._set_manager
        ui.install = self._install
        ui.launch = self._launch
        ui.uninstall = self._uninstall
        ui.open_finished = lambda: native.open_path(str(ctx.session.out_root() / FINISHED_DIR_NAME))

    def key(self) -> str:
        """The chosen way of installing."""
        return MANAGERS[self.index][0]

    def _setup(self):
        """Setup's screen, which found MO2 and Vortex."""
        return self.ctx.screens["setup"]

    def target(self) -> str:
        """The folder the mods go into, or "" when the chosen manager was not found."""
        session = self.ctx.session
        key = self.key()
        if key == "mo2":
            mo2 = self._setup().mo2
            return str(mo2.mods) if mo2 else ""
        if key == "vortex":
            return install.vortex_folder(session.cfg.get("vortex.staging", ""), "staging")
        if key == "vortex_downloads":
            return install.vortex_folder(session.cfg.get("vortex.downloads", ""), "downloads")
        return str(session.out_root() / FINISHED_DIR_NAME)

    def setup_changed(self) -> None:
        """Setup checked again: reread the rows."""
        self.refresh()

    def run_finished(self) -> None:
        """A run may have rebuilt zips."""
        self.refresh()

    def shown(self) -> None:
        """Opened: the zips may have changed on disk."""
        self.refresh()

    def refresh(self) -> None:
        """Read each zip's status on a worker, then paint."""
        key, target, out_root = self.key(), self.target(), self.ctx.session.out_root()

        def _read():
            """[(zip, status)] in install order."""
            zips = install.finished_zips(out_root)
            if key == "zips" or not target:
                return [(z, "zip") for z in zips]
            status = install.download_status if key == "vortex_downloads" else install.folder_status
            return [(z, status(target, z)) for z in zips]

        self.ctx.work(_read, self._paint)

    def _paint(self, rows) -> None:
        """The switch, the list and the buttons."""
        self.rows = rows
        ui, key, target = self.ctx.ui, self.key(), self.target()
        ui.manager = self.index
        ui.installs = model(self._row(i, z, s) for i, (z, s) in enumerate(rows))
        ui.manager_where = self._where(key, target)
        todo = [z for z, s in rows if s in ("new", "update")]
        ui.install_enabled = bool(todo) and bool(target)
        ui.install_label = "" if key == "zips" else (f"Install {len(todo)}" if todo else "All installed")
        ui.launch_label = {"mo2": "Play through MO2", "vortex": "Open Vortex", "vortex_downloads": "Open Vortex"
                           }.get(key, "") if self._manager_exe() else ""
        paint_rail(self.ctx)

    def _where(self, key: str, target: str) -> str:
        """The line beside the switch: where the mods go."""
        if key == "mo2":
            mo2 = self._setup().mo2
            return mo2.describe() if mo2 else "Mod Organizer 2 not found: set its folder in Setup"
        if not target:
            return "Vortex's folder not found: set it in Setup"
        if key == "zips":
            return "Install the zips with any mod manager, in this order"
        return target

    @staticmethod
    def _row(i: int, zip_path, status: str) -> dict:
        """One InstallRow."""
        text, tone = STATUS[status]
        size = zip_path.stat().st_size / 1048576
        return {"order": str(i + 1), "name": zip_path.stem, "detail": f"{size:,.1f} MB", "status": text,
                "tone": tone}

    def _manager_exe(self):
        """MO2 or Vortex as found, or a false value."""
        return self._setup().mo2 if self.key() == "mo2" else self._setup().vortex

    def rail_state(self) -> tuple:
        """(state, summary) for the rail."""
        if not self.rows:
            return "idle", "Nothing built yet"
        todo = sum(1 for _z, s in self.rows if s in ("new", "update"))
        return ("warn", f"{todo} to install") if todo else ("done", MANAGERS[self.index][1])

    def _set_manager(self, index: int) -> None:
        """Pick a way of installing."""
        self.index = index
        self.ctx.session.save(MANAGER_CONFIG_KEY, self.key())
        self.refresh()

    def _install(self) -> None:
        """Install every new or changed zip, in order (MO2 rewrites its whole list in order)."""
        key, target = self.key(), self.target()
        zips = [z for z, _s in self.rows] if key == "mo2" else [z for z, s in self.rows if s in ("new", "update")]
        if not zips or not target:
            return
        mo2 = self._setup().mo2
        job = {"mo2": lambda log: install.mo2_install(mo2, zips, log),
               "vortex": lambda log: install.folder_install(target, zips, log),
               "vortex_downloads": lambda log: install.vortex_downloads(target, zips, log)}[key]
        self.ctx.screens["convert"].start_task(f"Installing with {MANAGERS[self.index][1]}", job,
                                               lambda _r: self._installed(key))

    def _installed(self, key: str) -> None:
        """Say what the manager needs next."""
        if key == "vortex":
            self.ctx.info("Installed", "Start Vortex: it lists the new mods. Enable them and deploy.")
        elif key == "vortex_downloads":
            self.ctx.info("Copied", "In Vortex, open Downloads and install each mod in the order shown here.")

    def _launch(self) -> None:
        """Play through MO2, or open Vortex."""
        if self.key() == "mo2":
            if not install.mo2_launch(self._setup().mo2):
                self.ctx.toast("Mod Organizer 2's program was not found")
        elif self._setup().vortex:
            install.vortex_launch(self._setup().vortex)

    def _uninstall(self) -> None:
        """Remove every mod we installed, after asking."""
        key, target = self.key(), self.target()
        if key not in ("mo2", "vortex") or not target:
            return
        mo2 = self._setup().mo2
        job = (lambda log: install.mo2_uninstall(mo2, log)) if key == "mo2" else (
            lambda log: install.folder_uninstall(target, log))
        self.ctx.confirm("Uninstall", f"Remove every converted mod TESRACT installed into {target}?",
                         "Uninstall", "Keep", lambda: self.ctx.screens["convert"].start_task("Uninstalling", job))
