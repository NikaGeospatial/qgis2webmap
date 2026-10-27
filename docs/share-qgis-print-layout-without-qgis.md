---
title: Sharing a print layout with someone who has no QGIS
seo_title: Share a QGIS print layout with someone who has no QGIS
description: >-
  Send a client or colleague your QGIS print layout as one HTML file they can
  open, print at true size and explore - not a project file, not a dead PDF.
---

# Sharing a print layout with someone who has no QGIS

**To share a QGIS print layout with someone who does not have QGIS, send the
cartograph, not the project.** Export the layout as a cartograph - one HTML
file - and send that file. They double-click it, see the page in their
browser, and can print it at its real size. Maps you left live still pan and
zoom inside the page.

The one thing that catches people out: many mail filters strip `.html`
attachments. Zip the file before you email it.

## Why the usual answers fall short

**Sending the project.** A `.qgz` points at data on your machine, so it has to
travel with a folder of layers whose paths resolve on the other end - and the
recipient needs QGIS to open it. You have also handed over your whole dataset
to answer a request for one page.

**Sending a PDF.** Often right, and the cartograph can make one too. But a PDF
is final: the first time they ask to see the north end closer, or the same
sheet for the next parcel, you are back in QGIS exporting again.

**Sending a PNG.** All of the PDF's problems, and the text is no longer text.

## What a cartograph gives them

- **The page as you laid it out** - maps, legend, scale bar, north arrow,
  grid, title, logos - at the layout's page size.
- **Printing at true size** from Chrome or Edge. Firefox and Safari need the
  paper size picked in their print dialog - see
  [printing a cartograph](printing-a-cartograph.md).
- **Interactive maps where you chose them** - pan, zoom, click a feature for its
  attributes - and pixel-exact pictures everywhere else. See
  [interactive or image frames](live-or-static-frames.md).
- **Every page of an atlas** in the one file, turned with the arrow keys. See
  [atlas cartographs](cartograph-atlas.md).

What they need: a web browser. Not QGIS, not an account, and - unless a live
map draws an online tile layer - not an internet connection. An interactive map needs
JavaScript and WebGL, which practically every current browser has. A page of
only image frames needs neither: it reads even in a mail or file preview.

## What travels in the file

Be deliberate about this before you send it:

- An **Image** frame carries an **image** of the map. Nobody can recover the
  features or their attributes from it.
- An **Interactive** frame carries the **features of the layers it draws** - their
  shapes, and the attributes its popups show. Anyone with the file can open it
  in a text editor and read that data.

To keep attributes out, use the **Layers** section of the **Layout** tab - the
same list as the web map's Layers tab: untick
**Popups** on a layer, or set a field to *Do not show this field*, and those
values are not written into the file at all. A field the map draws with - the
one a categorised style or a label reads - stays, since it is visible on the
map anyway. To keep the features themselves out, set the frame to **Image**:
then the file holds only a picture. Database passwords and service
credentials are never written into the file in either case.

## Sending it

1. Make the cartograph - [your first cartograph](cartograph-first-export.md)
   takes about ten minutes. It saves itself; there is no export step.
2. **Share → Reveal in Finder** (**Show in Explorer**, **Show in file
   manager**) opens the folder it is in. **Copy file path** puts its path on the
   clipboard for an upload form.
3. **Zip it before emailing.** Mail providers and corporate filters often
   quarantine or strip `.html` attachments, because HTML is a common phishing
   vector - the file never arrives, and nobody is told. Right-click the file
   and choose **Compress** (macOS) or **Send to → Compressed (zipped) folder**
   (Windows). A cartograph is already compressed inside, so the zip is about the
   same size; it only changes the wrapper.
4. Chat apps, shared drives and USB sticks take the `.html` as it is.

## When to send a PDF instead

- **A print shop.** Send the PDF from **Save PDF…**, set up in
  [PDF settings](printing-a-cartograph.md#print-settings) with bleed, crop
  marks and, where they ask for it, CMYK.
- **A filing system that only takes PDFs.**
- **A reader on a very locked-down machine** that blocks JavaScript in local
  files - unless every frame is static, which reads without it.

Everywhere else, the cartograph is the better thing to send: it is the PDF
when they print it, and more than the PDF when they do not.
