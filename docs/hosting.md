---
title: Putting a map online
seo_title: Host a QGIS web map online
description: >-
  Where an exported map can live once you have made it, what NIKA hosting will be when it exists, and the Content Security Policy trap that empties the map.
---

# Putting a map online

An exported map is an ordinary file. Nothing about it needs NIKA, and nothing
about it uploads on its own — putting it somewhere is a thing you do, not a
thing it does.

## Hosting it yourself, today

The export is plain HTML with no server requirement, so any static host works:
GitHub Pages, S3, Netlify, or a folder on a web server you already run. Choose
the **Folder** output mode if you would rather copy a directory than a single
file.

That is the whole answer right now, and for most maps it is a good one.

## Hosting with NIKA is not built yet

There is a **Host** button in the export dialog and in the preview. It does not
upload anything. It opens a short form, because we would rather find out whether
people want one-click hosting before building an account system, file storage
and an expiry job for a feature nobody asked for.

If you want it, say so there — that is genuinely how the decision gets made.

When it does exist it will be a separate, explicit step after signing in, with a
confirmation naming what is about to leave your machine, and never a side effect
of exporting. Two things will be worth reading carefully on that screen:

**The data goes with the map.** A Standalone HTML has every feature embedded in
it. Publishing it publishes the attributes too, including any column you left in
because it was convenient. Check the popup field list on the **Layers** tab
before you publish anywhere — including a host of your own.

**Authorisation is yours to confirm.** Licence terms on source data usually
distinguish between analysing it and republishing it, and the plugin cannot know
which of your layers are yours to publish.

## What an exported map does on the network

Opening one sends a single anonymous usage report to NIKA, as the OnlyMap
runtime licence covers — page counts and the hostname, never your map's data and
never anything identifying whoever opened it. That happens wherever the file is
opened, including from your own disk and your own web server. See
[privacy](privacy.md) for exactly what the report contains.

Nothing else leaves the page.

## If your site sets a Content Security Policy

Opening the exported file directly, or putting it on an ordinary static host,
needs nothing special. But if you serve it from a site that sets a strict
**Content Security Policy**, the map will not draw unless that policy allows
`unsafe-eval` for scripts.

This is not a quirk of one feature. The map renderer turns your QGIS symbology
into small expressions — the colour for each category, the class breaks of a
graduated layer, label text — and compiles them in the browser. That
compilation step is what a policy without `unsafe-eval` blocks, so a page that
forbids it loses the whole map, not just the styling.

What you will see: the map area stays empty and the browser console reports a
Content Security Policy violation.

The fix is to allow `unsafe-eval` in the `script-src` directive of the page
hosting the map, ideally scoped to just that page:

```
Content-Security-Policy: script-src 'self' 'unsafe-eval'
```

If the map uses layers with SVG or shaped markers, it also carries those markers
as embedded images, so the policy has to allow `data:` images:

```
Content-Security-Policy: script-src 'self' 'unsafe-eval'; img-src 'self' data:
```

Without it the markers do not appear and the rest of the map draws normally,
which is easy to mistake for a data problem.

If your organisation cannot relax that policy, serve the map from its own page
or subdomain with its own policy, and link or iframe it from the strict one.

Nothing here applies to double-clicking the exported file, sending it to
someone, or hosting it on a static host that sets no policy of its own — which
is how most exported maps are used.
