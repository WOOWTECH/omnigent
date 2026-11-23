# Omnigent MCP server

Administrators can link an Omnigent account to a revocable URL and let an external MCP client operate sessions and scheduled automations with that account's permissions.

## Sub-features

- `mcp-admin-nav`: Find the profile manager under Settings → Admin.
- `mcp-profile-create`: Verify an account password and display a new MCP URL once.
- `mcp-profile-list`: Review active and revoked profiles and their last-used time.
- `mcp-profile-revoke`: Revoke a profile so later MCP requests fail.
- `mcp-session-history`: Page through complete history with account permissions.
- `mcp-public-url`: Copy the configured external URL independently of HA Ingress.
- `mcp-client-tools`: Connect Claude Code or Codex and call session or automation tools.

## How to get to it (user POV)

- Omnigent web app: **Settings → Admin → Omnigent MCP**. An administrator on an accounts-auth deployment can create, review, and revoke profiles.
- External MCP client: add the one-time HTTPS URL from the creation dialog as a Streamable HTTP MCP server. The endpoint exposes session, sharing, and scheduled-automation tools.

## Driving it with the repro environment

Preconditions: an isolated Omnigent deployment using accounts authentication, an admin account, and an existing non-admin account with a password. The external MCP client must be able to reach the deployment over HTTPS; local HTTP is suitable only for isolated development.

- `mcp-admin-nav` / `mcp-profile-list`: sign in as admin, open Settings → Admin → Omnigent MCP, and confirm the profile form and current profile list render.
- `mcp-profile-create`: create a profile for the existing account. Confirm a URL appears once and the profile is added to the list. Close the dialog and confirm the URL is not recoverable from the list.
- `mcp-profile-revoke`: revoke the active profile. Confirm its state changes to Revoked.
- `mcp-session-history`: Page through complete history with account permissions.
- `mcp-public-url`: Copy the configured external URL independently of HA Ingress.
- `mcp-client-tools`: configure Claude Code or Codex with an active profile URL, list sessions, then revoke the profile and confirm a later request is rejected.

Automated coverage (each owns its environment):

- `tests/server/integration/test_mcp_server.py`: password verification, admin-only profile management, real MCP HTTP routing with and without a proxy prefix, complete 200-item history, cursors, session sharing, read/edit enforcement, token hashing, revocation, canonical public URLs, and account-generation invalidation.
- `tests/server/test_mcp_creation.py`: connected-host first-message dispatch, managed-sandbox initial-input contract, and recovery after a dispatch failure.
- `tests/db/test_migration_mcp_profiles.py`: upgrade from the prior database revision, a single migration head, and downgrade/re-upgrade.
- `tests/server/test_mcp_public_url.py`: explicit HTTPS/prefix rules, local development, and invalid URL rejection.
- `web/src/pages/McpServerPage.test.tsx`: copy the server connection URL and clear the password.
- `web/src/hooks/useIsAdmin.test.tsx` and `web/src/shell/settingsNav.test.tsx`: admin discovery on initial load and accounts-only profile navigation.

The browser create/copy/revoke and full host-backed automation journeys still require the isolated deployment steps above. Never paste a live profile URL into shared configuration or evidence.

## Gotchas

- Settings → General → MCP configures outgoing MCP connections. Incoming Omnigent links are under Admin.
- Profile linking currently requires the built-in accounts authentication provider. OIDC and header-auth deployments do not accept account passwords for this flow.
- The URL itself is a bearer secret and is shown only once. Revoking the profile invalidates later calls; a password reset also invalidates profiles linked to that account generation.
- HTTPS protects the URL in transit. Reverse-proxy logs must redact the `/mcp/private_…` path because an ingress sees the URL before Omnigent sanitizes its own logs.
