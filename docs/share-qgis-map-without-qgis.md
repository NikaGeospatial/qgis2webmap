---
title: Sharing with someone who has no QGIS
seo_title: Share a QGIS map with someone who has no QGIS
description: >-
  Send a colleague, client or stakeholder a QGIS map they can actually open - no QGIS install, no account, no data handover, no web server.
---

# Sharing with someone who has no QGIS

The request always arrives the same way. Someone who does not do GIS has seen
your map over your shoulder and wants "a copy". What they mean is that they want
to look at it themselves, later, without you in the room — and everything you can
easily give them is either a picture that cannot be explored or a project file
they cannot open.

This page is about closing that gap.

## Why the obvious answers do not work

**Sending the project file.** A `.qgz` is not self-contained. It points at data
that lives somewhere on your machine, so it has to travel with a folder of
layers, and the paths have to resolve on the other end. Even when all of that
works, you have asked someone to install a desktop GIS to read one map — and you
have handed over your whole dataset to answer a question that needed a look.

**Sending a screenshot or a PDF.** Genuinely the right answer sometimes. But the
first time they ask "can you zoom in on the north end" or "which parcel is that",
you are back to exporting a new image for every question.

**Putting it on a web GIS.** Fine if you already have one. Otherwise it is an
account, an upload and usually a seat limit — a lot of infrastructure for one
map, and it puts your data on someone else's server.

**Screen-sharing a call.** Works once. It does not survive the meeting, and it
does not answer the follow-up question next Tuesday.

## What QGIS2WebMap does instead

The plugin reads your saved project and writes **one HTML file**. Attach it to an
email, drop it in a shared folder, put it on a USB stick. The recipient
double-clicks it and gets your map in their browser.

What they get:

- your layers, in the order and styling you set in QGIS
- pan, zoom, and layer switching
- popups with the attributes you chose to expose
- 3D terrain and extrusion, if you enabled them

What they need: a browser. Not QGIS, not an account, not a login, and — apart
from a basemap if you chose one — not an internet connection.

What they do not get: your project file, your data files, or anything you
excluded on the Layers tab. Fields you hid in QGIS stay hidden in the export.

## The one thing that trips people up

**Mail filters strip `.html` attachments.** This is the single most common
delivery failure, and it is not about your file — HTML is a common phishing
vector, so many providers and most corporate filters quarantine or remove it on
sight. The map never arrives, and often nobody is told.

Export as **Share ZIP** instead. A zip passes filters that a bare `.html` does
not, and it carries a short README telling the recipient how to open it. It is
the same map; only the wrapper changes.

The other one: **do not use Folder mode for this.** Folder mode exists for
uploading to a web server. Browsers refuse to load the runtime from a separate
file on a `file://` page, so a folder export that someone unzips and
double-clicks will not draw. Standalone HTML or Share ZIP for anything that has
to open off a disk.

## If the map should live at a URL instead

Sending a file is right for one person, or a handful. If the map needs a link —
an intranet page, a public site, an embed in a report — export as **Folder** and
publish it, or use [Host with OnlyMap](hosting.md) to put it online from the
export itself. Read the Content Security Policy note on that page first if the
site you are embedding into already sets one; it is what silently blanks an
otherwise-working map.

## Before you send it

Look at the **Fidelity** tab. It names everything that changed on the way out of
QGIS — symbology that could not be translated exactly, settings that will not
appear, layers left out — while you can still do something about it. The point of
the tab is that the recipient never has to be the one who discovers a layer is
missing.

Then press **Open exported map** and look at the real file yourself. It is the
same file they will get.

## Where to go next

- [How to export QGIS to a web map](qgis-to-web-map.md) — the short version
- [Your first export](first-export.md) — the full walkthrough
- [Sharing a map](sharing.md) — the three output modes in detail
- [Privacy](privacy.md) — what an exported map sends, and what it never sends
