---
title: Enhance a map with AI
description: >-
  An exported map is readable HTML, so an AI assistant can add filters, charts or branding without breaking portability. Start with NIKA Desktop.
---

# Enhance a map with AI

An exported map is a readable HTML document, not a compiled bundle. The map is
described by plain markup you can edit — which means an AI assistant can edit it
for you, and you do not have to write any code yourself.

**You do not need to understand the markup below to do this.** It is here so you
can see there is nothing hidden; skip to
[Start with NIKA Desktop](#start-with-nika-desktop) if you would rather just ask
for what you want.

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

## Start with NIKA Desktop

If you are not already living in a terminal, this is the one to use.
**[NIKA Desktop](https://docs.nikaplanet.com/nika-desktop/overview)** is NIKA's
own agent — the same people who make this plugin and the map library your export
runs on. It is an ordinary desktop application: point it at a folder, type what
you want in plain language, and it works through the job itself.

That matters here for one reason. It already knows OnlyMap, so it does not have
to be taught the vocabulary your exported map is written in before it can touch
it safely — which is the failure mode described below.

Ask it in the same plain language you would use with a colleague:

> Open the map I exported to this folder. Add a filter for the `kind` field, and
> make the civil airports blue. Do not change the data or the attribution.

## Or any assistant that can read files

Claude Code, Codex and similar tools work too — the exported file is just HTML.
Open it and describe what you want:

> Read this exported map. Add a filter widget for the `kind` field, and change
> the civil airports to blue. Keep the data and the attribution unchanged.

### Give a general assistant the OnlyMap skill first

An assistant that has not seen OnlyMap's attribute vocabulary will guess, and
guesses produce a map that no longer opens. OnlyMap publishes a skill file that
teaches it the real syntax. Point your assistant at it before asking for
changes:

> Read the OnlyMap skill at
> <https://raw.githubusercontent.com/NikaGeospatial/onlymap-js/main/skills/onlymapjs/SKILL.md>.
> Inspect this exported map, preserve its data and attribution, then add
> <what you want>. Validate the result by opening it locally, and keep the map
> portable.

**Claude Code** users can install it once instead of pasting the link each time:
copy [`skills/onlymapjs/`](https://github.com/NikaGeospatial/onlymap-js/tree/main/skills/onlymapjs)
into `~/.claude/skills/`, and it loads whenever you work on an OnlyMap file.

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
[docs.nikaplanet.com](https://docs.nikaplanet.com), and NIKA Desktop is
documented at
[docs.nikaplanet.com/nika-desktop](https://docs.nikaplanet.com/nika-desktop/overview).
