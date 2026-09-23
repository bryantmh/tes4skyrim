"""FO3/FNV projectiles and gun sounds: PROJ records, the AMMO's projectile,
and the WEAP's shoot/dry-fire/idle sound links.

A gun names its projectile on `DNAM.Projectile`, its rounds on `NAM0`, and
its sounds on `SNAM`/`XNAM`/`NAM7`/`TNAM`/`UNAM`; the AMMO's own `DAT2`
projectile is usually null. Skyrim launches the AMMO's projectile, so each
FNV PROJ converts to a TES5 PROJ and every AMMO is pointed at the PROJ the
guns firing it name.
See: docs/commentary/tes4_export_falloutnv.md#projectiles
"""

import struct
from collections import Counter

from ..base.writer import (pack_formid_subrecord, pack_obnd, pack_record,
                      pack_string_subrecord, pack_subrecord)
from .common import get_float, get_formid, get_int, get_str, prefix_path

#: FNV PROJ DATA type Continuous Beam, which TES5 lacks -> TES5 Beam (0x04).
_FNV_TYPE_CONTINUOUS_BEAM = 0x10
#: PROJ flags kept, never Hitscan. See: docs/commentary/tes4_export_falloutnv.md#no-hitscan
_SHARED_FLAGS = 0x02 | 0x04 | 0x08 | 0x20 | 0x40 | 0x80 | 0x100 | 0x200
#: FNV PROJ DATA flag bit 0: the shot is instant, so it flies straight (no gravity).
_FNV_FLAG_HITSCAN = 0x01
#: TES5 WEAP sound subrecords a FNV gun fills, in TES5 order (all SNDR links).
GUN_SOUND_SIGS = ('SNAM', 'XNAM', 'NAM7', 'TNAM', 'UNAM')

#: AMMO FormID (low 24 bits) -> the PROJ its guns name most, built per run.
_AMMO_PROJECTILE = {}
#: FLST FormID (low 24 bits) -> member FormIDs, built per run.
_FORMLISTS = {}


def _records(by_type: dict, master_export: dict, sig: str):
    """The plugin's records of `sig`, then its masters'."""
    yield from by_type.get(sig, [])
    if master_export:
        yield from (r for r in master_export.values()
                    if r.get('Signature') == sig)


def _formlist_members(by_type: dict, master_export: dict) -> dict:
    """{FLST FormID low 24 bits: [member FormIDs]} over plugin and masters."""
    out = {}
    for rec in _records(by_type, master_export, 'FLST'):
        members = []
        while f'LNAM[{len(members)}]' in rec:
            members.append(get_formid(rec, f'LNAM[{len(members)}]'))
        out[get_formid(rec, 'FormID') & 0xFFFFFF] = members
    return out


def index_gun_projectiles(by_type: dict, master_export: dict = None) -> int:
    """Map every ammo to the projectile the guns loading it fire most often.

    A gun's `NAM0` is an AMMO or a FLST of AMMOs; a list votes for each
    member. Reads the plugin's WEAPs and its masters'. Returns the number
    of ammo types indexed.
    See: docs/commentary/tes4_export_falloutnv.md#formlists
    """
    _AMMO_PROJECTILE.clear()
    _FORMLISTS.clear()
    _FORMLISTS.update(_formlist_members(by_type, master_export))
    votes = {}
    for rec in _records(by_type, master_export, 'WEAP'):
        proj = get_formid(rec, 'DNAM.Projectile')
        for member in gun_ammo(rec) if proj else ():
            votes.setdefault(member & 0xFFFFFF, Counter())[proj] += 1
    for ammo, c in votes.items():
        _AMMO_PROJECTILE[ammo] = c.most_common(1)[0][0]
    return len(_AMMO_PROJECTILE)


def gun_ammo(rec: dict) -> list:
    """The AMMO FormIDs a FNV gun loads: its `NAM0`, expanded when a FLST.
    See: docs/commentary/tes4_export_falloutnv.md#formlists
    """
    ammo = get_formid(rec, 'NAM0')
    if not ammo:
        return []
    return _FORMLISTS.get(ammo & 0xFFFFFF, [ammo])


