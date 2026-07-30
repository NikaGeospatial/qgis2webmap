# Sharing a map

Three ways to share, chosen on the **Map** tab.

## Standalone HTML — the default

One file. Everything is inside it: the map, its styling and all of its data.

Send it, copy it to a USB stick, put it on a shared drive. The recipient
double-clicks it. They need a web browser and nothing else - no QGIS, no account,
no internet connection.

## Share ZIP

The same map, zipped, with a short README explaining how to open it.

Worth choosing even when the file is small: **many mail providers and corporate
filters quarantine or strip `.html` attachments**, because HTML is a common
phishing vector. A zip usually passes filters that a bare `.html` does not. If
someone says your map "never arrived", try this.

## Folder

The map and its README as loose files, for uploading to a web server yourself.

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
