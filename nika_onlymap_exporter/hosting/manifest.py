"""The publish manifest, as types - the shape `publish/start` is given.

The manifest is what a release *is* now: a list of files, each named by its
path and pinned by its digest, plus the runtime the page expects and the
producer that built it. The server's kind registry decides what is publishable
from that, which is why this client no longer carries a filename allowlist.

**Built elsewhere, transmitted here.** Nothing in this module produces a
manifest; the plugin's writer does, from what it actually wrote. These types
exist so the handshake can be checked at the boundary rather than passing a
`dict[str, object]` through three calls and hoping.

Kept in `hosting/` and free of any import from `core/`, `writers/` or
`packaging/` for the same reason the rest of the package is (see
`hosting/__init__.py`): the whole handshake has to be testable without QGIS,
and a type that reaches for the exporter's internals would end that.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from typing import Literal, TypedDict

# The one manifest shape this client speaks. Sent verbatim; a server that
# introduces a second will say so in `schemaVersion`, and this plugin will
# refuse rather than guess.
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_KIND = "onlymap-map/v1"

# The roles this kind admits. A closed set rather than a free string: the
# server's kind registry enforces exactly these, so a producer that invents one
# is rejected at `publish/start`, and catching that here is cheaper than a round
# trip that says the same thing.
Role = Literal["page", "thumbnail", "data", "raster"]


class ProducerInfo(TypedDict):
    """Who built the artifact. Diagnostics, never authorization."""

    name: str
    version: str


class RuntimeInfo(TypedDict):
    """The OnlyMap build the page expects, named rather than uploaded.

    `sha256` is the digest of that runtime as this plugin knows it, so the
    server can refuse a mismatch instead of serving a page 8.3 MB of JavaScript
    it was never built against.
    """

    name: str
    version: str
    sha256: str


class ManifestFile(TypedDict):
    """One file in the release.

    `path` is relative to the artifact root and is also the path the file is
    served at. `sha256` is the content address: it is what the server
    deduplicates on, and what decides whether this file needs uploading at all.
    """

    path: str
    role: Role
    mediaType: str
    size: int
    sha256: str


class PublishManifest(TypedDict):
    """The whole release description, exactly as it goes on the wire."""

    schemaVersion: Literal[1]
    kind: str
    entry: str
    producer: ProducerInfo
    runtime: RuntimeInfo
    files: list[ManifestFile]
    externalOrigins: list[str]
    # Two fields rather than one, because they are trusted differently and end
    # up in different CSP directives. `externalOrigins` is derived from the
    # page and reaches `connect-src` and `img-src` only. `runtimeScriptSources`
    # is chosen from a whitelist the plugin ships - never from page content -
    # and is the only thing here that may name somewhere code runs from, so
    # merging the two would hand a page author `script-src` on our own origin.
    # The producer of each is `packaging/publish_manifest`; see its note above
    # `TERRAIN_MESH_WORKER` for why the entries are path-pinned.
    runtimeScriptSources: list[str]
