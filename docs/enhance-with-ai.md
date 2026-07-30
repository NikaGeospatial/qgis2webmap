# Enhance a map with AI

An exported map is a readable HTML document, not a compiled bundle. The map is
described by plain markup you can edit - which means an AI assistant can edit it
for you.

## What the file looks like inside

```html
<om-map center="[-160.65, 57.4]" zoom="4" basemap="none" telemetry="off">
  <om-layer id="airports" type="GeoJsonLayer"
            label="Airports"
            get-fill-color="$kind == 'civil' ? '#2e8b57' : '#b22222'">
    <script type="application/json">{ ...your data... }</script>
  </om-layer>
  <om-widget type="legend" position="top-end"></om-widget>
</om-map>
```

Layers, colours and controls are attributes. Change one and reload the file.

## Asking an assistant to change it

Open the exported `.html` with Claude Code, Codex, or any assistant that can read
files, and describe what you want:

> Read this exported map. Add a filter widget for the `kind` field, and change
> the civil airports to blue. Keep the data and the attribution unchanged.

Useful things to ask for:

- new colours, or a different colour scheme
- a filter or search control
- a title, a legend caption, or different wording
- a chart alongside the map
- a story that steps through several views

## Two rules

1. **Do not edit the runtime.** The large `<script>` block at the bottom is
   minified library code. Nothing you want to change lives there.
2. **Keep the attribution.** If your data came with a licence or a required
   credit, it has to stay.

## Reference

The full attribute vocabulary is documented at
[docs.nikaplanet.com](https://docs.nikaplanet.com).
