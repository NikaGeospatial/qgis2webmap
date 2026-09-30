"""Opt-in user research: who uses the plugin, and for what.

Specified in `specs/2026-09-30-user-research.md`, whose wire format is a
contract shared with the server. Everything in this package is pure Python so
the rules that decide what may leave the machine can be read and tested without
a QGIS application; the dialogs and the network live in `ui/`.

**Nothing is collected, queued or sent unless the user chose Share.** That gate
is `consent.may_collect`, and every path that produces a report goes through it.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""
