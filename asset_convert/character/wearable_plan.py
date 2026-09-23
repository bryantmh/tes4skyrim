"""Which wearable NIF variants the converted plugin actually references.

A converted wearable can exist on disk in three forms:

    armor/iron/m/cuirass.nif      the plain converted mesh
    armor/iron/m/cuirass_0.nif    weight-0 variant
    armor/iron/m/cuirass_1.nif    weight-1 variant (body-morphed)

but the plugin never references all three for the same mesh, so writing all
three always wastes space:

  * ARMA sets the weight slider ONLY for gear covering body/hands/feet
    (tes5_import.record_types.equipment._build_arma).  With the slider on it
    references <name>_1.nif and the engine derives its partner _0 — the plain
    <name>.nif is dead.  With the slider off it references the plain
    <name>.nif — both _0 and _1 are dead.
  * ARMO's ground (dropped-item) model is the WorldModel when there is one and
    otherwise falls back to the biped path, which keeps the plain <name>.nif
    alive for the ~76 shields and odds and ends that ship no _gnd mesh.

This module derives that same set of decisions straight from the export, so the
converter writes exactly the files the plugin asks for.  The rules here MUST
track equipment.convert_ARMO / _build_arma — if the slider condition or the
ground-model fallback changes there, change it here too.
"""

import os
from pathlib import Path

from asset_convert.character.morrowind_coverage import (SkinFill, covered_partitions, hidden_skin,
                                                        part_slots, sided_slot)
from asset_convert.character.wearable_plan_falloutnv import biped_bit_body_parts


def _worn_models(rec: dict) -> list:
    """The normalized worn model paths a wearable record names."""
    return [norm_model_path(model) for key in ('Male.BipedModel.MODL', 'Female.BipedModel.MODL')
            if (model := rec.get(key, '').strip())]


def _wearables(export_dir):
    """Every ARMO and CLOT record of the export."""
    for name in ('ARMO.txt', 'CLOT.txt'):
        yield from iter_records(Path(export_dir) / name)


def build_skin_fill(export_dir) -> dict:
    """Morrowind worn mesh path -> its SkinFill (partitions its ARMA hides, parts it covers).

    The hidden partitions are every section of the skin files the ARMA hides:
    the ARMO's own body slots (a one-sided piece's own slot) plus the
    partitions its parts overlap, as `equipment._arma_bod2` writes them.
    See: docs/commentary/asset_convert_armor.md#morrowind-skin-fill
    """
    out = {}
    for rec in _wearables(export_dir):
        slots = part_slots(rec)
        if not slots:
            continue
        sided = sided_slot(rec)
        own = {sided} if sided else body_parts_for_flags(int(rec.get('BMDT.BipedFlags', '0') or 0))
        hidden = hidden_skin(covered_partitions(slots) | frozenset(own))
        for model in _worn_models(rec) if hidden else ():
            out[model] = SkinFill(hidden, frozenset(slots))
    return out


def build_sided_slots(export_dir) -> dict:
    """Morrowind worn mesh path -> the Skyrim slot of the one-sided piece wearing it.

    See: docs/commentary/asset_convert_armor.md#body-slot-layout
    """
    out = {}
    for rec in _wearables(export_dir):
        slot = sided_slot(rec)
        for model in _worn_models(rec) if slot else ():
            out[model] = slot
    return out

# TES4 BMDT biped bits 2=UpperBody 3=LowerBody 4=Hand 5=Foot — the gear the
# vanilla weight slider applies to.  Mirrors _build_arma's `use_slider`.
_SLIDER_BIPED_MASK = 0b111100

# Variant flags
BASE = 1        # <name>.nif
W0 = 2          # <name>_0.nif
W1 = 4          # <name>_1.nif
WORN = 8        # named as an ARMA worn (biped) model by some ARMO/CLOT record
FEMALE = 16
MALE = 32
#: Named as an AMMO model: hangs on the QUIVER node when its NIF carries no Prn.
QUIVER = 64

