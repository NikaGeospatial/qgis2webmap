"""Links and pictures in popups.

A popup is one template the runtime fills from whichever feature was clicked,
and its `{{name}}` substitution is plain text (escaped) - so it cannot make one
feature's value a link and the next one's plain text. For a field that needs
that, the exporter writes the markup itself, per feature, into a property of
its own, and the template inserts that property as it is (`{{{name}}}`, the
runtime's unescaped form). Everything inserted that way is built here, from
escaped text and nothing else, so no attribute value can put markup into the
page: a value only ever becomes the text of an element, the address of a link
whose scheme is http, https or mailto, or the address of a picture.

What becomes what follows QGIS's own configuration:

* A value that is a web or email address (the whole value) is a link, opening
  in a new tab. A field QGIS shows with its Attachment widget set to "display
  resource path as link" without "display full path" shows the file name, as
  the widget does.
* An Attachment field whose document viewer is Image shows pictures. A file on
  this computer, resolved the way QGIS resolves it (`PopupAttachment`), is
  carried with the map, made smaller when it is large - when it is in the
  project's folder or the widget's default path, even after following `..`
  and links; anywhere else it is not read, and the popup shows its path. A
  web address is loaded live, so it needs an internet connection.

Only the values of features the map carries, in fields its popups show, are
looked at, so no other file is ever read or carried.

The picture loader imports Qt; everything else here is pure.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import functools
import hashlib
import html
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence, Sized
from pathlib import Path
from typing import Any

from .export_ir import ExportLayer, PopupFieldSpec, PopupPicture
from .fidelity_report import FidelityReportBuilder
from .report_facts import format_bytes

# A picture wider or taller than this is scaled down to it. A popup is at most
# 22rem (352 CSS pixels) wide, so this is sharp on a high-density screen with
# room to spare, and a typical phone photo drops from megabytes to a few
# hundred kilobytes.
MAX_PICTURE_EDGE = 1280
# One picture may not be larger than this once it has been made smaller.
MAX_PICTURE_BYTES = 1024 * 1024
# The pictures of one map, all layers together, each counted once.
MAX_EXPORT_PICTURE_BYTES = 20 * 1024 * 1024

# Formats every browser shows, by QImageReader's name for them: a file in one of
# these that is small enough is carried byte for byte.
_WEB_FORMATS = {
    "jpeg": ("image/jpeg", "jpg"),
    "jpg": ("image/jpeg", "jpg"),
    "png": ("image/png", "png"),
    "gif": ("image/gif", "gif"),
    "webp": ("image/webp", "webp"),
}
_JPEG_QUALITY = 85

# The whole value, without spaces or control characters. Only these schemes:
# `javascript:` would run code, `data:` and `file:` would open something the
# reader cannot see the source of, and nothing else is a link a recipient's
# browser can follow.
_LINK = re.compile(
    r"(?:https?://[^\s\x00-\x1f\x7f/?#][^\s\x00-\x1f\x7f]*"
    r"|mailto:[^\s\x00-\x1f\x7f]+)",
    re.I,
)
_WEB = re.compile(r"https?://", re.I)
# A scheme, as opposed to a Windows drive letter (`C:\photos`).
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]+:", re.I)

PICTURE_CLASS = "om-popup-picture"


class PictureProblem(enum.Enum):
    """Why a local picture is not carried; the popup shows its path instead."""

    MISSING = "missing"
    UNREADABLE = "unreadable"
    TOO_LARGE = "too_large"
    OUTSIDE = "outside"
    OVER_BUDGET = "over_budget"


@dataclasses.dataclass(frozen=True)
class LoadedPicture:
    """A picture ready to carry. `reduced` when it is not the file's own bytes;
    `version` the file's path, time and size when it was read, for
    `_CARRIED`."""

    data: bytes
    media_type: str
    extension: str
    reduced: bool = False
    version: tuple[str, int, int] | None = None


PictureLoader = Callable[[Path], "LoadedPicture | PictureProblem"]


def _text(value: object) -> str:
    """A value as the runtime would print it: JavaScript's `String(value)`."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def is_link(value: object) -> bool:
    """Whether `value` is, as a whole, an address the popup may link to."""
    return isinstance(value, str) and _LINK.fullmatch(value.strip()) is not None


def link_markup(address: str, label: str) -> str:
    # `noopener` so the opened page cannot reach back into the map, and
    # `noreferrer` so it is not told the map's address.
    return (
        f'<a href="{html.escape(address)}" target="_blank" '
        f'rel="noopener noreferrer">{html.escape(label)}</a>'
    )


