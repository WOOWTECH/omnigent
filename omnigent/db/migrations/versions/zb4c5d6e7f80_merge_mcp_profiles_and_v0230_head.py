"""Merge the WOOWTECH MCP profile branch with the upstream v0.23.0 head.

Revision ID: zb4c5d6e7f80
Revises: ffeec66eadb5, za3b4c5d6e7f

``za3b4c5d6e7f`` (MCP profiles) was released on top of v0.21.0's head
``d0a1f879a01d``. Upstream v0.22/v0.23 continue from the same revision, so a
database created by the 0.21.0 overlay sits at ``za3b4c5d6e7f`` and must still
receive every upstream revision. Keeping ``za3b4c5d6e7f`` where it was and
joining the two heads here lets Alembic apply the missing upstream branch;
re-parenting it would mark those upstream revisions as already applied.

Upgrade and downgrade only reconcile Alembic's version rows; the merge itself
changes no application schema or data.
"""

from __future__ import annotations

revision: str = "zb4c5d6e7f80"
down_revision: tuple[str, ...] = ("ffeec66eadb5", "za3b4c5d6e7f")
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Join the independently applied parent revisions."""


def downgrade() -> None:
    """Restore both parent version markers without undoing either schema."""
