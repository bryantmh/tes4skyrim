"""Field-level audit of a converted creature's Havok project against a
vanilla Skyrim creature.

Decompiles (hkxcmd) the skeleton/character/behavior/project hkx of both
creatures and compares them CLASS BY CLASS: for every Havok class present in
the vanilla file, reports scalar/enum params whose VALUE SET in our file
differs from vanilla's, and classes vanilla has that we lack entirely.
Reference-valued params (#NNNN), names, and count-dependent arrays are
ignored — the point is contract fields (modes, flags, gains, damping...),
not topology that legitimately differs per creature.

Usage:
  python tools/creature_hkx_diff.py \
      --vanilla-dir "references/Skyrim Animations/meshes/actors/canine" \
      --vanilla-skel "character assets dog/skeleton.hkx" \
      --vanilla-char "characters dog/dog.hkx" \
      --vanilla-beh  "behaviors/dogbehavior.hkx" \
      --ours-dir "path/to/actors/tes4/rat" [--keep-xml]

Ours must be LE 32-bit hkx (regenerate via `python -m asset_convert
.hkx_behavior <export creature dir> <tmp>` — shipped files are AMD64 and
hkxcmd cannot read them).
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from asset_convert.hkx_xml import _to_hkxcmd_path  # noqa: E402
from subprocess_flags import windows_cmd  # noqa: E402

HKXCMD = os.path.join(os.path.dirname(__file__), '..', 'external', 'hkxcmd',
                      'hkxcmd.exe')

# params whose values legitimately differ per creature
SKIP_PARAMS = {
    'name', 'id', 'animationName', 'behaviorName', 'behaviorFilename',
    'rigName', 'ragdollName', 'localTime', 'event', 'events', 'triggers',
    'animationNames', 'eventNames', 'variableNames', 'characterPropertyNames',
    'attributeNames', 'variableInfos', 'eventInfos', 'wordVariableValues',
    'referencePose', 'parentIndices', 'bones', 'boneIndices', 'bonePairMap',
    'boneWeights', 'simpleMappings', 'unmappedBones', 'transform',
    'sweptTransform', 'inertiaAndMassInv', 'vertexA', 'vertexB', 'radius',
    'translations', 'rotations', 'stateId', 'toStateId', 'userData',
    'partitions', 'transitions', 'wildcardTransitions', 'states',
    'numelements',
}
REF_RE = re.compile(r'^(#\d+|null)(\s+#\d+)*$')


def decompile(src, dst):
    # hkxcmd wants native backslash paths; forward slashes fail silently
    # (off Windows, under Wine: _to_hkxcmd_path prefixes the Z: drive so the
    # backslash form still resolves to the same file — see hkx_xml.py).
    src = os.path.abspath(os.path.normpath(src))
    dst = os.path.abspath(os.path.normpath(dst))
    cmd = [os.path.abspath(HKXCMD), 'convert', '-v:xml',
           _to_hkxcmd_path(src), _to_hkxcmd_path(dst)]
    r = subprocess.run(windows_cmd(cmd), capture_output=True, text=True)
    if not os.path.isfile(dst):
        raise RuntimeError(f'hkxcmd failed on {src}: {r.stdout} {r.stderr}')


def parse(xml_path):
    """{class: {param: set(values)}} for scalar-ish params."""
    text = open(xml_path, encoding='ascii', errors='replace').read()
    out = defaultdict(lambda: defaultdict(set))
    for m in re.finditer(r'<hkobject name="#\d+" class="(\w+)"(.*?)\n\t\t'
                         r'</hkobject>', text, re.S):
        cls, body = m.group(1), m.group(2)
        for pm in re.finditer(r'<hkparam name="(\w+)"[^>]*>([^<]*)</hkparam>',
                              body):
            k, v = pm.group(1), pm.group(2).strip()
            if k in SKIP_PARAMS or not v or '\n' in v or REF_RE.match(v):
                continue
            out[cls][k].add(v)
    return out


def compare(tag, van, ours):
    print(f'===== {tag} =====')
    missing = sorted(set(van) - set(ours))
    if missing:
        print(f'  CLASSES vanilla has, ours LACKS: {missing}')
    extra = sorted(set(ours) - set(van))
    if extra:
        print(f'  classes only ours has (info): {extra}')
    for cls in sorted(set(van) & set(ours)):
        for k in sorted(set(van[cls]) | set(ours[cls])):
            v_v, v_o = van[cls].get(k, set()), ours[cls].get(k, set())
            if v_v != v_o:
                print(f'  {cls}.{k}:')
                print(f'      vanilla: {sorted(v_v)}')
                print(f'      ours:    {sorted(v_o)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vanilla-dir', required=True)
    ap.add_argument('--vanilla-skel', required=True)
    ap.add_argument('--vanilla-char', required=True)
    ap.add_argument('--vanilla-beh', required=True)
    ap.add_argument('--ours-dir', required=True,
                    help='converted creature dir (LE hkx): .../actors/tes4/<name>')
    ap.add_argument('--keep-xml', action='store_true')
    args = ap.parse_args()

    name = os.path.basename(os.path.normpath(args.ours_dir))
    ours = {
        'skeleton': os.path.join(args.ours_dir, 'character assets',
                                 'skeleton.hkx'),
        'character': os.path.join(args.ours_dir, 'characters',
                                  f'tes4{name}character.hkx'),
        'behavior': os.path.join(args.ours_dir, 'behaviors',
                                 f'tes4{name}behavior.hkx'),
        'project': os.path.join(args.ours_dir, f'tes4{name}project.hkx'),
    }
    van = {
        'skeleton': os.path.join(args.vanilla_dir, args.vanilla_skel),
        'character': os.path.join(args.vanilla_dir, args.vanilla_char),
        'behavior': os.path.join(args.vanilla_dir, args.vanilla_beh),
    }
    # vanilla project file lives next to the character dir, find *project.hkx
    for dirpath, _d, files in os.walk(args.vanilla_dir):
        for fn in files:
            if fn.lower().endswith('project.hkx'):
                van['project'] = os.path.join(dirpath, fn)
    tmp = tempfile.mkdtemp(prefix='hkxdiff_')
    for part in ('skeleton', 'character', 'behavior', 'project'):
        if part not in van or not os.path.isfile(van[part]):
            print(f'===== {part} ===== (vanilla file missing, skipped)')
            continue
        if not os.path.isfile(ours[part]):
            print(f'===== {part} ===== (OUR file missing: {ours[part]})')
            continue
        vx = os.path.join(tmp, f'van_{part}.xml')
        ox = os.path.join(tmp, f'our_{part}.xml')
        decompile(van[part], vx)
        decompile(ours[part], ox)
        compare(part, parse(vx), parse(ox))
    if args.keep_xml:
        print('XML kept in', tmp)


if __name__ == '__main__':
    sys.exit(main())
