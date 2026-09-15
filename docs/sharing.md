---
title: Sharing a map
seo_title: Share a QGIS map as one HTML file
description: >-
  The map file, a share ZIP or a folder - which to reach for, and why an email filter is usually what decides it.
lead_image:
  src: /images/export-02-formats.png
  alt: The Share menu, with the map file, share ZIP and folder rows.
---

# Sharing a map

Three ways to share, all of them on the **Share** menu in the top-right corner of
the dialog.

You do not choose between them in advance any more. Your map is one `.html` file
- the **Map file** on the Map tab - and a zip or a folder is made from it at the
moment you need one. Every Share row writes the file first if it needs to, so
there is nothing to press beforehand.

## The map file — the default

One file. Everything is inside it: the map, its styling and all of its data.

Send it, copy it to a USB stick, put it on a shared drive. The recipient
double-clicks it. They need a web browser and nothing else - no QGIS, no account,
no internet connection.

**It is the largest of the three, and that is inherent to the format.** Every
feature is written into the HTML itself, because there is no sibling folder for
it to live in - that is exactly what makes the file self-contained. A map whose
data runs to 20 MB produces a file of at least that size.

Nothing in a browser objects to a large HTML file. What objects is email: most
providers reject attachments over about 20-25 MB, and corporate filters are often
stricter. The Map tab shows your map's data size under the Map file field, and
warns if it is over the limit - the file is still written if you want it. To make it smaller, untick **Popups** on layers that do not need
them, or reduce **Coordinate precision** on the Map tab.

## Share ▸ Export share ZIP

The same map, zipped, with a short README explaining how to open it. Your map
file stays where it is - this is a copy for somebody else.

Worth choosing even when the file is small: **many mail providers and corporate
filters quarantine or strip `.html` attachments**, because HTML is a common
phishing vector. A zip usually passes filters that a bare `.html` does not. If
someone says your map "never arrived", try this.

## Share ▸ Export folder for hosting

For uploading to a web server yourself. You get a small `index.html`, the
OnlyMap runtime as a separate `onlymap.js`, and a README. Splitting them is the
point: a browser caches `onlymap.js` once and reuses it for every map on your
site, instead of downloading the same few megabytes inside every page.

**This one does not open by double-clicking it.** Browsers refuse to load the
runtime that way, so the folder has to be served over `http://` or `https://`.
Your map file is the one that opens straight off a disk.

## Which to pick

| Situation | Use |
|---|---|
| Emailing it to one person | The map file, or **Export share ZIP** if it is blocked |
| Posting it in a chat app | The map file |
| Attaching to a report or ticket | **Export share ZIP** |
| Publishing on your own site | **Export folder for hosting** |
| Checking it yourself before sending | **Open in browser** - the real file, not a preview copy |

## What the recipient sees

An interactive map: pan, zoom, toggle layers, click a feature to see its
attributes.

If they preview the file inside an email client or a file manager - somewhere
that does not run JavaScript - they see a short message telling them to open it
in a browser, rather than a blank window.
