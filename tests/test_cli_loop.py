"""CLI tests for `hermes superagent loop`.

Offline like the other CLI tests: a throwaway HERMES_HOME, so the merged
settings are the defaults and the two publishing switches start off.
"""

from __future__ import annotations

import json
import types

import pytest


def _args(**kwargs):
    base = dict(
        superagent_command="loop",
        session_id="",
        enable=False,
        disable=False,
        rounds=None,
        json=False,
    )
    base.update(kwargs)
    return types.SimpleNamespace(**base)


@pytest.fixture
def cli(tmp_path, monkeypatch, sa):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setattr(sa.sa_settings, "_hermes_home", lambda: home)
    monkeypatch.setattr(sa.sa_settings, "config_path", lambda: home / "config.yaml")
    for name in sa.sa_settings.ENV_VARS.values():
        monkeypatch.delenv(name, raising=False)
    sa.sa_settings.clear_cache()

    db = tmp_path / "superagent.db"
    monkeypatch.setattr(sa.sa_cli, "_db_path", lambda: db)
    yield types.SimpleNamespace(sa=sa, home=home, db=db, store=sa.sa_chats.ChatStore(db))
    sa.sa_settings.clear_cache()


def town(cli, status="idle"):
    return cli.store.create_session("build the parser", title="build the parser", status=status)


def arm_settings(cli):
    """The host switches a loop needs; the CLI reads them through settings."""
    cli.sa.sa_settings.save_settings({
        "allow_router_pr_approve": True,
        "allow_worker_merge_pr": True,
    })


# -- show ------------------------------------------------------------------


def test_loop_command_shows_the_default_off_state(cli, capsys):
    root = town(cli)
    rc = cli.sa.sa_cli.run(_args(session_id=root["id"]), object())
    out = capsys.readouterr().out
    assert rc == 0
    assert "development loop: off" in out
    assert "3 round budget" in out


def test_loop_json_reports_the_loop_block(cli, capsys):
    root = town(cli)
    rc = cli.sa.sa_cli.run(_args(session_id=root["id"], json=True), object())
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["changed"] is False
    assert payload["loop"] == {"enabled": False, "round": 0, "rounds": 3}
    assert payload["pending"] is None


# -- arm / disarm ----------------------------------------------------------


def test_loop_arms_with_a_round_budget(cli, capsys):
    arm_settings(cli)
    root = town(cli)
    rc = cli.sa.sa_cli.run(
        _args(session_id=root["id"], enable=True, rounds=5), object()
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "development loop: on — round 0 of 5 (5 left)" in out
    row = cli.store.get_session(root["id"])
    assert row["dev_loop"] == 1
    assert row["dev_loop_rounds"] == 5


def test_loop_off_keeps_the_budget_and_the_progress(cli, capsys):
    arm_settings(cli)
    root = town(cli)
    cli.sa.sa_cli.run(_args(session_id=root["id"], enable=True, rounds=4), object())
    cli.store.update_session(root["id"], dev_loop_round=2)
    capsys.readouterr()

    rc = cli.sa.sa_cli.run(_args(session_id=root["id"], disable=True), object())
    out = capsys.readouterr().out
    assert rc == 0
    assert "development loop: off" in out
    row = cli.store.get_session(root["id"])
    assert row["dev_loop"] == 0
    assert row["dev_loop_rounds"] == 4
    assert row["dev_loop_round"] == 2


def test_loop_rounds_alone_changes_the_budget_without_resetting(cli, capsys):
    arm_settings(cli)
    root = town(cli)
    cli.sa.sa_cli.run(_args(session_id=root["id"], enable=True, rounds=2), object())
    cli.store.update_session(root["id"], dev_loop_round=1)
    capsys.readouterr()

    rc = cli.sa.sa_cli.run(_args(session_id=root["id"], rounds=6), object())
    out = capsys.readouterr().out
    assert rc == 0
    assert "round 1 of 6 (5 left)" in out


def test_loop_reports_a_waiting_pr(cli, capsys):
    arm_settings(cli)
    root = town(cli)
    worker = cli.store.create_session(
        "fix it", parent_session_id=root["id"], status="review", pr_number=12,
    )
    cli.store.update_session(worker["id"], finished_at=1000.0)
    cli.sa.sa_cli.run(_args(session_id=root["id"], enable=True), object())
    capsys.readouterr()

    rc = cli.sa.sa_cli.run(_args(session_id=root["id"]), object())
    out = capsys.readouterr().out
    assert rc == 0
    assert "PR #12" in out
    assert worker["id"] in out


# -- refusals --------------------------------------------------------------


def test_loop_arming_needs_the_publishing_switches(cli, capsys):
    root = town(cli)
    rc = cli.sa.sa_cli.run(_args(session_id=root["id"], enable=True), object())
    out = capsys.readouterr().out
    assert rc == 2
    assert "Allow Router & workers to review/approve PRs" in out
    assert cli.store.get_session(root["id"])["dev_loop"] is None


def test_loop_refuses_a_worker_row(cli, capsys):
    root = town(cli)
    worker = cli.store.create_session("work", parent_session_id=root["id"])
    rc = cli.sa.sa_cli.run(_args(session_id=worker["id"], enable=True), object())
    out = capsys.readouterr().out
    assert rc == 2
    assert "only a router town" in out


def test_loop_rejects_an_out_of_range_budget(cli, capsys):
    arm_settings(cli)
    root = town(cli)
    rc = cli.sa.sa_cli.run(_args(session_id=root["id"], enable=True, rounds=0), object())
    out = capsys.readouterr().out
    assert rc == 2
    assert "between 1 and 20" in out


def test_loop_rejects_on_and_off_together(cli, capsys):
    root = town(cli)
    rc = cli.sa.sa_cli.run(
        _args(session_id=root["id"], enable=True, disable=True), object()
    )
    assert rc == 2
    assert "opposites" in capsys.readouterr().out


def test_loop_unknown_session_is_a_runtime_error(cli, capsys):
    rc = cli.sa.sa_cli.run(_args(session_id="S-9999"), object())
    assert rc == 1
    assert "unknown chat session" in capsys.readouterr().out


def test_loop_without_an_id_prints_usage(cli, capsys):
    rc = cli.sa.sa_cli.run(_args(session_id=""), object())
    assert rc == 2
    assert "usage: hermes superagent loop" in capsys.readouterr().out


# -- towns view ------------------------------------------------------------


def test_towns_view_marks_an_armed_loop(cli, sa):
    arm_settings(cli)
    root = town(cli)
    sa.sa_devloop.arm(cli.store, root["id"], enabled=True, rounds=4)
    data = cli.store.list_towns()
    text = sa.sa_cli.render_towns(data)
    assert "loop 0/4" in text
