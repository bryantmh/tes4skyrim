"""Generated creature RACE / ARMA / ARMO(skin) records.

Every CREA whose model folder was converted by the creature pipeline
(asset_convert/creature_pipeline.py — see export/<plugin>/creature_projects.json)
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

Deliberate v1 simplifications (documented in docs/creature_conversion.md):
GNAM points at the vanilla canine body-part data (bone names won't match
the Oblivion skeleton — hits still register, dismember targeting is off),
and ARMA has no footstep SNDD yet.
"""

import json
import os
import struct

from .writer import (pack_record, pack_subrecord, pack_string_subrecord,
                     pack_formid_subrecord, pack_obnd)
from .text_reader import get_formid, get_int, get_str

# ---------------------------------------------------------------------------
# Vanilla template constants (Skyrim.esm DogRace chain, byte-verified)
# ---------------------------------------------------------------------------

_RACE_DATA_TEMPLATE = bytes.fromhex(
    '0f23ff00ff00ff00ff00ff00ff0000000000803f0000803f0000803f0000803f'
    '4889300000002041000000000000a041000048430000803f0000803f0000803f'
    '01000000ffffffffffffffff00000000ffffffff000000000000000000002041'
    '0000004100008042ffffffff00000000000000000000803e0000a04000000000'
    '7fea7dc20000000000000000000048c2000000000000824200000000000096c3'
    '00000000')
_MODT = bytes.fromhex('020000000000000000000000')
_ARMA_DNAM = bytes.fromhex('000000000000001100000000')
_MTNM_CODES = (b'WALK', b'RUN1', b'SNEK', b'BLDO', b'SWIM')
_EGT_MALE = 'Actors\\Character\\UpperBodyHumanMale.egt'
_EGT_FEMALE = 'Actors\\Character\\UpperBodyHumanFemale.egt'
_GNAM_BPTD = 0x0004FBF5      # canine body part data (see module docstring)
_NAM4_IMPACT_MAT = 0x0005A28F
_NAM5_IMPACT_SET = 0x000A956F
_ONAM_OPEN_SND = 0x000A5013
_LNAM_CLOSE_SND = 0x000A5014
_VNAM_EQUIP_FLAGS = 0xFFFFE001
_QNAM_UNARMED = 0x00013F42
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

# Extra ARMA biped slots for body parts beyond the first (bits 10.. = slots
# 40..46, the creature-safe range). Part 0 always gets slot 32 (Body, 0x4).
_EXTRA_SLOT_BITS = [1 << (10 + i) for i in range(14)]

# crea_fid_low24 → (race_fid, folder) — consumed by convert_CREA
_CREA_RACE_MAP = {}
# folder → project summary (attacks etc.) for anything else that needs it
_PROJECTS = {}


def get_creature_race(fid_low24: int):
    """Generated race FormID for a converted CREA, or None (→ fallback to
    resolve_creature_race aliasing)."""
    entry = _CREA_RACE_MAP.get(fid_low24)
    return entry[0] if entry else None


def _race_data(rec: dict) -> bytes:
    """The 164-byte RACE DATA: DogRace template with CREA stat patches."""
    data = bytearray(_RACE_DATA_TEMPLATE)
    struct.pack_into('<f', data, 36, float(get_int(rec, 'DATA.Health', 50)))
    struct.pack_into('<f', data, 40,
                     float(get_int(rec, 'ACBS.SpellPoints', 0)))
    struct.pack_into('<f', data, 44, float(get_int(rec, 'ACBS.Fatigue', 100)))
    struct.pack_into('<f', data, 96,
                     float(max(1, get_int(rec, 'DATA.AttackDamage', 5))))
    reach = get_int(rec, 'RNAM.AttackReach', 64) or 64
    struct.pack_into('<f', data, 100, float(reach))
    return bytes(data)


def _atkd(damage_mult: float = 1.0) -> bytes:
    """44-byte attack data: vanilla dog Attack1 values."""
    return struct.pack('<ffIIfffIfff',
                       damage_mult, 1.0, 0, 0, 0.0, 35.0, 0.75, 0, 0.0, 0.0,
                       1.0)


def _build_race(writer, rec, folder: str, bodies: list, proj: dict,
                race_fid: int, skin_fid: int, edid: str, full: str) -> None:
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
    subs += pack_subrecord('DATA', _race_data(rec))

    skeleton = proj['skeleton_nif']
    for marker in ('MNAM', 'FNAM'):
        subs += pack_subrecord(marker, b'')
        subs += pack_string_subrecord('ANAM', skeleton)
        subs += pack_subrecord('MODT', _MODT)
    for code in _MTNM_CODES:
        subs += pack_subrecord('MTNM', code)
    subs += pack_subrecord('VTCK', struct.pack('<II', 0, 0))
    subs += pack_subrecord('PNAM', struct.pack('<f', 5.0))
    subs += pack_subrecord('UNAM', struct.pack('<f', 3.0))

    for event, _clip in proj.get('attacks', []):
        subs += pack_subrecord('ATKD', _atkd())
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
    for slot in range(32):
        subs += pack_subrecord('NAME', b'BODY\x00' if slot == 2 else b'\x00')
    subs += pack_subrecord('VNAM', struct.pack('<I', _VNAM_EQUIP_FLAGS))
    subs += pack_formid_subrecord('QNAM', _QNAM_UNARMED)
    subs += pack_formid_subrecord('UNES', _QNAM_UNARMED)

    writer.add_record('RACE', pack_record('RACE', race_fid, 0, subs))


