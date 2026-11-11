"""The Router's development loop: worker PR -> review -> merge -> next instruction.

A town already has every part of this loop as a *manual* tool call: the router
can review a pull request, merge it, and message the worker that opened it. This
module is the automation policy — the switch the operator flips when they want
that cycle to run by itself for N rounds.

Shape of one round
------------------
1. A worker turn ends having published a pull request.
2. The Router gets one turn: review that PR, merge it if it is sound, and send
   the worker the next instruction (which starts the worker's next turn).
3. That worker publishes its next PR, and the loop repeats until the round
   budget is spent.

Two rules keep it from spinning:

* ``dev_loop:<worker>:<turn>`` is marked when a round starts, so two hooks
  firing for the same finished turn cannot launch two Router turns.
* ``dev_loop_merged:<worker>:<pr>`` is marked by ``superagent_pr_merge`` when a
  PR lands. A worker turn that only *re-reads* an already-merged PR therefore
  does not start another round; a turn after REQUEST_CHANGES (nothing merged)
  does, which is how a rejected PR comes back for a second look.

Nothing here commits, pushes or merges by itself. It launches a Router turn;
the Router calls the same tools a person would, under the same settings, and
every one of them can refuse.
"""

from __future__ import annotations

from typing import Any, Optional

try:  # normal: imported as part of the plugin package
    from . import sa_chats, sa_settings
except ImportError:  # top-level import (dashboard backend, pytest)
    import sa_chats  # type: ignore[no-redef]
    import sa_settings  # type: ignore[no-redef]

# A loop of one round is a single review; twenty is already a long unattended
# stretch, and the budget is per town rather than per day.
DEFAULT_ROUNDS = 3
MIN_ROUNDS = 1
MAX_ROUNDS = 20

# One round per finished worker turn, and one merge per PR. Both keys live in
# the `gh_seen` table (the same dedupe store the GitHub reply uses) so a
# restart cannot replay a round.
TURN_KEY = "dev_loop:{worker_id}:{turn}"
MERGED_KEY = "dev_loop_merged:{worker_id}:{pr_number}"

# Which settings a loop needs before it may start. Merge and approve are the
# two capabilities the Router has to exercise; the loop never grants them, it
# only uses them. Keeping this in one function means the route, the CLI and the
# prompt builder cannot disagree about whether the loop can run.
REQUIRED_SETTINGS = ("allow_router_pr_approve", "allow_worker_merge_pr")

# What a missing setting is called in a sentence a person can act on.
_SETTING_LABELS = {
    "allow_router_pr_approve": "Allow Router & workers to review/approve PRs",
    "allow_worker_merge_pr": "Allow workers to comment and merge PRs",
}


def normalize_rounds(value: Any, *, strict: bool = False) -> int:
    """Return a round budget in range.

    ``strict=True`` is the API/CLI path: a bad number is an error rather than a
    silent default, because a budget the operator typed is a budget they meant.
    The loader is lenient so a hand-edited row degrades to the default.
    """
    if isinstance(value, bool):
        if strict:
            raise ValueError("rounds must be an integer")
        return DEFAULT_ROUNDS
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        if strict:
            raise ValueError("rounds must be an integer")
        return DEFAULT_ROUNDS
    if number < MIN_ROUNDS or number > MAX_ROUNDS:
        if strict:
            raise ValueError(f"rounds must be between {MIN_ROUNDS} and {MAX_ROUNDS}")
        return DEFAULT_ROUNDS
    return number


def loop_on(session: dict[str, Any] | None) -> bool:
    """True when this row's development loop is armed."""
    return bool((session or {}).get("dev_loop"))


def rounds_of(session: dict[str, Any] | None) -> int:
    """The round budget on a root row (a blank column means the default)."""
    return normalize_rounds((session or {}).get("dev_loop_rounds"))


def rounds_used(session: dict[str, Any] | None) -> int:
    """How many rounds this root has already spent."""
    try:
        return max(0, int((session or {}).get("dev_loop_round") or 0))
    except (TypeError, ValueError):
        return 0


