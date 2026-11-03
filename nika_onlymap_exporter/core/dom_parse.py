"""Parsing XML into a `QDomDocument` the same way on Qt5 and Qt6."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qgis.PyQt.QtXml import QDomDocument


def parse_dom(xml: str) -> QDomDocument | None:
    """The parsed document, or `None` when `xml` is not well-formed.

    `QDomDocument.setContent` returns a tuple on both PyQt5 and PyQt6, and a
    failed parse is `(False, message, line, column)` -- a non-empty tuple, so
    truthy. A bare `if not document.setContent(xml)` therefore can never fire:
    a malformed style would carry on as an empty document. Every parse goes
    through here instead.
    """
    # Imported here so a caller that only needs QGIS lazily can import this module.
    from qgis.PyQt.QtXml import QDomDocument

    document = QDomDocument()
    result = document.setContent(xml)
    parsed = result[0] if isinstance(result, tuple) else result
    return document if parsed else None


def read_dom(xml: str) -> QDomDocument:
    """The document as far as Qt could read `xml`, never refused.

    A parse error keeps everything Qt read before it (a complete style with
    junk after it reads in full), and text Qt cannot read at all is an empty
    document. That is what the saved-style readers in `live_count` and
    `raster_smoothing` have always worked from: PyQt5 and PyQt6 both return
    a non-empty tuple from `setContent`, so their old
    `if not document.setContent(xml)` never fired, and 0.3.1 keeps their
    results as 0.3.0 gave them. A caller that must refuse malformed XML uses
    `parse_dom`.
    """
    from qgis.PyQt.QtXml import QDomDocument

    document = QDomDocument()
    document.setContent(xml)
    return document
