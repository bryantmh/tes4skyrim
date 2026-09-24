"""Per-phase magic-effect meshes.

An Oblivion MGEF model packs every phase of a spell into one NIF as named
NiControllerSequences (`SpecialIdle_Cast`, `_Projectile`, `_AreaEffect`,
`_HitEffect`, `_SummonEffect`).  Skyrim keeps one mesh per job -- the casting
ARTO, the PROJ, the EXPL, the hit ARTO -- each driven by a stock behavior
graph (or, for an explosion, by the engine playing `SpecialIdle_AreaEffect`
by name).  This module derives one mesh per authored phase from the
converted NIF, keeping only that phase's sequence under the names its
driver expects.  Every Oblivion phase sequence drives every emitter in the
file, the other phases' emitters to silence, so one sequence is the whole
job.

Oblivion shows nothing in the hand before the release, and Skyrim's casting
graph plays idle, charge and ready loops there.  Those are built from the
Cast phase's own emitters held on steadily, at the rates vanilla hand art
uses relative to its ready loop (medians over 11 vanilla hand meshes).
"""

import io
import os
import re
import struct

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.nif.nif_passes import add_animobject_bged
from asset_convert.nif.sequences import CYCLE_CLAMP, CYCLE_LOOP, clone_sequence_as

PHASE_CAST = 'Cast'
PHASE_PROJECTILE = 'Projectile'
PHASE_AREA = 'AreaEffect'
PHASE_HIT = 'HitEffect'
PHASE_SUMMON = 'SummonEffect'

#: Oblivion names every phase sequence `SpecialIdle_<phase>`.
SEQUENCE_PREFIX = 'SpecialIdle_'

#: Vanilla casting-art graph: mIdle/mReady/mIdleStaff loop, mIntro/mCharge/mCast play once.
GRAPH_CASTING = 'Magic\\CastingBasic.hkx'
#: Vanilla one-shot hit art and looping projectiles both play `mIdle` under this graph.
GRAPH_IDLE_ON_LOAD = 'Magic\\IdleOnLoad.hkx'

#: Hand loops from the Cast emitters: (name, cycle, birth-rate share of the authored cast rate).
HAND_LOOPS = (('mIntro', CYCLE_CLAMP, 0.23), ('mIdle', CYCLE_LOOP, 0.5),
              ('mCharge', CYCLE_CLAMP, 0.8), ('mReady', CYCLE_LOOP, 1.0),
              ('mIdleStaff', CYCLE_LOOP, 0.23))

#: phase -> (file suffix, graph or '', name the phase sequence is renamed to or None).
PHASE_SPECS = {
    PHASE_CAST: ('cast', GRAPH_CASTING, 'mCast'),
    PHASE_HIT: ('hit', GRAPH_IDLE_ON_LOAD, 'mIdle'),
    PHASE_SUMMON: ('summon', GRAPH_IDLE_ON_LOAD, 'mIdle'),
    PHASE_AREA: ('area', '', None),
    PHASE_PROJECTILE: ('projectile', GRAPH_IDLE_ON_LOAD, 'mIdle'),
}

_SEQUENCE_NAME = re.compile(rb'SpecialIdle_([A-Za-z]+)')
_MODEL_LINE = re.compile(r'^Model\.MODL=(.+)$', re.M)
_UNSET_FLOAT = -3.0e38


# ---------------------------------------------------------------------------
# Which phases a model authors, and where each phase mesh goes
# ---------------------------------------------------------------------------

def effect_phases(nif_bytes: bytes) -> tuple:
    """Phases authored in a NIF, in PHASE_SPECS order.

    A sequence name is length-prefixed in both the Oblivion (inline string)
    and Skyrim (header string table) layouts, so one test reads either side.
    """
    found = set()
    for m in _SEQUENCE_NAME.finditer(nif_bytes):
        if m.start() >= 4 and struct.unpack_from('<I', nif_bytes, m.start() - 4)[0] == m.end() - m.start():
            found.add(m.group(1).decode('ascii'))
    return tuple(p for p in PHASE_SPECS if p in found)


def phase_mesh(model: str, phase: str) -> str:
    """A phase mesh's path relative to the converted tree: `<model stem>_<suffix>.nif`."""
    stem, _ = os.path.splitext(model.replace('/', '\\').lower())
    return f'{stem}_{PHASE_SPECS[phase][0]}.nif'


def effect_models(rec_dir) -> list:
    """Distinct MGEF model paths a plugin's export names, sorted."""
    path = os.path.join(str(rec_dir), 'MGEF.txt')
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8', errors='replace') as f:
        models = {m.strip().replace('\\\\', '\\') for m in _MODEL_LINE.findall(f.read())}
    return sorted(m for m in models if m)


