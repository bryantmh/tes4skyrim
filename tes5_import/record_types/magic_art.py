"""ARTO, PROJ and EXPL companions for magic effects, and the MGEF sound set.

Skyrim keeps a spell's visuals in records Oblivion never had: casting and
hit art are ARTO records, the bolt a PROJ, the area burst an EXPL, and the
sounds an SNDD array on the MGEF.  Each is built from the TES4 MGEF's own
fields -- its Model.MODL, split per phase by asset_convert.nif.magic_art,
its four sounds, its light and projectile speed -- and written once per
authored key, so effects sharing a mesh share the companion.  Values with
no Oblivion source copy vanilla FireboltProjectile01 and FireBallExp01.
"""

import os
import struct

from asset_convert.nif.magic_art import (PHASE_AREA, PHASE_CAST, PHASE_HIT,
                                         PHASE_PROJECTILE, PHASE_SUMMON,
                                         effect_phases, phase_mesh)
from ..base.text_reader import get_float, get_formid, get_str
from ..base.writer import (pack_obnd, pack_record, pack_string_subrecord,
                           pack_subrecord)
from .common import prefix_path
from .sound import master_subrecord, master_sound_descriptor

#: ARTO DNAM art type: 0 Magic Casting, 1 Magic Hit Effect.
ART_CASTING = 0
ART_HIT = 1

#: Oblivion.exe fMagicProjectileBaseSpeed default (no plugin overrides it), times MGEF DATA.ProjectileSpeed.
BOLT_BASE_SPEED = 1000.0
#: Oblivion.exe fMagicProjectileMaxDistance default.
BOLT_RANGE = 10000.0

#: MGEF SNDD sound types (xEdit wbMagicEffectSounds): 3 Release, 5 On Hit.
SOUND_RELEASE = 3
SOUND_ON_HIT = 5

_state = {'writer': None, 'mesh_root': '', 'master_index': None}
#: {model path lowered: phases its source NIF authors}.
_phases: dict = {}
#: {(record type, key): output FormID} of every companion already written.
_companions: dict = {}
#: Output FormIDs of the SOUN records that carry a sound file, and so an SNDR.
_sounding: set = set()
#: EditorIDs of the MGEFs this plugin's own export defines.
_own_effects: set = set()


def begin(writer, mesh_root: str, soun_records, master_index=None,
          own_effects=()) -> None:
    """Start a plugin: its source meshes, its SOUNs with an SNDR, its masters' records and its own MGEFs."""
    _state['writer'] = writer
    _state['mesh_root'] = str(mesh_root)
    _state['master_index'] = master_index
    _phases.clear()
    _companions.clear()
    _sounding.clear()
    _sounding.update(get_formid(r, 'FormID') for r in soun_records
                     if get_str(r, 'FNAM.Filename'))
    _own_effects.clear()
    _own_effects.update(get_str(r, 'EditorID') for r in own_effects)


def _model(rec: dict) -> str:
    """The MGEF's model path, '' for none."""
    return get_str(rec, 'Model.MODL')


def phases_for(model: str) -> tuple:
    """Phases the model's source NIF authors (none when it is not in this plugin)."""
    key = model.lower()
    if key not in _phases:
        path = os.path.join(_state['mesh_root'], model.replace('/', '\\'))
        if model and os.path.isfile(path):
            with open(path, 'rb') as f:
                _phases[key] = effect_phases(f.read())
        else:
            _phases[key] = ()
    return _phases[key]


def _companion(sig: str, key, build) -> int:
    """Write ``build(fid)`` once per (sig, key); its FormID, 0 without a writer."""
    writer = _state['writer']
    if writer is None:
        return 0
    cached = _companions.get((sig, key))
    if cached:
        return cached
    fid = writer.derive_formid(sig, key)
    writer.add_record(sig, build(fid))
    _companions[(sig, key)] = fid
    return fid


def _sndr(rec: dict, key: str) -> int:
    """The SNDR of the SOUN a MGEF field names -- this plugin's or a master's -- 0 when it has none."""
    soun = get_formid(rec, key)
    writer = _state['writer']
    if not soun or writer is None:
        return 0
    if soun in _sounding:
        return writer.derive_formid('SNDR', soun)
    return master_sound_descriptor(_state['master_index'], soun)


def _edid(model: str, what: str) -> str:
    """EditorID stem for a companion of ``model``."""
    return f'TES4{os.path.splitext(os.path.basename(model.replace("/", chr(92))))[0]}{what}'


def _art_object(model: str, phase: str, art_type: int) -> int:
    """The ARTO showing one phase of ``model``; 0 when the model lacks it."""
    if phase not in phases_for(model):
        return 0

    def build(fid):
        """The packed ARTO: EDID OBND MODL DNAM."""
        subs = pack_string_subrecord('EDID', _edid(model, f'{phase}Art'))
        subs += pack_obnd()
        subs += pack_string_subrecord('MODL', prefix_path(phase_mesh(model, phase)))
        subs += pack_subrecord('DNAM', struct.pack('<I', art_type))
        return pack_record('ARTO', fid, 0, subs)
    return _companion('ARTO', (model.lower(), phase), build)


def casting_art(rec: dict) -> int:
    """Casting-art ARTO from the model's Cast phase; 0 without one."""
    return _art_object(_model(rec), PHASE_CAST, ART_CASTING)


