"""Generated creature RACE / ARMA / ARMO(skin) records.

Every CREA whose model folder was converted by the creature pipeline
(asset_convert/havok/creature_pipeline.py — see export/<plugin>/creature_projects.json)
gets a GENERATED race chain instead of the old Skyrim-race aliasing
(skyrim_overrides.resolve_creature_race, kept only as a fallback for
creatures without a converted project). Humanoid NPC_ records are NOT
affected — they keep the Skyrim playable-race override system.

Record layouts are mirrored from a real Skyrim.esm dump of DogRace
(000131EE) / SkinDog (0004B2C9) / NakedDogAA (0004B2CA):

  RACE: EDID FULL DESC WNAM BOD2 KSIZ KWDA DATA(164) MNAM ANAM MODT FNAM
        ANAM MODT MTNM*5 VTCK PNAM UNAM [ATKD ATKE]* NAM1 MNAM INDX MODL
        MODT FNAM INDX MODL MODT GNAM NAM3 MNAM MODL MODT FNAM MODL MODT
        NAM4 NAM5 ONAM LNAM NAME*32 VNAM QNAM UNES
  ARMA: EDID BOD2 RNAM DNAM(12) MOD2
  ARMO: EDID OBND BOD2 RNAM DESC MODL* DATA DNAM   (flags=4 non-playable)

DATA(164) template = DogRace values with per-creature patches at
offset 36 (starting health), 40 (magicka), 44 (stamina), 96 (unarmed
damage) and 100 (unarmed reach). One race is created per unique
(model folder, body-part set) so e.g. dog and wolf — which share one
folder/project but list different NIFZ parts — get separate races that
reference the same generated behavior project.

Deliberate v1 simplifications (documented in docs/commentary/asset_convert_creature.md):
GNAM points at the vanilla canine body-part data (bone names won't match
the Oblivion skeleton — hits still register, dismember targeting is off),
and ARMA has no footstep SNDD yet.
"""

from asset_convert.havok.behavior_vocabulary import movement_type_names
import os
import struct

from ..base.writer import (pack_record, pack_subrecord, pack_string_subrecord,
                     pack_formid_subrecord, pack_obnd)
from ..base.text_reader import get_float, get_formid, get_int, get_str
from .creature_projects import (bodies_of, folder_of,
                                folders_built_by_master, load_projects)

# GMST fNPCHealthLevelBonus (Skyrim.esm) — health the engine grants per level
# above 1. Defined here rather than imported from record_types.actors because
# actors.py imports this module, and a module-level import back would cycle.
TES5_HEALTH_LEVEL_BONUS = 5

# ---------------------------------------------------------------------------
# Vanilla template constants (Skyrim.esm DogRace chain, byte-verified)
# ---------------------------------------------------------------------------

#: TES4 fMagickaReturnBase/Mult: magicka regen is base + mult * Willpower, %/s.
_MAGICKA_RETURN = {'fMagickaReturnBase': 0.75, 'fMagickaReturnMult': 0.02}


def _load_magicka_return(by_type: dict, master_export: dict) -> None:
    """Adopt the plugin's (else its master's) fMagickaReturn* GMST values."""
    for recs in ((master_export or {}).get('GMST', []), by_type.get('GMST', [])):
        for rec in recs:
            edid = get_str(rec, 'EditorID')
            if edid in _MAGICKA_RETURN:
                _MAGICKA_RETURN[edid] = get_float(rec, 'DATA.Value',
                                                  _MAGICKA_RETURN[edid])

_RACE_DATA_TEMPLATE = bytes.fromhex(
    '0f23ff00ff00ff00ff00ff00ff0000000000803f0000803f0000803f0000803f'
    '4889300000002041000000000000a041000048430000803f0000803f0000803f'
    '01000000ffffffffffffffff00000000ffffffff000000000000000000002041'
    '0000004100008042ffffffff00000000000000000000803e0000a04000000000'
    '7fea7dc20000000000000000000048c2000000000000824200000000000096c3'
    '00000000')
_MODT = bytes.fromhex('020000000000000000000000')
_ARMA_DNAM = bytes.fromhex('000000000000001100000000')
# MOVT SPED layout: 11 floats — leftWalk, leftRun, rightWalk, rightRun,
# forwardWalk, forwardRun, backWalk, backRun, rotateInPlaceWalk,
# rotateInPlaceRun, rotateWhileMovingRun (rotate in RADIANS/sec — vanilla
# dog: 0,0,0,0, 74.54, 500.14, 74.54, 74.54, π, 3π/2, 3π/2, both dog MOVTs
# byte-identical).  Per-creature forward/back speeds come from the clip
# root-motion endpoints (proj['speeds'], ck-cmd calculateMOVTs method):
# commanded speed must equal the animation's natural speed or actors slide/
# moonwalk (2026-07-09 "far too fast" report — every creature was shipping
# the vanilla dog's 500 u/s run).  No run clip → run = walk speed (the
# creature simply can't move faster than its only gait).
_DOG_WALK, _DOG_RUN = 74.54, 500.14
_ROT_WALK, _ROT_RUN = 3.14159265, 4.71238898
_MOVT_INAM = bytes.fromhex('FFFF7F7FFFFF7F7FFFFF7F7F')


def _movt_sped(speeds: dict) -> bytes:
    """Commanded speed == the shipped animation's root-motion speed, at
    playback rate 1.0 — the invariant every vanilla creature holds (chaurus:
    MOVT run 350.267 == blend top anchor == run clip natural speed; sabrecat
    563, wolf 555 likewise).  Oblivion's faster attribute-formula ground
    speed is BAKED into the clips by the creature pipeline
    (hkx_anim.timescale_clip), so `speeds` here are already final; a runtime
    MOVT raise above the clips' real speed was tried twice (2026-07-16 and
    after) and the lion still ran in slow motion in game."""
    walk = speeds.get('walk') or _DOG_WALK
    run = speeds.get('run') or walk
    back = speeds.get('back') or walk * 0.8
    # Strafe columns: 0 while the creature ships no Left/Right clip (the
    # vanilla dog case, and what every creature used to get).  Once a strafe
    # STATE exists in the graph the clip's root motion is real, so commanding
    # 0 sideways makes the actor glide -- same invariant as forward/back.
    left = speeds.get('left') or 0.0
    right = speeds.get('right') or 0.0
    return struct.pack('<11f', left, left, right, right, walk, max(run, walk),
                       back, back, _ROT_WALK, _ROT_RUN, _ROT_RUN)


def _movt_sped_swim(speeds: dict) -> bytes:
    """SPED for the generated <base>Swim movement type (see
    hkx_behavior.movement_type_names): the swim clips' own natural speeds."""
    swim = speeds.get('swim') or _DOG_WALK
    fast = speeds.get('swimfast') or swim
    back = speeds.get('swimback') or swim * 0.8
    return struct.pack('<11f', 0.0, 0.0, 0.0, 0.0, swim, max(fast, swim),
                       back, back, _ROT_WALK, _ROT_RUN, _ROT_RUN)


