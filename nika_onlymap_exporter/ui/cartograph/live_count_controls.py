"""The Edit tab's setup for the selected Live count.

What to count, as a few plain choices: the layer, all its features or only
those with one field's value, and the words under the number. Where and which
features come from the map the counter follows, so there is nothing here about
extents, visibility or filters. Under the choices, one line says what the
counter counts (the source summary) and, when it cannot show a number, why.

Every change is one undo step on the layout's own stack, applied once per
gesture (a choice made, or a typed value when it is committed).

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from qgis.core import QgsLayout, QgsLayoutItem, QgsLayoutItemLabel, QgsLayoutItemMap
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QWidget,
)

from ...core import live_count as lc
from ..i18n import tr
from ..theme import align_embedded_edits
from .frames_list import _QuietCombo
from .layout_items import item_edit, map_frames, map_label, one_step
from .live_counts import LiveCounts

#: How many of a field's values are offered as suggestions (any value can
#: still be typed).
VALUE_SUGGESTIONS = 1000
_SPACING = 8


class LiveCountSetup(QWidget):
    """Following / Count from / Count / Field / Value / Caption."""

    #: The counter was changed here.
    edited = pyqtSignal()

    def __init__(
        self, parent: QWidget | None = None, labels: list[QLabel] | None = None
    ) -> None:
        super().__init__(parent)
        self._layout_ref: QgsLayout | None = None
        self._number: QgsLayoutItemLabel | None = None
        self._service: LiveCounts | None = None
        self._loading = False
        self._labels = labels if labels is not None else []

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(_SPACING)
        grid.setVerticalSpacing(_SPACING)
        grid.setColumnStretch(1, 1)

        # One map: "Following: Main map". Several: a labelled choice.
        self.following_text = QLabel(self)
        self.following_text.setProperty("role", "hint")
        self.following_text.setWordWrap(True)
        self.following_text.setMinimumWidth(1)
        grid.addWidget(self.following_text, 0, 0, 1, 2)
        self.following_label = self._label(tr("Following"))
        self.following_combo = self._combo(tr("Map this counter follows"))
        self.following_combo.activated.connect(self._on_map)
        grid.addWidget(self.following_label, 1, 0)
        grid.addWidget(self.following_combo, 1, 1)

        self.layer_label = self._label(tr("Count from"))
        self.layer_combo = self._combo(tr("Layer to count"))
        self.layer_combo.setPlaceholderText(tr("Choose a layer"))
        self.layer_combo.activated.connect(self._on_layer)
        self.layer_label.setBuddy(self.layer_combo)
        grid.addWidget(self.layer_label, 2, 0)
        grid.addWidget(self.layer_combo, 2, 1)

        self.mode_label = self._label(tr("Count"))
        self.mode_combo = self._combo(tr("What to count"))
        self.mode_combo.addItem(tr("All features"), lc.MODE_ALL)
        self.mode_combo.addItem(tr("Matching a value"), lc.MODE_VALUE)
        self.mode_combo.activated.connect(self._on_mode)
        self.mode_label.setBuddy(self.mode_combo)
        grid.addWidget(self.mode_label, 3, 0)
        grid.addWidget(self.mode_combo, 3, 1)

        self.field_label = self._label(tr("Field"))
        self.field_combo = self._combo(tr("Field to match"))
        self.field_combo.setPlaceholderText(tr("Choose a field"))
        self.field_combo.activated.connect(self._on_field)
        self.field_label.setBuddy(self.field_combo)
        grid.addWidget(self.field_label, 4, 0)
        grid.addWidget(self.field_combo, 4, 1)

        self.value_label = self._label(tr("Value"))
        self.value_combo = _QuietCombo(self)
        self.value_combo.setEditable(True)
        self.value_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.value_combo.setAccessibleName(tr("Value to match"))
        self.value_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.value_combo.setMinimumContentsLength(6)
        self.value_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.value_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.value_combo.activated.connect(self._on_value_chosen)
        line = self.value_combo.lineEdit()
        if line is not None:
            line.setPlaceholderText(tr("Choose or type a value"))
            line.editingFinished.connect(self._on_value_typed)
        self.value_label.setBuddy(self.value_combo)
        grid.addWidget(self.value_label, 5, 0)
        grid.addWidget(self.value_combo, 5, 1)

        self.caption_label = self._label(tr("Caption"))
        self.caption_edit = QLineEdit(self)
        self.caption_edit.setAccessibleName(tr("Caption of the live count"))
        self.caption_edit.setMinimumWidth(1)
        self.caption_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.caption_edit.editingFinished.connect(self._on_caption)
        self.caption_edit.returnPressed.connect(self.caption_edit.clearFocus)
        self.caption_label.setBuddy(self.caption_edit)
        grid.addWidget(self.caption_label, 6, 0)
        grid.addWidget(self.caption_edit, 6, 1)

        self.summary = QLabel(self)
        self.summary.setProperty("role", "hint")
        self.summary.setWordWrap(True)
        self.summary.setMinimumWidth(1)
        grid.addWidget(self.summary, 7, 0, 1, 2)
        self.message = QLabel(self)
        self.message.setProperty("role", "checkVerdict")
        self.message.setWordWrap(True)
        self.message.setMinimumWidth(1)
        grid.addWidget(self.message, 8, 0, 1, 2)
        self.setVisible(False)

    # ---- building --------------------------------------------------------

    def _label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setProperty("role", "hint")
        self._labels.append(label)
        return label

    def _combo(self, accessible_name: str) -> QComboBox:
        combo = _QuietCombo(self)
        combo.setAccessibleName(accessible_name)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(6)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return combo

    def set_service(self, service: LiveCounts | None) -> None:
        self._service = service

    # ---- showing the counter ---------------------------------------------

    def number(self) -> QgsLayoutItemLabel | None:
        return self._number

    def show_for(self, layout: QgsLayout, item: QgsLayoutItem) -> bool:
        """Show the setup of the counter `item` is a part of. False (and
        hidden) when it is not one."""
        number = lc.owner_of(layout, item)
        if number is None:
            self._number = None
            self.setVisible(False)
            return False
        self._layout_ref, self._number = layout, number
        self._loading = True
        try:
            self._fill(layout, number)
        finally:
            self._loading = False
        self.setVisible(True)
        return True

    def _fill(self, layout: QgsLayout, number: QgsLayoutItemLabel) -> None:
        spec = lc.read_spec(number)
        project = layout.project()
        maps = map_frames(layout)
        followed = layout.itemByUuid(spec.map_uuid) if spec.map_uuid else None
        followed_map = followed if isinstance(followed, QgsLayoutItemMap) else None

        several = len(maps) > 1
        self.following_label.setVisible(several)
        self.following_combo.setVisible(several)
        self.following_text.setVisible(not several)
        self.following_combo.clear()
        for frame in maps:
            self.following_combo.addItem(map_label(frame), frame.uuid())
        if several:
            self.following_combo.setCurrentIndex(
                self.following_combo.findData(spec.map_uuid)
            )
            self.following_combo.setPlaceholderText(tr("Choose a map"))
        elif followed_map is not None:
            self.following_text.setText(
                tr("Following: {map}").format(map=map_label(followed_map))
            )
        else:
            self.following_text.setText(tr("Following: no map on the page"))

        # The layers: all the project's point, line and polygon layers; those
        # the followed map does not draw say so (they count 0).
        self.layer_combo.clear()
        layers = lc.countable_layers(project) if project is not None else []
        for layer in layers:
            name = layer.name()
            if followed_map is not None and not lc.map_draws(followed_map, layer.id()):
                name = tr("{layer} (not shown on this map)").format(layer=name)
            self.layer_combo.addItem(name, layer.id())
        self.layer_combo.setCurrentIndex(self.layer_combo.findData(spec.layer_id))

        self.mode_combo.setCurrentIndex(self.mode_combo.findData(spec.mode))
        value_mode = spec.mode == lc.MODE_VALUE
        for widget in (
            self.field_label,
            self.field_combo,
            self.value_label,
            self.value_combo,
        ):
            widget.setVisible(value_mode)
        layer = project.mapLayer(spec.layer_id) if project and spec.layer_id else None
        self.field_combo.clear()
        if lc.is_countable(layer):
            for field in layer.fields():
                self.field_combo.addItem(field.name(), field.name())
        self.field_combo.setCurrentIndex(self.field_combo.findData(spec.field))
        self._fill_value(layer, spec)

        caption = lc.caption_of(layout, number)
        text = caption.text() if caption is not None else ""
        if not (self.caption_edit.hasFocus() and self.caption_edit.isModified()):
            self.caption_edit.setText(text)
            self.caption_edit.setModified(False)
        self.caption_label.setVisible(caption is not None)
        self.caption_edit.setVisible(caption is not None)

        self.summary.setText(lc.source_summary(layout, number))
        self.summary.setVisible(bool(self.summary.text()))
        self._show_message(layers)

    def _fill_value(self, layer: object, spec: lc.CountSpec) -> None:
        combo = self.value_combo
        combo.clear()
        kind = (
            lc.field_kind(layer, spec.field)
            if lc.is_countable(layer) and spec.field
            else None
        )
        combo.setEditable(kind != "bool")
        # A new editable box has a new line edit, which needs aligning again.
        align_embedded_edits(combo)
        combo.setEnabled(kind is not None)
        line = combo.lineEdit()
        if kind is None:
            if line is not None:
                line.clear()
            return
        combo.addItem(tr("(no value)"), None)
        suggestions: list[str] = (
            [tr("Yes"), tr("No")]
            if kind == "bool"
            else list(lc.unique_values(layer, spec.field, VALUE_SUGGESTIONS))
        )
        for text in suggestions:
            combo.addItem(text, text)
        if spec.value_is_null:
            combo.setCurrentIndex(0)
        elif spec.value is not None:
            index = combo.findData(spec.value)
            if index >= 0:
                combo.setCurrentIndex(index)
            elif line is not None:
                combo.setCurrentIndex(-1)
                line.setText(spec.value)
        else:
            combo.setCurrentIndex(-1)
            if line is not None:
                line.clear()

    def _show_message(self, layers: list[object]) -> None:
        number = self._number
        text, tone = "", "hint"
        if number is not None:
            outcome = self._service.outcome(number) if self._service else None
            spec = lc.read_spec(number)
            if not layers and not spec.layer_id:
                text = tr(
                    "This project has no point, line or polygon layer to count. "
                    "Add one to the project."
                )
            elif outcome is not None and outcome.state not in (
                lc.CountState.OK,
                lc.CountState.PENDING,
            ):
                text = outcome.message
                if outcome.state in (lc.CountState.INVALID, lc.CountState.UNAVAILABLE):
                    tone = "error"
            elif outcome is None and not spec.layer_id:
                text = tr("Choose the layer to count.")
        self.message.setProperty("tone", tone)
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def refresh(self) -> None:
        """The service has a new outcome for the counter on show (or its
        layers changed): show the setup as it now is, leaving alone what the
        user is typing."""
        layout, number = self._layout_ref, self._number
        if layout is None or number is None or sip.isdeleted(number):
            return
        focus = QApplication.focusWidget()
        if focus is not None and self.isAncestorOf(focus):
            project = layout.project()
            layers = lc.countable_layers(project) if project is not None else []
            self._show_message(layers)
            return
        self._loading = True
        try:
            self._fill(layout, number)
        finally:
            self._loading = False

    def focus_first_choice(self) -> None:
        """Put the keyboard on what the counter needs first."""
        number = self._number
        if number is None:
            return
        spec = lc.read_spec(number)
        if not spec.layer_id:
            self.layer_combo.setFocus(Qt.FocusReason.TabFocusReason)

    # ---- the changes ------------------------------------------------------

    def _change(
        self,
        text: str,
        make: Callable[[lc.CountSpec], lc.CountSpec],
        also: Callable[[QgsLayout, QgsLayoutItemLabel], None] | None = None,
    ) -> None:
        layout, number = self._layout_ref, self._number
        if (
            self._loading
            or layout is None
            or number is None
            or sip.isdeleted(layout)
            or sip.isdeleted(number)
        ):
            return
        before = lc.read_spec(number)
        after = make(before)
        if after == before and also is None:
            return
        with one_step(layout, text):
            if after != before:
                with item_edit(layout, number, text):
                    lc.write_spec(number, after)
            if also is not None:
                also(layout, number)
        self._after_change()

    def _after_change(self) -> None:
        if self._service is not None:
            self._service.mark_dirty()
        layout, number = self._layout_ref, self._number
        if layout is not None and number is not None and not sip.isdeleted(number):
            self._loading = True
            try:
                self._fill(layout, number)
            finally:
                self._loading = False
        self.edited.emit()

    def _on_map(self, index: int) -> None:
        uuid = str(self.following_combo.itemData(index) or "")
        self._change(
            tr("Change followed map"), lambda spec: replace(spec, map_uuid=uuid)
        )

    def _on_layer(self, index: int) -> None:
        layout = self._layout_ref
        layer_id = str(self.layer_combo.itemData(index) or "")
        project = layout.project() if layout is not None else None
        layer = project.mapLayer(layer_id) if project and layer_id else None
        if layer is None:
            return

        def make(spec: lc.CountSpec) -> lc.CountSpec:
            changed = replace(spec, layer_id=layer.id(), layer_name=layer.name())
            if (
                layer.id() != spec.layer_id
                and layer.fields().lookupField(spec.field) < 0
            ):
                changed = replace(changed, field="", value=None, value_is_null=False)
            return changed

        def caption(lay: QgsLayout, number: QgsLayoutItemLabel) -> None:
            # A caption is written for the user's first layer, so the page
            # reads sensibly at once; later changes leave their words alone.
            part = lc.caption_of(lay, number)
            if part is not None and not part.text():
                with item_edit(lay, part, tr("Change live count")):
                    part.setText(layer.name())

        self._change(tr("Change counted layer"), make, also=caption)

    def _on_mode(self, index: int) -> None:
        mode = str(self.mode_combo.itemData(index))
        self._change(
            tr("Change what is counted"), lambda spec: replace(spec, mode=mode)
        )

    def _on_field(self, index: int) -> None:
        field = str(self.field_combo.itemData(index) or "")
        self._change(
            tr("Change counted field"),
            lambda spec: replace(spec, field=field, value=None, value_is_null=False),
        )

    def _on_value_chosen(self, index: int) -> None:
        if self.value_combo.itemData(index) is None:
            self._set_value(None, True)
        else:
            self._set_value(str(self.value_combo.itemText(index)), False)

    def _on_value_typed(self) -> None:
        line = self.value_combo.lineEdit()
        if line is None or self._loading:
            return
        text = line.text()
        if not line.isModified():
            return
        line.setModified(False)
        if text == tr("(no value)"):
            self._set_value(None, True)
        elif text.strip() == "" and self.value_combo.currentIndex() < 0:
            return
        else:
            self._set_value(text, False)

    def _set_value(self, value: str | None, is_null: bool) -> None:
        self._change(
            tr("Change matched value"),
            lambda spec: replace(spec, value=value, value_is_null=is_null),
        )

    def _on_caption(self) -> None:
        layout, number = self._layout_ref, self._number
        if self._loading or layout is None or number is None:
            return
        if not self.caption_edit.isModified():
            return
        self.caption_edit.setModified(False)
        text = self.caption_edit.text()
        part = lc.caption_of(layout, number)
        if part is not None and part.text() == text:
            return
        if part is None:
            return
        with item_edit(layout, part, tr("Change caption")):
            part.setText(text)
        self._after_change()
