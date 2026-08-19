#!/usr/bin/env python3
"""Announce a new plugin release to Discord, once it is actually live.

    python scripts/announce_release.py [--dry-run] [--force]

Asks plugins.qgis.org which version of the plugin is approved and downloadable
right now, compares that against the last version we announced, and posts the
matching CHANGELOG.md section to a Discord webhook when they differ.

**The trigger is approval, not a git tag.** A tag can be pushed early, or
pushed and then found broken; the plugin repository also holds new versions in
a review queue for days. Announcing on the tag would tell people a release is
out while the download still serves the old zip. Asking the repository what it
is serving is the only signal that cannot be wrong, so that is the one used.

The webhook URL is read from the `DISCORD_WEBHOOK_URL` environment variable and
from nowhere else - no dotenv file, no committed default. In CI it comes from
the repository secret of that name. That is deliberate: a webhook living in one
person's local file stops working the day they leave, and nothing in the
repository would say why.

`--dry-run` prints the exact payload and posts nothing; it needs no webhook, so
it is the safe way to see what a release would say.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# The last version this script posted about. Committed rather than cached: the
# Actions cache is evicted after a week of disuse, and a forgotten state file
# would re-announce a release people already read. In git it is also an audit
# trail of what was announced and when.
STATE_FILE = REPO_ROOT / ".github" / "last-announced-version"

# Our entry in the repository's index. The numeric id is what the listing is
# keyed on; the name is a display string and has been edited before.
PLUGIN_ID = "5922"
PLUGIN_SLUG = "nika_onlymap_exporter"
PLUGIN_PAGE = f"https://plugins.qgis.org/plugins/{PLUGIN_SLUG}/"

# The index is served per QGIS version, and only lists plugins compatible with
# the one asked for. 3.44 is the LTR the plugin declares and is tested against
# (see tests/unit/test_metadata.py); asking for anything older would return an
# index our plugin is correctly absent from.
PLUGINS_XML = "https://plugins.qgis.org/plugins/plugins.xml?qgis=3.44"

DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"

# The Discord role to ping. Pinging is a decision about interrupting people, so
# it is spelled out here rather than passed in and forgotten.
ROLE_ID = "1539538256297992212"

# Discord's documented caps. A message that breaches either is rejected whole,
# which would look exactly like a broken webhook.
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_TOTAL_LIMIT = 6000
MAX_EMBEDS = 4

# The index is ~8 MB and this runs on a schedule, so it can afford to wait.
TIMEOUT_SECONDS = 60

USER_AGENT = (
    "qgis2webmap-release-announcer (+https://github.com/NikaGeospatial/qgis2webmap)"
)

TRUNCATION_NOTICE = "\n\n[Read the rest of this release's notes on GitHub]({url})"

CHANGELOG_URL = (
    "https://github.com/NikaGeospatial/qgis2webmap/blob/main/CHANGELOG.md#{anchor}"
)


class Section(NamedTuple):
    """One release's changelog entry."""

    version: str
    date: str
    body: str


class Decision(NamedTuple):
    """Whether to post, and why not when we are not."""

    should_post: bool
    reason: str
    version: str = ""
    section: Section | None = None
    ping: bool = False


# --- versions ---------------------------------------------------------------


def parse_version(version: str) -> tuple:
    """A dotted version as comparable integers.

    Text comparison ranks `0.1.10` below `0.1.9` and would silently skip a
    release, so nothing here compares versions as strings.
    """
    core = re.split(r"[-+]", version.strip(), maxsplit=1)[0]
    parts = []
    for piece in core.split("."):
        parts.append(int(piece) if piece.isdigit() else 0)
    return tuple(parts)


def is_newer(previous: str, current: str) -> bool:
    if not current:
        return False
    if not previous:
        return True
    return parse_version(current) > parse_version(previous)


def should_ping(previous: str, current: str) -> bool:
    """Ping the role for a minor or major bump, stay silent for a patch.

    A patch release is usually a fix nobody was waiting on. Pinging a role for
    each one teaches people to mute the role, which costs us the announcements
    that do matter. With no recorded history we ping: the first announcement is
    either a real release or a misconfiguration, and both want eyes on them.
    """
    if not previous:
        return True
    old = parse_version(previous)
    new = parse_version(current)
    # Pad so 1.0 and 1.0.0 compare on the same footing.
    old = old + (0,) * (3 - len(old)) if len(old) < 3 else old
    new = new + (0,) * (3 - len(new)) if len(new) < 3 else new
    return new[:2] > old[:2]


# --- what the plugin repository is serving ---------------------------------


