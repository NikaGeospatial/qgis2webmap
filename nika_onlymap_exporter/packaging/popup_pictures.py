r"""Popup pictures, carried the way the export's tier carries everything else.

The reader has already chosen and prepared every picture a popup shows
(`core.popup_media`), and its markup names each one by a file name. Here that
name becomes something the recipient's browser can open:

* **Folder and Share ZIP** write each picture beside `index.html` under that
  name, so the markup's relative address finds it - served, or opened from an
  extracted folder by double-click, since a browser shows a sibling picture
  from `file://` (it is only `fetch` that it refuses).
* **A single file** has nowhere to put one, so the page carries each picture
  once, as base64 in a block of its own (`build_picture_embed`), and a small
  script turns it into a `blob:` address and puts that address in place of
  the name in the layer data, before the runtime reads it. Every feature
  showing the picture refers to the same address: a photo a thousand
  features share is in the page once, not a thousand times, and the page is
  as large as the pictures plus the data, which is what the size warnings
  count (`dependency_scanner.measure_data_bytes`). A cartograph's live frames
  carry theirs the same way.

Only a name in a picture's own markup is replaced, and only by a picture the
page carries: the script looks for the markup at the start of one of the
properties that hold it (`picture_fields`), as compact JSON writes it -
`"photo_html":"\u003cimg class=\"om-popup-picture\" src=\"...`. Inside a
JSON string every quote is escaped, so that unescaped `"photo_html":"` is
always the property's own name, and no value, whatever it spells, matches.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
from html import escape
from pathlib import Path

from ..core.export_ir import ExportLayer, ExportProject, OutputMode, PopupPicture
from ..core.popup_media import PICTURE_CLASS, picture_sources
from .asset_embedder import PICTURE_RESOLVER

# The tiers with a folder to put a picture in.
_SIBLING_MODES = frozenset({OutputMode.FOLDER, OutputMode.SHARE_ZIP})

# The pictures' blocks' type. Unknown to the browser, so it neither runs nor
# renders them; the script below finds them by it and removes them once read.
PICTURE_SCRIPT_TYPE = "application/x-om-picture"

# A plain script, not a module, so it runs as the parser reaches it: after the
# layer data above it and before the runtime module, which reads that data.
_RESOLVER_SCRIPT = r"""      <script>
(() => {
  const addresses = new Map();
  for (const el of document.querySelectorAll('script[type="%(type)s"]')) {
    const text = atob(el.textContent.trim());
    const bytes = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
    const picture = new Blob([bytes], { type: el.dataset.type });
    addresses.set(el.dataset.src, URL.createObjectURL(picture));
    el.remove();
  }
  // A picture property's markup as compact JSON writes it (see the module).
  const named = new RegExp(
    String.raw`("(?:%(fields)s)":"\\u003cimg class=\\"%(class)s\\" src=\\")` +
      String.raw`(popup-picture-[0-9a-f]{16}\.[a-z]+)(\\")`,
    "g"
  );
  const resolve = (text) => text.replace(named, (whole, before, name, after) =>
    addresses.has(name) ? before + addresses.get(name) + after : whole);
  // For an inflater that runs after this: a page whose data arrives gzipped
  // puts the addresses in once the data is JSON.
  window.%(resolver)s = resolve;
  for (const block of document.querySelectorAll('script[type="application/json"]')) {
    if (block.textContent.includes("popup-picture-")) {
      block.textContent = resolve(block.textContent);
    }
  }
})();
      </script>"""


def picture_fields(layers: Iterable[ExportLayer]) -> tuple[str, ...]:
    """The properties `layers` hold a popup picture's markup in."""
    return tuple(
        sorted(
            {
                field.name
                for layer in layers
                if layer.popup_pictures
                for field in layer.popup.fields
                if field.markup
                and field.attachment is not None
                and field.attachment.picture
            }
        )
    )


def build_picture_embed(pictures: Iterable[PopupPicture], fields: Iterable[str]) -> str:
    """The page markup that carries `pictures` and puts their addresses in
    the `fields` that show them (`picture_fields`); empty when there are
    none. Goes after every data block it must resolve and before the
    runtime."""
    blocks = [
        f'      <script type="{PICTURE_SCRIPT_TYPE}" data-src="{escape(p.name)}" '
        f'data-type="{escape(p.media_type)}">'
        f"{base64.b64encode(p.data).decode('ascii')}</script>"
        for p in pictures
    ]
    if not blocks:
        return ""
    script = _RESOLVER_SCRIPT % {
        "type": PICTURE_SCRIPT_TYPE,
        # ASCII word characters (`popup_media._markup_property`): nothing to
        # escape in a pattern.
        "fields": "|".join(fields),
        "class": PICTURE_CLASS,
        "resolver": PICTURE_RESOLVER,
    }
    return "\n".join([*blocks, script])


def place_popup_pictures(
    project: ExportProject, destination: Path, mode: OutputMode
) -> tuple[tuple[PopupPicture, ...], tuple[Path, ...]]:
    """Carry `project`'s popup pictures for `mode`: the pictures the page must
    carry inside (`build_picture_embed`), and the files written beside it.

    Beside the page for a tier with a folder, inside it otherwise.
    """
    pictures = picture_sources(project.layers)
    if mode not in _SIBLING_MODES:
        return tuple(pictures.values()), ()
    written = []
    for name, picture in pictures.items():
        path = destination / name
        path.write_bytes(picture.data)
        written.append(path)
    return (), tuple(written)
