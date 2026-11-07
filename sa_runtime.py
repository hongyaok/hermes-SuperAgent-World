"""One Runner per database, shared by the dashboard and the spawn tool.

The dashboard imports ``dashboard/plugin_api.py`` standalone while the plugin's
tools are registered through the package; both run in the gateway process. If
each built its own Runner, ``max_workers`` would be enforced twice and a router
could exceed the configured cap by spawning through the tool. This module is
the single owner of that process-wide pool.

It is deliberately small: construction from the merged settings, a
path-keyed registry, and a reset hook for a settings save. Tests replace
``shared_runner`` (or seed the registry) rather than spawning a real process.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Optional

try:  # normal: imported as part of the plugin package
    from . import sa_runner, sa_settings
except ImportError:  # top-level import (gateway, pytest)
    import sa_runner  # type: ignore[no-redef]
    import sa_settings  # type: ignore[no-redef]

_RUNNERS: dict[str, Any] = {}
_LOCK = threading.Lock()
# Historical names, kept so the dashboard and its tests can share one pool.
RUNNERS = _RUNNERS
LOCK = _LOCK


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    items = [part.strip() for part in raw.split(",") if part.strip()]
    return items or list(default)


def runner_root(repo: str) -> str:
    """Allowed roots for a Runner: the clone plus the plugin's PR workspaces."""
    primary = (
        os.environ.get("SUPERAGENT_REPO_ROOT") or repo or os.environ.get("SUPERAGENT_REPO") or ""
    ).strip()
    if not primary:
        return ""
    try:
        workspaces = str(sa_settings.data_dir() / "workspaces")
    except Exception:
        workspaces = ""
    return os.pathsep.join(dict.fromkeys(part for part in (primary, workspaces) if part))


def build_runner(store: Any, settings: Optional[dict[str, Any]] = None) -> Any:
    """Build a Runner with the same defaults the dashboard has always used."""
    settings = settings if settings is not None else sa_settings.load_settings()
    repo = str(settings.get("repo_path") or "")
    return sa_runner.Runner(
        store,
        hermes_bin=os.environ.get("SUPERAGENT_HERMES_BIN") or None,
        max_workers=int(settings.get("max_workers") or 2),
        toolsets=tuple(_env_list("SUPERAGENT_WORKER_TOOLSETS", ["file", "terminal"])),
        router_toolset="superagent",
        yolo=bool(settings.get("worker_yolo", True)),
        model=str(settings.get("default_model") or ""),
        provider=str(settings.get("default_provider") or ""),
        repo=repo,
        repo_root=runner_root(repo),
        max_turns=int(settings.get("chat_max_turns") or 120),
        turn_timeout_seconds=_env_int(
            "SUPERAGENT_TURN_TIMEOUT_SECONDS", sa_runner.DEFAULT_TURN_TIMEOUT_SECONDS
        ),
    )


def shared_runner(store: Any, settings: Optional[dict[str, Any]] = None) -> Any:
    """Return the process-wide Runner for ``store``, creating it once."""
    key = str(getattr(store, "path", "") or "")
    with _LOCK:
        runner = _RUNNERS.get(key)
        if runner is None:
            runner = build_runner(store, settings)
            _RUNNERS[key] = runner
        return runner


def reset_runner(path: Optional[str] = None) -> None:
    """Drop one Runner (or every Runner) after a settings change."""
    with _LOCK:
        if path is None:
            _RUNNERS.clear()
        else:
            _RUNNERS.pop(str(path), None)
