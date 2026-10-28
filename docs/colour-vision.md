---
title: Colour vision
seo_title: Check a QGIS map legend for colour blindness
description: >-
  Simulate protanopia, deuteranopia, tritanopia and greyscale on the QGIS
  canvas and in the browser, and see which legend classes become impossible
  to tell apart.
lead_image:
  src: /images/cartograph-colour-vision.png
  alt: The Colour vision dialog simulating deuteranopia - Residential, Commercial and Park parcels are marked as looking alike, and Industrial is ok.
---

# Colour vision

About one man in twelve and one woman in two hundred see colour differently.
A legend whose classes are red and green can be a legend of one colour to
them. The **Colour vision** dialog shows you the map as they see it, and
checks the legend for you: which classes collapse into each other, and for
whom.

Open it with the **colour vision** button (the contrast icon) at the top
right of the dialog, on Cartograph (Beta). It floats beside the dialog and
stays open while you work.

![The Colour vision dialog, simulating deuteranopia.](images/cartograph-colour-vision.png)

## Simulate

Choose a condition under **Simulate**:

- **Normal vision**
- **Protanopia** - no red-sensitive cones
- **Deuteranopia** - no green-sensitive cones, the most common
- **Tritanopia** - no blue-sensitive cones, rare
- **Grayscale (achromatopsia)** - no colour at all; also how the page looks
  photocopied

Choosing one shows it at once on the layout preview, beside the table. Press
**Apply** to keep it on. Closing the dialog (**Close**, Escape or the window)
drops a choice you did not apply.

Apply keeps the simulation on in the plugin's own previews, and your choice is
remembered: it is still on after you close the dialog, switch layouts, reopen
Cartograph or restart QGIS. Two tick boxes add places to send it to:

- **Also apply to the QGIS map canvas** - filters the main QGIS window's map,
  the same as QGIS's own **View → Preview Mode**. Off by default.
- **Also open browser preview when applying** - reopens the cartograph in your
  browser with the same simulation applied to the whole page. Off each time
  the dialog opens.

While a simulation is on anywhere, the colour vision button stays pressed and
its tooltip names the condition. Choose **Normal vision** and Apply to switch
it off. A preview mode you had set in QGIS yourself before the simulation
reached the canvas is put back then. Saved files and exports always keep their
real colours.

## Check

The table lists every legend class, layer by layer:

| Column | What it is |
|---|---|
| **Layer**, **Class** | The legend entry |
| **Colour** | Its colour |
| **Simulated** | The same colour as the chosen condition sees it |
| **Note** | **ok**, or **like** the classes it can no longer be told apart from |

With **Normal vision** chosen, the table shows every collapse under any
condition, each marked with the condition it happens under.

Two classes count as collapsing when their simulated colours are closer than
a CIEDE2000 difference of 10 - roughly, too close to tell apart at swatch size
on a legend. Only classes of the same layer are compared, since a reader tells
two layers apart by where they sit in the legend. Classes that already look
alike with normal vision are not reported here: that is a different problem
with the palette.

It is the same check the cartograph's own validator runs, with the same
numbers, so the dialog and the file never disagree.

**Which classes are listed.** Every class of every layer drawn in an **Interactive**
frame, and the written-out rows of each legend on an **Image** frame - what
the saved cartograph's legends actually show. Each layer appears once, however
many frames draw it. The table describes the last save, and refreshes after every
save while the dialog is open; before the first save it is empty.

## Ask NIKA to fix

**Ask NIKA to fix ↗** is shown greyed out for now: the
[hand-off to NIKA](enhance-with-ai.md#hand-off-to-nika) is not available for
cartographs yet. The findings above name each pair, the conditions it
collapses under and how close it comes, which is what the advice below works
from.

## Fixing a palette yourself

- Vary **lightness**, not only hue. Two colours that differ in lightness stay
  apart for almost everyone, and in greyscale.
- Avoid pairing red with green, and blue with purple, for neighbouring
  classes.
- For a graduated layer, a single-hue ramp from light to dark survives every
  condition.
- Change the colours in QGIS; the cartograph saves itself, and the table
  updates.
