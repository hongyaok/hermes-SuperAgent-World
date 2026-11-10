"""A tiny GitHub REST client for the watcher (stdlib only, no `gh` CLI).

Only what the plugin needs: the authenticated login, one repository, its open
pull requests, their comments, and posting a comment back. Everything goes
through `GitHubClient._call`, which adds the headers once and turns an HTTP or
network failure into a `GitHubError` with a message safe to show a user (no
token, ever).

`prepare_pr_checkout` is the other half: it fetches `refs/pull/<n>/head` from
GitHub into the local clone and creates a detached `git worktree` for it, so a
worker can edit the PR without disturbing the user's checkout.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

API_ROOT = "https://api.github.com"
USER_AGENT = "superagent-plugin"
API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT = 20
GIT_TIMEOUT = 120
BODY_LIMIT = 4000
MAX_PROMPT_BODY = 1500
MAX_PROMPT_COMMENT = 2000

Handler = Callable[[list[str], str, int], tuple[int, str, str]]


class GitHubError(Exception):
    """A GitHub or git failure with the HTTP status when there is one."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(str(message))
        self.status = int(status or 0)


# The module-level handle lets tests replace the network call without touching
# urllib globally.
_urlopen = urllib.request.urlopen


def _redact(text: Any, token: str = "") -> str:
    """Remove the token and any Authorization header from a message."""
    value = "" if text is None else str(text)
    if token:
        value = value.replace(token, "[redacted]")
    return re.sub(
        r"(?i)(authorization\s*:\s*(?:basic|bearer|token)\s+)\S+", r"\1[redacted]", value
    )


def _tail(text: Any, limit: int = 600) -> str:
    value = ("" if text is None else str(text)).strip()
    return value[-limit:] if len(value) > limit else value


