"""Tests for the router town's development loop (sa_devloop).

The loop is policy, not a new publishing power: every round ends in the same
tool calls a person would make. These tests pin the four decisions — when a
round starts, how the budget is spent, what it refuses, and what it records.
"""

from __future__ import annotations

import json
import time

import pytest


class FakeRunner:
    """Records the Router turns the loop launches."""

    def __init__(self, fail: str = ""):
        self.fail = fail
        self.launches = []

    def launch_turn(
        self, session_id, prompt, *, resume_session_id=None, model=None, provider=None,
        reasoning_effort=None, repo=None,
    ):
        if self.fail:
            raise RuntimeError(self.fail)
        self.launches.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                "resume_session_id": resume_session_id,
                "model": model,
                "provider": provider,
                "repo": repo,
            }
        )
        return {"session_id": session_id}


def loop_settings(**overrides):
    """The switches a loop needs, on, with no GitHub base branch pinned."""
    base = {
        "allow_router_pr_approve": True,
        "allow_worker_merge_pr": True,
        "github_pr_base_branch": "",
    }
    base.update(overrides)
    return base


def make_town(store, *, pr_number=42):
    """A router with one worker that finished a turn and published a PR."""
    root = store.create_session(
        "build the parser", status="idle", model="m", provider="p", repo="C:/repo",
    )
    store.update_session(root["id"], hermes_session_id="h-1")
    worker = store.create_session(
        "fix the parser", parent_session_id=root["id"], status="review",
        pr_number=pr_number, pr_url=f"https://github.com/acme/widgets/pull/{pr_number}",
        branch_name="superagent/fix-parser",
    )
    store.update_session(worker["id"], finished_at=1000.0)
    return root, store.get_session(worker["id"])


# -- the value itself ------------------------------------------------------


def test_normalize_rounds_defaults_and_rejects(sa):
    loop = sa.sa_devloop
    assert loop.normalize_rounds("") == loop.DEFAULT_ROUNDS
    assert loop.normalize_rounds(None) == loop.DEFAULT_ROUNDS
    assert loop.normalize_rounds("5") == 5
    # Lenient loading: a hand-edited bad row degrades instead of crashing.
    assert loop.normalize_rounds(0) == loop.DEFAULT_ROUNDS
    assert loop.normalize_rounds(999) == loop.DEFAULT_ROUNDS
    assert loop.normalize_rounds(True) == loop.DEFAULT_ROUNDS
    # Strict is the API/CLI path: the number the operator typed is the number
    # they meant, so a bad one is an error.
    with pytest.raises(ValueError):
        loop.normalize_rounds(0, strict=True)
    with pytest.raises(ValueError):
        loop.normalize_rounds(loop.MAX_ROUNDS + 1, strict=True)
    with pytest.raises(ValueError):
        loop.normalize_rounds("three", strict=True)


def test_blank_columns_mean_off_with_the_default_budget(sa):
    loop = sa.sa_devloop
    assert loop.loop_on({}) is False
    assert loop.rounds_of({}) == loop.DEFAULT_ROUNDS
    assert loop.rounds_used({}) == 0
    assert loop.loop_on({"dev_loop": 1}) is True


# -- store -----------------------------------------------------------------


def test_store_migrates_the_loop_columns_onto_an_old_table(tmp_path, sa):
    """An installed DB predates these columns; opening it must add them.

    The previous release's table is rebuilt from today's SCHEMA minus the three
    loop columns, so this is the real upgrade path rather than a toy table.
    """
    import sqlite3

    old_schema = "\n".join(
        line for line in sa.sa_chats.SCHEMA.splitlines()
        if not line.strip().startswith("dev_loop")
    )
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(old_schema)
    conn.commit()
    conn.close()

    store = sa.sa_chats.ChatStore(path)
    store.create_session("legacy row")
    row = store.list_sessions()["sessions"][0]
    assert row["dev_loop"] is None
    assert row["dev_loop_rounds"] is None
    assert row["dev_loop_round"] is None
    # The new columns are writable through the ordinary door.
    store.update_session(row["id"], dev_loop=1, dev_loop_rounds=2, dev_loop_round=1)
    assert store.get_session(row["id"])["dev_loop"] == 1


def test_list_children_returns_workers_oldest_first(chat, sa):
    root = chat.create_session("town")
    first = chat.create_session("one", parent_session_id=root["id"])
    second = chat.create_session("two", parent_session_id=root["id"])
    chat.set_archived(second["id"], True)

    live = [row["id"] for row in chat.list_children(root["id"])]
    everyone = [row["id"] for row in chat.list_children(root["id"], include_archived=True)]
    assert live == [first["id"]]
    assert everyone == [first["id"], second["id"]]


# -- arming ----------------------------------------------------------------


