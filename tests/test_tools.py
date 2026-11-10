import json

import pytest


class FakeCtx:
    def __init__(self, config=None):
        self.config = config or {"mode": "observe"}
        self.subagent_lifecycle = None

    def get_config(self, key, default=None):
        return self.config.get(key, default)


def test_ask_records_an_observed_ticket(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_engine, "_db_path", lambda: tmp_path / "db.sqlite")
    result = json.loads(sa.sa_tools.superagent_ask({"request": "Fix the parser crash"}, ctx=FakeCtx()))
    assert result["ok"] is True
    assert result["ticket"]["status"] == "observed"


def test_ask_rejects_an_empty_request(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_engine, "_db_path", lambda: tmp_path / "db.sqlite")
    result = json.loads(sa.sa_tools.superagent_ask({"request": "  "}, ctx=FakeCtx()))
    assert "error" in result


def test_status_reports_counts(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_engine, "_db_path", lambda: tmp_path / "db.sqlite")
    ctx = FakeCtx()
    sa.sa_tools.superagent_ask({"request": "Fix the parser crash"}, ctx=ctx)
    result = json.loads(sa.sa_tools.superagent_status({}, ctx=ctx))
    assert result["mode"] == "observe"
    assert result["counts"]["observed"] == 1


def test_tick_without_a_parent_still_reports(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_engine, "_db_path", lambda: tmp_path / "db.sqlite")
    ctx = FakeCtx({"mode": "auto"})
    sa.sa_tools.superagent_ask({"request": "Fix the parser crash"}, ctx=ctx)
    result = json.loads(sa.sa_tools.superagent_tick({}, ctx=ctx))
    assert result["tick"]["mode"] == "auto"
    assert result["status"]["queued"] == 1


def test_handlers_never_raise(tmp_path, monkeypatch, sa):
    def boom(_ctx):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(sa.sa_engine, "build_engine", boom)
    result = json.loads(sa.sa_tools.superagent_status({}, ctx=FakeCtx()))
    assert "database exploded" in result["error"]


def test_register_wires_tools_commands_blueprint_skill_and_docs(sa):
    from pathlib import Path

    class RecordingCtx(FakeCtx):
        def __init__(self):
            super().__init__()
            self.tools = {}
            self.commands = {}
            self.cli_commands = {}
            self.blueprints = {}
            self.skills = {}

        def register_tool(self, *, name, toolset, schema, handler):
            self.tools[name] = (toolset, schema, handler)

        def register_command(self, name, *, handler, description, args_hint=""):
            self.commands[name] = (handler, description, args_hint)

        def register_cli_command(self, *, name, help, setup_fn, handler_fn):
            self.cli_commands[name] = (help, setup_fn, handler_fn)

        def register_automation_blueprint(self, name, **kwargs):
            self.blueprints[name] = kwargs

        def register_skill(self, name, skill_md):
            self.skills[name] = Path(skill_md)

    ctx = RecordingCtx()
    sa.register(ctx)

    assert set(ctx.tools) == {
        "superagent_ask", "superagent_tick", "superagent_status",
        "superagent_run", "superagent_spawn", "superagent_town_status",
        "superagent_message_worker", "superagent_pr_create", "superagent_pr_review",
        "superagent_pr_merge",
    }
    assert all(toolset == "superagent" for toolset, _, _ in ctx.tools.values())
    assert "superagent" in ctx.commands
    assert "superagent" in ctx.cli_commands
    assert "watch" in ctx.blueprints
    assert "superagent-operator" in ctx.skills
    assert ctx.skills["superagent-operator"].is_file()

    root = Path(sa.__file__).resolve().parent
    assert (root / "README.md").is_file()
    assert (root / "DESIGN.md").is_file()


def test_slash_command_subcommands(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_engine, "_db_path", lambda: tmp_path / "db.sqlite")
    ctx = FakeCtx({"mode": "observe"})

    assert "Usage: /superagent" in sa.slash_command("help", ctx)
    assert "Usage: /superagent ask" in sa.slash_command("ask", ctx)
    assert "Usage: /superagent run" in sa.slash_command("run", ctx)
    assert "Unknown subcommand" in sa.slash_command("nonexistent", ctx)

    asked = sa.slash_command("ask Fix the crash in parser.py", ctx)
    ticket_id = asked.split()[0]
    assert "recorded as observed" in asked

    status_out = sa.slash_command("status", ctx)
    assert ticket_id in status_out

    run_out = sa.slash_command(f"run {ticket_id}", ctx)
    assert ticket_id in run_out
    assert "queued" in run_out

    tick_out = sa.slash_command("tick", ctx)
    assert "polled=" in tick_out


