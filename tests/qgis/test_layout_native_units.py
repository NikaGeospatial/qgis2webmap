"""A layout whose native units are not millimetres exports the same cartograph
as the physically identical millimetre layout.

QGIS keeps every layout scene coordinate in the layout's own units (inches,
centimetres, points), while the cartograph is laid out in millimetres. These
tests build the same page in each unit -- a plain and a rotated label, a
grouped label, and three map frames (plain, rotated item, rotated content),
each with a background, a frame and one big marker at the extent centre plus a
small one off-centre -- and check the RESULT: the item boxes read for the
cartograph, and the pictures both static-frame paths draw. The source layout
must come out of every render exactly as it went in.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

qgis_core = pytest.importorskip("qgis.core")

from nika_onlymap_exporter.core.fidelity_report import (  # noqa: E402
    FidelityReportBuilder,
)
from nika_onlymap_exporter.core.layout_reader import read_layout  # noqa: E402
from nika_onlymap_exporter.exporters.frame_raster import (  # noqa: E402
    AsyncFrameRender,
    render_static_frame,
)

#: Where the diagnostic pictures go: `Q2W_TEST_OUT_DIR` if set, otherwise the
#: test's own temporary folder (set per test by `_picture_folder`).
OUT_DIR: Path | None = None

DPI = 100
WAIT_S = 60.0
#: Native units under test: name -> (QGIS unit, millimetres per unit).
UNITS = {
    "mm": ("LayoutMillimeters", 1.0),
    "inch": ("LayoutInches", 25.4),
    "cm": ("LayoutCentimeters", 10.0),
    "pt": ("LayoutPoints", 25.4 / 72.0),
}
POSITION_TOLERANCE_MM = 0.05

CENTRE_RED = (220, 20, 20)
OFFSET_BLUE = (20, 40, 220)
BACKGROUND = (250, 235, 150)
FRAME = (0, 90, 0)
EXTENT = (0.0, 0.0, 10000.0, 6000.0)  # 5:3 -- the frames are 5:3 too
OFFSET_MARKER = (2000.0, 4800.0)  # 20 % from the left, 20 % from the top


@dataclass
class Scene:
    layout: object
    maps: dict[str, object]
    labels: dict[str, object]
    unit_mm: float


def _unit(name: str):
    from qgis.core import QgsUnitTypes

    return getattr(QgsUnitTypes, UNITS[name][0])


def _marker_layer(project):
    from qgis.core import (
        QgsFeature,
        QgsGeometry,
        QgsMarkerSymbol,
        QgsPointXY,
        QgsVectorLayer,
    )

    layer = QgsVectorLayer("Point?crs=EPSG:3857&field=id:integer", "marks", "memory")
    centre = QgsFeature(layer.fields())
    centre.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(5000, 3000)))
    other = QgsFeature(layer.fields())
    other.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*OFFSET_MARKER)))
    layer.dataProvider().addFeatures([centre, other])
    layer.updateExtents()
    from qgis.core import QgsRuleBasedRenderer

    root = QgsRuleBasedRenderer.Rule(None)
    for expression, colour, size in (
        ("$x = 5000", CENTRE_RED, "14"),
        ("$x = 2000", OFFSET_BLUE, "6"),
    ):
        symbol = QgsMarkerSymbol.createSimple(
            {
                "name": "circle",
                "color": "{},{},{}".format(*colour),
                "size": size,
                "outline_style": "no",
            }
        )
        root.appendChild(QgsRuleBasedRenderer.Rule(symbol, filterExp=expression))
    layer.setRenderer(QgsRuleBasedRenderer(root))
    project.addMapLayer(layer)
    return layer


def _place(layout, item, unit_name, x_mm, y_mm, w_mm, h_mm, rotation=0.0) -> None:
    """Put `item` at the millimetre box given, stored in `unit_name`'s units.
    A rotation turns the item about the centre of that box."""
    from qgis.core import QgsLayoutPoint, QgsLayoutSize

    unit, mm = _unit(unit_name), UNITS[unit_name][1]
    item.attemptResize(QgsLayoutSize(w_mm / mm, h_mm / mm, unit))
    item.attemptMove(QgsLayoutPoint(x_mm / mm, y_mm / mm, unit))
    if rotation:
        item.setItemRotation(rotation, True)


def build_scene(project, unit_name: str, *, blend_first_map: bool = False) -> Scene:
    from qgis.core import (
        QgsLayoutItemLabel,
        QgsLayoutItemMap,
        QgsLayoutMeasurement,
        QgsPrintLayout,
    )
    from qgis.PyQt.QtGui import QColor, QPainter

    project.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
    marks = _marker_layer(project)
    layout = QgsPrintLayout(project)
    # The units come first, as in the layout designer for a layout made in
    # inches: the page is then placed in the layout's own scene units.
    layout.setUnits(_unit(unit_name))
    layout.initializeDefaults()
    layout.setName(f"units-{unit_name}")

    labels = {}
    for key, text, box, rotation in (
        ("plain", "plain label", (20.0, 15.0, 60.0, 10.0), 0.0),
        # The centre of this one is (50.8, 63.5) mm, i.e. (2, 2.5) inches.
        ("rotated", "rotated label", (30.8, 57.5, 40.0, 12.0), 30.0),
    ):
        label = QgsLayoutItemLabel(layout)
        label.setText(text)
        layout.addLayoutItem(label)
        _place(layout, label, unit_name, *box, rotation=rotation)
        labels[key] = label
    grouped = []
    for index, x in enumerate((200.0, 245.0)):
        label = QgsLayoutItemLabel(layout)
        label.setText(f"grouped {index}")
        layout.addLayoutItem(label)
        _place(layout, label, unit_name, x, 150.0, 40.0, 10.0)
        grouped.append(label)
        labels[f"grouped{index}"] = label
    layout.groupItems(grouped)

    maps = {}
    for key, box, item_rotation, content_rotation in (
        ("plain", (100.0, 15.0, 100.0, 60.0), 0.0, 0.0),
        ("rotated_item", (20.0, 100.0, 90.0, 54.0), 20.0, 0.0),
        ("rotated_content", (130.0, 100.0, 60.0, 36.0), 0.0, 30.0),
    ):
        item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(item)
        item.setCrs(qgis_core.QgsCoordinateReferenceSystem("EPSG:3857"))
        item.setLayers([marks])
        _place(layout, item, unit_name, *box)
        item.setExtent(qgis_core.QgsRectangle(*EXTENT))
        item.setMapRotation(content_rotation)
        item.setBackgroundEnabled(True)
        item.setBackgroundColor(QColor(*BACKGROUND))
        item.setFrameEnabled(True)
        item.setFrameStrokeColor(QColor(*FRAME))
        item.setFrameStrokeWidth(
            QgsLayoutMeasurement(1.2, qgis_core.QgsUnitTypes.LayoutMillimeters)
        )
        if item_rotation:
            item.setItemRotation(item_rotation, True)
        item.refresh()
        maps[key] = item
    if blend_first_map:
        maps["plain"].setBlendMode(QPainter.CompositionMode.CompositionMode_Multiply)
    return Scene(layout, maps, labels, UNITS[unit_name][1])


# ---- snapshots -------------------------------------------------------------


def _snapshot(scene: Scene) -> dict[str, object]:
    """Everything a render could disturb in the source layout."""
    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument

    layout = scene.layout
    document = QDomDocument()
    element = layout.writeXml(document, QgsReadWriteContext())
    document.appendChild(element)
    items = {}
    for item in layout.items():
        if not isinstance(item, qgis_core.QgsLayoutItem):
            continue
        position, size = item.positionWithUnits(), item.sizeWithUnits()
        items[item.uuid()] = (
            position.x(),
            position.y(),
            int(position.units()),
            size.width(),
            size.height(),
            int(size.units()),
            item.itemRotation(),
            item.isVisible(),
            item.itemOpacity(),
            item.isSelected(),
        )
    maps = {
        key: (item.mapRotation(), item.extent().toString(8), item.scale())
        for key, item in scene.maps.items()
    }
    return {
        "xml": document.toString(),
        "items": items,
        "maps": maps,
        "units": int(layout.units()),
        "undo": layout.undoStack().stack().count(),
    }


def _assert_unchanged(before, after) -> None:
    assert after["units"] == before["units"]
    assert after["undo"] == before["undo"]
    assert after["maps"] == before["maps"]
    assert after["items"].keys() == before["items"].keys()
    for uuid, old in before["items"].items():
        new = after["items"][uuid]
        assert new[2] == old[2] and new[5] == old[5], "an item changed units"
        for index in (0, 1, 3, 4, 6, 8):
            assert new[index] == pytest.approx(old[index], abs=1e-9)
        assert new[7] == old[7] and new[9] == old[9]
    assert after["xml"] == before["xml"]


# ---- pixels ----------------------------------------------------------------


def _png_image(png_bytes: bytes):
    from qgis.PyQt.QtGui import QImage

    image = QImage.fromData(png_bytes)
    assert not image.isNull()
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _array(image):
    """The picture as an (h, w, 4) array of r, g, b, a."""
    import numpy as np

    argb = image.convertToFormat(image.Format.Format_ARGB32)
    ptr = argb.constBits()
    ptr.setsize(argb.sizeInBytes())
    bgra = np.frombuffer(ptr, dtype=np.uint8).reshape(argb.height(), -1, 4)
    return bgra[:, : argb.width(), [2, 1, 0, 3]].astype(int)


def _close(pixel_rgb, colour, tolerance=40) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel_rgb, colour))


def _centroid(image, colour) -> tuple[float, float] | None:
    """The centre of the pixels of `colour`, as fractions of the picture."""
    import numpy as np

    data = _array(image)
    hit = (np.abs(data[:, :, :3] - np.array(colour)).max(axis=2) <= 40) & (
        data[:, :, 3] >= 250
    )
    ys, xs = np.nonzero(hit)
    if len(xs) == 0:
        return None
    return (
        (xs.mean() + 0.5) / image.width(),
        (ys.mean() + 0.5) / image.height(),
    )


def _rgba(image, x, y) -> tuple[int, int, int, int]:
    return tuple(int(v) for v in _array(image)[y, x])


def _frame_thickness(image) -> int:
    """How many frame-coloured pixels run down the middle of the top edge."""
    x = image.width() // 2
    count = 0
    for y in range(image.height() // 4):
        r, g, b, a = _rgba(image, x, y)
        if a > 200 and _close((r, g, b), FRAME, 70):
            count += 1
    return count


def _draw_async(qgis_app, layout, item):
    from qgis.PyQt.QtCore import QEventLoop, QTimer

    drawing = AsyncFrameRender(layout, item, DPI)
    assert AsyncFrameRender.supports(layout, item)
    drawing.start()
    loop = QEventLoop()
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if drawing.done else None)
    poll.start(20)
    deadline.start(int(WAIT_S * 1000))
    if not drawing.done:
        loop.exec()
    poll.stop()
    deadline.stop()
    assert drawing.done, "the frame never finished drawing"
    from qgis.PyQt.QtGui import QImage

    return drawing.take_image().convertToFormat(QImage.Format.Format_ARGB32)


@pytest.fixture(autouse=True)
def _picture_folder(tmp_path):
    global OUT_DIR
    chosen = os.environ.get("Q2W_TEST_OUT_DIR")
    OUT_DIR = Path(chosen) if chosen else tmp_path / "pictures"
    yield
    OUT_DIR = None


def _save(image, name: str) -> None:
    if OUT_DIR is None:
        return
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        image.save(str(OUT_DIR / f"{name}.png"))
    except OSError:
        pass


# ---- the tests -------------------------------------------------------------

OTHER_UNITS = ["inch", "cm", "pt"]
MAP_KEYS = ["plain", "rotated_item", "rotated_content"]


def _boxes(scene: Scene) -> dict[str, tuple[float, float, float, float, float]]:
    """Every exported item's (x, y, w, h, rotation) in mm, by item display name."""
    reading = read_layout(scene.layout, FidelityReportBuilder())
    names = {item.uuid(): key for key, item in scene.maps.items()}
    out = {}
    for frame in reading.frames:
        out[f"map:{names[frame.map_item.uuid()]}"] = (
            frame.box.x,
            frame.box.y,
            frame.box.w,
            frame.box.h,
            frame.rotation,
        )
    texts = [item for item in reading.furniture if hasattr(item, "body")]
    for text in texts:
        out[f"text:{text.body}"] = (
            text.box.x,
            text.box.y,
            text.box.w,
            text.box.h,
            text.rotation,
        )
    return out


