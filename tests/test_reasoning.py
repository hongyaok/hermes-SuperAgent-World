"""Reasoning effort: storage, settings, API plumbing and CLI argv."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_api():
    path = ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("superagent_plugin_api_effort", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["superagent_plugin_api_effort"] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self):
        self.launches = []

    def launch_turn(
        self, session_id, prompt, *, resume_session_id=None, model=None, provider=None,
        reasoning_effort=None, repo=None
    ):
        self.launches.append({
            "session_id": session_id, "prompt": prompt, "model": model,
            "provider": provider, "reasoning_effort": reasoning_effort, "repo": repo,
        })
        return {"session_id": session_id}

    def reap(self):
        return []


@pytest.fixture
def api(tmp_path, monkeypatch):
    module = load_api()
    db = tmp_path / "superagent.db"
    runner = FakeRunner()
    monkeypatch.setattr(module, "_db_path", lambda: db)
    monkeypatch.setattr(module, "_get_runner", lambda: runner)
    monkeypatch.setattr(
        module.sa_settings, "load_settings", lambda: dict(module.sa_settings.DEFAULTS)
    )
    return module, runner


def test_settings_normalises_aliases_and_rejects_unknown_levels():
    from sa_plugin import sa_settings

    assert sa_settings.normalize_reasoning_effort(" OFF ") == "none"
    assert sa_settings.normalize_reasoning_effort("xhigh", strict=True) == "xhigh"
    assert sa_settings.normalize_reasoning_effort("max") == "max"
    with pytest.raises(ValueError):
        sa_settings.normalize_reasoning_effort("infinite", strict=True)
    assert sa_settings.normalize_reasoning_effort("infinite") == ""


def test_settings_patch_accepts_levels_above_high(api):
    from fastapi import HTTPException

    module, _runner = api
    assert module._settings_patch({"default_reasoning_effort": "ultra"}) == {
        "default_reasoning_effort": "ultra"
    }
    with pytest.raises(HTTPException) as excinfo:
        module._settings_patch({"default_reasoning_effort": "enormous"})
    assert excinfo.value.status_code == 400


def test_create_chat_persists_thinking_and_worker_inherits_it(api):
    module, runner = api
    created = module.create_chat(
        {"prompt": "root", "model": "m", "provider": "p", "reasoning_effort": "max"}
    )
    assert created["session"]["reasoning_effort"] == "max"

    child = module.create_chat(
        {"prompt": "worker", "parent_session_id": created["session"]["id"]}
    )
    assert child["session"]["reasoning_effort"] == "max"
    assert child["session"]["model"] == "m"
    # The row carries the level; the runner reads it when building argv.
    assert runner.launches[0].get("reasoning_effort") is None


def test_create_chat_rejects_an_unknown_level(api):
    module, _runner = api
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        module.create_chat({"prompt": "root", "reasoning_effort": "infinite"})
    assert excinfo.value.status_code == 400


def test_set_chat_model_can_change_only_thinking(api):
    module, _runner = api
    store = module._chat_store()
    session = store.create_session("root", model="m", provider="p", status="idle")
    updated = module.set_chat_model(
        session["id"], {"reasoning_effort": "xhigh"}
    )["session"]
    assert updated["model"] == "m"
    assert updated["provider"] == "p"
    assert updated["reasoning_effort"] == "xhigh"


def test_runner_argv_adds_reasoning_above_high(chat, sa):
    runner = sa.sa_runner.Runner(
        chat, hermes_bin="hermes", model="m", provider="p", repo=""
    )
    argv = runner._build_argv(
        "hermes", "do work", model="m", provider="p", reasoning_effort="xhigh"
    )
    assert argv[argv.index("--reasoning") + 1] == "xhigh"
    plain = runner._build_argv("hermes", "do work", model="m", provider="p")
    assert "--reasoning" not in plain