_MTNM_CODES = (b'WALK', b'RUN1', b'SNEK', b'BLDO', b'SWIM')
_EGT_MALE = 'Actors\\Character\\UpperBodyHumanMale.egt'
_EGT_FEMALE = 'Actors\\Character\\UpperBodyHumanFemale.egt'
_GNAM_BPTD = 0x0004FBF5      # canine body part data (see module docstring)
_NAM4_IMPACT_MAT = 0x0005A28F
_NAM5_IMPACT_SET = 0x000A956F
_ONAM_OPEN_SND = 0x000A5013
_LNAM_CLOSE_SND = 0x000A5014
# RACE VNAM 'Equipment Flags' — which weapon classes this race may EQUIP.
# Bit 0 Hand To Hand, 1 1H Sword, 2 1H Dagger, 3 1H Axe, 4 1H Mace,
# 5 2H Sword, 6 2H Axe, 7 Bow, 8 Staff, 9 Spell, 10 Shield, 11 Torch,
# 12 Crossbow (wbDefinitionsTES5.pas:9510).  The engine refuses to equip a
# class whose bit is clear, so this is a hard gate in front of the actor's
# inventory — not a hint.
#
# The template value below is DogRace's: hand-to-hand ONLY.  Every generated
# creature race inherited it, which is why armed creatures (goblins, dremora,
# skeletons) carried their weapons but fought bare-handed — the weapon was in
# CNTO and combat AI wanted it, but the race forbade the equip.  Weapon-using
# vanilla creature races set the bits they need instead: FalmerRace FFFFEE8B,
# DraugrRace/SkeletonRace FFFFE6FF, NordRace FFFFFFFF.
#
# Bits 13+ are set in every vanilla race (the high FFFFE… pattern is universal,
# including DogRace), so they carry over unchanged and only the low 13 vary.
_VNAM_BASE = 0xFFFFE000          # the always-set high bits, no weapon class
_VNAM_HAND_TO_HAND = 1 << 0
_VNAM_SPELL = 1 << 9
_VNAM_SHIELD = 1 << 10
_VNAM_TORCH = 1 << 11

# TES4 WEAP DATA.Type → the VNAM bit that lets the race equip it.
# (wbDefinitionsTES4 WEAP: 0 Blade1H, 1 Blade2H, 2 Blunt1H, 3 Blunt2H,
# 4 Staff, 5 Bow.)  Oblivion has no separate dagger/axe/mace class, so a 1H
# blade opens Sword+Dagger and 1H blunt opens Axe+Mace — the converted WEAP
# records pick one of those Skyrim types per weapon, and the race must permit
# whichever the converter chose or that weapon alone stays unequipped.
_TES4_WEAP_TYPE_VNAM = {
    0: (1 << 1) | (1 << 2),      # Blade 1H  → 1H Sword + 1H Dagger
    1: (1 << 5) | (1 << 6),      # Blade 2H  → 2H Sword + 2H Axe
    2: (1 << 3) | (1 << 4),      # Blunt 1H  → 1H Axe + 1H Mace
    3: (1 << 5) | (1 << 6),      # Blunt 2H  → 2H Sword + 2H Axe
    4: 1 << 8,                   # Staff
    5: (1 << 7) | (1 << 12),     # Bow       → Bow + Crossbow
}

# EQUP equip slots (Skyrim.esm EQUP dump).  A race lists the slots it can fill;
# DogRace names only RightHand, so even with the VNAM bits set an armed
# creature had nowhere to put a shield or an off-hand weapon.  Weapon-using
# vanilla creature races name both hands (+Voice/Potion), so armed creatures
# get the same set.
_QNAM_UNARMED = 0x00013F42       # RightHand
_QNAM_LEFT_HAND = 0x00013F43
_QNAM_VOICE = 0x00025BEE
_QNAM_POTION = 0x00035698
_KW_ANIMAL = 0x00013798
_KW_UNDEAD = 0x00013796
_KW_DAEDRA = 0x00013797
_KW_CREATURE = 0x00013795

# TES4 creature folder → actor-type keyword set
_FOLDER_KEYWORDS = {
    'bear': [_KW_ANIMAL], 'boar': [_KW_ANIMAL], 'deer': [_KW_ANIMAL],
    'dog': [_KW_ANIMAL], 'horse': [_KW_ANIMAL], 'mountainlion': [_KW_ANIMAL],
    'mudcrab': [_KW_ANIMAL], 'rat': [_KW_ANIMAL], 'sheep': [_KW_ANIMAL],
    'slaughterfish': [_KW_ANIMAL],
    'skeleton': [_KW_UNDEAD, _KW_CREATURE],
    'zombie': [_KW_UNDEAD, _KW_CREATURE],
    'lich': [_KW_UNDEAD, _KW_CREATURE],
    'ghost': [_KW_UNDEAD, _KW_CREATURE],
    'wraith': [_KW_UNDEAD, _KW_CREATURE],
    'clannfear': [_KW_DAEDRA, _KW_CREATURE],
    'daedroth': [_KW_DAEDRA, _KW_CREATURE],
    'scamp': [_KW_DAEDRA, _KW_CREATURE],
    'spiderdaedra': [_KW_DAEDRA, _KW_CREATURE],
    'xivilai': [_KW_DAEDRA, _KW_CREATURE],
    'flameatronach': [_KW_DAEDRA, _KW_CREATURE],
    'frostatronach': [_KW_DAEDRA, _KW_CREATURE],
    'stormatronach': [_KW_DAEDRA, _KW_CREATURE],
    'mehrunesdagon': [_KW_DAEDRA, _KW_CREATURE],
}

# crea_fid_low24 → (race_fid, folder) — consumed by convert_CREA
_CREA_RACE_MAP = {}

# folder(lower) → generated creature VTYP FormID, filled by
# build_creature_voice_types() at the END of the import run.
#
# A creature must NOT share a humanoid dialogue voice type. Every one of the 42
# vanilla Cr* voice types writes DNAM=0 (no flags), while human voices set bit 0
# 'Allow Default Dialog' (DNAM=1 male / 3 female) — the flag that routes an
# actor into the dialogue system. convert_CREA ran creatures through the
# humanoid chain (TES4 race → VOICE_TYPE_MAP → Imperial fallback), and since a
# creature has no TES4 race in that map they ALL landed on TES4MaleImperial:
# a human dialogue voice, which is not how a creature is voiced.
#
# Allocated LAST, after every other generated record, so no existing FormID
# moves — an allocating pass inserted earlier renumbers everything after it
# (see project_formid_allocation_order_contract; it broke NPC outfits).
_CREA_VOICE_MAP = {}

# Vanilla creature VTYP DNAM: 0 on all 42 Cr* records in Skyrim.esm.
_CREATURE_VTYP_DNAM = 0


# crea_fid_low24 → folder, for EVERY CREA — including ones that never got a
# generated race. A creature whose mesh is an effect NIF rather than a skinned
# body has no NIFZ parts, so build_creature_races skips it (the 5 Oblivion
# Will-o-the-Wisps), but it still carries a full CSDT sound set and still needs
# a creature voice rather than the humanoid fallback.
_CREA_FOLDER_MAP = {}

# Generated body-ARMA FormID → creature folder. The footstep chain
# (creature_footsteps) needs to find each creature's ARMA to fill in SNDD.
_CREA_ARMA_FOLDER = {}


def get_creature_arma_folders() -> dict:
    """{generated body ARMA FormID: creature folder}, for footstep wiring."""
    return _CREA_ARMA_FOLDER


# Vanilla Skyrim ACTI DefaultAshPileGhost (Effects\\AshPileGhost01.nif) -- a
# ghost-tinted pile of goo, which is exactly Oblivion's ectoplasm.  Vanilla
# also ships DefaultAshPileDarkGhost (0x0010C649) and DefaultAshPileGhostBlack
# (0x0010D6EF); the plain ghost pile is the right default for both the ghost
# and the wraith.  Master
# index 0, written unremapped (same convention as the AACT ids in
# creature_idles).
DEFAULT_ASH_PILE_GHOST = 0x00101048


# folder -> generated pile ACTI FormID (one per dissolving creature folder)
_CREA_PILE_ACTI = {}


