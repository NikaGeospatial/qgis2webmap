---
title: Host with OnlyMap
description: >-
  Publishing an exported map. What you are asked before anything is uploaded, visibility options, and the Content Security Policy trap.
---

# Host with OnlyMap

Every exported map carries a **Host with OnlyMap** link in its bottom-right
credit component. Hosting turns the file you already checked into a shareable
link, without re-exporting anything.

[OnlyMap](https://onlymap.nikaplanet.com/) is the map library that draws every
map this plugin exports; its own documentation covers what the runtime can do
once your map is published.

## Nothing uploads on its own

An exported map is a file on your disk. Opening it sends one anonymous usage
report and nothing else — your map is not uploaded, and the **Host** link is a
link, not an upload. It opens the OnlyMap hosting page and asks you to choose
the exported file yourself. See [privacy](privacy.md) for what the report
contains.

This is deliberate. A page opened from disk cannot honestly ask for consent on
your behalf, so it does not try.

## What you are asked before anything is published

> You are about to upload a copy of this map and its included data. Confirm that
> you are authorised to publish it and choose Public, Unlisted, or Private
> visibility.

Two things worth reading carefully:

**The data goes with the map.** A Standalone HTML has every feature embedded in
it. Publishing it publishes the attributes too, including any column you left in
because it was convenient. Check the popup field list on the **Layers** tab
before you host.

**Authorisation is yours to confirm.** Licence terms on source data usually
distinguish between analysing it and republishing it. The plugin cannot know
which of your layers are yours to publish.

## Visibility

| Choice | Who can open it |
|---|---|
| **Public** | Anyone, and it may be indexed by search engines |
| **Unlisted** | Anyone holding the link |
| **Private** | You, and accounts you grant access to |

## Publishing from inside QGIS

Not in `0.1.0`. A **Publish with OnlyMap** action in the export dialog is
planned, and it will be a separate, explicit step after account sign-in with the
same data-inventory confirmation — never a side effect of exporting.

For now: export locally, open the map, check it, then use the **Host** link.

## Hosting somewhere else

The export is a plain HTML file with no server requirement, so any static host
works — GitHub Pages, S3, Netlify, or a folder on a web server. Choose the
**Folder** output mode if you would rather copy a directory than a single file.

Nothing in the artifact phones home to NIKA, whether you host with OnlyMap or
not.

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