#: Morrowind WPDT type -> Prn, Oblivion's vocabulary. See: docs/commentary/asset_convert_armor.md#morrowind-weapons
MORROWIND_WEAPON_PRN = {0: 'WeaponDagger', 1: 'WeaponSword', 2: 'BackWeapon',
                        3: 'WeaponMace', 4: 'BackWeapon', 5: 'BackWeapon',
                        6: 'BackWeapon', 7: 'WeaponAxe', 8: 'BackWeapon',
                        9: 'BackWeapon', 10: 'BackWeapon', 11: 'WeaponDagger'}

# TES4 BMDT biped bit -> the Skyrim body part the geometry belongs in.  This is
# the plugin's OWN statement of what the item is, so it replaces guessing the
# slot from the filename ('helm' in the stem) or from the geometry name.
# Bit 0 Head, 1 Hair, 2 UpperBody, 3 LowerBody, 4 Hand, 5 Foot.
_SBP_131_HAIR = 131
_SBP_32_BODY = 32
_SBP_49_LOWER_BODY = 49
_SBP_33_HANDS = 33
_SBP_37_FEET = 37
_SBP_36_RING = 36
_SBP_40_NECK = 40
_BIPED_BIT_BODY_PART = [
    (0, _SBP_131_HAIR),        # Head  -> helmets ride Skyrim's hair slot
    (1, _SBP_131_HAIR),        # Hair
    (2, _SBP_32_BODY),
    (3, _SBP_49_LOWER_BODY),
    (4, _SBP_33_HANDS),
    (5, _SBP_37_FEET),
    # Jewellery.  Bit meanings per xEdit wbBipedFlags (wbDefinitionsTES4.pas):
    # 6 Right Ring, 7 Left Ring, 8 Amulet -- NOT 7=amulet/8=tail (tail is 15).
    # Slots measured from vanilla Skyrim: goldring_1.nif partitions as 36,
    # amulet.nif as 40.  Listed last so a ring that also claims a body slot is
    # still slotted by the body one.
    (6, _SBP_36_RING),         # Right Ring
    (7, _SBP_36_RING),         # Left Ring
    (8, _SBP_40_NECK),         # Amulet
]


def body_part_for_flags(biped_flags: int):
    """Skyrim body part implied by a record's BMDT biped flags, or None.

    Head/Hair win when set: a helmet that also claims UpperBody is still
    headgear.  None means the flags say nothing useful and the caller should
    fall back to inspecting the mesh.
    """
    for bit, bp in biped_bit_body_parts(_BIPED_BIT_BODY_PART):
        if biped_flags & (1 << bit):
            return bp
    return None


def norm_model_path(path: str) -> str:
    """Normalize an export model path to a lowercase mesh-relative key.

    The export escapes backslashes, so a model path arrives as
    'armor\\\\fur\\\\m\\\\gauntlets.nif' — collapse the doubling, or every key
    ends up with '//' separators and never matches a real relative path.
    """
    p = path.strip().lower().replace('\\\\', '\\').replace('\\', '/')
    while '//' in p:
        p = p.replace('//', '/')
    return p.lstrip('/')


def iter_records(txt: Path):
    if not txt.is_file():
        return
    body = txt.read_text(encoding='utf-8', errors='replace')
    for chunk in body.split('---RECORD_BEGIN---')[1:]:
        rec = {}
        for line in chunk.split('---RECORD_END---')[0].splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            rec[k] = v
        if rec:
            yield rec


