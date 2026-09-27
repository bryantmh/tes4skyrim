"""FO3/FNV script blocks Oblivion never emits.

`BLOCK_MAP.get(...)` returning None makes `assemble` drop the block body
entirely, so a block type absent from the table is silent data loss. FO3/FNV
author eight Oblivion does not; five have a real Papyrus event, verified
against `references/SkyrimCKWiki_210522/skyrim/<Event>_-_ObjectReference.html`.

See: docs/commentary/script_convert.md#fo3fnv-script-blocks
"""

_END = 'EndEvent'

#: FO3/FNV block type -> (Papyrus event header, terminator).
FALLOUT_BLOCK_MAP = {
    'onopen':   ('Event OnOpen(ObjectReference akActionRef)', _END),
    'onclose':  ('Event OnClose(ObjectReference akActionRef)', _END),
    'ongrab':   ('Event OnGrab()', _END),
    'onrelease': ('Event OnRelease()', _END),
    'ondestructionstagechange': (
        'Event OnDestructionStageChanged(int aiOldStage, int aiCurrentStage)',
        _END),
    'oncombatend': (
        'Event OnCombatStateChanged(Actor akTarget, int aeCombatState)', _END),
}

#: FO3/FNV blocks merged into OnCombatStateChanged, with their state guard.
FALLOUT_COMBAT_STATE_GUARDS = {'oncombatend': 'aeCombatState == 0'}

#: FO3/FNV block filters: block type -> (event parameter, Papyrus type).
FALLOUT_BLOCK_FILTER_PARAM = {
    'onopen':  ('akActionRef', 'ObjectReference'),
    'onclose': ('akActionRef', 'ObjectReference'),
}


#: FO3/FNV names only commands_falloutnv handles, so the parser reads them AS commands.
FALLOUT_HANDLED_COMMANDS = frozenset({
    'setobjectivedisplayed', 'setobjectivecompleted', 'setobjectivefailed',
    'setquestdelay',
})

#: FO3/FNV spellings of shared handlers: alias -> the handler's TES4 name.
FALLOUT_COMMAND_ALIASES = {'cios': 'cast', 'castimmediateonself': 'cast'}

