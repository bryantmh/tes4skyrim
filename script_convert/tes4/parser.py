"""Parse Oblivion (TES4/OBSE) tokens into a TES4 AST.

Recursive descent over the token stream from `lexer.tokenize`.  The grammar is
small -- a census of the real corpus (Oblivion.esm, Nehrim.esm, Morrowind_ob.esm)
found no line continuations, no hex literals and no `->`; a statement is
exactly one line, which is why NEWLINE terminates every statement rule.

Two rules govern the whole file:

  * **A statement is one line.**  Nothing spans lines, so a parse error can be
    contained to the line that caused it.
  * **Never fail the script.**  A script that does not convert takes down every
    OTHER script that declares a property of its type, so an unparseable line
    degrades to `Raw` (kept verbatim) rather than raising.  Oblivion's own
    compiler was permissive -- MG09Script ships a stray backtick -- and we have
    to be at least as forgiving.

Block structure is the payoff: `if`/`elseif`/`else`/`endif` and
`while`/`loop` produce nodes that OWN their bodies, so nesting is correct by
construction instead of being re-derived from emitted text afterwards.
"""

from __future__ import annotations

import dataclasses
from enum import Enum, auto

from script_convert.tes4.lexer import PRECEDENCE, T, Token, tokenize
from script_convert.tes4.nodes import (
    Assign,
    BinOp,
    Blank,
    Block,
    Call,
    Comment,
    Expr,
    ExprStmt,
    Goto,
    Ident,
    If,
    Index,
    Label,
    Literal,
    Member,
    Raw,
    Return,
    Script,
    SetFunctionValue,
    Stmt,
    Unary,
    VarDecl,
    While,
)


class Mode(Enum):
    """SCRIPT parses `begin`/`end` blocks; FRAGMENT is a bare statement list.

    An INFO result script or a QUST stage fragment has no block wrappers -- it
    is a body.  That is a parser PARAMETER, not a reason for a second
    hand-written line loop (the old `convert_fragment` duplicated the var-decl
    regex and the scn/begin/end skipping to get exactly this).
    """
    SCRIPT = auto()
    FRAGMENT = auto()


VAR_TYPES = frozenset({
    'short', 'long', 'int', 'float', 'ref', 'reference',
    'string_var', 'array_var',
})



# Operators that bind their two operands into ONE argument in the OBSE `Call`
# grammar.  `-` is deliberately absent: it is far more often a unary sign on a
# separate argument (`Call Foo 1 -1`) than a spaced-out subtraction, and the
# comma form covers subtraction unambiguously.  `+` IS here -- it cannot start
# an argument the way `-` can.
_ARITHMETIC_JOIN = frozenset({'*', '/', '+', '%', '&&', '||',
                              '==', '!=', '<', '>', '<=', '>=', '&', '|'})


def _unquote(text: str) -> str:
    """Strip Oblivion's optional quotes from an EditorID spelling."""
    if len(text) >= 2 and text[0] == '"':
        return text[1:-1] if text[-1] == '"' else text[1:]
    return text


#: TES4 commands whose argument count is FIXED, so a sign after the last one
#: is an operator on the RESULT and never another argument.  Whitespace alone
#: cannot decide it: `GetPos Z +15` is tight, exactly like the negative
#: argument in `SetFactionRank X -1`, but GetPos takes only the axis -- so
#: without the arity the `+15` was absorbed, the axis lookup failed its
#: `X/Y/Z` test, and the read silently degraded to `GetPositionX()`.
_FIXED_ARITY = {
    'getpos': 1, 'getangle': 1, 'getstartingpos': 1, 'getstartingangle': 1,
    'getdeadcount': 1, 'getitemcount': 1, 'getav': 1, 'getactorvalue': 1,
    'getbaseav': 1, 'getbaseactorvalue': 1, 'getdistance': 1,
    'getstage': 1, 'getdisposition': 1,
}

