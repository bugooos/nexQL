"""
nexql/parser/parser.py
──────────────────────
Recursive-descent parser for Piql.

WHY THIS IS ITS OWN MODULE:
    In the monolith, PiqlParser was a single class of ~700 lines that mixed:
    • tokenisation (moved to lexer.py)
    • grammar validation (moved to validator/)
    • AST production (this file)
    • IDE-facing helpers (parse_to_ast, tokenize — kept as thin adapters here)

  Separating these means the parser core can be tested purely against AST
  output without needing an executor, and the validator can be tested
  against AST nodes without re-parsing.

PIPELINE POSITION:
  Lexer → tokens → Parser → ParseResult (QueryDocument | ParseError)

PUBLIC API:
  Parser().parse(source: str) -> ParseResult
  Parser().tokenize(source: str) -> list[Token]   (thin adapter for IDE)
"""

from __future__ import annotations
import re
from typing import Any, Optional

from .lexer import Lexer
from .tokens import Token, TokenType, EOF_TOKEN
from ..nexql_ast.nodes import (
    Method, VariableRef, DeleteMarker, Directive, FieldSelection,
    QueryDocument, ParseError, ParseResult, NqlValue,
)

# Reserved words that may not be used as collection targets in mutating ops
_RESERVED = frozenset({"type", "schema", "fragment", "alias", "true", "false", "null"})

# $-directives that are accepted (others produce warnings, not errors)
_ALLOWED_DIRECTIVES = frozenset({"limit", "offset", "after", "sort", "fields"})


