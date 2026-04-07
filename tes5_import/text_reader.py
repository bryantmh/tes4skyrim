"""
Text record parser — reads KEY=VALUE export files back into dictionaries.

Each record is delimited by ---RECORD_BEGIN--- and ---RECORD_END---.
Lines starting with # are comments. Values are unescaped.
"""

import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

_WORKER_COUNT = max(1, (os.cpu_count() or 4) - 2)


def unescape_value(value: str) -> str:
    """Unescape special characters from export format."""
    result = []
    i = 0
    while i < len(value):
        if value[i] == '\\' and i + 1 < len(value):
            c = value[i + 1]
            if c == 'n':
                result.append('\n')
            elif c == 'r':
                result.append('\r')
            elif c == 't':
                result.append('\t')
            elif c == '\\':
                result.append('\\')
            else:
                result.append(c)
            i += 2
        else:
            result.append(value[i])
            i += 1
    return ''.join(result)


def parse_record_block(lines: list) -> dict:
    """Parse a single record block (list of KEY=VALUE lines) into a dict.

    Returns a dict where keys map to values. Duplicate keys get list values.
    Special handling for indexed keys like Item[0].FormID — stored as nested lists.
    """
    record = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        eq = line.find('=')
        if eq < 0:
            continue
        key = line[:eq]
        value = unescape_value(line[eq + 1:])

        if key in record:
            # Convert to list if duplicate key
            existing = record[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                record[key] = [existing, value]
        else:
            record[key] = value

    return record


def parse_export_file(filepath: str) -> list:
    """Parse an entire export file into a list of record dicts.

    Each dict has at minimum: Signature, FormID, EditorID (if present).
    """
    records = []
    current_lines = None

    with open(filepath, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if line == '---RECORD_BEGIN---':
                current_lines = []
            elif line == '---RECORD_END---':
                if current_lines is not None:
                    record = parse_record_block(current_lines)
                    if record:
                        records.append(record)
                current_lines = None
            elif current_lines is not None:
                current_lines.append(line)

    return records


def parse_export_directory(export_dir: str, type_filter: set = None) -> list:
    """Parse all per-type export files from a directory in parallel.

    Returns a list of record dicts, optionally filtered by type.
    Deduplicates records by FormID (keeps the last occurrence).
    """
    all_records = []
    if not os.path.isdir(export_dir):
        return all_records

    # Collect files to parse
    tasks = []
    for txt_file in sorted(os.listdir(export_dir)):
        if not txt_file.endswith('.txt') or txt_file == '_HEADER.txt':
            continue
        sig = txt_file.replace('.txt', '').replace('_SKIP', '')
        if type_filter and sig not in type_filter:
            continue
        tasks.append(os.path.join(export_dir, txt_file))

    # Parse files in parallel (I/O + parsing is the bottleneck)
    results_by_task = {}
    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as ex:
        future_map = {ex.submit(parse_export_file, fp): fp for fp in tasks}
        for future in as_completed(future_map):
            fp = future_map[future]
            results_by_task[fp] = future.result()

    # Preserve sorted order for deterministic output
    for fp in tasks:
        all_records.extend(results_by_task.get(fp, []))

    # Deduplicate by FormID (keep last occurrence)
    seen = {}
    for i, rec in enumerate(all_records):
        fid = rec.get('FormID')
        if fid:
            seen[fid] = i
    if len(seen) < len(all_records):
        keep = set(seen.values())
        all_records = [rec for i, rec in enumerate(all_records) if i in keep]

    return all_records


def group_records_by_type(records: list) -> dict:
    """Group record dicts by their Signature field."""
    by_type = defaultdict(list)
    for rec in records:
        sig = rec.get('Signature', '')
        if sig:
            by_type[sig].append(rec)
    return dict(by_type)


def get_int(record: dict, key: str, default: int = 0) -> int:
    """Get an integer value from a record dict."""
    val = record.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_float(record: dict, key: str, default: float = 0.0) -> float:
    """Get a float value from a record dict."""
    val = record.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_formid(record: dict, key: str, default: int = 0) -> int:
    """Get a FormID (hex string) as an integer, applying load order remapping."""
    val = record.get(key)
    if val is None:
        return default
    try:
        fid = int(val, 16)
        if fid and _formid_index_offset:
            # Shift high byte by offset (e.g., +1 when Skyrim.esm inserted at index 0)
            high = (fid >> 24) & 0xFF
            fid = ((high + _formid_index_offset) << 24) | (fid & 0x00FFFFFF)
        return fid
    except (ValueError, TypeError):
        return default


# Module-level FormID remapping: when converting TES4→TES5, the file's load
# order index changes because new masters (e.g., Skyrim.esm) are prepended.
_formid_index_offset = 0

def set_formid_index_offset(offset: int):
    """Set the load order index offset for FormID remapping.

    For Oblivion.esm with Skyrim.esm added as master: offset=1
    (all 0x00XXXXXX become 0x01XXXXXX).
    """
    global _formid_index_offset
    _formid_index_offset = offset


def get_formid_index_offset() -> int:
    """Return the current FormID load order index offset."""
    return _formid_index_offset


def get_str(record: dict, key: str, default: str = '') -> str:
    """Get a string value."""
    val = record.get(key)
    if val is None:
        return default
    return str(val)


def get_indexed_list(record: dict, prefix: str, field: str, count_key: str = None) -> list:
    """Get a list of indexed values like Item[0].FormID, Item[1].FormID, etc.

    Returns list of values, maintaining index order.
    """
    result = []
    i = 0
    while True:
        key = f"{prefix}[{i}].{field}" if field else f"{prefix}[{i}]"
        if key not in record:
            break
        result.append(record[key])
        i += 1
    return result


def get_indexed_dicts(record: dict, prefix: str, fields: list) -> list:
    """Get a list of indexed dictionaries from record.

    E.g., get_indexed_dicts(rec, 'Item', ['FormID', 'Count'])
    returns [{'FormID': '...', 'Count': '1'}, ...]
    """
    result = []
    i = 0
    while True:
        item = {}
        found = False
        for field in fields:
            key = f"{prefix}[{i}].{field}"
            if key in record:
                item[field] = record[key]
                found = True
        if not found:
            break
        result.append(item)
        i += 1
    return result