def build_biped_flags(export_dir) -> dict:
    """Map mesh-relative NIF path -> the BMDT biped flags of the record wearing it.

    The authored answer to 'what slot is this?', which the converter previously
    guessed from the filename stem.  A mesh worn by several records ORs their
    flags together; in practice they agree.
    """
    export_dir = Path(export_dir)
    flags: dict = {}
    for name in ('ARMO.txt', 'CLOT.txt'):
        for rec in iter_records(export_dir / name):   # noqa: plugin-path (record/manifest filename)
            try:
                bf = int(rec.get('BMDT.BipedFlags', '0') or 0)
            except ValueError:
                continue
            if not bf:
                continue
            for key in ('Male.BipedModel.MODL', 'Female.BipedModel.MODL'):
                mp = rec.get(key, '').strip()
                if mp:
                    k = norm_model_path(mp)
                    flags[k] = flags.get(k, 0) | bf
    return flags


#: Biped-flag sub-map key; norm_model_path never emits it, so it cannot collide with a mesh entry.
BIPED_FLAGS_KEY = '*biped_flags*'

#: Weapon Prn sub-map key; norm_model_path can never emit it either.
WEAPON_PRN_KEY = '*weapon_prn*'

#: Skin-fill sub-map key: a Morrowind worn mesh -> the body partitions its ARMA hides.
SKIN_FILL_KEY = '*skin_fill*'

#: Sided-slot sub-map key: a Morrowind one-sided piece's worn mesh -> its Skyrim slot.
SIDED_SLOT_KEY = '*sided_slot*'

#: Sub-maps a base's plan merges into instead of replacing.
_NESTED_KEYS = (BIPED_FLAGS_KEY, WEAPON_PRN_KEY, SKIN_FILL_KEY, SIDED_SLOT_KEY)


def biped_flags_for(plan: dict, src_path, meshes_root) -> int:
    """BMDT biped flags for a source NIF, or 0 when no record wears it.

    Accepts the plan dict returned by build_plan (the flag map rides along
    under BIPED_FLAGS_KEY) or a bare flag map.
    """
    if not plan:
        return 0
    flags = plan.get(BIPED_FLAGS_KEY)
    if not isinstance(flags, dict):
        flags = plan
    try:
        rel = os.path.relpath(str(src_path), str(meshes_root))
    except (ValueError, TypeError):
        return 0
    val = flags.get(norm_model_path(rel), 0)
    return val if isinstance(val, int) else 0


def body_parts_for_flags(biped_flags: int) -> list:
    """EVERY Skyrim body part a record's BMDT flags claim.

    body_part_for_flags returns the single most head-ward one; this returns the
    whole set, which is what a mesh holding several shapes needs to be resolved
    against (see skin_retarget._body_part_from_skin_bones).
    """
    out = []
    for bit, bp in biped_bit_body_parts(_BIPED_BIT_BODY_PART):
        if biped_flags & (1 << bit) and bp not in out:
            out.append(bp)
    return out


def build_weapon_prns(export_dir) -> dict:
    """Mesh-relative NIF path -> the Prn a Morrowind weapon record gives it.

    A 4.0.0.2 weapon carries no Prn; the WPDT type is the authored answer, and
    a staff is the one keyword Oblivion's own refinement keeps.
    See: docs/commentary/asset_convert_armor.md#morrowind-weapons
    """
    out = {}
    for rec in iter_records(Path(export_dir) / 'WEAP.txt'):
        model = rec.get('Model.MODL', '').strip()
        kind = rec.get('MorrowindWeaponType', '').strip()
        if not model or not kind.isdigit():
            continue
        prn = MORROWIND_WEAPON_PRN.get(int(kind))
        if prn and 'staff' in os.path.basename(norm_model_path(model)):
            prn = 'WeaponStaff'
        if prn:
            out[norm_model_path(model)] = prn
    return out


def _want_ammo(export_dir: Path, want):
    """Flag every AMMO record's model as a QUIVER attachment."""
    for rec in iter_records(export_dir / 'AMMO.txt'):
        want(rec.get('Model.MODL', ''), QUIVER | BASE)


def _inherit(plan: dict, inherited: dict) -> None:
    """Lay a base's plan under `plan`; the nested maps merge instead of replacing."""
    for k, v in inherited.items():
        if k in _NESTED_KEYS:
            plan.setdefault(k, {}).update(v)
        else:
            plan[k] = v


