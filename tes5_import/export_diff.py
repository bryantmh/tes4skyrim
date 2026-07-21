"""Author-intent diffing between a master's export and a plugin's export.

The rule for converting an override is: take the master's CONVERTED record
exactly, then replace only the fields the plugin's AUTHOR changed. This module
answers "what did the author change?" — and it answers it from the two TES4
exports, never by comparing two conversion runs.

Why that distinction matters. Diffing conversions conflates two things:

    the author changed this field        (real override content)
    our pass re-derived it differently   (an artifact of less context)

and the converter cannot tell them apart. Nehrim's Translation.esp made that
concrete: comparing conversions reported 1821 changed NPC_ `RNAM` values, so
every NPC's race was rewritten to a vanilla Skyrim race and the game hung on
load. Comparing exports reports ZERO changed RNAMs, because the author never
touched a single one. A field that neither export changes can never drift.

Order-insensitivity is the other half. Oblivion does not preserve list order
between a master and a plugin that overrides it: 1166 of 1264 Nehrim NPC_
inventories differ positionally while only 5 differ as a set. A positional
diff would rewrite 1161 inventories for no reason.
"""

import re

# Indexed export keys look like `Item[3].FormID` / `Spell[0]`.
_INDEXED_RE = re.compile(r'^(?P<name>[A-Za-z0-9_.]+)\[(?P<idx>\d+)\](?P<rest>.*)$')

# Keys that are bookkeeping rather than authored content. Comparing them
# produces spurious differences: a list's count follows from the list itself,
# and the record's own identity is not a field anyone "changed".
_IGNORED_KEYS = frozenset({
    'FormID',
    'Signature',
    'EditorID',
})

# Counts derived from an indexed list end in 'Count' (ItemCount, SpellCount,
# AIPackageCount, TargetCount, ...). The list comparison already covers any
# real change, and Oblivion re-counts them per file.
def _is_count_key(key: str) -> bool:
    return key.endswith('Count')


# Per-list field normalisers, for TES4 fields whose export value carries
# UNINITIALISED CS MEMORY alongside the real data. QSTA 'Flags' is a u8
# (Compass Marker Ignores Locks) followed by 3 unused garbage bytes
# (wbDefinitionsTES4: wbInteger(itU8) + wbUnused(3)) — comparing the raw u32
# reported 58 quest-target "changes" whose meaningful byte was identical.
def _mask_u8(value: str) -> str:
    try:
        return str(int(value) & 0xFF)
    except (ValueError, TypeError):
        return value


_LIST_FIELD_NORMALIZERS = {
    ('Target', 'Flags'): _mask_u8,
}


def _split_indexed(record: dict) -> tuple:
    """Partition a record into (scalars, {list_name: {index: {field: value}}})."""
    scalars = {}
    lists = {}
    for key, value in record.items():
        m = _INDEXED_RE.match(key)
        if not m:
            scalars[key] = value
            continue
        name = m.group('name')
        idx = int(m.group('idx'))
        field = m.group('rest').lstrip('.') or ''
        lists.setdefault(name, {}).setdefault(idx, {})[field] = value
    return scalars, lists


def _list_as_multiset(name: str, entries: dict) -> list:
    """Normalise one indexed list into an order-independent comparable form."""
    out = []
    for _idx, fields in sorted(entries.items()):
        normed = []
        for field, value in sorted(fields.items()):
            fn = _LIST_FIELD_NORMALIZERS.get((name, field))
            normed.append((field, fn(value) if fn else value))
        out.append(tuple(normed))
    return sorted(out)


def diff_records(master_rec: dict, plugin_rec: dict) -> dict:
    """What the plugin's author changed, relative to the master.

    Returns {key: plugin_value} for scalar keys, plus {list_name: True} for
    indexed lists whose CONTENTS differ as a multiset. An empty result means
    the plugin's record is authorially identical to the master's, so the
    override should be dropped rather than emitted.
    """
    m_scalars, m_lists = _split_indexed(master_rec)
    p_scalars, p_lists = _split_indexed(plugin_rec)

    changed = {}
    for key in set(m_scalars) | set(p_scalars):
        if key in _IGNORED_KEYS or _is_count_key(key):
            continue
        if m_scalars.get(key) != p_scalars.get(key):
            changed[key] = p_scalars.get(key)

    for name in set(m_lists) | set(p_lists):
        m_entries = _list_as_multiset(name, m_lists.get(name, {}))
        p_entries = _list_as_multiset(name, p_lists.get(name, {}))
        if m_entries != p_entries:
            changed[name + '[]'] = True

    return changed


def changed_keys(master_rec: dict, plugin_rec: dict) -> set:
    """Just the set of changed keys (see diff_records)."""
    return set(diff_records(master_rec, plugin_rec))
