"""The apparatus sidecar TESRuntime's alchemy hooks read.

Every plugin that owns an apparatus, TES3 or TES4, writes
`SKSE/Plugins/TESRuntime/<plugin>.apparatus.json`: each apparatus with its
quality on Morrowind's scale. A TES3 source adds the potion GMSTs, the two
messages OpenMW shows, and the `player` record's Intelligence and Luck, all
taken from its merged master chain.

See: docs/commentary/tes_runtime_alchemy.md#alchemy-apparatus
"""

import json
import os

from core.plugin_masters import masters_from_export_header
from tes4_export.tes3_reader import is_tes3

from ..base.text_reader import unescape_value
from ..dialogue.morrowind_sidecar import (export_records, export_root,
                                          is_tes3_export)
from ..dialogue.morrowind_sidecar_source import source_binary
from .crime import SIDECAR_DIR

#: The export the sidecar is built from, and the fields it reads.
_APPARATUS_EXPORT = 'APPA.txt'
_APPARATUS_KEYS = ('EditorID', 'FormID', 'DATA.Type', 'DATA.Quality')

#: `(TES4 quality, TES3 quality)` at each grade Morroblivion ported, paired by EditorID; (0, 0) anchors the bottom.
_TES4_TO_TES3_QUALITY = ((0.0, 0.0), (10.0, 0.15), (25.0, 0.5), (50.0, 1.0),
                         (75.0, 1.2), (100.0, 1.5), (150.0, 2.0))

#: The GMSTs the runtime reads: the three potion multipliers and OpenMW's two messages.
_SETTINGS = ('fPotionStrengthMult', 'fPotionT1MagMult', 'fPotionT1DurMult',
             'sInventoryMessage3', 'sNotifyMessage45')

#: TES3's attribute order: Intelligence is second, Luck last.
_INTELLIGENCE = 1
_LUCK = 7


def tes3_quality(quality: float) -> float:
    """A TES4 apparatus quality on TES3's scale: straight-line between the
    grades Morroblivion ported, and along the last grade's slope past it.

    See: docs/commentary/tes_runtime_alchemy.md#apparatus-quality-scale
    """
    grades = list(zip(_TES4_TO_TES3_QUALITY, _TES4_TO_TES3_QUALITY[1:]))
    (low, low_tes3), (high, high_tes3) = next(
        (pair for pair in grades if quality <= pair[1][0]), grades[-1])
    return low_tes3 + (quality - low) * (high_tes3 - low_tes3) / (high - low)


def _source_is_tes3(export_dir: str, plugin: str) -> bool:
    """True when the plugin's binary is TES3; its export decides without one."""
    binary = source_binary(export_root(export_dir), plugin)
    if binary and os.path.isfile(binary):
        return is_tes3(binary)
    return is_tes3_export(export_dir)


def _apparatus_rows(export_dir: str, plugin: str) -> list:
    """Each apparatus this plugin OWNS -- the index byte matching its master
    count -- as `{id, form: [plugin, local id], type, quality}`, quality on
    TES3's scale."""
    own = f'{len(masters_from_export_header(export_dir)):02X}'
    tes3 = _source_is_tes3(export_dir, plugin)
    rows = []
    for rec in export_records(os.path.join(export_dir, _APPARATUS_EXPORT),
                              _APPARATUS_KEYS):
        formid = rec.get('FormID', '')
        if formid[:2].upper() != own or not rec.get('DATA.Type'):
            continue
        quality = float(rec.get('DATA.Quality') or 0.0)
        rows.append({'id': rec.get('EditorID') or formid,
                     'form': [plugin, int(formid, 16) & 0xFFFFFF],
                     'type': int(rec['DATA.Type']),
                     'quality': quality if tes3 else tes3_quality(quality)})
    return rows


def _settings(gathered: dict) -> dict:
    """The `_SETTINGS` GMSTs the merged TES3 chain defines, each `name=type,value`."""
    out = {}
    for name in _SETTINGS:
        line = gathered.get('gmsts', {}).get(name.lower(), '')
        kind, _, value = line.partition('=')[2].partition(',')
        if kind == 's':
            out[name] = unescape_value(value)
        elif kind in ('f', 'i'):
            out[name] = float(value)
    return out


def _player(gathered: dict) -> dict:
    """The `player` record's Intelligence and Luck, from its actor line's
    comma-joined attributes, second-last field; {} without one."""
    line = gathered.get('actors', {}).get('player', '')
    if not line:
        return {}
    attributes = line.rsplit('|', 2)[-2].split(',')
    return {'intelligence': int(attributes[_INTELLIGENCE]),
            'luck': int(attributes[_LUCK])}


def write_apparatus_sidecar(export_dir: str, output_path: str,
                            plugin_name: str, gathered=None) -> int:
    """Write `<plugin>.apparatus.json` when the plugin owns an apparatus, and
    delete a stale one when it owns none. `gathered` is a TES3 chain's
    `morrowind_sidecar_source.gather`. Returns files written."""
    plugin = os.path.basename(plugin_name)
    path = os.path.join(os.path.dirname(output_path), SIDECAR_DIR,
                        f'{os.path.splitext(plugin)[0]}.apparatus.json')
    rows = _apparatus_rows(export_dir, plugin)
    if not rows:
        if os.path.isfile(path):
            os.remove(path)
        return 0
    doc = {'version': 1, 'plugin': plugin, 'apparatus': rows}
    if gathered:
        doc['settings'] = _settings(gathered)
        doc['player'] = _player(gathered)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(doc, handle, indent=1, sort_keys=True)
    return 1
