"""Does a shell command run only through the wrapper, with nothing beside it?

Every simple command in the line must be a wrapper call or a READ_ONLY helper
that cannot write a `.py`.  Anything else -- a second program after `&&`, a
subshell, a `$(...)` that runs before the wrapper starts -- escapes the gate.
See: docs/reference/script_convert_architecture.md#every-command-runs-inside-the-wrapper
"""

import os
import re
import shlex

WRAPPER = 'tools/validate/safe_run.py'

#: Helpers that may sit beside the wrapper: they read, print or change directory.
READ_ONLY = frozenset({
    'cd', 'echo', 'true', 'ls', 'pwd', 'cat', 'tail', 'head', 'grep', 'sort', 'uniq', 'wc', 'cut',
    'tr', 'select-object', 'select-string', 'measure-object', 'sort-object',
    'out-string', 'write-output',
})

#: Interpreters the wrapper is launched with.
PYTHONS = frozenset({'python', 'python3', 'python.exe', 'python3.exe', 'py'})

#: A heredoc opener; group 2 is the delimiter that closes its body.
HEREDOC = re.compile(r'<<-?\s*([\'"]?)(\w+)\1')

#: An escaped character, a single-quoted span, or a double-quoted span.
QUOTED = re.compile(r'\\.|\'[^\']*\'|"(?:\\.|[^"\\])*"', re.S)

#: An environment assignment prefixing a command, `NAME=value`.
ASSIGNMENT = re.compile(r'^[A-Za-z_]\w*=')

PUNCTUATION = '();<>|&\n'


def strip_heredocs(command: str) -> str:
    """`command` without heredoc bodies, which are data rather than commands."""
    kept, closing = [], None
    for line in command.split('\n'):
        if closing is not None:
            closing = None if line.strip() == closing else closing
            continue
        kept.append(line)
        found = HEREDOC.findall(line)
        closing = found[-1][1] if found else None
    return '\n'.join(kept)


def _unquote_literal(match) -> str:
    """Keep a double-quoted span, which still expands; drop anything literal."""
    span = match.group(0)
    return re.sub(r'\\.', '', span) if span.startswith('"') else ''


def substitutes(text: str) -> bool:
    """True when `$(` or a backtick sits outside single quotes."""
    return bool(re.search(r'\$\(|`', QUOTED.sub(_unquote_literal, text)))


def _is_separator(token: str) -> bool:
    """True for `&&`, `||`, `;`, `|`, `&` or a newline; False for a redirect."""
    return (set(token) <= set(PUNCTUATION) and token[0] not in '<>'
            and bool(set(token) & set(';|&\n')))


def segments(text: str) -> list:
    """Each simple command as a list of words, split on control operators."""
    lex = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
    lex.whitespace = ' \t\r'
    lex.whitespace_split = True
    out, words = [], []
    for token in lex:
        if token and _is_separator(token):
            out.append(words)
            words = []
        else:
            words.append(token)
    out.append(words)
    return [w for w in out if w]


def is_wrapper(words: list) -> bool:
    """True for `python <path ending in the wrapper> ...`."""
    return (len(words) > 1 and os.path.basename(words[0]).lower() in PYTHONS
            and words[1].replace('\\', '/').endswith(WRAPPER))


def writes_python(words: list) -> bool:
    """True when a redirect in `words` targets a `.py` file."""
    return any(w.startswith('>') and nxt.lower().endswith('.py')
               for w, nxt in zip(words, words[1:]))


def escape(command: str):
    """Why `command` runs code outside the wrapper, or None when it does not."""
    text = strip_heredocs(command)
    if substitutes(text):
        return 'a `$(...)` or backtick runs before the wrapper starts'
    try:
        parts = segments(text)
    except ValueError as exc:
        return 'the command could not be split (%s)' % exc
    wrapped = False
    for words in parts:
        while words and ASSIGNMENT.match(words[0]):
            words = words[1:]
        if is_wrapper(words):
            wrapped = True
        elif not words or words[0].lower() not in READ_ONLY:
            return '`%s` runs outside the wrapper' % ' '.join(words[:3])
        elif writes_python(words):
            return '`%s` writes a .py outside the wrapper' % ' '.join(words)
    return None if wrapped else 'no part of it runs through the wrapper'
