"""Vanilla Skyrim asset lookup with automatic BSA extraction.

THIS MODULE IS FOR THE RUNTIME PIPELINE ONLY -- i.e. code that must ship a
vanilla Skyrim file into the converted output (body/hands/feet meshes for skin
splicing, the book reading templates, ...).  Such code must never resolve
through references/, because references/ is a comparison tree that is not
guaranteed to be present.  For those callers the search order is:

    1. export/skyrim_assets/<rel>           (cache of prior BSA extractions)
    2. the game's own SSE BSA archives      (auto-detected via registry),
       extracted on demand and cached in 1.

DO NOT USE THIS MODULE TO ANSWER "what does vanilla do here?" WHILE DEBUGGING.
That is the exact opposite rule: CLAUDE.md forbids reading the live SSE install
for anything but Papyrus logs and Skyrim.esm, and forbids digging through
SSE-format assets/BSAs at all.  For investigation, read `references/Skyrim
Meshes` and the `references/Skyrim.esm` dump instead -- they exist precisely so
no one has to touch the install.  A cache miss here is NOT permission to go
hunting through the archives for the right path; go to references/.

BSA-sourced files are SSE-format; read them with asset_convert.sse_nif
(pyffi Patch 8 + BSTriShape->NiTriShape conversion).
"""

import os
from pathlib import Path

_REPO = Path(__file__).parent.parent
_CACHE_DIR = _REPO / 'export' / 'skyrim_assets'

_skyrim_data_override = None
_skyrim_data_cached = False
_skyrim_data = None


def set_skyrim_data(path):
    """Explicitly set the SSE Data folder (overrides registry detection).

    PIPELINE USE ONLY -- see the module docstring. Pointing this at the live
    install to go looking at vanilla assets is the forbidden use.
    """
    global _skyrim_data_override, _skyrim_data_cached
    _skyrim_data_override = str(path) if path else None
    _skyrim_data_cached = False


def _config_tes5_path():
    """tes5DataPath from conversion_config.json, or None.

    winreg (below) is Windows-only, so off Windows this config entry is the
    only auto-detection source. Read directly rather than importing convert.py
    to avoid a dependency cycle; empty/absent by default, so this is a no-op
    on a Windows machine that hasn't set it.
    """
    try:
        import json
        with open(_REPO / 'conversion_config.json', 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        p = cfg.get('tes5DataPath', '') or ''
        return p if p and os.path.isdir(p) else None
    except (FileNotFoundError, OSError, ValueError):
        return None


def find_skyrim_data():
    """SSE Data folder: explicit override, else conversion_config.json, else
    the Windows registry.

    PIPELINE USE ONLY. This returns the path to the LIVE, heavily-modded SSE
    install. Do NOT use it to go looking at vanilla assets, to check whether a
    build deployed, or to answer "what does vanilla do here?" -- CLAUDE.md
    allows that folder for Papyrus logs and Skyrim.esm ONLY. For investigation
    use `references/Skyrim Meshes`, the `references/Skyrim.esm` dump,
    `references/nifskope` and `references/nif [version].xml`.
    """
    global _skyrim_data_cached, _skyrim_data
    if _skyrim_data_cached:
        return _skyrim_data
    _skyrim_data_cached = True
    _skyrim_data = None
    if _skyrim_data_override and os.path.isdir(_skyrim_data_override):
        _skyrim_data = _skyrim_data_override
        return _skyrim_data
    configured = _config_tes5_path()
    if configured:
        _skyrim_data = configured
        return _skyrim_data
    try:
        import winreg
        for hive_key in (r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
                         r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_key) as key:
                    path, _ = winreg.QueryValueEx(key, "Installed Path")
                data = os.path.join(path, "Data")
                if os.path.isdir(data):
                    _skyrim_data = data
                    break
            except OSError:
                continue
    except ImportError:
        pass
    return _skyrim_data


def _bsa_globs_for(rel):
    """BSA name patterns that may hold this file, in search order.

    PIPELINE USE ONLY. Widening these patterns to hunt for a file you want to
    LOOK AT is the forbidden use -- SSE-format assets/BSAs are off-limits for
    investigation (CLAUDE.md). If a lookup misses, the answer is in
    `references/`, not in another archive.

    Behaviour graphs and animations live under `meshes\\` but ship in
    `Skyrim - Animations.bsa`, NOT the mesh archives — so a .hkx lookup has to
    try both or it silently returns None (which is what hid the animated-
    activator behaviour graphs).
    """
    norm = rel.replace('/', '\\').lstrip('\\').lower()
    top = norm.split('\\', 1)[0]
    if norm.endswith('.hkx'):
        return ['Skyrim - Animations.bsa', 'Skyrim - Meshes*.bsa']
    return {
        'meshes': ['Skyrim - Meshes*.bsa'],
        'textures': ['Skyrim - Textures*.bsa'],
    }.get(top, ['Skyrim - *.bsa'])


def get_asset_bytes(rel):
    """Return the bytes of a vanilla Skyrim file, or None.

    PIPELINE USE ONLY -- call this when the converter must SHIP a vanilla file
    into output/.  It is not a research tool: answering "how does vanilla author
    X?" by pulling assets out of the SSE BSAs is forbidden (CLAUDE.md), and if
    what comes back will not survive a parse, that is the rule telling you to go
    to references/, not a problem to work around.

    **A parse failure here is the guardrail. Do not engineer past it.**
    Recorded failure (2026-08-05): a session pulled the vanilla draugr/human
    `skeleton.nif` from the BSAs to survey weapon attachment nodes, hit pyffi's
    block-size error on their bhkRigidBody/bhkCapsuleShape layouts, and
    hand-rolled a bespoke NIF header parser to read them anyway. Three rules
    broken at once: SSE assets were off-limits for investigation to begin with,
    `references/nifskope` + `references/nif [version].xml` are the sanctioned
    answer for NIF structure questions, and `tools/` was never checked first.

    rel: data-relative path, e.g. r'meshes\\actors\\character\\character
    assets\\malebody_0.nif'.  Search order: extraction cache, then game BSAs
    (SSE format; extracted + cached).
    """
    rel = rel.replace('/', '\\').lstrip('\\')

    cached = _CACHE_DIR / Path(*rel.split('\\'))
    if cached.is_file():
        return cached.read_bytes()

    data_dir = find_skyrim_data()
    if not data_dir:
        return None
    from .bsa_extract import read_bsa_files
    candidates = []
    for pattern in _bsa_globs_for(rel):
        candidates.extend(sorted(Path(data_dir).glob(pattern)))
    for bsa in candidates:
        found = read_bsa_files(str(bsa), [rel])
        raw = found.get(rel.lower())
        if raw is not None:
            # Cache atomically: pool workers may race on the same file.
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(cached.suffix + '.tmp%d' % os.getpid())
            tmp.write_bytes(raw)
            try:
                os.replace(tmp, cached)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            return raw
    return None


def get_body_nif_bytes(basename):
    """Vanilla character-asset NIF (malebody_0.nif etc.) as bytes, or None.

    PIPELINE USE ONLY -- this exists so skin splicing can ship a vanilla body
    mesh into output/. Do NOT call it to inspect how a vanilla mesh is built;
    read `references/Skyrim Meshes` instead (see get_asset_bytes).
    """
    return get_asset_bytes(
        'meshes\\actors\\character\\character assets\\' + basename)
