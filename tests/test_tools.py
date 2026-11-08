import json


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