def missing_requirements(settings: dict[str, Any] | None) -> list[str]:
    """Settings the loop needs but the host has not enabled, in panel order."""
    current = settings or {}
    return [key for key in REQUIRED_SETTINGS if not bool(current.get(key))]


def missing_sentence(missing: list[str]) -> str:
    """A sentence naming the switches to turn on, or '' when nothing is missing."""
    if not missing:
        return ""
    labels = [_SETTING_LABELS.get(key, key) for key in missing]
    joined = " and ".join(labels)
    return f"the development loop needs {joined} in Superagent Settings"


def turn_key(worker_id: str, turn: Any) -> str:
    """Dedupe key for one finished worker turn."""
    try:
        stamp = int(float(turn or 0) * 1000)
    except (TypeError, ValueError):
        stamp = 0
    return TURN_KEY.format(worker_id=str(worker_id or "").strip(), turn=stamp)


def merged_key(worker_id: str, pr_number: Any) -> str:
    """Dedupe key for one landed pull request."""
    try:
        number = int(pr_number or 0)
    except (TypeError, ValueError):
        number = 0
    return MERGED_KEY.format(worker_id=str(worker_id or "").strip(), pr_number=number)


def merge_recorded(store: Any, worker_id: str, pr_number: Any) -> bool:
    """True when this worker's PR is known to have been merged by the loop."""
    number = str(pr_number or "").strip()
    if not number or number == "0":
        return False
    try:
        return bool(store.is_seen(merged_key(worker_id, number)))
    except Exception:
        return False


def record_merge(store: Any, session_id: str, pr_number: Any) -> None:
    """Remember that a pull request is done, for every session that owns it.

    The caller is usually the Router merging a worker's PR, so the key has to
    land on the *worker* row — that is the row ``pending_pr`` reads. When the
    caller owns the PR itself (a worker merging its own work) the child scan
    finds nothing and only the caller is marked.
    """
    try:
        number = int(pr_number or 0)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        return
    owners = [str(session_id or "").strip()]
    try:
        for child in store.list_children(str(session_id or "")):
            try:
                if int(child.get("pr_number") or 0) == number:
                    owners.append(str(child.get("id") or "").strip())
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    for owner in dict.fromkeys(owner for owner in owners if owner):
        try:
            store.mark_seen(merged_key(owner, number))
        except Exception:
            pass


def pending_pr(store: Any, root_id: str) -> Optional[dict[str, Any]]:
    """The oldest worker PR this town has not handled yet, or None.

    "Not handled" means: the worker finished a turn (so there is a turn to
    dedupe on), its row carries a PR number, that PR was not merged by the loop,
    and no round has started for that finish.
    """
    try:
        workers = store.list_children(root_id)
    except Exception:
        return None
    for worker in workers:
        try:
            number = int(worker.get("pr_number") or 0)
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        try:
            finished = float(worker.get("finished_at") or 0)
        except (TypeError, ValueError):
            finished = 0.0
        if finished <= 0:
            # A row that never completed a turn has nothing to review yet.
            continue
        if merge_recorded(store, str(worker.get("id") or ""), number):
            continue
        try:
            if store.is_seen(turn_key(str(worker.get("id") or ""), finished)):
                continue
        except Exception:
            pass
        return {"worker": worker, "pr_number": number, "turn": finished}
    return None


