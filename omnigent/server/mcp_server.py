"""Inbound Streamable HTTP MCP endpoint for Omnigent sessions and automations."""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from omnigent.db.account_authority import account_authority_scope
from omnigent.server.accounts_store import SqlAlchemyAccountStore
from omnigent.server.auth import AuthProvider, UnifiedAuthProvider
from omnigent.server.mcp_profile_store import McpServerProfile, McpServerProfileStore
from omnigent.server.mcp_public_url import mcp_connection_url

_PROFILE_PATH = re.compile(r"^/mcp/private_(omcp_[A-Za-z0-9_-]+)/?$")
_AUTOMATION_UPDATE_FIELDS = frozenset(
    {
        "name",
        "prompt",
        "rrule",
        "agent_id",
        "timezone",
        "model_override",
        "reasoning_effort",
        "permission_mode",
        "max_cost_usd",
        "session_mode",
        "session_id",
        "workspace",
        "host_id",
        "execution_target",
        "project_id",
        "state",
    }
)


class McpProfilePathMiddleware:
    """Authenticate the token path, then scrub it before downstream logging."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        profile_store: McpServerProfileStore,
        account_store: SqlAlchemyAccountStore,
        base_path: str,
    ) -> None:
        self.app = app
        self.profile_store = profile_store
        self.account_store = account_store
        self.base_path = base_path.rstrip("/")
        try:
            public_path = urlsplit(mcp_connection_url("")).path.removesuffix("/mcp/private_")
        except ValueError:
            # Invalid public configuration blocks link creation, not server startup.
            public_path = ""
        self.prefixes = sorted({self.base_path, public_path}, key=len, reverse=True)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        original_path = scope.get("path", "")
        path = original_path
        for prefix in self.prefixes:
            if prefix and original_path.startswith(prefix + "/mcp"):
                path = original_path[len(prefix) :]
                break
        is_mcp = path == "/mcp" or path.startswith("/mcp/")
        if not is_mcp and "/mcp/private_" not in original_path:
            await self.app(scope, receive, send)
            return

        # Scrub even unsupported transports and unknown proxy prefixes.
        scope["path"] = "/mcp/"
        scope["raw_path"] = b"/mcp/"
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        match = _PROFILE_PATH.fullmatch(path) if is_mcp else None
        if match is None:
            await _send_error(send, 404, "MCP profile endpoint not found")
            return

        profile = await asyncio.to_thread(self.profile_store.resolve_active_token, match.group(1))
        if profile is None or not await asyncio.to_thread(
            self.account_store.accepts_generation,
            profile.user_id,
            profile.account_generation,
        ):
            await _send_error(send, 401, "Invalid or revoked MCP profile")
            return

        state = scope.setdefault("state", {})
        state["omnigent_mcp_profile"] = profile

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_no_store)


class McpMountPathMiddleware:
    """Restore the full mounted path after Omnigent strips a proxy prefix."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            # Starlette mounts extend root_path; the SDK router expects path
            # to include that root even when BasePathMiddleware stripped it.
            scope = dict(scope)
            scope["path"] = scope.get("root_path", "").rstrip("/") + "/"
            scope["raw_path"] = scope["path"].encode("utf-8")
        await self.app(scope, receive, send)