def _pile_mesh_bounds(proj, pile_name):
    """Integer OBND bounds of an emitted death-pile NIF, or None.

    Read from the shipped mesh rather than assumed: the two piles differ by
    4x in size, and the activation target the engine builds from OBND has to
    cover the geometry the player is looking at.
    """
    import os

    rel = os.path.join('meshes', proj.get('body_dir', ''), pile_name)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = []
    out = os.path.join(root, 'output')
    if os.path.isdir(out):
        for plugin in sorted(os.listdir(out)):
            candidates.append(os.path.join(out, plugin, rel))
    path = next((p for p in candidates if os.path.exists(p)), None)
    if path is None:
        return None
    try:
        from asset_convert.nif.pyffi_monkey_patch import apply_patches
        apply_patches()
        from pyffi.formats.nif import NifFormat
        data = NifFormat.Data()
        with open(path, 'rb') as f:
            data.read(f)
    except Exception:
        return None
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    seen = set()
    for r in data.roots:
        if r is None:
            continue
        for blk in r.tree():
            if not isinstance(blk, NifFormat.NiTriBasedGeom) or \
                    id(blk) in seen:
                continue
            seen.add(id(blk))
            geom = getattr(blk, 'data', None)
            verts = getattr(geom, 'vertices', None) if geom else None
            if not verts:
                continue
            s = float(getattr(blk, 'scale', 1.0) or 1.0)
            t = blk.translation
            base = (t.x, t.y, t.z)
            for v in verts:
                for i, c in enumerate((v.x, v.y, v.z)):
                    w = c * s + base[i]
                    if w < lo[i]:
                        lo[i] = w
                    if w > hi[i]:
                        hi[i] = w
    if lo[0] > hi[0]:
        return None
    import math
    return ([int(math.floor(v)) for v in lo],
            [int(math.ceil(v)) for v in hi])


def build_creature_death_piles(writer) -> int:
    """One ACTI per dissolving creature, pointing at its EXTRACTED pile mesh.

    The pile an Oblivion ghost leaves is authored geometry inside its own
    skeleton.nif, which asset_convert lifts into `<folder>deathpile.nif` next
    to the creature project (see nif_converter.extract_death_pile).  This
    wraps that mesh in a record `Actor.AttachAshPile` can place -- Oblivion's
    own ectoplasm rather than Skyrim's DefaultAshPileGhost.

    ACTI, NOT STAT: a static cannot be activated.  All six `DefaultAshPile*`
    records in Skyrim.esm are ACTI.  Layout copied from DefaultAshPileGhost
    (0x00101048): EDID OBND FULL MODL PNAM FNAM.  OBND is read from the
    shipped mesh (`_pile_mesh_bounds`) because the ghost's pile is ~21 units
    across and the wraith's ~92; FULL is the crosshair prompt, PNAM the
    marker color (xEdit: SetRequired) and FNAM a zero U16 flag word.

    Creatures whose skeleton carries no pile keep the vanilla fallback.

    See: docs/commentary/asset_convert_creature.md#pile-acti-record-fields
    """
    _CREA_PILE_ACTI.clear()
    for folder in sorted(_PROJECTS):
        proj = _PROJECTS[folder]
        if not proj.get('dissolves_on_death'):
            continue
        pile = proj.get('death_pile')
        if not pile:
            continue
        fid = writer.derive_formid('CREA_PILE', folder)
        subs = pack_string_subrecord(
            'EDID', f'TES4Cr{folder.capitalize()}DeathPile')
        bounds = _pile_mesh_bounds(proj, pile)
        if bounds is None:
            subs += pack_obnd(-24, -24, -4, 24, 24, 16)
        else:
            (x1, y1, z1), (x2, y2, z2) = bounds
            subs += pack_obnd(x1, y1, z1, x2, y2, z2)
        subs += pack_string_subrecord('FULL', 'Ectoplasm')
        subs += pack_string_subrecord(
            'MODL', f"{proj['body_dir']}\\{pile}")
        subs += pack_subrecord('PNAM', b'\x00\x00\x00\x00')
        subs += pack_subrecord('FNAM', struct.pack('<H', 0))
        writer.add_record('ACTI', pack_record('ACTI', fid, 0, subs))
        _CREA_PILE_ACTI[folder] = fid
    return len(_CREA_PILE_ACTI)


def creature_dissolve_info(crea_fid: int):
    """(ash pile FormID, death-clip seconds) when this CREA dissolves on death.

    None for every ordinary creature.  The trigger is the AUTHORED animation,
    never a name: asset_convert.havok.hkx_behavior.detect_dissolve marks a project
    whose death.kf hides the actor's own skin holder (`SkinAttachment`) with a
    NiVisController instead of dropping the body.  Measured over every creature
    folder in all three test plugins, that fires on exactly four -- ghost and
    wraith in Oblivion and Nehrim -- and correctly rejects willothewisp, which
    has a `Bip01 ContainerGoo01` node but never hides its body.

    Those visibility channels have no destination in a Havok clip (and ghosts
    have no ragdoll to collapse them either), so without this the corpse stands
    upright in mid-air for ever.  Skyrim's own mechanism for the same effect is
    Actor.AttachAshPile -- see script_convert/static_scripts/TES4_GhostDissolve.psc.
    """
    folder = _CREA_FOLDER_MAP.get(crea_fid & 0x00FFFFFF)
    if not folder:
        return None
    proj = _PROJECTS.get(folder)
    if not proj or not proj.get('dissolves_on_death'):
        return None
    # This creature's OWN extracted ectoplasm when it has one; vanilla's
    # ghost pile only as a fallback for a creature whose skeleton carries
    # no authored pile.
    pile = _CREA_PILE_ACTI.get(folder) or DEFAULT_ASH_PILE_GHOST
    return pile, float(proj.get('death_duration') or 0.0)


def build_creature_voice_types(writer) -> int:
    """Phase LAST: one VTYP per creature folder.

    Runs after all other allocation so existing generated FormIDs are
    unchanged; the RACE/actor records that reference these voices are already
    written, so their VTCK slots are patched afterwards (patch_creature_voices).
    """
    _CREA_VOICE_MAP.clear()
    for folder in sorted(set(_CREA_FOLDER_MAP.values())):
        fid = writer.derive_formid('CREA_VTYP', folder)
        subs = pack_string_subrecord(
            'EDID', f'TES4Cr{folder.capitalize()}Voice')
        subs += pack_subrecord('DNAM', struct.pack('<B', _CREATURE_VTYP_DNAM))
        writer.add_record('VTYP', pack_record('VTYP', fid, 0, subs))
        _CREA_VOICE_MAP[folder] = fid
    return len(_CREA_VOICE_MAP)
# folder → project summary (attacks etc.) for anything else that needs it
_PROJECTS = {}


# folder → generated BPTD FormID, filled by build_creature_body_parts()
# at the END of the import run (allocation-last contract, like the VTYPs).
_CREA_BPTD_MAP = {}

# Vanilla DogBodyPartData (0x0004FBF5) BPND payloads, verbatim. Torso is
# part type 0, Head part type 1; everything else in the struct is the
# vanilla default block.
_BPND_TORSO = bytes.fromhex(
    '0000803F000005FF640000000000000000000000000000000000803F00000000000000'
    '00000000000000803F00000000000000000000000000000000000000000000000000000'
    '00000000000000000000000803F')
_BPND_HEAD = bytes.fromhex(
    '0000803F000105FF640000000000000000000000000000000000803F00000000000000'
    '00000000000000803F00000000000000000000000000000000000000000000000000000'
    '00000000000000000000000803F')
_BPTD_MODT = bytes.fromhex('020000000000000000000000')


