"""Per-field popup modes: persistence and override precedence.

Both halves are pure, so they run without QGIS. `apply_field_modes` is split out
of `translate_popup` for exactly that reason - the precedence rule is the part
worth pinning down, and it does not need a layer to express.

The recurring theme is tolerance: settings come back from a project file that a
user, another plugin, or a newer build of this one may have written, and a bad
entry must degrade to the default rather than stop the dialog opening.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json

from nika_onlymap_exporter.core.export_ir import PopupFieldMode, PopupFieldSpec
from nika_onlymap_exporter.core.popup_translator import apply_field_modes
from nika_onlymap_exporter.core.settings import LayerSettings


class TestLayerSettingsPersistence:
    def test_field_modes_round_trip(self) -> None:
        original = LayerSettings(fields={"name": PopupFieldMode.HEADER_ALWAYS.value})
        restored = LayerSettings.from_dict(json.loads(json.dumps(original.to_dict())))
        assert restored.fields == {"name": PopupFieldMode.HEADER_ALWAYS.value}

    def test_absent_fields_key_is_an_empty_mapping(self) -> None:
        """Projects written before this feature must still load."""
        restored = LayerSettings.from_dict({"include": True, "popup": True})
        assert restored.fields == {}

    def test_settings_are_not_shared_between_layers(self) -> None:
        """A mutable default would make every layer edit the same dict."""
        first, second = LayerSettings(), LayerSettings()
        first.fields["name"] = PopupFieldMode.HIDDEN.value
        assert second.fields == {}

    def test_an_unknown_mode_is_dropped_rather_than_raising(self) -> None:
        restored = LayerSettings.from_dict(
            {"fields": {"name": "invented_mode", "kind": PopupFieldMode.HIDDEN.value}}
        )
        assert restored.fields == {"kind": PopupFieldMode.HIDDEN.value}

    def test_malformed_field_entries_do_not_raise(self) -> None:
        for broken in ("a string", 42, None, ["a", "list"], {"": "hidden"}):
            assert LayerSettings.from_dict({"fields": broken}).fields == {}

    def test_non_string_mode_values_are_dropped(self) -> None:
        assert LayerSettings.from_dict({"fields": {"name": {"a": 1}}}).fields == {}


class TestApplyFieldModes:
    FIELDS = (
        PopupFieldSpec("name", mode=PopupFieldMode.INLINE_WITH_DATA),
        PopupFieldSpec("secret", mode=PopupFieldMode.HIDDEN),
    )

    def test_no_overrides_returns_the_fields_untouched(self) -> None:
        assert apply_field_modes(self.FIELDS, None) is self.FIELDS
        assert apply_field_modes(self.FIELDS, {}) is self.FIELDS

    def test_an_explicit_choice_overrides_the_qgis_default(self) -> None:
        """Un-hiding a column hidden in the attribute table has to be possible."""
        applied = apply_field_modes(
            self.FIELDS, {"secret": PopupFieldMode.INLINE_ALWAYS.value}
        )
        assert applied[1].mode is PopupFieldMode.INLINE_ALWAYS

    def test_fields_without_an_override_keep_their_derived_mode(self) -> None:
        applied = apply_field_modes(
            self.FIELDS, {"secret": PopupFieldMode.NO_LABEL.value}
        )
        assert applied[0].mode is PopupFieldMode.INLINE_WITH_DATA

    def test_an_override_for_a_field_that_no_longer_exists_is_ignored(self) -> None:
        """Settings outlive schema changes; a dropped column must not crash."""
        applied = apply_field_modes(
            self.FIELDS, {"removed_column": PopupFieldMode.HIDDEN.value}
        )
        assert applied == self.FIELDS

    def test_an_unparseable_override_falls_back_to_the_derived_mode(self) -> None:
        applied = apply_field_modes(self.FIELDS, {"name": "invented_mode"})
        assert applied[0].mode is PopupFieldMode.INLINE_WITH_DATA

    def test_aliases_survive_an_override(self) -> None:
        fields = (PopupFieldSpec("name", alias="Station name"),)
        applied = apply_field_modes(fields, {"name": PopupFieldMode.HIDDEN.value})
        assert applied[0].display_name == "Station name"
