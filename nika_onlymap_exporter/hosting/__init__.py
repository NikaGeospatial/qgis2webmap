"""Talking to NIKA's hosting API. See docs/hosting.md for the promised UX.

Deliberately free of Qt and PyQGIS so the whole handshake is testable at the
unit tier against a fake transport, which is the only way a network feature gets
covered by a suite that is never allowed to reach the network.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""
