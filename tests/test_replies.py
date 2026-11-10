"""Tests for the reply-on-finish path (sa_replies.py) and its runner hook.

No network: the GitHub client is a canned factory and the store is a small
recording double, because what matters here is the decision — is a reply
allowed, is it the right thread, and does it happen once per session.
"""

from __future__ import annotations

import json

import pytest

from sa_plugin import sa_replies


def session(**overrides):
    data = {
        "id": "S-0001",
        "title": "PR #7: Add parser tests",
        "source": "github_pr",
        "github_repo": "owner/name",
        "pr_number": 7,
        "issue_number": None,
        "payload": None,
        "prompt": "Work on GitHub pull request #7.",
        "status": "review",
        "messages": [
            {"role": "user", "content": "Work on GitHub pull request #7."},
            {"role": "assistant", "content": "Fixed the parser and ran the tests."},
        ],
    }
    data.update(overrides)
    return data


class FakeStore:
    def __init__(self, row):
        self.row = row
        self.seen = set()
        self.events = []

    def get_session(self, session_id, message_limit=200, event_limit=50):
        if session_id != self.row.get("id"):
            return None
        return dict(self.row)

    def is_seen(self, key):
        return key in self.seen

    def mark_seen(self, key):
        self.seen.add(key)

    def add_event(self, session_id, kind, detail):
        self.events.append((kind, detail))


class FakeClient:
    instances: list["FakeClient"] = []
    fail = ""
    posted: list[tuple[int, str]] = []

    def __init__(self, token, repo):
        self.token = token
        self.repo = repo
        FakeClient.instances.append(self)

    def post_comment(self, number, body):
        if FakeClient.fail:
            raise RuntimeError(FakeClient.fail)
        FakeClient.posted.append((number, body))
        return {"id": 1, "html_url": f"https://github.com/{self.repo}/issues/{number}#c1"}


def settings(**overrides):
    data = {
        "github_reply_on_finish": True,
        "github_repo": "owner/name",
        "github_token": "ghp_reply_secret",
        "github_agent_login": "agent",
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def _reset():
    FakeClient.instances = []
    FakeClient.fail = ""
    FakeClient.posted = []


def test_no_reply_when_the_setting_is_off(sa):
    store = FakeStore(session())
    result = sa_replies.reply_after_turn(
        store,
        "S-0001",
        settings=settings(github_reply_on_finish=False),
        client_factory=pytest.fail,  # a disabled reply must not build a client
    )
    assert result["posted"] is False
    assert "off" in result["reason"]
    assert FakeClient.posted == []


def test_a_finished_pr_comment_is_posted_once(sa):
    store = FakeStore(session())
    result = sa_replies.reply_after_turn(
        store, "S-0001", settings=settings(), client_factory=FakeClient
    )

    assert result["posted"] is True
    assert result["number"] == 7
    assert len(FakeClient.posted) == 1
    number, body = FakeClient.posted[0]
    assert number == 7
    assert "Superagent finished S-0001" in body
    assert "Fixed the parser and ran the tests." in body
    assert sa_replies.reply_key("S-0001") in store.seen
    assert [kind for kind, _ in store.events] == ["replied"]

    # A follow-up turn must not re-post on the same thread.
    again = sa_replies.reply_after_turn(
        store, "S-0001", settings=settings(), client_factory=FakeClient
    )
    assert again["posted"] is False
    assert "already replied" in again["reason"]
    assert len(FakeClient.posted) == 1


def test_an_issue_reply_goes_to_the_issue_number(sa):
    store = FakeStore(session(source="github_issue", pr_number=None, issue_number=3))
    result = sa_replies.reply_after_turn(
        store, "S-0001", settings=settings(), client_factory=FakeClient
    )
    assert result["posted"] is True
    assert FakeClient.posted[0][0] == 3


def test_a_non_github_session_is_skipped(sa):
    store = FakeStore(session(source="desktop", pr_number=None))
    result = sa_replies.reply_after_turn(
        store, "S-0001", settings=settings(), client_factory=pytest.fail
    )
    assert result["posted"] is False
    assert "GitHub thread" in result["reason"]


def test_a_thread_without_a_number_is_skipped(sa):
    store = FakeStore(session(pr_number=None, issue_number=None))
    result = sa_replies.reply_after_turn(
        store, "S-0001", settings=settings(), client_factory=pytest.fail
    )
    assert result["posted"] is False
    assert "no issue or PR number" in result["reason"]


def test_a_missing_token_is_reported_not_raised(sa):
    store = FakeStore(session())
    result = sa_replies.reply_after_turn(
        store,
        "S-0001",
        settings=settings(github_token=""),
        client_factory=pytest.fail,
    )
    assert result["posted"] is False
    assert "not connected" in result["reason"]


def test_a_failed_comment_becomes_an_event_and_never_raises(sa):
    FakeClient.fail = "GitHub 403: Resource not accessible by personal access token (ghp_reply_secret)"
    store = FakeStore(session())

    result = sa_replies.reply_after_turn(
        store, "S-0001", settings=settings(), client_factory=FakeClient
    )

    assert result["posted"] is False
    assert "403" in result["error"]
    assert "ghp_reply_secret" not in json.dumps(result)
    assert [kind for kind, _ in store.events] == ["reply_failed"]
    assert sa_replies.reply_key("S-0001") not in store.seen
    assert FakeClient.posted == []


# -- runner wiring -------------------------------------------------------


class StubRunner:
    chat = object()
    events: list = []

    def _add_event(self, session_id, kind, detail):
        self.events.append((kind, detail))


def test_the_runner_hook_calls_the_reply(sa, monkeypatch):
    calls = []
    monkeypatch.setattr(
        sa.sa_replies, "reply_after_turn", lambda store, sid: calls.append(sid)
    )
    runner = StubRunner()
    sa.sa_runner.Runner._reply_on_github(runner, "S-0009")
    assert calls == ["S-0009"]
    assert runner.events == []


def test_the_runner_hook_never_raises(sa, monkeypatch):
    def boom(store, sid):
        raise RuntimeError("github is down")

    monkeypatch.setattr(sa.sa_replies, "reply_after_turn", boom)
    runner = StubRunner()
    sa.sa_runner.Runner._reply_on_github(runner, "S-0009")  # must not propagate
    assert runner.events == [("reply_failed", "github is down")]


def test_a_real_store_row_produces_the_preview_body(sa, tmp_path):
    """The reply is the same composition the Comment panel previews."""
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    row = store.create_session(
        "Work on GitHub pull request #7.",
        title="PR #7: Add parser tests",
        source="github_pr",
        github_repo="owner/name",
        pr_number=7,
        status=sa.sa_chats.REVIEW,
    )
    store.add_message(row["id"], "assistant", "Fixed the parser; the suite is green.")

    result = sa_replies.reply_after_turn(
        store, row["id"], settings=settings(), client_factory=FakeClient
    )

    assert result["posted"] is True
    number, body = FakeClient.posted[0]
    assert number == 7
    assert body.startswith("Superagent finished " + row["id"])
    assert "Fixed the parser; the suite is green." in body
    assert body.rstrip().endswith("_Generated by Superagent._")
