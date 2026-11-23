"""Credential paths must be scrubbed even when the transport is unsupported."""

from typing import cast

import pytest
from starlette.types import Message, Scope

from omnigent.server.accounts_store import SqlAlchemyAccountStore
from omnigent.server.mcp_profile_store import McpServerProfileStore
from omnigent.server.mcp_server import McpProfilePathMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "websocket"])
@pytest.mark.parametrize("prefix", ["/external", "/unknown"])
async def test_rejected_transport_redacts_path(monkeypatch, transport, prefix):
    monkeypatch.setenv("OMNIGENT_MCP_PUBLIC_BASE_URL", "https://mcp.example.test/external")
    forwarded = []
    sent: list[Message] = []

    async def app(scope, receive, send):
        forwarded.append(scope)

    async def receive():
        return {"type": "websocket.connect" if transport == "websocket" else "http.request"}

    async def send(message):
        sent.append(message)

    class Profiles:
        def resolve_active_token(self, token):
            return None

    middleware = McpProfilePathMiddleware(
        app,
        profile_store=cast(McpServerProfileStore, Profiles()),
        account_store=cast(SqlAlchemyAccountStore, None),
        base_path="/ui",
    )
    path = f"{prefix}/mcp/private_omcp_test_bearer"
    scope = cast(Scope, {"type": transport, "path": path, "raw_path": path.encode()})
    await middleware(scope, receive, send)
    assert not forwarded
    assert scope["path"] == "/mcp/" and scope["raw_path"] == b"/mcp/"
    assert "omcp_test_bearer" not in repr(scope) + repr(sent)
    if transport == "websocket":
        assert sent == [{"type": "websocket.close", "code": 1008}]
    else:
        assert sent[0]["status"] == (401 if prefix == "/external" else 404)


@pytest.mark.asyncio
async def test_invalid_public_config_keeps_existing_routes_available(monkeypatch):
    monkeypatch.setenv("OMNIGENT_MCP_PUBLIC_BASE_URL", "http://private.example.test")
    forwarded = []

    async def app(scope, receive, send):
        forwarded.append(scope["path"])

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        raise AssertionError("The middleware must pass ordinary routes through")

    middleware = McpProfilePathMiddleware(
        app,
        profile_store=cast(McpServerProfileStore, None),
        account_store=cast(SqlAlchemyAccountStore, None),
        base_path="",
    )
    await middleware(cast(Scope, {"type": "http", "path": "/health"}), receive, send)
    assert forwarded == ["/health"]
