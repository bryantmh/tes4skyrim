"""A block's movie plays after the rest of the block.

TES4 `PlayBink` only queued the movie, so the result script's later lines ran in the
same frame before it played. Skyrim's `Game.PlayBink` freezes the game, Papyrus
included, until the movie ends, so a converted call is moved past its block's
remaining lines.

See: docs/commentary/script_convert.md#playbink-halts-the-game
"""
import re

#: A converted movie call.
_MOVIE = re.compile(r'^\s*Game\.PlayBink\(')
#: The end of a function or event.
_END = re.compile(r'^\s*(EndFunction|EndEvent)\b')
#: The start of a function or event, with or without a return type.
_START = re.compile(r'^\s*(\w+(\[\])?\s+)?(Function|Event)\s')
#: A return, which would skip a movie moved past it.
_RETURN = re.compile(r'^\s*Return\b')


def _indent(line: str) -> int:
    """Leading spaces of `line`."""
    return len(line) - len(line.lstrip(' '))


def _block(lines: list, i: int) -> tuple:
    """(body start, end) of the block holding line `i`; a fragment is the whole list."""
    start = next((j + 1 for j in range(i - 1, -1, -1) if _START.match(lines[j])), 0)
    end = next((j for j in range(i + 1, len(lines)) if _END.match(lines[j])), len(lines))
    return start, end


def _movable(lines: list, i: int, start: int, end: int) -> bool:
    """Whether line `i` sits at its block's top level with code after it and no return."""
    rest = [line for line in lines[i + 1:end] if line.strip()]
    top = min(_indent(line) for line in lines[start:end] if line.strip())
    return bool(rest) and _indent(lines[i]) == top and not any(_RETURN.match(line) for line in rest)


def movie_last(lines: list) -> list:
    """`lines` with every top-level `Game.PlayBink` call moved to the end of its block."""
    out = list(lines)
    for i in reversed(range(len(out))):
        if not _MOVIE.match(out[i]):
            continue
        start, end = _block(out, i)
        if _movable(out, i, start, end):
            out.insert(end - 1, out.pop(i))
    return out
