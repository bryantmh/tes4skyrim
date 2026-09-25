"""The TES4 commands that convert from DATA: one `Cmd` row each.

A row covers every command whose conversion is "resolve a receiver, convert a
few arguments, emit one template".  What needs real logic lives in
`commands.py` as a handler; `HANDLED_COMMANDS` names those so the parser still
reads them AS commands.  Every set below is a PROJECTION of `COMMAND_ROWS`, so
it is derived here, next to the table, and never before it -- deriving one
first froze the FNV rows out of `KNOWN_COMMANDS`.

See: docs/commentary/script_convert.md#command-rows
"""

from script_convert.constants import ACTOR_VALUE_MAP
from script_convert.constants_falloutnv import (FALLOUT_COMMAND_ALIASES,
                                                FALLOUT_COMMAND_ROWS,
                                                FALLOUT_HANDLED_COMMANDS)

# --------------------------------------------------------------------------
# The row type and its lookups
# --------------------------------------------------------------------------


#: Papyrus natives whose parameter N needs a cast; keyed by PAPYRUS name.
PARAM_TYPES = {
    'additem': {1: 'Int'},
    'removeitem': {1: 'Int'},
    'additemhealthpercent': {1: 'Int'},
    'addspell': {0: 'Spell'},
    'removespell': {0: 'Spell'},
    'isinfaction': {0: 'Faction'},
    'addtofaction': {0: 'Faction'},
    'removefromfaction': {0: 'Faction'},
    'getfactionrank': {0: 'Faction'},
    'setfactionrank': {0: 'Faction'},
    'modfactionrank': {0: 'Faction'},
    'tes4polyfill.update3d': {0: 'ObjectReference'},
}


def param_types(tes4_name: str) -> dict:
    """`{index: Papyrus type}` for a command, under any of its spellings."""
    direct = PARAM_TYPES.get(tes4_name)
    if direct is not None:
        return direct
    row = COMMAND_ROWS.get(tes4_name)
    emit = (row.emit or '') if row is not None else ''
    return PARAM_TYPES.get(emit.split('(')[0].lower(), {})

#: Every OBSE raw-input command converts to the same inert marker.
_OBSE_INPUT_NOTE = '{f} {a}  ;OBSE input command, no Papyrus equivalent'

ACTOR, AV, SELF, OBJREF, RAW, MAP = ('ACTOR', 'AV', 'SELF', 'OBJREF',
                                    'RAW', 'MAP')

#: The realm list property: the plugin's crime factions, main realm first.
CRIME_REALMS = ('TES4CrimeFactions', 'FormList')

#: The crime faction of the realm the player is in.
CRIME_FACTION = 'TES4Polyfill.CrimeFaction(TES4CrimeFactions)'


class Cmd:
    """One command's conversion, as data.

    `emit_row` in `emit/commands.py` renders it; that module's docstring is the
    spec for `emit`'s placeholders and for `subj`.
    """

    __slots__ = ('emit', 'subj', 'types', 'defaults', 'note', 'self_type',
                 'bare', 'arms', 'flags', 'max_args')

    def __init__(self, emit='0', subj=SELF, types=(), defaults=(), note='',
                 self_type=None, bare=False, arms=(), flags='', max_args=None):
        """Store one row; see the class docstring for each field's meaning."""
        self.emit = emit
        self.subj = subj
        self.types = dict(types)
        self.defaults = dict(defaults)
        self.note = note
        self.self_type = self_type
        self.bare = bare
        #: Papyrus arity; arguments past it are dropped.
        self.max_args = max_args
        #: The two alternatives a `{?n<want>}` placeholder picks between.
        self.arms = arms
        #: Per-command properties, space separated; `HAS_FLAG` is the vocabulary.
        self.flags = frozenset(flags.split()) if flags else frozenset()


