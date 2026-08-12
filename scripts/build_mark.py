"""Generate the QGIS2WebMap brand marks.

The mark exists at two optical sizes, which is why this script exists rather
than the SVGs being hand-edited:

  * `docs/assets/logo.svg`  - the full artwork. OnlyMap's stacked slabs
    projected from a holographic emitter, with an extruded city on the top
    slab. For large use only: the README banner, the docs hero, social.
  * `docs/assets/mark.svg`  - the simplified mark, and
    `nika_onlymap_exporter/icons/qgis2webmap.svg` - the same file. For small
    use: the QGIS toolbar (24 px by default), the Plugin Manager, the favicon
    and the site masthead.
  * `docs/assets/banner.svg` - the README banner: mark plus wordmark.

Why two sizes at all: QGIS draws toolbar icons at 24 px. Scaled to that, the
full artwork's 0.7-unit streets land at 0.26 px and its 3.8-unit slab gaps at
1.4 px, so the streets dissolve, the layers merge and the emitter detail
disappears - a green blob. The simplified mark drops the emitter and the glow,
uses two slabs instead of three, one bold street instead of a grid, and heavier
borders, so it survives 16 px.

Three constraints are load-bearing in every file this writes. Each one looks
fine in Chromium and breaks elsewhere, which is how each was missed:

  * NO gradients and NO filters. An unsupported paint server falls back to
    solid BLACK in plain SVG viewers - a gradient "glow" rendered as a black
    cone. Glows here are stacked flat fills with `fill-opacity`.
  * NO clip-path. Honoured by Chromium, ignored by other renderers - roads
    sprayed outside the slab. All geometry is computed to fit instead.
  * Buildings are positioned in MAP space and projected onto the slab, never in
    screen coordinates, so they sit on the surface instead of floating over it.

Run from the repository root:  python3 scripts/build_mark.py
"""

from __future__ import annotations

import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LIME = "#abf051"
SHADE = "#617e35"
DEEP = "#33471c"
PAPER = "#f5f2e7"
AMBER = "#eba941"
PLATE = "#170d19"
SLAB = "#241328"

# Everything on a slab is expressed in "map space": a, b in [-0.5, 0.5], where
# the square maps onto the isometric diamond. Keeping road and parcel geometry
# inside that range is what makes clip-path unnecessary.
EDGE = 0.50


def project(cx: float, cy: float, hw: float, hh: float, a: float, b: float):
    return cx + a * hw + b * hw, cy - a * hh + b * hh


def poly(cx, cy, hw, hh, pts, fill, indent="  "):
    coords = [project(cx, cy, hw, hh, a, b) for a, b in pts]
    d = " ".join(
        ("M" if i == 0 else "L") + f"{x:.2f} {y:.2f}" for i, (x, y) in enumerate(coords)
    )
    return f'{indent}<path d="{d} Z" fill="{fill}"/>'


def slab_path(cx, cy, hw, hh, cut=2.6):
    """An isometric diamond with softened corners, the way OnlyMap draws it."""
    top, right = (cx, cy - hh), (cx + hw, cy)
    bottom, left = (cx, cy + hh), (cx - hw, cy)
    t = cut / math.hypot(hw, hh)
    mix = lambda p, q: (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))
    p = [
        mix(top, right), mix(right, top), mix(right, bottom), mix(bottom, right),
        mix(bottom, left), mix(left, bottom), mix(left, top), mix(top, left),
    ]
    f = lambda v: f"{v[0]:.2f} {v[1]:.2f}"
    return (
        f"M{f(p[0])} L{f(p[1])} Q{f(right)} {f(p[2])} L{f(p[3])} Q{f(bottom)} {f(p[4])} "
        f"L{f(p[5])} Q{f(left)} {f(p[6])} L{f(p[7])} Q{f(top)} {f(p[0])} Z"
    )


