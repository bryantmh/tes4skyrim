"""Step 5, Install & play: every converted mod listed with a tick box, installed where a mod manager uses it.

Three ways: into Mod Organizer 2 (hardlinked into its mods folder, listed in the profile), into
Vortex's staging folder (hardlinked, then registered so Vortex lists them), or by hand: zips made here
for the mods whose zip is missing or older than their files. Copying replaces hardlinks only when the
target is on another drive. Each installed row uninstalls on its own.
See: docs/commentary/core_gui.md#mod-managers
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from core.gui import checks, install, native
from core.gui import install_mods as mods
from core.gui.session import MANAGER_CONFIG_KEY
from core.gui.window import model, paint_rail
from output_layout import FINISHED_DIR_NAME, finished_dir

#: (saved key, label) per way of installing, in the order the switch shows them.
MANAGERS = (("mo2", "Mod Organizer 2"), ("vortex", "Vortex"), ("zips", "Manual install"))
#: Status -> (pill text, tone).
STATUS = {"new": ("New", "accent"), "update": ("Update", "warn"), "installed": ("Installed", "good"),
          "missing": ("No zip", "warn"), "stale": ("Old zip", "warn"), "ready": ("Zip ready", "good")}
#: Statuses that still need doing.
TODO = frozenset({"new", "update", "missing", "stale"})
#: Statuses of a mod that is in the manager's folder, so its row can uninstall it.
IN_PLACE = frozenset({"installed", "update"})
#: Way of installing -> the folder button's text.
FOLDER_LABELS = {"mo2": "Open mods folder", "vortex": "Open staging folder", "zips": "Open Finished Mods"}
#: Way of installing -> what the folder line says when that manager was not found.
NOT_FOUND = {"mo2": "Mod Organizer 2 not found: set its folder in Setup",
             "vortex": "Vortex's folder not found: set it in Setup"}


@dataclass
class Row:
    """One listed mod: what it is, how many files, and what is left to do for it."""

    mod: mods.Mod
    summary: dict
    status: str = ""


def megabytes(size: int) -> str:
    """A size in MB, or GB from 1 GB."""
    return f"{size / 1073741824:,.1f} GB" if size >= 1073741824 else f"{size / 1048576:,.1f} MB"


def status_of(mod: mods.Mod, summary: dict, key: str, target: str) -> str:
    """One mod's status for the chosen way of installing."""
    if key == "zips":
        return mods.zip_state(mod, summary)
    return install.mod_status(target, mod, summary) if target else "new"


def read_rows(out_root, key: str, target: str) -> list:
    """Every mod with its files summarized and its status; the folders are read side by side (disk waits)."""
    def _one(mod):
        """One mod's row."""
        row = Row(mod, mods.summary(mod))
        row.status = status_of(mod, row.summary, key, target)
        return row

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(_one, mods.list_mods(out_root)))


def restatus(rows: list, key: str, target: str) -> list:
    """The same rows with statuses for another way of installing; the files are not read again."""
    return [Row(r.mod, r.summary, status_of(r.mod, r.summary, key, target)) for r in rows]


def how_placed(out_root, target: str) -> str:
    """Whether installing into `target` hardlinks or copies, in a few words."""
    return "hardlinked, same drive" if install.same_volume(out_root, target) else \
        "copied: the output folder is on another drive"


