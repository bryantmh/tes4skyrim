"""Oblivion creature -> equivalent VANILLA Skyrim creature. REFERENCE DATA.

*** NOTHING IN THE PIPELINE IMPORTS THIS MODULE YET. ***

This is the lookup table for a future converter option that would use a vanilla
Skyrim creature in place of the converted Oblivion one wherever Skyrim already
ships the same creature — trading "looks exactly like the original" for
vanilla-quality animation, ragdoll, sound and combat AI. The table and the
reasoning behind each row are the deliverable; wiring it into convert.py is a
separate, deliberate step that has NOT been taken.

The default path remains `creature_races.py`, which generates a full race chain
per Oblivion creature from its own converted mesh + behavior project.

WHAT A SWAP WOULD HAVE TO REPLACE
---------------------------------
Swapping the RACE alone is not enough and is the trap here: a vanilla race
points its ANAM/NAM3 at a vanilla skeleton + behavior project, so the actor also
needs the vanilla *skin* (WNAM ARMO) or it renders as the Oblivion mesh on a
Skyrim skeleton — bone names will not match and the actor explodes or T-poses.
Each entry therefore carries the race AND its vanilla skin ARMO.

The creature's own STATS should be kept (health, level, damage, factions,
inventory, AI packages, scripts) — only the visual/animation shell swapped, so a
level-40 Oblivion boss stays a level-40 boss wearing a vanilla body.

MATCH QUALITY
-------------
'exact' entries are the same creature in both games. 'near' entries are the same
archetype but a visibly different species (mountain lion vs sabre cat) and
should require an explicit opt-in. Everything absent from this table has no
honest Skyrim equivalent and must be generated — see
docs/reference/creature_race_equivalence.md for the full reasoning, including why the
keyword fallbacks in skyrim_overrides.CREA_RACE_PATTERNS are not equivalences.

Inspect coverage with `python tools/creature/creature_swap_report.py -f <plugin>`.

All FormIDs verified against references/Skyrim.esm/RACE.txt and ARMO.txt.
The keyword fallback keeps constants of its own (base/equivalents.py); this
table does not use them. tests/test_creature_race_fallback.py holds the two
tables to one FormID per race.
"""

EXACT = 'exact'
NEAR = 'near'


class Swap:
    """One vanilla stand-in: the race, its skin, and how good the match is."""

    __slots__ = ('race', 'skin', 'quality', 'skyrim_name', 'note')

    def __init__(self, race, skin, quality, skyrim_name, note=''):
        self.race = race
        self.skin = skin
        self.quality = quality
        self.skyrim_name = skyrim_name
        self.note = note

    def __repr__(self):
        return f'<Swap {self.skyrim_name} 0x{self.race:08X} {self.quality}>'


# ---------------------------------------------------------------------------
# Folder-level swaps: every CREA whose mesh lives in this folder maps here.
# Keyed by the same folder token creature_races._folder_of computes.
# ---------------------------------------------------------------------------

BY_FOLDER = {
    # ---- exact: the same creature in both games ----
    'mudcrab':       Swap(0x000BA545, 0x000BA546, EXACT, 'MudcrabRace'),
    'slaughterfish': Swap(0x00013203, 0x0004124A, EXACT, 'SlaughterfishRace'),
    'spriggan':      Swap(0x00013204, 0x00092E29, EXACT, 'SprigganRace'),
    'horse':         Swap(0x000131FD, 0x00060715, EXACT, 'HorseRace',
                          'see horse_rideability_plan.md — vanilla horse is '
                          'already rideable, which the converted one is not'),
    'flameatronach': Swap(0x000131F5, 0x0008691B, EXACT, 'AtronachFlameRace'),
    'frostatronach': Swap(0x000131F6, 0x0005B2E7, EXACT, 'AtronachFrostRace'),
    'stormatronach': Swap(0x000131F7, 0x0006881E, EXACT, 'AtronachStormRace'),
    'skeleton':      Swap(0x000B7998, 0x000B799A, EXACT, 'SkeletonRace',
                          'weapon-using; VNAM already permits blades/bows'),
    'bear':          Swap(0x000131E7, 0x000868FD, EXACT, 'BearBrownRace',
                          'per-CREA refinement in BY_EDITORID picks '
                          'black/brown'),
    'willothewisp':  Swap(0x00013208, 0x00042528, EXACT, 'WispRace'),
    'troll':         Swap(0x00013205, 0x00016EE4, EXACT, 'TrollRace',
                          'same creature and same role in both games — the '
                          'cave-dwelling brute troll. Models differ in detail '
                          '(Skyrim\'s is a three-eyed ape) but it is a troll '
                          'standing in for a troll, not a substitute species'),

    # ---- near: same archetype, visibly different species ----
    'rat':           Swap(0x00013201, 0x00016EE5, NEAR, 'SkeeverRace',
                          'skeever is Skyrim\'s rat — same role, bigger'),
    'mountainlion':  Swap(0x00013200, 0x00016EE6, NEAR, 'SabreCatRace',
                          'same big-cat rig and pounce; sabre tusks differ'),
    'sheep':         Swap(0x000131FA, 0x0006F278, NEAR, 'GoatRace',
                          'Skyrim has no sheep; goat is the livestock match'),
    'zombie':        Swap(0x00000D53, 0x00016EE3, NEAR, 'DraugrRace',
                          'draugr are armed/armoured; zombies are unarmed'),
    'ogre':          Swap(0x000131F9, 0x00048D94, NEAR, 'GiantRace',
                          'giants are much larger'),
    'deer':          Swap(0x000CF89B, 0x000CF89C, NEAR, 'DeerRace',
                          'skin is SkinReinDeer — the vanilla names are '
                          'crossed: ElkRace uses SkinDeer. Verified, not a '
                          'typo. ElkRace is closer for the antlered Buck; '
                          'see BY_EDITORID'),
}


