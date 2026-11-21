"""Inbound MCP uses a password-verified account and the existing REST permissions."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import select

from omnigent.db.db_models import SqlMcpServerProfile
from omnigent.entities.conversation import MessageData, NewConversationItem
from omnigent.server.accounts_store import SqlAlchemyAccountStore
from omnigent.server.passwords import hash_password
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from tests._helpers.session import bundle_files, post_session_bundle
from tests.server.integration.test_accounts_auth_e2e import (
    _ADMIN_PASSWORD,
    _ADMIN_USERNAME,
    _build_app,
)

pytestmark = pytest.mark.asyncio
_PROFILE_API = "/v1/admin/mcp-server/profiles"
_PASSWORD = "mcp-test-password-12345"


@pytest.mark.parametrize(
    ("base_path", "public_base"),
    [("", None), ("/lab", None), ("/ui", "https://mcp.example.test/external")],
)
async def test_account_profiles_transport_permissions_and_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_path: str, public_base: str | None
) -> None:
    monkeypatch.setenv("OMNIGENT_WEB_BASE_PATH", base_path)
    if public_base:
        monkeypatch.setenv("OMNIGENT_MCP_PUBLIC_BASE_URL", public_base)
    else:
        monkeypatch.delenv("OMNIGENT_MCP_PUBLIC_BASE_URL", raising=False)
    app = _build_app(tmp_path, monkeypatch, init_admin_password=_ADMIN_PASSWORD)
    accounts = SqlAlchemyAccountStore(f"sqlite:///{tmp_path}/test.db")
    for user in ("alice", "bob"):
        accounts.create_user_with_password(user, hash_password(_PASSWORD))

    transport = httpx.ASGITransport(app=app)
    async with (
        app.state.omnigent_mcp_server.session_manager.run(),
        httpx.AsyncClient(transport=transport, base_url="http://test") as admin,
        httpx.AsyncClient(transport=transport, base_url="http://test") as alice,
        httpx.AsyncClient(transport=transport, base_url="http://test") as external,
    ):
        for client, username, password in (
            (admin, _ADMIN_USERNAME, _ADMIN_PASSWORD),
            (alice, "alice", _PASSWORD),
        ):
            response = await client.post(
                "/auth/login", json={"username": username, "password": password}
            )
            assert response.status_code == 200, response.text

        assert (await external.get(_PROFILE_API)).status_code == 401
        assert (await alice.get(_PROFILE_API)).status_code == 403
        body = {"name": "Alice integration", "username": "alice", "password": _PASSWORD}
        assert (await alice.post(_PROFILE_API, json=body)).status_code == 403
        fake_password = "private-password-marker" * 50
        invalid = await admin.post(_PROFILE_API, json={**body, "password": fake_password})
        assert invalid.status_code == 422
        assert fake_password not in invalid.text and "private-password-marker" not in invalid.text
        assert invalid.headers["cache-control"] == "no-store"
        wrong = await admin.post(_PROFILE_API, json={**body, "password": "incorrect"})
        assert wrong.status_code == 401

        with monkeypatch.context() as invalid_config:
            invalid_config.setenv("OMNIGENT_MCP_PUBLIC_BASE_URL", "http://unsafe.example.test")
            assert (await admin.post(_PROFILE_API, json=body)).status_code == 500
            assert (await admin.get(_PROFILE_API)).json()["profiles"] == []
        created = await admin.post(
            _PROFILE_API, json=body, headers={"Host": "untrusted.example.test"}
        )
        assert created.status_code == 201, created.text
        assert created.headers["cache-control"] == "no-store"
        profile = created.json()
        assert profile["profile"]["user_id"] == "alice"
        assert profile["connection_url"] == (
            f"{public_base or f'http://localhost:8000{base_path}'}/mcp/private_{profile['token']}"
        )
        bob_response = await admin.post(
            _PROFILE_API, json={**body, "name": "Bob integration", "username": "bob"}
        )
        assert bob_response.status_code == 201, bob_response.text
        profiles = {"alice": profile, "bob": bob_response.json()}
        listed = await admin.get(_PROFILE_API)
        assert profile["token"] not in listed.text
        assert _PASSWORD not in listed.text
        assert "token_hash" not in listed.text
        assert (
            await external.get("/auth/me", headers={"Authorization": f"Bearer {profile['token']}"})
        ).status_code == 401

        async def rpc(user: str, method: str, params: dict | None = None) -> httpx.Response:
            return await external.post(
                urlsplit(profiles[user]["connection_url"]).path,
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            )

        async def call(user: str, name: str, arguments: dict) -> dict:
            response = await rpc(user, "tools/call", {"name": name, "arguments": arguments})
            assert response.status_code == 200, response.text
            return response.json()["result"]

        initialized = await rpc(
            "alice",
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "omnigent-integration", "version": "1"},
            },
        )
        assert initialized.status_code == 200, initialized.text
        assert initialized.headers["cache-control"] == "no-store"
        assert "mcp-session-id" not in initialized.headers
        tools = await rpc("alice", "tools/list")
        assert tools.status_code == 200, tools.text
        names = {tool["name"] for tool in tools.json()["result"]["tools"]}
        assert "omnigent_session_history" in names
        assert {
            "omnigent_session_create",
            "omnigent_session_share",
            "omnigent_automation_create",
        } <= names

        bundle = bundle_files(
            {
                "agent.yaml": (
                    b"name: mcp-fixture\nprompt: Reply briefly.\nexecutor:\n"
                    b"  model: gpt-4o-mini\n  harness: openai-agents\n"
                )
            }
        )
        session = await post_session_bundle(
            alice.post, "/v1/sessions", bundle, metadata={"title": "Alice private MCP session"}
        )
        assert session.status_code == 201, session.text
        session_id = session.json()["session_id"]
        store = SqlAlchemyConversationStore(f"sqlite:///{tmp_path}/test.db")
        saved = store.append(
            session_id,
            [
                NewConversationItem(
                    type="message",
                    response_id=f"history-{i}",
                    data=MessageData(
                        role="user",
                        content=[{"type": "input_text", "text": f"turn {i}"}],
                    ),
                )
                for i in range(200)
            ],
        )
        ids = [item.id for item in saved]
        received = []
        after = None
        while True:
            result = await call(
                "alice",
                "omnigent_session_history",
                {
                    "session_id": session_id,
                    "limit": 37,
                    "after": after,
                    "order": "asc",
                },
            )
            assert not result.get("isError"), result
            page = json.loads(result["content"][0]["text"])
            received.extend(item["id"] for item in page["data"])
            if not page["has_more"]:
                break
            after = page["last_id"]
        assert received == ids
        result = await call(
            "alice",
            "omnigent_session_history",
            {
                "session_id": session_id,
                "limit": 3,
                "after": ids[-1],
                "order": "desc",
            },
        )
        page = json.loads(result["content"][0]["text"])
        assert [item["id"] for item in page["data"]] == list(reversed(ids[-4:-1]))
        result = await call(
            "alice",
            "omnigent_session_history",
            {
                "session_id": session_id,
                "limit": 3,
                "before": ids[3],
                "order": "asc",
            },
        )
        page = json.loads(result["content"][0]["text"])
        assert [item["id"] for item in page["data"]] == ids[:3]
        for invalid in (
            {"limit": 0},
            {"limit": 101},
            {"after": ids[0], "before": ids[-1]},
            {"order": "bad"},
        ):
            result = await call(
                "alice", "omnigent_session_history", {"session_id": session_id, **invalid}
            )
            assert result["isError"]
        assert (await call("bob", "omnigent_session_history", {"session_id": session_id}))[
            "isError"
        ]
        alice_sessions = await call("alice", "omnigent_session_list", {})
        assert not alice_sessions.get("isError"), alice_sessions
        assert session_id in json.dumps(alice_sessions)
        bob_sessions = await call("bob", "omnigent_session_list", {})
        assert not bob_sessions.get("isError"), bob_sessions
        assert session_id not in json.dumps(bob_sessions)
        assert (await call("bob", "omnigent_session_get", {"session_id": session_id}))["isError"]
        shared = await call(
            "alice",
            "omnigent_session_share",
            {"session_id": session_id, "user_id": "bob", "permission": "read"},
        )
        assert not shared.get("isError"), shared
        assert not (await call("bob", "omnigent_session_get", {"session_id": session_id})).get(
            "isError"
        )
        history = await call("bob", "omnigent_session_history", {"session_id": session_id})
        assert not history.get("isError"), history
        denied = await call(
            "bob", "omnigent_session_send", {"session_id": session_id, "message": "must be denied"}
        )
        assert denied["isError"]
        unshared = await call(
            "alice", "omnigent_session_unshare", {"session_id": session_id, "user_id": "bob"}
        )
        assert not unshared.get("isError"), unshared
        assert (await call("bob", "omnigent_session_get", {"session_id": session_id}))["isError"]

        assert (await call("bob", "omnigent_session_history", {"session_id": session_id}))[
            "isError"
        ]

        from omnigent.db.utils import get_or_create_engine

        with get_or_create_engine(f"sqlite:///{tmp_path}/test.db").connect() as connection:
            rows = connection.execute(select(SqlMcpServerProfile.__table__)).mappings().all()
            assert all(len(row["token_hash"]) == 32 for row in rows)
            assert all(row["last_used_at"] is not None for row in rows)
            assert profile["token"] not in repr(rows)

        revoked = await admin.delete(f"{_PROFILE_API}/{profile['profile']['id']}")
        assert revoked.status_code == 204
        assert (await rpc("alice", "tools/list")).status_code == 401
        assert (await rpc("bob", "tools/list")).status_code == 200
        accounts.update_password(
            "bob", hash_password("replacement-password"), revoke_sessions=True
        )
        assert (await rpc("bob", "tools/list")).status_code == 401
