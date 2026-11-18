"""Run console lines on a running test instance, TES4 names resolved, and print each answer.

    python tools/validate/safe_run.py python -m tools.live.tes4_console [--instance N] LINE [LINE ...]

LINE is `Name.Command args` or a bare command, with TES4 EditorIDs resolved the way .tes4test `run` lines are;
`!cmd` sends the line raw; `@HEXREF cmd` runs it on that reference.
"""
import argparse
import os

from tools.live import tes4_cases, test_instance
from tools.live.game_bridge import Bridge


def answer(bridge: Bridge, line: str, names) -> str:
    """The printable answer to one console line."""
    if line.startswith('@'):
        ref, _, command = line[1:].partition(' ')
        return '>> %s [at %s]\n%s' % (command, ref, bridge.console(command, ref=int(ref, 16)).strip()[:1500])
    if line.startswith('!'):
        return '>> %s [raw]\n%s' % (line[1:], bridge.console(line[1:]).strip()[:1500])
    ok, output = tes4_cases.console(bridge, line, names)
    return '>> %s [%s]\n%s' % (line, 'ok' if ok else 'REJECTED', output[:1500])


def main() -> None:
    """Parse arguments and print each line's answer."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--instance', type=int, default=1, help='test instance slot (1 = the first)')
    parser.add_argument('lines', nargs='+', help='console lines')
    args = parser.parse_args()
    test_instance.use_instance(args.instance)
    names = tes4_cases.build_names(os.path.join(test_instance.LOCAL_APPDATA, 'Skyrim Special Edition', 'Plugins.txt'))
    bridge = Bridge(test_instance.PIPE)
    for line in args.lines:
        print(answer(bridge, line, names), flush=True)


if __name__ == '__main__':
    main()
