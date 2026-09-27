"""Does a shell command run only through the wrapper, with nothing beside it?

Every simple command in the line must be a wrapper call or a READ_ONLY helper
that cannot write a file; a line of helpers alone passes.  Anything else -- a
second program after `&&`, a subshell, a `$(...)`, `<(...)` or script block that
runs before the wrapper starts, `sort -o` or `uniq IN OUT` -- escapes the gate.
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

#: An unquoted `(` or a `{` not opening `${`: a subshell, process substitution or script block.
GROUPING = re.compile(r'\(|(?<!\$)\{')

#: `sort`'s output flag, alone or in a short-option cluster, or `--output`.
SORT_OUTPUT = re.compile(r'^-[a-z]*o|^--o')

#: `uniq` options that take a value, so the value is not read as a file operand.
UNIQ_VALUED = frozenset({'-f', '-s', '-w'})


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


def groups(text: str) -> bool:
    """True when an unquoted `(` or `{` would run a nested command."""
    return bool(GROUPING.search(QUOTED.sub('', text)))


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


def _uniq_output(words: list) -> bool:
    """True when `uniq` names a second file operand, which it writes."""
    operands, skip = 0, False
    for word in words[1:]:
        if skip:
            skip = False
        elif word in UNIQ_VALUED:
            skip = True
        elif not word.startswith('-'):
            operands += 1
    return operands > 1


def helper_writes(words: list) -> bool:
    """True when a READ_ONLY helper would write: a `.py` redirect, `sort -o`, `uniq IN OUT`."""
    head = words[0].lower()
    if head == 'sort' and any(SORT_OUTPUT.match(w) for w in words[1:]):
        return True
    return (head == 'uniq' and _uniq_output(words)) or writes_python(words)


def escape(command: str):
    """Why `command` runs code outside the wrapper, or None when it does not.

    A line of READ_ONLY helpers alone passes: appending a no-op wrapper call
    already made it pass, so requiring one closed nothing.
    See: docs/reference/script_convert_architecture.md#every-command-runs-inside-the-wrapper
    """
    text = strip_heredocs(command)
    if substitutes(text):
        return 'a `$(...)` or backtick runs before the wrapper starts'
    if groups(text):
        return 'an unquoted `(` or `{` runs a nested command before the wrapper starts'
    try:
        parts = segments(text)
    except ValueError as exc:
        return 'the command could not be split (%s)' % exc
    for words in parts:
        while words and ASSIGNMENT.match(words[0]):
            words = words[1:]
        if is_wrapper(words):
            continue
        if not words or words[0].lower() not in READ_ONLY:
            return '`%s` runs outside the wrapper' % ' '.join(words[:3])
        if helper_writes(words):
            return '`%s` writes a file outside the wrapper' % ' '.join(words)
    return None
