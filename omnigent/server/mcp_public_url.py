"""Canonical external MCP URLs, independent of UI ingress headers."""

from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit, urlunsplit


def mcp_connection_url(token: str) -> str:
    """Return the configured deployment URL with a one-time profile token."""
    explicit = os.environ.get("OMNIGENT_MCP_PUBLIC_BASE_URL", "").strip()
    base = explicit or os.environ.get("OMNIGENT_ACCOUNTS_BASE_URL", "").strip()
    error = "Set OMNIGENT_MCP_PUBLIC_BASE_URL to an external HTTPS deployment URL"
    try:
        parsed = urlsplit(base)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError(error) from exc
    path = parsed.path.rstrip("/")
    if not explicit and not path:
        path = os.environ.get("OMNIGENT_WEB_BASE_PATH", "").strip().rstrip("/")
        if path and not path.startswith("/"):
            path = "/" + path
    decoded_path = unquote(path)
    local_http = parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not hostname
        or (parsed.scheme != "https" and not local_http)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in base
        or "#" in base
        or "\\" in base
        or any(char.isspace() or ord(char) < 32 for char in base + decoded_path)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
        or "/api/hassio_ingress" in decoded_path
        or "\\" in decoded_path
        or (port is not None and port < 1)
    ):
        raise ValueError(error)
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/mcp/private_{token}", "", ""))
