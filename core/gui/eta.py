"""How long a conversion will take: what a plugin holds, read from its source before anything runs.

A plugin's content is its records' bytes by type (from the plugin file's own record and group
headers), a few record counts, and its assets by kind (from its archives' file tables, plus an added
mod's loose tree). Each step is a list of sub-steps, each timed from its own driver.
See: docs/commentary/core_gui.md#run-estimates
"""

import ctypes
import functools
import json
import mmap
import os
import re
import struct
import time
from collections import Counter
from ctypes import wintypes
from pathlib import Path

import version as version_info
from asset_convert.sources import source_registry
from asset_convert.sources.bsa_extract import get_bsa_files, read_bsa_directory
from asset_convert.sources.bsa_extract_morrowind import is_morrowind_bsa, read_index
from core.gui.config import EXPORT_DIR, load_config
from core.gui.eta_fit import CACHED, MODEL
from core.plugin_masters import get_masters_from_binary
from output_layout import DATA_ROOT
from tes4_export.export_morrowind import MORROWIND_SOURCE_KEY, SOURCE_HYBRID, VANILLA_MASTERS
from tes4_export.morrowind_grass import GRASS_MODEL_PREFIX
from tes4_export.morrowind_world import TES3_CELL_SIZE, TES4_CELL_SIZE
from tools.navmesh import navmesh_cache

#: Header sizes: a TES3 record, and the TES4/FNV record/group header (HEDR follows it).
TES3_HEADER, TES4_HEADERS = 16, (20, 24)
#: The records entry counting a Morrowind plugin's voiced dialogue lines (its voices are loose files).
VOICED = "voiced"
#: The records entry counting pathgrids: one generated navmesh each.
PATHGRIDS = "pathgrids"
#: The records entry counting exterior cells, each of which may be seeded a navmesh without a pathgrid.
EXTERIOR_CELLS = "exterior_cells"
#: The records entry counting outdoor pathgrids, whose navmeshes cost far more than an interior's.
OUTDOOR_PATHGRIDS = "outdoor_pathgrids"
#: The records entry counting Morrowind armor and clothing built from body parts: one assembled worn mesh each.
WORN = "worn"
#: The records entry counting a Morrowind plugin's exterior references.
REFS = "refs"
#: Record types counted (not only sized), as records entries "count:<type>".
COUNTED = frozenset((b"LAND", b"HAIR", b"WEAP"))
#: A Morrowind reference inside a cell body: FRMR, then its base object's id (NAME).
REF_BASE = re.compile(rb"FRMR\x04\x00\x00\x00.{4}NAME.{4}([^\x00]*)", re.DOTALL)
#: A static whose model sits under this folder is groundcover (`morrowind_grass.is_grass_model`).
GRASS_PREFIX = GRASS_MODEL_PREFIX.encode("latin-1")
#: The assets entry holding [creature folders, bytes of the largest one].
LARGEST_CREATURE = "largest_creature"
#: Seconds one creature folder takes per MB, the median of the six largest in Nehrim, FalloutNV and Elsweyr.
CREATURE_SECONDS_PER_MB = 6.5
#: Loop rounds in the CPU benchmark (about 0.1 s on the profiling machine).
BENCH_ROUNDS = 200_000


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def _tes3_field(body: bytes, sig: bytes):
    """The first `sig` subrecord's data in a Morrowind record body, or None."""
    pos = 0
    while pos + 8 <= len(body):
        length = struct.unpack_from("<I", body, pos + 4)[0]
        if body[pos:pos + 4] == sig:
            return body[pos + 8:pos + 8 + length]
        pos += 8 + length
    return None


def _tes3_body(out: Counter, kind: str, body: bytes, seen: tuple) -> None:
    """Count what one Morrowind record body adds: voiced lines, cells and their references' bases, pathgrids, worn
    models, grass statics. `seen` is (interior names, pathgrids, reference bases, grass static ids)."""
    interiors, grids, bases, grass = seen
    out[VOICED] += kind == "INFO" and b"SNAM" in body
    out[WORN] += kind in ("ARMO", "CLOT") and _tes3_field(body, b"INDX") is not None
    flags = _tes3_field(body, b"DATA") if kind == "CELL" else None
    if flags and struct.unpack_from("<I", flags)[0] & 1:
        interiors.add(_tes3_field(body, b"NAME"))
    elif flags:
        out[EXTERIOR_CELLS] += 1
        bases.update(REF_BASE.findall(body))
    if kind == "PGRD":
        grids.append((_tes3_field(body, b"DATA")[:8] == bytes(8), _tes3_field(body, b"NAME")))
    model = _tes3_field(body, b"MODL") if kind == "STAT" else None
    if model and model.replace(b"/", b"\\").lower().startswith(GRASS_PREFIX):
        grass.add(_tes3_field(body, b"NAME").rstrip(b"\0").lower())


