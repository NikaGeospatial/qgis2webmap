#!/usr/bin/env python3
"""Check the CSP tables in `publish_manifest` against the runtime we pin.

    python scripts/verify_basemap_origins.py [--json]

Three tables, all in `packaging/publish_manifest.py`, all deciding part of the
Content-Security-Policy a published map is served under: `BASEMAP_TILE_ORIGINS`,
`TERRAIN_TILE_ORIGINS` and the `TERRAIN_SCRIPT_SOURCES` whitelist. Get any of
them wrong and the browser blocks the very thing the map was configured to draw
- tiles, elevation, or the worker that turns one into the other - and nothing in
the export or the publish says why. This script is how they are kept honest.

The name still says basemap because that is the table it was written for, and
because it is quoted by name in three error messages and a test. It grew to
cover the other two rather than being split: all three are read out of the same
eight-megabyte bundle, and a sibling script would download it a second time and
copy this one's fetch, digest and origin helpers to do it.

**It needs the network, and that is why it is a script and not a test.** The
unit suite runs offline; it can only check that each table was verified against
the version currently pinned (see `BASEMAP_ORIGINS_VERIFIED_AGAINST` and its two
counterparts). Actually re-deriving the answers means fetching things. Run this
after bumping `runtime-lock.json`, then move those constants to the new version.

What it does, in the same order the browser does:

1. Downloads the pinned `onlymap.standalone.js` from the CDN URL the hosted
   pages themselves load, and verifies it against the digest in the lock file -
   so what is read is provably the build we ship against, not whatever `latest`
   happens to be today.
2. Reads the `registerBasemap(...)` calls out of it. That registry is the only
   place a preset name becomes a style, so it is the only correct source.
3. For every preset the exporter can emit, resolves that style the way MapLibre
   would: fetch the style JSON, collect its sprite, glyphs and source URLs, then
   follow any TileJSON `url` to the tile templates it hands back. CARTO's vector
   styles need that last hop - their tiles live on four `tiles-a..d` hosts named
   nowhere in the style itself.
4. Reads the `registerTerrain(...)` calls the same way. Relief needs no style
   hop at all: a terrain preset's `elevationData` is already a `{z}/{x}/{y}`
   template, so the origin is in the bundle.
5. Rebuilds the mesh-decoder worker URL from the bundle rather than trusting the
   whitelist's spelling of it - the loaders.gl module, version and worker
   filename that loaders.gl's own URL builder concatenates - and checks the
   result is exactly what `TERRAIN_SCRIPT_SOURCES` names.
6. Compares all of that against the tables, and reports every difference in both
   directions. A missing origin blocks tiles; a stale extra one spends part of a
   twenty-origin budget on a host nothing contacts.

Exit status is 0 when the tables match, 1 when they do not, and 2 when the
check could not be made at all - an unreachable host is not a passing table.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nika_onlymap_exporter.core.manifest_builder import (  # noqa: E402
    BASEMAP_PRESETS,
    TERRAIN_PRESETS,
)
from nika_onlymap_exporter.packaging.cdn_runtime import pinned_runtime  # noqa: E402
from nika_onlymap_exporter.packaging.publish_manifest import (  # noqa: E402
    BASEMAP_ORIGINS_VERIFIED_AGAINST,
    BASEMAP_TILE_ORIGINS,
    RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST,
    TERRAIN_ORIGINS_VERIFIED_AGAINST,
    TERRAIN_SCRIPT_SOURCES,
    TERRAIN_TILE_ORIGINS,
)

# Long, because the bundle is eight megabytes and a style server on a slow day
# is still a correct answer. This runs by hand, not in a build's critical path.
TIMEOUT_SECONDS = 60

USER_AGENT = "Mozilla/5.0 (compatible; qgis2webmap-basemap-origin-check)"

# The registry as it is emitted: minified builds rename the function but keep
# the call shape and the string literals, so match on the argument rather than
# on the identifier. The body is taken up to the matching brace by hand below,
# because a preset's style can be an inline object with nested braces.
_REGISTER_PATTERN = re.compile(r'([A-Za-z_$][\w$]*)\("([^"]+)",\s*\{')

# `{key}` presets are MapTiler's. The exporter never emits them - a key in an
# exported page is a key every recipient can spend - so they are out of scope
# here rather than something to resolve without a key.
_KEY_PLACEHOLDER = "{key}"

# The terrain registry's payload. Unlike a basemap, a relief preset needs no
# style hop: `elevationData` is already a tile template, and `texture` - when a
# preset carries one - is another. Both count, so both are matched.
_ELEVATION_PATTERN = re.compile(r'elevationData:\s*"([^"]+)"')
_TEXTURE_PATTERN = re.compile(r'texture:\s*"([^"]+)"')

# The loaders.gl loader object deck.gl's TerrainLayer decodes DEM tiles with.
# Matched on the string literals rather than on the identifier, for the reason
# `_REGISTER_PATTERN` is: minification renames `jEA` and `Ese` every build but
# cannot touch `"terrain"` or the shape of the object around it. `worker: !0` is
# part of the match on purpose - a build that stopped using a worker for this
# loader would stop needing the whitelist entry, and that must not look like a
# version bump.
_TERRAIN_LOADER_PATTERN = re.compile(
    r'id: "terrain",\s*module: "terrain",\s*version: ([\w$]+),\s*worker: !0'
)

# loaders.gl's own worker-URL builder, as it survives into the bundle:
#
#     r = `https://unpkg.com/@loaders.gl/${e.module}${o}/dist/${i}`;
#
# with `o` the `@`-prefixed version and `i` the `${e.id}-worker.js` filename the
# browser branch picks. Asserting the template still has this shape is what
# stops this script from confirming a whitelist entry the runtime no longer
# builds - if loaders.gl ever changes host, layout or filename, the entry is
# wrong in a way no amount of re-reading the version would catch.
_WORKER_TEMPLATE_PATTERN = re.compile(
    r"https://unpkg\.com/@loaders\.gl/\$\{\w+\.module\}\$\{\w+\}/dist/\$\{\w+\}"
)
_WORKER_FILENAME_PATTERN = re.compile(r"\$\{\w+\.id\}-worker\.js")

# How that URL is reached, which is not `new Worker(url)`. loaders.gl wraps it
# in `importScripts('<url>')`, blobs that one-liner and constructs the worker
# from the `blob:` URL - so the remote fetch is a script import inside a worker
# that inherits the page's policy, and it is `script-src` that has to name it.
# Checked here because the whitelist's own note tells a server which directives
# to build from these entries, and that instruction is only right while the
# runtime still loads them this way.
_IMPORT_SCRIPTS_PATTERN = re.compile(r"importScripts\('\$\{\w+\}'\)")

# What that builder produces once the three holes are filled. Written out here
# rather than assembled by hand at the call site so the one place this script
# claims to know the URL's shape is next to the evidence that it does.
_WORKER_URL = "https://unpkg.com/@loaders.gl/{module}@{version}/dist/{id}-worker.js"

# loaders.gl's sentinel for an unpinned loader. Reaching it means the runtime
# asks unpkg for whatever is newest at the moment the first DEM tile lands, and
# a whitelist entry cannot honestly pin that - see `terrain_worker_url`.
_UNPINNED_VERSION = "latest"


class VerificationError(RuntimeError):
    """The check could not be carried out, which is not the same as a failure."""


def fetch(url: str) -> bytes:
    """One GET, with a timeout, and a plain error if it does not arrive.

    A browser-shaped `User-Agent` because some tile hosts - OpenFreeMap among
    them - answer urllib's default with a 403. Identifying the tool as well, so
    an operator reading their logs can see what this is and that it asks for a
    style document a handful of times a year, not for tiles.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = response.read()
            assert isinstance(data, bytes)
            return data
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise VerificationError(f"could not fetch {url}: {exc}") from exc