def test_town_status_and_message_worker(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: dict(sa.sa_settings.DEFAULTS))
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    root = store.create_session("router town")
    worker = store.create_session("fix parser", parent_session_id=root["id"], status="review")
    store.add_message(worker["id"], "assistant", "Fixed the parser bug.")

    status_res = json.loads(
        sa.sa_tools.superagent_town_status(
            {"parent_session_id": root["id"], "include_diff": False}, ctx=FakeCtx()
        )
    )
    assert status_res["ok"] is True
    assert status_res["town"]["worker_count"] == 1
    assert status_res["workers"][0]["id"] == worker["id"]
    assert status_res["workers"][0]["last_assistant_message"] == "Fixed the parser bug."

    launches = []

    class DummyRunner:
        def launch_turn(self, session_id, prompt, **kwargs):
            launches.append((session_id, prompt, kwargs))
            store.update_session(session_id, status="running")

    monkeypatch.setattr(sa.sa_runtime, "shared_runner", lambda _store: DummyRunner())

    # approve_pr blocked when allow_router_pr_approve is False
    denied = json.loads(
        sa.sa_tools.superagent_message_worker(
            {
                "parent_session_id": root["id"],
                "worker_session_id": worker["id"],
                "message": "approve it",
                "action": "approve_pr",
            },
            ctx=FakeCtx(),
        )
    )
    assert denied["ok"] is False
    assert "allow_router_pr_approve" in denied["error"]

    # Normal follow-up succeeds
    sent = json.loads(
        sa.sa_tools.superagent_message_worker(
            {
                "parent_session_id": root["id"],
                "worker_session_id": worker["id"],
                "message": "add one more unit test",
            },
            ctx=FakeCtx(),
        )
    )
    assert sent["ok"] is True
    assert len(launches) == 1
    assert launches[0][0] == worker["id"]
    assert "add one more unit test" in launches[0][1]


def test_pr_create_and_pr_review_tools(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    root = store.create_session("router")
    worker = store.create_session("worker", parent_session_id=root["id"], repo=str(tmp_path))

    # Disabled by default
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: dict(sa.sa_settings.DEFAULTS))
    disabled_pr = json.loads(
        sa.sa_tools.superagent_pr_create(
            {"session_id": worker["id"], "title": "Fix bug"}, ctx=FakeCtx()
        )
    )
    assert disabled_pr["ok"] is False
    assert "allow_worker_pr_create" in disabled_pr["error"]

    disabled_rev = json.loads(
        sa.sa_tools.superagent_pr_review(
            {"session_id": root["id"], "pr_number": 7, "event": "APPROVE", "body": "LGTM"},
            ctx=FakeCtx(),
        )
    )
    assert disabled_rev["ok"] is False
    assert "allow_router_pr_approve" in disabled_rev["error"]

    # Enable both settings and mock git + GitHubClient
    enabled = dict(
        sa.sa_settings.DEFAULTS,
        allow_worker_pr_create=True,
        allow_router_pr_approve=True,
        github_repo="acme/widgets",
        github_token="ghp_testtoken123456",
        github_pr_base_branch="main",
    )
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: enabled)
    monkeypatch.setattr(
        sa.sa_github,
        "commit_and_push_branch",
        lambda *a, **kw: {"branch": kw["branch"], "sha": "abc1234", "committed": True, "pushed": True},
    )
    monkeypatch.setattr(
        sa.sa_github.GitHubClient,
        "create_pr",
        lambda self, **kw: {
            "number": 42,
            "title": kw["title"],
            "html_url": "https://github.com/acme/widgets/pull/42",
            "head_ref": kw["head"],
            "base_ref": kw["base"],
            "draft": kw["draft"],
        },
    )
    monkeypatch.setattr(
        sa.sa_github.GitHubClient,
        "create_review",
        lambda self, number, event="COMMENT", body="": {
            "id": 77,
            "state": event,
            "html_url": f"https://github.com/acme/widgets/pull/{number}#pullrequestreview-77",
            "fallback": False,
        },
    )

    created = json.loads(
        sa.sa_tools.superagent_pr_create(
            {"session_id": worker["id"], "title": "Fix parser crash", "body": "Closes #1"},
            ctx=FakeCtx(),
        )
    )
    assert created["ok"] is True
    assert created["pr"]["number"] == 42
    assert created["session"]["pr_number"] == 42

    reviewed = json.loads(
        sa.sa_tools.superagent_pr_review(
            {"session_id": root["id"], "pr_number": 42, "event": "APPROVE", "body": "Ship it"},
            ctx=FakeCtx(),
        )
    )
    assert reviewed["ok"] is True
    assert reviewed["review"]["state"] == "APPROVE"


