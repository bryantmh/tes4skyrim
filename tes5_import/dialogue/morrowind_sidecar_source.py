"""
What the MorrowindRuntime sidecar takes straight from the TES3 BINARIES.

Two things cannot come from one plugin's text export:

  * DIALOGUE IS CUMULATIVE. A plugin adds responses to its masters' topics,
    and an NPC answers from the union -- "join the Fighters Guild" is almost
    entirely Morrowind.esm's. Each INFO names the response it follows (PNAM)
    and precedes (NNAM), and the merged order IS the filter precedence.
  * THE AUTHORED STRINGS. An NPC's race, class and faction are names in TES3
    and the dialogue filter compares names; the export holds minted FormIDs.

So the plugin and every TES3 master that can be found are read here, once
each and in load order, and merged the way OpenMW's InfoOrder merges them.

See: docs/commentary/morrowind_runtime.md#sidecar
"""

import os

from asset_convert.sources import source_registry
from core.plugin_masters import get_masters_from_binary
from tes4_export.export_morrowind import format_record
from tes4_export.record_types.morrowind_dialog import (DIAL_SIG, INFO_SIG,
                                                       export_DIAL,
                                                       export_INFO, info_id)
from tes4_export.tes3_reader import get_string, get_subrecord, read_file

#: NPDT comes in two sizes; (disposition, rank) byte offsets in each.
_NPDT_FIELDS = {52: (44, 46), 12: (2, 4)}

#: NPC_ FLAG bit 0.
_FEMALE = 0x1


def _binary(root: str, plugin: str):
    """Where `plugin`'s binary is: an imported mod's copy, else a game dir."""
    found = source_registry.plugin_binary(root, plugin)
    if found:
        return str(found)
    folder = source_registry.directory_for(root, plugin)
    return os.path.join(folder, plugin) if folder else None


def plugin_chain(root: str, plugin: str) -> list:
    """`(name, path)` for each TES3 master that can be found, then `plugin`,
    in load order. A master that is not installed is reported and skipped."""
    own = _binary(root, plugin)
    if not own:
        return []
    chain = []
    for master in get_masters_from_binary(own):
        path = _binary(root, master)
        if path:
            chain.append((master, path))
        else:
            print(f'    sidecar: master {master} not found -- its dialogue '
                  f'and actors are left out')
    return chain + [(plugin, own)]


def _text(rec, sig: str) -> str:
    """A string subrecord, or ''."""
    sub = get_subrecord(rec, sig)
    return get_string(sub) if sub is not None else ''


def _place(order: list, entry: dict) -> None:
    """InfoOrder::insertInfo: replace in place, else after PNAM, else before
    NNAM, else first when it names no predecessor, else last."""
    ids = [row['id'] for row in order]
    if entry['id'] in ids:
        order[ids.index(entry['id'])] = entry
    elif entry['prev'] in ids:
        order.insert(ids.index(entry['prev']) + 1, entry)
    elif entry['next'] in ids:
        order.insert(ids.index(entry['next']), entry)
    elif not entry['prev']:
        order.insert(0, entry)
    else:
        order.append(entry)


def _merge_info(order: list, rec) -> None:
    """One INFO into its topic's order; a deleted one leaves it."""
    entry = {'id': info_id(rec), 'prev': _text(rec, 'PNAM'),
             'next': _text(rec, 'NNAM'), 'rec': rec}
    if rec.deleted:
        order[:] = [row for row in order if row['id'] != entry['id']]
    else:
        _place(order, entry)


def _actor_line(rec) -> str:
    """`id=race|class|faction|rank|disposition|female|name` for one NPC_."""
    data = get_subrecord(rec, 'NPDT')
    raw = data.data if data is not None else b''
    disposition_at, rank_at = _NPDT_FIELDS.get(len(raw), (None, None))
    flag = get_subrecord(rec, 'FLAG')
    flags = int.from_bytes(flag.data[:4], 'little') if flag is not None else 0
    fields = (_text(rec, 'RNAM'), _text(rec, 'CNAM'), _text(rec, 'ANAM'),
              raw[rank_at] if rank_at is not None else 0,
              raw[disposition_at] if disposition_at is not None else 50,
              1 if flags & _FEMALE else 0, _text(rec, 'FNAM'))
    return f'{rec.record_id}=' + '|'.join(str(field) for field in fields)


def _take(out: dict, rec, topic: str) -> str:
    """Fold one record into `out`; returns the topic INFOs now belong to."""
    if rec.type == 'DIAL':
        topic = rec.record_id.lower()
        if not rec.deleted:
            out['topics'][topic] = rec
            out['infos'].setdefault(topic, [])
    elif rec.type == 'INFO' and topic in out['infos']:
        _merge_info(out['infos'][topic], rec)
    elif rec.type == 'NPC_' and rec.record_id and not rec.deleted:
        out['actors'][rec.record_id.lower()] = _actor_line(rec)
    return topic


def gather(chain: list) -> dict:
    """`{'topics', 'infos', 'actors'}` over the whole chain, each plugin read
    ONCE, a later plugin overriding or extending an earlier one.

    `topics` is `{lower id: DIAL rec}`, `infos` `{lower id: [entry]}` in merged
    order, `actors` `{lower id: table line}`.
    """
    out = {'topics': {}, 'infos': {}, 'actors': {}}
    for _name, path in chain:
        topic = ''
        for rec in read_file(path)[1]:
            topic = _take(out, rec, topic)
    return out


def write_merged_dialogue(gathered: dict, out_dir: str) -> tuple:
    """MWDI.txt and MWIN.txt for the merged chain. Returns their counts."""
    dial_blocks, info_blocks = [], []
    for key, rec in gathered['topics'].items():
        dial_blocks.append(format_record(DIAL_SIG, rec.record_id,
                                         export_DIAL(rec)))
        for ordinal, entry in enumerate(gathered['infos'].get(key, [])):
            info_blocks.append(format_record(
                INFO_SIG, entry['id'],
                export_INFO(entry['rec'], ordinal, rec.record_id)))
    for name, blocks in (('MWDI.txt', dial_blocks), ('MWIN.txt', info_blocks)):
        with open(os.path.join(out_dir, name), 'w', encoding='utf-8') as fh:
            fh.write('\n\n'.join(blocks) + '\n')
    return len(dial_blocks), len(info_blocks)
