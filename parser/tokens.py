"""
nexql/parser/tokens.py
─────────────────────
Canonical token type definitions for the Piql lexer.

WHY THIS FILE EXISTS:
  The original codebase used raw strings like "METHOD", "STRING", "IDENT"
  scattered across the tokenizer and parser without a shared definition.
  Any typo in those strings caused silent mismatches.  Centralising them
  here gives a single source of truth, enables IDE completion, and makes
  exhaustive-match linting possible.

OWNERSHIP: parser layer only.  Runtime, validator, and planner consume
  Token objects through the AST; they never inspect raw token types directly.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class TokenType(Enum):
    # Operators / methods
    METHOD     = auto()   # ? + ~ ! >> <<

    # Identifiers and literals
    IDENT      = auto()   # foo, user, Post
    STRING     = auto()   # "hello world"
    NUMBER     = auto()   # 42, -3.14
    BOOL       = auto()   # true | false
    NULL       = auto()   # null

    # Special sigil tokens
    DIRECTIVE  = auto()   # @auth, @cache
    VARIABLE   = auto()   # $limit, $userId

    # Structural punctuation
    LBRACE     = auto()   # {
    RBRACE     = auto()   # }
    LPAREN     = auto()   # (
    RPAREN     = auto()   # )
    LBRACKET   = auto()   # [
    RBRACKET   = auto()   # ]
    COLON      = auto()   # :
    COMMA      = auto()   # ,
    DOT        = auto()   # .
    SPREAD     = auto()   # ...
    STAR       = auto()   # *

    # Comparison operators
    GTE        = auto()   # >=
    LTE        = auto()   # <=
    NEQ        = auto()   # !=
    GT         = auto()   # >
    LT         = auto()   # <

    # Meta
    EOF        = auto()
    UNKNOWN    = auto()


# Reverse lookup: literal string → TokenType (for fast dispatch)
_METHOD_LITERALS = frozenset({"?", "+", "~", "!", ">>", "<<"})
_COMPARATOR_MAP = {
    ">=": TokenType.GTE,
    "<=": TokenType.LTE,
    "!=": TokenType.NEQ,
    "<>": TokenType.NEQ,
    ">": TokenType.GT,
    "<": TokenType.LT,
}
_PUNCT_MAP = {
    "{": TokenType.LBRACE, "}": TokenType.RBRACE,
    "(": TokenType.LPAREN, ")": TokenType.RPAREN,
    "[": TokenType.LBRACKET, "]": TokenType.RBRACKET,
    ":": TokenType.COLON, ",": TokenType.COMMA,
    ".": TokenType.DOT, "*": TokenType.STAR,
}
_KEYWORD_MAP = {"true": TokenType.BOOL, "false": TokenType.BOOL,
                "null": TokenType.NULL}


@dataclass(frozen=True, slots=True)
class Token:
    """Immutable token produced by the lexer.

    Attributes:
        type:    Semantic category of this token.
        value:   Raw string value from the source.
        start:   Byte offset of the first character in the source string.
        end:     Byte offset one past the last character.
        line:    1-based source line number (optional, filled by lexer).
        column:  1-based column of `start` within `line`.
    """
    type:   TokenType
    value:  str
    start:  int = 0
    end:    int = 0
    line:   int = 1
    column: int = 1

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, @{self.start})"


# Sentinel EOF token
EOF_TOKEN = Token(TokenType.EOF, "", -1, -1)
