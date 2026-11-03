---
title: Printing a cartograph
seo_title: Print a QGIS layout from the browser at true page size
description: >-
  Print a cartograph at its real page size, export a PDF or a 300 dpi PNG, and
  set bleed, crop marks, safe zone and CMYK or ICC colour for a print shop.
lead_image:
  src: /images/cartograph-print-settings.png
  alt: The Print settings dialog - page size from the layout, 300 dpi, 3 mm bleed, crop marks and safe zone ticked, and the RGB browser-print profile.
---

# Printing a cartograph

A cartograph knows its own page size. Open it in a browser, print, and an A3
layout comes out as A3 - no QGIS, no re-export. This page covers printing it
yourself, sending it to someone who will, and preparing it for a print shop.

## Save PDF

**Save PDF…** in the dialog's header (or **Ctrl+S** there) opens the system save
dialog, suggesting the layout's name, and QGIS writes every page of the layout
as it is right now, edits included. It needs no saved HTML and never opens a
browser. Interactive frames are drawn static in the PDF; the HTML keeps them
interactive. The PDF is made from a copy, so the layout, its frame choices, the
selection, the atlas position and Undo are untouched. The result shows in the
status line with **Open PDF** and **Show in folder**; a failure says why and
keeps your layout. Page size, bleed, crop marks, resolution and colour profile
come from [PDF settings](#print-settings).

## Print from the browser

For a PDF from the plugin, use **Save PDF…** (**Ctrl+S** in the dialog): QGIS
writes it from the layout, with no browser involved - see
[Save PDF](#save-pdf). To print the HTML cartograph itself, open it in a browser
(**Share → Preview in browser**) and print with **Ctrl+P** (**Cmd+P** on
macOS); to get a PDF that way, choose **Save as PDF** as the printer.

- **Chrome, Edge, Brave, Opera and other Chromium-based browsers** print at
  the page's true size. Leave the margins at their default; the page sets its
  own.
- **Firefox and Safari** ignore the page size a web page asks for and print on
  whatever paper is selected in the print dialog, scaled to fit. Choose the
  matching paper size there, or print from a Chromium-based browser. This is a
  browser limitation, not something the file can fix.

Text stays text in the PDF - selectable and searchable - and the legend, scale
bar, north arrow and grid stay sharp vector shapes.

**For a page with interactive frames, print through `?print=1`, not Ctrl+P.** An interactive
map is drawn by the graphics card, and browsers print such a map blank or at
screen resolution. (The plugin's Save PDF… avoids this: QGIS draws every
interactive frame as a static map of its current view.) `?print=1` added to the
cartograph's address: the page waits until every map has finished drawing, swaps each
interactive map for an image rendered at print resolution, and only then opens the
print dialog. The browser's own **Ctrl+P** does none of that - the print
dialog opens at once, before an image could be made.

Anyone can do the same without the plugin: add `?print=1` to the end of the
cartograph's address in the browser (`…/Parcel-survey.html?print=1`) and
press Enter.

A page of only image frames prints the same either way.

## Export PNG

**Share → Export PNG...** opens the cartograph, which renders the page as a
PNG at the **saved resolution** (300 dpi unless Print settings say otherwise) and downloads it through your browser - into your Downloads
folder, as with any download. Interactive frames are re-rendered at that resolution,
not enlarged from the screen.

For an atlas, see [exporting every page](cartograph-atlas.md#exporting-every-page).

## Print settings

**Share → PDF settings...** sets up the page for print. The settings are
stored **per layout**, with the project, and written into the cartograph on
every save - so a cartograph you send someone prints with them too.

![The Print settings dialog.](images/cartograph-print-settings.png)

Until you save them once, a cartograph has none of these: no bleed, no marks,
the layout's own page size. The dialog opens with a print-shop starting point
of 300 dpi, a 3 mm bleed, crop marks and the safe-zone guide.

### Page

- **Size** - the layout's own page (*A4 landscape (from layout)*), a preset
  (A5 to A0, Letter or Legal, portrait or landscape) or **Custom...** in
  millimetres. Items keep their positions measured from the top-left corner,
  so a page smaller than the layout cuts off whatever falls outside it.
- **Resolution** - **150**, **300** or **600 dpi**: how finely interactive frames are
  rendered when the page is printed, and the resolution of a PDF made by QGIS.
  An image frame keeps the DPI set for it in Map frames.
- **Bleed** - **None**, **3 mm**, **5 mm** or **1/8 in**. The printed sheet
  grows by that much on every side, so a slightly-off cut shows no white edge:
  A3 with a 3 mm bleed prints on a 303 × 426 mm sheet. Items keep their
  positions from the trim corner.
- **Crop marks** - trim marks at the four corners, drawn in the bleed. They
  need a bleed to sit in, so the box is greyed out with **None**.
- **Safe zone guide** - a line 5 mm inside the trim, on screen only, showing
  where text risks being cut. It is never printed and never exported.

### Colour

- **Profile**:
  - **RGB (sRGB) — browser print** - the default. Screens and browsers are RGB;
    fine for office printers, and for posters printed from a PDF.
  - **CMYK PDF/X-4 — via QGIS** - see below.
  - **Printer ICC profile...** - pick the print shop's `.icc` or `.icm` file;
    see below.
- **Flatten to image** - prints one picture instead of text, shapes and
  images. Text stops being selectable, but some print shops' machines handle
  it better. Off by default.
- **Embed fonts** - for a PDF made by QGIS: off turns text into outlines.

### Buttons

- **Save PDF…** - saves the settings and saves the PDF straight away.
- **Save** - keeps the settings for this layout without exporting.
- **Save as preset...** - keeps them under a name, for every project. Pick a
  saved one from the list at the top.

## CMYK and print-shop profiles

A browser can only print RGB. A print shop that asks for **CMYK**, a
**PDF/X-4** file or their **ICC profile** needs the colour management QGIS adds to
the same Save PDF…, from the same PDF settings:

1. Choose **CMYK PDF/X-4 — via QGIS** or **Printer ICC profile...**.
2. Press **Save PDF…** and choose where to save.

QGIS prints a copy of your print layout - every page, grown by the bleed, with the
crop marks drawn in - using the chosen colour model, and puts your project's
colour settings back afterwards. Your layout itself is never changed.

The **Title** and **Theme** in Page settings are part of the layout, so the PDF
carries them. A page **attribution** with no text item on the page to carry it
is left out of the PDF, and the status line says so after the export; add it to
the layout as a label.

With **CMYK**, the PDF is PDF/X-4 when the project has a CMYK profile set in
**Project Properties → Styles**; without one you get CMYK colours but no output
profile, and the plugin says so. With an **ICC profile** (ask the shop - FOGRA39
is common in Europe, GRACoL in North America), it is embedded as the PDF's
output intent.

Both choices are **greyed out, with the reason in their tooltip**, when:

- QGIS is older than 3.40, or **built on a Qt older than 6.8** - QGIS 3 LTR
  builds are Qt 5, and many QGIS installs are not yet on Qt 6.8. The tooltip
  names the version you have.
- **Any frame is Interactive.** QGIS draws the layout, not the browser, so it cannot
  draw an interactive frame as the cartograph shows it. Set every frame to **Image**
  in Map frames, on the Layout tab.

A saved CMYK choice that this QGIS cannot honour shows as RGB, with a line
saying why - it is never swapped silently.

The Page settings section's **Title**, **Attribution** and **Theme** are part
of the layout, so a CMYK or ICC PDF carries them. Only an attribution that has
no text item on the page is left out, and the export asks about it.

In a PDF made by QGIS, text and shapes stay vector. Only a layer or item that
uses transparency, a blend mode or an effect is rasterised, at the saved
resolution, so it prints as it looks instead of opaque; the export says so.

## Printing an atlas

**Save PDF…** saves the layout's own pages; for the atlas use **Share → Save atlas as
PDF…**, which saves one run of pages per feature in one PDF (it never swaps in
just the feature on screen). The pager at the foot of the sheet is never printed.

For the whole series, use **Print all pages...** on the
[Atlas tab](cartograph-atlas.md#exporting-and-printing-every-page): every page
is rendered and laid out for one print job, and **Save as PDF** gives one
multi-page PDF, each page at its true size in a Chromium-based browser. The
pages are images rendered at 200 dpi, so text in that PDF is not selectable.
**Export all pages (PNG, ZIP)...** beside it gives every page as a 300 dpi PNG
instead.

## Sending it to someone who will print it

- Send the cartograph itself - see
  [sharing a cartograph](share-qgis-print-layout-without-qgis.md).
- Tell them to print from Chrome or Edge, or to pick the right paper size in
  Firefox and Safari.
- If the page has interactive frames, consider making them Image frames first: a page of Image frames
  prints the same however it is printed, and needs no graphics support.
- For a print shop, send the PDF from **Save PDF…**, not the HTML.