def build_prompt(
    root: dict[str, Any],
    worker: dict[str, Any],
    *,
    pr_number: int,
    round_no: int,
    rounds: int,
    base: str = "main",
) -> str:
    """The Router's instruction for one loop round.

    It names the exact tool calls rather than describing the goal: a headless
    turn has no chance to ask what "handle the PR" means, and the three tools
    are the only publishing powers the loop is allowed to use.
    """
    root_id = str(root.get("id") or "")
    worker_id = str(worker.get("id") or "")
    title = str(worker.get("title") or worker_id).strip()
    url = str(worker.get("pr_url") or "").strip()
    branch = str(worker.get("branch_name") or "").strip()
    repo = str(root.get("repo") or "").strip() or "the repository"
    base_branch = str(base or "main").strip() or "main"
    head = branch or "the PR's head branch"
    pr = int(pr_number)

    return f"""Development loop — round {round_no} of {rounds} for town {root_id}.

Worker {worker_id} ("{title}") opened pull request #{pr}{f' ({url})' if url else ''}.
That is the only pull request to handle this round.

Do these three steps in one turn:

1. Review it. In {repo}, run `git fetch origin`, then read
   `git diff origin/{base_branch}...origin/{head}` and the files it touches.
   Judge it against what you asked for, and whether it is safe to land.

2. If it is sound, approve and merge it:
   superagent_pr_review(session_id="{root_id}", pr_number={pr}, event="APPROVE", body="...")
   superagent_pr_merge(session_id="{root_id}", pr_number={pr}, merge_method="merge", body="...")
   Use a merge commit (not squash, not rebase): it keeps the worker's branch an
   ancestor of {base_branch}, so its next pull request shows only new work.
   If it is not sound, do NOT merge — make the correction the next instruction.

3. Send the next instruction to the same worker:
   superagent_message_worker(parent_session_id="{root_id}", worker_session_id="{worker_id}", message="...")
   One concrete next piece of work that continues from the merged state. Ask it
   to publish the result as a pull request when it is done, and to run
   `git fetch origin && git merge --ff-only origin/{base_branch}` before editing,
   because its branch predates this merge. Do not ask it to redo merged work.

Do not spawn another worker for this round and do not touch other pull
requests. The loop stops on its own after {rounds} rounds, so you do not need
to count them. Finish with one short paragraph: what you merged and what you
asked for next."""


def _note_once(store: Any, root_id: str, key: str, kind: str, detail: str) -> None:
    """Add an event at most once per key (a parked loop must not spam the feed)."""
    try:
        if store.is_seen(key):
            return
        store.mark_seen(key)
        store.add_event(root_id, kind, detail)
    except Exception:
        pass


