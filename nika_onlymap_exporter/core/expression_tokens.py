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

Pure Python: no PyQGIS, no Qt, unit-tested in CI.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

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
_ATLAS_ATTRIBUTE_RE = re.compile(
    r"^attribute\s*\(\s*@atlas_feature\s*,\s*'([^']+)'\s*\)$", re.IGNORECASE
)
_BARE_FIELD_RE = re.compile(r'^(?:"([^"]+)"|\'([^\']+)\')$')


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
        atlas_attr_match = _ATLAS_ATTRIBUTE_RE.match(expr)
        if atlas_attr_match:
            builder.token(f"{{{{atlas.{atlas_attr_match.group(1)}}}}}")
            return
        bare_field_match = _BARE_FIELD_RE.match(expr)
        if bare_field_match:
            field_name = bare_field_match.group(1) or bare_field_match.group(2)
            builder.token(f"{{{{atlas.{field_name}}}}}")
            return

    # Anything else -- including atlas-shaped expressions outside an atlas --
    # has no cartograph-side evaluator and must be frozen.
    builder.freeze(expr)
