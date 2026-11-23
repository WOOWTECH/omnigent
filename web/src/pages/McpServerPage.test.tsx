import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ create: vi.fn(), list: vi.fn(), revoke: vi.fn() }));
vi.mock("@/lib/CapabilitiesContext", () => ({ useServerInfo: () => ({ accounts_enabled: true }) }));
vi.mock("@/lib/identity", () => ({
  resolveIdentity: async () => "admin",
  getCurrentIsAdmin: () => true,
}));
vi.mock("@/lib/mcpServerApi", () => ({
  createMcpServerProfile: api.create,
  listMcpServerProfiles: api.list,
  revokeMcpServerProfile: api.revoke,
}));

import { McpServerPage } from "./McpServerPage";

afterEach(cleanup);

it("copies the server connection URL even when the UI uses an ingress origin", async () => {
  const url = "https://mcp.example.test/external/mcp/private_omcp_test";
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText: copy }, configurable: true });
  api.list.mockResolvedValue({ ok: true, value: [] });
  api.create.mockResolvedValue({
    ok: true,
    value: { profile: { name: "External" }, token: "omcp_test", connection_url: url },
  });
  render(<McpServerPage />);
  await screen.findByRole("heading", { name: "Create profile" });
  fireEvent.change(screen.getByLabelText("Profile name"), { target: { value: "External" } });
  fireEvent.change(screen.getByLabelText("Omnigent account"), { target: { value: "alice" } });
  fireEvent.change(screen.getByLabelText("Account password"), {
    target: { value: "test-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create MCP link" }));
  await screen.findByText(url);
  fireEvent.click(screen.getByRole("button", { name: /Copy/ }));
  await waitFor(() => expect(copy).toHaveBeenCalledWith(url));
  expect(screen.getByLabelText("Account password")).toHaveValue("");
});