class TestItemPlacementIsPhysical:
    @pytest.mark.parametrize("unit_name", OTHER_UNITS)
    def test_boxes_match_the_millimetre_layout(
        self, qgis_app, project, unit_name
    ) -> None:
        reference = _boxes(build_scene(project, "mm"))
        other = _boxes(build_scene(project, unit_name))
        assert other.keys() == reference.keys()
        assert len(reference) == 7
        for key, ref in reference.items():
            for got, want in zip(other[key], ref):
                assert got == pytest.approx(want, abs=POSITION_TOLERANCE_MM), key

    def test_the_rotated_label_is_centred_where_it_was_put(
        self, qgis_app, project
    ) -> None:
        boxes = _boxes(build_scene(project, "inch"))
        x, y, w, h, rotation = boxes["text:rotated label"]
        assert (x + w / 2, y + h / 2) == pytest.approx((50.8, 63.5), abs=0.05)
        assert (w, h, rotation) == pytest.approx((40.0, 12.0, 30.0), abs=0.05)

    def test_grouped_labels_keep_their_places(self, qgis_app, project) -> None:
        boxes = _boxes(build_scene(project, "inch"))
        assert boxes["text:grouped 0"][:4] == pytest.approx(
            (200.0, 150.0, 40.0, 10.0), abs=0.05
        )
        assert boxes["text:grouped 1"][:4] == pytest.approx(
            (245.0, 150.0, 40.0, 10.0), abs=0.05
        )

    def test_reading_leaves_the_layout_as_it_was(self, qgis_app, project) -> None:
        scene = build_scene(project, "inch")
        before = _snapshot(scene)
        _boxes(scene)
        _assert_unchanged(before, _snapshot(scene))


