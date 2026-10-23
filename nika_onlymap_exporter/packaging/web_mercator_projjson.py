"""EPSG:3857 as PROJJSON, for pages that must draw rasters with no network.

The runtime's `COGLayer` resolves a COG's EPSG code by fetching
`https://epsg.io/<code>.json`, ignoring the CRS definitions the runtime itself
bundles, so an offline page drew no raster at all. Every COG this plugin writes
is in Web Mercator (`raster_cog.WEB_MERCATOR_EPSG`), so this one definition is
all the raster stand-in (`raster_embed`) has to answer that request with.

Generated from PROJ's database by QGIS (`QgsCoordinateReferenceSystem.toJsonString`);
`tests/qgis/test_cartograph_rasters.py` holds it equal to what QGIS writes.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

WEB_MERCATOR_PROJJSON = (
    '{"type":"ProjectedCRS","name":"WGS 84 / Pseudo-Mercator","base_crs":{"ty'
    'pe":"GeographicCRS","name":"WGS 84","datum_ensemble":{"name":"World Geod'
    'etic System 1984 ensemble","members":[{"name":"World Geodetic System 198'
    '4 (Transit)","id":{"authority":"EPSG","code":1166}},{"name":"World Geode'
    'tic System 1984 (G730)","id":{"authority":"EPSG","code":1152}},{"name":"'
    'World Geodetic System 1984 (G873)","id":{"authority":"EPSG","code":1153}'
    '},{"name":"World Geodetic System 1984 (G1150)","id":{"authority":"EPSG",'
    '"code":1154}},{"name":"World Geodetic System 1984 (G1674)","id":{"author'
    'ity":"EPSG","code":1155}},{"name":"World Geodetic System 1984 (G1762)","'
    'id":{"authority":"EPSG","code":1156}},{"name":"World Geodetic System 198'
    '4 (G2139)","id":{"authority":"EPSG","code":1309}},{"name":"World Geodeti'
    'c System 1984 (G2296)","id":{"authority":"EPSG","code":1383}}],"ellipsoi'
    'd":{"name":"WGS 84","semi_major_axis":6378137,"inverse_flattening":298.2'
    '57223563},"accuracy":"2.0","id":{"authority":"EPSG","code":6326}},"coord'
    'inate_system":{"subtype":"ellipsoidal","axis":[{"name":"Geodetic latitud'
    'e","abbreviation":"Lat","direction":"north","unit":"degree"},{"name":"Ge'
    'odetic longitude","abbreviation":"Lon","direction":"east","unit":"degree'
    '"}]},"id":{"authority":"EPSG","code":4326}},"conversion":{"name":"Popula'
    'r Visualisation Pseudo-Mercator","method":{"name":"Popular Visualisation'
    ' Pseudo Mercator","id":{"authority":"EPSG","code":1024}},"parameters":[{'
    '"name":"Latitude of natural origin","value":0,"unit":"degree","id":{"aut'
    'hority":"EPSG","code":8801}},{"name":"Longitude of natural origin","valu'
    'e":0,"unit":"degree","id":{"authority":"EPSG","code":8802}},{"name":"Fal'
    'se easting","value":0,"unit":"metre","id":{"authority":"EPSG","code":880'
    '6}},{"name":"False northing","value":0,"unit":"metre","id":{"authority":'
    '"EPSG","code":8807}}]},"coordinate_system":{"subtype":"Cartesian","axis"'
    ':[{"name":"Easting","abbreviation":"X","direction":"east","unit":"metre"'
    '},{"name":"Northing","abbreviation":"Y","direction":"north","unit":"metr'
    'e"}]},"scope":"Web mapping and visualisation.","area":"World between 85.'
    '06\\u00b0S and 85.06\\u00b0N.","bbox":{"south_latitude":-85.06,"west_longi'
    'tude":-180,"north_latitude":85.06,"east_longitude":180},"id":{"authority'
    '":"EPSG","code":3857}}'
)
