import { withBasePath } from "@/lib/basePath";
import { authenticatedFetch } from "@/lib/identity";

export interface McpServerProfile {
  id: string;
  name: string;
  user_id: string;
  created_by: string;
  created_at: number;
  last_used_at: number | null;
  revoked_at: number | null;
  active: boolean;
}

export interface McpServerProfileCreated {
  profile: McpServerProfile;
  /** Returned once. The server stores only its hash. */
  token: string;
  connection_url: string;
}

export type McpServerApiResult<T> = { ok: true; value: T } | { ok: false; error: string };

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown; error?: unknown };
    const detail = body.detail ?? body.error;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const messages = detail
        .filter(
          (item): item is { msg: string } =>
            typeof item === "object" && item !== null && typeof item.msg === "string",
        )
        .map((item) => item.msg);
      if (messages.length > 0) return messages.join("; ");
    }
    if (
      typeof detail === "object" &&
      detail !== null &&
      "message" in detail &&
      typeof detail.message === "string"
    ) {
      return detail.message;
    }
  } catch {
    // Keep the status-based message when the response has no JSON body.
  }
  return `Request failed (${response.status}).`;
}

export async function listMcpServerProfiles(): Promise<McpServerApiResult<McpServerProfile[]>> {
  try {
    const response = await authenticatedFetch(withBasePath("/v1/admin/mcp-server/profiles"));
    if (!response.ok) return { ok: false, error: await errorMessage(response) };
    const body = (await response.json()) as { profiles: McpServerProfile[] };
    return { ok: true, value: body.profiles };
  } catch {
    return { ok: false, error: "Could not reach the Omnigent server." };
  }
}

export async function createMcpServerProfile(input: {
  name: string;
  username: string;
  password: string;
}): Promise<McpServerApiResult<McpServerProfileCreated>> {
  try {
    const response = await authenticatedFetch(withBasePath("/v1/admin/mcp-server/profiles"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    });
    if (!response.ok) return { ok: false, error: await errorMessage(response) };
    return { ok: true, value: (await response.json()) as McpServerProfileCreated };
  } catch {
    return { ok: false, error: "Could not reach the Omnigent server." };
  }
}

export async function revokeMcpServerProfile(profileId: string): Promise<McpServerApiResult<void>> {
  try {
    const response = await authenticatedFetch(
      withBasePath(`/v1/admin/mcp-server/profiles/${encodeURIComponent(profileId)}`),
      { method: "DELETE" },
    );
    if (!response.ok) return { ok: false, error: await errorMessage(response) };
    return { ok: true, value: undefined };
  } catch {
    return { ok: false, error: "Could not reach the Omnigent server." };
  }
}
