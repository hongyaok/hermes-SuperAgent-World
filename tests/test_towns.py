"""Towns: the root/worker hierarchy, its read model, and real spawning.

The tests use a throwaway SQLite file and a fake runner: no hermes process is
started. The point is the contract every surface shares — one root per town,
one level of workers, stable slots, and no phantom building after a delete.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from sa_plugin import sa_chats

ROOT = Path(__file__).resolve().parents[1]


def load_api():
    path = ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("superagent_plugin_api_towns", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["superagent_plugin_api_towns"] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    """Records launches and mirrors the store writes a real Runner makes."""

    def __init__(self, store=None, fail: str = ""):
        self.store = store
        self.fail = fail
        self.launches = []

    def launch_turn(
        self, session_id, prompt, *, resume_session_id=None, model=None, provider=None,
        reasoning_effort=None, repo=None
    ):
        if self.fail:
            raise RuntimeError(self.fail)
        self.launches.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                "model": model,
                "provider": provider,
                **({"reasoning_effort": reasoning_effort} if reasoning_effort is not None else {}),
                "repo": repo,
            }
        )
        if self.store is not None:
            self.store.update_session(session_id, status="running")
        return {"session_id": session_id}

    def reap(self):
        return []


@pytest.fixture
def api(tmp_path, monkeypatch, sa):
    module = load_api()
    db = tmp_path / "superagent.db"
    runner = FakeRunner()
    monkeypatch.setattr(module, "_db_path", lambda: db)
    monkeypatch.setattr(module, "_get_runner", lambda: runner)
    return module, db, runner


# -- migration -------------------------------------------------------------


def test_migration_adds_hierarchy_without_inventing_parents(tmp_path, sa):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, title TEXT,"
        " prompt TEXT NOT NULL, status TEXT NOT NULL, model TEXT, provider TEXT,"
        " repo TEXT, hermes_session_id TEXT, source TEXT, github_repo TEXT,"
        " pr_number INTEGER, pr_url TEXT, pid INTEGER, tokens INTEGER,"
        " duration_ms INTEGER, error TEXT, last_event TEXT, created_at REAL NOT NULL,"
        " updated_at REAL NOT NULL, started_at REAL, finished_at REAL)"
    )
    conn.execute(
        "INSERT INTO chat_sessions (id, title, prompt, status, created_at, updated_at)"
        " VALUES ('S-0001', 'old chat', 'old chat', 'review', 1, 1)"
    )
    conn.commit()
    conn.close()

    store = sa.sa_chats.ChatStore(path)
    with sqlite3.connect(path) as check:
        columns = {row[1] for row in check.execute("PRAGMA table_info(chat_sessions)")}
    assert {"parent_session_id", "town_slot", "reasoning_effort"} <= columns

    session = store.get_session("S-0001", message_limit=0)
    assert session["parent_session_id"] is None
    towns = store.list_towns()
    assert [town["id"] for town in towns["towns"]] == ["S-0001"]
    assert towns["towns"][0]["workers"] == []


# -- store -----------------------------------------------------------------


def test_workers_group_under_one_root_with_stable_slots(chat):
    root = chat.create_session("router for the town")
    first = chat.create_session("worker one", parent_session_id=root["id"])
    second = chat.create_session("worker two", parent_session_id=root["id"])

    assert first["parent_session_id"] == root["id"]
    assert second["parent_session_id"] == root["id"]
    assert 0 <= first["town_slot"] < sa_chats.MAX_TOWN_WORKERS
    assert 0 <= second["town_slot"] < sa_chats.MAX_TOWN_WORKERS
    assert first["town_slot"] != second["town_slot"]
    assert first["avatar_slug"] in sa_chats.DEFAULT_PET_SLUGS
    assert second["avatar_slug"] in sa_chats.DEFAULT_PET_SLUGS
    assert first["avatar_slug"] != second["avatar_slug"]

    expected_order = [
        w["id"]
        for w in sorted([first, second], key=lambda w: (w["town_slot"], w["created_at"]))
    ]
    expected_slots = sorted([first["town_slot"], second["town_slot"]])

    towns = chat.list_towns()
    assert [town["id"] for town in towns["towns"]] == [root["id"]]
    assert [worker["id"] for worker in towns["towns"][0]["workers"]] == expected_order
    assert towns["town_count"] == 1
    assert towns["worker_count"] == 2

    # A second root is a second town; workers are still not towns.
    other = chat.create_session("another town")
    towns = chat.list_towns()
    assert {town["id"] for town in towns["towns"]} == {root["id"], other["id"]}
    assert all(first["id"] not in {t["id"] for t in towns["towns"]} for _ in [0])

    # Listing again returns the same plot numbers: polling cannot move a house.
    again = chat.list_towns()
    by_id = {town["id"]: town for town in again["towns"]}
    assert [worker["town_slot"] for worker in by_id[root["id"]]["workers"]] == expected_slots


def test_create_session_rejects_bad_parents(chat, sa, monkeypatch):
    root = chat.create_session("root")
    with pytest.raises(ValueError, match="unknown parent"):
        chat.create_session("x", parent_session_id="S-9999")
    with pytest.raises(ValueError, match="malformed parent"):
        chat.create_session("x", parent_session_id="not-a-session")
    with pytest.raises(ValueError, match="malformed parent"):
        chat.create_session("x", parent_session_id="S-1")
    with pytest.raises(ValueError, match="string or null"):
        chat.create_session("x", parent_session_id=123)
    with pytest.raises(ValueError, match="non-empty"):
        chat.create_session("x", parent_session_id="")

    child = chat.create_session("worker", parent_session_id=root["id"])
    with pytest.raises(ValueError, match="workers cannot spawn workers"):
        chat.create_session("grandchild", parent_session_id=child["id"])

    chat.set_archived(root["id"], True)
    with pytest.raises(ValueError, match="archived"):
        chat.create_session("second", parent_session_id=root["id"])
    chat.set_archived(root["id"], False)

    monkeypatch.setattr(sa.sa_chats, "MAX_TOWN_WORKERS", 2)
    # The child above already counts; one more fills the town and the next is
    # refused before it can become a house.
    chat.create_session("second", parent_session_id=root["id"])
    with pytest.raises(ValueError, match="max 2"):
        chat.create_session("third", parent_session_id=root["id"])


def test_archive_and_delete_never_create_phantom_towns(chat):
    root = chat.create_session("root")
    child = chat.create_session("worker", parent_session_id=root["id"])

    chat.set_archived(child["id"], True)
    assert chat.list_towns()["towns"][0]["worker_count"] == 0
    assert chat.list_towns(archived="all")["towns"][0]["worker_count"] == 1

    chat.set_archived(root["id"], True)
    assert chat.list_towns()["towns"] == []
    assert chat.list_towns(archived="only")["towns"][0]["id"] == root["id"]

    chat.set_archived(root["id"], False)
    assert chat.delete_session(root["id"]) is True
    data = chat.list_towns(archived="all")
    assert data["towns"] == []
    assert data["orphaned_count"] == 1
    assert data["orphaned"][0]["id"] == child["id"]


def test_full_town_assigns_every_grid_slot(chat):
    root = chat.create_session("root")
    slots = [
        chat.create_session(f"worker-{index}", parent_session_id=root["id"])["town_slot"]
        for index in range(sa_chats.MAX_TOWN_WORKERS)
    ]
    assert sorted(slots) == list(range(sa_chats.MAX_TOWN_WORKERS))
    with pytest.raises(ValueError, match="max 12"):
        chat.create_session("overflow", parent_session_id=root["id"])
    town = chat.list_towns()["towns"][0]
    assert town["worker_count"] == sa_chats.MAX_TOWN_WORKERS
    assert [worker["town_slot"] for worker in town["workers"]] == list(
        range(sa_chats.MAX_TOWN_WORKERS)
    )


def test_slot_reuses_a_gap_after_delete(chat):
    root = chat.create_session("root")
    first = chat.create_session("first", parent_session_id=root["id"], randomize_slot=False)
    second = chat.create_session("second", parent_session_id=root["id"], randomize_slot=False)
    third = chat.create_session("third", parent_session_id=root["id"], randomize_slot=False)
    assert [first["town_slot"], second["town_slot"], third["town_slot"]] == [0, 1, 2]

    assert chat.delete_session(second["id"]) is True
    replacement = chat.create_session("replacement", parent_session_id=root["id"], randomize_slot=False)
    assert replacement["town_slot"] == 1
    slots = [worker["town_slot"] for worker in chat.list_towns()["towns"][0]["workers"]]
    assert slots == [0, 1, 2]


def test_list_towns_clamps_legacy_slots_to_the_grid(chat):
    root = chat.create_session("root")
    child = chat.create_session("worker", parent_session_id=root["id"])
    with chat._connect() as conn:
        conn.execute("UPDATE chat_sessions SET town_slot = 99 WHERE id = ?", (child["id"],))
    town = chat.list_towns()["towns"][0]
    assert town["workers"][0]["town_slot"] == 0


def test_archived_orphans_follow_the_archived_mode(chat):
    root = chat.create_session("root")
    child = chat.create_session("worker", parent_session_id=root["id"])
    chat.set_archived(child["id"], True)
    assert chat.delete_session(root["id"]) is True

    hidden = chat.list_towns()
    assert hidden["orphaned_count"] == 0
    assert hidden["orphaned"] == []
    assert chat.list_towns(archived="all")["orphaned_count"] == 1
    assert chat.list_towns(archived="only")["orphaned_count"] == 1


def test_list_sessions_stays_flat_and_compatible(chat):
    root = chat.create_session("root")
    child = chat.create_session("worker", parent_session_id=root["id"])
    data = chat.list_sessions()
    ids = [row["id"] for row in data["sessions"]]
    assert ids == [child["id"], root["id"]]
    assert data["sessions"][0]["parent_session_id"] == root["id"]
    assert data["sessions"][1]["parent_session_id"] is None


# -- API -------------------------------------------------------------------


def test_towns_endpoint_groups_and_reports_orphans(api):
    module, _db, runner = api
    root = module.create_chat({"prompt": "root", "model": "m", "provider": "p"})["session"]
    child = module.create_chat(
        {"prompt": "worker", "parent_session_id": root["id"]}
    )["session"]
    assert child["parent_session_id"] == root["id"]
    assert child["model"] == "m"
    assert child["provider"] == "p"
    assert child["source"] == "worker"
    assert runner.launches[-1]["session_id"] == child["id"]

    body = module.list_towns()
    assert body["town_count"] == 1
    assert body["towns"][0]["id"] == root["id"]
    assert [w["id"] for w in body["towns"][0]["workers"]] == [child["id"]]

    # Flat /chats remains compatible and now carries the additive field.
    flat = module.list_chats()
    assert {row["id"] for row in flat["sessions"]} == {root["id"], child["id"]}
    assert all("parent_session_id" in row for row in flat["sessions"])

    # A missing root leaves an orphan, never a second town.
    store = module._chat_store()
    store.update_session(root["id"], status="review")
    store.update_session(child["id"], status="review")
    assert module.delete_chat(root["id"])["ok"] is True
    body = module.list_towns()
    assert body["towns"] == []
    assert body["orphaned_count"] == 1


def test_api_rejects_invalid_parent_relationships(api):
    from fastapi import HTTPException

    module, _db, _runner = api
    root = module.create_chat({"prompt": "root"})["session"]
    child = module.create_chat(
        {"prompt": "worker", "parent_session_id": root["id"]}
    )["session"]

    for body in (
        {"prompt": "x", "parent_session_id": 7},
        {"prompt": "x", "parent_session_id": ""},
        {"prompt": "x", "parent_session_id": "nope"},
        {"prompt": "x", "parent_session_id": "S-9999"},
        {"prompt": "x", "parent_session_id": child["id"]},
    ):
        with pytest.raises(HTTPException) as excinfo:
            module.create_chat(body)
        assert excinfo.value.status_code == 400

    with pytest.raises(HTTPException) as excinfo:
        module.list_towns(archived="sometimes")
    assert excinfo.value.status_code == 400


def test_api_worker_inherits_repo_and_refuses_a_different_one(tmp_path, api):
    module, _db, _runner = api
    repo = tmp_path / "repo"
    repo.mkdir()
    root = module.create_chat({"prompt": "root", "repo": str(repo)})["session"]
    child = module.create_chat(
        {"prompt": "worker", "parent_session_id": root["id"], "repo": str(repo)}
    )["session"]
    assert child["repo"] == root["repo"]

    other = tmp_path / "other"
    other.mkdir()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        module.create_chat(
            {"prompt": "worker", "parent_session_id": root["id"], "repo": str(other)}
        )
    assert excinfo.value.status_code == 400


# -- spawn tool ------------------------------------------------------------


def test_spawn_tool_creates_and_launches_one_real_worker(tmp_path, monkeypatch, sa):
    path = tmp_path / "superagent.db"
    store = sa.sa_chats.ChatStore(path)
    root = store.create_session(
        "router", model="m", provider="p", reasoning_effort="max", repo=""
    )
    runner = FakeRunner(store)
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(sa.sa_runtime, "shared_runner", lambda _store: runner)

    first = json.loads(
        sa.sa_tools.superagent_spawn(
            {"parent_session_id": root["id"], "prompt": "fix the parser"}, ctx=object()
        )
    )
    assert first["ok"] is True
    child = first["session"]
    assert child["id"] != root["id"]
    assert child["parent_session_id"] == root["id"]
    assert 0 <= child["town_slot"] < sa_chats.MAX_TOWN_WORKERS
    assert child["status"] == "running"
    assert child["reasoning_effort"] == "max"
    assert runner.launches[0]["prompt"] == "fix the parser"
    assert runner.launches[0]["reasoning_effort"] == "max"

    second = json.loads(
        sa.sa_tools.superagent_spawn(
            {"parent_session_id": root["id"], "prompt": "add a test"}, ctx=object()
        )
    )
    assert 0 <= second["session"]["town_slot"] < sa_chats.MAX_TOWN_WORKERS
    assert second["session"]["town_slot"] != child["town_slot"]

    nested = json.loads(
        sa.sa_tools.superagent_spawn(
            {"parent_session_id": child["id"], "prompt": "too deep"}, ctx=object()
        )
    )
    assert "workers cannot spawn workers" in nested["error"]
    assert len(store.list_towns()["towns"][0]["workers"]) == 2


def test_spawn_tool_reports_a_failed_launch_without_a_phantom(tmp_path, monkeypatch, sa):
    path = tmp_path / "superagent.db"
    store = sa.sa_chats.ChatStore(path)
    root = store.create_session("router")
    runner = FakeRunner(store, fail="binary missing")
    monkeypatch.setattr(sa.sa_settings, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(sa.sa_runtime, "shared_runner", lambda _store: runner)

    result = json.loads(
        sa.sa_tools.superagent_spawn(
            {"parent_session_id": root["id"], "prompt": "work"}, ctx=object()
        )
    )
    assert result["ok"] is False
    assert "binary missing" in result["error"]
    child = result["session"]
    assert child["status"] == "failed"
    assert child["parent_session_id"] == root["id"]
    # The row exists as a failed house (visible), but it was never launched.
    assert runner.launches == []


# -- vendored art ----------------------------------------------------------


def test_town_assets_are_an_explicit_allowlist_with_correct_mime():
    from fastapi import HTTPException

    module = load_api()
    manifest = module.town_asset_manifest()
    assert manifest["ok"] is True
    assert set(manifest["assets"]) == {
        "grass.png", "house-router.png", "house-a.png", "house-b.png",
        "house-c.png", "house-d.png", "house-e.png", "house-f.png",
        "house-g.png", "person.png", "tree-a.png", "tree-b.png",
        "tree-c.png", "bush.png", "fountain.png", "rock.png",
        "lamp.png", "road-h.png", "road-v.png", "plaza-stone.png",
        "water.png", "bridge.png", "bench.png", "well.png",
        "bulletin-board.png", "flowers.png", "signpost.png",
    }
    for name, item in manifest["assets"].items():
        assert item["mime"] == "image/png"
        assert item["data_url"].startswith("data:image/png;base64,")

    png = module.town_asset("grass.png")
    assert png.media_type == "image/png"
    assert bytes(png.body).startswith(bytes.fromhex("89504e470d0a1a0a"))

    for name in ("../plugin_api.py", "nope.png", "assets/town/grass.png"):
        with pytest.raises(HTTPException) as excinfo:
            module.town_asset(name)
        assert excinfo.value.status_code == 404


def test_town_pets_and_reroll_and_dispatch_endpoints(tmp_path, monkeypatch):
    path = tmp_path / "superagent.db"
    store = sa_chats.ChatStore(path)
    api = load_api()
    runner = FakeRunner(store)
    monkeypatch.setattr(api, "_db_path", lambda: path)
    monkeypatch.setattr(api, "_get_runner", lambda: runner)
    monkeypatch.setattr(api.sa_settings, "data_dir", lambda: tmp_path)

    root = api.create_chat({"prompt": "router"})["session"]
    worker = api.create_chat(
        {"prompt": "worker", "parent_session_id": root["id"]}
    )["session"]
    store.update_session(worker["id"], status="review")

    pets_resp = api.town_pets(slugs=f"{root['avatar_slug']},{worker['avatar_slug']}")
    assert pets_resp["ok"] is True
    assert root["avatar_slug"] in pets_resp["pets"]
    assert worker["avatar_slug"] in pets_resp["pets"]

    reroll_resp = api.reroll_town_pets({"town_id": root["id"]})
    assert reroll_resp["ok"] is True
    assert root["id"] in reroll_resp["updated"]
    assert worker["id"] in reroll_resp["updated"]

    dispatch_resp = api.town_dispatch(
        root["id"],
        {
            "worker_session_id": worker["id"],
            "action": "approve_pr",
            "pr_number": 42,
            "message": "Looks great, please approve PR #42",
        },
    )
    assert dispatch_resp["ok"] is True
    assert dispatch_resp["action"] == "approve_pr"
    assert dispatch_resp["worker_session_id"] == worker["id"]
    assert "superagent_pr_review" in runner.launches[-1]["prompt"]


def test_town_cascade_archive_and_delete_and_retry_failed_and_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "superagent.db"
    store = sa_chats.ChatStore(path)
    api = load_api()
    runner = FakeRunner(store)
    monkeypatch.setattr(api, "_db_path", lambda: path)
    monkeypatch.setattr(api, "_get_runner", lambda: runner)
    monkeypatch.setattr(api.sa_settings, "data_dir", lambda: tmp_path)

    root = api.create_chat({"prompt": "router"})["session"]
    w1 = api.create_chat({"prompt": "worker 1", "parent_session_id": root["id"]})["session"]
    w2 = api.create_chat({"prompt": "worker 2", "parent_session_id": root["id"]})["session"]
    store.update_session(root["id"], status="done")
    store.update_session(w1["id"], status="failed")
    store.update_session(w2["id"], status="done")

    # Batch retry failed workers in the town
    retry_resp = api.town_retry_failed(root["id"])
    assert retry_resp["ok"] is True
    assert retry_resp["count"] == 1
    assert retry_resp["retried"] == [w1["id"]]
    store.update_session(w1["id"], status="done")

    # Cascade archive root + all non-busy workers
    arch_resp = api.archive_chat(root["id"], {"archived": True, "cascade": True})
    assert arch_resp["ok"] is True
    assert store.get_session(root["id"])["archived_at"] is not None
    assert store.get_session(w1["id"])["archived_at"] is not None
    assert store.get_session(w2["id"])["archived_at"] is not None

    # Unarchive with cascade
    api.archive_chat(root["id"], {"archived": False, "cascade": True})
    assert store.get_session(root["id"])["archived_at"] is None
    assert store.get_session(w1["id"])["archived_at"] is None

    # Workspace cleanup prunes stale worker-* / pr-* dirs while preserving active ones
    ws_root = tmp_path / "workspaces"
    active_wt = ws_root / f"worker-{w1['id']}"
    stale_wt = ws_root / "worker-S-9999"
    active_wt.mkdir(parents=True)
    stale_wt.mkdir(parents=True)
    store.update_session(w1["id"], worktree_path=str(active_wt))

    clean_resp = api.cleanup_workspaces({})
    assert clean_resp["ok"] is True
    assert stale_wt.name in clean_resp["removed"]
    assert active_wt.exists()
    assert not stale_wt.exists()

    # Cascade delete root + non-busy workers
    del_resp = api.delete_chat(root["id"], cascade="true")
    assert del_resp["ok"] is True
    assert store.get_session(root["id"]) is None
    assert store.get_session(w1["id"]) is None
    assert store.get_session(w2["id"]) is None

