"""Preserve UTF-8 asset paths when BSArch uses the Windows ANSI codepage."""
import os
import hashlib
import struct
from pathlib import Path


def name_hash(name, extension=False):
    """Skyrim BSA hash (xEdit Core/wbHash.pas, TwbHash.TES5)."""
    stem = name.lower()
    suffix = b''
    if extension and b'.' in stem:
        stem, ext = stem.rsplit(b'.', 1)
        suffix = b'.' + ext
    n = len(stem)
    low = ((stem[-1] | (stem[-2] << 8 if n > 2 else 0)
            | (n & 255) << 16 | stem[0] << 24) if n else 0)
    low |= {b'.kf': 0x80, b'.nif': 0x8000, b'.dds': 0x8080,
            b'.wav': 0x80000000}.get(suffix, 0)
    def rolling(text):
        value = 0
        for c in text:
            value = (c + value * 0x1003f) & 0xffffffff
        return value
    return low | ((rolling(stem[1:-2]) + rolling(suffix)) & 0xffffffff) << 32


def staging_path(relative):
    """ASCII staging spelling; the original path is restored in the BSA."""
    return Path(*(part if part.isascii() else
                  '__utf8_' + hashlib.sha256(part.encode('utf-8')).hexdigest()
                  + (Path(part).suffix if Path(part).suffix.isascii() else '')
                  for part in Path(relative).parts))


def rewrite_names(archive, replacements):
    """Replace raw asset paths, preserving compression and file contents.

    Keys and values are bytes. Only ASCII is case folded, as in Skyrim's
    narrow asset paths; Cyrillic case in the voice EditorID stays intact.
    """
    archive = Path(archive)
    temporary = archive.with_name(archive.name + '.utf8-tmp')
    try:
        with archive.open('rb') as src:
            header = list(struct.unpack('<4s8I', src.read(36)))
            if header[:2] != [b'BSA\0', 105]:
                raise ValueError('UTF-8 name rewriting requires a Skyrim SE BSA')
            src.seek(header[2])
            folders = [struct.unpack('<QIIQ', src.read(24))
                       for _ in range(header[4])]
            original = []
            for _, count, _, _ in folders:
                folder = src.read(src.read(1)[0]).rstrip(b'\0')
                for _ in range(count):
                    _, size, offset = struct.unpack('<QII', src.read(16))
                    original.append((folder, size, offset))
            filenames = src.read(header[7]).split(b'\0')
            groups = {}
            for (folder, size, offset), filename in zip(original, filenames):
                path = folder + b'\\' + filename
                new = replacements.get(path, path).lower()
                folder, filename = new.rsplit(b'\\', 1)
                payload_size = size & 0x3fffffff
                embedded = b''
                if header[3] & 0x100:
                    src.seek(offset)
                    old_len = src.read(1)[0]
                    offset += 1 + old_len
                    payload_size -= 1 + old_len
                    if len(new) > 255:
                        raise ValueError('Embedded BSA path exceeds 255 UTF-8 bytes')
                    embedded = bytes([len(new)]) + new
                entry = [name_hash(filename, True), filename, size & 0xc0000000,
                         offset, payload_size, embedded]
                bucket = groups.setdefault(folder, {})
                if filename in bucket:
                    raise ValueError('Duplicate UTF-8 archive path: ' + repr(new))
                bucket[filename] = entry
            groups = sorted(((folder, sorted(files.values()))
                             for folder, files in groups.items()),
                            key=lambda pair: name_hash(pair[0]))
            header[2] = 36
            header[4] = len(groups)
            header[5] = len(original)
            header[6] = sum(len(folder) + 1 for folder, _ in groups)
            header[7] = sum(len(e[1]) + 1 for _, entries in groups for e in entries)
            data_start = (36 + 24 * header[4] + header[4] + header[6]
                          + 16 * header[5] + header[7])
            with temporary.open('wb') as dst:
                dst.write(struct.pack('<4s8I', *header))
                pos = 36 + 24 * header[4]
                for folder, entries in groups:
                    if len(folder) + 1 > 255:
                        raise ValueError('BSA folder exceeds 254 UTF-8 bytes')
                    dst.write(struct.pack('<QIIQ', name_hash(folder), len(entries),
                                          0, pos + header[7]))
                    pos += len(folder) + 2 + 16 * len(entries)
                offset = data_start
                for folder, entries in groups:
                    dst.write(bytes([len(folder) + 1]) + folder + b'\0')
                    for hash_, _, flags, _, size, embedded in entries:
                        total = size + len(embedded)
                        dst.write(struct.pack('<QII', hash_, flags | total, offset))
                        offset += total
                for _, entries in groups:
                    for e in entries:
                        dst.write(e[1] + b'\0')
                for _, entries in groups:
                    for _, _, _, offset, size, embedded in entries:
                        src.seek(offset)
                        dst.write(embedded)
                        while size:
                            block = src.read(min(size, 1024 * 1024))
                            if not block:
                                raise ValueError('Truncated BSA payload')
                            dst.write(block)
                            size -= len(block)
        os.replace(temporary, archive)
    finally:
        if temporary.exists():
            temporary.unlink()
