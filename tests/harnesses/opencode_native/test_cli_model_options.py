"""WOOWTECH overlay：host 端 `opencode models --standalone` 模型清單（OpenCode 2.x）。"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omnigent.harnesses.opencode_native import app_server
from omnigent.harnesses.opencode_native.app_server import (
    OpenCodeVersionError,
    list_opencode_cli_model_options,
)


@pytest.fixture(autouse=True)
def _isolated_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_server, "_cli_model_options_cache", {})
    monkeypatch.setattr(app_server, "find_opencode_cli", lambda *_a: "/bin/opencode")
    monkeypatch.setattr(app_server, "resolve_opencode_version", lambda _cli: "2.0.32")
    monkeypatch.delenv("OMNIGENT_OPENCODE_SKIP_VERSION_CHECK", raising=False)


def _fake_run(monkeypatch: pytest.MonkeyPatch, **result: object) -> Mock:
    run = Mock(return_value=SimpleNamespace(**{"returncode": 0, "stderr": "", **result}))
    monkeypatch.setattr(
        app_server,
        "subprocess",
        SimpleNamespace(
            run=run,
            DEVNULL=subprocess.DEVNULL,
            SubprocessError=subprocess.SubprocessError,
        ),
    )
    return run


def test_parses_one_qualified_model_per_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway ids keep everything after the first slash; noise lines are skipped."""
    _fake_run(
        monkeypatch,
        stdout=(
            "\x1b[2mopenai/gpt-5\x1b[0m\n"
            "omnigent/deepseek/deepseek-chat\n"
            "openai/gpt-5\n"
            "\n"
            "Starting private server ...\n"
            "not-a-model\n"
        ),
    )

    options = list_opencode_cli_model_options()

    assert options == [
        {
            "id": "openai/gpt-5",
            "model": "gpt-5",
            "providerID": "openai",
            "displayName": "openai/gpt-5",
            "name": "gpt-5",
            "isDefault": False,
        },
        {
            "id": "omnigent/deepseek/deepseek-chat",
            "model": "deepseek/deepseek-chat",
            "providerID": "omnigent",
            "displayName": "omnigent/deepseek/deepseek-chat",
            "name": "deepseek/deepseek-chat",
            "isDefault": False,
        },
    ]


def test_reuses_a_recent_result_and_refreshes_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """picker 每 15 秒輪詢；60 秒內重用上一次的結果，不重開私有 server。"""
    run = _fake_run(monkeypatch, stdout="openai/gpt-5\n")

    first = list_opencode_cli_model_options(now=100.0)
    first[0]["id"] = "mutated by caller"
    second = list_opencode_cli_model_options(now=159.0)
    assert run.call_count == 1
    assert second[0]["id"] == "openai/gpt-5"

    list_opencode_cli_model_options(now=161.0)
    assert run.call_count == 2


def test_failure_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed listing raises and the next poll tries again."""
    run = _fake_run(monkeypatch, returncode=1, stdout="", stderr="boom")

    with pytest.raises(RuntimeError, match="failed with code 1: boom"):
        list_opencode_cli_model_options(now=0.0)
    with pytest.raises(RuntimeError):
        list_opencode_cli_model_options(now=1.0)
    assert run.call_count == 2


def test_rejects_opencode_1x_before_running_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """1.x has no ``--standalone`` and is outside the 0.21 harness range."""
    monkeypatch.setattr(app_server, "resolve_opencode_version", lambda _cli: "1.18.35")
    run = _fake_run(monkeypatch, stdout="openai/gpt-5\n")

    with pytest.raises(OpenCodeVersionError, match=r"requires >=2\.0\.18,<2\.1\.0"):
        list_opencode_cli_model_options()
    run.assert_not_called()


def test_skip_version_check_env_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The harness's own escape hatch also applies to the preview."""
    monkeypatch.setenv("OMNIGENT_OPENCODE_SKIP_VERSION_CHECK", "1")
    monkeypatch.setattr(app_server, "resolve_opencode_version", lambda _cli: "2.1.4")
    run = _fake_run(monkeypatch, stdout="openai/gpt-5\n")

    assert [o["id"] for o in list_opencode_cli_model_options()] == ["openai/gpt-5"]
    assert run.call_args.args[0] == ["/bin/opencode", "models", "--standalone"]


def test_command_timeout_is_a_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _fake_run(monkeypatch, stdout="")
    run.side_effect = subprocess.TimeoutExpired(cmd="opencode", timeout=60)

    with pytest.raises(RuntimeError, match="Could not run 'opencode models'"):
        list_opencode_cli_model_options()
