"""Nemesis interoperability: keep our creature projects alive in a load order
that runs Nemesis Unlimited Behavior Engine.

THE PROBLEM
-----------
The game reads exactly ONE ``meshes\\animationdatasinglefile.txt`` and one
``meshes\\animationsetdatasinglefile.txt`` out of Data. Nemesis regenerates that
pair into its own output mod, which by installation convention wins the
conflict -- and it builds them from ITS OWN vanilla baseline, so every project
this converter generates falls out of them.

What that looks like in game: the behaviour graph still loads (the actor is
visible and its idle plays), but no clip has any metadata -- no playback rate,
no triggers, no root motion -- so nothing binds. The creature slides at its
MOVT speed with no locomotion animation and never attacks.

THE FIX: OVERRIDE NEMESIS'S BASELINE, DON'T PATCH AND DON'T EDIT ITS INSTALL
---------------------------------------------------------------------------
Nemesis does not read the game's ``animationdatasinglefile.txt``. It reads its
own pair, shipped as ordinary Data assets::

    meshes\\nemesis_animationdatasinglefile.txt      429 vanilla projects
    meshes\\nemesis_animationsetdatasinglefile.txt    49 vanilla projects

``UpdateFilesStart::VanillaUpdate`` finds them by walking
``nemesisInfo->GetDataPath() + "meshes\\"`` (see ``GetPathLoop`` /
``GetFileLoop`` in ``src/update/updateprocess.cpp``), matching the ``nemesis_``
prefix and stripping it with ``curFileName.substr(8)`` before
``AnimDataDisassemble``.

Because they live under ``meshes\\``, they are overridable like any other
asset. So we ship OUR OWN copy -- Nemesis's originals plus our projects -- in
our own mod, later in the load order. Nemesis then reads our pair as its
baseline and carries every creature through each regeneration. **Nothing in the
Nemesis installation is modified**; uninstalling our mod restores its files.

Crucially this also means our projects are already registered before any mod
patch is examined, so the new-project registration branch in ``ModThread``
(which mutates ``projectlist`` / ``projectIndexMap`` and ``newAnimSetData`` with
no lock, from a thread pool) is never entered. A ``Nemesis_Engine/mod/`` patch
that registered 86 new projects was tried first and crashed Nemesis with an
access violation; this route cannot reach that code at all.

LOAD ORDER
----------
Our mod must sit AFTER "Nemesis Unlimited Behavior Engine" (so our baseline
wins) and BEFORE "Nemesis Output" (so the pair the GAME reads is the one
Nemesis just regenerated, which contains our projects *and* every other
animation mod's). Getting this backwards does not break creatures -- it drops
everything else Nemesis generated.

THE OTHER ROUTE
---------------
``inject_into_cache`` merges our projects straight into whichever generated
pair currently wins, after the fact. It needs re-running after every Nemesis
regeneration, where the baseline override does not.
"""

import json
import os

from asset_convert.animation_data import (
    merge_animationdata, merge_animationsetdata,
    strip_generated_animationdata, strip_generated_animationsetdata,
)

# What the GAME reads.
CACHE_FILES = ('animationdatasinglefile.txt', 'animationsetdatasinglefile.txt')
# What NEMESIS reads as its vanilla baseline (same grammar, `nemesis_` prefix).
BASELINE_FILES = tuple('nemesis_' + f for f in CACHE_FILES)


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------

def load_manifests(meshes_dir: str) -> list:
    """Every generated creature project under `<meshes>/actors/tes4/`.

    Walks to ANY depth rather than assuming one. The layout moved from
    `actors/tes4/<folder>/` to `actors/tes4/<plugin namespace>/<folder>/` so
    that two plugins shipping a creature folder of the same name stop
    overwriting each other (`hkx_behavior.project_layout`), and a loader that
    hardcodes the depth silently finds NOTHING the day that happens -- which
    reads as "no creatures to register" rather than as a broken scan.
    """
    actors = os.path.join(meshes_dir, 'actors', 'tes4')
    out = []
    if not os.path.isdir(actors):
        return out
    for dirpath, _dirnames, filenames in os.walk(actors):
        if 'project_manifest.json' in filenames:
            with open(os.path.join(dirpath, 'project_manifest.json'),
                      encoding='utf-8') as f:
                out.append(json.load(f))
    out.sort(key=lambda m: m['project_txt'].lower())
    return out


def load_all_manifests(output_root: str, log=print) -> list:
    """Every generated project across EVERY converted plugin in `output/`.

    The two singlefiles are shared by the whole load order -- one file, one
    copy in Data -- so they must register the UNION of all plugins' projects.
    Loading only one plugin's manifests would silently de-register every
    sibling's creatures: Oblivion.esm owns 44 projects and ElsweyrAnequina
    another 42, and a 44-project merge drops the other 42 exactly as surely as
    Nemesis drops all 86.
    """
    seen, out = {}, []
    if not os.path.isdir(output_root):
        return out
    for plugin in sorted(os.listdir(output_root)):
        meshes = os.path.join(output_root, plugin, 'meshes')
        if not os.path.isdir(meshes):
            continue
        for m in load_manifests(meshes):
            key = m['project_txt'].lower()
            if key in seen:
                log(f'  [nemesis] {m["project_txt"]}: also in {seen[key]}, '
                    f'keeping the first')
                continue
            seen[key] = plugin
            out.append(m)
    return out