def picture_markup(src: str, alt: str) -> str:
    # The alternative text is the file name: what shows if the picture cannot
    # be loaded, such as a web picture opened with no connection.
    return (
        f'<img class="{PICTURE_CLASS}" src="{html.escape(src)}" '
        f'alt="{html.escape(alt)}" referrerpolicy="no-referrer">'
    )


def file_name(value: str) -> str:
    """The last part of a path or address, as the Attachment widget shows it."""
    stem = re.split(r"[?#]", value, maxsplit=1)[0].rstrip("/\\")
    return re.split(r"[/\\]", stem)[-1] or value


def local_path(value: str, field: PopupFieldSpec) -> Path | None:
    """The file `value` names on this computer, as QGIS resolves it; `None`
    for an address, or a relative path QGIS would look for in no fixed place."""
    if _SCHEME.match(value):
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    base = field.attachment.base_dir if field.attachment is not None else None
    return Path(base) / path if base else None


def is_inside(path: Path, roots: Iterable[str]) -> bool:
    """Whether `path`, followed through `..` and links to where it really
    leads, is in one of the folders `roots` (themselves followed the same
    way)."""
    real = _followed(str(path))
    for root in roots:
        folder = _followed(root)
        # ValueError: on another drive.
        with contextlib.suppress(ValueError):
            if os.path.commonpath([real, folder]) == folder:
                return True
    return False


def _followed(path: str) -> str:
    """`path` through its links and `..`; by its name alone when it holds a
    NUL, which no file name can, so the picture is reported as not found."""
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return os.path.normpath(os.path.abspath(path))


def value_markup(
    value: object,
    field: PopupFieldSpec,
    picture_src: Callable[[str], str | None],
) -> str:
    """The escaped markup a popup shows for one value of `field`.

    `picture_src` turns a local path into the name the page carries the
    picture under, or `None` when it is not carried; it is only asked about
    the values of a picture field. Empty for an empty value, so a row set to
    show only with data still hides.
    """
    text = _text(value)
    stripped = text.strip()
    if not stripped:
        return ""
    attachment = field.attachment
    if attachment is not None and attachment.picture:
        if _WEB.match(stripped):
            if is_link(stripped):
                return picture_markup(stripped, file_name(stripped))
            return html.escape(text)
        if not _SCHEME.match(stripped):
            src = picture_src(stripped)
            if src is not None:
                return picture_markup(src, file_name(stripped))
            return html.escape(text)
    if is_link(stripped):
        label = (
            file_name(stripped)
            if attachment is not None and attachment.file_name_label
            else stripped
        )
        return link_markup(stripped, label)
    return html.escape(text)


def _markup_property(name: str, taken: set[str]) -> str:
    """A property name for `name`'s markup that nothing else uses (ASCII word
    characters only, because the template substitutes nothing else)."""
    stem = re.sub(r"\W+", "_", name, flags=re.ASCII).strip("_") or "field"
    candidate = f"{stem}_html"
    while candidate in taken:
        candidate += "_"
    taken.add(candidate)
    return candidate