def pinned_bundle() -> str:
    """The pinned runtime's source, proven to be the pinned bytes."""
    runtime = pinned_runtime()
    data = fetch(runtime.url)
    digest = hashlib.sha256(data).hexdigest()
    if digest != runtime.sha256:
        raise VerificationError(
            f"{runtime.url} served sha256 {digest}, but runtime-lock.json pins "
            f"{runtime.sha256}. Nothing read out of those bytes would describe "
            "the runtime this plugin ships against."
        )
    return data.decode("utf-8", errors="replace")


def _balanced_object(source: str, open_brace: int) -> str:
    """The `{...}` starting at `open_brace`, brace-counted rather than regexed.

    Crude on purpose. A brace inside a string literal would throw the count off,
    and the registry has none - checked by the fact that every preset parsed
    here round-trips to a usable style. A real JS parser for one call shape
    would be a dependency this script does not otherwise need.
    """
    depth = 0
    for index in range(open_brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : index + 1]
    raise VerificationError("a registerBasemap call in the bundle never closes.")


def registered_styles(bundle: str) -> dict[str, str]:
    """Preset name to style URL, for the presets that resolve to a URL.

    Presets whose style is an inline object - `osm` is one - come back with the
    tile templates found inside that object joined into the same mapping, since
    for CSP purposes a template and a style URL are both just somewhere the
    browser goes. Keyed presets are skipped entirely; see `_KEY_PLACEHOLDER`.
    """
    found: dict[str, str] = {}
    for match in _REGISTER_PATTERN.finditer(bundle):
        name = match.group(2)
        if name not in BASEMAP_PRESETS:
            continue
        body = _balanced_object(bundle, match.end() - 1)
        if _KEY_PLACEHOLDER in body:
            continue

        style = re.search(r'style:\s*"([^"]+)"', body)
        if style is not None:
            found[name] = style.group(1)
            continue

        # An inline style object: the tile templates are right there, so there
        # is nothing to fetch and the origins are read straight out of the body.
        tiles = re.findall(r'"(https://[^"]+/\{z\}[^"]*)"', body)
        if tiles:
            found[name] = tiles[0]
    return found


