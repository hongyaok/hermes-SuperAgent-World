"""Generate the 12 new Superagent Towns environment sprites and vendor 8 Petdex pets.

Extracts the remaining MIT buildings and props (`house-e.png`, `house-f.png`,
`house-g.png`, `bridge.png`, `bench.png`, `flowers.png`, `signpost.png`) from
the upstream Hermes Town atlases (`https://github.com/sxuff/hermes-town`),
generates the MIT tileable terrain and well sprites (`road-h.png`, `road-v.png`,
`plaza-stone.png`, `water.png`, `well.png`), and vendors 8 real character and
creature spritesheets from `https://petdex.dev/api/manifest` downsampled to
`384x468` WebP (`8x9` grid of `48x52` frames) in `assets/town/pets/`.
"""

from __future__ import annotations

import io
import json
import math
import random
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.petdex_assets import (  # noqa: E402
    MANIFEST_URL,
    PETS_DIR,
    SHEET_HEIGHT,
    SHEET_WIDTH,
    USER_AGENT,
    downsample_sheet,
)
from scripts.extract_hermes_town_assets import (  # noqa: E402
    BUILDINGS,
    frame,
    key_sheet,
    prop_rect,
)

TOWN_DIR = ROOT / "assets" / "town"
HERMES_TOWN_RAW = "https://raw.githubusercontent.com/sxuff/hermes-town/main/src/assets"

BUNDLED_PET_SLUGS = (
    "hermes-kody",
    "coding-cat-pet",
    "ai-workbot",
    "knight-shiba",
    "wizard",
    "green-slime",
    "cyberknight",
    "peri-the-owl",
)


