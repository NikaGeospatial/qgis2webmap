# What gets exported

0.1.0 supports a deliberately narrow set, well, rather than everything, badly.
Whatever is not translated exactly appears in the **Fidelity** tab - it is never
dropped silently.

## Layers

| | |
|---|---|
| Vector points, lines, polygons | Yes |
| Layer order and groups, including nested groups | Yes |
| Layer visibility and opacity | Yes |
| Scale-dependent visibility | Yes, converted to zoom levels |
| Rasters | Not in 0.1.0 |
| Attribute-only tables | Not exported - nothing to draw |

Data in any CRS is reprojected to WGS84 on the way out.

## Symbology

| | |
|---|---|
| Single symbol | Yes |
| Categorized | Yes, with a category legend |
| Graduated | Yes, keeping your exact class breaks |
| Marker shapes (square, star, triangle, …) | Recorded, and reported if approximated |
| Rule-based, embedded-symbol renderers | Not in 0.1.0, reported by name |
| Stacked symbol layers | Bottom layer only, and reported |

## Labels

Text, font size, colour and halo are translated. Placement rules, collision
handling and curved labels are not - the web renderer places labels itself.

Labels using an expression rather than a single field are not translated in
0.1.0.

## Popups

Field names, aliases, and fields you hid in the QGIS attribute table are all
respected. Popups are labelled by default.

## The map itself

Legend, layer switcher, zoom controls and a scale bar are **on by default**.

The starting view comes from your data's extent. Data crossing the 180th
meridian - Alaska, Fiji, Chukotka - is handled correctly rather than opening
zoomed out to the whole world.

No basemap is included in 0.1.0, which is what lets an exported map work with no
internet connection.