def registered_terrain_origins(bundle: str) -> dict[str, tuple[str, ...]]:
    """Relief preset to the origins it fetches, straight out of the registry.

    No network hop, unlike `origins_for_style`: `registerTerrain` stores a
    `{z}/{x}/{y}` DEM template as a literal, so the host is in the bundle. A
    preset's optional `texture` is collected too - the exporter never emits a
    preset that has one, but a table built from a partial read of the registry
    would be wrong the day it does.

    Keyed presets are skipped for the reason basemaps' are; see
    `_KEY_PLACEHOLDER`.
    """
    found: dict[str, tuple[str, ...]] = {}
    for match in _REGISTER_PATTERN.finditer(bundle):
        name = match.group(2)
        if name not in TERRAIN_PRESETS:
            continue
        body = _balanced_object(bundle, match.end() - 1)
        if _KEY_PLACEHOLDER in body:
            continue
        urls = _ELEVATION_PATTERN.findall(body) + _TEXTURE_PATTERN.findall(body)
        if urls:
            found[name] = tuple(sorted({_origin(url) for url in urls if _origin(url)}))
    return found


def terrain_worker_url(bundle: str) -> str:
    """The exact URL the runtime will ask unpkg for the mesh decoder at.

    Rebuilt from the bundle's own parts rather than read as one string, because
    it never exists as one string: loaders.gl concatenates the module, the
    version and the `-worker.js` filename at the moment it needs a worker. So
    all three parts are checked, and so is the template that joins them.

    Raises rather than returning something plausible when the version cannot be
    pinned. An unpinned worker - loaders.gl's `latest` sentinel - is a finding,
    not a value: a whitelist entry naming a version the runtime does not ask for
    would authorise one URL and block the one actually fetched, and inventing a
    pin to make this script pass is the failure it exists to prevent.
    """
    if _WORKER_TEMPLATE_PATTERN.search(bundle) is None:
        raise VerificationError(
            "the bundle no longer builds worker URLs as "
            "`https://unpkg.com/@loaders.gl/<module>@<version>/dist/<file>`. "
            "TERRAIN_SCRIPT_SOURCES pins a URL shape the runtime has stopped "
            "using, so it authorises the wrong thing and blocks the right one."
        )
    if _WORKER_FILENAME_PATTERN.search(bundle) is None:
        raise VerificationError(
            "the bundle no longer names its browser workers `<id>-worker.js`, "
            "so the file TERRAIN_SCRIPT_SOURCES pins is not the file fetched."
        )
    if _IMPORT_SCRIPTS_PATTERN.search(bundle) is None:
        raise VerificationError(
            "the bundle no longer reaches its worker URLs through an "
            "`importScripts` shim in a blob. The whitelist's entries are still "
            "the right URLs, but the note above TERRAIN_MESH_WORKER tells a "
            "server to put them in `script-src` and to allow `worker-src "
            "blob:`, and that is only correct while this is how they load."
        )

    loader = _TERRAIN_LOADER_PATTERN.search(bundle)
    if loader is None:
        raise VerificationError(
            "the bundle registers no worker-backed loaders.gl terrain loader. "
            "Either the mesh decoding moved off a Web Worker - in which case "
            "TERRAIN_SCRIPT_SOURCES should lose its entry rather than keep a "
            "stale one - or the loader object changed shape and this check can "
            "no longer read it."
        )

    version = _const_value(bundle, loader.group(1))
    if version is None:
        raise VerificationError(
            f"the terrain loader's version is the identifier {loader.group(1)!r} "
            "and the bundle assigns it no string literal, so which loaders.gl "
            "release the worker comes from cannot be established."
        )
    if version == _UNPINNED_VERSION:
        raise VerificationError(
            "the terrain loader carries no version, so the runtime fetches its "
            "mesh decoder from an unpinned `@latest` URL. That cannot be "
            "path-pinned: any entry naming a version would block the worker, "
            "and an entry without one would authorise every version of the "
            "package. This needs a decision, not a table edit."
        )
    return _WORKER_URL.format(module="terrain", version=version, id="terrain")


