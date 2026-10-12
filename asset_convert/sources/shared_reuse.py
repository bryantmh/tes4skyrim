"""Dependency stamps for reusing successful shared-asset work between runs."""
import hashlib
import json
import os
from pathlib import Path


def stamps(roots):
    result = {}
    for root in roots:
        root = Path(root)
        if root.is_file():
            paths = [root]
        elif root.is_dir():
            paths = (Path(folder) / name for folder, dirs, files in os.walk(root)
                     for name in files if '__pycache__' not in Path(folder).parts)
        else:
            result[str(root.resolve())] = None
            continue
        for path in paths:
            stat = path.stat()
            result[str(path.resolve())] = [stat.st_size, stat.st_mtime_ns,
                                         stat.st_ctime_ns]
    return result


def implementation_stamp():
    package = Path(__file__).resolve().parents[1]
    # Converter code and shipped templates can change without a version bump.
    roots = [package, package.parent / 'core' / 'collision_options.py',
             package.parent / 'lib']
    return stamps(roots)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def reusable(path, key, outputs):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data['key'] == key and data['outputs'] == stamps(outputs)
    except (OSError, ValueError, KeyError):
        return False


def remember(path, key, outputs):
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'key': key, 'outputs': stamps(outputs)}),
                        encoding='utf-8')
        temp.replace(path)
    except OSError as exc:
        print(f'  Shared asset reuse unavailable: {exc}')
