"""Petdex character & creature spritesheet pipeline for Superagent Towns.

Bundled pets live in ``assets/town/pets/`` as pre-downsampled ``384x468`` WebP
atlases (8 columns × 9 rows of ``48x52`` frames) alongside ``catalog.json``.
When online, additional pets from ``https://petdex.dev/api/manifest`` can be
fetched on demand from ``https://assets.petdex.dev/pets/``, downsampled with
nearest-neighbour resampling, and cached locally. Every failure path falls back
deterministically to a bundled pet so the town scene never renders broken art.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import random
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Optional

from PIL import Image

PETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "town" / "pets"
CATALOG_PATH = PETS_DIR / "catalog.json"

MANIFEST_URL = "https://petdex.dev/api/manifest"
ASSETS_PREFIX = "https://assets.petdex.dev/pets/"
USER_AGENT = "Hermes-Superagent/1.0 (+https://petdex.dev)"

MANIFEST_TTL_SECONDS = 24 * 60 * 60
MAX_SPRITE_BYTES = 6 * 1024 * 1024
MAX_BUNDLE_SLUGS = 20

COLS = 8
ROWS = 9
FRAME_WIDTH = 48
FRAME_HEIGHT = 52
SHEET_WIDTH = COLS * FRAME_WIDTH    # 384
SHEET_HEIGHT = ROWS * FRAME_HEIGHT  # 468

SOURCE_FRAME_WIDTH = 192
SOURCE_FRAME_HEIGHT = 208

ROW_STATES: tuple[str, ...] = (
    "idle",
    "running-right",
    "running-left",
    "waving",
    "jumping",
    "failed",
    "waiting",
    "running",
    "review",
)

ROWS_MAP: dict[str, int] = {state: idx for idx, state in enumerate(ROW_STATES)}

ALLOWED_KINDS = frozenset({"character", "creature"})
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")

# Module-level urlopen seam so unit tests can monkeypatch network calls cleanly.
_urlopen = urllib.request.urlopen


def _is_valid_slug(slug: Any) -> bool:
    return isinstance(slug, str) and bool(_SLUG_RE.match(slug))


def _is_valid_asset_url(url: Any) -> bool:
    return isinstance(url, str) and url.startswith(ASSETS_PREFIX)


def _open_url(url: str, timeout: int = 8):
    """Call ``_urlopen`` with a User-Agent header when using standard urllib."""
    if _urlopen is urllib.request.urlopen:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        return _urlopen(req, timeout=timeout)
    try:
        return _urlopen(url, timeout=timeout)
    except TypeError:
        return _urlopen(url)


def _normalize_entry(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    slug = str(raw.get("slug") or "").strip()
    if not _is_valid_slug(slug):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ALLOWED_KINDS:
        return None
    spritesheet_url = str(
        raw.get("spritesheet_url") or raw.get("spritesheetUrl") or ""
    ).strip()
    if spritesheet_url and not _is_valid_asset_url(spritesheet_url):
        return None
    display_name = str(
        raw.get("display_name") or raw.get("displayName") or slug
    ).strip() or slug
    submitted_by = str(
        raw.get("submitted_by") or raw.get("submittedBy") or "community"
    ).strip() or "community"
    version_raw = raw.get("sprite_version") or raw.get("spriteVersionNumber") or 1
    try:
        sprite_version = max(1, int(version_raw))
    except (TypeError, ValueError):
        sprite_version = 1
    return {
        "slug": slug,
        "display_name": display_name,
        "kind": kind,
        "submitted_by": submitted_by,
        "sprite_version": sprite_version,
        "spritesheet_url": spritesheet_url,
    }


def bundled_catalog() -> list[dict[str, Any]]:
    """Return metadata for the vendored pets in ``assets/town/pets/catalog.json``."""
    if not CATALOG_PATH.is_file():
        return []
    try:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = payload.get("pets", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in items:
        entry = _normalize_entry(raw)
        if entry is not None:
            result.append(entry)
    return result


def fetch_remote_manifest(
    cache_dir: Optional[Path] = None,
    timeout: int = 8,
) -> list[dict[str, Any]]:
    """Fetch and filter the Petdex manifest, caching for 24h with bundled fallback."""
    cache_file: Optional[Path] = None
    if cache_dir is not None:
        cache_file = Path(cache_dir) / "petdex_manifest.json"
        if cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_at = float(cached.get("cached_at") or 0.0)
                if (time.time() - cached_at) < MANIFEST_TTL_SECONDS:
                    pets = cached.get("pets")
                    if isinstance(pets, list) and pets:
                        valid = [
                            e for e in (_normalize_entry(item) for item in pets)
                            if e is not None and _is_valid_asset_url(e.get("spritesheet_url"))
                        ]
                        if valid:
                            return valid
            except (OSError, ValueError, TypeError):
                pass

    try:
        with _open_url(MANIFEST_URL, timeout=timeout) as resp:
            raw_bytes = resp.read(MAX_SPRITE_BYTES)
        payload = json.loads(raw_bytes.decode("utf-8"))
        raw_pets = payload.get("pets", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_pets, list):
            return bundled_catalog()

        filtered: list[dict[str, Any]] = []
        for item in raw_pets:
            entry = _normalize_entry(item)
            if entry is None:
                continue
            if not _is_valid_asset_url(entry.get("spritesheet_url")):
                continue
            filtered.append(entry)

        if not filtered:
            return bundled_catalog()

        if cache_file is not None:
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(
                    json.dumps({"cached_at": time.time(), "pets": filtered}),
                    encoding="utf-8",
                )
            except OSError:
                pass

        return filtered
    except Exception:
        return bundled_catalog()


def downsample_sheet(raw_bytes: bytes) -> bytes:
    """Crop a Petdex spritesheet to 9 rows (if v2 11-row) and downsample to 384x468."""
    with Image.open(io.BytesIO(raw_bytes)) as img:
        rgba = img.convert("RGBA")

    width, height = rgba.size
    if width <= 0 or height <= 0:
        raise ValueError("invalid spritesheet dimensions")

    # Standard Petdex frames have aspect 192x208 across 8 columns.
    # v1 sheets have 9 rows (e.g. 1536x1872); v2 sheets have 11 rows (e.g. 1536x2288).
    expected_row_height = (width / COLS) * (SOURCE_FRAME_HEIGHT / SOURCE_FRAME_WIDTH)
    expected_nine_rows_height = round(expected_row_height * ROWS)
    if height > round(expected_nine_rows_height * 1.05):
        rgba = rgba.crop((0, 0, width, min(height, expected_nine_rows_height)))

    if rgba.size != (SHEET_WIDTH, SHEET_HEIGHT):
        rgba = rgba.resize((SHEET_WIDTH, SHEET_HEIGHT), Image.Resampling.NEAREST)

    out = io.BytesIO()
    try:
        rgba.save(out, format="WEBP", lossless=True, method=4)
    except Exception:
        out = io.BytesIO()
        rgba.save(out, format="PNG")
    return out.getvalue()


def pick_random_pet(
    exclude: Iterable[str] = (),
    *,
    allow_remote: bool = True,
    cache_dir: Optional[Path] = None,
    rng: Optional[random.Random] = None,
) -> dict[str, Any]:
    """Pick a random pet from the catalog, preferring slugs not in ``exclude``."""
    catalog = (
        fetch_remote_manifest(cache_dir=cache_dir)
        if allow_remote
        else bundled_catalog()
    )
    if not catalog:
        catalog = bundled_catalog()
    if not catalog:
        return {
            "slug": "hermes-kody",
            "display_name": "Hermes Kody",
            "kind": "creature",
            "submitted_by": "community",
        }

    excluded = {str(s).strip() for s in (exclude or ()) if s}
    candidates = [entry for entry in catalog if entry["slug"] not in excluded]
    if not candidates:
        candidates = catalog

    chooser = rng.choice if rng is not None else random.choice
    chosen = chooser(candidates)
    return {
        "slug": chosen["slug"],
        "display_name": chosen["display_name"],
        "kind": chosen["kind"],
        "submitted_by": chosen.get("submitted_by", "community"),
    }


def _safe_child_path(base_dir: Path, filename: str) -> Optional[Path]:
    try:
        resolved_base = base_dir.resolve()
        candidate = (resolved_base / filename).resolve()
        if candidate.parent != resolved_base:
            return None
        return candidate
    except OSError:
        return None


def _mime_for_bytes(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return "image/webp"


def _build_sprite_payload(entry: dict[str, Any], data: bytes) -> dict[str, Any]:
    mime = _mime_for_bytes(data)
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "slug": entry["slug"],
        "display_name": entry.get("display_name") or entry["slug"],
        "kind": entry.get("kind") or "creature",
        "submitted_by": entry.get("submitted_by") or "community",
        "cols": COLS,
        "rows": ROWS,
        "frame_width": FRAME_WIDTH,
        "frame_height": FRAME_HEIGHT,
        "rows_map": dict(ROWS_MAP),
        "mime": mime,
        "data_url": f"data:{mime};base64,{b64}",
    }


def _fallback_bundled_sprite(seed_slug: str) -> dict[str, Any]:
    bundled = bundled_catalog()
    if bundled:
        digest = hashlib.sha256(str(seed_slug or "default").encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % len(bundled)
        for offset in range(len(bundled)):
            entry = bundled[(index + offset) % len(bundled)]
            for ext in (".webp", ".png"):
                path = _safe_child_path(PETS_DIR, f"{entry['slug']}{ext}")
                if path is not None and path.is_file():
                    try:
                        return _build_sprite_payload(entry, path.read_bytes())
                    except OSError:
                        continue

    # Ultimate emergency synthetic sheet if bundled files are missing on disk.
    img = Image.new("RGBA", (SHEET_WIDTH, SHEET_HEIGHT), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return _build_sprite_payload(
        {
            "slug": "hermes-kody",
            "display_name": "Hermes Kody",
            "kind": "creature",
            "submitted_by": "community",
        },
        buf.getvalue(),
    )


def get_pet_sprite(
    slug: str,
    *,
    allow_remote: bool = True,
    cache_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Resolve a pet spritesheet by ``slug`` with local caching and bundled fallback."""
    clean_slug = str(slug or "").strip()
    if not _is_valid_slug(clean_slug):
        return _fallback_bundled_sprite(clean_slug)

    bundled_by_slug = {entry["slug"]: entry for entry in bundled_catalog()}

    # 1. Check vendored bundle in assets/town/pets/<slug>.webp
    for ext in (".webp", ".png"):
        bundled_path = _safe_child_path(PETS_DIR, f"{clean_slug}{ext}")
        if bundled_path is not None and bundled_path.is_file():
            try:
                data = bundled_path.read_bytes()
                meta = bundled_by_slug.get(
                    clean_slug,
                    {
                        "slug": clean_slug,
                        "display_name": clean_slug.replace("-", " ").title(),
                        "kind": "creature",
                        "submitted_by": "community",
                    },
                )
                return _build_sprite_payload(meta, data)
            except OSError:
                break

    # 2. Check local cache directory if provided
    cache_pets_dir: Optional[Path] = None
    if cache_dir is not None:
        cache_pets_dir = Path(cache_dir) / "pets"
        cached_path = _safe_child_path(cache_pets_dir, f"{clean_slug}.webp")
        if cached_path is not None and cached_path.is_file():
            try:
                data = cached_path.read_bytes()
                manifest_map = {
                    e["slug"]: e
                    for e in (
                        fetch_remote_manifest(cache_dir=cache_dir)
                        if allow_remote
                        else bundled_catalog()
                    )
                }
                meta = manifest_map.get(
                    clean_slug,
                    {
                        "slug": clean_slug,
                        "display_name": clean_slug.replace("-", " ").title(),
                        "kind": "creature",
                        "submitted_by": "community",
                    },
                )
                return _build_sprite_payload(meta, data)
            except OSError:
                pass

    # 3. Fetch remote spritesheet if allowed
    if allow_remote:
        manifest = fetch_remote_manifest(cache_dir=cache_dir)
        remote_entry = next((e for e in manifest if e["slug"] == clean_slug), None)
        if remote_entry is not None:
            url = remote_entry.get("spritesheet_url") or ""
            if _is_valid_asset_url(url):
                try:
                    with _open_url(url, timeout=10) as resp:
                        raw_bytes = resp.read(MAX_SPRITE_BYTES + 1)
                    if 0 < len(raw_bytes) <= MAX_SPRITE_BYTES:
                        downsampled = downsample_sheet(raw_bytes)
                        if cache_pets_dir is not None:
                            try:
                                cache_pets_dir.mkdir(parents=True, exist_ok=True)
                                target = _safe_child_path(
                                    cache_pets_dir, f"{clean_slug}.webp"
                                )
                                if target is not None:
                                    target.write_bytes(downsampled)
                            except OSError:
                                pass
                        return _build_sprite_payload(remote_entry, downsampled)
                except Exception:
                    pass

    return _fallback_bundled_sprite(clean_slug)


def get_pets_bundle(
    slugs: Iterable[str],
    *,
    allow_remote: bool = True,
    cache_dir: Optional[Path] = None,
) -> dict[str, dict[str, Any]]:
    """Return resolved sprite payloads for up to 20 requested slugs."""
    result: dict[str, dict[str, Any]] = {}
    for raw_slug in slugs or ():
        if len(result) >= MAX_BUNDLE_SLUGS:
            break
        key = str(raw_slug or "").strip()
        if not key or key in result:
            continue
        result[key] = get_pet_sprite(
            key, allow_remote=allow_remote, cache_dir=cache_dir
        )
    return result
