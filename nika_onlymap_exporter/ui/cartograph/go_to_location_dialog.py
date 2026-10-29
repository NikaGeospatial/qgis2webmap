"""Map frames > "Go to location...": show a place inside one map frame.

A compact modal dialog with two tabs. **Coordinates** takes a latitude and a
longitude (or an easting and a northing) in a coordinate system the user can
change; **Feature** searches a layer the frame draws by display name, QGIS
feature ID or an ID/text attribute and lists what it found. Nothing is applied
while the user types, searches or selects: only **Go** changes the map, as one
undo step on the layout's own stack, and only the area shown inside the frame
changes - never the frame's place, size or rotation on the sheet.

The logic (parsing, transforms, the search itself, the destination) lives in
`core.go_to_location`; this module is the widgets and the rules of the
interaction:

- Enter in the search box searches, it never goes: no button is a default
  button. Enter on the results list goes, a double click selects and goes.
- A search runs on a background task and is polled by a timer, so the window
  stays responsive on a large layer and an old search can never replace the
  result of a newer one (a generation counter).
- The frame, its atlas and the layer are looked at again whenever they matter
  (a timer, and once more when Go is pressed).
- The last mode and the last layer per frame are remembered for the session in
  this module, never in the layout.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from dataclasses import dataclass

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsExpression,
    QgsFeature,
    QgsPointXY,
    QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.gui import QgsProjectionSelectionDialog
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QLocale, QModelIndex, QRect, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import (
    QFontMetrics,
    QGuiApplication,
    QKeyEvent,
    QPainter,
    QPalette,
    QShowEvent,
)
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import go_to_location as core
from ...core.go_to_location import ApplyError, FrameTarget, SearchJob, TransformError
from ..fitted_label import FittedLabel
from ..i18n import plural, tr
from . import frames_list

WGS84 = "EPSG:4326"
COORDINATES_TAB, FEATURE_TAB = 0, 1
PREFERRED_WIDTH = 620
MINIMUM_WIDTH = 520
SCREEN_MARGIN = 40
POLL_MS = 40
WATCH_MS = 300

_ROW_ROLE = Qt.ItemDataRole.UserRole
_PRIMARY_ROLE = Qt.ItemDataRole.UserRole + 1
_SECONDARY_ROLE = Qt.ItemDataRole.UserRole + 2

# What the plugin remembers between openings in one session - not in the layout.
_session: dict[str, object] = {"mode": COORDINATES_TAB, "layers": {}}


def reset_session() -> None:
    """Forget the remembered mode and layers (a new plugin session; tests)."""
    _session["mode"] = COORDINATES_TAB
    _session["layers"] = {}


def _remembered_layers() -> dict[str, str]:
    layers = _session["layers"]
    assert isinstance(layers, dict)
    return layers


@dataclass
class Hit:
    """A result row. Its identity is the layer's ID and the feature's ID, never
    its name (two features can share one)."""

    layer_id: str
    fid: int
    usable: bool  # has a location to go to
    single_point: bool
    drawn: bool  # the layer's style draws it


class _ResultDelegate(QStyledItemDelegate):
    """A result as two lines: the name, bold, and its IDs under it, muted; each
    cut with an ellipsis at the row's edge."""

    PAD_X, PAD_Y = 8, 5

    def _lines(self, option: QStyleOptionViewItem) -> tuple[QFontMetrics, QFontMetrics]:
        bold = option.font
        bold.setBold(True)
        return QFontMetrics(bold), QFontMetrics(option.font)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        primary, secondary = self._lines(option)
        return QSize(0, primary.height() + secondary.height() + 2 * self.PAD_Y + 2)

    def paint(
        self, painter: QPainter | None, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        if painter is None:
            return
        shown = QStyleOptionViewItem(option)
        self.initStyleOption(shown, index)
        shown.text = ""
        style = (
            shown.widget.style() if shown.widget is not None else QApplication.style()
        )
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, shown, painter, shown.widget
        )
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        group = QPalette.ColorGroup.Normal if enabled else QPalette.ColorGroup.Disabled
        palette = option.palette
        primary_font = option.font
        primary_font.setBold(True)
        primary, secondary = QFontMetrics(primary_font), QFontMetrics(option.font)
        area = option.rect.adjusted(self.PAD_X, self.PAD_Y, -self.PAD_X, -self.PAD_Y)
        painter.save()
        painter.setFont(primary_font)
        painter.setPen(palette.color(group, QPalette.ColorRole.Text))
        first = QRect(area.left(), area.top(), area.width(), primary.height())
        painter.drawText(
            first,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            primary.elidedText(
                str(index.data(_PRIMARY_ROLE) or ""),
                Qt.TextElideMode.ElideRight,
                first.width(),
            ),
        )
        painter.setFont(option.font)
        painter.setPen(palette.color(group, QPalette.ColorRole.PlaceholderText))
        second = QRect(
            area.left(), first.bottom() + 1, area.width(), secondary.height()
        )
        painter.drawText(
            second,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            secondary.elidedText(
                str(index.data(_SECONDARY_ROLE) or ""),
                Qt.TextElideMode.ElideRight,
                second.width(),
            ),
        )
        painter.restore()