class InstallView:
    """The Install & play screen."""

    def __init__(self, ctx):
        """Wire the screen; rows are read when Setup has found the managers."""
        self.ctx = ctx
        self.rows = []
        self.checked = set()
        self.seen = {}
        self.dirty = True
        keys = [k for k, _ in MANAGERS]
        saved = ctx.session.manager
        self.index = keys.index(saved) if saved in keys else 0
        ui = ctx.ui
        ui.manager_options = model(label for _k, label in MANAGERS)
        ui.manager = self.index
        ui.set_manager = self._set_manager
        ui.install_toggle = self._toggle
        ui.install_select = self._select
        ui.install = self._install
        ui.launch = self._launch
        ui.uninstall = self._uninstall
        ui.open_target = self._open_target

    def key(self) -> str:
        """The chosen way of installing."""
        return MANAGERS[self.index][0]

    def _setup(self):
        """Setup's screen, which found MO2 and Vortex."""
        return self.ctx.screens["setup"]

    def target(self) -> str:
        """The folder the mods go into, or "" when the chosen manager was not found (and for manual installs)."""
        key = self.key()
        if key == "mo2":
            mo2 = self._setup().mo2
            return str(mo2.mods) if mo2 else ""
        if key == "vortex":
            return install.vortex_staging(self.ctx.session.cfg.get("vortex.staging", ""))
        return ""

    def setup_changed(self) -> None:
        """Setup checked again: reread the rows."""
        self.refresh()

    def run_finished(self) -> None:
        """A run ended: read the output again when next shown (saved summaries make that cheap)."""
        self.dirty = True

    def shown(self) -> None:
        """Opened: read the output again when it may have changed, else just each mod's install state."""
        if self.dirty or not self.rows:
            self.refresh()
        else:
            self._restatus()

    def refresh(self) -> None:
        """Read each mod's status on a worker, then paint."""
        key, target, out_root = self.key(), self.target(), self.ctx.session.out_root()
        self.dirty = False
        self.ctx.work(lambda: read_rows(out_root, key, target), self._paint)

    def _restatus(self) -> None:
        """Another way of installing: new statuses for the rows already read."""
        rows, key, target = self.rows, self.key(), self.target()
        if not rows:
            self.refresh()
            return
        self.ctx.work(lambda: restatus(rows, key, target), self._paint)

    def _paint(self, rows: list) -> None:
        """Take the rows, tick each one that newly has work to do (unseen, or its status or files changed), and paint.

        See: docs/commentary/core_gui.md#install-ticks
        """
        self.rows = rows
        for row in rows:
            state = (row.status, row.summary)
            if row.status in TODO and self.seen.get(row.mod.name) != state:
                self.checked.add(row.mod.name)
            self.seen[row.mod.name] = state
        self._show()

    def _show(self) -> None:
        """The switch, the folder line, the list and the buttons from the rows and ticks."""
        ui, key, target = self.ctx.ui, self.key(), self.target()
        ui.manager = self.index
        ui.installs = model(self._row(i, r) for i, r in enumerate(self.rows))
        ui.manager_where, ui.manager_detail, folder = self._place(key, target)
        ui.folder_label, ui.folder_enabled = FOLDER_LABELS[key], bool(folder)
        todo = self._todo()
        ready = bool(target) or key == "zips"
        ui.install_enabled = bool(todo) and ready
        ui.install_label = self._label(key, ready, len(todo))
        ui.launch_label = {"mo2": "Play through MO2", "vortex": "Open Vortex"}.get(key, "") if self._manager_exe() else ""
        paint_rail(self.ctx)

    def _todo(self) -> list:
        """The ticked rows an install would act on (a ticked, up to date zip is left alone)."""
        return [r for r in self.rows if r.mod.name in self.checked and (self.key() != "zips" or r.status in TODO)]

    def _label(self, key: str, ready: bool, count: int) -> str:
        """The main button's text, "" when this way of installing has no button."""
        if not ready:
            return ""
        if key == "zips":
            return f"Make {count} zip{'' if count == 1 else 's'}" if count else "Zips up to date"
        return f"Install {count}" if count else "Nothing ticked"

    def _place(self, key: str, target: str) -> tuple:
        """(folder line, its muted detail, the folder the button opens or "") for the chosen way."""
        out_root = self.ctx.session.out_root()
        if key == "zips":
            finished = str(out_root / FINISHED_DIR_NAME)
            return finished, "Zips of the ticked mods whose zip is missing or out of date", finished
        if not target:
            return NOT_FOUND[key], "", ""
        detail = how_placed(out_root, target)
        if key == "mo2":
            detail = f'profile "{self._setup().mo2.profile}" · {detail}'
        return target, detail, target

    def _open_target(self) -> None:
        """Open the chosen way's folder: MO2's mods, Vortex's staging, or Finished Mods (made if missing)."""
        key = self.key()
        folder = str(finished_dir(self.ctx.session.out_root())) if key == "zips" else self.target()
        if folder:
            native.open_path(folder)

    def _row(self, i: int, row: Row) -> dict:
        """One InstallRow."""
        text, tone = STATUS[row.status]
        role = mods.ROLES.get(row.mod.name, "")
        detail = " · ".join(p for p in (role, f"{row.summary["count"]:,} files", megabytes(row.summary["size"])) if p)
        return {"order": str(i + 1), "name": row.mod.name, "detail": detail, "status": text, "tone": tone,
                "checked": row.mod.name in self.checked, "removable": self.key() != "zips" and row.status in IN_PLACE}

    def _manager_exe(self):
        """MO2 or Vortex as found, or a false value."""
        return {"mo2": self._setup().mo2, "vortex": self._setup().vortex}.get(self.key())

    def rail_state(self) -> tuple:
        """(state, summary) for the rail."""
        if not self.rows:
            return "idle", "Nothing converted yet"
        if self.key() == "vortex" and not self.target() or self.key() == "mo2" and not self._setup().mo2:
            return "warn", f"{MANAGERS[self.index][1]} not found"
        todo = len(self._todo())
        return ("warn", f"{todo} to install") if todo else ("done", MANAGERS[self.index][1])

    def _set_manager(self, index: int) -> None:
        """Pick a way of installing; what needs doing differs, so the default ticks start again."""
        self.index = index
        self.seen, self.checked = {}, set()
        self.ctx.session.save(MANAGER_CONFIG_KEY, self.key())
        self._restatus()

    def _toggle(self, index: int, on: bool) -> None:
        """Tick or untick one row."""
        if 0 <= index < len(self.rows):
            (self.checked.add if on else self.checked.discard)(self.rows[index].mod.name)
            self._show()

    def _select(self, on: bool) -> None:
        """Tick every row, or none."""
        self.checked = {r.mod.name for r in self.rows} if on else set()
        self._show()

    def _job(self, wanted: list):
        """The function that does the install for the chosen way, taking a log callable."""
        key, target, session = self.key(), self.target(), self.ctx.session
        everything, out_root = [r.mod for r in self.rows], session.out_root()
        mo2, exe = self._setup().mo2, self._setup().vortex
        if key == "mo2":
            return lambda log: install.mo2_install(mo2, wanted, everything, out_root, log)
        if key == "zips":
            return lambda log: [mods.build_zip(m, e, out_root, log) for m, e in wanted
                                if mods.zip_state(m, e) != "ready"]

        def _vortex(log):
            """Place the files, then list them in Vortex when this is Vortex's own staging folder."""
            install.install_all(target, wanted, out_root, log)
            if exe and install.vortex_owns(target):
                install.vortex_register(exe, [m.name for m, _e in wanted], log)
        return _vortex

    def _install(self) -> None:
        """Install the ticked mods (or make their zips), reading their files fresh on the worker and saving
        each mod's summary from that read, so the list compares against what was installed."""
        names = [r.mod for r in self._todo()]
        if not names or (self.key() != "zips" and not self.target()):
            return
        job = self._job

        def _run(log):
            """Read each mod's files once, keep that as its summary, then install."""
            wanted = [(m, mods.files(m)) for m in names]
            for mod, entries in wanted:
                mods.save_summary(mod, entries)
            job(wanted)(log)

        self.ctx.screens["convert"].start_task(
            "Making zips" if self.key() == "zips" else f"Installing with {MANAGERS[self.index][1]}",
            _run, lambda _r: self._installed(self.key(), [m.name for m in names]))

    def _installed(self, key: str, done: list) -> None:
        """Untick the mods just installed, say what the manager needs next, and reread the list."""
        self.checked -= set(done)
        if key == "vortex":
            note = "Start Vortex: it lists the new mods. Enable them and deploy."
            if not install.vortex_owns(self.target()):
                note = "The files are in that folder. Vortex does not use it, so add the mods in Vortex yourself."
            self.ctx.info("Installed", note)
        self.refresh()

    def _launch(self) -> None:
        """Play through MO2, or open Vortex."""
        if self.key() == "mo2":
            if not install.mo2_launch(self._setup().mo2):
                self.ctx.toast("Mod Organizer 2's program was not found")
        elif self._setup().vortex:
            install.vortex_launch(self._setup().vortex)

    def _uninstall(self, index: int) -> None:
        """Remove one installed mod from the chosen manager, after asking."""
        key, target = self.key(), self.target()
        if key == "zips" or not 0 <= index < len(self.rows) or not target:
            return
        name = self.rows[index].mod.name
        self.ctx.confirm("Uninstall", f"Remove {name} from {MANAGERS[self.index][1]}? Your output folder keeps "
                         "its files.", "Uninstall", "Keep", lambda: self._start_uninstall(key, target, name))

    def _start_uninstall(self, key: str, target: str, name: str) -> None:
        """Run the uninstall as a task; the output is untouched, so only the install states are reread."""
        mo2, exe = self._setup().mo2, self._setup().vortex
        data = str(checks.skyrim_folders(self.ctx.session.tes5)[1] or "")
        self.ctx.screens["convert"].start_task(f"Uninstalling {name}",
                                               lambda log: self._remove(key, target, name, (mo2, exe, data), log),
                                               self._uninstalled)

    @staticmethod
    def _remove(key: str, target: str, name: str, found: tuple, log) -> str:
        """Uninstall `name` from MO2 or Vortex's staging; returns what the user must still do, or ""."""
        mo2, exe, data = found
        if key == "mo2":
            install.mo2_uninstall(mo2, [name], log)
            return ""
        if not install.vortex_owns(target):
            install.uninstall_mod(target, name, log)
            return ""
        return install.vortex_uninstall(exe, target, install.vortex_data_dir(data), name, log)

    def _uninstalled(self, notice: str) -> None:
        """Say what is left to do in the manager, if anything, and reread the install states."""
        if notice:
            self.ctx.info("Uninstalled", notice)
        self._restatus()