#: Commands whose FIRST argument may be a tight negative number, so the sign
#: starts the argument list rather than continuing it.  A leading tight sign
#: is otherwise refused, because `x -1` on a plain variable is subtraction,
#: not a call -- these are the only three names in the corpus where it is an
#: argument (`PositionWorld -3328.02, 280.33, ...`), and all three take
#: coordinates.
_NEGATIVE_FIRST_ARG = frozenset({
    'positionworld', 'positioncell', 'emcsetmusictype',
})


class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0
        self.saw_leading_comma = False

    # -- token helpers -----------------------------------------------------

    @property
    def cur(self) -> Token:
        return self.toks[self.i]

    def at_end(self) -> bool:
        return self.cur.kind is T.EOF

    def advance(self) -> Token:
        tok = self.toks[self.i]
        if tok.kind is not T.EOF:
            self.i += 1
        return tok

    def accept_op(self, *ops: str) -> Token | None:
        if self.cur.is_op(*ops):
            return self.advance()
        return None

    def accept_ident(self, *names: str) -> Token | None:
        if self.cur.is_ident(*names):
            return self.advance()
        return None

    def skip_newlines(self) -> None:
        while self.cur.kind is T.NEWLINE:
            self.advance()

    def line_tokens(self) -> list[Token]:
        """Consume and return the rest of the current source line."""
        out = []
        while self.cur.kind not in (T.NEWLINE, T.EOF):
            out.append(self.advance())
        return out

    def take_line_end(self) -> str:
        """Consume to end of line, returning a trailing comment if present.

        Anything else left on the line is dropped -- callers that care have
        already consumed what they understand.
        """
        comment = ''
        while self.cur.kind not in (T.NEWLINE, T.EOF):
            tok = self.advance()
            if tok.kind is T.COMMENT and not comment:
                comment = tok.text
        if self.cur.kind is T.NEWLINE:
            self.advance()
        return comment

    # -- expressions -------------------------------------------------------

    def parse_expression(self) -> Expr:
        return self._parse_binary(0)

    def _parse_binary(self, level: int) -> Expr:
        if level >= len(PRECEDENCE):
            # Innermost level: an operand has just been produced, so this is
            # where a bare TES4 command picks up its arguments.  Doing it here
            # rather than at the top covers EVERY operand position --
            # `if getstage ND10 >= 20 && getstage ND10 < 50` has a command on
            # both sides of the `&&`, and absorbing only the leftmost silently
            # dropped the right-hand arguments.
            return self._absorb_args(self._parse_unary())
        left = self._parse_binary(level + 1)
        while True:
            # A condition may CONTINUE on the next line: TES4 accepts
            #     if ( a == 0 ) && ( b == 0 )
            #          && ( c == 0 )
            # and the engine reads it as one expression.  A newline followed
            # by a binary operator is therefore not the end of the statement.
            # Emitted as two lines it produced `&& (...)` on its own, which
            # Papyrus rejects ("invalid token: &&").
            self._skip_continuation(level)
            if not (self.cur.kind is T.OP
                    and self.cur.text in PRECEDENCE[level]):
                break
            op = self.advance()
            # A DANGLING operator -- `if x <= 256 && ` with nothing after it.
            # Authored damage (Morroblivion's fbmwMalexaScript), which
            # Oblivion ignored.  Consuming the next line as the right operand
            # swallows a whole statement into the condition, so the operator
            # is dropped and the condition ends where it stands.
            if self.cur.kind in (T.NEWLINE, T.EOF, T.COMMENT):
                break
            right = self._parse_binary(level + 1)
            left = BinOp(op.text, left, right, line=op.line)
        return left

    def _skip_continuation(self, level: int) -> None:
        """Consume NEWLINEs when the next real token continues the expression."""
        j = self.i
        while self.toks[j].kind is T.NEWLINE:
            j += 1
        if (j != self.i and self.toks[j].kind is T.OP
                and self.toks[j].text in PRECEDENCE[level]):
            self.i = j

    def _parse_unary(self) -> Expr:
        # OBSE's `$x` is a string CAST, written as a prefix on the operand:
        # `let sTime := "0" + $ihour + ":"`.  Parsed as an ordinary unary so
        # the emitter can turn it into `(x as String)`; left as a stray
        # operator it reached Papyrus verbatim and the scanner rejected the
        # `$` outright, failing the script and the three that import it.
        tok = self.accept_op('-', '+', '$')
        if tok:
            return Unary(tok.text, self._parse_unary(), line=tok.line)
        return self._parse_postfix()

    def _parse_postfix(self) -> Expr:
        expr = self._parse_primary()
        while True:
            if self.cur.is_op('.'):
                # `a.b` -- the member name may be QUOTED. Oblivion treats
                # quotes around an EditorID as optional, and Nehrim uses them
                # constantly (`"NQ15W02TresorRef".AddItem "NQ15W02Gold001", 100`,
                # 890 statements).  Accepting them here is what
                # `_unquote_identifiers` does with a regex pre-pass in the old
                # text pipeline -- and it had to exist because the assignment
                # TARGET and the VALUE went through different code paths that
                # mangled the quotes differently.
                nxt = self.toks[self.i + 1]
                if nxt.kind not in (T.IDENT, T.STRING):
                    break
                self.advance()
                name = self.advance()
                expr = Member(expr, _unquote(name.text), line=name.line)
            elif self.cur.is_op('['):
                open_tok = self.advance()
                idx = self.parse_expression()
                self.accept_op(']')
                expr = Index(expr, idx, line=open_tok.line)
            else:
                break
        return expr

    def _parse_primary(self) -> Expr:
        tok = self.cur
        if tok.kind is T.NUMBER:
            self.advance()
            return Literal(tok.text, line=tok.line)
        if tok.kind is T.STRING:
            self.advance()
            # A quoted name FOLLOWED BY `.` is an EditorID with Oblivion's
            # optional quotes, not a string value: `"NQ16"."NQ16CountVar"`.
            if self.cur.is_op('.'):
                return Ident(_unquote(tok.text), line=tok.line)
            return Literal(tok.text, is_string=True, line=tok.line)
        if tok.kind is T.IDENT:
            self.advance()
            return Ident(tok.text, line=tok.line)
        if tok.is_op('('):
            self.advance()
            inner = self.parse_expression()
            self.accept_op(')')
            # Record that the author parenthesised this, so the emitter can
            # echo it.  Frozen dataclass, so replace rather than mutate.
            return dataclasses.replace(inner, parenthesised=True)
        # Anything else (a stray operator, the MG09Script backtick) is not an
        # expression; consume it so the caller cannot loop forever.
        self.advance()
        return Raw(tok.text, line=tok.line)

    def _parse_call_args(self, name: str = '', *,
                         arithmetic: bool = False) -> tuple[Expr, ...]:
        """Arguments to a command, up to an operator or end of line.

        TES4 separates them with whitespace, commas, or a mix:
        `SayTo BaurusRef, CharGenMain 1` is three arguments.

        Each argument is a single OPERAND, never a nested command call: in
        `player.additem Gold001 100`, `Gold001` and `100` are two arguments,
        not `Gold001(100)`.  Parsing an argument as a full expression produced
        exactly that nesting -- the tokens were all present, so a
        content-preservation check could not see it, only a structural test.

        Stopping at a binary operator is what lets a call be an operand:
        `getstage ND10 >= 20` ends the argument run at `>=` so the comparison
        binds outside the call.

        `arithmetic=True` selects the OBSE `Call <Script> ...` grammar, where
        an operator instead JOINS its operands into one argument: Nehrim writes
        `Call GlobalScriptExpGained 30 * ( x - y ), 1, 1, -1`, which is four
        arguments, the first spelled with spaces around the operator.  `-` and
        `+` stay separators even there, because a sign introducing the next
        argument (`Call Foo 1 -1`) is far more common than a spaced-out
        subtraction, and the comma form covers subtraction unambiguously.
        """
        args: list[Expr] = []
        after_comma = False
        limit = _FIXED_ARITY.get(name.lower())
        # OBSE `Call <Script> <args>` passes EXPRESSIONS, so an operator
        # between two operands binds them into one argument rather than
        # ending the run: `Call ExpGained 30 * (getPCMiscStat 8 - l)` passes
        # one product, not the literal 30.
        if name.lower() == 'call':
            arithmetic = True
        # Reported to the caller through the parser, since this returns only
        # the argument tuple.  A LEADING comma is meaningful: for a
        # zero-argument command the token after it is the receiver.
        self.saw_leading_comma = self.cur.is_op(',')
        while True:
            # A command with a KNOWN arity stops absorbing once it is full.
            full = limit is not None and len(args) >= limit
            tok = self.cur
            if tok.kind in (T.NEWLINE, T.EOF, T.COMMENT):
                break
            if tok.is_op(','):
                self.advance()
                after_comma = True
                continue
            # `(` opens a parenthesised operand and `$` an OBSE string
            # cast; both START an argument rather than ending the run.
            if tok.kind is T.OP and tok.text not in ('(', '$'):
                # A sign continues the ARGUMENT LIST once the list has started
                # -- `Player.SetFactionRank SEHeretic -1` passes -1 (the
                # current converter emits `SetFactionRank(SEHeretic, -1)`), and
                # `rotate z, -30` does the same after a comma.
                #
                # Before any argument exists, a sign is a BINARY operator on
                # the command's result, so the run ends and the operator binds
                # outside the call: `GetItemCount Gold001 >= 100`, and
                # `x + 1` where x is a plain variable.
                sign_ok = '-' if arithmetic else ('-', '+')
                # TIGHT against its operand (`-1`), never spaced (`- 10000`):
                # with no call parentheses, whitespace is the only thing
                # telling a negative ARGUMENT from a subtraction on the RESULT.
                # Getting it wrong silently drops an operand -- `GetPos Y -
                # 10000` became `GetPositionY()` with the subtraction gone.
                nxt = self.toks[min(self.i + 1, len(self.toks) - 1)]
                tight = (not full
                         and nxt.col == tok.col + len(tok.text)
                         and self._starts_operand(nxt))
                starts = args or name.lower() in _NEGATIVE_FIRST_ARG
                if not ((after_comma or (starts and tight))
                        and tok.text in sign_ok):
                    break
            before = self.i
            args.append(self._parse_operand(arithmetic=arithmetic))
            after_comma = False
            if self.i == before:  # no progress: bail rather than spin
                break
        return tuple(args)

    @staticmethod
    def _starts_operand(tok: Token) -> bool:
        """Could `tok` begin an operand (rather than end the argument run)?

        `$` is OBSE's string-cast PREFIX, so it opens an operand exactly as
        `(` does -- `MessageBoxEX $msg` passes one argument, and refusing the
        `$` here left the command with none.
        """
        return (tok.kind in (T.IDENT, T.NUMBER, T.STRING)
                or tok.is_op('(', '$'))

    def _parse_operand(self, *, arithmetic: bool = False) -> Expr:
        """One argument-position operand: a literal, name, `recv.name` or `(...)`.

        Deliberately does NOT absorb further tokens as arguments of its own --
        that is what keeps `additem Gold001 100` flat.

        With `arithmetic`, an operator between two operands binds them into
        this one argument (the OBSE `Call` grammar); see `_parse_call_args`.
        """
        # `$` is OBSE's string cast and binds to the operand that follows,
        # exactly like a sign: `MessageBoxEX $msg` passes ONE argument.
        tok = self.accept_op('-', '+', '$')
        if tok:
            return Unary(tok.text, self._parse_operand(arithmetic=arithmetic),
                         line=tok.line)
        expr = self._parse_postfix()
        if not arithmetic:
            return expr
        # Join `a * b` into one argument. `-`/`+` are excluded: they far more
        # often introduce the NEXT argument than continue this one.
        while (self.cur.kind is T.OP and self.cur.text in _ARITHMETIC_JOIN
               and self._starts_operand(self.toks[self.i + 1])):
            op = self.advance()
            right = self._parse_operand(arithmetic=arithmetic)
            expr = BinOp(op.text, expr, right, line=op.line)
        return expr

    def _parse_command_stmt(self, tokens_start: int):
        """A bare statement: `Activate`, `player.additem Gold001 100`."""
        expr = self._parse_postfix()
        receiver = None
        name = ''
        if isinstance(expr, Member):
            receiver, name = expr.owner, expr.name
        elif isinstance(expr, Ident):
            name = expr.name
        else:
            # Not a command shape at all -- keep the source line verbatim.
            return None
        args = self._parse_call_args(name)
        return Call(name, args, receiver, self.saw_leading_comma,
                    line=self.toks[tokens_start].line)

    # -- statements --------------------------------------------------------

    def _raw_stmt(self, start: int, line: int) -> Stmt:
        """Everything from `start` to end of line, kept as source text."""
        self.i = start
        toks = self.line_tokens()
        comment = ''
        if toks and toks[-1].kind is T.COMMENT:
            comment = toks[-1].text
            toks = toks[:-1]
        if self.cur.kind is T.NEWLINE:
            self.advance()
        text = ' '.join(t.text for t in toks)
        return Comment(text=text, line=line, comment=comment) if not text else \
            _raw_expr_stmt(text, line, comment)

    def parse_statement(self, terminators: frozenset) -> Stmt | None:
        """One statement, or None when a terminator keyword is next.

        The terminator is NOT consumed -- the block parser that owns it does
        that, which is what makes nesting correct by construction.
        """
        self.skip_newlines()
        if self.at_end():
            return None

        tok = self.cur
        low = tok.text.lower() if tok.kind is T.IDENT else ''
        if low and low in terminators:
            return None

        line = tok.line
        start = self.i

        if tok.kind is T.COMMENT:
            self.advance()
            self.take_line_end()
            return Comment(text=tok.text, line=line)

        # A separator rule the author wrote WITHOUT a `;` -- `----------`,
        # `:====`, `== == ==`. Oblivion tolerated these decorations; parsed as
        # expressions they become invalid unary-operator chains in Papyrus.
        j = self.i
        while self.toks[j].kind not in (T.NEWLINE, T.EOF, T.COMMENT):
            j += 1
        banner = ''.join(t.text for t in self.toks[self.i:j])
        if (len(banner) >= 3 and all(t.kind is T.OP for t in self.toks[self.i:j])
                and all(ch in '-=:._*~#' for ch in banner)):
            self.i = j
            return Comment(text=';' + banner, line=line,
                           comment=self.take_line_end())

        if tok.kind is T.IDENT:
            # An UNMATCHED closer or branch keyword -- `endif` with no open
            # `if`, an `else` after its `if` already closed.  Authored scripts really do carry these (Oblivion's
            # MGMageConversationFollowScript has a spare `endif`, and the
            # engine ignored it), and the block parser that owns a real closer
            # has already consumed it via `terminators`.  Reaching here means
            # nothing opened it, so it is dropped: emitting it as a statement
            # produced `endif()`, an undefined call that failed the script.
            if low in ('endif', 'endwhile', 'loop', 'else', 'elseif'):
                self.advance()
                return Comment(text=';%s  ;unmatched closer, dropped' % tok.text,
                               line=line, comment=self.take_line_end())
            if low in VAR_TYPES and self.toks[self.i + 1].kind is T.IDENT:
                self.advance()
                name = self.advance()
                return VarDecl(vtype=low, name=name.text, line=line,
                               comment=self.take_line_end())
            if low == 'set':
                return self._parse_set(line, start)
            if low == 'let':
                return self._parse_let(line, start)
            if low in ('if', 'elseif'):
                return self._parse_if(line)
            if low == 'while':
                return self._parse_while(line)
            if low == 'return':
                self.advance()
                return Return(line=line, comment=self.take_line_end())
            if low == 'setfunctionvalue':
                self.advance()
                value = (self.parse_expression()
                         if self.cur.kind not in (T.NEWLINE, T.EOF, T.COMMENT)
                         else None)
                return SetFunctionValue(value=value, line=line,
                                        comment=self.take_line_end())
            if low == 'label' and self.toks[self.i + 1].kind is T.NUMBER:
                self.advance()
                num = self.advance()
                return Label(number=num.text, line=line,
                             comment=self.take_line_end())
            if low == 'goto' and self.toks[self.i + 1].kind is T.NUMBER:
                self.advance()
                num = self.advance()
                return Goto(number=num.text, line=line,
                            comment=self.take_line_end())

            call = self._parse_command_stmt(start)
            if call is not None:
                return ExprStmt(expr=call, line=line,
                                comment=self.take_line_end())

        # A statement can also OPEN with a quoted EditorID receiver:
        # `"NQ15W02TresorRef".AddItem "NQ15W02Gold001", 100` (Nehrim, 890
        # statements).  The quotes are Oblivion's optional form, so this is a
        # command call like any other.
        if tok.kind is T.STRING and self.toks[self.i + 1].is_op('.'):
            call = self._parse_command_stmt(start)
            if call is not None:
                return ExprStmt(expr=call, line=line,
                                comment=self.take_line_end())

        # CSE also accepts quotes around a standalone zero-argument command.
        # Only known zero-argument names take this path; an authored string
        # remains a string rather than being guessed into a function call.
        if tok.kind is T.STRING and self.toks[self.i + 1].kind \
                in (T.NEWLINE, T.EOF, T.COMMENT):
            from script_convert.constants import _ZERO_ARG_REF_FUNCTIONS
            command = _unquote(tok.text)
            if command.lower() in _ZERO_ARG_REF_FUNCTIONS:
                self.advance()
                return ExprStmt(expr=Call(command, (), None, line=line),
                                line=line, comment=self.take_line_end())

        return self._raw_stmt(start, line)

    def _parse_set(self, line: int, start: int) -> Stmt:
        """`set <target> to <value>`."""
        self.advance()  # 'set'
        target = self._parse_postfix()
        if not self.accept_ident('to'):
            # Malformed; keep the line rather than guessing at its halves.
            return self._raw_stmt(start, line)
        value = self.parse_expression()
        return Assign(target=target, value=value, line=line,
                      comment=self.take_line_end())

    def _parse_let(self, line: int, start: int) -> Stmt:
        """OBSE `let <target> := <value>` / `let x += 1`."""
        self.advance()  # 'let'
        target = self._parse_postfix()
        op = ''
        if self.accept_op(':='):
            pass
        elif self.cur.kind is T.OP and self.cur.text in ('+=', '-=', '*=', '/='):
            op = self.advance().text[0]
        elif self.accept_op('='):
            pass
        else:
            return self._raw_stmt(start, line)
        value = self.parse_expression()
        return Assign(target=target, value=value, op=op, is_let=True,
                      line=line, comment=self.take_line_end())

    def _absorb_args(self, value: Expr) -> Expr:
        """Fold bare following tokens into `value` as a command's arguments.

        TES4 commands carry no parentheses, so `getstage ND10` is a call whose
        argument is just the next token.  An operand is only ever a call when
        something callable (a name, or `recv.name`) is followed by another
        operand token -- a number, string, name or `(`.  Stopping at operators
        and closers is what keeps `getstage ND10 >= 20` from swallowing `>=`.
        """
        if not isinstance(value, (Ident, Member)):
            return value
        args = self._parse_call_args(value.name)
        if not args:
            return value
        recv = value.owner if isinstance(value, Member) else None
        return Call(value.name, args, recv, self.saw_leading_comma,
                    line=value.line)

    def _parse_if(self, line: int) -> Stmt:
        """`if <cond> ... [elseif ...] [else ...] endif`."""
        self.advance()  # 'if' / 'elseif'
        cond = self.parse_expression()
        comment = self.take_line_end()
        node = If(cond=cond, line=line, comment=comment)

        node.body = self._parse_body(_IF_TERMINATORS)
        while self.cur.is_ident('elseif'):
            elif_line = self.cur.line
            self.advance()
            econd = self.parse_expression()
            self.take_line_end()
            ebody = self._parse_body(_IF_TERMINATORS)
            node.elifs.append((econd, ebody, elif_line))
        if self.cur.is_ident('else'):
            self.advance()
            # TES4 accepts `else <condition>` as an elseif; keep the source
            # shape so emission does not have to invent one.
            if self.cur.kind not in (T.NEWLINE, T.EOF, T.COMMENT):
                econd = self.parse_expression()
                self.take_line_end()
                node.elifs.append((econd, self._parse_body(_IF_TERMINATORS),
                                   line))
                node.else_is_elseif = True
            else:
                self.take_line_end()
                node.orelse = self._parse_body(_IF_TERMINATORS)
        if self.cur.is_ident('endif'):
            self.advance()
            self.take_line_end()
        return node

    def _parse_while(self, line: int) -> Stmt:
        self.advance()  # 'while'
        cond = self.parse_expression()
        comment = self.take_line_end()
        node = While(cond=cond, line=line, comment=comment)
        node.body = self._parse_body(_WHILE_TERMINATORS)
        if self.cur.is_ident('loop', 'endwhile'):
            self.advance()
            self.take_line_end()
        return node

    def _parse_body(self, terminators: frozenset) -> list:
        """Statements until a terminator keyword or EOF."""
        out = []
        while True:
            self.skip_newlines()
            if self.at_end():
                return out
            stmt = self.parse_statement(terminators)
            if stmt is None:
                return out
            out.append(stmt)

    def _parse_block(self, line: int) -> Block:
        """`begin <type> [filter] ... end`."""
        self.advance()  # 'begin'
        btype = ''
        if self.cur.kind is T.IDENT:
            btype = self.advance().text.lower()
        # The filter is the rest of the line verbatim: it RESTRICTS the block
        # to one object and dropping it makes the block fire for everyone.
        parts = []
        while self.cur.kind not in (T.NEWLINE, T.EOF, T.COMMENT):
            parts.append(self.advance().text)
        comment = self.take_line_end()
        node = Block(btype=btype, filter=' '.join(parts).strip(), line=line,
                     comment=comment)
        node.body = self._parse_body(_BLOCK_TERMINATORS)
        if self.cur.is_ident('end'):
            self.advance()
            self.take_line_end()
        return node


