"""Live features from a WFS, OGC API Features or ArcGIS FeatureServer layer.

OnlyMap 0.13.4 loads a GeoJSON layer's features from a service for the area in
view when its `data` carries an extent token (`{bbox-crs84}`, or
`{bbox-epsg-4326}` latitude first), again when the view settles somewhere new,
following each service's own paging up to `max-features` (10,000 by default):
WFS 2.0 by `STARTINDEX`, OGC API Features by its `next` links, ArcGIS by
`resultOffset` while the response says `exceededTransferLimit`. Responses must
be GeoJSON. A failed request is an `om-layer-error` event, which the page
answers by switching the layer to the copy embedded at export
(`templates/layer-notice.html`).

So a service layer is exported live first, its embedded copy the fallback -
unless its look is worked out from its features at export, which the live
features would not carry (rule classes, label points, shown values, filter
panels, count bubbles): then it stays embedded. A layer too big to carry is
live only. The user can pick either on the layer (`LiveMode`).

Pure Python: the QGIS side (`layer_reader.read_live_source`) reads the source
and the reasons; this module builds the request and settles the mode.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .export_ir import (
    ExportLayer,
    LiveMode,
    RendererKind,
)
from .fidelity_report import FidelityReportBuilder
from .manifest_builder import CATEGORY_FILTER_MAX, undrawn_filter, undrawn_left

# QGIS provider key -> the service, as the Fidelity tab names it. Only these
# three: the runtime pages no other feature service (SensorThings stays
# embedded, as it was).
LIVE_PROVIDERS = {
    "wfs": "WFS",
    "oapif": "OGC API Features",
    "arcgisfeatureserver": "ArcGIS FeatureServer",
}

# Features per request. The runtime pages in steps of what each response
# returned, so a server that caps lower still pages correctly; 1,000 is the
# runtime's own default and within every server's usual maximum.
PAGE_SIZE = 1000

# The most features the runtime keeps for one view, which it reports when the
# service has more (its `DEFAULT_MAX_FEATURES`; not written, so the runtime's
# default stands).
MAX_FEATURES = 10_000

# The query parameters a WFS request is built from, dropped from the address
# QGIS keeps so the request is ours alone.
_WFS_PARAMETERS = frozenset(
    {
        "service",
        "version",
        "request",
        "typename",
        "typenames",
        "outputformat",
        "srsname",
        "bbox",
        "count",
        "maxfeatures",
        "startindex",
        "resulttype",
        "filter",
        "propertyname",
    }
)


def live_host(url: str) -> str:
    """The host a live layer's requests go to, as the reader's browser sees it."""
    return urlsplit(url).netloc.rpartition("@")[2]


def _without(url: str, names: Iterable[str]) -> tuple[str, str]:
    """`url` without a fragment and without the query parameters `names`
    (case ignored), and the separator to append more with."""
    drop = {name.lower() for name in names}
    parts = urlsplit(url)
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in drop
    ]
    base = urlunsplit(parts._replace(query=urlencode(kept), fragment=""))
    return base, "&" if kept else "?"


def wfs_template(url: str, typename: str) -> str:
    """A WFS 2.0 GetFeature for the area in view, as GeoJSON in longitude and
    latitude.

    The box is written latitude first with its CRS named, which is what WFS
    2.0 reads in EPSG:4326; without the name a server reads it in the
    layer's own CRS. `SRSNAME=EPSG:4326` asks for the features in degrees,
    which GeoServer writes longitude first in GeoJSON, as the runtime reads
    them. `COUNT` is the page the runtime steps `STARTINDEX` by.
    """
    base, sep = _without(url, _WFS_PARAMETERS)
    return (
        f"{base}{sep}SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
        f"&TYPENAMES={quote(typename, safe=':')}"
        "&OUTPUTFORMAT=application/json&SRSNAME=EPSG:4326"
        "&BBOX={bbox-epsg-4326},urn:ogc:def:crs:EPSG::4326"
        f"&COUNT={PAGE_SIZE}"
    )


def ogcapi_template(url: str, collection: str) -> str:
    """An OGC API Features `items` request for the area in view.

    `url` is the landing page QGIS keeps; the runtime follows the response's
    `next` links. `bbox` is longitude first (CRS84), as the standard reads it.
    """
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    items = f"{path}/collections/{quote(collection, safe='')}/items"
    base = urlunsplit(parts._replace(path=items, query="", fragment=""))
    return f"{base}?f=json&bbox={{bbox-crs84}}&limit={PAGE_SIZE}"


def arcgis_template(url: str, fields: Iterable[str] = ()) -> str:
    """An ArcGIS FeatureServer (or MapServer) layer `query` for the area in
    view, as GeoJSON in degrees.

    `fields` are the attributes the map reads, the only ones asked for; none
    asks for the geometry alone. The runtime steps `resultOffset` while the
    response says there is more.
    """
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    base = urlunsplit(parts._replace(path=f"{path}/query", query="", fragment=""))
    names = ",".join(quote(name, safe="") for name in fields)
    out_fields = f"&outFields={names}" if names else ""
    return (
        f"{base}?where=1%3D1&geometry={{bbox-crs84}}"
        "&geometryType=esriGeometryEnvelope&inSR=4326"
        f"&spatialRel=esriSpatialRelIntersects{out_fields}&outSR=4326"
        f"&f=geojson&resultRecordCount={PAGE_SIZE}"
    )


def _computed_at_export(layer: ExportLayer) -> list[str]:
    """What in the finished layer is worked out from its features at export.

    The reasons `layer_reader.read_live_source` cannot see yet: count bubbles
    and labels are settled project-wide (`project_reader._declutter`), and the
    popup pictures a map carries after every layer is read.
    """
    reasons: list[str] = []
    renderer = layer.renderer
    if renderer.field_generated:
        reasons.append("its rule-based style (each feature's rule is worked out)")
    elif undrawn_left(renderer) is not None:
        # What the runtime's filter leaves out on a live layer is left to it
        # (`manifest_builder.undrawn_filter`); the rest is dropped at export.
        if renderer.kind is RendererKind.GRADUATED:
            reasons.append(
                "the ranges switched off between drawn ones in its style, or "
                "the gaps between its ranges (their features are left out)"
            )
        elif renderer.unmatched_hidden:
            reasons.append(
                f"which of its features belong to its more than "
                f"{CATEGORY_FILTER_MAX} categories (the others are left out)"
            )
        else:
            reasons.append(
                "the categories switched off in its style (their features are left out)"
            )
    labeling = layer.labeling
    if labeling.enabled and labeling.field_name:
        if labeling.number_format is not None:
            reasons.append("its Formatted numbers labels")
        elif labeling.expression:
            reasons.append("its expression labels")
        else:
            reasons.append("its labels (where each one goes is worked out)")
    if layer.filter is not None:
        reasons.append("its Filter by panel (its values and ranges are counted)")
    # Live features merge into the same bubbles, at the zoom worked out from
    # the features read at export; but the runtime counts in a bubble the
    # features its filter leaves out (`manifest_builder.undrawn_filter`).
    if layer.cluster_max_zoom is not None and undrawn_filter(renderer):
        reasons.append(
            "its count bubbles (which would count the features its style leaves out)"
        )
    if layer.popup_pictures or layer.popup_files:
        reasons.append("the pictures its popup carries")
    return reasons


def _listed(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def resolve_live(
    layer: ExportLayer,
    report: FidelityReportBuilder,
    too_big_bytes: int,
) -> ExportLayer:
    """Settle where `layer`'s features come from, and say so.

    A layer with no live source is returned as it is. Otherwise: embedded when
    its look is worked out from its features at export (whatever was chosen -
    the live features would draw without it) or when Embedded was chosen;
    live only when chosen, or when automatic and the embedded copy would be
    over `too_big_bytes`; live first otherwise. A live-only layer carries no
    features, which the map's reader then loads.
    """
    live = layer.live
    if live is None:
        return layer
    name = layer.name
    subject = f"Live features of '{name}'"
    computed = _computed_at_export(layer)
    reasons = [*live.embedded_only, *computed]
    size = len(layer.geojson_text.encode("utf-8")) if layer.geojson is not None else 0
    too_big = size > too_big_bytes
    choice = live.choice

    if choice is LiveMode.EMBEDDED:
        return dataclasses.replace(layer, live=None)
    if reasons:
        # What reading the layer found (a login, a filter, a WFS 1.x) stops
        # the map loading it live; only a look is worked out from features.
        causes = []
        if live.embedded_only:
            causes.append(
                "the map cannot load it live because of "
                + _listed(list(live.embedded_only))
            )
        if computed:
            causes.append(
                f"the map works out {_listed(computed)} from the features when "
                "it is exported, which features loaded live would not carry"
            )
        detail = (
            f"Embedded, not loaded live from {live.host}: {'; and '.join(causes)}. "
            "The map shows the features as they were then."
        )
        if too_big:
            detail += (
                f" At {size / 1024 / 1024:.0f} MB they make a large file; "
                "without those, the layer could load live instead."
            )
        if choice is None:
            report.preserved(subject, detail, layer.layer_id)
        else:
            report.approximated(subject, detail, layer.layer_id)
        return dataclasses.replace(layer, live=None)

    mode = choice or (LiveMode.LIVE_ONLY if too_big else LiveMode.LIVE_FIRST)
    paging = (
        f"the features in view, again when the map moves elsewhere, up to "
        f"{MAX_FEATURES:,} at a time"
    )
    if mode is LiveMode.LIVE_FIRST:
        report.approximated(
            subject,
            f"Loads live from {live.host} ({live.service}) in the reader's "
            f"browser: {paging}. So {live.host} sees those requests and the area "
            "being looked at. Where the service cannot be read - it refuses "
            "maps from other sites, is down, or the reader is offline - the map "
            "shows the copy embedded at export instead.",
            layer.layer_id,
        )
        return dataclasses.replace(layer, live=dataclasses.replace(live, mode=mode))

    why = (
        f"At {size / 1024 / 1024:.0f} MB its features are too big to carry in "
        "the map, so it"
        if choice is None
        else "It"
    )
    report.approximated(
        subject,
        f"{why} loads live only, from {live.host} ({live.service}) in the "
        f"reader's browser: {paging}. So {live.host} sees those requests and the "
        "area being looked at, and without it - the service refuses maps from "
        "other sites, is down, or the reader is offline - the layer is empty "
        "and the map names it as not loaded.",
        layer.layer_id,
    )
    return dataclasses.replace(
        layer,
        live=dataclasses.replace(live, mode=mode),
        geojson={"type": "FeatureCollection", "features": []},
    )