# -- spawn tool: worker model resolution ------------------------------------


class SpawnRunner:
    """Records launch arguments; the real Runner is exercised elsewhere."""

    def __init__(self):
        self.launches = []

    def launch_turn(self, session_id, prompt, **kwargs):
        self.launches.append({"session_id": session_id, "prompt": prompt, **kwargs})


@pytest.fixture
def spawn_env(tmp_path, monkeypatch, sa):
    """A worker town in a throwaway data dir, with a recording runner."""
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    for name in sa.sa_settings.ENV_VARS.values():
        monkeypatch.delenv(name, raising=False)
    sa.sa_settings.clear_cache()
    runner = SpawnRunner()
    monkeypatch.setattr(sa.sa_runtime, "shared_runner", lambda _store: runner)
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    yield store, runner
    sa.sa_settings.clear_cache()


def test_spawn_tool_resolves_worker_model_before_the_parent(spawn_env, sa):
    store, runner = spawn_env
    root = store.create_session(
        "router",
        model="router-model",
        provider="router-provider",
        reasoning_effort="high",
        repo="",
    )
    sa.sa_settings.save_settings(
        {
            "worker_model": "cheap/worker-model",
            "worker_provider": "cheap-provider",
            "worker_reasoning_effort": "low",
        }
    )

    first = json.loads(
        sa.sa_tools.superagent_spawn(
            {"parent_session_id": root["id"], "prompt": "fix the parser"}, ctx=object()
        )
    )
    assert first["ok"] is True
    child = first["session"]
    assert child["model"] == "cheap/worker-model"
    assert child["provider"] == "cheap-provider"
    assert child["reasoning_effort"] == "low"
    assert runner.launches[-1]["model"] == "cheap/worker-model"
    assert runner.launches[-1]["provider"] == "cheap-provider"
    assert runner.launches[-1]["reasoning_effort"] == "low"

    # A per-spawn argument beats the setting.
    second = json.loads(
        sa.sa_tools.superagent_spawn(
            {
                "parent_session_id": root["id"],
                "prompt": "one-off job",
                "model": "one-off-model",
                "provider": "one-off-provider",
                "reasoning_effort": "max",
            },
            ctx=object(),
        )
    )
    assert second["ok"] is True
    assert second["session"]["model"] == "one-off-model"
    assert second["session"]["provider"] == "one-off-provider"
    assert second["session"]["reasoning_effort"] == "max"

    # Empty settings restore the pre-feature inheritance from the router.
    sa.sa_settings.save_settings(
        {"worker_model": "", "worker_provider": "", "worker_reasoning_effort": ""}
    )
    third = json.loads(
        sa.sa_tools.superagent_spawn(
            {"parent_session_id": root["id"], "prompt": "inherit"}, ctx=object()
        )
    )
    assert third["session"]["model"] == "router-model"
    assert third["session"]["provider"] == "router-provider"
    assert third["session"]["reasoning_effort"] == "high"


def test_spawn_tool_stores_the_worker_harness_setting(spawn_env, sa):
    store, runner = spawn_env
    root = store.create_session("router")

    # The default is the bash-only mini harness.
    first = json.loads(
        sa.sa_tools.superagent_spawn(
            {"parent_session_id": root["id"], "prompt": "fix the parser"}, ctx=object()
        )
    )
    assert first["ok"] is True
    assert first["session"]["harness"] == "mini"

    # Switching the town setting changes the stored row for the next worker.
    sa.sa_settings.save_settings({"worker_harness": "hermes"})
    second = json.loads(
        sa.sa_tools.superagent_spawn(
            {"parent_session_id": root["id"], "prompt": "full-toolset job"}, ctx=object()
        )
    )
    assert second["session"]["harness"] == "hermes"
    assert store.get_session(second["session"]["id"], message_limit=0)["harness"] == "hermes"