# ---------------------------------------------------------------------------
# Shared merge
# ---------------------------------------------------------------------------

def _read(path):
    with open(path, encoding='latin-1') as f:
        return f.read().splitlines()


def _write(path, lines):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='latin-1', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')


def _merge_pair(manifests, sources: dict, dest_names, out_dir, log, label):
    """Strip our old blocks from each source, merge ours in, write to out_dir.

    Stripping first is what makes a re-run a REPLACE rather than a no-op:
    `merge_*` skips a project whose name is already registered, so without it a
    creature rebuild would never reach the game.
    """
    counts = {}
    for (src_path, dest_name), (strip, merge) in zip(
            zip(sources, dest_names),
            ((strip_generated_animationdata, merge_animationdata),
             (strip_generated_animationsetdata, merge_animationsetdata))):
        lines = _read(src_path)
        before = int(lines[0])
        lines = strip(lines)
        kept = int(lines[0])
        merged = merge(lines, manifests)
        _write(os.path.join(out_dir, dest_name), merged)
        total = int(merged[0])
        counts[dest_name] = total
        stale = before - kept
        note = f' ({stale} stale ours dropped)' if stale else ''
        log(f'  [nemesis] {label} {dest_name}: {kept} original projects kept'
            f'{note} + {total - kept} ours = {total}')
    return counts


# ---------------------------------------------------------------------------
# Mode 1: override Nemesis's own baseline (the recommended route)
# ---------------------------------------------------------------------------

def find_nemesis_baseline(root: str, max_depth: int = 6) -> list:
    """Directories under `root` holding Nemesis's baseline pair.

    Returns [(meshes_dir, n_ad_projects, n_generated)] so the caller can see
    which copy is pristine and which already carries our creatures. The copy
    that wins is a property of the user's mod manager, so it is reported rather
    than guessed.
    """
    from asset_convert.animation_data import is_generated_project
    hits = []
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        lower = {f.lower() for f in filenames}
        if not set(BASELINE_FILES) <= lower:
            continue
        path = os.path.join(dirpath, BASELINE_FILES[0])
        try:
            lines = _read(path)
            n = int(lines[0])
            gen = sum(1 for x in lines[1:1 + n] if is_generated_project(x))
        except (ValueError, IndexError, OSError):
            n, gen = -1, -1
        hits.append((dirpath, n, gen))
    return sorted(hits)


def _mo2_ini_values(path: str) -> dict:
    """`ModOrganizer.ini` as a flat dict, Qt escaping undone.

    Hand-parsed rather than via configparser: values are Qt-serialised
    (`gamePath=@ByteArray(D:\\\\Steam\\\\...)`) with doubled backslashes, and
    the wrapper has to come off before the path means anything.
    """
    out = {}
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith((';', '#', '[')) or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                val = val.strip()
                if val.startswith('@ByteArray(') and val.endswith(')'):
                    val = val[len('@ByteArray('):-1]
                out[key.strip()] = val.replace('\\\\', '\\')
    except OSError:
        pass
    return out


def mo2_instances() -> list:
    """[(name, mods_dir, game_path)] for every Mod Organizer 2 instance.

    MO2 keeps global instances in `%LOCALAPPDATA%\\ModOrganizer\\<name>\\`;
    `base_directory` in each instance's ini says where its mods actually live
    (it is routinely on another drive), defaulting to the instance folder.
    Portable instances live next to their own exe and cannot be discovered
    from here.
    """
    root = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ModOrganizer')
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        inst = os.path.join(root, name)
        ini = os.path.join(inst, 'ModOrganizer.ini')
        if not os.path.isfile(ini):
            continue
        vals = _mo2_ini_values(ini)
        base = vals.get('base_directory') or inst
        mods = vals.get('mod_directory') or os.path.join(base, 'mods')
        if os.path.isdir(mods):
            out.append((name, mods, vals.get('gamePath', '')))
    return out


def _baseline_counts(meshes_dir: str):
    """(total projects, how many are ours) for a baseline pair, or (-1, -1)."""
    from asset_convert.animation_data import is_generated_project
    try:
        lines = _read(os.path.join(meshes_dir, BASELINE_FILES[0]))
        n = int(lines[0])
        return n, sum(1 for x in lines[1:1 + n] if is_generated_project(x))
    except (OSError, ValueError, IndexError):
        return -1, -1


