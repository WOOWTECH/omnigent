import { afterEach, expect, it, vi } from "vitest";

const fetchMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/identity", () => ({ authenticatedFetch: fetchMock }));
import { createMcpServerProfile } from "./mcpServerApi";

afterEach(() => vi.clearAllMocks());

it("does not render validation inputs returned by an older server", async () => {
  fetchMock.mockResolvedValue(
    new Response(
      JSON.stringify({
        detail: [
          {
            msg: "String should have at most 1024 characters",
            input: "private-password-marker",
            ctx: { password: "private-password-marker" },
          },
        ],
      }),
      { status: 422 },
    ),
  );
  const result = await createMcpServerProfile({
    name: "Test",
    username: "alice",
    password: "private-password-marker",
  });
  expect(result).toEqual({ ok: false, error: "String should have at most 1024 characters" });
  expect(JSON.stringify(result)).not.toContain("private-password-marker");
});
