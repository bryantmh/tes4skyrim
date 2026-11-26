"""The folder update: unpacking the release zip and swapping the program files.

See: docs/commentary/core_gui.md#updates
"""

import zipfile

from core.gui import exe_update


def _write(root, files: dict) -> None:
    """Create `files` ({relative path: text}) under `root`."""
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_unpack_strips_the_top_folder_and_stays_inside(tmp_path):
    """TESRACT/a.dll lands as a.dll; an entry climbing out of the target is skipped."""
    archive = tmp_path / "TESRACT.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("TESRACT/TESRACT.exe", "exe")
        zf.writestr("TESRACT/lib/a.dll", "dll")
        zf.writestr("TESRACT/../escaped.txt", "bad")
    exe_update.unpack(archive, tmp_path / "new")
    assert (tmp_path / "new" / "TESRACT.exe").read_text() == "exe"
    assert (tmp_path / "new" / "lib" / "a.dll").read_text() == "dll"
    assert not (tmp_path / "escaped.txt").exists()


def test_finish_replaces_only_the_manifest_files(tmp_path, monkeypatch):
    """Old program files go (an emptied folder too), the new ones arrive, user data stays."""
    monkeypatch.setattr(exe_update, "wait_for_exit", lambda pid: None)
    started = []
    monkeypatch.setattr(exe_update.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    folder, new = tmp_path / "TESRACT", tmp_path / "new"
    _write(folder, {"TESRACT.exe": "old", "oldlib/x.dll": "old", "conversion_config.json": "{}",
                    "export/Oblivion.esm/x.txt": "data", exe_update.MANIFEST: "TESRACT.exe\noldlib/x.dll\n"})
    _write(new, {"TESRACT.exe": "new", "newlib/y.dll": "new", exe_update.MANIFEST: "TESRACT.exe\nnewlib/y.dll\n"})
    exe_update.finish(folder, 1, new, start=False)
    assert (folder / "TESRACT.exe").read_text() == "new"
    assert (folder / "newlib" / "y.dll").is_file()
    assert not (folder / "oldlib").exists()
    assert (folder / "conversion_config.json").is_file() and (folder / "export" / "Oblivion.esm" / "x.txt").is_file()
    assert started == []


def test_manifest_lines_outside_the_folder_are_ignored(tmp_path):
    """A manifest naming ../something never reaches outside the program folder."""
    folder = tmp_path / "TESRACT"
    _write(folder, {exe_update.MANIFEST: "../outside.txt\nTESRACT.exe\n", "TESRACT.exe": "x"})
    _write(tmp_path, {"outside.txt": "keep"})
    assert exe_update.program_files(folder) == [(folder / "TESRACT.exe").resolve()]