_IF_TERMINATORS = frozenset({'elseif', 'else', 'endif', 'end'})
_WHILE_TERMINATORS = frozenset({'loop', 'endwhile', 'end'})
_BLOCK_TERMINATORS = frozenset({'end'})
_TOP_TERMINATORS = frozenset()


def _raw_expr_stmt(text: str, line: int, comment: str) -> Stmt:
    return ExprStmt(expr=Raw(text, line=line), line=line, comment=comment)


def split_call_args(rest: str) -> list[str]:
    """Split the argument tail of an OBSE `Call <Script> ...` into SOURCE text.

    The tree-based replacement for the hand-rolled scanner that used to track
    quote state, bracket depth and operator context by character.  The lexer
    already knows where a string ends and the parser already knows where an
    argument ends, so this just records the token span of each argument and
    slices the original text -- callers still receive strings, which keeps the
    emitted output identical while the scanning logic disappears.
    """
    toks = tokenize(rest)
    p = Parser(toks)
    out: list[str] = []
    while True:
        tok = p.cur
        if tok.kind in (T.NEWLINE, T.EOF, T.COMMENT):
            break
        if tok.is_op(','):
            p.advance()
            continue
        start = p.i
        p._parse_operand(arithmetic=True)
        if p.i == start:            # no progress: bail rather than spin
            break
        # Slice the ORIGINAL text spanned by this argument's tokens, so the
        # caller sees exactly what the author wrote (columns are 1-based, and
        # an argument tail is always a single line).
        last = toks[p.i - 1]
        lo = toks[start].col - 1
        hi = last.col - 1 + len(last.text)
        arg = rest[lo:hi].strip()
        if arg:
            out.append(arg)
    return out