class GitHubClient:
    """Minimal REST client bound to one repository."""

    def __init__(self, token: str, repo: str, timeout: int = DEFAULT_TIMEOUT):
        self.token = (token or "").strip()
        self.repo = (repo or "").strip()
        self.timeout = int(timeout or DEFAULT_TIMEOUT)

    # -- transport -------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return API_ROOT + "/" + path.lstrip("/")

    def _call(self, method: str, path: str, payload: Optional[dict] = None) -> Any:
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._url(path), data=data, headers=headers, method=method.upper()
        )
        try:
            with _urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            message = ""
            try:
                body = exc.read().decode("utf-8", "replace")
                parsed = json.loads(body) if body.strip() else {}
                if isinstance(parsed, dict):
                    message = str(parsed.get("message") or "")
                if not message:
                    message = body.strip()[:300]
            except Exception:
                message = ""
            detail = _redact(message or exc.reason, self.token)
            raise GitHubError(f"GitHub {exc.code}: {detail}", exc.code) from None
        except urllib.error.URLError as exc:
            raise GitHubError(f"cannot reach GitHub: {_redact(exc.reason, self.token)}") from None
        except (OSError, ValueError) as exc:
            raise GitHubError(f"GitHub request failed: {_redact(exc, self.token)}") from None
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise GitHubError("GitHub returned a response that is not JSON") from None

    def _repo_path(self, suffix: str) -> str:
        if not self.repo:
            raise GitHubError("no GitHub repository configured")
        base = f"repos/{self.repo}"
        return f"{base}/{suffix.lstrip('/')}" if suffix else base

    # -- identity and repository ----------------------------------------

    def me(self) -> dict[str, Any]:
        data = self._call("GET", "/user") or {}
        return {"login": str(data.get("login") or ""), "id": data.get("id")}

    def repo_info(self) -> dict[str, Any]:
        data = self._call("GET", self._repo_path("")) or {}
        return {
            "full_name": str(data.get("full_name") or self.repo),
            "default_branch": str(data.get("default_branch") or ""),
            "private": bool(data.get("private")),
            "open_issues": int(data.get("open_issues") or 0),
        }

    # -- pull requests ---------------------------------------------------

    def _normalize_pr(self, item: dict) -> dict[str, Any]:
        head = item.get("head") if isinstance(item.get("head"), dict) else {}
        base = item.get("base") if isinstance(item.get("base"), dict) else {}
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        return {
            "number": item.get("number"),
            "title": str(item.get("title") or ""),
            "body": str(item.get("body") or ""),
            "author": str(user.get("login") or ""),
            "state": str(item.get("state") or ""),
            "head_ref": str(head.get("ref") or ""),
            "head_sha": str(head.get("sha") or ""),
            "base_ref": str(base.get("ref") or ""),
            "html_url": str(item.get("html_url") or ""),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
            "draft": bool(item.get("draft")),
            # Only `GET /pulls/{n}` fills these; the list endpoint leaves them
            # absent, so a merge decision always re-reads the PR first.
            "mergeable": item.get("mergeable"),
            "mergeable_state": str(item.get("mergeable_state") or ""),
            "merged": bool(item.get("merged")),
            "repo": self.repo,
        }

    def list_open_prs(self, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 10), 100))
        query = urllib.parse.urlencode(
            {"state": "open", "sort": "updated", "direction": "desc", "per_page": limit}
        )
        data = self._call("GET", self._repo_path(f"pulls?{query}")) or []
        if not isinstance(data, list):
            return []
        return [self._normalize_pr(item) for item in data if isinstance(item, dict)]

    def get_pr(self, number: int) -> dict[str, Any]:
        data = self._call("GET", self._repo_path(f"pulls/{int(number)}")) or {}
        if not isinstance(data, dict) or not data:
            raise GitHubError(f"pull request #{number} was not found", 404)
        return self._normalize_pr(data)

    def create_pr(
        self,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> dict[str, Any]:
        clean_title = str(title or "").strip()[:256]
        clean_head = str(head or "").strip()
        clean_base = str(base or "").strip()
        if not clean_title:
            raise GitHubError("pull request title is required")
        if not clean_head:
            raise GitHubError("pull request head branch is required")
        if not clean_base:
            raise GitHubError("pull request base branch is required")
        payload = {
            "title": clean_title,
            "head": clean_head,
            "base": clean_base,
            "body": str(body or "")[:BODY_LIMIT],
            "draft": bool(draft),
        }
        data = self._call("POST", self._repo_path("pulls"), payload) or {}
        if not isinstance(data, dict) or not data:
            raise GitHubError("GitHub did not return a pull request object")
        return self._normalize_pr(data)

    def create_review(
        self,
        number: int,
        event: str = "COMMENT",
        body: str = "",
        *,
        fallback_on_self_approve: bool = True,
    ) -> dict[str, Any]:
        verdict = str(event or "COMMENT").strip().upper()
        if verdict not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
            raise GitHubError(
                f"review event must be APPROVE, REQUEST_CHANGES, or COMMENT (got {event!r})"
            )
        clean_body = str(body or "")[:BODY_LIMIT]
        payload = {"event": verdict, "body": clean_body}
        try:
            data = self._call("POST", self._repo_path(f"pulls/{int(number)}/reviews"), payload) or {}
        except GitHubError as exc:
            if (
                exc.status == 422
                and verdict in ("APPROVE", "REQUEST_CHANGES")
                and fallback_on_self_approve
            ):
                fallback_body = f"**[Superagent {verdict}]**\n\n{clean_body}".strip()
                comment = self.post_comment(int(number), fallback_body)
                return {
                    "id": comment.get("id"),
                    "state": f"{verdict}_COMMENT_FALLBACK",
                    "html_url": str(comment.get("html_url") or ""),
                    "fallback": True,
                }
            raise
        return {
            "id": data.get("id") if isinstance(data, dict) else None,
            "state": str((data.get("state") if isinstance(data, dict) else None) or verdict),
            "html_url": str((data.get("html_url") if isinstance(data, dict) else None) or ""),
            "fallback": False,
        }

    def merge_pr(
        self,
        number: int,
        *,
        merge_method: str = "merge",
        commit_title: str = "",
        commit_message: str = "",
    ) -> dict[str, Any]:
        """Merge a pull request and report what GitHub actually did.

        GitHub answers 200 for a merge that did not happen (``merged: false``)
        and 405/409/422 for one it refused, so the caller must read the body
        rather than the status. Branch protection, "checks are failing" and
        "head branch was modified" all arrive here as a `GitHubError` carrying
        the status, which is what the tool reports back to the model.
        """
        method = str(merge_method or "merge").strip().lower()
        if method not in ("merge", "squash", "rebase"):
            raise GitHubError(
                f"merge_method must be merge, squash, or rebase (got {merge_method!r})"
            )
        payload: dict[str, Any] = {"merge_method": method}
        clean_title = str(commit_title or "").strip()
        clean_message = str(commit_message or "").strip()
        if clean_title:
            payload["commit_title"] = clean_title[:256]
        if clean_message:
            payload["commit_message"] = clean_message[:BODY_LIMIT]
        data = self._call("PUT", self._repo_path(f"pulls/{int(number)}/merge"), payload) or {}
        if not isinstance(data, dict):
            raise GitHubError("GitHub returned an unexpected merge response")
        return {
            "merged": bool(data.get("merged")),
            "sha": str(data.get("sha") or ""),
            "message": str(data.get("message") or ""),
        }

    # -- issues ----------------------------------------------------------

    def _normalize_issue(self, item: dict) -> dict[str, Any]:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        labels = [
            str(label.get("name") or "")
            for label in (item.get("labels") or [])
            if isinstance(label, dict)
        ]
        return {
            "number": item.get("number"),
            "title": str(item.get("title") or ""),
            "body": str(item.get("body") or ""),
            "author": str(user.get("login") or ""),
            "state": str(item.get("state") or ""),
            "labels": [name for name in labels if name],
            "comment_count": int(item.get("comments") or 0),
            "html_url": str(item.get("html_url") or ""),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
            "repo": self.repo,
        }

    def list_open_issues(self, limit: int = 10) -> list[dict[str, Any]]:
        """Open issues only — pull requests never come back from here.

        GitHub has no "issues but not pull requests" filter on this endpoint:
        every PR *is* an issue and arrives with a `pull_request` key. So the
        page is fetched slightly larger and the PRs are dropped before the
        slice, which keeps the caller's `limit` meaning "issues".
        """
        limit = max(1, min(int(limit or 10), 100))
        per_page = min(100, limit * 2 + 20)
        query = urllib.parse.urlencode(
            {"state": "open", "sort": "updated", "direction": "desc", "per_page": per_page}
        )
        data = self._call("GET", self._repo_path(f"issues?{query}")) or []
        if not isinstance(data, list):
            return []
        issues = [
            self._normalize_issue(item)
            for item in data
            if isinstance(item, dict) and not item.get("pull_request")
        ]
        return issues[:limit]

    def get_issue(self, number: int) -> dict[str, Any]:
        data = self._call("GET", self._repo_path(f"issues/{int(number)}")) or {}
        if not isinstance(data, dict) or not data:
            raise GitHubError(f"issue #{number} was not found", 404)
        if data.get("pull_request"):
            # "/issues/7" answers for a PR too; refuse rather than let a PR be
            # handled as an issue with no diff to read.
            raise GitHubError(f"#{number} is a pull request, not an issue", 422)
        return self._normalize_issue(data)

    # -- comments --------------------------------------------------------

    def _normalize_comment(self, item: dict, kind: str) -> dict[str, Any]:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        return {
            "id": item.get("id"),
            "kind": kind,
            "author": str(user.get("login") or ""),
            "body": str(item.get("body") or ""),
            "html_url": str(item.get("html_url") or ""),
            "path": item.get("path") or "",
            "line": item.get("line") or item.get("original_line"),
            "created_at": str(item.get("created_at") or ""),
        }

    def list_comments(self, number: int) -> list[dict[str, Any]]:
        """Issue comments and review comments, merged and oldest first."""
        number = int(number)
        issue = self._call("GET", self._repo_path(f"issues/{number}/comments?per_page=100")) or []
        review = self._call("GET", self._repo_path(f"pulls/{number}/comments?per_page=100")) or []
        items: list[dict[str, Any]] = []
        for kind, rows in (("issue", issue), ("review", review)):
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, dict):
                    items.append(self._normalize_comment(row, kind))
        # ISO-8601 timestamps sort lexicographically; ids break ties (GitHub
        # allocates them monotonically) so two same-second comments keep order.
        items.sort(key=lambda item: (item["created_at"], item["id"] or 0))
        return items

    def list_issue_comments(self, number: int) -> list[dict[str, Any]]:
        """Conversation comments on an issue, oldest first.

        Deliberately not `list_comments`: that also asks for
        `/pulls/<n>/comments`, which 404s for an issue. An issue has no diff,
        so there are no review comments to merge in.
        """
        rows = self._call(
            "GET", self._repo_path(f"issues/{int(number)}/comments?per_page=100")
        ) or []
        items = [
            self._normalize_comment(row, "issue")
            for row in rows
            if isinstance(row, dict)
        ] if isinstance(rows, list) else []
        items.sort(key=lambda item: (item["created_at"], item["id"] or 0))
        return items

    def post_comment(self, number: int, body: str) -> dict[str, Any]:
        payload = {"body": str(body or "")[:BODY_LIMIT]}
        data = self._call("POST", self._repo_path(f"issues/{int(number)}/comments"), payload) or {}
        return {
            "id": data.get("id"),
            "html_url": str(data.get("html_url") or ""),
            "created_at": str(data.get("created_at") or ""),
        }