class _CarriedPictures:
    """The pictures maps carried, by file, each while the file keeps the time
    and size it was read at: a cartograph re-reads its frames on every save,
    and decoding the same photos each time would stall it. Only pictures a
    map carried (`keep_picture`), and no more bytes of them than one map may
    carry, the least recently used let go first - so the bytes of a picture
    that was turned down are let go as soon as it is. Any thread."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, LoadedPicture] = OrderedDict()
        self._bytes = 0

    def get(self, version: tuple[str, int, int]) -> LoadedPicture | None:
        with self._lock:
            held = self._entries.get(version[0])
            if held is None or held.version != version:
                return None
            self._entries.move_to_end(version[0])
            return held

    def put(self, picture: LoadedPicture) -> None:
        if picture.version is None or len(picture.data) > self._max_bytes:
            return
        path = picture.version[0]
        with self._lock:
            old = self._entries.pop(path, None)
            if old is not None:
                self._bytes -= len(old.data)
            self._entries[path] = picture
            self._bytes += len(picture.data)
            while self._bytes > self._max_bytes:
                _path, dropped = self._entries.popitem(last=False)
                self._bytes -= len(dropped.data)


_CARRIED = _CarriedPictures(MAX_EXPORT_PICTURE_BYTES)


def _read_picture(path: str, _size: int) -> LoadedPicture | PictureProblem:
    """`load_picture` for a file of `_size` bytes, read afresh."""
    from qgis.PyQt.QtCore import QBuffer, QIODevice, Qt
    from qgis.PyQt.QtGui import QImageReader

    reader = QImageReader(path)
    # Turned the way the camera's orientation tag says, as a browser shows
    # the original: the tag does not survive re-saving.
    reader.setAutoTransform(True)
    kind = bytes(reader.format().data()).decode("ascii", "replace").lower()
    if not kind:
        return PictureProblem.UNREADABLE
    size = reader.size()
    edge = max(size.width(), size.height()) if size.isValid() else 0
    web = _WEB_FORMATS.get(kind)
    if web is not None and edge <= MAX_PICTURE_EDGE and _size <= MAX_PICTURE_BYTES:
        try:
            data = Path(path).read_bytes()
        except OSError:
            return PictureProblem.MISSING
        return LoadedPicture(data, web[0], web[1])
    if edge > MAX_PICTURE_EDGE:
        reader.setScaledSize(
            size.scaled(
                MAX_PICTURE_EDGE, MAX_PICTURE_EDGE, Qt.AspectRatioMode.KeepAspectRatio
            )
        )
    image = reader.read()
    if image.isNull():
        return PictureProblem.UNREADABLE
    alpha = image.hasAlphaChannel()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(
        buffer, "PNG" if alpha else "JPEG", -1 if alpha else _JPEG_QUALITY
    ):
        return PictureProblem.UNREADABLE
    data = bytes(buffer.data().data())
    if len(data) > MAX_PICTURE_BYTES:
        return PictureProblem.TOO_LARGE
    if alpha:
        return LoadedPicture(data, "image/png", "png", reduced=True)
    return LoadedPicture(data, "image/jpeg", "jpg", reduced=True)


def load_picture(path: Path) -> LoadedPicture | PictureProblem:
    """Read the picture at `path`, made smaller if it is large. **Any thread**
    (QImage, never QPixmap)."""
    try:
        stat = path.stat()
    except (OSError, ValueError):
        # ValueError: a value with a NUL in it, which no file can be named.
        return PictureProblem.MISSING
    if not path.is_file():
        return PictureProblem.MISSING
    version = (str(path), stat.st_mtime_ns, stat.st_size)
    kept = _CARRIED.get(version)
    if kept is not None:
        return kept
    result = _read_picture(version[0], stat.st_size)
    if isinstance(result, PictureProblem):
        return result
    return dataclasses.replace(result, version=version)


def keep_picture(picture: LoadedPicture) -> None:
    """Remember `picture`, which a map carries, for the next read of its file
    (`load_picture`); a picture not read from a file is not remembered."""
    _CARRIED.put(picture)


@dataclasses.dataclass
class _Budget:
    """The map's pictures so far, by name, across every layer, and their
    bytes all together."""

    carried: set[str] = dataclasses.field(default_factory=set)
    used: int = 0


@dataclasses.dataclass
class _Tally:
    """What one layer's popups ended up showing, for its Fidelity rows."""

    link_fields: set[str] = dataclasses.field(default_factory=set)
    remote: set[str] = dataclasses.field(default_factory=set)
    carried: dict[str, int] = dataclasses.field(default_factory=dict)
    reduced: set[str] = dataclasses.field(default_factory=set)
    problems: dict[PictureProblem, list[str]] = dataclasses.field(default_factory=dict)
    unplaced: list[str] = dataclasses.field(default_factory=list)
    local_files: set[str] = dataclasses.field(default_factory=set)
    picture_fields: set[str] = dataclasses.field(default_factory=set)


def _media_fields(
    fields: Iterable[PopupFieldSpec], features: Sequence[Mapping[str, Any]]
) -> list[PopupFieldSpec]:
    """The shown fields whose values need markup: Attachment fields, and any
    field holding at least one address. Every other field keeps the plain
    `{{name}}` it always had."""
    chosen = []
    for field in fields:
        if field.markup:
            continue
        if field.attachment is not None or any(
            is_link((feature.get("properties") or {}).get(field.name))
            for feature in features
        ):
            chosen.append(field)
    return chosen


def attach_popup_media(
    layers: Sequence[ExportLayer],
    report: FidelityReportBuilder,
    load: PictureLoader = load_picture,
) -> list[ExportLayer]:
    """`layers` with their popups' links and pictures in place.

    In draw order, so when the map's pictures are over
    `MAX_EXPORT_PICTURE_BYTES`, the ones left out are the last layer's.
    """
    budget = _Budget()
    return [_with_media(layer, report, load, budget) for layer in layers]


