import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ admin: false, resolve: vi.fn() }));
vi.mock("@/lib/identity", () => ({
  getCurrentIsAdmin: () => identity.admin,
  resolveIdentity: identity.resolve,
}));

import { useIsAdmin } from "./useIsAdmin";

afterEach(cleanup);

it("resolves an admin identity even when the initial cache is false", async () => {
  identity.admin = false;
  identity.resolve.mockImplementation(async () => {
    identity.admin = true;
    return "admin";
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  const { result } = renderHook(() => useIsAdmin(), { wrapper });
  await waitFor(() => expect(result.current).toBe(true));
  expect(identity.resolve).toHaveBeenCalled();
  client.clear();
});
