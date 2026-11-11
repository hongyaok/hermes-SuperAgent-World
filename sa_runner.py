"""Launch headless ``hermes chat`` subprocesses for Superagent sessions.

Hermes' subagent lifecycle requires an active parent agent bound to a chat turn,
so the dashboard backend cannot start a subagent from an HTTP request. The
supported pattern in this Hermes version is the one the built-in kanban
dispatcher uses: spawn a headless ``hermes chat`` process and read its
stream-json output on stdout.

Each turn is one subprocess and one daemon thread. Workers never commit or
push: they leave changes in the working tree for a human to review.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

try:  # normal: imported as part of the plugin package
    from . import sa_chats, sa_devloop, sa_model_policy, sa_replies
except ImportError:  # top-level import (dashboard backend, pytest)
    import sa_chats  # type: ignore[no-redef]
    import sa_devloop  # type: ignore[no-redef]
    import sa_model_policy  # type: ignore[no-redef]
    import sa_replies  # type: ignore[no-redef]

# How much stderr is kept for the error message of a failed turn.
STDERR_LIMIT = 4000
# Tool arguments are shown in the session timeline; keep them one line.
TOOL_DETAIL_LIMIT = 160

# Server-side caps. The prompt is checked before a worker is launched, assistant
# text is clipped before it is stored, and every event detail is clipped too.
MAX_PROMPT_CHARS = 20000
MAX_MESSAGE_CHARS = sa_chats.MAX_MESSAGE_CHARS
MAX_EVENT_CHARS = sa_chats.MAX_EVENT_CHARS

# A turn older than this is treated as runaway and cancelled by reap()/list.
DEFAULT_TURN_TIMEOUT_SECONDS = 1800

# The mini-swe-agent style harness ends a task by echoing this exact string.
# The prompt asks for it as the final action and the runner records a
# `submitted` event when a tool result carries it, so the transcript says why
# the worker stopped instead of leaving the reader to infer it from silence.
COMPLETION_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


class RepoPathError(ValueError, RuntimeError):
    """A repository path the runner refuses to use.

    Subclasses ``ValueError`` so HTTP handlers can turn it into a 400, and
    ``RuntimeError`` because launch failures have always raised that here.
    """


def resolve_repo_path(repo: str, *, root: str = "") -> str:
    """Validate a worker repository path and return its resolved form.

    Rejects paths that do not exist, are not directories, contain ``..``, or
    resolve (through a symlink) outside every allowed root. ``root`` may hold
    several ``os.pathsep``-separated roots (the configured clone plus the
    plugin's PR workspaces), and an empty root disables containment. The
    returned string is what may be passed to subprocess argv/cwd; callers must
    never use the raw input.
    """
    raw = (repo or "").strip()
    if not raw:
        return ""
    if any(part == ".." for part in Path(raw).parts):
        raise RepoPathError(f"repository path may not contain '..': {raw}")

    path = Path(os.path.expanduser(raw))
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        raise RepoPathError(f"repository path does not exist: {raw}") from None
    except OSError as exc:
        raise RepoPathError(f"repository path is not usable: {raw} ({exc})") from None
    if not resolved.is_dir():
        raise RepoPathError(f"repository path is not a directory: {raw}")

    parts = root if isinstance(root, (list, tuple)) else str(root or "").split(os.pathsep)
    roots = []
    for part in parts:
        text = str(part or "").strip()
        if text:
            roots.append(Path(os.path.expanduser(text)).resolve(strict=False))
    if roots and not any(resolved == allowed or allowed in resolved.parents for allowed in roots):
        raise RepoPathError(
            f"repository path escapes the configured root {os.pathsep.join(str(r) for r in roots)}: {raw}"
        )
    return str(resolved)


REDACTED = "[redacted]"

# Token shapes and headers that must never reach the database: chat events,
# messages and error text all pass through redact_secrets() first.
_AUTH_HEADER = re.compile(
    r"(?i)(\bauthorization\b[\"']?\s*[:=]\s*[\"']?)"
    r"(?:(?:bearer|basic|token)\s+)?[^\s\"',;}]+"
)
_SECRET_PATTERNS = (
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{8,}=*"),
)


def redact_secrets(text: Any) -> str:
    """Replace GitHub/Slack/OpenAI credentials and Authorization values.

    Deliberately over-eager rather than clever: a false positive costs a few
    characters in a log, a miss costs a leaked credential.
    """
    value = "" if text is None else str(text)
    if not value:
        return value
    value = _AUTH_HEADER.sub(lambda match: match.group(1) + REDACTED, value)
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def _clip_event(detail: Any) -> str:
    """Event details are capped at 2 000 chars after redaction."""
    return redact_secrets(detail)[:MAX_EVENT_CHARS]


def _clip_message(content: Any) -> str:
    """Stored assistant/user text is capped at 20 000 chars after redaction."""
    return redact_secrets(content)[:MAX_MESSAGE_CHARS]


def _turn_timeout(explicit: Optional[int]) -> int:
    """SUPERAGENT_TURN_TIMEOUT_SECONDS, default 1800; <= 0 disables the guard."""
    if explicit is not None:
        return int(explicit)
    raw = (os.environ.get("SUPERAGENT_TURN_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_TURN_TIMEOUT_SECONDS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TURN_TIMEOUT_SECONDS


def parse_event(line: str) -> Optional[dict]:
    """Return one stream-json event, or None for anything that is not an object.

    The CLI also prints plain lines (for example a trailing ``session_id: ...``
    summary), so callers must tolerate junk instead of crashing.
    """
    if not isinstance(line, str):
        return None
    text = line.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def build_prompt(
    request: str,
    *,
    repo: str,
    session_id: str,
    source: str = "desktop",
    can_spawn: bool = False,
    can_create_pr: bool = False,
    can_approve_pr: bool = False,
) -> str:
    """Instructions for the first turn of a session (resume turns send raw text).

    A root session is the town's Router. When it is launched with the plugin's
    toolset, it gets one extra, explicit affordance: spawn real workers with
    ``superagent_spawn``. Workers never get this section, so the prompt and the
    toolset agree that the hierarchy is one level.
    """
    town = ""
    if can_spawn:
        extra_router = ""
        if can_approve_pr:
            extra_router += (
                f'\n- To review or approve a PR directly or via a worker, use superagent_pr_review(session_id="{session_id}", pr_number=..., event="APPROVE"|"REQUEST_CHANGES"|"COMMENT", body="...") or instruct a worker via superagent_message_worker.'
            )
        if can_create_pr:
            extra_router += (
                '\n- Workers can open pull requests with superagent_pr_create when instructed.'
            )
        town = f"""
Town mode:
- You are the Router for this town. Split the request into independent jobs when that is the honest reading; do not invent parallel work.
- For each job, call the superagent_spawn tool with parent_session_id="{session_id}" and a self-contained prompt. Each call creates one real worker session, one house, and one person.
- To check on your workers, their diffs, and PRs, call superagent_town_status(parent_session_id="{session_id}").
- To send follow-up instructions to an idle/review worker (or ask it to create/review/approve a PR), call superagent_message_worker(parent_session_id="{session_id}", worker_session_id="...", message="...").
- Spawn at most {sa_chats.MAX_TOWN_WORKERS} workers. Workers cannot spawn more workers.
- Do not do the delegated jobs yourself. Finish with a short summary of what you delegated and why.{extra_router}
"""
    if can_create_pr:
        commit_rule = (
            f'- Do NOT push directly to main/master or merge. To publish changes as a pull request when asked, use the superagent_pr_create tool (with session_id="{session_id}"); otherwise leave changes in the working tree.'
        )
    else:
        commit_rule = "- Do NOT commit, push, merge, or open a pull request. Leave changes in the working tree."
    approve_rule = ""
    if can_approve_pr and not can_spawn:
        approve_rule = (
            f'\n- When asked to review or approve a pull request, use superagent_pr_review(session_id="{session_id}", pr_number=..., event="APPROVE"|"REQUEST_CHANGES"|"COMMENT", body="...").'
        )
    return f"""You are one Superagent worker session ({session_id}), started from {source}.

Request:
{(request or "").strip()}
{town}
Rules:
- Work only inside the repository: {repo or "the current working directory"}. Do not touch files outside it.
- Make the smallest change that satisfies the request; follow the repository's existing style.
- Run the relevant tests or checks. If there are none, say what you checked instead.
{commit_rule}{approve_rule}
- If the request needs a design decision, ask one concrete question rather than guessing.
- Finish with a summary of at most 200 words: what changed, files touched, checks run, and anything unresolved.
"""


def build_mini_prompt(
    request: str,
    *,
    repo: str,
    session_id: str,
    can_create_pr: bool = False,
    can_approve_pr: bool = False,
) -> str:
    """Instructions for a bash-only worker, in the mini-swe-agent style.

    This is the mini-swe-agent *architecture* on top of Hermes' headless chat,
    not the external package: the worker has one action tool (the terminal),
    must issue exactly one command per step, keeps a linear transcript and
    ends by echoing ``COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT``. Hermes owns the
    model loop and the hard step cap (``--max-turns``), so there is no extra
    binary, dependency or model API code to maintain.
    """
    if can_create_pr:
        publish_rule = (
            f'- Do NOT push directly to main/master or merge. When the request asks you to publish, call the superagent_pr_create tool (session_id="{session_id}"); it is an allowed non-terminal action in this turn.'
        )
    else:
        publish_rule = (
            "- Do NOT commit, push, merge, or open a pull request. Leave changes in the working tree."
        )
    approve_rule = ""
    if can_approve_pr:
        approve_rule = (
            f'\n- When asked to review or approve a pull request, call superagent_pr_review(session_id="{session_id}", pr_number=..., event="APPROVE"|"REQUEST_CHANGES"|"COMMENT", body="..."); it is an allowed non-terminal action in this turn.'
        )
    return f"""You are one Superagent worker session ({session_id}) running in bash-only mode.

Request:
{(request or "").strip()}

Working rules:
- Work only inside the repository: {repo or "the current working directory"}. Do not touch files outside it.
- You have exactly one action tool: the terminal (bash). Every assistant response must issue exactly one terminal command. No prose-only turns while work remains, and no multiple commands in one turn.
- Inspect before you edit: read the relevant files and the repository's existing style first. Make the smallest change that satisfies the request.
- Run the relevant tests or checks with the same terminal tool. If there are none, say what you checked instead.
- The turn has a hard step cap (Hermes' --max-turns); prefer a few decisive commands over exploration.
{publish_rule}{approve_rule}
- When the task is finished, your final action must be exactly: echo {COMPLETION_MARKER}
- After that command returns, reply with a plain-text summary of at most 200 words and no further tool call: what changed, files touched, checks run, and anything unresolved.
"""


def resolve_hermes_bin(explicit: Optional[str] = None) -> str:
    """Find the hermes executable: explicit arg, env override, then PATH."""
    candidate = (explicit or os.environ.get("SUPERAGENT_HERMES_BIN") or "").strip()
    if not candidate:
        candidate = shutil.which("hermes") or ""
    if not candidate:
        raise RuntimeError(
            "hermes executable not found; install hermes or set SUPERAGENT_HERMES_BIN"
        )
    return candidate


def _terminate_tree(proc: Any) -> None:
    """Stop a worker and its children (kept small so tests can patch it)."""
    if proc is None:
        return
    pid = getattr(proc, "pid", None)
    if os.name == "nt" and pid:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
            return
        except Exception:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _drain_stderr(proc: Any, sink: list[str]) -> None:
    """Read stderr in the background so a chatty child cannot fill the pipe."""
    total = 0
    try:
        for line in proc.stderr:
            if total >= STDERR_LIMIT:
                continue  # keep draining, stop storing
            text = str(line)
            sink.append(text[: STDERR_LIMIT - total])
            total += len(text)
    except Exception:
        return


def _text_of(event: dict) -> str:
    """Best-effort assistant text from a stream-json event."""
    text = event.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [
                part.get("text")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            joined = "\n".join(parts).strip()
            if joined:
                return joined
    return ""


def _tool_detail(event: dict) -> str:
    """One-line ``tool: args`` summary for the session timeline."""
    name = event.get("tool") or event.get("name") or event.get("tool_name") or "tool"
    args = event.get("args")
    if args is None:
        args = event.get("input")
    if isinstance(args, str):
        text = args
    elif args is None:
        text = ""
    else:
        try:
            text = json.dumps(args, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(args)
    text = " ".join(text.split())
    if len(text) > TOOL_DETAIL_LIMIT:
        text = text[: TOOL_DETAIL_LIMIT - 1] + "…"
    return f"{name}: {text}" if text else str(name)


def _stderr_summary(text: str) -> str:
    """Drop the CLI's session trailer lines; keep the real diagnostics."""
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("session_id:"):
            continue
        if lowered.startswith("session:") and len(stripped.split()) == 2:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _session_id_from_stderr(text: str) -> str:
    """The CLI prints ``session_id: local-...`` on stderr at the end of a run."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.lower().startswith("session_id:"):
            value = line.split(":", 1)[1].strip()
            if value:
                return value
    return ""


def _result_metrics(event: dict) -> dict:
    """tokens / duration / session id / final text from a ``type=result`` event."""
    metrics: dict[str, Any] = {}
    if not isinstance(event, dict):
        return metrics
    session_id = event.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        metrics["session_id"] = session_id.strip()
    tokens = event.get("tokens")
    total: Any = None
    if isinstance(tokens, dict):
        total = tokens.get("total") or tokens.get("output")
    elif isinstance(tokens, (int, float)):
        total = tokens
    if isinstance(total, (int, float)):
        metrics["tokens"] = int(total)
    duration = event.get("duration_ms")
    if isinstance(duration, (int, float)):
        metrics["duration_ms"] = int(duration)
    text = event.get("text")
    if isinstance(text, str) and text.strip():
        metrics["text"] = text.strip()
    error = event.get("error")
    if isinstance(error, str) and error.strip():
        metrics["error"] = error.strip()
    return metrics


class Runner:
    """Runs one headless ``hermes chat`` turn per launch, at most N at a time."""

    def __init__(
        self,
        store_or_chatstore: Any,
        *,
        hermes_bin: Optional[str] = None,
        max_workers: int = 2,
        toolsets: tuple[str, ...] = ("file", "terminal"),
        router_toolset: str = "",
        yolo: bool = True,
        model: str = "",
        provider: str = "",
        reasoning_effort: str = "",
        repo: str = "",
        repo_root: str = "",
        max_turns: int = 120,
        turn_timeout_seconds: Optional[int] = None,
        env: Optional[dict] = None,
        source_tag: Optional[str] = None,
        can_create_pr: bool = False,
        can_approve_pr: bool = False,
    ):
        if hasattr(store_or_chatstore, "create_session"):
            self.chat = store_or_chatstore
        else:  # a plain sa_store.Store: reuse the same database file
            self.chat = sa_chats.ChatStore(getattr(store_or_chatstore, "path"))
        self.hermes_bin = hermes_bin or ""
        self.max_workers = max(1, int(max_workers or 1))
        self.toolsets = tuple(toolsets or ("file", "terminal"))
        # Only root sessions get the plugin toolset; that is what makes a
        # router able to call superagent_spawn without giving every worker the
        # same power. Empty means "this Runner never spawns towns" (tests).
        self.router_toolset = (router_toolset or "").strip()
        self.yolo = bool(yolo)
        self.model = model or ""
        self.provider = provider or ""
        self.reasoning_effort = (reasoning_effort or "").strip()
        self.repo = repo or ""
        # The configured root every per-turn repo must resolve inside. Defaults
        # to the configured repo, so a chat cannot wander into another tree.
        self.repo_root = (repo_root or os.environ.get("SUPERAGENT_REPO_ROOT") or "").strip()
        self.max_turns = max(1, int(max_turns or 1))
        self.turn_timeout_seconds = _turn_timeout(turn_timeout_seconds)
        self.env = dict(env or {})
        # The gateway only accepts source tags it advertises (cli/tool/oneshot
        # on this version), so `superagent` cannot be used here. "tool" is the
        # documented tag for third-party integrations: hidden from user lists.
        self.source_tag = (source_tag or os.environ.get("SUPERAGENT_CHAT_SOURCE") or "tool").strip() or "tool"
        self.can_create_pr = bool(can_create_pr)
        self.can_approve_pr = bool(can_approve_pr)

        self._sem = threading.Semaphore(self.max_workers)
        self._lock = threading.Lock()
        self._procs: dict[str, Any] = {}
        self._started: dict[str, float] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancelled: set[str] = set()
        self._cancel_reasons: dict[str, str] = {}
        self._resolved_bin: Optional[str] = None

    # -- public API ------------------------------------------------------

    def binary(self) -> str:
        """Resolved hermes executable (raises RuntimeError when missing)."""
        if not self._resolved_bin:
            self._resolved_bin = resolve_hermes_bin(self.hermes_bin)
        return self._resolved_bin

    def available(self) -> tuple[bool, str]:
        try:
            return True, self.binary()
        except RuntimeError as exc:
            return False, str(exc)

    def launch_turn(
        self,
        session_id: str,
        prompt: str,
        *,
        resume_session_id: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        repo: Optional[str] = None,
        block: bool = False,
        can_create_pr: Optional[bool] = None,
        can_approve_pr: Optional[bool] = None,
    ) -> dict:
        """Start one turn in a daemon thread; raises RuntimeError if it cannot."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"prompt is too long (max {MAX_PROMPT_CHARS} chars)")
        binary = self.binary()
        # Resolve before anything is persisted or queued: the resolved path is
        # the only one that may reach subprocess argv/cwd.
        repo_path = resolve_repo_path(
            repo if repo is not None else self.repo,
            root=self.repo_root or self.repo,
        )

        session = self.chat.get_session(session_id, message_limit=0) or {}
        # A data-training ("contributor") tier cannot start headless unless the
        # host opted in. Refuse here, before a process or a queue slot exists,
        # so every surface (API, tools, CLI, retry) fails with the same reason
        # instead of a worker that dies on Hermes' consent prompt.
        sa_model_policy.check_model(
            str(model or self.model or session.get("model") or "")
        )
        is_router = session.get("parent_session_id") is None
        # The harness lives on the row, so every launch path (REST, tool,
        # retry, CLI) agrees without threading a new argument through callers.
        # A root is always the full Hermes Router; mini is a worker-only mode.
        harness = (
            "hermes"
            if is_router
            else str(session.get("harness") or "hermes").strip().lower()
        )
        mini = harness == "mini"
        can_spawn = bool(is_router and self.router_toolset)
        eff_create_pr = self.can_create_pr if can_create_pr is None else bool(can_create_pr)
        eff_approve_pr = self.can_approve_pr if can_approve_pr is None else bool(can_approve_pr)
        needs_plugin_tools = can_spawn or eff_create_pr or eff_approve_pr
        # mini-swe-agent style: bash is the only action tool. The plugin
        # toolset is still added when the turn may create/approve a PR,
        # because those are the worker's only way to publish.
        toolsets = ("terminal",) if mini else self.toolsets
        if needs_plugin_tools and self.router_toolset and self.router_toolset not in toolsets:
            toolsets = toolsets + (self.router_toolset,)
        resume = (resume_session_id or "").strip() or None
        if resume:
            worker_prompt = prompt
        elif mini:
            worker_prompt = build_mini_prompt(
                prompt,
                repo=repo_path,
                session_id=session_id,
                can_create_pr=eff_create_pr,
                can_approve_pr=eff_approve_pr,
            )
        else:
            worker_prompt = build_prompt(
                prompt,
                repo=repo_path,
                session_id=session_id,
                source=session.get("source") or "desktop",
                can_spawn=can_spawn,
                can_create_pr=eff_create_pr,
                can_approve_pr=eff_approve_pr,
            )

        fields: dict[str, Any] = {"status": sa_chats.QUEUED, "error": None, "pid": None}
        if model:
            fields["model"] = model
        elif self.model:
            fields["model"] = self.model
        if provider:
            fields["provider"] = provider
        elif self.provider:
            fields["provider"] = self.provider
        # None means "keep the row's current level"; an explicit empty string
        # clears it. The row wins over the Runner's configured default.
        if reasoning_effort is None:
            effort = str(session.get("reasoning_effort") or self.reasoning_effort or "").strip()
        else:
            effort = str(reasoning_effort).strip()
        if reasoning_effort is not None:
            fields["reasoning_effort"] = effort
        elif not str(session.get("reasoning_effort") or "").strip() and effort:
            fields["reasoning_effort"] = effort
        self.chat.update_session(session_id, **fields)
        if mini and not resume:
            # One line, on the first turn only, so the transcript explains why
            # this worker has a terminal and nothing else.
            self._add_event(session_id, "harness", "mini: bash-only worker")
        self._add_event(session_id, "queued", "waiting for a worker slot")

        thread = threading.Thread(
            target=self._run_turn,
            args=(session_id, worker_prompt),
            kwargs={
                "binary": binary,
                "resume": resume,
                "model": model or self.model,
                "provider": provider or self.provider,
                "reasoning_effort": effort,
                "repo": repo_path,
                "toolsets": toolsets,
            },
            name=f"superagent-{session_id}",
            daemon=True,
        )
        if block:
            # A CLI process is short-lived: a daemon thread would be killed the
            # moment the command returns, leaving the session queued forever
            # with no error. Run the turn on this thread instead.
            self._run_turn(
                session_id,
                worker_prompt,
                binary=binary,
                resume=resume,
                model=model or self.model,
                provider=provider or self.provider,
                reasoning_effort=effort,
                repo=repo_path,
                toolsets=toolsets,
            )
            return {
                "session_id": session_id,
                "resume": bool(resume),
                "command": binary,
                "blocked": True,
            }
        with self._lock:
            self._threads[session_id] = thread
            self._started[session_id] = time.time()
        thread.start()
        return {"session_id": session_id, "resume": bool(resume), "command": binary}

    def stop(self, session_id: str) -> bool:
        """Cancel a running (or queued) turn. Returns True if a process was killed."""
        return self._cancel(session_id, "cancelled by user")

    def reap(self, *, now: Optional[float] = None, timeout_seconds: Optional[int] = None) -> list[dict]:
        """Cancel turns older than the timeout; returns the cancelled sessions.

        Runaway guard: a queued/running turn whose start (or creation, while it
        waits for a worker slot) is older than SUPERAGENT_TURN_TIMEOUT_SECONDS
        (default 1800) is cancelled, with the reason recorded as an event. The
        dashboard calls this on every chat-list read, so a forgotten turn cannot
        hold a worker slot forever.
        """
        timeout = int(self.turn_timeout_seconds if timeout_seconds is None else timeout_seconds)
        if timeout <= 0:
            return []
        moment = time.time() if now is None else float(now)
        expired: list[dict] = []
        try:
            # Archived rows are hidden from the rail but must still be reaped:
            # a turn that was archived in a race with its own launch is live
            # all the same.
            sessions = self.chat.list_sessions(limit=500, archived="all").get("sessions", [])
        except Exception:
            return expired
        for row in sessions:
            status = str(row.get("status") or "")
            if status not in (sa_chats.QUEUED, sa_chats.RUNNING):
                continue
            started = row.get("started_at") or row.get("created_at") or 0
            try:
                age = moment - float(started)
            except (TypeError, ValueError):
                continue
            if age <= timeout:
                continue
            # Say which phase timed out: a queued turn waited for a worker
            # slot, a running one held it. Same cancellation either way.
            reason = f"turn timed out after {int(age)}s while {status} (limit {timeout}s)"
            self._cancel(str(row["id"]), reason)
            expired.append({"id": row["id"], "status": sa_chats.CANCELLED, "reason": reason})
        return expired

    def active(self) -> list[dict]:
        """Sessions with a live process, ready for a status chip or grid."""
        with self._lock:
            items = []
            for session_id, proc in self._procs.items():
                if proc is None or proc.poll() is not None:
                    continue
                items.append(
                    {
                        "session_id": session_id,
                        "pid": getattr(proc, "pid", None),
                        "started_at": self._started.get(session_id),
                    }
                )
        return items

    # -- internals -------------------------------------------------------

    def _add_event(self, session_id: str, kind: str, detail: str = "") -> None:
        """Persist one event with secrets scrubbed and the detail cap applied."""
        self.chat.add_event(session_id, kind, _clip_event(detail))

    def _add_message(self, session_id: str, role: str, content: str) -> None:
        """Persist one message with secrets scrubbed and the 20k cap applied."""
        self.chat.add_message(session_id, role, _clip_message(content))

    def _cancel(self, session_id: str, reason: str) -> bool:
        """Mark a turn cancelled, remember why, and kill its process if live."""
        with self._lock:
            self._cancelled.add(session_id)
            self._cancel_reasons[session_id] = reason
            proc = self._procs.get(session_id)
            thread = self._threads.get(session_id)
            if proc is None and (thread is None or not thread.is_alive()):
                # Nothing will consume the flag; keep the reason for the audit
                # event only, so later turns are not cancelled by a stale flag.
                self._cancelled.discard(session_id)
                thread_alive = False
            else:
                thread_alive = True
        killed = False
        if proc is not None and proc.poll() is None:
            _terminate_tree(proc)
            killed = True
        self.chat.update_session(
            session_id,
            status=sa_chats.CANCELLED,
            error=_clip_event(reason),
            finished_at=time.time(),
            pid=None,
        )
        self._add_event(session_id, "cancelled", reason)
        if not thread_alive:
            self._cancel_reasons.pop(session_id, None)
        return killed

    def _cancel_reason(self, session_id: str) -> str:
        with self._lock:
            return self._cancel_reasons.pop(session_id, "")

    def _run_turn(
        self,
        session_id: str,
        prompt: str,
        *,
        binary: str,
        resume: Optional[str],
        model: str,
        provider: str,
        reasoning_effort: str,
        repo: str,
        toolsets: tuple[str, ...],
    ) -> None:
        with self._sem:
            if self._take_cancelled(session_id):
                reason = self._cancel_reason(session_id) or "cancelled before start"
                self._finish(session_id, sa_chats.CANCELLED, error=reason)
                return
            try:
                self._execute(
                    session_id,
                    prompt,
                    binary=binary,
                    resume=resume,
                    model=model,
                    provider=provider,
                    reasoning_effort=reasoning_effort,
                    repo=repo,
                    toolsets=toolsets,
                )
            except Exception as exc:  # never leak a dead thread
                self._finish(session_id, sa_chats.FAILED, error=f"runner error: {exc}")

    def _execute(
        self,
        session_id: str,
        prompt: str,
        *,
        binary: str,
        resume: Optional[str],
        model: str,
        provider: str,
        reasoning_effort: str,
        repo: str,
        toolsets: tuple[str, ...],
    ) -> None:
        argv = self._build_argv(
            binary, prompt, resume_session_id=resume, model=model, provider=provider,
            reasoning_effort=reasoning_effort, repo=repo, toolsets=toolsets,
        )
        self._add_event(session_id, "started", " ".join(argv))

        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in self.env.items()})
        stderr_lines: list[str] = []
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # A pipe, not DEVNULL: on Windows an isatty() call on the NUL
                # device reports True, so a child given DEVNULL still believes
                # it has a terminal, prints a consent prompt and cancels the
                # turn. Closing the write end at once gives every worker a real
                # "no terminal" answer — it either proceeds or refuses fast.
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=repo or None,
                env=env,
            )
        except Exception as exc:
            self._finish(session_id, sa_chats.FAILED, error=f"cannot start hermes: {exc}")
            return
        try:
            proc.stdin.close()
        except Exception:
            pass  # a fake process in a test has no pipe to close

        with self._lock:
            self._procs[session_id] = proc
        # Register the process before flipping to `running`, so a `running`
        # session always has a pid and shows up in active().
        self.chat.update_session(
            session_id, status=sa_chats.RUNNING, started_at=time.time(), pid=getattr(proc, "pid", None)
        )

        reader = threading.Thread(
            target=_drain_stderr, args=(proc, stderr_lines), name=f"superagent-stderr-{session_id}", daemon=True
        )
        reader.start()

        exit_code: Optional[int] = None
        read_error = ""
        result: dict = {}
        buffer: list[str] = []
        buffer_chars = 0
        wrote_text = False
        submitted = False

        def flush_text() -> None:
            """Turn the buffered ``text`` deltas into one assistant message."""
            nonlocal wrote_text, buffer_chars
            if not buffer:
                return
            text = "".join(buffer).strip()
            buffer.clear()
            buffer_chars = 0
            if text:
                self._add_message(session_id, "assistant", text)
                wrote_text = True

        try:
            for line in proc.stdout:
                event = parse_event(line)
                if event is None:
                    continue
                kind = str(event.get("type") or "")
                if kind == "system":
                    self._on_system(session_id, event)
                elif kind == "text":
                    fragment = event.get("text")
                    if isinstance(fragment, str) and fragment and buffer_chars < MAX_MESSAGE_CHARS:
                        # Stop buffering past the cap: a runaway worker must
                        # not balloon memory before the store clips the text.
                        fragment = fragment[: MAX_MESSAGE_CHARS - buffer_chars]
                        buffer.append(fragment)
                        buffer_chars += len(fragment)
                elif kind in ("assistant", "message"):
                    text = _text_of(event)
                    if text:
                        flush_text()
                        self._add_message(session_id, "assistant", text)
                        wrote_text = True
                elif kind in ("tool_use", "tool"):
                    flush_text()
                    self._add_event(session_id, "tool", _tool_detail(event))
                elif kind == "tool_result":
                    detail = _tool_detail(event)
                    # The mini harness marks completion with an echoed string.
                    # It can land in the command args or in the output, so the
                    # whole event is checked rather than just the summary.
                    # Record it once; a worker that keeps going afterwards is
                    # not trusted to have submitted twice.
                    if not submitted and COMPLETION_MARKER in json.dumps(
                        event, ensure_ascii=False, default=str
                    ):
                        submitted = True
                        flush_text()
                        self._add_event(
                            session_id, "submitted", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
                        )
                    if event.get("is_error"):
                        flush_text()
                        self._add_event(session_id, "tool_error", detail)
                elif kind == "result":
                    result = event
                elif kind == "error":
                    flush_text()
                    self._add_event(
                        session_id,
                        "error",
                        _text_of(event) or str(event.get("error") or ""),
                    )
        except Exception as exc:
            read_error = str(exc)
        finally:
            try:
                exit_code = proc.wait(timeout=10)
            except Exception:
                exit_code = None
            reader.join(timeout=2)
        flush_text()

        metrics = _result_metrics(result)
        fields: dict[str, Any] = {"finished_at": time.time()}
        if metrics.get("session_id"):
            fields["hermes_session_id"] = metrics["session_id"]
        if metrics.get("tokens") is not None:
            fields["tokens"] = metrics["tokens"]
        if metrics.get("duration_ms") is not None:
            fields["duration_ms"] = metrics["duration_ms"]
        if not wrote_text and metrics.get("text"):
            self._add_message(session_id, "assistant", metrics["text"])

        stderr_tail = "".join(stderr_lines)
        stderr_session_id = _session_id_from_stderr(stderr_tail)
        if stderr_session_id and not fields.get("hermes_session_id"):
            fields["hermes_session_id"] = stderr_session_id

        cancelled = self._take_cancelled(session_id)
        if cancelled:
            status = sa_chats.CANCELLED
            error = self._cancel_reason(session_id) or "cancelled by user"
        elif read_error:
            status = sa_chats.FAILED
            error = f"could not read worker output: {read_error}"
        elif exit_code == 0:
            status = sa_chats.REVIEW
            error = None
        else:
            tail = _stderr_summary(stderr_tail)
            status = sa_chats.FAILED
            reason = metrics.get("error") or tail or f"hermes exited with code {exit_code}"
            error = reason[-STDERR_LIMIT:]

        if metrics.get("tokens") is not None or metrics.get("duration_ms") is not None:
            self._add_event(
                session_id,
                "result",
                f"exit={exit_code} tokens={metrics.get('tokens')} duration_ms={metrics.get('duration_ms')}",
            )
        self._finish(session_id, status, error=error, **fields)
        if status == sa_chats.REVIEW:
            self._reply_on_github(session_id)
            self._continue_dev_loop(session_id)

    def _on_system(self, session_id: str, event: dict) -> None:
        fields: dict[str, Any] = {}
        hermes_session_id = event.get("session_id")
        if isinstance(hermes_session_id, str) and hermes_session_id.strip():
            fields["hermes_session_id"] = hermes_session_id.strip()
        model = event.get("model")
        if isinstance(model, str) and model.strip():
            fields["model"] = model.strip()
        if fields:
            self.chat.update_session(session_id, **fields)
        self._add_event(session_id, "system", str(event.get("subtype") or "init"))

    def _build_argv(
        self,
        binary: str,
        prompt: str,
        *,
        resume_session_id: Optional[str] = None,
        model: str = "",
        provider: str = "",
        reasoning_effort: str = "",
        repo: str = "",
        toolsets: Optional[tuple[str, ...]] = None,
    ) -> list[str]:
        argv = [
            binary,
            "chat",
            "-q",
            prompt,
            "-Q",
            "--format",
            "stream-json",
            "--source",
            self.source_tag,
            "-t",
            ",".join(toolsets or self.toolsets),
        ]
        if self.yolo:
            argv.append("--yolo")
        if self.max_turns:
            argv += ["--max-turns", str(int(self.max_turns))]
        if model:
            argv += ["-m", model]
        if provider:
            argv += ["--provider", provider]
        if reasoning_effort:
            argv += ["--reasoning", reasoning_effort]
        if repo:
            argv += ["--in", repo]
        if resume_session_id:
            argv += ["--resume", resume_session_id]
        return argv

    def _finish(self, session_id: str, status: str, *, error: Optional[str] = None, **extra: Any) -> None:
        clean_error = _clip_event(error) if error else None
        fields = {"status": status, "error": clean_error, "pid": None}
        fields.update(extra)
        # Write the event first: once the new status is visible, its audit
        # trail already contains the line that explains it.
        self._add_event(session_id, status, error or status)
        self.chat.update_session(session_id, **fields)
        with self._lock:
            self._procs.pop(session_id, None)
            self._threads.pop(session_id, None)
            self._started.pop(session_id, None)
            self._cancel_reasons.pop(session_id, None)

    def _reply_on_github(self, session_id: str) -> None:
        """Best-effort: post the summary back to the session's PR or issue.

        Runs only after a turn ends in `review`, and only when
        `github_reply_on_finish` is on — `sa_replies` owns the rest of the
        decision. A reply must never change the session's status, so every
        failure lands as an event.
        """
        try:
            sa_replies.reply_after_turn(self.chat, session_id)
        except Exception as exc:  # noqa: BLE001 - a reply cannot fail a turn
            try:
                self._add_event(session_id, "reply_failed", str(exc)[:MAX_EVENT_CHARS])
            except Exception:
                pass

    def _continue_dev_loop(self, session_id: str) -> None:
        """Best-effort: start this town's next development-loop round.

        Runs after a turn ends in `review`, for workers and the Router alike:
        a worker's finish is what offers a new PR, and the Router's own finish
        is what drains a PR that arrived while it was busy. `sa_devloop` owns
        every decision (armed? budget left? a PR waiting?); a failure here is an
        event, never a failed turn.
        """
        try:
            sa_devloop.after_turn(self.chat, session_id, runner=self)
        except Exception as exc:  # noqa: BLE001 - the loop cannot fail a turn
            try:
                self._add_event(session_id, "dev_loop_failed", str(exc)[:MAX_EVENT_CHARS])
            except Exception:
                pass

    def _take_cancelled(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._cancelled:
                self._cancelled.discard(session_id)
                return True
        return False