def published_version_from_xml(xml_text: str) -> str | None:
    """The live version, read off our entry in the repository index.

    Matched on `plugin_id`, and the attributes are read out of the one opening
    tag that carries it. The index lists every approved plugin, so a regex that
    merely found the first `version=` would announce someone else's release.
    """
    for tag in re.finditer(r"<pyqgis_plugin\b([^>]*)>", xml_text):
        attrs = tag.group(1)
        if re.search(rf'plugin_id="{PLUGIN_ID}"', attrs) is None:
            continue
        version = re.search(r'version="([^"]+)"', attrs)
        if version:
            return version.group(1).strip()
    return None


def published_version_from_html(html: str) -> str | None:
    """Fallback: the highest version offered for download on the plugin page.

    Used only when the index cannot be fetched. The page lists every version
    ever approved, so this takes the highest rather than the first - the table's
    ordering is presentation and not something we are promised.
    """
    found = re.findall(r"version/(\d+(?:\.\d+)*)/download", html)
    if not found:
        return None
    return max(found, key=parse_version)


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def published_version() -> str | None:
    """Ask the repository what is live, preferring the index over the page."""
    try:
        return published_version_from_xml(fetch(PLUGINS_XML))
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"note: plugin index unreachable ({error}); trying the plugin page")
    try:
        return published_version_from_html(fetch(PLUGIN_PAGE))
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"error: plugin page unreachable too ({error})")
        return None


# --- the changelog ----------------------------------------------------------

_HEADING = re.compile(r"^## \[([^\]]+)\](?:\s*-\s*(\S+))?\s*$", re.M)


def changelog_section(text: str, version: str) -> Section | None:
    """The changelog entry for one version, without its heading.

    Returns None when the version has no section. That is a real situation -
    the repository can approve a version whose notes were never written - and
    posting an empty announcement is worse than posting nothing, so the caller
    treats it as a skip rather than an error.
    """
    headings = list(_HEADING.finditer(text))
    for index, heading in enumerate(headings):
        if heading.group(1).strip() != version:
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        return Section(
            version=version,
            date=(heading.group(2) or "").strip(),
            body=text[start:end].strip(),
        )
    return None


# --- fitting it into a Discord message -------------------------------------


def _split_into_blocks(body: str) -> list:
    """The body as bullets and headings, each kept whole.

    Chunking happens between these, never inside one, so a split never lands
    mid-sentence.
    """
    blocks: list = []
    current: list = []
    for line in body.splitlines():
        starts_block = line.startswith("- ") or line.startswith("#")
        if starts_block and current:
            blocks.append("\n".join(current).strip())
            current = []
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _cut_on_a_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    spaced = cut.rsplit(" ", 1)[0]
    return (spaced if spaced else cut).rstrip()


def chunk_body(
    body: str,
    notice: str = "",
    embed_limit: int = EMBED_DESCRIPTION_LIMIT,
    total_limit: int = EMBED_TOTAL_LIMIT,
    max_embeds: int = MAX_EMBEDS,
) -> tuple:
    """Split a changelog section into embed descriptions that Discord accepts.

    Our entries are long prose - a single release has run past 4000 characters -
    and Discord caps one description at 4096 and a message's embeds at 6000
    together. Both caps reject the entire message, so this splits on bullet
    boundaries up to those limits and, when the section still does not fit,
    stops at the last whole bullet and appends `notice` pointing at the full
    text. Returns the chunks and whether anything was dropped.
    """
    body = body.strip()
    if not body:
        return [], False
    if len(body) <= min(embed_limit, total_limit):
        return [body], False

    budget = total_limit - len(notice)
    blocks = _split_into_blocks(body)
    chunks: list = []
    current = ""
    used = 0
    truncated = False

    for block in blocks:
        separator = "\n\n" if current else ""
        addition = len(separator) + len(block)
        if len(current) + addition <= embed_limit and used + addition <= budget:
            current += separator + block
            used += addition
            continue
        # It does not fit here. Start a fresh embed if there is room for one.
        if current:
            chunks.append(current)
            current = ""
        if len(chunks) >= max_embeds or used + len(block) > budget:
            truncated = True
            break
        if len(block) > embed_limit:
            truncated = True
            break
        current = block
        used += len(block)

    if current:
        chunks.append(current)

    if not chunks:
        # One paragraph longer than an embed, with no boundary to cut at.
        truncated = True
        chunks = [_cut_on_a_word(body, embed_limit - len(notice))]

    if truncated and chunks:
        chunks[-1] = chunks[-1] + notice

    return chunks, truncated