def test_arm_sets_the_budget_and_round_zero(chat, sa):
    loop = sa.sa_devloop
    root = chat.create_session("town", status="idle")
    updated = loop.arm(chat, root["id"], enabled=True, rounds=4, settings=loop_settings())
    assert updated["dev_loop"] == 1
    assert updated["dev_loop_rounds"] == 4
    assert updated["dev_loop_round"] == 0
    assert updated["events"][-1]["kind"] == "dev_loop"

    off = loop.arm(chat, root["id"], enabled=False, settings=loop_settings())
    assert off["dev_loop"] == 0


def test_arming_a_running_loop_keeps_its_progress(chat, sa):
    """Bumping the budget mid-run must not hand back the rounds already spent."""
    loop = sa.sa_devloop
    root = chat.create_session("town", status="idle")
    loop.arm(chat, root["id"], enabled=True, rounds=3, settings=loop_settings())
    chat.update_session(root["id"], dev_loop_round=2)

    bumped = loop.arm(chat, root["id"], enabled=True, rounds=6, settings=loop_settings())
    assert bumped["dev_loop_rounds"] == 6
    assert bumped["dev_loop_round"] == 2

    # Turning it off and on again is a deliberate fresh start.
    loop.arm(chat, root["id"], enabled=False, settings=loop_settings())
    fresh = loop.arm(chat, root["id"], enabled=True, rounds=6, settings=loop_settings())
    assert fresh["dev_loop_round"] == 0


def test_arm_refuses_a_worker_and_an_archived_town(chat, sa):
    loop = sa.sa_devloop
    root = chat.create_session("town")
    worker = chat.create_session("work", parent_session_id=root["id"])
    with pytest.raises(ValueError) as worker_error:
        loop.arm(chat, worker["id"], enabled=True, settings=loop_settings())
    assert "router town" in str(worker_error.value)

    chat.set_archived(root["id"], True)
    with pytest.raises(ValueError) as archived_error:
        loop.arm(chat, root["id"], enabled=True, settings=loop_settings())
    assert "archived" in str(archived_error.value)


def test_arm_refuses_while_the_publishing_switches_are_off(chat, sa):
    """A loop whose merge tool would refuse every call is a misleading switch."""
    loop = sa.sa_devloop
    root = chat.create_session("town")
    off = loop_settings(allow_worker_merge_pr=False, allow_router_pr_approve=False)

    with pytest.raises(ValueError) as excinfo:
        loop.arm(chat, root["id"], enabled=True, settings=off)
    message = str(excinfo.value)
    assert "Allow Router & workers to review/approve PRs" in message
    assert "Allow workers to comment and merge PRs" in message
    assert loop.loop_on(chat.get_session(root["id"])) is False

    # Disarming is always allowed, even after the switches were turned off.
    loop.arm(chat, root["id"], enabled=True, settings=loop_settings())
    assert loop.arm(chat, root["id"], enabled=False, settings=off)["dev_loop"] == 0


# -- what is due -----------------------------------------------------------


def test_pending_pr_finds_the_oldest_unhandled_worker_pr(chat, sa):
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    due = loop.pending_pr(chat, root["id"])
    assert due["pr_number"] == 42
    assert due["worker"]["id"] == worker["id"]

    # A round started for that turn takes it out of the queue until it finishes
    # again (its branch gets a new head, or the Router rejected the PR).
    chat.mark_seen(loop.turn_key(worker["id"], 1000.0))
    assert loop.pending_pr(chat, root["id"]) is None
    chat.update_session(worker["id"], finished_at=2000.0)
    assert loop.pending_pr(chat, root["id"])["pr_number"] == 42


def test_pending_pr_skips_a_merged_pr_and_an_unfinished_turn(chat, sa):
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    loop.record_merge(chat, root["id"], 42)
    assert loop.pending_pr(chat, root["id"]) is None

    other = chat.create_session("second", parent_session_id=root["id"], status="review", pr_number=43)
    # No finished turn yet: there is nothing to review.
    assert loop.pending_pr(chat, root["id"]) is None
    chat.update_session(other["id"], finished_at=3000.0)
    assert loop.pending_pr(chat, root["id"])["pr_number"] == 43


def test_record_merge_marks_the_worker_that_owns_the_pr(chat, sa):
    """The Router merges a worker's PR, so the key must land on the worker row."""
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    loop.record_merge(chat, root["id"], 42)
    assert chat.is_seen(loop.merged_key(worker["id"], 42))
    # A different PR for the same worker is still pending.
    assert loop.merge_recorded(chat, worker["id"], 43) is False


# -- a round ---------------------------------------------------------------