def build_creature_body_parts(writer) -> int:
    """Phase LAST: one BPTD (body part data) per creature folder.

    Every generated creature RACE was pointing its GNAM at the vanilla
    CANINE body part data, whose part node names (Canine_Pelvis,
    Canine_Head) exist in no converted skeleton — so the engine's
    ragdoll/hit binding had nothing to attach to: corpses could not be
    grabbed with havok and sank through the floor. Each folder now gets a
    BPTD in the vanilla dog LAYOUT (Head part + Torso part) but with THIS
    skeleton's actual ragdoll part bones as the node names.

    Allocated last so no other generated FormID moves; the RACE GNAM slots
    are byte-patched afterwards (patch_creature_bptd).
    """
    _CREA_BPTD_MAP.clear()
    for folder in sorted({f for _r, f in _CREA_RACE_MAP.values()}):
        proj = _PROJECTS.get(folder) or {}
        part_bones = proj.get('ragdoll_bones') or []
        if not part_bones:
            continue
        skel = proj['skeleton_nif']
        base_path = f'BASE Meshes\\{skel}'
        torso_node = part_bones[0]
        spine = next((b for b in part_bones
                      if 'spine' in b.lower() or 'chest' in b.lower()),
                     torso_node)
        # Oblivion rigs name the head bone 'Bip01 Head' OR 'Bip01 Skull';
        # vanilla BPTDs match ('Canine_Head', cow 'Scull')
        head = next((b for b in part_bones
                     if 'head' in b.lower() or 'skull' in b.lower()
                     or 'scull' in b.lower()), None)

        def _part(name, node, target, bpnd):
            s = pack_string_subrecord('BPTN', name)
            s += pack_string_subrecord('BPNN', node)
            s += pack_string_subrecord('BPNT', target)
            s += pack_string_subrecord('BPNI', base_path)
            s += pack_subrecord('BPND', bpnd)
            # NAM1 is the Limb Replacement Model *path*. All 76 vanilla body
            # parts write it as the empty string (a lone NUL). Writing '0'
            # here made it a 1-character filename, so the engine took the
            # "has a replacement mesh" branch and fed "0" into the archive
            # path-hash lookup, faulting during load. NAM5 (wbModelInfo) is
            # zero-length in vanilla, not a NUL-terminated empty string.
            s += pack_string_subrecord('NAM1', '')
            s += pack_string_subrecord('NAM4', base_path)
            s += pack_subrecord('NAM5', b'')
            return s

        fid = writer.derive_formid('CREA_BPTD', folder)
        subs = pack_string_subrecord(
            'EDID', f'TES4{folder.capitalize()}BodyPartData')
        subs += pack_string_subrecord('MODL', skel)
        subs += pack_subrecord('MODT', _BPTD_MODT)
        # vanilla dog order: Head part first, Torso second
        if head:
            subs += _part('Head', head, head, _BPND_HEAD)
        subs += _part('Torso', torso_node, spine, _BPND_TORSO)
        writer.add_record('BPTD', pack_record('BPTD', fid, 0, subs))
        _CREA_BPTD_MAP[folder] = fid
    return len(_CREA_BPTD_MAP)


def patch_creature_bptd(writer) -> int:
    """Point every generated creature RACE's GNAM at its folder's BPTD
    (same placeholder-then-patch approach as the voice types)."""
    if not _CREA_BPTD_MAP:
        return 0
    race_bptd = {}
    for _crea_fid, (race_fid, folder) in _CREA_RACE_MAP.items():
        b = _CREA_BPTD_MAP.get(folder)
        if b:
            race_bptd[race_fid] = b
    patched = 0
    records = writer._top_groups.get('RACE') or []
    for i, blob in enumerate(records):
        if len(blob) < 24:
            continue
        fid = struct.unpack_from('<I', blob, 12)[0]
        bptd = race_bptd.get(fid)
        if not bptd:
            continue
        at = blob.find(b'GNAM', 24)
        if at < 0 or struct.unpack_from('<H', blob, at + 4)[0] != 4:
            continue
        records[i] = blob[:at + 6] + struct.pack('<I', bptd) + blob[at + 10:]
        patched += 1
    return patched


def patch_creature_voices(writer) -> int:
    """Point every converted creature's VTCK at its generated creature voice.

    Creature NPC_ records carry a 4-byte VTCK and the generated RACE an 8-byte
    male+female pair; both were written with the humanoid fallback voice before
    the creature VTYPs existed (build_creature_voice_types runs last so it
    cannot disturb any other FormID). This rewrites those slots in the packed
    bytes — the same placeholder-then-patch approach used for actor sounds and
    ForceGreet topics.

    Returns the number of records patched.
    """
    if not _CREA_VOICE_MAP:
        return 0

    # crea_fid -> voice, for every creature (raced or not)
    actor_voice = {}
    for crea_fid, folder in _CREA_FOLDER_MAP.items():
        voice = _CREA_VOICE_MAP.get(folder)
        if voice:
            actor_voice[crea_fid] = voice
    # race_fid -> voice, only for the folders that produced a race
    race_voice = {}
    for _crea_fid, (race_fid, folder) in _CREA_RACE_MAP.items():
        voice = _CREA_VOICE_MAP.get(folder)
        if voice:
            race_voice[race_fid] = voice

    patched = 0
    for sig, table, size in (('NPC_', actor_voice, 4), ('RACE', race_voice, 8)):
        records = writer._top_groups.get(sig) or []
        for i, blob in enumerate(records):
            if len(blob) < 24:
                continue
            fid = struct.unpack_from('<I', blob, 12)[0]
            voice = table.get(fid if sig == 'RACE' else fid & 0x00FFFFFF)
            if not voice:
                continue
            at = blob.find(b'VTCK', 24)
            if at < 0 or struct.unpack_from('<H', blob, at + 4)[0] != size:
                continue
            off = at + 6
            new = (struct.pack('<I', voice) if size == 4
                   else struct.pack('<II', voice, voice))
            records[i] = blob[:off] + new + blob[off + size:]
            patched += 1
    return patched


def get_creature_race(fid_low24: int):
    """Generated race FormID for a converted CREA, or None (→ fallback to
    resolve_creature_race aliasing)."""
    entry = _CREA_RACE_MAP.get(fid_low24)
    return entry[0] if entry else None


# A generated creature RACE is SHARED by every CREA that maps to the same mesh
# folder + body set (`made[key]` in build_creature_races), so it cannot carry any
# one creature's health. It therefore uses the same flat base every vanilla
# playable race uses, and each creature's whole pool lives in its own
# per-record ACBS.HealthOffset. An earlier version wrote the first creature's
# pool into the shared race and left the offset at 0, which gave every other
# creature sharing that race the wrong health (measured: 345 Nehrim actors, e.g.
# all three StartCelleTroll variants at 15 HP instead of 450).
CREATURE_RACE_BASE_HEALTH = 50.0


def creature_race_health(rec: dict) -> float:
    """Starting Health for a CREA's generated RACE — a flat shared base.

    See CREATURE_RACE_BASE_HEALTH: the race is shared, so per-creature health
    must not go here. `creature_health_offset` carries it instead.
    """
    return CREATURE_RACE_BASE_HEALTH


