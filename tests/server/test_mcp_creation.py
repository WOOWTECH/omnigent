"""The two session launch modes use their actual REST input contracts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import Context

from omnigent.server.auth import create_auth_provider
from omnigent.server.mcp_profile_store import McpServerProfile
from omnigent.server.mcp_server import create_omnigent_mcp_server
from omnigent.server.schemas import SessionCreateRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("send_fails", [False, True])
async def test_first_task_contract(
    monkeypatch: pytest.MonkeyPatch, managed: bool, send_fails: bool
) -> None:
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "accounts")
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_COOKIE_SECRET", "ab" * 32)
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_BASE_URL", "http://localhost:8000")
    monkeypatch.delenv("OMNIGENT_OIDC_ISSUER", raising=False)
    calls: list[dict] = []
    app = FastAPI()

    @app.post("/v1/sessions")
    async def create(request: Request) -> dict:
        body = await request.json()
        SessionCreateRequest.model_validate(body)
        assert request.headers["authorization"].startswith("Bearer ")
        calls.append(body)
        return {"id": "test-session"}

    @app.post("/v1/sessions/test-session/events")
    async def send(request: Request) -> JSONResponse:
        calls.append(await request.json())
        return JSONResponse({"ok": not send_fails}, status_code=503 if send_fails else 200)

    mcp = create_omnigent_mcp_server(app=app, auth_provider=create_auth_provider())
    profile = McpServerProfile("id", "name", "alice", "generation", "admin", 0, None, None)
    request = Request({"type": "http", "state": {"omnigent_mcp_profile": profile}})
    context = cast(Context, SimpleNamespace(request_context=SimpleNamespace(request=request)))
    tool = mcp._tool_manager.get_tool("omnigent_session_create")
    assert tool is not None
    arguments = {
        "ctx": context,
        "agent_id": "test-agent",
        "initial_message": "first task",
        "host_type": "managed" if managed else "external",
        "host_id": None if managed else "test-host",
    }
    if send_fails and not managed:
        with pytest.raises(RuntimeError, match="Session test-session was created"):
            await tool.fn(**arguments)
    else:
        assert await tool.fn(**arguments) == {"id": "test-session"}
    assert "initial_items" not in calls[0]
    if managed:
        assert len(calls) == 1
        message = calls[0]["initial_input"]
    else:
        assert len(calls) == 2
        assert "initial_input" not in calls[0]
        message = calls[1]
    assert message["data"]["content"] == [{"type": "input_text", "text": "first task"}]
