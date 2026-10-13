"""Failed and cancelled launches release resources before registry ownership."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from omnigent.inner.datamodel import OSEnvSandboxSpec, OSEnvSpec, TerminalEnvSpec
from omnigent.inner.terminal import TerminalCreateResult, TerminalInstance
from omnigent.terminals import TerminalRegistry, registry

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required"),
    pytest.mark.timeout(20),
]


def _capture_instances(monkeypatch: pytest.MonkeyPatch) -> list[TerminalInstance]:
    created: list[TerminalInstance] = []
    original = registry.create_terminal_instance

    def create(*args: Any, **kwargs: Any) -> TerminalCreateResult:
        result = original(*args, **kwargs)
        created.append(result.instance)
        return result

    monkeypatch.setattr(registry, "create_terminal_instance", create)
    return created


def _spec(workspace: Path, *, env: dict[str, str] | None = None) -> TerminalEnvSpec:
    return TerminalEnvSpec(
        command="cat",
        env=env or {},
        os_env=OSEnvSpec(cwd=str(workspace), sandbox=OSEnvSandboxSpec(type="none")),
    )


async def test_missing_tmux_during_spawn_releases_private_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    created = _capture_instances(monkeypatch)
    terminal_registry = TerminalRegistry()
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    caplog.set_level(logging.WARNING, logger=registry.__name__)
    try:
        with pytest.raises(FileNotFoundError) as raised:
            await terminal_registry.launch(
                "owned-session", "shell", "main", _spec(tmp_path, env={"PATH": str(empty_bin)})
            )
        assert raised.value.filename == "tmux"
        assert len(created) == 1
        assert not created[0].private_dir.exists()
        assert terminal_registry.list_for_conversation("owned-session") == []
        assert terminal_registry.active_conversation_ids() == []
        record = next(
            record
            for record in caplog.records
            if getattr(record, "event_name", None) == "terminal_launch_failed"
        )
        assert record.attributes["launch_stage"] == "terminal_spawn"
        assert record.attributes["native_launch_os_errno"] == errno.ENOENT
        assert record.attributes["launch_cleanup_outcome"] == "closed"
        assert str(empty_bin) not in json.dumps(record.attributes)
    finally:
        for instance in created:
            await instance.close()


@pytest.mark.parametrize("cleanup_failure", ["error", "timeout", "cancelled"])
async def test_cleanup_failure_keeps_original_launch_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cleanup_failure: str,
) -> None:
    instance = TerminalInstance(
        name="shell", session_key="main", socket_path=tmp_path / "socket", private_dir=tmp_path
    )
    original = FileNotFoundError(errno.ENOENT, "synthetic-private-error", "synthetic-private-file")
    monkeypatch.setattr(instance, "launch", AsyncMock(side_effect=original))

    async def close() -> None:
        if cleanup_failure == "timeout":
            await asyncio.Event().wait()
        elif cleanup_failure == "cancelled":
            raise asyncio.CancelledError
        else:
            raise RuntimeError("synthetic-private-cleanup-error")

    monkeypatch.setattr(instance, "close", close)
    monkeypatch.setattr(
        registry,
        "create_terminal_instance",
        lambda *_a, **_k: TerminalCreateResult(instance, tmp_path),
    )
    monkeypatch.setattr(registry, "_CLOSE_TIMEOUT_S", 0.02)
    caplog.set_level(logging.WARNING, logger=registry.__name__)

    with pytest.raises(FileNotFoundError) as raised:
        await TerminalRegistry().launch("owned-session", "shell", "main", _spec(tmp_path))

    assert raised.value is original
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "terminal_launch_failed"
    )
    assert record.attributes["launch_cleanup_outcome"] == (
        "timed_out" if cleanup_failure == "timeout" else cleanup_failure
    )
    assert "synthetic-private" not in json.dumps(record.attributes)


@pytest.mark.parametrize("cancel_stage", ["spawn", "liveness"])
async def test_cancelled_launch_reaps_real_tmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_stage: str
) -> None:
    created = _capture_instances(monkeypatch)
    reached = asyncio.Event()
    release = asyncio.Event()
    original_launch = TerminalInstance.launch
    original_alive = TerminalInstance.is_alive

    async def launch(instance: TerminalInstance, *, cwd: Path | None = None) -> None:
        await original_launch(instance, cwd=cwd)
        if cancel_stage == "spawn":
            reached.set()
            await release.wait()

    async def alive(instance: TerminalInstance) -> bool:
        result = await original_alive(instance)
        if cancel_stage == "liveness":
            reached.set()
            await release.wait()
        return result

    monkeypatch.setattr(TerminalInstance, "launch", launch)
    monkeypatch.setattr(TerminalInstance, "is_alive", alive)
    terminal_registry = TerminalRegistry()
    task = asyncio.create_task(
        terminal_registry.launch("owned-session", "shell", "main", _spec(tmp_path))
    )
    try:
        await asyncio.wait_for(reached.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(created) == 1
        instance = created[0]
        assert not instance.private_dir.exists()
        assert not instance.running
        result = subprocess.run(
            ["tmux", "-S", str(instance.socket_path), "has-session"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        assert result.returncode != 0
        assert terminal_registry.get("owned-session", "shell", "main") is None
    finally:
        release.set()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for instance in created:
            await instance.close()
