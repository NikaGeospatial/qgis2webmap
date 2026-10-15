"""Translating QGIS label expressions into cartograph `{{tokens}}`.

A QGIS label commonly interleaves literal text with `[% expr %]` blocks,
evaluated against the layout's expression context. A handful of those
expressions have an exact, *dynamic* equivalent in the cartograph runtime's
token vocabulary (`docs/cartograph.md` "Tokens"); everything else has no
cartograph-side evaluator, so it has to be evaluated once at export time and
frozen into the output as a literal string.

This module does the first half only -- the pure, syntactic translation. It
never touches `QgsExpression`: recognising `@map_scale` is a string match, but
*evaluating* an arbitrary expression needs PyQGIS and the layout's live
context, which belongs to `core/layout_reader.py` (not written yet). So an
unrecognised (or recognised-but-ineligible) expression comes back as a
"freeze" marker -- its raw text, to be evaluated by the caller and slotted
back in with `TranslatedLabel.assemble()`.

Corrections against issue #1 §2.1, from `specs/cartograph/spike-findings.md`:

- `{{scale}}` and `{{crs}}` are only unambiguous on a page with exactly one
  frame -- the runtime itself warns otherwise. This module never emits them:
  every scale/CRS token is frame-qualified (`{{frame.<id>.scale}}` /
  `{{frame.<id>.crs}}`), using the label's linked frame (or the page's
  reference frame, chosen by the caller and passed in as `frame_id`).
- `@map_scale` is **frozen**, not tokenised, on a **static** frame: QGIS's
  `item.scale()` and the runtime's homography-derived scale disagree by about
  0.1% (ground vs. grid distance), and a static frame's QGIS-rendered raster
  never moves, so the token would silently drift from what the picture shows.
  `@map_crs` has no such mismatch -- both modes know the CRS exactly -- so it
  is always tokenised.
- `now()`/`format_date(now(), …)`: the runtime's `{{date}}` token is always
  ISO-8601 (`2026-09-08`). A `format_date` call asking for anything else must
  be frozen to QGIS's own formatting, never silently reformatted.
- The atlas tokens (`{{atlas.index}}`, `{{atlas.count}}`, `{{atlas.<field>}}`)
  only make sense while an atlas is iterating; outside one they are frozen
  like any other unrecognised expression.
- A `{{atlas.<field>}}` token only resolves for a field name the runtime's
  token pattern can spell -- ASCII letters, digits and `_` (its pattern is
  JavaScript's word class, which is ASCII-only). Any other name, and the
  two names the runtime reserves for its own page number and page count
  (`index`, `count`), are frozen instead of producing a token that would
  render literally on every page.
- `'text'` is a QGIS string literal, never a field: only `"double quoted"` (or
  bare) names are field references.

The atlas's own expressions -- its filter, sort, filename and page name --
are translated here too (`translate_filter_expression`,
`translate_sort_expression`, `translate_atlas_pattern`), by one small
tokenizer and a deliberately narrow recursive-descent parser. Narrow on
purpose: an expression it does not fully understand comes back as `None`, and
the caller records it as approximated, rather than being half-translated into
a predicate that pages a different set of features than QGIS does.

Pure Python: no PyQGIS, no Qt, unit-tested in CI.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# Private-use-area sentinels delimiting a frozen-expression placeholder inside
# `TranslatedLabel.template`. They cannot appear in text QGIS or a user can
# type, so `assemble()` can find them with a plain string search.
_PLACEHOLDER_OPEN = ""
_PLACEHOLDER_CLOSE = ""


def _placeholder(index: int) -> str:
    return f"{_PLACEHOLDER_OPEN}{index}{_PLACEHOLDER_CLOSE}"


_PLACEHOLDER_RE = re.compile(f"{_PLACEHOLDER_OPEN}(\\d+){_PLACEHOLDER_CLOSE}")

# --------------------------------------------------------------------------
# Recognised expressions (issue #1 §2.1, corrected against the runtime)
# --------------------------------------------------------------------------

_MAP_SCALE_RE = re.compile(r"^(?:round\s*\(\s*)?@map_scale(?:\s*\))?$", re.IGNORECASE)
_MAP_CRS_RE = re.compile(r"^@map_crs(?:_description)?$", re.IGNORECASE)
_PROJECT_TITLE_RE = re.compile(r"^@project_title$", re.IGNORECASE)
_NOW_RE = re.compile(r"^now\s*\(\s*\)$", re.IGNORECASE)
_FORMAT_DATE_NOW_RE = re.compile(
    r"^format_date\s*\(\s*now\s*\(\s*\)\s*,\s*'([^']*)'\s*\)$", re.IGNORECASE
)
# The runtime's {{date}} token is always this ISO pattern -- see the module
# docstring. Any other format string must be frozen.
_ISO_DATE_FORMAT = "yyyy-MM-dd"
_ATLAS_FEATURENUMBER_RE = re.compile(r"^@atlas_featurenumber$", re.IGNORECASE)
_ATLAS_TOTALFEATURES_RE = re.compile(r"^@atlas_totalfeatures$", re.IGNORECASE)


@dataclass(frozen=True)
class TranslatedLabel:
    """The result of translating one label's text.

    `template` is the text with every recognised expression already replaced
    by its literal token (e.g. `{{frame.main.scale}}`) and every unrecognised
    or ineligible one replaced by a placeholder. `frozen_expressions` holds
    the raw QGIS expression text for each placeholder, in the order they
    appear in `template` -- that order is the contract `assemble()` relies on.
    """

    template: str
    frozen_expressions: tuple[str, ...] = ()

    def assemble(self, frozen_values: Sequence[str]) -> str:
        """Substitute each frozen placeholder with its evaluated value.

        `frozen_values` must align 1:1 with `frozen_expressions` -- the caller
        evaluates each raw expression (with `QgsExpression` and the layout's
        context) and passes the results back in the same order.
        """
        if len(frozen_values) != len(self.frozen_expressions):
            raise ValueError(
                f"expected {len(self.frozen_expressions)} frozen value(s), "
                f"got {len(frozen_values)}"
            )

        def _replace(match: re.Match[str]) -> str:
            return frozen_values[int(match.group(1))]

        return _PLACEHOLDER_RE.sub(_replace, self.template)


@dataclass
class _Builder:
    frame_id: str
    frame_is_live: bool
    in_atlas: bool
    pieces: list[str] = field(default_factory=list)
    frozen: list[str] = field(default_factory=list)

    def literal(self, text: str) -> None:
        if text:
            self.pieces.append(text)

    def token(self, text: str) -> None:
        self.pieces.append(text)

    def freeze(self, expression: str) -> None:
        index = len(self.frozen)
        self.frozen.append(expression)
        self.pieces.append(_placeholder(index))

    def result(self) -> TranslatedLabel:
        return TranslatedLabel(
            template="".join(self.pieces), frozen_expressions=tuple(self.frozen)
        )


def _split_expression_blocks(text: str) -> list[tuple[str, str]]:
    """Split `text` into `("text", literal)` / `("expr", inner)` pairs.

    A block starts at `[%` and ends at the next `%]` that is not nested inside
    a bracketed sub-expression (e.g. an array index `arr[0]` inside the
    expression) -- plain `[`/`]` characters between the markers add to a depth
    counter so they cannot be mistaken for the block's own delimiters. A block
    with no matching close is not a block at all: everything from its `[%`
    onward is literal text, exactly as a user who typed a stray `[%` with no
    intention of writing an expression would expect to see it rendered.
    """
    parts: list[tuple[str, str]] = []
    literal_start = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i : i + 2] == "[%":
            j = i + 2
            depth = 0
            close = -1
            while j < n:
                if depth == 0 and text[j : j + 2] == "%]":
                    close = j
                    break
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]" and depth > 0:
                    depth -= 1
                j += 1
            if close == -1:
                break  # unterminated: the rest is literal text (handled below)
            if i > literal_start:
                parts.append(("text", text[literal_start:i]))
            parts.append(("expr", text[i + 2 : close].strip()))
            i = close + 2
            literal_start = i
        else:
            i += 1
    if literal_start < n:
        parts.append(("text", text[literal_start:n]))
    return parts


def translate_label(
    text: str,
    *,
    frame_id: str,
    frame_is_live: bool,
    in_atlas: bool = False,
) -> TranslatedLabel:
    """Translate one QGIS label's text into a cartograph template.

    `frame_id` is the label's linked map, or the layout's reference map when
    the label has none -- the caller resolves that; this function only needs
    the id to frame-qualify `{{frame.<id>.scale}}` / `{{frame.<id>.crs}}`.
    """
    builder = _Builder(
        frame_id=frame_id, frame_is_live=frame_is_live, in_atlas=in_atlas
    )
    for kind, value in _split_expression_blocks(text):
        if kind == "text":
            builder.literal(value)
        else:
            _translate_expression(value, builder)
    return builder.result()


def _translate_expression(expr: str, builder: _Builder) -> None:
    if _MAP_SCALE_RE.match(expr):
        if builder.frame_is_live:
            builder.token(f"{{{{frame.{builder.frame_id}.scale}}}}")
        else:
            # Frozen on static frames: see the module docstring's scale note.
            builder.freeze(expr)
        return

    if _MAP_CRS_RE.match(expr):
        builder.token(f"{{{{frame.{builder.frame_id}.crs}}}}")
        return

    if _PROJECT_TITLE_RE.match(expr):
        builder.token("{{title}}")
        return

    if _NOW_RE.match(expr):
        builder.token("{{date}}")
        return

    format_date_match = _FORMAT_DATE_NOW_RE.match(expr)
    if format_date_match:
        if format_date_match.group(1) == _ISO_DATE_FORMAT:
            builder.token("{{date}}")
        else:
            # A non-ISO format must be frozen to QGIS's own rendering -- the
            # runtime's {{date}} token has no format argument.
            builder.freeze(expr)
        return

    if builder.in_atlas:
        if _ATLAS_FEATURENUMBER_RE.match(expr):
            builder.token("{{atlas.index}}")
            return
        if _ATLAS_TOTALFEATURES_RE.match(expr):
            builder.token("{{atlas.count}}")
            return
        # A field, `attribute(@atlas_feature, ...)`, or a `||`/`concat()`
        # of those with literals and the two atlas variables -- the same
        # translation the atlas filename and page name use.
        pattern = translate_atlas_pattern(expr)
        if pattern is not None:
            builder.token(pattern)
            return

    # Anything else -- including atlas-shaped expressions outside an atlas --
    # has no cartograph-side evaluator and must be frozen.
    builder.freeze(expr)


# --------------------------------------------------------------------------
# Atlas expressions: filename / page name patterns, filter, sort
# --------------------------------------------------------------------------

#: The field names a runtime `{{atlas.<field>}}` token can address: ASCII word
#: characters only (JavaScript's `\w`), see the module docstring.
_TOKEN_FIELD_RE = re.compile(r"^[A-Za-z0-9_]+$")
#: `$field` in the runtime's core expression language (`/^\$[A-Za-z_][\w]*$/`).
_DOLLAR_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: Names the runtime resolves to the page number and page count itself.
_RESERVED_ATLAS_NAMES = frozenset({"index", "count"})

_KEYWORDS = frozenset(
    {
        "AND",
        "OR",
        "NOT",
        "IS",
        "IN",
        "LIKE",
        "ILIKE",
        "NULL",
        "TRUE",
        "FALSE",
        "BETWEEN",
        "CASE",
        "WHEN",
        "THEN",
        "ELSE",
        "END",
    }
)

_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<string>'(?:[^'\\]|''|\\.)*')
  | (?P<column>"(?:[^"]|"")*")
  | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<variable>@[A-Za-z_][A-Za-z0-9_]*)
  | (?P<special>\$[A-Za-z_][A-Za-z0-9_]*)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\|\||<>|!=|<=|>=|=|<|>|\+|-|\*|/|%|\^|\(|\)|,|~)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class _Token:
    kind: str  # string / column / number / variable / special / name / keyword / op
    value: str


def _tokenize(expression: str) -> list[_Token] | None:
    """QGIS expression text -> tokens, or `None` on anything unrecognised.

    Strings are unescaped (`''` and `\'` both mean a quote); column names
    lose their double quotes; keywords are upper-cased so the parsers below
    can compare them directly.
    """
    tokens: list[_Token] = []
    position = 0
    while position < len(expression):
        match = _TOKEN_RE.match(expression, position)
        if match is None:
            return None
        position = match.end()
        kind = match.lastgroup or ""
        text = match.group(0)
        if kind == "space":
            continue
        if kind == "string":
            body = text[1:-1].replace("''", "'")
            body = re.sub(r"\\(.)", r"\1", body)
            tokens.append(_Token("string", body))
        elif kind == "column":
            tokens.append(_Token("column", text[1:-1].replace('""', '"')))
        elif kind == "name" and text.upper() in _KEYWORDS:
            tokens.append(_Token("keyword", text.upper()))
        else:
            tokens.append(_Token(kind, text))
    return tokens


def _is_op(token: _Token | None, *values: str) -> bool:
    return token is not None and token.kind == "op" and token.value in values


def _is_keyword(token: _Token | None, *values: str) -> bool:
    return token is not None and token.kind == "keyword" and token.value in values


def _field_name(tokens: list[_Token]) -> str | None:
    """The field a token run names on its own: `"lot"`, `lot`, or
    `attribute(@atlas_feature, 'lot')`."""
    if len(tokens) == 1 and tokens[0].kind in ("column", "name"):
        return tokens[0].value
    if (
        len(tokens) == 6
        and tokens[0].kind == "name"
        and tokens[0].value.lower() == "attribute"
        and _is_op(tokens[1], "(")
        and tokens[2].kind == "variable"
        and tokens[2].value.lower() == "@atlas_feature"
        and _is_op(tokens[3], ",")
        and tokens[4].kind == "string"
        and _is_op(tokens[5], ")")
    ):
        return tokens[4].value
    return None


def _split_top_level(tokens: list[_Token], separator: str) -> list[list[_Token]]:
    """Split on `separator` ops that are not inside parentheses."""
    parts: list[list[_Token]] = [[]]
    depth = 0
    for token in tokens:
        if _is_op(token, "("):
            depth += 1
        elif _is_op(token, ")"):
            depth -= 1
        if depth == 0 and _is_op(token, separator):
            parts.append([])
        else:
            parts[-1].append(token)
    return parts


def _strip_parentheses(tokens: list[_Token]) -> list[_Token]:
    """`( x )` -> `x`, for as many wrapping pairs as genuinely wrap the whole."""
    while len(tokens) >= 2 and _is_op(tokens[0], "(") and _is_op(tokens[-1], ")"):
        depth = 0
        for index, token in enumerate(tokens):
            if _is_op(token, "("):
                depth += 1
            elif _is_op(token, ")"):
                depth -= 1
            if depth == 0 and index < len(tokens) - 1:
                return tokens  # the first "(" closes before the end
        tokens = tokens[1:-1]
    return tokens


def _pattern_piece(tokens: list[_Token]) -> str | None:
    tokens = _strip_parentheses(tokens)
    parts = _split_top_level(tokens, "||")
    if len(parts) > 1:
        pieces = [_pattern_piece(part) for part in parts]
        if any(piece is None for piece in pieces):
            return None
        return "".join(piece for piece in pieces if piece is not None)
    if len(tokens) == 1:
        token = tokens[0]
        if token.kind == "string":
            # A literal that itself spells a token would be resolved by the
            # runtime as one; refuse rather than change its meaning.
            return None if "{{" in token.value else token.value
        if token.kind == "number":
            return token.value
        if token.kind == "variable":
            name = token.value.lower()
            if name == "@atlas_featurenumber":
                return "{{atlas.index}}"
            if name == "@atlas_totalfeatures":
                return "{{atlas.count}}"
            return None
    field_name = _field_name(tokens)
    if (
        field_name is not None
        and _TOKEN_FIELD_RE.match(field_name)
        and field_name.lower() not in _RESERVED_ATLAS_NAMES
    ):
        return f"{{{{atlas.{field_name}}}}}"
    if (
        tokens
        and tokens[0].kind == "name"
        and tokens[0].value.lower() == "concat"
        and len(tokens) >= 3
        and _is_op(tokens[1], "(")
        and _is_op(tokens[-1], ")")
        and _strip_parentheses(tokens[1:]) != tokens[1:]
    ):
        pieces = [
            _pattern_piece(argument) for argument in _split_top_level(tokens[2:-1], ",")
        ]
        if any(piece is None for piece in pieces):
            return None
        return "".join(piece for piece in pieces if piece is not None)
    return None


def translate_atlas_pattern(expression: str) -> str | None:
    """A QGIS atlas expression -> a runtime token pattern, or `None`.

    Understands what atlas filenames, page names and atlas-page labels are
    actually made of: string and number literals, fields (`"lot"`, `lot`,
    `attribute(@atlas_feature, 'lot')`), `@atlas_featurenumber` ->
    `{{atlas.index}}` (both 1-based), `@atlas_totalfeatures` ->
    `{{atlas.count}}`, joined with `||` or `concat()`. QGIS's own default
    filename `'output_'||@atlas_featurenumber` becomes `output_{{atlas.index}}`.

    Anything else (a function call, arithmetic, `@atlas_pagename`, which the
    runtime has no token for) returns `None`.
    """
    tokens = _tokenize(expression.strip())
    if not tokens:
        return None
    return _pattern_piece(tokens)


def translate_sort_expression(expression: str, *, ascending: bool) -> str | None:
    """A QGIS atlas sort expression -> the runtime's `sort=` value, or `None`.

    The runtime sorts by a field (`"lot"`, `"-lot"` for descending), so only
    an expression that *is* a field translates. A name containing a comma or
    starting with `-` would be read as a list or a direction by the runtime's
    parser, so it is refused too.
    """
    tokens = _tokenize(expression.strip())
    if not tokens:
        return None
    field_name = _field_name(_strip_parentheses(tokens))
    if (
        field_name is None
        or not field_name.strip()
        or field_name != field_name.strip()
        or "," in field_name
        or field_name.startswith("-")
    ):
        return None
    return field_name if ascending else f"-{field_name}"


class _UntranslatableError(Exception):
    """Raised inside the filter parser for anything it does not translate."""


class _FilterParser:
    """QGIS filter expression -> the runtime's core expression language.

    The core language is a whitelisted JavaScript expression subset with
    `$field` sugar (`docs/cartograph.md` "Atlas": `filter="$area > 500"`).
    Every sub-expression is parenthesised on the way out, so the output keeps
    QGIS's precedence whatever JavaScript's would have been.
    """

    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.position = 0

    def peek(self) -> _Token | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> _Token:
        token = self.peek()
        if token is None:
            raise _UntranslatableError("unexpected end")
        self.position += 1
        return token

    def parse(self) -> str:
        result = self.or_expr()
        if self.peek() is not None:
            raise _UntranslatableError("trailing tokens")
        return result

    def or_expr(self) -> str:
        left = self.and_expr()
        while _is_keyword(self.peek(), "OR"):
            self.take()
            left = f"({left} || {self.and_expr()})"
        return left

    def and_expr(self) -> str:
        left = self.not_expr()
        while _is_keyword(self.peek(), "AND"):
            self.take()
            left = f"({left} && {self.not_expr()})"
        return left

    def not_expr(self) -> str:
        if _is_keyword(self.peek(), "NOT"):
            self.take()
            return f"(!{self.not_expr()})"
        return self.comparison()

    def comparison(self) -> str:
        left = self.additive()
        token = self.peek()
        if _is_op(token, "=", "<>", "!=", "<", ">", "<=", ">="):
            operator = self.take().value
            js_operator = {"=": "==", "<>": "!="}.get(operator, operator)
            return f"({left} {js_operator} {self.additive()})"
        if _is_keyword(token, "IS"):
            self.take()
            negated = _is_keyword(self.peek(), "NOT")
            if negated:
                self.take()
            if not _is_keyword(self.take(), "NULL"):
                raise _UntranslatableError("IS with a non-NULL operand")
            return f"({left} {'!=' if negated else '=='} null)"
        negated = False
        if _is_keyword(token, "NOT") and _is_keyword(
            self.tokens[self.position + 1]
            if self.position + 1 < len(self.tokens)
            else None,
            "IN",
        ):
            self.take()
            negated = True
            token = self.peek()
        if _is_keyword(token, "IN"):
            self.take()
            if not _is_op(self.take(), "("):
                raise _UntranslatableError("IN without a list")
            values = [self.additive()]
            while _is_op(self.peek(), ","):
                self.take()
                values.append(self.additive())
            if not _is_op(self.take(), ")"):
                raise _UntranslatableError("unclosed IN list")
            if negated:
                return "(" + " && ".join(f"{left} != {v}" for v in values) + ")"
            return "(" + " || ".join(f"{left} == {v}" for v in values) + ")"
        return left

    def additive(self) -> str:
        left = self.multiplicative()
        while _is_op(self.peek(), "+", "-"):
            operator = self.take().value
            left = f"({left} {operator} {self.multiplicative()})"
        return left

    def multiplicative(self) -> str:
        left = self.unary()
        while _is_op(self.peek(), "*", "/", "%"):
            operator = self.take().value
            left = f"({left} {operator} {self.unary()})"
        return left

    def unary(self) -> str:
        if _is_op(self.peek(), "-"):
            self.take()
            return f"(-{self.unary()})"
        return self.atom()

    def atom(self) -> str:
        token = self.take()
        if token.kind == "number":
            return token.value
        if token.kind == "string":
            return json.dumps(token.value, ensure_ascii=False)
        if token.kind == "keyword" and token.value in ("TRUE", "FALSE", "NULL"):
            return token.value.lower()
        if token.kind in ("column", "name"):
            if token.kind == "name" and _is_op(self.peek(), "("):
                raise _UntranslatableError("function call")
            if not _DOLLAR_FIELD_RE.match(token.value):
                raise _UntranslatableError(
                    "field name the core language cannot address"
                )
            return f"${token.value}"
        if _is_op(token, "("):
            inner = self.or_expr()
            if not _is_op(self.take(), ")"):
                raise _UntranslatableError("unclosed parenthesis")
            return inner
        raise _UntranslatableError(f"unsupported token {token.value!r}")


def translate_filter_expression(expression: str) -> str | None:
    """A QGIS atlas filter -> a `<om-atlas filter>` predicate, or `None`.

    Translates comparisons (`=`, `<>`/`!=`, `<`, `>`, `<=`, `>=`), `AND`/`OR`/
    `NOT`, `IS [NOT] NULL`, `[NOT] IN (...)`, arithmetic, string/number/
    boolean literals and fields whose names the core language can address as
    `$field`. Everything else -- function calls, `LIKE`, `BETWEEN`, `CASE`,
    `||`, `$area` and the other `$` specials, `@variables` -- returns `None`.
    """
    tokens = _tokenize(expression.strip())
    if not tokens:
        return None
    try:
        return _FilterParser(tokens).parse()
    except _UntranslatableError:
        return None
