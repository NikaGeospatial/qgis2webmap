"""The prompt the "Hand off to NIKA" dialog puts on the clipboard.

**Pure, so the wording can be pinned.** The prompt is a contract as much as a
message: NIKA desktop's critique and authoring skills trigger on its opening
sentence (nika-agent#69), so a reworded line here is a cross-repo change. Keeping
it out of the dialog lets `tests/unit/test_handoff_prompt.py` hold every
target x task x output combination against golden files, with no QGIS and no
display.

**One dialog, two outputs.** Issue #3 section 4 specifies the templates for a
cartograph; a web map gets the parallel wording - its own skill name, and
critique and enhance items that belong to an interactive map rather than a
printed page.

**English on purpose.** The prompt is read by an agent, and the trigger sentence
must match byte for byte whatever language the dialog is shown in, so nothing
here goes through `tr()`.

**Optional facts are left out, never printed empty.** A page size, a bleed, an
atlas page count or colour-vision findings appear only when the caller knows
them. A prompt that says "print safety at None" sends the agent after a value
that does not exist.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Target = Literal["nika_desktop", "other_agent"]
Task = Literal["critique", "enhance"]
OutputKind = Literal["cartograph", "web_map"]

TARGETS: tuple[Target, ...] = ("nika_desktop", "other_agent")
TASKS: tuple[Task, ...] = ("critique", "enhance")
OUTPUTS: tuple[OutputKind, ...] = ("cartograph", "web_map")

# The product named in the opening sentence. The issue's template says
# "QGIS2Carto" because it was written for the standalone plugin; here the file
# is made by this one, and the prompt must name what actually made it.
PRODUCER = "QGIS2WebMap"

# Where a general-purpose agent learns the vocabulary before editing. The
# cartograph link is the issue's (onlymap's llms.txt "Cartographs" section); the
# web-map one is the skill file docs/enhance-with-ai.md already sends people to.
CARTOGRAPH_PRIMER = (
    "https://raw.githubusercontent.com/NikaGeospatial/onlymap/main/llms.txt"
)
WEB_MAP_PRIMER = (
    "https://raw.githubusercontent.com/NikaGeospatial/onlymap-js/main/"
    "skills/onlymapjs/SKILL.md"
)


@dataclass(frozen=True)
class PageInfo:
    """What is known about the printed page. Every field may be unknown.

    `size` and `bleed` are display strings ("A3 landscape", "3 mm") so the
    caller decides units once, where it knows them.
    """

    size: str | None = None
    bleed: str | None = None
    atlas_pages: int | None = None


@dataclass(frozen=True)
class HandoffRequest:
    """Everything one prompt is built from.

    `source_name` is the QGIS layout's name for a cartograph and the project's
    name for a web map. `files` are the paths to list under "Focus on these
    files", relative to the output's folder; empty means just the output file.
    """

    target: Target
    task: Task
    output: OutputKind
    file_path: str
    source_name: str
    plugin_version: str
    files: tuple[str, ...] = ()
    extra: str = ""
    cvd_findings: str | None = None
    page: PageInfo | None = None


def file_name(path: str) -> str:
    """The last component of `path`, whichever separator it was written with.

    Not `Path(path).name`: a Windows path read on Linux - or in a golden file
    generated on one platform and checked on another - would come back whole.
    """
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1]


def build_prompt(request: HandoffRequest) -> str:
    """The full prompt for `request`, ending in a newline."""
    paragraphs = [_opening(request), _task(request)]

    extra = request.extra.strip()
    if extra:
        paragraphs.append(f"Extra instructions from me:\n{extra}")

    files = request.files or (file_name(request.file_path),)
    listed = "\n".join(f"- `{name}`" for name in files)
    paragraphs.append(
        f"Focus on these files (the whole export folder is readable):\n{listed}"
    )
    return "\n\n".join(paragraphs) + "\n"


def _opening(request: HandoffRequest) -> str:
    if request.target == "nika_desktop":
        # The trigger sentence nika-agent#69 matches. Keep it verbatim.
        if request.output == "cartograph":
            return (
                f"Open the cartograph at {request.file_path} (exported by "
                f"{PRODUCER} {request.plugin_version} from the QGIS layout "
                f'"{request.source_name}"). Load the cartograph-authoring skill '
                "first."
            )
        return (
            f"Open the web map at {request.file_path} (exported by {PRODUCER} "
            f'{request.plugin_version} from the QGIS project "{request.source_name}"). '
            "Load the map-authoring skill first."
        )

    if request.output == "cartograph":
        return (
            f'Read {CARTOGRAPH_PRIMER} and its "Cartographs" section before '
            f"touching anything. The cartograph is {request.file_path}, a plain "
            "HTML document using @nika-js/onlymap/cartograph."
        )
    return (
        f"Read {WEB_MAP_PRIMER} before touching anything. The web map is "
        f"{request.file_path}, a plain HTML document using @nika-js/onlymap."
    )


def _task(request: HandoffRequest) -> str:
    if request.task == "critique":
        if request.output == "cartograph":
            lead = (
                "Render the cartograph to an image and critique it as a "
                "cartographer would: "
            )
            items = _cartograph_critique_items(request)
        else:
            lead = (
                "Open the web map in a browser and critique it as a cartographer "
                "would: "
            )
            items = _web_map_critique_items(request)
        return (
            lead
            + _join(items)
            + ".\nReport findings ranked by impact first. Apply fixes only after "
            "I confirm."
        )

    if request.output == "cartograph":
        return (
            "Improve the cartograph without changing its data or page size: fix "
            'anything the Fidelity tab marked "changed", tidy label collisions, and '
            "make the legend and title read well in print.\nEdit the HTML directly, "
            "keep every element id, and show me a before/after render."
        )
    return (
        "Improve the web map without changing its data: fix anything the Fidelity "
        'tab marked "changed", tidy label collisions across zoom levels, and make '
        "the legend, layer names and popups read well.\nEdit the HTML directly, "
        "keep every element id, and show me before/after screenshots."
    )


def _colour_vision(request: HandoffRequest) -> str:
    findings = (request.cvd_findings or "").strip()
    return f"colour-vision safety ({findings})" if findings else "colour-vision safety"


def _cartograph_critique_items(request: HandoffRequest) -> list[str]:
    page = request.page or PageInfo()
    size = (page.size or "").strip()
    bleed = (page.bleed or "").strip()
    print_safety = "print safety"
    if size:
        print_safety += f" at {size}"
    if bleed:
        print_safety += f" with {bleed} bleed"

    items = [
        "visual hierarchy and figure-ground",
        "label collisions",
        "legend completeness against the frames",
        _colour_vision(request),
        "scale bar and projection sanity",
        print_safety,
    ]
    # One page has nothing to overlap with; only an atlas of several does.
    if page.atlas_pages is not None and page.atlas_pages > 1:
        items.append(f"overlaps across the {page.atlas_pages} atlas pages")
    return items


def _web_map_critique_items(request: HandoffRequest) -> list[str]:
    return [
        "visual hierarchy and figure-ground at the opening view",
        "label collisions and legibility across zoom levels",
        "legend completeness against the layers",
        _colour_vision(request),
        "popup content and layer names",
        "load weight and use on a phone-sized screen",
    ]


def _join(items: list[str]) -> str:
    """`a, b, and c` - the serial comma the issue's template uses."""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + ", and " + items[-1]