def _fetch_bytes(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _frame_exact(
    sheet: Image.Image,
    rect: tuple[int, int, int, int],
    width: int,
    height: int | None = None,
) -> Image.Image:
    x, y, w, h = rect
    crop = sheet.crop((x, y, x + w, y + h))
    alpha = crop.getchannel("A")
    box = alpha.point(lambda value: 255 if value >= 128 else 0).getbbox()
    if box is None:
        raise ValueError(f"empty sprite in {rect}")
    crop = crop.crop(box)
    target_h = height if height is not None else max(1, round(width * crop.height / crop.width))
    crop = crop.resize((width, target_h), Image.Resampling.LANCZOS)
    pixels = crop.load()
    for yy in range(target_h):
        for xx in range(width):
            r, g, b, a = pixels[xx, yy]
            pixels[xx, yy] = (r, g, b, 255 if a >= 120 else 0)
    return crop


def _cell_rect(sheet: Image.Image, index: int, cols: int, rows: int) -> tuple[int, int, int, int]:
    col, row = index % cols, index // cols
    x0 = round(col * sheet.width / cols)
    y0 = round(row * sheet.height / rows)
    x1 = round((col + 1) * sheet.width / cols)
    y1 = round((row + 1) * sheet.height / rows)
    return (x0, y0, x1 - x0, y1 - y0)


def _generate_road_h() -> Image.Image:
    """Tileable 64x32 horizontal cobblestone path with grassy stone borders."""
    w, h = 64, 32
    img = Image.new("RGBA", (w, h))
    px = img.load()
    rng = random.Random(4201)

    for y in range(h):
        dist_center = abs(y - (h - 1) / 2.0)
        for x in range(w):
            wave = math.sin((x / w) * math.pi * 4.0) * 1.2
            edge = dist_center + wave
            grain = rng.randint(-6, 6)
            if edge > 12.2:
                # Grass border matching grass.png palette
                r, g, b = 78 + grain, 102 + grain, 48 + grain // 2
            elif edge > 10.3:
                # Curb / mossy border transition
                r, g, b = 96 + grain, 98 + grain, 72 + grain
            else:
                # Warm earth mortar base
                r, g, b = 128 + grain, 112 + grain, 84 + grain
            px[x, y] = (
                max(0, min(255, r)),
                max(0, min(255, g)),
                max(0, min(255, b)),
                255,
            )

    draw = ImageDraw.Draw(img)
    # Draw tileable cobblestone rows along the road core
    for row_idx, y0 in enumerate((6, 11, 16, 21)):
        x = 0
        col_idx = 0
        while x < w:
            sw = 7 if (row_idx + col_idx) % 2 == 0 else 9
            if x + sw > w:
                sw = w - x
            if sw >= 4:
                shade = rng.randint(-10, 14)
                base = (154 + shade, 144 + shade, 122 + shade, 255)
                hi = (182 + shade, 172 + shade, 148 + shade, 255)
                sh = (98 + shade // 2, 90 + shade // 2, 74 + shade // 2, 255)
                draw.rectangle([x + 1, y0, x + sw - 2, y0 + 3], fill=base)
                draw.line([x + 1, y0, x + sw - 2, y0], fill=hi)
                draw.line([x + 1, y0 + 3, x + sw - 2, y0 + 3], fill=sh)
            x += sw
            col_idx += 1
    return img


def _generate_road_v() -> Image.Image:
    """Tileable 32x64 vertical cobblestone path with grassy stone borders."""
    road_h = _generate_road_h()
    return road_h.transpose(Image.Transpose.ROTATE_270)


def _generate_plaza_stone() -> Image.Image:
    """Tileable 64x64 warm flagstone plaza pavement pattern."""
    w, h = 64, 64
    img = Image.new("RGBA", (w, h))
    px = img.load()
    rng = random.Random(7109)

    for y in range(h):
        for x in range(w):
            grain = rng.randint(-5, 5)
            px[x, y] = (104 + grain, 96 + grain, 82 + grain, 255)

    draw = ImageDraw.Draw(img)
    row_h = 8
    for row in range(h // row_h):
        y0 = row * row_h
        offset = 4 if (row % 2 == 1) else 0
        widths = [12, 10, 14, 12, 16]
        x = -offset
        idx = 0
        while x < w:
            sw = widths[(row + idx) % len(widths)]
            shade = ((row * 7 + idx * 13) % 19) - 9
            fill = (146 + shade, 137 + shade, 118 + shade, 255)
            hi = (172 + shade, 162 + shade, 140 + shade, 255)
            sh = (108 + shade // 2, 100 + shade // 2, 85 + shade // 2, 255)
            for dx in (0, w):
                rx0 = max(0, x + dx + 1)
                rx1 = min(w - 1, x + dx + sw - 1)
                if rx1 > rx0:
                    draw.rectangle([rx0, y0 + 1, rx1, y0 + row_h - 2], fill=fill)
                    draw.line([rx0, y0 + 1, rx1, y0 + 1], fill=hi)
                    draw.line([rx0, y0 + row_h - 2, rx1, y0 + row_h - 2], fill=sh)
            if (row + idx) % 5 == 0:
                mx = (x + sw // 2) % w
                px[mx, y0] = (86, 104, 66, 255)
            x += sw
            idx += 1
    return img


def _generate_water() -> Image.Image:
    """Tileable 64x64 pixel-art pond/stream water with subtle ripples and bank edge."""
    w, h = 64, 64
    img = Image.new("RGBA", (w, h))
    px = img.load()
    rng = random.Random(9012)

    for y in range(h):
        for x in range(w):
            wave1 = math.sin((x / w) * math.pi * 2.0 + (y / h) * math.pi * 2.0) * 6.0
            wave2 = math.cos((x / w) * math.pi * 4.0 - (y / h) * math.pi * 2.0) * 4.0
            grain = rng.randint(-3, 3)
            # Subtle shallow bank tint on top/bottom 3px while remaining tileable
            edge_dist = min(y, h - 1 - y)
            if edge_dist < 3:
                bank_mix = (3 - edge_dist) / 4.0
                r = round((42 + wave1 + grain) * (1 - bank_mix) + 74 * bank_mix)
                g = round((98 + wave1 + wave2 + grain) * (1 - bank_mix) + 106 * bank_mix)
                b = round((122 + wave1 * 1.2 + grain) * (1 - bank_mix) + 82 * bank_mix)
            else:
                r = round(38 + wave1 * 0.8 + grain)
                g = round(96 + wave1 + wave2 + grain)
                b = round(126 + wave1 * 1.3 + wave2 * 0.6 + grain)
            px[x, y] = (
                max(0, min(255, r)),
                max(0, min(255, g)),
                max(0, min(255, b)),
                255,
            )

    draw = ImageDraw.Draw(img)
    ripples = [
        (6, 10, 15), (34, 14, 12), (18, 26, 16),
        (44, 30, 11), (10, 44, 14), (36, 50, 15), (22, 56, 10),
    ]
    for rx, ry, rw in ripples:
        draw.line([rx, ry, (rx + rw) % w, ry], fill=(138, 192, 206, 255))
        draw.line([rx + 2, ry + 1, min(w - 1, rx + rw - 2), ry + 1], fill=(28, 74, 98, 255))
    return img


def _generate_well() -> Image.Image:
    """Pixel-art 44x48 stone wishing well with wooden roof and hanging bucket."""
    w, h = 44, 48
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    outline = (36, 30, 26, 255)
    stone_dark = (102, 98, 92, 255)
    stone_mid = (138, 132, 122, 255)
    stone_hi = (172, 166, 154, 255)
    water_dark = (32, 74, 98, 255)
    water_hi = (88, 148, 172, 255)
    wood_dark = (92, 58, 34, 255)
    wood_mid = (132, 86, 48, 255)
    wood_hi = (164, 112, 66, 255)
    roof_dark = (118, 52, 42, 255)
    roof_mid = (154, 72, 54, 255)
    roof_hi = (186, 96, 72, 255)

    # Ground shadow rim
    draw.ellipse([5, 39, 38, 46], fill=(28, 36, 22, 255))

    # Wooden support posts
    for px0 in (10, 31):
        draw.rectangle([px0, 14, px0 + 3, 33], fill=wood_mid, outline=outline)
        draw.line([px0 + 1, 15, px0 + 1, 32], fill=wood_hi)

    # Crossbeam + crank + rope + bucket
    draw.rectangle([9, 16, 34, 18], fill=wood_dark, outline=outline)
    draw.line([21, 18, 21, 27], fill=(196, 178, 132, 255))
    draw.line([22, 18, 22, 27], fill=(142, 124, 88, 255))
    draw.rectangle([19, 25, 24, 30], fill=wood_mid, outline=outline)

    # Stone circular base wall
    draw.rectangle([8, 29, 35, 43], fill=stone_mid, outline=outline)
    draw.ellipse([7, 26, 36, 34], fill=stone_hi, outline=outline)
    draw.ellipse([11, 28, 32, 32], fill=water_dark, outline=outline)
    draw.line([15, 30, 26, 30], fill=water_hi)

    # Stone masonry courses
    for sy in (34, 38, 42):
        draw.line([9, sy, 34, sy], fill=stone_dark)
    for sx in (14, 22, 29):
        draw.line([sx, 31, sx, 34], fill=stone_dark)
        draw.line([sx - 2, 35, sx - 2, 38], fill=stone_dark)
        draw.line([sx + 1, 39, sx + 1, 42], fill=stone_dark)

    # Pitched wooden shingle roof
    roof_poly = [(21, 2), (40, 14), (3, 14)]
    draw.polygon(roof_poly, fill=roof_mid, outline=outline)
    draw.line([21, 3, 6, 13], fill=roof_hi)
    draw.line([21, 3, 37, 13], fill=roof_dark)
    for ry in (6, 9, 12):
        half = (ry - 2) * 1.45
        draw.line([round(21 - half), ry, round(21 + half), ry], fill=roof_dark)
    draw.rectangle([4, 13, 39, 15], fill=wood_dark, outline=outline)
    return img


def generate_town_assets() -> None:
    TOWN_DIR.mkdir(parents=True, exist_ok=True)
    print("Downloading Hermes Town MIT source atlases...")
    buildings = key_sheet(Image.open(io.BytesIO(_fetch_bytes(f"{HERMES_TOWN_RAW}/buildings.png"))))
    props = key_sheet(Image.open(io.BytesIO(_fetch_bytes(f"{HERMES_TOWN_RAW}/props.png"))))
    furniture = key_sheet(Image.open(io.BytesIO(_fetch_bytes(f"{HERMES_TOWN_RAW}/furniture.png"))))

    extracted = {
        "house-e.png": frame(buildings, BUILDINGS["library"], 138),
        "house-f.png": frame(buildings, BUILDINGS["post"], 138),
        "house-g.png": frame(buildings, BUILDINGS["observatory"], 138),
        "bridge.png": _frame_exact(props, prop_rect(4), 56, 36),
        "bench.png": _frame_exact(furniture, _cell_rect(furniture, 0, 4, 4), 40, 24),
        "flowers.png": _frame_exact(furniture, _cell_rect(furniture, 1, 4, 4), 36, 24),
        "signpost.png": _frame_exact(furniture, _cell_rect(furniture, 5, 4, 4), 28, 38),
        "well.png": _generate_well(),
        "road-h.png": _generate_road_h(),
        "road-v.png": _generate_road_v(),
        "plaza-stone.png": _generate_plaza_stone(),
        "water.png": _generate_water(),
    }

    for name, img in extracted.items():
        out_path = TOWN_DIR / name
        img.save(out_path, format="PNG")
        print(f"  wrote {name:18} {img.width}x{img.height} ({out_path.stat().st_size} bytes)")


def generate_bundled_pets() -> None:
    PETS_DIR.mkdir(parents=True, exist_ok=True)
    print("Fetching Petdex manifest...")
    manifest_bytes = _fetch_bytes(MANIFEST_URL)
    manifest_data = json.loads(manifest_bytes.decode("utf-8"))
    pets_list = manifest_data.get("pets", manifest_data)
    by_slug = {p["slug"]: p for p in pets_list if isinstance(p, dict) and "slug" in p}

    catalog_entries = []
    for slug in BUNDLED_PET_SLUGS:
        item = by_slug.get(slug)
        if not item:
            print(f"  WARNING: {slug} not found in manifest, skipping")
            continue
        url = item["spritesheetUrl"]
        raw = _fetch_bytes(url)
        downsampled = downsample_sheet(raw)
        out_file = PETS_DIR / f"{slug}.webp"
        out_file.write_bytes(downsampled)

        with Image.open(io.BytesIO(downsampled)) as check_img:
            assert check_img.size == (SHEET_WIDTH, SHEET_HEIGHT), check_img.size

        entry = {
            "slug": item["slug"],
            "display_name": item.get("displayName") or item["slug"],
            "kind": item.get("kind") or "creature",
            "submitted_by": item.get("submittedBy") or "community",
            "sprite_version": int(item.get("spriteVersionNumber") or 1),
            "spritesheet_url": url,
        }
        catalog_entries.append(entry)
        print(
            f"  vendored pet {slug:20} ({entry['kind']:9}) -> "
            f"{SHEET_WIDTH}x{SHEET_HEIGHT} ({len(downsampled)} bytes)"
        )

    catalog_path = PETS_DIR / "catalog.json"
    catalog_path.write_text(
        json.dumps({"pets": catalog_entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(catalog_entries)} pets to {catalog_path}")


def main() -> None:
    generate_town_assets()
    generate_bundled_pets()


if __name__ == "__main__":
    main()
