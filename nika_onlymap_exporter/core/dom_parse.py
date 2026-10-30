"""Parsing XML into a `QDomDocument` the same way on Qt5 and Qt6."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qgis.PyQt.QtXml import QDomDocument


def parse_dom(xml: str) -> QDomDocument | None:
    """The parsed document, or `None` when `xml` is not well-formed.

    `QDomDocument.setContent` returns a bool on Qt5 but a `ParseResult` named
    tuple on Qt6, and a failed Qt6 parse is `(False, message, line, column)` --
    a non-empty tuple, so truthy. A bare `if not document.setContent(xml)`
    therefore can never fire on Qt6: a malformed style would carry on as an
    empty document. Every parse goes through here instead.
    """
    # Imported here so a caller that only needs QGIS lazily can import this module.
    from qgis.PyQt.QtXml import QDomDocument

    document = QDomDocument()
    result = document.setContent(xml)
    parsed = result[0] if isinstance(result, tuple) else result
    return document if parsed else None