def creature_health_offset(rec: dict) -> int:
    """ACBS.HealthOffset for a CREA — carries the creature's whole TES4 pool.

    Solved so the engine's own sum,
        RACE.StartingHealth + HealthOffset + (Level-1)*fNPCHealthLevelBonus,
    lands exactly on TES4 DATA.Health. This is per-record, so creatures sharing a
    generated race still each get their own health.

    A TES4 pool of 0 is pinned dead: those are intentional corpse props (52 in
    Nehrim, 45 in Oblivion) that ship dead in Oblivion too, including the
    PC-Level-Mult ones whose runtime level term would otherwise revive them.
    """
    health = get_int(rec, 'DATA.Health', 50)
    if health <= 0:
        return -32768
    base = CREATURE_RACE_BASE_HEALTH
    if get_int(rec, 'ACBS.Flags') & 0x80:
        # PC Level Mult: the level term is a runtime multiplier, not authored.
        return max(-32768, min(int(health - base), 32767))
    level_term = (creature_capped_level(rec) - 1) * TES5_HEALTH_LEVEL_BONUS
    return max(-32768, min(int(health - base - level_term), 32767))


def creature_capped_level(rec: dict) -> int:
    """ACBS.Level for a CREA, lowered when its level term is unrepresentable.

    The engine's per-level bonus is what makes a very high level unusable: at
    level 32,767 the term is 163,830, which no int16 offset can cancel. Such a
    level is meaningless in Skyrim anyway (vanilla's highest NPC is level 100),
    so it is reduced to the highest value whose term the offset CAN cancel,
    keeping the resulting health pool exactly faithful.
    """
    level = max(1, min(get_int(rec, 'ACBS.Level', 1), 65535))
    if get_int(rec, 'ACBS.Flags') & 0x80:
        return 1000
    health = get_int(rec, 'DATA.Health', 50)
    surplus = health - CREATURE_RACE_BASE_HEALTH
    # Lower bound: the offset must be able to cancel the level term.
    max_level = int((surplus + 32768) // TES5_HEALTH_LEVEL_BONUS) + 1
    level = max(1, min(level, max_level))
    if surplus > 32767:
        # Too much health for the int16 offset alone (Nehrim ships 15 such
        # creatures, up to 65,200 HP). RAISE the level so its per-level bonus —
        # the engine's own mechanism — absorbs the surplus, exactly as
        # _health_and_level does for NPCs. Rounded up so the remainder still
        # fits, and capped at the U16 field.
        need = surplus - 32767
        level = max(level, min(65535, -(-need // TES5_HEALTH_LEVEL_BONUS) + 1))
    # ACBS.Level is a U16 struct field. CREATURE_RACE_BASE_HEALTH is a float
    # (it is packed as <f into RACE DATA), so surplus — and every level derived
    # from it above — is a float too, which struct.pack rejects with "required
    # argument is not an integer". Only creatures needing the raise-level branch
    # (health surplus > 32767) ever hit it, which is why it went unnoticed.
    return int(level)


# Built by build_creature_races from this plugin (+ its masters'): the item
# graph a CREA inventory can reach.  Phase 0f runs BEFORE outfits.load_item_index
# (Phase 0i), so the race builder cannot use that index and keeps its own —
# it only needs weapon/armor types, not the outfit slot machinery.
_WEAP_TYPE = {}      # fid_low24 → TES4 WEAP DATA.Type
_ARMOR_FIDS = set()  # fid_low24 of ARMO records (shield detection)
_SHIELD_FIDS = set() # fid_low24 of ARMO records occupying the shield slot
_LVLI_REC = {}       # fid_low24 → LVLI record dict
_SPEL_REC = {}       # fid_low24 → SPEL record dict
_LVSP_REC = {}       # fid_low24 → LVSP record dict

# TES4 ARMO BMDT biped slot bits (wbDefinitionsTES4.pas:1326-1341).
# Shield is bit 13 and Torch bit 14 — NOT bit 9, which is 'Weapon'.
_TES4_BIPED_SHIELD = 1 << 13
_TES4_BIPED_TORCH = 1 << 14

_MAX_LVLI_DEPTH = 8


def load_creature_item_index(by_type: dict, master_export: dict = None) -> None:
    """Index WEAP/ARMO/LVLI so a CREA's equipment classes can be determined.

    Mirrors outfits.load_item_index's masters-first ordering: a dependent
    plugin arms its creatures out of its master's armoury, so without the
    masters' records most inventory entries resolve to nothing and the
    creature's race would be built as unarmed.
    """
    _WEAP_TYPE.clear()
    _ARMOR_FIDS.clear()
    _SHIELD_FIDS.clear()
    _LVLI_REC.clear()
    _SPEL_REC.clear()
    _LVSP_REC.clear()

    def _add(rec):
        sig = rec.get('Signature')
        if sig not in ('WEAP', 'ARMO', 'LVLI', 'SPEL', 'LVSP'):
            return
        try:
            fid = int(rec.get('FormID', ''), 16) & 0x00FFFFFF
        except (ValueError, TypeError):
            return
        if sig == 'WEAP':
            _WEAP_TYPE[fid] = get_int(rec, 'DATA.Type')
        elif sig == 'ARMO':
            _ARMOR_FIDS.add(fid)
            if get_int(rec, 'BMDT.BipedFlags') & (_TES4_BIPED_SHIELD
                                                  | _TES4_BIPED_TORCH):
                _SHIELD_FIDS.add(fid)
        elif sig == 'SPEL':
            _SPEL_REC[fid] = rec
        elif sig == 'LVSP':
            _LVSP_REC[fid] = rec
        else:
            _LVLI_REC[fid] = rec

    if master_export:
        for rec in master_export.values():
            _add(rec)
    # This plugin's own records are indexed LAST so an override wins.
    for sig in ('WEAP', 'ARMO', 'LVLI', 'SPEL', 'LVSP'):
        for rec in by_type.get(sig, []):
            _add(rec)


def _item_vnam_bits(fid: int, depth: int = 0, path=()) -> int:
    """VNAM equipment bits every item this inventory entry can yield needs.

    A leveled list contributes the UNION of its leaves': the engine rolls one
    at spawn, and whichever it picks must be equippable, so the race has to
    permit them all.
    """
    fid &= 0x00FFFFFF
    if fid in path or depth > _MAX_LVLI_DEPTH:
        return 0
    wtype = _WEAP_TYPE.get(fid)
    if wtype is not None:
        return _TES4_WEAP_TYPE_VNAM.get(wtype, 0)
    if fid in _SHIELD_FIDS:
        return _VNAM_SHIELD
    if fid in _ARMOR_FIDS:
        return 0                 # ordinary armor needs no equipment flag
    rec = _LVLI_REC.get(fid)
    if rec is None:
        return 0
    sub_path = path + (fid,)
    bits = 0
    for i in range(get_int(rec, 'EntryCount')):
        try:
            entry = int(rec.get(f'Entry[{i}].FormID', ''), 16)
        except (ValueError, TypeError):
            continue
        bits |= _item_vnam_bits(entry, depth + 1, sub_path)
    return bits


def _creature_equip_flags(recs: list) -> int:
    """VNAM 'Equipment Flags' for a generated race, from what its creatures
    actually carry.

    A generated race is SHARED by every CREA with the same mesh folder and body
    set, so the flags are the union over all of them — a race must permit
    whatever any of its creatures was authored to wield (goblin berserkers,
    warlords and shamans share one skeleton but carry blades, bows and staffs
    respectively).

    Hand-to-hand is always allowed — every creature can attack unarmed, and
    that is the one bit even DogRace sets.  Spell is added only when a creature
    sharing the race actually knows one: TES4 grants spells through SPLO rather
    than an inventory item, so the inventory alone never reveals it.  Vanilla
    splits the same way (census of 99 Skyrim.esm races: 60 set the Spell bit,
    31 are exactly FFFFE001 with neither spells nor weapons).
    """
    bits = _VNAM_HAND_TO_HAND
    for rec in recs:
        if get_int(rec, 'SpellCount'):
            bits |= _VNAM_SPELL
        for i in range(get_int(rec, 'ItemCount')):
            try:
                fid = int(rec.get(f'Item[{i}].FormID', ''), 16)
            except (ValueError, TypeError):
                continue
            bits |= _item_vnam_bits(fid)
    # A creature that can hold a shield can hold a torch on the same node.
    if bits & _VNAM_SHIELD:
        bits |= _VNAM_TORCH
    return _VNAM_BASE | bits


# TES4 CREA ACBS.Flags locomotion bits -> TES5 RACE DATA.Flags
# (wbDefinitionsTES4 CREA ACBS: 0x10 Swims, 0x20 Flies, 0x40 Walks;
# wbRACE_DATAFlags01: 0x40 Swims, 0x80 Flies, 0x100 Walks, 0x800 No Combat
# In Water). The AI keeps an actor where its race says it can go: vanilla
# SlaughterfishRace = 0x00100040 (Swims, no Walks), WolfRace 0x00108948
# (Swims|Walks|NoCombatInWater), HorkerRace 0x00100148 (Swims|Walks, fights
# in water). The DogRace template carried Swims|Walks|NoCombatInWater for
# EVERY creature, so slaughterfish walked out of the water and bounced
# around on land (2026-08-23).
_TES4_CREA_SWIMS, _TES4_CREA_FLIES, _TES4_CREA_WALKS = 0x10, 0x20, 0x40
_RACE_SWIMS, _RACE_FLIES, _RACE_WALKS = 0x40, 0x80, 0x100
_RACE_NO_COMBAT_IN_WATER = 0x800


def _race_locomotion_flags(recs: list) -> int:
    """Swims/Flies/Walks union over the creatures sharing the race (one race
    per mesh folder), plus NoCombatInWater for pure walkers."""
    t4 = 0
    for r in recs:
        t4 |= get_int(r, 'ACBS.Flags')
    bits = 0
    if t4 & _TES4_CREA_SWIMS:
        bits |= _RACE_SWIMS
    if t4 & _TES4_CREA_FLIES:
        bits |= _RACE_FLIES
    if t4 & _TES4_CREA_WALKS or not bits:
        bits |= _RACE_WALKS
    if not (bits & _RACE_SWIMS):
        bits |= _RACE_NO_COMBAT_IN_WATER
    return bits


def _race_data(rec: dict, race_recs: list = None) -> bytes:
    """The 164-byte RACE DATA: DogRace template with CREA stat patches.

    Starting Health is the flat CREATURE_RACE_BASE_HEALTH, NOT this creature's
    pool: the race is shared by every CREA with the same mesh folder and body
    set, so per-creature health belongs in ACBS.HealthOffset instead (see
    creature_health_offset).
    """
    data = bytearray(_RACE_DATA_TEMPLATE)
    flags = struct.unpack_from('<I', data, 32)[0]
    flags &= ~(_RACE_SWIMS | _RACE_FLIES | _RACE_WALKS
               | _RACE_NO_COMBAT_IN_WATER)
    flags |= _race_locomotion_flags(race_recs or [rec])
    struct.pack_into('<I', data, 32, flags)
    struct.pack_into('<f', data, 36, float(creature_race_health(rec)))
    # Starting Magicka is 0 for the same shared-race reason as health: the
    # actor's whole TES4 SpellPoints pool ships as ACBS.MagickaOffset (see
    # actors._crea_acbs) — the founding record's pool here would double it
    # for the founder and misstate it for everyone else. An actor with zero
    # magicka can never pay a cast cost, so this pair is a casting gate.
    struct.pack_into('<f', data, 40, 0.0)
    struct.pack_into('<f', data, 44, float(get_int(rec, 'ACBS.Fatigue', 100)))
    struct.pack_into('<f', data, 88, _MAGICKA_RETURN['fMagickaReturnBase']
                     + _MAGICKA_RETURN['fMagickaReturnMult']
                     * get_int(rec, 'DATA.Willpower', 50))
    struct.pack_into('<f', data, 96,
                     float(max(1, get_int(rec, 'DATA.AttackDamage', 5))))
    reach = get_int(rec, 'RNAM.AttackReach', 64) or 64
    struct.pack_into('<f', data, 100, float(reach))
    return bytes(data)


# TES4 SPIT.Type: 0 = castable Spell, 1 = Disease, 2 = Power,
# 3 = Lesser Power, 4 = Ability, 5 = Poison. Only a castable Spell is an
# ATTACK; Abilities/Diseases are passive racial traits and must never be
# fired at a target (the scamp's AbDaedricResistWeak is a self-buff).
_TES4_SPIT_SPELL = 0

# TES4 effect target types (as the export writes them): Self, Touch, Target.
_TES4_EFFECT_SELF = 'Self'
_TES4_EFFECT_TOUCH = 'Touch'
_TES4_EFFECT_TARGET = 'Target'


def _spell_effect_ranges(fid: int, depth: int = 0) -> set:
    """The effect delivery ranges a TES4 spell can produce ('Self'/'Touch'/
    'Target'), castable spells (SPIT.Type 0) only.

    A leveled spell resolves through its entries — the scamp's fireball
    arrives as LVSP LL2CreatureScampStunted100 -> SPEL Flare — so a leveled
    list yields the union of its members' ranges.
    """
    if depth > _MAX_LVLI_DEPTH:
        return set()
    fid &= 0x00FFFFFF
    rec = _SPEL_REC.get(fid)
    if rec is not None:
        if get_int(rec, 'SPIT.Type') != _TES4_SPIT_SPELL:
            return set()
        return {rec.get(f'Effect[{i}].Type')
                for i in range(get_int(rec, 'EffectCount'))
                if rec.get(f'Effect[{i}].Type')}
    out = set()
    lv = _LVSP_REC.get(fid)
    if lv is not None:
        for i in range(get_int(lv, 'EntryCount')):
            try:
                entry = int(lv.get(f'Entry[{i}].FormID', ''), 16)
            except (ValueError, TypeError):
                continue
            out |= _spell_effect_ranges(entry, depth + 1)
    return out


def _spell_is_offensive(fid: int) -> bool:
    """Would this TES4 spell be aimed at an enemy?  Castable spells with at
    least one non-Self effect; Abilities/Diseases/Powers are passive racial
    traits (the scamp's AbDaedricResistWeak is a self-buff) and never
    qualify."""
    return bool(_spell_effect_ranges(fid) - {_TES4_EFFECT_SELF})


def creature_touch_attack_spell(recs: list) -> int:
    """The spell to hang on the race's MELEE attacks as ATKD 'Attack Spell',
    or 0.

    This is the vanilla melee-caster idiom: the flame atronach's four
    ordinary attackStart_* entries each name crAtronachFlameMeleeAttack /
    ...PowerAttack — a fire spell applied by the swing (109 attack entries
    across Skyrim.esm carry an Attack Spell). Its TES4 analogue is the
    TOUCH-delivery offensive spell a creature cast in melee range, so
    exactly those qualify: castable (SPIT.Type 0), at least one Touch
    effect, and no Target effect (aimed spells go through the real cast
    chain — the graph's FireForget states — instead).

    First match in (record, slot) order so the result is deterministic; the
    race is shared by every CREA with the same mesh + body set.
    """
    for rec in recs:
        for i in range(get_int(rec, 'SpellCount')):
            fid = get_formid(rec, f'Spell[{i}]')
            if not fid:
                continue
            ranges = _spell_effect_ranges(fid)
            if _TES4_EFFECT_TOUCH in ranges and \
                    _TES4_EFFECT_TARGET not in ranges:
                return fid
    return 0


def creature_has_offensive_spell(recs: list) -> bool:
    """Does any creature sharing the race know a spell it would aim at an
    enemy? (Gates nothing by itself — SPLO + the graph's cast chain do the
    casting — but callers use it for reporting.)"""
    return any(_spell_is_offensive(get_formid(rec, f'Spell[{i}]') or 0)
               for rec in recs
               for i in range(get_int(rec, 'SpellCount')))


def _atkd(damage_mult: float = 1.0, spell: int = 0, chance: float = 1.0,
          flags: int = 0) -> bytes:
    """44-byte attack data: vanilla dog Attack1 values.

    `spell` is the ATKD 'Attack Spell' (xEdit: FormID -> [SPEL, SHOU, NULL]).
    When set, performing this attack CASTS that spell — the mechanism every
    vanilla casting creature uses.
    """
    return struct.pack('<ffIIfffIfff',
                       damage_mult, chance, spell, flags, 0.0, 35.0, 0.75, 0,
                       0.0, 0.0, 1.0)


def _build_race(writer, rec, folder: str, bodies: list, proj: dict,
                race_fid: int, skin_fid: int, edid: str, full: str,
                vnam_flags: int = None, race_recs: list = None) -> None:
    subs = b''
    subs += pack_string_subrecord('EDID', edid)
    subs += pack_string_subrecord('FULL', full)
    subs += pack_subrecord('DESC', b'\x00')
    subs += pack_formid_subrecord('WNAM', skin_fid)
    subs += pack_subrecord('BOD2', struct.pack('<II', 0, 2))
    keywords = _FOLDER_KEYWORDS.get(folder, [_KW_CREATURE])
    subs += pack_subrecord('KSIZ', struct.pack('<I', len(keywords)))
    subs += pack_subrecord('KWDA',
                           b''.join(struct.pack('<I', k) for k in keywords))
    subs += pack_subrecord('DATA', _race_data(rec, race_recs))

    skeleton = proj['skeleton_nif']
    for marker in ('MNAM', 'FNAM'):
        subs += pack_subrecord(marker, b'')
        subs += pack_string_subrecord('ANAM', skeleton)
        subs += pack_subrecord('MODT', _MODT)
    for code in _MTNM_CODES:
        subs += pack_subrecord('MTNM', code)
    from ..record_types.actor_common import resolve_actor_voice
    subs += pack_subrecord('VTCK', struct.pack(
        '<II', resolve_actor_voice(rec, 'Male'),
        resolve_actor_voice(rec, 'Female')))
    subs += pack_subrecord('PNAM', struct.pack('<f', 5.0))
    subs += pack_subrecord('UNAM', struct.pack('<f', 3.0))

    # A TOUCH-delivery offensive spell rides the melee attacks as ATKD
    # 'Attack Spell' — the vanilla melee-caster idiom (the flame atronach's
    # four ordinary attack entries each name a fire spell; see
    # creature_touch_attack_spell). Aimed/self spells are NOT attacks: they
    # go through SPLO + the behavior graph's FireForget cast chain.
    touch_spell = creature_touch_attack_spell(race_recs or [rec])
    for event, _clip in proj.get('attacks', []):
        subs += pack_subrecord('ATKD', _atkd(spell=touch_spell))
        subs += pack_string_subrecord('ATKE', event)

    subs += pack_subrecord('NAM1', b'')
    for marker, egt in (('MNAM', _EGT_MALE), ('FNAM', _EGT_FEMALE)):
        subs += pack_subrecord(marker, b'')
        subs += pack_subrecord('INDX', struct.pack('<I', 0))
        subs += pack_string_subrecord('MODL', egt)
        subs += pack_subrecord('MODT', _MODT)
    subs += pack_formid_subrecord('GNAM', _GNAM_BPTD)

    subs += pack_subrecord('NAM3', b'')
    for marker in ('MNAM', 'FNAM'):
        subs += pack_subrecord(marker, b'')
        subs += pack_string_subrecord('MODL', proj['project_hkx'])
        subs += pack_subrecord('MODT', _MODT)

    subs += pack_formid_subrecord('NAM4', _NAM4_IMPACT_MAT)
    subs += pack_formid_subrecord('NAM5', _NAM5_IMPACT_SET)
    subs += pack_formid_subrecord('ONAM', _ONAM_OPEN_SND)
    subs += pack_formid_subrecord('LNAM', _LNAM_CLOSE_SND)
    # Biped object names: vanilla creatures name ONLY slot 2 ('BODY') and ship
    # the whole animal (body+head+eyes+tail) as a single skinned NIF on that
    # one BODY-slot ARMA (census: DogRace names 'BODY' and nothing else).  The
    # creature pipeline merges all Oblivion body parts into one <creature>.nif
    # for exactly this reason, so a single BODY slot is all that's needed.
    for slot in range(32):
        subs += pack_subrecord('NAME', b'BODY\x00' if slot == 2 else b'\x00')
    if vnam_flags is None:
        vnam_flags = _VNAM_BASE | _VNAM_HAND_TO_HAND | _VNAM_SPELL
    subs += pack_subrecord('VNAM', struct.pack('<I', vnam_flags))
    # Equip slots. An unarmed race needs only the right hand (DogRace); a race
    # that may hold a weapon needs somewhere to put it, and the off-hand too
    # for a shield/torch/dual wield — an armed creature whose race named only
    # RightHand still could not equip, because the slot list gates the equip
    # alongside VNAM. Weapon-using vanilla creature races (DraugrRace,
    # SkeletonRace) name RightHand+LeftHand+Voice+Potion, so armed converted
    # creatures get the same four.
    armed = vnam_flags & ~(_VNAM_BASE | _VNAM_HAND_TO_HAND | _VNAM_SPELL)
    if armed:
        slots = [_QNAM_UNARMED, _QNAM_LEFT_HAND, _QNAM_VOICE, _QNAM_POTION]
    elif vnam_flags & _VNAM_SPELL:
        # A caster needs a HAND slot to equip the spell into — this is the
        # gate, not VNAM: every vanilla caster race lists LeftHand (0x13F43)
        # (AtronachFlameRace LeftHand+Potion, HagravenRace Left+Right+Potion,
        # SprigganRace/ChaurusRace/WispRace Left+Right) while the non-caster
        # WolfRace lists RightHand only. Without it the engine never equips
        # the spell (live scamp 2026-08-23: 100 magicka, in combat,
        # HasEquippedSpell 0 in both hands), bWantCast* is never raised and
        # the whole cast handshake is moot.
        slots = [_QNAM_UNARMED, _QNAM_LEFT_HAND]
    else:
        slots = [_QNAM_UNARMED]
    for slot in slots:
        subs += pack_formid_subrecord('QNAM', slot)
    subs += pack_formid_subrecord('UNES', _QNAM_UNARMED)

    writer.add_record('RACE', pack_record('RACE', race_fid, 0, subs))


def _build_movts(writer, folder: str, proj: dict) -> None:
    """Generated MOVT records for one creature project (once per folder).

    The engine gives an actor movement types by matching the behavior
    graph's `iState_<X>` variables against MOVT records with MNAM == <X>
    (vanilla: dogbehavior iState_DogDefault/iState_DogRun ↔ Dog_Default_MT/
    Dog_Run_MT; ck-cmd's RetargetCreature clones MOVTs the same way). A
    graph whose iState_* names have no MOVT records — or a plugin whose
    MOVTs have no graph variables — leaves the actor unable to move AT ALL
    (no AI movement, no `tc` control, no locomotion events: the 2026-07-09
    stuck-in-idle root cause). The names come from the creature pipeline
    manifest so graph and records agree by construction (like ATKE)."""
    names = proj.get('movement_types') or movement_type_names(folder)
    speeds = proj.get('speeds') or {}
    sped = _movt_sped(speeds)
    sped_swim = _movt_sped_swim(speeds)
    for mnam in names:
        subs = pack_string_subrecord('EDID', f'{mnam}_MT')
        subs += pack_string_subrecord('MNAM', mnam)
        # the generated Swim movement type carries the water speeds; the
        # graph switches iState onto it while the engine sets isSwimming
        subs += pack_subrecord('SPED',
                               sped_swim if mnam.endswith('Swim') else sped)
        subs += pack_subrecord('INAM', _MOVT_INAM)
        writer.add_record('MOVT', pack_record(
            'MOVT', writer.derive_formid('MOVT', (folder, mnam)),
                                              0, subs))


def _build_skin(writer, folder: str, bodies: list, race_fid: int,
                skin_fid: int, edid_base: str, body_dir: str) -> None:
    """Single BODY-slot ARMA (the merged whole-animal NIF) + its skin ARMO.

    Vanilla creatures use ONE ARMA on slot BODY (0x4); the creature pipeline
    merges every Oblivion body part into one <creature>.nif so a single ARMA
    covers the whole animal (see merge_creature_body / DogRace census)."""
    body = bodies[0]
    # `bodies` are the source CREA's TES4 NIFZ model paths (authored), not
    # our merged output name, so this key survives changes to mesh merging.
    arma_fid = writer.derive_formid('CREA_ARMA', (folder, tuple(bodies)))
    stem = os.path.splitext(body)[0]
    subs = b''
    subs += pack_string_subrecord('EDID', f'TES4{edid_base}{stem}AA')
    subs += pack_subrecord('BOD2', struct.pack('<II', 0x4, 2))
    subs += pack_formid_subrecord('RNAM', race_fid)
    subs += pack_subrecord('DNAM', _ARMA_DNAM)
    subs += pack_string_subrecord('MOD2', f'{body_dir}\\{body}')
    writer.add_record('ARMA', pack_record('ARMA', arma_fid, 0, subs))
    # Footstep sounds hang off ARMA.SNDD, which is written later (the FSTS it
    # points at is allocated last so it cannot shift other FormIDs) — see
    # creature_footsteps.patch_creature_footsteps.
    _CREA_ARMA_FOLDER[arma_fid] = folder

    subs = b''
    subs += pack_string_subrecord('EDID', f'TES4Skin{edid_base}')
    subs += pack_obnd(0, 0, 0, 0, 0, 0)
    subs += pack_subrecord('BOD2', struct.pack('<II', 0x4, 2))
    subs += pack_formid_subrecord('RNAM', race_fid)
    subs += pack_subrecord('DESC', b'\x00')
    subs += pack_formid_subrecord('MODL', arma_fid)
    subs += pack_subrecord('DATA', struct.pack('<If', 0, 0.0))
    subs += pack_subrecord('DNAM', struct.pack('<f', 0.0))
    # flags=4: non-playable (vanilla SkinDog)
    writer.add_record('ARMO', pack_record('ARMO', skin_fid, 4, subs))


def _race_groups(by_type: dict) -> dict:
    """(folder, bodies) -> every CREA sharing it, in by_type order.

    A generated race is SHARED by every CREA with the same folder and body set,
    so its equipment flags must be the union over all of them — computed in this
    pre-pass because the race is written on the FIRST record it sees, before the
    rest of the group has been walked.
    """
    race_recs = {}
    for rec in by_type.get('CREA', []):
        folder = folder_of(rec)
        proj = _PROJECTS.get(folder)
        if proj is None:
            continue
        bodies = bodies_of(rec, proj)
        if bodies is None:
            continue
        race_recs.setdefault((folder, tuple(bodies)), []).append(rec)
    return race_recs


def _race_is_armed(vnam: int) -> bool:
    """True when equipment flags name a weapon class beyond fists and spells."""
    return bool(vnam & ~(_VNAM_BASE | _VNAM_HAND_TO_HAND | _VNAM_SPELL))


def _build_race_chain(writer, rec, folder: str, bodies: list, proj: dict,
                      key, race_recs: dict) -> tuple:
    """Emit the RACE + skin ARMA/ARMO pair for one (folder, bodies) key.

    Derives both FormIDs from ``key`` before writing, keeping allocation order
    tied to authored data rather than to walk order.  Returns
    ``(race FormID, VNAM equipment flags)``.
    """
    race_fid = writer.derive_formid('CREA_RACE', key)
    skin_fid = writer.derive_formid('CREA_SKIN', key)
    edid = get_str(rec, 'EditorID') or folder
    edid_base = ''.join(c for c in edid if c.isalnum()) or folder
    full = get_str(rec, 'FULL') or edid
    vnam = _creature_equip_flags(race_recs.get(key, [rec]))
    _build_race(writer, rec, folder, bodies, proj,
                race_fid, skin_fid, f'TES4{edid_base}Race', full,
                vnam_flags=vnam,
                race_recs=race_recs.get(key, [rec]))
    _build_skin(writer, folder, bodies, race_fid, skin_fid,
                edid_base, proj['body_dir'])
    return race_fid, vnam


def _index_crea_folders(by_type: dict) -> None:
    """Mesh folder for EVERY CREA, race or not: voice types key off it.

    A creature with no body NIFs (wisps) still needs one, and a plugin with
    no converted projects still gets creature voices.
    """
    for rec in by_type.get('CREA', []):
        model = (get_str(rec, 'Model.MODL') or '').replace('/', '\\')
        parts = [p for p in model.lower().split('\\') if p]
        folder = parts[-2] if len(parts) >= 2 else ''
        if folder:
            _CREA_FOLDER_MAP[get_formid(rec, 'FormID') & 0x00FFFFFF] = folder


def build_creature_races(by_type: dict, writer, export_dir: str,
                         master_export: dict = None) -> None:
    """Phase 0f: one generated RACE + skin ARMO/ARMA per unique
    (creature folder, body-part set) among CREA records with a converted
    project. Populates the crea→race map used by convert_CREA."""
    global _PROJECTS
    _CREA_RACE_MAP.clear()
    _CREA_FOLDER_MAP.clear()
    _CREA_ARMA_FOLDER.clear()
    load_creature_item_index(by_type, master_export)
    _load_magicka_return(by_type, master_export)
    _index_crea_folders(by_type)

    _PROJECTS, owner_slot = load_projects(export_dir)
    if not _PROJECTS:
        print('  Creature projects: none (creature_projects.json missing — '
              'run the creatures step); CREA falls back to race aliasing')
        return

    race_recs = _race_groups(by_type)

    made = {}
    movt_folders = folders_built_by_master(master_export, _PROJECTS,
                                           owner_slot)
    n_races = 0
    n_armed = 0
    for rec in by_type.get('CREA', []):
        folder = folder_of(rec)
        proj = _PROJECTS.get(folder)
        if proj is None:
            continue
        fid = get_formid(rec, 'FormID') & 0x00FFFFFF

        bodies = bodies_of(rec, proj)
        if bodies is None:
            continue

        if folder not in movt_folders:
            _build_movts(writer, folder, proj)
            from .creature_idles import build_creature_idles
            build_creature_idles(writer, folder, proj)
            movt_folders.add(folder)

        key = (folder, tuple(bodies))
        if key not in made:
            made[key], vnam = _build_race_chain(writer, rec, folder, bodies,
                                                proj, key, race_recs)
            n_races += 1
            if _race_is_armed(vnam):
                n_armed += 1
        _CREA_RACE_MAP[fid] = (made[key], folder)

    print(f'  Creature races: {n_races} generated '
          f'({n_armed} weapon-capable, '
          f'{len(_CREA_RACE_MAP)} CREA records mapped, '
          f'{len(_PROJECTS)} converted projects)')
