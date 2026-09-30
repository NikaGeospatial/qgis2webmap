"""The research scrubbing rules, one by one.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nika_onlymap_exporter.research.scrub import (
    MAX_OTHER_LENGTH,
    MAX_TEXT_LENGTH,
    scrub,
    scrub_other,
)


class TestPaths:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("/home/alice/projects/roads.shp", "roads"),
            ("C:\\Users\\bob\\Desktop\\parcels.gpkg", "parcels"),
            ("\\\\fileserver\\gis\\dem.tif", "dem"),
            ("~/maps/survey.geojson", "survey"),
            ("./data/wells.csv", "wells"),
            ("data/roads.shp", "roads"),
            ("Layer from /srv/share/rivers.shp here", "Layer from rivers here"),
            ("a/b/c", "c"),
        ],
    )
    def test_keeps_the_last_component_without_its_extension(
        self, text: str, expected: str
    ) -> None:
        assert scrub(text) == expected

    @pytest.mark.parametrize("text", ["Roads / Rail", "A \\ B", "x // y"])
    def test_separators_on_their_own_are_punctuation(self, text: str) -> None:
        assert scrub(text) == text

    def test_any_token_with_a_separator_is_a_path(self) -> None:
        # The server's reading, mirrored: stricter than guessing which slashes
        # are prose, and it can only ever remove text, never keep more.
        assert scrub("Roads 2020/2021") == "Roads 2021"

    def test_an_anchored_path_with_spaces_keeps_no_folder(self) -> None:
        cleaned = scrub("C:\\Users\\Jane Smith\\Maps\\roads.shp")
        assert cleaned == "roads"

    def test_an_email_inside_a_path_is_not_half_kept(self) -> None:
        assert scrub("x/jane@corp.com") == "[email]"

    def test_a_qgis_source_string_loses_its_folders(self) -> None:
        # The provider options after `|` are kept, as the server keeps them;
        # the folders, which are what identify someone, are not.
        assert scrub("/home/alice/x.gpkg|layername=roads") == "x.gpkg|layername=roads"

    def test_no_directory_name_survives(self) -> None:
        cleaned = scrub("/home/alice.smith/Clients/AcmeCorp/site.shp")
        assert "alice" not in cleaned
        assert "Acme" not in cleaned


class TestUrls:
    @pytest.mark.parametrize(
        "text",
        [
            "https://example.com/tiles/{z}/{x}/{y}.png",
            "http://intranet.local/wms?SERVICE=WMS&token=abc",
            "file:///C:/data/secret.tif",
            "www.example.org/page",
            "ftp://files.example.com/a.zip",
        ],
    )
    def test_urls_are_removed_entirely(self, text: str) -> None:
        cleaned = scrub(f"Basemap {text} copy")
        assert cleaned == "Basemap copy"

    def test_url_is_not_shortened_to_its_last_segment(self) -> None:
        assert "token" not in scrub("https://x.com/a/b?token=secret123")
        assert scrub("https://x.com/a/secret.png") == ""


class TestEmails:
    def test_emails_are_replaced(self) -> None:
        assert scrub("Contact jane.doe+maps@agency.gov.uk") == "Contact [email]"


class TestNumbers:
    @pytest.mark.parametrize(
        "text",
        ["+65 9123 4567", "(020) 7946-0958", "020.7946.0958", "+1 (555) 123-4567"],
    )
    def test_phone_like_runs_become_number(self, text: str) -> None:
        assert scrub(f"Call {text} now") == "Call [number] now"

    def test_six_digit_runs_become_number(self) -> None:
        assert scrub("Parcel 123456") == "Parcel [number]"
        assert scrub("Job 9876543210") == "Job [number]"

    def test_short_numbers_survive(self) -> None:
        assert scrub("Zone 12345") == "Zone 12345"
        assert scrub("Survey 2024") == "Survey 2024"
        assert scrub("EPSG 3.44.1") == "EPSG 3.44.1"


class TestControlAndLength:
    def test_control_characters_are_removed(self) -> None:
        assert scrub("Ro\x00ads\x07") == "Roads"

    def test_bidi_overrides_are_removed(self) -> None:
        assert scrub("abc\u202edef") == "abcdef"

    def test_whitespace_is_collapsed_and_trimmed(self) -> None:
        assert scrub("  Main\t\troads \n 2024  ") == "Main roads 2024"

    def test_a_control_character_cannot_split_a_number_past_its_rule(self) -> None:
        assert scrub("12\x003456") == "[number]"

    def test_cut_to_sixty(self) -> None:
        assert len(scrub("x" * 200)) == MAX_TEXT_LENGTH

    def test_use_case_other_gets_140(self) -> None:
        assert len(scrub_other("y" * 500)) == MAX_OTHER_LENGTH

    def test_none_and_empty(self) -> None:
        assert scrub(None) == ""
        assert scrub("   ") == ""


VECTORS = json.loads(
    (Path(__file__).with_name("research_scrub_vectors.json")).read_text(
        encoding="utf-8"
    )
)


class TestSharedVectorsWithTheServer:
    """The server's own scrub test cases: both sides must agree on every one."""

    @pytest.mark.parametrize(
        "case", VECTORS["cases"], ids=lambda case: repr(case["input"][:30])
    )
    def test_same_output_as_the_server(self, case: dict[str, object]) -> None:
        text = case["input"]
        max_length = case["max_length"]
        assert isinstance(text, str) and isinstance(max_length, int)
        assert scrub(text, max_length) == case["expected"]

    @pytest.mark.parametrize("text", VECTORS["idempotent"])
    def test_idempotent_on_the_server_cases(self, text: str) -> None:
        assert scrub(scrub(text)) == scrub(text)


class TestIdempotence:
    @pytest.mark.parametrize(
        "text",
        [
            "/home/alice/roads.shp",
            "Call +65 9123 4567 or mail a@b.com at https://x.com/y",
            "x" * 200,
            # Not a path until the cut leaves a short extension on the end.
            "x" * 50 + "/b.abcdefghijk",
            "Parcel 123456 in /srv/share/p.gpkg",
            "  spaced\ttext\n",
            "[number] and [email] already",
        ],
    )
    def test_scrubbing_twice_changes_nothing(self, text: str) -> None:
        once = scrub(text)
        assert scrub(once) == once
        assert scrub_other(scrub_other(text)) == scrub_other(text)
