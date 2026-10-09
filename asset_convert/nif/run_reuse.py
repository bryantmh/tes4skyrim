"""Reuse successful NIF conversions with matching inputs and output context.

The latest result at a destination wins: a different plugin context must
convert again, even if an older result for that context was cached before.
"""
import dataclasses
import hashlib
import json
import os
from pathlib import Path

from asset_convert.collision import resting_items_plan
from asset_convert.character import wearable_plan as wp
from asset_convert.collision.clutter_plan import CLUTTER_KEY
from asset_convert.nif.door_plan import DOOR_KEY
from asset_convert.nif.fixture_plan import FIXTURE_KEY, ANIMATED_KEY
from asset_convert.game_paths import current_namespace
from core.collision_options import winding_fix_enabled


def _normal(value):
    if dataclasses.is_dataclass(value):
        return _normal(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _normal(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_normal(v) for v in value), key=repr)
    if isinstance(value, (tuple, list)):
        return [_normal(v) for v in value]
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(_normal(value), sort_keys=True).encode()).hexdigest()


def _stamp(path):
    s = Path(path).stat()
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns]


class RunReuse:
    def __init__(self, storage_dir, token, plan, options):
        self.path = Path(storage_dir) / '.mod-mesh-reuse.json'
        self.token = token
        self.entries = {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('token') == token:
                self.entries = data['entries']
        except (OSError, ValueError, KeyError):
            pass
        self.plan = plan or {}
        self.mesh_root = Path(options[2])
        self.texture_roots = [self.mesh_root.parent / 'textures', *options[3]]
        self.resting = resting_items_plan._load(self.plan.get(resting_items_plan.RESTING_KEY))
        # Process supervision changes between plugin invocations without
        # changing the converted mesh (the lock owner is a process ID).
        operational = {'TESCONV_RUN_LOG', 'TESCONV_LOGS_DIR', 'TESCONV_WORKERS',
                       'TESCONV_HEAVY_LOCK_HELD', 'TESCONV_HEAVY_LOCK_SUPERVISED',
                       'TESCONV_JOB', 'TESCONV_JOB_MEM', 'TESCONV_JOB_MEM_GB'}
        env = {k: v for k, v in os.environ.items()
               if k.startswith('TESCONV_') and k not in operational}
        # Pool initialization exports the resolved namespace to the environment.
        # It must not make the first batch's context differ from the next one.
        env['TESCONV_ASSET_NAMESPACE'] = current_namespace()
        self.context = _digest([options, current_namespace(),
                                winding_fix_enabled(), env])
        if token == 'imported-mod':
            from asset_convert.sources.shared_reuse import implementation_stamp, stamps
            dependencies = [self.mesh_root.parent / 'textures', *options[3]]
            self.context = _digest([self.context, implementation_stamp(),
                                    stamps(dependencies)])

    def mesh_context(self, source):
        rel = wp.norm_model_path(source.relative_to(self.mesh_root).as_posix())
        context = {k: v for k, v in self.plan.items() if k.startswith('*')}
        context['variants'] = self.plan.get(rel, wp.BASE) if self.plan else None
        for key in (wp.BIPED_FLAGS_KEY, wp.WEAPON_PRN_KEY, wp.SKIN_FILL_KEY,
                    wp.SIDED_SLOT_KEY, CLUTTER_KEY):
            context[key] = self.plan.get(key, {}).get(rel)
        context[wp.BIPED_FLAGS_KEY] = wp.biped_flags_for(self.plan, source, self.mesh_root)
        for key in (DOOR_KEY, FIXTURE_KEY, ANIMATED_KEY):
            context[key] = rel in self.plan.get(key, ())
        # Only the placement data this mesh reads matters, not the index path
        # or unrelated models added by another plugin.
        resting = None
        entry = self.resting['models'].get(rel) if self.resting else None
        if entry is not None and context[FIXTURE_KEY]:
            cells, placements = entry
            resting = [cells, placements.tolist(),
                       [self.resting['cells'][cell].tolist() for cell in cells]]
        context[resting_items_plan.RESTING_KEY] = resting
        return context

    def key(self, source):
        # Animation sidecars can affect fixture output too.
        sidecars = {p.name: _stamp(p) for p in source.parent.iterdir()
                    if p.is_file() and p.suffix.lower() == '.kf'}
        return _digest([self.context, self.mesh_context(source), str(source.resolve()),
                        _stamp(source), sidecars])

    def take(self, source, destination):
        name = str(destination.resolve())
        entry = self.entries.get(name)
        if entry and entry['key'] == self.key(source):
            try:
                if all(_stamp(p) == stamp for p, stamp in entry['outputs'].items()):
                    return entry['result']
            except OSError:
                pass
        self.entries.pop(name, None)
        return None

    def remember(self, source, destination, result):
        if result.get('error') or not (result.get('converted') or result.get('copied')):
            return
        if not destination.parent.exists():
            return
        # Includes weight and beast variants, even when the plain NIF is removed.
        outputs = {str(p.resolve()): _stamp(p) for p in destination.parent.iterdir()
                   if p.is_file() and p.suffix.lower() == '.nif'
                   and (p.stem == destination.stem
                        or p.stem.startswith(destination.stem + '_'))}
        # Derived textures are outputs too: a cached NIF must not keep a
        # reference to a missing height map or detail atlas.
        mesh_parent = next((p for p in destination.parents
                            if p.name.casefold() == 'meshes'), None)
        if mesh_parent is not None:
            from asset_convert.game_paths import current_namespace
            for texture in result.get('textures', ()):
                rel = texture.replace('\\', '/').removeprefix('textures/').lstrip('/')
                original = rel.removeprefix(current_namespace() + '/')
                derived = mesh_parent.parent / 'textures' / rel
                if derived.is_file() and not any((Path(root) / original).is_file()
                                                for root in self.texture_roots):
                    outputs[str(derived.resolve())] = _stamp(derived)
        if outputs:
            self.entries[str(destination.resolve())] = {
                'key': self.key(source), 'outputs': outputs, 'result': _normal(result)}

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({'token': self.token, 'entries': self.entries}),
                                 encoding='utf-8')
        except OSError as exc:
            print(f'  Mesh reuse unavailable: {exc}')
