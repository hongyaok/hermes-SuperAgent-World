"""Which models a headless worker may run, and how the host says yes.

Hermes refuses an explicit ``-m`` override for a data-training tier (Meta's
``…-contributor`` ids) when the run has no terminal to confirm on, unless
``config.yaml`` carries ``security.allow_data_training_tiers_noninteractive:
true``. Every Superagent worker is headless, so a contributor model used to
launch, die about two seconds later with "Model override cancelled", and leave
a failed house whose only explanation was the vendor's own warning text.

This module is the one place that answers "is this a data-training tier?" and
"did the host opt in?", so the dashboard, the plugin tools and the CLI refuse
identically and *before* a session is created, instead of launching a worker
that cannot start.

The check is deliberately the same rule Hermes applies (a ``contributor``
dash-token in the model id, no provider test) and the same literal ``True``
test on the config value: a stricter or looser copy here would disagree with
the child process, which is the only opinion that matters. The host config is
read through Hermes' own ``load_config()`` when it is importable, because the
CLI ships as a frozen binary that does not bundle PyYAML.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable

# The key Hermes itself reads in `_confirm_startup_expensive_model_override`.
CONFIG_KEY = "security.allow_data_training_tiers_noninteractive"
CONFIG_SECTION = "security"
CONFIG_LEAF = "allow_data_training_tiers_noninteractive"
# What a user would type to arrive at the same state without the panel.
SET_COMMAND = f"hermes config set {CONFIG_KEY} true"
# Hermes matches "contributor" as a dash-separated token of the id.
TIER_TOKEN = "contributor"

# One indented `leaf: value` line, for the no-PyYAML fallback.
_LEAF_LINE = re.compile(
    r"^\s+" + re.escape(CONFIG_LEAF) + r"\s*:\s*(?P<value>[^#]*?)\s*(?:#.*)?$"
)


def is_data_training_tier(model: str) -> bool:
    """True for a tier whose vendor trains on prompts (``…-contributor``).

    Matched on the id alone, exactly like Hermes: the tier is a property of the
    model name, so a relay or custom endpoint serving the same id is treated
    the same way.
    """
    parts = [part for part in str(model or "").strip().lower().split("-") if part]
    return TIER_TOKEN in parts


def host_config_path() -> Path:
    """The ``config.yaml`` the launched ``hermes chat`` child will read."""
    # Relative first: inside the plugin package that resolves to the very
    # module object the gateway and the tests patched, not a second copy
    # imported under a top-level name.
    try:
        from .sa_settings import _hermes_home
    except ImportError:  # loaded standalone (dashboard backend, pytest)
        from sa_settings import _hermes_home
    return Path(_hermes_home()) / "config.yaml"


def _scan_config_text(text: str) -> bool:
    """Read the one key out of raw YAML text, without a YAML parser.

    The CLI ships as a frozen binary that does not bundle PyYAML, so a hard
    ``import yaml`` would report "blocked" on a machine that had opted in.
    Handles the shape Hermes writes — an indented ``leaf: true`` inside a
    top-level ``security:`` block — and only the literal ``true`` counts,
    because the child compares with ``is True``.
    """
    in_section = False
    value = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[:1].isspace() and stripped.endswith(":"):
            in_section = stripped[:-1].strip() == CONFIG_SECTION
            continue
        if not in_section:
            continue
        match = _LEAF_LINE.match(line)
        if match:
            # Quotes stay: Hermes compares the loaded value with `is True`, so
            # `'true'` (a string) must not read as the opt-in.
            value = match.group("value").strip().lower() == "true"
    return value


def _section_value(section: Any) -> bool:
    """The literal-True test Hermes applies to the loaded section."""
    return bool(isinstance(section, dict) and section.get(CONFIG_LEAF) is True)


def _read_host_state(path: Path | None) -> tuple[bool, str]:
    """(allowed, why-not) for the host opt-in.

    With no explicit path this asks Hermes' own loader first — the same call
    the startup guard makes — so the answer cannot drift from the child's. An
    explicit path (tests) is read directly.
    """
    if path is None:
        try:
            from hermes_cli.config import load_config

            config = load_config()
            if isinstance(config, dict):
                return _section_value(config.get(CONFIG_SECTION)), ""
        except Exception:
            pass  # not importable outside Hermes, or the file is invalid

    target = path or host_config_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, "missing"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    try:
        import yaml

        data = yaml.safe_load(text) or {}
        section = data.get(CONFIG_SECTION) if isinstance(data, dict) else None
        return _section_value(section), ""
    except ImportError:  # frozen CLI: no PyYAML in the bundle
        return _scan_config_text(text), ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def host_allows_data_tiers(path: Path | None = None) -> bool:
    """True when the host already allows data tiers without a terminal.

    A missing file, unreadable YAML or a string ``"true"`` all read as False:
    Hermes compares the value with ``is True``, so anything else would let this
    check pass while the child refuses.
    """
    return _read_host_state(path)[0]


def host_state(path: Path | None = None) -> dict[str, Any]:
    """Everything a surface needs to explain the gate, in one read."""
    allowed, error = _read_host_state(path)
    target = path or host_config_path()
    return {
        "allowed": allowed,
        "config_path": str(target),
        "config_exists": target.is_file(),
        "error": "" if allowed else error,
        "set_command": SET_COMMAND,
    }


def blocked_message(model: str) -> str:
    """One sentence a user can act on, naming the key and the trade-off."""
    name = str(model or "").strip() or "(unknown model)"
    return (
        f"{name} is a data-training ('contributor') tier model. Workers run headless,"
        " so Hermes refuses it unless the host opts in with"
        f" {CONFIG_KEY}: true in config.yaml (or run: {SET_COMMAND})."
        " Enabling it lets the model vendor train on worker prompts;"
        " Settings -> Contributor model tiers does the same thing from the panel."
        " The standard variant without the -contributor suffix needs no opt-in."
    )


def check_model(model: str, *, allowed: bool | None = None) -> None:
    """Raise ``ValueError`` when *model* cannot start as a headless worker.

    ``allowed`` lets a caller that already read the host state skip a second
    file read; ``None`` reads it now.
    """
    if not is_data_training_tier(model):
        return
    if allowed is None:
        allowed = host_allows_data_tiers()
    if not allowed:
        raise ValueError(blocked_message(model))


def set_host_allowance(
    allowed: bool, *, run: Callable[..., Any] | None = None
) -> dict[str, Any]:
    """Write the host opt-in through ``hermes config set``, never by hand.

    Rewriting ``config.yaml`` here would drop the user's comments and could
    disagree with Hermes' own writer, so the host's command does the edit. The
    caller gets the *observed* state back, not the requested one, so a write
    that silently did nothing is visible instead of reported as success.
    """
    runner = run or subprocess.run
    try:
        from .sa_runner import resolve_hermes_bin
    except ImportError:  # loaded standalone (dashboard backend, pytest)
        from sa_runner import resolve_hermes_bin
    binary = resolve_hermes_bin("")
    argv = [
        binary,
        "config",
        "set",
        CONFIG_KEY,
        "true" if allowed else "false",
    ]
    result = runner(argv, capture_output=True, text=True, timeout=60)
    if getattr(result, "returncode", 1) != 0:
        tail = str(
            getattr(result, "stderr", "") or getattr(result, "stdout", "") or ""
        ).strip()
        raise ValueError(
            f"could not write {CONFIG_KEY}: {tail[-300:] or 'hermes config set failed'}"
        )
    state = host_state()
    return {"allowed": bool(state["allowed"]), "path": state["config_path"]}
