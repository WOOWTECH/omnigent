"""Upgrade the prior release database without introducing a second head."""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from omnigent.db.utils import _build_alembic_config


def test_mcp_profiles_upgrade_and_downgrade(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path}/migration.db"
    config = _build_alembic_config(uri)
    assert ScriptDirectory.from_config(config).get_current_head() == "za3b4c5d6e7f"
    command.upgrade(config, "d0a1f879a01d")
    command.upgrade(config, "head")
    engine = sa.create_engine(uri)
    try:
        inspector = sa.inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("mcp_server_profiles")}
        assert {"account_generation", "token_hash", "revoked_at"} <= columns
        assert "password" not in columns and "token" not in columns
        assert any(
            index["unique"] and index["column_names"] == ["workspace_id", "token_hash"]
            for index in inspector.get_indexes("mcp_server_profiles")
        )
        command.downgrade(config, "d0a1f879a01d")
        assert not sa.inspect(engine).has_table("mcp_server_profiles")
        command.upgrade(config, "head")
        assert sa.inspect(engine).has_table("mcp_server_profiles")
    finally:
        engine.dispose()