def test_after_turn_starts_one_router_round_with_the_pr_named(chat, sa):
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    loop.arm(chat, root["id"], enabled=True, rounds=3, settings=loop_settings())
    runner = FakeRunner()

    result = loop.after_turn(chat, worker["id"], runner=runner, settings=loop_settings())

    assert result["started"] is True
    assert result["round"] == 1
    assert result["rounds"] == 3
    assert result["pr_number"] == 42
    assert len(runner.launches) == 1
    launch = runner.launches[0]
    root_id = root["id"]
    worker_id = worker["id"]
    # The round is a turn on the Router itself, resumed with its own history.
    assert launch["session_id"] == root_id
    assert launch["resume_session_id"] == "h-1"
    assert launch["repo"] == "C:/repo"
    # The prompt names the exact tools and both ends of the diff.
    assert f'superagent_pr_review(session_id="{root_id}", pr_number=42' in launch["prompt"]
    assert f'superagent_pr_merge(session_id="{root_id}", pr_number=42' in launch["prompt"]
    assert (
        f'superagent_message_worker(parent_session_id="{root_id}",'
        f' worker_session_id="{worker_id}"'
    ) in launch["prompt"]
    assert "git diff origin/main...origin/" in launch["prompt"]
    assert "round 1 of 3" in launch["prompt"]

    assert chat.get_session(root["id"])["dev_loop_round"] == 1
    kinds = [event["kind"] for event in chat.get_session(root["id"])["events"]]
    assert "dev_loop" in kinds

    # The same finished turn cannot start a second round.
    again = loop.after_turn(chat, worker["id"], runner=runner, settings=loop_settings())
    assert again["started"] is False
    assert len(runner.launches) == 1


def test_loop_stops_when_the_budget_is_spent(chat, sa):
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    loop.arm(chat, root["id"], enabled=True, rounds=1, settings=loop_settings())
    runner = FakeRunner()
    assert loop.after_turn(chat, worker["id"], runner=runner, settings=loop_settings())["started"]

    # The worker finishes again after the merge: a fresh turn, but no budget.
    chat.update_session(worker["id"], finished_at=2000.0)
    result = loop.after_turn(chat, worker["id"], runner=runner, settings=loop_settings())
    assert result["started"] is False
    assert result["reason"] == "round budget spent"
    assert len(runner.launches) == 1
    kinds = [event["kind"] for event in chat.get_session(root["id"])["events"]]
    assert "dev_loop_done" in kinds


def test_a_disarmed_loop_never_launches(chat, sa):
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    runner = FakeRunner()
    result = loop.after_turn(chat, worker["id"], runner=runner, settings=loop_settings())
    assert result["started"] is False
    assert runner.launches == []


def test_a_busy_router_waits_and_its_own_finish_drains_the_queue(chat, sa):
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    loop.arm(chat, root["id"], enabled=True, rounds=3, settings=loop_settings())
    chat.update_session(root["id"], status="running")
    runner = FakeRunner()

    skipped = loop.after_turn(chat, worker["id"], runner=runner, settings=loop_settings())
    assert skipped["started"] is False
    assert skipped["reason"] == "the router is busy"
    assert runner.launches == []

    # The Router finishes; its own hook finds the waiting PR.
    chat.update_session(root["id"], status="review")
    drained = loop.after_turn(chat, root["id"], runner=runner, settings=loop_settings())
    assert drained["started"] is True
    assert len(runner.launches) == 1


def test_missing_switches_block_with_an_event_and_no_turn(chat, sa):
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    chat.update_session(root["id"], dev_loop=1, dev_loop_rounds=3, dev_loop_round=0)
    runner = FakeRunner()
    off = loop_settings(allow_router_pr_approve=False)

    result = loop.after_turn(chat, worker["id"], runner=runner, settings=off)
    assert result["started"] is False
    assert result["missing"] == ["allow_router_pr_approve"]
    assert runner.launches == []
    kinds = [event["kind"] for event in chat.get_session(root["id"])["events"]]
    assert kinds.count("dev_loop_blocked") == 1

    # A second attempt does not repeat the same event.
    loop.after_turn(chat, worker["id"], runner=runner, settings=off)
    kinds = [event["kind"] for event in chat.get_session(root["id"])["events"]]
    assert kinds.count("dev_loop_blocked") == 1


def test_a_failed_launch_leaves_the_round_for_the_next_turn(chat, sa):
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    loop.arm(chat, root["id"], enabled=True, rounds=3, settings=loop_settings())

    broken = FakeRunner(fail="hermes missing")
    result = loop.after_turn(chat, worker["id"], runner=broken, settings=loop_settings())
    assert result["started"] is False
    assert "hermes missing" in result["error"]
    assert chat.get_session(root["id"])["dev_loop_round"] == 0
    kinds = [event["kind"] for event in chat.get_session(root["id"])["events"]]
    assert "dev_loop_failed" in kinds

    # The dedupe key was cleared, so a later attempt can still run this turn.
    working = FakeRunner()
    retried = loop.after_turn(chat, worker["id"], runner=working, settings=loop_settings())
    assert retried["started"] is True