#: See: docs/commentary/script_convert.md#command-rows
COMMAND_ROWS = {
    #: Int because TES4 call sites compare and assign 0/1.
    'isanimplaying': Cmd(
        '({ref}.GetAnimationVariableBool("bAnimPlaying") as Int)', OBJREF),

    #: GetArmorRating -> DamageResist actor value (what armor rating feeds).
    'getarmorrating': Cmd('{ref}.GetActorValue("DamageResist")', ACTOR, flags='actor_only'),

    #: Skyrim marks people with ActorTypeNPC; converted races lack it.
    'getiscreature': Cmd('TES4Polyfill.GetIsCreature({ref})', ACTOR, flags='actor_arg zero_arg'),
    'iscreature': Cmd('TES4Polyfill.GetIsCreature({ref})', ACTOR, flags='actor_arg'),

    #: IsGuard: membership in Skyrim's guard dialogue faction.
    'isguard': Cmd('TES4Polyfill.IsGuard({ref})', ACTOR, flags='actor_arg zero_arg'),

    #: No Papyrus refraction control; an alpha fade is the closest visual.
    'setactorrefraction': Cmd(
        'TES4Polyfill.SetActorRefraction({ref}, {a0})', ACTOR,
        defaults={0: '0'}, flags='actor_arg'),

    #: NOT DrawWeapon. See: docs/commentary/script_convert.md#paired-onoff-commands-asymmetric-map
    'setalert': Cmd('{ref}.SetAlert({b0})', ACTOR, defaults={0: '0'}),

    #: Reset3DState -> MoveTo self (reloads 3D).
    'reset3dstate': Cmd('{ref}.MoveTo({ref})'),

    #: SetRestrained -> SetDontMove.
    'setrestrained': Cmd('{ref}.SetDontMove({b0})', ACTOR, defaults={0: '0'}),

    #: IsOnGround: Skyrim has only the inverse.
    'isonground': Cmd('!({ref}.IsFlying())', RAW),

    #: IsInAir: cast to Int because TES4 call sites compare/assign 0/1.
    'isinair': Cmd('({ref}.IsFlying() as Int)', ACTOR),

    #: IsAlarmed is the nearest state: noticed a hostile action.
    'getattacked': Cmd('({ref}.IsAlarmed() as Int)', ACTOR, flags='zero_arg'),

    #: IsActorUsingATorch: equipped-item type 11 is the torch slot.
    'isactorusingatorch': Cmd('({ref}.GetEquippedItemType(0) == 11)', ACTOR, flags='cmp_bool'),

    #: Unlock takes no argument in TES4; Skyrim's Lock(false) is the unlock.
    'unlock': Cmd('{ref}.Lock(false)', OBJREF),

    #: Identity, not an actor read. See: docs/commentary/script_convert.md#getisreference-not-an-actor-test
    'getisreference': Cmd('{ref} == {a0}', OBJREF, defaults={0: 'None'}),
    'getisref': Cmd('{ref} == {a0}', OBJREF, defaults={0: 'None'}, flags='cmp_bool'),

    #: Places a fresh instance of the BASE, the copy callers want.
    'createfullactorcopy': Cmd(
        '{ref}.PlaceAtMe({ref}.GetActorBase())', ACTOR),

    #: Exact natives both ways. See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'getpcexpelled': Cmd('{a0}.IsPlayerExpelled()', types={0: 'Faction'},
                         defaults={0: 'None'}),
    'ispcexpelled': Cmd('{a0}.IsPlayerExpelled()', types={0: 'Faction'},
                        defaults={0: 'None'}),
    'isexpelled': Cmd('{a0}.IsPlayerExpelled()', types={0: 'Faction'},
                      defaults={0: 'None'}),

    #: OBSE printf-style: a format string plus its arguments.
    'printtoconsole': Cmd('Debug.Trace({fmt})'),
    'printc': Cmd('Debug.Trace({fmt})'),

    #: Button list is dropped. See: docs/commentary/script_convert.md#obse-constructs-no-shape-to-translate
    'messageboxex': Cmd('Debug.MessageBox({fmt})'),
    'messageex': Cmd('Debug.MessageBox({fmt})'),

    #: The animation graph answers this natively; no SKSE dependency.
    'iscasting': Cmd('({ref}.GetAnimationVariableBool("bIsCastingRight") || '
                     '{ref}.GetAnimationVariableBool("bIsCastingLeft"))', ACTOR, flags='bare_bool zero_arg'),

    #: Value only, AV implicit. See: docs/commentary/script_convert.md#argument-that-looks-ignorable
    'setcurrenthealth': Cmd('{ref}.SetActorValue("Health", {a0})', RAW,
                            defaults={0: '0'}),

    #: SetPCExpelled: Skyrim's exact native.  See getpcexpelled above.
    'setpcexpelled': Cmd('{p0}.SetPlayerExpelled({b1})', types={0: 'Faction'},
                         defaults={0: 'None', 1: '1'}),

    #: Not promoted. See: docs/commentary/script_convert.md#commands-that-must-not-promote
    'sms': Cmd('{p0}.Stop({ref})', OBJREF, types={0: 'EffectShader'},
               defaults={0: 'Self'}),
    'stopmagicshadervisuals': Cmd(
        '{p0}.Stop({ref})', OBJREF, types={0: 'EffectShader'},
        defaults={0: 'Self'}),

    #: `pms`/PlayMagicShaderVisuals: same ObjectReference contract as `sms`.
    'pms': Cmd('{p0}.Play({ref}, {a1})', OBJREF, types={0: 'EffectShader'},
               defaults={0: 'Self', 1: '-1.0'}),
    'playmagicshadervisuals': Cmd(
        '{p0}.Play({ref}, {a1})', OBJREF, types={0: 'EffectShader'},
        defaults={0: 'Self', 1: '-1.0'}),

    #: GetIsCurrentWeather / GetWeatherPercent: Weather.psc globals.
    'getweatherpercent': Cmd('Weather.GetCurrentWeatherTransition()'),
    'getcurrentweatherpercent': Cmd('Weather.GetCurrentWeatherTransition()'),
    'getiscurrentweather': Cmd('(Weather.GetCurrentWeather() == {a0})',
                               types={0: 'Weather'}, defaults={0: 'None'}),

    #: abOverride FALSE. See: docs/commentary/script_convert.md#weather-holds-reapplication-vs-lock
    'forceweather': Cmd('{p0}.ForceActive(False)', types={0: 'Weather'}),
    'fw': Cmd('{p0}.ForceActive(False)', types={0: 'Weather'}),
    'setweather': Cmd('{p0}.SetActive(False, False)', types={0: 'Weather'}),
    'sw': Cmd('{p0}.SetActive(False, False)', types={0: 'Weather'}),

    #: Consumes the receiver. See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
 
    # --------------------------------------------------------------------------
    # Commands with no Papyrus equivalent
    # --------------------------------------------------------------------------
    #: See: docs/commentary/script_convert.md#neutralised-command-inert-in-position

    #: No cell-coordinate move. See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer
    'positioncell': Cmd(note='PositionCell needs a target marker; Papyrus '
                             'MoveTo takes a reference, not cell '
                             'coordinates ({a})'),

    #: See: docs/commentary/script_convert.md#obse-constructs-no-shape-to-translate
    'runscriptline': Cmd(note='{f} - OBSE console execution, no Papyrus '
                              'equivalent ({f} {a})'),
    'runbatchscript': Cmd(note='{f} - OBSE console execution, no Papyrus '
                               'equivalent ({f} {a})'),

    #: See: docs/commentary/script_convert.md#obse-constructs-no-shape-to-translate
    'seteventhandler': Cmd(
        note='{f} - OBSE event registration; Papyrus binds events by '
             'declaring them on the attached script ({f} {a})'),
    'removeeventhandler': Cmd(
        note='{f} - OBSE event registration; Papyrus binds events by '
             'declaring them on the attached script ({f} {a})'),

    'getcrosshairref': Cmd(
        'None', note='getCrosshairRef has no Papyrus equivalent (read as None)'),
    'getcrosshairreference': Cmd(
        'None', note='getCrosshairRef has no Papyrus equivalent (read as None)', flags='branch_only'),
    'getstringgamesetting': Cmd(
        '""', note='GetStringGameSetting has no Papyrus equivalent (read as "")'),
    'getpackagetarget': Cmd(
        'None', note='getPackageTarget has no Papyrus equivalent (read as None)'),

    #: NOT StopCombat -- opposite direction. See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
    'scaonactor': Cmd('{ref}.StopCombatAlarm()', AV),
    'sca': Cmd('{ref}.StopCombatAlarm()', AV),
    'stopcombatalarmonactor': Cmd('{ref}.StopCombatAlarm()', AV, flags='zero_arg'),
    #: ClearOwnership
    'clearownership': Cmd('{ref}.SetActorOwner(Game.GetPlayer().GetActorBase())', SELF, flags='zero_arg'),
    #: Reset → ref.Reset()
    'reset': Cmd('{ref}.Reset()', AV, flags='branch_only objref_self'),
    #: DeleteFullActorCopy
    'deletefullactorcopy': Cmd('{ref}.Delete()', AV, flags='branch_only'),
    'opendoor': Cmd('{ref}.SetOpen(true)', SELF),
    'closedoor': Cmd('{ref}.SetOpen(false)', SELF),
    'getsize': Cmd('{ref}.GetScale()', SELF),
    #: ResetHealth: TES4 ResetHealth -> RestoreActorValue("Health", 9999)
    'resethealth': Cmd('{ref}.RestoreActorValue("Health", 9999)', AV, flags='actor_only branch_only'),
    #: Skyrim takes no args, so the TES4 package argument is dropped.
    'evaluatepackage': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_arg actor_only zero_arg'),
    'evp': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_arg actor_only'),
    'addscriptpackage': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_only drop_args'),
    'removescriptpackage': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_only drop_args'),
    'stopwaiting': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_only'),
    #: ClearLookAt / StopLook: Skyrim version takes no args (drop TES4 target arg)
    'clearlookat': Cmd('{ref}.ClearLookAt()', AV, flags='actor_only'),
    'stoplook': Cmd('{ref}.ClearLookAt()', AV, flags='actor_only zero_arg'),
    'stoplooking': Cmd('{ref}.ClearLookAt()', AV, flags='actor_only'),
    #: GetEquippedItemType: Skyrim requires hand param (0=left, 1=right)
    'getweaponanimtype': Cmd('{ref}.GetEquippedItemType(1)', AV, flags='actor_only zero_arg'),
    'getequippeditemtype': Cmd('{ref}.GetEquippedItemType(1)', AV, flags='branch_only'),
    #: IsRidingHorse: Actor.IsOnMount() in Skyrim
    'isridinghorse': Cmd('{ref}.IsOnMount()', AV, flags='cmp_bool'),
    #: GetRace: ref.GetRace() -> ref.GetRace()
    'getrace': Cmd('{ref}.GetRace()', AV, flags='actor_only'),
    #: IsInInterior: ref.IsInInterior -> ref.GetParentCell().IsInterior()
    'isininterior': Cmd('{ref}.GetParentCell().IsInterior()', AV, flags='bare_bool cmp_bool objref_self'),
    #: GetContainer: item.GetContainer -> item.GetContainer()
    'getcontainer': Cmd('{ref}.GetContainer()', SELF),
    #: Crime/fame/infamy writes: argument 0 cast to what the setter declares.
    'setcrimegold': Cmd(f'TES4Polyfill.SetCrimeGold({CRIME_FACTION}, {{i0}})', self_type=CRIME_REALMS, flags='actor_only'),
    'modcrimegold': Cmd(f'{CRIME_FACTION}.ModCrimeGold({{c0}}, false)', self_type=CRIME_REALMS, flags='actor_only'),
    'modpcfame': Cmd('TES4Fame.Mod({f0})', self_type=('TES4Fame', 'GlobalVariable')),
    'modpcinfamy': Cmd('TES4Infamy.Mod({f0})', self_type=('TES4Infamy', 'GlobalVariable')),
    'setpcfame': Cmd('TES4Fame.SetValueInt({i0})', self_type=('TES4Fame', 'GlobalVariable')),
    'setpcinfamy': Cmd('TES4Infamy.SetValueInt({i0})', self_type=('TES4Infamy', 'GlobalVariable')),
    #: Crime calls act on the realm the player is in; PayFine confiscates and releases.
    'gotojail': Cmd(f'{CRIME_FACTION}.SendPlayerToJail()', self_type=CRIME_REALMS),
    'getcrimegold': Cmd(f'{CRIME_FACTION}.GetCrimeGold()', self_type=CRIME_REALMS, flags='actor_only'),
    'payfine': Cmd(f'{CRIME_FACTION}.PlayerPayCrimeGold(true, true)', self_type=CRIME_REALMS),
    'payfinethief': Cmd(f'{CRIME_FACTION}.PlayerPayCrimeGold(false, false)', self_type=CRIME_REALMS),
    'getplayerinseworld': Cmd('TES4Polyfill.CrimeRealm(TES4CrimeFactions)', self_type=CRIME_REALMS),
    #: Fame/Infamy → GlobalVariable
    'getpcfame': Cmd('TES4Fame.GetValueInt()', self_type=('TES4Fame', 'GlobalVariable')),
    'getpcinfamy': Cmd('TES4Infamy.GetValueInt()', self_type=('TES4Infamy', 'GlobalVariable')),
    'getinfame': Cmd('TES4Infamy.GetValueInt()', self_type=('TES4Infamy', 'GlobalVariable')),
    #: See: docs/commentary/script_convert.md#14-getdayofweek-had-two-conversions
    'getdayofweek': Cmd('(GameDaysPassed.GetValueInt() % 7)', self_type=('GameDaysPassed', 'GlobalVariable')),
    'getdayoftheweek': Cmd('(GameDaysPassed.GetValueInt() % 7)', self_type=('GameDaysPassed', 'GlobalVariable')),
    #: GetAmountSoldStolen: gold fenced, paired with ModAmountSoldStolen above.
    'getamountsoldstolen': Cmd('TES4GoldFenced.GetValue()', self_type=('TES4GoldFenced', 'GlobalVariable')),
    #: See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'getplayercontrolsdisabled': Cmd('TES4ControlsDisabled.GetValue()', self_type=('TES4ControlsDisabled', 'GlobalVariable')),
    'getplayercontrolsdisabled_': Cmd('TES4ControlsDisabled.GetValue()', self_type=('TES4ControlsDisabled', 'GlobalVariable'), flags='branch_only'),

    #: See: docs/commentary/script_convert.md#advancepclevel-level-actor-value
    'advancepclevel': Cmd('Game.GetPlayer().ModActorValue("Level", 1)'),
    #: Slot name dropped. See: docs/commentary/script_convert.md#argument-that-looks-ignorable
    'con_save': Cmd('Game.RequestSave()'),
    'con_savegame': Cmd('Game.RequestSave()'),
    'getdisposition': Cmd('50'),
    #: GetIsPlayableRace
    'getisplayablerace': Cmd('true', flags='zero_arg'),
    'getplayerinjail': Cmd('Game.GetPlayer().IsArrested()'),
    #: GetRandomPercent -> Utility.RandomInt(0, 99)
    'getrandompercent': Cmd('Utility.RandomInt(0, 99)'),
    #: VampireStatus is 1 exactly while the vampire has recently fed.
    'hasvampirefed': Cmd('TES4Polyfill.HasVampireFed()'),
    #: Jail, NOT expulsion. See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'isplayerinjail': Cmd('Game.GetPlayer().IsArrested()'),
    'isplayerinprison': Cmd('Game.GetPlayer().IsArrested()'),
    'isthirdperson': Cmd('False'),
    'releaseweatheroverride': Cmd('Weather.ReleaseOverride()'),
    'savegame': Cmd('Game.RequestSave()'),
    'senttojail': Cmd('Game.GetPlayer().IsArrested()'),
    'triggerhitshader': Cmd('Game.TriggerScreenBlood(3)'),

    'addachievement': Cmd(note='{f}'),
    'addflames': Cmd(note='{f} has no Skyrim equivalent', flags='zero_arg'),
    'attachashpile': Cmd(note='{f}'),
    'bookread': Cmd(note='GetBookRead'),
    'disablelinkedpathpoints': Cmd(note='{f}', flags='zero_arg'),
    'enablelinkedpathpoints': Cmd(note='{f}', flags='zero_arg'),
    'essentialdeathreload': Cmd(note='{f}'),
    'flamesoff': Cmd(note='{f} has no Skyrim equivalent'),
    'flameson': Cmd(note='{f} has no Skyrim equivalent'),
    #: ForceFlee → StartCombat avoidance (approximate)
    'getaltcontrol': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    #: GetBookRead -> no direct equivalent, return 0
    'getbookread': Cmd(note='{f}'),
    #: Bare literal, operand position. See: docs/commentary/script_convert.md#neutralised-command-inert-in-position
    'getcontrol': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'getcrimeknown': Cmd(note='{f}'),
    'getcurrentaipackage': Cmd(note='{f}', flags='zero_arg'),
    'getcurrentaiprocedure': Cmd(note='{f}', flags='zero_arg'),
    'getcurrentpackage': Cmd(note='{f}'),
    'getfullgoldvalue': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    #: Bare literal, operand position. See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer
    'getgamerestarted': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    #: IgnoreFriendlyHits is a SETTER only; Papyrus cannot read it back.
    'getignorefriendlyhits': Cmd(note='GetIgnoreFriendlyHits — Skyrim exposes only the setter', flags='bare_bool zero_arg'),
    'getisalerted': Cmd(note='{f}', flags='zero_arg'),
    #: The CK wiki names IsAlarmed as GetAlarmed's own Papyrus version.
    'getalarmed': Cmd('{ref}.IsAlarmed()', ACTOR,
                      flags='actor_only zero_arg cmp_bool'),
    'getdisease': Cmd(note='{f} has no Papyrus equivalent (read as 0)',
                      flags='zero_arg'),
    'getfriendhit': Cmd(note='{f} has no Papyrus equivalent (read as 0)',
                        flags='zero_arg'),
    'getwantblocking': Cmd(note='{f} has no Papyrus equivalent (read as 0)',
                           flags='zero_arg'),
    'ismoving': Cmd(note='{f} has no Papyrus equivalent (read as 0)',
                    flags='zero_arg'),
    'isturning': Cmd(note='{f} has no Papyrus equivalent (read as 0)',
                     flags='zero_arg'),
    'getisplayerbirthsign': Cmd(note='{f}'),
    'getitems': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'getmousecontrol': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'getnumericinisetting': Cmd(note='GetNumericINISetting has no Papyrus equivalent (read as 0)'),
    #: Bare literal. See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer
    'getobjecttype': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    #: The engine tracks no "last ridden" horse, and SKSE adds none.
    'getplayerhaslastriddenhorse': Cmd(note='{f} has no Skyrim equivalent', flags='bare_bool zero_arg'),
    'getrestrained': Cmd(note='GetRestrained', flags='zero_arg'),
    'getstartingpos': Cmd(note='{f}'),
    'gettalkedtopc': Cmd(note='GetTalkedToPC', flags='cmp_bool zero_arg'),
    'gettalkedtopcp': Cmd(note='GetTalkedToPC'),
    'gettype': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'getweaponskilltype': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'hasbeenpickedup': Cmd(note='{f}'),
    #: Skyrim lights carry no scriptable flame state.
    'hasflames': Cmd(note='HasFlames has no Skyrim equivalent', flags='bare_bool zero_arg'),
    'hasvariable': Cmd(note='{f}'),
    #: No argument. See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
    'isactordetected': Cmd(note='IsActorDetected (no Skyrim equivalent)', flags='cmp_bool'),
    'isbuttonpressed': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'iscontrolpressed': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'iscurrentfurnitureobj': Cmd('TES4Polyfill.IsCurrentFurnitureObj({ref}, {p0})',
                                 ACTOR, types={0: 'Form'}, flags='actor_only'),
    'iscurrentfurnitureref': Cmd('TES4Polyfill.IsCurrentFurnitureRef({ref}, {p0})',
                                 ACTOR, types={0: 'ObjectReference'},
                                 flags='actor_only'),
    'isidleplaying': Cmd(note='{f}', flags='zero_arg'),
    'isindangerouswater': Cmd(note='{f}', flags='zero_arg'),
    #: Bare 0, operand position. See: docs/commentary/script_convert.md#neutralised-command-inert-in-position
    'iskeypressed': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'iskeypressed2': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'iskeypressed3': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'isonguard': Cmd(note='{f}'),
    'isplayable': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'isplayable2': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'isplayermovingintonewspace': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'isplayerslastriddenhorse': Cmd(note='{f} has no Skyrim equivalent', flags='bare_bool zero_arg'),
    #: IsSwimming → no vanilla equivalent, approximate with submerged check
    'isswimming': Cmd(note='IsSwimming', flags='actor_only bare_bool cmp_bool zero_arg'),
    'istimepassing': Cmd(note='{f}'),
    'menumode': Cmd(note='{f}'),
    'offerhorse': Cmd(note='{f}'),
    'opencurrentcontainer': Cmd(note='{f}'),
    'pcb': Cmd(note='{f}'),
    'playbink': Cmd(note='{f}'),
    'purgecellbuffers': Cmd(note='{f}'),
    'refreshtopiclist': Cmd(note='{f}'),
    'removeflames': Cmd(note='{f} has no Skyrim equivalent', flags='zero_arg'),
    'removetopic': Cmd(note='{f}'),
    'respawnhorse': Cmd(note='{f}'),
    #: Rotate → no-op
    'rotate': Cmd(note='Rotate'),
    'sendtrespassalarm': Cmd(note='{f}'),
    #: SetActorFullName → no-op (SKSE required for SetDisplayName)
    'setactorfullname': Cmd(note='SetActorFullName'),
    #: SetActorsAI → no-op
    'setactorsai': Cmd(note='SetActorsAI'),
    'setallreachable': Cmd(note='{f}'),
    'setallvisible': Cmd(note='{f}'),
    #: SetCellFullName no-op
    'setcellfullname': Cmd(note='{f}'),
    'setcellownership': Cmd(note='{f}'),
    'setcellpublicflag': Cmd(note='{f}'),
    'setclass': Cmd(note='{f}'),
    #: SetCombatStyle → no-op (managed by CK/race)
    'setcombatstyle': Cmd(note='SetCombatStyle'),
    #: Form.psc has no name setter. See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer
    'setdisplayname': Cmd(note='{f}'),
    'setdoordisabletakeoff': Cmd(note='{f}'),
    #: SetForceSneaking
    'setforcesneak': Cmd(note='SetForceSneak', flags='actor_only'),
    'setignorefriendlyhits': Cmd(note='{f}'),
    #: SetInCharGen: no-op
    'setinchargen': Cmd(note='SetInCharGen'),
    'setinvestmentgold': Cmd(note='{f}'),
    #: SetItemValue → no-op
    'setitemvalue': Cmd(note='SetItemValue'),
    #: SetLevel → no-op
    'setlevel': Cmd(note='SetLevel'),
    'setname': Cmd(note='{f}'),
    'setnoavoidance': Cmd(note='{f}'),
    'setnorumors': Cmd(note='{f}'),
    'setpackduration': Cmd(note='{f}'),
    #: SetPlayerInSEWorld: which realm's bounty the crime calls read and write.
    'setplayerinseworld': Cmd('TES4Polyfill.SetCrimeRealm(TES4CrimeFactions, {i0})', self_type=CRIME_REALMS),
    'setpublic': Cmd(note='{f}'),
    'setquestobject': Cmd(note='{f}'),
    #: SetRigidBodyMass → no-op
    'setrigidbodymass': Cmd(note='SetRigidBodyMass'),
    'setsceneiscomplex': Cmd(note='{f}'),
    'setshowquestitems': Cmd(note='{f}'),
    'showdialogsubtitles': Cmd(note='{f}'),
    'showenchantment': Cmd(note='{f}'),
    'showspellmaking': Cmd(note='{f}'),
    'stopsound': Cmd(note='StopSound has no Papyrus equivalent'),
    'trapupdate': Cmd(note='{f}'),
    #: Wait → no-op (TES4 Wait is a package instruction, not a time delay)
    'wait': Cmd(note='Wait is a package instruction'),
    #: No-op by design. See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer
    'wakeuppc': Cmd(note='WakeUpPC (no Skyrim equivalent; body runs in OnSleepStart)'),

    # --------------------------------------------------------------------------
    # OBSE / TES4-only commands with no VANILLA Papyrus
    # --------------------------------------------------------------------------
    #: See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer
    'preloadmagiceffect': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'closeallmenus': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setmodelpath': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getmodelpath': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setlowlevelprocessing': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setharvested': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'selectplayerspell': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setquestitem': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setpcamurderer': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setcellwaterheight': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setstringinisetting': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setstringgamesettingex': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getobseversion': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    #: getfirstref/getnextref are NOT here. See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'getformfrommod': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getaltcontrol2': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'sifh': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'equipme': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'modavmod': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getvelocity': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setvelocity': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'isunderwater': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getvampire': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getweapontype': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'iswaiting': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getnumfollowers': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getnthfollower': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getspells': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setattackdamage': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'togglespecialanim': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setavmod': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'starttimer': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getmodlocaldata': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setmodlocaldata': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setaltcontrol': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    #: See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer
    'setplayerskeletonpath': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getplayerskeletonpath': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    #: Form-type tests and fileexists are NOT here -- both need handlers.
    'getgodmode': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getplayerbirthsign': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getdisplayname': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getname': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    #: Dead by construction. See: docs/commentary/script_convert.md#neutralised-command-inert-in-position
    'getavmodf': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setavmodf': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),

    # --------------------------------------------------------------------------
    # Commands the generic mapped-call rendering covers.
    # --------------------------------------------------------------------------
    # --------------------------------------------------------------------------
    # Actor Values
    # --------------------------------------------------------------------------
    'getactorvalue': Cmd('GetActorValue', MAP, flags='actor_only av av_read'),
    'setactorvalue': Cmd('SetActorValue', MAP, flags='actor_only av'),
    'modactorvalue': Cmd('ModActorValue', MAP, flags='actor_only av'),
    'forceactorvalue': Cmd('ForceActorValue', MAP, flags='actor_only av'),
    'getav': Cmd('GetActorValue', MAP, flags='actor_only av av_read'),
    'setav': Cmd('SetActorValue', MAP, flags='actor_only av'),
    'modav': Cmd('ModActorValue', MAP, flags='actor_only av'),
    'forceav': Cmd('ForceActorValue', MAP, flags='actor_only av'),
    'getbaseactorvalue': Cmd('GetBaseActorValue', MAP, flags='actor_only av av_read'),
    'getbaseav': Cmd('GetBaseActorValue', MAP, flags='actor_only av av_read'),

    #: --- Items / Inventory ---
    'additem': Cmd('AddItem', MAP, flags='actor_only objref_shared'),
    'removeitem': Cmd('RemoveItem', MAP, flags='actor_only objref_shared'),
    'getitemcount': Cmd('GetItemCount', MAP, flags='actor_only objref_self objref_shared'),
    'equipitem': Cmd('EquipItem', MAP, flags='actor_only'),
    'unequipitem': Cmd('UnequipItem', MAP, flags='actor_only'),
    'getnumitems': Cmd('GetNumItems', MAP),
    'getinventoryobject': Cmd('GetNthForm', MAP),
    'drop': Cmd('DropObject', MAP),

    #: --- Spells ---
    'addspell': Cmd('AddSpell', MAP, flags='actor_only'),
    'removespell': Cmd('RemoveSpell', MAP, flags='actor_only'),
    'hasspell': Cmd('HasSpell', MAP, flags='actor_only cmp_bool'),
    'dispel': Cmd('DispelSpell', MAP, flags='actor_only'),
    'dispelspell': Cmd('DispelSpell', MAP, flags='actor_only'),
    'dispelallspells': Cmd('DispelAllSpells', MAP, flags='actor_only zero_arg'),

    #: --- Movement / Position ---
    'getdistance': Cmd('GetDistance', MAP, flags='actor_only objref_shared'),
    'getparentcell': Cmd('GetParentCell', MAP, flags='objref_self'),
    'setposition': Cmd('SetPosition', MAP),
    'getlinkedref': Cmd('GetLinkedRef', MAP, flags='objref_self'),
    'getheadingangle': Cmd('GetHeadingAngle', MAP),

    #: --- Enable / Disable ---
    'enable': Cmd('Enable', MAP, flags='objref_self'),
    'disable': Cmd('Disable', MAP, flags='objref_self'),
    'isenabled': Cmd('IsEnabled', MAP, flags='bare_bool cmp_bool'),
    'activate': Cmd('Activate', MAP, flags='objref_self'),
    'delete': Cmd('Delete', MAP, flags='objref_self'),
    'markfordelete': Cmd('Delete', MAP, flags='zero_arg'),
    'placeatme': Cmd('PlaceAtMe', MAP, flags='actor_only objref_shared'),
    #: Native SetDestroyed (4300/0x10CC) has NO reader, so the polyfill mirrors every write into TES4DestroyedRefs.

    #: --- Actor State ---

    #: See: docs/commentary/script_convert.md#kill-takes-only-the-killer
    'kill': Cmd('Kill', MAP, max_args=1, flags='actor_only actor_arg'),
    'killandresurrect': Cmd('Kill', MAP), # then Resurrect manually
    'resurrect': Cmd('Resurrect', MAP, flags='actor_only drop_args'),
    'getdead': Cmd('IsDead', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    'isdead': Cmd('IsDead', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    'isincombat': Cmd('IsInCombat', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    #: SetForceSneak is inert, so live sneak state is the closest read.
    'getforcesneak': Cmd('IsSneaking', MAP, flags='actor_only bare_bool zero_arg'),
    #: TES4 knocked-down state ~ Skyrim's bleedout/recovery state.
    'getknockedstate': Cmd('IsBleedingOut', MAP, flags='actor_only bare_bool zero_arg'),
    'startcombat': Cmd('StartCombat', MAP, flags='actor_arg actor_only'),
    'stopcombat': Cmd('StopCombat', MAP, flags='actor_only drop_args zero_arg'),
    #: See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
    'issneaking': Cmd('IsSneaking', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    'isweaponout': Cmd('IsWeaponDrawn', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    'getsitting': Cmd('GetSitState', MAP, flags='actor_only zero_arg'),
    'getsleeping': Cmd('GetSleepState', MAP, flags='actor_only zero_arg'),
    'getequipped': Cmd('IsEquipped', MAP, flags='actor_only'),
    'istalking': Cmd('IsInDialogueWithPlayer', MAP, flags='zero_arg'),
    'setunconscious': Cmd('SetUnconscious', MAP, flags='actor_only'),
    'setghost': Cmd('SetGhost', MAP, flags='actor_only'),
    'isghost': Cmd('IsGhost', MAP, flags='actor_only bare_bool cmp_bool'),
    #: Getters were unmapped. See: docs/commentary/script_convert.md#zero-arg-reads-need-function-map
    'getisghost': Cmd('IsGhost', MAP),
    'getunconscious': Cmd('IsUnconscious', MAP),
    'resetai': Cmd('ResetAI', MAP, flags='zero_arg'),

    #: --- Factions ---
    'getinfaction': Cmd('IsInFaction', MAP, flags='actor_only cmp_bool'),
    'getfactionrank': Cmd('GetFactionRank', MAP, flags='actor_only'),
    'setfactionrank': Cmd('SetFactionRank', MAP, flags='actor_only'),
    'modfactionrank': Cmd('ModFactionRank', MAP),
    'addfaction': Cmd('AddToFaction', MAP),
    'removefaction': Cmd('RemoveFromFaction', MAP),
    'removefromfaction': Cmd('RemoveFromFaction', MAP),

    # --------------------------------------------------------------------------
    # AI
    # --------------------------------------------------------------------------
    #: setforcerun is NOT here. See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem

    #: --- Quest ---
    'setstage': Cmd('SetStage', MAP, bare=True),
    'getstage': Cmd('GetStage', MAP, bare=True),
    'getstagedone': Cmd('GetStageDone', MAP, bare=True, flags='cmp_bool'),
    'startquest': Cmd('Start', MAP, bare=True),
    'stopquest': Cmd('Stop', MAP, bare=True),
    'getquestrunning': Cmd('IsRunning', MAP, bare=True, flags='cmp_bool'),
    'isquestcompleted': Cmd('IsCompleted', MAP, bare=True),
    'completequest': Cmd('CompleteQuest', MAP, bare=True),

    #: --- UI / Messages ---
    'message': Cmd('Debug.Notification', MAP, bare=True),
    'messagebox': Cmd('Debug.MessageBox', MAP, bare=True),
    'showmessage': Cmd('Debug.MessageBox', MAP, bare=True),

    # --------------------------------------------------------------------------
    # Math (OBSE)
    # --------------------------------------------------------------------------
    #: Both engines use DEGREES. See: docs/commentary/script_convert.md#obse-constructs-no-shape-to-translate
    'sin': Cmd('Math.sin', MAP, bare=True),
    'cos': Cmd('Math.cos', MAP, bare=True),
    'tan': Cmd('Math.tan', MAP, bare=True),
    'asin': Cmd('Math.asin', MAP, bare=True),
    'acos': Cmd('Math.acos', MAP, bare=True),
    'atan': Cmd('Math.atan', MAP, bare=True),
    'sqrt': Cmd('Math.sqrt', MAP, bare=True),
    'pow': Cmd('Math.pow', MAP, bare=True),
    'abs': Cmd('Math.abs', MAP, bare=True),
    'floor': Cmd('Math.Floor', MAP, bare=True),
    'ceil': Cmd('Math.Ceiling', MAP, bare=True),
    'exp': Cmd('TES4Polyfill.Exp', MAP, bare=True),
    'log': Cmd('TES4Polyfill.Log', MAP, bare=True),

    # --------------------------------------------------------------------------
    # OBSE "NS"/silent variants
    # --------------------------------------------------------------------------
    #: abSilent on the same native. See: docs/commentary/script_convert.md#argument-that-looks-ignorable
    'additemns': Cmd('AddItem', MAP),
    'removeitemns': Cmd('RemoveItem', MAP),
    'addspellns': Cmd('AddSpell', MAP),
    'removespellns': Cmd('RemoveSpell', MAP),
    'equipitemsilent': Cmd('EquipItem', MAP),
    'equipitemns': Cmd('EquipItem', MAP),
    'unequipitemns': Cmd('UnequipItem', MAP),
    #: abSilent on the same native. See: docs/commentary/script_convert.md#argument-that-looks-ignorable
    'equipitem2ns': Cmd('EquipItem', MAP),
    'unequipitem2': Cmd('UnequipItem', MAP),
    'unequipitem2ns': Cmd('UnequipItem', MAP),
    'unequipitemsilent': Cmd('UnequipItem', MAP),
    #: OBSE aliases that only widen the vanilla command's argument types.
    'modav2': Cmd('ModActorValue', MAP, flags='av'),
    'modactorvalue2': Cmd('ModActorValue', MAP, flags='av'),
    'getav2': Cmd('GetActorValue', MAP, flags='av av_read'),
    'setav2': Cmd('SetActorValue', MAP, flags='av'),
    'rand': Cmd('Utility.RandomFloat', MAP, bare=True),
    'islocked': Cmd('IsLocked', MAP, flags='bare_bool objref_self'),
    'getequippedobject': Cmd('GetEquippedWeapon', MAP),
    #: PlayGamebryoAnimation is Skyrim's own looping-animation call.
    'loopgroup': Cmd('PlayGamebryoAnimation', MAP),
    #: See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'ismodloaded': Cmd('TES4Polyfill.IsModLoaded', MAP, bare=True),
    #: See: docs/commentary/script_convert.md#zero-arg-reads-need-function-map
    'equipitem2': Cmd('EquipItem', MAP),
    #: See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
    'isplugininstalled': Cmd('TES4Polyfill.IsModLoaded', MAP, bare=True),
    #: Debug.Trace is Papyrus's own log write, the same capability.
    'print': Cmd('Debug.Trace', MAP, bare=True),

    # --------------------------------------------------------------------------
    # OBSE commands with no VANILLA Papyrus equivalent (neutralised)
    # --------------------------------------------------------------------------
    #: See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer

    # --------------------------------------------------------------------------
    # Camera / 3D refresh (OBSE)
    # --------------------------------------------------------------------------
    #: See: docs/commentary/script_convert.md#argument-that-looks-ignorable

    #: --- Game State ---
    'getgamesetting': Cmd('Game.GetGameSettingFloat', MAP, bare=True),
    'getgs': Cmd('Game.GetGameSettingFloat', MAP, bare=True),
    'getpcinfaction': Cmd('Game.GetPlayer().IsInFaction', MAP, bare=True),
    'showracemenu': Cmd('Game.ShowRaceMenu', MAP, bare=True),
    'getlevel': Cmd('GetLevel', MAP, flags='actor_only zero_arg'),
    #: 'isininterior' handled by special handler in _emit_function
    'getcurrentgametime': Cmd('Utility.GetCurrentGameTime', MAP, bare=True),
    'getcurrenttime': Cmd('Utility.GetCurrentGameTime', MAP, bare=True),

    #: --- Sound ---

    #: --- Animation ---
    'lookat': Cmd('SetLookAt', MAP, flags='actor_only'),

    #: --- Misc ---
    'getparentref': Cmd('GetLinkedRef', MAP, flags='objref_self zero_arg'),
    'lock': Cmd('Lock', MAP, flags='objref_self'),
    'getlocked': Cmd('IsLocked', MAP, flags='bare_bool cmp_bool objref_self zero_arg'),
    'getlocklevel': Cmd('GetLockLevel', MAP, flags='objref_self zero_arg'),
    'setownership': Cmd('SetActorOwner', MAP), # handled by special handler above
    'setscale': Cmd('SetScale', MAP, flags='actor_only objref_shared'),
    'getscale': Cmd('GetScale', MAP, flags='actor_only objref_shared zero_arg'),
    'say': Cmd('Say', MAP, flags='actor_only objref_shared'),
    'setfactionreaction': Cmd('SetReaction', MAP, bare=True),
    'modfactionreaction': Cmd('ModReaction', MAP, bare=True),
    'triggerscreenblood': Cmd('Game.TriggerScreenBlood', MAP, bare=True),
    'removeme': Cmd('Delete', MAP),

    #: --- Object state ---
    'setdisabled': Cmd('Disable', MAP),
    'setenabled': Cmd('Enable', MAP),
    'getis3dloaded': Cmd('Is3DLoaded', MAP, flags='bare_bool'),

    # --------------------------------------------------------------------------
    # Weather
    # --------------------------------------------------------------------------
    #: See: docs/commentary/script_convert.md#zero-arg-reads-need-function-map

    #: --- Special compound player.X ---
    'player.additem': Cmd('Game.GetPlayer().AddItem', MAP, bare=True),
    'player.removeitem': Cmd('Game.GetPlayer().RemoveItem', MAP, bare=True),
    'player.getitemcount': Cmd('Game.GetPlayer().GetItemCount', MAP, bare=True),
    'player.addspell': Cmd('Game.GetPlayer().AddSpell', MAP, bare=True),
    'player.removespell': Cmd('Game.GetPlayer().RemoveSpell', MAP, bare=True),
    'player.moveto': Cmd('Game.GetPlayer().MoveTo', MAP, bare=True),
    'player.placeatme': Cmd('Game.GetPlayer().PlaceAtMe', MAP, bare=True),

    #: --- Additional Actor/Combat ---
    'getcombattarget': Cmd('GetCombatTarget', MAP,
                           flags='actor_only zero_arg'),
    'getparentcellowner': Cmd('GetParentCell', MAP),
    'hasmagiceffect': Cmd('HasMagicEffect', MAP, flags='actor_only'),
    'setopendoor': Cmd('SetOpen', MAP),

    #: --- Player state ---
    'disableplayercontrols': Cmd('Game.DisablePlayerControls', MAP, bare=True),
    'enableplayercontrols': Cmd('Game.EnablePlayerControls', MAP, bare=True),
    'enablefasttravel': Cmd('Game.EnableFastTravel', MAP, bare=True),
    #: Worldspace dropped. See: docs/commentary/script_convert.md#argument-that-looks-ignorable

    #: --- AI/Package ---

    #: --- Object Interaction ---
    'removeallitems': Cmd('RemoveAllItems', MAP, flags='actor_only objref_shared'),
    #: Special handlers in `_emit_function` decide each of these.

    # --------------------------------------------------------------------------
    # Cell/Location
    # --------------------------------------------------------------------------

    #: --- Faction/Crime ---

    #: --- Dialog/Topic ---
    'saycustom': Cmd('Say', MAP, flags='actor_only objref_shared'),

    #: --- Look/Perception ---
    'look': Cmd('SetLookAt', MAP, flags='actor_only'),

    # --------------------------------------------------------------------------
    # Display/Name
    # --------------------------------------------------------------------------
    #: See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer

    #: --- Travel ---
    'movetomyeditorlocation': Cmd('MoveToMyEditorLocation', MAP),
    'moveto': Cmd('MoveTo', MAP, flags='objref_self'),
    'movetomarker': Cmd('MoveTo', MAP),

    #: --- Path/Linked Points ---

    #: --- Shader/Visual Effects ---

    #: --- AI/Wait ---
    'sayto': Cmd('Say', MAP, flags='actor_only objref_shared'),

    #: --- Detection ---

    #: --- Door/Object State ---
    'setopenstate': Cmd('SetOpen', MAP, flags='objref_self'),

    #: --- Player Skill/Misc ---
    'modpcskill': Cmd('Game.AdvanceSkill', MAP, bare=True, flags='av'),
    'modpcmiscstat': Cmd('Game.IncrementStat', MAP, bare=True),

    #: --- Trap/Custom functions that are quest-specific ---

    #: --- Gold ---
    'getgold': Cmd('GetGoldAmount', MAP, flags='actor_only zero_arg'),

    #: --- Alpha ---
    'saa': Cmd('SetAlpha', MAP, flags='actor_only actorbase_arg'),
    'setactoralpha': Cmd('SetAlpha', MAP, flags='actor_only actorbase_arg'),
    'gaa': Cmd('GetAlpha', MAP, flags='actor_only actorbase_arg'),
    'getactoralpha': Cmd('GetAlpha', MAP, flags='actor_only actorbase_arg'),

    # --------------------------------------------------------------------------
    # Interior
    # --------------------------------------------------------------------------

    #: --- Save ---
    'autosave': Cmd('Game.RequestAutoSave', MAP, bare=True),

    # --------------------------------------------------------------------------
    # Misc unmapped
    # --------------------------------------------------------------------------
    #: See: docs/commentary/script_convert.md#zero-arg-reads-need-function-map
    'getclothingvalue': Cmd(note='{f} {a}  (clothing value not tracked in Skyrim; 0)', flags='zero_arg'),
    'getshouldattack': Cmd(note='{f} {a}  (no Papyrus equivalent; 0 -- sibling IsInCombat term carries the check)'),
    'getopenstate': Cmd('GetOpenState', MAP, flags='zero_arg'),
    'isessential': Cmd('IsEssential', MAP, flags='actor_arg actor_only cmp_bool zero_arg'),
    'getlos': Cmd('HasLOS', MAP, flags='actor_only'),
    #: GetActorOwner answers it. See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
    'setlookat': Cmd('SetLookAt', MAP, flags='actor_only'),
    #: What the subject and the activating reference are called here.
    'getself': Cmd('{self_ref}'),
    'getactionref': Cmd('{action_ref}'),

    #: GetPCIsSex: Skyrim's ActorBase.GetSex() is 0 male / 1 female.
    'getpcissex': Cmd(
        'Game.GetPlayer().GetActorBase().GetSex() == {?0female}',
        arms=('1', '0'), defaults={0: 'male'}),

    #: GetIsSex on any actor, same encoding.
    'getissex': Cmd('({ref}.GetActorBase().GetSex() == {?0female})', ACTOR,
                    arms=('1', '0'), defaults={0: 'male'}, flags='cmp_bool'),

    #: See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'getlocalgravity': Cmd('{?0z}', arms=('-9.81', '0.0'), defaults={0: 'Z'}),

    #: See: docs/commentary/script_convert.md#argument-that-looks-ignorable
    'togglefirstperson': Cmd(
        '{?01}', arms=('Game.ForceFirstPerson()',
                       'Game.ForceThirdPerson()'), defaults={0: '0'}),

    #: See: docs/commentary/script_convert.md#tes4s-destroyed-flag-has-no
    'getdisabled': Cmd('TES4Polyfill.GetDisabled({ref}, {destroyed})', flags='objref_self zero_arg'),
    'isdisabled': Cmd('TES4Polyfill.GetDisabled({ref}, {destroyed})', flags='bare_bool objref_self'),
    'getdestroyed': Cmd('TES4Polyfill.GetDestroyed({ref}, {destroyed})', flags='bare_no_equiv zero_arg'),

    #: SetDestroyed writes that same shadow list.
    'setdestroyed': Cmd(
        'TES4Polyfill.SetDestroyed({ref}, {destroyed}, {b0})',
        defaults={0: '1'}),

    #: See: docs/commentary/script_convert.md#closing-oblivion-gate-destroyed-flag
    'closecurrentobliviongate': Cmd(
        'TES4Polyfill.CloseCurrentOblivionGate({destroyed})'),
    'forcecloseobliviongate': Cmd(
        'TES4Polyfill.CloseOblivionGate({ref}, {destroyed})'),
    'closeobliviongate': Cmd(
        'TES4Polyfill.CloseOblivionGate({ref}, {destroyed})'),

    #: See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'modamountsoldstolen': Cmd(
        'TES4GoldFenced.Mod({f0})', defaults={0: '1'},
        self_type=('TES4GoldFenced', 'GlobalVariable')),

    #: See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'ispcamurderer': Cmd(f'({CRIME_FACTION}.GetCrimeGoldViolent() >= 1000)',
                         self_type=CRIME_REALMS),
    'ispcanmurderer': Cmd(f'({CRIME_FACTION}.GetCrimeGoldViolent() >= 1000)',
                          self_type=CRIME_REALMS),
    'getpcismurderer': Cmd(f'({CRIME_FACTION}.GetCrimeGoldViolent() >= 1000)',
                           self_type=CRIME_REALMS),

    #: See: docs/commentary/script_convert.md#argument-that-looks-ignorable
    'setdoordefaultopen': Cmd('{ref}.SetOpen({b0})', defaults={0: '1'}),

    #: SetScale / SetSize.
    'setsize': Cmd('{ref}.SetScale({a0})', defaults={0: '1.0'}),

    #: GetPCMiscStat reads one of the game's own tracked statistics.
    'getpcmiscstat': Cmd('Game.QueryStat("{s0}")',
                         defaults={0: 'Items Stolen'}),

    #: Not promoted. See: docs/commentary/script_convert.md#commands-that-must-not-promote
    'getinsamecell': Cmd('({ref}.GetParentCell() == {a0}.GetParentCell())',
                         OBJREF, defaults={0: 'Game.GetPlayer()'},
                         flags='cmp_bool'),
    'getinsamecellas': Cmd('({ref}.GetParentCell() == {a0}.GetParentCell())',
                           OBJREF, defaults={0: 'Game.GetPlayer()'}),

    # --------------------------------------------------------------------------
    # No Papyrus equivalent
    # --------------------------------------------------------------------------
    #: Opener only. See: docs/commentary/script_convert.md#obse-constructs-no-shape-to-translate
    'foreach': Cmd(note='{f} - OBSE array/string command, no Papyrus '
                        'equivalent ({f} {a})'),
    'loop': Cmd(note='{f} - OBSE array/string command, no Papyrus '
                     'equivalent ({f} {a})'),
    'getmodindex': Cmd('1',
                       note='GetModIndex - Papyrus cannot read load order'),
    'unlockachievement': Cmd(
        note='UnlockAchievement {a}  ;no Papyrus equivalent', flags='bare_no_equiv'),

    # --------------------------------------------------------------------------
    # Migrated from `_emit_function`'s branch chain
    # --------------------------------------------------------------------------
    #: See: docs/commentary/script_convert.md#command-rows

    #: Statement position. See: docs/commentary/script_convert.md#neutralised-command-inert-in-position
    'skipanim': Cmd(note='SkipAnim  ;no Papyrus equivalent', flags='bare_no_equiv zero_arg'),
    'setnumericinisetting': Cmd(note='{f} {a}  ;no Papyrus INI access'),

    #: See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
    'update3d': Cmd('TES4Polyfill.Update3D({ref})', OBJREF),

    #: Argument becomes the receiver. See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
    'uncompletequest': Cmd('{a0}.Reset()', RAW, defaults={0: 'Self'}),

    #: Not promoted. See: docs/commentary/script_convert.md#commands-that-must-not-promote
    'getpos': Cmd('{ref}.GetPosition{g0}()', OBJREF, defaults={0: 'X'}, flags='objref_self'),
    'getangle': Cmd('{ref}.GetAngle{g0}()', OBJREF, defaults={0: 'X'}, flags='objref_self'),
    'getstartingangle': Cmd('{ref}.GetAngle{g0}()', OBJREF,
                            defaults={0: 'X'}),

    #: Register the EMITTED name. See: docs/commentary/script_convert.md#argument-that-looks-ignorable
    'playsound': Cmd('{p0}.Play(Game.GetPlayer())', types={0: 'Sound'}),
    'playsound3d': Cmd('{p0}.Play({ref})', OBJREF, types={0: 'Sound'}),

    #: See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'getpcfactionsteal': Cmd(
        '({a0}.GetCrimeGoldNonViolent() > 0) as Int',
        defaults={0: 'None'}, types={0: 'Faction'}),
    'getpcfactionmurder': Cmd(
        '({a0}.GetCrimeGoldViolent() >= 1000) as Int',
        defaults={0: 'None'}, types={0: 'Faction'}),
    'getpcfactionattack': Cmd(
        '({a0}.GetCrimeGoldViolent() > 0 && '
        '{a0}.GetCrimeGoldViolent() < 1000) as Int',
        defaults={0: 'None'}, types={0: 'Faction'}),

    #: See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'getnextref': Cmd(
        'Game.FindRandomActorFromRef(Game.GetPlayer(), 4096.0)'),

    #: ShowMap: the marker is the subject; bare it maps Self.
    'showmap': Cmd('{p0}.AddToMap(true)', types={0: 'ObjectReference'},
                   defaults={0: 'Self'}),

    #: See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'setforcerun': Cmd('{ref}.SetActorValue("SpeedMult", {?01})', ACTOR,
                       defaults={0: '0'}, arms=('150.0', '100.0'), flags='actor_only branch_only'),

    #: IsPCRace / GetPCIsRace -> the player's race compared to the argument.
    'ispcrace': Cmd('Game.GetPlayer().GetRace() == {a0}',
                    defaults={0: 'None'}, types={0: 'Race'}),
    'getpcisrace': Cmd('Game.GetPlayer().GetRace() == {a0}',
                       defaults={0: 'None'}, types={0: 'Race'}, flags='cmp_bool'),

    #: Expel -> faction.SetPlayerExpelled(true).
    'expel': Cmd('{a0}.SetPlayerExpelled(true)', defaults={0: 'None'},
                 types={0: 'Faction'}),

    #: GetIsRace: ref.GetIsRace RaceRef -> ref.GetRace() == raceRef.
    'getisrace': Cmd('{ref}.GetRace() == {a0}', ACTOR,
                     defaults={0: 'None'}, types={0: 'Race'}, flags='cmp_bool'),

    #: See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer
    'getinworldspace': Cmd('{ref}.GetWorldSpace() == {a0}', RAW,
                           defaults={0: 'None', 'ref': 'Game.GetPlayer()'},
                           types={0: 'WorldSpace'}),

    #: See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
    'getdetected': Cmd('{a0}.IsDetectedBy({ref})', ACTOR,
                       defaults={0: 'Game.GetPlayer()'},
                       types={0: 'Actor'}, flags='actor_arg cmp_bool'),

    #: Swapped and RESCALED. See: docs/commentary/script_convert.md#receiver-and-argument-not-interchangeable
    'getdetectionlevel': Cmd('(({a0}.IsDetectedBy({ref}) as Int) * 3)', ACTOR,
                             defaults={0: 'Game.GetPlayer()'},
                             types={0: 'Actor'}, flags='actor_arg'),

    #: See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'flee': Cmd('{ref}.SetActorValue("Confidence", 0)\n'
                '  {ref}.EvaluatePackage()', ACTOR, flags='bare_no_equiv'),
    'forceflee': Cmd('{ref}.SetActorValue("Confidence", 0)\n'
                     '  {ref}.EvaluatePackage()', ACTOR),

    #: Answers PRESENT. See: docs/commentary/script_convert.md#neutralised-command-inert-in-position
    'fileexists': Cmd('1', note='FileExists - converted assets are deployed '
                                'by the pipeline, not under the TES4 path'),

    #: See: docs/commentary/script_convert.md#argument-that-looks-ignorable
    'setcanfasttravelfromworld': Cmd(
        'Game.EnableFastTravel({b1})', defaults={1: '1'},
        note='Skyrim fast travel is global, not per-worldspace'),

    #: PickIdle / PlayIdle -> a behavior-graph event on the actor.
    'pickidle': Cmd('Debug.SendAnimationEvent({ref}, "{s0}")', ACTOR,
                    defaults={0: 'IdleForceDefaultState'}, flags='zero_arg'),
    'playidle': Cmd('Debug.SendAnimationEvent({ref}, "{s0}")', ACTOR,
                    defaults={0: 'IdleForceDefaultState'}),

    #: Kept as ONE family. See: docs/commentary/script_convert.md#obse-constructs-no-shape-to-translate
    'disablekey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'enablekey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'tapkey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'holdkey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'releasekey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'playback': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'playbackalt': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'disablecontrol': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'enablecontrol': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'tapcontrol': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),

    #: GMST substitute. See: docs/commentary/script_convert.md#equivalent-in-a-different-subsystem
    'resetfalldamagetimer': Cmd(
        'TES4Polyfill.SuppressFallDamage({event_actor})', flags='zero_arg'),

}

