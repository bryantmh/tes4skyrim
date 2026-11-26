"""Process-pool worker for BSA extraction: reads, decompresses and writes one chunk of entries.

Kept apart from bsa_extract so a spawned worker imports only zlib and lz4, not the audio stack.

See: docs/commentary/asset_convert_bsa.md#parallel-extraction
"""
import os
import zlib

import lz4.frame

#: Most error messages a chunk reports back; the caller prints at most ten per archive.
_MAX_MESSAGES = 10


def _write_unlinked(out_path, data) -> None:
    """Write `data`, unlinking first so a hard-linked seed file is never truncated through the link."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.isfile(out_path):
        os.unlink(out_path)
    with open(out_path, 'wb') as fh:
        fh.write(data)


def extract_chunk(bsa_path, codec, embedded, entries):
    """Extract `entries` [(out_path, size, offset, compressed)] from one archive.

    `codec` is 'zlib' or 'lz4'; `embedded` says each payload starts with its own
    bstring path. Returns (extracted, corrupt, errors, messages): an entry whose
    compressed payload is corrupt counts as `corrupt` and writes nothing.
    """
    decompress = lz4.frame.decompress if codec == 'lz4' else zlib.decompress
    extracted = corrupt = errors = 0
    messages = []
    with open(bsa_path, 'rb') as fh:
        for out_path, size, offset, compressed in entries:
            fh.seek(offset)
            if embedded:
                name_len = fh.read(1)[0]
                fh.seek(name_len, 1)
                size -= 1 + name_len
            data = fh.read(size)
            try:
                if compressed:
                    data = decompress(data[4:])
            except (zlib.error, RuntimeError):
                corrupt += 1
                continue
            try:
                _write_unlinked(out_path, data)
                extracted += 1
            except Exception as exc:
                errors += 1
                if len(messages) < _MAX_MESSAGES:
                    messages.append(f'{out_path}: {exc}')
    return extracted, corrupt, errors, messages
