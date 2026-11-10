"""Post a finished turn's summary back to GitHub, when the operator allows it.

The manual flow already produces this text: ``sa_diff.default_comment_body`` is
exactly what the **Comment on PR** panel previews. Reusing it here keeps the
automatic reply and the human-confirmed one identical, so a reply can never say
something the preview would not have said.

Two deliberate limits:

* **One reply per session, not per turn.** A follow-up message would otherwise
  re-post a near-identical comment on the same thread, which reads as noise to
  everyone watching the repository.
* **Best effort only.** This runs at the end of a worker turn, so it never
  raises and never touches the session's status; a failed reply becomes a
  ``reply_failed`` event, not a failed turn.

Nothing here commits, pushes or merges.
"""

from __future__ import annotations

import os
from typing import Any, Optional

try:  # normal: imported as part of the plugin package
    from . import sa_diff, sa_github, sa_settings
except ImportError:  # top-level import (dashboard backend, pytest)
    import sa_diff  # type: ignore[no-redef]
    import sa_github  # type: ignore[no-redef]
    import sa_settings  # type: ignore[no-redef]

# Which GitHub threads a reply can go to. The session's `source` is the marker.
REPLY_SOURCES = ("github_pr", "github_issue")
# One comment per session; the key lives in the same `gh_seen` table as the
# comment dedupe, under its own prefix so a re-baseline never clears it.
REPLY_KEY = "gh_reply:{session_id}"


def reply_key(session_id: str) -> str:
    return REPLY_KEY.format(session_id=str(session_id or "").strip())


def thread_number(session: dict[str, Any] | None) -> Optional[int]:
    """The PR or issue number a session belongs to, if any."""
    data = session or {}
    for key in ("pr_number", "issue_number"):
        try:
            value = int(data.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _token(settings: dict[str, Any]) -> str:
    token = str(settings.get("github_token") or "").strip()
    if token:
        return token
    for name in ("SUPERAGENT_GITHUB_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def reply_after_turn(
    store: Any,
    session_id: str,
    *,
    settings: Optional[dict[str, Any]] = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    """Post the session's summary on its issue/PR when `github_reply_on_finish` is on.

    Returns ``{posted, reason|number|url, error}``. Never raises: the caller is
    the end of a worker turn.
    """
    try:
        current = settings if isinstance(settings, dict) else sa_settings.load_settings()
    except Exception as exc:
        return {"posted": False, "error": str(exc)}

    if not bool(current.get("github_reply_on_finish")):
        return {"posted": False, "reason": "github_reply_on_finish is off"}

    session = store.get_session(session_id)
    if not session:
        return {"posted": False, "reason": "unknown session"}
    if str(session.get("source") or "") not in REPLY_SOURCES:
        return {"posted": False, "reason": "not a GitHub thread"}
    number = thread_number(session)
    if number is None:
        return {"posted": False, "reason": "the session has no issue or PR number"}

    key = reply_key(session_id)
    try:
        if store.is_seen(key):
            return {"posted": False, "reason": "already replied"}
    except Exception:
        pass

    repo = str(session.get("github_repo") or current.get("github_repo") or "").strip()
    token = _token(current)
    if not repo or not token:
        return {"posted": False, "reason": "GitHub is not connected"}

    body = sa_diff.default_comment_body(session, None, limit=sa_github.BODY_LIMIT)
    if not body.strip():
        return {"posted": False, "reason": "the reply body is empty"}

    try:
        factory = client_factory or sa_github.GitHubClient
        client = factory(token, repo)
        posted = client.post_comment(int(number), body)
    except Exception as exc:
        message = sa_github._redact(str(exc) or exc.__class__.__name__, token)
        try:
            store.add_event(session_id, "reply_failed", message)
        except Exception:
            pass
        return {"posted": False, "error": message}

    url = str((posted or {}).get("html_url") or "")
    try:
        store.mark_seen(key)
        store.add_event(
            session_id, "replied", f"comment on #{number}" + (f" {url}" if url else "")
        )
    except Exception:
        pass
    return {"posted": True, "number": int(number), "url": url}
