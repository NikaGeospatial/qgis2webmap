"""Popup fields the web template cannot reference are renamed, not leaked.

The runtime's popup interpolator substitutes `{{name}}` only for `\\w+` names
(verified against the pinned bundle's substitution regex). A field called
"Last Known Eruption" therefore left the literal `{{Last Known Eruption}}` in
every popup - discovered on the Indonesia volcano demo, whose GeoPackage uses
spaced and parenthesised column names throughout.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from nika_onlymap_exporter.core.export_ir import PopupFieldSpec, PopupSpec
from nika_onlymap_exporter.core.fidelity_report import FidelityReportBuilder
from nika_onlymap_exporter.core.popup_translator import rename_untemplatable_fields


def collection(properties: dict) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": None, "properties": properties}],
    }


class TestRenameUntemplatableFields:
    def test_spaced_and_parenthesised_names_are_renamed(self) -> None:
        popup = PopupSpec(
            fields=(
                PopupFieldSpec(name="Name"),
                PopupFieldSpec(name="Last Known Eruption"),
                PopupFieldSpec(name="Elevation (Meters)"),
            )
        )
        data = collection(
            {
                "Name": "Slamet",
                "Last Known Eruption": "2019 CE",
                "Elevation (Meters)": 3428,
            }
        )
        report = FidelityReportBuilder()
        data, popup = rename_untemplatable_fields(
            data, popup, set(), report, "Popup fields of 'v'", "layer-1"
        )
        names = [f.name for f in popup.fields]
        assert names == ["Name", "Last_Known_Eruption", "Elevation_Meters"]
        properties = data["features"][0]["properties"]
        assert properties["Last_Known_Eruption"] == "2019 CE"
        assert properties["Elevation_Meters"] == 3428
        assert "Last Known Eruption" not in properties

    def test_display_labels_keep_the_original_spelling(self) -> None:
        popup = PopupSpec(fields=(PopupFieldSpec(name="Last Known Eruption"),))
        _, popup = rename_untemplatable_fields(
            collection({"Last Known Eruption": "1963"}),
            popup,
            set(),
            FidelityReportBuilder(),
            "s",
            "l",
        )
        assert popup.fields[0].display_name == "Last Known Eruption"

    def test_an_explicit_alias_survives_the_rename(self) -> None:
        popup = PopupSpec(
            fields=(PopupFieldSpec(name="Elevation (Meters)", alias="Elevation (m)"),)
        )
        _, popup = rename_untemplatable_fields(
            collection({"Elevation (Meters)": 1}),
            popup,
            set(),
            FidelityReportBuilder(),
            "s",
            "l",
        )
        assert popup.fields[0].display_name == "Elevation (m)"

    def test_safe_names_and_data_are_untouched(self) -> None:
        popup = PopupSpec(fields=(PopupFieldSpec(name="eruptions_50y"),))
        data = collection({"eruptions_50y": 4})
        out, popup_out = rename_untemplatable_fields(
            data, popup, set(), FidelityReportBuilder(), "s", "l"
        )
        assert out == data
        assert popup_out == popup

    def test_protected_drawing_fields_are_never_renamed(self) -> None:
        """Accessors reference drawing fields by their real name."""
        popup = PopupSpec(fields=(PopupFieldSpec(name="max vei"),))
        report = FidelityReportBuilder()
        out, popup_out = rename_untemplatable_fields(
            collection({"max vei": 4}), popup, {"max vei"}, report, "s", "l"
        )
        assert popup_out.fields[0].name == "max vei"
        assert out["features"][0]["properties"] == {"max vei": 4}

    def test_collisions_get_a_distinct_name(self) -> None:
        popup = PopupSpec(
            fields=(
                PopupFieldSpec(name="max_vei"),
                PopupFieldSpec(name="max vei"),
            )
        )
        _, popup_out = rename_untemplatable_fields(
            collection({"max_vei": 1, "max vei": 2}),
            popup,
            set(),
            FidelityReportBuilder(),
            "s",
            "l",
        )
        assert [f.name for f in popup_out.fields] == ["max_vei", "max_vei_"]

    def test_non_ascii_names_are_renamed(self) -> None:
        """JavaScript's `\\w` is ASCII-only, so 'Höhe' is untemplatable even
        though Python's Unicode-aware `\\w+` matches it."""
        popup = PopupSpec(fields=(PopupFieldSpec(name="Höhe"),))
        data, popup = rename_untemplatable_fields(
            collection({"Höhe": 3428}),
            popup,
            set(),
            FidelityReportBuilder(),
            "s",
            "l",
        )
        assert popup.fields[0].name == "H_he"
        assert data["features"][0]["properties"] == {"H_he": 3428}

    def test_a_rename_never_collides_with_a_protected_drawing_field(self) -> None:
        """A drawing-only field survives in the properties without appearing
        among the popup fields; a rename landing on its name would clobber it."""
        popup = PopupSpec(fields=(PopupFieldSpec(name="max vei"),))
        data, popup = rename_untemplatable_fields(
            collection({"max_vei": 1, "max vei": 2}),
            popup,
            {"max_vei"},
            FidelityReportBuilder(),
            "s",
            "l",
        )
        assert popup.fields[0].name == "max_vei_"
        assert data["features"][0]["properties"] == {"max_vei": 1, "max_vei_": 2}

    def test_a_protected_untemplatable_field_is_reported_as_leaking(self) -> None:
        """It cannot be renamed - accessors use the real name - so the popup
        shows the raw placeholder, and the report must say so."""
        report = FidelityReportBuilder()
        rename_untemplatable_fields(
            collection({"max vei": 4}),
            PopupSpec(fields=(PopupFieldSpec(name="max vei"),)),
            {"max vei"},
            report,
            "s",
            "l",
        )
        assert any("raw placeholder" in item.detail for item in report.items)

    def test_the_rename_is_reported(self) -> None:
        report = FidelityReportBuilder()
        rename_untemplatable_fields(
            collection({"a b": 1}),
            PopupSpec(fields=(PopupFieldSpec(name="a b"),)),
            set(),
            report,
            "Popup fields of 'v'",
            "l",
        )
        assert any("renamed" in item.detail for item in report.items)