def _with_media(
    layer: ExportLayer,
    report: FidelityReportBuilder,
    load: PictureLoader,
    budget: _Budget,
) -> ExportLayer:
    popup = layer.popup
    if not popup.enabled or layer.geojson is None:
        return layer
    features = list(layer.geojson.get("features") or ())
    fields = _media_fields(popup.visible_fields, features)
    if not fields:
        return layer

    taken = {f.name for f in popup.fields}
    for feature in features:
        taken.update((feature.get("properties") or {}).keys())
    targets = {field.name: _markup_property(field.name, taken) for field in fields}

    tally = _Tally()
    # What became of each file: the name it is carried under, or why it is
    # not. Never its bytes: those are kept only in `pictures`, for the ones
    # carried, so a layer of thousands of photos holds no more than the map
    # may carry.
    outcomes: dict[Path, str | PictureProblem] = {}
    pictures: dict[str, PopupPicture] = {}

    def carry(path: Path, roots: Sequence[str]) -> str | PictureProblem:
        if not is_inside(path, roots):
            return PictureProblem.OUTSIDE
        result = load(path)
        if isinstance(result, PictureProblem):
            return result
        name = (
            "popup-picture-"
            f"{hashlib.sha256(result.data).hexdigest()[:16]}.{result.extension}"
        )
        if name not in budget.carried:
            if budget.used + len(result.data) > MAX_EXPORT_PICTURE_BYTES:
                return PictureProblem.OVER_BUDGET
            budget.carried.add(name)
            budget.used += len(result.data)
            keep_picture(result)
        pictures[name] = PopupPicture(name, result.data, result.media_type)
        tally.carried[name] = len(result.data)
        if result.reduced:
            tally.reduced.add(name)
        return name

    def picture_src(value: str, field: PopupFieldSpec) -> str | None:
        label = field.display_name
        tally.picture_fields.add(label)
        path = local_path(value, field)
        if path is None:
            tally.unplaced.append(value)
            return None
        outcome = outcomes.get(path)
        if outcome is None:
            roots = field.attachment.roots if field.attachment is not None else ()
            outcome = outcomes[path] = carry(path, roots)
        if isinstance(outcome, PictureProblem):
            tally.problems.setdefault(outcome, []).append(value)
            return None
        return outcome

    rewritten = []
    for feature in features:
        properties = feature.get("properties") or {}
        added = {}
        for field in fields:
            value = properties.get(field.name)
            added[targets[field.name]] = value_markup(
                value, field, functools.partial(picture_src, field=field)
            )
            _note(tally, field, value)
        rewritten.append({**feature, "properties": {**properties, **added}})

    _report(layer, tally, report)
    return dataclasses.replace(
        layer,
        geojson={**layer.geojson, "features": rewritten},
        popup=dataclasses.replace(
            popup,
            fields=tuple(
                dataclasses.replace(
                    f, name=targets[f.name], alias=f.display_name, markup=True
                )
                if f.name in targets
                else f
                for f in popup.fields
            ),
        ),
        popup_pictures=tuple(pictures.values()),
    )


def _note(tally: _Tally, field: PopupFieldSpec, value: object) -> None:
    """Count what a value became that `picture_src` does not see."""
    text = _text(value).strip()
    if not text:
        return
    attachment = field.attachment
    picture = attachment is not None and attachment.picture
    if picture and _WEB.match(text) and is_link(text):
        tally.remote.add(text)
    elif is_link(text):
        tally.link_fields.add(field.display_name)
    elif attachment is not None and not picture and not _SCHEME.match(text):
        tally.local_files.add(field.display_name)


def _names(labels: Iterable[str]) -> str:
    return ", ".join(f"'{label}'" for label in sorted(labels))


def _count(items: Sized, one: str, many: str) -> str:
    """ "1 picture is", "3 pictures are": `one` or `many` after the count."""
    return f"{len(items)} {one if len(items) == 1 else many}"


