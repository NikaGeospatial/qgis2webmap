"""Links and pictures in popups, and no markup from the data.

A field whose popup value needs markup - a link, a picture - has that markup
built per feature by the exporter and inserted unescaped by the template, so
these tests hold the line that matters most: whatever a value holds, the only
markup that reaches the page is the exporter's own, and a value is only ever
text, a link with a safe scheme, or a picture's address.

The picture loader is replaced by a fake here (the real one needs Qt, see
`tests/qgis/test_popup_media.py`), so these are the decisions alone.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
from html.parser import HTMLParser
from pathlib import Path

import pytest

from nika_onlymap_exporter.core import popup_media
from nika_onlymap_exporter.core.export_ir import (
    ExportLayer,
    ExportProject,
    FidelityStatus,
    GeometryKind,
    OutputMode,
    PopupAttachment,
    PopupFieldMode,
    PopupFieldSpec,
    PopupSpec,
    SourceKind,
)
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.manifest_builder import build_popup_elements
from nika_onlymap_exporter.core.popup_media import (
    MAX_EXPORT_PICTURE_BYTES,
    LoadedPicture,
    PictureProblem,
    attach_popup_media,
    is_link,
    value_markup,
)
from nika_onlymap_exporter.packaging.popup_pictures import (
    embed_popup_pictures,
    place_popup_pictures,
)

PICTURE = PopupAttachment(picture=True, base_dir="/project")
HOSTILE = (
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "JAVASCRIPT:alert(1)",
    " javascript:alert(document.cookie)",
    "data:text/html,<script>alert(1)</script>",
    "data:image/svg+xml,<svg onload=alert(1)>",
    "file:///etc/passwd",
    'https://example.org/"onmouseover="alert(1)',
    "{{{other}}}",
)
# The only markup the exporter writes, and the only attributes it gives it.
ALLOWED = {
    "a": {"href", "target", "rel"},
    "img": {"class", "src", "alt", "referrerpolicy"},
}


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.elements.append((tag, dict(attrs)))


def elements(markup: str) -> list[tuple[str, dict[str, str | None]]]:
    parser = _Markup()
    parser.feed(markup)
    parser.close()
    return parser.elements


def assert_only_our_markup(markup: str) -> None:
    for tag, attributes in elements(markup):
        assert tag in ALLOWED, markup
        assert set(attributes) <= ALLOWED[tag], markup
        for name in ("href", "src"):
            address = (attributes.get(name) or "").strip().lower()
            assert not address.startswith(("javascript:", "data:", "file:")), markup


def no_pictures(_value: str) -> str | None:
    return None


class TestLinks:
    @pytest.mark.parametrize(
        "value",
        [
            "https://example.org",
            "http://example.org/a?b=1&c=2#d",
            "HTTPS://EXAMPLE.ORG/",
            "mailto:someone@example.org",
            "  https://example.org/padded  ",
        ],
    )
    def test_web_and_email_addresses_are_links(self, value) -> None:
        assert is_link(value)

    @pytest.mark.parametrize(
        "value",
        [
            "javascript:alert(1)",
            "data:text/html,hi",
            "file:///etc/passwd",
            "ftp://example.org/file",
            "https://",
            "https:// example.org",
            "see https://example.org",
            "example.org",
            "https://example.org/\x00",
            None,
            42,
        ],
    )
    def test_nothing_else_is(self, value) -> None:
        assert not is_link(value)

    def test_a_link_opens_in_a_new_tab_and_cannot_reach_back(self) -> None:
        markup = value_markup(
            "https://example.org/a?b=1&c=2", PopupFieldSpec("url"), no_pictures
        )
        [(tag, attributes)] = elements(markup)
        assert tag == "a"
        assert attributes == {
            "href": "https://example.org/a?b=1&c=2",
            "target": "_blank",
            "rel": "noopener noreferrer",
        }

    def test_an_attachment_link_shows_its_file_name_as_qgis_does(self) -> None:
        field = PopupFieldSpec("doc", attachment=PopupAttachment(file_name_label=True))
        markup = value_markup(
            "https://example.org/files/report.pdf", field, no_pictures
        )
        assert markup.endswith(">report.pdf</a>")

    def test_without_that_setting_it_shows_the_whole_address(self) -> None:
        field = PopupFieldSpec("doc", attachment=PopupAttachment())
        markup = value_markup("https://example.org/report.pdf", field, no_pictures)
        assert markup.endswith(">https://example.org/report.pdf</a>")


class TestHostileValues:
    @pytest.mark.parametrize("value", HOSTILE)
    @pytest.mark.parametrize(
        "field",
        [
            PopupFieldSpec("plain"),
            PopupFieldSpec("picture", attachment=PICTURE),
            PopupFieldSpec("doc", attachment=PopupAttachment(file_name_label=True)),
        ],
        ids=["plain", "picture", "attachment"],
    )
    def test_a_value_never_becomes_markup(self, value, field) -> None:
        markup = value_markup(value, field, lambda path: "popup-picture-x.jpg")
        assert_only_our_markup(markup)
        assert "<script" not in markup

    def test_a_script_value_is_shown_as_its_text(self) -> None:
        markup = value_markup(
            "<script>alert(1)</script>", PopupFieldSpec("plain"), no_pictures
        )
        assert markup == "&lt;script&gt;alert(1)&lt;/script&gt;"
        assert elements(markup) == []

    def test_a_quote_cannot_leave_the_address(self) -> None:
        markup = value_markup(
            'https://example.org/"onmouseover="alert(1)',
            PopupFieldSpec("url"),
            no_pictures,
        )
        [(_tag, attributes)] = elements(markup)
        assert set(attributes) == {"href", "target", "rel"}
        assert attributes["href"] == 'https://example.org/"onmouseover="alert(1)'

    def test_a_triple_brace_value_stays_text(self) -> None:
        """The runtime fills the template in one pass, so a value is never
        read as a placeholder - and here it is escaped text either way."""
        assert value_markup("{{{x}}}", PopupFieldSpec("plain"), no_pictures) == (
            "{{{x}}}"
        )


class TestPictureValues:
    def test_a_carried_picture_is_an_image_named_by_the_carrier(self) -> None:
        markup = value_markup(
            "photos/a.jpg",
            PopupFieldSpec("photo", attachment=PICTURE),
            lambda path: "popup-picture-0123456789abcdef.jpg",
        )
        [(tag, attributes)] = elements(markup)
        assert tag == "img"
        assert attributes["src"] == "popup-picture-0123456789abcdef.jpg"
        assert attributes["alt"] == "a.jpg"

    def test_a_web_picture_is_loaded_live(self) -> None:
        markup = value_markup(
            "https://example.org/pics/b.png?size=2",
            PopupFieldSpec("photo", attachment=PICTURE),
            no_pictures,
        )
        [(tag, attributes)] = elements(markup)
        assert tag == "img"
        assert attributes["src"] == "https://example.org/pics/b.png?size=2"
        assert attributes["referrerpolicy"] == "no-referrer"

    def test_a_picture_not_carried_shows_its_path(self) -> None:
        markup = value_markup(
            "photos/missing.jpg",
            PopupFieldSpec("photo", attachment=PICTURE),
            no_pictures,
        )
        assert markup == "photos/missing.jpg"

    def test_an_empty_value_is_empty_so_with_data_rows_hide(self) -> None:
        for value in (None, "", "   "):
            assert (
                value_markup(
                    value, PopupFieldSpec("x", attachment=PICTURE), no_pictures
                )
                == ""
            )

    def test_a_relative_path_resolves_where_qgis_resolves_it(self) -> None:
        field = PopupFieldSpec("p", attachment=PopupAttachment(base_dir="/project"))
        assert popup_media.local_path("a/b.jpg", field) == Path("/project/a/b.jpg")
        assert popup_media.local_path("/abs/c.jpg", field) == Path("/abs/c.jpg")
        unrooted = PopupFieldSpec("p", attachment=PopupAttachment(base_dir=None))
        assert popup_media.local_path("a/b.jpg", unrooted) is None
        assert popup_media.local_path("https://x.org/a.jpg", field) is None


def _layer(fields, rows, name="sites") -> ExportLayer:
    return ExportLayer(
        layer_id=f"{name}-id",
        name=name,
        geometry_kind=GeometryKind.POINT,
        source_kind=SourceKind.MEMORY,
        popup=PopupSpec(fields=tuple(fields)),
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [0, 0]},
                    "properties": row,
                }
                for row in rows
            ],
        },
    )


class FakeLoader:
    """Answers for the paths it knows; records every path it is asked for."""

    def __init__(self, answers) -> None:
        self.answers = answers
        self.asked: list[Path] = []

    def __call__(self, path: Path):
        self.asked.append(path)
        return self.answers.get(path, PictureProblem.MISSING)


def _properties(layer: ExportLayer) -> list[dict]:
    return [dict(f["properties"]) for f in layer.geojson["features"]]


class TestAttachPopupMedia:
    def test_a_field_with_nothing_to_mark_up_is_left_alone(self) -> None:
        layer = _layer([PopupFieldSpec("note")], [{"note": "plain words"}])
        [result] = attach_popup_media([layer], FidelityReportBuilder(), FakeLoader({}))
        assert result is layer

    def test_links_get_a_markup_property_and_the_value_stays(self) -> None:
        layer = _layer(
            [PopupFieldSpec("url")], [{"url": "https://a.org"}, {"url": "x"}]
        )
        report = FidelityReportBuilder()
        [result] = attach_popup_media([layer], report, FakeLoader({}))
        [field] = result.popup.fields
        assert field.markup and field.name == "url_html"
        assert field.display_name == "url"
        first, second = _properties(result)
        assert first["url"] == "https://a.org"
        assert first["url_html"].startswith('<a href="https://a.org"')
        assert second["url_html"] == "x"
        [row] = report.items
        assert row.status is FidelityStatus.PRESERVED
        assert "internet connection" in row.detail

    def test_the_markup_property_never_overwrites_a_field(self) -> None:
        layer = _layer(
            [PopupFieldSpec("url"), PopupFieldSpec("url_html")],
            [{"url": "https://a.org", "url_html": "mine"}],
        )
        [result] = attach_popup_media([layer], FidelityReportBuilder(), FakeLoader({}))
        [properties] = _properties(result)
        assert properties["url_html"] == "mine"
        assert properties["url_html_"].startswith("<a ")

    def test_a_picture_is_carried_once_under_its_hash(self) -> None:
        picture = LoadedPicture(b"jpeg-bytes", "image/jpeg", "jpg")
        loader = FakeLoader({Path("/project/a.jpg"): picture})
        layer = _layer(
            [PopupFieldSpec("photo", attachment=PICTURE)],
            [{"photo": "a.jpg"}, {"photo": "/project/a.jpg"}],
        )
        report = FidelityReportBuilder()
        [result] = attach_popup_media([layer], report, loader)
        [carried] = result.popup_pictures
        assert carried.data == b"jpeg-bytes"
        assert carried.name.startswith("popup-picture-") and carried.name.endswith(
            ".jpg"
        )
        assert all(carried.name in p["photo_html"] for p in _properties(result))
        assert loader.asked == [Path("/project/a.jpg")]
        assert [r.status for r in report.items] == [FidelityStatus.PRESERVED]

    def test_a_hidden_field_and_a_layer_without_popups_read_no_file(self) -> None:
        loader = FakeLoader({})
        hidden = _layer(
            [PopupFieldSpec("photo", attachment=PICTURE, mode=PopupFieldMode.HIDDEN)],
            [{"photo": "a.jpg"}],
        )
        off = _layer(
            [PopupFieldSpec("photo", attachment=PICTURE)], [{"photo": "a.jpg"}], "off"
        )
        off = dataclasses.replace(off, popup=PopupSpec(enabled=False))
        attach_popup_media([hidden, off], FidelityReportBuilder(), loader)
        assert loader.asked == []

    @pytest.mark.parametrize(
        ("problem", "words"),
        [
            (PictureProblem.MISSING, "could not be found"),
            (PictureProblem.UNREADABLE, "is not a picture the map can show"),
            (PictureProblem.TOO_LARGE, "even when made smaller"),
        ],
    )
    def test_a_picture_left_out_shows_its_path_and_is_reported(
        self, problem, words
    ) -> None:
        loader = FakeLoader({Path("/project/a.jpg"): problem})
        layer = _layer(
            [PopupFieldSpec("photo", attachment=PICTURE)], [{"photo": "a.jpg"}]
        )
        report = FidelityReportBuilder()
        [result] = attach_popup_media([layer], report, loader)
        assert _properties(result)[0]["photo_html"] == "a.jpg"
        assert result.popup_pictures == ()
        [row] = report.items
        assert row.status is FidelityStatus.UNSUPPORTED
        assert words in row.detail and "'a.jpg'" in row.detail

    def test_the_maps_picture_budget_leaves_out_the_last_layers(self) -> None:
        big = MAX_EXPORT_PICTURE_BYTES // 2 + 1
        loader = FakeLoader(
            {
                Path("/project/one.jpg"): LoadedPicture(
                    b"1" * big, "image/jpeg", "jpg"
                ),
                Path("/project/two.jpg"): LoadedPicture(
                    b"2" * big, "image/jpeg", "jpg"
                ),
            }
        )
        first = _layer(
            [PopupFieldSpec("photo", attachment=PICTURE)],
            [{"photo": "one.jpg"}],
            "first",
        )
        second = _layer(
            [PopupFieldSpec("photo", attachment=PICTURE)],
            [{"photo": "two.jpg"}],
            "second",
        )
        report = FidelityReportBuilder()
        kept, dropped = attach_popup_media([first, second], report, loader)
        assert len(kept.popup_pictures) == 1
        assert dropped.popup_pictures == ()
        assert _properties(dropped)[0]["photo_html"] == "two.jpg"
        assert any(
            r.status is FidelityStatus.UNSUPPORTED and "left out" in r.detail
            for r in report.items
        )

    def test_a_downscaled_picture_is_reported_as_approximated(self) -> None:
        loader = FakeLoader(
            {
                Path("/project/a.jpg"): LoadedPicture(
                    b"x", "image/jpeg", "jpg", reduced=True
                )
            }
        )
        layer = _layer(
            [PopupFieldSpec("photo", attachment=PICTURE)], [{"photo": "a.jpg"}]
        )
        report = FidelityReportBuilder()
        attach_popup_media([layer], report, loader)
        assert [r.status for r in report.items] == [
            FidelityStatus.PRESERVED,
            FidelityStatus.APPROXIMATED,
        ]

    def test_web_pictures_are_reported_as_needing_a_connection(self) -> None:
        layer = _layer(
            [PopupFieldSpec("photo", attachment=PICTURE)],
            [{"photo": "https://example.org/a.jpg"}],
        )
        report = FidelityReportBuilder()
        [result] = attach_popup_media([layer], report, FakeLoader({}))
        assert result.popup_pictures == ()
        [row] = report.items
        assert row.status is FidelityStatus.APPROXIMATED
        assert "internet connection" in row.detail

    def test_the_template_inserts_markup_and_only_markup_unescaped(self) -> None:
        layer = _layer(
            [PopupFieldSpec("url"), PopupFieldSpec("note")],
            [{"url": "https://a.org", "note": "n"}],
        )
        [result] = attach_popup_media([layer], FidelityReportBuilder(), FakeLoader({}))
        markup = build_popup_elements(result)
        assert "{{{url_html}}}" in markup
        assert "{{note}}" in markup and "{{{note}}}" not in markup


class TestCarryingPictures:
    def _project(self) -> ExportProject:
        loader = FakeLoader(
            {
                Path("/project/a.jpg"): LoadedPicture(
                    b"\xff\xd8jpeg", "image/jpeg", "jpg"
                )
            }
        )
        layer = _layer(
            [PopupFieldSpec("photo", attachment=PICTURE), PopupFieldSpec("note")],
            [{"photo": "a.jpg", "note": 'src="popup-picture-0000000000000000.jpg"'}],
        )
        [layer] = attach_popup_media([layer], FidelityReportBuilder(), loader)
        return ExportProject(title="t", layers=(layer,))

    def test_a_single_file_carries_the_picture_inside(self) -> None:
        project = embed_popup_pictures(self._project())
        [properties] = _properties(project.layers[0])
        assert 'src="data:image/jpeg;base64,/9hqcGVn"' in properties["photo_html"]

    def test_only_markup_is_rewritten_never_a_value(self) -> None:
        project = embed_popup_pictures(self._project())
        [properties] = _properties(project.layers[0])
        assert properties["note"] == 'src="popup-picture-0000000000000000.jpg"'

    @pytest.mark.parametrize("mode", [OutputMode.FOLDER, OutputMode.SHARE_ZIP])
    def test_a_folder_carries_it_beside_the_page(self, mode, tmp_path) -> None:
        project = self._project()
        placed, files = place_popup_pictures(project, tmp_path, mode)
        [path] = files
        assert path.parent == tmp_path and path.read_bytes() == b"\xff\xd8jpeg"
        assert path.name in _properties(placed.layers[0])[0]["photo_html"]

    def test_a_single_file_writes_no_file(self, tmp_path) -> None:
        _placed, files = place_popup_pictures(
            self._project(), tmp_path, OutputMode.STANDALONE_HTML
        )
        assert files == () and list(tmp_path.iterdir()) == []
