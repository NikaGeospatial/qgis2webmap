"""The export, as a Processing algorithm.

Issue #29's architecture table: *"a Processing provider makes export usable
outside the main dialog and supports later batch/model workflows."*

The load-bearing rule is that this is **not a second export implementation**.
It reads the project with the same `read_project` and produces the artifact with
the same `build_artifact` as the dialog. qgis2web's cost is that its dialog and
its algorithm drifted; the only thing this file owns is turning Processing
parameters into the arguments those calls already take.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterString,
)

from ..core.export_ir import ExportSettings, OutputMode
from ..core.fidelity_report import FidelityReportBuilder
from ..core.project_reader import read_project
from ..packaging.artifact_builder import build_artifact
from ..packaging.runtime_manager import (
    RUNTIME_DOWNLOAD_SIZE,
    RuntimeNotAcceptedError,
    RuntimeUnavailableError,
)
from ..writers.onlymap_writer import ExportBlockedError

# Order is the user-facing enum order, so it is part of the algorithm's saved
# parameters. Append, never reorder: a stored model would silently change mode.
OUTPUT_MODES = (
    (OutputMode.STANDALONE_HTML, "Standalone HTML (one file)"),
    (OutputMode.SHARE_ZIP, "Share ZIP"),
    (OutputMode.FOLDER, "Folder"),
)


class ExportProjectAlgorithm(QgsProcessingAlgorithm):
    """Export the current project to a portable OnlyMap web map."""

    OUTPUT = "OUTPUT"
    MODE = "MODE"
    TITLE = "TITLE"
    LEGEND = "LEGEND"
    LAYER_SWITCHER = "LAYER_SWITCHER"
    ZOOM_CONTROLS = "ZOOM_CONTROLS"
    SCALE_BAR = "SCALE_BAR"

    def createInstance(self):  # noqa: N802 -- QGIS Processing API
        return ExportProjectAlgorithm()

    def name(self) -> str:
        return "exportwebmap"

    def displayName(self) -> str:  # noqa: N802
        return "Export to OnlyMap web map"

    def group(self) -> str:
        return "Web map"

    def groupId(self) -> str:  # noqa: N802
        return "webmap"

    def shortHelpString(self) -> str:  # noqa: N802
        return (
            "Exports the current QGIS project to a portable OnlyMap web map.\n\n"
            "The default output is a single HTML file another person can "
            "double-click and use without QGIS. Exported maps contain no "
            "tracking and make no network requests.\n\n"
            "Anything that cannot be translated exactly is reported in the log "
            "rather than dropped silently. For the full fidelity report, and "
            "for per-layer popup and label choices, use "
            "Web → QGIS2WebMap by NIKA.\n\n"
            "<b>First run on a new computer:</b> open Web → QGIS2WebMap by NIKA "
            "and export once from there. It shows the OnlyMap runtime licence "
            f"and installs the runtime ({RUNTIME_DOWNLOAD_SIZE}, once per "
            "computer). This "
            "algorithm cannot do that itself - there is no way to show a "
            "licence during a batch run - so until then it stops immediately "
            "and says so. Every run after that works offline."
        )

    def initAlgorithm(self, config=None) -> None:  # noqa: N802
        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE,
                "Output mode",
                options=[label for _, label in OUTPUT_MODES],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.TITLE, "Map title (blank uses the project title)", optional=True
            )
        )
        for key, label in (
            (self.LEGEND, "Legend"),
            (self.LAYER_SWITCHER, "Layer switcher"),
            (self.ZOOM_CONTROLS, "Zoom controls"),
            (self.SCALE_BAR, "Scale bar"),
        ):
            self.addParameter(QgsProcessingParameterBoolean(key, label, True))

        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.OUTPUT,
                "Exported map",
                fileFilter="HTML files (*.html);;ZIP files (*.zip)",
            )
        )

    RUNTIME_GUIDANCE = (
        "Open Web -> QGIS2WebMap by NIKA once and run an export from the "
        "dialog. It shows the OnlyMap licence and installs the runtime, after "
        "which this algorithm works offline. On a machine with no internet "
        "access, install the runtime pack and set ONLYMAP_RUNTIME_DIR."
    )

    @classmethod
    def _check_runtime_first(cls) -> None:
        """Refuse before reading the project, not after translating it.

        The licence gate is a precondition, and a first run used to discover it
        only at the write step - after 50 seconds of reading layers and
        translating symbology, all of it discarded. The work was never going to
        be usable, and a failure at the end of a long job reads as a crash where
        the same message up front reads as a prompt.
        """
        from ..packaging.runtime_manager import default_provider

        try:
            default_provider().preflight()
        except RuntimeNotAcceptedError as exc:
            # Headless: there is no one to show a licence to, and accepting a
            # commercial licence on a user's behalf from a batch run is not a
            # thing this plugin will do. Point at the interactive path instead.
            raise QgsProcessingException(f"{exc}\n\n{cls.RUNTIME_GUIDANCE}") from exc
        except RuntimeUnavailableError as exc:
            raise QgsProcessingException(str(exc)) from exc

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        self._check_runtime_first()
        mode = OUTPUT_MODES[self.parameterAsEnum(parameters, self.MODE, context)][0]
        destination = Path(self.parameterAsFileOutput(parameters, self.OUTPUT, context))
        title = (
            self.parameterAsString(parameters, self.TITLE, context) or ""
        ).strip() or None

        settings = ExportSettings(
            output_mode=mode,
            show_legend=self.parameterAsBool(parameters, self.LEGEND, context),
            show_layer_switcher=self.parameterAsBool(
                parameters, self.LAYER_SWITCHER, context
            ),
            show_zoom_controls=self.parameterAsBool(
                parameters, self.ZOOM_CONTROLS, context
            ),
            show_scale_bar=self.parameterAsBool(parameters, self.SCALE_BAR, context),
        )

        report = FidelityReportBuilder()
        export = read_project(
            context.project(), report, settings=settings, title_override=title
        )

        # Same rule as the dialog: informed breakage is the user's decision,
        # undiscovered breakage is a defect. Processing has no Fidelity tab, so
        # the report goes to the log where a batch run can still be audited.
        self._report(report, feedback)

        # Processing offers one file-destination widget for all three modes, so
        # in folder mode the chosen path can already exist as a file - which
        # `mkdir` raises on with a message that says nothing useful.
        if mode is OutputMode.FOLDER and destination.is_file():
            raise QgsProcessingException(
                f"'{destination}' is an existing file, and folder mode needs a "
                "directory. Choose a path that is a folder or does not exist yet."
            )

        if not export.is_exportable:
            raise QgsProcessingException(
                "There is nothing to export. Add at least one vector layer with "
                "features to the project."
            )

        # `build_artifact`, not a local write-then-export pair: it is what stages
        # through a temporary directory (so a batch run leaves no half-written
        # build folder beside the user's output) and what writes the recipient's
        # README. Reimplementing those here is exactly the dialog/algorithm drift
        # this module exists to avoid.
        try:
            result, outcome = build_artifact(export, destination, mode=mode)
        except ExportBlockedError as exc:
            raise QgsProcessingException(
                "The export was blocked so it could not write a broken map:\n"
                + "\n".join(exc.reasons)
            ) from exc
        except RuntimeNotAcceptedError as exc:
            # Still guarded here as well as up front: acceptance can be revoked,
            # or the cache cleared, between the preflight and the write.
            raise QgsProcessingException(f"{exc}\n\n{self.RUNTIME_GUIDANCE}") from exc
        except RuntimeUnavailableError as exc:
            raise QgsProcessingException(str(exc)) from exc

        for warning in result.warnings:
            feedback.pushWarning(warning)

        feedback.pushInfo(outcome.summary())
        return {self.OUTPUT: str(outcome.path)}

    @staticmethod
    def _report(report: FidelityReportBuilder, feedback) -> None:
        """Push every fidelity entry to the Processing log."""
        for item in report.items:
            line = f"[{item.status.value}] {item.subject}: {item.detail}"
            if item.status.value in {"unsupported", "blocked"}:
                feedback.pushWarning(line)
            else:
                feedback.pushInfo(line)