def _tes3_records(fh, size: int, bases: Counter, grass: set) -> Counter:
    """{record type: bytes} for a Morrowind plugin, plus VOICED (dialogue lines naming a voice file), WORN, LAND's
    count, REFS (exterior references) and PATHGRIDS / OUTDOOR_PATHGRIDS (indoors: grid (0, 0) and NAME an interior
    cell's, as OpenMW decides it). Fills `bases` {exterior reference base id: count} and `grass` (grass static ids)."""
    out, pos, interiors, grids = Counter(), 0, set(), []
    while pos + TES3_HEADER <= size:
        fh.seek(pos)
        head = fh.read(TES3_HEADER)
        kind, length = head[:4].decode("latin-1"), struct.unpack_from("<I", head, 4)[0]
        out[kind] += TES3_HEADER + length
        if head[:4] in COUNTED:
            out["count:" + kind] += 1
        if kind in ("INFO", "CELL", "PGRD", "ARMO", "CLOT", "STAT"):
            _tes3_body(out, kind, fh.read(length), (interiors, grids, bases, grass))
        pos += TES3_HEADER + length
    out[PATHGRIDS] = len(grids)
    out[OUTDOOR_PATHGRIDS] = sum(1 for origin, name in grids if not (origin and name in interiors))
    out[REFS] = sum(bases.values())
    return out


def _tes4_records(fh, size: int, header: int) -> Counter:
    """{top group label: bytes} for an Oblivion or New Vegas plugin, plus PATHGRIDS, OUTDOOR_PATHGRIDS (those
    under WRLD), exterior cells and the COUNTED types' counts.

    Walks into every group, since pathgrids sit inside the cell groups.
    """
    out, pos, top_end, label = Counter(), 0, 0, ""
    with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        while pos + header <= size:
            sig, length = mm[pos:pos + 4], struct.unpack_from("<I", mm, pos + 4)[0]
            top = pos >= top_end
            if sig == b"GRUP":
                if top:
                    label = mm[pos + 8:pos + 12].decode("latin-1")
                    out[label] += length
                    top_end = pos + length
                pos += header
                continue
            if top:
                label = sig.decode("latin-1")
                out[label] += header + length
            if sig in COUNTED:
                out["count:" + sig.decode("latin-1")] += 1
            out[PATHGRIDS] += sig == b"PGRD"
            out[OUTDOOR_PATHGRIDS] += sig == b"PGRD" and label == "WRLD"
            out[EXTERIOR_CELLS] += sig == b"CELL" and label == "WRLD"
            pos += header + length
    return out


def plugin_format(path) -> str:
    """'tes3', 'tes4' or 'fnv', from the plugin file's first record."""
    with open(path, "rb") as fh:
        head = fh.read(28)
    if head[:4] == b"TES3":
        return "tes3"
    return "fnv" if head[24:28] == b"HEDR" else "tes4"


def records(path, bases=None, grass=None) -> Counter:
    """{record type: bytes} in the plugin at `path`, plus the counts (lower-case keys); a Morrowind plugin also fills
    `bases` and `grass` (`_tes3_records`)."""
    size, fmt = os.path.getsize(path), plugin_format(path)
    with open(path, "rb") as fh:
        if fmt == "tes3":
            return _tes3_records(fh, size, Counter() if bases is None else bases, set() if grass is None else grass)
        return _tes4_records(fh, size, TES4_HEADERS[fmt == "fnv"])


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def asset_kind(rel: str) -> str:
    """The kind of asset a stored path is, by folder and extension."""
    low = rel.lower().replace("/", "\\")
    ext = os.path.splitext(low)[1]
    if ext == ".kf":
        return "kf"
    if ext == ".nif":
        return "creature_nif" if low.startswith("meshes\\creatures\\") else "nif"
    if ext == ".spt":
        return "spt"
    if ext == ".dds":
        return "dds"
    if low.startswith(("sound\\voice\\", "sound\\speech\\")):
        return "voice"
    if low.startswith("music\\"):
        return "music"
    return "sound" if low.startswith("sound\\") else "other"


