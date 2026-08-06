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
            # `getRootNode()`, not `document`. The component moved into an
            # `om-widget`, so it lives in a shadow root, and
            # `document.activeElement` reports the *host* element for anything
            # focused inside one - the link would look unreachable while being
            # perfectly focused. Each root tracks its own `activeElement`.
            assert link.evaluate("el => el.getRootNode().activeElement === el")

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

    def test_it_does_not_cover_the_runtime_attribution(
        self, page, exported_map
    ) -> None:
        """The regression that put our chip under the runtime's own control.

        Runtime 0.6.0 mounts mandated chrome of its own - the provider
        attribution into the `bottom-end` slot, the licence badge into
        `bottom-start` - and that slot container is anchored at exactly the
        `bottom: 12px; inset-inline-end: 12px` our chip used to claim by
        absolute positioning. They drew on top of each other. The chip is an
        `om-widget` in the same slot now, so the runtime lays both out.

        Asserted on the mandated host rather than on a pixel offset: the fix is
        "the runtime owns this corner", and a coordinate assertion would pass
        again the moment someone reintroduced a hand-tuned inset.
        """
        open_map(page, exported_map)
        page.wait_for_selector("om-map canvas", timeout=30_000)

        chrome = page.locator('[data-om-mandated-chrome="attribution"]')
        assert chrome.count() > 0, "0.6.0 always mounts the attribution host"

        # The host is only *filled* when there is a provider to credit, and this
        # fixture has no basemap - so an overlap check against its live box
        # silently passes by measuring nothing. Assert against the band the slot
        # occupies instead: the container is anchored `bottom: 12px` and the
        # control measured 24px tall in 0.6.0, so anything below 36px from the
        # map's bottom edge is inside the runtime's territory.
        reserved = 36
        map_box = page.locator("om-map").bounding_box()
        credit = page.locator(".om-credit").bounding_box()
        assert map_box is not None and credit is not None

        credit_bottom_gap = (map_box["y"] + map_box["height"]) - (
            credit["y"] + credit["height"]
        )
        assert credit_bottom_gap >= reserved, (
            "the credit chip reaches into the bottom-end slot the runtime mounts "
            f"its attribution into ({credit_bottom_gap:.0f}px clear, needs {reserved})"
        )

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


class TestHighlightColour:
    """The setting that looked configurable and was inert.

    Every layer of this is invisible on disk. The attribute was in the file, the
    unit tier asserted it was in the file, and it still did nothing: quoted as an
    expression-language literal, the runtime handed deck.gl the raw string
    `'#1de9c880'`, deck's `Array.isArray(highlightColor)` guard rejected it, and
    the highlight rendered in deck's default navy `[0, 0, 128, 128]`. Only the
    resolved layer prop tells the two apart, so only a browser can.
    """

    @staticmethod
    def _prop(page):
        return page.evaluate(
            """() => {
              const core = document.querySelector('om-map').core;
              const deck = core.deck || core._deck;
              const layer = deck.props.layers.find((l) => l.id === 'stations');
              return layer ? layer.props.highlightColor : null;
            }"""
        )

    def test_the_colour_reaches_deck_as_rgba(self, page, highlighted_map) -> None:
        open_map(page, highlighted_map)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        page.wait_for_timeout(1500)

        assert self._prop(page) == [29, 233, 200, 128]

    def test_it_is_not_left_as_an_unresolved_string(
        self, page, highlighted_map
    ) -> None:
        """The regression guard, stated as the failure rather than the fix.

        A future change that reintroduces quoting passes the previous test's
        `==` never - but says nothing about *why*. This one names the shape.
        """
        open_map(page, highlighted_map)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        page.wait_for_timeout(1500)

        value = self._prop(page)
        assert not isinstance(value, str), f"deck.gl will ignore {value!r}"
        assert value != [0, 0, 128, 128], "this is deck's default, not ours"


