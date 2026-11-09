"""Read-only access to the vendored Superagent Towns sprites.

A Hermes desktop plugin has no static-file mount, and the renderer's REST door
returns JSON, so the sprite bytes are exposed twice from one explicit
allowlist: a binary route for direct use and a manifest of data URLs that works
over local and remote gateways alike. There is no path parameter that reaches
the filesystem: a name is looked up in :data:`ASSETS`, and only then is the
matching file read.

The artwork is third-party. Its licences are recorded in
``assets/town/ATTRIBUTION.md`` and are separate from the plugin's MIT licence.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "town"
PNG = "image/png"

# Explicit allowlist. A new sprite is a deliberate code change, not a file a
# request can name.
ASSETS: dict[str, str] = {
    "grass.png": PNG,
    "grass-b.png": PNG,
    "grass-c.png": PNG,
    "house-router.png": PNG,
    "house-a.png": PNG,
    "house-b.png": PNG,
    "house-c.png": PNG,
    "house-d.png": PNG,
    "house-e.png": PNG,
    "house-f.png": PNG,
    "house-g.png": PNG,
    "person.png": PNG,
    "tree-a.png": PNG,
    "tree-b.png": PNG,
    "tree-c.png": PNG,
    "bush.png": PNG,
    "fountain.png": PNG,
    "rock.png": PNG,
    "lamp.png": PNG,
    "road-h.png": PNG,
    "road-v.png": PNG,
    "plaza-stone.png": PNG,
    "water.png": PNG,
    "bridge.png": PNG,
    "well.png": PNG,
    "bulletin-board.png": PNG,
    "signpost.png": PNG,
    "bench.png": PNG,
    "flowers.png": PNG,
    "plot-empty.png": PNG,
}


class UnknownAsset(KeyError):
    """The requested sprite is not in the allowlist."""


def _path_for(name: str) -> Path:
    mime = ASSETS.get(str(name))
    if mime is None:
        raise UnknownAsset(name)
    path = (ASSETS_DIR / str(name)).resolve()
    # The allowlist already makes traversal impossible; this is the second
    # lock, so a future edit to ASSETS cannot accidentally point outside.
    if path.parent != ASSETS_DIR.resolve():
        raise UnknownAsset(name)
    return path


@lru_cache(maxsize=len(ASSETS))
def read(name: str) -> tuple[bytes, str]:
    """Return ``(bytes, mime)`` for one allowlisted sprite."""
    path = _path_for(name)
    return path.read_bytes(), ASSETS[str(name)]


@lru_cache(maxsize=1)
def manifest() -> dict[str, dict[str, str]]:
    """Every sprite as a data URL, in one small JSON document.

    A base64 payload is deliberate: ``ctx.rest`` is the only supported fetch
    path for a plugin, and a remote gateway may not expose the binary route to
    the renderer. The images are a few kilobytes each; the page fetches this
    once per mount and reuses the same strings for every house.
    """
    assets: dict[str, dict[str, str]] = {}
    for name, mime in ASSETS.items():
        data, actual_mime = read(name)
        assets[name] = {
            "mime": actual_mime,
            "size": str(len(data)),
            "data_url": f"data:{actual_mime};base64,{base64.b64encode(data).decode('ascii')}",
        }
    return assets