def _archive_entries(bsa) -> list:
    """[(stored path, bytes)] in one archive of either generation."""
    if is_morrowind_bsa(bsa):
        return [(name, size) for name, (_start, size) in read_index(bsa).items()]
    with open(bsa, "rb") as fh:
        return [(path, size) for path, size, _o, _c in read_bsa_directory(fh)[2]]


def _loose_entries(root: Path) -> list:
    """[(path under `root`, bytes)] for every file in an added mod's asset tree."""
    out = []
    for sub in ("meshes", "textures", "sound", "music", "trees"):
        for folder, _dirs, files in os.walk(root / sub):
            out += [(os.path.relpath(os.path.join(folder, f), root), os.path.getsize(os.path.join(folder, f)))
                    for f in files]
    return out


def assets(plugin: str, data_dir) -> dict:
    """{kind: [files, bytes]} the plugin's conversion reads: its archives, or an added mod's loose tree.

    LARGEST_CREATURE holds [creature folders, bytes of the largest].
    """
    entries = []
    for bsa in get_bsa_files(data_dir, plugin) if data_dir else ():
        entries += _archive_entries(bsa)
    if source_registry.get(EXPORT_DIR, plugin):
        entries += _loose_entries(source_registry.asset_root(EXPORT_DIR, plugin))
    out, creatures = {}, Counter()
    for rel, size in entries:
        tally = out.setdefault(asset_kind(rel), [0, 0])
        tally[0] += 1
        tally[1] += size
        parts = rel.lower().replace("/", "\\").split("\\")
        if len(parts) > 3 and parts[:2] == ["meshes", "creatures"]:
            creatures[parts[2]] += size
    out[LARGEST_CREATURE] = [len(creatures), max(creatures.values(), default=0)]
    return out


# ---------------------------------------------------------------------------
# This computer
# ---------------------------------------------------------------------------

def _bench_once() -> float:
    """Seconds for a fixed mix of the work the pipeline does most: parsing text, dicts, float math."""
    start = time.perf_counter()
    table = {}
    for i in range(BENCH_ROUNDS):
        key = f"Record{i % 997}.Field{i % 13}"
        table[key] = table.get(key, 0.0) + (i * 0.5) ** 0.5
        key.split(".")[1].lower()
    return time.perf_counter() - start


def bench_seconds() -> float:
    """The benchmark's best of three runs, so a busy moment doesn't count against the computer."""
    return min(_bench_once() for _ in range(3))


def plugin_path(plugin: str):
    """The plugin file this run reads, or None when it cannot be found."""
    data_dir = source_registry.directory_for(EXPORT_DIR, plugin)
    path = source_registry.plugin_binary(EXPORT_DIR, plugin) or (Path(data_dir) / plugin if data_dir else None)
    return path if path is not None and Path(path).is_file() else None


def all_masters(plugin: str) -> list:
    """Every master `plugin` loads, masters of masters included, each once."""
    seen, todo, out = set(), [plugin], []
    while todo:
        path = plugin_path(todo.pop())
        for master in get_masters_from_binary(str(path)) if path else ():
            if master.lower() not in seen:
                seen.add(master.lower())
                todo.append(master)
                out.append(master)
    return out


def content(plugin: str) -> dict:
    """{'format', 'records', 'assets', 'master_mb', 'vanilla', 'grass', 'grass_refs'} for `plugin`, or {} when its
    file cannot be found. 'grass' holds its grass static ids; 'grass_refs' counts its exterior references to a grass
    static of its own or a master's.

    Read again only when the plugin file changes.
    """
    path = plugin_path(plugin)
    if path is None:
        return {}
    stat = os.stat(path)
    return _content(plugin, str(path), stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=256)
