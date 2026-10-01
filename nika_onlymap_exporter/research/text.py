"""What the dialogs say about research sharing, in one testable place.

Kept out of the Qt code for the reason `hosting/consent.py` is: this wording is
a promise about what leaves the machine, and it is easier to keep true when it
can be read and tested without a QGIS application.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from . import consent

# Spelled out rather than formatted: `%B` follows the machine's locale and
# `%-d` does not exist on Windows. A unit test holds it to `RESEARCH_ENDS`.
END_DATE_TEXT = "31 January 2027"

ABOUT_YOU_INTRO = (
    "A few questions about who your maps are for help us decide what to build "
    "next. Every question is optional. If you choose Share in the next step, "
    "your answers are sent to NIKA with each report; if not, they stay on "
    "this computer."
)

ABOUT_YOU_NOTHING_SENT = "Nothing is sent unless you choose Share in the next step."

RESEARCH_HEADING = "Help us decide what to build"

RESEARCH_WHAT = (
    "If you choose Share, the plugin sends NIKA a short description of each map "
    "you export or publish, once per map and again only when its layers change: "
    "the map title, layer and field names, layer types and formats, a rough "
    "feature count, the project's coordinate system, how big an area the map "
    "covers, its centre rounded to the nearest whole degree (about 110 km), and "
    "which features you used. A random code made for each map links that "
    "map's versions together; it is never linked to you, this computer or "
    "your NIKA account. Once a week it also sends counts of exports, previews, "
    "publishes and failures, with the exports also counted by the time of day "
    "you made them (in four-hour blocks). The map and weekly reports include "
    "your time zone, to the nearest hour. Your About you answers go with each "
    "one."
)

RESEARCH_NEVER = (
    "Never sent: feature values or geometry, anything more precise than a whole "
    "degree, file paths, web addresses, user names or passwords, or any ID that "
    "links reports to you, this computer or your NIKA account. Names are "
    "cleaned first: paths are cut to the file name, and web addresses, emails "
    "and long numbers are removed."
)

RESEARCH_CHANGE = (
    "Nothing is sent unless you choose Share. You can change your mind at any "
    "time on the Help tab. Research sharing stops for good on "
    f"{END_DATE_TEXT}."
)

SEE_WHAT_IS_SENT = "See exactly what is sent"


def choice_line(state: str, over: bool) -> str:
    """One sentence stating the current choice and where to change it."""
    if state == consent.ENDED or over:
        return "Research sharing has ended. Nothing is sent."
    if state == consent.SHARE:
        return (
            "Research sharing is on: map descriptions and weekly counts are "
            "sent. Change this on the Help tab."
        )
    if state == consent.DONT_SHARE:
        return "Research sharing is off: nothing is sent. Change this on the Help tab."
    return "Research sharing is off until you choose Share on the Help tab."