def _check_picture(image, key: str, unit_name: str) -> None:
    centre = _centroid(image, CENTRE_RED)
    assert centre is not None, f"{key}/{unit_name}: no centre marker"
    assert centre == pytest.approx((0.5, 0.5), abs=0.015)
    # Background fills the whole frame and the frame is drawn on top.
    r, g, b, a = _rgba(image, 12, image.height() // 2)
    assert a == 255 and _close((r, g, b), BACKGROUND, 12), (key, unit_name)
    assert _frame_thickness(image) >= 2, f"{key}/{unit_name}: frame missing"
    if key != "rotated_content":
        # The small marker sits 20 % in from the left and the top.
        other = _centroid(image, OFFSET_BLUE)
        assert other is not None
        assert other == pytest.approx((0.2, 0.2), abs=0.02)


def _compare_pictures(reference, other, key: str, unit_name: str) -> None:
    assert other.size() == reference.size()
    for colour in (CENTRE_RED, OFFSET_BLUE):
        a, b = _centroid(reference, colour), _centroid(other, colour)
        if a is None and b is None:
            continue
        assert a is not None and b is not None, (key, unit_name, colour)
        assert (b[0] - a[0]) * reference.width() == pytest.approx(0, abs=1.5)
        assert (b[1] - a[1]) * reference.height() == pytest.approx(0, abs=1.5)
    assert abs(_frame_thickness(other) - _frame_thickness(reference)) <= 1
    assert _rgba(other, 12, other.height() // 2) == pytest.approx(
        _rgba(reference, 12, reference.height() // 2), abs=3
    )


class TestStaticFramesArePhysical:
    """Both ways of drawing a static frame, per native unit."""

    @pytest.mark.parametrize("unit_name", ["mm", *OTHER_UNITS])
    @pytest.mark.parametrize("key", MAP_KEYS)
    def test_synchronous_picture(self, qgis_app, project, unit_name, key) -> None:
        scene = build_scene(project, unit_name)
        before = _snapshot(scene)
        render = render_static_frame(scene.layout, scene.maps[key], dpi=DPI)
        image = _png_image(render.png_bytes)
        _save(image, f"sync-{key}-{unit_name}")
        _assert_unchanged(before, _snapshot(scene))
        _check_picture(image, key, unit_name)
        if unit_name != "mm":
            reference_scene = build_scene(project, "mm")
            reference = _png_image(
                render_static_frame(
                    reference_scene.layout, reference_scene.maps[key], dpi=DPI
                ).png_bytes
            )
            _compare_pictures(reference, image, key, unit_name)

    @pytest.mark.parametrize("unit_name", ["mm", *OTHER_UNITS])
    @pytest.mark.parametrize("key", MAP_KEYS)
    def test_asynchronous_picture(self, qgis_app, project, unit_name, key) -> None:
        scene = build_scene(project, unit_name)
        before = _snapshot(scene)
        image = _draw_async(qgis_app, scene.layout, scene.maps[key])
        _save(image, f"async-{key}-{unit_name}")
        _assert_unchanged(before, _snapshot(scene))
        _check_picture(image, key, unit_name)
        if unit_name != "mm":
            reference_scene = build_scene(project, "mm")
            reference = _draw_async(
                qgis_app, reference_scene.layout, reference_scene.maps[key]
            )
            _compare_pictures(reference, image, key, unit_name)

    @pytest.mark.parametrize("unit_name", ["mm", "inch"])
    @pytest.mark.parametrize("key", MAP_KEYS)
    def test_both_paths_draw_the_same_picture(
        self, qgis_app, project, unit_name, key
    ) -> None:
        scene = build_scene(project, unit_name)
        sync = _png_image(
            render_static_frame(scene.layout, scene.maps[key], dpi=DPI).png_bytes
        )
        drawn = _draw_async(qgis_app, scene.layout, scene.maps[key])
        _compare_pictures(sync, drawn, key, unit_name)

    @pytest.mark.parametrize("unit_name", ["mm", "inch"])
    def test_a_frame_the_job_cannot_draw_takes_the_synchronous_path(
        self, qgis_app, project, unit_name
    ) -> None:
        """A blend mode on the map item itself is one `AsyncFrameRender`
        declines, so the export draws it with `render_static_frame`."""
        scene = build_scene(project, unit_name, blend_first_map=True)
        assert not AsyncFrameRender.supports(scene.layout, scene.maps["plain"])
        image = _png_image(
            render_static_frame(scene.layout, scene.maps["plain"], dpi=DPI).png_bytes
        )
        _check_picture(image, "plain", unit_name)

    @pytest.mark.parametrize("unit_name", OTHER_UNITS)
    def test_asynchronous_picture_with_a_blend_mode_layer(
        self, qgis_app, project, unit_name
    ) -> None:
        """A layer with a blend mode is drawn onto an intermediate picture
        sized from the item's bounding box."""
        from qgis.PyQt.QtGui import QPainter

        pictures = []
        for name in ("mm", unit_name):
            scene = build_scene(project, name)
            scene.maps["plain"].layers()[0].setBlendMode(
                QPainter.CompositionMode.CompositionMode_Multiply
            )
            assert scene.maps["plain"].containsAdvancedEffects()
            pictures.append(_draw_async(qgis_app, scene.layout, scene.maps["plain"]))
            _save(pictures[-1], f"blend-{name}")
        # The multiplied blue marker is no longer the blue it was; the
        # centre marker, over nothing, keeps its colour.
        centre = _centroid(pictures[1], CENTRE_RED)
        assert centre == pytest.approx((0.5, 0.5), abs=0.015)
        assert _frame_thickness(pictures[1]) >= 2
        _compare_pictures(pictures[0], pictures[1], "plain", unit_name)