class _ResultList(QListWidget):
    """The results: Enter on the selected row asks to go."""

    go_requested = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802
        if event is not None and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            self.go_requested.emit()
            return
        super().keyPressEvent(event)


def _button(text: str, accessible: str, parent: QWidget) -> QPushButton:
    """A push button that is never a default button, so Enter in a text box
    cannot reach it through the dialog."""
    button = QPushButton(text, parent)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.setAccessibleName(accessible)
    return button


def _name_clear_button(edit: QLineEdit, name: str) -> None:
    for button in edit.findChildren(QToolButton):
        button.setAccessibleName(name)


def _field_label(text: str, buddy: QWidget, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setBuddy(buddy)
    return label


def _units_text(crs: QgsCoordinateReferenceSystem) -> str:
    unit = crs.mapUnits()
    names = {
        Qgis.DistanceUnit.Meters: tr("metres"),
        Qgis.DistanceUnit.Kilometers: tr("kilometres"),
        Qgis.DistanceUnit.Feet: tr("feet"),
    }
    return names.get(unit, QgsUnitTypes.toString(unit).lower())


def crs_text(crs: QgsCoordinateReferenceSystem) -> str:
    """The coordinate system as the read-only field shows it."""
    name = crs.description() or crs.authid() or tr("Unknown coordinate system")
    ident = f" ({crs.authid()})" if crs.authid() else ""
    if core.is_geographic(crs):
        return tr("{name} — latitude/longitude{ident}").format(name=name, ident=ident)
    return f"{name}{ident}"


class GoToLocationDialog(QDialog):
    """The Go to location dialog for one map frame (`FrameTarget`)."""

    def __init__(
        self,
        target: FrameTarget,
        parent: QWidget | None = None,
        locale: QLocale | None = None,
    ) -> None:
        super().__init__(parent)
        self.target = target
        self._locale = locale or QLocale()
        self._crs = QgsCoordinateReferenceSystem(WGS84)
        self._coords_ready = False
        self._first_activation = True
        self._coordinate_note = ""
        self._revealed = [False, False]
        self._generation = 0
        self._job: SearchJob | None = None
        self._layer: QgsVectorLayer | None = None
        self._results_fresh = False
        self._apply_error = ""
        self.setWindowTitle(tr("Go to location"))
        self.setModal(True)
        self.setObjectName("goToLocationDialog")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        self.context_label = frames_list._ElidedLabel(
            tr("Map: {name}").format(name=target.name), self
        )
        self.context_label.setToolTip(target.name)
        self.context_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.context_label.setProperty("role", "hint")
        outer.addWidget(self.context_label)

        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_coordinates_tab(), tr("Coordinates"))
        self.tabs.addTab(self._build_feature_tab(), tr("Feature"))
        outer.addWidget(self.tabs, 1)
        outer.addLayout(self._build_footer())

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll_search)
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(WATCH_MS)
        self._watch_timer.timeout.connect(self._watch)

        self._populate_layers()
        self._set_crs(self._crs, announce=False)
        mode = _session["mode"]
        self.tabs.setCurrentIndex(mode if isinstance(mode, int) else COORDINATES_TAB)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._on_tab_changed(self.tabs.currentIndex(), remember=False)
        self._refresh_all()
        self._fit_to_content()

    # ---- building -----------------------------------------------------------

    def _build_coordinates_tab(self) -> QWidget:
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(12)

        self.crs_title = QLabel(tr("Coordinate system"), page)
        box.addWidget(self.crs_title)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.crs_value = FittedLabel("", page)
        self.crs_value.setProperty("role", "fieldValue")
        self.crs_value.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        row.addWidget(self.crs_value, 1)
        self.crs_button = _button(tr("Change…"), tr("Change coordinate system"), page)
        self.crs_button.clicked.connect(self.choose_crs)
        row.addWidget(self.crs_button, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(row)

        self.first_edit = QLineEdit(page)
        self.second_edit = QLineEdit(page)
        self.first_label = _field_label("", self.first_edit, page)
        self.second_label = _field_label("", self.second_edit, page)
        for index, (label, edit) in enumerate(
            ((self.first_label, self.first_edit), (self.second_label, self.second_edit))
        ):
            edit.setClearButtonEnabled(True)
            edit.textChanged.connect(self._on_coordinate_text_changed)
            edit.editingFinished.connect(self._on_coordinate_finished)
            group = QVBoxLayout()
            group.setSpacing(4)
            group.addWidget(label)
            group.addWidget(edit)
            box.addLayout(group)
            edit.setProperty("coordinateIndex", index)
        self.coordinate_hint = FittedLabel("", page)
        self.coordinate_hint.setProperty("role", "hint")
        box.addWidget(self.coordinate_hint)
        self.coordinate_message = FittedLabel("", page)
        self.coordinate_message.setProperty("role", "inlineNote")
        self.coordinate_message.setProperty("tone", "warning")
        box.addWidget(self.coordinate_message)
        box.addStretch(1)
        return page

    def _build_feature_tab(self) -> QWidget:
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 12, 0, 0)
        box.setSpacing(12)

        self.layer_combo = QComboBox(page)
        self.layer_combo.setAccessibleName(tr("Layer"))
        self.layer_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.layer_combo.setMinimumContentsLength(10)
        self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        box.addLayout(self._group(tr("Layer"), self.layer_combo, page))

        self.source_combo = QComboBox(page)
        self.source_combo.setAccessibleName(tr("Search in"))
        self.source_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.source_combo.setMinimumContentsLength(10)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        group = self._group(tr("Search in"), self.source_combo, page)
        self.source_hint = FittedLabel("", page)
        self.source_hint.setProperty("role", "hint")
        group.addWidget(self.source_hint)
        box.addLayout(group)

        self.query_edit = QLineEdit(page)
        self.query_edit.setPlaceholderText(tr("Search selected layer…"))
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.setAccessibleName(tr("Name or ID"))
        _name_clear_button(self.query_edit, tr("Clear search"))
        self.query_edit.textChanged.connect(self._on_query_changed)
        self.query_edit.returnPressed.connect(self.start_search)
        self.search_button = _button(tr("Search"), tr("Search"), page)
        self.search_button.clicked.connect(self.start_search)
        query_row = QHBoxLayout()
        query_row.setSpacing(8)
        query_row.addWidget(self.query_edit, 1)
        query_row.addWidget(self.search_button)
        group = QVBoxLayout()
        group.setSpacing(4)
        group.addWidget(_field_label(tr("Name or ID"), self.query_edit, page))
        group.addLayout(query_row)
        box.addLayout(group)

        self.status_label = FittedLabel("", page)
        self.status_label.setProperty("role", "hint")
        box.addWidget(self.status_label)

        self.results = _ResultList(page)
        self.results.setAccessibleName(tr("Results"))
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.setItemDelegate(_ResultDelegate(self.results))
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.results.setProperty("role", "locationResults")
        self.results.itemSelectionChanged.connect(self._on_result_selected)
        self.results.itemDoubleClicked.connect(self._on_result_double_clicked)
        self.results.go_requested.connect(self.go)
        box.addWidget(self.results, 1)

        self.fit_check = QCheckBox(tr("Fit whole feature"), page)
        self.fit_check.setAccessibleName(tr("Fit whole feature"))
        self.fit_check.setEnabled(False)
        box.addWidget(self.fit_check)
        self.scope_hint = FittedLabel(
            tr("Searches the whole selected layer, beyond the current map view."), page
        )
        self.scope_hint.setProperty("role", "hint")
        box.addWidget(self.scope_hint)
        self.feature_message = FittedLabel("", page)
        self.feature_message.setProperty("role", "inlineNote")
        self.feature_message.setProperty("tone", "warning")
        box.addWidget(self.feature_message)
        return page

    def _group(self, title: str, field: QWidget, parent: QWidget) -> QVBoxLayout:
        group = QVBoxLayout()
        group.setSpacing(4)
        group.addWidget(_field_label(title, field, parent))
        group.addWidget(field)
        return group

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.footer_status = FittedLabel("", self)
        self.footer_status.setProperty("role", "inlineNote")
        self.footer_status.setProperty("tone", "warning")
        row.addWidget(self.footer_status, 1, Qt.AlignmentFlag.AlignVCenter)
        self.cancel_button = _button(tr("Cancel"), tr("Cancel"), self)
        self.cancel_button.clicked.connect(self.reject)
        self.go_button = _button(tr("Go"), tr("Go"), self)
        self.go_button.setProperty("role", "primary")
        self.go_button.clicked.connect(self.go)
        self.go_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        row.addWidget(self.go_button)
        return row

    # ---- window -------------------------------------------------------------

    def _screen_size(self) -> QSize:
        screen = self.screen() or QGuiApplication.primaryScreen()
        return (
            screen.availableGeometry().size()
            if screen is not None
            else QSize(1024, 768)
        )

    def _fit_to_content(self) -> None:
        """Width: 620 (not below 520), never wider than the screen. Height: what
        the open tab needs, never taller than the screen. The tab that is not
        open does not count towards the height."""
        available = self._screen_size()
        width = min(PREFERRED_WIDTH, available.width() - SCREEN_MARGIN)
        self.setMinimumWidth(min(MINIMUM_WIDTH, width))
        current = self.tabs.currentWidget()
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            policy = (
                QSizePolicy.Policy.Preferred
                if page is current
                else QSizePolicy.Policy.Ignored
            )
            page.setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        self.layout().activate()
        height = min(self.sizeHint().height(), available.height() - SCREEN_MARGIN)
        self.resize(width, max(height, self.minimumSizeHint().height()))

    def showEvent(self, event: QShowEvent | None) -> None:  # noqa: N802
        super().showEvent(event)
        _name_clear_button(self.query_edit, tr("Clear search"))
        self._name_coordinate_clear_buttons()
        self._watch_timer.start()
        self._focus_for_tab(self.tabs.currentIndex())

    def done(self, result: int) -> None:
        self._watch_timer.stop()
        self._poll_timer.stop()
        self._generation += 1
        if self._job is not None:
            self._job.cancel()
            self._job = None
        super().done(result)

    # ---- tabs ---------------------------------------------------------------

    def _on_tab_changed(self, index: int, remember: bool = True) -> None:
        if remember:
            _session["mode"] = index
        self._fit_to_content()
        self._focus_for_tab(index)
        self._update_go()

    def _focus_for_tab(self, index: int) -> None:
        if index == COORDINATES_TAB and self._first_activation and self.isVisible():
            self._first_activation = False
            self.first_edit.setFocus()
        elif index == FEATURE_TAB and self.isVisible():
            if self.layer_combo.currentData() is not None:
                self.query_edit.setFocus()

    # ---- coordinates ----------------------------------------------------------

    def _name_coordinate_clear_buttons(self) -> None:
        _name_clear_button(
            self.first_edit, tr("Clear {field}").format(field=self.first_label.text())
        )
        _name_clear_button(
            self.second_edit, tr("Clear {field}").format(field=self.second_label.text())
        )

    def _axis_names(self) -> tuple[str, str]:
        if core.is_geographic(self._crs):
            return tr("Latitude"), tr("Longitude")
        return tr("Easting (X)"), tr("Northing (Y)")

    def _example(self, value: float) -> str:
        return core.format_number(value, True, self._locale)

    def _set_crs(
        self, crs: QgsCoordinateReferenceSystem, announce: bool = True
    ) -> None:
        """Switch the coordinate system. A valid pair is converted to the same
        place; anything else is cleared and the user is told."""
        old = self._crs
        converted: tuple[str, str] | None = None
        if announce and crs != old:
            point = self._typed_point()
            if point is not None:
                try:
                    moved = core.transform_point(point, old, crs, self.target.project)
                except TransformError:
                    moved = None
                if moved is not None:
                    first, second = core.point_to_fields(moved, crs)
                    if not (
                        core.field_range_problem(0, first, crs)
                        or core.field_range_problem(1, second, crs)
                    ):
                        geographic = core.is_geographic(crs)
                        converted = (
                            core.format_number(first, geographic, self._locale),
                            core.format_number(second, geographic, self._locale),
                        )
        self._crs = crs
        self.crs_value.setText(crs_text(crs))
        first_name, second_name = self._axis_names()
        self.first_label.setText(first_name)
        self.second_label.setText(second_name)
        self.first_edit.setAccessibleName(first_name)
        self.second_edit.setAccessibleName(second_name)
        self._name_coordinate_clear_buttons()
        if core.is_geographic(crs):
            self.first_edit.setPlaceholderText(
                tr("Example: {value}").format(value=self._example(36.10))
            )
            self.second_edit.setPlaceholderText(
                tr("Example: {value}").format(value=self._example(-112.10))
            )
            self.coordinate_hint.setText(
                tr("Enter decimal degrees. Latitude first, longitude second.")
            )
        else:
            self.first_edit.setPlaceholderText("")
            self.second_edit.setPlaceholderText("")
            self.coordinate_hint.setText(
                tr("Enter easting and northing in {units}.").format(
                    units=_units_text(crs)
                )
            )
        self._revealed = [False, False]
        for edit in (self.first_edit, self.second_edit):
            edit.blockSignals(True)
        if announce and crs != old:
            if converted is not None:
                self.first_edit.setText(converted[0])
                self.second_edit.setText(converted[1])
                self._coordinate_note = ""
            else:
                self.first_edit.clear()
                self.second_edit.clear()
                self._coordinate_note = tr(
                    "Coordinate system changed. Enter coordinates in {name}."
                ).format(name=crs.description() or crs.authid())
        for edit in (self.first_edit, self.second_edit):
            edit.blockSignals(False)
            edit.setCursorPosition(0)
        self._refresh_coordinates()

    def choose_crs(self) -> None:
        """Change...: QGIS's own coordinate-system chooser."""
        chooser = QgsProjectionSelectionDialog(self)
        chooser.setCrs(self._crs)
        if chooser.exec() and chooser.crs().isValid():
            self._set_crs(chooser.crs())

    def _read(self, index: int) -> core.ParsedNumber:
        edit = self.first_edit if index == 0 else self.second_edit
        return core.parse_decimal(edit.text(), self._locale)

    def _typed_point(self) -> QgsPointXY | None:
        """The two fields as a point in the chosen CRS, when both are valid."""
        first, second = self._read(0), self._read(1)
        if first.value is None or second.value is None:
            return None
        if core.field_range_problem(
            0, first.value, self._crs
        ) or core.field_range_problem(1, second.value, self._crs):
            return None
        return core.fields_to_point(first.value, second.value, self._crs)

    def _field_message(self, index: int) -> str:
        """What is wrong with one populated field, or "" if nothing is."""
        parsed = self._read(index)
        geographic = core.is_geographic(self._crs)
        if parsed.problem == core.EMPTY:
            return ""
        if parsed.problem == core.PAIR:
            return tr("Enter one value in each field.")
        if parsed.problem == core.UNSUPPORTED:
            if geographic:
                return tr("Use decimal degrees only, for example {example}.").format(
                    example=self._example(36.10 if index == 0 else -112.10)
                )
            return tr("Use a plain decimal number, for example {example}.").format(
                example=core.format_number(412345.6, False, self._locale)
            )
        if parsed.problem == core.INVALID:
            if geographic:
                return (
                    tr("Enter a valid latitude.")
                    if index == 0
                    else tr("Enter a valid longitude.")
                )
            return (
                tr("Enter a valid easting.")
                if index == 0
                else tr("Enter a valid northing.")
            )
        assert parsed.value is not None
        if core.field_range_problem(index, parsed.value, self._crs):
            return (
                tr("Latitude must be between -90 and 90.")
                if index == 0
                else tr("Longitude must be between -180 and 180.")
            )
        return ""

    def _on_coordinate_text_changed(self, _text: str) -> None:
        edit = self.sender()
        index = (
            edit.property("coordinateIndex") if isinstance(edit, QLineEdit) else None
        )
        if index in (0, 1):
            self._revealed[index] = False
        self._coordinate_note = ""
        self._refresh_coordinates()

    def _on_coordinate_finished(self) -> None:
        edit = self.sender()
        index = (
            edit.property("coordinateIndex") if isinstance(edit, QLineEdit) else None
        )
        if index in (0, 1):
            self._revealed[index] = True
        self._refresh_coordinates()

    def _conversion_problem(self) -> str:
        """Both fields are valid but the place cannot be moved into the frame's
        coordinate system."""
        point = self._typed_point()
        if point is None:
            return ""
        try:
            core.transform_point(
                point, self._crs, self.target.crs(), self.target.project
            )
        except TransformError:
            return tr(
                "These coordinates cannot be converted to the map's coordinate system."
            )
        return ""

    def _refresh_coordinates(self) -> None:
        """Go's readiness, and the message: errors only for fields the user has
        finished with (or tried to submit), never for a field still being typed."""
        messages = [self._field_message(0), self._field_message(1)]
        shown = [m for i, m in enumerate(messages) if m and self._revealed[i]]
        problem = self._conversion_problem() if not any(messages) else ""
        self._coords_ready = (
            self._typed_point() is not None and not any(messages) and not problem
        )
        self.coordinate_message.setText(
            shown[0] if shown else problem or self._coordinate_note
        )
        self._update_go()

    def submit_coordinates(self) -> None:
        """Enter in a coordinate field: validate what is populated and, if both
        fields are fine, go."""
        self._revealed = [True, True]
        self._refresh_coordinates()
        if self._coords_ready:
            self.go()
        elif self._read(0).ok and not self.second_edit.text().strip():
            self.second_edit.setFocus()

    # ---- the feature tab --------------------------------------------------------

    def _populate_layers(self) -> None:
        layers = self.target.layers()
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        names = [layer.name() for layer in layers]
        if len(layers) > 1:
            self.layer_combo.addItem(tr("Choose a layer…"), None)
        for layer in layers:
            label = layer.name()
            if names.count(label) > 1:
                label = f"{label} ({layer.crs().authid()}, {layer.id()[-6:]})"
            self.layer_combo.addItem(label, layer.id())
            self.layer_combo.setItemData(
                self.layer_combo.count() - 1,
                layer.source(),
                Qt.ItemDataRole.ToolTipRole,
            )
        choice = _remembered_layers().get(self.target.key)
        index = self.layer_combo.findData(choice) if choice else -1
        if index < 0 and len(layers) == 1:
            index = 0
        self.layer_combo.setCurrentIndex(max(index, 0))
        self.layer_combo.blockSignals(False)
        searchable = bool(layers)
        for widget in (
            self.layer_combo,
            self.source_combo,
            self.query_edit,
            self.search_button,
        ):
            widget.setEnabled(searchable)
        self._select_layer()

    def _current_layer(self) -> QgsVectorLayer | None:
        layer_id = self.layer_combo.currentData()
        if not layer_id:
            return None
        for layer in self.target.layers():
            if layer.id() == layer_id:
                return layer
        return None

    def _select_layer(self) -> None:
        """Follow the layer combo: rebuild "Search in" for it."""
        self._layer = self._current_layer()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        if self._layer is not None:
            for source in core.search_sources(self._layer):
                self.source_combo.addItem(source.label, source)
        self.source_combo.blockSignals(False)
        self.source_combo.setEnabled(self._layer is not None)
        self._update_source_hint()

    def _current_source(self) -> core.SearchSource | None:
        data = self.source_combo.currentData()
        return data if isinstance(data, core.SearchSource) else None

    def _update_source_hint(self) -> None:
        source = self._current_source()
        if source is None:
            self.source_hint.setText("")
        elif source.kind == core.DISPLAY:
            self.source_hint.setText(tr("Find features by their displayed name."))
        elif source.kind == core.FEATURE_ID:
            self.source_hint.setText(tr("Use the feature's internal QGIS ID."))
        else:
            self.source_hint.setText(tr("Search {field}.").format(field=source.label))

    def _on_layer_changed(self, _index: int) -> None:
        layer_id = self.layer_combo.currentData()
        if layer_id:
            _remembered_layers()[self.target.key] = layer_id
        self._select_layer()
        self._invalidate(clear_status=True)
        self._refresh_search_button()

    def _on_source_changed(self, _index: int) -> None:
        self._update_source_hint()
        self._invalidate(clear_status=True)
        self._refresh_search_button()

    def _on_query_changed(self, _text: str) -> None:
        empty = not self.query_edit.text().strip()
        self._invalidate(clear_status=True, clear_results=empty)
        self._refresh_search_button()
        source = self._current_source()
        text = self.query_edit.text().strip()
        wrong = (
            source is not None
            and source.whole_number
            and text
            and core.parse_whole_number(text) is None
        )
        self.feature_message.setText(tr("Enter a whole-number ID.") if wrong else "")

    def _refresh_search_button(self) -> None:
        source = self._current_source()
        layer = self._layer
        text = self.query_edit.text().strip()
        ok = layer is not None and source is not None and bool(text)
        if ok and source is not None and source.whole_number:
            ok = core.parse_whole_number(text) is not None
        self.search_button.setEnabled(bool(ok) and self.layer_combo.isEnabled())

    def _invalidate(
        self, clear_status: bool = False, clear_results: bool = False
    ) -> None:
        """Whatever was found no longer answers what is asked: drop the selection,
        grey the old list, forget any search still running."""
        self._generation += 1
        if self._job is not None:
            self._job.cancel()
            self._job = None
            self._poll_timer.stop()
        self._results_fresh = False
        self.results.blockSignals(True)
        self.results.clearSelection()
        self.results.blockSignals(False)
        if clear_results:
            self.results.clear()
        self.results.setEnabled(not self.results.count())
        if clear_status:
            self.status_label.setText("")
        self.fit_check.setEnabled(False)
        self.fit_check.setChecked(False)
        self.feature_message.setText("")
        self._update_go()

    def start_search(self) -> None:
        """Search the whole selected layer for the query, in the background."""
        layer, source = self._layer, self._current_source()
        if layer is None or source is None or not self.search_button.isEnabled():
            return
        plan = core.SearchPlan(layer, source, self.query_edit.text())
        if not plan.valid:
            return
        self._invalidate()
        self.results.clear()
        self.results.setEnabled(True)
        self.status_label.setText(tr("Searching…"))
        self._job = core.start_search(layer, plan, self._generation)
        self._poll_timer.start()

    def _poll_search(self) -> None:
        job = self._job
        if job is None:
            self._poll_timer.stop()
            return
        if not job.done:
            return
        self._poll_timer.stop()
        self._job = None
        if job.generation != self._generation:
            return
        self._show_outcome(job)

    def _result_texts(
        self,
        layer: QgsVectorLayer,
        source: core.SearchSource,
        matches: list[core.Match],
    ) -> dict[int, str]:
        texts: dict[int, str] = {}
        for match in matches:
            if source.kind == core.ATTRIBUTE:
                value = match.feature[source.field_name]
                texts[match.fid] = "" if value is None else str(value)
            elif source.kind == core.FEATURE_ID:
                texts[match.fid] = str(match.fid)
            else:
                texts[match.fid] = core.display_text(layer, match.feature)
        return texts

    def _secondary_line(
        self,
        layer: QgsVectorLayer,
        source: core.SearchSource,
        feature: QgsFeature,
        usable: bool,
    ) -> str:
        parts: list[str] = []
        shows_name = source.kind == core.ATTRIBUTE and core.display_expression(
            layer
        ).strip() != QgsExpression.quotedColumnRef(source.field_name)
        if shows_name:
            field = layer.fields().field(source.field_name)
            parts.append(f"{field.displayName()}: {feature[source.field_name]}")
        parts.append(tr("QGIS ID: {id}").format(id=feature.id()))
        if not usable:
            parts.append(tr("No location"))
        return " · ".join(parts)

    def _show_outcome(self, job: SearchJob) -> None:
        layer, source = self._layer, self._current_source()
        self.results.clear()
        if job.error or job.outcome is None or layer is None or source is None:
            self.status_label.setText(tr("The search could not be completed."))
            return
        outcome = job.outcome
        texts = self._result_texts(layer, source, outcome.matches)
        ranked = core.rank_matches(outcome, texts, self.query_edit.text())
        for match in ranked:
            geometry = match.feature.geometry()
            usable = core.has_location(geometry)
            name = (
                texts[match.fid]
                if source.kind == core.DISPLAY
                else core.display_text(layer, match.feature)
            )
            primary = name or tr("Feature {id}").format(id=match.fid)
            secondary = self._secondary_line(layer, source, match.feature, usable)
            item = QListWidgetItem(primary)
            item.setData(_PRIMARY_ROLE, primary)
            item.setData(_SECONDARY_ROLE, secondary)
            item.setToolTip(f"{primary}\n{secondary}")
            item.setData(
                _ROW_ROLE,
                Hit(
                    match.layer_id,
                    match.fid,
                    usable,
                    usable and core.is_single_point(geometry),
                    True,
                ),
            )
            self.results.addItem(item)
        self._results_fresh = True
        self.results.setEnabled(True)
        count = self.results.count()
        if outcome.more:
            self.status_label.setText(
                tr("Showing the first 50 matches. Refine your search.")
            )
        elif count == 0:
            self.status_label.setText(tr("No matching features."))
        else:
            self.status_label.setText(plural(count, "{n} match", "{n} matches"))
        usable_rows = [
            i for i in range(count) if self._hit(self.results.item(i)).usable
        ]
        if count == 1:
            self.results.setCurrentRow(0)
        elif len(usable_rows) == 1:
            self.results.setCurrentRow(usable_rows[0])
        elif count > 1:
            self.feature_message.setText(tr("Select a feature."))
        self._update_go()

    def _hit(self, item: QListWidgetItem | None) -> Hit:
        data = item.data(_ROW_ROLE) if item is not None else None
        assert isinstance(data, Hit)
        return data

    def _selected_hit(self) -> Hit | None:
        items = self.results.selectedItems()
        if not items or not self._results_fresh:
            return None
        return self._hit(items[0])

    def _on_result_selected(self) -> None:
        hit = self._selected_hit()
        self.fit_check.blockSignals(True)
        if hit is None:
            self.fit_check.setEnabled(False)
            self.fit_check.setChecked(False)
        elif not hit.usable:
            self.fit_check.setEnabled(False)
            self.fit_check.setChecked(False)
            self.feature_message.setText(tr("This feature has no location to display."))
        elif hit.single_point:
            self.fit_check.setEnabled(False)
            self.fit_check.setChecked(False)
            self.feature_message.setText("")
        else:
            self.fit_check.setEnabled(True)
            self.fit_check.setChecked(True)
            self.feature_message.setText("")
        self.fit_check.blockSignals(False)
        if hit is not None and hit.usable:
            self._warn_if_hidden_by_style(hit)
        self._update_go()

    def _warn_if_hidden_by_style(self, hit: Hit) -> None:
        layer = self._layer
        if layer is None or sip.isdeleted(layer):
            return
        feature = layer.getFeature(hit.fid)
        if feature.isValid() and not core.would_be_drawn(layer, feature):
            self.feature_message.setText(
                tr(
                    "This feature may not appear on the map: "
                    "the layer's styling hides it."
                )
            )

    def _on_result_double_clicked(self, _item: QListWidgetItem) -> None:
        self.go()

    # ---- state ------------------------------------------------------------------

    def _frame_problem(self) -> str:
        if not self.target.available():
            return tr("This map frame is no longer available.")
        if self.target.follows_atlas():
            return tr(
                "This map follows the atlas. "
                "Turn off the map series to choose a fixed location."
            )
        return ""

    def _watch(self) -> None:
        """The layout or a layer may change under an open dialog."""
        if sip.isdeleted(self):
            return
        layer = self._layer
        if layer is not None and not any(
            other.id() == layer.id() for other in self.target.layers()
        ):
            self._layer_gone()
        self._refresh_all()

    def _layer_gone(self) -> None:
        """The searched layer left the project or the frame: its results are
        dropped, and the list of layers is read again."""
        self._populate_layers()
        self._invalidate(clear_status=True, clear_results=True)
        self.feature_message.setText(tr("This layer is no longer available."))

    def _refresh_all(self) -> None:
        self._refresh_coordinates()
        self._refresh_search_button()
        self._update_go()

    def _update_go(self) -> None:
        problem = self._frame_problem()
        self.footer_status.setText(problem or self._apply_error)
        if self.tabs.currentIndex() == COORDINATES_TAB:
            ready = self._coords_ready
        else:
            hit = self._selected_hit()
            ready = hit is not None and hit.usable
        self.go_button.setEnabled(bool(ready) and not problem)
        enabled = not problem
        self.crs_button.setEnabled(enabled)
        for widget in (self.first_edit, self.second_edit):
            widget.setEnabled(enabled)

    # ---- go ---------------------------------------------------------------------

    def go(self) -> None:
        """Apply the destination of the open tab as one undo step, then close.
        On any problem the dialog stays open and says what to do."""
        self._apply_error = ""
        if self.tabs.currentIndex() == COORDINATES_TAB:
            self._revealed = [True, True]
            self._refresh_coordinates()
            if not self._coords_ready:
                return
        else:
            hit = self._selected_hit()
            if hit is None or not hit.usable:
                return
        problem = self._frame_problem()
        if problem:
            self._update_go()
            return
        try:
            if self.tabs.currentIndex() == COORDINATES_TAB:
                point = self._typed_point()
                assert point is not None
                extent = core.coordinate_destination(self.target, point, self._crs)
            else:
                extent = self._feature_extent()
                if extent is None:
                    return
            self.target.apply_extent(extent, tr("Go to location"))
        except TransformError:
            self._fail(
                tr("This location cannot be converted to the map's coordinate system.")
            )
            return
        except ApplyError as error:
            self._fail(self._apply_message(error.reason))
            return
        except Exception:  # an unexpected failure must not abort QGIS from a slot
            self._fail(
                tr("The map could not be moved. Close this dialog and try again.")
            )
            return
        self.accept()

    def _feature_extent(self):  # QgsRectangle | None
        hit = self._selected_hit()
        layer = self._layer
        if hit is None or layer is None or sip.isdeleted(layer):
            self._source_lost("layer")
            return None
        fit = self.fit_check.isEnabled() and self.fit_check.isChecked()
        try:
            return core.feature_destination(self.target, layer, hit.fid, fit)
        except LookupError as error:
            self._source_lost(str(error))
            return None

    def _source_lost(self, reason: str) -> None:
        """The layer or the feature changed since the search: the result is
        dropped and nothing moves."""
        self.results.blockSignals(True)
        self.results.clearSelection()
        self.results.blockSignals(False)
        self._results_fresh = False
        self.results.setEnabled(False)
        self.fit_check.setEnabled(False)
        self.fit_check.setChecked(False)
        message = {
            "layer": tr("This layer is no longer available."),
            "feature": tr("This feature is no longer available."),
            "location": tr("This feature has no location to display."),
        }.get(reason, tr("This feature is no longer available."))
        self.feature_message.setText(message)
        self._update_go()

    def _apply_message(self, reason: str) -> str:
        return {
            "frame": tr("This map frame is no longer available."),
            "atlas": tr(
                "This map follows the atlas. "
                "Turn off the map series to choose a fixed location."
            ),
            "data-defined": tr(
                "This map's view is set by an expression, "
                "so a location cannot be chosen here."
            ),
        }.get(reason, tr("No view could be made for this location."))

    def _fail(self, message: str) -> None:
        self._apply_error = message
        self.footer_status.setText(message)

    # ---- keys ---------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802
        if event is not None and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focus = self.focusWidget()
            event.accept()
            if focus in (self.first_edit, self.second_edit):
                self.submit_coordinates()
            elif isinstance(focus, QPushButton) and focus.isEnabled():
                focus.click()
            # Anywhere else Enter does nothing: no button is a default button,
            # so the search box can only ever search.
            return
        super().keyPressEvent(event)
