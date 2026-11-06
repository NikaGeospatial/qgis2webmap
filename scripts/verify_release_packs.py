#!/usr/bin/env python3
"""Assert a published release serves the language packs its tag pins.

    python3 scripts/verify_release_packs.py v0.3.3

Every copy of a version downloads its languages from one asset of its GitHub
release, `qgis2webmap-language-packs-<version>.zip` (`ui/languages.py`). This
fetches that asset the way the plugin does - over https, following redirects
only to GitHub's hosts - and checks it with the plugin's own verification
against the pins in `packs.json` at the tag. `verify_tag_packs.py` checks the
tag before the release is built; this checks what users get after it is
published, so it fails while the asset is missing or differs.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nika_onlymap_exporter.ui import languages

PACKS_JSON = "nika_onlymap_exporter/i18n/packs.json"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand each redirect back, so every hop is checked as the plugin checks it."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def fetch(url: str) -> languages.Reply:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url, timeout=60) as response:
            return languages.Reply(
                response.status,
                response.read(languages.MAX_DOWNLOAD_BYTES + 1),
            )
    except urllib.error.HTTPError as error:
        return languages.Reply(error.code, location=error.headers.get("Location", ""))


def pins_at(ref: str) -> dict[str, str] | None:
    shown = subprocess.run(
        ["git", "show", f"{ref}:{PACKS_JSON}"], capture_output=True, check=False
    )
    if shown.returncode != 0:
        return None
    return {code: e["sha256"] for code, e in json.loads(shown.stdout)["packs"].items()}


def check(version: str, pins: dict[str, str], get: languages.Fetcher) -> int:
    """How many packs the release serves, every one matching `pins`."""
    data = languages._get(languages.asset_url(version), get)
    return len(languages.verified_packs(data, pins))


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].startswith("v"):
        print(__doc__, file=sys.stderr)
        return 2
    tag = sys.argv[1]
    pins = pins_at(tag)
    if pins is None:
        print(f"error: no tag {tag} here with {PACKS_JSON}", file=sys.stderr)
        return 1
    try:
        count = check(tag[1:], pins, fetch)
    except languages.LanguageDownloadError as error:
        print(f"FAILED -- {languages.asset_url(tag[1:])}: {error}", file=sys.stderr)
        return 1
    print(f"the {tag} release serves all {count} language packs, matching packs.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