# -- triggers ------------------------------------------------------------

# The short aliases, `@sa` and `/sa`, anywhere in a body. Both lookarounds
# matter: without them `@sample` would trigger, and so would the `/sa` in
# `docs/sa.md`, because the missing rule is "the alias must stand alone".
_SHORT_TRIGGER = re.compile(r"(?<![\w-])[@/]sa(?![\w-])", re.IGNORECASE)


def is_trigger(body: str, login: str = "") -> bool:
    """True when a comment asks Superagent to do something.

    Accepted forms, anywhere in the body: `@<login>`, the short aliases `@sa`
    and `/sa`, `@superagent`, `/superagent`, or a line that starts with
    `superagent:`. Case-insensitive; an empty body never triggers.
    """
    text = str(body or "").strip()
    if not text:
        return False
    lowered = text.lower()
    login = (login or "").strip().lower()
    if login and f"@{login}" in lowered:
        return True
    if _SHORT_TRIGGER.search(text):
        return True
    if "@superagent" in lowered or "/superagent" in lowered:
        return True
    return any(line.strip().lower().startswith("superagent:") for line in lowered.splitlines())


def is_bot(login: str) -> bool:
    """GitHub Apps end in `[bot]`; the common service accounts are known."""
    name = (login or "").strip().lower()
    if not name:
        return False
    if name.endswith("[bot]"):
        return True
    return name in ("github-actions", "dependabot")


