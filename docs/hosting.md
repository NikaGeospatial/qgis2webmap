---
title: Putting a map online
seo_title: Host a QGIS web map online
description: >-
  Where an exported map can live once you have made it, how one-click NIKA hosting works and what its confirmation screen is telling you, and the Content Security Policy trap that empties the map.
---

# Putting a map online

An exported map is an ordinary file. Nothing about it needs NIKA, and nothing
about it uploads on its own — putting it somewhere is a thing you do, not a
thing it does.

## Hosting it yourself

The export is plain HTML with no server requirement, so any static host works:
GitHub Pages, S3, Netlify, or a folder on a web server you already run. Choose
the **Folder** output mode if you would rather copy a directory than a single
file.

For many maps that is a good answer, and nothing about an exported file needs
NIKA to serve it.

## Hosting with NIKA

Press **Host** in the export dialog. The map is uploaded to NIKA and you get a
public link, and pressing Host again on the same project republishes to the
same address rather than scattering a new link with every edit.

It is a separate, explicit step and never a side effect of exporting. Nothing
about pressing **Export** uploads anything, and nothing about pressing **Host**
changes the file you export.

### What happens, in order

1. **You sign in, once.** Your browser opens NIKA's sign-in page and you
   approve this computer there. The plugin never sees your password, and the
   token it is given is stored per machine — deliberately *not* in the `.qgz`,
   because a project file gets emailed, committed and handed to a contractor.
2. **The map is built on your machine.** Nothing has left it yet.
3. **You confirm.** The screen names the map, the files, their total size, and
   how many layers and features are in them.
4. **Upload, then publish.**

### What publishing means, and why

The confirmation screen names what leaves your machine and stops there. The
three points below are the ones worth understanding before the first publish
rather than re-reading on every one, so they live here instead of in the
dialog.

**Anyone with the link can open it.** A hosted map has no password, and a link
that has been shared cannot be unshared.

**The data goes with the map.** Every feature is embedded in the page.
Publishing it publishes the attributes too, including any column you left in
because it was convenient. Check the popup field list on the **Layers** tab
before you publish anywhere — including a host of your own.

**Authorisation is yours to confirm.** Licence terms on source data usually
distinguish between analysing it and republishing it, and the plugin cannot
know which of your layers are yours to publish.

### What is actually uploaded

The map page and a thumbnail of your canvas, and nothing else. The confirmation
screen names both, so the list you approve is the list that is sent.

In particular the OnlyMap runtime — the ~8.3 MB of JavaScript that draws the
map — **is not uploaded**. It is byte-identical for every map built against the
same OnlyMap release, so NIKA already stores one copy per release and points
your map at it; what leaves your machine is the version number, not the bytes.
Two things follow from that. The upload is ~8.3 MB smaller than the folder on
your disk, and your plan's per-map size allowance is spent entirely on your own
map and its data.

### The free plan truncates a hosted map

This is the one that surprises people, because it does not apply to the file
you export and open yourself. OnlyMap's free-plan caps — 5 layers, and 25,000
rows per layer — apply on a hosted `http(s)` page and nowhere else. A project
past them, published on a free account, is **visibly incomplete to whoever
opens the link**, and the plugin has to tell you that before it uploads rather
than let your audience discover it.

So it does: if your account is on the free tier and the project is past the
caps, a second screen names every layer that will be cut before anything is
uploaded. You can publish anyway — the plugin never decides that for you — but
not without knowing.

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