def ammo_projectile(rec: dict) -> int:
    """The TES5 projectile of a FNV AMMO: its DAT2 one, else its guns', else 0."""
    own = get_formid(rec, 'DAT2.Projectile')
    if own:
        return own
    return _AMMO_PROJECTILE.get(get_formid(rec, 'FormID') & 0xFFFFFF, 0)


def gun_sound_subs(rec: dict, writer) -> bytes:
    """SNAM/XNAM/NAM7/TNAM/UNAM for a FNV gun, as its SOUNs' companion SNDRs."""
    subs = b''
    for sig in GUN_SOUND_SIGS:
        soun = get_formid(rec, sig)
        if soun:
            subs += pack_formid_subrecord(sig, writer.derive_formid('SNDR', soun))
    return subs


def gun_sheathe_sounds(rec: dict, writer) -> tuple:
    """(draw SNDR, sheathe SNDR) from the gun's NAM9/NAM8, 0 where unset."""
    return tuple(writer.derive_formid('SNDR', fid) if fid else 0
                 for fid in (get_formid(rec, 'NAM9'), get_formid(rec, 'NAM8')))


def convert_PROJ(rec: dict, writer=None) -> bytes:
    """A FNV PROJ as a TES5 PROJ of the same type and flags with the FNV
    flight data (a bullet is a Missile: consumed on contact).

    TES5 DATA (92 bytes, wbDefinitionsTES5): 0 flags u16, 2 type u16,
    4 gravity, 8 speed, 12 range, 16 light, 20 muzzle flash light, 24 tracer
    chance, 28/32 alt-trigger proximity/timer, 36 explosion, 40 sound,
    44 muzzle flash duration, 48 fade, 52 impact force, 72 collision radius,
    80 relaunch interval.
    See: docs/commentary/tes4_export_falloutnv.md#formlists
    """
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID'))
    subs += pack_obnd(*(get_int(rec, f'OBND.{k}') for k in
                        ('X1', 'Y1', 'Z1', 'X2', 'Y2', 'Z2')))
    if get_str(rec, 'FULL'):
        subs += pack_string_subrecord('FULL', get_str(rec, 'FULL'))
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))
    fnv_flags = get_int(rec, 'DATA.Flags')
    gravity = (0.0 if fnv_flags & _FNV_FLAG_HITSCAN
               else get_float(rec, 'DATA.Gravity'))
    ptype = get_int(rec, 'DATA.Type', 1)
    if ptype == _FNV_TYPE_CONTINUOUS_BEAM:
        ptype = 0x04
    data = bytearray(92)
    struct.pack_into('<HH', data, 0, fnv_flags & _SHARED_FLAGS, ptype)
    struct.pack_into('<fff', data, 4, gravity,
                     get_float(rec, 'DATA.Speed', 3600.0),
                     max(get_float(rec, 'DATA.Range'), 60000.0))
    struct.pack_into('<II', data, 16, get_formid(rec, 'DATA.Light'), 0)
    struct.pack_into('<fff', data, 24, get_float(rec, 'DATA.TracerChance'),
                     get_float(rec, 'DATA.AltTriggerProximity'),
                     get_float(rec, 'DATA.AltTriggerTimer'))
    struct.pack_into('<II', data, 36, get_formid(rec, 'DATA.Explosion'),
                     sndr_of(writer, get_formid(rec, 'DATA.Sound')))
    struct.pack_into('<fff', data, 44,
                     get_float(rec, 'DATA.MuzzleFlashDuration'),
                     get_float(rec, 'DATA.FadeDuration', 0.5),
                     get_float(rec, 'DATA.ImpactForce', 1.0))
    struct.pack_into('<f', data, 72, 0.5)
    struct.pack_into('<f', data, 80, 0.25)
    subs += pack_subrecord('DATA', bytes(data))
    subs += pack_string_subrecord('NAM1', prefix_path(get_str(rec, 'NAM1')))
    subs += pack_subrecord('VNAM', struct.pack('<I', get_int(rec, 'VNAM', 1)))
    return pack_record('PROJ', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


def sndr_of(writer, soun: int) -> int:
    """The companion SNDR of a SOUN link, 0 for none."""
    return writer.derive_formid('SNDR', soun) if soun and writer else 0
