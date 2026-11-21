"""Add revocable account-bound inbound MCP profiles."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "za3b4c5d6e7f"
down_revision: str | None = "d0a1f879a01d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the profile table and its token/user lookup indexes."""
    if sa.inspect(op.get_bind()).has_table("mcp_server_profiles"):
        return

    op.create_table(
        "mcp_server_profiles",
        sa.Column("workspace_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("account_generation", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column(
            "token_hash",
            sa.LargeBinary(length=32).with_variant(mysql.BINARY(length=32), "mysql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ux_mcp_server_profiles_token_hash",
        "mcp_server_profiles",
        ["workspace_id", "token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_mcp_server_profiles_user",
        "mcp_server_profiles",
        ["workspace_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    """Drop inbound MCP profile links."""
    if sa.inspect(op.get_bind()).has_table("mcp_server_profiles"):
        op.drop_table("mcp_server_profiles")