def _content(plugin: str, path: str, _mtime: int, _size: int) -> dict:
    """`content` for one version of the plugin file."""
    bases, grass, masters = Counter(), set(), all_masters(plugin)
    found = {"format": plugin_format(path), "records": dict(records(path, bases, grass)),
             "assets": assets(plugin, source_registry.directory_for(EXPORT_DIR, plugin)),
             "master_mb": sum(os.path.getsize(p) for p in map(plugin_path, masters) if p) / 1e6,
             "vanilla": plugin.lower() in VANILLA_MASTERS, "grass": frozenset(grass)}
    if bases:
        grass = grass.union(*(content(m).get("grass", ()) for m in masters))
        lowered = Counter()
        for base, n in bases.items():
            lowered[base.lower()] += n
        found["grass_refs"] = sum(lowered[g] for g in grass)
    return found


def hybrid_mode() -> bool:
    """Whether Morrowind plugins export in the hybrid mode (Morroblivion's meshes and ground, and a companion)."""
    return str(load_config().get(MORROWIND_SOURCE_KEY, "")).strip().lower() == SOURCE_HYBRID


def warm_steps(plugin: str) -> set:
    """Steps whose caches are in place: those that ran before, Import only while its navmesh cache matches the code."""
    ran = set(version_info.steps_run_at(plugin))
    if "import_" in ran and not navmesh_cache.cache_matches_tag(plugin, navmesh_cache.source_tag(plugin)):
        ran.discard("import_")
    return ran


def plugin_plan(plugin: str, steps, workers: int) -> dict:
    """{step: [(sub-step, seconds)]} for converting `plugin` through `steps`, each warm or cold as `warm_steps`
    finds it; {} when the plugin file cannot be found."""
    found = content(plugin)
    if not found:
        return {}
    warm, hybrid = warm_steps(plugin), hybrid_mode()
    return {step: substep_seconds(step, found, step in warm, workers, hybrid=hybrid) for step in steps}


def plugin_estimate(plugin: str, steps, workers: int) -> dict:
    """{step: seconds} for converting `plugin` through `steps` (`plugin_plan`'s sub-steps summed)."""
    return {step: sum(s for _n, s in subs) for step, subs in plugin_plan(plugin, steps, workers).items()}


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

#: The load-order action Create LOD and its one timed sub-step.
LOD_STEP, LOD_SUBSTEP = "create_lod", "Creating LOD"

#: Seconds per exterior cell of the plugins Create LOD reads: Oblivion.esm's 18 worldspaces (33,639 cells) took 658 s.
LOD_SECONDS_PER_CELL = 658 / 33639

#: step -> its sub-steps in run order: (name, regex of the log line it starts at; None: the step's own start).
SUBSTEPS = {
    "export": (("Exporting records", None),
               ("Matching Morroblivion models", r"^  Creatures: \d+ of|^  Hybrid: matching Morroblivion"),
               ("Listing Morroblivion assets", r"^  Morroblivion ground:"),
               ("Writing records", r"^  Morroblivion \(hybrid\):|^  Text code page:")),
    "extract": (("Unpacking archives", None),),
    "meshes": (("Planning", None),
               ("Converting NIFs", r"^Found \d+ NIF files"),
               ("Assembling Morrowind armor", r"^  Morrowind armor: \d+ worn"),
               ("Hair and grass", r"^  Hair and grass\.\.\.$"),
               ("Copying textures", r"^Copy Textures to Output"),
               ("Checking textures", r"^  Textures: \d+ files"),
               ("Book icons", r"\] Generating book inventory-art meshes")),
    "speedtrees": (("Converting SpeedTrees", None),),
    "creatures": (("Splitting creatures", None),
                  ("Converting creatures", r"^  Converting \d+ creatures"),
                  ("Gun animations", r"^  Retargeting \d+ \S+ gun clips")),
    "import_": (("Loading masters", None),
                ("Reading the export", r"^Reading exports from:"),
                ("Converting records", r"^Converting records\.\.\."),
                ("Generating navmeshes", r"^  Generating \d+ navmeshes"),
                ("Writing the plugin", r"^  Phase: navmesh generation|^  Converting \d+ LAND records"),
                ("Morroblivion companion", r"Morroblivion Hybrid\.esp\] Importing")),
    "sounds": (("Voices", None), ("Sounds", r"^  \[Non-voice sounds\]"), ("Music", r"\] Converting music to xWMA")),
    "scripts": (("Translating scripts", None), ("Compiling", r"\] Compiling \d+ Papyrus scripts")),
}


