"""The research choice, and the one gate everything that collects goes through.

Four states:

* `unset` - never asked, or asked and not yet answered. Nothing is collected.
* `share` - the user clicked Share. Reports are counted, queued and sent.
* `dont_share` - the user clicked Don't share. Nothing is collected, ever,
  until they change it on the Help tab.
* `ended` - the server answered `410`: research is over. Permanent; no later
  choice reopens it.

`RESEARCH_ENDS` is the plugin's own hard stop, independent of the server: after
that date nothing is counted, queued or sent even if the server still accepts.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from datetime import date

UNSET = "unset"
SHARE = "share"
DONT_SHARE = "dont_share"
ENDED = "ended"

STATES = (UNSET, SHARE, DONT_SHARE, ENDED)

RESEARCH_ENDS = date(2027, 1, 31)


def research_over(today: date) -> bool:
    """Whether the hard stop has passed. The end date itself is the last day."""
    return today > RESEARCH_ENDS


def may_collect(consent: str, today: date) -> bool:
    """The gate: count, queue or send only on an explicit Share, and only in time."""
    return consent == SHARE and not research_over(today)


def can_ask(consent: str, today: date) -> bool:
    """Whether the Share / Don't share question is still worth putting to anyone."""
    return consent != ENDED and not research_over(today)


def normalise(value: object) -> str:
    """A stored choice read back; anything unrecognised is `unset`, never `share`."""
    return value if isinstance(value, str) and value in STATES else UNSET
