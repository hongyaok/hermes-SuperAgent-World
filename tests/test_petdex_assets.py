"""Tests for the Petdex character spritesheet pipeline and expanded town assets."""

from __future__ import annotations

import base64
import io
import json
import random
from pathlib import Path

from PIL import Image

from dashboard import petdex_assets, town_assets


class _FakeHTTPResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, limit: int = -1) -> bytes:
        if limit is None or limit < 0:
            return self._payload
        return self._payload[:limit]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _make_synthetic_sheet(width: int, height: int, rows: int) -> bytes:
    """Create a synthetic RGBA sheet with distinct row colours."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    row_h = height // rows
    for r in range(rows):
        color = ((r * 25 + 30) % 256, (r * 40 + 60) % 256, (r * 55 + 90) % 256, 255)
        band = Image.new("RGBA", (width, row_h), color)
        img.paste(band, (0, r * row_h))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_expanded_town_assets_allowlist_and_dimensions():
    expected_new = {
        "house-e.png",
        "house-f.png",
        "house-g.png",
        "road-h.png",
        "road-v.png",
        "plaza-stone.png",
        "water.png",
        "bridge.png",
        "well.png",
        "signpost.png",
        "bench.png",
        "flowers.png",
        "bulletin-board.png",
    }
    assert expected_new <= set(town_assets.ASSETS)
    assert len(town_assets.ASSETS) == 27

    manifest = town_assets.manifest()
    assert len(manifest) == 27
    for name in expected_new:
        raw, mime = town_assets.read(name)
        assert mime == "image/png"
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(io.BytesIO(raw)) as img:
            assert img.width > 0 and img.height > 0

    exact_sizes = {
        "road-h.png": (64, 32),
        "road-v.png": (32, 64),
        "plaza-stone.png": (64, 64),
        "water.png": (64, 64),
        "bridge.png": (56, 36),
        "well.png": (44, 48),
        "signpost.png": (28, 38),
        "bench.png": (40, 24),
        "flowers.png": (36, 24),
        "bulletin-board.png": (48, 44),
    }
    for name, size in exact_sizes.items():
        raw, _ = town_assets.read(name)
        with Image.open(io.BytesIO(raw)) as img:
            assert img.size == size, f"{name} expected {size}, got {img.size}"


def test_bundled_catalog_and_sheets_geometry():
    catalog = petdex_assets.bundled_catalog()
    assert len(catalog) >= 6

    seen_slugs = set()
    for entry in catalog:
        slug = entry["slug"]
        seen_slugs.add(slug)
        assert entry["kind"] in ("character", "creature")
        assert entry["display_name"]
        assert entry["submitted_by"]
        assert entry["sprite_version"] >= 1

        sheet_path = petdex_assets.PETS_DIR / f"{slug}.webp"
        assert sheet_path.is_file(), f"missing bundled sheet for {slug}"
        with Image.open(sheet_path) as img:
            assert img.size == (petdex_assets.SHEET_WIDTH, petdex_assets.SHEET_HEIGHT)
            assert img.size == (384, 468)

    assert len(seen_slugs) == len(catalog)


def test_downsample_sheet_v1_9_rows_and_v2_11_rows():
    v1_bytes = _make_synthetic_sheet(1536, 1872, 9)
    v1_out = petdex_assets.downsample_sheet(v1_bytes)
    with Image.open(io.BytesIO(v1_out)) as img1:
        assert img1.size == (384, 468)
        rgba1 = img1.convert("RGBA")
        # Row 8 (bottom of 9 rows) should match row index 8 color from synthetic generator
        r8_color = ((8 * 25 + 30) % 256, (8 * 40 + 60) % 256, (8 * 55 + 90) % 256, 255)
        assert rgba1.getpixel((24, 8 * 52 + 26)) == r8_color

    v2_bytes = _make_synthetic_sheet(1536, 2288, 11)
    v2_out = petdex_assets.downsample_sheet(v2_bytes)
    with Image.open(io.BytesIO(v2_out)) as img2:
        assert img2.size == (384, 468)
        rgba2 = img2.convert("RGBA")
        # Row 8 in the cropped 9-row output must still be row index 8 (rows 9 and 10 cropped off)
        assert rgba2.getpixel((24, 8 * 52 + 26)) == r8_color


def test_fetch_remote_manifest_filters_caches_and_falls_back(tmp_path: Path, monkeypatch):
    calls: list[str] = []
    fake_manifest = {
        "generatedAt": "2026-11-08T00:00:00Z",
        "total": 4,
        "pets": [
            {
                "slug": "astro-fox",
                "displayName": "Astro Fox",
                "kind": "creature",
                "submittedBy": "alice",
                "spritesheetUrl": "https://assets.petdex.dev/pets/astro-fox-123/sprite.webp",
                "spriteVersionNumber": 2,
            },
            {
                "slug": "desk-lamp-prop",
                "displayName": "Desk Lamp",
                "kind": "object",
                "submittedBy": "bob",
                "spritesheetUrl": "https://assets.petdex.dev/pets/desk-lamp-123/sprite.webp",
                "spriteVersionNumber": 1,
            },
            {
                "slug": "evil-host-pet",
                "displayName": "Evil Host",
                "kind": "character",
                "submittedBy": "mallory",
                "spritesheetUrl": "https://evil.example.com/pets/evil/sprite.webp",
                "spriteVersionNumber": 1,
            },
            {
                "slug": "pixel-bard",
                "displayName": "Pixel Bard",
                "kind": "character",
                "submittedBy": "carol",
                "spritesheetUrl": "https://assets.petdex.dev/pets/pixel-bard-456/sprite.webp",
                "spriteVersionNumber": 1,
            },
        ],
    }

    def fake_urlopen(url, timeout=8):
        calls.append(str(url))
        return _FakeHTTPResponse(json.dumps(fake_manifest).encode("utf-8"))

    monkeypatch.setattr(petdex_assets, "_urlopen", fake_urlopen)

    result = petdex_assets.fetch_remote_manifest(cache_dir=tmp_path)
    assert [p["slug"] for p in result] == ["astro-fox", "pixel-bard"]
    assert len(calls) == 1
    assert (tmp_path / "petdex_manifest.json").is_file()

    # Second call within 24h TTL uses the cached file without hitting _urlopen
    def fail_urlopen(url, timeout=8):
        raise OSError("network disabled")

    monkeypatch.setattr(petdex_assets, "_urlopen", fail_urlopen)
    cached_result = petdex_assets.fetch_remote_manifest(cache_dir=tmp_path)
    assert [p["slug"] for p in cached_result] == ["astro-fox", "pixel-bard"]

    # Uncached call on network error falls back cleanly to bundled_catalog()
    empty_cache = tmp_path / "offline"
    fallback = petdex_assets.fetch_remote_manifest(cache_dir=empty_cache)
    assert [p["slug"] for p in fallback] == [
        p["slug"] for p in petdex_assets.bundled_catalog()
    ]


def test_pick_random_pet_respects_exclusions():
    catalog = petdex_assets.bundled_catalog()
    all_slugs = [p["slug"] for p in catalog]
    exclude_all_but_one = all_slugs[:-1]
    expected_remaining = all_slugs[-1]

    picked = petdex_assets.pick_random_pet(
        exclude=exclude_all_but_one,
        allow_remote=False,
        rng=random.Random(1),
    )
    assert picked["slug"] == expected_remaining
    assert picked["kind"] in ("character", "creature")

    rng = random.Random(42)
    varied = {
        petdex_assets.pick_random_pet(allow_remote=False, rng=rng)["slug"]
        for _ in range(25)
    }
    assert len(varied) >= 3


def test_get_pet_sprite_bundled_remote_traversal_and_fallback(tmp_path: Path, monkeypatch):
    # 1. Bundled pet resolves directly from disk without network
    bundled_slug = petdex_assets.bundled_catalog()[0]["slug"]
    sprite = petdex_assets.get_pet_sprite(bundled_slug, allow_remote=False)
    assert sprite["slug"] == bundled_slug
    assert sprite["cols"] == 8
    assert sprite["rows"] == 9
    assert sprite["frame_width"] == 48
    assert sprite["frame_height"] == 52
    assert sprite["rows_map"]["idle"] == 0
    assert sprite["rows_map"]["review"] == 8
    assert sprite["data_url"].startswith(f"data:{sprite['mime']};base64,")

    # 2. Remote pet is fetched, downsampled to 384x468, and cached to tmp_path/pets/<slug>.webp
    synthetic_remote = _make_synthetic_sheet(1536, 1872, 9)
    remote_manifest = {
        "pets": [
            {
                "slug": "remote-griffin",
                "displayName": "Remote Griffin",
                "kind": "creature",
                "submittedBy": "tester",
                "spritesheetUrl": "https://assets.petdex.dev/pets/remote-griffin-999/sprite.png",
                "spriteVersionNumber": 1,
            }
        ]
    }
    urls_fetched: list[str] = []

    def fake_urlopen(url, timeout=8):
        u = str(url)
        urls_fetched.append(u)
        if "api/manifest" in u:
            return _FakeHTTPResponse(json.dumps(remote_manifest).encode("utf-8"))
        if "remote-griffin-999" in u:
            return _FakeHTTPResponse(synthetic_remote)
        raise OSError(f"unexpected URL {u}")

    monkeypatch.setattr(petdex_assets, "_urlopen", fake_urlopen)
    remote_sprite = petdex_assets.get_pet_sprite(
        "remote-griffin", allow_remote=True, cache_dir=tmp_path
    )
    assert remote_sprite["slug"] == "remote-griffin"
    assert remote_sprite["display_name"] == "Remote Griffin"
    cached_webp = tmp_path / "pets" / "remote-griffin.webp"
    assert cached_webp.is_file()

    b64_part = remote_sprite["data_url"].split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(b64_part))) as decoded_img:
        assert decoded_img.size == (384, 468)

    # 3. Path traversal slugs are rejected and fall back deterministically to a bundled pet
    bundled_slugs = {p["slug"] for p in petdex_assets.bundled_catalog()}
    for bad_slug in ("../etc/passwd", "..\\ town_assets", "a/b/c", ""):
        safe_sprite = petdex_assets.get_pet_sprite(
            bad_slug, allow_remote=True, cache_dir=tmp_path
        )
        assert safe_sprite["slug"] in bundled_slugs
        assert safe_sprite["data_url"].startswith("data:image/")

    # 4. HTTP failure on unknown remote slug falls back deterministically to a bundled pet
    def broken_urlopen(url, timeout=8):
        raise OSError("HTTP 503 Service Unavailable")

    monkeypatch.setattr(petdex_assets, "_urlopen", broken_urlopen)
    fallback_a = petdex_assets.get_pet_sprite(
        "nonexistent-dragon", allow_remote=True, cache_dir=tmp_path / "err"
    )
    fallback_b = petdex_assets.get_pet_sprite(
        "nonexistent-dragon", allow_remote=True, cache_dir=tmp_path / "err"
    )
    assert fallback_a["slug"] in bundled_slugs
    assert fallback_a["slug"] == fallback_b["slug"]

    # 5. Bundle helper resolves multiple slugs
    bundle = petdex_assets.get_pets_bundle(
        [bundled_slug, "remote-griffin"], allow_remote=False, cache_dir=tmp_path
    )
    assert set(bundle.keys()) == {bundled_slug, "remote-griffin"}
