"""Run the Node town render probe as part of the Python suite.

The probe rewrites the three import specifiers to stubs and renders the real
desktop tree to text. It is the cheapest way to catch a ReferenceError or a
stale-transcript regression without launching Electron.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "towns_render_probe.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_town_render_probe():
    result = subprocess.run(
        [shutil.which("node") or "node", str(PROBE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "town render probe: ok" in result.stdout