def build_plan(export_dir, _seen=None) -> dict:
    """Map mesh-relative NIF path -> bitmask of the variants the plugin uses.

    *export_dir* is the per-plugin export directory (e.g. export/Oblivion.esm).
    Paths absent from the result are referenced by no ARMO/CLOT record.

    The BASE's records count too.  An asset-only mod -- a retexture stack, or
    an ordered merge -- has no ARMO/CLOT of its own, so on its own evidence no
    mesh is ever worn and no weight variants are written.  The armour then
    falls back to the base conversion's `_0`/`_1` pair, which was decided
    without any of the mod's textures, and every improvement to those meshes is
    silently unused.  Worn gear is exactly what benefits most from a specular
    map, so this is not a corner case.

    The base's plan is laid down FIRST and the tree's own records are merged on
    top, so a mod that does ship ARMO/CLOT still wins for the meshes it names.
    """
    export_dir = Path(export_dir)
    plan: dict = {}

    from asset_convert.sources import base_plugins
    # `_seen` closes a base CYCLE.  A chain is user-authored (`--base` at
    # one end, `_HEADER.txt` masters at the other), so nothing stops it
    # looping; comparing against export_dir alone catches only A->A, and
    # A->B->A recursed until the interpreter died.
    _seen = set(_seen or ())
    _seen.add(Path(export_dir).resolve())
    for base in base_plugins.export_dirs(export_dir):
        if Path(base).resolve() in _seen:
            continue
        _inherit(plan, build_plan(base, _seen))

    def want(path: str, flags: int):
        if path:
            key = norm_model_path(path)
            plan[key] = plan.get(key, 0) | flags

    for name in ('ARMO.txt', 'CLOT.txt'):
        for rec in iter_records(export_dir / name):   # noqa: plugin-path (record/manifest filename)
            male_biped = rec.get('Male.BipedModel.MODL', '').strip()
            female_biped = rec.get('Female.BipedModel.MODL', '').strip()
            male_world = rec.get('Male.WorldModel.MODL', '').strip()
            female_world = rec.get('Female.WorldModel.MODL', '').strip()
            try:
                biped_flags = int(rec.get('BMDT.BipedFlags', '0') or 0)
            except ValueError:
                biped_flags = 0

            # ARMA worn models (MOD2/MOD3): _1 + engine-derived _0 when the
            # slider is on, otherwise the plain mesh.  WORN rides along on every
            # biped reference — it is what marks the mesh as body-worn gear, a
            # fact only the plugin knows (see is_worn).
            worn_flags = WORN | (
                (W0 | W1) if (biped_flags & _SLIDER_BIPED_MASK) else BASE)
            want(male_biped, worn_flags | MALE)
            want(female_biped or male_biped, worn_flags | FEMALE)

            # ARMO ground models (MOD2/MOD4): always the plain mesh, and the
            # biped mesh stands in when the record ships no world model.
            # A female-only wearable has no male field at all, so the fallback
            # has to reach across genders or its dropped item has no mesh —
            # this must track convert_ARMO's ground_model expression exactly.
            want(male_world or male_biped or female_world or female_biped, BASE)
            want(female_world, BASE)

    _want_ammo(export_dir, want)
    # Carry the authored slot data alongside, so callers that need to know what
    # a mesh IS (not just which variants to write) do not re-parse the export.
    # Merged, not assigned: an inherited base's flags are already in here
    # (see the base_plugins loop above) and this tree's own must win
    # per entry rather than replacing the map wholesale.
    plan.setdefault(BIPED_FLAGS_KEY, {}).update(build_biped_flags(export_dir))
    plan.setdefault(WEAPON_PRN_KEY, {}).update(build_weapon_prns(export_dir))
    plan.setdefault(SKIN_FILL_KEY, {}).update(build_skin_fill(export_dir))
    plan.setdefault(SIDED_SLOT_KEY, {}).update(build_sided_slots(export_dir))
    return plan


