"""The hand-off prompt, pinned against golden files.

The opening sentence is a contract with NIKA desktop (nika-agent#69): its skills
trigger on that wording, so an accidental edit must fail here rather than
silently stop the hand-off working. Every target x task x output combination
has a golden file under `golden/handoff/`, plus cases for the optional facts
(colour-vision findings, page size, bleed, atlas pages, extra instructions).

To regenerate the golden files after a deliberate wording change:

    UPDATE_HANDOFF_GOLDENS=1 pytest tests/unit/test_handoff_prompt.py

then read the diff before committing it - that diff is the wording change.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from nika_onlymap_exporter.core.handoff_prompt import (
    OUTPUTS,
    TARGETS,
    TASKS,
    HandoffRequest,
    PageInfo,
    build_prompt,
    file_name,
)

GOLDEN_DIR = Path(__file__).parent / "golden" / "handoff"
UPDATE = os.environ.get("UPDATE_HANDOFF_GOLDENS") == "1"


def _base(target: str, task: str, output: str) -> HandoffRequest:
    if output == "cartograph":
        return HandoffRequest(
            target=target,  # type: ignore[arg-type]
            task=task,  # type: ignore[arg-type]
            output="cartograph",
            file_path="/home/ana/maps/parcel-survey.html",
            source_name="Parcel survey",
            plugin_version="0.3.0",
        )
    return HandoffRequest(
        target=target,  # type: ignore[arg-type]
        task=task,  # type: ignore[arg-type]
        output="web_map",
        file_path="/home/ana/maps/airports.html",
        source_name="alaska",
        plugin_version="0.3.0",
    )


COMBOS = [(o, t, k) for o in OUTPUTS for t in TARGETS for k in TASKS]

OPTIONAL_CASES: dict[str, HandoffRequest] = {
    "cartograph-critique-all-optional": replace(
        _base("nika_desktop", "critique", "cartograph"),
        files=("parcel-survey.html", "data/parcels.geojson"),
        extra="Keep the client's logo.\nThe title must stay in French.  ",
        cvd_findings="2 of 9 colour pairs fail under deuteranopia",
        page=PageInfo(size="A3 landscape", bleed="3 mm", atlas_pages=12),
    ),
    "cartograph-critique-size-only": replace(
        _base("other_agent", "critique", "cartograph"),
        page=PageInfo(size="A4 portrait"),
    ),
    "cartograph-critique-bleed-only": replace(
        _base("nika_desktop", "critique", "cartograph"),
        page=PageInfo(bleed="5 mm", atlas_pages=1),
    ),
    "web-map-critique-cvd-and-extra": replace(
        _base("nika_desktop", "critique", "web_map"),
        cvd_findings="all colour pairs pass",
        extra="Most readers are on phones.",
        page=PageInfo(size="A3", bleed="3 mm", atlas_pages=4),
    ),
    "windows-path": replace(
        _base("nika_desktop", "enhance", "web_map"),
        file_path="C:\\Users\\Ana\\Documents\\airports.html",
    ),
}


def _check_golden(name: str, prompt: str) -> None:
    path = GOLDEN_DIR / f"{name}.md"
    if UPDATE:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8", newline="\n")
    assert path.exists(), (
        f"missing golden {path.name}; run with UPDATE_HANDOFF_GOLDENS=1"
    )
    assert prompt == path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("output", "target", "task"), COMBOS)
def test_every_combination_matches_its_golden(
    output: str, target: str, task: str
) -> None:
    prompt = build_prompt(_base(target, task, output))
    _check_golden(
        f"{output.replace('_', '-')}-{target.replace('_', '-')}-{task}", prompt
    )


@pytest.mark.parametrize("name", sorted(OPTIONAL_CASES))
def test_optional_fields_match_their_golden(name: str) -> None:
    _check_golden(name, build_prompt(OPTIONAL_CASES[name]))


def test_there_is_a_golden_for_all_eight_combinations() -> None:
    assert len(COMBOS) == 8
    assert len(set(COMBOS)) == 8


class TestTheNikaDesktopContract:
    """The sentences nika-agent#69 triggers on, asserted outside the goldens so
    a regenerated golden cannot quietly absorb a change to them."""

    def test_cartograph_opening_loads_the_authoring_skill(self) -> None:
        for task in TASKS:
            prompt = build_prompt(_base("nika_desktop", task, "cartograph"))
            first = prompt.splitlines()[0]
            assert first.startswith(
                "Open the cartograph at /home/ana/maps/parcel-survey.html (exported by "
            )
            assert first.endswith(
                'from the QGIS layout "Parcel survey"). '
                "Load the cartograph-authoring skill first."
            )

    def test_web_map_opening_loads_the_map_authoring_skill(self) -> None:
        prompt = build_prompt(_base("nika_desktop", "enhance", "web_map"))
        assert prompt.splitlines()[0].endswith(
            'from the QGIS project "alaska"). Load the map-authoring skill first.'
        )

    def test_other_agent_never_names_a_nika_skill(self) -> None:
        for output in OUTPUTS:
            for task in TASKS:
                prompt = build_prompt(_base("other_agent", task, output))
                assert "skill first" not in prompt


class TestOptionalFacts:
    def test_nothing_unknown_is_ever_printed(self) -> None:
        for output, target, task in COMBOS:
            prompt = build_prompt(
                replace(
                    _base(target, task, output),
                    cvd_findings="   ",
                    page=PageInfo(size="", bleed=None, atlas_pages=None),
                )
            )
            for word in ("None", "()", "at  ", " with  bleed", "{", "}"):
                assert word not in prompt, (output, target, task, word)

    def test_colour_vision_clause_without_findings(self) -> None:
        prompt = build_prompt(_base("nika_desktop", "critique", "cartograph"))
        assert "colour-vision safety, scale bar" in prompt

    def test_a_single_page_has_no_atlas_overlap_clause(self) -> None:
        request = replace(
            _base("nika_desktop", "critique", "cartograph"),
            page=PageInfo(atlas_pages=1),
        )
        assert "atlas" not in build_prompt(request)

    def test_page_facts_never_reach_a_web_map(self) -> None:
        prompt = build_prompt(OPTIONAL_CASES["web-map-critique-cvd-and-extra"])
        assert "bleed" not in prompt
        assert "atlas" not in prompt

    def test_extra_instructions_heading_only_when_given(self) -> None:
        plain = build_prompt(_base("nika_desktop", "enhance", "cartograph"))
        assert "Extra instructions" not in plain
        blank = build_prompt(
            replace(_base("nika_desktop", "enhance", "cartograph"), extra=" \n ")
        )
        assert blank == plain


class TestFiles:
    def test_defaults_to_the_output_file_alone(self) -> None:
        prompt = build_prompt(_base("nika_desktop", "enhance", "cartograph"))
        assert prompt.endswith(
            "Focus on these files (the whole export folder is readable):\n"
            "- `parcel-survey.html`\n"
        )

    def test_no_frames_folder_is_listed(self) -> None:
        # Static frames are inlined in the HTML in this plugin; listing a
        # `.frames/` folder would send the agent looking for one that is not there.
        for output, target, task in COMBOS:
            assert ".frames" not in build_prompt(_base(target, task, output))

    @pytest.mark.parametrize(
        ("path", "name"),
        [
            ("/a/b/map.html", "map.html"),
            ("C:\\Users\\Ana\\map.html", "map.html"),
            ("map.html", "map.html"),
            ("/a/b/folder/", "folder"),
        ],
    )
    def test_file_name_handles_both_separators(self, path: str, name: str) -> None:
        assert file_name(path) == name