def _build_skin(writer, folder: str, bodies: list, race_fid: int,
                skin_fid: int, edid_base: str) -> None:
    """ARMA per body-part NIF + the skin ARMO referencing them all."""
    arma_fids = []
    slot_union = 0
    for i, body in enumerate(bodies):
        slot = 0x4 if i == 0 else _EXTRA_SLOT_BITS[min(i - 1,
                                                       len(_EXTRA_SLOT_BITS) - 1)]
        slot_union |= slot
        arma_fid = writer.alloc_formid()
        stem = os.path.splitext(body)[0]
        subs = b''
        subs += pack_string_subrecord('EDID', f'TES4{edid_base}{stem}AA')
        subs += pack_subrecord('BOD2', struct.pack('<II', slot, 2))
        subs += pack_formid_subrecord('RNAM', race_fid)
        subs += pack_subrecord('DNAM', _ARMA_DNAM)
        subs += pack_string_subrecord(
            'MOD2', f'Actors\\TES4\\{folder}\\{body}')
        writer.add_record('ARMA', pack_record('ARMA', arma_fid, 0, subs))
        arma_fids.append(arma_fid)

    subs = b''
    subs += pack_string_subrecord('EDID', f'TES4Skin{edid_base}')
    subs += pack_obnd(0, 0, 0, 0, 0, 0)
    subs += pack_subrecord('BOD2', struct.pack('<II', slot_union, 2))
    subs += pack_formid_subrecord('RNAM', race_fid)
    subs += pack_subrecord('DESC', b'\x00')
    for arma_fid in arma_fids:
        subs += pack_formid_subrecord('MODL', arma_fid)
    subs += pack_subrecord('DATA', struct.pack('<If', 0, 0.0))
    subs += pack_subrecord('DNAM', struct.pack('<f', 0.0))
    # flags=4: non-playable (vanilla SkinDog)
    writer.add_record('ARMO', pack_record('ARMO', skin_fid, 4, subs))


def build_creature_races(by_type: dict, writer, export_dir: str) -> None:
    """Phase 0f: one generated RACE + skin ARMO/ARMA per unique
    (creature folder, body-part set) among CREA records with a converted
    project. Populates the crea→race map used by convert_CREA."""
    global _PROJECTS
    _CREA_RACE_MAP.clear()

    proj_path = os.path.join(export_dir, 'creature_projects.json')
    if not os.path.exists(proj_path):
        print('  Creature projects: none (creature_projects.json missing — '
              'run the creatures step); CREA falls back to race aliasing')
        return
    with open(proj_path, encoding='utf-8') as f:
        _PROJECTS = json.load(f)

    made = {}
    n_races = 0
    for rec in by_type.get('CREA', []):
        model = (get_str(rec, 'Model.MODL') or '').replace('/', '\\')
        parts = [p for p in model.lower().split('\\') if p]
        # "Creatures\Dog\Skeleton.NIF" → folder "dog"
        folder = parts[-2] if len(parts) >= 2 else ''
        proj = _PROJECTS.get(folder)
        if proj is None:
            continue
        fid = get_formid(rec, 'FormID') & 0x00FFFFFF

        nifz = []
        for i in range(get_int(rec, 'NIFZCount', 0)):
            fn = (get_str(rec, f'NIFZ[{i}]') or '').lower()
            if fn in proj['bodies']:
                nifz.append(fn)
        bodies = sorted(set(nifz)) or list(proj['bodies'])

        key = (folder, tuple(bodies))
        if key not in made:
            race_fid = writer.alloc_formid()
            skin_fid = writer.alloc_formid()
            edid = get_str(rec, 'EditorID') or folder
            edid_base = ''.join(c for c in edid if c.isalnum()) or folder
            full = get_str(rec, 'FULL') or edid
            _build_race(writer, rec, folder, bodies, proj,
                        race_fid, skin_fid, f'TES4{edid_base}Race', full)
            _build_skin(writer, folder, bodies, race_fid, skin_fid,
                        edid_base)
            made[key] = race_fid
            n_races += 1
        _CREA_RACE_MAP[fid] = (made[key], folder)

    print(f'  Creature races: {n_races} generated '
          f'({len(_CREA_RACE_MAP)} CREA records mapped, '
          f'{len(_PROJECTS)} converted projects)')
