"""Layer data as its own file, addressed by what it contains.

The single-file artifact inlines everything because it has to: `file://` blocks
`fetch` of a sibling, so a page opened from a disk can only read data it already
carries. A hosted map is served over HTTP and has the opposite problem - eight
megabytes of GeoJSON in the page is eight megabytes the browser cannot cache,
re-parse-free, or start rendering around. So hosted exports write each layer's
data beside the page and let the runtime fetch it.

**Nothing here is compressed.** A hosted page is served under a
Content-Security-Policy whose `script-src` names only the SRI-pinned runtime, so
the inline shim that inflates a gzipped block cannot run at all. The compression
still happens - as `Content-Encoding` on the wire, which the server applies and
the browser undoes before our code ever sees the bytes. That is strictly better
than the inline form: it is transparent, it covers the page as well as the data,
and it costs no base64.

**The URL is the content digest.** The server stores every asset under
`assets/{sha256}.{ext}` and serves that key directly; a page that asked for a
file by any other name would force a per-request lookup to translate it. Naming
the digest in the page removes that lookup, and makes the reference immutable -
a URL either resolves to exactly those bytes or does not resolve.

That is also why the local file name is *not* the digest. The names written here
have to satisfy a flat 64-character rule, and a 64-character digest plus an
extension does not fit. The local name is for the operator reading the directory;
`assets/{digest}.{ext}` is for the browser. The publish manifest carries both.

A hosted page writes that reference root-absolute, as `/assets/{digest}.{ext}`,
so it resolves to the same URL whether or not the link the visitor followed
ended in a slash. `HOSTED_ASSET_URL_PREFIX` has the whole argument.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..core.manifest_builder import DataPayload

# GeoJSON, and named as such. The runtime sniffs the format from the URL's
# extension before it looks at any Content-Type, so `.geojson` is what decides
# the file is parsed as features rather than guessed at. `.json` would fall
# through to a JSON default instead, which is a different code path for no gain.
DATA_EXTENSION = ".geojson"

# A raster's extension, for the same reason. `raster_staging` writes a
# Cloud-Optimized GeoTIFF and the served key has to keep the suffix: the
# runtime's COG reader is selected by it, and `publish_manifest.ROLES_BY_SUFFIX`
# reads the same character sequence to decide the file is a raster rather than
# something nobody meant to publish.
RASTER_EXTENSION = ".tif"

# Where the server keeps every asset. A prefix, not a directory we create: no
# such folder exists in the built output, because the local names and the served
# keys are deliberately different. See the module docstring.
ASSET_URL_PREFIX = "assets/"

# What a *hosted* page writes instead. Root-absolute, and it has to be: a hosted
# map is reachable both as `maps.acme.com/lagos-housing` and, if the link the
# visitor was given carried a trailing slash, as `maps.acme.com/lagos-housing/`.
# A relative `assets/x` resolves against the last path segment, so those two
# spellings of the same page would ask for two different URLs, and one of them
# would 404 - a broken map decided by a character in a link nobody controls.
# A leading slash removes the ambiguity, and it also means every map on a shared
# hostname shares one cache entry per digest instead of one per map.
#
# Hosted only. A folder or ZIP export may be opened from a subdirectory, and a
# single file may be opened over `file://`, where a leading slash points at the
# root of the disk rather than at the site - so those tiers keep the relative
# form above. The two are not interchangeable, which is why they are two
# constants and why callers have to say which tier they are writing for.
HOSTED_ASSET_URL_PREFIX = "/assets/"

# The file-name rule the built output has to satisfy. Flat, no directory
# separators, no leading dot, and short enough to survive any archive format or
# object store it passes through.
FLAT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_FLAT_NAME_LENGTH = 64

# What a name falls back to when the layer's own name survives sanitising as
# nothing at all - a layer called "Ц" or "..." is entirely possible.
FALLBACK_STEM = "layer"


class HostedModeConflictError(ValueError):
    """Hosted output and inline compression were both asked for.

    They cannot both happen. Inline compression relies on a shim in an inline
    `<script>` to inflate the blocks before the runtime upgrades the elements,
    and a hosted page's CSP forbids inline script. Silently dropping one of the
    two would produce a map that loads and draws nothing.
    """


def asset_url(sha256: str, extension: str) -> str:
    """The relative content-addressed reference, for a tier served from a path.

    A function as well as the property below because layer data is not the only
    thing that gets addressed this way: a raster is written by `raster_staging`,
    which has no `ExternalDataFile` to ask. One spelling of the rule, so the two
    payload kinds cannot come to disagree about where an asset lives.
    """
    return f"{ASSET_URL_PREFIX}{sha256}{extension}"


def hosted_asset_url(sha256: str, extension: str) -> str:
    """The root-absolute form a hosted page writes. See `HOSTED_ASSET_URL_PREFIX`."""
    return f"{HOSTED_ASSET_URL_PREFIX}{sha256}{extension}"


def sha256_of_file(path: Path) -> str:
    """A file's digest, read in blocks rather than into one buffer.

    Layer data is hashed from bytes already in memory, because it was just
    serialised there. A raster is not: an orthophoto is routinely larger than
    the machine would like to hold twice, and the digest is needed for exactly
    the same reason - it is the name the page refers to the file by.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class ExternalDataFile:
    """One written data file, and how the page must refer to it."""

    key: str
    path: Path
    sha256: str
    size_bytes: int

    @property
    def url(self) -> str:
        """The content-addressed URL the page uses, not the local file name."""
        return asset_url(self.sha256, DATA_EXTENSION)

    @property
    def hosted_url(self) -> str:
        """The same reference for a served page: root-absolute, not relative.

        A separate property rather than a flag on the one above, so a caller
        cannot get the tier wrong by leaving an argument off. See
        `HOSTED_ASSET_URL_PREFIX` for why the two forms cannot be shared.
        """
        return hosted_asset_url(self.sha256, DATA_EXTENSION)