def _report(layer: ExportLayer, tally: _Tally, report: FidelityReportBuilder) -> None:
    subject = f"Popups of '{layer.name}'"
    layer_id = layer.layer_id
    if tally.link_fields:
        report.preserved(
            subject,
            f"Web and email addresses in {_names(tally.link_fields)} open as "
            "links, in a new tab. Following one needs an internet connection.",
            layer_id,
        )
    if tally.carried:
        report.preserved(
            subject,
            f"{_count(tally.carried, 'picture is', 'pictures are')} carried in "
            f"the map from {_names(tally.picture_fields)} "
            f"({format_bytes(sum(tally.carried.values()))}), so they show "
            "without an internet connection.",
            layer_id,
        )
    if tally.reduced:
        report.approximated(
            subject,
            f"{_count(tally.reduced, 'picture was', 'pictures were')} made "
            "smaller to keep the map small enough to share: scaled down to "
            f"{MAX_PICTURE_EDGE} pixels across where larger, and saved as JPEG "
            "(PNG where transparent). The files themselves are unchanged.",
            layer_id,
        )
    if tally.remote:
        report.approximated(
            subject,
            f"{_count(tally.remote, 'picture is a', 'pictures are')} web "
            f"address{'' if len(tally.remote) == 1 else 'es'}, not carried in "
            "the map but loaded each time a popup opens, so they show only "
            "with an internet connection.",
            layer_id,
        )
    too_large = f"larger than {format_bytes(MAX_PICTURE_BYTES)} even when made smaller"
    left_out = {
        PictureProblem.MISSING: ("picture could not", "pictures could not"),
        PictureProblem.UNREADABLE: ("file is not", "files are not"),
        PictureProblem.TOO_LARGE: ("picture is", "pictures are"),
    }
    reasons = {
        PictureProblem.MISSING: "be found",
        PictureProblem.UNREADABLE: "a picture the map can show",
        PictureProblem.TOO_LARGE: too_large,
    }
    for problem, (one, many) in left_out.items():
        values = tally.problems.get(problem)
        if values:
            report.unsupported(
                subject,
                f"{_count(set(values), one, many)} {reasons[problem]}, so the "
                f"popup shows the path instead - for example '{values[0]}'.",
                layer_id,
            )
    outside = tally.problems.get(PictureProblem.OUTSIDE)
    if outside:
        report.unsupported(
            subject,
            f"{_count(set(outside), 'picture is', 'pictures are')} outside the "
            "project's folder, so the map does not carry "
            f"{'it' if len(set(outside)) == 1 else 'them'} and the popup shows "
            f"the path instead - for example '{outside[0]}'. To carry a "
            "picture, move it into the project's folder, or under the "
            "Attachment widget's default path.",
            layer_id,
        )
    if tally.unplaced:
        report.unsupported(
            subject,
            f"{_count(set(tally.unplaced), 'picture has', 'pictures have')} a "
            "relative path, but the Attachment widget stores absolute paths "
            "(or a default path left empty), so QGIS itself looks for it in "
            "whatever folder it was started from. The popup shows the path "
            f"instead - for example '{tally.unplaced[0]}'. Store the paths "
            "relative to the project, or set the default path.",
            layer_id,
        )
    over_budget = tally.problems.get(PictureProblem.OVER_BUDGET)
    if over_budget:
        report.unsupported(
            subject,
            f"{_count(set(over_budget), 'picture was', 'pictures were')} "
            "left out because the map already carries "
            f"{format_bytes(MAX_EXPORT_PICTURE_BYTES)} of pictures, so the "
            "popup shows the path instead - for example "
            f"'{over_budget[0]}'.",
            layer_id,
        )
    if tally.local_files:
        holds = "holds" if len(tally.local_files) == 1 else "hold"
        report.approximated(
            subject,
            f"{_names(tally.local_files)} {holds} files on this computer, which "
            "a web map cannot open, so the popup shows the path. To show "
            "pictures, set the field's Attachment widget to show the document "
            "as an Image.",
            layer_id,
        )


def picture_sources(layers: Iterable[ExportLayer]) -> dict[str, PopupPicture]:
    """Every picture the layers' popups show, once each, by name."""
    return {
        picture.name: picture for layer in layers for picture in layer.popup_pictures
    }


def replace_picture_names(
    layer: ExportLayer, sources: Mapping[str, str]
) -> ExportLayer:
    """`layer` with each picture's name in its popup markup replaced by
    `sources[name]` - a `data:` URI for a page that carries its pictures
    inside. Only the markup properties are touched, never a data value."""
    if not layer.popup_pictures or layer.geojson is None:
        return layer
    targets = {f.name for f in layer.popup.fields if f.markup}
    pattern = re.compile(r'src="(popup-picture-[0-9a-f]{16}\.[a-z]+)"')

    def swap(match: re.Match[str]) -> str:
        return f'src="{sources.get(match.group(1), match.group(1))}"'

    features = []
    for feature in layer.geojson.get("features") or ():
        properties = dict(feature.get("properties") or {})
        for name in targets:
            value = properties.get(name)
            if isinstance(value, str) and "popup-picture-" in value:
                properties[name] = pattern.sub(swap, value)
        features.append({**feature, "properties": properties})
    return dataclasses.replace(layer, geojson={**layer.geojson, "features": features})
