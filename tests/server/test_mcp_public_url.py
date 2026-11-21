"""Public MCP links are independent of browser and ingress origins."""

import pytest

from omnigent.server.mcp_public_url import mcp_connection_url


@pytest.mark.parametrize(
    ("public_base", "accounts_base", "web_path", "expected"),
    [
        (
            "https://mcp.example.test",
            "http://localhost:8000",
            "/api/hassio_ingress/key",
            "https://mcp.example.test",
        ),
        (
            "https://mcp.example.test/lab/",
            "http://localhost:8000",
            "/lab",
            "https://mcp.example.test/lab",
        ),
        (None, "https://app.example.test/lab", "/lab", "https://app.example.test/lab"),
        (None, "https://app.example.test", "/lab", "https://app.example.test/lab"),
        (None, "http://localhost:8000", "", "http://localhost:8000"),
        ("http://[::1]:8000/", "https://app.example.test", "", "http://[::1]:8000"),
    ],
)
def test_public_link(monkeypatch, public_base, accounts_base, web_path, expected):
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_BASE_URL", accounts_base)
    monkeypatch.setenv("OMNIGENT_WEB_BASE_PATH", web_path)
    if public_base is None:
        monkeypatch.delenv("OMNIGENT_MCP_PUBLIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("OMNIGENT_MCP_PUBLIC_BASE_URL", public_base)
    assert mcp_connection_url("omcp_test") == expected + "/mcp/private_omcp_test"


@pytest.mark.parametrize(
    "public_base",
    [
        "http://example.test",
        "https://user:password@example.test",
        "https://example.test/?key=secret",
        "https://example.test/#fragment",
        "https://example.test/api/hassio_ingress/key",
        "https://example.test/../lab",
        "https://example.test/%2e%2e/lab",
        "https://example.test\\evil",
        "https://example.test:bad",
        "https://example.test/with space",
        "//example.test",
    ],
)
def test_reject_invalid_public_base(monkeypatch, public_base):
    monkeypatch.setenv("OMNIGENT_MCP_PUBLIC_BASE_URL", public_base)
    with pytest.raises(ValueError, match="OMNIGENT_MCP_PUBLIC_BASE_URL"):
        mcp_connection_url("omcp_test")
