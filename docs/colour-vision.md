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

Open it with **Accessibility** in the corner of the dialog, on Cartograph
(Beta). <!-- verify: the Accessibility button is wired to this dialog - in the
current build it is disabled with the tooltip "Coming in a later version" -->

![The Colour vision dialog, simulating deuteranopia.](images/cartograph-colour-vision.png)

## Simulate

Choose a condition under **Simulate**:

- **Normal vision**
- **Protanopia** - no red-sensitive cones
- **Deuteranopia** - no green-sensitive cones, the most common
- **Tritanopia** - no blue-sensitive cones, rare
- **Grayscale (achromatopsia)** - no colour at all; also how the page looks
  photocopied

It applies to two places, each with its own tick box:

- **QGIS canvas** - filters the map canvas, the same as QGIS's own
  **View → Preview Mode**.
- **Browser preview** - reopens the cartograph in your browser with the same
  simulation applied to the whole page.

While a simulation is on, the **Accessibility** button stays pressed and its
tooltip names the condition. Closing the dialog leaves the canvas as you set
it - choose **Normal vision** to switch it off. A preview mode you had set in
QGIS yourself before opening the dialog is left alone.

## Check

The table lists every legend class, layer by layer:

| Column | What it is |
|---|---|
| **Layer**, **Class** | The legend entry |
| **Colour** | Its colour |
| **Simulated** | The same colour as the chosen condition sees it |
| **Note** | **ok**, or **like** the classes it can no longer be told apart from |

Under the table, one line sums it up: *2 pairs of classes collapse under
deuteranopia*, or *All classes stay distinguishable under every condition*.
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

<!-- verify: which legend classes the table lists - the dialog itself says
"Save the cartograph with a live map frame first" when it has none -->

## Ask NIKA to fix

**Ask NIKA to fix ↗** opens the [hand-off dialog](enhance-with-ai.md#hand-off-to-nika)
with the findings already written into the prompt - each pair, the
conditions it collapses under and how close it comes - so an AI agent can
propose a safer palette. It is greyed out when nothing collapses.

## Fixing a palette yourself

- Vary **lightness**, not only hue. Two colours that differ in lightness stay
  apart for almost everyone, and in greyscale.
- Avoid pairing red with green, and blue with purple, for neighbouring
  classes.
- For a graduated layer, a single-hue ramp from light to dark survives every
  condition.
- Change the colours in QGIS; the cartograph saves itself, and the table
  updates.
