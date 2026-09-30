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
public link, and the button then says **Republish**: pressing it again on the
same project republishes to the same address rather than scattering a new link
with every edit.

The button follows the map on NIKA's side, not just what the project remembers.
While the dialog is open it checks the published map in the background (see
[privacy](privacy.md#if-you-publish-or-host-a-map) for what that sends), and if
the map has been taken down or deleted in the dashboard, no longer exists, or
belongs to a different NIKA organisation from the one you are signed in to, the
button says
**Host as new map**: pressing it publishes the project as a new map with a new
address and leaves the old one as it is. A paused map can still be republished;
it stays paused until it is resumed in the dashboard. An expired or stopped map
comes back online when you republish it, except on the free plan once its 7
days are over: NIKA then refuses the publish and says why, and only enterprise
hosting can bring that map back.

It is a separate, explicit step and never a side effect of exporting. Nothing
about pressing **Export** uploads anything, and nothing about pressing **Host**
changes the file you export.

### Taking a map down, and deleting it

Both happen in the NIKA dashboard, not in QGIS. So does everything else about
managing a published map — renaming it, pausing and resuming it, passwords and
access links. The plugin is only a way to put a map online, and NIKA refuses
its sign-in anything more.

**Take down** stops the map serving and keeps it: it can be restored from the
dashboard for 30 days, and its name is held for 7 days so an old link cannot
land on a different map.

**Delete permanently** — the red button on the map's page, which asks you to
type the map's name — skips all of that. Every address the map had answers
"not found" at once, its data is deleted, its name is free for another map
straight away, and nothing can bring it back: not the dashboard, not NIKA
support. A project that pointed at it offers **Host as new map**.

**What counts toward your storage:** only maps that are live. A map that is
paused, taken down, deleted, expired or stopped does not count, so any of those
frees its space for the next publish.

### How big a map can be

- **Free plan:** 25 MB per map, three maps, 75 MB in total.
- **Enterprise:** no per-map limit — maps of any size up to your storage.
- **Every plan:** no single file can be larger than 5 GB, because each file is
  sent in one upload and that is the most one upload can carry. NIKA refuses
  the publish before anything is uploaded and names the file.

Every size limit is checked before any map data is uploaded, so a refused
publish uploads nothing.

Size is not free for the people opening the map, though. Vector layers are
downloaded and read whole by the browser, so layers of hundreds of MB load
slowly, and a single layer file above roughly 500 MB will not open in browsers
at all. Rasters are read a tile at a time and are not affected.

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

**Anyone with the link can open it** — unless you give the map a password in the
NIKA dashboard, on plans that include password protection. Without one, a link
that has been shared cannot be unshared.

**The data goes with the map.** Every feature is published with it.
Publishing it publishes the attributes too, including any column you left in
because it was convenient. Check the popup field list on the **Layers** tab
before you publish anywhere — including a host of your own.

**Authorisation is yours to confirm.** Licence terms on source data usually
distinguish between analysing it and republishing it, and the plugin cannot
know which of your layers are yours to publish.

### What is actually uploaded

The map page, the data files for its layers, and a thumbnail of your canvas, and
nothing else. The confirmation screen names them, so the list you approve is
the list that is sent.

In particular the OnlyMap runtime — the ~8.3 MB of JavaScript that draws the
map — **is not uploaded**. It is byte-identical for every map built against the
same OnlyMap release, so NIKA already stores one copy per release and points
your map at it; what leaves your machine is the version number, not the bytes.
Two things follow from that. The upload is ~8.3 MB smaller than the folder on
your disk, and every byte counted against your plan is your own map and its
data.

### Hosted maps are not truncated

OnlyMap's free-plan caps — 5 layers, and 25,000 rows per layer — apply on a
hosted `http(s)` page unless the page carries a licence key. NIKA hosting serves
every map with one, on the free tier too, so a hosted map shows every layer and
every feature. The limits a free account meets are NIKA's own: the number of
maps, their size, and how long they stay up.

The one exception is a map built against an OnlyMap runtime older than the one
the key works with. The plugin knows this before anything is uploaded, and if
your project would lose layers or features, a second screen names every layer
that would be cut. You can publish anyway — the plugin never decides that for
you — but not without knowing.

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