def _const_value(bundle: str, name: str) -> str | None:
    """The string a minified `const` was assigned, or `None` if it was not one."""
    match = re.search(rf"\b{re.escape(name)}\s*=\s*\"([^\"]*)\"", bundle)
    return match.group(1) if match is not None else None


def _style_urls(style: object) -> tuple[set[str], set[str]]:
    """Every URL a style JSON sends the browser to, and which are TileJSON.

    Two sets rather than one, because only a source's `url` is a document worth
    following - a sprite or a glyph template is a leaf, and fetching one of
    those either 404s on its `{fontstack}` placeholder or downloads a PNG for
    nothing. Both sets count towards the origins; only the second is resolved
    one hop further.
    """
    urls: set[str] = set()
    tilejson: set[str] = set()
    if not isinstance(style, dict):
        return urls, tilejson
    for field in ("sprite", "glyphs"):
        value = style.get(field)
        if isinstance(value, str):
            urls.add(value)
        elif isinstance(value, list):
            # The multi-sprite form: a list of {id, url} entries.
            for entry in value:
                if isinstance(entry, dict) and isinstance(entry.get("url"), str):
                    urls.add(entry["url"])
    sources = style.get("sources")
    if isinstance(sources, dict):
        for source in sources.values():
            if not isinstance(source, dict):
                continue
            if isinstance(source.get("url"), str):
                urls.add(source["url"])
                tilejson.add(source["url"])
            tiles = source.get("tiles")
            if isinstance(tiles, list):
                urls.update(tile for tile in tiles if isinstance(tile, str))
    return urls, tilejson


def _tilejson_urls(url: str) -> set[str]:
    """The tile templates a TileJSON hands back, or nothing if it is not one.

    This hop is the reason the table cannot be written from the style URL alone.
    CARTO's styles point their vector source at a `tiles.json`, and only that
    document names `tiles-a` through `tiles-d`, which MapLibre then round-robins
    over. A table built without following it would declare two of six origins.
    """
    if "{z}" in url:  # already a template, nothing to resolve
        return set()
    try:
        document = json.loads(fetch(url))
    except ValueError:
        return set()
    if not isinstance(document, dict):
        return set()
    tiles = document.get("tiles")
    if not isinstance(tiles, list):
        return set()
    return {tile for tile in tiles if isinstance(tile, str)}


