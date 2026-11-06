"""The export, as a Processing algorithm.

Issue #29's architecture table: *"a Processing provider makes export usable
outside the main dialog and supports later batch/model workflows."*

The load-bearing rule is that this is **not a second export implementation**.
It reads the project with the same `prepare_from_state`, from the dialog's
choices saved in the project, and produces the artifact with the same
`build_artifact` as the dialog. qgis2web's cost is that its dialog and its
algorithm drifted; the only thing this file owns is turning Processing
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

from ..core.cancellation import ExportCancelledError
from ..core.export_ir import OutputMode
from ..core.fidelity_report import FidelityReportBuilder
from ..core.license_policy import default_policy, report_verdict
from ..core.project_reader import PendingProject, finish_project, prepare_from_state
from ..core.settings import DialogState, load_state, resolve_license_key
from ..core.translatable import N_
from ..packaging.artifact_builder import build_artifact
from ..packaging.runtime_manager import (
    RUNTIME_DOWNLOAD_SIZE,
    RuntimeNotAcceptedError,
    RuntimeUnavailableError,
)
from ..translation import plural, tr
from ..writers.onlymap_writer import ExportBlockedError, OnlyMapWriter

# Where the progress bar stands when reading ends and when the finished artifact
# starts being put in place (the last point Processing's Cancel still stops it).
READ_SHARE = 70
COMMIT_AT = 95

# Order is the user-facing enum order, so it is part of the algorithm's saved
# parameters. Append, never reorder: a stored model would silently change mode.
OUTPUT_MODES = (
    (OutputMode.STANDALONE_HTML, N_("Standalone HTML (one file)")),
    (OutputMode.SHARE_ZIP, N_("Share ZIP")),
    (OutputMode.FOLDER, N_("Folder")),
)

# The map controls a run can set, each to the `DialogState` field it overrides.
WIDGET_PARAMETERS = (
    ("LEGEND", "show_legend"),
    ("LAYER_SWITCHER", "show_layer_switcher"),
    ("ZOOM_CONTROLS", "show_zoom_controls"),
    ("SCALE_BAR", "show_scale_bar"),
    ("MEASURE", "show_measure"),
)


class ExportProjectAlgorithm(QgsProcessingAlgorithm):
    """Export the current project to a portable OnlyMap web map."""

    OUTPUT = "OUTPUT"
    MODE = "MODE"
    TITLE = "TITLE"
    LICENSE_KEY = "LICENSE_KEY"
    LEGEND = "LEGEND"
    LAYER_SWITCHER = "LAYER_SWITCHER"
    ZOOM_CONTROLS = "ZOOM_CONTROLS"
    SCALE_BAR = "SCALE_BAR"
    MEASURE = "MEASURE"
    EXPORT_BLOCKED = "EXPORT_BLOCKED"

    def createInstance(self):  # noqa: N802 -- QGIS Processing API
        return ExportProjectAlgorithm()

    def name(self) -> str:
        return "exportwebmap"

    def displayName(self) -> str:  # noqa: N802
        return tr("Export to OnlyMap web map")

    def group(self) -> str:
        return tr("Web map")

    def groupId(self) -> str:  # noqa: N802
        return "webmap"

    def shortHelpString(self) -> str:  # noqa: N802
        return tr(
            "Exports the current QGIS project to a portable OnlyMap web map.\n\n"
            "The default output is a single HTML file another person can "
            "double-click and use without QGIS. Exported maps send one "
            "anonymous usage report to NIKA when they load, and nothing "
            "else — see docs/privacy.md.\n\n"
            "Anything that cannot be translated exactly is reported in the log "
            "rather than dropped silently. If part of the project cannot be "
            "read at all (a Blocked item, such as a layer whose data source is "
            "missing), the export stops and lists the problems, unless "
            "'Export even if some items are blocked' is ticked - then the map "
            "is written without them and each one is logged as a warning. "
            "For the full fidelity report, use Web → QGIS2WebMap by NIKA.\n\n"
            "The choices made in that dialog and saved with the project - which "
            "layers to include, each layer's popup, labels and clustering, the "
            "basemap and the rest of the Appearance tab - are used here too. A "
            "title or control set here replaces the saved one; this window "
            "always sends its control ticks, while a script or qgis_process "
            "run that leaves one out keeps the saved choice.\n\n"
            "<b>First run on a new computer:</b> open Web → QGIS2WebMap by NIKA "
            "and export once from there. It shows the OnlyMap runtime licence "
            "and installs the runtime ({size}, once per "
            "computer). This "
            "algorithm cannot do that itself - there is no way to show a "
            "licence during a batch run - so until then it stops immediately "
            "and says so. Every run after that works offline."
        ).format(size=RUNTIME_DOWNLOAD_SIZE)

    def initAlgorithm(self, config=None) -> None:  # noqa: N802
        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODE,
                tr("Output mode"),
                options=[tr(label) for _, label in OUTPUT_MODES],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.TITLE,
                tr("Map title (blank uses the saved map name, then the project title)"),
                optional=True,
            )
        )
        # Without this a batch export ran on the free plan even for a user who
        # had pasted a key into the dialog, because nothing here read it. Blank
        # is the normal case: it falls back to the environment, then to the key
        # the dialog stored.
        self.addParameter(
            QgsProcessingParameterString(
                self.LICENSE_KEY,
                tr(
                    "OnlyMap licence key (blank uses ONLYMAP_LICENSE_KEY, then any "
                    "key already saved on this computer; only needed for a map "
                    "you publish to a web server)"
                ),
                optional=True,
            )
        )
        for key, label in (
            (self.LEGEND, N_("Legend")),
            (self.LAYER_SWITCHER, N_("Layer switcher")),
            (self.ZOOM_CONTROLS, N_("Zoom controls")),
            (self.SCALE_BAR, N_("Scale bar")),
            (self.MEASURE, N_("Measure tool")),
        ):
            self.addParameter(QgsProcessingParameterBoolean(key, tr(label), True))

        # Off by default: a batch run has nobody to ask, so the safe answer to
        # "part of the project could not be read" stays a refusal. Ticking it is
        # the batch equivalent of the dialog's "export anyway".
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
                tr("Exported map"),
                fileFilter=tr("HTML files (*.html);;ZIP files (*.zip)"),
            )
        )

    RUNTIME_GUIDANCE = N_(
        "Open Web -> QGIS2WebMap by NIKA once and run an export from the "
        "dialog. It shows the OnlyMap licence and installs the runtime, after "
        "which this algorithm works offline. On a machine with no internet "
        "access, install the runtime pack and set ONLYMAP_RUNTIME_DIR."
    )

    CLIP_REFUSAL = N_(
        "This project's web map is set to export only the features in the "
        "QGIS view, and a Processing run has no QGIS view to clip to, so "
        "nothing was exported rather than every feature. To export the whole "
        "layers, untick 'Export only the features in this view' in the "
        "QGIS2WebMap dialog and save the project."
    )

    @classmethod
    def _check_runtime_first(cls, feedback=None) -> None:
        """Refuse before reading the project, not after translating it.

        The licence gate is a precondition, and a first run used to discover it
        only at the write step - after 50 seconds of reading layers and
        translating symbology, all of it discarded. The work was never going to
        be usable, and a failure at the end of a long job reads as a crash where
        the same message up front reads as a prompt.

        A runtime whose licence was accepted but which is not on disk yet (a
        plugin update pinning a newer one) is downloaded here too, through
        QGIS's network settings as the dialog does: the write step's own
        download knows nothing of a proxy set only in QGIS, and would fail
        after the whole read.
        """
        from ..packaging.runtime_manager import FetchingRuntime, default_provider
        from ..ui.runtime_setup import make_qgis_downloader

        provider = default_provider()
        try:
            provider.preflight()
            if isinstance(provider, FetchingRuntime) and not provider.is_core_cached():
                provider.fetch(downloader=make_qgis_downloader(feedback))
        except OSError as exc:
            raise QgsProcessingException(str(exc)) from exc
        except RuntimeNotAcceptedError as exc:
            # Headless: there is no one to show a licence to, and accepting a
            # commercial licence on a user's behalf from a batch run is not a
            # thing this plugin will do. Point at the interactive path instead.
            raise QgsProcessingException(
                f"{exc}\n\n{tr(cls.RUNTIME_GUIDANCE)}"
            ) from exc
        except RuntimeUnavailableError as exc:
            raise QgsProcessingException(str(exc)) from exc

    _prepared: tuple[PendingProject, FidelityReportBuilder] | None = None

    def _mode(self, parameters, context) -> OutputMode:
        return OUTPUT_MODES[self.parameterAsEnum(parameters, self.MODE, context)][0]

    def _state(self, parameters, context) -> DialogState:
        """The dialog's choices saved in the project, under the parameters given.

        Saved, so a batch run exports what the dialog would for the same
        project: its layers, popups, labels, basemap and Appearance. A
        parameter that is passed wins over what was saved; one that is left out
        (a script or `qgis_process` naming only `OUTPUT`) keeps it. The output
        mode is always this run's own, since `OUTPUT` is named for it.
        """
        state = load_state(context.project())
        state.output_mode = self._mode(parameters, context)
        title = (self.parameterAsString(parameters, self.TITLE, context) or "").strip()
        if title:
            state.map_name = title
        for key, attribute in WIDGET_PARAMETERS:
            if parameters.get(key) is not None:
                setattr(
                    state, attribute, self.parameterAsBool(parameters, key, context)
                )
        return state

    def _prepare(
        self, parameters, context
    ) -> tuple[PendingProject, FidelityReportBuilder]:
        """Read the live project. Must run on the thread that owns it."""
        state = self._state(parameters, context)
        if state.clip_to_extent:
            # No canvas here, and a clip is a promise about what leaves the
            # computer: exporting every feature instead would publish the ones
            # the clip was chosen to keep out. So the run refuses.
            raise QgsProcessingException(tr(self.CLIP_REFUSAL))
        report = FidelityReportBuilder()
        # No canvas here: an extent set to the QGIS view is reported as
        # unreadable and the map opens on its data, as for a dialog without one.
        pending = prepare_from_state(context.project(), report, state)
        return pending, report

    def prepareAlgorithm(self, parameters, context, feedback):  # noqa: N802
        """Runs on the main thread, before `processAlgorithm` goes to a worker.

        Everything that needs the live project and its layers happens here: a
        Processing algorithm without `NoThreading` runs `processAlgorithm` on a
        background thread, where QGIS says not to touch the GUI-owned project.
        What is stored holds no project or layer, only what the worker reads.
        """
        self._check_runtime_first(feedback)
        self._prepared = self._prepare(parameters, context)
        return True

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        try:
            return self._process(parameters, context, feedback)
        except ExportCancelledError:
            # Stopped before anything was committed: the destination is as it
            # was. Processing reads the cancel off the feedback.
            return {}

    def _process(self, parameters, context, feedback):
        if self._prepared is None:
            # Called without `prepareAlgorithm` (a script, a test): same thread
            # as the caller, which is the one that owns the project.
            self._check_runtime_first(feedback)
            self._prepared = self._prepare(parameters, context)
        pending, report = self._prepared
        self._prepared = None
        mode = self._mode(parameters, context)
        destination = Path(self.parameterAsFileOutput(parameters, self.OUTPUT, context))

        license_key = resolve_license_key(
            self.parameterAsString(parameters, self.LICENSE_KEY, context)
        )
        writer = OnlyMapWriter(license_policy=default_policy(license_key))

        def on_layer(done: int, total: int, _name: str) -> None:
            if feedback.isCanceled():
                raise ExportCancelledError
            feedback.setProgress(READ_SHARE * done / total if total else 0)

        export = finish_project(pending, report, on_layer)
        # The caps are the recipient's experience, so a batch run has to record
        # them where an audit can find them. Processing has no Fidelity tab.
        report_verdict(writer.license_policy.evaluate(export), report)

        # Same rule as the dialog: informed breakage is the user's decision,
        # undiscovered breakage is a defect. Processing has no Fidelity tab, so
        # the report goes to the log where a batch run can still be audited.
        self._report(report, feedback)

        # Processing offers one file-destination widget for all three modes, so
        # in folder mode the chosen path can already exist as a file - which
        # `mkdir` raises on with a message that says nothing useful.
        if mode is OutputMode.FOLDER and destination.is_file():
            raise QgsProcessingException(
                tr(
                    "'{path}' is an existing file, and folder mode needs a "
                    "directory. Choose a path that is a folder or does not exist "
                    "yet."
                ).format(path=destination)
            )

        if not export.exportable_layers:
            raise QgsProcessingException(
                tr(
                    "There is nothing to export. Add at least one vector or raster "
                    "layer with data to the project."
                )
            )
        # Still a refusal here by default, unlike the dialog, which warns and
        # lets the user decide: a batch run has nobody to ask, and writing a map
        # with a layer quietly missing is the outcome a warning exists to
        # prevent. `EXPORT_BLOCKED` is the user deciding in advance - and then
        # it is not quiet: every missing item is a warning in the log.
        if export.blocking_items:
            if not self.parameterAsBool(parameters, self.EXPORT_BLOCKED, context):
                raise QgsProcessingException(
                    tr(
                        "Some of the project could not be read, so the map would "
                        "be missing it:"
                    )
                    + "\n"
                    + "\n".join(item.detail for item in export.blocking_items)
                    + "\n\n"
                    + tr(
                        "Tick 'Export even if some items are blocked' to export "
                        "the rest anyway."
                    )
                )
            feedback.pushWarning(
                plural(
                    len(export.blocking_items),
                    "Exporting anyway, as requested: {n} blocked item will be "
                    "missing from the map.",
                    "Exporting anyway, as requested: {n} blocked items will be "
                    "missing from the map.",
                )
            )
            for item in export.blocking_items:
                feedback.pushWarning(
                    tr("Missing from the map - {subject}: {detail}").format(
                        subject=item.subject, detail=item.detail
                    )
                )

        # `build_artifact`, not a local write-then-export pair: it is what stages
        # through a temporary directory (so a batch run leaves no half-written
        # build folder beside the user's output) and what writes the recipient's
        # README. Reimplementing those here is exactly the dialog/algorithm drift
        # this module exists to avoid.
        def on_raster_progress(done: int, total: int | None) -> None:
            if total:
                feedback.setProgress(
                    READ_SHARE + (COMMIT_AT - READ_SHARE) * done / total
                )

        try:
            result, outcome = build_artifact(
                export,
                destination,
                mode=mode,
                writer=writer,
                on_progress=on_raster_progress,
                should_cancel=feedback.isCanceled,
                on_commit=lambda: feedback.setProgress(COMMIT_AT),
            )
        except ExportBlockedError as exc:
            raise QgsProcessingException(
                tr("The export was blocked so it could not write a broken map:")
                + "\n"
                + "\n".join(exc.reasons)
            ) from exc
        except RuntimeNotAcceptedError as exc:
            # Still guarded here as well as up front: acceptance can be revoked,
            # or the cache cleared, between the preflight and the write.
            raise QgsProcessingException(
                f"{exc}\n\n{tr(self.RUNTIME_GUIDANCE)}"
            ) from exc
        except RuntimeUnavailableError as exc:
            raise QgsProcessingException(str(exc)) from exc

        for warning in result.warnings:
            feedback.pushWarning(warning)

        feedback.pushInfo(outcome.summary())
        feedback.setProgress(100)
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