async def _send_error(send: Send, status_code: int, message: str) -> None:
    body = f'{{"error":"{message}"}}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def create_omnigent_mcp_server(
    *,
    app: ASGIApp,
    auth_provider: AuthProvider | None,
) -> FastMCP:
    """Build the native MCP server; every tool call reuses Omnigent REST auth."""
    if not isinstance(auth_provider, UnifiedAuthProvider) or auth_provider._source != "accounts":
        raise ValueError("Inbound MCP requires a server-mintable accounts auth provider")

    mcp = FastMCP(
        "Omnigent",
        instructions=(
            "Use these tools to list, create, inspect, message, and share Omnigent sessions, "
            "and to manage scheduled automations. Operations run as the Omnigent account "
            "linked to the MCP profile and obey that account's existing permissions."
        ),
        host="0.0.0.0",
        streamable_http_path="/",
        json_response=True,
        # Keep Streamable HTTP requests independent so HA replicas do not need
        # MCP session affinity or a shared in-memory session registry.
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    def current_profile(ctx: Context) -> McpServerProfile:
        request = ctx.request_context.request
        if request is None:
            raise RuntimeError("MCP profile authentication is required")
        profile = request.scope.get("state", {}).get("omnigent_mcp_profile")
        if not isinstance(profile, McpServerProfile):
            raise RuntimeError("MCP profile authentication is required")
        return profile

    async def omnigent_api(
        ctx: Context,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        profile = current_profile(ctx)
        with account_authority_scope(profile.user_id, profile.account_generation):
            session_token = auth_provider.mint_runner_token(profile.user_id, ttl_seconds=120)
        if session_token is None:
            raise RuntimeError("The linked account cannot create an Omnigent session token")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://omnigent.internal",
            timeout=60,
        ) as client:
            response = await client.request(
                method,
                path,
                params=params,
                json=body,
                headers={"Authorization": f"Bearer {session_token}"},
            )
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:1000]
            raise RuntimeError(f"Omnigent API returned {response.status_code}: {detail}")
        if response.status_code == 204:
            return {"ok": True}
        return response.json()

    @mcp.tool(name="omnigent_session_list", structured_output=False)
    async def session_list(
        ctx: Context,
        limit: int = 20,
        after: str | None = None,
        visibility: str = "all",
        include_archived: bool = False,
        search_query: str | None = None,
    ) -> Any:
        """List sessions visible to the linked Omnigent account."""
        params: dict[str, Any] = {
            "limit": min(max(limit, 1), 100),
            "visibility": visibility,
            "include_archived": str(include_archived).lower(),
        }
        if after:
            params["after"] = after
        if search_query:
            params["search_query"] = search_query
        return await omnigent_api(ctx, "GET", "/v1/sessions", params=params)

    @mcp.tool(name="omnigent_agent_list", structured_output=False)
    async def agent_list(
        ctx: Context,
        user_agents: bool = False,
        limit: int = 50,
        after: str | None = None,
    ) -> Any:
        """List available server agents or the linked account's own agents."""
        params: dict[str, Any] = {"limit": min(max(limit, 1), 100)}
        if user_agents:
            params["scope"] = "user"
        if after:
            params["after"] = after
        return await omnigent_api(ctx, "GET", "/v1/agents", params=params)

    @mcp.tool(name="omnigent_session_get", structured_output=False)
    async def session_get(ctx: Context, session_id: str) -> Any:
        """Read a session snapshot when the linked account has access."""
        return await omnigent_api(
            ctx,
            "GET",
            f"/v1/sessions/{quote(session_id, safe='')}",
        )

    @mcp.tool(name="omnigent_session_history", structured_output=False)
    async def session_history(
        ctx: Context,
        session_id: str,
        limit: Annotated[int, Field(ge=1, le=100)] = 50,
        after: str | None = None,
        before: str | None = None,
        order: Literal["asc", "desc"] = "asc",
    ) -> Any:
        """Read complete session history in pages. Use last_id as after for the
        next page while has_more is true; after and before are mutually exclusive.
        """
        if after is not None and before is not None:
            raise ValueError("after and before are mutually exclusive")
        params: dict[str, Any] = {"limit": limit, "order": order}
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before
        return await omnigent_api(
            ctx, "GET", f"/v1/sessions/{quote(session_id, safe='')}/items", params=params
        )

    @mcp.tool(name="omnigent_host_list", structured_output=False)
    async def host_list(ctx: Context) -> Any:
        """List the linked account's hosts to select a host_id for session execution."""
        return await omnigent_api(ctx, "GET", "/v1/hosts")

    @mcp.tool(name="omnigent_session_create", structured_output=False)
    async def session_create(
        ctx: Context,
        agent_id: str,
        initial_message: str,
        title: str | None = None,
        host_type: str = "external",
        host_id: str | None = None,
        workspace: str | None = None,
        workspaces: list[str] | None = None,
        sandbox_provider: str | None = None,
        project_id: str | None = None,
    ) -> Any:
        """Create a session and send its first task. Select an online host_id from
        omnigent_host_list (or a configured managed sandbox) to execute the task.
        """
        if host_type == "external" and host_id is None and project_id is None:
            raise ValueError("Select an online host_id with omnigent_host_list")
        initial_input = {
            "type": "message",
            "data": {
                "role": "user",
                "content": [{"type": "input_text", "text": initial_message}],
            },
        }
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "host_type": host_type,
        }
        if host_type == "managed":
            body["initial_input"] = initial_input
        if title is not None:
            body["title"] = title
        if host_id is not None:
            body["host_id"] = host_id
        if workspace is not None:
            body["workspace"] = workspace
        if workspaces is not None:
            body["workspaces"] = workspaces
        if sandbox_provider is not None:
            body["sandbox_provider"] = sandbox_provider
        if project_id is not None:
            body["project_id"] = project_id
        created = await omnigent_api(ctx, "POST", "/v1/sessions", body=body)
        if host_type != "managed":
            session_id = created["id"]
            try:
                await omnigent_api(
                    ctx,
                    "POST",
                    f"/v1/sessions/{quote(session_id, safe='')}/events",
                    body=initial_input,
                )
            except RuntimeError as exc:
                raise RuntimeError(
                    f"Session {session_id} was created, but its first message failed: {exc}"
                ) from exc
        return created

    @mcp.tool(name="omnigent_session_send", structured_output=False)
    async def session_send(ctx: Context, session_id: str, message: str) -> Any:
        """Send a user message to a session; Omnigent applies normal edit permissions."""
        return await omnigent_api(
            ctx,
            "POST",
            f"/v1/sessions/{quote(session_id, safe='')}/events",
            body={
                "type": "message",
                "data": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": message}],
                },
            },
        )

    @mcp.tool(name="omnigent_session_share", structured_output=False)
    async def session_share(
        ctx: Context,
        session_id: str,
        user_id: str,
        permission: str = "read",
    ) -> Any:
        """Grant another Omnigent user read, edit, or manage access to a session."""
        levels = {"read": 1, "edit": 2, "manage": 3}
        if permission not in levels:
            raise ValueError("permission must be read, edit, or manage")
        return await omnigent_api(
            ctx,
            "PUT",
            f"/v1/sessions/{quote(session_id, safe='')}/permissions",
            body={"user_id": user_id, "level": levels[permission]},
        )

    @mcp.tool(name="omnigent_session_unshare", structured_output=False)
    async def session_unshare(ctx: Context, session_id: str, user_id: str) -> Any:
        """Remove a user's explicit access to a session."""
        return await omnigent_api(
            ctx,
            "DELETE",
            f"/v1/sessions/{quote(session_id, safe='')}/permissions/{quote(user_id, safe='')}",
        )

    @mcp.tool(name="omnigent_automation_list", structured_output=False)
    async def automation_list(ctx: Context) -> Any:
        """List scheduled automations owned by the linked Omnigent account."""
        return await omnigent_api(ctx, "GET", "/v1/scheduled-tasks")

    @mcp.tool(name="omnigent_automation_create", structured_output=False)
    async def automation_create(
        ctx: Context,
        name: str,
        prompt: str,
        rrule: str,
        agent_id: str,
        timezone: str = "UTC",
        session_mode: str = "new",
        session_id: str | None = None,
        execution_target: str = "connected_host",
        host_id: str | None = None,
        workspace: str | None = None,
        model_override: str | None = None,
        reasoning_effort: str | None = None,
        permission_mode: str | None = None,
        max_cost_usd: float | None = None,
        project_id: str | None = None,
    ) -> Any:
        """Create an automation using Omnigent's RRULE and ownership validation."""
        body = {
            key: value
            for key, value in {
                "name": name,
                "prompt": prompt,
                "rrule": rrule,
                "agent_id": agent_id,
                "timezone": timezone,
                "session_mode": session_mode,
                "session_id": session_id,
                "execution_target": execution_target,
                "host_id": host_id,
                "workspace": workspace,
                "model_override": model_override,
                "reasoning_effort": reasoning_effort,
                "permission_mode": permission_mode,
                "max_cost_usd": max_cost_usd,
                "project_id": project_id,
            }.items()
            if value is not None
        }
        return await omnigent_api(ctx, "POST", "/v1/scheduled-tasks", body=body)

    @mcp.tool(name="omnigent_automation_update", structured_output=False)
    async def automation_update(
        ctx: Context,
        automation_id: str,
        updates: dict[str, Any],
    ) -> Any:
        """Update an owned automation with the same fields accepted by Omnigent's API."""
        unknown = set(updates) - _AUTOMATION_UPDATE_FIELDS
        if unknown:
            raise ValueError(f"Unsupported automation fields: {', '.join(sorted(unknown))}")
        return await omnigent_api(
            ctx,
            "PATCH",
            f"/v1/scheduled-tasks/{quote(automation_id, safe='')}",
            body=updates,
        )

    @mcp.tool(name="omnigent_automation_run", structured_output=False)
    async def automation_run(ctx: Context, automation_id: str) -> Any:
        """Run an owned automation now."""
        return await omnigent_api(
            ctx,
            "POST",
            f"/v1/scheduled-tasks/{quote(automation_id, safe='')}/run",
            body={},
        )

    @mcp.tool(name="omnigent_automation_runs", structured_output=False)
    async def automation_runs(ctx: Context, automation_id: str) -> Any:
        """List recent run records for an owned automation."""
        return await omnigent_api(
            ctx,
            "GET",
            f"/v1/scheduled-tasks/{quote(automation_id, safe='')}/runs",
        )

    @mcp.tool(name="omnigent_automation_delete", structured_output=False)
    async def automation_delete(ctx: Context, automation_id: str) -> Any:
        """Delete an owned automation."""
        return await omnigent_api(
            ctx,
            "DELETE",
            f"/v1/scheduled-tasks/{quote(automation_id, safe='')}",
        )

    return mcp