# -- checkout ------------------------------------------------------------


def _default_git_runner(argv: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
    proc = subprocess.run(
        argv,
        cwd=cwd or None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def prepare_pr_checkout(
    repo_path: str,
    github_repo: str,
    number: int,
    workspace_root: str,
    token: str,
    git_runner: Optional[Handler] = None,
) -> dict[str, Any]:
    """Fetch a PR's head into `repo_path` and check it out as a worktree.

    Returns ``{path, reused, branch_ref}``. The clone is only ever used as the
    object store; the work happens in `<workspace_root>/pr-<number>`, detached
    at the fetched ref, so the user's checkout and branch are untouched.
    """
    number = int(number)
    source = Path(str(repo_path or "")).expanduser()
    if not (source / ".git").exists():
        raise GitHubError(f"not a git repository: {repo_path}")

    workspace = Path(str(workspace_root or "")).expanduser()
    dest = workspace / f"pr-{number}"
    ref = f"refs/superagent/pr-{number}"
    if (dest / ".git").exists():
        return {"path": str(dest), "reused": True, "branch_ref": ref}
    if dest.exists():
        raise GitHubError(f"checkout path exists and is not a git checkout: {dest}")
    workspace.mkdir(parents=True, exist_ok=True)

    run = git_runner or _default_git_runner
    auth = base64.b64encode(f"x-access-token:{token or ''}".encode("utf-8")).decode("ascii")
    fetch_argv = [
        "git",
        "-C",
        str(source),
        "-c",
        f"http.extraHeader=Authorization: Basic {auth}",
        "fetch",
        "--no-tags",
        f"https://github.com/{github_repo}.git",
        f"+refs/pull/{number}/head:{ref}",
    ]
    try:
        code, _out, err = run(fetch_argv, str(source), GIT_TIMEOUT)
    except Exception as exc:
        detail = _tail(_redact(exc, token)) or exc.__class__.__name__
        raise GitHubError(f"could not fetch pull request #{number}: {detail}") from None
    if code != 0:
        detail = _tail(_redact(err, token)) or f"git exited with code {code}"
        raise GitHubError(f"could not fetch pull request #{number}: {detail}")

    worktree_argv = ["git", "-C", str(source), "worktree", "add", "--detach", str(dest), ref]
    try:
        code, _out, err = run(worktree_argv, str(source), GIT_TIMEOUT)
    except Exception as exc:
        detail = _tail(_redact(exc, token)) or exc.__class__.__name__
        raise GitHubError(f"could not create a checkout for pull request #{number}: {detail}") from None
    if code != 0:
        detail = _tail(_redact(err, token)) or f"git exited with code {code}"
        raise GitHubError(f"could not create a checkout for pull request #{number}: {detail}")

    return {"path": str(dest), "reused": False, "branch_ref": ref}


_PROTECTED_BRANCHES = {"main", "master", "head", "develop", "production"}
_VALID_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def sanitize_branch_name(session_id: str, title: str = "", explicit: str = "") -> str:
    """Return a safe git branch name for a worker, rejecting protected branches."""
    raw = str(explicit or "").strip()
    if raw:
        if (
            ".." in raw
            or raw.startswith(("/", ".", "-"))
            or raw.endswith(("/", ".", ".lock"))
            or "//" in raw
            or not _VALID_BRANCH_RE.match(raw)
        ):
            raise GitHubError(f"invalid git branch name: {raw!r}")
        if raw.lower() in _PROTECTED_BRANCHES:
            raise GitHubError(f"refusing to use protected branch: {raw}")
        return raw

    sid = re.sub(r"[^a-z0-9-]+", "-", str(session_id or "worker").strip().lower()).strip("-") or "worker"
    slug = re.sub(r"[^a-z0-9]+", "-", str(title or "").strip().lower()).strip("-")[:40].strip("-")
    return f"superagent/{sid}-{slug}" if slug else f"superagent/{sid}"


def prepare_worker_worktree(
    repo_path: str,
    session_id: str,
    workspace_root: str,
    *,
    branch: str = "",
    title: str = "",
    base_ref: str = "HEAD",
    git_runner: Optional[Handler] = None,
) -> dict[str, Any]:
    """Create an isolated git worktree and branch for a spawned worker session."""
    source = Path(str(repo_path or "")).expanduser()
    if not (source / ".git").exists():
        raise GitHubError(f"not a git repository: {repo_path}")

    branch_name = sanitize_branch_name(session_id, title=title, explicit=branch)
    sid_clean = re.sub(r"[^a-z0-9-]+", "-", str(session_id or "worker").strip().lower()).strip("-") or "worker"
    workspace = Path(str(workspace_root or "")).expanduser()
    dest = workspace / f"worker-{sid_clean}"
    if (dest / ".git").exists():
        return {"path": str(dest), "branch": branch_name, "reused": True}
    if dest.exists():
        raise GitHubError(f"worktree path exists and is not a git checkout: {dest}")
    workspace.mkdir(parents=True, exist_ok=True)

    run = git_runner or _default_git_runner
    argv = [
        "git",
        "-C",
        str(source),
        "worktree",
        "add",
        "-B",
        branch_name,
        str(dest),
        str(base_ref or "HEAD").strip() or "HEAD",
    ]
    try:
        code, _out, err = run(argv, str(source), GIT_TIMEOUT)
    except Exception as exc:
        detail = _tail(_redact(exc)) or exc.__class__.__name__
        raise GitHubError(f"could not create worker worktree for {session_id}: {detail}") from None
    if code != 0:
        detail = _tail(_redact(err)) or f"git exited with code {code}"
        raise GitHubError(f"could not create worker worktree for {session_id}: {detail}")

    return {"path": str(dest), "branch": branch_name, "reused": False}


def commit_and_push_branch(
    repo_path: str,
    github_repo: str,
    token: str,
    *,
    branch: str,
    commit_message: str,
    git_runner: Optional[Handler] = None,
) -> dict[str, Any]:
    """Stage, commit (if dirty), and push ``branch`` to GitHub with redacted errors."""
    source = Path(str(repo_path or "")).expanduser()
    if not (source / ".git").exists():
        raise GitHubError(f"not a git repository: {repo_path}")
    clean_repo = str(github_repo or "").strip()
    if not clean_repo or "/" not in clean_repo:
        raise GitHubError("no GitHub repository configured")
    clean_token = str(token or "").strip()
    if not clean_token:
        raise GitHubError("no GitHub token configured")
    branch_name = sanitize_branch_name("", explicit=branch)
    message = str(commit_message or "").strip() or f"Superagent changes on {branch_name}"
    run = git_runner or _default_git_runner

    def _exec(argv: list[str], err_prefix: str) -> str:
        try:
            code, out, err = run(argv, str(source), GIT_TIMEOUT)
        except Exception as exc:
            detail = _tail(_redact(exc, clean_token)) or exc.__class__.__name__
            raise GitHubError(f"{err_prefix}: {detail}") from None
        if code != 0:
            detail = _tail(_redact(err or out, clean_token)) or f"git exited with code {code}"
            raise GitHubError(f"{err_prefix}: {detail}")
        return out

    status_out = _exec(
        ["git", "-C", str(source), "status", "--porcelain=v1"],
        "could not inspect git status",
    )
    _exec(
        ["git", "-C", str(source), "checkout", "-B", branch_name],
        f"could not switch to branch {branch_name}",
    )
    committed = False
    if status_out.strip():
        _exec(
            ["git", "-C", str(source), "add", "-A"],
            "could not stage changes",
        )
        _exec(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Superagent",
                "-c",
                "user.email=superagent@users.noreply.github.com",
                "commit",
                "-m",
                message,
            ],
            "could not commit changes",
        )
        committed = True

    auth = base64.b64encode(f"x-access-token:{clean_token}".encode("utf-8")).decode("ascii")
    _exec(
        [
            "git",
            "-C",
            str(source),
            "-c",
            f"http.extraHeader=Authorization: Basic {auth}",
            "push",
            "https://github.com/" + clean_repo + ".git",
            f"HEAD:refs/heads/{branch_name}",
        ],
        f"could not push branch {branch_name}",
    )
    sha_out = _exec(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        "could not resolve HEAD sha",
    )
    return {
        "branch": branch_name,
        "sha": sha_out.strip(),
        "committed": committed,
        "pushed": True,
    }


# -- prompt --------------------------------------------------------------


def build_pr_prompt(
    pr: dict[str, Any],
    comment: Optional[dict[str, Any]] = None,
    checkout: str = "",
    *,
    can_create_pr: bool = False,
    can_approve_pr: bool = False,
    can_merge: bool = False,
) -> str:
    """The worker prompt for one PR: context, the ask, and the ground rules."""
    pr = pr or {}
    number = pr.get("number")
    title = str(pr.get("title") or "").strip()
    repo = str(pr.get("repo") or "").strip()
    author = str(pr.get("author") or "").strip()
    if not author:
        user = pr.get("user") if isinstance(pr.get("user"), dict) else {}
        author = str(user.get("login") or "").strip()
    base = str(pr.get("base_ref") or "").strip()
    head = str(pr.get("head_ref") or "").strip()
    if not base:
        base = str((pr.get("base") or {}).get("ref") if isinstance(pr.get("base"), dict) else "").strip()
    if not head:
        head = str((pr.get("head") or {}).get("ref") if isinstance(pr.get("head"), dict) else "").strip()
    base = base or "main"
    body = str(pr.get("body") or "").strip()

    lines = [f"Work on GitHub pull request #{number}" + (f" in {repo}." if repo else "."), ""]
    lines.append(f"PR #{number}: {title}".rstrip())
    if pr.get("html_url"):
        lines.append(f"URL: {pr['html_url']}")
    lines.append(f"Author: {author or 'unknown'}")
    lines.append(f"Branches: {head or '?'} -> {base or '?'}")
    if body:
        clipped = body if len(body) <= MAX_PROMPT_BODY else body[:MAX_PROMPT_BODY] + "…"
        lines += ["", "Description:", clipped]

    if comment:
        c_author = str(comment.get("author") or "someone").strip() or "someone"
        c_body = str(comment.get("body") or "").strip() or "(no text)"
        if len(c_body) > MAX_PROMPT_COMMENT:
            c_body = c_body[:MAX_PROMPT_COMMENT] + "…"
        lines += ["", f"Triggering comment from @{c_author}:", c_body]

    lines += ["", "Checkout:"]
    if checkout:
        lines += [
            f"An isolated git worktree for this PR is at: {checkout}",
            "Work only inside that directory; do not touch the original clone.",
        ]
    else:
        lines.append("No isolated checkout was prepared; work in the repository you were given.")

    if can_create_pr:
        commit_rule = (
            "- Do NOT push directly to main/master or merge. To publish changes as a pull request"
            " when asked, use the superagent_pr_create tool; otherwise leave changes in the working tree."
        )
    else:
        commit_rule = (
            "- Do NOT commit, push, merge, or open/update a pull request."
            " Leave changes in the working tree for a human."
        )

    lines += [
        "",
        "What to do:",
        f"- Inspect what this PR changes: `git diff origin/{base}...HEAD`"
        " (fetch the base branch first if that ref is missing).",
        "- Make the smallest fix that addresses the request; follow the repository's existing style.",
        "- Run the relevant tests or checks. If there are none, say what you checked instead.",
        commit_rule,
    ]
    if can_approve_pr:
        lines.append(
            "- When asked to review or approve this pull request, use the superagent_pr_review tool."
        )
    if can_merge:
        lines.append(
            "- When this pull request is sound — it does what it claims, the checks you ran pass,"
            " and nothing else is obviously broken — write your verdict as the `body` of one"
            " superagent_pr_merge call and let it post that comment and merge. Never merge a draft,"
            " a failing change, or one you would ask a question about: say why and leave it open."
        )
    lines += [
        "- If the request needs a design decision, ask one concrete question rather than guessing.",
        "- Finish with a summary of at most 200 words: what changed, files touched,"
        " checks run, and anything unresolved.",
    ]
    return "\n".join(lines)


def build_issue_prompt(
    issue: dict[str, Any],
    comment: Optional[dict[str, Any]] = None,
    checkout: str = "",
    *,
    can_create_pr: bool = False,
) -> str:
    """The worker prompt for one issue: context, the ask, and the ground rules.

    An issue has no diff to review, so this asks for the fix the comment
    requests; `build_pr_prompt` is the reviewing shape.
    """
    issue = issue or {}
    number = issue.get("number")
    title = str(issue.get("title") or "").strip()
    repo = str(issue.get("repo") or "").strip()
    author = str(issue.get("author") or "").strip()
    labels = [str(name).strip() for name in (issue.get("labels") or []) if str(name).strip()]
    body = str(issue.get("body") or "").strip()

    lines = [f"Work on GitHub issue #{number}" + (f" in {repo}." if repo else "."), ""]
    lines.append(f"Issue #{number}: {title}".rstrip())
    if issue.get("html_url"):
        lines.append(f"URL: {issue['html_url']}")
    lines.append(f"Opened by: {author or 'unknown'}")
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    if body:
        clipped = body if len(body) <= MAX_PROMPT_BODY else body[:MAX_PROMPT_BODY] + "…"
        lines += ["", "Description:", clipped]

    if comment:
        c_author = str(comment.get("author") or "someone").strip() or "someone"
        c_body = str(comment.get("body") or "").strip() or "(no text)"
        if len(c_body) > MAX_PROMPT_COMMENT:
            c_body = c_body[:MAX_PROMPT_COMMENT] + "…"
        lines += ["", f"Triggering comment from @{c_author}:", c_body]

    lines += ["", "Checkout:"]
    if checkout:
        lines += [
            f"An isolated git worktree for this issue is at: {checkout}",
            "Work only inside that directory; do not touch the original clone.",
        ]
    else:
        lines.append("No isolated checkout was prepared; work in the repository you were given.")

    if can_create_pr:
        commit_rule = (
            "- Do NOT push to main/master. To publish the fix, call superagent_pr_create once:"
            " it commits this worktree, pushes a superagent/ branch and opens the pull request."
            f" Put `Closes #{number}` in its body so the issue closes when the PR merges."
            " If the change is not ready, leave it in the working tree and say what is missing."
        )
    else:
        commit_rule = (
            "- Do NOT commit, push, merge, or open/update a pull request."
            " Leave changes in the working tree for a human."
        )

    lines += [
        "",
        "What to do:",
        "- Read the code the issue points at before changing anything.",
        "- Make the smallest change that resolves the request; follow the repository's existing style.",
        "- Add or update a test that would have caught the problem, when the repository has tests.",
        "- Run the relevant tests or checks. If there are none, say what you checked instead.",
        commit_rule,
        "- If the request needs a design decision, ask one concrete question rather than guessing.",
        "- Finish with a summary of at most 200 words: what changed, files touched,"
        " checks run, and anything unresolved.",
    ]
    return "\n".join(lines)


def prune_worktrees(
    workspace_root: str | Path,
    active_paths: set[str] | list[str] | tuple[str, ...] = (),
    *,
    repo_path: str | Path = "",
    git_runner: Optional[Handler] = None,
) -> dict[str, Any]:
    """Remove unused `pr-*` and `worker-*` worktrees under `workspace_root`.

    Any directory whose resolved path matches an entry in `active_paths` (such
    as a non-archived session's `repo` or `worktree_path`) is preserved.
    """
    run = git_runner or _default_git_runner
    root = Path(str(workspace_root or "")).expanduser()
    if not root.is_dir():
        return {"ok": True, "removed": [], "kept": [], "removed_count": 0, "kept_count": 0}
    try:
        root_resolved = root.resolve(strict=True)
    except OSError:
        return {"ok": True, "removed": [], "kept": [], "removed_count": 0, "kept_count": 0}

    active_resolved: set[Path] = set()
    for raw in active_paths or ():
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            active_resolved.add(Path(text).expanduser().resolve(strict=False))
        except OSError:
            continue

    source_dir: Optional[Path] = None
    if str(repo_path or "").strip():
        cand = Path(str(repo_path)).expanduser()
        if cand.is_dir():
            try:
                source_dir = cand.resolve(strict=True)
            except OSError:
                source_dir = None

    removed: list[str] = []
    kept: list[str] = []
    for entry in sorted(root_resolved.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if not (entry.name.startswith("pr-") or entry.name.startswith("worker-")):
            continue
        try:
            resolved_entry = entry.resolve(strict=False)
        except OSError:
            resolved_entry = entry
        if resolved_entry in active_resolved:
            kept.append(entry.name)
            continue
        if source_dir is not None:
            try:
                run(
                    ["git", "-C", str(source_dir), "worktree", "remove", "--force", str(resolved_entry)],
                    str(source_dir),
                    GIT_TIMEOUT,
                )
            except Exception:
                pass
        if resolved_entry.exists():
            try:
                shutil.rmtree(resolved_entry, ignore_errors=True)
            except OSError:
                pass
        if not resolved_entry.exists():
            removed.append(entry.name)
        else:
            kept.append(entry.name)

    if source_dir is not None:
        try:
            run(
                ["git", "-C", str(source_dir), "worktree", "prune"],
                str(source_dir),
                GIT_TIMEOUT,
            )
        except Exception:
            pass

    return {
        "ok": True,
        "removed": removed,
        "kept": kept,
        "removed_count": len(removed),
        "kept_count": len(kept),
    }
