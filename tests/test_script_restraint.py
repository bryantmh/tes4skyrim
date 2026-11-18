"""TES4 SetRestrained: a release followed by a script package is one call, and a release re-picks the package.

See: docs/commentary/script_convert.md#setrestrained-is-restrained
"""
from pathlib import Path

from script_convert.release_order import release_into_package

_POLYFILL = Path(__file__).resolve().parents[1] / 'script_convert' / 'static_scripts' / 'TES4Polyfill.psc'


def test_a_release_followed_by_a_package_is_one_call():
    """A release then a package on one actor become ReleaseIntoPackage."""
    lines = ['    TES4Polyfill.SetRestrained(restrainedRef, false, TES4Restrained)',
             '    TES4Polyfill.AddScriptPackage(restrainedRef, ToPlayer, TES4ScriptPackageList, TES4ScriptPackageArmed)']
    assert release_into_package(lines) == [
        '    TES4Polyfill.ReleaseIntoPackage(restrainedRef, ToPlayer, TES4ScriptPackageList, '
        'TES4ScriptPackageArmed, TES4Restrained)']


def test_a_release_of_another_actor_stays_apart():
    """Only a release and a package on the SAME actor are merged."""
    lines = ['  TES4Polyfill.SetRestrained(a, false, TES4Restrained)',
             '  TES4Polyfill.AddScriptPackage(b, P, L, A)']
    assert release_into_package(lines) == lines


def test_the_polyfill_release_repicks_the_package():
    """The polyfill's release evaluates the actor's package again."""
    body = _POLYFILL.read_text(encoding='utf-8').split('Function SetRestrained(')[1].split('EndFunction')[0]
    release = body.split('If !abRestrained')[1].split('EndIf')[0]
    assert 'EvaluatePackage()' in release
