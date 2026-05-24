"""
nexql/parser/lexer.py
─────────────────────
The Piql lexer (tokenizer).

WHY THIS IS SEPARATE FROM THE PARSER:
    In the original monolith, `PiqlParser.tokenize()` mixed token production
  with parser state.  A standalone lexer can be:
    • unit-tested independently of the parser
    • reused by the syntax highlighter without loading the parser
    • replaced/extended (e.g. for a binary or streaming lexer) without
      touching parser logic

PIPELINE POSITION:
  Raw query string → Lexer → List[Token] → Parser

OWNERSHIP: parser layer only.  No runtime imports here.
"""

from __future__ import annotations
import re
from typing import Iterator

from .tokens import Token, TokenType, EOF_TOKEN


# ─── Compiled master pattern ──────────────────────────────────────────────────
# Order matters: longer alternatives must come first.
_PATTERN = re.compile(
    r"""
    (?P<SPREAD>  \.\.\.)                        |
    (?P<GTE>     >=)                            |
    (?P<LTE>     <=)                            |
    (?P<NEQ>     !=|<>)                         |
    (?P<METHOD>  >>|<<|[?+~!])                  |
    (?P<DIRECTIVE> @\w+)                        |
    (?P<VARIABLE>  \$\w+)                       |
    (?P<STRING>  "(?:[^"\\]|\\.)*")             |
    (?P<NUMBER>  -?\d+\.?\d*[eE][+-]?\d+|-?\d+\.\d+|-?\d+) |
    (?P<STAR>    \*)                            |
    (?P<GT>      >)                             |
    (?P<LT>      <)                             |
    (?P<IDENT>   [A-Za-z_][\w.]*)              |
    (?P<LBRACE>  \{) | (?P<RBRACE>  \})        |
    (?P<LPAREN>  \() | (?P<RPAREN>  \))        |
    (?P<LBRACKET>\[) | (?P<RBRACKET>\])        |
    (?P<COLON>   :)  | (?P<COMMA>   ,)         |
    (?P<UNKNOWN> \S)
    """,
    re.VERBOSE,
)

# keyword override: IDENT values that are actually BOOL or NULL
_KEYWORD_OVERRIDE: dict[str, TokenType] = {
    "true": TokenType.BOOL,
    "false": TokenType.BOOL,
    "null": TokenType.NULL,
    "on": TokenType.IDENT,   # kept IDENT; parser handles "... on Type"
}


class Lexer:
    """Stateless tokenizer.  Call `tokenize()` to get a token stream."""

    @staticmethod
    def unescape_string(raw: str, warnings: list[str] | None = None, context: str = "") -> str:
        """Decode Piql string escapes.

        Supported escapes: \", \\, /, \\b, \\f, \\n, \\r, \\t, \\uXXXX.
        Unknown escapes are preserved as-is and optionally reported as warnings.
        """
        body = raw[1:-1] if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"' else raw
        mapping = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }

        out: list[str] = []
        i = 0
        while i < len(body):
            ch = body[i]
            if ch != "\\":
                out.append(ch)
                i += 1
                continue

            if i + 1 >= len(body):
                out.append("\\")
                if warnings is not None:
                    suffix = f" ({context})" if context else ""
                    warnings.append(f"Invalid escape sequence '\\' at end of string{suffix}")
                i += 1
                continue

            esc = body[i + 1]
            if esc in mapping:
                out.append(mapping[esc])
                i += 2
                continue

            if esc == "u":
                hex_digits = body[i + 2:i + 6]
                if len(hex_digits) == 4 and re.fullmatch(r"[0-9a-fA-F]{4}", hex_digits):
                    out.append(chr(int(hex_digits, 16)))
                    i += 6
                    continue
                out.extend(["\\", "u"])
                if warnings is not None:
                    suffix = f" ({context})" if context else ""
                    warnings.append(f"Invalid unicode escape sequence '\\u{hex_digits}'{suffix}")
                i += 2
                continue

            out.extend(["\\", esc])
            if warnings is not None:
                suffix = f" ({context})" if context else ""
                warnings.append(f"Invalid escape sequence '\\{esc}'{suffix}")
            i += 2

        return "".join(out)

    def tokenize(self, source: str) -> list[Token]:
        """Lex *source* into a list of Token objects (not including EOF).

        Raises:
            Nothing – unknown characters produce UNKNOWN tokens so the
            parser can emit a located error.
        """
        source = self._strip_comments(source or "")
        return list(self._scan(source))

    def tokenize_with_eof(self, source: str) -> list[Token]:
        tokens = self.tokenize(source)
        tokens.append(EOF_TOKEN)
        return tokens

    # ── internal ──────────────────────────────────────────────────────────────

    def _strip_comments(self, source: str) -> str:
        """Remove `#` and `//` comments while preserving source layout.

        Comment text is replaced with spaces so token start/end offsets and
        line/column tracking remain stable for the parser.
        """
        chars = list(source)
        in_string = False
        escaped = False
        i = 0

        while i < len(chars):
            ch = chars[i]

            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                i += 1
                continue

            if ch == '"':
                in_string = True
                i += 1
                continue

            if ch == "#" or (ch == "/" and i + 1 < len(chars) and chars[i + 1] == "/"):
                if ch == "/":
                    chars[i] = " "
                    chars[i + 1] = " "
                    i += 2
                else:
                    chars[i] = " "
                    i += 1

                while i < len(chars) and chars[i] != "\n":
                    chars[i] = " "
                    i += 1
                continue

            i += 1

        return "".join(chars)

    def _scan(self, source: str) -> Iterator[Token]:
        line   = 1
        line_start = 0

        for m in _PATTERN.finditer(source):
            kind_name = m.lastgroup
            value     = m.group(0)
            start     = m.start()
            end       = m.end()
            col       = start - line_start + 1

            # Track newlines (strings may contain them)
            newlines = value.count("\n")
            if newlines:
                line += newlines
                line_start = start + value.rfind("\n") + 1

            ttype = TokenType[kind_name]   # names match enum members exactly

            # Keyword override for IDENT tokens
            if ttype is TokenType.IDENT and value in _KEYWORD_OVERRIDE:
                ttype = _KEYWORD_OVERRIDE[value]

            yield Token(ttype, value, start, end, line, col)