def autodetect(prefer_data: str = None) -> list:
    """[(dir, source, projects, ours)] of every Nemesis baseline found.

    Ordered best-first on two keys:

    * the MO2 instance pointing at the game install we are converting for --
      a machine routinely carries several (SE, VR, Oblivion) and picking the
      wrong one silently builds from another game's project list;
    * PRISTINE copies first. Our own deployed output also carries a
      `nemesis_*singlefile.txt` pair, so a plain scan finds it too; merging
      from it would pin the baseline to a stale snapshot of Nemesis's vanilla
      list instead of tracking the real one.
    """
    prefer_game = ''
    if prefer_data:
        prefer_game = os.path.normcase(os.path.normpath(
            os.path.dirname(os.path.normpath(prefer_data))))

    ranked = []
    for name, mods, game_path in mo2_instances():
        try:
            entries = sorted(os.listdir(mods))
        except OSError:
            continue
        same_game = bool(prefer_game and game_path and os.path.normcase(
            os.path.normpath(game_path)) == prefer_game)
        for mod in entries:
            found = baseline_dir(os.path.join(mods, mod))
            if found:
                total, ours = _baseline_counts(found)
                ranked.append(((0 if same_game else 1, 1 if ours else 0),
                               found, f'MO2 instance "{name}" / {mod}',
                               total, ours))

    # Vortex, or a manual install: the files sit straight in the game's Data.
    if prefer_data:
        found = baseline_dir(prefer_data)
        if found:
            total, ours = _baseline_counts(found)
            ranked.append(((2, 1 if ours else 0), found, 'game Data folder',
                           total, ours))

    ranked.sort(key=lambda t: (t[0], t[1].lower()))
    seen, out = set(), []
    for _rank, path, source, total, ours in ranked:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            out.append((path, source, total, ours))
    return out


def baseline_dir(root: str):
    """The folder holding the baseline pair, or None.

    Accepts the Nemesis mod ROOT (`...\\Nemesis Unlimited Behavior Engine`) or
    the `meshes` folder itself. A folder picker naturally lands on the root --
    that is the thing with a recognisable name -- so `meshes` is appended here
    rather than being something the user has to know to type.
    """
    if not root:
        return None
    for cand in (root, os.path.join(root, 'meshes')):
        if all(os.path.isfile(os.path.join(cand, f)) for f in BASELINE_FILES):
            return cand
    return None


def write_baseline_override(manifests: list, nemesis_root: str,
                            out_meshes_dir: str, log=print) -> dict:
    """Write `nemesis_*singlefile.txt` = Nemesis's originals + our projects.

    `nemesis_root` is the Nemesis mod folder (or its `meshes` subfolder);
    `out_meshes_dir` is OUR mod's `meshes` folder. The Nemesis install is only
    READ.
    """
    src_dir = baseline_dir(nemesis_root)
    if not src_dir:
        raise FileNotFoundError(
            f'no {BASELINE_FILES[0]} under {nemesis_root!r} (looked there and '
            f'in its meshes subfolder)')
    srcs = [os.path.join(src_dir, f) for f in BASELINE_FILES]
    counts = _merge_pair(manifests, srcs, BASELINE_FILES, out_meshes_dir, log,
                         'baseline')
    log(f'  [nemesis] baseline read from {src_dir} (not modified)')
    log('  [nemesis] load order: our mod AFTER "Nemesis Unlimited Behavior '
        'Engine", BEFORE "Nemesis Output"')
    return counts


# ---------------------------------------------------------------------------
# Mode 2: inject into whichever generated pair currently wins
# ---------------------------------------------------------------------------

def inject_into_cache(manifests: list, base_dir: str, out_dir: str = None,
                      log=print) -> dict:
    """Merge our projects into the GAME-facing pair sitting in `base_dir`.

    Writes to `out_dir` (defaults to `base_dir`, i.e. in place). Idempotent.
    Unlike the baseline override this must be re-run after every Nemesis
    regeneration, because Nemesis rewrites the file it patches.
    """
    out_dir = out_dir or base_dir
    srcs = [os.path.join(base_dir, f) for f in CACHE_FILES]
    for p in srcs:
        if not os.path.exists(p):
            raise FileNotFoundError(f'no {os.path.basename(p)} in {base_dir}')
    return _merge_pair(manifests, srcs, CACHE_FILES, out_dir, log, 'inject')


def find_caches(root: str, max_depth: int = 6) -> list:
    """Locate every game-facing `animationdatasinglefile.txt` under a mods root.

    Returns [(path, n_projects, n_generated)].
    """
    from asset_convert.animation_data import is_generated_project
    hits = []
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        for fn in filenames:
            if fn.lower() != CACHE_FILES[0]:
                continue
            path = os.path.join(dirpath, fn)
            try:
                lines = _read(path)
                n = int(lines[0])
                gen = sum(1 for x in lines[1:1 + n] if is_generated_project(x))
            except (ValueError, IndexError, OSError):
                n, gen = -1, -1
            hits.append((path, n, gen))
    return sorted(hits)
