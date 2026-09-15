"""The pinned runtime, loaded from a CDN instead of from beside the page.

Only hosted maps do this. A folder, a ZIP and a single HTML file all have to
open with no internet, so they keep the runtime bytes they were given - see
`OnlyMapWriter.write`. A hosted map is already useless offline, so it can trade
the copy for a URL every other map on the internet shares a cache entry with.

Trading a local file for a URL means trusting a third party with the code that
draws the map, which is exactly what subresource integrity exists to refuse. The
`integrity` attribute names the digest the fetched bytes must have; a CDN that
served anything else is rejected by the browser and the map does not load,
rather than loading something we did not pin.

**Everything comes out of `runtime-lock.json`.** Not one version string is
written here. The lock file is the single place a runtime bump has to be made,
and a second copy in code is a second copy that can be forgotten - which is the
same failure `_plugin_version` exists to prevent for the plugin's own version.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from .runtime_manager import RUNTIME_JS, read_lock

# jsdelivr rather than unpkg: both serve byte-identical copies of the npm
# tarball - verified against the registry for the pinned build - and jsdelivr is
# the one with a documented multi-CDN origin. The path is npm's own layout, so
# nothing about it is jsdelivr-specific if the host ever has to change.
CDN_URL_TEMPLATE = "https://cdn.jsdelivr.net/npm/{package}@{version}/dist/{file}"

# Anonymous, not omitted. A script fetched cross-origin is opaque to the page
# unless CORS says otherwise, and the browser refuses to check `integrity`
# against bytes it is not allowed to read - so without this the pin would be
# silently unenforced. "anonymous" also means no cookies leave with the request.
CROSSORIGIN = "anonymous"


class RuntimeLockError(RuntimeError):
    """`runtime-lock.json` cannot describe the runtime a hosted page must load."""


@dataclass(frozen=True)
class PinnedRuntime:
    """One pinned runtime file, as a hosted page has to refer to it."""

    package: str
    version: str
    file_name: str
    url: str
    integrity: str
    sha256: str
    size_bytes: int


def sri_from_hex(digest_hex: str) -> str:
    """The `integrity` form of a digest already recorded as hex.

    A conversion, never a second hash: the two spellings must be the same bytes,
    and re-hashing something to produce the base64 form is how they stop being.
    """
    try:
        raw = binascii.unhexlify(digest_hex)
    except (binascii.Error, ValueError) as error:
        raise RuntimeLockError(f"{digest_hex!r} is not a hex sha256.") from error
    if len(raw) != 32:
        raise RuntimeLockError(f"{digest_hex!r} is not a 32-byte sha256.")
    return f"sha256-{base64.b64encode(raw).decode('ascii')}"


def pinned_runtime(file_name: str = RUNTIME_JS) -> PinnedRuntime:
    """What the lock file says a hosted page must load, or an error saying why not.

    Hard failure, unlike `lock_mismatches`, which only warns. A warning is right
    when the bytes on disk disagree with the pin - the export still works. Here
    there are no bytes at all: an unresolvable pin means the emitted page would
    carry a URL or an integrity value that is wrong, and a wrong `integrity`
    does not degrade, it blank-screens.

    `onlymap.standalone.js` by default and by design. The other build,
    `onlymapjs.js`, is code-split: it pulls further chunks at runtime, and a
    single `integrity` value cannot pin files the attribute never names.
    """
    lock = read_lock()
    if not lock:
        raise RuntimeLockError("runtime-lock.json is missing or unreadable.")

    package = lock.get("package")
    version = lock.get("version")
    entry = (lock.get("files") or {}).get(file_name) or {}
    digest = entry.get("sha256")
    size = entry.get("bytes")
    if not (package and version and digest and isinstance(size, int)):
        raise RuntimeLockError(
            f"runtime-lock.json does not fully pin {file_name}: it needs a "
            "package, a version, and that file's sha256 and bytes."
        )

    # Recorded base64 wins, but a lock file that only ever stored hex is still
    # usable - the two forms are the same digest, so one can always produce the
    # other. Verified either way, so a mistyped `sri` is caught here rather than
    # by a browser refusing to run the runtime.
    integrity = entry.get("sri") or sri_from_hex(digest)
    if integrity != sri_from_hex(digest):
        raise RuntimeLockError(
            f"runtime-lock.json's `sri` for {file_name} is not the base64 form "
            "of its own sha256."
        )

    return PinnedRuntime(
        package=str(package),
        version=str(version),
        file_name=file_name,
        url=CDN_URL_TEMPLATE.format(package=package, version=version, file=file_name),
        integrity=integrity,
        sha256=str(digest),
        size_bytes=size,
    )


def runtime_script_tag(runtime: PinnedRuntime, indent: str = "    ") -> str:
    """The `<script>` element a hosted page loads the runtime with.

    `type="module"`, because the standalone build is one. It is published from
    a `"type": "module"` package and its top level evaluates
    `new URL(..., import.meta.url)` to locate the raster worker it ships inline;
    `import.meta` is a SYNTAX error in a classic script, so the browser discards
    the entire bundle before a single statement runs.

    That failure is silent in every way that matters. The response is a 200, the
    integrity check passes, the CSP is satisfied, the `<om-map>` markup sits in
    the document untouched - and nothing upgrades it, so the page renders blank
    with one console line nobody is watching. This docstring previously asserted
    the opposite, and a unit test asserted it back, which is how every hosted map
    shipped with a runtime that could not parse.

    `defer` is kept although a module script defers by default: it is redundant,
    not wrong, and the attribute states the ordering the page depends on - that
    the `<om-map>` markup above is already in the document when the elements
    upgrade - for a reader who should not have to know that rule.

    The offline tiers reached the same conclusion independently; see
    `writers/onlymap_writer._inline_runtime_element`.
    """
    return "\n".join(
        [
            f"{indent}<script",
            f'{indent}  type="module"',
            f'{indent}  src="{runtime.url}"',
            f'{indent}  integrity="{runtime.integrity}"',
            f'{indent}  crossorigin="{CROSSORIGIN}"',
            f"{indent}  defer",
            f"{indent}></script>",
        ]
    )