# ---------------------------------------------------------------------------
# Per-creature overrides, matched on EditorID substring (lowercased).
#
# Needed where ONE Oblivion folder holds several distinct creatures that Skyrim
# splits into separate races. The canonical case is `dog`, which in Oblivion
# holds dog, wolf, timber wolf AND the SI skinned/skeletal hounds — Skyrim has a
# separate DogRace and WolfRace, and nothing for a skinned hound.
#
# Checked BEFORE BY_FOLDER. First match wins, so order matters: longer/more
# specific keys first.
# ---------------------------------------------------------------------------

BY_EDITORID = [
    # ---- Blockers. These MUST precede the generic keys below, because they
    # exist to stop a broader key from claiming a creature it does not fit.
    # A None means "always generate", even though the folder (or a looser
    # keyword) has a swap.
    ('skeletalhound',  None),   # SI-only, no equivalent → always generate
    ('skinnedhound',   None),
    ('skinned hound',  None),
    ('deadhound',      None),
    # Undead canines: Morrowind_ob's `undeadwolf` folder holds bone/skeletal
    # wolves that the plain 'wolf' key below would swap to a LIVING WolfRace —
    # a furred wolf standing in for a skeleton. Skyrim has no undead wolf
    # (C06WolfSpiritRace is a quest-specific spirit), so these generate.
    ('wolfskeleton',   None),
    ('skeletonwolf',   None),
    ('bonewolf',       None),
    ('wolfbone',       None),
    ('undeadwolf',     None),
    # Likewise a 'boneghost' is not a wisp shade.
    ('boneghost',      None),

    # `dog` folder — Skyrim splits dog from wolf
    ('wolf',  Swap(0x0001320A, 0x0004E886, EXACT, 'WolfRace')),
    ('dog',   Swap(0x000131EE, 0x0004B2C9, EXACT, 'DogRace')),
    # German (Nehrim). Its creature EditorIDs are German, so the English keys
    # above miss every one of them — 01Hund, EmmaHund and the Fuchs foxes all
    # fell through to a generated race despite Skyrim having exact matches.
    # 'wolf' is spelled the same in German and is already caught above.
    ('hund',   Swap(0x000131EE, 0x0004B2C9, EXACT, 'DogRace')),   # dog
    # FoxRace's WNAM really is SkinWolf — Skyrim's fox is a reskinned wolf
    # race sharing the wolf's skin ARMO. Verified in the dump; not a typo.
    ('fuchs',  Swap(0x00109C7C, 0x0004E886, EXACT, 'FoxRace')),   # fox
    # Nehrim's hare (01Hase01) is authored on the RAT mesh, so the folder rule
    # would make it a Skeever. The name is the authored intent and Skyrim has a
    # real hare, so the EditorID wins here — this is exactly why BY_EDITORID is
    # checked before BY_FOLDER.
    ('hase',   Swap(0x0006DC99, 0x0006DC9B, EXACT, 'HareRace')),  # hare
    ('huhn',   Swap(0x000A919D, 0x000A919C, EXACT, 'ChickenRace')),
    ('hahn',   Swap(0x000A919D, 0x000A919C, EXACT, 'ChickenRace')),  # rooster
    ('kuh',    Swap(0x0004E785, 0x0004E784, EXACT, 'CowRace')),
    ('schaf',  Swap(0x000131FA, 0x0006F278, NEAR, 'GoatRace')),   # sheep
    ('ziege',  Swap(0x000131FA, 0x0006F278, EXACT, 'GoatRace')),  # goat
    ('pferd',  Swap(0x000131FD, 0x00060715, EXACT, 'HorseRace')), # horse

    # `bear` folder — Skyrim has color-matched bears. BearBlackRace's skin is
    # SkinBearCave (the black bear IS the cave bear in Skyrim), not SkinBearBlack.
    ('blackbear',   Swap(0x000131E8, 0x000187FE, EXACT, 'BearBlackRace')),
    ('bearblack',   Swap(0x000131E8, 0x000187FE, EXACT, 'BearBlackRace')),
    ('brownbear',   Swap(0x000131E7, 0x000868FD, EXACT, 'BearBrownRace')),
    ('bearbrown',   Swap(0x000131E7, 0x000868FD, EXACT, 'BearBrownRace')),

    # `deer` folder — Buck is antlered, Doe is not. ElkRace's skin really is
    # named SkinDeer (crossed with DeerRace/SkinReinDeer); verified in the dump.
    ('deerbuck',  Swap(0x000131ED, 0x0005E979, NEAR, 'ElkRace',
                       'Skyrim elk is the antlered male')),
    ('buck',      Swap(0x000131ED, 0x0005E979, NEAR, 'ElkRace')),

    # `ghost` folder — the wisp shade is the only translucent floating humanoid
    ('ghost', Swap(0x000F1182, 0x000F1187, NEAR, 'WispShadeRace',
                   'wisp shade is a wispmother thrall, not a generic ghost')),
]


