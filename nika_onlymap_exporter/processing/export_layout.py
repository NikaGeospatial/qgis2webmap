"""The cartograph export, as a Processing algorithm.

Same rule as `export_project.py`: **not a second implementation**. It calls
`core/cartograph_export.export_layout_to_cartograph`, the function the
dialog's autosave runs in two halves, and owns only the translation from
Processing parameters to that call's arguments.

**No threading.** A cartograph build reads, hides and renders the print
layout's own items, which must happen on the GUI thread (see the
`core/cartograph_export.py` docstring). QGIS's own layout export algorithms
carry `FlagNoThreading` for the same reason, and so does this one.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

from qgis.core import (
    Qgis,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterLayout,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)

from ..core.atomic_write import write_text_atomic
from ..core.cartograph_export import export_layout_to_cartograph
from ..core.fidelity_report import FidelityReportBuilder
from ..core.layout_reader import default_mode_for_map
from ..core.license_policy import default_policy
from ..core.settings import (
    DEFAULT_STATIC_DPI,
    resolve_license_key,
)
from ..packaging.runtime_manager import (
    RuntimeNotAcceptedError,
    RuntimeUnavailableError,
)
from ..ui.i18n import N_, tr
from ..writers.onlymap_writer import OnlyMapWriter
from .export_project import ExportProjectAlgorithm

# Order is part of a saved model's parameters. Append, never reorder.
FRAME_MODES = (
    ("auto", N_("Automatic (live where it matches QGIS, static otherwise)")),
    ("live", N_("All live")),
    ("static", N_("All static")),
)


class ExportLayoutAlgorithm(QgsProcessingAlgorithm):
    """Export a print layout to a portable, printable cartograph."""

    LAYOUT = "LAYOUT"
    FRAMES = "FRAMES"
    DPI = "DPI"
    TITLE = "TITLE"
    EXPORT_BLOCKED = "EXPORT_BLOCKED"
    OUTPUT = "OUTPUT"

    def createInstance(self):  # noqa: N802 -- QGIS Processing API
        return ExportLayoutAlgorithm()

    def name(self) -> str:
        return "exportcartograph"

    def displayName(self) -> str:  # noqa: N802
        return tr("Export print layout to cartograph (Beta)")

    def group(self) -> str:
        return tr("Cartograph")

    def groupId(self) -> str:  # noqa: N802
        return "cartograph"

    def flags(self):
        return super().flags() | Qgis.ProcessingAlgorithmFlag.NoThreading

    def shortHelpString(self) -> str:  # noqa: N802
        return tr(
            "Exports a QGIS print layout to a cartograph: one portable HTML "
            "file that opens by double-click and prints at true page size "
            "from the browser.\n\n"
            "Map items become live frames (pan and zoom, in Web Mercator or in "
            "the map's own CRS) or static frames (a pixel-exact image in the "
            "layout's own CRS). By default a map is live when the browser can "
            "draw it as QGIS does (EPSG:3857 or EPSG:4326, or any other "
            "unrotated map of vector layers or image tiles) and static "
            "otherwise; 'Frames' overrides that for every map on the "
            "layout.\n\n"
            "Anything that cannot be translated exactly is reported in the log. "
            "If an item is Blocked (for example a second page, which is not "
            "exported), the export stops unless 'Export even if some items are "
            "blocked' is ticked.\n\n"
            "<b>First run on a new computer:</b> open Web → QGIS2WebMap by NIKA "
            "and export once from there, which installs the OnlyMap runtime. "
            "Until then this algorithm stops and says so."
        )

    def initAlgorithm(self, config=None) -> None:  # noqa: N802
        self.addParameter(QgsProcessingParameterLayout(self.LAYOUT, tr("Print layout")))
        self.addParameter(
            QgsProcessingParameterEnum(
                self.FRAMES,
                tr("Frames"),
                options=[tr(label) for _, label in FRAME_MODES],
                defaultValue=0,
            )
        )
        dpi = QgsProcessingParameterNumber(
            self.DPI,
            tr("Static frame resolution (dpi)"),
            type=Qgis.ProcessingNumberParameterType.Integer,
            defaultValue=DEFAULT_STATIC_DPI,
            minValue=72,
            maxValue=600,
        )
        self.addParameter(dpi)
        self.addParameter(
            QgsProcessingParameterString(
                self.TITLE,
                tr("Title (blank uses the project title)"),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.EXPORT_BLOCKED,
                tr("Export even if some items are blocked"),
                False,
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT,
                tr("Cartograph"),
                fileFilter=tr("HTML files (*.html)"),
            )
        )

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        ExportProjectAlgorithm._check_runtime_first()
        project = context.project()
        layout = self.parameterAsLayout(parameters, self.LAYOUT, context)
        if layout is None:
            raise QgsProcessingException(tr("Choose a print layout to export."))
        destination = Path(self.parameterAsFileOutput(parameters, self.OUTPUT, context))
        if destination.suffix.lower() != ".html":
            destination = destination.with_suffix(".html")
        title = (
            self.parameterAsString(parameters, self.TITLE, context) or ""
        ).strip() or None
        dpi = self.parameterAsInt(parameters, self.DPI, context)
        choice = FRAME_MODES[self.parameterAsEnum(parameters, self.FRAMES, context)][0]
        frame_modes = _frame_modes(layout, choice)

        from ..packaging.runtime_manager import default_provider

        try:
            runtime = default_provider().load()
        except RuntimeNotAcceptedError as exc:
            raise QgsProcessingException(
                f"{exc}\n\n{tr(ExportProjectAlgorithm.RUNTIME_GUIDANCE)}"
            ) from exc
        except RuntimeUnavailableError as exc:
            raise QgsProcessingException(str(exc)) from exc

        report = FidelityReportBuilder()
        try:
            html = export_layout_to_cartograph(
                layout,
                project,
                runtime,
                report,
                frame_modes=frame_modes,
                static_dpi=dpi,
                title_override=title,
                # Same key resolution as the web-map algorithm: environment,
                # then whatever the dialog stored on this computer.
                writer=OnlyMapWriter(
                    license_policy=default_policy(resolve_license_key())
                ),
            )
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc

        ExportProjectAlgorithm._report(report, feedback)
        blocked = [item for item in report.items if item.status.value == "blocked"]
        if blocked and not self.parameterAsBool(
            parameters, self.EXPORT_BLOCKED, context
        ):
            raise QgsProcessingException(
                tr("Some of the layout cannot be exported as it is:")
                + "\n"
                + "\n".join(f"{item.subject}: {item.detail}" for item in blocked)
                + "\n\n"
                + tr(
                    "Tick 'Export even if some items are blocked' to export "
                    "the rest anyway."
                )
            )

        _write_atomic(destination, html)
        feedback.pushInfo(
            tr("Wrote {path} ({size} KB).").format(
                path=destination, size=max(1, destination.stat().st_size // 1024)
            )
        )
        return {self.OUTPUT: str(destination)}


def _frame_modes(layout, choice: str) -> dict[str, str]:
    """Each map item's mode, keyed by uuid, for the chosen policy."""
    from qgis.core import QgsLayoutItemMap

    modes: dict[str, str] = {}
    for item in layout.items():
        if not isinstance(item, QgsLayoutItemMap):
            continue
        if choice == "auto":
            modes[item.uuid()] = default_mode_for_map(item)
        else:
            modes[item.uuid()] = choice
    return modes


def _write_atomic(path: Path, content: str) -> None:
    try:
        write_text_atomic(path, content)
    except OSError as exc:
        raise QgsProcessingException(
            tr("Could not write {path}: {error}").format(path=path, error=exc)
        ) from exc
