"""The data-training ("contributor") gate: what counts, and who may run it.

Two facts keep this honest: the tier rule must be the same one Hermes applies
(a ``contributor`` dash-token in the model id, matched on the id alone), and the
opt-in must be the literal ``true`` Hermes compares with ``is True``. A test
that only checked "our copy of the rule" would pass while every worker died.
"""

from __future__ import annotations

import pytest


def test_contributor_suffix_is_a_data_training_tier(sa):
    policy = sa.sa_model_policy
    assert policy.is_data_training_tier("muse-spark-1.3-contributor")
    assert policy.is_data_training_tier("muse-spark-1.2-contributor-free")
    assert policy.is_data_training_tier("meta/muse-spark-1.3-contributor")
    # Case and surrounding whitespace do not matter.
    assert policy.is_data_training_tier("  MUSE-SPARK-1.3-CONTRIBUTOR ")


def test_ordinary_models_are_not_data_tiers(sa):
    policy = sa.sa_model_policy
    for model in (
        "muse-spark-1.3",
        "deepseek-v4.1-flash",
        "qwen/qwen3.8-27b",
        "contributors-helper",  # a different token, not the tier
        "",
        None,
    ):
        assert policy.is_data_training_tier(model) is False


def test_host_allows_only_a_literal_true(sa, tmp_path):
    policy = sa.sa_model_policy
    config = tmp_path / "config.yaml"
    assert policy.host_allows_data_tiers(config) is False  # missing file

    config.write_text(
        "security:\n  allow_data_training_tiers_noninteractive: true\n", encoding="utf-8"
    )
    assert policy.host_allows_data_tiers(config) is True

    # A string is not the literal True Hermes compares against.
    config.write_text(
        "security:\n  allow_data_training_tiers_noninteractive: 'true'\n",
        encoding="utf-8",
    )
    assert policy.host_allows_data_tiers(config) is False

    config.write_text("model:\n  default: x\n", encoding="utf-8")
    assert policy.host_allows_data_tiers(config) is False

    config.write_text("security: [not, a, mapping]\n", encoding="utf-8")
    assert policy.host_allows_data_tiers(config) is False


def test_check_model_refuses_a_tier_the_host_has_not_allowed(sa):
    policy = sa.sa_model_policy
    with pytest.raises(ValueError) as excinfo:
        policy.check_model("muse-spark-1.3-contributor", allowed=False)

    message = str(excinfo.value)
    assert policy.CONFIG_KEY in message
    assert policy.SET_COMMAND in message
    assert "-contributor" in message  # tells the user the way out
    assert "train" in message  # states the trade-off, not just the rule


def test_check_model_passes_when_allowed(sa):
    policy = sa.sa_model_policy
    policy.check_model("muse-spark-1.3-contributor", allowed=True)
    # An ordinary model never consults the host state at all.
    policy.check_model("deepseek-v4.1-flash", allowed=False)


def test_check_model_reads_the_host_state_when_not_given(sa, monkeypatch):
    policy = sa.sa_model_policy
    monkeypatch.setattr(policy, "host_allows_data_tiers", lambda: True)
    policy.check_model("muse-spark-1.3-contributor")

    monkeypatch.setattr(policy, "host_allows_data_tiers", lambda: False)
    with pytest.raises(ValueError):
        policy.check_model("muse-spark-1.3-contributor")


def test_set_host_allowance_writes_through_hermes_config(sa, monkeypatch, tmp_path):
    policy = sa.sa_model_policy
    config = tmp_path / "config.yaml"
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        config.write_text(
            "security:\n  allow_data_training_tiers_noninteractive: true\n",
            encoding="utf-8",
        )
        return Result()

    monkeypatch.setattr(policy, "host_config_path", lambda: config)
    monkeypatch.setattr(sa.sa_runner, "resolve_hermes_bin", lambda *a, **k: "hermes")
    result = policy.set_host_allowance(True, run=fake_run)

    assert calls == [["hermes", "config", "set", policy.CONFIG_KEY, "true"]]
    # The read-back is what the caller reports, not the requested value.
    assert result["allowed"] is True
    assert result["path"] == str(config)


def test_set_host_allowance_reports_the_observed_state(sa, monkeypatch, tmp_path):
    policy = sa.sa_model_policy
    config = tmp_path / "config.yaml"
    config.write_text(
        "security:\n  allow_data_training_tiers_noninteractive: true\n", encoding="utf-8"
    )

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    # The command "succeeds" without changing the file, which is exactly the
    # case a request-echoing implementation would report as allowed.
    monkeypatch.setattr(policy, "host_config_path", lambda: config)
    monkeypatch.setattr(sa.sa_runner, "resolve_hermes_bin", lambda *a, **k: "hermes")
    monkeypatch.setattr(policy, "_read_host_state", lambda path=None: (False, ""))
    assert policy.set_host_allowance(False, run=lambda argv, **k: Result())["allowed"] is False


def test_set_host_allowance_surfaces_a_failed_write(sa, monkeypatch):
    policy = sa.sa_model_policy

    class Result:
        returncode = 2
        stdout = ""
        stderr = "unknown config key: security.allow_data_training_tiers_noninteractive"

    monkeypatch.setattr(sa.sa_runner, "resolve_hermes_bin", lambda *a, **k: "hermes")
    with pytest.raises(ValueError) as excinfo:
        policy.set_host_allowance(True, run=lambda argv, **k: Result())
    assert policy.CONFIG_KEY in str(excinfo.value)
    assert "unknown config key" in str(excinfo.value)


def test_the_no_yaml_fallback_reads_the_security_block(sa):
    scan = sa.sa_model_policy._scan_config_text
    assert scan("security:\n  allow_data_training_tiers_noninteractive: true\n") is True
    assert scan("security:\n  allow_data_training_tiers_noninteractive: true  # opt-in\n") is True
    assert scan("security:\n  other: 1\n  allow_data_training_tiers_noninteractive: false\n") is False
    # A string is not the literal True Hermes compares against.
    assert scan("security:\n  allow_data_training_tiers_noninteractive: 'true'\n") is False
    # The same key nested under another section is not the host switch.
    assert scan("model:\n  security:\n    allow_data_training_tiers_noninteractive: true\n") is False
    assert scan("") is False


def test_host_state_survives_a_frozen_build_without_yaml(sa, monkeypatch, tmp_path):
    """The CLI ships as a frozen binary with no PyYAML: the read must still work."""
    import sys

    config = tmp_path / "config.yaml"
    config.write_text(
        "security:\n  allow_data_training_tiers_noninteractive: true\n", encoding="utf-8"
    )
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` now raises

    assert sa.sa_model_policy.host_allows_data_tiers(config) is True
    state = sa.sa_model_policy.host_state(config)
    assert state["allowed"] is True and state["config_exists"] is True