COMMAND_ROWS.update(
    (name, Cmd(**dict(spec, subj=globals()[spec['subj']])))
    if isinstance(spec.get('subj'), str) else (name, Cmd(**spec))
    for name, spec in FALLOUT_COMMAND_ROWS.items())


#: Names only a handler converts, so the parser reads them AS commands.
HANDLED_COMMANDS = frozenset((
    'addtopic', 'closecurrentobliviongate', 'closeobliviongate',
    'con_runmemorypass', 'disablecontrol', 'disablekey', 'emcgetplaylist',
    'emcisbattleoverridden', 'emcismusiconhold', 'emcmusicnexttrack',
    'emcmusicresume', 'emcmusicstop', 'emcplaytrack', 'emcsetbattleoverride',
    'emcsetmusichold', 'emcsetmusictype', 'enablecontrol', 'enablekey',
    'expel', 'fileexists', 'flee', 'forceflee', 'getactionref',
    'getbuttonpressed', 'getdestroyed', 'getdetected', 'getdetectionlevel',
    'getfirstref', 'getgameloaded', 'getglobalvalue', 'getincell',
    'getincell', 'getinsamecell', 'getinsamecellas', 'getinworldspace',
    'getisclass', 'getiscurrentpackage', 'getisid', 'getisrace', 'getissex',
    'getlocalgravity', 'getmenufloatvalue', 'getmenuhastrait',
    'getmenustringvalue', 'getmodindex', 'getnextref', 'getnthspell',
    'getownership', 'getpcfactionattack', 'getpcfactionattack',
    'getpcfactionmurder', 'getpcfactionsteal', 'getpcfactionsteal',
    'getpcisclass', 'getpcismurderer', 'getpcisrace', 'getpcissex',
    'getsecondspassed', 'getself', 'getspellcount',
    'holdkey', 'isactionref', 'isactivator', 'isactor', 'isarmor', 'isbook',
    'isclothing', 'iscontainer', 'isdoor', 'isingredient', 'iskey', 'islight',
    'ismisc', 'isowner', 'ispcamurderer', 'ispcanmurderer', 'ispcrace',
    'isplayersleeping', 'ispotion', 'israining', 'isspelltarget', 'isweapon',
    'lookismile', 'modamountsoldstolen', 'moddisposition', 'pathtoref',
    'pickidle', 'playback', 'playbackalt', 'playgroup', 'playidle',
    'playmagiceffectvisuals', 'playsound', 'playsound3d', 'pme',
    'positionworld', 'pushactoraway', 'releasekey', 'resetfalldamagetimer',
    'resetinterior', 'setcanfasttravelfromworld', 'setdestroyed',
    'setdoordefaultopen', 'setessential', 'setforcewalk', 'setglobalvalue',
    'setnumericinisetting', 'setpcfactionattack', 'setpcfactionmurder',
    'setpcfactionsteal', 'showbirthsignmenu', 'showmap', 'skipanim', 'sme',
    'startconversation', 'stopmagiceffectvisuals', 'streammusic',
    'sv_construct', 'tapcontrol', 'tapkey', 'togglefirstperson',
    'uncompletequest', 'unlockachievement', 'update3d',
))


