"""What a built hosted output *is*, described for whatever publishes it.

The directory a hosted export leaves behind is a page and some files. That is
enough for a browser and not enough for a publisher, which has to know which
file is the entry point, which are data and which are pixels, what each one
weighs, and what its bytes hash to before it is asked to store them under that
hash. This module reads the finished directory and says so.

**Read, never guess.** Every size and digest here comes from the bytes actually
on disk, at the point they are complete. That is why `index.html` is hashed last
and why nothing writes to the directory afterwards: a manifest describing a page
that then changed is worse than no manifest, because it looks authoritative.

`path` is the flat local name as written. It is deliberately not the served key:
the server stores each asset at `assets/{sha256}.{ext}`, which it can compute
from this file, and the page already references the data that way. Carrying both
here is what lets a publisher match one to the other without opening the page.
(The page spells that key root-absolute, `/assets/...`, so the reference does
not change with a trailing slash in the visitor's link - see `hosted_assets`.)

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from ..hosting.manifest import (
    MANIFEST_KIND,
    MANIFEST_SCHEMA_VERSION,
    ManifestFile,
    ProducerInfo,
    PublishManifest,
    Role,
    RuntimeInfo,
)
from ..hosting.thumbnail import THUMBNAIL_FILENAME
from .cdn_runtime import pinned_runtime
from .hosted_assets import FLAT_NAME_PATTERN

# Both are re-exported from the wire contract rather than restated, so the
# emitter and the client cannot drift apart on the two values the server keys
# its kind registry off.
SCHEMA_VERSION = MANIFEST_SCHEMA_VERSION

# The kind, and the reason there is one. A publisher storing several kinds of
# artifact needs to know what it is holding before it can decide what to do
# with it, and "there is an index.html" is not an answer - a tileset and a map
# both have one. Versioned separately from the schema: the file list's shape can
# stay put while what a map is made of changes.
ARTIFACT_KIND = MANIFEST_KIND

ENTRY_NAME = "index.html"

PRODUCER_NAME = "qgis2webmap"
RUNTIME_NAME = "onlymap"


# Extension to role, for this kind. A closed map rather than a default: a file
# nobody expected in a hosted output is a bug in the export, and silently giving
# it a plausible role would publish it as though it belonged there.
ROLES_BY_SUFFIX: dict[str, Role] = {
    ".geojson": "data",
    ".json": "data",
    ".tif": "raster",
    ".tiff": "raster",
}

MEDIA_TYPES: dict[str, str] = {
    ".html": "text/html",
    # The registered type, not `application/json`. It is what the runtime's
    # sniffer falls back to when it looks past the URL, and it is what a store
    # has to serve back for that fallback to agree with the extension.
    ".geojson": "application/geo+json",
    ".json": "application/json",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# The runtime build the origin table below was read out of. Compared against
# `runtime-lock.json` by the unit tests, so bumping the pin without re-reading
# the runtime's basemap registry fails loudly here rather than silently in a
# recipient's browser. `scripts/verify_basemap_origins.py` does the re-reading;
# this constant records that somebody ran it.
BASEMAP_ORIGINS_VERIFIED_AGAINST = "0.7.6"

# CARTO's two vector presets, and why one basemap needs six origins.
#
# The style lives at `basemaps.cartocdn.com`. Everything the style then pulls -
# fonts, sprite, and the TileJSON describing the vector source - lives at
# `tiles.basemaps.cartocdn.com`, a different host and therefore a different
# origin. That TileJSON in turn hands MapLibre four tile URLs, on `tiles-a`
# through `tiles-d`, which it round-robins over; declaring three of the four
# would block a quarter of the tiles, which is worse than blocking all of them
# because it looks like a network fault rather than a policy one.
#
# Read from the live styles, not guessed. See the module note on the table.
CARTO_VECTOR_ORIGINS = (
    "https://basemaps.cartocdn.com",
    "https://tiles.basemaps.cartocdn.com",
    "https://tiles-a.basemaps.cartocdn.com",
    "https://tiles-b.basemaps.cartocdn.com",
    "https://tiles-c.basemaps.cartocdn.com",
    "https://tiles-d.basemaps.cartocdn.com",
)

# Preset name to the origins its tiles, fonts and sprites actually come from.
#
# ORIGINS - scheme and host - because that is what a Content-Security-Policy is
# written in. `manifest_builder.BASEMAP_HOSTS` holds registrable domains for the
# fidelity report's prose, which is a different value for a different reader:
# "openfreemap.org" tells a person who they are depending on and tells a browser
# nothing, since the host that serves the tiles is `tiles.openfreemap.org`.
# The two must not be substituted for each other.
#
# A closed table with no default. Anything not in it raises, because the failure
# mode of guessing is a published map whose CSP blocks its own basemap, and a
# refused publish is recoverable where a blank map shipped to a client is not.
BASEMAP_TILE_ORIGINS: dict[str, tuple[str, ...]] = {
    # The default. No basemap, no request, nothing to declare.
    "none": (),
    # An inline raster style registered in the runtime itself: the tile template
    # is in the bundle, so this one origin is knowable without fetching anything.
    "osm": ("https://tile.openstreetmap.org",),
    # OpenFreeMap serves the style, its fonts, its sprites and its planet tiles
    # all from the one host, so the vector presets cost exactly one origin.
    "liberty": ("https://tiles.openfreemap.org",),
    "bright": ("https://tiles.openfreemap.org",),
    "positron": ("https://tiles.openfreemap.org",),
    "dark-matter": CARTO_VECTOR_ORIGINS,
    "voyager": CARTO_VECTOR_ORIGINS,
}

# The runtime build the terrain table below was read out of. Recorded apart from
# the basemap constant even though one script re-derives both: they are two
# different registries in the runtime, and a bump that moves one need not move
# the other. Keeping the two versions separate makes "when was this last
# checked" a fact about each table rather than an inference from the other's.
TERRAIN_ORIGINS_VERIFIED_AGAINST = "0.7.6"

# Relief preset to the origins its elevation tiles come from.
#
# The same shape and the same reason as `BASEMAP_TILE_ORIGINS`, for a registry
# that is not the same one. `registerTerrain` resolves `terrain="terrarium"` to
# a DEM tile template inside the runtime, and until this table existed nothing
# put that host into `externalOrigins` - so a relief map published with a
# Content-Security-Policy that blocked its own elevation data. That failure is
# quieter than a blocked basemap: the surface comes back flat and the layers
# draw at sea level, which looks like a map that was never given terrain rather
# than a map whose terrain was refused.
#
# Elevation only. Imagery draped over the surface arrives as
# `terrain-texture="https://..."`, a URL the page spells out in full, and it is
# already read off by `_FETCHED_URL_PATTERNS`. Recording it here as well would
# be two spellings of one fact, and two spellings can disagree.
#
# A closed table with no default, for the reason the basemap table is one.
TERRAIN_TILE_ORIGINS: dict[str, tuple[str, ...]] = {
    # The default. Flat ground, no DEM, nothing to declare.
    "none": (),
    # AWS Open Data's keyless terrarium tiles. The template is a literal in the
    # runtime's terrain registry, so unlike a vector basemap there is no style
    # or TileJSON hop between the preset name and the host.
    #
    # `maptiler-terrain` and `mapterhorn` are registered by the pinned runtime
    # too and are absent deliberately: the exporter cannot emit either (see
    # `manifest_builder.TERRAIN_PRESETS`), and a table listing presets nothing
    # writes is a table nothing keeps honest.
    "terrarium": ("https://s3.amazonaws.com",),
}

# Where the page states which preset it chose. `<om-map basemap="positron">`,
# and deliberately not `basemap-key`, `terrain-texture` or anything else ending
# in the word - hence the guard on the character before it.
_BASEMAP_ATTR_PATTERN = re.compile(r'(?<![-\w])basemap="([^"]*)"', re.IGNORECASE)

# The same guard, and here it is doing more work than it is for the basemap:
# `terrain-texture`, `terrain-decoder`, `terrain-max-zoom` and
# `terrain-exaggeration` are all real attributes on the same element, and the
# first of them is already matched by `_FETCHED_URL_PATTERNS`. Requiring the
# `=` immediately after the word is what keeps the two from reading each other.
_TERRAIN_ATTR_PATTERN = re.compile(r'(?<![-\w])terrain="([^"]*)"', re.IGNORECASE)

# Twenty is the cap the publish API takes. A page contacting more third parties
# than that is not a map with a basemap, it is something a person should look
# at before it goes out.
#
# Over the cap this raises, and that is a deliberate reversal: the list used to
# be truncated by sort order, which published a plausible-looking set of origins
# with a basemap missing from the end of it and said nothing anywhere. A refused
# publish is a problem somebody can see; a silently trimmed CSP is a blank map
# discovered by whoever the link was sent to. A CARTO basemap plus terrain is
# already nine origins, so the old cap of ten was one preset away from biting.
MAX_EXTERNAL_ORIGINS = 20

# The two places an emitted page names a URL it will actually fetch: the pinned
# runtime, and the raster basemap draped over relief. Everything else in the
# template is an `href` a reader may click, which is not the same thing - a
# manifest that listed those would claim the map phones home to pages it only
# links to.
_FETCHED_URL_PATTERNS = (
    re.compile(r'<script\b[^>]*\bsrc="(https://[^"]+)"', re.IGNORECASE),
    re.compile(r'\bterrain-texture="(https://[^"]+)"', re.IGNORECASE),
)


# --------------------------------------------------------------------------
# Script sources: a platform whitelist, and deliberately not a manifest input.
#
# `externalOrigins` reaches `connect-src` and `img-src` and nothing else. That
# restriction is the security boundary, not an oversight. Everything in that
# list is derived from a customer's page, so an origin able to reach
# `script-src` would let whoever authored that page authorise arbitrary code
# execution on our own serving origin - against every other map served from it,
# not just theirs. Nothing a customer can influence may name a script source,
# which is why the answer below is a second field rather than a wider first one.
#
# The runtime nonetheless loads code it did not ship with. deck.gl's
# TerrainLayer decodes each DEM tile into a mesh inside a Web Worker, and
# loaders.gl fetches that worker from a CDN at the moment the first tile lands
# rather than bundling it. Blocking it does not degrade politely: the tiles
# arrive, the mesh never does, and the surface stays flat.
#
# So the whitelist is OURS. It sits here beside the kind's other constants, it
# goes out as `runtimeScriptSources` rather than folded into `externalOrigins`,
# and it is keyed by the runtime version it was read out of the same way the
# origin tables are. A page selects from it - by naming a terrain preset - and
# can never add to it. Growing it takes a reviewed diff to this file, which is
# the entire point of it living in plugin code.
#
# **Path-pinned, never origin-pinned.** `https://unpkg.com/` as a source
# expression authorises every package anybody has ever published to npm to run
# on our origin, which gives away almost everything the whitelist was for. A
# CSP source expression matches a path ending in `/` as a prefix and any other
# path exactly, so each entry names the exact package, version and file the
# runtime asks for and nothing else on the host.
#
# Read out of the pinned bundle, not from loaders.gl's documentation: the
# version in the URL is the loader object's own `version` field, which is the
# loaders.gl release deck.gl was built against and is not the version of
# anything this repo pins directly.
#
# **Which directives this needs, because it is not the obvious two.** The
# runtime never calls `new Worker("https://unpkg.com/...")`. loaders.gl wraps
# the URL in a one-line script - `importScripts('<url>')` - makes a Blob of it
# and constructs the worker from the resulting `blob:` URL. So a server building
# the policy from this field needs BOTH of:
#
#   worker-src   blob:   - for constructing the worker at all. Not the URL
#                          below: the thing `new Worker` is handed is the blob.
#                          `worker-src` falls back to `child-src`, then
#                          `script-src`, then `default-src`, so a policy with a
#                          restrictive `default-src` and no `worker-src` blocks
#                          this without ever mentioning workers.
#   script-src   <url>   - for the `importScripts` the blob then performs. A
#                          worker created from a `blob:` URL inherits its
#                          creator's policy, so this is checked against the
#                          page's own `script-src`, which is what makes the path
#                          pin below do the work it is here to do.
#
# The same `worker-src blob:` covers the runtime's own inline workers, which are
# bundled source rather than a fetch and so need no entry here.
#
# One consequence worth stating plainly: these bytes are unverifiable in a way
# the runtime itself is not. `cdn_runtime` pins the runtime with subresource
# integrity, and there is no equivalent for a script reached through
# `importScripts` inside a worker - no `integrity` attribute exists to carry.
# The version pin is the whole of the guarantee, which is a reason to keep this
# list as short as it is rather than a reason to relax it.
TERRAIN_MESH_WORKER = (
    "https://unpkg.com/@loaders.gl/terrain@4.4.3/dist/terrain-worker.js"
)

# The runtime build the whitelist was read out of. Its own constant for the
# reason the two origin tables have their own: the worker URL carries a
# loaders.gl version that moves when deck.gl's dependency moves, entirely
# independently of anything in the basemap or terrain registries.
RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST = "0.7.6"

# Which whitelist entries a relief preset actually calls for.
#
# Keyed by preset rather than emitted unconditionally, so a map with no terrain
# publishes with no script source at all. Every entry in a policy is something
# that has to be true; a flat map that authorised a mesh decoder it will never
# construct would be widening its own policy for nothing.
#
# Closed, like the origin tables, and for the same reason.
TERRAIN_SCRIPT_SOURCES: dict[str, tuple[str, ...]] = {
    "none": (),
    # One worker, and it is the layer's rather than the preset's: every relief
    # preset goes through the same TerrainLayer, so a second keyless DEM added
    # to `TERRAIN_TILE_ORIGINS` later would name this same entry here.
    "terrarium": (TERRAIN_MESH_WORKER,),
}


class PublishManifestError(RuntimeError):
    """The directory is not a hosted output this manifest can describe."""


def role_for(name: str) -> Role:
    """Which of the four roles a file in a hosted output plays."""
    if name == ENTRY_NAME:
        return "page"
    if name == THUMBNAIL_FILENAME:
        return "thumbnail"
    role = ROLES_BY_SUFFIX.get(Path(name).suffix.lower())
    if role is None:
        raise PublishManifestError(
            f"{name!r} has no role in a {ARTIFACT_KIND} artifact. A hosted "
            "output holds the page, its thumbnail, layer data and rasters, and "
            "publishing anything else would ship a file nothing put there."
        )
    return role


def media_type_for(name: str) -> str:
    """The type the store must serve this file back as.

    Falls back to the generic binary type rather than raising: an unknown
    extension has already been rejected by `role_for`, so anything reaching here
    is a known role whose exact type we simply have no better name for.
    """
    return MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def describe_file(path: Path) -> ManifestFile:
    """One file's entry, hashed from the bytes on disk."""
    data = path.read_bytes()
    return ManifestFile(
        path=path.name,
        role=role_for(path.name),
        mediaType=media_type_for(path.name),
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def basemap_origins(preset: str) -> tuple[str, ...]:
    """Where a basemap preset's tiles come from, or a refusal to guess.

    The page names a preset, not a URL, and the resolution from one to the other
    happens inside the runtime's basemap registry. That is exactly why this
    table exists: the server turns `externalOrigins` into the page's
    Content-Security-Policy, and an origin missing from it is an origin the
    browser blocks - so a preset we cannot resolve has to stop the publish
    rather than ship a map that draws its layers over nothing.

    A bring-your-own style URL is a legal `basemap` value for the runtime and is
    not accepted here. This exporter never emits one - `build_manifest` falls
    back to "none" for anything outside `BASEMAP_PRESETS` - and a style URL
    alone does not say which hosts the style will then pull tiles from, which is
    the whole problem this function exists to solve.
    """
    origins = BASEMAP_TILE_ORIGINS.get(preset)
    if origins is None:
        raise PublishManifestError(
            f"The page asks for basemap {preset!r}, and there is no record of "
            "which origins that resolves to. Publishing anyway would give the "
            "map a Content-Security-Policy that blocks its own basemap tiles, "
            "so it would render blank. Add the preset to BASEMAP_TILE_ORIGINS "
            "after checking it with scripts/verify_basemap_origins.py, or pass "
            "external_origins= if you already know the answer."
        )
    return origins


def terrain_origins(preset: str) -> tuple[str, ...]:
    """Where a relief preset's elevation tiles come from, or a refusal to guess.

    The basemap argument, one registry over. `terrain="terrarium"` becomes a DEM
    tile URL inside the runtime, so the page never spells the host out and the
    server has nothing to put in `connect-src` unless this table supplies it.

    Only the elevation data. The draped imagery is a URL the page writes down,
    and `_FETCHED_URL_PATTERNS` reads it from there.
    """
    origins = TERRAIN_TILE_ORIGINS.get(preset)
    if origins is None:
        raise PublishManifestError(
            f"The page asks for terrain {preset!r}, and there is no record of "
            "which origins that resolves to. Publishing anyway would give the "
            "map a Content-Security-Policy that blocks its own DEM tiles, so "
            "it would render without its elevation data - a flat surface with "
            "every layer at sea level, and nothing saying why. Add the preset "
            "to TERRAIN_TILE_ORIGINS after checking it with "
            "scripts/verify_basemap_origins.py, or pass external_origins= if "
            "you already know the answer."
        )
    return origins


def terrain_script_sources(preset: str) -> tuple[str, ...]:
    """Which whitelisted script sources a relief preset needs, or a refusal.

    Separate from `terrain_origins` because the two answers go to different CSP
    directives and only one of them is a customer-derived value at all - see the
    whitelist's own note. Raises rather than returning nothing for an unknown
    preset, so a preset added to one table and forgotten in the other cannot
    publish a map whose mesh decoder is blocked.
    """
    sources = TERRAIN_SCRIPT_SOURCES.get(preset)
    if sources is None:
        raise PublishManifestError(
            f"The page asks for terrain {preset!r}, and there is no record of "
            "which script sources the runtime loads its mesh decoder from for "
            "it. Publishing anyway would give the map a Content-Security-Policy "
            "that blocks that worker, so the DEM tiles would arrive and the "
            "relief surface would never be built from them. Add the preset to "
            "TERRAIN_SCRIPT_SOURCES after checking it with "
            "scripts/verify_basemap_origins.py."
        )
    return sources


def declared_basemap(page_html: str) -> str | None:
    """The preset the page names, or `None` if it names none at all."""
    match = _BASEMAP_ATTR_PATTERN.search(page_html)
    return match.group(1) if match is not None else None


def declared_terrain(page_html: str) -> str | None:
    """The relief preset the page names, or `None` for a flat map.

    `None` is by far the common answer: `build_manifest` writes the attribute
    only when relief is switched on, so most pages never mention terrain at all.
    """
    match = _TERRAIN_ATTR_PATTERN.search(page_html)
    return match.group(1) if match is not None else None


def runtime_script_sources(page_html: str) -> tuple[str, ...]:
    """The whitelisted script sources this page's runtime will actually fetch.

    The page selects; it does not supply. Every value that can come back is a
    literal from `TERRAIN_SCRIPT_SOURCES` above, and the only thing read off the
    page is which of them applies. That asymmetry is the whole design: see the
    whitelist's note for why a customer-derived string must never reach
    `script-src`.

    Sorted and deduplicated, like the origins, so two exports of one project
    produce byte-identical manifests.
    """
    sources: set[str] = set()
    preset = declared_terrain(page_html)
    if preset is not None:
        sources.update(terrain_script_sources(preset))
    return tuple(sorted(sources))


def derive_external_origins(page_html: str) -> tuple[str, ...]:
    """The origins the page will contact when it is opened.

    Three sources, because the page states its dependencies in two different
    ways and does it for two different registries. The URLs it spells out - the
    pinned runtime and the raster texture draped over relief, see
    `_FETCHED_URL_PATTERNS` - can simply be read off. The basemap and the relief
    preset are named rather than spelled out, so each is looked up in its own
    table, and both tables raise rather than returning nothing for a preset they
    do not know.

    Sorted and deduplicated, because this is a set written down as a list, and a
    manifest that reordered between two identical exports would defeat the
    deterministic-artifact promise the writer makes.
    """
    origins: set[str] = set()
    for pattern in _FETCHED_URL_PATTERNS:
        for url in pattern.findall(page_html):
            parts = urlsplit(url)
            if parts.scheme and parts.netloc:
                origins.add(f"{parts.scheme}://{parts.netloc}")

    # No attribute at all is not an unknown preset: a page that never mentions a
    # basemap is asking for none, which is what the runtime's own default gives
    # it. Only a value we cannot resolve is worth refusing the publish over.
    preset = declared_basemap(page_html)
    if preset is not None:
        origins.update(basemap_origins(preset))

    # Same reading for the same reason: no attribute at all means flat ground,
    # which the runtime's default already gives the page. Only a value that
    # cannot be resolved is worth refusing the publish over.
    relief = declared_terrain(page_html)
    if relief is not None:
        origins.update(terrain_origins(relief))

    return _within_cap(origins)


_TITLE_TAG_PATTERN = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def declared_title(page_html: str) -> str:
    """What the page's own `<title>` says.

    Read off the built page rather than threaded through as a parameter, the
    same way `externalOrigins` is: the template writes exactly one `<title>`
    (`templates/map.html`, `@TITLE@`), HTML-escaped, so unescaping it here
    recovers the text `resolve_title` produced - the dialog's Map name field,
    or the project title, or the file name. Empty when the tag is missing or
    empty, which the server treats as "seed from the slug instead".
    """
    match = _TITLE_TAG_PATTERN.search(page_html)
    if match is None:
        return ""
    return html.unescape(match.group(1)).strip()


def build_publish_manifest(
    directory: Path,
    external_origins: Sequence[str] | None = None,
    producer_version: str | None = None,
) -> PublishManifest:
    """Describe a built hosted output directory.

    `external_origins` overrides the derivation for a caller that knows more
    than the page does - the hosting client, say, which knows what the runtime
    resolves a basemap preset to. Passing an empty sequence means exactly that
    and is not the same as passing nothing. The same cap and the same refusal to
    trim apply to it: a caller who knows better still cannot publish a policy
    the API will silently shorten.

    `runtimeScriptSources` takes no such override, and that is not an omission.
    It is the one field that decides what may execute on the serving origin, so
    it is derived only from the platform's own whitelist - see the note above
    `TERRAIN_MESH_WORKER` - and a caller cannot widen it by asking.

    The entry is listed first and the rest follow in name order, so two exports
    of the same project produce the same manifest.
    """
    entry = directory / ENTRY_NAME
    if not entry.is_file():
        raise PublishManifestError(
            f"{directory} holds no {ENTRY_NAME}, so it is not a built hosted "
            "output. The page is written last; a directory without one is an "
            "export that did not finish."
        )

    files = [describe_file(entry)]
    for path in sorted(p for p in directory.iterdir() if p.name != ENTRY_NAME):
        if not path.is_file():
            raise PublishManifestError(
                f"{path.name} is not a file. Hosted output is flat: a directory "
                "in it would be published under a name the page cannot reach."
            )
        _check_flat_name(path.name)
        files.append(describe_file(path))

    runtime = pinned_runtime()
    page_html = entry.read_text(encoding="utf-8")
    origins = (
        derive_external_origins(page_html)
        if external_origins is None
        else _within_cap(external_origins)
    )

    return PublishManifest(
        schemaVersion=SCHEMA_VERSION,
        kind=ARTIFACT_KIND,
        entry=ENTRY_NAME,
        producer=ProducerInfo(
            name=PRODUCER_NAME, version=producer_version or _producer_version()
        ),
        runtime=RuntimeInfo(
            name=RUNTIME_NAME, version=runtime.version, sha256=runtime.sha256
        ),
        files=files,
        externalOrigins=list(origins),
        runtimeScriptSources=list(runtime_script_sources(page_html)),
        title=declared_title(page_html),
    )


def _within_cap(origins: Iterable[str]) -> tuple[str, ...]:
    """The origins, sorted and deduplicated, or a refusal if there are too many.

    Named for what it checks rather than what it used to do. This trimmed the
    list to `MAX_EXTERNAL_ORIGINS` until the cap was raised; the trim is what is
    actually being removed here, and the raise is the point - see the constant.
    """
    ordered = tuple(sorted(set(origins)))
    if len(ordered) > MAX_EXTERNAL_ORIGINS:
        raise PublishManifestError(
            f"The page contacts {len(ordered)} external origins and the publish "
            f"API accepts at most {MAX_EXTERNAL_ORIGINS}: "
            + ", ".join(ordered)
            + ". Nothing here can choose which of them to drop - a "
            "Content-Security-Policy missing any one of them blocks that host "
            "in a recipient's browser and says so nowhere else - so this is a "
            "map that needs a person to look at what it depends on."
        )
    return ordered


def _check_flat_name(name: str) -> None:
    if not FLAT_NAME_PATTERN.match(name):
        raise PublishManifestError(
            f"{name!r} is not a publishable file name. Names have to be flat, "
            "start with a letter or digit, and stay within 64 characters."
        )


def _producer_version() -> str:
    """The plugin's version, from the one file a release always bumps.

    Imported here rather than at module scope on purpose: the writer imports
    this package, and importing it back at module scope would make the direction
    of that dependency depend on which module a caller happened to reach first.
    """
    from ..writers.onlymap_writer import PLUGIN_VERSION

    return PLUGIN_VERSION
