"""
Text record parser — reads KEY=VALUE export files back into dictionaries.

Each record is delimited by ---RECORD_BEGIN--- and ---RECORD_END---.
Lines starting with # are comments. Values are unescaped.
"""

import math
import mmap
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.worker_budget import worker_count

_WORKER_COUNT = worker_count()

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


DELIM_BEGIN = b'---RECORD_BEGIN---'
DELIM_END = b'---RECORD_END---'


def find_delim_line(buf, needle: bytes, pos: int) -> int:
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
            begin = find_delim_line(mm, DELIM_BEGIN, start)
            while begin != -1 and begin < end:
                nl = mm.find(b'\n', begin)
                if nl < 0:
                    break
                rec_end = find_delim_line(mm, DELIM_END, nl + 1)
                if rec_end < 0:
                    break
                block = mm[nl + 1:rec_end].decode('utf-8')
                record = parse_record_block(block.splitlines())
                if record:
                    records.append(record)
                begin = find_delim_line(mm, DELIM_BEGIN,
                                        rec_end + len(DELIM_END))
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


def info_result_script(rec: dict) -> str:
    """An INFO's whole result script: its Begin text, then FO3/FNV's End text.

    See: docs/commentary/tes4_export_falloutnv.md#info-end-script
    """
    parts = (rec.get('ResultScript') or '', rec.get('ResultScriptEnd') or '')
    return '\n'.join(p for p in parts if p)


def _export_files(export_dir: str, type_filter: set, exclude: set) -> list:
    """The per-type export files to parse, in sorted order.

    `exclude` names signatures never to READ: a record whose id is not a hex
    FormID cannot enter the pipeline at all, so dropping it later is too late.
    """
    tasks = []
    for txt_file in sorted(os.listdir(export_dir)):
        if not txt_file.endswith('.txt') or txt_file == '_HEADER.txt':
            continue
        sig = txt_file.replace('.txt', '').replace('_SKIP', '')
        if type_filter and sig not in type_filter:
            continue
        if exclude and sig in exclude:
            continue
        tasks.append(os.path.join(export_dir, txt_file))
    return tasks


def parse_export_directory(export_dir: str, type_filter: set = None,
                           exclude: set = None) -> list:
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

    tasks = _export_files(export_dir, type_filter, exclude)

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

    # Deduplicate by FormID (keep last occurrence).
    #
    # A FormID of all zeros is NOT an identity and must never be deduplicated
    # on: real plugins ship records with one. ElsweyrAnequina.esp has 7 LAND
    # records whose FormID is literally 0x00000000 (confirmed by reading the
    # original Oblivion .esp — the mod's own data, not an export defect), and
    # collapsing them onto the single key '00000000' discarded 6 of the 7.
    # Their cells then shipped a children group of references with no LAND in
    # it, and since a plugin's child GRUP REPLACES the master's, the terrain
    # under them vanished in-game while the placed clutter still rendered.
    # Keyed by identity instead so each survives as its own record.
    seen = {}
    for i, rec in enumerate(all_records):
        fid = rec.get('FormID')
        if fid and fid.strip('0'):
            seen[fid] = i
        else:
            seen[('\0null', i)] = i
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


# The +/-FLT_MAX sentinel. Anything at or past it is a poison marker rather
# than data: it is what a float field holds when a tool wrote "no value", and
# no real record quantity in either game reaches 3.4e38.
_FLT_MAX = 3.4028234663852886e+38


def get_float(record: dict, key: str, default: float = 0.0) -> float:
    """Get a float value from a record dict.

    NaN and +/-FLT_MAX fall back to `default`. Neither is legal record data
    in either game, but real plugins ship both -- TWMP Valenwood/Elsweyr has
    505 REFRs whose DATA RotZ is literally 0x7FC00000 (NaN) or 0xFF7FFFFF
    (-FLT_MAX), authored by whatever tool made the plugin. The export is a
    pure dump, so those bytes arrive here verbatim and `float("nan")` parses
    without complaint; the poison then flows into the written ESP and out
    again into the LODGen input, where the C# parser rejects the line
    ("Input string was not in a correct format" for NaN, "Value was either
    too large or too small for a Single" for the FLT_MAX literal, which
    "%.4f" expands to 40 digits) and the worldspace bakes zero .bto tiles.

    Note FLT_MAX is *finite*, so an isfinite() check alone does not catch it.
    Clamping at this choke point fixes every float consumer at once rather
    than at each one that happens to notice.
    """
    val = record.get(key)
    if val is None:
        return default
    try:
        f = float(val)
    except (ValueError, TypeError):
        return default
    if not math.isfinite(f) or abs(f) >= _FLT_MAX:
        return default
    return f