def changelog_anchor(version: str, date: str) -> str:
    """GitHub's slug for a `## [0.1.3] - 2026-08-17` heading.

    GitHub lowercases the heading, drops everything that is not alphanumeric,
    a space or a hyphen, then turns spaces into hyphens - so the brackets and
    the dots vanish and ` - ` becomes `---`. Verified against the rendered file:
    that heading anchors at `#013---2026-08-17`. Guessing `#013` produces a link
    that loads the page at the top, which is a broken link that still looks like
    it worked.
    """
    slug = version.replace(".", "")
    return f"{slug}---{date}" if date else slug


def build_payload(version: str, date: str, body: str, ping: bool) -> dict:
    """The JSON Discord receives.

    `allowed_mentions` is the security-relevant part: `parse: []` means the
    message can only ping what is listed explicitly, so an `@everyone` that ends
    up in a changelog line stays inert text.
    """
    notice = TRUNCATION_NOTICE.format(
        url=CHANGELOG_URL.format(anchor=changelog_anchor(version, date))
    )
    chunks, _ = chunk_body(body, notice=notice)

    headline = (
        f"**QGIS2WebMap by NIKA {version}** is live on the QGIS plugin repository."
    )
    content = f"<@&{ROLE_ID}> {headline}" if ping else headline

    embeds: list = []
    for index, chunk in enumerate(chunks):
        embed: dict = {"description": chunk}
        if index == 0:
            embed["title"] = f"v{version}" + (f" - {date}" if date else "")
            embed["url"] = PLUGIN_PAGE
        embeds.append(embed)

    return {
        "content": content,
        "embeds": embeds,
        "allowed_mentions": (
            {"parse": [], "roles": [ROLE_ID]} if ping else {"parse": []}
        ),
    }


# --- the decision -----------------------------------------------------------


def decide(announced: str, live: str | None, changelog_text: str) -> Decision:
    """Whether this run should post, given the three facts it has."""
    if not live:
        return Decision(False, "could not read the live version from the repository")
    if live == announced:
        return Decision(False, f"{live} was already announced")
    if not is_newer(announced, live):
        return Decision(
            False,
            f"live version {live} is not newer than the announced {announced}"
            " - a version bump is probably still awaiting approval",
        )
    section = changelog_section(changelog_text, live)
    if section is None:
        return Decision(False, f"no changelog section for {live}; nothing to announce")
    return Decision(
        should_post=True,
        reason=f"{live} is live and was not announced",
        version=live,
        section=section,
        ping=should_ping(announced, live),
    )


def post(webhook: str, payload: dict) -> bool:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as error:
        # The body carries Discord's reason; the status alone is rarely enough.
        detail = error.read().decode("utf-8", errors="replace")[:500]
        print(f"error: Discord rejected the message ({error.code}): {detail}")
        return False
    except (urllib.error.URLError, OSError) as error:
        print(f"error: could not reach Discord ({error})")
        return False


def read_state() -> str:
    if not STATE_FILE.is_file():
        return ""
    return STATE_FILE.read_text(encoding="utf-8").strip()


def write_state(version: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(version + "\n", encoding="utf-8")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Announce a newly approved plugin release to Discord."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the payload and post nothing (needs no webhook)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="post even if this version was already announced",
    )
    args = parser.parse_args(argv)

    announced = read_state()
    live = published_version()
    print(f"announced: {announced or '(none)'}    live: {live or '(unknown)'}")

    changelog_text = CHANGELOG.read_text(encoding="utf-8")
    decision = decide(announced, live, changelog_text)

    if not decision.should_post and args.force and live:
        section = changelog_section(changelog_text, live)
        if section is None:
            print(f"nothing to do: no changelog section for {live}")
            return 0
        decision = Decision(True, "forced", live, section, should_ping(announced, live))

    if not decision.should_post:
        print(f"nothing to do: {decision.reason}")
        return 0

    assert decision.section is not None
    payload = build_payload(
        decision.version,
        decision.section.date,
        decision.section.body,
        decision.ping,
    )

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        print(f"\ndry run: would announce {decision.version}, ping={decision.ping}")
        return 0

    webhook = os.environ.get(DISCORD_WEBHOOK_ENV, "").strip()
    if not webhook:
        print(
            f"error: {DISCORD_WEBHOOK_ENV} is not set. In CI it comes from the"
            f" repository secret of that name (Settings -> Secrets and variables"
            f" -> Actions). Locally, use --dry-run instead."
        )
        return 1

    if not post(webhook, payload):
        return 1

    write_state(decision.version)
    print(f"announced {decision.version} (ping={decision.ping})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
