"""Admin API for creating and revoking inbound Omnigent MCP profiles."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.accounts_store import SqlAlchemyAccountStore
from omnigent.server.auth import AuthProvider, UnifiedAuthProvider
from omnigent.server.mcp_profile_store import McpServerProfile, McpServerProfileStore
from omnigent.server.mcp_public_url import mcp_connection_url
from omnigent.server.routes._auth_helpers import get_user_id
from omnigent.server.routes.accounts_auth import verify_account_password
from omnigent.stores.permission_store import PermissionStore


class CreateMcpServerProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("name")
    @classmethod
    def require_nonblank_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized


def _profile_json(profile: McpServerProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "user_id": profile.user_id,
        "created_by": profile.created_by,
        "created_at": profile.created_at,
        "last_used_at": profile.last_used_at,
        "revoked_at": profile.revoked_at,
        "active": profile.revoked_at is None,
    }


def create_mcp_server_profiles_router(
    *,
    profile_store: McpServerProfileStore,
    account_store: SqlAlchemyAccountStore,
    permission_store: PermissionStore,
    auth_provider: AuthProvider | None,
) -> APIRouter:
    """Create admin endpoints for password-verified account links."""
    router = APIRouter()
    accounts_auth = isinstance(auth_provider, UnifiedAuthProvider) and (
        auth_provider._source == "accounts"
    )

    async def require_admin(request: Request) -> str:
        if not accounts_auth:
            raise OmnigentError(
                "MCP profiles require the accounts authentication provider",
                code=ErrorCode.FORBIDDEN,
            )
        user_id = get_user_id(request, auth_provider)
        if user_id is None:
            raise OmnigentError("Authentication required", code=ErrorCode.UNAUTHORIZED)
        is_admin = await asyncio.to_thread(permission_store.is_admin, user_id)
        if not is_admin:
            raise OmnigentError("Admin privileges required", code=ErrorCode.FORBIDDEN)
        return user_id

    @router.get("/admin/mcp-server/profiles")
    async def list_profiles(request: Request, response: Response) -> dict[str, Any]:
        await require_admin(request)
        response.headers["Cache-Control"] = "no-store"
        profiles = await asyncio.to_thread(profile_store.list_profiles)
        return {"profiles": [_profile_json(profile) for profile in profiles]}

    @router.post(
        "/admin/mcp-server/profiles",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_profile(
        request: Request,
        response: Response,
        body: CreateMcpServerProfileRequest,
    ) -> dict[str, Any]:
        created_by = await require_admin(request)
        response.headers["Cache-Control"] = "no-store"
        username = body.username.strip().lower()
        generation = await asyncio.to_thread(
            verify_account_password,
            account_store,
            username,
            body.password.get_secret_value(),
        )
        if generation is None:
            # 被連結帳號的帳密錯誤屬於表單輸入錯誤；回 401 會被前端當成登入過期而導去登入頁
            raise OmnigentError(
                "Invalid username or password",
                code=ErrorCode.INVALID_INPUT,
            )

        token = f"omcp_{secrets.token_urlsafe(32)}"
        try:
            connection_url = mcp_connection_url(token)
        except ValueError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INTERNAL_ERROR) from exc
        try:
            profile = await asyncio.to_thread(
                profile_store.create,
                name=body.name.strip(),
                user_id=username,
                account_generation=generation,
                created_by=created_by,
                token=token,
            )
        except ValueError as exc:
            raise OmnigentError(
                "The linked account changed while creating the profile",
                code=ErrorCode.CONFLICT,
            ) from exc
        return {
            "profile": _profile_json(profile),
            "token": token,
            "connection_url": connection_url,
        }

    @router.delete("/admin/mcp-server/profiles/{profile_id}", status_code=204)
    async def revoke_profile(request: Request, profile_id: str) -> Response:
        await require_admin(request)
        exists = await asyncio.to_thread(profile_store.revoke, profile_id)
        if not exists:
            raise OmnigentError("MCP profile not found", code=ErrorCode.NOT_FOUND)
        return Response(status_code=204)

    return router