# The PLAYER's ids. Both games hardcode them at the SAME values, and Skyrim.esm
# — master index 0 of our output — defines them, so a reference to either must
# NEVER be shifted to our load index:
#   0x14  ACHR PlayerRef   (the placed reference)
#   0x07  NPC_ Player      (its base record; present in the
#                           references/Skyrim.esm dump as EditorID=Player)
#
# Shifting 0x14 dangles outright (the CK's "Unable to find Package Target
# Reference (01000014)" — 144 packages plus 5 quest aliases). Shifting 0x07 is
# worse because it does NOT dangle: Oblivion.esm has its own Player NPC_ at
# 0x07, so 01000007 silently resolves to the CONVERTED OBLIVION player instead
# of the real one. A package targeting "the player" then aims at an actor that
# is not the one you control and the procedure never engages — Morroblivion's
# chargen guard said "follow me" and stood still, his Escort package's PTDA
# pointing at 01000007.
#
# THE one definition for the import side — import these rather than declaring a
# local PLAYER_FID. (script_convert keeps its own `_PLAYER_FORMIDS` because it
# matches the hex STRINGS a raw SCRO carries, before any parsing.)
#
# APPLIES TO REFERENCES ONLY — exactly like _ITEM_SUBSTITUTIONS below, and for
# the same reason. Oblivion.esm defines its OWN record at 0x07 (NPC_ "Bendu
# Olo", EditorID=Player), so passing a record's own id through unshifted writes
# that record at 0x00000007 — an OVERRIDE of Skyrim.esm's Player, replacing the
# real player's race, voice, spells, AI data and factions with Oblivion's. That
# is what broke the CharacterGen Emperor conversation: his topics are gated on
# `GetIsID(Player) [Target]` (1,325 such conditions across INFO), and with the
# player base clobbered the target test stopped matching, so every CGEmperor
# topic vanished and only the quest-less 'Rumors' channel survived.
# Own ids therefore keep shifting to 0x01000007 (inert — simply never pointed
# at), while REFERENCES to 0x07/0x14 resolve to Skyrim.esm's real player.
PLAYER_REF_FID = 0x14    # ACHR PlayerRef
PLAYER_BASE_FID = 0x07   # NPC_ Player
_ENGINE_FIXED_FORMIDS = frozenset({PLAYER_REF_FID, PLAYER_BASE_FID})


# INJECTED FormIDs: raw TES4 FormID -> the id it must take in our own space.
#
# Oblivion let a plugin ADD records carrying a master's load-order index (an
# "injected" record). Nehrim's Translation.esp does this 3 times. Shifting them
# like an override lands them in the converted master's range at ids the master
# does not define, so the engine resolves the reference against the master,
# finds nothing, and hangs loading. They are new records and belong in OUR
# index. Populated by set_injected_formids() once the master index is known.
_injected_formids = {}


def set_injected_formids(mapping: dict):
    """Register raw-FormID -> our-space-FormID redirects for injected records."""
    _injected_formids.clear()
    _injected_formids.update(mapping)


def get_injected_formids() -> dict:
    return dict(_injected_formids)


def remap_formid(fid: int, offset: int = None,
                 is_own_id: bool = False) -> int:
    """Shift a TES4 FormID's load-order index into the output plugin's space.

    The TES5 master list is the TES4 one with `offset` new masters prepended,
    so EVERY index shifts up by offset — not just index 0. An override of a
    master keeps that master's (shifted) index and so still refers to the
    converted master's record; clobbering the index instead would turn every
    override into a new record in our own file.

    Injected records (registered via set_injected_formids) are the exception:
    they carry a master's index but the master has no such record, so they are
    redirected into our own space instead of dangling in the master's.

    `is_own_id` marks a record's OWN FormID rather than a reference to another
    record. Own ids skip the _ENGINE_FIXED_FORMIDS passthrough: that table
    exists so a REFERENCE to the player lands on Skyrim.esm's copy, but
    Oblivion.esm defines its own record at 0x07, and writing it unshifted makes
    it an override of the real Player. See _ENGINE_FIXED_FORMIDS.
    """
    if offset is None:
        offset = _formid_index_offset
    redirect = _injected_formids.get(fid)
    if redirect is not None:
        return redirect
    if fid and offset and (is_own_id or fid not in _ENGINE_FIXED_FORMIDS):
        high = (fid >> 24) & 0xFF
        return ((high + offset) << 24) | (fid & 0x00FFFFFF)
    return fid


#: Engine-fixed base objects: a REFERENCE to one lands on Skyrim.esm's copy.
from .equivalents import TES4_ITEM_FORMID_TO_SKYRIM as _ITEM_SUBSTITUTIONS


def get_formid(record: dict, key: str, default: int = 0) -> int:
    """Get a FormID (hex string) as an integer, applying load order remapping.

    Engine-fixed substitutions apply to REFERENCES only: `key == 'FormID'` is
    the record's own id and is left alone, so Oblivion's Gold001 is still
    written at its remapped id rather than colliding with Skyrim's 0x0F.
    """
    val = record.get(key)
    if val is None:
        return default
    try:
        raw = int(val, 16)
    except (ValueError, TypeError):
        return default
    is_own_id = (key == 'FormID')
    if not is_own_id:
        sub = _ITEM_SUBSTITUTIONS.get(raw)
        if sub is not None:
            return sub
    return remap_formid(raw, is_own_id=is_own_id)


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


def get_hex_bytes(record: dict, key: str, default: bytes = b'') -> bytes:
    """Get a raw byte blob exported as an uppercase hex string.

    Used by records the export dumps verbatim (WTHR NAM0 color tables).
    Returns `default` if the key is absent or is not valid hex.
    """
    val = record.get(key)
    if not val:
        return default
    try:
        return bytes.fromhex(str(val))
    except ValueError:
        return default
