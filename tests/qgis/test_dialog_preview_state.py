"""Settings reaching the preview.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations


def _dialog(project, make_memory_layer):
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    class FakeIface:
        def mainWindow(self):  # noqa: N802 - mirrors the QGIS interface
            return None

    project.addMapLayer(make_memory_layer("roads", features=[("a", [1.0, 2.0])]))
    return MainDialog(FakeIface(), None)


def test_a_reused_read_carries_the_settings_of_now(
    qgis_app, project, make_memory_layer
) -> None:
    """A colour changed after the last read used to never reach the page."""
    dialog = _dialog(project, make_memory_layer)
    dialog._cached_export, dialog._cached_report = dialog._read_current_project()
    dialog._cached_signature = dialog.state.data_snapshot()
    dialog.state.widget_background = "#40e0d0"

    seen = []
    assert dialog._ensure_export(lambda export, _report: seen.append(export), "x")
    background = seen[0].settings.widget_background
    assert background is not None
    assert background.as_css() == "#40e0d0"
    dialog.close()


# ---- the cache follows what QGIS holds now --------------------------------


class _FakeCanvas:
    """The two calls `extent_from_canvas` makes, over a rectangle tests move."""

    def __init__(self) -> None:
        from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

        self.rectangle = QgsRectangle(0.0, 0.0, 10.0, 10.0)
        self._crs = QgsCoordinateReferenceSystem("EPSG:4326")

    def extent(self):
        return self.rectangle

    def mapSettings(self):  # noqa: N802 - mirrors QgsMapCanvas
        canvas = self

        class _Settings:
            def destinationCrs(self):  # noqa: N802
                return canvas._crs

        return _Settings()


def _live_dialog(project, make_memory_layer, qgis_app):
    """A dialog over a four-point layer, with a canvas the test can pan."""
    from nika_onlymap_exporter.ui.main_dialog import MainDialog

    canvas = _FakeCanvas()

    class Iface:
        def mainWindow(self):  # noqa: N802
            return None

        def mapCanvas(self):  # noqa: N802
            return canvas

    layer = make_memory_layer(
        "pts",
        fields="name:string",
        features=[("a", [1.0, 1.0]), ("b", [2.0, 2.0]), ("c", [3.0, 3.0])],
    )
    project.addMapLayer(layer)
    dialog = MainDialog(Iface(), None)
    return dialog, layer, canvas


def _export_through_dialog(dialog, qgis_app):
    """Export the way the buttons do: `_ensure_export`, cached or worker read.

    Returns the exported layer's (name, coordinates) pairs, parsed from the
    GeoJSON the writer would be handed, plus whether a worker read happened.
    """
    import time

    seen = []
    reads = []
    original = dialog._read_current_project

    def counting(progress=None):
        reads.append(1)
        return original(progress)

    dialog._read_current_project = counting
    assert dialog._ensure_export(lambda export, _report: seen.append(export), "x")
    deadline = time.monotonic() + 60
    while not seen and time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.02)
    assert seen
    # The worker thread is released once the success slot has run.
    while dialog._job is not None and time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.02)
    dialog._read_current_project = original
    features = [
        (f["properties"].get("name"), tuple(f["geometry"]["coordinates"]))
        for layer in seen[0].layers
        for f in layer.geojson["features"]
    ]
    return sorted(features, key=str), bool(reads)


def _qgis_truth(layer):
    return sorted(
        (
            f["name"],
            (f.geometry().asPoint().x(), f.geometry().asPoint().y()),
        )
        for f in layer.getFeatures()
    )


def _mutate_attribute(layer):
    layer.startEditing()
    first = next(layer.getFeatures())
    layer.changeAttributeValue(first.id(), 0, "edited")


def _mutate_geometry(layer):
    from qgis.core import QgsGeometry, QgsPointXY

    layer.startEditing()
    first = next(layer.getFeatures())
    layer.changeGeometry(first.id(), QgsGeometry.fromPointXY(QgsPointXY(7.0, 7.0)))


def _mutate_add(layer):
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY

    layer.startEditing()
    feature = QgsFeature(layer.fields())
    feature.setAttribute(0, "new")
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(8.0, 8.0)))
    layer.addFeature(feature)


def _mutate_delete(layer):
    layer.startEditing()
    layer.deleteFeature(next(layer.getFeatures()).id())


def _mutate_rollback(layer):
    _mutate_attribute(layer)
    layer.rollBack()


def _mutate_subset(layer):
    assert layer.setSubsetString("\"name\" = 'a'")


def _mutate_source(layer):
    layer.setDataSource(
        "Point?crs=EPSG:4326&field=name:string&uid={11111111-1111-1111-1111-111111111111}",
        layer.name(),
        "memory",
    )
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY

    feature = QgsFeature(layer.fields())
    feature.setAttribute(0, "elsewhere")
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(5.0, 5.0)))
    layer.dataProvider().addFeatures([feature])


MUTATIONS = {
    "attribute": _mutate_attribute,
    "geometry": _mutate_geometry,
    "add": _mutate_add,
    "delete": _mutate_delete,
    "rollback": _mutate_rollback,
    "subset": _mutate_subset,
    "source": _mutate_source,
}


def test_each_edit_reaches_the_next_export(
    qgis_app, project, make_memory_layer
) -> None:
    """A read taken before an edit must not be what the next export carries."""
    for name, mutate in MUTATIONS.items():
        project.clear()
        dialog, layer, _canvas = _live_dialog(project, make_memory_layer, qgis_app)
        before, _ = _export_through_dialog(dialog, qgis_app)
        assert before == _qgis_truth(layer), name

        mutate(layer)
        qgis_app.processEvents()  # the watcher coalesces on the event loop

        after, read_again = _export_through_dialog(dialog, qgis_app)
        assert read_again, name
        assert after == _qgis_truth(layer), name
        if name != "rollback":
            # A rollback returns to what was read, so only the re-read differs.
            assert after != before, name
        layer.rollBack()
        dialog.close()


def test_every_content_signal_marks_the_cache_stale(
    qgis_app, project, make_memory_layer
) -> None:
    """Edits that create no feature difference to compare still invalidate,
    such as a schema change: the signal alone must reach the dialog."""
    from qgis.core import QgsField
    from qgis.PyQt.QtCore import QMetaType

    dialog, layer, _canvas = _live_dialog(project, make_memory_layer, qgis_app)
    _export_through_dialog(dialog, qgis_app)
    assert dialog._cached_export is not None
    layer.startEditing()
    layer.addAttribute(QgsField("extra", QMetaType.Type.QString))
    qgis_app.processEvents()
    assert dialog._cached_export is None
    _export_through_dialog(dialog, qgis_app)
    layer.deleteAttribute(layer.fields().indexOf("extra"))
    qgis_app.processEvents()
    assert dialog._cached_export is None
    layer.rollBack()
    dialog.close()


def test_a_pan_rebuilds_only_when_the_export_uses_the_view(
    qgis_app, project, make_memory_layer
) -> None:
    from qgis.core import QgsRectangle

    from nika_onlymap_exporter.core.export_ir import ExtentSource

    dialog, _layer, canvas = _live_dialog(project, make_memory_layer, qgis_app)
    _export_through_dialog(dialog, qgis_app)

    # Full-extent mode: the view is irrelevant, so a pan reuses the read.
    canvas.rectangle = QgsRectangle(20.0, 20.0, 30.0, 30.0)
    _, rebuilt = _export_through_dialog(dialog, qgis_app)
    assert not rebuilt

    # Current-view mode: the same pan must reach the page.
    dialog.state.extent_source = ExtentSource.CANVAS
    seen = []
    _export_through_dialog(dialog, qgis_app)
    canvas.rectangle = QgsRectangle(40.0, 40.0, 50.0, 50.0)
    dialog._ensure_export(lambda export, _r: seen.append(export), "x")
    import time

    deadline = time.monotonic() + 60
    while not seen and time.monotonic() < deadline:
        qgis_app.processEvents()
        time.sleep(0.02)
    assert seen[0].extent is not None
    assert seen[0].extent.west == 40.0
    _, rebuilt = _export_through_dialog(dialog, qgis_app)
    assert not rebuilt  # nothing moved since

    # Clipping to the view uses it too, whatever the map opens on.
    dialog.state.extent_source = ExtentSource.DATA
    dialog.state.clip_to_extent = True
    names, _ = _export_through_dialog(dialog, qgis_app)
    assert names == []  # every point lies outside 40..50
    canvas.rectangle = QgsRectangle(0.0, 0.0, 10.0, 10.0)
    names, rebuilt = _export_through_dialog(dialog, qgis_app)
    assert rebuilt
    assert [n for n, _ in names] == ["a", "b", "c"]
    dialog.close()
