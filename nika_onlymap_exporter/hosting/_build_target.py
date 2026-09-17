"""Which deployment this BUILD was packaged for.

Empty in the repository, and rewritten by `scripts/package_plugin.py --api-base`
when it writes the zip. A checkout therefore behaves exactly like a production
build, and the only artifacts that point elsewhere are ones somebody
deliberately built to.

## Why a generated module rather than an environment variable

`NIKA_API_BASE` still works and still wins - see `_configured_api_base`. But it
has to be set by whoever launches QGIS, and the failure mode when it is
forgotten is the worst one available: the publish silently goes to PRODUCTION.
That is an acceptable risk for one developer who knows the trap, and not one to
hand to a team who were given a zip and told to install it.

Baking the target into the artifact makes the choice a property of the file
somebody installed rather than of how they happened to start the application.

## Why it is a whole module and not a string in `metadata.txt`

`metadata.txt` is read by QGIS, shown to users and parsed by the plugin
repository. A field there that changes where data is sent would be both
invisible in that context and easy to edit by hand into something unintended.
This is code, it is imported once, and `require_secure_url` still gates whatever
it names.
"""

from __future__ import annotations

# Empty means "no build-time target": fall through to the production default.
# A packaged dev build carries the dev API base here instead.
API_BASE = ""

# Shown in the publish confirmation so the person pressing the button can see
# which deployment is about to receive their map. Empty for production builds,
# where naming it would be noise.
LABEL = ""
