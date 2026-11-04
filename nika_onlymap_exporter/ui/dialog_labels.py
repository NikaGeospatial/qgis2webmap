"""The wording of the web map dialog's fixed choices, in the order offered.

Each table maps a setting's value to the label its combo box shows, marked
with `N_()` and translated where the combo is filled. Order is display order.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from ..core.export_ir import ExtentSource, OutputMode, OverlayCorner, PopupFieldMode
from ..core.translatable import N_

# The Map tab's "Export as" choice, worded as 0.1.4 had it: what each one is
# for, not what it is called internally.
MODE_LABELS = {
    OutputMode.STANDALONE_HTML: N_("Standalone HTML - one file, opens by double-click"),
    OutputMode.SHARE_ZIP: N_("Share ZIP - a zip to email or upload"),
    OutputMode.FOLDER: N_("Folder - for publishing to a web server"),
}

# Worded so the choice is readable without knowing the model: "with data" says
# what happens, where qgis2web's "visible with data" needs its manual.
POPUP_MODE_LABELS = {
    PopupFieldMode.NO_LABEL: N_("Value only, no label"),
    PopupFieldMode.INLINE_ALWAYS: N_("Label beside value - always show"),
    PopupFieldMode.INLINE_WITH_DATA: N_("Label beside value - only if it has data"),
    PopupFieldMode.HEADER_ALWAYS: N_("Label above value - always show"),
    PopupFieldMode.HEADER_WITH_DATA: N_("Label above value - only if it has data"),
    PopupFieldMode.HIDDEN: N_("Do not show this field"),
}

# Centres first: they are the only positions not already holding map chrome, so
# they are what most people should pick.
CAPTION_CORNER_LABELS = {
    OverlayCorner.TOP_CENTER: N_("Top centre - clear of the controls"),
    OverlayCorner.BOTTOM_CENTER: N_("Bottom centre - clear of the controls"),
    OverlayCorner.TOP_LEFT: N_("Top left - shared with the layer switcher"),
    OverlayCorner.TOP_RIGHT: N_("Top right - shared with the legend"),
    OverlayCorner.BOTTOM_LEFT: N_("Bottom left - shared with zoom and scale"),
    OverlayCorner.BOTTOM_RIGHT: N_("Bottom right - shared with the credit"),
}

# "None" first and default: it is the only choice that keeps the export working
# offline and contacting nobody. The rest are the runtime's registered presets,
# minus the MapTiler ones, which need an API key the file would have to carry in
# plain text for every recipient to read.
BASEMAP_LABELS = {
    "none": N_("None - the export stays offline and contacts nobody"),
    "osm": N_("OpenStreetMap"),
    "positron": N_("Positron - pale, for data on top"),
    "dark-matter": N_("Dark Matter - dark background"),
    "voyager": N_("Voyager - general purpose"),
    "liberty": N_("Liberty - detailed street map"),
    "bright": N_("Bright - high contrast"),
}

# Relief, not a backdrop, so it sits in the same group as the basemap but on its
# own row. One entry beyond "off": the runtime's other preset needs a MapTiler
# key, which an exported file would have to carry where anyone can read it.
TERRAIN_LABELS = {
    "none": N_("Flat - no elevation"),
    "terrarium": N_("Global relief - tilts the map so it shows"),
}

# A short list rather than a spinner: the useful range is narrow, and naming the
# sizes means nobody has to reason about what 1.35 looks like.
CHROME_SCALE_LABELS = {
    1.0: N_("Normal"),
    0.85: N_("Small"),
    1.25: N_("Large"),
    1.5: N_("Extra large"),
    2.0: N_("Largest - for presentations and big screens"),
}

# Beside the Measure tool switch, in the order of the runtime's own Units row,
# which this choice replaces on the map.
MEASURE_UNIT_LABELS = {
    "metric": N_("Metric"),
    "imperial": N_("Imperial"),
    "nautical": N_("Nautical"),
}

EXTENT_LABELS = {
    ExtentSource.DATA: N_("The data - every feature is visible"),
    ExtentSource.CANVAS: N_("The current QGIS view"),
}
