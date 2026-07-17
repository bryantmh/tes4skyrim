"""
Text record parser — reads KEY=VALUE export files back into dictionaries.

Each record is delimited by ---RECORD_BEGIN--- and ---RECORD_END---.
Lines starting with # are comments. Values are unescaped.
"""

import mmap
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

_WORKER_COUNT = max(1, (os.cpu_count() or 4) - 2)

# Byte size of one parse job. Big files (LAND.txt is ~1.4 GB) are split into
# ranges of this size so parsing spreads across every worker instead of one
# worker owning one whole file.
_PARSE_CHUNK_BYTES = 16 * 1024 * 1024


def unescape_value(value: str) -> str:
    """Unescape special characters from export format."""
    # Fast path: the overwhelming majority of values (all numeric/hex data,
    # FormIDs, most strings) contain no escapes at all. `in` scans at C speed;
    # the char-by-char Python loop below is ~100x slower.
    if '\\' not in value:
        return value
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


_DELIM_BEGIN = b'---RECORD_BEGIN---'
_DELIM_END = b'---RECORD_END---'


def _find_delim_line(buf, needle: bytes, pos: int) -> int:
    """Find *needle* at *pos* or later where it forms a whole line.

    A match only counts if it starts at the beginning of a line and is
    followed by a line break (or EOF) — mirroring the old line-based parser,
    which compared entire lines, so a delimiter string embedded inside a
    value never terminates a record.
    """
    n = len(buf)
    while True:
        i = buf.find(needle, pos)
        if i < 0:
            return -1
        j = i + len(needle)
        if (i == 0 or buf[i - 1:i] == b'\n') and \
                (j >= n or buf[j:j + 1] in (b'\r', b'\n')):
            return i
        pos = i + 1


def parse_file_range(args: tuple) -> list:
    """Parse the records whose ---RECORD_BEGIN--- line starts in [start, end).

    args = (filepath, start, end). Module-level so it is picklable for
    ProcessPoolExecutor on Windows (spawn). Chunk boundaries partition the
    file: every record belongs to exactly one chunk (the one containing its
    BEGIN delimiter's byte offset), so parsing ranges in order reproduces the
    whole-file parse exactly.
    """
    filepath, start, end = args
    records = []
    with open(filepath, 'rb') as f:
        try:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:  # empty file
            return records
        try:
            begin = _find_delim_line(mm, _DELIM_BEGIN, start)
            while begin != -1 and begin < end:
                nl = mm.find(b'\n', begin)
                if nl < 0:
                    break
                rec_end = _find_delim_line(mm, _DELIM_END, nl + 1)
                if rec_end < 0:
                    break
                block = mm[nl + 1:rec_end].decode('utf-8')
                record = parse_record_block(block.splitlines())
                if record:
                    records.append(record)
                begin = _find_delim_line(mm, _DELIM_BEGIN,
                                         rec_end + len(_DELIM_END))
        finally:
            mm.close()
    return records


def parse_export_file(filepath: str) -> list:
    """Parse an entire export file into a list of record dicts.

    Each dict has at minimum: Signature, FormID, EditorID (if present).
    """
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return []
    return parse_file_range((filepath, 0, size))


def parse_export_directory(export_dir: str, type_filter: set = None) -> list:
    """Parse all per-type export files from a directory in parallel.

    Returns a list of record dicts, optionally filtered by type.
    Deduplicates records by FormID (keeps the last occurrence).

    Parsing is pure-Python string work that holds the GIL, so a *process*
    pool is used (threads serialise on one core here). Files are split into
    byte ranges so huge files (LAND.txt ~1.4 GB) spread across all workers;
    results are concatenated in job order, which keeps the record order
    identical to a serial whole-file parse.
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

    # Build (file, start, end) range jobs
    jobs = []
    for fp in tasks:
        try:
            size = os.path.getsize(fp)
        except OSError:
            continue
        if size == 0:
            continue
        for start in range(0, size, _PARSE_CHUNK_BYTES):
            jobs.append((fp, start, min(start + _PARSE_CHUNK_BYTES, size)))

    workers = min(_WORKER_COUNT, len(jobs))
    if workers <= 1:
        chunk_results = [parse_file_range(job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            # ex.map preserves job order -> deterministic record order.
            chunk_results = list(ex.map(parse_file_range, jobs))

    for chunk in chunk_results:
        all_records.extend(chunk)

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


# Engine-hardcoded FormIDs that exist in NO data file: references to them must
# never be shifted to our load index, or they dangle (the CK's "Unable to find
# Package Target Reference (01000014)" — 144 packages plus 5 quest aliases all
# pointing at a remapped PlayerRef). Skyrim hardcodes the same ids. NOTE: most
# other low ids (Tamriel WRLD 0x3C, gold MISC 0xF, Player NPC_ 0x7, ...) DO
# exist as real records in Oblivion.esm and must keep remapping normally.
_ENGINE_FIXED_FORMIDS = frozenset({0x14})   # PlayerRef


def get_formid(record: dict, key: str, default: int = 0) -> int:
    """Get a FormID (hex string) as an integer, applying load order remapping."""
    val = record.get(key)
    if val is None:
        return default
    try:
        fid = int(val, 16)
        if (fid and _formid_index_offset
                and fid not in _ENGINE_FIXED_FORMIDS):
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
