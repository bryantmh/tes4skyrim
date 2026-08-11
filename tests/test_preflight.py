"""Per-phase dependency preflight: gating, reporting and the README contract.

The preflight exists because a missing external tool does not fail loudly on its
own -- Sounds without ffmpeg just ships a plugin whose every voice line is
silent, and nothing downstream reports it.  So three properties have to hold:

  * a phase's probe must use the SAME lookup the phase itself uses, or a check
    can pass while the phase then fails to find the tool,
  * a gap must stop that phase AND every phase after it, never run them anyway,
  * every pip package a probe names must be in the README's install line, or a
    user following the README still hits the wall.
"""
import importlib.util
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import preflight  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
#  Structure
# --------------------------------------------------------------------------

def test_every_phase_has_a_label():
    """A phase without a label would print its bare key in the abort banner."""
    for phase in preflight._REQUIREMENTS:
        assert phase in preflight.PHASE_LABELS, f"{phase} has no display label"


def test_convert_phase_names_match_preflight():
    """convert.py's selection list must name phases preflight actually knows.

    A typo here is silent: check_phases() would skip the phase entirely and the
    dependency would go unchecked.
    """
    src = open(os.path.join(ROOT, 'convert.py'), encoding='utf-8').read()
    block = re.search(r"_selected = \[name for name, on in \((.*?)\n    \) if on\]",
                      src, re.S)
    assert block, "could not find the phase-selection block in convert.py"
    names = re.findall(r"\('([a-z_]+)',", block.group(1))
    assert names, "no phase names parsed out of convert.py"
    for n in names:
        assert n in preflight._REQUIREMENTS, \
            f"convert.py selects unknown phase {n!r}"
    # And every phase with requirements must actually be reachable.
    for phase, reqs in preflight._REQUIREMENTS.items():
        if reqs:
            assert phase in names, \
                f"phase {phase!r} declares dependencies but convert.py never checks it"


def test_satisfied_phase_reports_nothing():
    """A phase with no declared requirements is always satisfied."""
    assert preflight.check_phase('export') == []
    assert preflight.check_phases(['export', 'pack_zip']) is None


# --------------------------------------------------------------------------
#  Gating
# --------------------------------------------------------------------------

def test_check_phases_returns_the_first_failure_in_run_order():
    """Reporting a later phase's gap would bury the one that stops the run."""
    order = ['export', 'meshes', 'sounds', 'scripts']
    boom = preflight.Missing('thing', 'purpose', 'install it')
    orig = dict(preflight._REQUIREMENTS)
    try:
        preflight._REQUIREMENTS['sounds'] = [lambda: boom]
        preflight._REQUIREMENTS['scripts'] = [lambda: boom]
        phase, missing = preflight.check_phases(order)
        assert phase == 'sounds', "must report the EARLIEST failing phase"
        assert missing == [boom]
    finally:
        preflight._REQUIREMENTS.clear()
        preflight._REQUIREMENTS.update(orig)


def test_a_raising_probe_does_not_kill_the_run(capsys):
    """A broken probe must warn, not abort a conversion that could have run."""
    orig = dict(preflight._REQUIREMENTS)
    try:
        def _explode():
            raise RuntimeError("registry on fire")
        preflight._REQUIREMENTS['export'] = [_explode]
        assert preflight.check_phase('export') == []
        assert 'WARNING' in capsys.readouterr().out
    finally:
        preflight._REQUIREMENTS.clear()
        preflight._REQUIREMENTS.update(orig)


def test_report_names_the_phase_the_cause_and_the_skipped_work():
    missing = [preflight.Missing('ffmpeg', 'Decoding audio',
                                 'winget install Gyan.FFmpeg')]
    out = preflight.format_report('sounds', missing, ['scripts', 'pack_zip'])
    assert 'Sounds' in out                      # which phase stopped
    assert 'ffmpeg' in out                      # what is missing
    assert 'Decoding audio' in out              # why it is needed
    assert 'winget install Gyan.FFmpeg' in out  # how to fix it
    assert 'Scripts' in out and 'Pack Mod Zip' in out   # what else is skipped


