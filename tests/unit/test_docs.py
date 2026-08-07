"""Documentation integrity.

Docs rot quietly. These checks are cheap and catch the two ways it happens: a
link that no longer resolves, and a guide that stops shipping with the plugin so
the Help tab and the website drift apart.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import importlib.util
import re
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"


# Imported, never copied. This file exists to catch the packager and the Help tab
# drifting apart, so a third hand-maintained copy of the list here would be the
# very bug under test - as it was: adding a guide to both real lists left this
# stale duplicate failing, which is the wrong end to find out from.
def _load_packager():
    """Import `scripts/package_plugin.py`, which is a script, not a package."""
    spec = importlib.util.spec_from_file_location(
        "_package_plugin", REPO_ROOT / "scripts" / "package_plugin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELP_DOCS = _load_packager().HELP_DOCS

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


# `strip_images` lives in `ui/main_dialog.py`, which imports PyQGIS - unavailable
# on this tier. Its source is executed in isolation rather than reimplemented
# here: a second copy of the regexes would pass while the real one was broken,
# which is precisely the drift the tests above exist to catch.
def _load_strip_images():
    source = (REPO_ROOT / "nika_onlymap_exporter" / "ui" / "main_dialog.py").read_text(
        encoding="utf-8"
    )
    start = source.index("_IMAGE_LINE = re.compile")
    end = source.index("def load_help_markdown")
    namespace: dict = {"re": re}
    exec(compile(source[start:end], "main_dialog:strip_images", "exec"), namespace)
    return namespace["strip_images"]


def markdown_files() -> list[Path]:
    return sorted(DOCS.glob("*.md"))


class TestDocsExist:
    def test_every_bundled_guide_is_present(self) -> None:
        for name in HELP_DOCS:
            assert (DOCS / name).is_file(), f"docs/{name} is missing"

    def test_pages_config_exists(self) -> None:
        """Without it GitHub Pages serves raw Markdown."""
        assert (DOCS / "_config.yml").is_file()


class TestLinks:
    def test_relative_links_resolve(self) -> None:
        """A link to a moved or renamed guide is a 404 for a real user."""
        broken: list[str] = []
        for path in markdown_files():
            for target in MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                resolved = (path.parent / target.split("#")[0]).resolve()
                if not resolved.exists():
                    broken.append(f"{path.name} -> {target}")
        assert not broken, f"broken relative links: {broken}"

    def test_no_placeholder_urls(self) -> None:
        placeholders = ("example.com", "TODO", "FIXME", "XXX")
        offenders = [
            f"{path.name}: {placeholder}"
            for path in markdown_files()
            for placeholder in placeholders
            if placeholder in path.read_text(encoding="utf-8")
        ]
        assert not offenders, offenders


class TestHelpTabImages:
    """The guides carry screenshots for the website only.

    Two readers, one source. The site shows the pictures; the Help tab strips
    them, because showing them there would need `setBaseUrl` on the document
    *and* every PNG inside the zip - about 2.1 MB instead of 223 KB, for a
    reader already looking at the dialog the screenshots depict.
    """

    def test_the_guides_do_carry_images(self) -> None:
        """Guard the other side: a strip that strips nothing proves nothing."""
        installation = (DOCS / "installation.md").read_text(encoding="utf-8")
        assert "![" in installation

    def test_no_image_survives_into_the_help_text(self) -> None:
        strip_images = _load_strip_images()
        for guide in sorted(DOCS.glob("*.md")):
            stripped = strip_images(guide.read_text(encoding="utf-8"))
            assert "![" not in stripped, f"{guide.name} leaks an image into Help"

    def test_a_whole_line_image_leaves_no_blank_gap(self) -> None:
        strip_images = _load_strip_images()
        assert strip_images("before\n\n![alt](a.png)\n\nafter") == ("before\n\nafter")

    def test_an_inline_image_is_removed_in_place(self) -> None:
        strip_images = _load_strip_images()
        assert strip_images("see ![alt](a.png) here") == "see  here"


class TestPrivacyClaim:
    """The privacy promise is made in several places; they must agree."""

    def test_readme_and_privacy_guide_both_state_it(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        privacy = (DOCS / "privacy.md").read_text(encoding="utf-8")
        for text in (readme, privacy):
            assert "one anonymous usage report" in text
            assert "and nothing" in text

    def test_the_claim_is_verifiable_by_the_reader(self) -> None:
        """A promise a user cannot check is just marketing."""
        privacy = (DOCS / "privacy.md").read_text(encoding="utf-8")
        assert "Network tab" in privacy


class TestPackagedHelp:
    """The Help tab reads these files; a build that drops them shows nothing."""

    ZIP_GLOB = "qgis2webmap-*.zip"

    def _zip(self) -> Path | None:
        candidates = sorted((REPO_ROOT / "dist").glob(self.ZIP_GLOB))
        return candidates[-1] if candidates else None

    def test_guides_are_inside_the_built_zip(self) -> None:
        archive = self._zip()
        if archive is None:
            pytest.skip("no built zip; run scripts/package_plugin.py first")
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
        for name in HELP_DOCS:
            expected = f"nika_onlymap_exporter/help/{name}"
            assert expected in names, f"{expected} missing from the plugin zip"

    def test_help_loader_lists_only_files_that_ship(self) -> None:
        """The dialog's page list and the packager's list must not diverge."""
        source = (
            REPO_ROOT / "nika_onlymap_exporter" / "ui" / "main_dialog.py"
        ).read_text(encoding="utf-8")
        referenced = set(re.findall(r'"([a-z-]+\.md)"', source))
        assert referenced, "the Help tab references no guides"
        assert referenced <= set(HELP_DOCS), (
            f"Help tab references guides that are not packaged: "
            f"{referenced - set(HELP_DOCS)}"
        )


class TestRuntimeDownloadSize:
    """The download size is a promise about someone's bandwidth.

    It is shown in the licence dialog at the moment a user decides whether to
    accept, so it has to be true. It was wrong for a whole release: the
    0.3.3 -> 0.5.11 bump left four hand-written copies saying "about 3 MB" for a
    download that had become 4.5 MB. One constant now, and this test stops the
    guide drifting away from it.
    """

    @staticmethod
    def _declared_size() -> str:
        from nika_onlymap_exporter.packaging.runtime_manager import (
            RUNTIME_DOWNLOAD_SIZE,
        )

        return RUNTIME_DOWNLOAD_SIZE

    def test_the_installation_guide_quotes_the_same_size(self) -> None:
        text = (DOCS / "installation.md").read_text(encoding="utf-8")
        assert self._declared_size() in text, (
            f"docs/installation.md does not mention {self._declared_size()!r}; "
            "update it or the constant so the two agree"
        )

    def test_no_guide_states_a_different_size(self) -> None:
        """Catches a stale figure left behind in another guide."""
        pattern = re.compile(r"runtime[^.]{0,80}?about (\d+(?:\.\d+)?) ?MB", re.I)
        declared = self._declared_size()
        wrong: list[str] = []
        for path in markdown_files():
            for match in pattern.finditer(path.read_text(encoding="utf-8")):
                if match.group(0).split("about ")[-1].rstrip() not in declared:
                    wrong.append(f"{path.name}: {match.group(0)!r}")
        assert not wrong, f"stale runtime download sizes: {wrong}"
