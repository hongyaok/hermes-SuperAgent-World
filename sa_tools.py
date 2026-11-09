"""Tool handlers. Every handler returns a JSON string, never raises."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from . import (
        sa_chats,
        sa_diff,
        sa_engine,
        sa_github,
        sa_model_policy,
        sa_runner,
        sa_runtime,
        sa_settings,
    )
except ImportError:  # top-level import (root __init__ loaded as `__init__` by pytest)
    import sa_chats  # type: ignore[no-redef]
    import sa_diff  # type: ignore[no-redef]
    import sa_engine  # type: ignore[no-redef]
    import sa_github  # type: ignore[no-redef]
    import sa_model_policy  # type: ignore[no-redef]
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
        raw_pr_number = (args or {}).get("pr_number")
        use_worktree_arg = (args or {}).get("use_worktree")
        model_arg = str((args or {}).get("model") or "").strip()
        provider_arg = str((args or {}).get("provider") or "").strip()
        effort_arg = str((args or {}).get("reasoning_effort") or "").strip()
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
        if len(model_arg) > 200 or len(provider_arg) > 200:
            return json.dumps({"error": "model/provider is too long (max 200 chars)"})
        try:
            effort_arg = sa_settings.normalize_reasoning_effort(effort_arg, strict=True)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        pr_number: int | None = None
        if raw_pr_number is not None and str(raw_pr_number).strip() != "":
            try:
                pr_number = int(raw_pr_number)
            except (TypeError, ValueError):
                return json.dumps({"error": f"invalid pr_number: {raw_pr_number!r}"})

        store = _chat_store()
        parent = store.get_session(parent_id, message_limit=0)
        if parent is None:
            return json.dumps({"error": f"unknown parent session {parent_id}"})
        if parent.get("parent_session_id") is not None:
            return json.dumps({"error": "workers cannot spawn workers"})
        if parent.get("archived_at") is not None:
            return json.dumps({"error": f"parent session {parent_id} is archived"})

        settings = sa_settings.load_settings()
        # Precedence per field: explicit tool argument > worker setting >
        # parent/router session value. Workers are often cheaper and faster
        # than the router, and a single spawn may still need a one-off model.
        model = (
            model_arg
            or str(settings.get("worker_model") or "").strip()
            or str(parent.get("model") or "").strip()
        )
        provider = (
            provider_arg
            or str(settings.get("worker_provider") or "").strip()
            or str(parent.get("provider") or "").strip()
        )
        effort = (
            effort_arg
            or sa_settings.normalize_reasoning_effort(settings.get("worker_reasoning_effort"))
            or sa_settings.normalize_reasoning_effort(parent.get("reasoning_effort"))
        )
        parent_repo = str(parent.get("repo") or "").strip()
        base_repo = parent_repo or str(settings.get("repo_path") or "").strip()
        gh_repo = str(parent.get("github_repo") or settings.get("github_repo") or "").strip() or None
        pr_url = (
            f"https://github.com/{gh_repo}/pull/{pr_number}"
            if (pr_number and gh_repo)
            else None
        )

        # A worker runs headless, so a data-training tier is refused before the
        # row exists; the router relays the reason instead of spawning a house
        # that can never start. Check the resolved model, not the parent's:
        # the worker setting or a per-spawn override may name the tier.
        try:
            sa_model_policy.check_model(model)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

        child = store.create_session(
            prompt,
            title=title,
            model=model,
            provider=provider,
            reasoning_effort=effort,
            repo=parent_repo,
            source="worker",
            github_repo=gh_repo,
            pr_number=pr_number,
            pr_url=pr_url,
            parent_session_id=parent_id,
            # The town setting is the control; there is deliberately no
            # per-spawn argument, so a router cannot silently opt a worker out
            # of the cheap, predictable harness.
            harness=sa_settings.normalize_harness(settings.get("worker_harness")),
        )
        child_id = str(child["id"])
        worker_repo = parent_repo
        workspace_root = str(sa_settings.data_dir() / "workspaces")
        want_worktree = (
            bool(use_worktree_arg)
            if use_worktree_arg is not None
            else bool(settings.get("allow_worker_git_worktree", True))
        )
        if pr_number and base_repo and (Path(base_repo) / ".git").exists() and gh_repo:
            try:
                co = sa_github.prepare_pr_checkout(
                    base_repo,
                    gh_repo,
                    pr_number,
                    workspace_root,
                    str(settings.get("github_token") or ""),
                )
                worker_repo = str(co["path"])
                store.update_session(
                    child_id,
                    repo=worker_repo,
                    worktree_path=worker_repo,
                    branch_name=str(co.get("branch_ref") or ""),
                )
            except Exception as exc:
                store.add_event(
                    child_id,
                    "worktree_fallback",
                    sa_runner.redact_secrets(f"PR checkout fallback: {exc}"),
                )
        elif want_worktree and base_repo and (Path(base_repo) / ".git").exists():
            try:
                wt = sa_github.prepare_worker_worktree(
                    base_repo,
                    child_id,
                    workspace_root,
                    title=str(child.get("title") or title),
                )
                worker_repo = str(wt["path"])
                store.update_session(
                    child_id,
                    repo=worker_repo,
                    worktree_path=worker_repo,
                    branch_name=str(wt.get("branch") or ""),
                )
            except Exception as exc:
                store.add_event(
                    child_id,
                    "worktree_fallback",
                    sa_runner.redact_secrets(f"worktree fallback: {exc}"),
                )

        store.add_event(parent_id, "spawned", f"{child_id}: {child.get('title') or ''}")
        try:
            runner = sa_runtime.shared_runner(store)
            runner.launch_turn(
                child_id,
                prompt,
                model=model,
                provider=provider,
                reasoning_effort=effort,
                repo=worker_repo,
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


def superagent_town_status(args: dict, ctx: Any = None, **kwargs: Any) -> str:
    """Return the status of a router town and all of its workers."""
    try:
        if ctx is None:
            return json.dumps({"error": "plugin context unavailable"})
        parent_id = str((args or {}).get("parent_session_id") or "").strip()
        include_diff = bool((args or {}).get("include_diff", True))
        if not parent_id:
            return json.dumps({"error": "parent_session_id is required"})
        if not sa_chats.is_session_id(parent_id):
            return json.dumps({"error": f"malformed parent session id: {parent_id!r}"})

        store = _chat_store()
        parent = store.get_session(parent_id, message_limit=10)
        if parent is None:
            return json.dumps({"error": f"unknown parent session {parent_id}"})
        if parent.get("parent_session_id") is not None:
            return json.dumps({"error": f"session {parent_id} is a worker, not a router town"})

        towns_payload = store.list_towns(limit=500, archived="all")
        town_row = next(
            (t for t in towns_payload.get("towns", []) if t.get("id") == parent_id),
            None,
        )
        raw_workers = (town_row or {}).get("workers", [])
        workers_summary: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        for w in raw_workers:
            wid = str(w.get("id") or "")
            full_w = store.get_session(wid, message_limit=20, event_limit=10) or w
            st = str(full_w.get("status") or "")
            status_counts[st] = status_counts.get(st, 0) + 1
            item: dict[str, Any] = {
                "id": wid,
                "title": full_w.get("title"),
                "status": st,
                "town_slot": w.get("town_slot"),
                "avatar_slug": full_w.get("avatar_slug"),
                "repo": full_w.get("repo"),
                "worktree_path": full_w.get("worktree_path"),
                "branch_name": full_w.get("branch_name"),
                "pr_number": full_w.get("pr_number"),
                "pr_url": full_w.get("pr_url"),
                "last_event": full_w.get("last_event"),
                "last_assistant_message": sa_diff.last_assistant_text(full_w)[:600],
            }
            if include_diff and full_w.get("repo"):
                diff = sa_diff.collect_diff(str(full_w.get("repo")), max_patch_chars=2000)
                item["diff"] = {
                    "ok": bool(diff.get("ok")),
                    "file_count": len(diff.get("files") or []),
                    "files": (diff.get("files") or [])[:30],
                    "stat": diff.get("stat") or "",
                }
            workers_summary.append(item)

        return json.dumps(
            {
                "ok": True,
                "parent_session_id": parent_id,
                "town": {
                    "id": parent["id"],
                    "title": parent.get("title"),
                    "status": parent.get("status"),
                    "avatar_slug": parent.get("avatar_slug"),
                    "repo": parent.get("repo"),
                    "worker_count": len(workers_summary),
                    "worker_status_counts": status_counts,
                },
                "workers": workers_summary,
            },
            default=str,
        )
    except Exception as exc:
        return _error(exc)


def superagent_message_worker(args: dict, ctx: Any = None, **kwargs: Any) -> str:
    """Send a follow-up instruction from a router town to one of its workers."""
    try:
        if ctx is None:
            return json.dumps({"error": "plugin context unavailable"})
        parent_id = str((args or {}).get("parent_session_id") or "").strip()
        worker_id = str((args or {}).get("worker_session_id") or "").strip()
        message = str((args or {}).get("message") or "").strip()
        action = str((args or {}).get("action") or "follow_up").strip().lower()
        raw_pr_number = (args or {}).get("pr_number")

        if not parent_id or not sa_chats.is_session_id(parent_id):
            return json.dumps({"error": "valid parent_session_id is required"})
        if not worker_id or not sa_chats.is_session_id(worker_id):
            return json.dumps({"error": "valid worker_session_id is required"})
        if not message:
            return json.dumps({"error": "message is required"})
        if action not in ("follow_up", "create_pr", "review_pr", "approve_pr"):
            return json.dumps(
                {"error": f"action must be follow_up, create_pr, review_pr, or approve_pr (got {action!r})"}
            )

        settings = sa_settings.load_settings()
        if action in ("approve_pr", "review_pr") and not settings.get("allow_router_pr_approve"):
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        "PR approval/review by agents is disabled. "
                        "Enable allow_router_pr_approve in Superagent Settings."
                    ),
                }
            )
        if action == "create_pr" and not settings.get("allow_worker_pr_create"):
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        "PR creation by workers is disabled. "
                        "Enable allow_worker_pr_create in Superagent Settings."
                    ),
                }
            )

        store = _chat_store()
        parent = store.get_session(parent_id, message_limit=0)
        if parent is None:
            return json.dumps({"error": f"unknown parent session {parent_id}"})
        if parent.get("parent_session_id") is not None:
            return json.dumps({"error": "only a root router session may message workers"})

        worker = store.get_session(worker_id, message_limit=0)
        if worker is None:
            return json.dumps({"error": f"unknown worker session {worker_id}"})
        if worker.get("parent_session_id") != parent_id:
            return json.dumps(
                {"error": f"worker {worker_id} does not belong to town {parent_id}"}
            )
        if worker.get("status") in (sa_chats.QUEUED, sa_chats.RUNNING):
            return json.dumps(
                {
                    "ok": False,
                    "error": f"worker {worker_id} is currently {worker.get('status')}; wait for its turn to finish",
                }
            )

        pr_number = raw_pr_number if raw_pr_number is not None else worker.get("pr_number")
        if action == "create_pr":
            formatted = (
                f"[Router instruction from {parent_id} — Create Pull Request]\n"
                f"{message}\n\n"
                f'When ready, call superagent_pr_create with session_id="{worker_id}".'
            )
        elif action in ("review_pr", "approve_pr"):
            verdict_hint = "APPROVE" if action == "approve_pr" else "APPROVE, REQUEST_CHANGES, or COMMENT"
            pr_part = f", pr_number={int(pr_number)}" if pr_number else ""
            formatted = (
                f"[Router instruction from {parent_id} — {action.replace('_', ' ').title()}]\n"
                f"{message}\n\n"
                f'Submit your review using superagent_pr_review(session_id="{worker_id}"{pr_part}, event="{verdict_hint}", body="...").'
            )
        else:
            formatted = f"[Router follow-up from {parent_id}]\n{message}"

        store.add_message(worker_id, "user", formatted)
        store.add_event(parent_id, "router_message", f"-> {worker_id} ({action}): {message[:120]}")
        store.add_event(worker_id, "router_message", f"from {parent_id} ({action}): {message[:120]}")

        try:
            runner = sa_runtime.shared_runner(store)
            runner.launch_turn(
                worker_id,
                formatted,
                resume_session_id=str(worker.get("hermes_session_id") or "") or None,
                model=str(worker.get("model") or ""),
                provider=str(worker.get("provider") or ""),
                reasoning_effort=str(worker.get("reasoning_effort") or ""),
                repo=str(worker.get("repo") or ""),
            )
        except Exception as exc:
            err_msg = sa_runner.redact_secrets(str(exc) or exc.__class__.__name__)
            store.update_session(worker_id, status=sa_chats.FAILED, error=err_msg)
            store.add_event(worker_id, "failed", err_msg)
            return json.dumps(
                {
                    "ok": False,
                    "error": err_msg,
                    "session": store.get_session(worker_id, message_limit=0),
                },
                default=str,
            )

        return json.dumps(
            {
                "ok": True,
                "parent_session_id": parent_id,
                "worker_session_id": worker_id,
                "action": action,
                "session": store.get_session(worker_id, message_limit=0),
            },
            default=str,
        )
    except Exception as exc:
        return _error(exc)


def superagent_pr_create(args: dict, ctx: Any = None, **kwargs: Any) -> str:
    """Commit, push a worker branch, and open a GitHub pull request."""
    try:
        if ctx is None:
            return json.dumps({"error": "plugin context unavailable"})
        settings = sa_settings.load_settings()
        if not settings.get("allow_worker_pr_create"):
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        "PR creation by workers is disabled. "
                        "Enable allow_worker_pr_create in Superagent Settings."
                    ),
                }
            )
        session_id = str((args or {}).get("session_id") or "").strip()
        title = str((args or {}).get("title") or "").strip()
        body = str((args or {}).get("body") or "").strip()
        explicit_branch = str((args or {}).get("branch") or "").strip()
        explicit_base = str((args or {}).get("base") or "").strip()
        commit_message = str((args or {}).get("commit_message") or "").strip() or title

        if not session_id or not sa_chats.is_session_id(session_id):
            return json.dumps({"ok": False, "error": "valid session_id is required"})
        if not title:
            return json.dumps({"ok": False, "error": "title is required"})

        store = _chat_store()
        session = store.get_session(session_id, message_limit=0)
        if session is None:
            return json.dumps({"ok": False, "error": f"unknown session {session_id}"})

        repo_path = str(
            session.get("worktree_path")
            or session.get("repo")
            or settings.get("repo_path")
            or ""
        ).strip()
        github_repo = str(
            session.get("github_repo") or settings.get("github_repo") or ""
        ).strip()
        token = str(settings.get("github_token") or "").strip()
        if not repo_path:
            return json.dumps({"ok": False, "error": "no repository path configured for session"})
        if not github_repo or "/" not in github_repo:
            return json.dumps({"ok": False, "error": "no GitHub repository (owner/name) configured"})
        if not token:
            return json.dumps({"ok": False, "error": "no GitHub token configured"})

        branch = sa_github.sanitize_branch_name(
            session_id,
            title=title,
            explicit=explicit_branch or str(session.get("branch_name") or ""),
        )
        push_info = sa_github.commit_and_push_branch(
            repo_path,
            github_repo,
            token,
            branch=branch,
            commit_message=commit_message,
        )

        client = sa_github.GitHubClient(token, github_repo)
        base = explicit_base or str(settings.get("github_pr_base_branch") or "").strip()
        if not base:
            try:
                base = str(client.repo_info().get("default_branch") or "").strip() or "main"
            except Exception:
                base = "main"
        draft_arg = (args or {}).get("draft")
        draft = (
            bool(draft_arg)
            if draft_arg is not None
            else bool(settings.get("github_pr_draft_by_default", False))
        )

        pr = client.create_pr(title=title, head=branch, base=base, body=body, draft=draft)
        updated = store.update_session(
            session_id,
            pr_number=pr.get("number"),
            pr_url=pr.get("html_url"),
            github_repo=github_repo,
            branch_name=branch,
        )
        detail = f"PR #{pr.get('number')} ({branch} -> {base}) {pr.get('html_url') or ''}".strip()
        store.add_event(session_id, "pr_created", detail)
        if session.get("parent_session_id"):
            store.add_event(str(session["parent_session_id"]), "pr_created", f"{session_id}: {detail}")

        return json.dumps(
            {
                "ok": True,
                "pr": pr,
                "branch": branch,
                "push": push_info,
                "session": updated,
            },
            default=str,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": sa_runner.redact_secrets(exc)})


def superagent_pr_review(args: dict, ctx: Any = None, **kwargs: Any) -> str:
    """Submit a pull request review (APPROVE, REQUEST_CHANGES, or COMMENT)."""
    try:
        if ctx is None:
            return json.dumps({"error": "plugin context unavailable"})
        session_id = str((args or {}).get("session_id") or "").strip()
        event = str((args or {}).get("event") or "COMMENT").strip().upper()
        body = str((args or {}).get("body") or "").strip()
        raw_pr_number = (args or {}).get("pr_number")

        if not session_id or not sa_chats.is_session_id(session_id):
            return json.dumps({"ok": False, "error": "valid session_id is required"})
        if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
            return json.dumps(
                {
                    "ok": False,
                    "error": f"event must be APPROVE, REQUEST_CHANGES, or COMMENT (got {event!r})",
                }
            )

        settings = sa_settings.load_settings()
        if event in ("APPROVE", "REQUEST_CHANGES") and not settings.get("allow_router_pr_approve"):
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        "PR approval/review verdict by agents is disabled. "
                        "Enable allow_router_pr_approve in Superagent Settings."
                    ),
                }
            )

        store = _chat_store()
        session = store.get_session(session_id, message_limit=0)
        if session is None:
            return json.dumps({"ok": False, "error": f"unknown session {session_id}"})

        pr_val = raw_pr_number if raw_pr_number is not None else session.get("pr_number")
        try:
            pr_number = int(pr_val)
        except (TypeError, ValueError):
            return json.dumps({"ok": False, "error": "pr_number is required"})

        github_repo = str(
            session.get("github_repo") or settings.get("github_repo") or ""
        ).strip()
        token = str(settings.get("github_token") or "").strip()
        if not github_repo or "/" not in github_repo:
            return json.dumps({"ok": False, "error": "no GitHub repository (owner/name) configured"})
        if not token:
            return json.dumps({"ok": False, "error": "no GitHub token configured"})

        client = sa_github.GitHubClient(token, github_repo)
        review = client.create_review(pr_number, event=event, body=body)
        detail = f"PR #{pr_number} {review.get('state') or event}"
        store.add_event(session_id, "pr_reviewed", detail)
        if session.get("parent_session_id"):
            store.add_event(str(session["parent_session_id"]), "pr_reviewed", f"{session_id}: {detail}")

        return json.dumps(
            {
                "ok": True,
                "pr_number": pr_number,
                "review": review,
                "session_id": session_id,
            },
            default=str,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": sa_runner.redact_secrets(exc)})

