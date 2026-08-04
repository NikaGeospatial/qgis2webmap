"""The release gates that are only true if a browser says so.

From issue #29:

- The exported map opens successfully on a clean machine with no QGIS installed.
- Standalone HTML makes no unwanted network request.
- The two acquisition calls to action are keyboard accessible, do not cover map
  controls, and do not exfiltrate map data.

Run against each supported engine:

    pytest tests/browser --browser chromium --browser firefox --browser webkit

Chrome and Edge are both Chromium; `--browser chromium` covers the engine, and
the branded-channel run (`--browser-channel msedge`) is for the release matrix.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import pytest

WEBGL2_PROBE = """
() => {
  try {
    const canvas = document.createElement('canvas');
    return canvas.getContext('webgl2') !== null;
  } catch (error) {
    return false;
  }
}
"""


def open_map(page, exported_map):
    """Load the artifact from disk and wait for the runtime to define <om-map>."""
    page.goto(exported_map.as_uri())
    page.wait_for_function(
        "() => customElements.get('om-map') !== undefined", timeout=30_000
    )
    return page


def require_webgl(page):
    """Skip when the browser has no WebGL2 context to give.

    deck.gl needs one, so without it the map genuinely cannot draw -- but that
    is a fact about the machine, not about the export. Headless Firefox has no
    bundled software renderer the way Chromium has SwiftShader, so on a box with
    no GPU it reports `FEATURE_FAILURE_WEBGL_EXHAUSTED_DRIVERS`.

    Skipping keeps that honest in both directions: this tier stays silent about
    an environment it cannot judge, and still fails loudly on a machine that
    *can* render, which is what the release matrix runs on.
    """
    if not page.evaluate(WEBGL2_PROBE):
        pytest.skip("no WebGL2 context in this browser/environment")


class TestItOpens:
    def test_the_map_mounts(self, page, exported_map) -> None:
        """The first gate: a recipient double-clicks the file and sees a map."""
        open_map(page, exported_map)
        assert page.locator("om-map").count() == 1
        page.wait_for_selector("om-map canvas", timeout=30_000)

    def test_the_canvas_has_real_size(self, page, exported_map) -> None:
        """A mounted-but-zero-height canvas looks identical to a broken export."""
        open_map(page, exported_map)
        box = page.locator("om-map canvas").first.bounding_box()
        assert box is not None
        assert box["width"] > 100
        assert box["height"] > 100

    def test_no_console_errors(self, page, exported_map) -> None:
        errors: list[str] = []
        page.on(
            "console",
            lambda message: (
                errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_map(page, exported_map)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        assert not errors, f"console errors: {errors}"

    def test_the_title_reaches_the_document(self, page, exported_map) -> None:
        open_map(page, exported_map)
        assert page.title() == "Browser tier map"


class TestItStaysOffline:
    def test_it_makes_no_external_request(
        self, page_with_network_log, exported_map
    ) -> None:
        """The privacy promise, measured rather than asserted by design."""
        page = open_map(page_with_network_log, exported_map)
        page.wait_for_selector("om-map canvas", timeout=30_000)

        external = [
            url
            for url in page.requests_made
            if not url.startswith(("file://", "data:", "blob:"))
        ]
        assert not external, f"the exported map called out to: {external}"

    def test_it_works_with_javascript_disabled(
        self, browser, exported_map, browser_name
    ) -> None:
        """Mail previews and iOS QuickLook run HTML with scripts off.

        Without the fallback the recipient sees a blank frame and concludes the
        file is broken - the incumbent's worst sharing failure.
        """
        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        page.goto(exported_map.as_uri())

        text = page.locator("om-fallback").inner_text()
        assert "web browser" in text.lower()
        context.close()


class TestTheCreditComponent:
    def test_the_attribution_is_visible(self, page, exported_map) -> None:
        """`.first` because the data credit can name NIKA too, and two matches
        is a strict-mode error rather than a pass."""
        open_map(page, exported_map)
        assert page.get_by_role("link", name="OnlyMap").first.is_visible()
        assert page.get_by_role("link", name="NIKA").first.is_visible()

    def test_the_removed_calls_to_action_are_gone(self, page, exported_map) -> None:
        """ "Enhance" and "Host" pointed at pages that 404ed.

        Asserted from the rendered page rather than the markup: the point is
        that no recipient can click through to a dead end.
        """
        open_map(page, exported_map)
        assert page.get_by_role("link", name="Enhance").count() == 0
        assert page.get_by_role("link", name="Host").count() == 0

    def test_the_data_credit_is_visible(self, page, exported_map) -> None:
        """Attribution behind a click would not satisfy a licence obligation."""
        open_map(page, exported_map)
        assert page.locator(".om-credit-data").is_visible()
        assert "Fixture Survey" in page.locator(".om-credit-data").inner_text()

    def test_the_links_are_reachable_by_keyboard(self, page, exported_map) -> None:
        """A release gate, and the reason the component is anchors not divs."""
        open_map(page, exported_map)
        handles = page.locator(".om-credit a")
        # Two: OnlyMap and NIKA. It was three before "Enhance" and "Host" were
        # removed for pointing at 404s, and the data credit adds more when the
        # fixture carries attribution.
        assert handles.count() >= 2
        for index in range(handles.count()):
            link = handles.nth(index)
            link.focus()
            assert link.evaluate("el => el === document.activeElement")

    def test_it_does_not_cover_the_map_controls(self, page, exported_map) -> None:
        """The gate the below-the-map layout used to satisfy by construction.

        Now that the component overlays the map, it has to be checked: the
        manifest puts zoom controls and the scale bar in `bottom-start`, so a
        `bottom-end` chip must not reach them.
        """
        open_map(page, exported_map)
        page.wait_for_selector("om-map canvas", timeout=30_000)

        credit = page.locator(".om-credit").bounding_box()
        assert credit is not None

        for selector in (
            'om-widget[type="zoom-controls"]',
            'om-widget[type="scale-bar"]',
        ):
            widget = page.locator(selector)
            if widget.count() == 0:
                continue
            box = widget.first.bounding_box()
            if box is None:
                continue
            overlaps = (
                credit["x"] < box["x"] + box["width"]
                and box["x"] < credit["x"] + credit["width"]
                and credit["y"] < box["y"] + box["height"]
                and box["y"] < credit["y"] + credit["height"]
            )
            assert not overlaps, f"the credit component covers {selector}"

    def test_it_contains_no_script_or_form(self, page, exported_map) -> None:
        """No exfiltration: the component is links and text, nothing else."""
        open_map(page, exported_map)
        markup = page.locator(".om-credit").inner_html()
        for forbidden in ("<script", "<form", "<input", "onerror=", "onclick="):
            assert forbidden not in markup.lower()


class TestSmallScreens:
    def test_it_collapses_to_the_mark(self, page, exported_map) -> None:
        page.set_viewport_size({"width": 480, "height": 800})
        open_map(page, exported_map)
        assert page.locator(".om-credit-mark").is_visible()

    def test_focus_expands_it(self, page, exported_map) -> None:
        """Expanding on keyboard focus is what keeps the collapsed state usable."""
        page.set_viewport_size({"width": 480, "height": 800})
        open_map(page, exported_map)

        page.locator(".om-credit-mark").focus()
        assert page.locator(".om-credit-row").is_visible()


class TestPopupFieldModes:
    """The half of the popup-mode feature only a browser can settle.

    Whether a "with data" row disappears depends on the value of the feature the
    recipient clicked, and the popup is one template shared by every feature -
    so the exported file cannot decide it. The runtime interpolates `{{name}}`
    to an empty string and the shipped stylesheet does the rest, which makes
    this tier the only place the behaviour exists to be tested.

    The selection is injected rather than clicked: hitting a 6px deck.gl point
    needs its projected pixel, and `injectPickInternal` is the entry point the
    runtime's own tests use, landing on the same path a real pick takes -
    interpolation, overlay flush and all. It is an internal name, so an upstream
    rename breaks this test loudly, which is the intended trade.
    """

    # Field order in the `popup_modes_map` fixture, restated because `tests/` is
    # not a package and conftest cannot be imported. A hidden row is still in
    # the DOM, so indexing stays stable whatever the styles do.
    ORDER = (
        "inline_with_data",
        "header_with_data",
        "inline_always",
        "header_always",
        "no_label",
    )

    # Feature 0 has a value for every field; feature 1 has none.
    WITH_DATA = 0
    WITHOUT_DATA = 1

    def _open_popup(self, page, popup_modes_map, feature: int) -> dict[str, dict]:
        """Show the popup for one of the fixture's two features.

        The picked object is read back out of the mounted layer rather than
        rebuilt here, so the popup is interpolated from the data the artifact
        actually shipped - the same object a real click would hand it.
        """
        open_map(page, popup_modes_map)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)

        page.evaluate(
            """(index) => {
                const map = document.querySelector('om-map');
                const layer = map.getLayers()[0];
                map.injectPickInternal({
                    layerId: layer.id,
                    object: layer.data[index],
                    index,
                    coordinate: [0.87, 51.15],
                    pixel: [100, 100],
                    type: 'click',
                });
            }""",
            feature,
        )
        # Wait for the interpolation, not merely for the rows: the overlay
        # renders the raw template first and substitutes values on the next
        # frame, and a row still holding `{{name}}` is not empty - measuring
        # there would report every row as visible whatever the styles say.
        page.wait_for_function(
            """() => {
                const root = document.querySelector('om-overlay')?.shadowRoot;
                const rows = root?.querySelectorAll('.om-popup-row');
                if (!rows || rows.length === 0) return false;
                return Array.from(rows).every(
                    (row) =>
                        !row.querySelector('.om-popup-value').textContent.includes('{{')
                );
            }""",
            timeout=10_000,
        )

        # Geometry, not computed `display`, decides "above" versus "beside": a
        # span inside a flex row is blockified to `block` too, so the property
        # reads identically in both layouts and would prove nothing.
        measured = page.evaluate(
            """() => {
                const root = document.querySelector('om-overlay').shadowRoot;
                return Array.from(root.querySelectorAll('.om-popup-row')).map((row) => {
                    const label = row.querySelector('.om-popup-label');
                    const value = row.querySelector('.om-popup-value');
                    return {
                        display: getComputedStyle(row).display,
                        hasLabel: label !== null,
                        labelBox: label ? label.getBoundingClientRect().toJSON() : null,
                        valueBox: value.getBoundingClientRect().toJSON(),
                    };
                });
            }"""
        )
        assert len(measured) == len(self.ORDER)
        return dict(zip(self.ORDER, measured))

    def test_the_stylesheet_reaches_the_shadow_root_at_all(
        self, page, popup_modes_map
    ) -> None:
        """The rule this whole feature rests on. A div defaults to `block`, so
        seeing `flex` proves the popup CSS travelled inside the overlay."""
        rows = self._open_popup(page, popup_modes_map, self.WITH_DATA)
        assert rows["inline_always"]["display"] == "flex"

    def test_with_data_rows_vanish_when_the_value_is_empty(
        self, page, popup_modes_map
    ) -> None:
        rows = self._open_popup(page, popup_modes_map, self.WITHOUT_DATA)
        assert rows["inline_with_data"]["display"] == "none"
        assert rows["header_with_data"]["display"] == "none"

    def test_always_rows_survive_an_empty_value(self, page, popup_modes_map) -> None:
        """The whole point of the distinction: these stay put."""
        rows = self._open_popup(page, popup_modes_map, self.WITHOUT_DATA)
        for name in ("inline_always", "header_always", "no_label"):
            assert rows[name]["display"] != "none"

    def test_every_row_shows_when_the_feature_has_data(
        self, page, popup_modes_map
    ) -> None:
        rows = self._open_popup(page, popup_modes_map, self.WITH_DATA)
        hidden = [name for name, row in rows.items() if row["display"] == "none"]
        assert hidden == []

    def test_header_fields_stack_their_label_above_the_value(
        self, page, popup_modes_map
    ) -> None:
        rows = self._open_popup(page, popup_modes_map, self.WITH_DATA)

        for name in ("header_always", "header_with_data"):
            row = rows[name]
            assert row["labelBox"]["bottom"] <= row["valueBox"]["top"], (
                f"{name}: the label should sit above its value"
            )

        # The inline modes keep the label beside the value, on one line.
        inline = rows["inline_always"]
        assert inline["labelBox"]["right"] <= inline["valueBox"]["left"]
        assert inline["labelBox"]["top"] == inline["valueBox"]["top"]

        assert rows["no_label"]["hasLabel"] is False


class TestExtrusion:
    """A raised map has to be raised in the renderer, not only in the markup.

    deck.gl ignores props it does not recognise without complaining, so an
    attribute that reached the file but not the layer looks identical on disk
    and draws flat.
    """

    def test_it_opens_without_console_errors(self, page, extruded_map) -> None:
        errors: list[str] = []
        page.on(
            "console",
            lambda message: (
                errors.append(message.text) if message.type == "error" else None
            ),
        )
        open_map(page, extruded_map)
        require_webgl(page)
        page.wait_for_timeout(1500)
        assert errors == []

    def test_the_layer_is_extruded_and_tilted(self, page, extruded_map) -> None:
        open_map(page, extruded_map)
        require_webgl(page)
        assert (
            page.evaluate(
                "() => document.querySelector('om-layer').getAttribute('extruded')"
            )
            == "true"
        )
        # Without the tilt the extrusion is invisible, which is the whole point.
        assert (
            float(
                page.evaluate(
                    "() => document.querySelector('om-map').getAttribute('pitch')"
                )
            )
            > 0
        )