def hit_art(rec: dict) -> int:
    """Hit-effect ARTO: the HitEffect phase, else the SummonEffect one."""
    model = _model(rec)
    return (_art_object(model, PHASE_HIT, ART_HIT)
            or _art_object(model, PHASE_SUMMON, ART_HIT))


def projectile(rec: dict) -> int:
    """PROJ flying the model's own Projectile phase; 0 when it has none.

    DATA per xEdit wbRecord PROJ (92 bytes): Missile type, the TES4 speed,
    range, light and bolt sound; fade 0.5, impact force 1.5, collision radius
    10 and relaunch 0.25 as vanilla FireboltProjectile01 writes them.
    """
    model = _model(rec)
    if PHASE_PROJECTILE not in phases_for(model):
        return 0
    speed = BOLT_BASE_SPEED * (get_float(rec, 'DATA.ProjectileSpeed', 1.0) or 1.0)
    light, sound = get_formid(rec, 'DATA.Light'), _sndr(rec, 'DATA.BoltSound')

    def build(fid):
        """The packed PROJ: EDID OBND MODL DATA NAM1 VNAM."""
        data = bytearray(92)
        struct.pack_into('<HHfffI', data, 0, 0, 1, 0.0, speed, BOLT_RANGE, light)
        struct.pack_into('<I', data, 40, sound)
        struct.pack_into('<ff', data, 48, 0.5, 1.5)
        struct.pack_into('<f', data, 72, 10.0)
        struct.pack_into('<f', data, 80, 0.25)
        subs = pack_string_subrecord('EDID', _edid(model, f'Projectile{fid & 0xFFFFFF:06X}'))
        subs += pack_obnd()
        subs += pack_string_subrecord('MODL', prefix_path(phase_mesh(model, PHASE_PROJECTILE)))
        subs += pack_subrecord('DATA', bytes(data))
        subs += pack_string_subrecord('NAM1', '')
        subs += pack_subrecord('VNAM', struct.pack('<I', 1))
        return pack_record('PROJ', fid, 0, subs)
    return _companion('PROJ', (model.lower(), speed, light, sound), build)


def explosion(rec: dict) -> int:
    """EXPL playing the model's AreaEffect phase; 0 when it has none.

    Visual only, as Skyrim applies an area effect through the effect's own
    area: vanilla FireBallExp01's DATA with no force, since Oblivion spells
    push nothing.
    """
    model = _model(rec)
    if PHASE_AREA not in phases_for(model):
        return 0
    sound = _sndr(rec, 'DATA.AreaSound')

    def build(fid):
        """The packed EXPL: EDID OBND MODL DATA (52 bytes)."""
        subs = pack_string_subrecord('EDID', _edid(model, f'Explosion{fid & 0xFFFFFF:06X}'))
        subs += pack_obnd()
        subs += pack_string_subrecord('MODL', prefix_path(phase_mesh(model, PHASE_AREA)))
        subs += pack_subrecord('DATA', struct.pack(
            '<6I5f2I', 0, sound, 0, 0, 0, 0, 0.0, 0.0, 320.0, 1024.0, 0.0,
            0x41, 1))
        return pack_record('EXPL', fid, 0, subs)
    return _companion('EXPL', (model.lower(), sound), build)


def _master_effect(rec: dict) -> int:
    """This plugin's id for the master's converted MGEF `rec` names; 0 for one of its own.

    A master's export names its SOUNs in the master's own FormID space and
    its meshes live in the master's asset tree, so neither can be resolved
    here; the master's converted record already has both.
    See: docs/commentary/tes5_import_magic.md#master-effects
    """
    code = get_str(rec, 'EditorID')
    index = _state['master_index']
    if not index or not code or code in _own_effects:
        return 0
    return index.find_by_edid(b'MGEF', code)


def master_signature(fid: int) -> str:
    """The converted record type of a master's record, '' when no master holds `fid`."""
    index = _state['master_index']
    return index.signature(fid).decode('ascii', 'replace') if index and fid else ''


def master_effect_data(rec: dict):
    """A master effect's converted DATA in this plugin's ids; None if own."""
    fid = _master_effect(rec)
    return master_subrecord(_state['master_index'], fid, b'MGEF', b'DATA') if fid else None


def _master_sound_set(rec: dict):
    """The SNDD a master's conversion wrote for this effect; None when no master has it."""
    fid = _master_effect(rec)
    if not fid:
        return None
    sounds = master_subrecord(_state['master_index'], fid, b'MGEF', b'SNDD')
    return pack_subrecord('SNDD', sounds) if sounds else b''


def sound_set(rec: dict) -> bytes:
    """The MGEF's SNDD, type-sorted: the cast sound on Release, the hit sound On Hit.

    A master's effect keeps the sound set the master's conversion wrote.
    """
    inherited = _master_sound_set(rec)
    if inherited is not None:
        return inherited
    entries = [struct.pack('<II', kind, sndr)
               for kind, key in ((SOUND_RELEASE, 'DATA.CastingSound'),
                                 (SOUND_ON_HIT, 'DATA.HitSound'))
               if (sndr := _sndr(rec, key))]
    return pack_subrecord('SNDD', b''.join(entries)) if entries else b''
