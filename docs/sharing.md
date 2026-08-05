# Sharing a map

Three ways to share, chosen on the **Map** tab.

## Standalone HTML — the default

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
stricter. The Map tab shows your map's data size next to this option, and warns
before exporting if it is over the limit - you can still export the single file
if you want it. To make it smaller, untick **Popups** on layers that do not need
them, or reduce **Coordinate precision** on the Map tab.

## Share ZIP

The same map, zipped, with a short README explaining how to open it.

Worth choosing even when the file is small: **many mail providers and corporate
filters quarantine or strip `.html` attachments**, because HTML is a common
phishing vector. A zip usually passes filters that a bare `.html` does not. If
someone says your map "never arrived", try this.

## Folder

For uploading to a web server yourself. You get a small `index.html`, the
OnlyMap runtime as a separate `onlymap.js`, and a README. Splitting them is the
point: a browser caches `onlymap.js` once and reuses it for every map on your
site, instead of downloading the same few megabytes inside every page.

**This one does not open by double-clicking it.** Browsers refuse to load the
runtime that way, so the folder has to be served over `http://` or `https://`.
Use Standalone HTML if you want a file that opens straight off a disk.

## Which to pick

| Situation | Use |
|---|---|
| Emailing it to one person | Standalone HTML, or Share ZIP if it is blocked |
| Posting it in a chat app | Standalone HTML |
| Attaching to a report or ticket | Share ZIP |
| Publishing on your own site | Folder |

## What the recipient sees

An interactive map: pan, zoom, toggle layers, click a feature to see its
attributes.

If they preview the file inside an email client or a file manager - somewhere
that does not run JavaScript - they see a short message telling them to open it
in a browser, rather than a blank window.