def arm(
    store: Any,
    session_id: str,
    *,
    enabled: bool,
    rounds: Any = None,
    settings: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Arm or disarm one root's loop; returns the updated session row.

    Raises ``ValueError`` for anything the store should not keep: a worker row,
    an archived chat, an out-of-range budget, or arming while the two publishing
    switches are off (a loop that cannot merge is a misleading switch). Turning
    the loop on from off starts a fresh round counter; raising the budget of a
    loop that is already running keeps the rounds it has spent.
    """
    current = settings if isinstance(settings, dict) else sa_settings.load_settings()
    session = store.get_session(session_id, message_limit=0)
    if session is None:
        raise ValueError(f"unknown chat session {session_id}")
    if session.get("parent_session_id") is not None:
        raise ValueError("only a router town can run a development loop")
    if session.get("archived_at") is not None:
        raise ValueError(f"session {session_id} is archived; unarchive it first")

    budget = rounds_of(session) if rounds is None else normalize_rounds(rounds, strict=True)
    if enabled:
        missing = missing_requirements(current)
        if missing:
            raise ValueError(missing_sentence(missing) + ".")

    fields: dict[str, Any] = {
        "dev_loop": 1 if enabled else 0,
        "dev_loop_rounds": budget,
    }
    if enabled and not loop_on(session):
        # Arming a fresh loop starts a fresh budget; raising the budget of a
        # loop that is already running keeps the rounds it has spent.
        fields["dev_loop_round"] = 0
    detail = (
        f"armed: {budget} round{'s' if budget != 1 else ''}"
        if enabled
        else f"off at round {rounds_used(session)} of {budget}"
    )
    # Event first, then the row: once the new state is visible, its audit trail
    # already holds the line that explains it (the same order the runner uses).
    try:
        store.add_event(session_id, "dev_loop", detail)
    except Exception:
        pass
    updated = store.update_session(session_id, **fields)
    if updated is None:
        raise ValueError(f"unknown chat session {session_id}")
    return updated


def after_turn(
    store: Any,
    session_id: str,
    *,
    runner: Any = None,
    settings: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Start this town's next loop round if one is due. Never raises.

    Called at the end of every turn (`sa_runner.Runner._finish`). It is a pull,
    not a push: any finished turn asks whether a worker PR is waiting, so a
    round interrupted by a busy Router is picked up by that Router turn's own
    finish instead of being queued twice.
    """
    try:
        current = settings if isinstance(settings, dict) else sa_settings.load_settings()
    except Exception as exc:  # a settings read cannot fail a turn
        return {"started": False, "error": str(exc)}
    try:
        session = store.get_session(session_id, message_limit=0)
    except Exception as exc:
        return {"started": False, "error": str(exc)}
    if not session:
        return {"started": False, "reason": "unknown session"}

    root_id = str(session.get("parent_session_id") or session.get("id") or "")
    try:
        root = session if root_id == session.get("id") else store.get_session(root_id, message_limit=0)
    except Exception as exc:
        return {"started": False, "error": str(exc)}
    if not root:
        return {"started": False, "reason": f"unknown router {root_id}"}
    if not loop_on(root):
        return {"started": False, "reason": "the development loop is off"}

    missing = missing_requirements(current)
    if missing:
        _note_once(
            store, root_id, f"dev_loop_blocked:{root_id}",
            "dev_loop_blocked", missing_sentence(missing) + ".",
        )
        return {"started": False, "reason": missing_sentence(missing), "missing": missing}

    if str(root.get("status") or "") in (sa_chats.QUEUED, sa_chats.RUNNING):
        # The Router is mid-turn. Its own finish runs this hook again, so the
        # waiting PR is handled then rather than by a second queued turn.
        return {"started": False, "reason": "the router is busy"}

    used = rounds_used(root)
    budget = rounds_of(root)
    if used >= budget:
        _note_once(
            store, root_id, f"dev_loop_done:{root_id}:{used}", "dev_loop_done",
            f"round {used} of {budget} finished; the loop is parked",
        )
        return {"started": False, "reason": "round budget spent", "round": used, "rounds": budget}

    due = pending_pr(store, root_id)
    if due is None:
        return {"started": False, "reason": "no worker pull request is waiting"}

    worker = due["worker"]
    worker_id = str(worker.get("id") or "")
    pr_number = int(due["pr_number"])
    prompt = build_prompt(
        root, worker,
        pr_number=pr_number,
        round_no=used + 1,
        rounds=budget,
        base=str(current.get("github_pr_base_branch") or "main"),
    )
    # Mark before launching: two finish hooks for one turn must not both start
    # a Router turn. A failed launch clears it again so the next turn retries.
    key = turn_key(worker_id, due["turn"])
    try:
        store.mark_seen(key)
    except Exception:
        pass

    try:
        target = runner if runner is not None else _shared_runner(store, current)
        target.launch_turn(
            root_id,
            prompt,
            resume_session_id=str(root.get("hermes_session_id") or "") or None,
            model=str(root.get("model") or ""),
            provider=str(root.get("provider") or ""),
            reasoning_effort=str(root.get("reasoning_effort") or ""),
            repo=str(root.get("repo") or ""),
        )
    except Exception as exc:
        try:
            store.clear_seen(key)
        except Exception:
            pass
        message = _redact(exc)
        try:
            store.add_event(root_id, "dev_loop_failed", message)
        except Exception:
            pass
        return {"started": False, "error": message}

    used += 1
    try:
        store.update_session(root_id, dev_loop_round=used)
        store.add_event(
            root_id, "dev_loop",
            f"round {used}/{budget}: PR #{pr_number} from {worker_id}",
        )
    except Exception:
        pass
    return {
        "started": True,
        "round": used,
        "rounds": budget,
        "worker_session_id": worker_id,
        "pr_number": pr_number,
    }


def _shared_runner(store: Any, settings: dict[str, Any]) -> Any:
    """The process-wide Runner. Imported here to keep sa_runner <- devloop acyclic."""
    import sa_runtime  # local import: sa_runtime imports sa_runner, which imports this

    return sa_runtime.shared_runner(store, settings)


def _redact(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    try:
        import sa_runner  # local: same cycle as _shared_runner

        return sa_runner.redact_secrets(text)
    except Exception:
        return text[:600]
