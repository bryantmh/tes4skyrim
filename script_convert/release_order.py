"""An actor a block releases and then hands a script package gets both in one polyfill call.

See: docs/commentary/script_convert.md#setrestrained-is-restrained
"""
import re

#: A converted release: `TES4Polyfill.SetRestrained(<ref>, false, <list>)`.
_RELEASE = re.compile(r'^(?P<pad>\s*)TES4Polyfill\.SetRestrained\((?P<ref>[^,]+), false, (?P<held>\w+)\)\s*$')
#: A converted AddScriptPackage call on a reference.
_ADD = re.compile(r'^\s*TES4Polyfill\.AddScriptPackage\((?P<ref>[^,]+), (?P<rest>.*)\)\s*$')


def release_into_package(lines: list) -> list:
    """`lines` with each release directly followed by an AddScriptPackage on the same actor merged.

    Oblivion ran both in one frame, so the freed actor's first package was the
    script's; the merged call holds its AI off while both change.
    """
    out, i = [], 0
    while i < len(lines):
        release = _RELEASE.match(lines[i])
        add = _ADD.match(lines[i + 1]) if release and i + 1 < len(lines) else None
        if add and release.group('ref').strip() == add.group('ref').strip():
            out.append(f"{release.group('pad')}TES4Polyfill.ReleaseIntoPackage({release.group('ref')}, "
                       f"{add.group('rest')}, {release.group('held')})")
            i += 2
            continue
        out.append(lines[i])
        i += 1
    return out
