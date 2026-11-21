# Omnigent MCP server

Omnigent exposes a native, stateless Streamable HTTP MCP endpoint for external agents. The admin UI is at **Settings → Admin → Omnigent MCP**. Stateless requests let HA replicas serve the same profile without MCP session affinity.

## Authentication and profiles

- This profile flow is available when Omnigent uses the built-in **accounts** authentication provider.
- An administrator creates a profile by entering its label and the linked Omnigent account's username and password. The server verifies the password, discards it, and stores the account ID and current account generation.
- Omnigent returns a URL such as `https://omnigent.example.com/mcp/private_omcp_<random>`. The URL is a bearer credential: anyone who has it can use that profile's tools as the linked account.
- The database stores only a SHA-256 digest of the random token. The full URL appears once after creation; it cannot be retrieved later. Revoke the profile and create a replacement if the URL is lost or exposed.
- Every MCP HTTP request checks that the profile is active and the linked account generation is still current. Revocation, account deletion, or password reset invalidates the profile.
- Tool calls obtain a short-lived Omnigent session token and call the existing API in-process. Existing session ownership, sharing, automation, and policy checks still apply.

The server does not store or reuse the profile password. Serve Omnigent through HTTPS before sharing a profile URL. Because the token is in the URL path, configure ingress and reverse-proxy access logs to redact `/mcp/private_…`; Omnigent sanitizes the path before its own ASGI access logging.

## Public deployment URL

Set `OMNIGENT_MCP_PUBLIC_BASE_URL` to the complete external HTTPS base URL,
including the deployment prefix when applicable, such as `https://mcp.example.com/omnigent`.
The profile API returns `connection_url` once with the token; Settings copies this exact URL.
The explicit base is authoritative and is not combined with the UI base path.

For Home Assistant, configure a dedicated HTTPS route to the add-on's exposed port.
The Home Assistant `/api/hassio_ingress/...` UI URL requires a browser cookie and
cannot be used by external MCP clients. The public MCP route authenticates with
its profile token and does not require a Home Assistant cookie.

When the explicit base is unset, Omnigent uses the configured accounts base URL,
adding `OMNIGENT_WEB_BASE_PATH` only when that URL has no path. HTTP is accepted
only for localhost, 127.0.0.1, or ::1 development. Invalid URLs are rejected before
a profile is saved; request Host and forwarded headers do not choose the URL.

## Available tools

| Tool | Action |
| --- | --- |
| `omnigent_agent_list` | List server agents or the linked account's own agents. |
| `omnigent_host_list` | List the linked account's hosts to select an online execution host. |
| `omnigent_session_list` | List sessions visible to the linked account, with pagination and search. |
| `omnigent_session_get` | Read an accessible session snapshot (most recent 100 items). |
| `omnigent_session_history` | Page through the complete history with limit, after/before cursors, and asc/desc order. |
| `omnigent_session_create` | Create a session and dispatch its first task on a selected host or managed sandbox. |
| `omnigent_session_send` | Send a message to an accessible session. |
| `omnigent_session_share` | Grant a user read, edit, or manage access to a session. |
| `omnigent_session_unshare` | Remove a user's explicit session permission. |
| `omnigent_automation_list` | List the linked account's scheduled automations. |
| `omnigent_automation_create` | Create an automation using Omnigent's RRULE and launch validation. |
| `omnigent_automation_update` | Update an owned automation. |
| `omnigent_automation_run` | Trigger an owned automation immediately. |
| `omnigent_automation_runs` | Read run records for an owned automation. |
| `omnigent_automation_delete` | Delete an owned automation. |

## Connect an external agent

Create a profile in Settings and copy its one-time URL. Use that URL as the Streamable HTTP server URL. Keep it in the agent's private user configuration; do not commit it to a shared repository.

Claude Code:

```sh
claude mcp add --transport http omnigent 'https://omnigent.example.com/mcp/private_omcp_<random>'
claude mcp list
```

Codex CLI:

```sh
codex mcp add omnigent --url 'https://omnigent.example.com/mcp/private_omcp_<random>'
codex mcp list
```

Replace the example URL with the URL copied from Omnigent. Both clients support remote HTTP MCP servers; these commands store the connection in the client configuration. Consult the clients' current guides for scope and configuration options: [Claude Code MCP](https://code.claude.com/docs/en/mcp) and [Codex MCP servers](https://developers.openai.com/codex/mcp).

For a connected host, call `omnigent_agent_list` and `omnigent_host_list`, select an online host owned by the linked account, and pass its `host_id` to `omnigent_session_create`. The tool creates the session and dispatches the first message through the events API. A configured managed sandbox uses `host_type="managed"` and the server's durable initial-input queue. If the session is created but message dispatch fails, the error includes the created session ID so the client can recover it.

## Complete conversation history

Call `omnigent_session_history` with `session_id`, `limit` (1–100, default 50),
and `order` (`asc` by default). While `has_more` is true, pass the returned
`last_id` as `after` for the next page. `before` reads the previous side of the
cursor in the selected order; it cannot be combined with `after`. The result
contains `data`, `first_id`, `last_id`, and `has_more`, matching the REST items API.
Read access and sharing revocation apply to every page.

## Operational notes

- The MCP endpoint and profile administration API are mounted only when the accounts provider, account store, and permission store are configured.
- The profile URL is not an Omnigent API key and does not grant administrator privileges. It inherits only the linked account's access.
- Clients should connect to the exact profile URL, including the `/mcp/private_…` path. A trailing slash is accepted.
- App-level profile checks run on every Streamable HTTP request. An already-running tool call may finish while an administrator revokes its profile; subsequent requests are rejected.
- Stateless MCP transport does not replace the deployment's routing requirements for host and runner connections. Multi-replica execution needs separate validation in that deployment.
