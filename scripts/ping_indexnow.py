#!/usr/bin/env python3
"""Tell IndexNow that the documentation site has changed.

IndexNow is a push notification for search indexes: instead of waiting to be
crawled, the site says "these URLs changed". Google does not participate, so
this buys nothing there. Bing, Yandex, Seznam and Naver do - and Bing's index is
what feeds ChatGPT Search and Copilot, which is the actual reason this exists.

The protocol is deliberately small. A key is published as a text file at the
site root, whose contents are the key itself; submitting that key alongside a
list of URLs proves the submitter controls the site. **The key is not a secret**
- it is served publicly at `keyLocation` by design - so it belongs in the
repository next to the site, not in a repository secret. A secret would be
security theatre and would also break the verification, since the file has to be
readable by anyone.

Run after a deploy, against the live site:

    python3 scripts/ping_indexnow.py

URLs come from the site's own `sitemap.xml` rather than a hand-kept list, so a
new guide is submitted the day it ships without anyone remembering to add it.

Failure is never fatal. A search engine declining a submission is not a reason
to fail a documentation deploy that has already succeeded, so every error path
here reports and exits 0.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

SITE = "https://qgis2webmap.nikaplanet.com"
KEY = "33535fdeba8f806345713db08a07fa35"

# Any participating endpoint forwards to the others, so one call reaches all of
# them; api.indexnow.org is the vendor-neutral one.
ENDPOINT = "https://api.indexnow.org/indexnow"

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
TIMEOUT = 30

# The protocol's own ceiling is 10,000 URLs per request. This site has ~20, so
# the cap is a guard against a runaway sitemap rather than a real limit.
MAX_URLS = 10_000


def fetch_sitemap_urls() -> list[str]:
    """Every `<loc>` in the live sitemap."""
    with urllib.request.urlopen(f"{SITE}/sitemap.xml", timeout=TIMEOUT) as response:
        tree = ET.fromstring(response.read())

    urls = [
        element.text.strip()
        for element in tree.iter(f"{SITEMAP_NS}loc")
        if element.text and element.text.strip()
    ]

    # Submitting a URL on another host is what gets a key rejected outright, so
    # drop anything unexpected rather than trusting the sitemap blindly.
    return [url for url in urls if url.startswith(f"{SITE}/")][:MAX_URLS]


def verify_key_is_published() -> bool:
    """The key file has to be readable, and has to contain exactly the key.

    Checked before submitting because an unreadable or mismatched key file is
    the one failure that makes every future submission fail silently - the
    endpoint accepts the request and then discards it.
    """
    try:
        with urllib.request.urlopen(f"{SITE}/{KEY}.txt", timeout=TIMEOUT) as response:
            published = response.read().decode("utf-8").strip()
    except (urllib.error.URLError, OSError) as error:
        print(f"indexnow: key file is not reachable ({error})", file=sys.stderr)
        return False

    if published != KEY:
        print(
            f"indexnow: key file contains {published!r}, expected {KEY!r}",
            file=sys.stderr,
        )
        return False

    return True


def submit(urls: list[str]) -> None:
    payload = json.dumps(
        {
            "host": SITE.removeprefix("https://"),
            "key": KEY,
            "keyLocation": f"{SITE}/{KEY}.txt",
            "urlList": urls,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        # 200 accepted, 202 accepted-but-key-still-being-validated. Both fine.
        print(f"indexnow: submitted {len(urls)} URLs, HTTP {response.status}")


def main() -> int:
    if not verify_key_is_published():
        print("indexnow: skipped", file=sys.stderr)
        return 0

    try:
        urls = fetch_sitemap_urls()
    except (urllib.error.URLError, ET.ParseError, OSError) as error:
        print(f"indexnow: could not read the sitemap ({error})", file=sys.stderr)
        return 0

    if not urls:
        print("indexnow: sitemap listed no URLs on this host", file=sys.stderr)
        return 0

    try:
        submit(urls)
    except urllib.error.HTTPError as error:
        # 422 is the interesting one: it means the key or a URL was rejected.
        print(
            f"indexnow: rejected with HTTP {error.code} - {error.read()[:200]!r}",
            file=sys.stderr,
        )
    except (urllib.error.URLError, OSError) as error:
        print(f"indexnow: submission failed ({error})", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