# Nehrim-only folders that DO have a vanilla match. Kept separate from
# BY_FOLDER only for readability — merged into it at import time below.
_NEHRIM = {
    'chicken': Swap(0x000A919D, 0x000A919C, EXACT, 'ChickenRace'),
    'cow':     Swap(0x0004E785, 0x0004E784, EXACT, 'CowRace'),
    'ox':      Swap(0x0004E785, 0x0004E784, NEAR, 'CowRace',
                    'ox is a cow-class draft animal'),
    'spinne':  Swap(0x000131F8, 0x0003636F, EXACT, 'FrostbiteSpiderRace',
                    'spinne = spider'),
    'hillgiant': Swap(0x000131F9, 0x00048D94, NEAR, 'GiantRace'),
    # TrollFrostRace shares SkinTroll with TrollRace (verified — one skin,
    # two races; the frost variant differs by race record, not by skin).
    # Exact for the same reason as `troll`: a troll standing in for a troll.
    'nightmaretroll': Swap(0x00013206, 0x00016EE4, EXACT, 'TrollFrostRace'),
    'mrsiikasdonkey': Swap(0x000131FD, 0x00060715, NEAR, 'HorseRace',
                           'donkey rides on the horse rig'),
}
BY_FOLDER.update(_NEHRIM)


def resolve(edid: str, folder: str, allow_near: bool = False):
    """Vanilla Swap for this creature, or None to keep the converted one.

    `edid` wins over `folder` because one Oblivion folder can hold several
    creatures Skyrim treats separately (dog/wolf). An explicit None in
    BY_EDITORID means "this specific creature has no match even though its
    folder does" — the SI skinned hound in the `dog` folder — and must stop the
    lookup rather than falling through to the folder entry.
    """
    text = (edid or '').lower().replace('_', '').replace(' ', '')
    for key, swap in BY_EDITORID:
        if key.replace(' ', '') in text:
            if swap is None:
                return None
            return swap if (allow_near or swap.quality == EXACT) else None
    swap = BY_FOLDER.get((folder or '').lower())
    if swap is None:
        return None
    return swap if (allow_near or swap.quality == EXACT) else None


def stats(allow_near: bool = False) -> str:
    """One-line summary for the import log."""
    folders = [s for s in BY_FOLDER.values()
               if allow_near or s.quality == EXACT]
    return (f'{len(folders)} folder swaps '
            f'({sum(1 for s in folders if s.quality == EXACT)} exact, '
            f'{sum(1 for s in folders if s.quality == NEAR)} near)')
