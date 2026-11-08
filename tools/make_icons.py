#!/usr/bin/env python3
"""Generate the Luna PWA icons (icon-192.png, icon-512.png).

The GitHub push tool is text-only, so the binary icons are NOT in the
repo — run this once on the host (PythonAnywhere console, inside the
app virtualenv; `pip install pillow` first if needed) and the manifest
+ apple-touch-icon references resolve:

    cd ~/luna-test-driver
    python3 tools/make_icons.py

Design: ARTISY navy (#0a0e1a) rounded square, gold (#f5c451)
crescent moon, "LUNA" in gold beneath it. Same icons the bench
generated for local testing.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent.parent
NAVY = (10, 14, 26, 255)
GOLD = (245, 196, 81, 255)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = int(size * 0.12)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=NAVY)
    # Gold crescent: a gold disc with a navy disc cut out of it.
    cx, cy = size // 2, int(size * 0.36)
    r = int(size * 0.20)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GOLD)
    ox, oy = cx + int(r * 0.45), cy - int(r * 0.18)
    draw.ellipse([ox - r, oy - r, ox + r, oy + r], fill=NAVY)
    # LUNA wordmark beneath the moon.
    font = _font(int(size * 0.13))
    text = "LUNA"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], int(size * 0.66) - bbox[1]),
              text, font=font, fill=GOLD)
    return img


def main() -> None:
    for size in (192, 512):
        path = HERE / f"icon-{size}.png"
        make_icon(size).save(path, "PNG")
        print(f"wrote {path} ({size}x{size})")


if __name__ == "__main__":
    main()