#: Rows plus handler-only names. See: docs/commentary/script_convert.md#command-rows
KNOWN_COMMANDS = (frozenset(COMMAND_ROWS) | HANDLED_COMMANDS
                  | FALLOUT_HANDLED_COMMANDS | frozenset(FALLOUT_COMMAND_ALIASES))


#: See: docs/commentary/script_convert.md#papyrus-value-types
PAPYRUS_VALUE_TYPES = frozenset({'Int', 'Float', 'Bool', 'String',
                                  'GlobalVariable'})


#: `ACTOR_VALUE_MAP` keyed lowercase: the tree hands over one name to look up.
ACTOR_VALUE_MAP_LOW = {k.lower(): v for k, v in ACTOR_VALUE_MAP.items()}


#: See: docs/commentary/script_convert.md#command-prefix-families
COMMAND_PREFIXES = (
    #: See: docs/commentary/script_convert.md#obse-constructs-no-shape-to-translate
    ('ar_', Cmd(note='{f} - OBSE array/string command, no Papyrus '
                     'equivalent ({f} {a})')),
    ('sv_', Cmd(note='{f} - OBSE array/string command, no Papyrus '
                     'equivalent ({f} {a})')),
    #: Papyrus cannot execute the console at all.
    ('con_', Cmd(note='{f} {a}  ;OBSE console command, no Papyrus '
                      'equivalent')),
    #: Skyrim's UI is Scaleform and exposes none of this.
    ('getmenu', Cmd(note='{f} {a}  ;OBSE menu query, no Papyrus equivalent')),
    ('setmenu', Cmd(note='{f} {a}  ;OBSE menu query, no Papyrus equivalent')),
    #: Elys Music Control. See: docs/commentary/script_convert.md#obse-constructs-no-shape-to-translate
    ('emcm', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
    ('emcs', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
    ('emcg', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
    ('emci', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
    ('emcp', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
)


def command_prefix_row(name: str):
    """The `COMMAND_PREFIXES` row for `name`, or None.

    Longest prefix first, so a specific family beats a broader one.
    """
    low = name.lower()
    best = None
    for prefix, row in COMMAND_PREFIXES:
        if low.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), row)
    return best[1] if best else None



#: Compound `ref.func` names whose bare form has its own handler.
COMPOUND_HAS_OWN_HANDLER = ('placeatme', 'moveto', 'movetomarker')


#: TES4 commands that imply `player` when bare; Papyrus requires it written.
DEFAULT_ARGS = {
    'startconversation': 'Game.GetPlayer()',
    'sayto': 'Game.GetPlayer()',
    'getrandompercent': '0, 99',
    'isactordetected': 'Game.GetPlayer()',
    'getdetected': 'Game.GetPlayer()',
    'isdetectedby': 'Game.GetPlayer()',
    'setownership': 'Game.GetPlayer().GetActorBase()',
    'setactorowner': 'Game.GetPlayer().GetActorBase()',
}

#: Events on the engine's dispatch path, where a blocking Say stalls it.
DISPATCH_EVENTS = ('onpackagestart', 'onpackageend', 'onpackagechange',
                   'onhit', 'oncombatstatechanged', 'onactivate',
                   'ondeath', 'ondying', 'onload', 'oncellattach',
                   'onlocationchange')

#: Tokens a Say command needs before its topic can be a speak-as target.
SAY_SPEAKAS_MIN_TOKENS = {'say': 3, 'saycustom': 3, 'sayto': 4}

#: GMSTs Skyrim exposes only as an actor value.
GMST_TO_ACTOR_VALUE = {
    'fjumpheightmin': 'JumpingBonus',
    'fjumpheightmax': 'JumpingBonus',
    'fmoverunmult': 'SpeedMult',
    'fmovecharwalkmin': 'SpeedMult',
    'fmovecharwalkmax': 'SpeedMult',
    'fmoverunathleticsmult': 'SpeedMult',
}


#: Actor values TES5 stores as an enum tier, with the tier count.
ENUM_ACTOR_VALUES = {
    'aggression': 3, 'confidence': 4, 'assistance': 2,
    'mood': 8, 'morality': 3,
}

#: See: docs/commentary/script_convert.md#aggression-confidence-are-enums
ENUM_AV_LADDERS = {
    'aggression': ((106, 3), (65, 2), (5.000001, 1), (0, 0)),
    'confidence': ((100, 4), (70, 3), (40, 2), (15, 1), (0, 0)),
}




def _flagged(flag):
    """Every command whose COMMAND_ROWS row carries `flag`; rowless ones cannot."""
    return frozenset(name for name, row in COMMAND_ROWS.items()
                     if flag in row.flags)


#: Commands whose Papyrus equivalent takes fewer arguments; drop the extras.
DROP_ARGS_FUNCS = _flagged('drop_args')

#: Declared on ActorBase. See: docs/commentary/script_convert.md#commands-that-must-not-promote
ACTORBASE_ARG_FUNCTIONS = _flagged('actorbase_arg') | frozenset({
    'getdeadcount', 'setessential',
})

#: Actor PARAMETER, so the call site casts. See: docs/commentary/script_convert.md#commands-that-must-not-promote
ACTOR_ARG_FUNCTIONS = _flagged('actor_arg') | frozenset({
    'getrelationshiprank', 'isdetectedby', 'ishostiletoactor',
    'isspelltarget', 'setrelationshiprank',
})

#: Actor-only, so a caller's property type is inferred from them.
ACTOR_ONLY_FUNCTIONS = _flagged('actor_only') | frozenset({
    'drawweapon', 'getalpha', 'getclass', 'getdeadcount',
    'getgoldamount', 'getincombat', 'getsitstate', 'getsleepstate',
    'getweapondrawn', 'haslos', 'isequipped', 'isinfaction',
    'pathtoref', 'setalpha', 'setcell', 'setessential',
    'setopacity', 'setplayerteammate', 'setrace',
    'setrelationshiprank', 'sheatheweapon', 'startconversation',
})

#: See: docs/commentary/script_convert.md#skyrim-has-no-attributes
ACTOR_VALUE_FUNCTIONS = _flagged('av') | frozenset({
    'advancepcskill',
})

#: The readers must yield a value; the writers are dropped instead.
ACTOR_VALUE_READ_FUNCTIONS = _flagged('av_read')

#: Boolean (0/1) and usable as a bare check.
BARE_BOOL_FUNCTIONS = _flagged('bare_bool') | frozenset({
    'is3dloaded',
})

BARE_NO_EQUIV_COMMANDS = _flagged('bare_no_equiv') | frozenset({
    'con_runmemorypass', 'emcgetplaylist', 'emcisbattleoverridden',
    'emcismusiconhold', 'emcmusicnexttrack', 'emcmusicresume',
    'emcmusicstop', 'emcplaytrack', 'emcsetbattleoverride',
    'emcsetmusichold', 'emcsetmusictype', 'getmenufloatvalue',
    'getmenuhastrait', 'getmenustringvalue', 'streammusic',
})

#: See: docs/commentary/script_convert.md#13-twelve-commands-were-treated
BRANCH_ONLY_COMMANDS = _flagged('branch_only') | frozenset({
    'setgamesetting', 'setnumericgamesetting',
    'setnumericgamesettingfloat',
})

#: See: docs/commentary/script_convert.md#6-two-divergent-boolean-function
COMPARISON_BOOL_FUNCTIONS = _flagged('cmp_bool') | frozenset({
    'getincell', 'getisclass', 'getiscurrentpackage', 'getisid',
    'getpcisclass', 'gettalkedtopcparam', 'isactionref',
    'isinfaction', 'isowner',
})

OBJREF_IMPLICIT_SELF_FUNCTIONS = _flagged('objref_self') | frozenset({
    'getbaseobject', 'is3dloaded', 'isdeleted', 'playanimation',
    'playgroup', 'setactorowner', 'setangle', 'setpos',
})

#: On ObjectReference, so they must NOT promote. See: docs/commentary/script_convert.md#commands-that-must-not-promote
OBJREF_SHARED_FUNCTIONS = _flagged('objref_shared') | frozenset({
    'getalpha', 'setalpha', 'setcell',
})

#: Comma-written receivers. See: docs/commentary/script_convert.md#commands-that-must-not-promote
ZERO_ARG_REF_FUNCTIONS = _flagged('zero_arg') | frozenset({'isactor'})

#: What `emit/expr.py` reads: every name either list above calls boolean.
BOOL_VALUED_FUNCTIONS = BARE_BOOL_FUNCTIONS | COMPARISON_BOOL_FUNCTIONS
