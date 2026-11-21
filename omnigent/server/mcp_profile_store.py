"""Persistence for account-bound credentials used by the inbound MCP server."""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from omnigent.db.account_authority import lock_account
from omnigent.db.db_models import SqlMcpServerProfile, current_workspace_id
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    run_write_transaction,
)


@dataclass(frozen=True, slots=True)
class McpServerProfile:
    id: str
    name: str
    user_id: str
    account_generation: str
    created_by: str
    created_at: int
    last_used_at: int | None
    revoked_at: int | None


def hash_mcp_token(token: str) -> bytes:
    """Hash a random 256-bit MCP bearer token before it reaches storage."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def _to_profile(row: SqlMcpServerProfile) -> McpServerProfile:
    return McpServerProfile(
        id=row.id,
        name=row.name,
        user_id=row.user_id,
        account_generation=row.account_generation,
        created_by=row.created_by,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


class McpServerProfileStore:
    """Stores only token hashes and the account generation each link belongs to."""

    def __init__(self, storage_location: str) -> None:
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.mcp_server_profile_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.mcp_server_profile_store",
            immediate=True,
        )

    def create(
        self,
        *,
        name: str,
        user_id: str,
        account_generation: str,
        created_by: str,
        token: str,
    ) -> McpServerProfile:
        """Persist a profile if the linked account still has the verified generation."""
        now = int(time.time())
        profile_id = secrets.token_hex(16)
        workspace_id = current_workspace_id()

        def write(session: Session) -> McpServerProfile:
            account = lock_account(session, user_id)
            if (
                account is None
                or account.deleted_at is not None
                or account.account_generation != account_generation
            ):
                raise ValueError("The linked account changed while the profile was being created")
            row = SqlMcpServerProfile(
                workspace_id=workspace_id,
                id=profile_id,
                name=name,
                user_id=user_id,
                account_generation=account_generation,
                created_by=created_by,
                token_hash=hash_mcp_token(token),
                created_at=now,
                last_used_at=None,
                revoked_at=None,
            )
            session.add(row)
            session.flush()
            return _to_profile(row)

        return run_write_transaction(self._session_immediate, "create_mcp_server_profile", write)

    def list_profiles(self) -> list[McpServerProfile]:
        with self._session("list_mcp_server_profiles") as session:
            rows = session.execute(
                select(SqlMcpServerProfile)
                .where(SqlMcpServerProfile.workspace_id == current_workspace_id())
                .order_by(SqlMcpServerProfile.created_at.desc(), SqlMcpServerProfile.id.desc())
            ).scalars()
            return [_to_profile(row) for row in rows]

    def resolve_active_token(self, token: str) -> McpServerProfile | None:
        """Resolve a bearer token and atomically touch its last-used timestamp."""
        token_hash = hash_mcp_token(token)

        def write(session: Session) -> McpServerProfile | None:
            row = session.execute(
                select(SqlMcpServerProfile)
                .where(
                    SqlMcpServerProfile.workspace_id == current_workspace_id(),
                    SqlMcpServerProfile.token_hash == token_hash,
                    SqlMcpServerProfile.revoked_at.is_(None),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return None
            row.last_used_at = int(time.time())
            session.flush()
            return _to_profile(row)

        return run_write_transaction(self._session_immediate, "resolve_mcp_server_profile", write)

    def revoke(self, profile_id: str) -> bool:
        """Revoke one profile; return whether it exists in the active workspace."""
        now = int(time.time())

        def write(session: Session) -> bool:
            row = session.get(SqlMcpServerProfile, (current_workspace_id(), profile_id))
            if row is None:
                return False
            if row.revoked_at is None:
                session.execute(
                    update(SqlMcpServerProfile)
                    .where(
                        SqlMcpServerProfile.workspace_id == current_workspace_id(),
                        SqlMcpServerProfile.id == profile_id,
                        SqlMcpServerProfile.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
            return True

        return run_write_transaction(self._session_immediate, "revoke_mcp_server_profile", write)
