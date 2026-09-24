"""Which skill a Morrowind trainer teaches through Skyrim's training menu.

OpenMW's `TrainingWindow::setPtr` offers the trainer's best skills, ties in
skill order, each up to the trainer's own value in it. Skyrim's menu teaches
the one skill the trainer's class names, so the best one Skyrim has is taught,
capped at the trainer's value.
"""

from ..base.constants import TES5_SKILL_ORDER
from ..dialogue.morrowind_sidecar_source import npc_services, npc_stats

#: ESM::NPC::Training, the services bit that lists Training.
_SERVICE_TRAINING = 0x4000

#: TES3 skill index -> the Skyrim skill carrying it, as MorrowindRuntime's kSkills maps them; None where Skyrim has none.
_SKYRIM_SKILL = (
    'Block', 'Smithing', 'HeavyArmor', 'HeavyArmor', 'OneHanded', 'OneHanded',
    'OneHanded', 'OneHanded', None, 'Enchanting', 'Destruction', 'Alteration',
    'Illusion', 'Conjuration', 'Illusion', 'Restoration', 'Alchemy',
    'LightArmor', 'Lockpicking', 'Sneak', None, 'LightArmor', 'OneHanded',
    'Marksman', 'Speechcraft', 'Speechcraft', 'OneHanded')


def taught_skill(skills: list):
    """(TES5_SKILL_ORDER index, cap) of the best skill Skyrim has, or None."""
    for index in sorted(range(len(_SKYRIM_SKILL)), key=lambda i: -skills[i]):
        if _SKYRIM_SKILL[index] and skills[index] > 0:
            return (TES5_SKILL_ORDER.index(_SKYRIM_SKILL[index]),
                    min(255, skills[index]))
    return None


def morrowind_trainers(tables: dict, wanted: set) -> dict:
    """{lowercased NPC id: (TES5_SKILL_ORDER index, cap)} for each trainer in
    `wanted`, as the chain's `tables` define it."""
    trainers = {}
    for key in sorted(wanted):
        rec = tables['actors'].get(key)
        if (rec is None or rec.type != 'NPC_'
                or not npc_services(rec, tables) & _SERVICE_TRAINING):
            continue
        taught = taught_skill(npc_stats(rec, tables)['skills'])
        if taught:
            trainers[key] = taught
    return trainers
