"""Tests for the release announcement poster.

The announcer talks to two things this suite must never touch: the QGIS plugin
repository and a Discord webhook. Everything here works on strings that were
captured from those services, so the whole file runs offline and no test can
post to a real channel.

The behaviour worth guarding is the part that is hard to notice going wrong: an
announcement that fires for a version nobody approved yet, a role ping on a
patch release, or a message Discord rejects because the changelog section was
longer than an embed can hold.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "announce_release.py"


def _load_module():
    """Import the script by path; `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("announce_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["announce_release"] = module
    spec.loader.exec_module(module)
    return module


announce = _load_module()


# --- what the plugin repository says is live -------------------------------

# Trimmed from a real plugins.xml response. The neighbouring plugin is here on
# purpose: the file lists every approved plugin, so matching must be anchored to
# our plugin_id and not to whichever version attribute appears first.
PLUGINS_XML = """<?xml version="1.0" encoding="utf-8"?>
<plugins>
  <pyqgis_plugin name="Some Other Plugin" version="9.9.9" plugin_id="1234">
    <version>9.9.9</version>
  </pyqgis_plugin>
  <pyqgis_plugin name="QGIS2WebMap by NIKA" version="0.1.3" plugin_id="5922">
    <version>0.1.3</version>
    <file_name>nika_onlymap_exporter.0.1.3.zip</file_name>
  </pyqgis_plugin>
</plugins>
"""


def test_published_version_reads_our_plugin() -> None:
    assert announce.published_version_from_xml(PLUGINS_XML) == "0.1.3"


def test_published_version_ignores_other_plugins() -> None:
    """A version belonging to a different plugin must never be announced."""
    without_us = PLUGINS_XML.replace('plugin_id="5922"', 'plugin_id="6000"')
    assert announce.published_version_from_xml(without_us) is None


def test_published_version_survives_attribute_reordering() -> None:
    """The repository's attribute order is not a contract we control."""
    reordered = (
        '<plugins><pyqgis_plugin plugin_id="5922" version="0.2.0" '
        'name="QGIS2WebMap by NIKA"></pyqgis_plugin></plugins>'
    )
    assert announce.published_version_from_xml(reordered) == "0.2.0"


def test_published_version_from_html_fallback() -> None:
    html = '<a href="/plugins/nika_onlymap_exporter/version/0.1.3/download/">dl</a>'
    assert announce.published_version_from_html(html) == "0.1.3"


def test_published_version_from_html_picks_the_highest() -> None:
    """The page lists every version; the newest is the live one."""
    html = (
        '<a href="/plugins/x/version/0.1.2/download/">a</a>'
        '<a href="/plugins/x/version/0.1.10/download/">b</a>'
        '<a href="/plugins/x/version/0.1.3/download/">c</a>'
    )
    assert announce.published_version_from_html(html) == "0.1.10"


# --- reading the changelog --------------------------------------------------

CHANGELOG = """# Changelog

Preamble prose that is not part of any release.

## [Unreleased]

### Added
- **Not out yet.** Must never be announced.

## [0.1.3] - 2026-08-17

### Added
- **Textured 3D relief.** The runtime replaces the basemap while relief is on.

### Fixed
- **A layer's labels are named as its labels.** It is now "Peaks (labels)".

## [0.1.2] - 2026-08-06

### Fixed
- **Something older.** Prose.
"""


def test_changelog_section_finds_the_version() -> None:
    section = announce.changelog_section(CHANGELOG, "0.1.3")
    assert section is not None
    assert section.date == "2026-08-17"
    assert "Textured 3D relief" in section.body
    assert "### Added" in section.body


def test_changelog_section_stops_at_the_next_version() -> None:
    """Bleeding into the previous release would announce old work as new."""
    section = announce.changelog_section(CHANGELOG, "0.1.3")
    assert section is not None
    assert "Something older" not in section.body
    assert "0.1.2" not in section.body


def test_changelog_section_never_returns_unreleased() -> None:
    section = announce.changelog_section(CHANGELOG, "0.1.3")
    assert section is not None
    assert "Not out yet" not in section.body


def test_changelog_section_missing_version_is_none() -> None:
    """A live version with no changelog entry must skip, not post an empty body."""
    assert announce.changelog_section(CHANGELOG, "0.1.4") is None


def test_changelog_section_tolerates_a_missing_date() -> None:
    text = "## [1.0.0]\n\n### Added\n- **Thing.** Prose.\n"
    section = announce.changelog_section(text, "1.0.0")
    assert section is not None
    assert section.date == ""
    assert "Thing" in section.body


def test_the_real_changelog_parses() -> None:
    """Guards the parser against a future reformat of the actual file."""
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = announce.changelog_section(text, "0.1.3")
    assert section is not None
    assert section.body.strip()


# --- deciding whether to ping ----------------------------------------------


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ("0.1.3", "0.1.4", False),  # patch: posts quietly
        ("0.1.3", "0.2.0", True),  # minor: worth interrupting people for
        ("0.9.1", "1.0.0", True),  # major
        ("0.1.3", "0.1.10", False),  # double-digit patch is still a patch
        ("", "0.1.4", True),  # no history: announce loudly once
    ],
)
def test_should_ping(previous: str, current: str, expected: bool) -> None:
    assert announce.should_ping(previous, current) is expected


def test_is_newer_compares_numerically_not_as_text() -> None:
    """String comparison would rank 0.1.10 below 0.1.9 and skip a release."""
    assert announce.is_newer("0.1.9", "0.1.10") is True
    assert announce.is_newer("0.1.10", "0.1.9") is False
    assert announce.is_newer("0.1.3", "0.1.3") is False


# --- fitting a long changelog into Discord's limits ------------------------


def _bullets(count: int, length: int) -> str:
    return "\n".join(f"- **Item {i}.** {'x' * length}" for i in range(count))


def test_short_body_is_one_chunk() -> None:
    chunks, truncated = announce.chunk_body("- **Small.** Prose.")
    assert chunks == ["- **Small.** Prose."]
    assert truncated is False


def test_long_body_splits_across_embeds() -> None:
    chunks, _ = announce.chunk_body(_bullets(4, 1200))
    assert len(chunks) > 1
    assert all(len(c) <= announce.EMBED_DESCRIPTION_LIMIT for c in chunks)


def test_chunks_split_on_bullet_boundaries() -> None:
    """A cut mid-sentence reads as a bug; a cut between bullets reads as a list."""
    chunks, _ = announce.chunk_body(_bullets(4, 1200))
    for chunk in chunks[1:]:
        assert chunk.lstrip().startswith("- ")


def test_total_across_embeds_respects_the_message_limit() -> None:
    """Discord rejects the whole message when its embeds sum past 6000."""
    chunks, truncated = announce.chunk_body(_bullets(20, 1500))
    assert truncated is True
    assert sum(len(c) for c in chunks) <= announce.EMBED_TOTAL_LIMIT
    assert len(chunks) <= announce.MAX_EMBEDS


def test_truncated_body_says_so_and_links_on() -> None:
    chunks, truncated = announce.chunk_body(_bullets(20, 1500), notice="\n\nMORE")
    assert truncated is True
    assert chunks[-1].endswith("MORE")


def test_a_single_oversized_bullet_is_cut_on_a_word_boundary() -> None:
    """One enormous paragraph has no bullet boundary to cut at; it must still fit."""
    body = "- **Huge.** " + "word " * 3000
    chunks, truncated = announce.chunk_body(body, notice="\n\nMORE")
    assert truncated is True
    assert len(chunks) == 1
    assert len(chunks[0]) <= announce.EMBED_DESCRIPTION_LIMIT
    assert not chunks[0].replace("\n\nMORE", "").endswith("wor")


# --- the payload Discord receives ------------------------------------------


def test_changelog_anchor_matches_githubs_slug() -> None:
    """Verified against the rendered CHANGELOG.md: `#013---2026-08-17`.

    A wrong anchor is a link that still loads the page, just at the top - the
    kind of breakage nobody reports.
    """
    assert announce.changelog_anchor("0.1.3", "2026-08-17") == "013---2026-08-17"
    assert announce.changelog_anchor("1.0.0", "") == "100"


def test_payload_pings_the_role_when_asked() -> None:
    payload = announce.build_payload("0.2.0", "2026-09-01", "- **New.** Prose.", True)
    assert f"<@&{announce.ROLE_ID}>" in payload["content"]
    assert payload["allowed_mentions"]["roles"] == [announce.ROLE_ID]


def test_payload_cannot_ping_anything_but_that_one_role() -> None:
    """`parse: []` is what stops an @everyone in a changelog from firing."""
    payload = announce.build_payload("0.2.0", "2026-09-01", "- **New.** Prose.", True)
    assert payload["allowed_mentions"]["parse"] == []


def test_patch_payload_is_silent() -> None:
    payload = announce.build_payload("0.1.4", "2026-09-01", "- **Fix.** Prose.", False)
    assert "<@&" not in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}


def test_payload_carries_the_version_and_links() -> None:
    payload = announce.build_payload("0.1.4", "2026-09-01", "- **Fix.** Prose.", False)
    assert "0.1.4" in payload["content"]
    first = payload["embeds"][0]
    assert "0.1.4" in first["title"]
    assert first["url"] == announce.PLUGIN_PAGE
    assert "Prose" in first["description"]


def test_payload_embeds_stay_within_discord_limits() -> None:
    payload = announce.build_payload("0.2.0", "2026-09-01", _bullets(20, 1500), True)
    embeds = payload["embeds"]
    assert len(embeds) <= announce.MAX_EMBEDS
    assert all(
        len(e["description"]) <= announce.EMBED_DESCRIPTION_LIMIT for e in embeds
    )
    assert sum(len(e["description"]) for e in embeds) <= announce.EMBED_TOTAL_LIMIT


def test_only_the_first_embed_has_a_title() -> None:
    """Repeating the heading on every embed reads as three separate releases."""
    payload = announce.build_payload("0.2.0", "2026-09-01", _bullets(6, 1200), True)
    embeds = payload["embeds"]
    assert "title" in embeds[0]
    assert all("title" not in e for e in embeds[1:])


# --- the decision the workflow actually makes ------------------------------


def test_decide_announces_a_newly_approved_version() -> None:
    decision = announce.decide("0.1.3", "0.1.4", CHANGELOG.replace("0.1.3", "0.1.4"))
    assert decision.should_post is True
    assert decision.version == "0.1.4"


def test_decide_is_quiet_when_nothing_changed() -> None:
    decision = announce.decide("0.1.3", "0.1.3", CHANGELOG)
    assert decision.should_post is False
    assert "already announced" in decision.reason


def test_decide_ignores_a_version_bump_that_is_not_live_yet() -> None:
    """The whole point of polling: a tag ahead of approval announces nothing."""
    decision = announce.decide("0.1.4", "0.1.3", CHANGELOG)
    assert decision.should_post is False


def test_decide_skips_when_the_changelog_has_no_section() -> None:
    decision = announce.decide("0.1.2", "0.1.3", "# Changelog\n\nNothing here.\n")
    assert decision.should_post is False
    assert "changelog" in decision.reason.lower()