def test_spawn_tool_rejects_a_bad_effort_without_creating_a_row(spawn_env, sa):
    store, runner = spawn_env
    root = store.create_session("router")

    result = json.loads(
        sa.sa_tools.superagent_spawn(
            {
                "parent_session_id": root["id"],
                "prompt": "work",
                "reasoning_effort": "infinite",
            },
            ctx=object(),
        )
    )
    assert "reasoning_effort must be one of" in result["error"]
    assert "session" not in result
    assert runner.launches == []
    assert [row["id"] for row in store.list_sessions()["sessions"]] == [root["id"]]


def test_spawn_tool_refuses_a_contributor_model_before_creating_a_row(
    spawn_env, sa, monkeypatch
):
    store, runner = spawn_env
    root = store.create_session("router")
    monkeypatch.setattr(sa.sa_model_policy, "host_allows_data_tiers", lambda: False)
    sa.sa_settings.save_settings({"worker_model": "muse-spark-1.3-contributor"})

    from_settings = json.loads(
        sa.sa_tools.superagent_spawn(
            {"parent_session_id": root["id"], "prompt": "work"}, ctx=object()
        )
    )
    assert "contributor" in from_settings["error"]
    assert "session" not in from_settings

    # An explicit per-spawn model is checked the same way.
    sa.sa_settings.save_settings({"worker_model": ""})
    from_arg = json.loads(
        sa.sa_tools.superagent_spawn(
            {
                "parent_session_id": root["id"],
                "prompt": "work",
                "model": "meta/muse-spark-1.3-contributor",
            },
            ctx=object(),
        )
    )
    assert "contributor" in from_arg["error"]

    assert runner.launches == []
    assert [row["id"] for row in store.list_sessions()["sessions"]] == [root["id"]]




# -- merge tool ------------------------------------------------------------


def _review_session(store, **overrides):
    data = {"pr_number": 42, "github_repo": "acme/widgets", "source": "github_pr"}
    data.update(overrides)
    return store.create_session("review the pull request", **data)


def test_pr_merge_is_gated_by_its_own_setting(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    row = _review_session(store)
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: dict(sa.sa_settings.DEFAULTS))

    out = json.loads(
        sa.sa_tools.superagent_pr_merge(
            {"session_id": row["id"], "body": "LGTM"}, ctx=FakeCtx()
        )
    )

    assert out["ok"] is False
    assert "allow_worker_merge_pr" in out["error"]


def test_pr_merge_posts_the_comment_then_merges(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    row = _review_session(store)
    enabled = dict(
        sa.sa_settings.DEFAULTS,
        allow_worker_merge_pr=True,
        github_repo="acme/widgets",
        github_token="tok",
    )
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: enabled)

    order = []

    def fake_get_pr(self, number):
        order.append("get_pr")
        return {
            "number": number,
            "state": "open",
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "merged": False,
        }

    def fake_comment(self, number, body):
        order.append("comment")
        return {"id": 9, "html_url": f"https://github.com/acme/widgets/pull/{number}#issuecomment-9"}

    def fake_merge(self, number, *, merge_method="merge", commit_title="", commit_message=""):
        order.append(("merge", merge_method))
        return {"merged": True, "sha": "abc123", "message": "Pull Request successfully merged"}

    monkeypatch.setattr(sa.sa_github.GitHubClient, "get_pr", fake_get_pr)
    monkeypatch.setattr(sa.sa_github.GitHubClient, "post_comment", fake_comment)
    monkeypatch.setattr(sa.sa_github.GitHubClient, "merge_pr", fake_merge)

    out = json.loads(
        sa.sa_tools.superagent_pr_merge(
            {"session_id": row["id"], "body": "LGTM, merging", "merge_method": "squash"},
            ctx=FakeCtx(),
        )
    )

    assert out["ok"] is True
    assert out["merged"] is True
    assert out["sha"] == "abc123"
    # The comment is posted before the merge, so it survives a refusal.
    assert order == ["get_pr", "comment", ("merge", "squash")]
    kinds = [event["kind"] for event in store.get_session(row["id"])["events"]]
    assert "pr_commented" in kinds
    assert "pr_merged" in kinds
    # The verdict comment is the thread's one reply for this session.
    assert store.is_seen(sa.sa_replies.reply_key(row["id"]))


def test_pr_merge_refuses_a_draft(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    row = _review_session(store)
    enabled = dict(sa.sa_settings.DEFAULTS, allow_worker_merge_pr=True, github_token="tok")
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: enabled)
    monkeypatch.setattr(
        sa.sa_github.GitHubClient,
        "get_pr",
        lambda self, number: {"number": number, "state": "open", "draft": True, "mergeable": True},
    )
    monkeypatch.setattr(
        sa.sa_github.GitHubClient,
        "merge_pr",
        pytest.fail,  # a refused PR must never reach the merge call
    )

    out = json.loads(
        sa.sa_tools.superagent_pr_merge({"session_id": row["id"]}, ctx=FakeCtx())
    )

    assert out["ok"] is False
    assert "draft" in out["error"]