#: FO3/FNV commands as COMMAND_ROWS specs (Cmd keyword arguments); 'MAP'/'ACTOR' name constants.MAP/ACTOR.
FALLOUT_COMMAND_ROWS = {
    'setenemy': dict(emit='{p0}.SetEnemy({p1}, {b2}, {b3})',
                     types={0: 'Faction', 1: 'Faction'}, defaults={2: '0', 3: '0'}),
    'setally': dict(emit='{p0}.SetAlly({p1}, {b2}, {b3})',
                    types={0: 'Faction', 1: 'Faction'}, defaults={2: '0', 3: '0'}),
    'getfactionrelation': dict(emit='{ref}.GetFactionReaction({a0})',
                               subj='ACTOR', flags='actor_only actor_arg'),
    'applyimagespacemodifier': dict(emit='{p0}.Apply({f1})',
                                    types={0: 'ImageSpaceModifier'}, defaults={1: '1.0'}),
    'removeimagespacemodifier': dict(emit='{p0}.Remove()', types={0: 'ImageSpaceModifier'}),
    'showwarning': dict(emit='Debug.MessageBox({fmt})'),
    'showmessage': dict(emit='{p0}.Show({a1}, {a2})', types={0: 'Message'},
                        defaults={1: '0.0', 2: '0.0'}),
    'addtofaction': dict(emit='{ref}.SetFactionRank({p0}, {a1})', subj='ACTOR',
                         types={0: 'Faction'}, defaults={1: '0'},
                         flags='actor_only'),
    'removefromfaction': dict(emit='{ref}.RemoveFromFaction({p0})',
                              subj='ACTOR', types={0: 'Faction'},
                              flags='actor_only'),
    'gethealthpercentage': dict(
        emit='{ref}.GetActorValuePercentage("Health")', subj='ACTOR',
        flags='actor_only zero_arg'),
    'getdestructionstage': dict(emit='{ref}.GetCurrentDestructionStage()',
                                subj='OBJREF', flags='zero_arg'),
    'getcontainerinventorycount': dict(
        emit='0', note='{f} - GetNumItems is SKSE-only, not vanilla (read as 0)',
        flags='zero_arg'),
    'hasbeeneaten': dict(
        emit='0', note='{f} - Skyrim has no cannibalism flag (read as 0)',
        flags='bare_bool cmp_bool zero_arg'),
    #: FNV's abbreviation for GetIgnoreFriendlyHits.
    'gifh': dict(emit='{ref}.IsIgnoringFriendlyHits()', subj='OBJREF',
                 flags='bare_bool cmp_bool zero_arg'),
    'getfurnituremarkerid': dict(
        emit='0', note='{f} - Skyrim has no furniture marker id (read as 0)',
        flags='zero_arg'),
    'gethitlocation': dict(
        emit='0', note='{f} - Skyrim has no hit location (read as 0)',
        flags='zero_arg'),
    'getmapmarkervisible': dict(emit='{ref}.IsMapMarkerVisible()',
                                subj='OBJREF',
                                flags='bare_bool cmp_bool zero_arg'),
    'player.gethealthpercentage': dict(
        emit='Game.GetPlayer().GetActorValuePercentage("Health")',
        flags='zero_arg'),
    'ishardcore': dict(
        emit='0', note='{f} - Skyrim has no hardcore mode (read as 0)',
        flags='zero_arg'),
    'getweaponhealthperc': dict(
        emit='0', note='{f} - Skyrim has no weapon condition (read as 0)',
        flags='zero_arg'),
    'getactorfactionplayerenemy': dict(
        emit='0', note='{f} - no Papyrus faction-enemy read (read as 0)',
        flags='zero_arg'),
    'getanimaction': dict(
        emit='0', note='{f} - Skyrim has no anim-action read (read as 0)',
        flags='zero_arg'),
    'getignorecrime': dict(
        emit='0', note='{f} - Skyrim has no ignore-crime read (read as 0)',
        flags='zero_arg'),
    'isgoredisabled': dict(
        emit='0', note='{f} - Skyrim has no gore toggle (read as 0)',
        flags='zero_arg'),
    'iswin32': dict(
        emit='1', note='{f} - always the Windows build (read as 1)',
        flags='zero_arg'),
    'getxpfornextlevel': dict(
        emit='0', note='{f} - Skyrim has no XP curve read (read as 0)',
        flags='zero_arg'),
    'enablefasttravel': dict(emit='Game.EnableFastTravel({b0})', defaults={0: '1'}),
    'ispc1stperson': dict(emit='0', note='{f} - camera state is not a vanilla native (read as 0)',
                          flags='zero_arg'),
    'isps3': dict(emit='0', note='{f} - platform check (read as 0)', flags='zero_arg'),
    'resetai': dict(emit='{ref}.EvaluatePackage()', subj='ACTOR',
                    flags='actor_only zero_arg'),
    'hasloaded3d': dict(emit='{ref}.Is3DLoaded()', subj='OBJREF', flags='zero_arg cmp_bool'),
    'getownerlasttarget': dict(emit='None', note='{f} - magic-effect owner target is not exposed (read as None)',
                               flags='zero_arg'),
    'gethasnote': dict(emit='0', note='{f} {a} - Pip-Boy notes have no Skyrim equivalent (read as 0)'),
    'restoreav': dict(emit='RestoreActorValue', subj='MAP', flags='actor_only av'),
    'restoreactorvalue': dict(emit='RestoreActorValue', subj='MAP', flags='actor_only av'),
    'damageav': dict(emit='DamageActorValue', subj='MAP', flags='actor_only av'),
    'damageactorvalue': dict(emit='DamageActorValue', subj='MAP', flags='actor_only av'),
    'sendassaultalarm': dict(emit='{ref}.SendAssaultAlarm()', subj='ACTOR',
                             flags='actor_only zero_arg'),
    'getplayerteammate': dict(emit='{ref}.IsPlayerTeammate()', subj='ACTOR',
                              flags='actor_only zero_arg cmp_bool'),
    'rewardxp': dict(note='{f} {a} - Skyrim has no experience points'),
    'addnote': dict(note='{f} {a} - Pip-Boy notes have no Skyrim equivalent'),
    'setreputation': dict(note='{f} {a} - Skyrim has no reputation'),
    'addreputation': dict(note='{f} {a} - Skyrim has no reputation'),
    'removereputation': dict(note='{f} {a} - Skyrim has no reputation'),
    'getreputation': dict(emit='0', note='{f} {a} - Skyrim has no reputation (read as 0)'),
    'getreputationthreshold': dict(emit='0', note='{f} {a} - Skyrim has no reputation (read as 0)'),
    'isplayerinregion': dict(emit='0', note='{f} {a} - Skyrim has no region query (read as 0)'),
    'hasperk': dict(emit='0', note='{f} {a} - FO3/FNV perks are not converted (read as 0)'),
    #: Rendered by commands_falloutnv.quest_native; the rows carry the Bool flags.
    'getobjectivedisplayed': dict(emit='IsObjectiveDisplayed', subj='MAP', bare=True,
                                  flags='bare_bool cmp_bool'),
    'getobjectivecompleted': dict(emit='IsObjectiveCompleted', subj='MAP', bare=True,
                                  flags='bare_bool cmp_bool'),
    'getobjectivefailed': dict(emit='IsObjectiveFailed', subj='MAP', bare=True,
                               flags='bare_bool cmp_bool'),
}
