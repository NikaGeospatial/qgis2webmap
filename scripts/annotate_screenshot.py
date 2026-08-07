"""Draw the docs' house-style annotations onto a screenshot.

House style, from docs/images/NEEDED.md:
- one red #e03131 rectangle, 3 px, square corners, per thing to click
- numbered circles (red ground, white numeral) only for ordered sequences
- no arrows, no shadows, no blur
- drawn at final resolution so edges stay crisp

Usage:
    annotate.py IN OUT --box X,Y,W,H [--box ...] [--num X,Y,N ...] [--scale S]

`--scale` multiplies every coordinate, so boxes can be described against a
downscaled preview and drawn on the full-resolution original.
"""

from __future__ import annotations

import argparse

from PIL import Image, ImageDraw, ImageFont

RED = (224, 49, 49)
WHITE = (255, 255, 255)


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/run/current-system/sw/share/X11/fonts/DejaVuSans-Bold.ttf",
        "/nix/store/../share/fonts/truetype/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("src")
    p.add_argument("dest")
    p.add_argument("--box", action="append", default=[], help="X,Y,W,H")
    p.add_argument("--num", action="append", default=[], help="X,Y,N")
    p.add_argument("--redact", action="append", default=[], help="X,Y,W,H[,TEXT]")
    p.add_argument("--crop", help="X,Y,W,H applied before anything else")
    p.add_argument("--width", type=int, default=3)
    p.add_argument("--scale", type=float, default=1.0)
    args = p.parse_args()

    img = Image.open(args.src).convert("RGB")
    if args.crop:
        x, y, w, h = (int(v) for v in args.crop.split(","))
        img = img.crop((x, y, x + w, y + h))
    draw = ImageDraw.Draw(img)
    s = args.scale

    # Redaction first, so an annotation box can sit on top of a redacted field
    # if the field itself is the thing being pointed at. Painted flat rather
    # than blurred: a blur of a short string is often reversible, and on a
    # public docs site "reversible" is the same as "published".
    for spec in args.redact:
        parts = spec.split(",")
        x, y, w, h = (float(v) * s for v in parts[:4])
        label = parts[4] if len(parts) > 4 else ""
        draw.rectangle([x, y, x + w, y + h], fill=(232, 232, 232))
        if label:
            font = _font(int(min(h * 0.62, 22)))
            draw.text(
                (x + 6, y + h / 2), label, font=font, fill=(90, 90, 90), anchor="lm"
            )

    for spec in args.box:
        x, y, w, h = (float(v) * s for v in spec.split(","))
        # `outline` grows inward from the given rect, so the box never eats the
        # pixels just outside it - important when boxing a control that sits
        # tight against its neighbour.
        draw.rectangle([x, y, x + w, y + h], outline=RED, width=args.width)

    for spec in args.num:
        x, y, n = spec.split(",")
        cx, cy = float(x) * s, float(y) * s
        r = max(11.0, 13.0 * s)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=RED)
        font = _font(int(r * 1.3))
        draw.text((cx, cy), n, font=font, fill=WHITE, anchor="mm")

    img.save(args.dest)
    print(f"{args.dest}  {img.width}x{img.height}")


if __name__ == "__main__":
    main()
