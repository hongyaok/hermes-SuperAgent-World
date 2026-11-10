"""Watch GitHub pull requests and issues, and turn mentions into sessions.

One poll is: fetch the open pull requests and the open issues, read each
thread's comments, and act only on what is new. The very first successful poll
for a repository is a *baseline* — it records every existing comment and PR
head as seen without creating anything, so connecting an old repository does
not dump 200 sessions on the user.

What can create a session:

* a comment that mentions the agent (`@login`, the short aliases `@sa` and
  `/sa`, `@superagent`, `/superagent`, or a `superagent:` line) from an
  allowed human, on a pull request *or* an issue; and
* a new PR head, when `github_watch_new_prs` is on, once per head SHA.

`github_watch_issues` (on by default) decides whether issues are polled at
all; with it off this is the PR-only watcher it has always been.

In `observe` mode sessions are recorded as `observed` and nothing launches; a
human promotes them. In `auto` mode each session is queued and launched: a PR
in an isolated `git worktree` of its head, an issue in a fresh worker worktree
off the configured clone.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Optional

try:  # normal: imported as part of the plugin package
    from . import sa_chats, sa_github, sa_runner, sa_settings
except ImportError:  # top-level import (dashboard backend, pytest)
    import sa_chats  # type: ignore[no-redef]
    import sa_github  # type: ignore[no-redef]
    import sa_runner  # type: ignore[no-redef]
    import sa_settings  # type: ignore[no-redef]

DEFAULT_POLL_SECONDS = 90
MIN_POLL_SECONDS = 5
# The PR list is fetched for `GET /github/status` too; cache it for a while so
# a UI polling the status endpoint does not spend the API budget.
STATUS_PR_CACHE_SECONDS = 60
MAX_EVENT_CHARS = sa_chats.MAX_EVENT_CHARS


def _redact(text: Any) -> str:
    """Credentials never reach the database, even in a failure message."""
    return sa_runner.redact_secrets(text)[:MAX_EVENT_CHARS]


def _field(settings: Any, key: str, default: Any = None) -> Any:
    """Read one setting from a dict or an object (tests pass either)."""
    if isinstance(settings, dict):
        return settings.get(key, default)
    return getattr(settings, key, default)


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


class GitHubWatcher:
    """Polls one GitHub repository and creates sessions for new work."""

    def __init__(
        self,
        store: Any,
        runner: Any,
        settings_getter: Optional[Any] = None,
        client_factory: Optional[Any] = None,
    ):
        self.store = store
        self.runner = runner
        self._settings = settings_getter or sa_settings.load_settings
        self._client_factory = client_factory or sa_github.GitHubClient

        self._poll_lock = threading.Lock()
        self._bg_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.last_poll: Optional[float] = None
        self.last_error: str = ""
        self.sessions_created: int = 0
        self._login: str = ""
        self._last_prs: list[dict[str, Any]] = []
        self._last_issues: list[dict[str, Any]] = []
        self._prs_at: float = 0.0

    # -- helpers ---------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        try:
            data = self._settings()
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    def _repo(self, settings: dict[str, Any]) -> str:
        return _text(_field(settings, "github_repo", ""))

    def _token(self, settings: dict[str, Any]) -> str:
        return _text(_field(settings, "github_token", ""))

    def _poll_seconds(self, settings: Optional[dict[str, Any]] = None) -> int:
        settings = settings if settings is not None else self.settings()
        try:
            seconds = int(_field(settings, "github_poll_seconds", DEFAULT_POLL_SECONDS))
        except (TypeError, ValueError):
            seconds = DEFAULT_POLL_SECONDS
        return max(MIN_POLL_SECONDS, seconds)

    def _limit(self, settings: dict[str, Any]) -> int:
        try:
            return int(_field(settings, "github_pr_limit", 10) or 10)
        except (TypeError, ValueError):
            return 10

    def _watch_issues(self, settings: dict[str, Any]) -> bool:
        """Issues are watched unless the operator turned them off."""
        return bool(_field(settings, "github_watch_issues", True))

    def _workspace(self, settings: dict[str, Any]) -> str:
        """Where isolated checkouts live: the setting, else the data dir."""
        return _text(_field(settings, "workspace_root", "")) or str(
            sa_settings.data_dir() / "workspaces"
        )

    def _client(self, settings: dict[str, Any]) -> Any:
        return self._client_factory(self._token(settings), self._repo(settings))

    def _login_for(self, settings: dict[str, Any], client: Any) -> str:
        """The agent's login: the configured one, else whoever the token is."""
        configured = _text(_field(settings, "github_agent_login", ""))
        if configured:
            return configured
        if not self._login:
            try:
                self._login = _text((client.me() or {}).get("login"))
            except Exception:
                return ""
        return self._login

    @staticmethod
    def _comment_key(repo: str, comment: dict[str, Any]) -> str:
        kind = comment.get("kind") or "issue"
        return f"gh_comment:{repo}:{kind}:{comment.get('id')}"

    @staticmethod
    def _head_key(repo: str, number: int, sha: str) -> str:
        return f"gh_head:{repo}:{number}:{sha}"

    @staticmethod
    def _baseline_key(repo: str) -> str:
        return f"gh_baseline:{repo}"

    # -- status ----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """A JSON-safe snapshot for the dashboard; never includes the token."""
        settings = self.settings()
        repo = self._repo(settings)
        token = self._token(settings)
        configured = bool(repo and token)
        watch_mode = _text(_field(settings, "github_watch_mode", "observe")) or "observe"
        watch_issues = self._watch_issues(settings)
        login = _text(_field(settings, "github_agent_login", "")) or self._login
        allowed_raw = _field(settings, "github_allowed_users", []) or []
        if isinstance(allowed_raw, str):
            allowed_raw = [part for part in allowed_raw.split(",") if part.strip()]
        allowed_users = [str(name).strip() for name in allowed_raw if str(name).strip()]

        prs: list[dict[str, Any]] = list(self._last_prs) if configured else []
        issues: list[dict[str, Any]] = list(self._last_issues) if configured else []
        if configured:
            fresh = time.time() - self._prs_at < STATUS_PR_CACHE_SECONDS
            if not fresh:
                client = self._client(settings)
                limit = self._limit(settings)
                try:
                    prs = client.list_open_prs(limit=limit)
                except Exception as exc:
                    self.last_error = _redact(str(exc))
                    prs = list(self._last_prs)
                if watch_issues:
                    try:
                        issues = client.list_open_issues(limit=limit)
                    except Exception as exc:
                        # A broken issue list still leaves the PR list usable.
                        self.last_error = _redact(str(exc))
                        issues = list(self._last_issues)
                else:
                    issues = []
                self._last_prs = prs
                self._last_issues = issues
                self._prs_at = time.time()
                if not login:
                    login = self._login_for(settings, client)

        with self._bg_lock:
            watching = bool(self._thread is not None and self._thread.is_alive())
        return {
            "configured": configured,
            "repo": repo,
            "login": login,
            "token_set": bool(token),
            "open_prs": prs,
            "open_issues": issues,
            "last_poll": self.last_poll,
            "last_error": self.last_error,
            "watching": watching,
            "watch_mode": watch_mode,
            "watch_new_prs": bool(_field(settings, "github_watch_new_prs", False)),
            "watch_issues": watch_issues,
            "allowed_users": allowed_users,
            "poll_seconds": self._poll_seconds(settings),
            "sessions_created": self.sessions_created,
        }

    # -- polling ---------------------------------------------------------

    def poll_once(self) -> dict[str, Any]:
        """One polling pass. Safe to call concurrently: extra calls are no-ops."""
        settings = self.settings()
        repo = self._repo(settings)
        token = self._token(settings)
        if not repo or not token:
            return {"configured": False, "polled": 0, "created": [], "errors": []}
        if not self._poll_lock.acquire(blocking=False):
            return {
                "configured": True,
                "polled": 0,
                "created": [],
                "errors": ["a poll is already running"],
            }
        try:
            return self._poll(settings, repo)
        finally:
            self._poll_lock.release()

    def _poll(self, settings: dict[str, Any], repo: str) -> dict[str, Any]:
        created: list[str] = []
        errors: list[str] = []
        client = self._client(settings)
        limit = self._limit(settings)

        try:
            prs = client.list_open_prs(limit=limit)
        except sa_github.GitHubError as exc:
            self.last_error = str(exc)
            self.last_poll = time.time()
            self.store.set_state(f"gh_last_poll:{repo}", str(self.last_poll))
            return {"configured": True, "polled": 0, "created": [], "errors": [str(exc)]}

        watch_issues = self._watch_issues(settings)
        issues: list[dict[str, Any]] = []
        if watch_issues:
            try:
                issues = client.list_open_issues(limit=limit)
                self._last_issues = list(issues)
            except Exception as exc:
                # An issue-list failure must not cost the user their PR work,
                # and must not blank the list the panel is showing. Skipping
                # issue work for this pass is the safe half: a stale list could
                # only re-scan threads the dedupe table already covers.
                errors.append(f"issues: {_redact(str(exc))}")
        else:
            self._last_issues = []

        # One list of (kind, thread) pairs, so the baseline, dedupe and trigger
        # rules below are written once rather than once per kind of thread.
        threads: list[tuple[str, dict[str, Any]]] = [("pr", pr) for pr in prs]
        threads += [("issue", issue) for issue in issues]

        self.last_error = ""
        self._last_prs = list(prs)
        self._prs_at = time.time()
        login = self._login_for(settings, client)
        baseline = not self.store.is_seen(self._baseline_key(repo))
        watch_new_prs = bool(_field(settings, "github_watch_new_prs", False))
        for kind, thread in threads:
            try:
                number = int(thread.get("number") or 0)
            except (TypeError, ValueError):
                continue
            if not number:
                continue
            label = "PR" if kind == "pr" else "issue"
            try:
                comments = (
                    client.list_comments(number)
                    if kind == "pr"
                    else client.list_issue_comments(number)
                )
            except Exception as exc:
                errors.append(f"{label} #{number}: {_redact(str(exc))}")
                continue

            head_sha = _text(thread.get("head_sha")) if kind == "pr" else ""
            head_key = self._head_key(repo, number, head_sha) if head_sha else ""
            head_is_new = bool(head_key) and not self.store.is_seen(head_key)

            if baseline:
                # Record, do not act: everything that exists now is old news.
                for comment in comments:
                    self.store.mark_seen(self._comment_key(repo, comment))
                if head_key:
                    self.store.mark_seen(head_key)
                continue

            for comment in comments:
                key = self._comment_key(repo, comment)
                if self.store.is_seen(key):
                    continue
                # Mark first: one malformed comment must not replay forever.
                self.store.mark_seen(key)
                if not self._allowed(settings, comment, login):
                    continue
                session_id = self._open_session(
                    settings, thread, comment, kind=kind, errors=errors
                )
                if session_id:
                    created.append(session_id)

            # Track the head every poll so turning the flag on later does not
            # replay every PR that accumulated while it was off.
            if head_key:
                self.store.mark_seen(head_key)
            if watch_new_prs and head_is_new:
                review = {
                    "kind": "review_request",
                    "author": login or "superagent",
                    "body": (
                        "Review this pull request: summarise the change, flag risks and"
                        " missing tests, and fix anything that is clearly broken."
                    ),
                }
                session_id = self._open_session(
                    settings, thread, review, kind="pr", errors=errors, review=True
                )
                if session_id:
                    created.append(session_id)

        if baseline and not errors:
            # Only after a complete pass: a failed comment fetch leaves the
            # baseline open so the next poll retries instead of treating that
            # PR's history as brand-new work.
            self.store.mark_seen(self._baseline_key(repo))

        self.last_error = "" if not errors else _redact(errors[0])
        self.last_poll = time.time()
        self.store.set_state(f"gh_last_poll:{repo}", str(self.last_poll))
        return {
            "configured": True,
            "polled": len(threads),
            "created": created,
            "errors": errors,
        }

    def _allowed(self, settings: dict[str, Any], comment: dict[str, Any], login: str) -> bool:
        """Is this comment a work request from an allowed human?"""
        author = _text(comment.get("author"))
        if not author:
            return False
        if login and author.lower() == login.lower():
            return False  # never react to ourselves
        if sa_github.is_bot(author):
            return False
        raw = _field(settings, "github_allowed_users", []) or []
        if isinstance(raw, str):
            raw = [part for part in raw.split(",") if part.strip()]
        allowed = [str(name).strip().lower() for name in raw]
        if not allowed:
            return False  # an empty allow-list means nobody, by design
        if author.lower() not in allowed:
            return False
        return sa_github.is_trigger(_text(comment.get("body")), login)

    # -- session creation ------------------------------------------------

    def _prompt_for(
        self,
        settings: dict[str, Any],
        thread: dict[str, Any],
        comment: dict[str, Any],
        *,
        kind: str,
        checkout: str,
    ) -> str:
        """The worker prompt for one PR or issue, checkout included."""
        if kind == "pr":
            return sa_github.build_pr_prompt(
                thread,
                comment=comment,
                checkout=checkout,
                can_create_pr=sa_settings.pr_create_allowed(settings, "github_pr"),
                can_approve_pr=bool(_field(settings, "allow_router_pr_approve", False)),
                can_merge=bool(_field(settings, "allow_worker_merge_pr", False)),
            )
        return sa_github.build_issue_prompt(
            thread,
            comment=comment,
            checkout=checkout,
            # An issue publishes its fix behind its own switch.
            can_create_pr=sa_settings.pr_create_allowed(settings, "github_issue"),
        )

    def _open_session(
        self,
        settings: dict[str, Any],
        thread: dict[str, Any],
        comment: dict[str, Any],
        *,
        errors: list[str],
        kind: str = "pr",
        review: bool = False,
    ) -> str:
        repo = self._repo(settings)
        number = int(thread.get("number") or 0)
        title = _text(thread.get("title"))
        is_pr = kind == "pr"
        label = f"PR #{number}" if is_pr else f"Issue #{number}"
        mode = _text(_field(settings, "github_watch_mode", "observe")) or "observe"
        auto = mode == "auto"
        repo_path = _text(_field(settings, "repo_path", ""))
        checkout = ""
        warning = ""

        if auto and repo_path and is_pr:
            try:
                info = sa_github.prepare_pr_checkout(
                    repo_path,
                    repo,
                    number,
                    self._workspace(settings),
                    self._token(settings),
                )
                checkout = str(info.get("path") or "")
            except sa_github.GitHubError as exc:
                warning = str(exc)

        # Remember where this thread lives so a later chat message resumes in
        # the checkout (or the clone), not in the runner's default directory.
        session_repo = checkout
        if not session_repo and repo_path and Path(repo_path).expanduser().is_dir():
            session_repo = repo_path

        prompt = self._prompt_for(settings, thread, comment, kind=kind, checkout=checkout)
        effort = sa_settings.normalize_reasoning_effort(
            _field(settings, "default_reasoning_effort", "")
        )
        session = self.store.create_session(
            prompt,
            title=f"{label}: {title}".strip(),
            model=_text(_field(settings, "default_model", "")),
            provider=_text(_field(settings, "default_provider", "")),
            reasoning_effort=effort,
            repo=session_repo,
            source="github_pr" if is_pr else "github_issue",
            github_repo=repo,
            pr_number=number if is_pr else None,
            pr_url=_text(thread.get("html_url")) if is_pr else None,
            issue_number=None if is_pr else number,
            status=sa_chats.QUEUED if auto else sa_chats.OBSERVED,
        )
        session_id = str(session["id"])
        self.sessions_created += 1

        if review:
            summary = f"new head {_text(thread.get('head_sha'))[:12]} on PR #{number}"
        else:
            body = _text(comment.get("body")).splitlines()
            first = " ".join(body[0].split()) if body else ""
            summary = f"@{comment.get('author')} on {label}: {first}"
        self.store.add_event(session_id, "github", summary[:MAX_EVENT_CHARS])

        if not auto:
            return session_id

        if repo_path and not is_pr:
            # An issue has no head to fetch, so its row already exists and the
            # isolated worktree is attached to it. The user may be mid-edit in
            # the clone; a worker must not share that working tree.
            try:
                worktree = sa_github.prepare_worker_worktree(
                    repo_path,
                    session_id,
                    self._workspace(settings),
                    title=title,
                )
                isolated = str(worktree.get("path") or "")
                if isolated:
                    session_repo = isolated
                    prompt = self._prompt_for(
                        settings, thread, comment, kind=kind, checkout=isolated
                    )
                    self.store.update_session(
                        session_id,
                        prompt=prompt,
                        repo=isolated,
                        worktree_path=isolated,
                        branch_name=str(worktree.get("branch") or ""),
                    )
            except sa_github.GitHubError as exc:
                warning = str(exc)

        if warning:
            self.store.add_event(session_id, "github", f"checkout failed: {warning}")
        target = session_repo
        if not target:
            message = warning or "no local repository is configured for GitHub work"
            self._fail(session_id, message)
            errors.append(message)
            return session_id

        try:
            self.runner.launch_turn(
                session_id,
                prompt,
                model=_text(_field(settings, "default_model", "")) or None,
                provider=_text(_field(settings, "default_provider", "")) or None,
                repo=target,
            )
        except Exception as exc:
            message = f"could not launch worker: {exc}"
            self._fail(session_id, message)
            errors.append(message)
        return session_id

    def _fail(self, session_id: str, message: str) -> None:
        message = _redact(message) or "github worker failed"
        self.store.update_session(
            session_id, status=sa_chats.FAILED, error=message, finished_at=time.time(), pid=None
        )
        self.store.add_event(session_id, "failed", message)

    # -- background ------------------------------------------------------

    def start_background(self) -> bool:
        """Start the polling thread once. Returns True when a thread started."""
        with self._bg_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="superagent-github-watcher", daemon=True
            )
            self._thread.start()
            return True

    def stop_background(self) -> None:
        with self._bg_lock:
            thread = self._thread
            self._thread = None
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(self._poll_seconds()):
                break
            try:
                self.poll_once()
            except Exception as exc:  # never let the thread die noisily
                self.last_error = _redact(str(exc))