def _counts(found: dict) -> dict:
    """{asset kind: files} and {asset kind: MB} from `content`."""
    items = [(k, v) for k, v in found.get("assets", {}).items() if k != LARGEST_CREATURE]
    return {k: n for k, (n, _b) in items}, {k: b / 1e6 for k, (_n, b) in items}


def measures(found: dict, hybrid: bool = False) -> dict:
    """The drivers the sub-step model reads, from `content`; `hybrid` is the Morrowind hybrid mode."""
    rec, (count, size) = found.get("records", {}), _counts(found)
    tes3 = found.get("format") == "tes3"
    mb = sum(v for k, v in rec.items() if k.isupper()) / 1e6
    hyb = float(hybrid and tes3)
    vanilla = hyb * bool(found.get("vanilla"))
    masters = found.get("master_mb", 0.0)
    creature = found.get("assets", {}).get(LARGEST_CREATURE, [0, 0])
    grass = found.get("grass_refs", 0)
    plain = rec.get(REFS, 0) - grass
    return {"one": 1.0, "mb": mb, "master_mb": masters, "script_mb": rec.get("SCPT", 0) / 1e6,
            "indoor_pathgrids": rec.get(PATHGRIDS, 0) - rec.get(OUTDOOR_PATHGRIDS, 0),
            "exterior_cells": rec.get(EXTERIOR_CELLS, 0), "lands": rec.get("count:LAND", 0),
            "grass_refs": grass, "grass_cells": rec.get(EXTERIOR_CELLS, 0) if grass else 0, "hybrid_plain_refs": hyb * plain,
            "hybrid": hyb, "hybrid_load_mb": hyb * (mb + masters), "hybrid_textured": hyb * (1 - vanilla) * bool(count.get("dds")),
            "vanilla_lands": vanilla * rec.get("count:LAND", 0), "vanilla_mb": vanilla * mb,
            "worn": rec.get(WORN, 0), "hair": rec.get("count:HAIR", 0),
            "guns": rec.get("count:WEAP", 0) if found.get("format") == "fnv" else 0,
            "nif": count.get("nif", 0) + count.get("creature_nif", 0), "nif_mb": size.get("nif", 0) + size.get("creature_nif", 0),
            "dds": count.get("dds", 0), "dds_mb": size.get("dds", 0), "spt": count.get("spt", 0),
            "anim": count.get("kf", 0) + count.get("creature_nif", 0), "anim_mb": size.get("kf", 0) + size.get("creature_nif", 0),
            "creature_folders": creature[0], "largest_creature_mb": creature[1] / 1e6,
            "voices": rec.get(VOICED, 0) if tes3 else count.get("voice", 0),
            "sounds": count.get("sound", 0), "music": count.get("music", 0),
            "archive_mb": sum(size.values()), "archive_files": sum(count.values())}


