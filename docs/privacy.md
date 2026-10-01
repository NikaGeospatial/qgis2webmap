---
title: Privacy
seo_title: What an exported QGIS web map sends
description: >-
  Exported maps send one anonymous usage report when they load, and nothing else. What is sent, what is in the file, and how to verify it.
---

# Privacy

**Exported maps send one anonymous usage report when they load, and nothing else.**

Opening an exported map sends that one report and nothing more. Aside from
that, it works with the network cable unplugged, on a machine that has never
had QGIS installed.

## What that means concretely

- One anonymous usage report each time the map finishes loading. See below
  for exactly what is in it.
- No basemap tiles fetched from a server - unless you choose a basemap; see
  below.
- No fonts, scripts or styles loaded from a CDN - everything is inside the
  file.
- No identifier of any kind for whoever opens the map.

You can check this yourself: open an exported map in a browser, open the
developer tools Network tab, and reload. You will see exactly one request,
to NIKA's telemetry service.

## The one report: usage telemetry

Every exported map sends a small, anonymous usage report each time it
finishes loading. This comes from the OnlyMap runtime the map is built on -
it is not something this plugin adds - and it is described in the runtime's
licence.

The report can include:

- the OnlyMap runtime version
- counts of which features and layers the map uses
- which widgets are on the map, such as the legend or scale bar
- the hostname of the page the map is running from
- a map identifier, only if you set one
- a sanitised description of an error, if the map hit one

**Read the hostname point twice if you host on a private server.** The
hostname does not identify your data or who is viewing the map, but if you
host it at an internal address such as `maps.yourcompany.internal`, that
hostname is what gets sent - and that can reveal which organisation is using
the map.

The report never includes who opened the map, what page or URL they were on,
cookies, the map's data, or anything else about its contents. IP addresses
are not stored.

There is no setting in the plugin to turn this off today. It can be turned
off by hand, by editing `telemetry="off"` into the exported HTML file's
`<om-map>` tag after export - see the licence for the technical detail.

## The one exception: basemaps

There is no basemap by default, and a default export makes no basemap
requests at all.

If you choose one on the Map tab, that changes for everyone you send the map to:
their browser fetches tiles from the provider each time they open it. The
provider necessarily sees those requests, including the approximate area being
looked at.

Nothing else about the export changes because of a basemap choice - no
additional identifier travels with it, and the file is no larger. But the
"nothing else is fetched" guarantee only holds with the basemap set to None,
so the dialog warns in red when it is not, and the Fidelity tab names the
provider.

## What is in the file

The map's data, taken from your QGIS project. If a layer came from a database or
a service that needed a username or password, **the credentials are not written
into the map** - only the features that were read.

Be aware that the data itself is in the file, in full. If a layer contains
information that should not be shared, do not include that layer in the export.
The Fidelity tab lists exactly which layers were included.

## The one thing exporting downloads

Exporting downloads one thing in its entire life: the first time you build a
map, the plugin downloads the OnlyMap runtime from npm — about 4.8 MB, once per
computer, after showing you the licence and asking. See
[installation](installation.md). The only other request an export can cause is
a research report, and only if you chose **Share** — see
[research sharing](#research-sharing-off-unless-you-choose-share) below.

That request sends nothing about you or your data. It is an anonymous download
of a public package: no account, no token, no identifier, and nothing about
your project, your layers or your machine. npm can see that some computer at
your IP address downloaded a public file, which is what any software download
looks like.

After that, with research sharing off (the default), exporting never contacts
anything again. It works fully offline — your data is read from disk and written
into the file, and no part of it leaves your computer.

## Research sharing: off unless you choose Share

To decide what to build next, NIKA asks — once, in the "What's new" window
shown after an update — whether you are willing to share a short, anonymous
description of the maps you make. **Nothing is sent unless you click Share.**
Don't share, or closing the window without choosing, sends nothing. You can
change your mind at any time on the **Help** tab, under **Research sharing**,
which also has **See exactly what is sent**: the report built from your open
project, in the exact form it would be sent.

Before that window, an optional **About you** step asks who your maps are for,
your sector and what the maps do. It does not ask for your name or email. The
answers are stored on your computer and are only sent if you choose Share.

If you choose Share, the plugin sends, to NIKA's API:

- **After an export or publish, once per map** (and again only if its layers
  are added, removed or renamed, or their fields change): the map title; each
  layer's name, type (point, line, polygon, raster), source type (file,
  database, web service), format (such as `gpkg` or `shp`), a feature-count
  band such as `101-1k`, its field *names*, and whether it has a date field,
  labels, popups and which kind of styling; the project's coordinate system
  (such as `EPSG:27700`); how large an area the map covers, as a band from
  `site` to `world`; the centre of the map rounded to the nearest whole degree
  (about 110 km); the basemap and relief presets; which features you used
  (legend, popups, relief, labels and so on); the output type; and whether the
  project's time settings are on. Each map's reports also carry a **random
  code made for that map** (`map_id`), so NIKA can tell that two reports are
  two versions of the same map. It is made from random numbers alone, is kept
  only in the file described below, and is never linked to you, your computer
  or your NIKA account — it is not the hosted map's ID, and choosing Don't
  share deletes it.