def building(cx, cy, w, d, h, top=PAPER):
    """One extruded block: shaded face, lit face, roof."""
    return (
        f'    <path d="M{cx - w:g} {cy:g} L{cx:g} {cy + d:g} L{cx:g} {cy + d - h:g} '
        f'L{cx - w:g} {cy - h:g} Z" fill="{SHADE}"/>\n'
        f'    <path d="M{cx:g} {cy + d:g} L{cx + w:g} {cy:g} L{cx + w:g} {cy - h:g} '
        f'L{cx:g} {cy + d - h:g} Z" fill="{LIME}"/>\n'
        f'    <path d="M{cx:g} {cy - d - h:g} L{cx + w:g} {cy - h:g} L{cx:g} '
        f'{cy + d - h:g} L{cx - w:g} {cy - h:g} Z" fill="{top}"/>\n'
    )


def city(cx, cy, hw, hh, blocks):
    """Place buildings from map-space footprints, back to front."""
    drawn = []
    for a, b, half, height, roof in blocks:
        bx, by = project(cx, cy, hw, hh, a, b)
        drawn.append((by, building(bx, by, 2 * half * hw, 2 * half * hh, height, roof)))
    drawn.sort(key=lambda item: item[0])
    return "".join(part for _, part in drawn)


def cone(bl, br, y_bottom, tl, tr, y_top, steps=18, alpha=0.030):
    """A beam of light as stacked flat trapezoids - never a gradient."""
    span = y_bottom - y_top
    out = [f'  <g fill="{LIME}" fill-opacity="{alpha}">']
    for k in range(1, steps + 1):
        f = k / steps
        y = y_bottom - f * span
        out.append(
            f'    <path d="M{bl:g} {y_bottom:g} L{br:g} {y_bottom:g} '
            f'L{br + f * (tr - br):.2f} {y:.2f} L{bl + f * (tl - bl):.2f} {y:.2f} Z"/>'
        )
    out.append("  </g>")
    return "\n".join(out)


def halo(cx, cy, rx, ry, steps=12, alpha=0.032):
    out = [f'  <g fill="{LIME}" fill-opacity="{alpha}">']
    for k in range(1, steps + 1):
        f = k / steps
        out.append(f'    <ellipse cx="{cx}" cy="{cy}" rx="{rx * f:.2f}" ry="{ry * f:.2f}"/>')
    out.append("  </g>")
    return "\n".join(out)


HEADER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb}" width="{w}" height="{h}"
     role="img" aria-label="QGIS2WebMap by NIKA">
  <title>QGIS2WebMap by NIKA</title>
  <!--
    GENERATED by scripts/build_mark.py - edit that, not this file.
    {note}

    Deliberately does NOT use or resemble the QGIS logo; the QGIS trademark
    guidelines require separate permission for that.

    No gradients, no filters, no clip-path: each falls back badly outside
    Chromium. See the script's docstring for why.
  -->