def origins_for_style(style_url: str) -> tuple[str, ...]:
    """Resolve one preset's style the way the browser would, to origins."""
    urls = {style_url}
    if "{z}" not in style_url:
        document = json.loads(fetch(style_url))
        referenced, tilejson = _style_urls(document)
        urls |= referenced
        for url in tilejson:
            urls |= _tilejson_urls(url)
    return tuple(sorted({_origin(url) for url in urls if _origin(url)}))


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def verify() -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    """Compare every resolvable preset against the tables. Differences only.

    One bundle, three tables. Preset keys are namespaced in the result -
    `basemap:voyager`, `terrain:terrarium`, `script:terrain-worker` - because
    the three registries share names in principle and a report that said only
    `terrarium` would not say which table to go and fix.
    """
    bundle = pinned_bundle()
    styles = registered_styles(bundle)
    terrains = registered_terrain_origins(bundle)

    problems: dict[str, dict[str, list[str]]] = {}
    notes: list[str] = []

    for preset in TERRAIN_PRESETS:
        if preset == "none":
            continue
        observed = terrains.get(preset)
        if observed is None:
            notes.append(
                f"terrain:{preset}: the pinned runtime registers no keyless "
                "relief preset under this name. Either it was removed upstream "
                "or it now needs an API key, and the exporter should stop "
                "offering it in TERRAIN_PRESETS."
            )
            continue
        expected = tuple(TERRAIN_TILE_ORIGINS.get(preset, ()))
        if set(observed) != set(expected):
            problems[f"terrain:{preset}"] = {
                "missing": sorted(set(observed) - set(expected)),
                "stale": sorted(set(expected) - set(observed)),
                "observed": list(observed),
            }

    # The whitelist, checked against the URL the runtime actually constructs.
    # A failure to construct it at all is a finding about the runtime rather
    # than about the table, so it is reported and does not abort the rest.
    try:
        worker = terrain_worker_url(bundle)
    except VerificationError as exc:
        notes.append(f"script:terrain-worker: {exc}")
    else:
        declared = {
            source for sources in TERRAIN_SCRIPT_SOURCES.values() for source in sources
        }
        if declared != {worker}:
            problems["script:terrain-worker"] = {
                "missing": sorted({worker} - declared),
                "stale": sorted(declared - {worker}),
                "observed": [worker],
            }

    for preset in BASEMAP_PRESETS:
        if preset == "none":
            continue
        style_url = styles.get(preset)
        if style_url is None:
            notes.append(
                f"basemap:{preset}: the pinned runtime registers no keyless "
                "style for this name. Either the preset was removed upstream or "
                "it now needs an API key, and the exporter should stop offering "
                "it."
            )
            continue
        observed = origins_for_style(style_url)
        expected = tuple(BASEMAP_TILE_ORIGINS.get(preset, ()))
        if set(observed) != set(expected):
            problems[f"basemap:{preset}"] = {
                "missing": sorted(set(observed) - set(expected)),
                "stale": sorted(set(expected) - set(observed)),
                "observed": list(observed),
            }

    # A table entry for a preset the exporter cannot emit is dead weight rather
    # than a broken map, so it is a note. Checked per table: the two have
    # different key sets and a stale entry in one says nothing about the other.
    for label, table, presets in (
        ("BASEMAP_TILE_ORIGINS", BASEMAP_TILE_ORIGINS, BASEMAP_PRESETS),
        ("TERRAIN_TILE_ORIGINS", TERRAIN_TILE_ORIGINS, TERRAIN_PRESETS),
        ("TERRAIN_SCRIPT_SOURCES", TERRAIN_SCRIPT_SOURCES, TERRAIN_PRESETS),
    ):
        unknown = sorted(set(table) - set(presets))
        if unknown:
            notes.append(
                f"{label} names presets the exporter cannot emit: " + ", ".join(unknown)
            )
    return problems, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args(argv)

    runtime = pinned_runtime()
    try:
        problems, notes = verify()
    except VerificationError as exc:
        print(f"could not verify: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "version": runtime.version,
                    "verifiedAgainst": {
                        "BASEMAP_ORIGINS_VERIFIED_AGAINST": (
                            BASEMAP_ORIGINS_VERIFIED_AGAINST
                        ),
                        "TERRAIN_ORIGINS_VERIFIED_AGAINST": (
                            TERRAIN_ORIGINS_VERIFIED_AGAINST
                        ),
                        "RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST": (
                            RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST
                        ),
                    },
                    "problems": problems,
                    "notes": notes,
                },
                indent=2,
            )
        )
    else:
        for note in notes:
            print(f"note: {note}")
        for preset, detail in sorted(problems.items()):
            print(f"{preset}: table disagrees with the runtime")
            for origin in detail["missing"]:
                print(f"  missing (would be CSP-blocked): {origin}")
            for origin in detail["stale"]:
                print(f"  stale (nothing contacts it): {origin}")
        if not problems:
            print(
                "BASEMAP_TILE_ORIGINS, TERRAIN_TILE_ORIGINS and "
                f"TERRAIN_SCRIPT_SOURCES match onlymap {runtime.version}."
            )
            # Named individually rather than as a group: each records what it
            # was last read against, and a run that only re-derived some of
            # them must not look like it blessed all three.
            for constant, verified in (
                ("BASEMAP_ORIGINS_VERIFIED_AGAINST", BASEMAP_ORIGINS_VERIFIED_AGAINST),
                ("TERRAIN_ORIGINS_VERIFIED_AGAINST", TERRAIN_ORIGINS_VERIFIED_AGAINST),
                (
                    "RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST",
                    RUNTIME_SCRIPT_SOURCES_VERIFIED_AGAINST,
                ),
            ):
                if runtime.version != verified:
                    print(f'Set {constant} to "{runtime.version}" to record that.')
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