def test_after_turn_never_raises_on_an_unknown_session(chat, sa):
    result = sa.sa_devloop.after_turn(chat, "S-9999", runner=FakeRunner(), settings=loop_settings())
    assert result["started"] is False


# -- the merge record ------------------------------------------------------


def test_merging_through_the_tool_marks_the_pr_for_the_loop(chat, sa, monkeypatch, tmp_path):
    """The tool's success is what stops a later worker turn re-reviewing it."""
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    # The tool resolves its store through sa_settings.data_dir(); point that at
    # the same throwaway database the fixture uses.
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: loop_settings(
        github_repo="acme/widgets", github_token="tok",
    ))
    monkeypatch.setattr(
        sa.sa_github.GitHubClient, "get_pr",
        lambda self, number: {
            "number": number, "state": "open", "draft": False,
            "mergeable": True, "mergeable_state": "clean", "merged": False,
        },
    )
    monkeypatch.setattr(
        sa.sa_github.GitHubClient, "post_comment",
        lambda self, number, body: {"id": 9, "html_url": f"https://github.com/acme/widgets/pull/{number}"},
    )
    monkeypatch.setattr(
        sa.sa_github.GitHubClient, "merge_pr",
        lambda self, number, **kwargs: {"merged": True, "sha": "abc", "message": "ok"},
    )

    out = json.loads(sa.sa_tools.superagent_pr_merge(
        {"session_id": root["id"], "pr_number": 42, "body": "LGTM"}, ctx=object(),
    ))

    assert out["ok"] is True, out
    assert chat.is_seen(loop.merged_key(worker["id"], 42))
    assert loop.pending_pr(chat, root["id"]) is None


def test_two_rounds_walk_the_counter_to_the_budget(chat, sa):
    """The happy path: PR -> merge -> next PR -> merge -> parked, two rounds."""
    loop = sa.sa_devloop
    root, worker = make_town(chat)
    loop.arm(chat, root["id"], enabled=True, rounds=2, settings=loop_settings())
    runner = FakeRunner()

    # Round 1: the worker's first PR lands and the Router asks for the next step.
    first = loop.after_turn(chat, worker["id"], runner=runner, settings=loop_settings())
    assert first["round"] == 1
    loop.record_merge(chat, root["id"], 42)
    chat.update_session(root["id"], status="idle")

    # The worker continues in the same worktree and opens PR #43.
    chat.update_session(worker["id"], pr_number=43, finished_at=2000.0, status="review")
    second = loop.after_turn(chat, worker["id"], runner=runner, settings=loop_settings())
    assert second["round"] == 2
    assert "round 2 of 2" in runner.launches[-1]["prompt"]

    # A third PR has no budget left, so the loop parks itself.
    loop.record_merge(chat, root["id"], 43)
    chat.update_session(root["id"], status="idle")
    chat.update_session(worker["id"], pr_number=44, finished_at=3000.0, status="review")
    third = loop.after_turn(chat, worker["id"], runner=runner, settings=loop_settings())
    assert third["started"] is False
    assert third["reason"] == "round budget spent"
    assert len(runner.launches) == 2


# -- the panel mirrors the backend ----------------------------------------


def test_the_panel_mirrors_the_round_bounds(sa):
    """The number input's bounds and the backend's ceiling are one fact.

    There is no JS test harness, so this is a text check: a divergence here
    means the panel offers a budget the API rejects.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "desktop" / "plugin.js").read_text(
        encoding="utf-8"
    )
    loop = sa.sa_devloop
    assert f"const DEV_LOOP_DEFAULT_ROUNDS = {loop.DEFAULT_ROUNDS}" in source
    assert f"const DEV_LOOP_MIN_ROUNDS = {loop.MIN_ROUNDS}" in source
    assert f"const DEV_LOOP_MAX_ROUNDS = {loop.MAX_ROUNDS}" in source
    # The control is wired to the route its backend serves.
    assert "/chats/${session.id}/dev-loop" in source
    assert '"/chats/{session_id}/dev-loop"' in (
        Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
    ).read_text(encoding="utf-8")



def test_runner_hook_delegates_and_swallows_errors(chat, sa, monkeypatch):
    runner = sa.sa_runner.Runner(chat)
    seen = []
    monkeypatch.setattr(
        sa.sa_devloop, "after_turn",
        lambda store, session_id, **kwargs: seen.append((store, session_id)) or {"started": False},
    )
    runner._continue_dev_loop("S-0001")
    assert seen and seen[0][1] == "S-0001"

    def boom(*args, **kwargs):
        raise RuntimeError("loop exploded")

    monkeypatch.setattr(sa.sa_devloop, "after_turn", boom)
    runner._continue_dev_loop("S-0001")  # must not raise