# ---------------------------------------------------------------------------
# Building one phase mesh
# ---------------------------------------------------------------------------

def _read(path):
    """A fully read NifFormat.Data for `path`."""
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.inspect(f)
        data.read(f)
    return data


def _manager(data):
    """(root, its NiControllerManager), or (None, None) for a static mesh."""
    for root in data.roots:
        for block in root.tree():
            if isinstance(block, NifFormat.NiControllerManager):
                return root, block
    return None, None


def _sequence_named(mgr, name: str):
    """The manager's sequence called `name`, or None."""
    for seq in mgr.controller_sequences:
        if seq is not None and bytes(seq.name) == name.encode('latin-1'):
            return seq
    return None


def _keep_only(mgr, seq) -> None:
    """Leave `seq` as the manager's only sequence."""
    mgr.num_controller_sequences = 1
    mgr.controller_sequences.update_size()
    mgr.controller_sequences[0] = seq


def _peak(interp) -> float:
    """The highest value a float interpolator reaches."""
    data = getattr(interp, 'data', None)
    if data is not None and data.data.keys:
        return max(k.value for k in data.data.keys)
    return 0.0 if interp.float_value < _UNSET_FLOAT else interp.float_value


def _ever_on(interp) -> bool:
    """Whether a bool interpolator is true at any time."""
    data = getattr(interp, 'data', None)
    if data is not None and data.data.keys:
        return any(k.value for k in data.data.keys)
    return interp.bool_value == 1


def _steady_interpolator(interp, share: float):
    """A constant interpolator holding an emitter at `share` of its peak rate, or on."""
    if isinstance(interp, NifFormat.NiFloatInterpolator):
        steady = NifFormat.NiFloatInterpolator()
        steady.float_value = _peak(interp) * share
        return steady
    if isinstance(interp, NifFormat.NiBoolInterpolator):
        steady = NifFormat.NiBoolInterpolator()
        steady.bool_value = 1 if _ever_on(interp) else 0
        return steady
    return interp


def _add_steady_loop(root, cast, name: str, cycle: int, share: float) -> None:
    """Clone the Cast sequence as `name`, its emitters held steadily on."""
    loop = clone_sequence_as(root, cast, name, cycle)
    if loop is None:
        return
    for block in loop.controlled_blocks:
        if isinstance(block.controller, NifFormat.NiPSysEmitterCtlr):
            block.interpolator = _steady_interpolator(block.interpolator, share)


def shape_phase(data, phase: str) -> bool:
    """Reduce a converted effect mesh to one phase, named for its driver; False if absent."""
    root, mgr = _manager(data)
    seq = _sequence_named(mgr, SEQUENCE_PREFIX + phase) if mgr is not None else None
    if seq is None:
        return False
    _keep_only(mgr, seq)
    _, graph, name = PHASE_SPECS[phase]
    if name:
        seq.name = name.encode('latin-1')
    if phase == PHASE_CAST:
        for loop_name, cycle, share in HAND_LOOPS:
            _add_steady_loop(root, seq, loop_name, cycle, share)
    if graph:
        add_animobject_bged(data, graph)
    return True


def split_effect_mesh(converted_path: str, phases) -> list:
    """Write one phase mesh per phase beside `converted_path`; the paths written."""
    written = []
    stem, _ = os.path.splitext(str(converted_path))
    for phase in phases:
        data = _read(converted_path)
        if not shape_phase(data, phase):
            continue
        buf = io.BytesIO()
        data.write(buf)
        out = f'{stem}_{PHASE_SPECS[phase][0]}.nif'
        with open(out, 'wb') as f:
            f.write(buf.getvalue())
        written.append(out)
    return written


def split_effect_meshes(rec_dir, src_meshes, dst_meshes) -> dict:
    """Derive every phase mesh for the MGEF models `rec_dir` names.

    Phases are read from the SOURCE model (`src_meshes`), the mesh the
    import reads too; the phase files go beside the converted model in
    `dst_meshes`.  Returns {'sources', 'written', 'missing'} counts.
    """
    stats = {'sources': 0, 'written': 0, 'missing': 0}
    for model in effect_models(rec_dir):
        rel = model.replace('/', '\\')
        src = os.path.join(str(src_meshes), rel)
        dst = os.path.join(str(dst_meshes), rel.lower())
        if not (os.path.isfile(src) and os.path.isfile(dst)):
            stats['missing'] += 1
            continue
        with open(src, 'rb') as f:
            phases = effect_phases(f.read())
        if phases:
            stats['sources'] += 1
            stats['written'] += len(split_effect_mesh(dst, phases))
    return stats
