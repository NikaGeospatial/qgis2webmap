"""Every layout template, in the order the picker lists them.

The ten designs of `core/layout_templates.py`, then the five of
`core/template_designs.py`. Both modules only define templates; this one puts
them together, so neither has to change the other as it loads.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from .layout_templates import BASE_TEMPLATES, LayoutTemplate
from .template_designs import NEW_TEMPLATES

TEMPLATES: tuple[LayoutTemplate, ...] = BASE_TEMPLATES + NEW_TEMPLATES


def template_ids() -> tuple[str, ...]:
    return tuple(template.id for template in TEMPLATES)


def starter_templates() -> tuple[LayoutTemplate, ...]:
    """One starter per useful composition, without cosmetic near-duplicates.

    The complete registry is also used by the template geometry and fidelity
    probes; the picker is deliberately a smaller, task-oriented collection.
    """
    omitted = {"story-map", "marginalia", "atlas-ledger", "town-hall"}
    return tuple(template for template in TEMPLATES if template.id not in omitted)


def get_template(template_id: str) -> LayoutTemplate:
    for template in TEMPLATES:
        if template.id == template_id:
            return template
    raise ValueError(f"template {template_id!r} is not one of {template_ids()}")


__all__ = ["TEMPLATES", "get_template", "starter_templates", "template_ids"]