def flat_file_name(hint: str, extension: str, taken: Iterable[str] = ()) -> str:
    """A file name derived from a layer name that obeys the flat-name rule.

    Sanitising rather than escaping: a name that has to round-trip back to the
    layer would need an encoding, and nothing needs that - the manifest carries
    the mapping. So anything outside the allowed set becomes a hyphen, runs
    collapse, and the result is truncated to leave room for the extension.

    Collisions are resolved with a numeric suffix rather than by falling back to
    the digest, so two layers called the same thing still produce two files a
    person can tell apart.
    """
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", hint).strip("-._")
    stem = re.sub(r"-{2,}", "-", stem) or FALLBACK_STEM
    if not stem[0].isalnum():  # pragma: no cover - stripped above, kept as a guard
        stem = f"{FALLBACK_STEM}-{stem}"

    budget = MAX_FLAT_NAME_LENGTH - len(extension)
    used = set(taken)
    candidate = f"{stem[:budget]}{extension}"

    # Room for the suffix comes out of the stem, not out of the limit: appending
    # to an already-maximal name would produce a 65-character one that fails the
    # very rule this function exists to satisfy.
    counter = 2
    while candidate in used:
        suffix = f"-{counter}"
        candidate = f"{stem[: budget - len(suffix)]}{suffix}{extension}"
        counter += 1
    return candidate


def write_external_data(
    payloads: Iterable[DataPayload], destination: Path
) -> tuple[ExternalDataFile, ...]:
    """Write each payload beside the page and hash what was written.

    Ordering is the point of this function existing at all. The page has to name
    each file by its digest, so every file is written and hashed *before* the
    page is rendered - the page itself is hashed last, once it is complete and
    the digests it contains are settled. Anything that reversed that would be
    hashing a page that still had placeholders in it.

    Encoded as UTF-8 with no BOM, matching what the inline path puts in the
    document, so the same data produces the same digest either way.
    """
    destination.mkdir(parents=True, exist_ok=True)

    written: list[ExternalDataFile] = []
    names: set[str] = set()
    for payload in payloads:
        name = flat_file_name(payload.name_hint, DATA_EXTENSION, names)
        names.add(name)

        data = payload.text.encode("utf-8")
        path = destination / name
        path.write_bytes(data)
        written.append(
            ExternalDataFile(
                key=payload.key,
                path=path,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
            )
        )
    return tuple(written)


def data_urls(files: Iterable[ExternalDataFile], *, hosted: bool) -> dict[str, str]:
    """The mapping `build_manifest` takes: element key to content-addressed URL.

    `hosted` picks which of the two reference forms the page gets, and has no
    default on purpose: the wrong one does not fail at export time, it fails in
    somebody else's browser, so the tier has to be stated at every call site.
    """
    return {file.key: (file.hosted_url if hosted else file.url) for file in files}
