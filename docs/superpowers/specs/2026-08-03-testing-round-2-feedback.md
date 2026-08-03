# Testing feedback, round 2 (2026-08-03)

From the first real-QGIS run of the live-preview build (`1b6061d`), on QGIS 4.0.3
/ PyQt6. Ordered by severity, not by the order reported.

## P0 - blocks everything

1. **The map goes white when panned.** Everything disappears at roughly 25% right
   of centre. Panning left works until Alaska leaves the screen entirely; panning
   right, it blanks while Alaska is still only slightly past a quarter of the way
   from centre. Strongly suggests antimeridian handling - Alaska is the classic
   case - in either our extent maths or the emitted geometry.

## P1 - advertised features that do nothing

2. **Map title never appears on the map.** Reported before this round and still
   present.
3. **Project description (abstract) never appears** either. Likely the same cause
   as 2.
4. **Caption corner setting does nothing.** All four corners tried. When the
   caption did eventually appear, it collided with other chrome: in the bottom
   corners it drew in front of the scale bar and credits; in the top corners it
   drew *behind* them. So there is both a "does not render" bug and a z-order bug.
5. **Live preview does not rebuild when a layer is added or removed.** The layer
   tree is watched for the list, but the watcher does not trigger a preview
   rebuild. This one is mine, introduced with live preview.

## P2 - behaviour that is wrong rather than absent

6. **Hover popups stack.** Several layers overlapping at one point open several
   popups on top of each other; only the frontmost is readable.
7. **Cursor movement is laggy while hover popups are on.** Reported in round 1 and
   still present.
8. **Click popups follow the cursor** instead of staying anchored over the feature
   that was clicked.
9. **Thin lines are hard to click.** Some lines render narrow enough that hitting
   them for a popup is fiddly; the clickable width should not equal the drawn
   width.

## P3 - clarity and polish

10. **Every setting needs a one-line explainer.** Only coordinate precision and
    the hover toggle have one so far; new users should not need the guide open.
11. **Rename "Show the map name on the map" to "Map title", and default it on.**
12. **The legend repeats the map title**, which is redundant once the title is on
    by default.
13. **GitHub Pages needs a light/dark theme switcher.**

## Feature requests, round 2 (2026-08-03)

Split by whether issue #29 already promised them for `0.1.0`. That matters: the
first group are gaps against a commitment, the second are new scope.

### Already in the `0.1.0` scope list - so these are gaps, not new features

#29 lists "common colors, opacity, line width, marker size, and **basic SVG
markers**" and "single symbol, categorized, and **graduated** styling".

14. **SVG icons per layer**, chosen in that layer's row on the Layers tab, with
    size, fill colour, stroke colour and stroke width - the four properties named
    in Abhijay's screenshot.
15. **Value-driven symbols**: a categorized or graduated layer should be able to
    use a *different* icon per class, not just a different colour. This is the
    "airports svg categorized / airports svg graduated" case in the screenshot,
    and qgis2web only manages it in its OpenLayers export.
16. **Line width driven by value**, and colour ramps applied to lines.

### New scope, beyond `0.1.0`

17. **Clustering** for dense point layers.
18. **Texture / pattern fills.** Closest to #29's explicit "arbitrary QGIS
    renderer parity" exclusion; likely the weakest candidate of the set.
19. **Custom sizes for the legend, title and scale bar.**
20. **Top-centre and bottom-centre** caption positions, alongside the four corners.

### Broken, and a product gap rather than a bug

21. **The Enhance and Host links in the exported map are dead.** Both 404:
    `docs.nikaplanet.com/onlymap/enhance-with-ai` and
    `docs.nikaplanet.com/onlymap/hosting`. #29 makes both "part of the exported
    artifact contract from the first release", so this blocks the release rather
    than being cosmetic. Abhijay also asked what they are, which means the
    product definition is missing, not only the pages.

### Feasibility, checked against the shipped runtime

The bundle already contains `IconLayer`, `getIcon` and `iconAtlas` (so SVG
markers are possible), `ClusterLayer` and `AggregationLayer` (clustering),
`HeatmapLayer`, `HexagonLayer`, `GridLayer`, `TextLayer` and `PathLayer`. We emit
only `GeoJsonLayer` with flat colours today, so most of the list above is a
matter of emitting manifest we are not yet emitting - not of runtime support.

## Notes

Items 7 and 9 touch the OnlyMap runtime, which is sha256-pinned and not ours.
Item 6 and item 8 may be ours - they depend on how the manifest binds overlays -
and must be checked before being handed upstream.
