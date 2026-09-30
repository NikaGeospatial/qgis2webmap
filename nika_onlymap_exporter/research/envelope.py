"""The fields every report carries, and the environment they are read from.

`schema`, `kind`, `plugin_version`, `qgis_version`, `os`, and the profile. The
server adds `country` and `received_at` itself; nothing here names the machine,
the user or the install.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

SCHEMA_VERSION = 1

KIND_MAP = "map"
KIND_TALLY = "tally"
KIND_CONTACT = "contact"

OS_VALUES = ("windows", "macos", "linux", "other")

# `3.44.1-Solothurn` -> `3.44.1`. The release name adds nothing, and whatever
# a packager appends after the number is not ours to forward.
_VERSION_NUMBER = re.compile(r"^\d+(?:\.\d+){0,2}")


@dataclass(frozen=True)
class Environment:
    """Where the plugin is running, reduced to the three fields the spec allows."""

    plugin_version: str
    qgis_version: str
    os: str

    @classmethod
    def build(
        cls, plugin_version: str, qgis_version: str, platform: str
    ) -> Environment:
        return cls(
            plugin_version=version_number(plugin_version),
            qgis_version=version_number(qgis_version),
            os=os_name(platform),
        )


def version_number(text: str) -> str:
    match = _VERSION_NUMBER.match((text or "").strip())
    return match.group(0) if match else "unknown"


def os_name(platform: str | None = None) -> str:
    """`sys.platform` mapped onto the spec's four values."""
    value = sys.platform if platform is None else platform
    if value.startswith("win"):
        return "windows"
    if value == "darwin":
        return "macos"
    if value.startswith("linux"):
        return "linux"
    return "other"
