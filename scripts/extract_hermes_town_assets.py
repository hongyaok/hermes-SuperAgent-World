"""Extract the Superagent Towns sprites from Hermes Town's MIT atlases.

The source sheets are authored with a magenta chroma key. This script ports
the small part of ``src/art/reference.ts`` that turns one rectangle into a
trimmed RGBA sprite, then downsamples to the size the desktop scene uses.

Usage:
    python scripts/extract_hermes_town_assets.py C:/path/to/hermes-town

The source repository is not vendored; only the resulting PNGs in
``assets/town`` are. See ``assets/town/ATTRIBUTION.md``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "town"

# Rectangles are copied from hermes-town src/art/reference.ts. All coordinates
# are in source pixels and name one whole authored sprite.
BUILDINGS = {
    "library": (0, 0, 444, 480),
    "hall": (444, 0, 443, 480),
    "workshop": (887, 0, 444, 478),
    "post": (1331, 0, 443, 480),
    "observatory": (0, 481, 444, 406),
    "tavern": (444, 481, 443, 406),
    "forge": (887, 548, 444, 339),
    "house": (1331, 481, 443, 406),
}
TREES = [
    ((0, 0, 525, 518), 84),
    ((540, 0, 490, 518), 76),
    ((1100, 0, 436, 518), 56),
]
PROPS = {
    "fountain": 0,
    "rock": 5,
    "lamp": 6,
}


def key_sheet(image: Image.Image) -> Image.Image:
    """Remove the reserved magenta background and harden the silhouette."""
    rgb = image.convert("RGB")
    px = rgb.load()
    w, h = rgb.size
    out = Image.new("RGBA", rgb.size)
    dst = out.load()
    keyness = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            dist = max(255 - r, g, 255 - b) / 255.0
            keyness[y][x] = max(0.0, 1.0 - dist / 0.55)
    for y in range(h):
        for x in range(w):
            kn = keyness[y][x]
            r, g, b = px[x, y]
            if kn >= 0.999:
                dst[x, y] = (0, 0, 0, 0)
                continue
            if kn <= 0:
                dst[x, y] = (r, g, b, 255)
                continue
            a = 1.0 - kn
            dst[x, y] = (
                max(0, min(255, round((r - (1 - a) * 255) / a))),
                max(0, min(255, round(g / a))),
                max(0, min(255, round((b - (1 - a) * 255) / a))),
                round(255 * a),
            )
    # The author's sheets carry a dark halo; drop the same thin, mostly-key
    # border pixels the TypeScript renderer drops.
    for y in range(h):
        for x in range(w):
            r, g, b, a = dst[x, y]
            if a == 0:
                continue
            border = (
                (x == 0 or dst[x - 1, y][3] < 40)
                or (x + 1 >= w or dst[x + 1, y][3] < 40)
                or (y == 0 or dst[x, y - 1][3] < 40)
                or (y + 1 >= h or dst[x, y + 1][3] < 40)
            )
            if not border:
                continue
            lum = (r + g + b) / 3
            purplish = r > g + 25 and b > g + 25
            if a < 160 or (lum < 70 and purplish):
                dst[x, y] = (0, 0, 0, 0)
    return out


def frame(sheet: Image.Image, rect: tuple[int, int, int, int], width: int) -> Image.Image:
    x, y, w, h = rect
    crop = sheet.crop((x, y, x + w, y + h))
    alpha = crop.getchannel("A")
    box = alpha.point(lambda value: 255 if value >= 128 else 0).getbbox()
    if box is None:
        raise ValueError(f"empty sprite in {rect}")
    crop = crop.crop(box)
    height = max(1, round(width * crop.height / crop.width))
    crop = crop.resize((width, height), Image.Resampling.LANCZOS)
    pixels = crop.load()
    for yy in range(height):
        for xx in range(width):
            r, g, b, a = pixels[xx, yy]
            pixels[xx, yy] = (r, g, b, 255 if a >= 120 else 0)
    return crop


def prop_rect(index: int) -> tuple[int, int, int, int]:
    col, row = index % 4, index // 4
    x = round(col * 443.5)
    y = row * 444
    w = 443 if col % 2 else 444
    h = 443 if row else 444
    return (x, y, w, h)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="path to the hermes-town repository")
    args = parser.parse_args()
    assets = args.source / "src" / "assets"
    OUT.mkdir(parents=True, exist_ok=True)

    buildings = key_sheet(Image.open(assets / "buildings.png"))
    vegetation = key_sheet(Image.open(assets / "vegetation.png"))
    props = key_sheet(Image.open(assets / "props.png"))

    outputs = {
        "house-router.png": frame(buildings, BUILDINGS["hall"], 190),
        "house-a.png": frame(buildings, BUILDINGS["house"], 138),
        "house-b.png": frame(buildings, BUILDINGS["tavern"], 138),
        "house-c.png": frame(buildings, BUILDINGS["workshop"], 138),
        "house-d.png": frame(buildings, BUILDINGS["forge"], 138),
        "tree-a.png": frame(vegetation, TREES[0][0], TREES[0][1]),
        "tree-b.png": frame(vegetation, TREES[1][0], TREES[1][1]),
        "tree-c.png": frame(vegetation, TREES[2][0], TREES[2][1]),
        "bush.png": frame(vegetation, (1050, 600, 486, 424), 48),
        "fountain.png": frame(props, prop_rect(PROPS["fountain"]), 82),
        "rock.png": frame(props, prop_rect(PROPS["rock"]), 25),
        "lamp.png": frame(props, prop_rect(PROPS["lamp"]), 22),
    }
    for name, image in outputs.items():
        image.save(OUT / name)
        print(f"{name:20} {image.width}x{image.height}")


if __name__ == "__main__":
    main()