def variants_for(plan: dict, src_path, meshes_root) -> int:
    """Variant bitmask for a source NIF, or BASE if the plugin never names it.

    Meshes no ARMO/CLOT references (loose test assets, unused BSA content) keep
    their plain conversion and gain no weight variants.
    """
    try:
        rel = os.path.relpath(str(src_path), str(meshes_root))
    except ValueError:
        return BASE
    return plan.get(norm_model_path(rel), BASE)


_LATCH = [0, None, None, None]


def latch_variants(plan: dict, src_path, meshes_root):
    """Latch the plugin's variants, weapon Prn, skin fill and sided slot for the NIF to convert."""
    _LATCH[0] = variants_for(plan, src_path, meshes_root) if plan else 0
    _LATCH[1] = weapon_prn_for(plan, src_path, meshes_root) if plan else None
    _LATCH[2] = _sub_map_entry(plan, SKIN_FILL_KEY, src_path, meshes_root, None)
    _LATCH[3] = _sub_map_entry(plan, SIDED_SLOT_KEY, src_path, meshes_root, None)


def _sub_map_entry(plan, key: str, src_path, meshes_root, default):
    """`src_path`'s entry in the plan's `key` sub-map, or `default`."""
    try:
        rel = os.path.relpath(str(src_path), str(meshes_root))
    except (ValueError, TypeError):
        return default
    return (plan or {}).get(key, {}).get(norm_model_path(rel), default)


def mesh_skin_fill() -> SkinFill:
    """Body partitions to fill with skin in the NIF being converted."""
    return _LATCH[2]


def mesh_sided_slot():
    """The Skyrim slot of the one-sided Morrowind piece being converted, or None."""
    return _LATCH[3]


def weapon_prn_for(plan: dict, src_path, meshes_root):
    """The Prn a weapon record gives this NIF, or None."""
    try:
        rel = os.path.relpath(str(src_path), str(meshes_root))
    except ValueError:
        return None
    return plan.get(WEAPON_PRN_KEY, {}).get(norm_model_path(rel))


def mesh_weapon_prn():
    """The Prn the plugin's WEAP record gives the NIF being converted, or None."""
    return _LATCH[1]


def mesh_is_female(src_path) -> bool:
    """Fitted to the female body: a Female.BipedModel, else Oblivion's f/ folder."""
    v = _LATCH[0]
    female = bool(v & FEMALE) and not v & MALE
    return female or '/f/' in str(src_path).replace(chr(92), '/').lower()


def mesh_is_ammo() -> bool:
    """True if some AMMO record names the NIF being converted as its model.
    See: docs/commentary/asset_convert_falloutnv.md#ammo-prn
    """
    return bool(_LATCH[0] & QUIVER)


def is_worn(plan: dict, src_path, meshes_root) -> bool:
    """True if some ARMO/CLOT record wears this NIF on the body.

    The converter used to answer this by looking for 'armor' or 'clothes' in the
    source path.  That holds for vanilla Oblivion, which files every wearable
    under meshes\\armor or meshes\\clothes, but it is a guess about a naming
    convention rather than a fact about the plugin — Nehrim ships 88 worn meshes
    under its own folders (eyren/, spinat/, nehrim/, skeletonk/, ...) and every
    one of them was converted as a world object: BSFadeNode root instead of
    NiNode, plain NiSkinInstance instead of BSDismemberSkinInstance, and no
    retarget onto the Skyrim skeleton, so the engine draws nothing where the
    body should be.  The plugin's own biped model references are the authored
    answer, so ask them.
    """
    if not plan:
        return False
    try:
        rel = os.path.relpath(str(src_path), str(meshes_root))
    except (ValueError, TypeError):
        return False
    return bool(plan.get(norm_model_path(rel), 0) & WORN)
