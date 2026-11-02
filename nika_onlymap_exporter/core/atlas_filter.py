"""The Atlas "Matching a value" filter: one field against one typed value.

The tab offers a field and a value; this module turns them into the QGIS
expression the atlas stores, and reads such an expression back so a series set
up earlier (or in QGIS's own atlas panel) shows as the same choice. Typing
follows the field: text is quoted and escaped, a number stays bare, a yes/no
field takes TRUE or FALSE, and a real null is `IS NULL` - the text "NULL" is
an ordinary string and stays one.

Only the exact shapes written here are recognised; anything else (another
operator, a function, a second condition, a literal of the wrong type for the
field) is a custom expression and is never rewritten.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from qgis.core import (
    QgsExpression,
    QgsExpressionNodeBinaryOperator,
    QgsExpressionNodeColumnRef,
    QgsExpressionNodeFunction,
    QgsExpressionNodeLiteral,
    QgsExpressionNodeUnaryOperator,
    QgsVectorLayer,
)

from ..translation import tr
from .live_count import field_kind, is_integer_field, parse_field_number

#: What a yes/no field's value is called, as the filter keeps it.
TRUE_TEXT = "true"
FALSE_TEXT = "false"


@dataclass(frozen=True)
class Matching:
    """A recognised condition: `field` equals `value`, or is null."""

    field: str
    value: str = ""
    is_null: bool = False


#: Whole numbers up to here compare exactly as `"field" = 123`. QGIS's own
#: `=` goes through a double, so from 2**53 on neighbouring IDs are equal to
#: it; those are compared as text instead (`to_string("field") = '123'`).
_EXACT_AS_NUMBER = 2**53


def _number_text(number: int | float) -> str:
    if isinstance(number, int):
        return str(number)
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def condition_expression(
    layer: QgsVectorLayer, field: str, value: str, is_null: bool
) -> str:
    """The expression for `field` matching `value` (or null), typed by the
    field. Raises `ValueError` with words for the user when `value` does not
    fit the field."""
    kind = field_kind(layer, field)
    if kind is None:
        raise ValueError(
            tr("The layer “{layer}” has no field “{field}” any more.").format(
                layer=layer.name(), field=field
            )
        )
    column = QgsExpression.quotedColumnRef(field)
    if is_null:
        return f"{column} IS NULL"
    if kind == "number":
        number = parse_field_number(layer, field, value)
        if isinstance(number, float) and not math.isfinite(number):
            raise ValueError(
                tr(
                    "“{value}” is not a number, and “{field}” is a numeric field."
                ).format(value=value, field=field)
            )
        if isinstance(number, int) and abs(number) >= _EXACT_AS_NUMBER:
            return f"to_string({column}) = {QgsExpression.quotedString(str(number))}"
        return f"{column} = {_number_text(number)}"
    if kind == "bool":
        word = value.strip().lower()
        if word not in (TRUE_TEXT, FALSE_TEXT):
            raise ValueError(
                tr(
                    "“{value}” is not Yes or No, and “{field}” is a yes/no field."
                ).format(value=value, field=field)
            )
        return f"{column} = {word.upper()}"
    return f"{column} = {QgsExpression.quotedString(value)}"


def _is_null_literal(node: object) -> bool:
    if not isinstance(node, QgsExpressionNodeLiteral):
        return False
    value = node.value()
    if value is None:
        return True
    is_null = getattr(value, "isNull", None)
    return bool(is_null()) if callable(is_null) else False


def _number_literal(node: object) -> int | float | None:
    """The value of a bare number (a leading minus included), else `None`."""
    negative = False
    if isinstance(node, QgsExpressionNodeUnaryOperator):
        if node.op() != QgsExpressionNodeUnaryOperator.UnaryOperator.uoMinus:
            return None
        negative = True
        node = node.operand()
    if not isinstance(node, QgsExpressionNodeLiteral):
        return None
    value = node.value()
    # A yes/no literal is a bool in Python, which is also an int.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return -value if negative else value


def _read_exact_integer(layer: QgsVectorLayer, root: object) -> Matching | None:
    """`to_string("field") = '123'` on a whole-number field, the form written
    for numbers too large for `=` to tell apart; else `None`."""
    if not isinstance(root, QgsExpressionNodeBinaryOperator):
        return None
    if root.op() != QgsExpressionNodeBinaryOperator.BinaryOperator.boEQ:
        return None
    call, right = root.opLeft(), root.opRight()
    if not isinstance(call, QgsExpressionNodeFunction):
        return None
    if QgsExpression.Functions()[call.fnIndex()].name() != "to_string":
        return None
    arguments = call.args().list() if call.args() is not None else []
    if len(arguments) != 1 or not isinstance(arguments[0], QgsExpressionNodeColumnRef):
        return None
    index = layer.fields().lookupField(arguments[0].name())
    if index < 0 or not isinstance(right, QgsExpressionNodeLiteral):
        return None
    field = layer.fields().at(index).name()
    value = right.value()
    if not is_integer_field(layer, field) or not isinstance(value, str):
        return None
    # Only the exact text written here: digits as `str(int)` spells them.
    if not value.lstrip("-").isascii() or not value.lstrip("-").isdigit():
        return None
    if str(int(value)) != value or abs(int(value)) < _EXACT_AS_NUMBER:
        return None
    return Matching(field, value)


def read_condition(layer: QgsVectorLayer, expression: str) -> Matching | None:
    """`expression` as a field-and-value choice, or `None` when it is anything
    else. Never answers for an expression whose meaning the choice would
    change."""
    text = expression.strip()
    if not text:
        return None
    parsed = QgsExpression(text)
    if parsed.hasParserError():
        return None
    root = parsed.rootNode()
    if not isinstance(root, QgsExpressionNodeBinaryOperator):
        return None
    column = root.opLeft()
    if isinstance(column, QgsExpressionNodeFunction):
        return _read_exact_integer(layer, root)
    if not isinstance(column, QgsExpressionNodeColumnRef):
        return None
    index = layer.fields().lookupField(column.name())
    if index < 0:
        return None
    field = layer.fields().at(index).name()
    kind = field_kind(layer, field)
    operator = root.op()
    right = root.opRight()
    ops = QgsExpressionNodeBinaryOperator.BinaryOperator
    if operator == ops.boIs:
        return Matching(field, is_null=True) if _is_null_literal(right) else None
    if operator != ops.boEQ:
        return None
    if kind == "number":
        # A negative number's minus is a node of its own.
        number = _number_literal(right)
        if isinstance(number, float) and is_integer_field(layer, field):
            return None  # a decimal literal is the wrong type for this field
        if isinstance(number, int) and abs(number) >= _EXACT_AS_NUMBER:
            return None  # `=` cannot tell such numbers apart: not ours
        return None if number is None else Matching(field, _number_text(number))
    if not isinstance(right, QgsExpressionNodeLiteral):
        return None
    value = right.value()
    if kind == "bool":
        if isinstance(value, bool):
            return Matching(field, TRUE_TEXT if value else FALSE_TEXT)
        return None
    if isinstance(value, str):
        return Matching(field, value)
    return None