def test_pr_merge_refuses_an_unmergeable_pr(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    row = _review_session(store)
    enabled = dict(sa.sa_settings.DEFAULTS, allow_worker_merge_pr=True, github_token="tok")
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: enabled)
    monkeypatch.setattr(
        sa.sa_github.GitHubClient,
        "get_pr",
        lambda self, number: {
            "number": number,
            "state": "open",
            "draft": False,
            "mergeable": False,
            "mergeable_state": "dirty",
        },
    )
    monkeypatch.setattr(sa.sa_github.GitHubClient, "merge_pr", pytest.fail)

    out = json.loads(
        sa.sa_tools.superagent_pr_merge({"session_id": row["id"]}, ctx=FakeCtx())
    )

    assert out["ok"] is False
    assert "dirty" in out["error"]


def test_pr_merge_reports_a_github_refusal(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    row = _review_session(store)
    enabled = dict(sa.sa_settings.DEFAULTS, allow_worker_merge_pr=True, github_token="tok")
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: enabled)
    monkeypatch.setattr(
        sa.sa_github.GitHubClient,
        "get_pr",
        lambda self, number: {"number": number, "state": "open", "draft": False, "mergeable": True},
    )
    monkeypatch.setattr(
        sa.sa_github.GitHubClient,
        "merge_pr",
        lambda self, number, **kw: {
            "merged": False,
            "sha": "",
            "message": "Pull Request is not mergeable",
        },
    )

    out = json.loads(
        sa.sa_tools.superagent_pr_merge({"session_id": row["id"]}, ctx=FakeCtx())
    )

    assert out["ok"] is False
    assert "not mergeable" in out["error"]
    kinds = [event["kind"] for event in store.get_session(row["id"])["events"]]
    assert "pr_merge_refused" in kinds


def test_pr_merge_needs_a_linked_pull_request(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    row = store.create_session("just a chat")
    enabled = dict(sa.sa_settings.DEFAULTS, allow_worker_merge_pr=True, github_token="tok")
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: enabled)

    out = json.loads(
        sa.sa_tools.superagent_pr_merge({"session_id": row["id"]}, ctx=FakeCtx())
    )

    assert out["ok"] is False
    assert "no pull request" in out["error"]


def test_an_issue_session_can_resolve_itself_when_its_switch_is_on(tmp_path, monkeypatch, sa):
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    store = sa.sa_chats.ChatStore(tmp_path / "superagent.db")
    issue = store.create_session(
        "fix the crash", source="github_issue", issue_number=3, repo=str(tmp_path)
    )
    pr_session = store.create_session(
        "fix the crash", source="github_pr", pr_number=4, repo=str(tmp_path)
    )
    settings = dict(
        sa.sa_settings.DEFAULTS,
        allow_issue_pr_create=True,
        github_repo="acme/widgets",
        github_token="tok",
        github_pr_base_branch="main",
    )
    monkeypatch.setattr(sa.sa_settings, "load_settings", lambda: settings)
    monkeypatch.setattr(
        sa.sa_github,
        "commit_and_push_branch",
        lambda *a, **kw: {"branch": kw["branch"], "sha": "abc1234", "committed": True, "pushed": True},
    )
    monkeypatch.setattr(
        sa.sa_github.GitHubClient,
        "create_pr",
        lambda self, **kw: {
            "number": 42,
            "title": kw["title"],
            "html_url": "https://github.com/acme/widgets/pull/42",
            "head_ref": kw["head"],
            "base_ref": kw["base"],
            "draft": kw["draft"],
        },
    )

    refused = json.loads(
        sa.sa_tools.superagent_pr_create(
            {"session_id": pr_session["id"], "title": "Fix"}, ctx=FakeCtx()
        )
    )
    assert refused["ok"] is False
    assert "allow_worker_pr_create" in refused["error"]

    resolved = json.loads(
        sa.sa_tools.superagent_pr_create(
            {"session_id": issue["id"], "title": "Fix the crash", "body": "Closes #3"},
            ctx=FakeCtx(),
        )
    )
    assert resolved["ok"] is True
    assert resolved["pr"]["number"] == 42