def test_report_indents_multi_line_install_instructions():
    """Continuation lines must line up under the instruction, not the margin."""
    missing = [preflight.Missing('x', 'y', 'first line\nsecond line')]
    out = preflight.format_report('sounds', missing, [])
    pad = ' ' * len('        install it: ')
    assert f'{pad}second line' in out


def test_missing_dependency_exit_code_is_distinct_from_failure():
    """The GUI keys off this to stop later steps; 1 means "ran but failed"."""
    assert preflight.RC_MISSING_DEP == 2
    assert preflight.RC_MISSING_DEP != 1


# --------------------------------------------------------------------------
#  Python version warning
# --------------------------------------------------------------------------

def test_no_version_warning_on_the_supported_python(monkeypatch):
    monkeypatch.setattr(preflight.sys, 'version_info',
                        preflight.SUPPORTED_PYTHON + (0, 'final', 0))
    assert preflight.python_version_warning() is None


@pytest.mark.parametrize('ver', [(3, 12), (3, 13), (3, 15), (4, 0)])
def test_version_warning_states_the_consequence_and_the_toolchain(monkeypatch, ver):
    """Both things the user asked to be told: 'may run into issues' + build tools."""
    monkeypatch.setattr(preflight.sys, 'version_info', ver + (0, 'final', 0))
    w = preflight.python_version_warning()
    assert w is not None, f'no warning on Python {ver}'
    assert 'may run into issues' in w
    # The toolchain named must match the platform actually running: MSVC on
    # Windows, g++/clang++ everywhere else (native/build.py branches the same
    # way -- see docs/pipeline_reference.md#running-off-windows).
    if preflight.sys.platform == 'win32':
        assert 'Build Tools for Visual Studio' in w
    else:
        assert 'g++' in w and 'clang++' in w
    assert 'python native/build.py' in w
    # It must name both the version in use and the one that is supported.
    assert '.'.join(str(n) for n in ver) in w
    assert '.'.join(str(n) for n in preflight.SUPPORTED_PYTHON) in w


def test_version_warning_reports_this_interpreters_abi_tag(monkeypatch):
    """The expected .pyd name must come from sysconfig, not a hardcoded tag.

    A literal would tell a 3.13 user to look for a cp314 file that their build
    would never produce.
    """
    import sysconfig
    monkeypatch.setattr(preflight.sys, 'version_info', (3, 12, 0, 'final', 0))
    w = preflight.python_version_warning()
    suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.pyd'
    assert f'_navgrow_native{suffix}' in w


def test_version_warning_is_not_a_hard_dependency(monkeypatch):
    """A version mismatch must warn, never abort — the extension can be rebuilt."""
    monkeypatch.setattr(preflight.sys, 'version_info', (3, 12, 0, 'final', 0))
    for phase in preflight._REQUIREMENTS:
        for m in preflight.check_phase(phase):
            assert 'python' not in m.name.lower() or 'navgrow' in m.name.lower(), \
                f'version mismatch surfaced as a hard requirement: {m.name}'


def test_version_warning_banner_is_delimited_for_the_gui():
    """The GUI colours the banner by header + two rules; both must be present."""
    banner = preflight.format_python_warning('body text here')
    lines = [x for x in banner.split('\n') if x.strip()]
    assert lines[0].lower().startswith('warning: python version')
    rules = [x for x in lines if set(x.strip()) == {'-'}]
    assert len(rules) == 2, f'expected exactly 2 rules, got {len(rules)}'


def test_convert_prints_the_version_warning_without_aborting():
    """convert.py must call the warning and must not gate on it."""
    src = open(os.path.join(ROOT, 'convert.py'), encoding='utf-8').read()
    assert 'python_version_warning()' in src
    assert 'format_python_warning' in src
    # The only early return in the preflight block is the dependency gate.
    block = src.split('# ── Dependency preflight')[1].split('success = True')[0]
    returns = re.findall(r'return (\S+)', block)
    assert returns == ['preflight.RC_MISSING_DEP'], \
        f'unexpected early return in the preflight block: {returns}'


# --------------------------------------------------------------------------
#  Probes match what the phases actually do
# --------------------------------------------------------------------------