def physical_cores() -> int:
    """Physical CPU cores (hyperthreads not counted), from Windows; the logical count if it can't say."""
    size = wintypes.DWORD(0)
    kernel = ctypes.windll.kernel32
    kernel.GetLogicalProcessorInformation(None, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    if not size.value or not kernel.GetLogicalProcessorInformation(buf, ctypes.byref(size)):
        return os.cpu_count() or 1
    entry = ctypes.sizeof(ctypes.c_size_t) + 4 + 4 + 16
    return sum(1 for at in range(0, size.value, entry)
               if struct.unpack_from("<i", buf.raw, at + ctypes.sizeof(ctypes.c_size_t))[0] == 0) or (os.cpu_count() or 1)


#: The profiling machine: workers, physical and logical cores, and its `bench_seconds` in TESRACT.exe.
REF_WORKERS, REF_PHYSICAL, REF_LOGICAL, REF_BENCH = 29, 16, 32, 0.071
#: Throughput a hyperthread adds beyond the physical cores (fitted to 4-29 worker runs, 4.4% mean error).
HYPERTHREAD = 0.5
#: Learned per-computer correction of each sub-step's estimate: {"step/sub-step": actual / predicted}.
CORRECTIONS_FILE = DATA_ROOT / "logs" / "step_speed.json"
#: Weight of the newest run in a learned correction.
LEARN_WEIGHT = 0.5
#: Sub-steps shorter than this (seconds, estimated and actual) teach the learned correction nothing.
LEARN_MIN = 3.0


def throughput(workers: int, physical: int, logical: int) -> float:
    """Cores' worth of work `workers` processes get done at once on a machine with these cores."""
    return min(workers, physical) + HYPERTHREAD * max(0, min(workers, logical) - physical)


def _stretch(hist, cores: float) -> float:
    """How much longer the profiled stretch runs on `cores` of throughput (1.0 = no longer)."""
    return sum(share * max(1.0, throughput(busy, REF_PHYSICAL, REF_LOGICAL) / cores) for busy, share in hist)


@functools.lru_cache(maxsize=1)
def this_machine() -> tuple:
    """(single-core time relative to the profiling machine, physical cores, logical cores), measured once."""
    return bench_seconds() / REF_BENCH, physical_cores(), os.cpu_count() or 1


def reference_seconds(step: str, name: str, m: dict, fmt: str, warm: bool):
    """(seconds on the profiling machine, busy-core histogram) for one sub-step, or None when it never runs."""
    coef = MODEL.get((fmt, step, name, warm if (step, name) in CACHED else None))
    if coef is None:
        return None
    seconds = sum(b * m[k] for k, b in coef[0].items())
    if name == "Converting creatures":
        seconds = max(seconds, CREATURE_SECONDS_PER_MB * m["largest_creature_mb"])
    return seconds, coef[1]


def substep_seconds(step: str, found: dict, warm: bool, workers: int, machine=None, hybrid: bool = False) -> list:
    """[(sub-step, seconds)] for `step` of a plugin with content `found` on `machine` (this one by default); sub-steps
    the plugin never runs are left out."""
    speed, physical, logical = machine or this_machine()
    m, learned = measures(found, hybrid), corrections()
    cores, ref = throughput(workers, physical, logical), throughput(REF_WORKERS, REF_PHYSICAL, REF_LOGICAL)
    out = []
    for name, _marker in SUBSTEPS.get(step, ()):
        timed = reference_seconds(step, name, m, found.get("format"), warm)
        if timed is None or timed[0] <= 0:
            continue
        seconds, hist = timed
        scale = _stretch(hist, cores) / _stretch(hist, ref) if hist else 1.0
        out.append((name, speed * seconds * scale * learned.get(f"{step}/{name}", 1.0)))
    return out


def lod_plan(plugins) -> list:
    """[(sub-step, seconds)] for Create LOD reading `plugins`: their exterior cells, as converted cells.

    See: docs/commentary/core_gui.md#lod-estimate
    """
    cells = sum(_converted_cells(content(p)) for p in plugins)
    seconds = this_machine()[0] * cells * LOD_SECONDS_PER_CELL * corrections().get(f"{LOD_STEP}/{LOD_SUBSTEP}", 1.0)
    return [(LOD_SUBSTEP, seconds)] if cells else []


def _converted_cells(found: dict) -> float:
    """A plugin's exterior cells as converted cells: a Morrowind cell splits into (8192 / 4096)^2 of them."""
    cells = found.get("records", {}).get(EXTERIOR_CELLS, 0)
    return cells * (TES3_CELL_SIZE / TES4_CELL_SIZE) ** 2 if found.get("format") == "tes3" else cells


def step_seconds(step: str, found: dict, warm: bool, workers: int, machine=None, hybrid: bool = False) -> float:
    """Seconds `step` takes: its sub-steps summed."""
    return sum(s for _n, s in substep_seconds(step, found, warm, workers, machine, hybrid))


def corrections() -> dict:
    """The learned {"step/sub-step": factor}; {} before any run or when unreadable."""
    try:
        return json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def learn(step: str, name: str, predicted: float, actual: float) -> None:
    """Move one sub-step's correction toward what this run measured (`predicted` already includes the old one)."""
    if predicted <= 0 or actual <= 0 or max(predicted, actual) < LEARN_MIN:
        return
    data, key = corrections(), f"{step}/{name}"
    ratio = min(5.0, max(0.2, actual / predicted))
    data[key] = data.get(key, 1.0) * ratio ** LEARN_WEIGHT
    try:
        CORRECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CORRECTIONS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    """Print this computer's benchmark seconds (REF_BENCH is this, measured in TESRACT.exe on the profiling machine)."""
    print(f"bench {bench_seconds():.4f} s, {physical_cores()} physical cores, {os.cpu_count()} logical")
    return 0