def split_trailing_comment(line: str) -> tuple[str, str]:
    """Split a source line into (code, trailing `;` comment).

    The lexer already knows a `;` inside a string literal is not a comment --
    `MessageBox "a ; b"` keeps its semicolon -- so this just finds the COMMENT
    token and slices the original text at its column.

    A line with no `;` at all cannot carry one, and lexing it is pure cost:
    only 10% of corpus lines contain the character, so the guard skips nine
    tokenize calls in ten.
    """
    if ';' not in line:
        return line.rstrip(), ''
    for tok in tokenize(line):
        if tok.kind is T.COMMENT:
            return line[:tok.col - 1].rstrip(), line[tok.col - 1:]
        if tok.kind in (T.NEWLINE, T.EOF):
            break
    return line.rstrip(), ''


def split_param_names(block_filter: str) -> list[str]:
    """Parameter names from an OBSE `begin Function{...}` header.

    Both separators occur in the wild -- `{ a, b, c }` and
    `{ refRuneSpell levelRequired}` -- and the lexer already treats commas and
    whitespace alike, so the names are simply every identifier token between
    the braces.
    """
    return [t.text for t in tokenize(block_filter) if t.kind is T.IDENT]


def parse(source: str, mode: Mode = Mode.SCRIPT) -> Script:
    """Parse a script body (SCRIPT) or a result-script fragment (FRAGMENT).

    Never raises on bad input: an unparseable line becomes a Raw statement so
    one odd line cannot fail the whole script (and, through the property-type
    graph, every script that names it).
    """
    p = Parser(tokenize(source))
    script = Script()
    seen_vars: set[str] = set()
    in_body = False

    while True:
        p.skip_newlines()
        if p.at_end():
            break
        tok = p.cur

        if tok.kind is T.IDENT and tok.text.lower() in ('scriptname', 'scn'):
            p.advance()
            if p.cur.kind is T.IDENT:
                script.name = p.advance().text
            p.take_line_end()
            continue

        if tok.kind is T.IDENT and tok.text.lower() == 'begin' \
                and mode is Mode.SCRIPT:
            script.blocks.append(p._parse_block(tok.line))
            in_body = True
            continue

        # A FRAGMENT is a bare statement list, but a result script may still
        # be pasted in wrapped -- `Begin GameMode` ... `End`.  The wrapper is
        # not a statement: `End` parsed as one becomes the call `End()`, which
        # does not compile.  Skip the line, keeping the body between.
        if tok.kind is T.IDENT and mode is Mode.FRAGMENT \
                and tok.text.lower() in ('begin', 'end'):
            while not p.at_end() and p.cur.kind not in (T.NEWLINE, T.EOF):
                p.advance()
            p.take_line_end()
            continue

        stmt = p.parse_statement(_TOP_TERMINATORS)
        if stmt is None:
            p.advance()
            continue

        # TES4 variables are script-global wherever they are declared, so
        # hoist every declaration and de-duplicate by name.
        if isinstance(stmt, VarDecl):
            if stmt.name.lower() not in seen_vars:
                seen_vars.add(stmt.name.lower())
                script.variables.append(stmt)
            continue

        if isinstance(stmt, (Comment, Blank)) and not in_body \
                and not script.body:
            script.preamble.append(stmt)
        else:
            script.body.append(stmt)

    # A block's body can also declare variables (TES4 allows it); hoist those
    # too so the emitter sees one declaration list.
    for block in script.blocks:
        hoisted = []
        for stmt in block.body:
            if isinstance(stmt, VarDecl):
                if stmt.name.lower() not in seen_vars:
                    seen_vars.add(stmt.name.lower())
                    script.variables.append(stmt)
                continue
            hoisted.append(stmt)
        block.body = hoisted

    return script


def is_self_contained(expr: str) -> bool:
    """Is `expr` a complete expression -- balanced, not ending on an operator?

    Used to tell a condition that a mid-expression comment TRUNCATED from one
    that merely carries an ordinary trailing comment.  Counting brackets by
    character mistakes a parenthesis inside a string for a real one, and a
    regex for a dangling operator has to re-enumerate every operator spelling;
    the lexer already classifies both.
    """
    depth = 0
    last = None
    for tok in tokenize(expr):
        if tok.kind in (T.NEWLINE, T.EOF, T.COMMENT):
            break
        if tok.is_op('(', '['):
            depth += 1
        elif tok.is_op(')', ']'):
            depth -= 1
        last = tok
    if last is None:
        return False
    if depth != 0:
        return False
    # A trailing operator means the right-hand operand was cut off.  `)`/`]`
    # are operators too but legitimately end an expression.
    if last.kind is T.OP:
        return last.text in (')', ']')
    # TES4 also spells three operators as words.
    return not last.is_ident('and', 'or', 'not')
