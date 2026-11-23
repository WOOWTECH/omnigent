"""MCP profiles upgrade from every released base without a second head."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory

from omnigent.db.utils import _build_alembic_config

_HEAD = "zb4c5d6e7f80"
_MCP_PROFILES = "za3b4c5d6e7f"
# 0.21.0 的 head：overlay 的 MCP profiles 與上游 0.22／0.23 都從這裡分出去。
_V0210_HEAD = "d0a1f879a01d"
# 上游 v0.23.0 的 head。
_V0230_HEAD = "ffeec66eadb5"


def _assert_profiles_table(engine: sa.Engine) -> None:
    inspector = sa.inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("mcp_server_profiles")}
    assert {"account_generation", "token_hash", "revoked_at"} <= columns
    assert "password" not in columns and "token" not in columns
    assert any(
        index["unique"] and index["column_names"] == ["workspace_id", "token_hash"]
        for index in inspector.get_indexes("mcp_server_profiles")
    )


def test_single_head_merges_mcp_profiles_with_upstream() -> None:
    script = ScriptDirectory.from_config(_build_alembic_config("sqlite://"))
    assert script.get_heads() == [_HEAD]
    assert set(script.get_revision(_HEAD).down_revision) == {_V0230_HEAD, _MCP_PROFILES}
    # 0.21.0 overlay 發出去的 DB 停在 za3b4c5d6e7f；它的父節點不能改。
    assert script.get_revision(_MCP_PROFILES).down_revision == _V0210_HEAD


@pytest.mark.parametrize(
    "released_base",
    [
        pytest.param(_MCP_PROFILES, id="woowtech-0.21.0-mcp"),
        pytest.param(_V0230_HEAD, id="upstream-0.23.0"),
        pytest.param(_V0210_HEAD, id="upstream-0.21.0"),
    ],
)
def test_upgrade_from_released_base_reaches_both_schemas(
    tmp_path: Path, released_base: str
) -> None:
    uri = f"sqlite:///{tmp_path}/migration.db"
    config = _build_alembic_config(uri)
    command.upgrade(config, released_base)
    command.upgrade(config, "head")
    engine = sa.create_engine(uri)
    try:
        _assert_profiles_table(engine)
        # 上游 0.22／0.23 的表也要建出來（0.21.0 overlay 的 DB 不能跳過這段）。
        tables = set(sa.inspect(engine).get_table_names())
        assert {"memories", "inbox_items", "workflows", "scheduled_task_shares"} <= tables
        with engine.connect() as conn:
            versions = {
                row[0] for row in conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            }
        assert versions == {_HEAD}
    finally:
        engine.dispose()


def test_mcp_profiles_downgrade_and_reupgrade(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path}/migration.db"
    config = _build_alembic_config(uri)
    command.upgrade(config, "head")
    engine = sa.create_engine(uri)
    try:
        _assert_profiles_table(engine)
        # 退到兩條分支共同的 0.21.0 head：MCP profiles 與上游 0.22／0.23 的表都要拿掉。
        command.downgrade(config, _V0210_HEAD)
        assert not sa.inspect(engine).has_table("mcp_server_profiles")
        assert not sa.inspect(engine).has_table("memories")
        command.upgrade(config, "head")
        _assert_profiles_table(engine)
    finally:
        engine.dispose()
