"""The game's state at the moment a quest case fails, read through the harness's own bridge.

See: docs/commentary/headless_testing.md#failure-snapshot
"""
import os
import re

from tools.live import tes4_cases
from tools.live.game_bridge import BridgeError

#: `GetStage Q` / `GetStageDone Q n` in an expectation: the quest it reads.
STAGE_READ = re.compile(r'\bGetStage(?:Done)?\s+([A-Za-z_]\w*)')
#: `Name.something` at the start of an expectation: a quest variable or a reference's command.
OWNER = re.compile(r'^([A-Za-z_]\w*)\.')
#: A number or flag variable line of the console's `sqv`; its form properties are left out.
VARIABLE = re.compile(r'^\s*::(\w+?)_var = (-?[\d.]+|True|False)\s*$', re.MULTILINE)
#: A runtime FormID in the failed step's message (`talk 05028A7A: no conversation opened`).
FORM_ID = re.compile(r'\b[0-9A-Fa-f]{8}\b')
#: What is read from each reference a failed case names.
ACTOR_READS = ('GetPos x', 'GetPos y', 'GetPos z', 'GetDistance player', 'IsInCombat', 'GetDead',
               'GetCurrentAIProcedure', 'IsTalking', 'GetSitting',
               'GetActorValue Variable07', 'GetActorValue Variable09', 'IsInDialogueWithPlayer',
               'GetDisabled', 'IsWeaponOut', 'GetCombatState')
PLAYER_READS = ('player.GetPos x', 'player.GetPos y', 'player.GetPos z', 'player.IsInCombat')
VALUE = re.compile(r'>>\s*(.+)$')


def named(line: dict) -> tuple:
    """(quests, references) the failed case's expectations and spoken lines name, each in first-use order."""
    quests, refs = [], []
    for expect in line.get('expects', []):
        text = expect.get('expect', '')
        quests += STAGE_READ.findall(text)
        owner = OWNER.match(text)
        if owner and owner.group(1).lower() != 'player':
            refs.append(owner.group(1))
    refs += FORM_ID.findall(line.get('error', ''))
    refs += [said['speaker'] for said in line.get('said', []) if said.get('speaker')]
    quests = list(dict.fromkeys(quests))
    return quests, [ref for ref in dict.fromkeys(refs) if ref not in quests]


def short(output: str) -> str:
    """The value part of one console answer."""
    found = VALUE.search(output.strip().splitlines()[-1]) if output.strip() else None
    return found.group(1).strip() if found else output.strip()[-60:]


def read_ref(bridge, ref: str, names) -> str:
    """One line of `ACTOR_READS` for `ref` (a TES4 name or a runtime FormID in hex)."""
    values = []
    try:
        for command in ACTOR_READS:
            if tes4_cases.HEX_ID.match(ref):
                output = bridge.console(command, ref=int(ref, 16))
            else:
                output = tes4_cases.console(bridge, '%s.%s' % (ref, command), names)[1]
            values.append('%s=%s' % (command.replace('Get', '').replace(' ', ''), short(output)))
    except BridgeError as error:
        values.append('unreadable (%s)' % error)
    return '%s: %s' % (ref, ' '.join(values))


def snapshot(bridge, line: dict, names, menus, out_dir: str) -> list:
    """Text lines describing the game now, for the failed case `line`; also written to `out_dir`.

    `menus` is a callable returning (class, flags) per open menu. Quest variables come
    from the console's `sqv`, references from `ACTOR_READS`.
    """
    quests, refs = named(line)
    out = ['menus: %s' % ', '.join(str(menu[0]) for menu in menus(bridge))]
    out.append('player: ' + ' '.join(short(tes4_cases.console(bridge, read, names)[1]) for read in PLAYER_READS))
    out.append('game hour: ' + short(bridge.console('GetCurrentTime')))
    for quest in quests:
        variables = VARIABLE.findall(bridge.console('sqv %s' % quest))
        stage = short(tes4_cases.console(bridge, 'GetStage %s' % quest, names)[1])
        out.append('%s: stage %s; %s' % (quest, stage, ' '.join('%s=%s' % pair for pair in variables)))
    out += [read_ref(bridge, ref, names) for ref in refs[:8]]
    path = os.path.join(out_dir, 'fail_%s.txt' % line['case'])
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(out) + '\n')
    return out