"""


def document(vb, w, h, note, body):
    return HEADER.format(vb=vb, w=w, h=h, note=note) + body + "</svg>\n"


# --------------------------------------------------------------------------
# the full artwork
# --------------------------------------------------------------------------
def build_logo() -> str:
    cx, cy, hw, hh = 32, 29, 21, 8.8
    aw = 0.030
    body = [cone(25, 39, 50.5, 10, 54, 31), ""]
    body.append(f'  <path d="{slab_path(cx, cy + 7.5, hw, hh)}" fill="{DEEP}"/>')
    body.append(f'  <path d="{slab_path(cx, cy + 3.8, hw, hh)}" fill="{SHADE}"/>')
    body.append(f'  <path d="{slab_path(cx, cy, hw, hh)}" fill="{SLAB}"/>')
    body.append(
        poly(cx, cy, hw, hh,
             [(-0.20 + aw, 0.22 - aw), (0.20 + aw, 0.22 - aw),
              (0.20 + aw, EDGE), (-0.20 + aw, EDGE)], AMBER)
    )
    body.append("  <g>")
    for c in (-0.20, 0.22):
        body.append(poly(cx, cy, hw, hh,
                         [(c - aw, -EDGE), (c + aw, -EDGE), (c + aw, EDGE), (c - aw, EDGE)],
                         PAPER, "    "))
    body.append(poly(cx, cy, hw, hh,
                     [(-EDGE, 0.20 - aw), (-EDGE, 0.20 + aw), (EDGE, 0.20 + aw), (EDGE, 0.20 - aw)],
                     PAPER, "    "))
    # the bending road: a quadratic Bezier sampled in map space, each sample
    # offset along its own perpendicular, so the ribbon curves smoothly and
    # still foreshortens correctly. A stroked path would not.
    p0, p1, p2, w = (-EDGE, -0.30), (0.0, -0.11), (EDGE, -0.03), 0.034
    left, right = [], []
    for i in range(41):
        t = i / 40
        u = 1 - t
        c = (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
             u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])
        d = (2 * u * (p1[0] - p0[0]) + 2 * t * (p2[0] - p1[0]),
             2 * u * (p1[1] - p0[1]) + 2 * t * (p2[1] - p1[1]))
        length = math.hypot(*d) or 1
        n = (-d[1] / length * w, d[0] / length * w)
        left.append((c[0] + n[0], c[1] + n[1]))
        right.append((c[0] - n[0], c[1] - n[1]))
    body.append(poly(cx, cy, hw, hh, left + right[::-1], PAPER, "    "))
    body.append("  </g>")
    body.append(f'  <path d="{slab_path(cx, cy, hw, hh)}" fill="none" '
                f'stroke="{LIME}" stroke-width="1.5"/>')
    body.append("")
    body.append("  <g>")
    body.append(city(cx, cy, hw, hh, [
        (-0.365, 0.07, 0.0786, 11, PAPER),
        (0.010, 0.07, 0.0976, 15, AMBER),
        (0.375, 0.07, 0.0786, 7, PAPER),
        (-0.365, -0.14, 0.0644, 4, PAPER),
    ]).rstrip("\n"))
    body.append("  </g>")
    body.append("")
    body.append(halo(32, 50, 12.5, 4.4, steps=10, alpha=0.040))
    # the emitter: a disc with real thickness, a recessed ring, vents and a lens
    body.append(f'  <path d="M19 53 L19 55.6 A13 4.2 0 0 0 45 55.6 L45 53 Z" fill="{SHADE}"/>')
    body.append(f'  <ellipse cx="32" cy="55.6" rx="13" ry="4.2" fill="{SHADE}"/>')
    body.append(f'  <ellipse cx="32" cy="53" rx="13" ry="4.2" fill="{LIME}"/>')
    body.append(f'  <ellipse cx="32" cy="52.9" rx="9.2" ry="2.85" fill="{SHADE}"/>')
    for angle in (28, 66, 114, 152):
        vx = 32 + 10.4 * math.cos(math.radians(angle))
        vy = 53 + 3.3 * math.sin(math.radians(angle))
        body.append(f'  <ellipse cx="{vx:.2f}" cy="{vy:.2f}" rx="1.5" ry="0.62" '
                    f'fill="{PLATE}" fill-opacity="0.45"/>')
    body.append(f'  <ellipse cx="32" cy="52.8" rx="5.4" ry="1.65" fill="{PLATE}"/>')
    body.append(f'  <ellipse cx="32" cy="52.7" rx="3.1" ry="0.95" fill="{LIME}"/>')
    body.append(f'  <path d="M21.5 51.4 A13 4.2 0 0 1 42.5 51.4" fill="none" '
                f'stroke="{PAPER}" stroke-opacity="0.5" stroke-width="0.8" stroke-linecap="round"/>')
    inner = "\n".join(body) + "\n"
    return document("0 0 64 64", 64, 64,
                    "The FULL mark - large use only. At 24 px this collapses; use mark.svg.",
                    f'\n  <rect width="64" height="64" rx="14" fill="{PLATE}"/>\n\n{inner}')


# --------------------------------------------------------------------------
# the simplified mark, tuned for 16-48 px
# --------------------------------------------------------------------------
def build_mark() -> str:
    cx, cy, hw, hh = 32, 34, 24.5, 10.2
    aw = 0.085                      # one bold street, not a grid
    body = [f'  <path d="{slab_path(cx, cy + 6.2, hw, hh, cut=3.0)}" fill="{SHADE}"/>',
            f'  <path d="{slab_path(cx, cy, hw, hh, cut=3.0)}" fill="{SLAB}"/>']
    body.append(poly(cx, cy, hw, hh,
                     [(aw, aw), (EDGE, aw), (EDGE, EDGE), (aw, EDGE)], AMBER))
    body.append(poly(cx, cy, hw, hh,
                     [(-aw, -EDGE), (aw, -EDGE), (aw, EDGE), (-aw, EDGE)], PAPER))
    body.append(poly(cx, cy, hw, hh,
                     [(-EDGE, -aw), (-EDGE, aw), (EDGE, aw), (EDGE, -aw)], PAPER))
    body.append(f'  <path d="{slab_path(cx, cy, hw, hh, cut=3.0)}" fill="none" '
                f'stroke="{LIME}" stroke-width="2.6" stroke-linejoin="round"/>')
    body.append("")
    body.append("  <g>")
    body.append(city(cx, cy, hw, hh, [
        (-0.30, -0.30, 0.115, 11, PAPER),
        (0.30, -0.30, 0.115, 17, AMBER),
        (-0.30, 0.30, 0.115, 7, PAPER),
    ]).rstrip("\n"))
    body.append("  </g>")
    inner = "\n".join(body) + "\n"
    return document("0 0 64 64", 64, 64,
                    "The SIMPLIFIED mark - for 16-48 px: toolbar, Plugin Manager, favicon, masthead.",
                    f'\n  <rect width="64" height="64" rx="14" fill="{PLATE}"/>\n\n{inner}')


# --------------------------------------------------------------------------
# README banner
# --------------------------------------------------------------------------
def build_banner(logo_body: str) -> str:
    stack = ('"Space Grotesk","Inter",-apple-system,BlinkMacSystemFont,'
             '"Segoe UI",Roboto,Helvetica,Arial,sans-serif')
    art = logo_body[logo_body.index("-->") + 3:].replace("</svg>", "").strip("\n")
    art = "\n".join("      " + line if line.strip() else line for line in art.splitlines())
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 300" width="1200" height="300"
     role="img" aria-label="QGIS2WebMap by NIKA - turn a QGIS project into a portable web map">
  <title>QGIS2WebMap by NIKA</title>
  <!-- GENERATED by scripts/build_mark.py - edit that, not this file. -->

  <rect width="1200" height="300" rx="28" fill="{PLATE}"/>

  <g transform="translate(96 54) scale(3)">
{art}
  </g>

  <g font-family='{stack}' fill="{PAPER}">
    <text x="336" y="126" font-size="62" font-weight="700"
          letter-spacing="-1">QGIS2WebMap</text>
    <text x="336" y="163" font-size="23" font-weight="500"
          fill="{LIME}" letter-spacing="4">BY NIKA</text>
    <text x="336" y="221" font-size="27" fill="#bcb7ab">Turn a QGIS project into a portable web map.</text>
    <text x="336" y="257" font-size="21" fill="#807683">One HTML file. No account, no server, no coding.</text>
  </g>
</svg>
'''


def main() -> None:
    logo = build_logo()
    mark = build_mark()
    targets = {
        ROOT / "docs/assets/logo.svg": logo,
        ROOT / "docs/assets/mark.svg": mark,
        ROOT / "nika_onlymap_exporter/icons/qgis2webmap.svg": mark,
        ROOT / "docs/assets/banner.svg": build_banner(logo),
    }
    for path, text in targets.items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
