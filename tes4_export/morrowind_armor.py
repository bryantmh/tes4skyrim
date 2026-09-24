"""
Morrowind wearables: the body parts each record wears and the worn model it names.

A Morrowind ARMO/CLOT is dressed from BODY meshes, one per `INDX` slot; the
worn model Skyrim needs does not exist yet. Each record names a synthetic one
and lists its parts, and the mesh stage assembles it.

See: docs/commentary/tes4_export_morrowind.md#worn-models
"""

import os

from source_paths import resolve_plugin_path

from .morrowind_ids import encode_editor_id
from .record_types.common import escape_value
from .tes3_reader import get_string, get_subrecord, read_file, read_masters

#: Folder the assembled worn models live in, under meshes\.
WORN_DIR = 'armor' + chr(92) + 'morrowind'

#: INDX body-part slot of the head; only a piece filling it hides the actor's head.
_HEAD_PART = 0

#: TES4 BMDT Head biped bit.
_HEAD_BIT = 0x1


def load_body_models(source_path: str, records, export_dir: str) -> dict:
    """BODY id -> mesh path, from `records` and every declared master's own install.

    See: docs/commentary/tes4_export_morrowind.md#vanilla-assets
    """
    models = body_models_from(
        resolve_plugin_path(master, os.path.dirname(source_path), export_dir)
        for master in read_masters(source_path))
    _add_body_models(models, records)
    return models


def body_models_from(paths) -> dict:
    """BODY id -> mesh path over the plugin files at `paths`, later files overriding."""
    models = {}
    for path in paths:
        if os.path.isfile(path):
            _add_body_models(models, read_file(path)[1])
    return models


def _add_body_models(models: dict, records) -> None:
    """Add every BODY's mesh path to `models`, later plugins overriding."""
    for rec in records:
        if rec.type != 'BODY' or rec.deleted:
            continue
        modl = get_subrecord(rec, 'MODL')
        if modl is not None:
            models[rec.record_id.lower()] = get_string(modl).replace('/', chr(92))


def wearable_parts(rec, body_models: dict) -> list:
    """(slot, male mesh, female mesh) per INDX; a part with no known mesh is dropped."""
    parts = []
    for sub in rec.subrecords:
        if sub.type == 'INDX' and sub.data:
            parts.append([sub.data[0], '', ''])
        elif sub.type in ('BNAM', 'CNAM') and parts:
            parts[-1][1 if sub.type == 'BNAM' else 2] = body_models.get(
                get_string(sub).lower(), '')
    return [tuple(p) for p in parts if p[1] or p[2]]


def _covered_biped(biped: int, parts: list) -> int:
    """`biped` without the Head bit when a part list exists and fills no Head part.

    See: docs/commentary/tes4_export_morrowind.md#equipment-slots
    """
    if parts and not any(slot == _HEAD_PART for slot, _m, _f in parts):
        return biped & ~_HEAD_BIT
    return biped


def worn_model_path(record_id: str, female: bool = False) -> str:
    """The synthetic worn mesh a record names, under WORN_DIR by gender."""
    return '%s%s%s%s%s.nif' % (WORN_DIR, chr(92), 'f' if female else 'm',
                               chr(92), encode_editor_id(record_id).lower())


def emit_worn_models(lines: list, rec, biped: int, ctx) -> None:
    """The biped flags, the worn models, and the body parts they are assembled from.

    Every piece with body parts names a worn model, a pauldron included
    although no Oblivion slot holds one.
    See: docs/commentary/asset_convert_armor.md#body-slot-layout
    """
    parts = wearable_parts(rec, ctx.body_models)
    lines.append(f'BMDT.BipedFlags={_covered_biped(biped, parts)}')
    if not parts:
        return
    lines.append('Male.BipedModel.MODL='
                 + escape_value(worn_model_path(rec.record_id)))
    if any(female for _, _, female in parts):
        lines.append('Female.BipedModel.MODL='
                     + escape_value(worn_model_path(rec.record_id, True)))
    lines.append(f'MorrowindPartCount={len(parts)}')
    for index, (slot, male, female) in enumerate(parts):
        lines.append(f'MorrowindPart[{index}].Slot={slot}')
        if male:
            lines.append(f'MorrowindPart[{index}].Male={escape_value(male)}')
        if female:
            lines.append(f'MorrowindPart[{index}].Female={escape_value(female)}')
