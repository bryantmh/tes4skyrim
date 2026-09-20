"""
Who speaks a Morrowind bark, read off the records rather than guessed.

A TES3 INFO states its speaker outright: `ONAM` names one actor, `RNAM`/`CNAM`
/`FNAM` name a race, class and faction, and `DATA` carries the rank and the
gender. `MWDialogue::Filter` tests exactly those fields and nothing else, so
the audience is authored data and never needs inferring -- not from the folder
a recording sits in, which says nothing: `Vo\\ord\\` holds Ordinator lines whose
records say `class=guard, faction=temple`, and `Vo\\v\\` holds vampire grunts
whose records NAME each vampire.

The identity half of that filter is ported here, against the actors the export
can see -- this plugin's own and every converted master's. What comes back is
the real speaker set, which decides the gate:

  * a vanilla race the importer can voice -> its VTYP, which is plugin-owned
    and so cannot bleed onto another plugin's actors;
  * anything else -- a custom race, a faction, a class -- -> `GetIsID` over the
    speakers themselves, measured at most 9 for a custom race and 14 for the
    largest such group, so the chain always fits the engine's limit.

Script locals (`T_Local_Vampire`, `T_Local_Khajiit`) are deliberately NOT
identity: the scripts declaring them set them at runtime, and 1,553 TR scripts
across 21 races declare `T_Local_NPC`. They are conditions, and travel as
conditions.

See: docs/commentary/tes4_export_morrowind.md#voiced-barks
"""

import os
import struct

from ..tes3_reader import Tes3Record, get_string, get_subrecord

#: INFO/NPC DATA is `<i i b b b b`: type, disposition, rank, gender, pcrank.
_INFO_DATA = struct.Struct('<iibbbb')

#: The 52-byte NPDT (authored stats) and the 12-byte one (autocalc).
_NPDT_FULL = struct.Struct('<h8B27BxHHHBBBxi')
_NPDT_AUTO = struct.Struct('<hBBB3xi')

#: Where the faction rank sits in each NPDT layout.
_RANK_IN_FULL = 41
_RANK_IN_AUTO = 3

#: FNAM holding this means "only when the speaker has NO faction".
_FACTIONLESS = 'ffff'

#: The NPC FLAG bit that marks a female actor.
_FLAG_FEMALE = 0x01

#: DATA gender/rank meaning "unstated"; the filter skips the test.
_UNSET = -1

#: Export-dump delimiter, and the identity keys a master's actor carries.
_RECORD_END = '---RECORD_END---'
_MASTER_KEYS = frozenset((
    'EditorID', 'Signature', 'ACBS.Flags', 'MorrowindRace', 'MorrowindClass',
    'MorrowindFaction', 'MorrowindRank',
    'RNAM.Race', 'CNAM.Class', 'Faction[0].FormID', 'Faction[0].Rank'))


def _text(rec: Tes3Record, sig: str) -> str:
    """One string subrecord, lowercased, or '' when absent."""
    sub = get_subrecord(rec, sig)
    return get_string(sub).lower() if sub is not None else ''


def _actor_rank(rec: Tes3Record) -> int:
    """The faction rank an actor holds, from whichever NPDT it carries."""
    npdt = get_subrecord(rec, 'NPDT')
    if npdt is None:
        return _UNSET
    if len(npdt.data) >= _NPDT_FULL.size:
        return _NPDT_FULL.unpack_from(npdt.data, 0)[_RANK_IN_FULL]
    if len(npdt.data) >= _NPDT_AUTO.size:
        return _NPDT_AUTO.unpack_from(npdt.data, 0)[_RANK_IN_AUTO]
    return _UNSET


class Speaker:
    """One actor's identity, as the filter asks for it.

    A converted TES4 master states race, class and faction as FormIDs rather
    than TES3 names, so those ride alongside and the filter compares whichever
    pair it has.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """

    __slots__ = ('record_id', 'is_npc', 'race', 'clazz', 'faction', 'rank',
                 'female', 'fids')

    def __init__(self, rec: Tes3Record):
        """Read every field `MWDialogue::Filter` tests off one actor."""
        flags = get_subrecord(rec, 'FLAG')
        self.record_id = rec.record_id
        self.is_npc = rec.type == 'NPC_'
        self.race = _text(rec, 'RNAM')
        self.clazz = _text(rec, 'CNAM')
        self.faction = _text(rec, 'ANAM')
        self.rank = _actor_rank(rec)
        self.female = bool(flags.data[0] & _FLAG_FEMALE
                           if flags is not None and flags.data else False)
        self.fids = ()