class TestIconMarkers:
    """Markers QGIS rasterised, drawn by a real deck.gl.

    Everything here fails silently on disk. `point-type` reaching the file but
    not the layer, a sprite sheet the browser will not decode, an icon name the
    mapping does not contain - each produces a map with no markers at all and no
    error, which looks exactly like a map that has not finished loading.
    """

    def test_it_opens_without_console_errors(self, page, icon_map) -> None:
        errors: list[str] = []
        page.on(
            "console",
            lambda message: (
                errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_map(page, icon_map)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        page.wait_for_timeout(1500)
        assert errors == [], f"console errors: {errors}"

    def test_the_runtime_accepts_every_icon_attribute(self, page, icon_map) -> None:
        """The runtime warns by name for an attribute it does not recognise, so
        this catches a prop we invented or one deck.gl has since renamed -
        which is otherwise invisible until someone looks at the map."""
        warnings: list[str] = []
        page.on(
            "console",
            lambda message: (
                warnings.append(message.text) if message.type == "warning" else None
            ),
        )
        open_map(page, icon_map)
        require_webgl(page)
        page.wait_for_timeout(1500)
        unknown = [w for w in warnings if "Unknown attribute" in w]
        assert unknown == [], f"the runtime rejected an attribute: {unknown}"

    def test_the_point_sublayer_is_icons(self, page, icon_map) -> None:
        open_map(page, icon_map)
        assert (
            page.evaluate(
                "() => document.querySelector('om-layer').getAttribute('point-type')"
            )
            == "icon"
        )

    def test_the_sheet_is_a_decodable_image(self, page, icon_map) -> None:
        """A sprite sheet the browser cannot decode leaves every marker
        undrawn. Decoding it here is the only way to know the bytes are good."""
        open_map(page, icon_map)
        decoded = page.evaluate(
            """
            async () => {
              const src = document.querySelector('om-layer')
                .getAttribute('icon-atlas');
              const image = new Image();
              image.src = src;
              try {
                await image.decode();
              } catch (error) {
                return null;
              }
              return [image.naturalWidth, image.naturalHeight];
            }
            """
        )
        assert decoded is not None, "the browser could not decode the sprite sheet"
        assert decoded[0] > 0 and decoded[1] > 0

    def test_the_named_cell_is_inside_the_sheet(self, page, icon_map) -> None:
        """deck.gl draws nothing for a cell that runs off the edge, and says
        nothing about it."""
        open_map(page, icon_map)
        result = page.evaluate(
            """
            async () => {
              const layer = document.querySelector('om-layer');
              const mapping = JSON.parse(layer.getAttribute('icon-mapping'));
              const name = layer.getAttribute('get-icon').replace(/'/g, '');
              const image = new Image();
              image.src = layer.getAttribute('icon-atlas');
              await image.decode();
              const cell = mapping[name];
              if (!cell) return 'the named cell is missing from the mapping';
              if (cell.x + cell.width > image.naturalWidth) return 'cell overruns';
              if (cell.y + cell.height > image.naturalHeight) return 'cell overruns';
              return 'ok';
            }
            """
        )
        assert result == "ok"

    def test_the_map_still_draws(self, page, icon_map) -> None:
        open_map(page, icon_map)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        box = page.locator("om-map canvas").first.bounding_box()
        assert box is not None and box["width"] > 100

    def test_the_legend_shows_the_markers_own_picture(self, page, icon_map) -> None:
        """The custom legend's whole reason to exist: its swatch is cut from the
        same rendering the map draws, so the two cannot disagree. A swatch that
        failed to load reports a natural size of zero."""
        open_map(page, icon_map)
        # Search every widget rather than the first: the runtime's layout
        # manager reparents widgets into slot containers, so document order is
        # its business, not ours. Pinning to `querySelector('om-widget')` made
        # this test fail on a runtime bump for a legend that rendered perfectly.
        size = page.evaluate(
            """
            async () => {
              for (const widget of document.querySelectorAll('om-widget')) {
                const img = widget.shadowRoot
                  && widget.shadowRoot.querySelector('img.omni-swatch');
                if (!img) continue;
                await img.decode();
                return [img.naturalWidth, img.naturalHeight];
              }
              return null;
            }
            """
        )
        assert size is not None, "the legend has no image swatch"
        assert size[0] > 0 and size[1] > 0

    def test_the_legend_needs_no_script(self, page, icon_map) -> None:
        """A static widget, so the export stays markup and the Content Security
        Policy needs nothing beyond `data:` images for it."""
        open_map(page, icon_map)
        assert (
            page.evaluate("() => document.querySelectorAll('om-widget script').length")
            == 0
        )


class TestPopupsDoNotStack:
    """Reported from a real project: three overlapping layers, three popups.

    `show-overlay` sets `visible="true"` and nothing ever sets it back, and the
    runtime dispatches behaviours only when there is a pick - there is no
    unhover event. So a popup opened once stayed open forever, and hovering a
    spot covered by several layers left their popups piled on one coordinate.
    """

    @staticmethod
    def _visible(page) -> list[str]:
        return page.evaluate(
            """() => [...document.querySelectorAll('om-overlay')]
                 .filter(o => getComputedStyle(o).visibility !== 'hidden'
                           && o.getBoundingClientRect().width > 0)
                 .map(o => o.id)"""
        )

    def test_at_most_one_popup_is_ever_open(self, page, stacked_popups_map) -> None:
        open_map(page, stacked_popups_map)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        page.wait_for_timeout(1500)

        box = page.locator("om-map canvas").first.bounding_box()
        worst = 0
        # Sweep the middle of the canvas, where the shared coordinate sits.
        for gx in range(10, 23):
            for gy in range(6, 17):
                page.mouse.move(
                    box["x"] + box["width"] * gx / 32,
                    box["y"] + box["height"] * gy / 22,
                )
                page.wait_for_timeout(60)
                worst = max(worst, len(self._visible(page)))
        assert worst <= 1, f"{worst} popups were open at once"

    def test_hovering_empty_space_closes_the_popup(
        self, page, stacked_popups_map
    ) -> None:
        """The reason the overlays stay unscoped: an unscoped overlay follows a
        null selection to nowhere and hides itself. Scoping them would fix the
        stacking and leave the last popup stranded on screen instead."""
        open_map(page, stacked_popups_map)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        page.wait_for_timeout(1500)

        box = page.locator("om-map canvas").first.bounding_box()
        opened = False
        for gx in range(10, 23):
            for gy in range(6, 17):
                page.mouse.move(
                    box["x"] + box["width"] * gx / 32,
                    box["y"] + box["height"] * gy / 22,
                )
                page.wait_for_timeout(60)
                if self._visible(page):
                    opened = True
                    break
            if opened:
                break
        assert opened, "no popup ever opened, so the close cannot be tested"

        page.mouse.move(box["x"] + 5, box["y"] + box["height"] - 5)
        page.wait_for_timeout(600)
        assert self._visible(page) == []


# Counts pixels of the layer's colour in the map's own snapshot. `snapshot()`
# is used rather than a page screenshot because a deck.gl canvas is not
# guaranteed to preserve its drawing buffer, so reading it directly can return
# an empty image on a perfectly working map. Decoding happens in the browser -
# it already has a PNG decoder, and it saves this tier a Python imaging
# dependency it does not otherwise need.
COUNT_RED_PIXELS = """
async () => {
  const el = document.querySelector('om-map');
  const url = await el.snapshot();
  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
    img.src = url;
  });
  const canvas = document.createElement('canvas');
  canvas.width = img.width;
  canvas.height = img.height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0);
  const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  let red = 0;
  for (let i = 0; i < data.length; i += 4) {
    // Generous on the exact value: deck.gl antialiases the marker edge and the
    // canvas may be composited, so an exact 255/0/0 match would count only the
    // very centre of each point.
    if (data[i] > 180 && data[i + 1] < 80 && data[i + 2] < 80 && data[i + 3] > 200) {
      red++;
    }
  }
  return { red, width: canvas.width, height: canvas.height };
}
"""


class TestItDrawsAcrossTheAntimeridian:
    """The P0 blank-map bug, verified fixed against the pinned runtime.

    Data spanning ±180 used to vanish: deck.gl rendered one world copy while
    MapLibre repeated the world, so panning toward the seam produced a white
    map. Filed with NIKA 2026-08-03 and fixed upstream in 0.5.9. These tests
    exist so a future runtime bump cannot silently reintroduce it.
    """

    def test_the_map_mounts_on_the_seam(self, page, antimeridian_map) -> None:
        open_map(page, antimeridian_map)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        # Centred on the seam itself, which is where the bug lived.
        assert page.locator("om-map[center='[180.000000, 51.000000]']").count() == 1

    def test_features_on_both_sides_of_the_seam_are_drawn(
        self, page, antimeridian_map
    ) -> None:
        """The actual regression: are the points on screen, or is it white?"""
        open_map(page, antimeridian_map)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        page.wait_for_timeout(1500)  # let the first render settle

        result = page.evaluate(COUNT_RED_PIXELS)
        assert result["width"] > 0 and result["height"] > 0
        assert result["red"] > 0, (
            "no layer pixels on a map centred on the antimeridian - "
            "the world-copy bug is back"
        )


class TestDashedLinesReallyDash:
    """`dash` reaching the markup is not the same as deck.gl honouring it.

    deck.gl ignores a prop it does not understand without complaint, and the
    attribute is in line-width units rather than pixels - a conversion that
    would be silently wrong in either direction. Only a rendered line settles
    it, so this counts the pixels a solid line draws against a dashed one.
    """

    def _red_pixels(self, page, artifact) -> int:
        open_map(page, artifact)
        require_webgl(page)
        page.wait_for_selector("om-map canvas", timeout=30_000)
        page.wait_for_timeout(1500)
        return page.evaluate(COUNT_RED_PIXELS)["red"]

    def test_a_dashed_line_draws_less_than_a_solid_one(
        self, page, solid_line_map, dashed_line_map
    ) -> None:
        solid = self._red_pixels(page, solid_line_map)
        dashed = self._red_pixels(page, dashed_line_map)

        assert solid > 0, "the control line did not draw at all"
        assert dashed > 0, "the dashed line vanished entirely"
        # 4-on/2-off leaves roughly two thirds drawn. Asserting a generous
        # margin rather than a ratio keeps this from failing on antialiasing.
        assert dashed < solid * 0.9, (
            f"dashed line drew {dashed} pixels against a solid {solid} - "
            "the dash pattern is not reaching deck.gl"
        )
