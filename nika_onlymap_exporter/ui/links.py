"""Outward links, in one place.

Their own module rather than constants on the dialog because the preview writer
needs two of them and `main_dialog` already imports `preview` - reaching back
the other way would be an import cycle. Nothing here imports Qt or PyQGIS, which
is what lets a unit-tier test read these without a QGIS application.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

COMPANY_URL = "https://nikaplanet.com"
DOCS_URL = "https://docs.nikaplanet.com"
REPO_URL = "https://github.com/NikaGeospatial/qgis2webmap"

# The public server invite, never a `discord.com/channels/...` deep link: that
# form only resolves for someone already in the server, and everyone this is
# aimed at is by definition not. Name the channel in prose, link the invite.
DISCORD_URL = "https://discord.gg/RujwMpednf"

# A second invite, deliberately not the one above. Each surface gets its own so
# a join can be attributed to where it was clicked; reusing an invite makes
# every source look identical. This one belongs to the plugin's Help tab.
COMMUNITY_URL = "https://discord.gg/2CmbKkp5yg"

# Where "Host" goes until hosting exists. The button is a demand probe, not a
# feature: the form is the only thing behind it, and every label pointing here
# has to make that clear rather than promising an upload that cannot happen yet.
FEATURE_REQUEST_URL = "https://forms.gle/kHhZnHvGfCS3nDnH6"