- **Once a week**: counts of exports, previews, publishes and failures, how
  many different maps you exported, how many of them you also exported in
  an earlier week, and the week's exports counted by the local time of day
  you made them, in six four-hour blocks (midnight–4 am, 4–8 am and so on).
- **With the map and weekly reports**: your computer's time zone, as a whole
  number of hours from UTC (for example `8` for Singapore, `6` for India's
  +5:30).
- With each report: your About you answers, the plugin and QGIS versions, and
  your operating system (Windows, macOS, Linux).

**Never sent:** feature values or geometry, any coordinate finer than a whole
degree, file paths, web addresses, user names or passwords, your IP address
(NIKA's server uses it only transiently to limit request rates, and never
stores or logs it), or any ID that links reports to you, your computer or your
NIKA account.
Before anything is stored for sending, every name and title is cleaned: a path
is cut to its file name, web addresses are removed, and email addresses and
long numbers are replaced with `[email]` and `[number]`.

How it works:

- Everything is kept in one small file, `qgis2webmap/research.json`, in your
  QGIS profile folder. To decide whether a map has already been reported, the
  plugin keeps a salted hash of the project and its layer structure there; the
  salt is random per computer, and neither it nor the hash is ever sent. Each
  map's random code is kept there too, beside that hash.
- Reports are sent in the background through QGIS's own network settings,
  without your NIKA sign-in, and never delay an export. If they cannot be sent
  they wait (at most 50) and are retried the next time you open the dialog or
  export. One that NIKA's server keeps turning away is deleted after five
  tries rather than kept forever.
- Choosing **Don't share** later deletes anything still waiting to be sent,
  along with the maps' random codes: if you share again, your maps get new ones.
- Research sharing is temporary and ends for good in a few versions: the
  plugin then sends nothing and deletes anything waiting, whatever you chose.
  NIKA can also end it earlier from its side, and the plugin then stops for
  good.

## If you publish or host a map

Hosting is the exception, and necessarily so: putting a map on NIKA only works
by talking to NIKA's servers. Publishing to NIKA hosting, and asking an AI
assistant to modify a map, are separate actions that you start yourself. Nothing
is uploaded automatically, and a project you never press **Host** on never
causes a request to NIKA — apart from the research reports above, and only if
you chose **Share**.

What the hosting features send, all of it to NIKA's API:

- **Signing in.** Your browser opens NIKA's sign-in page; the plugin receives a
  sign-in token, which is stored on this computer and never in the project.
  That token is for publishing: besides reading your account details, it can
  only publish maps and check a published map's state. NIKA refuses it the
  rest — it cannot list, take down, delete, rename or password-protect a map,
  and cannot reach billing — so a copy of it is not a way to manage your maps.
- **Publishing.** First a description of the map — its title, file names, sizes
  and checksums, and the runtime version — and then, once you have confirmed,
  the map's files and a thumbnail.
- **Checking the published map.** While the dialog is open on a project that
  has been published, the plugin asks NIKA about that one map — when the dialog
  opens, when you come back to it, after a publish, and about once a minute — so
  the Host button can say whether the map can still be updated, or has been
  taken down. Each check sends the map's id and your sign-in token, and nothing
  about the project's contents. It is skipped when you are not signed in, and
  fails silently when you are offline.

## This website

Everything above is about the plugin and the maps it exports. This
documentation site is a separate thing, and it does count its visitors.

It uses PostHog to record which page was read, which site or link you arrived
from, and roughly how long the page was open, along with your country and
browser. Nothing is stored on your device - no cookies, no local storage - so
there is no consent banner to dismiss, and no way for the site to recognise you
on a later visit. You are never identified, and none of this touches the plugin,
your QGIS projects, or any map you export.

## Source

The plugin is GPL-2.0-or-later. You can read exactly what it does at
[github.com/NikaGeospatial/qgis2webmap](https://github.com/NikaGeospatial/qgis2webmap).