class Parser:
    """Stateless recursive-descent parser.  Create once, call parse() many times."""

    def __init__(self) -> None:
        self._lexer = Lexer()

    # ─── Public API ──────────────────────────────────────────────────────────

    def parse(self, source: str) -> ParseResult:
        """Parse *source* into a QueryDocument or ParseError."""
        source = (source or "").strip()
        if not source:
            return ParseError("Query is empty")

        try:
            self._validate_string_literals(source)
            tokens = self._lexer.tokenize_with_eof(source)
            state  = _ParserState(tokens, source)
            return self._parse_document(state)
        except _ParseException as exc:
            return ParseError(exc.message, exc.position, exc.line, exc.column)
        except Exception as exc:
            return ParseError(f"Internal parser error: {exc}")

    def tokenize(self, source: str) -> list[dict]:
        """Return token dicts compatible with the IDE protocol (legacy adapter)."""
        tokens = self._lexer.tokenize(source or "")
        return [{"type": t.type.name, "value": t.value,
                 "start": t.start, "end": t.end,
                 "line": t.line, "column": t.column}
                for t in tokens]

    # ─── Grammar rules ───────────────────────────────────────────────────────

    def _parse_document(self, s: "_ParserState") -> ParseResult:
        warnings: list[str] = []

        # ── method operator ─────────────────────────────────────────────────
        if s.peek().type is not TokenType.METHOD:
            return ParseError(
                f"Expected a method operator (?, +, ~, !, >>, <<) "
                f"but found {s.peek().value!r}",
                s.peek().start, s.peek().line, s.peek().column)
        method_tok = s.consume()
        try:
            method = Method.from_operator(method_tok.value)
        except ValueError as e:
            return ParseError(str(e), method_tok.start)

        # ── optional operation name (PascalCase) + target ───────────────────
        if s.peek().type is not TokenType.IDENT:
            return ParseError("Expected a target name after the method operator",
                              s.peek().start, s.peek().line, s.peek().column)

        first_ident = s.consume()
        op_name: Optional[str] = None
        target_tok: Token

        # If the first ident starts with an uppercase letter AND the next
        # token is also an IDENT, treat first as an operation name.
        if first_ident.value[:1].isupper() and s.peek().type is TokenType.IDENT:
            op_name    = first_ident.value
            target_tok = s.consume()
        else:
            target_tok = first_ident

        target = target_tok.value
        if "." in target:
            return ParseError(f"Target '{target}' cannot contain dots",
                              target_tok.start)
        if target.lower() in _RESERVED and method.is_mutation:
            return ParseError(f"'{target}' is a reserved keyword and cannot be used as a target",
                              target_tok.start)

        # ── args block(s) ────────────────────────────────────────────────────
        args: dict[str, NqlValue] = {}
        or_groups: list[dict] = []

        while s.peek().type is TokenType.LPAREN:
            s.consume()  # (
            group = self._parse_args(s, warnings)
            s.expect(TokenType.RPAREN, ")")
            # Warn if the filter block is empty
            if not group:
                warnings.append("Empty filter block (); remove () for cleaner syntax")
            or_groups.append(group)
            # if next IDENT is "or" and then "(", consume "or"
            if (s.peek().type is TokenType.IDENT
                    and s.peek().value == "or"
                    and s.peek_ahead(1).type is TokenType.LPAREN):
                s.consume()  # or
            else:
                break

        if len(or_groups) > 1:
            args = {"__or__": or_groups}
        elif or_groups:
            args = or_groups[0]

        # ── brace blocks ─────────────────────────────────────────────────────
        payload: dict[str, NqlValue] = {}
        fields:  list[FieldSelection] = []
        directives: list[Directive]   = []

        if method in (Method.CREATE, Method.UPDATE):
            # First { block } = payload (key value pairs)
            if s.peek().type is TokenType.LBRACE:
                s.consume()
                payload = self._parse_args(
                    s,
                    warnings,
                    allow_delete=(method is Method.UPDATE),
                )
                s.expect(TokenType.RBRACE, "}")
            # Optional second { block } = projection fields
            if s.peek().type is TokenType.LBRACE:
                s.consume()
                fields = self._parse_fields(s, warnings)
                s.expect(TokenType.RBRACE, "}")
        else:
            # READ / DELETE / SUBSCRIBE / PUBLISH — single optional projection
            if s.peek().type is TokenType.LBRACE:
                s.consume()
                fields = self._parse_fields(s, warnings)
                s.expect(TokenType.RBRACE, "}")

        # ── top-level directives (@name args?) ───────────────────────────────
        while s.peek().type is TokenType.DIRECTIVE:
            directives.append(self._parse_directive(s, warnings))

        # ── post-parse semantic validation ───────────────────────────────────
        self._validate_args(args, warnings)

        return QueryDocument(
            method         = method,
            target         = target,
            args           = args,
            payload        = payload,
            fields         = fields,
            directives     = directives,
            operation_name = op_name,
            warnings       = warnings,
            source         = s.source,
        )

    # ── args / filters ───────────────────────────────────────────────────────

    def _parse_args(
        self,
        s: "_ParserState",
        warnings: list[str],
        allow_delete: bool = False,
    ) -> dict[str, NqlValue]:
        """Parse a key-value argument block (no surrounding braces)."""
        args: dict[str, NqlValue] = {}
        or_groups: list[dict]     = []   # unused here; handled at document level

        while s.peek().type not in (TokenType.RPAREN, TokenType.RBRACE, TokenType.EOF):
            tok = s.peek()

            # skip commas (not part of spec but forgiving)
            if tok.type is TokenType.COMMA:
                s.consume()
                continue

            # read key
            is_dollar = tok.type is TokenType.VARIABLE
            is_delete = allow_delete and tok.type is TokenType.METHOD and tok.value == "!"

            if is_delete:
                s.consume()
                if s.peek().type is not TokenType.IDENT:
                    break
                raw_key = s.consume().value
            elif is_dollar:
                raw_key = s.consume().value[1:]   # strip $
            elif tok.type is TokenType.IDENT:
                raw_key = s.consume().value
            else:
                break   # stop gracefully

            # skip optional colon
            if s.peek().type is TokenType.COLON:
                s.consume()

            if is_delete:
                args[raw_key] = DeleteMarker()
                continue

            # check for .any() suffix: field.any(...) gets parsed as IDENT("field.any")
            if raw_key.endswith(".any") and s.peek().type is TokenType.LPAREN:
                field_name = raw_key[:-4]  # strip ".any"
                s.consume()  # consume (
                any_filters = self._parse_args(s, warnings, allow_delete=False)
                s.expect(TokenType.RPAREN, ")")
                args[field_name] = {"__any__": any_filters}
                continue

            # check for comparator
            comparator: Optional[str] = None
            if s.peek().type in (TokenType.GTE, TokenType.LTE,
                                  TokenType.NEQ, TokenType.GT, TokenType.LT):
                comparator = s.consume().value

            value = self._parse_value(s, warnings, allow_delete=allow_delete)

            if is_dollar and raw_key not in _ALLOWED_DIRECTIVES:
                warnings.append(f"Unknown $-keyword '${raw_key}' ignored")
                continue

            if raw_key == "sort":
                # After reading the field name as `value`, optionally consume
                # a direction keyword (asc / desc) that follows it bare.
                direction = "asc"
                if (s.peek().type is TokenType.IDENT
                        and s.peek().value.lower() in ("asc", "desc")):
                    direction = s.consume().value.lower()
                if isinstance(value, str):
                    value = {"field": value, "direction": direction}
                else:
                    value = self._normalise_sort(value)
                existing = args.get("sort")
                if existing is None:
                    args["sort"] = value
                elif isinstance(existing, list):
                    existing.append(value)
                else:
                    args["sort"] = [existing, value]
                continue

            if comparator:
                args[raw_key] = {comparator: value}
            else:
                args[raw_key] = value

        return args

    def _parse_value(
        self,
        s: "_ParserState",
        warnings: list[str],
        allow_delete: bool = False,
    ) -> NqlValue:
        tok = s.peek()

        if tok.type is TokenType.LBRACE:
            s.consume()
            obj: dict[str, NqlValue] = {}
            while s.peek().type not in (TokenType.RBRACE, TokenType.EOF):
                if s.peek().type is TokenType.COMMA:
                    s.consume(); continue
                if allow_delete and s.peek().type is TokenType.METHOD and s.peek().value == "!":
                    s.consume()
                    if s.peek().type is not TokenType.IDENT:
                        warnings.append("Expected field name after '!' in update payload")
                        continue
                    k = s.consume().value
                    if s.peek().type is TokenType.COLON:
                        s.consume()
                    obj[k] = DeleteMarker()
                    continue
                k = s.consume().value
                if s.peek().type is TokenType.COLON:
                    s.consume()
                obj[k] = self._parse_value(s, warnings, allow_delete=allow_delete)
            s.expect(TokenType.RBRACE, "}")
            return obj

        if tok.type is TokenType.LBRACKET:
            s.consume()
            arr: list[NqlValue] = []
            while s.peek().type not in (TokenType.RBRACKET, TokenType.EOF):
                if s.peek().type is TokenType.COMMA:
                    s.consume(); continue
                arr.append(self._parse_value(s, warnings, allow_delete=allow_delete))
            s.expect(TokenType.RBRACKET, "]")
            return arr

        if tok.type is TokenType.VARIABLE:
            s.consume()
            return VariableRef(tok.value[1:])

        if tok.type is TokenType.STRING:
            s.consume()
            return Lexer.unescape_string(
                tok.value,
                warnings=warnings,
                context=f"line {tok.line}, column {tok.column}",
            )

        if tok.type is TokenType.NUMBER:
            s.consume()
            # Scientific notation always becomes float (1e10, 3.14e-5, etc.)
            if "e" in tok.value.lower():
                return float(tok.value)
            # Decimal point → float
            elif "." in tok.value:
                return float(tok.value)
            # Otherwise → int
            else:
                return int(tok.value)

        if tok.type is TokenType.BOOL:
            s.consume()
            return tok.value == "true"

        if tok.type is TokenType.NULL:
            s.consume()
            return None

        if tok.type is TokenType.IDENT:
            s.consume()
            return tok.value

        # Unexpected token – consume it to avoid infinite loops, warn
        s.consume()
        warnings.append(f"Unexpected token {tok.value!r} in value position")
        return None

    def _normalise_sort(self, value: NqlValue) -> dict:
        if isinstance(value, dict) and "field" in value:
            return value
        if isinstance(value, list) and value:
            field = value[0]
            direction = str(value[1]).lower() if len(value) > 1 else "asc"
            return {"field": field, "direction": direction}
        return {"field": str(value), "direction": "asc"}

    # ── field selection ───────────────────────────────────────────────────────

    def _parse_fields(self, s: "_ParserState", warnings: list[str]) -> list[FieldSelection]:
        fields: list[FieldSelection] = []

        while s.peek().type not in (TokenType.RBRACE, TokenType.EOF):
            tok = s.peek()

            if tok.type is TokenType.COMMA:
                s.consume(); continue

            # wildcard
            if tok.type is TokenType.STAR:
                s.consume()
                fields.append(FieldSelection(name="*"))
                continue

            # spread: ... on TypeName { ... }
            if tok.type is TokenType.SPREAD:
                s.consume()
                if (s.peek().type is TokenType.IDENT
                        and s.peek().value == "on"):
                    s.consume()   # on
                    if s.peek().type is not TokenType.IDENT:
                        raise _ParseException("Expected type name after 'on'", s.peek())
                    type_name = s.consume().value
                    nested: list[FieldSelection] = []
                    if s.peek().type is TokenType.LBRACE:
                        s.consume()
                        nested = self._parse_fields(s, warnings)
                        s.expect(TokenType.RBRACE, "}")
                    fields.append(FieldSelection(
                        name="", type_condition=type_name, fields=nested))
                else:
                    # old fragment spreads – warn and skip
                    frag_name = s.consume().value if s.peek().type is TokenType.IDENT else "?"
                    warnings.append(f"Fragment spread '..{frag_name}' is not supported; ignored")
                continue

            # directive token at top level — skip (already consumed at doc level)
            if tok.type is TokenType.DIRECTIVE:
                self._parse_directive(s, warnings)
                continue

            # Delete markers are only valid in payload blocks, never in projections.
            if tok.type is TokenType.METHOD and tok.value == "!":
                raise _ParseException(
                    "Delete markers ('!') are not allowed in projection blocks; use them only in payload blocks",
                    tok,
                )

            # regular field / nested
            if tok.type is TokenType.IDENT:
                name = s.consume().value

                # Reject dotted field names (e.g., "profile.bio")
                # Dots are not allowed in projection field names; use nested blocks for traversal
                if "." in name:
                    raise _ParseException(
                        f"Field name '{name}' contains dots. "
                        f"Use nested blocks for traversal: {name.split('.')[0]} {{ {' '.join(name.split('.')[1:])} }}",
                        s.peek()
                    )

                # optional directives on this field
                field_directives: list[Directive] = []
                while s.peek().type is TokenType.DIRECTIVE:
                    field_directives.append(self._parse_directive(s, warnings))

                # optional field-level filters: field (key val, key val)
                field_filters: dict[str, NqlValue] = {}
                if s.peek().type is TokenType.LPAREN:
                    s.consume()  # (
                    field_filters = self._parse_args(s, warnings)
                    s.expect(TokenType.RPAREN, ")")

                # optional nested block
                nested_fields: list[FieldSelection] = []
                if s.peek().type is TokenType.LBRACE:
                    s.consume()
                    nested_fields = self._parse_fields(s, warnings)
                    s.expect(TokenType.RBRACE, "}")

                fields.append(FieldSelection(
                    name=name,
                    directives=field_directives,
                    filters=field_filters,
                    fields=nested_fields,
                ))
                continue

            # unknown token in field position – skip
            s.consume()

        return fields

    # ── directives ────────────────────────────────────────────────────────────

    def _parse_directive(self, s: "_ParserState", warnings: list[str]) -> Directive:
        tok = s.consume()  # @name
        name = tok.value[1:]  # strip @
        directive_args: dict[str, NqlValue] = {}
        if s.peek().type is TokenType.LPAREN:
            s.consume()
            directive_args = self._parse_args(s, warnings)
            s.expect(TokenType.RPAREN, ")")
        return Directive(name=name, args=directive_args)

    # ── semantic validation helpers ───────────────────────────────────────────

    def _validate_args(self, args: dict, warnings: list[str]) -> None:
        """Post-parse light semantic validation. Errors produce warnings (not exceptions)
        here; the Validator module enforces them as hard errors."""
        limit = args.get("limit")
        if limit is not None and not isinstance(limit, int):
            warnings.append("$limit must be an integer")
        if isinstance(limit, int) and limit <= 0:
            warnings.append("$limit must be a positive integer")

        offset = args.get("offset")
        if offset is not None and not isinstance(offset, int):
            warnings.append("$offset must be an integer")
        if isinstance(offset, int) and offset < 0:
            warnings.append("$offset must be non-negative")

        if "after" in args and "offset" in args:
            warnings.append("$after and $offset cannot be used together")

    def _validate_string_literals(self, source: str) -> None:
        """Reject unclosed string literals before tokenization.

        This preserves the existing lexer/parser behavior for valid strings,
        including multiline strings, while failing fast on an opening quote
        that never closes.
        """
        in_string = False
        escaped = False
        start_line = 0
        start_column = 0
        line = 1
        column = 0

        for ch in source:
            if ch == "\n":
                line += 1
                column = 0
                escaped = False
                continue

            column += 1

            if not in_string:
                if ch == '"':
                    in_string = True
                    start_line = line
                    start_column = column
                continue

            if escaped:
                escaped = False
                continue

            if ch == "\\":
                escaped = True
                continue

            if ch == '"':
                in_string = False

        if in_string:
            raise _ParseException(
                "Unclosed string literal",
                position=0,
                line=start_line,
                column=start_column,
            )


# ─── Internal helpers ─────────────────────────────────────────────────────────

class _ParseException(Exception):
    def __init__(
        self,
        message: str,
        token: Optional[Token] = None,
        *,
        position: int = 0,
        line: int = 0,
        column: int = 0,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.token   = token
        if token is not None:
            self.position = token.start
            self.line = token.line
            self.column = token.column
        else:
            self.position = position
            self.line = line
            self.column = column


class _ParserState:
    """Thin cursor over a flat token list."""

    def __init__(self, tokens: list[Token], source: str) -> None:
        self._tokens = tokens
        self._pos    = 0
        self.source  = source

    def peek(self, offset: int = 0) -> Token:
        idx = self._pos + offset
        if idx >= len(self._tokens):
            return EOF_TOKEN
        return self._tokens[idx]

    def peek_ahead(self, n: int) -> Token:
        return self.peek(n)

    def consume(self) -> Token:
        tok = self.peek()
        self._pos += 1
        return tok

    def expect(self, ttype: TokenType, display: str) -> Token:
        tok = self.peek()
        if tok.type is not ttype:
            raise _ParseException(
                f"Expected '{display}' but found {tok.value!r}", tok)
        return self.consume()