def test_audio_probes_use_the_phases_own_lookup(monkeypatch):
    """If these drifted, a check could pass while the Sounds phase then fails."""
    import asset_convert.audio_converter as ac
    for finder, expect in (('find_ffmpeg', 'ffmpeg'),
                           ('find_xwmaencode', 'xWMAEncode.exe'),
                           ('find_lipgenerator', 'LipGenerator.exe')):
        monkeypatch.setattr(ac, finder, lambda *a, **k: None)
        names = [m.name for m in preflight.check_phase('sounds')]
        assert expect in names, f"{finder} returning None did not surface {expect}"
        monkeypatch.setattr(ac, finder, lambda *a, **k: 'X')
        names = [m.name for m in preflight.check_phase('sounds')]
        assert expect not in names, f"{finder} succeeding still reported {expect}"


def test_native_probe_matches_the_loader_naming(monkeypatch):
    """The .pyd carries an ABI tag; the probe must use the same filename rule."""
    import sysconfig
    suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.pyd'
    expected = os.path.join(ROOT, 'native', 'dist', '_navgrow_native' + suffix)
    # Whatever the truth on this machine, probe and loader must agree.
    assert preflight._have_native('_navgrow_native') == os.path.isfile(expected)


def test_pillow_is_probed_by_import_name_not_pip_name():
    """Pillow imports as PIL -- probing 'Pillow' would always report it missing."""
    if importlib.util.find_spec('PIL') is None:
        pytest.skip('Pillow not installed')
    names = [m.name for m in preflight.check_phase('lod')]
    assert 'Pillow' not in names, \
        "Pillow is installed but preflight reported it missing (wrong probe name)"


# --------------------------------------------------------------------------
#  README contract
# --------------------------------------------------------------------------

def _all_missing_names():
    """Every Missing the table can produce, with nothing installed."""
    real = preflight.importlib.util.find_spec
    preflight.importlib.util.find_spec = lambda n: None
    try:
        out = []
        for phase in preflight._REQUIREMENTS:
            out.extend(preflight.check_phase(phase))
        return out
    finally:
        preflight.importlib.util.find_spec = real


def test_readme_pip_line_lists_every_required_package():
    """A user following the README must end up with every pip dependency."""
    readme = open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read()
    m = re.search(r'pip install ([A-Za-z0-9_ .-]+)\n', readme)
    assert m, "no 'pip install ...' line found in README.md"
    listed = set(m.group(1).split())

    wanted = {mm.install.split()[-1] for mm in _all_missing_names()
              if mm.install.startswith('pip install')}
    assert wanted, "no pip requirements declared -- probe harness broken"
    assert not (wanted - listed), \
        f"README pip line is missing: {sorted(wanted - listed)}"
    assert not (listed - wanted), \
        f"README pip line lists packages nothing requires: {sorted(listed - wanted)}"


def test_pytest_is_not_an_end_user_dependency():
    """Running a conversion must never require the test runner."""
    wanted = {mm.name for mm in _all_missing_names()}
    assert 'pytest' not in wanted

    readme = open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read()
    m = re.search(r'pip install ([A-Za-z0-9_ .-]+)\n', readme)
    assert 'pytest' not in m.group(1).split(), \
        "pytest is in the main install line; it belongs in the contributing section"


def test_committed_binaries_are_not_listed_as_things_to_install():
    """Bundled tools ship in the repo -- telling users to install them is wrong."""
    readme = open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read()
    table = readme.split('## Requirements', 1)[1].split('```bash', 1)[0]
    for name in ('BSArch', 'LODGenx64', 'hkxcmd', 'papyrus.exe',
                 'dovah_hkp_mesh_mopp_bridge'):
        assert name not in table, \
            f"{name} is committed under external/ and must not be a requirement"


@pytest.mark.parametrize('rel', [
    'external/papyrus-compiler/papyrus.exe',
    'external/hkxcmd/hkxcmd.exe',
    'external/bsarch/BSArch.exe',
    'external/lodgen/LODGenx64.exe',
    'external/mopp_bridge/dovah_hkp_mesh_mopp_bridge.exe',
])
def test_bundled_binaries_are_present(rel):
    """These are committed; if one vanishes the checkout (or AV) ate it."""
    assert os.path.isfile(os.path.join(ROOT, rel)), \
        f"{rel} should be committed in this repo"
