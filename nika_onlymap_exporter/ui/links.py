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

# The hosting feedback form. It used to be the whole of the "Host" button - a
# demand probe with nothing behind it - and the dialog's button now runs the
# real publish flow instead. What still points here is the preview's own chip,
# which is plugin UI shown to the author and can only ever open a link: a page
# in a browser cannot reach back into the dialog to start a publish.
#
# It must never appear in an exported artifact; `tests/fixtures` asserts that
# on every fixture project.
FEATURE_REQUEST_URL = "https://forms.gle/kHhZnHvGfCS3nDnH6"