class Audience:
    """A bark's stated speaker: the filter fields, and who satisfies them.

    `signature` is every field the identity test reads, so two barks filtering
    alike resolve once -- 6,264 vanilla barks collapse to 333 signatures.
    """

    __slots__ = ('actor', 'race', 'clazz', 'faction', 'factionless', 'rank',
                 'gender', 'fids')

    def __init__(self, rec: Tes3Record):
        """Read the identity filters one INFO states."""
        faction = _text(rec, 'FNAM')
        data = get_subrecord(rec, 'DATA')
        rank, gender = _UNSET, _UNSET
        if data is not None and len(data.data) >= _INFO_DATA.size:
            rank, gender = _INFO_DATA.unpack_from(data.data, 0)[2:4]
        self.actor = _text(rec, 'ONAM')
        self.race = _text(rec, 'RNAM')
        self.clazz = _text(rec, 'CNAM')
        self.factionless = faction == _FACTIONLESS
        self.faction = '' if self.factionless else faction
        self.rank = rank
        self.gender = gender
        self.fids = ()

    def bind(self, ctx, race_formids: dict) -> 'Audience':
        """Resolve this bark's race, class and faction into the FormID space.

        Race comes from the same table the NPC export races an actor by;
        Morrowind exports no RACE record, so the id index cannot supply one.
        See: docs/commentary/tes4_export_morrowind.md#voiced-barks
        """
        self.fids = (race_formids.get(self.race, 0),
                     _hex(ctx.resolve(self.clazz, 'CLAS')),
                     _hex(ctx.resolve(self.faction, 'FACT')))
        return self

    @property
    def signature(self) -> tuple:
        """Everything the identity test reads, for memoising the speaker set."""
        return (self.actor, self.race, self.clazz, self.faction,
                self.factionless, self.rank, self.gender, self.fids)

    @property
    def states_identity(self) -> bool:
        """Whether the record names a speaker at all; an open bark states none.

        See: docs/commentary/tes4_export_morrowind.md#voiced-barks
        """
        return bool(self.actor or self.race or self.clazz or self.faction
                    or self.factionless)

    def _named_as(self, who: Speaker) -> 'tuple | None':
        """This bark's (race, class, faction) as this speaker states identity.

        None when a stated name has no counterpart in the speaker's FormID
        space -- a custom race no converted master has -- because an unresolved
        name is not a match against everyone, it is a match against nobody.
        See: docs/commentary/tes4_export_morrowind.md#voiced-barks
        """
        if not who.fids:
            return self.race, self.clazz, self.faction
        for name, fid in zip((self.race, self.clazz, self.faction), self.fids):
            if name and not fid:
                return None
        return self.fids

    def accepts(self, who: Speaker) -> bool:
        """TES3's own identity test, in `filter.cpp TestInfo` order.

        A creature answers only a topic naming it directly, and the gender
        test is for the OPPOSITE gender, both as the runtime implements them.
        """
        if self.actor:
            return self.actor == who.record_id.lower()
        if not who.is_npc:
            return False
        stated = self._named_as(who)
        if stated is None:
            return False
        race, clazz, faction = stated
        mine = who.fids or (who.race, who.clazz, who.faction)
        if race and race != mine[0]:
            return False
        if clazz and clazz != mine[1]:
            return False
        if self.factionless:
            if mine[2]:
                return False
        elif faction:
            if faction != mine[2] or who.rank < self.rank:
                return False
        elif self.rank != _UNSET and who.rank < self.rank:
            return False
        return self.gender != (0 if who.female else 1)


def index_speakers(records: list) -> list:
    """Every actor this plugin defines, as `Speaker` rows.

    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    return [Speaker(rec) for rec in records
            if rec.type in ('NPC_', 'CREA') and not rec.deleted]


def _hex(value: str) -> int:
    """One exported FormID as an int, or 0 when it is absent or malformed."""
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        return 0


def _speaker_from_fields(fields: dict) -> 'Speaker | None':
    """One master actor as a `Speaker`, or None when it carries no identity.

    A Morrowind-exported master states the TES3 names; a TES4 one (Morroblivion)
    states FormIDs, which `Audience.accepts` resolves against.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    if not fields.get('EditorID'):
        return None
    try:
        flags = int(fields.get('ACBS.Flags', '0'))
    except ValueError:
        return None
    who = Speaker.__new__(Speaker)
    who.record_id = fields['EditorID']
    who.is_npc = fields.get('Signature') == 'NPC_'
    who.race = fields.get('MorrowindRace', '').lower()
    who.clazz = fields.get('MorrowindClass', '').lower()
    who.faction = fields.get('MorrowindFaction', '').lower()
    who.female = bool(flags & _FLAG_FEMALE)
    who.fids = (_hex(fields.get('RNAM.Race')),
                _hex(fields.get('CNAM.Class')),
                _hex(fields.get('Faction[0].FormID')))
    try:
        who.rank = int(fields.get('MorrowindRank')
                       or fields.get('Faction[0].Rank') or _UNSET)
    except ValueError:
        who.rank = _UNSET
    return who if (who.race or any(who.fids)) else None


def _read_speaker_dump(path: str, out: list) -> None:
    """Append every actor one export dump describes."""
    fields = {}
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.startswith(_RECORD_END):
                who = _speaker_from_fields(fields)
                if who is not None:
                    out.append(who)
                fields = {}
                continue
            key, _sep, value = line.rstrip('\n').partition('=')
            if key in _MASTER_KEYS:
                fields[key] = value


def master_speakers(master_dirs) -> list:
    """Every actor the converted masters supply, as `Speaker` rows.

    A dependent plugin's barks are spoken mostly by its masters' actors, so an
    audience resolved against its own records alone would be almost empty.
    Takes `ctx.master_dirs`: `(export folder, FormID remap)` per master.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    out = []
    for folder, _remap in master_dirs or ():
        for sig in ('NPC_', 'CREA'):
            path = os.path.join(folder, f'{sig}.txt')
            if os.path.isfile(path):
                _read_speaker_dump(path, out)
    return out


def resolve(audience: Audience, speakers: list, cache: dict) -> list:
    """The actors that satisfy one bark's filters, memoised by signature."""
    key = audience.signature
    hit = cache.get(key)
    if hit is None:
        hit = [who for who in speakers if audience.accepts(who)]
        cache[key] = hit
    return hit
