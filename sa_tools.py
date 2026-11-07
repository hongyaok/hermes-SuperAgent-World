"""Tool handlers. Every handler returns a JSON string, never raises."""

from __future__ import annotations

import json
from typing import Any

try:
    from . import sa_chats, sa_engine, sa_runner, sa_runtime, sa_settings
except ImportError:  # top-level import (root __init__ loaded as `__init__` by pytest)
    import sa_chats  # type: ignore[no-redef]
    import sa_engine  # type: ignore[no-redef]
    import sa_runner  # type: ignore[no-redef]
    import sa_runtime  # type: ignore[no-redef]
    import sa_settings  # type: ignore[no-redef]


def _engine(ctx: Any) -> sa_engine.Engine:
    return sa_engine.build_engine(ctx)


def _chat_store() -> sa_chats.ChatStore:
    """The same database the desktop uses, resolved through sa_settings."""
    return sa_chats.ChatStore(sa_settings.data_dir() / "superagent.db")


def _error(message: Any) -> str:
    return json.dumps({"error": sa_runner.redact_secrets(message)})


def superagent_ask(args: dict, ctx: Any = None, **kwargs: Any) -> str:
    try:
        if ctx is None:
            return json.dumps({"error": "plugin context unavailable"})
        request = str(args.get("request") or "").strip()
        if not request:
            return json.dumps({"error": "request is required"})
        ticket = _engine(ctx).ask(request, requester=kwargs.get("platform") or "operator")
        return json.dumps({"ticket": sa_engine.Engine._ticket_brief(ticket), "ok": True})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def superagent_tick(args: dict, ctx: Any = None, **kwargs: Any) -> str:
    try:
        if ctx is None:
            return json.dumps({"error": "plugin context unavailable"})
        engine = _engine(ctx)
        result = engine.tick(wait=bool(args.get("wait", False)))
        status = engine.status()
        return json.dumps({"tick": result.as_dict(), "status": status}, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def superagent_status(args: dict, ctx: Any = None, **kwargs: Any) -> str:
    try:
        if ctx is None:
            return json.dumps({"error": "plugin context unavailable"})
        ticket_id = str(args.get("ticket_id") or "").strip() or None
        return json.dumps(_engine(ctx).status(ticket_id), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def superagent_run(args: dict, ctx: Any = None, **kwargs: Any) -> str:
    try:
        if ctx is None:
            return json.dumps({"error": "plugin context unavailable"})
        ticket_id = str(args.get("ticket_id") or "").strip()
        if not ticket_id:
            return json.dumps({"error": "ticket_id is required"})
        ticket = _engine(ctx).promote(ticket_id, wait=bool(args.get("wait", False)))
        return json.dumps(
            {
                "ok": True,
                "launched": bool(ticket.get("launched")),
                "ticket": sa_engine.Engine._ticket_brief(ticket),
            },
            default=str,
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def superagent_spawn(args: dict, ctx: Any = None, **kwargs: Any) -> str:
    """Create and launch one real worker session under a router town.

    This is the tool a root chat calls: it reuses ``ChatStore`` for the
    relationship and slot, and ``sa_runtime`` for the one process-wide
    ``Runner``. It never calls ``ctx.subagent_lifecycle`` and never invents a
    session: a failed launch marks the child failed and reports the error, so
    the caller cannot claim a worker that does not exist.
    """
    try:
        if ctx is None:
            return json.dumps({"error": "plugin context unavailable"})
        parent_id = str((args or {}).get("parent_session_id") or "").strip()
        prompt = str((args or {}).get("prompt") or "").strip()
        title = str((args or {}).get("title") or "").strip()
        if not parent_id:
            return json.dumps({"error": "parent_session_id is required"})
        if not sa_chats.is_session_id(parent_id):
            return json.dumps({"error": f"malformed parent session id: {parent_id!r}"})
        if not prompt:
            return json.dumps({"error": "prompt is required"})
        if len(prompt) > sa_runner.MAX_PROMPT_CHARS:
            return json.dumps(
                {"error": f"prompt is too long (max {sa_runner.MAX_PROMPT_CHARS} chars)"}
            )

        store = _chat_store()
        parent = store.get_session(parent_id, message_limit=0)
        if parent is None:
            return json.dumps({"error": f"unknown parent session {parent_id}"})
        if parent.get("parent_session_id") is not None:
            return json.dumps({"error": "workers cannot spawn workers"})
        if parent.get("archived_at") is not None:
            return json.dumps({"error": f"parent session {parent_id} is archived"})

        child = store.create_session(
            prompt,
            title=title,
            model=str(parent.get("model") or ""),
            provider=str(parent.get("provider") or ""),
            repo=str(parent.get("repo") or ""),
            source="worker",
            parent_session_id=parent_id,
        )
        child_id = str(child["id"])
        store.add_event(parent_id, "spawned", f"{child_id}: {child.get('title') or ''}")
        try:
            runner = sa_runtime.shared_runner(store)
            runner.launch_turn(
                child_id,
                prompt,
                model=str(parent.get("model") or ""),
                provider=str(parent.get("provider") or ""),
                repo=str(parent.get("repo") or ""),
            )
        except Exception as exc:
            message = sa_runner.redact_secrets(str(exc) or exc.__class__.__name__)
            store.update_session(child_id, status=sa_chats.FAILED, error=message)
            store.add_event(child_id, "failed", message)
            return json.dumps(
                {
                    "ok": False,
                    "error": message,
                    "session": store.get_session(child_id, message_limit=0),
                },
                default=str,
            )
        store.add_event(child_id, "launched", f"spawned by router {parent_id}")
        return json.dumps(
            {
                "ok": True,
                "parent_session_id": parent_id,
                "session": store.get_session(child_id, message_limit=0),
            },
            default=str,
        )
    except ValueError as exc:
        return _error(exc)
    except Exception as exc:
        return _error(exc)

